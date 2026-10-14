"""Exchange files: FITS and TIFF round trips, row order, sanitising, the mono channel rule and the declared output."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.tools.exchange import (
    BOTTOM_UP,
    ROWORDER,
    TOP_DOWN,
    declared_output,
    read_exchange_fits,
    read_exchange_tiff,
    sanitize_basic,
    sanitize_studio,
    write_exchange_fits,
    write_exchange_tiff,
)

FITS_BLOCK = 2880


def _planes(seed: int, n: int = 3, h: int = 7, w: int = 5) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [rng.random((h, w), dtype=np.float32) for _ in range(n)]


def _data_offset(raw: bytes) -> int:
    """Byte offset of the first data block: the header ends with the END card, padded to 2880 bytes."""
    end = raw.find(b"END" + b" " * 77)
    assert end >= 0 and end % 80 == 0
    return ((end + 80 + FITS_BLOCK - 1) // FITS_BLOCK) * FITS_BLOCK


def test_fits_round_trip_keeps_float32_values_and_bottom_up_rows(tmp_path: Path) -> None:
    planes = _planes(1)
    path = tmp_path / "in.fits"
    scale = write_exchange_fits(path, planes, header={"OBJECT": "synthetic"})
    assert scale == 1.0

    back, header = read_exchange_fits(path, 3)
    assert header[ROWORDER] == BOTTOM_UP and header["OBJECT"] == "synthetic"
    assert len(back) == 3
    for a, b in zip(planes, back):
        assert b.dtype == np.float32
        np.testing.assert_array_equal(a, b)

    # On disk the first data row is the bottom row of the plane.
    raw = path.read_bytes()
    off = _data_offset(raw)
    h, w = planes[0].shape
    first_row = np.frombuffer(raw, dtype=">f4", count=w, offset=off)
    np.testing.assert_array_equal(first_row, planes[0][-1])


def test_top_down_result_is_flipped_back(tmp_path: Path) -> None:
    top = _planes(2, n=1)[0]
    path = tmp_path / "result.fits"
    # A result written top-down: its first data row is the top row. read_fits assumes bottom-up.
    write_fits(path, [np.flipud(top)], header={ROWORDER: TOP_DOWN})
    back, header = read_exchange_fits(path, 1)
    assert header[ROWORDER] == TOP_DOWN
    np.testing.assert_array_equal(back[0], top)

    # Without the keyword the same bytes are taken as bottom-up, so the rows come out reversed.
    plain = tmp_path / "plain.fits"
    write_fits(plain, [np.flipud(top)])
    unflipped, _ = read_exchange_fits(plain, 1)
    np.testing.assert_array_equal(unflipped[0], np.flipud(top))


def test_mono_target_takes_channel_zero_of_a_colour_result(tmp_path: Path) -> None:
    planes = _planes(3, n=3)
    path = tmp_path / "rgb.fits"
    write_exchange_fits(path, planes)
    back, _ = read_exchange_fits(path, 1)
    assert len(back) == 1
    np.testing.assert_array_equal(back[0], planes[0])

    with pytest.raises(ValueError):
        read_exchange_fits(path, 4)


def test_tiff_round_trip_is_32_bit_float_top_down(tmp_path: Path) -> None:
    planes = _planes(4)
    path = tmp_path / "rgb.tif"
    write_exchange_tiff(path, planes)
    back = read_exchange_tiff(path, 3)
    assert len(back) == 3
    for a, b in zip(planes, back):
        assert b.dtype == np.float32
        np.testing.assert_array_equal(a, b)

    raw = tifffile.imread(path)
    assert raw.shape == (planes[0].shape[0], planes[0].shape[1], 3)
    assert raw.dtype == np.float32

    mono = tmp_path / "mono.tif"
    write_exchange_tiff(mono, planes[:1])
    assert tifffile.imread(mono).shape == planes[0].shape
    np.testing.assert_array_equal(read_exchange_tiff(mono, 1)[0], planes[0])
    np.testing.assert_array_equal(read_exchange_tiff(path, 1)[0], planes[0])


def test_tiff_rejects_a_plane_count_it_cannot_hold(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_exchange_tiff(tmp_path / "bad.tif", _planes(5, n=2))


def test_sanitize_basic_zeroes_non_finite_and_copies() -> None:
    p = np.array([[np.nan, 0.5], [np.inf, -np.inf]], dtype=np.float32)
    out = sanitize_basic([p])[0]
    np.testing.assert_array_equal(out, np.array([[0.0, 0.5], [0.0, 0.0]], dtype=np.float32))
    assert np.isnan(p[0, 0]), "the input must not be changed"


def test_sanitize_studio_matches_loom_truncation() -> None:
    """Loom's studioSanitize through truncating PixelMath (lib/StepsSyqon.js:2049-2057), as recorded by a trace."""
    a = np.array([np.nan, -0.5, 0.25, 1e31, 3.0, 1.5, 1.005, np.inf, 1e29], dtype=np.float32)
    out, scale = sanitize_studio([a])
    assert scale == 1.0
    np.testing.assert_array_equal(out[0], np.array([0, 0, 0.25, 0, 1, 1, 1, 0, 1], dtype=np.float32))


def test_studio_threshold_and_magnitude_limit() -> None:
    """Values in [0, 1] are kept, values above 1 clip to 1 (the old 1.01 branch was unreachable), and |x| >= 1e30 is zeroed."""
    a = np.array([1.005, 1.02, 5e29, 9e29, 1e30, -1e30, 1e31], dtype=np.float64)
    out, scale = sanitize_studio([a])
    assert scale == 1.0
    np.testing.assert_array_equal(out[0], np.array([1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0]))


def test_studio_write_clips_and_reports_scale_one(tmp_path: Path) -> None:
    planes = [np.array([[0.2, 2.0], [4.0, 1.0]], dtype=np.float32)]
    path = tmp_path / "studio.fits"
    scale = write_exchange_fits(path, planes, sanitize="studio")
    assert scale == 1.0
    back, _ = read_exchange_fits(path, 1)
    np.testing.assert_array_equal(back[0], np.array([[0.2, 1.0], [1.0, 1.0]], dtype=np.float32))


def test_unknown_sanitize_mode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_exchange_fits(tmp_path / "x.fits", _planes(6, n=1), sanitize="none")


def test_declared_output_prefers_the_last_existing_line(tmp_path: Path) -> None:
    out = tmp_path / "o"
    out.mkdir()
    for name in ("a.xisf", "b.xisf", "c.fits"):
        (out / name).write_bytes(b"x")
    a, b, c = (str(out / n) for n in ("a.xisf", "b.xisf", "c.fits"))
    default = str(tmp_path / "default.xisf")

    assert declared_output(f"log\n{a}\n{b}\nbye\n", default, suffix=".xisf") == b
    assert declared_output(f"{a}\n{out / 'missing.xisf'}\n", default, suffix=".xisf") == a
    assert declared_output(f"{a}\n{c}\n", default, suffix=".xisf") == a
    assert declared_output(f"x\r\n{b}\r\n", default, suffix=".xisf") == b
    assert declared_output("nothing here\n", default, suffix=".xisf") == default
    assert declared_output("", default) == default
    assert declared_output(None, default) == default
    assert declared_output([c, b], default) == b


def test_declared_output_ignores_lines_of_three_characters_or_fewer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "abc").write_bytes(b"x")
    (tmp_path / "abcd").write_bytes(b"x")
    default = str(tmp_path / "default.out")
    assert declared_output("abc\n", default) == default
    assert declared_output("abcd\n", default) == "abcd"


def test_declared_output_matches_the_oracle(tmp_path: Path, oracle) -> None:
    """The oracle's studio_declared_output cases, with the recorder's fake output paths mapped onto tmp_path."""
    rec = oracle("loom_tempstretch")["cases"]["studio_declared_output"]
    out = tmp_path / "o"
    out.mkdir()
    for name in ("a.xisf", "b.xisf", "c.fits"):
        (out / name).write_bytes(b"x")
    base = str(out)
    default = str(tmp_path / "default.xisf")

    def mapped(value):
        return None if value is None else value.replace("/fake/out", base)

    cases = {
        "last_existing": "log\n/fake/out/a.xisf\n/fake/out/b.xisf\nbye\n",
        "skips_missing": "/fake/out/a.xisf\n/fake/out/missing.xisf\n",
        "ignores_other_suffix": "/fake/out/a.xisf\n/fake/out/c.fits\n",
        "crlf": "x\r\n/fake/out/b.xisf\r\n",
        "none": "nothing here\n",
        "empty": "",
    }
    for key, stdout in cases.items():
        got = declared_output(stdout.replace("/fake/out", base), default, suffix=".xisf")
        want = mapped(rec[key]) or default
        assert got == want, key


def test_read_fits_header_is_untouched_by_the_flip(tmp_path: Path) -> None:
    """A bottom-up file read through the exchange reader gives the same header as read_fits."""
    path = tmp_path / "h.fits"
    write_exchange_fits(path, _planes(7, n=1), header={"EXPTIME": 30.0})
    planes, header = read_exchange_fits(path, 1)
    raw_planes, raw_header = read_fits(path)
    assert header == raw_header
    np.testing.assert_array_equal(planes[0], raw_planes[0])
