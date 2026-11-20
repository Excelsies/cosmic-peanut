"""The play queue: tracks, the current one, and what is known about each. No GStreamer."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tidalviz.capture.transport import TrackInfo
from tidalviz.player.tags import TrackTags, clean_text

MAX_QUEUE = 2000  # also bounds the `queue` control message (docs/protocols.md §4)


@dataclass(slots=True, eq=False)
class Track:
    path: Path
    tags: TrackTags
    duration: float | None = None
    playable: bool | None = None  # None: not probed yet (assumed playable)
    reason: str = ""
    alive: bool = True  # False once it left the queue: late scan results for it are ignored

    def info(self) -> TrackInfo:
        t = self.tags
        return TrackInfo(t.title, t.artist, t.album, self.duration, self.playable is not False)


def _track(path: Path) -> Track:
    return Track(path, TrackTags(title=clean_text(path.stem)))


@dataclass(slots=True)
class PlayQueue:
    tracks: list[Track] = field(default_factory=lambda: [])
    index: int | None = None
    passed: list[Track] = field(default_factory=lambda: [])  # unplayable ones the last move skipped
    revision: int = 0  # bumps when `info()` would change (not on moving the current track)

    def __len__(self) -> int:
        return len(self.tracks)

    @property
    def current(self) -> Track | None:
        return None if self.index is None else self.tracks[self.index]

    def info(self) -> tuple[TrackInfo, ...]:
        return tuple(t.info() for t in self.tracks)

    def add(self, paths: Sequence[Path], *, replace: bool) -> int:
        """Append (or replace the queue with) these files; returns how many did not fit."""
        if replace:
            self._retire()
        room = MAX_QUEUE - len(self.tracks)
        self.tracks.extend(_track(p) for p in paths[:room])
        if self.index is None and self.tracks:
            self.index = 0
        self.revision += 1
        return max(0, len(paths) - room)

    def _retire(self) -> None:
        for t in self.tracks:
            t.alive = False
        self.tracks, self.index = [], None

    def restore(self, paths: Sequence[Path], *, index: int) -> None:
        self._retire()
        self.tracks = [_track(p) for p in paths[:MAX_QUEUE]]
        self.index = min(max(index, 0), len(self.tracks) - 1) if self.tracks else None
        self.revision += 1

    def clear(self) -> None:
        self._retire()
        self.revision += 1

    def select(self, index: int) -> None:
        if not 0 <= index < len(self.tracks):
            raise ValueError(f"no queue entry {index} (queue has {len(self.tracks)})")
        self.index = index

    def next(self) -> bool:
        """Move to the next track that is not known to be unplayable; False when none is left."""
        return self._move(range((self.index or 0) + 1, len(self.tracks)))

    def previous(self) -> bool:
        return self._move(range((self.index or 0) - 1, -1, -1))

    def _move(self, candidates: range) -> bool:
        self.passed = []
        for i in candidates:
            if self.tracks[i].playable is not False:
                self.index = i
                return True
            self.passed.append(self.tracks[i])
        return False

    def apply_probe(self, track: Track, *, tags: TrackTags, duration: float | None) -> None:
        track.tags, track.duration, track.playable, track.reason = tags, duration, True, ""
        self.revision += 1

    def mark_unplayable(self, track: Track, reason: str) -> None:
        track.playable, track.reason = False, reason
        self.revision += 1
