"""Throwaway mock of the Tidalviz host for developing the shell (not shipped).

Serves the shell on one port and a fake plugin on a second port, speaks the control protocol
(docs/protocols.md §3-4) with a fake registry of two visualizers, and streams synthetic binary v1
frames at ~94 Hz.

    uv run python web/shell/dev/mock_host.py [--port 8765] [--plugin-port 8766]

Then open the printed URL. Test hooks (mock only): GET /_log returns every message the shell
sent; POST /_send broadcasts a JSON host message to the shell.

The "Files" source is a pretend file player (mock_player.py): pick it in the source menu to get
the transport row, the queue and a clock. `--tracks N` sizes its queue (up to 2000), `--source
player` starts on it. Nothing plays; there is no audio.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import secrets
import time
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from mock_player import MockPlayer, sample_tracks

from tidalviz.frame import new_frame
from tidalviz.transport.frame import encode

SHELL_DIR = Path(__file__).resolve().parent.parent
HZ = 48000 / 512


def registry(plugin_origin: str) -> list[dict[str, Any]]:
    def viz(vid: str, name: str, renderer: str, params: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "key": f"mock/{vid}",
            "repo": "mock",
            "id": vid,
            "name": name,
            "description": f"Fake {name} visualizer",
            "author": "mock",
            "renderer": renderer,
            "thumbnailUrl": None,
            "params": params,
            "values": {},
            "disabled": False,
            "dev": vid == "orbit",
            "entryUrl": f"{plugin_origin}/r/mock/{vid}.js",
            "pageUrl": f"{plugin_origin}/v/mock/{vid}/",
        }

    hue = {
        "id": "hue",
        "type": "number",
        "label": "Hue",
        "min": 0,
        "max": 360,
        "step": 1,
        "default": 220,
    }
    return [
        viz(
            "bars",
            "Pulse Bars",
            "2d",
            [hue, {"id": "mirror", "type": "boolean", "label": "Mirror", "default": False}],
        ),
        viz(
            "orbit",
            "Orbit",
            "three",
            [
                {**hue, "default": 300},
                {
                    "id": "shape",
                    "type": "select",
                    "label": "Shape",
                    "options": ["dots", "rings"],
                    "default": "dots",
                },
                {"id": "tint", "type": "color", "label": "Tint", "default": "#88aaff"},
                {"id": "crash", "type": "boolean", "label": "Crash (test)", "default": False},
            ],
        ),
    ]


class MockHost:
    def __init__(
        self, shell_port: int, plugin_port: int, tracks: int = 12, source: str = "system"
    ) -> None:
        self.shell_port = shell_port
        self.plugin_port = plugin_port
        self.token = secrets.token_urlsafe(8)
        self.received: list[dict[str, Any]] = []
        self.clients: set[web.WebSocketResponse] = set()
        self.settings: dict[str, Any] = {"quality": "auto", "reduceFlashing": True}
        self.active: str | None = None
        self.sources = [
            {"id": "system", "name": "All audio"},
            {"id": "app:501", "name": "Music"},
            {"id": "player", "name": "Files"},
        ]
        self.active_source = source
        self.player = MockPlayer(sample_tracks(tracks))
        self.player_sig: tuple[Any, ...] | None = None
        self.player_revision: int | None = None
        self.player_task: asyncio.Task[None] | None = None
        # The pretend native dialog: open for `dialog_s`, then picks files (or finds no audio).
        self.dialog_s = 0.05
        self.dialog_finds_audio = True
        self.dialog_open = False
        self.dialog_tasks: set[asyncio.Task[None]] = set()
        self.visualizers: list[dict[str, Any]] = []
        self.runners: list[web.AppRunner] = []
        self.frame_task: asyncio.Task[None] | None = None

    @property
    def plugin_origin(self) -> str:
        return f"http://127.0.0.1:{self.plugin_port}"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.shell_port}/?token={self.token}"

    # ---- shell server -------------------------------------------------------------------------
    def csp(self) -> str:
        p = self.plugin_origin
        return (
            f"default-src 'self'; img-src 'self' {p} data:; frame-src {p}; "
            f"connect-src 'self' ws://127.0.0.1:{self.shell_port}; style-src 'self' 'unsafe-inline'"
        )

    async def index(self, _req: web.Request) -> web.StreamResponse:
        resp = web.FileResponse(SHELL_DIR / "index.html")
        resp.headers["Content-Security-Policy"] = self.csp()
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def static(self, req: web.Request) -> web.StreamResponse:
        path = (SHELL_DIR / req.match_info["path"]).resolve()
        if SHELL_DIR not in path.parents or not path.is_file():
            raise web.HTTPNotFound()
        resp = web.FileResponse(path)
        resp.headers["Content-Security-Policy"] = self.csp()
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def config(self, req: web.Request) -> web.StreamResponse:
        if req.query.get("token") != self.token:
            raise web.HTTPForbidden()
        return web.json_response(
            {"token": self.token, "pluginOrigin": self.plugin_origin, "dev": True}
        )

    @property
    def transport(self) -> bool:
        return self.active_source == "player"

    def hello(self) -> dict[str, Any]:
        self.player_sig, self.player_revision = self.player.signature(), self.player.revision
        return {
            "type": "hello",
            "version": 1,
            "pluginOrigin": self.plugin_origin,
            "visualizers": self.visualizers,
            "repos": [
                {"repo": "mock", "url": "https://example.com/mock.git", "path": None, "commit": "0123456789ab",
                 "previous": "ba9876543210", "dev": False, "builtin": False},
            ],
            "settings": self.settings,
            "sources": self.sources,
            "activeSource": self.active_source,
            "active": self.active,
            "dev": True,
            "transport": self.transport,
            "nowPlaying": self.player.now_playing() if self.transport else None,
            "queue": self.player.queue_fields() if self.transport else None,
        }  # fmt: skip

    async def broadcast(self, msg: dict[str, Any]) -> None:
        for ws in list(self.clients):
            await ws.send_str(json.dumps(msg))

    async def ws(self, req: web.Request) -> web.StreamResponse:
        if req.query.get("token") != self.token:
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        self.clients.add(ws)
        await ws.send_str(json.dumps(self.hello()))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                self.received.append(data)
                await self.on_message(data)
        finally:
            self.clients.discard(ws)
        return ws

    async def on_message(self, m: dict[str, Any]) -> None:
        t = m.get("type")
        if t == "select":
            self.active = m["key"]
        elif t == "params":
            for v in self.visualizers:
                if v["key"] == m["key"]:
                    v["values"] = m["values"]
        elif t == "settings":
            self.settings.update({k: v for k, v in m.items() if k != "type"})
        elif t == "setSource":
            self.active_source = m["id"]
            await self.broadcast(
                {
                    "type": "sources",
                    "sources": self.sources,
                    "active": self.active_source,
                    "transport": self.transport,
                }
            )
            await self.announce(force=True)
        elif t == "install":
            await self.broadcast({"type": "installPrompt", "id": "i1", "url": m["url"], "commit": "feedface1234",
                                  "visualizers": [{"id": "new", "name": "New Thing"}]})  # fmt: skip
        elif t == "installConfirm":
            await self.broadcast({"type": "installResult", "id": m["id"], "ok": bool(m["accept"])})
        elif t in ("transport", "playerOpen", "playerSelect", "playerClear"):
            await self.on_player_message(m)
        elif t == "pluginError":
            await self.broadcast(
                {"type": "status", "level": "error", "text": f"{m['key']}: {m['message']}"}
            )

    # ---- the pretend file player (mock_player.py) ----------------------------------------------
    async def on_player_message(self, m: dict[str, Any]) -> None:
        if not self.transport:
            return  # like the host: ignored unless the active source has a transport
        match m["type"]:
            case "transport":
                self.player.transport(m["action"], m.get("value"), m.get("serial"))
                await self.send_skip_status()
                # One `nowPlaying` follows every seek, even one that changed nothing or a paused one.
                await self.announce(now_playing=m["action"] == "seek")
                return
            case "playerOpen":
                if not self.dialog_open:  # a second open while the dialog is up is ignored
                    self.dialog_open = True
                    task = asyncio.create_task(self.dialog(m["kind"], m["replace"]))
                    self.dialog_tasks.add(task)
                    task.add_done_callback(self.dialog_tasks.discard)
                return
            case "playerSelect":
                text = self.player.select(m["index"])
                if text:
                    await self.broadcast({"type": "status", "level": "warn", "text": text})
            case "playerClear":
                self.player.clear()
        await self.announce()

    async def send_skip_status(self) -> None:
        if text := self.player.take_status():
            await self.broadcast({"type": "status", "level": "warn", "text": text})

    async def dialog(self, kind: str, replace: bool) -> None:
        try:
            await asyncio.sleep(self.dialog_s)
            if not self.dialog_finds_audio:
                text = "No audio files found"
                await self.broadcast({"type": "status", "level": "info", "text": text})
                return
            self.player.open(kind, replace)
            await self.announce()
        finally:
            self.dialog_open = False

    async def announce(self, force: bool = False, now_playing: bool = False) -> None:
        """Send what changed, as the real host does: `nowPlaying` unless only the position moved
        (the position alone changes every second by itself; a seek asks for one explicitly),
        `queue` when its revision moved."""
        if not self.transport:
            return
        sig = self.player.signature()
        if force or now_playing or sig != self.player_sig:
            self.player_sig = sig
            await self.broadcast({"type": "nowPlaying", **self.player.now_playing()})
        if force or self.player.revision != self.player_revision:
            self.player_revision = self.player.revision
            await self.broadcast({"type": "queue", **self.player.queue_fields()})

    async def player_loop(self) -> None:
        """Once a second while playing a position correction; a few tags found every 250 ms."""
        n = 0
        while True:
            await asyncio.sleep(0.25)
            n += 1
            if not self.transport:
                continue
            moved = self.player.end_of_track()
            await self.send_skip_status()
            if self.player.reveal():
                await self.announce()
            if moved:
                await self.announce()
            elif n % 4 == 0 and self.player.playing:
                await self.broadcast({"type": "nowPlaying", **self.player.now_playing()})

    async def get_log(self, _req: web.Request) -> web.StreamResponse:
        return web.json_response(self.received)

    async def post_send(self, req: web.Request) -> web.StreamResponse:
        await self.broadcast(await req.json())
        return web.json_response({"ok": True})

    # ---- plugin server ------------------------------------------------------------------------
    async def plugin_page(self, req: web.Request) -> web.StreamResponse:
        kind = req.match_info["id"]
        html = (
            f'<!doctype html><html data-kind="{kind}"><meta charset="utf-8">'
            "<style>html,body{margin:0;height:100%;background:#05050c;overflow:hidden}"
            "canvas{display:block;width:100%;height:100%}</style>"
            '<canvas></canvas><script type="module" src="/fake_plugin.js"></script></html>'
        )
        return web.Response(text=html, content_type="text/html", headers=self.plugin_headers())

    async def plugin_js(self, _req: web.Request) -> web.StreamResponse:
        resp = web.FileResponse(Path(__file__).with_name("fake_plugin.js"))
        resp.headers.update(self.plugin_headers())
        resp.content_type = "text/javascript"
        return resp

    def plugin_headers(self) -> dict[str, str]:
        p = self.plugin_origin
        return {
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
            "Content-Security-Policy": f"default-src 'none'; script-src {p}; style-src 'unsafe-inline'",
        }

    # ---- frames -------------------------------------------------------------------------------
    async def frames(self) -> None:
        frame = new_frame(stereo=False)
        frame.sample_rate = 48000.0
        i = 0
        period = 1 / HZ
        nxt = time.perf_counter()
        while True:
            t = i * period
            frame.index = i
            frame.host_time = time.monotonic()
            frame.onset = i % 47 == 0
            frame.silent = False
            level = 0.5 + 0.5 * math.sin(t * 2.1)
            frame.scalars[0] = 0.2 + 0.3 * level
            for b in range(frame.bands.shape[0]):
                frame.bands[b] = (
                    max(0.0, 0.5 + 0.45 * math.sin(t * 3 + b * 0.35)) * (1 - b / 90) * level
                )
            data = encode(frame)
            for ws in list(self.clients):
                if not ws.closed:
                    await ws.send_bytes(data)
            i += 1
            nxt += period
            await asyncio.sleep(max(0.0, nxt - time.perf_counter()))

    # ---- lifecycle ----------------------------------------------------------------------------
    async def start(self) -> None:
        plugin = web.Application()
        plugin.router.add_get("/v/{repo}/{id}/", self.plugin_page)
        plugin.router.add_get("/fake_plugin.js", self.plugin_js)
        prunner = web.AppRunner(plugin)
        await prunner.setup()
        psite = web.TCPSite(prunner, "127.0.0.1", self.plugin_port)
        await psite.start()
        self.plugin_port = prunner.addresses[0][1]

        shell = web.Application()
        shell.router.add_get("/", self.index)
        shell.router.add_get("/shell/{path:.+}", self.static)
        shell.router.add_get("/config.json", self.config)
        shell.router.add_get("/ws", self.ws)
        shell.router.add_get("/_log", self.get_log)
        shell.router.add_post("/_send", self.post_send)
        srunner = web.AppRunner(shell)
        await srunner.setup()
        ssite = web.TCPSite(srunner, "127.0.0.1", self.shell_port)
        await ssite.start()
        self.shell_port = srunner.addresses[0][1]

        self.runners = [prunner, srunner]
        self.visualizers = registry(self.plugin_origin)
        self.frame_task = asyncio.create_task(self.frames())
        self.player_task = asyncio.create_task(self.player_loop())

    async def stop(self) -> None:
        for task in (self.frame_task, self.player_task, *self.dialog_tasks):
            if task:
                task.cancel()
        for ws in list(self.clients):
            await ws.close()
        for r in self.runners:
            await r.cleanup()


async def _main(port: int, plugin_port: int, tracks: int, source: str) -> None:
    host = MockHost(port, plugin_port, tracks, source)
    await host.start()
    print(f"Mock host: {host.url}  (plugins on {host.plugin_origin})", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await host.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--plugin-port", type=int, default=0)
    ap.add_argument(
        "--tracks", type=int, default=12, help="entries in the pretend file queue (max 2000)"
    )
    ap.add_argument("--source", default="system", choices=["system", "player"])
    a = ap.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main(a.port, a.plugin_port, min(a.tracks, 2000), a.source))
