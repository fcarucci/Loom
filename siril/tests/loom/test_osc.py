"""OSC detection, colour-master selection, the remembered-kind key and the colour group's cache keys (loomsiril/loom/osc.py).

The oracle replay checks every recorded case of tests/oracle/loom_osc.json against the port (floats to 1e-9
relative, everything else exactly). The other tests pin what the oracle cannot: the Siril-only helpers and the
coverage guards.
"""

from __future__ import annotations

import copy
import inspect
import json
import math
from pathlib import Path
from typing import Any

import pytest

from loomsiril.loom import osc

ORACLE_FILE = Path(__file__).resolve().parents[1] / "oracle" / "loom_osc.json"

#: Recorded, but replayed with the white-balance port: the one recorded function with no twin here.
DEFERRED = {"clean_white_balance_key"}

#: Public functions of osc.py that have no JavaScript counterpart.
SIRIL_ONLY = {"colour_run", "js_config"}


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
