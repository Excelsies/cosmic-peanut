"""The hand-over of files to a running instance (docs/protocols.md §6): a per-user Unix socket."""

import json
import os
import socket
import stat
import threading
import time
from pathlib import Path

import pytest

from tidalviz.instance import (
    MAX_MESSAGE_BYTES,
    MAX_PATHS,
    Env,
    acquire,
    socket_path,
)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    return Env(runtime_dir=run, tmp_dir=tmp_path / "tmp", uid=os.getuid())


class Collector:
    def __init__(self) -> None:
        self.got: list[list[Path]] = []
        self.event = threading.Event()

    def __call__(self, paths: list[Path]) -> None:
        self.got.append(paths)
        self.event.set()

    def wait(self, n: int = 1, timeout: float = 3.0) -> bool:
        end = time.monotonic() + timeout
        while len(self.got) < n and time.monotonic() < end:
            time.sleep(0.01)
        return len(self.got) >= n


def talk(path: Path, payload: bytes, *, read: bool = True, timeout: float = 3.0) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall(payload)
        return s.recv(100) if read else b""


# --- where the socket lives ----------------------------------------------------------------------------


def test_the_socket_is_keyed_by_the_data_dir_and_lives_under_the_runtime_dir(
    env: Env, tmp_path: Path
):
    a = socket_path(tmp_path / "a", env)
    assert a == socket_path(tmp_path / "a" / ".." / "a", env)  # the resolved dir is what counts
    assert a != socket_path(tmp_path / "b", env)
    assert a.parent == env.runtime_dir / "tidalviz" and a.suffix == ".sock"


def test_a_very_long_data_dir_still_gives_a_short_socket_path(env: Env, tmp_path: Path):
    deep = tmp_path.joinpath(*["very-long-directory-name-" * 3] * 6)
    assert len(os.fsencode(socket_path(deep, env))) < 100  # sun_path holds 104-108 bytes


def test_without_a_runtime_dir_a_private_per_user_dir_under_tmp_is_used(tmp_path: Path):
    env = Env(runtime_dir=None, tmp_dir=tmp_path / "tmp", uid=4242)
    assert socket_path(tmp_path / "x", env).parent == tmp_path / "tmp" / "tidalviz-4242"


def test_a_runtime_dir_too_long_for_a_socket_falls_back_to_tmp(tmp_path: Path):
    long_run = tmp_path.joinpath(*["r" * 40] * 4)
    env = Env(runtime_dir=long_run, tmp_dir=tmp_path / "t", uid=1)
    assert socket_path(tmp_path / "x", env).parent == tmp_path / "t" / "tidalviz-1"


# --- the first instance, a second launch -----------------------------------------------------------------


def test_the_first_launch_owns_a_private_socket_and_a_second_hands_over_its_files(
    env: Env, tmp_path: Path
):
    first = acquire(tmp_path / "data", None, env)
    assert first.listener is not None and not first.handed_over
    sock = first.listener.path
    assert stat.S_IMODE(sock.stat().st_mode) == 0o600
    assert stat.S_IMODE(sock.parent.stat().st_mode) == 0o700
    seen = Collector()
    first.listener.serve(seen)
    second = acquire(tmp_path / "data", [Path("/m/a.mp3"), Path("/m/b dir")], env)
    assert second.handed_over and second.listener is None
    assert seen.wait() and seen.got == [[Path("/m/a.mp3"), Path("/m/b dir")]]
    first.listener.close()
    assert not sock.exists()  # removed on a clean shutdown


def test_another_data_dir_is_another_instance(env: Env, tmp_path: Path):
    a = acquire(tmp_path / "a", None, env)
    b = acquire(tmp_path / "b", [Path("/m/x.mp3")], env)
    assert a.listener is not None and b.listener is not None and not b.handed_over
    a.listener.close()
    b.listener.close()


def test_a_launch_without_files_next_to_a_running_instance_just_starts_without_a_listener(
    env: Env, tmp_path: Path
):
    first = acquire(tmp_path / "d", None, env)
    again = acquire(tmp_path / "d", None, env)
    assert again.listener is None and not again.handed_over
    assert first.listener is not None
    first.listener.close()


def test_a_stale_socket_left_by_a_crash_is_replaced(env: Env, tmp_path: Path):
    path = socket_path(tmp_path / "d", env)
    path.parent.mkdir(mode=0o700)
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(path))
    s.close()  # no listen, no unlink: what a killed process leaves
    assert path.exists()
    got = acquire(tmp_path / "d", [Path("/m/a.mp3")], env)
    assert got.listener is not None and not got.handed_over
    got.listener.close()


def test_something_that_is_not_a_socket_is_left_alone(env: Env, tmp_path: Path):
    path = socket_path(tmp_path / "d", env)
    path.parent.mkdir(mode=0o700)
    path.write_text("mine")
    got = acquire(tmp_path / "d", [Path("/m/a.mp3")], env)
    assert got.listener is None and not got.handed_over
    assert path.read_text() == "mine"


def test_a_runtime_dir_other_users_could_enter_is_refused(env: Env, tmp_path: Path):
    d = socket_path(tmp_path / "d", env).parent
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    got = acquire(tmp_path / "d", [Path("/m/a.mp3")], env)
    assert got.listener is None and not got.handed_over  # start normally, no listener


def test_two_launches_racing_never_lose_files_and_only_one_listens(env: Env, tmp_path: Path):
    results: list[tuple[int, object]] = []
    seen = Collector()
    barrier = threading.Barrier(6)

    def launch(i: int) -> None:
        barrier.wait()
        r = acquire(tmp_path / "d", [Path(f"/m/{i}.mp3")], env)
        if r.listener is not None:
            r.listener.serve(seen)
        results.append((i, r))

    threads = [threading.Thread(target=launch, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    owners = [(i, r) for i, r in results if r.listener is not None]  # type: ignore[attr-defined]
    handed = {Path(f"/m/{i}.mp3") for i, r in results if r.handed_over}  # type: ignore[attr-defined]
    assert len(owners) == 1 and len(handed) == 5  # nobody is left out
    assert seen.wait(5) and sum(len(g) for g in seen.got) == 5  # the owner keeps its own files
    assert {p for g in seen.got for p in g} == handed
    owners[0][1].listener.close()  # type: ignore[attr-defined]


# --- what the listener will not believe ------------------------------------------------------------------------


@pytest.fixture
def listening(env: Env, tmp_path: Path):
    r = acquire(tmp_path / "d", None, env)
    assert r.listener is not None
    seen = Collector()
    r.listener.serve(seen, read_timeout=0.3)
    yield r.listener.path, seen
    r.listener.close()


def good(path: Path) -> bytes:
    return talk(path, json.dumps({"v": 1, "paths": ["/m/ok.mp3"]}).encode() + b"\n")


@pytest.mark.parametrize(
    "payload",
    [
        b"not json\n",
        b"\xff\xfe\x00garbage\n",
        b'{"v": 1}\n',
        b'{"v": 2, "paths": ["/a"]}\n',
        b'{"v": 1, "paths": "/a"}\n',
        b'{"v": 1, "paths": ["relative/a.mp3"]}\n',
        b'{"v": 1, "paths": ["/a\\u0000b"]}\n',
        b'{"v": 1, "paths": [3]}\n',
        b'{"v": 1, "paths": []}\n',
        b'["/a"]\n',
        b'{"v": 1, "paths": ["' + b"/" + b"x" * 5000 + b'"]}\n',
        json.dumps({"v": 1, "paths": [f"/m/{i}" for i in range(MAX_PATHS + 1)]}).encode() + b"\n",
        b"x" * (MAX_MESSAGE_BYTES + 10),
    ],
)
def test_a_bad_message_is_refused_and_the_listener_carries_on(listening, payload: bytes):
    path, seen = listening
    try:
        reply = talk(path, payload)
    except OSError:  # the listener may hang up on an oversize message
        reply = b""
    assert not reply.startswith(b"ok")
    assert seen.got == []
    assert good(path).startswith(b"ok") and seen.wait()  # still serving


def test_a_client_that_sends_nothing_is_dropped_by_the_read_timeout_without_blocking_others(
    listening,
):
    path, _ = listening
    slow = socket.socket(socket.AF_UNIX)
    slow.settimeout(5)
    slow.connect(str(path))
    slow.sendall(b'{"v": 1, "paths": ["/m/slo')  # never finishes the line
    assert good(path).startswith(b"ok")  # served once the slow one has timed out (0.3 s)
    assert (
        slow.recv(10) == b""
    )  # and the slow one was hung up on (dropped, no reply), not left waiting
    slow.close()


def test_a_callback_that_raises_does_not_stop_the_listener(env: Env, tmp_path: Path):
    r = acquire(tmp_path / "d", None, env)
    assert r.listener is not None
    calls: list[int] = []
    ran = threading.Semaphore(0)  # released by each callback; the reply comes before the callback

    def boom(paths: list[Path]) -> None:
        calls.append(1)
        ran.release()
        raise RuntimeError("host bug")

    r.listener.serve(boom)
    assert good(r.listener.path).startswith(b"ok") and ran.acquire(timeout=5)
    assert good(r.listener.path).startswith(b"ok") and ran.acquire(timeout=5)  # still serving
    assert len(calls) == 2
    r.listener.close()


def test_a_listener_that_never_answers_makes_the_sender_give_up_and_start_normally(
    env: Env, tmp_path: Path
):
    path = socket_path(tmp_path / "d", env)
    path.parent.mkdir(mode=0o700)
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)  # accepts into the backlog, never replies
    t0 = time.monotonic()
    got = acquire(tmp_path / "d", [Path("/m/a.mp3")], env, ack_timeout=0.3)
    srv.close()
    assert got.listener is None and not got.handed_over  # the caller starts normally with its files
    assert time.monotonic() - t0 < 10  # it came back at all; the bound only catches a hang


def test_a_lock_file_that_is_a_symlink_is_not_followed(env: Env, tmp_path: Path):
    path = socket_path(tmp_path / "d", env)
    path.parent.mkdir(mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    path.with_suffix(".lock").symlink_to(victim)
    got = acquire(tmp_path / "d", [Path("/m/a.mp3")], env)
    assert got.listener is None and not got.handed_over  # start normally
    assert victim.read_text() == "precious"


def test_a_persistent_accept_error_is_logged_once_then_the_listener_gives_up(
    env: Env, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    r = acquire(tmp_path / "d", None, env)
    assert r.listener is not None
    with caplog.at_level("WARNING"):
        r.listener.serve(Collector(), retry_delay=0.001)
        r.listener._sock.close()  # every accept() now fails with EBADF
        thread = r.listener._thread
        assert thread is not None
        thread.join(5)
    assert not thread.is_alive()  # it stopped by itself
    failures = [m for m in caplog.messages if "accept failed" in m]
    assert len(failures) == 1  # not one line per failure
    assert any("giving up" in m for m in caplog.messages)
    r.listener.close()  # a later close is still fine
    # ... and the next launch finds nobody listening and takes over, as documented
    again = acquire(tmp_path / "d", [Path("/m/a.mp3")], env)
    assert again.listener is not None and not again.handed_over
    again.listener.close()


def test_one_accept_error_followed_by_a_good_client_keeps_the_listener_and_resets_the_count(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    r = acquire(tmp_path / "d", None, env)
    assert r.listener is not None
    seen = Collector()
    real_accept = r.listener._sock.accept
    flaky = {"left": 3}

    class Sock:
        def __getattr__(self, name):  # delegate everything else to the real socket
            return getattr(r.listener._sock_real, name)

    r.listener._sock_real = r.listener._sock  # type: ignore[attr-defined]
    wrapper = Sock()

    def accept():
        if flaky["left"]:
            flaky["left"] -= 1
            raise OSError("transient")
        return real_accept()

    wrapper.accept = accept  # type: ignore[attr-defined]
    r.listener._sock = wrapper  # type: ignore[assignment]
    r.listener.serve(seen, retry_delay=0.001)
    assert good(r.listener.path).startswith(b"ok") and seen.wait()  # served after the errors
    assert r.listener._thread is not None and r.listener._thread.is_alive()
    r.listener._sock = r.listener._sock_real  # type: ignore[attr-defined]
    r.listener.close()
