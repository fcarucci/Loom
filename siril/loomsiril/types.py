"""The records modules pass to each other (frozen interface, owned by E0).

The JS passes plain objects around; these dataclasses give them names and
fields so the tasks that produce them (E3a detection/assignment, E3b star
models and deblending) and the ones that consume them (E4 scene transforms,
E5 sprite drawing, E6 pipeline) can be written in parallel.

Rules:

* Field names are the JS names in snake_case (``fromGaia`` -> ``from_gaia``,
  ``modelCore`` -> ``model_core``, ``pxScale`` -> ``px_scale``).
* Images and patches are numpy arrays shaped ``(rows, cols)`` = ``(h, w)``,
  one array per channel, float32 unless stated. The JS flat index
  ``i = y*w + x`` is ``plane[y, x]`` here.
* Angles of spikes are RADIANS (as ``Sky.spikeDirections`` returns them).
* Where the JS copies a record with ``Object.assign({}, s, {...})``, use
  ``dataclasses.replace(s, ...)``. Where it hangs caches on an object
  (``sc._scaled``, ``sc.turned``, ``s._split``, ``s._glow``), use the
  ``cache`` dict.
* Gaia source records stay plain dicts ``{ra, dec, plx, pmra, pmdec, G, BP, RP}``
  (NaN for blanks), exactly as ``Fly.parseGaiaCsv`` returns them.

A change that needs a field this file lacks adds it here in the same commit, with its test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from loomsiril.core import Point, Rect

__all__ = [
    "GaiaSource",
    "Projector",
    "Unprojector",
    "Placed",
    "SpikeReach",
    "Halo",
    "Spike",
    "StarModel",
    "Detection",
    "SpriteMatch",
    "Sprite",
    "SceneSprite",
    "Scene",
]

#: One Gaia record: keys ra, dec, plx, pmra, pmdec, G, BP, RP (float, NaN for blanks).
GaiaSource = dict

#: ``(ra, dec) -> Point`` in Fly-Through's pixel convention, or None off the projection.
Projector = Callable[[float, float], Optional[Point]]

#: ``(x, y) -> (ra, dec)`` degrees, the inverse of a Projector.
Unprojector = Callable[[float, float], tuple]


@dataclass
class Placed:
    """``Fly.placeStars`` output: a Gaia source with a usable parallax, inside the image.

    ``d`` is the distance in parsecs (1000 / parallax in mas); x, y its projected pixel.
    """

    source: GaiaSource
    d: float
    x: float
    y: float


@dataclass
class SpikeReach:
    """A spike as a halo records it (``d.halo.spikes``): direction (radians) and reach (px)."""

    angle: float
    reach: float


@dataclass
class Halo:
    """``d.halo`` set by ``Sky.starModel``: how far a detection's light reaches.

    ``halo`` may be None (then ``radius`` is used, as ``Fly.withinReach`` does).
    ``Fly.dropBorrowedSpikes`` rewrites radius/outer/spikes in place.
    """

    radius: float
    halo: Optional[float]
    outer: float
    spikes: list[SpikeReach] = field(default_factory=list)
    pedestal: float = 0.0
    feather: float = 0.0


@dataclass
class Spike:
    """One fitted spike of a star model (``Sky.starSpikes``).

    ``angle`` radians; ``start`` the first radius (px) it is modelled from;
    ``amp[k]`` its amplitude at radius ``start + k``; ``reach`` the last radius.
    """

    angle: float
    start: float
    amp: np.ndarray
    reach: float


@dataclass
class StarModel:
    """``Sky.starModel`` (and ``Sky.pointModel``) result: a star's PSF model.

    ``prof[r]`` is the smoothed radial profile (intensity above sky) at radius r,
    zero past ``rmax``; ``col[c]`` the per-channel colour factor; ``support`` the
    radius beyond which the model is zero; ``core`` the core radius (px);
    ``parts`` extra point models attached to this star (``Fly.attachToCores``).
    """

    x: float
    y: float
    core: float
    prof: np.ndarray
    rmax: float
    spikes: list[Spike]
    col: list[float]
    support: float
    spike_sigma: Optional[float] = None
    parts: Optional[list["StarModel"]] = None


@dataclass
class Detection:
    """A star detection (``Sky.detections`` record, ``Fly.addMissingBright`` adds ``from_gaia``).

    ``nmax`` is 0 ("not measured") as with PixInsight 1.9's StarDetector.
    ``model``/``halo`` are filled by the star modeller (E3b).
    """

    index: int
    x: float
    y: float
    flux: float
    rect: Rect
    nmax: int = 0
    model: Optional[StarModel] = None
    halo: Optional[Halo] = None
    from_gaia: bool = False


@dataclass
class SpriteMatch:
    """An entry of ``Fly.assignSprites(...).sprites``: a placed star matched to its detection."""

    placed: Placed
    det: Detection
    rect: Rect


@dataclass
class Sprite:
    """``Sky.deblendSprite`` output: one moving star's deblended light.

    ``rect`` is the patch's place in the image; ``pixels``/``model_core``/
    ``model_spikes`` hold one ``(rect.height, rect.width)`` float32 array per
    channel; ``mask`` is the uint8 ``(rect.height, rect.width)`` footprint (None
    after ``scaled_scene``, which drops it as the JS deletes it).
    ``spike_angles`` radians. ``px_scale`` carries the scene's shrink factor.
    """

    source: GaiaSource
    d: float
    placed: Placed
    det: Detection
    rect: Rect
    radius: float
    centre: Point
    mask: Optional[np.ndarray]
    pixels: list[np.ndarray]
    model_core: list[np.ndarray]
    model_spikes: list[np.ndarray]
    spike_angles: list[float]
    px_scale: float = 1.0
    cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass
class SceneSprite:
    """``scene.sprites[k]`` = ``{s, p0}``: the sprite and its catalogue position at frame 0 (None off-projection)."""

    s: Sprite
    p0: Optional[Point]


@dataclass
class Scene:
    """``Render.scene``: everything a frame needs.

    ``S`` starless and ``R`` residual (stars layer outside every sprite), one
    ``(h, w)`` float32 plane per channel; ``target`` ``{ra, dec}``; ``tp`` the
    target's pixel; ``D`` the target distance (pc, ``inf`` for a galaxy);
    ``catalogue`` projected catalogue entries (dicts with x, y, source) or None.
    """

    w: int
    h: int
    nc: int
    S: list[np.ndarray]
    R: list[np.ndarray]
    sprites: list[SceneSprite]
    project: Projector
    target: dict[str, float]
    tp: Optional[Point]
    D: float
    catalogue: Optional[list[dict[str, Any]]] = None
    px_scale: float = 1.0
    cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
