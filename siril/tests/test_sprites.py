"""Sprite drawing (E5: loomsiril/sprites.py) against the JS oracle and a per-pixel reference.

Oracle: tests/oracle/frame.json (tools/oracle/frame.js) records the pieces of Render.js on a
48 x 32 scene with three hand-made sprites: spriteBounds, coreRadius, placeSprite, shutterPath,
seenShares, splitPatches, glowPatch and addSprites into zero planes. The scene's inputs are
stored in the same JSON, so nothing here rebuilds an input with a formula.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from loomsiril import geometry as G
from loomsiril.core import Point, Rect, tan_project
from loomsiril.frame import RenderOptions
from loomsiril.resample import patch_sample
from loomsiril.sprites import (
    Camera,
    CoreSprite,
    DrawHow,
    FrameState,
    FrameTarget,
    SpikeHow,
    SpriteDraw,
    _grouped_sums,
    _sprite_context,
    _sample_sums,
    add_sprites,
    core_radius,
    draw_sprites,
    footprint_pays,
    glow_patch,
    place_sprite,
    placed_channels,
    sample_groups,
    seen_shares,
    shutter_path,
    split_patches,
    sprite_bounds,
)
from loomsiril.types import Detection, Placed, Scene, SceneSprite, Sprite, StarModel

TOL = 1.0 / 65535


# ---------------------------------------------------------------------------
# The oracle scene, rebuilt from the recorded inputs.
# ---------------------------------------------------------------------------


def _sprite_from(d: dict[str, Any], j: int) -> Sprite:
    rect = Rect(**{k: int(v) for k, v in d["rect"].items()})
    det_rect = Rect(**{k: int(v) for k, v in d["det"]["rect"].items()})
    model = d["det"]["model"]
    star_model = None
    if model is not None:
        star_model = StarModel(x=d["det"]["x"], y=d["det"]["y"], core=float(model["core"]), prof=np.zeros(1),
                               rmax=0.0, spikes=[], col=[1.0], support=0.0)
    det = Detection(index=j, x=float(d["det"]["x"]), y=float(d["det"]["y"]), flux=1.0, rect=det_rect, model=star_model)
    rh, rw = rect.height, rect.width
    src = d["source"]
    return Sprite(source=src, d=float(d["d"]), placed=Placed(source=src, d=float(d["d"]), x=det.x, y=det.y), det=det,
                  rect=rect, radius=0.0, centre=Point(det.x, det.y), mask=None,
                  pixels=[np.asarray(p, dtype=np.float32).reshape(rh, rw) for p in d["pixels"]],
                  model_core=[np.asarray(p, dtype=np.float32).reshape(rh, rw) for p in d["modelCore"]],
                  model_spikes=[np.asarray(p, dtype=np.float32).reshape(rh, rw) for p in d["modelSpikes"]],
                  spike_angles=[float(a) for a in d["spikeAngles"]], px_scale=float(d.get("pxScale", 1.0)))


def _projector(wcs: dict[str, Any]):
    def project(ra: float, dec: float):
        return tan_project(ra, dec, wcs)
    return project


def _scene(oracle: Any) -> tuple[Scene, dict[str, Any]]:
    sc = oracle("frame")["cases"]["scene"]
    wcs = sc["wcs"]
    project = _projector(wcs)
    sprites = [_sprite_from(d, j) for j, d in enumerate(sc["sprites"])]
    S = [np.asarray(p, dtype=np.float32).reshape(sc["h"], sc["w"]) for p in sc["S"]]
    R = [np.asarray(p, dtype=np.float32).reshape(sc["h"], sc["w"]) for p in sc["R"]]
    scene = Scene(w=sc["w"], h=sc["h"], nc=sc["nc"], S=S, R=R,
                  sprites=[SceneSprite(s=s, p0=project(s.source["ra"], s.source["dec"])) for s in sprites],
                  project=project, target=sc["target"], tp=project(sc["target"]["ra"], sc["target"]["dec"]),
                  D=float(sc["D"]))
    return scene, sc


def _opts(d: dict[str, Any], **over: Any) -> RenderOptions:
    base = dict(travel=float(d["travel"]), easing=d["easing"], growth=float(d["growth"]), brightening=bool(d["brightening"]),
                twinkle=float(d["twinkle"]), duration=float(d["duration"]), frame_dt=float(d["frameDt"]))
    base.update(over)
    return RenderOptions(**base)


def _cam(c: dict[str, Any]) -> Camera:
    return Camera(x=float(c["x"]), y=float(c["y"]), fx=float(c["fx"]), fy=float(c["fy"]))


# ---------------------------------------------------------------------------
# Oracle replay.
# ---------------------------------------------------------------------------


def test_sprite_bounds_matches_js(oracle: Any) -> None:
    scene, _ = _scene(oracle)
    cases = oracle("frame")["cases"]["spriteBounds"]
    for case in cases:
        j, cx, cy, g, cam, out_w, out_h = case["args"]
        got = sprite_bounds(scene.sprites[j].s, cx, cy, g, FrameTarget(_cam(cam), out_w, out_h))
        assert got == case["out"], (j, got, case["out"])


def test_sprite_bounds_clipped_matches_js(oracle: Any) -> None:
    scene, _ = _scene(oracle)
    for case in oracle("frame")["cases"]["spriteBoundsClipped"]:
        j, cx, cy, g, cam, out_w, out_h = case["args"]
        got = sprite_bounds(scene.sprites[j].s, cx, cy, g, FrameTarget(_cam(cam), out_w, out_h))
        assert got == case["out"]
        assert 0 <= got["u0"] and got["u1"] <= out_w - 1 and 0 <= got["v0"] and got["v1"] <= out_h - 1


def test_core_radius_matches_js(oracle: Any) -> None:
    scene, _ = _scene(oracle)
    expected = oracle("frame")["cases"]["coreRadius"]
    assert [core_radius(e.s) for e in scene.sprites] == pytest.approx(expected, abs=1e-12)


def test_place_sprite_matches_js(oracle: Any) -> None:
    scene, sc = _scene(oracle)
    opts_json = sc["frameOpts"]
    for t, block in zip([0.0, 0.3, 1.0], oracle("frame")["cases"]["placeSprite"]):
        s = float(opts_json["travel"]) * _ease_smoothstep(t)
        opts = _opts(opts_json, t=t, K=1.0, coarse_mips=False)
        for j, want in enumerate(block):
            q = place_sprite(FrameState(scene, s, opts), scene.sprites[j], j)
            if want is None:
                assert q is None
                continue
            assert q is not None
            assert q.g == pytest.approx(want["g"], abs=1e-12)
            assert q.k_outer == pytest.approx(want["kOuter"], abs=1e-12)
            assert q.k_core == pytest.approx(want["kCore"], abs=1e-12)
            assert q.rc == pytest.approx(want["rc"], abs=1e-12)
            assert q.cx == pytest.approx(want["cx"], abs=1e-9)
            assert q.cy == pytest.approx(want["cy"], abs=1e-9)
            assert q.alpha == pytest.approx(want["alpha"], abs=1e-12)
            assert q.spike_length == pytest.approx(want["spikeLength"], abs=1e-12)
            assert q.m["ratio"] == pytest.approx(want["ratio"], rel=1e-12)


def _ease_smoothstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def test_shutter_path_matches_js(oracle: Any) -> None:
    scene, sc = _scene(oracle)
    case = oracle("frame")["cases"]["shutterPath"]
    j, t, travel, frame_dt = case["args"]
    opts = _opts(sc["frameOpts"], t=t, K=1.0, motion_blur=True, frame_dt=frame_dt, travel=travel, coarse_mips=False)
    s = travel * _ease_smoothstep(t)
    state = FrameState(scene, s, opts)
    q = place_sprite(state, scene.sprites[j], j)
    path = shutter_path(state, q, FrameTarget(Camera(0.0, 0.0, 1.5, 1.5), scene.w, scene.h))
    assert len(path) == len(case["out"]) > 1
    for p, want in zip(path, case["out"]):
        assert p.x == pytest.approx(want["x"], abs=1e-9)
        assert p.y == pytest.approx(want["y"], abs=1e-9)


def test_shutter_path_still_is_one_point() -> None:
    """Without motion blur, or on the first frame, a star is drawn once where it is."""
    scene = _tiny_scene()
    target = FrameTarget(Camera(0, 0, 1, 1), scene.w, scene.h)
    for opts in (RenderOptions(travel=10.0, t=0.0, motion_blur=True, frame_dt=0.1),
                 RenderOptions(travel=10.0, t=0.5, motion_blur=False, frame_dt=0.1)):
        state = FrameState(scene, 0.0, opts)
        q = replace(place_sprite(state, scene.sprites[0], 0), cx=3.0, cy=4.0)
        assert shutter_path(state, q, target) == [Point(3.0, 4.0)]


def test_seen_shares_matches_js(oracle: Any) -> None:
    scene, sc = _scene(oracle)
    case = oracle("frame")["cases"]["seenShares"]
    args = case["args"]
    t = args["t"]
    s = float(sc["frameOpts"]["travel"]) * _ease_smoothstep(t)
    opts = _opts(sc["frameOpts"], t=t, K=1.0, coarse_mips=False)
    placed = []
    for j, e in enumerate(scene.sprites):
        q = place_sprite(FrameState(scene, s, opts), e, j)
        if q is not None:
            placed.append(q)
    seen_shares(placed, FrameTarget(Camera(0.0, 0.0, 1.5, 1.5), args["outW"], args["outH"]), args["ease"])
    assert [q.j for q in placed] == [w["j"] for w in case["out"]]
    for q, want in zip(placed, case["out"]):
        assert q.seen == pytest.approx(want["seen"], abs=1e-12)
        assert q.u == pytest.approx(want["u"], abs=1e-9)
        assert q.v == pytest.approx(want["v"], abs=1e-9)
        assert q.r == pytest.approx(want["r"], abs=1e-12)


def test_split_patches_matches_js(oracle: Any) -> None:
    scene, _ = _scene(oracle)
    case = oracle("frame")["cases"]["splitPatches"]
    sp = scene.sprites[0].s
    parts = split_patches(sp, 0, float(case["args"]["rc"]), sp.pixels[0])
    want = case["out"]
    x0, y0, x1, y1 = (want["coreRect"][k] for k in ("x0", "y0", "x1", "y1"))
    assert (parts.core_sprite.rect.x0, parts.core_sprite.rect.y0, parts.core_sprite.rect.x1, parts.core_sprite.rect.y1) == (x0, y0, x1, y1)
    assert np.allclose(parts.photo_core.ravel(), want["photoCore"], atol=1e-6)
    assert np.allclose(parts.model_core.ravel(), want["modelCore"], atol=1e-6)
    assert np.allclose(parts.model_outer.ravel(), want["modelOuter"], atol=1e-6)


def test_glow_patch_matches_js(oracle: Any) -> None:
    scene, _ = _scene(oracle)
    want = oracle("frame")["cases"]["glowPatch"]["out"]
    sp = scene.sprites[1].s
    assert np.allclose(glow_patch(sp, 0).ravel(), want, atol=1e-6)


def test_add_sprites_into_zero_planes_matches_js(oracle: Any) -> None:
    scene, sc = _scene(oracle)
    for block in oracle("frame")["cases"]["addSprites"]:
        t = block["t"]
        s = float(sc["frameOpts"]["travel"]) * _ease_smoothstep(t)
        opts = _opts(sc["frameOpts"], t=t, K=1.0, coarse_mips=False)
        T = [np.zeros((scene.h, scene.w), dtype=np.float32)]
        add_sprites(FrameState(scene, s, opts), T, FrameTarget(Camera(0.0, 0.0, 1.0, 1.0), scene.w, scene.h))
        assert np.max(np.abs(T[0].ravel().astype(np.float64) - np.asarray(block["T"]))) <= TOL


# ---------------------------------------------------------------------------
# Vectorised draw against a per-pixel reference (a port of spritePixels, a few pixels checked).
# ---------------------------------------------------------------------------


def _ref_glow_n(ctx: dict[str, Any], dx: float, dy: float) -> int:
    """Render.glowSamples for one glow pixel (the JS rule, scalar): 0 is the full count."""
    if not ctx["radial"]:
        return 0
    cam = ctx["cam"]
    h = 0.5 * math.sqrt(cam.fx * cam.fx + cam.fy * cam.fy)
    ro = math.sqrt(dx * dx + dy * dy) - h
    if ro <= ctx["rc"]:
        return 0
    stretch = max(G.radial_source(ro, ctx["rc"], ctx["g"], ctx["seen"]) / ro,
                  1 / (ctx["g"] * max(0.05, ctx["seen"])))
    span = max(cam.fx, cam.fy) * stretch / ctx["f2"]
    n = max(1, math.ceil(span - 1e-9))
    return 0 if n >= max(ctx["nx"], ctx["ny"]) else n


def _ref_pixel(ctx: dict[str, Any], u: int, v: int, n: int = 0) -> list[float]:
    """Render.spritePixels for one output pixel, scalar, straight from the JS (n: the footprint count, 0 the full one)."""
    cam = ctx["cam"]
    nc = len(ctx["mps"])
    nx = min(n, ctx["nx"]) if n else ctx["nx"]
    ny = min(n, ctx["ny"]) if n else ctx["ny"]
    sums = [0.0] * nc
    for sy in range(ny):
        dy = cam.y + (v + (sy + 0.5) / ny) * cam.fy - 0.5 - ctx["cy"]
        for sx in range(nx):
            dx = cam.x + (u + (sx + 0.5) / nx) * cam.fx - 0.5 - ctx["cx"]
            if ctx["axes"] is not None:
                best, best_lat = None, math.inf
                for a in ctx["axes"]:
                    along = dx * a[0] + dy * a[1]
                    lat = -dx * a[1] + dy * a[0]
                    if along > 0 and abs(lat) < best_lat:
                        best_lat, best = abs(lat), (a, along, lat)
                if best is None:
                    continue
                a, along, lat = best
                al = along if along <= ctx["rc"] else ctx["rc"] + (along - ctx["rc"]) / ctx["stretch"]
                ax_, ay_ = al * a[0] - lat * a[1], al * a[1] + lat * a[0]
                for ch in range(nc):
                    sums[ch] += ctx["ks"][ch] * float(patch_sample(ctx["mps"][ch], ctx["rw"], ctx["rh"],
                                                                   (ctx["ox"] + ax_ + 0.5) / ctx["f2"] - 0.5,
                                                                   (ctx["oy"] + ay_ + 0.5) / ctx["f2"] - 0.5))
            else:
                f = 1 / ctx["g"]
                sm = -1.0
                if ctx["radial"]:
                    ro = math.sqrt(dx * dx + dy * dy)
                    f = G.radial_source(ro, ctx["rc"], ctx["g"], ctx["seen"]) / ro if ro > 0 else 1.0
                    t = min(1.0, max(0.0, ro / ctx["rc"]))
                    sm = t * t * (3 - 2 * t)
                px = (ctx["ox"] + dx * f + 0.5) / ctx["f2"] - 0.5
                py = (ctx["oy"] + dy * f + 0.5) / ctx["f2"] - 0.5
                for ch in range(nc):
                    w = ctx["ks"][ch] if sm < 0 else ctx["ks"][ch] + (ctx["kos"][ch] - ctx["ks"][ch]) * sm
                    sums[ch] += w * float(patch_sample(ctx["mps"][ch], ctx["rw"], ctx["rh"], px, py))
    return [s_ / (nx * ny) for s_ in sums]


def _ref_context(sp: Sprite, cam: Camera, cx: float, cy: float, g: float, ks: list[float],
                 k_outers: list[float], rc: float, how: DrawHow | None, patches: list[np.ndarray]) -> dict[str, Any]:
    """The reference's view of a draw: the same mip planes and offsets as the port's context, read as the JS reads them."""
    ctx = _sprite_context(SpriteDraw(patches, sp, cx, cy, g, ks, k_outers, rc, how), cam)
    return {"cam": cam, "mps": ctx.mps, "nx": ctx.nx, "ny": ctx.ny, "cx": cx, "cy": cy, "rc": rc, "ks": ctx.ks,
            "kos": ctx.k_os, "g": g, "ox": ctx.ox, "oy": ctx.oy, "f2": ctx.f2, "radial": ctx.radial, "seen": ctx.seen,
            "axes": ctx.axes.tolist() if ctx.axes is not None else None, "stretch": ctx.stretch,
            "rw": sp.rect.width, "rh": sp.rect.height}


def _make_sprite(rw: int, rh: int, seed: int, spikes: bool) -> Sprite:
    rng = np.random.default_rng(seed)
    x0, y0 = 5, 6
    rect = Rect(x0, y0, x0 + rw, y0 + rh)
    det = Detection(index=0, x=x0 + rw / 2 + 0.3, y=y0 + rh / 2 - 0.2, flux=1.0, rect=Rect(x0, y0, x0 + rw, y0 + rh))
    yy, xx = np.mgrid[0:rh, 0:rw].astype(np.float64)
    g = np.exp(-((xx - (det.x - x0)) ** 2 + (yy - (det.y - y0)) ** 2) / 4.0).astype(np.float32)
    g = (g + 0.01 * rng.random((rh, rw))).astype(np.float32)
    spike_plane = np.zeros((rh, rw), dtype=np.float32)
    angles: list[float] = []
    if spikes:
        angles = [0.0, math.pi / 2]
        dx = xx - (det.x - x0)
        dy = yy - (det.y - y0)
        for a in angles:
            along = dx * math.cos(a) + dy * math.sin(a)
            lat = -dx * math.sin(a) + dy * math.cos(a)
            spike_plane += (0.2 * np.exp(-lat * lat / 0.5) * np.exp(-np.maximum(along, 0) / 6) * (along > 0)).astype(np.float32)
    pixels = (g + spike_plane).astype(np.float32)
    return Sprite(source={"ra": 10.0, "dec": 20.0, "G": 8.0}, d=200.0, placed=Placed(source={}, d=200.0, x=0, y=0),
                  det=det, rect=rect, radius=0.0, centre=Point(det.x, det.y), mask=None,
                  pixels=[pixels], model_core=[g], model_spikes=[spike_plane], spike_angles=angles, px_scale=1.0)


def _tiny_scene() -> Scene:
    sp = _make_sprite(9, 9, 1, spikes=False)
    return Scene(w=32, h=24, nc=1, S=[np.zeros((24, 32), np.float32)], R=[np.zeros((24, 32), np.float32)],
                 sprites=[SceneSprite(s=sp, p0=Point(sp.det.x, sp.det.y))], project=lambda ra, dec: Point(0, 0),
                 target={"ra": 0.0, "dec": 0.0}, tp=Point(0, 0), D=900.0)


@pytest.mark.parametrize("spikes", [False, True])
def test_draw_sprites_equals_per_pixel_reference(spikes: bool) -> None:
    """The vectorised draw equals the scalar JS formulas pixel by pixel (within 1e-6), glow and spikes."""
    sp = _make_sprite(13, 11, 3, spikes=spikes)
    cam = Camera(x=2.0, y=1.0, fx=1.5, fy=1.5)
    cx, cy = sp.det.x + 1.2, sp.det.y - 0.7
    g, rc = 1.25, 1.0
    ks = [1.0]
    how = DrawHow(seen=1.0, spike=SpikeHow(length=1.5, angles=sp.spike_angles)) if spikes else DrawHow(seen=0.8)
    out_w, out_h = 24, 20
    acc = [np.zeros((out_h, out_w), dtype=np.float32)]
    patches = [sp.model_spikes[0] if spikes else sp.pixels[0]]
    draw_sprites(acc, FrameTarget(cam, out_w, out_h), SpriteDraw(patches, sp, cx, cy, g, ks, [ks[0]], rc, how))
    ref = _ref_context(sp, cam, cx, cy, g, ks, [ks[0]], rc, how, patches)
    assert ref["f2"] == 1                     # no mip level at this scale: the planes are the patches
    b = sprite_bounds(sp, cx, cy, max(g, ref["stretch"]), FrameTarget(cam, out_w, out_h))
    worst = 0.0
    for v in range(b["v0"], b["v1"] + 1):
        for u in range(b["u0"], b["u1"] + 1):
            want = _ref_pixel(ref, u, v)[0]
            worst = max(worst, abs(float(acc[0][v, u]) - want))
    assert worst <= 1e-6, worst


def test_draw_sprites_footprint_equals_per_pixel_reference() -> None:
    """High/Medium: pixels grouped by their footprint count give the JS per-pixel result (its samples a side)."""
    sp = _make_sprite(15, 15, 7, spikes=False)
    cam = Camera(x=0.0, y=0.0, fx=3.0, fy=3.0)
    cx, cy = sp.det.x, sp.det.y
    g, rc, ks = 3.0, 2.0, [1.0]
    how = DrawHow(seen=1.0, footprint=True, coarse=False)
    out_w, out_h = 10, 10
    acc = [np.zeros((out_h, out_w), dtype=np.float32)]
    patches = [sp.pixels[0]]
    draw_sprites(acc, FrameTarget(cam, out_w, out_h), SpriteDraw(patches, sp, cx, cy, g, ks, [ks[0]], rc, how))
    ref = _ref_context(sp, cam, cx, cy, g, ks, [ks[0]], rc, how, patches)
    assert footprint_pays(_sprite_context(SpriteDraw(patches, sp, cx, cy, g, ks, [ks[0]], rc, how), cam))
    b = sprite_bounds(sp, cx, cy, max(g, ref["stretch"]), FrameTarget(cam, out_w, out_h))
    counts = set()
    worst = 0.0
    for v in range(b["v0"], b["v1"] + 1):
        for u in range(b["u0"], b["u1"] + 1):
            dx = cam.x + (u + 0.5) * cam.fx - 0.5 - cx
            dy = cam.y + (v + 0.5) * cam.fy - 0.5 - cy
            n = _ref_glow_n(ref, dx, dy)
            counts.add(n)
            want = _ref_pixel(ref, u, v, n)[0]
            worst = max(worst, abs(float(acc[0][v, u]) - want))
    assert len(counts) > 1, counts            # the test exercises more than one count
    assert worst <= 1e-6, worst


def _old_sample_groups(nx_p: np.ndarray, ny_p: np.ndarray) -> list[tuple[int, int, np.ndarray]]:
    """Private reference: the grouping draw_sprites used before E5b (one np.unique per call, then a mask per pair)."""
    out: list[tuple[int, int, np.ndarray]] = []
    for pair in np.unique(np.stack([nx_p, ny_p], axis=1), axis=0):
        nxg, nyg = int(pair[0]), int(pair[1])
        out.append((nxg, nyg, np.nonzero((nx_p == nxg) & (ny_p == nyg))[0]))
    return out


def _old_grouped_sums(ctx: Any, uu: np.ndarray, vv: np.ndarray, nx_p: np.ndarray, ny_p: np.ndarray) -> np.ndarray:
    """Private reference: the pre-E5b loop over np.unique pairs, verbatim in its arithmetic."""
    sums = np.zeros((len(ctx.mps), uu.size), dtype=np.float64)
    for nxg, nyg, sel in _old_sample_groups(nx_p, ny_p):
        part = _sample_sums(ctx, uu[sel], vv[sel], nxg, nyg)
        sums[:, sel] = part / (nxg * nyg)
    return sums


@pytest.mark.parametrize("seed", range(6))
def test_sample_groups_equal_np_unique_grouping(seed: int) -> None:
    """The one-pass grouping gives the same pairs, in the same order, with the same pixels as np.unique did."""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 400))
    nx_p = rng.integers(1, 7, size=n).astype(np.int64)
    ny_p = rng.integers(1, 5, size=n).astype(np.int64)
    got = sample_groups(nx_p, ny_p)
    want = _old_sample_groups(nx_p, ny_p)
    assert [(a, b) for a, b, _ in got] == [(a, b) for a, b, _ in want]
    for (_, _, g_sel), (_, _, w_sel) in zip(got, want):
        assert np.array_equal(np.sort(g_sel), w_sel)
    assert sum(g.size for _, _, g in got) == n     # every pixel in exactly one group


@pytest.mark.parametrize("seed", range(4))
def test_grouped_sums_equal_old_grouping_on_random_inputs(seed: int) -> None:
    """Footprint sums with random per-pixel sample counts are bit-identical to the old np.unique grouping."""
    rng = np.random.default_rng(100 + seed)
    sp = _make_sprite(15, 15, 20 + seed, spikes=False)
    cam = Camera(x=0.0, y=0.0, fx=12.0, fy=12.0)     # several samples per output pixel
    how = DrawHow(seen=1.0, footprint=True, coarse=False)
    patches = [sp.pixels[0], sp.pixels[0] * 0.5]
    ctx = _sprite_context(SpriteDraw(patches, sp, sp.det.x, sp.det.y, 3.0, [1.0, 0.7], [1.0, 0.7], 2.0, how), cam)
    assert ctx.nx >= 2 and ctx.ny >= 2     # several sample counts to group
    n = int(rng.integers(20, 300))
    uu = rng.integers(0, 12, size=n).astype(np.int64)
    vv = rng.integers(0, 12, size=n).astype(np.int64)
    nx_p = rng.integers(1, ctx.nx + 1, size=n).astype(np.int64)
    ny_p = rng.integers(1, ctx.ny + 1, size=n).astype(np.int64)
    got = _grouped_sums(ctx, uu, vv, nx_p, ny_p)
    want = _old_grouped_sums(ctx, uu, vv, nx_p, ny_p)
    assert got.shape == (2, n)
    assert np.array_equal(got, want)


def test_draw_sprites_outside_bounds_untouched() -> None:
    sp = _make_sprite(9, 9, 5, spikes=False)
    acc = [np.zeros((30, 30), dtype=np.float32)]
    draw_sprites(acc, FrameTarget(Camera(0, 0, 1, 1), 30, 30), SpriteDraw([sp.pixels[0]], sp, sp.det.x, sp.det.y, 1.0, [1.0], [1.0], 2.0, None))
    touched = np.argwhere(acc[0] != 0)
    b = sprite_bounds(sp, sp.det.x, sp.det.y, 1.0, FrameTarget(Camera(0, 0, 1, 1), 30, 30))
    assert touched.size > 0
    assert touched[:, 0].min() >= b["v0"] and touched[:, 0].max() <= b["v1"]
    assert touched[:, 1].min() >= b["u0"] and touched[:, 1].max() <= b["u1"]


def test_core_radius_for_core_zero_uses_rect() -> None:
    """A point model (core 0) draws with its detection box: half its width, at least 1 px."""
    sp = _make_sprite(9, 9, 2, spikes=False)
    sp.det.model = StarModel(x=0, y=0, core=0.0, prof=np.zeros(1), rmax=0.0, spikes=[], col=[1.0], support=0.0)
    assert core_radius(sp) == pytest.approx(max(1.0, (sp.det.rect.x1 - sp.det.rect.x0) / 2))
    sp.det.model = None
    assert core_radius(sp) == pytest.approx(max(1.0, (sp.det.rect.x1 - sp.det.rect.x0) / 2))
    sp.det.model = StarModel(x=0, y=0, core=2.5, prof=np.zeros(1), rmax=0.0, spikes=[], col=[1.0], support=0.0)
    assert core_radius(sp) == pytest.approx(2.5)


def test_empty_model_spikes_takes_the_core_path() -> None:
    """Render.placedChannels tests modelSpikes for truthiness: an empty array is truthy in JS, so modelSpikes[cc]
    (undefined) is taken: the channel has no spikes, its glow is the photograph, and nothing is drawn as spikes."""
    scene = _tiny_scene()
    e = scene.sprites[0]
    sp = e.s
    sp.model_spikes = []
    opts = RenderOptions(travel=10.0, t=0.0, K=1.0)
    q = place_sprite(FrameState(scene, 0.0, opts), e, 0)
    assert q is not None
    ch = placed_channels(q, opts, 0.0, 1, 0.0)
    assert ch["spikes"] == [None]
    assert np.array_equal(ch["glow"][0], sp.pixels[0])
    T = [np.zeros((scene.h, scene.w), dtype=np.float32)]
    add_sprites(FrameState(scene, 0.0, opts), T, FrameTarget(Camera(0, 0, 1, 1), scene.w, scene.h))
    assert np.all(np.isfinite(T[0])) and float(T[0].max()) > 0.0


def test_placed_channels_with_one_spikes_entry_for_two_channels() -> None:
    """Two channels, one model_spikes entry: channel 0 has its spikes, channel 1 (index past the end, JS undefined)
    has none, its glow is its photograph, and nothing raises."""
    scene = _tiny_scene()
    e = scene.sprites[0]
    sp = e.s
    base = sp.pixels[0]
    sp.pixels = [base, base * 0.5]
    sp.spike_angles = [0.0]
    sp.model_spikes = [np.full_like(base, 0.1)]
    opts = RenderOptions(travel=10.0, t=0.0, K=1.0)
    q = place_sprite(FrameState(scene, 0.0, opts), e, 0)
    assert q is not None
    ch = placed_channels(q, opts, 0.0, 2, 0.0)
    assert ch["spikes"][0] is not None and ch["spikes"][1] is None
    assert np.array_equal(ch["glow"][1], sp.pixels[1])


def test_split_patches_core_zero_has_no_core_part() -> None:
    """With core radius 0 the whole model is outside the core (the core share is 0 at the centre)."""
    sp = _make_sprite(9, 9, 4, spikes=False)
    parts = split_patches(sp, 0, 0.0, sp.pixels[0])
    assert isinstance(parts.core_sprite, CoreSprite)
    assert parts.model_outer.shape == (9, 9)
