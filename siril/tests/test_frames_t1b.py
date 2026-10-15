"""The Frame Selector's grouping, anomaly flags and file lists equal Loom's JavaScript on every recorded case.

tests/oracle/fs_flags.json holds, for each case, the arguments and the JS result (recorded by tools/oracle/fs_flags.js
through tools/record_oracle.js in node). Each case is replayed here on loomsiril.frames_groups, frames_flags and
frames_files and compared: floats to 1e-9 relative, the rest exactly. Dict key order is not compared (the JS object
sorts integer-like keys first). The suite needs neither node nor Loom.

The guard tests at the end show that the rules the cases exercise (the sigma floor, the minimum frame count, the
zero star count) each matter: with a rule weakened, the replay of the cases that need it must disagree.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Callable

import pytest

from loomsiril import frames_files as Fi
from loomsiril import frames_flags as Fl
from loomsiril import frames_groups as Fg
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_flags.json"
if not PATH.exists():
    pytest.skip("oracle fs_flags.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]

UNDEFINED = "__undefined__"

#: The JS name of each function the recorder calls, to its Python port.
DISPATCH: dict[str, Callable[..., Any]] = {
    "notSubframe": Fg.not_subframe, "calibrationKind": Fg.calibration_kind, "skippedCounts": Fg.skipped_counts,
    "skippedLine": Fg.skipped_line, "unreadableLine": Fg.unreadable_line,
    "noSubframesMessage": Fg.no_subframes_message, "groupByFilter": Fg.group_by_filter,
    "autoRejectAllowed": Fg.auto_reject_allowed, "normalImageType": Fg.normal_image_type,
    "comparability": Fg.comparability,
    "obsTime": Fl.obs_time, "airmass": Fl.airmass, "isNumber": Fl.is_number, "lowerQuartile": Fl.lower_quartile,
    "flagBaseline": Fl.flag_baseline, "flagHigh": Fl.flag_high, "corrected": Fl.corrected,
    "flagReferences": Fl.flag_references, "blurCause": Fl.blur_cause, "isCloud": Fl.is_cloud,
    "timeOrder": Fl.time_order, "anomalyFlags": Fl.anomaly_flags, "flagSummary": Fl.flag_summary,
    "airmassLine": Fl.airmass_line, "rowTooltip": Fl.row_tooltip, "frameTags": Fl.frame_tags,
    "digestCacheEntry": Fi.digest_cache_entry, "digestCacheStored": Fi.digest_cache_stored,
    "digestCacheHit": Fi.digest_cache_hit, "digestRacy": Fi.digest_racy,
    "shortNames": Fi.short_names, "commonPrefix": Fi.common_prefix, "lastSeparatorIn": Fi.last_separator_in,
    "outputName": Fi.output_name, "needingXisf": Fi.needing_xisf, "byDirectory": Fi.by_directory,
    "destinationIsSource": Fi.destination_is_source, "emptyRefusal": Fi.empty_refusal,
    "withoutTrailingSlash": Fi.without_trailing_slash, "outputMapping": Fi.output_mapping,
    "stripFirst": Fi.strip_first, "tileAt": Fi.tile_at, "thumbnailOrder": Fi.thumbnail_order,
    "batchesOf": Fi.batches_of, "measuringLines": Fi.measuring_lines,
    "File.extractDirectory": Fi.extract_directory, "File.extractExtension": Fi.extract_extension,
    "File.extractName": Fi.extract_name,
}

#: Functions that change their arguments in JS; the recorded output is the first argument afterwards.
MUTATING: dict[str, Callable[..., Any]] = {"digestCacheRecord": Fi.digest_cache_record}

#: Constants of the JS, by name.
CONSTANTS: dict[str, Any] = {
    "FLAG_K": Fl.FLAG_K, "FLAG_MIN_FRAMES": Fl.FLAG_MIN_FRAMES, "DROPPED_FRACTION": Fl.DROPPED_FRACTION,
    "BLUR_MARGIN": Fl.BLUR_MARGIN, "SEEING_AIRMASS_POWER": Fl.SEEING_AIRMASS_POWER, "EXTINCTION": Fl.EXTINCTION,
    "CLOUD_DIMMING": Fl.CLOUD_DIMMING, "CLOUD_BACKGROUND_RISE": Fl.CLOUD_BACKGROUND_RISE,
    "FLAG_ORDER": Fl.FLAG_ORDER, "FLAG_TAG": Fl.FLAG_TAG, "FLAG_BADGE": Fl.FLAG_BADGE,
    "FLAG_SUMMARY_ORDER": Fl.FLAG_SUMMARY_ORDER,
}


def same(expected: Any, actual: Any, where: str = "") -> None:
    """Assert equal as the JS would see them: floats to 1e-9 relative, bools never equal to numbers."""
    if expected == UNDEFINED:
        assert actual is None, where
        return
    if isinstance(expected, bool) or isinstance(actual, bool):
        assert expected is actual, where
    elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if math.isnan(expected):
            assert math.isnan(actual), where
        else:
            assert actual == pytest.approx(expected, rel=1e-9, abs=1e-12), where
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), where
        for k, (e, a) in enumerate(zip(expected, actual)):
            same(e, a, f"{where}[{k}]")
    elif isinstance(expected, dict):
        assert isinstance(actual, dict), where
        assert set(actual) == set(expected), f"{where}: keys {sorted(actual)}"
        for k in expected:
            same(expected[k], actual[k], f"{where}.{k}")
    else:
        assert expected == actual, where


def replay(case: dict[str, Any]) -> Any:
    """Run one recorded call on the Python port, on a copy of its arguments."""
    name = case["fn"]
    args = copy.deepcopy(case["args"])
    if name == "mergeBatch":
        measured = args[2]
        return {"ret": Fi.merge_batch(args[0], args[1], measured), "measured": measured}
    if name in MUTATING:
        MUTATING[name](*args)
        return args[0]
    return DISPATCH[name](*args)


@pytest.mark.parametrize("key", sorted(CASES))
def test_case_matches_js(key: str) -> None:
    case = CASES[key]
    if "value" in case:
        same(case["out"], CONSTANTS[case["value"]], key)
        return
    same(case["out"], replay(case), key)


def test_every_js_function_is_ported() -> None:
    """The recorder calls only functions that have a Python port (or are merged into one of the cases above)."""
    called = {c["fn"] for c in CASES.values() if "fn" in c}
    assert called <= set(DISPATCH) | set(MUTATING) | {"mergeBatch"}


def test_thumbnail_order_is_a_permutation() -> None:
    """Every index appears exactly once, whatever the selection, the window or where it sits."""
    for count in range(0, 12):
        for selected in range(-1, count):
            for first in range(0, max(1, count)):
                for visible in range(1, 5):
                    order = Fi.thumbnail_order(count, selected, first, visible)
                    assert sorted(order) == list(range(count))


# ---- guards: a weakened rule must make a recorded case disagree -------------------------------------------

def _mismatch(key: str) -> bool:
    try:
        same(CASES[key]["out"], replay(CASES[key]), key)
    except AssertionError:
        return True
    return False


def test_guard_sigma_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the 1% floor, a baseline with sigma below the floor counts as spread."""
    monkeypatch.setattr(Fl, "SIGMA_FLOOR_FRACTION", 0.0)
    assert _mismatch("flag_baseline/sigma_below_floor")


def test_guard_minimum_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the minimum, four frames are enough to flag anything."""
    monkeypatch.setattr(Fl, "FLAG_MIN_FRAMES", 3)
    assert _mismatch("anomaly_flags/too_few_frames") or _mismatch("flag_baseline/too_few")


def test_guard_zero_star_count_is_a_reading() -> None:
    """A star count of zero is a real reading: read as excluded, the recorded stars baseline no longer matches."""
    case = copy.deepcopy(CASES["flag_baseline/zero_allowed"])
    case["args"][2] = False
    with pytest.raises(AssertionError):
        same(case["out"], replay(case), "zero excluded")


def test_guard_dropped_is_not_cloud() -> None:
    """A dropped star count alone is DROPPED, never CLOUD: the cloud rule reads flux and background only."""
    assert CASES["anomaly_flags/dropped_star_count"]["out"][11] == ["dropped"]
    assert Fl.anomaly_flags(*CASES["anomaly_flags/dropped_star_count"]["args"])[11] == ["dropped"]


def test_guard_comparability_missing_key_is_not_null() -> None:
    """A missing key and a null value are different values: a group mixing them is a mixed group."""
    light = {"exposure": 300, "binning": 1, "binningY": 1, "imageType": "Light Frame", "width": 1, "height": 1,
             "calibrated": True}
    missing = [dict(light), {k: v for k, v in light.items() if k != "binning"}]
    nulls = [dict(light, binning=None), dict(light, binning=None)]
    mixed = [dict(light), dict(light, binning=None)]
    assert Fg.comparability(missing) == {"uniform": False, "problems": ["mixed binning"]}
    assert Fg.comparability(nulls) == {"uniform": True, "problems": []}
    assert Fg.comparability(mixed) == {"uniform": False, "problems": ["mixed binning"]}
