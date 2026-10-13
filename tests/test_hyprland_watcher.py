"""The Hyprland watcher against a stand-in Hyprland: real Unix sockets speaking its protocols
(`.socket.sock`: one request per connection, reply, close; `.socket2.sock`: "EVENT>>DATA" lines).
"""

import asyncio
import contextlib
import json
import logging
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from tests.test_hyprland import MONITORS, PID, client
from tidalviz.hyprland import HyprlandWatcher, watcher_from_env


class FakeHyprland:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.clients: Any = [client(1)]
        self.monitors: Any = MONITORS
        self.requests: list[str] = []
        self.listeners: list[asyncio.StreamWriter] = []
        self.connects = 0  # to the event socket
        self.drop = False  # accept an event connection, then close it at once
        self._servers: list[asyncio.Server] = []

    async def start(self) -> None:
        self._servers = [
            await asyncio.start_unix_server(self._request, path=self.folder / ".socket.sock"),
            await asyncio.start_unix_server(self._events, path=self.folder / ".socket2.sock"),
        ]

    async def _request(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        cmd = (await r.read(1024)).decode()
        self.requests.append(cmd)
        reply = {"j/clients": self.clients, "j/monitors": self.monitors}.get(cmd)
        if reply is None:
            reply = b"unknown request"
        w.write(reply if isinstance(reply, bytes) else json.dumps(reply).encode())
        await w.drain()
        w.close()

    async def _events(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        self.connects += 1
        if self.drop:
            w.close()
            return
        self.listeners.append(w)
        with contextlib.suppress(ConnectionError):
            await r.read()  # held open until the client leaves or `hang_up`

    async def emit(self, *lines: str | bytes) -> None:
        for w in self.listeners:
            for line in lines:
                w.write(line if isinstance(line, bytes) else line.encode() + b"\n")
            await w.drain()

    async def hang_up(self) -> None:
        for w in self.listeners:
            w.close()
        self.listeners.clear()

    async def close(self) -> None:
        await self.hang_up()
        for s in self._servers:
            s.close()
            await s.wait_closed()
        for name in (".socket.sock", ".socket2.sock"):
            (self.folder / name).unlink(missing_ok=True)


@pytest.fixture
def folder() -> Iterator[Path]:
    # AF_UNIX paths are limited to ~108 bytes; pytest's tmp_path can be longer.
    path = Path(tempfile.mkdtemp(prefix="hypr"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def hypr(folder: Path) -> AsyncIterator[FakeHyprland]:
    h = FakeHyprland(folder)
    await h.start()
    yield h
    await h.close()


class Changes(list[bool]):
    def __call__(self, visible: bool) -> None:
        self.append(visible)


async def until(cond: Callable[[], bool], timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.005)


@pytest_asyncio.fixture
async def watch(folder: Path) -> AsyncIterator[Callable[[], tuple[HyprlandWatcher, Changes]]]:
    made: list[HyprlandWatcher] = []

    def make() -> tuple[HyprlandWatcher, Changes]:
        changes = Changes()
        w = HyprlandWatcher(folder, PID, changes, debounce=0.02, backoff=(0.05, 0.1))
        w.start()
        made.append(w)
        return w, changes

    yield make
    for w in made:
        await w.stop()


@pytest.mark.asyncio
async def test_reports_hidden_and_shown_as_our_window_leaves_and_returns(hypr, watch):
    w, changes = watch()
    await until(lambda: len(hypr.requests) == 2)  # the first look, right after connecting
    assert w.visible and changes == []
    hypr.clients = [client(21)]
    await hypr.emit("movewindowv2>>0x1,21,21")
    await until(lambda: changes == [False])
    assert not w.visible
    hypr.clients = [client(1)]
    await hypr.emit("workspacev2>>1,1")
    await until(lambda: changes == [False, True])


@pytest.mark.asyncio
async def test_a_burst_of_events_costs_one_look(hypr, watch):
    _, changes = watch()
    await until(lambda: len(hypr.requests) == 2)
    hypr.clients = [client(21)]
    await hypr.emit(
        "workspace>>4", "workspacev2>>4,4", "focusedmon>>OUT-1,4", "focusedmonv2>>OUT-1,4"
    )
    await until(lambda: changes == [False])
    await asyncio.sleep(0.1)
    assert sorted(hypr.requests) == ["j/clients"] * 2 + ["j/monitors"] * 2


@pytest.mark.asyncio
async def test_high_rate_and_malformed_lines_cost_nothing(hypr, watch):
    _, changes = watch()
    await until(lambda: len(hypr.requests) == 2)
    await hypr.emit(
        "activewindow>>kitty,title", "activewindowv2>>0x1", "windowtitlev2>>0x1,title",
        "no separator", b"\xff\xfe>>\x00\n", "", ">>", "x" * 200_000,
    )  # fmt: skip
    await asyncio.sleep(0.15)
    assert len(hypr.requests) == 2
    hypr.clients = [client(21)]
    await hypr.emit("closewindow>>0x2")  # still listening
    await until(lambda: changes == [False])


@pytest.mark.asyncio
async def test_a_running_screencast_counts_as_visible(hypr, watch):
    hypr.clients = [client(21)]
    _, changes = watch()
    await until(lambda: changes == [False])
    await hypr.emit("screencast>>1,0")
    await until(lambda: changes == [False, True])
    await hypr.emit("screencast>>0,0")
    await until(lambda: changes == [False, True, False])


@pytest.mark.asyncio
async def test_an_unexpected_reply_reads_as_visible(hypr, watch):
    hypr.clients = [client(21)]
    _, changes = watch()
    await until(lambda: changes == [False])
    hypr.monitors = None  # the stand-in answers "unknown request", like Hyprland
    await hypr.emit("monitorremovedv2>>1,OUT-1,desc")
    await until(lambda: changes == [False, True])


@pytest.mark.asyncio
async def test_fails_open_when_hyprland_goes_away_and_picks_up_again(hypr, watch):
    hypr.clients = [client(21)]
    _, changes = watch()
    await until(lambda: changes == [False])
    await hypr.hang_up()
    await until(lambda: changes == [False, True])
    await until(lambda: changes == [False, True, False])  # reconnected after the backoff


@pytest.mark.asyncio
async def test_an_event_socket_that_keeps_dropping_backs_off_and_warns_once(
    hypr, folder: Path, caplog: pytest.LogCaptureFixture
):
    hypr.drop = True  # accepts, then closes: a connect that never turns into a working stream
    w = HyprlandWatcher(folder, PID, Changes(), debounce=0.02, backoff=(0.05, 0.1, 0.2, 0.4))
    with caplog.at_level(logging.DEBUG, logger="tidalviz.hyprland"):
        w.start()
        try:
            await asyncio.sleep(0.6)
        finally:
            await w.stop()
    assert 2 <= hypr.connects <= 4  # 0, 0.1, 0.3 s; without backing off, one every 0.05 s
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


@pytest.mark.asyncio
async def test_a_drop_after_a_healthy_connection_warns_again(
    hypr, folder: Path, caplog: pytest.LogCaptureFixture
):
    def warnings() -> int:
        return len([r for r in caplog.records if r.levelno >= logging.WARNING])

    w = HyprlandWatcher(folder, PID, Changes(), backoff=(0.05,), stable_after=0.1)
    with caplog.at_level(logging.DEBUG, logger="tidalviz.hyprland"):
        w.start()
        try:
            for n in (1, 2):
                await until(lambda n=n: hypr.connects == n and len(hypr.listeners) == 1)
                await asyncio.sleep(0.15)  # healthy
                await hypr.hang_up()
                await until(lambda n=n: warnings() == n)
        finally:
            await w.stop()


@pytest.mark.asyncio
async def test_waits_for_hyprland_to_appear(folder: Path, watch):
    w, changes = watch()
    await asyncio.sleep(0.2)  # several failed attempts
    assert w.visible and changes == []
    hypr = FakeHyprland(folder)
    hypr.clients = [client(21)]
    await hypr.start()
    try:
        await until(lambda: changes == [False])
    finally:
        await hypr.close()


@pytest.mark.asyncio
async def test_stop_is_prompt_whether_connected_or_waiting(hypr, folder: Path):
    for target in (folder, folder / "missing"):
        w = HyprlandWatcher(target, PID, Changes(), backoff=(30.0,))
        w.start()
        await asyncio.sleep(0.05)
        async with asyncio.timeout(1.0):
            await w.stop()


def test_from_env_only_under_hyprland_and_not_when_opted_out():
    sig = {"HYPRLAND_INSTANCE_SIGNATURE": "abc_1_2", "XDG_RUNTIME_DIR": "/run/user/7"}
    w = watcher_from_env(PID, Changes(), sig)
    assert w is not None and w.socket_dir == Path("/run/user/7/hypr/abc_1_2")
    assert watcher_from_env(PID, Changes(), {"XDG_RUNTIME_DIR": "/run/user/7"}) is None
    assert watcher_from_env(PID, Changes(), {**sig, "TIDALVIZ_HYPRLAND_VISIBILITY": "0"}) is None
