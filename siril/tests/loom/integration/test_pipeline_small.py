"""A small RGB run of the orchestrator under siril-cli: the whole chain (ingest, the gradient-free background
extraction, registration, crop, combine, SPCC, stretch, publish) on three synthetic masters.

The run must produce RGB.fits carrying the Loom output keyword and a world coordinate system, in under two minutes.
The composite is calibrated with SPCC, which needs Siril's SPCC database: the offline test image does not carry it, so
the test is network-marked like the other SPCC tests (LOOMSIRIL_NETWORK=1 after docker/network-setup.sh). Skipped
without siril-cli (marker ``siril``).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from .. import synth
from ..siril_run import run_pyscript
from .conftest import CATALOGUE_ATTEMPTS, CATALOGUE_PAUSE_S, online_catalogue_crash

pytestmark = pytest.mark.siril

FILTERS = {"R": "Baader R", "G": "Baader G", "B": "Baader B"}

SCRIPT = r'''
import json, os, sys
import sirilpy as s
siril = s.SirilInterface(); siril.connect()
from loomsiril.io_fits import read_fits
from loomsiril.loom import config as loom_config, pipeline
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

here = os.getcwd()
cache = os.path.join(here, "cache")
os.makedirs(cache, exist_ok=True)
cfg = loom_config.defaults()
cfg.update({
    "paths": {"L": "", "R": os.path.join(here, "R.fits"), "G": os.path.join(here, "G.fits"),
              "B": os.path.join(here, "B.fits"), "H": "", "S": "", "O": ""},
    "filters": {"R": "Baader R", "G": "Baader G", "B": "Baader B"},
    "outputDir": os.path.join(here, "out"),
    "cacheDir": cache,
    "useCache": True,
    "gradientTool": "none",
    "stretch": True,
})

def factory(work):
    return RunContext(ops=SirilCommandOps(siril, log=siril.log), work_dir=work, platform=sys.platform, tools={})

report = pipeline.run(cfg, factory, platform="linux", env={}, home=here, stamp="small")
planes, header = read_fits(report.outputs["RGB"])
info = {
    "produced": report.produced,
    "name": os.path.basename(report.outputs["RGB"]),
    "duration_s": report.duration_s,
    "loomout": str(header.get("LOOMOUT", "")),
    "wcs": "CTYPE1" in header and "CRVAL1" in header,
    "planes": len(planes),
}
print("RESULT=" + json.dumps(info), flush=True)
'''


@pytest.mark.network
def test_small_rgb_run_under_siril_cli(tmp_path: Path) -> None:
    shifts = {"R": (0.0, 0.0), "G": (-2.6, 3.4), "B": (3.1, -2.2)}
    for index, channel in enumerate(("R", "G", "B")):
        # the default frame carries enough stars for the registration to find its reference
        master = synth.make_master(channel, seed=index + 2, shift=shifts[channel], filter_name=FILTERS[channel])
        synth.write_master(tmp_path / f"{channel}.fits", master)
    for attempt in range(1, CATALOGUE_ATTEMPTS + 1):
        run = run_pyscript(SCRIPT, tmp_path)
        if not online_catalogue_crash(run.returncode, run.stdout + run.stderr) or attempt == CATALOGUE_ATTEMPTS:
            break
        time.sleep(CATALOGUE_PAUSE_S)  # Siril's online catalogue failed on SSL and siril-cli died (see conftest)
    run.check()
    result = run.value("RESULT")
    assert result["produced"] == ["RGB"]
    assert result["name"] == "RGB.fits"
    assert result["loomout"] == "RGB"
    assert result["wcs"] is True
    assert result["planes"] == 3
    assert result["duration_s"] < 120
