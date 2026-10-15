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
    GROUP_REGISTER_LAYER,
    group_register_params,
    match_halos,
    measure_psf_sigma,
    member_split_header,
    register_channels,
    register_group_image,
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


# ---------------------------------------------------------------------------------------------------------------
# a colour group registered as one image
# ---------------------------------------------------------------------------------------------------------------

#: What the fake Siril does to the group image: every plane moves by this many rows and columns.
GROUP_ROLL = (2, 3)


def _group_rgbcomp(ops: RecordingOps, r: Any, g: Any, b: Any, out: Any) -> str:
    """Siril's rgbcomp: three plates become one three-plane image whose header is a temporary one."""
    planes = [read_fits(str(p))[0][0] for p in (r, g, b)]
    header = dict(read_fits(str(r))[1])
    header["FILTER"] = "TEMPGROUP"
    header["LOOMSRC"] = "temporary-group"
    path = str(out) + ".fit"
    write_fits(path, planes, header, bitpix=-32)
    return path


def _group_registrar(layers: list) -> Any:
    """Siril's register on a two-frame colour sequence: the first frame is the reference and stays; the second
    moves by GROUP_ROLL. The sequence files Siril leaves behind are made too."""

    def handler(ops: RecordingOps, name: str, layer: Any = None) -> None:
        layers.append(layer)
        work = Path(ops.workdir)
        for k, frame in enumerate(sorted(work.glob("*.fit")), start=1):
            planes, header = read_fits(str(frame))
            if k > 1:
                planes = [np.roll(p, GROUP_ROLL, axis=(0, 1)) for p in planes]
            write_fits(work / f"r_{name}_{k:05d}.fit", planes, header, bitpix=-32)
        # what Siril 1.4 leaves next to the frames
        (work / f"{name}_.seq").write_text("seq")
        (work / f"r_{name}_.seq").write_text("seq")
        (work / f"{name}_conversion.txt").write_text("conversion")
        (work / "cache").mkdir(exist_ok=True)
        for k in (1, 2):
            (work / "cache" / f"{name}_{k:05d}.lst").write_text("stars")
            (work / f"{name}_{k:05d}.fit").symlink_to(work / ("01_ref.fit" if k == 1 else "02_group.fit"))

    return handler


def _planes3(seed: int, shape: tuple[int, int] = (24, 32)) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [rng.random(shape).astype(np.float32) for _ in range(3)]


def test_group_register_params_hold_the_reference_key_and_the_planes() -> None:
    assert group_register_params("ref-key", ["H", "O", "O"]) == {"ref": "ref-key", "planes": "H,O,O"}
    assert group_register_params("k", ["R", "G", "B"]) == {"ref": "k", "planes": "R,G,B"}


def test_group_layers_are_osc_green_and_oscnb_first_plane() -> None:
    assert GROUP_REGISTER_LAYER == {"OSC": 1, "OSCNB": 0}


def test_member_split_header_takes_the_masters_filter_and_loomsrc() -> None:
    header = {"OBJECT": "M42", "FILTER": "TEMPGROUP", "LOOMSRC": "temporary-group", "EXPTIME": 3.0}
    got = member_split_header(header, "O", "/data/night/master.fits", 1)
    assert got["FILTER"] == "OIII" and got["LOOMSRC"] == "master.fits|plane:1"
    assert got["OBJECT"] == "M42" and got["EXPTIME"] == 3.0
    assert header["FILTER"] == "TEMPGROUP"  # the input is not changed
    assert member_split_header(header, "H", "/d/m.fits", 0)["FILTER"] == "Ha"
    assert member_split_header(header, "G", "/d/m.fits", 1)["FILTER"] == "G"
    assert member_split_header({}, "R", "/d/m.fits", 0)["FILTER"] == "R"


def test_member_split_header_keeps_the_end_of_a_long_master_name() -> None:
    long_name = "x" * 90 + "_session_ha_oiii.fits"
    loomsrc = member_split_header({}, "H", "/d/" + long_name, 0)["LOOMSRC"]
    assert len(loomsrc) <= 68 and loomsrc.endswith("_session_ha_oiii.fits|plane:0") and loomsrc.startswith("...")


def test_register_group_image_commands_planes_and_cleanup(tmp_path: Path) -> None:
    layers: list = []
    ops = RecordingOps(tmp_path, handlers={"rgbcomp": _group_rgbcomp, "register": _group_registrar(layers)})
    ref = tmp_path / "ref.fits"
    write_fits(ref, [np.full((24, 32), 0.5, np.float32)], {"FILTER": "G"}, bitpix=-32)
    h, o = _planes3(1)[0], _planes3(2)[0]
    plates = [_write(tmp_path / "h.fits", h), _write(tmp_path / "o.fits", o), str(tmp_path / "o.fits")]
    logs: list[str] = []

    planes, header = register_group_image(ops, str(ref), plates, 0, tmp_path, logs.append)

    folder = tmp_path / "register-1"
    assert ops.transcript == [
        f'rgbcomp "{ref}" "{ref}" "{ref}" "-out={folder}/01_ref"',
        f'rgbcomp "{plates[0]}" "{plates[1]}" "{plates[2]}" "-out={folder}/02_group"',
        f'cd "{folder}"',
        f'link reg "-out={folder}"',
        "setref reg 1",
        "register reg -transf=homography -interp=lanczos4 -layer=0",
        f'cd "{tmp_path}"',
    ]
    assert layers == [0]
    assert len(planes) == 3
    np.testing.assert_array_equal(planes[0], np.roll(h, GROUP_ROLL, axis=(0, 1)))
    np.testing.assert_array_equal(planes[1], np.roll(o, GROUP_ROLL, axis=(0, 1)))
    np.testing.assert_array_equal(planes[2], planes[1])
    assert not folder.exists()  # every temporary file went by name, then the folder
    assert ops.workdir == str(tmp_path)


def test_register_group_image_takes_exactly_three_plates(tmp_path: Path) -> None:
    ops = RecordingOps(tmp_path)
    with pytest.raises(ValueError):
        register_group_image(ops, str(tmp_path / "ref.fits"), ["a", "b"], 1, tmp_path, lambda text: None)
    assert ops.transcript == []


def test_register_group_image_failure_restores_the_folder_and_removes_the_temporaries(tmp_path: Path) -> None:
    ops = RecordingOps(tmp_path, handlers={"rgbcomp": _group_rgbcomp}, fail={"register": RuntimeError("no stars")})
    ref = _write(tmp_path / "ref.fits", np.full((24, 32), 0.5))
    plate = _write(tmp_path / "h.fits", np.random.default_rng(3).random((24, 32)))
    with pytest.raises(SirilOpsError):
        register_group_image(ops, ref, [plate, plate, plate], 0, tmp_path, lambda text: None)
    assert ops.workdir == str(tmp_path)
    assert not (tmp_path / "register-1").exists()


def test_register_group_image_without_an_output_frame_names_the_failure(tmp_path: Path) -> None:
    ops = RecordingOps(tmp_path, handlers={"rgbcomp": _group_rgbcomp})  # register records only: no r_reg_00002.fit
    ref = _write(tmp_path / "ref.fits", np.full((24, 32), 0.5))
    plate = _write(tmp_path / "h.fits", np.random.default_rng(3).random((24, 32)))
    with pytest.raises(StageFailed, match="produced no registered frame"):
        register_group_image(ops, ref, [plate, plate, plate], 0, tmp_path, lambda text: None)
    assert not (tmp_path / "register-1").exists() and ops.workdir == str(tmp_path)


# ---------------------------------------------------------------------------------------------------------------
# _Run.register with colour groups
# ---------------------------------------------------------------------------------------------------------------

import os  # noqa: E402

from loomsiril.loom import cache, colour_load, osc, pipeline, stages_correct  # noqa: E402
from loomsiril.loom import config as loom_config  # noqa: E402
from loomsiril.loom.runctx import RunContext  # noqa: E402

SHAPE = (24, 32)
_FILTERS = {"R": "R", "G": "G", "B": "B", "H": "Ha", "O": "OIII"}


class _AnyTool(dict):
    def __missing__(self, key: str) -> str:
        return "/fake/" + key


def _colour_masters(root: Path) -> dict[str, str]:
    """An OSC master (R, G, B planes) and an OSCNB master (H, O, spare), written once per folder."""
    out = {}
    for slot, name, seed, filt in (("OSC", "osc.fits", 10, "L-Pro"), ("OSCNB", "nb.fits", 20, "L-eXtreme")):
        path = root / "masters" / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            write_fits(path, _planes3(seed, SHAPE), {"FILTER": filt, "INSTRUME": "ZWO ASI2600MC", "OBJECT": "M42"},
                       bitpix=-32)
        out[slot] = str(path)
    return out


class _Colour:
    """One run state around the masters of ``slots``, with the fake Siril, its log and the master reads counted."""

    count = 0

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, slots: tuple = ("OSC", "OSCNB"),
                 fail: Any = None, **over: Any) -> None:
        _Colour.count += 1
        masters = _colour_masters(root)
        self.root = root
        self.masters = masters
        kinds = {"OSC": "broadband", "OSCNB": "dualband"}
        (root / "cache").mkdir(exist_ok=True)
        work = root / f"work{_Colour.count}"
        work.mkdir()
        cfg = loom_config.defaults()
        cfg.update({"paths": {**cfg["paths"], **{s: masters[s] for s in slots}},
                    "oscKinds": {s: kinds[s] for s in slots}, "useCache": True, "cacheDir": str(root / "cache"),
                    "outputDir": str(root / "out"), "gradientTool": "none"})
        cfg.update(over)
        self.lines: list[str] = []
        self.layers: list = []
        self.ops = RecordingOps(root / "wd", handlers={"rgbcomp": _group_rgbcomp,
                                                       "register": _group_registrar(self.layers)}, fail=fail)
        (root / "wd").mkdir(exist_ok=True)
        self.ctx = RunContext(ops=self.ops, work_dir=str(work), log=self.lines.append, warn=self.lines.append,
                              tools=_AnyTool())
        self.store = (cache.CacheStore(str(root / "cache"), log=lambda level, text: self.lines.append(text))
                      if cfg["useCache"] else None)
        self.run = pipeline._Run(self.ctx, cfg, self.store, str(root / "cache"), {})
        self.work = work
        self.reads: list[str] = []
        real = colour_load.read_fits

        def counting(path: Any) -> Any:
            self.reads.append(os.fspath(path))
            return real(path)

        monkeypatch.setattr(colour_load, "read_fits", counting)
        self.run.load_channels()
        self.before = {k: c.current_key for k, c in self.run.chans.items()}

    # the keys a registered OSCNB group is stored under, from the members' keys before registration
    def group_key(self, ref: str = "G") -> str:
        return osc.osc_group_register_key("registerGroup", [self.before["H"], self.before["O"]],
                                          {"ref": self.before[ref], "planes": "H,O,O"})

    def split_keys(self) -> list[str]:
        key = self.group_key()
        return [osc.osc_registered_sibling_key(key, i) for i in range(2)]

    def sibling_paths(self) -> dict[str, str]:
        """The unregistered plates the loader stored, as cache paths."""
        return {k: self.store.path_for(self.before[k]) for k in self.before} if self.store else {}


def _member_ok(path: str, member: str, plane: int, master: str, original: np.ndarray) -> None:
    """The plate holds the group's plane moved by the fake registration and the master-derived provenance."""
    planes, header = read_fits(path)
    assert len(planes) == 1
    np.testing.assert_array_equal(planes[0], np.roll(original, GROUP_ROLL, axis=(0, 1)))
    assert header["FILTER"] == _FILTERS[member], (member, header.get("FILTER"))
    assert header["LOOMSRC"] == f"{os.path.basename(master)}|plane:{plane}", (member, header.get("LOOMSRC"))


def _check_nb(c: _Colour, originals: list[np.ndarray]) -> None:
    for member, plane in (("H", 0), ("O", 1)):
        _member_ok(c.run.chans[member].plate, member, plane, c.masters["OSCNB"], originals[plane])
    assert [c.run.chans[m].current_key for m in "HO"] == c.split_keys()


def _nb_planes() -> list[np.ndarray]:
    return _planes3(20, SHAPE)


def _stat(path: str) -> tuple:
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size, Path(path).read_bytes()


def _register_commands(ops: RecordingOps) -> list[str]:
    return [t for t in ops.transcript if t.startswith(("register", "rgbcomp", "link", "setref"))]


def test_a_colour_run_never_measures_and_registers_even_when_the_quality_function_raises(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kw: Any) -> Any:
        raise AssertionError("a colour run does not measure star quality")

    monkeypatch.setattr(stages_correct, "master_quality", boom)
    c = _Colour(tmp_path, monkeypatch)
    ref = c.run.register()
    assert ref == "G"
    assert "register: reference is G: no L, and no usable star measurements; G by the fixed order G > R > B > H > O" \
        in c.lines
    _check_nb(c, _nb_planes())


def test_a_mono_run_still_measures_its_masters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def measure(ctx: Any, paths: Any, cache_dir: Any, warn: Any = None) -> dict:
        calls.append(list(paths))
        return {}

    monkeypatch.setattr(stages_correct, "master_quality", measure)
    src = tmp_path / "masters"
    src.mkdir()
    paths = {ch: _write(src / f"{ch}.fits", np.random.default_rng(i).random(SHAPE)) for i, ch in enumerate("RGB")}
    cfg = loom_config.defaults()
    cfg.update({"paths": {**cfg["paths"], **paths}, "useCache": False, "outputDir": str(tmp_path / "out"),
                "gradientTool": "none"})
    work = tmp_path / "work"
    work.mkdir()
    ops = RecordingOps(tmp_path, handlers={"register": _register_handler})
    ctx = RunContext(ops=ops, work_dir=str(work), tools=_AnyTool())
    run = pipeline._Run(ctx, cfg, None, str(tmp_path / "cache"), {})
    run.load_channels()
    assert run.register() == "G"
    assert calls == [[paths["R"], paths["G"], paths["B"]]]
    assert any(t.startswith("register reg -transf=homography -interp=lanczos4") for t in ops.transcript)
    assert not any("-layer" in t for t in ops.transcript)  # a mono run keeps today's command text


def test_the_exact_command_sequence_of_an_oscnb_group_against_an_osc_reference(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _Colour(tmp_path, monkeypatch)
    sibling = c.sibling_paths()
    c.run.register()
    folder = c.work / "register-1"
    ref = sibling["G"]
    assert c.ops.transcript == [
        f'rgbcomp "{ref}" "{ref}" "{ref}" "-out={folder}/01_ref"',
        f'rgbcomp "{sibling["H"]}" "{sibling["O"]}" "{sibling["O"]}" "-out={folder}/02_group"',
        f'cd "{folder}"',
        f'link reg "-out={folder}"',
        "setref reg 1",
        "register reg -transf=homography -interp=lanczos4 -layer=0",
        f'cd "{tmp_path / "wd"}"',
    ]
    assert c.layers == [0]
    # the identity members keep their plates untouched; the group's members take the split keys
    for member in "RGB":
        assert c.run.chans[member].current_key == c.before[member]
    assert c.run.plate_of("G") == sibling["G"] and c.run.plate_of("R") == sibling["R"]
    _check_nb(c, _nb_planes())
    assert f"register: H/O -> G (colour group OSCNB, one transform)" in c.lines


def test_the_reference_group_is_identity_and_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for slots in (("OSC",), ("OSCNB",)):
        c = _Colour(tmp_path / "_".join(slots), monkeypatch, slots)
        ref = c.run.register()
        assert ref == {"OSC": "G", "OSCNB": "H"}[slots[0]]
        assert c.ops.transcript == []
        assert {k: ch.current_key for k, ch in c.run.chans.items()} == c.before
        assert c.reads == []  # not even a plate was opened


def test_a_cold_group_registration_stores_the_group_image_and_each_split(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _Colour(tmp_path, monkeypatch)
    siblings = c.sibling_paths()
    c.run.register()
    stored = {k: c.store.lookup(c.before[k]) for k in c.before}
    first = {k: _stat(p) for k, p in siblings.items() if os.path.exists(p)}
    group = c.store.load(c.group_key())
    assert group is not None and len(group[0]) == 3
    # the cache holds the registered group image, the group image carries the temporary header, not the members'
    assert group[1]["FILTER"] == "TEMPGROUP"
    for split_key, member in zip(c.split_keys(), "HO"):
        assert c.store.lookup(split_key) == c.run.chans[member].plate
        assert read_fits(c.store.lookup(split_key))[1]["FILTER"] == _FILTERS[member]
    # a plane that was handed out (the unregistered plates) is not stored again
    assert {k: _stat(p) for k, p in siblings.items() if os.path.exists(p)} == first
    assert all(stored[k] for k in stored if k in "RGBHO" and k in stored)


def test_a_warm_run_loads_the_cached_splits_and_touches_nothing(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    _Colour(tmp_path, monkeypatch).run.register()
    warm = _Colour(tmp_path, monkeypatch)
    splits = {k: _stat(warm.store.path_for(sk)) for k, sk in zip("HO", warm.split_keys())}
    group_before = _stat(warm.store.path_for(warm.group_key()))
    warm.run.register()
    assert warm.ops.transcript == [] and warm.reads == []   # no Siril command, no master opened
    _check_nb(warm, _nb_planes())
    assert {k: _stat(warm.store.path_for(sk)) for k, sk in zip("HO", warm.split_keys())} == splits  # not re-stored
    assert _stat(warm.store.path_for(warm.group_key())) == group_before
    assert any("registerGroup HIT" in ln for ln in warm.lines)


def test_a_missing_split_is_split_again_from_the_cached_group_image(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    _Colour(tmp_path, monkeypatch).run.register()
    again = _Colour(tmp_path, monkeypatch)
    h_key, o_key = again.split_keys()
    os.remove(again.store.path_for(o_key))
    kept = _stat(again.store.path_for(h_key))
    again.run.register()
    assert again.ops.transcript == [] and again.reads == []
    _check_nb(again, _nb_planes())
    assert _stat(again.store.path_for(h_key)) == kept            # the split that was cached is not stored again
    assert os.path.exists(again.store.path_for(o_key))           # the missing one is
    assert any("registerGroup HIT (split again)" in ln for ln in again.lines)


def test_a_cached_split_that_fails_to_load_falls_back_to_the_group_image(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    _Colour(tmp_path, monkeypatch).run.register()
    again = _Colour(tmp_path, monkeypatch)
    h_key, o_key = again.split_keys()
    Path(again.store.path_for(o_key)).write_bytes(b"not a fits file")
    again.run.register()
    assert again.ops.transcript == []   # the group image was enough: nothing registered again
    _check_nb(again, _nb_planes())
    assert any("would not read" in ln for ln in again.lines)


def test_a_split_with_another_masters_provenance_is_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Colour(tmp_path, monkeypatch).run.register()
    again = _Colour(tmp_path, monkeypatch)
    _h_key, o_key = again.split_keys()
    planes, header = read_fits(again.store.path_for(o_key))
    header["LOOMSRC"] = "someone-else.fits|plane:1"
    write_fits(again.store.path_for(o_key), planes, header, bitpix=-32)
    again.run.register()
    assert again.ops.transcript == []
    _check_nb(again, _nb_planes())


def test_a_cached_group_image_and_split_that_both_fail_register_again(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _Colour(tmp_path, monkeypatch).run.register()
    again = _Colour(tmp_path, monkeypatch)
    Path(again.store.path_for(again.split_keys()[0])).write_bytes(b"junk")
    Path(again.store.path_for(again.group_key())).write_bytes(b"junk")
    again.run.register()
    assert len(_register_commands(again.ops)) == 5
    _check_nb(again, _nb_planes())
    assert again.store.load(again.group_key()) is not None   # and the group image is cached again


def test_ignore_cache_registers_again_and_writes_the_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Colour(tmp_path, monkeypatch).run.register()
    again = _Colour(tmp_path, monkeypatch, ignoreCache=True)
    again.run.register()
    assert len(_register_commands(again.ops)) == 5 and again.layers == [0]
    _check_nb(again, _nb_planes())
    assert all(again.store.load(k) is not None for k in [again.group_key()] + again.split_keys())
    # the entries an ignoring run wrote serve the next run that reads the cache
    after = _Colour(tmp_path, monkeypatch)
    after.run.register()
    assert after.ops.transcript == []
    _check_nb(after, _nb_planes())


def test_cache_off_registers_every_run_and_keeps_the_plates_in_the_work_folder(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for _ in range(2):
        c = _Colour(tmp_path, monkeypatch, useCache=False)
        c.run.register()
        assert len(_register_commands(c.ops)) == 5
        for member in "HO":
            assert Path(c.run.chans[member].plate).parent == c.work
        _check_nb(c, _nb_planes())
    assert not list((tmp_path / "cache").glob("*.fits"))   # nothing was written to the cache


def test_a_split_that_cannot_be_cached_is_written_to_the_work_folder(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    c = _Colour(tmp_path, monkeypatch)
    real = c.store.store

    def refuse(key: str, planes: Any, header: Any, meta: Any) -> Any:
        return None if key in c.split_keys() else real(key, planes, header, meta)

    monkeypatch.setattr(c.store, "store", refuse)
    c.run.register()
    for member in "HO":
        assert Path(c.run.chans[member].plate).parent == c.work
    _check_nb(c, _nb_planes())
    assert any("could not be cached" in ln for ln in c.lines)


def test_the_group_leaves_no_temporary_file_or_folder_in_the_work_folder(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    c = _Colour(tmp_path, monkeypatch, useCache=False)
    c.run.register()
    names = sorted(p.name for p in c.work.rglob("*"))
    assert not any(n.startswith(("register-", "r_reg", "reg_", "cache", "01_ref", "02_group")) for n in names), names


def test_a_failed_group_registration_changes_no_member_and_stores_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _Colour(tmp_path, monkeypatch, fail={"register": RuntimeError("no stars")})
    with pytest.raises(SirilOpsError):
        c.run.register()
    assert {k: ch.current_key for k, ch in c.run.chans.items()} == c.before
    assert c.ops.workdir == str(tmp_path / "wd")
    assert c.store.lookup(c.group_key()) is None and not [c.store.lookup(k) for k in c.split_keys() if c.store.lookup(k)]
    assert not list(c.work.glob("register-*"))
