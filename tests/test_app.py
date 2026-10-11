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
