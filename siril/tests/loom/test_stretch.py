"""MTF histogram stretch: parameters, clip-safe black point, reference apply and Siril commands.

Numbers recorded from Loom's JavaScript (tools/oracle/loom_stretch.js) are checked to 1e-12 relative; the rest is
synthetic seeded data. Nothing here uses the network or writes outside pytest's tmp_path.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from loomsiril.loom import stretch as st


def _rel(expected: float) -> pytest.approx:
    # abs floor only for expected values that are exactly zero
    return pytest.approx(expected, rel=1e-12, abs=1e-300)


# ---- oracle: parameter rule ------------------------------------------------------------------------------------


def test_constants_match_loom(oracle) -> None:
    c = oracle("loom_stretch")["cases"]["constants"]
    assert st.STRETCH_SKY_TARGET == c["sky_target"] == 0.25
    assert st.STRETCH_STARS_TARGET == c["stars_target"] == 0.5
    assert st.STRETCH_CLIP_BUDGET == c["clip_budget"] == 1e-4


@pytest.mark.parametrize(
    "case,args",
    [
        ("sp_typical", (0.05, 0.01)),
        ("sp_target_0_10", (0.05, 0.01, 0.10)),
        ("sp_negative_minimum", (0.05, -0.002)),
        ("sp_very_small_x", (0.0100001, 0.01)),
        ("sp_loom_selftest_pair", (1.44898040e-3, 8.43324524e-4)),
        ("sp_loom_selftest_pair_target_0_10", (1.5398e-3, 8.4332e-4, 0.10)),
    ],
)
def test_stretch_parameters_match_loom(oracle, case: str, args: tuple) -> None:
    expected = oracle("loom_stretch")["cases"][case]
    got = st.stretch_parameters_from(*args)
    assert set(got) == {"c0", "m", "skyOut"}
    for k in ("c0", "m", "skyOut"):
        assert got[k] == _rel(expected[k]), k


@pytest.mark.parametrize("case,args", [
    ("sp_median_equals_minimum", (0.01, 0.01)),
    ("sp_median_below_minimum", (0.005, 0.01)),
])
def test_stretch_parameters_errors_match_loom_text(oracle, case: str, args: tuple) -> None:
    expected = oracle("loom_stretch")["cases"][case]["error"]
    assert expected == st.NO_SIGNAL_MESSAGE
    with pytest.raises(ValueError) as info:
        st.stretch_parameters_from(*args)
    assert str(info.value) == expected


def test_default_target_is_the_sky_target() -> None:
    assert st.stretch_parameters_from(0.05, 0.01) == st.stretch_parameters_from(0.05, 0.01, st.STRETCH_SKY_TARGET)


# ---- oracle: midtone transfer ----------------------------------------------------------------------------------


def test_mtf_midtone_grid_matches_loom(oracle) -> None:
    for x0, t, expected in oracle("loom_stretch")["cases"]["mtf_midtone_grid"]:
        assert st.mtf_midtone_for(x0, t) == _rel(expected), (x0, t)


def test_mtf_apply_grid_matches_loom(oracle) -> None:
    for m, x, expected in oracle("loom_stretch")["cases"]["mtf_apply_grid"]:
        assert st.mtf_apply(m, x) == _rel(expected), (m, x)


def test_mtf_apply_array_equals_scalar_and_fixes_endpoints() -> None:
    m = 0.2
    xs = np.linspace(0.0, 1.0, 17)
    arr = st.mtf_apply(m, xs)
    assert arr.dtype == np.float64
    assert np.array_equal(arr, np.array([st.mtf_apply(m, float(x)) for x in xs]))
    assert st.mtf_apply(m, 0.0) == 0.0
    assert st.mtf_apply(m, 1.0) == pytest.approx(1.0, rel=1e-15)


# ---- oracle: clip-safe black point -----------------------------------------------------------------------------

CLIP_CASES = [
    "clip_none_below",
    "clip_below_within_budget",
    "clip_over_budget_distinct",
    "clip_over_budget_ties_at_cut",
    "clip_all_below_equal",
    "clip_lowest_alone_over_budget",
    "clip_lowest_alone_negative",
    "clip_budget_floor_150x135",
]


@pytest.mark.parametrize("case", CLIP_CASES)
def test_clip_safe_black_point_matches_loom(oracle, case: str) -> None:
    rec = oracle("loom_stretch")["cases"][case]
    n = rec["width"] * rec["height"]
    assert n >= 20000
    samples = np.array(rec["head"] + [rec["fill"]] * (n - len(rec["head"])), dtype=np.float32)
    got = st.clip_safe_black_point(samples.reshape(rec["height"], rec["width"]), rec["robust_min"])
    assert got == _rel(rec["result"]), case


# ---- robust minimum --------------------------------------------------------------------------------------------


def test_robust_minimum_ignores_a_single_cold_pixel() -> None:
    plane = np.full((50, 60), 0.05, dtype=np.float32)
    plane[10, 10] = 0.0
    warnings: list[str] = []
    assert st.robust_minimum_per_channel([plane], warn=warnings.append) == [float(np.float32(0.05))]
    assert float(plane.min()) == 0.0  # the raw minimum is what the cold pixel would set
    assert warnings == []


def test_robust_minimum_falls_back_to_raw_minimum_with_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    plane = np.full((20, 20), 0.05, dtype=np.float32)
    plane[3, 3] = 0.01

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("filter unavailable")

    monkeypatch.setattr(st.ndimage, "median_filter", broken)
    warnings: list[str] = []
    got = st.robust_minimum_per_channel([plane, plane], warn=warnings.append)
    assert got == [float(np.float32(0.01))] * 2
    assert len(warnings) == 1 and "filter unavailable" in warnings[0] and "raw minimum" in warnings[0]


@pytest.mark.parametrize("level", [0.1, 1.0])
def test_flat_image_has_no_signal(level: float) -> None:
    flat = np.full((40, 50), level, dtype=np.float32)
    for planes, linked in (([flat], True), ([flat, flat, flat], True), ([flat, flat, flat], False)):
        with pytest.raises(ValueError) as info:
            st.stretch_parameters_for_planes(planes, linked=linked)
        assert str(info.value) == st.NO_SIGNAL_MESSAGE, (len(planes), linked)
    with pytest.raises(ValueError) as info:
        st.stretch_parameters_from(level, level)
    assert str(info.value) == st.NO_SIGNAL_MESSAGE


@pytest.mark.parametrize("n", [0, 2, 4])
def test_plane_count_must_be_one_or_three(n: int) -> None:
    plane = np.full((20, 20), 0.1, dtype=np.float32)
    with pytest.raises(ValueError):
        st.stretch_parameters_for_planes([plane] * n, linked=True)


def _two_level_field() -> np.ndarray:
    # 150 x 200 = 30000 pixels, so the clip budget is 3 and an isolated defect is within it
    return np.where(np.arange(200)[None, :] < 100, 0.05, 0.08).astype(np.float32).repeat(150, axis=0)


def test_one_cold_pixel_does_not_set_the_black_point_end_to_end() -> None:
    plane = _two_level_field()
    plane[10, 10] = 0.0  # inside the 0.05 region
    assert float(plane.min()) == 0.0
    params = st.stretch_parameters_for_planes([plane], linked=True)
    assert params["c0"] == float(np.float32(0.05))


def test_a_3x3_cold_block_is_a_defect_only_for_the_3x3_window() -> None:
    plane = _two_level_field()
    plane[10:13, 10:13] = 0.0
    # a 3x3 median of a 3x3 block of zeros is zero, so the filtered minimum follows the block (window 3)
    assert st.robust_minimum_per_channel([plane]) == [0.0]


# ---- synthetic images ------------------------------------------------------------------------------------------


def _stars(rng: np.random.Generator, shape: tuple, n: int, amp: float, sigma: float) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    field = np.zeros(shape, dtype=np.float64)
    for _ in range(n):
        cy, cx = rng.uniform(0, h), rng.uniform(0, w)
        field += amp * rng.uniform(0.3, 1.0) * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * sigma**2))
    return field


def _colour_sky(seed: int, skies: tuple, noise: float, shape=(300, 400), stars=6) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    planes = []
    for sky in skies:
        p = sky + noise * rng.standard_normal(shape) + _stars(rng, shape, stars, 0.3, 2.0)
        planes.append(p.astype(np.float32))
    return planes


def _input_medians(planes: list) -> list[float]:
    return [float(np.median(p.astype(np.float64))) for p in planes]


def test_linked_stretch_follows_the_rule_exactly() -> None:
    planes = _colour_sky(7, (0.04, 0.05, 0.06), noise=0.002)
    linked = st.stretch_parameters_for_planes(planes, linked=True)
    c0 = linked["c0"]
    x = [(md - c0) / (1 - c0) for md in _input_medians(planes)]
    # the mean input median (after the shared black point) is what lands on the target
    assert st.mtf_apply(linked["m"], sum(x) / 3) == _rel(linked["skyOut"])
    assert linked["skyOut"] == _rel(0.25)
    out = st.apply_mtf(planes, linked, linked=True)
    # each channel is the shared transform of its own normalised median (the transfer is nonlinear, so the mean
    # of the output medians is not the target: here it is about 0.240)
    expected = [st.mtf_apply(linked["m"], xi) for xi in x]
    got = _input_medians(out)
    assert got == pytest.approx(expected, abs=1e-6)
    assert abs(sum(got) / 3 - 0.25) <= 0.012


def test_linked_midtone_follows_the_mean_of_medians_on_a_skewed_sky() -> None:
    planes = _colour_sky(21, (0.02, 0.03, 0.09), noise=0.002)
    linked = st.stretch_parameters_for_planes(planes, linked=True)
    c0 = linked["c0"]
    meds = _input_medians(planes)
    x_mean = (sum(meds) / 3 - c0) / (1 - c0)
    x_median = (sorted(meds)[1] - c0) / (1 - c0)
    assert linked["m"] == _rel(st.mtf_midtone_for(x_mean, 0.25))
    assert abs(linked["m"] - st.mtf_midtone_for(x_median, 0.25)) > 1e-3
    assert linked["skyOut"] == _rel(0.25)


def test_linked_black_point_is_clamped_at_zero_when_a_channel_goes_negative() -> None:
    planes = _colour_sky(5, (0.05, 0.06, 0.07), noise=0.002)
    planes[0][100:103, 100:103] = -0.01  # a 3x3 block below zero survives the median filter
    linked = st.stretch_parameters_for_planes(planes, linked=True)
    assert linked["c0"] == 0.0
    assert linked["skyOut"] == _rel(0.25)


def test_unlinked_top_level_numbers_are_channel_zero() -> None:
    planes = _colour_sky(13, (0.02, 0.05, 0.08), noise=0.002)
    params = st.stretch_parameters_for_planes(planes, linked=False)
    ch0 = params["channels"][0]
    assert params["c0"] == ch0["c0"] and params["m"] == ch0["m"] and params["skyOut"] == ch0["skyOut"]
    assert params["channels"][1]["c0"] != ch0["c0"]


def test_mono_sky_lands_on_the_target() -> None:
    (plane,) = _colour_sky(3, (0.03,), noise=0.002)
    params = st.stretch_parameters_for_planes([plane], linked=True)
    (out,) = st.apply_mtf([plane], params, linked=True)
    assert abs(float(np.median(out.astype(np.float64))) - 0.25) <= 0.005


def test_unlinked_uses_each_channels_own_parameters() -> None:
    planes = _colour_sky(11, (0.02, 0.05, 0.08), noise=0.002)
    params = st.stretch_parameters_for_planes(planes, linked=False)
    out = st.apply_mtf(planes, params, linked=False)
    for i, plane in enumerate(planes):
        only = st.apply_mtf([plane], params["channels"][i], linked=True)[0]
        assert np.array_equal(out[i], only)


# ---- clipping invariant ----------------------------------------------------------------------------------------

_H, _W = 150, 200
_N = _H * _W


def _clip_case(k: int) -> tuple[list[np.ndarray], bool]:
    """Eight seeded cases: mono and colour, linked and unlinked, with a dark noise tail or a darker channel."""
    rng = np.random.default_rng(1000 + k)
    yy, xx = np.mgrid[0:_H, 0:_W].astype(np.float64)
    nebula = np.maximum(0.0, 0.55 - np.hypot((xx - 0.62 * _W) / _W, (yy - 0.55 * _H) / _H))

    def plane(offset: float, scale: float, noise: float, tail: float = 0.0) -> np.ndarray:
        v = offset + 0.02 * scale * nebula + noise * rng.standard_normal((_H, _W))
        if tail:
            hit = rng.random((_H, _W)) < tail
            v = np.where(hit, v - rng.exponential(0.01, (_H, _W)), v)
        v = np.maximum(v, rng.uniform(1e-4, 2e-3, (_H, _W)))  # positive, and no tie pile-up at a floor
        return v.astype(np.float32)

    if k == 0:  # mono nebula
        return [plane(0.004, 1.0, 0.0004)], False
    if k == 1:  # mono with a dark noise tail
        return [plane(0.01, 1.0, 0.002, tail=0.02)], False
    if k == 2:  # mono with a few cold pixels already at zero
        p = plane(0.01, 1.0, 0.002)
        p.reshape(-1)[rng.choice(_N, 5, replace=False)] = 0.0
        return [p], False
    if k == 3:  # colour, linked, similar channels
        return [plane(0.006, 1.0, 0.0004), plane(0.006, 1.0, 0.0004), plane(0.006, 1.0, 0.0004)], True
    if k == 4:  # colour, unlinked, one channel darker
        return [plane(0.002, 0.5, 0.0004), plane(0.006, 3.0, 0.0004), plane(0.006, 3.0, 0.0004)], False
    if k == 5:  # colour, unlinked, dark noise tail on one channel
        return [plane(0.01, 1.0, 0.002), plane(0.01, 1.0, 0.002), plane(0.01, 1.0, 0.002, tail=0.02)], False
    if k == 6:  # colour, unlinked, random offsets and scales
        offs = rng.uniform(0.0005, 0.01, 3)
        scs = rng.uniform(0.2, 2.0, 3)
        return [plane(offs[c], scs[c], 0.0003) for c in range(3)], False
    # colour, linked, one channel offset below the others
    return [plane(0.001, 1.0, 0.0004), plane(0.006, 1.0, 0.0004), plane(0.006, 1.0, 0.0004)], True


@pytest.mark.parametrize("k", range(8))
def test_stretch_does_not_clip_real_signal_to_zero(k: int) -> None:
    planes, linked = _clip_case(k)
    params = st.stretch_parameters_for_planes(planes, linked=linked)
    out = st.apply_mtf(planes, params, linked=linked)
    budget = math.floor(st.STRETCH_CLIP_BUDGET * _N)
    for i, (before, after) in enumerate(zip(planes, out)):
        zeros_before = int(np.count_nonzero(before <= 0))
        zeros_after = int(np.count_nonzero(after == 0))
        assert zeros_after - zeros_before <= budget, f"case {k} channel {i}: {zeros_after - zeros_before} new zeros"


# ---- Siril commands --------------------------------------------------------------------------------------------


def _fmt_expected(v: float) -> str:
    return format(float(v), ".17g")


def test_mtf_commands_linked_and_mono() -> None:
    p = {"c0": 0.25, "m": 0.5}
    assert st.mtf_commands(p, linked=True, n_channels=3) == [("mtf", "0.25", "0.5", "1")]
    assert st.mtf_commands(p, linked=False, n_channels=1) == [("mtf", "0.25", "0.5", "1")]
    q = {"c0": 0.01, "m": 0.11214953271028037}
    assert st.mtf_commands(q, linked=True, n_channels=3) == [
        ("mtf", _fmt_expected(0.01), _fmt_expected(0.11214953271028037), "1")
    ]


def test_mtf_commands_unlinked_one_per_channel() -> None:
    params = {
        "c0": 0.125,
        "m": 0.25,
        "channels": [
            {"c0": 0.125, "m": 0.25, "skyOut": 0.25},
            {"c0": 0.0625, "m": 0.5, "skyOut": 0.25},
            {"c0": 0.5, "m": 0.75, "skyOut": 0.25},
        ],
    }
    assert st.mtf_commands(params, linked=False, n_channels=3) == [
        ("mtf", "0.125", "0.25", "1", "R"),
        ("mtf", "0.0625", "0.5", "1", "G"),
        ("mtf", "0.5", "0.75", "1", "B"),
    ]


def test_mtf_commands_reproduce_apply_mtf_per_channel() -> None:
    planes = _colour_sky(5, (0.02, 0.05, 0.08), noise=0.002)
    params = st.stretch_parameters_for_planes(planes, linked=False)
    ref = st.apply_mtf(planes, params, linked=False)
    commands = st.mtf_commands(params, linked=False, n_channels=3)
    for (name, low, mid, high, chan), plane, expected in zip(commands, planes, ref):
        assert name == "mtf" and high == "1"
        x = plane.astype(np.float64)
        v = np.clip((x - float(low)) / (1.0 - float(low)), 0.0, 1.0)
        got = st.mtf_apply(float(mid), v)
        assert np.max(np.abs(got - expected.astype(np.float64))) <= 2.1e-7, chan


def test_fmt_round_trips_exactly() -> None:
    rng = np.random.default_rng(99)
    values = list(rng.uniform(0.0, 1.0, 200)) + [1 / 3, 1e-300, 0.1, 0.25, 1.0 - 2**-53]
    for v in values:
        assert float(st._fmt(v)) == float(v)


def test_unlinked_commands_need_channels() -> None:
    with pytest.raises(ValueError):
        st.mtf_commands({"c0": 0.1, "m": 0.2}, linked=False, n_channels=3)


# ---- timing (Docker only) --------------------------------------------------------------------------------------


@pytest.mark.perf
def test_parameters_for_a_large_colour_image_are_fast() -> None:
    rng = np.random.default_rng(2026)
    planes = [(0.02 + 0.01 * rng.random((3000, 4000), dtype=np.float32)).astype(np.float32) for _ in range(3)]
    t0 = time.perf_counter()
    st.stretch_parameters_for_planes(planes, linked=False)
    assert time.perf_counter() - t0 < 10.0
