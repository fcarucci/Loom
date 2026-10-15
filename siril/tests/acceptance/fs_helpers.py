"""Shared fixtures for the Frame Selector acceptance tests: synthetic metrics, channels, FITS frames and
Siril-format text. Expected values in the tests are worked out by hand from the documented behaviour; nothing here
computes a verdict."""

from __future__ import annotations

import importlib.util
import math
import os
import zlib
from pathlib import Path
from typing import Any, Optional

import numpy as np

REPO = Path(__file__).resolve().parents[2]

# ---- synthetic metrics --------------------------------------------------------------------------------------


def metrics(fwhm: float = 3.0, ecc: float = 0.30, stars: float = 200, snr: float = 50.0, **extra: Any) -> dict[str, Any]:
    """One frame's measurements with the four ranking metrics; ``extra`` adds altitude, psfFlux, background..."""
    m: dict[str, Any] = {"fwhm": fwhm, "eccentricity": ecc, "stars": stars, "psfSNR": snr}
    m.update(extra)
    return m


def ladder(n: int = 20, **over: Any) -> list[dict[str, Any]]:
    """n good frames whose every metric steps evenly, so median and MAD are known by hand.

    fwhm 3.00 + 0.02 i, ecc 0.300 + 0.004 i, stars 200 - 2 i, psfSNR 50 - 0.5 i. For an even n the median is the mean
    of the two middle values and the MAD is n/4 steps (n = 20: 5 steps), i.e. sigma = 1.4826 * 5 * step.
    """
    out = []
    for i in range(n):
        out.append(metrics(3.0 + 0.02 * i, 0.300 + 0.004 * i, 200 - 2 * i, 50.0 - 0.5 * i))
    for k, v in over.items():
        for m in out:
            m[k] = v
    return out


def mad_sigma(values: list[float]) -> float:
    """1.4826 * median(|x - median|), the normalised MAD, with numpy (independent of the code under test)."""
    a = np.asarray(values, dtype=float)
    return float(1.4826 * np.median(np.abs(a - np.median(a))))


def channel(key: str, rows: list[Optional[dict[str, Any]]], settings: Optional[dict[str, Any]] = None,
            problems: Optional[list[str]] = None) -> dict[str, Any]:
    """A judged channel built through the public API: entries, new_channel, optional settings, recompute."""
    from loomsiril import frames

    entries = [{"path": f"/data/{key}/frame_{i:03d}.fit"} for i in range(len(rows))]
    by_path = {e["path"]: rows[i] for i, e in enumerate(entries)}
    ch = frames.new_channel(key, entries, by_path, problems)
    if settings:
        ch["settings"].update(settings)
    return frames.recompute(ch)


def states(ch: dict[str, Any]) -> list[str]:
    return [r["state"] for r in ch["rows"]]


def rejected_indices(ch: dict[str, Any]) -> list[int]:
    return [i for i, r in enumerate(ch["rows"]) if r["state"] == "rejected"]


# ---- anomaly-tag channels -----------------------------------------------------------------------------------

FIVE_MIN_MS = 5 * 60 * 1000


def sharp_channel(n: int = 20, altitude: Optional[float] = None) -> list[dict[str, Any]]:
    """n sharp, round, bright frames with small steady variation (so every sigma rule has a spread).

    fwhm 3.00..3.08 (lower quartile 3.00), ecc 0.28..0.32 (median 0.30), stars 196..204 (median 200),
    psfFlux 980..1020 (median 1000), background 100 (median 100). Optional altitude on every frame.
    """
    out = []
    for i in range(n):
        j = i % 5
        m = metrics(3.0 + 0.02 * j, 0.28 + 0.01 * j, 196 + 2 * j, 50.0, psfFlux=980.0 + 10 * j, background=100.0)
        if altitude is not None:
            m["altitude"] = altitude
        out.append(m)
    return out


def times_for(n: int) -> list[int]:
    """Frame times five minutes apart, in listing order."""
    return [1_800_000_000_000 + i * FIVE_MIN_MS for i in range(n)]


# ---- synthetic FITS frames ----------------------------------------------------------------------------------


def write_light(folder: Path, name: str, header: dict[str, Any], w: int = 16, h: int = 12, planes: int = 1) -> str:
    """A small FITS file with the given header keywords; returns its path as text."""
    from loomsiril.io_fits import write_fits

    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(zlib.crc32(name.encode()))
    data = [rng.uniform(0.05, 0.1, (h, w)).astype(np.float32) for _ in range(planes)]
    p = folder / name
    write_fits(p, data, header, bitpix=16)
    return str(p)


def star_field_frame(path: Path, fwhm: float = 3.0, elong: float = 1.0, scale: float = 1.0, bg: float = 0.08,
                     nstars: int = 120, seed: int = 100, header: Optional[dict[str, Any]] = None) -> None:
    """A 400 x 300 mono 16-bit FITS of Gaussian stars at fixed positions (the same field for every frame), for real
    Siril runs. ``elong`` is the ratio of the x width to the y width; ``scale`` multiplies star peaks."""
    from loomsiril.io_fits import write_fits

    W, H = 400, 300
    r0 = np.random.default_rng(7)
    pos: list[tuple[float, float]] = []
    while len(pos) < 120:
        x, y = r0.uniform(12, W - 13), r0.uniform(12, H - 13)
        if all((x - a) ** 2 + (y - b) ** 2 > 18 ** 2 for a, b in pos):
            pos.append((x, y))
    peaks = r0.uniform(0.08, 0.7, 120)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    img = np.full((H, W), bg) + 0.01 * xx / W
    sy = fwhm / 2.3548200450309493
    sx = sy * elong
    for (x, y), p in list(zip(pos, peaks))[:nstars]:
        img += p * scale * np.exp(-((xx - x) ** 2 / (2 * sx * sx) + (yy - y) ** 2 / (2 * sy * sy)))
    img = np.clip(img + np.random.default_rng(seed).normal(0, 0.004, img.shape), 0, 1)
    hdr = {"FILTER": "L", "EXPTIME": 120.0, "XBINNING": 1, "YBINNING": 1, "IMAGETYP": "Light"}
    hdr.update(header or {})
    write_fits(path, [img.astype(np.float32)], hdr, bitpix=16)


# ---- Siril output text (formats as Siril 1.4 writes them) ---------------------------------------------------


def seq_text(rows: list[Optional[tuple]], medians: list[tuple[float, float]], name: str = "fs_",
             layer: int = 0) -> str:
    """A ``.seq`` file after ``register -2pass``: S, L, one I line per image, one R line per image
    (fwhm, wfwhm, roundness, quality, background, nbstars; ``None`` is the all-zero line of a failed registration)
    and one M line per image (median, bgnoise)."""
    n = len(rows)
    out = ["#Siril sequence file. Contains list of images, selection, registration data and statistics",
           f"S '{name}' 1 {n} {n} 5 0 6 0 0 0", "L 3" if layer else "L 1"]
    out += [f"I {i + 1} 1" for i in range(n)]
    ident = "H 1 0 0 0 1 0 0 0 1"
    for r in rows:
        if r is None:
            out.append(f"R{layer} 0 0 0 0 0 0 H 0 0 0 0 0 0 0 0 0")
        else:
            fwhm, wfwhm, roundness, quality, bg, nb = r
            out.append(f"R{layer} {fwhm} {wfwhm} {roundness} {quality} {bg} {nb} {ident}")
    for i, (med, bgnoise) in enumerate(medians):
        out.append(f"M{layer}-{i} 120000 120000 {med + 100} {med} 2000.5 -999999 -999999 -999999 -999999 -999999 "
                   f"4222 49199 65535 {bgnoise}")
    return "\n".join(out) + "\n"


def star_list_text(stars: list[tuple[float, float]]) -> str:
    """A Siril star list; each star is (amplitude A, magnitude)."""
    out = [f"# {len(stars)} stars found using the following parameters:",
           "# sigma=1.00 roundness=0.50 radius=5 relax=0 profile=0 minbeta=1.5 max_stars=2000 layer=0 minA=0.00 "
           "maxA=0.00 maxR=1.00",
           "# star#\tlayer\tB\tA\tbeta\tX\tY\tFWHMx [px]\tFWHMy [px]\tFWHMx [\"]\tFWHMy [\"]\tangle\tRMSE\tmag\tSat\tProfile\tRA\tDec"]
    for i, (a, mag) in enumerate(stars):
        out.append(f"{i + 1}\t0\t5289.853251\t{a}\t     -1.00\t     23.86\t     18.06\t      3.04\t      3.03\t"
                   f"     -1.00\t     -1.00\t69.14\t 2.398e+02\t    {mag}\t1\tGaussian\t9990000000.000000\t9990000000.000000")
    return "\n".join(out) + "\n"


def load_siril_run():
    """tests/loom/siril_run.py by path (that folder is a package; this one is not)."""
    spec = importlib.util.spec_from_file_location("fs_siril_run", REPO / "tests" / "loom" / "siril_run.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def approx_equal(a: float, b: float, rel: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-12)


def listing(folder: Path) -> list[str]:
    """Every file below a folder, relative, sorted: to check that nothing was written."""
    return sorted(os.path.relpath(os.path.join(d, f), folder) for d, _, fs in os.walk(folder) for f in fs)
