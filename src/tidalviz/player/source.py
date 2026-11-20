"""`PlayerSource`: plays the queue's files through GStreamer and is an `AudioSource` for them.

One worker thread owns everything (`Playback`: the queue, the playback state, the pipeline, the
scan results). Other threads (the host's loop, the scan pool) only post commands to it and read
the immutable snapshots it publishes, so there are no locks. Each loop it pulls the next block
from the engine and calls ``on_samples`` with it; when nothing is playing (paused, stopped,
between tracks, empty queue) it calls ``on_samples`` with a block of silence at the hop cadence,
because the pipeline treats a source that goes quiet as frozen, not as silent.

Failure policy: a file that cannot be played is a per-track outcome (marked unplayable, reported
as a status, skipped). ``failed`` is only for the engine breaking (the output reports an error or stops
delivering, pipeline fault), which makes the pipeline restart this source, stopped at the current track.
"""

import dataclasses
import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

import numpy as np

from tidalviz.capture.base import OnSamples, SourceFormat
from tidalviz.capture.transport import Changed, NowPlaying, QueueSnapshot, StatusSink
from tidalviz.player import engine
from tidalviz.player.engine import BLOCK, CHANNELS, SAMPLE_RATE, EngineError
from tidalviz.player.library import AUDIO_EXTENSIONS, collect_audio_files
from tidalviz.player.playback import Playback, View
from tidalviz.player.queue import Track
from tidalviz.player.scan import Probe, ProbeResult, ScanPool
from tidalviz.player.sink import SinkSpec, sink_from_env
from tidalviz.player.store import QueueStore
from tidalviz.player.tags import clean_text

log = logging.getLogger(__name__)

BLOCK_S = BLOCK / SAMPLE_RATE
PULL_S = 0.02  # longest wait for a block (and so for a command) while music flows
STALL_S = 0.05  # no block for this long and the source counts as quiet: silence blocks start
STALL_TIMEOUT_S = 5.0  # PLAYING but no block this long: the output is stuck (engine failure)
JOIN_S = 2.0


@dataclasses.dataclass(frozen=True, slots=True)
class _Seek:
    """A queued seek, recognisable among the commands so that stale ones can be skipped."""

    run: Callable[[float, int | None], None]
    seconds: float
    serial: int | None

    def __call__(self) -> None:
        self.run(self.seconds, self.serial)


class PlayerSource:
    name = "Files"

    def __init__(
        self,
        store_path: Path,
        *,
        sink: SinkSpec | None = None,
        probe: Probe = engine.probe,
        stall_timeout: float = STALL_TIMEOUT_S,
        scan_timeout: float | None = None,
    ) -> None:
        self.failed = threading.Event()
        self._format = SourceFormat(float(SAMPLE_RATE), CHANNELS)
        spec = sink if sink is not None else sink_from_env()
        engine.check_sink(spec)
        self._probe = probe
        self._pb = Playback(
            QueueStore(store_path),
            sink=spec,
            stall_timeout=stall_timeout,
            report=self._report,
            **({} if scan_timeout is None else {"scan_timeout": scan_timeout}),
        )
        self._published_revision: int = -1
        self._commands: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self._on_change: Changed | None = None
        self._on_status: StatusSink | None = None
        self._thread: threading.Thread | None = None
        self._stop: threading.Event = threading.Event()  # replaced on every start
        self._pos = (0, 0.0)  # (load, seconds): the worker's last reading of the position
        self._block = np.zeros((BLOCK, CHANNELS), dtype=np.float32)
        self._zeros = np.zeros((BLOCK, CHANNELS), dtype=np.float32)
        self._view: View = self._pb.view()
        self._snapshot = QueueSnapshot(self._pb.queue.revision, self._pb.queue.info())

    # --- AudioSource ------------------------------------------------------------------------

    @property
    def format(self) -> SourceFormat:
        return self._format

    def start(self, on_samples: OnSamples) -> None:
        if self._thread is not None and self._thread.is_alive():
            # A worker that outlived a timed-out stop() still owns the playback state.
            raise RuntimeError("the player thread is still running")
        self.failed.clear()
        stop = self._stop = threading.Event()  # this worker's own: nothing can un-stop it later
        self._pb.cancelled = stop.is_set
        pool = ScanPool(self._probe, self._probed)
        self._pb.pool = pool
        pool.submit(t for t in self._pb.queue.tracks if t.playable is None)
        self._thread = threading.Thread(
            target=self._run, args=(on_samples, pool, stop), name="tidalviz-player", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Safe from any thread, including ``on_samples`` (the worker then finishes on its own).
        A worker that does not finish within JOIN_S stays registered, so `start()` refuses."""
        self._stop.set()
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return
        thread.join(JOIN_S)
        if thread.is_alive():
            log.warning("player thread did not stop within %.0f s", JOIN_S)
        else:
            self._thread = None

    # --- Transport --------------------------------------------------------------------------

    def bind(self, on_change: Changed, on_status: StatusSink) -> None:
        self._on_change, self._on_status = on_change, on_status

    def now_playing(self) -> NowPlaying:
        view = self._view
        loaded, seconds = self._pos
        position = view.hold if view.hold is not None else seconds if loaded == view.loaded else 0.0
        return dataclasses.replace(view.now, position=position)

    def play(self) -> None:
        self._commands.put(self._pb.play)

    def pause(self) -> None:
        self._commands.put(self._pb.pause)

    def toggle(self) -> None:
        self._commands.put(self._pb.toggle)

    def next(self) -> None:
        self._commands.put(self._pb.next)

    def previous(self) -> None:
        self._commands.put(self._pb.previous)

    def seek(self, seconds: float, serial: int | None = None) -> None:
        self._commands.put(_Seek(self._pb.seek, max(0.0, seconds), serial))

    def select(self, index: int) -> None:
        count = self._view.now.count
        if not 0 <= index < count:
            raise ValueError(f"no queue entry {index} (queue has {count})")
        self._commands.put(partial(self._pb.select, index))

    # --- QueueControl -----------------------------------------------------------------------

    @property
    def extensions(self) -> tuple[str, ...]:
        return AUDIO_EXTENSIONS

    @property
    def last_folder(self) -> str | None:
        return self._pb.last_folder

    def queue(self) -> QueueSnapshot:
        return self._snapshot

    def open(self, paths: Sequence[Path], *, replace: bool) -> None:
        """Files and folders; folders are searched here, on the caller's thread. Nothing found
        changes nothing (and says so)."""
        found = collect_audio_files(paths)
        if found.missing:  # a count, never the names
            n = found.missing
            self._report(
                "warn", f"{n} file was not found" if n == 1 else f"{n} files were not found"
            )
        if not found.files and found.missing == len(paths):
            return  # every path was missing, and that was said: no second status
        if not found.files:
            self._report(
                "warn" if found.truncated else "info",
                "No audio files found in the part searched (the folder is very large)"
                if found.truncated
                else "No audio files found",
            )
            return
        if found.truncated:
            self._report(
                "warn",
                f"The folder is too big to search completely: found {len(found.files)} audio files before stopping",
            )
        self._pb.last_folder = str(paths[0] if paths[0].is_dir() else paths[0].parent)
        self._commands.put(partial(self._pb.open, found.files, replace))

    def clear(self) -> None:
        self._commands.put(self._pb.clear)

    # --- the worker -------------------------------------------------------------------------

    def _run(self, on_samples: OnSamples, pool: ScanPool, stop: threading.Event) -> None:
        pb, block, zeros = self._pb, self._block, self._zeros
        next_silence = last_block = 0.0
        try:
            while not stop.is_set():
                self._run_commands(0.0)
                backend = pb.backend
                now = time.monotonic()
                flowing = now - last_block <= STALL_S
                wait = PULL_S if flowing else min(PULL_S, max(0.0, next_silence - now))
                got = False
                if backend is not None:
                    got = backend.pull(wait, block)
                    spare = wait - (time.monotonic() - now)
                    if not got and spare > 0.001:
                        # An appsink that is not running yet (or flushing) answers at once; sleep
                        # the rest of the wait rather than spin, but still hear commands.
                        self._run_commands(spare)
                else:
                    self._run_commands(wait)
                if got:
                    last_block = t = time.monotonic()
                    pb.block(last_block)
                    if stop.is_set():
                        break
                    on_samples(block, t)
                else:
                    now = time.monotonic()
                    if now >= next_silence and now - last_block > STALL_S:
                        on_samples(zeros, now)
                        on_time = now - next_silence < BLOCK_S
                        next_silence = next_silence + BLOCK_S if on_time else now + BLOCK_S
                if pb.backend is not None:
                    pb.handle(pb.backend.poll(), time.monotonic())
                pb.tick(time.monotonic())
                self._pos = (pb.loads, pb.position())
                self._publish()
        except EngineError as e:
            log.warning("player engine failed: %s", e)
            self._report("error", str(e))
            self._fail(stop)
        except Exception:
            log.exception("player stopped unexpectedly")
            self._report("error", "The player stopped unexpectedly")
            self._fail(stop)
        finally:
            pool.close()
            pb.close_backend()
            pb.persist()
            self._pos = (pb.loads, 0.0)
            self._publish()

    def _fail(self, stop: threading.Event) -> None:
        if not stop.is_set():
            self.failed.set()

    def _run_commands(self, wait: float) -> None:
        try:
            first = self._commands.get(timeout=wait) if wait > 0 else self._commands.get_nowait()
        except queue.Empty:
            return
        cmds = [first]
        while True:
            try:
                cmds.append(self._commands.get_nowait())
            except queue.Empty:
                break
        # Key auto-repeat can queue dozens of seeks; each is an accurate flushing seek, so only
        # the newest runs.
        newest = max((i for i, c in enumerate(cmds) if isinstance(c, _Seek)), default=-1)
        for i, cmd in enumerate(cmds):
            if isinstance(cmd, _Seek) and i != newest:
                continue
            try:
                cmd()
            except EngineError:
                raise
            except Exception:
                log.exception("player command failed")
                self._report("error", "That player command failed")

    def _probed(self, track: Track, result: ProbeResult) -> None:
        """Pool thread: hand a scan result to the worker."""
        self._commands.put(partial(self._pb.apply_probe, track, result))

    # --- publishing ---------------------------------------------------------------------------

    def _publish(self) -> None:
        pb = self._pb
        if not pb.dirty:
            return
        pb.dirty = False
        self._view = pb.view()
        if pb.queue.revision != self._published_revision:
            self._published_revision = pb.queue.revision
            self._snapshot = QueueSnapshot(pb.queue.revision, pb.queue.info())
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                log.exception("player change callback failed")

    def _report(self, level: str, text: str) -> None:
        log.log(logging.INFO if level == "info" else logging.WARNING, text)
        if self._on_status is not None:
            self._on_status(level, clean_text(text))
