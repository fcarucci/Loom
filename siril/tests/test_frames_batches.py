"""measure_in_batches, run_batches, NO_SETTLER and throttle equal Loom's JavaScript (pixinsight/script/lib/Frames.js
2019-2134) on every recorded case.

tests/oracle/fs_batches.json holds, for each case, the arguments (the fakes are described as data), the log of what
the fakes were called with, and the JS result (recorded by tools/oracle/fs_batches.js through tools/record_oracle.js
in node). Each case is rebuilt here, replayed on loomsiril.frames_batches and compared: the log and the result must
be equal. The suite needs neither node nor Loom.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

import pytest

from loomsiril import frames_batches as B
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_batches.json"
if not PATH.exists():
    pytest.skip("oracle fs_batches.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]


def _fake_measure(spec: dict[str, Any], log: list[str]):
    calls = [0]

    def measure(batch: list[str]) -> Optional[dict[str, Any]]:
        c = calls[0]
        calls[0] += 1
        log.append(f"measure {c} [{','.join(batch)}]")
        if spec["throwAt"] == c:
            raise RuntimeError(f"measure failed at call {c}")
        if spec["nullAt"] == c:
            return None
        got: dict[str, Any] = {}
        for i, p in enumerate(batch):
            if p not in spec["omit"]:
                got[p] = {"call": c, "pos": i}
        if spec["foreignAt"] == c:
            got[spec["foreign"]] = {"call": c, "pos": -1}
        return got

    return measure


def _fake_on_batch(spec: dict[str, Any], log: list[str]):
    calls = [0]

    def on_batch(done: int) -> Any:
        c = calls[0]
        calls[0] += 1
        log.append(f"onBatch {c} {done}")
        if spec.get("throwAt") == c:
            raise RuntimeError(f"onBatch failed at call {c}")
        if spec.get("undefinedAt") == c:
            return None  # JS undefined: goes on
        if spec.get("zeroAt") == c:
            return 0  # JS 0 is not === false: goes on
        return c != spec["falseAt"]

    return on_batch


def _fake_settler(spec: dict[str, Any], log: list[str]):
    starts = [0]

    class Settler:
        def start(self, batch: list[str], got: dict[str, Any]) -> Optional[str]:
            k = starts[0]
            starts[0] += 1
            log.append(f"start {','.join(batch)} got {','.join(got)}")
            if spec.get("startThrowAt") == k:
                raise RuntimeError(f"settler start failed at call {k}")
            return None if spec["startNull"] else f"t{k}"

        def finish(self, token: Any) -> None:
            log.append(f"finish {token}")
            if token == f"t{spec.get('finishThrowAt')}":
                raise RuntimeError(f"settler finish failed for {token}")

        def kill(self, token: Any) -> None:
            log.append(f"kill {token}")

    return Settler()


def _run_measure(args: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    log: list[str] = []
    measure = _fake_measure(args["measure"], log)
    on_batch = None if args["onBatch"] is None else _fake_on_batch(args["onBatch"], log)
    settler = None if args["settler"] is None else _fake_settler(args["settler"], log)
    try:
        result = B.measure_in_batches(copy.deepcopy(args["paths"]), args["size"], measure, on_batch, settler)
    except Exception as e:  # the JS records e.message
        result = {"threw": str(e)}
    return log, result


def _run_throttle(args: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    log: list[str] = []
    fn = None
    if args["fn"] is not None:
        calls = [0]

        def fn(done: int, total: int, name: Any) -> bool:
            k = calls[0]
            calls[0] += 1
            log.append(f"fn {k} {done}/{total} {name}")
            return k != args["fn"]["falseAt"]

    times = iter(args["times"])
    w = B.throttle(fn, args["ms"], lambda: next(times))
    returns = None if w is None else [w(*c) for c in args["calls"]]
    return log, {"wrapper": None if w is None else "function", "returns": returns}


def _run_no_settler() -> tuple[list[str], dict[str, Any]]:
    n = B.NO_SETTLER
    return [], {"start": n.start(["a"], {}), "finish": n.finish("x"), "kill": n.kill("x")}


def _run(key: str) -> tuple[list[str], dict[str, Any]]:
    case = CASES[key]
    if key.startswith("measure/"):
        return _run_measure(case["args"])
    if key.startswith("throttle/"):
        return _run_throttle(case["args"])
    return _run_no_settler()


def _mismatch(expected: Any, actual: Any, where: str = "result") -> Optional[str]:
    """Where ``actual`` first differs from the JS ``expected``, or None when they agree. Bools and None are exact;
    numbers compare by value."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return None if expected is actual else f"{where}: {expected!r} != {actual!r}"
    if expected is None or actual is None:
        return None if expected is actual else f"{where}: {expected!r} != {actual!r}"
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(expected) != set(actual):
            return f"{where}: keys differ"
        for k in expected:
            bad = _mismatch(expected[k], actual[k], f"{where}.{k}")
            if bad:
                return bad
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return f"{where}: list length differs"
        for i, (e, a) in enumerate(zip(expected, actual)):
            bad = _mismatch(e, a, f"{where}[{i}]")
            if bad:
                return bad
        return None
    return None if expected == actual else f"{where}: {expected!r} != {actual!r}"


@pytest.mark.parametrize("key", sorted(CASES))
def test_case_matches_js(key: str) -> None:
    case = CASES[key]
    log, result = _run(key)
    assert log == case["log"], "log differs from the JavaScript"
    bad = _mismatch(case["result"], result)
    assert bad is None, bad


def test_no_callbacks_and_no_settler_are_the_same_run() -> None:
    """A run with no settler gives the same result as one with NO_SETTLER passed explicitly."""
    paths = [f"/subs/Ha/L_{i:03d}.xisf" for i in range(1, 8)]
    plain = B.measure_in_batches(list(paths), 3, lambda b: {p: 1 for p in b})
    explicit = B.measure_in_batches(list(paths), 3, lambda b: {p: 1 for p in b}, None, B.NO_SETTLER)
    assert plain == explicit
    assert plain["measured"] == {p: 1 for p in paths}


def test_throttle_of_nothing_is_nothing() -> None:
    assert B.throttle(None, 100) is None


def test_default_clock_holds_back_a_quick_second_call() -> None:
    seen: list[tuple[int, int]] = []
    w = B.throttle(lambda d, t, n: seen.append((d, t)) or True, 1e12)
    assert w is not None
    assert w(1, 5, "a") is True
    assert w(2, 5, "b") is True
    assert seen == [(1, 5)]
