"""The synthetic masters (tests/loom/synth.py) are what they claim: deterministic, stars where the WCS says."""

from __future__ import annotations

import numpy as np

from loomsiril.io_fits import read_fits

from . import synth


def _centroid(img: np.ndarray, x: float, y: float, r: int = 4) -> tuple[float, float]:
    xi, yi = int(round(x)), int(round(y))
    win = img[yi - r:yi + r + 1, xi - r:xi + r + 1].astype(np.float64)
    win = np.clip(win - np.median(img[yi - 3 * r:yi + 3 * r + 1, xi - 3 * r:xi + 3 * r + 1]), 0, None)
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return float((xx * win).sum() / win.sum() + xi), float((yy * win).sum() / win.sum() + yi)


def test_same_arguments_give_identical_masters() -> None:
    a, b = synth.make_master("R", seed=3), synth.make_master("R", seed=3)
    assert np.array_equal(a.planes[0], b.planes[0]) and a.header == b.header
    assert not np.array_equal(a.planes[0], synth.make_master("R", seed=4).planes[0])  # noise follows the seed


def test_header_carries_filter_instrument_solve_hints_and_wcs() -> None:
    m = synth.make_master("H")
    h = m.header
    assert h["FILTER"] == "Ha" and h["INSTRUME"] == "ZWO ASI2600MM Pro" and h["IMAGETYP"] == "Light"
    assert h["XPIXSZ"] == synth.NATIVE_PIXEL_UM and h["RA"] == synth.RA0 and h["FOCALLEN"] > 0
    assert h["CTYPE1"] == "RA---TAN" and "CD1_1" in h
    bare = synth.make_master("R", with_wcs=False, instrume=None, filter_name="")
    assert "CTYPE1" not in bare.header and "INSTRUME" not in bare.header and "FILTER" not in bare.header
    assert m.planes[0].dtype == np.float32 and m.shape == (512, 640)
    assert 0.0 <= float(m.planes[0].min()) and float(m.planes[0].max()) <= 1.0


def test_stars_sit_where_the_wcs_puts_them() -> None:
    m = synth.make_master("L", noise=0.0)
    img = m.planes[0]
    h, w = img.shape
    errs = [np.hypot(*np.subtract(_centroid(img, x, y), (x, y)))
            for x, y, p in m.stars if p > 0.3 and 12 <= x < w - 12 and 12 <= y < h - 12]
    assert len(errs) > 20
    assert float(np.median(errs)) < 0.05


def test_shifted_and_drizzled_masters_show_the_same_sky() -> None:
    ref = synth.make_master("L")
    drz = synth.make_master("R", drizzle=2, shift=(3.0, -2.0))
    assert drz.shape == (2 * ref.shape[0], 2 * ref.shape[1])
    assert drz.header["XPIXSZ"] == synth.NATIVE_PIXEL_UM / 2
    assert abs(drz.header["CD1_1"] * 2 - ref.header["CD1_1"]) < 1e-15
    # the same catalogue star maps into both frames through their own WCS
    cat = synth.sky_catalogue()
    p_ref = synth.sky_to_pixel(ref.header, cat[:5, 0], cat[:5, 1])
    p_drz = synth.sky_to_pixel(drz.header, cat[:5, 0], cat[:5, 1])
    expect = (p_ref + 0.5) * 2 - 0.5 + np.array([3.0 * 2, -2.0 * 2])  # 1-based centre scaling, then the shift
    assert np.allclose(p_drz, expect, atol=1e-6)


def test_gradient_and_blob_are_added() -> None:
    flat = synth.make_master("L", noise=0.0)
    grad = synth.make_master("L", noise=0.0, gradient=(0.04, 0.0, 0.0), blob=(100, 100, 10, 0.02))
    diff = grad.planes[0].astype(np.float64) - flat.planes[0]
    assert diff[:, -5:].mean() - diff[:, :5].mean() > 0.035
    assert abs(diff[100, 100] - 0.04 * (100 / 640 - 0.5) - 0.02) < 1e-5  # gradient term + blob peak


def test_written_master_round_trips_through_io_fits(tmp_path) -> None:
    m = synth.make_master("G", w=64, h=48)
    p = synth.write_master(tmp_path / "g.fits", m)
    planes, hdr = read_fits(p)
    assert np.array_equal(planes[0], m.planes[0])
    for k in ("CRPIX1", "CRPIX2", "CD1_1", "CD1_2", "CD2_1", "CD2_2", "FILTER", "XPIXSZ"):
        assert hdr[k] == m.header[k] if isinstance(m.header[k], str) else abs(hdr[k] - m.header[k]) < 1e-9, k


def test_master_set_has_one_master_per_channel_with_distinct_offsets() -> None:
    s = synth.make_master_set(("L", "R", "G"), w=64, h=48)
    assert list(s) == ["L", "R", "G"]
    assert s["L"].header["CRPIX1"] != s["R"].header["CRPIX1"]
