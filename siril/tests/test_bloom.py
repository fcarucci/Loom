"""Bloom and headroom maths on the stars layer (E4: loomsiril/bloom.py).

Replays tests/oracle/bloom.json, recorded from Loom's Render.js (the per-pixel reference
path, which is what runs outside PixInsight) by tools/oracle/bloom.js.
"""

from __future__ import annotations

import numpy as np
import pytest

from loomsiril.bloom import (
    BLOOM_GAINS,
    BLOOM_GLARE,
    BLOOM_TIGHT,
    BLOOM_WIDE,
    bloom,
    bloom_sigmas,
    past_white,
    saturate_stars,
    stars_to_headroom,
    whiten_past_white,
)

# float32 planes; the JS blur accumulates in float32, so 1e-5 (the planned bloom tolerance)
TOL = 1e-5


@pytest.fixture(scope="module")
def bo(oracle):
    return oracle("bloom")["cases"]


def _stack(planes, w, h):
    return [np.asarray(p, dtype=np.float32).reshape(h, w).copy() for p in planes]


def _input(bo):
    inp = bo["input"]
    return _stack(inp["T"], inp["w"], inp["h"]), inp["w"], inp["h"]


def _assert_planes(got, exp, w, h, tol=TOL):
    assert len(got) == len(exp)
    for g, e in zip(got, exp):
        np.testing.assert_allclose(np.asarray(g).reshape(-1), np.asarray(e), rtol=0, atol=tol)


def test_constants_match_js(bo):
    c = bo["constants"]
    assert BLOOM_TIGHT == c["BLOOM_TIGHT"]
    assert BLOOM_WIDE == c["BLOOM_WIDE"]
    assert BLOOM_GLARE == c["BLOOM_GLARE"]
    assert list(BLOOM_GAINS) == c["BLOOM_GAINS"]


@pytest.mark.parametrize("sat_index", [0, 1])
def test_saturate_stars_matches_js(bo, sat_index):
    T, w, h = _input(bo)
    case = bo["saturateStars"][sat_index]
    saturate_stars(T, case["sat"])
    _assert_planes(T, case["out"], w, h)


@pytest.mark.parametrize("case_index", [0, 1])
def test_stars_to_headroom_matches_js(bo, case_index):
    T, w, h = _input(bo)
    case = bo["starsToHeadroomJs"][case_index]
    if case["factor"] is not None:
        stars_to_headroom(T, case["factor"])
    else:
        stars_to_headroom(T, np.asarray(case["map"], dtype=np.float32).reshape(h, w))
    _assert_planes(T, case["out"], w, h)


def test_whiten_past_white_matches_js(bo):
    T, w, h = _input(bo)
    whiten_past_white(T)
    _assert_planes(T, bo["whitenPastWhite"], w, h)


def test_past_white_matches_js(bo):
    T, w, h = _input(bo)
    E = past_white(T)
    _assert_planes(E, bo["pastWhite"]["past"], w, h)
    assert past_white([np.minimum(p, 1.0) for p in T]) is None


@pytest.mark.parametrize("case", [0, 1, 2, 3])
def test_bloom_sigmas_match_js(bo, case):
    c = bo["bloomSigmas"][case]
    got = bloom_sigmas(c["args"][0], c["args"][1])
    np.testing.assert_allclose(got, c["out"], rtol=0, atol=1e-12)


@pytest.mark.parametrize("case", [0, 1, 2])
def test_bloom_js_matches_js(bo, case):
    T, w, h = _input(bo)
    c = bo["bloomJs"][case]
    bloom(T, w, h, amount=c["opts"]["amount"], seconds=c["opts"]["seconds"])
    _assert_planes(T, c["out"], w, h)


@pytest.mark.parametrize("case", [0, 1, 2])
def test_bloom_dispatcher_matches_js(bo, case):
    T, w, h = _input(bo)
    c = bo["bloom"][case]
    bloom(T, w, h, amount=c["opts"]["amount"], seconds=c["opts"]["seconds"])
    _assert_planes(T, c["out"], w, h)


# --- behaviour ---------------------------------------------------------------

def test_nothing_past_white_means_bloom_changes_nothing():
    rng = np.random.default_rng(7)
    T = [rng.random((24, 32), dtype=np.float32) for _ in range(3)]
    before = [t.copy() for t in T]
    bloom(T, 32, 24, amount=1.0, seconds=2.0)
    for t, b in zip(T, before):
        np.testing.assert_array_equal(t, b)


def test_whiten_changes_only_past_white_pixels():
    T = [np.full((4, 4), 0.5, dtype=np.float32), np.full((4, 4), 0.3, dtype=np.float32),
         np.full((4, 4), 0.2, dtype=np.float32)]
    T[0][1, 1] = 2.0
    T[1][1, 1] = 0.9
    T[2][1, 1] = 0.1
    before = [t.copy() for t in T]
    whiten_past_white(T)
    mask = np.ones((4, 4), dtype=bool)
    mask[1, 1] = False
    for t, b in zip(T, before):
        np.testing.assert_array_equal(t[mask], b[mask])
    # the past-white pixel moved toward its brightest channel, the others moved up
    assert T[1][1, 1] > 0.9 and T[2][1, 1] > 0.1
    assert T[0][1, 1] == pytest.approx(2.0)


def test_amount_zero_is_a_no_op(bo):
    T, w, h = _input(bo)
    before = [t.copy() for t in T]
    bloom(T, w, h, amount=0.0, seconds=1.0)
    for t, b in zip(T, before):
        np.testing.assert_array_equal(t, b)
    bloom(T, w, h, amount=-0.5, seconds=1.0)
    for t, b in zip(T, before):
        np.testing.assert_array_equal(t, b)


def test_bloom_spreads_light_past_white_outward(bo):
    T, w, h = _input(bo)
    before = [t.copy() for t in T]
    bloom(T, w, h, amount=1.0, seconds=0.0)
    # the glow adds light around a clipped core: a pixel two columns from the core at (x=10, y=8) gains
    assert float(T[0][8, 12]) > float(before[0][8, 12])
    # and a pixel nowhere near any core is untouched (the blur radius is 1 px per pass)
    assert float(T[0][0, 0]) == float(before[0][0, 0])
