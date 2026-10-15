"""The master-flat plan, its report lines and the folder scan equal Loom's JavaScript on every recorded case.

tests/oracle/fs_master_flat_plan.json holds, for each case, the arguments, the fakes (headers, folder tree) and the JS
result, recorded by tools/oracle/fs_master_flat_plan.js through tools/record_oracle.js in node. Each case is replayed
here on loomsiril.master_flat with the same fakes and compared: floats to 1e-9 relative, NaN equal to NaN, the rest
exactly. A scan case writes its folder tree to a temporary directory and scans that. The suite needs neither node nor
Loom.

The Siril differences (the extension of the masters, the folder listing of the disk) are checked on their own at the
end.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import math
import os
from typing import Any, Callable

import pytest

from loomsiril import master_flat as mf
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_master_flat_plan.json"
if not PATH.exists():
    pytest.skip("oracle fs_master_flat_plan.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]

#: The JS name of each function the recorder calls, to its Python port.
DISPATCH: dict[str, Callable[..., Any]] = {
    "buildPlan": mf.build_plan, "darkShortfall": mf.dark_shortfall, "masterFlatNum": mf.master_flat_num,
    "frameLine": mf.frame_line, "darkSummaryLines": mf.dark_summary_lines, "planLines": mf.plan_lines,
    "scanFolder": mf.scan_folder,
}

#: The fake tree of the recorder is rooted here; a replay writes it below the temporary directory instead.
FAKE_ROOT = "/fake"


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


def _to_root(value: Any, root: str) -> Any:
    """A recorded path under the fake root, moved below ``root``; anything else unchanged."""
    if isinstance(value, str) and (value == FAKE_ROOT or value.startswith(FAKE_ROOT + "/")):
        return root + value[len(FAKE_ROOT):]
    return value


def _map_strings(obj: Any, fn: Callable[[Any], Any]) -> Any:
    if isinstance(obj, dict):
        return {_map_strings(k, fn): _map_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_map_strings(v, fn) for v in obj]
    return fn(obj)


def _materialize(tree: dict[str, list[dict[str, Any]]], root: str) -> None:
    """Write a recorded folder tree below ``root``: directories, files of the recorded size and time, and symbolic
    links to directories. A recorded folder reached through a symbolic link is written to a real folder under
    ``root/_targets``, which the link points at; the scan must not follow the link."""
    links = {f"{folder.rstrip('/')}/{e['name']}" for folder, entries in tree.items() for e in entries
             if e["isDirectory"] and e["isSymbolicLink"]}

    def physical(folder: str) -> str:
        key = folder.rstrip("/")
        if key in links:
            return os.path.join(root, "_targets", key[len(FAKE_ROOT) + 1:].replace("/", "_"))
        return _to_root(key, root)

    for folder in tree:
        os.makedirs(physical(folder), exist_ok=True)
    for folder, entries in tree.items():
        base = physical(folder)
        for e in entries:
            path = os.path.join(base, e["name"])
            if e["isDirectory"]:
                if e["isSymbolicLink"]:
                    os.symlink(physical(f"{folder.rstrip('/')}/{e['name']}"), path)
                else:
                    os.makedirs(path, exist_ok=True)
                continue
            with open(path, "wb") as fh:
                fh.write(b"x" * int(e["size"]))
            when = dt.datetime.fromisoformat(e["modified"].replace("Z", "+00:00")).timestamp()
            os.utime(path, (when, when))


def _replay_scan(case: dict[str, Any], root: str) -> Any:
    _materialize(case["tree"], root)
    headers = {_to_root(p, root): entry for p, entry in case["headers"].items()}
    headers = {p: {"info": e["info"], "keywords": e["keywords"]} for p, e in headers.items()}
    folder = _to_root(case["dir"], root)
    args: list[Any] = [folder, _reader(headers)]
    if case.get("depth") is not None:
        args.append(case["depth"])
    return mf.scan_folder(*args)


def _replay(key: str, root: str) -> str | None:
    case = CASES[key]
    if "tree" in case:
        expected = _map_strings(case["out"], lambda v: _to_root(v, root))
        return _mismatch(expected, _replay_scan(case, root), key)
    args = copy.deepcopy(case.get("args", []))
    fn = DISPATCH[case["fn"]]
    if case["fn"] in ("buildPlan", "darkShortfall"):
        if "ext" in case:
            result = fn(*args, ext=case["ext"])
        else:
            result = fn(*args)
    else:
        result = fn(*args)
    return _mismatch(case["out"], result, key)


@pytest.mark.parametrize("key", sorted(CASES))
def test_case_matches_js(key: str, tmp_path) -> None:
    bad = _replay(key, str(tmp_path / "fake"))
    assert bad is None, bad


def test_every_recorded_function_is_ported() -> None:
    """A new function in the recorder must get a port and a row here, or the suite stops."""
    called = {c["fn"] for c in CASES.values() if "fn" in c}
    assert called <= set(DISPATCH), sorted(called - set(DISPATCH))


def test_recorder_is_the_expected_size() -> None:
    assert len(CASES) >= 40


@pytest.mark.parametrize("prefix", [
    "plan/one_filter", "plan/two_exposures", "plan/part_without_dark", "plan/no_darks", "plan/flats_without_temperature",
    "plan/one_flat_only", "plan/two_binnings", "plan/two_filters", "plan/case_clashing", "plan/duplicate_flat",
    "plan/gain_split", "plan/null_exposure", "shortfall/", "line/", "summary/", "lines/", "scan/",
])
def test_recorder_covers_each_listed_case(prefix: str) -> None:
    assert any(k.startswith(prefix) for k in CASES), prefix


# ---- the Siril differences and plain behaviour -------------------------------------------------------

def _flat(path: str, **over: Any) -> dict[str, Any]:
    d = {"path": path, "name": path.rsplit("/", 1)[-1], "imagetyp": "Flat", "filter": "Ha", "exposure": 2.0,
         "binning": 1.0, "gain": 120.0, "temp": -10.0}
    d.update(over)
    return d


def _dark(path: str, kind: str = "master", **over: Any) -> dict[str, Any]:
    d = {"path": path, "name": path.rsplit("/", 1)[-1], "imagetyp": "Master Dark" if kind == "master" else "Dark",
         "filter": None, "exposure": 2.0, "binning": 1.0, "gain": 120.0, "temp": -10.0, "kind": kind,
         "size": 1, "modified": "2026-09-18T20:00:00.000Z"}
    d.update(over)
    return d


def test_build_plan_names_masters_with_fit_by_default() -> None:
    flats = [_flat("/f/a.fit", binning=1.0), _flat("/f/b.fit", binning=1.0),
             _flat("/f/c.fit", binning=2.0), _flat("/f/d.fit", binning=2.0)]
    darks = [_dark("/d/m1.fit", binning=1.0), _dark("/d/m2.fit", binning=2.0)]
    jobs = mf.build_plan(flats, darks, "/work/Flat")
    assert [j["name"] for j in jobs] == ["masterFlat_Ha_bin1.fit", "masterFlat_Ha_bin2.fit"]
    assert [j["master"] for j in jobs] == ["/work/Flat/masterFlat_Ha_bin1.fit", "/work/Flat/masterFlat_Ha_bin2.fit"]
    assert all(j["skip"] is None for j in jobs)


def test_build_plan_extension_follows_ext() -> None:
    flats = [_flat("/f/a.fit"), _flat("/f/b.fit")]
    fit = mf.build_plan(flats, [_dark("/d/m.fit")], "/w")
    xisf = mf.build_plan(flats, [_dark("/d/m.fit")], "/w", ext=".xisf")
    assert fit[0]["name"] == "masterFlat_Ha.fit"
    assert xisf[0]["name"] == "masterFlat_Ha.xisf"


def test_build_plan_clash_suffix_keeps_extension() -> None:
    flats = [_flat("/f/a.fit", filter="Ha", binning=None), _flat("/f/b.fit", filter="HA", binning=None)]
    jobs = mf.build_plan(flats, [_dark("/d/m.fit", binning=None)], "/w")
    assert [j["name"] for j in jobs] == ["masterFlat_Ha.fit", "masterFlat_HA_2.fit"]


def test_dark_shortfall_without_where_says_darks_folder() -> None:
    assert mf.dark_shortfall(_flat("/f/a.fit"), [], None) == " (no darks found in the darks folder or below it)"
    assert mf.dark_shortfall(_flat("/f/a.fit"), [], "") == " (no darks found in the darks folder or below it)"


def test_scan_skips_hidden_names(tmp_path) -> None:
    """An AppleDouble twin and a hidden folder are not images and are not scanned (Util.findEntries drops them)."""
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "dark_in_hidden.xisf").write_bytes(b"x")
    (tmp_path / "._dark_twin.xisf").write_bytes(b"x")
    (tmp_path / "dark_ok.xisf").write_bytes(b"x")
    header = _reader({str(tmp_path / "dark_ok.xisf"): {"info": True, "keywords": {"IMAGETYP": "Dark Frame"}},
                      str(tmp_path / ".hidden" / "dark_in_hidden.xisf"): {"info": True, "keywords": {}},
                      str(tmp_path / "._dark_twin.xisf"): {"info": True, "keywords": {}}})
    found = mf.scan_folder(str(tmp_path), header)
    assert [d["name"] for d in found] == ["dark_ok.xisf"]


def test_scan_does_not_follow_a_symbolic_link_to_a_folder(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "dark_real.xisf").write_bytes(b"x")
    darks = tmp_path / "darks"
    darks.mkdir()
    os.symlink(real, darks / "linked")
    header = _reader({str(real / "dark_real.xisf"): {"info": True, "keywords": {}}})
    assert mf.scan_folder(str(darks), header) == []
    assert mf.SCAN_DEPTH == 4


def test_scan_missing_and_empty_arguments_give_nothing(tmp_path) -> None:
    header = _reader({})
    assert mf.scan_folder(None, header) == []
    assert mf.scan_folder("", header) == []
    assert mf.scan_folder(str(tmp_path / "missing"), header) == []


def test_scan_strips_trailing_slashes(tmp_path) -> None:
    (tmp_path / "dark_a.xisf").write_bytes(b"x")
    header = _reader({str(tmp_path / "dark_a.xisf"): {"info": True, "keywords": {}}})
    found = mf.scan_folder(str(tmp_path) + "//", header)
    assert [d["path"] for d in found] == [str(tmp_path / "dark_a.xisf")]
