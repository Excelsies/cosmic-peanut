"""Hyprland window visibility (Linux): is any window of this process on a monitor?

WebKitGTK never sets `document.visibilityState` to "hidden" on Hyprland (GTK3 has no suspended
state), so a window on a workspace no monitor shows keeps drawing every frame. The host asks
this module instead and tells the shell (`windowVisibility`, docs/protocols.md §4). Every
doubt reads as visible: a wrong "hidden" would freeze a visualizer the user is watching.
"""

from typing import Any, cast


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
