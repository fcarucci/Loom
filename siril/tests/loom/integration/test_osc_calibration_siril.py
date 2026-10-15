"""The colour calibration of an OSC composite inside a real siril-cli (marker ``siril``, network for the plate solve).

An OSC composite is built from a synthetic field of public Gaia stars, plate-solved by Siril and calibrated with
``spcc "-oscsensor=<model>" "-oscfilter=<name>" "-osclpf=<name>"``. The cache folder is kept between runs of the
stages: a warm run that changes only the sensor, then only the front filter, then only the low-pass filter runs the
calibration stage again each time and nothing before it, with the white balance measured (sharpening on: the gains
are applied in numpy) and without (sharpening off: Siril calibrates the composite).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ..siril_run import SirilRun, run_pyscript
from .test_osc_spcc_siril import _write_field, database  # noqa: F401  (the fixture is used by name)

pytestmark = [pytest.mark.siril, pytest.mark.network]

SCRIPT = r'''
import json, os
import sirilpy as s
from loomsiril.loom import cache, pipeline, stages_colour
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = siril.get_siril_wd()
ops.set_fits_compression(False)  # the run turns it off; saves must write plain FITS

plates = {ch: os.path.join(root, "field_nowcs_" + ch.lower() + ".fits") for ch in "RGB"}
sent = []
real_spcc_osc = ops.spcc_osc


def recording_spcc_osc(sensor, front, lpf):
    sent.append([sensor, front, lpf])
    return real_spcc_osc(sensor, front, lpf)


ops.spcc_osc = recording_spcc_osc
COMMON = {"x0": 0, "y0": 0, "x1": 512, "y1": 384}
INSTRUME = "ZWO ASI2600MC Pro"
FACTORS = [1.1, 1.0, 0.9]
CHOICES = [
    ("cold", "Sony IMX571", "", ""),
    ("warm", "Sony IMX571", "", ""),
    ("sensor", "Canon EOS 20D", "", ""),
    ("front", "Canon EOS 20D", "Antlia Triband RGB Ultra II", ""),
    ("lpf", "Canon EOS 20D", "Antlia Triband RGB Ultra II", "Canon EOS 350D blocking filter"),
]


def scenario(mode):
    factors = FACTORS if mode == "sharpening-on" else None
    store = cache.CacheStore(os.path.join(root, "cache_" + mode))
    results = []
    for tag, sensor, front, lpf in CHOICES:
        cfg = {"paths": {"OSC": "/m/osc.fits"}, "filters": {}, "useCache": True, "ignoreCache": False,
               "oscSensor": {"sensor": sensor, "frontFilter": front, "lpf": lpf}}
        work = os.path.join(root, "work_" + mode + "_" + tag)
        os.makedirs(work, exist_ok=True)
        lines = []
        ctx = RunContext(ops=ops, work_dir=work, log=lines.append, warn=lines.append)
        params = stages_colour.rgb_stage_params(cfg, INSTRUME, factors)
        source = cache.rgb_source_key({ch: "k" + ch for ch in "RGB"}, COMMON, False)
        chain = cache.build_stage_keys(source, params)
        ran = []
        runners = {}
        for stage, runner in stages_colour.rgb_runners(ctx, plates, cfg, INSTRUME, factors).items():
            def counted(i, o, stage=stage, runner=runner):
                ran.append(stage)
                return runner(i, o)
            runners[stage] = counted
        del sent[:]
        result = pipeline.process_chain(ctx, "RGB", lambda: plates["R"], chain, runners, store, cfg)
        results.append({"tag": tag, "ran": ran, "sent": [list(t) for t in sent], "exists": os.path.isfile(result.path),
                        "warnings": [ln for ln in lines if "could not" in ln or "failed" in ln]})
    return results


# one printed line per mode: Siril cuts a long log line
print("RESULT_OFF=" + json.dumps(scenario("sharpening-off")), flush=True)
print("RESULT_ON=" + json.dumps(scenario("sharpening-on")), flush=True)
'''

NONE_FRONT = "No filter"
NONE_LPF = "Full spectrum (no filter)"


def _results(run: SirilRun, key: str) -> dict[str, dict]:
    return {r["tag"]: r for r in run.value(key)}


def test_an_osc_composite_is_calibrated_with_the_sensor_and_each_spectral_choice_calibrates_again(
        database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:  # noqa: F811
    _write_field(tmp_path, gaia_tsv_path)
    run = run_pyscript(SCRIPT, tmp_path).check()
    off, on = _results(run, "RESULT_OFF"), _results(run, "RESULT_ON")

    stages = ["combine", "solveRGB", "spccRGB"]
    for mode in (off, on):
        assert mode["cold"]["ran"] == stages
        assert mode["warm"]["ran"] == []
        for tag in ("sensor", "front", "lpf"):
            assert mode[tag]["ran"] == ["spccRGB"], tag  # the calibration again, nothing before it
        assert all(r["exists"] and r["warnings"] == [] for r in mode.values())

    # sharpening off: Siril calibrates the composite with the OSC set of the run, none names filled in
    assert off["cold"]["sent"] == [["Sony IMX571", NONE_FRONT, NONE_LPF]]
    assert off["warm"]["sent"] == []
    assert off["sensor"]["sent"] == [["Canon EOS 20D", NONE_FRONT, NONE_LPF]]
    assert off["front"]["sent"] == [["Canon EOS 20D", "Antlia Triband RGB Ultra II", NONE_LPF]]
    assert off["lpf"]["sent"] == [["Canon EOS 20D", "Antlia Triband RGB Ultra II", "Canon EOS 350D blocking filter"]]
    # sharpening on: the measured gains are applied in numpy, Siril's SPCC is not called
    assert all(r["sent"] == [] for r in on.values())
