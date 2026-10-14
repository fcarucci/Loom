"""Star-model primitives: deblending rules and the image helpers under them (E3b).

Ports Fly.js 1825-2035 (spike angles, fragments, core attachment, spike fits,
the local radial profile, the deblend share) and the image helpers of Sky.js
(585-770 and 971-983: intensity, noise, rings, core labels, readers, medians).
The star-model fitting itself (Sky.sprites and friends) is E3c, built on these.

Conventions (see docs/parity/e3b.md):

* Images and patches are ``(h, w)`` numpy arrays, one plane per channel. JS
  ``Sky.fn(buf, w, h, ...)`` becomes ``fn(buf, ...)``: the buffer carries its
  own shape, so ``w`` and ``h`` are read from ``buf.shape`` (R1-d).
* Angles of spikes returned by :func:`spike_angles` are 1-degree bin indices
  (as the JS returns them); spike records carry radians.
* JS ``undefined``/``null`` results are ``None``; JS ``NaN`` is ``float('nan')``.
"""

from __future__ import annotations

import functools
import math
from typing import Any, Callable, NamedTuple, Optional, Sequence

import numpy as np

from loomsiril.core import clamp, grown_rect, js_round, mad, median, smoothstep
from loomsiril.jsmath import js_max, js_min, js_or
from loomsiril.types import Detection, SpikeReach, SpriteMatch, StarModel

__all__ = [
    "SPIKE_WINDOW",
    "SPIKE_FIT",
    "SETTLE_WINDOW",
    "DEBLEND_CAP",
    "FWHM_DEFAULT",
    "HALO_MAX",
    "SPIKE_MAX",
    "spike_angles",
    "local_peak",
    "is_fragment",
    "fragment_filter",
    "attach_to_cores",
    "lateral_sigma",
    "drop_borrowed_spikes",
    "fit_spike",
    "fit_spike_pair",
    "local_profile",
    "deblend",
    "intensity",
    "noise_sigma",
    "other_star",
    "ring_start",
    "ring_stats",
    "core_labels",
    "reader",
    "sparse_median",
    "bilinear",
    "smooth_profile",
]

# ---------------------------------------------------------------------------
# Constants (Sky.js 585-587, Fly.js 1825-2035)

FWHM_DEFAULT: float = 3  # px, when the caller has no measurement (Sky.FWHM_DEFAULT)
HALO_MAX: float = 150  # px, the largest halo half-width measured (Sky.HALO_MAX)
SPIKE_MAX: float = 600  # px, the longest spike followed; spikes run far beyond halos (Sky.SPIKE_MAX)

SPIKE_WINDOW: int = 8  # samples averaged either side along a spike for its detection threshold (Fly.SPIKE_WINDOW)
SPIKE_FIT: int = 20  # inner spike samples a star's spike scale is fitted over (Fly.SPIKE_FIT)
SETTLE_WINDOW: int = 4  # rings averaged on each side of the "has it stopped falling" test (Fly.SETTLE_WINDOW)
DEBLEND_CAP: float = 2  # the most light the models at a pixel take, as a multiple of what they predict (Fly.DEBLEND_CAP)

_NAN = float("nan")
_INF = float("inf")


# ---------------------------------------------------------------------------
# JS number semantics kept where Python's builtins differ


def _max0(v: float) -> float:
    """``Math.max(0, v)``."""
    return js_max(0.0, v)


# ---------------------------------------------------------------------------
# Spikes and fragments (Fly.js 1825-1870)


def spike_angles(prof: Sequence[float], noise: float) -> list[int]:
    """Fly.spikeAngles: 1-degree bins of an angular profile that are spikes.

    A bin is a spike when it stands above ``max(6*noise, 0.1*top)`` and is the
    highest of its +-3 neighbours (wrapping at 360). Spikes are lines through
    the star, so a bin keeps only when a bin opposite it (within 2 degrees)
    also qualifies: a neighbouring star on one side is not a spike.
    """
    arr = np.asarray(prof, dtype=np.float64)
    n = int(arr.shape[0])
    top = float(np.max(arr)) if n else -_INF
    thr = js_max(6 * noise, 0.1 * top)
    out: list[int] = []
    for a in range(n):
        v = float(arr[a])
        peak = v > thr
        d = -3
        while d <= 3 and peak:
            if d != 0 and float(arr[(a + d + n) % n]) > v:
                peak = False
            d += 1
        if peak:
            out.append(a)
    # spikes are lines through the star: keep a direction only with its
    # opposite (a neighbouring star on one side read as a spike and was
    # carried off with the bright star)
    half = n / 2

    def opposed(a: int) -> bool:
        for b in out:
            d = abs(math.fmod(b - a + 3 * n / 2, n) - half)
            if abs(d - half) <= 2 or d >= half - 2:
                return True
        return False

    return [a for a in out if opposed(a)]


def _window_max(value: Callable[[int, int], float], x: float, y: float) -> float:
    """The largest value of the 3x3 pixels about the rounded (x, y), NaN ignored as the JS max is not (``v0``)."""
    X = int(js_round(x))
    Y = int(js_round(y))
    v0 = -_INF
    for j in (-1, 0, 1):
        for i in (-1, 0, 1):
            v0 = js_max(v0, value(X + i, Y + j))
    return v0


def local_peak(value: Callable[[int, int], float], x: float, y: float, rad: float) -> bool:
    """Fly.localPeak: is (x, y) a local peak of ``value(x, y)``?

    The brightest pixel of its 3x3 (a centroid need not fall on the peak pixel)
    is no fainter than any pixel within ``rad``. A point on a brighter star's
    slope or spike has brighter pixels toward that star.
    """
    X = int(js_round(x))
    Y = int(js_round(y))
    v0 = _window_max(value, x, y)
    R = math.ceil(rad)
    for j in range(-R, R + 1):
        for i in range(-R, R + 1):
            if (abs(i) > 1 or abs(j) > 1) and i * i + j * j <= rad * rad and value(X + i, Y + j) > v0:
                return False
    return True


def is_fragment(d: Detection, dets: Sequence[Detection], value_at: Callable[[int, int], float], px: float) -> bool:
    """Fly.isFragment: is detection ``d`` part of a brighter star?

    It is a fragment when it is no local peak (within ``max(2, px)``), or it
    lies in a brighter detection's core (grown by one FWHM) where that one is
    at least as bright: a saturated core is flat, so every point of it ties.
    Catalogue-added bright stars (``from_gaia``) are stars.

    This is the plain O(len(dets)) port; :func:`fragment_filter` is the
    subquadratic filter the pipeline uses.
    """
    if d.from_gaia:
        return False
    if not local_peak(value_at, d.x, d.y, js_max(2, px)):
        return True
    v0 = _window_max(value_at, d.x, d.y)
    for b in dets:
        if b is d or not (b.flux > d.flux):
            continue
        core = 0.5 * (b.rect.x1 - b.rect.x0) + px
        if math.hypot(b.x - d.x, b.y - d.y) <= core and value_at(int(js_round(b.x)), int(js_round(b.y))) >= v0:
            return True
    return False


def _fragment_index(dets: Sequence[Detection], px: float) -> tuple[dict, float, list[int], list[float]]:
    """A grid over ``dets`` for :func:`fragment_filter` (E3e).

    Returns ``(cells, cell, rest, cores)``: ``cores[k]`` is detection k's core radius
    (``0.5 * rect width + px``, as is_fragment uses it); ``cells`` maps a grid cell to the indices of the
    detections whose position falls in it; ``rest`` holds the indices with a non-finite position or core,
    which every query checks. The cell is at least twice the largest core, so a detection within its core of
    a point lies in one of the 3x3 cells about that point.
    """
    cores = [0.5 * (b.rect.x1 - b.rect.x0) + px for b in dets]
    cell = max([1.0] + [2.0 * c for c in cores if math.isfinite(c)])
    cells: dict[tuple[int, int], list[int]] = {}
    rest: list[int] = []
    for k, b in enumerate(dets):
        if math.isfinite(b.x) and math.isfinite(b.y) and math.isfinite(cores[k]):
            cells.setdefault((math.floor(b.x / cell), math.floor(b.y / cell)), []).append(k)
        else:
            rest.append(k)
    return cells, cell, rest, cores


def _grid_candidates(d: Detection, dets: Sequence[Detection], index: tuple[dict, float, list[int], list[float]]) -> Sequence[int]:
    """Indices that may hold a brighter core over ``d``: the 3x3 grid cells about it and the unplaceable ``rest``.

    A non-finite position has no cell, so every detection is a candidate (the JS scan).
    """
    cells, cell, rest, _ = index
    if not (math.isfinite(d.x) and math.isfinite(d.y)):
        return range(len(dets))
    cx = math.floor(d.x / cell)
    cy = math.floor(d.y / cell)
    candidates = list(rest)
    for j in (-1, 0, 1):
        for i in (-1, 0, 1):
            candidates.extend(cells.get((cx + i, cy + j), ()))
    return candidates


def _brighter_core_by_grid(d: Detection, dets: Sequence[Detection], value_at: Callable[[int, int], float],
                           index: tuple[dict, float, list[int], list[float]]) -> bool:
    """is_fragment's last test (a brighter core holds ``d``), over the grid's candidates only."""
    cores = index[3]
    v0 = _window_max(value_at, d.x, d.y)
    for k in _grid_candidates(d, dets, index):
        b = dets[k]
        if b is d or not (b.flux > d.flux):
            continue
        if math.hypot(b.x - d.x, b.y - d.y) <= cores[k] and value_at(int(js_round(b.x)), int(js_round(b.y))) >= v0:
            return True
    return False


def fragment_filter(dets: Sequence[Detection], value_at: Callable[[int, int], float], px: float) -> list[Detection]:
    """The detections :func:`is_fragment` does not reject, in their order (starlayer's filter, E3e).

    Gives ``[d for d in dets if not is_fragment(d, dets, value_at, px)]`` on the list as it is when the call
    starts, but the brighter detections of each one come from a grid built here, once, so the cost is not
    quadratic. Nothing is kept between calls: a later call sees the detections as they are then.
    """
    index = _fragment_index(dets, px)
    kept: list[Detection] = []
    for d in dets:
        if d.from_gaia:
            kept.append(d)
            continue
        if not local_peak(value_at, d.x, d.y, js_max(2, px)):
            continue
        if _brighter_core_by_grid(d, dets, value_at, index):
            continue
        kept.append(d)
    return kept


def attach_to_cores(sprites: Sequence[SpriteMatch], extras: Sequence[StarModel]) -> list[StarModel]:
    """Fly.attachToCores: missed sources inside a moving star's core join its model.

    Each extra goes to the nearest sprite whose core (``model.core``) holds it,
    appended to ``owner.parts`` (the list is created when absent). Returns the
    extras that stay unattached.
    """
    kept: list[StarModel] = []
    for e in extras:
        owner: Optional[StarModel] = None
        for s in sprites:
            m = s.det.model
            if math.hypot(e.x - m.x, e.y - m.y) <= m.core and (
                owner is None or math.hypot(e.x - m.x, e.y - m.y) < math.hypot(e.x - owner.x, e.y - owner.y)
            ):
                owner = m
        if owner is not None:
            if owner.parts is None:
                owner.parts = []
            owner.parts.append(e)
        else:
            kept.append(e)
    return kept


def lateral_sigma(p: Sequence[float], step: float) -> Optional[float]:
    """Fly.lateralSigma: a spike's sigma from its profile across it.

    ``p`` is sampled every ``step`` px, centred on the spike. The width is the
    full width at half maximum above the profile's own baseline (its outer two
    px on each side), over 2.3548. None when there is no peak.
    """
    arr = np.asarray(p, dtype=np.float64)
    n = int(arr.shape[0])
    mid = (n - 1) / 2
    edge = max(1, int(js_round(2 / step)))
    base = 0.0
    for k in range(edge):
        base += float(arr[k]) + float(arr[n - 1 - k])
    base /= 2 * edge
    q = arr - base
    peak = float(np.max(q)) if n else -_INF
    if not (peak > 1e-9):
        return None

    def half(direction: int) -> float:
        k = int(js_round(mid))
        while k + direction >= 0 and k + direction < n:
            if q[k + direction] < peak / 2:
                return (abs(k - mid) + (q[k] - peak / 2) / (q[k] - q[k + direction])) * step
            k += direction
        return mid * step

    return (half(-1) + half(1)) / 2.3548


def _parallel(a: float, b: float) -> bool:
    """Two spike directions (radians) are parallel: they agree within 2 degrees, lines not rays."""
    d = abs(math.fmod(abs(a - b), math.pi))
    return min(d, math.pi - d) < 2 * math.pi / 180


def _spikes_covering(m: StarModel, bm: StarModel) -> list[float]:
    """Angles of the spikes of brighter model ``bm`` that lie on star ``m``: its axis within the spike's half-width,
    between the spike's start and reach."""
    dx = m.x - bm.x
    dy = m.y - bm.y
    half = max(2.0, 3 * js_or(bm.spike_sigma, 1))
    covering: list[float] = []
    for sp in bm.spikes:
        c = math.cos(sp.angle)
        s = math.sin(sp.angle)
        along = dx * c + dy * s
        if along >= sp.start and along <= sp.reach and abs(-dx * s + dy * c) <= half:
            covering.append(sp.angle)
    return covering


def _borrowed_angles(d: Detection, spiky: Sequence[Detection]) -> list[float]:
    """Angles of the spikes that star ``d`` borrows: those of every brighter star in ``spiky`` it sits on."""
    borrowed: list[float] = []
    for b in spiky:
        if b is d or not (b.flux > d.flux):
            continue
        borrowed.extend(_spikes_covering(d.model, b.model))
    return borrowed


def _drop_spikes(d: Detection, borrowed: Sequence[float]) -> None:
    """Remove the spikes of ``d`` parallel to a borrowed angle; the support and the halo follow what is left."""
    m = d.model
    m.spikes = [sp for sp in m.spikes if not any(_parallel(a, sp.angle) for a in borrowed)]
    reach = 0.0
    for sp in m.spikes:
        reach = js_max(reach, sp.reach)
    m.support = js_max(m.rmax, reach) + 1
    if d.halo is not None:
        d.halo.radius = m.support
        d.halo.outer = m.support
        d.halo.spikes = [SpikeReach(angle=sp.angle, reach=sp.reach) for sp in m.spikes]


def drop_borrowed_spikes(dets: Sequence[Detection]) -> None:
    """Fly.dropBorrowedSpikes: a star lying on a brighter star's spike drops that spike.

    The light there is the brighter star's, and owning it tore a stretch of
    spike off the bright star when the two moved apart. A spike is borrowed when
    the star sits on a brighter star's spike (within ``max(2, 3*spikeSigma)``
    of its axis, between its start and reach). Parallel spikes go, the support
    shrinks to what is left, and the halo (``d.halo``) is rewritten to match.
    Rewrites in place. ``dets`` carry flux, model and halo.
    """
    spiky = [d for d in dets if d.model is not None and len(d.model.spikes) > 0]
    for d in spiky:
        borrowed = _borrowed_angles(d, spiky)
        if borrowed:
            _drop_spikes(d, borrowed)


def fit_spike(e: Sequence[float], T: Sequence[float], start: int, sigma: float) -> dict[str, Any]:
    """Fly.fitSpike: one star's spike as part of the PSF.

    ``e[k]`` is its excess at r = start + k; ``T[r]`` the image's spike falloff
    (per unit scale). The scale is the median of e/T over the inner SPIKE_FIT
    samples, so light borrowed from a neighbour there does not count, and
    nothing further out does at all. The amplitude follows the falloff and
    reaches as far as it stands above the noise of a spike averaged over a
    ``2*SPIKE_WINDOW + 1`` window. Returns ``{scale, amp, reach}``; ``amp`` is
    a float64 array, ``reach`` -1 when there is no spike.
    """
    tarr = np.asarray(T, dtype=np.float64)
    ratios: list[float] = []
    for k in range(min(SPIKE_FIT, len(e))):
        if start + k < tarr.shape[0] and tarr[start + k] > 0:
            ratios.append(float(e[k]) / float(tarr[start + k]))
    scale = median(ratios) if ratios else 0.0
    n = 2 * SPIKE_WINDOW + 1
    floor = 3 * 1.2247 * sigma / math.sqrt(n)
    amp: list[float] = []
    reach = -1
    # the fitted scale itself must stand out above its median's noise, measured from the ratios' own
    # scatter: dividing by a falloff that shrinks outward makes the outer ratios far noisier than sigma/T[start]
    noise = 1.2533 * mad(ratios) / math.sqrt(len(ratios)) if ratios else _INF
    if scale > 3 * noise:
        k = 0
        while start + k < tarr.shape[0] and scale * tarr[start + k] > floor:
            amp.append(scale * float(tarr[start + k]))
            reach = k
            k += 1
    return {"scale": scale, "amp": np.asarray(amp, dtype=np.float64), "reach": reach}


def fit_spike_pair(e1: Sequence[float], e2: Sequence[float], T: Sequence[float], start: int, sigma: float) -> dict[str, Any]:
    """Fly.fitSpikePair: an opposite pair of spikes is equal by nature, so both take the stronger side's fit."""
    a = fit_spike(e1, T, start, sigma)
    b = fit_spike(e2, T, start, sigma)
    return b if b["scale"] > a["scale"] else a


def local_profile(raw: Sequence[float], min_r: int, noise: float) -> dict[str, Any]:
    """Fly.localProfile: a star's radial profile on its local sky.

    ``raw[r]`` is the ring median above the global sky. The profile ends where it
    stops falling: the mean of the next W rings is no more than 3 sigma (of that
    difference) above the mean of the W after them. The later window is the
    local sky; the profile is raw minus that, and reaches zero at ``rmax``.
    ``settled`` is False when raw ran out first. Returns
    ``{prof (float64 array), rmax, sky, settled}``.
    """
    W = SETTLE_WINDOW
    rawl = [float(v) for v in raw]

    def mean(a: int, b: int) -> float:
        s = 0.0
        for k in range(a, b):
            s += rawl[k]
        return s / (b - a)

    r = max(1, int(min_r))
    while r + 2 * W <= len(rawl):
        ring_noise = 1.2533 * noise / math.sqrt(2 * math.pi * r)
        limit = 3 * ring_noise * math.sqrt(2) / math.sqrt(W)
        near = mean(r, r + W)
        far = mean(r + W, r + 2 * W)
        if near - far <= limit:
            rmax = r + W
            prof = [_max0(rawl[k] - far) if k < rmax else 0.0 for k in range(rmax + 1)]
            return {"prof": np.asarray(prof, dtype=np.float64), "rmax": rmax, "sky": far, "settled": True}
        r += 1
    sky = js_min(0.0, rawl[-1]) if rawl else 0.0
    prof = [_max0(v - sky) for v in rawl]
    return {"prof": np.asarray(prof, dtype=np.float64), "rmax": len(rawl), "sky": sky, "settled": False}


def deblend(Mi: float, Msum: float, L: float, sigma: float) -> float:
    """Fly.deblend: the light one star takes at one pixel.

    ``Mi`` is its model there, ``Msum`` all the models there, ``L`` the pixel's
    light (stars layer above the sky), ``sigma`` the noise. Where the stars stand
    well above the noise the star takes its model's share of the data; where they
    have faded into it, only its model, so the noise stays where it is. The
    shares add up to the pixel's light, but the light a pixel is given is capped
    at DEBLEND_CAP times what the models predict there.
    """
    if not (Msum > 0) or not (Mi > 0):
        return 0.0
    ratio = Msum / sigma if sigma != 0 else _INF
    a = smoothstep(1, 5, ratio)
    return a * (Mi / Msum) * js_min(L, DEBLEND_CAP * Msum) + (1 - a) * Mi


# ---------------------------------------------------------------------------
# Image helpers (Sky.js 585-770, 971-983)


def intensity(planes: Sequence[np.ndarray]) -> np.ndarray:
    """Sky.intensity: the mean of the channels, float32, shaped like one plane.

    The JS reads each channel into a buffer and adds ``b[i]/nc`` in float32, one
    channel after another; this does the same (``out += plane / nc``). ``planes``
    stands in for the JS image's ``getSamples`` (one ``(h, w)`` array per channel).
    """
    nc = len(planes)
    out = np.zeros(np.asarray(planes[0]).shape, dtype=np.float32)
    nc32 = np.float32(nc)
    for plane in planes:
        out += np.asarray(plane, dtype=np.float32) / nc32
    return out


def noise_sigma(buf: np.ndarray) -> float:
    """Sky.noiseSigma: 1.4826 x MAD of every 7th sample of the flat buffer; 0 when the MAD is 0."""
    sample = np.asarray(buf).ravel()[::7].astype(np.float64).tolist()
    m = mad(sample)
    return 0.0 if m == 1 else m


def other_star(labels: Optional[np.ndarray], i: tuple[int, int], self_: int) -> bool:
    """Sky.otherStar: does pixel ``i`` belong to a star other than ``self_``?

    Here ``i`` is a ``(y, x)`` pair (the JS takes a flat index ``y*w + x``; with
    ``labels`` shaped ``(h, w)`` the pair names the same pixel). Labels are the
    index of the detection whose core holds the pixel, negative for none; with
    ``labels`` None nothing is another star.
    """
    if labels is None:
        return False
    y, x = i
    lab = int(labels[y, x])
    return lab >= 0 and lab != self_


def ring_start(lo: float, dy: int) -> int:
    """Sky.ringStart: the first dx of a ring row ``dy`` away whose distance reaches the inner radius (``lo`` is its square)."""
    t = lo - dy * dy
    return int(math.ceil(math.sqrt(t))) if t > 0 else 0


@functools.lru_cache(maxsize=None)
def _ring_offsets(r: int) -> tuple[np.ndarray, np.ndarray]:
    """The (dy, dx) offsets of the round ring of integer radius r, in the JS visiting order.

    Sky.ringStats visits dy from -r-1 to r+1; each row's dx runs from ring_start
    to the outer edge, and each dx also takes -dx. Cached per radius: a ring's
    offsets do not depend on the star, only the image bounds and labels do.
    """
    lo = (r - 0.5) * (r - 0.5)
    hi = (r + 0.5) * (r + 0.5)
    dys: list[int] = []
    dxs: list[int] = []
    for dy in range(-r - 1, r + 2):
        outer = hi - dy * dy
        if outer <= 0:
            continue
        a = ring_start(lo, dy)
        b = int(math.ceil(math.sqrt(outer))) - 1
        for dx in range(a, b + 1):
            d2 = dx * dx + dy * dy
            if d2 < lo or d2 >= hi:
                continue
            dys.append(dy)
            dxs.append(dx)
            if dx != 0:
                dys.append(dy)
                dxs.append(-dx)
    dy_arr = np.asarray(dys, dtype=np.int64)
    dx_arr = np.asarray(dxs, dtype=np.int64)
    dy_arr.setflags(write=False)
    dx_arr.setflags(write=False)
    return dy_arr, dx_arr


class _Ring(NamedTuple):
    """A round ring of Sky.ringStats: its centre (cx, cy) and integer radius r."""

    cx: float
    cy: float
    r: int


def _ring_pixels(shape: tuple[int, int], ring: _Ring, labels: Optional[np.ndarray],
                 self_: int) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """The ring's pixels that are on the image and free (no other detection's core): ``(ys, xs)``, or None if none is left."""
    h, w = shape
    dy, dx = _ring_offsets(ring.r)
    X = int(js_round(ring.cx))
    Y = int(js_round(ring.cy))
    ys = Y + dy
    xs = X + dx
    keep = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    ys = ys[keep]
    xs = xs[keep]
    if labels is not None and xs.size:
        lab = np.asarray(labels)[ys, xs]
        free = (lab < 0) | (lab == self_)
        ys = ys[free]
        xs = xs[free]
    if xs.size == 0:
        return None
    return ys, xs


def ring_stats(
    buf: np.ndarray,
    cx: float,
    cy: float,
    r: int,
    labels: Optional[np.ndarray],
    self_: int,
) -> Optional[dict[str, float]]:
    """Sky.ringStats: median and brightest pixel of the round ring of radius r around (cx, cy).

    Pixels off the image, and pixels that belong to another detection's core
    (``labels`` >= 0 and != ``self_``), are skipped. ``max`` ignores NaN as the
    JS does (``value > top``). Returns ``{"med", "max"}``, or None when no pixel
    is left (a ring off the image). ``r`` is an integer radius.
    """
    if int(r) != r:
        raise ValueError("ring_stats: r must be an integer radius (JS callers pass integers)")
    r = int(r)
    pix = _ring_pixels(np.shape(buf), _Ring(cx, cy, r), labels, self_)
    if pix is None:
        return None
    vals = np.asarray(buf)[pix[0], pix[1]].astype(np.float64)
    finite_max = vals[~np.isnan(vals)]
    top = float(finite_max.max()) if finite_max.size else -_INF
    # the median is the JS one: the element at index n >> 1 of the sorted values
    srt = np.sort(vals)
    return {"med": float(srt[srt.size >> 1]), "max": top}


def core_labels(dets: Sequence[Detection], w: int, h: int, fwhm: float) -> np.ndarray:
    """Sky.coreLabels: each pixel labelled with the first detection whose core (grown by one FWHM) holds it.

    Returns ``(h, w)`` int32, -1 where no core reaches. The first detection in
    ``dets`` wins a pixel (fill in order, only where still -1).
    """
    labels = np.full((h, w), -1, dtype=np.int32)
    for k, d in enumerate(dets):
        g = grown_rect(d.rect, fwhm, w, h)
        x0, y0 = max(0, int(g.x0)), max(0, int(g.y0))
        x1, y1 = min(w, int(g.x1)), min(h, int(g.y1))
        if x1 <= x0 or y1 <= y0:
            continue
        view = labels[y0:y1, x0:x1]
        view[view < 0] = k
    return labels


def reader(buf: np.ndarray) -> Callable[[float, float], float]:
    """Sky.reader: a pixel reader over ``buf`` (h x w, its shape), clamped at the edges.

    Integer coordinates only; the JS reads ``undefined`` for a fractional index,
    which is NaN here.
    """
    arr = np.asarray(buf)
    h, w = arr.shape

    def read(x: float, y: float) -> float:
        if not (math.isfinite(x) and math.isfinite(y)):
            return _NAN
        if x != int(x) or y != int(y):
            return _NAN
        xi = int(clamp(int(x), 0, w - 1))
        yi = int(clamp(int(y), 0, h - 1))
        return float(arr[yi, xi])

    return read


def sparse_median(buf: np.ndarray) -> float:
    """Sky.sparseMedian: the stars layer's sky, a sparse-sample median with glows clipped.

    Every 7th sample of the flat buffer; iterated up to five times with everything
    more than 3 sigma above the median dropped, so big glows do not pull it up.
    """
    v = np.asarray(buf).ravel()[::7].astype(np.float64).tolist()
    m = median(v)
    for _ in range(5):
        sig = js_or(mad(v) / 1.4826, 0.0)
        kept = [x for x in v if x <= m + 3 * sig]
        if len(kept) == len(v) or len(kept) < 10:
            break
        v = kept
        m = median(v)
    return m


def _in_image_neighbours(x: float, y: float, h: int, w: int) -> list[tuple[int, int, float]]:
    """The in-image pixels around (x, y) as ``(xx, yy, weight)``, in the JS order (y outer, x inner).

    The weights are the bilinear ones of the four neighbours of (x, y); a neighbour off the h x w image
    is left out, and its weight with it.
    """
    x0 = math.floor(x)
    y0 = math.floor(y)
    fx = x - x0
    fy = y - y0
    out: list[tuple[int, int, float]] = []
    for j in (0, 1):
        for i in (0, 1):
            xx = x0 + i
            yy = y0 + j
            if xx < 0 or yy < 0 or xx >= w or yy >= h:
                continue
            out.append((xx, yy, (fx if i else 1 - fx) * (fy if j else 1 - fy)))
    return out


def bilinear(buf: np.ndarray, x: float, y: float) -> float:
    """Sky.bilinear: bilinear interpolation over the in-image neighbours of (x, y), 0 when none is in.

    A non-finite coordinate gives 0, as the JS does (no neighbour weight left).
    """
    if not (math.isfinite(x) and math.isfinite(y)):
        return 0.0
    arr = np.asarray(buf)
    h, w = arr.shape
    s = 0.0
    wt = 0.0
    for xx, yy, k in _in_image_neighbours(x, y, h, w):
        s += k * float(arr[yy, xx])
        wt += k
    return s / wt if wt > 0 else 0.0


def smooth_profile(prof: Sequence[float], from_: int) -> np.ndarray:
    """Sky.smoothProfile: a 5-ring running mean from ``from_`` out, then made non-increasing.

    Rings below ``from_`` are kept as they are. The non-increasing pass runs from
    index 1 on, as the JS does.
    """
    p = [float(v) for v in prof]
    out = list(p)
    for r in range(from_, len(p)):
        s = 0.0
        n = 0
        for k in range(max(from_, r - 2), min(len(p) - 1, r + 2) + 1):
            s += p[k]
            n += 1
        out[r] = s / n
    for r in range(1, len(out)):
        out[r] = js_min(out[r], out[r - 1])
    return np.asarray(out, dtype=np.float64)
