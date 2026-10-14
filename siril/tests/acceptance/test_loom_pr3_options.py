"""Acceptance: the options file (Loom's defaults, migrations of old saved choices, the output folder never saved).

Black box through ``loomsiril.loom.config``; skipped until that module exists. Expected values are Loom's
(lib/Config.js defaults, lib/StepsSyqon.js migration) written out here as literals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

config = pytest.importorskip("loomsiril.loom.config")

# Loom's defaults for every field the port keeps (lib/Config.js:126-188), plus the port's two additions.
EXPECTED_DEFAULTS = {
    "paths": {"L": "", "R": "", "G": "", "B": "", "H": "", "S": "", "O": ""},
    "savedList": "",
    "filters": {},
    "gradientTool": "GraXpert",
    "graxpertNarrowband": False,
    "smoothing": 0.5,
    "validateOnly": False,
    "keepWindowsOnError": False,
    "palettes": [],
    "narrowbandBandwidth": 3.0,
    "narrowbandNormalize": True,
    "reduceHalos": False,
    "sharpenTool": "none",
    "parallaxFamily": "classic",
    "stretch": False,
    "stretchMethod": "mtf",
    "stretchTarget": 0.15,   # port: the sky level of the stretch; Loom has the fixed 0.25
    "keepLinear": False,
    "separateLStars": False,
    "exportPsb": False,
    "projectName": "",
    "exportDir": "",
    "outputDir": "",   # saved: the dialog reopens with the last output folder
    "starTool": "none",
    "noiseTool": "none",
    "noiseLevel": "medium",
    "noiseLevelL": "",
    "starReduction": "none",
    "detailLevel": "none",
    "useCache": True,
    "cacheDir": "",
    "ignoreCache": False,
    "spccSensor": "",
}
REMOVED = ("views", "marsPath", "autoUpdate")

MAS_WARNING = "MultiscaleAdaptiveStretch is not available in Siril; using the Siril GHS stretch instead"
MLDENOISE_WARNING = "MLDenoise is not available in Siril; denoise is off"


def _fresh() -> dict:
    return json.loads(json.dumps(config.defaults()))


def _migrate(cfg: dict, **kw):
    warnings: list = []
    out = config.migrate_config(cfg, warnings.append, **kw)
    return (out if out is not None else cfg), warnings


def _write(cfg_dir: Path, obj) -> Path:
    path = Path(config.config_path(str(cfg_dir)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    return path


def test_defaults_equal_loom_s_for_every_kept_field() -> None:
    d = config.defaults()
    for key, value in EXPECTED_DEFAULTS.items():
        assert key in d, key
        assert d[key] == value, key
        assert type(d[key]) is type(value), key
    for key in REMOVED:
        assert key not in d, f"{key} was removed from the port"
    assert set(d) == set(EXPECTED_DEFAULTS)


def test_defaults_are_independent_copies() -> None:
    a = config.defaults()
    a["paths"]["L"] = "x"
    a["palettes"].append("SHO")
    assert config.defaults()["paths"]["L"] == "" and config.defaults()["palettes"] == []


def test_config_path_is_inside_the_loomsiril_folder(tmp_path: Path) -> None:
    assert Path(config.config_path(str(tmp_path))) == tmp_path / "loomsiril" / "loom.json"


def test_missing_file_gives_the_defaults_without_a_warning(tmp_path: Path) -> None:
    warnings: list = []
    assert config.load(str(tmp_path), warnings.append) == config.defaults()
    assert warnings == []


def test_round_trip(tmp_path: Path) -> None:
    cfg = _fresh()
    cfg.update({"stretch": True, "stretchMethod": "ghs", "noiseTool": "Siril denoise", "noiseLevel": "high",
                "palettes": ["SHO", "HOO"], "narrowbandBandwidth": 6.5, "projectName": "M42 ° mosaic",
                "filters": {"R": "Astronomik Red", "G": "Astronomik Green", "B": "Astronomik Blue"},
                "paths": {**cfg["paths"], "L": "/data/L.fits"}, "cacheDir": str(tmp_path / "cache")})
    path = config.save(cfg, str(tmp_path))
    assert Path(path) == Path(config.config_path(str(tmp_path))) and Path(path).is_file()
    warnings: list = []
    back = config.load(str(tmp_path), warnings.append)
    assert back == {**cfg, "ignoreCache": False}
    assert warnings == []


def test_output_folder_is_saved_and_loaded(tmp_path: Path) -> None:
    cfg = _fresh()
    cfg["outputDir"] = "/results/run1"
    cfg["ignoreCache"] = True
    path = config.save(cfg, str(tmp_path))
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert raw["outputDir"] == "/results/run1"
    assert raw.get("ignoreCache", False) is False, "ignore-cache is for one run only"
    warnings: list = []
    back = config.load(str(tmp_path), warnings.append)
    assert back["outputDir"] == "/results/run1"
    assert warnings == []
    assert config.load(str(tmp_path), lambda t: None).get("ignoreCache") is False


def test_save_is_atomic_and_creates_the_folder(tmp_path: Path) -> None:
    target = tmp_path / "fresh" / "config"
    path = Path(config.save(_fresh(), str(target)))
    assert path.is_file()
    assert [p.name for p in path.parent.iterdir()] == ["loom.json"], "no temporary file left behind"
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("{") and json.loads(raw)
    config.save({**_fresh(), "stretch": True}, str(target))
    assert json.loads(path.read_text(encoding="utf-8"))["stretch"] is True
    assert [p.name for p in path.parent.iterdir()] == ["loom.json"]


def test_unknown_keys_are_ignored_with_a_warning_each(tmp_path: Path) -> None:
    _write(tmp_path, {"stretch": True, "bogusOne": 1, "bogusTwo": "x"})
    warnings: list = []
    cfg = config.load(str(tmp_path), warnings.append)
    assert cfg["stretch"] is True and "bogusOne" not in cfg and "bogusTwo" not in cfg
    assert len(warnings) == 2
    assert any("bogusOne" in w for w in warnings) and any("bogusTwo" in w for w in warnings)


def test_wrong_types_fall_back_to_the_default_with_a_warning(tmp_path: Path) -> None:
    _write(tmp_path, {"smoothing": "very", "stretch": "yes", "palettes": "SHO", "noiseLevel": "low"})
    warnings: list = []
    cfg = config.load(str(tmp_path), warnings.append)
    assert cfg["smoothing"] == 0.5 and cfg["stretch"] is False and cfg["palettes"] == []
    assert cfg["noiseLevel"] == "low"
    assert len(warnings) >= 3


def test_corrupt_file_gives_defaults_and_a_warning(tmp_path: Path) -> None:
    _write(tmp_path, "{ this is not json")
    warnings: list = []
    assert config.load(str(tmp_path), warnings.append) == config.defaults()
    assert len(warnings) == 1
    _write(tmp_path, "[1, 2, 3]")
    warnings.clear()
    assert config.load(str(tmp_path), warnings.append) == config.defaults()
    assert warnings


def test_partial_files_keep_the_other_defaults(tmp_path: Path) -> None:
    _write(tmp_path, {"sharpenTool": "BlurXTerminator"})
    cfg = config.load(str(tmp_path), lambda t: None)
    assert cfg["sharpenTool"] == "BlurXTerminator"
    assert {k: v for k, v in cfg.items() if k != "sharpenTool"} == {k: v for k, v in EXPECTED_DEFAULTS.items() if k != "sharpenTool"}


# ---- migrations Loom already had


def test_gradient_tool_comes_from_the_old_checkbox() -> None:
    cfg = _fresh(); cfg.update({"gradientTool": "", "useGraXpert": True})
    assert _migrate(cfg)[0]["gradientTool"] == "GraXpert"
    cfg = _fresh(); cfg.update({"gradientTool": "", "useGraXpert": False})
    assert _migrate(cfg)[0]["gradientTool"] == "none"
    cfg = _fresh(); cfg.update({"gradientTool": "", "useGraXpert": True, "smoothing": 0.5})
    assert _migrate(cfg)[0]["gradientTool"] == "GraXpert"
    cfg = _fresh(); cfg["gradientTool"] = "SyQon Studio Deep Gradient"
    assert _migrate(cfg)[0]["gradientTool"] == "SyQon Studio Deep Gradient"
    cfg = _fresh(); cfg["gradientTool"] = "none"
    assert _migrate(cfg)[0]["gradientTool"] == "none"


def test_old_studio_prism_name_becomes_essential_or_two_point_zero() -> None:
    cfg = _fresh(); cfg["noiseTool"] = "SyQon Studio Prism"
    out, _ = _migrate(cfg)
    assert out["noiseTool"] in ("SyQon Studio Prism Essential", "SyQon Studio Prism 2.0")
    assert out["noiseTool"] != "SyQon Studio Prism"


def test_unsupported_prism2_strengths_load_as_medium() -> None:
    cfg = _fresh(); cfg.update({"noiseTool": "SyQon Studio Prism 2.0", "noiseLevel": "low", "stretch": True})
    assert _migrate(cfg)[0]["noiseLevel"] == "medium"  # Low is not offered
    cfg = _fresh(); cfg.update({"noiseTool": "SyQon Studio Prism 2.0", "noiseLevel": "high", "stretch": False})
    assert _migrate(cfg)[0]["noiseLevel"] == "medium"  # High needs the stretch
    cfg = _fresh(); cfg.update({"noiseTool": "SyQon Studio Prism 2.0", "noiseLevel": "high", "stretch": True})
    assert _migrate(cfg)[0]["noiseLevel"] == "high"
    cfg = _fresh(); cfg.update({"noiseTool": "NoiseXTerminator", "noiseLevel": "low", "noiseLevelL": "high"})
    out = _migrate(cfg)[0]
    assert (out["noiseLevel"], out["noiseLevelL"]) == ("low", "high")
    cfg = _fresh(); cfg.update({"noiseTool": "SyQon Studio Prism 2.0", "noiseLevel": "medium", "noiseLevelL": "low", "stretch": True})
    assert _migrate(cfg)[0]["noiseLevelL"] == "medium"


def test_retired_studio_parallax_correct_only_becomes_bxt_with_levels_off() -> None:
    cfg = _fresh(); cfg.update({"sharpenTool": "SyQon Studio Parallax (correct only)", "starReduction": "high", "detailLevel": "medium"})
    out = _migrate(cfg)[0]
    assert (out["sharpenTool"], out["starReduction"], out["detailLevel"]) == ("BlurXTerminator", "none", "none")


def test_current_choices_are_left_alone() -> None:
    cfg = _fresh(); cfg.update({"sharpenTool": "SyQon Parallax", "starReduction": "high", "noiseTool": "SyQon Prism",
                                "starTool": "StarNet2", "stretch": True, "stretchMethod": "ghs"})
    before = json.loads(json.dumps(cfg))
    out, warnings = _migrate(cfg)
    assert out == before and warnings == []


# ---- the port's own migrations


def test_replaced_stretch_method_becomes_ghs_with_a_warning() -> None:
    cfg = _fresh(); cfg.update({"stretch": True, "stretchMethod": "MultiscaleAdaptiveStretch"})
    out, warnings = _migrate(cfg)
    assert out["stretchMethod"] == "ghs"
    assert warnings == [MAS_WARNING]


def test_removed_denoise_tool_becomes_none_with_a_warning() -> None:
    cfg = _fresh(); cfg.update({"noiseTool": "MLDenoise", "noiseLevel": "high"})
    out, warnings = _migrate(cfg)
    assert out["noiseTool"] == "none"
    assert warnings == [MLDENOISE_WARNING]


def test_migrations_apply_when_loading_a_file(tmp_path: Path) -> None:
    _write(tmp_path, {"stretch": True, "stretchMethod": "MultiscaleAdaptiveStretch", "noiseTool": "MLDenoise"})
    warnings: list = []
    cfg = config.load(str(tmp_path), warnings.append)
    assert cfg["stretchMethod"] == "ghs" and cfg["noiseTool"] == "none"
    assert MAS_WARNING in warnings and MLDENOISE_WARNING in warnings


def test_saved_view_sources_are_dropped_with_one_warning(tmp_path: Path) -> None:
    _write(tmp_path, {"savedList": "file\t/data/a.fits\nview\tM42_L\nfile\t/data/b.fits\nview\tM42_R"})
    warnings: list = []
    cfg = config.load(str(tmp_path), warnings.append)
    assert cfg["savedList"].splitlines() == ["file\t/data/a.fits", "file\t/data/b.fits"]
    assert len(warnings) == 1 and "view" in warnings[0].lower()


def test_new_choices_are_accepted_as_saved(tmp_path: Path) -> None:
    cfg = _fresh()
    cfg.update({"noiseTool": "Siril denoise", "stretchMethod": "ghs", "spccSensor": "Sony IMX411/455/461/533/571",
                "gradientTool": "none"})
    config.save(cfg, str(tmp_path))
    warnings: list = []
    back = config.load(str(tmp_path), warnings.append)
    assert (back["noiseTool"], back["stretchMethod"], back["gradientTool"]) == ("Siril denoise", "ghs", "none")
    assert back["spccSensor"] == "Sony IMX411/455/461/533/571" and warnings == []
