"""Siril background extraction of the mgc slot against a real siril-cli (marker ``siril``).

A synthetic master with a smooth gradient (and a faint blob) is run through ``run_mgc`` inside Siril; the output is
compared with the same master rendered without the gradient, which is the truth. Checked: the residual gradient
(range of 8 x 6 block medians of output minus truth) stays under 5% of the injected range, the star fluxes agree
within 1%, and the blob keeps its amplitude within 5%.
"""

from __future__ import annotations

import numpy as np
import pytest

from loomsiril.io_fits import read_fits

from .. import synth
from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

W, H = 640, 512
GRADIENT = (0.04, 0.02, 0.03)
BLOB = (420, 120, 25, 0.02)

SCRIPT = r'''
import json
import sirilpy as s
from loomsiril.loom.siril_ops import SirilCommandOps
from loomsiril.loom.stages_subsky import mgc_params, run_mgc

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = "ROOT"
params = mgc_params(None, "L")
out = run_mgc(ops, root + "/gradient.fits", root + "/gradient_out.fits", params, log=siril.log)
print("RESULT=" + json.dumps({"saved": out}), flush=True)
'''


def _block_medians(img: np.ndarray, nx: int = 8, ny: int = 6) -> np.ndarray:
    h, w = img.shape
    return np.array([[np.median(img[j * h // ny:(j + 1) * h // ny, i * w // nx:(i + 1) * w // nx])
                      for i in range(nx)] for j in range(ny)])


def _disk_mask(shape: tuple[int, int], x: float, y: float, r0: float, r1: float) -> np.ndarray:
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    d2 = (xx - x) ** 2 + (yy - y) ** 2
    return (d2 >= r0 * r0) & (d2 <= r1 * r1)


def _star_flux(img: np.ndarray, x: float, y: float) -> float:
    """Aperture flux of a star above the median of a surrounding annulus (local background removed)."""
    ring = _disk_mask(img.shape, x, y, 10, 14)
    core = _disk_mask(img.shape, x, y, 0, 8)
    return float(np.sum(img[core] - np.median(img[ring])))


def _blob_amplitude(img: np.ndarray, x: float, y: float) -> float:
    centre = _disk_mask(img.shape, x, y, 0, 5)
    ring = _disk_mask(img.shape, x, y, 90, 110)
    return float(np.mean(img[centre]) - np.median(img[ring]))


def test_background_extraction_removes_the_gradient_and_keeps_stars_and_blob(tmp_path) -> None:
    kw = dict(w=W, h=H, blob=BLOB)
    grad = synth.make_master("L", gradient=GRADIENT, **kw)
    truth = synth.make_master("L", **kw)
    synth.write_master(tmp_path / "gradient.fits", grad)

    run = run_pyscript(SCRIPT.replace("ROOT", str(tmp_path)), tmp_path).check()
    assert run.value("RESULT")["saved"].endswith("gradient_out.fits")

    out = read_fits(tmp_path / "gradient_out.fits")[0][0].astype(np.float64)
    ref = truth.planes[0].astype(np.float64)
    injected = grad.planes[0].astype(np.float64) - ref
    injected_range = float(np.ptp(injected))

    residual = _block_medians(out - ref)
    assert float(np.ptp(residual)) < 0.05 * injected_range

    bright = [(x, y, peak) for x, y, peak in truth.stars if peak > 0.2 and 30 < x < W - 30 and 30 < y < H - 30]
    assert len(bright) >= 3
    for x, y, _ in bright[:10]:
        f_out = _star_flux(out, x, y)
        f_ref = _star_flux(ref, x, y)
        assert abs(f_out - f_ref) <= 0.01 * abs(f_ref)

    bx, by, _, _ = BLOB
    a_out = _blob_amplitude(out, bx, by)
    a_ref = _blob_amplitude(ref, bx, by)
    assert abs(a_out - a_ref) <= 0.05 * abs(a_ref)
