"""The geometry stages between per-channel correction and colour combination: registration of every channel onto the
reference, the crop to the area all channels cover, PSF halo matching and the narrowband balance (Loom's
``lib/Pipeline.js:2129-2388``, ``lib/Steps.js:1366-1534`` and the star measurement of ``lib/Steps.js:3572-3661``).

Every function takes plate paths and returns new paths; no input is ever modified. Plates are 32-bit float FITS files
in the run's work folder, top-down as ``loomsiril.io_fits`` reads them.
"""

from __future__ import annotations

import math
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional

import numpy as np

from loomsiril.io_fits import read_fits, write_fits

from . import imageops
from .colour_load import _PLANE_NAMES, _loomsrc
from .siril_ops import SirilOps
from .stagekit import StageFailed, run_siril_stage

__all__ = [
    "GROUP_REGISTER_LAYER",
    "balance_narrowband",
    "common_crop_rect",
    "crop_plate",
    "group_register_params",
    "match_halos",
    "measure_psf_sigma",
    "member_split_header",
    "register_channels",
    "register_group_image",
    "register_stage_params",
]

#: Side of the central square the stars are measured on (lib/Steps.js:3572-3661, ``Math.min(1200, w, h)``).
_STAR_SQUARE = 1200
#: At most this many detected stars are measured, in the detector's order (lib/Steps.js:3572-3661).
_MAX_STARS = 400
#: Fraction of each side of the frame used for the central medians (lib/Steps.js:262-273 and 1506-1534).
_CENTRE_FRACTION = 0.6
#: Linear-fit rejection bounds (lib/Steps.js:1411-1460).
_LINEAR_FIT_LOW = 0
_LINEAR_FIT_HIGH = 0.92
#: The channel order of Loom's ``Util.CHANNELS`` (lib/Util.js:436) and its narrowband subset (lib/Util.js:439).
_CHANNEL_ORDER: tuple[str, ...] = ("L", "R", "G", "B", "H", "S", "O")
_NARROWBAND: tuple[str, ...] = ("H", "S", "O")
_SEQUENCE = "reg"
#: The channel of the colour sequence the stars are found on when a colour group is registered as one image: the
#: green plane of an ``OSC`` group image, the first plane (H) of an ``OSCNB`` one (whose reference is expanded from G).
GROUP_REGISTER_LAYER: dict[str, int] = {"OSC": 1, "OSCNB": 0, "OSCHA": 0}


def register_stage_params(ref_chain_key: str) -> dict[str, Any]:
    """The register stage's cache parameters: the reference's own chain key (lib/Pipeline.js:2129-2215)."""
    return {"ref": ref_chain_key}


def _fresh_folder(work_dir: Path) -> Path:
    """The first ``register-<n>`` folder under ``work_dir`` that does not exist yet."""
    n = 1
    while (work_dir / f"register-{n}").exists():
        n += 1
    return work_dir / f"register-{n}"


def register_channels(ops: SirilOps, plates: Mapping[str, str], ref: str, work_dir: Any,
                      log: Callable[[str], Any]) -> dict[str, str]:
    """Register every channel onto the reference plate; returns each channel's registered plate.

    The plates are copied into a fresh ``register-<n>`` folder under ``work_dir`` with the reference first (so it is
    frame 1 of the sequence), Siril links and registers them there (``setref`` 1, homography, lanczos4; no two-pass),
    and each output is moved to ``<work_dir>/<channel>_register.fits``. The reference's output is bit-identical to its
    input. With no channel other than the reference, nothing is sent to Siril.
    """
    if ref not in plates:
        raise ValueError(f"the reference {ref!r} is not one of the plates")
    work = Path(os.fspath(work_dir))
    others = [ch for ch in plates if ch != ref]
    log(f"register: reference is {ref}")
    if not others:
        return {ref: os.fspath(plates[ref])}

    order = [ref] + others
    folder = _fresh_folder(work)
    folder.mkdir(parents=True)
    for index, ch in enumerate(order, start=1):
        shutil.copyfile(os.fspath(plates[ch]), folder / f"{index:02d}_{ch}.fits")

    previous = ops.wd()
    for ch in others:
        log(f"register: {ch} -> {ref}")
    ops.cd(folder)
    try:
        ops.link_sequence(_SEQUENCE)
        ops.setref(_SEQUENCE, 1)
        ops.register(_SEQUENCE)
    finally:
        ops.cd(previous)

    out: dict[str, str] = {}
    for index, ch in enumerate(order, start=1):
        produced = folder / f"r_{_SEQUENCE}_{index:05d}.fit"
        if not produced.exists():
            raise StageFailed(f"register failed on {ch}: Siril produced no registered frame for it (its own output is "
                              "in the console just above)")
        target = work / f"{ch}_register.fits"
        shutil.move(os.fspath(produced), os.fspath(target))
        out[ch] = os.fspath(target)
        log(f"register: {ch} registered to {out[ch]}")
    return out


def group_register_params(ref_chain_key: str, planes: Sequence[str]) -> dict[str, Any]:
    """The cache parameters of a grouped registration: the reference's chain key and the group image's planes.

    ``planes`` are the channel letters of the group image in plane order (``R,G,B`` or ``H,O,O``).
    """
    return {"ref": ref_chain_key, "planes": ",".join(planes)}


def member_split_header(header: Mapping[str, Any], member: str, master_path: Any, plane: int) -> dict[str, Any]:
    """``header`` with the FILTER and LOOMSRC of one member of a colour master, as the unregistered plate has them.

    Both come from the master (the channel's name, the master's file name and the plane), never from the header of
    the group image the member was registered in: that image is temporary and carries the first member's keywords.
    """
    out = {k: v for k, v in header.items() if k not in ("FILTER", "LOOMSRC")}
    out["FILTER"] = _PLANE_NAMES.get(member, member)
    out["LOOMSRC"] = _loomsrc(os.fspath(master_path), plane)
    return out


def _remove_exact(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def register_group_image(ops: SirilOps, ref_plate: Any, group_plates: Sequence[Any], layer: int, work_dir: Any,
                         log: Callable[[str], Any]) -> tuple[list[np.ndarray], dict[str, Any]]:
    """Register three plates onto the reference as one colour image, so they share one transform.

    The reference plate is expanded to three identical channels (``rgbcomp``) and the three ``group_plates`` (plane
    order, a plane may repeat: ``H, O, O``) are combined the same way. Siril links the two images in a fresh
    ``register-<n>`` folder under ``work_dir`` (the reference first), registers them with ``-layer=<layer>`` and the
    registered group image is read back: ``(planes, header)``. Every temporary file is removed by name, and the
    folder with them, whether or not the registration worked.
    """
    if len(group_plates) != 3:
        raise ValueError(f"a colour group image has three planes, got {len(group_plates)}")
    work = Path(os.fspath(work_dir))
    folder = _fresh_folder(work)
    folder.mkdir(parents=True)
    ref_stem, group_stem = folder / "01_ref", folder / "02_group"
    produced = folder / f"r_{_SEQUENCE}_00002.fit"
    # what ``link`` and ``register`` leave in the folder (measured on Siril 1.4): the sequence and its conversion
    # record, the two links to the frames, the registered frames and the star lists in the ``cache`` folder
    temporaries = [folder / f"{_SEQUENCE}_.seq", folder / f"r_{_SEQUENCE}_.seq", folder / f"{_SEQUENCE}_conversion.txt",
                   folder / f"r_{_SEQUENCE}_00001.fit", produced]
    for index in (1, 2):
        temporaries += [folder / f"{_SEQUENCE}_{index:05d}.fit", folder / "cache" / f"{_SEQUENCE}_{index:05d}.lst"]
    previous = ops.wd()
    try:
        ref_image = os.fspath(ops.rgbcomp(ref_plate, ref_plate, ref_plate, ref_stem))
        group_image = os.fspath(ops.rgbcomp(group_plates[0], group_plates[1], group_plates[2], group_stem))
        temporaries += [Path(ref_image), Path(group_image)]
        ops.cd(folder)
        try:
            ops.link_sequence(_SEQUENCE)
            ops.setref(_SEQUENCE, 1)
            ops.register(_SEQUENCE, layer=layer)
        finally:
            ops.cd(previous)
        if not produced.exists():
            raise StageFailed("register failed on the colour group: Siril produced no registered frame for it (its "
                              "own output is in the console just above)")
        planes, header = read_fits(os.fspath(produced))
        if len(planes) != 3:
            raise StageFailed(f"register failed on the colour group: the registered image has {len(planes)} "
                              "channels, not 3")
        log("register: colour group registered as one image")
        return planes, header
    finally:
        for path in temporaries:
            _remove_exact(path)
        for leftover in (folder / "cache", folder):
            try:
                leftover.rmdir()
            except OSError:
                pass  # something Siril wrote that is not listed above: the run's work folder is removed at the end


def _percent_text(value: float) -> str:
    """``value.toFixed(1)`` (round half up on the exact binary value), as Loom writes its percentages."""
    return str(Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def common_crop_rect(plates: Mapping[str, str], ref: str,
                     log: Optional[Callable[[str], Any]] = None) -> tuple[int, int, int, int]:
    """The rectangle (x0, y0, x1, y1, end-exclusive) every plate covers, without registration fill.

    Each plate's valid rectangle is taken with a margin of four pixels (lib/Pipeline.js:2221-2272). With ``log``, the
    line ``crop: common area W x H at (x0,y0) - P% of the reference frame`` is written (before the refusal when the
    common area is under half of the reference frame, as Loom does). Raises :class:`StageFailed` with Loom's message
    when the channels do not overlap or the common area is under half of the reference frame.
    """
    if ref not in plates:
        raise ValueError(f"the reference {ref!r} is not one of the plates")
    rects: list[tuple[int, int, int, int]] = []
    ref_shape: Optional[tuple[int, int]] = None
    for ch, path in plates.items():
        planes, _ = read_fits(os.fspath(path))
        rects.append(imageops.valid_rect(planes[0], imageops.VALID_RECT_MARGIN))
        if ch == ref:
            ref_shape = planes[0].shape
    assert ref_shape is not None
    height, width = ref_shape
    ref_rect = (0, 0, width, height)
    try:
        common = imageops.common_area(rects, ref_rect)
    except ValueError as exc:
        # common_area refuses before returning; the line is still written for the refusals that have a common area
        common = imageops.intersect_rects(rects)
        if common is not None and log is not None:
            log(_crop_line(common, ref_rect))
        raise StageFailed(str(exc)) from exc
    if log is not None:
        log(_crop_line(common, ref_rect))
    return common


def _crop_line(common: tuple[int, int, int, int], ref_rect: tuple[int, int, int, int]) -> str:
    x0, y0, x1, y1 = common
    ref_area = (ref_rect[2] - ref_rect[0]) * (ref_rect[3] - ref_rect[1])
    pct = 100 * ((x1 - x0) * (y1 - y0)) / ref_area
    return (f"crop: common area {x1 - x0} x {y1 - y0} at ({x0},{y0}) - {_percent_text(pct)}% of the reference "
            "frame")


def crop_plate(ops: SirilOps, in_path: Any, out_path: Any, rect: tuple[int, int, int, int]) -> str:
    """Crop a plate to ``rect`` (x0, y0, x1, y1, end-exclusive) and save it; returns the saved path.

    Siril's ``crop`` takes the origin from the top and the size (``crop x0 y0 x1-x0 y1-y0``); the WCS is updated by
    Siril.
    """
    x0, y0, x1, y1 = (int(v) for v in rect)
    return run_siril_stage(ops, in_path, out_path, lambda o: o.crop(x0, y0, x1 - x0, y1 - y0))


def _measure(ops: SirilOps, path: Any) -> Optional[tuple[float, int]]:
    """(sigma, number of stars) of the plate's PSF on its central square, or None when no star was measured."""
    ops.load(path)
    height, width = ops.pixels()[0].shape
    side = min(_STAR_SQUARE, width, height)
    x0 = (width - side) >> 1
    y0 = (height - side) >> 1
    ops.crop(x0, y0, side, side)
    stars = ops.find_stars()[:_MAX_STARS]
    fitted = [s for s in stars if _positive(s["fwhmx"]) and _positive(s["fwhmy"])]
    sigma = imageops.psf_sigma_from_fwhm([s["fwhmx"] for s in fitted], [s["fwhmy"] for s in fitted])
    if sigma is None:
        return None
    return sigma, len(fitted)


def _positive(value: Any) -> bool:
    f = float(value)
    return math.isfinite(f) and f > 0


def measure_psf_sigma(ops: SirilOps, path: Any, log: Optional[Callable[[str], Any]] = None) -> Optional[float]:
    """The PSF sigma of a plate, measured on its central square of at most 1200 pixels (lib/Steps.js:3572-3661).

    The first 400 detected stars are used; the sigma is ``imageops.psf_sigma_from_fwhm`` of their FWHM. Nothing is
    saved. Returns None when no star was measured.
    """
    measured = _measure(ops, path)
    if measured is None:
        return None
    sigma, n = measured
    if log is not None:
        log(f"psf: {n} fitted stars, sigma={sigma:.3f}")
    return sigma


def match_halos(ops: SirilOps, plates: Mapping[str, str], work_dir: Any,
                log: Callable[[str], Any]) -> dict[str, str]:
    """Blur every narrower colour plate to the widest PSF; returns the plates with the blurred ones replaced.

    L is never measured nor convolved (lib/Pipeline.js:2274-2335). A channel whose PSF cannot be measured is left as it
    is. With fewer than two measured channels nothing changes. Each channel is blurred by
    ``imageops.sigma_to_reach(widest, sigma)`` in numpy; the blurred plates are written to
    ``<work_dir>/<channel>_halo.fits``.
    """
    work = Path(os.fspath(work_dir))
    result = {ch: os.fspath(path) for ch, path in plates.items()}
    measured: list[tuple[str, float]] = []
    for ch in _CHANNEL_ORDER:
        if ch == "L" or ch not in plates:
            continue
        found = _measure(ops, plates[ch])
        if found is None:
            log(f"halos: {ch}: no PSF fit, leaving it untouched")
            continue
        sigma, n = found
        measured.append((ch, sigma))
        fwhm = imageops.sigma_to_fwhm(sigma)
        log(f"halos: {ch} sigma={sigma:.3f} (FWHM {fwhm:.2f} px, {n} stars)")

    if len(measured) < 2:
        log("halos: skipped: fewer than two channels could be measured")
        return result

    widest = max(sigma for _, sigma in measured)
    log(f"halos: matching to the widest, sigma={widest:.3f}")
    for ch, sigma in measured:
        add = imageops.sigma_to_reach(widest, sigma)
        if add <= 0:
            log(f"halos: {ch}: already the widest, unchanged")
            continue
        log(f"halos: {ch}: convolving by sigma={add:.3f}")
        planes, header = read_fits(plates[ch])
        blurred = imageops.gaussian_blur(planes, add)
        target = work / f"{ch}_halo.fits"
        write_fits(target, blurred, header, bitpix=-32)
        result[ch] = os.fspath(target)
    return result


def _js_exponential(value: float, digits: int) -> str:
    """``value.toExponential(digits)`` as JavaScript writes it (``1.2345e-3``, no padding of the exponent)."""
    mantissa, exponent = f"{value:.{digits}e}".split("e")
    return f"{mantissa}e{int(exponent):+d}"


def balance_narrowband(ops: SirilOps, plates: Mapping[str, str], palettes_wanted: bool, work_dir: Any,
                       log: Callable[[str], Any]) -> dict[str, str]:
    """Balance the narrowband plates (H, S, O) to the one with the lowest central median (lib/Pipeline.js:2337-2388).

    Fewer than two narrowband plates: nothing changes. With a palette wanted, each other plate gets a background
    offset (``x + (refMedian - median)``, numpy, not truncated), which keeps the line ratios for SPCC. Otherwise each
    other plate is linear-matched to the reference (Siril ``linear_match <ref> 0 0.92``). Returns the plates with the
    changed ones replaced by ``<work_dir>/<channel>_balance.fits``.
    """
    work = Path(os.fspath(work_dir))
    result = {ch: os.fspath(path) for ch, path in plates.items()}
    present = [ch for ch in _NARROWBAND if ch in plates]
    medians: dict[str, float] = {}
    for ch in present:
        planes, _ = read_fits(plates[ch])
        medians[ch] = imageops.median_of_centre(planes, _CENTRE_FRACTION)
    ref = imageops.min_median_key(medians)
    if ref is None or len(medians) <= 1:
        log("linearfit: skipped: fewer than two narrowband channels")
        return result

    others = [ch for ch in present if ch != ref]
    if palettes_wanted:
        log(f"background: narrowband sky floors matched to {ref} by offset; line ratios preserved for SPCC")
        for ch in others:
            planes, header = read_fits(plates[ch])
            delta = medians[ref] - imageops.median_of_centre(planes, _CENTRE_FRACTION)
            log(f"background: {ch}: offset {_js_exponential(delta, 4)} to match {ref}")
            shifted = imageops.background_offset(planes, medians[ref])
            target = work / f"{ch}_balance.fits"
            write_fits(target, shifted, header, bitpix=-32)
            result[ch] = os.fspath(target)
        return result

    log(f"linearfit: reference is {ref}")
    for ch in others:
        log(f"linearfit: {ch} -> {ref}")
        target = work / f"{ch}_balance.fits"
        ref_path = os.fspath(plates[ref])
        result[ch] = run_siril_stage(
            ops, plates[ch], target,
            lambda o, r=ref_path: o.linear_match(r, _LINEAR_FIT_LOW, _LINEAR_FIT_HIGH),
        )
    return result
