"""Temporary stretch: round trips, linking, the expression strings and PRISM target selection, against the oracle."""

from __future__ import annotations

import math

import numpy as np
import pytest

from loomsiril.loom.stretch import mtf_apply, mtf_midtone_for
from loomsiril.loom.tools import tempstretch as ts
from loomsiril.loom.tools.tempstretch import (
    StretchState,
    prism_background,
    prism_mtf_target,
    prism_raised_target,
    prism_samples,
    prism_stretch_stats,
    stretch_forward,
    stretch_inverse,
    syqon_denormalize_expression,
    syqon_format_stretch,
    syqon_inverse_mtf_expression,
    syqon_mtf_expression,
    syqon_normalize_expression,
)


def _synth(w: int, h: int, nch: int, base: float, span: float, mod: int) -> list[np.ndarray]:
    """The integer-driven synthetic planes the oracle recorder uses (same operations, same order)."""
    c = np.arange(nch)[:, None, None]
    y = np.arange(h)[None, :, None]
    x = np.arange(w)[None, None, :]
    k = (x * 37 + y * 101 + c * 53) % mod
    return [(k[i] / mod) * span + base for i in range(nch)]


def _mono_stretch_planes(seed: int, n: int = 1) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [rng.random((31, 29)).astype(np.float32) * 0.8 + 0.1 for _ in range(n)]


# ---------------------------------------------------------------------------
# Constants and number text
# ---------------------------------------------------------------------------


def test_constants_match_loom(oracle) -> None:
    c = oracle("loom_tempstretch")["cases"]["constants"]
    assert ts.SYQON_MTF_TARGET == c["syqon_mtf_target"] == 0.12
    assert ts.SYQON_TILE_SIZE == c["syqon_tile_size"] == 512
    assert ts.SYQON_OVERLAP == c["syqon_overlap"] == 128
    assert ts.SYQON_PAD == c["syqon_pad"] == 512
    assert ts.SYQON_TIMEOUT_MS == c["syqon_timeout_ms"] == 1_200_000
    assert ts.PRISM_DEFAULT_TARGET == c["prism_default_target"] == 0.15
    assert ts.PRISM_CORPUS_STD_MIN == c["prism_corpus_std_min"] == 0.0474
    assert ts.PRISM_CORPUS_STD_MAX == c["prism_corpus_std_max"] == 0.1288
    assert ts.PRISM_HIGHLIGHT_CAP == c["prism_highlight_cap"] == 0.98
    assert ts.PRISM_TARGET_MAX == c["prism_target_max"] == 0.70


def _pixel(recorded: list[str]) -> list[str]:
    """The oracle stores PixelMath's pixel symbol as {px}."""
    return [s.replace("{px}", "$" + "T") for s in recorded]


def test_expression_strings_match_loom(oracle) -> None:
    c = oracle("loom_tempstretch")["cases"]
    mins = c["normalise_mins"]
    assert [syqon_normalize_expression(m) for m in mins] == _pixel(c["normalise_expression"])
    assert [syqon_denormalize_expression(m) for m in mins] == _pixel(c["denormalise_expression"])
    pairs = [(0.1234567891234567, 0.12), (0.25, 0.5), (0.00762939453125, 0.12)]
    assert [syqon_mtf_expression(om, tm) for om, tm in pairs] == _pixel(c["mtf_expression"])
    assert [syqon_inverse_mtf_expression(om, tm) for om, tm in pairs] == _pixel(c["inverse_mtf_expression"])
    assert syqon_format_stretch([0.0123456789, 0.5, 1 / 3, 2 / 3 * 1e-3, 0.00000762939453125]) == c["format_stretch"]


def test_sixteen_decimal_text_rounds_exact_ties_away_from_zero() -> None:
    # 2**-17 has 17 decimals ending in 5: Python's own rounding would give ...312, Loom's gives ...313.
    assert syqon_format_stretch([2**-17]) == ["0.0000076293945313"]
    assert syqon_format_stretch([-(2**-17)]) == ["-0.0000076293945313"]
    assert syqon_format_stretch([-0.0]) == ["0.0000000000000000"]


def test_non_finite_numbers_cannot_be_written_as_expressions() -> None:
    with pytest.raises(ValueError):
        syqon_normalize_expression(float("nan"))


def test_mtf_helpers_are_the_stretch_module_functions(oracle) -> None:
    rows = oracle("loom_tempstretch")["cases"]["mtf_midtone_rows"]
    for r in rows:
        assert mtf_midtone_for(r["x0"], r["target"]) == r["midtone"]
    applied = oracle("loom_tempstretch")["cases"]["mtf_applied"]
    for r in applied:
        assert mtf_apply(r["m"], r["x"]) == r["y"]
    assert ts.mtf_apply is mtf_apply and ts.mtf_midtone_for is mtf_midtone_for


# ---------------------------------------------------------------------------
# Temporary stretch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("linked", [False, True])
def test_forward_then_inverse_returns_the_input(linked: bool) -> None:
    planes = _mono_stretch_planes(1, n=3)
    # Every value is at or above its channel minimum, so the forward step never clips.
    stretched, state = stretch_forward(planes, 0.12, linked)
    back = stretch_inverse(stretched, state)
    for a, b in zip(planes, back):
        np.testing.assert_allclose(b, a.astype(np.float64), rtol=0, atol=1e-7)


def test_forward_maps_the_normalised_median_to_the_target() -> None:
    planes = _mono_stretch_planes(2)  # 31 x 29 = 899 values: odd, so the median is one value
    stretched, _state = stretch_forward(planes, 0.12, linked=False)
    assert np.median(stretched[0]) == pytest.approx(0.12, abs=1e-9)


def test_linked_stretch_shares_one_minimum_and_the_mean_median() -> None:
    planes = _mono_stretch_planes(3, n=3)
    planes[1] = planes[1] + np.float32(0.05)
    stretched, state = stretch_forward(planes, 0.12, linked=True)
    assert state.linked
    assert len(set(state.minima)) == 1 and state.minima[0] == min(float(np.min(p)) for p in planes)
    assert len(set(state.medians)) == 1
    norm = [(np.asarray(p, dtype=np.float64) - state.minima[0]) / (1 - state.minima[0]) for p in planes]
    assert state.medians[0] == pytest.approx(sum(float(np.median(n)) for n in norm) / 3.0, rel=0, abs=1e-15)


def test_unlinked_stretch_keeps_one_minimum_per_channel() -> None:
    planes = _mono_stretch_planes(4, n=3)
    planes[1] = planes[1] + np.float32(0.05)
    _stretched, state = stretch_forward(planes, 0.12, linked=False)
    assert not state.linked
    assert state.minima[0] != state.minima[1]
    assert len(state.medians) == 3 and len(set(state.medians)) > 1


def test_forward_output_is_clipped_to_the_unit_interval() -> None:
    planes = _mono_stretch_planes(5)
    planes[0][0, 0] = np.float32(0.0)  # the minimum, normalised to 0
    planes[0][1, 1] = np.float32(1.0)
    stretched, _ = stretch_forward(planes, 0.12, linked=False)
    assert stretched[0].min() >= 0.0 and stretched[0].max() <= 1.0


def test_reverse_clamps_the_transfer_and_the_result_as_loom_does() -> None:
    """Loom truncates after the inverse transfer and after the de-normalisation (lib/StepsSyqon.js:513-523)."""
    state = StretchState(target=0.12, linked=False, minima=(0.0,), medians=(0.5,))
    # The transfer of 1.5 is 1.0476 and is clamped to 1 before anything else.
    assert stretch_inverse([np.array([[1.5]])], state)[0][0, 0] == 1.0
    # A negative original minimum: the clamped transfer of 0 gives -0.2 and comes back as 0.
    neg = StretchState(target=0.12, linked=False, minima=(-0.2,), medians=(0.5,))
    assert stretch_inverse([np.array([[0.0, 1.0]])], neg)[0].tolist() == [[0.0, 1.0]]
    mid = stretch_inverse([np.array([[0.5]])], neg)[0][0, 0]
    expected = np.clip(np.clip(0.5 * 0.5 * (0.12 - 1) / (0.5 * 0.12 - 0.5 * 0.5 + 0.12 * 0.5 - 0.12), 0, 1) * 1.2 - 0.2, 0, 1)
    assert mid == pytest.approx(float(expected), rel=1e-12)


def test_normalised_values_are_truncated_before_the_median() -> None:
    """More than half the pixels above the normalised range: the truncated median is 1, which Loom refuses."""
    plane = np.where(np.arange(100) < 60, 1.5, 0.3).astype(np.float32).reshape(10, 10)
    with pytest.raises(ValueError, match="outside"):
        stretch_forward([plane], 0.12, linked=False)

    # A minority above the range is clipped in the output and does not move the median.
    few = np.where(np.arange(100) < 30, 1.5, np.linspace(0.3, 0.9, 100)).astype(np.float32).reshape(10, 10)
    out, _ = stretch_forward([few], 0.12, linked=False)
    assert out[0].max() <= 1.0 and out[0].min() >= 0.0


def test_normalised_median_outside_the_unit_interval_is_refused() -> None:
    flat = [np.full((5, 5), 0.4, dtype=np.float32)]
    with pytest.raises(ValueError, match="outside"):
        stretch_forward(flat, 0.12, linked=False)
    with pytest.raises(ValueError, match="outside"):
        stretch_forward([np.full((5, 5), 0.4, dtype=np.float32) for _ in range(3)], 0.12, linked=True)


def test_target_outside_the_unit_interval_is_refused() -> None:
    with pytest.raises(ValueError):
        stretch_forward(_mono_stretch_planes(6), 1.0, linked=False)


def test_forward_is_the_midtone_transfer_for_the_median_pair() -> None:
    """Forward mapping om -> tm is the MTF with midtone mtf_midtone_for(om, tm), as Loom's expression writes it."""
    om, tm = 0.2, 0.12
    m = mtf_midtone_for(om, tm)
    x = np.linspace(0.0, 1.0, 101)
    state = StretchState(target=tm, linked=False, minima=(0.0,), medians=(om,))
    back = stretch_inverse([mtf_apply(m, x)], state)[0]
    np.testing.assert_allclose(back, x, rtol=0, atol=1e-12)


# ---------------------------------------------------------------------------
# PRISM target selection, against the oracle
# ---------------------------------------------------------------------------


def _images() -> dict:
    mono = _synth(300, 200, 1, 0.02, 0.9, 211)
    rgb = _synth(151, 97, 3, 0.03, 0.4, 199)
    flat = _synth(200, 150, 1, 0.5, 0.001, 211)
    flat[0][0, 0] = 0.0  # one dark pixel sets the black point; the rest sit at the middle
    zero = _synth(40, 30, 1, 0, 0, 7)
    return {"mono": mono, "rgb": rgb, "flat": flat, "zero": zero}


def test_prism_background_and_samples_match_the_oracle(oracle) -> None:
    c = oracle("loom_tempstretch")["cases"]
    imgs = _images()

    bg_mono = prism_background(imgs["mono"])
    assert bg_mono.all_min == c["prism_background_mono"]["allMin"]
    assert bg_mono.x0 == pytest.approx(c["prism_background_mono"]["x0"], rel=1e-14, abs=0)

    bg_rgb = prism_background(imgs["rgb"])
    assert bg_rgb.all_min == c["prism_background_rgb"]["allMin"]
    assert bg_rgb.x0 == pytest.approx(c["prism_background_rgb"]["x0"], rel=1e-14, abs=0)

    px, p999 = prism_samples(imgs["mono"], bg_mono.all_min, 7)
    assert px.shape == (c["prism_samples_mono_count"], 3)
    assert p999 == pytest.approx(c["prism_p999_mono"], rel=1e-14, abs=0)

    _px_rgb, p999_rgb = prism_samples(imgs["rgb"], bg_rgb.all_min, 7)
    assert p999_rgb == pytest.approx(c["prism_p999_rgb"], rel=1e-14, abs=0)


def test_prism_stretch_stats_match_the_oracle(oracle) -> None:
    c = oracle("loom_tempstretch")["cases"]
    imgs = _images()
    bg = prism_background(imgs["mono"])
    px, _ = prism_samples(imgs["mono"], bg.all_min, 7)
    for t, want in zip((0.15, 0.25, 0.4), c["prism_stretch_stats_mono"]):
        got = prism_stretch_stats(px, bg.x0, t)
        assert got.mean == pytest.approx(want["mean"], rel=1e-12)
        assert got.std == pytest.approx(want["std"], rel=1e-12)


def test_prism_raised_target_matches_the_oracle(oracle) -> None:
    c = oracle("loom_tempstretch")["cases"]
    imgs = _images()

    bg = prism_background(imgs["mono"])
    px, p999 = prism_samples(imgs["mono"], bg.all_min, 7)
    for key, p, std in (
        ("prism_raised_target_low", p999, 0.01),
        ("prism_raised_target_capped", 0.99, 0.01),
        ("prism_raised_target_high", p999, 0.3),
    ):
        got = prism_raised_target(px, bg.x0, p, std)
        want = c[key]
        assert got.target == want["target"], key
        assert got.std == pytest.approx(want["std"], rel=1e-12), key
        assert got.hi == pytest.approx(want["hi"], rel=1e-12), key

    flat_bg = prism_background(imgs["flat"])
    flat_px, flat_p999 = prism_samples(imgs["flat"], flat_bg.all_min, 7)
    std_default = prism_stretch_stats(flat_px, flat_bg.x0, ts.PRISM_DEFAULT_TARGET).std
    got = prism_raised_target(flat_px, flat_bg.x0, flat_p999, std_default)
    want = c["prism_raised_target_flat"]
    assert got.target == want["target"]
    assert got.std == pytest.approx(want["std"], rel=1e-12)


def test_prism_mtf_target_matches_the_oracle(oracle) -> None:
    c = oracle("loom_tempstretch")["cases"]
    imgs = _images()
    assert prism_mtf_target(imgs["mono"], 7) == c["prism_mtf_target_mono"]
    assert prism_mtf_target(imgs["rgb"], 7) == c["prism_mtf_target_rgb"]
    assert prism_mtf_target(imgs["flat"], 7) == c["prism_mtf_target_flat"]
    assert prism_mtf_target(imgs["zero"], 7) == c["prism_mtf_target_zero"]
    assert prism_mtf_target(imgs["rgb"]) == c["prism_mtf_target_default_stride"]


def test_prism_mtf_target_defaults_for_an_uncharacterisable_image() -> None:
    zero = _synth(40, 30, 1, 0, 0, 7)
    assert prism_mtf_target(zero) == ts.PRISM_DEFAULT_TARGET


def test_mono_images_use_one_channel_for_all_three_samples() -> None:
    mono = _synth(12, 9, 1, 0.1, 0.5, 13)
    px, _ = prism_samples(mono, 0.1, 1)
    assert np.all(px[:, 0] == px[:, 1]) and np.all(px[:, 1] == px[:, 2])
    assert all(math.isfinite(v) for v in px.ravel())


def test_reverse_clips_the_transfer_before_the_normalisation_is_undone() -> None:
    """With a positive minimum, a tool value below 0 must be clipped before the de-normalisation (Loom's two steps)."""
    state = StretchState(target=0.12, linked=False, minima=(0.5,), medians=(0.5,))
    # The transfer of -0.01 is -0.0783; clipped to 0 it de-normalises to 0.5, unclipped it would be 0.4608.
    raw = 0.5 * (-0.01) * (0.12 - 1) / (0.5 * 0.12 - 0.5 * (-0.01) + 0.12 * (-0.01) - 0.12)
    assert raw < 0.0
    out = stretch_inverse([np.array([[-0.01]])], state)[0][0, 0]
    assert out == pytest.approx(0.5, rel=0, abs=1e-15)
    assert out != pytest.approx(raw * 0.5 + 0.5, abs=1e-6)
    # A tool value above 1 clamps to 1 as well.
    high = stretch_inverse([np.array([[1.5]])], state)[0][0, 0]
    assert high == pytest.approx(1.0, rel=0, abs=1e-15)


def test_even_count_median_straddling_one_uses_the_clipped_pair() -> None:
    """Ten pixels: the middle pair is (0.9, 5). Clipped it is (0.9, 1) -> median 0.95, which is valid."""
    plane = np.array([0.0, 0.1, 0.2, 0.3, 0.9, 5.0, 5.0, 5.0, 5.0, 5.0]).reshape(2, 5)
    out, state = stretch_forward([plane], 0.12, linked=False)
    assert state.medians[0] == pytest.approx(0.95, rel=0, abs=1e-15)
    assert out[0].min() >= 0.0 and out[0].max() <= 1.0


def test_majority_above_one_on_an_even_count_is_refused() -> None:
    """Six of ten pixels above the range: the clipped median is 1, which is refused."""
    plane = np.array([0.0, 0.1, 0.2, 0.3, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0]).reshape(2, 5)
    with pytest.raises(ValueError, match="outside"):
        stretch_forward([plane], 0.12, linked=False)
