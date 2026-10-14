"""Sprite drawing: each moving star drawn into the stars layer (E5: loomsiril/sprites.py).

Ported from Loom ``script/lib/Render.js`` (v0.4.1). The JS draws one output pixel at a
time (``Render.spriteRow`` -> ``spritePixels`` -> ``glowSums`` / ``spikeSums``); here every
output pixel of a sprite's bounds is evaluated at once with numpy, and the (nx, ny)
sub-sample grid is a loop over sample slots (a handful of passes), never over pixels.
Patch reads go through ``resample.patch_sample`` on the mip level the JS picks.

The JS skips output pixels whose samples can only read dark patch pixels
(``Render.reachTests``, ``darkMaps``, ``nearLight`` ...). Those are exact speed-ups for a
scalar interpreter: a samples-in-bounds computation does the same job, so they are not
ported (docs/parity/e5.md).

Naming: JS ``Render.fooBar`` -> ``foo_bar``. Sprite caches (``_split``, ``_glow``, ``_mips``)
live in ``sprite.cache`` (and ``CoreSprite.cache``) under keys documented below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from loomsiril import geometry as G
from loomsiril.colour import HDR_PEAK_DEFAULT
from loomsiril.core import Point, Rect, smoothstep, smoothstep_arr
from loomsiril.presets import star_quality
from loomsiril.resample import mip_level, mip_of, patch_sample
from loomsiril.types import Detection, Sprite, StarModel

__all__ = [
    "Camera",
    "FrameState",
    "CoreSprite",
    "DrawHow",
    "FrameTarget",
    "PlacedSprite",
    "SpikeHow",
    "SpriteDraw",
    "SplitParts",
    "add_sprites",
    "core_radius",
    "draw_placed",
    "draw_sprites",
    "draw_placed_spikes",
    "glow_patch",
    "glow_samples",
    "headroom_map",
    "placed_channels",
    "place_sprite",
    "seen_shares",
    "shutter_path",
    "split_patches",
    "sprite_bounds",
    "star_key",
]

@dataclass
class Camera:
    """``cam``: maps output pixels to camera (source) pixels, ``q = x + (u + 0.5) fx - 0.5``."""

    x: float
    y: float
    fx: float
    fy: float

    def to_u(self, q: float) -> float:
        """The output column of camera x q: the inverse of ``q = x + (u + 0.5) fx - 0.5``."""
        return (q - self.x + 0.5) / self.fx - 0.5

    def to_v(self, q: float) -> float:
        """The output row of camera y q: the inverse of ``q = y + (v + 0.5) fy - 0.5``."""
        return (q - self.y + 0.5) / self.fy - 0.5


@dataclass(frozen=True)
class FrameTarget:
    """Where a sprite is drawn: the camera (``cam``) and the output frame's size (``out_w`` x ``out_h``).

    Every sprite function that needs the frame takes this one object instead of the three values.
    """

    cam: Camera
    out_w: int
    out_h: int


@dataclass(frozen=True)
class FrameState:
    """What places and draws every sprite of one frame: the scene ``sc``, the camera travel ``s`` and the frame's ``opts``.

    Passed to :func:`place_sprite`, :func:`draw_placed` and :func:`add_sprites` instead of the three values.
    """

    sc: Any
    s: float
    opts: Any


@dataclass
class SpikeHow:
    """``how.spike = {length, angles}``: spikes stretch along their length, on the given angles (radians)."""

    length: float
    angles: list[float]


@dataclass
class DrawHow:
    """``how`` of ``Render.drawSprites``: ``seen`` (None: 1), ``coarse`` (Medium mips), ``footprint``
    (High/Medium glow sample counts), ``spike`` (a spike draw) and ``mip`` (False: no mip level)."""

    seen: Optional[float] = None
    coarse: bool = False
    footprint: bool = False
    spike: Optional[SpikeHow] = None
    mip: Optional[bool] = None


@dataclass
class CoreSprite:
    """``splitPatches``' ``coreSprite``: the core's own box (``rect``) and the detection it belongs to."""

    rect: Rect
    det: Detection
    cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass
class SpriteDraw:
    """The arguments of ``Render.drawSprites`` for one draw: ``patches`` (one per channel), the sprite ``sp``, its centre
    (``cx``, ``cy``), growth ``g``, the glow gains ``ks`` and the outer gains ``k_outers`` (None: as ``ks``), the core
    radius ``rc`` and ``how``. Drawn into a :class:`FrameTarget` by :func:`draw_sprites`.
    """

    patches: Sequence[np.ndarray]
    sp: Any
    cx: float
    cy: float
    g: float
    ks: Sequence[float]
    k_outers: Sequence[Optional[float]]
    rc: float
    how: Optional[DrawHow]


@dataclass
class SplitParts:
    """``Render.splitPatches`` result: the photograph and the model inside the core box, the model outside it."""

    photo_core: np.ndarray
    model_core: np.ndarray
    model_outer: np.ndarray
    core_sprite: CoreSprite


@dataclass
class PlacedSprite:
    """``Render.placeSprite`` record: where and how a sprite is drawn this frame.

    ``j`` the sprite's index in the scene; ``m`` the ``Fly.moved`` result ({ra, dec, front, ratio});
    ``g`` growth; ``k_outer``/``k_core`` the gains of the glow and of the core; ``rc`` the core radius;
    ``cx``, ``cy`` its centre; ``seen`` the share in frame (set by :func:`seen_shares`).
    """

    e: Any                       # the SceneSprite (s, p0)
    j: int
    sp: Sprite
    m: dict[str, Any]
    g: float
    k_outer: float
    k_core: float
    rc: float
    cx: float
    cy: float
    seen: float
    alpha: float
    spike_length: float
    u: float = 0.0
    v: float = 0.0
    r: float = 0.0
    frame_seen: float = 1.0


@dataclass
class _Ctx:
    """``Render.spriteContext``'s ``ctx``: one place every sample is read through."""

    mps: list[np.ndarray]
    f2: int
    ox: float
    oy: float
    cx: float
    cy: float
    g: float
    rc: float
    seen: float
    radial: bool
    cam: Camera
    ks: list[float]
    k_os: list[float]
    nx: int
    ny: int
    axes: Optional[np.ndarray]
    stretch: float


# ---------------------------------------------------------------------------
# Small helpers: vectorised forms of the scalar Fly functions.
# ---------------------------------------------------------------------------


def _how_seen(how: Optional[DrawHow]) -> float:
    """``Render.howSeen``: how.seen, 1 when unset."""
    return how.seen if (how is not None and how.seen is not None) else 1.0


def _how_mip_level(how: Optional[DrawHow], cam: Camera, gs: float) -> int:
    """``Render.howMipLevel``: the mip level a sprite is drawn from (0 when how.mip is False)."""
    if how is not None and how.mip is False:
        return 0
    return mip_level(min(cam.fx, cam.fy), gs, bool(how.coarse) if how is not None else False)


def _mip_plane(owner_cache: dict[str, Any], patch: np.ndarray, level: int) -> np.ndarray:
    """``Render.mipOf``: level ``level`` of a patch, halved from the patch; cached on the owner by the patch's identity."""
    if level == 0:
        return patch
    key = "mips:%d" % id(patch)
    entry = owner_cache.get(key)
    if entry is None or entry[0] is not patch:
        entry = (patch, [])
        owner_cache[key] = entry
    return mip_of(patch, level, entry[1])


def star_key(s: dict[str, Any]) -> str:
    """``Render.starKey``: a catalogue star's identity across the scene's copies (its sky position, 6 decimals)."""
    ra = s.get("ra")
    if ra is None:
        return ""
    return "%s,%s" % (_to_fixed6(ra), _to_fixed6(s.get("dec")))


def _to_fixed6(v: Any) -> str:
    """JS ``Number.prototype.toFixed(6)`` for a finite number (ties follow the binary value, as in Python)."""
    return "%.6f" % float(v)


def _cat_field(entry: dict[str, Any], key: str) -> Any:
    """A catalogue entry's field: flat (ra, G, ...) or under ``source`` (the Scene's projected entries)."""
    if key in entry:
        return entry[key]
    src = entry.get("source")
    if isinstance(src, dict):
        return src.get(key)
    return None


def _finite(v: Any) -> bool:
    """``isFinite`` of a number (None and NaN are not finite)."""
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


# ---------------------------------------------------------------------------
# Bounds and the core.
# ---------------------------------------------------------------------------


def sprite_bounds(sp: Any, cx: float, cy: float, g: float, target: FrameTarget) -> dict[str, int]:
    """``Render.spriteBounds``: the output pixels a sprite (centred cx, cy, grown g) can touch, inclusive, clipped to the frame."""
    cam, out_w, out_h = target.cam, target.out_w, target.out_h
    r = sp.rect
    return {
        "u0": max(0, math.floor(cam.to_u(cx + (r.x0 - 1 - sp.det.x) * g))),
        "u1": min(out_w - 1, math.ceil(cam.to_u(cx + (r.x1 - sp.det.x) * g))),
        "v0": max(0, math.floor(cam.to_v(cy + (r.y0 - 1 - sp.det.y) * g))),
        "v1": min(out_h - 1, math.ceil(cam.to_v(cy + (r.y1 - sp.det.y) * g))),
    }


def core_radius(sp: Sprite) -> float:
    """``Render.coreRadius``: the model's core (a model with core > 0), else half the detection box (at least 1 px).

    A point model (core 0) falls back to the rect, as in the JS.
    """
    model: Optional[StarModel] = sp.det.model
    if model is not None and model.core > 0:
        return model.core
    r = sp.det.rect
    return max(1.0, (r.x1 - r.x0) / 2) if r is not None else 0.0


# ---------------------------------------------------------------------------
# The sprite context and the sample sums.
# ---------------------------------------------------------------------------


def _sprite_context(d: SpriteDraw, cam: Camera) -> _Ctx:
    """``Render.spriteContext``: the mip planes, offsets, gains and sample counts one sprite draw reads through."""
    patches, sp, cx, cy, g, ks = d.patches, d.sp, d.cx, d.cy, d.g, d.ks
    k_outers, rc, how = d.k_outers, d.rc, d.how
    seen = _how_seen(how)
    spike = how.spike if how is not None else None
    r = sp.rect
    rw, rh = r.x1 - r.x0, r.y1 - r.y0
    radial = spike is None and (rc > 0 and (g != 1 or seen < 1))
    # spikes stretch along their length only (how.spike = {length, angles}): shorter for what is hidden
    stretch = spike.length * max(0.05, seen) if spike is not None else 1.0
    # when a sprite pixel is smaller than an output pixel, the output pixel averages n x n samples over its footprint
    gs = 1.0 if (radial or spike is not None) else g
    # shrunk far down, sample a pre-shrunk copy (a mip level), not n x n points of the full one
    level = _how_mip_level(how, cam, gs)
    f2 = 1 << level
    owner = sp.cache
    mps = [_mip_plane(owner, p, level) for p in patches]
    if spike is not None:
        ks_c = [k * seen for k in ks]
        k_os = [k * seen for k in ks]
        axes: Optional[np.ndarray] = np.array([[math.cos(a), math.sin(a)] for a in spike.angles], dtype=np.float64).reshape(-1, 2)
    else:
        ks_c = list(ks)
        k_os = []
        for i, k in enumerate(ks):
            ko = k_outers[i]
            k_os.append((ko if ko is not None else k) * seen)
        axes = None
    return _Ctx(mps=mps, f2=f2, ox=sp.det.x - r.x0, oy=sp.det.y - r.y0, cx=cx, cy=cy, g=g, rc=rc, seen=seen,
                radial=radial, cam=cam, ks=ks_c, k_os=k_os,
                nx=max(1, math.ceil(cam.fx / (gs * f2))), ny=max(1, math.ceil(cam.fy / (gs * f2))),
                axes=axes, stretch=stretch)


def footprint_pays(ctx: _Ctx) -> bool:
    """``Render.footprintPays``: High only samples a glow by its footprint when that can take fewer than the full count."""
    cam = ctx.cam
    return ctx.radial and math.ceil(max(cam.fx, cam.fy) / (ctx.g * max(0.05, ctx.seen) * ctx.f2) - 1e-9) < ctx.nx


def glow_samples(ctx: _Ctx, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    """``Render.glowSamples``: per pixel centred (dx, dy) from the star, the samples a side (its footprint), 0 for the full count.

    Vectorised over pixels. A pixel reaching into the core keeps the full count.
    """
    dx = np.asarray(dx, dtype=np.float64)
    dy = np.asarray(dy, dtype=np.float64)
    out = np.zeros(dx.shape, dtype=np.int64)
    if not ctx.radial:
        return out
    h = 0.5 * math.sqrt(ctx.cam.fx * ctx.cam.fx + ctx.cam.fy * ctx.cam.fy)
    ro = np.sqrt(dx * dx + dy * dy) - h
    outer = ro > ctx.rc
    if not outer.any():
        return out
    rr = np.where(outer, ro, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        stretch = np.maximum(G.radial_source_arr(rr, ctx.rc, ctx.g, ctx.seen) / rr,
                             1.0 / (ctx.g * max(0.05, ctx.seen)))
    span = max(ctx.cam.fx, ctx.cam.fy) * stretch / ctx.f2
    n = np.maximum(1, np.ceil(span - 1e-9)).astype(np.int64)
    full = max(ctx.nx, ctx.ny)
    out = np.where(outer & (n < full), n, 0)
    return out


def _sample_sums(ctx: _Ctx, uu: np.ndarray, vv: np.ndarray, nx: int, ny: int) -> np.ndarray:
    """``Render.spikeSums`` / ``Render.glowSums``: the nx x ny samples' sums per channel for the pixels (uu, vv).

    Returns (channels, pixels), not divided by the sample count. Loops over sample slots only.
    """
    cam = ctx.cam
    nc = len(ctx.mps)
    acc = np.zeros((nc, uu.size), dtype=np.float64)
    if uu.size == 0:
        return acc
    for sy in range(ny):
        dy = cam.y + (vv + (sy + 0.5) / ny) * cam.fy - 0.5 - ctx.cy
        for sx in range(nx):
            dx = cam.x + (uu + (sx + 0.5) / nx) * cam.fx - 0.5 - ctx.cx
            if ctx.axes is not None:
                _add_spike_sample(ctx, acc, dx, dy)
            else:
                _add_glow_sample(ctx, acc, dx, dy)
    return acc


def _add_glow_sample(ctx: _Ctx, acc: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> None:
    """``Render.glowSums`` for one sample slot: read where ``Fly.radialSource`` puts it, weighted from k to kO by smoothstep."""
    if ctx.radial:
        ro = np.sqrt(dx * dx + dy * dy)
        with np.errstate(divide="ignore", invalid="ignore"):
            f = np.where(ro > 0, G.radial_source_arr(ro, ctx.rc, ctx.g, ctx.seen) / np.where(ro > 0, ro, 1.0), 1.0)
        sm = smoothstep_arr(0.0, ctx.rc, ro)
    else:
        f = 1.0 / ctx.g
        sm = None
    px = (ctx.ox + dx * f + 0.5) / ctx.f2 - 0.5
    py = (ctx.oy + dy * f + 0.5) / ctx.f2 - 0.5
    for ch, mp in enumerate(ctx.mps):
        if sm is None:
            w = ctx.ks[ch]
        else:
            w = ctx.ks[ch] + (ctx.k_os[ch] - ctx.ks[ch]) * sm
        acc[ch] += w * patch_sample(mp, mp.shape[1], mp.shape[0], px, py)


def _add_spike_sample(ctx: _Ctx, acc: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> None:
    """``Render.spikeSums`` for one sample slot: each sample read along the nearest spike axis it points along (``Render.spikeSource``)."""
    axes = ctx.axes
    assert axes is not None
    n = dx.size
    best_lat = np.full(n, np.inf)
    best_along = np.zeros(n)
    best_lt = np.zeros(n)
    ax0 = np.zeros(n)
    ax1 = np.zeros(n)
    has = np.zeros(n, dtype=bool)
    for i in range(axes.shape[0]):
        a0, a1 = axes[i, 0], axes[i, 1]
        along = dx * a0 + dy * a1
        lat = -dx * a1 + dy * a0
        cond = (along > 0) & (np.abs(lat) < best_lat)
        best_lat = np.where(cond, np.abs(lat), best_lat)
        best_along = np.where(cond, along, best_along)
        best_lt = np.where(cond, lat, best_lt)
        ax0 = np.where(cond, a0, ax0)
        ax1 = np.where(cond, a1, ax1)
        has |= cond
    if not has.any():
        return
    al = np.where(best_along <= ctx.rc, best_along, ctx.rc + (best_along - ctx.rc) / ctx.stretch)
    sx = np.where(has, al * ax0 - best_lt * ax1, 0.0)
    sy = np.where(has, al * ax1 + best_lt * ax0, 0.0)
    px = (ctx.ox + sx + 0.5) / ctx.f2 - 0.5
    py = (ctx.oy + sy + 0.5) / ctx.f2 - 0.5
    for ch, mp in enumerate(ctx.mps):
        val = ctx.ks[ch] * patch_sample(mp, mp.shape[1], mp.shape[0], px, py)
        acc[ch] += np.where(has, val, 0.0)


# ---------------------------------------------------------------------------
# Drawing a sprite into the accumulators.
# ---------------------------------------------------------------------------


def sample_groups(nx_p: np.ndarray, ny_p: np.ndarray) -> list[tuple[int, int, np.ndarray]]:
    """Pixels grouped by their (nx, ny) sample count, one group per distinct pair.

    One counting pass and one stable sort replace ``np.unique(axis=0)`` per sample count
    (which sorted a void view of the pairs: 11 s of a 1080p frame in the acceptance-test scene). Pairs are
    packed into one integer ``nx * (ny_top) + ny``, so the groups come out in ascending pair
    order, as ``np.unique`` returned them; each group holds its pixel indices in ascending order.
    """
    ny_top = int(ny_p.max()) + 1
    key = nx_p.astype(np.int64) * ny_top + ny_p.astype(np.int64)
    order = np.argsort(key, kind="stable")
    counts = np.bincount(key)
    groups: list[tuple[int, int, np.ndarray]] = []
    start = 0
    for k in np.flatnonzero(counts):
        c = int(counts[k])
        groups.append((int(k) // ny_top, int(k) % ny_top, order[start:start + c]))
        start += c
    return groups


def _grouped_sums(ctx: _Ctx, uu: np.ndarray, vv: np.ndarray, nx_p: np.ndarray, ny_p: np.ndarray) -> np.ndarray:
    """The per-pixel sample sums, divided by each pixel's own sample count: (channels, pixels).

    Pixels that share a sample count are summed in one ``_sample_sums`` pass. A constant count
    (every pixel at the full count) is one pass without any grouping.
    """
    nc = len(ctx.mps)
    sums = np.zeros((nc, uu.size), dtype=np.float64)
    if uu.size == 0:
        return sums
    if np.all(nx_p == nx_p[0]) and np.all(ny_p == ny_p[0]):
        groups: list[tuple[int, int, Any]] = [(int(nx_p[0]), int(ny_p[0]), slice(None))]
    else:
        groups = sample_groups(nx_p, ny_p)
    for nxg, nyg, sel in groups:
        part = _sample_sums(ctx, uu[sel], vv[sel], nxg, nyg)
        sums[:, sel] = part / (nxg * nyg)
    return sums


def draw_sprites(accs: Sequence[np.ndarray], target: FrameTarget, d: SpriteDraw) -> None:
    """``Render.drawSprites``: adds one sprite's light, one channel per patch, into the accumulators (float32 planes, (out_h, out_w)).

    Every output pixel of the sprite's bounds is evaluated at once; the glow's samples are summed
    slot by slot. With High/Medium's footprint, pixels are grouped by their sample count (JS
    ``glowSamples``) and each group is one pass.
    """
    cam = target.cam
    patches, sp, cx, cy, g, how = d.patches, d.sp, d.cx, d.cy, d.g, d.how
    ctx = _sprite_context(d, cam)
    b = sprite_bounds(sp, cx, cy, max(g, ctx.stretch), target)
    if b["u1"] < b["u0"] or b["v1"] < b["v0"]:
        return
    vv, uu = np.meshgrid(np.arange(b["v0"], b["v1"] + 1), np.arange(b["u0"], b["u1"] + 1), indexing="ij")
    vv = vv.reshape(-1)
    uu = uu.reshape(-1)
    nc = len(patches)
    nx_p = np.full(uu.size, ctx.nx, dtype=np.int64)
    ny_p = np.full(uu.size, ctx.ny, dtype=np.int64)
    if how is not None and how.footprint and footprint_pays(ctx):
        n = glow_samples(ctx, cam.x + (uu + 0.5) * cam.fx - 0.5 - ctx.cx, cam.y + (vv + 0.5) * cam.fy - 0.5 - ctx.cy)
        sampled = n > 0
        nx_p = np.where(sampled, np.minimum(n, ctx.nx), ctx.nx)
        ny_p = np.where(sampled, np.minimum(n, ctx.ny), ctx.ny)
    sums = _grouped_sums(ctx, uu, vv, nx_p, ny_p)
    for ch in range(nc):
        accs[ch][vv, uu] += sums[ch]


def draw_placed(state: FrameState, T: list[np.ndarray], q: PlacedSprite, target: FrameTarget) -> None:
    """``Render.drawPlaced``: one placed star, along its shutter path, in each channel.

    Its photograph turns to its model up close, twinkling. Glow, core and spikes are drawn
    with their own weights, as in the JS.
    """
    opts = state.opts
    sp = q.sp
    path = shutter_path(state, q, target)
    seconds = (opts.t or 0) * (opts.duration or 0)
    # up close the star turns from its photograph (noise, processing marks) to its smooth model -- its core
    # sooner (Fly.coreModelWeight); its spikes take only the glow's boost
    wo = G.model_weight(q.m["ratio"]) if sp.model_core is not None else 0.0
    wc = G.core_model_weight(q.m["ratio"]) if sp.model_core is not None else 0.0
    ch = placed_channels(q, opts, seconds, len(T), wc)
    tw, glow, spikes, parts = ch["tw"], ch["glow"], ch["spikes"], ch["parts"]
    glow_how = DrawHow(seen=q.seen, coarse=bool(opts.coarse_mips), footprint=bool(star_quality(opts.star_quality)["footprint"]))
    draws: list[tuple[list[np.ndarray], Any, float]] = [(glow, sp, 1 - wo)]
    if parts[0] is not None:
        draws.append((_pick(parts, "photo_core"), parts[0].core_sprite, wo - wc))
        draws.append((_pick(parts, "model_core"), parts[0].core_sprite, wc))
    if parts[0] is not None and wo > 0:
        draws.append((_pick(parts, "model_outer"), sp, wo))
    for patches, src, weight in draws:
        if weight == 0:
            continue
        s1 = [t * weight / len(path) for t in tw]
        ks = [q.k_core * x for x in s1]
        k_os = [q.k_outer * x for x in s1]
        for p in path:
            draw_sprites(T, target, SpriteDraw(patches, src, p.x, p.y, q.g, ks, k_os, q.rc, glow_how))
    if spikes[0] is not None:
        draw_placed_spikes(T, q, target, ch, path)


def _pick(parts: Sequence[Optional[SplitParts]], key: str) -> list[np.ndarray]:
    """One field of each channel's split parts (``parts[c].<key>``); only called when every channel has parts."""
    return [getattr(p, key) for p in parts]


def placed_channels(q: PlacedSprite, opts: Any, seconds: float, nc: int, wc: float) -> dict[str, Any]:
    """``Render.placedChannels``: per channel, the twinkle, glow patch, spike patch and core split (when wc > 0).

    The dict also carries ``coarse`` (the Medium mip rule of the frame): the channel plan of one placed star.
    """
    sp = q.sp
    tw: list[float] = []
    glow: list[np.ndarray] = []
    spikes: list[Optional[np.ndarray]] = []
    parts: list[Optional[SplitParts]] = []
    for c in range(nc):
        cc = min(c, len(sp.pixels) - 1)
        tw.append(G.twinkle(q.j + 1, seconds, opts.twinkle, c))
        spikes.append(sp.model_spikes[cc] if (sp.spike_angles is not None and sp.model_spikes is not None and cc < len(sp.model_spikes)) else None)
        glow.append(glow_patch(sp, cc) if spikes[c] is not None else sp.pixels[cc])
        parts.append(split_patches(sp, cc, q.rc, glow[c]) if wc > 0 else None)
    return {"tw": tw, "glow": glow, "spikes": spikes, "parts": parts, "coarse": bool(opts.coarse_mips)}


def draw_placed_spikes(T: list[np.ndarray], q: PlacedSprite, target: FrameTarget, ch: dict[str, Any],
                       path: list[Point]) -> None:
    """``Render.drawPlacedSpikes``: a placed star's spikes drawn on their own from its model, along the shutter path."""
    sp = q.sp
    sk = [q.alpha * t / len(path) for t in ch["tw"]]
    how = DrawHow(seen=q.seen, coarse=ch["coarse"], spike=SpikeHow(length=q.spike_length, angles=list(sp.spike_angles)))
    for p in path:
        draw_sprites(T, target, SpriteDraw(ch["spikes"], sp, p.x, p.y, q.g, sk, sk, q.rc, how))


def glow_patch(sp: Sprite, c: int) -> np.ndarray:
    """``Render.glowPatch``: a sprite's photograph minus its spikes (its glow), cached on the sprite (key ``glow:c``)."""
    key = "glow:%d" % c
    if key not in sp.cache:
        sp.cache[key] = (sp.pixels[c].astype(np.float64) - sp.model_spikes[c].astype(np.float64)).astype(np.float32)
    return sp.cache[key]


def _core_share(sp: Sprite, rc: float, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """The core's share at (x, y): 1 inside rc, 0 beyond rc + 3 px (smooth between; 3 px of the full-size scene)."""
    edge = 3 * (sp.px_scale or 1)
    return 1 - smoothstep_arr(rc, rc + edge, np.hypot(x - sp.det.x, y - sp.det.y))


def _core_box(sp: Sprite, rc: float) -> Rect:
    """The core's own box: the detection's pixel, grown by rc + 4 px, clipped to the sprite's rect."""
    r = sp.rect
    R = math.ceil(rc) + 4
    fx, fy = math.floor(sp.det.x), math.floor(sp.det.y)
    return Rect(max(r.x0, fx - R), max(r.y0, fy - R), min(r.x1, fx + R + 1), min(r.y1, fy + R + 1))


def _outer_model(sp: Sprite, rc: float, M: np.ndarray) -> np.ndarray:
    """The model outside the core, over the whole sprite rect (float32, (rh, rw))."""
    r = sp.rect
    xx, yy = np.meshgrid(np.arange(r.x0, r.x1, dtype=np.float64), np.arange(r.y0, r.y1, dtype=np.float64))
    return ((1 - _core_share(sp, rc, xx, yy)) * M).astype(np.float32)


def _core_parts(sp: Sprite, rc: float, cr: Rect, P: np.ndarray, M: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The photograph and the model inside the core box cr, each weighted by the core's share (float32)."""
    r = sp.rect
    xs_core = np.arange(cr.x0, cr.x1, dtype=np.float64)
    ys_core = np.arange(cr.y0, cr.y1, dtype=np.float64)
    cxx, cyy = np.meshgrid(xs_core, ys_core)
    k = _core_share(sp, rc, cxx, cyy)
    ri = (cyy - r.y0).astype(np.int64)
    ci = (cxx - r.x0).astype(np.int64)
    return (k * P[ri, ci]).astype(np.float32), (k * M[ri, ci]).astype(np.float32)


def split_patches(sp: Sprite, c: int, rc: float, photo: Optional[np.ndarray] = None) -> SplitParts:
    """``Render.splitPatches``: the photograph and the model cut at the core (rc, a soft 3 px edge), cached on the sprite (key ``split:c``).

    The core parts are cut to the core's own box (rc + 4 px) so drawing them costs little; the core
    and the rest turn to the model at different distances.
    """
    key = "split:%d" % c
    if key in sp.cache:
        return sp.cache[key]
    r = sp.rect
    P = np.asarray(photo if photo is not None else sp.pixels[c], dtype=np.float64)
    M = np.asarray(sp.model_core[c], dtype=np.float64)
    cr = _core_box(sp, rc)
    model_outer = _outer_model(sp, rc, M)
    photo_core, model_core = _core_parts(sp, rc, cr, P, M)
    assert photo_core.shape == (cr.y1 - cr.y0, cr.x1 - cr.x0) and model_outer.shape == (r.y1 - r.y0, r.x1 - r.x0)
    out = SplitParts(photo_core=photo_core, model_core=model_core, model_outer=model_outer,
                     core_sprite=CoreSprite(rect=cr, det=sp.det))
    sp.cache[key] = out
    return out


# ---------------------------------------------------------------------------
# Placing, shutter, seen shares and the HDR headroom.
# ---------------------------------------------------------------------------


def place_sprite(state: FrameState, e: Any, j: int) -> Optional[PlacedSprite]:
    """``Render.placeSprite``: where and how a sprite is drawn at camera travel s, or None when it is not (faded, passed, off the projection).

    The centre never spreads slower than the backdrop (``Fly.screenPosition``); the glow grows and spreads
    its light (``Fly.growth``); the core keeps the light a magnified core had.
    """
    sc, s, opts = state.sc, state.s, state.opts
    sp = e.s
    m = G.moved(sp.source["ra"], sp.source["dec"], sp.d, sc.target, s)
    alpha = G.opacity(m["ratio"], m["front"])
    if alpha <= 0 or e.p0 is None:
        return None
    p = sc.project(m["ra"], m["dec"])
    if p is None:
        return None
    K = opts.K or 1.0
    g = max(K, G.growth(m["ratio"], opts.growth))
    k_outer = ((m["ratio"] * m["ratio"]) if opts.brightening else 1.0) / (g * g) * alpha
    at = G.screen_position(Point(sp.det.x + p.x - e.p0.x, sp.det.y + p.y - e.p0.y), Point(sp.det.x, sp.det.y), sc.tp, K)
    return PlacedSprite(e=e, j=j, sp=sp, m=m, g=g, k_outer=k_outer, k_core=k_outer * g * g, rc=core_radius(sp),
                        cx=at.x, cy=at.y, seen=1.0, alpha=alpha, spike_length=G.spike_length(m["ratio"], opts.brightening, g))


def seen_shares(placed: list[PlacedSprite], target: FrameTarget, ease: float) -> None:
    """``Render.seenShares``: what is seen of each placed star's core (in frame, not behind a nearer one); ``ease`` 0 leaves all seen."""
    if not (ease > 0):
        return
    cam, out_w, out_h = target.cam, target.out_w, target.out_h
    for q in placed:
        q.u = cam.to_u(q.cx)
        q.v = cam.to_v(q.cy)
        q.r = q.rc / cam.fx
        q.frame_seen = G.disc_visible(q.u, q.v, q.r, out_w, out_h)
    for q in placed:
        cover = 0.0
        for o in placed:
            if o is q or not (o.m["ratio"] > q.m["ratio"]):
                continue
            d = math.hypot(o.u - q.u, o.v - q.v)
            if d < o.r + q.r:
                cover = max(cover, G.cover_fraction(q.r, o.r, d))
        q.seen = 1 - ease * (1 - q.frame_seen * (1 - cover))


def shutter_path(state: FrameState, q: PlacedSprite, target: FrameTarget) -> list[Point]:
    """``Render.shutterPath``: the centres a star is drawn at while the shutter is open (motion blur), one step per ~0.75 px.

    The first frame (t = 0) is drawn still, so it stays the image. Its length is measured on the camera
    (``target.cam``).
    """
    opts = state.opts
    if not opts.motion_blur or not (opts.frame_dt > 0) or not (opts.t > 0):
        return [Point(q.cx, q.cy)]
    ends = _shutter_ends(state, q)
    cam = target.cam
    length = math.hypot((ends[1].x - ends[0].x) / cam.fx, (ends[1].y - ends[0].y) / cam.fy)
    return _shutter_samples(ends, int(G.shutter_steps(length)))


def _shutter_ends(state: FrameState, q: PlacedSprite) -> list[Point]:
    """The centres at the shutter's two ends (the frame's time -/+ half the shutter); the still centre where the star is not in front."""
    sc, opts, sp, e = state.sc, state.opts, q.sp, q.e
    h = 0.5 * G.SHUTTER * min(opts.frame_dt, opts.t)
    ends: list[Point] = []
    for d in (-h, h):
        tt = max(0.0, min(1.0, opts.t + d))
        m = G.moved(sp.source["ra"], sp.source["dec"], sp.d, sc.target, opts.travel * G.ease(tt, opts.easing))
        p = sc.project(m["ra"], m["dec"]) if m["front"] else None
        if p is not None:
            ends.append(G.screen_position(Point(sp.det.x + p.x - e.p0.x, sp.det.y + p.y - e.p0.y), Point(sp.det.x, sp.det.y),
                                          sc.tp, opts.K or 1.0))
        else:
            ends.append(Point(q.cx, q.cy))
    return ends


def _shutter_samples(ends: list[Point], n: int) -> list[Point]:
    """``n`` centres evenly from one shutter end to the other (the middle one when n is 1)."""
    out: list[Point] = []
    for k in range(n):
        f = k / (n - 1) if n > 1 else 0.5
        out.append(Point(ends[0].x + f * (ends[1].x - ends[0].x), ends[0].y + f * (ends[1].y - ends[0].y)))
    return out


def add_sprites(state: FrameState, T: list[np.ndarray], target: FrameTarget) -> list[PlacedSprite]:
    """``Render.addSprites``: every sprite in front of the camera, drawn into the stars layers T (in place); returns the placed list."""
    placed: list[PlacedSprite] = []
    for j, e in enumerate(state.sc.sprites):
        q = place_sprite(state, e, j)
        if q is not None:
            placed.append(q)
    # a star partly out of the frame or behind a nearer one casts glow and spikes by what is seen of it;
    # eased in over the clip's first tenth, so frame 0 stays the image
    seen_shares(placed, target, smoothstep(0.0, 0.1, state.opts.t or 0))
    for q in placed:
        draw_placed(state, T, q, target)
    return placed


@dataclass
class _HeadroomMap:
    """The headroom map being painted, and what each star's bump needs: the frame target, the brightest catalogue G
    (``g_bright``), the HDR peak over SDR white (``peak``) and the scene's pixel scale (``px``)."""

    the_map: np.ndarray
    target: FrameTarget
    g_bright: float
    peak: float
    px: float

    def reach(self, gain: float) -> float:
        """How far a star of this gain paints: grows with its brightness (only pixels above the knee change, so it can be generous)."""
        return self.px * G.HEADROOM_RADIUS * (1 + 3 * (gain - 1) / max(1e-6, self.peak - 1))

    def paint(self, x: float, y: float, r: float, gain: float) -> None:
        """One star's bump, centred at (x, y) on the frame's camera and of radius r, painted into the map by maximum."""
        cam, out_w, out_h = self.target.cam, self.target.out_w, self.target.out_h
        u = cam.to_u(x)
        v = cam.to_v(y)
        sigma = max(0.5, G.HEADROOM_SIGMA * r / cam.fx)
        ro = 3 * sigma
        y0 = max(0, math.floor(v - ro))
        y1 = min(out_h - 1, math.ceil(v + ro))
        x0 = max(0, math.floor(u - ro))
        x1 = min(out_w - 1, math.ceil(u + ro))
        if y1 < y0 or x1 < x0:
            return
        yy, xx = np.meshgrid(np.arange(y0, y1 + 1, dtype=np.float64), np.arange(x0, x1 + 1, dtype=np.float64), indexing="ij")
        dist = np.sqrt((xx - u) * (xx - u) + (yy - v) * (yy - v))
        f = G.headroom_bump_arr(dist, sigma, gain)
        box = self.the_map[y0:y1 + 1, x0:x1 + 1]
        np.maximum(box, f.astype(np.float32), out=box)


def _headroom_stars(sc: Any) -> list[dict[str, Any]]:
    """The stars the map paints: the scene's catalogue, else the sprites' own stars at their backdrop positions."""
    if sc.catalogue:
        return list(sc.catalogue)
    return [{"x": e.p0.x if e.p0 else 0.0, "y": e.p0.y if e.p0 else 0.0, "G": (e.s.source or {}).get("G")}
            for e in sc.sprites]


def _brightest_g(sc: Any, stars: list[dict[str, Any]]) -> float:
    """The brightest finite G of the stars (cached on the scene as ``gBright``); inf when none has one."""
    if "gBright" not in sc.cache:
        gs = [_cat_field(st, "G") for st in stars]
        gs = [g for g in gs if _finite(g)]
        sc.cache["gBright"] = min(gs) if gs else math.inf
    return sc.cache["gBright"]


def _paint_moving_stars(hm: _HeadroomMap, placed: list[PlacedSprite]) -> dict[str, bool]:
    """The placed (moving) stars: each paints its gain where it is drawn. Returns the star keys they cover."""
    moving: dict[str, bool] = {}
    for q in placed:
        src = q.sp.source or {}
        gain = G.magnitude_gain(src["G"], hm.g_bright, hm.peak) if _finite(src.get("G")) else 1.0
        moving[star_key(src)] = True
        if gain > 1:
            hm.paint(q.cx, q.cy, max(hm.reach(gain), q.rc * max(1.0, q.g) + 2 * hm.px), gain)
    return moving


def _paint_catalogue_stars(hm: _HeadroomMap, sc: Any, stars: list[dict[str, Any]], moving: dict[str, bool], K: float) -> None:
    """Every other catalogued star paints its gain where the zooming backdrop carries it (the backdrop zoom K)."""
    for st in stars:
        gval = _cat_field(st, "G")
        if not _finite(gval) or moving.get(star_key(st), False):
            continue
        gain = G.magnitude_gain(gval, hm.g_bright, hm.peak)
        if gain > 1:
            x, y = _cat_field(st, "x"), _cat_field(st, "y")
            hm.paint(sc.tp.x + (x - sc.tp.x) * K, sc.tp.y + (y - sc.tp.y) * K, hm.reach(gain) * K, gain)


def headroom_map(sc: Any, placed: list[PlacedSprite], opts: Any, target: FrameTarget) -> np.ndarray:
    """``Render.headroomMap``: per output pixel, the HDR peak factor: every catalogued star (moving where drawn, still where the backdrop carries it) paints its magnitude's gain.

    Elsewhere 1. The peak is the chosen one over SDR white (HDR_REFERENCE_WHITE).
    """
    stars = _headroom_stars(sc)
    hm = _HeadroomMap(the_map=np.ones((target.out_h, target.out_w), dtype=np.float32), target=target,
                      g_bright=_brightest_g(sc, stars), peak=(opts.peak or HDR_PEAK_DEFAULT) / G.HDR_REFERENCE_WHITE,
                      px=sc.px_scale or 1.0)
    moving = _paint_moving_stars(hm, placed)
    _paint_catalogue_stars(hm, sc, stars, moving, opts.K or 1.0)
    return hm.the_map
