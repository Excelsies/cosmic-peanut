"""Helpers for driving a `PlayerSource` the way the pipeline does."""

import threading
import time
from collections.abc import Callable

import numpy as np

from tidalviz.capture.base import SourceFormat
from tidalviz.frame import F32

BLOCK = 512


def wait_until(pred: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


class Recorder:
    """An `on_samples` that keeps copies of what it was handed, plus status and change events."""

    def __init__(self) -> None:
        self.blocks: list[tuple[F32, float]] = []
        self.statuses: list[tuple[str, str]] = []
        self.changes = 0
        self.threads: set[int] = set()
        self._lock = threading.Lock()

    def on_samples(self, block: F32, t: float) -> None:
        with self._lock:
            self.blocks.append((block.copy(), t))
            self.threads.add(threading.get_ident())

    def on_change(self) -> None:
        self.changes += 1

    def on_status(self, level: str, text: str) -> None:
        self.statuses.append((level, text))

    def snapshot(self) -> list[tuple[F32, float]]:
        with self._lock:
            return list(self.blocks)

    def count(self) -> int:
        with self._lock:
            return len(self.blocks)

    def peaks(self) -> np.ndarray:
        return np.array([float(np.abs(b).max()) for b, _ in self.snapshot()])

    def loud(self, threshold: float = 1e-4) -> int:
        return int((self.peaks() > threshold).sum())

    def fmt_ok(self, fmt: SourceFormat) -> bool:
        return all(
            b.shape == (BLOCK, fmt.channels) and b.dtype == np.float32 for b, _ in self.snapshot()
        )
