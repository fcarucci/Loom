"""The colour stages: the clean white-balance measurement, the RGB composite (combine, solve, SPCC or the measured white
balance) and the narrowband palettes (combine, SPCC, normalisation).

What lives here:

* ``rgb_stage_params`` / ``palette_stage_params``: the stage parameters hashed into cache keys, in Loom's stage order.
  Their shapes are Loom's (``lib/Pipeline.js:1453-1666``).
* ``rgb_runners`` / ``palette_runners``: the stage runners, ``fn(in_path, out_path) -> runctx.StageOutput``. A combine
  runner reads its three inputs from the ``plates`` mapping and ignores ``in_path``, as Loom's combine reads its views.
* ``measure_clean_white_balance``: the white balance of the composite built from the pre-aberration channels, recovered
  from the pixels SPCC changes (``imageops.recover_wb_factors``); ``None`` when it cannot be measured, and the RGB
  composite then calibrates directly.

Failures of the palette calibration and normalisation are non-fatal: the plate is kept as it was, and it is returned
with ``skip_cache=True`` so that it is never stored as a calibrated result.

Plates are float FITS files in the run's work folder, read and written with ``loomsiril.io_fits``. Importing this
module has no side effects and does not import sirilpy or tkinter.
"""

from __future__ import annotations

import math
import os
import shutil
from collections.abc import Mapping, Sequence
from typing import Any, Callable, Optional

from loomsiril.io_fits import read_fits, write_fits

from . import cache, channels, imageops, levels, nbnorm
from .runctx import RunContext, StageOutput
from .stagekit import StageFailed, run_siril_stage
from .stages_correct import _has_astrometric_solution, _solve_runner
from .stages_geometry import crop_plate, register_channels

__all__ = [
    "measure_clean_white_balance",
    "palette_runners",
    "palette_stage_params",
    "rgb_runners",
    "rgb_spcc_names",
    "rgb_stage_params",
]

Runner = Callable[[Any, Any], StageOutput]

#: The broadband channels of the RGB composite, in combine order.
RGB_CHANNELS: tuple[str, ...] = ("R", "G", "B")
#: SPCC's default narrowband bandwidth in nm (Steps.js:1461-1505, used when the configured value is not positive).
DEFAULT_NARROWBAND_BANDWIDTH = 3.0
#: Folder under the work folder for the clean white-balance measurement, apart from the run's own plates.
CLEAN_WB_FOLDER = "clean-wb"
#: Label of the reference plate in the clean measurement's registration.
_CLEAN_REFERENCE = "REF"


# ---- stage parameters (cache keys) ------------------------------------------------------------------------------------


def rgb_stage_params(config: Mapping[str, Any], lum_instrume: Any,
                     white_balance: Optional[Sequence[float]]) -> dict[str, Any]:
    """The RGB composite's stage parameters, in Loom's order: ``combine``, ``solveRGB``, ``spccRGB``.

    ``spccRGB`` carries the white balance source: the measured factors ``[r, g, b]`` when they exist, else ``"direct"``.
    The stage ``spfcRGB`` is not run (it is not part of this port), so it has no entry.
    """
    factors: Any = [float(f) for f in white_balance] if white_balance is not None else "direct"
    return {
        "combine": {},
        "solveRGB": {},
        "spccRGB": {"filters": config.get("filters") or {}, "instrume": lum_instrume, "whiteBalance": factors},
    }


def palette_stage_params(pal: str, config: Mapping[str, Any]) -> dict[str, Any]:
    """A palette's stage parameters, in Loom's order: ``paletteCombine``, ``paletteSpcc`` and, only when the option is
    on, ``paletteNorm``. Its absence (not a disabled entry) keeps the cache keys of runs without the option unchanged.
    """
    params: dict[str, Any] = {
        "paletteCombine": {},
        "paletteSpcc": {"palette": pal, "bandwidth": config.get("narrowbandBandwidth")},
    }
    if config.get("narrowbandNormalize"):
        params["paletteNorm"] = nbnorm.stage_params(pal)
    return params


# ---- helpers ----------------------------------------------------------------------------------------------------------


def _filters(config: Mapping[str, Any]) -> Mapping[str, Any]:
    return config.get("filters") or {}


def rgb_spcc_names(config: Mapping[str, Any], lum_instrume: Any) -> Optional[tuple[str, str, str, str]]:
    """The broadband SPCC names of the run, ``(sensor, R filter, G filter, B filter)``, or None when a filter is missing.

    The sensor is ``config["spccSensor"]`` when set, else the camera's own (``channels.spcc_sensor_for``). The
    orchestrator passes the result to :func:`palette_runners` so that a narrowband SPCC carries the same names.
    """
    filters = _filters(config)
    if any(not filters.get(ch) for ch in RGB_CHANNELS):
        return None
    sensor = config.get("spccSensor") or channels.spcc_sensor_for(lum_instrume)
    return (str(sensor), str(filters["R"]), str(filters["G"]), str(filters["B"]))


def _broadband_names(config: Mapping[str, Any], lum_instrume: Any) -> tuple[str, str, str, str]:
    """The broadband SPCC names; a missing filter stops the stage with the message Loom gives."""
    filters = _filters(config)
    for ch in RGB_CHANNELS:
        if not filters.get(ch):
            raise StageFailed(f"No filter curve for the {ch} channel. SPCC cannot colour-calibrate without red, green "
                              "and blue curves: choose a filter for each channel in the dialog.")
    names = rgb_spcc_names(config, lum_instrume)
    assert names is not None
    return names


def _bandwidth(value: Any) -> float:
    """The narrowband bandwidth: the configured value when it is a positive number, else SPCC's default of 3 nm."""
    if isinstance(value, bool):
        return DEFAULT_NARROWBAND_BANDWIDTH
    try:
        bw = float(value)
    except (TypeError, ValueError):
        return DEFAULT_NARROWBAND_BANDWIDTH
    return bw if math.isfinite(bw) and bw > 0 else DEFAULT_NARROWBAND_BANDWIDTH


def _require(plates: Mapping[str, Any], needed: Sequence[str]) -> None:
    missing = [ch for ch in needed if ch not in plates]
    if missing:
        raise ValueError("missing plates: " + ", ".join(missing))


def _combine(ctx: RunContext, paths: Sequence[Any], out_path: Any) -> StageOutput:
    """Combine three mono plates (R, G, B order, already in one frame) into a colour plate.

    ``rgbcomp`` writes the composite (Siril adds ``.fit`` to its output name); the keywords whose values differ between
    the three inputs are then removed, as Loom holds them back during ChannelCombination, and the plate is rewritten
    with its pixels unchanged. The composite keeps the first plate's world coordinates, as Siril gives it.
    """
    r, g, b = (os.fspath(p) for p in paths)
    dst = os.fspath(out_path)
    headers = [read_fits(p)[1] for p in (r, g, b)]
    ctx.log(f"combine -> {os.path.basename(dst)}")
    produced = ctx.ops.rgbcomp(r, g, b, os.path.splitext(dst)[0])
    planes, header = read_fits(os.fspath(produced))
    write_fits(dst, planes, imageops.without_inconsistent_keywords(header, headers), bitpix=-32)
    return StageOutput(path=dst)


def _spcc_rgb_runner(ctx: RunContext, config: Mapping[str, Any], lum_instrume: Any,
                     factors: Optional[Sequence[float]]) -> Runner:
    def run(in_path: Any, out_path: Any) -> StageOutput:
        src = os.fspath(in_path)
        dst = os.fspath(out_path)
        if factors is not None:
            planes, header = read_fits(src)
            ctx.log("whitebalance: gains R/G/B = " + " / ".join(f"{f:.6f}" for f in factors))
            write_fits(dst, imageops.apply_white_balance(planes, factors), header, bitpix=-32)
            return StageOutput(path=dst)
        sensor, r, g, b = _broadband_names(config, lum_instrume)
        ctx.log("colour calibration (SPCC) -> RGB")
        saved = run_siril_stage(ctx.ops, src, dst, lambda o: o.spcc_broadband(sensor, r, g, b))
        return StageOutput(path=saved)

    return run


def rgb_runners(ctx: RunContext, plates: Mapping[str, Any], config: Mapping[str, Any], lum_instrume: Any,
                factors: Optional[Sequence[float]]) -> dict[str, Runner]:
    """The RGB composite's runners: ``combine`` from the R, G and B plates, ``solveRGB``, and ``spccRGB``.

    ``plates`` maps R, G and B to their (already cropped and registered) plate paths. With ``factors`` (the measured
    clean white balance) ``spccRGB`` applies them in numpy; without, it runs SPCC on the composite.
    """
    _require(plates, RGB_CHANNELS)
    paths = [plates[ch] for ch in RGB_CHANNELS]

    def combine(in_path: Any, out_path: Any) -> StageOutput:
        return _combine(ctx, paths, out_path)

    return {
        "combine": combine,
        "solveRGB": _solve_runner(ctx, "RGB"),
        "spccRGB": _spcc_rgb_runner(ctx, config, lum_instrume, factors),
    }


def _palette_map(pal: str, plates: Mapping[str, Any]) -> list[str]:
    """The channel letters a palette maps to R, G and B, after checking that the palette exists and is supplied."""
    mapping = channels.PALETTES.get(pal)
    if mapping is None or channels.palette_wavelengths(pal) is None:
        raise ValueError(f"Unknown palette: {pal}")
    missing = channels.palette_missing(pal, list(plates))
    if missing:
        raise ValueError(f"The {pal} palette needs {' and '.join(missing)}, which "
                         f"{'was' if len(missing) == 1 else 'were'} not supplied.")
    return list(mapping)


#: Keywords that carry a plate's astrometric solution (and its pointing), copied from a solved plate onto a palette.
_SOLUTION_KEYWORDS = ("CTYPE", "CRVAL", "CRPIX", "CD1_", "CD2_", "CDELT", "CROTA", "CUNIT", "PC1_", "PC2_", "LONPOLE",
                      "LATPOLE", "EQUINOX", "RADESYS", "A_", "B_", "AP_", "BP_", "WCSAXES", "PLTSOLVD", "OBJCTRA",
                      "OBJCTDEC")


def _solution_of(plates: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The solution keywords of the first plate that has one (L, R, G, B, then the narrowband ones), or None.

    Every plate is registered to one reference and cropped alike, so a solved plate's solution holds for a palette built
    from them. ``combine`` drops the keywords that differ between its three inputs, and the narrowband plates, never
    solved themselves, differ in all of them: a palette would otherwise reach SPCC without coordinates.
    """
    for key in ("L", "R", "G", "B", "H", "S", "O"):
        if key not in plates:
            continue
        header = read_fits(os.fspath(plates[key]))[1]
        if _has_astrometric_solution(header):
            return {k: v for k, v in header.items()
                    if str(k).strip() in ("RA", "DEC") or str(k).strip().startswith(_SOLUTION_KEYWORDS)}
    return None


def _palette_spcc(ctx: RunContext, pal: str, plates: Mapping[str, Any], config: Mapping[str, Any],
                  rgb_spcc_names: Optional[tuple]) -> Runner:
    wavelengths = channels.palette_wavelengths(pal) or []
    bandwidth = _bandwidth(config.get("narrowbandBandwidth"))

    def run(in_path: Any, out_path: Any) -> StageOutput:
        src = os.fspath(in_path)
        dst = os.fspath(out_path)
        current = src
        try:
            _, header = read_fits(current)
            if not _has_astrometric_solution(header):
                solved = os.path.join(os.fspath(ctx.work_dir), f"{pal}_solved.fits")
                donor = _solution_of(plates)
                if donor is not None:
                    planes, header = read_fits(current)
                    ctx.log(f"{pal}: using the astrometric solution of the registered plates")
                    write_fits(solved, planes, {**header, **donor}, bitpix=-32)
                    current = solved
                else:
                    current = _solve_runner(ctx, pal)(current, solved).path
            ctx.log(f"{pal} narrowband: " + ", ".join(f"{'RGB'[i]}={w['channel']} {w['nm']}nm"
                                                    for i, w in enumerate(wavelengths))
                    + f", bandwidth {bandwidth}nm")
            ctx.ops.load(current)
            wl = [w["nm"] for w in wavelengths]
            kwargs: dict[str, Any] = {}
            if rgb_spcc_names is not None:
                sensor, r, g, b = rgb_spcc_names
                kwargs = {"sensor": sensor, "r": r, "g": g, "b": b}
            ctx.ops.spcc_narrowband(wl[0], bandwidth, wl[1], bandwidth, wl[2], bandwidth, **kwargs)
            saved = ctx.ops.save(dst)
            return StageOutput(path=os.fspath(saved))
        except Exception as exc:
            if ctx.cancelled():
                raise
            ctx.warn(f"{pal} could not be calibrated ({exc}); the palette is kept uncalibrated")
            shutil.copyfile(current, dst)
            return StageOutput(path=dst, skip_cache=True)

    return run


def _palette_norm(ctx: RunContext, pal: str) -> Runner:
    def run(in_path: Any, out_path: Any) -> StageOutput:
        src = os.fspath(in_path)
        dst = os.fspath(out_path)
        try:
            planes, header = read_fits(src)
            write_fits(dst, nbnorm.normalize(planes, pal), header, bitpix=-32)
            return StageOutput(path=dst)
        except Exception as exc:
            if ctx.cancelled():
                raise
            ctx.warn(f"{pal} could not be normalised ({exc}); the palette is kept as it is")
            shutil.copyfile(src, dst)
            return StageOutput(path=dst, skip_cache=True)

    return run


def palette_runners(ctx: RunContext, pal: str, plates: Mapping[str, Any], config: Mapping[str, Any],
                    rgb_spcc_names: Optional[tuple]) -> dict[str, Runner]:
    """The runners of one narrowband palette: ``paletteCombine``, ``paletteSpcc`` and, when enabled,
    ``paletteNorm``.

    ``plates`` maps the narrowband channels (H, S, O) to their plate paths. ``rgb_spcc_names`` is ``(sensor, r, g, b)``
    when a broadband calibration names its database entries in this run, else None: the narrowband SPCC then carries
    the same four names (Siril refuses it after a broadband SPCC without them, see ``siril_ops``).
    """
    mapping = _palette_map(pal, plates)

    def combine(in_path: Any, out_path: Any) -> StageOutput:
        return _combine(ctx, [plates[ch] for ch in mapping], out_path)

    runners: dict[str, Runner] = {
        "paletteCombine": combine,
        "paletteSpcc": _palette_spcc(ctx, pal, plates, config, rgb_spcc_names),
    }
    if config.get("narrowbandNormalize"):
        runners["paletteNorm"] = _palette_norm(ctx, pal)
    return runners


# ---- clean white balance ----------------------------------------------------------------------------------------------


def _measure_clean(ctx: RunContext, clean_plates: Mapping[str, tuple[str, str]], ref_plate: Any,
                   rect: tuple[int, int, int, int], config: Mapping[str, Any], lum_instrume: Any) -> list[float]:
    """Register the pre-aberration R, G and B plates to the reference, crop, combine, solve and calibrate, then recover
    the gains from the pixels SPCC changed. Raises when a step fails; the caller turns that into a fallback.

    ``ref_plate`` is the reference as it is before any crop: the channels are cropped here, after registration.
    """
    work = os.path.join(os.fspath(ctx.work_dir), CLEAN_WB_FOLDER)
    os.makedirs(work, exist_ok=True)
    plates = {_CLEAN_REFERENCE: os.fspath(ref_plate)}
    for ch in RGB_CHANNELS:
        plates[ch] = os.fspath(clean_plates[ch][1])
    registered = register_channels(ctx.ops, plates, _CLEAN_REFERENCE, work, ctx.log)
    cropped = [crop_plate(ctx.ops, registered[ch], os.path.join(work, f"{ch}_clean_crop.fits"), rect)
               for ch in RGB_CHANNELS]
    composite = _combine(ctx, cropped, os.path.join(work, "RGB_clean.fits")).path
    solved = _solve_runner(ctx, "RGB")(composite, os.path.join(work, "RGB_clean_solved.fits")).path
    before = read_fits(solved)[0]
    sensor, r, g, b = _broadband_names(config, lum_instrume)
    ctx.ops.load(solved)
    ctx.ops.spcc_broadband(sensor, r, g, b)
    after_path = os.fspath(ctx.ops.save(os.path.join(work, "RGB_clean_spcc.fits")))
    after = read_fits(after_path)[0]
    factors = imageops.recover_wb_factors(before, after)
    if any(not math.isfinite(f) or f <= 0 for f in factors):
        raise ValueError("SPCC did not record white balance factors")
    ctx.log("whitebalance: measured on uncorrected channels: " + " / ".join(f"{f:.6f}" for f in factors))
    return factors


def measure_clean_white_balance(ctx: RunContext, clean_plates: Mapping[str, tuple[str, str]], ref_plate: Any,
                                rect: tuple[int, int, int, int], config: Mapping[str, Any], lum_instrume: Any,
                                store: Optional[cache.CacheStore], use_cache: bool,
                                ignore_cache: bool) -> Optional[list[float]]:
    """The SPCC white balance of the pre-aberration channels, as ``[r, g, b]`` gains, or None.

    ``clean_plates`` maps R, G and B to ``(cache key, plate path)``: the pre-aberration entry of each channel. ``ref_plate``
    is the registration reference (uncropped) and ``rect`` is the run's crop, ``(x0, y0, x1, y1)``.

    Nothing is measured unless a sharpen tool is chosen and all three channels are given. A stored ``<key>.wb.json``
    is used when the cache is on and not ignored; otherwise the measurement runs and, when the cache is on, is stored.
    Any failure warns and returns None, and the RGB composite then calibrates directly.
    """
    if not levels.tool_chosen(config.get("sharpenTool")):
        return None
    if any(ch not in clean_plates for ch in RGB_CHANNELS):
        return None
    use_stored = bool(use_cache) and not ignore_cache
    try:
        common = dict(zip(("x0", "y0", "x1", "y1"), (int(v) for v in rect)))
        key = cache.clean_white_balance_key({ch: clean_plates[ch][0] for ch in RGB_CHANNELS}, common,
                                            _filters(config), lum_instrume)
        if use_stored and store is not None:
            stored = store.read_wb(key)
            if stored is not None:
                ctx.log(f"cache: clean white balance HIT {key[:12]}...")
                return stored
            ctx.log(f"cache: clean white balance MISS {key[:12]}...")
        factors = _measure_clean(ctx, clean_plates, ref_plate, rect, config, lum_instrume)
    except Exception as exc:
        if ctx.cancelled():
            raise
        ctx.warn(f"could not build the uncorrected reference ({exc}). Falling back to SPCC on the corrected composite, "
                 "which is known to produce a colour cast on aberration-corrected data.")
        return None
    if use_cache and store is not None:
        store.write_wb(key, factors)
    return factors
