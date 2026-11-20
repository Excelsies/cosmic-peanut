"""What the player is doing: the queue, the loaded track, play / pause / stopped, seek holds.

Owned by the source's worker thread: every method here runs there, so nothing is locked. The
source reads the immutable `View` this builds and never touches the queue itself. Nothing here
runs a probe (a Discoverer can take seconds): an unscanned track is moved to the front of the scan
pool and playback of it starts when its answer arrives, with silence in between.
"""

import dataclasses
import enum
import logging
import time
from collections.abc import Callable
from pathlib import Path

from tidalviz.capture.transport import NowPlaying
from tidalviz.player.engine import (
    DISCOVER_TIMEOUT_S,
    OUTPUT_SILENT,
    TIMED_OUT,
    EngineError,
    Event,
    GstBackend,
)
from tidalviz.player.queue import MAX_QUEUE, PlayQueue, Track
from tidalviz.player.scan import ProbeResult, ScanPool
from tidalviz.player.sink import SinkSpec
from tidalviz.player.store import QueueStore, StoredQueue
from tidalviz.player.tags import clean_text

log = logging.getLogger(__name__)

Report = Callable[[str, str], None]  # (level, text) -> a status for the shell


class State(enum.Enum):
    STOPPED = "stopped"  # nothing loaded (a restored queue waits here)
    PAUSED = "paused"
    PLAYING = "playing"


@dataclasses.dataclass(frozen=True, slots=True)
class View:
    """What other threads may read: replaced as a whole, never modified."""

    now: NowPlaying  # position 0; the source fills it in
    hold: float | None  # a seek target shown until the first block after the seek
    loaded: int  # which load of a track this is (the position belongs to one load)


@dataclasses.dataclass(frozen=True, slots=True)
class _Waiting:
    """A track whose scan answer is awaited before it can be loaded."""

    track: Track
    play: bool
    seek: float | None
    since: float


class Playback:
    def __init__(
        self,
        store: QueueStore,
        *,
        sink: SinkSpec,
        stall_timeout: float,
        report: Report,
        scan_timeout: float = DISCOVER_TIMEOUT_S + 2.0,
    ) -> None:
        self._scan_timeout = scan_timeout
        self._store = store
        self._sink = sink
        self._stall_timeout = stall_timeout
        self._report = report
        self.cancelled: Callable[[], bool] = lambda: False  # True once the worker must stop
        saved = store.load()
        self.last_folder = (
            saved.last_folder
        )  # also set by `PlayerSource.open`, on the caller's thread
        self.queue = PlayQueue()
        self.queue.restore(saved.paths, index=saved.index)
        self.pool: ScanPool | None = None
        self.state = State.STOPPED
        self.backend: GstBackend | None = None
        self.hold: float | None = None
        self.progress_at: float | None = None  # while PLAYING: the last block (or the start)
        self.waiting: _Waiting | None = None
        self.seeks = 0
        self.serial = 0  # bumps whenever a track is (re)loaded; seeks name the one they mean
        self._skips: list[Track] = []  # unplayable tracks passed over on the way to this load
        self.loads = 0
        self.dirty = True  # something a reader could see changed since the last `view()`

    def view(self) -> View:
        q, cur = self.queue, self.queue.current
        if cur is None:
            now = NowPlaying("", "", "", None, 0.0, False, None, len(q), self.seeks, self.serial)
        else:
            t = cur.tags
            wants = self.waiting.play if self.waiting is not None else False
            playing = self.state is State.PLAYING or wants
            now = NowPlaying(
                t.artist,
                t.album,
                t.title,
                cur.duration,
                0.0,
                playing,
                q.index,
                len(q),
                self.seeks,
                self.serial,
            )
        return View(now, self.hold, self.loads)

    def position(self) -> float:
        """Seconds into the loaded track (worker only: it queries GStreamer)."""
        if self.backend is None or self.queue.index is None:
            return 0.0
        return self.backend.position() or 0.0

    # --- engine events ------------------------------------------------------------------------

    def block(self, now: float) -> None:
        """A block of the track's audio was delivered."""
        self.progress_at = now
        if self.hold is not None:
            self.hold = None
            self.dirty = True

    def handle(self, events: list[Event], now: float) -> None:
        for event in events:  # an output failure is never outranked by a file problem
            if event.kind == "engine-error":
                raise EngineError(event.text)
        for event in events:
            if event.kind == "track-error":
                self._track_failed(clean_text(event.text))
            else:
                self._advance()
            return  # later events belonged to the track just left
        stalled = self.progress_at is not None and now - self.progress_at > self._stall_timeout
        if self.state is State.PLAYING and stalled:
            # Every track here passed the preflight, and file problems arrive as bus errors: a
            # track that plays but yields no audio is far more likely a stuck output.
            raise EngineError(OUTPUT_SILENT)

    def _track_failed(self, reason: str) -> None:
        track = self.queue.current
        if track is not None:
            self.queue.mark_unplayable(track, reason)
        self._advance(failed=track)

    def _advance(self, failed: Track | None = None) -> None:
        """Past the end of the current track (it finished or failed)."""
        moved = self._step(forward=True, failed=failed)
        if moved:
            self._load_current(play=True)
        else:
            self._report_skips(self._take_skips())
            self._end_of_queue()

    def _step(self, *, forward: bool, failed: Track | None = None) -> bool:
        """Move to the next / previous playable track, remembering the unplayable ones passed."""
        moved = self.queue.next() if forward else self.queue.previous()
        self._skips = ([failed] if failed is not None else []) + self.queue.passed
        return moved

    def _take_skips(self) -> list[Track]:
        skipped, self._skips = self._skips, []
        return skipped

    def _report_skips(self, skipped: list[Track]) -> None:
        """One status for a run of tracks passed over: its reason if it is one, else a count."""
        if len(skipped) == 1:
            self._report("warn", f"Skipped {skipped[0].tags.title}: {skipped[0].reason}")
        elif skipped:
            self._report("warn", f"Skipped {len(skipped)} tracks that could not be played")

    def tick(self, now: float) -> None:
        """Called every worker loop: a track whose scan answer never comes is skipped."""
        w = self.waiting
        if w is not None and now - w.since > self._scan_timeout:
            self.waiting = None
            if w.track.alive and w.track.playable is None:
                self.queue.mark_unplayable(w.track, TIMED_OUT)
                self.dirty = True
            if self.queue.current is w.track:
                self._load_current(play=w.play, then_seek=w.seek)

    # --- commands -----------------------------------------------------------------------------

    def play(self) -> None:
        if self.waiting is not None:
            self.waiting = dataclasses.replace(self.waiting, play=True)
            self.dirty = True
        elif self.state is State.PAUSED and self.backend is not None:
            self.backend.play()
            self.state, self.progress_at = State.PLAYING, time.monotonic()
            self.dirty = True
        elif self.state is not State.PLAYING:
            self._load_current(play=True)

    def pause(self) -> None:
        if self.waiting is not None:  # cancels a play that is waiting for a scan answer
            self.waiting = dataclasses.replace(self.waiting, play=False)
            self.dirty = True
        elif self.state is State.PLAYING and self.backend is not None:
            self.backend.pause()
            self.state, self.progress_at = State.PAUSED, None
            self.dirty = True

    def toggle(self) -> None:
        wants_play = self.state is State.PLAYING or (self.waiting is not None and self.waiting.play)
        if wants_play:
            self.pause()
        else:
            self.play()

    def next(self) -> None:
        if self._step(forward=True):
            self._load_current(play=True)
        else:
            self._report_skips(self._take_skips())

    def previous(self) -> None:
        if self._step(forward=False):
            self._load_current(play=True)
        elif self.backend is None:
            self._report_skips(self._take_skips())
            self.play()  # stopped on the first track: play it from the start
        else:
            self._report_skips(self._take_skips())
            self.serial += 1  # the same track starts over: a seek aimed at the old run is stale
            self.seek(0.0)  # already on the first track: start it over, playing
            self.play()

    def select(self, index: int) -> None:
        if not 0 <= index < len(self.queue):
            raise ValueError(f"no queue entry {index}")
        track = self.queue.tracks[index]
        if track.playable is False:
            self._report_skips([track])
            return
        self.queue.select(index)
        self._load_current(play=True)

    def seek(self, seconds: float, serial: int | None = None) -> None:
        self.seeks += 1  # every attempt is a change the shell hears about, rejected or not
        self.dirty = True
        if serial is not None and serial != self.serial:
            return  # meant for a track that is no longer loaded
        if self.backend is None:
            if self.queue.current is not None:  # a stopped queue: open the track paused, then seek
                self._load_current(play=False, then_seek=seconds)
            return
        self._seek_loaded(self.backend, seconds)

    def _seek_loaded(self, backend: GstBackend, seconds: float) -> None:
        if backend.seek(seconds, self.cancelled):
            self.hold, self.progress_at = seconds, time.monotonic()
        else:
            self._report("warn", "This file cannot be seeked")
        self.dirty = True

    def open(self, files: list[Path], replace: bool) -> None:
        """Queue ``files``. Replacing starts the new queue; appending to a queue that was empty
        does too; appending to a non-empty one leaves playback alone."""
        was_empty = len(self.queue) == 0
        if replace:
            self.close_backend()
            self._cancel_scan()  # the old queue's probes must not delay the new one's
        first_new = 0 if replace else len(self.queue)
        dropped = self.queue.add(files, replace=replace)
        new = self.queue.tracks[first_new:]
        if new and self.pool is not None:
            self.pool.submit(new)
        if dropped:
            self._report("warn", f"The queue holds {MAX_QUEUE} tracks; {dropped} were not added")
        self.persist()
        self.dirty = True
        if new and (replace or was_empty):
            self._load_current(play=True)

    def clear(self) -> None:
        self.close_backend()
        self._cancel_scan()
        self.serial += 1
        self.queue.clear()
        self.persist()
        self.dirty = True

    def _cancel_scan(self) -> None:
        if self.pool is not None:
            self.pool.cancel()

    def apply_probe(self, track: Track, result: ProbeResult) -> None:
        if not track.alive:  # it left the queue while being probed
            return
        if result.playable:
            self.queue.apply_probe(track, tags=result.tags or track.tags, duration=result.duration)
        else:
            self.queue.mark_unplayable(track, result.reason)
        self.dirty = True
        w = self.waiting
        if w is not None and w.track is track:
            self.waiting = None
            if self.queue.current is track:
                self._load_current(play=w.play, then_seek=w.seek)

    # --- loading tracks -----------------------------------------------------------------------

    def _load_current(self, *, play: bool, then_seek: float | None = None) -> None:
        """Start the current track, first passing over (and reporting once) any that cannot be
        played."""
        self.serial += 1
        skipped = self._take_skips()
        for _ in range(len(self.queue) + 1):  # at most one pass over the queue
            if self.cancelled():
                return
            track = self.queue.current
            if track is None:
                break
            if track.playable is None:  # not scanned yet: wait for its answer, in silence
                if self.pool is None:
                    raise RuntimeError("the scan pool is not running")
                self.close_backend()
                self.waiting = _Waiting(track, play, then_seek, time.monotonic())
                self._skips = skipped  # reported when the wait ends
                self.pool.submit([track], urgent=True)
                self.dirty = True
                return
            if track.playable is False:
                skipped.append(track)
                moved = self.queue.next()
                skipped += self.queue.passed
                if moved:
                    continue
                break
            self._report_skips(skipped)
            self._open(track, play, then_seek)
            return
        self._report_skips(skipped)
        self._end_of_queue()

    def _open(self, track: Track, play: bool, then_seek: float | None) -> None:
        if self.backend is None:
            self.backend = GstBackend(self._sink)
        self.waiting = None
        self.backend.open(track.path, play=play)
        self.loads += 1
        self.state = State.PLAYING if play else State.PAUSED
        self.hold = None
        self.progress_at = time.monotonic() if play else None
        self.persist()
        self.dirty = True
        if then_seek is not None:
            self._seek_loaded(self.backend, then_seek)

    def _end_of_queue(self) -> None:
        """Nothing left to play: stop (silence follows) and rewind to the first track."""
        self.close_backend()
        self.serial += 1
        self.queue.index = 0 if len(self.queue) else None
        self.persist()
        self.dirty = True

    def close_backend(self) -> None:
        backend, self.backend = self.backend, None
        if backend is not None:
            backend.close()
        self.state, self.hold, self.progress_at, self.waiting = State.STOPPED, None, None, None
        self.dirty = True

    def persist(self) -> None:
        q = self.queue
        state = StoredQueue([t.path for t in q.tracks], q.index or 0, self.last_folder)
        try:
            self._store.save(state)
        except OSError as e:
            log.warning("could not save the queue: %s", e)
