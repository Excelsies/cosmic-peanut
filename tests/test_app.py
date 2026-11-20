import os
import subprocess
import sys
from pathlib import Path

import pytest

from tidalviz.app import APP_ROOT, builtin_dirs, parse_args


def test_defaults():
    a = parse_args([])
    assert a.dev is None and a.source is None and a.bench is None and a.seconds == 60


def test_dev_folder_and_source(tmp_path: Path):
    a = parse_args(["--dev", str(tmp_path), "--source", "synthetic:demo"])
    assert a.dev == tmp_path and a.source == "synthetic:demo"


def test_bench(tmp_path: Path):
    a = parse_args(["--bench", "builtin/bars", "--seconds", "5", "--out", str(tmp_path / "r.json")])
    assert (a.bench, a.seconds, a.out) == ("builtin/bars", 5, tmp_path / "r.json")


def test_builtin_dirs_exist_with_manifests():
    dirs = builtin_dirs()
    assert [d.name for d in dirs] == ["builtin", "cosmic-peanut", "template"]
    assert all((d / "tidalviz.json").is_file() for d in dirs)


_BLAS_PROBE = (
    "import os, sys, {mod}; "
    "assert 'numpy' not in sys.modules; "
    "print(os.environ.get('OPENBLAS_NUM_THREADS'))"
)


@pytest.mark.parametrize("mod", ["tidalviz.app", "tools"])
@pytest.mark.parametrize(("given", "expected"), [(None, "1"), ("4", "4")])
def test_openblas_pool_is_single_threaded_before_numpy_loads(
    mod: str, given: str | None, expected: str
):
    env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
    if given is not None:
        env["OPENBLAS_NUM_THREADS"] = given
    out = subprocess.run(
        [sys.executable, "-c", _BLAS_PROBE.format(mod=mod)],
        env=env,
        cwd=APP_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == expected


# --- files on the command line -------------------------------------------------------------------------------------


def test_positional_paths_become_absolute_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    a = parse_args(["/music/a.mp3", "rel/b.flac", "dir/../c.ogg"])
    assert a.paths == [Path("/music/a.mp3"), tmp_path / "rel" / "b.flac", tmp_path / "c.ogg"]


def test_no_paths_is_an_empty_list():
    assert parse_args([]).paths == []


def test_file_uris_are_accepted_and_decoded():
    a = parse_args(["file:///music/a%20b.mp3", "file://localhost/music/c.mp3"])
    assert a.paths == [Path("/music/a b.mp3"), Path("/music/c.mp3")]


def test_uri_decoding_keeps_non_utf8_names_and_pins_file_colon_without_slashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    (got,) = parse_args(["file:///tmp/%FF%FEx.mp3"]).paths
    assert (
        os.fsencode(got) == b"/tmp/\xff\xfex.mp3"
    )  # the bytes survive, as the file system has them
    (plain,) = parse_args(["file:foo.mp3"]).paths  # not a URI: a file named `file:foo.mp3` here
    assert plain == tmp_path / "file:foo.mp3"


def test_a_nul_in_a_path_is_a_usage_error(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as e:
        parse_args(["file:///tmp/a%00b.mp3"])
    assert e.value.code == 2 and "not a usable path" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["http://example.com/a.mp3", "file://otherhost/a.mp3", "smb://x/y"])
def test_other_uris_are_a_usage_error(bad: str, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as e:
        parse_args([bad])
    assert e.value.code == 2 and "not a local file" in capsys.readouterr().err


def test_paths_with_a_bench_run_or_another_source_are_a_usage_error():
    for argv in (["--bench", "builtin/bars", "/a.mp3"], ["--source", "system", "/a.mp3"]):
        with pytest.raises(SystemExit) as e:
            parse_args(argv)
        assert e.value.code == 2
    assert parse_args(["--source", "player", "/a.mp3"]).paths == [Path("/a.mp3")]


def test_files_select_the_player_for_this_launch_when_it_can_run(monkeypatch: pytest.MonkeyPatch):
    from tidalviz import app

    monkeypatch.setattr(app, "player_available", lambda: True)
    assert app.launch_source(parse_args(["/a.mp3"])) == "player"
    assert app.launch_source(parse_args([])) is None
    assert app.launch_source(parse_args(["--source", "player"])) == "player"
    monkeypatch.setattr(app, "player_available", lambda: False)
    assert app.launch_source(parse_args(["/a.mp3"])) is None  # start as usual; the host says why


def test_a_second_launch_hands_its_files_to_the_running_instance_and_exits_without_a_window(
    tmp_path: Path,
):
    import threading
    import time

    from tidalviz.instance import Env, acquire

    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    data = tmp_path / "data"
    first = acquire(data, None, Env(run, tmp_path / "t", os.getuid()))
    assert first.listener is not None
    got: list[list[Path]] = []
    first.listener.serve(got.append)
    f = tmp_path / "my song.mp3"
    out = subprocess.run(
        [sys.executable, "-m", "tidalviz.app", "--home", str(data), str(f)],
        env={**os.environ, "XDG_RUNTIME_DIR": str(run)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    end = time.monotonic() + 3
    while not got and time.monotonic() < end:
        time.sleep(0.01)
    first.listener.close()
    assert out.returncode == 0, out.stderr
    assert got == [[f]]
    assert threading.active_count() >= 1
