"""Frame Selector: the header of each frame file (loomsiril/frames_headers.py), built from synthetic files.

Every file is written here under ``tmp_path``. The readers never need pixels, so the compressed and XISF fixtures
carry headers only.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import numpy as np

from loomsiril import frames_headers as fh
from loomsiril.io_fits import write_fits


def _card(key: str, value: str) -> str:
    return f"{key:<8}= {value}".ljust(80)


def _fits_bytes(cards: list[str], data: bytes = b"") -> bytes:
    """Cards, the END card and blanks up to a whole 2880-byte block, then ``data``."""
    head = "".join(c.ljust(80) for c in cards) + "END".ljust(80)
    return head.encode("ascii") + b" " * (-len(head) % 2880) + data


def _primary_cards() -> list[str]:
    return [_card("SIMPLE", "T"), _card("BITPIX", "8"), _card("NAXIS", "0"), _card("EXTEND", "T")]


def _write_fz(path: Path, primary_extra: list[str] | None = None, zimage: str = "T", znaxis3: str | None = None) -> str:
    """A tile-compressed frame: an empty primary HDU, then a BINTABLE of 400 x 300 with FILTER 'L'."""
    primary = _fits_bytes(_primary_cards() + (primary_extra or []))
    ext_cards = [
        _card("XTENSION", "'BINTABLE'"),
        _card("BITPIX", "8"),
        _card("NAXIS", "2"),
        _card("ZIMAGE", zimage),
        _card("ZBITPIX", "16"),
        _card("ZNAXIS", "2" if znaxis3 is None else "3"),
        _card("ZNAXIS1", "400"),
        _card("ZNAXIS2", "300"),
    ]
    if znaxis3 is not None:
        ext_cards.append(_card("ZNAXIS3", znaxis3))
    ext_cards.append(_card("FILTER", "'L       '"))
    ext = _fits_bytes(ext_cards)
    path.write_bytes(primary + ext)
    return str(path)


def test_compressed_fits_without_zimage_is_unknown(tmp_path: Path) -> None:
    path = _write_fz(tmp_path / "plain_table.fits.fz", zimage="F")
    assert fh.read_header(path) is None
    info, keyword = fh.quiet_header(path)
    assert info is None
    assert keyword("FILTER") is None


def test_three_plane_compressed_fits_has_three_channels(tmp_path: Path) -> None:
    path = _write_fz(tmp_path / "rgb.fits.fz", znaxis3="3")
    info = fh.read_header(path)
    assert info is not None
    assert (info.width, info.height, info.channels) == (400, 300, 3)


def _xisf(path: Path, xml: str) -> str:
    body = xml.encode("utf-8")
    path.write_bytes(b"XISF0100" + struct.pack("<I", len(body)) + b"\x00" * 4 + body)
    return str(path)


def _xisf_xml(filter_value: str = "'OIII'") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">\n'
        ' <Image geometry="64:48:1" sampleFormat="Float32" colorSpace="Gray" location="attachment:4096:12288">\n'
        f'  <FITSKeyword name="FILTER" value="{filter_value}" comment=""/>\n'
        " </Image>\n"
        "</xisf>\n"
    )


def _plane(w: int = 64, h: int = 48) -> np.ndarray:
    return np.zeros((h, w), dtype=np.float32)


def test_fits_keywords_and_geometry(tmp_path: Path) -> None:
    path = str(tmp_path / "light.fits")
    write_fits(path, [_plane()], header={
        "FILTER": "Ha",
        "EXPTIME": 300.0,
        "XBINNING": 2,
        "IMAGETYP": "Light Frame",
        "DATE-OBS": "2026-10-14T22:00:00",
    })
    info = fh.read_header(path)
    assert info is not None
    assert (info.width, info.height, info.channels) == (64, 48, 1)
    kw = info.keywords
    assert fh.keyword_value(kw, "FILTER") == "Ha"
    assert fh.keyword_value(kw, "EXPTIME") == "300.0"
    assert fh.keyword_value(kw, "XBINNING") == "2"
    assert fh.keyword_value(kw, "IMAGETYP") == "Light Frame"
    assert fh.keyword_value(kw, "DATE-OBS") == "2026-10-14T22:00:00"
    # the raw value text keeps the quotes, as PixInsight hands it over
    assert dict(kw)["FILTER"] == "'Ha      '"


def test_three_plane_fits_has_three_channels(tmp_path: Path) -> None:
    path = str(tmp_path / "rgb.fit")
    write_fits(path, [_plane(), _plane(), _plane()])
    info = fh.read_header(path)
    assert info is not None
    assert (info.width, info.height, info.channels) == (64, 48, 3)


def test_history_card_is_a_keyword_with_its_text(tmp_path: Path) -> None:
    path = tmp_path / "hist.fit"
    path.write_bytes(_fits_bytes(_primary_cards()[:3] + [
        _card("NAXIS1", "4"),
        _card("NAXIS2", "4"),
        "HISTORY Calibrated with WBPP".ljust(80),
    ]))
    info = fh.read_header(str(path))
    assert info is not None
    assert fh.keyword_value(info.keywords, "HISTORY") == "Calibrated with WBPP"


def test_compressed_fits_geometry_and_filter_from_the_extension(tmp_path: Path) -> None:
    path = _write_fz(tmp_path / "tile.fits.fz")
    info = fh.read_header(path)
    assert info is not None
    assert (info.width, info.height, info.channels) == (400, 300, 1)
    assert fh.keyword_value(info.keywords, "FILTER") == "L"


def test_compressed_fits_extension_keyword_wins_over_primary(tmp_path: Path) -> None:
    path = _write_fz(tmp_path / "both.fit.fz", primary_extra=[_card("FILTER", "'R'")])
    info = fh.read_header(path)
    assert info is not None
    assert fh.keyword_value(info.keywords, "FILTER") == "L"
    assert fh.keyword_value(info.keywords, "NAXIS") == "2"  # a name only the extension carries still reads


def test_xisf_keyword_and_geometry(tmp_path: Path) -> None:
    path = _xisf(tmp_path / "master.xisf", _xisf_xml())
    info = fh.read_header(path)
    assert info is not None
    assert (info.width, info.height, info.channels) == (64, 48, 1)
    assert fh.keyword_value(info.keywords, "FILTER") == "OIII"


def test_xisf_without_image_geometry_keeps_its_keywords(tmp_path: Path) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">\n'
        ' <Metadata>\n'
        '  <FITSKeyword name="FILTER" value="\'Ha\'" comment=""/>\n'
        ' </Metadata>\n'
        "</xisf>\n"
    )
    path = _xisf(tmp_path / "nogeom.xisf", xml)
    info = fh.read_header(path)
    assert info is not None
    assert (info.width, info.height, info.channels) == (0, 0, 0)
    assert fh.keyword_value(info.keywords, "FILTER") == "Ha"
    entry = fh.entry_for(path)
    assert (entry["width"], entry["height"]) == (0, 0)
    assert entry["filter"] == "Ha"


def test_keyword_value_quoted_blank_and_absent() -> None:
    kw = [("A", "''"), ("B", "'  x '"), ("C", "   "), ("D", "120.0"), ("E", "T")]
    assert fh.keyword_value(kw, "A") is None
    assert fh.keyword_value(kw, "B") == "x"
    assert fh.keyword_value(kw, "C") is None
    assert fh.keyword_value(kw, "D") == "120.0"
    assert fh.keyword_value(kw, "E") == "T"
    assert fh.keyword_value(kw, "MISSING") is None
    assert fh.keyword_value(None, "A") is None


def test_keyword_value_takes_the_first_match() -> None:
    assert fh.keyword_value([("F", "'a'"), ("F", "'b'")], "F") == "a"


def test_quiet_header_on_a_missing_file(tmp_path: Path) -> None:
    info, keyword = fh.quiet_header(str(tmp_path / "absent.fits"))
    assert info is None
    assert keyword("FILTER") is None


def test_quiet_header_on_garbage_bytes(tmp_path: Path) -> None:
    for name in ("junk.fits", "junk.fits.fz", "junk.xisf", "junk.fit"):
        path = tmp_path / name
        path.write_bytes(bytes(range(256)) * 40)
        info, keyword = fh.quiet_header(str(path))
        assert info is None, name
        assert keyword("FILTER") is None, name


def test_read_header_refuses_an_unknown_extension(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("SIMPLE  =                    T\n")
    assert fh.read_header(str(path)) is None


def test_entry_for_calibrated_states(tmp_path: Path) -> None:
    write_fits(str(tmp_path / "a_c.fit"), [_plane()], header={"FILTER": "Ha"})
    write_fits(str(tmp_path / "a_c_2.fits"), [_plane()], header={"FILTER": "Ha"})
    hist = tmp_path / "h.fit"
    hist.write_bytes(_fits_bytes(_primary_cards()[:3] + [
        _card("NAXIS1", "4"), _card("NAXIS2", "4"), "HISTORY WBPP".ljust(80),
    ]))
    write_fits(str(tmp_path / "u.fit"), [_plane()])

    assert fh.entry_for(str(tmp_path / "a_c.fit"))["calibrated"] == "yes"
    assert fh.entry_for(str(tmp_path / "a_c_2.fits"))["calibrated"] == "yes"
    assert fh.entry_for(str(hist))["calibrated"] == "history"
    assert fh.entry_for(str(tmp_path / "u.fit"))["calibrated"] == "unknown"


def test_entry_for_has_the_js_keys_and_values(tmp_path: Path) -> None:
    path = str(tmp_path / "light.fit")
    write_fits(path, [_plane()], header={
        "FILTER": "OIII", "EXPTIME": 120.0, "XBINNING": 1, "YBINNING": 1,
        "IMAGETYP": "Light Frame", "DATE-OBS": "2026-10-14T22:00:00",
    })
    entry = fh.entry_for(path)
    assert set(entry) == {
        "path", "filter", "exposure", "binning", "binningY", "imageType", "time", "width", "height", "calibrated",
    }
    assert entry["path"] == path
    assert entry["filter"] == "OIII"
    assert entry["exposure"] == "120.0"
    assert entry["binning"] == "1"
    assert entry["binningY"] == "1"
    assert entry["imageType"] == "Light Frame"
    assert entry["time"] == "2026-10-14T22:00:00"
    assert (entry["width"], entry["height"]) == (64, 48)
    assert entry["calibrated"] == "unknown"


def test_entry_for_a_missing_file_has_zero_geometry_and_no_values(tmp_path: Path) -> None:
    entry = fh.entry_for(str(tmp_path / "gone.fit"))
    assert (entry["width"], entry["height"]) == (0, 0)
    assert entry["filter"] is None
    assert entry["time"] is None
    assert entry["calibrated"] == "unknown"


def test_frame_files_in_lists_only_frames_sorted(tmp_path: Path) -> None:
    folder = tmp_path / "lights"
    folder.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = str(elsewhere / "real.fits")
    write_fits(real, [_plane()])

    write_fits(str(folder / "b_c.FIT"), [_plane()])
    write_fits(str(folder / "d.fts"), [_plane()])
    _write_fz(folder / "a.fits.fz")
    _xisf(folder / "c.xisf", _xisf_xml())
    write_fits(str(folder / ".hidden.fits"), [_plane()])
    (folder / "notes.txt").write_text("not a frame")
    (folder / "sub.fits").mkdir()  # a directory whose name looks like a frame
    os.symlink(real, folder / "link.fits")

    assert fh.frame_files_in(str(folder)) == [
        f"{folder}/a.fits.fz",
        f"{folder}/b_c.FIT",
        f"{folder}/c.xisf",
        f"{folder}/d.fts",
        f"{folder}/link.fits",
    ]


def test_frame_files_in_a_missing_folder_is_empty(tmp_path: Path) -> None:
    assert fh.frame_files_in(str(tmp_path / "none")) == []


def test_frame_extensions_pattern() -> None:
    for name in ("a.xisf", "a.FITS", "a.fit", "a.fts", "a.fits.fz", "a.FIT.FZ"):
        assert fh.FRAME_EXTENSIONS.search(name), name
    for name in ("a.fz", "a.txt", "a.fits.gz", "afits"):
        assert not fh.FRAME_EXTENSIONS.search(name), name
