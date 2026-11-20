"""PlayerSource against real GStreamer (output into fakesink, so nothing is audible)."""

import itertools
import threading
import time
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.player_media import RAMP_SLOPE, Media
from tests.player_support import BLOCK, Recorder, wait_until
from tidalviz.capture.transport import QueueControl, Transport
from tidalviz.player.store import QueueStore, StoredQueue

SR = 48000
BLOCK_S = BLOCK / SR


@pytest.fixture
def make(gst, media: Media, tmp_path: Path):
    """Build sources over a throwaway state dir; every one is stopped at teardown."""
    from tidalviz.player.source import PlayerSource

    made: list[tuple[PlayerSource, Recorder]] = []

    def factory(*files: Path, store: Path | None = None, **kw):
        path = store or tmp_path / "player.json"
        if files:  # a queue saved by an earlier run: restored paused, as at app start
            QueueStore(path).save(StoredQueue(list(files), 0, None))
        src = PlayerSource(path, **kw)
        rec = Recorder()
        src.bind(rec.on_change, rec.on_status)
        made.append((src, rec))
        return src, rec

    yield factory
    for src, _ in made:
        src.stop()


def test_it_is_an_audio_source_with_both_capabilities(make):
    src, _ = make()
    assert isinstance(src, Transport) and isinstance(src, QueueControl)
    assert (src.format.sample_rate, src.format.channels) == (48000.0, 2)
    assert src.failed.is_set() is False


def test_it_delivers_48k_stereo_float_blocks_of_one_hop_from_the_worker_thread(make, media: Media):
    src, rec = make(media.tone_a)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: rec.loud() >= 20)
    src.stop()
    assert rec.fmt_ok(src.format)
    assert rec.threads and threading.get_ident() not in rec.threads and len(rec.threads) == 1
    loud = [b for b, _ in rec.snapshot() if np.abs(b).max() > 1e-4]
    assert 0.4 < max(float(np.abs(b).max()) for b in loud) < 0.55  # the 0.5 tone, unscaled


@pytest.mark.parametrize("which", ["tone_a", "mono", "tone_c"])
def test_any_file_comes_out_as_48k_stereo(make, media: Media, which: str):
    src, rec = make(getattr(media, which))
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: rec.loud() >= 10)
    src.stop()
    assert rec.fmt_ok(src.format)
    blocks = [b for b, _ in rec.snapshot() if np.abs(b).max() > 1e-4]
    if which == "mono":  # 22.05 kHz mono: both channels carry the same signal
        assert np.allclose(blocks[3][:, 0], blocks[3][:, 1], atol=1e-5)


def test_playing_blocks_arrive_at_the_hop_cadence_with_monotonic_times(make, media: Media):
    src, rec = make(media.ramp)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: rec.loud() >= 60)
    src.stop()
    times = [t for b, t in rec.snapshot() if np.abs(b).max() > 1e-4][5:60]
    gaps = np.diff(times)
    assert np.all(gaps >= 0)
    mean_gap = (times[-1] - times[0]) / (len(times) - 1)  # the average holds on a busy machine
    assert abs(mean_gap - BLOCK_S) < 0.2 * BLOCK_S  # 10.67 ms


def test_tags_and_duration_come_from_the_discoverer_not_from_playing(make, media: Media):
    src, rec = make(media.tone_a, media.tone_b, media.tone_c, media.mono)
    src.start(rec.on_samples)  # starts the scan; nothing is played
    assert wait_until(lambda: len(src.queue().entries) == 4)
    assert wait_until(lambda: all(e.duration for e in src.queue().entries))
    q = src.queue().entries
    assert (q[0].artist, q[0].album, q[0].title) == ("Artist A", "Album A", "Track A")
    assert q[1].artist == "Artist B"  # surrounding whitespace stripped
    assert (q[2].artist, q[2].album, q[2].title) == (
        "Sigur Rós",
        "Ágætis byrjun",
        "Svefn-g-englar ✓",
    )
    assert (q[3].artist, q[3].album, q[3].title) == ("", "", "mono")  # file name, nothing guessed
    assert all(1.0 < e.duration < 1.5 for e in q if e.duration)
    assert all(e.playable for e in q)
    assert src.now_playing().playing is False


def test_a_restored_queue_is_not_playing_and_emits_silence_blocks(make, media: Media):
    src, rec = make(media.tone_a)
    src.start(rec.on_samples)
    assert wait_until(lambda: rec.count() >= 30)
    src.stop()
    assert rec.loud() == 0 and rec.fmt_ok(src.format)
    ts = np.array([t for _, t in rec.snapshot()])
    assert abs((ts[-1] - ts[0]) / (len(ts) - 1) - BLOCK_S) < 0.2 * BLOCK_S


def test_pause_emits_silence_and_resume_continues_the_same_track(make, media: Media):
    src, rec = make(media.ramp)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: rec.loud() >= 30)
    src.pause()
    assert wait_until(lambda: not src.now_playing().playing)
    time.sleep(0.2)  # let in-flight blocks drain
    n = rec.count()
    assert wait_until(lambda: rec.count() >= n + 20)  # silence keeps coming
    paused = src.now_playing()
    assert all(float(np.abs(b).max()) == 0.0 for b, _ in rec.snapshot()[n:])
    src.play()
    assert wait_until(lambda: rec.loud() >= 60)
    resumed = src.now_playing()
    assert resumed.playing and resumed.position >= paused.position
    assert resumed.index == 0 and resumed.title == "ramp"
    src.stop()


def test_seek_is_accurate_and_the_position_is_held_until_the_first_block(make, media: Media):
    src, rec = make(media.ramp)
    src.start(rec.on_samples)
    src.seek(4.0)  # a stopped queue: the track opens paused, then seeks
    assert wait_until(lambda: src.now_playing().position == 4.0)
    src.play()
    assert wait_until(lambda: rec.loud() >= 5)
    src.stop()
    first = next(b for b, _ in rec.snapshot() if np.abs(b).max() > 1e-4)
    where = float(first.mean()) / RAMP_SLOPE  # the ramp's value is the time into the file
    assert abs(where - 4.0) < 0.06, where  # accurate: a key-unit seek lands 0.6-1.4 s early (WMA)


def test_seeking_while_playing_holds_the_target_then_follows_the_stream(make, media: Media):
    src, rec = make(media.ramp)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: rec.loud() >= 10)
    src.seek(6.0)
    assert wait_until(lambda: src.now_playing().position >= 6.0)  # the target, until a block lands
    n = rec.count()
    assert wait_until(
        lambda: any(
            np.abs(b).max() > 1e-4 and b.mean() / RAMP_SLOPE > 5.8 for b, _ in rec.snapshot()[n:]
        )
    )
    assert 5.9 < src.now_playing().position < 7.0
    src.stop()


def test_at_the_end_of_a_track_it_moves_on_and_at_the_end_of_the_queue_it_stops(make, media: Media):
    src, rec = make(media.tone_a, media.tone_b)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: src.now_playing().index == 1, 5.0)
    assert src.now_playing().title == "Track B"
    assert wait_until(lambda: not src.now_playing().playing, 5.0)
    np_ = src.now_playing()
    assert np_.index == 0 and np_.count == 2  # back at the top, ready to play again
    n = rec.count()
    assert wait_until(lambda: rec.count() >= n + 20)
    assert all(
        float(np.abs(b).max()) == 0.0 for b, _ in rec.snapshot()[-15:]
    )  # silence after the end
    peaks = rec.peaks()
    loud = np.flatnonzero(peaks > 1e-4)
    assert 0.4 < peaks[loud[: len(loud) // 3]].max() < 0.55  # first track's 0.5 tone
    assert 0.2 < peaks[loud[-5:]].max() < 0.3  # then the second's 0.25 tone
    src.stop()


def test_next_previous_and_select_play_the_chosen_entry(make, media: Media):
    src, rec = make(media.tone_a, media.ramp, media.tone_b)
    src.start(rec.on_samples)
    src.select(1)
    assert wait_until(lambda: src.now_playing().playing and src.now_playing().title == "ramp")
    src.next()
    assert wait_until(lambda: src.now_playing().index == 2)
    src.previous()
    assert wait_until(lambda: src.now_playing().index == 1)
    with pytest.raises(ValueError):
        src.select(3)
    src.stop()


def test_open_replaces_or_appends_and_a_replace_starts_playing(make, media: Media):
    src, rec = make(media.tone_a)
    src.start(rec.on_samples)
    src.open([media.tone_b], replace=False)
    assert wait_until(lambda: src.now_playing().count == 2)
    assert src.now_playing().playing is False  # an append never starts playback by itself
    src.open([media.tone_d, media.tone_c], replace=True)
    assert wait_until(lambda: src.now_playing().playing and src.now_playing().count == 2)
    assert wait_until(lambda: src.now_playing().title == "Track D")
    src.clear()
    assert wait_until(lambda: src.now_playing().count == 0)
    n = src.now_playing()
    assert (n.index, n.title, n.playing) == (None, "", False)
    src.stop()


def test_changes_are_reported_when_the_track_or_the_state_changes(make, media: Media):
    src, rec = make(media.tone_a)
    src.start(rec.on_samples)
    before = rec.changes
    src.play()
    assert wait_until(lambda: rec.changes > before and src.now_playing().playing)
    src.stop()


# --- files that cannot be played ---------------------------------------------------------------


@pytest.mark.parametrize("bad", ["missing", "text", "jpg", "noise", "truncated"])
def test_an_unplayable_file_is_skipped_reported_and_never_a_failure(
    make, media: Media, bad: str, tmp_path: Path
):
    if bad == "missing":  # queued while it existed, deleted before it was played
        gone = tmp_path / "gone.flac"
        gone.write_bytes(media.mono.read_bytes())
        src, rec = make(gone, media.tone_a)
        gone.unlink()
    else:
        src, rec = make(getattr(media, bad), media.tone_a)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: src.now_playing().title == "Track A" and rec.loud() >= 5, 8.0)
    assert src.failed.is_set() is False
    entries = src.queue().entries
    if bad != "truncated":  # a cut-off FLAC may well play to its (early) end
        assert entries[0].playable is False
        assert any(lvl == "warn" and "Skipped" in text for lvl, text in rec.statuses)
    assert all("/" not in text and str(media.root) not in text for _, text in rec.statuses)
    src.stop()


def test_a_queue_with_nothing_playable_stops_with_silence(make, media: Media, tmp_path: Path):
    gone = tmp_path / "gone.flac"
    gone.write_bytes(media.mono.read_bytes())
    src, rec = make(media.text, media.jpg, gone)
    gone.unlink()
    src.start(rec.on_samples)
    src.play()
    # One status for the whole run, whether the scan or the playback found each track out.
    assert wait_until(lambda: rec.statuses, 8.0)
    assert wait_until(lambda: not src.now_playing().playing)
    time.sleep(0.3)
    assert rec.statuses == [("warn", "Skipped 3 tracks that could not be played")]
    assert src.failed.is_set() is False and src.now_playing().index == 0
    n = rec.count()
    assert wait_until(lambda: rec.count() >= n + 10)
    assert rec.loud() == 0
    src.stop()


def test_a_file_error_the_bus_reports_stays_a_track_error_even_without_a_preflight(
    make, media: Media
):
    from tidalviz.player.scan import ProbeResult

    src, rec = make(media.jpg, media.tone_a, probe=lambda p: ProbeResult(True))
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: src.now_playing().title == "tone_a" and rec.loud() >= 3, 8.0)
    assert not src.failed.is_set() and any("Skipped" in t for _, t in rec.statuses)
    src.stop()


class StuckOutput:
    """A backend whose output never delivers a block (a hung PulseAudio): no error either."""

    @staticmethod
    def install(monkeypatch: pytest.MonkeyPatch) -> None:
        import tidalviz.player.playback as pbm
        from tidalviz.player.engine import GstBackend

        class Stuck(GstBackend):
            def pull(self, timeout_s, out):  # type: ignore[no-untyped-def]
                time.sleep(min(timeout_s, 0.01))
                return False

        monkeypatch.setattr(pbm, "GstBackend", Stuck)


def test_a_stuck_output_is_an_engine_failure_after_the_stall_timeout_not_a_skipped_track(
    make, media: Media, monkeypatch: pytest.MonkeyPatch
):
    StuckOutput.install(monkeypatch)
    src, rec = make(media.tone_a, media.tone_b, stall_timeout=0.5)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(src.failed.is_set, 5.0)
    assert [lvl for lvl, _ in rec.statuses] == ["error"]  # one status, no track blamed
    assert all(e.playable for e in src.queue().entries)
    assert wait_until(lambda: not worker_alive())
    src.stop()


def test_an_output_error_on_the_bus_stops_the_pipeline_at_once_and_blocks_stop(
    make, media: Media, monkeypatch: pytest.MonkeyPatch
):
    """The sink element posts an ERROR mid-playback (as a vanished PulseAudio sink does)."""
    from gi.repository import GLib, Gst  # type: ignore[import-not-found]

    import tidalviz.player.playback as pbm
    from tidalviz.player.engine import GstBackend

    class Flaky(GstBackend):
        def __init__(self, sink):  # type: ignore[no-untyped-def]
            super().__init__(sink)

            def boom() -> None:
                time.sleep(0.4)
                out = self._bin.get_by_name("out")
                out.post_message(
                    Gst.Message.new_error(out, GLib.Error("sink gone /secret/x"), "dbg")
                )

            threading.Thread(target=boom, daemon=True).start()

    monkeypatch.setattr(pbm, "GstBackend", Flaky)
    src, rec = make(media.ramp, media.tone_a)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(src.failed.is_set, 5.0)
    assert wait_until(lambda: not worker_alive())
    assert rec.statuses == [
        ("error", "The audio output stopped working")
    ]  # one, and no library text
    n = rec.count()
    time.sleep(0.3)
    assert rec.count() == n  # the pipeline was stopped: no further blocks
    assert src.queue().entries[0].playable
    src.stop()


def test_an_output_that_cannot_be_built_is_an_engine_failure_not_a_failed_command(
    make, media: Media
):
    from tidalviz.player.sink import SinkSpec

    src, rec = make(media.tone_a, sink=SinkSpec("fakesink", "no-such-property-device"))
    src.start(rec.on_samples)
    src.play()
    assert wait_until(src.failed.is_set, 5.0)
    assert rec.statuses == [("error", "The audio output stopped working")]
    src.stop()


@pytest.mark.real_sink  # builds a pulsesink on purpose, aimed at a server that does not exist
def test_a_broken_output_sink_is_the_engines_failure_and_stops_the_pipeline(
    make, media: Media, monkeypatch: pytest.MonkeyPatch
):
    from tidalviz.player.sink import SinkSpec

    monkeypatch.setenv("PULSE_SERVER", "unix:/nonexistent/tidalviz-test-socket")  # nothing can play
    src, rec = make(media.tone_a, sink=SinkSpec("pulsesink", "tidalviz_no_such_sink"))
    src.start(rec.on_samples)
    src.play()
    assert wait_until(src.failed.is_set, 8.0)
    assert any(lvl == "error" for lvl, _ in rec.statuses)
    assert src.queue().entries[0].playable  # the file was never at fault
    src.stop()


# --- lifecycle -----------------------------------------------------------------------------------


def worker_alive() -> bool:
    return any(t.name == "tidalviz-player" and t.is_alive() for t in threading.enumerate())


def test_stop_can_be_called_from_inside_on_samples_and_the_object_restarts(make, media: Media):
    src, rec = make(media.tone_a, media.tone_b)
    seen = threading.Event()

    def stopper(block, t):
        rec.on_samples(block, t)
        if rec.count() == 5:
            src.stop()  # from the worker thread itself
            seen.set()

    src.start(stopper)
    assert seen.wait(3.0)
    assert wait_until(lambda: not worker_alive())
    assert src.failed.is_set() is False
    src.select(1)  # the queue survived; select it now, then restart
    src.start(rec.on_samples)
    assert wait_until(lambda: src.now_playing().index == 1 and src.now_playing().playing)
    assert wait_until(lambda: rec.loud() >= 3)
    src.stop()
    assert not worker_alive()


def test_start_twice_is_an_error_and_stop_twice_is_harmless(make, media: Media):
    src, rec = make(media.tone_a)
    src.start(rec.on_samples)
    with pytest.raises(RuntimeError):
        src.start(rec.on_samples)
    src.stop()
    src.stop()
    assert not worker_alive()


def test_stop_joins_the_worker_and_closes_the_pipeline_while_playing(make, media: Media):
    src, rec = make(media.ramp)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: rec.loud() >= 5)
    src.stop()
    assert not worker_alive()  # stop() joined it (it has no way to return early otherwise)
    assert src.now_playing().playing is False


def test_the_queue_is_saved_and_a_new_source_restores_it_paused(make, media: Media, tmp_path: Path):
    store = tmp_path / "state.json"
    src, rec = make(media.tone_a, media.tone_b, media.tone_c, store=store)
    src.start(rec.on_samples)
    src.select(2)
    assert wait_until(lambda: src.now_playing().index == 2)
    src.stop()

    media.tone_b.rename(media.tone_b.with_suffix(".gone"))  # one file disappears meanwhile
    try:
        again, rec2 = make(store=store)
        n = again.now_playing()
        assert (n.count, n.index, n.playing) == (2, 1, False)  # still the M4A, now entry 1
        assert n.title == "tone_c"  # tags arrive once it is started and scanned
        again.start(rec2.on_samples)
        assert wait_until(lambda: again.now_playing().title == "Svefn-g-englar ✓")
        assert again.now_playing().playing is False
        again.stop()
    finally:
        media.tone_b.with_suffix(".gone").rename(media.tone_b)


def test_open_remembers_the_folder_for_the_next_dialog(make, media: Media):
    src, _ = make()
    assert src.last_folder is None
    src.open([media.tone_a], replace=True)
    assert src.last_folder == str(media.root)
    src.open([media.root], replace=False)
    assert src.last_folder == str(media.root)


def test_a_folder_is_expanded_in_natural_order_and_junk_is_left_out(
    make, media: Media, tmp_path: Path
):
    folder = tmp_path / "album"
    folder.mkdir()
    for n in ("t10.flac", "t2.flac", "t1.flac", "cover.jpg"):
        (folder / n).write_bytes(
            media.mono.read_bytes() if n.endswith("flac") else media.jpg.read_bytes()
        )
    src, rec = make()
    src.start(rec.on_samples)
    src.open([folder], replace=True)
    assert wait_until(lambda: src.now_playing().count == 3)
    assert [e.title for e in src.queue().entries] == ["t1", "t2", "t10"]
    src.stop()


# --- cost ------------------------------------------------------------------------------------------


def test_pulling_a_block_retains_nothing_and_allocates_a_bounded_transient(gst, media: Media):
    """Not zero: PyGObject hands back a ~4 KB `bytes` copy per block (measured 5.3 KB peak). What
    is pinned is that no block-sized object accumulates (a leaked copy per block would retain
    400 KB over this run; the measured residue is ~7 KB of small objects, and RSS and the live
    object census stay flat over 750 blocks) and that the transient stays within two blocks."""
    import gc
    import tracemalloc

    from tidalviz.player.engine import BLOCK_BYTES, GstBackend
    from tidalviz.player.sink import SinkSpec

    backend = GstBackend(SinkSpec("fakesink"))
    out = np.zeros((BLOCK, 2), dtype=np.float32)

    def pull_one() -> None:  # the appsink answers "nothing" at once until the pipeline has started
        end = time.monotonic() + 3.0
        while not backend.pull(0.5, out):
            assert time.monotonic() < end
            time.sleep(0.002)

    backend.open(media.ramp, play=True)
    try:
        for _ in range(150):  # warm up lazily created Python state
            pull_one()
        tracemalloc.start()
        try:
            gc.collect()  # cyclic garbage is not a per-block cost
            before = tracemalloc.get_traced_memory()[0]
            worst = 0
            for _ in range(100):
                tracemalloc.reset_peak()
                base = tracemalloc.get_traced_memory()[0]
                pull_one()
                worst = max(worst, tracemalloc.get_traced_memory()[1] - base)
            gc.collect()
            retained = tracemalloc.get_traced_memory()[0] - before
            print(f"transient worst {worst} B, retained {retained} B over 100 blocks")
        finally:
            tracemalloc.stop()
    finally:
        backend.close()
    assert worst < 2 * BLOCK_BYTES, worst  # the copy PyGObject makes, nothing like a new array
    assert retained < 16 * BLOCK_BYTES, retained  # 64 KB: far below one block per block


# --- under the real pipeline ---------------------------------------------------------------------


@pytest.fixture
def pipeline_for(make):
    from tidalviz.pipeline import AudioPipeline

    pipes: list[AudioPipeline] = []

    def build(src):
        frames: list[bytes] = []
        pipe = AudioPipeline(src, frames.append, backoff=(0.05,))
        pipes.append(pipe)
        return pipe, frames

    yield build
    for p in pipes:
        p.stop()


def test_the_pipeline_publishes_frames_for_playing_and_for_silence(
    make, media: Media, pipeline_for
):
    from tidalviz.transport.frame import decode

    src, _ = make(media.ramp)
    pipe, frames = pipeline_for(src)
    pipe.start()
    assert wait_until(lambda: len(frames) >= 3)  # silence is reported: frames flow while stopped
    assert decode(frames[-1]).silent
    src.play()
    assert wait_until(lambda: any(not decode(f).silent for f in frames[-20:]), 5.0)
    assert pipe.stats().dropped_frames < 5


def test_a_paused_pipeline_does_not_stop_the_music(make, media: Media, pipeline_for):
    src, _ = make(media.ramp)
    pipe, frames = pipeline_for(src)
    pipe.start()
    src.play()
    assert wait_until(lambda: src.now_playing().playing and src.now_playing().position > 0.2)
    pipe.pause()  # no renderer connected: analysis stops, the source keeps running
    time.sleep(0.1)
    published, p0 = len(frames), src.now_playing().position
    time.sleep(0.6)
    assert len(frames) == published
    assert src.now_playing().playing and src.now_playing().position > p0 + 0.4
    pipe.resume()
    assert wait_until(lambda: len(frames) > published)


@pytest.mark.perf
def test_clicks_in_a_file_become_onsets_on_the_same_grid(make, media: Media, pipeline_for):
    from tests.player_media import CLICK_PERIOD_S
    from tidalviz.frame import SCALAR_INDEX
    from tidalviz.transport.frame import decode

    src, _ = make(media.click)
    pipe, frames = pipeline_for(src)
    pipe.start()
    src.play()
    assert wait_until(lambda: src.now_playing().playing, 3.0)
    assert wait_until(lambda: not src.now_playing().playing, 12.0)  # the 5 s file ran out
    onsets = []
    for data in frames:
        f = decode(data)
        if f.onset:
            onsets.append(f.host_time - float(f.scalars[SCALAR_INDEX["onsetAge"]]))
    print(
        f"onsets {len(onsets)}, gap error ms: {np.round(np.diff(onsets) * 1000 - 500, 2).tolist()}"
    )
    assert 9 <= len(onsets) <= 10, onsets  # ten clicks in the file
    gaps = np.diff(onsets)
    assert float(np.median(np.abs(gaps - CLICK_PERIOD_S))) < 0.010, (
        gaps
    )  # typically well under 1 ms
    assert np.all(np.abs(gaps - CLICK_PERIOD_S) < 0.030), gaps  # the same grid (loose: host timing)


# --- more of the transport ---------------------------------------------------------------------------


def test_previous_goes_back_or_restarts_the_first_track_and_toggle_flips(make, media: Media):
    src, rec = make(media.ramp, media.tone_a)
    src.start(rec.on_samples)
    src.toggle()
    assert wait_until(lambda: src.now_playing().playing and src.now_playing().position > 0.3)
    src.toggle()
    assert wait_until(lambda: not src.now_playing().playing)
    assert src.now_playing().position > 0.3
    src.previous()  # already on the first track: starts it over and plays
    assert wait_until(lambda: src.now_playing().playing and src.now_playing().position < 0.3)
    assert src.now_playing().index == 0
    src.next()
    assert wait_until(lambda: src.now_playing().index == 1 and src.now_playing().playing)
    src.previous()
    assert wait_until(lambda: src.now_playing().index == 0 and src.now_playing().playing)
    src.stop()


def test_selecting_a_known_bad_entry_reports_it_and_stays_put(make, media: Media):
    src, rec = make(media.tone_a, media.text)
    src.start(rec.on_samples)
    assert wait_until(lambda: src.queue().entries[1:] and not src.queue().entries[1].playable)
    src.select(1)
    assert wait_until(lambda: any("Skipped" in t for _, t in rec.statuses))
    assert src.now_playing().index == 0 and src.now_playing().playing is False
    src.stop()


def test_a_full_queue_says_how_many_did_not_fit(gst, tmp_path: Path):
    from tidalviz.player.playback import Playback
    from tidalviz.player.queue import MAX_QUEUE
    from tidalviz.player.scan import ProbeResult, ScanPool
    from tidalviz.player.sink import SinkSpec
    from tidalviz.player.store import QueueStore

    said: list[tuple[str, str]] = []
    pb = Playback(
        QueueStore(tmp_path / "p.json"),
        sink=SinkSpec("fakesink"),
        stall_timeout=1.0,
        report=lambda level, text: said.append((level, text)),
    )
    results: list[Any] = []
    pb.pool = ScanPool(lambda p: ProbeResult(False, "test"), lambda t, r: results.append((t, r)))
    try:
        pb.open([tmp_path / f"{i}.mp3" for i in range(MAX_QUEUE + 7)], replace=True)
        assert len(pb.queue) == MAX_QUEUE
        assert ("warn", f"The queue holds {MAX_QUEUE} tracks; 7 were not added") in said
        assert pb.waiting is not None  # the first track's answer is awaited, not probed inline
    finally:
        pb.pool.close()


def test_an_empty_queue_is_valid_it_plays_silence_and_reports_nothing_playing(make, tmp_path: Path):
    src, rec = make()
    src.start(rec.on_samples)
    assert wait_until(lambda: rec.count() >= 20)
    src.play()
    src.next()
    src.seek(3.0)
    time.sleep(0.1)
    n = src.now_playing()
    assert (n.index, n.count, n.title, n.playing, n.position) == (None, 0, "", False, 0.0)
    assert src.queue().entries == () and rec.loud() == 0 and not src.failed.is_set()
    src.stop()


# --- no path ever reaches the shell (review A1) -----------------------------------------------------


@pytest.mark.parametrize("how", ["deleted", "unreadable"])
def test_a_file_that_goes_bad_after_the_scan_is_reported_without_a_path(
    make, media: Media, tmp_path: Path, how: str
):
    import os
    import shutil

    secret = tmp_path / "private-user-dir" / "nested"
    secret.mkdir(parents=True)
    f = secret / "song.flac"
    shutil.copy(media.tone_a, f)
    src, rec = make(f, media.tone_b)
    src.start(rec.on_samples)
    assert wait_until(lambda: src.queue().entries[0].duration is not None)  # the preflight passed
    f.unlink() if how == "deleted" else os.chmod(f, 0)
    try:
        src.play()
        assert wait_until(lambda: any("Skipped" in t for _, t in rec.statuses), 8.0)
        assert wait_until(lambda: src.now_playing().title == "Track B")  # and it moved on
    finally:
        if f.exists():
            os.chmod(f, 0o644)
        src.stop()
    expected = "file not found" if how == "deleted" else "cannot be read"
    assert rec.statuses[0] == ("warn", f"Skipped Track A: {expected}")
    parts = {p for p in tmp_path.parts if len(p) > 3} | {"nested", "private-user-dir"}
    for _, text in rec.statuses:
        assert "/" not in text and not any(p in text for p in parts), text
    assert not src.failed.is_set()


def test_every_reason_a_user_can_be_given_is_in_the_fixed_vocabulary(
    make, media: Media, tmp_path: Path
):
    gone = tmp_path / "gone.flac"
    gone.write_bytes(media.mono.read_bytes())
    src, rec = make(media.text, media.jpg, media.noise, media.truncated, gone)
    gone.unlink()
    src.start(rec.on_samples)
    assert wait_until(lambda: all(e.duration or not e.playable for e in src.queue().entries), 8.0)
    src.play()
    wait_until(lambda: any("gone" in t for _, t in rec.statuses), 8.0)
    src.stop()
    allowed = (
        "file not found", "cannot be read", "not an audio file", "could not be decoded",
        "timed out", "tracks that could not be played",
    )  # fmt: skip
    assert rec.statuses
    for _, text in rec.statuses:
        assert text.endswith(allowed), text


# --- stop() and start() never leave two workers (review A2) -------------------------------------------


def workers() -> int:
    return len([t for t in threading.enumerate() if t.name == "tidalviz-player" and t.is_alive()])


def test_a_stop_that_times_out_keeps_the_old_worker_registered_and_start_refuses(
    make, media: Media, monkeypatch: pytest.MonkeyPatch
):
    import tidalviz.player.source as source_mod

    monkeypatch.setattr(source_mod, "JOIN_S", 0.2)
    src, rec = make(media.tone_a)
    inside, release = threading.Event(), threading.Event()

    def stuck(block, t):  # a consumer that does not return: the worker cannot see the stop
        inside.set()
        release.wait(10.0)

    src.start(stuck)
    assert inside.wait(3.0)
    src.stop()  # the join times out
    assert workers() == 1
    with pytest.raises(RuntimeError):  # the pipeline would retry with backoff
        src.start(rec.on_samples)
    assert workers() == 1
    release.set()
    assert wait_until(lambda: workers() == 0)  # the old worker was told to stop and does
    src.start(rec.on_samples)  # now it may
    assert wait_until(lambda: rec.count() >= 3)
    assert workers() == 1
    src.stop()
    assert workers() == 0


def test_stop_then_start_with_a_slow_scan_never_runs_two_workers(make, media: Media):
    from tidalviz.player.engine import probe as real_probe

    gate = threading.Event()

    def slow(p: Path):
        gate.wait(5.0)
        return real_probe(p)

    src, rec = make(media.tone_a, media.tone_b, probe=slow)
    peak = 0
    for _ in range(3):  # what AudioPipeline's supervisor does on a failure: stop, then start
        src.start(rec.on_samples)
        src.play()
        time.sleep(0.1)
        src.stop()
        peak = max(peak, workers())
    assert peak == 0 and workers() == 0  # the worker never waits on a probe
    gate.set()


def test_stop_returns_promptly_while_a_load_waits_for_its_scan_result(make, media: Media):
    gate = threading.Event()
    from tidalviz.player.engine import probe as real_probe

    src, rec = make(media.tone_a, probe=lambda p: (gate.wait(5.0), real_probe(p))[1])
    src.start(rec.on_samples)
    src.play()
    time.sleep(0.2)
    src.stop()
    assert workers() == 0
    gate.set()


# --- the worker never probes (review B3) ---------------------------------------------------------------


def test_the_worker_never_runs_a_probe_and_silence_flows_while_one_is_pending(make, media: Media):
    from tidalviz.player.engine import probe as real_probe

    where: list[str] = []

    def slow(p: Path):
        where.append(threading.current_thread().name)
        time.sleep(0.5)
        return real_probe(p)

    src, rec = make(media.tone_a, probe=slow)
    src.start(rec.on_samples)
    src.play()  # the track is not scanned yet: its answer is awaited
    assert src.now_playing().playing or wait_until(lambda: src.now_playing().playing)
    n0 = rec.count()
    time.sleep(0.3)
    assert rec.count() - n0 >= 15  # silence kept coming at the hop cadence meanwhile
    assert rec.loud() == 0
    assert wait_until(lambda: rec.loud() >= 5, 5.0)  # then the track started by itself
    src.stop()
    assert where and "tidalviz-player" not in where


def test_a_replace_open_probes_each_file_once(make, media: Media):
    from collections import Counter

    from tidalviz.player.engine import probe as real_probe

    seen: Counter[str] = Counter()

    def counting(p: Path):
        seen[p.name] += 1
        time.sleep(0.05)
        return real_probe(p)

    src, rec = make(probe=counting)
    src.start(rec.on_samples)
    src.open([media.tone_a, media.tone_b, media.tone_d], replace=True)
    assert wait_until(
        lambda: all(e.duration for e in src.queue().entries[:3]) and len(src.queue().entries) == 3
    )
    time.sleep(0.3)
    assert set(seen.values()) == {1}, seen
    src.stop()


# --- replacing or clearing cancels the old queue's scan (review B4) ------------------------------------


def test_replace_drops_the_old_queues_pending_probes_and_ignores_their_results(make, media: Media):
    from tidalviz.player.engine import probe as real_probe

    gate = threading.Event()
    started: list[str] = []
    old = [
        media.tone_a,
        media.tone_b,
        media.tone_c,
        media.tone_d,
        media.mono,
        media.ramp,
        media.click,
    ]

    def gated(p: Path):
        started.append(p.name)
        gate.wait(5.0)
        return real_probe(p)

    src, rec = make(*old, probe=gated)
    src.start(rec.on_samples)
    assert wait_until(lambda: len(started) == 4)  # four workers busy, three queued
    src.open([media.tone_a], replace=True)  # (copy of a name: a brand new Track)
    assert wait_until(lambda: src.now_playing().count == 1)
    revision = src.queue().revision
    gate.set()
    assert wait_until(
        lambda: src.queue().entries and src.queue().entries[0].duration is not None, 5.0
    )
    time.sleep(0.3)
    assert (
        len(started) <= 4 + 1 + 1
    )  # the in-flight four, the new queue's track (maybe urgent + normal)
    assert src.queue().revision - revision <= 2  # results for dropped tracks did not bump it
    src.stop()


# --- an open that finds nothing changes nothing (protocol 3) ----------------------------------------------


def test_opening_something_with_no_audio_says_so_and_leaves_the_queue_and_playback_alone(
    make, media: Media, tmp_path: Path
):
    src, rec = make(media.tone_a, media.tone_b)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: src.now_playing().playing and rec.loud() >= 3)
    empty = tmp_path / "just-covers"
    empty.mkdir()
    (empty / "cover.jpg").write_bytes(b"x")
    src.open([empty], replace=True)
    assert rec.statuses[-1] == ("info", "No audio files found")
    time.sleep(0.2)
    n = src.now_playing()
    assert n.count == 2 and n.playing and n.index == 0
    src.stop()


# --- seeks (protocol 1, 4 and the rejected case) ------------------------------------------------------------


def test_seeks_queued_together_run_once_with_the_newest_target(make, media: Media):
    src, rec = make(media.ramp)
    for s in (1.0, 2.0, 3.0, 4.0, 5.0):  # key auto-repeat, before the worker gets to them
        src.seek(s)
    src.start(rec.on_samples)
    assert wait_until(lambda: src.now_playing().seeks >= 1 and src.now_playing().position == 5.0)
    time.sleep(0.2)
    assert src.now_playing().seeks == 1
    src.stop()


def test_every_seek_attempt_is_a_reported_change_even_when_the_backend_refuses(
    make, media: Media, monkeypatch: pytest.MonkeyPatch
):
    import tidalviz.player.playback as pbm
    from tidalviz.player.engine import GstBackend

    class Refuses(GstBackend):
        def seek(self, seconds, cancelled=lambda: False):  # type: ignore[no-untyped-def]
            return False

    monkeypatch.setattr(pbm, "GstBackend", Refuses)
    src, rec = make(media.ramp)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: src.now_playing().playing)
    src.pause()
    assert wait_until(lambda: not src.now_playing().playing)
    before, changes = src.now_playing().seeks, rec.changes
    src.seek(7.0)
    assert wait_until(lambda: src.now_playing().seeks == before + 1)
    assert rec.changes > changes  # the host is told, so the shell can drop its optimistic target
    assert src.now_playing().position < 3.0  # the true position, not the refused target
    assert ("warn", "This file cannot be seeked") in rec.statuses
    src.stop()


def test_previous_on_the_first_track_counts_as_a_seek(make, media: Media):
    src, rec = make(media.ramp)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: src.now_playing().playing)
    before = src.now_playing().seeks
    src.previous()
    assert wait_until(lambda: src.now_playing().seeks == before + 1)
    src.stop()


# --- cost over time, on the worker loop (review: tighten) -------------------------------------------------------


@pytest.mark.perf
def test_the_worker_loop_retains_nothing_that_grows_with_the_number_of_blocks(
    make, gst, tmp_path: Path
):
    """Real time, ~17 s through the whole worker (pull, copy, poll, publish, on_samples).

    Only allocations made by our own player and capture modules are counted (`tracemalloc`
    sees every thread, and earlier tests leave daemon threads and garbage behind). A long
    warm-up, then four windows of 250 blocks. The residue of small objects plateaus (≈11 KB
    after 14,000 blocks, with occasional one-off steps of ~4 KB), so the rule is about the
    slope: a real leak grows in every window, a one-off step in one. Leaking one small object per
    block adds ≥ 10 KB per window; the bound is 2 KB."""
    import gc
    import tracemalloc
    import wave

    f = tmp_path / "long.wav"
    n = 48000 * 25
    x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(n) / 48000) * 32767).astype("<i2")
    with wave.open(str(f), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.stack([x, x], 1).tobytes())
    src, _ = make(f)
    seen = [0]

    def count(block, t):  # allocation-free consumer
        seen[0] += 1

    def retained_now() -> int:
        gc.collect()
        ours = [
            tracemalloc.Filter(True, "*/tidalviz/player/*"),
            tracemalloc.Filter(True, "*/tidalviz/capture/*"),
        ]
        return sum(
            st.size for st in tracemalloc.take_snapshot().filter_traces(ours).statistics("filename")
        )

    src.start(count)
    src.play()
    tracemalloc.start()
    try:
        assert wait_until(lambda: seen[0] >= 500, 15.0)  # warm-up
        readings = [retained_now()]
        for _ in range(4):
            target = seen[0] + 250
            assert wait_until(lambda t=target: seen[0] >= t, 15.0)
            readings.append(retained_now())
    finally:
        tracemalloc.stop()
        src.stop()
    deltas = [b - a for a, b in itertools.pairwise(readings)]
    assert min(deltas) < 2048, (readings, deltas)


def test_the_clicks_arrive_on_the_files_own_grid_counted_in_blocks_not_seconds(make, media: Media):
    """Timing-free: blocks are never dropped or doubled, so the loud blocks of a click track sit
    where the samples put them (a click every 24,000 frames = 46.875 blocks)."""
    src, rec = make(media.click)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: not src.now_playing().playing and rec.loud() > 0, 15.0)
    src.stop()
    peaks = rec.peaks()
    loud = np.flatnonzero(peaks > 1e-3)
    starts = [int(i) for k, i in enumerate(loud) if k == 0 or i - loud[k - 1] > 5]
    assert len(starts) == 10, starts
    assert set(np.diff(starts)) <= {46, 47, 48}, np.diff(starts)


# --- one status per run of skipped tracks (manual test item 9) ------------------------------------------


@pytest.mark.parametrize("scanned_first", [True, False])
def test_a_single_bad_track_gets_its_own_status_whoever_finds_it_out_first(
    make, media: Media, scanned_first: bool
):
    from tidalviz.player.engine import probe as real_probe

    delay = 0.0 if scanned_first else 0.4

    def probe(p: Path):
        time.sleep(delay)
        return real_probe(p)

    src, rec = make(media.text, probe=probe)
    src.start(rec.on_samples)
    if scanned_first:
        assert wait_until(lambda: not src.queue().entries[0].playable)
    src.play()
    assert wait_until(lambda: rec.statuses, 5.0)
    time.sleep(0.3)
    assert rec.statuses == [("warn", "Skipped lyrics: not an audio file")]
    src.stop()


def test_a_run_of_skips_before_a_playable_track_is_one_status_and_the_track_plays(
    make, media: Media, tmp_path: Path
):
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    src, rec = make(media.text, empty, media.tone_a)
    src.start(rec.on_samples)
    assert wait_until(
        lambda: not src.queue().entries[0].playable and not src.queue().entries[1].playable
    )
    src.play()
    assert wait_until(lambda: src.now_playing().index == 2 and rec.loud() >= 3, 5.0)
    assert rec.statuses == [("warn", "Skipped 2 tracks that could not be played")]
    src.stop()


# --- the load serial (manual test item 4) ------------------------------------------------------------------


def test_the_serial_bumps_on_every_load_and_a_restart_of_the_same_track(make, media: Media):
    src, rec = make(media.ramp, media.tone_a)
    src.start(rec.on_samples)
    serials = [src.now_playing().serial]
    for act in (src.play, lambda: src.select(0), src.next, src.previous):
        act()
        assert wait_until(lambda s=serials[-1]: src.now_playing().serial > s), act
        serials.append(src.now_playing().serial)
    src.previous()  # first track: it starts over
    assert wait_until(lambda: src.now_playing().serial > serials[-1])
    src.stop()


def test_a_seek_for_a_track_that_is_gone_is_answered_but_not_performed(make, media: Media):
    src, rec = make(media.tone_a)
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: src.now_playing().playing)
    old = src.now_playing().serial
    assert wait_until(lambda: not src.now_playing().playing, 6.0)  # the end: rewound and stopped
    rewound = src.now_playing()
    assert rewound.index == 0 and rewound.serial > old and rewound.position == 0.0
    changes, seeks = rec.changes, rewound.seeks
    src.seek(40.0, old)  # the stale one the shell sent a moment too late
    assert wait_until(lambda: src.now_playing().seeks == seeks + 1)
    assert rec.changes > changes  # answered, so the shell resyncs
    time.sleep(0.2)
    n = src.now_playing()
    assert n.position == 0.0 and not n.playing and n.serial == rewound.serial
    src.seek(0.5, rewound.serial)  # a matching serial seeks
    assert wait_until(lambda: src.now_playing().position == 0.5)
    src.stop()


# --- waiting for a scan answer (review 3) --------------------------------------------------------------------


def test_a_scan_that_never_answers_ends_the_wait_as_a_timeout(make, media: Media):
    gate = threading.Event()
    src, rec = make(media.tone_a, probe=lambda p: (gate.wait(5.0), None)[1], scan_timeout=0.3)  # type: ignore[arg-type,return-value]
    src.start(rec.on_samples)
    src.play()
    assert wait_until(lambda: rec.statuses, 3.0)
    assert rec.statuses == [("warn", "Skipped tone_a: timed out")]
    assert not src.failed.is_set()
    gate.set()
    src.stop()


def test_pause_during_the_wait_cancels_the_pending_play_and_play_restores_it(make, media: Media):
    from tidalviz.player.engine import probe as real_probe

    src, rec = make(media.tone_a, probe=lambda p: (time.sleep(0.5), real_probe(p))[1])
    src.start(rec.on_samples)
    src.play()
    assert src.now_playing().playing or wait_until(lambda: src.now_playing().playing)
    src.pause()
    assert wait_until(lambda: not src.now_playing().playing)
    time.sleep(1.0)  # the answer arrived meanwhile: the track is loaded, but paused
    assert rec.loud() == 0 and not src.now_playing().playing
    src.toggle()
    assert wait_until(lambda: rec.loud() >= 3 and src.now_playing().playing)
    src.stop()


# --- the two ways a pulled block can be wrong end as the fixed output failure ----------------------------


class _FakeBuffer:
    def __init__(self, mapped: bool, size: int) -> None:
        self.mapped, self.size, self.unmapped = mapped, size, False

    def map(self, flags):  # type: ignore[no-untyped-def]
        return self.mapped, types.SimpleNamespace(size=self.size, data=b"\0" * self.size)

    def unmap(self, info):  # type: ignore[no-untyped-def]
        self.unmapped = True


class _FakeAppSink:
    def __init__(self, buf: _FakeBuffer) -> None:
        self.buf = buf

    def try_pull_sample(self, timeout_ns: int):  # type: ignore[no-untyped-def]
        return types.SimpleNamespace(get_buffer=lambda: self.buf)


@pytest.mark.parametrize(
    ("mapped", "size", "logged"), [(False, 4096, "cannot map"), (True, 100, "audio block of 100")]
)
def test_an_unmappable_or_wrongly_sized_block_raises_the_fixed_output_failure(
    gst, caplog: pytest.LogCaptureFixture, mapped: bool, size: int, logged: str
):
    from tidalviz.player.engine import OUTPUT_FAILED, EngineError, GstBackend
    from tidalviz.player.sink import SinkSpec

    backend = GstBackend(SinkSpec("fakesink"))
    buf = _FakeBuffer(mapped, size)
    backend._appsink = _FakeAppSink(buf)  # the appsink is GStreamer's: a stand-in for a bad block
    with caplog.at_level("ERROR"), pytest.raises(EngineError) as e:
        backend.pull(0.1, np.zeros((BLOCK, 2), dtype=np.float32))
    assert str(e.value) == OUTPUT_FAILED  # the log has the details, the status does not
    assert logged in caplog.text and str(size) not in str(e.value)
    assert buf.unmapped is mapped  # a mapped buffer is always released
    backend.close()


# --- what the user hears when the folder walk gives up -------------------------------------------------------


def test_a_folder_walk_that_hits_its_entry_cap_with_no_audio_is_a_warning_not_silence(
    make, media: Media, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import tidalviz.player.source as source_mod
    from tidalviz.player.library import collect_audio_files as real

    monkeypatch.setattr(source_mod, "collect_audio_files", lambda paths: real(paths, entry_limit=5))
    big = tmp_path / "home"
    big.mkdir()
    for i in range(20):
        (big / f"note{i:02}.txt").write_text("x")
    src, rec = make(media.tone_a)
    src.start(rec.on_samples)
    src.open([big], replace=True)
    assert rec.statuses == [
        ("warn", "No audio files found in the part searched (the folder is very large)")
    ]
    assert src.now_playing().count == 1  # nothing was changed
    src.stop()


def test_a_folder_walk_cut_by_the_entry_cap_keeps_the_first_files_and_says_so(
    make, tmp_path: Path, media: Media, monkeypatch: pytest.MonkeyPatch
):
    import tidalviz.player.source as source_mod
    from tidalviz.player.library import collect_audio_files as real

    monkeypatch.setattr(source_mod, "collect_audio_files", lambda paths: real(paths, entry_limit=5))
    big = tmp_path / "lib"
    big.mkdir()
    for n in ("a.flac", "b.flac"):
        (big / n).write_bytes(media.mono.read_bytes())
    (big / "zz").mkdir()
    for i in range(20):
        (big / "zz" / f"z{i:02}.txt").write_text("x")
    src, rec = make()
    src.start(rec.on_samples)
    src.open([big], replace=True)
    assert wait_until(lambda: src.now_playing().count == 2)
    assert rec.statuses == [
        ("warn", "The folder is too big to search completely: found 2 audio files before stopping")
    ]
    assert [e.title for e in src.queue().entries] == ["a", "b"]
    src.stop()


# --- chosen paths that do not exist (a count, never a name) -----------------------------------------------------------


def test_missing_paths_next_to_good_ones_are_counted_in_one_status(
    make, media: Media, tmp_path: Path
):
    src, rec = make()
    src.start(rec.on_samples)
    src.open(
        [tmp_path / "secret-gone-1.mp3", media.tone_a, tmp_path / "secret-gone-2.mp3"], replace=True
    )
    assert wait_until(lambda: src.now_playing().count == 1)
    assert rec.statuses == [("warn", "2 files were not found")]
    src.stop()


def test_a_single_missing_path_alone_is_one_status(make, tmp_path: Path):
    src, rec = make()
    src.open([tmp_path / "secret-gone.mp3"], replace=True)
    assert rec.statuses == [("warn", "1 file was not found")]  # that already covers every path
    assert not any("secret" in t for _, t in rec.statuses)


def test_a_missing_path_next_to_a_folder_without_audio_still_says_no_audio(make, tmp_path: Path):
    src, rec = make()
    (tmp_path / "covers").mkdir()
    (tmp_path / "covers" / "a.jpg").write_bytes(b"x")
    src.open([tmp_path / "gone.mp3", tmp_path / "covers"], replace=True)
    assert rec.statuses == [("warn", "1 file was not found"), ("info", "No audio files found")]
