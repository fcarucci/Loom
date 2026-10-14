"""The per-channel correction stages that run before registration (broadband L, R, G, B and narrowband H, S, O).

What lives here:

* ``ingest_channel``: a channel's source file loaded by Siril and saved as a working FITS plate (lazy: the
  orchestrator calls it only when a stage must run). ``source_key`` is the channel's source fingerprint.
* ``broadband_stage_params`` / ``narrowband_stage_params``: the stage parameters hashed into cache keys, in Loom's
  stage order. Built from ``levels`` and ``stages_subsky``; the shapes are Loom's.
* ``broadband_runners`` / ``narrowband_runners``: the stage runners, ``fn(in_path, out_path) -> runctx.StageOutput``.
* ``master_quality``: the FWHM, eccentricity, noise and star count of the masters for the dialog list, cached in
  ``master-quality.json``.

Ports Loom's lib/Pipeline.js (lazyFileChannel, broadbandStages, broadbandRunners, narrowbandStages, aberrationParams),
lib/Steps.js (hasAstrometricSolution, verifyAndReport, solve, graxpert, aberration, measureMasterFWHM and the master
quality cache) and lib/StepsSyqon.js (gradientToolOf, gradientStageParams, removeGradient).

Stage notes: a stage with no runner (the gradient stage when the tool is none, the aberration stage when no sharpen
tool is chosen) is keyed exactly as Loom keys it; the orchestrator passes its input through. Correction failures
raise (they are never ``SKIP_CACHE``). The residual check never fails a run.

Importing this module has no side effects and does not import sirilpy or tkinter.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from collections.abc import Callable, Mapping
from typing import Any, Optional

from loomsiril.io_fits import read_fits

from . import cache, levels, preflight
from .residuals import RESIDUALS_BAD_PX, RESIDUALS_WARN_PX
from .runctx import RunContext, StageOutput
from .siril_ops import SirilOpsError
from .stagekit import StageFailed
from .stages_subsky import BROADBAND_CHANNELS, mgc_params, run_mgc
from .tools import graxpert_ai, rcastro, syqon_legacy, syqon_studio

__all__ = [
    "NARROWBAND_CHANNELS",
    "broadband_runners",
    "broadband_stage_params",
    "has_astrometric_solution",
    "ingest_channel",
    "master_quality",
    "master_quality_key",
    "narrowband_runners",
    "narrowband_stage_params",
    "solve_runner",
    "source_key",
]

Runner = Callable[[Any, Any], StageOutput]

#: The narrowband channels (never solved, never background-extracted with mgc).
NARROWBAND_CHANNELS: tuple[str, ...] = ("H", "S", "O")

#: Executable ids in the run context's ``tools`` mapping (loomsiril.loom.tools.specs).
STUDIO_TOOL_ID = syqon_studio.TOOL_NAME
RC_ASTRO_TOOL_ID = "rcastro"
PARALLAX_TOOL_ID = "parallax_cli"
GRAXPERT_TOOL_ID = graxpert_ai.TOOL_ID

#: The gradient tools a configuration may name (Loom's removeGradient, lib/StepsSyqon.js:1499-1512).
_KNOWN_GRADIENT_TOOLS = (levels.GRADIENT_TOOL_NONE, levels.GRADIENT_TOOL_GRAXPERT, levels.GRADIENT_TOOL_STUDIO)

#: Version prefix of the master-quality cache keys (Steps.MASTER_QUALITY_VERSION is "v3" in Loom).
MASTER_QUALITY_VERSION = "siril1"
MASTER_QUALITY_FILE = "master-quality.json"

#: Parameters of the Studio Parallax aberration pass (the correction only: no reduction, no deblur).
_STUDIO_ABERRATION_DOMAIN = "linear"


# ---- ingest and source identity ---------------------------------------------------------------------------------------


def source_key(path: Any) -> str:
    """The source fingerprint of a channel file: ``cache.file_source_key`` of its path, size and modification time."""
    p = os.fspath(path)
    st = os.stat(p)
    return cache.file_source_key(p, st.st_size, st.st_mtime)


def ingest_channel(ctx: RunContext, channel: str, src_path: Any) -> str:
    """Load the channel's source file in Siril and save it as ``<work>/<channel>_src.fits``; returns that path.

    The source file is read, never modified. ``set32bits`` is a mode, repeated harmlessly for each channel.
    """
    src = os.path.abspath(os.fspath(src_path))
    dst = os.path.join(os.fspath(ctx.work_dir), f"{channel}_src.fits")
    ctx.ops.set32bits()
    ctx.ops.load(src)
    saved = ctx.ops.save(dst)
    ctx.log(f"load {channel} <- {src}")
    return os.fspath(saved)


# ---- stage parameters (cache keys) ------------------------------------------------------------------------------------


def _check_broadband(channel: str) -> None:
    if channel not in BROADBAND_CHANNELS:
        raise ValueError(f"{channel!r} is not a broadband channel (L, R, G or B)")


def broadband_stage_params(channel: str, config: Mapping[str, Any], studio_found: bool) -> dict[str, Any]:
    """The pre-registration stage parameters of a broadband channel, in Loom's stage order.

    ``solve`` is always present (``{}``); ``mgc`` only when the gradient tool is none (``stages_subsky.mgc_params``);
    ``graxpert`` is the gradient stage, present as Loom keeps it (``enabled`` false for none and the other tools);
    ``aberration`` is keyed on the tool that really corrects (``levels.aberration_params``).
    """
    _check_broadband(channel)
    params: dict[str, Any] = {"solve": {}}
    mgc = mgc_params(config, channel)
    if mgc is not None:
        params["mgc"] = mgc
    params["graxpert"] = levels.gradient_stage_params(config)
    params["aberration"] = levels.aberration_params(config, bool(studio_found))
    return params


def narrowband_stage_params(config: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """The pre-registration stages of a narrowband channel (H, S, O), or None when it runs none.

    Only the gradient stage, and only when the narrowband option is on and a gradient tool is chosen. Never mgc.
    None (not an always-present stage) keeps the narrowband cache keys unchanged for runs that do not use the option.
    """
    if not config or not config.get("graxpertNarrowband"):
        return None
    if levels.gradient_tool_of(config) == levels.GRADIENT_TOOL_NONE:
        return None
    return {"graxpert": levels.gradient_stage_params(config)}


# ---- runners ----------------------------------------------------------------------------------------------------------


def studio_found(ctx: RunContext) -> bool:
    """Studio is found when its executable was resolved for this run.

    The orchestrator passes this same value to :func:`broadband_stage_params`, so a Studio stage is keyed exactly
    when the runner would use Studio.
    """
    return STUDIO_TOOL_ID in ctx.tools


_studio_found = studio_found  # the former private name, kept for callers of this module


def _studio(ctx: RunContext) -> syqon_studio.SyqonStudio:
    return syqon_studio.SyqonStudio(ctx.tool(STUDIO_TOOL_ID), ctx.tool_context(syqon_studio.ToolContext),
                                    ctx.tool_store)


def _number(value: Any) -> Optional[float]:
    """A finite header number, or None (booleans and text are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _px(value: Any) -> str:
    v = _number(value)
    return f"{v:.3f} px" if v is not None else "no value"


def has_astrometric_solution(header: Mapping[str, Any]) -> bool:
    """A solved header by the rule preflight uses (CTYPE1 and CRVAL1, case-insensitive), read from a FITS header."""
    keywords = [{"name": str(name), "value": str(value)} for name, value in header.items()]
    return preflight._has_wcs({"keywords": keywords})


def _report_verdict(ctx: RunContext, channel: str, result: Mapping[str, Any]) -> None:
    """The verdict line of a residual measurement (Steps.verifyAndReport): ok logs; poor, bad and unknown warn.

    The line carries the counts and the median deviation in px only: the residual checker does not return the RMS or
    the arcsecond figures that Loom also prints.
    """
    verdict = result.get("verdict")
    summary = (f"{channel}: {result.get('n_stars')} stars, {result.get('n_matched')} matched, "
               f"{result.get('n_retained')} retained, median deviation {_px(result.get('median_px'))}")
    if verdict == "bad":
        ctx.warn(f"{summary} -- THE SOLUTION IS WRONG. The median deviation has reached the "
                 f"{RESIDUALS_BAD_PX:.1f} px matching tolerance, so detected stars are no longer being paired with "
                 "the right catalog stars. Calibration will use the wrong sky positions.")
    elif verdict == "poor":
        ctx.warn(f"{summary} -- POOR. The median deviation is worse than the {RESIDUALS_WARN_PX:.3f} px PixInsight's "
                 "previous solver reached on a hard mosaic panel, so check the field edges before trusting the "
                 "calibration.")
    elif verdict == "unknown":
        ctx.warn(f"{summary} -- no usable deviation statistic; reported without a verdict.")
    else:
        ctx.log(summary)


def _verify_solution(ctx: RunContext, channel: str, header: Mapping[str, Any]) -> None:
    """Measure the solution of the image loaded in Siril against the catalogue. Never fails the run.

    Skipped without a residual checker, or once the checker has switched itself off for the run (no catalogue, or a
    header outside the contract); the checker logs its own skip line, once per run.
    """
    checker = ctx.residuals
    if checker is None or getattr(checker, "skipped", False):
        return
    try:
        stars = ctx.ops.find_stars()
        result = checker.check(channel, stars, header)
    except Exception as exc:  # a verification that cannot run is a lost diagnostic, not a failed run
        ctx.warn(f"{channel}: the astrometric solution could not be verified ({exc}). The solution itself is "
                 "unchanged and the run continues.")
        return
    if result is not None:
        _report_verdict(ctx, channel, result)


def solve_runner(ctx: RunContext, channel: str) -> Runner:
    def run(in_path: Any, out_path: Any) -> StageOutput:
        src = os.fspath(in_path)
        dst = os.fspath(out_path)
        _, header = read_fits(src)
        ctx.ops.load(src)
        if has_astrometric_solution(header):
            ctx.log(f"{channel} already has an astrometric solution -- skipping")
            shutil.copyfile(src, dst)
        else:
            ctx.log(f"plate solving {channel}")
            try:
                ctx.ops.platesolve(force=False, focal=_number(header.get("FOCALLEN")),
                                   pixel_size=_number(header.get("XPIXSZ")))
            except SirilOpsError as exc:
                raise StageFailed(f"plate solving failed on {channel}: {exc}") from exc
            ctx.ops.save(dst)
            _, header = read_fits(dst)
        _verify_solution(ctx, channel, header)
        return StageOutput(path=dst)

    return run


def _mgc_runner(ctx: RunContext, channel: str, params: Mapping[str, Any]) -> Runner:
    def run(in_path: Any, out_path: Any) -> StageOutput:
        return StageOutput(path=run_mgc(ctx.ops, in_path, out_path, params, log=ctx.log, label=channel))

    return run


def _gradient_runner(ctx: RunContext, channel: str, config: Mapping[str, Any]) -> Optional[Runner]:
    """The gradient stage's runner for the chosen tool, or None for none (the stage is absent)."""
    tool = levels.gradient_tool_of(config)
    if tool not in _KNOWN_GRADIENT_TOOLS:
        raise ValueError(f"Unknown gradient removal tool: {tool}")
    if tool == levels.GRADIENT_TOOL_GRAXPERT:
        def run_graxpert(in_path: Any, out_path: Any) -> StageOutput:
            saved = graxpert_ai.run_bge(ctx.ops, ctx.tool(GRAXPERT_TOOL_ID), in_path, out_path, config["smoothing"],
                                        label=channel, log=ctx.log)
            return StageOutput(path=saved)

        return run_graxpert
    if tool == levels.GRADIENT_TOOL_STUDIO:
        def run_studio(in_path: Any, out_path: Any) -> StageOutput:
            ctx.log(f"SyQon Studio Deep Gradient -> {channel}")
            dst = os.fspath(out_path)
            _studio(ctx).deep_gradient({}, os.fspath(in_path), dst)
            return StageOutput(path=dst)

        return run_studio
    return None


def _aberration_runner(ctx: RunContext, channel: str, config: Mapping[str, Any]) -> Optional[Runner]:
    """The aberration stage's runner for the tool that really corrects; None when no sharpen tool is chosen."""
    sharpen = config.get("sharpenTool")
    if not levels.tool_chosen(sharpen):
        return None
    corrector = levels.aberration_corrector(sharpen, studio_found(ctx))
    if corrector == levels.SHARPEN_TOOL_BXT:
        def run_bxt(in_path: Any, out_path: Any) -> StageOutput:
            ctx.log(f"aberration {channel}: BlurXTerminator correction only")
            dst = os.fspath(out_path)
            rcastro.RcAstro(ctx.tool(RC_ASTRO_TOOL_ID), ctx.tool_context(rcastro.ToolContext)).aberration(in_path, dst)
            return StageOutput(path=dst)

        return run_bxt
    if corrector == levels.SHARPEN_TOOL_SYQON:
        def run_syqon(in_path: Any, out_path: Any) -> StageOutput:
            ctx.log(f"aberration {channel}: SyQon Parallax (unlinked)")
            dst = os.fspath(out_path)
            syqon_legacy.parallax(ctx.tool(PARALLAX_TOOL_ID), ctx.tool_context(syqon_legacy.ToolContext),
                                  in_path, dst, "aberration", 0.0, False)
            return StageOutput(path=dst)

        return run_syqon
    if corrector == levels.SHARPEN_TOOL_STUDIO:
        family = levels.studio_family(config)

        def run_studio(in_path: Any, out_path: Any) -> StageOutput:
            ctx.log(f"aberration {channel}: SyQon Studio Parallax (correction only)")
            dst = os.fspath(out_path)
            settings = {"parallax": {"correction": True, "reduction": 0, "deblur": 0, "family": family}}
            _studio(ctx).parallax(_STUDIO_ABERRATION_DOMAIN, settings, os.fspath(in_path), dst)
            return StageOutput(path=dst)

        return run_studio
    raise ValueError(f"Aberration correction is not implemented for {sharpen}")


def broadband_runners(ctx: RunContext, channel: str, config: Mapping[str, Any]) -> dict[str, Runner]:
    """The runners of a broadband channel, by stage name. A stage absent here has no work to do (see the module note).

    ``solve`` always; ``mgc`` only with the gradient tool none; ``graxpert`` for GraXpert or Studio Deep Gradient;
    ``aberration`` only when a sharpen tool is chosen. The Studio check reads the run's resolved tools (see
    :func:`studio_found`). An unknown gradient tool raises ValueError here, as Loom's removeGradient throws.
    """
    _check_broadband(channel)
    runners: dict[str, Runner] = {"solve": solve_runner(ctx, channel)}
    mgc = mgc_params(config, channel)
    if mgc is not None:
        runners["mgc"] = _mgc_runner(ctx, channel, mgc)
    gradient = _gradient_runner(ctx, channel, config)
    if gradient is not None:
        runners["graxpert"] = gradient
    aberration = _aberration_runner(ctx, channel, config)
    if aberration is not None:
        runners["aberration"] = aberration
    return runners


def narrowband_runners(ctx: RunContext, channel: str, config: Mapping[str, Any]) -> dict[str, Runner]:
    """The runners of a narrowband channel: the gradient stage, when ``narrowband_stage_params`` asks for it."""
    if channel not in NARROWBAND_CHANNELS:
        raise ValueError(f"{channel!r} is not a narrowband channel (H, S or O)")
    if narrowband_stage_params(config) is None:
        return {}
    gradient = _gradient_runner(ctx, channel, config)
    return {} if gradient is None else {"graxpert": gradient}


# ---- master quality (the dialog list) ---------------------------------------------------------------------------------


def master_quality_key(path: Any) -> str:
    """``siril1|<path>|<size>|<mtime in ms>``: a re-stacked master (new size or time) is measured again.

    A file that cannot be read keys on ``0|0``, as Loom's key does (lib/Steps.js masterQualityKey).
    """
    p = os.fspath(path)
    try:
        st = os.stat(p)
        size, mtime_ms = st.st_size, st.st_mtime_ns // 1_000_000
    except OSError:
        size, mtime_ms = 0, 0
    return f"{MASTER_QUALITY_VERSION}|{p}|{size}|{mtime_ms}"


def _quality_from_analysis(analysis: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The four measurements of one master, or None when Siril gives no usable FWHM (nothing is cached then).

    Eccentricity is sqrt(1 - roundness^2); Siril's roundness is the minor/major axis ratio.
    """
    fwhm = _number(analysis.get("fwhm"))
    if fwhm is None or not fwhm > 0:
        return None
    roundness = _number(analysis.get("roundness"))
    eccentricity = None if roundness is None else math.sqrt(max(0.0, 1.0 - roundness * roundness))
    return {
        "fwhm": fwhm,
        "eccentricity": eccentricity,
        "noise": _number(analysis.get("bgnoise")),
        "stars": _number(analysis.get("nbstars")),
    }


def _load_table(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_table(path: str, table: Mapping[str, Any], warn: Optional[Callable[[str], Any]]) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(table, separators=(",", ":")))
    except OSError as exc:  # a cache that cannot be written costs a re-measurement, nothing more
        if warn is not None:
            warn(f"could not save the master measurements: {exc}")


def master_quality(ctx_or_ops: Any, paths: Any, cache_dir: Any,
                   warn: Optional[Callable[[str], Any]] = None) -> dict[str, dict[str, Any]]:
    """The quality record of each master path: ``{"fwhm", "eccentricity", "noise", "stars"}``, keyed by path.

    Answers from ``<cache_dir>/master-quality.json`` when the key (``master_quality_key``) is there; otherwise
    ``siril_ops.analyse`` measures it and the result is stored. A path that cannot be measured is left out of the
    result (``warn`` is told why, when given). ``ctx_or_ops`` is a run context or a SirilOps.
    """
    ops = getattr(ctx_or_ops, "ops", ctx_or_ops)
    table_path = os.path.join(os.fspath(cache_dir), MASTER_QUALITY_FILE)
    table = _load_table(table_path)
    out: dict[str, dict[str, Any]] = {}
    changed = False
    for raw in paths:
        path = os.fspath(raw)
        key = master_quality_key(path)
        cached = table.get(key)
        if isinstance(cached, dict):
            out[path] = dict(cached)
            continue
        try:
            quality = _quality_from_analysis(ops.analyse(path))
        except Exception as exc:  # one master that cannot be measured must not stop the list
            if warn is not None:
                warn(f"could not measure {path}: {exc}")
            continue
        if quality is None:
            if warn is not None:
                warn(f"could not measure {path}: Siril gave no FWHM")
            continue
        table[key] = quality
        out[path] = dict(quality)
        changed = True
    if changed:
        _save_table(table_path, table, warn)
    return out


# Former private names, kept for modules written against them.
_has_astrometric_solution = has_astrometric_solution
_solve_runner = solve_runner
