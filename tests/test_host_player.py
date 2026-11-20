"""The host and the transport capability: control messages in, `nowPlaying` / `queue` out.

Most tests use a fake source that implements the capability (the host must work with any source
that does); the end-to-end ones at the bottom use the real player with a fakesink output.
"""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import aiohttp
import pytest
import pytest_asyncio

from tests.test_host import BUILTINS, FakeWindow, Shell, connect
from tidalviz.capture.base import SourceFormat
from tidalviz.capture.transport import (
    Changed,
    NowPlaying,
    QueueSnapshot,
    StatusSink,
    TrackInfo,
)
from tidalviz.host import Host


class FakeTransportSource:
    """An AudioSource that is also a Transport and a QueueControl, driven by the test."""

    def __init__(self) -> None:
        self.failed = threading.Event()
        self.calls: list[tuple[Any, ...]] = []
        self.stopped = threading.Event()
        self.extensions = (".mp3", ".flac")
        self.last_folder: str | None = "/music/last"
        self.np = NowPlaying("Band", "Album", "Song", 200.0, 12.5, True, 0, 2)
        self.entries = (
            TrackInfo("Song", "Band", "Album", 200.0, True),
            TrackInfo("Other", "", "", None, False),
        )
        self.revision = 1
        self._on_change: Changed | None = None
        self._on_status: StatusSink | None = None
        self.select_error = False

    @property
    def format(self) -> SourceFormat:
        return SourceFormat(48000.0, 2)

    def start(self, on_samples: Any) -> None:
        pass

    def stop(self) -> None:
        self.stopped.set()

    def bind(self, on_change: Changed, on_status: StatusSink) -> None:
        self._on_change, self._on_status = on_change, on_status

    def now_playing(self) -> NowPlaying:
        return self.np

    def queue(self) -> QueueSnapshot:
        return QueueSnapshot(self.revision, self.entries)

    def play(self) -> None:
        self.calls.append(("play",))

    def pause(self) -> None:
        self.calls.append(("pause",))

    def toggle(self) -> None:
        self.calls.append(("toggle",))

    def next(self) -> None:
        self.calls.append(("next",))

    def previous(self) -> None:
        self.calls.append(("previous",))

    def seek(self, seconds: float, serial: int | None = None) -> None:
        self.calls.append(("seek", seconds, serial))

    def select(self, index: int) -> None:
        if self.select_error:
            raise ValueError("no such entry")
        self.calls.append(("select", index))

    def open(self, paths: Sequence[Path], *, replace: bool) -> None:
        self.calls.append(("open", list(paths), replace, threading.current_thread().name))

    def clear(self) -> None:
        self.calls.append(("clear",))

    # test controls
    def change(self, **fields: Any) -> None:
        """Change state and report it from another thread, as the real worker does."""
        for k, v in fields.items():
            setattr(self, k, v)
        assert self._on_change is not None
        threading.Thread(target=self._on_change).start()

    def status(self, level: str, text: str) -> None:
        assert self._on_status is not None
        threading.Thread(target=self._on_status, args=(level, text)).start()


class DialogWindow(FakeWindow):
    def __init__(self) -> None:
        super().__init__()
        self.files: list[str] = []
        self.folder: str | None = None
        self.dialogs: list[tuple[str, str | None, Any]] = []

    def pick_audio_files(self, directory: str | None, extensions: Sequence[str]) -> list[str]:
        self.dialogs.append(("files", directory, tuple(extensions)))
        return self.files

    def pick_folder(self, directory: str | None = None) -> str | None:
        self.dialogs.append(("folder", directory, None))
        return self.folder


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> list[FakeTransportSource]:
    from tidalviz import host as host_module

    made: list[FakeTransportSource] = []

    def make(root: Path) -> FakeTransportSource:
        made.append(FakeTransportSource())
        return made[-1]

    monkeypatch.setattr(host_module, "player_source", make)
    monkeypatch.setattr(host_module, "player_available", lambda: True)
    return made


@pytest_asyncio.fixture
async def http() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest_asyncio.fixture
async def started(tmp_path: Path, fake: list[FakeTransportSource]):
    """A host whose active source is the fake player."""
    hosts: list[Host] = []
    window = DialogWindow()

    async def start(source_id: str = "player") -> tuple[Host, DialogWindow]:
        h = Host(root=tmp_path / "home", builtin_dirs=BUILTINS, source_id=source_id, window=window)
        await h.start()
        hosts.append(h)
        return h, window

    yield start
    for h in hosts:
        await h.stop()


async def drain(shell: Shell, type_: str, seconds: float) -> list[dict[str, Any]]:
    """Every text message of ``type_`` that arrives within ``seconds``."""
    out: list[dict[str, Any]] = []
    end = asyncio.get_running_loop().time() + seconds
    while (left := end - asyncio.get_running_loop().time()) > 0:
        try:
            m = await shell.ws.receive(timeout=left)
        except TimeoutError:
            break
        if m.type == aiohttp.WSMsgType.TEXT:
            data = json.loads(m.data)
            if data["type"] == type_:
                out.append(data)
    return out


# --- hello and the sources list ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_hello_carries_transport_support_the_now_playing_state_and_the_queue(started, http):
    host, _ = await started()
    shell = await connect(host, http)
    hello = await shell.next_json("hello")
    assert hello["transport"] is True and hello["activeSource"] == "player"
    assert hello["nowPlaying"] == {
        "artist": "Band",
        "album": "Album",
        "title": "Song",
        "duration": 200.0,
        "position": 12.5,
        "playing": True,
        "index": 0,
        "count": 2,
        "serial": 0,
        "hostTime": pytest.approx(time.monotonic(), abs=5),
    }
    assert hello["queue"] == {
        "entries": [
            {
                "title": "Song",
                "artist": "Band",
                "album": "Album",
                "duration": 200.0,
                "playable": True,
            },
            {"title": "Other", "artist": "", "album": "", "duration": None, "playable": False},
        ]
    }
    assert {"id": "player", "name": "Files"} in hello["sources"]


@pytest.mark.asyncio
async def test_a_source_without_transport_says_so_in_hello(started, http):
    host, _ = await started("synthetic:demo")
    hello = await (await connect(host, http)).next_json("hello")
    assert hello["transport"] is False and hello["nowPlaying"] is None and hello["queue"] is None


@pytest.mark.asyncio
async def test_a_reloaded_shell_gets_the_current_state_again(started, fake, http):
    host, _ = await started()
    first = await connect(host, http)
    await first.next_json("hello")
    fake[0].change(np=NowPlaying("B2", "A2", "S2", 10.0, 3.0, False, 1, 2))
    await first.next_json("nowPlaying")
    second = await connect(host, http)  # the web view was reloaded
    hello = await second.next_json("hello")
    assert (hello["nowPlaying"]["title"], hello["nowPlaying"]["playing"]) == ("S2", False)


@pytest.mark.asyncio
async def test_the_player_is_not_offered_without_its_engine(
    started, monkeypatch: pytest.MonkeyPatch, http
):
    from tidalviz import host as host_module

    monkeypatch.setattr(host_module, "player_available", lambda: False)
    host, _ = await started("synthetic:demo")
    hello = await (await connect(host, http)).next_json("hello")
    assert all(s["id"] != "player" for s in hello["sources"])


@pytest.mark.asyncio
async def test_a_saved_player_source_without_its_engine_falls_back_to_system_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http
):
    from tidalviz import host as host_module
    from tidalviz.capture import SyntheticSource
    from tidalviz.settings import Settings

    root = tmp_path / "home"
    Settings(root / "settings.json").update({"source": "player"})
    monkeypatch.setattr(host_module, "player_available", lambda: False)
    monkeypatch.setattr(host_module, "system_source", lambda: SyntheticSource("silence"))
    h = Host(root=root, builtin_dirs=BUILTINS, window=FakeWindow())
    await h.start()
    try:
        hello = await (await connect(h, http)).next_json("hello")
        assert hello["activeSource"] == "system" and hello["transport"] is False
    finally:
        await h.stop()


# --- commands ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_actions_drive_the_capability(started, fake, http):
    host, _ = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    for action in ("play", "pause", "toggle", "next", "previous"):
        await shell.send({"type": "transport", "action": action})
    await shell.send({"type": "transport", "action": "seek", "value": 42.5})
    await shell.send({"type": "playerSelect", "index": 1})
    await shell.send({"type": "playerClear"})
    for _ in range(100):
        if len(fake[0].calls) == 8:
            break
        await asyncio.sleep(0.01)
    assert fake[0].calls == [
        ("play",),
        ("pause",),
        ("toggle",),
        ("next",),
        ("previous",),
        ("seek", 42.5, None),
        ("select", 1),
        ("clear",),
    ]


@pytest.mark.asyncio
async def test_a_stale_select_index_is_ignored_and_the_host_carries_on(started, fake, http):
    host, _ = await started()
    fake[0].select_error = True
    shell = await connect(host, http)
    await shell.next_json("hello")
    await shell.send({"type": "playerSelect", "index": 99})
    await shell.send({"type": "transport", "action": "next"})
    for _ in range(100):
        if fake[0].calls:
            break
        await asyncio.sleep(0.01)
    assert fake[0].calls == [("next",)]


@pytest.mark.asyncio
async def test_transport_messages_are_ignored_when_the_source_has_none(started, http):
    host, _ = await started("synthetic:demo")
    shell = await connect(host, http)
    await shell.next_json("hello")
    await shell.send({"type": "transport", "action": "play"})
    await shell.send({"type": "playerClear"})
    await shell.send({"type": "playerOpen", "kind": "files", "replace": True})
    await shell.send({"type": "heartbeat", "t": 1})
    assert (await shell.next_json("windowVisibility"))["visible"] in (True, False)


@pytest.mark.asyncio
async def test_open_files_asks_the_window_starting_in_the_last_folder_then_opens_off_the_loop(
    started, fake, http
):
    host, window = await started()
    window.files = ["/music/a.mp3", "/music/b.flac"]
    shell = await connect(host, http)
    await shell.next_json("hello")
    await shell.send({"type": "playerOpen", "kind": "files", "replace": True})
    for _ in range(100):
        if any(c[0] == "open" for c in fake[0].calls):
            break
        await asyncio.sleep(0.01)
    assert window.dialogs == [("files", "/music/last", (".mp3", ".flac"))]
    (call,) = [c for c in fake[0].calls if c[0] == "open"]
    assert call[1:3] == ([Path("/music/a.mp3"), Path("/music/b.flac")], True)
    assert call[3] != threading.current_thread().name  # a worker thread: a folder walk can be slow


@pytest.mark.asyncio
async def test_open_folder_appends_and_a_cancelled_dialog_does_nothing(started, fake, http):
    host, window = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    window.folder = None
    await shell.send({"type": "playerOpen", "kind": "folder", "replace": False})
    await asyncio.sleep(0.2)
    assert fake[0].calls == []
    window.folder = "/music/album"
    await shell.send({"type": "playerOpen", "kind": "folder", "replace": False})
    for _ in range(100):
        if fake[0].calls:
            break
        await asyncio.sleep(0.01)
    assert window.dialogs[-1] == ("folder", "/music/last", None)
    assert fake[0].calls[0][1:3] == ([Path("/music/album")], False)


@pytest.mark.asyncio
async def test_a_dialog_that_blows_up_is_reported_and_the_host_carries_on(started, fake, http):
    host, window = await started()

    def boom(directory: str | None, extensions: Sequence[str]) -> list[str]:
        raise RuntimeError("no display: cannot open /home/me/secret/x.flac")

    window.pick_audio_files = boom  # type: ignore[method-assign]
    shell = await connect(host, http)
    await shell.next_json("hello")
    await shell.send({"type": "playerOpen", "kind": "files", "replace": False})
    status = await shell.next_json("status")
    assert status["level"] == "error" and status["text"] == "Could not open the chosen files"
    assert "no display" not in json.dumps(status)  # exception text can carry a path: log only


@pytest.mark.asyncio
async def test_a_transport_without_a_queue_ignores_the_queue_messages(
    started, fake, http, monkeypatch: pytest.MonkeyPatch
):
    from tidalviz import host as host_module

    class OnlyTransport:
        """Has play/pause/... but no queue to fill: a Transport that is not a QueueControl."""

        def __init__(self) -> None:
            self.inner = FakeTransportSource()
            self.failed = self.inner.failed
            self.format = self.inner.format
            for name in ("start", "stop", "bind", "now_playing", "play", "pause", "toggle", "next",
                         "previous", "seek", "select"):  # fmt: skip
                setattr(self, name, getattr(self.inner, name))

    only = OnlyTransport()
    monkeypatch.setattr(host_module, "player_source", lambda root: only)
    host, window = await started()
    shell = await connect(host, http)
    hello = await shell.next_json("hello")
    assert hello["transport"] is True and hello["queue"] is None
    for msg in ({"type": "playerClear"}, {"type": "playerOpen", "kind": "files", "replace": True}):
        await shell.send(msg)
    await shell.send({"type": "transport", "action": "play"})
    for _ in range(100):
        if only.inner.calls:
            break
        await asyncio.sleep(0.01)
    assert only.inner.calls == [("play",)] and window.dialogs == []


# --- state going out -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_change_in_the_source_pushes_now_playing_once(started, fake, http):
    host, _ = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    fake[0].change(np=NowPlaying("X", "Y", "Z", 5.0, 0.0, True, 1, 2))
    msg = await shell.next_json("nowPlaying")
    assert (msg["title"], msg["index"], msg["playing"]) == ("Z", 1, True)
    assert "queue" not in {m["type"] for m in await drain(shell, "queue", 0.3)}  # unchanged queue


@pytest.mark.asyncio
async def test_a_burst_of_tag_updates_is_batched_into_a_few_queue_messages(started, fake, http):
    host, _ = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    src = fake[0]
    for i in range(100):
        src.entries = (TrackInfo(f"t{i}", "", "", 1.0, True),) * 2
        src.change(revision=src.revision + 1)
        await asyncio.sleep(0.002)
    queues = await drain(shell, "queue", 0.8)
    assert 1 <= len(queues) <= 5
    assert queues[-1]["entries"][0]["title"] == "t99"  # the last state always arrives


@pytest.mark.asyncio
async def test_position_is_refreshed_about_once_a_second_while_playing_only(
    started, fake, http, monkeypatch: pytest.MonkeyPatch
):
    from tidalviz import host as host_module

    monkeypatch.setattr(host_module, "STATS_INTERVAL_S", 0.1)
    host, _ = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    assert len(await drain(shell, "nowPlaying", 0.55)) >= 3  # playing: a steady trickle
    fake[0].change(np=NowPlaying("X", "Y", "Z", 5.0, 0.0, False, 0, 2))
    await drain(shell, "nowPlaying", 0.3)  # the change itself
    assert await drain(shell, "nowPlaying", 0.5) == []  # paused: nothing


@pytest.mark.asyncio
async def test_source_statuses_reach_the_shell_capped(started, fake, http):
    host, _ = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    fake[0].status("warn", "Skipped Song: not playable audio")
    msg = await shell.next_json("status")
    assert (msg["level"], msg["text"]) == ("warn", "Skipped Song: not playable audio")
    fake[0].status("warn", "x" * 5000)
    assert len((await shell.next_json("status"))["text"]) <= 300


# --- switching sources -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switching_to_the_player_announces_transport_and_its_state(started, fake, http):
    host, _ = await started("synthetic:demo")
    shell = await connect(host, http)
    await shell.next_json("hello")
    await shell.send({"type": "setSource", "id": "player"})
    msg = await shell.next_json("sources")
    assert msg["active"] == "player" and msg["transport"] is True
    assert (await shell.next_json("nowPlaying"))["title"] == "Song"
    assert len((await shell.next_json("queue"))["entries"]) == 2
    assert host.settings.data["source"] == "player"


@pytest.mark.asyncio
async def test_switching_away_stops_the_player_and_withdraws_transport(started, fake, http):
    host, _ = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    await shell.send({"type": "setSource", "id": "synthetic:demo"})
    assert (await shell.next_json("sources"))["transport"] is False
    assert await asyncio.to_thread(fake[0].stopped.wait, 3.0)  # the pipeline stopped it
    fake[0].change(np=NowPlaying("L", "L", "Late", None, 0.0, True, 0, 1))  # a late report
    assert await drain(shell, "nowPlaying", 0.4) == []
    await shell.send({"type": "transport", "action": "play"})
    await asyncio.sleep(0.1)
    assert ("play",) not in fake[0].calls


@pytest.mark.asyncio
async def test_switching_back_builds_a_fresh_source_which_restores_the_queue(started, fake, http):
    host, _ = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    await shell.send({"type": "setSource", "id": "synthetic:demo"})
    await shell.next_json("sources")
    await shell.send({"type": "setSource", "id": "player"})
    assert (await shell.next_json("sources"))["transport"] is True
    assert len(fake) == 2  # the old instance was stopped; a new one reads the saved queue


# --- the real player behind the host -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_real_player_plays_a_file_chosen_in_the_dialog_without_leaking_paths(
    tmp_path: Path, gst, media, http
):
    window = DialogWindow()
    window.files = [str(media.tone_a), str(media.tone_b)]
    h = Host(root=tmp_path / "home", builtin_dirs=BUILTINS, source_id="player", window=window)
    await h.start()
    try:
        shell = await connect(h, http)
        hello = await shell.next_json("hello")
        assert hello["transport"] is True and hello["queue"]["entries"] == []
        await shell.send({"type": "playerOpen", "kind": "files", "replace": True})
        seen: list[dict[str, Any]] = []
        async with asyncio.timeout(10):
            while True:
                m = await shell.ws.receive()
                if m.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(m.data)
                seen.append(data)
                if data["type"] == "nowPlaying" and data["playing"] and data["title"] == "Track A":
                    break
        # Tags arrive as the scan fills in; wait until both entries have theirs.
        async with asyncio.timeout(10):
            while True:
                queue = await shell.next_json("queue")
                if [e["title"] for e in queue["entries"]] == ["Track A", "Track B"]:
                    break
        assert str(tmp_path) not in json.dumps(seen) and str(media.root) not in json.dumps(seen)
        assert (tmp_path / "home" / "player.json").exists()
    finally:
        await h.stop()


@pytest.mark.asyncio
async def test_stopping_the_host_stops_the_engine_within_the_shutdown_timeout(
    tmp_path: Path, gst, media, http
):
    from tidalviz.player.store import QueueStore, StoredQueue

    root = tmp_path / "home"
    QueueStore(root / "player.json").save(StoredQueue([media.ramp], 0, None))
    h = Host(root=root, builtin_dirs=BUILTINS, source_id="player", window=FakeWindow())
    await h.start()
    shell = await connect(h, http)
    await shell.next_json("hello")
    await shell.send({"type": "transport", "action": "play"})
    async with asyncio.timeout(10):
        while not (await shell.next_json("nowPlaying"))["playing"]:
            pass
    await h.stop()  # returns: pipeline.stop() joined the worker within its timeouts
    assert not any(t.name == "tidalviz-player" and t.is_alive() for t in threading.enumerate())


@pytest.mark.asyncio
async def test_a_seek_always_gets_a_now_playing_even_when_nothing_else_changed(started, fake, http):
    host, _ = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    base = fake[0].np
    import dataclasses

    fake[0].change(
        np=dataclasses.replace(base, seeks=base.seeks + 1)
    )  # a rejected seek: same state
    msg = await shell.next_json("nowPlaying")
    assert msg["position"] == base.position and msg["title"] == "Song"


@pytest.mark.asyncio
async def test_a_second_open_while_a_dialog_is_up_is_ignored(started, fake, http):
    host, window = await started()
    release = threading.Event()
    window.files = ["/music/a.mp3"]
    original = window.pick_audio_files

    def blocking(directory: str | None, extensions: Sequence[str]) -> list[str]:
        release.wait(5.0)
        return original(directory, extensions)

    window.pick_audio_files = blocking  # type: ignore[method-assign]
    shell = await connect(host, http)
    await shell.next_json("hello")
    for _ in range(3):
        await shell.send({"type": "playerOpen", "kind": "files", "replace": False})
    await asyncio.sleep(0.3)
    release.set()
    for _ in range(100):
        if any(c[0] == "open" for c in fake[0].calls):
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.3)
    assert len(window.dialogs) == 1 and [c[0] for c in fake[0].calls] == ["open"]
    await shell.send(
        {"type": "playerOpen", "kind": "files", "replace": False}
    )  # the next one works
    for _ in range(100):
        if len(window.dialogs) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(window.dialogs) == 2


@pytest.mark.asyncio
async def test_a_seek_carries_the_serial_it_was_meant_for_to_the_source(started, fake, http):
    host, _ = await started()
    shell = await connect(host, http)
    await shell.next_json("hello")
    await shell.send({"type": "transport", "action": "seek", "value": 40.0, "serial": 7})
    for _ in range(100):
        if fake[0].calls:
            break
        await asyncio.sleep(0.01)
    assert fake[0].calls == [("seek", 40.0, 7)]


@pytest.mark.asyncio
async def test_a_new_serial_alone_is_a_change_worth_a_now_playing(started, fake, http):
    import dataclasses

    host, _ = await started()
    shell = await connect(host, http)
    hello = await shell.next_json("hello")
    assert hello["nowPlaying"]["serial"] == 0
    # The same track selected again restarts it: nothing but the serial differs.
    fake[0].change(np=dataclasses.replace(fake[0].np, serial=1))
    assert (await shell.next_json("nowPlaying"))["serial"] == 1
