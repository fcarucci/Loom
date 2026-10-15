"""The Siril master-flat engine (loomsiril.master_flat_siril, MasterFlat.js 1088-1398) against a stub Siril.

The stub records every command, follows ``cd`` and the compression switch, and on ``calibrate`` and ``stack``
writes FITS files into the working folder named as Siril names them (``pp_f_00001.fit``, ``integrated.fit``). The
command lines checked here are the ones measured on siril-cli 1.4.4. The stub needs no Siril. One test runs the
engine in real siril-cli and is marked siril (skipped locally).
"""

from __future__ import annotations

import errno
import os
import re
import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import numpy as np
import pytest

from loomsiril import master_flat as mf
from loomsiril import master_flat_run as mfr
from loomsiril import master_flat_siril as mfs
from loomsiril.io_fits import read_fits, write_fits
from loom.siril_run import run_pyscript

H, W = 12, 16


class _Header:
    """What ``read_header`` returns: ``info`` with the geometry, and ``keyword(name)`` as text."""

    def __init__(self, info: Any, header: dict[str, Any]) -> None:
        self.info = info
        self._header = header

    def keyword(self, name: str) -> Optional[str]:
        v = self._header.get(name)
        return None if v is None else str(v)


def read_header(path: Any) -> Optional[_Header]:
    path = os.fspath(path)
    if not os.path.exists(path):
        return None
    planes, header = read_fits(path)
    info = SimpleNamespace(width=int(planes[0].shape[1]), height=int(planes[0].shape[0]))
    return _Header(info, header)


def write_frame(path: Path, plane: np.ndarray, header: dict[str, Any]) -> None:
    write_fits(path, [plane.astype(np.float32)], header=header, bitpix=-32)


def make_darks(folder: Path, count: int, exposure: float = 2.0) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    out = []
    for i in range(count):
        plane = 0.02 + 0.001 * rng.standard_normal((H, W))
        p = folder / f"dark{i:02d}.fit"
        write_frame(p, plane, {"IMAGETYP": "Dark", "EXPTIME": exposure, "GAIN": 100, "CCD-TEMP": -10,
                               "XBINNING": 1, "YBINNING": 1, "DATE-OBS": "2026-10-14T22:00:00"})
        out.append(str(p))
    return out


def make_flats(folder: Path, count: int, *, filt: str = "L", exposure: float = 2.0) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(2)
    vignette = np.linspace(0.6, 1.0, W)[None, :] * np.ones((H, 1))
    out = []
    for i in range(count):
        plane = (0.45 + 0.02 * i) * vignette + 0.02 + 0.001 * rng.standard_normal((H, W))
        p = folder / f"flat{i:02d}.fit"
        write_frame(p, plane, {"IMAGETYP": "Flat", "FILTER": filt, "EXPTIME": exposure, "GAIN": 100,
                               "CCD-TEMP": -10, "XBINNING": 1, "YBINNING": 1,
                               "DATE-OBS": "2026-10-14T22:30:00"})
        out.append(str(p))
    return out


class FakeSiril:
    """A Siril stand-in: ``cmd`` runs the few commands the engine sends and records each one.

    The only compression state is ``compression.enabled``; ``set compression.enabled=<bool>`` changes it.
    ``read_fails`` makes the config read raise, ``set_fails`` makes the switch off raise.
    """

    def __init__(self, root: Path, *, compression: bool = False, fail: Optional[str] = None,
                 bad_stack: bool = False, read_fails: bool = False, set_fails: bool = False) -> None:
        self.root = str(root)
        self.cwd = str(root)
        self.cmds: list[str] = []
        self.compression = compression
        self.fail = fail
        self.bad_stack = bad_stack
        self.read_fails = read_fails
        self.set_fails = set_fails

    def get_siril_wd(self) -> str:
        return self.cwd

    def get_siril_config(self, section: str, key: str) -> Any:
        assert (section, key) == ("compression", "enabled"), (section, key)
        if self.read_fails:
            raise RuntimeError("no such config call")
        return self.compression

    def cmd(self, text: str) -> bool:
        self.cmds.append(text)
        if self.fail is not None and self.fail in text:
            raise RuntimeError("Siril said no to " + text)
        if text == "set compression.enabled=false" and self.set_fails:
            raise RuntimeError("Siril said no to " + text)
        if text.startswith("set compression.enabled="):
            self.compression = text.endswith("=true")
            return True
        parts = shlex.split(text)
        if parts[0] == "cd":
            if not os.path.isdir(parts[1]):
                raise RuntimeError("no such folder: " + parts[1])
            self.cwd = parts[1]
            return True
        if parts[0] == "calibrate":
            return self._calibrate(parts)
        if parts[0] == "stack":
            return self._stack(parts)
        raise RuntimeError("the stub does not know: " + text)

    def _calibrate(self, parts: list[str]) -> bool:
        assert parts[1] == "f_" and parts[2] == "-dark=dark.fit", parts
        dark = read_fits(os.path.join(self.cwd, "dark.fit"))[0][0]
        for name in sorted(os.listdir(self.cwd)):
            if re.fullmatch(r"f_\d{5}\.fit", name):
                planes, header = read_fits(os.path.join(self.cwd, name))
                write_fits(os.path.join(self.cwd, "pp_" + name), [planes[0] - dark], header=header, bitpix=-32)
        return True

    def _stack(self, parts: list[str]) -> bool:
        prefix = parts[1]
        out = next(p[len("-out="):] for p in parts if p.startswith("-out="))
        names = sorted(n for n in os.listdir(self.cwd) if re.fullmatch(re.escape(prefix) + r"\d{5}\.fit", n))
        if not names:
            raise RuntimeError("no frames with the prefix " + prefix)
        frames = [read_fits(os.path.join(self.cwd, n)) for n in names]
        stacked = np.mean([f[0][0] for f in frames], axis=0).astype(np.float32)
        if self.bad_stack:
            stacked = stacked[:, :-1]
        write_fits(os.path.join(self.cwd, out + ".fit"), [stacked], header=frames[0][1], bitpix=-32)
        return True


def flat_job(paths: list[str], *, exposures: Optional[list[float]] = None, filt: str = "L",
             binning: float = 1.0) -> dict[str, Any]:
    exposures = exposures or [2.0] * len(paths)
    flats = [{"path": p, "exposure": e, "gain": 100.0, "temp": -10.0, "filter": filt, "binning": binning}
             for p, e in zip(paths, exposures)]
    parts: list[dict[str, Any]] = []
    for f in flats:
        part = next((q for q in parts if q["exposure"] == f["exposure"]), None)
        if part is None:
            part = {"exposure": f["exposure"], "gain": 100.0, "flats": [], "dark": None}
            parts.append(part)
        part["flats"].append(f)
    return {"name": mf.file_name(filt, None), "filter": filt, "binning": binning, "flats": flats,
            "parts": parts}


def dark_group(paths: list[str], key: str = "dark-k") -> dict[str, Any]:
    return {"kind": "raw", "key": key, "paths": paths, "exposure": 2.0, "binning": 1.0, "gain": 100.0,
            "temp": -10.0, "temps": [-10.0]}


def integrate_flats(engine: mfs.SirilMasterFlatEngine, job: dict[str, Any], flat_paths: list[str],
                    dark_path: str, out: str) -> dict[str, Any]:
    job_dir = engine.temp_dir(job)
    cal = engine.calibrate(flat_paths, dark_path, job_dir)
    assert cal["ok"], cal["reason"]
    return engine.integrate(cal["paths"], mf.flat_integration_spec(len(cal["paths"])), out, job)


@pytest.fixture
def rig(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "siril-wd"
    root.mkdir()
    cache = tmp_path / "cache"
    dest = tmp_path / "masters"
    dest.mkdir()
    darks = make_darks(tmp_path / "raw" / "darks", 5)
    flats = make_flats(tmp_path / "raw" / "flats", 9)
    siril = FakeSiril(root)
    engine = mfs.SirilMasterFlatEngine(siril, read_header, str(cache))
    return SimpleNamespace(root=root, cache=cache, dest=dest, darks=darks, flats=flats, siril=siril,
                           engine=engine, tmp=tmp_path)


def _job_dir(rig: SimpleNamespace, job: dict[str, Any]) -> str:
    return str(rig.cache / "master-flat-work" / mf.safe_name(job["name"].replace(".fit", "")))


# ---------------------------------------------------------------------------
# Command lines, as measured on siril-cli 1.4.4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "count, stack_line",
    [
        (9, "stack pp_f_ rej w 4 3 -norm=mul -out=integrated"),
        (4, "stack pp_f_ rej p 0.2 0.1 -norm=mul -out=integrated"),
        (2, "stack pp_f_ rej n -norm=mul -out=integrated"),
    ],
)
def test_flat_commands_match_the_measured_lines(rig: SimpleNamespace, count: int, stack_line: str) -> None:
    job = flat_job(rig.flats[:count])
    out = str(rig.dest / ".partial_masterFlat_L.fit")
    res = integrate_flats(rig.engine, job, rig.flats[:count], rig.darks[0], out)
    assert res == {"ok": True, "reason": ""}
    job_dir = _job_dir(rig, job)
    root = str(rig.root)
    assert rig.siril.cmds == [
        f'cd "{job_dir}"',
        "calibrate f_ -dark=dark.fit",
        f'cd "{root}"',
        f'cd "{job_dir}/integrate"',
        stack_line,
        f'cd "{root}"',
    ]


@pytest.mark.parametrize(
    "count, stack_line",
    [
        (5, "stack d_ rej p 0.2 0.1 -nonorm -out=integrated"),
        (8, "stack d_ rej w 4 3 -nonorm -out=integrated"),
        (2, "stack d_ rej n -nonorm -out=integrated"),
    ],
)
def test_dark_commands_follow_the_rejection_rule(tmp_path: Path, count: int, stack_line: str) -> None:
    root = tmp_path / "siril-wd"
    root.mkdir()
    cache = tmp_path / "cache"
    darks = make_darks(tmp_path / "raw", count)
    siril = FakeSiril(root)
    engine = mfs.SirilMasterFlatEngine(siril, read_header, str(cache))
    res = engine.master_dark(dark_group(darks))
    assert res["ok"], res["reason"]
    dark_dir = str(cache / "master-flat-work" / mf.safe_name("masterDark_dark-k"))
    assert siril.cmds == [f'cd "{dark_dir}"', stack_line, f'cd "{root}"']


# ---------------------------------------------------------------------------
# Work folders, compression and the working directory
# ---------------------------------------------------------------------------


def test_compression_enabled_is_switched_off_for_the_step_and_back_on(tmp_path: Path) -> None:
    root = tmp_path / "siril-wd"
    root.mkdir()
    darks = make_darks(tmp_path / "raw", 2)
    siril = FakeSiril(root, compression=True)
    engine = mfs.SirilMasterFlatEngine(siril, read_header, str(tmp_path / "cache"))
    assert engine.master_dark(dark_group(darks))["ok"]
    switches = [c for c in siril.cmds if c.startswith("set compression")]
    assert switches == ["set compression.enabled=false", "set compression.enabled=true"]
    stack_at = next(i for i, c in enumerate(siril.cmds) if c.startswith("stack"))
    assert siril.cmds.index("set compression.enabled=false") < stack_at < siril.cmds.index(
        "set compression.enabled=true")
    assert siril.compression is True


def test_compression_disabled_is_left_disabled(tmp_path: Path) -> None:
    root = tmp_path / "siril-wd"
    root.mkdir()
    darks = make_darks(tmp_path / "raw", 2)
    siril = FakeSiril(root, compression=False)
    engine = mfs.SirilMasterFlatEngine(siril, read_header, str(tmp_path / "cache"))
    assert engine.master_dark(dark_group(darks))["ok"]
    assert not any(c.startswith("set compression") for c in siril.cmds)
    assert siril.compression is False


def test_unreadable_compression_state_is_left_alone_with_a_warning(tmp_path: Path) -> None:
    root = tmp_path / "siril-wd"
    root.mkdir()
    darks = make_darks(tmp_path / "raw", 2)
    siril = FakeSiril(root, compression=True, read_fails=True)
    log: list[str] = []
    engine = mfs.SirilMasterFlatEngine(siril, read_header, str(tmp_path / "cache"), log=log.append)
    assert engine.master_dark(dark_group(darks))["ok"]
    assert not any(c.startswith("set compression") for c in siril.cmds)
    assert any(line.startswith("master flat: warning: FITS compression state could not be read") for line in log)


def test_compression_is_switched_back_on_when_switching_off_raised(tmp_path: Path) -> None:
    root = tmp_path / "siril-wd"
    root.mkdir()
    darks = make_darks(tmp_path / "raw", 2)
    siril = FakeSiril(root, compression=True, set_fails=True)
    engine = mfs.SirilMasterFlatEngine(siril, read_header, str(tmp_path / "cache"))
    res = engine.master_dark(dark_group(darks))
    assert res["ok"] is False
    assert res["reason"] == "Siril said no to set compression.enabled=false"
    assert siril.compression is True
    assert siril.cwd == str(root)


def test_cd_and_compression_restored_when_stack_raises(tmp_path: Path) -> None:
    root = tmp_path / "siril-wd"
    root.mkdir()
    flats = make_flats(tmp_path / "raw", 9)
    darks = make_darks(tmp_path / "raw_d", 2)
    siril = FakeSiril(root, compression=True)
    engine = mfs.SirilMasterFlatEngine(siril, read_header, str(tmp_path / "cache"))
    job = flat_job(flats)
    dest = tmp_path / "masters"
    dest.mkdir()
    dark = engine.master_dark(dark_group(darks))
    assert dark["ok"]
    siril.fail = "stack"
    out = str(dest / ".partial_masterFlat_L.fit")
    res = integrate_flats(engine, job, flats, dark["path"], out)
    assert res["ok"] is False
    assert res["reason"].startswith("Siril said no to stack")
    assert siril.cwd == str(root)
    assert siril.compression is True
    assert siril.cmds[-2:] == ["set compression.enabled=true", f'cd "{root}"']
    assert not os.path.exists(out)


def test_cd_restored_when_calibrate_raises(rig: SimpleNamespace) -> None:
    rig.siril.fail = "calibrate"
    job = flat_job(rig.flats)
    cal = rig.engine.calibrate(rig.flats, rig.darks[0], rig.engine.temp_dir(job))
    assert cal["ok"] is False
    assert cal["reason"].startswith("Siril said no to calibrate")
    assert rig.siril.cwd == str(rig.root)


def test_a_command_siril_refuses_is_a_failed_step(rig: SimpleNamespace) -> None:
    rig.siril.fail = "calibrate"
    cal = rig.engine.calibrate(rig.flats, rig.darks[0], rig.engine.temp_dir(flat_job(rig.flats)))
    assert cal == {"ok": False, "reason": "Siril said no to calibrate f_ -dark=dark.fit"}


def test_inputs_are_linked_with_siril_names(rig: SimpleNamespace) -> None:
    job_dir = rig.engine.temp_dir(flat_job(rig.flats))
    rig.engine.calibrate(rig.flats, rig.darks[0], job_dir)
    assert sorted(n for n in os.listdir(job_dir) if n.startswith("f_")) == [
        f"f_{i:05d}.fit" for i in range(1, 10)]
    assert os.path.islink(os.path.join(job_dir, "f_00001.fit"))
    assert os.path.islink(os.path.join(job_dir, "dark.fit"))
    assert os.path.realpath(os.path.join(job_dir, "dark.fit")) == os.path.realpath(rig.darks[0])


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def test_flat_header_is_rewritten_for_one_exposure(rig: SimpleNamespace) -> None:
    out = str(rig.dest / ".partial_masterFlat_L.fit")
    assert integrate_flats(rig.engine, flat_job(rig.flats), rig.flats, rig.darks[0], out)["ok"]
    planes, header = read_fits(out)
    assert header["IMAGETYP"] == "Master Flat"
    assert header["FILTER"] == "L"
    assert header["EXPTIME"] == 2
    assert header["XBINNING"] == 1 and header["YBINNING"] == 1
    assert header["GAIN"] == 100 and header["CCD-TEMP"] == -10
    assert "DATE-OBS" not in header
    assert header["BITPIX"] == -32
    assert planes[0].shape == (H, W)


def test_flat_header_drops_exposure_for_two_exposures(rig: SimpleNamespace) -> None:
    flats = rig.flats[:4]
    exposures = [2.0, 2.0, 5.0, 5.0]
    out = str(rig.dest / ".partial_masterFlat_L.fit")
    job = flat_job(flats, exposures=exposures)
    assert integrate_flats(rig.engine, job, flats, rig.darks[0], out)["ok"]
    _planes, header = read_fits(out)
    assert header["IMAGETYP"] == "Master Flat"
    assert "EXPTIME" not in header
    assert "DATE-OBS" not in header


def test_dark_header_is_the_master_dark_header(rig: SimpleNamespace) -> None:
    res = rig.engine.master_dark(dark_group(rig.darks))
    assert res["ok"]
    _planes, header = read_fits(res["path"])
    assert header["IMAGETYP"] == "Master Dark"
    assert "EXPTIME" not in header and "GAIN" not in header and "CCD-TEMP" not in header
    assert "DATE-OBS" not in header


def test_apply_header_keeps_others_and_drops_none() -> None:
    kept = mfs.integration_apply_header({"OBJECT": "x", "FILTER": "old", "DATE-OBS": "d"},
                                        {"FILTER": None, "IMAGETYP": "Master Flat", "EXPTIME": 2.0})
    assert kept == {"OBJECT": "x", "IMAGETYP": "Master Flat", "EXPTIME": 2}


# ---------------------------------------------------------------------------
# Master darks: the cache and the partial
# ---------------------------------------------------------------------------


def test_cached_master_dark_is_reused(rig: SimpleNamespace) -> None:
    first = rig.engine.master_dark(dark_group(rig.darks))
    assert first["ok"] and first["reason"] == ""
    rig.siril.cmds.clear()
    again = rig.engine.master_dark(dark_group(rig.darks))
    assert again == {"ok": True, "path": first["path"], "reason": "cached"}
    assert rig.siril.cmds == []


def test_master_dark_missing_raw_dark_fails_without_siril(rig: SimpleNamespace) -> None:
    os.remove(rig.darks[1])
    res = rig.engine.master_dark(dark_group(rig.darks))
    assert res == {"ok": False, "reason": "a dark is no longer there: " + rig.darks[1]}
    assert rig.siril.cmds == []


def test_partial_is_never_left_behind_on_a_failed_stack(rig: SimpleNamespace) -> None:
    rig.siril.fail = "stack"
    res = rig.engine.master_dark(dark_group(rig.darks))
    assert res["ok"] is False
    assert not (rig.cache / "master-darks").exists() or os.listdir(rig.cache / "master-darks") == []


def test_bad_master_dark_geometry_is_not_stored(rig: SimpleNamespace) -> None:
    rig.siril.bad_stack = True
    res = rig.engine.master_dark(dark_group(rig.darks))
    assert res == {"ok": False, "reason": "the master dark does not match its darks"}
    assert not (rig.cache / "master-darks").exists() or os.listdir(rig.cache / "master-darks") == []


def test_partial_flat_is_not_left_behind_on_failure(rig: SimpleNamespace) -> None:
    rig.siril.fail = "stack"
    out = rig.dest / ".partial_masterFlat_L.fit"
    res = integrate_flats(rig.engine, flat_job(rig.flats), rig.flats, rig.darks[0], str(out))
    assert res["ok"] is False
    assert not out.exists()


# ---------------------------------------------------------------------------
# The folder, publish, resolve and the partials
# ---------------------------------------------------------------------------


def test_temp_dir_is_fresh_and_named_by_the_job(rig: SimpleNamespace) -> None:
    job = flat_job(rig.flats)
    d = rig.engine.temp_dir(job)
    assert d == _job_dir(rig, job)
    (Path(d) / "stale.fit").write_bytes(b"x")
    assert rig.engine.temp_dir(job) == d
    assert os.path.isdir(d) and not os.path.exists(os.path.join(d, "stale.fit"))


def test_remove_partials_leaves_links_and_masters(rig: SimpleNamespace) -> None:
    folder = rig.dest
    (folder / ".partial_masterFlat_a.fit").write_bytes(b"x")
    (folder / "masterFlat_a.fit").write_bytes(b"x")
    os.symlink(str(rig.dest / "masterFlat_a.fit"), str(folder / ".partial_masterFlat_b.fit"))
    rig.engine.remove_partials(str(folder))
    names = sorted(os.listdir(folder))
    assert ".partial_masterFlat_a.fit" not in names
    assert ".partial_masterFlat_b.fit" in names
    assert "masterFlat_a.fit" in names


def test_resolve_marks_a_symlink_and_resolves_the_rest(rig: SimpleNamespace) -> None:
    real = rig.dest / "real.fit"
    real.write_bytes(b"x")
    link = rig.dest / "link.fit"
    os.symlink(str(real), str(link))
    assert rig.engine.resolve(str(link)) == "symlink:" + str(link)
    assert rig.engine.resolve(str(real)) == os.path.realpath(str(rig.dest)) + "/real.fit"
    assert rig.engine.resolve(str(rig.dest)) == os.path.realpath(str(rig.dest))


def test_publish_moves_and_remove_reports(rig: SimpleNamespace) -> None:
    partial = rig.dest / ".partial_masterFlat_L.fit"
    partial.write_bytes(b"x")
    final = rig.dest / "masterFlat_L.fit"
    assert rig.engine.publish(str(partial), str(final)) is True
    assert final.exists() and not partial.exists()
    assert rig.engine.publish(str(partial), str(final)) is False
    assert rig.engine.remove(str(final)) is True
    assert rig.engine.remove(str(final)) is True
    assert rig.engine.exists(str(final)) is False


def test_publish_never_replaces_an_existing_master(rig: SimpleNamespace) -> None:
    partial = rig.dest / ".partial_masterFlat_L.fit"
    partial.write_bytes(b"new")
    final = rig.dest / "masterFlat_L.fit"
    final.write_bytes(b"kept")
    assert rig.engine.publish(str(partial), str(final)) is False
    assert final.read_bytes() == b"kept"


def test_publish_loses_a_race_to_another_writer(rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    partial = rig.dest / ".partial_masterFlat_L.fit"
    partial.write_bytes(b"new")
    final = rig.dest / "masterFlat_L.fit"
    real_link = os.link

    def racing_link(src: str, dst: str) -> None:
        Path(dst).write_bytes(b"other writer")  # the other writer lands between the check and the link
        real_link(src, dst)

    monkeypatch.setattr(mfs.os, "link", racing_link)
    assert rig.engine.publish(str(partial), str(final)) is False
    assert final.read_bytes() == b"other writer"


def test_publish_without_hard_links_copies_exclusively(rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    partial = rig.dest / ".partial_masterFlat_L.fit"
    partial.write_bytes(b"master")
    final = rig.dest / "masterFlat_L.fit"

    def no_links(src: str, dst: str) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(mfs.os, "link", no_links)
    assert rig.engine.publish(str(partial), str(final)) is True
    assert final.read_bytes() == b"master" and not partial.exists()


def test_publish_copy_fallback_never_replaces_either(rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    partial = rig.dest / ".partial_masterFlat_L.fit"
    partial.write_bytes(b"new")
    final = rig.dest / "masterFlat_L.fit"
    final.write_bytes(b"kept")

    def no_links(src: str, dst: str) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(mfs.os, "link", no_links)
    assert rig.engine.publish(str(partial), str(final)) is False
    assert final.read_bytes() == b"kept"


def test_remove_dir_removes_the_folder_and_its_steps(rig: SimpleNamespace) -> None:
    job = flat_job(rig.flats)
    integrate_flats(rig.engine, job, rig.flats, rig.darks[0], str(rig.dest / ".partial_masterFlat_L.fit"))
    d = _job_dir(rig, job)
    assert os.path.isdir(os.path.join(d, "integrate"))
    assert rig.engine.remove_dir(d) is True
    assert not os.path.exists(d)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _got(width: int = W, height: int = H, filt: Optional[str] = "L", imagetyp: Optional[str] = "Master Flat",
         mean: Any = 0.5) -> SimpleNamespace:
    keywords = {"FILTER": filt, "IMAGETYP": imagetyp}
    return SimpleNamespace(width=width, height=height, keyword=lambda n: keywords.get(n), mean=mean)


WANT = SimpleNamespace(width=W, height=H)


@pytest.mark.parametrize(
    "got, filt, expected",
    [
        (_got(), "L", None),
        (_got(width=W + 1), "L", "geometry changed"),
        (_got(width=None), "L", "the master could not be read"),
        (_got(filt="Ha"), "L", "FILTER is Ha, expected L"),
        (_got(filt=None), "L", "FILTER is missing, expected L"),
        (_got(filt=None), None, None),
        (_got(imagetyp="Master Dark"), "L", "IMAGETYP is Master Dark, expected a master flat"),
        (_got(imagetyp="Light"), "L", "IMAGETYP is Light, expected a master flat"),
        (_got(imagetyp=None), "L", "IMAGETYP is missing, expected a master flat"),
        (_got(mean=0.0), "L", "the master is empty or not finite"),
        (_got(mean=float("nan")), "L", "the master is empty or not finite"),
        (_got(mean=None), "L", "the master is empty or not finite"),
    ],
)
def test_verify_master_checks(got: SimpleNamespace, filt: Optional[str], expected: Optional[str]) -> None:
    assert mfr.verify_master(got, WANT, filt) == expected


def test_engine_verify_is_the_shared_check_on_the_written_master(rig: SimpleNamespace) -> None:
    out = rig.dest / ".partial_masterFlat_L.fit"
    job = flat_job(rig.flats)
    assert integrate_flats(rig.engine, job, rig.flats, rig.darks[0], str(out))["ok"]
    assert rig.engine.verify(str(out), job) is None
    wrong = dict(job, filter="Ha")
    assert rig.engine.verify(str(out), wrong) == "FILTER is L, expected Ha"


def test_mean_of_is_the_mean_of_every_pixel(tmp_path: Path) -> None:
    p = tmp_path / "m.fit"
    write_frame(p, np.full((H, W), 0.25), {"IMAGETYP": "Master Flat"})
    assert mfs.mean_of(str(p)) == pytest.approx(0.25)
    assert mfs.mean_of(str(tmp_path / "missing.fit")) is None


def test_rejection_and_setup_params_text() -> None:
    assert mfs.integration_apply_rejection({"method": "none"}) == ["rej", "n"]
    assert mfs.integration_setup_params("d_", mf.dark_integration_spec(9), "integrated") == (
        "stack d_ rej w 4 3 -nonorm -out=integrated")


# ---------------------------------------------------------------------------
# Real siril-cli
# ---------------------------------------------------------------------------

_SIRIL_SCRIPT = r'''
import json, os, sys
import numpy as np
import sirilpy as s
from types import SimpleNamespace
from loomsiril import master_flat as mf
from loomsiril import master_flat_siril as mfs
from loomsiril.io_fits import read_fits

base = sys.argv[1]
siril = s.SirilInterface()
siril.connect()


class _Header:
    def __init__(self, info, header):
        self.info = info
        self._header = header

    def keyword(self, name):
        v = self._header.get(name)
        return None if v is None else str(v)


def read_header(path):
    if not os.path.exists(path):
        return None
    planes, header = read_fits(path)
    return _Header(SimpleNamespace(width=int(planes[0].shape[1]), height=int(planes[0].shape[0])), header)


engine = mfs.SirilMasterFlatEngine(siril, read_header, os.path.join(base, "cache"))
darks = sorted(os.path.join(base, "raw", n) for n in os.listdir(os.path.join(base, "raw")) if n.startswith("dark"))
flats = sorted(os.path.join(base, "raw", n) for n in os.listdir(os.path.join(base, "raw")) if n.startswith("flat"))
group = {"kind": "raw", "key": "dark-k", "paths": darks, "exposure": 2.0, "binning": 1.0, "gain": 100.0,
         "temp": -10.0, "temps": [-10.0]}
dark = engine.master_dark(group)
print("dark=" + json.dumps(dark))
job = {"name": mf.file_name("L", None), "filter": "L", "binning": 1.0, "parts": [],
       "flats": [{"path": p, "exposure": 2.0, "gain": 100.0, "temp": -10.0, "filter": "L", "binning": 1.0}
                 for p in flats]}
job["parts"] = [{"exposure": 2.0, "gain": 100.0, "flats": job["flats"], "dark": None}]
jd = engine.temp_dir(job)
cal = engine.calibrate(flats, dark["path"], jd)
print("calibrated=" + json.dumps(cal["ok"]))
out = os.path.join(base, "masters", ".partial_masterFlat_L.fit")
os.makedirs(os.path.dirname(out), exist_ok=True)
res = engine.integrate(cal["paths"], mf.flat_integration_spec(len(cal["paths"])), out, job)
print("integrate=" + json.dumps(res))
verdict = engine.verify(out, job)
print("verify=" + json.dumps(verdict))
if verdict is None:
    final = os.path.join(base, "masters", "masterFlat_L.fit")
    print("published=" + json.dumps(engine.publish(out, final)))
'''


def _vignette() -> np.ndarray:
    x = (np.arange(W)[None, :] - W / 2) / W
    y = (np.arange(H)[:, None] - H / 2) / H
    return 1.0 - 0.3 * (x ** 2 + y ** 2)


def _dark_signal(rng: np.random.Generator) -> np.ndarray:
    hot = rng.random((H, W)) < 0.05
    return 0.02 + 0.0005 * np.arange(W)[None, :] / W + 0.04 * hot


def _synthetic_real_frames(base: Path) -> list[np.ndarray]:
    """Five darks and nine flats; the flats carry the same dark signal, and the first has a saturated blip.

    Returns the raw flat planes, so a test can stack them without the dark for comparison.
    """
    rng = np.random.default_rng(7)
    signal = _dark_signal(rng)
    raw = base / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        plane = signal + 0.001 * rng.standard_normal((H, W))
        write_frame(raw / f"dark{i:02d}.fit", plane, {"IMAGETYP": "Dark", "EXPTIME": 2.0, "GAIN": 100,
                                                      "CCD-TEMP": -10, "XBINNING": 1, "YBINNING": 1})
    flats = []
    for i in range(9):
        plane = (0.45 + 0.03 * i) * _vignette() + signal + 0.001 * rng.standard_normal((H, W))
        if i == 0:
            plane[4:8, 4:8] = 1.0
        flats.append(plane)
        write_frame(raw / f"flat{i:02d}.fit", plane, {"IMAGETYP": "Flat", "FILTER": "L", "EXPTIME": 2.0,
                                                      "GAIN": 100, "CCD-TEMP": -10, "XBINNING": 1,
                                                      "YBINNING": 1})
    return flats


def _flatness(stack: np.ndarray) -> float:
    """Coefficient of variation of the master over the true vignetting, the way the flatness check reads it."""
    ratio = stack / _vignette()
    return float(np.std(ratio) / np.mean(ratio))


@pytest.mark.siril
def test_master_flat_made_in_real_siril(tmp_path: Path) -> None:
    raw_flats = _synthetic_real_frames(tmp_path)
    run = run_pyscript(_SIRIL_SCRIPT, tmp_path, args=[str(tmp_path)]).check()
    assert run.value("dark")["ok"] is True
    assert run.value("calibrated") is True
    assert run.value("integrate") == {"ok": True, "reason": ""}
    assert run.value("verify") is None
    master = tmp_path / "masters" / "masterFlat_L.fit"
    assert master.exists()
    planes, header = read_fits(master)
    assert header["BITPIX"] == -32
    assert header["IMAGETYP"] == "Master Flat"
    assert header["FILTER"] == "L"
    assert _flatness(planes[0].astype(np.float64)) < 0.01
    # The same flats stacked without the dark: the dark signal and the hot pixels stay in the master.
    # Both sides drop the lowest and highest value of each pixel, so the blip is rejected in each.
    normalised = np.sort(np.array([plane / float(np.mean(plane)) for plane in raw_flats]), axis=0)
    assert _flatness(normalised[1:-1].mean(axis=0)) > 0.015
