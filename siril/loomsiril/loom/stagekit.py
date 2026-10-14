"""Shared helpers for the stage modules: the failure type, the cache-skip marker, the image fingerprint and the
silent-failure guard around a Siril stage (Loom's ``lib/Steps.js`` and ``lib/Pipeline.js`` helpers).

Stage modules import this module read-only; it has no side effects at import and imports nothing from Siril.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Optional

import numpy as np

from loomsiril.jsmath import number_str

from .siril_ops import SirilOps

__all__ = [
    "SKIP_CACHE",
    "StageFailed",
    "format_signature",
    "image_signature",
    "run_siril_stage",
    "unchanged_problem",
]

#: A stage result that must not be cached (lib/Pipeline.js:580, ``Pipeline.SKIP_CACHE``).
SKIP_CACHE = "loom-skip-cache"


class StageFailed(RuntimeError):
    """A stage that did not produce its result. The message is written to the log as it is."""


def format_signature(mean: float, median: float, maximum: float) -> str:
    """``Steps.imageSignature``'s text: the three statistics joined by ``|``, each as a JavaScript number string."""
    return "|".join(number_str(float(v)) for v in (mean, median, maximum))


def image_signature(planes: Sequence[Any]) -> str:
    """A cheap fingerprint of an image (lib/Steps.js:1322-1326): mean, median and maximum over ALL planes together.

    The statistics are taken in float64 over every sample of every plane, so a background extraction that keeps
    the mean (it does) still changes the median or the maximum.
    """
    if len(planes) == 0:
        raise ValueError("image_signature needs at least one plane")
    samples = np.concatenate([np.asarray(p, dtype=np.float64).ravel() for p in planes])
    return format_signature(float(samples.mean()), float(np.median(samples)), float(samples.max()))


def unchanged_problem(name: str, ran: bool, before: str, after: str, tool: str = "GraXpert") -> Optional[str]:
    """``Steps.graxpertProblem`` with the tool name substituted: why a run is a failure, or None when it is not.

    ``before`` and ``after`` are :func:`image_signature` strings; ``ran`` says whether the tool reported success.
    Extraction always moves pixels, so an unchanged image means the tool did nothing without saying so.
    """
    if not ran:
        return (f"{tool} failed on {name}: the process reported failure (its own output, with the reason, "
                "is in the console just above)")
    if before == after:
        return (f"{tool} did not change {name}: it reported success but the image is identical to the one it was "
                "given, so the background was not extracted (its own output is in the console just above)")
    return None


def run_siril_stage(ops: SirilOps, in_path: Any, out_path: Any, apply: Callable[[SirilOps], Any],
                    guard_name: Optional[str] = None) -> str:
    """Load ``in_path``, run ``apply(ops)`` on the image, save it to ``out_path`` (a ``.fits`` path) and return the
    saved path.

    With ``guard_name`` (the plate or channel label), the image fingerprint is taken before and after ``apply``; an
    unchanged image raises :class:`StageFailed` so that a command which silently did nothing cannot pass as a result.
    """
    ops.load(in_path)
    before = image_signature(ops.pixels()) if guard_name else ""
    apply(ops)
    if guard_name:
        after = image_signature(ops.pixels())
        if before == after:
            raise StageFailed(f"{guard_name} did not change the image: Siril reported success but the image is "
                              "identical to the one it was given (its own output is in the console just above)")
    return ops.save(out_path)
