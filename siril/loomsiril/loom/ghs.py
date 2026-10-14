"""The GHS stretch stage: Siril's ``autoghs`` (the generalised hyperbolic stretch) on a composite or a mono plate.

The parameters and the command come from :mod:`levels` (``stretch_params`` for method "ghs", ``GHS_PARAMS``,
``ghs_command``): this module runs them and does not redefine them. RGB composites and palettes are stretched linked;
L and mono plates unlinked (the caller passes ``linked``).

With ``keep_linear``, the linear input is kept as the companion file: the input file is copied to ``linear_path``
before the stretch, so the copy is bit-identical to the input. Any failure (a Siril command error, or an output that
was not written) raises :class:`stagekit.StageFailed`; the caller keeps the plate linear and does not cache it.

Pure with respect to Siril: all Siril work goes through the ``ops`` object. No sirilpy or tkinter import.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from typing import Any, Optional

from . import levels
from .siril_ops import SirilOps, SirilOpsError
from .stagekit import StageFailed

__all__ = ["run_ghs_stretch"]

_METHOD = levels.STRETCH_METHOD_GHS


def run_ghs_stretch(ops: SirilOps, in_path: Any, out_path: Any, params: Optional[Mapping[str, Any]], linked: bool,
                    keep_linear: bool, linear_path: Any = None,
                    log: Optional[Callable[[str], Any]] = None) -> dict[str, Any]:
    """Stretch ``in_path`` with GHS and save the result to ``out_path``.

    ``params`` is the stage's cache-key parameters (``levels.stretch_params`` with method "ghs"); it must name the
    GHS method. ``linked`` selects Siril's ``-linked`` form. With ``keep_linear``, ``in_path`` is copied to
    ``linear_path`` before the stretch and that path is returned as ``"linear"``; otherwise ``"linear"`` is None.

    Returns ``{"out": out_path, "linear": linear_path or None}``. Raises :class:`stagekit.StageFailed` on any failure.
    """
    if params is None or params.get("method") != _METHOD:
        raise ValueError(f"run_ghs_stretch needs the {_METHOD!r} stretch parameters, got {params!r}")
    if keep_linear and not linear_path:
        raise ValueError("keep_linear needs a linear_path")
    command = levels.ghs_command(linked)
    if log is not None:
        log("ghs: " + " ".join(str(part) for part in command))
    linear_out: Optional[str] = None
    try:
        ops.load(in_path)
        if keep_linear:
            shutil.copyfile(os.fspath(in_path), os.fspath(linear_path))
            linear_out = os.fspath(linear_path)
        ops.ghs(command)
        saved = ops.save(out_path)
    except SirilOpsError as exc:
        raise StageFailed(f"GHS stretch failed on {os.fspath(in_path)}: {exc}") from exc
    except OSError as exc:
        raise StageFailed(f"GHS stretch failed on {os.fspath(in_path)}: {exc}") from exc
    if not os.path.isfile(os.fspath(saved)):
        raise StageFailed(f"GHS stretch wrote no output at {os.fspath(out_path)}")
    return {"out": out_path, "linear": linear_out}
