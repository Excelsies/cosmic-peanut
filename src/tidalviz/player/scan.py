"""Probing queue entries (tags, duration, can-it-play) on a few background threads.

The probe itself is injected: the GStreamer discoverer lives in `engine.py`, so everything here
runs and is tested without GStreamer. Nothing else in the player ever runs a probe.
"""

import itertools
import logging
import queue
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from tidalviz.player.queue import Track
from tidalviz.player.tags import TrackTags

log = logging.getLogger(__name__)

WORKERS = 4  # 1,106 real files: 30 s on one thread, 8 s on four (NOTES.md → Local-file player)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    playable: bool
    reason: str = ""  # why not: a fixed phrase, never a path or library text
    tags: TrackTags | None = None
    duration: float | None = None


Probe = Callable[[Path], ProbeResult]
OnResult = Callable[[Track, ProbeResult], None]  # called on a pool thread


class ScanPool:
    """Daemon threads started on first use. `cancel()` drops work not yet reported; `close()`
    abandons everything."""

    def __init__(self, probe: Probe, on_result: OnResult, *, workers: int = WORKERS) -> None:
        self._probe = probe
        self._on_result = on_result
        self._workers = workers
        # (priority, order, generation, track); priority 0 = urgent, 1 = background
        self._jobs: queue.PriorityQueue[tuple[int, int, int, Track | None]] = queue.PriorityQueue()
        self._order = itertools.count()
        self._generation = 0
        self._threads: list[threading.Thread] = []
        self._closed = threading.Event()
        self._busy: set[int] = set()  # tracks being probed right now
        self._lock = threading.Lock()

    def submit(self, tracks: Iterable[Track], *, urgent: bool = False) -> None:
        if self._closed.is_set():
            raise RuntimeError("scan pool is closed")
        for t in tracks:
            self._jobs.put((0 if urgent else 1, next(self._order), self._generation, t))
        while len(self._threads) < self._workers:
            thread = threading.Thread(target=self._run, name="tidalviz-scan", daemon=True)
            self._threads.append(thread)
            thread.start()

    def cancel(self) -> None:
        """Forget everything submitted so far (queued, and the result of what is running)."""
        self._generation += 1

    def close(self) -> None:
        self._closed.set()
        for _ in self._threads:
            self._jobs.put((2, next(self._order), self._generation, None))  # wakes idle workers

    def _run(self) -> None:
        while not self._closed.is_set():
            _, _, generation, track = self._jobs.get()
            if track is None or self._closed.is_set():
                return
            if generation != self._generation or track.playable is not None:
                continue  # cancelled, or already answered (by an urgent probe)
            with self._lock:
                if id(track) in self._busy:
                    continue  # another thread is on it (an urgent job and a background one)
                self._busy.add(id(track))
            try:
                result = self._probe(track.path)
            except Exception:
                log.exception("probing %s failed", track.path.name)
                result = ProbeResult(False, "cannot be read")
            finally:
                with self._lock:
                    self._busy.discard(id(track))
            if generation == self._generation and not self._closed.is_set():
                self._on_result(track, result)
