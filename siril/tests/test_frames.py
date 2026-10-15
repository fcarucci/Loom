"""The Frame Selector decision core equals Loom's JavaScript (pixinsight/script/lib/Frames.js) on every recorded case.

tests/oracle/fs_core.json holds, for each case, the arguments and the JS result (recorded by tools/oracle/fs_core.js
through tools/record_oracle.js in node). Each case is replayed here on loomsiril.frames and compared: floats to 1e-9
relative, NaN equal to NaN, the rest exactly. The suite needs neither node nor Loom.

The guard tests at the end show that the sigma floor, MIN_FRAMES and the clamp each matter: with the guard removed
the replay of the cases that exercise it must disagree with the JavaScript.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Callable

import pytest

from loomsiril import frames as F
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_core.json"
if not PATH.exists():
    pytest.skip("oracle fs_core.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]

#: The JS name of each function the recorder calls, to its Python port.
DISPATCH: dict[str, Callable[..., Any]] = {
    "metricsFromRow": F.metrics_from_row, "optionalOk": F.optional_ok, "optionalProblems": F.optional_problems,
    "sanitizeOptional": F.sanitize_optional, "storedMetrics": F.stored_metrics, "cacheEntryUsable": F.cache_entry_usable,
    "metricInRange": F.metric_in_range, "isWholeCount": F.is_whole_count, "looksLikeCount": F.looks_like_count,
    "meaningProblems": F.meaning_problems, "metricValid": F.metric_valid, "frameValid": F.frame_valid,
    "median": F.median, "sigma": F.sigma, "gate": F.gate, "validOnly": F.valid_only, "columnOf": F.column_of,
    "relativeGates": F.relative_gates, "medians": F.medians, "score": F.score,
    "defaultSettings": F.default_settings, "copyOf": F.copy_of, "applyPreset": F.apply_preset,
    "acceptedBand": F.accepted_band, "relativeBand": F.relative_band, "narrowBand": F.narrow_band,
    "relativeFailures": F.relative_failures, "absoluteFailures": F.absolute_failures, "autoGates": F.auto_gates,
    "verdict": F.verdict, "parseLimit": F.parse_limit, "formatLimit": F.format_limit, "limitValue": F.limit_value,
    "withLimit": F.with_limit, "displayLimit": F.display_limit, "round": F.round_display,
    "finalState": F.final_state, "leftOut": F.left_out, "keepCount": F.keep_count, "counts": F.counts,
    "summaryLine": F.summary_line, "canEdit": F.can_edit, "nextPhase": F.next_phase,
    # the manifest's `created` stamp is the clock, so only its entries are compared
    "buildManifest": lambda rows: {"entries": F.build_manifest(rows)["entries"]},
    "manifestPending": F.manifest_pending, "recordOutcome": F.record_outcome,
    "identityMatches": F.identity_matches, "newChannel": F.new_channel, "recompute": F.recompute,
    "judgeRow": F.judge_row,
}

#: JS functions that change their arguments; the JS result is the return value, else the first argument.
MUTATORS = {"sanitizeOptional", "recordOutcome", "recompute", "judgeRow"}

UNDEFINED_MARK = "__undefined__"


def _call(case: dict[str, Any]) -> Any:
    name = case["fn"]
    args = copy.deepcopy(case["args"])
    ret = DISPATCH[name](*args)
    if name in MUTATORS:
        return args[0] if ret is None else ret
    return ret


def _mismatch(expected: Any, actual: Any, where: str = "out") -> str | None:
    """Where ``actual`` first differs from the JS ``expected``, or None when they agree."""
    if expected == UNDEFINED_MARK:
        return None if actual is F.UNDEFINED else f"{where}: JS undefined, Python {actual!r}"
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
        return _mismatch(case["out"], getattr(F, case["value"]), key)
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
    assert len(CASES) >= 380


# ---- the guards: each one must change an outcome the JavaScript pins ------------------------------

def _mismatches(keys: list[str]) -> list[str]:
    return [k for k in keys if _replay(k) is not None]


def test_sigma_floor_guard_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["gate/floor_below_one_percent", "gate/mad_zero_degenerate"]
    assert _mismatches(keys) == []
    monkeypatch.setattr(F, "SIGMA_FLOOR_FRACTION", 0.0)
    assert set(_mismatches(keys)) == {"gate/floor_below_one_percent"}, "without the floor a 0.74 spread gates"


def test_min_frames_guard_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["relative_gates/nine_valid_no_gates", "relative_gates/ten_with_one_invalid_no_gates"]
    assert _mismatches(keys) == []
    monkeypatch.setattr(F, "MIN_FRAMES", 3)
    assert set(_mismatches(keys)) == set(keys), "without MIN_FRAMES nine frames would clip"


def test_clamp_guard_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["score/clamp_low_psfsnr", "score/clamp_high_ecc", "score/clamp_high_stars"]
    assert _mismatches(keys) == []
    monkeypatch.setattr(F, "CLAMP", {"lo": 0.0, "hi": 1e300})
    assert set(_mismatches(keys)) == set(keys), "without the clamp an outlier term swamps the score"
