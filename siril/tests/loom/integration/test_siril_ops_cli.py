"""siril_ops against a real siril-cli (1.4.4): each command the port sends, run inside Siril.

Three siril-cli runs: a file and registration script (folders with spaces), a processing script, and a network-marked
script on a real-sky field (plate solving and SPCC; needs LOOMSIRIL_NETWORK=1 and Siril's online catalogues and
SPCC database). Each script prints one ``RESULT=<json>`` line; the assertions read it. Skipped without siril-cli
(marker ``siril``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits
from loomsiril.loom.siril_ops import spcc_database_candidates, spcc_names

from .. import synth
from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

#: Centre of the public Gaia field of the network-marked test (IC 1396; see tests/fixtures/README.md).
FIELD_RA, FIELD_DEC = 324.745, 57.514
#: 2.0 arcsec per pixel with a 3.76 um pixel: the focal length that scale implies, in mm.
FIELD_FOCAL_MM = 387.5
#: How far the wrong-centre frame's CRVAL1 is moved (degrees of RA). At Dec +57.5 one degree of RA is about 0.54 deg
#: on the sky, so 0.1 deg of RA stays inside the solver's search and the forced solve can align on the stars.
WRONG_CENTRE_OFFSET_DEG = 0.1

SCRIPT_FILES = r'''
import json, os
import numpy as np
import sirilpy as s
from loomsiril.io_fits import read_fits
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = siril.get_siril_wd()
lights = os.path.join(root, "lights dir")
empty = os.path.join(root, "empty dir")
out = {"is_cli": ops.is_cli()}

ops.set32bits()
ops.load(os.path.join(lights, "a1.fits"))
ops.save(os.path.join(root, "saved.fits"))
out["saved_exists"] = os.path.exists(os.path.join(root, "saved.fits"))
out["saved_bare_refused"] = out["saved_upper_refused"] = False
try:
    ops.save(os.path.join(root, "saved"))
except ValueError:
    out["saved_bare_refused"] = True
try:
    ops.save(os.path.join(root, "SAVED.FITS"))
except ValueError:
    out["saved_upper_refused"] = True

ops.cd(empty)
out["cd_with_space_ok"] = siril.get_siril_wd().endswith("empty dir")
ops.cd(lights)
ops.link_sequence("lights")
ops.setref("lights", 1)
ops.register("lights")
ref = read_fits(os.path.join(lights, "a1.fits"))[0][0]
regs = [read_fits(os.path.join(lights, "r_lights_%05d.fit" % i))[0][0] for i in (1, 2, 3)]
out["reg_reference_identical"] = bool(np.array_equal(regs[0], ref))
out["reg_shapes"] = [list(r.shape) for r in regs]
out["ref_shape"] = list(ref.shape)

ops.load(os.path.join(lights, "a1.fits"))
base = ops.pixels()[0]
ops.crop(10, 20, 50, 40)
cropped = ops.pixels()[0]
out["crop_shape"] = list(cropped.shape)
out["crop_maxdiff"] = float(np.max(np.abs(cropped.astype(np.float64) - base[20:60, 10:60].astype(np.float64))))

# the output path has a space too: it is sent as one quoted -out= token
returned = ops.rgbcomp(os.path.join(root, "red.fits"), os.path.join(root, "green.fits"),
                       os.path.join(root, "blue.fits"), os.path.join(root, "my rgb"))
rgb_planes, _ = read_fits(returned)
out["rgb_returned"] = os.path.basename(returned)
out["rgb_planes"] = len(rgb_planes)
out["quoted_entries"] = sorted(n for d in (root, lights, empty) for n in os.listdir(d) if '"' in n)

ops.load(os.path.join(lights, "a1.fits"))
px = ops.pixels()[0].astype(np.float64)
ops.mtf(0.01, 0.2)
after = ops.pixels()[0].astype(np.float64)
x = np.clip((px - 0.01) / (1.0 - 0.01), 0.0, 1.0)
expect = (0.2 - 1.0) * x / ((2 * 0.2 - 1.0) * x - 0.2)
out["mtf_maxdiff"] = float(np.max(np.abs(after - expect)))
print("RESULT=" + json.dumps(out), flush=True)
'''

SCRIPT_PROCESS = r'''
import json, os
import numpy as np
import sirilpy as s
from loomsiril.io_fits import read_fits
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = siril.get_siril_wd()
p = lambda name: os.path.join(root, name)
out = {}


def jsonable(value):
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)


ops.load(p("zeros.fits"))
ops.ghs(("autoghs", "-linked", "0", "4.5", "-b=13", "-hp=0.7", "-lp=0"))
out["ghs_zero_kept"] = bool(np.all(ops.pixels()[0][:10, :10] == 0.0))

ops.load(p("a1.fits"))
ops.platesolve()
out["platesolve_ran"] = True
before = ops.pixels()[0]
ops.linear_match(p("a2.fits"))
out["linear_match_changed"] = bool(not np.array_equal(ops.pixels()[0], before))

ops.load(p("a1.fits"))
before = ops.pixels()[0]
ops.denoise(0.6)
out["denoise_maxdiff"] = float(np.max(np.abs(ops.pixels()[0].astype(np.float64) - before.astype(np.float64))))

ops.load(p("a1.fits"))
ops.subsky()
out["subsky_ran"] = True

ops.icc_assign("sRGB")
builtin = ops.icc_profile()
out["icc_profile_bytes"] = len(builtin) if builtin else 0
with open(p("mono.icc"), "wb") as fh:
    fh.write(builtin or b"")
ops.icc_assign(p("mono.icc"))
out["icc_file_profile_bytes"] = len(ops.icc_profile() or b"")

ops.update_key("loomout", "RGB starless", "Loom pipeline output")
ops.save(p("keyed.fits"))
out["loomout"] = read_fits(p("keyed.fits"))[1].get("LOOMOUT")
out["comment_in_file"] = b"Loom pipeline output" in open(p("keyed.fits"), "rb").read()

ops.load(p("a1.fits"))
# one short line per star: siril-cli cuts long log lines, so the list is not part of RESULT
for st in ops.find_stars():
    print("STAR=" + json.dumps([st["xpos"], st["ypos"], st["fwhmx"], st["fwhmy"]]), flush=True)

out["analyse"] = {k: jsonable(v) for k, v in ops.analyse(p("a1.fits")).items()}

ops.load(p("a1.fits"))
before = ops.pixels()[0].astype(np.float64)
ops.pyscript(p("halve.py"))
out["pyscript_maxdiff"] = float(np.max(np.abs(ops.pixels()[0].astype(np.float64) - 0.5 * before)))
print("RESULT=" + json.dumps(out), flush=True)
'''

SCRIPT_SKY = r'''
import json, os, re
import sirilpy as s
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = siril.get_siril_wd()
p = lambda name: os.path.join(root, name)
out = {}


def crval1():
    match = re.search(r"CRVAL1\s*=\s*([-+0-9.Ee]+)", ops.header())
    return float(match.group(1)) if match else None


# 1. no WCS in the frame: a plain platesolve must solve it
ops.load(p("field_nowcs_r.fits"))
out["nowcs_crval_before"] = crval1()
ops.platesolve()
out["plain_solved_crval"] = crval1()

# 2. a frame whose WCS is wrong by 0.5 deg: a plain platesolve leaves an already-solved image alone ...
ops.load(p("field_wrong_r.fits"))
out["wrong_crval_before"] = crval1()
ops.platesolve()
out["wrong_after_plain"] = crval1()
# ... and -force with the focal length and pixel size solves it again
ops.load(p("field_wrong_r.fits"))
ops.platesolve(force=True, focal=@FOCAL@, pixel_size=3.76)
out["wrong_after_forced"] = crval1()

# 3. colour composite of the WCS-less frames, solved, then SPCC
ops.rgbcomp(p("field_nowcs_r.fits"), p("field_nowcs_g.fits"), p("field_nowcs_b.fits"), p("field_rgb"))
ops.load(p("field_rgb.fit"))
ops.platesolve()
# narrowband first: once a broadband SPCC has run in a session, Siril keeps its sensor and filter selection and a
# narrowband SPCC that follows it fails with "Argument error" (measured on Siril 1.4.4)
ops.spcc_narrowband(656.28, 7.0, 500.7, 25.0, 495.9, 20.0)
out["spcc_narrowband_ran"] = True
ops.spcc_broadband(@SENSOR@, @RNAME@, @GNAME@, @BNAME@)
out["spcc_broadband_ran"] = True
out["solved_after_spcc"] = crval1() is not None
print("RESULT=" + json.dumps(out), flush=True)
'''

HALVE_SCRIPT = r'''
import sirilpy as s
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface()
siril.connect()
ops = SirilCommandOps(siril)
ops.set_pixels([plane * 0.5 for plane in ops.pixels()])
'''


def _spcc_folder() -> Path:
    """Siril's SPCC database folder in this environment (the first candidate that exists)."""
    for candidate in spcc_database_candidates(sys.platform, os.environ, os.path.expanduser("~")):
        if os.path.isdir(candidate):
            return Path(candidate)
    pytest.fail("the SPCC database is not installed here; the network-marked test needs it")


def _real_sky_field(channel: str, sky: float, tint: float, rows: np.ndarray, w: int = 512, h: int = 384,
                    wcs: bool = True, crval_offset: float = 0.0):
    """A synthetic frame of the public Gaia stars (``rows``: ra, dec, G) around IC 1396, at 2 arcsec per pixel.

    The stars are rendered with the true WCS; ``wcs=False`` drops the WCS keywords (RA and DEC stay as hints) and
    ``crval_offset`` moves the header's CRVAL1 away from the true centre (a wrong solution to be corrected).
    """
    header = synth.tan_header(w, h, 2.0)
    header["CRVAL1"], header["CRVAL2"] = FIELD_RA, FIELD_DEC
    xy = synth.sky_to_pixel(header, rows[:, 0], rows[:, 1])
    img = np.full((h, w), sky, dtype=np.float64)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    sig = 3.0 / 2.35482
    for (x, y), g in zip(xy, rows[:, 2]):
        if not (6 <= x < w - 6 and 6 <= y < h - 6):
            continue
        amp = tint * min(0.8, 10 ** (-0.4 * (g - 9.0)))
        x0, y0 = int(x), int(y)
        sl = (slice(y0 - 6, y0 + 7), slice(x0 - 6, x0 + 7))
        img[sl] += amp * np.exp(-((xx[sl] - x) ** 2 + (yy[sl] - y) ** 2) / (2 * sig * sig))
    img += np.random.default_rng(7).normal(0.0, 0.002, img.shape)
    img = np.clip(img, 0.0, 1.0).astype(np.float32)
    header.update({"RA": FIELD_RA, "DEC": FIELD_DEC, "FOCALLEN": FIELD_FOCAL_MM, "XPIXSZ": 3.76, "YPIXSZ": 3.76,
                   "FILTER": channel, "IMAGETYP": "Light"})
    if not wcs:
        header = {k: v for k, v in header.items()
                  if not k.startswith(("CRVAL", "CRPIX", "CD", "CTYPE", "EQUINOX"))}
    else:
        header["CRVAL1"] = header["CRVAL1"] + crval_offset
    return synth.Master(channel=channel, planes=[img], header=header)


def test_file_commands_registration_crop_rgbcomp_and_mtf(tmp_path) -> None:
    # the work folder of link and register has a space in its name, as does the output path of rgbcomp
    (tmp_path / "lights dir").mkdir()
    (tmp_path / "empty dir").mkdir()
    lights = tmp_path / "lights dir"
    # a dense patch of sky so that registration has enough stars to align on
    sky = synth.sky_catalogue(n_stars=80, seed=5, radius_deg=0.05)
    synth.write_master(lights / "a1.fits", synth.make_master("L", w=128, h=96, seed=1, catalogue=sky))
    synth.write_master(lights / "a2.fits",
                       synth.make_master("L", w=128, h=96, seed=2, shift=(4.0, -3.0), catalogue=sky))
    # the third member is a 2x drizzled master of the same sky: it registers onto the native reference size
    synth.write_master(lights / "a3.fits",
                       synth.make_master("L", w=128, h=96, seed=3, shift=(-2.5, 5.0), drizzle=2, catalogue=sky))
    for index, (ch, name) in enumerate((("R", "red"), ("G", "green"), ("B", "blue"))):
        synth.write_master(tmp_path / f"{name}.fits", synth.make_master(ch, w=128, h=96, seed=10 + index))

    run = run_pyscript(SCRIPT_FILES, tmp_path).check()
    res = run.value("RESULT")

    assert res["is_cli"] is True
    assert res["saved_exists"] is True
    assert res["saved_bare_refused"] is True
    assert res["saved_upper_refused"] is True
    assert res["cd_with_space_ok"] is True
    assert res["reg_reference_identical"] is True
    assert res["ref_shape"] == [96, 128]
    assert res["reg_shapes"] == [[96, 128], [96, 128], [96, 128]]
    for n in (1, 2, 3):
        assert (lights / f"r_lights_{n:05d}.fit").is_file()
    assert res["crop_shape"] == [40, 50]
    assert res["crop_maxdiff"] < 1e-6
    assert res["rgb_returned"] == "my rgb.fit" and res["rgb_planes"] == 3
    assert (tmp_path / "my rgb.fit").is_file()
    assert res["quoted_entries"] == []
    assert res["mtf_maxdiff"] < 2e-6


def test_processing_icc_keys_stars_analyse_and_pyscript(tmp_path) -> None:
    # a dense patch of sky: the default catalogue puts only a few stars in a frame this small
    sky = synth.sky_catalogue(n_stars=80, seed=5, radius_deg=0.05)
    a1 = synth.make_master("L", w=160, h=120, seed=1, fwhm=3.0, catalogue=sky)
    synth.write_master(tmp_path / "a1.fits", a1)
    synth.write_master(tmp_path / "a2.fits",
                       synth.make_master("L", w=160, h=120, seed=2, shift=(5.0, 2.0), catalogue=sky))
    zeros = synth.make_master("L", w=160, h=120, seed=4, catalogue=sky)
    zeros.planes[0][:10, :10] = 0.0
    synth.write_master(tmp_path / "zeros.fits", zeros)
    (tmp_path / "halve.py").write_text(HALVE_SCRIPT, encoding="utf-8")

    run = run_pyscript(SCRIPT_PROCESS, tmp_path).check()
    res = run.value("RESULT")
    res["stars"] = [json.loads(line[len("STAR="):]) for line in run.lines("STAR=")]

    assert res["ghs_zero_kept"] is True
    assert res["platesolve_ran"] is True
    assert res["linear_match_changed"] is True
    assert res["denoise_maxdiff"] > 0.0
    assert res["subsky_ran"] is True
    assert res["icc_profile_bytes"] > 0 and res["icc_file_profile_bytes"] > 0
    assert res["loomout"] == "RGB starless"
    assert res["comment_in_file"] is True

    fields = {"fwhm", "roundness", "bgnoise", "nbstars", "channels", "filter", "width", "height", "wfwhm",
              "imagetype", "timestamp"}
    assert fields <= set(res["analyse"])
    assert res["analyse"]["width"] == 160 and res["analyse"]["height"] == 120

    # find_stars: Siril's positions (column + 0.5, distance from the top + 0.5) match the synthetic stars
    arr = np.asarray(res["stars"], dtype=np.float64).reshape(-1, 4)
    xy = np.column_stack([arr[:, 0] - 0.5, arr[:, 1] - 0.5])
    fwhm = arr[:, 2:4]
    assert len(xy) >= 10
    truth = a1.stars[:, :2]
    dist = np.min(np.hypot(xy[:, None, 0] - truth[None, :, 0], xy[:, None, 1] - truth[None, :, 1]), axis=1)
    assert float(np.mean(dist < 1.0)) >= 0.9
    assert abs(float(np.median(fwhm)) - 3.0) < 0.5

    # pyscript: the stand-in script halved the loaded image, and the change is visible here
    assert res["pyscript_maxdiff"] < 1e-6


@pytest.mark.network
def test_platesolve_and_spcc_on_a_real_sky_field(tmp_path, gaia_tsv_path) -> None:
    """Plate solving (plain and forced, with focal length and pixel size) and SPCC on public Gaia stars.

    Needs network access for Siril's online catalogues and an installed SPCC database; runs only with
    LOOMSIRIL_NETWORK=1 (never in the offline gate).
    """
    folder = _spcc_folder()
    sensors = spcc_names("monosensor", folder)
    reds, greens, blues = (spcc_names(k, folder) for k in ("redfilter", "greenfilter", "bluefilter"))
    if not (sensors and reds and greens and blues):
        pytest.fail(f"the SPCC database at {folder} lacks a sensor or a filter name")

    data = np.genfromtxt(gaia_tsv_path, delimiter="\t", names=True)
    near = (np.hypot((data["ra"] - FIELD_RA) * np.cos(np.radians(FIELD_DEC)), data["dec"] - FIELD_DEC) < 0.15)
    rows = np.column_stack([data["ra"][near], data["dec"][near], data["G"][near]])
    rows = rows[rows[:, 2] < 13.0]
    assert len(rows) >= 30, "too few bright Gaia stars in the field"

    for channel, tint in (("R", 1.0), ("G", 0.8), ("B", 0.6)):
        low = channel.lower()
        synth.write_master(tmp_path / f"field_nowcs_{low}.fits",
                           _real_sky_field(channel, 0.05, tint, rows, wcs=False))
    synth.write_master(tmp_path / "field_wrong_r.fits",
                       _real_sky_field("R", 0.05, 1.0, rows, crval_offset=WRONG_CENTRE_OFFSET_DEG))

    script = (SCRIPT_SKY.replace("@FOCAL@", repr(FIELD_FOCAL_MM)).replace("@SENSOR@", repr(sensors[0]))
              .replace("@RNAME@", repr(reds[0])).replace("@GNAME@", repr(greens[0]))
              .replace("@BNAME@", repr(blues[0])))
    res = run_pyscript(script, tmp_path).check().value("RESULT")

    # the WCS-less frame had no CRVAL and the plain solve gave the true centre
    assert res["nowcs_crval_before"] is None
    assert abs(res["plain_solved_crval"] - FIELD_RA) < 0.05
    # the plain solve left the wrong-centre frame alone (already solved); the forced solve corrected it
    assert abs(res["wrong_crval_before"] - (FIELD_RA + WRONG_CENTRE_OFFSET_DEG)) < 1e-6
    assert abs(res["wrong_after_plain"] - (FIELD_RA + WRONG_CENTRE_OFFSET_DEG)) < 1e-6
    assert abs(res["wrong_after_forced"] - FIELD_RA) < 0.05
    assert res["spcc_broadband_ran"] is True
    assert res["spcc_narrowband_ran"] is True
    assert res["solved_after_spcc"] is True
