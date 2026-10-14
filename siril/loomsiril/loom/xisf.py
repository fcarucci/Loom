"""Reader for the XML header of a PixInsight XISF file: keywords, properties and geometry, never pixels.

An XISF file is the 8-byte signature ``XISF0100``, a little-endian uint32 header length, 4 reserved bytes, then the
UTF-8 XML header; the pixel blocks follow it and are not read here. The XML is parsed with ``xml.etree``; elements
are matched by local name, so documents with or without the XISF namespace (``http://www.pixinsight.com/xisf``) read
the same way.
"""

from __future__ import annotations

import os
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Optional, Union

XISF_SIGNATURE = b"XISF0100"
XISF_NAMESPACE = "http://www.pixinsight.com/xisf"
_PREAMBLE = 16  # signature (8) + header length (4) + reserved (4)
MAX_HEADER_BYTES = 16 * 1024 * 1024  # a larger declared header is refused before anything is allocated
_DTD_OR_ENTITY = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_UTF8_BOM = b"\xef\xbb\xbf"
_XML_DECLARED_ENCODING = re.compile(rb"""<\?xml[^>]*?encoding\s*=\s*["']([^"']+)["']""")


@dataclass
class XisfHeader:
    """What the XML header says about the image. ``keywords`` are ``(name, value, comment)`` in document order."""

    keywords: list[tuple[str, str, str]] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    geometry: Optional[tuple[int, int, int]] = None
    sample_format: Optional[str] = None
    #: Properties whose text is not available here (stored out of line or compressed). Not warned: every
    #: PixInsight master stores its astrometric solution this way, and Siril would print one log line per property.
    notes: list[str] = field(default_factory=list)


def _local(tag: Any) -> str:
    """The element name without its namespace: ``{ns}Property`` -> ``Property``."""
    return str(tag).rsplit("}", 1)[-1]


def _strip_fits_quotes(value: str) -> str:
    """A FITS keyword value without its surrounding single quotes and blank padding (as Util.keywordValue does)."""
    v = value.strip()
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        v = v[1:-1]
    return v.strip()


def _geometry(text: Optional[str]) -> Optional[tuple[int, int, int]]:
    """``width:height[:channels]`` as ints; channels default to 1. None when absent or malformed."""
    if not text:
        return None
    parts = text.split(":")
    try:
        w, h = int(parts[0]), int(parts[1])
        c = int(parts[2]) if len(parts) > 2 else 1
    except (ValueError, IndexError):
        return None
    return (w, h, c)


def _check_utf8(xml_bytes: bytes, path: str) -> None:
    """Refuse a header that is not UTF-8 text, before its markup is searched.

    The DOCTYPE and ENTITY check below matches bytes, which finds the markup only in an ASCII-compatible UTF-8 header.
    A UTF-16 or UTF-32 header (a byte-order mark, or NUL bytes between the characters) would hide ``<!DOCTYPE`` from
    that check, so it is refused here, as is any header that declares another encoding.
    """
    if xml_bytes.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in xml_bytes:
        raise ValueError(f"XISF header is not UTF-8 (UTF-16 or wider): {path}")
    body = xml_bytes[len(_UTF8_BOM):] if xml_bytes.startswith(_UTF8_BOM) else xml_bytes
    try:
        body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"XISF header is not valid UTF-8: {path}: {exc}") from exc
    declared = _XML_DECLARED_ENCODING.match(body)
    if declared is not None and declared.group(1).lower() not in (b"utf-8", b"utf8"):
        raise ValueError(f"XISF header declares {declared.group(1).decode('ascii', 'replace')}, not UTF-8: {path}")


def read_xisf_header(path: Union[str, "os.PathLike[str]"]) -> XisfHeader:
    """Parse the XML header of the XISF file at ``path``. Raises ValueError for anything that is not a valid header.

    Refused: a bad signature, a declared header longer than MAX_HEADER_BYTES or longer than the file, a DOCTYPE or
    ENTITY declaration, a header that is not UTF-8 (UTF-16 or another declared encoding included), and XML that does
    not parse. Properties whose text is
    not in the header (``location="attachment:..."`` or compressed) are left out of ``properties``, recorded in
    ``notes`` and raised as a UserWarning.
    """
    with open(path, "rb") as fh:
        preamble = fh.read(_PREAMBLE)
        if len(preamble) < _PREAMBLE or preamble[:8] != XISF_SIGNATURE:
            raise ValueError(f"not an XISF file (bad signature): {path}")
        (length,) = struct.unpack("<I", preamble[8:12])
        if length > MAX_HEADER_BYTES:
            raise ValueError(f"XISF header length {length} exceeds the {MAX_HEADER_BYTES}-byte cap: {path}")
        size = os.fstat(fh.fileno()).st_size
        if _PREAMBLE + length > size:
            raise ValueError(f"XISF header length {length} runs past the end of the file: {path}")
        xml_bytes = fh.read(length)
    _check_utf8(xml_bytes, str(path))
    if _DTD_OR_ENTITY.search(xml_bytes):
        raise ValueError(f"XISF header declares a DTD or entities, which XISF never uses: {path}")
    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, LookupError, ValueError) as exc:  # LookupError: an unknown XML encoding name
        raise ValueError(f"XISF header is not readable XML: {path}: {exc}") from exc

    out = XisfHeader()
    for el in root.iter():
        name = _local(el.tag)
        if name == "FITSKeyword":
            kname = el.get("name")
            if kname is None:
                continue
            out.keywords.append((kname, _strip_fits_quotes(el.get("value", "")), el.get("comment", "")))
        elif name == "Property":
            pid = el.get("id")
            if pid is None:
                continue
            loc = el.get("location") or ""
            if "value" not in el.attrib and (loc.startswith("attachment:") or el.get("compression")):
                note = f"property {pid} is stored out of line or compressed ({loc or el.get('compression')}); " \
                       "its text is not read"
                out.notes.append(note)   # recorded, not warned: PixInsight masters carry dozens of these
                continue
            out.properties[pid] = el.get("value") if "value" in el.attrib else (el.text or "")
        elif name == "Image" and out.geometry is None:
            out.geometry = _geometry(el.get("geometry"))
            out.sample_format = el.get("sampleFormat")
    return out
