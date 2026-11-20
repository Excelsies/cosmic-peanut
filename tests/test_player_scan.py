"""The library scan pool: probes run off the caller's thread, several at once, cancellable."""

import threading
import time
from pathlib import Path

from tidalviz.player.queue import PlayQueue, Track
from tidalviz.player.scan import ProbeResult, ScanPool
from tidalviz.player.tags import TrackTags


def tracks(n: int) -> list[Track]:
    q = PlayQueue()
    q.add([Path(f"/m/{i}.mp3") for i in range(n)], replace=False)
    return q.tracks


def wait_for(pred, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def test_every_submitted_track_gets_exactly_one_result():
    got: list[tuple[Track, ProbeResult]] = []
    lock = threading.Lock()

    def on_result(t: Track, r: ProbeResult) -> None:
        with lock:
            got.append((t, r))

    pool = ScanPool(
        lambda p: ProbeResult(True, tags=TrackTags(title=p.stem), duration=1.0), on_result
    )
    ts = tracks(40)
    pool.submit(ts)
    assert wait_for(lambda: len(got) == 40)
    pool.close()
    assert {id(t) for t, _ in got} == {id(t) for t in ts}
    assert all(r.tags is not None and r.tags.title == t.path.stem for t, r in got)


def test_probes_run_on_several_threads_at_once_and_never_on_the_callers():
    seen: set[int] = set()
    inside = 0
    peak = 0
    lock = threading.Lock()
    done = threading.Event()

    def probe(p: Path) -> ProbeResult:
        nonlocal inside, peak
        with lock:
            seen.add(threading.get_ident())
            inside += 1
            peak = max(peak, inside)
        time.sleep(0.03)
        with lock:
            inside -= 1
        return ProbeResult(True)

    count = 0

    def on_result(t: Track, r: ProbeResult) -> None:
        nonlocal count
        count += 1
        if count == 12:
            done.set()

    pool = ScanPool(probe, on_result, workers=4)
    pool.submit(tracks(12))
    assert done.wait(3.0)
    pool.close()
    assert threading.get_ident() not in seen
    assert 2 <= peak <= 4


def test_a_probe_that_raises_marks_that_track_unplayable_and_the_rest_continue():
    got: dict[str, ProbeResult] = {}

    def probe(p: Path) -> ProbeResult:
        if p.stem == "1":
            raise RuntimeError("boom")
        return ProbeResult(True)

    pool = ScanPool(probe, lambda t, r: got.__setitem__(t.path.stem, r), workers=1)
    pool.submit(tracks(3))
    assert wait_for(lambda: len(got) == 3)
    pool.close()
    assert got["1"].playable is False and got["1"].reason
    assert got["0"].playable and got["2"].playable


def test_close_drops_whatever_has_not_started():
    gate = threading.Event()
    started: list[str] = []
    got: list[str] = []

    def probe(p: Path) -> ProbeResult:
        started.append(p.stem)
        gate.wait(3.0)
        return ProbeResult(True)

    pool = ScanPool(probe, lambda t, r: got.append(t.path.stem), workers=1)
    pool.submit(tracks(10))
    assert wait_for(lambda: started == ["0"])
    pool.close()
    gate.set()
    time.sleep(0.1)
    assert started == ["0"] and got == []  # in-flight work is discarded too


def test_a_submit_after_close_is_an_error():
    pool = ScanPool(lambda p: ProbeResult(True), lambda t, r: None)
    pool.close()
    try:
        pool.submit(tracks(1))
    except RuntimeError:
        return
    raise AssertionError("submit after close must raise")


def test_an_urgent_track_goes_ahead_of_the_queue():
    gate = threading.Event()
    order: list[str] = []

    def probe(p: Path) -> ProbeResult:
        order.append(p.stem)
        gate.wait(3.0)
        return ProbeResult(True)

    pool = ScanPool(probe, lambda t, r: None, workers=1)
    ts = tracks(6)
    pool.submit(ts[:5])
    assert wait_for(lambda: order == ["0"])  # the single worker is busy with the first
    pool.submit([ts[5]], urgent=True)
    gate.set()
    assert wait_for(lambda: len(order) == 6)
    pool.close()
    assert order[:2] == ["0", "5"]


def test_cancel_drops_queued_work_and_the_results_of_work_in_flight():
    gate = threading.Event()
    started: list[str] = []
    got: list[str] = []

    def probe(p: Path) -> ProbeResult:
        started.append(p.stem)
        gate.wait(3.0)
        return ProbeResult(True)

    pool = ScanPool(probe, lambda t, r: got.append(t.path.stem), workers=1)
    pool.submit(tracks(5))
    assert wait_for(lambda: started == ["0"])
    pool.cancel()
    fresh = tracks(2)
    pool.submit(fresh)  # new work after the cancel is kept
    gate.set()
    assert wait_for(lambda: len(got) == 2)
    pool.close()
    assert started == ["0", "0", "1"] and got == ["0", "1"]  # the old "0" ran but was not reported


def test_a_track_that_already_has_its_answer_is_not_probed_again():
    probed: list[str] = []
    ts = tracks(2)
    ts[0].playable = True
    pool = ScanPool(
        lambda p: probed.append(p.stem) or ProbeResult(True), lambda t, r: None, workers=1
    )
    pool.submit(ts)
    assert wait_for(lambda: probed == ["1"])
    pool.close()


def test_a_track_submitted_twice_is_probed_once_even_with_free_workers():
    gate = threading.Event()
    probed: list[str] = []

    def probe(p: Path) -> ProbeResult:
        probed.append(p.stem)
        gate.wait(3.0)
        return ProbeResult(True)

    got: list[str] = []
    pool = ScanPool(probe, lambda t, r: got.append(t.path.stem), workers=4)
    (t,) = tracks(1)
    pool.submit([t], urgent=True)
    assert wait_for(lambda: probed == ["0"])
    pool.submit([t])  # a background job for the same track while the urgent one is running
    time.sleep(0.1)
    gate.set()
    assert wait_for(lambda: got == ["0"])
    pool.close()
    assert probed == ["0"]
