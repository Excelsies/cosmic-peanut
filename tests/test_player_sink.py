"""TIDALVIZ_PLAYER_SINK: which element plays, and on which device."""

import pytest

from tidalviz.player.sink import ENV_VAR, SinkSpec, sink_from_env


def test_unset_or_blank_means_pulsesink_on_the_default_device():
    assert sink_from_env({}) == SinkSpec("pulsesink", None)
    assert sink_from_env({ENV_VAR: "  "}) == SinkSpec("pulsesink", None)


def test_an_element_alone_or_with_a_device():
    assert sink_from_env({ENV_VAR: "fakesink"}) == SinkSpec("fakesink", None)
    assert sink_from_env({ENV_VAR: "pulsesink:tidalviz_test"}) == SinkSpec(
        "pulsesink", "tidalviz_test"
    )
    assert sink_from_env({ENV_VAR: "pulsesink:"}) == SinkSpec("pulsesink", None)


@pytest.mark.parametrize("value", ["pipewiresink", "alsasink:hw:0", "fakesink:x", "rm -rf"])
def test_anything_else_is_refused_loudly(value: str):
    with pytest.raises(ValueError, match=ENV_VAR):
        sink_from_env({ENV_VAR: value})
