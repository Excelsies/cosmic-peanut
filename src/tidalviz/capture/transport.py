"""Optional capabilities of an `AudioSource` that knows what is playing (docs/protocols.md §2).

The host asks a source for these with ``isinstance`` and never names the implementation, so a
source backed by the desktop's media session could provide them later. `Transport` is "what is
playing and how to control it"; `QueueControl` adds a list of tracks the user can fill.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# Called from any thread. `changed` carries no data: pull `now_playing()` / `queue()` afterwards.
Changed = Callable[[], None]
StatusSink = Callable[[str, str], None]  # (level "info" | "warn" | "error", plain text)


@dataclass(frozen=True, slots=True)
class TrackInfo:
    """One queue entry as shown to the user (no paths: the shell never needs one)."""

    title: str
    artist: str
    album: str
    duration: float | None  # seconds; None until known
    playable: bool  # False once the file proved unplayable


@dataclass(frozen=True, slots=True)
class NowPlaying:
    artist: str
    album: str
    title: str
    duration: float | None
    position: float  # seconds into the track (the seek target until the first block after a seek)
    playing: bool  # False: paused or stopped
    index: int | None  # position in the queue; None when there is no current track
    count: int  # queue length
    seeks: int = 0  # counts seeks, so a seek is a change even when nothing else differs
    serial: int = 0  # bumps whenever a track is (re)loaded; a seek names the one it was meant for


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    revision: int  # changes whenever `entries` would differ
    entries: tuple[TrackInfo, ...]


@runtime_checkable
class Transport(Protocol):
    def now_playing(self) -> NowPlaying: ...

    def bind(self, on_change: Changed, on_status: StatusSink) -> None:
        """Where to report changes and per-track problems; replaces any earlier binding."""
        ...

    def play(self) -> None: ...

    def pause(self) -> None: ...

    def toggle(self) -> None: ...

    def next(self) -> None: ...

    def previous(self) -> None: ...

    def seek(self, seconds: float, serial: int | None = None) -> None:
        """Seek the current track. With ``serial``, only if no other track was loaded since."""
        ...

    def select(self, index: int) -> None:
        """Play queue entry ``index``. Raises ValueError when there is no such entry."""
        ...


@runtime_checkable
class QueueControl(Protocol):
    @property
    def extensions(self) -> tuple[str, ...]:
        """File name extensions (lower case, with the dot) the source can open."""
        ...

    @property
    def last_folder(self) -> str | None:
        """Where the open dialog should start."""
        ...

    def queue(self) -> QueueSnapshot: ...

    def open(self, paths: Sequence[Path], *, replace: bool) -> None:
        """Queue these files (folders are searched recursively), replacing the queue or not."""
        ...

    def clear(self) -> None: ...
