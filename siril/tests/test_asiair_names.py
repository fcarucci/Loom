"""The ASIAIR name parser, sessions and nights equal Loom's JavaScript (pixinsight/script/lib/AsiairNames.js, lines 1-331).

tests/oracle/fs_asiair_names.json holds, for each case, the arguments and the JS result (recorded by
tools/oracle/fs_asiair_names.js through tools/record_oracle.js in node). Each case is replayed here on
loomsiril.asiair_names and compared: floats to 1e-9 relative, NaN equal to NaN, the rest exactly. The suite needs
neither node nor Loom.

The guard tests at the end show that the calendar checks and the four-hour split each change an outcome the
JavaScript pins.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Callable

import pytest

from loomsiril import asiair_names as A
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_asiair_names.json"
if not PATH.exists():
    pytest.skip("oracle fs_asiair_names.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]

#: The JS name of each function the recorder calls, to its Python port.
DISPATCH: dict[str, Callable[..., Any]] = {
    "parseName": A.parse_name, "stampKey": A.stamp_key, "sessions": A.sessions, "nights": A.nights,
    "filtersOf": A.filters_of, "byTarget": A.by_target, "dateOf": A.date_of,
}


def _mismatch(expected: Any, actual: Any, where: str = "out") -> str | None:
    """Where ``actual`` first differs from the JS ``expected``, or None when they agree."""
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
    return None if expected == actual else f"{where}: {expected!r} != {actual!r}"


def _replay(key: str) -> str | None:
    case = CASES[key]
    args = copy.deepcopy(case["args"])
    return _mismatch(case["out"], DISPATCH[case["fn"]](*args), key)


@pytest.mark.parametrize("key", sorted(CASES))
def test_case_matches_js(key: str) -> None:
    bad = _replay(key)
    assert bad is None, bad


def test_every_recorded_function_is_ported() -> None:
    """A new function in the recorder must get a port and a row here, or the suite stops."""
    called = {c["fn"] for c in CASES.values()}
    assert called <= set(DISPATCH), sorted(called - set(DISPATCH))


def test_recorder_is_the_expected_size() -> None:
    assert len(CASES) >= 60


def test_stamp_key_is_utc_seconds() -> None:
    """The key is the UTC epoch second of the printed wall-clock time, independent of the machine's zone."""
    assert A.stamp_key("19700101-000000") == 0
    assert A.stamp_key("19691231-235959") == -1
    assert A.stamp_key("20260807-215716") == 1786139836


# ---- the guards: each one must change an outcome the JavaScript pins -------------------------------------------

def _mismatches(keys: list[str]) -> list[str]:
    return [k for k in keys if _replay(k) is not None]


def test_rolled_date_guard_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["stampKey/31_february_refused", "stampKey/leap_day_2026_rolled_refused"]
    assert _mismatches(keys) == []
    monkeypatch.setattr(A, "stamp_rolled", lambda ms, y, mo, d: False)
    assert set(_mismatches(keys)) == set(keys), "without the rolled-date check February 31 would be filed as 3 March"


def test_four_hour_split_guard_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["sessions/gap_exactly_4h_one_session", "sessions/gap_4h_plus_1s_splits"]
    assert _mismatches(keys) == []
    monkeypatch.setattr(A, "GAP_HOURS", 5)
    assert set(_mismatches(keys)) == {"sessions/gap_4h_plus_1s_splits"}, "with a five-hour gap 4h+1s must not split"


def test_appledouble_sidecar_is_refused() -> None:
    name = "._Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit"
    assert A.parse_name(name) is None
