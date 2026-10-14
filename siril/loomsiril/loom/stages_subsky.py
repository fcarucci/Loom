"""The gradient correction of the mgc slot when no gradient tool is chosen: Siril's background extraction.

With the gradient tool set to none, each broadband channel (L, R, G, B) runs
``subsky 2 -samples=20 -tolerance=1.0`` after its solve, in the place of the MGC stage. Narrowband channels (H, S, O)
never get it, with or without the narrowband GraXpert option. The parameters are the stage's cache key, so a
different gradient setting never reuses a result computed with another one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Optional

from . import levels
from .siril_ops import SirilOps
from .stagekit import run_siril_stage

__all__ = ["BROADBAND_CHANNELS", "mgc_params", "run_mgc"]

#: The channels that run the stage (narrowband H, S and O never do).
BROADBAND_CHANNELS: tuple[str, ...] = ("L", "R", "G", "B")

_SUBSKY_DEGREE = 2
_SUBSKY_SAMPLES = 20
_SUBSKY_TOLERANCE = 1.0


def mgc_params(config: Optional[Mapping[str, Any]], channel: str) -> Optional[dict[str, Any]]:
    """The mgc stage's parameters for ``channel``, or None when the stage is absent for it.

    Present only for a broadband channel while the gradient tool is none. The key order is the one the cache keys
    are built from.
    """
    if channel not in BROADBAND_CHANNELS:
        return None
    if levels.gradient_tool_of(config) != levels.GRADIENT_TOOL_NONE:
        return None
    return {
        "tool": "subsky",
        "model": "poly",
        "degree": _SUBSKY_DEGREE,
        "samples": _SUBSKY_SAMPLES,
        "tolerance": _SUBSKY_TOLERANCE,
    }


def run_mgc(ops: SirilOps, in_path: Any, out_path: Any, params: Mapping[str, Any],
            log: Optional[Callable[[str], Any]] = None, label: str = "image") -> str:
    """Run the background extraction on ``in_path`` and save the result to ``out_path``; returns the saved path.

    ``params`` is what :func:`mgc_params` returned. ``label`` names the plate or channel in the failure message when
    the image is unchanged (``stagekit.StageFailed``).
    """
    degree = int(params["degree"])
    samples = int(params["samples"])
    tolerance = float(params["tolerance"])
    if log is not None:
        log(f"mgc: subsky {degree} -samples={samples} -tolerance={tolerance!r}")
    return run_siril_stage(
        ops, in_path, out_path,
        lambda o: o.subsky(degree, samples, tolerance),
        guard_name=label,
    )
