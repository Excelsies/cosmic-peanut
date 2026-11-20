"""PCM producers (docs/protocols.md §2) and the ring buffer they feed."""

import sys
from pathlib import Path

from tidalviz.capture.base import AudioSource, OnSamples, SourceFormat
from tidalviz.capture.file import FileSource, read_wav
from tidalviz.capture.ring import RingBuffer
from tidalviz.capture.synthetic import SyntheticSource

__all__ = [
    "AudioApp",
    "AudioSource",
    "FileSource",
    "OnSamples",
    "RingBuffer",
    "SourceFormat",
    "SyntheticSource",
    "app_source",
    "list_audio_apps",
    "player_available",
    "player_source",
    "read_wav",
    "system_source",
]

# The platform's capture backend (the rest of the package is portable): catap on macOS,
# PipeWire elsewhere (Linux). Each branch defines the same four names.
if sys.platform == "darwin":
    from tidalviz.capture.catap_source import (
        AudioApp,
        CatapAppSource,
        CatapSystemSource,
        list_audio_apps,
    )

    __all__ += ["CatapAppSource", "CatapSystemSource"]

    def system_source() -> AudioSource:
        return CatapSystemSource()

    def app_source(app: AudioApp) -> AudioSource:
        return CatapAppSource(app.name)

else:
    from tidalviz.capture.pipewire_source import (
        AudioApp,
        PipeWireAppSource,
        PipeWireSystemSource,
        list_audio_apps,
    )

    __all__ += ["PipeWireAppSource", "PipeWireSystemSource"]

    def system_source() -> AudioSource:
        return PipeWireSystemSource()

    def app_source(app: AudioApp) -> AudioSource:
        return PipeWireAppSource(app)


def player_available() -> bool:
    """Whether the local-file player can run here (it needs GStreamer, i.e. the `gtk` extra)."""
    from tidalviz.player import engine_available

    return engine_available()


def player_source(root: Path) -> AudioSource:
    """The local-file player, keeping its queue under ``root``. ValueError without GStreamer."""
    from tidalviz.player import create_source

    return create_source(root)
