"""Temporary stretch for the SyQon and Studio hand-off, and the PRISM target choice.

Port of Loom's temporary stretch (lib/StepsSyqon.js:339-486, the stretch and its exact reverse), of the PixelMath
expression builders (lib/StepsSyqon.js:418-458), of the SyQon constants (lib/StepsSyqon.js:232-237) and of the PRISM
target selection (lib/StepsSyqon.js:977-1145).

The stretch works on float64 planes: each channel's minimum is recorded, the data are normalised to that minimum,
the normalised median is moved to a target with a midtone transfer, and the reverse undoes the transfer and then
the normalisation. Loom runs every step through PixelMath with truncation to [0, 1]: the normalised values are
clipped before the median is read, the forward result is clipped, and the reverse is clipped after each of its two
steps. This module reproduces those clips. Loom's PixelMath carries the same numbers as 16-decimal text (see the
expression builders); this module keeps the float64 values, and the builders are provided for the expression
strings themselves.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

import numpy as np

from loomsiril.loom.stretch import mtf_apply, mtf_midtone_for

__all__ = [
    "SYQON_MTF_TARGET",
    "SYQON_TILE_SIZE",
    "SYQON_OVERLAP",
    "SYQON_PAD",
    "SYQON_TIMEOUT_MS",
    "PRISM_DEFAULT_TARGET",
    "PRISM_CORPUS_STD_MIN",
    "PRISM_CORPUS_STD_MAX",
    "PRISM_HIGHLIGHT_CAP",
    "PRISM_TARGET_MAX",
    "StretchState",
    "Background",
    "Stats",
    "RaisedTarget",
    "stretch_forward",
    "stretch_inverse",
    "mtf_midtone_for",
    "mtf_apply",
    "syqon_normalize_expression",
    "syqon_mtf_expression",
    "syqon_inverse_mtf_expression",
    "syqon_denormalize_expression",
    "syqon_format_stretch",
    "prism_mtf_target",
    "prism_background",
    "prism_samples",
    "prism_normalised_sample",
    "prism_stretch_stats",
    "prism_raised_target",
]

log = logging.getLogger(__name__)

# lib/StepsSyqon.js:232-237
SYQON_MTF_TARGET = 0.12
SYQON_TILE_SIZE = 512
SYQON_OVERLAP = 128
SYQON_PAD = 512
SYQON_TIMEOUT_MS = 20 * 60 * 1000

# lib/StepsSyqon.js:977-982
PRISM_DEFAULT_TARGET = 0.15
PRISM_CORPUS_STD_MIN = 0.0474
PRISM_CORPUS_STD_MAX = 0.1288
PRISM_HIGHLIGHT_CAP = 0.98
PRISM_TARGET_MAX = 0.70

# PixelMath's symbol for the pixel being rewritten (lib/StepsSyqon.js:418-458).
_PIXEL = "$" + "T"

# BT.709 luminance weights, as the PRISM paper uses them (lib/StepsSyqon.js:1000).
_LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)


# ---------------------------------------------------------------------------
# Number text as Loom's format( "%.Nf", v ) writes it
# ---------------------------------------------------------------------------


def _fixed(value: float, digits: int) -> str:
    """``Number.prototype.toFixed`` for a finite number: the exact binary value rounded half away from zero.

    Python's own ``%`` formatting rounds exact ties to even, JavaScript rounds them up in magnitude; a 2**-17
    value is such a tie at 16 decimals.
    """
    x = float(value)
    if not math.isfinite(x):
        raise ValueError(f"cannot format non-finite number {value!r}")
    d = Decimal(x)
    if d == 0:
        d = Decimal(0)
    return format(d.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP), "f")


def _f16(value: float) -> str:
    return _fixed(value, 16)


# ---------------------------------------------------------------------------
# PixelMath expression strings (lib/StepsSyqon.js:418-458)
# ---------------------------------------------------------------------------


def syqon_normalize_expression(min_value: float) -> str:
    """PixelMath rescaling the pixel so that ``min_value`` becomes 0."""
    m = _f16(min_value)
    return "(" + _PIXEL + "-" + m + ")/(1-" + m + ")"


def syqon_mtf_expression(om: float, tm: float) -> str:
    """PixelMath for the midtone transfer taking the median ``om`` to ``tm``."""
    o, t = _f16(om), _f16(tm)
    return "((" + o + "-1)*" + t + "*" + _PIXEL + ")/(" + o + "*(" + t + "+" + _PIXEL + "-1)-" + t + "*" + _PIXEL + ")"


def syqon_inverse_mtf_expression(om: float, tm: float) -> str:
    """PixelMath undoing :func:`syqon_mtf_expression`: the median ``tm`` back to ``om``."""
    o, t = _f16(om), _f16(tm)
    return (
        "(" + o + "*" + _PIXEL + "*(" + t + "-1))/(" + o + "*" + t + "-" + o + "*" + _PIXEL + "+" + t + "*" + _PIXEL
        + "-" + t + ")"
    )


def syqon_denormalize_expression(min_value: float) -> str:
    """PixelMath undoing :func:`syqon_normalize_expression`: 0 back to ``min_value``."""
    m = _f16(min_value)
    return "(" + _PIXEL + "*(1-" + m + ")+" + m + ")"


def syqon_format_stretch(values: Sequence[float]) -> list[str]:
    """The numbers as the stretch writes them into PixelMath: 16 decimals."""
    return [_f16(v) for v in values]


# ---------------------------------------------------------------------------
# Temporary stretch (lib/StepsSyqon.js:339-486)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StretchState:
    """What the reverse needs: the target median, and per channel the minimum and the normalised median.

    A linked stretch stores the one shared minimum and the mean median in every channel's slot, as Loom does.
    """

    target: float
    linked: bool
    minima: tuple[float, ...]
    medians: tuple[float, ...]


def _check_target(target: float) -> float:
    t = float(target)
    if not (math.isfinite(t) and 0.0 < t < 1.0):
        raise ValueError(f"stretch target must lie in (0, 1), got {target!r}")
    return t


def _check_median(om: float, where: str) -> float:
    om = float(om)
    if not (math.isfinite(om) and 0.0 < om < 1.0):
        raise ValueError(f"normalised median {om!r} outside (0, 1) for {where}")
    return om


def _normalise(a: np.ndarray, minimum: float) -> np.ndarray:
    """Rescale so ``minimum`` becomes 0, truncated to [0, 1] as Loom's PixelMath is (lib/StepsSyqon.js:339-370)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.clip((a - minimum) / (1.0 - minimum), 0.0, 1.0)


def _mtf_forward(x: np.ndarray, om: float, tm: float) -> np.ndarray:
    """Median ``om`` to ``tm``, clipped to [0, 1]."""
    with np.errstate(divide="ignore", invalid="ignore"):
        y = ((om - 1.0) * tm * x) / (om * (tm + x - 1.0) - tm * x)
    return np.clip(y, 0.0, 1.0)


def _mtf_inverse(y: np.ndarray, om: float, tm: float) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return (om * y * (tm - 1.0)) / (om * tm - om * y + tm * y - tm)


def stretch_forward(
    planes: Sequence[np.ndarray], target: float, linked: bool
) -> tuple[list[np.ndarray], StretchState]:
    """Stretch float planes toward ``target``; returns the stretched float64 planes and the reverse state.

    Unlinked: each plane has its own minimum and normalised median. Linked: one minimum (the smallest over the
    planes) and the mean of the normalised medians, so every plane gets the same curve. A normalised median
    outside (0, 1) raises ValueError, as Loom throws.
    """
    if not planes:
        raise ValueError("stretch_forward needs at least one plane")
    tm = _check_target(target)
    arrs = [np.asarray(p, dtype=np.float64) for p in planes]
    mins = [float(np.min(a)) for a in arrs]

    if linked:
        all_min = min(mins)
        norm = [_normalise(a, all_min) for a in arrs]
        om_linked = sum(float(np.median(n)) for n in norm) / len(arrs)
        om_linked = _check_median(om_linked, "linked stretch")
        out = [_mtf_forward(n, om_linked, tm) for n in norm]
        state = StretchState(
            target=tm,
            linked=True,
            minima=tuple(all_min for _ in arrs),
            medians=tuple(om_linked for _ in arrs),
        )
        return out, state

    out = []
    oms: list[float] = []
    for c, (a, m) in enumerate(zip(arrs, mins)):
        n = _normalise(a, m)
        om = _check_median(float(np.median(n)), f"channel {c}" if len(arrs) > 1 else "the plane")
        oms.append(om)
        out.append(_mtf_forward(n, om, tm))
    state = StretchState(target=tm, linked=False, minima=tuple(mins), medians=tuple(oms))
    return out, state


def stretch_inverse(planes: Sequence[np.ndarray], state: StretchState) -> list[np.ndarray]:
    """Undo :func:`stretch_forward`: the median transfer first, then the normalisation.

    Loom clips after each of the two PixelMath steps (lib/StepsSyqon.js:513-523), so the transfer's output and the
    final values are both truncated to [0, 1]; a tool result outside that range comes back clamped, and a negative
    original minimum returns negative values as 0. ``planes`` must match the state's channel count.
    """
    if len(planes) != len(state.medians):
        raise ValueError(f"state has {len(state.medians)} channel(s); got {len(planes)} plane(s)")
    out = []
    for p, om, mn in zip(planes, state.medians, state.minima):
        y = np.asarray(p, dtype=np.float64)
        x = np.clip(_mtf_inverse(y, om, state.target), 0.0, 1.0)
        out.append(np.clip(x * (1.0 - mn) + mn, 0.0, 1.0))
    return out


# ---------------------------------------------------------------------------
# PRISM target selection (lib/StepsSyqon.js:977-1145)
# ---------------------------------------------------------------------------


class Background(NamedTuple):
    all_min: float
    x0: float


class Stats(NamedTuple):
    mean: float
    std: float


class RaisedTarget(NamedTuple):
    target: float
    std: float
    hi: float


def _channels(planes: Sequence[np.ndarray]) -> int:
    """Three colour planes or one: mono images repeat their one plane (lib/StepsSyqon.js:1043)."""
    return 3 if len(planes) >= 3 else 1


def prism_background(planes: Sequence[np.ndarray]) -> Background:
    """The darkest level over the channels, and the mean of the channel medians normalised against it."""
    arrs = [np.asarray(p, dtype=np.float64) for p in planes]
    nch = _channels(arrs)
    all_min = float(np.min(arrs[0]))
    for c in range(1, nch):
        all_min = min(all_min, float(np.min(arrs[c])))
    x0 = 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        for c in range(nch):
            x0 += (float(np.median(arrs[c])) - all_min) / (1.0 - all_min)
    return Background(all_min=all_min, x0=x0 / nch)


def prism_normalised_sample(planes: Sequence[np.ndarray], x: int, y: int, nch: int, all_min: float) -> list[float]:
    """One normalised [r, g, b] sample; mono images give their one plane in all three slots."""
    v = []
    for k in range(3):
        raw = float(planes[k if nch > 1 else 0][y, x])
        v.append((raw - all_min) / (1.0 - all_min))
    return v


def prism_samples(planes: Sequence[np.ndarray], all_min: float, step: int) -> tuple[np.ndarray, float]:
    """One normalised sample every ``step`` pixels, and the 99.9th percentile of the brightest channel."""
    arrs = [np.asarray(p, dtype=np.float64) for p in planes]
    nch = _channels(arrs)
    h, w = arrs[0].shape
    px: list[list[float]] = []
    bright: list[float] = []
    for y in range(0, h, step):
        for x in range(0, w, step):
            v = prism_normalised_sample(arrs, x, y, nch, all_min)
            px.append(v)
            bright.append(max(v[0], max(v[1], v[2])))
    if not bright:
        raise ValueError("prism_samples: the image has no pixels")
    bright.sort()
    idx = min(len(bright) - 1, int(math.floor(0.999 * len(bright))))
    return np.asarray(px, dtype=np.float64).reshape(-1, 3), bright[idx]


def prism_stretch_stats(px: np.ndarray, x0: float, target: float) -> Stats:
    """Mean and standard deviation of the BT.709 luminance of ``px`` after a pre-stretch to ``target``."""
    m = mtf_midtone_for(x0, target)
    xn = np.where(px < 0, 0.0, px)
    t = np.asarray(mtf_apply(m, xn), dtype=np.float64)
    lum = t[:, 0] * _LUMA_WEIGHTS[0] + t[:, 1] * _LUMA_WEIGHTS[1] + t[:, 2] * _LUMA_WEIGHTS[2]
    n = lum.shape[0]
    mean = float(lum.sum()) / n
    sum2 = float((lum * lum).sum())
    return Stats(mean=mean, std=math.sqrt(max(0.0, sum2 / n - mean * mean)))


def prism_raised_target(px: np.ndarray, x0: float, p999: float, std_at_default: float) -> RaisedTarget:
    """The least contrasty target from the default up that reaches the corpus minimum, or the closest one.

    Stops at the highlight cap. ``std_at_default`` seeds the search.
    """
    best = RaisedTarget(target=PRISM_DEFAULT_TARGET, std=std_at_default, hi=0.0)
    best_dist = abs(std_at_default - PRISM_CORPUS_STD_MIN)
    t = PRISM_DEFAULT_TARGET
    while t <= PRISM_TARGET_MAX + 1e-9:
        m = mtf_midtone_for(x0, t)
        hi = float(mtf_apply(m, p999))
        if hi > PRISM_HIGHLIGHT_CAP:
            break
        st = prism_stretch_stats(px, x0, t)
        dist = 0.0 if st.std > PRISM_CORPUS_STD_MIN else abs(st.std - PRISM_CORPUS_STD_MIN)
        if dist < best_dist or (dist == 0 and st.std < best.std):
            best_dist = dist
            best = RaisedTarget(target=t, std=st.std, hi=hi)
        t += 0.05
    return best


def prism_mtf_target(planes: Sequence[np.ndarray], stride: int | None = None) -> float:
    """The stretch target for PRISM: the default when the image is in the training range, else raised.

    ``planes`` is one plane (mono) or three (colour), each (height, width). ``stride`` is the sampling step in
    pixels (29 when omitted or 0).
    """
    arrs = [np.asarray(p, dtype=np.float64) for p in planes]
    step = stride or 29
    bg = prism_background(arrs)
    x0 = bg.x0
    if not (x0 > 0 and x0 < 1):
        log.warning("prism: could not characterise the image; using the default")
        return PRISM_DEFAULT_TARGET

    px, p999 = prism_samples(arrs, bg.all_min, step)
    at_default = prism_stretch_stats(px, x0, PRISM_DEFAULT_TARGET)
    if PRISM_CORPUS_STD_MIN <= at_default.std <= PRISM_CORPUS_STD_MAX:
        log.info(
            "prism: target %.2f (std %.4f, inside the training range %s-%s)",
            PRISM_DEFAULT_TARGET,
            at_default.std,
            PRISM_CORPUS_STD_MIN,
            PRISM_CORPUS_STD_MAX,
        )
        return PRISM_DEFAULT_TARGET

    best = prism_raised_target(px, x0, p999, at_default.std)
    log.info(
        "prism: target %.2f (std %.4f, highlights %.3f); raised from %s where std was %.4f",
        best.target,
        best.std,
        best.hi,
        PRISM_DEFAULT_TARGET,
        at_default.std,
    )
    if best.std < PRISM_CORPUS_STD_MIN:
        log.warning(
            "prism: this image is flatter than anything in the training corpus (std %.4f < %s) at every usable "
            "target; denoising may still over-smooth",
            best.std,
            PRISM_CORPUS_STD_MIN,
        )
    return best.target
