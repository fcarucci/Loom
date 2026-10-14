"""Star detection on the stars layer (E3a), replacing PixInsight's StarDetector.

Ported from Loom ``script/lib/Sky.js`` (v0.4.1): ``Sky.detections`` (569-583),
``Sky.intensity`` (590-599), ``Sky.noiseSigma`` (602-607), ``Sky.brightMeasure``
(1131-1137) and ``Sky.eachInWindow`` (1139-1143). The JS handed the image to
PixInsight's StarDetector; here :func:`detect_stars` is a numpy detector with
the same output record (:class:`loomsiril.types.Detection`).

Pixel convention: pixel i's centre is at integer i, as everywhere in loomsiril.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from loomsiril.core import detection_rect, js_round, mad
from loomsiril.types import Detection

__all__ = [
    "intensity",
    "noise_sigma",
    "detect_stars",
    "bright_measure",
    "each_in_window",
    "SAMPLE_STEP",
    "BRIGHT_WINDOW",
]

#: Sky.noiseSigma reads every 7th sample of the buffer (a sparse, cheap sample).
SAMPLE_STEP: int = 7

#: Sky.brightMeasure looks 30 px either side of the position (the saturated core).
BRIGHT_WINDOW: int = 30

#: Smallest noise the detector uses: one 16-bit quantisation step, 1/65535 (E3e). A stars layer that is
#: mostly exact zeros (a noiseless field, or a StarNet layer after unscreen) has MAD 0, so noise_sigma is 0
#: and the threshold sky + k*noise would sit on the background: every faint Gaussian tail became a star
#: (129,080 detections for a 30-star field). No 16-bit input can express a smaller noise than this step.
NOISE_FLOOR: float = 1.0 / 65535.0

# 3x3 neighbours that come BEFORE a pixel in raster order (row-major), and those AFTER it.
# A plateau of equal values then has exactly one maximum: its first pixel in raster order.
_EARLIER = ((-1, -1), (-1, 0), (-1, 1), (0, -1))
_LATER = ((0, 1), (1, -1), (1, 0), (1, 1))


def intensity(planes: Sequence[np.ndarray]) -> np.ndarray:
    """``Sky.intensity``: the mean of the channels, float32, shaped like one plane.

    The JS accumulates ``b[i]/nc`` channel by channel into a Float32Array; this
    does the same accumulation in float32.
    """
    nc = len(planes)
    out = np.zeros(np.shape(planes[0]), dtype=np.float32)
    for p in planes:
        out += np.asarray(p, dtype=np.float32) / np.float32(nc)
    return out


def noise_sigma(buf: np.ndarray) -> float:
    """``Sky.noiseSigma``: robust noise of a buffer, 1.4826 x MAD over every 7th sample.

    Returns 0 when the MAD is exactly the ``|| 1`` fallback of :func:`loomsiril.core.mad`
    (a buffer with no spread), as the JS does (``Fly.mad(sample) == 1 ? 0 : ...``).
    """
    sample = np.asarray(buf).ravel()[::SAMPLE_STEP].astype(np.float64).tolist()
    m = mad(sample)
    return 0.0 if m == 1 else m


#: The 65th percentile, P50 + 0.3853 sigma under a normal distribution (Phi(0.3853) = 0.65).
_P65: float = 65.0
_PHI_INV_65: float = 0.3853204664

#: A layer counts as zero-inflated (clipped at 0 by unscreen) when at least this fraction of its sampled
#: pixels is exactly 0. Measured (R2-0, synthetic stars + noise): a noisy one-channel layer has about 0.45-0.50,
#: a noisy three-channel layer 0.10-0.17 (averaging the channels makes exact zeros rare), a noiseless layer
#: 0.37-0.55 but with a tiny estimate (< 1/65535, so the floor decides). The gate sits between 0.17 and 0.37.
ZERO_FRACTION_GATE: float = 0.3


def _upper_half_sigma(inten: np.ndarray) -> float:
    """Noise of a zero-inflated buffer from its upper half: (P65 - P50) / 0.3853 over every 7th sample.

    Not a JS function: the JS never thresholds its own noise (PixInsight's StarDetector found the stars),
    so this is a Python-side estimate for :func:`detect_stars` only.

    Why: a normal distribution clipped at zero keeps its upper half intact, so (P65 - P50) / 0.3853 recovers
    sigma. The MAD does not, where the median sits on the clip boundary: about half the pixels of a noisy
    one-channel stars layer are exactly 0 (unscreen clips the negative noise), so the MAD sees only the sliver
    around the median and reports ~27x too little noise (3.7e-5 for 1e-3).

    Gate: the estimate is used only when the sampled zero fraction is at least ZERO_FRACTION_GATE; otherwise it
    returns 0.0 and the MAD decides. Without the gate, P65 - P50 exceeds the MAD on an unclipped layer too (a
    three-channel layer: 4.6e-4 against a MAD of 3.9e-4), which raised the threshold and silently lost faint
    stars (nc3 150 bright stars, 5-sigma recall 65/84 -> 0/84).

    Choice P65 over P84: on crowded mono fields the star light in the upper tail biases the estimate high, and
    P84 is further into that tail. Measured (sigma 1e-3, 320x180, 3 seeds, 100 faint stars at 6/8/10 sigma;
    recall within 0.75 px): 40 bright stars at 10 sigma P84 27%, P65 53%,
    ideal 83%; 150 bright stars at 10 sigma P84 2%, P65 4%, ideal 60%. Bias remains: the caveat is in the
    docstring of detect_stars and docs/parity/e3a.md.
    """
    s = inten.ravel()[::SAMPLE_STEP].astype(np.float64)
    if float(np.mean(s == 0.0)) < ZERO_FRACTION_GATE:
        return 0.0
    p50, p65 = np.percentile(s, [50.0, _P65])
    return float((p65 - p50) / _PHI_INV_65)


def _smooth3x3(a: np.ndarray) -> np.ndarray:
    """3x3 box mean with the edge pixel held outward (numpy slicing, no loops over pixels)."""
    p = np.pad(a, 1, mode="edge")
    h, w = a.shape
    acc = np.zeros((h, w), dtype=np.float64)
    for dy in range(3):
        for dx in range(3):
            acc += p[dy:dy + h, dx:dx + w]
    return acc / 9.0


def detect_stars(planes: Sequence[np.ndarray], sigma_k: float = 5.0, fwhm: float = 3.0) -> list[Detection]:
    """Detections of the stars layer, brightest first, as ``Sky.detections`` returns them.

    Steps (each named for the JS it replaces):

    1. intensity = mean of the channels; smoothed = 3x3 box mean (edge held).
    2. sky = median of the intensity; noise = the larger of :func:`noise_sigma` and
       :func:`_upper_half_sigma` (R2-0: a zero-inflated mono layer), floored at :data:`NOISE_FLOOR`
       (E3e: a zero noise must not put the threshold on the sky).
    3. candidates = smoothed pixels above ``sky + sigma_k * noise`` that are the
       maximum of their 3x3 neighbourhood. Ties: a pixel must be strictly above
       the neighbours before it in raster order and at least equal to those after
       it, so a flat top yields one candidate, its first pixel (raster order).
       Off-image neighbours never block a border star.
    4. for each candidate: peak = smoothed value; core = pixels within
       ``max(2, fwhm)`` of the candidate; centroid = intensity-weighted mean over
       the core of ``(intensity - sky)`` clipped at 0; flux = sum of
       ``(intensity - sky)`` over the core; area = core pixels at or above half
       of ``(peak - sky)``.
    5. ``Detection(index, x, y, flux, rect=detection_rect(x, y, area), nmax=0)``,
       sorted by flux descending (stable, so raster order breaks ties), reindexed.

    ``nmax`` stays 0 ("not measured"), as with PixInsight 1.9's StarDetector.
    """
    inten = intensity(planes).astype(np.float64)
    h, w = inten.shape
    sky = float(np.median(inten))
    # R2-0: the upper-half estimate covers a zero-inflated (clipped) mono layer where the MAD collapses; it is 0
    # for any other layer (see _upper_half_sigma), so the max keeps the MAD there. E3e: never 0, so a flat
    # background cannot pass as stars.
    noise = max(noise_sigma(inten), _upper_half_sigma(inten), NOISE_FLOOR)
    smooth = _smooth3x3(inten)
    thr = sky + sigma_k * noise

    # Strict-maximum test on the smoothed image; off-image neighbours are -inf.
    padded = np.full((h + 2, w + 2), -np.inf)
    padded[1:-1, 1:-1] = smooth
    cand = smooth > thr
    for dy, dx in _EARLIER:
        cand &= smooth > padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
    for dy, dx in _LATER:
        cand &= smooth >= padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
    ys, xs = np.nonzero(cand)  # row-major: raster order

    # Core radius: max(2, fwhm) px, as a disc around the candidate.
    r = max(2.0, float(fwhm))
    R = int(math.floor(r))
    rows: list[tuple[float, float, float, int]] = []
    for y, x in zip(ys.tolist(), xs.tolist()):
        y0, y1 = max(0, y - R), min(h, y + R + 1)
        x0, x1 = max(0, x - R), min(w, x + R + 1)
        yy = np.arange(y0, y1, dtype=np.float64)[:, None]
        xx = np.arange(x0, x1, dtype=np.float64)[None, :]
        disk = (xx - x) ** 2 + (yy - y) ** 2 <= r * r
        above = inten[y0:y1, x0:x1] - sky
        peak = float(smooth[y, x])
        weight = np.where(disk, np.maximum(above, 0.0), 0.0)
        wsum = float(weight.sum())
        if wsum > 0:
            cx = float((weight * xx).sum()) / wsum
            cy = float((weight * yy).sum()) / wsum
        else:
            cx, cy = float(x), float(y)
        flux = float(np.where(disk, above, 0.0).sum())
        area = int(np.count_nonzero(disk & (above >= 0.5 * (peak - sky))))
        rows.append((cx, cy, flux, area))

    order = sorted(range(len(rows)), key=lambda k: -rows[k][2])
    out: list[Detection] = []
    for index, k in enumerate(order):
        cx, cy, flux, area = rows[k]
        out.append(Detection(index=index, x=cx, y=cy, flux=flux,
                             rect=detection_rect(cx, cy, area), nmax=0))
    return out


def each_in_window(w: int, h: int, cx: int, cy: int, R: int) -> tuple[slice, slice]:
    """``Sky.eachInWindow``: the (2R+1)-square about (cx, cy) clipped to the w x h image.

    The JS calls ``fn(index, dx, dy)`` per pixel; here the window is a slice pair
    ``(rows, cols)`` for a ``(h, w)`` plane. The offsets are ``rows - cy`` and
    ``cols - cx``: ``arange(rows.start, rows.stop) - cy`` and likewise for cols.
    """
    return (slice(max(0, cy - R), min(h, cy + R + 1)),
            slice(max(0, cx - R), min(w, cx + R + 1)))


def bright_measure(buf: np.ndarray, x: float, y: float) -> dict[str, float]:
    """``Sky.brightMeasure``: flux and core area of a star the detector missed, at (x, y).

    ``flux`` = light within 8 px (``dx*dx + dy*dy <= 64``) of the rounded position;
    ``area`` = pixels within 30 px at half its peak or more (the saturated core).
    ``peak`` starts at 0 as in the JS, so an all-negative window has peak 0.
    Returns ``{"flux": float, "area": int}``.
    """
    cx = int(js_round(x))
    cy = int(js_round(y))
    h, w = np.shape(buf)
    rs, cs = each_in_window(w, h, cx, cy, BRIGHT_WINDOW)
    win = np.asarray(buf, dtype=np.float64)[rs, cs]
    if win.size == 0:
        return {"flux": 0.0, "area": 0}
    dy = np.arange(rs.start, rs.stop, dtype=np.int64)[:, None] - cy
    dx = np.arange(cs.start, cs.stop, dtype=np.int64)[None, :] - cx
    near = dx * dx + dy * dy <= 64
    flux = float(np.sum(win[near]))
    # JS: `if (buf[i] > peak) peak = buf[i]` from 0; NaN never wins, so it is ignored.
    peak = max(0.0, float(np.max(np.where(np.isnan(win), -np.inf, win))))
    area = int(np.count_nonzero(win >= 0.5 * peak))
    return {"flux": flux, "area": area}
