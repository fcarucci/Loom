"""Resampling kernels, shrink, mips, box blur and area fit (E4: loomsiril/resample.py).

Replays tests/oracle/render_axes.json (recorded from Loom's Render.js by
tools/oracle/render_axes.js) and checks the behaviour the frame renderer relies on.
"""

from __future__ import annotations

import numpy as np
import pytest

from loomsiril.resample import (
    AxisView,
    AxisWeights,
    axis_weights,
    box_blur,
    box_radius,
    cubic_weights,
    fit_plane,
    halve,
    kernel_taps,
    area_mean,
    mip_level,
    mip_of,
    normalise_weights,
    patch_sample,
    resample,
    resample_rows,
    shrink_axis,
    shrink_patch,
    tent_taps,
)

WEIGHT_TOL = 1e-12
PIXEL_TOL = 1e-6


def _axis(args: list) -> AxisWeights:
    """Render.axisWeights' recorded JS arguments (outN, cropStart, cropLen, tp, K, srcN, kernel), retargeted 1:1 to the
    AxisView form: the same numbers in the same places, no other change."""
    out_n, crop_start, crop_len, tp, K, src_n, kernel = args
    return axis_weights(out_n, AxisView(crop_start, crop_len, tp, K, src_n), kernel)


@pytest.fixture(scope="module")
def ra(oracle):
    return oracle("render_axes")["cases"]


def _plane(vals, w, h):
    return np.asarray(vals, dtype=np.float32).reshape(h, w)


# --- oracle replay -----------------------------------------------------------

@pytest.mark.parametrize("name", ["identity", "zoomBicubic", "zoomBilinear", "minifyTent",
                                  "minifyOffCentre", "edgeClamp", "wholeRounding"])
def test_axis_weights_match_js(ra, name):
    case = ra["axisWeights"][name]
    ax = _axis(case["args"])
    exp = case["out"]
    assert ax.out_n == exp["outN"]
    assert ax.start.dtype == np.int32 and ax.start.shape == (exp["outN"] + 1,)
    np.testing.assert_array_equal(ax.start, np.asarray(exp["start"]))
    np.testing.assert_array_equal(ax.idx, np.asarray(exp["idx"]))
    np.testing.assert_allclose(ax.w, np.asarray(exp["w"]), rtol=0, atol=WEIGHT_TOL)


def test_kernel_taps_match_js(ra):
    for case in ra["kernelTaps"]:
        t = kernel_taps(*case["args"])
        assert list(t.i) == case["out"]["i"]
        np.testing.assert_allclose(t.w, case["out"]["w"], rtol=0, atol=WEIGHT_TOL)


def test_tent_taps_match_js(ra):
    for case in ra["tentTaps"]:
        t = tent_taps(*case["args"])
        assert list(t.i) == case["out"]["i"]
        np.testing.assert_allclose(t.w, case["out"]["w"], rtol=0, atol=WEIGHT_TOL)


def test_cubic_weights_match_js(ra):
    for case in ra["cubicWeights"]:
        np.testing.assert_allclose(cubic_weights(case["args"][0]), case["out"], rtol=0, atol=WEIGHT_TOL)


def test_resample_matches_js(ra):
    block = ra["resample"]
    inp = block["input"]
    buf = _plane(inp["buf"], inp["w"], inp["h"])
    for case in block["cases"]:
        ax = _axis(case["ax"])
        ay = _axis(case["ay"])
        got = resample(buf, inp["w"], ax, ay)
        assert got.dtype == np.float32
        np.testing.assert_allclose(got.reshape(-1), case["out"], rtol=0, atol=PIXEL_TOL)


def test_resample_rows_matches_js(ra):
    block = ra["resampleRows"]
    inp = block["input"]
    buf = _plane(inp["buf"], inp["w"], inp["h"])
    ax = _axis(block["ax"])
    got = resample_rows(buf, inp["w"], ax, block["rmin"], block["rows"])
    np.testing.assert_allclose(got.reshape(-1), block["out"], rtol=0, atol=PIXEL_TOL)


def test_shrink_axis_matches_js(ra):
    for case in ra["shrinkAxis"]:
        s = shrink_axis(*case["args"])
        exp = case["out"]
        assert s.j0 == exp["j0"] and s.m == exp["m"]
        np.testing.assert_array_equal(s.start, np.asarray(exp["start"]))
        np.testing.assert_array_equal(s.idx, np.asarray(exp["idx"]))
        np.testing.assert_allclose(s.w, np.asarray(exp["w"]), rtol=0, atol=WEIGHT_TOL)


def test_normalise_weights_matches_js(ra):
    case = ra["normaliseWeights"][0]
    w = np.asarray(case["input"], dtype=np.float64)
    normalise_weights(w, case["from"], case["to"])
    np.testing.assert_allclose(w, case["out"], rtol=0, atol=WEIGHT_TOL)


def test_shrink_patch_matches_js(ra):
    block = ra["shrinkPatch"]
    img = _plane(block["image"]["buf"], block["image"]["w"], block["image"]["h"])
    for case in block["cases"]:
        patch = _plane(case["patch"], case["w"], case["h"])
        r = shrink_patch(patch, case["x0"], case["y0"], case["w"], case["h"], case["d"], case["imgW"], case["imgH"])
        exp = case["out"]
        assert (r["x0"], r["y0"], r["w"], r["h"]) == (exp["x0"], exp["y0"], exp["w"], exp["h"])
        np.testing.assert_allclose(r["buf"].reshape(-1), exp["buf"], rtol=0, atol=PIXEL_TOL)
    assert img.shape == (block["image"]["h"], block["image"]["w"])


def test_halve_matches_js(ra):
    inp, exp = ra["halve"]["input"], ra["halve"]["out"]
    got = halve(_plane(inp["d"], inp["w"], inp["h"]))
    assert got.shape == (exp["h"], exp["w"])
    np.testing.assert_allclose(got.reshape(-1), exp["d"], rtol=0, atol=PIXEL_TOL)


def test_mip_of_matches_js(ra):
    inp = ra["halve"]["input"]
    base = _plane(inp["d"], inp["w"], inp["h"])
    for case in ra["mipOf"]:
        _, _, L = case["args"]
        got = mip_of(base, L, [])
        exp = case["out"]
        assert got.shape == (exp["h"], exp["w"])
        np.testing.assert_allclose(got.reshape(-1), exp["d"], rtol=0, atol=PIXEL_TOL)


def test_patch_sample_matches_js(ra):
    block = ra["patchSample"]
    inp = block["input"]
    patch = _plane(inp["patch"], inp["rw"], inp["rh"])
    for case in block["points"]:
        x, y = case["args"]
        assert patch_sample(patch, inp["rw"], inp["rh"], x, y) == pytest.approx(case["out"], abs=PIXEL_TOL)


def test_box_blur_matches_js(ra):
    block = ra["boxBlur"]
    inp = block["input"]
    for case in block["cases"]:
        a = _plane(inp["a"], inp["w"], inp["h"])
        out = box_blur(a, inp["w"], inp["h"], case["r"])
        np.testing.assert_allclose(out.reshape(-1), case["out"], rtol=0, atol=PIXEL_TOL)


def test_fit_plane_matches_js(ra):
    for case in ra["fitPlane"]:
        src = _plane(case["src"]["a"], case["src"]["w"], case["src"]["h"])
        got = fit_plane(src, case["src"]["w"], case["src"]["h"], case["w"], case["h"])
        assert got.dtype == np.float32 and got.shape == (case["h"], case["w"])
        np.testing.assert_allclose(got.reshape(-1), case["out"], rtol=0, atol=PIXEL_TOL)


def test_area_mean_matches_js(ra):
    big = ra["fitPlane"][1]["src"]
    src = _plane(big["a"], big["w"], big["h"])
    for case in ra["areaMean"]:
        x0, y0, sx, sy = case["args"]
        assert area_mean(src, big["w"], big["h"], x0, y0, sx, sy) == pytest.approx(case["out"], abs=PIXEL_TOL)


def test_box_radius_and_mip_level_match_js(ra):
    for case in ra["boxRadius"]:
        assert box_radius(case["args"][0]) == case["out"]
    for case in ra["mipLevel"]:
        assert mip_level(*case["args"]) == case["out"]


# --- behaviour ---------------------------------------------------------------

def test_one_to_one_weights_are_the_identity():
    rng = np.random.default_rng(4)
    buf = rng.random((9, 12), dtype=np.float32)
    ax = axis_weights(12, AxisView(0, 12, 5.5, 1.0, 12), "bicubic")
    ay = axis_weights(9, AxisView(0, 9, 4.0, 1.0, 9), "bicubic")
    np.testing.assert_array_equal(resample(buf, 12, ax, ay), buf)


def test_indices_lie_inside_the_source(ra):
    for case in ra["axisWeights"].values():
        ax = _axis(case["args"])
        assert ax.idx.min() >= 0 and ax.idx.max() < case["args"][5]
    assert axis_weights(16, AxisView(0, 16, 5.0, 1.0, 16), "bicubic").idx.max() < 16


def _gaussian_sprite(n: int = 48, sigma: float = 5.0) -> np.ndarray:
    y, x = np.mgrid[0:n, 0:n].astype(np.float64)
    c = n / 2 - 0.5
    return np.exp(-((x - c) ** 2 + (y - c) ** 2) / (2 * sigma * sigma)).astype(np.float32)


@pytest.mark.parametrize("phase", [0.0, 0.33, 0.67])
def test_minified_sprite_light_is_conserved(phase):
    # 48 px source shown at 1/3 scale (step 3, tent path); the sprite keeps its light at every phase
    src = _gaussian_sprite()
    out_n = 16
    ax = axis_weights(out_n, AxisView(phase * 3.0, 48.0, 24.0, 1.0, 48), "bicubic")
    ay = axis_weights(out_n, AxisView(phase * 3.0, 48.0, 24.0, 1.0, 48), "bicubic")
    out = resample(src, 48, ax, ay).astype(np.float64)
    step = 3.0
    assert out.sum() * step * step == pytest.approx(src.astype(np.float64).sum(), rel=0.05)


def test_shrink_weights_sum_to_one_over_the_whole_image():
    for n, d in [(11, 0.5), (9, 0.6), (13, 1 / 3)]:
        s = shrink_axis(0, n, d, n)
        for j in range(s.m):
            assert s.w[s.start[j]:s.start[j + 1]].sum() == pytest.approx(1.0, abs=1e-12)


def test_shrink_patch_inside_image_keeps_mean_level():
    buf = np.full((4, 6), 0.25, dtype=np.float32)
    r = shrink_patch(buf, 2, 2, 6, 4, 0.5, 20, 20)   # even-aligned: no new pixel is cut by the patch edge
    assert r["buf"].shape == (r["h"], r["w"])
    np.testing.assert_allclose(r["buf"], 0.25, atol=1e-6)


def test_halve_and_mip_sizes():
    plane = np.arange(15, dtype=np.float32).reshape(3, 5)
    assert halve(plane).shape == (2, 3)
    assert halve(halve(plane)).shape == (1, 2)
    cache: list = []
    assert mip_of(plane, 0, cache).shape == (3, 5)
    assert mip_of(plane, 2, cache).shape == (1, 2)
    assert len(cache) == 3


def test_patch_sample_is_zero_outside():
    patch = np.ones((3, 4), dtype=np.float32)
    assert patch_sample(patch, 4, 3, -3.0, 1.0) == 0.0
    assert patch_sample(patch, 4, 3, 9.0, 1.0) == 0.0
    assert patch_sample(patch, 4, 3, 1.5, -4.0) == 0.0
    # half outside the left edge: half the light
    assert patch_sample(patch, 4, 3, -0.5, 1.0) == pytest.approx(0.5)
    # inside: exactly the pixel
    assert patch_sample(patch, 4, 3, 2.0, 1.0) == pytest.approx(1.0)


def test_patch_sample_takes_arrays():
    patch = np.arange(12, dtype=np.float32).reshape(3, 4)
    xs = np.array([0.0, 1.0, 2.5])
    ys = np.array([0.0, 1.0, 1.0])
    got = patch_sample(patch, 4, 3, xs, ys)
    assert np.shape(got) == (3,)
    assert got[0] == pytest.approx(0.0) and got[1] == pytest.approx(5.0)


def _clamp_mean_pass(line: np.ndarray, r: int) -> np.ndarray:
    n = len(line)
    out = np.empty(n)
    for i in range(n):
        idx = [min(max(k, 0), n - 1) for k in range(i - r, i + r + 1)]
        out[i] = line[idx].mean()
    return out


def test_box_blur_holds_the_edge():
    # one row: the column passes see length-1 lines (no change); the row passes are a clamped box mean
    row = np.array([[0.9, 0.1, 0.4, 0.0, 0.7, 0.2, 0.5]], dtype=np.float32)
    got = box_blur(row.copy(), 7, 1, 1)
    ref = row[0].astype(np.float64)
    for _ in range(3):
        ref = _clamp_mean_pass(ref, 1)
    np.testing.assert_allclose(got[0], ref, atol=PIXEL_TOL)


def test_box_blur_of_a_flat_plane_is_flat_and_r0_is_a_no_op():
    a = np.full((5, 7), 0.4, dtype=np.float32)
    np.testing.assert_allclose(box_blur(a.copy(), 7, 5, 2), 0.4, atol=1e-6)
    b = np.arange(35, dtype=np.float32).reshape(5, 7)
    np.testing.assert_array_equal(box_blur(b.copy(), 7, 5, 0), b)
