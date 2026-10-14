"""GraXpert-AI background extraction, run inside Siril through the official GraXpert-AI Siril script.

The script is Siril's own scripts-repository file ``GraXpert-AI.py``; Loom never runs GraXpert's standalone command
line. The loaded image is extracted in place by a ``pyscript`` command, so the call is synchronous and needs no
exchange files. A failure inside the script does not raise in the caller (Siril only logs it), so the result is
checked with the silent-failure guard: an image whose statistics did not change was not extracted.

Readiness is checked without running anything: the script file, the model folder and the Python packages the
script imports in Siril's Python environment. Nothing is downloaded here.
"""

from __future__ import annotations

import importlib.util
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from loomsiril.loom import stagekit
from loomsiril.loom.siril_ops import SirilOps, SirilOpsError

from .resolver import Resolver
from .specs import graxpert_text_check

__all__ = ["GRAXPERT_PACKAGES", "TOOL_ID", "bge_args", "readiness", "run_bge", "script_version"]

#: The resolver's tool id of the script.
TOOL_ID = "graxpert-ai"

#: Modules the script imports in Siril's Python environment (checked by name, never imported).
GRAXPERT_PACKAGES: tuple[str, ...] = ("onnxruntime", "astropy", "appdirs", "cv2", "PyQt6")

def bge_args(smoothing: float) -> list[str]:
    """The arguments of the script's background-extraction run: ``-bge -correction=subtraction -smoothing=<%.2f>``.

    No ``-model`` (the latest model is used), no ``-gpu`` (background extraction has no GPU flag) and no ``-keep_bg``.
    """
    value = float(smoothing)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"smoothing must be in [0, 1], got {smoothing!r}")
    return ["-bge", "-correction=subtraction", f"-smoothing={value:.2f}"]


def run_bge(ops: SirilOps, script_path: Any, in_fits: Any, out_fits: Any, smoothing: float, label: str,
            log: Optional[Callable[[str], Any]] = None) -> str:
    """Extract the background of ``in_fits`` with the GraXpert-AI script and save the result to ``out_fits``.

    Returns the saved path. ``label`` names the image in the messages. Raises ``stagekit.StageFailed`` with the
    GraXpert wording when the script reports a failure through Siril or leaves the image unchanged.
    """
    args = bge_args(smoothing)
    if log is not None:
        log(f"{label}: GraXpert-AI {' '.join(args)}")
    ops.load(in_fits)
    before = stagekit.image_signature(ops.pixels())
    try:
        ops.pyscript(script_path, args)
    except SirilOpsError as exc:
        raise stagekit.StageFailed(
            stagekit.unchanged_problem(label, False, before, before, tool="GraXpert")) from exc
    after = stagekit.image_signature(ops.pixels())
    problem = stagekit.unchanged_problem(label, True, before, after, tool="GraXpert")
    if problem is not None:
        raise stagekit.StageFailed(problem)
    saved = ops.save(out_fits)
    if not os.path.isfile(os.fspath(saved)):
        raise stagekit.StageFailed(f"GraXpert did not write {label}: {saved} is missing after the save")
    return saved


def script_version(path: Any) -> Optional[str]:
    """The script's ``VERSION`` string, read as text (the script is never executed), or None when it has none."""
    try:
        text = Path(os.fspath(path)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    verdict = graxpert_text_check(text)
    return verdict.version if verdict.passed else None


def readiness(resolver: Resolver, find_spec: Callable[[str], Any] = importlib.util.find_spec) -> list[str]:
    """What stops GraXpert-AI from running, one line per problem; an empty list means it is ready.

    Checks, in order: the script was found (and passes its text check), the background-extraction model folder
    holds a model, and the packages the script imports are importable in the current interpreter (the Loom script
    runs in Siril's Python environment). Nothing is installed or downloaded.
    """
    problems: list[str] = []
    if resolver.resolve(TOOL_ID) is None:
        reason = resolver.rejection(TOOL_ID)
        detail = f": {reason}" if reason else ""
        problems.append(f"GraXpert-AI script not found{detail}; install it from Siril's scripts repository")
    folder, has_model = resolver.graxpert_model()
    if not has_model:
        where = f" in {folder}" if folder else ""
        problems.append(
            f"GraXpert model missing{where}: run GraXpert once (or the script's model manager) to download the "
            "background-extraction model")
    missing = [name for name in GRAXPERT_PACKAGES if _missing(name, find_spec)]
    if missing:
        problems.append(
            f"Python packages missing in Siril's Python environment: {', '.join(missing)}; the first run of the "
            "script in Siril's GUI installs them")
    return problems


def _missing(name: str, find_spec: Callable[[str], Any]) -> bool:
    try:
        return find_spec(name) is None
    except (ImportError, ValueError):
        return True

