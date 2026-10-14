"""loomsiril.io_fits and loomsiril.io_tiff: round trips, BZERO/BSCALE, header types, ICC, RGBA, .part hygiene."""

from __future__ import annotations

import math
import os
import struct

import numpy as np
import pytest

from loomsiril import io_fits, io_tiff


def _card(key: str, value: str) -> bytes:
    return f"{key:<8}= {value:>20}".ljust(80).encode("ascii")


def _fits_bytes(cards: list[bytes], data: bytes) -> bytes:
    head = b"".join(cards) + f"{'END':<80}".encode("ascii")
    head += b" " * (-len(head) % 2880)
    return head + data + b"\0" * (-len(data) % 2880)


# ---------------------------------------------------------------- FITS


def test_fits_float32_round_trip_exact(tmp_path):
    rng = np.random.default_rng(7)
    plane = rng.random((12, 20), dtype=np.float32)
    p = tmp_path / "a.fits"
    io_fits.write_fits(p, [plane], bitpix=-32)
    planes, header = io_fits.read_fits(p)
    assert len(planes) == 1 and planes[0].dtype == np.float32 and planes[0].shape == (12, 20)
    assert np.array_equal(planes[0], plane)
    assert header["BITPIX"] == -32 and header["NAXIS1"] == 20 and header["NAXIS2"] == 12
    assert os.path.getsize(p) % 2880 == 0


def test_fits_unsigned16_round_trip_within_one_lsb(tmp_path):
    plane = np.linspace(0, 1, 7 * 9, dtype=np.float32).reshape(7, 9)
    p = tmp_path / "u16.fits"
    io_fits.write_fits(p, [plane], bitpix=16)
    planes, header = io_fits.read_fits(p)
    assert header["BZERO"] == 32768 and header["BITPIX"] == 16
    assert planes[0].dtype == np.float32
    assert np.max(np.abs(planes[0] - plane)) <= 1 / 65535 + 1e-7


def test_fits_three_channels_are_planes(tmp_path):
    rng = np.random.default_rng(3)
    planes_in = [rng.random((5, 6), dtype=np.float32) for _ in range(3)]
    p = tmp_path / "rgb.fits"
    io_fits.write_fits(p, planes_in, bitpix=-32, header={"OBJECT": "IC 1396"})
    planes, header = io_fits.read_fits(p)
    assert header["NAXIS"] == 3 and header["NAXIS3"] == 3
    assert len(planes) == 3
    for a, b in zip(planes, planes_in):
        assert np.array_equal(a, b)
    assert header["OBJECT"] == "IC 1396"


def test_fits_row_order_is_top_down_after_read(tmp_path):
    # the array a caller gets is top-down: a row written as the top row comes back as array row 0
    plane = np.zeros((4, 3), dtype=np.float32)
    plane[0, :] = 0.25
    p = tmp_path / "rows.fits"
    io_fits.write_fits(p, [plane])
    planes, header = io_fits.read_fits(p)
    assert np.all(planes[0][0] == 0.25) and np.all(planes[0][1:] == 0)
    assert header["LOOMROWS"] == "top-down"


def _raw_fits(tmp_path, name: str, bottom_up: np.ndarray, extra: list[bytes]) -> tuple[object, np.ndarray]:
    """A FITS file written by hand: big-endian -32 data, first stored row = FITS row 1 = the BOTTOM of the image."""
    h, w = bottom_up.shape
    cards = [_card("SIMPLE", "T"), _card("BITPIX", "-32"), _card("NAXIS", "2"), _card("NAXIS1", str(w)),
             _card("NAXIS2", str(h))] + extra
    p = tmp_path / name
    p.write_bytes(_fits_bytes(cards, bottom_up.astype(">f4").tobytes()))
    return p, bottom_up


def _tan_cards(crpix1: float, crpix2: float, cd: tuple[float, float, float, float]) -> list[bytes]:
    out = []
    for k, v in (("CRVAL1", "324.745"), ("CRVAL2", "57.514"), ("CRPIX1", repr(crpix1)), ("CRPIX2", repr(crpix2)),
                 ("CD1_1", repr(cd[0])), ("CD1_2", repr(cd[1])), ("CD2_1", repr(cd[2])), ("CD2_2", repr(cd[3]))):
        out.append(_card(k, v))
    return out


def test_fits_read_puts_fits_row_one_at_the_bottom_and_maps_the_wcs(tmp_path):
    from loomsiril import core, wcs

    h, w = 30, 40
    bottom_up = np.zeros((h, w), dtype=np.float32)
    bottom_up[0, 0] = 1.0        # FITS row 1, column 1: the bottom-left of the displayed image
    bottom_up[h - 1, w - 1] = 0.5  # the top-right, to be sure it is not symmetric
    raw_wcs = {"crval1": 324.745, "crval2": 57.514, "crpix1": 21.25, "crpix2": 9.5,
               "cd": [-0.0006, 0.00011, 0.00007, 0.00055]}
    p, _ = _raw_fits(tmp_path, "bottom.fits", bottom_up,
                     _tan_cards(raw_wcs["crpix1"], raw_wcs["crpix2"], tuple(raw_wcs["cd"])))
    planes, header = io_fits.read_fits(p)
    top = planes[0]
    assert top[h - 1, 0] == 1.0 and top[0, w - 1] == 0.5
    assert header["LOOMROWS"] == "top-down"
    got = wcs.keyword_wcs(header)
    assert got is not None
    assert got["crpix2"] == pytest.approx(h + 1 - raw_wcs["crpix2"], abs=1e-12)
    # the pixel at FITS (1-based) (1, 1) = 0-based (0, 0) on the raw row = (0, h-1) in the top-down array
    raw_sky = core.tan_unproject(0, 0, raw_wcs)
    top_sky = core.tan_unproject(0, h - 1, got)
    assert abs(raw_sky["ra"] - top_sky["ra"]) < 1e-9 and abs(raw_sky["dec"] - top_sky["dec"]) < 1e-9


def test_fits_read_converts_pc_cdelt_form(tmp_path):
    from loomsiril import core, wcs

    h, w = 20, 25
    bottom_up = np.zeros((h, w), dtype=np.float32)
    bottom_up[0, 0] = 1.0
    cards = [_card("CRVAL1", "10.0"), _card("CRVAL2", "-5.0"), _card("CRPIX1", "12.5"), _card("CRPIX2", "7.25"),
             _card("CDELT1", "-0.0004"), _card("CDELT2", "0.0004"), _card("PC1_1", "0.9"), _card("PC1_2", "0.4"),
             _card("PC2_1", "-0.4"), _card("PC2_2", "0.9")]
    p, _ = _raw_fits(tmp_path, "pc.fits", bottom_up, cards)
    planes, header = io_fits.read_fits(p)
    top = planes[0]
    assert top[h - 1, 0] == 1.0
    raw = {"crval1": 10.0, "crval2": -5.0, "crpix1": 12.5, "crpix2": 7.25,
           "cd": [-0.0004 * 0.9, -0.0004 * 0.4, 0.0004 * -0.4, 0.0004 * 0.9]}
    got = wcs.keyword_wcs(header)
    assert got is not None
    for (x, y) in [(0, 0), (5, 3), (24, 19)]:
        a = core.tan_unproject(x, h - 1 - y, raw)  # bottom-up row of the same pixel is h-1-y
        b = core.tan_unproject(x, y, got)          # top-down row y of the converted header
        assert abs(a["ra"] - b["ra"]) < 1e-9 and abs(a["dec"] - b["dec"]) < 1e-9


def test_materialise_cd_from_crota2_is_the_fits_rotation():
    cdelt1, cdelt2, rot = -0.0004, 0.00055, 23.0
    out = io_fits.materialise_cd({"CDELT1": cdelt1, "CDELT2": cdelt2, "CROTA2": rot, "CRVAL1": 10.0})
    r = math.radians(rot)
    assert out["CD1_1"] == pytest.approx(cdelt1 * math.cos(r), rel=1e-15)
    assert out["CD1_2"] == pytest.approx(-cdelt2 * math.sin(r), rel=1e-15)
    assert out["CD2_1"] == pytest.approx(cdelt1 * math.sin(r), rel=1e-15)
    assert out["CD2_2"] == pytest.approx(cdelt2 * math.cos(r), rel=1e-15)
    assert not {"CDELT1", "CDELT2", "CROTA2"} & set(out)   # the keys it converts are dropped
    assert out["CRVAL1"] == 10.0


def test_fits_read_converts_crota2_cdelt_form(tmp_path):
    from loomsiril import core, wcs

    h, w = 20, 25
    bottom_up = np.zeros((h, w), dtype=np.float32)
    bottom_up[0, 0] = 1.0
    cdelt1, cdelt2, rot = -0.0004, 0.00055, 23.0
    cards = [_card("CRVAL1", "10.0"), _card("CRVAL2", "-5.0"), _card("CRPIX1", "12.5"), _card("CRPIX2", "7.25"),
             _card("CDELT1", repr(cdelt1)), _card("CDELT2", repr(cdelt2)), _card("CROTA2", repr(rot))]
    p, _ = _raw_fits(tmp_path, "crota.fits", bottom_up, cards)
    planes, header = io_fits.read_fits(p)
    top = planes[0]
    assert top[h - 1, 0] == 1.0
    r = math.radians(rot)
    raw = {"crval1": 10.0, "crval2": -5.0, "crpix1": 12.5, "crpix2": 7.25,
           "cd": [cdelt1 * math.cos(r), -cdelt2 * math.sin(r), cdelt1 * math.sin(r), cdelt2 * math.cos(r)]}
    got = wcs.keyword_wcs(header)
    assert got is not None
    for (x, y) in [(0, 0), (5, 3), (24, 19)]:
        a = core.tan_unproject(x, h - 1 - y, raw)  # bottom-up row of the same pixel is h-1-y
        b = core.tan_unproject(x, y, got)          # top-down row y of the converted header
        assert abs(a["ra"] - b["ra"]) < 1e-9 and abs(a["dec"] - b["dec"]) < 1e-9


def test_fits_write_then_read_is_identity(tmp_path):
    from loomsiril import wcs

    rng = np.random.default_rng(21)
    planes_in = [rng.random((17, 23), dtype=np.float32) for _ in range(2)]
    raw = {"crval1": 324.745, "crval2": 57.514, "crpix1": 11.75, "crpix2": 4.5,
           "cd": [-0.0005, 0.00012, 0.00003, 0.0005]}
    header_in = {"OBJECT": "IC 1396", "EXPTIME": 120.0}
    header_in.update(_wcs_header(raw))
    p = tmp_path / "rt.fits"
    io_fits.write_fits(p, planes_in, header=header_in)
    planes_out, header_out = io_fits.read_fits(p)
    assert len(planes_out) == 2
    for a, b in zip(planes_out, planes_in):
        assert np.array_equal(a, b)
    assert "LOOMROWS" in header_out and header_out["OBJECT"] == "IC 1396"
    got = wcs.keyword_wcs(header_out)
    assert got["crpix2"] == pytest.approx(raw["crpix2"], rel=0, abs=1e-12)
    assert got["cd"] == pytest.approx(raw["cd"], rel=0, abs=1e-12)
    assert got["crpix1"] == raw["crpix1"] and got["crval1"] == raw["crval1"]


def _wcs_header(w: dict) -> dict:
    """Header keywords for a top-down wcs dict (as read_fits returns it)."""
    return {"CRVAL1": w["crval1"], "CRVAL2": w["crval2"], "CRPIX1": w["crpix1"], "CRPIX2": w["crpix2"],
            "CD1_1": w["cd"][0], "CD1_2": w["cd"][1], "CD2_1": w["cd"][2], "CD2_2": w["cd"][3]}


def test_fits_written_file_is_bottom_up_on_disk(tmp_path):
    # a file we write reads the right way up in Siril: the top row of the array is the LAST row in the file
    top = np.zeros((5, 4), dtype=np.float32)
    top[0, 0] = 0.75  # top-left of the displayed image
    p = tmp_path / "disk.fits"
    io_fits.write_fits(p, [top])
    raw = p.read_bytes()
    data = raw[2880:2880 + 5 * 4 * 4]
    first_row = np.frombuffer(data[:16], dtype=">f4")
    last_row = np.frombuffer(data[-16:], dtype=">f4")
    assert np.all(first_row == 0) and last_row[0] == 0.75


def test_fits_and_tiff_planes_are_pixel_aligned(tmp_path):
    from loomsiril import io_tiff

    rng = np.random.default_rng(5)
    h, w = 12, 18
    bottom_up = np.zeros((h, w), dtype=np.float32)
    bottom_up[1, 2] = 0.9   # FITS row 2, column 3: near the bottom
    bottom_up[h - 2, w - 3] = 0.3
    bottom_up += rng.random((h, w), dtype=np.float32) * 0.01
    p, _ = _raw_fits(tmp_path, "align.fits", bottom_up, [])
    fits_planes, _ = io_fits.read_fits(p)
    tif = tmp_path / "align.tif"
    io_tiff.write_tiff16(tif, fits_planes)
    tiff_planes, _ = io_tiff.read_image(tif)
    assert tiff_planes[0].shape == fits_planes[0].shape
    assert np.max(np.abs(tiff_planes[0] - np.clip(fits_planes[0], 0, 1))) <= 1 / 65535 + 1e-7
    # the bright FITS pixel lands on the same displayed position in both: array row h-1-1 = h-2, column 2
    assert np.unravel_index(np.argmax(tiff_planes[0]), (h, w)) == (h - 2, 2)


def test_fits_bzero_bscale_applied(tmp_path):
    # raw int16 -1 with BZERO 32768 is unsigned 32767; scaled by 65535
    raw = np.array([[-32768, -1], [0, 32767]], dtype=">i2")
    cards = [_card("SIMPLE", "T"), _card("BITPIX", "16"), _card("NAXIS", "2"), _card("NAXIS1", "2"),
             _card("NAXIS2", "2"), _card("BZERO", "32768"), _card("BSCALE", "1")]
    p = tmp_path / "bzero.fits"
    p.write_bytes(_fits_bytes(cards, raw.tobytes()))
    planes, header = io_fits.read_fits(p)
    assert header["BZERO"] == 32768
    # FITS row 1 is the bottom row: the top-down array has it last
    expect = np.array([[32768, 65535], [0, 32767]], dtype=np.float64) / 65535
    assert np.allclose(planes[0], expect, atol=1e-7, rtol=0)


def test_fits_bscale_on_float_applied(tmp_path):
    raw = np.array([[1.0, 2.0]], dtype=">f4")
    cards = [_card("SIMPLE", "T"), _card("BITPIX", "-32"), _card("NAXIS", "2"), _card("NAXIS1", "2"),
             _card("NAXIS2", "1"), _card("BSCALE", "0.5"), _card("BZERO", "0.25")]
    p = tmp_path / "scale.fits"
    p.write_bytes(_fits_bytes(cards, raw.tobytes()))
    planes, _ = io_fits.read_fits(p)
    assert np.allclose(planes[0], [[0.75, 1.25]])


def test_fits_header_value_types(tmp_path):
    p = tmp_path / "hdr.fits"
    io_fits.write_fits(p, [np.zeros((2, 2), dtype=np.float32)],
                       header={"FLAG": True, "OFF": False, "COUNT": 42, "GAIN": 1.5, "NAME": "M 31 'x'",
                               "EXPO": 1.25e-3})
    _, header = io_fits.read_fits(p)
    assert header["FLAG"] is True and header["OFF"] is False
    assert header["COUNT"] == 42 and isinstance(header["COUNT"], int)
    assert header["GAIN"] == 1.5 and isinstance(header["GAIN"], float)
    assert header["NAME"] == "M 31 'x'"
    assert header["EXPO"] == pytest.approx(1.25e-3)


def test_fits_string_and_comment_parsing(tmp_path):
    cards = [_card("SIMPLE", "T"), _card("BITPIX", "-32"), _card("NAXIS", "2"), _card("NAXIS1", "1"),
             _card("NAXIS2", "1"),
             (f"{'CTYPE1':<8}= {chr(39) + 'RA---TAN' + chr(39):<20} / projection").ljust(80).encode("ascii"),
             (f"{'CRVAL1':<8}= {'324.745':>20} / degrees").ljust(80).encode("ascii")]
    p = tmp_path / "cm.fits"
    p.write_bytes(_fits_bytes(cards, struct.pack(">f", 0.5)))
    planes, header = io_fits.read_fits(p)
    assert header["CTYPE1"] == "RA---TAN"
    assert header["CRVAL1"] == 324.745
    assert planes[0][0, 0] == 0.5


def test_fits_write_rejects_mixed_shapes(tmp_path):
    with pytest.raises(ValueError):
        io_fits.write_fits(tmp_path / "x.fits", [np.zeros((2, 2), np.float32), np.zeros((3, 2), np.float32)])


def test_fits_write_rejects_unknown_bitpix(tmp_path):
    with pytest.raises(ValueError):
        io_fits.write_fits(tmp_path / "x.fits", [np.zeros((2, 2), np.float32)], bitpix=8)


# ---------------------------------------------------------------- TIFF


def test_tiff16_round_trip_with_icc(tmp_path):
    rng = np.random.default_rng(11)
    planes = [rng.random((9, 13), dtype=np.float32) for _ in range(3)]
    icc = bytes(range(256)) * 4
    p = tmp_path / "out.tif"
    io_tiff.write_tiff16(p, planes, icc=icc)
    got, got_icc = io_tiff.read_image(p)
    assert got_icc == icc
    assert len(got) == 3 and all(g.dtype == np.float32 and g.shape == (9, 13) for g in got)
    for g, src in zip(got, planes):
        assert np.max(np.abs(g - np.clip(src, 0, 1))) <= 1 / 65535 + 1e-7


def test_tiff16_grey_and_no_icc(tmp_path):
    plane = np.array([[0.0, 0.5], [1.0, 2.0]], dtype=np.float32)  # 2.0 clips to 1
    p = tmp_path / "grey.tif"
    io_tiff.write_tiff16(p, [plane])
    got, icc = io_tiff.read_image(p)
    assert icc is None
    assert len(got) == 1
    assert np.allclose(got[0], [[0.0, 0.5], [1.0, 1.0]], atol=1 / 65535 + 1e-7)


def test_tiff16_part_file_gone_after_write(tmp_path):
    p = tmp_path / "frame.tif"
    io_tiff.write_tiff16(p, [np.zeros((3, 3), np.float32)])
    assert p.exists()
    assert not (tmp_path / "frame.tif.part").exists()
    assert sorted(x.name for x in tmp_path.iterdir()) == ["frame.tif"]


def test_read_rgba_drops_alpha(tmp_path):
    import tifffile

    rgba = np.zeros((4, 5, 4), dtype=np.uint8)
    rgba[..., 0] = 255
    rgba[..., 3] = 7
    p = tmp_path / "rgba.tif"
    tifffile.imwrite(p, rgba, photometric="rgb", extrasamples=["unassalpha"])
    got, _ = io_tiff.read_image(p)
    assert len(got) == 3
    assert np.all(got[0] == 1.0) and np.all(got[1] == 0.0) and np.all(got[2] == 0.0)


def test_read_8bit_scaled_by_255(tmp_path):
    import tifffile

    grey = np.array([[0, 128, 255]], dtype=np.uint8)
    p = tmp_path / "g8.tif"
    tifffile.imwrite(p, grey)
    got, icc = io_tiff.read_image(p)
    assert icc is None
    assert np.allclose(got[0], [[0.0, 128 / 255, 1.0]], atol=1e-7)


def test_read_planar_rgb(tmp_path):
    import tifffile

    planes = np.zeros((3, 4, 6), dtype=np.uint16)
    planes[0] = 65535
    p = tmp_path / "planar.tif"
    tifffile.imwrite(p, planes, planarconfig="separate", photometric="rgb")
    got, _ = io_tiff.read_image(p)
    assert len(got) == 3
    assert np.all(got[0] == 1.0) and np.all(got[1] == 0.0)


# ---------------------------------------------------------------- R1-e characterization (old code)
# Pins the value parser's branches that no other test reaches, and the bytes write_fits emits for one
# small file at each bitpix (the sha256 values were taken from the code before the R1-e refactor).


def test_parse_value_blank_value_and_comment_only_is_none():
    assert io_fits._parse_value("") is None
    assert io_fits._parse_value("   / only a comment") is None


def test_parse_value_unparseable_text_is_returned_as_text():
    assert io_fits._parse_value("NOTANUMBER / c") == "NOTANUMBER"


def test_parse_value_number_forms():
    assert io_fits._parse_value("   -12 / x") == -12
    assert io_fits._parse_value("1.5D3") == 1500.0
    assert io_fits._parse_value("2.5d-1") == 0.25
    assert io_fits._parse_value("T") is True
    assert io_fits._parse_value("F / no") is False


def test_parse_value_string_forms():
    assert io_fits._parse_value("'a''b' / c") == "a'b"
    assert io_fits._parse_value("'ab   '") == "ab"


def test_write_fits_without_planes_is_refused(tmp_path):
    with pytest.raises(ValueError, match="at least one plane"):
        io_fits.write_fits(tmp_path / "x.fits", [])


def test_write_fits_skips_structural_keywords_from_the_header(tmp_path):
    plane = np.zeros((2, 3), dtype=np.float32)
    path = tmp_path / "s.fits"
    io_fits.write_fits(path, [plane], {"NAXIS": 7, "BZERO": 3, "SIMPLE": False, "EXTEND": True, "OBJECT": "M42"})
    _, header = io_fits.read_fits(path)
    assert header["NAXIS"] == 2 and "BZERO" not in header and header["SIMPLE"] is True
    assert header["OBJECT"] == "M42"


def test_write_fits_float64_round_trip_and_size(tmp_path):
    rng = np.random.default_rng(11)
    planes = [rng.random((5, 7)), rng.random((5, 7))]
    path = tmp_path / "d.fits"
    io_fits.write_fits(path, planes, bitpix=-64)
    back, _ = io_fits.read_fits(path)
    assert os.path.getsize(path) == 2880 + 2880  # one header block, 2 * 5 * 7 * 8 = 560 bytes padded to a block
    for a, b in zip(planes, back):
        assert np.array_equal(a.astype(np.float32), b)


_PIN_P = np.array([[0.0, 0.25, 0.5], [0.75, 1.0, 0.125]], dtype=np.float32)
_PIN_Q = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], dtype=np.float32)
_PIN_HEADER = {"OBJECT": "SHA", "CRPIX1": 2.5, "CRPIX2": 1.0, "CD1_1": -0.001, "CD1_2": 0.0, "CD2_1": 0.0,
               "CD2_2": 0.001, "EXPTIME": 12.5, "FLAG": True}


@pytest.mark.parametrize(
    ("bitpix", "sha256"),
    [
        (-32, "ad5791d3a400cbc5738c0b7c4f4688e1298db268d89ae7762e3379c74a15014b"),
        (-64, "7769aaea1229c87c582c2bccca92b931d76573a5c97de77204db51cd62edc834"),
        (16, "28fdfe108feb6ec7a96619256f33803179611c20b8176c00bb2e50a777891240"),
    ],
)
def test_write_fits_bytes_are_pinned(tmp_path, bitpix, sha256):
    import hashlib

    path = tmp_path / f"pin{bitpix}.fits"
    io_fits.write_fits(path, [_PIN_P, _PIN_Q], _PIN_HEADER, bitpix=bitpix)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256
