"""Colour management for Fly-Through: ICC parsing, tone curves, the SDR and HDR output transforms.

Port of Fly.js 1334-1623 (Fly.matMul ... Fly.hdrTransform), Sky.colourOf
(Sky.js 1282-1300) and Render.srgbIcc (Render.js 1352-1357). The scalar
functions keep the JS arithmetic; the per-pixel work of the output transforms
is vectorised with numpy (OutputTransform), and the JS lookup tables become
np.interp over the same sample points (lut_of, decode_lut).
"""

from __future__ import annotations

import math
import struct
from typing import Any, Callable, Optional, Sequence

import numpy as np

from loomsiril.core import clamp, js_round

# ---- constants -----------------------------------------------------------------

# Bradford adaptation D50 -> D65 (ICC profiles' PCS is D50; video is D65).
BRADFORD_D50_D65 = [0.9555766, -0.0230393, 0.0631636, -0.0282895, 1.0099416, 0.0210077,
                    0.0122982, -0.0204830, 1.3299098]
XYZ_TO_RGB = {
    "rec709": [3.2404542, -1.5371385, -0.4985314, -0.9692660, 1.8760108, 0.0415560,
               0.0556434, -0.2040259, 1.0572252],
    "rec2020": [1.7166512, -0.3556708, -0.2533663, -0.6666844, 1.6164812, 0.0157685,
                0.0176399, -0.0427706, 0.9421031],
}

# The sRGB profile (D50-adapted primaries, IEC 61966-2-1 curve): the default.
SRGB_PROFILE_NAME = "sRGB IEC61966-2.1"
SRGB_CURVE: dict[str, Any] = {"type": "para", "fn": 3, "p": [2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045]}
SRGB_COLOUR: dict[str, Any] = {
    "matrix": [0.4360747, 0.3850649, 0.1430804, 0.2225045, 0.7168786, 0.0606169,
               0.0139322, 0.0971045, 0.7141733],
    "trc": [SRGB_CURVE, SRGB_CURVE, SRGB_CURVE],
    "name": "sRGB",
}

SDR_WHITE_NITS = 203            # ITU-R BT.2408 reference white
HDR_PEAK_DEFAULT = 1000
HLG_REFERENCE_PEAK = 1000       # HLG is relative to a 1000-nit display, system gamma 1.2
HDR_DEFAULT_TRANSFER = {"social_vertical": "hlg", "social_square": "hlg", "youtube_4k": "hlg",
                        "youtube_1080": "hlg", "exhibition": "pq"}
DECODE_LUT = 65536              # samples in a lookup table over [0, 1]

_NAN = float("nan")
_INF = float("inf")
_IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


# ---- JS arithmetic helpers -------------------------------------------------------

def _pow(base: float, exp: float) -> float:
    """JS Math.pow: never raises; a negative base to a fractional power is NaN, 0 to a negative power Infinity."""
    try:
        return math.pow(base, exp)
    except ValueError:
        return _INF if base == 0 else _NAN
    except OverflowError:
        return _INF


def _div(num: float, den: float) -> float:
    """JS division: a zero denominator gives +-Infinity (NaN for 0/0), not an exception."""
    if den == 0:
        if num == 0 or num != num:
            return _NAN
        return math.copysign(_INF, num) * math.copysign(1.0, den)
    return num / den


def _exp(x: float) -> float:
    """JS Math.exp: overflow gives Infinity."""
    try:
        return math.exp(x)
    except OverflowError:
        return _INF


def _js_or(value: Any, default: Any) -> Any:
    """JS ``value || default``: 0, NaN, null, undefined and "" all fall back to the default."""
    if value is None or value == "" or value is False:
        return default
    if isinstance(value, (int, float)) and (value == 0 or value != value):
        return default
    return value


# ---- matrices and curves -------------------------------------------------------------

def mat_mul(a: Sequence[float], b: Sequence[float]) -> list[float]:
    """Fly.matMul: row-major 3x3 times 3x3."""
    c: list[float] = []
    for r in range(3):
        for col in range(3):
            c.append(a[3 * r] * b[col] + a[3 * r + 1] * b[3 + col] + a[3 * r + 2] * b[6 + col])
    return c


def table_curve(t: Sequence[float], x: float) -> float:
    """Fly.tableCurve: a tone-curve table's value at x in [0,1], linearly interpolated.

    A NaN or infinite x gives NaN here (the JS would index past the table); trc_decode clamps first.
    """
    n = len(t)
    if x != x or x in (_INF, -_INF):
        return _NAN
    f = x * (n - 1)
    i = min(n - 2, math.floor(f))
    if i < 0:
        return _NAN         # JS: t[-1] is undefined
    return t[i] + (f - i) * (t[i + 1] - t[i])


def para_curve(fn: int, p: Sequence[float], x: float) -> float:
    """Fly.paraCurve: a parametric tone curve (ICC types 0-4) at x; x itself for an unknown type."""
    pp = list(p) + [_NAN] * (7 - len(p))       # a missing JS parameter (undefined) is NaN in the arithmetic
    g, a, b, cc, d, e, ff = pp[0], pp[1], pp[2], pp[3], pp[4], pp[5], pp[6]
    if fn == 0:
        return _pow(x, g)
    if fn == 1:
        return _pow(a * x + b, g) if x >= _div(-b, a) else 0.0
    if fn == 2:
        return _pow(a * x + b, g) + cc if x >= _div(-b, a) else cc
    if fn == 3:
        return _pow(a * x + b, g) if x >= d else cc * x
    if fn == 4:
        return _pow(a * x + b, g) + e if x >= d else cc * x + ff
    return x


def trc_decode(c: dict[str, Any], x: float) -> float:
    """Fly.trcDecode: an ICC tone curve at x in [0,1] (identity, gamma, table or parametric)."""
    x = clamp(x, 0.0, 1.0)
    kind = c.get("type")
    if kind == "gamma":
        return _pow(x, c["g"])
    if kind == "table":
        return table_curve(c["t"], x)
    if kind != "para":
        return x
    return para_curve(c["fn"], c["p"], x)


# ---- ICC parsing -------------------------------------------------------------------

def _byte(data: bytes, i: int) -> int:
    """One byte of an ICC profile; past either end reads 0 (the JS reads undefined, which shifts as 0)."""
    return data[i] if 0 <= i < len(data) else 0


def _u32(data: bytes, o: int) -> int:
    return (_byte(data, o) << 24) + (_byte(data, o + 1) << 16) + (_byte(data, o + 2) << 8) + _byte(data, o + 3)


def _u16(data: bytes, o: int) -> int:
    return (_byte(data, o) << 8) + _byte(data, o + 1)


def _s15(data: bytes, o: int) -> float:
    v = _u32(data, o)
    return (v - 0x100000000 if v > 0x7FFFFFFF else v) / 65536


def _str4(data: bytes, o: int) -> str:
    return "".join(chr(_byte(data, o + k)) for k in range(4))


_PARA_PARAMETERS = [1, 3, 4, 5, 7]     # parameter count of ICC parametric types 0-4


def read_curve(data: bytes, off: int) -> Optional[dict[str, Any]]:
    """Fly.readCurve: one 'curv' or 'para' tone-curve tag at off, or None.

    A 'curv' table that would run past the end of the profile is refused (None) rather than read as zeros:
    a corrupt count must not turn into a multi-gigabyte loop.
    """
    kind = _str4(data, off)
    if kind == "curv":
        n = _u32(data, off + 8)
        if n == 0:
            return {"type": "identity"}
        if n == 1:
            return {"type": "gamma", "g": _u16(data, off + 12) / 256}
        if off + 12 + 2 * n > len(data):
            return None
        return {"type": "table", "t": [_u16(data, off + 12 + 2 * i) / 65535 for i in range(n)]}
    if kind == "para":
        fn = _u16(data, off + 8)
        if fn >= len(_PARA_PARAMETERS):
            return None
        p = [_s15(data, off + 12 + 4 * k) for k in range(_PARA_PARAMETERS[fn])]
        return {"type": "para", "fn": fn, "p": p}
    return None


def parse_icc_colour(data: bytes) -> Optional[dict[str, Any]]:
    """Fly.parseIccColour: { matrix (RGB -> XYZ D50, row-major), trc: [r, g, b], gray: False } from an ICC
    profile's rXYZ/gXYZ/bXYZ and rTRC/gTRC/bTRC tags; a gray profile's kTRC gives
    { gray: True, trc: [k, k, k], matrix: None }. None for LUT-only or unreadable profiles.
    """
    length = len(data)
    if length < 132:
        return None
    tags: dict[str, int] = {}
    n = _u32(data, 128)
    for i in range(n):
        if not 144 + 12 * i <= length:
            break
        at = 132 + 12 * i
        tags[_str4(data, at)] = _u32(data, at + 4)
    if "kTRC" in tags:
        k = read_curve(data, tags["kTRC"])
        return {"gray": True, "trc": [k, k, k], "matrix": None} if k is not None else None
    need = ["rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC"]
    if any(t not in tags or tags[t] + 12 > length for t in need):
        return None
    cols = [[_s15(data, tags[t] + 8), _s15(data, tags[t] + 12), _s15(data, tags[t] + 16)]
            for t in ("rXYZ", "gXYZ", "bXYZ")]
    trc = [read_curve(data, tags[t]) for t in ("rTRC", "gTRC", "bTRC")]
    if any(c is None for c in trc):
        return None
    matrix = [cols[0][0], cols[1][0], cols[2][0], cols[0][1], cols[1][1], cols[2][1], cols[0][2], cols[1][2], cols[2][2]]
    return {"matrix": matrix, "trc": trc, "gray": False}


def source_to_target(matrix: Sequence[float], target: str) -> list[float]:
    """Fly.sourceToTarget: source linear RGB -> target linear RGB ("rec709" | "rec2020")."""
    return mat_mul(XYZ_TO_RGB[target], mat_mul(BRADFORD_D50_D65, matrix))


# ---- transfer functions ------------------------------------------------------------

def srgb_encode(lin: float) -> float:
    """Fly.srgbEncode: IEC 61966-2-1 OETF, linear [0,1] -> sRGB signal."""
    lin = clamp(lin, 0.0, 1.0)
    return 12.92 * lin if lin <= 0.0031308 else 1.055 * _pow(lin, 1 / 2.4) - 0.055


def pq_encode(nits: float) -> float:
    """Fly.pqEncode: SMPTE ST 2084 (PQ) inverse EOTF, absolute nits -> signal."""
    m1 = 2610 / 16384
    m2 = 2523 / 4096 * 128
    c1 = 3424 / 4096
    c2 = 2413 / 4096 * 32
    c3 = 2392 / 4096 * 32
    lin = _pow(clamp(nits, 0.0, 10000.0) / 10000, m1)
    return _pow((c1 + c2 * lin) / (1 + c3 * lin), m2)


def hlg_encode(e: float) -> float:
    """Fly.hlgEncode: BT.2100 HLG OETF, scene-linear [0,1] -> signal."""
    a = 0.17883277
    b = 1 - 4 * a
    c = 0.5 - a * math.log(4 * a)
    e = clamp(e, 0.0, 1.0)
    return math.sqrt(3 * e) if e <= 1 / 12 else a * math.log(12 * e - b) + c


def hlg_from_display(rgb: Sequence[float]) -> list[float]:
    """Fly.hlgFromDisplay: BT.2100 inverse OOTF (gamma 1.2); display RGB (1 = 1000 nits), BT.2020 -> scene RGB."""
    y = 0.2627 * rgb[0] + 0.6780 * rgb[1] + 0.0593 * rgb[2]
    if not y > 0:
        return [0.0, 0.0, 0.0]
    k = _pow(y, (1 - 1.2) / 1.2)
    return [rgb[0] * k, rgb[1] * k, rgb[2] * k]


def rolloff(lum: float, peak: float) -> float:
    """Fly.rolloff: highlights above white roll off towards peak (in units of SDR white); identity to white."""
    if lum <= 1:
        return lum
    return 1 + (peak - 1) * (1 - _exp(_div(-(lum - 1), peak - 1)))


def lut_of(fn: Callable[[float], float]) -> Callable[[np.ndarray], np.ndarray]:
    """Fly.lutOf: a table for any [0,1] -> [0,1] function, read with linear interpolation.

    The table has DECODE_LUT + 1 samples at i/DECODE_LUT, as in the JS; the returned reader works on
    arrays: x <= 0 and NaN read the first entry, x >= 1 the last, as Fly.lutOf does.
    """
    n = DECODE_LUT
    xs = np.arange(n + 1, dtype=np.float64) / n
    table = np.array([fn(i / n) for i in range(n + 1)], dtype=np.float64)

    def read(x: Any) -> np.ndarray:
        v = np.asarray(x, dtype=np.float64)
        v = np.where(v > 0, v, 0.0)
        return np.interp(v, xs, table)

    return read


def decode_lut(curve: dict[str, Any]) -> Callable[[np.ndarray], np.ndarray]:
    """Fly.decodeLut: the decode table of one tone curve, as an array reader (see lut_of)."""
    return lut_of(lambda x: trc_decode(curve, x))


# ---- output transforms -------------------------------------------------------------

def is_hdr(mode: Any) -> bool:
    """Fly.isHdr: an output transfer "pq" or "hlg" (anything else is SDR)."""
    return mode == "pq" or mode == "hlg"


def _rolloff_array(lum: np.ndarray, peak: float) -> np.ndarray:
    """Fly.rolloff over an array (identity at or below white)."""
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        rolled = 1 + (peak - 1) * (1 - np.exp(-(lum - 1) / (peak - 1)))
    return np.where(lum <= 1, lum, rolled)


def _hlg_encode_array(e: np.ndarray) -> np.ndarray:
    """Fly.hlgEncode over an array."""
    a = 0.17883277
    b = 1 - 4 * a
    c = 0.5 - a * math.log(4 * a)
    e = np.clip(e, 0.0, 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        high = a * np.log(12 * e - b) + c
    return np.where(e <= 1 / 12, np.sqrt(3 * e), high)


class OutputTransform:
    """Fly.outputTransform (SDR) and Fly.hdrTransform (PQ, HLG) as one object.

    ``apply(planes, excess)`` converts the planes IN PLACE: three planes (R, G, B) for a colour image, one
    plane for a gray one. ``excess`` is the unscreened star light above white, one array per plane (None
    entries and a None list mean none); SDR ignores it. HDR transforms measure every frame into ``stats``
    ({"max_cll", "max_fall"}, in nits, for the HDR10 metadata); SDR leaves them at 0. ``mode`` is the output
    transfer: "sdr", "pq" or "hlg".
    """

    def __init__(self, colour: dict[str, Any], mode: str, opts: Optional[dict[str, Any]] = None) -> None:
        opts = opts or {}
        self.mode = mode
        self.hdr = is_hdr(mode)
        self.stats: dict[str, float] = {"max_cll": 0.0, "max_fall": 0.0}
        gray = bool(colour.get("gray"))
        self._dec = [decode_lut(c) for c in colour["trc"]]
        if not self.hdr:
            self._enc = lut_of(srgb_encode)
            self._m: Optional[list[float]] = None if gray else source_to_target(colour["matrix"], "rec709")
            return
        # HDR: BT.2020 linear light; PQ peak from opts, HLG fixed at its reference peak
        self._m = list(_IDENTITY) if gray else source_to_target(colour["matrix"], "rec2020")
        peak = _js_or(opts.get("peak"), HDR_PEAK_DEFAULT) if mode == "pq" else HLG_REFERENCE_PEAK
        self.peak = peak
        self._p = peak / SDR_WHITE_NITS
        self._pq = lut_of(lambda x: pq_encode(x * peak))     # x = nits / peak

    # -- public -----------------------------------------------------------------------

    def apply(self, planes: list[np.ndarray], excess: Optional[list[Optional[np.ndarray]]] = None) -> None:
        """Convert planes in place: 3 planes RGB, 1 plane gray (Fly.apply / Fly.applyGray)."""
        if len(planes) == 1:
            self._apply_gray(planes[0], excess)
        else:
            self._apply_rgb(planes, excess)

    # -- internals ---------------------------------------------------------------------

    def _apply_rgb(self, planes: list[np.ndarray], excess: Optional[list[Optional[np.ndarray]]]) -> None:
        r, g, b = (np.asarray(p, dtype=np.float64) for p in planes)
        if not self.hdr:
            out = self._sdr_rgb(r, g, b)
        else:
            out, nits = self._hdr_rgb((r, g, b), tuple(self._excess(excess, r.shape, 3)))
            self._measure(nits)
        for p, o in zip(planes, out):
            p[...] = o

    def _apply_gray(self, plane: np.ndarray, excess: Optional[list[Optional[np.ndarray]]]) -> None:
        k = np.asarray(plane, dtype=np.float64)
        if not self.hdr:
            plane[...] = self._enc(self._dec[0](k))       # Fly.applyGray: channel 0's curve
            return
        x = self._excess(excess, k.shape, 1)[0]
        out, nits = self._hdr_rgb((k, k, k), (x, x, x))
        plane[...] = out[1]                              # Fly.hdrTransform.applyGray keeps K from green
        self._measure(nits)

    @staticmethod
    def _excess(excess: Optional[list[Optional[np.ndarray]]], shape: tuple, count: int) -> list[np.ndarray]:
        """The excess planes as float64, zeros where none was given."""
        items = list(excess) if excess is not None else []
        out = []
        for i in range(count):
            e = items[i] if i < len(items) else None
            out.append(np.zeros(shape) if e is None else np.asarray(e, dtype=np.float64))
        return out

    def _sdr_rgb(self, r: np.ndarray, g: np.ndarray, b: np.ndarray) -> list[np.ndarray]:
        lr, lg, lb = self._dec[0](r), self._dec[1](g), self._dec[2](b)
        if self._m is not None:
            m = self._m      # the JS Fly.outputTransform: x, y, z from the decoded (lr, lg, lb)
            lr, lg, lb = (m[0] * lr + m[1] * lg + m[2] * lb,
                          m[3] * lr + m[4] * lg + m[5] * lb,
                          m[6] * lr + m[7] * lg + m[8] * lb)
        return [self._enc(lr), self._enc(lg), self._enc(lb)]

    def _hdr_rgb(self, rgb: tuple[np.ndarray, np.ndarray, np.ndarray],
                 excess: tuple[np.ndarray, np.ndarray, np.ndarray]) -> tuple[list[np.ndarray], np.ndarray]:
        """Fly.hdrTransform's one(): the composite plus the star light above white, in BT.2020 SDR-white units.

        ``rgb`` are the three encoded planes and ``excess`` the three star-light planes above white (the same
        array three times for a gray image). The brightest channel is rolled off to the peak with the others
        scaled alike (hue kept). Returns the three output signals and the pixel luminance in nits (for
        MaxCLL/MaxFALL).
        """
        m = self._m
        lr = self._dec[0](rgb[0]) + excess[0]
        lg = self._dec[1](rgb[1]) + excess[1]
        lb = self._dec[2](rgb[2]) + excess[2]
        v0 = np.maximum(0.0, m[0] * lr + m[1] * lg + m[2] * lb)
        v1 = np.maximum(0.0, m[3] * lr + m[4] * lg + m[5] * lb)
        v2 = np.maximum(0.0, m[6] * lr + m[7] * lg + m[8] * lb)
        top = np.maximum(np.maximum(v0, v1), v2)
        over = top > 1
        safe = np.where(over, top, 2.0)
        k = np.where(over, _rolloff_array(safe, self._p) / safe, 1.0)
        v0, v1, v2, top = v0 * k, v1 * k, v2 * k, top * k
        w = SDR_WHITE_NITS
        if self.mode == "pq":
            out = [self._pq(v0 / self._p), self._pq(v1 / self._p), self._pq(v2 / self._p)]
        else:
            # HLG: the inverse OOTF on display light (1 = the 1000-nit reference), then the OETF
            a, bb, c = v0 * w / self.peak, v1 * w / self.peak, v2 * w / self.peak
            y = 0.2627 * a + 0.6780 * bb + 0.0593 * c
            ok = y > 0
            ys = np.where(ok, y, 1.0)
            kk = np.power(ys, (1 - 1.2) / 1.2)
            out = [_hlg_encode_array(np.where(ok, a * kk, 0.0)),
                   _hlg_encode_array(np.where(ok, bb * kk, 0.0)),
                   _hlg_encode_array(np.where(ok, c * kk, 0.0))]
        return out, top * w

    def _measure(self, nits: np.ndarray) -> None:
        """Fly.hdrTransform's frame(): MaxCLL is the brightest pixel, MaxFALL the frame's mean (both nits)."""
        n = nits.size
        if n == 0:
            return
        valid = nits[~np.isnan(nits)]
        if valid.size:
            self.stats["max_cll"] = max(self.stats["max_cll"], float(valid.max()))
        mean = float(nits.sum()) / n
        if mean > self.stats["max_fall"]:
            self.stats["max_fall"] = mean


def output_transform(colour: Optional[dict[str, Any]], mode: str,
                     opts: Optional[dict[str, Any]] = None) -> OutputTransform:
    """Fly.outputTransform: the per-frame transform for a colour description and mode ("sdr", "pq", "hlg").

    ``colour`` None means sRGB; ``opts["peak"]`` is the PQ peak in nits (default HDR_PEAK_DEFAULT).
    """
    return OutputTransform(colour or SRGB_COLOUR, mode, opts)


# ---- the image's colour, and the built-in sRGB profile -----------------------------------

def colour_of(icc: Optional[bytes]) -> dict[str, Any]:
    """Sky.colourOf: the colour an image's ICC profile describes, with a note for the report.

    None (no profile) and an unreadable profile are both taken as sRGB, each with its own note.
    """
    if icc is None:
        return {"colour": SRGB_COLOUR, "note": "no colour profile: taken as sRGB"}
    parsed = parse_icc_colour(icc)
    if parsed is None:
        return {"colour": SRGB_COLOUR, "note": "colour profile is not a matrix/curve profile: taken as sRGB"}
    return {"colour": parsed, "note": "colour converted from the image's own profile"}


def _s15_bytes(v: float) -> bytes:
    """An s15Fixed16 number, big-endian (JS: be32(Math.round(v*65536) >>> 0))."""
    return struct.pack(">I", int(js_round(v * 65536)) & 0xFFFFFFFF)


def _xyz_tag(x: float, y: float, z: float) -> bytes:
    return b"XYZ " + b"\0" * 4 + _s15_bytes(x) + _s15_bytes(y) + _s15_bytes(z)


def _desc_tag(text: str) -> bytes:
    """A v2 textDescriptionType: ASCII, then empty Unicode and ScriptCode parts."""
    ascii_ = text.encode("ascii") + b"\0"
    return (b"desc" + b"\0" * 4 + struct.pack(">I", len(ascii_)) + ascii_
            + struct.pack(">I", 0) + struct.pack(">I", 0) + struct.pack(">H", 0) + b"\0" + b"\0" * 67)


def _para3_tag() -> bytes:
    """The sRGB curve as a parametric type 3 (g, a, b, c, d)."""
    p = SRGB_CURVE["p"]
    return b"para" + b"\0" * 4 + struct.pack(">HH", 3, 0) + b"".join(_s15_bytes(v) for v in p)


def _text_tag(text: str) -> bytes:
    return b"text" + b"\0" * 4 + text.encode("ascii") + b"\0"


def srgb_icc_bytes() -> bytes:
    """Render.srgbIcc: a minimal ICC v2 display profile for sRGB, generated in code (no system profile needed).

    Header (128 bytes), tag table, then desc, wtpt (D50), rXYZ/gXYZ/bXYZ (sRGB primaries adapted to D50),
    rTRC/gTRC/bTRC (the IEC 61966-2-1 curve as parametric type 3) and cprt. Big-endian throughout; every tag
    starts on a 4-byte boundary; the profile size is in the header.
    """
    tags = [
        (b"desc", _desc_tag(SRGB_PROFILE_NAME)),
        (b"wtpt", _xyz_tag(0.9642, 1.0, 0.8249)),                  # D50 PCS white
        # the same D50 primaries as SRGB_COLOUR.matrix, to 7 digits: 4-digit values miss the 1e-4 identity
        (b"rXYZ", _xyz_tag(0.4360747, 0.2225045, 0.0139322)),
        (b"gXYZ", _xyz_tag(0.3850649, 0.7168786, 0.0971045)),
        (b"bXYZ", _xyz_tag(0.1430804, 0.0606169, 0.7141733)),
        (b"rTRC", _para3_tag()),
        (b"gTRC", _para3_tag()),
        (b"bTRC", _para3_tag()),
        (b"cprt", _text_tag("No copyright, use freely")),
    ]
    table_end = 128 + 4 + 12 * len(tags)
    table = b""
    body = b""
    for sig, data in tags:
        offset = table_end + len(body)
        table += sig + struct.pack(">II", offset, len(data))
        body += data + b"\0" * (-len(data) % 4)
    size = table_end + len(body)
    header = (
        struct.pack(">I", size)                                    # 0: profile size
        + b"\0" * 4                                                # 4: preferred CMM
        + struct.pack(">I", 0x02100000)                            # 8: version 2.1
        + b"mntr" + b"RGB " + b"XYZ "                              # 12, 16, 20: class, colour space, PCS
        + struct.pack(">6H", 2000, 1, 1, 0, 0, 0)                  # 24: creation date
        + b"acsp"                                                  # 36: file signature
        + b"\0" * 4 + struct.pack(">I", 0) + b"\0" * 4 + b"\0" * 4 + b"\0" * 8   # 40-63: platform, flags, ids
        + struct.pack(">I", 0)                                     # 64: rendering intent (perceptual)
        + _s15_bytes(0.9642) + _s15_bytes(1.0) + _s15_bytes(0.8249)  # 68: PCS illuminant D50
        + b"\0" * 4 + b"\0" * 16 + b"\0" * 28                     # 80-127: creator, profile id, reserved
    )
    assert len(header) == 128
    return header + struct.pack(">I", len(tags)) + table + body
