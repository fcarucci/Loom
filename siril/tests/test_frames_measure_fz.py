"""Compressed frames through real siril-cli: a Rice-compressed copy made by Siril itself is measured like its plain
original, and read with the same pixels and orientation.

Siril writes the compressed copy (``setcompress 1 -type=rice 16`` then ``save``) inside the Siril script, so no
compression library is needed on the host. The file is renamed to ``*.fits.fz``. Marked siril: skipped locally, runs
in the Siril image.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import write_fits
from loom.siril_run import run_pyscript

W, H, N_STARS = 400, 300, 120
PLAIN = [f"f{i:02d}" for i in range(1, 8)]  # seven plain frames; f01 is also compressed by Siril


def _frame(path: Path, seed: int, fwhm: float) -> None:
    """A 400 x 300 16-bit mono frame of Gaussian stars at fixed positions (the same field for every frame)."""
    rng0 = np.random.default_rng(7)
    pos: list[tuple[float, float]] = []
    while len(pos) < N_STARS:
        x, y = rng0.uniform(12, W - 13), rng0.uniform(12, H - 13)
        if all((x - a) ** 2 + (y - b) ** 2 > 18 ** 2 for a, b in pos):
            pos.append((x, y))
    peaks = rng0.uniform(0.08, 0.7, N_STARS)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    img = np.full((H, W), 0.08) + 0.01 * xx / W
    sy = fwhm / 2.3548200450309493
    for (x, y), p in zip(pos, peaks):
        img += p * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sy * sy))
    img = np.clip(img + np.random.default_rng(seed).normal(0, 0.004, img.shape), 0, 1)
    write_fits(str(path), [img.astype(np.float32)], {"FILTER": "L", "EXPTIME": 120.0}, bitpix=16)


_SCRIPT = r'''
import json, os, sys
import numpy as np
import sirilpy as s
from loomsiril.frames_measure import measure_batch
from loomsiril.frames_render import read_quietly

base = sys.argv[1]
siril = s.SirilInterface()
siril.connect()
plain = os.path.join(base, "f01.fit")
siril.cmd('load "%s"' % plain)
siril.cmd("setcompress 1 -type=rice 16")
siril.cmd('save "comp"')
made = [n for n in os.listdir(base) if n.startswith("comp") and n.endswith(".fz")]
print("made=" + json.dumps(sorted(made)), flush=True)
fz = os.path.join(base, "f01.fits.fz")
os.rename(os.path.join(base, made[0]), fz)

names = ["f%02d" % i for i in range(1, 9)]
paths = [os.path.join(base, n + ".fit") for n in names[1:7]] + [plain, fz]  # eight inputs
res, problem = measure_batch(siril, paths, os.path.join(base, "work"))
print("problem=" + json.dumps(problem), flush=True)
for p, m in res.items():
    key = "fz" if p.endswith(".fz") else os.path.basename(p)[:-4]
    print("m." + key + "=" + json.dumps(m), flush=True)

a = read_quietly(plain, siril)
b = read_quietly(fz, siril)
same = a is not None and b is not None and a.shape == b.shape
print("rq.shape=" + json.dumps(None if a is None else list(a.shape)), flush=True)
print("rq.same=" + json.dumps(bool(same and np.array_equal(a, b))), flush=True)
print("rq.maxdiff=" + json.dumps(float(np.max(np.abs(a - b))) if same else None), flush=True)
'''


@pytest.mark.siril
def test_siril_compressed_frame_measures_and_reads_like_its_plain_copy(tmp_path: Path) -> None:
    for k, name in enumerate(PLAIN):
        _frame(tmp_path / f"{name}.fit", seed=200 + k, fwhm=3.0 + 0.05 * k)
    run = run_pyscript(_SCRIPT, tmp_path, args=[str(tmp_path)]).check()

    problems = [ln.split("=", 1)[1] for ln in run.lines("problem=")]
    assert problems == ["null"], run.tail()
    got = {ln[2:].split("=", 1)[0]: json.loads(ln.split("=", 1)[1]) for ln in run.lines("m.")}
    assert set(got) == {"f01", "f02", "f03", "f04", "f05", "f06", "f07", "fz"}, run.tail()
    assert got["fz"] is not None and got["f01"] is not None
    assert got["fz"]["fwhm"] == pytest.approx(got["f01"]["fwhm"], rel=0.02)
    assert abs(got["fz"]["stars"] - got["f01"]["stars"]) <= 2
    assert got["fz"]["background"] == pytest.approx(got["f01"]["background"], rel=0.02)

    assert run.value("rq.shape") == [H, W]
    assert run.value("rq.same") is True, run.tail()
