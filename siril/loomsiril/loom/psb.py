"""Writer for Photoshop Large Document (PSB) files: a byte-level port of Loom's lib/Psb.js.

Version 2 PSB, RGB, 16 bits per sample, raw (uncompressed) channel data, no alpha and no masks. Layers are
pixel layers, group markers, and Curves / Hue-Saturation / Vibrance adjustment layers. The ICC profile goes into
image resource 1039. Every ``snake_case`` function below mirrors a JavaScript function of the same meaning in
``script/lib/Psb.js`` (cited by line range in each docstring); the JavaScript is the reference.

Document tree (bottom layer first, like the JavaScript ``Psb.write`` entries). Each node is a dict:

* ``{"kind": "pixel", "name", "planes", ...}``: ``planes`` is a list of one or three top-down 2-D arrays with
  samples in [0, 1]; one plane is written to all three channels.
* ``{"kind": "group", "name", "children": [...], "open": True, ...}``: children are bottom first.
* ``{"kind": "curves", "name", "channels": [...]}``: channel ids 0 = composite, 1 = red, 2 = green, 3 = blue.
* ``{"kind": "hue", "name"}`` and ``{"kind": "vibrance", "name"}``.

Optional keys on every node: ``blend`` (four-character key, default normal; groups default to pass-through),
``visible`` (default True), ``opacity`` (0..255, default 255), ``clipping`` (default False).

Only the pure parts of ``Psb.js`` are ported. The threaded byte swap is replaced by a plain numpy conversion, which
writes the same bytes: 16-bit big-endian, planar, one channel after another.
"""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Sequence

import numpy as np

# lib/Psb.js:32-40
SIGNATURE = "8BPS"
VERSION_PSB = 2
COLOR_MODE_RGB = 3
COMPRESSION_RAW = 0

DIVIDER_OPEN = 1
DIVIDER_CLOSED = 2
DIVIDER_BOUNDING = 3

# lib/Psb.js:847-851 (the trailing space in "lum " is part of the key)
BLEND_NORMAL = "norm"
BLEND_SCREEN = "scrn"
BLEND_LUMINOSITY = "lum "
BLEND_SOFT_LIGHT = "sLit"
BLEND_PASS_THROUGH = "pass"

# lib/Psb.js:867
RESOURCE_ICC_PROFILE = 1039

# lib/Psb.js:271-276: the six colour bands of the Hue/Saturation dropdown
HUE_RANGES = [
    [315, 345, 15, 45],  # reds
    [15, 45, 75, 105],  # yellows
    [75, 105, 135, 165],  # greens
    [135, 165, 195, 225],  # cyans
    [195, 225, 255, 285],  # blues
    [255, 285, 315, 345],  # magentas
]

# Rows converted per numpy block when writing a plane (bounds the float64 working copy).
_BLOCK_SAMPLES = 1 << 22


def _utf16_units(s: str) -> list[int]:
    """The UTF-16 code units of ``s``, as JavaScript's string indexing sees them."""
    raw = s.encode("utf-16-be", "surrogatepass")
    return [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]


class Buffer:
    """A growable big-endian byte buffer (lib/Psb.js:51-154 ``Psb.Buffer``)."""

    __slots__ = ("bytes",)

    def __init__(self) -> None:
        self.bytes = bytearray()

    def u8(self, v: int) -> Buffer:
        self.bytes.append(v & 0xFF)
        return self

    def u16(self, v: int) -> Buffer:
        self.bytes += bytes(((v >> 8) & 0xFF, v & 0xFF))
        return self

    def u32(self, v: int) -> Buffer:
        self.bytes += (v & 0xFFFFFFFF).to_bytes(4, "big")
        return self

    def u64(self, v: int) -> Buffer:
        if v < 0:
            raise ValueError(f"u64 cannot hold a negative length: {v}")
        self.u32(v >> 32)
        self.u32(v & 0xFFFFFFFF)
        return self

    def i32(self, v: int) -> Buffer:
        return self.u32(v & 0xFFFFFFFF)

    def ascii(self, s: str) -> Buffer:
        self.bytes += bytes(ord(c) & 0xFF for c in s)
        return self

    def zeros(self, n: int) -> Buffer:
        self.bytes += bytes(n)
        return self

    def pascal(self, s: Any, pad: int) -> Buffer:
        """Pascal string, names cut to 255 units, padded so the whole field is a multiple of ``pad``."""
        units = _utf16_units(str(s))[:255]
        self.u8(len(units))
        self.bytes += bytes(u & 0xFF for u in units)
        over = (1 + len(units)) % pad
        if over:
            self.zeros(pad - over)
        return self

    def unicode_name(self, s: Any) -> Buffer:
        """UTF-16BE name with its count, padded so the block is a multiple of four (lib/Psb.js:131-143)."""
        units = _utf16_units(str(s))
        self.u32(len(units))
        for u in units:
            self.u16(u)
        over = (4 + 2 * len(units)) % 4
        if over:
            self.zeros(4 - over)
        return self

    def append(self, b: Buffer | bytes | bytearray | Sequence[int]) -> Buffer:
        if isinstance(b, Buffer):
            self.bytes += b.bytes
        else:
            self.bytes += bytes(b)
        return self

    def length(self) -> int:
        return len(self.bytes)

    def to_bytes(self) -> bytes:
        return bytes(self.bytes)


def tagged_block(key: str, payload: Buffer) -> Buffer:
    """'8BIM' + key + 4-byte length + data, padded to an even length (lib/Psb.js:166-176)."""
    data = payload.bytes
    b = Buffer()
    b.ascii("8BIM").ascii(key)
    b.u32(len(data)).append(data)
    if len(data) % 2:
        b.zeros(1)
    return b


def _identity_curve(buf: Buffer) -> None:
    buf.u16(2)  # two points
    buf.u16(0).u16(0)  # (output, input) = black
    buf.u16(255).u16(255)  # ...and white


def curves_payload(channel_ids: Sequence[int]) -> Buffer:
    """The 'curv' payload with identity curves (lib/Psb.js:213-251)."""
    ids: list[int] = []
    for c in channel_ids:
        if c not in ids:
            ids.append(c)

    bitmap = 0
    for c in ids:
        bitmap |= 1 << c

    b = Buffer()
    b.u8(0)  # is_map: point curves
    b.u16(1)  # version
    b.u32(bitmap)
    for _ in ids:
        _identity_curve(b)

    b.ascii("Crv ")
    b.u16(4)
    b.u32(len(ids))
    for c in ids:
        b.u16(c)
        _identity_curve(b)

    while b.length() % 4:
        b.zeros(1)
    return b


def hue_saturation_payload() -> Buffer:
    """The 'hue2' payload with every setting at zero (lib/Psb.js:278-304)."""
    b = Buffer()
    b.u16(2)  # version
    b.u8(0)  # COLORIZE, which must be 0
    b.u8(0)  # padding
    b.u16(0).u16(0).u16(0)  # colorization: hue, saturation, lightness
    b.u16(0).u16(0).u16(0)  # master
    for band in HUE_RANGES:
        for v in band:
            b.u16(v)
        b.u16(0).u16(0).u16(0)  # no shift in this band
    while b.length() % 4:
        b.zeros(1)
    return b


def vibrance_payload() -> Buffer:
    """The 'vibA' payload: a descriptor with vibrance and saturation at zero (lib/Psb.js:322-332)."""
    b = Buffer()
    b.u32(16)  # version
    b.u32(0)  # descriptor name: empty
    b.u32(0).ascii("null")  # class id
    b.u32(2)  # items
    b.u32(8).ascii("vibrance").ascii("long").u32(0)
    b.u32(0).ascii("Strt").ascii("long").u32(0)
    return b  # 56 bytes


def is_adjustment(layer: dict[str, Any]) -> bool:
    """Curves, hue/saturation and vibrance layers hold no pixels (lib/Psb.js:378-382)."""
    return (layer.get("curves") is not None) or (layer.get("hue_saturation") is True) or (
        layer.get("vibrance") is True
    )


def has_pixels(layer: dict[str, Any]) -> bool:
    """Only plain pixel layers carry pixels (lib/Psb.js:385-388)."""
    return layer.get("divider") is None and not is_adjustment(layer)


def layer_extra_blocks(layer: dict[str, Any]) -> Buffer:
    """Tagged blocks after a layer record: name, group divider, adjustment data (lib/Psb.js:339-371)."""
    b = Buffer()

    luni = Buffer()
    luni.unicode_name(layer["name"])
    b.append(tagged_block("luni", luni))

    divider = layer.get("divider")
    if divider is not None:
        lsct = Buffer()
        lsct.u32(divider)
        # a group header repeats its blend mode here; Photoshop reads the group's blend from this copy
        if divider in (DIVIDER_OPEN, DIVIDER_CLOSED):
            lsct.ascii("8BIM").ascii(layer.get("blend") or BLEND_NORMAL)
        b.append(tagged_block("lsct", lsct))

    if layer.get("curves") is not None:
        b.append(tagged_block("curv", curves_payload(layer["curves"])))
    if layer.get("hue_saturation"):
        b.append(tagged_block("hue2", hue_saturation_payload()))
    if layer.get("vibrance"):
        b.append(tagged_block("vibA", vibrance_payload()))
    return b


def composite_base_layer(layers: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The first visible pixel layer, else the first pixel layer of any visibility (lib/Psb.js:403-412)."""
    for layer in layers:
        if has_pixels(layer) and layer.get("visible"):
            return layer
    for layer in layers:
        if has_pixels(layer):
            return layer
    return None


def layer_record(layer: dict[str, Any], width: int, height: int, channel_lengths: Sequence[int]) -> Buffer:
    """One layer record; ``channel_lengths`` include each channel's 2-byte compression word (lib/Psb.js:414-470)."""
    b = Buffer()

    # Markers and adjustment layers have an empty rectangle; pixel layers take the document bounds.
    if has_pixels(layer):
        b.i32(0).i32(0).i32(height).i32(width)
    else:
        b.i32(0).i32(0).i32(0).i32(0)

    ids = layer["channel_ids"]
    b.u16(len(ids))
    for c, cid in enumerate(ids):
        b.u16(cid + 65536 if cid < 0 else cid)
        b.u64(channel_lengths[c])  # PSB: 8-byte lengths

    b.ascii("8BIM")
    b.ascii(layer.get("blend") or BLEND_NORMAL)
    opacity = layer.get("opacity")
    b.u8(255 if opacity is None else opacity)
    b.u8(1 if layer.get("clipping") else 0)

    # Flags: 0x08 "bit 5 has useful information", 0x02 hidden, 0x10 "pixel data irrelevant" on group markers
    flags = 0x08
    if not layer.get("visible"):
        flags |= 0x02
    if layer.get("divider") is not None:
        flags |= 0x10
    b.u8(flags)
    b.u8(0)  # filler

    extra = Buffer()
    extra.u32(0)  # layer mask data: none
    extra.u32(0)  # blending ranges: none
    extra.pascal(layer["name"], 4)
    extra.append(layer_extra_blocks(layer))

    b.u32(extra.length()).append(extra)
    return b


def _flat_layer(node: dict[str, Any]) -> dict[str, Any]:
    """A flat layer for a pixel or adjustment node (lib/Psb.js:534-547)."""
    kind = node.get("kind")
    curves = None
    planes = None
    if kind == "curves":
        if node.get("channels") is None:
            raise ValueError(f"curves layer {node.get('name')!r} needs 'channels'")
        curves = [int(c) for c in node["channels"]]
    elif kind == "pixel":
        planes = node.get("planes")
    elif kind not in ("hue", "vibrance"):
        raise ValueError(f"unknown layer kind {kind!r}")
    opacity = node.get("opacity")
    return {
        "name": node["name"],
        "divider": None,
        "visible": node.get("visible") is not False,
        "blend": node.get("blend") or BLEND_NORMAL,
        "opacity": 255 if opacity is None else opacity,
        "channel_ids": [-1, 0, 1, 2],
        "curves": curves,
        "hue_saturation": kind == "hue",
        "vibrance": kind == "vibrance",
        "clipping": bool(node.get("clipping")),
        "planes": planes,
    }


def flatten_group(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Bounding divider, the group's layers, then the header (lib/Psb.js:519-532)."""
    bounding = {
        "name": "</Layer group>",
        "divider": DIVIDER_BOUNDING,
        "visible": True,
        "blend": BLEND_NORMAL,
        "opacity": 255,
        "channel_ids": [0, 1, 2, -1],
        "curves": None,
        "hue_saturation": False,
        "vibrance": False,
        "clipping": False,
        "planes": None,
    }
    opacity = node.get("opacity")
    header = {
        "name": node["name"],
        "divider": DIVIDER_CLOSED if node.get("open") is False else DIVIDER_OPEN,
        "visible": node.get("visible") is not False,
        "blend": node.get("blend") or BLEND_PASS_THROUGH,
        "opacity": 255 if opacity is None else opacity,
        "channel_ids": [0, 1, 2, -1],
        "curves": None,
        "hue_saturation": False,
        "vibrance": False,
        "clipping": False,
        "planes": None,
    }
    return [bounding, *flatten(node["children"]), header]


def flatten(entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The document tree as the flat, bottom-first record list Photoshop stores (lib/Psb.js:504-516).

    No reversal happens here: the last record written is the top layer in Photoshop, as in the JavaScript.
    """
    out: list[dict[str, Any]] = []
    for node in entries:
        if node.get("kind") == "group":
            out.extend(flatten_group(node))
        else:
            out.append(_flat_layer(node))
    return out


def declare_channels(layers: list[dict[str, Any]], samples: int) -> list[list[int]]:
    """Three channel byte lengths per layer; sets each layer's channel ids to 0, 1, 2 (lib/Psb.js:939-951)."""
    pixel_bytes = 2 + samples * 2  # compression word + raw samples
    marker_bytes = 2  # compression word only
    lengths: list[list[int]] = []
    for layer in layers:
        per = pixel_bytes if has_pixels(layer) else marker_bytes
        lengths.append([per, per, per])
        layer["channel_ids"] = [0, 1, 2]
    return lengths


def layer_records(
    layers: Sequence[dict[str, Any]], width: int, height: int, channel_lengths: Sequence[Sequence[int]]
) -> Buffer:
    """The layer count and every layer record (lib/Psb.js:953-961)."""
    records = Buffer()
    records.u16(len(layers))
    for layer, lengths in zip(layers, channel_lengths):
        records.append(layer_record(layer, width, height, lengths))
    return records


def image_resource(res_id: int, data: bytes | bytearray | Sequence[int]) -> Buffer:
    """'8BIM' + id + empty name + length + data, padded to even lengths (lib/Psb.js:869-879)."""
    b = Buffer()
    b.ascii("8BIM")
    b.u16(res_id)
    b.u16(0)  # empty Pascal name, padded to even
    b.u32(len(data)).append(data)
    if len(data) % 2:
        b.zeros(1)
    return b


def append_icc_resource(h: Buffer, icc_profile: bytes | bytearray | Sequence[int] | None) -> None:
    """The ICC profile as image resource 1039, or an empty resource section (lib/Psb.js:992-1004)."""
    if icc_profile is None or len(icc_profile) == 0:
        h.u32(0)
        return
    res = image_resource(RESOURCE_ICC_PROFILE, bytes(icc_profile))
    h.u32(res.length()).append(res)


def file_header(width: int, height: int, icc_profile: bytes | bytearray | Sequence[int] | None) -> Buffer:
    """Signature, version, channels, size, depth, mode, colour data and image resources (lib/Psb.js:967-981)."""
    h = Buffer()
    h.ascii(SIGNATURE)
    h.u16(VERSION_PSB)
    h.zeros(6)
    h.u16(3)  # channels in the composite
    h.u32(height)
    h.u32(width)
    h.u16(16)  # bits per sample
    h.u16(COLOR_MODE_RGB)
    h.u32(0)  # colour mode data: none
    append_icc_resource(h, icc_profile)
    return h


def _check_plane_shape(plane: Any, width: int, height: int) -> None:
    shape = np.shape(plane)
    if shape != (height, width):
        raise ValueError(f"plane shape {shape} does not match the document's {height} x {width}")


def _row_blocks(height: int, width: int) -> range:
    return range(0, height, max(1, _BLOCK_SAMPLES // max(width, 1)))


def _write_plane(fh: BinaryIO, plane: Any, width: int, height: int) -> None:
    """One channel as 16-bit big-endian samples, rows top to bottom (the numpy form of lib/Psb.js:823-844).

    Callers validate the plane first (shape and finiteness), so this only writes.
    """
    arr = np.asarray(plane)
    rows = max(1, _BLOCK_SAMPLES // max(width, 1))
    for y0 in range(0, height, rows):
        block = arr[y0 : y0 + rows].astype(np.float64)
        fh.write(np.round(np.clip(block, 0.0, 1.0) * 65535.0).astype(">u2").tobytes())


def _plane_for_channel(planes: Sequence[Any], channel: int) -> Any:
    """A mono plate fills all three channels from its only plane (lib/Psb.js:1035-1037)."""
    return planes[channel] if len(planes) == 3 else planes[0]


def _write_pixel_channels(fh: BinaryIO, planes: Sequence[Any], width: int, height: int) -> None:
    """Three channels, each a compression word then its samples (lib/Psb.js:1028-1039)."""
    for ch in range(3):
        fh.write(bytes(2))  # compression word: raw
        _write_plane(fh, _plane_for_channel(planes, ch), width, height)


def _write_composite(fh: BinaryIO, base: dict[str, Any], width: int, height: int) -> None:
    """One compression word, then the three planes of the stand-in layer (lib/Psb.js:1048-1058)."""
    fh.write(bytes(2))
    for cc in range(3):
        _write_plane(fh, _plane_for_channel(base["planes"], cc), width, height)


def _icc_bytes(icc_profile: bytes | bytearray | Sequence[int] | None) -> bytes | None:
    """The profile as bytes, or None for an empty section. Raises ValueError for values outside 0..255."""
    if icc_profile is None or len(icc_profile) == 0:
        return None
    return bytes(icc_profile)


def _prepare(
    document: Sequence[dict[str, Any]],
    width: int,
    height: int,
    icc_profile: bytes | bytearray | Sequence[int] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[list[int]], bytes | None]:
    """Every check that can fail, before any file is opened.

    Returns the flat layers, the composite stand-in, the channel lengths and the ICC bytes. Raises ValueError
    for a bad size, a pixel layer without one or three planes, a plane of the wrong shape, a non-finite sample
    (NaN or Inf), a document with no pixel layer, or an ICC value outside 0..255.
    """
    width = int(width)
    height = int(height)
    if width < 1 or height < 1:
        raise ValueError(f"document size must be positive, got {width} x {height}")
    icc = _icc_bytes(icc_profile)
    layers = flatten(document)
    for layer in layers:
        if not has_pixels(layer):
            continue
        planes = layer.get("planes")
        if planes is None or len(planes) not in (1, 3):
            raise ValueError(f"pixel layer {layer['name']!r} needs one or three planes")
        for plane in planes:
            _check_plane_shape(plane, width, height)
            arr = np.asarray(plane)
            for y0 in _row_blocks(height, width):
                if not np.isfinite(arr[y0 : y0 + max(1, _BLOCK_SAMPLES // max(width, 1))]).all():
                    raise ValueError(f"pixel layer {layer['name']!r} holds a NaN or infinite sample")
    base = composite_base_layer(layers)
    if base is None:
        raise ValueError("the document has no pixel layer to stand in for the composite")
    channel_lengths = declare_channels(layers, width * height)
    return layers, base, channel_lengths, icc


def _write_document(
    fh: BinaryIO,
    prepared: tuple[list[dict[str, Any]], dict[str, Any], list[list[int]], bytes | None],
    width: int,
    height: int,
) -> None:
    """The whole file in the order of lib/Psb.js:881-931. Input must come from :func:`_prepare`."""
    layers, base, channel_lengths, icc = prepared
    records = layer_records(layers, width, height, channel_lengths)

    channel_data_bytes = sum(lengths[0] * 3 for lengths in channel_lengths)
    layer_info_length = records.length() + channel_data_bytes
    layer_info_pad = 1 if layer_info_length % 2 else 0  # PSB pads the layer info to an even length
    layer_and_mask_length = 8 + layer_info_length + layer_info_pad + 4  # + length word + global mask word

    h = file_header(width, height, icc)
    h.u64(layer_and_mask_length)
    h.u64(layer_info_length + layer_info_pad)
    fh.write(h.to_bytes())

    fh.write(records.to_bytes())
    for layer in layers:
        if has_pixels(layer):
            _write_pixel_channels(fh, layer["planes"], width, height)
        else:
            fh.write(bytes(6))  # three zero-area channels: compression words only
    if layer_info_pad:
        fh.write(bytes(1))

    fh.write(bytes(4))  # global layer mask info: none
    _write_composite(fh, base, width, height)


def write_psb(
    path: str | Path,
    document: Sequence[dict[str, Any]],
    width: int,
    height: int,
    icc_bytes: bytes | bytearray | Sequence[int] | None = None,
) -> Path:
    """Write ``document`` (bottom layer first) as a PSB file at ``path`` (lib/Psb.js:881-932).

    This is the export path. Everything that can fail is checked before the target is touched. The file is
    streamed to a temporary sibling and moved into place with ``os.replace`` only when it is complete, so an
    existing file at ``path`` is left alone on any error, and no partial file is left behind.
    """
    target = Path(path)
    prepared = _prepare(document, width, height, icc_bytes)
    width = int(width)
    height = int(height)
    # O_EXCL on a fresh UUID name: never adopts an existing file. The kernel applies the file-creation mask, so the
    # result has the same permissions as a file created with plain open(); the process-wide mask is not touched.
    tmp_name = str(target.parent / ("." + target.name + "." + uuid.uuid4().hex + ".part"))
    fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as fh:
            _write_document(fh, prepared, width, height)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return target


def psb_bytes(
    document: Sequence[dict[str, Any]],
    width: int,
    height: int,
    icc_bytes: bytes | bytearray | Sequence[int] | None = None,
) -> bytes:
    """The same file as :func:`write_psb` produces, returned as bytes.

    TEST HELPER ONLY. It holds the whole file in memory (about twice its size while building it). The export
    path must use :func:`write_psb`, which streams each channel to disk.
    """
    width = int(width)
    height = int(height)
    prepared = _prepare(document, width, height, icc_bytes)
    buf = io.BytesIO()
    _write_document(buf, prepared, width, height)
    return buf.getvalue()
