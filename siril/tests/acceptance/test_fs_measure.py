"""Measurement: Siril's registration figures mapped to the Frame Selector's metrics, matched to inputs by path.

The pure tests drive ``measure_batch`` against a stand-in for the Siril interface that writes the files Siril 1.4
writes (a ``.seq`` file with R and M lines and one star list per image); expected figures are worked out by hand
from the numbers put in. The last test runs real ``siril-cli`` (marked ``siril``; it skips where there is none).
Skips until ``loomsiril.frames_measure`` is on the branch.
"""

from __future__ import annotations

import json
import math
import os
import statistics
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from fs_helpers import load_siril_run, seq_text, star_field_frame, star_list_text

fm = pytest.importorskip("loomsiril.frames_measure")
frames = pytest.importorskip("loomsiril.frames")


# ---- a stand-in for the Siril interface --------------------------------------------------------------------


class FakeSiril:
    """Follows ``cd``; on ``register`` it reads the links in the current directory and writes the seq file and the
    star lists Siril would, using a per-source-file plan keyed by the source file's name."""

    def __init__(self, start: Path, plan: dict[str, dict[str, Any]], fail_register: bool = False) -> None:
        self.cwd = str(start)
        self.plan = plan
        self.fail_register = fail_register
        self.calls: list[str] = []
        self.fallback_asked: list[str] = []
        self.link_targets_at_register: list[str] = []

    def get_siril_wd(self) -> str:
        return self.cwd

    def cmd(self, text: str) -> None:
        self.calls.append(text)
        word = text.split()[0]
        if word == "cd":
            self.cwd = text[3:].strip().strip('"')
        elif word == "register":
            if self.fail_register:
                raise RuntimeError("register failed")
            links = sorted(n for n in os.listdir(self.cwd) if n.startswith("fs_") and n.endswith(".fit"))
            specs = []
            for n in links:
                target = os.readlink(os.path.join(self.cwd, n))
                self.link_targets_at_register.append(target)
                specs.append(self.plan[os.path.basename(target)])
            Path(self.cwd, "fs_.seq").write_text(
                seq_text([s["r"] for s in specs], [s["m"] for s in specs]), encoding="utf-8")
            os.makedirs(os.path.join(self.cwd, "cache"), exist_ok=True)
            for i, s in enumerate(specs, start=1):
                Path(self.cwd, "cache", f"fs_{i:05d}.lst").write_text(star_list_text(s["stars"]), encoding="utf-8")

    def analyse_image_from_file(self, path: str) -> Any:
        self.fallback_asked.append(os.path.basename(path))
        fwhm, roundness, nbstars = self.plan[os.path.basename(path)]["fallback"]
        return SimpleNamespace(fwhm=fwhm, roundness=roundness, nbstars=nbstars, wfwhm=0.0, bgnoise=0.001)


PLAN = {
    # r: fwhm wfwhm roundness quality background nbstars; m: median bgnoise; stars: (A, mag)
    "a.fit": dict(r=(3.07, 3.07, 0.80, 0, 5577.98, 117), m=(5592.0, 287.818),
                  stars=[(40000.0, -14.0), (45000.0, -14.2), (50000.0, -14.4)], fallback=(9.9, 0.1, 999)),
    "b.fit": dict(r=(5.02, 4.76, 0.99, 0, 5582.39, 120), m=(5633.0, 383.124),
                  stars=[(20000.0, -13.0), (24000.0, -13.2), (30000.0, -13.4), (31000.0, -13.0)],
                  fallback=(9.9, 0.1, 999)),
    "c.fit": dict(r=(3.03, 4.9, 0.60, 0, 8202.31, 80), m=(8208.0, 282.309),
                  stars=[(12000.0, -13.5)], fallback=(9.9, 0.1, 999)),
    # registration failed (all-zero R line): the figures come from the single-image analysis
    "d.fit": dict(r=None, m=(5571.0, 262.578), stars=[(30000.0, -14.0), (34000.0, -14.0)],
                  fallback=(2.89, 0.989, 10)),
    # registration failed and the analysis finds nothing either
    "e.fit": dict(r=None, m=(5571.0, 262.578), stars=[], fallback=(0.0, 0.0, 0)),
}


def _sources(folder: Path, names: list[str]) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for n in names:
        (folder / n).write_bytes(b"x")
        out.append(str(folder / n))
    return out


def _run(tmp_path: Path, names: list[str], **kw: Any):
    paths = _sources(tmp_path / "src", names)
    start = tmp_path / "session"
    start.mkdir(exist_ok=True)
    s = FakeSiril(start, PLAN, kw.pop("fail_register", False))
    work = tmp_path / "work"
    res, problem = fm.measure_batch(s, paths, str(work), **kw)
    return s, paths, res, problem, work


# ---- the mapping, by hand ---------------------------------------------------------------------------------------


def test_eccentricity_from_roundness(tmp_path: Path) -> None:
    """Eccentricity is derived from Siril's roundness (minor over major FWHM): sqrt(1 - roundness squared)."""
    s, paths, res, problem, _ = _run(tmp_path, ["a.fit", "b.fit"])
    assert problem is None
    assert res[paths[0]]["eccentricity"] == pytest.approx(0.6, rel=1e-9)            # sqrt(1 - 0.64)
    assert res[paths[1]]["eccentricity"] == pytest.approx(math.sqrt(1 - 0.99 ** 2), rel=1e-9)


def test_fwhm_stars_background_and_noise_come_from_siril(tmp_path: Path) -> None:
    """FWHM and the star count are Siril's registration figures; background is the image median and noise Siril's
    background noise."""
    _, paths, res, _, _ = _run(tmp_path, ["a.fit"])
    m = res[paths[0]]
    assert m["fwhm"] == pytest.approx(3.07)
    assert m["stars"] == 117 and isinstance(m["stars"], int)
    assert m["background"] == pytest.approx(5592.0 / 65535)        # divided by the M line's normValue
    assert m["noise"] == pytest.approx(287.818 / 65535)


def test_psf_snr_and_flux_from_the_star_list(tmp_path: Path) -> None:
    """PSF SNR is the median star amplitude over the background noise; star flux is the median of 10^(-0.4 mag)."""
    _, paths, res, _, _ = _run(tmp_path, ["a.fit", "b.fit"])
    a = res[paths[0]]
    assert a["psfSNR"] == pytest.approx(45000.0 / 287.818, rel=1e-9)
    assert a["psfFlux"] == pytest.approx(10 ** (0.4 * 14.2) / 65535, rel=1e-9)                # middle of 14.0, 14.2, 14.4
    b = res[paths[1]]
    fluxes = [10 ** (0.4 * x) for x in (13.0, 13.2, 13.4, 13.0)]
    assert b["psfSNR"] == pytest.approx(statistics.median([20000, 24000, 30000, 31000]) / 383.124, rel=1e-9)
    assert b["psfFlux"] == pytest.approx(statistics.median(fluxes) / 65535, rel=1e-9)


def test_snr_weight_is_dropped(tmp_path: Path) -> None:
    """Siril has no SNR estimate: that figure is dropped (null)."""
    _, paths, res, _, _ = _run(tmp_path, ["a.fit"])
    assert res[paths[0]]["snrWeight"] is None


def test_a_normal_frame_is_a_complete_valid_measurement(tmp_path: Path) -> None:
    """Every figure the selection ranks on is present and positive."""
    _, paths, res, _, _ = _run(tmp_path, ["a.fit"])
    assert set(res[paths[0]]) == set(frames.STORED_KEYS)
    assert frames.frame_valid(res[paths[0]])


def test_altitude_is_whatever_the_header_reader_gave(tmp_path: Path) -> None:
    """The altitude comes from the header (Siril does not give one a script can read)."""
    _, paths, res, _, _ = _run(tmp_path, ["a.fit", "b.fit"], altitude_of=lambda p: 41.5 if p.endswith("a.fit") else None)
    assert res[paths[0]]["altitude"] == 41.5
    assert res[paths[1]]["altitude"] is None


def test_an_all_zero_registration_row_uses_the_fallback(tmp_path: Path) -> None:
    """A frame whose registration failed (an all-zero row) is measured again on its own, and still comes out."""
    s, paths, res, _, _ = _run(tmp_path, ["a.fit", "d.fit"])
    d = res[paths[1]]
    assert d is not None
    assert d["fwhm"] == pytest.approx(2.89)
    assert d["eccentricity"] == pytest.approx(math.sqrt(1 - 0.989 ** 2), rel=1e-9)
    assert d["stars"] == 10
    assert s.fallback_asked == ["d.fit"]                       # only the frame that needed it


def test_a_frame_with_no_result_is_unmeasurable(tmp_path: Path) -> None:
    """A frame with no result is "unmeasurable": registration failed and the analysis found nothing."""
    _, paths, res, problem, _ = _run(tmp_path, ["a.fit", "e.fit"])
    assert problem is None
    assert res[paths[1]] is None
    assert not frames.frame_valid(res[paths[1]])


# ---- matched by path ----------------------------------------------------------------------------------------------


def test_results_are_keyed_by_path_whatever_the_input_order(tmp_path: Path) -> None:
    """Results are matched to inputs by path, never by position: frames handed over in a shuffled, non-sorted order
    each get their own numbers."""
    s, paths, res, _, _ = _run(tmp_path, ["c.fit", "a.fit", "e.fit", "d.fit", "b.fit"])
    by_name = {os.path.basename(p): res[p] for p in paths}
    assert set(res) == set(paths)
    assert by_name["a.fit"]["fwhm"] == pytest.approx(3.07)
    assert by_name["b.fit"]["fwhm"] == pytest.approx(5.02)
    assert by_name["c.fit"]["fwhm"] == pytest.approx(3.03)
    assert by_name["c.fit"]["stars"] == 80
    assert by_name["d.fit"]["stars"] == 10
    assert by_name["e.fit"] is None
    assert [os.path.basename(t) for t in s.link_targets_at_register] == ["c.fit", "a.fit", "e.fit", "d.fit", "b.fit"]


def test_a_duplicate_path_aborts_the_channel(tmp_path: Path) -> None:
    """A duplicate or unexpected path aborts that channel: nothing is measured, nothing is run in Siril."""
    paths = _sources(tmp_path / "src", ["a.fit", "b.fit"])
    s = FakeSiril(tmp_path, PLAN)
    res, problem = fm.measure_batch(s, [paths[0], paths[1], paths[0]], str(tmp_path / "work"))
    assert res == {}
    assert problem and paths[0] in problem
    assert s.calls == []


def test_siril_is_left_in_the_directory_it_was_in_and_nothing_is_left_behind(tmp_path: Path) -> None:
    """The scan writes nothing but the cache: Siril's working directory is restored, also when registration fails,
    and the temporary sequence is removed."""
    for fail in (False, True):
        sub = tmp_path / ("fail" if fail else "ok")
        s, paths, _, _, work = _run(sub, ["a.fit"], fail_register=fail)
        assert s.cwd == str(sub / "session")
        assert not work.exists()
        assert [os.path.basename(p) for p in os.listdir(sub / "src")] == ["a.fit"]


# ---- real Siril ------------------------------------------------------------------------------------------------------

SCRIPT = '''
import json, os, random, sys
import sirilpy
from loomsiril.frames_measure import measure_batch
siril = sirilpy.SirilInterface()
siril.connect()
frames_dir, work = sys.argv[1], sys.argv[2]
paths = sorted(os.path.join(frames_dir, n) for n in os.listdir(frames_dir) if n.endswith(".fit"))
random.Random(3).shuffle(paths)
for k in range(0, len(paths), 6):
    res, problem = measure_batch(siril, paths[k:k + 6], work)
    print("problem=" + json.dumps(problem), flush=True)
    for p, m in res.items():
        print("m." + os.path.basename(p)[:-4] + "=" + json.dumps(m), flush=True)
'''


@pytest.mark.siril
def test_real_siril_measures_known_defects(tmp_path: Path) -> None:
    """The measurements come from Siril itself: on synthetic frames with known defects (shuffled, in two batches) a
    blurred frame has the largest FWHM, an elongated one the largest eccentricity, a cloudy one lower flux and higher
    background, and a frame with 12 stars the smallest star count, each matched to its own file."""
    folder = tmp_path / "frames"
    folder.mkdir()
    for i in range(1, 9):
        star_field_frame(folder / f"f{i:02d}.fit", fwhm=3.0 + 0.05 * (i % 3), seed=100 + i)
    star_field_frame(folder / "f09.fit", fwhm=5.5, seed=109)                  # blurred
    star_field_frame(folder / "f10.fit", fwhm=3.0, elong=1.5, seed=110)       # elongated
    star_field_frame(folder / "f11.fit", scale=0.4, bg=0.12, seed=111)        # cloud
    star_field_frame(folder / "f12.fit", nstars=12, seed=112)                 # dropped
    before = sorted(os.listdir(folder))
    run = load_siril_run().run_pyscript(SCRIPT, tmp_path / "run", [str(folder), str(tmp_path / "work")]).check()
    got = {ln[2:].split("=", 1)[0]: json.loads(ln.split("=", 1)[1]) for ln in run.lines("m.")}
    assert sorted(got) == [f"f{i:02d}" for i in range(1, 13)], run.tail()
    assert [json.loads(ln.split("=", 1)[1]) for ln in run.lines("problem=")] == [None, None]
    assert all(got[n] is not None for n in got)
    normal = [got[f"f{i:02d}"] for i in range(1, 9)]
    assert all(frames.frame_valid(m) for m in normal)
    med = lambda key, rows=normal: statistics.median(m[key] for m in rows)       # noqa: E731
    assert got["f09"]["fwhm"] == max(m["fwhm"] for m in got.values())
    assert got["f09"]["fwhm"] > 1.4 * med("fwhm")
    assert got["f10"]["eccentricity"] == max(m["eccentricity"] for m in got.values())
    assert got["f10"]["eccentricity"] > 0.5
    assert got["f11"]["psfFlux"] < 0.75 * med("psfFlux")
    assert got["f11"]["background"] > 1.05 * med("background")
    assert got["f12"]["stars"] == min(m["stars"] for m in got.values())
    assert got["f12"]["stars"] < 20
    assert sorted(os.listdir(folder)) == before                    # the frames folder is untouched
