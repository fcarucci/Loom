"""Frame Selector: reading a folder, measuring it by channel and cancelling (loomsiril.frames_scan).

Real FITS files are written under pytest's tmp_path with io_fits; the measurement is a fake, so these tests need
neither Siril, PixInsight, node nor a display.
"""

from __future__ import annotations

import logging
import os
import types
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pytest

from loomsiril import frames as F
from loomsiril import frames_scan as S
from loomsiril.frames import NO_FILTER
from loomsiril.frames_headers import frame_files_in
from loomsiril.io_fits import write_fits

CONFIG = "test configuration"


def _frame(path: Path, filter_: Optional[str] = None, imagetyp: str = "Light Frame",
           pixel: float = 1.0) -> str:
    # OBJECT is the file's name: identical bytes would share one digest, and so one cached measurement
    header: dict[str, Any] = {"IMAGETYP": imagetyp, "OBJECT": os.path.basename(str(path))}
    if filter_ is not None:
        header["FILTER"] = filter_
    write_fits(str(path), [np.full((4, 4), pixel, dtype=np.float32)], header=header)
    return str(path)


def _value(path: str) -> float:
    """A number that depends only on the file's name: deterministic across scans."""
    return float(sum(map(ord, os.path.basename(path))))


def _metrics_for(path: str) -> dict[str, Any]:
    return {k: _value(path) for k in F.STORED_KEYS}


class FakeMeasure:
    """Stands in for loomsiril.frames_measure.measure_batch. Records every call; can rewrite, fail or raise."""

    def __init__(self, *, before: Optional[Callable[[list[str]], None]] = None,
                 problem_for: Optional[set[str]] = None, raise_for: Optional[set[str]] = None) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.altitude_of: Any = "not called"
        self._before = before
        self._problem_for = problem_for or set()
        self._raise_for = raise_for or set()

    @property
    def measured(self) -> list[str]:
        return [p for paths, _ in self.calls for p in paths]

    def __call__(self, siril: Any, paths: list[str], work_dir: str,
                 altitude_of: Optional[Callable[[str], Any]] = None) -> tuple[dict[str, Any], Optional[str]]:
        self.calls.append((list(paths), work_dir))
        self.altitude_of = altitude_of
        if self._before is not None:
            self._before(paths)
        if self._raise_for & set(paths):
            raise RuntimeError("siril failed")
        if self._problem_for & set(paths):
            return {}, "SubframeSelector failed"
        return {p: _metrics_for(p) for p in paths}, None


def _scan(paths: list[str], cache: Path, measure: Callable[..., Any], progress: Any = None) -> dict[str, Any]:
    return S.scan_paths(paths, progress, siril=None, cache_dir=str(cache), measure=measure, config_text=CONFIG)


def _folder(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A folder of lights (L, Ha, no filter), a dark and a master."""
    folder = tmp_path / "inputs"
    folder.mkdir()
    files = {
        "l1": _frame(folder / "light_L_1.fit", "L"),
        "l2": _frame(folder / "light_L_2.fit", "L"),
        "h1": _frame(folder / "light_Ha_1.fit", "Ha"),
        "n1": _frame(folder / "light_none.fit"),
        "dark": _frame(folder / "dark_1.fit", None, "Dark Frame"),
        "master": _frame(folder / "master_light.fit", "L"),
    }
    return folder, files


# ---- grouping, entries, the result shape --------------------------------------------------------------

def test_grouping_skips_masters_and_darks_and_measures_each_channel(tmp_path: Path) -> None:
    folder, files = _folder(tmp_path)
    cache = tmp_path / "cache"
    fake = FakeMeasure()
    result = S.scan(str(folder), None, siril=None, cache_dir=str(cache), measure=fake, config_text=CONFIG)

    assert set(result) == {"channels", "unstable", "skipped", "cancelled"}
    assert result["cancelled"] is False
    assert result["unstable"] == []
    assert sorted(result["skipped"], key=lambda s: s["path"]) == [
        {"path": files["dark"], "kind": "dark"},
        {"path": files["master"], "kind": "master"},
    ]
    assert set(result["channels"]) == {"L", "Ha", NO_FILTER}
    lights = {files["l1"], files["l2"]}
    L = result["channels"]["L"]
    assert {e["path"] for e in L["entries"]} == lights
    assert all("identity" in e and len(e["identity"]["digest"]) == 40 for e in L["entries"])
    assert set(L["metrics"]) == lights
    assert L["metrics"][files["l1"]] == {k: _value(files["l1"]) for k in F.STORED_KEYS}
    assert L["problems"] == []
    assert [e["path"] for e in result["channels"]["Ha"]["entries"]] == [files["h1"]]
    assert [e["path"] for e in result["channels"][NO_FILTER]["entries"]] == [files["n1"]]
    assert sorted(fake.measured) == sorted([files["l1"], files["l2"], files["h1"], files["n1"]])


def test_batches_of_eight_with_the_work_directory_under_the_cache(tmp_path: Path) -> None:
    paths = [_frame(tmp_path / f"light_L_{i:02d}.fit", "L") for i in range(9)]
    cache = tmp_path / "cache"
    fake = FakeMeasure()
    _scan(paths, cache, fake)
    assert [len(batch) for batch, _ in fake.calls] == [8, 1]
    work_dirs = [work for _, work in fake.calls]
    assert work_dirs == [str(cache / "measure-work" / "1"), str(cache / "measure-work" / "2")]
    assert fake.altitude_of is not None and callable(fake.altitude_of)


def test_entries_and_metrics_are_the_same_as_the_measurement_gives(tmp_path: Path) -> None:
    paths = [_frame(tmp_path / "light_L_1.fit", "L")]
    result = _scan(paths, tmp_path / "cache", FakeMeasure())
    assert result["channels"]["L"]["metrics"][paths[0]] == F.stored_metrics(_metrics_for(paths[0]))


# ---- the cache -------------------------------------------------------------------------------------------

def test_second_scan_measures_nothing(tmp_path: Path) -> None:
    folder, files = _folder(tmp_path)
    cache = tmp_path / "cache"
    first = FakeMeasure()
    one = S.scan(str(folder), None, siril=None, cache_dir=str(cache), measure=first, config_text=CONFIG)
    second = FakeMeasure()
    two = S.scan(str(folder), None, siril=None, cache_dir=str(cache), measure=second, config_text=CONFIG)
    assert len(first.calls) > 0
    assert second.calls == []
    assert {k: v["metrics"] for k, v in two["channels"].items()} == {
        k: v["metrics"] for k, v in one["channels"].items()}


def test_a_frame_rewritten_during_measuring_is_unstable_and_measured_again_later(tmp_path: Path) -> None:
    paths = [_frame(tmp_path / "light_L_1.fit", "L"), _frame(tmp_path / "light_L_2.fit", "L")]
    target = paths[1]
    cache = tmp_path / "cache"

    def rewrite(batch: list[str]) -> None:
        if target in batch:
            _frame(Path(target), "L", pixel=7.0)  # a different frame, still a FITS file in the same channel

    result = _scan(paths, cache, FakeMeasure(before=rewrite))
    assert result["unstable"] == [target]
    assert result["cancelled"] is False
    L = result["channels"]["L"]
    assert set(L["metrics"]) == {paths[0]}
    assert [e["path"] for e in L["entries"]] == paths

    later = FakeMeasure()
    again = _scan(paths, cache, later)
    assert later.measured == [target]          # the old numbers were never stored under the new bytes
    assert again["unstable"] == []
    assert set(again["channels"]["L"]["metrics"]) == set(paths)


# ---- cancellation ----------------------------------------------------------------------------------------

def test_cancel_during_reading_leaves_no_channels_and_measures_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(S, "READING_EVERY_MS", 0)
    folder, _ = _folder(tmp_path)
    paths = frame_files_in(str(folder))
    seen: list[tuple[int, int, str]] = []

    def reading(done: int, total: int, name: str) -> Optional[bool]:
        seen.append((done, total, name))
        return False if done == 2 else None

    fake = FakeMeasure()
    result = _scan(paths, tmp_path / "cache", fake, types.SimpleNamespace(reading=reading))
    assert result["cancelled"] is True
    assert result["channels"] == {}
    assert fake.calls == []
    assert seen[-1][0] == 2 and seen[-1][1] == len(paths)


def test_cancel_before_a_channel_keeps_the_finished_channels(tmp_path: Path) -> None:
    folder, _ = _folder(tmp_path)
    paths = frame_files_in(str(folder))
    seen_channels: list[int] = []

    def measuring(done: int, total: int, filter_: str, overall: dict[str, Any]) -> Optional[bool]:
        if overall["channel"] not in seen_channels:
            seen_channels.append(overall["channel"])
        return False if overall["channel"] == 2 and done == 0 else None

    result = _scan(paths, tmp_path / "cache", FakeMeasure(), types.SimpleNamespace(measuring=measuring))
    assert result["cancelled"] is True
    assert len(result["channels"]) == 1
    assert seen_channels == [1, 2]


def test_cancel_mid_channel_drops_the_channel_but_keeps_its_finished_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(S, "MEASURE_BATCH", 1)
    paths = [_frame(tmp_path / f"light_L_{i}.fit", "L") for i in range(3)]
    cache = tmp_path / "cache"

    def measuring(done: int, total: int, filter_: str, overall: dict[str, Any]) -> Optional[bool]:
        return False if done == 2 else None

    result = _scan(paths, cache, FakeMeasure(), types.SimpleNamespace(measuring=measuring))
    assert result == {"channels": {}, "unstable": [], "skipped": [], "cancelled": True}

    # the two batches that finished are cached: a scan now measures only the third frame
    later = FakeMeasure()
    _scan(paths, cache, later)
    assert later.measured == [paths[2]]


# ---- abandoned channels -------------------------------------------------------------------------------------

def test_a_channel_whose_batch_reports_a_problem_is_abandoned(tmp_path: Path) -> None:
    paths = [_frame(tmp_path / "light_L_1.fit", "L"), _frame(tmp_path / "light_Ha_1.fit", "Ha")]
    result = _scan(paths, tmp_path / "cache", FakeMeasure(problem_for={paths[0]}))
    assert result["channels"]["L"] == {"entries": result["channels"]["L"]["entries"], "metrics": {},
                                       "problems": ["measurement failed"]}
    assert set(result["channels"]["Ha"]["metrics"]) == {paths[1]}
    assert result["cancelled"] is False


def test_a_channel_whose_measurement_raises_is_abandoned_and_the_scan_goes_on(tmp_path: Path) -> None:
    paths = [_frame(tmp_path / "light_L_1.fit", "L"), _frame(tmp_path / "light_Ha_1.fit", "Ha")]
    result = _scan(paths, tmp_path / "cache", FakeMeasure(raise_for={paths[0]}))
    assert result["channels"]["L"]["metrics"] == {}
    assert result["channels"]["L"]["problems"] == ["measurement failed"]
    assert set(result["channels"]["Ha"]["metrics"]) == {paths[1]}


# ---- progress --------------------------------------------------------------------------------------------

def test_overall_progress_counts_a_cached_frame_as_done_for_its_channel(tmp_path: Path) -> None:
    first = [_frame(tmp_path / "light_L_1.fit", "L"), _frame(tmp_path / "light_L_2.fit", "L")]
    cache = tmp_path / "cache"
    _scan(first, cache, FakeMeasure())

    new = _frame(tmp_path / "light_L_3.fit", "L")
    calls: list[tuple[int, int, str, dict[str, Any]]] = []

    def measuring(done: int, total: int, filter_: str, overall: dict[str, Any]) -> None:
        calls.append((done, total, filter_, dict(overall)))

    _scan(first + [new], cache, FakeMeasure(), types.SimpleNamespace(measuring=measuring))
    # channel: 2 of 3 frames already measured, so the channel starts at 2 and ends at 3
    assert [(d, t, f) for d, t, f, _ in calls] == [(2, 3, "L"), (3, 3, "L")]
    start = calls[0][3]
    assert (start["total"], start["channel"], start["channels"]) == (1, 1, 1)
    assert start["done"] == 0
    assert calls[-1][3]["done"] == 1


def test_reading_progress_is_told_before_each_file_and_unreadable_files_are_listed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(S, "READING_EVERY_MS", 0)
    caplog.set_level(logging.INFO, logger="loomsiril.frames_scan")
    folder, _ = _folder(tmp_path)
    missing = str(folder / "light_L_gone.fit")
    paths = frame_files_in(str(folder)) + [missing]
    told: list[tuple[int, int, str]] = []

    def reading(done: int, total: int, name: str) -> None:
        told.append((done, total, name))

    result = _scan(paths, tmp_path / "cache", FakeMeasure(), types.SimpleNamespace(reading=reading))
    # one call per file before it is read, then the calls made while the digests settle (the last file, again)
    assert [t[0] for t in told[:len(paths)]] == list(range(1, len(paths) + 1))
    assert all(t == (len(paths), len(paths), "light_L_gone") for t in told[len(paths):])
    assert told[0] == (1, len(paths), "dark_1")
    assert any(name == "light_L_gone" for _, _, name in told)
    assert missing not in [e["path"] for c in result["channels"].values() for e in c["entries"]]
    messages = [r.getMessage() for r in caplog.records if r.name == "loomsiril.frames_scan"]
    assert "Skipped 2 files that are not light subframes: 1 dark, 1 master." in messages
    assert "Could not read 1 file; it is left out." in messages


# ---- nothing is written but the cache ------------------------------------------------------------------------

def test_nothing_is_written_outside_the_cache_directory(tmp_path: Path) -> None:
    folder, _ = _folder(tmp_path)
    cache = tmp_path / "cache"
    before = {p: p.stat().st_mtime_ns for p in folder.iterdir()}
    S.scan(str(folder), None, siril=None, cache_dir=str(cache), measure=FakeMeasure(), config_text=CONFIG)
    assert {p: p.stat().st_mtime_ns for p in folder.iterdir()} == before
    written = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")
                     if p.is_file() and cache not in p.parents and folder not in p.parents)
    assert written == []
    assert (cache / "frame-quality.json").is_file()
