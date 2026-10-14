"""The frame renderer: one Fly-Through frame from a scene (E5: loomsiril/frame.py).

Ported from Loom ``script/lib/Render.js`` (v0.4.1): ``Render.frame`` (945-978),
``Render.frameImage`` (1009-1039, without the logo: status D, see docs/parity/e5.md) and
``Render.sceneFor`` (1593-1600). The stars layer's sprites are drawn by
:mod:`loomsiril.sprites`; the resampling by :mod:`loomsiril.resample`; the glow and
headroom by :mod:`loomsiril.bloom` and :mod:`loomsiril.scene`.

Planes are float32 ``(h, w)`` arrays, one per channel, top row first.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Optional

import numpy as np

from loomsiril import geometry as G
from loomsiril.bloom import bloom, saturate_stars, stars_to_headroom
from loomsiril.colour import OutputTransform, is_hdr
from loomsiril.presets import needs_rotation, star_quality
from loomsiril.resample import AxisView, axis_weights, resample
from loomsiril.scene import composite, layer_axis, or_default, rotate_scene, star_side, upscale_stars
from loomsiril.sprites import Camera, FrameState, FrameTarget, add_sprites, headroom_map
from loomsiril.types import Scene

__all__ = ["RenderOptions", "frame", "frame_image", "scene_for"]


@dataclass
class RenderOptions:
    """``opts`` of ``Render.frame`` (every field the frame and sprite code reads; defaults are what the JS does when unset).

    ``travel`` camera travel (pc, the JS ``opts.travel``, default 0); ``easing`` ("linear" or any
    other = smoothstep); ``growth`` growth amount (default GROWTH_DEFAULT: the JS has no default and
    the callers always set it); ``brightening`` spikes and glow brighten with the light ratio;
    ``kernel`` "bicubic" (default) or "bilinear"; ``backdrop_motion`` (None: BACKDROP_MOTION_DEFAULT);
    ``star_quality`` "highest" (default), "high" or "medium"; ``star_saturation`` (None: 1);
    ``bloom`` amount (None: 0); ``twinkle`` amount (0: none); ``motion_blur`` the shutter; ``frame_dt``
    the frame interval in clip seconds; ``duration`` the clip length in seconds; ``output`` the output
    transform (None: none); ``star_hdr`` the stars into HDR headroom; ``peak`` the HDR peak nits
    (None: HDR_PEAK_DEFAULT); ``t`` the frame's time (set by the frame, not by the caller);
    ``K`` the backdrop zoom (set by the frame); ``coarse_mips`` the Medium mip rule (set by the frame).
    """

    travel: float = 0.0
    easing: str = "smoothstep"
    growth: float = G.GROWTH_DEFAULT
    brightening: bool = False
    kernel: str = "bicubic"
    backdrop_motion: Optional[float] = None
    star_quality: Optional[str] = None
    star_saturation: Optional[float] = None
    bloom: Optional[float] = None
    twinkle: float = 0.0
    motion_blur: bool = False
    frame_dt: float = 0.0
    duration: float = 0.0
    output: Optional[OutputTransform] = None
    star_hdr: bool = False
    peak: Optional[float] = None
    t: float = 0.0
    K: Optional[float] = None
    coarse_mips: bool = False


def frame(sc: Scene, t: float, opts: RenderOptions, out_w: int, out_h: int, crop: Mapping[str, float]) -> list[np.ndarray]:
    """``Render.frame``: the frame at time t in [0, 1], out_w x out_h, of the camera crop ``{x, y, w, h}`` (image pixels).

    Resamples the backdrop and the stars layer (the stars at starRes of the video), adds every moved
    sprite to the stars layer, blooms and headrooms it, upscales it, and screens it onto the backdrop.
    Returns one float32 plane per channel.
    """
    s = opts.travel * G.ease(t, opts.easing)
    K = G.backdrop_zoom(sc.D, s, or_default(opts.backdrop_motion, G.BACKDROP_MOTION_DEFAULT))
    kernel = opts.kernel or "bicubic"
    vx = AxisView(crop["x"], crop["w"], sc.tp.x, K, sc.w)
    vy = AxisView(crop["y"], crop["h"], sc.tp.y, K, sc.h)
    ax = axis_weights(out_w, vx, kernel)
    ay = axis_weights(out_h, vy, kernel)
    # the stars layer at starRes of the video (Medium: half), the nebula always at full size
    sr = star_quality(opts.star_quality)["starRes"] or 1
    tw, th = star_side(out_w, sr), star_side(out_h, sr)
    cam = Camera(x=crop["x"], y=crop["y"], fx=crop["w"] / tw, fy=crop["h"] / th)
    target = FrameTarget(cam=cam, out_w=tw, out_h=th)
    tax = layer_axis(ax, tw, out_w, crop["x"], crop["w"], sc.tp.x, K, sc.w, kernel)
    tay = layer_axis(ay, th, out_h, crop["y"], crop["h"], sc.tp.y, K, sc.h, kernel)
    S = [resample(sc.S[c], sc.w, ax, ay) for c in range(sc.nc)]
    T = [resample(sc.R[c], sc.w, tax, tay) for c in range(sc.nc)]
    # before the start (a crossfade's pre-roll) the clock that drives twinkle, bloom, and the shutter holds at 0:
    # with the camera at rest there, every such moment is frame 0
    clock = max(0.0, t)
    opts_t = replace(opts, t=clock, K=K, coarse_mips=sr < 1)
    placed = add_sprites(FrameState(sc, s, opts_t), T, target)
    saturate_stars(T, or_default(opts.star_saturation, 1))
    # light past white blooms (none in the image itself, so frame 0 is untouched)
    bloom(T, tw, th, amount=or_default(opts.bloom, 0), seconds=clock * (opts.duration or 0))
    # in HDR, bright stars reach into the headroom by their magnitude; the backdrop keeps its tone
    hdr_out = opts.output is not None and is_hdr(opts.output.mode)
    # the map with the frame's backdrop zoom, so a catalogue star's bump follows the star the backdrop carries
    if hdr_out and opts.star_hdr:
        stars_to_headroom(T, headroom_map(sc, placed, replace(opts_t, K=K), target))
    # up onto the full-size frame (Catmull-Rom, pixel centres as everywhere: output u reads (u + 0.5) tw/outW - 0.5)
    T = upscale_stars(T, tw, th, out_w, out_h)
    return frame_image(S, T, opts, FrameTarget(cam=cam, out_w=out_w, out_h=out_h), clock)


def frame_image(S: list[np.ndarray], T: list[np.ndarray], opts: RenderOptions, target: FrameTarget,
                clock: float) -> list[np.ndarray]:
    """``Render.frameImage`` without the logo: the nebula S and the stars T screened together, through the output transform.

    ``target`` is the full-size output frame (its ``out_w`` x ``out_h``); the channel count is len(S). Returns one
    float32 ``(out_h, out_w)`` plane per channel. The composite is in the image's own
    encoding; the output transform (when set) converts it to the video's colour space.
    ``clock`` is unused without a logo and kept for the JS signature.
    """
    del clock  # the logo (its fade) is not ported (docs/parity/e5.md)
    out_w, out_h = target.out_w, target.out_h
    hdr = opts.output is not None and is_hdr(opts.output.mode)
    out: list[np.ndarray] = []
    excess: list[Optional[np.ndarray]] = []
    for c in range(len(S)):
        o, x = composite(S[c], T[c], hdr)
        out.append(o)
        excess.append(x)
    if opts.output is not None:
        if len(S) >= 3:
            opts.output.apply(out[:3], excess[:3])
        else:
            opts.output.apply(out[:1], excess[:1])
    return [np.asarray(p, dtype=np.float32).reshape(out_h, out_w) for p in out]


def scene_for(sc: Scene, out_w: int, out_h: int) -> Scene:
    """``Render.sceneFor``: the scene in the orientation a preset needs, turned once and kept in ``sc.cache["turned"]``."""
    if not needs_rotation(sc.w, sc.h, out_w, out_h):
        return sc
    turned = sc.cache.get("turned")
    if turned is None:
        turned = rotate_scene(sc)
        sc.cache["turned"] = turned
    turned.D = sc.D            # the dialog sets the distance on the scene: the turned copy follows it
    return turned
