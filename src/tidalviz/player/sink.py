"""Which GStreamer element plays the audio, and where (`TIDALVIZ_PLAYER_SINK`)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_VAR = "TIDALVIZ_PLAYER_SINK"
ELEMENTS = ("pulsesink", "fakesink")  # pipewiresink played at the wrong speed after a rate change


@dataclass(frozen=True, slots=True)
class SinkSpec:
    element: str = "pulsesink"
    device: str | None = None  # a PulseAudio sink name; None follows the default device


def sink_from_env(environ: Mapping[str, str] = os.environ) -> SinkSpec:
    """``TIDALVIZ_PLAYER_SINK=<element>[:<device>]``, e.g. ``fakesink`` or ``pulsesink:my_sink``.

    Unset means ``pulsesink`` on the default device. Tests use ``fakesink``; manual tests pin a
    null sink by name so nothing reaches the speakers.
    """
    value = environ.get(ENV_VAR, "").strip()
    if not value:
        return SinkSpec()
    element, _, device = value.partition(":")
    if element not in ELEMENTS:
        raise ValueError(f"{ENV_VAR}: {element!r} is not one of {', '.join(ELEMENTS)}")
    if device and element != "pulsesink":
        raise ValueError(f"{ENV_VAR}: only pulsesink takes a device")
    return SinkSpec(element, device or None)
