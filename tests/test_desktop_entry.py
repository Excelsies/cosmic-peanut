"""tools/desktop_entry.py against temporary XDG dirs and a fake xdg-mime (never the real ones)."""

import configparser
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tidalviz.player.library import AUDIO_EXTENSIONS
from tools import desktop_entry as de

REPO = Path(__file__).resolve().parent.parent


FAKE_XDG_MIME = r"""\
import os, sys
cfg = os.path.join(os.environ["XDG_CONFIG_HOME"], "mimeapps.list")
args = sys.argv[1:]
open(os.environ["_LOG"], "a").write(" ".join(["xdg-mime"] + args) + "\n")

def read():
    entries, section = {}, ""
    for line in (open(cfg).read().splitlines() if os.path.exists(cfg) else []):
        if line.startswith("["):
            section = line.strip("[]")
        elif section == "Default Applications" and "=" in line:
            k, _, v = line.partition("=")
            entries[k] = v
    return entries

if args[:2] == ["query", "default"]:
    # Like the real one: also answers from the desktop files (here: always ours when asked to lie).
    print("cosmic-peanut.desktop" if os.environ.get("_QUERY_LIES") else read().get(args[2], "").split(";")[0])
elif args[0] == "default" and not os.environ.get("_DEFAULT_NOOP"):
    entries = read()
    entries[args[2]] = args[1] + ";"
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    open(cfg, "w").write("[Default Applications]\n" + "".join(f"{k}={v}\n" for k, v in entries.items()))
"""


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, str]:
    """XDG dirs under tmp and a PATH holding fake `uv`, `xdg-mime` and `update-desktop-database`.

    The fake xdg-mime keeps its defaults in the sandbox's mimeapps.list, which starts out naming
    another player for audio/mpeg."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    config = tmp_path / "config"
    config.mkdir()
    (config / "mimeapps.list").write_text("[Default Applications]\naudio/mpeg=vlc.desktop;\n")
    (bin_dir / "xdg-mime").write_text(f"#!{sys.executable}\n" + FAKE_XDG_MIME)
    (bin_dir / "update-desktop-database").write_text(
        f'#!/bin/sh\necho update-desktop-database "$@" >> {log}\n'
    )
    (bin_dir / "uv").write_text("#!/bin/sh\n")
    for f in bin_dir.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    return {
        "PATH": str(bin_dir),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(config),
        "HOME": str(tmp_path / "home"),
        "_LOG": str(log),
    }


def split_exec(value: str) -> list[str]:
    """Exec as the Desktop Entry spec reads it: the key's backslash escapes, then double-quoted
    arguments in which \\ \" \\` \\$ are escapes (not shlex: it leaves \\$ alone)."""
    line = value.replace("\\\\", "\\")
    out: list[str] = []
    cur, quoted, started, i = "", False, False, 0
    while i < len(line):
        c = line[i]
        if quoted and c == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 1
        elif c == '"':
            quoted, started = not quoted, True
        elif c == " " and not quoted:
            if started or cur:
                out.append(cur)
            cur, started = "", False
        else:
            cur += c
        i += 1
    if started or cur:
        out.append(cur)
    return out


def run(env: dict[str, str], *argv: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = de.main(list(argv), env=env)
    return code, capsys.readouterr().out


def entry_path(env: dict[str, str]) -> Path:
    return Path(env["XDG_DATA_HOME"]) / "applications" / "cosmic-peanut.desktop"


def parsed(path: Path) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser(interpolation=None)
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read(path, encoding="utf-8")
    return cp


# --- the table ------------------------------------------------------------------------------------------


def test_every_extension_the_player_opens_has_a_mime_type_and_no_stray_ones():
    assert set(de.MIME_BY_EXTENSION) == set(AUDIO_EXTENSIONS)
    assert all(types for types in de.MIME_BY_EXTENSION.values())


def test_the_table_has_the_names_shared_mime_info_reports_and_the_common_aliases():
    types = set(de.mime_types())
    assert {
        "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/flac", "audio/x-flac", "audio/x-wav",
        "audio/wav", "audio/vnd.wave", "audio/ogg", "audio/x-vorbis+ogg", "audio/x-opus+ogg",
        "audio/opus", "audio/x-ms-wma", "video/x-ms-asf", "audio/aac", "audio/x-aiff", "audio/aiff",
    } <= types  # fmt: skip
    assert len(de.mime_types()) == len(types)  # no duplicates


# --- the entry ------------------------------------------------------------------------------------------------


def test_install_writes_a_valid_desktop_entry_that_runs_this_checkout_from_anywhere(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    code, out = run(sandbox, "install", capsys=capsys)
    assert code == 0
    path = entry_path(sandbox)
    de_ = parsed(path)["Desktop Entry"]
    assert (de_["Name"], de_["Type"], de_["Terminal"]) == ("Cosmic Peanut", "Application", "false")
    assert (
        de_["Categories"] == "AudioVideo;Audio;Player;" and de_["Icon"] == "multimedia-audio-player"
    )
    assert de_["Comment"]
    mimes = de_["MimeType"]
    assert mimes.endswith(";") and set(mimes.rstrip(";").split(";")) == set(de.mime_types())
    args = split_exec(de_["Exec"])
    assert args == [
        str(Path(sandbox["PATH"]) / "uv"), "run", "--project", str(REPO), "--extra", "gtk", "tidalviz", "%F",
    ]  # fmt: skip
    assert str(path) in out
    assert "update-desktop-database" in Path(sandbox["_LOG"]).read_text()


def test_install_twice_leaves_the_same_file(sandbox, capsys: pytest.CaptureFixture[str]):
    run(sandbox, "install", capsys=capsys)
    first = entry_path(sandbox).read_bytes()
    assert run(sandbox, "install", capsys=capsys)[0] == 0
    assert entry_path(sandbox).read_bytes() == first
    assert len(list(entry_path(sandbox).parent.iterdir())) == 1  # no temp files left behind


def test_a_missing_update_desktop_database_is_not_an_error(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    (Path(sandbox["PATH"]) / "update-desktop-database").unlink()
    assert run(sandbox, "install", capsys=capsys)[0] == 0 and entry_path(sandbox).exists()


def test_install_without_uv_says_so_and_writes_nothing(sandbox, capsys: pytest.CaptureFixture[str]):
    (Path(sandbox["PATH"]) / "uv").unlink()
    code, out = run(sandbox, "install", capsys=capsys)
    assert code == 1 and "uv" in out and not entry_path(sandbox).exists()


def test_the_exec_line_is_quoted_per_the_desktop_entry_spec_for_awkward_paths():
    uv = "/home/me/.local/bin/uv"
    project = Path('/home/me/My Projects/cosmic "peanut" $HOME/100%/back\\slash')
    line = de.exec_line(uv, project)
    args = split_exec(line)
    assert args[:4] == [uv, "run", "--project", str(project).replace("%", "%%")]
    assert args[-1] == "%F"
    assert "100%%" in line  # a literal % is written %%


def test_xdg_data_home_defaults_to_dot_local_share_under_home(tmp_path: Path):
    env = {"HOME": str(tmp_path)}
    assert de.applications_dir(env) == tmp_path / ".local" / "share" / "applications"
    assert de.mimeapps_path(env) == tmp_path / ".config" / "mimeapps.list"


# --- defaults -----------------------------------------------------------------------------------------------------


def mimeapps(env: dict[str, str]) -> dict[str, str]:
    return de.explicit_defaults(env)


def test_set_default_backs_up_mimeapps_list_once_and_reports_each_change(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    cfg = Path(sandbox["XDG_CONFIG_HOME"])
    original = (cfg / "mimeapps.list").read_text()
    run(sandbox, "install", capsys=capsys)
    code, out = run(sandbox, "set-default", capsys=capsys)
    assert code == 0
    backups = list(cfg.glob("mimeapps.list.bak-*"))
    assert len(backups) == 1 and backups[0].read_text() == original
    assert str(backups[0]) in out
    assert "audio/mpeg: vlc.desktop -> cosmic-peanut.desktop" in out
    assert "audio/flac: (not set) -> cosmic-peanut.desktop" in out
    wanted = [t for t in de.mime_types() if t != "video/x-ms-asf"]
    assert all(mimeapps(sandbox)[t] == "cosmic-peanut.desktop;" for t in wanted)
    assert run(sandbox, "set-default", capsys=capsys)[0] == 0  # already ours: nothing to change
    assert len(list(cfg.glob("mimeapps.list.bak-*"))) == 1


def test_the_decision_comes_from_mimeapps_list_not_from_what_xdg_mime_query_answers(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    """The real `xdg-mime query default` falls back to the desktop files' MimeType lists, so once
    the entry is installed it can say cosmic-peanut.desktop for a type the user's file gives to
    another player (seen with a real xdg-mime): the defaults must still be written."""
    run(sandbox, "install", capsys=capsys)
    env = {**sandbox, "_QUERY_LIES": "1"}
    code, out = run(env, "set-default", capsys=capsys)
    assert code == 0 and "Already the default" not in out
    assert mimeapps(sandbox)["audio/mpeg"] == "cosmic-peanut.desktop;"


def test_a_type_that_does_not_take_is_listed_and_the_exit_code_is_nonzero(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    run(sandbox, "install", capsys=capsys)
    env = {**sandbox, "_DEFAULT_NOOP": "1"}  # xdg-mime says nothing and exits 0; the file disagrees
    code, out = run(env, "set-default", capsys=capsys)
    assert code == 1 and "did not take" in out and "audio/mpeg" in out and "audio/flac" in out


def test_video_x_ms_asf_is_in_the_entry_but_only_set_with_include_asf(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    run(sandbox, "install", capsys=capsys)
    assert "video/x-ms-asf" in parsed(entry_path(sandbox))["Desktop Entry"]["MimeType"]
    _, out = run(sandbox, "set-default", capsys=capsys)
    assert "video/x-ms-asf: skipped" in out and "ASF" in out
    assert "video/x-ms-asf" not in mimeapps(sandbox)
    code, out = run(sandbox, "set-default", "--include-asf", capsys=capsys)
    assert code == 0 and mimeapps(sandbox)["video/x-ms-asf"] == "cosmic-peanut.desktop;"


def test_set_default_before_install_asks_for_install_first(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    code, out = run(sandbox, "set-default", capsys=capsys)
    assert code == 1 and "install" in out
    assert mimeapps(sandbox) == {"audio/mpeg": "vlc.desktop;"}


def test_set_default_without_a_mimeapps_list_needs_no_backup(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    (Path(sandbox["XDG_CONFIG_HOME"]) / "mimeapps.list").unlink()
    run(sandbox, "install", capsys=capsys)
    code, out = run(sandbox, "set-default", capsys=capsys)
    assert code == 0 and "backup" not in out.lower()


def test_uninstall_removes_the_entry_and_only_prints_how_to_restore(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    cfg = Path(sandbox["XDG_CONFIG_HOME"])
    run(sandbox, "install", capsys=capsys)
    run(sandbox, "set-default", capsys=capsys)
    before = (cfg / "mimeapps.list").read_bytes()
    code, out = run(sandbox, "uninstall", capsys=capsys)
    assert code == 0 and not entry_path(sandbox).exists()
    assert (cfg / "mimeapps.list").read_bytes() == before  # not touched
    assert "mimeapps.list.bak-" in out and "restore" in out.lower() and "oldest backup" in out
    assert run(sandbox, "uninstall", capsys=capsys)[0] == 0  # nothing left: still fine


def test_the_printed_restore_command_quotes_awkward_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    cfg = tmp_path / "my config"
    cfg.mkdir()
    (cfg / "mimeapps.list.bak-20260101-000000").write_text("x")
    env = {
        "XDG_CONFIG_HOME": str(cfg),
        "XDG_DATA_HOME": str(tmp_path / "d"),
        "PATH": "",
        "HOME": str(tmp_path),
    }
    _, out = run(env, "uninstall", capsys=capsys)
    assert f"cp '{cfg}/mimeapps.list.bak-20260101-000000' '{cfg}/mimeapps.list'" in out


def test_install_refuses_a_checkout_path_with_a_newline(
    sandbox, monkeypatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(de, "CHECKOUT", Path("/tmp/evil\nExec=rm -rf"))
    code, out = run(sandbox, "install", capsys=capsys)
    assert code == 1 and "newline" in out and not entry_path(sandbox).exists()


def test_status_shows_installed_exec_and_the_explicit_default_per_type(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    code, out = run(sandbox, "status", capsys=capsys)
    assert code == 0 and "not installed" in out
    run(sandbox, "install", capsys=capsys)
    _, out = run(sandbox, "status", capsys=capsys)
    assert "installed" in out and "Exec=" in out and "audio/mpeg: vlc.desktop" in out
    assert all(t in out for t in de.mime_types())
    assert "audio/flac: (not chosen)" in out  # nothing explicit for it


def test_status_agrees_with_set_default_and_shows_what_is_only_registered(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    """`xdg-mime query default` also answers from the desktop files' own lists; with the lying
    fake it says cosmic-peanut for everything, but nothing was chosen in mimeapps.list."""
    run(sandbox, "install", capsys=capsys)
    _, out = run({**sandbox, "_QUERY_LIES": "1"}, "status", capsys=capsys)
    assert "audio/flac: (not chosen; registered: cosmic-peanut.desktop)" in out
    assert "audio/mpeg: vlc.desktop" in out  # the explicit entry wins
    run(sandbox, "set-default", capsys=capsys)
    _, out = run(sandbox, "status", capsys=capsys)
    assert "audio/flac: cosmic-peanut.desktop" in out and "(not chosen" not in out.replace(
        "video/x-ms-asf: (not chosen", ""
    )


def desktop_list(env: dict[str, str], name: str, body: str) -> Path:
    path = Path(env["XDG_CONFIG_HOME"]) / f"{name}-mimeapps.list"
    path.write_text(body)
    return path


def test_a_desktop_specific_list_that_gives_a_type_to_another_player_is_warned_about_not_edited(
    sandbox, capsys: pytest.CaptureFixture[str]
):
    env = {**sandbox, "XDG_CURRENT_DESKTOP": "KDE:Plasma"}
    body = "[Default Applications]\naudio/flac=vlc.desktop;\naudio/mpeg=cosmic-peanut.desktop;\n"
    path = desktop_list(env, "kde", body)
    other = desktop_list(env, "gnome", "[Default Applications]\naudio/ogg=vlc.desktop;\n")
    run(env, "install", capsys=capsys)
    code, out = run(env, "set-default", capsys=capsys)
    (line,) = [ln for ln in out.splitlines() if path.name in ln]
    assert code == 0 and "audio/flac" in line and "audio/mpeg" not in line  # ours: no complaint
    assert other.name not in out  # a desktop we are not running
    assert path.read_text() == body  # never edited
    _, status = run(env, "status", capsys=capsys)
    assert any(path.name in ln and "audio/flac" in ln for ln in status.splitlines())


def test_no_warning_without_a_conflicting_desktop_list(sandbox, capsys: pytest.CaptureFixture[str]):
    env = {**sandbox, "XDG_CURRENT_DESKTOP": "KDE"}
    run(env, "install", capsys=capsys)
    assert "-mimeapps.list" not in run(env, "set-default", capsys=capsys)[1]


# --- guard ------------------------------------------------------------------------------------------------------------


def test_the_tool_refuses_politely_off_linux(
    sandbox, monkeypatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(sys, "platform", "darwin")
    code, out = run(sandbox, "install", capsys=capsys)
    assert code == 1 and "Linux" in out and not entry_path(sandbox).exists()


def test_it_runs_as_a_module_with_a_usage_error_for_a_bad_command(sandbox):
    out = subprocess.run(
        [sys.executable, "-m", "tools.desktop_entry", "frobnicate"],
        env={**os.environ, **sandbox},
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    assert out.returncode == 2
