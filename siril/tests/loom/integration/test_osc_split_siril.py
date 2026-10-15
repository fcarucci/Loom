"""Colour masters split into channel plates inside a real siril-cli (marker ``siril``).

A synthetic colour master in a format ``read_fits`` cannot read directly (a tile-compressed ``.fits.fz`` made by Siril,
and an XISF written here with uncompressed planar pixel data) goes through the run's channel loading twice with one
cache folder. The cold run converts the master once through Siril and splits it into its one-channel plates; the warm
run converts nothing and hands out the cached plates. The plates equal the planes of the source.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits

from .. import synth
from ..siril_run import SirilRun, run_pyscript

pytestmark = pytest.mark.siril

W, H = 160, 128
XISF_DATA_OFFSET = 4096

SCRIPT = r'''
import json, os
import sirilpy as s
from loomsiril.loom import cache, config as loom_config, pipeline, stages_correct
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
ROOT = "ROOT_DIR"
SLOT, KIND, MASTER = "SLOT_NAME", "KIND_NAME", "MASTER_PATH"
CACHE = os.path.join(ROOT, "cache")
os.makedirs(CACHE, exist_ok=True)

conversions = []
real_ingest = stages_correct.ingest_channel


def counting(ctx, channel, src):
    conversions.append(os.path.basename(str(src)))
    return real_ingest(ctx, channel, src)


stages_correct.ingest_channel = counting
ops.set_fits_compression(False)  # the run turns it off; saves must write plain FITS


def one_run(tag):
    work = os.path.join(ROOT, "work_" + tag)
    os.makedirs(work, exist_ok=True)
    cfg = loom_config.defaults()
    cfg.update({"paths": dict(cfg["paths"], **{SLOT: MASTER}), "oscKinds": {SLOT: KIND}, "useCache": True,
                "cacheDir": CACHE, "outputDir": os.path.join(ROOT, "out"), "gradientTool": "none"})
    lines = []
    ctx = RunContext(ops=ops, work_dir=work, log=lines.append, warn=lines.append)
    state = pipeline._Run(ctx, cfg, cache.CacheStore(CACHE), CACHE, {})
    before = len(conversions)
    state.load_channels()
    plates = {key: state.plate_of(key) for key in state.chans}
    return {"plates": plates, "conversions": len(conversions) - before,
            "lines": [ln for ln in lines if ln.startswith("OSC ")]}


# one printed line per run: Siril cuts a long log line
print("RESULT_COLD=" + json.dumps(one_run("cold")), flush=True)
print("RESULT_WARM=" + json.dumps(one_run("warm")), flush=True)
'''

MAKE_FZ = r'''
import json
import sirilpy as s
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
siril.cmd("set compression.quantization=256")  # the finest quantisation Siril allows: float planes stay close
ops.set_fits_compression(True)
ops.load("ROOT_DIR/source.fits")
ops.save("ROOT_DIR/master.fits")
ops.set_fits_compression(False)
print("RESULT=" + json.dumps({"fz": True}), flush=True)
'''


def _source_planes() -> list[np.ndarray]:
    return [synth.make_master(ch, w=W, h=H, seed=i + 1).planes[0] for i, ch in enumerate(("R", "G", "B"))]


def _write_xisf(path: Path, planes: list[np.ndarray]) -> Path:
    """A three-plane Float32 RGB XISF with uncompressed planar pixel data at byte 4096 (rows top-down)."""
    data = b"".join(np.ascontiguousarray(p, dtype="<f4").tobytes() for p in planes)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">\n'
        f' <Image geometry="{W}:{H}:3" sampleFormat="Float32" colorSpace="RGB" '
        f'location="attachment:{XISF_DATA_OFFSET}:{len(data)}">\n'
        "  <FITSKeyword name=\"FILTER\" value=\"'L-eXtreme'\" comment=\"\"/>\n"
        "  <FITSKeyword name=\"INSTRUME\" value=\"'ZWO ASI2600MC Pro'\" comment=\"\"/>\n"
        " </Image>\n"
        "</xisf>\n"
    ).encode("utf-8")
    preamble = b"XISF0100" + struct.pack("<I", XISF_DATA_OFFSET - 16) + b"\x00" * 4
    assert len(xml) <= XISF_DATA_OFFSET - 16
    path.write_bytes(preamble + xml + b" " * (XISF_DATA_OFFSET - 16 - len(xml)) + data)
    return path


def _script(tmp_path: Path, slot: str, kind: str, master: Path) -> str:
    return (SCRIPT.replace("ROOT_DIR", str(tmp_path)).replace("SLOT_NAME", slot).replace("KIND_NAME", kind)
            .replace("MASTER_PATH", str(master)))


def _results(run: SirilRun) -> dict:
    return {"cold": run.value("RESULT_COLD"), "warm": run.value("RESULT_WARM")}


def _check(result: dict, planes: list[np.ndarray], members: dict[str, int], name: str, sidecars: Path,
           atol: float = 1e-6) -> None:
    cold, warm = result["cold"], result["warm"]
    assert cold["conversions"] == 1, cold["lines"]       # the master is converted once for all its members
    assert warm["conversions"] == 0, warm["lines"]       # the warm run converts nothing
    for run in (cold, warm):
        assert sorted(run["plates"]) == sorted(members)
        for key, plane in members.items():
            plates, header = read_fits(run["plates"][key])
            assert len(plates) == 1 and plates[0].shape == planes[plane].shape
            assert np.allclose(plates[0], planes[plane], atol=atol), key
            assert header["FILTER"] == {"H": "Ha", "O": "OIII"}.get(key, key)
            assert header["LOOMSRC"] == f"{name}|plane:{plane}"
        assert len(run["lines"]) == 1  # the group line, cold and warm alike
    assert cold["plates"] == warm["plates"]  # the cache entries serve both runs
    assert len(list(sidecars.glob("*.osc.json"))) == 1


def test_an_xisf_colour_master_is_converted_once_and_split(tmp_path: Path) -> None:
    planes = _source_planes()
    master = _write_xisf(tmp_path / "wbpp.xisf", planes)
    run = run_pyscript(_script(tmp_path, "OSCNB", "dualband", master), tmp_path).check()
    _check(_results(run), planes, {"H": 0, "O": 1}, "wbpp.xisf", tmp_path / "cache")


def test_a_compressed_fits_colour_master_is_converted_once_and_split(tmp_path: Path) -> None:
    planes = _source_planes()
    write_fits(tmp_path / "source.fits", planes, {"FILTER": "L-eXtreme", "INSTRUME": "ZWO ASI2600MC Pro"}, bitpix=-32)
    run_pyscript(MAKE_FZ.replace("ROOT_DIR", str(tmp_path)), tmp_path, name="make_fz.py").check()
    master = tmp_path / "master.fits.fz"
    assert master.is_file(), sorted(os.listdir(tmp_path))
    run = run_pyscript(_script(tmp_path, "OSC", "broadband", master), tmp_path).check()
    _check(_results(run), planes, {"R": 0, "G": 1, "B": 2}, "master.fits.fz", tmp_path / "cache",
           atol=1e-4)  # Siril quantises float planes when it compresses: the .fz master is not exact
