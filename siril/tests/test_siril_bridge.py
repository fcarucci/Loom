"""Tests for loomsiril.siril_bridge: the pure helpers the Siril script uses (E7).

Everything runs on synthetic arrays. The StarNet CLI is faked by a small
script in tmp_path (the real one is only run by the ``starnet`` marker).
"""

from __future__ import annotations

import json
import math
import stat
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import synth
from loomsiril import core, io_tiff
from loomsiril import siril_bridge as sb
from loomsiril.wcs import flip_rows, keyword_wcs


# ---------------------------------------------------------------- helpers

def _fits_header_from_top_down(header_top: dict, h: int) -> dict:
    """The header Siril would hold for the same sky on bottom-up rows (FITS order).

    Independent of siril_bridge: CRPIX2 is mirrored about the row centre and the
    CD terms that multiply dy (CD1_2, CD2_2) change sign; CD1_1, CD2_1 do not.
    """
    out = dict(header_top)
    out["CRPIX2"] = h + 1 - header_top["CRPIX2"]
    out["CD1_2"] = -header_top["CD1_2"]
    out["CD2_2"] = -header_top["CD2_2"]
    return out


def _asymmetric_field(w: int = 96, h: int = 64):
    """Top-down display: a marker block in the top-left quadrant and one star near the top."""
    disp = np.zeros((h, w), dtype=np.float32)
    disp[2:10, 3:13] = 1.0                       # marker: top-left quadrant of the DISPLAYED image
    yy, xx = np.mgrid[0:h, 0:w]
    star_x, star_y = 70.0, 12.0
    disp += 0.8 * np.exp(-((xx - star_x) ** 2 + (yy - star_y) ** 2) / (2 * 1.5 ** 2)).astype(np.float32)
    header_top = synth.tan_header(w, h, ra=324.745, dec=56.6, scale_arcsec=2.0, rot_deg=20.0)
    header_top["CRPIX1"] = 30.0                  # off-centre reference pixel: the CRPIX flip is visible
    header_top["CRPIX2"] = 20.0
    return disp, header_top, (star_x, star_y)


#: The fake CLI's preamble: ``--version`` answers like the real tool, then the real argument protocol is parsed.
_FAKE_PREAMBLE = (
    "import sys\n"
    "if sys.argv[1:] == ['--version']:\n"
    "    print('starnet2 version: 2.6.2'); sys.exit(0)\n"
    "args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n"
    "inp, out = args['--input'], args['--output']\n"
    "from loomsiril import io_fits\n"
)


def _fake_starnet(tmp_path: Path, name: str, body: str) -> str:
    """An executable Python script standing in for the StarNet CLI: ``--version`` and ``--input X --output Y``.

    ``body`` runs after the preamble, which has set ``inp`` and ``out`` (absolute FITS paths).
    """
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{_FAKE_PREAMBLE}{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


COPY_STARNET = (
    "planes, _ = io_fits.read_fits(inp)\n"
    "io_fits.write_fits(out, planes)\n"
)
GREY_STARNET = (
    "planes, _ = io_fits.read_fits(inp)\n"
    "if len(planes) == 3: planes = [(planes[0] + planes[1] + planes[2]) / 3]\n"
    "io_fits.write_fits(out, planes)\n"
)
RGB_STARNET = (
    "planes, _ = io_fits.read_fits(inp)\n"
    "io_fits.write_fits(out, [planes[0], planes[0], planes[0] * 0.5])\n"
)


# ---------------------------------------------------------------- pixel data

def test_planes_uint16_colour_scaled_and_flipped_to_top_down():
    raw = np.zeros((3, 4, 5), dtype=np.uint16)
    raw[:, 0, :] = 65535                           # Siril row 0 is the BOTTOM row: white
    planes = sb.planes_from_pixeldata(raw)
    assert len(planes) == 3
    for p in planes:
        assert p.dtype == np.float32 and p.shape == (4, 5)
        assert p[3, 0] == pytest.approx(1.0)      # ... and it is the last row of the top-down plane
        assert p[0, 0] == 0.0


def test_planes_uint8_mono_and_float_kept_as_is():
    mono8 = np.arange(12, dtype=np.uint8).reshape(3, 4)
    (p,) = sb.planes_from_pixeldata(mono8)
    assert p.shape == (3, 4) and p.dtype == np.float32
    assert p[0, 0] == pytest.approx(8 / 255)        # Siril's last row (8..11) is the top row
    assert p.max() == pytest.approx(11 / 255)
    f64 = np.full((2, 2), 0.25, dtype=np.float64)
    (q,) = sb.planes_from_pixeldata(f64)
    assert q.dtype == np.float32 and np.all(q == np.float32(0.25))


def test_planes_reject_unknown_dtype_and_rank():
    with pytest.raises(ValueError):
        sb.planes_from_pixeldata(np.zeros((2, 2), dtype=np.int32))
    with pytest.raises(ValueError):
        sb.planes_from_pixeldata(np.zeros((2, 2, 2, 2), dtype=np.float32))


# ---------------------------------------------------------------- header, WCS, target

def test_wcs_from_header_reads_tan_keywords_and_none_when_missing():
    disp, header_top, _ = _asymmetric_field()
    w = sb.wcs_from_header(header_top)
    assert w == synth.wcs_from_header(header_top)
    assert sb.wcs_from_header({"CRVAL1": 1.0}) is None
    # Siril hands numbers as strings with quotes sometimes: keyword_number copes
    assert sb.wcs_from_header({**header_top, "CRVAL1": "'324.745'"})["crval1"] == pytest.approx(324.745)


def test_top_down_wcs_is_flip_rows_of_the_fits_wcs():
    disp, header_top, _ = _asymmetric_field()
    h = disp.shape[0]
    header_fits = _fits_header_from_top_down(header_top, h)
    got = sb.top_down_wcs(header_fits, h)
    assert got == flip_rows(keyword_wcs(header_fits), h)
    assert got["crpix2"] == pytest.approx(header_top["CRPIX2"])      # two flips give the original
    assert got["cd"] == pytest.approx(synth.wcs_from_header(header_top)["cd"])


def _siril_style_header(header_top: dict, h: int) -> dict:
    """The header Siril 1.4.4 hands out after plate solving: CDELT1/2 + PC1_1..PC2_2, no CD keys.

    The FITS (bottom-up) WCS of the same sky, written as CD = CDELT_i * PC_ij (what siril-cli 1.4.4 gave back for a
    30 degree rotated CD matrix: PC 0.866/-0.5/0.5/0.866, CDELT -0.001/0.001).
    """
    fits = _fits_header_from_top_down(header_top, h)
    cd = [fits["CD1_1"], fits["CD1_2"], fits["CD2_1"], fits["CD2_2"]]
    cdelt1, cdelt2 = -0.001, 0.001
    out = {k: v for k, v in fits.items() if not k.startswith("CD")}
    out.update({"CDELT1": cdelt1, "CDELT2": cdelt2,
                "PC1_1": cd[0] / cdelt1, "PC1_2": cd[1] / cdelt1,
                "PC2_1": cd[2] / cdelt2, "PC2_2": cd[3] / cdelt2})
    return out


def test_siril_pc_header_gives_the_rotated_wcs_through_the_bridge():
    disp, header_top, (sx, sy) = _asymmetric_field()
    h = disp.shape[0]
    header_top = dict(header_top)
    header_top.update(synth.tan_header(disp.shape[1], h, ra=324.745, dec=56.6, scale_arcsec=3.6, rot_deg=30.0))
    header_top["CRPIX1"], header_top["CRPIX2"] = 30.0, 20.0
    siril_header = _siril_style_header(header_top, h)
    assert not any(k in siril_header for k in ("CD1_1", "CD1_2", "CD2_1", "CD2_2"))
    assert abs(siril_header["PC1_2"]) == pytest.approx(0.5) and abs(siril_header["PC2_1"]) == pytest.approx(0.5)

    truth = synth.wcs_from_header(header_top)                 # top-down ground truth, rotated
    fits_cd = [truth["cd"][0], -truth["cd"][1], truth["cd"][2], -truth["cd"][3]]
    assert sb.wcs_from_header(siril_header)["cd"] == pytest.approx(fits_cd, rel=1e-9)   # CDELT*PC, bottom-up
    bridged = sb.bridge_image(disp[::-1].copy(), siril_header, psf_stars=None, icc=None)
    assert bridged.wcs["cd"] == pytest.approx(truth["cd"], rel=1e-9)
    assert bridged.wcs["crpix2"] == pytest.approx(truth["crpix2"])
    ra, dec = synth.tan_unproject(sx, sy, truth)
    px, py = core.tan_project(ra, dec, bridged.wcs)
    assert px == pytest.approx(sx, abs=1e-6) and py == pytest.approx(sy, abs=1e-6)
    assert "PC1_1" not in bridged.header and "CDELT1" not in bridged.header   # no stale keys left behind
    assert bridged.header["CD1_2"] == pytest.approx(truth["cd"][1], rel=1e-9)
    assert sb.wcs_from_header(bridged.header) == bridged.wcs


def test_siril_pc_header_reaches_the_job(fake_pipeline):
    """job_from_dialog takes the WCS from the Siril-style header (no CD keys), as the script does."""
    disp, header_top, _ = _asymmetric_field()
    h = disp.shape[0]
    siril_header = _siril_style_header(header_top, h)
    b = sb.bridge_image(disp[::-1].copy(), siril_header, psf_stars=None, icc=None)
    job = sb.job_from_dialog({"starless": [disp], "stars": [disp]}, b.planes, b.header, None)
    assert job.wcs == sb.wcs_from_header(header_top)


def test_target_from_header_is_header_centre_ra_dec():
    disp, header_top, _ = _asymmetric_field()
    t = sb.target_from_header(header_top)
    assert t == {"ra": pytest.approx(324.745), "dec": pytest.approx(56.6)}
    assert sb.target_from_header({}) is None


# ---------------------------------------------------------------- orientation

def test_orientation_asymmetric(tmp_path):
    """Bottom-up Siril data + FITS header in; top-down planes, WCS and starless out, all aligned."""
    disp, header_top, (sx, sy) = _asymmetric_field()
    h, w = disp.shape
    truth_wcs = synth.wcs_from_header(header_top)            # top-down ground truth
    ra, dec = synth.tan_unproject(sx, sy, truth_wcs)         # a Gaia star on the star's top-down pixel
    siril_pixels = disp[::-1].copy()                         # what Siril holds: row 0 is the bottom
    header_fits = _fits_header_from_top_down(header_top, h)

    # Siril's PSF coordinates (measured on siril-cli 1.4.4): xpos = col + 0.5, ypos = distance from the TOP + 0.5
    psf = [SimpleNamespace(xpos=sx + 0.5, ypos=sy + 0.5, A=0.8, fwhmx=3.0, fwhmy=3.0),
           SimpleNamespace(xpos=7.5, ypos=5.5, A=1.0, fwhmx=3.0, fwhmy=3.0)]   # on the marker
    bridged = sb.bridge_image(siril_pixels, header_fits, psf_stars=psf, icc=None)

    # 1. marker: top-left of the displayed image is top-left of the planes
    plane = bridged.planes[0]
    assert plane.shape == (h, w)
    assert plane[2:10, 3:13].min() == pytest.approx(1.0)
    assert plane[-10:, -13:].max() < 0.5                      # the bottom-right corner is empty
    assert np.unravel_index(np.argmax(plane), plane.shape)[0] < h // 2
    assert np.array_equal(plane, disp)                        # the bridge gives the displayed image back exactly

    # 2. a Gaia star lands on its star with the bridged (top-down) WCS
    px, py = core.tan_project(ra, dec, bridged.wcs)
    assert px == pytest.approx(sx, abs=1e-6) and py == pytest.approx(sy, abs=1e-6)
    # ... and the raw FITS WCS would NOT (the flip is needed, not decorative)
    qx, qy = core.tan_project(ra, dec, keyword_wcs(header_fits))
    assert abs(qy - sy) > 1.0

    # 2b. PSF stars land on the top-down pixels, not mirrored (brightest first: the marker is the brighter one)
    got = {(round(d.x), round(d.y)) for d in bridged.detections}
    assert got == {(70, 12), (7, 5)}
    d0 = bridged.detections[0]
    assert plane[int(round(d0.y)), int(round(d0.x))] > 0.5                  # the bright one sits on the marker

    # 3. a starless plane round-tripping through the (fake) StarNet TIFF is pixel-aligned
    binary = _fake_starnet(tmp_path, "starnet_copy", COPY_STARNET)
    starless = sb.run_starnet(binary, bridged.planes, tmp_path / "work")
    assert np.allclose(starless[0], plane, atol=1.5 / 65535)
    assert np.unravel_index(np.argmax(starless[0]), plane.shape) == np.unravel_index(np.argmax(plane), plane.shape)


def test_detections_observed_siril_coordinates():
    """Measured on siril-cli 1.4.4: a star at array row 90 of 100 (bottom-up), col 30 -> xpos 30.4989, ypos 9.5005.

    Siril's ypos is measured from the top of its display (edge convention), so the top-down row is 9.
    """
    stars = [SimpleNamespace(xpos=30.4989, ypos=9.5005, A=0.8, fwhmx=3.0, fwhmy=3.0)]
    (d,) = sb.detections_from_psf(stars)
    assert d.x == pytest.approx(29.9989, abs=1e-6)
    assert d.y == pytest.approx(9.0005, abs=1e-6)
    assert (round(d.x), round(d.y)) == (30, 9)


# ---------------------------------------------------------------- PSF stars

def test_detections_from_psf_order_rect_and_skip_non_finite():
    stars = [
        SimpleNamespace(xpos=11.0, ypos=21.0, A=0.2, fwhmx=4.0, fwhmy=4.0),
        SimpleNamespace(xpos=51.0, ypos=41.0, A=0.9, fwhmx=4.0, fwhmy=6.0),
        SimpleNamespace(xpos=float("nan"), ypos=1.0, A=0.5, fwhmx=4.0, fwhmy=4.0),
    ]
    dets = sb.detections_from_psf(stars)
    assert [d.index for d in dets] == [0, 1]
    assert dets[0].flux == pytest.approx(0.9) and dets[1].flux == pytest.approx(0.2)   # brightest first
    d = dets[0]
    assert (d.x, d.y) == (pytest.approx(50.5), pytest.approx(40.5))
    # area pi*(fwhm/2)^2 with fwhm = mean(4, 6) = 5 -> r = 2.5 -> rounds half up to 3 (core.detection_rect)
    assert d.rect == core.detection_rect(d.x, d.y, math.pi * 2.5 ** 2)
    assert d.nmax == 0 and d.from_gaia is False
    assert sb.detections_from_psf(None) == []


# ---------------------------------------------------------------- StarNet

def test_starnet_args_are_pinned(tmp_path):
    """The StarNet2 CLI: --input and --output only (its own unscreen is not requested; see siril_bridge)."""
    binary = str(tmp_path / "starnet2")
    inp, out = tmp_path / "in.fits", tmp_path / "out.fits"
    assert sb.starnet_args(binary, inp, out) == [binary, "--input", str(inp), "--output", str(out)]


def test_run_starnet_invocation_is_input_output_and_no_unscreen(tmp_path):
    """The binary is called with exactly --input and --output, both absolute FITS paths in the work folder."""
    record = tmp_path / "argv.json"
    binary = _fake_starnet(tmp_path, "starnet2", "import json\n"
                           f"json.dump(sys.argv[1:], open({str(record)!r}, 'w'))\n" + COPY_STARNET)
    sb.run_starnet(binary, [np.full((4, 5), 0.5, dtype=np.float32)], tmp_path / "work")
    argv = json.loads(record.read_text(encoding="utf-8"))
    assert argv[0::2] == ["--input", "--output"] and len(argv) == 4
    assert Path(argv[1]).is_absolute() and Path(argv[3]).is_absolute()
    assert Path(argv[1]).name == "starnet_in.fits" and Path(argv[3]).name == "starnet_out.fits"
    assert "--unscreen" not in argv and "-n" not in argv


def test_starnet_version_missing_binary_is_a_clear_error(tmp_path):
    with pytest.raises(sb.StarnetError, match="cannot run"):
        sb.run_starnet(str(tmp_path / "absent"), [np.zeros((4, 4), dtype=np.float32)], tmp_path / "t")


def test_starnet_version_broken_binary_is_a_clear_error(tmp_path):
    binary = _fake_starnet(tmp_path, "starnet2", "")
    broken = tmp_path / "broken"
    broken.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('no weights'); sys.exit(7)\n",
                      encoding="utf-8")
    broken.chmod(broken.stat().st_mode | stat.S_IXUSR)
    assert sb.starnet_version(binary) == "starnet2 version: 2.6.2"
    with pytest.raises(sb.StarnetError, match="--version.*7.*no weights"):
        sb.starnet_version(str(broken))
    with pytest.raises(sb.StarnetError, match="--version"):
        sb.run_starnet(str(broken), [np.zeros((4, 4), dtype=np.float32)], tmp_path / "t")


def test_find_starnet_env_then_path(tmp_path, monkeypatch):
    fake = _fake_starnet(tmp_path, "starnet2", COPY_STARNET)
    monkeypatch.setenv("LOOMSIRIL_STARNET", fake)
    assert sb.find_starnet() == fake
    empty = tmp_path / "empty_bin"
    empty.mkdir()
    monkeypatch.setenv("LOOMSIRIL_STARNET", str(tmp_path / "missing"))
    monkeypatch.setenv("PATH", str(empty))                    # no starnet2/starnet++ here
    assert sb.find_starnet() is None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_starnet(bin_dir, "starnet++", COPY_STARNET)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert sb.find_starnet() == str(bin_dir / "starnet++")


def test_run_starnet_copy_is_aligned(tmp_path):
    rng = np.random.default_rng(3)
    planes = [rng.random((20, 30), dtype=np.float32) for _ in range(3)]
    binary = _fake_starnet(tmp_path, "starnet2", COPY_STARNET)
    out = sb.run_starnet(binary, planes, tmp_path / "t")
    assert len(out) == 3
    for a, b in zip(out, planes):
        assert a.dtype == np.float32 and a.shape == b.shape
        assert np.allclose(a, b, atol=1.5 / 65535)


def test_run_starnet_grey_output_for_colour_input_is_replicated(tmp_path):
    planes = [np.full((8, 9), v, dtype=np.float32) for v in (0.2, 0.4, 0.6)]
    binary = _fake_starnet(tmp_path, "starnet2", GREY_STARNET)
    out = sb.run_starnet(binary, planes, tmp_path / "t")
    assert len(out) == 3
    for p in out:
        assert np.allclose(p, 0.4, atol=2 / 65535)


def test_run_starnet_colour_output_for_grey_input_is_averaged(tmp_path):
    planes = [np.full((8, 9), 0.5, dtype=np.float32)]
    binary = _fake_starnet(tmp_path, "starnet2", RGB_STARNET)   # output (R, G, B/2) = (0.5, 0.5, 0.25)
    out = sb.run_starnet(binary, planes, tmp_path / "t")
    assert len(out) == 1
    assert np.allclose(out[0], (0.5 + 0.5 + 0.25) / 3, atol=2 / 65535)


def test_run_starnet_runs_in_the_binary_directory(tmp_path):
    """Weights are looked up next to the binary (the StarNet2 zip layout), so the CLI runs from its own folder."""
    bin_dir = tmp_path / "starnet"
    bin_dir.mkdir()
    (bin_dir / "weights.pt").write_text("weights", encoding="utf-8")
    binary = _fake_starnet(bin_dir, "starnet2",
                           "import os\n"
                           "if not os.path.exists('weights.pt'): sys.exit(5)\n" + COPY_STARNET)
    out = sb.run_starnet(binary, [np.full((4, 4), 0.5, dtype=np.float32)], tmp_path / "work")
    assert np.allclose(out[0], 0.5, atol=2 / 65535)


def test_run_starnet_removes_its_fits_files_on_success_and_on_error(tmp_path):
    ok = _fake_starnet(tmp_path, "ok", COPY_STARNET)
    sb.run_starnet(ok, [np.full((4, 5), 0.5, dtype=np.float32)], tmp_path / "work")
    assert list((tmp_path / "work").iterdir()) == []
    bad = _fake_starnet(tmp_path, "bad", "sys.exit(3)")
    with pytest.raises(sb.StarnetError):
        sb.run_starnet(bad, [np.zeros((4, 4), dtype=np.float32)], tmp_path / "work")
    assert list((tmp_path / "work").iterdir()) == []


def test_run_starnet_refuses_a_starnet_older_than_the_minimum(tmp_path):
    old = tmp_path / "old"
    old.write_text(f"#!{sys.executable}\nprint('starnet2 version: 2.5.2')\n", encoding="utf-8")
    old.chmod(old.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(sb.StarnetError, match="2.5.2 is too old: Loom needs 2.6.2 or newer"):
        sb.run_starnet(str(old), [np.zeros((4, 4), dtype=np.float32)], tmp_path / "work")


def test_run_starnet_default_temp_folder_is_removed(tmp_path, monkeypatch):
    root = tmp_path / "tmproot"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    binary = _fake_starnet(tmp_path, "starnet2", COPY_STARNET)
    sb.run_starnet(binary, [np.full((4, 4), 0.5, dtype=np.float32)])
    bad = _fake_starnet(tmp_path, "starnet3", "sys.exit(3)")
    with pytest.raises(sb.StarnetError):
        sb.run_starnet(bad, [np.zeros((4, 4), dtype=np.float32)])
    assert list(root.iterdir()) == []


def test_run_starnet_timeout_stops_the_run_and_reports_it(tmp_path):
    """A CLI that hangs (sleeps) is stopped after the timeout and reported; the real limit is one hour."""
    sleepy = _fake_starnet(tmp_path, "starnet2", "import time\ntime.sleep(60)\n")
    with pytest.raises(sb.StarnetError, match="did not finish within 0.5 s and was stopped"):
        sb.run_starnet(sleepy, [np.zeros((4, 4), dtype=np.float32)], tmp_path / "t", timeout=0.5)
    assert sb.STARNET_TIMEOUT_S == 3600.0


def test_run_starnet_failure_is_an_error(tmp_path):
    binary = _fake_starnet(tmp_path, "starnet2", "import sys\nsys.stderr.write('bad input')\nsys.exit(3)")
    with pytest.raises(sb.StarnetError, match="exited with 3.*bad input"):
        sb.run_starnet(binary, [np.zeros((4, 4), dtype=np.float32)], tmp_path / "t")


def _window(a: np.ndarray, y: int, x: int, r: int = 4) -> np.ndarray:
    """The (2r+1)^2 window around (y, x), clipped at the array edge."""
    return a[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]


def _peak_offset(a: np.ndarray, y: int, x: int, r: int = 4) -> tuple[int, int]:
    """Where the window's maximum lies, relative to (y, x): (dy, dx)."""
    win = _window(a, y, x, r)
    iy, ix = np.unravel_index(int(np.argmax(win)), win.shape)
    return (max(0, y - r) + int(iy) - y, max(0, x - r) + int(ix) - x)


@pytest.mark.starnet
@pytest.mark.parametrize("nc", [1, 3])
def test_real_starnet_cli_on_synthetic_image(tmp_path, nc):
    """The real StarNet2 CLI on a synthetic 512 x 512 field (its minimum size), checked on its RAW output.

    The bridge's split (starless clamped to the image, stars by unscreen) would make starless <= image and
    screen(starless, stars) == image true whatever StarNet did, so those are not asserted here. The checks are:
    the run works and leaves no FITS files behind; the raw output stays near the image; the stars the image has
    are mostly gone; and the removed light sits at the injected star positions, not mirrored. Bounds are measured
    on this field, not tuned to StarNet's output, and no StarNet output is stored.
    """
    binary = sb.find_starnet()
    assert binary is not None
    field = synth.make_field(w=512, h=512, n_stars=60, nc=nc, seed=2)
    image = field.image
    work = tmp_path / "t"
    raw = sb.run_starnet(binary, image, work)
    assert len(raw) == nc and all(p.shape == (512, 512) and p.dtype == np.float32 for p in raw)
    assert list(work.iterdir()) == []                          # the FITS in and out are removed after the run

    # 1. overshoot: StarNet's float output may exceed the image a little (8.3e-3 on this field); 2e-2 is the bound
    overshoot = max(float(np.max(r - i)) for r, i in zip(raw, image))
    assert overshoot <= 2e-2
    # 2. star light removed: where the stars layer has light, at least half of it is gone from the starless output
    for c in range(nc):
        removed = np.clip(image[c] - raw[c], 0.0, None)
        mask = field.stars[c] > 0.05
        assert removed[mask].sum() >= 0.5 * field.stars[c][mask].sum()
    # 3. orientation, channel 0, bright stars: the removed light peaks at the injected position (median offset
    #    <= 1.5 px) and is larger there than at the vertically mirrored position for at least 90 % of them
    light = np.clip(image[0] - raw[0], 0.0, None)
    bright = [s for s in field.truth if s.peak >= 0.4]
    assert len(bright) >= 5
    offsets, mirrored_ok = [], 0
    for s in bright:
        y0, x0 = int(round(s.y)), int(round(s.x))
        dy, dx = _peak_offset(light, y0, x0)
        offsets.append(np.hypot(dy, dx))
        mirrored_ok += _window(light, y0, x0).sum() > _window(light, 511 - y0, x0).sum()
    assert float(np.median(offsets)) <= 1.5
    assert mirrored_ok >= 0.9 * len(bright)


# ---------------------------------------------------------------- split_stars

def test_split_stars_recovers_the_stars_layer():
    field = synth.make_field(w=48, h=32, n_stars=6, nc=1, seed=5)
    image = field.image
    starless, stars = sb.split_stars(image, field.starless)
    assert np.allclose(stars[0], field.stars[0], atol=2e-5)
    assert np.all(starless[0] <= image[0] + 1e-7)


def test_split_stars_clamps_starless_to_image():
    image = [np.full((2, 2), 0.3, dtype=np.float32)]
    starless_brightened = [np.full((2, 2), 0.6, dtype=np.float32)]
    starless, stars = sb.split_stars(image, starless_brightened)
    assert np.allclose(starless[0], 0.3)                     # min(starless, image)
    assert np.all(stars[0] >= 0) and np.all(stars[0] <= 1)


# ---------------------------------------------------------------- read layer files

def test_read_layer_tiff_and_fits_are_top_down(tmp_path):
    plane = np.zeros((6, 7), dtype=np.float32)
    plane[0, 0] = 1.0                                       # top-left of a top-down layer
    tif = tmp_path / "starless.tif"
    io_tiff.write_tiff16(tif, [plane])
    got = sb.read_layer(tif)
    assert len(got) == 1 and got[0][0, 0] == pytest.approx(1.0, abs=1 / 65535)
    with pytest.raises(ValueError):
        sb.read_layer(tmp_path / "layer.png")


# ---------------------------------------------------------------- bridge_image

def test_bridge_image_returns_top_down_planes_wcs_detections_and_target():
    disp, header_top, _ = _asymmetric_field()
    h = disp.shape[0]
    header_fits = _fits_header_from_top_down(header_top, h)
    stars = [SimpleNamespace(xpos=5.5, ypos=3.5, A=0.4, fwhmx=3.0, fwhmy=3.0)]
    b = sb.bridge_image((disp[::-1] * 65535).astype(np.uint16), header_fits, stars, icc=b"icc")
    assert b.height == h and b.width == disp.shape[1]
    assert b.icc == b"icc"
    assert b.target == sb.target_from_header(header_top)
    assert (b.detections[0].x, b.detections[0].y) == (pytest.approx(5.0), pytest.approx(3.0))
    assert b.header["CRPIX2"] == pytest.approx(header_top["CRPIX2"])
    assert sb.wcs_from_header(b.header) == b.wcs


# ---------------------------------------------------------------- job_from_dialog

@pytest.fixture
def fake_pipeline(monkeypatch):
    """A stand-in for loomsiril.pipeline (E6 not merged yet): FlyJob with the frozen fields."""
    mod = types.ModuleType("loomsiril.pipeline")
    from dataclasses import dataclass, field

    @dataclass
    class FlyJob:
        starless: list
        stars: list
        wcs: dict
        target: object = None
        target_type: str = "nebula"
        distance: object = None
        gaia: str = "cache"
        presets: list = field(default_factory=lambda: ["youtube_1080"])
        duration: float = 8.0
        fps: int = 30
        loop: str = "none"
        orientation: str = "landscape"
        star_quality: str = "high"
        quality: str = "high"
        dynamic: str = "sdr"
        video: bool = False
        music_path: str = ""
        music_fade: bool = True
        ffmpeg: object = None
        format: object = None
        icc: object = None
        detections: object = None
        object_name: str = ""
        fwhm: object = None
        options: dict = field(default_factory=dict)
        out_dir: str = "."
        logo_path: str = ""
        logo_place: str = "off"
        logo_opacity: float = 1.0
        logo_delay: float = 0.0

    mod.FlyJob = FlyJob
    monkeypatch.setitem(sys.modules, "loomsiril.pipeline", mod)
    return mod


def test_job_from_dialog_builds_the_frozen_flyjob(fake_pipeline):
    disp, header_top, _ = _asymmetric_field()
    h = disp.shape[0]
    starless = [np.full(disp.shape, 0.1, dtype=np.float32)]
    stars = [np.full(disp.shape, 0.2, dtype=np.float32)]
    values = {
        "starless": starless, "stars": stars, "target": None, "target_type": "galaxy",
        "distance": None, "gaia": "cache", "presets": ["social_vertical"], "duration": 6.0, "fps": 25,
        "loop": "pingpong", "star_quality": "medium", "dynamic": "hdr", "video": True, "out_dir": "/x",
        "music_path": "/m/a.mp3", "music_fade": False,
    }
    job = sb.job_from_dialog(values, [disp], header_top, b"icc")
    assert isinstance(job, fake_pipeline.FlyJob)
    assert job.starless[0] is starless[0] and job.stars[0] is stars[0]
    assert job.wcs == sb.wcs_from_header(header_top)
    assert job.target == sb.target_from_header(header_top)           # None target -> header centre
    assert (job.target_type, job.presets, job.duration, job.fps, job.loop) == ("galaxy", ["social_vertical"], 6.0, 25, "pingpong")
    assert (job.star_quality, job.dynamic, job.video, job.out_dir, job.icc) == ("medium", "hdr", True, "/x", b"icc")
    assert job.distance is None and job.detections is None
    assert (job.music_path, job.music_fade) == ("/m/a.mp3", False)


def test_job_from_dialog_splits_stars_when_only_starless_given(fake_pipeline):
    disp, header_top, _ = _asymmetric_field()
    field_ = synth.make_field(w=disp.shape[1], h=disp.shape[0], n_stars=5, nc=1, seed=9)
    job = sb.job_from_dialog({"starless": field_.starless}, field_.image, header_top, None)
    assert np.allclose(job.stars[0], field_.stars[0], atol=2e-5)


def test_job_from_dialog_needs_a_plate_solution(fake_pipeline):
    disp, _, _ = _asymmetric_field()
    with pytest.raises(ValueError, match="plate"):
        sb.job_from_dialog({"starless": [disp]}, [disp], {}, None)


# ---------------------------------------------------------------- options JSON

def test_options_round_trip_in_tmp_path(tmp_path):
    values = {"target_type": "galaxy", "presets": ["youtube_4k"], "duration": 12.5, "fps": 60,
              "starless": [np.zeros((2, 2))], "video": True}
    path = sb.save_options(values, tmp_path / "cfg")
    assert path == sb.options_path(tmp_path / "cfg")
    assert path.name == "loomsiril-fly.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "starless" not in data                              # arrays never go in the options file
    assert sb.load_options(tmp_path / "cfg") == {"target_type": "galaxy", "presets": ["youtube_4k"],
                                                 "duration": 12.5, "fps": 60, "video": True}


def test_options_missing_or_corrupt_give_empty(tmp_path):
    assert sb.load_options(tmp_path / "none") == {}
    cfg = tmp_path / "bad"
    cfg.mkdir()
    (cfg / "loomsiril-fly.json").write_text("{not json", encoding="utf-8")
    assert sb.load_options(cfg) == {}
    (cfg / "loomsiril-fly.json").write_text("[1, 2]", encoding="utf-8")
    assert sb.load_options(cfg) == {}
