"""Frame Selector: frames drawn for display (stretch, thumbnails, the reference stretch and the render cache).

The functions are checked against the closed forms of FrameSelector.js (mtf, the stretch parameters, the midpoint of
the transfer), against the rules of the render cache (a render is stored once, then hit, and a memo is read without
reading its frame), and against the two read paths: FITS through loomsiril.io_fits, and everything else through a
stub of sirilpy's ``load_image_from_file``. No Siril, node or display is needed.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril import frames_render as R
from loomsiril.frames_preview_cache import PreviewCache
from loomsiril.io_fits import read_fits, write_fits


@pytest.fixture(autouse=True)
def _reset_reads() -> None:
    R.IO["frame_reads"] = 0


def _cache(tmp_path: Path) -> PreviewCache:
    return PreviewCache(str(tmp_path / "cache"))


def _write_mono(path: Path, h: int, w: int, level: float, seed: int = 1) -> Path:
    rng = np.random.default_rng(seed)
    plane = (level + 0.01 * rng.standard_normal((h, w))).astype(np.float32)
    write_fits(path, [plane])
    return path


class _Siril:
    """A stub of sirilpy's SirilInterface: load_image_from_file returns what the test gives it."""

    def __init__(self, data: Any = None, error: Exception | None = None) -> None:
        self.data = data
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    def load_image_from_file(self, path: str, with_pixels: bool = False) -> Any:
        self.calls.append((path, with_pixels))
        if self.error is not None:
            raise self.error
        if self.data is None:
            return None

        class FFit:
            pass

        ff = FFit()
        ff.data = self.data  # type: ignore[attr-defined]
        return ff


# mtf: the closed form, and the clamps at 0 and 1 (FrameSelector.js 310-316)


def _mtf_closed(m: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return (m - 1) * x / ((2 * m - 1) * x - m)


@pytest.mark.parametrize("m", [0.25, 0.1, 0.5, 0.8])
def test_mtf_matches_closed_form_at_the_edges_and_midtone(m: float) -> None:
    assert R.mtf(m, 0.0) == 0.0
    assert R.mtf(m, -0.3) == 0.0
    assert R.mtf(m, 1.0) == 1.0
    assert R.mtf(m, 1.7) == 1.0
    assert R.mtf(m, m) == pytest.approx(0.5, abs=1e-12)


def test_mtf_matches_closed_form_on_a_grid() -> None:
    xs = np.linspace(0.0, 1.0, 201)
    for m in (0.25, 0.1, 0.6):
        expected = np.array([_mtf_closed(m, float(x)) for x in xs])
        assert np.allclose(R.mtf(m, xs), expected, rtol=1e-12, atol=1e-12)
        for x, e in zip(xs[::20], expected[::20]):
            assert R.mtf(m, float(x)) == pytest.approx(e, rel=1e-12, abs=1e-12)


def test_mtf_target_maps_the_median_of_an_unclipped_stretch_to_the_target() -> None:
    # With the median at 0.1 the transfer sends it to STRETCH.TARGET exactly (shadows at zero).
    m = R.mtf(R.STRETCH["TARGET"], 0.1)
    assert R.mtf(m, 0.1) == pytest.approx(R.STRETCH["TARGET"], abs=1e-12)


# stretch_params: median and MAD, the shadows floor, the midtone


def test_stretch_params_uses_median_and_scaled_mad() -> None:
    # 101 samples: median 0.1 exactly, absolute deviations 0 once and 0.01 a hundred times, so MAD = 0.01.
    x = np.concatenate([np.full(50, 0.09), [0.1], np.full(50, 0.11)])
    p = R.stretch_params(x)
    mad = 0.01 * 1.4826
    shadows = 0.1 - 2.8 * mad
    assert p["shadows"] == pytest.approx(shadows, abs=1e-12)
    assert p["midtone"] == pytest.approx(_mtf_closed(0.25, 0.1 - shadows), abs=1e-12)


def test_stretch_params_floors_shadows_at_zero() -> None:
    x = np.linspace(0.0, 0.2, 101)  # median 0.1, MAD 0.05: 0.1 - 2.8 * 1.4826 * 0.05 < 0
    p = R.stretch_params(x)
    assert p["shadows"] == 0.0
    assert p["midtone"] == pytest.approx(_mtf_closed(0.25, 0.1), abs=1e-12)


def test_stretch_params_reads_all_channels_together() -> None:
    mono = np.linspace(0.0, 0.2, 101)
    rgb = np.stack([mono, mono, mono])
    assert R.stretch_params(rgb) == R.stretch_params(mono)


# stretch_in_place: the median goes to the target when the shadows clip is zero; the band loop changes nothing


def test_stretch_in_place_maps_the_median_to_the_target_and_keeps_the_object() -> None:
    img = np.linspace(0.0, 0.2, 101).astype(np.float32).reshape(101, 1)
    before = img
    out = R.stretch_in_place(img)
    assert out is before
    assert float(np.median(img)) == pytest.approx(R.STRETCH["TARGET"], abs=1e-6)
    assert np.all(np.diff(img.ravel()) >= 0)


def test_stretch_in_place_uses_given_params_per_channel() -> None:
    plane = np.linspace(0.0, 1.0, 40, dtype=np.float32).reshape(4, 10)
    img = np.stack([plane, plane])
    expected = R.mtf(0.4, (plane - 0.1) / 0.9)
    R.stretch_in_place(img, {"shadows": 0.1, "midtone": 0.4})
    assert np.allclose(img[0], expected, atol=1e-6)
    assert np.allclose(img[1], expected, atol=1e-6)


# the stretch memo: read back only when it holds two finite numbers


def test_stretch_memo_round_trips_and_rejects_bad_content(tmp_path: Path) -> None:
    good = tmp_path / "stretch-a.json"
    R.write_stretch_memo(str(good), {"shadows": 0.25, "midtone": 0.125})
    assert R.read_stretch_memo(str(good)) == {"shadows": 0.25, "midtone": 0.125}
    bad = tmp_path / "stretch-b.json"
    bad.write_text('{"shadows": NaN, "midtone": 0.1}', encoding="utf-8")
    assert R.read_stretch_memo(str(bad)) is None
    text = tmp_path / "stretch-c.json"
    text.write_text('{"shadows": "0.1", "midtone": 0.1}', encoding="utf-8")
    assert R.read_stretch_memo(str(text)) is None
    assert R.read_stretch_memo(str(tmp_path / "missing.json")) is None
    assert R.read_stretch_memo(None) is None


# reference_stretch: the median background, read once, then from the memo


def test_reference_stretch_picks_the_median_background_and_reads_the_memo_second_time(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    frames = {
        "low": _write_mono(tmp_path / "low.fit", 40, 50, 0.05, seed=1),
        "mid": _write_mono(tmp_path / "mid.fit", 40, 50, 0.20, seed=2),
        "high": _write_mono(tmp_path / "high.fit", 40, 50, 0.45, seed=3),
    }
    rows = [
        {"path": str(frames["high"]), "metrics": {"background": 0.9}},
        {"path": str(frames["low"]), "metrics": {"background": 0.1}},
        {"path": str(frames["mid"]), "metrics": {"background": 0.5}},
        {"path": str(tmp_path / "no-metrics.fit"), "metrics": None},
    ]
    # Three rows have a background; the one at the lower median is the mid frame.
    first = R.reference_stretch(rows, cache)
    assert R.IO["frame_reads"] == 1
    planes, _ = read_fits(frames["mid"])
    assert first == R.stretch_params(planes[0])

    memo_files = list(Path(cache.preview_dir()).glob("stretch-*.json"))
    assert len(memo_files) == 1

    second = R.reference_stretch(rows, cache)
    assert R.IO["frame_reads"] == 1, "the second call must read its memo, not the frame"
    assert second == first


def test_reference_stretch_is_none_without_a_background_or_a_file(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert R.reference_stretch([{"path": "x.fit", "metrics": {"background": None}}], cache) is None
    assert R.reference_stretch([{"path": str(tmp_path / "gone.fit"), "metrics": {"background": 0.1}}], cache) is None


# render_frame: stored once, then the cache is hit


def test_render_frame_stores_then_hits_the_cache(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    frame = _write_mono(tmp_path / "f.fit", 30, 40, 0.1)
    first = R.render_frame(str(frame), "preview", None, cache)
    assert first is not None and first.dtype == np.uint8 and first.shape == (30, 40)
    assert R.IO["frame_reads"] == 1
    key = cache.preview_key(str(frame), "preview", R.render_params("preview"))
    assert key is not None and Path(cache.preview_path(key, R.PREVIEW_EXT)).exists()

    second = R.render_frame(str(frame), "preview", None, cache)
    assert R.IO["frame_reads"] == 1, "a cached render must not read the frame"
    assert second is not None and np.array_equal(first, second)


def test_render_frame_is_none_for_an_unreadable_frame(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert R.render_frame(str(tmp_path / "absent.fit"), "preview", None, cache) is None


def test_render_frame_stretch_changes_the_cache_key(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    frame = _write_mono(tmp_path / "f.fit", 20, 20, 0.1)
    plain = cache.preview_key(str(frame), "preview", R.render_params("preview"))
    shared = cache.preview_key(str(frame), "preview", R.render_params("preview", {"shadows": 0.01, "midtone": 0.3}))
    assert plain != shared


# thumbnails: fit the box, keep the aspect, and keep the channels


@pytest.mark.parametrize(("h", "w", "expected"), [(100, 200, (60, 120)), (200, 300, (80, 120)), (80, 120, (80, 120))])
def test_thumbnail_fits_the_box_and_keeps_aspect(tmp_path: Path, h: int, w: int, expected: tuple[int, int]) -> None:
    cache = _cache(tmp_path)
    frame = _write_mono(tmp_path / "t.fit", h, w, 0.2)
    thumb = R.thumbnail_of(str(frame), None, cache)
    assert thumb is not None
    assert thumb.shape == expected
    assert thumb.dtype == np.uint8


def test_thumbnail_of_a_colour_frame_is_rgb(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    planes = [np.full((100, 200), v, dtype=np.float32) for v in (0.1, 0.2, 0.3)]
    frame = tmp_path / "rgb.fit"
    write_fits(frame, planes)
    thumb = R.thumbnail_of(str(frame), None, cache)
    assert thumb is not None and thumb.shape == (60, 120, 3)


def test_thumbnail_of_a_missing_file_is_none(tmp_path: Path) -> None:
    assert R.thumbnail_of(str(tmp_path / "none.fit"), None, _cache(tmp_path)) is None


def test_resample_area_keeps_a_constant_and_halves_by_block_means() -> None:
    flat = np.full((7, 9), 0.4, dtype=np.float32)
    out = R.resample_area(flat, 0.5)
    assert out.shape == (4, 5)  # round(7 * 0.5) = 4 with the half rounded up; round(9 * 0.5) = 5
    assert np.allclose(out, 0.4, atol=1e-6)
    grid = np.arange(16, dtype=np.float32).reshape(4, 4)
    blocks = R.resample_area(grid, 0.5)
    assert np.allclose(blocks, [[2.5, 4.5], [10.5, 12.5]])


def test_area_weights_split_a_pixel_by_its_overlap() -> None:
    # 3 input samples into 2 outputs: output 0 covers [0, 1.5), output 1 covers [1.5, 3).
    W = R._area_weights(3, 2)
    assert np.allclose(W, [[1 / 1.5, 0.5 / 1.5, 0.0], [0.0, 0.5 / 1.5, 1 / 1.5]], atol=1e-7)
    assert np.allclose(W.sum(axis=1), 1.0)


# eight_bit: rounding half up, clipping, channel order


def test_eight_bit_rounds_half_up_and_clips() -> None:
    x = np.array([[-0.5, 0.0, 1.0 / 510.0, 0.5, 1.0, 2.0]], dtype=np.float32)
    assert R.eight_bit(x).tolist() == [[0, 0, 1, 128, 255, 255]]
    assert R.eight_bit(np.array([[np.nan]], dtype=np.float32)).tolist() == [[0]]


def test_eight_bit_channels_become_rgb_last() -> None:
    chw = np.zeros((3, 2, 2), dtype=np.float32)
    chw[0] = 1.0
    out = R.eight_bit(chw)
    assert out.shape == (2, 2, 3)
    assert np.all(out[..., 0] == 255) and np.all(out[..., 1:] == 0)


# the non-FITS path: sirilpy's load_image_from_file, flipped to top-down


def test_non_fits_path_calls_load_image_from_file_and_flips_rows() -> None:
    bottom_up = np.arange(4 * 5, dtype=np.uint16).reshape(4, 5) * 3000
    stub = _Siril(data=bottom_up)
    got = R.read_quietly("/frames/light.xisf", stub)
    assert stub.calls == [("/frames/light.xisf", True)]
    assert got is not None and got.dtype == np.float32 and got.shape == (4, 5)
    expected = (np.flipud(bottom_up).astype(np.float64) / 65535.0).astype(np.float32)
    assert np.allclose(got, expected, rtol=1e-6, atol=1e-7)
    assert got[0, 0] == pytest.approx(bottom_up[-1, 0] / 65535.0, rel=1e-6)


def test_non_fits_colour_path_flips_the_rows_of_each_plane() -> None:
    cube = np.stack([np.arange(6, dtype=np.uint16).reshape(2, 3) * 1000 + c for c in range(3)])
    got = R.read_quietly("light.fz", _Siril(data=cube))
    assert got is not None and got.shape == (3, 2, 3)
    assert np.allclose(got, np.flip(cube, axis=-2).astype(np.float64) / 65535.0, atol=1e-6)


def test_read_frame_counts_non_fits_reads_too() -> None:
    R.read_frame("light.xisf", _Siril(data=np.zeros((2, 2), dtype=np.uint16)))
    assert R.IO["frame_reads"] == 1


def test_non_fits_without_siril_is_none() -> None:
    assert R.read_quietly("light.xisf", None) is None


def test_non_fits_failures_are_none_and_never_raise() -> None:
    assert R.read_quietly("light.xisf", _Siril(data=None)) is None
    assert R.read_quietly("light.xisf", _Siril(error=RuntimeError("no such file"))) is None
    assert R.read_quietly("light.xisf", _Siril(data=np.zeros(3, dtype=np.uint16))) is None


# the FITS path: loomsiril.io_fits, top-down


def test_fits_read_matches_io_fits_top_down(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    planes = [rng.random((6, 8), dtype=np.float32) for _ in range(2)]
    path = tmp_path / "rgbish.fits"
    write_fits(path, planes)
    got = R.read_quietly(str(path))
    ref, _ = read_fits(path)
    assert got is not None and got.shape == (2, 6, 8)
    assert np.array_equal(got, np.stack(ref))


def test_fits_mono_read_is_two_dimensional(tmp_path: Path) -> None:
    path = _write_mono(tmp_path / "m.fts", 5, 7, 0.3)
    got = R.read_quietly(str(path), _Siril(error=AssertionError("Siril must not be asked for a FITS")))
    assert got is not None and got.shape == (5, 7)


def test_unreadable_fits_is_none(tmp_path: Path) -> None:
    garbage = tmp_path / "bad.fit"
    garbage.write_bytes(b"not a fits file at all")
    assert R.read_quietly(str(garbage)) is None
    assert R.read_quietly(str(tmp_path / "absent.fits")) is None


# the cached file, and the parameters that key it


def test_corrupt_cached_render_is_removed_and_not_returned(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    key = "deadbeef"
    path = Path(cache.preview_path(key, R.PREVIEW_EXT))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not an array")
    assert R.cached_render(key, cache) is None
    assert not path.exists()


def test_render_params_keeps_js_order_and_fixed_seven_digits() -> None:
    p = R.render_params("thumb", {"shadows": 0.00390625, "midtone": 0.5})
    assert list(p) == ["shadows", "target", "clip", "mid", "w", "h"]
    assert p["clip"] == "0.0039063"  # toFixed(7) rounds the exact binary tie away from zero
    assert p["mid"] == "0.5000000"
    assert p["w"] == 120 and p["h"] == 80
    assert list(R.render_params("preview")) == ["shadows", "target"]


def test_thumb_box_and_stretch_constants_are_the_js_values() -> None:
    assert R.STRETCH == {"SHADOWS": 2.8, "TARGET": 0.25}
    assert R.THUMB == {"W": 120, "H": 80}
    assert math.isclose(R.stretch_params(np.linspace(0, 0.2, 101))["midtone"], _mtf_closed(0.25, 0.1))
