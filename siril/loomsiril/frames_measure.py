"""Frame Selector: the per-frame measurements, taken with Siril, for the Siril port of the Frame Selector.

Ported in spirit from ``pixinsight/script/FrameSelector.js`` lines 55-211 (the SubframeSelector routine: the
process set-up, the measurement of one batch, the collection of its rows, the schema and path checks). The
PixInsight routine asks SubframeSelector for its numbers; this one asks Siril, through its own registration
output, so every figure below is one Siril wrote or one Siril fitted. No star fit is made here.

Route, for one batch of up to 8 frames of one channel:

1. The work directory is emptied. Input ``i`` (1-based, in batch order) becomes the link ``fs_<i:05d>.fit`` to the
   absolute source path. A source that is not FITS (``.fit .fits .fts``, optionally ``.fz``, any case) is read
   with ``siril.load_image_from_file(path, with_pixels=True)``, whose pixels are its ``data``, and written there
   as a FITS file of the same name. That call does not change the image open in Siril. A source without pixels
   is a problem for the whole batch.
2. Siril's working directory and its star-finder focal length and pixel size are read. Siril enters the work
   directory, ``setfindstar reset`` resets the star finder, and ``register fs_ -2pass`` writes the sequence file
   ``fs_.seq`` and one star list per image in ``cache/``. Afterwards Siril is sent back to the remembered
   directory and the focal length and pixel size are set again (``setfindstar -focal=... -pixelsize=...``). The
   other star-finder settings (sigma, radius, roundness, profile, relax, magnitude limits, convergence) cannot be
   read from Siril: they are reset for the batch and not restored, and each batch logs that. If the focal length
   or pixel size cannot be read (no call, an error, or no finite number), no reset is sent and nothing is
   restored: the batch is measured with Siril's current star-finder settings and a warning is logged. A failure
   to send Siril back is logged, not raised, so it does not hide the result.
3. ``fs_.seq`` is read: its ``S`` count must equal the batch size, its ``I`` indices must be exactly 1..n, and the
   registration layer must hold exactly n rows, so rows cannot shift. Results are matched to inputs by our own
   index, never by position in a listing. Siril caps the star count at ``max_stars`` (2000 after the reset).
4. The work directory is deleted. It holds links only; they are removed, never followed.

Not safe to run concurrently against one Siril: the working directory and the star-finder settings are global.

Mapping from the Frame Selector fields to the Siril figures (Siril 1.4.4):

=============  ==============================================================================================
field          Siril figure
=============  ==============================================================================================
fwhm           ``R`` line fwhm (px) of the registration layer
eccentricity   sqrt(1 - roundness^2), roundness = ``R`` line roundness (minor / major FWHM)
stars          ``R`` line nbstars (capped at Siril's max_stars)
psfSNR         median over the star list of A (fitted peak) divided by the ``M`` line bgnoise (a ratio, unscaled)
psfFlux        median over the star list of 10^(-0.4 mag) (Siril's fitted Gaussian flux), divided by normValue
background     ``M`` line median, divided by normValue
noise          ``M`` line bgnoise, divided by normValue
snrWeight      none (Siril has no SNR estimate); always None
altitude       supplied by the caller (from the FITS header)
=============  ==============================================================================================

Units: the ``M`` line normValue is the scale Siril reads the frame on (65535 for 16-bit data; the float scale for
float data). Dividing background, noise and psfFlux by it puts 16-bit, float and XISF frames of one channel on
the same 0..1 scale. A frame without a usable normValue gets None for those three.

A frame whose ``R`` line is all zero (registration failed, too few stars) is measured again with
``siril.analyse_image_from_file(path)`` for fwhm, roundness and star count. A frame with neither is unmeasurable.
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

DEFAULTS_WARNING = ("WARNING: the focal length or pixel size could not be read, so this measurement used Siril's "
                    "current star-finder settings (no reset, nothing restored)")

RESET_NOTE = ("setfindstar reset: sigma, radius, roundness, profile, relax, magnitude limits and convergence are "
              "reset for this batch and not restored (Siril does not report them); focal length and pixel size "
              "are restored")

_MISSING = -999999.0
_R_FIELDS = ("fwhm", "wfwhm", "roundness", "quality", "background", "nbstars")
_M_FIELDS = ("total", "ngoodpix", "mean", "median", "sigma", "avgDev", "mad", "sqrtbwmv", "location", "scale",
             "min", "max", "normValue", "bgnoise")
_S_FIELDS = ("start", "count", "selected", "fixed", "ref", "version", "varsize", "fz", "drizzle")
_FITS_NAME = re.compile(r"\.(fit|fits|fts)(\.fz)?$", re.IGNORECASE)
_S_LINE = re.compile(r"^S\s+'([^']*)'\s*(.*)$")
_R_KEY = re.compile(r"^R(\d+)$")
_M_KEY = re.compile(r"^M(\d+)-(\d+)$")


class _CommandFailed(RuntimeError):
    """A Siril command that raised or returned False."""


class _BatchError(RuntimeError):
    """A problem that abandons the whole batch; the message is the problem."""


def link_name(i: int) -> str:
    """The work-directory name of input ``i`` (1-based): ``fs_00001.fit`` for 1."""
    return f"{SEQ}{i:05d}.fit"


def _missing_or(value: float) -> Optional[float]:
    return None if value == _MISSING else value


def parse_seq(text: str) -> dict[str, Any]:
    """A Siril 1.4.4 sequence file. Keys: the S fields (``name``, ``start``, ``count``, ...), ``layers`` (the L
    line), ``included`` ({index: 0/1}, index 1-based), ``sizes`` ({index: (w, h)} for variable-size sequences),
    ``R`` ({layer: [row, ...]} in index order; a row has the R fields, the H matrix is left out) and ``M``
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
            vals = [float(v) for v in parts[1:7]]
            if len(vals) != len(_R_FIELDS):
                raise ValueError(f"bad R line: {line!r}")
            seq["R"].setdefault(layer, []).append(dict(zip(_R_FIELDS, vals)) | {"nbstars": int(vals[5])})
        elif _M_KEY.match(key):
            layer, index0 = (int(g) for g in _M_KEY.match(key).groups())
            vals = [_missing_or(float(v)) for v in parts[1:1 + len(_M_FIELDS)]]
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
    layer = registration_layer(seq)
    if layer is not None and len(seq["R"][layer]) != n:
        return f"sequence has {len(seq['R'][layer])} registration rows for layer {layer}, batch has {n}"
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


def _scaled(value: Optional[float], scale: Optional[float]) -> Optional[float]:
    return value / scale if value is not None and scale is not None else None


def metrics_from_siril(r: Optional[Mapping[str, Any]], m: Optional[Mapping[str, Any]], stars: list[dict[str, Any]],
                       fallback: Any, altitude: Optional[float]) -> Optional[dict[str, Any]]:
    """The stored metrics of one frame (keys: ``frames.STORED_KEYS``), or None when the frame is unmeasurable.

    background, noise and psfFlux are divided by the M line's normValue (see the module docstring); psfSNR is a
    ratio of the raw star peak to the raw background noise."""
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

    raw_background = m.get("median") if m is not None else None
    raw_noise = m.get("bgnoise") if m is not None else None
    norm = m.get("normValue") if m is not None else None
    scale = norm if _finite(norm) and norm > 0 else None

    psf_snr = None
    if stars and _finite(raw_noise) and raw_noise > 0:
        peaks = [s["A"] for s in stars if _finite(s.get("A"))]
        if peaks:
            psf_snr = median([a / raw_noise for a in peaks])
    psf_flux = None
    mags = [s["mag"] for s in stars if _finite(s.get("mag"))]
    if mags:
        psf_flux = _scaled(median([10 ** (-0.4 * mag) for mag in mags]), scale)

    out = {"fwhm": fwhm, "eccentricity": ecc, "noise": _scaled(raw_noise, scale), "stars": star_count,
           "psfSNR": psf_snr, "background": _scaled(raw_background, scale), "snrWeight": None,
           "altitude": altitude, "psfFlux": psf_flux}
    return {k: out[k] for k in STORED_KEYS}


def _is_fits(path: str) -> bool:
    return _FITS_NAME.search(os.path.basename(path)) is not None


def _cmd(siril: Any, text: str) -> None:
    """One Siril command; any exception from ``cmd`` (or a False return) is a failed command."""
    try:
        if siril.cmd(text) is False:
            raise RuntimeError("Siril reported that the command failed")
    except Exception as exc:
        raise _CommandFailed(f"{text}: {exc}") from exc


def _read_prefs(siril: Any) -> Optional[dict[str, Any]]:
    """The star-finder focal length and pixel size, as Siril stores them (what ``setfindstar reset`` may change).

    None unless both are finite numbers: a call that is missing, raises, or gives None, a string or NaN (Siril gives
    no response) means the reset is not sent at all."""
    try:
        prefs = {"focal_length": siril.get_siril_config("starfinder", "focal_length"),
                 "pixel_size": siril.get_siril_config("starfinder", "pixel_size")}
    except Exception:
        return None
    if all(_finite(v) for v in prefs.values()):
        return prefs
    return None


def _restore_text(prefs: Mapping[str, Any]) -> Optional[str]:
    parts = []
    if prefs.get("focal_length") is not None:
        parts.append(f"-focal={float(prefs['focal_length'])!r}")
    if prefs.get("pixel_size") is not None:
        parts.append(f"-pixelsize={float(prefs['pixel_size'])!r}")
    return command_text("setfindstar", *parts) if parts else None


def _link_one(siril: Any, path: str, dst: str) -> None:
    """Make ``dst`` a link to a FITS source, or a FITS copy of a non-FITS source. On failure ``dst`` is removed."""
    try:
        if _is_fits(path):
            if not os.path.isfile(path):
                raise FileNotFoundError(f"no such file: {path}")
            os.symlink(path, dst)
            return
        loaded = siril.load_image_from_file(path, with_pixels=True)
        data = _get(loaded, "data") if loaded is not None else None
        if data is None:
            raise _BatchError(f"no pixels for {path}")
        write_fits(dst, planes_from_pixeldata(data))
    except Exception:
        if os.path.lexists(dst):
            os.remove(dst)
        raise


def _link_inputs(siril: Any, abs_paths: list[str], work_dir: str,
                 note: Callable[[str], None]) -> tuple[list[int], dict[int, str]]:
    """Link every input that can be read, renumbered 1..k in order. Returns (indices of the linked inputs, {index
    of an unreadable input: reason}). An unreadable input does not stop the others."""
    good: list[int] = []
    bad: dict[int, str] = {}
    for i, path in enumerate(abs_paths):
        dst = os.path.join(work_dir, link_name(len(good) + 1))
        try:
            _link_one(siril, path, dst)
        except Exception as exc:
            bad[i] = str(exc)
            note(f"frame not measured: {path}: {exc}")
        else:
            good.append(i)
    return good, bad


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


def _leave(siril: Any, wd0: Optional[str], prefs: Optional[Mapping[str, Any]], note: Callable[[str], None]) -> None:
    """Send Siril back to its directory and restore its focal length and pixel size. Failures are logged only."""
    if wd0 is not None:
        try:
            _cmd(siril, command_text("cd", quote_path(wd0)))
        except Exception as exc:
            note(f"cannot return Siril to {wd0}: {exc}")
    if prefs is not None:
        text = _restore_text(prefs)
        if text is not None:
            try:
                _cmd(siril, text)
            except Exception as exc:
                note(f"cannot restore star-finder settings: {exc}")


def measure_batch(siril: Any, paths: list[str], work_dir: str,
                  altitude_of: Optional[Callable[[str], Optional[float]]] = None,
                  log: Optional[Callable[[str], None]] = None) -> tuple[dict[str, Optional[dict[str, Any]]],
                                                                        Optional[str]]:
    """Measure one batch of up to 8 frames of one channel through Siril's registration.

    Returns ``(results, problem)``. ``results`` maps each input path (as given) to its metrics, None when the frame
    is unmeasurable. A frame that cannot be read (missing, no pixels, any error while linking it) is None and the
    others are measured as if it were not there; if no frame can be read, ``results`` is ``{}`` and ``problem`` is
    None. ``problem`` is set only for a batch-wide failure (working directory, sequence, Siril commands): then
    ``results`` is ``{}``. If the star-finder focal length or pixel size cannot be read, no reset is sent and the
    batch is measured with Siril's current star-finder settings; the warning goes to ``log`` on each such batch
    (the caller reports it once per scan). A failed register leaves every readable frame to the fallback alone.
    Nothing is printed. Not safe to run concurrently against one Siril."""
    note = log or (lambda _text: None)
    seen: set[str] = set()
    for p in paths:
        key = os.path.abspath(p)
        if key in seen:
            return {}, f"duplicate path: {p}"
        seen.add(key)
    abs_paths = [os.path.abspath(p) for p in paths]
    work_dir = os.path.abspath(work_dir)

    if os.path.lexists(work_dir):
        shutil.rmtree(work_dir)
    try:
        os.makedirs(work_dir)
    except OSError as exc:
        return {}, f"cannot create the work directory: {exc}"
    try:
        good, bad = _link_inputs(siril, abs_paths, work_dir, note)
        if not good:
            return {}, None
        n = len(good)

        seq: Optional[dict[str, Any]] = None
        wd0: Optional[str] = None
        prefs = _read_prefs(siril)
        restore: Optional[dict[str, Any]] = None  # set just before the reset is sent: a reset that Siril applied
        try:                                       # is undone even when its reply fails
            wd0 = siril.get_siril_wd()
            _cmd(siril, command_text("cd", quote_path(work_dir)))
            if prefs is not None:
                restore = prefs
                _cmd(siril, command_text("setfindstar", "reset"))
                note(RESET_NOTE)
            else:
                note(DEFAULTS_WARNING)
            try:
                _cmd(siril, command_text("register", SEQ, "-2pass"))
            except _CommandFailed as exc:
                note(f"register failed, fallback only: {exc}")
            else:
                seq, problem = _read_seq(work_dir, n)
                if problem is not None:
                    return {}, problem
        except Exception as exc:
            return {}, f"cannot run Siril commands: {exc}"
        finally:
            _leave(siril, wd0, restore, note)

        layer = registration_layer(seq) if seq is not None else None
        results: dict[str, Optional[dict[str, Any]]] = {paths[i]: None for i in bad}
        for j, i in enumerate(good, start=1):
            path, abs_path = paths[i], abs_paths[i]
            r = None
            m = None
            if seq is not None and layer is not None:
                r = seq["R"][layer][j - 1]
                m = seq["M"].get((layer, j - 1))
            try:
                stars = _read_stars(work_dir, j)
            except (OSError, ValueError) as exc:
                return {}, f"unreadable star list for {path}: {exc}"
            fallback = None
            if not _r_usable(r):
                try:
                    fallback = siril.analyse_image_from_file(abs_path)
                except Exception as exc:
                    note(f"analyse failed for {path}: {exc}")
            altitude = altitude_of(path) if altitude_of is not None else None
            results[path] = metrics_from_siril(r, m, stars, fallback, altitude)
        return results, None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
