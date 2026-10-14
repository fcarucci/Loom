"""Pixel-level helpers of Loom's pipeline, in numpy: rectangles and crop geometry, central medians, background
offset, white balance, halo sigma arithmetic, Gaussian blur, frequency separation and keyword consistency.

Images are lists of 2-D float planes, top-down (row 0 is the top), as ``loomsiril.io_fits`` returns them. A
rectangle is a tuple ``(x0, y0, x1, y1)`` in pixels with the end coordinates EXCLUSIVE (Loom's ``{x0,y0,x1,y1}``).

Functions that port Loom code cite its source by file and line range (lib/Steps.js etc.).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import numpy as np
from scipy import ndimage

from loomsiril.core import unscreen as _unscreen
from loomsiril.jsmath import number_str

__all__ = [
    "FWHM_PER_SIGMA",
    "FS_PEDESTAL",
    "FS_SCALE",
    "FS_SIGMA_FACTOR",
    "MIN_COMMON_PERCENT",
    "VALID_RECT_MARGIN",
    "background_offset",
    "central_rect",
    "common_area",
    "fwhm_to_sigma",
    "frequency_separate",
    "gaussian_blur",
    "inconsistent_keywords",
    "intersect_rects",
    "median_of_centre",
    "min_median_key",
    "psf_sigma_from_fwhm",
    "recover_wb_factors",
    "sigma_to_fwhm",
    "sigma_to_reach",
    "unscreen_stars",
    "valid_rect",
    "without_inconsistent_keywords",
    "apply_white_balance",
]

#: Loom's ``Util.sigmaToFWHM`` constant (lib/Util.js:1655-1656).
FWHM_PER_SIGMA: float = 2.3548200450309493

#: Edge margin of ``Steps.validRect`` when none is given (lib/Steps.js:148-224).
VALID_RECT_MARGIN: int = 4

#: Below this percentage of the reference frame, the common area is refused (lib/Pipeline.js:2221-2272).
MIN_COMMON_PERCENT: float = 50.0

#: Frequency separation constants (lib/Steps.js:3662-3711).
FS_SIGMA_FACTOR: float = 2.0
FS_PEDESTAL: float = 0.5
FS_SCALE: float = 2.0

#: Loom formats PixelMath offsets with "%.12f" (lib/Steps.js:1264, :1525) before they are applied.
_OFFSET_DECIMALS: int = 12
#: Loom formats white-balance gains with "%.10f" (lib/Steps.js:1240).
_GAIN_DECIMALS: int = 10

#: Pixels outside (0, WB_SATURATION) take no part in the white-balance factor fit.
_WB_SATURATION: float = 0.9
#: The factor fit samples at most about this many pixels per channel (a regular stride keeps it exact on linear data).
_WB_MAX_SAMPLES: int = 4_000_000
#: Iteration cap and clipping width of the factor fit's robust residual clipping.
_WB_MAX_ITERATIONS: int = 10
_WB_CLIP_SIGMA: float = 3.0
#: Residual spread below which the fit is exact and clipping stops (exactly linear data).
_WB_EXACT_SPREAD: float = 1e-12

#: Gaussian blur settings of Loom's Convolution (lib/Steps.js:4038-4065), as scipy names them.
_BLUR_MODE: str = "nearest"
_BLUR_TRUNCATE: float = 4.0


def _as_float_plane(plane: np.ndarray) -> np.ndarray:
    return np.asarray(plane, dtype=np.float64)


def _fixed1(value: float) -> str:
    """``value.toFixed(1)`` (round half up on the exact binary value)."""
    return str(Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _fixed(value: float, decimals: int) -> float:
    """``Number(value.toFixed(decimals))`` as Loom's "%.Nf" formatting reads it back."""
    return float(f"{float(value):.{decimals}f}")


def _js_string(value: Any) -> str:
    """``String(value)`` in JS for the scalar types a FITS header yields, then trimmed as Loom trims names and values."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, np.integer, np.floating)):
        return number_str(float(value))
    return str(value).strip()


def _upper_median(values: Sequence[float]) -> float:
    """Loom's median (lib/Steps.js:3572-3661): sorted, element ``len >> 1``."""
    a = sorted(float(v) for v in values)
    return a[len(a) >> 1]


def valid_rect(plane: np.ndarray, margin: int = VALID_RECT_MARGIN) -> tuple[int, int, int, int]:
    """Rectangle of real data in a plane, with registration fill trimmed (lib/Steps.js:148-224).

    A row (column) counts as data when its median is positive: registration fill is zero across a whole strip,
    while real rows have noise and so a positive median, even where the sky is dark. The scan starts from each
    edge and stops at the first data row or column; ``margin`` is then trimmed from every side.
    """
    p = _as_float_plane(plane)
    if p.ndim != 2 or p.shape[0] == 0 or p.shape[1] == 0:
        raise ValueError("valid_rect: need a non-empty 2-D plane")
    h, w = p.shape
    row_has = (np.median(p, axis=1) > 0).tolist()
    col_has = (np.median(p, axis=0) > 0).tolist()

    y0 = 0
    while y0 < h - 1 and not row_has[y0]:
        y0 += 1
    y1 = h - 1
    while y1 > y0 and not row_has[y1]:
        y1 -= 1
    x0 = 0
    while x0 < w - 1 and not col_has[x0]:
        x0 += 1
    x1 = w - 1
    while x1 > x0 and not col_has[x1]:
        x1 -= 1

    return (min(x0 + margin, w), min(y0 + margin, h), max(x1 + 1 - margin, 0), max(y1 + 1 - margin, 0))


def intersect_rects(rects: Sequence[tuple[int, int, int, int]]) -> Optional[tuple[int, int, int, int]]:
    """Common area of rectangles, or None when there are none or they do not overlap (lib/Util.js:1439-1460)."""
    if not rects:
        return None
    x0, y0, x1, y1 = rects[0]
    for r in rects[1:]:
        x0 = max(x0, r[0])
        y0 = max(y0, r[1])
        x1 = min(x1, r[2])
        y1 = min(y1, r[3])
    if x1 > x0 and y1 > y0:
        return (x0, y0, x1, y1)
    return None


def common_area(
    rects: Sequence[tuple[int, int, int, int]], ref_rect: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """The area every channel covers, refused when it is under half of the reference frame (lib/Pipeline.js:2221-2272).

    ``ref_rect`` is the reference frame's full extent, normally ``(0, 0, width, height)``. Raises ValueError with
    Loom's message when the channels do not overlap or the common area is below ``MIN_COMMON_PERCENT``.
    """
    common = intersect_rects(rects)
    if common is None:
        raise ValueError("The channels have no overlapping imaged area; they cannot be combined.")
    ref_area = (ref_rect[2] - ref_rect[0]) * (ref_rect[3] - ref_rect[1])
    crop_area = (common[2] - common[0]) * (common[3] - common[1])
    # a zero-area reference gives NaN in Loom (no throw): the comparison below is then false
    pct = 100 * crop_area / ref_area if ref_area != 0 else math.nan
    if pct < MIN_COMMON_PERCENT:
        raise ValueError(
            "The common imaged area is only " + _fixed1(pct) + "% of the reference frame. That is almost "
            "certainly a misdetected edge rather than real data; refusing to crop away most of the image."
        )
    return common


def central_rect(w: int, h: int, fraction: float) -> tuple[int, int, int, int]:
    """The centred rectangle covering ``fraction`` of each side (lib/Util.js:414-418)."""
    mx = math.floor(w * (1 - fraction) / 2)
    my = math.floor(h * (1 - fraction) / 2)
    return (mx, my, w - mx, h - my)


def median_of_centre(planes: Sequence[np.ndarray], fraction: float = 0.6) -> float:
    """Median over the central ``fraction`` of the image, pooled over all planes (lib/Steps.js:262-273).

    Loom's ``Image.median()`` with no channel arguments takes every channel, so the planes are pooled.
    """
    if not planes:
        raise ValueError("median_of_centre: no planes")
    h, w = planes[0].shape
    x0, y0, x1, y1 = central_rect(w, h, fraction)
    pooled = np.concatenate([_as_float_plane(p)[y0:y1, x0:x1].ravel() for p in planes])
    return float(np.median(pooled))


def min_median_key(medians: Mapping[str, float]) -> Optional[str]:
    """Key of the lowest value, the first one on a tie; None when empty (lib/Util.js:428-435)."""
    best_key: Optional[str] = None
    best_value = 0.0
    for key, value in medians.items():
        if best_key is None or value < best_value:
            best_key, best_value = key, value
    return best_key


def _background_offset_f64(planes: Sequence[np.ndarray], ref_median: float) -> list[np.ndarray]:
    delta = _fixed(float(ref_median) - median_of_centre(planes, 0.6), _OFFSET_DECIMALS)
    return [_as_float_plane(p) + delta for p in planes]


def background_offset(planes: Sequence[np.ndarray], ref_median: float) -> list[np.ndarray]:
    """Planes shifted so their central median equals ``ref_median`` (lib/Steps.js:1506-1534).

    The offset is formatted to twelve decimals, as Loom writes it into PixelMath, and added as is: it is not
    truncated, so pixels can go below zero. Returns float32 planes.
    """
    return [p.astype(np.float32) for p in _background_offset_f64(planes, ref_median)]


def _white_balance_f64(planes: Sequence[np.ndarray], factors: Sequence[float]) -> list[np.ndarray]:
    if len(factors) != 3 or len(planes) != 3:
        raise ValueError("apply_white_balance: need three factors and three planes")
    gains = [_fixed(f, _GAIN_DECIMALS) for f in factors]
    gained = [_as_float_plane(p) * g for p, g in zip(planes, gains)]
    medians = [float(np.median(g)) for g in gained]
    target = (medians[0] + medians[1] + medians[2]) / 3
    return [g + _fixed(target - m, _OFFSET_DECIMALS) for g, m in zip(gained, medians)]


def apply_white_balance(planes: Sequence[np.ndarray], factors: Sequence[float]) -> list[np.ndarray]:
    """Per-channel gains, then every channel's background brought to the mean of the three (lib/Steps.js:1223-1266).

    The gains are rounded to ten decimals first, as PixelMath receives them ("%.10f"), and the background offsets
    to twelve. The maths is float64; the planes come back float32. Each channel is offset by
    ``mean(medians) - median_c``, with medians taken over the whole gained plane.
    """
    return [p.astype(np.float32) for p in _white_balance_f64(planes, factors)]


def _fit_line(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Least-squares ``y = k*x + b``; raises when ``x`` has no spread."""
    mx = float(np.mean(x))
    my = float(np.mean(y))
    dx = x - mx
    denom = float(np.dot(dx, dx))
    if denom == 0.0:
        raise ValueError("recover_wb_factors: a channel has no spread in its before values")
    k = float(np.dot(dx, y - my)) / denom
    return k, my - k * mx


def _wb_fit_mask(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """Pixels usable for the factor fit: strictly inside (0, 0.9) in both before and after."""
    return (before > 0) & (before < _WB_SATURATION) & (after > 0) & (after < _WB_SATURATION)


def recover_wb_factors(before_planes: Sequence[np.ndarray], after_planes: Sequence[np.ndarray]) -> list[float]:
    """Per-channel white-balance gains from a before and after pair of planes (no Loom counterpart).

    For each channel, fits ``after = k*before + b`` over the pixels with ``0 < before < 0.9`` and
    ``0 < after < 0.9``, then repeatedly drops pixels whose residual lies beyond three standard deviations of
    the kept residuals and refits. Stops when the kept set no longer changes (at most ten rounds). The slopes
    are the factors. Siril's SPCC does not report its factors, so they are recovered from the pixels.
    """
    if len(before_planes) != len(after_planes):
        raise ValueError("recover_wb_factors: the before and after planes differ in number")
    factors: list[float] = []
    for c, (bp, ap) in enumerate(zip(before_planes, after_planes)):
        b = _as_float_plane(bp).ravel()
        a = _as_float_plane(ap).ravel()
        if a.shape != b.shape:
            raise ValueError("recover_wb_factors: channel %d differs in shape" % c)
        if b.size > _WB_MAX_SAMPLES:
            stride = -(-b.size // _WB_MAX_SAMPLES)
            b = b[::stride]
            a = a[::stride]
        mask = _wb_fit_mask(b, a)
        if int(mask.sum()) < 2:
            raise ValueError("recover_wb_factors: fewer than two usable pixels in channel %d" % c)
        keep = mask
        k = 0.0
        for _ in range(_WB_MAX_ITERATIONS):
            k, intercept = _fit_line(b[keep], a[keep])
            resid = a - (k * b + intercept)
            spread = float(np.std(resid[keep]))
            if spread <= _WB_EXACT_SPREAD:
                break
            new_keep = mask & (np.abs(resid) <= _WB_CLIP_SIGMA * spread)
            if int(new_keep.sum()) < 2:
                raise ValueError("recover_wb_factors: clipping left fewer than two pixels in channel %d" % c)
            if np.array_equal(new_keep, keep):
                break
            keep = new_keep
        factors.append(k)
    return factors


def inconsistent_keywords(headers: Sequence[Mapping[str, Any]]) -> list[str]:
    """Keyword names whose value is not the same in every header (lib/Steps.js:2200-2216).

    Names and values are compared as JS strings after trimming (so 1 and 1.0 agree, as "1" in Loom). A keyword
    missing from any header is inconsistent. The result is sorted.
    """
    n = len(headers)
    values: dict[str, list[Optional[str]]] = {}
    for i, header in enumerate(headers):
        for name, value in header.items():
            key = str(name).strip()
            values.setdefault(key, [None] * n)[i] = _js_string(value)
    out: list[str] = []
    for name, v in values.items():
        if any(v[i] is None or v[i] != v[0] for i in range(n)):
            out.append(name)
    return sorted(out)


def without_inconsistent_keywords(header: Mapping[str, Any], headers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``header`` without the keywords that disagree across ``headers`` (lib/Steps.js:2226-2248)."""
    drop = set(inconsistent_keywords(headers))
    return {k: v for k, v in header.items() if str(k).strip() not in drop}


def sigma_to_reach(widest: float, sigma: float) -> float:
    """Blur sigma that takes a PSF of ``sigma`` up to ``widest``; 0 when it is already as wide (lib/Util.js:1646-1652)."""
    t = float(widest)
    c = float(sigma)
    if not (math.isfinite(t) and math.isfinite(c)) or t <= c:
        return 0.0
    return math.sqrt(t * t - c * c)


def sigma_to_fwhm(sigma: float) -> float:
    """FWHM of a Gaussian with the given sigma (lib/Util.js:1655)."""
    return sigma * FWHM_PER_SIGMA


def fwhm_to_sigma(fwhm: float) -> float:
    """Sigma of a Gaussian with the given FWHM (lib/Util.js:1656)."""
    return fwhm / FWHM_PER_SIGMA


def psf_sigma_from_fwhm(fwhmx_list: Sequence[float], fwhmy_list: Sequence[float]) -> Optional[float]:
    """PSF sigma from per-star FWHM measurements: the mean of the two medians, converted to sigma.

    The medians use Loom's definition (the element at ``len >> 1`` after sorting). Returns None when either list
    is empty. The star measurements themselves come from Siril.
    """
    if not fwhmx_list or not fwhmy_list:
        return None
    return ((_upper_median(fwhmx_list) + _upper_median(fwhmy_list)) / 2) / FWHM_PER_SIGMA


def gaussian_blur(planes: Sequence[np.ndarray], sigma: float) -> list[np.ndarray]:
    """Gaussian blur of each plane, ``scipy.ndimage.gaussian_filter`` with ``mode="nearest"`` and ``truncate=4``.

    Loom's Convolution (lib/Steps.js:4038-4065) runs PixInsight's kernel, which is not bit-reproducible here.
    A sigma that is not positive returns copies unchanged.
    """
    if not sigma > 0:
        return [np.array(p, copy=True) for p in planes]
    out: list[np.ndarray] = []
    for p in planes:
        arr = np.asarray(p)
        if not np.issubdtype(arr.dtype, np.floating):
            arr = arr.astype(np.float64)
        out.append(ndimage.gaussian_filter(arr, sigma=sigma, mode=_BLUR_MODE, truncate=_BLUR_TRUNCATE))
    return out


def frequency_separate(plane: np.ndarray, sigma: float) -> tuple[np.ndarray, np.ndarray]:
    """Split a plane into a blurred low layer and the high layer the blur removed (lib/Steps.js:3662-3711).

    ``low`` is the Gaussian blur at ``sigma`` (already scaled: the caller multiplies a measured PSF sigma by
    ``FS_SIGMA_FACTOR``). ``high = clip((x - low)/FS_SCALE + FS_PEDESTAL, 0, 1)``, so that ``low + (high -
    FS_PEDESTAL)*FS_SCALE`` gives back ``x`` wherever the high layer was not clipped. Both are float64.
    """
    x = _as_float_plane(plane)
    low = gaussian_blur([x], sigma)[0].astype(np.float64)
    high = np.clip((x - low) / FS_SCALE + FS_PEDESTAL, 0.0, 1.0)
    return low, high


def unscreen_stars(image: np.ndarray, starless: np.ndarray) -> np.ndarray:
    """Stars layer from an image and its starless version (lib/Steps.js, deriveStarsByUnscreen).

    ``core.unscreen`` already truncates to [0, 1], as the PixelMath it ports does, so no second clip is needed.
    """
    return _unscreen(image, starless)
