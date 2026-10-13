"""Hyprland window visibility (Linux): the decision as a pure function over `j/clients` and
`j/monitors` replies (hand-written in Hyprland 0.56's schema, only the fields that matter)."""

from typing import Any

from tidalviz.hyprland import window_visible

PID = 4242


def client(workspace: int, *, pid: int = PID, **extra: Any) -> dict[str, Any]:
    name = str(workspace) if workspace > 0 else f"special:ws{-workspace}"
    return {
        "address": f"0x{workspace + 1000:x}",
        "mapped": True,
        "hidden": False,
        "visible": True,  # true even on a workspace no monitor shows: not used
        "workspace": {"id": workspace, "name": name},
        "floating": False,
        "monitor": 0,
        "pid": pid,
        "pinned": False,
        "fullscreen": 0,
        **extra,
    }


def monitor(mid: int, active: int, special: int = 0) -> dict[str, Any]:
    return {
        "id": mid,
        "name": f"OUT-{mid}",
        "activeWorkspace": {"id": active, "name": str(active)},
        "specialWorkspace": {"id": special, "name": f"special:ws{-special}" if special else ""},
        "focused": mid == 0,
        "disabled": False,
    }


MONITORS = [monitor(0, active=1), monitor(1, active=4)]


def test_a_window_on_a_workspace_no_monitor_shows_is_hidden():
    assert window_visible([client(21)], MONITORS, PID) is False


def test_a_window_on_any_monitors_active_workspace_is_visible():
    assert window_visible([client(1)], MONITORS, PID) is True
    assert window_visible([client(4)], MONITORS, PID) is True  # the unfocused monitor


def test_a_window_on_a_shown_special_workspace_is_visible_and_on_a_closed_one_hidden():
    shown = [monitor(0, active=1, special=-98), monitor(1, active=4)]
    assert window_visible([client(-98)], shown, PID) is True
    assert window_visible([client(-98)], MONITORS, PID) is False  # e.g. a "minimized" scratchpad


def test_a_pinned_window_is_visible_wherever_its_workspace_field_points():
    assert window_visible([client(21, pinned=True)], MONITORS, PID) is True


def test_an_unmapped_window_is_hidden_but_an_inactive_group_tab_counts_as_visible():
    assert window_visible([client(1, mapped=False)], MONITORS, PID) is False
    # Tab switches only show up in the high-rate activewindow event, which the watcher skips,
    # so a "hidden" tab could become the shown one unnoticed: treat it as visible.
    assert window_visible([client(1, hidden=True)], MONITORS, PID) is True


def test_fullscreen_does_not_matter_only_the_workspace_does():
    assert window_visible([client(21, fullscreen=2)], MONITORS, PID) is False
    assert window_visible([client(1, fullscreen=2)], MONITORS, PID) is True


def test_any_visible_window_of_ours_counts_and_other_processes_are_ignored():
    assert window_visible([client(21), client(1)], MONITORS, PID) is True
    assert window_visible([client(21), client(1, pid=PID + 1)], MONITORS, PID) is False


def test_a_screencast_counts_as_visible():
    assert window_visible([client(21)], MONITORS, PID, screencast=True) is True


def test_fails_open_when_our_window_is_missing_or_the_replies_are_unexpected():
    assert window_visible([client(1, pid=PID + 1)], MONITORS, PID) is True  # not mapped yet
    assert window_visible({"error": "x"}, MONITORS, PID) is True
    assert window_visible([client(21)], "unknown request", PID) is True
    assert window_visible([client(21)], [], PID) is True  # no monitor list: can't tell
    assert window_visible([{**client(21), "workspace": None}], MONITORS, PID) is True
    assert window_visible(["junk", client(21)], [7, *MONITORS], PID) is False
