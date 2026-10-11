"""GTK window environment (Linux); the window itself is thin platform code, like `window.py`."""

from pathlib import Path
from typing import Any

from tidalviz.window_gtk import configure_gl_environment, monitor_refresh_hz

VAR = "__NV_DISABLE_EXPLICIT_SYNC"


def test_disables_nvidia_explicit_sync_when_the_nvidia_driver_is_loaded(tmp_path: Path):
    marker = tmp_path / "version"
    marker.write_text("NVRM version: ...")
    env: dict[str, str] = {}
    configure_gl_environment(env, nvidia_marker=marker)
    assert env == {VAR: "1"}


def test_leaves_the_environment_alone_without_the_nvidia_driver(tmp_path: Path):
    env: dict[str, str] = {}
    configure_gl_environment(env, nvidia_marker=tmp_path / "missing")
    assert env == {}


def test_a_value_the_user_set_wins(tmp_path: Path):
    marker = tmp_path / "version"
    marker.write_text("x")
    env = {VAR: "0"}
    configure_gl_environment(env, nvidia_marker=marker)
    assert env == {VAR: "0"}


class _Monitor:
    def __init__(self, millihertz: int) -> None:
        self.millihertz = millihertz

    def get_refresh_rate(self) -> int:
        return self.millihertz


class _Display:
    def __init__(self, monitor: _Monitor | None) -> None:
        self.monitor = monitor
        self.asked: Any = None

    def get_monitor_at_window(self, window: Any) -> _Monitor | None:
        self.asked = window
        return self.monitor


class _Widget:
    """The two Gtk.Widget calls monitor_refresh_hz makes (GTK needs a display to run)."""

    def __init__(self, window: Any, display: _Display) -> None:
        self.window, self.display = window, display

    def get_window(self) -> Any:
        return self.window

    def get_display(self) -> _Display:
        return self.display


def test_reads_the_refresh_rate_of_the_monitor_showing_the_window():
    display = _Display(_Monitor(29_970))
    gdk_window = object()
    assert monitor_refresh_hz(_Widget(gdk_window, display)) == 29.97
    assert display.asked is gdk_window


def test_refresh_rate_is_0_when_unknown():
    assert monitor_refresh_hz(_Widget(None, _Display(_Monitor(60_000)))) == 0  # not realized
    assert monitor_refresh_hz(_Widget(object(), _Display(None))) == 0
    assert monitor_refresh_hz(_Widget(object(), _Display(_Monitor(0)))) == 0
