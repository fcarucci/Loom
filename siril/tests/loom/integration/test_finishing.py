"""The finishing runners against the real tools: siril-cli (marker ``siril``) and the StarNet2 CLI (marker ``starnet``).

Siril: the stretch of a composite (MTF, and the keep-linear companion) and a Siril denoise run through
``finishing_runners`` inside siril-cli, checked against the numpy reference of the same stretch.
StarNet2: the star extraction of a 640x512 synthetic composite. Synthetic plates only; skipped when the tool is absent.
"""

from __future__ import annotations

import os
import shutil

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import finishing, levels, stretch
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import RecordingOps
from loomsiril.starnet_licence import STARNET_LICENCE_REV

from ..siril_run import run_pyscript

W, H = 320, 256
SCRIPT = r'''
import json
import sirilpy as s
from loomsiril.loom import finishing, levels
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
ctx = RunContext(ops=ops, work_dir="ROOT", log=siril.log, warn=siril.log)
stretch_cfg = {"stretch": True, "stretchMethod": "mtf", "keepLinear": True,
               "sharpenTool": "none", "starTool": "none", "noiseTool": "none"}
runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING, stretch_cfg, noun="composite")
out = runners["stretchRGB"]("ROOT/SRC", "ROOT/STRETCHED")
print("RESULT=" + json.dumps({"path": out.path, "linear": out.companions.get("linear"),
                              "skip_cache": out.skip_cache}), flush=True)
denoise_cfg = {"stretch": False, "sharpenTool": "none", "starTool": "none",
               "noiseTool": levels.NR_TOOL_SIRIL, "noiseLevel": "low"}
runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING, denoise_cfg, noun="composite")
out = runners["denoiseLinearRGB"]("ROOT/SRC", "ROOT/DENOISED")
print("DENOISE=" + json.dumps({"path": out.path, "skip_cache": out.skip_cache}), flush=True)
'''


def _composite(channels: int, h: int, w: int) -> list[np.ndarray]:
    """A linear composite: a gradient sky with a few Gaussian stars, screened over the sky."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    sky = 0.08 + 0.04 * xx / (w - 1)
    centres = [(h // 5, w // 6), (h // 2, w // 2), (3 * h // 4, w // 4), (h // 3, 5 * w // 6), (4 * h // 5, 2 * w // 3)]
    planes = []
    for c in range(channels):
        stars = np.zeros((h, w))
        for cy, cx in centres:
            stars += (0.4 + 0.1 * c) * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 1.6 ** 2))
        stars = np.clip(stars, 0.0, 1.0)
        planes.append((1.0 - (1.0 - sky) * (1.0 - stars)).astype(np.float32))
    return planes


def _write_composite(path, channels: int = 3, h: int = H, w: int = W) -> str:
    write_fits(path, _composite(channels, h, w), bitpix=-32)
    return os.fspath(path)


def _starnet_exe() -> str | None:
    env = os.environ.get("LOOMSIRIL_STARNET")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    return shutil.which("starnet2") or shutil.which("starnet++")


@pytest.mark.siril
def test_siril_stretch_and_denoise_through_the_runners(tmp_path) -> None:
    src = _write_composite(tmp_path / "SRC.fits")
    script = SCRIPT.replace("ROOT", str(tmp_path)).replace("SRC", "SRC.fits") \
        .replace("STRETCHED", "STRETCHED.fits").replace("DENOISED", "DENOISED.fits")
    run = run_pyscript(script, tmp_path).check()
    result = run.value("RESULT")
    assert result["skip_cache"] is False
    assert result["linear"] is not None and os.path.isfile(result["linear"])
    assert open(result["linear"], "rb").read() == open(src, "rb").read()

    planes, _ = read_fits(src)
    want = stretch.apply_mtf(planes, stretch.stretch_parameters_for_planes(planes, True, stretch.STRETCH_SKY_TARGET),
                             True)
    got, _ = read_fits(tmp_path / "STRETCHED.fits")
    for g, w in zip(got, want):
        assert np.allclose(g, w, atol=2e-6)

    denoise = run.value("DENOISE")
    assert denoise["skip_cache"] is False
    after, _ = read_fits(denoise["path"])
    assert any(not np.array_equal(a, b) for a, b in zip(after, planes))


@pytest.mark.starnet
def test_starnet_extract_on_a_640x512_composite(tmp_path) -> None:
    exe = _starnet_exe()
    if exe is None:
        pytest.skip("StarNet2 CLI not found")
    planes = _composite(3, 512, 640)
    src = tmp_path / "composite.fits"
    write_fits(src, planes, bitpix=-32)
    ctx = RunContext(ops=RecordingOps(tmp_path), work_dir=os.fspath(tmp_path),
                     tools={"starnet2": exe}, starnet_revision=STARNET_LICENCE_REV)
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          {"stretch": False, "sharpenTool": "none", "starTool": levels.STAR_TOOL_STARNET,
                                           "noiseTool": "none"}, noun="composite")
    out = runners["extractRGB"](os.fspath(src), os.fspath(tmp_path / "starless.fits"))
    assert out.skip_cache is False
    starless, _ = read_fits(out.path)
    stars, _ = read_fits(out.companions["stars"])
    assert len(starless) == len(stars) == 3
    assert starless[0].shape == (512, 640)
    for s_plane in stars:
        assert s_plane.min() >= 0.0 and s_plane.max() <= 1.0
        assert s_plane.max() > 0.05  # the synthetic stars are found and extracted
    for a, b in zip(starless, planes):
        assert float(a.max()) < float(b.max())
