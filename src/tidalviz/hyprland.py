"""Hyprland window visibility (Linux): is any window of this process on a monitor?

WebKitGTK never sets `document.visibilityState` to "hidden" on Hyprland (GTK3 has no suspended
state), so a window on a workspace no monitor shows keeps drawing every frame. The host asks
this module instead and tells the shell (`windowVisibility`, docs/protocols.md §4). Every
doubt reads as visible: a wrong "hidden" would freeze a visualizer the user is watching.
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

log = logging.getLogger(__name__)

# socket2 events after which our window may have appeared on or left a monitor. Deliberately
# not activewindow/windowtitle (several per second while a terminal title animates), fullscreen
# or changefloatingmode (they don't move a window between workspaces).
EVENTS = frozenset(
    {
        "workspace", "workspacev2", "focusedmon", "focusedmonv2", "activespecial",
        "activespecialv2", "moveworkspace", "moveworkspacev2", "movewindow", "movewindowv2",
        "openwindow", "closewindow", "pin", "monitoradded", "monitoraddedv2", "monitorremoved",
        "monitorremovedv2", "screencast",
    }
)  # fmt: skip
DEBOUNCE_S = 0.075  # a workspace switch emits 2-6 events at once: one look after the burst
BACKOFF_S = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0)  # reconnect delays; the last one repeats
STABLE_S = 5.0  # an event connection up this long was healthy: backoff and warning start over
TIMEOUT_S = 2.0  # one request (connect, send, read to EOF)
MAX_REPLY = 4 * 1024 * 1024
MAX_LINE = 1024 * 1024


def window_visible(clients: Any, monitors: Any, pid: int, *, screencast: bool = False) -> bool:
    """Whether a window of ``pid`` is on screen, from `j/clients` and `j/monitors` replies.

    Visible: mapped, and pinned or on a workspace some monitor shows (its active workspace, or
    its open special workspace). Fullscreen state, occlusion and group tabs (`hidden`) are not
    considered. Visible regardless: a screencast is running, our window isn't listed (not
    mapped yet), or a reply has an unexpected shape.
    """
    if screencast or not isinstance(clients, list) or not isinstance(monitors, list):
        return True
    shown: set[int] = set()
    for m in _dicts(cast(list[Any], monitors)):
        for key in ("activeWorkspace", "specialWorkspace"):
            ws_id = _workspace_id(m.get(key))
            if ws_id is not None and ws_id != 0:  # specialWorkspace id 0: none open
                shown.add(ws_id)
    if not shown:
        return True
    ours = [c for c in _dicts(cast(list[Any], clients)) if c.get("pid") == pid]
    return not ours or any(_on_screen(c, shown) for c in ours)


def _dicts(items: list[Any]) -> list[dict[str, Any]]:
    return [cast(dict[str, Any], x) for x in items if isinstance(x, dict)]


def _workspace_id(ws: Any) -> int | None:
    ws_id = cast(dict[str, Any], ws).get("id") if isinstance(ws, dict) else None
    return ws_id if isinstance(ws_id, int) and not isinstance(ws_id, bool) else None


def _on_screen(c: dict[str, Any], shown: set[int]) -> bool:
    if c.get("mapped") is False:
        return False
    if c.get("pinned") is True:
        return True
    ws_id = _workspace_id(c.get("workspace"))
    return ws_id is None or ws_id in shown


class HyprlandWatcher:
    """Follows Hyprland's event socket and calls ``on_change(visible)`` when our window goes
    on or off screen (starting from visible). Lives on the asyncio loop: `start`, then `stop`.

    After a relevant event (debounced), it asks `.socket.sock` for `j/clients` and `j/monitors`
    and applies `window_visible`. When Hyprland can't be reached, or answers unexpectedly, it
    reads as visible, logs once, and reconnects with ``backoff``.
    """

    def __init__(
        self,
        socket_dir: Path,
        pid: int,
        on_change: Callable[[bool], None],
        *,
        debounce: float = DEBOUNCE_S,
        backoff: Sequence[float] = BACKOFF_S,
        timeout: float = TIMEOUT_S,
        stable_after: float = STABLE_S,
    ) -> None:
        self.socket_dir = socket_dir
        self._pid = pid
        self._on_change = on_change
        self._debounce = debounce
        self._backoff = tuple(backoff)
        self._timeout = timeout
        self._stable_after = stable_after
        self._visible = True
        self._screencasts = 0
        self._connected_at: float | None = None  # loop time the event socket connected
        self._warned = False
        self._task: asyncio.Task[None] | None = None

    @property
    def visible(self) -> bool:
        return self._visible

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        failures = 0
        while True:  # ends when stop() cancels the task
            self._connected_at = None
            try:
                await self._watch()
                problem = "Hyprland event socket closed"
            except (OSError, ValueError) as e:
                problem = f"Hyprland event socket unavailable ({e})"
            except Exception:  # never let a bug leave the window marked hidden
                log.exception("Hyprland watcher failed")
                problem = "Hyprland watcher failed"
            # A socket that accepts and drops at once is still failing: keep backing off.
            stable = self._stable()
            self._screencasts = 0
            self._set(True)
            if stable:
                self._warned = False  # news after a healthy run
            self._report(problem)
            failures = 0 if stable else failures + 1
            await asyncio.sleep(self._backoff[min(failures, len(self._backoff) - 1)])

    async def _watch(self) -> None:
        """Read events until the socket closes; look again after each burst of relevant ones."""
        reader, writer = await asyncio.open_unix_connection(
            self.socket_dir / ".socket2.sock", limit=MAX_LINE
        )
        try:
            self._connected_at = asyncio.get_running_loop().time()
            await self._look()
            loop = asyncio.get_running_loop()
            due: float | None = None  # when the pending look runs
            while True:
                try:
                    wait = None if due is None else max(0.0, due - loop.time())
                    line = await asyncio.wait_for(reader.readline(), wait)
                except TimeoutError:
                    due = None
                    await self._look()
                    continue
                except ValueError:  # a line over MAX_LINE: skipped
                    continue
                if not line:
                    return
                if self._relevant(line) and due is None:
                    due = loop.time() + self._debounce
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _relevant(self, line: bytes) -> bool:
        name, sep, data = line.decode("utf-8", "replace").rstrip("\n").partition(">>")
        if not sep or name not in EVENTS:
            return False
        if name == "screencast":  # "STATE,OWNER"; several casts can overlap
            state = data.partition(",")[0]
            if state == "1":
                self._screencasts += 1
            elif state == "0":
                self._screencasts = max(0, self._screencasts - 1)
        return True

    async def _look(self) -> None:
        try:
            clients = await self._request("j/clients")
            monitors = await self._request("j/monitors")
        except (OSError, TimeoutError, ValueError) as e:
            self._report(f"Hyprland request failed ({e})")
            self._set(True)
            return
        if self._stable():
            self._warned = False
        self._set(window_visible(clients, monitors, self._pid, screencast=self._screencasts > 0))

    async def _request(self, command: str) -> list[Any]:
        """One request on `.socket.sock` (Hyprland answers, then closes). JSON list or raises."""
        async with asyncio.timeout(self._timeout):
            reader, writer = await asyncio.open_unix_connection(self.socket_dir / ".socket.sock")
            try:
                writer.write(command.encode())
                await writer.drain()
                reply = bytearray()
                while chunk := await reader.read(65536):  # bounded by MAX_REPLY and the timeout
                    reply += chunk
                    if len(reply) > MAX_REPLY:
                        raise ValueError(f"{command}: reply over {MAX_REPLY} bytes")
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        data = json.loads(reply)
        if not isinstance(data, list):
            raise ValueError(f"{command}: unexpected reply")
        return cast(list[Any], data)

    def _stable(self) -> bool:
        since = self._connected_at
        return since is not None and asyncio.get_running_loop().time() - since >= self._stable_after

    def _set(self, visible: bool) -> None:
        if visible == self._visible:
            return
        self._visible = visible
        log.info("window %s", "on screen" if visible else "on a hidden workspace")
        try:
            self._on_change(visible)
        except Exception:
            log.exception("Hyprland visibility callback failed")

    def _report(self, problem: str) -> None:
        """Warn once per run of failures (until the event socket has stayed up `stable_after`
        and a look succeeds, or it drops after that); then debug."""
        if self._warned:
            log.debug("%s", problem)
            return
        self._warned = True
        log.warning("%s; treating the window as visible", problem)


def watcher_from_env(
    pid: int, on_change: Callable[[bool], None], env: Mapping[str, str] = os.environ
) -> HyprlandWatcher | None:
    """A watcher for the Hyprland session in ``env``, or None outside Hyprland or when
    ``TIDALVIZ_HYPRLAND_VISIBILITY=0`` opts out."""
    signature = env.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not signature or env.get("TIDALVIZ_HYPRLAND_VISIBILITY") == "0":
        return None
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return HyprlandWatcher(Path(runtime) / "hypr" / signature, pid, on_change)
