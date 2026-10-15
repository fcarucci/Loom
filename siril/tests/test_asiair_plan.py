"""Flat batches, flat matching, the import root and the copy manifest equal Loom's JavaScript (AsiairNames.js 333-703).

tests/oracle/fs_asiair_plan.json holds, for each case, the arguments and the JS result (recorded by
tools/oracle/fs_asiair_plan.js through tools/record_oracle.js in node). Each case is replayed here on
loomsiril.asiair_names and compared: floats to 1e-9 relative, NaN equal to NaN, the rest exactly. The suite needs
neither node nor Loom.

The recorded paths for the master-flats check are relative to siril/, so every test runs from that folder. The
manifest cases were recorded with the JavaScript's ``.xisf`` names, so they replay with ``extension=".xisf"``; the
tests at the end check the default, a byte copy that keeps each source's own extension.

The guard tests at the end show that the present-and-equal rule, the path-component containment and the root
folder rule each change an outcome the JavaScript pins.
"""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

import pytest

from loomsiril import asiair_names as A

SIRIL_ROOT = Path(__file__).resolve().parents[1]
PATH = SIRIL_ROOT / "tests" / "oracle" / "fs_asiair_plan.json"
if not PATH.exists():
    pytest.skip("oracle fs_asiair_plan.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]


def _manifest_xisf(approved_lights: Any, flat_matches: Any, destination: Any) -> Any:
    """The JavaScript's manifest: every name written with the ``.xisf`` extension."""
    return A.manifest(approved_lights, flat_matches, destination, extension=".xisf")


#: The JS name of each function the recorder calls, to its Python port.
DISPATCH: dict[str, Callable[..., Any]] = {
    "flatBatches": A.flat_batches, "assignBatches": A.assign_batches, "attributeFit": A.attribute_fit,
    "mustMatch": A.must_match, "flatSuits": A.flat_suits, "matchFlats": A.match_flats, "isInside": A.is_inside,
    "importRoot": A.import_root, "exposureList": A.exposure_list, "darkSource": A.dark_source,
    "masterFlatsProblem": A.master_flats_problem, "importSummary": A.import_summary,
    "skipFiltersWithFailedCopies": A.skip_filters_with_failed_copies, "manifest": _manifest_xisf,
}


@pytest.fixture(autouse=True)
def _run_from_siril_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(SIRIL_ROOT)


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


# ---- the manifest's extension parameter ----------------------------------------------------------------------

def test_default_extension_is_a_byte_copy() -> None:
    out = A.manifest([{"path": "/s/Light_O_1.FITS", "filter": "OIII"}],
                     [{"filter": "OIII", "flats": [{"path": "/f/Flat_O_1.fit"}]}], "/v")
    assert out["lights"] == [{"src": "/s/Light_O_1.FITS", "dst": "/v/Light/Light_O_1.FITS"}]
    assert out["flats"] == [{"src": "/f/Flat_O_1.fit", "dst": "/v/Flat/Flat_O_1.fit", "filter": "OIII"}]


def test_xisf_extension_replaces_the_source_extension() -> None:
    out = A.manifest([{"path": "/s/Light_O_1.FITS", "filter": "OIII"}], [], "/v", extension=".xisf")
    assert out["lights"] == [{"src": "/s/Light_O_1.FITS", "dst": "/v/Light/Light_O_1.xisf"}]


def test_default_extension_still_refuses_a_clash() -> None:
    out = A.manifest([{"path": "/a/L.fit", "filter": "H"}, {"path": "/b/l.fit", "filter": "H"}], [], "/v")
    assert out["lights"] == [] and out["flats"] == []
    assert [c["dst"] for c in out["collisions"]] == ["/v/Light/l.fit"]


# ---- paths and the root folder, component by component -----------------------------------------------------

@pytest.mark.parametrize("path, root, inside", [
    ("/Volumes/ASIAIR/Light/x.fit", "/Volumes/ASIAIR", True),
    ("/Volumes/ASIAIR-backup/x", "/Volumes/ASIAIR", False),
    ("/Volumes/ASIAIR/", "/Volumes/ASIAIR/", True),
    ("/Volumes/ASIAIR", "/Volumes/ASIAIR//", True),
])
def test_is_inside_compares_components(path: str, root: str, inside: bool) -> None:
    assert A.is_inside(path, root) is inside


@pytest.mark.parametrize("chosen, root, name", [
    ("/Volumes/ASIAIR/Light/", "/Volumes/ASIAIR", "Light"),
    ("/Volumes/ASIAIR/Lights", "/Volumes/ASIAIR/Lights", None),
    ("/Light", "/Light", None),
])
def test_import_root_names_the_parent_of_light_and_flat(chosen: str, root: str, name: str | None) -> None:
    assert A.import_root(chosen) == {"root": root, "chosen": name}


# ---- guards: each one must change an outcome the JavaScript pins ------------------------------------------

def _mismatches(keys: list[str]) -> list[str]:
    return [k for k in keys if _replay(k) is not None]


def test_present_and_equal_guard_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["flatSuits/filter_and_binning_both_absent_refused"]
    assert _mismatches(keys) == []
    monkeypatch.setattr(A, "must_match", lambda a, b: a == b)
    assert set(_mismatches(keys)) == set(keys), "without the present check two absent fields would agree"


def test_component_containment_guard_matters() -> None:
    """A character-prefix test would call each of these inside; the JavaScript says they are not."""
    keys = ["isInside/sibling_sharing_prefix", "isInside/sibling_numeric_suffix", "isInside/windows_drive_sibling"]
    assert _mismatches(keys) == []
    for k in keys:
        path, root = CASES[k]["args"]
        assert str(path).startswith(str(root)) is True and CASES[k]["out"] is False, k


def test_root_folder_rule_guard_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["importRoot/plural_is_someones_folder", "importRoot/prefix_is_not_a_match", "importRoot/light_folder"]
    assert _mismatches(keys) == []
    monkeypatch.setattr(A, "_ROOT_FOLDER", re.compile(r"(?:[lL]ight|[fF]lat|[lL]ights)\Z"))
    assert set(_mismatches(keys)) == {"importRoot/plural_is_someones_folder"}, "Lights would be taken as a Light folder"


def test_clash_refusal_guard_matters(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["manifest/clash_plan_and_autorun", "manifest/clash_case_insensitive"]
    assert _mismatches(keys) == []
    monkeypatch.setattr(A, "_loose_eq", lambda a, b: True)
    assert set(_mismatches(keys)) == set(keys), "without the clash check the second source would overwrite the first"
