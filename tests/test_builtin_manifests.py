"""The built-in visualizers and the template repo ship valid, complete manifests."""

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "src/tidalviz/plugins/manifest.schema.json").read_text())
REPOS = {
    "builtin": ROOT / "plugins/builtin",
    "template": ROOT / "plugins/template",
    "cosmic-peanut": ROOT / "plugins/cosmic-peanut",
    "fieldwork": ROOT / "plugins/fieldwork",
}
BUILTIN_REPOS = ("builtin", "cosmic-peanut")
THUMB_MAX = 60 * 1024


def _manifest(repo: str) -> dict:
    return json.loads((REPOS[repo] / "tidalviz.json").read_text())


def _entries() -> list[tuple[str, dict]]:
    return [(repo, v) for repo in REPOS for v in _manifest(repo)["visualizers"]]


@pytest.mark.parametrize("repo", REPOS)
def test_manifest_validates_against_schema(repo: str):
    jsonschema.validate(_manifest(repo), SCHEMA)


def test_builtin_renderers():
    got = {v["id"]: (v["renderer"], v.get("libs", [])) for v in _manifest("builtin")["visualizers"]}
    assert got == {
        "bars": ("2d", []),
        "undertow": ("webgl2", []),
        "orbit": ("three", ["three"]),
        "pulsar": ("2d", []),
        "stargate": ("webgl2", []),
        "blaze": ("webgl2", []),
        "cascade": ("webgl2", []),
        "radar": ("webgl2", []),
        "tentacube": ("three", ["three"]),
        "tiedye": ("webgl2", []),
        "skull": ("webgl2", []),
        "darksun": ("webgl2", []),
        "eclipse": ("webgl2", []),
        "marbling": ("webgl2", []),
        "laminar": ("webgl2", []),
        "tetraballs": ("webgl2", []),
    }


def test_template_is_one_webgl2_visualizer():
    (viz,) = _manifest("template")["visualizers"]
    assert viz["renderer"] == "webgl2"


@pytest.mark.parametrize(
    ("repo", "viz"), _entries(), ids=lambda x: x if isinstance(x, str) else x["id"]
)
def test_entry_and_thumbnail_exist(repo: str, viz: dict):
    assert (REPOS[repo] / viz["entry"]).is_file()
    if "thumbnail" in viz:
        thumb = REPOS[repo] / viz["thumbnail"]
        assert thumb.is_file()
        assert thumb.stat().st_size <= THUMB_MAX


@pytest.mark.parametrize(
    ("repo", "viz"), _entries(), ids=lambda x: x if isinstance(x, str) else x["id"]
)
def test_param_defaults_are_legal(repo: str, viz: dict):
    for p in viz.get("params", []):
        if p["type"] == "number":
            assert p["min"] <= p["default"] <= p["max"], p["id"]
        elif p["type"] == "select":
            assert p["default"] in p["options"], p["id"]


@pytest.mark.parametrize("repo", BUILTIN_REPOS)
def test_builtins_have_thumbnails_and_descriptions(repo: str):
    for v in _manifest(repo)["visualizers"]:
        assert v.get("thumbnail") and v.get("description"), v["id"]


def test_bars_params_cover_every_type():
    (bars,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "bars"]
    types = {p["type"] for p in bars["params"]}
    assert types == {"number", "color", "boolean", "select"}


def test_undertow_params_match_prd_example():
    (u,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "undertow"]
    params = {p["id"]: p for p in u["params"]}
    assert params["speed"]["type"] == "number"
    assert params["tint"]["type"] == "color"
    assert params["mirror"]["type"] == "boolean"
    assert params["mode"]["options"] == ["rings", "bars"]


@pytest.mark.parametrize("repo", REPOS)
def test_repo_ships_current_sdk_types(repo: str):
    sdk = (ROOT / "web/sdk/tidalviz.d.ts").read_text()
    assert (REPOS[repo] / "tidalviz.d.ts").read_text() == sdk


def test_template_readme_covers_the_workflow():
    readme = (REPOS["template"] / "README.md").read_text()
    for needle in ("tidalviz --dev", "Add folder", "reduceFlashing", "bassAtt", "sandbox", "git"):
        assert needle in readme, needle


def test_pulsar_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "pulsar"]
    assert (v["name"], v["entry"], v["thumbnail"]) == (
        "Pulsar",
        "src/pulsar.js",
        "thumbs/pulsar.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == ["lines", "speed", "height", "color", "lineWidth"]
    assert params["lines"]["default"] == "80"
    assert all(40 <= int(o) <= 120 for o in params["lines"]["options"])
    assert params["speed"]["default"] == 10
    assert params["color"]["default"] == "#ffffff"


def test_cosmic_peanut_manifest_matches_prd():
    (v,) = _manifest("cosmic-peanut")["visualizers"]
    assert (v["id"], v["name"], v["renderer"], v["entry"]) == (
        "cosmic-peanut",
        "Cosmic Peanut",
        "webgl2",
        "src/cosmic-peanut.js",
    )
    assert "libs" not in v
    got = {
        p["id"]: tuple(p.get(k) for k in ("type", "label", "default", "min", "max", "options"))
        for p in v["params"]
    }
    assert got == {
        "travel": ("number", "Travel time", 7, 2, 20, None),
        "amp": ("number", "Amplitude", 0.22, 0, 0.6, None),
        "detail": ("number", "Detail", 0.7, 0, 1, None),
        "pulse": ("number", "Bass pulse", 0.35, 0, 1, None),
        "orbit": ("number", "Orbit speed", 0.12, -0.6, 0.6, None),
        "bright": ("number", "Brightness", 1, 0.2, 2, None),
        "density": ("select", "Rings", "medium", None, None, ["sparse", "medium", "dense"]),
        "palette": ("select", "Palette", "nebula", None, None, ["nebula", "ember", "phosphor"]),
        "lines": ("select", "Lines", "soft", None, None, ["soft", "fine"]),
        # Added after the PRD at the user's request: 2× MSAA for soft lines costs ~7.5 MB of GPU
        # memory at 1440p, so it can be switched off.
        "antialias": ("boolean", "Antialias soft lines", True, None, None, None),
    }
    assert [p["id"] for p in v["params"]] == list(got)
    assert {p["id"]: p.get("step") for p in v["params"]}["travel"] == 0.5


def test_stargate_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "stargate"]
    assert (v["name"], v["entry"], v["thumbnail"]) == (
        "Stargate",
        "src/stargate.js",
        "thumbs/stargate.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == ["speed", "roll", "detail", "streak", "spread", "brightness", "palette"]
    assert params["roll"]["default"] == 0.25
    assert params["roll"]["min"] < 0 < params["roll"]["max"]
    assert params["palette"]["options"] == ["film", "ember", "ice", "mono"]
    assert params["palette"]["default"] == "film"
    assert (REPOS["builtin"] / "shaders/stargate/stargate.frag").is_file()


def test_blaze_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "blaze"]
    assert (v["name"], v["entry"], v["renderer"]) == ("Blaze", "src/blaze.js", "webgl2")
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "intensity",
        "reactivity",
        "height",
        "turbulence",
        "speed",
        "glow",
        "palette",
        "rendering",
        "feed",
        "layout",
        "detail",
    ]
    r = params["reactivity"]
    assert (r["type"], r["min"], r["max"], r["default"]) == ("number", 0, 2, 1)
    # The 64-band "spectrum" feed is retired: a saved "spectrum" isn't an option any more, so the
    # host (and the plugin's resolveFeed) fall back to the default, the spectrogram.
    assert params["feed"]["options"] == ["spectrogram", "waveform"]
    assert params["feed"]["default"] == "spectrogram"
    assert params["layout"]["type"] == "select"
    assert params["layout"]["options"] == ["mirrored", "linear"]
    assert params["layout"]["default"] == "mirrored"
    # The original four, then flame-test materials (iron and nickel throw sparks).
    assert params["palette"]["options"] == [
        "natural",
        "blue gas",
        "green chemical",
        "ember mono",
        "propane",
        "copper",
        "boron",
        "sodium",
        "lithium",
        "strontium",
        "potassium",
        "calcium",
        "barium",
        "iron",
        "nickel",
    ]
    assert params["palette"]["default"] == "natural"
    # blackbody: Tetraballs' fire model; simple: the original hand-keyed colour ramps.
    assert params["rendering"]["type"] == "select"
    assert params["rendering"]["options"] == ["blackbody", "simple"]
    assert params["rendering"]["default"] == "blackbody"
    assert (params["detail"]["min"], params["detail"]["max"]) == (0.15, 0.5)


def test_cascade_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "cascade"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Cascade",
        "src/cascade.js",
        "webgl2",
        "thumbs/cascade.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "flow",
        "density",
        "width",
        "height",
        "gravity",
        "spray",
        "mist",
        "turbulence",
        "palette",
    ]
    assert params["density"]["options"] == ["16k", "32k", "64k"]
    assert params["palette"]["options"] == ["glacier", "tropical", "moonlit", "mono"]
    assert params["palette"]["default"] == "glacier"
    # Fills the window by default; width and height narrow the lip and shorten the drop.
    for pid in ("width", "height"):
        assert params[pid]["default"] == params[pid]["max"] == 1
        assert 0 < params[pid]["min"] < 1
    assert params["density"]["default"] == "64k"


def test_radar_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "radar"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Radar",
        "src/radar.js",
        "webgl2",
        "thumbs/radar.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "sync",
        "speed",
        "persistence",
        "gain",
        "palette",
        "graticule",
        "floor",
        "maxFreq",
    ]
    assert params["sync"]["options"] == ["off", "beat", "bar"]
    assert params["sync"]["default"] == "bar"
    # lib/radar.js DEFAULT_SPEED: Reduce motion slows the sweep only while it's at this default.
    assert params["speed"]["default"] == 0.25
    assert params["palette"]["options"] == ["green", "amber", "blue", "white"]
    assert params["palette"]["default"] == "green"
    # The spectrogram's dB floor (below the auto-gain reference) and top frequency (radar.js maxHzOf).
    floor = params["floor"]
    assert (floor["type"], floor["min"], floor["max"], floor["default"]) == (
        "number",
        -80,
        -30,
        -60,
    )
    assert params["maxFreq"]["options"] == ["4k", "8k", "16k"]
    assert "contacts" not in params
    for f in ("fullscreen.vert", "paint.frag", "composite.frag"):
        assert (REPOS["builtin"] / "shaders/radar" / f).is_file(), f


def test_tentacube_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "tentacube"]
    assert (v["name"], v["entry"], v["renderer"], v["libs"], v["thumbnail"]) == (
        "Tentacube",
        "src/tentacube.js",
        "three",
        ["three"],
        "thumbs/tentacube.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "length",
        "segments",
        "drag",
        "twitch",
        "pulse",
        "morph",
        "material",
        "background",
        "tiling",
        "bloom",
        "distance",
    ]
    assert params["morph"]["options"] == ["off", "slow", "fast"]
    assert params["material"]["options"] == ["auto", "chrome", "iridescent", "emissive", "obsidian"]
    assert params["material"]["default"] == "auto"
    assert params["background"]["options"] == ["hyperbolic", "none"]
    assert params["tiling"]["options"] == ["{7,3}", "{5,4}", "{4,5}"]
    seg = params["segments"]
    assert (seg["min"], seg["max"], seg["step"], seg["default"]) == (12, 48, 1, 24)
    # The creature code keys "reduce motion" off the twitch default.
    assert params["twitch"]["default"] == 1


def test_tiedye_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "tiedye"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Tie-Dye",
        "src/tiedye.js",
        "webgl2",
        "thumbs/tiedye.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "pattern",
        "palette",
        "fabric",
        "twist",
        "speed",
        "bleed",
        "reactivity",
        "bloom",
    ]
    # Option order is the shader index order in lib/tiedye.js (PATTERNS, PALETTES, FABRICS).
    assert params["pattern"]["options"] == ["spiral", "bullseye", "crumple", "shibori"]
    assert params["pattern"]["default"] == "spiral"
    assert params["palette"]["options"] == ["rainbow", "sunset", "ocean", "neon"]
    assert params["palette"]["default"] == "rainbow"
    assert params["fabric"]["options"] == ["white", "none"]
    assert params["fabric"]["default"] == "white"
    # lib/tiedye.js DEFAULT_SPEED: Reduce motion slows the spin only while Speed is at this default.
    assert params["speed"]["default"] == 1
    assert params["bloom"]["type"] == "boolean"
    assert params["bloom"]["default"] is True
    for f in ("fullscreen.vert", "tiedye.frag"):
        assert (REPOS["builtin"] / "shaders/tiedye" / f).is_file(), f


def test_skull_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "skull"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Skull Trip",
        "src/skull.js",
        "webgl2",
        "thumbs/skull.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "reactivity",
        "density",
        "warp",
        "speed",
        "jaw",
        "mode",
        "stripes",
        "size",
    ]
    # lib/skull-motion.js DEFAULTS: Reduce motion calms flow, warp, nods and ripples only while
    # these are at their defaults.
    for pid in ("reactivity", "density", "warp", "speed", "jaw", "size"):
        assert params[pid]["type"] == "number"
        assert params[pid]["default"] == 1, pid
    # lib/opart.js MODES / STRIPES.
    assert params["mode"]["options"] == ["monochrome", "acid"]
    assert params["mode"]["default"] == "monochrome"
    assert params["stripes"]["options"] == ["black & white", "black only"]
    assert params["stripes"]["default"] == "black & white"
    for f in ("fullscreen.vert", "opart.glsl", "skull.frag", "composite.frag"):
        assert (REPOS["builtin"] / "shaders/skull" / f).is_file(), f


def test_darksun_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "darksun"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Dark Sun",
        "src/darksun.js",
        "webgl2",
        "thumbs/darksun.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "reactivity",
        "rangeWidth",
        "rangeDepth",
        "beam",
        "sunSize",
        "palette",
        "backdrop",
    ]
    # lib/darksun.js DEFAULTS / RANGES mirror these.
    got = {
        k: (p["min"], p["max"], p["default"]) for k, p in params.items() if p["type"] == "number"
    }
    assert got == {
        "reactivity": (0, 2, 1),
        "rangeWidth": (0.3, 0.7, 0.5),
        "rangeDepth": (0.3, 2, 1),
        "beam": (0, 2, 1),
        "sunSize": (0.5, 1.6, 1),
    }
    assert params["palette"]["options"] == ["dusk", "ash", "teal", "gold"]
    assert params["palette"]["default"] == "dusk"
    assert params["backdrop"]["options"] == ["painted", "none"]
    assert params["backdrop"]["default"] == "painted"
    for f in ("fullscreen.vert", "darksun.frag"):
        assert (REPOS["builtin"] / "shaders/darksun" / f).is_file(), f


def test_eclipse_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "eclipse"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Eclipse",
        "src/eclipse.js",
        "webgl2",
        "thumbs/eclipse.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "reactivity",
        "reach",
        "thickness",
        "clouds",
        "tint",
        "sky",
        "rotation",
    ]
    r = params["reactivity"]
    assert (r["type"], r["min"], r["max"], r["default"]) == ("number", 0, 2, 1)
    # lib/eclipse.js TINTS; tintGamma / skyAlpha fall back to the defaults for anything else.
    assert params["tint"]["options"] == ["monochrome", "silver", "sepia", "cold blue"]
    assert params["tint"]["default"] == "monochrome"
    assert params["sky"]["options"] == ["black", "none"]
    assert params["sky"]["default"] == "black"
    # lib/eclipse.js DEFAULT_ROTATION: Reduce motion slows the drift only while it's at this default.
    rot = params["rotation"]
    assert rot["default"] == 0.03
    assert rot["min"] < 0 < rot["max"]
    for f in ("fullscreen.vert", "noise.glsl", "eclipse.frag", "clouds.frag"):
        assert (REPOS["builtin"] / "shaders/eclipse" / f).is_file(), f


def test_marbling_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "marbling"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Marbling",
        "src/marbling.js",
        "webgl2",
        "thumbs/marbling.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == ["palette", "paper", "pour", "size", "rake", "reactivity", "renew"]
    assert params["palette"]["options"] == ["beast", "indigo", "emerald", "gold"]
    assert params["palette"]["default"] == "beast"
    assert params["paper"]["options"] == ["cream", "none"]
    assert params["paper"]["default"] == "cream"
    # lib/marbling.js DEFAULTS: Reduce motion calms pour/rake/renew only while they sit here.
    for pid in ("pour", "size", "rake", "reactivity", "renew"):
        assert params[pid]["type"] == "number"
        assert params[pid]["default"] == 1, pid
    assert params["rake"]["min"] == 0
    assert params["renew"]["min"] == 0
    for f in (
        "fullscreen.vert",
        "sample.glsl",
        "events.glsl",
        "sim.frag",
        "resample.frag",
        "composite.frag",
    ):
        assert (REPOS["builtin"] / "shaders/marbling" / f).is_file(), f


def test_laminar_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "laminar"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Laminar",
        "src/laminar.js",
        "webgl2",
        "thumbs/laminar.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "reactivity",
        "speed",
        "turbulence",
        "density",
        "size",
        "detail",
        "trail",
        "palette",
        "backdrop",
    ]
    # lib/laminar.js MOTION_DEFAULTS: Reduce motion swaps these only while they're at the default.
    for pid in ("reactivity", "speed", "turbulence"):
        assert params[pid]["default"] == 1, pid
    # lib/flow.js simGrid clamps Detail to 0.08-0.4.
    assert (params["detail"]["min"], params["detail"]["max"]) == (0.08, 0.4)
    assert params["trail"] == {"id": "trail", "type": "boolean", "label": "Trail", "default": True}
    assert params["palette"]["options"] == ["currents", "sea glass", "sunset", "mono"]
    assert params["palette"]["default"] == "currents"
    assert params["backdrop"]["options"] == ["black", "none"]
    assert params["backdrop"]["default"] == "black"
    for f in ("common.glsl", "advect.frag", "correct.frag", "composite.frag"):
        assert (REPOS["builtin"] / "shaders/laminar" / f).is_file()


def test_tetraballs_manifest():
    (v,) = [v for v in _manifest("builtin")["visualizers"] if v["id"] == "tetraballs"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Tetraballs",
        "src/tetraballs.js",
        "webgl2",
        "thumbs/tetraballs.jpg",
    )
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "material",
        "reactivity",
        "size",
        "bounce",
        "tumble",
        "background",
        "quality",
        "tint",
    ]
    # Order matches lib/tetra-material.js MATERIALS (after "auto").
    assert params["material"]["options"] == [
        "auto",
        "chrome",
        "soap",
        "jade",
        "brushed",
        "velvet",
        "glass",
        "water",
        "fire",
        "smoke",
    ]
    assert params["material"]["default"] == "chrome"
    assert params["background"]["options"] == ["studio", "none"]
    assert params["background"]["default"] == "studio"
    assert params["quality"]["options"] == ["low", "medium", "high"]
    # lib/tetra-motion.js DEFAULT_TUMBLE / DEFAULT_BOUNCE: Reduce motion calms them at these values.
    assert params["tumble"]["default"] == 0.35
    assert params["bounce"]["default"] == 1
    for name in ("common.glsl", "materials.glsl", "surface.glsl", "volume.glsl", "composite.frag"):
        assert (REPOS["builtin"] / "shaders/tetraballs" / name).is_file()


def test_fieldwork_lists_its_visualizers_in_order():
    m = _manifest("fieldwork")
    assert [v["id"] for v in m["visualizers"]] == [
        "chladni",
        "murmuration",
        "plotter",
        "misregister",
    ]
    for v in m["visualizers"]:
        assert v["author"] == "Fieldwork" and v.get("description") and v.get("thumbnail"), v["id"]


def test_chladni_manifest():
    (v,) = [v for v in _manifest("fieldwork")["visualizers"] if v["id"] == "chladni"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Chladni",
        "src/chladni.js",
        "webgl2",
        "thumbs/chladni.jpg",
    )
    assert "libs" not in v
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == ["palette", "backdrop", "grains", "complexity", "reactivity", "hop"]
    # lib/chladni-params.js indexes these options in this order (composite.frag's palettes too).
    assert params["palette"]["options"] == ["brass", "slate", "cobalt", "mono"]
    assert params["palette"]["default"] == "brass"
    assert params["backdrop"]["options"] == ["plate", "none"]
    assert params["backdrop"]["default"] == "plate"
    # Battery quality swaps 64k for 32k only while Grains is at this default.
    assert params["grains"]["options"] == ["32k", "64k", "128k"]
    assert params["grains"]["default"] == "64k"
    # lib/chladni-params.js DEFAULT_REACTIVITY: Reduce motion calms the plate only at this default.
    assert params["reactivity"]["default"] == 1
    for pid, lo, hi, default in (
        ("complexity", 0.5, 2, 1),
        ("reactivity", 0, 2, 1),
        ("hop", 0, 2, 1),
    ):
        p = params[pid]
        assert (p["min"], p["max"], p["default"], p["step"]) == (lo, hi, default, 0.05), pid
    for f in (
        "update.vert",
        "update.frag",
        "deposit.vert",
        "deposit.frag",
        "fullscreen.vert",
        "composite.frag",
    ):
        assert (REPOS["fieldwork"] / "shaders/chladni" / f).is_file()


def test_murmuration_manifest():
    (v,) = [v for v in _manifest("fieldwork")["visualizers"] if v["id"] == "murmuration"]
    assert (v["name"], v["entry"], v["renderer"], v["libs"], v["thumbnail"]) == (
        "Murmuration",
        "src/murmuration.js",
        "three",
        ["three"],
        "thumbs/murmuration.jpg",
    )
    assert v.get("description")
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == ["sky", "ink", "flock", "speed", "falcon", "reactivity"]
    # Order matches lib/murmuration-params.js SKIES (shader palette index) and FLOCKS.
    assert params["sky"]["options"] == ["dusk", "overcast", "rose", "none"]
    assert params["sky"]["default"] == "dusk"
    assert params["ink"] == {
        "id": "ink",
        "type": "color",
        "label": "Ink (sky none)",
        "default": "#ece6da",
    }
    assert params["flock"]["options"] == ["2000", "4000", "6000"]
    assert params["flock"]["default"] == "4000"
    speed = params["speed"]
    assert (speed["min"], speed["max"], speed["step"]) == (0.2, 2, 0.05)
    # lib/murmuration-params.js DEFAULT_SPEED: Reduce motion applies only while speed is at it.
    assert speed["default"] == 1
    assert params["falcon"]["default"] is True
    react = params["reactivity"]
    assert (react["min"], react["max"], react["step"], react["default"]) == (0, 2, 0.05, 1)
    for f in ("sky.glsl", "sky.vert", "sky.frag", "bird.vert", "bird.frag"):
        assert (REPOS["fieldwork"] / "shaders/murmuration" / f).is_file()


def test_plotter_manifest():
    (v,) = [v for v in _manifest("fieldwork")["visualizers"] if v["id"] == "plotter"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Plotter",
        "src/plotter.js",
        "2d",
        "thumbs/plotter.jpg",
    )
    assert "libs" not in v
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == ["grid", "phrase", "paper", "inks", "weight", "chatter", "speed"]
    # Option order matches lib/plotter-params.js (GRID_OPTIONS, PAPER_FILL, INKS, ...).
    assert params["grid"]["options"] == ["4x3", "3x2", "6x4", "1x1"]
    assert params["grid"]["default"] == "4x3"
    assert params["phrase"]["options"] == ["2 bars", "4 bars", "8 bars"]
    assert params["phrase"]["default"] == "4 bars"
    assert params["paper"]["options"] == ["blueprint", "cream", "white", "none"]
    assert params["paper"]["default"] == "blueprint"
    assert params["inks"]["options"] == ["fineliner", "sepia", "mono"]
    assert params["inks"]["default"] == "fineliner"
    ranges = {
        k: (params[k]["min"], params[k]["max"], params[k]["default"])
        for k in ("weight", "chatter", "speed")
    }
    # lib/plotter-params.js DEFAULT_CHATTER / DEFAULT_SPEED: Reduce motion calms them at these values.
    assert ranges == {"weight": (0.5, 3, 1), "chatter": (0, 1, 0.5), "speed": (0.25, 2, 1)}


def test_misregister_manifest():
    (v,) = [v for v in _manifest("fieldwork")["visualizers"] if v["id"] == "misregister"]
    assert (v["name"], v["entry"], v["renderer"], v["thumbnail"]) == (
        "Misregister",
        "src/misregister.js",
        "webgl2",
        "thumbs/misregister.jpg",
    )
    assert "libs" not in v
    assert v.get("description")
    params = {p["id"]: p for p in v["params"]}
    assert list(params) == [
        "palette",
        "paper",
        "screen",
        "misregister",
        "grain",
        "relayout",
        "reactivity",
    ]
    # Option order = index order in src/lib/misregister-params.js (PALETTES, PAPERS, RELAYOUT_BARS).
    assert params["palette"]["options"] == ["riso", "bauhaus", "sunset", "mono"]
    assert params["palette"]["default"] == "riso"
    assert params["paper"]["options"] == ["cream", "white", "kraft", "none"]
    assert params["paper"]["default"] == "cream"
    assert params["relayout"]["options"] == ["8 bars", "16 bars", "32 bars", "never"]
    # DEFAULT_RELAYOUT and DEFAULTS.misregister: Reduce motion calms them only at these values.
    assert params["relayout"]["default"] == "8 bars"
    got = {
        k: tuple(params[k].get(f) for f in ("type", "min", "max", "step", "default"))
        for k in ("screen", "misregister", "grain", "reactivity")
    }
    assert got == {
        "screen": ("number", 30, 160, 1, 90),
        "misregister": ("number", 0, 1, 0.01, 0.5),
        "grain": ("number", 0, 1, 0.01, 0.4),
        "reactivity": ("number", 0, 2, 0.05, 1),
    }
    for f in ("fullscreen.vert", "plates.frag", "halftone.frag"):
        assert (REPOS["fieldwork"] / "shaders/misregister" / f).is_file(), f
