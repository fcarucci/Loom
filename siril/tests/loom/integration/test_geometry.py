"""Geometry stages against a real siril-cli (marker ``siril``): registration, the common crop and halo matching.

Registration: four synthetic masters (one of them drizzled 2x) are registered onto the reference; the reference comes
back bit-identical, every output has the reference's size, and bright stars sit within 0.1 px of their true reference
positions. The common crop equals the numpy intersection of the valid rectangles, and the cropped WCS still maps a
catalogue star onto its centroid within 0.1 px. Halo matching blurs the narrower channel (fwhm 3.0) to the wider one
(fwhm 4.0): the sigmas agree within 5% and the reference channel is left alone.
"""

from __future__ import annotations

import numpy as np
import pytest

from loomsiril.io_fits import read_fits
from loomsiril.loom import imageops

from .. import synth
from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

REG_SCRIPT = r'''
import json
import sirilpy as s
from loomsiril.loom.siril_ops import SirilCommandOps
from loomsiril.loom.stages_geometry import common_crop_rect, crop_plate, register_channels

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = "ROOT"
plates = PLATES
reg = register_channels(ops, plates, "L", root, siril.log)
rect = common_crop_rect(reg, "L")
cropped = {}
for ch in reg:
    cropped[ch] = crop_plate(ops, reg[ch], root + "/" + ch + "_crop.fits", rect)
print("RESULT=" + json.dumps({"reg": reg, "rect": list(rect), "cropped": cropped}), flush=True)
'''

HALO_SCRIPT = r'''
import json
import sirilpy as s
from loomsiril.loom.siril_ops import SirilCommandOps
from loomsiril.loom.stages_geometry import match_halos, measure_psf_sigma

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = "ROOT"
plates = PLATES
before = {ch: measure_psf_sigma(ops, plates[ch], siril.log) for ch in plates}
out = match_halos(ops, plates, root, siril.log)
after = {ch: measure_psf_sigma(ops, out[ch], siril.log) for ch in plates}
print("RESULT=" + json.dumps({"out": out, "before": before, "after": after}), flush=True)
'''


def _centroid(img: np.ndarray, x: float, y: float, r: int = 5) -> tuple[float, float]:
    """Flux-weighted centroid of the background-subtracted window around (x, y)."""
    xi, yi = int(round(x)), int(round(y))
    win = img[yi - r:yi + r + 1, xi - r:xi + r + 1].astype(np.float64)
    weight = np.clip(win - np.median(win), 0.0, None)
    yy, xx = np.mgrid[yi - r:yi + r + 1, xi - r:xi + r + 1]
    total = weight.sum()
    return float((weight * xx).sum() / total), float((weight * yy).sum() / total)


def _bright_inside(stars: np.ndarray, w: int, h: int, margin: int = 20, peak: float = 0.3) -> np.ndarray:
    keep = (stars[:, 2] > peak) & (stars[:, 0] > margin) & (stars[:, 0] < w - margin) \
        & (stars[:, 1] > margin) & (stars[:, 1] < h - margin)
    return stars[keep]


def test_registration_residual_reference_identity_sizes_crop_and_wcs(tmp_path) -> None:
    masters = synth.make_master_set(("L", "R", "G"))
    masters["B"] = synth.make_master("B", drizzle=2, shift=(3.1, -2.2), rotation_deg=0.3, seed=4)
    paths = {ch: str(synth.write_master(tmp_path / f"{ch}.fits", m)) for ch, m in masters.items()}
    script = REG_SCRIPT.replace("ROOT", str(tmp_path)).replace("PLATES", repr(paths))

    res = run_pyscript(script, tmp_path).check().value("RESULT")

    ref = masters["L"]
    h, w = ref.shape
    ref_in = read_fits(paths["L"])[0][0]
    ref_out = read_fits(res["reg"]["L"])[0][0]
    np.testing.assert_array_equal(ref_out, ref_in)

    outs = {}
    for ch in masters:
        planes, _ = read_fits(res["reg"][ch])
        assert planes[0].shape == (h, w), ch
        outs[ch] = planes[0]

    truth = _bright_inside(ref.stars, w, h)
    assert len(truth) >= 5
    for ch in ("R", "G", "B"):
        for x, y, _ in truth:
            mx, my = _centroid(outs[ch], x, y)
            assert abs(mx - x) < 0.1 and abs(my - y) < 0.1, (ch, x, y, mx, my)

    rects = [imageops.valid_rect(outs[ch], imageops.VALID_RECT_MARGIN) for ch in masters]
    expected = imageops.intersect_rects(rects)
    assert tuple(res["rect"]) == expected

    x0, y0, x1, y1 = expected
    for ch in masters:
        planes, header = read_fits(res["cropped"][ch])
        assert planes[0].shape == (y1 - y0, x1 - x0), ch
        if ch == "L":
            crop_header = header

    cat = synth.sky_catalogue()
    pix = synth.sky_to_pixel(crop_header, cat[:, 0], cat[:, 1])
    cropped_l = read_fits(res["cropped"]["L"])[0][0]
    ch_, cw = cropped_l.shape
    checked = 0
    for (px, py), peak in zip(pix, cat[:, 2]):
        if peak < 0.4 or not (20 < px < cw - 20 and 20 < py < ch_ - 20):
            continue
        mx, my = _centroid(cropped_l, px, py)
        assert abs(mx - px) < 0.1 and abs(my - py) < 0.1, (px, py, mx, my)
        checked += 1
    assert checked >= 3


def test_halo_matching_brings_the_sigmas_within_five_percent(tmp_path) -> None:
    masters = {
        "L": synth.make_master("L", fwhm=3.0, seed=1),
        "R": synth.make_master("R", fwhm=3.0, seed=2, shift=(4.2, 1.1)),
        "G": synth.make_master("G", fwhm=4.0, seed=3, shift=(-2.6, 3.4)),
    }
    paths = {ch: str(synth.write_master(tmp_path / f"{ch}.fits", m)) for ch, m in masters.items()}
    script = HALO_SCRIPT.replace("ROOT", str(tmp_path)).replace("PLATES", repr(paths))

    res = run_pyscript(script, tmp_path).check().value("RESULT")

    before = res["before"]
    assert abs(before["R"] - 3.0 / 2.35482) < 0.05 * (3.0 / 2.35482)
    assert abs(before["G"] - 4.0 / 2.35482) < 0.05 * (4.0 / 2.35482)

    out = res["out"]
    assert out["L"] == paths["L"]
    assert out["G"] == paths["G"]
    assert out["R"] != paths["R"]

    after = res["after"]
    assert abs(after["R"] - after["G"]) <= 0.05 * after["G"]
    assert abs(after["G"] - before["G"]) <= 0.05 * before["G"]
    assert abs(after["L"] - before["L"]) <= 0.05 * before["L"]
    assert after["R"] > 1.2 * before["R"]
