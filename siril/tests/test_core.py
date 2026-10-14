"""loomsiril.core against the JS oracle (tests/oracle/core.json) and known answers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from loomsiril import core
from loomsiril.core import Point, Rect


def _same(a: float, b: float, tol: float = 1e-12) -> bool:
    if isinstance(b, float) and math.isnan(b):
        return isinstance(a, float) and math.isnan(a)
    return abs(a - b) <= tol


def test_rad_matches_js(oracle):
    assert core.RAD == oracle("core")["cases"]["RAD"]


@pytest.mark.parametrize("fn", ["smoothstep", "median", "mad"])
def test_against_js(oracle, fn):
    for case in oracle("core")["cases"][fn]:
        got = getattr(core, fn)(*case["args"])
        assert _same(got, case["out"]), (fn, case, got)


def test_smoothstep_rests_at_both_ends():
    assert core.smoothstep(0, 1, 0) == 0 and core.smoothstep(0, 1, 1) == 1
    h = 1e-6
    assert core.smoothstep(0, 1, h) < 1e-11 and 1 - core.smoothstep(0, 1, 1 - h) < 1e-11


def test_median_is_upper_middle_like_js():
    assert core.median([4, 1, 3, 2]) == 3


def test_mad_of_constant_set_is_one():
    assert core.mad([2.0, 2.0, 2.0]) == 1


def test_clamp_propagates_nan():
    assert math.isnan(core.clamp(float("nan"), 0, 1))
    assert core.clamp(5, 0, 1) == 1 and core.clamp(-5, 0, 1) == 0


def test_rect_shape_and_dict_round_trip():
    r = Rect(2, 3, 7, 5)
    assert (r.width, r.height) == (5, 2)
    assert r.contains(2, 3) and not r.contains(7, 3) and not r.contains(2, 5)
    assert Rect.from_dict(r.as_dict()) == r
    plane = np.arange(100).reshape(10, 10)
    assert plane[r.slices()].shape == (2, 5)
    assert Rect(1, 1, 1, 4).is_empty()


def test_point_unpacks():
    x, y = Point(1.5, 2.5)
    assert (x, y) == (1.5, 2.5) and Point(1, 2).x == 1


def test_unscreen_inverts_screen():
    rng = np.random.default_rng(0)
    s = rng.uniform(0, 0.9, (8, 8)).astype(np.float32)
    t = rng.uniform(0, 1, (8, 8)).astype(np.float32)
    back = core.unscreen(core.screen(s, t), s)
    assert back.dtype == np.float32
    assert np.max(np.abs(back - t)) < 1e-5


def test_unscreen_follows_pixelmath_guards():
    # iif(S < 1, max(0, (I - S)/(1 - S)), 0): S == 1 gives 0, I < S gives 0.
    i = np.array([0.5, 0.2, 1.0], dtype=np.float32)
    s = np.array([1.0, 0.4, 0.5], dtype=np.float32)
    assert core.unscreen(i, s).tolist() == [0.0, 0.0, 1.0]


@pytest.mark.parametrize("fn,js", [("grown_rect", "grownRect"), ("detection_rect", "detectionRect"),
                                   ("square_rect", "squareRect")])
def test_rects_against_js(oracle, fn, js):
    for case in oracle("core")["cases"][js]:
        args = list(case["args"])
        if fn == "grown_rect":
            args[0] = Rect.from_dict(args[0])
        got = getattr(core, fn)(*args)
        assert got == Rect.from_dict(case["out"]), (fn, case, got)


def test_js_round_against_js(oracle):
    for case in oracle("core")["cases"]["jsRound"]:
        assert core.js_round(*case["args"]) == case["out"], case


def _near(a, b, tol=1e-12):
    return abs(a - b) <= tol * max(1.0, abs(b))


@pytest.mark.parametrize("fn,js", [("vec", "vec"), ("radec", "radec"), ("separation", "separation"),
                                   ("tan_project", "tanProject"), ("tan_unproject", "tanUnproject")])
def test_sky_against_js(oracle, fn, js):
    for case in oracle("core")["cases"][js]:
        got = getattr(core, fn)(*case["args"])
        want = case["out"]
        if isinstance(want, list):
            assert all(_near(g, e) for g, e in zip(got, want)), (case, got)
        elif isinstance(want, dict):
            g = got._asdict() if hasattr(got, "_asdict") else got
            assert set(g) == set(want) and all(_near(g[k], want[k], 1e-9) for k in want), (case, got)
        else:
            assert _near(got, want, 1e-9), (case, got)


def test_tan_round_trip_and_reference_pixel():
    w = {"crval1": 324.745, "crval2": 57.514, "crpix1": 200.5, "crpix2": 150.5, "cd": [-0.0005, 0, 0, 0.0005]}
    c = core.tan_project(324.745, 57.514, w)
    assert abs(c.x - 199.5) < 1e-9 and abs(c.y - 149.5) < 1e-9
    assert core.tan_project(324.745, 57.564, w).y > c.y  # north is +y for a positive CD2_2
    back = core.tan_unproject(*core.tan_project(325.0, 57.3, w), w)
    assert abs(back["ra"] - 325.0) < 1e-9 and abs(back["dec"] - 57.3) < 1e-9


def test_smoothstep_arr_is_the_scalar_elementwise():
    xs = np.array([-1.0, 0.0, 0.25, 1.5, 2.0, 3.0, 4.0, 4.5, 9.0, np.nan], dtype=np.float32)
    got = core.smoothstep_arr(1.5, 4, xs)
    assert got.dtype == np.float32
    for x, g in zip(xs, got):
        want = core.smoothstep(1.5, 4, float(x))
        assert (math.isnan(want) and math.isnan(g)) or g == pytest.approx(want, rel=1e-6, abs=1e-7)
    assert math.isnan(got[-1])


def test_smoothstep_arr_float64_stays_float64():
    got = core.smoothstep_arr(0.0, 1.0, np.linspace(-0.5, 1.5, 9))
    assert got.dtype == np.float64
    assert got[0] == 0.0 and got[-1] == 1.0
