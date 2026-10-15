"""Frame identities and the measurement cache: loomsiril.frames_cache, ported from FrameSelector.js.

These tests need neither Siril, PixInsight, node nor a display. Files live under pytest's tmp_path, which the test
command points at the Siril worktree's scratch area.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from loomsiril import frames as F
from loomsiril import frames_cache as C
from loomsiril import frames_files as FF


def _metrics(value: float = 1.0) -> dict[str, Any]:
    return {k: value for k in F.STORED_KEYS}


def _write(path: Path, data: bytes) -> str:
    path.write_bytes(data)
    return str(path)


def _age(path: str, seconds: float = 60.0) -> None:
    """Puts the mtime well in the past. The ctime stays recent, so tests that need a hit also widen the settle window."""
    past = time.time() - seconds
    os.utime(path, (past, past))


@pytest.fixture
def settled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file is trusted at once. The racy rule (a recent change is never cached) has its own test below."""
    monkeypatch.setattr(FF, "DIGEST_SETTLE_MS", 0)


# ---- digest ------------------------------------------------------------------------------------------

def test_digest_is_sha1_of_the_whole_file(tmp_path: Path) -> None:
    data = os.urandom(9 * 1024 * 1024 + 123)  # more than two 4 MiB chunks
    path = _write(tmp_path / "frame.fit", data)
    assert C.digest(path) == hashlib.sha1(data).hexdigest()


def test_digest_of_an_empty_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "empty.fit", b"")
    assert C.digest(path) == hashlib.sha1(b"").hexdigest()


@pytest.mark.parametrize("make_path", ["missing", "directory"])
def test_digest_unreadable_is_none_with_one_warning(tmp_path: Path, make_path: str) -> None:
    target = tmp_path / ("nope.fit" if make_path == "missing" else "a_dir")
    if make_path == "directory":
        target.mkdir()
    warnings: list[str] = []
    assert C.digest(str(target), warnings.append) is None
    assert len(warnings) == 1
    assert str(target) in warnings[0]


# ---- file_stat, identity_of, file_identity -----------------------------------------------------------

def test_file_stat_fields_and_types(tmp_path: Path) -> None:
    path = _write(tmp_path / "frame.fit", b"abc")
    before = time.time_ns() // 1_000_000
    st = C.file_stat(path)
    after = time.time_ns() // 1_000_000
    assert st is not None
    assert set(st) == {"size", "mtime", "created", "at"}
    assert st["size"] == 3
    assert isinstance(st["mtime"], int) and isinstance(st["at"], int)
    assert isinstance(st["created"], int)  # st_birthtime or st_ctime: always present on these systems
    assert before <= st["at"] <= after


def test_file_stat_missing_is_none(tmp_path: Path) -> None:
    assert C.file_stat(str(tmp_path / "nope.fit")) is None


def test_identity_of_none_cases() -> None:
    stat = {"size": 3, "mtime": 5, "created": 4, "at": 9}
    assert C.identity_of(None, "a" * 40) is None
    assert C.identity_of(stat, None) is None
    assert C.identity_of(None, None) is None
    assert C.identity_of(stat, "a" * 40) == {"digest": "a" * 40, "size": 3, "mtime": 5}


def test_file_identity_matches_stat_and_digest(tmp_path: Path) -> None:
    path = _write(tmp_path / "frame.fit", b"xyz")
    ident = C.file_identity(path)
    assert ident == {"digest": hashlib.sha1(b"xyz").hexdigest(), "size": 3,
                     "mtime": os.stat(path).st_mtime_ns // 1_000_000}
    assert C.file_identity(str(tmp_path / "gone.fit")) is None


# ---- Hasher ------------------------------------------------------------------------------------------

def test_hasher_job_digests_every_added_path(tmp_path: Path) -> None:
    paths = [_write(tmp_path / f"f{i}.fit", bytes([i]) * (i + 1)) for i in range(6)]
    hasher = C.Hasher()
    try:
        job = hasher.start(paths[:2])
        for p in paths[2:]:
            job.add(p)
        r = job.wait()
    finally:
        hasher.close()
    assert r["cancelled"] is False
    assert r["digests"] == {p: hashlib.sha1(Path(p).read_bytes()).hexdigest() for p in paths}


# ---- MeasurementStore --------------------------------------------------------------------------------

def _identity(digest_hex: str = "a" * 40) -> dict[str, Any]:
    return {"digest": digest_hex, "size": 3, "mtime": 5}


def test_store_save_then_new_store_finds_the_metrics(tmp_path: Path) -> None:
    store = C.MeasurementStore(str(tmp_path / "cache"), "config A")
    store.store(_identity(), _metrics(2.5))
    store.save()
    again = C.MeasurementStore(str(tmp_path / "cache"), "config A")
    assert again.cached(_identity()) == _metrics(2.5)


def test_store_misses_on_other_config_version_or_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = str(tmp_path / "cache")
    store = C.MeasurementStore(cache, "config A")
    store.store(_identity(), _metrics())
    store.save()
    assert C.MeasurementStore(cache, "config B").cached(_identity()) is None
    assert C.MeasurementStore(cache, "config A").cached(_identity("b" * 40)) is None
    monkeypatch.setattr(C, "MEASURE_VERSION", "siril-v2")
    assert C.MeasurementStore(cache, "config A").cached(_identity()) is None


def test_store_entry_missing_a_stored_key_is_a_miss(tmp_path: Path) -> None:
    cache = str(tmp_path / "cache")
    partial = _metrics()
    del partial[F.STORED_KEYS[0]]
    store = C.MeasurementStore(cache, "config A")
    store.store(_identity(), partial)
    store.save()
    assert C.MeasurementStore(cache, "config A").cached(_identity()) is None


def test_store_corrupt_json_is_an_empty_table(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / C.TABLE_NAME).write_text("{not json", encoding="utf-8")
    store = C.MeasurementStore(str(cache), "config A")
    assert store.load_table() == {}
    assert store.cached(_identity()) is None


def test_store_non_object_json_is_an_empty_table(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / C.TABLE_NAME).write_text("[1, 2]", encoding="utf-8")
    assert C.MeasurementStore(str(cache), "config A").load_table() == {}


def test_save_without_a_change_does_not_rewrite(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    store = C.MeasurementStore(str(cache), "config A")
    store.store(_identity(), _metrics())
    store.save()
    table_file = cache / C.TABLE_NAME
    old = time.time() - 3600
    os.utime(table_file, (old, old))
    store.save()  # nothing changed since the last save
    assert table_file.stat().st_mtime == pytest.approx(old, abs=1)


def test_save_creates_the_directory(tmp_path: Path) -> None:
    cache = tmp_path / "new" / "dir"
    store = C.MeasurementStore(str(cache), "config A")
    store.store(_identity(), _metrics())
    store.save()
    assert (cache / C.TABLE_NAME).is_file()


def test_save_failure_only_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    store = C.MeasurementStore(str(blocker), "config A")  # a file where the directory should be
    store.store(_identity(), _metrics())
    store.save()  # must not raise
    assert "could not save measurements" in caplog.text


# ---- Identities --------------------------------------------------------------------------------------

@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records every path digest() is asked for, through the module global the hasher calls."""
    calls: list[str] = []
    real = C.digest

    def counting(path: str, warn: Any = None) -> Any:
        calls.append(path)
        return real(path, warn)

    monkeypatch.setattr(C, "digest", counting)
    return calls


def _scan(paths: list[str], cache_dir: str | None, use_cache: bool) -> dict[str, Any]:
    hasher = C.Hasher()
    try:
        ids = C.Identities(hasher, cache_dir=cache_dir, use_cache=use_cache)
        for p in paths:
            ids.request(p)
        return ids.finish()
    finally:
        hasher.close()


def test_identities_cache_hit_skips_unchanged_files(tmp_path: Path, counted: list[str],
                                                    settled: None) -> None:
    cache = str(tmp_path / "cache")
    paths = []
    for i in range(3):
        p = _write(tmp_path / f"f{i}.fit", f"frame {i}".encode())
        _age(p)
        paths.append(p)

    first = _scan(paths, cache, use_cache=True)
    assert sorted(counted) == sorted(paths)
    assert all(first["ids"][p] is not None for p in paths)

    counted.clear()
    second = _scan(paths, cache, use_cache=True)
    assert counted == []
    assert second["ids"] == first["ids"]


def test_touching_a_file_digests_only_that_one(tmp_path: Path, counted: list[str], settled: None) -> None:
    cache = str(tmp_path / "cache")
    paths = []
    for i in range(3):
        p = _write(tmp_path / f"f{i}.fit", f"frame {i}".encode())
        _age(p)
        paths.append(p)
    first = _scan(paths, cache, use_cache=True)

    touched = paths[1]
    later = time.time() + 30
    os.utime(touched, (later, later))  # a new mtime; the size is unchanged
    counted.clear()
    second = _scan(paths, cache, use_cache=True)

    assert counted == [touched]
    assert second["ids"][touched]["digest"] == first["ids"][touched]["digest"]
    assert second["ids"][touched]["mtime"] != first["ids"][touched]["mtime"]


def test_a_just_written_file_is_not_cached(tmp_path: Path, counted: list[str]) -> None:
    """The racy rule: a file changed within the settle window cannot vouch for its own digest, so it is read again."""
    cache = str(tmp_path / "cache")
    p = _write(tmp_path / "fresh.fit", b"just written")
    _scan([p], cache, use_cache=True)
    counted.clear()
    _scan([p], cache, use_cache=True)
    assert counted == [p]


def test_identities_without_cache_never_persist(tmp_path: Path, settled: None) -> None:
    p = _write(tmp_path / "f.fit", b"data")
    _age(p)
    _scan([p], str(tmp_path / "cache"), use_cache=False)
    assert not (tmp_path / "cache" / C.DIGEST_CACHE_NAME).exists()


def test_identities_missing_file_gives_none(tmp_path: Path) -> None:
    gone = str(tmp_path / "gone.fit")
    r = _scan([gone], None, use_cache=False)
    assert r["ids"] == {gone: None}


def test_finish_with_should_stop_true_is_cancelled(tmp_path: Path) -> None:
    paths = [_write(tmp_path / f"f{i}.fit", os.urandom(64)) for i in range(3)]
    hasher = C.Hasher()
    try:
        ids = C.Identities(hasher)
        for p in paths:
            ids.request(p)
        r = ids.finish(lambda: True)
    finally:
        hasher.close()
    assert r["cancelled"] is True
    assert set(r["ids"]) == set(paths)


def test_abandon_remembers_only_the_digests_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  settled: None) -> None:
    cache = str(tmp_path / "cache")
    fast = [_write(tmp_path / f"fast{i}.fit", f"fast {i}".encode()) for i in range(2)]
    slow = _write(tmp_path / "slow.fit", b"slow frame")
    for p in fast + [slow]:
        _age(p)

    release = threading.Event()
    real = C.digest

    def gated(path: str, warn: Any = None) -> Any:
        if path == slow:
            release.wait(timeout=10)
        return real(path, warn)

    monkeypatch.setattr(C, "digest", gated)
    hasher = C.Hasher()
    try:
        ids = C.Identities(hasher, cache_dir=cache, use_cache=True)
        for p in fast + [slow]:
            ids.request(p)
        deadline = time.time() + 10
        while time.time() < deadline and not set(fast) <= set(ids._job.digests):
            time.sleep(0.01)
        assert set(fast) <= set(ids._job.digests)
        ids.abandon()
        release.set()  # the slow digest ends now, after the abandon, and must not be remembered
    finally:
        hasher.close()

    table = json.loads((tmp_path / "cache" / C.DIGEST_CACHE_NAME).read_text(encoding="utf-8"))
    assert set(table) == set(fast)
    for p in fast:
        assert table[p]["digest"] == hashlib.sha1(Path(p).read_bytes()).hexdigest()


# ---- cached_split ------------------------------------------------------------------------------------

def test_cached_split_separates_cached_from_needed(tmp_path: Path) -> None:
    store = C.MeasurementStore(str(tmp_path / "cache"), "config A")
    group = []
    for i in range(3):
        ident = _identity(f"{i}" * 40)
        group.append({"path": f"/frames/f{i}.fit", "identity": ident})
    store.store(group[1]["identity"], _metrics(7.0))
    split = C.cached_split(group, store)
    assert split["need"] == ["/frames/f0.fit", "/frames/f2.fit"]
    assert split["metrics"] == {"/frames/f1.fit": _metrics(7.0)}


# ---- helpers ------------------------------------------------------------------------------------------

def test_digest_cache_rules_are_the_frames_files_ones() -> None:
    """The pure rules are called from frames_files, not copied: the names resolve to the same objects."""
    assert C.digest_cache_hit is FF.digest_cache_hit
    assert C.digest_cache_record is FF.digest_cache_record
    assert C.cache_entry_usable is F.cache_entry_usable


def test_config_signature_is_twelve_hex_digits() -> None:
    sig = C.config_signature("settings")
    assert sig == hashlib.sha1("settings".encode("utf-8")).hexdigest()[:12]
    assert len(sig) == 12
