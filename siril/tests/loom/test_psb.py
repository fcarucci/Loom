"""The PSB writer (loomsiril/loom/psb.py) pinned to Loom's JavaScript (tests/oracle/loom_psb.json).

Byte strings are compared with the hex recorded from lib/Psb.js; the whole 7 x 5 document is compared by length,
sha256 and bytes. A minimal reader written here, independent of the writer, parses the output back.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.loom.psb import (
    BLEND_NORMAL,
    BLEND_PASS_THROUGH,
    DIVIDER_BOUNDING,
    DIVIDER_CLOSED,
    DIVIDER_OPEN,
    HUE_RANGES,
    Buffer,
    composite_base_layer,
    curves_payload,
    declare_channels,
    file_header,
    flatten,
    hue_saturation_payload,
    image_resource,
    layer_record,
    layer_records,
    psb_bytes,
    tagged_block,
    vibrance_payload,
    write_psb,
)
from loomsiril.loom import psb as psb_module

W, H = 7, 5
FIXTURE = Path(__file__).parent / "fixtures_psb" / "golden_7x5.psb.sha256"
ICC = bytes((i * 7 + 3) & 0xFF for i in range(37))


def _sample(seed: int, ch: int, y: int, x: int) -> int:
    return (seed * 7919 + ch * 1237 + y * 131 + x * 17) % 65536


def _plane(seed: int, ch: int) -> np.ndarray:
    """A top-down 5 x 7 float32 plane whose 16-bit values are exactly _sample()."""
    raw = np.array([[_sample(seed, ch, y, x) for x in range(W)] for y in range(H)], dtype=np.float64)
    return (raw / 65535.0).astype(np.float32)


def _samples(seed: int, ch: int) -> np.ndarray:
    return np.array([[_sample(seed, ch, y, x) for x in range(W)] for y in range(H)], dtype=np.int64)


def golden_document() -> list[dict[str, Any]]:
    """The 7 x 5 document: the Python form of the tree recorded in tools/oracle/loom_psb.js."""
    return [
        {"kind": "pixel", "name": "Base", "planes": [_plane(1, c) for c in range(3)]},
        {"kind": "pixel", "name": "Hidden base", "visible": False, "planes": [_plane(2, 0)]},
        {
            "kind": "group",
            "name": "Étoiles",
            "blend": "scrn",
            "open": False,
            "opacity": 128,
            "children": [
                {"kind": "curves", "name": "Curve", "channels": [1]},
                {"kind": "hue", "name": "HS", "clipping": True, "visible": False},
                {"kind": "vibrance", "name": "Vib"},
                {"kind": "pixel", "name": "M42 ° \U0001F600", "planes": [_plane(3, c) for c in range(3)]},
            ],
        },
        {"kind": "pixel", "name": "", "blend": "lum ", "planes": [_plane(4, c) for c in range(3)]},
    ]


def _tree_layers() -> list[dict[str, Any]]:
    return flatten(golden_document())


def _hex(b: Buffer | bytes) -> str:
    return bytes(b.bytes if isinstance(b, Buffer) else b).hex()


@pytest.fixture
def cases(oracle) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return oracle("loom_psb")["cases"]


# ---- tagged blocks, resources, header -----------------------------------------------------------


def test_tagged_blocks_match_javascript(cases: dict[str, Any]) -> None:
    luni = Buffer().unicode_name("Étoiles")
    assert _hex(tagged_block("luni", luni)) == cases["tagged_block_luni"]
    assert _hex(tagged_block("lsct", Buffer().u32(3).ascii("8BIM").ascii("scrn"))) == cases["tagged_block_lsct"]
    assert _hex(tagged_block("curv", curves_payload([1]))) == cases["tagged_block_curv"]
    assert _hex(tagged_block("hue2", hue_saturation_payload())) == cases["tagged_block_hue2"]
    assert _hex(tagged_block("vibA", vibrance_payload())) == cases["tagged_block_vibA"]
    assert _hex(tagged_block("lsct", Buffer().u8(7).u16(0x0102))) == cases["tagged_block_odd"]


def test_unicode_name_matches_javascript(cases: dict[str, Any]) -> None:
    assert _hex(Buffer().unicode_name("M42 ° \U0001F600")) == cases["unicode_name_astral"]


def test_image_resources_match_javascript(cases: dict[str, Any]) -> None:
    assert _hex(image_resource(1039, ICC)) == cases["image_resource_1039"]
    assert _hex(image_resource(1039, [1, 2, 3])) == cases["image_resource_odd"]


def test_file_headers_match_javascript(cases: dict[str, Any]) -> None:
    assert _hex(file_header(W, H, ICC)) == cases["file_header_7x5_icc"]
    assert _hex(file_header(W, H, None)) == cases["file_header_7x5_none"]


def test_adjustment_payloads_match_javascript(cases: dict[str, Any]) -> None:
    assert _hex(curves_payload([1])) == cases["curves_payload_1"]
    assert _hex(curves_payload([2, 3])) == cases["curves_payload_23"]
    assert _hex(curves_payload([0])) == cases["curves_payload_0"]
    assert _hex(curves_payload([0, 1, 2, 3])) == cases["curves_payload_0123"]
    assert _hex(hue_saturation_payload()) == cases["hue_saturation_payload"]
    assert _hex(vibrance_payload()) == cases["vibrance_payload"]


def test_hue_bands_are_the_six_photoshop_ranges() -> None:
    assert len(HUE_RANGES) == 6
    assert HUE_RANGES[0] == [315, 345, 15, 45]


# ---- flattening and layer records ---------------------------------------------------------------


def test_flatten_matches_javascript(cases: dict[str, Any]) -> None:
    layers = _tree_layers()
    expected = cases["flatten_nested"]
    assert len(layers) == len(expected)
    for got, want in zip(layers, expected):
        assert got["name"] == want["name"]
        assert got["divider"] == want["divider"]
        assert got["visible"] == want["visible"]
        assert got["blend"] == want["blend"]
        assert got["opacity"] == want["opacity"]
        assert got["channel_ids"] == want["channel_ids"]
        assert got["curves"] == want["curves"]
        assert got["hue_saturation"] == want["hue_saturation"]
        assert got["vibrance"] == want["vibrance"]
        assert got["clipping"] == want["clipping"]


def test_composite_base_matches_javascript(cases: dict[str, Any]) -> None:
    base = composite_base_layer(_tree_layers())
    assert base is not None and base["name"] == cases["composite_base_name"]
    assert composite_base_layer(flatten([{"kind": "curves", "name": "Curve", "channels": [0]}])) is None
    assert cases["composite_base_none"] is None


def test_channel_lengths_and_records_match_javascript(cases: dict[str, Any]) -> None:
    layers = _tree_layers()
    lengths = declare_channels(layers, W * H)
    assert lengths == cases["declare_channels_lengths"]
    assert _hex(layer_records(layers, W, H, lengths)) == cases["layer_records_nested"]


@pytest.mark.parametrize(
    ("index", "label"),
    [
        (0, "pixel"),
        (1, "hidden_pixel"),
        (2, "bounding_inner"),
        (3, "curves"),
        (4, "hue"),
        (5, "vibrance"),
        (6, "pixel_astral"),
        (7, "group_header"),
        (8, "pixel_lum"),
    ],
)
def test_single_layer_records_match_javascript(cases: dict[str, Any], index: int, label: str) -> None:
    layers = _tree_layers()
    lengths = declare_channels(layers, W * H)
    assert _hex(layer_record(layers[index], W, H, lengths[index])) == cases[f"layer_record_{label}"]


def test_group_header_keeps_the_group_blend_and_closed_state() -> None:
    header = _tree_layers()[7]
    assert header["divider"] == DIVIDER_CLOSED
    assert header["blend"] == "scrn"
    assert header["opacity"] == 128
    bounding = _tree_layers()[2]
    assert bounding["divider"] == DIVIDER_BOUNDING
    assert bounding["blend"] == BLEND_NORMAL
    open_group = flatten([{"kind": "group", "name": "G", "children": []}])
    assert open_group[-1]["divider"] == DIVIDER_OPEN
    assert open_group[-1]["blend"] == BLEND_PASS_THROUGH


# ---- the whole document ---------------------------------------------------------------------------


def test_whole_document_matches_javascript(cases: dict[str, Any]) -> None:
    data = psb_bytes(golden_document(), W, H, ICC)
    assert len(data) == cases["whole_7x5_length"]
    assert hashlib.sha256(data).hexdigest() == cases["whole_7x5_sha256"]
    assert data.hex() == cases["whole_7x5_hex"]


def test_golden_sha256_fixture_matches_javascript(cases: dict[str, Any]) -> None:
    recorded = FIXTURE.read_text(encoding="ascii").split()[0]
    assert recorded == cases["whole_7x5_sha256"]


def test_write_psb_file_matches_in_memory_bytes(tmp_path: Path) -> None:
    target = write_psb(tmp_path / "golden_7x5.psb", golden_document(), W, H, ICC)
    assert target.read_bytes() == psb_bytes(golden_document(), W, H, ICC)


# ---- an independent reader for the bytes the writer produces ------------------------------------


def _u16(d: bytes, p: int) -> int:
    return struct.unpack_from(">H", d, p)[0]


def _u32(d: bytes, p: int) -> int:
    return struct.unpack_from(">I", d, p)[0]


def _u64(d: bytes, p: int) -> int:
    return struct.unpack_from(">Q", d, p)[0]


def _utf16_name(block: bytes) -> str:
    count = _u32(block, 0)
    return block[4 : 4 + 2 * count].decode("utf-16-be", "surrogatepass")


def _read_psb(d: bytes) -> dict[str, Any]:
    assert d[0:4] == b"8BPS"
    assert _u16(d, 4) == 2  # version: PSB
    assert d[6:12] == bytes(6)
    out: dict[str, Any] = {
        "channels": _u16(d, 12),
        "height": _u32(d, 14),
        "width": _u32(d, 18),
        "depth": _u16(d, 22),
        "mode": _u16(d, 24),
    }
    p = 26
    assert _u32(d, p) == 0  # colour mode data
    p += 4
    res_len = _u32(d, p)
    p += 4
    res_end = p + res_len
    out["resources"] = {}
    while p < res_end:
        assert d[p : p + 4] == b"8BIM"
        res_id = _u16(d, p + 4)
        name_len = d[p + 6]
        assert _u16(d, p + 6) == 0  # empty Pascal name, padded to two bytes
        size = _u32(d, p + 8)
        out["resources"][res_id] = d[p + 12 : p + 12 + size]
        p += 12 + size + (size % 2)
    assert p == res_end

    lam_len = _u64(d, p)
    p += 8
    li_len = _u64(d, p)
    p += 8
    li_start = p
    assert lam_len == 8 + li_len + 4  # length word + section + global mask word
    count = _u16(d, p)
    p += 2
    layers = []
    for _ in range(count):
        top, left, bottom, right = (struct.unpack_from(">i", d, p + 4 * k)[0] for k in range(4))
        p += 16
        nch = _u16(d, p)
        p += 2
        chans = []
        for _c in range(nch):
            chans.append((struct.unpack_from(">h", d, p)[0], _u64(d, p + 2)))
            p += 10
        assert d[p : p + 4] == b"8BIM"
        blend = d[p + 4 : p + 8].decode("latin1")
        opacity, clipping, flags, filler = d[p + 8], d[p + 9], d[p + 10], d[p + 11]
        assert filler == 0
        p += 12
        extra_len = _u32(d, p)
        p += 4
        extra_end = p + extra_len
        assert _u32(d, p) == 0 and _u32(d, p + 4) == 0  # no mask, no blending ranges
        p += 8
        name_len = d[p]
        p += 1 + name_len
        p += (4 - (1 + name_len) % 4) % 4
        pascal_len = name_len
        blocks: dict[str, bytes] = {}
        while p < extra_end:
            assert d[p : p + 4] == b"8BIM"
            key = d[p + 4 : p + 8].decode("latin1")
            size = _u32(d, p + 8)
            blocks[key] = d[p + 12 : p + 12 + size]
            p += 12 + size + (size % 2)
        assert p == extra_end
        layers.append(
            {
                "rect": (top, left, bottom, right),
                "channels": chans,
                "blend": blend,
                "opacity": opacity,
                "clipping": clipping,
                "flags": flags,
                "name": _utf16_name(blocks["luni"]),
                "pascal_len": pascal_len,
                "blocks": blocks,
            }
        )

    chan_p = p
    for layer in layers:
        layer["data_offset"] = chan_p
        for _cid, length in layer["channels"]:
            chan_p += length
    pad = li_len - (chan_p - li_start)  # the section's even-length pad, if any
    assert pad in (0, 1)
    p = chan_p + pad
    assert _u32(d, p) == 0  # global mask info
    p += 4
    out["composite_compression"] = _u16(d, p)
    out["composite_end"] = p + 2 + 3 * W * H * 2
    out["layers"] = layers
    out["file_end"] = len(d)
    return out


def _channel_samples(d: bytes, offset: int, width: int, height: int) -> np.ndarray:
    assert _u16(d, offset) == 0
    return np.frombuffer(d, dtype=">u2", count=width * height, offset=offset + 2).reshape(height, width).astype(np.int64)


def test_reader_sees_header_resources_and_layers() -> None:
    d = psb_bytes(golden_document(), W, H, ICC)
    info = _read_psb(d)
    assert (info["channels"], info["height"], info["width"], info["depth"], info["mode"]) == (3, H, W, 16, 3)
    assert info["resources"][1039] == ICC
    assert [layer["name"] for layer in info["layers"]] == [
        "Base",
        "Hidden base",
        "</Layer group>",
        "Curve",
        "HS",
        "Vib",
        "M42 ° \U0001F600",
        "Étoiles",
        "",
    ]
    assert info["file_end"] == info["composite_end"]
    assert info["composite_compression"] == 0


def test_reader_checks_flags_blend_and_divider_blocks() -> None:
    info = _read_psb(psb_bytes(golden_document(), W, H, ICC))
    layers = info["layers"]
    assert layers[1]["flags"] & 0x02  # hidden
    assert layers[2]["flags"] & 0x10  # group marker
    assert layers[7]["blocks"]["lsct"][0:4] == struct.pack(">I", DIVIDER_CLOSED)
    assert layers[7]["blocks"]["lsct"][4:12] == b"8BIMscrn"
    assert layers[7]["blend"] == "scrn"
    assert layers[8]["blend"] == "lum "
    assert "curv" in layers[3]["blocks"] and "hue2" in layers[4]["blocks"] and "vibA" in layers[5]["blocks"]
    assert layers[6]["blocks"]["luni"] != b""
    assert layers[4]["clipping"] == 1


def test_reader_recovers_the_planes_exactly() -> None:
    d = psb_bytes(golden_document(), W, H, ICC)
    info = _read_psb(d)
    base = info["layers"][0]
    for ch, (_cid, length) in enumerate(base["channels"]):
        assert length == 2 + 2 * W * H
        got = _channel_samples(d, base["data_offset"] + sum(x[1] for x in base["channels"][:ch]), W, H)
        assert np.array_equal(got, _samples(1, ch))
    hidden = info["layers"][1]
    mono = _channel_samples(d, hidden["data_offset"], W, H)
    assert np.array_equal(mono, _samples(2, 0))
    comp_start = info["composite_end"] - 3 * W * H * 2
    for cc in range(3):
        plane = np.frombuffer(d, dtype=">u2", count=W * H, offset=comp_start + cc * W * H * 2).reshape(H, W)
        assert np.array_equal(plane.astype(np.int64), _samples(1, cc))


def test_mono_plane_fills_all_three_channels() -> None:
    doc = [{"kind": "pixel", "name": "Mono", "planes": [_plane(5, 0)]}]
    d = psb_bytes(doc, W, H)
    info = _read_psb(d)
    start = info["layers"][0]["data_offset"]
    expected = _samples(5, 0)
    for ch in range(3):
        offset = start + ch * (2 + 2 * W * H)
        assert np.array_equal(_channel_samples(d, offset, W, H), expected)


def test_samples_are_clipped_and_rounded_to_sixteen_bits() -> None:
    plane = np.array([[-0.5, 0.0, 0.5, 1.0, 2.0, 1.0 / 65535.0 * 0.4, 0.99999]] * 5, dtype=np.float32)
    doc = [{"kind": "pixel", "name": "Edge", "planes": [plane]}]
    d = psb_bytes(doc, 7, 5)
    info = _read_psb(d)
    got = _channel_samples(d, info["layers"][0]["data_offset"], 7, 5)
    assert got[0].tolist() == [0, 0, 32768, 65535, 65535, 0, 65534]


def test_document_needs_a_pixel_layer_and_valid_planes() -> None:
    with pytest.raises(ValueError, match="no pixel layer"):
        psb_bytes([{"kind": "curves", "name": "C", "channels": [1]}], W, H)
    with pytest.raises(ValueError, match="shape"):
        psb_bytes([{"kind": "pixel", "name": "P", "planes": [np.zeros((4, 7), np.float32)]}], W, H)
    with pytest.raises(ValueError, match="one or three planes"):
        psb_bytes([{"kind": "pixel", "name": "P", "planes": [_plane(1, 0), _plane(1, 1)]}], W, H)
    with pytest.raises(ValueError, match="unknown layer kind"):
        psb_bytes([{"kind": "text", "name": "T"}], W, H)
    with pytest.raises(ValueError, match="positive"):
        psb_bytes([{"kind": "pixel", "name": "P", "planes": [_plane(1, 0)]}], 0, H)


# ---- failure handling: nothing partial, nothing overwritten ------------------------------------


def _good_document() -> list[dict[str, Any]]:
    return [{"kind": "pixel", "name": "Good", "planes": [_plane(1, c) for c in range(3)]}]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_samples_are_rejected_and_leave_nothing_on_disk(tmp_path: Path, value: float) -> None:
    bad = _plane(1, 0).copy()
    bad[3, 2] = value
    doc = [{"kind": "pixel", "name": "Bad", "planes": [_plane(1, 1), bad, _plane(1, 2)]}]
    with pytest.raises(ValueError, match="NaN or infinite"):
        write_psb(tmp_path / "out.psb", doc, W, H, ICC)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ([{"kind": "pixel", "name": "P", "planes": [np.zeros((4, 7), np.float32)]}], "shape"),
        ([{"kind": "pixel", "name": "P", "planes": [_plane(1, 0), _plane(1, 1)]}], "one or three planes"),
        ([{"kind": "pixel", "name": "P"}], "one or three planes"),
        ([{"kind": "curves", "name": "C", "channels": [1]}], "no pixel layer"),
        ([{"kind": "text", "name": "T"}], "unknown layer kind"),
    ],
)
def test_validation_failures_leave_the_directory_empty(tmp_path: Path, document: list, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        write_psb(tmp_path / "out.psb", document, W, H, ICC)
    assert list(tmp_path.iterdir()) == []


def test_bad_size_and_bad_icc_fail_before_the_file_is_touched(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        write_psb(tmp_path / "out.psb", _good_document(), 0, H)
    with pytest.raises(ValueError):
        write_psb(tmp_path / "out.psb", _good_document(), W, H, [300])
    assert list(tmp_path.iterdir()) == []


def test_existing_file_is_untouched_when_validation_fails(tmp_path: Path) -> None:
    target = tmp_path / "plate.psb"
    write_psb(target, _good_document(), W, H, ICC)
    before = target.read_bytes()
    bad = _plane(1, 0).copy()
    bad[0, 0] = float("nan")
    with pytest.raises(ValueError):
        write_psb(target, [{"kind": "pixel", "name": "Bad", "planes": [bad]}], W, H, ICC)
    assert target.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plate.psb"]


def test_existing_file_is_untouched_when_writing_fails_midway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "plate.psb"
    write_psb(target, _good_document(), W, H, ICC)
    before = target.read_bytes()
    calls = {"n": 0}
    real = psb_module._write_plane

    def failing(fh: Any, plane: Any, width: int, height: int) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        real(fh, plane, width, height)

    monkeypatch.setattr(psb_module, "_write_plane", failing)
    with pytest.raises(OSError, match="disk full"):
        write_psb(target, _good_document(), W, H, ICC)
    assert target.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plate.psb"]


def test_successful_write_replaces_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "plate.psb"
    target.write_bytes(b"old")
    write_psb(target, _good_document(), W, H, ICC)
    assert target.read_bytes() == psb_bytes(_good_document(), W, H, ICC)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plate.psb"]
    assert os.access(target, os.R_OK)


# ---- composite stand-in and long names ----------------------------------------------------------


def test_composite_takes_the_first_visible_pixel_layer(tmp_path: Path) -> None:
    doc = [
        {"kind": "pixel", "name": "Hidden", "visible": False, "planes": [_plane(8, c) for c in range(3)]},
        {"kind": "pixel", "name": "Visible", "planes": [_plane(9, c) for c in range(3)]},
        {"kind": "pixel", "name": "Top", "planes": [_plane(10, c) for c in range(3)]},
    ]
    d = psb_bytes(doc, W, H)
    info = _read_psb(d)
    comp_start = info["composite_end"] - 3 * W * H * 2
    for cc in range(3):
        plane = np.frombuffer(d, dtype=">u2", count=W * H, offset=comp_start + cc * W * H * 2).reshape(H, W)
        assert np.array_equal(plane.astype(np.int64), _samples(9, cc))


def test_composite_falls_back_to_the_first_pixel_layer_when_all_are_hidden() -> None:
    doc = [
        {"kind": "pixel", "name": "A", "visible": False, "planes": [_plane(8, 0)]},
        {"kind": "pixel", "name": "B", "visible": False, "planes": [_plane(9, 0)]},
    ]
    d = psb_bytes(doc, W, H)
    info = _read_psb(d)
    comp_start = info["composite_end"] - 3 * W * H * 2
    plane = np.frombuffer(d, dtype=">u2", count=W * H, offset=comp_start).reshape(H, W)
    assert np.array_equal(plane.astype(np.int64), _samples(8, 0))


def test_long_name_is_cut_to_255_in_pascal_and_kept_whole_in_luni() -> None:
    name = "\u00e9" * 299 + "\U0001F600"  # 301 UTF-16 units
    assert len(name.encode("utf-16-be", "surrogatepass")) // 2 == 301
    d = psb_bytes([{"kind": "pixel", "name": name, "planes": [_plane(1, 0)]}], W, H)
    layer = _read_psb(d)["layers"][0]
    assert layer["pascal_len"] == 255
    assert layer["name"] == name
    assert _u32(layer["blocks"]["luni"], 0) == 301


def test_written_file_has_the_mode_of_a_plain_open(tmp_path: Path) -> None:
    plain = tmp_path / "plain.bin"
    with plain.open("wb") as fh:
        fh.write(b"x")
    target = tmp_path / "plate.psb"
    write_psb(target, _good_document(), W, H, ICC)
    assert (target.stat().st_mode & 0o777) == (plain.stat().st_mode & 0o777)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plain.bin", "plate.psb"]
