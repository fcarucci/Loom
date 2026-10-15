"""The night picker's data equals Loom's JavaScript (pixinsight/script/lib/NightDialog.js, lines 1-119) on every recorded case.

tests/oracle/fs_night_survey.json holds, for each case, the arguments and the JS result (recorded by
tools/oracle/fs_night_survey.js through tools/record_oracle.js in node). Each case is replayed here on
loomsiril.night_dialog and compared: floats to 1e-9 relative, NaN equal to NaN, the rest exactly. The suite needs
neither node nor Loom, and nothing here imports tkinter.

One recorded case is a deliberate deviation (``DEVIATIONS``): the JavaScript leaves a target named like an inherited
object property out of ``targets``, and the port lists it. The recorder stays the JavaScript reference; the replay
expects the port's list for that case and checks that the JavaScript differs from it in that field only.

The guard tests at the end show that the deviation and the default of RECENT each change an outcome.
"""

from __future__ import annotations

import ast
import copy
import json
import math
from pathlib import Path
from typing import Any, Callable

import pytest

from loomsiril import night_dialog as N
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_night_survey.json"
if not PATH.exists():
    pytest.skip("oracle fs_night_survey.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]

MODULE_PATH = Path(N.__file__)

#: The JS name of each function the recorder calls, to its Python port.
DISPATCH: dict[str, Callable[..., Any]] = {
    "clockOf": N.clock_of, "rowFor": N.row_for, "flatSummary": N.flat_summary, "surveyOf": N.survey_of,
    "recentNights": N.recent_nights, "flatsForNight": N.flats_for_night,
}

UNDEFINED_MARK = "__undefined__"

#: Cases where the port deliberately differs from the recorded JavaScript: expected output as the port gives it.
DEVIATIONS: dict[str, Callable[[Any], Any]] = {
    # A target named like an inherited object property is listed (the JS drops it from `targets`).
    "survey/target_names_of_object_properties": lambda out: {**out, "targets": ["constructor", "toString", "M42"]},
}


def _call(case: dict[str, Any]) -> Any:
    args = copy.deepcopy(case["args"])
    return DISPATCH[case["fn"]](*args)


def _mismatch(expected: Any, actual: Any, where: str = "out") -> str | None:
    """Where ``actual`` first differs from the JS ``expected``, or None when they agree."""
    if expected == UNDEFINED_MARK:
        return None if actual is None else f"{where}: JS undefined, Python {actual!r}"
    if isinstance(expected, bool) or isinstance(actual, bool):
        return None if expected is actual else f"{where}: {expected!r} != {actual!r}"
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        if math.isnan(expected) or math.isnan(actual):
            return None if (math.isnan(expected) and math.isnan(actual)) else f"{where}: {expected!r} != {actual!r}"
        return None if math.isclose(expected, actual, rel_tol=1e-9, abs_tol=0.0) else f"{where}: {expected!r} != {actual!r}"
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{where}: JS object, Python {type(actual).__name__}"
        if set(expected) != set(actual):
            return f"{where}: keys {sorted(set(expected) ^ set(actual))} differ"
        for k in expected:
            bad = _mismatch(expected[k], actual[k], f"{where}.{k}")
            if bad:
                return bad
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return f"{where}: list length {len(expected)} vs {len(actual) if isinstance(actual, list) else type(actual).__name__}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            bad = _mismatch(e, a, f"{where}[{i}]")
            if bad:
                return bad
        return None
    if expected is None or actual is None:
        return None if expected is actual else f"{where}: {expected!r} != {actual!r}"
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return None if expected == actual else f"{where}: {expected!r} != {actual!r}"
    return None if expected == actual else f"{where}: {expected!r} != {actual!r}"


def _replay(key: str, deviations: bool = True) -> str | None:
    case = CASES[key]
    if "value" in case:
        return _mismatch(case["out"], getattr(N, case["value"]), key)
    expected = case["out"]
    if deviations and key in DEVIATIONS:
        expected = DEVIATIONS[key](expected)
    return _mismatch(expected, _call(case), key)


@pytest.mark.parametrize("key", sorted(CASES))
def test_case_matches_js(key: str) -> None:
    bad = _replay(key)
    assert bad is None, bad


def test_every_recorded_function_is_ported() -> None:
    """A new function in the recorder must get a port and a row here, or the suite stops."""
    called = {c["fn"] for c in CASES.values() if "fn" in c}
    assert called <= set(DISPATCH), sorted(called - set(DISPATCH))


def test_recorder_is_the_expected_size() -> None:
    assert len(CASES) >= 30


def test_every_js_function_has_a_case() -> None:
    called = {c["fn"] for c in CASES.values() if "fn" in c}
    assert called == set(DISPATCH)
    assert "const/RECENT" in CASES


# ---- what the cases pin, stated directly --------------------------------------------------------------

def test_clock_of_is_hours_and_minutes() -> None:
    assert N.clock_of("20260807-215716") == "21:57"


def test_flat_summary_wording() -> None:
    flats = [{"filter": "L", "flats": [1], "strength": "exact"}]
    assert N.flat_summary(flats)[0]["text"] == "L: 1 flat"
    weak = [{"filter": "Ha", "flats": [1, 2], "strength": "weak"}]
    assert N.flat_summary(weak)[0]["text"] == "Ha: 2 flats (partial match)"
    missing = [{"filter": "OIII", "flats": [], "strength": "missing"}]
    out = N.flat_summary(missing)[0]
    assert out["text"] == "OIII: no flats" and out["count"] == 0


def _survey() -> dict[str, Any]:
    return copy.deepcopy(CASES["survey/four_nights_two_targets"]["out"])


def test_card_lists_each_target_once_with_its_nights() -> None:
    survey = _survey()
    assert survey["targets"] == ["M42", "IC 1396A"]
    assert [(n["target"], n["date"]) for n in survey["nights"]][:2] == [("M42", "2026-08-07"), ("IC 1396A", "2026-08-07")]


def test_recent_nights_keeps_the_newest_three() -> None:
    survey = _survey()
    got = N.recent_nights(survey, "M42", None)
    assert [n["date"] for n in got] == ["2026-08-10", "2026-08-09", "2026-08-08"]
    assert N.recent_nights(survey, "M42", 1)[0]["date"] == "2026-08-10"
    assert N.recent_nights(survey, "M42", 0) == got


def test_recent_nights_negative_k_slices_from_the_end() -> None:
    survey = _survey()
    assert len(N.recent_nights(survey, "M42", -1)) == 3


def test_recent_default_is_three() -> None:
    assert N.RECENT == 3


def test_flats_are_owned_by_their_session_and_orphans_by_none() -> None:
    survey = _survey()
    night = next(n for n in survey["nights"] if n["target"] == "M42" and n["date"] == "2026-08-08")
    flats = N.flats_for_night(survey, night)
    assert flats and all(f["stamp"].startswith("20260808") or f["stamp"].startswith("20260807") for f in flats)
    orphan = CASES["survey/lights_only_no_flats"]["out"]
    assert N.flats_for_night(orphan, orphan["nights"][0]) == []


def test_flat_batch_on_the_midpoint_goes_to_the_earlier_session() -> None:
    out = CASES["survey/batch_on_midpoint_goes_to_earlier"]["out"]
    assert list(out["flatsBySession"]) == ["0"]


def test_unparseable_names_are_carried_through() -> None:
    out = CASES["survey/four_nights_two_targets"]["out"]
    assert out["unparseable"] == CASES["survey/four_nights_two_targets"]["args"][0]["unparseable"]


def test_unreadable_stamps_are_not_in_any_night() -> None:
    out = CASES["survey/unreadable_stamps_excluded"]["out"]
    assert sum(n["count"] for n in out["nights"]) == 2


def test_no_tkinter_in_this_module() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert not [n for n in names if n.split(".")[0] in ("tkinter", "_tkinter")], names


def test_docstring_names_each_ported_function_with_its_lines() -> None:
    doc = N.__doc__ or ""
    for expected in ("clockOf", "rowFor", "flatSummary", "surveyOf", "RECENT", "recentNights", "flatsForNight"):
        assert expected in doc, expected
    for lines in ("24-27", "33-41", "48-64", "71-99", "106", "107-113", "116-119"):
        assert lines in doc, lines


# ---- the guards: each one must change an outcome the JavaScript pins ----------------------------------

def _mismatches(keys: list[str]) -> list[str]:
    return [k for k in keys if _replay(k) is not None]


def test_plain_object_names_stay_in_targets() -> None:
    scan = copy.deepcopy(CASES["survey/target_names_of_object_properties"]["args"][0])
    assert N.survey_of(scan, None)["targets"] == ["constructor", "toString", "M42"]


def test_recorded_js_differs_from_port_only_in_targets() -> None:
    case = CASES["survey/target_names_of_object_properties"]
    assert case["out"]["targets"] == ["M42"], "the JavaScript drops inherited-property names from targets"
    got = _call(case)
    assert {k for k in got if got[k] != case["out"][k]} == {"targets"}


def test_recent_nights_of_object_property_target_matches_js() -> None:
    assert _replay("recent_nights/constructor_target") is None


def test_plain_object_quirk_is_the_only_deviation() -> None:
    assert _replay("survey/target_names_of_object_properties") is None
    bad = _replay("survey/target_names_of_object_properties", deviations=False)
    assert bad is not None and "targets" in bad, "without the deviation the replay disagrees on targets"


# ---- hand-built cards, independent of the recorder ---------------------------------------------------------

def _hand_scan(nights: int) -> dict[str, Any]:
    """`nights` nights of one target, each with three lights ten minutes apart, keys in seconds."""
    lights = []
    for i in range(nights):
        base = i * 100_000
        for j in range(3):
            lights.append({"target": "NGC 7000", "filter": "Ha", "stamp": f"2026081{i}-21{j:02d}00",
                           "key": base + j * 600})
    return {"lights": lights, "flats": [], "unparseable": []}


def test_hand_built_card_gives_one_night_per_session() -> None:
    survey = N.survey_of(_hand_scan(4), None)
    assert [n["date"] for n in survey["nights"]] == ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]
    assert survey["targets"] == ["NGC 7000"]


@pytest.mark.parametrize("k, expected", [
    (None, 3), (0, 3), (float("nan"), 3), (2.5, 2), (-1.5, 3), (1, 1), (9, 4), (float("inf"), 4), (float("-inf"), 0),
])
def test_recent_nights_count_for_each_k(k: Any, expected: int) -> None:
    survey = N.survey_of(_hand_scan(4), None)
    assert len(N.recent_nights(survey, "NGC 7000", k)) == expected


def test_recent_nights_newest_first_by_hand() -> None:
    survey = N.survey_of(_hand_scan(4), None)
    assert [n["date"] for n in N.recent_nights(survey, "NGC 7000", 2)] == ["2026-08-13", "2026-08-12"]


def test_hand_built_object_property_targets_are_kept() -> None:
    scan = {"lights": [{"target": "__proto__", "filter": "L", "stamp": "20260807-210000", "key": 1000},
                       {"target": "hasOwnProperty", "filter": "L", "stamp": "20260807-230000", "key": 2000}],
            "flats": [], "unparseable": []}
    survey = N.survey_of(scan, None)
    assert survey["targets"] == ["__proto__", "hasOwnProperty"]
    assert len(N.recent_nights(survey, "__proto__", None)) == 1


def test_hand_built_flats_follow_the_session() -> None:
    scan = _hand_scan(2)
    scan["flats"] = [{"filter": "Ha", "stamp": "20260812-050000", "key": 100_000 + 2 * 600 + 3600 * 6}]
    survey = N.survey_of(scan, None)
    night = survey["nights"][0]
    assert N.flats_for_night(survey, night) == []
    assert N.flats_for_night(survey, survey["nights"][1]) == survey["flatsBySession"]["1"]
    assert len(N.flats_for_night(survey, survey["nights"][1])) == 1


# ---- the guards: each one must change an outcome ----------------------------------------------------------

def _mismatches(keys: list[str]) -> list[str]:
    return [k for k in keys if _replay(k) is not None]


def test_recent_default_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["recent_nights/default_three_newest_first", "recent_nights/zero_falls_back_to_recent"]
    assert _mismatches(keys) == []
    monkeypatch.setattr(N, "RECENT", 2)
    assert set(_mismatches(keys)) == set(keys), "a default of two shows one night too few"
