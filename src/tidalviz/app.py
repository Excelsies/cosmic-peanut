"""Entry point: `uv run tidalviz [--dev PATH] [--source ID] [--bench KEY --seconds N]`.

Threads (PRD): Cocoa/pywebview on the main thread, the asyncio loop (servers, control) on its
own thread, plus the pipeline's capture and analysis threads.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from tidalviz.paths import default_root

# Before numpy loads: OpenBLAS would start one idle worker per core, and every BLAS call here is
# far below its threading threshold.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

if TYPE_CHECKING:
    from tidalviz.host import Host
    from tidalviz.window import PyWebviewWindow

log = logging.getLogger("tidalviz")

APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def builtin_dirs() -> list[Path]:
    plugins = APP_ROOT / "plugins"
    return [plugins / "builtin", plugins / "cosmic-peanut", plugins / "template"]


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="tidalviz", description=__doc__)
    p.add_argument("--dev", type=Path, help="plugin folder to hot-reload (enables Web Inspector)")
    p.add_argument(
        "--source", help="system | app:<pid> | synthetic:<kind> | player (this launch only)"
    )
    p.add_argument("--bench", metavar="KEY", help="benchmark a visualizer and write a report")
    p.add_argument("--seconds", type=int, default=60, help="benchmark length")
    p.add_argument("--out", type=Path, default=Path("bench.json"), help="benchmark report path")
    p.add_argument(
        "--home",
        type=Path,
        help="data dir (default: ~/Library/Application Support; Linux: $XDG_DATA_HOME or ~/.local/share)",
    )
    p.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="audio files or folders (or file:// URIs) to play; hands them to a running instance",
    )
    args = p.parse_args(argv)
    try:
        args.paths = [local_path(raw) for raw in args.paths]
    except ValueError as e:
        p.error(str(e))
    if args.paths and args.bench is not None:
        p.error("files cannot be given with --bench")
    if args.paths and args.source not in (None, "player"):
        p.error("files are played by the player source; drop --source or use --source player")
    return args


def local_path(raw: str) -> Path:
    """An absolute path from a command-line argument (a path or a file:// URI), taken against the
    working directory (only ``file://`` is a URI: ``file:foo.mp3`` is a file named that) of the process that was launched, before anything is handed over."""
    if "://" in raw:
        uri = urlparse(raw)
        if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
            raise ValueError(f"not a local file: {raw!r}")
        raw = unquote(uri.path, errors="surrogateescape")  # a non-UTF-8 name round-trips
    if "\0" in raw:
        raise ValueError(f"not a usable path: {raw!r}")
    return Path(os.path.abspath(raw))


def player_available() -> bool:
    from tidalviz.capture import player_available as available  # numpy loads after OPENBLAS is set

    return available()


def launch_source(args: argparse.Namespace) -> str | None:
    """The source for this launch: the player when files were given (and it can run here)."""
    if args.source is None and args.paths and player_available():
        return "player"
    return args.source


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    root = args.home or default_root()

    listener = None
    if args.bench is None:  # a benchmark run must not share or take over the user's instance
        from tidalviz.instance import acquire

        got = acquire(root, args.paths or None)
        if got.handed_over:
            log.info("handed %d path(s) to the running instance", len(args.paths))
            return
        listener = got.listener

    import webview  # type: ignore[import-untyped]

    from tidalviz.host import Host

    if sys.platform == "darwin":
        from tidalviz.window import PyWebviewWindow as Window
    else:
        from tidalviz.window_gtk import GtkWindow as Window
        from tidalviz.window_gtk import configure_gl_environment

        configure_gl_environment()  # before GTK/WebKit start; the web process inherits it

    dev = args.dev is not None
    window = Window()
    host = Host(
        root=root,
        builtin_dirs=builtin_dirs(),
        window=window,
        source_id=launch_source(args),
        dev=dev,
        bench_key=args.bench,
        watch_hyprland=sys.platform == "linux",  # WebKitGTK never reports hidden there
    )
    if args.dev is not None:
        host.use_dev_folder(args.dev)

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="tidalviz-asyncio", daemon=True).start()
    asyncio.run_coroutine_threadsafe(host.start(), loop).result(timeout=10)
    log.info("shell %s  plugins %s", host.servers.shell_origin, host.servers.plugin_origin)

    def open_paths(paths: Sequence[Path]) -> None:
        asyncio.run_coroutine_threadsafe(host.open_paths(paths), loop).result(timeout=60)

    if args.paths:
        threading.Thread(
            target=open_paths, args=(args.paths,), name="tidalviz-open", daemon=True
        ).start()
    if listener is not None:
        listener.serve(lambda paths: open_paths(paths))

    win = webview.create_window(  # pyright: ignore[reportUnknownMemberType]
        "Tidalviz",
        host.servers.shell_url,
        width=1280,
        height=720,
        min_size=(320, 180),
        background_color="#000000",
        # Always transparent-capable; the shell paints a black backdrop unless the user turns on
        # "Transparent background". Frameless: the top bar is the drag region (easy_drag would
        # steal drags meant for the visual, e.g. Cosmic Peanut's orbit).
        transparent=True,
        frameless=host.settings.data["borderless"],
        on_top=host.settings.data["onTop"],
        easy_drag=False,
    )
    window.attach(win, borderless=host.settings.data["borderless"])
    if args.bench is not None:
        threading.Thread(target=_finish_bench, args=(host, window, args), daemon=True).start()
    try:
        webview.start(debug=dev)  # pyright: ignore[reportUnknownMemberType]
    finally:
        if listener is not None:
            listener.close()
        asyncio.run_coroutine_threadsafe(host.stop(), loop).result(timeout=10)
        loop.call_soon_threadsafe(loop.stop)


def _finish_bench(host: "Host", window: "PyWebviewWindow", args: argparse.Namespace) -> None:
    import time

    time.sleep(args.seconds)
    assert host.bench is not None
    report = host.bench.report()
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    log.info("bench report written to %s: %s", args.out, report)
    window.quit()


if __name__ == "__main__":
    main()
