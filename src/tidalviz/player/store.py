"""player.json in the app's data dir: the queue's file paths, the current index, and the last
folder the open dialog used. Not part of settings.json; the playback position is not kept."""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from tidalviz.player.queue import MAX_QUEUE
from tidalviz.plugins.store import atomic_write_json

VERSION = 1


@dataclass(frozen=True, slots=True)
class StoredQueue:
    paths: list[Path]
    index: int
    last_folder: str | None


class QueueStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> StoredQueue:
        """The saved queue without the files that are gone; the index follows the survivors."""
        try:
            loaded: Any = json.loads(self.path.read_text())
        except FileNotFoundError:
            return StoredQueue([], 0, None)
        except (OSError, ValueError, RecursionError):  # the last: absurdly nested JSON
            os.replace(
                self.path,
                self.path.with_name(f"{self.path.name}.corrupt-{int(time.time() * 1000)}"),
            )
            return StoredQueue([], 0, None)
        if not isinstance(loaded, dict):
            return StoredQueue([], 0, None)
        doc = cast(dict[str, Any], loaded)
        raw: Any = doc.get("paths")
        names = (
            [p for p in cast(list[Any], raw)[:MAX_QUEUE] if isinstance(p, str)]
            if isinstance(raw, list)
            else []
        )
        index: Any = doc.get("index")
        index = index if type(index) is int else 0
        folder: Any = doc.get("lastFolder")
        paths: list[Path] = []
        kept_before_index = 0
        for i, name in enumerate(names):
            if Path(name).is_file():
                paths.append(Path(name))
                kept_before_index += i < index
        index = min(kept_before_index, max(len(paths) - 1, 0))
        return StoredQueue(paths, index, folder if isinstance(folder, str) else None)

    def save(self, state: StoredQueue) -> None:
        atomic_write_json(
            self.path,
            {
                "version": VERSION,
                "paths": [str(p) for p in state.paths],
                "index": state.index,
                "lastFolder": state.last_folder,
            },
        )
