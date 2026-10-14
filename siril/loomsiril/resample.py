"""Resampling kernels for the frame renderer (E4).

Ported from Loom ``script/lib/Render.js`` (v0.4.1): the separable resample of the
backdrop and the stars layers (``Render.axisWeights`` .. ``Render.resampleRows``),
the bilinear patch sample and the mip pyramid of the sprites, the box blur, the area
fit of a plane, and the area shrink of a patch (``Render.shrinkAxis`` ..
``Render.scaledScene``'s patch cut).

Storage rule: the JS keeps its weight and pixel arrays in Float32Array and does its
arithmetic in double. Each stored result here is rounded to float32 at the same
point (the weights in :func:`axis_weights`, the passes of :func:`resample`), so the
numbers agree with the JS to float32 rounding.

Vectorisation: the JS loops over output pixels and taps. Here each axis is one sparse
map (``start``/``idx``/``w``) applied with a loop over the tap slot (at most a handful
per output pixel): the same taps and weights as the JS, summed in the same order, and
about 1000 times cheaper than a dense (out_n, src_n) product for a 1080p frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional, Sequence

import numpy as np

from loomsiril.core import js_round

__all__ = [
    "AxisView",
    "AxisWeights",
    "ShrinkAxis",
    "Taps",
    "area_mean",
    "axis_weights",
    "box_blur",
    "box_radius",
    "cubic_weights",
    "fit_plane",
    "halve",
    "kernel_taps",
    "mip_level",
    "mip_of",
    "normalise_weights",
    "patch_sample",
    "resample",
    "resample_rows",
    "shrink_axis",
    "shrink_patch",
    "tent_taps",
]

#: ``Math.LN2``.
LN2: float = 0.6931471805599453


class Taps(NamedTuple):
    """``{i, w}``: the source indices of one sample and their weights (JS ``Render.kernelTaps``)."""

    i: list[int]
    w: list[float]


class _Taps(NamedTuple):
    """The sparse taps of one axis, the three arrays always used together: output pixel u reads
    ``idx[start[u]:start[u+1]]`` with the weights ``w`` there, so the output size is ``len(start) - 1``."""

    start: np.ndarray
    idx: np.ndarray
    w: np.ndarray


@dataclass(frozen=True)
class AxisWeights:
    """``Render.axisWeights`` result: output pixel u uses taps ``start[u] .. start[u+1]-1``.

    ``idx`` (int32) are source indices already clamped to the image; ``w`` (float64,
    holding float32 values as the JS Float32Array does) the weights. JS ``outN`` is ``out_n``.
    """

    out_n: int
    start: np.ndarray
    idx: np.ndarray
    w: np.ndarray


@dataclass(frozen=True)
class ShrinkAxis:
    """``Render.shrinkAxis`` result: the new pixels ``j0 .. j0 + m - 1`` and their taps (old pixels relative to i0)."""

    j0: int
    m: int
    start: np.ndarray
    idx: np.ndarray
    w: np.ndarray


def _f32(a: Any) -> np.ndarray:
    """Round to float32 and back to float64: what a JS Float32Array keeps of a double."""
    return np.asarray(a, dtype=np.float64).astype(np.float32).astype(np.float64)


def _empty_axis(out_n: int) -> AxisWeights:
    return AxisWeights(out_n, np.zeros(out_n + 1, dtype=np.int32), np.zeros(0, dtype=np.int32),
                       np.zeros(0, dtype=np.float64))


def cubic_weights(f: Any) -> np.ndarray:
    """``Render.cubicWeights``: Catmull-Rom weights for fraction f (last axis of size 4).

    Interpolating, so exact at whole pixels.
    """
    f = np.asarray(f, dtype=np.float64)
    f2 = f * f
    f3 = f2 * f
    return np.stack([0.5 * (-f3 + 2 * f2 - f), 0.5 * (3 * f3 - 5 * f2 + 2),
                     0.5 * (-3 * f3 + 4 * f2 + f), 0.5 * (f3 - f2)], axis=-1)


def kernel_taps(b: float, kernel: str) -> Taps:
    """``Render.kernelTaps``: Catmull-Rom (or bilinear for drafts) taps around b."""
    i0 = math.floor(b)
    f = b - i0
    ws = [0.0, 1 - f, f, 0.0] if kernel == "bilinear" else [float(v) for v in cubic_weights(f)]
    return Taps([i0 - 1, i0, i0 + 1, i0 + 2], ws)


def tent_taps(b: float, m: float) -> Taps:
    """``Render.tentTaps``: a normalised tent of half-width m centred on b (an area filter for minification)."""
    i: list[int] = []
    w: list[float] = []
    total = 0.0
    for x in range(math.ceil(b - m), math.floor(b + m) + 1):
        t = 1 - abs(x - b) / m
        if t <= 0:
            continue
        i.append(x)
        w.append(t)
        total += t
    return Taps(i, [v / total for v in w])


@dataclass(frozen=True)
class AxisView:
    """One axis of a frame's zoom: the camera crop (``crop_start``, ``crop_len``, image pixels), the target
    pixel ``tp``, the backdrop zoom ``K`` and the source size ``src_n``: the JS ``axisWeights`` arguments after
    ``outN`` and ``kernel``, as one object (the frame and the stars layer share it)."""

    crop_start: float
    crop_len: float
    tp: float
    K: float
    src_n: int


def axis_weights(out_n: int, view: AxisView, kernel: str) -> AxisWeights:
    """``Render.axisWeights``: the sparse map of one axis of a uniform zoom K about tp.

    Output u covers camera pixel q = cropStart + (u + 0.5) step - 0.5 (step = cropLen/outN),
    which samples the source at b = tp + (q - tp)/K. When one output pixel spans more than
    one source pixel (m = step/K > 1, every real preset) the kernel is a normalised tent as
    wide as the step: a fixed 4-tap kernel there point-samples, and a star's light swung
    between 77% and 100% as the camera moved (it read as twinkling). At 1:1 and when zooming
    in, the kernel is Catmull-Rom (or bilinear for drafts), exact at whole pixels, so frame 0
    stays the image.
    """
    out_n = int(out_n)
    if out_n <= 0:
        return _empty_axis(0)
    step = view.crop_len / out_n
    m = step / view.K
    b = _sample_positions(out_n, view, step)
    if m > 1.0001:
        counts, iv, wv = _tent_taps_all(b, m)
    else:
        counts, iv, wv = _kernel_taps_all(b, kernel)
    return _pack_axis(counts, iv, wv, view.src_n)


def _sample_positions(out_n: int, view: AxisView, step: float) -> np.ndarray:
    """The source position b of every output pixel; a position within 1e-9 of a whole pixel is that pixel (frame 0 is exact)."""
    u = np.arange(out_n, dtype=np.float64)
    q = view.crop_start + (u + 0.5) * step - 0.5
    b = view.tp + (q - view.tp) / view.K
    whole = np.floor(b + 0.5)                          # Math.round
    return np.where(np.abs(b - whole) < 1e-9, whole, b)


def _tent_taps_all(b: np.ndarray, m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The normalised tent taps (half-width m) of every position b: (taps per pixel, source indices, weights)."""
    lo = np.ceil(b - m)
    hi = np.floor(b + m)
    n_max = int(np.max(hi - lo)) + 1
    xs = lo[:, None] + np.arange(n_max, dtype=np.float64)[None, :]
    t = 1 - np.abs(xs - b[:, None]) / m
    valid = (xs <= hi[:, None]) & (t > 0)
    counts = valid.sum(axis=1)
    sums = np.where(valid, t, 0.0).sum(axis=1)
    return counts, xs[valid], t[valid] / np.repeat(sums, counts)


def _kernel_taps_all(b: np.ndarray, kernel: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The 4-tap Catmull-Rom (or bilinear) taps of every position b: (taps per pixel, source indices, weights)."""
    i0 = np.floor(b)
    f = b - i0
    taps = i0[:, None] + np.array([-1.0, 0.0, 1.0, 2.0])[None, :]
    if kernel == "bilinear":
        zero = np.zeros_like(f)
        cw = np.stack([zero, 1 - f, f, zero], axis=1)
    else:
        cw = cubic_weights(f)
    return np.full(b.size, 4, dtype=np.int64), taps.reshape(-1), cw.reshape(-1)


def _pack_axis(counts: np.ndarray, iv: np.ndarray, wv: np.ndarray, src_n: int) -> AxisWeights:
    """The CSR-style map: pixel u's taps are ``start[u] .. start[u+1]-1``; indices clamped to the source, weights float32-rounded.

    ``counts`` holds one tap count per output pixel, so the output size is ``counts.size``.
    """
    out_n = int(counts.size)
    start = np.zeros(out_n + 1, dtype=np.int32)
    start[1:] = np.cumsum(counts)
    idx = np.clip(iv, 0, src_n - 1).astype(np.int32)
    return AxisWeights(out_n, start, idx, _f32(wv))


def _apply_taps(plane: np.ndarray, taps: _Taps | AxisWeights | ShrinkAxis, axis: int) -> np.ndarray:
    """Sparse separable pass in float64: out[u] = sum_k w[k] plane[idx[k]] over u's taps.

    ``taps`` is any object with the ``start``, ``idx`` and ``w`` arrays (``_Taps``, ``AxisWeights``,
    ``ShrinkAxis``). ``axis=1`` maps the columns of a (rows, src) plane to out_n columns; ``axis=0`` maps
    rows. Taps are summed in the JS order (slot by slot, u-major).
    """
    start, idx, w = taps.start, taps.idx, taps.w
    out_n = len(start) - 1
    plane = np.asarray(plane, dtype=np.float64)
    if axis == 1:
        out = np.zeros((plane.shape[0], out_n), dtype=np.float64)
    else:
        out = np.zeros((out_n, plane.shape[1]), dtype=np.float64)
    if out_n == 0 or idx.size == 0:
        return out
    counts = np.diff(start)
    kmax = int(counts.max())
    for j in range(kmax):
        has = counts > j
        pos = np.minimum(start[:-1] + j, idx.size - 1)
        src = idx[pos]
        wj = np.where(has, w[pos], 0.0)
        if axis == 1:
            out += plane[:, src] * wj[None, :]
        else:
            out += plane[src, :] * wj[:, None]
    return out


def resample_rows(buf: np.ndarray, w: int, ax: AxisWeights, rmin: int, rows: int) -> np.ndarray:
    """``Render.resampleRows``: source rows ``rmin .. rmin + rows - 1`` resampled through ax; (rows, ax.out_n) float32."""
    part = np.asarray(buf)[rmin:rmin + rows, :w]
    return _apply_taps(part, ax, axis=1).astype(np.float32)


def resample(buf: np.ndarray, w: int, ax: AxisWeights, ay: AxisWeights) -> np.ndarray:
    """``Render.resample``: separable resample of a (h, w) channel, rows first, then columns.

    Returns a float32 array of shape (ay.out_n, ax.out_n). Source rows no output row needs
    are skipped, as in the JS.
    """
    out_w, out_h = ax.out_n, ay.out_n
    if ay.idx.size == 0:
        return np.zeros((out_h, out_w), dtype=np.float32)
    rmin = int(ay.idx.min())
    rmax = int(ay.idx.max())
    tmp = resample_rows(buf, w, ax, rmin, rmax - rmin + 1)
    local = (ay.idx - rmin).astype(np.int32)
    return _apply_taps(tmp, _Taps(ay.start, local, ay.w), axis=0).astype(np.float32)


def patch_sample(patch: np.ndarray, rw: int, rh: int, x: Any, y: Any) -> Any:
    """``Render.patchSample``: bilinear sample of a rw x rh patch, zero outside.

    x and y may be numpy arrays (broadcast together); a float comes back for scalars.
    """
    p = np.asarray(patch, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = x - x0
    fy = y - y0
    s = np.zeros(np.broadcast(x, y).shape, dtype=np.float64)
    for j in (0, 1):
        for i in (0, 1):
            xx = x0 + i
            yy = y0 + j
            ok = (xx >= 0) & (yy >= 0) & (xx < rw) & (yy < rh)
            xi = np.clip(xx, 0, max(rw - 1, 0)).astype(np.int64)
            yi = np.clip(yy, 0, max(rh - 1, 0)).astype(np.int64)
            wgt = (fx if i else 1 - fx) * (fy if j else 1 - fy)
            s = s + np.where(ok, wgt * p[yi, xi], 0.0)
    return float(s) if s.ndim == 0 else s


def mip_level(fx: float, g: float, coarse: bool) -> int:
    """``Render.mipLevel``: the level to draw from when a sprite pixel (grown by g) is 1/fx of an output pixel.

    One halving short of a level pixel filling the output pixel, so it is still averaged from
    2-3 samples a side (the coarsest level put a sharp core on a single sample: 27% off at
    the peak). Coarse (Medium quality): a level pixel fills the output pixel.
    """
    r = fx / g
    if coarse:
        return int(math.floor(math.log(r) / LN2)) if r >= 2 else 0
    return int(math.floor(math.log(r) / LN2)) - 1 if r >= 4 else 0


def halve(p: np.ndarray) -> np.ndarray:
    """``Render.halve``: each 2 x 2 block's mean, zero beyond the edge; shape (ceil(h/2), ceil(w/2)) float32."""
    p = np.asarray(p, dtype=np.float64)
    h, w = p.shape
    h2 = -(-h // 2)
    w2 = -(-w // 2)
    padded = np.zeros((2 * h2, 2 * w2), dtype=np.float64)
    padded[:h, :w] = p
    blocks = padded.reshape(h2, 2, w2, 2)
    total = blocks[:, 0, :, 0] + blocks[:, 0, :, 1] + blocks[:, 1, :, 0] + blocks[:, 1, :, 1]
    return (total / 4).astype(np.float32)


def mip_of(patch: np.ndarray, L: int, cache: list) -> np.ndarray:
    """``Render.mipOf``: level L of a patch, halved L times (level 0 is the patch itself).

    numpy arrays cannot carry ``_mips``, so the caller passes a list that holds the levels
    (empty at first) and keeps it with the patch.
    """
    if not cache:
        cache.append(np.asarray(patch))
    while len(cache) <= L:
        cache.append(halve(cache[-1]))
    return cache[L]


def box_radius(s: float) -> int:
    """``Render.boxRadius``: the box radius whose three passes blur like a Gaussian of sigma s (sigma^2 = r(r + 1))."""
    return max(1, int(js_round(math.sqrt(s * s + 0.25) - 0.5)))


def _box_pass(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """One box pass of radius r along an axis, edges held (the edge value repeats outside)."""
    arr = np.moveaxis(np.asarray(a, dtype=np.float64), axis, -1)
    n = arr.shape[-1]
    span = 2 * r + 1
    padded = np.concatenate([np.repeat(arr[..., :1], r, axis=-1), arr,
                             np.repeat(arr[..., -1:], r, axis=-1)], axis=-1)
    csum = np.concatenate([np.zeros(arr.shape[:-1] + (1,)), np.cumsum(padded, axis=-1)], axis=-1)
    out = (csum[..., span:span + n] - csum[..., 0:n]) / span
    return np.moveaxis(out, -1, axis)


def box_blur(a: np.ndarray, w: int, h: int, r: float) -> np.ndarray:
    """``Render.boxBlur``: three box passes a side (~Gaussian of box radius r), edges hold their value; in place.

    Each pass is a cumulative-sum box over the rows, then the columns, rounded to float32 as
    the JS Float32Array stores it. A radius below 1 changes nothing.
    """
    if not (r >= 1):
        return a
    rr = int(r)
    for _ in range(3):
        a[...] = _box_pass(a, rr, axis=1).astype(np.float32)
        a[...] = _box_pass(a, rr, axis=0).astype(np.float32)
    return a


def area_mean(src: np.ndarray, sw: int, sh: int, x0: float, y0: float, sx: float, sy: float) -> float:
    """``Render.areaMean``: the mean of a plane over x0..x0 + sx, y0..y0 + sy, each pixel weighted by its coverage."""
    x1 = x0 + sx
    y1 = y0 + sy
    total = 0.0
    area = 0.0
    for yy in range(math.floor(y0), min(sh, math.ceil(y1))):
        fy = min(y1, yy + 1) - max(y0, yy)
        for xx in range(math.floor(x0), min(sw, math.ceil(x1))):
            f = fy * (min(x1, xx + 1) - max(x0, xx))
            total += f * float(src[yy, xx])
            area += f
    return total / area if area > 0 else 0.0


def _area_axis(out_n: int, src_n: int, step: float) -> _Taps:
    """Normalised coverage taps of ``areaMean`` along one axis: output j covers [j step, j step + step)."""
    start = [0]
    idx: list[int] = []
    wts: list[float] = []
    for j in range(out_n):
        lo = j * step
        hi = lo + step
        cov = []
        for i in range(math.floor(lo), min(src_n, math.ceil(hi))):
            cov.append((i, min(hi, i + 1) - max(lo, i)))
        total = sum(f for _, f in cov)
        if total > 0:
            for i, f in cov:
                idx.append(i)
                wts.append(f / total)
        start.append(len(idx))
    return _Taps(np.asarray(start, dtype=np.int32), np.asarray(idx, dtype=np.int32),
                 np.asarray(wts, dtype=np.float64))


def fit_plane(src: np.ndarray, sw: int, sh: int, w: int, h: int) -> np.ndarray:
    """``Render.fitPlane``: a plane resized to w x h; bilinear when enlarging, area means when shrinking.

    Returns float32 (h, w).
    """
    src = np.asarray(src, dtype=np.float64)
    sx = sw / w
    sy = sh / h
    if sx <= 1 and sy <= 1:
        u = (np.arange(w, dtype=np.float64) + 0.5) * sx - 0.5
        v = (np.arange(h, dtype=np.float64) + 0.5) * sy - 0.5
        out = patch_sample(src, sw, sh, u[None, :], v[:, None])
        return np.broadcast_to(out, (h, w)).astype(np.float32)
    tmp = _apply_taps(src, _area_axis(w, sw, sx), axis=1)
    return _apply_taps(tmp, _area_axis(h, sh, sy), axis=0).astype(np.float32)


def normalise_weights(w: Any, frm: int, to: int) -> None:
    """``Render.normaliseWeights``: w[frm..to) scaled in place to sum to 1 (left as they are when they sum to 0)."""
    total = 0.0
    for k in range(frm, to):
        total += w[k]
    if total > 0:
        for k in range(frm, to):
            w[k] = w[k] / total


def shrink_axis(i0: int, n: int, d: float, N: int) -> ShrinkAxis:
    """``Render.shrinkAxis``: one axis of an area-average shrink by d (< 1).

    Old pixel i covers [i, i + 1), new pixel j covers [j/d, (j + 1)/d), so a centre x maps to
    (x + 0.5) d - 0.5 and a patch shrinks onto the same grid as the whole image. For the n old
    pixels from i0 the new ones j0 .. j0 + m - 1 are listed with their old pixels (relative to
    i0) and their overlap x d. A new pixel reaching past the image (N old pixels: its last row
    or column) is the mean of what it covers; one cut by a patch's edge keeps its share.
    """
    j0 = math.floor(i0 * d)
    j1 = math.ceil((i0 + n) * d)
    start = [0]
    idx: list[int] = []
    w: list[float] = []
    for j in range(j0, j1):
        a = j / d
        b = (j + 1) / d
        for i in range(max(i0, math.floor(a)), min(i0 + n, math.ceil(b))):
            ov = min(b, i + 1) - max(a, i)
            if ov > 0:
                idx.append(i - i0)
                w.append(ov * d)
        if N > 0 and (j + 1) / d > N:
            normalise_weights(w, start[-1], len(idx))
        start.append(len(idx))
    return ShrinkAxis(j0, j1 - j0, np.asarray(start, dtype=np.int32), np.asarray(idx, dtype=np.int32),
                      np.asarray(w, dtype=np.float64))


def shrink_patch(buf: np.ndarray, x0: int, y0: int, rw: int, rh: int, d: float, w: int, h: int) -> dict[str, Any]:
    """``Render.shrinkPatch``: a rw x rh patch (top-left (x0, y0) of a w x h image) area-averaged by d.

    Returns ``{buf, x0, y0, w, h}`` with buf float32 (h, w) of the shrunk patch.
    """
    ax = shrink_axis(x0, rw, d, w)
    ay = shrink_axis(y0, rh, d, h)
    tmp = _apply_taps(buf, ax, axis=1).astype(np.float32)
    out = _apply_taps(tmp, ay, axis=0).astype(np.float32)
    return {"buf": out, "x0": ax.j0, "y0": ay.j0, "w": ax.m, "h": ay.m}
