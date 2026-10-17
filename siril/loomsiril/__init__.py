"""Loom Fly-Through for Siril: a Python port of Loom's Fly-Through (PixInsight).

The pure core (this package) needs only numpy and tifffile and runs under any
Python 3.10+. The Siril script in ``siril/`` is a thin layer over it.

Entry points: ``loomsiril.pipeline.run(FlyJob(...))`` (also exported here) and the
command line ``python -m loomsiril``. The modules (geometry, parallax, presets, encode,
colour, gaia, wcs, io_fits, io_tiff, detect, assign, starmodel, starfit, starlayer,
resample, bloom, scene, sprites, frame, pipeline, cli, siril_bridge) are imported by
name, e.g. ``from loomsiril import geometry``.
"""

from __future__ import annotations

import pkgutil
from importlib import import_module
from typing import Any

__version__ = "0.5.3"

from loomsiril.core import (
    RAD,
    Point,
    Rect,
    clamp,
    detection_rect,
    dot,
    grown_rect,
    js_round,
    mad,
    median,
    radec,
    screen,
    separation,
    smoothstep,
    square_rect,
    tan_project,
    tan_unproject,
    unscreen,
    vec,
)

from loomsiril.types import (
    Detection,
    Halo,
    Placed,
    Scene,
    SceneSprite,
    Spike,
    SpikeReach,
    Sprite,
    SpriteMatch,
    StarModel,
)

#: The names re-exported from loomsiril.pipeline, which is imported on first use (PEP 562, see __getattr__):
#: pipeline needs core and the maths layers, so an eager import here closed an import cycle.
_PIPELINE_EXPORTS: frozenset[str] = frozenset({"FlyJob", "Result", "run"})

#: The package's submodules (core, pipeline, geometry, ...), each imported on first attribute access.
_SUBMODULES: frozenset[str] = frozenset(
    name for _, name, _ in pkgutil.iter_modules(__path__) if not name.startswith("_")
)

__all__ = [
    "__version__",
    "FlyJob",
    "Result",
    "run",
    "Detection",
    "Halo",
    "Placed",
    "Scene",
    "SceneSprite",
    "Spike",
    "SpikeReach",
    "Sprite",
    "SpriteMatch",
    "StarModel",
    "RAD",
    "Point",
    "Rect",
    "clamp",
    "detection_rect",
    "dot",
    "grown_rect",
    "js_round",
    "mad",
    "median",
    "radec",
    "screen",
    "separation",
    "smoothstep",
    "square_rect",
    "tan_project",
    "tan_unproject",
    "unscreen",
    "vec",
]


def __getattr__(name: str) -> Any:
    """PEP 562: ``loomsiril.run``, ``loomsiril.FlyJob`` and ``loomsiril.Result`` come from loomsiril.pipeline,
    and ``loomsiril.pipeline``, ``loomsiril.geometry``, ... are imported on first access (they are submodules)."""
    if name in _PIPELINE_EXPORTS:
        return getattr(import_module("loomsiril.pipeline"), name)
    if name in _SUBMODULES:
        return import_module("loomsiril." + name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """The module's names (its dunders included), the public names and the submodules, so ``dir(loomsiril)`` lists
    them before any of them is imported."""
    return sorted(set(globals()) | set(__all__) | _SUBMODULES)
