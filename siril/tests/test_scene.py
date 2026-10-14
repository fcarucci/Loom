"""Scene assembly, blend, composite, quarter turn and shrink (E4: loomsiril/scene.py).

Behaviour checks on a synthetic field and a hand-made sprite; the JS numbers for the
quarter turn are replayed in tests/test_resample.py (render_axes oracle, rotateScene).
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
from dataclasses import replace

import numpy as np
import pytest

from loomsiril.core import Point, Rect
from loomsiril.scene import (
    EXCESS_MAX,
    SceneInputs,
    blend,
    composite,
    layer_axis,
    or_default,
    rotate_scene,
    scaled_scene,
    scene,
    star_side,
    upscale_stars,
)
from loomsiril.types import Detection, Placed, Sprite, Spike, StarModel

H, W = 20, 30


def _projector(ra: float, dec: float):
    """A simple affine projector; None off the top of the sky."""
    if dec > 80:
        return None
    return Point(ra * 2.0 - 1.0, dec + 3.0)


def _sprite(seed: int = 1) -> Sprite:
    rng = np.random.default_rng(seed)
    source = {"ra": 12.0, "dec": 6.0, "plx": 5.0, "pmra": 0.0, "pmdec": 0.0, "G": 8.0, "BP": 8.2, "RP": 7.6}
    model = StarModel(x=5.0, y=6.0, core=2.0, prof=np.ones(4, dtype=np.float32), rmax=3.0,
                      spikes=[Spike(angle=0.3, start=1.0, amp=np.ones(2, dtype=np.float32), reach=3.0)],
                      col=[1.0, 0.9, 0.8], support=3.0)
    det = Detection(index=0, x=5.0, y=6.0, flux=1.0, rect=Rect(3, 4, 8, 9), model=model)
    return Sprite(source=source, d=100.0, placed=Placed(source=source, d=100.0, x=5.0, y=6.0), det=det,
                  rect=Rect(3, 4, 8, 9), radius=3.0, centre=Point(5.0, 6.0),
                  mask=np.ones((5, 5), dtype=np.uint8),
                  pixels=[rng.random((5, 5), dtype=np.float32) for _ in range(3)],
                  model_core=[rng.random((5, 5), dtype=np.float32) for _ in range(3)],
                  model_spikes=[rng.random((5, 5), dtype=np.float32)],
                  spike_angles=[0.3], px_scale=1.0)


def _planes(seed: int, nc: int = 2) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [rng.random((H, W), dtype=np.float32) for _ in range(nc)]


def _scene(seed: int = 3, with_sprite: bool = True) -> object:
    starless = _planes(seed)
    stars = _planes(seed + 1)
    sprites = [_sprite(seed)] if with_sprite else []
    return scene(SceneInputs(starless, stars, sprites, _projector, {"ra": 10.0, "dec": 5.0}, 250.0,
                             catalogue=[{"x": 1.0, "y": 2.0, "source": {"ra": 1.0, "dec": 2.0}}]))


# --- scene() -----------------------------------------------------------------

def test_scene_shapes_and_projection():
    sc = _scene()
    assert (sc.w, sc.h, sc.nc) == (W, H, 2)
    assert all(p.shape == (H, W) and p.dtype == np.float32 for p in sc.S + sc.R)
    assert sc.D == 250.0
    assert sc.tp == Point(19.0, 8.0)                     # _projector(10, 5)
    assert sc.sprites[0].p0 == Point(23.0, 9.0)          # _projector(12, 6)
    assert sc.catalogue[0]["x"] == 1.0


def test_scene_takes_the_residual_as_given_and_masks_the_stars():
    starless = _planes(5)
    stars = _planes(6)
    mask = np.ones((H, W), dtype=bool)
    mask[:, :10] = False
    sc = scene(SceneInputs(starless, stars, [], _projector, {"ra": 10.0, "dec": 5.0}, 1.0, residual_mask=mask))
    assert np.all(sc.R[0][:, :10] == 0) and np.all(sc.R[1][:, :10] == 0)
    np.testing.assert_array_equal(sc.R[0][:, 10:], stars[0][:, 10:])
    np.testing.assert_array_equal(sc.S[0], starless[0])
    given = _planes(8)
    sc2 = scene(SceneInputs(starless, stars, [], _projector, {"ra": 10.0, "dec": 5.0}, 1.0, residual=given))
    assert sc2.R[0] is given[0]
    assert sc2.catalogue is None


def test_scene_p0_is_none_off_projection():
    s = _sprite()
    s = replace(s, source=dict(s.source, dec=85.0))
    sc = scene(SceneInputs(_planes(1), _planes(2), [s], _projector, {"ra": 10.0, "dec": 85.0}, 1.0))
    assert sc.sprites[0].p0 is None and sc.tp is None


# --- blend ---------------------------------------------------------------------

def test_blend_is_alpha_mix_and_leaves_b_untouched():
    a = _planes(11)
    b = _planes(12)
    a0 = [p.copy() for p in a]
    b0 = [p.copy() for p in b]
    out = blend(a, b, 0.25)
    for o, x, y in zip(out, a0, b0):
        np.testing.assert_allclose(o, 0.25 * x + 0.75 * y, rtol=0, atol=1e-6)
        assert o.dtype == np.float32
    for p, q in zip(b, b0):
        np.testing.assert_array_equal(p, q)


# --- composite -------------------------------------------------------------------

def test_composite_is_screen_and_excess_only_for_hdr():
    S = np.array([[0.2, 0.9, -0.1, 1.4]], dtype=np.float32)
    T = np.array([[0.5, 0.3, 0.2, 2.5]], dtype=np.float32)
    out, excess = composite(S, T, False)
    s = np.clip(S, 0, 1).astype(np.float64)
    t = np.clip(T, 0, 1).astype(np.float64)
    np.testing.assert_allclose(out, 1 - (1 - s) * (1 - t), atol=1e-6)
    assert excess is None
    out_h, excess_h = composite(S, T, True)
    np.testing.assert_allclose(out_h, out, atol=0)
    np.testing.assert_allclose(excess_h, [[0.0, 0.0, 0.0, 1.5]], atol=1e-6)
    assert EXCESS_MAX == 1e30


# --- rotate_scene ----------------------------------------------------------------

def test_rotate_scene_turns_the_planes_and_points_clockwise():
    sc = _scene(with_sprite=False)
    t = rotate_scene(sc)
    assert (t.w, t.h) == (H, W)
    # pixel (x, y) goes to (h - 1 - y, x): the top-left pixel goes to the top-right
    assert t.S[0][0, H - 1] == sc.S[0][0, 0]
    assert t.S[0].shape == (W, H)
    assert t.tp == Point(H - 1 - 8.0, 19.0)
    assert t.project(10.0, 5.0) == Point(H - 1 - 8.0, 19.0)
    assert t.catalogue[0]["x"] == H - 1 - 2.0 and t.catalogue[0]["y"] == 1.0


def test_rotate_scene_matches_js(oracle):
    case = oracle("render_axes")["cases"]["rotateScene"]
    inp, exp = case["input"], case["out"]
    sc = scene(SceneInputs(
        [np.asarray(p, dtype=np.float32).reshape(inp["h"], inp["w"]) for p in inp["S"]],
        [np.asarray(p, dtype=np.float32).reshape(inp["h"], inp["w"]) for p in inp["R"]],
        [], lambda ra, dec: Point(ra * 1.5 + 0.25, dec - 0.5), {"ra": 2.0, "dec": 1.0}, 100.0,
        residual=[np.asarray(p, dtype=np.float32).reshape(inp["h"], inp["w"]) for p in inp["R"]],
        catalogue=[{"x": 1.0, "y": 2.0, "source": {"ra": 1.0, "dec": 2.0}}]))
    sc = replace(sc, nc=inp["nc"], tp=Point(1.5, 0.5))
    t = rotate_scene(sc)
    assert (t.w, t.h) == (exp["w"], exp["h"])
    for got, want in zip(t.S, exp["S"]):
        np.testing.assert_allclose(got.reshape(-1), want, rtol=0, atol=1e-6)
    for got, want in zip(t.R, exp["R"]):
        np.testing.assert_allclose(got.reshape(-1), want, rtol=0, atol=1e-6)
    assert t.tp == Point(exp["tp"]["x"], exp["tp"]["y"])
    assert t.catalogue[0]["x"] == exp["catalogue"][0]["x"] and t.catalogue[0]["y"] == exp["catalogue"][0]["y"]
    assert t.project(2.0, 1.25) == Point(exp["projected"]["x"], exp["projected"]["y"])


def test_rotate_scene_four_quarter_turns_are_the_identity():
    sc = _scene()
    t = sc
    for _ in range(4):
        t = rotate_scene(t)
    assert (t.w, t.h) == (W, H)
    for a, b in zip(t.S + t.R, sc.S + sc.R):
        np.testing.assert_array_equal(a, b)
    assert abs(t.tp.x - sc.tp.x) <= 1e-9 and abs(t.tp.y - sc.tp.y) <= 1e-9
    s0, s1 = sc.sprites[0].s, t.sprites[0].s
    assert s1.rect == s0.rect
    for a, b in zip(s1.pixels, s0.pixels):
        np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(s1.mask, s0.mask)
    for a, b in zip(s1.spike_angles, s0.spike_angles):
        assert math.isclose(math.fmod(a - b, 2 * math.pi), 0.0, abs_tol=1e-9)
    assert t.sprites[0].p0.x == pytest.approx(sc.sprites[0].p0.x, abs=1e-9)
    assert t.sprites[0].p0.y == pytest.approx(sc.sprites[0].p0.y, abs=1e-9)


def test_rotate_scene_turns_the_sprite_rect_and_patches():
    sc = _scene()
    t = rotate_scene(sc)
    s = t.sprites[0].s
    assert s.rect == Rect(H - 9, 3, H - 4, 8)            # (h - y1, x0, h - y0, x1)
    assert s.pixels[0].shape == (s.rect.height, s.rect.width)
    assert s.spike_angles[0] == pytest.approx(0.3 + math.pi / 2)
    assert t.cache == {}


# --- scaled_scene ----------------------------------------------------------------

def test_scaled_scene_positions_and_shapes():
    sc = _scene()
    d = 0.5
    sc_s = scaled_scene(sc, d)
    assert (sc_s.w, sc_s.h) == (math.ceil(W * d), math.ceil(H * d))
    assert sc_s.S[0].shape == (sc_s.h, sc_s.w)
    s = sc_s.sprites[0].s
    # (p + 0.5) d - 0.5 for the sprite's centre and its catalogue position
    assert s.det.x == pytest.approx((5.0 + 0.5) * d - 0.5)
    assert s.det.y == pytest.approx((6.0 + 0.5) * d - 0.5)
    assert sc_s.sprites[0].p0.x == pytest.approx((23.0 + 0.5) * d - 0.5)
    assert sc_s.sprites[0].p0.y == pytest.approx((9.0 + 0.5) * d - 0.5)
    assert sc_s.tp.x == pytest.approx((19.0 + 0.5) * d - 0.5)
    assert s.pixels[0].shape == (s.rect.height, s.rect.width)
    assert s.det.model.core == pytest.approx(2.0 * d)
    assert s.px_scale == pytest.approx(d)
    assert s.mask is None
    assert s.cache == {}


def test_scaled_scene_is_cached_for_the_same_d():
    sc = _scene()
    a = scaled_scene(sc, 0.5)
    assert scaled_scene(sc, 0.5) is a
    b = scaled_scene(sc, 0.25)
    assert b is not a
    assert scaled_scene(sc, 0.5) is not a          # the cache holds the last d only (as the JS)


def _fingerprint(obj: object) -> str:
    """A SHA-256 over a scene's content: arrays by dtype, shape and bytes; dataclasses, dicts and lists field by field."""
    h = hashlib.sha256()

    def walk(o: object) -> None:
        if isinstance(o, np.ndarray):
            h.update(f"nd:{o.dtype}:{o.shape}:".encode())
            h.update(np.ascontiguousarray(o).tobytes())
        elif dataclasses.is_dataclass(o) and not isinstance(o, type):
            h.update(f"dc:{type(o).__name__}(".encode())
            for f in dataclasses.fields(o):
                h.update(f.name.encode() + b"=")
                walk(getattr(o, f.name))
            h.update(b")")
        elif isinstance(o, dict):
            h.update(b"{")
            for k in sorted(o, key=repr):
                walk(k)
                walk(o[k])
            h.update(b"}")
        elif isinstance(o, (list, tuple)):
            h.update(b"[" if isinstance(o, list) else b"(")
            for x in o:
                walk(x)
            h.update(b"]" if isinstance(o, list) else b")")
        elif callable(o):
            h.update(b"callable")
        else:
            h.update(repr(o).encode())

    walk(obj)
    return h.hexdigest()


def _bare_sprite_scene() -> object:
    """A scene whose sprite has no radius, model, rect or px_scale, and which has no catalogue (the None branches)."""
    s = _sprite(4)
    s = replace(s, radius=None, px_scale=None, det=replace(s.det, model=None, rect=None))
    return scene(SceneInputs(_planes(4), _planes(5), [s], _projector, {"ra": 10.0, "dec": 5.0}, 250.0))


# Characterization of scaled_scene (R2-b, recorded on fly/final@a242afd before the split): the whole output
# scene, field by field, for a rich sprite, a bare sprite (the None branches) and three shrink factors.
_SCALED_FINGERPRINTS = {
    ("rich", 0.5): "605cd553f18af5e965cfca2d0177fe31be802db3003727bf8e8d9ac4afc1ef26",
    ("rich", 0.25): "86cfab57e5478eabf306ae2850702fb715327f34237e7fbced7af1e55ebc57d5",
    ("rich", 0.37): "ade170c22e71b5eed08fb1d93f1fed3ede5c26b7a28f9d10a48344a14c788857",
    ("bare", 0.5): "6503f6331ccb185bb577b03c5adc255e1f5b5fd8ae580bcea2a04ef4a3ff4b8e",
    ("bare", 0.25): "7eba1a477ef4888ec9a347113075726b820bb017025fbe27472e3746bb0c7067",
}


@pytest.mark.parametrize("case,d", list(_SCALED_FINGERPRINTS))
def test_scaled_scene_output_is_characterized(case: str, d: float) -> None:
    sc = _scene() if case == "rich" else _bare_sprite_scene()
    assert _fingerprint(scaled_scene(sc, d)) == _SCALED_FINGERPRINTS[(case, d)]


# --- small helpers -----------------------------------------------------------------

def test_small_helpers():
    assert or_default(None, 3) == 3 and or_default(0, 3) == 0
    assert star_side(1080, 1.0) == 1080
    assert star_side(1080, 0.5) == 540
    assert star_side(3, 0.1) == 1
    full = object()
    assert layer_axis(full, 10, 10, 0, 1, 0, 1, 10, "bicubic") is full
    up = upscale_stars([np.ones((2, 2), dtype=np.float32)], 2, 2, 4, 4)
    assert up[0].shape == (4, 4)
    same = [np.ones((2, 2), dtype=np.float32)]
    assert upscale_stars(same, 2, 2, 2, 2) is same
