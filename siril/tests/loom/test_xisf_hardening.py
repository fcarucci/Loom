"""The XISF header reader refuses a DOCTYPE or ENTITY declaration whatever the encoding of the header.

The DOCTYPE and ENTITY check matches bytes, so a header in UTF-16 (where every character is followed by a NUL byte)
would slip past it. These tests build the headers byte by byte and check that they are refused before parsing.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from loomsiril.loom import xisf

DOCTYPE_XML = ('<?xml version="1.0" encoding="{enc}"?><!DOCTYPE xisf [<!ENTITY e "boom">]>'
               '<xisf version="1.0"><Image geometry="2:2:1"/></xisf>')


def _file(tmp_path: Path, header: bytes, name: str = "master.xisf") -> str:
    data = b"XISF0100" + struct.pack("<I", len(header)) + b"\x00" * 4 + header
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def test_utf16_le_header_with_doctype_is_refused(tmp_path: Path) -> None:
    body = DOCTYPE_XML.format(enc="UTF-16").encode("utf-16-le")
    with pytest.raises(ValueError, match="not UTF-8"):
        xisf.read_xisf_header(_file(tmp_path, body))


def test_utf16_be_header_with_byte_order_mark_and_doctype_is_refused(tmp_path: Path) -> None:
    body = DOCTYPE_XML.format(enc="UTF-16").encode("utf-16")  # with a big- or little-endian byte-order mark
    with pytest.raises(ValueError, match="not UTF-8"):
        xisf.read_xisf_header(_file(tmp_path, body))


def test_utf16_header_without_declaration_and_with_doctype_is_refused(tmp_path: Path) -> None:
    body = DOCTYPE_XML.format(enc="UTF-16").split("?>", 1)[1].encode("utf-16-le")
    with pytest.raises(ValueError):
        xisf.read_xisf_header(_file(tmp_path, body))


def test_utf8_header_with_doctype_is_still_refused(tmp_path: Path) -> None:
    body = DOCTYPE_XML.format(enc="UTF-8").encode("utf-8")
    with pytest.raises(ValueError, match="DTD"):
        xisf.read_xisf_header(_file(tmp_path, body))


def test_utf8_bom_header_with_doctype_is_still_refused(tmp_path: Path) -> None:
    body = b"\xef\xbb\xbf" + DOCTYPE_XML.format(enc="UTF-8").encode("utf-8")
    with pytest.raises(ValueError, match="DTD"):
        xisf.read_xisf_header(_file(tmp_path, body))


def test_header_declaring_another_encoding_is_refused(tmp_path: Path) -> None:
    body = '<?xml version="1.0" encoding="ISO-8859-1"?><xisf version="1.0"/>'.encode("latin-1")
    with pytest.raises(ValueError, match="not UTF-8"):
        xisf.read_xisf_header(_file(tmp_path, body))


def test_invalid_utf8_bytes_are_refused(tmp_path: Path) -> None:
    body = b'<?xml version="1.0" encoding="UTF-8"?><xisf version="1.0">\xff\xfe</xisf>'
    with pytest.raises(ValueError, match="not valid UTF-8"):
        xisf.read_xisf_header(_file(tmp_path, body))


def test_plain_utf8_header_still_reads(tmp_path: Path) -> None:
    body = ('<?xml version="1.0" encoding="UTF-8"?><xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">'
            '<Image geometry="640:512:1" sampleFormat="Float32"/></xisf>').encode("utf-8")
    header = xisf.read_xisf_header(_file(tmp_path, body))
    assert header.geometry == (640, 512, 1)
