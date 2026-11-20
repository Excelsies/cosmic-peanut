"""Without GStreamer (no PyGObject: a plain `uv sync`, CI, macOS) nothing breaks and no player is
offered. `gi` is made unimportable in a subprocess; nothing is uninstalled."""

import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPT = textwrap.dedent(
    """
    import sys
    sys.modules["gi"] = None  # `import gi` now raises ImportError

    import tidalviz, tidalviz.app, tidalviz.host, tidalviz.capture, tidalviz.media
    import tidalviz.player, tidalviz.player.library, tidalviz.player.queue, tidalviz.player.store
    import tidalviz.player.scan, tidalviz.player.tags, tidalviz.player.sink
    assert "tidalviz.player.engine" not in sys.modules, "importing the app must not load the engine"

    from tidalviz.capture import player_available, player_source
    assert player_available() is False
    try:
        player_source(__import__("pathlib").Path(sys.argv[1]))
    except ValueError as e:
        print("refused:", e)
    else:
        raise SystemExit("player_source must refuse without GStreamer")

    from tidalviz.host import Host, make_source
    try:
        make_source("player", root=__import__("pathlib").Path(sys.argv[1]))
    except ValueError:
        pass
    else:
        raise SystemExit("make_source('player') must refuse")
    h = Host.__new__(Host)
    assert all(s["id"] != "player" for s in Host.sources(h, []))
    print("ok")
    """
)


def test_the_app_imports_and_offers_no_player_when_gi_cannot_be_imported(tmp_path: Path):
    out = subprocess.run(
        [sys.executable, "-I", "-c", SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("ok")
    assert "refused:" in out.stdout
