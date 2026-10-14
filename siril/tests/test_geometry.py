"""loomsiril.geometry against the JS oracle (tests/oracle/geometry.json) and the selftest checks."""

from __future__ import annotations

import math
from typing import Any

import pytest

from loomsiril import geometry as g
from loomsiril.core import Point, radec, separation, vec

# 1e-12 relative for the arithmetic replayed from the oracle; the sine hash of
# seeded()/twinkle() is loosened to the JS near() epsilon (libm and V8 sin
# differ by an ulp, and the hash multiplies it by 43758).
TOL = 1e-12
HASH_TOL = 1e-9


def _match(got: Any, want: Any, tol: float = TOL) -> bool:
    """Recursive comparison: dict keys, list lengths, NaN == NaN, bool before number."""
    if isinstance(want, dict):
        return isinstance(got, dict) and set(got) == set(want) and all(_match(got[k], want[k], tol) for k in want)
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and all(_match(a, b, tol) for a, b in zip(got, want))
    if want is None or isinstance(want, bool):
        return got is want or got == want
    if isinstance(want, float) and math.isnan(want):
        return isinstance(got, float) and math.isnan(got)
    if isinstance(want, float) and math.isinf(want):
        return got == want
    return math.isclose(got, want, rel_tol=tol, abs_tol=tol * 1e-3)


def _point(d: Any) -> Point:
    return Point(d["x"], d["y"])


def _cases(oracle, name: str):
    return oracle("geometry")["cases"][name]


def test_constants_match_js(oracle):
    c = oracle("geometry")["cases"]["constants"][0]["out"]
    assert g.BACKDROP_MOTION_DEFAULT == c["BACKDROP_MOTION_DEFAULT"]
    assert g.HDR_MAG_RANGE == c["HDR_MAG_RANGE"]
    assert g.HEADROOM_RADIUS == c["HEADROOM_RADIUS"]
    assert g.HEADROOM_SIGMA == c["HEADROOM_SIGMA"]
    assert g.HDR_REFERENCE_WHITE == c["HDR_REFERENCE_WHITE"]
    assert g.SPIKE_LENGTH_MAX == c["SPIKE_LENGTH_MAX"]
    assert g.SHUTTER == c["SHUTTER"]
    assert g.TWINKLE_DEFAULT == c["TWINKLE_DEFAULT"]
    assert g.GROWTH_DEFAULT == c["GROWTH_DEFAULT"]
    assert list(g.MODEL_BLEND) == c["MODEL_BLEND"]
    assert list(g.CORE_MODEL_BLEND) == c["CORE_MODEL_BLEND"]
    assert list(g.FADE_NEAR) == c["FADE_NEAR"]


def test_moved_matches_js(oracle):
    for case in _cases(oracle, "moved"):
        got = g.moved(*case["args"])
        assert _match(got, case["out"]), case


def test_screen_position_matches_js(oracle):
    for case in _cases(oracle, "screenPosition"):
        c, p0, tp, k = case["args"]
        got = g.screen_position(_point(c), _point(p0), _point(tp), k)
        assert _match({"x": got.x, "y": got.y}, case["out"]), case


@pytest.mark.parametrize("name, fn", [
    ("backdropZoom", g.backdrop_zoom), ("backdropScale", g.backdrop_scale), ("radialSource", g.radial_source),
    ("discVisible", g.disc_visible), ("discGridCount", g.disc_grid_count), ("coverFraction", g.cover_fraction),
    ("headroomBump", g.headroom_bump), ("magnitudeGain", g.magnitude_gain), ("spikeLength", g.spike_length),
    ("shutterSteps", g.shutter_steps), ("growth", g.growth), ("pixelScale", g.pixel_scale),
    ("coreModelWeight", g.core_model_weight), ("modelWeight", g.model_weight), ("opacity", g.opacity),
    ("ease", g.ease),
])
def test_replays_oracle(oracle, name, fn):
    for case in _cases(oracle, name):
        got = fn(*case["args"])
        assert _match(got, case["out"]), (name, case, got)


@pytest.mark.parametrize("name", ["seeded", "twinkle"])
def test_hash_and_twinkle_replay_oracle(oracle, name):
    fn = {"seeded": g.seeded, "twinkle": g.twinkle}[name]
    for case in _cases(oracle, name):
        assert _match(fn(*case["args"]), case["out"], HASH_TOL), (name, case)


# ---------------------------------------------------------------------------
# Behaviour checks (the selftest's own claims, in Python).
# ---------------------------------------------------------------------------


def test_vec_radec_round_trip():
    r = radec(vec(324.745, 57.514))
    assert r["ra"] == pytest.approx(324.745, abs=1e-9)
    assert r["dec"] == pytest.approx(57.514, abs=1e-9)


T = {"ra": 324.745, "dec": 57.514}


def test_moved_on_axis_keeps_direction_and_ratio_d_over_d_minus_s():
    on = g.moved(324.745, 57.514, 500, T, 100)
    assert on["ra"] == pytest.approx(324.745, abs=1e-9)
    assert on["dec"] == pytest.approx(57.514, abs=1e-9)
    assert on["ratio"] == pytest.approx(500 / 400, rel=1e-12)
    assert on["front"] is True


def test_moved_off_axis_is_exact():
    off = g.moved(324.745, 58.514, 100, T, 50)
    th = math.pi / 180
    px, pz = 100 * math.sin(th), 100 * math.cos(th) - 50
    assert separation(off, T) == pytest.approx(math.atan2(px, pz) * 180 / math.pi, abs=1e-9)
    assert off["ratio"] == pytest.approx(100 / math.sqrt(px * px + pz * pz), rel=1e-12)


def test_passed_star_is_behind_the_camera():
    assert g.moved(324.745, 57.6, 40, T, 60)["front"] is False


def test_moved_on_the_camera_gives_infinite_ratio():
    on_cam = g.moved(T["ra"], T["dec"], 50, T, 50)
    assert on_cam["ratio"] == math.inf


def test_backdrop_k_and_galaxy():
    assert g.backdrop_scale(900, 180) == pytest.approx(900 / 720, rel=1e-15)
    assert g.backdrop_scale(math.inf, 180) == 1
    assert g.backdrop_zoom(math.inf, 180, 1) == 1
    assert g.backdrop_zoom(900, 180, 0) == 1
    assert g.backdrop_zoom(900, 180, 0.4) == pytest.approx(1 + 0.4 * (900 / 720 - 1), rel=1e-12)
    assert g.BACKDROP_MOTION_DEFAULT == 0.4


def test_growth_rule_and_inverse_square():
    assert g.growth(1, 0.15) == 1
    assert g.growth(3, 0.15) == pytest.approx(1.3, abs=1e-12)
    grow = g.growth(2.5, 0.2)
    assert g.pixel_scale(2.5, 0.2, True) * grow * grow == pytest.approx(2.5 * 2.5, abs=1e-12)
    assert g.pixel_scale(2.5, 0.2, False) * grow * grow == pytest.approx(1, abs=1e-12)


def test_opacity_monotone_and_zero_behind():
    assert g.opacity(2, True) == 1
    assert g.opacity(25, True) == 0
    assert g.opacity(1.5, False) == 0
    samples = [g.opacity(1 + 0.25 * k, True) for k in range(97)]
    assert all(b <= a + 1e-12 for a, b in zip(samples, samples[1:]))


def test_twinkle_deterministic_and_first_frame_untouched():
    series = [g.twinkle(17, k / 30, 0.03, 0) for k in range(301)]
    again = [g.twinkle(17, k / 30, 0.03, 0) for k in range(301)]
    assert series == again
    assert series[0] == pytest.approx(1, abs=1e-15)
    assert 0.9 < min(series) and max(series) < 1.1 and max(series) - min(series) > 0.02
    assert max(abs(b - a) for a, b in zip(series, series[1:])) < 0.01
    assert g.twinkle(1, 2.5, 0.03, 0) != g.twinkle(2, 2.5, 0.03, 0)
    assert g.twinkle(1, 2.5, 0, 0) == 1


def test_seeded_in_unit_interval():
    vals = [g.seeded(s, k) for s in range(20) for k in range(6)]
    assert all(0 <= v < 1 for v in vals)


def test_shutter_steps_rules():
    assert g.shutter_steps(0.2) == 1
    assert g.shutter_steps(6) == 8
    assert g.shutter_steps(100) == 12


def test_radial_source_and_spikes():
    assert g.radial_source(3, 5, 2) == 3
    assert g.radial_source(25, 5, 2) == 15
    assert g.radial_source(25, 5, 1) == 25
    assert g.radial_source(25, 5, 2, 0.5) == 25
    assert g.spike_length(1, True, 1) == 1
    assert g.spike_length(3, True, 1.3) == 3
    assert g.spike_length(9, True, 2.2) == g.SPIKE_LENGTH_MAX
    assert g.spike_length(3, False, 1.3) == 1.3


def test_visibility_and_cover():
    assert g.disc_visible(50, 50, 5, 100, 100) == 1
    assert g.disc_visible(0, 50, 5, 100, 100) == pytest.approx(0.5, abs=0.02)
    assert g.disc_visible(100, 100, 5, 100, 100) == pytest.approx(0.25, abs=0.02)
    assert g.disc_visible(-10, 50, 5, 100, 100) == 0
    assert g.cover_fraction(5, 5, 11) == 0
    assert g.cover_fraction(3, 10, 4) == 1
    assert g.cover_fraction(5, 5, 5) == pytest.approx(0.391, abs=1e-3)


def test_headroom_and_magnitude_gain():
    assert g.headroom_bump(0, 2, 4) == 4
    assert g.headroom_bump(20, 2, 4) == pytest.approx(1, abs=1e-12)
    assert g.magnitude_gain(5, 5, 4) == 4
    assert g.magnitude_gain(7.5, 5, 4) == pytest.approx(2.5, abs=1e-12)
    assert g.magnitude_gain(10, 5, 4) == 1
    assert g.HDR_REFERENCE_WHITE == 203


def test_js_corners_nan_and_infinity_do_not_raise():
    assert math.isnan(g.radial_source(25, 5, 2, math.nan))
    assert math.isnan(g.shutter_steps(math.nan))
    assert g.shutter_steps(math.inf) == 12
    assert math.isnan(g.spike_length(math.nan, True, 1))
    assert g.pixel_scale(2, -1, True) == math.inf
    assert g.cover_fraction(5, 5, math.nan) != g.cover_fraction(5, 5, 5)


def test_ease_modes():
    assert g.ease(0.3, "linear") == 0.3
    assert g.ease(0.3, "smooth") == pytest.approx(0.216, abs=1e-12)
    assert abs(g.ease(0.001, "smoothstep") - g.ease(0, "smoothstep")) < 1e-5
