"""find_stars against a real siril-cli (marker ``siril``): the user's star-finder focal length and pixel size survive
the run. ``setfindstar reset`` in Siril 1.4.4 zeroes both persisted preferences; Loom restores them after findstar.
"""

from __future__ import annotations

import json

import pytest

from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

SCRIPT = r'''
import json, os
import numpy as np
import sirilpy as s
from loomsiril.io_fits import write_fits
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface()
siril.connect()
root = siril.get_siril_wd()
p = lambda name: os.path.join(root, name)

h, w = 120, 160
rng = np.random.default_rng(5)
img = rng.normal(0.05, 0.002, (h, w))
yy, xx = np.mgrid[0:h, 0:w]
for cx, cy in [(20, 20), (60, 30), (100, 50), (140, 90), (40, 95), (90, 110), (120, 20), (70, 70), (30, 60),
               (150, 40), (10, 100), (110, 80)]:
    img += 0.5 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 1.3 ** 2))
write_fits(p("stars.fits"), [img.astype(np.float32)], {}, bitpix=-32)

ops = SirilCommandOps(siril, log=siril.log)
ops.load(p("stars.fits"))
siril.cmd("setfindstar -focal=600 -pixelsize=3.76")  # sirilpy returns None on success; a failure raises
stars = ops.find_stars()
prefs = [siril.get_siril_config("starfinder", "focal_length"), siril.get_siril_config("starfinder", "pixel_size")]
print("RESULT=" + json.dumps({"prefs": prefs, "nstars": len(stars)}), flush=True)
'''


def test_find_stars_keeps_the_star_finder_focal_length_and_pixel_size(tmp_path) -> None:
    res = run_pyscript(SCRIPT, tmp_path).check().value("RESULT")
    assert res["nstars"] >= 5
    assert res["prefs"] == [600.0, 3.76]
