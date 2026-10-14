"""Minimal FITS reader and writer (primary HDU only; no astropy).

Reads BITPIX 8, 16, 32, -32, -64 with NAXIS 2 or 3 (NAXIS3 = channels), applying BZERO and BSCALE. Integer data
is scaled to [0, 1] by (2^bits - 1) after BZERO; float data is returned as it is. Writes -32 float, -64 float or
16-bit unsigned (BZERO 32768). Blocks are 2880 bytes and cards 80 characters.

Row order: FITS stores the first row at the BOTTOM of the image as displayed. Everything inside loomsiril is
TOP-DOWN (array row 0 = the top of the displayed image, as in the JS, TIFF, StarNet and ffmpeg), so the planes are
flipped ONCE here: read_fits returns top-down planes and write_fits writes bottom-up ones. The TAN keywords are
converted the same way (wcs.flip_rows: CRPIX2 -> h + 1 - CRPIX2, CD1_2 and CD2_2 negated). A PC/CDELT/CROTA2 header
is first written out as CD keywords (CDi_j = CDELTi * PCi_j), since the flip is a sign change on CD. The header
read_fits returns carries ``LOOMROWS = "top-down"``; write_fits drops that key.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from loomsiril.wcs import keyword_number

__all__ = ["read_fits", "write_fits", "materialise_cd"]

BLOCK = 2880
CARD = 80
_DTYPES: dict[int, str] = {8: ">u1", 16: ">i2", 32: ">i4", -32: ">f4", -64: ">f8"}
_RESERVED = {"SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3", "BZERO", "BSCALE", "END", "EXTEND"}
_INT = re.compile(r"[+-]?\d+")
_CD_KEYS = ("CD1_1", "CD1_2", "CD2_1", "CD2_2")
_PC_KEY = re.compile(r"PC\d_\d")
_CDELT_KEY = re.compile(r"CDELT\d")
LOOMROWS = "LOOMROWS"


def _parse_value(raw: str) -> Any:
    """A FITS header value field (columns 11-80): quoted string, logical T/F, integer, or float (D exponents allowed)."""
    s = raw.lstrip()
    if s.startswith("'"):
        return _parse_string(s)
    body = s.split("/", 1)[0].strip()
    if body == "":
        return None
    return _parse_scalar(body)


def _parse_string(s: str) -> str:
    """A quoted FITS string (``s`` starts at the opening quote): two quotes inside are one, trailing blanks go."""
    out: list[str] = []
    i = 1
    while i < len(s):
        ch = s[i]
        if ch == "'":
            if i + 1 < len(s) and s[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            break
        out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _parse_scalar(body: str) -> Any:
    """An unquoted value without its comment: T or F, an integer, else a float (see _parse_float)."""
    if body == "T":
        return True
    if body == "F":
        return False
    if _INT.fullmatch(body):
        return int(body)
    return _parse_float(body)


def _parse_float(body: str) -> float | str:
    """A float written with a D or d exponent too; text that is not a number is returned as it is."""
    try:
        return float(body.replace("D", "E").replace("d", "e"))
    except ValueError:
        return body


def _parse_header(data: bytes) -> tuple[dict[str, Any], int]:
    """The primary header as a dict and the byte offset of the data (a whole number of 2880-byte blocks)."""
    header: dict[str, Any] = {}
    pos = 0
    while True:
        block = data[pos:pos + BLOCK]
        if len(block) < BLOCK:
            raise ValueError("FITS header has no END card")
        pos += BLOCK
        for c in range(0, BLOCK, CARD):
            card = block[c:c + CARD].decode("ascii", errors="replace")
            key = card[:8].rstrip()
            if key == "END":
                return header, pos
            if not key or key in ("COMMENT", "HISTORY") or card[8:10] != "= ":
                continue
            header[key] = _parse_value(card[10:])


def materialise_cd(out: dict[str, Any]) -> dict[str, Any]:
    """A PC/CDELT/CROTA2 WCS as explicit CD keywords (FITS: CDi_j = CDELTi * PCi_j); the PC/CDELT/CROTA2 keys go.

    CROTA2 is used only when the header has no PCi_j key (PC wins). Then CD1_1 = CDELT1*cos(r), CD1_2 = -CDELT2*sin(r),
    CD2_1 = CDELT1*sin(r), CD2_2 = CDELT2*cos(r) with r = CROTA2 in radians (the AIPS convention). A missing CDELT is 1.
    """
    def num(k: str, default: float) -> float:
        v = keyword_number(out, k)
        return default if v is None else v

    cdelt1, cdelt2 = num("CDELT1", 1.0), num("CDELT2", 1.0)
    if not any(_PC_KEY.fullmatch(k) for k in out) and "CROTA2" in out:
        rot = math.radians(num("CROTA2", 0.0))
        cd = [cdelt1 * math.cos(rot), -cdelt2 * math.sin(rot), cdelt1 * math.sin(rot), cdelt2 * math.cos(rot)]
    else:
        pc = [num("PC1_1", 1.0), num("PC1_2", 0.0), num("PC2_1", 0.0), num("PC2_2", 1.0)]
        cd = [cdelt1 * pc[0], cdelt1 * pc[1], cdelt2 * pc[2], cdelt2 * pc[3]]
    kept = {k: v for k, v in out.items() if not (_PC_KEY.fullmatch(k) or _CDELT_KEY.fullmatch(k) or k == "CROTA2")}
    for k, v in zip(_CD_KEYS, cd):
        kept[k] = v
    return kept


def _flip_header(header: Mapping[str, Any], h: int) -> dict[str, Any]:
    """The header's TAN keywords on the other row order (wcs.flip_rows for the keywords; an involution).

    Without any CD/PC/CDELT/CROTA2 keyword there is no WCS to convert and the header is returned as it is.
    ``LOOMROWS`` is not a FITS keyword: it is dropped here.
    """
    out = {k: v for k, v in header.items() if k != LOOMROWS}
    if not any(k in out for k in _CD_KEYS):
        if not any(_PC_KEY.fullmatch(k) or _CDELT_KEY.fullmatch(k) or k == "CROTA2" for k in out):
            return out
        out = materialise_cd(out)
    crpix2 = keyword_number(out, "CRPIX2")
    if crpix2 is not None:
        out["CRPIX2"] = h + 1 - crpix2
    for k in ("CD1_2", "CD2_2"):
        v = keyword_number(out, k)
        if v is not None:
            out[k] = -v
    return out


def read_fits(path: str | os.PathLike[str]) -> tuple[list[np.ndarray], dict[str, Any]]:
    """Read the primary HDU: ``(planes, header)``, top-down.

    ``planes`` is a list of float32 arrays shaped (NAXIS2, NAXIS1), one per channel, flipped so that array row 0 is
    the top of the displayed image (FITS row NAXIS2). ``header`` maps keyword -> str, int, float or bool (T/F ->
    True/False); its TAN keywords are converted to the top-down rows and ``LOOMROWS`` is set to ``"top-down"``.
    """
    data = Path(path).read_bytes()
    header, offset = _parse_header(data)
    bitpix = int(header.get("BITPIX", 0))
    naxis = int(header.get("NAXIS", 0))
    if bitpix not in _DTYPES:
        raise ValueError(f"{path}: unsupported BITPIX {bitpix}")
    if naxis not in (2, 3):
        raise ValueError(f"{path}: NAXIS {naxis} not supported (2 or 3)")
    nx, ny = int(header["NAXIS1"]), int(header["NAXIS2"])
    nz = int(header["NAXIS3"]) if naxis == 3 else 1
    count = nx * ny * nz
    need = count * abs(bitpix) // 8
    if len(data) < offset + need:
        raise ValueError(f"{path}: data truncated ({len(data) - offset} of {need} bytes)")
    raw = np.frombuffer(data, dtype=_DTYPES[bitpix], count=count, offset=offset).reshape(nz, ny, nx)
    vals = raw.astype(np.float64)
    bzero = float(header.get("BZERO", 0))
    bscale = float(header.get("BSCALE", 1))
    if bzero != 0 or bscale != 1:
        vals = vals * bscale + bzero
    if bitpix > 0:  # integer data: scale to [0, 1]
        vals = vals / float(2 ** bitpix - 1)
    planes = [np.ascontiguousarray(vals[c][::-1], dtype=np.float32) for c in range(nz)]  # FITS bottom-up -> top-down
    header = _flip_header(header, ny)
    header[LOOMROWS] = "top-down"
    return planes, header


def _card(key: str, value: Any) -> bytes:
    """One 80-character header card: ``KEY     = value`` with the FITS column rules for numbers and strings."""
    if len(key) > 8:
        raise ValueError(f"FITS keyword longer than 8 characters: {key!r}")
    if isinstance(value, bool):
        v = ("T" if value else "F").rjust(20)
    elif isinstance(value, int):
        v = str(value).rjust(20)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"FITS value for {key} is not finite")
        v = repr(float(value)).upper().replace("E+", "E").rjust(20)
    else:
        text = str(value)
        if not text.isascii():
            raise ValueError(f"FITS string value for {key} must be ASCII")
        v = ("'" + text.replace("'", "''").ljust(8) + "'").ljust(20)
    line = f"{key:<8}= {v}".ljust(CARD)
    if len(line) > CARD:
        raise ValueError(f"FITS card for {key} is longer than 80 characters")
    return line.encode("ascii")


def write_fits(
    path: str | os.PathLike[str],
    planes: Sequence[np.ndarray],
    header: Mapping[str, Any] | None = None,
    bitpix: int = -32,
) -> None:
    """Write planes (each (h, w), all the same shape, top-down as loomsiril holds them) as a primary HDU.

    The planes and the header's TAN keywords are written bottom-up, as FITS and Siril expect.

    ``bitpix`` -32 or -64 stores the float values as they are; 16 stores values clipped to [0, 1] scaled to
    0..65535 with rounding, unsigned via BZERO 32768. One plane gives NAXIS 2; several give NAXIS 3.
    ``header`` adds extra keywords (structural keywords are written here and skipped if given).
    """
    if not planes:
        raise ValueError("write_fits needs at least one plane")
    shape = np.asarray(planes[0]).shape
    if len(shape) != 2 or any(np.asarray(p).shape != shape for p in planes):
        raise ValueError("write_fits: every plane must be 2-D with the same shape")
    if bitpix not in (-32, -64, 16):
        raise ValueError(f"write_fits: bitpix {bitpix} not supported (-32, -64 or 16)")
    ny, nx = shape
    cards = _structural_cards(bitpix, len(planes), nx, ny) + _header_cards(header or {}, ny)
    Path(path).write_bytes(_header_block(cards) + _data_block(planes, bitpix))


def _structural_cards(bitpix: int, nz: int, nx: int, ny: int) -> list[bytes]:
    """SIMPLE, BITPIX, NAXIS and its axes, and BZERO/BSCALE for 16-bit unsigned data."""
    cards = [_card("SIMPLE", True), _card("BITPIX", bitpix), _card("NAXIS", 2 if nz == 1 else 3),
             _card("NAXIS1", nx), _card("NAXIS2", ny)]
    if nz > 1:
        cards.append(_card("NAXIS3", nz))
    if bitpix == 16:
        cards += [_card("BZERO", 32768), _card("BSCALE", 1)]
    return cards


def _header_cards(header: Mapping[str, Any], ny: int) -> list[bytes]:
    """The caller's keywords on the bottom-up rows (TAN converted), without the structural ones."""
    return [
        _card(k, v)
        for k, v in _flip_header(header, ny).items()
        if not (k in _RESERVED or k.startswith("NAXIS"))
    ]


def _header_block(cards: Sequence[bytes]) -> bytes:
    """The cards, the END card, and blanks up to a whole 2880-byte block."""
    head = b"".join(cards) + f"{'END':<{CARD}}".encode("ascii")
    return head + b" " * (-len(head) % BLOCK)


def _data_block(planes: Sequence[np.ndarray], bitpix: int) -> bytes:
    """The planes as big-endian data, bottom-up, padded with zeros to a whole block."""
    stack = np.stack([np.asarray(p, dtype=np.float64)[::-1] for p in planes])  # top-down -> FITS bottom-up
    if bitpix == 16:
        q = np.floor(np.clip(np.nan_to_num(stack, nan=0.0), 0.0, 1.0) * 65535 + 0.5).astype(np.int64)
        body = (q - 32768).astype(">i2").tobytes()
    elif bitpix == -32:
        body = stack.astype(">f4").tobytes()
    else:
        body = stack.astype(">f8").tobytes()
    return body + b"\0" * (-len(body) % BLOCK)
