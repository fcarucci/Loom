"""The logo layer of a Fly-Through (Loom for Siril): a picture drawn over every frame.

A port of the logo part of Loom's PixInsight Fly-Through: ``Fly.LOGO_PLACES`` / ``LOGO_SIZE`` / ``LOGO_MARGIN`` /
``logoFade`` / ``logoRect`` (Fly.js 1215-1233), ``Render.logoLayer`` / ``fitPlane`` / ``addLogo`` (Render.js 575-646)
and ``FlyThrough.readLogo`` (FlyThrough.js 197-212, here ``read_logo``).

How it works, as in the JS: the logo is sized so its long side is 20 % of the frame's SHORT side, kept 4 % of the short
side from the edges (or centred), its colour is premultiplied by its alpha times the opacity, then area-averaged
(shrinking) or bilinearly sampled (enlarging) to that size. It is painted into the frame's composite, BEFORE the output
transform (so it goes through the same colour conversion as the picture and, in HDR, covers the star light above white
too): ``logo + frame x (1 - alpha)``. ``fade-in after N s`` hides it until N seconds into the clip and then brings it in
over one second (a smoothstep); 0 shows it from the first frame.

Files: PixInsight reads any file format it has a reader for. Here the logo is read by this module without any extra
package: **PNG** (all colour types and bit depths, interlaced or not, with its transparency), **TIFF** (8/16-bit
integer or float; grey, grey + alpha, RGB, RGBA) and **FITS** (through ``io_fits``). JPEG and others are refused with a
message that says so.
"""

from __future__ import annotations

import os
import struct
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from loomsiril.core import js_round, smoothstep
from loomsiril.resample import fit_plane

__all__ = [
    "LOGO_FILE_TYPES",
    "LOGO_MARGIN",
    "LOGO_PLACES",
    "LOGO_PLACE_LABELS",
    "LOGO_SIZE",
    "LogoError",
    "LogoImage",
    "LogoLayer",
    "add_logo",
    "attach",
    "file_stamp",
    "is_on",
    "layer_for",
    "logo_fade",
    "logo_layer",
    "logo_rect",
    "logo_settings",
    "place_id",
    "place_label",
    "read_logo",
]

#: The places of the dialog's combo, after "Off" (Fly.LOGO_PLACES).
LOGO_PLACES: list[str] = ["center", "top-left", "top-mid", "top-right", "bottom-left", "bottom-mid", "bottom-right"]
#: The dialog's labels: "Off" first, then LOGO_PLACES in order (FlyThrough.js 1411).
LOGO_PLACE_LABELS: list[str] = ["Off", "Center", "Top left", "Top middle", "Top right", "Bottom left", "Bottom middle", "Bottom right"]
LOGO_SIZE: float = 0.2     # the logo's long side, of the frame's short side
LOGO_MARGIN: float = 0.04  # its margin from the edges (title-safe), of the short side
#: File extensions the reader takes (for a file dialog); the content decides, not the extension.
LOGO_FILE_TYPES: list[str] = [".png", ".tif", ".tiff", ".fit", ".fits", ".fts"]


def place_id(label: str) -> str:
    """The place id of a combo label or id ("Top left" or "top-left" -> "top-left"; "Off" or anything unknown -> "off")."""
    text = str(label or "").strip()
    if text in LOGO_PLACES:
        return text
    return LOGO_PLACES[LOGO_PLACE_LABELS.index(text) - 1] if text in LOGO_PLACE_LABELS[1:] else "off"


def place_label(place: str) -> str:
    """The combo label of a place id ("top-left" -> "Top left"; unknown -> "Off")."""
    return LOGO_PLACE_LABELS[LOGO_PLACES.index(place) + 1] if place in LOGO_PLACES else LOGO_PLACE_LABELS[0]


class LogoError(ValueError):
    """The logo file is not there or cannot be read; the message is for the user."""


@dataclass
class LogoImage:
    """A logo as read: ``planes`` one (grey) or three (RGB) float32 ``(h, w)`` planes in [0, 1], top-down; ``alpha`` its own
    transparency plane or None (opaque)."""

    planes: list[np.ndarray]
    alpha: Optional[np.ndarray] = None

    @property
    def width(self) -> int:
        return int(self.planes[0].shape[1])

    @property
    def height(self) -> int:
        return int(self.planes[0].shape[0])


@dataclass
class LogoLayer:
    """``Render.logoLayer``'s result: the logo for one frame size. ``x``, ``y``, ``w``, ``h`` its rectangle in the frame;
    ``p`` its colour planes premultiplied by alpha x opacity (one per frame channel); ``a`` its alpha x opacity."""

    x: int
    y: int
    w: int
    h: int
    p: list[np.ndarray]
    a: np.ndarray


# ---- the maths (Fly.js) ------------------------------------------------------------------------------------------------


def logo_fade(seconds: float, delay: float) -> float:
    """``Fly.logoFade``: how much of the logo shows ``seconds`` in, with a fade-in ``delay`` (s): all at 0; hidden until
    the delay, then in over a second."""
    return smoothstep(delay, delay + 1, seconds) if delay > 0 else 1.0


def logo_rect(frame_w: int, frame_h: int, lw: int, lh: int, place: str) -> Optional[dict[str, float]]:
    """``Fly.logoRect``: where a logo (lw x lh) goes in a frame_w x frame_h frame at ``place``; None for "off" (or any place
    that is not one of LOGO_PLACES). ``x`` and ``y`` may be halves (centred); ``w`` and ``h`` are whole pixels."""
    if place not in LOGO_PLACES:
        return None
    sh = min(frame_w, frame_h)
    k = js_round(LOGO_SIZE * sh) / max(lw, lh)
    m = js_round(LOGO_MARGIN * sh)
    w = js_round(lw * k)
    h = js_round(lh * k)
    if place == "center":
        return {"x": (frame_w - w) / 2, "y": (frame_h - h) / 2, "w": w, "h": h}
    row, _, col = place.partition("-")
    x = m if col == "left" else frame_w - m - w if col == "right" else (frame_w - w) / 2
    y = m if row == "top" else frame_h - m - h
    return {"x": x, "y": y, "w": w, "h": h}


# ---- the layer (Render.js) ---------------------------------------------------------------------------------------------


def logo_layer(img: LogoImage, frame_w: int, frame_h: int, place: str, channels: int = 3,
               opacity: Optional[float] = None) -> Optional[LogoLayer]:
    """``Render.logoLayer``: the logo for a frame_w x frame_h frame at ``place``, colour premultiplied by alpha x ``opacity``
    (None: 1; clamped to [0, 1]; the logo's own transparency is kept and scaled), area-averaged to its size. A grey logo is
    grey in every channel. None when the place is off."""
    op = 1.0 if opacity is None else max(0.0, min(1.0, float(opacity)))
    r = logo_rect(frame_w, frame_h, img.width, img.height, place)
    if r is None:
        return None
    x, y, w, h = int(js_round(r["x"])), int(js_round(r["y"])), int(r["w"]), int(r["h"])
    lw, lh = img.width, img.height
    base = np.ones((lh, lw), dtype=np.float32) if img.alpha is None else np.asarray(img.alpha, dtype=np.float32)
    alpha = (base * np.float32(op)).astype(np.float32)    # the logo's opacity scales its own transparency
    planes = []
    for c in range(channels or 3):
        b = (np.asarray(img.planes[min(c, len(img.planes) - 1)], dtype=np.float32) * alpha).astype(np.float32)
        planes.append(fit_plane(b, lw, lh, w, h))
    return LogoLayer(x=x, y=y, w=w, h=h, p=planes, a=fit_plane(alpha, lw, lh, w, h))


def add_logo(layer: LogoLayer, out: Sequence[np.ndarray], excess: Sequence[Optional[np.ndarray]],
             fade: Optional[float] = None) -> None:
    """``Render.addLogo``: the logo over a frame's composite, in place: ``logo + frame x (1 - alpha)`` (premultiplied), faded by
    ``fade`` (None: 1; 0 or less draws nothing); the HDR star light above white (``excess`` planes, None for SDR) is covered
    too. ``out`` are the channels' ``(h, w)`` planes (flat arrays are taken as ``(h, w)`` of the logo's frame by the caller)."""
    f = 1.0 if fade is None else float(fade)
    if not f > 0:
        return
    for c, plane in enumerate(out):
        fh, fw = plane.shape
        # the part of the logo's rectangle inside the frame (the JS never meets a logo that leaves it)
        x0, y0 = max(0, layer.x), max(0, layer.y)
        x1, y1 = min(fw, layer.x + layer.w), min(fh, layer.y + layer.h)
        if x1 <= x0 or y1 <= y0:
            continue
        sl = (slice(y0 - layer.y, y1 - layer.y), slice(x0 - layer.x, x1 - layer.x))
        dst = (slice(y0, y1), slice(x0, x1))
        a = f * layer.a[sl].astype(np.float64)
        p = layer.p[min(c, len(layer.p) - 1)][sl].astype(np.float64)
        draw = a > 0
        cur = plane[dst].astype(np.float64)
        plane[dst] = np.where(draw, f * p + cur * (1 - a), cur).astype(plane.dtype)
        ex = excess[c] if c < len(excess) else None
        if ex is not None:
            e = ex[dst].astype(np.float64)
            ex[dst] = np.where(draw, e * (1 - a), e).astype(ex.dtype)


# ---- reading the file (FlyThrough.readLogo) ----------------------------------------------------------------------------


def read_logo(path: str | os.PathLike[str]) -> LogoImage:
    """``FlyThrough.readLogo`` for a file: PNG, TIFF or FITS by its content (not its extension). Raises :class:`LogoError`
    with a message for the user when the file is not there, is of another format, or cannot be read."""
    p = os.fspath(path)
    if not os.path.isfile(p):
        raise LogoError("The logo file is not there: " + p)
    try:
        with open(p, "rb") as fh:
            head = fh.read(16)
    except OSError as exc:
        raise LogoError(f"The logo file cannot be read: {p} ({exc.strerror or exc})") from exc
    try:
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return _read_png(p)
        if head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
            return _read_tiff(p)
        if head.startswith(b"SIMPLE"):
            return _read_fits(p)
    except LogoError:
        raise
    except Exception as exc:  # a damaged file: say which, in plain words
        raise LogoError(f"The logo {p} could not be read ({exc})") from exc
    ext = os.path.splitext(p)[1].lower() or "(none)"
    raise LogoError(f"The logo {p} is not a PNG, TIFF or FITS file (extension {ext}); convert it to PNG (keeps its transparency)")


def _scale_of(dtype: np.dtype) -> Optional[float]:
    if dtype == np.uint8:
        return 255.0
    return float(2 ** (dtype.itemsize * 8) - 1) if dtype.kind == "u" else None


def _from_channels(arr: np.ndarray) -> LogoImage:
    """``arr`` shaped ``(c, h, w)`` (grey, grey + alpha, RGB or RGBA; more channels: the first four) as a LogoImage."""
    scale = _scale_of(arr.dtype)
    chans = [np.ascontiguousarray((arr[c].astype(np.float64) / scale) if scale else arr[c], dtype=np.float32)
             for c in range(arr.shape[0])]
    n = len(chans)
    if n == 1:
        return LogoImage(chans[:1])
    if n == 2:
        return LogoImage(chans[:1], chans[1])
    if n == 3:
        return LogoImage(chans)
    return LogoImage(chans[:3], chans[3])


def _read_tiff(path: str) -> LogoImage:
    import tifffile

    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        arr = np.asarray(page.asarray())
        planar = getattr(page, "planarconfig", None) == 2  # PLANARCONFIG.SEPARATE
    if arr.ndim == 2:
        arr = arr[np.newaxis]
    elif not planar:
        arr = np.moveaxis(arr, -1, 0)
    return _from_channels(arr)


def _read_fits(path: str) -> LogoImage:
    from loomsiril.io_fits import read_fits

    planes, _ = read_fits(path)
    return LogoImage([np.asarray(p, dtype=np.float32) for p in planes[:3]] if len(planes) >= 3 else [np.asarray(planes[0], dtype=np.float32)])


_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
#: Adam7 passes: (x start, y start, x step, y step).
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    pos, out = 8, []
    while pos + 8 <= len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        out.append((kind, data[pos + 8:pos + 8 + n]))
        pos += 12 + n
        if kind == b"IEND":
            break
    return out


def _unfilter(raw: np.ndarray, rows: int, stride: int, bpp: int) -> np.ndarray:
    """PNG scanline filters 0-4 undone: ``raw`` is ``rows`` x (1 + stride) bytes; returns ``(rows, stride)`` uint8."""
    out = np.zeros((rows, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.int32)
    for r in range(rows):
        ft = int(raw[r, 0])
        line = raw[r, 1:].astype(np.int32)
        if ft == 0:
            cur = line
        elif ft == 2:  # Up
            cur = (line + prev) & 255
        elif ft == 1:  # Sub: running sum per byte lane
            cur = line.copy()
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 255
        elif ft == 3:  # Average
            cur = line.copy()
            for i in range(stride):
                left = cur[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:  # Paeth
            cur = line.copy()
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[i] = (cur[i] + pred) & 255
        else:
            raise LogoError(f"unknown PNG filter {ft}")
        out[r] = cur
        prev = cur
    return out


def _png_samples(lines: np.ndarray, width: int, nch: int, depth: int) -> np.ndarray:
    """Unfiltered scanlines ``(rows, stride)`` to samples ``(rows, width, nch)`` (uint16 for 16-bit, else uint8)."""
    rows = lines.shape[0]
    if depth == 8:
        return lines[:, :width * nch].reshape(rows, width, nch)
    if depth == 16:
        v = lines[:, :width * nch * 2].reshape(rows, width * nch, 2).astype(np.uint16)
        return ((v[..., 0] << 8) | v[..., 1]).reshape(rows, width, nch)
    bits = np.unpackbits(lines, axis=1)                         # 1, 2 or 4 bits per sample, packed per row
    per = bits[:, :width * nch * depth].reshape(rows, width * nch, depth)
    weights = (1 << np.arange(depth - 1, -1, -1)).astype(np.uint16)
    return (per.astype(np.uint16) * weights).sum(axis=2).astype(np.uint8).reshape(rows, width, nch)


def _read_png(path: str) -> LogoImage:
    with open(path, "rb") as fh:
        data = fh.read()
    chunks = _png_chunks(data)
    ihdr = next((c for k, c in chunks if k == b"IHDR"), None)
    if ihdr is None or len(ihdr) < 13:
        raise LogoError(f"The logo {path} is not a valid PNG (no header)")
    width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", ihdr[:13])
    if ctype not in _PNG_CHANNELS or depth not in (1, 2, 4, 8, 16):
        raise LogoError(f"The logo {path} is a PNG of a kind not supported (colour type {ctype}, {depth} bits)")
    nch = _PNG_CHANNELS[ctype]
    palette = next((c for k, c in chunks if k == b"PLTE"), None)
    trns = next((c for k, c in chunks if k == b"tRNS"), None)
    raw = zlib.decompress(b"".join(c for k, c in chunks if k == b"IDAT"))
    bits_pp = nch * depth
    bpp = max(1, bits_pp // 8)
    samples = np.zeros((height, width, nch), dtype=np.uint16 if depth == 16 else np.uint8)
    passes = _ADAM7 if interlace else ((0, 0, 1, 1),)
    pos = 0
    for x0, y0, dx, dy in passes:
        pw = (width - x0 + dx - 1) // dx
        ph = (height - y0 + dy - 1) // dy
        if pw <= 0 or ph <= 0:
            continue
        stride = (pw * bits_pp + 7) // 8
        need = ph * (stride + 1)
        block = np.frombuffer(raw, dtype=np.uint8, count=need, offset=pos).reshape(ph, stride + 1)
        pos += need
        samples[y0::dy, x0::dx] = _png_samples(_unfilter(block, ph, stride, bpp), pw, nch, depth)
    return _png_image(samples, ctype, depth, palette, trns)


def _png_image(samples: np.ndarray, ctype: int, depth: int, palette: Optional[bytes], trns: Optional[bytes]) -> LogoImage:
    full = float(2 ** depth - 1)
    if ctype == 3:  # palette: index to RGB, tRNS gives the alpha of the first entries
        if palette is None:
            raise LogoError("a palette PNG without its palette")
        pal = np.frombuffer(palette, dtype=np.uint8).reshape(-1, 3).astype(np.float32) / 255.0
        idx = samples[..., 0].astype(np.int64)
        idx = np.clip(idx, 0, len(pal) - 1)
        planes = [np.ascontiguousarray(pal[idx, c]) for c in range(3)]
        alpha = None
        if trns:
            table = np.ones(len(pal), dtype=np.float32)
            t = np.frombuffer(trns, dtype=np.uint8)[:len(pal)]
            table[:len(t)] = t.astype(np.float32) / 255.0
            alpha = np.ascontiguousarray(table[idx])
        return LogoImage(planes, alpha)
    f = samples.astype(np.float64) / full
    if ctype == 0:
        planes, alpha = [f[..., 0]], None
    elif ctype == 4:
        planes, alpha = [f[..., 0]], f[..., 1]
    elif ctype == 2:
        planes, alpha = [f[..., c] for c in range(3)], None
    else:
        planes, alpha = [f[..., c] for c in range(3)], f[..., 3]
    if alpha is None and trns and ctype in (0, 2) and len(trns) >= 2 * samples.shape[2]:  # a single transparent colour
        key = np.asarray(struct.unpack(">" + "H" * samples.shape[2], trns[:2 * samples.shape[2]]), dtype=np.int64)
        alpha = np.where(np.all(samples.astype(np.int64) == key, axis=2), 0.0, 1.0)
    return LogoImage([np.ascontiguousarray(p, dtype=np.float32) for p in planes],
                     None if alpha is None else np.ascontiguousarray(alpha, dtype=np.float32))


# ---- the options (what the dialog and the job carry) -------------------------------------------------------------------


def _get(options: Mapping[str, Any], camel: str, snake: str, default: Any) -> Any:
    if camel in options and options[camel] is not None:
        return options[camel]
    if snake in options and options[snake] is not None:
        return options[snake]
    return default


def logo_settings(options: Mapping[str, Any]) -> dict[str, Any]:
    """The logo's options by either spelling: ``path`` ("" when none), ``place`` (a LOGO_PLACES name; "off" when unknown),
    ``opacity`` (0-1) and ``delay`` (s, 0 or more).

    The engine's camelCase names (``logoPath``, ``logoPlace``, ``logoOpacity``, ``logoDelay``: the JS options, opacity a
    fraction) win; else the dialog's snake_case ones (``logo_path``, ``logo_place``, ``logo_opacity`` in PERCENT as the
    slider holds it, ``logo_delay``)."""
    path = str(_get(options, "logoPath", "logo_path", "") or "").strip()
    place = str(_get(options, "logoPlace", "logo_place", "off") or "off")
    if place not in LOGO_PLACES:
        place = "off"
    try:
        if options.get("logoOpacity") is not None:
            opacity = float(options["logoOpacity"])
        elif options.get("logo_opacity") is not None:
            opacity = float(options["logo_opacity"]) / 100.0
        else:
            opacity = 1.0
    except (TypeError, ValueError):
        opacity = 1.0
    try:
        delay = float(_get(options, "logoDelay", "logo_delay", 0.0))
    except (TypeError, ValueError):
        delay = 0.0
    return {"path": path, "place": place, "opacity": max(0.0, min(1.0, opacity)), "delay": max(0.0, delay)}


def is_on(options: Mapping[str, Any]) -> bool:
    """Whether the options draw a logo: a file is named and the placement is not Off."""
    s = logo_settings(options)
    return bool(s["path"]) and s["place"] != "off"


def file_stamp(path: str) -> str:
    """The logo file's identity for the frame signature: absolute path, modification time (ns) and size; a missing file says
    so. A changed logo therefore renders its frames again; an unchanged one reuses them."""
    p = os.path.abspath(path)
    try:
        st = os.stat(p)
    except OSError:
        return f"{p}|missing"
    return f"{p}|{st.st_mtime_ns}|{st.st_size}"


def attach(opts: dict[str, Any]) -> None:
    """Reads the logo the options name and sets ``opts["logoImage"]`` (a :class:`LogoImage`, encode-only for the signature)
    and ``opts["logoStamp"]`` (:func:`file_stamp`, in the signature), as ``FlyThrough.readLogo`` sets ``opts.logoImage``
    before a render or draft. Nothing is set, and a stale ``logoImage`` removed, when there is no path or the place is off.
    Raises :class:`LogoError` when the file is missing or unreadable. A ``logoImage`` already there is kept."""
    s = logo_settings(opts)
    if not s["path"] or s["place"] == "off":
        # no logo is drawn: none of its values may stay in the options (they would key the frames and the draft for nothing)
        for key in ("logoImage", "logoStamp", "logoPath", "logoPlace", "logoOpacity", "logoDelay",
                    "logo_path", "logo_place", "logo_opacity", "logo_delay"):
            opts.pop(key, None)
        return
    if not isinstance(opts.get("logoImage"), LogoImage):
        opts["logoImage"] = read_logo(s["path"])
    opts["logoStamp"] = file_stamp(s["path"])
    # one spelling for the frame code and the signature
    opts["logoPath"], opts["logoPlace"], opts["logoOpacity"], opts["logoDelay"] = s["path"], s["place"], s["opacity"], s["delay"]


def layer_for(opts: Mapping[str, Any], frame_w: int, frame_h: int, channels: int) -> Optional[LogoLayer]:
    """The layer of the options' logo for a frame size (``Render.logoLayer`` of ``opts.logoImage``): None without a logo."""
    img = opts.get("logoImage")
    if not isinstance(img, LogoImage):
        return None
    s = logo_settings(opts)
    return logo_layer(img, frame_w, frame_h, s["place"], channels, s["opacity"])
