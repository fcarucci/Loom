"""Colour stages against a real siril-cli (marker ``siril``): the RGB combine with keyword cleanup, the measured white
balance applied in numpy, and (network-marked) a broadband SPCC followed by a palette's narrowband SPCC in one session.

The combine test writes three synthetic plates whose FILTER and EXPTIME differ and whose OBJECT agrees: the composite
keeps the pixels of the three plates and loses exactly the two keywords that differ. The white-balance path needs no
Siril command, so its result must equal ``imageops.apply_white_balance`` exactly. The network test is one siril-cli
session: the RGB composite is calibrated broadband, then the palette is calibrated narrowband with the four names
(Siril refuses the narrowband command after a broadband one without them). It needs LOOMSIRIL_NETWORK=1 and the SPCC
database (docker/network-setup.sh).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import imageops

from ..siril_run import run_pyscript
from .test_spcc_session import _database_names, _write_field_frames

pytestmark = pytest.mark.siril

SHAPE = (96, 128)
INSTRUME = "ZWO ASI294MC"
FACTORS = [1.25, 1.0, 0.8]

COMBINE_SCRIPT = r'''
import json
import sirilpy as s
from loomsiril.loom import stages_colour
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = siril.get_siril_wd()
warnings = []
ctx = RunContext(ops=ops, work_dir=root, log=siril.log, warn=warnings.append)
plates = PLATES
runners = stages_colour.rgb_runners(ctx, plates, CONFIG, INSTRUME, FACTORS)
combined = runners["combine"](None, root + "/RGB_combined.fits").path
applied = runners["spccRGB"](combined, root + "/RGB_wb.fits").path
print("RESULT=" + json.dumps({"combined": combined, "applied": applied, "warnings": warnings}), flush=True)
'''

SESSION_SCRIPT = r'''
import json, os
import sirilpy as s
from loomsiril.io_fits import read_fits
from loomsiril.loom import stages_colour
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = siril.get_siril_wd()
p = lambda name: os.path.join(root, name)
warnings = []
ctx = RunContext(ops=ops, work_dir=root, log=siril.log, warn=warnings.append)
CONFIG = @CONFIG@
NAMES = @NAMES@


def change(a, b):
    """Largest absolute difference between two plate stacks."""
    return max(float(abs(x.astype("float64") - y.astype("float64")).max()) for x, y in zip(a, b))


rgb = stages_colour.rgb_runners(ctx, {"R": p("field_nowcs_r.fits"), "G": p("field_nowcs_g.fits"),
                                      "B": p("field_nowcs_b.fits")}, CONFIG, @INSTRUME@, None)
combined = rgb["combine"](None, p("RGB.fits")).path
solved = rgb["solveRGB"](combined, p("RGB_solved.fits")).path
calibrated = rgb["spccRGB"](solved, p("RGB_spcc.fits")).path

palette = stages_colour.palette_runners(ctx, "SHO", {"H": p("field_nowcs_r.fits"), "S": p("field_nowcs_g.fits"),
                                                     "O": p("field_nowcs_b.fits")}, CONFIG, NAMES)
pal_combined = palette["paletteCombine"](None, p("SHO.fits")).path
pal_calibrated = palette["paletteSpcc"](pal_combined, p("SHO_spcc.fits")).path

out = {
    "rgb_change": change(read_fits(solved)[0], read_fits(calibrated)[0]),
    "palette_change": change(read_fits(pal_combined)[0], read_fits(pal_calibrated)[0]),
    "warnings": warnings,
}
print("RESULT=" + json.dumps(out), flush=True)
'''


def _combine_script(plates: dict[str, str]) -> str:
    return (COMBINE_SCRIPT.replace("PLATES", repr(plates)).replace("CONFIG", repr({"filters": {}}))
            .replace("INSTRUME", repr(INSTRUME)).replace("FACTORS", repr(FACTORS)))


def _plates(tmp_path: Path) -> dict[str, str]:
    """Three synthetic plates: a shared OBJECT and INSTRUME, and FILTER and EXPTIME that differ."""
    rng = np.random.default_rng(7)
    headers = {
        "R": {"OBJECT": "M42", "FILTER": "R", "EXPTIME": 60.0, "INSTRUME": INSTRUME},
        "G": {"OBJECT": "M42", "FILTER": "G", "EXPTIME": 60.0, "INSTRUME": INSTRUME},
        "B": {"OBJECT": "M42", "FILTER": "B", "EXPTIME": 120.0, "INSTRUME": INSTRUME},
    }
    paths = {}
    for seed, ch in enumerate(("R", "G", "B")):
        plane = rng.uniform(0.05, 0.4, SHAPE).astype(np.float32)
        paths[ch] = str(tmp_path / f"{ch}_plate.fits")
        write_fits(paths[ch], [plane], headers[ch], bitpix=-32)
    return paths


def test_combine_keeps_pixels_and_drops_differing_keywords(tmp_path) -> None:
    plates = _plates(tmp_path)
    script = _combine_script(plates)

    res = run_pyscript(script, tmp_path).check().value("RESULT")

    assert res["warnings"] == []
    planes, header = read_fits(res["combined"])
    assert len(planes) == 3
    for plane, ch in zip(planes, ("R", "G", "B")):
        np.testing.assert_array_equal(plane, read_fits(plates[ch])[0][0])
    assert "FILTER" not in header and "EXPTIME" not in header
    assert header["OBJECT"] == "M42"


def test_white_balance_path_matches_the_numpy_reference(tmp_path) -> None:
    plates = _plates(tmp_path)
    script = _combine_script(plates)

    res = run_pyscript(script, tmp_path).check().value("RESULT")

    combined_planes = read_fits(res["combined"])[0]
    expected = imageops.apply_white_balance(combined_planes, FACTORS)
    applied = read_fits(res["applied"])[0]
    for got, exp in zip(applied, expected):
        np.testing.assert_array_equal(got, exp)


@pytest.mark.network
def test_broadband_then_palette_narrowband_in_one_session(tmp_path, gaia_tsv_path) -> None:
    """One session: the RGB composite is calibrated broadband, then the SHO palette narrowband with the four names."""
    sensor, red, green, blue = _database_names()
    _write_field_frames(tmp_path, gaia_tsv_path)
    config = {"filters": {"R": red, "G": green, "B": blue}, "spccSensor": sensor,
              "narrowbandBandwidth": 3.0, "narrowbandNormalize": False}
    script = (SESSION_SCRIPT.replace("@CONFIG@", repr(config)).replace("@INSTRUME@", repr(INSTRUME))
              .replace("@NAMES@", repr((sensor, red, green, blue))))

    res = run_pyscript(script, tmp_path).check().value("RESULT")

    assert res["warnings"] == []
    assert res["rgb_change"] > 0.0
    assert res["palette_change"] > 0.0
