"""Narrowband normalisation of a three-channel palette composite, in numpy.

Loom calls PixInsight's NarrowbandNormalization once per palette with fixed neutral settings
(lib/Steps.js:2162-2199). This module is the numpy replacement for that call. Only the neutral mode is implemented:
lightness off, blend mode 1, every boost 1, SCNR 0, shadow point 1, highlight reduction 1, brightness 1, Ha blend 0.6.
The algorithm and its sources are recorded in docs/loom-nbnorm.md.

The input is the palette composite as three linear planes, already mapped to R, G and B by the palette (SHO: R=S,
G=H, B=O; HOO: R=H, G=O, B=O; HSO: R=H, G=S, B=O). The Ha-carrying channel is the reference.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

#: Palette name -> index used by PixInsight's NarrowbandNormalization.palette parameter (lib/Steps.js:2162).
PALETTE_INDEX = {"HOO": 0, "SHO": 1, "HSO": 2}

#: Narrowband source carried by each output channel (R, G, B) for each palette.
PALETTE_SOURCES = {
    "HOO": ("H", "O", "O"),
    "SHO": ("S", "H", "O"),
    "HSO": ("H", "S", "O"),
}

#: Names the algorithm in the cache key, so a result made by another one is never served again.
METHOD = "sky-offset-v1"

#: Weight of the Ha-carrying reference in every channel the palette does not map to Ha.
HA_BLEND = 0.6

#: Scale factor between the median absolute deviation and the standard deviation of a Gaussian.
MAD_TO_SIGMA = 1.4826


def _palette_name(palette: str) -> str:
    if not isinstance(palette, str) or palette not in PALETTE_INDEX:
        raise ValueError(f"unknown narrowband palette {palette!r}; expected one of {sorted(PALETTE_INDEX)}")
    return palette


def stage_params(palette: str) -> dict[str, str]:
    """Parameters recorded for the stage: the palette name, validated."""
    return {"palette": _palette_name(palette), "method": METHOD}


def reference_index(palette: str) -> int:
    """Index (0 = R, 1 = G, 2 = B) of the channel that carries Ha for ``palette``."""
    return PALETTE_SOURCES[_palette_name(palette)].index("H")


def _check_planes(planes: Sequence[np.ndarray]) -> list[np.ndarray]:
    if len(planes) != 3:
        raise ValueError(f"expected 3 planes (R, G, B), got {len(planes)}")
    out = [np.asarray(p, dtype=np.float64) for p in planes]
    shape = out[0].shape
    if len(shape) != 2:
        raise ValueError("planes must be two-dimensional")
    for p in out:
        if p.shape != shape:
            raise ValueError("all planes must have the same shape")
        if not np.all(np.isfinite(p)):
            raise ValueError("planes must be finite")
    return out


def background_and_scale(plane: np.ndarray) -> tuple[float, float]:
    """Robust background and noise of one plane: median, and 1.4826 times the median absolute deviation.

    Raises ValueError if the plane has no spread (its MAD is zero), since it cannot be scaled to a reference.
    """
    background = float(np.median(plane))
    sigma = MAD_TO_SIGMA * float(np.median(np.abs(plane - background)))
    if not sigma > 0.0:
        raise ValueError(
            f"channel has no noise: its median absolute deviation around the median {background:g} is zero, so it "
            "cannot be scaled to the Ha reference (a flat or saturated channel); pass a channel with spread"
        )
    return background, sigma


def scale_to_reference(planes: Sequence[np.ndarray], palette: str) -> list[np.ndarray]:
    """Give every channel the reference channel's background and noise level (float64, unclipped).

    For channel c with background b_c and noise s_c, and reference r with b_r and s_r:
        y_c = (x_c - b_c) * (s_r / s_c) + b_r
    The reference channel is returned unchanged. Because the map is affine with a positive slope, the median of
    each output channel equals b_r.
    """
    chans = _check_planes(planes)
    ref = reference_index(palette)
    b_ref, s_ref = background_and_scale(chans[ref])
    out: list[np.ndarray] = []
    for i, plane in enumerate(chans):
        if i == ref:
            out.append(plane.copy())
            continue
        b_c, s_c = background_and_scale(plane)
        out.append((plane - b_c) * (s_ref / s_c) + b_ref)
    return out


#: Side, in pixels, of the blocks averaged before the sky is read, and the percentile of the block means taken as the sky.
SKY_BLOCK = 16
SKY_PERCENTILE = 2.0


def sky_level(plane: np.ndarray) -> float:
    """The sky background of one plane: a low percentile of its block means.

    The median is no use here: on a frame filled by nebulosity it is the nebula's level, which differs between lines
    (S is weaker than H). Averaging blocks first keeps the percentile above the noise, which is the same in every
    channel only on average.
    """
    h, w = plane.shape
    b = SKY_BLOCK
    if h < 2 * b or w < 2 * b:
        return float(np.percentile(plane, SKY_PERCENTILE))
    blocks = plane[: h - h % b, : w - w % b].reshape(h // b, b, w // b, b).mean(axis=(1, 3))
    return float(np.percentile(blocks, SKY_PERCENTILE))


def normalize(planes: Sequence[np.ndarray], palette: str) -> list[np.ndarray]:
    """Give the palette composite ``planes`` (R, G, B) one sky level, keeping the line ratios of the nebula.

    Each channel is shifted so its sky (``sky_level``) equals the Ha channel's, and clipped to [0, 1]. Nothing is scaled and
    no channel is mixed into another: an earlier version matched every channel's noise to Ha's and blended 60% of Ha into
    the other two, a guess at PixInsight's NarrowbandNormalization (docs/loom-nbnorm.md) that left every channel with
    about the same signal, so a palette came out nearly grey. A colour cast in the sky is what is removed here, the
    palette's colours are the data's.
    """
    ref = reference_index(palette)
    chans = _check_planes(planes)
    target = sky_level(chans[ref])
    return [np.clip(c - sky_level(c) + target, 0.0, 1.0).astype(np.float32) for c in chans]
