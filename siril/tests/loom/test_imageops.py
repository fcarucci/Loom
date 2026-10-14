"""Pixel helpers of loomsiril.loom.imageops: pinned to Loom's JavaScript (tests/oracle/loom_imageops.json) and to
the white-balance golden of Loom's self-test, plus synthetic checks of the numerical behaviour."""

from __future__ import annotations

import math
import re

import numpy as np
import pytest

from loomsiril.core import unscreen
from loomsiril.loom import imageops as io


def _frame(w: int, h: int, top: int, bottom: int, left: int, right: int, value: float = 1.0) -> np.ndarray:
    """Zero fill outside [top, h-bottom) x [left, w-right) and ``value`` inside."""
    p = np.zeros((h, w), dtype=np.float64)
    p[top : h - bottom, left : w - right] = value
    return p


# --- oracle replay -----------------------------------------------------------------------------------------


def test_valid_rect_matches_loom(oracle) -> None:
    cases = oracle("loom_imageops")["cases"]["valid_rect"]
    assert len(cases) == 7
    for case in cases:
        plane = np.asarray(case["data"], dtype=np.float64)
        got = io.valid_rect(plane) if case["margin"] is None else io.valid_rect(plane, case["margin"])
        assert list(got) == case["rect"]


def test_intersect_rects_matches_loom(oracle) -> None:
    for case in oracle("loom_imageops")["cases"]["intersect_rects"]:
        rects = [(r["x0"], r["y0"], r["x1"], r["y1"]) for r in case["rects"]]
        got = io.intersect_rects(rects)
        assert (list(got) if got is not None else None) == case["result"]


def test_central_rect_matches_loom(oracle) -> None:
    for case in oracle("loom_imageops")["cases"]["central_rect"]:
        assert list(io.central_rect(case["w"], case["h"], case["fraction"])) == case["rect"]


def test_min_median_key_matches_loom(oracle) -> None:
    for case in oracle("loom_imageops")["cases"]["min_median_key"]:
        assert io.min_median_key(case["medians"]) == case["key"]


def test_sigma_to_reach_matches_loom(oracle) -> None:
    for case in oracle("loom_imageops")["cases"]["sigma_to_reach"]:
        assert io.sigma_to_reach(case["target"], case["current"]) == pytest.approx(case["result"], rel=1e-15, abs=0)


def test_sigma_fwhm_matches_loom(oracle) -> None:
    for case in oracle("loom_imageops")["cases"]["sigma_fwhm"]:
        assert io.sigma_to_fwhm(case["sigma"]) == case["fwhm"]
        assert io.fwhm_to_sigma(case["fwhm"]) == pytest.approx(case["back"], rel=1e-15)


def test_inconsistent_keywords_matches_loom(oracle) -> None:
    for case in oracle("loom_imageops")["cases"]["inconsistent_keywords"]:
        headers = [{k["name"]: k["value"] for k in lst} for lst in case["lists"]]
        assert io.inconsistent_keywords(headers) == case["result"]


def test_common_area_decision_matches_loom(oracle) -> None:
    """cropToCommonArea's decision: the common rectangle, or the refusal with Loom's exact message."""
    cases = oracle("loom_imageops")["cases"]["common_area"]
    assert len(cases) == 6
    for case in cases:
        planes = {k: _frame(*spec) for k, spec in case["specs"].items()}
        rects = [io.valid_rect(p, 4) for p in planes.values()]
        ref = planes[case["refKey"]]
        ref_rect = (0, 0, ref.shape[1], ref.shape[0])
        result = case["result"]
        if result["ok"]:
            assert list(io.common_area(rects, ref_rect)) == result["rect"], case["name"]
        else:
            with pytest.raises(ValueError, match=re.escape(result["error"])):
                io.common_area(rects, ref_rect)


# --- rectangles and crop geometry --------------------------------------------------------------------------


def test_valid_rect_trims_black_registration_borders() -> None:
    p = _frame(60, 40, top=5, bottom=3, left=2, right=7)
    # data rows 5..36, columns 2..52 -> margin 4 on every side, end exclusive
    assert io.valid_rect(p) == (2 + 4, 5 + 4, 53 - 4, 37 - 4)


def test_valid_rect_ignores_a_hot_pixel_in_the_fill() -> None:
    p = _frame(30, 20, top=2, bottom=2, left=2, right=2)
    p[0, 10] = 0.95  # one cosmic ray in an empty strip: the strip's median stays 0
    assert io.valid_rect(p, 0) == (2, 2, 28, 18)


def test_valid_rect_margin_zero_keeps_full_data_extent() -> None:
    p = np.full((12, 15), 0.2)
    assert io.valid_rect(p, 0) == (0, 0, 15, 12)


def test_common_area_refuses_below_half_with_loom_message() -> None:
    rects = [(0, 0, 10, 10), (0, 0, 4, 10)]
    with pytest.raises(ValueError, match="only 8.0% of the reference frame"):
        io.common_area(rects, (0, 0, 20, 25))


def test_common_area_threshold_is_percentage_of_reference() -> None:
    assert io.common_area([(0, 0, 10, 10)], (0, 0, 10, 10)) == (0, 0, 10, 10)
    with pytest.raises(ValueError):
        io.common_area([(0, 0, 10, 10)], (0, 0, 21, 10))  # 47.6%


# --- medians and background ---------------------------------------------------------------------------------


def test_median_of_centre_uses_the_central_fraction() -> None:
    p = np.zeros((100, 100))
    p[:10, :] = 5.0  # outside the central 60%: must not count
    p[20:80, 20:80] = 0.25
    assert io.median_of_centre([p], 0.6) == 0.25


def test_background_offset_is_not_truncated() -> None:
    p = np.full((50, 50), 0.2, dtype=np.float32)
    p[0:5, 0:5] = 0.0
    (out,) = io.background_offset([p], ref_median=0.05)
    assert out.dtype == np.float32
    assert out.min() == pytest.approx(-0.15, abs=1e-6)  # the zero pixels go below zero
    assert float(np.median(out)) == pytest.approx(0.05, abs=1e-6)


# --- white balance ---------------------------------------------------------------------------------------


def test_white_balance_matches_loom_selftest_golden() -> None:
    """lib/selftest: backgrounds 0.1/0.2/0.3, one red star at 0.3, factors [3, 1, 0.5]."""
    planes = []
    for bg in (0.1, 0.2, 0.3):
        planes.append(np.full((64, 64), bg, dtype=np.float32))
    planes[0][10, 10] = 0.3
    out = io.apply_white_balance(planes, [3, 1, 0.5])
    medians = [f"{float(np.median(o)):.6f}" for o in out]
    assert medians == ["0.216667", "0.216667", "0.216667"]
    assert f"{float(out[0][10, 10]):.6f}" == "0.816667"


def test_white_balance_needs_three_factors() -> None:
    planes = [np.zeros((4, 4), np.float32)] * 3
    with pytest.raises(ValueError):
        io.apply_white_balance(planes, [1.0, 1.0])


def test_recover_wb_factors_seeded_noise() -> None:
    rng = np.random.default_rng(20261013)
    gains = [1.2, 0.8, 1.05]
    offsets = [0.01, -0.005, 0.0]
    before = [rng.uniform(0.05, 0.5, (200, 200)) for _ in gains]
    after = [k * b + o + rng.normal(0.0, 1e-4, b.shape) for k, o, b in zip(gains, offsets, before)]
    got = io.recover_wb_factors(before, after)
    assert np.allclose(got, gains, atol=1e-4, rtol=0)


def test_recover_wb_factors_exact_on_linear_data() -> None:
    rng = np.random.default_rng(7)
    before = [rng.uniform(0.01, 0.8, (80, 80)) for _ in range(3)]
    gains = [0.7, 1.3, 2.0]
    after = [k * b + 0.05 for k, b in zip(gains, before)]
    got = io.recover_wb_factors(before, after)
    assert np.allclose(got, gains, atol=1e-9, rtol=0)


def test_sigma_to_reach_is_quadrature_difference() -> None:
    assert io.sigma_to_reach(3.0, 2.0) == pytest.approx(math.sqrt(5.0), rel=1e-15)
    assert io.sigma_to_reach(2.0, 2.0) == 0.0
    assert io.sigma_to_reach(1.0, 2.0) == 0.0
    assert io.sigma_to_reach(float("nan"), 2.0) == 0.0


def _moment_sigma(img: np.ndarray) -> float:
    y, x = np.indices(img.shape)
    w = img / img.sum()
    cx = float((w * x).sum())
    cy = float((w * y).sum())
    var = float((w * ((x - cx) ** 2 + (y - cy) ** 2)).sum()) / 2
    return math.sqrt(var)


def test_gaussian_blur_adds_variances() -> None:
    n = 129
    y, x = np.indices((n, n))
    s1 = 2.0
    star = np.exp(-((x - 64) ** 2 + (y - 64) ** 2) / (2 * s1 * s1))
    (blurred,) = io.gaussian_blur([star], 3.0)
    expected = math.sqrt(s1 * s1 + 3.0 * 3.0)
    assert _moment_sigma(blurred) == pytest.approx(expected, rel=0.05)
    assert _moment_sigma(star) == pytest.approx(s1, rel=0.01)


def test_gaussian_blur_non_positive_sigma_is_identity() -> None:
    p = np.arange(12, dtype=np.float32).reshape(3, 4)
    (out,) = io.gaussian_blur([p], 0.0)
    assert np.array_equal(out, p) and out is not p


def test_frequency_separation_recombines_exactly_where_unclipped() -> None:
    rng = np.random.default_rng(5)
    x = 0.5 + 0.1 * rng.standard_normal((64, 64))
    low, high = io.frequency_separate(x, sigma=2.0)
    raw = (x - low) / io.FS_SCALE + io.FS_PEDESTAL
    inside = (raw >= 0) & (raw <= 1)
    assert inside.all()
    recombined = low + (high - io.FS_PEDESTAL) * io.FS_SCALE
    assert np.max(np.abs(recombined[inside] - x[inside])) < 1e-12


def test_inconsistent_keywords_strip_and_missing() -> None:
    headers = [{"FILTER": "Ha", "EXPTIME": "300 ", "X": "1"}, {"FILTER": "Ha", "EXPTIME": "300", "Y": "2"}]
    assert io.inconsistent_keywords(headers) == ["X", "Y"]


def test_without_inconsistent_keywords_drops_only_disagreements() -> None:
    headers = [{"FILTER": "Ha", "EXPTIME": "300"}, {"FILTER": "OIII", "EXPTIME": "300"}]
    assert io.without_inconsistent_keywords(headers[0], headers) == {"EXPTIME": "300"}


# --- pinned constants and edge behaviour -------------------------------------------------------------------


def test_loom_constants_are_pinned() -> None:
    assert io.FS_PEDESTAL == 0.5 and io.FS_SCALE == 2.0 and io.FS_SIGMA_FACTOR == 2.0
    assert io.MIN_COMMON_PERCENT == 50.0 and io.VALID_RECT_MARGIN == 4
    assert io.FWHM_PER_SIGMA == 2.3548200450309493


def test_psf_sigma_even_length_uses_loom_upper_median() -> None:
    # Loom's med(): sorted, element len >> 1; for [4, 5, 6, 7] that is 6, not the mean 5.5
    assert io.psf_sigma_from_fwhm([7.0, 4.0, 6.0, 5.0], [5.0, 7.0, 4.0, 6.0]) == pytest.approx(
        ((6.0 + 6.0) / 2) / 2.3548200450309493, rel=1e-15
    )


def test_psf_sigma_odd_length_and_unsorted() -> None:
    assert io.psf_sigma_from_fwhm([9.0, 1.0, 5.0], [2.0, 8.0, 3.0]) == pytest.approx(
        ((5.0 + 3.0) / 2) / 2.3548200450309493, rel=1e-15
    )


def test_frequency_separation_high_layer_clips_both_sides() -> None:
    x = np.zeros((31, 31))
    x[15, 15] = 5.0   # raw high = (5 - low)/2 + 0.5 > 1
    x[5, 5] = -5.0    # raw high = (-5 - low)/2 + 0.5 < 0
    low, high = io.frequency_separate(x, sigma=1.0)
    assert high[15, 15] == 1.0 and high[5, 5] == 0.0
    expected = np.clip((x - low) / 2.0 + 0.5, 0.0, 1.0)
    assert np.max(np.abs(high - expected)) < 1e-15


def test_frequency_separation_recombination_uses_the_literal_constants() -> None:
    rng = np.random.default_rng(9)
    x = 0.5 + 0.05 * rng.standard_normal((40, 40))
    low, high = io.frequency_separate(x, sigma=1.5)
    assert np.max(np.abs(low + (high - 0.5) * 2.0 - x)) < 1e-12


def test_white_balance_gains_round_to_ten_decimals() -> None:
    rng = np.random.default_rng(3)
    planes = [rng.uniform(0.01, 0.5, (30, 30)) for _ in range(3)]
    factors = [1.0 / 3.0, 1.0 / 7.0, 2.0 / 3.0]
    got = io._white_balance_f64(planes, factors)
    gains = [round(f, 10) for f in factors]
    gained = [p * g for p, g in zip(planes, gains)]
    medians = [float(np.median(g)) for g in gained]
    target = sum(medians) / 3
    expected = [g + float(f"{target - m:.12f}") for g, m in zip(gained, medians)]
    for e, o in zip(expected, got):
        assert np.max(np.abs(e - o)) < 1e-12
    unrounded = [p * f for p, f in zip(planes, factors)]
    assert np.max(np.abs(unrounded[0] - got[0])) > 1e-11


def test_white_balance_offsets_round_to_twelve_decimals() -> None:
    planes = [np.full((8, 8), v) for v in (0.1234567890123, 0.2345678901234, 0.3456789012345)]
    got = io._white_balance_f64(planes, [1.0, 1.0, 1.0])
    target = sum(float(np.median(p)) for p in planes) / 3
    for p, o in zip(planes, got):
        expected = p + float(f"{target - float(np.median(p)):.12f}")
        assert np.max(np.abs(o - expected)) < 1e-15


def test_background_offset_uses_the_central_sixty_percent() -> None:
    p = np.full((100, 100), 0.9)
    p[20:80, 30:63] = 0.2  # 1980 px: 55% of the central 60% square, 40% of the 70% one, 19.8% of the plane
    (out,) = io.background_offset([p], ref_median=0.5)
    assert out[0, 0] == pytest.approx(0.9 + 0.3, abs=1e-6)  # the offset is the 0.2 central median, 0.3
    assert io.median_of_centre([p], 0.6) == 0.2
    assert io.median_of_centre([p], 0.7) == 0.9
    assert io.median_of_centre([p], 1.0) == 0.9


def test_median_of_centre_pools_all_planes() -> None:
    planes = [np.full((50, 50), v) for v in (1.0, 2.0, 3.0)]
    assert io.median_of_centre(planes, 0.6) == 2.0


def test_valid_rect_hot_pixel_in_a_fill_column_is_ignored() -> None:
    p = _frame(30, 20, top=0, bottom=0, left=5, right=5)
    p[7, 1] = 0.9  # one bright pixel in the empty first column: its median stays 0
    assert io.valid_rect(p, 0) == (5, 0, 25, 20)


def test_valid_rect_all_fill_plane_clamps_to_the_far_corner() -> None:
    assert io.valid_rect(np.zeros((5, 6)), 4) == (6, 5, 2, 1)


def test_common_area_accepts_exactly_fifty_percent() -> None:
    assert io.common_area([(0, 0, 10, 5)], (0, 0, 10, 10)) == (0, 0, 10, 5)


def test_common_area_zero_area_reference_does_not_raise() -> None:
    assert io.common_area([(0, 0, 3, 3)], (0, 0, 0, 0)) == (0, 0, 3, 3)


def test_inconsistent_keywords_sorted_and_stripped() -> None:
    headers = [
        {" ZED ": "1", "MID": "a", "ALPHA": "x"},
        {"ZED": "2", "MID": "b", "ALPHA": "y"},
    ]
    assert io.inconsistent_keywords(headers) == ["ALPHA", "MID", "ZED"]


def test_inconsistent_keywords_one_and_one_point_zero_agree() -> None:
    assert io.inconsistent_keywords([{"X": 1}, {"X": 1.0}]) == []
    assert io.inconsistent_keywords([{"X": 1}, {"X": 1.5}]) == ["X"]


def test_recover_wb_factors_excludes_saturated_and_zero_pixels() -> None:
    rng = np.random.default_rng(21)
    b = rng.uniform(0.05, 0.5, 4000)
    a = 1.1 * b + 0.03
    # wild values where the masks must exclude them: before or after at/above 0.9, or at/below 0
    wild_b = np.array([0.9, 0.95, 0.0, -0.2, 0.5, 0.5, 0.3, 0.3])
    wild_a = np.array([0.5, 0.5, 0.5, 0.5, 0.9, 0.95, 0.0, -0.1]) * 40
    b2 = np.concatenate([b, wild_b])
    a2 = np.concatenate([a, wild_a])
    (k,) = io.recover_wb_factors([b2], [a2])
    assert k == pytest.approx(1.1, abs=1e-9)


def test_recover_wb_factors_outliers_inside_the_mask_are_clipped() -> None:
    rng = np.random.default_rng(31)
    b = rng.uniform(0.05, 0.5, 20000)
    a = 0.8 * b + 0.1 + rng.normal(0.0, 1e-4, b.shape)
    bad = rng.choice(b.size, size=200, replace=False)
    a[bad] += 0.25  # 1% outliers, all inside the saturation masks
    (k,) = io.recover_wb_factors([b], [a])
    assert k == pytest.approx(0.8, abs=1e-4)


def test_recover_wb_factors_float32_quantised_data() -> None:
    rng = np.random.default_rng(41)
    b = rng.uniform(0.05, 0.6, (120, 120)).astype(np.float32)
    a = (1.1 * b.astype(np.float64) + 0.05).astype(np.float32)
    (k,) = io.recover_wb_factors([b], [a])
    assert k == pytest.approx(1.1, abs=1e-6)


def test_recover_wb_factors_striding_keeps_exact_linear_fit() -> None:
    rng = np.random.default_rng(51)
    b = rng.uniform(0.05, 0.6, 4_200_000)
    a = 0.6 * b + 0.2
    (k,) = io.recover_wb_factors([b], [a])
    assert k == pytest.approx(0.6, abs=1e-9)


def test_common_area_refusal_text_for_ratio_below_half() -> None:
    with pytest.raises(ValueError, match=re.escape("only 49.9% of the reference frame")):
        io.common_area([(0, 0, 499, 100)], (0, 0, 1000, 100))


def test_background_offset_rounds_to_twelve_decimals() -> None:
    p = np.full((20, 20), 0.12345678901234)
    (out,) = io._background_offset_f64([p], ref_median=0.76543219876543)
    delta = float(f"{0.76543219876543 - 0.12345678901234:.12f}")
    assert np.max(np.abs(out - (0.12345678901234 + delta))) < 1e-15


def test_background_offset_uses_the_sixty_percent_square_not_half() -> None:
    p = np.full((100, 100), 0.9)
    p[20:80, 20:80] = 0.2
    p[25:51, 25:75] = 0.9  # central 50%: 1300 of 2500 are 0.9; central 60%: 2300 of 3600 are 0.2
    assert io.median_of_centre([p], 0.5) == 0.9
    assert io.median_of_centre([p], 0.6) == 0.2
    (out,) = io.background_offset([p], ref_median=0.5)
    assert out[0, 0] == pytest.approx(0.9 + 0.3, abs=1e-6)


def test_wb_fit_mask_boundaries() -> None:
    before = np.array([0.0, 1e-12, 0.5, 0.9, 0.8999999, 0.5, 0.5, -0.1])
    after = np.array([0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.9, 0.5])
    assert io._wb_fit_mask(before, after).tolist() == [False, True, True, False, True, False, False, False]


def test_unscreen_stars_is_core_unscreen() -> None:
    from loomsiril.core import unscreen as core_unscreen

    image = np.array([[0.5, 0.2], [0.9, 0.1]])
    starless = np.array([[0.25, 0.4], [0.5, 0.1]])
    got = io.unscreen_stars(image, starless)
    assert np.array_equal(got, core_unscreen(image, starless))
    assert got.max() <= 1.0


def test_gaussian_blur_is_nearest_edge_mode_with_truncate_four() -> None:
    sigma = 2.0
    n = 40
    ramp = np.arange(n, dtype=np.float64)
    radius = int(4.0 * sigma + 0.5)
    w = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    w /= w.sum()
    expected = np.empty(n)
    for i in range(n):
        idx = np.clip(i + np.arange(-radius, radius + 1), 0, n - 1)
        expected[i] = float(np.dot(w, ramp[idx]))
    (got,) = io.gaussian_blur([np.tile(ramp, (3, 1))], sigma)
    assert np.max(np.abs(got[1] - expected)) < 1e-9

def _reference_factor(before: np.ndarray, after: np.ndarray) -> float:
    """Independent statement of the factor fit: polyfit, 3-sigma residual clipping, at most ten rounds."""
    m = (before > 0) & (before < 0.9) & (after > 0) & (after < 0.9)
    x = before[m]
    y = after[m]
    keep = np.ones(x.size, dtype=bool)
    for _ in range(10):
        k, c = np.polyfit(x[keep], y[keep], 1)
        r = y - (k * x + c)
        spread = float(np.std(r[keep]))
        if spread <= 1e-12:
            break
        nxt = np.abs(r) <= 3.0 * spread
        if np.array_equal(nxt, keep):
            break
        keep = nxt
    return float(k)


def test_recover_wb_factors_matches_the_reference_fit_on_noisy_outliers() -> None:
    rng = np.random.default_rng(77)
    b = rng.uniform(0.02, 0.6, 6000)
    a = 0.85 * b + 0.04 + rng.normal(0.0, 2e-3, b.shape)
    idx = rng.choice(b.size, size=600, replace=False)
    a[idx] += rng.uniform(0.005, 0.08, idx.size)  # moderate deviations that sit near the clip width
    (got,) = io.recover_wb_factors([b], [a])
    assert got == pytest.approx(_reference_factor(b, a), abs=1e-10)
