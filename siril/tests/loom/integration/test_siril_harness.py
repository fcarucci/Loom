"""siril-cli runs Python scripts headless in the test image: the harness every Siril integration test relies on.

Measured facts pinned here (Siril 1.4.4): sirilpy's pixel data is bottom-up (row 0 is the bottom row, FITS order)
while loomsiril.io_fits holds images top-down; a Python error inside ``pyscript`` does not change siril-cli's exit
status, so scripts report through printed lines (and the Loom script through its status file).
"""

from __future__ import annotations

import numpy as np
import pytest

from .. import synth
from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

SCRIPT = r'''
import json, sys
import sirilpy as s
siril = s.SirilInterface(); siril.connect()
import numpy, scipy, tifffile
import loomsiril.loom
siril.cmd("load", "master.fits")
px = siril.get_image_pixeldata()
hdr = siril.get_image_fits_header()
info = {"is_cli": siril.is_cli(), "shape": list(px.shape), "dtype": str(px.dtype),
        "row0_mean": float(px[0].mean()), "rowlast_mean": float(px[-1].mean()),
        "filter": "FILTER  = 'R" in hdr, "wcs": "CRPIX1" in hdr}
print("INFO=" + json.dumps(info), flush=True)
'''


def test_pyscript_runs_offline_with_loom_dependencies_and_reads_a_synthetic_master(tmp_path) -> None:
    m = synth.make_master("R", w=96, h=64, gradient=(0.0, 0.2, 0.0))  # brighter towards the bottom (top-down rows)
    synth.write_master(tmp_path / "master.fits", m)
    run = run_pyscript(SCRIPT, tmp_path).check()
    info = run.value("INFO")
    assert info["is_cli"] is True
    assert info["shape"] == [64, 96] and info["dtype"] == "float32"
    assert info["filter"] and info["wcs"]
    top_down = m.planes[0]
    # sirilpy row 0 is the image's bottom row
    assert abs(info["row0_mean"] - float(top_down[-1].mean())) < 1e-6
    assert abs(info["rowlast_mean"] - float(top_down[0].mean())) < 1e-6


def test_python_errors_in_pyscript_do_not_change_the_exit_status(tmp_path) -> None:
    run = run_pyscript("import sys\nprint('BEFORE', flush=True)\nsys.exit(3)\n", tmp_path)
    assert run.returncode == 0 and run.lines("BEFORE")
    run = run_pyscript("raise RuntimeError('boom')\n", tmp_path, name="raises.py")
    assert run.returncode == 0 and "RuntimeError: boom" in run.stdout
    with pytest.raises(AssertionError):
        run.check()
