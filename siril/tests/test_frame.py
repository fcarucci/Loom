"""The frame renderer (E5: loomsiril/frame.py) on synthetic scenes and against the JS oracle.

Oracle: tests/oracle/frame.json, the frames of Render.frame on the 48 x 32 hand-made scene
(t = 0, 0.3, 1 at 32 x 18; Medium, High and motion blur; the HDR headroom map at t = 0.3 and 1;
PQ frames at t = 0 and 1 with the transform's MaxCLL/MaxFALL). The behaviour checks port the
intent of selftest.js fly.render (frame 0 equals the input; a near star moves and stays in
frame and lands on its projection; a galaxy's backdrop does not move; a passed star is not
drawn) and fly.hdr (headroom only for PQ and HLG; an approaching star rises above SDR white),
each on a synthetic scene built here, as the PixInsight scene of the JS cannot be.

The perf tests (640 x 360, 30 sprites; 1920 x 1080, 20 stars in 3 channels, highest) run only with
LOOMSIRIL_PERF=1, which the Docker image sets: they never run locally.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace
from typing import Any, Optional

import numpy as np
import pytest

from loomsiril import geometry as G
from loomsiril.colour import SRGB_COLOUR, output_transform, pq_encode
from loomsiril.core import Point, Rect, screen, tan_project, tan_unproject
from loomsiril.frame import RenderOptions, frame, frame_image, scene_for
from loomsiril.sprites import Camera, FrameState, FrameTarget, add_sprites, headroom_map, place_sprite
from loomsiril.types import Detection, Placed, Scene, SceneSprite, Sprite, StarModel

from test_sprites import _opts, _scene  # the oracle scene, rebuilt from the recorded inputs

TOL = 1.0 / 65535

WCS = {"crval1": 324.745, "crval2": 57.514, "crpix1": 20.5, "crpix2": 14.5, "cd": [-0.0005, 0.00001, 0.00002, 0.0005]}


def _project(ra: float, dec: float) -> Optional[Point]:
    return tan_project(ra, dec, WCS)


def _sprite(det_x: float, det_y: float, rect: Rect, pixels: list[np.ndarray], d: float, G_mag: float = 8.0,
            core: float = 0.0, model_core: Optional[list[np.ndarray]] = None) -> Sprite:
    """A hand-made sprite whose catalogue position is the pixel it is detected at (so frame 0 draws it there)."""
    u = tan_unproject(det_x, det_y, WCS)
    source = {"ra": u["ra"], "dec": u["dec"], "G": G_mag}
    det_rect = Rect(rect.x0, rect.y0, rect.x1, rect.y1)
    model = StarModel(x=det_x, y=det_y, core=core, prof=np.zeros(1), rmax=0.0, spikes=[], col=[1.0], support=0.0) if core > 0 else None
    det = Detection(index=0, x=det_x, y=det_y, flux=1.0, rect=det_rect, model=model)
    # a smooth model of the star: by default the photograph itself (a zero model would remove the core up close)
    mc = model_core if model_core is not None else pixels
    return Sprite(source=source, d=d, placed=Placed(source=source, d=d, x=det_x, y=det_y), det=det, rect=rect, radius=0.0,
                  centre=Point(det_x, det_y), mask=None, pixels=[p.astype(np.float32) for p in pixels],
                  model_core=[p.astype(np.float32) for p in mc], model_spikes=[np.zeros_like(p, dtype=np.float32) for p in pixels],
                  spike_angles=[], px_scale=1.0)


def _gauss_patch(rect: Rect, cx: float, cy: float, sigma: float, peak: float) -> np.ndarray:
    yy, xx = np.mgrid[rect.y0:rect.y1, rect.x0:rect.x1].astype(np.float64)
    return (peak * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma * sigma))).astype(np.float32)


def _scene_of(w: int, h: int, S: list[np.ndarray], R: list[np.ndarray], sprites: list[Sprite], D: float,
              target_xy: tuple[float, float] = (12.0, 9.0)) -> Scene:
    t = tan_unproject(target_xy[0], target_xy[1], WCS)
    target = {"ra": t["ra"], "dec": t["dec"]}
    tp = _project(target["ra"], target["dec"])
    return Scene(w=w, h=h, nc=len(S), S=[np.asarray(p, dtype=np.float32) for p in S],
                 R=[np.asarray(p, dtype=np.float32) for p in R],
                 sprites=[SceneSprite(s=s, p0=_project(s.source["ra"], s.source["dec"])) for s in sprites],
                 project=_project, target=target, tp=tp, D=D)


def _synthetic_backdrop(w: int, h: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    return (0.1 + 0.3 * xx / w + 0.2 * yy / h + 0.05 * rng.random((h, w))).astype(np.float32)


def _two_sprites(w: int, h: int) -> tuple[list[Sprite], np.ndarray]:
    """Two bright sprites in a w x h frame; returns them and the expected stars layer at frame 0 (their pixels pasted)."""
    specs = [(9.3, 7.6, 2.0, 0.6, 180.0), (21.4, 9.8, 1.6, 0.9, 260.0)]
    sprites = []
    T = np.zeros((h, w), dtype=np.float64)
    for dx, dy, sigma, peak, d in specs:
        r = int(math.ceil(3 * sigma)) + 1
        rect = Rect(int(dx) - r, int(dy) - r, int(dx) + r + 1, int(dy) + r + 1)
        patch = _gauss_patch(rect, dx, dy, sigma, peak)
        sprites.append(_sprite(dx, dy, rect, [patch], d))
        T[rect.y0:rect.y1, rect.x0:rect.x1] += patch
    return sprites, T.astype(np.float32)


# ---------------------------------------------------------------------------
# Oracle replay.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["frame0", "frame03", "frame1"])
def test_frame_matches_js_oracle(oracle: Any, case: str) -> None:
    scene, sc = _scene(oracle)
    want_block = [b for b in oracle("frame")["cases"]["frame"] if b["name"] == case][0]
    opts = _opts(sc["frameOpts"], star_quality=None)
    got = frame(scene, want_block["t"], opts, want_block["outW"], want_block["outH"], sc["crop"])
    assert len(got) == 1
    diff = np.max(np.abs(got[0].ravel().astype(np.float64) - np.asarray(want_block["out"])))
    assert diff <= TOL, diff


@pytest.mark.parametrize("key, quality, t", [("frameMedium", "medium", 1.0), ("frameHigh", "high", 1.0)])
def test_frame_star_quality_matches_js_oracle(oracle: Any, key: str, quality: str, t: float) -> None:
    scene, sc = _scene(oracle)
    want = oracle("frame")["cases"][key]["out"]
    opts = _opts(sc["frameOpts"], star_quality=quality)
    got = frame(scene, t, opts, 32, 18, sc["crop"])
    diff = np.max(np.abs(got[0].ravel().astype(np.float64) - np.asarray(want)))
    assert diff <= TOL, (quality, diff)


def test_frame_motion_blur_matches_js_oracle(oracle: Any) -> None:
    scene, sc = _scene(oracle)
    want = oracle("frame")["cases"]["frameBlur"]["out"]
    opts = _opts(sc["frameOpts"], motion_blur=True)
    got = frame(scene, 0.3, opts, 32, 18, sc["crop"])
    diff = np.max(np.abs(got[0].ravel().astype(np.float64) - np.asarray(want)))
    assert diff <= TOL, diff


@pytest.mark.parametrize("idx", [0, 1])
def test_headroom_map_matches_js_oracle(oracle: Any, idx: int) -> None:
    """Render.headroomMap at t = 0.3 and 1 (starHdr, peak 1000), with the stars placed by addSprites as Render.frame does."""
    scene, sc = _scene(oracle)
    want = oracle("frame")["cases"]["headroomMap"][idx]
    t = float(want["t"])
    d = sc["frameOpts"]
    s = float(d["travel"]) * G.ease(t, d["easing"])
    K = G.backdrop_zoom(float(sc["D"]), s, G.BACKDROP_MOTION_DEFAULT)
    assert K == pytest.approx(float(want["K"]), abs=1e-12)
    out_w, out_h = 32, 18
    cam = Camera(x=0.0, y=0.0, fx=scene.w / out_w, fy=scene.h / out_h)
    opts = _opts(d, t=t, K=K, coarse_mips=False, star_hdr=True, peak=float(want["peak"]))
    placed = add_sprites(FrameState(scene, s, opts), [np.zeros((out_h, out_w), np.float32)], FrameTarget(cam, out_w, out_h))
    got = headroom_map(scene, placed, opts, FrameTarget(cam, out_w, out_h))
    diff = np.max(np.abs(got.ravel().astype(np.float64) - np.asarray(want["out"], dtype=np.float64)))
    assert diff <= 1e-6, (t, diff)


def test_headroom_map_core_radius_case_matches_js_oracle(oracle: Any) -> None:
    """Render.headroomMap where a placed star's core radius, not its reach, sets the bump (growth 6, faint sprites,
    one bright catalogue star off the frame so gBright is 9.5 and only the third sprite gets a gain)."""
    scene, sc = _scene(oracle)
    want = oracle("frame")["cases"]["headroomMapCore"]
    for e, g in zip(scene.sprites, want["G"]):
        e.s.source = dict(e.s.source, G=g)           # a copy: the loaded oracle is shared
    scene.catalogue = [dict(c) for c in want["catalogue"]]
    scene.cache.pop("gBright", None)
    t = float(want["t"])
    d = sc["frameOpts"]
    s = float(d["travel"]) * G.ease(t, d["easing"])
    K = G.backdrop_zoom(float(sc["D"]), s, G.BACKDROP_MOTION_DEFAULT)
    assert K == pytest.approx(float(want["K"]), abs=1e-12)
    out_w, out_h = 32, 18
    cam = Camera(x=0.0, y=0.0, fx=scene.w / out_w, fy=scene.h / out_h)
    opts = _opts(d, t=t, K=K, growth=float(want["growth"]), coarse_mips=False, star_hdr=True, peak=float(want["peak"]))
    placed = add_sprites(FrameState(scene, s, opts), [np.zeros((out_h, out_w), np.float32)], FrameTarget(cam, out_w, out_h))
    got = headroom_map(scene, placed, opts, FrameTarget(cam, out_w, out_h))
    diff = np.max(np.abs(got.ravel().astype(np.float64) - np.asarray(want["out"], dtype=np.float64)))
    assert diff <= 1e-6, diff


def test_frame_pq_matches_js_oracle(oracle: Any) -> None:
    """Render.frame with the PQ transform (peak 1000) at t = 0 and 1: the frames to 1e-6, and the transform's
    MaxCLL and MaxFALL after each frame (the same transform carries them from frame 0 to frame 1)."""
    scene, sc = _scene(oracle)
    tr = output_transform(SRGB_COLOUR, "pq", {"peak": 1000.0})
    blocks = oracle("frame")["cases"]["framePQ"]
    assert [b["t"] for b in blocks] == [0, 1]
    for block in blocks:
        opts = _opts(sc["frameOpts"], output=tr)
        got = frame(scene, float(block["t"]), opts, block["outW"], block["outH"], sc["crop"])
        assert len(got) == 1
        diff = np.max(np.abs(got[0].ravel().astype(np.float64) - np.asarray(block["out"], dtype=np.float64)))
        assert diff <= 1e-6, (block["t"], diff)
        assert tr.stats["max_cll"] == pytest.approx(block["stats"]["maxCll"], rel=1e-6)
        assert tr.stats["max_fall"] == pytest.approx(block["stats"]["maxFall"], rel=1e-6)


# ---------------------------------------------------------------------------
# Behaviour (selftest.js fly.render intent, synthetic scenes).
# ---------------------------------------------------------------------------


def test_frame_zero_equals_screen_of_scene() -> None:
    """Frame 0 equals the input: screen(S, T) of the starless and the stars layer, within 1/65535 (no output transform)."""
    w, h = 40, 30
    S = _synthetic_backdrop(w, h)
    sprites, T_expected = _two_sprites(w, h)
    sc = _scene_of(w, h, [S], [np.zeros((h, w), np.float32)], sprites, D=900.0)
    got = frame(sc, 0.0, RenderOptions(travel=60.0, easing="linear", growth=0.15, brightening=True), w, h,
                {"x": 0, "y": 0, "w": w, "h": h})
    want = screen(S, T_expected)
    assert np.max(np.abs(got[0].astype(np.float64) - want.astype(np.float64))) <= TOL


def test_frame_zero_equals_screen_of_scene_three_channels() -> None:
    """Colour: three channels, each frame 0 plane equals its screen (the same geometry in every channel)."""
    w, h = 40, 30
    S = [_synthetic_backdrop(w, h, seed=k) for k in range(3)]
    sprites, T_expected = _two_sprites(w, h)
    sc = _scene_of(w, h, S, [np.zeros((h, w), np.float32)] * 3, sprites, D=900.0)
    got = frame(sc, 0.0, RenderOptions(travel=60.0, easing="linear", growth=0.15, brightening=True), w, h,
                {"x": 0, "y": 0, "w": w, "h": h})
    assert len(got) == 3
    for c in range(3):
        want = screen(S[c], T_expected)
        assert np.max(np.abs(got[c].astype(np.float64) - want.astype(np.float64))) <= TOL


def test_near_star_moves_stays_in_frame_and_lands_on_projection() -> None:
    """A nearby star moves to its projection: the drawn light's centroid is within 0.5 px of the expected centre.

    The expected centre is the selftest's (fly.render): detection + (projection of the moved star - projection
    at frame 0), with Fly.moved and the scene's projection, independent of place_sprite (which is only checked
    to draw the star at all).
    """
    w, h = 48, 36
    sprites, _ = _two_sprites(w, h)
    near = sprites[0]                      # d = 180 pc
    S = np.zeros((h, w), np.float32)
    sc = _scene_of(w, h, [S], [np.zeros((h, w), np.float32)], [near], D=900.0)
    travel = 90.0
    opts = RenderOptions(travel=travel, easing="linear", growth=0.15, brightening=True)
    src = near.source
    moved = G.moved(src["ra"], src["dec"], near.d, sc.target, travel)
    p1 = sc.project(moved["ra"], moved["dec"])
    p0 = sc.project(src["ra"], src["dec"])
    ex = near.det.x + p1.x - p0.x
    ey = near.det.y + p1.y - p0.y
    assert 0 <= ex < w and 0 <= ey < h
    assert math.hypot(ex - near.det.x, ey - near.det.y) >= 3.0
    assert place_sprite(FrameState(sc, travel, opts), sc.sprites[0], 0) is not None
    img = frame(sc, 1.0, opts, w, h, {"x": 0, "y": 0, "w": w, "h": h})[0].astype(np.float64)
    # the light's intensity-weighted centre in a window round the projection (the core clips at white, so
    # the first maximum is no centre: a centroid is)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    win = (np.abs(xx - ex) <= 4) & (np.abs(yy - ey) <= 4)
    total = img[win].sum()
    assert total > 0
    mx = (xx[win] * img[win]).sum() / total
    my = (yy[win] * img[win]).sum() / total
    assert abs(mx - ex) <= 0.5 and abs(my - ey) <= 0.5, (mx, my, ex, ey)


def test_galaxy_backdrop_does_not_move() -> None:
    """A galaxy (D infinite) has no backdrop zoom: its frame equals the input at any time."""
    w, h = 40, 30
    S = _synthetic_backdrop(w, h)
    sc = _scene_of(w, h, [S], [np.zeros((h, w), np.float32)], [], D=math.inf)
    got = frame(sc, 1.0, RenderOptions(travel=150.0, easing="linear", growth=0.15, brightening=True), w, h,
                {"x": 0, "y": 0, "w": w, "h": h})
    assert np.max(np.abs(got[0].astype(np.float64) - S.astype(np.float64))) <= TOL


def test_passed_star_is_never_drawn() -> None:
    """A star the camera has passed (travel beyond its distance) is not in the frame: the frame equals the one without it."""
    w, h = 40, 30
    sprites, _ = _two_sprites(w, h)
    S = _synthetic_backdrop(w, h)
    near = _sprite(sprites[0].det.x, sprites[0].det.y, sprites[0].rect, [sprites[0].pixels[0]], 50.0)
    with_star = _scene_of(w, h, [S], [np.zeros((h, w), np.float32)], [near], D=900.0)
    without = _scene_of(w, h, [S], [np.zeros((h, w), np.float32)], [], D=900.0)
    opts = RenderOptions(travel=80.0, easing="linear", growth=0.15, brightening=True)
    a = frame(with_star, 1.0, opts, w, h, {"x": 0, "y": 0, "w": w, "h": h})[0]
    b = frame(without, 1.0, opts, w, h, {"x": 0, "y": 0, "w": w, "h": h})[0]
    assert np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))) == 0.0


# ---------------------------------------------------------------------------
# HDR (selftest.js fly.hdr intent): headroom only for PQ and HLG.
# ---------------------------------------------------------------------------


class _RecordingOutput:
    """An output transform that does nothing but record the excess light it is given."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.excess: Optional[list[Optional[np.ndarray]]] = None

    def apply(self, planes: list[np.ndarray], excess: Any = None) -> None:
        self.excess = list(excess) if excess is not None else None


def _hdr_scene() -> Scene:
    w, h = 40, 30
    sprites, _ = _two_sprites(w, h)
    bright = _sprite(sprites[1].det.x, sprites[1].det.y, sprites[1].rect,
                     [sprites[1].pixels[0] * 3.0], d=260.0, G_mag=6.0)
    return _scene_of(w, h, [np.zeros((h, w), np.float32)], [np.zeros((h, w), np.float32)], [bright, sprites[0]], D=900.0)


def _max_excess(out: _RecordingOutput) -> float:
    assert out.excess is not None and out.excess[0] is not None
    return float(np.max(out.excess[0]))


def test_headroom_only_for_pq_and_hlg() -> None:
    sc = _hdr_scene()
    base = dict(travel=20.0, easing="linear", growth=0.15, brightening=True, t=0.0, peak=1000.0)
    sdr = _RecordingOutput("sdr")
    frame(sc, 0.0, RenderOptions(**base, output=sdr, star_hdr=True), 40, 30, {"x": 0, "y": 0, "w": 40, "h": 30})
    assert sdr.excess == [None] or sdr.excess is None or all(e is None for e in sdr.excess)


def test_approaching_star_rises_above_sdr_white_in_pq_and_hlg() -> None:
    """With the headroom on, a bright star's light past white grows in PQ and HLG; the SDR frame carries none."""
    sc = _hdr_scene()
    crop = {"x": 0, "y": 0, "w": 40, "h": 30}
    plain = _RecordingOutput("pq")
    frame(sc, 0.0, RenderOptions(travel=20.0, easing="linear", growth=0.15, brightening=True, output=plain), 40, 30, crop)
    boosted = _RecordingOutput("pq")
    frame(sc, 0.0, RenderOptions(travel=20.0, easing="linear", growth=0.15, brightening=True, output=boosted, star_hdr=True),
          40, 30, crop)
    assert _max_excess(plain) > 0.0
    assert _max_excess(boosted) > _max_excess(plain)
    hlg = _RecordingOutput("hlg")
    frame(sc, 0.0, RenderOptions(travel=20.0, easing="linear", growth=0.15, brightening=True, output=hlg, star_hdr=True),
          40, 30, crop)
    assert _max_excess(hlg) > _max_excess(plain) * 0.99


def test_headroom_leaves_sdr_frames_unchanged() -> None:
    sc = _hdr_scene()
    crop = {"x": 0, "y": 0, "w": 40, "h": 30}
    off = frame(sc, 0.0, RenderOptions(travel=20.0, easing="linear", growth=0.15, brightening=True, output=_RecordingOutput("sdr")),
                40, 30, crop)[0]
    on = frame(sc, 0.0, RenderOptions(travel=20.0, easing="linear", growth=0.15, brightening=True,
                                      output=_RecordingOutput("sdr"), star_hdr=True), 40, 30, crop)[0]
    assert np.array_equal(off, on)


def test_hlg_star_peak_exceeds_sdr_white_and_stays_under_one() -> None:
    """HLG (fly.hdr): the bright star's peak code value is above the HLG code of SDR white and at most 1."""
    sc = _hdr_scene()
    crop = {"x": 0, "y": 0, "w": 40, "h": 30}
    white = output_transform(SRGB_COLOUR, "hlg")
    plane = [np.ones((1, 1), np.float32)]
    white.apply(plane)                                   # SDR white (1.0 sRGB) through the HLG transform
    sdr_white = float(plane[0][0, 0])
    assert sdr_white == pytest.approx(0.75, abs=1e-3)    # the HLG reference white code value (BT.2100)
    out = frame(sc, 0.0, RenderOptions(travel=20.0, easing="linear", growth=0.15, brightening=True,
                                       output=output_transform(SRGB_COLOUR, "hlg"), star_hdr=True), 40, 30, crop)[0]
    peak = float(out.max())
    assert sdr_white < peak <= 1.0, (sdr_white, peak)


def test_pq_frame_zero_is_the_image_mapped_into_pq() -> None:
    """Frame 0 in PQ equals the PQ transform of screen(S, T) (the image itself), within 1e-6 (fly.render's PQ check)."""
    w, h = 40, 30
    S = _synthetic_backdrop(w, h)
    sprites, T_expected = _two_sprites(w, h)
    sc = _scene_of(w, h, [S], [np.zeros((h, w), np.float32)], sprites, D=900.0)
    got = frame(sc, 0.0, RenderOptions(travel=60.0, easing="linear", growth=0.15, brightening=True,
                                       output=output_transform(SRGB_COLOUR, "pq", {"peak": 1000.0})),
                w, h, {"x": 0, "y": 0, "w": w, "h": h})[0]
    s64, t64 = S.astype(np.float64), T_expected.astype(np.float64)
    image = [((1 - (1 - s64) * (1 - t64)).astype(np.float32))]
    output_transform(SRGB_COLOUR, "pq", {"peak": 1000.0}).apply(image)
    assert np.max(np.abs(got.astype(np.float64) - image[0].astype(np.float64))) <= 1e-6


def _pq_nits(code: float) -> float:
    """The nits whose PQ code is ``code``: pq_encode inverted by bisection (pq_encode is increasing)."""
    lo, hi = 0.0, 10000.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if pq_encode(mid) < code:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def test_pq_max_cll_of_approaching_star_is_above_sdr_white_and_under_the_peak() -> None:
    """fly.hdr: after a frame with an approaching bright star, the frame's top PQ code is above pq_encode(203) + 0.02
    (the JS margin: well above SDR white, about 230 nits, not merely 203) and at most pq_encode(peak); MaxCLL agrees
    with that code and is at most the peak."""
    sc = _hdr_scene()
    tr = output_transform(SRGB_COLOUR, "pq", {"peak": 1000.0})
    out = frame(sc, 1.0, RenderOptions(travel=20.0, easing="linear", growth=0.15, brightening=True, output=tr,
                                       star_hdr=True, peak=1000.0), 40, 30, {"x": 0, "y": 0, "w": 40, "h": 30})[0]
    top = float(out.max())
    assert top > pq_encode(203.0) + 0.02, top
    assert top <= pq_encode(1000.0) + 1e-6, top
    assert tr.stats["max_cll"] == pytest.approx(_pq_nits(top), rel=1e-3)
    assert _pq_nits(pq_encode(203.0) + 0.02) < tr.stats["max_cll"] <= 1000.0 + 1e-6, tr.stats
    assert tr.stats["max_fall"] <= tr.stats["max_cll"]


# ---------------------------------------------------------------------------
# Plumbing: scene_for, frame_image without an output transform.
# ---------------------------------------------------------------------------


def test_scene_for_turns_once_and_keeps_the_turned_copy() -> None:
    w, h = 40, 20
    sc = _scene_of(w, h, [np.zeros((h, w), np.float32)], [np.zeros((h, w), np.float32)], [], D=900.0)
    turned = scene_for(sc, 20, 40)              # a portrait preset for a landscape image: turned
    assert turned is not sc and turned.w == h and turned.h == w
    assert scene_for(sc, 20, 40) is turned      # kept in the cache
    assert scene_for(sc, 40, 20) is sc          # the same orientation: not turned


def test_frame_image_without_output_is_the_composite() -> None:
    S = [np.full((4, 5), 0.25, np.float32)]
    T = [np.full((4, 5), 0.5, np.float32)]
    out = frame_image(S, T, RenderOptions(), FrameTarget(Camera(0.0, 0.0, 1.0, 1.0), 5, 4), 0.0)
    assert len(out) == 1 and out[0].shape == (4, 5)
    assert np.allclose(out[0], 1 - 0.75 * 0.5, atol=1e-7)


# ---------------------------------------------------------------------------
# Performance (Docker only: LOOMSIRIL_PERF=1).
# ---------------------------------------------------------------------------


def _field_scene(synth_field: Any, w: int, h: int, n_stars: int, nc: int = 1) -> Scene:
    field = synth_field(w=w, h=h, n_stars=n_stars, nc=nc)
    sprites = []
    for st in field.truth:
        sigma = st.fwhm / 2.3548200450309493
        r = int(math.ceil(3 * sigma)) + 1
        x0, y0 = max(0, int(round(st.x)) - r), max(0, int(round(st.y)) - r)
        x1, y1 = min(w, int(round(st.x)) + r + 1), min(h, int(round(st.y)) + r + 1)
        rect = Rect(x0, y0, x1, y1)
        patch = _gauss_patch(rect, st.x, st.y, sigma, st.peak)
        source = {"ra": st.ra, "dec": st.dec, "G": st.G}
        det = Detection(index=len(sprites), x=st.x, y=st.y, flux=st.flux, rect=rect)
        sprites.append(Sprite(source=source, d=1000.0 / st.plx if st.plx > 0 else 1000.0,
                              placed=Placed(source=source, d=1.0, x=st.x, y=st.y), det=det, rect=rect, radius=0.0,
                              centre=Point(st.x, st.y), mask=None, pixels=[patch], model_core=[patch],
                              model_spikes=[np.zeros_like(patch)], spike_angles=[], px_scale=1.0))
    S = [np.asarray(p, dtype=np.float32) for p in field.starless]
    R = [np.zeros((h, w), np.float32) for _ in S]
    t0 = field.truth[0]

    def project(ra: float, dec: float) -> Optional[Point]:
        return tan_project(ra, dec, field.wcs)

    target = {"ra": t0.ra, "dec": t0.dec}
    return Scene(w=w, h=h, nc=len(S), S=S, R=R,
                 sprites=[SceneSprite(s=s, p0=project(s.source["ra"], s.source["dec"])) for s in sprites],
                 project=project, target=target, tp=project(target["ra"], target["dec"]), D=900.0)


@pytest.mark.perf
def test_perf_640x360(synth_field: Any) -> None:
    """One 640 x 360 frame of a 30-sprite scene at full scale, highest quality: the second call takes under 2 s."""
    sc = _field_scene(synth_field, 640, 360, 30)
    opts = RenderOptions(travel=150.0, easing="linear", growth=0.15, brightening=True, twinkle=0.03, duration=10.0,
                         star_quality="highest")
    crop = {"x": 0, "y": 0, "w": 640, "h": 360}
    frame(sc, 0.5, opts, 640, 360, crop)           # warm-up
    start = time.perf_counter()
    drawn = frame(sc, 0.5, opts, 640, 360, crop)
    elapsed = time.perf_counter() - start
    print("perf 640x360 frame, 30 sprites, highest: %.3f s" % elapsed)
    assert elapsed < 2.0, elapsed
    # the timed frame really drew the sprites: the same frame of the scene without them differs (not a vacuous pass)
    bare = frame(replace(sc, sprites=[]), 0.5, opts, 640, 360, crop)
    assert float(np.max(np.abs(drawn[0].astype(np.float64) - bare[0].astype(np.float64)))) > 1e-3


@pytest.mark.perf
def test_perf_1080p_qa_scene(synth_field: Any) -> None:
    """The acceptance-test scene at full size: a 1920 x 1080 field of 20 stars in 3 channels (the heavier of the two
    acceptance fields: test_pr4_frame0 has 12 stars in 1 channel), one frame at t = 0.5, highest quality: the second
    call takes under 6 s, and the sprites are drawn (the same frame without them differs)."""
    sc = _field_scene(synth_field, 1920, 1080, 20, nc=3)
    opts = RenderOptions(travel=150.0, easing="linear", growth=0.15, brightening=True, twinkle=0.03, duration=10.0,
                         star_quality="highest")
    crop = {"x": 0, "y": 0, "w": 1920, "h": 1080}
    frame(sc, 0.5, opts, 1920, 1080, crop)         # warm-up
    start = time.perf_counter()
    drawn = frame(sc, 0.5, opts, 1920, 1080, crop)
    elapsed = time.perf_counter() - start
    print("perf 1920x1080 frame, 20 stars, 3 channels, highest: %.3f s" % elapsed)
    assert elapsed < 6.0, elapsed
    bare = frame(replace(sc, sprites=[]), 0.5, opts, 1920, 1080, crop)
    assert float(np.max(np.abs(drawn[0].astype(np.float64) - bare[0].astype(np.float64)))) > 1e-3


def test_field_scene_smoke(synth_field: Any) -> None:
    """A small field with sprites from its truth renders a frame of the field's size (no perf claim)."""
    sc = _field_scene(synth_field, 96, 54, 8)
    out = frame(sc, 0.5, RenderOptions(travel=60.0, easing="linear", growth=0.15, brightening=True), 96, 54,
                {"x": 0, "y": 0, "w": 96, "h": 54})
    assert len(out) == 1 and out[0].shape == (54, 96)
    assert np.all(np.isfinite(out[0]))
    assert float(out[0].max()) <= 1.0 + 1e-6 and float(out[0].min()) >= -1e-6
