"""The Frame Selector plot geometry equals Loom's JavaScript (pixinsight/script/lib/Frames.js 613-724) on every recorded case.

tests/oracle/fs_plot.json holds, for each case, the arguments and the JS result (recorded by tools/oracle/fs_plot.js
through tools/record_oracle.js in node). Each case is replayed here on loomsiril.frames_plot and compared: floats to
1e-9 relative, NaN equal to NaN, the rest exactly. The suite needs neither node nor Loom.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Callable

import pytest

from loomsiril import frames_plot as P
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_plot.json"
if not PATH.exists():
    pytest.skip("oracle fs_plot.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]

#: The JS name of each function the recorder calls, to its Python port.
DISPATCH: dict[str, Callable[..., Any]] = {
    "pointAt": P.point_at, "spacedLabels": P.spaced_labels, "plotBounds": P.plot_bounds,
    "metricColumn": P.metric_column, "displayValue": P.display_value,
}

#: The recorded constants, by their JS name.
CONSTANTS: dict[str, Any] = {"SCORE_COLUMN": P.SCORE_COLUMN}


def _call(case: dict[str, Any]) -> Any:
    args = copy.deepcopy(case["args"])
    return DISPATCH[case["fn"]](*args)


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
    if expected is None or actual is None:
        return None if expected is actual else f"{where}: {expected!r} != {actual!r}"
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return None if expected == actual else f"{where}: {expected!r} != {actual!r}"
    return None if expected == actual else f"{where}: {expected!r} != {actual!r}"


def _replay(key: str) -> str | None:
    case = CASES[key]
    if "value" in case:
        return _mismatch(case["out"], CONSTANTS[case["value"]], key)
    return _mismatch(case["out"], _call(case), key)


@pytest.mark.parametrize("key", sorted(CASES))
def test_case_matches_js(key: str) -> None:
    bad = _replay(key)
    assert bad is None, bad


def test_every_recorded_function_is_ported() -> None:
    """A new function in the recorder must get a port and a row here, or the suite stops."""
    called = {c["fn"] for c in CASES.values() if "fn" in c}
    assert called <= set(DISPATCH), sorted(called - set(DISPATCH))


def test_recorder_is_the_expected_size() -> None:
    assert len(CASES) >= 40


def test_click_outside_is_rejected_before_rounding() -> None:
    """A click in the left margin must not round back to the first frame (the JS comment says so)."""
    assert P.point_at(5, 10, 100, 10) == -1
    assert P.point_at(10, 10, 100, 10) == 0
