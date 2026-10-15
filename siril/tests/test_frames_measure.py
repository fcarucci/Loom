"""Tests for loomsiril.frames_measure: Siril's sequence and star-list output read back, and the batch route.

The fixtures under tests/fixtures/frames_measure are Siril 1.4.4 output text (a registered mono sequence of 12
images, an RGB sequence of 4, and two star lists). The batch tests run against a stub that writes the same files
where a real register would, so they need no Siril. One test runs real siril-cli (marked siril, skipped locally).
"""

from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from loomsiril import frames
from loomsiril import frames_measure as fm
from loomsiril.io_fits import write_fits

from loom.siril_run import run_pyscript

FIXTURES = Path(__file__).parent / "fixtures" / "frames_measure"
MONO_A_MEDIAN = 5592.0
MONO_A_BGNOISE = 287.818
STAR_A_IMG1 = [45585.530159, 44581.162222, 44472.237820]
STAR_MAG_IMG1 = [-14.19, -14.17, -14.17]


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _mono() -> dict:
    return fm.parse_seq(_text("mono.seq"))


def _rgb() -> dict:
    return fm.parse_seq(_text("rgb.seq"))


# ---- parse_seq -------------------------------------------------------------------------------------------------

def test_parse_seq_mono_counts_and_indices() -> None:
    seq = _mono()
    assert seq["count"] == 12 and seq["selected"] == 11 and seq["layers"] == 1
    assert sorted(seq["included"]) == list(range(1, 13))
    assert sum(seq["included"].values()) == seq["selected"]
    assert len(seq["R"][0]) == 12
    assert len(seq["M"]) == 12


def test_parse_seq_mono_first_registration_row() -> None:
    row = _mono()["R"][0][0]
    assert row["fwhm"] == pytest.approx(3.07018)
    assert row["wfwhm"] == pytest.approx(3.07018)
    assert row["roundness"] == pytest.approx(0.986754)
    assert row["quality"] == 0
    assert row["background"] == pytest.approx(5577.98)
    assert row["nbstars"] == 117


def test_parse_seq_all_zero_row_of_image_12() -> None:
    row = _mono()["R"][0][11]
    assert row["fwhm"] == 0 and row["roundness"] == 0 and row["nbstars"] == 0


def test_parse_seq_mono_stats_of_image_index_0() -> None:
    m = _mono()["M"][(0, 0)]
    assert m["median"] == pytest.approx(MONO_A_MEDIAN)
    assert m["bgnoise"] == pytest.approx(MONO_A_BGNOISE)
    assert m["total"] == 120000 and m["normValue"] == 65535


def test_parse_seq_missing_value_is_none() -> None:
    m = _mono()["M"][(0, 0)]
    assert m["avgDev"] is None and m["sqrtbwmv"] is None and m["scale"] is None


def test_parse_seq_rgb_layer_1() -> None:
    seq = _rgb()
    assert seq["layers"] == 3
    assert sorted(seq["included"]) == [1, 2, 3, 4]
    assert sorted(seq["R"]) == [1]
    assert len(seq["R"][1]) == 4
    assert seq["R"][1][0]["fwhm"] == pytest.approx(3.07015)
    assert seq["M"][(1, 0)]["median"] == pytest.approx(5033)
    assert seq["M"][(1, 0)]["bgnoise"] == pytest.approx(259.056)


def test_parse_seq_ignores_comments_and_blank_lines() -> None:
    seq = fm.parse_seq("#Siril sequence file.\n\nS 'fs_' 1 1 1 5 0 6 0 0 0\nL 1\nI 1 1\nR0 1 1 0.5 0 2 3 H 1 0 0 0 1 0 0 0 1\n")
    assert seq["count"] == 1 and seq["R"][0][0]["nbstars"] == 3


def test_parse_seq_reads_variable_size_sizes() -> None:
    seq = fm.parse_seq("S 'fs_' 1 2 2 0 0 6 1 0 0\nL 1\nI 1 1 400,300\nI 2 1 200,100\n")
    assert seq["sizes"] == {1: (400, 300), 2: (200, 100)}


def test_parse_seq_bad_s_line_raises() -> None:
    with pytest.raises(ValueError):
        fm.parse_seq("S 'fs_' 1 2\n")


# ---- parse_star_list -------------------------------------------------------------------------------------------

def test_parse_star_list_rows_and_first_star() -> None:
    rows = fm.parse_star_list(_text("fs_00001.lst"))
    assert len(rows) == 3
    assert rows[0]["A"] == pytest.approx(45585.530159)
    assert rows[0]["mag"] == pytest.approx(-14.19)
    assert rows[0]["Profile"] == "Gaussian"


def test_parse_star_list_columns_by_name_not_position() -> None:
    lines = _text("fs_00001.lst").splitlines()
    header = lines[2].lstrip("# ").split("\t")
    order = [4, 0, 12, 1, 5, 2, 15, 3, 6, 7, 8, 9, 10, 11, 13, 14, 16, 17]
    assert sorted(order) == list(range(18))
    shuffled_header = "\t".join(header[i] for i in order)
    shuffled_rows = []
    for row in lines[3:]:
        cells = row.split("\t")
        shuffled_rows.append("\t".join(cells[i] for i in order))
    text = "\n".join([lines[0], lines[1], "# " + shuffled_header] + shuffled_rows) + "\n"
    rows = fm.parse_star_list(text)
    assert [r["A"] for r in rows] == pytest.approx(STAR_A_IMG1)
    assert [r["mag"] for r in rows] == pytest.approx(STAR_MAG_IMG1)


def test_parse_star_list_count_comment_is_not_used() -> None:
    text = _text("fs_00012.lst")
    assert "# 12 stars found" in text
    assert len(fm.parse_star_list(text)) == 3


def test_parse_star_list_rows_before_header_raise() -> None:
    with pytest.raises(ValueError):
        fm.parse_star_list("1\t0\t5\t100\t0\t1\t2\n")


# ---- small functions -------------------------------------------------------------------------------------------

def test_link_name() -> None:
    assert fm.link_name(1) == "fs_00001.fit"
    assert fm.link_name(12) == "fs_00012.fit"


def test_eccentricity_values() -> None:
    assert fm.eccentricity(0.555213) == pytest.approx(math.sqrt(1 - 0.555213 ** 2))
    assert fm.eccentricity(0.555213) == pytest.approx(0.8317, abs=1e-4)
    assert fm.eccentricity(1.0) == 0
    assert fm.eccentricity(1.5) == 0


def test_registration_layer_mono_and_rgb() -> None:
    assert fm.registration_layer(_mono()) == 0
    assert fm.registration_layer(_rgb()) == 1
    assert fm.registration_layer({"R": {}}) is None


def test_batch_problem_count_mismatch() -> None:
    assert "12 images" in fm.batch_problem(_mono(), 8)


def test_batch_problem_missing_index() -> None:
    seq = _mono()
    del seq["included"][5]
    assert "lacks image index 5" in fm.batch_problem(seq, 12)


def test_batch_problem_extra_index() -> None:
    seq = _mono()
    seq["included"][13] = 1
    assert "13" in fm.batch_problem(seq, 12)


def test_batch_problem_ok() -> None:
    assert fm.batch_problem(_mono(), 12) is None


# ---- metrics_from_siril ----------------------------------------------------------------------------------------

def _stars() -> list[dict]:
    return fm.parse_star_list(_text("fs_00001.lst"))


def test_metrics_normal_frame_keys_and_values() -> None:
    seq = _mono()
    r, m = seq["R"][0][0], seq["M"][(0, 0)]
    out = fm.metrics_from_siril(r, m, _stars(), None, 61.5)
    assert set(out) == set(frames.STORED_KEYS)
    assert out["fwhm"] == pytest.approx(3.07018)
    assert out["eccentricity"] == pytest.approx(math.sqrt(1 - 0.986754 ** 2))
    assert out["stars"] == 117
    assert out["background"] == pytest.approx(MONO_A_MEDIAN)
    assert out["noise"] == pytest.approx(MONO_A_BGNOISE)
    assert out["psfSNR"] == pytest.approx(STAR_A_IMG1[1] / MONO_A_BGNOISE)
    flux = sorted(10 ** (-0.4 * m_) for m_ in STAR_MAG_IMG1)[1]
    assert out["psfFlux"] == pytest.approx(flux)
    assert out["snrWeight"] is None
    assert out["altitude"] == 61.5
    assert frames.frame_valid(out)


def test_metrics_all_zero_r_uses_fallback() -> None:
    seq = _mono()
    fallback = SimpleNamespace(fwhm=2.89, roundness=0.989, nbstars=10)
    out = fm.metrics_from_siril(seq["R"][0][11], seq["M"][(0, 11)], _stars(), fallback, None)
    assert out["fwhm"] == pytest.approx(2.89)
    assert out["eccentricity"] == pytest.approx(math.sqrt(1 - 0.989 ** 2))
    assert out["stars"] == 10


def test_metrics_all_zero_r_without_fallback_is_none() -> None:
    seq = _mono()
    assert fm.metrics_from_siril(seq["R"][0][11], seq["M"][(0, 11)], _stars(), None, None) is None


def test_metrics_fallback_with_zero_fwhm_is_none() -> None:
    fallback = {"fwhm": 0.0, "roundness": 0.9, "nbstars": 5}
    assert fm.metrics_from_siril(None, None, [], fallback, None) is None


def test_metrics_missing_r_and_no_fallback_is_none() -> None:
    assert fm.metrics_from_siril(None, _mono()["M"][(0, 0)], _stars(), None, None) is None


def test_metrics_no_stars_gives_no_snr_or_flux() -> None:
    seq = _mono()
    out = fm.metrics_from_siril(seq["R"][0][0], seq["M"][(0, 0)], [], None, None)
    assert out["psfSNR"] is None
    assert out["psfFlux"] is None
    assert out["stars"] == 117


def test_metrics_missing_m_line_gives_no_background_or_snr() -> None:
    seq = _mono()
    out = fm.metrics_from_siril(seq["R"][0][0], None, _stars(), None, None)
    assert out["background"] is None and out["noise"] is None and out["psfSNR"] is None
    assert out["psfFlux"] is not None


# ---- measure_batch against a stub (no Siril) -------------------------------------------------------------------

class CommandError(Exception):
    pass


class FakeSiril:
    """Records every command. ``cd`` changes the directory it reports; ``register`` writes the fixture sequence
    and two star lists into the current directory, as Siril would for a batch of 12."""

    def __init__(self, wd: str, register_error: bool = False) -> None:
        self.wd = wd
        self.cwd = wd
        self.register_error = register_error
        self.commands: list[str] = []
        self.links_during_register: dict[str, str] = {}
        self.loaded: list[str] = []
        self.analysed: list[str] = []
        self.pixels = None

    def get_siril_wd(self) -> str:
        return self.wd

    def cmd(self, text: str) -> bool:
        self.commands.append(text)
        if text.startswith('cd "'):
            self.cwd = text[len('cd "'):-1]
            return True
        if text == "register fs_ -2pass":
            for name in sorted(os.listdir(self.cwd)):
                full = os.path.join(self.cwd, name)
                if name.startswith("fs_") and os.path.islink(full):
                    self.links_during_register[name] = os.readlink(full)
            if self.register_error:
                raise CommandError("register failed")
            shutil.copy(FIXTURES / "mono.seq", os.path.join(self.cwd, "fs_.seq"))
            os.makedirs(os.path.join(self.cwd, "cache"), exist_ok=True)
            for name in ("fs_00001.lst", "fs_00012.lst"):
                shutil.copy(FIXTURES / name, os.path.join(self.cwd, "cache", name))
        return True

    def analyse_image_from_file(self, path: str) -> SimpleNamespace:
        self.analysed.append(path)
        return SimpleNamespace(fwhm=2.89, roundness=0.989, nbstars=10, wfwhm=0.0)

    def load_image_from_file(self, path: str, with_pixels: bool = True) -> SimpleNamespace:
        self.loaded.append(path)
        return SimpleNamespace(pixeldata=self.pixels)

    def get_image_pixeldata(self):
        return self.pixels


def _names(n: int) -> list[str]:
    return [f"/frames/light_{k:02d}.fit" for k in range(1, n + 1)]


def test_measure_batch_keys_by_path_in_any_order(tmp_path) -> None:
    names = _names(12)
    order = [names[k] for k in (7, 2, 11, 0, 5, 9, 1, 3, 10, 4, 8, 6)]
    siril = FakeSiril(str(tmp_path / "siril_wd"))
    siril.wd = str(tmp_path / "siril_wd")
    os.makedirs(siril.wd)
    results, problem = fm.measure_batch(siril, order, str(tmp_path / "work"))
    assert problem is None
    assert set(results) == set(order)
    # position 0 of the sequence is the first path of the batch, whichever order the paths came in
    assert results[order[0]]["fwhm"] == pytest.approx(3.07018)
    assert results[order[0]]["stars"] == 117
    seq = _mono()
    assert results[order[5]]["fwhm"] == pytest.approx(seq["R"][0][5]["fwhm"])
    assert results[order[5]]["background"] == pytest.approx(seq["M"][(0, 5)]["median"])


def test_measure_batch_links_point_at_sources_during_register(tmp_path) -> None:
    names = _names(12)
    siril = FakeSiril(str(tmp_path / "siril_wd"))
    os.makedirs(siril.wd)
    fm.measure_batch(siril, names, str(tmp_path / "work"))
    assert siril.links_during_register == {fm.link_name(i): names[i - 1] for i in range(1, 13)}


def test_measure_batch_command_text_and_cd_restored(tmp_path) -> None:
    names = _names(12)
    wd0 = str(tmp_path / "siril_wd")
    os.makedirs(wd0)
    siril = FakeSiril(wd0)
    fm.measure_batch(siril, names, str(tmp_path / "work"))
    assert siril.commands[0] == f'cd "{tmp_path / "work"}"'
    assert siril.commands[1] == "setfindstar reset"
    assert siril.commands[2] == "register fs_ -2pass"
    assert siril.commands[-1] == f'cd "{wd0}"'
    assert siril.cwd == wd0


def test_measure_batch_cd_restored_when_register_raises(tmp_path) -> None:
    names = _names(12)
    wd0 = str(tmp_path / "siril_wd")
    os.makedirs(wd0)
    siril = FakeSiril(wd0, register_error=True)
    results, problem = fm.measure_batch(siril, names, str(tmp_path / "work"))
    assert problem is None
    assert siril.cwd == wd0
    assert siril.commands[-1] == f'cd "{wd0}"'
    # no sequence and no star lists: every frame has only the fallback, which this stub gives for each
    assert all(results[n] is not None and results[n]["stars"] == 10 for n in names)
    assert all(results[n]["background"] is None for n in names)


def test_measure_batch_work_dir_removed(tmp_path) -> None:
    names = _names(12)
    wd0 = str(tmp_path / "siril_wd")
    os.makedirs(wd0)
    work = tmp_path / "work"
    siril = FakeSiril(wd0)
    fm.measure_batch(siril, names, str(work))
    assert not work.exists()


def test_measure_batch_fallback_only_for_all_zero_row(tmp_path) -> None:
    names = _names(12)
    wd0 = str(tmp_path / "siril_wd")
    os.makedirs(wd0)
    siril = FakeSiril(wd0)
    results, _ = fm.measure_batch(siril, names, str(tmp_path / "work"), altitude_of=lambda p: 45.0)
    assert siril.analysed == [names[11]]
    assert results[names[11]]["fwhm"] == pytest.approx(2.89)
    assert results[names[0]]["altitude"] == 45.0


def test_measure_batch_duplicate_path_refused_without_commands(tmp_path) -> None:
    names = _names(3)
    siril = FakeSiril(str(tmp_path))
    results, problem = fm.measure_batch(siril, [names[0], names[1], names[0]], str(tmp_path / "work"))
    assert results == {}
    assert problem == f"duplicate path: {names[0]}"
    assert siril.commands == []


def test_measure_batch_sequence_count_mismatch_abandons_batch(tmp_path) -> None:
    names = _names(8)
    wd0 = str(tmp_path / "siril_wd")
    os.makedirs(wd0)
    siril = FakeSiril(wd0)
    results, problem = fm.measure_batch(siril, names, str(tmp_path / "work"))
    assert results == {}
    assert problem == "sequence has 12 images, batch has 8"
    assert siril.cwd == wd0


def test_measure_batch_non_fits_source_is_loaded_and_written(tmp_path) -> None:
    names = _names(11) + ["/frames/light_12.xisf"]
    wd0 = str(tmp_path / "siril_wd")
    os.makedirs(wd0)
    siril = FakeSiril(wd0)
    siril.pixels = np.arange(30 * 40, dtype=np.uint16).reshape(30, 40)
    seen_work: dict[str, bool] = {}
    original_register = siril.cmd

    def watching_cmd(text: str) -> bool:
        if text == "register fs_ -2pass":
            seen_work["fs_00012.fit_is_file"] = os.path.isfile(os.path.join(siril.cwd, "fs_00012.fit")) and not \
                os.path.islink(os.path.join(siril.cwd, "fs_00012.fit"))
        return original_register(text)

    siril.cmd = watching_cmd
    results, problem = fm.measure_batch(siril, names, str(tmp_path / "work"))
    assert problem is None
    assert siril.loaded == ["/frames/light_12.xisf"]
    assert seen_work == {"fs_00012.fit_is_file": True}
    assert results["/frames/light_12.xisf"]["fwhm"] == pytest.approx(2.89)


def test_measure_batch_log_gets_failed_register(tmp_path) -> None:
    names = _names(12)
    wd0 = str(tmp_path / "siril_wd")
    os.makedirs(wd0)
    notes: list[str] = []
    fm.measure_batch(FakeSiril(wd0, register_error=True), names, str(tmp_path / "work"), log=notes.append)
    assert any("register failed" in n for n in notes)


# ---- real Siril (skipped locally, runs in the Siril image) -----------------------------------------------------

#: Frame names and what each is: the normal frames, a blurred one, an elongated one, a cloudy one, a sparse one.
NORMAL = ["f01", "f02", "f03", "f04", "f05", "f06", "f07", "f08"]


def _synthetic_frames(dst: Path) -> dict[str, str]:
    """Twelve 400x300 16-bit frames of the same 120 Gaussian stars, with a known difference per frame."""
    w, h, n_stars = 400, 300, 120
    rng0 = np.random.default_rng(7)
    pos: list[tuple[float, float]] = []
    while len(pos) < n_stars:
        x, y = rng0.uniform(12, w - 13), rng0.uniform(12, h - 13)
        if all((x - a) ** 2 + (y - b) ** 2 > 18 ** 2 for a, b in pos):
            pos.append((x, y))
    peaks = rng0.uniform(0.08, 0.7, n_stars)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    spec = {f"f{i:02d}": (3.0 + 0.05 * (i % 3), 1.0, 1.0, 0.08, 120) for i in range(1, 9)}
    spec["f09"] = (5.0, 1.0, 1.0, 0.08, 120)
    spec["f10"] = (3.0, 1.8, 1.0, 0.08, 120)
    spec["f11"] = (3.0, 1.0, 0.4, 0.12, 120)
    spec["f12"] = (3.0, 1.0, 1.0, 0.08, 12)
    paths: dict[str, str] = {}
    for k, (name, (fwhm, elong, scale, bg, count)) in enumerate(sorted(spec.items())):
        rng = np.random.default_rng(100 + k)
        img = np.full((h, w), bg) + 0.01 * xx / w
        sy = fwhm / 2.3548200450309493
        sx = sy * elong
        for (x, y), p in list(zip(pos, peaks))[:count]:
            img += p * scale * np.exp(-((xx - x) ** 2 / (2 * sx * sx) + (yy - y) ** 2 / (2 * sy * sy)))
        img = np.clip(img + rng.normal(0, 0.004, img.shape), 0, 1)
        path = dst / f"{name}.fit"
        write_fits(str(path), [img.astype(np.float32)], {"FILTER": "L", "EXPTIME": 120.0,
                                                         "DATE-OBS": f"2026-10-01T21:{k:02d}:00"}, bitpix=16)
        paths[name] = str(path)
    return paths


_SIRIL_SCRIPT = r'''
import json, os, sys
import sirilpy as s
from loomsiril.frames_measure import measure_batch

base = sys.argv[1]
siril = s.SirilInterface()
siril.connect()
names_a = ["f%02d" % i for i in range(1, 9)]
names_b = ["f09", "f10", "f11", "f12", "f01", "f02", "f03", "f04"]
for label, names in (("a", names_a), ("b", names_b)):
    paths = [os.path.join(base, n + ".fit") for n in names]
    results, problem = measure_batch(siril, paths, os.path.join(base, "work_" + label))
    if problem is not None:
        print("problem=" + problem)
        continue
    for name, p in zip(names, paths):
        m = results[p]
        print("m." + name + "=" + json.dumps(m))
'''


@pytest.mark.siril
def test_measure_batch_in_siril_separates_the_frames(tmp_path) -> None:
    _synthetic_frames(tmp_path)
    run = run_pyscript(_SIRIL_SCRIPT, tmp_path, args=[str(tmp_path)]).check()
    got = {}
    for line in run.lines("m."):
        key, _, value = line[2:].partition("=")
        got[key] = json.loads(value)
    assert set(got) == {"f01", "f02", "f03", "f04", "f05", "f06", "f07", "f08", "f09", "f10", "f11", "f12"}
    normal = [got[n] for n in NORMAL]
    assert all(frames.frame_valid(m) for m in normal)
    median_fwhm = frames.median([m["fwhm"] for m in normal])
    median_flux = frames.median([m["psfFlux"] for m in normal])
    median_bg = frames.median([m["background"] for m in normal])
    assert got["f09"]["fwhm"] > 1.4 * median_fwhm
    assert got["f10"]["eccentricity"] > 0.7
    assert got["f11"]["psfFlux"] < 0.75 * median_flux
    assert got["f11"]["background"] > 1.05 * median_bg
    assert got["f12"] is not None and got["f12"]["stars"] < 20
