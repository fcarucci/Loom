"""Frame Selector: frames drawn for display, ported from ``pixinsight/script/FrameSelector.js``.

Covers the stretch that makes a linear frame visible, the thumbnails and full previews built from it, the
reference stretch shared by a whole filter, and the render cache they go through. Each JS function has a Python
twin with the same meaning, named in snake_case (the JS line ranges are FrameSelector.js's own):

    STRETCH                 251       -> STRETCH
    io                      258       -> IO
    readQuietly             267-291   -> read_quietly
    readFrame               303-307   -> read_frame
    mtf                     310-316   -> mtf
    stretchParams           320-326   -> stretch_params
    referenceStretch        336-362   -> reference_stretch
    readStretchMemo         364-374   -> read_stretch_memo
    writeStretchMemo        376-386   -> write_stretch_memo
    stretchInPlace          395-411   -> stretch_in_place
    stretchedImage          419-436   -> stretched_image
    eightBit                439-448   -> eight_bit
    renderParams            451-465   -> render_params
    cachedRender            472-497   -> cached_render
    PREVIEW_HINTS           504       -> PREVIEW_HINTS
    storeRender             511-539   -> store_render
    renderFrame             547-576   -> render_frame
    thumbnailOf             579-582   -> thumbnail_of
    THUMB                   1462      -> THUMB
    closeAll                589-...   not ported: it closes PixInsight image windows

Differences from the JavaScript, all deliberate:

* Pixels are numpy arrays, not PixInsight Image objects. A frame is a float32 array of shape (h, w) or (c, h, w),
  top-down as loomsiril holds planes; a render is a uint8 array of shape (h, w) or (h, w, 3), which the Tk widgets turn
  into PhotoImages. Nothing here opens a window or runs a process.
* Pixels come from loomsiril.io_fits for FITS files (.fit, .fits, .fts) and, for anything else (XISF, .fz), from
  ``siril.load_image_from_file(path, with_pixels=True)``. That call does not change the image open in Siril; its data
  is bottom-up, so the rows are flipped here, and integer samples are scaled to [0, 1].
* Every function that touches the cache takes the PreviewCache explicitly (``Cache.previewDir`` and friends are
  globals in the JavaScript). A render is stored as ``<key>.npy`` (the uint8 array, uncompressed); the XISF writer
  options of PREVIEW_HINTS have no meaning for it and are kept only so every JS name has its twin.
* ``IO["frame_reads"]`` counts the frames read through ``read_frame``, as ``FrameSelector.io.frameReads`` does.
* The median is ``numpy.median`` over all samples of all channels (the mean of the two middle samples for an even
  count). The MAD is the median of the absolute deviations about that median, scaled by 1.4826 as the JavaScript
  scales ``Image.MAD()``.
* Thumbnails are shrunk by exact area averaging: each output pixel is the mean of the input area it covers, with
  fractional weights at its edges, to ``round(size * factor)`` pixels (at least one). The JavaScript calls
  ``Image.resample``, whose interpolation is PixInsight's.
* ``eight_bit`` clips to [0, 1], maps NaN to 0 and rounds half up: ``floor(x * 255 + 0.5)``.
* The stretch is FrameSelector.js's formula kept exactly. The midtone is taken at ``median - shadows`` while the
  transfer divides by ``1 - shadows``, so the median lands on STRETCH.TARGET exactly only when the shadows clip is
  zero (the median is below SHADOWS MAD), and close to it otherwise.
* A file that cannot be read gives None and a log line; nothing here raises on a bad file.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Mapping, Sequence
from typing import Any, Optional

import numpy as np
from numpy.typing import ArrayLike

from loomsiril.frames import to_fixed
from loomsiril.frames_flags import is_number
from loomsiril.frames_preview_cache import (
    PREVIEW_CAP_BYTES,
    STRETCH_MEMO_PREFIX,
    STRETCH_MEMO_SUFFIX,
    PreviewCache,
)
from loomsiril.io_fits import read_fits
from loomsiril.jsmath import js_div, js_max

log = logging.getLogger(__name__)

#: FrameSelector.STRETCH (FrameSelector.js 251): the shadows clip in MADs, and the median's target.
STRETCH: dict[str, float] = {"SHADOWS": 2.8, "TARGET": 0.25}

#: FrameSelector.THUMB (FrameSelector.js 1462): the filmstrip tile's box, in pixels.
THUMB: dict[str, int] = {"W": 120, "H": 80}

#: FrameSelector.io (FrameSelector.js 258): frames read through read_frame, counted.
IO: dict[str, int] = {"frame_reads": 0}

#: The extension of a cached render: the uint8 array, saved with numpy.
PREVIEW_EXT = ".npy"

#: FrameSelector.PREVIEW_HINTS (FrameSelector.js 504): the options of the XISF writer the JavaScript stores renders with.
PREVIEW_HINTS = "verbosity 0"

FITS_SUFFIXES = (".fit", ".fits", ".fts")

#: Samples per band when a frame is stretched, as stretchInPlace's band of 1048576 samples (FrameSelector.js 395-411).
_BAND_SAMPLES = 1048576


def _is_fits(path: str) -> bool:
    return os.path.splitext(str(path))[1].lower() in FITS_SUFFIXES


def _read_with_siril(path: str, siril: Any) -> Optional[np.ndarray]:
    """A non-FITS file's pixels through sirilpy, top-down, as float32 in [0, 1]. None when Siril cannot read it."""
    if siril is None:
        return None
    ff = siril.load_image_from_file(str(path), with_pixels=True)
    if ff is None or getattr(ff, "data", None) is None:
        return None
    data = np.asarray(ff.data)
    if data.ndim not in (2, 3):
        return None
    data = data[..., ::-1, :]  # bottom-up, as sirilpy returns pixel data -> top-down
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float64) / float(np.iinfo(data.dtype).max)
    return np.ascontiguousarray(data, dtype=np.float32)


def read_quietly(path: str, siril: Any = None) -> Optional[np.ndarray]:
    """``FrameSelector.readQuietly`` (FrameSelector.js 267-291): the first image of a file, without opening a window.

    A FITS file is read by loomsiril.io_fits: (h, w) for one plane, (c, h, w) for several. Any other file goes through
    ``siril.load_image_from_file`` (see the module docstring). None when the file cannot be read; never raises.
    """
    try:
        if _is_fits(path):
            planes, _header = read_fits(path)
            if not planes:
                return None
            return planes[0] if len(planes) == 1 else np.stack(planes)
        return _read_with_siril(path, siril)
    except Exception as e:  # an unreadable file is a missing frame, not an error
        log.warning("could not read %s: %s", path, e)
        return None


def read_frame(path: str, siril: Any = None) -> Optional[np.ndarray]:
    """``FrameSelector.readFrame`` (FrameSelector.js 303-307): read_quietly, counted in IO["frame_reads"]."""
    IO["frame_reads"] += 1
    return read_quietly(path, siril)


def mtf(m: float, x: ArrayLike) -> Any:
    """``FrameSelector.mtf`` (FrameSelector.js 310-316): the midtones transfer function, scalar or vectorised.

    0 at or below 0, 1 at or above 1, else (m - 1) x / ((2m - 1) x - m).
    """
    if np.ndim(x) == 0:
        xf = float(x)
        if xf <= 0:
            return 0.0
        if xf >= 1:
            return 1.0
        return js_div((m - 1) * xf, (2 * m - 1) * xf - m)
    xa = np.asarray(x, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        y = (m - 1) * xa / ((2 * m - 1) * xa - m)
    return np.where(xa <= 0, 0.0, np.where(xa >= 1, 1.0, y))


def stretch_params(img: ArrayLike) -> dict[str, float]:
    """``FrameSelector.stretchParams`` (FrameSelector.js 320-326): the shadows clip and midtone for a frame's own median
    and MAD, over all samples of all channels."""
    S = STRETCH
    x = np.asarray(img).ravel()
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    shadows = js_max(0.0, med - S["SHADOWS"] * mad)
    return {"shadows": shadows, "midtone": float(mtf(S["TARGET"], js_max(1e-8, med - shadows)))}


def read_stretch_memo(path: Optional[str]) -> Optional[dict[str, float]]:
    """``FrameSelector.readStretchMemo`` (FrameSelector.js 364-374): the stretch remembered at ``path``, or None.

    A memo that is missing, unreadable, or holds anything but two finite numbers is no memo.
    """
    try:
        if path is None or not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            p = json.loads(f.read(), parse_constant=_no_constant)
        if isinstance(p, dict) and is_number(p.get("shadows")) and is_number(p.get("midtone")):
            return p
        return None
    except Exception:
        return None


def _no_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON token {token}")


def write_stretch_memo(path: Optional[str], params: Mapping[str, float]) -> None:
    """``FrameSelector.writeStretchMemo`` (FrameSelector.js 376-386): remember a stretch beside the previews.

    Only a cache: a failure is ignored and the stretch is measured again next time.
    """
    try:
        if path is None:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"shadows": params["shadows"], "midtone": params["midtone"]}))
    except Exception:
        pass


def reference_stretch(rows: Sequence[Mapping[str, Any]], cache: PreviewCache, siril: Any = None) -> Optional[dict[str, float]]:
    """``FrameSelector.referenceStretch`` (FrameSelector.js 336-362): the stretch of the frame with the median background.

    ``rows`` are dicts with ``path`` and ``metrics`` (whose ``background`` may be missing). The chosen frame is the
    one at the lower median of the rows that have a background. Its stretch is remembered in a memo beside the
    previews, so a reopened review reads no frame for it. None when the reference cannot be read.
    """
    with_bg = [
        r for r in rows
        if r.get("metrics") is not None and is_number((r.get("metrics") or {}).get("background"))
    ]
    if not with_bg:
        return None
    with_bg.sort(key=lambda r: r["metrics"]["background"])
    ref = with_bg[(len(with_bg) - 1) // 2]
    if not os.path.exists(ref["path"]):
        return None
    key = cache.preview_key(ref["path"], "stretch", render_params("preview"))
    memo = os.path.join(cache.preview_dir(), STRETCH_MEMO_PREFIX + key + STRETCH_MEMO_SUFFIX) if key is not None else None
    kept = read_stretch_memo(memo)
    if kept is not None:
        return kept
    img = read_frame(ref["path"], siril)
    if img is None:
        return None
    params = stretch_params(img)
    write_stretch_memo(memo, params)
    return params


def stretch_in_place(img: np.ndarray, params: Optional[Mapping[str, float]] = None) -> np.ndarray:
    """``FrameSelector.stretchInPlace`` (FrameSelector.js 395-411): stretch ``img`` in place and return it.

    Each sample becomes mtf(midtone, (x - shadows) / (1 - shadows)); without ``params`` they are measured on ``img``
    first. Done band by band over each channel, as the JavaScript does.
    """
    P = params if params is not None else stretch_params(img)
    shadows, midtone = P["shadows"], P["midtone"]
    span = js_max(1e-8, 1 - shadows)
    h, w = img.shape[-2], img.shape[-1]
    band = max(1, _BAND_SAMPLES // max(1, w))
    for c in np.ndindex(img.shape[:-2]):
        plane = img[c]
        for y0 in range(0, h, band):
            rows = plane[y0:y0 + band]
            rows[...] = mtf(midtone, (rows - shadows) / span)
    return img


def _area_weights(n_in: int, n_out: int) -> np.ndarray:
    """The (n_out, n_in) matrix of area averages: row j is the input interval [j s, (j+1) s) with s = n_in / n_out,
    each input sample weighted by its overlap and the row normalised to 1."""
    W = np.zeros((n_out, n_in), dtype=np.float64)
    s = n_in / n_out
    for j in range(n_out):
        a, b = j * s, (j + 1) * s
        for i in range(int(math.floor(a)), min(n_in, int(math.ceil(b)))):
            overlap = min(b, i + 1) - max(a, i)
            if overlap > 0:
                W[j, i] = overlap
        W[j] /= W[j].sum()
    return W.astype(np.float32)


def resample_area(img: np.ndarray, factor: float) -> np.ndarray:
    """``img`` shrunk (or grown) by ``factor`` on both axes by exact area averaging, to ``round(size * factor)`` pixels
    (at least one), the form of the JavaScript's ``Image.resample(factor)`` used for thumbnails."""
    h, w = img.shape[-2], img.shape[-1]
    nh = max(1, int(math.floor(h * factor + 0.5)))
    nw = max(1, int(math.floor(w * factor + 0.5)))
    if nh == h and nw == w:
        return img
    rows = _area_weights(h, nh)
    cols = _area_weights(w, nw)
    out = np.matmul(rows, img.astype(np.float32, copy=False))
    return np.matmul(out, cols.T)


def stretched_image(path: str, fit: Optional[Mapping[str, int]], params: Optional[Mapping[str, float]],
                    siril: Any = None) -> Optional[np.ndarray]:
    """``FrameSelector.stretchedImage`` (FrameSelector.js 419-436): a frame stretched for display, or None when it
    cannot be read. ``fit``, a ``{"W", "H"}`` box, shrinks the frame into the box first with its aspect kept; None
    keeps full size. The stretch is measured after the shrink, as the JavaScript does when ``params`` is None."""
    img = read_frame(path, siril)
    if img is None:
        return None
    if fit is not None:
        h, w = img.shape[-2], img.shape[-1]
        img = resample_area(img, min(fit["W"] / w, fit["H"] / h))
    return stretch_in_place(img, params)


def eight_bit(img: np.ndarray) -> np.ndarray:
    """``FrameSelector.eightBit`` (FrameSelector.js 439-448): an 8-bit copy of an image in [0, 1].

    (c, h, w) with three channels becomes (h, w, 3) RGB; one channel becomes (h, w). Each sample is clipped to [0, 1]
    and rounded half up: ``floor(x * 255 + 0.5)``; NaN becomes 0.
    """
    x = np.nan_to_num(np.asarray(img, dtype=np.float32), nan=0.0)
    x = np.clip(x, 0.0, 1.0)
    u8 = np.floor(x * 255.0 + 0.5).astype(np.uint8)
    if u8.ndim == 2:
        return u8
    if u8.shape[0] == 1:
        return u8[0]
    if u8.shape[0] == 3:
        return np.ascontiguousarray(np.transpose(u8, (1, 2, 0)))
    raise ValueError(f"a render needs 1 or 3 channels, not {u8.shape[0]}")


def render_params(kind: str, stretch: Optional[Mapping[str, float]] = None) -> dict[str, Any]:
    """``FrameSelector.renderParams`` (FrameSelector.js 451-465): everything that changes a render of ``kind``, for its
    cache key. Keys are in the JavaScript's order."""
    p: dict[str, Any] = {"shadows": STRETCH["SHADOWS"], "target": STRETCH["TARGET"]}
    if stretch is not None:
        p["clip"] = to_fixed(stretch["shadows"], 7)
        p["mid"] = to_fixed(stretch["midtone"], 7)
    if kind == "thumb":
        p["w"] = THUMB["W"]
        p["h"] = THUMB["H"]
    return p


def cached_render(key: str, cache: PreviewCache) -> Optional[np.ndarray]:
    """``FrameSelector.cachedRender`` (FrameSelector.js 472-497): a cached render as a uint8 array, or None when there is
    none. An entry that will not read is removed, so it is rendered afresh rather than failing on every view."""
    path = cache.preview_path(key, PREVIEW_EXT)
    if not os.path.exists(path):
        return None
    try:
        img = np.load(path, allow_pickle=False)
        if img.dtype != np.uint8 or img.ndim not in (2, 3):
            raise ValueError("it holds no image")
        cache.note_preview_use(key)
        return img
    except Exception as e:
        log.warning("a cached preview could not be read, rendering again: %s", e)
        try:
            os.remove(path)
        except OSError:
            pass
        return None


def store_render(key: str, img: np.ndarray, cache: PreviewCache) -> None:
    """``FrameSelector.storeRender`` (FrameSelector.js 511-539): write a render to the cache, through a temporary name so
    a half-written file is never an entry, then trim the folder to its cap. A render that cannot be cached is still
    shown: the failure is logged and nothing is raised."""
    path = cache.preview_path(key, PREVIEW_EXT)
    part = path + ".part"
    try:
        os.makedirs(cache.preview_dir(), exist_ok=True)
        with open(part, "wb") as f:
            np.save(f, np.ascontiguousarray(img), allow_pickle=False)
        os.replace(part, path)
        cache.note_preview_use(key)
        cache.trim_previews(PREVIEW_CAP_BYTES, key)
    except Exception as e:
        log.warning("could not cache a preview: %s", e)
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError:
            pass


def render_frame(path: str, kind: str, stretch: Optional[Mapping[str, float]], cache: PreviewCache,
                 siril: Any = None) -> Optional[np.ndarray]:
    """``FrameSelector.renderFrame`` (FrameSelector.js 547-576): a frame drawn for display, from the cache when it is
    there, else read and stretched (and then cached). ``kind`` is "preview", full size, or "thumb", fitted to a
    filmstrip tile. The first view and every later one are the same picture: the render is stored as it is shown.
    None when the frame cannot be read."""
    try:
        key = cache.preview_key(path, kind, render_params(kind, stretch))
        hit = cached_render(key, cache) if key is not None else None
        if hit is not None:
            return hit
        full = stretched_image(path, THUMB if kind == "thumb" else None, stretch, siril)
        if full is None:
            return None
        out = eight_bit(full)
        if key is not None:
            store_render(key, out, cache)
        return out
    except Exception as e:
        log.warning("no %s for %s: %s", kind, path, e)
        return None


def thumbnail_of(path: str, stretch: Optional[Mapping[str, float]], cache: PreviewCache,
                 siril: Any = None) -> Optional[np.ndarray]:
    """``FrameSelector.thumbnailOf`` (FrameSelector.js 579-582): one frame's thumbnail, or None when it cannot be read."""
    return render_frame(path, "thumb", stretch, cache, siril) if os.path.exists(path) else None
