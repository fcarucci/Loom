"""JS semantics that Python lacks (loomsiril.jsmath), pinned to what the JS does."""

from __future__ import annotations

import math

import numpy as np
import pytest

from loomsiril import core, gaia, jsmath, presets
from loomsiril import geometry as geo

#: String(number) as JS prints it: zeros, exponent edges, non-finite values, ints, long digits.
JS_NUMBER_TEXT = [
    (0, "0"), (0.0, "0"), (-0.0, "0"), (1, "1"), (-3, "-3"), (10, "10"), (1e15, "1000000000000000"),
    (2**53, "9007199254740992"), (1e20, "100000000000000000000"), (1e21, "1e+21"),
    (1e-7, "1e-7"), (1e-6, "0.000001"), (1.5e-6, "0.0000015"), (1e-5, "0.00001"),
    (123.456, "123.456"), (12.5, "12.5"), (24.5, "24.5"), (0.15, "0.15"), (1.5e16, "15000000000000000"),
    (0.1 + 0.2, "0.30000000000000004"), (5e-324, "5e-324"), (1.7976931348623157e308, "1.7976931348623157e+308"),
    (123456789012345678901, "123456789012345680000"), (math.nan, "NaN"), (math.inf, "Infinity"),
    (-math.inf, "-Infinity"),
]


@pytest.mark.parametrize("v, text", JS_NUMBER_TEXT)
def test_number_str_is_js_string_of_number(v, text):
    assert jsmath.number_str(v) == text


def test_presets_and_gaia_use_the_one_number_text():
    """presets.js_num_str is re-exported from jsmath; gaia's URL numbers use the same function."""
    assert presets.js_num_str is jsmath.number_str
    assert gaia.number_str is jsmath.number_str


@pytest.mark.parametrize("text, value", [
    ("12px", 12.0), ("  -3.5e2x", -350.0), (".5", 0.5), ("+.5e-1", 0.05), ("1e", 1.0), ("7.", 7.0),
])
def test_parse_float_reads_the_prefix(text, value):
    assert jsmath.parse_float(text) == value


@pytest.mark.parametrize("text", ["", "abc", "-", "e5"])
def test_parse_float_without_a_number_is_nan(text):
    assert math.isnan(jsmath.parse_float(text))


@pytest.mark.parametrize("v, text", [(None, "null"), (True, "true"), (False, "false"), (2.0, "2"),
                                      (1.5, "1.5"), (0.0, "0"), ("x", "x"), (7, "7")])
def test_js_string_of_header_values(v, text):
    assert jsmath.js_string(v) == text


def test_json_string_uses_the_short_escapes_and_u_for_controls():
    assert jsmath.json_string('a"b\\c\n\x01\t') == '"a\\"b\\\\c\\n\\u0001\\t"'
    assert jsmath.json_string("plain") == '"plain"'


def test_js_round_is_the_core_one_halves_up():
    assert jsmath.js_round is core.js_round
    assert [jsmath.js_round(v) for v in (2.5, -2.5, 0.49, -0.5)] == [3, -2, 0, 0]


def test_js_div_gives_ieee_results_for_zero():
    assert jsmath.js_div(6, 3) == 2
    assert jsmath.js_div(1, 0) == math.inf
    assert jsmath.js_div(-1, 0) == -math.inf
    assert jsmath.js_div(1, -0.0) == -math.inf
    assert jsmath.js_div(-1, -0.0) == math.inf
    assert math.isnan(jsmath.js_div(0, 0))
    assert math.isnan(jsmath.js_div(math.nan, 0))
    assert math.isnan(jsmath.js_div(math.nan, 2))


def test_js_max_and_min_propagate_nan():
    assert jsmath.js_max(2, 1) == 2 and jsmath.js_max(1, 2) == 2
    assert jsmath.js_min(2, 1) == 1 and jsmath.js_min(1, 2) == 1
    for a, b in [(math.nan, 1.0), (1.0, math.nan), (math.nan, math.nan)]:
        assert math.isnan(jsmath.js_max(a, b))
        assert math.isnan(jsmath.js_min(a, b))


def test_js_sqrt_acos_sin_are_nan_where_js_is():
    assert jsmath.js_sqrt(4) == 2
    assert math.isnan(jsmath.js_sqrt(-1)) and math.isnan(jsmath.js_sqrt(math.nan))
    assert jsmath.js_acos(1) == 0
    assert math.isnan(jsmath.js_acos(2)) and math.isnan(jsmath.js_acos(-1.5))
    assert math.isnan(jsmath.js_acos(math.nan))
    assert jsmath.js_sin(0) == 0
    assert math.isnan(jsmath.js_sin(math.inf)) and math.isnan(jsmath.js_sin(-math.inf))
    assert math.isnan(jsmath.js_sin(math.nan))


def test_js_floor_and_ceil_keep_non_finite_values():
    assert jsmath.js_floor(2.7) == 2.0 and jsmath.js_floor(-2.5) == -3.0
    assert jsmath.js_ceil(2.1) == 3.0 and jsmath.js_ceil(-2.5) == -2.0
    assert jsmath.js_floor(math.inf) == math.inf and jsmath.js_floor(-math.inf) == -math.inf
    assert jsmath.js_ceil(math.inf) == math.inf and jsmath.js_ceil(-math.inf) == -math.inf
    assert math.isnan(jsmath.js_floor(math.nan)) and math.isnan(jsmath.js_ceil(math.nan))
    assert isinstance(jsmath.js_floor(3), float) and isinstance(jsmath.js_ceil(3), float)


# ---------------------------------------------------------------------------
# Array twins of geometry's sprite maths (geometry.radial_source_arr, headroom_bump_arr).
# ---------------------------------------------------------------------------

def _radii() -> np.ndarray:
    return np.array([0.0, 0.5, 1.0, 2.0, 3.0, 4.5, 5.0, 7.25, 12.0, 25.0, 100.0], dtype=np.float64)


@pytest.mark.parametrize("seen", [1.0, 0.5, 0.05, 0.01, 0.0, 2.0])
@pytest.mark.parametrize("g", [1.0, 2.0, 0.5])
def test_radial_source_arr_is_the_scalar_elementwise(seen, g):
    ro = _radii()
    got = geo.radial_source_arr(ro, 5.0, g, seen)
    for r, v in zip(ro, got):
        assert v == geo.radial_source(float(r), 5.0, g, seen)


def test_radial_source_arr_nan_seen_floors_to_0_05():
    # The array twin floors with Python max, so a NaN seen reads as 0.05 (the sprite path always had it; frames
    # depend on it). The scalar keeps NaN (js_max): the two differ on purpose, and this pins the array one.
    ro = _radii()
    assert np.array_equal(geo.radial_source_arr(ro, 5.0, 2.0, math.nan),
                          geo.radial_source_arr(ro, 5.0, 2.0, 0.05), equal_nan=True)
    assert math.isnan(geo.radial_source(25.0, 5.0, 2.0, math.nan))


@pytest.mark.parametrize("sigma, gain", [(2.0, 4.0), (0.5, 1.5), (7.0, 3.0), (1.0, 1.0)])
def test_headroom_bump_arr_is_the_scalar_elementwise(sigma, gain):
    r = _radii()
    got = geo.headroom_bump_arr(r, sigma, gain)
    for x, v in zip(r, got):
        assert v == pytest.approx(geo.headroom_bump(float(x), sigma, gain), rel=1e-15, abs=1e-15)


def test_headroom_bump_arr_centre_and_far_limits():
    got = geo.headroom_bump_arr(np.array([0.0, 1e6]), 2.0, 4.0)
    assert got[0] == 4.0
    assert got[1] == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("v", [None, 0, 0.0, -0.0, math.nan])
def test_is_falsy_and_js_or_take_the_fallback(v):
    assert jsmath.is_falsy(v) is True
    assert jsmath.js_or(v, 7.5) == 7.5


@pytest.mark.parametrize("v", [1, -2.5, 1e-300, math.inf, -math.inf])
def test_is_falsy_false_and_js_or_keeps_the_value(v):
    assert jsmath.is_falsy(v) is False
    assert jsmath.js_or(v, 7.5) == v


@pytest.mark.parametrize("v", [np.float64(np.nan), np.float32(0.0), np.float64(0.0), np.float32(np.nan)])
def test_js_or_numpy_scalars_take_the_fallback(v):
    """The call sites pass numpy floats (flux, MAD): NaN and zero of any float type are falsy."""
    assert jsmath.js_or(v, 7.5) == 7.5


def test_js_or_returns_the_fallback_object_unchanged():
    assert isinstance(jsmath.js_or(None, 1.0), float)
    assert jsmath.js_or(None, 1) == 1
