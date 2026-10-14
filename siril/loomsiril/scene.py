"""Scene assembly, quarter turn, shrink, blend and composite (E4).

Ported from Loom ``script/lib/Render.js`` (v0.4.1): ``Render.channels`` and ``Render.scene``,
``Render.blend``, ``Render.logoLayer``'s neighbours ``Render.layerAxis`` / ``orDefault`` /
``upscaleStars`` / ``starSide``, ``Render.composite`` (the per-pixel ``compositeJs`` path),
``Render.rotateScene`` and ``Render.scaledScene``. Sprite drawing and the frame are E5.

Scenes are plain dataclasses (``types.Scene``); the JS's ``sc._scaled`` and ``sc.turned``
caches live in ``sc.cache``. Planes are float32 ``(h, w)`` arrays.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Optional, Sequence

import numpy as np

from loomsiril.core import Point, Rect, js_round
from loomsiril.resample import AxisView, AxisWeights, axis_weights, resample, shrink_patch
from loomsiril.types import Projector, Scene, SceneSprite, Sprite

__all__ = [
    "EXCESS_MAX",
    "blend",
    "channels",
    "composite",
    "layer_axis",
    "or_default",
    "rotate_scene",
    "scaled_scene",
    "SceneInputs",
    "scene",
    "star_side",
    "upscale_stars",
]

#: No upper clamp on the light past white (``Render.EXCESS_MAX``).
EXCESS_MAX: float = 1e30


def channels(planes: Sequence[np.ndarray], mask: Any = None) -> list[np.ndarray]:
    """``Render.channels``: float32 copies of the planes; where ``mask`` is 0 the copy is 0 (stars outside it are dropped)."""
    out: list[np.ndarray] = []
    keep = None if mask is None else np.asarray(mask).astype(bool)
    for p in planes:
        b = np.array(p, dtype=np.float32, copy=True)
        if keep is not None:
            b[~keep] = 0.0
        out.append(b)
    return out


@dataclass
class SceneInputs:
    """``Render.scene(a)``'s one argument, the JS object's fields in snake_case.

    ``starless`` the backdrop planes; ``stars`` the stars layer (None reads as no planes); ``sprites`` the
    placed sprites; ``project`` the sky projection; ``target`` {"ra", "dec"} the scene's aim; ``D`` the
    distance (pc, inf for a galaxy). ``residual`` the residual planes as they are, or else the stars layer
    with ``residual_mask`` zeroing where it is 0; ``catalogue`` the Gaia records the frames may draw.
    """

    starless: Sequence[np.ndarray]
    stars: Optional[Sequence[np.ndarray]]
    sprites: Sequence[Sprite]
    project: Projector
    target: dict[str, float]
    D: float
    residual: Optional[Sequence[np.ndarray]] = None
    residual_mask: Any = None
    catalogue: Optional[list[dict[str, Any]]] = None


def scene(a: SceneInputs) -> Scene:
    """``Render.scene``: everything a frame needs, from the starless backdrop and the stars layer.

    ``S`` is the starless planes; ``R`` the residual (see :class:`SceneInputs`). Each sprite gets its
    catalogue position at frame 0 (``p0``); ``tp`` is the target's pixel.
    """
    S = channels(a.starless)
    R = ([np.asarray(p, dtype=np.float32) for p in a.residual] if a.residual is not None
         else channels(a.stars or [], a.residual_mask))
    h, w = S[0].shape
    tp = a.project(a.target["ra"], a.target["dec"])
    sprite_list = [SceneSprite(s=s, p0=a.project(s.source["ra"], s.source["dec"])) for s in a.sprites]
    return Scene(w=w, h=h, nc=len(S), S=S, R=R, sprites=sprite_list, project=a.project, target=a.target,
                 tp=tp, D=a.D, catalogue=a.catalogue)


def blend(a: Sequence[np.ndarray], b: Sequence[np.ndarray], alpha: float) -> list[np.ndarray]:
    """``Render.blend``: new planes ``alpha*a + (1 - alpha)*b`` per channel (a crossfade's frames); ``b`` is left as it is."""
    out: list[np.ndarray] = []
    for pa, pb in zip(a, b):
        v = alpha * np.asarray(pa, dtype=np.float64) + (1 - alpha) * np.asarray(pb, dtype=np.float64)
        out.append(v.astype(np.float32))
    return out


def composite(Sc: np.ndarray, Tc: np.ndarray, hdr: bool) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """``Render.compositeJs``: the starless S and the stars T screened, both clamped to [0, 1].

    Returns ``(out, excess)``: excess is the light past white, T - 1 where T > 1 (capped at
    :data:`EXCESS_MAX`), only when ``hdr``; otherwise None.
    """
    s = np.clip(np.asarray(Sc, dtype=np.float64), 0.0, 1.0)
    t = np.clip(np.asarray(Tc, dtype=np.float64), 0.0, 1.0)
    out = (1 - (1 - s) * (1 - t)).astype(np.float32)
    excess = None
    if hdr:
        raw = np.asarray(Tc, dtype=np.float64)
        excess = np.where(raw > 1, np.minimum(raw - 1, EXCESS_MAX), 0.0).astype(np.float32)
    return out, excess


def layer_axis(full: AxisWeights, n: int, out_n: int, o: float, length: float, tp: float, K: float,
               src_n: int, kernel: str) -> AxisWeights:
    """``Render.layerAxis``: one axis' weights for the stars layer (n samples); the video's own (full) when that is its size."""
    return full if n == out_n else axis_weights(n, AxisView(o, length, tp, K, src_n), kernel)


def or_default(value: Any, dflt: Any) -> Any:
    """``Render.orDefault``: the option value, or the default when it is unset (None)."""
    return value if value is not None else dflt


def upscale_stars(T: list[np.ndarray], tw: int, th: int, out_w: int, out_h: int) -> list[np.ndarray]:
    """``Render.upscaleStars``: the stars layers (tw x th) up onto the full-size frame, Catmull-Rom; as they are when that is their size."""
    if tw == out_w and th == out_h:
        return T
    ux = axis_weights(out_w, AxisView(0, tw, 0, 1, tw), "bicubic")
    uy = axis_weights(out_h, AxisView(0, th, 0, 1, th), "bicubic")
    return [resample(b, tw, ux, uy) for b in T]


def star_side(n: int, sr: float) -> int:
    """``Render.starSide``: the stars layer's side for a video side of n at starRes sr (Medium: half, at least 1 px)."""
    return max(1, int(js_round(n * sr))) if sr < 1 else n


# ---------------------------------------------------------------------------
# The quarter turn and the area shrink of a whole scene.
# ---------------------------------------------------------------------------

def _turn(buf: np.ndarray) -> np.ndarray:
    """A plane turned 90 degrees clockwise: JS ``out[x*bh + (bh-1-y)] = buf[y*bw + x]``, i.e. ``np.rot90(buf, -1)``."""
    return np.ascontiguousarray(np.rot90(np.asarray(buf), -1))


def _turn_pt(p: Optional[Point], h: int) -> Optional[Point]:
    """Pixel (x, y) goes to (h - 1 - y, x) in a scene h pixels high (None stays None)."""
    if p is None:
        return None
    return Point(h - 1 - p.y, p.x)


def _turn_sprite(s: Sprite, h: int) -> Sprite:
    """``Render.rotateScene``'s sprite: the detection, rect, patches, spike directions and mask turned with the scene."""
    r = s.rect
    det_pt = _turn_pt(Point(s.det.x, s.det.y), h)
    det = replace(s.det, x=det_pt.x, y=det_pt.y)               # the JS leaves det.rect and det.model as they were
    rect = Rect(h - r.y1, r.x0, h - r.y0, r.x1)
    mask = None if s.mask is None else _turn(s.mask)
    return replace(
        s,
        det=det,
        rect=rect,
        pixels=[_turn(b) for b in s.pixels],
        model_core=[_turn(b) for b in s.model_core],          # every patch it carries turns with it
        model_spikes=[_turn(b) for b in s.model_spikes],
        spike_angles=[a + math.pi / 2 for a in s.spike_angles],  # (x, y) -> (h - 1 - y, x) turns directions by 90 degrees
        mask=mask,
        cache={},                                             # core parts cut before the turn are not this sprite's
    )


def rotate_scene(sc: Scene) -> Scene:
    """``Render.rotateScene``: the scene turned 90 degrees clockwise (w and h swap).

    Backdrop, residual, every sprite, the projection, the target pixel and the catalogue turn
    together, so frame 0 of the turned scene is exactly the turned image.
    """
    h = sc.h
    sprites = [SceneSprite(s=_turn_sprite(e.s, h), p0=_turn_pt(e.p0, h)) for e in sc.sprites]
    project = sc.project
    catalogue = None
    if sc.catalogue is not None:
        catalogue = [dict(c, x=h - 1 - c["y"], y=c["x"]) for c in sc.catalogue]
    return Scene(w=sc.h, h=sc.w, nc=sc.nc, S=[_turn(b) for b in sc.S], R=[_turn(b) for b in sc.R],
                 sprites=sprites, project=lambda ra, dec: _turn_pt(project(ra, dec), h),
                 target=sc.target, tp=_turn_pt(sc.tp, h), D=sc.D, catalogue=catalogue,
                 px_scale=sc.px_scale, cache={})


def _shrink_point(p: Optional[Point], d: float) -> Optional[Point]:
    """A scene pixel after the area shrink by d: (p + 0.5) d - 0.5 (None stays None)."""
    if p is None:
        return None
    return Point((p.x + 0.5) * d - 0.5, (p.y + 0.5) * d - 0.5)


def _shrink_whole(b: np.ndarray, d: float, w: int, h: int) -> np.ndarray:
    """A whole w x h plane area-averaged by d (the backdrop and the residual)."""
    return shrink_patch(b, 0, 0, w, h, d, w, h)["buf"]


def _shrink_detection(det: Any, d: float) -> Any:
    """The detection with its position, model core and rect shrunk by d (the JS leaves its other fields as they were)."""
    model = det.model
    if model is not None:
        model = replace(model, core=model.core * d)
    pt = _shrink_point(Point(det.x, det.y), d)
    dr = det.rect
    rect = None if dr is None else Rect(dr.x0 * d, dr.y0 * d, dr.x1 * d, dr.y1 * d)
    return replace(det, x=pt.x, y=pt.y, model=model, rect=rect)


def _shrink_sprite(e: SceneSprite, d: float, w: int, h: int) -> SceneSprite:
    """One sprite of ``scaledScene``: its patches cut from its rect at d, its detection, core, radius and px scale.

    The mask and the caches belong to the full-size patches, so they are dropped (as the JS deletes them).
    """
    s = e.s
    r = s.rect

    def cut(b: np.ndarray) -> dict[str, Any]:
        return shrink_patch(b, r.x0, r.y0, r.width, r.height, d, w, h)

    px = [cut(b) for b in s.pixels]
    g0 = px[0]
    out = replace(
        s,
        det=_shrink_detection(s.det, d),
        rect=Rect(g0["x0"], g0["y0"], g0["x0"] + g0["w"], g0["y0"] + g0["h"]),
        pixels=[q["buf"] for q in px],
        model_core=[cut(b)["buf"] for b in s.model_core],
        model_spikes=[cut(b)["buf"] for b in s.model_spikes],
        radius=None if s.radius is None else s.radius * d,
        px_scale=(s.px_scale or 1.0) * d,
        mask=None,
        cache={},
    )
    return SceneSprite(s=out, p0=_shrink_point(e.p0, d))


def _shrink_catalogue(catalogue: Optional[list[dict[str, Any]]], d: float) -> Optional[list[dict[str, Any]]]:
    """The catalogue entries with their projected x and y shrunk by d (None stays None)."""
    if catalogue is None:
        return None
    return [dict(c, x=(c["x"] + 0.5) * d - 0.5, y=(c["y"] + 0.5) * d - 0.5) for c in catalogue]


def scaled_scene(sc: Scene, d: float) -> Scene:
    """``Render.scaledScene``: the scene area-averaged by d (< 1) for High and Medium star quality.

    The backdrop, every sprite's patches and positions, its core's size, the catalogue and the
    projection are shrunk with it; ``px_scale`` carries d for the lengths counted in scene
    pixels. Positions map by (p + 0.5) d - 0.5. Kept in ``sc.cache["scaled"]`` for the same d;
    the distance follows the scene's.
    """
    cached = sc.cache.get("scaled")
    if cached is not None and cached["d"] == d:
        cached["scene"].D = sc.D
        return cached["scene"]
    project = sc.project
    scaled = Scene(
        w=math.ceil(sc.w * d), h=math.ceil(sc.h * d), nc=sc.nc,
        S=[_shrink_whole(b, d, sc.w, sc.h) for b in sc.S],
        R=[_shrink_whole(b, d, sc.w, sc.h) for b in sc.R],
        sprites=[_shrink_sprite(e, d, sc.w, sc.h) for e in sc.sprites],
        project=lambda ra, dec: _shrink_point(project(ra, dec), d),
        target=sc.target, tp=_shrink_point(sc.tp, d), D=sc.D,
        catalogue=_shrink_catalogue(sc.catalogue, d), px_scale=(sc.px_scale or 1.0) * d, cache={},
    )
    sc.cache["scaled"] = {"d": d, "scene": scaled}
    return scaled
