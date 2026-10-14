"""Cache-aware stage-chain execution (loomsiril.loom.chain.process_chain) over a real CacheStore in tmp_path.

The runners are fakes: each adds a constant to its input plate, writes a small synthetic FITS plate to the work
folder, and records the call. The final value of a chain is therefore the source value plus the sum of the adds, which
shows which stages really ran.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.cache import CacheStore, build_stage_keys
from loomsiril.loom.chain import ChainResult, RunCancelled, process_chain
from loomsiril.loom.runctx import RunContext, StageOutput

LABEL = "ch1"
SOURCE_VALUE = 10.0
SHAPE = (4, 5)
ADDS = {"solve": 1.0, "extractL": 2.0, "stretchL": 4.0, "denoiseL": 8.0}
FULL_CHAIN = ["solve", "extractL", "stretchL", "denoiseL"]
PARAMS = {
    "solve": {"mode": "a"},
    "extractL": {"size": 3},
    "stretchL": {"amount": 0.5},
    "denoiseL": {"strength": 2},
}


def _write_plate(path: str, value: float) -> None:
    write_fits(path, [np.full(SHAPE, value, dtype=np.float32)], None)


def _value(path: str) -> float:
    planes, _header = read_fits(path)
    return float(planes[0][0, 0])


class Fakes:
    """Fake stage runners that record their calls. ``skip`` names stages that return skip_cache=True."""

    def __init__(self, stages: list[str], skip: tuple[str, ...] = (), companion_for: tuple[str, ...] = ("extractL",)
                 ) -> None:
        self.calls: list[tuple[str, str]] = []
        self.outs: dict[str, str] = {}
        self.runners: dict[str, Callable[[str, str], StageOutput]] = {}
        for stage in stages:
            self.runners[stage] = self._make(stage, stage in skip, stage in companion_for)

    def _make(self, stage: str, skip: bool, companion: bool) -> Callable[[str, str], StageOutput]:
        def run(in_path: str, out_path: str) -> StageOutput:
            self.calls.append((stage, in_path))
            self.outs[stage] = out_path
            planes, header = read_fits(in_path)
            write_fits(out_path, [planes[0] + np.float32(ADDS[stage])], header)
            companions: dict[str, str] = {}
            if companion:
                star_path = os.path.join(os.path.dirname(out_path), LABEL + "_stars.fits")
                _write_plate(star_path, 100.0 + ADDS[stage])
                companions["stars"] = star_path
            return StageOutput(path=out_path, companions=companions, skip_cache=skip)

        return run

    def ran(self) -> list[str]:
        return [stage for stage, _path in self.calls]


class Harness:
    """A work folder, a cache folder, a source plate and a log list around one fake run context."""

    def __init__(self, tmp_path: Path) -> None:
        self.work = tmp_path / "work"
        self.work.mkdir()
        self.cache_dir = tmp_path / "cache"
        self.source_path = str(tmp_path / "source.fits")
        _write_plate(self.source_path, SOURCE_VALUE)
        self.source_calls = 0
        self.logs: list[str] = []
        self.warns: list[str] = []
        self.store_logs: list[tuple[str, str]] = []
        self.cancel_when: Optional[Callable[[], bool]] = None

    def ctx(self) -> RunContext:
        return RunContext(ops=None, work_dir=str(self.work), log=self.logs.append, warn=self.warns.append,
                          cancelled=self.cancel_when if self.cancel_when is not None else (lambda: False))

    def store(self) -> CacheStore:
        return CacheStore(str(self.cache_dir), log=lambda level, text: self.store_logs.append((level, text)))

    def source(self) -> str:
        self.source_calls += 1
        return self.source_path

    def run(self, chain: list[dict[str, Any]], fakes: Fakes, store: Optional[CacheStore], config: dict[str, Any]
            ) -> ChainResult:
        return process_chain(self.ctx(), LABEL, self.source, chain, fakes.runners, store, config)


def _chain(stages: list[str] = FULL_CHAIN, params: Optional[dict[str, Any]] = None,
           source_key: Optional[str] = "src-key") -> list[dict[str, Any]]:
    table = PARAMS if params is None else params
    return build_stage_keys(source_key, {s: table[s] for s in stages})


CACHE_ON = {"useCache": True}


def test_full_miss_runs_all_and_stores_all(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    fakes = Fakes(FULL_CHAIN)
    store = h.store()
    result = h.run(chain, fakes, store, CACHE_ON)

    assert fakes.ran() == FULL_CHAIN
    assert h.source_calls == 1
    assert result.hit_stage is None
    assert result.key == chain[-1]["key"]
    assert _value(result.path) == pytest.approx(SOURCE_VALUE + sum(ADDS.values()))
    for entry in chain:
        assert store.lookup(entry["key"]) is not None
    assert "[cache] ch1 solve MISS (no entry)" in h.logs


def test_second_call_hits_last_stage_and_runs_nothing(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)

    h.logs.clear()
    fakes = Fakes(FULL_CHAIN)
    result = h.run(chain, fakes, store, CACHE_ON)

    assert fakes.calls == []
    assert h.source_calls == 1  # only the first call read the source
    assert result.hit_stage == "denoiseL"
    assert result.path == store.path_for(chain[-1]["key"])
    assert _value(result.path) == pytest.approx(SOURCE_VALUE + sum(ADDS.values()))
    assert f"[cache] ch1 denoiseL HIT {chain[-1]['key'][:12]}..." in h.logs
    assert f"[cache] ch1 solve HIT {chain[0]['key'][:12]}... (superseded by later cached stage denoiseL)" in h.logs


def test_source_is_not_called_on_a_full_hit(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)
    h.source_calls = 0
    h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)
    assert h.source_calls == 0


def test_changed_params_in_the_middle_invalidate_only_the_stages_after_it(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)

    changed = dict(PARAMS, stretchL={"amount": 0.75})
    chain2 = _chain(params=changed)
    assert chain2[0]["key"] == chain[0]["key"]
    assert chain2[1]["key"] == chain[1]["key"]
    assert chain2[2]["key"] != chain[2]["key"]
    assert chain2[3]["key"] != chain[3]["key"]

    fakes = Fakes(FULL_CHAIN)
    result = h.run(chain2, fakes, store, CACHE_ON)
    assert fakes.ran() == ["stretchL", "denoiseL"]
    assert result.hit_stage == "extractL"
    assert _value(result.path) == pytest.approx(SOURCE_VALUE + sum(ADDS.values()))


def test_corrupt_latest_entry_falls_back_one_stage_and_is_discarded(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)

    corrupt = store.path_for(chain[-1]["key"])
    with open(corrupt, "wb") as fh:
        fh.write(b"this is not a FITS plate")

    fakes = Fakes(FULL_CHAIN)
    result = h.run(chain, fakes, store, CACHE_ON)

    assert result.hit_stage == "stretchL"
    assert fakes.ran() == ["denoiseL"]
    assert any("denoiseL is in the cache but would not load" in w for w in h.warns)
    assert any(level == "warn" and "deleted it" in text for level, text in h.store_logs)
    # The discarded entry was recomputed and stored again, and it reads back as a plate.
    assert _value(corrupt) == pytest.approx(SOURCE_VALUE + sum(ADDS.values()))


def test_missing_companion_makes_that_stage_a_miss(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain(stages=["solve", "extractL"])
    store = h.store()
    fakes = Fakes(["solve", "extractL"], companion_for=("extractL",))
    h.run(chain, fakes, store, CACHE_ON)
    os.remove(store.companion_path_for(chain[1]["key"], "stars"))

    fakes = Fakes(["solve", "extractL"], companion_for=("extractL",))
    h.logs.clear()
    result = h.run(chain, fakes, store, CACHE_ON)

    assert result.hit_stage == "solve"
    assert fakes.ran() == ["extractL"]
    assert "[cache] ch1 extractL MISS (incomplete entry)" in h.logs
    assert os.path.isfile(store.companion_path_for(chain[1]["key"], "stars"))
    # The run wrote its companion to the work folder, and that is the plate the chain reports.
    assert os.path.isfile(result.companions["stars"])


def test_skip_cache_output_is_not_stored_and_later_stages_still_are(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    fakes = Fakes(FULL_CHAIN, skip=("stretchL",))
    result = h.run(chain, fakes, store, CACHE_ON)

    assert fakes.ran() == FULL_CHAIN
    assert store.lookup(chain[2]["key"]) is None
    assert store.lookup(chain[3]["key"]) is not None
    assert "[cache] ch1 stretchL not cached (the step did not complete)" in h.logs
    # The chain continued from the skipped output: stretchL added nothing to the value it passed on.
    assert _value(result.path) == pytest.approx(SOURCE_VALUE + sum(ADDS.values()))

    h.logs.clear()
    again = Fakes(FULL_CHAIN)
    result2 = h.run(chain, again, store, CACHE_ON)
    assert again.calls == []
    assert result2.hit_stage == "denoiseL"


def test_ignore_cache_reruns_every_stage_and_stores_results(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)

    h.logs.clear()
    fakes = Fakes(FULL_CHAIN)
    result = h.run(chain, fakes, store, {"useCache": True, "ignoreCache": True})

    assert fakes.ran() == FULL_CHAIN
    assert result.hit_stage is None
    assert "[cache] ch1 denoiseL MISS (cache ignored for this run)" in h.logs
    for entry in chain:
        assert store.lookup(entry["key"]) is not None


def test_cache_off_never_touches_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the cache was touched with useCache off")

    for name in ("lookup", "load", "store", "load_companion", "store_companion", "entry_complete"):
        monkeypatch.setattr(store, name, boom)

    fakes = Fakes(FULL_CHAIN)
    result = h.run(chain, fakes, store, {"useCache": False})

    assert fakes.ran() == FULL_CHAIN
    assert result.hit_stage is None
    assert not os.path.exists(h.cache_dir)
    assert not any("[cache]" in line for line in h.logs)


def test_cancel_between_stages_raises_and_keeps_earlier_entries(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    fakes = Fakes(FULL_CHAIN)
    h.cancel_when = lambda: len(fakes.calls) >= 2

    with pytest.raises(RunCancelled, match="Cancelled by user"):
        h.run(chain, fakes, store, CACHE_ON)

    assert fakes.ran() == ["solve", "extractL"]
    assert store.lookup(chain[0]["key"]) is not None
    assert store.lookup(chain[1]["key"]) is not None
    assert store.lookup(chain[2]["key"]) is None


def test_stage_with_params_and_no_runner_is_passed_through_and_stored(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain(stages=["solve", "extractL", "stretchL"])
    store = h.store()
    fakes = Fakes(["solve", "extractL"])
    result = h.run(chain, fakes, store, CACHE_ON)

    assert fakes.ran() == ["solve", "extractL"]
    assert "[cache] ch1 stretchL MISS (no entry)" in h.logs
    assert any("ch1 stretchL has no runner" in line for line in h.logs)
    assert store.lookup(chain[2]["key"]) is not None
    assert _value(result.path) == pytest.approx(SOURCE_VALUE + ADDS["solve"] + ADDS["extractL"])

    h.logs.clear()
    again = Fakes(["solve", "extractL"])
    result2 = h.run(chain, again, store, CACHE_ON)
    assert again.calls == []
    assert result2.hit_stage == "stretchL"
    key12 = chain[2]["key"][:12]
    assert f"[cache] ch1 stretchL HIT {key12}..." in h.logs
    assert _value(result2.path) == pytest.approx(SOURCE_VALUE + ADDS["solve"] + ADDS["extractL"])


def test_companion_of_a_hit_is_reported_in_place(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain(stages=["solve", "extractL"])
    store = h.store()
    fakes = Fakes(["solve", "extractL"], companion_for=("extractL",))
    h.run(chain, fakes, store, CACHE_ON)

    again = Fakes(["solve", "extractL"], companion_for=("extractL",))
    result = h.run(chain, again, store, CACHE_ON)
    assert again.calls == []
    assert result.companions == {"stars": store.companion_path_for(chain[1]["key"], "stars")}
    assert result.path == store.path_for(chain[1]["key"])


def test_empty_chain_returns_the_source_and_no_key(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    fakes = Fakes([])
    result = h.run([], fakes, h.store(), CACHE_ON)
    assert result == ChainResult(path=h.source_path, companions={}, key=None, hit_stage=None)
    assert h.source_calls == 1


def test_ignore_cache_stores_into_an_empty_cache(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    fakes = Fakes(FULL_CHAIN)
    h.run(chain, fakes, store, {"useCache": True, "ignoreCache": True})

    assert fakes.ran() == FULL_CHAIN
    for entry in chain:
        assert store.lookup(entry["key"]) is not None
    assert _value(store.path_for(chain[-1]["key"])) == pytest.approx(SOURCE_VALUE + sum(ADDS.values()))


def test_superseded_stage_companion_is_loaded_into_the_result(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)

    again = Fakes(FULL_CHAIN)
    result = h.run(chain, again, store, CACHE_ON)
    assert again.calls == []
    assert result.hit_stage == "denoiseL"
    assert result.companions == {"stars": store.companion_path_for(chain[1]["key"], "stars")}
    assert f"[cache] ch1 extractL HIT {chain[1]['key'][:12]}... (superseded by later cached stage denoiseL)" in h.logs


def test_superseded_stage_with_missing_companion_warns(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)
    os.remove(store.companion_path_for(chain[1]["key"], "stars"))

    result = h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)
    assert "stars" not in result.companions
    assert ("[cache] ch1 extractL superseded and its stars companion is missing; this run produces no stars frame "
            "for ch1") in h.warns


def test_superseded_stage_without_an_entry_is_logged_as_skipped(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    h.run(chain, Fakes(FULL_CHAIN), store, CACHE_ON)
    os.remove(store.path_for(chain[2]["key"]))

    fakes = Fakes(FULL_CHAIN)
    result = h.run(chain, fakes, store, CACHE_ON)
    assert result.hit_stage == "denoiseL"
    assert fakes.calls == []
    assert "[cache] ch1 stretchL MISS (no entry; skipped -- denoiseL is cached)" in h.logs


def test_corrupt_companion_is_lost_and_the_chain_falls_back(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain(stages=["solve", "extractL"])
    store = h.store()
    h.run(chain, Fakes(["solve", "extractL"]), store, CACHE_ON)
    with open(store.companion_path_for(chain[1]["key"], "stars"), "wb") as fh:
        fh.write(b"not a FITS companion")

    fakes = Fakes(["solve", "extractL"])
    result = h.run(chain, fakes, store, CACHE_ON)
    assert result.hit_stage == "solve"
    assert fakes.ran() == ["extractL"]
    assert "[cache] ch1 extractL lost its stars companion; looking further back" in h.warns


def test_a_raising_runner_propagates_and_keeps_earlier_entries(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    fakes = Fakes(FULL_CHAIN)

    def boom(in_path: str, out_path: str) -> StageOutput:
        raise RuntimeError("boom")

    fakes.runners["stretchL"] = boom
    with pytest.raises(RuntimeError, match="boom"):
        h.run(chain, fakes, store, CACHE_ON)

    assert store.lookup(chain[0]["key"]) is not None
    assert store.lookup(chain[1]["key"]) is not None
    assert store.lookup(chain[2]["key"]) is None
    assert store.lookup(chain[3]["key"]) is None


def test_unreadable_output_plate_is_not_cached_and_the_run_continues(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    fakes = Fakes(FULL_CHAIN)
    missing = str(tmp_path / "never_written.fits")

    def no_output(in_path: str, out_path: str) -> StageOutput:
        fakes.calls.append(("denoiseL", in_path))
        return StageOutput(path=missing)

    fakes.runners["denoiseL"] = no_output
    result = h.run(chain, fakes, store, CACHE_ON)

    assert store.lookup(chain[3]["key"]) is None
    assert store.lookup(chain[2]["key"]) is not None
    assert result.path == missing
    assert any("denoiseL could not be read for the cache" in w for w in h.warns)


def test_work_file_name_and_store_metadata(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    chain = _chain()
    store = h.store()
    fakes = Fakes(FULL_CHAIN)
    h.run(chain, fakes, store, CACHE_ON)

    assert fakes.outs["stretchL"] == os.path.join(str(h.work), "ch1_stretchL.fits")
    with open(store.meta_path_for(chain[2]["key"]), encoding="utf-8") as fh:
        meta = json.load(fh)
    assert meta == {"channel": "ch1", "stage": "stretchL", "params": {"amount": 0.5}}
