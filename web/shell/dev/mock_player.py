"""A pretend file player for the mock host (dev-only, not shipped, plays no audio).

It keeps a queue and a clock and follows the transport semantics of docs/protocols.md §4, so the
shell's player UI can be developed and checked without GStreamer or the real host. It is a mock:
the files are made up, the "dialog" instantly picks a few, and nothing here is the real
implementation (src/tidalviz/player).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

MAX_QUEUE = 2000
# The host's reasons for an unplayable file (src/tidalviz/player), as the shell shows them.
REASONS = (
    "file not found",
    "cannot be read",
    "not an audio file",
    "could not be decoded",
    "timed out",
)
# The host's status for a run of two or more consecutive skipped tracks (warn level); one track
# gives `Skipped <title>: <reason>`. `{count}` is the number of tracks.
SKIPPED_RUN = "Skipped {count} tracks that could not be played"
UNKNOWN_LENGTH_S = 30.0  # how long a track without a known duration "plays" before the next


@dataclass
class Track:
    title: str
    artist: str = ""
    album: str = ""
    duration: float | None = 180.0
    playable: bool = True
    reason: str = "could not be decoded"  # why it is unplayable
    # What the background scan has not found yet (shown after `reveal()`).
    hidden_tags: tuple[str, str] = field(default=("", ""))


def sample_tracks(n: int = 12) -> list[Track]:
    """A varied queue: tagged, untagged, no duration, unplayable, markup, very long text."""
    special = [
        Track("Music Is Math", "Boards of Canada", "Geogaddi", 321.5),
        Track("07 - field recording"),  # untagged: the host falls back to the file name
        Track("Live set, no duration", "Some Band", "", None),
        Track("Broken file", "Nobody", "Corrupt", 100.0, playable=False, reason=REASONS[3]),
        Track("<img src=x onerror=alert(1)>", "<b>bold</b>", "&lt;i&gt;", 90.0),
        Track(
            "A title so long that it has to be cut with an ellipsis " * 3,
            "An equally long band name " * 3,
            "Album " * 20,
            245.0,
        ),
        Track("Short one", "Band", "Album", 12.0),
    ]
    more = [Track(f"Track {i:02d}", f"Band {i % 4}", f"Album {i % 3}", 150.0 + i) for i in range(n)]
    return (special + more)[: max(n, 0)]


class MockPlayer:
    def __init__(self, tracks: list[Track]) -> None:
        self.tracks = tracks
        self.index: int | None = 0 if tracks else None
        self.playing = False
        self._pos = 0.0
        self._at = time.monotonic()  # when _pos was valid
        self.revision = 0  # bumps when the queue changes
        self._skipped: list[
            Track
        ] = []  # unplayable tracks passed over since the last take_status()
        self.stopped = False  # the end-of-queue rewind leaves it stopped, not just paused
        self.serial = 0  # bumps on every track load, so a seek can say which load it was meant for

    # --- clock -------------------------------------------------------------------------------
    def position(self) -> float:
        if not self.playing:
            return self._pos
        p = self._pos + (time.monotonic() - self._at)
        d = self.length()
        return min(p, d) if d is not None else p

    def length(self) -> float | None:
        if self.index is None:
            return None
        return self.tracks[self.index].duration

    def _set(self, pos: float, playing: bool | None = None) -> None:
        self._pos, self._at = pos, time.monotonic()
        if playing is not None:
            self.playing = playing

    # --- state out ---------------------------------------------------------------------------
    def now_playing(self) -> dict[str, Any]:
        t = self.tracks[self.index] if self.index is not None else None
        return {
            "artist": t.artist if t else "",
            "album": t.album if t else "",
            "title": t.title if t else "",
            "duration": t.duration if t else None,
            "position": self.position(),
            "playing": self.playing,
            "index": self.index,
            "count": len(self.tracks),
            "hostTime": time.monotonic(),
            "serial": self.serial,
        }

    def signature(self) -> tuple[Any, ...]:
        """Everything but the position, which moves by itself: a change here is worth a message of its own."""
        n = self.now_playing()
        return tuple(
            n[k]
            for k in ("artist", "album", "title", "duration", "playing", "index", "count", "serial")
        )

    def queue_fields(self) -> dict[str, Any]:
        return {
            "entries": [
                {
                    "title": t.title,
                    "artist": t.artist,
                    "album": t.album,
                    "duration": t.duration,
                    "playable": t.playable,
                }
                for t in self.tracks
            ]
        }

    # --- commands ----------------------------------------------------------------------------
    def _playable_from(self, start: int, step: int) -> int | None:
        i = start
        while 0 <= i < len(self.tracks):
            if self.tracks[i].playable:
                return i
            self._skipped.append(self.tracks[i])
            i += step
        return None

    def take_status(self) -> str | None:
        """One status for the run of tracks just skipped (like the host), or None."""
        skipped, self._skipped = self._skipped, []
        if not skipped:
            return None
        if len(skipped) == 1:
            return f"Skipped {skipped[0].title}: {skipped[0].reason}"
        return SKIPPED_RUN.format(count=len(skipped))

    def _start(self, index: int) -> None:
        self.serial += 1
        self.stopped = False
        self.index = index
        self._set(0.0, True)

    def transport(self, action: str, value: float | None, serial: int | None = None) -> None:
        if self.index is None:
            return
        match action:
            case "play":
                if self.stopped:  # a play from stopped loads the track again
                    self.serial += 1
                    self.stopped = False
                self._set(self.position(), True)
            case "pause":
                self._set(self.position(), False)
            case "toggle":
                self._set(self.position(), not self.playing)
            case "next":
                nxt = self._playable_from(self.index + 1, 1)
                if nxt is not None:
                    self._start(nxt)
            case "previous":
                prev = self._playable_from(self.index - 1, -1)
                self._start(prev if prev is not None else self.index)
            case "seek" if value is not None:
                if serial is not None and serial != self.serial:
                    return  # meant for a track that is no longer loaded
                d = self.length()
                self._set(min(value, d) if d is not None else value)

    def select(self, index: int) -> str | None:
        """A status text when the entry can't be played; a stale index is ignored."""
        if not 0 <= index < len(self.tracks):
            return None
        if not self.tracks[index].playable:
            return f"Skipped {self.tracks[index].title}: {self.tracks[index].reason}"
        self._start(index)
        return None

    def clear(self) -> None:
        self.tracks, self.index = [], None
        self.serial += 1
        self._set(0.0, False)
        self.revision += 1

    def open(self, kind: str, replace: bool) -> None:
        """The "native dialog": picks a few made-up files, or a made-up folder of them."""
        fresh = [Track(f"{'Folder' if kind == 'folder' else 'File'} pick {len(self.tracks) + i + 1}",
                       hidden_tags=("Scanned band", "Scanned album")) for i in range(5 if kind == "folder" else 3)]  # fmt: skip
        was_empty = not self.tracks
        self.tracks = (fresh if replace else self.tracks + fresh)[:MAX_QUEUE]
        self.revision += 1
        if replace or was_empty:
            self._start(0)

    def reveal(self, budget: int = 2) -> bool:
        """The background scan finding tags for a couple of files. True if anything changed."""
        done = 0
        for t in self.tracks:
            if t.hidden_tags != ("", ""):
                t.artist, t.album = t.hidden_tags
                t.hidden_tags = ("", "")
                done += 1
                if done == budget:
                    break
        if done:
            self.revision += 1
        return done > 0

    def end_of_track(self) -> bool:
        """The track ran out: the next one, or stop and rewind at the end. True if it moved on."""
        if self.index is None or not self.playing:
            return False
        length = self.length()
        if self.position() < (UNKNOWN_LENGTH_S if length is None else length):
            return False
        nxt = self._playable_from(self.index + 1, 1)
        if nxt is not None:
            self._start(nxt)
        else:
            self.serial += 1
            self.stopped = True
            self.index = 0 if self.tracks else None
            self._set(0.0, False)
        return True
