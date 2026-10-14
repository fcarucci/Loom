"""The run orchestrator under siril-cli without SPCC: a luminance-only run and a narrowband run with no palette.

Both runs use background extraction (gradient tool none), a stretch and an export folder, so they exercise ingest,
registration (narrowband), crop, publish, export and the cleanup of the work folder, all offline. The composite is not
built here (its SPCC step needs the network-marked database); see test_pipeline_small.py for that run.
Skipped without siril-cli (marker ``siril``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .. import synth
from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

SCRIPT = r'''
import json, os, sys
import sirilpy as s
siril = s.SirilInterface(); siril.connect()
from loomsiril.loom import config as loom_config, pipeline
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

here = os.getcwd()

def run_case(name, paths):
    cache = os.path.join(here, "cache-" + name)
    os.makedirs(cache, exist_ok=True)
    cfg = loom_config.defaults()
    cfg.update({
        "paths": {"L": "", "R": "", "G": "", "B": "", "H": "", "S": "", "O": "", **paths},
        "outputDir": os.path.join(here, "out-" + name),
        "exportDir": os.path.join(here, "tif-" + name),
        "cacheDir": cache,
        "useCache": True,
        "gradientTool": "none",
        "stretch": True,
    })
    before = siril.get_siril_wd()
    def factory(work):
        return RunContext(ops=SirilCommandOps(siril), work_dir=work, platform=sys.platform, tools={})
    report = pipeline.run(cfg, factory, platform="linux", env={}, home=here, stamp=name)
    work = os.path.join(cache, "work", name)
    return {
        "produced": report.produced,
        "exports": sorted(os.path.basename(p) for p in report.exports),
        "exports_exist": all(os.path.isfile(p) for p in report.exports),
        "work_removed": not os.path.exists(work),
        "cwd_restored": siril.get_siril_wd() == before,
    }

info = {
    "lum": run_case("lum", {"L": os.path.join(here, "L.fits")}),
    "nb": run_case("nb", {"H": os.path.join(here, "H.fits"), "S": os.path.join(here, "S.fits"),
                          "O": os.path.join(here, "O.fits")}),
}
print("RESULT=" + json.dumps(info), flush=True)
'''


def test_luminance_and_narrowband_runs_under_siril_cli_without_spcc(tmp_path: Path) -> None:
    synth.write_master(tmp_path / "L.fits", synth.make_master("L", seed=1))
    shifts = {"H": (0.0, 0.0), "S": (-2.6, 3.4), "O": (3.1, -2.2)}
    for index, channel in enumerate(("H", "S", "O")):
        master = synth.make_master(channel, seed=index + 5, shift=shifts[channel], fwhm=3.0 + index)
        synth.write_master(tmp_path / f"{channel}.fits", master)
    result = run_pyscript(SCRIPT, tmp_path).check().value("RESULT")

    lum = result["lum"]
    assert lum["produced"] == ["L"]
    assert lum["exports"] == ["L.tif"]
    assert lum["exports_exist"] is True
    assert lum["work_removed"] is True
    assert lum["cwd_restored"] is True

    nb = result["nb"]
    assert nb["produced"] == ["H", "O", "S"]
    assert nb["exports"] == ["H.tif", "O.tif", "S.tif"]
    assert nb["exports_exist"] is True
    assert nb["work_removed"] is True
    assert nb["cwd_restored"] is True
