"""The correction stages inside a real siril-cli (marker ``siril``): ingest, solve, subsky and the GraXpert route.

Synthetic masters only. The GraXpert route runs the stand-in script under tests/loom/tools/fixtures (a plane fit, not
a model). The XISF input is written here with uncompressed attached pixel data, as the test_xisf header tests do.
"""

from __future__ import annotations

import filecmp
import struct
from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits

from .. import synth
from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

W, H = 320, 256
GRADIENT = (0.04, 0.02, 0.03)
STANDIN = Path(__file__).resolve().parent.parent / "tools" / "fixtures" / "fake_graxpert_ai.py"
XISF_DATA_OFFSET = 4096

PRELUDE = r'''
import json
import sirilpy as s
from loomsiril.loom import stages_correct as sc
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
ctx = RunContext(ops=ops, work_dir="ROOT", log=siril.log, warn=siril.log)
'''


def _script(body: str, root: Path) -> str:
    return (PRELUDE + body).replace("ROOT", str(root))


def _block_medians(img: np.ndarray, nx: int = 8, ny: int = 6) -> np.ndarray:
    h, w = img.shape
    return np.array([[np.median(img[j * h // ny:(j + 1) * h // ny, i * w // nx:(i + 1) * w // nx])
                      for i in range(nx)] for j in range(ny)])


def _gradient_range(img: np.ndarray) -> float:
    m = _block_medians(img)
    return float(np.ptp(m))


def write_xisf(path: Path, plane: np.ndarray) -> Path:
    """A single-plane Float32 XISF with uncompressed pixel data at byte 4096 (rows top-down, little-endian)."""
    h, w = plane.shape
    data = np.ascontiguousarray(plane, dtype="<f4").tobytes()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">\n'
        f' <Image geometry="{w}:{h}:1" sampleFormat="Float32" colorSpace="Gray" '
        f'location="attachment:{XISF_DATA_OFFSET}:{len(data)}">\n'
        "  <FITSKeyword name=\"FILTER\" value=\"'R'\" comment=\"\"/>\n"
        "  <FITSKeyword name=\"INSTRUME\" value=\"'ZWO ASI2600MM Pro'\" comment=\"\"/>\n"
        " </Image>\n"
        "</xisf>\n"
    ).encode("utf-8")
    preamble = b"XISF0100" + struct.pack("<I", XISF_DATA_OFFSET - 16) + b"\x00" * 4
    body = xml + b" " * (XISF_DATA_OFFSET - 16 - len(xml))
    assert len(xml) <= XISF_DATA_OFFSET - 16
    path.write_bytes(preamble + body + data)
    return path


def test_ingest_reads_fits_and_xisf_and_leaves_the_sources_alone(tmp_path: Path) -> None:
    fits_master = synth.make_master("L", w=W, h=H)
    fits_src = synth.write_master(tmp_path / "L.fits", fits_master)
    xisf_master = synth.make_master("R", w=W, h=H)
    xisf_src = write_xisf(tmp_path / "R.xisf", xisf_master.planes[0])
    before = (fits_src.read_bytes(), xisf_src.read_bytes())

    body = (
        'fits_out = sc.ingest_channel(ctx, "L", "ROOT/L.fits")\n'
        'xisf_out = sc.ingest_channel(ctx, "R", "ROOT/R.xisf")\n'
        'print("RESULT=" + json.dumps({"fits": fits_out, "xisf": xisf_out}), flush=True)\n'
    )
    run = run_pyscript(_script(body, tmp_path), tmp_path).check()
    out = run.value("RESULT")
    assert out["fits"].endswith("L_src.fits") and out["xisf"].endswith("R_src.fits")

    assert (fits_src.read_bytes(), xisf_src.read_bytes()) == before
    for key, master in (("fits", fits_master), ("xisf", xisf_master)):
        planes, _header = read_fits(out[key])
        assert planes[0].shape == master.planes[0].shape
        assert np.allclose(planes[0], master.planes[0], atol=1e-6)


def test_solve_does_not_solve_a_master_that_already_has_a_wcs(tmp_path: Path) -> None:
    src = synth.write_master(tmp_path / "G.fits", synth.make_master("G", w=W, h=H, with_wcs=True))
    body = (
        'out = sc.broadband_runners(ctx, "G", {"gradientTool": "none", "smoothing": 0.5})["solve"]'
        '("ROOT/G.fits", "ROOT/G_solved.fits")\n'
        'print("RESULT=" + json.dumps({"out": out.path}), flush=True)\n'
    )
    run = run_pyscript(_script(body, tmp_path), tmp_path).check()
    assert run.value("RESULT")["out"].endswith("G_solved.fits")
    assert filecmp.cmp(src, tmp_path / "G_solved.fits", shallow=False)


def test_subsky_runs_on_l_only_when_the_gradient_tool_is_none(tmp_path: Path) -> None:
    kw = dict(w=W, h=H, gradient=GRADIENT)
    synth.write_master(tmp_path / "gradient.fits", synth.make_master("L", **kw))
    body = (
        'cfg = {"gradientTool": "none", "smoothing": 0.5}\n'
        'runners = sc.broadband_runners(ctx, "L", cfg)\n'
        'narrow = sc.narrowband_runners(ctx, "H", dict(cfg, graxpertNarrowband=True))\n'
        'graxpert = sc.broadband_runners(ctx, "R", {"gradientTool": "GraXpert", "smoothing": 0.5})\n'
        'out = runners["mgc"]("ROOT/gradient.fits", "ROOT/gradient_out.fits").path\n'
        'print("RESULT=" + json.dumps({"mgc": "mgc" in runners, "graxpert_on_l": "graxpert" in runners, '
        '"narrow": sorted(narrow), "gradient_r_has_mgc": "mgc" in graxpert, "out": out}), flush=True)\n'
    )
    run = run_pyscript(_script(body, tmp_path), tmp_path).check()
    result = run.value("RESULT")
    assert result["mgc"] is True
    assert result["graxpert_on_l"] is False
    assert result["narrow"] == []
    assert result["gradient_r_has_mgc"] is False
    before = read_fits(tmp_path / "gradient.fits")[0][0].astype(np.float64)
    after = read_fits(tmp_path / "gradient_out.fits")[0][0].astype(np.float64)
    assert _gradient_range(after) < 0.25 * _gradient_range(before)


def test_graxpert_route_on_r_runs_the_script_and_removes_the_gradient(tmp_path: Path) -> None:
    kw = dict(w=W, h=H, gradient=GRADIENT)
    synth.write_master(tmp_path / "R.fits", synth.make_master("R", **kw))
    body = (
        f'ctx.tools = {{"graxpert-ai": {str(STANDIN)!r}}}\n'
        'runners = sc.broadband_runners(ctx, "R", {"gradientTool": "GraXpert", "smoothing": 0.5})\n'
        'out = runners["graxpert"]("ROOT/R.fits", "ROOT/R_bge.fits").path\n'
        'print("RESULT=" + json.dumps({"mgc": "mgc" in runners, "out": out}), flush=True)\n'
    )
    run = run_pyscript(_script(body, tmp_path), tmp_path).check()
    result = run.value("RESULT")
    assert result["mgc"] is False
    assert result["out"].endswith("R_bge.fits")
    before = read_fits(tmp_path / "R.fits")[0][0].astype(np.float64)
    after = read_fits(tmp_path / "R_bge.fits")[0][0].astype(np.float64)
    assert _gradient_range(after) < 0.5 * _gradient_range(before)
