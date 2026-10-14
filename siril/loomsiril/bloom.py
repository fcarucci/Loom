"""Bloom and headroom maths on the stars layer (E4).

Ported from Loom ``script/lib/Render.js`` (v0.4.1), the per-pixel reference versions:
``Render.saturateStars``, ``Render.starsToHeadroomJs``, ``Render.bloomSigmas``,
``Render.whitenPastWhite``, ``Render.pastWhite`` and ``Render.bloomJs``. The PixInsight
image-operation paths (``Render.bloom`` through ``ImageOp_*``, ``Render.starsToHeadroom``)
are not ported: this module is the path that runs outside PixInsight, which is what the
Node suite and the Python port use.

Planes are float32 ``(h, w)`` arrays, one per channel, changed in place as the JS does.
Each stored result is rounded to float32 where the JS stores it.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

import numpy as np

from loomsiril.core import smoothstep_arr
from loomsiril.resample import box_blur, box_radius

__all__ = [
    "BLOOM_GAINS",
    "BLOOM_GLARE",
    "BLOOM_TIGHT",
    "BLOOM_WIDE",
    "bloom",
    "bloom_sigmas",
    "past_white",
    "saturate_stars",
    "stars_to_headroom",
    "whiten_past_white",
]

BLOOM_TIGHT: float = 0.005    # the round-core glow's sigma, of the frame's short side (0.0035 left a hard edge)
BLOOM_WIDE: float = 0.02      # the wide glow's
BLOOM_GLARE: float = 0.06     # the very wide, faint glare's
BLOOM_GAINS: tuple[float, float, float, float] = (0.6, 0.25, 0.35, 0.1)   # tight, wide, the whitening share, glare


def saturate_stars(T: list[np.ndarray], sat: float) -> None:
    """``Render.saturateStars``: the stars layer's colour scaled by ``sat`` about each pixel's mean (brightness kept).

    In place, for three or more channels; ``sat == 1`` changes nothing.
    """
    if sat == 1 or len(T) < 3:
        return
    t0 = T[0].astype(np.float64)
    t1 = T[1].astype(np.float64)
    t2 = T[2].astype(np.float64)
    m = (t0 + t1 + t2) / 3
    for c in range(3):
        T[c][...] = m + (T[c].astype(np.float64) - m) * sat


def stars_to_headroom(T: list[np.ndarray], factor: Any) -> None:
    """``Render.starsToHeadroomJs``: every channel multiplied by ``factor`` (a number or an (h, w) map), in place.

    The map keeps each star's hue (every channel by the same factor); 1 elsewhere.
    """
    f = np.asarray(factor, dtype=np.float64)
    for c in range(len(T)):
        T[c][...] = T[c].astype(np.float64) * f


def bloom_sigmas(short: float, seconds: Optional[float]) -> list[float]:
    """``Render.bloomSigmas``: the three glows' sigmas (px) for a frame whose short side is ``short``.

    The wide and the glare breathe by 5% with the clock (``seconds``; None or 0 holds them).
    """
    breathe = 1 + 0.05 * math.sin(2 * math.pi * 0.3 * (seconds or 0))
    return [BLOOM_TIGHT * short, BLOOM_WIDE * short * breathe, BLOOM_GLARE * short * breathe]


def whiten_past_white(T: list[np.ndarray]) -> None:
    """``Render.whitenPastWhite``: past white a core whitens: its channels are drawn toward its brightest.

    Per pixel, with top the brightest channel: where top > 1, each channel moves by
    (top - L) x 0.8 smoothstep(1.5, 4, top), as a sensor saturates (from 1.5x, so the rim
    keeps its colour). In place.
    """
    top = np.zeros(T[0].shape, dtype=np.float64) if T else np.zeros(0)
    for c in range(len(T)):
        top = np.maximum(top, T[c].astype(np.float64))
    past = ~(top <= 1)
    f = 0.8 * smoothstep_arr(1.5, 4, top)
    for c in range(len(T)):
        t = T[c].astype(np.float64)
        T[c][...] = np.where(past, t + (top - t) * f, t)


def past_white(T: Sequence[np.ndarray]) -> Optional[list[np.ndarray]]:
    """``Render.pastWhite``: each channel's light past white (T - 1 where T > 1), or None when there is none."""
    any_past = False
    E: list[np.ndarray] = []
    for t in T:
        over = t > 1
        if over.any():
            any_past = True
        E.append(np.where(over, t.astype(np.float64) - 1, 0.0).astype(np.float32))
    return E if any_past else None


def bloom(T: list[np.ndarray], w: int, h: int, amount: float = 1.0, seconds: Optional[float] = 0.0) -> None:
    """``Render.bloomJs``: light past white spreads into a tight glow, a wide faint one and a very wide glare.

    The bloom is a share of it white in every channel, as a sensor saturates. Nothing past
    white (the image itself) blooms nothing. ``amount <= 0`` (or NaN) is a no-op. In place.
    """
    if not (amount > 0):
        return
    if len(T) > 1:
        whiten_past_white(T)
    E = past_white(T)
    if E is None:
        return
    sg = bloom_sigmas(min(w, h), seconds)
    G = BLOOM_GAINS
    n_e = len(E)
    white = np.zeros((h, w), dtype=np.float32)
    for c in range(n_e):
        white = (white.astype(np.float64) + E[c].astype(np.float64) / n_e).astype(np.float32)
    tight_w = box_blur(white.copy(), w, h, box_radius(sg[0]))
    glare = box_blur(white, w, h, box_radius(sg[2]))
    for c in range(len(T)):
        tight = box_blur(E[c].copy(), w, h, box_radius(sg[0]))
        wide = box_blur(E[c].copy(), w, h, box_radius(sg[1]))
        glow = (G[0] * tight.astype(np.float64) + G[1] * wide.astype(np.float64)
                + G[2] * tight_w.astype(np.float64) + G[3] * glare.astype(np.float64))
        T[c][...] = (T[c].astype(np.float64) + amount * glow).astype(np.float32)
