"""Frame Selector: the per-frame measurements, taken with Siril, for the Siril port of the Frame Selector.

Ported in spirit from ``pixinsight/script/FrameSelector.js`` lines 55-211 (the SubframeSelector routine: the
process set-up, the measurement of one batch, the collection of its rows, the schema and path checks). The
PixInsight routine asks SubframeSelector for its numbers; this one asks Siril, through its own registration
output, so every figure below is one Siril wrote or one Siril fitted. No star fit is made here.

Route, for one batch of up to 8 frames of one channel:

1. The work directory is emptied. Input ``i`` (1-based, in batch order) becomes the link ``fs_<i:05d>.fit`` to the
   source file. A source that is not FITS (``.fit .fits .fts``, optionally ``.fz``, any case) is loaded through
   sirilpy and written there as a FITS file of the same name instead.
2. Siril's working directory is remembered, Siril enters the work directory, ``setfindstar reset`` fixes the star
   finder, ``register fs_ -2pass`` writes the sequence file ``fs_.seq`` and one star list per image in
   ``cache/``. Siril is always sent back to the remembered directory, also after a failure.
3. ``fs_.seq`` is read: its ``S`` count must equal the batch size and its ``I`` indices must be exactly 1..n.
   Results are matched to inputs by our own index, never by position in a listing.
4. The work directory is deleted. It holds links only; they are removed, never followed.

Mapping from the Frame Selector fields to the Siril figures (Siril 1.4.4):

=============  ==============================================================================================
field          Siril figure
=============  ==============================================================================================
fwhm           ``R`` line fwhm (px) of the registration layer
eccentricity   sqrt(1 - roundness^2), roundness = ``R`` line roundness (minor / major FWHM)
stars          ``R`` line nbstars (Siril caps the count at its ``maxstars`` setting)
psfSNR         median over the star list of A (fitted peak) divided by the ``M`` line bgnoise
psfFlux        median over the star list of 10^(-0.4 mag) (Siril's fitted Gaussian flux)
background     ``M`` line median (image median)
noise          ``M`` line bgnoise (background noise, same units as the background)
snrWeight      none (Siril has no SNR estimate); always None
altitude       supplied by the caller (from the FITS header)
=============  ==============================================================================================

A frame whose ``R`` line is all zero (registration failed, too few stars) is measured again with
``siril.analyse_image_from_file(path)`` for fwhm, roundness and star count. A frame with neither is unmeasurable.

Loading a non-FITS source with sirilpy makes it the image open in Siril; that side effect is Siril's, not hidden.
"""

from __future__ import annotations

import math
import os
import re
import shutil
from collections.abc import Callable, Mapping
from typing import Any, Optional

from loomsiril.frames import STORED_KEYS, median
from loomsiril.io_fits import write_fits
from loomsiril.jsmath import js_max, js_sqrt
from loomsiril.loom.siril_ops import command_text, quote_path
from loomsiril.siril_bridge import planes_from_pixeldata

MEASURE_CONFIG = "setfindstar reset; register fs_ -2pass"  # goes into the cache key elsewhere
SEQ = "fs_"

_MISSING = -999999.0
_R_FIELDS = ("fwhm", "wfwhm", "roundness", "quality", "background", "nbstars")
_M_FIELDS = ("total", "ngoodpix", "mean", "median", "sigma", "avgDev", "mad", "sqrtbwmv", "location", "scale",
             "min", "max", "normValue", "bgnoise")
_S_FIELDS = ("start", "count", "selected", "fixed", "ref", "version", "varsize", "fz", "drizzle")
_FITS_NAME = re.compile(r"\.(fit|fits|fts)(\.fz)?$", re.IGNORECASE)
_S_LINE = re.compile(r"^S\s+'([^']*)'\s*(.*)$")
_R_KEY = re.compile(r"^R(\d+)$")
_M_KEY = re.compile(r"^M(\d+)-(\d+)$")


def link_name(i: int) -> str:
    """The work-directory name of input ``i`` (1-based): ``fs_00001.fit`` for 1."""
    return f"{SEQ}{i:05d}.fit"


def _number(text: str) -> float:
    return float(text)


def _missing_or(value: float) -> Optional[float]:
    return None if value == _MISSING else value


def parse_seq(text: str) -> dict[str, Any]:
    """A Siril 1.4.4 sequence file. Keys: the S fields (``name``, ``start``, ``count``, ...), ``layers`` (the L
    line), ``included`` ({index: 0/1}, index 1-based), ``sizes`` ({index: (w, h)} for variable-size sequences),
    ``R`` ({layer: [row, ...]} in index order; a row has the R fields and ``H`` is left out) and ``M``
    ({(layer, index0): {field: value or None}}; -999999 is None)."""
    seq: dict[str, Any] = {"name": None, "layers": None, "included": {}, "sizes": {}, "R": {}, "M": {}}
    for s_field in _S_FIELDS:
        seq[s_field] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        key = parts[0]
        if key == "S":
            m = _S_LINE.match(line)
            if m is None:
                raise ValueError(f"bad S line: {line!r}")
            vals = [int(v) for v in m.group(2).split()]
            if len(vals) != len(_S_FIELDS):
                raise ValueError(f"bad S line: {line!r}")
            seq["name"] = m.group(1)
            seq.update(zip(_S_FIELDS, vals))
        elif key == "L":
            seq["layers"] = int(parts[1])
        elif key == "I":
            index = int(parts[1])
            seq["included"][index] = int(parts[2])
            if len(parts) > 3:
                w, h = parts[3].split(",")
                seq["sizes"][index] = (int(w), int(h))
        elif _R_KEY.match(key):
            layer = int(_R_KEY.match(key).group(1))
            vals = [_number(v) for v in parts[1:7]]
            seq["R"].setdefault(layer, []).append(dict(zip(_R_FIELDS, vals)) | {"nbstars": int(vals[5])})
        elif _M_KEY.match(key):
            layer, index0 = (int(g) for g in _M_KEY.match(key).groups())
            vals = [_missing_or(_number(v)) for v in parts[1:1 + len(_M_FIELDS)]]
            if len(vals) != len(_M_FIELDS):
                raise ValueError(f"bad M line: {line!r}")
            seq["M"][(layer, index0)] = dict(zip(_M_FIELDS, vals))
    return seq


def parse_star_list(text: str) -> list[dict[str, Any]]:
    """Siril's star list: the column header is the commented line ``# star#<TAB>layer<TAB>...``; the rows are read
    by those column names (never by position). Numbers become floats, ``Profile`` stays text."""
    columns: Optional[list[str]] = None
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith("#"):
            cells = [c.strip() for c in line[1:].strip().split("\t")]
            if "star#" in cells:
                columns = cells
            continue
        if columns is None:
            raise ValueError("star list rows before the column header")
        values = [v.strip() for v in line.split("\t")]
        if len(values) != len(columns):
            raise ValueError(f"star row has {len(values)} fields, header has {len(columns)}")
        row: dict[str, Any] = {}
        for name, value in zip(columns, values):
            try:
                row[name] = float(value)
            except ValueError:
                row[name] = value
        rows.append(row)
    return rows


def registration_layer(seq: dict[str, Any]) -> Optional[int]:
    """The layer that has R lines: 0 for mono and colour-filter data, 1 for RGB. None when no layer has any; the
    lowest one when several do."""
    layers = sorted(seq.get("R", {}))
    return layers[0] if layers else None


def batch_problem(seq: dict[str, Any], n: int) -> Optional[str]:
    """What is wrong with a sequence for a batch of ``n`` inputs, or None when it is right."""
    count = seq.get("count")
    if count is None:
        return "sequence file has no S line"
    if count != n:
        return f"sequence has {count} images, batch has {n}"
    included = seq.get("included", {})
    for index in range(1, n + 1):
        if index not in included:
            return f"sequence lacks image index {index}"
    for index in sorted(included):
        if not 1 <= index <= n:
            return f"sequence has image index {index} outside 1..{n}"
    return None


def eccentricity(roundness: float) -> float:
    """sqrt(max(0, 1 - r*r)): the eccentricity of the elliptical Gaussian with minor/major ratio ``roundness``."""
    return js_sqrt(js_max(0.0, 1 - roundness * roundness))


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _r_usable(r: Any) -> bool:
    """Any of fwhm, roundness, nbstars non-zero (a finite value)."""
    return r is not None and any(_finite(_get(r, k)) and _get(r, k) != 0 for k in ("fwhm", "roundness", "nbstars"))


def _fallback_usable(fb: Any) -> bool:
    return fb is not None and _finite(_get(fb, "fwhm")) and _get(fb, "fwhm") > 0


def metrics_from_siril(r: Optional[Mapping[str, Any]], m: Optional[Mapping[str, Any]], stars: list[dict[str, Any]],
                       fallback: Any, altitude: Optional[float]) -> Optional[dict[str, Any]]:
    """The stored metrics of one frame (keys: ``frames.STORED_KEYS``), or None when the frame is unmeasurable."""
    if _r_usable(r):
        source: Any = r
    elif _fallback_usable(fallback):
        source = fallback
    else:
        return None
    fwhm = float(_get(source, "fwhm"))
    roundness = _get(source, "roundness")
    ecc = eccentricity(float(roundness)) if _finite(roundness) else None
    nb = _get(source, "nbstars")
    star_count = int(nb) if _finite(nb) else None

    background = m.get("median") if m is not None else None
    noise = m.get("bgnoise") if m is not None else None

    psf_snr = None
    if stars and _finite(noise) and noise > 0:
        peaks = [s["A"] for s in stars if _finite(s.get("A"))]
        if peaks:
            psf_snr = median([a / noise for a in peaks])
    psf_flux = None
    mags = [s["mag"] for s in stars if _finite(s.get("mag"))]
    if mags:
        psf_flux = median([10 ** (-0.4 * mag) for mag in mags])

    out = {"fwhm": fwhm, "eccentricity": ecc, "noise": noise, "stars": star_count, "psfSNR": psf_snr,
           "background": background, "snrWeight": None, "altitude": altitude, "psfFlux": psf_flux}
    return {k: out[k] for k in STORED_KEYS}


def _is_fits(path: str) -> bool:
    return _FITS_NAME.search(os.path.basename(path)) is not None


def _attr(obj: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        value = _get(obj, name)
        if value is not None:
            return value
    return None


def _load_pixels(siril: Any, path: str) -> Any:
    """Pixels of a non-FITS source as sirilpy gives them (bottom-up, as Siril holds them)."""
    loaded = siril.load_image_from_file(path, with_pixels=True)
    pixels = loaded if hasattr(loaded, "shape") else _attr(loaded, ("pixeldata", "data"))
    if pixels is None:
        pixels = siril.get_image_pixeldata()
    return pixels


def _cmd(siril: Any, text: str) -> None:
    """One Siril command; any exception from ``cmd`` is a failed command."""
    try:
        if siril.cmd(text) is False:
            raise RuntimeError("Siril reported that the command failed")
    except Exception as exc:
        raise _CommandFailed(f"{text}: {exc}") from exc


class _CommandFailed(RuntimeError):
    pass


def _link_inputs(siril: Any, paths: list[str], work_dir: str) -> None:
    for i, path in enumerate(paths, start=1):
        dst = os.path.join(work_dir, link_name(i))
        if _is_fits(path):
            os.symlink(path, dst)
        else:
            planes = planes_from_pixeldata(_load_pixels(siril, path))
            write_fits(dst, planes)


def _read_seq(work_dir: str, n: int) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    seq_path = os.path.join(work_dir, f"{SEQ}.seq")
    if not os.path.isfile(seq_path):
        return None, None
    with open(seq_path, encoding="utf-8") as fh:
        try:
            seq = parse_seq(fh.read())
        except ValueError as exc:
            return None, f"unreadable sequence file: {exc}"
    problem = batch_problem(seq, n)
    return (None, problem) if problem else (seq, None)


def _read_stars(work_dir: str, i: int) -> list[dict[str, Any]]:
    path = os.path.join(work_dir, "cache", f"{SEQ}{i:05d}.lst")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return parse_star_list(fh.read())


def measure_batch(siril: Any, paths: list[str], work_dir: str,
                  altitude_of: Optional[Callable[[str], Optional[float]]] = None,
                  log: Optional[Callable[[str], None]] = None) -> tuple[dict[str, Optional[dict[str, Any]]],
                                                                        Optional[str]]:
    """Measure one batch of up to 8 frames of one channel through Siril's registration.

    Returns ``(results, problem)``: ``results`` maps each input path to its metrics (None when unmeasurable);
    ``problem`` is None, or the reason the batch was abandoned (then ``results`` is empty). A failed register
    leaves every frame to the fallback alone. Nothing is printed; ``log`` gets the failed steps."""
    seen: set[str] = set()
    for p in paths:
        if p in seen:
            return {}, f"duplicate path: {p}"
        seen.add(p)
    note = log or (lambda _text: None)
    n = len(paths)

    if os.path.lexists(work_dir):
        shutil.rmtree(work_dir)
    os.makedirs(work_dir)
    try:
        try:
            _link_inputs(siril, paths, work_dir)
        except Exception as exc:
            return {}, f"cannot prepare the batch: {exc}"

        wd0 = siril.get_siril_wd()
        seq: Optional[dict[str, Any]] = None
        try:
            _cmd(siril, command_text("cd", quote_path(work_dir)))
            _cmd(siril, command_text("setfindstar", "reset"))
            try:
                _cmd(siril, command_text("register", f"{SEQ}", "-2pass"))
                seq, problem = _read_seq(work_dir, n)
                if problem is not None:
                    return {}, problem
            except _CommandFailed as exc:
                note(f"register failed, fallback only: {exc}")
        except _CommandFailed as exc:
            return {}, f"cannot run Siril commands: {exc}"
        finally:
            _cmd(siril, command_text("cd", quote_path(wd0)))

        layer = registration_layer(seq) if seq is not None else None
        results: dict[str, Optional[dict[str, Any]]] = {}
        for i, path in enumerate(paths, start=1):
            r = None
            m = None
            if seq is not None and layer is not None:
                rows = seq["R"][layer]
                r = rows[i - 1] if i - 1 < len(rows) else None
                m = seq["M"].get((layer, i - 1))
            try:
                stars = _read_stars(work_dir, i)
            except (OSError, ValueError) as exc:
                return {}, f"unreadable star list for {path}: {exc}"
            fallback = None
            if not _r_usable(r):
                try:
                    fallback = siril.analyse_image_from_file(path)
                except Exception as exc:
                    note(f"analyse failed for {path}: {exc}")
            altitude = altitude_of(path) if altitude_of is not None else None
            results[path] = metrics_from_siril(r, m, stars, fallback, altitude)
        return results, None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
