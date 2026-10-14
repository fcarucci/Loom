"""Star-model fitting and deblending (E3c): the spike directions, widths and falloff, each star's PSF model, the model sums, the second pass for missed stars and the per-sprite deblend.

Ports Sky.js 683-731 (the env part of Sky.sprites: :func:`make_env`) and
771-1130 (spike directions/width/falloff, star models, model evaluation,
model sums, unexplained sources, the deblend). The Sky.sprites driver itself
is E3d. Image helpers and the 1-D rules (Fly.spikeAngles, localProfile,
fitSpike, deblend, ...) are E3b in :mod:`loomsiril.starmodel` and are called,
not re-implemented.

Conventions (see docs/parity/e3c.md):

* Every image buffer is a 2-D ``(h, w)`` numpy plane (never flat).
* ``model_window``/``radial_window``/``spike_window`` take an EXCLUSIVE window
  ``x0, y0, x1, y1`` (as ``Rect`` does) and return ``(y1-y0, x1-x0)`` float64.
  Their scalar twins ``model_at``/``radial_at``/``spike_at`` are the JS code.
* Vectorised twins keep the JS arithmetic order where the float result depends
  on it; sums over pixels or samples may differ from the JS in the last bits
  (numpy sums pairwise), far below the oracle tolerance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, NamedTuple, Optional, Sequence

import numpy as np

from loomsiril import starmodel as sm
from loomsiril.core import Point, Rect, js_round, mad
from loomsiril.jsmath import is_falsy, js_max, js_min
from loomsiril.types import Detection, Halo, Spike, SpikeReach, SpriteMatch, Sprite, StarModel

__all__ = [
    "SPIKE_SIDE",
    "SPIKE_SIGMA",
    "SPIKE_STARS",
    "SPIKE_LEVER",
    "MISSED_SIGMA",
    "MISSED_SHARE",
    "StarEnv",
    "sprite_px",
    "make_env",
    "spike_excess",
    "spike_directions",
    "spike_side",
    "spike_width",
    "spike_falloff",
    "smooth_falloff",
    "star_model",
    "star_profile",
    "star_spikes",
    "star_colour",
    "model_at",
    "radial_at",
    "spike_at",
    "model_window",
    "radial_window",
    "spike_window",
    "model_sums",
    "unexplained_sources",
    "excess_peak",
    "point_model",
    "add_to_sums",
    "deblend_sprite",
]

# ---------------------------------------------------------------------------
# Constants (Sky.js 771-776)

SPIKE_SIDE: float = 3  # px either side of a spike where its surroundings are read (Sky.SPIKE_SIDE)
SPIKE_SIGMA: float = 1.0  # px: a spike's half-width (Gaussian sigma) when it cannot be measured (Sky.SPIKE_SIGMA)
SPIKE_STARS: int = 12  # the brightest stars read for the image's spike directions (Sky.SPIKE_STARS)
SPIKE_LEVER: float = 100  # px out along a spike its direction is refined over (Sky.SPIKE_LEVER)

MISSED_SIGMA: float = 5  # an unexplained peak this far above the noise is a star (Sky.MISSED_SIGMA)
MISSED_SHARE: float = 0.5  # ...and at least half the modelled light there (Sky.MISSED_SHARE)

_NAN = float("nan")
_INF = float("inf")
_DEG = math.pi / 180


# ---------------------------------------------------------------------------
# JS number semantics


def _steps(start: float, stop: float) -> list[float]:
    """The values a JS ``for (r = start; r < stop; ++r)`` visits (accumulated, so floats match the JS)."""
    out: list[float] = []
    r = start
    while r < stop:
        out.append(r)
        r += 1
    return out


def _top_stars(dets: Sequence[Detection]) -> list[Detection]:
    """The brightest SPIKE_STARS detections, by flux descending (the JS stable sort)."""
    return sorted(dets, key=lambda d: -d.flux)[:SPIKE_STARS]


# ---------------------------------------------------------------------------
# Vectorised image reads (the bilinear of Sky.bilinear over arrays)


def _bilinear_arr(buf: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Sky.bilinear over arrays of coordinates (same neighbour weights and order as the scalar one)."""
    arr = np.asarray(buf)
    h, w = arr.shape
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    finite = np.isfinite(x) & np.isfinite(y)
    xs = np.where(finite, x, 0.0)
    ys = np.where(finite, y, 0.0)
    x0 = np.floor(xs)
    y0 = np.floor(ys)
    fx = xs - x0
    fy = ys - y0
    s = np.zeros(np.broadcast(xs, ys).shape, dtype=np.float64)
    wt = np.zeros_like(s)
    for j in (0, 1):
        for i in (0, 1):
            xx = x0 + i
            yy = y0 + j
            ok = (xx >= 0) & (yy >= 0) & (xx < w) & (yy < h)
            k = (fx if i else 1 - fx) * (fy if j else 1 - fy)
            val = arr[np.clip(yy, 0, h - 1).astype(np.int64), np.clip(xx, 0, w - 1).astype(np.int64)].astype(np.float64)
            s = s + np.where(ok, k * val, 0.0)
            wt = wt + np.where(ok, k, 0.0)
    out = np.where(wt > 0, s / np.where(wt > 0, wt, 1.0), 0.0)
    return np.where(finite, out, 0.0)


class _Reads(NamedTuple):
    """Where a spike is read from its detection: the radii ``rs`` and the side distance (None: SPIKE_SIDE)."""

    rs: np.ndarray
    side: Optional[float]


def _excess_trig(env: StarEnv, d: Detection, dirs: tuple[np.ndarray, np.ndarray], reads: _Reads) -> np.ndarray:
    """Sky.spikeExcess over a grid from the directions' cosines and sines (``dirs`` = (c, s): (n, 1) columns).

    Rows are the directions, columns the radii ``reads.rs``, all measured from the detection ``d``. The
    elementwise operations are those of the scalar :func:`spike_excess`, so the values are the same floats.
    """
    c, s = dirs
    S = reads.side if not is_falsy(reads.side) else SPIKE_SIDE
    r = np.asarray(reads.rs, dtype=np.float64)[None, :]
    x = d.x + r * c
    y = d.y + r * s
    on = _bilinear_arr(env.I, x, y)
    side_v = 0.5 * (_bilinear_arr(env.I, x - S * s, y + S * c) + _bilinear_arr(env.I, x + S * s, y - S * c))
    return on - side_v


def _excess_grid(env: StarEnv, d: Detection, angs: np.ndarray, reads: _Reads) -> np.ndarray:
    """Sky.spikeExcess over a grid: rows are angles (radians), columns radii; ``reads.side`` defaults to SPIKE_SIDE."""
    a = np.asarray(angs, dtype=np.float64)
    return _excess_trig(env, d, (np.cos(a)[:, None], np.sin(a)[:, None]), reads)


def spike_excess(
    buf: np.ndarray, cx: float, cy: float, ang: float, r: float, side: Optional[float] = None
) -> float:
    """Sky.spikeExcess: light on the line at angle ``ang``, distance ``r`` from (cx, cy), above the pixels beside it."""
    c = math.cos(ang)
    s = math.sin(ang)
    x = cx + r * c
    y = cy + r * s
    S = side if not is_falsy(side) else SPIKE_SIDE
    on = sm.bilinear(buf, x, y)
    side_v = 0.5 * (sm.bilinear(buf, x - S * s, y + S * c) + sm.bilinear(buf, x + S * s, y - S * c))
    return on - side_v


# ---------------------------------------------------------------------------
# The env record (Sky.sprites, Sky.js 694-701)


@dataclass
class StarEnv:
    """The JS ``env`` of Sky.sprites: ``I`` the mean intensity, ``T`` the channel planes.

    ``labels`` (h, w) int32 core labels; ``noise`` the intensity's sigma;
    ``sky`` per-channel sparse medians; ``sky_i`` the intensity's; ``spikes``
    the spike directions (radians); ``spike_sigma`` the spike half-width;
    ``spike_falloff`` the spike falloff T[r] (r = 0 .. SPIKE_MAX - 1).
    """

    I: np.ndarray
    T: list[np.ndarray]
    w: int
    h: int
    nc: int
    fwhm: int
    labels: np.ndarray
    noise: float
    sky: list[float]
    sky_i: float
    spikes: list[float] = field(default_factory=list)
    spike_sigma: float = SPIKE_SIGMA
    spike_falloff: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))


def sprite_px(fwhm: Optional[float]) -> int:
    """Sky.sprites: ``px = Math.max(1, Math.round(fwhm || Sky.FWHM_DEFAULT))``."""
    v = sm.FWHM_DEFAULT if is_falsy(fwhm) else fwhm
    return int(max(1, js_round(v)))


def make_env(stars_planes: Sequence[np.ndarray], dets: Sequence[Detection], fwhm_px: int) -> StarEnv:
    """The env part of Sky.sprites (Sky.js 694-701): I, T, labels, noise, sky, sky_i, spikes, spike_sigma, spike_falloff.

    ``stars_planes`` is one ``(h, w)`` plane per channel (the stars layer, JS
    ``starsImage``); ``dets`` are detections whose ``index`` is already set (after
    the fragment filter); ``fwhm_px`` is the rounded FWHM from :func:`sprite_px`.
    """
    T = [np.array(p, dtype=np.float32, copy=True) for p in stars_planes]
    h, w = T[0].shape
    nc = len(T)
    px = int(fwhm_px)
    I = sm.intensity(T)
    labels = sm.core_labels(dets, w, h, px)
    env = StarEnv(
        I=I,
        T=T,
        w=w,
        h=h,
        nc=nc,
        fwhm=px,
        labels=labels,
        noise=sm.noise_sigma(I),
        sky=[sm.sparse_median(p) for p in T],
        sky_i=sm.sparse_median(I),
    )
    env.spikes = spike_directions(env, dets)
    env.spike_sigma = spike_width(env, dets, env.spikes)
    env.spike_falloff = spike_falloff(env, dets, env.spikes)
    return env


# ---------------------------------------------------------------------------
# Spike directions, width, falloff (Sky.js 777-908)


def spike_directions(env: StarEnv, dets: Sequence[Detection]) -> list[float]:
    """Sky.spikeDirections: the image's spike directions (radians) from the brightest stars' light by angle.

    Each direction found by :func:`loomsiril.starmodel.spike_angles` is refined
    to 0.05 degrees on the same stars over a long lever arm (SPIKE_LEVER).
    Empty for an image without spikes.
    """
    top = _top_stars(dets)
    angs = np.arange(360, dtype=np.float64) * _DEG
    prof = np.zeros(360, dtype=np.float64)
    for d in top:
        core = js_max(d.rect.x1 - d.x, d.x - d.rect.x0) + env.fwhm
        norm = js_max(1e-6, d.flux)
        rs = np.asarray(_steps(core + 2, core + 22), dtype=np.float64)
        ex = _excess_grid(env, d, angs, _Reads(rs, None))
        prof += ex.sum(axis=1) / norm
    noise = mad(prof.tolist()) / 1.4826

    def along(deg: float) -> float:
        ang = np.asarray([deg * math.pi / 180], dtype=np.float64)
        total = 0.0
        for d in top:
            core = js_max(d.rect.x1 - d.x, d.x - d.rect.x0) + env.fwhm
            rs = np.asarray(_steps(core + 2, core + SPIKE_LEVER), dtype=np.float64)
            part = float(_excess_grid(env, d, ang, _Reads(rs, None)).sum())
            total += part / js_max(1e-6, d.flux)
        return total

    out: list[float] = []
    for a in sm.spike_angles(prof.tolist(), noise):
        best: float = a
        best_score = -_INF
        t = a - 1.0
        while t <= a + 1 + 1e-9:
            sc = along(t)
            if sc > best_score:
                best_score = sc
                best = t
            t += 0.05
        out.append(best * math.pi / 180)
    return out


def spike_side(sigma: Optional[float]) -> float:
    """Sky.spikeSide: where a spike's surroundings are read, clear of its flanks: ``max(SPIKE_SIDE, 3*sigma)``."""
    s = SPIKE_SIGMA if is_falsy(sigma) else sigma
    return js_max(SPIKE_SIDE, 3 * s)


def spike_width(env: StarEnv, dets: Sequence[Detection], angles: Sequence[float]) -> float:
    """Sky.spikeWidth: the spikes' Gaussian sigma (px) from the brightest stars' light across their spikes, every half pixel."""
    if not len(angles):
        return SPIKE_SIGMA
    top = _top_stars(dets)
    lat = -8 + 0.5 * np.arange(33, dtype=np.float64)  # l = -8 .. 8 step 0.5 (Sky.js 775-779: p has 33 samples)
    p = np.zeros(33, dtype=np.float64)
    for ang in angles:
        c = math.cos(ang)
        s = math.sin(ang)
        for d in top:
            core = js_max(d.rect.x1 - d.x, d.x - d.rect.x0) + env.fwhm
            norm = js_max(1e-6, d.flux)
            rs = np.asarray(_steps(core + 2, core + SPIKE_LEVER), dtype=np.float64)
            if rs.size == 0:
                continue
            x = d.x + rs[:, None] * c - lat[None, :] * s
            y = d.y + rs[:, None] * s + lat[None, :] * c
            p += _bilinear_arr(env.I, x, y).sum(axis=0) / norm
    sigma = sm.lateral_sigma(p.tolist(), 0.5)
    if is_falsy(sigma):
        return SPIKE_SIGMA
    return js_max(0.5, js_min(4, sigma))


def smooth_falloff(T: Sequence[float], first: int) -> np.ndarray:
    """Sky.smoothFalloff: the averages T (from index first on) smoothed over SPIKE_WINDOW, never rising outward, the inside held at the first value."""
    W = sm.SPIKE_WINDOW
    n = len(T)
    out: list[float] = []
    for r in range(n):
        if r < first:
            out.append(0.0)
            continue
        s = 0.0
        m = 0
        for k in range(max(first, r - W), min(n - 1, r + W) + 1):
            s += float(T[k])
            m += 1
        out.append(js_max(0.0, s / m))
    for r in range(first + 1, len(out)):
        out[r] = js_min(out[r], out[r - 1])
    for r in range(0, first):
        out[r] = out[first]
    return np.asarray(out, dtype=np.float64)


def spike_falloff(env: StarEnv, dets: Sequence[Detection], angles: Sequence[float]) -> np.ndarray:
    """Sky.spikeFalloff: the image's spike falloff T[r], r = 0 .. SPIKE_MAX-1.

    The brightest stars' spike excess per unit flux, averaged over their spikes
    outside each one's core, then :func:`smooth_falloff`.
    """
    smax = sm.SPIKE_MAX
    T = np.zeros(smax, dtype=np.float64)
    n = np.zeros(smax, dtype=np.int64)
    top = _top_stars(dets)
    side = spike_side(env.spike_sigma)
    for ang in angles:
        for d in top:
            core = math.ceil(js_max(d.rect.x1 - d.x, d.x - d.rect.x0)) + env.fwhm
            norm = js_max(1e-6, d.flux)
            start = int(core + 2)
            if start >= smax:
                continue
            rs = np.arange(start, smax, dtype=np.int64)
            ex = _excess_grid(env, d, np.asarray([ang], dtype=np.float64), _Reads(rs.astype(np.float64), side))
            T[rs] += ex[0] / norm
            n[rs] += 1
    first = -1
    for r in range(smax):
        if n[r]:
            T[r] /= n[r]
            if first < 0:
                first = r
    if first < 0:
        return T
    return smooth_falloff(T.tolist(), first)


# ---------------------------------------------------------------------------
# Star models (Sky.js 909-970)


def star_profile(env: StarEnv, d: Detection, min_r: int) -> dict[str, Any]:
    """Sky.starProfile: a star's ring medians outward, other stars excluded, until its profile stops falling (Fly.localProfile).

    Returns the localProfile dict ``{prof, rmax, sky, settled}``.
    """
    self_ = d.index
    raw: list[float] = []
    lp: Optional[dict[str, Any]] = None
    for r in range(sm.HALO_MAX):
        if r == 0:
            med: Optional[float] = sm.bilinear(env.I, d.x, d.y)
        else:
            st = sm.ring_stats(env.I, d.x, d.y, r, env.labels, self_)
            med = st["med"] if st is not None else None
        raw.append((med if med is not None else env.sky_i) - env.sky_i)
        if r >= min_r + 2 * sm.SETTLE_WINDOW:
            lp = sm.local_profile(raw, min_r, env.noise)
            if lp["settled"]:
                return lp
    return sm.local_profile(raw, min_r, env.noise)


def star_spikes(env: StarEnv, d: Detection, min_r: int) -> list[Spike]:
    """Sky.starSpikes: a star's spikes along the image's directions, each fitted with its opposite partner (Fly.fitSpikePair).

    The excess along every direction is read in one grid (:func:`_excess_trig`) from the scalar
    cos/sin, so it equals the per-direction :func:`spike_excess` reads exactly.
    """
    if not env.spikes:
        return []
    side = spike_side(env.spike_sigma)
    c = np.asarray([math.cos(a) for a in env.spikes], dtype=np.float64)[:, None]
    s = np.asarray([math.sin(a) for a in env.spikes], dtype=np.float64)[:, None]
    rs = np.asarray([min_r + k for k in range(sm.SPIKE_FIT)], dtype=np.float64)
    grid = _excess_trig(env, d, (c, s), _Reads(rs, side))
    spikes: list[Spike] = []
    for i, ang in enumerate(env.spikes):
        j = next((k for k, b in enumerate(env.spikes) if abs(abs(b - ang) - math.pi) < 2 * math.pi / 180), None)
        if j is not None:
            sp = sm.fit_spike_pair(grid[i].tolist(), grid[j].tolist(), env.spike_falloff, int(min_r), env.noise)
        else:
            sp = sm.fit_spike(grid[i].tolist(), env.spike_falloff, int(min_r), env.noise)
        if sp["reach"] > 0:
            spikes.append(Spike(angle=ang, start=min_r, amp=sp["amp"], reach=min_r + sp["reach"]))
    return spikes


def star_colour(env: StarEnv, d: Detection, min_r: int) -> list[float]:
    """Sky.starColour: each channel's light over the core against the intensity's, 1 where there is none."""
    x_lo = max(0, math.floor(d.x - min_r))
    x_hi = min(env.w - 1, math.ceil(d.x + min_r))
    y_lo = max(0, math.floor(d.y - min_r))
    y_hi = min(env.h - 1, math.ceil(d.y + min_r))
    ys, xs = np.mgrid[y_lo : y_hi + 1, x_lo : x_hi + 1]
    keep = np.hypot(xs - d.x, ys - d.y) <= min_r
    sI = 0.0
    col = [0.0] * env.nc
    if keep.any():
        yk = ys[keep]
        xk = xs[keep]
        sI = float(np.sum(env.I[yk, xk].astype(np.float64) - env.sky_i))
        for c in range(env.nc):
            col[c] = float(np.sum(env.T[c][yk, xk].astype(np.float64) - env.sky[c]))
    return [js_max(0.0, v / sI) if sI > 0 else 1.0 for v in col]


def star_model(env: StarEnv, d: Detection) -> StarModel:
    """Sky.starModel: one star's model, and sets ``d.halo`` (the reach tests of Fly.assignSprites).

    ``core`` is the minimum radius (the detection's half-extent plus one FWHM).
    The caller stores the result in ``d.model``.
    """
    min_r = math.ceil(js_max(d.rect.x1 - d.x, d.x - d.rect.x0)) + env.fwhm
    lp = star_profile(env, d, min_r)
    spikes = star_spikes(env, d, min_r)
    col = star_colour(env, d, min_r)
    reach_max = 0.0
    for sp in spikes:
        reach_max = js_max(reach_max, sp.reach)
    support = js_max(lp["rmax"], reach_max) + 1
    d.halo = Halo(
        radius=support,
        halo=lp["rmax"],
        outer=support,
        spikes=[SpikeReach(angle=sp.angle, reach=sp.reach) for sp in spikes],
        pedestal=0.0,
        feather=0.0,
    )
    return StarModel(
        x=d.x,
        y=d.y,
        core=min_r,
        prof=sm.smooth_profile(lp["prof"], int(min_r)),
        rmax=lp["rmax"],
        spikes=spikes,
        col=col,
        support=support,
        spike_sigma=env.spike_sigma,
    )


# ---------------------------------------------------------------------------
# Model evaluation (Sky.js 985-1015): scalar, then windows


def radial_at(m: StarModel, x: float, y: float) -> float:
    """Sky.radialAt: a model's round part at pixel (x, y): its radial profile."""
    r = math.hypot(x - m.x, y - m.y)
    if not (r < m.rmax):
        return 0.0
    r0 = math.floor(r)
    f = r - r0
    prof = m.prof
    nxt = prof[r0 + 1] if r0 + 1 < len(prof) else 0.0
    return float(prof[r0] + f * (nxt - prof[r0]))


def spike_at(m: StarModel, x: float, y: float) -> float:
    """Sky.spikeAt: a model's spikes at pixel (x, y)."""
    dx = x - m.x
    dy = y - m.y
    v = 0.0
    sg = SPIKE_SIGMA if is_falsy(m.spike_sigma) else m.spike_sigma
    for sp in m.spikes:
        c = math.cos(sp.angle)
        s = math.sin(sp.angle)
        along = dx * c + dy * s
        lat = -dx * s + dy * c
        if along < sp.start or along > sp.reach or abs(lat) > 3 * sg:
            continue
        t = along - sp.start
        t0 = math.floor(t)
        g = t - t0
        amp = sp.amp
        nxt = amp[t0 + 1] if t0 + 1 < len(amp) else 0.0
        a = amp[t0] + g * (nxt - amp[t0])
        v += a * math.exp(-lat * lat / (2 * sg * sg))
    return v


def model_at(m: StarModel, x: float, y: float) -> float:
    """Sky.modelAt: a model's value (intensity) at pixel (x, y)."""
    return radial_at(m, x, y) + spike_at(m, x, y)


def _grid(x0: int, y0: int, x1: int, y1: int) -> tuple[np.ndarray, np.ndarray]:
    """Float pixel coordinates of an exclusive window, shaped (y1-y0, x1-x0)."""
    ys, xs = np.mgrid[y0:y1, x0:x1]
    return xs.astype(np.float64), ys.astype(np.float64)


def radial_window(m: StarModel, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """:func:`radial_at` over the exclusive window ``x0..x1, y0..y1`` (float64, (h, w))."""
    X, Y = _grid(x0, y0, x1, y1)
    out = np.zeros(X.shape, dtype=np.float64)
    prof = np.asarray(m.prof, dtype=np.float64)
    P = prof.shape[0]
    if P == 0:
        return out
    r = np.hypot(X - m.x, Y - m.y)
    inside = r < m.rmax
    rr = np.where(inside, r, 0.0)
    r0 = np.floor(rr).astype(np.int64)
    f = rr - r0
    p0 = prof[np.clip(r0, 0, P - 1)]
    nxt = np.where(r0 + 1 < P, prof[np.clip(r0 + 1, 0, P - 1)], 0.0)
    return np.where(inside, p0 + f * (nxt - p0), 0.0)


def spike_window(m: StarModel, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """:func:`spike_at` over the exclusive window ``x0..x1, y0..y1`` (float64, (h, w))."""
    X, Y = _grid(x0, y0, x1, y1)
    out = np.zeros(X.shape, dtype=np.float64)
    sg = SPIKE_SIGMA if is_falsy(m.spike_sigma) else m.spike_sigma
    dx = X - m.x
    dy = Y - m.y
    for sp in m.spikes:
        amp = np.asarray(sp.amp, dtype=np.float64)
        L = amp.shape[0]
        if L == 0:
            continue
        c = math.cos(sp.angle)
        s = math.sin(sp.angle)
        along = dx * c + dy * s
        lat = -dx * s + dy * c
        ok = ~((along < sp.start) | (along > sp.reach) | (np.abs(lat) > 3 * sg))
        t = along - sp.start
        t0 = np.floor(t)
        g = t - t0
        ti = np.clip(t0, 0, L - 1).astype(np.int64)
        a0 = amp[ti]
        a1 = np.where(t0 + 1 < L, amp[np.clip(t0 + 1, 0, L - 1).astype(np.int64)], 0.0)
        a = a0 + g * (a1 - a0)
        out += np.where(ok, a * np.exp(-lat * lat / (2 * sg * sg)), 0.0)
    return out


def model_window(m: StarModel, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """:func:`model_at` over the exclusive window ``x0..x1, y0..y1`` (float64, (h, w)): radial plus spikes."""
    return radial_window(m, x0, y0, x1, y1) + spike_window(m, x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# Model sums (Sky.js 1016-1035, 1084-1092)


def _accumulate(env: StarEnv, models: Sequence[StarModel], sums: Optional[list[np.ndarray]] = None) -> list[np.ndarray]:
    """Adds each model's window x colour into the per-channel planes ``sums`` (new zero planes when None)."""
    if sums is None:
        sums = [np.zeros((env.h, env.w), dtype=np.float64) for _ in range(env.nc)]
    for m in models:
        R = m.support
        x0 = max(0, math.floor(m.x - R))
        x1 = min(env.w - 1, math.ceil(m.x + R))
        y0 = max(0, math.floor(m.y - R))
        y1 = min(env.h - 1, math.ceil(m.y + R))
        if x1 < x0 or y1 < y0:
            continue
        v = model_window(m, x0, y0, x1 + 1, y1 + 1)
        keep = ~(v <= 0)  # Sky.modelSums skips v <= 0 (a NaN is still added, as the JS does)
        for c in range(env.nc):
            sums[c][y0 : y1 + 1, x0 : x1 + 1] += np.where(keep, v * m.col[c], 0.0)
    return sums


def model_sums(env: StarEnv, dets: Sequence[Detection]) -> list[np.ndarray]:
    """Sky.modelSums: every detection's model added up, per channel (model x colour), as (h, w) float64 planes."""
    return _accumulate(env, [d.model for d in dets])


def add_to_sums(env: StarEnv, models: Sequence[StarModel], sums: list[np.ndarray]) -> None:
    """Sky.addToSums: adds the models to the per-channel sums, in place."""
    _accumulate(env, models, sums)


# ---------------------------------------------------------------------------
# Unexplained light and point models (Sky.js 1036-1083)


def excess_peak(excess: Callable[[int, int], float], x: int, y: int, e: float) -> bool:
    """Sky.excessPeak: is (x, y), excess e, the highest of its 3 x 3 neighbours in ``excess``?"""
    for j in (-1, 0, 1):
        for k in (-1, 0, 1):
            if (j or k) and excess(x + k, y + j) > e:
                return False
    return True


def point_model(env: StarEnv, sums: Sequence[np.ndarray], x: int, y: int, e: float) -> StarModel:
    """Sky.pointModel: a Gaussian of the image's FWHM with peak ``e``; colour what the models there leave; core 0.0 (not a moving star's core)."""
    i_y, i_x = int(y), int(x)
    sig = env.fwhm / 2.3548  # the JS literal, not the more precise constant
    rr = math.ceil(3 * sig)
    col: list[float] = []
    for c in range(env.nc):
        if float(env.I[i_y, i_x]) - env.sky_i > 0:
            col.append(js_max(0.0, (float(env.T[c][i_y, i_x]) - env.sky[c] - float(sums[c][i_y, i_x])) / e))
        else:
            col.append(1.0)
    prof = np.asarray([e * math.exp(-r * r / (2 * sig * sig)) for r in range(rr + 1)], dtype=np.float64)
    return StarModel(
        x=float(x),
        y=float(y),
        core=0.0,
        prof=prof,
        rmax=float(rr),
        spikes=[],
        col=col,
        support=float(rr + 1),
        spike_sigma=None,
    )


def _window_pixels(m: StarModel, w: int, h: int) -> Iterator[tuple[int, int]]:
    """The (x, y) pixels of a model's support square that are at least one pixel inside the image."""
    R = m.support
    for y in range(max(1, math.floor(m.y - R)), min(h - 2, math.ceil(m.y + R)) + 1):
        for x in range(max(1, math.floor(m.x - R)), min(w - 2, math.ceil(m.x + R)) + 1):
            yield x, y


class _MissedScan:
    """The scan of Sky.unexplainedSources: the light the models leave, ``excess(x, y)``, and its peaks.

    ``excess`` is the stars layer's intensity less the sky less the mean of the channels' model
    sums (the JS ``excess`` callback); ``is_point_peak`` is the Fly.localPeak test on the image.
    """

    def __init__(self, env: StarEnv, sums: Sequence[np.ndarray]) -> None:
        self.env = env
        self.sums = sums
        self.value_i = sm.reader(env.I)

    def sum_i(self, y: int, x: int) -> float:
        """The mean over the channels of the model sums at (x, y)."""
        t = 0.0
        for c in range(self.env.nc):
            t += float(self.sums[c][y, x])
        return t / self.env.nc

    def excess(self, x: int, y: int) -> float:
        """The light the models leave at (x, y): intensity less sky less the model sums."""
        return float(self.env.I[y, x]) - self.env.sky_i - self.sum_i(y, x)

    def is_point_peak(self, x: int, y: int, e: float) -> bool:
        """Not a bead on a spike or slope: a peak of the excess and a peak of the image (Fly.localPeak)."""
        return excess_peak(self.excess, x, y, e) and sm.local_peak(self.value_i, x, y, js_max(2, self.env.fwhm))

    def scan(self, sprites: Sequence[SpriteMatch]) -> list[StarModel]:
        """The point models of the compact peaks in the sprites' windows, each pixel once."""
        env = self.env
        w, h = env.w, env.h
        found: list[StarModel] = []
        seen: set[int] = set()
        for s in sprites:
            for x, y in _window_pixels(s.det.model, w, h):
                i = y * w + x
                if i in seen:
                    continue
                e = self.excess(x, y)
                if e < MISSED_SIGMA * env.noise or e < MISSED_SHARE * self.sum_i(y, x):
                    continue
                if not self.is_point_peak(x, y, e):
                    continue
                seen.add(i)
                found.append(point_model(env, self.sums, x, y, e))
        return found


def unexplained_sources(env: StarEnv, sprites: Sequence[SpriteMatch], sums: Sequence[np.ndarray]) -> list[StarModel]:
    """Sky.unexplainedSources: point models for compact light the models do not explain, inside the moving stars' reach.

    Local maxima of (stars layer - sky - all models), well above the noise and a
    real share of the modelled light, that are also peaks of the image
    (Fly.localPeak): a model's shortfall along a spike is not a star.
    """
    return _MissedScan(env, sums).scan(sprites)


# ---------------------------------------------------------------------------
# The per-sprite deblend (Sky.js 1093-1130)


def _deblend_arrays(Mi: np.ndarray, Msum: np.ndarray, L: np.ndarray, sigma: float) -> np.ndarray:
    """A vectorised twin of :func:`loomsiril.starmodel.deblend` (same formula, same guards)."""
    Mi = np.asarray(Mi, dtype=np.float64)
    Msum = np.asarray(Msum, dtype=np.float64)
    L = np.asarray(L, dtype=np.float64)
    ok = (Msum > 0) & (Mi > 0)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        ratio = Msum / sigma if sigma != 0 else np.full(Msum.shape, _INF)
        t = np.clip((ratio - 1) / (5 - 1), 0.0, 1.0)  # smoothstep(1, 5, ratio), as core.smoothstep
        a = t * t * (3 - 2 * t)
        share = np.where(ok, Mi / np.where(ok, Msum, 1.0), 0.0)
        val = a * share * np.minimum(L, sm.DEBLEND_CAP * Msum) + (1 - a) * Mi
    return np.where(ok, val, 0.0)


def deblend_sprite(env: StarEnv, s: SpriteMatch, sums: Sequence[np.ndarray], residual: list[np.ndarray]) -> Sprite:
    """Sky.deblendSprite: one moving star's deblended light, taken out of ``residual`` (per channel, updated in place).

    Each pixel the model reaches takes its share (Fly.deblend) of the light;
    ``model_core`` is the round part (the model's colour-weighted radial part,
    parts included) and ``model_spikes`` its spikes, both as the JS draws them.
    """
    m = s.det.model
    R = m.support
    w, h, nc = env.w, env.h, env.nc
    x0 = max(0, math.floor(m.x - R))
    y0 = max(0, math.floor(m.y - R))
    x1 = min(w, math.ceil(m.x + R) + 1)
    y1 = min(h, math.ceil(m.y + R) + 1)
    rect = Rect(x0, y0, x1, y1)
    rw = x1 - x0
    rh = y1 - y0
    sl = (slice(y0, y1), slice(x0, x1))

    mi = model_window(m, x0, y0, x1, y1)
    act = ~(mi <= 0)  # Sky.deblendSprite skips the pixels the model does not reach
    mask = act.astype(np.uint8)
    part_mi = [(model_window(p, x0, y0, x1, y1), p) for p in (m.parts or [])]
    spike_base = spike_window(m, x0, y0, x1, y1)

    pixels: list[np.ndarray] = []
    model_core: list[np.ndarray] = []
    model_spikes: list[np.ndarray] = []
    for c in range(nc):
        mc = mi * m.col[c]
        for pv, p in part_mi:
            mc = mc + pv * p.col[c]
        v = _deblend_arrays(mc, sums[c][sl], env.T[c][sl].astype(np.float64) - env.sky[c], env.noise)
        v = np.where(act, v, 0.0)
        sk = np.where(act, spike_base * m.col[c], 0.0)
        pixels.append(v.astype(np.float32))
        model_spikes.append(sk.astype(np.float32))
        model_core.append(np.where(act, mc - sk, 0.0).astype(np.float32))
        residual[c][sl] -= v  # float32 plane: the difference rounds on store, as the JS Float32Array does

    return Sprite(
        source=s.placed.source,
        d=s.placed.d,
        placed=s.placed,
        det=s.det,
        rect=rect,
        radius=R,
        centre=Point(s.det.x, s.det.y),
        mask=mask.reshape(rh, rw),
        pixels=[p.reshape(rh, rw) for p in pixels],
        model_core=[p.reshape(rh, rw) for p in model_core],
        model_spikes=[p.reshape(rh, rw) for p in model_spikes],
        spike_angles=[sp.angle for sp in m.spikes],
    )
