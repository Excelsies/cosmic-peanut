"""Make Cosmic Peanut an audio player of the Linux desktop ("Open with", default player).

    uv run python -m tools.desktop_entry install      # write the .desktop file (rerunnable)
    uv run python -m tools.desktop_entry set-default  # make it the default for the audio types
    uv run python -m tools.desktop_entry status       # what is installed, who is the default
    uv run python -m tools.desktop_entry uninstall    # remove the .desktop file

`set-default` backs up ``mimeapps.list`` first and prints the backup's path; `uninstall` never
touches it. Linux only. Every path is computed at run time from the environment and this file's
location.
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from tidalviz.player.library import AUDIO_EXTENSIONS

DESKTOP_FILE = "cosmic-peanut.desktop"
CHECKOUT = Path(__file__).resolve().parents[1]

# What shared-mime-info reports for each extension (checked with `xdg-mime query filetype` on real
# MP3, M4A, WMA and WAV files and on files made with GStreamer: audio/mpeg, audio/x-m4a,
# video/x-ms-asf for WMA, audio/x-wav, audio/flac, audio/ogg), plus the aliases other tools use.
# video/x-ms-asf is what a real WMA file is reported as, so it also claims ASF video.
MIME_BY_EXTENSION: dict[str, tuple[str, ...]] = {
    ".aac": ("audio/aac", "audio/x-aac"),
    ".aif": ("audio/x-aiff", "audio/aiff"),
    ".aiff": ("audio/x-aiff", "audio/aiff"),
    ".flac": ("audio/flac", "audio/x-flac"),
    ".m4a": ("audio/mp4", "audio/x-m4a", "audio/m4a"),
    ".mp3": ("audio/mpeg", "audio/mp3"),
    ".oga": ("audio/ogg", "audio/x-vorbis+ogg"),
    ".ogg": ("audio/ogg", "audio/x-vorbis+ogg"),
    ".opus": ("audio/x-opus+ogg", "audio/opus"),
    ".wav": ("audio/x-wav", "audio/wav", "audio/vnd.wave"),
    ".wma": ("audio/x-ms-wma", "video/x-ms-asf"),
}
assert set(MIME_BY_EXTENSION) == set(AUDIO_EXTENSIONS), "update MIME_BY_EXTENSION with the player"


def mime_types() -> list[str]:
    """Every type once, in the table's order."""
    return list(dict.fromkeys(t for types in MIME_BY_EXTENSION.values() for t in types))


# --- paths -----------------------------------------------------------------------------------------


def _home(env: Mapping[str, str]) -> Path:
    return Path(env["HOME"]) if env.get("HOME") else Path.home()


def applications_dir(env: Mapping[str, str]) -> Path:
    data = env.get("XDG_DATA_HOME", "")
    base = Path(data) if data and os.path.isabs(data) else _home(env) / ".local" / "share"
    return base / "applications"


def mimeapps_path(env: Mapping[str, str]) -> Path:
    cfg = env.get("XDG_CONFIG_HOME", "")
    base = Path(cfg) if cfg and os.path.isabs(cfg) else _home(env) / ".config"
    return base / "mimeapps.list"


# --- the entry -------------------------------------------------------------------------------------

_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")


def _quote(arg: str) -> str:
    """One Exec argument as the Desktop Entry spec wants it (a literal % is %%)."""
    arg = arg.replace("%", "%%")
    if not arg or not _RESERVED & set(arg):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def exec_line(uv: str, project: Path) -> str:
    """The Exec value as written in the file: argument quoting, then the key's own backslash escaping."""
    args = [uv, "run", "--project", str(project), "--extra", "gtk", "tidalviz"]
    return " ".join(_quote(a) for a in args).replace("\\", "\\\\") + " %F"


def render_entry(uv: str, project: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Cosmic Peanut\n"
        "Comment=Play audio files with a live music visualizer\n"
        f"Exec={exec_line(uv, project)}\n"
        "Icon=multimedia-audio-player\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;Player;\n"
        f"MimeType={';'.join(mime_types())};\n"
    )


# --- commands --------------------------------------------------------------------------------------


def _out(text: str = "") -> None:
    print(text)


def _run(cmd: Sequence[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=dict(env), check=False)


def _tool(name: str, env: Mapping[str, str]) -> str | None:
    return shutil.which(name, path=env.get("PATH"))


def _default_of(xdg_mime: str, mime: str, env: Mapping[str, str]) -> str:
    return _run([xdg_mime, "query", "default", mime], env).stdout.strip() or "(none)"


def _update_database(directory: Path, env: Mapping[str, str]) -> None:
    tool = _tool("update-desktop-database", env)
    if tool is not None:
        _run([tool, str(directory)], env)


def install(env: Mapping[str, str]) -> int:
    uv = _tool("uv", env)
    if uv is None:
        _out("uv was not found on PATH; install it first (https://docs.astral.sh/uv/).")
        return 1
    if "\n" in str(CHECKOUT) or "\r" in str(CHECKOUT):
        _out("This checkout's path contains a newline, which a desktop entry cannot hold.")
        return 1
    directory = applications_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / DESKTOP_FILE
    fd, tmp = tempfile.mkstemp(prefix=f".{DESKTOP_FILE}.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(render_entry(str(Path(uv).absolute()), CHECKOUT))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _update_database(directory, env)
    _out(f"Installed {target}")
    _out("Run `set-default` to make it the default player for audio files.")
    return 0


SKIPPED_BY_DEFAULT = {
    "video/x-ms-asf": (
        "xdg-mime reports a .wma as this, but it also covers ASF/WMV video; gio reports a .wma "
        "as audio/x-ms-wma. Use --include-asf to claim it too."
    )
}


def read_defaults(path: Path) -> dict[str, str]:
    """The ``[Default Applications]`` entries of one mimeapps.list, as written (missing file or
    key: absent)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    section = ""
    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Default Applications" and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def explicit_defaults(env: Mapping[str, str]) -> dict[str, str]:
    """What mimeapps.list says. `xdg-mime query default` is no help for "did the user choose?": it
    also answers from the desktop files' own MimeType lists."""
    return read_defaults(mimeapps_path(env))


def desktop_lists(env: Mapping[str, str]) -> list[Path]:
    """``<desktop>-mimeapps.list`` files for the running desktop(s), which the spec reads first."""
    base = mimeapps_path(env).parent
    names = [n.lower() for n in env.get("XDG_CURRENT_DESKTOP", "").split(":") if n]
    return [p for n in names if (p := base / f"{n}-mimeapps.list").exists()]


def desktop_conflicts(env: Mapping[str, str], types: Sequence[str]) -> list[str]:
    """One warning per desktop-specific list that gives one of ``types`` to another player."""
    out: list[str] = []
    for path in desktop_lists(env):
        entries = read_defaults(path)
        theirs = [t for t in types if t in entries and not _is_ours(entries[t])]
        if theirs:
            out.append(
                f"Warning: {path.name} gives {', '.join(theirs)} to another player; it is read before "
                f"mimeapps.list, so Cosmic Peanut will not be used for them. It was not edited."
            )
    return out


def _is_ours(entry: str | None) -> bool:
    return entry is not None and entry.split(";", 1)[0].strip() == DESKTOP_FILE


def set_default(env: Mapping[str, str], include_asf: bool = False) -> int:
    target = applications_dir(env) / DESKTOP_FILE
    if not target.exists():
        _out(f"{target} does not exist: run `install` first.")
        return 1
    xdg_mime = _tool("xdg-mime", env)
    if xdg_mime is None:
        _out("xdg-mime was not found on PATH (package xdg-utils).")
        return 1
    wanted = [t for t in mime_types() if include_asf or t not in SKIPPED_BY_DEFAULT]
    for mime in mime_types():
        if mime not in wanted:
            _out(f"{mime}: skipped: {SKIPPED_BY_DEFAULT[mime]}")
    previous = explicit_defaults(env)
    todo = [t for t in wanted if not _is_ours(previous.get(t))]
    if not todo:
        _out("Already the default for every audio type in mimeapps.list; nothing to change.")
        return 0
    mimeapps = mimeapps_path(env)
    if mimeapps.exists():
        backup = mimeapps.with_name(f"{mimeapps.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(mimeapps, backup)
        _out(f"Backed up {mimeapps} to {backup}")
    for mime in todo:
        result = _run([xdg_mime, "default", DESKTOP_FILE, mime], env)
        if result.returncode != 0:
            _out(f"{mime}: xdg-mime failed ({result.stderr.strip() or result.returncode})")
    now = explicit_defaults(env)
    for warning in desktop_conflicts(env, wanted):
        _out(warning)
    for mime in todo:
        was = previous.get(mime, "(not set)").rstrip(";") or "(not set)"
        _out(f"{mime}: {was} -> {now.get(mime, '(not set)').rstrip(';')}")
    failed = [t for t in todo if not _is_ours(now.get(t))]
    if failed:  # `xdg-mime default` may say it worked and the file disagree
        _out(
            f"These types did not take (mimeapps.list does not name {DESKTOP_FILE}): {', '.join(failed)}"
        )
        return 1
    return 0


def uninstall(env: Mapping[str, str]) -> int:
    target = applications_dir(env) / DESKTOP_FILE
    if target.exists():
        target.unlink()
        _update_database(target.parent, env)
        _out(f"Removed {target}")
    else:
        _out(f"{target} is not installed.")
    mimeapps = mimeapps_path(env)
    backups = sorted(mimeapps.parent.glob(f"{mimeapps.name}.bak-*"))
    _out(f"{mimeapps} was not touched.")
    if backups:
        _out(
            "To restore the defaults from before `set-default` (the oldest backup): "
            f"cp {shlex.quote(str(backups[0]))} {shlex.quote(str(mimeapps))}"
        )
    else:
        _out("To restore the defaults, pick another player for each type in your file manager.")
    return 0


def status(env: Mapping[str, str]) -> int:
    target = applications_dir(env) / DESKTOP_FILE
    if target.exists():
        exec_ = next(
            (
                line
                for line in target.read_text(encoding="utf-8").splitlines()
                if line.startswith("Exec=")
            ),
            "Exec=?",
        )
        _out(f"installed: {target}")
        _out(exec_)
    else:
        _out(f"not installed ({target})")
    xdg_mime = _tool("xdg-mime", env)
    if xdg_mime is None:
        _out("xdg-mime not found: cannot show the default handlers.")
        return 0
    explicit = explicit_defaults(env)
    for mime in mime_types():
        if mime in explicit:
            _out(f"{mime}: {explicit[mime].rstrip(';')}")
        else:  # only registered by desktop files: not a choice anyone made
            registered = _default_of(xdg_mime, mime, env)
            note = "" if registered == "(none)" else f"; registered: {registered}"
            _out(f"{mime}: (not chosen{note})")
    for warning in desktop_conflicts(env, mime_types()):
        _out(warning)
    return 0


COMMANDS = {
    "install": install,
    "set-default": set_default,
    "uninstall": uninstall,
    "status": status,
}


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.desktop_entry", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument(
        "--include-asf",
        action="store_true",
        help="set-default: also claim video/x-ms-asf (WMA as xdg-mime reports it, and ASF video)",
    )
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        _out("tools.desktop_entry only supports Linux (desktop entries and xdg-mime).")
        return 1
    run_env = os.environ if env is None else env
    if args.command == "set-default":
        return set_default(run_env, include_asf=args.include_asf)
    return COMMANDS[args.command](run_env)


if __name__ == "__main__":
    sys.exit(main())
