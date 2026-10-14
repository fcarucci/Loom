"""The XISF header reader (loomsiril/loom/xisf.py): XML header only, no pixels.

Every file is synthetic: an XISF preamble, an XML header written here, and optional pixel bytes that the reader must
never need.
"""

from __future__ import annotations

import struct
from pathlib import Path
from xml.sax.saxutils import escape

import warnings

import pytest

from loomsiril.loom import channels as ch
from loomsiril.loom import xisf

from . import synth

NS = 'xmlns="http://www.pixinsight.com/xisf"'

HISTORY_WITH_DRIZZLE = (
    '<?xml version="1.0"?><ProcessingHistory>'
    '<instance class="DrizzleIntegration" id="DrizzleIntegration1">'
    '<parameter id="scale" value="2"/></instance></ProcessingHistory>'
)


def _xisf_bytes(xml: str, pixels: bytes = b"") -> bytes:
    body = xml.encode("utf-8")
    return b"XISF0100" + struct.pack("<I", len(body)) + b"\x00" * 4 + body + pixels


def _header_xml(compression: str = "", namespaced: bool = True) -> str:
    ns = NS if namespaced else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xisf version="1.0" {ns}>
 <Image geometry="640:512:1" sampleFormat="Float32" colorSpace="Gray"
        location="attachment:4096:1310720"{compression}>
  <FITSKeyword name="FILTER" value="'Ha'" comment="filter wheel slot"/>
  <FITSKeyword name="INSTRUME" value="'ZWO ASI2600MM Pro'" comment=""/>
  <FITSKeyword name="XPIXSZ" value="1.88" comment="pixel size"/>
  <FITSKeyword name="IMAGETYP" value="'Master Light'" comment=""/>
  <Property id="PCL:Signature:Integration" type="String">process=ImageIntegration</Property>
  <Property id="PixInsight:ProcessingHistory" type="String" value="{escape(HISTORY_WITH_DRIZZLE, {'"': '&quot;'})}"/>
  <Property id="Observation:Object:Name" type="String">M 42</Property>
 </Image>
</xisf>
"""


def _write(tmp_path: Path, data: bytes, name: str = "master.xisf") -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_reads_keywords_properties_and_geometry(tmp_path: Path) -> None:
    hdr = xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(_header_xml())))
    assert hdr.geometry == (640, 512, 1)
    assert hdr.sample_format == "Float32"
    assert hdr.keywords == [
        ("FILTER", "Ha", "filter wheel slot"),
        ("INSTRUME", "ZWO ASI2600MM Pro", ""),
        ("XPIXSZ", "1.88", "pixel size"),
        ("IMAGETYP", "Master Light", ""),
    ]
    assert hdr.properties["PCL:Signature:Integration"] == "process=ImageIntegration"
    assert hdr.properties["Observation:Object:Name"] == "M 42"


def test_value_attribute_property_form_is_read(tmp_path: Path) -> None:
    hdr = xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(_header_xml())))
    assert hdr.properties["PixInsight:ProcessingHistory"] == HISTORY_WITH_DRIZZLE


def test_compressed_data_attribute_does_not_change_the_header(tmp_path: Path) -> None:
    plain = xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(_header_xml()), "a.xisf"))
    comp = xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(_header_xml(' compression="zlib:4096"'), b"\xff" * 16),
                                        "b.xisf"))
    assert comp == plain


def test_no_namespace_document_reads_the_same(tmp_path: Path) -> None:
    ns = xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(_header_xml()), "ns.xisf"))
    bare = xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(_header_xml(namespaced=False)), "bare.xisf"))
    assert bare == ns


def test_no_pixel_data_is_needed(tmp_path: Path) -> None:
    """The reader never looks past the header: a file with no pixel block at all still reads."""
    hdr = xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(_header_xml())))
    assert hdr.geometry == (640, 512, 1)


def test_bad_signature_is_refused(tmp_path: Path) -> None:
    data = b"XISF0200" + _xisf_bytes(_header_xml())[8:]
    with pytest.raises(ValueError, match="signature"):
        xisf.read_xisf_header(_write(tmp_path, data))


def test_short_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        xisf.read_xisf_header(_write(tmp_path, b"XISF0100"))


def test_header_length_past_end_of_file_is_refused(tmp_path: Path) -> None:
    data = b"XISF0100" + struct.pack("<I", 10_000) + b"\x00" * 4 + b"<xisf/>"
    with pytest.raises(ValueError, match="past the end"):
        xisf.read_xisf_header(_write(tmp_path, data))


def test_malformed_xml_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not readable XML"):
        xisf.read_xisf_header(_write(tmp_path, _xisf_bytes("<xisf><Image")))


def test_quoted_keyword_padding_is_stripped(tmp_path: Path) -> None:
    xml = _header_xml().replace("value=\"'Ha'\"", "value=\"'Ha      '\"")
    hdr = xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(xml)))
    assert hdr.keywords[0] == ("FILTER", "Ha", "filter wheel slot")


def test_read_header_maps_xisf_to_the_channel_rules(tmp_path: Path) -> None:
    info = ch.read_header(_write(tmp_path, _xisf_bytes(_header_xml())))
    assert info["format"] == "xisf"
    assert (info["width"], info["height"]) == (640, 512)
    assert ch.keyword_value(info["keywords"], "FILTER") == "Ha"
    assert ch.channel_from_filter(ch.keyword_value(info["keywords"], "FILTER")) == "H"
    assert ch.master_drizzle_label(info) == "2x"


def test_xisf_integration_signature_without_drizzle_is_not_drizzled(tmp_path: Path) -> None:
    xml = _header_xml().replace('<Property id="PixInsight:ProcessingHistory"', '<Property id="unused"')
    info = ch.read_header(_write(tmp_path, _xisf_bytes(xml)))
    assert info["signature"] == "process=ImageIntegration"
    assert ch.master_drizzle_label(info) == ""


def test_xisf_drizzle_scale_is_read_from_history(tmp_path: Path) -> None:
    info = ch.read_header(_write(tmp_path, _xisf_bytes(_header_xml())))
    assert ch.drizzle_scale_from_history(info["history"]) == 2


def test_unknown_xml_encoding_is_a_value_error_not_a_lookup_error(tmp_path: Path) -> None:
    xml = '<?xml version="1.0" encoding="no-such-codec"?><xisf version="1.0"/>'
    with pytest.raises(ValueError):
        xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(xml)))


def test_one_odd_file_does_not_abort_the_master_scan(tmp_path: Path) -> None:
    _write(tmp_path, _xisf_bytes('<?xml version="1.0" encoding="no-such-codec"?><xisf version="1.0"/>'),
           "masterLight_FILTER-Ha_odd.xisf")
    master = synth.make_master("H", w=48, h=40, filter_name="Ha", catalogue=synth.sky_catalogue(n_stars=12))
    synth.write_master(tmp_path / "masterLight_FILTER-Ha_good.fits", master)
    scan = ch.scan_masters(str(tmp_path))
    assert scan is not None
    assert scan["confirmed"]["H"]["name"] == "masterLight_FILTER-Ha_good.fits"


def test_dtd_and_entity_declarations_are_refused(tmp_path: Path) -> None:
    xml = ('<?xml version="1.0"?><!DOCTYPE xisf [<!ENTITY e "boom">]>'
           '<xisf version="1.0"><Image geometry="2:2:1"/></xisf>')
    with pytest.raises(ValueError, match="DTD"):
        xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(xml)))


def test_declared_header_over_the_cap_is_refused_before_reading(tmp_path: Path) -> None:
    data = b"XISF0100" + struct.pack("<I", xisf.MAX_HEADER_BYTES + 1) + b"\x00" * 4
    with pytest.raises(ValueError, match="cap"):
        xisf.read_xisf_header(_write(tmp_path, data))


def test_two_part_geometry_defaults_to_one_channel(tmp_path: Path) -> None:
    xml = _header_xml().replace('geometry="640:512:1"', 'geometry="640:512"')
    assert xisf.read_xisf_header(_write(tmp_path, _xisf_bytes(xml))).geometry == (640, 512, 1)


def test_out_of_line_property_is_noted_without_a_warning(tmp_path: Path) -> None:
    xml = ('<?xml version="1.0"?><xisf version="1.0"><Image geometry="2:2:1">'
           '<Property id="PixInsight:ProcessingHistory" type="String" location="attachment:4096:2000"/>'
           '</Image></xisf>')
    path = _write(tmp_path, _xisf_bytes(xml))
    with warnings.catch_warnings():
        warnings.simplefilter("error")   # any warning fails the test: Siril logs each one
        hdr = xisf.read_xisf_header(path)
        assert ch.read_header(path)["history"] is None
    assert "PixInsight:ProcessingHistory" not in hdr.properties
    assert any("PixInsight:ProcessingHistory" in n for n in hdr.notes)
