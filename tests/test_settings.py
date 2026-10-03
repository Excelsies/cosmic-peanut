import json
from pathlib import Path

from tidalviz.settings import DEFAULTS, Settings


def test_defaults_when_file_missing(tmp_path: Path):
    s = Settings(tmp_path / "settings.json")
    assert s.data == DEFAULTS
    assert s.data["reduceFlashing"] is True and s.data["quality"] == "auto"


def test_update_persists_and_reports_changes(tmp_path: Path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    changed = s.update({"quality": "battery", "autoCycleSeconds": 30, "reduceFlashing": True})
    assert changed == {"quality": "battery", "autoCycleSeconds": 30}
    assert Settings(path).data["quality"] == "battery"


def test_update_drops_unknown_keys_and_bad_values(tmp_path: Path):
    s = Settings(tmp_path / "settings.json")
    changed = s.update(
        {"quality": "ultra", "autoCycleSeconds": -5, "hudVisible": "yes", "evil": 1, "active": 3}
    )
    assert changed == {}
    assert "evil" not in s.data


def test_params_per_visualizer(tmp_path: Path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    s.set_params("builtin/bars", {"color": "#ff0000", "mirror": True})
    assert Settings(path).params_for("builtin/bars") == {"color": "#ff0000", "mirror": True}
    assert s.params_for("builtin/orbit") == {}


def test_corrupt_file_falls_back_to_defaults_and_keeps_a_backup(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    s = Settings(path)
    assert s.data == DEFAULTS
    assert any(p.name.startswith("settings.json.corrupt") for p in tmp_path.iterdir())


def test_loaded_values_are_validated_too(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"quality": "nope", "reduceFlashing": False}))
    s = Settings(path)
    assert s.data["quality"] == "auto" and s.data["reduceFlashing"] is False


def test_transparent_and_borderless_default_on(tmp_path: Path):
    s = Settings(tmp_path / "settings.json")
    assert s.data["transparent"] is True and s.data["borderless"] is True
    assert s.update({"transparent": False, "borderless": "no"}) == {"transparent": False}


def test_float_on_top_defaults_on(tmp_path: Path):
    s = Settings(tmp_path / "settings.json")
    assert s.data["onTop"] is True
    assert s.update({"onTop": False}) == {"onTop": False}


def test_background_opacity_defaults_to_see_through_and_allows_0_to_1(tmp_path: Path):
    s = Settings(tmp_path / "settings.json")
    assert s.data["backgroundOpacity"] == 0
    assert s.update({"backgroundOpacity": 0.55}) == {"backgroundOpacity": 0.55}
    assert Settings(tmp_path / "settings.json").data["backgroundOpacity"] == 0.55
    assert s.update({"backgroundOpacity": 1}) == {"backgroundOpacity": 1}
    assert s.update({"backgroundOpacity": 0}) == {"backgroundOpacity": 0}


def test_visual_opacity_defaults_to_opaque_and_keeps_a_0_1_floor(tmp_path: Path):
    s = Settings(tmp_path / "settings.json")
    assert s.data["visualOpacity"] == 1.0
    assert s.update({"visualOpacity": 0.1}) == {"visualOpacity": 0.1}
    assert s.update({"visualOpacity": 0}) == {}
    assert s.update({"visualOpacity": 0.09}) == {}


def test_opacities_reject_out_of_range_non_finite_and_non_numbers(tmp_path: Path):
    s = Settings(tmp_path / "settings.json")
    for key in ("backgroundOpacity", "visualOpacity"):
        for bad in (
            -0.1,
            1.01,
            2,
            float("nan"),
            float("inf"),
            10**400,
            -(10**400),
            True,
            False,
            "0.5",
            None,
        ):
            assert s.update({key: bad}) == {}, (key, bad)
    assert (s.data["backgroundOpacity"], s.data["visualOpacity"]) == (0, 1.0)


def test_hand_edited_out_of_range_opacities_load_as_the_defaults(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"backgroundOpacity": 3, "visualOpacity": 0.0}))
    s = Settings(path)
    assert (s.data["backgroundOpacity"], s.data["visualOpacity"]) == (0, 1.0)


def test_hand_edited_huge_int_opacities_load_as_the_defaults(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(
        '{"backgroundOpacity": 1' + "0" * 400 + ', "visualOpacity": 1' + "0" * 400 + "}"
    )
    s = Settings(path)
    assert (s.data["backgroundOpacity"], s.data["visualOpacity"]) == (0, 1.0)
