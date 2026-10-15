"""The preview cache equals Loom's JavaScript on every recorded pure case, and its file-system rules hold.

tests/oracle/fs_preview_cache.json holds, for each case, the arguments and the JS result (recorded by
tools/oracle/fs_preview_cache.js through tools/record_oracle.js in node). The pure cases (previewKeyOf and
previewEvictions) are replayed on loomsiril.frames_preview_cache: floats to 1e-9 relative, NaN equal to NaN, the rest
exactly. The file-system functions are tested against the rules in the JavaScript comments in temporary folders. The
suite needs neither node nor Loom.
"""

from __future__ import annotations

import copy
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from loomsiril import frames_preview_cache as P
from conftest import ORACLE_DIR

PATH = ORACLE_DIR / "fs_preview_cache.json"
if not PATH.exists():
    pytest.skip("oracle fs_preview_cache.json not recorded (run tools/record_oracle.js)", allow_module_level=True)

CASES: dict[str, dict[str, Any]] = json.loads(PATH.read_text(encoding="utf-8"))["cases"]

#: The JS name of each function the recorder calls, to its Python port.
DISPATCH = {
    "previewKeyOf": P.preview_key_of,
    "previewEvictions": P.preview_evictions,
}

#: The JS constants the recorder reads, to their Python twins.
CONSTANTS = {"PREVIEW_VERSION": P.PREVIEW_VERSION, "PREVIEW_CAP_BYTES": P.PREVIEW_CAP_BYTES}

UNDEFINED_MARK = "__undefined__"


def _call(case: dict[str, Any]) -> Any:
    args = copy.deepcopy(case["args"])
    return DISPATCH[case["fn"]](*args)


def _mismatch(expected: Any, actual: Any, where: str = "out") -> Optional[str]:
    """Where ``actual`` first differs from the JS ``expected``, or None when they agree."""
    if expected == UNDEFINED_MARK:
        return f"{where}: JS undefined, Python {actual!r}" if actual is not None else None
    if isinstance(expected, bool) or isinstance(actual, bool):
        return None if expected is actual else f"{where}: {expected!r} != {actual!r}"
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        if math.isnan(expected) or math.isnan(actual):
            return None if (math.isnan(expected) and math.isnan(actual)) else f"{where}: {expected!r} != {actual!r}"
        return None if math.isclose(expected, actual, rel_tol=1e-9, abs_tol=0.0) else f"{where}: {expected!r} != {actual!r}"
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return f"{where}: list {expected!r} != {actual!r}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            bad = _mismatch(e, a, f"{where}[{i}]")
            if bad:
                return bad
        return None
    return None if expected == actual else f"{where}: {expected!r} != {actual!r}"


def _replay(key: str) -> Optional[str]:
    case = CASES[key]
    if "value" in case:
        return _mismatch(case["out"], CONSTANTS[case["value"]], key)
    return _mismatch(case["out"], _call(case), key)


@pytest.mark.parametrize("key", sorted(CASES))
def test_case_matches_js(key: str) -> None:
    bad = _replay(key)
    assert bad is None, bad


def test_recorder_has_enough_pure_cases() -> None:
    pure = [k for k, c in CASES.items() if "fn" in c]
    assert len(pure) >= 30
    assert {CASES[k]["fn"] for k in pure} == set(DISPATCH)


def test_every_recorded_key_case_has_nested_params() -> None:
    nested = [k for k in CASES if k.startswith("key_of/") and "nested" in k]
    assert len(nested) >= 6


def test_eviction_cap_is_strict_and_keep_is_protected() -> None:
    """Without the strict cap test the exactly-at-cap case would evict; without keep the kept entry would go."""
    assert _replay("evictions/exactly_at_cap") is None
    assert CASES["evictions/exactly_at_cap"]["out"] == []
    assert CASES["evictions/keep_never_evicted"]["out"] == ["b"]


# ---- pure functions: the rules the JavaScript comments state, beyond the recorded values -----------------

def test_key_depends_on_every_field() -> None:
    base = P.preview_key_of("/a.fit", 10, "2026-01-01T00:00:00.000Z", "screen", {"w": 1})
    variants = [
        P.preview_key_of("/b.fit", 10, "2026-01-01T00:00:00.000Z", "screen", {"w": 1}),
        P.preview_key_of("/a.fit", 11, "2026-01-01T00:00:00.000Z", "screen", {"w": 1}),
        P.preview_key_of("/a.fit", 10, "2026-01-01T00:00:00.001Z", "screen", {"w": 1}),
        P.preview_key_of("/a.fit", 10, "2026-01-01T00:00:00.000Z", "thumb", {"w": 1}),
        P.preview_key_of("/a.fit", 10, "2026-01-01T00:00:00.000Z", "screen", {"w": 2}),
    ]
    assert len({base, *variants}) == 6


def test_key_ignores_parameter_insertion_order() -> None:
    a = P.preview_key_of("/a.fit", 1, "t", "k", {"b": 1, "a": 2})
    b = P.preview_key_of("/a.fit", 1, "t", "k", {"a": 2, "b": 1})
    assert a == b


def test_key_is_sha1_hex_of_the_documented_string() -> None:
    from loomsiril.loom.cache import hash_str

    text = "preview|1|screen|/a.fit|10|t|w=1"
    assert P.preview_key_of("/a.fit", 10, "t", "screen", {"w": 1}) == hash_str(text)
    assert len(hash_str(text)) == 40


def test_eviction_never_removes_more_than_needed() -> None:
    entries = [{"key": "a", "bytes": 30, "used": 1}, {"key": "b", "bytes": 30, "used": 2},
               {"key": "c", "bytes": 30, "used": 3}]
    gone = P.preview_evictions(entries, 60, None)
    assert gone == ["a"]
    assert P.preview_evictions(entries, 90, None) == []


def test_eviction_leaves_input_unchanged() -> None:
    entries = [{"key": "c", "bytes": 30, "used": 3}, {"key": "a", "bytes": 30, "used": 1}]
    snapshot = copy.deepcopy(entries)
    P.preview_evictions(entries, 10, None)
    assert entries == snapshot


# ---- file system: a temporary preview folder ---------------------------------------------------------------

def _cache(tmp_path: Path) -> P.PreviewCache:
    return P.PreviewCache(str(tmp_path))


def _store(cache: P.PreviewCache, key: str, nbytes: int, ext: str = ".png") -> str:
    folder = cache.preview_dir()
    os.makedirs(folder, exist_ok=True)
    path = cache.preview_path(key, ext)
    with open(path, "wb") as f:
        f.write(b"x" * nbytes)
    return path


def test_preview_dir_defaults_under_cache_dir(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert cache.preview_dir() == os.path.join(str(tmp_path), "Loom-previews")


def test_preview_dir_override_wins(tmp_path: Path) -> None:
    override = str(tmp_path / "scratch")
    cache = P.PreviewCache(str(tmp_path / "other"), override)
    assert cache.preview_dir() == override
    assert cache.preview_index_path() == os.path.join(override, "index.json")


def test_preview_path_takes_the_renderers_extension(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert cache.preview_path("abc", ".npy") == os.path.join(cache.preview_dir(), "abc.npy")
    assert cache.preview_path("abc", ".png") == os.path.join(cache.preview_dir(), "abc.png")


def test_preview_key_is_none_for_a_missing_file(tmp_path: Path) -> None:
    assert _cache(tmp_path).preview_key(str(tmp_path / "nope.fit"), "screen", None) is None


def test_preview_key_matches_key_of_its_stat(tmp_path: Path) -> None:
    src = tmp_path / "light.fit"
    src.write_bytes(b"abcdef")
    ms = 1_767_225_600_123
    os.utime(src, ns=(ms * 1_000_000, ms * 1_000_000))
    key = _cache(tmp_path).preview_key(str(src), "screen", {"w": 640})
    assert key == P.preview_key_of(str(src), 6, "2026-01-01T00:00:00.123Z", "screen", {"w": 640})


def test_preview_key_changes_when_the_file_is_rewritten(tmp_path: Path) -> None:
    src = tmp_path / "light.fit"
    src.write_bytes(b"abcdef")
    os.utime(src, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    cache = _cache(tmp_path)
    first = cache.preview_key(str(src), "screen", None)
    src.write_bytes(b"abcdeg")
    os.utime(src, ns=(1_000_000_000_000_000_000 + 5_000_000, 1_000_000_000_000_000_000 + 5_000_000))
    assert cache.preview_key(str(src), "screen", None) != first


def test_index_is_empty_when_missing(tmp_path: Path) -> None:
    assert _cache(tmp_path).read_preview_index() == {}


@pytest.mark.parametrize("text", ["{not json", "null", "[1, 2]", '{"a": NaN}', '"text"', ""])
def test_corrupt_index_reads_as_empty(tmp_path: Path, text: str) -> None:
    cache = _cache(tmp_path)
    os.makedirs(cache.preview_dir())
    Path(cache.preview_index_path()).write_text(text, encoding="utf-8")
    assert cache.read_preview_index() == {}


def test_note_preview_use_writes_the_index(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.note_preview_use("abc")
    idx = json.loads(Path(cache.preview_index_path()).read_text(encoding="utf-8"))
    assert set(idx) == {"abc"}
    assert isinstance(idx["abc"], int) and idx["abc"] > 0


def test_note_preview_use_is_strictly_later_within_one_millisecond(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path)
    monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
    for key in ["a", "b", "c"]:
        cache.note_preview_use(key)
    idx = cache.read_preview_index()
    assert idx["a"] < idx["b"] < idx["c"]
    assert idx["c"] == 1_700_000_000_000 + 2


def test_note_preview_use_moves_a_key_to_the_front(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path)
    monkeypatch.setattr(time, "time_ns", lambda: 1_000_000_000_000_000_000)
    cache.note_preview_use("a")
    cache.note_preview_use("b")
    monkeypatch.setattr(time, "time_ns", lambda: 1_000_000_000_000_000_000 + 5_000_000_000)
    cache.note_preview_use("a")
    idx = cache.read_preview_index()
    assert idx["a"] > idx["b"]


def test_entries_are_listed_with_their_use_and_size(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "k1", 100)
    _store(cache, "k2", 250, ".npy")
    Path(cache.preview_index_path()).write_text('{"k1": 42}', encoding="utf-8")
    entries = {e["key"]: e for e in cache.preview_entries()}
    assert set(entries) == {"k1", "k2"}
    assert entries["k1"]["bytes"] == 100 and entries["k1"]["used"] == 42
    assert entries["k2"]["bytes"] == 250 and entries["k2"]["used"] == 0
    assert cache.preview_bytes() == 350


def test_entries_skip_the_index_and_dot_files(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "k1", 10)
    Path(cache.preview_index_path()).write_text("{}", encoding="utf-8")
    Path(cache.preview_dir(), ".hidden").write_bytes(b"12345")
    os.makedirs(os.path.join(cache.preview_dir(), "subfolder"))
    assert [e["key"] for e in cache.preview_entries()] == ["k1"]


def test_entries_of_a_missing_folder_are_empty(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert cache.preview_entries() == []
    assert cache.preview_bytes() == 0


def test_trim_removes_the_oldest_first_and_keeps_the_rest(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "old", 40)
    _store(cache, "mid", 40)
    _store(cache, "new", 40)
    Path(cache.preview_index_path()).write_text('{"old": 1, "mid": 2, "new": 3}', encoding="utf-8")
    assert cache.trim_previews(100) == 1
    assert not os.path.exists(cache.preview_path("old", ".png"))
    assert os.path.exists(cache.preview_path("mid", ".png"))
    assert os.path.exists(cache.preview_path("new", ".png"))
    assert cache.preview_bytes() == 80


def test_trim_never_evicts_the_kept_entry(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "keep", 500)
    _store(cache, "other", 100)
    Path(cache.preview_index_path()).write_text('{"keep": 1, "other": 2}', encoding="utf-8")
    cache.trim_previews(100, "keep")
    assert os.path.exists(cache.preview_path("keep", ".png"))
    assert not os.path.exists(cache.preview_path("other", ".png"))


def test_trim_with_the_cap_already_met_removes_nothing(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "a", 50)
    assert cache.trim_previews(50) == 0
    assert os.path.exists(cache.preview_path("a", ".png"))


def test_trim_evicts_across_formats_by_key(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "old", 60, ".png")
    _store(cache, "new", 60, ".npy")
    Path(cache.preview_index_path()).write_text('{"old": 1, "new": 2}', encoding="utf-8")
    assert cache.trim_previews(70) == 1
    assert not os.path.exists(cache.preview_path("old", ".png"))
    assert os.path.exists(cache.preview_path("new", ".npy"))


def test_trim_prunes_index_lines_for_removed_files(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "old", 60)
    _store(cache, "new", 60)
    Path(cache.preview_index_path()).write_text('{"old": 1, "new": 2, "ghost": 3}', encoding="utf-8")
    cache.trim_previews(70)
    idx = json.loads(Path(cache.preview_index_path()).read_text(encoding="utf-8"))
    assert idx == {"new": 2}


def test_prune_leaves_the_index_alone_when_nothing_is_stale(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "a", 5)
    text = '{"a":7}'
    Path(cache.preview_index_path()).write_text(text, encoding="utf-8")
    cache.prune_preview_index()
    assert Path(cache.preview_index_path()).read_text(encoding="utf-8") == text


def test_index_is_written_compactly_like_json_stringify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path)
    monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
    cache.note_preview_use("abc")
    assert Path(cache.preview_index_path()).read_text(encoding="utf-8") == '{"abc":1700000000000}'


def test_note_preview_use_survives_a_corrupt_index(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    os.makedirs(cache.preview_dir())
    Path(cache.preview_index_path()).write_text("{broken", encoding="utf-8")
    cache.note_preview_use("abc")
    assert set(cache.read_preview_index()) == {"abc"}


def test_trim_survives_a_missing_folder(tmp_path: Path) -> None:
    assert _cache(tmp_path).trim_previews(0) == 0


def test_every_ported_function_is_present() -> None:
    for name in ["preview_dir", "preview_path", "preview_index_path", "preview_key", "read_preview_index",
                 "note_preview_use", "preview_entries", "preview_bytes", "trim_previews", "prune_preview_index"]:
        assert callable(getattr(P.PreviewCache, name)), name
    assert callable(P.preview_key_of) and callable(P.preview_evictions)
    assert P.PREVIEW_CAP_BYTES == 1024 * 1024 * 1024
    assert P.PREVIEW_VERSION == "1"


# ---- fix round: in-progress files, memos, clearing, atomic index, races, entries without keys -------------

@pytest.mark.parametrize("name", ["abc.png.part", "abc.npy.tmp", "stretch-abc.json.part", "x.tmp"])
def test_entries_ignore_in_progress_files(tmp_path: Path, name: str) -> None:
    cache = _cache(tmp_path)
    _store(cache, "real", 10)
    Path(cache.preview_dir(), name).write_bytes(b"y" * 999)
    assert [e["key"] for e in cache.preview_entries()] == ["real"]
    assert cache.preview_bytes() == 10


@pytest.mark.parametrize("name", ["stretch-abc123.json", "stretch-0f.json"])
def test_entries_ignore_stretch_memos(tmp_path: Path, name: str) -> None:
    cache = _cache(tmp_path)
    _store(cache, "real", 10)
    Path(cache.preview_dir(), name).write_text('{"midtone": 0.25}', encoding="utf-8")
    assert [e["key"] for e in cache.preview_entries()] == ["real"]


def test_a_memo_is_not_evicted_as_a_preview(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "old", 60)
    _store(cache, "new", 60)
    memo = Path(cache.preview_dir(), "stretch-old.json")
    memo.write_text("{}", encoding="utf-8")
    Path(cache.preview_index_path()).write_text('{"old": 1, "new": 2}', encoding="utf-8")
    assert cache.trim_previews(70) == 1
    assert memo.exists()
    assert not os.path.exists(cache.preview_path("old", ".png"))


def test_a_memo_does_not_count_toward_the_cap(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "a", 50)
    Path(cache.preview_dir(), "stretch-a.json").write_bytes(b"z" * 500)
    assert cache.trim_previews(50) == 0
    assert os.path.exists(cache.preview_path("a", ".png"))


def test_clear_empties_the_folder_and_reports_preview_bytes(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _store(cache, "a", 1000)
    _store(cache, "b", 1000, ".npy")
    Path(cache.preview_dir(), "stretch-a.json").write_bytes(b"m" * 77)
    Path(cache.preview_dir(), "left.part").write_bytes(b"p" * 33)
    Path(cache.preview_index_path()).write_text('{"a": 1}', encoding="utf-8")
    os.makedirs(os.path.join(cache.preview_dir(), "sub"))
    assert cache.clear_previews() == 2000
    assert os.listdir(cache.preview_dir()) == ["sub"]
    assert cache.preview_entries() == []


def test_clear_of_a_missing_folder_frees_nothing(tmp_path: Path) -> None:
    assert _cache(tmp_path).clear_previews() == 0


def test_index_is_replaced_atomically_in_the_same_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path)
    _store(cache, "old", 60)
    _store(cache, "new", 60)
    Path(cache.preview_index_path()).write_text('{"old": 1, "new": 2, "ghost": 3}', encoding="utf-8")
    moves: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: str, dst: str) -> None:
        moves.append((os.path.dirname(src), dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    cache.trim_previews(70)
    assert moves == [(cache.preview_dir(), cache.preview_index_path())]
    assert json.loads(Path(cache.preview_index_path()).read_text(encoding="utf-8")) == {"new": 2}
    assert not [n for n in os.listdir(cache.preview_dir()) if n.endswith(".tmp")]


def test_failed_index_write_keeps_the_old_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path)
    _store(cache, "a", 10)
    Path(cache.preview_index_path()).write_text('{"a":5}', encoding="utf-8")

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    cache.note_preview_use("a")
    assert Path(cache.preview_index_path()).read_text(encoding="utf-8") == '{"a":5}'
    assert not [n for n in os.listdir(cache.preview_dir()) if n.endswith(".tmp")]


def test_a_file_that_vanishes_before_stat_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path)
    _store(cache, "keep", 10)
    gone = _store(cache, "gone", 20)
    real_stat = os.stat

    def racing_stat(path: object, *args: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == gone:
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", racing_stat)
    assert [e["key"] for e in cache.preview_entries()] == ["keep"]
    assert cache.preview_bytes() == 10


def test_entry_without_a_key_is_counted_but_never_evicted() -> None:
    entries = [{"bytes": 40, "used": 1}, {"key": "b", "bytes": 40, "used": 2}]
    assert P.preview_evictions(entries, 50, None) == ["b"]
    assert P.preview_evictions([{"bytes": 40, "used": 1}], 10, None) == []


def test_keep_none_protects_nothing_but_a_string_key() -> None:
    entries = [{"key": "a", "bytes": 40, "used": 1}, {"key": "b", "bytes": 40, "used": 2}]
    assert P.preview_evictions(entries, 50, None) == ["a"]
    assert P.preview_evictions(entries, 50, "a") == ["b"]
