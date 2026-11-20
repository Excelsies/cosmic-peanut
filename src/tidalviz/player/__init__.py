"""Local-file music player: queue, library scan, GStreamer source (docs/protocols.md §2, §4).

GStreamer and PyGObject exist only with the `gtk` extra on Linux. Nothing here imports them
except `engine.py`, and this package never imports that at import time: `engine_available()`
tries it once, and `create_source()` is the one way in for the rest of the app.
"""

import functools
import importlib
from pathlib import Path

from tidalviz.capture.base import AudioSource

STORE_NAME = "player.json"


@functools.cache
def _engine_problem() -> str | None:
    """Why the GStreamer engine cannot be imported, or None when it can."""
    try:
        importlib.import_module("tidalviz.player.engine")
    except (ImportError, ValueError) as e:  # ValueError: gi.require_version found no typelib
        return f"{type(e).__name__}: {e}"
    return None


def engine_available() -> bool:
    return _engine_problem() is None


def create_source(root: Path) -> AudioSource:
    """The `player` source, keeping its queue in ``root``. ValueError when GStreamer is missing."""
    problem = _engine_problem()
    if problem is not None:
        raise ValueError(f"the file player needs GStreamer ({problem})")
    from tidalviz.player.source import PlayerSource

    return PlayerSource(root / STORE_NAME)
