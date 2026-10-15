"""The pixel grid an Image file must fit: the open image's (the plate solution and the star list come from it).

An Image file may be the same field at another pixel size (a TIFF exported smaller than the 11799 x 7211 image open in
Siril, say): when its aspect ratio equals the open image's (within :data:`ASPECT_TOLERANCE`) it is resampled onto the
open image's grid before the stars are extracted. Anything else (another shape, another channel count) is refused,
with a message that says what to do. Nothing here imports tkinter; the header readers touch the disk, so the dialog
runs them on its worker thread.

Resampling uses the port's own kernels (:mod:`loomsiril.resample`): Catmull-Rom when enlarging, a normalised tent (an
area filter) when shrinking, with pixel centres aligned (output pixel u reads source position (u + 0.5) / scale - 0.5).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

import numpy as np

from loomsiril import resample

__all__ = ["ASPECT_TOLERANCE", "Grid", "file_grid", "fit_planes", "grid_line", "verdict"]

#: (width, height, channels).
Grid = tuple[int, int, int]

#: How far the file's width / height may be from the open image's, relative (0.5 %).
ASPECT_TOLERANCE = 0.005

_HEADER_LIMIT = 1 << 20          # a FITS header is a few blocks: never read more than this looking for END
_CHUNK = 2880 * 4
_ROWS = 512                      # output rows resampled at a time (bounds the memory of a big enlargement)


def _kind(channels: int) -> str:
    return {1: "mono (1 channel)", 3: "colour (3 channels)"}.get(channels, f"{channels}-channel")


def verdict(file: Grid, opened: Grid) -> tuple[bool, str]:
    """``(usable, text)`` for an image file of grid ``file`` against the open image's ``opened``.

    ``(True, "")``: the same grid. ``(True, line)``: the same field at another size, resampled (``line`` is the grey
    note). ``(False, reason)``: refused, the reason says what to do."""
    fw, fh, fc = file
    ow, oh, oc = opened
    size = f"{fw} x {fh} vs {ow} x {oh}"
    if min(fw, fh, ow, oh) <= 0:
        return False, f"The image file or the open image has no pixels ({size})."
    if abs((fw / fh) / (ow / oh) - 1.0) > ASPECT_TOLERANCE:
        return False, (f"The image file's shape differs from the open image's ({size}): it must be the same field, "
                       "only scaled. Export it with the open image's aspect ratio, or open the file in Siril, "
                       "plate-solve it and run Fly-Through on it.")
    if fc != oc:
        return False, (f"The image file is {_kind(fc)} but the open image is {_kind(oc)}: it is not converted. "
                       "Export the file with the open image's channels, or open it in Siril, plate-solve it and "
                       "run Fly-Through on it.")
    if (fw, fh) == (ow, oh):
        return True, ""
    return True, (f"Image file {fw} x {fh} is the same field as the open image ({ow} x {oh}): "
                  "it is resampled to that size")


def file_grid(path: str) -> Grid:
    """``(width, height, channels)`` of a TIFF or FITS file, from its header only (the pixels are not read).

    Channels count as :func:`loomsiril.siril_bridge.read_layer` gives them: an alpha sample is dropped."""
    p = Path(os.path.expanduser(str(path).strip()))
    suffix = p.suffix.lower()
    if suffix in (".tif", ".tiff"):
        import tifffile

        with tifffile.TiffFile(os.fspath(p)) as tf:
            page = tf.pages[0]
            w, h, c = int(page.imagewidth), int(page.imagelength), int(page.samplesperpixel)
        return w, h, (1 if c == 2 else min(c, 3))
    if suffix in (".fit", ".fits", ".fts"):
        from loomsiril import io_fits

        data = b""
        with open(p, "rb") as f:
            while len(data) < _HEADER_LIMIT:
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                data += chunk
                try:
                    header, _ = io_fits._parse_header(data)
                except ValueError:                      # no END card yet: read on
                    continue
                naxis = int(header.get("NAXIS", 0))
                if naxis not in (2, 3):
                    raise ValueError(f"{p.name}: NAXIS {naxis} not supported (2 or 3)")
                return int(header["NAXIS1"]), int(header["NAXIS2"]), int(header["NAXIS3"]) if naxis == 3 else 1
        raise ValueError(f"{p.name}: not a FITS file (no complete header)")
    raise ValueError(f"{p.name}: an image file must be a .tif, .tiff, .fit, .fits or .fts file")


def grid_line(path: str, opened: Optional[Grid]) -> tuple[bool, str]:
    """:func:`verdict` for the file at ``path`` (reads its header): ``(usable, text)``; ``(True, "")`` when the open
    image's grid is not known or the file cannot be read at all (the existence check says that)."""
    if opened is None:
        return True, ""
    try:
        file = file_grid(path)
    except Exception as exc:
        return False, f"The image file cannot be read as TIFF or FITS ({exc}): export it again, or choose another file."
    return verdict(file, opened)


def _axis(out_n: int, src_n: int) -> resample.AxisWeights:
    view = resample.AxisView(crop_start=0.0, crop_len=float(out_n), tp=-0.5, K=out_n / src_n, src_n=src_n)
    return resample.axis_weights(out_n, view, "cubic")


def _rows_of(ax: resample.AxisWeights, r0: int, r1: int) -> resample.AxisWeights:
    """The taps of output rows ``r0 .. r1 - 1`` of ``ax`` as an axis of their own."""
    start = ax.start[r0:r1 + 1].astype(np.int64)
    lo, hi = int(start[0]), int(start[-1])
    return resample.AxisWeights(r1 - r0, (start - lo).astype(np.int32), ax.idx[lo:hi], ax.w[lo:hi])


def _fit(plane: np.ndarray, width: int, height: int) -> np.ndarray:
    h, w = plane.shape
    ax, ay = _axis(width, w), _axis(height, h)
    across = resample.resample_rows(plane, w, ax, 0, h)                   # every row, resampled to the new width
    same = resample.AxisWeights(width, np.arange(width + 1, dtype=np.int32), np.arange(width, dtype=np.int32),
                                np.ones(width, dtype=np.float64))
    out = np.empty((height, width), dtype=np.float32)
    for r0 in range(0, height, _ROWS):
        r1 = min(height, r0 + _ROWS)
        out[r0:r1] = resample.resample(across, width, same, _rows_of(ay, r0, r1))
    lo, hi = float(plane.min()), float(plane.max())
    np.clip(out, lo, hi, out=out)                                         # Catmull-Rom rings: stay in the file's range
    return out


def fit_planes(planes: Sequence[np.ndarray], width: int, height: int) -> list[np.ndarray]:
    """Every plane resampled onto a ``width`` x ``height`` grid (planes already that size are returned as they are)."""
    out: list[np.ndarray] = []
    for plane in planes:
        h, w = plane.shape
        out.append(plane if (w, h) == (width, height) else _fit(np.asarray(plane, dtype=np.float32), width, height))
    return out
