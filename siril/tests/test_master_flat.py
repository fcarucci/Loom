"""The master-flat decision core equals Loom's JavaScript (pixinsight/script/lib/MasterFlat.js, lines 1-447) on every case.

tests/oracle/fs_master_flat.json holds, for each case, the arguments, the fixtures (a settings store, a header table, a
keyword table) and the JS result, recorded by tools/oracle/fs_master_flat.js through tools/record_oracle.js in node.
Each case is replayed here on loomsiril.master_flat with the same fakes and compared: floats to 1e-9 relative, NaN
equal to NaN, the rest exactly. The suite needs neither node nor Loom.

The Siril differences (the file extension of file_name and dark_cache_path) are checked on their own at the end: the
oracle passes ext=".xisf", the JS value, and the plain tests show the ".fit" default.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
from typing import Any, Callable

import pytest

from loomsiril import master_flat as mf
from loomsiril.loom.cache import iso_ms
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_master_flat.json"
if not PATH.exists():
    pytest.skip("oracle fs_master_flat.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]

#: The JS name of each function the recorder calls, to its Python port.
DISPATCH: dict[str, Callable[..., Any]] = {
    "loadSettings": mf.load_settings, "saveSettings": mf.save_settings, "active": mf.active,
    "number": mf.number, "describe": mf.describe, "darkKind": mf.dark_kind, "darksOf": mf.darks_of,
    "cardDarks": mf.card_darks, "cardDarkMatch": mf.card_dark_match, "readCardDarks": mf.read_card_darks,
    "darkSourceOf": mf.dark_source_of, "cardDarksUi": mf.card_darks_ui,
    "sameExposure": mf.same_exposure, "compatible": mf.compatible, "darkSuits": mf.dark_suits,
    "chooseDark": mf.choose_dark, "rawGroupKey": mf.raw_group_key, "safeName": mf.safe_name,
    "fileName": mf.file_name, "rejectionFor": mf.rejection_for,
    "darkIntegrationSpec": mf.dark_integration_spec, "flatIntegrationSpec": mf.flat_integration_spec,
    "darkCachePath": mf.dark_cache_path,
}


class _Store:
    """The settings store the recorder fakes: values by key, refused reads and writes, and a log of every call."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.values: dict[str, Any] = dict(spec.get("values", {}))
        self.throw_read = set(spec.get("throwRead", []))
        self.throw_write = set(spec.get("throwWrite", []))
        self.calls: list[list[Any]] = []

    def read(self, key: str, data_type: str) -> Any:
        self.calls.append(["read", key, data_type])
        if key in self.throw_read:
            raise RuntimeError("store read refused")
        return self.values.get(key)

    def write(self, key: str, data_type: str, value: Any) -> None:
        self.calls.append(["write", key, data_type, value])
        if key in self.throw_write:
            raise RuntimeError("store write refused")
        self.values[key] = value


class _Header:
    """A header as the recorder's readHeader gives it: ``info`` is None when unreadable; keyword(name) is None when absent."""

    def __init__(self, entry: dict[str, Any]) -> None:
        self.info = {} if entry["info"] else None
        self._keywords = entry["keywords"]

    def keyword(self, name: str) -> Any:
        return self._keywords.get(name)


def _reader(headers: dict[str, Any]) -> Callable[[str], Any]:
    def read_header(path: str) -> Any:
        return _Header(headers[path]) if path in headers else None

    return read_header


def _keyword(table: dict[str, Any]) -> Callable[[str], Any]:
    return lambda name: table.get(name)


def _call(case: dict[str, Any]) -> Any:
    name = case["fn"]
    args = copy.deepcopy(case.get("args", []))
    if "store" in case:
        store = _Store(case["store"])
        result = DISPATCH[name](store, *args)
        return {"result": result, "values": store.values, "calls": store.calls}
    if "keywords" in case:
        return DISPATCH[name](*args, _keyword(case["keywords"]))
    if "headers" in case:
        return DISPATCH[name](*args, _reader(case["headers"]))
    if "ext" in case:
        return DISPATCH[name](*args, ext=case["ext"])
    return DISPATCH[name](*args)


def _mismatch(expected: Any, actual: Any, where: str = "out") -> str | None:
    """Where ``actual`` first differs from the JS ``expected``, or None when they agree."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return None if expected is actual else f"{where}: {expected!r} != {actual!r}"
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if math.isnan(expected) or math.isnan(actual):
            return None if (math.isnan(expected) and math.isnan(actual)) else f"{where}: {expected!r} != {actual!r}"
        if isinstance(expected, float) or isinstance(actual, float):
            return None if math.isclose(expected, actual, rel_tol=1e-9, abs_tol=0.0) else f"{where}: {expected!r} != {actual!r}"
        return None if expected == actual else f"{where}: {expected!r} != {actual!r}"
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
    return None if expected == actual else f"{where}: {expected!r} != {actual!r}"


def _replay(key: str) -> str | None:
    case = CASES[key]
    if "value" in case:
        return _mismatch(case["out"], getattr(mf, case["value"]), key)
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
    assert len(CASES) >= 60


# ---- the Siril differences and the settings round trip ----------------------------------------------

def test_file_name_defaults_to_siril_fit_extension() -> None:
    assert mf.file_name("Ha", 2) == "masterFlat_Ha_bin2.fit"
    assert mf.file_name("S II", None) == "masterFlat_S_II.fit"


def test_dark_cache_path_defaults_to_siril_fit_extension() -> None:
    group = {"key": "e2000b1g120"}
    sources = [{"path": "/card/Dark/dark_one.fit", "size": 4096, "modified": "2026-09-18T20:00:00.000Z"}]
    fit = mf.dark_cache_path(group, sources, "/work/cache")
    assert fit.endswith(".fit")
    assert fit == mf.dark_cache_path(group, sources, "/work/cache", ext=".fit")
    assert fit[:-len(".fit")] == mf.dark_cache_path(group, sources, "/work/cache", ext=".xisf")[:-len(".xisf")]


def test_save_then_load_round_trip() -> None:
    store = _Store({})
    mf.save_settings(store, "/darks/night", True)
    assert mf.load_settings(store) == {"darksFolder": "/darks/night", "onlyMasterFlats": True}


def test_image_extension_is_end_anchored_and_case_blind() -> None:
    assert mf.IMAGE_EXTENSION.search("L_001.XISF")
    assert mf.IMAGE_EXTENSION.search("c.fits")
    assert mf.IMAGE_EXTENSION.search("d.fts")
    assert not mf.IMAGE_EXTENSION.search("a.xisf.txt")
    assert not mf.IMAGE_EXTENSION.search("e.fz")


def test_file_info_of_reads_a_real_file(tmp_path) -> None:
    f = tmp_path / "dark_one.fit"
    f.write_bytes(b"x" * 1234)
    os.utime(f, (1700000000.0, 1700000000.0))
    size, modified = mf.file_info_of(str(f))
    assert size == 1234
    assert modified == "2023-11-14T22:13:20.000Z"
    assert modified == iso_ms(os.stat(f).st_mtime)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", modified)
    assert mf.file_info_of(str(tmp_path / "gone.fit")) == (None, None)
