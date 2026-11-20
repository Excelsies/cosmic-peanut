"""The native open dialogs (pywebview's file dialog behind a fake window object)."""

from collections.abc import Sequence
from typing import Any

import webview  # type: ignore[import-untyped]
from webview.util import parse_file_type  # type: ignore[import-untyped]

from tidalviz.window import PyWebviewWindow
from tidalviz.window_gtk import GtkWindow


class FakeWebviewWindow:
    def __init__(self, result: Sequence[str] | None) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def create_file_dialog(self, kind: int, **kw: Any) -> Sequence[str] | None:
        self.calls.append({"kind": kind, **kw})
        return self.result


def window(result: Sequence[str] | None) -> tuple[PyWebviewWindow, FakeWebviewWindow]:
    w = PyWebviewWindow()
    fake = FakeWebviewWindow(result)
    w.win = fake
    return w, fake


def test_pick_audio_files_is_a_multi_select_open_dialog_filtered_to_the_extensions():
    w, fake = window(["/m/a.mp3", "/m/b.flac"])
    got = w.pick_audio_files("/m", (".mp3", ".flac"))
    assert got == ["/m/a.mp3", "/m/b.flac"]
    (call,) = fake.calls
    assert call["kind"] == webview.FileDialog.OPEN and call["allow_multiple"] is True
    assert call["directory"] == "/m"
    (filter_,) = call["file_types"]
    description, patterns = parse_file_type(filter_)  # pywebview's own validator accepts it
    assert description == "Audio files"
    assert set(patterns.split(";")) == {"*.mp3", "*.MP3", "*.flac", "*.FLAC"}  # GTK matches case


def test_a_cancelled_dialog_gives_no_files_and_no_directory_starts_blank():
    w, fake = window(None)
    assert w.pick_audio_files(None, (".mp3",)) == []
    assert fake.calls[0]["directory"] == ""


def test_pick_folder_starts_where_asked_and_keeps_its_old_call_form():
    w, fake = window(["/music/album"])
    assert w.pick_folder("/music") == "/music/album"
    assert fake.calls[-1] == {"kind": webview.FileDialog.FOLDER, "directory": "/music"}
    assert w.pick_folder() == "/music/album"
    assert fake.calls[-1]["directory"] == ""
    assert window(None)[0].pick_folder() is None


def test_the_gtk_window_inherits_the_dialogs():
    assert GtkWindow.pick_audio_files is PyWebviewWindow.pick_audio_files
    assert GtkWindow.pick_folder is PyWebviewWindow.pick_folder
