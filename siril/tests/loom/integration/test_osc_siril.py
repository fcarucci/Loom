"""A colour group registered as one image inside a real siril-cli (marker ``siril``).

A synthetic ``OSC`` master (R, G, B planes of one sky) and an ``OSCNB`` master (H, O planes of the same sky shifted by
(3, 2) px) go through the run's own registration (``_Run.register`` on real plates): the OSC group is the reference
(its G) and the dual-band group is registered to it as one three-plane image with one transform. The registered H
and O plates sit on G's stars within 0.2 px and on each other within 0.05 px, and carry their master-derived FILTER
and LOOMSRC. A second run on the same cache folder loads the cached plates and sends Siril nothing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits

from .. import synth
from ..siril_run import run_pyscript

pytestmark = pytest.mark.siril

W, H = 480, 384
SHIFT = (3.0, 2.0)

SCRIPT = r'''
import json, os
import sirilpy as s
from loomsiril.loom import cache, config as loom_config, pipeline
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
ROOT = "ROOT_DIR"
CACHE = os.path.join(ROOT, "cache")
os.makedirs(CACHE, exist_ok=True)
sent = []
real_send = ops._send
ops._send = lambda text, *a, **k: (sent.append(text), real_send(text, *a, **k))[1]
ops.set_fits_compression(False)


def one_run(tag):
    work = os.path.join(ROOT, "work_" + tag)
    os.makedirs(work, exist_ok=True)
    cfg = loom_config.defaults()
    cfg.update({"paths": dict(cfg["paths"], OSC="OSC_PATH", OSCNB="NB_PATH"),
                "oscKinds": {"OSC": "broadband", "OSCNB": "dualband"}, "useCache": True, "cacheDir": CACHE,
                "outputDir": os.path.join(ROOT, "out"), "gradientTool": "none"})
    lines = []
    ctx = RunContext(ops=ops, work_dir=work, log=lines.append, warn=lines.append)
    state = pipeline._Run(ctx, cfg, cache.CacheStore(CACHE), CACHE, {})
    state.load_channels()
    before = len(sent)
    ref = state.register()
    commands = [t.split(" ")[0] for t in sent[before:]]
    plates = {key: state.plate_of(key) for key in state.chans}
    leftovers = sorted(os.path.relpath(os.path.join(d, f), work) + ("@" if os.path.islink(os.path.join(d, f)) else "")
                       for n in os.listdir(work) if n.startswith("register-")
                       for d, _dirs, files in os.walk(os.path.join(work, n)) for f in files + _dirs)
    return {"ref": ref, "plates": plates, "commands": commands, "keys": {k: c.current_key for k, c in state.chans.items()},
            "leftovers": leftovers}


# one printed line per field: Siril cuts a long log line
for tag in ("COLD", "WARM"):
    for field, value in one_run(tag.lower()).items():
        print("RESULT_" + tag + "_" + field + "=" + json.dumps(value), flush=True)
'''


def _centroid(img: np.ndarray, x: float, y: float, r: int = 5) -> tuple[float, float]:
    """Flux-weighted centroid of the background-subtracted window around (x, y)."""
    xi, yi = int(round(x)), int(round(y))
    win = img[yi - r:yi + r + 1, xi - r:xi + r + 1].astype(np.float64)
    weight = np.clip(win - np.median(win), 0.0, None)
    yy, xx = np.mgrid[yi - r:yi + r + 1, xi - r:xi + r + 1]
    total = weight.sum()
    return float((weight * xx).sum() / total), float((weight * yy).sum() / total)


def _masters(folder: Path) -> tuple[Path, Path, synth.Master]:
    """The OSC master (planes R, G, B), the OSCNB master (H, O and a spare copy of O), and the sky they share."""
    rgb = [synth.make_master(ch, w=W, h=H, seed=i + 1) for i, ch in enumerate("RGB")]
    nb = [synth.make_master(ch, w=W, h=H, seed=10 + i, shift=SHIFT) for i, ch in enumerate("HO")]
    common = {"INSTRUME": "ZWO ASI2600MC Pro"}
    osc_path, nb_path = folder / "osc_master.fits", folder / "nb_master.fits"
    write_fits(osc_path, [m.planes[0] for m in rgb], {**rgb[1].header, **common, "FILTER": "L-Pro"}, bitpix=-32)
    write_fits(nb_path, [nb[0].planes[0], nb[1].planes[0], nb[1].planes[0]],
               {**nb[0].header, **common, "FILTER": "L-eXtreme"}, bitpix=-32)
    return osc_path, nb_path, rgb[1]


def _bright(stars: np.ndarray, margin: int = 30, peak: float = 0.3) -> np.ndarray:
    keep = (stars[:, 2] > peak) & (stars[:, 0] > margin) & (stars[:, 0] < W - margin) \
        & (stars[:, 1] > margin) & (stars[:, 1] < H - margin)
    return stars[keep]


def test_a_dual_band_group_is_registered_onto_the_osc_green_as_one_image(tmp_path: Path) -> None:
    osc_path, nb_path, green = _masters(tmp_path)
    script = SCRIPT.replace("ROOT_DIR", str(tmp_path)).replace("OSC_PATH", str(osc_path)) \
        .replace("NB_PATH", str(nb_path))
    run = run_pyscript(script, tmp_path).check()
    fields = ("ref", "plates", "commands", "keys", "leftovers")
    cold = {f: run.value(f"RESULT_COLD_{f}") for f in fields}
    warm = {f: run.value(f"RESULT_WARM_{f}") for f in fields}

    assert cold["ref"] == warm["ref"] == "G"
    assert sorted(cold["plates"]) == ["B", "G", "H", "O", "R"]
    assert "register" in cold["commands"] and "rgbcomp" in cold["commands"], cold["commands"]
    assert cold["leftovers"] == [], cold["leftovers"]   # the temporary frames and their folder are gone

    g_plate = read_fits(cold["plates"]["G"])[0][0]
    stars = _bright(green.stars)
    assert len(stars) >= 5
    registered = {k: read_fits(cold["plates"][k]) for k in "HO"}
    for x, y, _ in stars:
        gx, gy = _centroid(g_plate, x, y)
        pos = {k: _centroid(registered[k][0][0], x, y) for k in "HO"}
        for k in "HO":
            assert abs(pos[k][0] - gx) < 0.2 and abs(pos[k][1] - gy) < 0.2, (k, x, y, pos[k], (gx, gy))
        assert abs(pos["H"][0] - pos["O"][0]) < 0.05 and abs(pos["H"][1] - pos["O"][1]) < 0.05, (x, y, pos)

    # the registered splits keep the master-derived provenance, not the temporary group image's
    for key, name, plane in (("H", "Ha", 0), ("O", "OIII", 1)):
        header = registered[key][1]
        assert header["FILTER"] == name and header["LOOMSRC"] == f"nb_master.fits|plane:{plane}", (key, header)

    # the warm run loads the cached splits: no Siril command, the same plates and keys
    assert warm["commands"] == [], warm["commands"]
    assert warm["plates"] == cold["plates"] and warm["keys"] == cold["keys"]
    for key in "HO":
        np.testing.assert_array_equal(read_fits(warm["plates"][key])[0][0], registered[key][0][0])
