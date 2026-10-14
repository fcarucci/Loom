"""Publishing under a real siril-cli: two synthetic plates become results, reload with their WCS and LOOMOUT, and the
built-in sRGB profile Siril reports for the plate is the one the saved FITS carries. Skipped without siril-cli
(marker ``siril``).
"""

from __future__ import annotations

import pytest

from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

PUBLISH_SCRIPT = r'''
import json, os
import numpy as np
import sirilpy as s
from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import icc, publish
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = siril.get_siril_wd()
plates = os.path.join(root, "plates dir")
os.makedirs(plates, exist_ok=True)
header = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 150.25, "CRVAL2": 2.5,
          "CRPIX1": 16.5, "CRPIX2": 12.5, "CDELT1": -5.5e-4, "CDELT2": 5.5e-4}
rng = np.random.default_rng(7)
mono_path = os.path.join(plates, "L.fits")
rgb_path = os.path.join(plates, "RGB.fits")
write_fits(mono_path, [rng.random((24, 32)).astype(np.float32)], header)
write_fits(rgb_path, [rng.random((24, 32)).astype(np.float32) for _ in range(3)], header)

# What Siril reports for the built-in sRGB profile, read from each source plate itself. Measured: a colour plate
# gets the 1256-byte sRGB profile and a mono plate gets Siril's 624-byte Gray profile under the same name "sRGB",
# so the expectation is per plate, not one profile for all results.
expected_icc = {}
for key, source in (("L", mono_path), ("RGB", rgb_path)):
    ops.load(source)
    ops.icc_assign("sRGB")
    expected_icc[key] = ops.icc_profile()

lines = []
plan = icc.profile_plan([])
outputs = publish.publish(ops, {"L": mono_path, "RGB": rgb_path}, {}, os.path.join(root, "published dir"),
                          plan, lambda level, text: lines.append(level + ": " + text))
publish.leave_main_result(ops, outputs, lambda level, text: lines.append(level + ": " + text))
reloaded = {}
for name, path in sorted(outputs.items()):
    planes, hdr = read_fits(path)
    ops.load(path)
    reloaded[name] = {
        "file": os.path.basename(path),
        "planes": len(planes),
        "shape": list(planes[0].shape),
        "loomout": hdr.get("LOOMOUT"),
        "ctype1": hdr.get("CTYPE1"),
        "crval1": hdr.get("CRVAL1"),
        "icc_len": len(ops.icc_profile() or b""),
        "icc_matches": ops.icc_profile() == expected_icc[name],
        "expected_len": len(expected_icc[name] or b""),
    }
print("result=" + json.dumps({"reloaded": reloaded, "lines": lines}))
'''


def test_published_plates_reload_with_wcs_loomout_and_icc(tmp_path) -> None:
    run = run_pyscript(PUBLISH_SCRIPT, tmp_path, name="publish_script.py").check()
    result = run.value("result")
    reloaded = result["reloaded"]
    assert sorted(reloaded) == ["L", "RGB"]
    for name, entry in reloaded.items():
        assert entry["file"] == f"{name}.fits"
        assert entry["loomout"] == name
        assert entry["ctype1"] == "RA---TAN"
        assert entry["crval1"] == 150.25
        assert entry["shape"] == [24, 32]
        assert entry["expected_len"] > 0
        assert entry["icc_len"] == entry["expected_len"]
        assert entry["icc_matches"] is True
    assert reloaded["L"]["planes"] == 1 and reloaded["RGB"]["planes"] == 3
    # Measured in Siril 1.4.4: the colour plate's sRGB is 1256 bytes, the mono plate's is 624 (Siril's Gray).
    assert reloaded["RGB"]["expected_len"] == 1256 and reloaded["L"]["expected_len"] == 624
    assert not [ln for ln in result["lines"] if ln.startswith("error")]
