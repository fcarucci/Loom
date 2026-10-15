"""Shared primitives every module uses (frozen interface, owned by E0).

Ported from Loom ``script/lib/Fly.js`` (v0.4.1): ``Fly.RAD``, ``Fly.smoothstep``,
``Fly.median``, ``Fly.mad``, plus the rectangle/point shapes the JS passes around
as plain objects (``{x0, y0, x1, y1}``, ``{x, y}``) and the screen/unscreen pair
that relates an image to its starless and stars layers
(``Steps.deriveStarsByUnscreen``).

Pixel convention everywhere in this package: pixel i's centre is at i (0-based).
A FITS CRPIX is 1-based, so the reference pixel is at ``crpix - 1``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, NamedTuple, Sequence

import numpy as np

__all__ = [
    "RAD",
    "dot",
    "radec",
    "separation",
    "tan_project",
    "tan_unproject",
    "vec",
    "Point",
    "Rect",
    "clamp",
    "detection_rect",
    "grown_rect",
    "mad",
    "median",
    "screen",
    "smoothstep",
    "smoothstep_arr",
    "js_round",
    "square_rect",
    "unscreen",
]

#: Degrees to radians (``Fly.RAD = Math.PI/180``).
RAD: float = math.pi / 180


class Point(NamedTuple):
    """A position in pixels (JS ``{x, y}``). Unpacks as a tuple: ``x, y = p``."""

    x: float
    y: float


@dataclass(slots=True)
class Rect:
    """An integer pixel rectangle, JS ``{x0, y0, x1, y1}``; x1 and y1 are EXCLUSIVE.

    Mutable on purpose: the JS grows and clips rectangles in place in a few
    places. Use :meth:`from_dict` / :meth:`as_dict` at JSON (oracle) borders.
    """

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        """Number of columns (x1 - x0)."""
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        """Number of rows (y1 - y0)."""
        return self.y1 - self.y0

    def is_empty(self) -> bool:
        """True when the rectangle covers no pixel."""
        return self.x1 <= self.x0 or self.y1 <= self.y0

    def contains(self, x: float, y: float) -> bool:
        """True when (x, y) lies inside, x1/y1 exclusive (the JS tests' rule)."""
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1

    def slices(self) -> tuple[slice, slice]:
        """``(rows, cols)`` slices for indexing a (H, W) numpy plane: ``plane[r.slices()]``."""
        return slice(self.y0, self.y1), slice(self.x0, self.x1)

    def as_dict(self) -> dict[str, int]:
        """The JS object shape, for JSON."""
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @classmethod
    def from_dict(cls, d: dict) -> "Rect":
        """Build from a JS-shaped mapping ``{x0, y0, x1, y1}``."""
        return cls(int(d["x0"]), int(d["y0"]), int(d["x1"]), int(d["y1"]))


def clamp(x: float, lo: float, hi: float) -> float:
    """``Math.max(lo, Math.min(hi, x))`` -- the JS idiom, NaN-propagating like it.

    Python's ``min(1.0, nan)`` returns 1.0, while JS's ``Math.min(1, NaN)`` is
    NaN; a NaN x therefore comes back as NaN here, as in the JS.
    """
    if x != x:  # NaN
        return x
    return max(lo, min(hi, x))


def smoothstep(e0: float, e1: float, x: float) -> float:
    """``Fly.smoothstep``: Hermite step, 0 below e0, 1 above e1, flat at both ends."""
    t = clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def smoothstep_arr(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    """``Fly.smoothstep`` over an array: the same Hermite step, clamped to [0, 1], elementwise.

    The dtype is the input's (float32 stays float32): the arithmetic is the scalar's.
    NaN in gives NaN out, as in the scalar.
    """
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def median(values: Iterable[float]) -> float:
    """``Fly.median``: the element at index ``n >> 1`` of the sorted values.

    NOT the statistician's median for even n: the JS takes the upper middle
    element, and parity with it matters more than convention. Empty input
    raises IndexError (the JS returns undefined).
    """
    s = sorted(values)
    return s[len(s) >> 1]


def mad(values: Sequence[float]) -> float:
    """``Fly.mad``: 1.4826 x median absolute deviation, or 1 when that is 0.

    The ``|| 1`` of the JS is kept: a set with no spread must not make a
    later division blow up.
    """
    m = median(values)
    return 1.4826 * median([abs(x - m) for x in values]) or 1


# ---------------------------------------------------------------------------
# Sky directions and the TAN projection (Fly.vec .. Fly.tanUnproject).
# Here rather than in geometry.py so wcs, gaia and the star code do not wait
# on the geometry task. A wcs is the Fly.js dict
# {crval1, crval2, crpix1, crpix2, cd: [cd11, cd12, cd21, cd22]}.
# ---------------------------------------------------------------------------


def vec(ra: float, dec: float) -> list[float]:
    """``Fly.vec``: unit vector of a direction given in degrees."""
    a, d = ra * RAD, dec * RAD
    return [math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d)]


def radec(v: Sequence[float]) -> dict[str, float]:
    """``Fly.radec``: ``{ra, dec}`` degrees of a (not necessarily unit) vector; ra in [0, 360)."""
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    ra = math.atan2(v[1], v[0]) / RAD
    return {"ra": math.fmod(ra + 360, 360), "dec": math.asin(v[2] / n) / RAD}


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    """``Fly.dot``: 3-vector dot product."""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def separation(a: dict, b: dict) -> float:
    """``Fly.separation``: angle between two ``{ra, dec}`` directions, degrees."""
    c = max(-1.0, min(1.0, dot(vec(a["ra"], a["dec"]), vec(b["ra"], b["dec"]))))
    return math.acos(c) / RAD


def tan_project(ra: float, dec: float, w: dict) -> Point:
    """``Fly.tanProject``: gnomonic (TAN) projection through FITS WCS keywords, 0-based pixels.

    Pixel i's centre is at i: CRPIX (1-based) lands on ``crpix - 1``.
    """
    a, d, a0, d0 = ra * RAD, dec * RAD, w["crval1"] * RAD, w["crval2"] * RAD
    cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    xi = math.cos(d) * math.sin(a - a0) / cosc / RAD
    eta = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cosc / RAD
    c = w["cd"]
    det = c[0] * c[3] - c[1] * c[2]
    dx = (c[3] * xi - c[1] * eta) / det
    dy = (-c[2] * xi + c[0] * eta) / det
    return Point(w["crpix1"] - 1 + dx, w["crpix2"] - 1 + dy)


def tan_unproject(x: float, y: float, w: dict) -> dict[str, float]:
    """``Fly.tanUnproject``: 0-based pixel -> ``{ra, dec}`` degrees (inverse of :func:`tan_project`)."""
    c = w["cd"]
    dx, dy = x - (w["crpix1"] - 1), y - (w["crpix2"] - 1)
    xi = (c[0] * dx + c[1] * dy) * RAD
    eta = (c[2] * dx + c[3] * dy) * RAD
    a0, d0 = w["crval1"] * RAD, w["crval2"] * RAD
    den = math.cos(d0) - eta * math.sin(d0)
    a = a0 + math.atan2(xi, den)
    d = math.atan2(math.sin(d0) + eta * math.cos(d0), math.sqrt(xi * xi + den * den))
    return {"ra": math.fmod(a / RAD + 360, 360), "dec": d / RAD}


def grown_rect(r: Rect, grow: float, w: int, h: int) -> Rect:
    """``Fly.grownRect``: the detection's rect grown by ``grow`` px, clipped to the w x h image."""
    return Rect(max(0, r.x0 - grow), max(0, r.y0 - grow), min(w, r.x1 + grow), min(h, r.y1 + grow))


def detection_rect(x: float, y: float, area: float) -> Rect:
    """``Fly.detectionRect``: a detection's core from its area.

    A square of half-width sqrt(area/pi), at least one pixel, x1/y1
    exclusive. PixInsight 1.9's StarDetector gave no rectangle of its own.
    JS ``Math.round`` rounds halves up (toward +inf), unlike Python's
    banker's ``round``: ``floor(v + 0.5)`` keeps the JS result.
    """
    r = max(1, math.floor(math.sqrt(max(0.0, area) / math.pi) + 0.5))
    return Rect(math.floor(x - r), math.floor(y - r), math.floor(x + r) + 1, math.floor(y + r) + 1)


def square_rect(x: float, y: float, r: float, w: int, h: int) -> Rect:
    """``Fly.squareRect``: a square footprint of half-width r around (x, y), clipped; x1/y1 exclusive."""
    return Rect(max(0, math.floor(x - r)), max(0, math.floor(y - r)),
                min(w, math.floor(x + r) + 1), min(h, math.floor(y + r) + 1))


def js_round(v: float) -> float:
    """JS ``Math.round``: halves round toward +infinity (Python's ``round`` rounds halves to even).

    Computed as ``floor(v)`` plus one when the fraction is at least a half, so that
    ``0.49999999999999994`` stays 0 (``floor(v + 0.5)`` rounds the sum up to 1.0) and large
    doubles are not disturbed by an addition. Finite input returns a Python int, as ``math.floor``
    does; NaN and +/-Infinity pass through as JS returns them. JS gives -0 for -0.5 and -0.0;
    this returns 0, which every caller prints or uses as 0.
    """
    if not math.isfinite(v):
        return v
    f = math.floor(v)
    return f + 1 if v - f >= 0.5 else f


def screen(starless: np.ndarray, stars: np.ndarray) -> np.ndarray:
    """Screen blend ``1 - (1 - S)(1 - T)``: how the stars layer sits on the starless one."""
    s = np.asarray(starless, dtype=np.float32)
    t = np.asarray(stars, dtype=np.float32)
    return (1 - (1 - s) * (1 - t)).astype(np.float32)


def unscreen(image: np.ndarray, starless: np.ndarray) -> np.ndarray:
    """Stars layer from an image and its starless version (``Steps.deriveStarsByUnscreen``).

    PixelMath there: ``iif(S < 1, max(0, (I - S)/(1 - S)), 0)`` truncated to [0, 1].
    The inverse of :func:`screen` wherever S < 1.
    """
    i = np.asarray(image, dtype=np.float64)
    s = np.asarray(starless, dtype=np.float64)
    out = np.zeros(np.broadcast(i, s).shape, dtype=np.float64)
    ok = s < 1
    out[ok] = np.maximum(0.0, (np.broadcast_to(i, out.shape)[ok] - np.broadcast_to(s, out.shape)[ok])
                         / (1 - np.broadcast_to(s, out.shape)[ok]))
    return np.clip(out, 0.0, 1.0).astype(np.float32)
