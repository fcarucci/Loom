"""The layered PSB document builder agrees with Loom's buildPsbDocument (oracle) and its trees write as PSB files."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.loom import psb_document as pd
from loomsiril.loom.psb import BLEND_LUMINOSITY, BLEND_SCREEN, BLEND_SOFT_LIGHT, flatten, write_psb

W, H = 7, 5


def _results(cases: dict[str, Any], name: str) -> dict[str, str]:
    """The result mapping for a recorded set: each handle is the result name itself."""
    return {n: n for n in cases["result_sets"][name]}


def _plane(handle: str, channel: int) -> np.ndarray:
    """Deterministic synthetic samples in [0, 1] for one handle and channel, top-down, shape (H, W)."""
    rng = np.random.default_rng(zlib.crc32(f"{handle}:{channel}".encode("utf-8")))
    return rng.random((H, W))


def _planes_for(handle: Any) -> list[np.ndarray]:
    """Colour plates carry three planes, the mono plates one (the writer copies it to all three channels)."""
    if str(handle).startswith("RGB") or str(handle) in ("HOO", "SHO_starless", "HSO_starless", "HOO_starless"):
        return [_plane(str(handle), c) for c in range(3)]
    return [_plane(str(handle), 0)]


# ---- agreement with the JavaScript oracle ---------------------------------------------------------------------


def test_document_trees_match_javascript(oracle) -> None:
    cases = oracle("loom_psb_document")["cases"]
    for name in cases["result_sets"]:
        built = pd.build_psb_document(_results(cases, name))
        assert built == cases["documents"][name], f"document tree differs for result set {name!r}"


def test_palette_names_and_palette_name_match_javascript(oracle) -> None:
    cases = oracle("loom_psb_document")["cases"]
    for name in cases["result_sets"]:
        results = _results(cases, name)
        assert pd.palette_names(results) == cases["palette_names"][name], name
        assert pd.palette_name(results) == cases["palette_name"][name], name


def test_star_layers_and_l_stars_layer_match_javascript(oracle) -> None:
    cases = oracle("loom_psb_document")["cases"]
    for name in cases["result_sets"]:
        results = _results(cases, name)
        assert pd.star_layers(results) == cases["star_layers"][name], name
        assert pd.l_stars_layer(results) == cases["l_stars_layer"][name], name


def test_palette_groups_match_javascript(oracle) -> None:
    cases = oracle("loom_psb_document")["cases"]
    for key, expected in cases["palette_group"].items():
        set_name, _, palette = key.partition("|")
        got = pd.palette_group(_results(cases, set_name), palette or None)
        assert got == expected, key


def test_broadband_groups_match_javascript(oracle) -> None:
    cases = oracle("loom_psb_document")["cases"]
    for key, expected in cases["broadband_group"].items():
        set_name, _, shown = key.partition("|")
        got = pd.broadband_group(_results(cases, set_name), shown == "true")
        assert got == expected, key


def test_palette_channels_match_javascript(oracle) -> None:
    cases = oracle("loom_psb_document")["cases"]
    for key, expected in cases["palette_channels"].items():
        palette, _, line = key.partition("|")
        assert pd.palette_channels_of(pd.PALETTES[palette], line) == expected, key


def test_curve_layer_names_match_javascript(oracle) -> None:
    cases = oracle("loom_psb_document")["cases"]
    for key, expected in cases["curve_layer_name"].items():
        label, _, ids = key.partition("|")
        assert pd.curve_layer_name(label, [int(c) for c in ids.split(",")]) == expected, key


def test_dso_adjustments_match_javascript(oracle) -> None:
    cases = oracle("loom_psb_document")["cases"]
    assert pd.dso_adjustments() == cases["dso_adjustments"]


def test_assert_plates_share_size_matches_javascript(oracle) -> None:
    cases = oracle("loom_psb_document")["cases"]
    for key, case in cases["assert_plates_share_size"].items():
        sizes = [tuple(s) for s in case["sizes"]]
        if case["message"] is None:
            pd.assert_plates_share_size(sizes, case["width"], case["height"])
        else:
            with pytest.raises(ValueError) as err:
                pd.assert_plates_share_size(sizes, case["width"], case["height"])
            assert str(err.value) == case["message"], key


# ---- behaviour the oracle does not pin directly ----------------------------------------------------------------


def test_handles_are_carried_through_unchanged() -> None:
    sentinel = object()
    doc = pd.build_psb_document({"RGB_starless": sentinel, "L_stars": "stars-handle"})
    rgb = doc[0]["group"][0]
    assert rgb["window"] is sentinel
    lum = [n for n in doc if n["name"] == "Stars"][0]["group"][0]
    assert lum["window"] == "stars-handle"


def test_empty_results_give_an_empty_document() -> None:
    assert pd.build_psb_document({}) == []


def test_palette_order_is_the_palette_table_order() -> None:
    doc = pd.build_psb_document({"SHO_starless": "a", "HSO_starless": "b", "HOO_starless": "c"})
    palette_groups = [n["name"] for n in doc if n["group"] is not None and n["name"] in pd.PALETTES]
    assert palette_groups == ["SHO", "HOO", "HSO"]
    assert [n["visible"] for n in doc[:3]] == [True, False, False]


def test_broadband_is_hidden_behind_a_palette_and_shown_alone() -> None:
    with_palette = pd.build_psb_document({"RGB_starless": "r", "HOO": "h"})
    alone = pd.build_psb_document({"RGB_starless": "r"})
    assert [n for n in with_palette if n["name"] == "RGB"][0]["visible"] is False
    assert [n for n in alone if n["name"] == "RGB"][0]["visible"] is True


def test_adjustments_only_when_a_palette_or_broadband_exists() -> None:
    names = [n["name"] for n in pd.build_psb_document({"L_stars": "l"})]
    assert "Background Curve" not in names and names == ["Stars", "Stars Curve"]
    names = [n["name"] for n in pd.build_psb_document({"RGB": "r"})]
    assert names[-3:] == ["Background Curve", "Faint Nebulosity Curve", "Color Vibrance"]


def test_stars_group_is_screen_and_its_curve_is_clipped() -> None:
    doc = pd.build_psb_document({"RGB": "r", "RGB_stars": "s"})
    stars, curve = doc[-2], doc[-1]
    assert (stars["name"], stars["blend"]) == ("Stars", BLEND_SCREEN)
    assert [c["name"] for c in stars["group"]] == ["RGB_stars", "Stars Saturation"]
    assert stars["group"][1]["hue_saturation"] is True and stars["group"][1]["clipping"] is True
    assert (curve["name"], curve["curves"], curve["clipping"]) == ("Stars Curve", [0], True)


def test_separated_l_stars_use_soft_light_on_the_high_half() -> None:
    layer = pd.l_stars_layer({"L_stars_low": "lo", "L_stars_high": "hi", "L_stars": "plain"})
    assert layer["name"] == "L Stars" and layer["blend"] == "pass"
    low, high = layer["group"]
    assert (low["name"], low["visible"]) == ("L_stars_low", False)
    assert (high["name"], high["blend"]) == ("L_stars_high", BLEND_SOFT_LIGHT)


def test_plain_l_stars_use_luminosity() -> None:
    layer = pd.l_stars_layer({"L_stars": "l"})
    assert (layer["name"], layer["blend"], layer["window"]) == ("L_stars", BLEND_LUMINOSITY, "l")


def test_to_psb_tree_maps_each_kind_and_omits_default_blend() -> None:
    doc = pd.build_psb_document({"RGB": "r", "RGB_stars": "s", "L_stars": "l", "HOO": "h"})
    tree = pd.to_psb_tree(doc, _planes_for)

    def walk(nodes: list[dict[str, Any]]) -> list[str]:
        kinds: list[str] = []
        for node in nodes:
            kinds.append(node["kind"])
            if node["kind"] == "group":
                kinds.extend(walk(node["children"]))
        return kinds

    kinds = walk(tree)
    assert set(kinds) == {"group", "pixel", "curves", "hue", "vibrance"}
    hue = [n for n in pd.to_psb_tree(doc, _planes_for) if n["name"] == "Stars"][0]["children"][1]
    assert hue["kind"] == "hue" and "blend" not in hue
    lum = [n for n in tree if n["name"] == "Stars"][0]["children"][-1]
    assert lum["blend"] == BLEND_LUMINOSITY and lum["planes"] and lum["kind"] == "pixel"


def test_psb_profile_or_none() -> None:
    assert pd.psb_profile_or_none(None) is None
    assert pd.psb_profile_or_none(b"") is None
    assert pd.psb_profile_or_none(bytearray(b"\x01\x02")) == b"\x01\x02"
    assert pd.psb_profile_or_none([3, 4]) == bytes([3, 4])


# ---- the trees are accepted by the PSB writer -------------------------------------------------------------------


def _record_count(nodes: list[dict[str, Any]]) -> int:
    """Records the writer emits for a tree: one per leaf, and a bounding divider plus header per group."""
    total = 0
    for node in nodes:
        if node["kind"] == "group":
            total += 2 + _record_count(node["children"])
        else:
            total += 1
    return total


WRITABLE_SETS = [
    "rgb_only",
    "rgb_with_stars",
    "rgb_and_sho",
    "sho_hoo_hso_no_rgb",
    "palette_with_stars",
    "l_stars_separated_and_plain",
    "nothing_but_l",
]


@pytest.mark.parametrize("name", WRITABLE_SETS)
def test_to_psb_tree_is_written_as_a_psb_file(oracle, tmp_path: Path, name: str) -> None:
    cases = oracle("loom_psb_document")["cases"]
    tree = pd.to_psb_tree(pd.build_psb_document(_results(cases, name)), _planes_for)
    target = write_psb(tmp_path / f"{name}.psb", tree, W, H)

    data = target.read_bytes()
    assert data[:4] == b"8BPS"
    assert struct.unpack(">H", data[4:6]) == (2,)  # PSB version
    assert struct.unpack(">H", data[12:14]) == (3,)  # channels in the composite
    assert struct.unpack(">I", data[14:18]) == (H,)
    assert struct.unpack(">I", data[18:22]) == (W,)
    assert struct.unpack(">H", data[22:24]) == (16,)  # bits per sample
    assert struct.unpack(">H", data[24:26]) == (3,)  # RGB

    # Walk the length words to the layer count: colour mode data, image resources, layer and mask info, layer info.
    pos = 26
    (colour_len,) = struct.unpack(">I", data[pos : pos + 4])
    pos += 4 + colour_len
    (resources_len,) = struct.unpack(">I", data[pos : pos + 4])
    pos += 4 + resources_len
    pos += 8  # layer and mask information length (8 bytes in PSB)
    pos += 8  # layer info length
    (layer_count,) = struct.unpack(">H", data[pos : pos + 2])
    assert layer_count == _record_count(tree)

    for node in tree:
        assert node["name"].encode("utf-16-be") in data, node["name"]
    assert list(tmp_path.glob(".*.part")) == []


@pytest.mark.parametrize(
    "name, keys",
    [
        ("rgb_with_stars", [b"8BIMscrn"]),
        ("nothing_but_l", [b"8BIMscrn", b"8BIMlum "]),
        ("l_stars_separated_and_plain", [b"8BIMpass", b"8BIMsLit"]),
    ],
)
def test_written_blend_keys_follow_the_tree(oracle, tmp_path: Path, name: str, keys: list[bytes]) -> None:
    cases = oracle("loom_psb_document")["cases"]
    tree = pd.to_psb_tree(pd.build_psb_document(_results(cases, name)), _planes_for)
    data = write_psb(tmp_path / f"{name}.psb", tree, W, H).read_bytes()
    for key in keys:
        assert key in data, key


def test_nothing_without_a_pixel_layer_is_refused(oracle, tmp_path: Path) -> None:
    cases = oracle("loom_psb_document")["cases"]
    tree = pd.to_psb_tree(pd.build_psb_document(_results(cases, "nothing")), _planes_for)
    assert tree == []
    with pytest.raises(ValueError):
        write_psb(tmp_path / "empty.psb", tree, W, H)
    assert not (tmp_path / "empty.psb").exists()


# ---- what to_psb_tree must keep: visibility, clipping and curve channels ------------------------------------------


def _flags(nodes: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """(kind, name, visible, clipping, channels) for every node, depth first, in document order."""
    out: list[tuple[Any, ...]] = []
    for node in nodes:
        channels = tuple(node["channels"]) if node["kind"] == "curves" else None
        out.append((node["kind"], node["name"], node["visible"], node["clipping"], channels))
        if node["kind"] == "group":
            out.extend(_flags(node["children"]))
    return out


def test_to_psb_tree_keeps_visibility_clipping_and_curve_channels() -> None:
    # SHO is first and shown; HOO is a second palette, hidden. Its OIII curve covers green and blue ([2, 3]).
    # The stars carry a clipped saturation layer and a clipped Stars Curve.
    results = {"SHO_starless": "a", "HOO_starless": "b", "RGB_stars": "c", "L_stars": "d"}
    tree = pd.to_psb_tree(pd.build_psb_document(results), _planes_for)
    assert _flags(tree) == [
        ("group", "SHO", True, False, None),
        ("pixel", "SHO_starless", True, False, None),
        ("curves", "OIII[B]", True, False, (3,)),
        ("curves", "SII[R]", True, False, (1,)),
        ("curves", "Ha[G]", True, False, (2,)),
        ("group", "HOO", False, False, None),
        ("pixel", "HOO_starless", True, False, None),
        ("curves", "OIII[G,B]", True, False, (2, 3)),
        ("curves", "Ha[R]", True, False, (1,)),
        ("curves", "Background Curve", True, False, (0,)),
        ("curves", "Faint Nebulosity Curve", True, False, (0,)),
        ("vibrance", "Color Vibrance", True, False, None),
        ("group", "Stars", True, False, None),
        ("pixel", "RGB_stars", True, False, None),
        ("hue", "Stars Saturation", True, True, None),
        ("pixel", "L_stars", True, False, None),
        ("curves", "Stars Curve", True, True, (0,)),
    ]


def test_hidden_and_clipped_flags_reach_the_written_records(tmp_path: Path) -> None:
    results = {"SHO_starless": "a", "HOO_starless": "b", "RGB_stars": "c"}
    tree = pd.to_psb_tree(pd.build_psb_document(results), _planes_for)
    flat = flatten(tree)
    headers = {rec["name"]: rec for rec in flat if rec["divider"] is not None}
    leaves = {rec["name"]: rec for rec in flat if rec["divider"] is None}
    assert headers["HOO"]["visible"] is False and headers["SHO"]["visible"] is True
    assert leaves["HOO_starless"]["visible"] is True
    assert leaves["Stars Curve"]["clipping"] is True
    assert leaves["Stars Saturation"]["clipping"] is True
    assert leaves["OIII[G,B]"]["curves"] == [2, 3]
    assert leaves["Stars Saturation"]["hue_saturation"] is True
    assert len(flat) == _record_count(tree)
    write_psb(tmp_path / "hidden.psb", tree, W, H)


def test_none_counts_as_absent() -> None:
    assert pd.build_psb_document({"RGB": None}) == []
    assert pd.build_psb_document({"L_stars": None, "RGB_stars": None}) == []
    assert pd.palette_names({"SHO_starless": None, "HOO": "h"}) == ["HOO"]
    assert pd.palette_name({"SHO": None}) is None
    assert pd.broadband_group({"RGB_starless": None, "RGB": "r"}, True)["group"][0]["window"] == "r"
    assert pd.l_stars_layer({"L_stars_low": "lo", "L_stars_high": None}) is None
    assert pd.l_stars_layer({"L_stars_low": None, "L_stars_high": "hi", "L_stars": "l"})["name"] == "L_stars"


def test_plates_share_size_defaults_to_the_first_plate() -> None:
    pd.assert_plates_share_size([(7, 5), (7, 5), (7, 5)])
    pd.assert_plates_share_size([(7, 5)])
    with pytest.raises(ValueError) as err:
        pd.assert_plates_share_size([(7, 5), (6, 5)])
    assert str(err.value) == "plates differ in size (6x5 against 7x5); they cannot share one document"
    with pytest.raises(ValueError):
        pd.assert_plates_share_size([(7, 5), (7, 5), (7, 4)])
