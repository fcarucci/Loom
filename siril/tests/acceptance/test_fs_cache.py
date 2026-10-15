"""Measurement cache: a measured frame is found again after reopening; a changed frame, configuration or version
misses; the scan writes nothing but the cache. Skips until ``loomsiril.frames_cache`` is on the branch."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fs_helpers import listing

fc = pytest.importorskip("loomsiril.frames_cache")
frames = pytest.importorskip("loomsiril.frames")

CONFIG = "setfindstar reset; register -2pass"


def _metrics(fwhm: float = 3.1) -> dict:
    """A full stored measurement (every stored key present)."""
    return {"fwhm": fwhm, "eccentricity": 0.2, "noise": 280.0, "stars": 117, "psfSNR": 90.0, "background": 5592.0,
            "snrWeight": None, "altitude": 52.5, "psfFlux": 2.6e5}


def _frame(folder: Path, name: str = "a.fit", payload: bytes = b"frame bytes 0123456789" * 50) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(payload)
    return str(p)


def _store(cache: Path, config: str = CONFIG):
    return fc.MeasurementStore(str(cache), config)


def test_a_measured_frame_is_found_again_after_reopening(tmp_path: Path) -> None:
    """Results are cached ... so re-opening a folder is instant: a measured frame is found again by a new store on
    the same directory."""
    p = _frame(tmp_path / "lights")
    ident = fc.file_identity(p)
    assert ident is not None
    first = _store(tmp_path / "cache")
    assert first.cached(ident) is None
    first.store(ident, _metrics())
    first.save()
    again = _store(tmp_path / "cache")
    assert again.cached(fc.file_identity(p)) == _metrics()


def test_a_changed_frame_is_measured_again(tmp_path: Path) -> None:
    """A changed frame is re-measured: touching the file's bytes makes it a miss (even with its size and time put
    back, as a replacement can)."""
    p = _frame(tmp_path / "lights")
    st = os.stat(p)
    s = _store(tmp_path / "cache")
    s.store(fc.file_identity(p), _metrics())
    s.save()
    data = bytearray(Path(p).read_bytes())
    data[100] ^= 0xFF                                         # same size
    Path(p).write_bytes(bytes(data))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))          # same time
    assert os.stat(p).st_size == st.st_size
    assert _store(tmp_path / "cache").cached(fc.file_identity(p)) is None
    # a different size is a miss as well
    Path(p).write_bytes(b"short")
    assert _store(tmp_path / "cache").cached(fc.file_identity(p)) is None


def test_a_different_measurement_configuration_is_a_miss(tmp_path: Path) -> None:
    """Results are cached keyed by ... the measurement configuration: a different configuration text misses."""
    p = _frame(tmp_path / "lights")
    ident = fc.file_identity(p)
    s = _store(tmp_path / "cache")
    s.store(ident, _metrics())
    s.save()
    assert _store(tmp_path / "cache").cached(ident) == _metrics()
    assert _store(tmp_path / "cache", CONFIG + "; setfindstar -sigma=3").cached(ident) is None
    assert fc.config_signature(CONFIG) != fc.config_signature(CONFIG + " ")


def test_a_different_version_is_a_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Results are cached keyed by ... a version prefix: a new measurement version finds nothing."""
    p = _frame(tmp_path / "lights")
    ident = fc.file_identity(p)
    s = _store(tmp_path / "cache")
    s.store(ident, _metrics())
    s.save()
    monkeypatch.setattr(fc, "MEASURE_VERSION", fc.MEASURE_VERSION + "-next")
    assert _store(tmp_path / "cache").cached(ident) is None


def test_each_frame_keeps_its_own_measurement(tmp_path: Path) -> None:
    """Results are matched to frames by what the frame is, not by position: two frames, two entries."""
    a = fc.file_identity(_frame(tmp_path / "lights", "a.fit", b"A" * 4000))
    b = fc.file_identity(_frame(tmp_path / "lights", "b.fit", b"B" * 4000))
    s = _store(tmp_path / "cache")
    s.store(a, _metrics(3.0))
    s.store(b, _metrics(5.0))
    s.save()
    t = _store(tmp_path / "cache")
    assert t.cached(a)["fwhm"] == 3.0
    assert t.cached(b)["fwhm"] == 5.0


def test_a_damaged_cache_file_is_an_empty_cache(tmp_path: Path) -> None:
    """Re-opening a folder never fails because of the cache: a corrupt table is simply empty."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / fc.TABLE_NAME).write_text("{not json", encoding="utf-8")
    p = _frame(tmp_path / "lights")
    assert _store(cache).cached(fc.file_identity(p)) is None


def test_reopening_a_folder_needs_no_new_measurement(tmp_path: Path) -> None:
    """Re-opening a folder is instant: with every frame cached, nothing is left to measure; one changed frame is."""
    lights = tmp_path / "lights"
    paths = [_frame(lights, f"f{i}.fit", bytes([i]) * 3000) for i in range(3)]
    group = [{"path": p, "identity": fc.file_identity(p)} for p in paths]
    s = _store(tmp_path / "cache")
    for g in group:
        s.store(g["identity"], _metrics())
    s.save()
    again = fc.cached_split([{"path": p, "identity": fc.file_identity(p)} for p in paths], _store(tmp_path / "cache"))
    assert again["need"] == []
    assert sorted(again["metrics"]) == sorted(paths)
    Path(paths[1]).write_bytes(b"changed" * 500)
    split = fc.cached_split([{"path": p, "identity": fc.file_identity(p)} for p in paths], _store(tmp_path / "cache"))
    assert split["need"] == [paths[1]]


def test_the_cache_files_are_the_only_files_written(tmp_path: Path) -> None:
    """The scan can be cancelled and writes nothing but the cache: the folder of frames is untouched, and the cache
    directory holds only the measurement table and the digest cache."""
    lights = tmp_path / "lights"
    paths = [_frame(lights, f"f{i}.fit", bytes([i + 1]) * 3000) for i in range(3)]
    before = {p: (Path(p).read_bytes(), os.stat(p).st_mtime_ns) for p in paths}
    listed = listing(lights)
    cache = tmp_path / "cache"
    ids = fc.Identities(cache_dir=str(cache), use_cache=True)
    for p in paths:
        ids.request(p)
    done = ids.finish()
    assert not done["cancelled"]
    s = _store(cache)
    for p in paths:
        s.store(done["ids"][p], _metrics())
    s.save()
    assert listing(lights) == listed
    assert {p: (Path(p).read_bytes(), os.stat(p).st_mtime_ns) for p in paths} == before
    assert set(listing(cache)) <= {fc.TABLE_NAME, fc.DIGEST_CACHE_NAME}
    assert fc.TABLE_NAME in listing(cache)
    assert {x.split(os.sep)[0] for x in listing(tmp_path)} == {"lights", "cache"}


def test_cancelling_a_scan_writes_no_measurements(tmp_path: Path) -> None:
    """The scan can be cancelled and writes nothing but the cache: a cancelled identity pass leaves no table."""
    lights = tmp_path / "lights"
    paths = [_frame(lights, f"f{i}.fit", bytes([i + 1]) * 3000) for i in range(3)]
    cache = tmp_path / "cache"
    ids = fc.Identities(cache_dir=str(cache), use_cache=True)
    for p in paths:
        ids.request(p)
    ids.abandon()
    assert fc.TABLE_NAME not in (os.listdir(cache) if cache.exists() else [])
    assert sorted(listing(lights)) == sorted(f"f{i}.fit" for i in range(3))
