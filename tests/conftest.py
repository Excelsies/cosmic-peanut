"""Suite-wide safety: nothing in the tests may play to a real audio device."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "real_sink: the test builds a non-fake output sink on purpose (it cannot play)"
    )


@pytest.fixture(autouse=True)
def _silent_player_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIDALVIZ_PLAYER_SINK", "fakesink")


@pytest.fixture(autouse=True)
def _no_real_output(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Fail the test if a `GstBackend` is built with anything but `fakesink`, unless the test is
    marked `real_sink`. The env var above is a default; this is the guarantee."""
    from tidalviz.player import engine_available

    if not engine_available() or request.node.get_closest_marker("real_sink"):
        yield
        return
    import tidalviz.player.engine as engine

    violations: list[str] = []
    original = engine.GstBackend.__init__

    def guarded(self, sink, *a, **kw):
        if sink.element != "fakesink":
            violations.append(f"GstBackend built with {sink!r}")
            raise AssertionError(f"test tried to use a real output: {sink!r}")
        original(self, sink, *a, **kw)

    monkeypatch.setattr(engine.GstBackend, "__init__", guarded)
    yield
    assert not violations, violations


@pytest.fixture(scope="session")
def gst():
    """The Gst module, or a skip: tests that need GStreamer run only where the engine imports."""
    from tidalviz.player import engine_available

    if not engine_available():
        pytest.skip("GStreamer (PyGObject with the gtk extra) is not available")
    from gi.repository import Gst  # type: ignore[import-not-found]

    return Gst


@pytest.fixture(scope="session")
def media(gst, tmp_path_factory: pytest.TempPathFactory):
    from tests.player_media import build

    return build(gst, tmp_path_factory.mktemp("player-media"))
