"""The options module: Loom's defaults, field table and migrations (pinned to the recorded Loom values), and the
file's load and save rules that Siril adds."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from loomsiril.loom import config as cfg

LOOM_TYPE = {"bool": "bool", "string": "str", "double": "float"}
LOOM_ONLY = {"views", "marsPath", "autoUpdate"}
PORT_ONLY = {"spccSensor", "outputDir", "stretchTarget"}
PORT_SHAPE = {"oscSensor"}  # Loom has the key too, in another shape on purpose: Siril SPCC database names


def _cases(oracle: Any) -> dict[str, Any]:
    return oracle("loom_config")["cases"]


def _warnings() -> tuple[list[str], Any]:
    seen: list[str] = []
    return seen, seen.append


def _write(folder: Path, document: Any) -> Path:
    path = Path(cfg.config_path(folder))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
    return path


# ---- defaults and the field table (oracle) -----------------------------------------------------------------------


def test_defaults_keys_are_loom_minus_dropped_plus_port_keys(oracle) -> None:
    loom = _cases(oracle)["defaults"]
    assert set(cfg.defaults()) == (set(loom) - LOOM_ONLY) | PORT_ONLY


def test_defaults_equal_loom_for_every_kept_field(oracle) -> None:
    loom = _cases(oracle)["defaults"]
    port = cfg.defaults()
    for key in set(loom) - LOOM_ONLY - PORT_SHAPE:
        assert port[key] == loom[key], key
    assert port["cacheDir"] == "" and port["spccSensor"] == ""


def test_fields_equal_loom_table_for_kept_names(oracle) -> None:
    loom = _cases(oracle)["fields"]
    assert set(cfg.FIELDS) == (set(loom) - LOOM_ONLY) | PORT_ONLY
    for name, spec in cfg.FIELDS.items():
        if name in PORT_ONLY:
            continue
        assert spec["type"] == LOOM_TYPE[loom[name]["type"]], name
        assert spec["load"] == loom[name]["load"], name
        assert spec["save"] == loom[name]["save"], name


def test_ignore_cache_is_in_defaults_but_never_persisted(tmp_path: Path) -> None:
    c = cfg.defaults()
    c["ignoreCache"] = True
    cfg.save(c, tmp_path)
    assert "ignoreCache" not in json.loads(Path(cfg.config_path(tmp_path)).read_text(encoding="utf-8"))
    assert cfg.load(tmp_path)["ignoreCache"] is False


# ---- migrate_config: every Loom case (oracle) ----------------------------------------------------------------------


def _studio_args(prism2_offered: Any) -> dict[str, bool]:
    if prism2_offered is None:
        return {"studio_found": False}
    return {"studio_found": True, "studio_prism2_unavailable": prism2_offered is False}


@pytest.mark.parametrize(
    "name",
    [
            "migrate_gradient_empty_graxpert_true",
            "migrate_gradient_empty_graxpert_false",
            "migrate_gradient_empty_no_flag",
            "migrate_gradient_studio_kept",
            "migrate_gradient_none_kept",
            "migrate_studio_old_name_prism2_true",
            "migrate_studio_old_name_prism2_false",
            "migrate_studio_essential_prism2_true",
            "migrate_studio_essential_prism2_false",
            "migrate_studio2_prism2_false",
            "migrate_studio2_prism2_undefined",
            "migrate_studio2_stretch_on_high",
            "migrate_studio2_stretch_off_high",
            "migrate_studio2_stretch_off_medium",
            "migrate_studio2_stretch_absent_high",
            "migrate_studio2_stretch_on_low",
            "migrate_studio2_unknown_level",
            "migrate_studio2_level_none",
            "migrate_nxt_low_kept",
            "migrate_prism_high_kept",
            "migrate_none_tool_low_kept",
            "migrate_noiseLevelL_low_studio2_stretch_off",
            "migrate_noiseLevelL_empty_kept",
            "migrate_noiseLevelL_high_stretch_on",
            "migrate_noiseLevelL_absent_nxt",
            "migrate_sharpen_correct_old",
            "migrate_sharpen_syqon_kept",
            "migrate_sharpen_studio_kept",
    ],
)
def test_migrate_config_matches_loom_case(oracle, name: str) -> None:
    case = _cases(oracle)[name]
    seen, warn = _warnings()
    out = cfg.migrate_config(copy.deepcopy(case["input"]), warn, **_studio_args(case["prism2Offered"]))
    assert out == case["output"]
    assert seen == []


def test_every_recorded_migration_case_is_covered(oracle) -> None:
    recorded = {k for k in _cases(oracle) if k.startswith("migrate_")}
    assert len(recorded) >= 25


# ---- migrate_config: the Siril replacements --------------------------------------------------------------------------


def test_multiscale_adaptive_stretch_becomes_ghs_with_warning() -> None:
    seen, warn = _warnings()
    out = cfg.migrate_config({"stretchMethod": "MultiscaleAdaptiveStretch", "noiseTool": "none"}, warn)
    assert out["stretchMethod"] == "ghs"
    assert seen == ["MultiscaleAdaptiveStretch is not available in Siril; using the Siril GHS stretch instead"]


def test_mtf_stretch_is_untouched_and_silent() -> None:
    seen, warn = _warnings()
    assert cfg.migrate_config({"stretchMethod": "mtf"}, warn)["stretchMethod"] == "mtf"
    assert seen == []


def test_mldenoise_becomes_off_with_warning() -> None:
    seen, warn = _warnings()
    out = cfg.migrate_config({"noiseTool": "MLDenoise", "noiseLevel": "high"}, warn)
    assert out["noiseTool"] == "none"
    assert seen == ["MLDenoise is not available in Siril; denoise is off"]


def test_view_lines_are_dropped_with_one_warning() -> None:
    seen, warn = _warnings()
    saved = "view\tM42_L\tfoo\nfile\t/x/y.fits\nview\tM42_R\tbar"
    out = cfg.migrate_config({"savedList": saved}, warn)
    assert out["savedList"] == "file\t/x/y.fits"
    assert len(seen) == 1


def test_saved_list_without_views_is_kept_and_silent() -> None:
    seen, warn = _warnings()
    out = cfg.migrate_config({"savedList": "file\t/x/y.fits"}, warn)
    assert out["savedList"] == "file\t/x/y.fits"
    assert seen == []


def test_gradient_tool_of_is_loom_rule() -> None:
    assert cfg.gradient_tool_of({"gradientTool": "", "useGraXpert": True}) == "GraXpert"
    assert cfg.gradient_tool_of({"gradientTool": "", "useGraXpert": False}) == "none"
    assert cfg.gradient_tool_of({"gradientTool": "none"}) == "none"


def test_supported_noise_level_prism2_ladder() -> None:
    s2 = cfg.NR_TOOL_STUDIO2
    assert cfg.supported_noise_level(s2, "low", True) == "medium"
    assert cfg.supported_noise_level(s2, "high", False) == "medium"
    assert cfg.supported_noise_level(s2, "high", True) == "high"
    assert cfg.supported_noise_level("NoiseXTerminator", "low", False) == "low"
    assert cfg.supported_noise_level(s2, "extreme", True) == "extreme"


# ---- load: missing, corrupt, unknown, typed ---------------------------------------------------------------------


def test_missing_file_gives_defaults_silently(tmp_path: Path) -> None:
    seen, warn = _warnings()
    assert cfg.load(tmp_path, warn) == cfg.defaults()
    assert seen == []


def test_corrupt_json_gives_defaults_with_one_warning(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, "{not json")
    assert cfg.load(tmp_path, warn) == cfg.defaults()
    assert len(seen) == 1 and "could not read" in seen[0]


def test_non_object_json_gives_defaults_with_warning(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, "[1, 2]")
    assert cfg.load(tmp_path, warn) == cfg.defaults()
    assert len(seen) == 1


def test_unknown_keys_are_ignored_with_one_warning_each(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, {"bogusA": 1, "bogusB": "x", "stretch": True})
    out = cfg.load(tmp_path, warn)
    assert out["stretch"] is True
    assert len(seen) == 2
    assert any("bogusA" in s for s in seen) and any("bogusB" in s for s in seen)


def test_wrong_type_uses_default_with_warning(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, {"stretch": "yes", "smoothing": True, "noiseTool": "NoiseXTerminator"})
    out = cfg.load(tmp_path, warn)
    assert out["stretch"] is False and out["smoothing"] == 0.5
    assert out["noiseTool"] == "NoiseXTerminator"
    assert len(seen) == 2


def test_outputdir_is_loaded_without_warning(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, {"outputDir": "/somewhere"})
    out = cfg.load(tmp_path, warn)
    assert out["outputDir"] == "/somewhere"
    assert seen == []


def test_filters_keep_only_rgb(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, {"filters": {"L": "Lum", "R": "Johnson R", "G": "", "B": "Johnson B"}})
    out = cfg.load(tmp_path, warn)
    assert out["filters"] == {"R": "Johnson R", "B": "Johnson B"}
    assert len(seen) == 1


def test_legacy_use_graxpert_checkbox_is_read(tmp_path: Path) -> None:
    _write(tmp_path, {"useGraXpert": True})
    assert cfg.load(tmp_path)["gradientTool"] == "GraXpert"
    _write(tmp_path, {"useGraXpert": False})
    assert cfg.load(tmp_path)["gradientTool"] == "none"


def test_stored_gradient_tool_wins_over_legacy_checkbox(tmp_path: Path) -> None:
    _write(tmp_path, {"gradientTool": "SyQon Studio Deep Gradient", "useGraXpert": True})
    assert cfg.load(tmp_path)["gradientTool"] == "SyQon Studio Deep Gradient"


def test_narrowband_bandwidth_must_be_positive(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, {"narrowbandBandwidth": 0})
    assert cfg.load(tmp_path, warn)["narrowbandBandwidth"] == 3.0
    assert len(seen) == 1
    _write(tmp_path, {"narrowbandBandwidth": 6.5})
    assert cfg.load(tmp_path)["narrowbandBandwidth"] == 6.5


def test_unknown_parallax_family_is_classic(tmp_path: Path) -> None:
    _write(tmp_path, {"parallaxFamily": "dramatic"})
    assert cfg.load(tmp_path)["parallaxFamily"] == "classic"


def test_empty_text_field_takes_default_and_empty_set_field_is_kept(tmp_path: Path) -> None:
    _write(tmp_path, {"noiseLevel": "", "noiseLevelL": ""})
    out = cfg.load(tmp_path)
    assert out["noiseLevel"] == "medium"
    assert out["noiseLevelL"] == ""


def test_load_runs_migrations_with_availability(tmp_path: Path) -> None:
    _write(tmp_path, {"noiseTool": "SyQon Studio Prism", "noiseLevel": "low", "stretch": True})
    out = cfg.load(tmp_path, studio_found=True, studio_prism2_unavailable=False)
    assert out["noiseTool"] == cfg.NR_TOOL_STUDIO2
    assert out["noiseLevel"] == "medium"


# ---- save: atomic write, persisted keys, round trip -----------------------------------------------------------------


def test_config_path_is_under_loomsiril(tmp_path: Path) -> None:
    assert cfg.config_path(tmp_path) == os.path.join(str(tmp_path), "loomsiril", "loom.json")


def test_outputdir_is_persisted(tmp_path: Path) -> None:
    """The dialog reopens with the last output folder (the owner asked for every option of the last run)."""
    c = cfg.defaults()
    c["outputDir"] = "/data/out"
    path = cfg.save(c, tmp_path)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert raw["outputDir"] == "/data/out"
    assert cfg.load(tmp_path)["outputDir"] == "/data/out"


def test_round_trip_preserves_configuration(tmp_path: Path) -> None:
    c = cfg.defaults()
    c.update(
        {
            "stretch": True,
            "stretchMethod": "ghs",
            "noiseTool": cfg.NR_TOOL_STUDIO2,
            "noiseLevel": "high",
            "projectName": "M42",
            "narrowbandBandwidth": 6.5,
            "filters": {"R": "Johnson R"},
            "palettes": ["HOO", "SHO"],
            "paths": {"L": "/a.fits", "R": "", "G": "", "B": "", "H": "", "S": "", "O": "", "OSC": "", "OSCNB": ""},
            "gradientTool": "SyQon Studio Deep Gradient",
            "smoothing": 0.25,
            "cacheDir": "/var/cache/loom",
        }
    )
    cfg.save(c, tmp_path)
    back = cfg.load(tmp_path, studio_found=True, studio_prism2_unavailable=False)
    for key in c:
        if key in ("outputDir", "ignoreCache"):
            continue
        assert back[key] == c[key], key


def test_save_writes_sorted_keys_with_two_space_indent(tmp_path: Path) -> None:
    path = cfg.save(cfg.defaults(), tmp_path)
    text = Path(path).read_text(encoding="utf-8")
    assert text.startswith('{\n  "')
    keys = [line.split('"')[1] for line in text.splitlines() if line.startswith('  "')]
    assert keys == sorted(keys)


def test_save_returns_the_written_path(tmp_path: Path) -> None:
    path = cfg.save(cfg.defaults(), tmp_path)
    assert path == cfg.config_path(tmp_path)
    assert Path(path).is_file()


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path) -> None:
    cfg.save(cfg.defaults(), tmp_path)
    cfg.save(cfg.defaults(), tmp_path)
    folder = Path(cfg.config_path(tmp_path)).parent
    assert sorted(p.name for p in folder.iterdir()) == ["loom.json"]


def test_failed_write_keeps_the_old_file_and_removes_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg.save({**cfg.defaults(), "projectName": "old"}, tmp_path)

    def boom(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cfg.os, "fsync", boom)
    with pytest.raises(OSError):
        cfg.save({**cfg.defaults(), "projectName": "new"}, tmp_path)
    folder = Path(cfg.config_path(tmp_path)).parent
    assert sorted(p.name for p in folder.iterdir()) == ["loom.json"]
    assert cfg.load(tmp_path)["projectName"] == "old"


def test_save_fills_falsy_or_fields_and_derives_gradient_tool(tmp_path: Path) -> None:
    path = cfg.save({"projectName": None, "narrowbandBandwidth": 0, "useGraXpert": True, "gradientTool": ""}, tmp_path)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert raw["projectName"] == ""
    assert raw["narrowbandBandwidth"] == 3.0
    assert raw["gradientTool"] == "GraXpert"
    assert "useGraXpert" not in raw


def test_save_keeps_only_rgb_filters(tmp_path: Path) -> None:
    path = cfg.save({"filters": {"L": "Lum", "R": "Johnson R", "G": ""}}, tmp_path)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert raw["filters"] == {"R": "Johnson R"}


# ---- review fixes: per-run flags, legacy checkbox, missing Studio, edge cases -----------------------------------


def test_per_run_flags_are_never_persisted(tmp_path: Path) -> None:
    c = cfg.defaults()
    c["validateOnly"] = True
    c["keepWindowsOnError"] = True
    path = cfg.save(c, tmp_path)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert "validateOnly" not in raw and "keepWindowsOnError" not in raw
    back = cfg.load(tmp_path)
    assert back["validateOnly"] is False and back["keepWindowsOnError"] is False


def test_per_run_flags_in_a_file_are_dropped_silently(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, {"validateOnly": True, "keepWindowsOnError": True})
    out = cfg.load(tmp_path, warn)
    assert out["validateOnly"] is False and out["keepWindowsOnError"] is False
    assert seen == []


def test_non_bool_legacy_use_graxpert_warns_and_keeps_default(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, {"useGraXpert": 1})
    out = cfg.load(tmp_path, warn)
    assert out["gradientTool"] == "GraXpert"
    assert len(seen) == 1 and "useGraXpert" in seen[0]


def test_essential_name_with_studio_not_found_stays_essential() -> None:
    seen, warn = _warnings()
    out = cfg.migrate_config({"noiseTool": cfg.NR_TOOL_STUDIO, "noiseLevel": "high", "stretch": True}, warn)
    assert out["noiseTool"] == cfg.NR_TOOL_STUDIO
    assert seen == []


def test_old_studio_name_with_studio_not_found_becomes_essential() -> None:
    out = cfg.migrate_config({"noiseTool": cfg.NR_TOOL_STUDIO_OLD, "noiseLevel": "low"})
    assert out["noiseTool"] == cfg.NR_TOOL_STUDIO


def test_saved_list_line_starting_with_view_but_not_view_tab_is_kept() -> None:
    seen, warn = _warnings()
    saved = "viewer\tkept.fits\nview\tdropped\nfile\tx.fits"
    out = cfg.migrate_config({"savedList": saved}, warn)
    assert out["savedList"] == "viewer\tkept.fits\nfile\tx.fits"
    assert len(seen) == 1


def test_noise_level_l_high_with_stretch_off_becomes_medium() -> None:
    config = {"noiseTool": cfg.NR_TOOL_STUDIO2, "noiseLevel": "medium", "noiseLevelL": "high", "stretch": False}
    out = cfg.migrate_config(config, studio_found=True, studio_prism2_unavailable=False)
    assert out["noiseLevelL"] == "medium"


def test_stored_multiscale_stretch_loads_as_ghs_with_warning(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, {"stretchMethod": "MultiscaleAdaptiveStretch", "stretch": False})
    out = cfg.load(tmp_path, warn)
    assert out["stretchMethod"] == "ghs"
    assert seen == ["MultiscaleAdaptiveStretch is not available in Siril; using the Siril GHS stretch instead"]


def test_deeply_nested_file_gives_defaults_with_warning(tmp_path: Path) -> None:
    seen, warn = _warnings()
    _write(tmp_path, "[" * 100000 + "]" * 100000)
    assert cfg.load(tmp_path, warn) == cfg.defaults()
    assert len(seen) == 1


# ---- OSC keys -----------------------------------------------------------------------------------------------------

OSC_SENSOR_DEFAULT = {"sensor": "", "frontFilter": "", "lpf": ""}


def test_osc_defaults_equal_loom_except_the_port_shape(oracle) -> None:
    loom = _cases(oracle)["defaults"]
    port = cfg.defaults()
    for key in ("oscKinds", "oscKindChoices"):
        assert key in loom and port[key] == loom[key] == {}, key
    assert port["paths"]["OSC"] == "" and port["paths"]["OSCNB"] == ""
    assert port["paths"] == loom["paths"]
    assert port["oscSensor"] == OSC_SENSOR_DEFAULT
    assert loom["oscSensor"] != port["oscSensor"]
    assert PORT_SHAPE <= set(loom)


def test_osc_keys_round_trip(tmp_path: Path) -> None:
    c = cfg.defaults()
    c["paths"]["OSC"] = "/data/osc.xisf"
    c["paths"]["OSCNB"] = "/data/oscnb.fits.fz"
    c["oscKindChoices"] = {"k1": "broadband", "k2": "dualband"}
    c["oscSensor"] = {"sensor": "ZWO ASI2600MC", "frontFilter": "Optolong L-Pro", "lpf": ""}
    cfg.save(c, tmp_path)
    seen, warn = _warnings()
    back = cfg.load(tmp_path, warn)
    assert seen == []
    assert back["paths"]["OSC"] == "/data/osc.xisf" and back["paths"]["OSCNB"] == "/data/oscnb.fits.fz"
    assert back["oscKindChoices"] == c["oscKindChoices"]
    assert back["oscSensor"] == c["oscSensor"]


def test_osc_kinds_is_never_persisted(tmp_path: Path) -> None:
    c = cfg.defaults()
    c["oscKinds"] = {"OSC": "broadband"}
    cfg.save(c, tmp_path)
    assert "oscKinds" not in json.loads(Path(cfg.config_path(tmp_path)).read_text(encoding="utf-8"))
    assert cfg.load(tmp_path)["oscKinds"] == {}
    _write(tmp_path, {"oscKinds": {"OSC": "dualband"}})
    seen, warn = _warnings()
    assert cfg.load(tmp_path, warn)["oscKinds"] == {}
    assert seen == []


@pytest.mark.parametrize("stored", [[], "broadband", 3, True])
def test_osc_kind_choices_not_an_object_warns_and_falls_back(tmp_path: Path, stored: Any) -> None:
    _write(tmp_path, {"oscKindChoices": stored})
    seen, warn = _warnings()
    assert cfg.load(tmp_path, warn)["oscKindChoices"] == {}
    assert len(seen) == 1 and "oscKindChoices" in seen[0]


def test_osc_kind_choices_bad_entries_are_dropped_with_a_warning(tmp_path: Path) -> None:
    _write(tmp_path, {"oscKindChoices": {"a": "broadband", "b": "narrowband", "c": 1, "d": "dualband"}})
    seen, warn = _warnings()
    assert cfg.load(tmp_path, warn)["oscKindChoices"] == {"a": "broadband", "d": "dualband"}
    assert len(seen) == 2 and all("oscKindChoices" in s for s in seen)


@pytest.mark.parametrize("stored", [[], "x", 3, True])
def test_osc_sensor_not_an_object_warns_and_falls_back(tmp_path: Path, stored: Any) -> None:
    _write(tmp_path, {"oscSensor": stored})
    seen, warn = _warnings()
    assert cfg.load(tmp_path, warn)["oscSensor"] == OSC_SENSOR_DEFAULT
    assert len(seen) == 1 and "oscSensor" in seen[0]


def test_osc_sensor_unknown_keys_and_non_text_values_are_dropped(tmp_path: Path) -> None:
    _write(tmp_path, {"oscSensor": {"sensor": "IMX571", "R": "x", "lpf": 5, "frontFilter": "UV/IR"}})
    seen, warn = _warnings()
    got = cfg.load(tmp_path, warn)["oscSensor"]
    assert got == {"sensor": "IMX571", "frontFilter": "UV/IR", "lpf": ""}
    assert len(seen) == 2 and all("oscSensor" in s for s in seen)


def test_osc_paths_are_accepted_from_a_file(tmp_path: Path) -> None:
    _write(tmp_path, {"paths": {"OSC": "/a/b.xisf", "OSCNB": "/a/c.xisf"}})
    seen, warn = _warnings()
    got = cfg.load(tmp_path, warn)["paths"]
    assert got["OSC"] == "/a/b.xisf" and got["OSCNB"] == "/a/c.xisf" and seen == []
