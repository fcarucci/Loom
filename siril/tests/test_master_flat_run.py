"""The master-flat checks, deletion rules and job runner equal Loom's JavaScript on every recorded case.

tests/oracle/fs_master_flat_run.json holds, for each case, the arguments, the scripted engine's table and the JS result
and call log, recorded by tools/oracle/fs_master_flat_run.js through tools/record_oracle.js in node. Each execute case
is replayed here with the same scripted engine (it logs every call and answers from the same table) and compared:
result and call log exactly, floats to 1e-9 relative, NaN equal to NaN. The suite needs neither node nor Loom.

The header adapter (HeaderView, quiet_header_obj) is checked on real FITS files written with io_fits, on a missing file,
and through master_flat.describe and master_flat.scan_folder. Rounding through master_flat_num is checked on the values
where the JavaScript Math.round and a floor(x + 0.5) differ.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Callable

import numpy as np
import pytest

from loomsiril import master_flat as mf
from loomsiril import master_flat_run as run
from loomsiril.io_fits import write_fits
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_master_flat_run.json"
if not PATH.exists():
    pytest.skip("oracle fs_master_flat_run.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]

#: The recorder's plans are built with the JS masters (.xisf).
EXT = ".xisf"


class _Obj:
    """A plain object with the JSON's fields as attributes (the recorder's ``got`` and ``want``)."""

    def __init__(self, fields: dict[str, Any]) -> None:
        self.__dict__.update(fields)


class _Got:
    """The master as verifyMaster reads it: width, height, mean, and keyword(name), None when absent."""

    def __init__(self, entry: dict[str, Any]) -> None:
        self.width = entry.get("width")
        self.height = entry.get("height")
        self.mean = entry.get("mean")
        self._keywords = entry.get("keywords", {})

    def keyword(self, name: str) -> Any:
        return self._keywords.get(name)


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


class _Fake:
    """The scripted engine of tools/oracle/fs_master_flat_run.js, built from the recorded table. Every call is logged
    in order with its arguments and its answer; ``resolve`` and ``remove_partials`` exist only when the table has them."""

    def __init__(self, table: dict[str, Any], log: list[Any]) -> None:
        self.t = table
        self.log = log
        self.fs = set(table.get("fs", []))
        if table.get("resolve") is not None:
            self.resolve = self._resolve
        if table.get("removePartials") is True:
            self.remove_partials = self._remove_partials

    def _resolve(self, p: str) -> str:
        r = self.t["resolve"]
        if p in r and r[p] == "throw":
            raise run.EngineError("cannot resolve " + p)
        return r.get(p, p)

    def _remove_partials(self, d: str) -> None:
        self.log.append(["removePartials", d])

    def exists(self, p: str) -> bool:
        r = p in self.fs
        self.log.append(["exists", p, r])
        return r

    def remove(self, p: str) -> bool:
        removed_throw = self.t.get("removeThrow", {})
        if p in removed_throw:
            self.log.append(["remove", p, "throw"])
            if removed_throw[p] == "gone":
                self.fs.discard(p)
            raise run.EngineError("locked: " + p)
        r = p not in self.t.get("removeFail", [])
        if r:
            self.fs.discard(p)
        self.log.append(["remove", p, r])
        return r

    def remove_dir(self, d: str) -> bool:
        self.log.append(["removeDir", d])
        return True

    def temp_dir(self, job: dict[str, Any]) -> str:
        if self.t.get("tempDirThrow") is not None:
            self.log.append(["tempDir", job["name"], "throw"])
            raise run.EngineError(self.t["tempDirThrow"])
        d = "/work/tmp/" + job["name"]
        self.log.append(["tempDir", job["name"], d])
        return d

    def master_dark(self, group: dict[str, Any]) -> dict[str, Any]:
        r = self.t["masterDark"].get(group["key"], {"ok": False, "reason": "not in table"})
        self.log.append(["masterDark", group["key"]])
        if r.get("throw"):
            raise run.EngineError(r["throw"])
        return {"ok": r.get("ok"), "path": r.get("path"), "reason": r.get("reason")}

    def calibrate(self, paths: list[str], dark_path: str, out_dir: str) -> dict[str, Any]:
        self.log.append(["calibrate", paths, dark_path, out_dir])
        r = self.t.get("calibrate", {}).get(paths[0], {"ok": True})
        if r.get("throw"):
            raise run.EngineError(r["throw"])
        if not r.get("ok"):
            return {"ok": False, "reason": r.get("reason")}
        n = len(paths) if r.get("count") is None else r["count"]
        return {"ok": True, "paths": [out_dir + "/cal_" + str(i) + ".xisf" for i in range(min(n, len(paths)))]}

    def integrate(self, paths: list[str], spec: dict[str, Any], out_path: str, job: dict[str, Any]) -> dict[str, Any]:
        self.log.append(["integrate", paths, spec, out_path, job["name"]])
        r = self.t.get("integrate", {}).get(out_path, {"ok": True})
        if r.get("throw"):
            raise run.EngineError(r["throw"])
        if r.get("ok") or r.get("leaves"):
            self.fs.add(out_path)
        return {"ok": True} if r.get("ok") else {"ok": False, "reason": r.get("reason")}

    def verify(self, partial: str, job: dict[str, Any]) -> Any:
        self.log.append(["verify", partial])
        return self.t.get("verify", {}).get(partial)

    def publish(self, partial: str, final: str) -> bool:
        self.log.append(["publish", partial, final])
        mode = self.t.get("publish", {}).get(final, True)
        if mode == "throw":
            raise run.EngineError("disk gone")
        if final in self.fs or mode is not True:
            return False
        self.fs.discard(partial)
        self.fs.add(final)
        return True


def _replay_execute(case: dict[str, Any]) -> tuple[Any, list[Any], str]:
    """Run one recorded plan through the scripted engine; returns (result, call log, report text)."""
    table = case["args"]["fake"]
    log: list[Any] = []
    engine = _Fake(table, log)
    progress = [0]

    def on_progress(done: int, total: int, text: str) -> None:
        progress[0] = done + 1
        log.append(["onProgress", done, total, text])

    opts: dict[str, Any] = {"destFlatDir": case["args"]["opts"]["destFlatDir"],
                            "cardRoot": case["args"]["opts"]["cardRoot"], "onProgress": on_progress}
    if table.get("stopAfter") is not None:
        def should_stop() -> bool:
            r = progress[0] > table["stopAfter"]
            log.append(["shouldStop", r])
            return r

        opts["shouldStop"] = should_stop
    out = run.execute(copy.deepcopy(case["args"]["jobs"]), engine, opts, ext=EXT)
    return out, log, run.report(out)


EXECUTE = sorted(k for k in CASES if CASES[k]["fn"] == "execute")


@pytest.mark.parametrize("key", EXECUTE)
def test_execute_matches_js(key: str) -> None:
    case = CASES[key]
    out, log, text = _replay_execute(case)
    bad = _mismatch(case["out"], out)
    assert bad is None, f"{key}: {bad}"
    bad = _mismatch(case["log"], log, "log")
    assert bad is None, f"{key}: {bad}"
    assert text == case["report"], f"{key}: report differs"


def _mayde_resolver(table: dict[str, Any] | None) -> Callable[[str], Any] | None:
    if table is None:
        return None

    def resolve(p: str) -> str:
        if p in table and table[p] == "throw":
            raise run.EngineError("cannot resolve " + p)
        return table.get(p, p)

    return resolve


VERIFY = sorted(k for k in CASES if CASES[k]["fn"] == "verifyMaster")
CLEAN = sorted(k for k in CASES if CASES[k]["fn"] == "cleanPath")
MAY = sorted(k for k in CASES if CASES[k]["fn"] == "mayDelete")
REPORT = sorted(k for k in CASES if CASES[k]["fn"] == "report")


@pytest.mark.parametrize("key", VERIFY)
def test_verify_master_matches_js(key: str) -> None:
    c = CASES[key]
    got = None if c["args"]["got"] is None else _Got(c["args"]["got"])
    out = run.verify_master(got, _Obj(c["args"]["want"]), c["args"]["filter"])
    assert _mismatch(c["out"], out) is None, key


@pytest.mark.parametrize("key", CLEAN)
def test_clean_path_matches_js(key: str) -> None:
    c = CASES[key]
    assert _mismatch(c["out"], run.clean_path(c["args"][0])) is None, key


@pytest.mark.parametrize("key", MAY)
def test_may_delete_matches_js(key: str) -> None:
    c = CASES[key]
    path, dest, master, card = c["args"]
    out = run.may_delete(path, dest, master, card, _mayde_resolver(c["resolve"]))
    assert _mismatch(c["out"], out) is None, key


@pytest.mark.parametrize("key", REPORT)
def test_report_matches_js(key: str) -> None:
    c = CASES[key]
    assert run.report(c["args"][0]) == c["out"], key


def test_recorded_case_count() -> None:
    assert len(CASES) >= 25
    assert len(EXECUTE) >= 25


# ---------------------------------------------------------------------------
# The header adapter
# ---------------------------------------------------------------------------


def _write_flat(path: Any, keywords: dict[str, Any]) -> None:
    write_fits(str(path), [np.full((3, 4), 0.5, dtype=np.float32)], header=keywords)


def test_quiet_header_obj_reads_a_real_fits(tmp_path: Any) -> None:
    p = tmp_path / "masterFlat_Ha.fit"
    _write_flat(p, {"IMAGETYP": "Master Flat", "FILTER": "Ha"})
    hv = run.quiet_header_obj(str(p))
    assert hv.info is not None
    assert (hv.info.width, hv.info.height) == (4, 3)
    assert hv.keyword("FILTER") == "Ha"
    assert hv.keyword("IMAGETYP") == "Master Flat"
    assert hv.keyword("NOPE") is None


def test_quiet_header_obj_missing_file(tmp_path: Any) -> None:
    hv = run.quiet_header_obj(str(tmp_path / "missing.fit"))
    assert hv.info is None
    assert hv.keyword("FILTER") is None
    assert hv.keyword("IMAGETYP") is None


def test_describe_and_scan_read_through_the_adapter(tmp_path: Any) -> None:
    dark = tmp_path / "dark_30s.fit"
    _write_flat(dark, {"IMAGETYP": "Dark Frame", "EXPOSURE": 30.0})
    flat = tmp_path / "flat_Ha.fit"
    _write_flat(flat, {"IMAGETYP": "Flat Field", "FILTER": "Ha"})
    described = mf.describe(str(flat), run.quiet_header_obj(str(flat)).keyword)
    assert described["path"] == str(flat)
    assert described["filter"] == "Ha"
    found = mf.scan_folder(str(tmp_path), run.quiet_header_obj)
    assert [e["path"] for e in found] == [str(dark)]
    assert found[0]["exposure"] == 30.0


# ---------------------------------------------------------------------------
# The rounding that master_flat_num prints with (JavaScript Math.round)
# ---------------------------------------------------------------------------


#: loomsiril.core.js_round is floor(x + 0.5), which gives 1 for 0.49999999999999994 (the sum rounds up to 1.0).
#: Strict xfail until core.js_round is fixed (floor(x), plus one when x - floor(x) >= 0.5); then it must pass.
_CORE_ROUND = pytest.mark.xfail(strict=True, reason="loomsiril.core.js_round rounds 0.49999999999999994 up")


@pytest.mark.parametrize("value, printed", [
    pytest.param(0.49999999999999994, "0", marks=_CORE_ROUND),
    (0.5, "1"),
    (-0.5, "0"),
    (2.5, "3"),
    (-2.5, "-2"),
])
def test_master_flat_num_rounds_as_js_math_round(value: float, printed: str) -> None:
    assert mf.master_flat_num(value, 1) == printed


@pytest.mark.parametrize("mean, reason", [
    (np.float32(1.5), None),
    (np.float64(2.0), None),
    (np.int64(3), None),
    (np.float32("nan"), "the master is empty or not finite"),
    (np.float64("inf"), "the master is empty or not finite"),
    (np.float64(0.0), "the master is empty or not finite"),
    (np.bool_(True), "the master is empty or not finite"),
])
def test_verify_mean_accepts_numpy_scalars(mean: Any, reason: str | None) -> None:
    got = _Got({"width": 4, "height": 3, "mean": mean, "keywords": {"FILTER": "Ha", "IMAGETYP": "Master Flat"}})
    assert run.verify_master(got, _Obj({"width": 4, "height": 3}), "Ha") == reason
