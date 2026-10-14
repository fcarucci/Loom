"""TIFF reading (with tifffile) and the 16-bit TIFF writer that replaces Render.writeTiff (Render.js 1326-1350).

Planes are float32 (h, w) arrays in [0, 1], one per channel, as everywhere in this package.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import tifffile

__all__ = ["read_image", "write_tiff16"]

#: The TIFF tag that carries an embedded ICC profile (InterColorProfile).
ICC_TAG = 34675
_PLANAR_SEPARATE = 2  # tifffile's PLANARCONFIG.SEPARATE


def read_image(path: str | os.PathLike[str]) -> tuple[list[np.ndarray], bytes | None]:
    """Read the first page: ``(planes, icc)``.

    8-bit is scaled by 1/255, 16-bit by 1/65535, other integers by 1/(2^bits - 1), float is kept. Grey gives one
    plane, RGB three. An alpha sample is dropped (as Sky.copyWindow does), so RGBA gives three planes. Interleaved
    and planar layouts are both read. ``icc`` is the embedded profile's bytes, or None.
    """
    with tifffile.TiffFile(os.fspath(path)) as tf:
        page = tf.pages[0]
        tag = page.tags.get(ICC_TAG)
        icc = bytes(tag.value) if tag is not None else None
        arr = np.asarray(page.asarray())
        planar = getattr(page, "planarconfig", None) == _PLANAR_SEPARATE
    if arr.ndim == 2:
        arr = arr[np.newaxis]
    elif not planar:
        arr = np.moveaxis(arr, -1, 0)  # interleaved (h, w, c) -> planar (c, h, w)
    n = arr.shape[0]
    if n == 2:  # grey + alpha
        arr = arr[:1]
    elif n > 3:  # RGBA (or more): keep the colour samples
        arr = arr[:3]
    if arr.dtype == np.uint8:
        scale = 255.0
    elif arr.dtype.kind == "u":
        scale = float(2 ** (arr.dtype.itemsize * 8) - 1)
    else:
        scale = None
    planes = []
    for c in range(arr.shape[0]):
        p = arr[c].astype(np.float64) / scale if scale is not None else arr[c]
        planes.append(np.ascontiguousarray(p, dtype=np.float32))
    return planes, icc


def write_tiff16(path: str | os.PathLike[str], planes: Sequence[np.ndarray], icc: bytes | None = None) -> None:
    """Write uncompressed 16-bit TIFF: grey (1 plane) or RGB (3 planes).

    Values are clipped to [0, 1] and scaled by 65535 with rounding (NaN becomes 0). The ICC profile, if given,
    goes in tag 34675. The file is written to ``<path>.part`` and renamed into place, so an interrupted write
    leaves no half-written frame behind.
    """
    n = len(planes)
    if n not in (1, 3):
        raise ValueError("write_tiff16 takes 1 (grey) or 3 (RGB) planes")
    stack = np.stack([np.asarray(p, dtype=np.float64) for p in planes])
    if stack.ndim != 3:
        raise ValueError("write_tiff16: planes must be 2-D and the same shape")
    q = np.floor(np.clip(np.nan_to_num(stack, nan=0.0), 0.0, 1.0) * 65535 + 0.5).astype(np.uint16)
    if n == 1:
        img, photometric = q[0], "minisblack"
    else:
        img, photometric = np.moveaxis(q, 0, -1), "rgb"
    target = Path(os.fspath(path))
    part = target.with_name(target.name + ".part")
    extratags = [(ICC_TAG, "B", len(icc), icc, False)] if icc else None
    try:
        tifffile.imwrite(os.fspath(part), img, photometric=photometric, compression=None, extratags=extratags)
        os.replace(part, target)
    except BaseException:
        if part.exists():
            part.unlink()
        raise
