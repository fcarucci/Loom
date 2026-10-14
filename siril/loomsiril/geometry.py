"""Motion, growth, opacity and twinkle maths of Loom's Fly-Through (E1a).

Ported from Loom ``script/lib/Fly.js`` (v0.4.1). Pure arithmetic: no image,
no file, no network. The JS is the oracle (tests/oracle/geometry.json).

JS corners kept on purpose: division by zero gives Infinity or NaN instead of
raising (see :func:`js_div`), ``Math.min``/``Math.max`` propagate NaN (see
:func:`js_min` and :func:`js_max`), and ``Math.floor``/``Math.ceil``/``Math.sin``
of a non-finite value give NaN or Infinity rather than an exception.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

import numpy as np

from loomsiril.core import RAD, Point, radec, smoothstep, vec
from loomsiril.jsmath import js_acos, js_ceil, js_div, js_floor, js_max, js_min, js_sin, js_sqrt

__all__ = [
    "BACKDROP_MOTION_DEFAULT",
    "CORE_MODEL_BLEND",
    "FADE_NEAR",
    "GROWTH_DEFAULT",
    "HDR_MAG_RANGE",
    "HDR_REFERENCE_WHITE",
    "HEADROOM_RADIUS",
    "HEADROOM_SIGMA",
    "MODEL_BLEND",
    "SHUTTER",
    "SPIKE_LENGTH_MAX",
    "TWINKLE_DEFAULT",
    "backdrop_scale",
    "backdrop_zoom",
    "core_model_weight",
    "cover_fraction",
    "disc_grid_count",
    "disc_visible",
    "ease",
    "growth",
    "headroom_bump",
    "headroom_bump_arr",
    "magnitude_gain",
    "model_weight",
    "moved",
    "opacity",
    "pixel_scale",
    "radial_source",
    "radial_source_arr",
    "screen_position",
    "seeded",
    "shutter_steps",
    "spike_length",
    "twinkle",
]

Direction = Mapping[str, float]  # {ra, dec} degrees


# ---------------------------------------------------------------------------
# Motion of the camera and of the backdrop.
# ---------------------------------------------------------------------------


def moved(ra: float, dec: float, d: float, target: Direction, s: float) -> dict[str, Any]:
    """``Fly.moved``: a star at distance d, direction (ra, dec), seen from a camera moved s towards the target.

    p = d v - s u, exact (no small-angle assumption). Returns the JS keys
    ``{ra, dec, front, ratio}``: ``front`` is True while the star is ahead of
    the camera, ``ratio`` = d/|p| is the light ratio that sets its growth.
    On the camera (|p| = 0) the ratio is Infinity and dec is NaN, as in JS.
    """
    v = vec(ra, dec)
    u = vec(target["ra"], target["dec"])
    p = [d * v[0] - s * u[0], d * v[1] - s * u[1], d * v[2] - s * u[2]]
    length = js_sqrt(p[0] * p[0] + p[1] * p[1] + p[2] * p[2])
    if length > 0:
        r = radec(p)
    else:
        # core.radec divides by |p|; on the camera JS gives ra = atan2(0, 0) and dec = NaN.
        r = {"ra": math.fmod(math.atan2(p[1], p[0]) / RAD + 360, 360), "dec": math.nan}
    front = (p[0] * u[0] + p[1] * u[1] + p[2] * u[2]) > 0
    return {"ra": r["ra"], "dec": r["dec"], "front": front, "ratio": js_div(d, length)}


def screen_position(c: Point, p0: Point, tp: Point, K: float) -> Point:
    """``Fly.screenPosition``: where a star is drawn when the backdrop zooms by K about the target tp.

    Its parallax position ``c`` (``p0`` at the start), but never nearer the
    target than the backdrop would carry it: whatever is in front of the
    backdrop spreads out at least as fast, or it reads as behind it.
    """
    r0 = math.hypot(p0.x - tp.x, p0.y - tp.y)
    r = math.hypot(c.x - tp.x, c.y - tp.y)
    want = K * r0
    if not (K > 1) or r >= want:
        return Point(c.x, c.y)
    if r == 0:
        return Point(tp.x + (p0.x - tp.x) * K, tp.y + (p0.y - tp.y) * K)
    return Point(tp.x + (c.x - tp.x) * want / r, tp.y + (c.y - tp.y) * want / r)


#: Share of the physically correct backdrop zoom that the nebula shows (Fly.BACKDROP_MOTION_DEFAULT).
BACKDROP_MOTION_DEFAULT: float = 0.4


def backdrop_scale(D: float, s: float) -> float:
    """``Fly.backdropScale``: the physically correct zoom D/(D - s); 1 for a galaxy (D infinite)."""
    return 1.0 if D == math.inf else js_div(D, D - s)


def backdrop_zoom(D: float, s: float, motion: float) -> float:
    """``Fly.backdropZoom``: ``motion`` (0-1) of the physically correct growth; a galaxy stays fixed."""
    return 1 + (backdrop_scale(D, s) - 1) * motion


def ease(t: float, mode: str) -> float:
    """``Fly.ease``: ``linear`` keeps t, any other mode is smoothstep(0, 1, t)."""
    return t if mode == "linear" else smoothstep(0, 1, t)


# ---------------------------------------------------------------------------
# Shape of a star on screen: radial stretch, visibility and cover.
# ---------------------------------------------------------------------------


def radial_source(r_out: float, rc: float, g: float, seen: Optional[float] = None) -> float:
    """``Fly.radialSource``: the source radius of a sprite pixel drawn ``r_out`` from the centre, grown by ``g``.

    Inside the core (``rc``) nowhere else: a star is a point, and a core
    magnified whole looked like a soft blob. Beyond it the light is stretched
    outward, so the glow and spikes grow as the star nears. ``seen`` (the share
    of a partly hidden star that is in frame) shortens the stretch, floored at 0.05.
    """
    if r_out <= rc:
        return r_out
    factor = 1.0 if seen is None else js_max(0.05, seen)
    return rc + js_div(r_out - rc, g * factor)


def radial_source_arr(ro: np.ndarray, rc: float, g: float, seen: float) -> np.ndarray:
    """``Fly.radialSource`` over an array: inside the core unchanged, beyond it stretched by g (seen floored at 0.05).

    Same maths as :func:`radial_source` with ``seen`` given. The floor is Python's
    ``max``, as the sprite path always had it: a NaN ``seen`` floors to 0.05 here,
    where the scalar keeps NaN.
    """
    factor = max(0.05, seen)
    with np.errstate(divide="ignore", invalid="ignore"):
        outside = rc + (ro - rc) / (g * factor)
    return np.where(ro <= rc, ro, outside)


def disc_grid_count(u: float, v: float, r: float, W: float, H: float) -> dict[str, int]:
    """``Fly.discGridCount``: over a 24 x 24 grid on a disc (centre u, v, radius r), how many points lie in the disc (``inDisc``) and of those in the W x H frame (``inFrame``)."""
    n = 24
    in_disc = 0
    in_frame = 0
    for j in range(n):
        for i in range(n):
            x = ((i + 0.5) / n * 2 - 1) * r
            y = ((j + 0.5) / n * 2 - 1) * r
            if x * x + y * y > r * r:
                continue
            in_disc += 1
            if u + x >= 0 and u + x < W and v + y >= 0 and v + y < H:
                in_frame += 1
    return {"inDisc": in_disc, "inFrame": in_frame}


def disc_visible(u: float, v: float, r: float, W: float, H: float) -> float:
    """``Fly.discVisible``: the share of a disc (centre u, v, radius r) inside a W x H frame."""
    if u - r >= 0 and v - r >= 0 and u + r <= W and v + r <= H:
        return 1
    count = disc_grid_count(u, v, r, W, H)
    return js_div(count["inFrame"], count["inDisc"]) if count["inDisc"] else 0


def cover_fraction(r1: float, r2: float, d: float) -> float:
    """``Fly.coverFraction``: the share of a disc of radius r1 covered by a disc of radius r2 whose centre is d away (the lens area)."""
    if d >= r1 + r2:
        return 0
    if d + r1 <= r2:
        return 1
    if d + r2 <= r1:
        return js_div(r2 * r2, r1 * r1)
    a1 = r1 * r1 * js_acos(js_div(d * d + r1 * r1 - r2 * r2, 2 * d * r1))
    a2 = r2 * r2 * js_acos(js_div(d * d + r2 * r2 - r1 * r1, 2 * d * r2))
    k = 0.5 * js_sqrt((-d + r1 + r2) * (d + r1 - r2) * (d - r1 + r2) * (d + r1 + r2))
    return js_div(a1 + a2 - k, math.pi * r1 * r1)


# ---------------------------------------------------------------------------
# HDR headroom and spikes.
# ---------------------------------------------------------------------------

#: Magnitudes from the brightest star to one that gets no headroom (100x in flux).
HDR_MAG_RANGE: float = 5
#: px (scene): the area a faint star's gain covers; the brightest cover 4x.
HEADROOM_RADIUS: float = 6
#: A star's bump: its sigma, of that area's radius.
HEADROOM_SIGMA: float = 0.5
#: Nits: SDR white in an HDR video (ITU-R BT.2408).
HDR_REFERENCE_WHITE: float = 203


def headroom_bump(r: float, sigma: float, gain: float) -> float:
    """``Fly.headroomBump``: the factor at radius r from a star's centre, ``gain`` at the centre falling (Gaussian, ``sigma``) to 1.

    Shaped by distance, not by the pixel's value: a core the photograph
    clipped flat would otherwise be lifted whole, a flat disc with a hard edge.
    """
    return 1 + (gain - 1) * math.exp(js_div(-r * r, 2 * sigma * sigma))


def headroom_bump_arr(r: np.ndarray, sigma: float, gain: float) -> np.ndarray:
    """``Fly.headroomBump`` over an array: gain at the centre falling (Gaussian, sigma) to 1."""
    return 1 + (gain - 1) * np.exp((-r * r) / (2 * sigma * sigma))


def magnitude_gain(G: float, gBright: float, peak: float) -> float:
    """``Fly.magnitudeGain``: a star's HDR peak from its magnitude G: ``peak`` for the brightest (gBright), 1 HDR_MAG_RANGE fainter, linear in magnitude."""
    w = js_max(0, js_min(1, (gBright + HDR_MAG_RANGE - G) / HDR_MAG_RANGE))
    return 1 + (peak - 1) * w


#: The longest a star's spikes are drawn, as a multiple of their measured length.
SPIKE_LENGTH_MAX: float = 4


def spike_length(ratio: float, brightening: bool, g: float) -> float:
    """``Fly.spikeLength``: how long a star's spikes are drawn, as a multiple of their measured length.

    A spike falling as 1/r^2, L times longer, shows what an L^2-times brighter
    one shows: the square root of the brightening (the light ratio, when
    brightening), at most SPIKE_LENGTH_MAX, never less than the glow's growth g.
    """
    return js_max(g, js_min(SPIKE_LENGTH_MAX, ratio if brightening else 1))


# ---------------------------------------------------------------------------
# Shutter and twinkle.
# ---------------------------------------------------------------------------

#: Of a frame's interval: a 180-degree shutter.
SHUTTER: float = 0.5


def shutter_steps(path_px: float) -> float:
    """``Fly.shutterSteps``: steps a star is drawn at along the path it covers while the shutter is open (about one per 0.75 px, 1 to 12)."""
    return js_max(1, js_min(12, js_ceil(path_px / 0.75)))


#: A star's brightness wobble, as a fraction.
TWINKLE_DEFAULT: float = 0.03


def seeded(seed: float, k: float) -> float:
    """``Fly.seeded``: a repeatable number in [0, 1) for star ``seed``, draw ``k``.

    The sine hash of the JS, kept float64: twinkle parity depends on its exact digits.
    """
    x = js_sin(seed * 12.9898 + k * 78.233) * 43758.5453
    return x - js_floor(x)


def twinkle(seed: float, seconds: float, amount: float, c: float) -> float:
    """``Fly.twinkle``: a star's brightness factor at ``seconds`` into the clip.

    1 + amount x a sum of three slow waves (0.3-1.5 Hz) of its own, minus their
    value at the start, so the first frame is untouched. Not physical -- a touch
    of life. Channel ``c`` shifts the fastest wave's phase, so the colours
    shimmer a little apart. ``amount`` not above 0 gives exactly 1.
    """
    if not (amount > 0):
        return 1
    c_or_0 = 0.0 if (c is None or c != c or c == 0) else c
    total = 0.0
    total0 = 0.0
    for k in range(3):
        f = 0.3 + 1.2 * seeded(seed, 3 * k)
        ph = 2 * math.pi * seeded(seed, 3 * k + 1) + (0.6 * c_or_0 if k == 2 else 0)
        a = 0.5 if k == 0 else 0.25
        total += a * js_sin(2 * math.pi * f * seconds + ph)
        total0 += a * js_sin(ph)
    return 1 + amount * (total - total0)


# ---------------------------------------------------------------------------
# Growth, pixel scale and the photograph-to-model blend.
# ---------------------------------------------------------------------------

#: Growth of a sprite as it nears: 0.15 of the light ratio's change.
GROWTH_DEFAULT: float = 0.15


def growth(ratio: float, growth_amount: float) -> float:
    """``Fly.growth``: sprite size factor for a star whose light ratio is ``ratio`` = d/|p|."""
    return 1 + growth_amount * (ratio - 1)


def pixel_scale(ratio: float, growth_amount: float, brightening: bool) -> float:
    """``Fly.pixelScale``: multiplier on a sprite's pixels AFTER resampling by g.

    The sprite's total light then scales as ratio^2 (inverse square) -- or stays
    the same with brightening off -- whatever the growth.
    """
    g = growth(ratio, growth_amount)
    return js_div(ratio * ratio if brightening else 1, g * g)


#: Light ratios over which a star turns from its photograph to its model.
MODEL_BLEND: tuple[float, float] = (2, 4)
#: ...and its core sooner: a saturated core is often a flat square in the photograph.
CORE_MODEL_BLEND: tuple[float, float] = (1, 1.5)


def core_model_weight(ratio: float) -> float:
    """``Fly.coreModelWeight``: how much of a star's core is drawn from its round model: none at the start, all by 1.5x nearer."""
    return smoothstep(CORE_MODEL_BLEND[0], CORE_MODEL_BLEND[1], ratio)


def model_weight(ratio: float) -> float:
    """``Fly.modelWeight``: how much of a star is drawn from its model at light ratio ``ratio``: none far away (frame 0 is the photograph), all up close."""
    return smoothstep(MODEL_BLEND[0], MODEL_BLEND[1], ratio)


#: Light ratios over which a close star fades out (was 8-20: close stars grew past what a sprite bears).
FADE_NEAR: tuple[float, float] = (6, 14)


def opacity(ratio: float, front: bool) -> float:
    """``Fly.opacity``: stars fade out as they near the camera, and are never drawn once passed (0 when not in front)."""
    if not front:
        return 0
    return 1 - smoothstep(FADE_NEAR[0], FADE_NEAR[1], ratio)
