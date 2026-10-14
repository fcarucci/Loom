"""The finishing tail of a composite and of the L plate: sharpen, star extraction, linear denoise, stretch, denoise.

``levels.finishing_params`` decides which stages a composite gets and with what parameters; this module provides the
RUNNERS of those stages, one function per stage name, each ``fn(in_path, out_path) -> runctx.StageOutput``. The order
of the stages is the order of :func:`finishing_runners`' result (sharpen, extract, linear denoise, stretch, denoise).

A failed sharpen, stretch or denoise is not fatal: the plate is kept as the stage found it (the output is a copy of
the input) and the stage is flagged ``skip_cache`` so that it is not stored. A failed star extraction fails the run.
A cancel (:class:`loomsiril.loom.tools.runner.ToolCancelled`) always propagates.

Port of ``lib/Pipeline.js`` finishingStages (runners half), ``lib/Steps.js`` star reduction, detail sharpening, star
extraction, denoise, stretch and frequency separation. Importing this module has no side effects.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from typing import Any, Optional

import numpy as np

from loomsiril.io_fits import read_fits, write_fits

from . import ghs, imageops, levels, stretch
from .stages_geometry import measure_psf_sigma
from .cache import STAGE_COMPANIONS
from .runctx import RunContext, StageOutput
from .siril_ops import SirilOps
from .stagekit import StageFailed, run_siril_stage
from .tools import rcastro, starnet, syqon_legacy, syqon_studio
from .tools.runner import ToolCancelled

__all__ = ["finishing_runners", "separate_l_stars"]

#: Runner signature of one finishing stage.
Runner = Callable[[str, str], StageOutput]

# ---- paths and failure handling ---------------------------------------------------------------------------------------


def _sibling(path: str, suffix: str) -> str:
    """``<dir>/<name><suffix>.fits`` next to ``path`` (the work files and companions of a stage)."""
    folder = os.path.dirname(os.fspath(path))
    base = os.path.splitext(os.path.basename(os.fspath(path)))[0]
    return os.path.join(folder, base + suffix + ".fits")


def _kept(in_path: str, out_path: str, companions: Optional[dict[str, str]] = None) -> StageOutput:
    """The plate as the stage found it, not to be cached (Loom's SKIP_CACHE)."""
    shutil.copyfile(os.fspath(in_path), os.fspath(out_path))
    return StageOutput(path=os.fspath(out_path), companions=dict(companions or {}), skip_cache=True)


def _failure_text(label: str, failed: str, kept: str, error: BaseException, noun: Optional[str]) -> str:
    """Loom's keptAfter message: ``<label> could not be <failed> (<e>); the <noun> is kept <kept>``."""
    subject = ("the " + noun) if noun else "it"
    return f"{label} could not be {failed} ({error}); {subject} is kept {kept}"


# ---- tool adapters ----------------------------------------------------------------------------------------------------


def _rcastro(ctx: RunContext) -> rcastro.RcAstro:
    return rcastro.RcAstro(ctx.tool("rcastro"), ctx.tool_context(rcastro.ToolContext))


def _studio(ctx: RunContext) -> syqon_studio.SyqonStudio:
    return syqon_studio.SyqonStudio(ctx.tool("syqon-cli"), ctx.tool_context(syqon_studio.ToolContext),
                                    ctx.tool_store)


def _legacy_context(ctx: RunContext) -> syqon_legacy.ToolContext:
    return ctx.tool_context(syqon_legacy.ToolContext)


def _star_removal(ctx: RunContext, tool: str, src: str, dst: str, studio_domain: str) -> None:
    """Remove the stars of ``src`` with the chosen tool; the starless plate goes to ``dst``."""
    if tool == levels.STAR_TOOL_STARNET:
        starnet.remove_stars(ctx.tool("starnet2"), ctx.tool_context(starnet.ToolContext), src, dst,
                             ctx.starnet_revision)
    elif tool == levels.STAR_TOOL_SXT:
        _rcastro(ctx).remove_stars(src, dst)
    elif tool == levels.STAR_TOOL_SYQON:
        syqon_legacy.starless(ctx.tool("SyQonStarless"), _legacy_context(ctx), src, dst)
    elif tool == levels.STAR_TOOL_STUDIO:
        _studio(ctx).axiom(studio_domain, {}, src, dst)
    else:
        raise ValueError(f"unknown star extraction tool: {tool!r}")


# ---- sharpen ----------------------------------------------------------------------------------------------------------


def _sharpen_steps(ctx: RunContext, p: Mapping[str, Any]) -> list[Callable[[str, str], None]]:
    """The calls one sharpen entry makes, in order: BXT's stars and detail in ONE pass; other tools one call each."""
    tool = p["tool"]
    stars = p.get("starsAmount")
    detail = p.get("detailAmount")
    family = p.get("family") or levels.STUDIO_PARALLAX_FAMILY
    if tool == levels.SHARPEN_TOOL_BXT:
        stars_amount = float(stars) if stars is not None else 0.0
        detail_amount = float(detail) if detail is not None else 0.0
        return [lambda src, dst: _rcastro(ctx).sharpen(src, dst, stars_amount, detail_amount)]

    steps: list[Callable[[str, str], None]] = []
    if tool == levels.SHARPEN_TOOL_SYQON:
        if stars is not None and float(stars) > 0:
            steps.append(lambda src, dst: syqon_legacy.parallax(
                ctx.tool("parallax_cli"), _legacy_context(ctx), src, dst, "star_reduction", float(stars), True))
        if detail is not None and float(detail) > 0:
            steps.append(lambda src, dst: syqon_legacy.parallax(
                ctx.tool("parallax_cli"), _legacy_context(ctx), src, dst, "sharpen", float(detail), True))
        return steps
    if tool == levels.SHARPEN_TOOL_STUDIO:
        if stars is not None and float(stars) > 0:
            steps.append(lambda src, dst: _studio(ctx).parallax(
                "linear", {"parallax": {"reduction": stars, "family": family}}, src, dst))
        if detail is not None and float(detail) > 0:
            steps.append(lambda src, dst: _studio(ctx).parallax(
                "linear", {"parallax": {"deblur": detail, "family": family}}, src, dst))
        return steps
    raise ValueError(f"sharpening is not implemented for {tool!r}")


def _sharpen_runner(ctx: RunContext, label: str, entry: Mapping[str, Any], noun: Optional[str]) -> Runner:
    def run(in_path: str, out_path: str) -> StageOutput:
        steps = _sharpen_steps(ctx, entry)
        current = os.fspath(in_path)
        for i, step in enumerate(steps):
            target = os.fspath(out_path) if i == len(steps) - 1 else _sibling(out_path, f"_step{i}")
            try:
                step(current, target)
            except ToolCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - a non-fatal stage keeps the plate as the calls left it
                ctx.warn(_failure_text(label, "corrected", "as it is", exc, noun))
                # Loom keeps the view as the last call left it: the star-reduced plate when detail failed
                return _kept(current, out_path)
            current = target
        return StageOutput(path=os.fspath(out_path))

    return run


# ---- star extraction --------------------------------------------------------------------------------------------------


def _mtf_apply(ops: SirilOps, params: Mapping[str, Any], linked: bool, n_channels: int) -> None:
    """Apply the MTF in Siril, one ``mtf`` command at a time (the commands of stretch.mtf_commands)."""
    for command in stretch.mtf_commands(dict(params), linked, n_channels):
        channel = command[4] if len(command) > 4 else None
        ops.mtf(float(command[1]), float(command[2]), channel)


def _extract_runner(ctx: RunContext, label: str, entry: Mapping[str, Any], config: Mapping[str, Any]) -> Runner:
    tool = entry["tool"]
    stretch_stars = bool(config.get("stretch"))

    def run(in_path: str, out_path: str) -> StageOutput:
        ctx.log(f"extracting stars from {label}")
        planes, header = read_fits(in_path)
        linked = len(planes) >= 3
        # Stars plate: a copy of the composite, stretched for the star tool (MTF always, also for the GHS method),
        # starless with the tool, then the stars are what the screen blend took away (unscreen).
        stars_copy = _sibling(out_path, "_stars_src")
        if stretch_stars:
            ctx.ops.load(in_path)
            params = stretch.stretch_parameters_for_planes(planes, linked, stretch.STRETCH_STARS_TARGET, ctx.warn)
            _mtf_apply(ctx.ops, params, linked, len(planes))
            ctx.ops.save(stars_copy)
        else:
            shutil.copyfile(in_path, stars_copy)
        stars_starless = _sibling(out_path, "_stars_starless")
        _star_removal(ctx, tool, stars_copy, stars_starless,
                      studio_domain="nonlinear" if stretch_stars else "linear")
        before, _ = read_fits(stars_copy)
        starless_stars, _ = read_fits(stars_starless)
        stars_planes = [np.clip(imageops.unscreen_stars(a, b), 0.0, 1.0)
                        for a, b in zip(before, starless_stars)]
        stars_path = _sibling(out_path, "_stars")
        write_fits(stars_path, stars_planes, header=header, bitpix=-32)
        # Starless plate: the tool on the untouched linear composite
        _star_removal(ctx, tool, in_path, out_path, studio_domain="linear")
        return StageOutput(path=os.fspath(out_path), companions={"stars": stars_path})

    return run


# ---- linear and stretched denoise -------------------------------------------------------------------------------------


def _denoise_dispatch(ctx: RunContext, entry: Mapping[str, Any], in_path: str, out_path: str,
                      already_stretched: bool) -> None:
    tool = entry["tool"]
    amount = entry.get("amount")
    if tool == levels.NR_TOOL_NXT:
        denoise, detail = amount
        _rcastro(ctx).denoise(in_path, out_path, float(denoise), float(detail))
    elif tool == levels.NR_TOOL_PRISM:
        syqon_legacy.prism(ctx.tool("prism_cli"), _legacy_context(ctx), in_path, out_path, float(amount),
                           already_stretched)
    elif tool == levels.NR_TOOL_STUDIO:
        _studio(ctx).run("prism-essential", syqon_studio.domain_for("prism-essential", stretched=already_stretched),
                         {"application": float(amount)}, in_path, out_path)
    elif tool == levels.NR_TOOL_STUDIO2:
        if not isinstance(amount, Mapping):
            raise ValueError("no Prism 2.0 pass for this strength")
        if amount.get("fineScale") is not None:
            raise ValueError("Prism 2.0's fineScale is not supported by the SyQon Studio adapter")
        model = amount["model"]
        _studio(ctx).run(model, syqon_studio.domain_for(model, stretched=already_stretched),
                         {"application": float(amount["application"])}, in_path, out_path)
    elif tool == levels.NR_TOOL_SIRIL:
        mod = float(amount)
        run_siril_stage(ctx.ops, in_path, out_path, lambda ops: ops.denoise(mod))
    else:
        raise ValueError(f"unknown noise reduction tool: {tool!r}")


def _denoise_runner(ctx: RunContext, label: str, entry: Mapping[str, Any], already_stretched: bool,
                    noun: Optional[str]) -> Runner:
    def run(in_path: str, out_path: str) -> StageOutput:
        try:
            _denoise_dispatch(ctx, entry, in_path, out_path, already_stretched)
        except ToolCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - a non-fatal stage keeps the plate
            ctx.warn(_failure_text(label, "denoised", "as it is", exc, noun))
            return _kept(in_path, out_path)
        return StageOutput(path=os.fspath(out_path))

    return run


# ---- stretch ----------------------------------------------------------------------------------------------------------


def _stretch_runner(ctx: RunContext, label: str, stage: str, entry: Mapping[str, Any], linked: bool,
                    config: Mapping[str, Any], noun: Optional[str]) -> Runner:
    keep = bool(config.get("keepLinear")) and STAGE_COMPANIONS.get(stage) == "linear"

    def run(in_path: str, out_path: str) -> StageOutput:
        ctx.log(f"stretching {label}")
        companions: dict[str, str] = {}
        if keep:
            # the stretch overwrites its input in Siril, so the linear version is taken first; a failure costs the
            # copy only, and the stretch proceeds
            linear = _sibling(out_path, "_linear")
            try:
                shutil.copyfile(in_path, linear)
                companions["linear"] = linear
            except OSError as exc:
                ctx.warn(f"could not keep the linear copy ({exc}); the stretch proceeds")
        try:
            if entry.get("method") == levels.STRETCH_METHOD_GHS:
                ghs.run_ghs_stretch(ctx.ops, in_path, out_path, entry, linked, keep_linear=False, log=ctx.log)
            else:
                planes, _ = read_fits(in_path)
                params = stretch.stretch_parameters_for_planes(planes, linked, entry["target"], ctx.warn)
                ctx.ops.load(in_path)
                _mtf_apply(ctx.ops, params, linked, len(planes))
                ctx.ops.save(out_path)
        except ToolCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - a non-fatal stage keeps the plate linear
            ctx.warn(_failure_text(label, "stretched", "linear", exc, noun))
            return _kept(in_path, out_path, companions)
        return StageOutput(path=os.fspath(out_path), companions=companions)

    return run


# ---- public API -------------------------------------------------------------------------------------------------------


def finishing_runners(ctx: RunContext, label: str, names: Mapping[str, Any], config: Mapping[str, Any], *,
                      noun: Optional[str] = None) -> dict[str, Runner]:
    """The runners of the finishing tail, keyed by stage name, for exactly the stages ``levels.finishing_params``
    returns for ``names`` and ``config`` (in the same order).

    ``label`` names the plate in the log and warnings; ``noun`` ("composite", "palette", or None for the L plate)
    is what Loom's warnings call the plate (``the <noun> is kept ...``).
    """
    params = levels.finishing_params(names, config)
    linked = not names.get("unlinked")
    stretched_slot = bool(config.get("stretch"))
    runners: dict[str, Runner] = {}
    for role in ("sharpen", "extract", "denoiseLinear", "stretch", "denoise"):
        stage = names.get(role)
        if not stage or stage not in params:
            continue
        entry = params[stage]
        if role == "sharpen":
            runners[stage] = _sharpen_runner(ctx, label, entry, noun)
        elif role == "extract":
            runners[stage] = _extract_runner(ctx, label, entry, config)
        elif role == "denoiseLinear":
            runners[stage] = _denoise_runner(ctx, label, entry, False, noun)
        elif role == "stretch":
            runners[stage] = _stretch_runner(ctx, label, stage, entry, linked, config, noun)
        else:
            runners[stage] = _denoise_runner(ctx, label, entry, stretched_slot, noun)
    return runners


def separate_l_stars(ctx: RunContext, l_stars_path: str, work_dir: str) -> tuple[str, str]:
    """Frequency separation of the L stars plate: returns ``(low_path, high_path)`` written under ``work_dir``.

    The blur sigma is ``imageops.FS_SIGMA_FACTOR`` times the plate's measured PSF sigma. Raises
    :class:`loomsiril.loom.stagekit.StageFailed` when no star can be measured; the caller keeps the plate and warns,
    as Loom does (lib/Pipeline.js separateLStars).
    """
    psf = measure_psf_sigma(ctx.ops, os.fspath(l_stars_path), ctx.log)
    if psf is None or not psf > 0:
        raise StageFailed("frequency separation: could not measure the stars on the L stars plate, so there is no "
                          "radius to separate at")
    sigma = psf * imageops.FS_SIGMA_FACTOR
    planes, header = read_fits(l_stars_path)
    lows: list[np.ndarray] = []
    highs: list[np.ndarray] = []
    for plane in planes:
        low, high = imageops.frequency_separate(plane, sigma)
        lows.append(low.astype(np.float32))
        highs.append(high.astype(np.float32))
    low_path = os.path.join(os.fspath(work_dir), "L_stars_low.fits")
    high_path = os.path.join(os.fspath(work_dir), "L_stars_high.fits")
    write_fits(low_path, lows, header=header, bitpix=-32)
    write_fits(high_path, highs, header=header, bitpix=-32)
    return low_path, high_path
