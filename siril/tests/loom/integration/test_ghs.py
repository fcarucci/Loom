"""GHS stretch of ``run_ghs_stretch`` against a real siril-cli (marker ``siril``).

A synthetic mono master and a three-plane composite are stretched inside Siril. Checked: the output is monotonic in
the input per channel, input 0 stays 0, two runs are bit-identical, and the sky lands near the 0.25 target (the
median of the mono plate; the mean of the channel medians of the composite, which is stretched linked).
"""

from __future__ import annotations

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import levels

from .. import synth
from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

W, H = 320, 256
SKY_TARGET = 0.25
TOL = 0.03

SCRIPT = r'''
import json
import sirilpy as s
from loomsiril.loom.ghs import run_ghs_stretch
from loomsiril.loom.levels import stretch_params
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = "ROOT"
linked = LINKED
params = stretch_params({"stretch": True, "stretchMethod": "ghs"}, linked)
res = run_ghs_stretch(ops, root + "/SRC", root + "/DST", params, linked, False, log=siril.log)
print("RESULT=" + json.dumps(res), flush=True)
'''


def _composite_input(tmp_path, name: str = "rgb.fits"):
    planes = []
    header = None
    for ch, seed in (("R", 3), ("G", 4), ("B", 5)):
        m = synth.make_master(ch, w=W, h=H, seed=seed)
        planes.append(m.planes[0])
        header = header or m.header
    # exact zeros in a corner: a stretch must keep them at zero
    for p in planes:
        p[:12, :12] = 0.0
    path = tmp_path / name
    write_fits(path, planes, header, bitpix=-32)
    return path


def _mono_input(tmp_path, name: str = "mono.fits"):
    m = synth.make_master("L", w=W, h=H)
    m.planes[0][:12, :12] = 0.0
    return synth.write_master(tmp_path / name, m)


def _run(tmp_path, src, out_name: str, linked: bool):
    script = (SCRIPT.replace("ROOT", str(tmp_path)).replace("SRC", src.name).replace("DST", out_name)
              .replace("LINKED", "True" if linked else "False"))
    run = run_pyscript(script, tmp_path).check()
    return run.value("RESULT")


def _monotonic_per_channel(src_planes, out_planes) -> None:
    for a, b in zip(src_planes, out_planes):
        x, y = a.ravel().astype(np.float64), b.ravel().astype(np.float64)
        order = np.argsort(x, kind="stable")
        assert np.all(np.diff(y[order]) >= -1e-6)


def test_mono_ghs_stretch_is_monotonic_keeps_zero_and_lands_on_the_sky_target(tmp_path) -> None:
    src = _mono_input(tmp_path)
    out = tmp_path / "mono_out.fits"
    res = _run(tmp_path, src, "mono_out.fits", linked=False)
    assert res == {"out": str(tmp_path / "mono_out.fits"), "linear": None}

    in_planes, _ = read_fits(src)
    out_planes, _ = read_fits(out)
    _monotonic_per_channel(in_planes, out_planes)
    assert np.all(out_planes[0][:12, :12] == 0.0)
    assert abs(float(np.median(out_planes[0])) - SKY_TARGET) <= TOL


def test_composite_ghs_stretch_is_linked_monotonic_and_lands_on_the_sky_target(tmp_path) -> None:
    src = _composite_input(tmp_path)
    out = tmp_path / "rgb_out.fits"
    _run(tmp_path, src, "rgb_out.fits", linked=True)

    in_planes, _ = read_fits(src)
    out_planes, _ = read_fits(out)
    assert len(out_planes) == 3
    _monotonic_per_channel(in_planes, out_planes)
    for p in out_planes:
        assert np.all(p[:12, :12] == 0.0)
    channel_medians = [float(np.median(p)) for p in out_planes]
    assert abs(float(np.mean(channel_medians)) - SKY_TARGET) <= TOL


def test_two_ghs_runs_are_bit_identical(tmp_path) -> None:
    src = _mono_input(tmp_path)
    _run(tmp_path, src, "first.fits", linked=False)
    _run(tmp_path, src, "second.fits", linked=False)
    first, h1 = read_fits(tmp_path / "first.fits")
    second, h2 = read_fits(tmp_path / "second.fits")
    # DATE is the time Siril wrote the file; every other header card and every sample must match
    diff = {k: (h1.get(k), h2.get(k)) for k in set(h1) | set(h2) if k != "DATE" and h1.get(k) != h2.get(k)}
    assert diff == {}
    assert all(np.array_equal(a, b) for a, b in zip(first, second))
