"""GraXpert-AI adapter run inside a real siril-cli (1.4.4): run_bge through SirilCommandOps and pyscript.

The script that Siril runs is a stand-in (tests/loom/tools/fixtures/fake_graxpert_ai.py): it fits a plane, subtracts
it, and has a failing variant selected by an environment variable. No GraXpert code or model is involved.
Skipped without siril-cli (marker ``siril``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits

from .. import synth
from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

FIXTURE = Path(__file__).resolve().parents[1] / "tools" / "fixtures" / "fake_graxpert_ai.py"
GRADIENT = (0.5, 0.25, 0.0)

PROBE = r'''
import json
import sys
import sirilpy as s
from loomsiril.loom.siril_ops import SirilCommandOps
from loomsiril.loom.stagekit import StageFailed
from loomsiril.loom.tools.graxpert_ai import run_bge

siril = s.SirilInterface()
siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
result = {"is_cli": ops.is_cli(), "raised": None, "saved": None}
try:
    result["saved"] = run_bge(ops, __STAND_IN__, __SRC__, __OUT__, 0.5, "L master", log=siril.log)
except StageFailed as exc:
    result["raised"] = str(exc)
print("RESULT=" + json.dumps(result), flush=True)
'''


def _slope(plane: np.ndarray) -> tuple[float, float]:
    h, w = plane.shape
    yy, xx = np.mgrid[0:h, 0:w]
    design = np.column_stack([xx.ravel(), yy.ravel(), np.ones(h * w)])
    coef, *_ = np.linalg.lstsq(design, plane.astype(np.float64).ravel(), rcond=None)
    return float(coef[0]), float(coef[1])


def _run(tmp_path: Path) -> tuple[Path, Path, object]:
    src = tmp_path / "linear_L.fits"
    synth.write_master(src, synth.make_master("L", w=128, h=96, gradient=GRADIENT))
    out = tmp_path / "extracted_L.fits"
    script = PROBE.replace("__STAND_IN__", repr(str(FIXTURE))).replace("__SRC__", repr(str(src))).replace(
        "__OUT__", repr(str(out)))
    run = run_pyscript(script, tmp_path / "siril", timeout=600).check()
    return src, out, run.value("RESULT")


def test_graxpert_ai_stand_in_extracts_the_background_inside_siril(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FAKE_GRAXPERT_VARIANT", raising=False)
    src, out, result = _run(tmp_path)
    assert result["is_cli"] is True
    assert result["raised"] is None
    assert result["saved"] == str(out)
    assert out.exists()
    before = read_fits(src)[0][0].astype(np.float64)
    after = read_fits(out)[0][0].astype(np.float64)
    slope_before = np.hypot(*_slope(before))
    slope_after = np.hypot(*_slope(after))
    assert slope_before > 1e-3
    assert slope_after < 0.1 * slope_before


def test_failing_script_variant_raises_the_graxpert_guard(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_GRAXPERT_VARIANT", "fail")
    _src, out, result = _run(tmp_path)
    assert result["is_cli"] is True
    assert result["saved"] is None
    assert result["raised"] is not None
    assert result["raised"].startswith("GraXpert did not change L master: ")
    assert not out.exists()
