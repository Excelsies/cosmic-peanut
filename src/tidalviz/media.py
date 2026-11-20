"""The host's side of the transport capability (docs/protocols.md §4: `transport`, `player*`,
`nowPlaying`, `queue`).

Lives on the asyncio loop. It binds to whichever active source implements `Transport`
(`capture/transport.py`), pushes that source's state to the shell, and turns the shell's
control messages into calls on it. It never names a concrete source.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from tidalviz.capture.base import AudioSource
from tidalviz.capture.transport import NowPlaying, QueueControl, QueueSnapshot, Transport

log = logging.getLogger(__name__)

FLUSH_S = 0.25  # state changes closer together than this go out as one message
STATUS_MAX = 300  # characters of a source's status text


class Dialogs(Protocol):
    def pick_audio_files(self, directory: str | None, extensions: Sequence[str]) -> list[str]: ...
    def pick_folder(self, directory: str | None = None) -> str | None: ...


def now_playing_fields(np: NowPlaying, host_time: float) -> dict[str, Any]:
    """`position` was read at `hostTime` (time.monotonic(), the clock of frame host times)."""
    return {
        "artist": np.artist,
        "album": np.album,
        "title": np.title,
        "duration": np.duration,
        "position": np.position,
        "playing": np.playing,
        "index": np.index,
        "count": np.count,
        "serial": np.serial,
        "hostTime": host_time,
    }


def queue_fields(snapshot: QueueSnapshot) -> dict[str, Any]:
    return {
        "entries": [
            {
                "title": e.title,
                "artist": e.artist,
                "album": e.album,
                "duration": e.duration,
                "playable": e.playable,
            }
            for e in snapshot.entries
        ]
    }


def _signature(np: NowPlaying) -> tuple[Any, ...]:
    """Everything in `nowPlaying` but the position, which moves by itself; a seek counts."""
    return (
        np.artist,
        np.album,
        np.title,
        np.duration,
        np.playing,
        np.index,
        np.count,
        np.seeks,
        np.serial,
    )


class MediaBridge:
    def __init__(
        self, broadcast: Callable[[dict[str, Any]], Awaitable[None]], dialogs: Dialogs
    ) -> None:
        self._broadcast = broadcast
        self._dialogs = dialogs
        self._source: AudioSource | None = None  # the bound Transport source, if any
        self._loop: asyncio.AbstractEventLoop | None = None
        self._flush: asyncio.TimerHandle | None = None
        self._last_flush = -FLUSH_S
        self._sent: tuple[Any, ...] | None = None  # the last nowPlaying, without its position
        self._sent_revision: int | None = None
        self._tasks: set[asyncio.Future[Any]] = set()
        self._dialog_open = False  # one native dialog at a time

    # --- binding ------------------------------------------------------------------------------

    @property
    def supported(self) -> bool:
        return self._source is not None

    def bind(self, source: AudioSource | None) -> None:
        """Follow ``source`` (None or one without transport: follow nothing). On the loop."""
        self._loop = asyncio.get_running_loop()
        if self._flush is not None:
            self._flush.cancel()
            self._flush = None
        self._sent = self._sent_revision = None
        if not isinstance(source, Transport):
            self._source = None
            return
        self._source = source
        self._sent = _signature(source.now_playing())  # what a connecting shell's hello carries
        self._sent_revision = source.queue().revision if isinstance(source, QueueControl) else None
        source.bind(
            lambda: self._from_thread(self._mark, source),
            lambda level, text: self._from_thread(self._status, source, level, text),
        )

    def _from_thread(self, fn: Callable[..., None], *args: Any) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):  # the loop closed between the check and the call
            loop.call_soon_threadsafe(fn, *args)

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- state out ----------------------------------------------------------------------------

    def hello_fields(self) -> dict[str, Any]:
        src = self._source
        if not isinstance(src, Transport):
            return {"transport": False, "nowPlaying": None, "queue": None}
        return {
            "transport": True,
            "nowPlaying": now_playing_fields(src.now_playing(), time.monotonic()),
            "queue": queue_fields(src.queue()) if isinstance(src, QueueControl) else None,
        }

    async def announce(self) -> None:
        """Send the whole state (after a source switch)."""
        await self._send_changes(force=True)

    def _mark(self, source: AudioSource) -> None:
        if source is not self._source or self._flush is not None:
            return
        loop = asyncio.get_running_loop()
        delay = max(0.0, self._last_flush + FLUSH_S - loop.time())
        self._flush = loop.call_later(delay, self._flush_now)

    def _flush_now(self) -> None:
        self._flush = None
        self._last_flush = asyncio.get_running_loop().time()
        self._spawn(self._send_changes())

    async def _send_changes(self, *, force: bool = False) -> None:
        src = self._source
        if not isinstance(src, Transport):
            return
        np = src.now_playing()
        if force or _signature(np) != self._sent:
            self._sent = _signature(np)
            await self._broadcast(
                {"type": "nowPlaying", **now_playing_fields(np, time.monotonic())}
            )
        if isinstance(src, QueueControl):
            snapshot = src.queue()
            if force or snapshot.revision != self._sent_revision:
                self._sent_revision = snapshot.revision
                await self._broadcast({"type": "queue", **queue_fields(snapshot)})

    async def tick(self) -> None:
        """Once a second: the position moves while playing, so the shell can correct its clock."""
        src = self._source
        if isinstance(src, Transport):
            np = src.now_playing()
            if np.playing:
                await self._broadcast(
                    {"type": "nowPlaying", **now_playing_fields(np, time.monotonic())}
                )

    def _status(self, source: AudioSource, level: str, text: str) -> None:
        if source is self._source:
            self._spawn(
                self._broadcast({"type": "status", "level": level, "text": text[:STATUS_MAX]})
            )

    async def open_files(self, paths: Sequence[Path], *, replace: bool) -> bool:
        """Queue files on the active source (off the loop: a folder walk blocks). False when the
        active source has no queue."""
        src = self._source
        if not isinstance(src, QueueControl):
            return False
        await asyncio.to_thread(src.open, list(paths), replace=replace)
        return True

    # --- commands in --------------------------------------------------------------------------

    def handle(self, msg: dict[str, Any]) -> None:
        """A validated `transport`, `playerOpen`, `playerSelect` or `playerClear` message."""
        src = self._source
        if not isinstance(src, Transport):
            log.info("ignoring %s: the active source has no transport", msg["type"])
            return
        match msg["type"]:
            case "transport":
                self._transport(src, msg["action"], msg.get("value"), msg.get("serial"))
            case "playerSelect":
                try:
                    src.select(msg["index"])
                except ValueError as e:
                    log.info("ignoring playerSelect: %s", e)  # the shell's list was stale
            case "playerClear" if isinstance(src, QueueControl):
                src.clear()
            case "playerOpen" if isinstance(src, QueueControl):
                if self._dialog_open:
                    log.info("ignoring playerOpen: a dialog is already open")
                else:
                    self._dialog_open = True
                    self._spawn(self._open(src, msg["kind"], msg["replace"]))
            case _:
                pass  # `playerClear` / `playerOpen` for a source without a queue

    @staticmethod
    def _transport(src: Transport, action: str, value: float | None, serial: int | None) -> None:
        match action:
            case "play":
                src.play()
            case "pause":
                src.pause()
            case "toggle":
                src.toggle()
            case "next":
                src.next()
            case "previous":
                src.previous()
            case "seek" if value is not None:
                src.seek(float(value), serial)
            case _:
                pass  # the schema admits no other action

    async def _open(self, src: QueueControl, kind: str, replace: bool) -> None:
        """The native dialog and the folder walk both block: off the loop."""
        try:
            start = src.last_folder
            if kind == "files":
                chosen = await asyncio.to_thread(
                    self._dialogs.pick_audio_files, start, src.extensions
                )
            else:
                folder = await asyncio.to_thread(self._dialogs.pick_folder, start)
                chosen = [folder] if folder else []
            if chosen:
                await asyncio.to_thread(
                    lambda: src.open([Path(p) for p in chosen], replace=replace)
                )
        except Exception:
            # The exception text can carry a file name or path: it goes to the log, not the shell.
            log.exception("opening files failed")
            await self._broadcast(
                {"type": "status", "level": "error", "text": "Could not open the chosen files"}
            )
        finally:
            self._dialog_open = False
