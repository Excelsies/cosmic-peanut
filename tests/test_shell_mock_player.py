"""The shell's dev mock host and its pretend file player (web/shell/dev, not shipped).

The shell's tooling can't be trusted if the mock disagrees with docs/protocols.md §4, so the mock
is held to it here, where the default suite and CI run it.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web" / "shell" / "dev"))
from mock_host import MockHost
from mock_player import REASONS, SKIPPED_RUN, MockPlayer, Track, sample_tracks


def player(n: int = 3, **over: Any) -> MockPlayer:
    return MockPlayer([Track(f"t{i}", duration=100.0, **over) for i in range(n)])


def test_next_on_the_last_track_does_nothing() -> None:
    p = player(2)
    p.transport("next", None)
    assert p.index == 1 and p.playing
    p.transport("next", None)
    assert p.index == 1


def test_previous_on_the_first_track_restarts_it_and_plays_even_when_paused() -> None:
    p = player(2)
    p.transport("seek", 40.0)
    p.transport("pause", None)
    p.transport("previous", None)
    assert p.index == 0 and p.position() < 1 and p.playing


def test_seek_while_paused_moves_the_position_not_the_signature() -> None:
    p = player()
    p.transport("pause", None)
    before = p.signature()
    p.transport("seek", 50.0)
    assert p.position() == 50.0 and p.signature() == before


def test_select_skips_unplayable_with_a_pathless_status_and_ignores_a_stale_index() -> None:
    p = MockPlayer([Track("ok"), Track("bad", playable=False)])
    text = p.select(1)
    assert text is not None and p.index == 0
    assert "/" not in text and "\\" not in text
    assert p.select(9) is None and p.index == 0
    assert p.select(0) is None and p.playing


def test_open_replace_starts_playing_and_append_to_a_non_empty_queue_leaves_playback() -> None:
    p = player(2)
    p.transport("pause", None)
    p.open("files", replace=False)
    assert len(p.tracks) == 5 and not p.playing
    p.open("folder", replace=True)
    assert len(p.tracks) == 5 and p.playing and p.index == 0


def test_end_of_the_queue_stops_and_rewinds_to_the_first_entry() -> None:
    p = MockPlayer([Track("only", duration=0.0)])
    p.transport("play", None)
    assert p.end_of_track()
    assert p.index == 0 and not p.playing and p.position() == 0.0


def test_selecting_an_unplayable_track_gives_the_hosts_reason() -> None:
    p = MockPlayer([Track("ok"), Track("bad", playable=False, reason="not an audio file")])
    assert p.select(1) == "Skipped bad: not an audio file"
    assert {t.reason for t in sample_unplayable()} <= set(REASONS)


def sample_unplayable() -> list[Track]:
    return [t for t in sample_tracks(12) if not t.playable]


def test_a_run_of_skipped_tracks_is_one_status() -> None:
    one = MockPlayer(
        [Track("a"), Track("bad", playable=False, reason="file not found"), Track("c")]
    )
    one.transport("next", None)
    assert one.index == 2 and one.take_status() == "Skipped bad: file not found"
    assert one.take_status() is None
    many = MockPlayer([Track("a"), *(Track(f"b{i}", playable=False) for i in range(3)), Track("e")])
    many.transport("next", None)
    assert many.index == 4 and many.take_status() == SKIPPED_RUN.format(count=3)


def test_clear_empties_everything() -> None:
    p = player()
    p.clear()
    assert p.index is None and not p.tracks and not p.playing
    assert p.now_playing()["count"] == 0


def test_every_load_bumps_the_serial() -> None:
    p = player(3)
    first = p.serial
    p.transport("next", None)
    assert p.serial > first
    last = p.serial
    p.transport("previous", None)
    assert p.serial > last
    last = p.serial
    p.transport("previous", None)  # on the first track: a restart is a load too
    assert p.serial > last
    last = p.serial
    assert p.select(0) is None  # the current track again
    assert p.serial > last
    last = p.serial
    p.open("files", replace=True)
    assert p.serial > last
    assert p.now_playing()["serial"] == p.serial


def test_seeks_and_pauses_are_not_loads() -> None:
    p = player()
    s = p.serial
    p.transport("seek", 10.0)
    p.transport("toggle", None)
    assert p.serial == s


def test_the_end_of_the_queue_rewind_is_a_load() -> None:
    p = MockPlayer([Track("only", duration=0.0)])
    p.transport("play", None)
    s = p.serial
    assert p.end_of_track()
    assert p.serial > s


def test_a_play_from_stopped_and_a_clear_are_loads_too() -> None:
    p = MockPlayer([Track("only", duration=0.0)])
    p.transport("play", None)
    assert p.end_of_track()  # stopped at the end of the queue
    s = p.serial
    p.transport("play", None)
    assert p.serial > s
    s = p.serial
    p.transport("pause", None)
    p.transport("play", None)  # paused, not stopped: no new load
    assert p.serial == s
    p.clear()
    assert p.serial > s


def test_a_seek_for_an_older_load_is_dropped() -> None:
    p = player()
    old = p.serial
    p.transport("next", None)
    p.transport("seek", 40.0, old)
    assert p.position() < 1
    p.transport("seek", 40.0, p.serial)
    assert 40.0 <= p.position() < 41.0
    p.transport("seek", 12.0, None)  # no serial: always applied
    assert 12.0 <= p.position() < 13.0


class Shell:
    """A socket to the mock, the way the shell uses it."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws

    async def send(self, **msg: Any) -> None:
        await self.ws.send_json(msg)

    async def text(self, timeout: float) -> dict[str, Any]:
        """The next JSON message; the mock's binary frames (94 Hz) are skipped."""
        end = asyncio.get_running_loop().time() + timeout
        while True:
            msg = await self.ws.receive(timeout=max(0.001, end - asyncio.get_running_loop().time()))
            if msg.type == aiohttp.WSMsgType.TEXT:
                return json.loads(msg.data)

    async def next(self, timeout: float = 3) -> dict[str, Any]:
        return await self.text(timeout)

    async def quiet(self, seconds: float = 0.4) -> list[dict[str, Any]]:
        """Every JSON message that arrives in the next `seconds`."""
        got: list[dict[str, Any]] = []
        end = asyncio.get_running_loop().time() + seconds
        while end - asyncio.get_running_loop().time() > 0:
            try:
                got.append(await self.text(end - asyncio.get_running_loop().time()))
            except TimeoutError:
                break
        return got


async def connect(host: MockHost, http: aiohttp.ClientSession) -> tuple[Shell, dict[str, Any]]:
    ws = await http.ws_connect(f"ws://127.0.0.1:{host.shell_port}/ws?token={host.token}")
    shell = Shell(ws)
    return shell, await shell.next()


@pytest.mark.asyncio
async def test_the_shell_protocol_over_a_real_socket() -> None:
    host = MockHost(0, 0, tracks=4, source="system")
    await host.start()
    try:
        async with aiohttp.ClientSession() as http:
            shell, hello = await connect(host, http)
            assert hello["transport"] is False
            assert hello["nowPlaying"] is None and hello["queue"] is None

            await shell.send(type="setSource", id="player")
            seen: dict[str, Any] = {}
            while {"sources", "nowPlaying", "queue"} - seen.keys():
                m = await shell.next()
                seen.setdefault(m["type"], m)
            assert seen["sources"]["transport"] is True
            assert seen["queue"]["entries"][0]["title"] == "Music Is Math"
            assert seen["nowPlaying"]["count"] == 4 and seen["nowPlaying"]["playing"] is False

            await shell.send(type="transport", action="toggle")
            np = await shell.next()
            assert np["type"] == "nowPlaying" and np["playing"] is True

            await shell.send(type="playerClear")
            kinds = {(await shell.next())["type"] for _ in range(2)}
            assert kinds == {"nowPlaying", "queue"}
    finally:
        await host.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("playing", [True, False])
async def test_a_nowplaying_follows_every_seek_paused_or_not(playing: bool) -> None:
    host = MockHost(0, 0, tracks=4, source="player")
    await host.start()
    try:
        async with aiohttp.ClientSession() as http:
            shell, _ = await connect(host, http)
            await shell.send(type="transport", action="play" if playing else "pause")
            await shell.quiet(0.1)
            await shell.send(type="transport", action="seek", value=42)
            np = next(m for m in await shell.quiet(0.3) if m["type"] == "nowPlaying")
            assert 42 <= np["position"] < 44 and np["playing"] is playing
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_a_stale_seek_is_dropped_but_answered() -> None:
    host = MockHost(0, 0, tracks=4, source="player")
    await host.start()
    try:
        async with aiohttp.ClientSession() as http:
            shell, hello = await connect(host, http)
            old = hello["nowPlaying"]["serial"]
            await shell.send(type="transport", action="next")
            await shell.quiet(0.2)
            await shell.send(type="transport", action="seek", value=40, serial=old)
            np = next(m for m in await shell.quiet(0.3) if m["type"] == "nowPlaying")
            assert np["position"] < 5 and np["serial"] > old
    finally:
        await host.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["previous-first", "select-current"])
async def test_restarting_the_playing_track_sends_a_nowplaying(action: str) -> None:
    host = MockHost(0, 0, tracks=4, source="player")
    await host.start()
    try:
        async with aiohttp.ClientSession() as http:
            shell, _ = await connect(host, http)
            await shell.send(type="transport", action="play")
            await shell.quiet(0.2)
            before = host.player.serial
            if action == "previous-first":
                await shell.send(type="transport", action="previous")
            else:
                await shell.send(type="playerSelect", index=0)
            got = [m for m in await shell.quiet(0.3) if m["type"] == "nowPlaying"]
            assert got and got[0]["serial"] > before and got[0]["playing"] is True
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_a_rejected_seek_still_gets_its_nowplaying() -> None:
    host = MockHost(0, 0, tracks=0, source="player")  # nothing to seek in
    await host.start()
    try:
        async with aiohttp.ClientSession() as http:
            shell, _ = await connect(host, http)
            await shell.send(type="transport", action="seek", value=5)
            got = await shell.quiet(0.3)
            assert next(m for m in got if m["type"] == "nowPlaying")["count"] == 0
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_an_open_that_finds_no_audio_is_an_info_status_and_changes_nothing() -> None:
    host = MockHost(0, 0, tracks=3, source="player")
    host.dialog_finds_audio = False
    await host.start()
    try:
        async with aiohttp.ClientSession() as http:
            shell, _ = await connect(host, http)
            await shell.send(type="transport", action="play")
            await shell.quiet(0.1)
            before = [t.title for t in host.player.tracks]
            await shell.send(type="playerOpen", kind="folder", replace=True)
            got = await shell.quiet(0.4)
            assert {"type": "status", "level": "info", "text": "No audio files found"} in got
            assert not [m for m in got if m["type"] == "queue"]
            assert [t.title for t in host.player.tracks] == before and host.player.playing
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_a_second_open_while_the_dialog_is_up_is_ignored() -> None:
    host = MockHost(0, 0, tracks=2, source="player")
    host.dialog_s = 0.3
    await host.start()
    try:
        async with aiohttp.ClientSession() as http:
            shell, _ = await connect(host, http)
            await shell.send(type="playerOpen", kind="files", replace=False)
            await shell.send(type="playerOpen", kind="files", replace=False)
            await shell.quiet(0.8)
            assert len(host.player.tracks) == 2 + 3  # one dialog's worth, not two
            await shell.send(type="playerOpen", kind="files", replace=False)
            await shell.quiet(0.6)
            assert len(host.player.tracks) == 2 + 3 + 3  # and the next one works again
    finally:
        await host.stop()
