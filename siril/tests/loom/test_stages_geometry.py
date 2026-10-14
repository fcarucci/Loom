"""stages_geometry: registration, the common crop, halo matching and the narrowband balance, checked against the
transcript a fake Siril records and against numpy expectations on synthetic frames (no Siril here).
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.loom import imageops
from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import siril_ops
from loomsiril.loom.siril_ops import RecordingOps, SirilCommandOps, SirilOps, SirilOpsError
from loomsiril.loom.stagekit import StageFailed
from loomsiril.loom.stages_geometry import (
    _percent_text,
    balance_narrowband,
    common_crop_rect,
    crop_plate,
    match_halos,
    measure_psf_sigma,
    register_channels,
    register_stage_params,
)

FWHM_PER_SIGMA = imageops.FWHM_PER_SIGMA


def _write(path: Path, data: np.ndarray) -> str:
    write_fits(path, [np.asarray(data, dtype=np.float32)], {"OBJECT": "synthetic"}, bitpix=-32)
    return str(path)


def _star(fwhm: float) -> dict[str, Any]:
    return {"xpos": 10.0, "ypos": 10.0, "fwhmx": fwhm, "fwhmy": fwhm, "A": 0.5}


class _StarFake(RecordingOps):
    """RecordingOps whose find_stars serves the stars of the file loaded last."""

    def __init__(self, workdir: Path, stars_by_file: dict[str, list[dict[str, Any]]], **kw: Any) -> None:
        super().__init__(workdir, **kw)
        self.stars_by_file = stars_by_file

    def load(self, path: Any) -> None:
        super().load(path)
        self._stars = list(self.stars_by_file.get(Path(path).name, []))


# ---------------------------------------------------------------------------------------------------------------
# wd() and the stage parameters
# ---------------------------------------------------------------------------------------------------------------


def test_recording_ops_wd_follows_cd_and_sends_nothing(tmp_path: Path) -> None:
    ops = RecordingOps(tmp_path)
    assert ops.wd() == str(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    ops.cd(sub)
    assert ops.wd() == str(sub)
    assert ops.transcript == [f'cd "{sub}"']


def test_sirilops_base_wd_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        SirilOps().wd()


class _SirilWd:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def get_siril_wd(self) -> str:
        return "/var/siril/work"

    def cmd(self, text: str) -> bool:
        self.commands.append(text)
        return True


def test_siril_command_ops_wd_reads_the_folder_without_a_command() -> None:
    siril = _SirilWd()
    ops = SirilCommandOps(siril)
    assert ops.wd() == "/var/siril/work"
    assert siril.commands == []


def test_siril_command_ops_wd_refuses_another_thread() -> None:
    ops = SirilCommandOps(_SirilWd())
    errors: list[BaseException] = []

    def other() -> None:
        try:
            ops.wd()
        except BaseException as exc:  # noqa: BLE001 - recorded and asserted below
            errors.append(exc)

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == siril_ops._PIPELINE_THREAD_MESSAGE


def test_register_stage_params_is_the_reference_key() -> None:
    assert register_stage_params("chain-key") == {"ref": "chain-key"}


# ---------------------------------------------------------------------------------------------------------------
# register_channels
# ---------------------------------------------------------------------------------------------------------------


def _register_handler(ops: RecordingOps, name: str) -> None:
    """Simulate Siril: output k is a copy of the k-th linked frame (sorted by file name)."""
    work = Path(ops.workdir)
    for k, frame in enumerate(sorted(work.glob("*.fits")), start=1):
        shutil.copyfile(frame, work / f"r_{name}_{k:05d}.fit")


def test_register_layout_transcript_and_outputs(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    plates = {ch: _write(tmp_path / f"{ch}_in.fits", rng.random((40, 50))) for ch in ("L", "R", "G")}
    ops = RecordingOps(tmp_path, handlers={"register": _register_handler})
    logs: list[str] = []

    out = register_channels(ops, plates, "L", tmp_path, logs.append)

    folder = tmp_path / "register-1"
    assert sorted(p.name for p in folder.glob("*.fits")) == ["01_L.fits", "02_R.fits", "03_G.fits"]
    assert ops.transcript == [
        f'cd "{folder}"',
        f'link reg "-out={folder}"',
        "setref reg 1",
        "register reg -transf=homography -interp=lanczos4",
        f'cd "{tmp_path}"',
    ]
    assert out == {ch: str(tmp_path / f"{ch}_register.fits") for ch in ("L", "R", "G")}
    for ch in ("L", "R", "G"):
        assert Path(out[ch]).exists()
    assert not list(folder.glob("r_reg_*.fit"))
    assert logs[0] == "register: reference is L"


def test_register_reference_is_bit_identical(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    ref_data = rng.random((30, 44)).astype(np.float32)
    plates = {"L": _write(tmp_path / "L_in.fits", ref_data), "R": _write(tmp_path / "R_in.fits", rng.random((30, 44)))}
    ops = RecordingOps(tmp_path, handlers={"register": _register_handler})

    out = register_channels(ops, plates, "L", tmp_path, lambda text: None)

    got, _ = read_fits(out["L"])
    np.testing.assert_array_equal(got[0], ref_data)


def test_register_second_run_uses_a_fresh_folder(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    plates = {"L": _write(tmp_path / "L_in.fits", rng.random((12, 12))),
              "G": _write(tmp_path / "G_in.fits", rng.random((12, 12)))}
    ops = RecordingOps(tmp_path, handlers={"register": _register_handler})
    register_channels(ops, plates, "L", tmp_path, lambda text: None)
    register_channels(ops, plates, "G", tmp_path, lambda text: None)
    assert (tmp_path / "register-1").is_dir() and (tmp_path / "register-2").is_dir()
    assert sorted(p.name for p in (tmp_path / "register-2").glob("*.fits")) == ["01_G.fits", "02_L.fits"]


def test_register_with_only_the_reference_sends_nothing(tmp_path: Path) -> None:
    src = _write(tmp_path / "L_in.fits", np.ones((8, 8)))
    ops = RecordingOps(tmp_path)
    out = register_channels(ops, {"L": src}, "L", tmp_path, lambda text: None)
    assert out == {"L": src}
    assert ops.transcript == []


def test_register_restores_the_folder_when_siril_fails(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    plates = {"L": _write(tmp_path / "L_in.fits", rng.random((8, 8))),
              "R": _write(tmp_path / "R_in.fits", rng.random((8, 8)))}
    ops = RecordingOps(tmp_path, fail={"register": RuntimeError("no stars")})
    with pytest.raises(SirilOpsError):
        register_channels(ops, plates, "L", tmp_path, lambda text: None)
    assert ops.workdir == str(tmp_path)


# ---------------------------------------------------------------------------------------------------------------
# common_crop_rect and crop_plate
# ---------------------------------------------------------------------------------------------------------------


def _block(shape: tuple[int, int], x0: int, x1: int, y0: int, y1: int, rng: np.random.Generator) -> np.ndarray:
    a = np.zeros(shape, dtype=np.float32)
    a[y0:y1, x0:x1] = rng.uniform(0.1, 1.0, (y1 - y0, x1 - x0))
    return a


def test_common_crop_rect_is_the_intersection_of_the_valid_rectangles(tmp_path: Path) -> None:
    rng = np.random.default_rng(5)
    plates = {
        "L": _write(tmp_path / "L.fits", _block((80, 100), 6, 94, 3, 77, rng)),
        "R": _write(tmp_path / "R.fits", _block((80, 100), 2, 98, 8, 70, rng)),
    }
    # valid rectangles with the margin of 4: L (10, 7, 90, 73), R (6, 12, 94, 66)
    assert common_crop_rect(plates, "L") == (10, 12, 90, 66)
    assert common_crop_rect(plates, "R") == (10, 12, 90, 66)


def test_common_crop_rect_refuses_a_too_small_area(tmp_path: Path) -> None:
    rng = np.random.default_rng(6)
    plates = {
        "L": _write(tmp_path / "L.fits", _block((80, 100), 6, 94, 3, 77, rng)),
        "R": _write(tmp_path / "R.fits", _block((80, 100), 0, 55, 0, 43, rng)),
    }
    # R's valid rectangle is (4, 4, 51, 39); with L's (10, 7, 90, 73) the common area is 41 x 32 = 1312 px, 16.4%
    with pytest.raises(StageFailed) as info:
        common_crop_rect(plates, "L")
    message = str(info.value)
    assert message.startswith("The common imaged area is only 16.4% of the reference frame.")
    assert "refusing to crop away most of the image." in message


def test_common_crop_rect_refuses_channels_without_overlap(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    plates = {
        "L": _write(tmp_path / "L.fits", _block((80, 100), 0, 10, 0, 80, rng)),
        "R": _write(tmp_path / "R.fits", _block((80, 100), 90, 100, 0, 80, rng)),
    }
    with pytest.raises(StageFailed, match="The channels have no overlapping imaged area; they cannot be combined."):
        common_crop_rect(plates, "L")


def test_crop_plate_transcript_and_pixels(tmp_path: Path) -> None:
    rng = np.random.default_rng(8)
    data = rng.random((48, 64)).astype(np.float32)
    src = _write(tmp_path / "in.fits", data)
    out_path = tmp_path / "out.fits"
    ops = RecordingOps(tmp_path)

    out = crop_plate(ops, src, out_path, (3, 5, 40, 30))

    assert out == str(out_path)
    assert ops.transcript == [f'load "{src}"', "crop 3 5 37 25", f'save "{out_path}"']
    got, _ = read_fits(out)
    np.testing.assert_array_equal(got[0], data[5:30, 3:40])


# ---------------------------------------------------------------------------------------------------------------
# measure_psf_sigma and match_halos
# ---------------------------------------------------------------------------------------------------------------


def test_measure_psf_sigma_crops_the_central_square_and_reads_the_medians(tmp_path: Path) -> None:
    src = _write(tmp_path / "R.fits", np.full((200, 300), 0.1))
    stars = [_star(3.0)] * 5 + [_star(4.0)] * 2
    ops = _StarFake(tmp_path, {"R.fits": stars})
    sigma = measure_psf_sigma(ops, src)
    # central square of min(1200, 300, 200) = 200 px at x = (300 - 200) >> 1 = 50, y = 0
    assert ops.transcript == [f'load "{src}"', "crop 50 0 200 200", "setfindstar reset -gaussian", "findstar"]
    assert sigma == pytest.approx((3.0 + 3.0) / 2 / FWHM_PER_SIGMA)


def test_measure_psf_sigma_uses_at_most_400_stars_in_detector_order(tmp_path: Path) -> None:
    src = _write(tmp_path / "R.fits", np.full((60, 60), 0.1))
    # 240 stars of FWHM 3 come first, then 260 of FWHM 9: the first 400 have median 3, all 500 would give 9
    stars = [_star(3.0)] * 240 + [_star(9.0)] * 260
    sigma = measure_psf_sigma(_StarFake(tmp_path, {"R.fits": stars}), src)
    assert sigma == pytest.approx(3.0 / FWHM_PER_SIGMA)


def test_measure_psf_sigma_is_none_without_stars(tmp_path: Path) -> None:
    src = _write(tmp_path / "R.fits", np.full((60, 60), 0.1))
    assert measure_psf_sigma(_StarFake(tmp_path, {}), src) is None


def test_measure_psf_square_is_capped_at_1200(tmp_path: Path) -> None:
    src = _write(tmp_path / "big.fits", np.zeros((1300, 1500), dtype=np.float32))
    ops = _StarFake(tmp_path, {"big.fits": []})
    measure_psf_sigma(ops, src)
    assert "crop 150 50 1200 1200" in ops.transcript


def _halo_plates(tmp_path: Path, names: tuple[str, ...]) -> dict[str, str]:
    rng = np.random.default_rng(9)
    return {ch: _write(tmp_path / f"{ch}.fits", rng.random((64, 96))) for ch in names}


def test_match_halos_blurs_narrower_channels_and_never_l(tmp_path: Path) -> None:
    plates = _halo_plates(tmp_path, ("L", "R", "G"))
    stars = {"L.fits": [_star(2.0)] * 5, "R.fits": [_star(3.0)] * 5, "G.fits": [_star(4.0)] * 5}
    ops = _StarFake(tmp_path, stars)
    logs: list[str] = []

    out = match_halos(ops, plates, tmp_path, logs.append)

    widest = 4.0 / FWHM_PER_SIGMA
    add = imageops.sigma_to_reach(widest, 3.0 / FWHM_PER_SIGMA)
    assert out == {"L": plates["L"], "R": str(tmp_path / "R_halo.fits"), "G": plates["G"]}
    assert f'load "{plates["L"]}"' not in ops.transcript
    assert not [t for t in ops.transcript if t.startswith("save ")]
    src, _ = read_fits(plates["R"])
    got, _ = read_fits(out["R"])
    expected = imageops.gaussian_blur(src, add)
    np.testing.assert_allclose(got[0], expected[0], atol=1e-6)
    assert logs[0] == "halos: R sigma=1.274 (FWHM 3.00 px, 5 stars)"
    assert "halos: matching to the widest, sigma=1.699" in logs
    assert f"halos: R: convolving by sigma={add:.3f}" in logs
    assert "halos: G: already the widest, unchanged" in logs


def test_match_halos_leaves_unmeasured_channels_and_skips_under_two(tmp_path: Path) -> None:
    plates = _halo_plates(tmp_path, ("R", "G"))
    ops = _StarFake(tmp_path, {"R.fits": [_star(3.0)] * 3})
    logs: list[str] = []

    out = match_halos(ops, plates, tmp_path, logs.append)

    assert out == plates
    assert "halos: G: no PSF fit, leaving it untouched" in logs
    assert "halos: skipped: fewer than two channels could be measured" in logs
    assert not [t for t in ops.transcript if t.startswith("save ")]


def test_match_halos_nothing_to_do_for_equal_widths(tmp_path: Path) -> None:
    plates = _halo_plates(tmp_path, ("R", "G"))
    ops = _StarFake(tmp_path, {"R.fits": [_star(3.5)] * 3, "G.fits": [_star(3.5)] * 3})
    out = match_halos(ops, plates, tmp_path, lambda text: None)
    assert out == plates


# ---------------------------------------------------------------------------------------------------------------
# balance_narrowband
# ---------------------------------------------------------------------------------------------------------------


def _const_plates(tmp_path: Path, values: dict[str, float], shape: tuple[int, int] = (20, 30)) -> dict[str, str]:
    return {ch: _write(tmp_path / f"{ch}.fits", np.full(shape, v)) for ch, v in values.items()}


def test_balance_fits_every_channel_to_the_lowest_median(tmp_path: Path) -> None:
    plates = _const_plates(tmp_path, {"H": 0.3, "S": 0.1, "O": 0.2, "R": 0.01})
    ops = RecordingOps(tmp_path)
    logs: list[str] = []

    out = balance_narrowband(ops, plates, False, tmp_path, logs.append)

    assert ops.transcript == [
        f'load "{plates["H"]}"',
        f'linear_match "{plates["S"]}" 0 0.92',
        f'save "{tmp_path / "H_balance.fits"}"',
        f'load "{plates["O"]}"',
        f'linear_match "{plates["S"]}" 0 0.92',
        f'save "{tmp_path / "O_balance.fits"}"',
    ]
    assert out == {"H": str(tmp_path / "H_balance.fits"), "S": plates["S"], "O": str(tmp_path / "O_balance.fits"),
                   "R": plates["R"]}
    assert logs == ["linearfit: reference is S", "linearfit: H -> S", "linearfit: O -> S"]


def test_balance_with_palettes_offsets_and_keeps_negatives(tmp_path: Path) -> None:
    h = np.full((20, 30), 0.3, dtype=np.float32)
    h[0, 0] = 0.0
    hp = _write(tmp_path / "H.fits", h)
    plates = {"H": hp, **_const_plates(tmp_path, {"S": 0.1, "O": 0.2})}
    ops = RecordingOps(tmp_path)
    logs: list[str] = []

    out = balance_narrowband(ops, plates, True, tmp_path, logs.append)

    assert ops.transcript == []
    assert out["S"] == plates["S"]
    got_h, _ = read_fits(out["H"])
    assert got_h[0][1, 1] == pytest.approx(0.1, abs=1e-6)
    assert got_h[0][0, 0] == pytest.approx(-0.2, abs=1e-6)
    got_o, _ = read_fits(out["O"])
    assert got_o[0][5, 5] == pytest.approx(0.1, abs=1e-6)
    assert logs == [
        "background: narrowband sky floors matched to S by offset; line ratios preserved for SPCC",
        "background: H: offset -2.0000e-1 to match S",
        "background: O: offset -1.0000e-1 to match S",
    ]


def test_balance_skips_with_fewer_than_two_narrowband_channels(tmp_path: Path) -> None:
    plates = _const_plates(tmp_path, {"H": 0.3, "R": 0.5})
    ops = RecordingOps(tmp_path)
    logs: list[str] = []
    assert balance_narrowband(ops, plates, True, tmp_path, logs.append) == plates
    assert balance_narrowband(ops, plates, False, tmp_path, logs.append) == plates
    assert ops.transcript == []
    assert logs == ["linearfit: skipped: fewer than two narrowband channels"] * 2


def test_common_crop_rect_logs_the_common_area_line(tmp_path: Path) -> None:
    rng = np.random.default_rng(10)
    plates = {
        "L": _write(tmp_path / "L.fits", _block((80, 100), 6, 94, 3, 77, rng)),
        "R": _write(tmp_path / "R.fits", _block((80, 100), 2, 98, 8, 70, rng)),
    }
    logs: list[str] = []
    assert common_crop_rect(plates, "L", logs.append) == (10, 12, 90, 66)
    assert logs == ["crop: common area 80 x 54 at (10,12) - 54.0% of the reference frame"]


def test_common_crop_rect_logs_the_line_before_the_too_small_refusal(tmp_path: Path) -> None:
    rng = np.random.default_rng(11)
    plates = {
        "L": _write(tmp_path / "L.fits", _block((80, 100), 6, 94, 3, 77, rng)),
        "R": _write(tmp_path / "R.fits", _block((80, 100), 0, 55, 0, 43, rng)),
    }
    logs: list[str] = []
    with pytest.raises(StageFailed, match="only 16.4% of the reference frame"):
        common_crop_rect(plates, "L", logs.append)
    assert logs == ["crop: common area 41 x 32 at (10,7) - 16.4% of the reference frame"]


def test_common_crop_rect_logs_nothing_without_an_overlap(tmp_path: Path) -> None:
    rng = np.random.default_rng(12)
    plates = {
        "L": _write(tmp_path / "L.fits", _block((80, 100), 0, 10, 0, 80, rng)),
        "R": _write(tmp_path / "R.fits", _block((80, 100), 90, 100, 0, 80, rng)),
    }
    logs: list[str] = []
    with pytest.raises(StageFailed):
        common_crop_rect(plates, "L", logs.append)
    assert logs == []


def test_register_missing_frame_names_the_channel(tmp_path: Path) -> None:
    rng = np.random.default_rng(13)
    plates = {"L": _write(tmp_path / "L_in.fits", rng.random((8, 8))),
              "R": _write(tmp_path / "R_in.fits", rng.random((8, 8)))}

    def only_the_reference(ops: RecordingOps, name: str) -> None:
        shutil.copyfile(Path(ops.workdir) / "01_L.fits", Path(ops.workdir) / f"r_{name}_00001.fit")

    ops = RecordingOps(tmp_path, handlers={"register": only_the_reference})
    with pytest.raises(StageFailed, match="register failed on R"):
        register_channels(ops, plates, "L", tmp_path, lambda text: None)
    assert ops.workdir == str(tmp_path)


def test_balance_centre_fraction_is_sixty_percent_not_fifty(tmp_path: Path) -> None:
    # 100 x 100 planes. The central 60% (rows and columns 20..79) has an outer band of 0.0 (rows 20..24 and 75..79,
    # and their columns) inside it; the central 50% (25..74) has 0.1 in rows 25..49 and 0.4 in rows 50..74; the
    # frame outside the 60% is 0.9. Medians: H over 60% = 0.1 (the band shifts the median), over 50% = 0.4;
    # S = 0.5 and O = 0.3 everywhere in the centre. So H is the reference at 60% and O at 50%.
    def h_plane() -> np.ndarray:
        a = np.full((100, 100), 0.9, dtype=np.float32)
        a[20:80, 20:80] = 0.0
        a[25:50, 25:75] = 0.1
        a[50:75, 25:75] = 0.4
        return a

    plates = {
        "H": _write(tmp_path / "H.fits", h_plane()),
        "S": _write(tmp_path / "S.fits", np.full((100, 100), 0.5, dtype=np.float32)),
        "O": _write(tmp_path / "O.fits", np.full((100, 100), 0.3, dtype=np.float32)),
    }
    logs: list[str] = []
    balance_narrowband(RecordingOps(tmp_path), plates, True, tmp_path, logs.append)
    assert logs == [
        "background: narrowband sky floors matched to H by offset; line ratios preserved for SPCC",
        "background: S: offset -4.0000e-1 to match H",
        "background: O: offset -2.0000e-1 to match H",
    ]


def test_percent_text_rounds_half_up_like_to_fixed() -> None:
    # 12.25 is exact in binary, so the tie goes up as JavaScript's toFixed(1) does
    assert _percent_text(12.25) == "12.3"
    assert _percent_text(12.24) == "12.2"
    assert _percent_text(54.0) == "54.0"


def test_measure_psf_sigma_drops_stars_without_a_positive_fwhm(tmp_path: Path) -> None:
    src = _write(tmp_path / "R.fits", np.full((60, 60), 0.1))
    bad = {"xpos": 1.0, "ypos": 1.0, "fwhmx": 0.0, "fwhmy": 0.0}
    stars = [bad] * 4 + [_star(3.0)] * 3
    logs: list[str] = []
    sigma = measure_psf_sigma(_StarFake(tmp_path, {"R.fits": stars}), src, logs.append)
    assert sigma == pytest.approx(3.0 / FWHM_PER_SIGMA)
    assert logs == [f"psf: 3 fitted stars, sigma={3.0 / FWHM_PER_SIGMA:.3f}"]
