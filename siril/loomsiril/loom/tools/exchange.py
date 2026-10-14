"""Exchange files for the external SyQon and Studio command-line tools, and the sanitising before a write.

Loom hands an image to an external tool as a 32-bit float file and reads the tool's result back. Two formats are
used: FITS (SyQon Studio; written bottom-up through :mod:`loomsiril.io_fits`, which flips the rows once) and float
TIFF (SyQon Starless; top-down through :mod:`loomsiril.io_tiff` and tifffile).

Sanitising, before any write (lib/StepsSyqon.js:2049-2063 for Studio):

* ``sanitize_basic``: non-finite values become 0.
* ``sanitize_studio``: non-finite and out-of-range (|x| >= 1e30) values become 0, then every value is clipped to
  [0, 1]. Loom runs this through PixelMath with truncation to [0, 1], so its maximum is never above 1 and Loom never
  scales: the returned scale is always 1.0 (kept in the return value for the callers' API).

Loom's temporary stretch clips to [0, 1] after the normalisation and after the inverse midtone transfer as well
(see tempstretch.py).

A result for a mono target that comes back with three channels takes channel 0.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import tifffile

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.io_tiff import read_image

__all__ = [
    "ROWORDER",
    "BOTTOM_UP",
    "TOP_DOWN",
    "sanitize_basic",
    "sanitize_studio",
    "write_exchange_fits",
    "read_exchange_fits",
    "write_exchange_tiff",
    "read_exchange_tiff",
    "declared_output",
]

ROWORDER = "ROWORDER"
BOTTOM_UP = "BOTTOM-UP"
TOP_DOWN = "TOP-DOWN"

# Values at or beyond this magnitude are treated as corrupt by Studio's sanitiser (lib/StepsSyqon.js:2050-2052).
_STUDIO_ABS_LIMIT = 1e30


def _float_copy(plane: np.ndarray) -> np.ndarray:
    a = np.array(plane, copy=True)
    if a.dtype.kind != "f":
        a = a.astype(np.float64)
    return a


def sanitize_basic(planes: Sequence[np.ndarray]) -> list[np.ndarray]:
    """Copies of the planes with every non-finite value set to 0; dtype is kept for float input."""
    out = []
    for p in planes:
        a = _float_copy(p)
        a[~np.isfinite(a)] = 0.0
        out.append(a)
    return out


def sanitize_studio(planes: Sequence[np.ndarray]) -> tuple[list[np.ndarray], float]:
    """Studio's sanitising, as Loom's PixelMath pass does it: ``(planes, 1.0)``.

    Non-finite and |x| >= 1e30 values become 0, then every value is clipped to [0, 1] (the PixelMath truncation).
    The scale is always 1.0: after the clip no maximum can exceed 1.01, so Loom never scales either.
    """
    out = []
    for p in planes:
        a = _float_copy(p)
        a[~np.isfinite(a) | (np.abs(a) >= _STUDIO_ABS_LIMIT)] = 0.0
        np.clip(a, 0.0, 1.0, out=a)
        out.append(a)
    return out, 1.0


def _take_channels(planes: list[np.ndarray], n_target_channels: int) -> list[np.ndarray]:
    """A mono target (one channel) takes channel 0 of a colour result; anything else must match in count."""
    if n_target_channels < 1:
        raise ValueError(f"n_target_channels must be at least 1, got {n_target_channels}")
    if len(planes) < n_target_channels:
        raise ValueError(f"the result has {len(planes)} channel(s); the target needs {n_target_channels}")
    return planes[:n_target_channels]


def write_exchange_fits(
    path: str | os.PathLike[str],
    planes: Sequence[np.ndarray],
    header: Mapping[str, Any] | None = None,
    sanitize: str = "basic",
) -> float:
    """Write float planes as a 32-bit float FITS exchange file; returns the scale applied (always 1.0).

    The file's header carries ``ROWORDER = 'BOTTOM-UP'``. ``sanitize`` is ``"basic"`` or ``"studio"``.
    """
    if sanitize == "basic":
        clean, scale = sanitize_basic(planes), 1.0
    elif sanitize == "studio":
        clean, scale = sanitize_studio(planes)
    else:
        raise ValueError(f"sanitize must be 'basic' or 'studio', got {sanitize!r}")
    hdr = dict(header or {})
    hdr[ROWORDER] = BOTTOM_UP
    write_fits(path, [np.asarray(p, dtype=np.float32) for p in clean], header=hdr, bitpix=-32)
    return scale


def read_exchange_fits(
    path: str | os.PathLike[str], n_target_channels: int
) -> tuple[list[np.ndarray], dict[str, Any]]:
    """Read a FITS result as top-down float planes, and its header.

    ``read_fits`` assumes bottom-up rows; a result whose header says ``ROWORDER = 'TOP-DOWN'`` is flipped back.
    """
    planes, header = read_fits(path)
    order = str(header.get(ROWORDER, BOTTOM_UP)).strip().upper()
    if order == TOP_DOWN:
        planes = [np.ascontiguousarray(p[::-1]) for p in planes]
    return _take_channels(planes, n_target_channels), header


def write_exchange_tiff(path: str | os.PathLike[str], planes: Sequence[np.ndarray]) -> None:
    """Write one or three float planes as a 32-bit float TIFF (top-down), after the basic sanitising."""
    if len(planes) not in (1, 3):
        raise ValueError(f"an exchange TIFF holds one or three planes, got {len(planes)}")
    arrs = [np.asarray(p, dtype=np.float32) for p in sanitize_basic(planes)]
    if len(arrs) == 1:
        tifffile.imwrite(os.fspath(path), arrs[0], photometric="minisblack")
    else:
        tifffile.imwrite(os.fspath(path), np.stack(arrs, axis=-1), photometric="rgb")


def read_exchange_tiff(path: str | os.PathLike[str], n_target_channels: int) -> list[np.ndarray]:
    """Read a float TIFF result as top-down float planes (TIFF rows are already top-down)."""
    planes, _icc = read_image(path)
    return _take_channels(planes, n_target_channels)


_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def declared_output(
    stdout: str | Iterable[str] | None,
    default_path: str | os.PathLike[str],
    suffix: str | None = None,
) -> str:
    """The last line of ``stdout`` that names an existing file, else ``default_path``.

    A tool's own declaration of where it wrote is the authority. ``suffix`` (for example ``".xisf"``) restricts
    the lines considered; lines of three characters or fewer are ignored (lib/StepsSyqon.js:2068-2070).
    """
    if stdout is None:
        lines: list[str] = []
    elif isinstance(stdout, str):
        lines = _LINE_BREAK.split(stdout)
    else:
        lines = [str(s) for s in stdout]
    for line in reversed(lines):
        s = line.strip()
        if len(s) <= 3:
            continue
        if suffix is not None and not s.lower().endswith(suffix.lower()):
            continue
        if os.path.isfile(s):
            return s
    return os.fspath(default_path)
