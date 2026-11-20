"""Validation of the player's shell → host messages (docs/protocols.md §4)."""

from typing import Any

import pytest

from tidalviz.transport.control_schema import ShellMessageError, validate_shell_message

VALID: list[dict[str, Any]] = [
    {"type": "transport", "action": "play"},
    {"type": "transport", "action": "pause"},
    {"type": "transport", "action": "toggle"},
    {"type": "transport", "action": "next"},
    {"type": "transport", "action": "previous"},
    {"type": "transport", "action": "seek", "value": 12.5},
    {"type": "transport", "action": "seek", "value": 0},
    {"type": "transport", "action": "seek", "value": 3.5, "serial": 12},
    {"type": "playerOpen", "kind": "files", "replace": True},
    {"type": "playerOpen", "kind": "folder", "replace": False},
    {"type": "playerSelect", "index": 0},
    {"type": "playerSelect", "index": 41},
    {"type": "playerClear"},
]

INVALID: list[dict[str, Any]] = [
    {"type": "transport"},
    {"type": "transport", "action": "rewind"},
    {"type": "transport", "action": 3},
    {"type": "transport", "action": "seek"},
    {"type": "transport", "action": "seek", "value": "12"},
    {"type": "transport", "action": "seek", "value": -1},
    {"type": "transport", "action": "seek", "value": float("inf")},
    {"type": "transport", "action": "seek", "value": True},
    {"type": "transport", "action": "seek", "value": 1, "serial": -1},
    {"type": "transport", "action": "seek", "value": 1, "serial": "3"},
    {"type": "transport", "action": "seek", "value": 1, "serial": 1.5},
    {"type": "playerOpen"},
    {"type": "playerOpen", "kind": "files"},
    {"type": "playerOpen", "kind": "url", "replace": True},
    {"type": "playerOpen", "kind": "files", "replace": "yes"},
    {"type": "playerSelect"},
    {"type": "playerSelect", "index": -1},
    {"type": "playerSelect", "index": 1.5},
    {"type": "playerSelect", "index": "1"},
]


@pytest.mark.parametrize(
    "msg", VALID, ids=lambda m: f"{m['type']}-{m.get('action', m.get('kind', ''))}"
)
def test_valid_messages_pass(msg: dict[str, Any]) -> None:
    assert validate_shell_message(msg) is True


def test_extra_fields_are_ignored_not_rejected_like_every_other_message() -> None:
    # The host never reads a path from the shell; an extra field is simply not looked at.
    assert validate_shell_message(
        {"type": "playerOpen", "kind": "files", "replace": True, "paths": ["/x"]}
    )


@pytest.mark.parametrize("msg", INVALID, ids=lambda m: repr(m))
def test_invalid_messages_raise(msg: dict[str, Any]) -> None:
    with pytest.raises(ShellMessageError):
        validate_shell_message(msg)
