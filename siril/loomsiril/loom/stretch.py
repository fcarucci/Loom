"""Deterministic histogram (MTF) stretch: parameters, clip-safe black point, reference apply, Siril commands.

Port of the MTF branch of Loom's stretch (``lib/Steps.js:2820-3066``, the midtone transfer in
``lib/StepsSyqon.js:983-997``). Pure numpy/scipy; no Siril or PixInsight object is touched here.

Parameter rule: a black point ``c0`` and a midtone ``m`` chosen so that the sky median lands on a target level after
the transfer ``mtf_apply(m, (x - c0) / (1 - c0))``. A linked stretch uses one black point and one midtone for all
colour planes; an unlinked stretch uses one pair per plane.
"""

from __future__ import annotations

from typing import Callable, Sequence

import math

import numpy as np
from scipy import ndimage

# Sky level the transfer maps the background to (lib/Steps.js:2820-2836).
STRETCH_SKY_TARGET = 0.25
# Stars master target, derived from the transfer slope at the sky level (lib/Steps.js:2837-2849).
STRETCH_STARS_TARGET = 0.5
# Fraction of pixels allowed to fall below the black point (lib/Steps.js:2950-2953).
STRETCH_CLIP_BUDGET = 1e-4

NO_SIGNAL_MESSAGE = (
    "This image has no signal above its darkest pixel; there is nothing to stretch."
)


def mtf_midtone_for(x0: float, target: float) -> float:
    """Midtone ``m`` such that ``mtf_apply(m, x0) == target`` (lib/StepsSyqon.js:983-985)."""
    return x0 * (target - 1) / (2 * target * x0 - target - x0)


def mtf_apply(m, x):
    """Midtone transfer ``((m - 1) x) / ((2m - 1) x - m)`` in float64 (lib/StepsSyqon.js:987-991).

    Accepts a scalar or an array; returns a Python float for a scalar and an ndarray otherwise.
    """
    arr = np.asarray(x, dtype=np.float64)
    d = ((2.0 * m - 1.0) * arr) - m
    out = ((m - 1.0) * arr) / d
    return float(out) if out.ndim == 0 else out


def _normalise(value: float, c0: float) -> float:
    """``(value - c0) / (1 - c0)`` with JavaScript's float semantics, so a black point of 1.0 cannot raise.

    Loom divides doubles: 0/0 is NaN (which the no-signal check then rejects) and a non-zero numerator gives an
    infinity. Python would raise ZeroDivisionError at ``c0 == 1``, so that case is mirrored here explicitly.
    """
    num = value - c0
    denom = 1.0 - c0
    if denom == 0.0:
        if num == 0.0 or math.isnan(num):
            return float("nan")
        return math.copysign(math.inf, num)
    return num / denom


def stretch_parameters_from(median: float, minimum: float, target: float | None = None) -> dict:
    """``{"c0", "m", "skyOut"}`` for one plane (lib/Steps.js:2850-2860).

    ``c0`` is the minimum clamped to zero; ``skyOut`` is the level the sky lands on, returned so callers can
    check it. A median that does not sit above ``c0`` has nothing to stretch and raises ``ValueError``.
    """
    t = STRETCH_SKY_TARGET if target is None else target
    c0 = float(max(0.0, minimum))
    x = _normalise(median, c0)
    if not x > 0:
        raise ValueError(NO_SIGNAL_MESSAGE)
    m = mtf_midtone_for(x, t)
    return {"c0": c0, "m": m, "skyOut": mtf_apply(m, x)}


def robust_minimum_per_channel(
    planes: Sequence[np.ndarray], warn: Callable[[str], object] | None = None
) -> list[float]:
    """Per-plane minimum of a 3x3 median-filtered copy: one isolated cold pixel cannot set it (lib/Steps.js:2898-2949).

    If the filter fails for any reason the raw minimum is used instead and ``warn`` (when given) is called with the
    reason, because the raw minimum is only less robust, never wrong.
    """
    try:
        return [
            float(ndimage.median_filter(np.asarray(p), size=3, mode="nearest").min()) for p in planes
        ]
    except Exception as exc:  # noqa: BLE001 - any failure falls back to the raw minimum, as Loom does
        if warn is not None:
            warn(
                "stretch: could not compute a defect-robust minimum "
                f"({exc}); using the raw minimum, which one cold pixel can drag down"
            )
        return [float(np.asarray(p).min()) for p in planes]


def clip_safe_black_point(plane: np.ndarray, robust_min: float) -> float:
    """Black point lowered so that at most ``STRETCH_CLIP_BUDGET`` of the plane lies at or below it.

    Port of ``Steps.clipSafeBlackPoint`` (lib/Steps.js:2950-2973). The samples are the float32 values, compared as
    float64. If more pixels than the budget lie below ``robust_min``, the answer is the highest value with at most
    the budget of pixels at or below it, stepping down past ties; if even the lowest value is shared by more than
    the budget, ``min(0, lowest)``.

    The budget is ``floor(1e-4 * N)`` pixels for N samples, which is 0 below 10,000 pixels. Then any pixel below the
    robust minimum moves the black point down to ``min(0, darkest pixel)`` (Loom behaviour).
    """
    samples = np.asarray(plane, dtype=np.float32).ravel().astype(np.float64)
    budget = int(np.floor(STRETCH_CLIP_BUDGET * samples.size))
    below = np.sort(samples[samples < robust_min])
    if below.size <= budget:
        return float(robust_min)
    j = budget - 1
    while j >= 0 and below[j + 1] == below[j]:
        j -= 1
    return float(below[j]) if j >= 0 else float(min(0.0, below[0]))


def stretch_parameters_for_planes(
    planes: Sequence[np.ndarray],
    linked: bool,
    target: float | None = None,
    warn: Callable[[str], object] | None = None,
) -> dict:
    """Stretch parameters for a mono (1 plane) or colour (3 planes) image (lib/Steps.js:2975-3027).

    Mono: ``stretch_parameters_from(median, black point)``. Colour, unlinked: the top-level ``c0``/``m``/``skyOut``
    are channel 0's and ``channels`` holds one dict per plane. Colour, linked: one dict where the black point is
    the darkest clip-safe value over the three planes and the midtone follows the mean of the three medians.
    """
    if len(planes) not in (1, 3):
        raise ValueError(f"expected 1 or 3 planes, got {len(planes)}")
    mins = robust_minimum_per_channel(planes, warn)
    mins = [clip_safe_black_point(p, mn) for p, mn in zip(planes, mins)]
    meds = [float(np.median(np.asarray(p, dtype=np.float32).astype(np.float64))) for p in planes]

    if len(planes) == 1:
        return stretch_parameters_from(meds[0], mins[0], target)

    if not linked:
        per = [stretch_parameters_from(meds[i], mins[i], target) for i in range(3)]
        return {"c0": per[0]["c0"], "m": per[0]["m"], "skyOut": per[0]["skyOut"], "channels": per}

    c0 = float(max(0.0, min(mins)))
    med_mean = (meds[0] + meds[1] + meds[2]) / 3
    x = _normalise(med_mean, c0)
    if not x > 0:
        raise ValueError(NO_SIGNAL_MESSAGE)
    t = STRETCH_SKY_TARGET if target is None else target
    m = mtf_midtone_for(x, t)
    return {"c0": c0, "m": m, "skyOut": mtf_apply(m, x)}


def apply_mtf(planes: Sequence[np.ndarray], params: dict, linked: bool) -> list[np.ndarray]:
    """Numpy reference of the stretch: ``mtf_apply(m, clip((x - c0) / (1 - c0), 0, 1))`` per plane, float32 out.

    Which parameters apply to which plane follows ``applyStretch`` (lib/Steps.js:3029-3051): a linked or mono
    stretch uses the one ``(c0, m)`` on every plane; an unlinked colour stretch uses ``params["channels"][i]``
    for plane ``i``.
    """
    per_plane_params: list[dict]
    if linked or len(planes) < 3:
        per_plane_params = [params] * len(planes)
    else:
        channels = params.get("channels")
        if channels is None or len(channels) < len(planes):
            raise ValueError("unlinked stretch needs per-channel parameters ('channels')")
        per_plane_params = list(channels[: len(planes)])

    out: list[np.ndarray] = []
    for plane, p in zip(planes, per_plane_params):
        x = np.asarray(plane, dtype=np.float32).astype(np.float64)
        c0 = float(p["c0"])
        v = np.clip((x - c0) / (1.0 - c0), 0.0, 1.0)
        out.append(np.asarray(mtf_apply(float(p["m"]), v), dtype=np.float32))
    return out


def _fmt(v: float) -> str:
    """Shortest text that round-trips the double exactly."""
    return format(float(v), ".17g")


def mtf_commands(params: dict, linked: bool, n_channels: int) -> list[tuple[str, ...]]:
    """Siril ``mtf low mid high [channel]`` commands that apply the same stretch as ``apply_mtf``.

    Siril's ``mtf`` was measured to agree with ``apply_mtf`` within 2.1e-7 on float32 data.
    Linked or mono: one command over all channels. Unlinked colour: one command per channel, R then G then B.
    """
    c0, m = params["c0"], params["m"]
    if linked or n_channels < 3:
        return [("mtf", _fmt(c0), _fmt(m), "1")]
    channels = params.get("channels")
    if channels is None or len(channels) < 3:
        raise ValueError("unlinked stretch needs per-channel parameters ('channels')")
    return [
        ("mtf", _fmt(channels[i]["c0"]), _fmt(channels[i]["m"]), "1", name)
        for i, name in enumerate(("R", "G", "B"))
    ]
