"""The GStreamer side of the player: the only module that imports PyGObject.

    playbin3 (audio only) -> audio-sink bin:
        tee ! queue ! audioconvert ! audioresample ! <output sink, sync=true>
        tee ! queue ! audioconvert ! audioresample ! F32LE/48000/2 interleaved
            ! audiobuffersplit(one hop) ! appsink sync=true

The appsink sees exactly the samples the output sink plays, 48 kHz stereo whatever the file is,
in blocks of one analysis hop. There is no GLib main loop and no signal handler: the caller (the
source's worker thread) pulls blocks and polls the bus, so every call into a `GstBackend` comes
from that one thread (the worker publishes the position for other threads to read). `probe` is the
exception: it runs on the scan threads, each with its own discoverer.

Nothing GStreamer says (error text, file names, URIs) is passed on: reasons shown to the user come
from the small fixed vocabulary below, and the library's own text goes to the debug log.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false, reportAttributeAccessIssue=false, reportPrivateImportUsage=false

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, NamedTuple

import gi  # type: ignore[import-not-found]
import numpy as np

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")  # gives GstAppSink its methods (try_pull_sample, ...)
gi.require_version("GstPbutils", "1.0")
from gi.repository import (  # type: ignore[import-not-found]  # noqa: E402
    GLib,
    Gst,
    GstApp,  # noqa: F401  # pyright: ignore[reportUnusedImport]  (importing it adds appsink's methods)
    GstPbutils,
)

from tidalviz.frame import F32  # noqa: E402
from tidalviz.player.scan import ProbeResult  # noqa: E402
from tidalviz.player.sink import SinkSpec  # noqa: E402
from tidalviz.player.tags import TrackTags, tags_for  # noqa: E402

Gst.init(None)

SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK = 512  # frames: the analysis hop
BLOCK_BYTES = BLOCK * CHANNELS * 4
_AUDIO_ONLY = 0x02  # GST_PLAY_FLAG_AUDIO
_REQUIRED = (
    "playbin3",
    "tee",
    "queue",
    "audioconvert",
    "audioresample",
    "audiobuffersplit",
    "appsink",
    "fakesink",
)
DISCOVER_TIMEOUT_S = 5
SEEK_PREROLL_WAIT_S = 2.0
_SLICE_S = 0.05  # longest single wait inside a call that can be asked to give up

# What a user may be told about a file (docs/protocols.md §4 `status`).
NOT_FOUND = "file not found"
UNREADABLE = "cannot be read"
NOT_AUDIO = "not an audio file"
UNDECODABLE = "could not be decoded"
TIMED_OUT = "timed out"
OUTPUT_FAILED = "The audio output stopped working"
OUTPUT_SILENT = "The audio output stopped delivering sound"

log = logging.getLogger(__name__)

_missing = [name for name in _REQUIRED if Gst.ElementFactory.find(name) is None]
if _missing:
    raise ImportError(f"GStreamer elements not installed: {', '.join(_missing)}")


class EngineError(RuntimeError):
    """The engine itself broke (the output reports an error, pipeline fault), not the file being played."""


class Event(NamedTuple):
    kind: Literal["eos", "track-error", "engine-error"]
    text: str


def reason_for(err: Any) -> str:
    """A `GLib.Error` from GStreamer as one phrase of the fixed vocabulary."""
    if err.matches(Gst.resource_error_quark(), Gst.ResourceError.NOT_FOUND):
        return NOT_FOUND
    if err.domain == "gst-resource-error-quark":  # OPEN_READ, READ, ...: permissions, I/O
        return UNREADABLE
    for code in (Gst.StreamError.TYPE_NOT_FOUND, Gst.StreamError.WRONG_TYPE):
        if err.matches(Gst.stream_error_quark(), code):
            return NOT_AUDIO
    return UNDECODABLE


def check_sink(spec: SinkSpec) -> None:
    if Gst.ElementFactory.find(spec.element) is None:
        raise ValueError(f"GStreamer element {spec.element} is not installed")


class GstBackend:
    """One playbin3 pipeline that plays one file after another (no gapless: a plain state change)."""

    def __init__(self, sink: SinkSpec) -> None:
        check_sink(sink)
        desc = (
            "tee name=t "
            "t. ! queue ! audioconvert ! audioresample ! identity name=tail "
            "t. ! queue ! audioconvert ! audioresample ! "
            f"audio/x-raw,format=F32LE,rate={SAMPLE_RATE},channels={CHANNELS},layout=interleaved ! "
            f"audiobuffersplit output-buffer-size={BLOCK_BYTES} strict-buffer-size=true ! "
            "appsink name=a sync=true emit-signals=false max-buffers=8 drop=false"
        )
        try:
            self._bin = Gst.parse_bin_from_description(desc, False)
            ghost = Gst.GhostPad.new("sink", self._bin.get_by_name("t").get_static_pad("sink"))
            self._bin.add_pad(ghost)
            self._appsink = self._bin.get_by_name("a")
            out = Gst.ElementFactory.make(sink.element, "out")
            if out is None:
                raise EngineError(OUTPUT_FAILED)
            out.set_property("sync", True)
            if sink.device is not None:  # a property value, never spliced into a description
                out.set_property("device", sink.device)
            self._bin.add(out)
            if not self._bin.get_by_name("tail").link(out):
                raise EngineError(OUTPUT_FAILED)
            play = Gst.ElementFactory.make("playbin3", "play")
            play.set_property("flags", _AUDIO_ONLY)
            play.set_property("audio-sink", self._bin)
        except (GLib.Error, TypeError, ValueError, AttributeError) as e:
            log.warning("cannot build the playback pipeline: %s", e)
            raise EngineError(OUTPUT_FAILED) from e
        self._play = play
        self._bus = play.get_bus()
        self._eos_seen = False

    # --- control ----------------------------------------------------------------------------

    def open(self, path: Path, *, play: bool) -> None:
        """Start (or prepare, paused) a file, abandoning the current one."""
        self._set_state(Gst.State.READY)
        self._bus.set_flushing(True)  # the old track's late messages must not count for this one
        self._bus.set_flushing(False)
        self._eos_seen = False
        self._play.set_property("uri", path.resolve().as_uri())
        self._set_state(Gst.State.PLAYING if play else Gst.State.PAUSED)

    def play(self) -> None:
        self._set_state(Gst.State.PLAYING)

    def pause(self) -> None:
        self._set_state(Gst.State.PAUSED)

    def seek(self, seconds: float, cancelled: Callable[[], bool] = lambda: False) -> bool:
        """An accurate seek (key-unit seeks landed 0.6-1.4 s early in WMA). Waits for a track that
        is still prerolling (a few ms; at most SEEK_PREROLL_WAIT_S, in slices, giving up as soon as
        ``cancelled()``), since a pipeline that has not prerolled cannot seek."""
        end = time.monotonic() + SEEK_PREROLL_WAIT_S
        while True:
            change, _, _ = self._play.get_state(int(_SLICE_S * Gst.SECOND))
            if change != Gst.StateChangeReturn.ASYNC:
                break
            if cancelled() or time.monotonic() >= end:
                return False
        flags = Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE
        return bool(self._play.seek_simple(Gst.Format.TIME, flags, int(seconds * Gst.SECOND)))

    def close(self) -> None:
        self._play.set_state(Gst.State.NULL)

    def _set_state(self, state: Any) -> None:
        # A failed change (a bad file, a dead sink) also posts an ERROR on the bus, which `poll`
        # classifies; here the return value only gets logged.
        if self._play.set_state(state) == Gst.StateChangeReturn.FAILURE:
            log.warning("GStreamer refused the state change to %s", state.value_nick)

    # --- reading ----------------------------------------------------------------------------

    def pull(self, timeout_s: float, out: F32) -> bool:
        """Wait up to ``timeout_s`` for the next block and copy it into ``out`` (BLOCK, CHANNELS).

        This is the one place that moves samples; PyGObject makes a ~4 KB ``bytes`` copy per call
        that is gone before the next, so a pipe-fed reader could replace it unchanged.
        """
        sample = self._appsink.try_pull_sample(int(timeout_s * Gst.SECOND))
        if sample is None:
            return False
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            log.error("cannot map an audio buffer")
            raise EngineError(OUTPUT_FAILED)
        try:
            if info.size != BLOCK_BYTES:
                log.error("audio block of %d bytes, expected %d", info.size, BLOCK_BYTES)
                raise EngineError(OUTPUT_FAILED)
            out.reshape(-1)[:] = np.frombuffer(info.data, dtype="<f4")
        finally:
            buf.unmap(info)
        return True

    def poll(self) -> list[Event]:
        """End of stream (once its last block has been pulled) and errors since the last call."""
        events: list[Event] = []
        while (
            msg := self._bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        ) is not None:
            if msg.type == Gst.MessageType.EOS:
                self._eos_seen = True
                continue
            err, debug = msg.parse_error()
            log.debug("GStreamer error: %s (%s)", err.message, debug)
            if self._in_sink_bin(msg.src):
                events.append(Event("engine-error", OUTPUT_FAILED))
            else:
                events.append(Event("track-error", reason_for(err)))
        if self._eos_seen and self._appsink.get_property("eos"):
            self._eos_seen = False
            events.append(Event("eos", ""))
        return events

    def _in_sink_bin(self, element: Any) -> bool:
        for _ in range(32):  # bounded walk up the element tree
            if element is None:
                return False
            if element is self._bin:
                return True
            element = element.get_parent()
        return False

    def position(self) -> float | None:
        """Seconds into the current file, or None while GStreamer can't say (right after a seek)."""
        ok, ns = self._play.query_position(Gst.Format.TIME)
        return ns / Gst.SECOND if ok else None


# --- preflight / tags ---------------------------------------------------------------------------

_local = threading.local()


def _discoverer() -> Any:
    d = getattr(_local, "d", None)
    if d is None:
        d = _local.d = GstPbutils.Discoverer.new(DISCOVER_TIMEOUT_S * Gst.SECOND)
    return d


def _tag(taglists: list[Any], name: str) -> str:
    value = ""
    for tl in taglists:  # later lists (the audio stream's own) win over the container's
        ok, text = tl.get_string(name)
        if ok and text:
            value = text
    return value


def probe(path: Path) -> ProbeResult:
    """Can this file be played, and what are its tags and length? One Discoverer per thread."""
    if not path.is_file():
        return ProbeResult(False, NOT_FOUND)
    if not os.access(path, os.R_OK):
        return ProbeResult(False, UNREADABLE)
    try:
        info = _discoverer().discover_uri(path.resolve().as_uri())
    except GLib.Error as e:
        log.debug("discoverer: %s", e.message)
        return ProbeResult(False, reason_for(e))
    streams = info.get_audio_streams()
    result = info.get_result()
    if result == GstPbutils.DiscovererResult.MISSING_PLUGINS:
        return ProbeResult(False, UNDECODABLE)
    if result == GstPbutils.DiscovererResult.TIMEOUT:
        return ProbeResult(False, TIMED_OUT)
    if result != GstPbutils.DiscovererResult.OK or not streams:
        return ProbeResult(False, NOT_AUDIO)
    lists = [
        tl for holder in (info.get_stream_info(), *streams) if (tl := holder.get_tags()) is not None
    ]
    tags: TrackTags = tags_for(
        path,
        artist=_tag(lists, "artist"),
        album=_tag(lists, "album"),
        title=_tag(lists, "title"),
    )
    ns = info.get_duration()
    duration = ns / Gst.SECOND if 0 < ns < Gst.CLOCK_TIME_NONE else None
    return ProbeResult(True, tags=tags, duration=duration)
