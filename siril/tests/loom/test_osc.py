"""OSC detection, colour-master selection, the remembered-kind key, the colour group's cache keys, registration
plan, cache action, sensor checks, dialog state and the LOOMSRC texts of exports (loomsiril/loom/osc.py).

The oracle replay checks every recorded case of tests/oracle/loom_osc.json against the port (floats to 1e-9
relative, everything else exactly). The other tests pin what the oracle cannot: the Siril-only helpers and the
coverage guards.
"""

from __future__ import annotations

import copy
import inspect
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from loomsiril.loom import osc

ORACLE_FILE = Path(__file__).resolve().parents[1] / "oracle" / "loom_osc.json"

#: Recorded, but replayed with the white-balance port: the one recorded function with no twin here.
DEFERRED = {"clean_white_balance_key"}

#: Public functions of osc.py that have no JavaScript counterpart.
SIRIL_ONLY = {"colour_run", "js_config"}


#: Loom functions that are recorded for nothing and not ported, with the reason.
SKIPPED = {
    "effectiveFilters": "Siril has no per-channel SPCC, so there are no per-channel filters to resolve",
    "oscSplitKeywords": "sets FITS keywords on PixInsight windows; Siril's twin lives with the registration stage",
    "oscRegisterGroup": "drives PixInsight windows and the PixInsight cache; Siril's twin lives with the registration stage",
    "cropCleanChannel": "crops a PixInsight view loaded from the cache; Siril's twin lives with the registration stage",
    "registrationReference": "reads PixInsight windows and keywords; Siril's twin lives with the registration stage",
    "logPaletteSources": "writes the run log (Util.log); the line it logs is osc_palette_sources, ported",
    "setSource": "sets LOOMSRC on a PixInsight result window; Siril stamps its exports through its own stages",
    "stampSources": "stamps LOOMSRC on PixInsight result windows; the texts it stamps are ported as osc_source_text",
    "loomSourceKeywords": "reads the keywords of a PixInsight window; the TIFF and PSB clones are PixInsight-only",
}

#: The functions this part of the port adds, with the fewest recorded cases each must keep.
NEW_FUNCTIONS = {
    "osc_group_cache_action": 300, "osc_registration_plan": 250, "clean_registration_plan": 150,
    "osc_channel_groups": 15, "osc_sensor_problems": 500, "osc_control_state": 12,
    "osc_palette_sources": 600, "osc_channel_source": 20, "osc_source_text": 100,
}


def _parse_constant(name: str) -> float:
    return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}[name]


def _load_cases() -> dict[str, Any]:
    with ORACLE_FILE.open(encoding="utf-8") as fh:
        return json.load(fh, parse_constant=_parse_constant)["cases"]


CASES = _load_cases()


def _same(a: Any, b: Any) -> bool:
    """Equality where NaN equals NaN, floats agree to 1e-9 relative, and bools never equal numbers."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=0.0) or a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if a is None or b is None:
        return a is b
    return a == b


def _run(case: dict[str, Any]) -> Any:
    """Call the port with the recorded arguments; returns what it gave (the filled config, when the JS fills one)."""
    fn, args = case["fn"], copy.deepcopy(case["args"])
    target = getattr(osc, fn)
    if not callable(target):
        return target
    if fn in ("input_channel_problems", "osc_kind_problems"):
        config, table = args
        problems = getattr(osc, fn)(config, table)
        if fn == "osc_kind_problems":
            return {"problems": problems, "oscKinds": config.get("oscKinds")}
        return problems
    if fn == "osc_sensor_problems":
        config, table = args
        return osc.osc_sensor_problems(config, table or {})
    if fn == "clean_registration_plan":
        try:
            return target(*args)
        except ValueError as exc:
            return {"throws": str(exc)}
    return target(*args)


@pytest.mark.parametrize("name", sorted(n for n, c in CASES.items() if c["fn"] not in DEFERRED))
def test_oracle_replay(name: str) -> None:
    actual = _run(CASES[name])
    expected = CASES[name]["result"]
    assert _same(actual, expected), f"{name}: port returned {actual!r}, Loom returned {expected!r}"


def test_oracle_covers_every_ported_function() -> None:
    fns = {c["fn"] for c in CASES.values()}
    for fn in sorted(fns - DEFERRED):
        assert hasattr(osc, fn), f"no twin for the recorded function {fn}"
    public = {n for n, v in vars(osc).items()
              if not n.startswith("_") and inspect.isfunction(v) and v.__module__ == osc.__name__}
    assert osc.narrowband_bandwidths is osc.osc_narrowband_bandwidths
    public.discard("narrowband_bandwidths")  # the same function under the stages' name
    assert public - fns - SIRIL_ONLY == set(), "public functions that are neither recorded nor Siril-only"
    assert SIRIL_ONLY <= public
    assert len(CASES) >= 3000


def test_deferred_list_is_the_white_balance_key_alone() -> None:
    assert DEFERRED == {"clean_white_balance_key"}
    assert {c["fn"] for c in CASES.values() if c["fn"] not in DEFERRED} >= {"osc_group_register_key"}
    assert any(c["fn"] == "clean_white_balance_key" for c in CASES.values())
    assert not hasattr(osc, "clean_white_balance_key")


def test_a_table_or_a_callable_gives_the_infos() -> None:
    config = {"paths": {"OSC": "/m/rgb.xisf"}}
    info = {"OSC": {"path": "/m/rgb.xisf", "filter": "UVIR", "numberOfChannels": 3}}
    by_table = copy.deepcopy(config)
    by_call = copy.deepcopy(config)
    assert osc.osc_kind_problems(by_table, info) == []
    assert osc.osc_kind_problems(by_call, lambda slot: info.get(slot)) == []
    assert by_table["oscKinds"] == by_call["oscKinds"] == {"OSC": "broadband"}


def test_a_branded_and_sized_filter_shares_its_models_key() -> None:
    plain = osc.osc_kind_key("L-eXtreme", "/m/a.xisf")
    assert plain == "filter:lextreme"
    for name in ("Optolong L-eXtreme 2\"", "ZWO L-eXtreme", "astronomik l extreme 36mm", "Radian_L-eXtreme"):
        assert osc.osc_kind_key(name, "/m/b.xisf") == plain
    assert osc.osc_kind_key("", "/Masters/A.xisf") == "path:/Masters/A.xisf"
    assert osc.osc_kind_key("", "/masters/a.xisf") != osc.osc_kind_key("", "/Masters/A.xisf")


def test_colour_run_means_a_colour_slot_is_supplied() -> None:
    assert osc.colour_run({"paths": {"OSC": "/m/rgb.xisf"}}) is True
    assert osc.colour_run({"paths": {"OSCNB": "/m/nb.xisf"}}) is True
    assert osc.colour_run({"views": {"OSC": "RGB_View"}}) is True
    assert osc.colour_run({"paths": {"R": "/m/r.xisf", "G": "/m/g.xisf", "B": "/m/b.xisf"}}) is False
    assert osc.colour_run({"paths": {"OSC": ""}}) is False
    assert osc.colour_run({}) is False
    assert osc.colour_run(None) is False


def test_js_config_names_the_sensor_for_all_three_channels() -> None:
    config = {"paths": {"OSC": "/m/rgb.xisf"}, "oscKinds": {"OSC": "broadband"},
              "oscKindChoices": {"filter:duo": "dualband"},
              "oscSensor": {"sensor": "Sony IMX585", "frontFilter": "UV/IR", "lpf": "none"}, "palettes": ["HOO"]}
    before = copy.deepcopy(config)
    js = osc.js_config(config)
    assert js["oscSensor"] == {"R": "Sony IMX585", "G": "Sony IMX585", "B": "Sony IMX585"}
    assert js["oscKinds"] == {"OSC": "broadband"} and js["oscKindChoices"] == {"filter:duo": "dualband"}
    assert js["paths"] == {"OSC": "/m/rgb.xisf"} and js["palettes"] == ["HOO"]
    assert config == before, "js_config must not change its argument"
    js["oscKinds"]["OSC"] = "dualband"
    assert config["oscKinds"] == {"OSC": "broadband"}, "the copy is independent"


def test_js_config_without_a_sensor_leaves_none() -> None:
    assert osc.js_config({"paths": {"R": "/m/r"}}) == {"paths": {"R": "/m/r"}}
    assert "oscSensor" not in osc.js_config({})
    assert osc.js_config(None) == {}
    assert osc.js_config({"oscSensor": {"sensor": ""}})["oscSensor"] == {"R": "", "G": "", "B": ""}


def test_every_new_function_is_recorded_in_depth() -> None:
    counts: dict[str, int] = {}
    for case in CASES.values():
        counts[case["fn"]] = counts.get(case["fn"], 0) + 1
    for fn, least in NEW_FUNCTIONS.items():
        assert counts.get(fn, 0) >= least, f"{fn}: {counts.get(fn, 0)} cases, expected at least {least}"
        assert callable(getattr(osc, fn)), fn


def test_the_registration_throw_is_recorded_and_raised() -> None:
    thrown = [c for c in CASES.values() if c["fn"] == "clean_registration_plan"
              and isinstance(c["result"], dict) and "throws" in c["result"]]
    assert thrown, "the throw text must be recorded"
    with pytest.raises(ValueError, match=r"^internal: with OSC in the run the reference must be its G \(got H\)$"):
        osc.clean_registration_plan("H", {"OSC": ["R", "G", "B"]})
    with pytest.raises(ValueError, match=r"\(got null\)$"):
        osc.clean_registration_plan(None, {"OSC": ["R", "G", "B"]})
    assert osc.clean_registration_plan("G", {"OSC": ["R", "G", "B"]}) == "identity"
    assert osc.clean_registration_plan("H", None) == "mono"
    assert osc.clean_registration_plan("H", {"OSCNB": ["H", "O"]}) == "mono"


def test_the_skipped_functions_are_not_ported() -> None:
    recorded = {c["fn"] for c in CASES.values()}
    for js_name, reason in SKIPPED.items():
        snake = re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), js_name)
        assert reason
        assert not hasattr(osc, snake), f"{snake} is listed as not ported"
        assert snake not in recorded


def test_sensor_curves_come_from_a_callable_a_table_or_nothing() -> None:
    config = {"paths": {"OSC": "/m/rgb.xisf"}, "oscSensor": {"R": "a", "G": "b", "B": "c"}}
    table = {"R": ["a"], "G": [], "B": ["c"]}
    expected = ["sensor colour filter curve 'b' is not in filters.xspd"]
    assert osc.osc_sensor_problems(config, table) == expected
    assert osc.osc_sensor_problems(config, lambda name, channel: name in table[channel]) == expected
    # without a filter database to ask, only the "all three chosen" rule can be checked
    assert osc.osc_sensor_problems(config, None) == []
    assert osc.osc_sensor_problems({"paths": {"OSC": "/m/rgb.xisf"}}, None) == \
        ["choose the sensor colour filters for the colour master (SPCC section)"]


def test_a_colour_run_registers_its_group_to_the_reference() -> None:
    groups = {"OSC": ["R", "G", "B"], "OSCNB": ["H", "O"]}
    plan = osc.osc_registration_plan(["L", "R", "G", "B", "H", "O"], groups)
    assert plan == {"ref": "G", "identity": ["R", "G", "B"], "mono": ["L"],
                    "groups": [{"slot": "OSCNB", "members": ["H", "O"], "planes": ["H", "O", "O"]}]}
    assert osc.osc_registration_plan(["H", "O"], groups)["ref"] == "H"
    assert osc.osc_registration_plan(["L"], None) == {"ref": None, "identity": [], "groups": [], "mono": ["L"]}


def test_the_cache_decides_between_register_split_and_load() -> None:
    on = {"useCache": True, "ignoreCache": False}
    assert osc.osc_group_cache_action(True, [True, True], on) == "load"
    assert osc.osc_group_cache_action(True, [True, False], on) == "split"
    assert osc.osc_group_cache_action(False, [True, True], on) == "register"
    assert osc.osc_group_cache_action(True, [True], {"useCache": True, "ignoreCache": True}) == "register"
    assert osc.osc_group_cache_action(True, [True], {"useCache": False}) == "register"


def test_channel_groups_follow_the_channels_own_group_field() -> None:
    chans = {"R": {"group": "OSC", "plane": 0}, "G": {"group": "OSC", "plane": 1}, "L": {"group": None, "plane": None}}
    assert osc.osc_channel_groups(chans) == {"OSC": ["R", "G"]}
    assert osc.osc_channel_groups({"L": {"group": None}}) is None


def test_control_state_follows_the_colour_slots() -> None:
    state = osc.osc_control_state({"paths": {"OSC": "/m/rgb.xisf"}})
    assert state["monoEnabled"] is False and state["sensorRowVisible"] is True and state["rgbFilterRowsVisible"] is False
    nb = osc.osc_control_state({"paths": {"OSCNB": "/m/nb.xisf"}})
    assert nb["sensorRowVisible"] is False and nb["rgbFilterRowsVisible"] is True and nb["shoHsoEnabled"] is False
    mono = osc.osc_control_state({"paths": {"R": "/m/r.xisf"}})
    assert mono["monoEnabled"] is True and mono["monoReason"] == "" and mono["shoHsoReason"] == ""


def test_the_palette_sources_line_names_the_master_and_both_lines() -> None:
    nb = {"paths": {"OSCNB": "/m/nb.xisf"}}
    assert osc.osc_palette_sources(nb, {"OSCNB": {"name": "L-eXtreme"}}, "HOO") == (
        "HOO: H from OSCNB L-eXtreme (Ha, plus SII for quad-band), O from OSCNB L-eXtreme (OIII with H\u03b2)")
    assert osc.osc_palette_sources(nb, {"OSCNB": {"name": "L-eXtreme"}}, None).startswith("HOO: ")
    assert osc.osc_palette_sources(nb, {"OSCNB": {}}, "HOO").startswith("HOO: H from OSCNB (Ha")
    assert osc.osc_palette_sources(nb, {"OSCNB": {"name": "L-eXtreme"}}, "SHO") == ""
    assert osc.osc_palette_sources({"paths": {"OSC": "/m/rgb.xisf"}}, {}, "HOO") == ""
    assert osc.osc_palette_sources({}, {}, "HOO") == ""


def test_a_channel_names_its_masters_file_and_plane() -> None:
    assert osc.osc_channel_source({"group": "OSC", "path": "C:\\masters\\rgb.xisf", "plane": 1}) == "rgb.xisf|plane:1"
    assert osc.osc_channel_source({"group": "OSC", "path": "/m/rgb.xisf", "plane": 0}) == "rgb.xisf|plane:0"
    assert osc.osc_channel_source({"group": None, "path": "/m/l.xisf", "plane": None}) is None
    assert osc.osc_channel_source(None) is None


def test_a_composite_or_palette_lists_the_planes_of_each_master() -> None:
    chans = {
        "R": {"group": "OSC", "path": "/m/rgb.xisf", "plane": 0}, "G": {"group": "OSC", "path": "/m/rgb.xisf", "plane": 1},
        "B": {"group": "OSC", "path": "/m/rgb.xisf", "plane": 2}, "H": {"group": "OSCNB", "path": "/m/nb.xisf", "plane": 0},
        "O": {"group": "OSCNB", "path": "/m/nb.xisf", "plane": 1}, "L": {"group": None, "path": "/m/l.xisf", "plane": None},
    }
    assert osc.osc_source_text(chans, ["R", "G", "B"], False) == "OSC rgb.xisf planes 0,1,2"
    assert osc.osc_source_text(chans, ["R", "G", "B"], True) == \
        "OSC rgb.xisf plane 0; OSC rgb.xisf plane 1; OSC rgb.xisf plane 2"
    assert osc.osc_source_text(chans, ["H", "O", "O"], True) == "OSCNB nb.xisf plane 0; OSCNB nb.xisf plane 1"
    assert osc.osc_source_text(chans, ["L"], False) is None
    assert osc.osc_source_text(chans, [], False) is None


def test_a_missing_plane_joins_as_an_empty_entry_as_javascript_does() -> None:
    chans = {"Q": {"group": "OSC", "path": "/m/rgb.xisf", "plane": None}}
    assert osc.osc_source_text(chans, ["Q"], False) == "OSC rgb.xisf plane "
    assert osc.osc_source_text(chans, ["Q"], True) == "OSC rgb.xisf plane null"


def test_the_source_helpers_that_touch_windows_are_not_ported() -> None:
    for name in ("log_palette_sources", "set_source", "stamp_sources", "loom_source_keywords"):
        assert not hasattr(osc, name), name
