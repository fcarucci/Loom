"""The single-Hα colour master (slot OSCHA) and the automatic OSC sensor (loomsiril/loom/osc.py).

The oracle replay (test_osc.py) pins the Loom JavaScript's answers; these tests pin the same rules in plain words
and what only Siril has: the INSTRUME to Siril SPCC sensor mapping.
"""

from __future__ import annotations

import pytest

from loomsiril.loom import channels, osc

MATCH = ["Ha", "HA", "ha", "H-alpha", "H-Alpha 3nm", "Halpha", "Hα", "Ha 7nm", "Ha7nm", "Astronomik 6nm Ha",
         "Baader Ha 7nm", "ZWO Ha 7nm", "Optolong H-alpha 7nm", "Antlia 3nm Ha", "  Ha  ", "H_alpha 12 nm"]
NO_MATCH = ["Optolong L-eXtreme", "L-eXtreme", "ALP-T Hα/OIII", "ALP-T", "HA/OIII", "Hα/OIII", "Ha+OIII",
            "Duo-Band", "Dual", "Quad", "Triad", "L-eNhance", "L-Ultimate", "", "Luminance", "Hydrogen", "Sha",
            "Shapley", "Alpha", "Chai", "OIII", "SII", "UVIR", "Hb", "Ha SII", "Haa", None]


@pytest.mark.parametrize("name", MATCH)
def test_a_single_halpha_filter_is_the_halpha_kind(name: str) -> None:
    info = osc.osc_filter_info(name)
    assert info is not None and info["kind"] == "halpha" and info["confident"] is True
    assert info["oiiiNm"] is None


@pytest.mark.parametrize("name", NO_MATCH)
def test_anything_else_is_not_the_halpha_kind(name: str | None) -> None:
    info = osc.osc_filter_info(name)
    assert info is None or info["kind"] != "halpha"


def test_the_bandwidth_of_the_filter_name_is_kept() -> None:
    assert osc.osc_filter_info("Baader Ha 7nm")["haNm"] == 7
    assert osc.osc_filter_info("Antlia 3nm Ha")["haNm"] == 3
    assert osc.osc_filter_info("Ha")["haNm"] is None


def test_dual_band_names_stay_dual_band() -> None:
    assert osc.osc_filter_info("L-eXtreme")["kind"] == "dualband"
    assert osc.osc_filter_info("ALP-T Hα/OIII")["kind"] == "dualband"
    assert osc.osc_filter_info("Duo-Band")["kind"] == "dualband"


def test_the_slot_and_its_recipe() -> None:
    assert osc.COLOUR_SLOTS == ["OSC", "OSCNB", "OSCHA"]
    assert osc.COLOUR_MEMBERS["OSCHA"] == ["H"]
    assert osc.OSC_RECIPES["OSCHA"] == {"H": 0}
    assert "halpha" in osc.OSC_KINDS


def test_a_halpha_master_is_routed_to_its_slot() -> None:
    assert osc.entry_channel("Ha 7nm", 3, "/m/a.fits", {}) == {"channel": "OSCHA", "colour": True,
                                                              "kindSource": "detected"}
    assert osc.effective_osc_kind(None, "Ha", "/m/a.fits", {})["kind"] == "halpha"


def test_a_remembered_kind_beats_the_detection_and_old_values_still_load() -> None:
    key = osc.osc_kind_key("Ha", "/m/a.fits")
    assert osc.effective_osc_kind(None, "Ha", "/m/a.fits", {key: "broadband"})["kind"] == "broadband"
    assert osc.effective_osc_kind(None, "Weird", "/m/a.fits", {osc.osc_kind_key("Weird", ""): "halpha"})["kind"] \
        == "halpha"
    assert osc.effective_osc_kind(None, "Weird", "/m/a.fits", {osc.osc_kind_key("Weird", ""): "dualband"})["kind"] \
        == "dualband"


def test_select_colour_masters_keeps_a_halpha_master_next_to_a_broadband_one() -> None:
    picks = osc.select_colour_masters(
        [{"path": "/m/rgb.fits", "filter": "UVIR", "numberOfChannels": 3, "mtime": 1},
         {"path": "/m/ha.fits", "filter": "Ha 7nm", "numberOfChannels": 3, "mtime": 2}], {})
    assert picks["OSC"]["path"] == "/m/rgb.fits"
    assert picks["OSCHA"]["path"] == "/m/ha.fits"
    assert picks["OSCNB"] is None and picks["unresolved"] == []


def test_the_halpha_master_supplies_only_h() -> None:
    cfg = {"paths": {"OSCHA": "/m/ha.fits"}}
    assert osc.colour_supplied(cfg) == ["OSCHA"]
    assert osc.supplied_channels(cfg) == ["H"]
    assert osc.supplied_channels({"paths": {"OSC": "/m/a", "OSCHA": "/m/h"}}) == ["R", "G", "B", "H"]


def test_halpha_and_dual_band_are_refused_because_both_supply_h() -> None:
    cfg = {"paths": {"OSCHA": "/m/ha.fits", "OSCNB": "/m/nb.fits"}}
    problem = osc.osc_combination_problem(cfg)
    assert problem is not None and "ha.fits" in problem and "nb.fits" in problem and "both" in problem
    assert osc.osc_combination_problem({"paths": {"OSCHA": "/m/ha.fits", "OSC": "/m/rgb.fits"}}) is None
    assert osc.osc_combination_problem({"paths": {"OSCNB": "/m/nb.fits", "OSC": "/m/rgb.fits"}}) is None
    assert osc.osc_combination_problem({"paths": {"OSCHA": "/m/ha.fits"}}) is None


def test_colour_and_mono_are_still_not_mixed_with_a_halpha_master() -> None:
    problem = osc.colour_mix_problem({"paths": {"OSCHA": "/m/ha.fits", "R": "/m/r.fits"}})
    assert problem is not None and "OSCHA=ha.fits" in problem and "R=r.fits" in problem
    assert osc.colour_mix_problem({"paths": {"OSCHA": "/m/ha.fits", "H": "/m/h.fits"}}) is not None


def test_sho_and_hso_are_refused_with_a_halpha_master() -> None:
    assert osc.osc_palette_problem({"paths": {"OSCHA": "/m/ha.fits"}, "palettes": ["SHO"]}) is not None
    assert osc.osc_palette_problem({"paths": {"OSCHA": "/m/ha.fits"}, "palettes": ["HOO"]}) is None


def test_the_kind_must_match_the_slot() -> None:
    cfg = {"paths": {"OSCHA": "/m/rgb.fits"}}
    info = {"OSCHA": {"path": "/m/rgb.fits", "filter": "UVIR", "numberOfChannels": 3}}
    problems = osc.osc_kind_problems(cfg, info)
    assert len(problems) == 1 and "broadband" in problems[0] and "Hα" in problems[0]
    cfg = {"paths": {"OSC": "/m/ha.fits"}}
    problems = osc.osc_kind_problems(cfg, {"OSC": {"path": "/m/ha.fits", "filter": "Ha", "numberOfChannels": 3}})
    assert len(problems) == 1 and "Hα" in problems[0]
    cfg = {"paths": {"OSCHA": "/m/ha.fits"}}
    assert osc.osc_kind_problems(cfg, {"OSCHA": {"path": "/m/ha.fits", "filter": "Ha 7nm",
                                                 "numberOfChannels": 3}}) == []
    assert cfg["oscKinds"] == {"OSCHA": "halpha"}


def test_a_halpha_master_needs_no_sensor_and_no_sensor_row() -> None:
    assert osc.osc_sensor_problems({"paths": {"OSCHA": "/m/ha.fits"}}) == []
    state = osc.osc_control_state({"paths": {"OSCHA": "/m/ha.fits"}})
    assert state["sensorRowVisible"] is False and state["monoEnabled"] is False
    assert state["rgbFilterRowsVisible"] is True


def test_a_halpha_master_is_registered_like_a_mono_h() -> None:
    plan = osc.osc_registration_plan(["H"], {"OSCHA": ["H"]})
    assert plan["ref"] == "H" and plan["identity"] == ["H"] and plan["groups"] == []
    plan = osc.osc_registration_plan(["R", "G", "B", "H"], {"OSC": ["R", "G", "B"], "OSCHA": ["H"]})
    assert plan["ref"] == "G"
    assert plan["groups"] == [{"slot": "OSCHA", "members": ["H"], "planes": ["H", "H", "H"]}]


def test_halpha_palette_sources_are_not_a_dual_band_line() -> None:
    assert osc.osc_palette_sources({"paths": {"OSCHA": "/m/ha.fits"}}, {"OSCHA": {"name": "Ha"}}, "HOO") == ""


# ---- the automatic sensor -----------------------------------------------------------------------------------------

SENSORS = [
    ("ZWO ASI2600MC Pro", "Sony IMX571"), ("ZWO ASI2600MC Air", "Sony IMX571"), ("ZWO ASI2600MC-Duo", "Sony IMX571"),
    ("ZWO  ASI2600MC   Pro", "Sony IMX571"), ("zwo asi2600mc pro", "Sony IMX571"), ("QHY268C", "Sony IMX571"),
    ("QHYCCD-Cameras-Capture (QHY268C-123)", "Sony IMX571"), ("  ZWO ASI2600MC Pro  ", "Sony IMX571"),
    ("ZWO ASI6200MC Pro", "Sony IMX455"), ("QHY600C", "Sony IMX455"), ("ZWO ASI533MC Pro", "Sony IMX533"),
    ("ZWO ASI294MC Pro", "Sony IMX294"), ("ZWO ASI585MC", "Sony IMX585"), ("ZWO ASI2400MC Pro", "Sony IMX410"),
    ("ZWO ASI678MC", "Sony IMX678"), ("ZWO ASI676MC", "Sony IMX676"), ("ZWO ASI662MC", "Sony IMX662"),
    ("ZWO ASI462MC", "Sony IMX462"), ("ZWO ASI183MC Pro", "Sony IMX183"), ("ZWO ASI178MC", "Sony IMX178"),
    ("ZWO ASI1600MC Pro", "Panasonic MN34230"), ("IMX571", "Sony IMX571"),
]
NO_SENSOR = ["ZWO ASI094MC Pro", "IMX461", "ZWO ASI2600MM Pro", "QHY268M", "ZWO ASI533MM Pro", "ZWO ASI294MM Pro", "", "   ", None, "Camera 1",
             "ZWO ASI9999MC", "Atik 16200", "ASI2600"]


@pytest.mark.parametrize("instrume,sensor", SENSORS)
def test_a_known_colour_camera_picks_its_siril_sensor(instrume: str, sensor: str) -> None:
    pick = osc.auto_osc_sensor(instrume)
    assert pick["sensor"] == sensor
    assert instrume.strip().split()[0] in pick["reason"] or instrume.strip() in pick["reason"]


@pytest.mark.parametrize("instrume", NO_SENSOR)
def test_a_mono_unknown_or_missing_camera_picks_nothing(instrume: str | None) -> None:
    pick = osc.auto_osc_sensor(instrume)
    assert pick["sensor"] == ""
    assert "unknown" in pick["reason"] or "no INSTRUME" in pick["reason"] or "mono" in pick["reason"]


def test_every_sensor_the_table_names_is_in_the_siril_database() -> None:
    import json
    from pathlib import Path
    root = Path.home() / "Library/Application Support/org.siril.Siril/siril-spcc-database/osc_sensors"
    if not root.is_dir():
        pytest.skip("no Siril SPCC database here")
    models = set()
    for f in root.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        for s in data if isinstance(data, list) else data.get("sensors", [data]):
            if isinstance(s, dict) and s.get("type") == "OSC_SENSOR":
                models.add(s.get("model"))
    assert {sensor for _pattern, sensor in osc.OSC_SENSOR_TABLE} <= models


def test_two_masters_with_different_cameras_pick_nothing() -> None:
    assert osc.common_osc_sensor(["ZWO ASI2600MC Pro", "ZWO ASI2600MC Pro"])["sensor"] == "Sony IMX571"
    assert osc.common_osc_sensor(["ZWO ASI2600MC Pro", None, ""])["sensor"] == "Sony IMX571"
    mixed = osc.common_osc_sensor(["ZWO ASI2600MC Pro", "ZWO ASI533MC Pro"])
    assert mixed["sensor"] == "" and "differ" in mixed["reason"]
    assert osc.common_osc_sensor([None])["sensor"] == ""


def test_channels_do_not_regress_for_mono() -> None:
    assert channels.spcc_sensor_for("ZWO ASI2600MM Pro") == "Sony IMX411/455/461/533/571"


def test_resolve_osc_sensor_prefers_the_manual_choice_and_logs_the_reason() -> None:
    cfg = {"paths": {"OSC": "/m/rgb.fits"}, "oscSensor": {"sensor": "", "frontFilter": "", "lpf": ""}}
    note = osc.resolve_osc_sensor(cfg, "ZWO ASI2600MC Pro")
    assert cfg["oscSensor"]["sensor"] == "Sony IMX571"
    assert "ZWO ASI2600MC Pro" in note and "Sony IMX571" in note and "automatic" in note
    cfg = {"paths": {"OSC": "/m/rgb.fits"}, "oscSensor": {"sensor": "Sony IMX585", "frontFilter": "", "lpf": ""}}
    note = osc.resolve_osc_sensor(cfg, "ZWO ASI2600MC Pro")
    assert cfg["oscSensor"]["sensor"] == "Sony IMX585" and "chosen" in note and "Sony IMX585" in note
    cfg = {"paths": {"OSC": "/m/rgb.fits"}, "oscSensor": {"sensor": "", "frontFilter": "", "lpf": ""}}
    note = osc.resolve_osc_sensor(cfg, "Camera 1")
    assert cfg["oscSensor"]["sensor"] == "" and "unknown" in note
    cfg = {"paths": {"OSCNB": "/m/nb.fits"}, "oscSensor": {"sensor": "", "frontFilter": "", "lpf": ""}}
    assert osc.resolve_osc_sensor(cfg, "ZWO ASI2600MC Pro") is None
    assert cfg["oscSensor"]["sensor"] == ""


# ---- the sensor table against Loom's (recorded cases of tools/oracle/loom_osc.js) -------------------------------------

import json
from pathlib import Path

_ORACLE = Path(__file__).resolve().parents[1] / "oracle" / "loom_osc.json"
_SENSOR_CASES = [c for c in json.loads(_ORACLE.read_text(encoding="utf-8"))["cases"].values()
                 if c["fn"] == "sensor_curves_for_camera"]

#: What Loom's table calls a model, and the Siril database models that are the same sensors (Loom lumps the IMX571
#: family and the IMX585/462/662 together because it has one curve set for each; Siril has one entry per sensor).
_SAME_SENSORS = {
    "Sony IMX533": {"Sony IMX533"}, "Sony IMX294": {"Sony IMX294"}, "Sony IMX676": {"Sony IMX676"},
    "Sony IMX585/462/662": {"Sony IMX585", "Sony IMX462", "Sony IMX662"},
    "Sony IMX571 family": {"Sony IMX571", "Sony IMX455"},
}
#: Cameras Loom's table lumps in with a family but whose sensor Siril's database does not hold: Siril picks nothing.
_LOOM_ONLY = ("asi094mc", "imx461")


def test_the_sensor_cases_were_recorded() -> None:
    assert len(_SENSOR_CASES) >= 50
    assert any(c["result"] is None for c in _SENSOR_CASES) and any(c["result"] is not None for c in _SENSOR_CASES)


@pytest.mark.parametrize("case", _SENSOR_CASES, ids=lambda c: repr(c["args"][0]))
def test_siril_knows_every_camera_loom_knows_and_names_the_same_sensor(case: dict) -> None:
    camera = case["args"][0]
    loom = case["result"]
    siril = osc.auto_osc_sensor(camera)["sensor"]
    norm = osc.normalise_filter(camera)
    if loom is None:
        return   # Siril knows more cameras than Loom: its database has an entry per sensor (see the next test)
    if any(token in norm for token in _LOOM_ONLY):
        assert siril == ""
    else:
        assert siril in _SAME_SENSORS[loom["model"]], f"{camera!r}: Loom {loom['model']}, Siril {siril!r}"


def test_a_camera_only_siril_knows_is_a_colour_model_with_a_database_entry() -> None:
    extra = [c["args"][0] for c in _SENSOR_CASES
             if c["result"] is None and osc.auto_osc_sensor(c["args"][0])["sensor"]]
    assert extra, "the recorded cameras include ones only Siril's table knows"
    for camera in extra:
        assert "mm" not in osc.normalise_filter(camera) and not osc.normalise_filter(camera).endswith("m")
