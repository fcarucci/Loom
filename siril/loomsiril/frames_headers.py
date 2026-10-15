"""Frame Selector: the header of each frame file and the fields the comparability check reads.

Ported from ``pixinsight/script``:

* ``FrameSelector.js`` 786-798 (``quietHeader``), 806-836 (``entryFor``), 928-934 (``frameFilesIn``);
* ``lib/Util.js`` 450-462 (``keywordValue``), 468-476 (``readHeader``), 1209-1222 (``findEntries``, its behaviour
  only: dot names are skipped and directories are not frames), and the header-only read of 1315-1380
  (``tryHeaderRead``, ``readImageInfo``), re-done here for the formats Siril reads: FITS (``.fit .fits .fts``),
  tile-compressed FITS (``.fz`` after one of those) and XISF (``.xisf``).

Only headers are read. FITS data and compressed tile blocks are skipped by seeking, and the XISF reader reads its
XML header and nothing after it: pixels are never read. An unreadable file, or one in a format not listed above,
gives no header rather than an error, so a scan of a folder never stops on one bad file.

Names are the JS names in snake_case. FITS keyword values are the raw text PixInsight hands to ``keywordValue``: a
string keeps its quotes (``'L       '``), a number is its text (``120.0``), a logical is ``T`` or ``F``. This holds for
FITS only: an XISF keyword's value has its quotes already removed by the XISF reader, and XISF HISTORY and COMMENT
text lives in the comment attribute, so ``calibrated`` is detected from FITS HISTORY cards only. A tile-compressed
file lists the extension's keywords before the primary's, so :func:`keyword_value`, which returns the first match,
gives the extension's value when both HDUs carry a keyword.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, Optional

from loomsiril.loom.xisf import read_xisf_header

FITS_BLOCK = 2880
FITS_CARD = 80
#: A header longer than this many 2880-byte blocks is not read (a file without an END card is not a FITS header).
MAX_HEADER_BLOCKS = 1024

#: Frame files: XISF, FITS (``.fit``, ``.fits``, ``.fts``), each optionally tile-compressed (``.fz``).
FRAME_EXTENSIONS = re.compile(r"\.(xisf|fits?|fts)(\.fz)?$", re.IGNORECASE)
#: A calibrated frame's name (WBPP writes ``_c`` and ``_c_<n>`` before the extension).
_CALIBRATED_NAME = re.compile(r"_c(_[0-9]+)?\.[a-z]+\Z", re.IGNORECASE)

Keyword = tuple[str, str]


@dataclass
class HeaderInfo:
    """A frame's header: ``keywords`` as ``(name, value text)`` in order, and the image geometry."""

    keywords: list[Keyword]
    width: int
    height: int
    channels: int


def keyword_value(keywords: Optional[list[Keyword]], name: str) -> Optional[str]:
    """The first keyword called ``name``, its value trimmed and unquoted; None when absent or blank (Util.keywordValue).

    ``'  x '`` gives ``x``; ``''`` and blanks give None.
    """
    if keywords is None:
        return None
    for kw_name, raw in keywords:
        if kw_name == name:
            v = raw.strip()
            if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
                v = v[1:-1]
            v = v.strip()
            return v if len(v) > 0 else None
    return None


def _value_text(raw: str) -> str:
    """The value of a card (columns 11-80): a quoted string with its quotes, else the text before any ``/`` comment."""
    s = raw.lstrip()
    if s.startswith("'"):
        i = 1
        while i < len(s):
            if s[i] == "'":
                if s[i + 1:i + 2] == "'":  # an escaped quote inside the string
                    i += 2
                    continue
                return s[:i + 1]
            i += 1
        return s  # unterminated: the rest of the card
    return s.split("/", 1)[0].strip()


def _read_header_cards(fh: BinaryIO) -> Optional[list[Keyword]]:
    """The cards of the header at the current position, up to END, as ``(name, value text)``; None if there is none."""
    cards: list[Keyword] = []
    for _ in range(MAX_HEADER_BLOCKS):
        block = fh.read(FITS_BLOCK)
        if len(block) < FITS_BLOCK:
            return None
        for c in range(0, FITS_BLOCK, FITS_CARD):
            text = block[c:c + FITS_CARD].decode("ascii", errors="replace")
            name = text[:8].rstrip()
            if name == "END":
                return cards
            if name in ("HISTORY", "COMMENT"):
                cards.append((name, text[8:].strip()))
            elif name and text[8:10] == "= ":
                cards.append((name, _value_text(text[10:])))
    return None


def _int_or_none(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _num(cards: list[Keyword], name: str, default: int) -> int:
    v = _int_or_none(keyword_value(cards, name))
    return default if v is None else v


def _data_bytes(cards: list[Keyword]) -> int:
    """Bytes of the data unit that follows these cards, padded to whole blocks (0 when NAXIS is 0)."""
    naxis = _num(cards, "NAXIS", 0)
    if naxis <= 0:
        return 0
    total = 1
    for i in range(1, naxis + 1):
        total *= _num(cards, f"NAXIS{i}", 0)
    bytes_ = abs(_num(cards, "BITPIX", 0)) // 8 * _num(cards, "GCOUNT", 1) * (_num(cards, "PCOUNT", 0) + total)
    return bytes_ + (-bytes_) % FITS_BLOCK


def _read_fits(path: str, compressed: bool) -> Optional[HeaderInfo]:
    """A FITS primary header; for ``.fz`` the BINTABLE extension that carries the tile-compressed image's geometry."""
    with open(path, "rb") as fh:
        primary = _read_header_cards(fh)
        if not primary or primary[0][0] != "SIMPLE":
            return None
        if not compressed:
            return HeaderInfo(
                keywords=primary,
                width=_num(primary, "NAXIS1", 0),
                height=_num(primary, "NAXIS2", 0),
                channels=_num(primary, "NAXIS3", 1),
            )
        fh.seek(_data_bytes(primary), os.SEEK_CUR)  # the primary carries no pixels; skip whatever data it declares
        ext = _read_header_cards(fh)
    if ext is None or keyword_value(ext, "XTENSION") != "BINTABLE" or keyword_value(ext, "ZIMAGE") != "T":
        return None
    return HeaderInfo(
        keywords=ext + primary,
        width=_num(ext, "ZNAXIS1", 0),
        height=_num(ext, "ZNAXIS2", 0),
        channels=_num(ext, "ZNAXIS3", 1),
    )


def _read_xisf(path: str) -> Optional[HeaderInfo]:
    """An XISF file's XML header: keywords in document order and the first Image's geometry.

    Without an Image geometry the keywords are still returned, with width, height and channels all 0.
    """
    header = read_xisf_header(path)
    keywords = [(name, value) for name, value, _comment in header.keywords]
    if header.geometry is None:
        return HeaderInfo(keywords=keywords, width=0, height=0, channels=0)
    width, height, channels = header.geometry
    return HeaderInfo(keywords=keywords, width=width, height=height, channels=channels)


def read_header(path: str) -> Optional[HeaderInfo]:
    """The header of the frame at ``path`` (Util.tryHeaderRead, header only); None when unreadable or not a frame format.

    The format is taken from the extension, as the frame list does. Nothing is decoded: see the module docstring.
    """
    match = FRAME_EXTENSIONS.search(os.path.basename(path))
    if match is None:
        return None
    ext, compressed = match.group(1).lower(), match.group(2) is not None
    try:
        if ext == "xisf":
            return None if compressed else _read_xisf(path)
        return _read_fits(path, compressed)
    except (OSError, ValueError):
        return None


def quiet_header(path: str) -> tuple[Optional[HeaderInfo], Callable[[str], Optional[str]]]:
    """``(info, keyword)``: the header of ``path`` and a lookup of one keyword in it. Never raises (FrameSelector.quietHeader).

    A missing file is not opened at all. ``keyword`` gives None for every name when there is no header.
    """
    try:
        info = read_header(path) if os.path.exists(path) else None
    except Exception:  # noqa: BLE001 -- a scan must go on past any one file
        info = None
    keywords = info.keywords if info is not None else None

    def keyword(name: str) -> Optional[str]:
        return keyword_value(keywords, name)

    return info, keyword


def entry_for(path: str) -> dict[str, object]:
    """The fields the comparability check reads for one frame (FrameSelector.entryFor), keyed as in the JS."""
    info, keyword = quiet_header(path)
    if _CALIBRATED_NAME.search(path):
        calibrated = "yes"
    else:
        calibrated = "history" if keyword("HISTORY") is not None else "unknown"
    return {
        "path": path,
        "filter": keyword("FILTER"),
        "exposure": keyword("EXPTIME"),
        "binning": keyword("XBINNING"),
        "binningY": keyword("YBINNING"),
        "imageType": keyword("IMAGETYP"),
        "time": keyword("DATE-OBS"),
        "width": info.width if info is not None else 0,
        "height": info.height if info is not None else 0,
        "calibrated": calibrated,
    }


def frame_files_in(folder: str) -> list[str]:
    """The frame files directly in ``folder`` as ``folder + "/" + name``, sorted by name (FrameSelector.frameFilesIn).

    Dot names and directories are left out; a symbolic link to a file counts. PixInsight lists in directory order;
    sorting by name is the deterministic equivalent. A folder that cannot be listed gives an empty list.
    """
    try:
        with os.scandir(folder) as entries:
            names = [
                e.name for e in entries
                if not e.name.startswith(".") and e.is_file() and FRAME_EXTENSIONS.search(e.name)
            ]
    except OSError:
        return []
    return [folder + "/" + name for name in sorted(names)]
