"""Output naming and kept results (loomsiril/loom/naming.py) against Loom's JavaScript and its own cases."""

from __future__ import annotations

import os

import pytest

from loomsiril.loom import naming


def test_oracle_ordered_output_keys(oracle) -> None:
    cases = oracle("loom_naming")["cases"]["ordered"]
    assert len(cases) >= 9
    for case in cases:
        assert naming.ordered_output_keys(case["input"]) == case["out"], case["input"]


def test_oracle_unique_name(oracle) -> None:
    cases = oracle("loom_naming")["cases"]["uniqueName"]
    for case in cases:
        taken = set(case["taken"])
        assert naming.unique_name(case["base"], taken.__contains__) == case["out"], case


def test_oracle_publish_id(oracle) -> None:
    cases = oracle("loom_naming")["cases"]["publishId"]
    for case in cases:
        taken = set(case["taken"])
        assert naming.publish_id(case["current"], case["wanted"], taken.__contains__) == case["out"], case


def test_oracle_psb_base_name(oracle) -> None:
    cases = oracle("loom_naming")["cases"]["psbBaseName"]
    for case in cases:
        assert naming.psb_base_name(case["config"]) == case["out"], case["config"]


def test_oracle_export_file_name(oracle) -> None:
    cases = oracle("loom_naming")["cases"]["exportName"]
    for case in cases:
        assert naming.export_file_name(case["name"]) == case["out"], case["name"]


def test_output_order_matches_loom() -> None:
    assert naming.OUTPUT_ORDER[:3] == ("L", "L_starless", "L_stars")
    assert naming.OUTPUT_ORDER[-1] == "HOO_stars"
    assert len(naming.OUTPUT_ORDER) == 17


def test_unknown_outputs_are_placed_after_known_ones() -> None:
    assert naming.ordered_output_keys(["zzz_custom", "L"]) == ["L", "zzz_custom"]


def test_unique_file_name_on_populated_folder(tmp_path) -> None:
    folder = str(tmp_path)
    assert naming.unique_file_name(folder, "L_stars") == os.path.join(folder, "L_stars.fits")
    for stem in ("L_stars.fits", "L_stars_1.fits"):
        (tmp_path / stem).write_bytes(b"")
    assert naming.unique_file_name(folder, "L_stars") == os.path.join(folder, "L_stars_2.fits")
    assert naming.unique_file_name(folder, "L_stars", ext=".xisf") == os.path.join(folder, "L_stars.xisf")


def test_unique_file_name_treats_a_dangling_link_as_taken(tmp_path) -> None:
    link = tmp_path / "RGB.fits"
    try:
        os.symlink(str(tmp_path / "missing-target"), link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    assert naming.unique_file_name(str(tmp_path), "RGB") == os.path.join(str(tmp_path), "RGB_1.fits")


def test_psb_base_name_defaults_to_loom() -> None:
    assert naming.psb_base_name({"projectName": "Rosette"}) == "Rosette"
    assert naming.psb_base_name({}) == "Loom"
    assert naming.psb_base_name(None) == "Loom"


def test_export_name_replaces_every_unsafe_character() -> None:
    assert naming.export_file_name("RGB starless") == "RGB_starless"
    assert naming.export_file_name("\U0001F600") == "__"


def test_main_result_order() -> None:
    assert naming.main_result(["L", "H", "RGB", "SHO_starless"]) == "RGB"
    assert naming.main_result(["L", "H", "SHO_starless", "HOO"]) == "SHO_starless"
    assert naming.main_result(["L_starless", "L", "H"]) == "L_starless"
    assert naming.main_result(["L", "H", "S", "O"]) == "L"
    assert naming.main_result(["H", "S", "O"]) == "H"
    assert naming.main_result(["S", "O"]) == "S"
    assert naming.main_result(["O"]) == "O"
    assert naming.main_result(["HSO", "HOO"]) == "HOO"
    assert naming.main_result(["RGB_starless", "RGB"]) == "RGB_starless"
    assert naming.main_result(["L_stars", "L_stars_low"]) is None


def test_kept_rgb_only() -> None:
    assert naming.kept_results({"L": False, "RGB": True}, {}) == ["RGB"]
    assert naming.kept_results({"RGB": True, "R": True, "G": True, "B": True}, {}) == ["RGB"]


def test_kept_lrgb_with_star_tool() -> None:
    available = {"L": True, "L_stars": True, "RGB": True, "RGB_stars": True}
    assert naming.kept_results(available, {}) == ["L_starless", "L_stars", "RGB_starless", "RGB_stars"]


def test_kept_sho_and_hoo_without_rgb() -> None:
    available = {"SHO": True, "SHO_stars": True, "HOO": True, "HOO_stars": True}
    assert naming.kept_results(available, {}) == [
        "SHO_starless",
        "SHO_stars",
        "HOO_starless",
        "HOO_stars",
    ]


def test_kept_narrowband_only_without_palette() -> None:
    assert naming.kept_results({"H": True, "S": True, "O": True}, {}) == ["H", "O", "S"]
    assert naming.kept_results({"H": True, "S": True, "O": True, "SHO": True}, {}) == ["SHO"]


def test_kept_rgb_with_palettes_and_stars() -> None:
    available = {
        "L": True,
        "RGB": True,
        "RGB_stars": True,
        "SHO": True,
        "SHO_stars": True,
        "HOO": True,
    }
    assert naming.kept_results(available, {}) == [
        "L",
        "RGB_starless",
        "RGB_stars",
        "SHO_starless",
        "HOO",
    ]


def test_palette_stars_kept_when_rgb_has_none() -> None:
    available = {"RGB": True, "SHO": True, "SHO_stars": True}
    assert naming.kept_results(available, {}) == ["RGB", "SHO_starless", "SHO_stars"]


def test_keep_linear_needs_rgb_for_rgb_linear() -> None:
    assert naming.kept_results({"RGB_linear": True}, {"keepLinear": True}) == []
    assert naming.kept_results({"L": True, "RGB_linear": True}, {"keepLinear": True}) == ["L"]


def test_keep_linear_adds_linear_copies() -> None:
    available = {"RGB": True, "RGB_linear": True, "SHO": True, "SHO_linear": True}
    assert naming.kept_results(available, {}) == ["RGB", "SHO"]
    # unknown names follow the known layout order, sorted
    assert naming.kept_results(available, {"keepLinear": True}) == ["RGB", "SHO", "RGB_linear", "SHO_linear"]


def test_separate_l_stars_adds_low_and_high() -> None:
    available = {"L": True, "L_stars": True, "L_stars_low": True, "L_stars_high": True}
    assert naming.kept_results(available, {}) == ["L_starless", "L_stars"]
    assert naming.kept_results(available, {"separateLStars": True}) == [
        "L_starless",
        "L_stars",
        "L_stars_low",
        "L_stars_high",
    ]


def test_kept_list_follows_output_order() -> None:
    available = {"HOO": True, "L": True, "RGB": True, "H": True}
    assert naming.kept_results(available, {}) == ["L", "RGB", "HOO"]
