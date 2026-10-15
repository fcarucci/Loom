"""Channel counts from headers (loomsiril/loom/channels.py read_header): FITS, tile-compressed FITS and XISF.

Only headers are read: every pixel reader is patched to raise, and the data parts written here are never valid pixels.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Optional

import pytest

from loomsiril import io_fits
from loomsiril.loom import channels as ch

BLOCK = 2880


def _card(name: str, value: Any = None, comment: str = "") -> bytes:
    if value is None:
        text = name.ljust(8)
    else:
        if isinstance(value, bool):
            v = "T" if value else "F"
        elif isinstance(value, str):
            v = "'" + value.replace("'", "''").ljust(8) + "'"
        else:
            v = str(value)
        text = name.ljust(8) + "= " + v.rjust(20) if not isinstance(value, str) else name.ljust(8) + "= " + v
        if comment:
            text += " / " + comment
    return text.ljust(80).encode("ascii")


def _header(cards: list[bytes]) -> bytes:
    raw = b"".join(cards) + _card("END")
    return raw + b" " * (-len(raw) % BLOCK)


def _pad(n: int) -> bytes:
    return b"\x00" * (-n % BLOCK)


def _plain_fits(naxes: list[int], extra: Optional[list[bytes]] = None) -> bytes:
    cards = [_card("SIMPLE", True), _card("BITPIX", 16), _card("NAXIS", len(naxes))]
    cards += [_card(f"NAXIS{i + 1}", n) for i, n in enumerate(naxes)]
    cards += extra or []
    return _header(cards) + b"\xff\xfe"  # a stub, not a pixel array: reading it as one would fail


def _fz(zaxes: list[int], lead_table: bool = False, primary_extra: Optional[list[bytes]] = None) -> bytes:
    pa: list[int] = []
    cards = [_card("SIMPLE", True), _card("BITPIX", 16), _card("NAXIS", len(pa))]
    cards += [_card(f"NAXIS{i + 1}", n) for i, n in enumerate(pa)]
    cards += (primary_extra or []) + [_card("EXTEND", True)]
    primary = _header(cards)
    if lead_table:  # an ordinary table before the image: skipped by its size
        table = [_card("XTENSION", "BINTABLE"), _card("BITPIX", 8), _card("NAXIS", 2), _card("NAXIS1", 10),
                 _card("NAXIS2", 100), _card("PCOUNT", 0), _card("GCOUNT", 1), _card("TFIELDS", 1)]
        primary += _header(table) + b"\x01" * 1000 + _pad(1000)
    ext = [_card("XTENSION", "BINTABLE"), _card("BITPIX", 8), _card("NAXIS", 2), _card("NAXIS1", 8),
           _card("NAXIS2", 4), _card("PCOUNT", 16), _card("GCOUNT", 1), _card("TFIELDS", 1),
           _card("ZIMAGE", True), _card("ZCMPTYPE", "RICE_1"), _card("ZBITPIX", 16), _card("ZNAXIS", len(zaxes))]
    ext += [_card(f"ZNAXIS{i + 1}", n) for i, n in enumerate(zaxes)]
    ext += [_card("FILTER", "Ha")]
    heap = 8 * 4 + 16
    return primary + _header(ext) + b"\x00" * heap + _pad(heap)


def _xisf(geometry: str) -> bytes:
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">'
           f'<Image geometry="{geometry}" sampleFormat="Float32" colorSpace="RGB" location="attachment:4096:100">'
           f'<FITSKeyword name="FILTER" value="\'L\'" comment=""/></Image></xisf>').encode("utf-8")
    return b"XISF0100" + struct.pack("<I", len(xml)) + b"\x00" * 4 + xml + b"\x00" * 16


@pytest.fixture(autouse=True)
def no_pixels(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a pixel reader was called")

    monkeypatch.setattr(io_fits, "read_fits", boom)
    import numpy as np
    monkeypatch.setattr(np, "fromfile", boom)
    monkeypatch.setattr(np, "frombuffer", boom)


def _write(tmp_path: Path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


@pytest.mark.parametrize("naxes, channels", [([40, 30], 1), ([40, 30, 3], 3), ([40, 30, 2], 2), ([40, 30, 1], 1)])
def test_fits_channels(tmp_path: Path, naxes: list[int], channels: int) -> None:
    h = ch.read_header(_write(tmp_path, "m.fits", _plain_fits(naxes)))
    assert h["channels"] == channels
    assert (h["width"], h["height"]) == (40, 30)


def test_fits_without_axes_is_one_channel(tmp_path: Path) -> None:
    assert ch.read_header(_write(tmp_path, "m.fit", _plain_fits([])))["channels"] == 1


@pytest.mark.parametrize("name", ["Master.FITS", "m.FIT", "MiXed.Fit", "Nébuleuse_Orion.fits", "ひかり.fit"])
def test_fits_names_mixed_case_and_non_ascii(tmp_path: Path, name: str) -> None:
    assert ch.read_header(_write(tmp_path, name, _plain_fits([8, 6, 3])))["channels"] == 3


@pytest.mark.parametrize("name", ["m.fz", "m.fits.fz", "m.fit.fz", "M.FITS.FZ", "Nébuleuse.fits.fz"])
@pytest.mark.parametrize("zaxes, channels", [([40, 30], 1), ([40, 30, 3], 3), ([40, 30, 1], 1)])
def test_fz_channels(tmp_path: Path, name: str, zaxes: list[int], channels: int) -> None:
    h = ch.read_header(_write(tmp_path, name, _fz(zaxes)))
    assert h["channels"] == channels
    assert (h["width"], h["height"]) == (40, 30)
    assert h["format"] == "fits"
    assert {"name": "FILTER", "value": "'Ha'"} in h["keywords"]


def test_fz_merges_primary_keywords_extension_wins(tmp_path: Path) -> None:
    extra = [_card("OBJECT", "M 42"), _card("FILTER", "L")]
    h = ch.read_header(_write(tmp_path, "m.fits.fz", _fz([10, 5, 3], primary_extra=extra)))
    kw = {k["name"]: k["value"] for k in h["keywords"]}
    assert kw["OBJECT"] == "'M 42'" and kw["FILTER"] == "'Ha'"
    assert len([k for k in h["keywords"] if k["name"] == "FILTER"]) == 1


def test_fz_skips_a_leading_table_by_its_size(tmp_path: Path) -> None:
    h = ch.read_header(_write(tmp_path, "m.fz", _fz([12, 7, 3], lead_table=True)))
    assert (h["channels"], h["width"], h["height"]) == (3, 12, 7)


def test_fz_znaxis_two_is_one_channel_even_with_a_stray_znaxis3(tmp_path: Path) -> None:
    data = _fz([40, 30]).replace(_card("FILTER", "Ha"), _card("ZNAXIS3", 3))
    assert ch.read_header(_write(tmp_path, "m.fz", data))["channels"] == 1


@pytest.mark.parametrize("geometry, channels", [("640:512:1", 1), ("640:512:3", 3), ("640:512", 1)])
def test_xisf_channels(tmp_path: Path, geometry: str, channels: int) -> None:
    h = ch.read_header(_write(tmp_path, "WBPP_Master.XISF", _xisf(geometry)))
    assert h["channels"] == channels
    assert (h["width"], h["height"]) == (640, 512)


def test_xisf_non_ascii_name(tmp_path: Path) -> None:
    assert ch.read_header(_write(tmp_path, "Orión_Ñandú.xisf", _xisf("4:4:3")))["channels"] == 3


def test_unknown_format_still_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ch.read_header(_write(tmp_path, "x.bin", b"not an image at all"))
