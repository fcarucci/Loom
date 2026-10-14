"""Adapters for the standalone SyQon command-line tools: SyQon Starless, prism_cli and parallax_cli.

Port of Loom's command lines and stage flow for the three standalone binaries:

* ``build_args`` is the port of ``Steps.syqonBuildArgs`` (lib/StepsSyqon.js:293-338) for parallax_cli, and the
  inline argument lists of ``Steps.prismExecuteStage`` (lib/StepsSyqon.js:1146-1227) and
  ``Steps.syqonStarlessRun`` (lib/StepsSyqon.js:1303-1417).
* ``starless`` exchanges float TIFF with SyQon Starless, which stretches internally, so no temporary stretch is
  made. ``prism`` and ``parallax`` exchange float FITS and wrap the tool in Loom's temporary stretch
  (:mod:`loomsiril.loom.tools.tempstretch`), forward before the tool and the exact inverse after it.

Each adapter receives an already resolved executable path; it never searches for a tool and never runs anything
else. A non-zero exit that left an output file is accepted (and logged by the runner); no output, or an output
that does not load, raises :class:`ToolNoOutput` with Loom's wording. Every run is a ``run_tool`` call with
``accept_nonzero=True`` and the 20 minute timeout of Loom (lib/StepsSyqon.js:232-237).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.tools.exchange import (
    read_exchange_fits,
    read_exchange_tiff,
    write_exchange_fits,
    write_exchange_tiff,
)
from loomsiril.loom.tools.resolver import pathmod
from loomsiril.loom.tools.runner import RunResult, ToolError, exchange_folder, run_tool
from loomsiril.loom.tools.tempstretch import (
    PRISM_DEFAULT_TARGET,
    SYQON_MTF_TARGET,
    SYQON_OVERLAP,
    SYQON_PAD,
    SYQON_TILE_SIZE,
    SYQON_TIMEOUT_MS,
    prism_mtf_target,
    stretch_forward,
    stretch_inverse,
)

__all__ = [
    "ToolContext",
    "ToolNoOutput",
    "ModelNotFound",
    "STARLESS_MODEL_NAME",
    "PARALLAX_TIMEOUT_SECONDS",
    "build_args",
    "starless",
    "starless_model_candidates",
    "starless_model_path",
    "starless_no_output_message",
    "prism",
    "parallax",
]

STARLESS_TOOL = "SyQonStarless"
PRISM_TOOL = "prism_cli"
PARALLAX_TOOL = "parallax_cli"

STARLESS_MODEL_NAME = "axiom3.mlmodelc"
_STARLESS_MODEL_FALLBACK = "/Applications/SyQonStarless.app/Contents/Resources/axiom3.mlmodelc"

# lib/StepsSyqon.js:232-237: the same timeout for every SyQon run, in seconds for run_tool.
PARALLAX_TIMEOUT_SECONDS = SYQON_TIMEOUT_MS / 1000.0

# Loom's operation labels (lib/Steps.js:2520, 2594, 2640), used in the failure messages.
_PARALLAX_LABELS = {
    "aberration": "aberration correction",
    "star_reduction": "star reduction",
    "sharpen": "detail sharpening",
}

# Prism's fixed arguments (lib/StepsSyqon.js:1200-1205).
_PRISM_MODEL_KIND = "prism_deep"
_PRISM_FP16 = ("--use-amp", "--amp-dtype", "fp16")


class ToolNoOutput(ToolError):
    """The tool left no output file that loads; the message is Loom's wording for the stage."""


class ModelNotFound(ToolError):
    """The SyQon Starless model is not beside the executable or in the app bundle (lib/StepsSyqon.js:1262-1275)."""


@dataclass(frozen=True)
class ToolContext:
    """What every tool adapter needs from the pipeline: the work folder, logging, cancel, progress and platform."""

    work_dir: str | os.PathLike[str]
    log: Callable[[str], None] | None
    cancelled: Callable[[], bool]
    on_progress: Callable[[float, str], None] | None
    platform: str


# --- argument lists -----------------------------------------------------------------------------------------------


def _format_2f(value: float) -> str:
    """PixInsight's ``format("%.2f", v)``."""
    return f"{float(value):.2f}"


def _syqon_build_args(opts: Mapping[str, Any]) -> list[str]:
    """Port of Steps.syqonBuildArgs (lib/StepsSyqon.js:293-338) for parallax_cli."""
    args = ["--i", str(opts["inputFilePath"]), "--o", str(opts["outputFilePath"])]
    # mode is pinned to "classic" and never emitted (lib/StepsSyqon.js:302-304)
    if opts.get("correctAberration"):
        args.append("--correct-aberration")
    star = opts.get("starReduction") or 0
    if star > 0:
        args += ["--star-reduction", str(int(star))]
    sharpen = opts.get("sharpen") or 0.0
    if sharpen > 0.0:
        args += ["--sharpen", _format_2f(sharpen)]
    args += ["--tile", str(opts["tileSize"]), "--overlap", str(opts["overlap"]), "--pad", str(opts["pad"])]
    info = opts.get("jsonInfoPath")
    if info:
        args += ["--json-info", str(info)]
    return args


def build_args(
    tool: str,
    operation: str,
    settings: Mapping[str, Any],
    in_path: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    extra: Mapping[str, Any] | None = None,
) -> list[str]:
    """The argument list (without the executable) for one run of a legacy SyQon tool.

    ``tool`` is ``"parallax"``, ``"prism"`` or ``"starless"``.

    * parallax: ``operation`` is ``"aberration"``, ``"star_reduction"`` or ``"sharpen"``; ``settings["amount"]`` is
      the star-reduction count or the sharpen alpha (ignored for aberration); ``extra["json_info"]`` is the info file.
    * prism: ``operation`` is ``"denoise"``; ``settings["strength"]`` is the strength (printed with two decimals).
    * starless: ``operation`` is ``"extract"``; ``extra["model"]`` is the model path. ``-d Auto`` is always passed:
      the default device crashes headless (lib/StepsSyqon.js:1310-1322).
    """
    extra = extra or {}
    if tool == "parallax":
        if operation not in _PARALLAX_LABELS:
            raise ValueError(f"unknown parallax operation {operation!r}")
        amount = settings.get("amount", 0)
        opts = {
            "inputFilePath": in_path,
            "outputFilePath": out_path,
            "jsonInfoPath": extra.get("json_info"),
            "correctAberration": operation == "aberration",
            "starReduction": amount if operation == "star_reduction" else 0,
            "sharpen": amount if operation == "sharpen" else 0.0,
            "tileSize": SYQON_TILE_SIZE,
            "overlap": SYQON_OVERLAP,
            "pad": SYQON_PAD,
        }
        return _syqon_build_args(opts)
    if tool == "prism":
        if operation != "denoise":
            raise ValueError(f"unknown prism operation {operation!r}")
        return [
            "--input", str(in_path),
            "--output", str(out_path),
            "--model-kind", _PRISM_MODEL_KIND,
            "--tile", str(SYQON_TILE_SIZE),
            "--overlap", str(SYQON_OVERLAP),
            "--pad", str(SYQON_PAD),
            "--strength", _format_2f(settings["strength"]),
            *_PRISM_FP16,
        ]
    if tool == "starless":
        if operation != "extract":
            raise ValueError(f"unknown starless operation {operation!r}")
        return [
            "-i", str(in_path),
            "-o", str(out_path),
            "-c", "pixinsight",
            "-m", str(extra["model"]),
            "-d", "Auto",
        ]
    raise ValueError(f"unknown legacy SyQon tool {tool!r}")


# --- SyQon Starless -----------------------------------------------------------------------------------------------


def starless_model_candidates(exe: str | os.PathLike[str], platform: str) -> list[str]:
    """Where the model could be, in Loom's preference order (lib/StepsSyqon.js:1245-1257)."""
    pm = pathmod(platform)
    candidates: list[str] = []
    exe_s = os.fspath(exe)
    exe_dir = pm.dirname(exe_s)  # .../Contents/MacOS
    parent = pm.dirname(exe_dir)  # .../Contents
    candidates.append(pm.join(parent, "Resources", STARLESS_MODEL_NAME))
    candidates.append(pm.join(exe_dir, STARLESS_MODEL_NAME))
    if platform != "win32":
        candidates.append(_STARLESS_MODEL_FALLBACK)
    return candidates


def starless_model_path(exe: str | os.PathLike[str], platform: str) -> str:
    """The first candidate that exists; raises :class:`ModelNotFound` when none does."""
    for candidate in starless_model_candidates(exe, platform):
        if os.path.exists(candidate):
            return candidate
    raise ModelNotFound("SyQon Starless model not found")


def starless_no_output_message(name: str, stderr: str | None) -> str:
    """Port of Steps.starlessNoOutputMessage (lib/StepsSyqon.js:1276-1280); ``stderr`` is the run's stderr text."""
    text = (stderr or "").strip()
    detail = f" stderr: {text}" if stderr else ""
    return f"SyQon Starless produced no output for {name}{detail}"


def _stderr_text(result: RunResult) -> str:
    return "\n".join(result.stderr_tail)


def _no_output_message(message: str, result: RunResult) -> str:
    """Loom's failure text: the stage message plus the run's stderr when there is any (syqonFailureDetail)."""
    stderr = _stderr_text(result).strip()
    return message + (f" stderr: {stderr}" if stderr else "")


def _load_result(loader: Callable[[], Any], message: str) -> Any:
    """Load a tool's output; an output that does not load counts as no output (the stage's wording)."""
    try:
        return loader()
    except Exception as exc:  # any unreadable result is treated as no result
        raise ToolNoOutput(f"{message} (the output could not be read: {exc})") from exc


def starless(exe: str | os.PathLike[str], ctx: ToolContext, in_fits: str | os.PathLike[str],
             out_fits: str | os.PathLike[str]) -> None:
    """SyQon Starless on a plate: the plate goes out as float TIFF and the starless result comes back."""
    planes, header = read_fits(in_fits)
    n_target = len(planes)
    name = Path(os.fspath(in_fits)).stem
    model = starless_model_path(exe, ctx.platform)
    with exchange_folder(ctx.work_dir, STARLESS_TOOL) as folder:
        in_tif = folder / "input.tif"
        out_tif = folder / "starless.tif"
        write_exchange_tiff(in_tif, planes)
        args = build_args("starless", "extract", {}, in_tif, out_tif, {"model": model})
        result = run_tool(
            STARLESS_TOOL,
            [os.fspath(exe), *args],
            cwd=folder,
            timeout=PARALLAX_TIMEOUT_SECONDS,
            cancelled=ctx.cancelled,
            on_progress=ctx.on_progress,
            log=ctx.log,
            platform=ctx.platform,
            accept_nonzero=True,
        )
        no_output = starless_no_output_message(name, _stderr_text(result))
        if not out_tif.exists():
            raise ToolNoOutput(no_output)
        # the tool returns three channels even for a mono plate: a mono plate takes channel 0
        out_planes = _load_result(lambda: read_exchange_tiff(out_tif, n_target), no_output)
        write_fits(out_fits, out_planes, header=header)


# --- Prism and Parallax (temporary stretch round trip) --------------------------------------------------------------


def prism(
    exe: str | os.PathLike[str],
    ctx: ToolContext,
    in_fits: str | os.PathLike[str],
    out_fits: str | os.PathLike[str],
    strength: float,
    stretched: bool,
) -> None:
    """prism_cli denoise on a plate (lib/StepsSyqon.js:1146-1227).

    The plate is stretched for the tool: linked for three channels, with the target 0.15 when the input is already
    stretched, else the target chosen by :func:`prism_mtf_target` (the measured one) for linear input.
    """
    planes, header = read_fits(in_fits)
    n = len(planes)
    name = Path(os.fspath(in_fits)).stem
    target = PRISM_DEFAULT_TARGET if stretched else prism_mtf_target(planes)
    forward, state = stretch_forward(planes, target, n >= 3)
    with exchange_folder(ctx.work_dir, PRISM_TOOL) as folder:
        in_path = folder / "input.fits"
        out_path = folder / "prism.fits"
        write_exchange_fits(in_path, forward, sanitize="basic")
        args = build_args("prism", "denoise", {"strength": strength}, in_path, out_path)
        result = run_tool(
            PRISM_TOOL,
            [os.fspath(exe), *args],
            cwd=folder,
            timeout=PARALLAX_TIMEOUT_SECONDS,
            cancelled=ctx.cancelled,
            on_progress=ctx.on_progress,
            log=ctx.log,
            platform=ctx.platform,
            accept_nonzero=True,
        )
        no_output = _no_output_message(f"SyQon Prism produced no output for {name}", result)
        if not out_path.exists():
            raise ToolNoOutput(no_output)
        result_planes = _load_result(lambda: read_exchange_fits(out_path, n)[0], no_output)
        write_fits(out_fits, stretch_inverse(result_planes, state), header=header)


def parallax(
    exe: str | os.PathLike[str],
    ctx: ToolContext,
    in_fits: str | os.PathLike[str],
    out_fits: str | os.PathLike[str],
    operation: str,
    amount: float,
    linked: bool,
) -> None:
    """parallax_cli on a plate (lib/StepsSyqon.js:838-870).

    ``operation`` is ``"aberration"``, ``"star_reduction"`` or ``"sharpen"``. The stretch targets 0.12; it is linked
    only for a colour plate and when the caller asks for it (aberration runs unlinked, composites linked).
    """
    if operation not in _PARALLAX_LABELS:
        raise ValueError(f"unknown parallax operation {operation!r}")
    planes, header = read_fits(in_fits)
    n = len(planes)
    name = Path(os.fspath(in_fits)).stem
    label = _PARALLAX_LABELS[operation]
    forward, state = stretch_forward(planes, SYQON_MTF_TARGET, bool(linked) and n >= 3)
    with exchange_folder(ctx.work_dir, PARALLAX_TOOL) as folder:
        in_path = folder / "input.fits"
        out_path = folder / "parallax.fits"
        info_path = folder / "parallax.json"
        write_exchange_fits(in_path, forward, sanitize="basic")
        args = build_args(
            "parallax", operation, {"amount": amount}, in_path, out_path, {"json_info": info_path}
        )
        result = run_tool(
            PARALLAX_TOOL,
            [os.fspath(exe), *args],
            cwd=folder,
            timeout=PARALLAX_TIMEOUT_SECONDS,
            cancelled=ctx.cancelled,
            on_progress=ctx.on_progress,
            log=ctx.log,
            platform=ctx.platform,
            accept_nonzero=True,
        )
        no_output = _no_output_message(
            f"SyQon Parallax {label} failed on {name}: no output file was produced.", result
        )
        if not out_path.exists():
            raise ToolNoOutput(no_output)
        result_planes = _load_result(lambda: read_exchange_fits(out_path, n)[0], no_output)
        write_fits(out_fits, stretch_inverse(result_planes, state), header=header)
