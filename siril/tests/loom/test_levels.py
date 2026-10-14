"""loomsiril/loom/levels.py: Loom's tool tables and cache-key parameter builders.

Oracle cases (tests/oracle/loom_levels.json, recorded from Loom's JavaScript by tools/oracle/loom_levels.js) must match
in value AND key order. The new behaviour of the port (Siril denoise, MLDenoise gone, the ghs stretch) is tested
directly.
"""

from __future__ import annotations

from typing import Any

import pytest

from loomsiril.loom import levels as L

CASES: dict[str, Any] = {}
ORACLE_TOOLS = ["NoiseXTerminator", "SyQon Prism", "SyQon Studio Prism Essential", "SyQon Studio Prism 2.0",
                "none", "toString", "Frobnicator"]
LEVELS = ["low", "medium", "high", "none", None, "extreme"]
PASSES = [None, "linear", "stretched", "all"]
NOISE_FLAGS = ["mldenoise", "nxt", "prism", "studio", "prism2Unavailable"]


@pytest.fixture(scope="module")
def cases(oracle: Any) -> dict[str, Any]:
    return oracle("loom_levels")["cases"]


def assert_same(actual: Any, expected: Any, where: str = "root") -> None:
    """Equal in value and in key order, recursively. bool is compared as bool (true is not 1)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: expected a dict, got {type(actual).__name__}"
        assert list(actual.keys()) == list(expected.keys()), f"{where}: key order {list(actual)} != {list(expected)}"
        for k in expected:
            assert_same(actual[k], expected[k], f"{where}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, (list, tuple)), f"{where}: expected a list, got {type(actual).__name__}"
        assert len(actual) == len(expected), f"{where}: length {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            assert_same(a, e, f"{where}[{i}]")
    elif expected is None or isinstance(expected, bool):
        assert actual is expected, f"{where}: {actual!r} is not {expected!r}"
    else:
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"


def _level(key: str) -> Any:
    return None if key == "null" else key


def _no_mldenoise(table: dict[str, Any]) -> dict[str, Any]:
    """Loom's table minus its MLDenoise entry, which the port removes."""
    return {k: v for k, v in table.items() if k != "mldenoise"}


# ---- constants and tables ---------------------------------------------------------------------------------------------


def test_constants_match_loom(cases: dict[str, Any]) -> None:
    for name, value in cases["constants"].items():
        assert_same(getattr(L, name), value, name)


def test_tables_match_loom_except_mldenoise(cases: dict[str, Any]) -> None:
    t = cases["tables"]
    assert_same(L.NOISE_LEVELS, _no_mldenoise(t["NOISE_LEVELS"]), "NOISE_LEVELS")
    assert_same(L.SHARPEN_LEVELS, t["SHARPEN_LEVELS"], "SHARPEN_LEVELS")
    assert_same(L.PRISM2_LADDER_LINEAR, t["PRISM2_LADDER_LINEAR"], "PRISM2_LADDER_LINEAR")
    assert_same(L.PRISM2_LADDER_STRETCHED, t["PRISM2_LADDER_STRETCHED"], "PRISM2_LADDER_STRETCHED")
    assert_same(L.PRISM2_LEVEL_NAMES, t["PRISM2_LEVEL_NAMES"], "PRISM2_LEVEL_NAMES")
    assert_same(L.RGB_FINISHING, t["RGB_FINISHING"], "RGB_FINISHING")
    assert_same(L.PALETTE_FINISHING, t["PALETTE_FINISHING"], "PALETTE_FINISHING")
    assert_same(L.L_FINISHING, t["L_FINISHING"], "L_FINISHING")


def test_prism2_linear_pass_is_off_and_ladder_is_the_stretched_one() -> None:
    assert L.PRISM2_LINEAR_PASS is False
    assert L.NOISE_LEVELS["studio2"] is L.PRISM2_LADDER_STRETCHED


# ---- tool lists -------------------------------------------------------------------------------------------------------


def test_noise_tool_lists_match_loom_minus_mldenoise_plus_siril(cases: dict[str, Any]) -> None:
    n = 0
    for name, rec in cases.items():
        if not name.startswith("noise_tools_from/"):
            continue
        n += 1
        expected = [t for t in rec["tools"] if t != "MLDenoise"] + ["Siril denoise"]
        assert L.noise_tools_from(rec["found"]) == expected, name
    assert n == 2 ** len(NOISE_FLAGS)


def test_sharpen_and_star_tool_lists_match_loom(cases: dict[str, Any]) -> None:
    seen = {"sharpen": 0, "star": 0}
    for name, rec in cases.items():
        if name.startswith("sharpen_tools_from/"):
            seen["sharpen"] += 1
            assert L.sharpen_tools_from(rec["found"]) == rec["tools"], name
        elif name.startswith("star_tools_from/"):
            seen["star"] += 1
            assert L.star_tools_from(rec["found"]) == rec["tools"], name
    assert seen == {"sharpen": 8, "star": 16}


def test_siril_denoise_is_always_offered_and_last() -> None:
    for m in range(2 ** 4):
        found = {k: bool(m & (1 << i)) for i, k in enumerate(["nxt", "prism", "studio", "prism2Unavailable"])}
        tools = L.noise_tools_from(found)
        assert tools[-1] == "Siril denoise" and tools.count("Siril denoise") == 1
    assert L.noise_tools_from({}) == ["Siril denoise"]


def test_mldenoise_is_never_offered_or_produced() -> None:
    for m in range(2 ** len(NOISE_FLAGS)):
        found = {k: bool(m & (1 << i)) for i, k in enumerate(NOISE_FLAGS)}
        assert "MLDenoise" not in L.noise_tools_from(found)
    assert not hasattr(L, "NR_TOOL_MLDENOISE")
    assert "mldenoise" not in L.NOISE_LEVELS
    assert L.noise_amount_for("MLDenoise", "medium") is None
    assert L.denoise_is_linear("MLDenoise") is False
    assert L.noise_levels_for("MLDenoise", True) == ["low", "medium", "high"]


# ---- per-tool tables --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ORACLE_TOOLS)
def test_noise_tool_tables_match_loom(cases: dict[str, Any], tool: str) -> None:
    rec = cases["noise_tool/" + tool]
    assert L.denoise_is_linear(tool) is rec["linear"]
    assert L.noise_levels_for(tool, True) == rec["levels"]["true"]
    assert L.noise_levels_for(tool, False) == rec["levels"]["false"]
    for key in rec["amount"]:
        level = _level(key)
        for i, p in enumerate(PASSES):
            assert_same(L.noise_amount_for(tool, level, p), rec["amount"][key][i], f"{tool}/{key}/{p}")
        assert L.noise_level_name(tool, level) == rec["level_name"][key], f"{tool}/{key}"
        assert L.supported_noise_level(tool, level, True) == rec["supported"][key]["true"], f"{tool}/{key}"
        assert L.supported_noise_level(tool, level, False) == rec["supported"][key]["false"], f"{tool}/{key}"


@pytest.mark.parametrize("tool", ["BlurXTerminator", "SyQon Parallax", "SyQon Studio Parallax",
                                  "SyQon Studio Parallax (correct only)", "none"])
def test_sharpen_tool_tables_match_loom(cases: dict[str, Any], tool: str) -> None:
    rec = cases["sharpen_tool/" + tool]
    for key in rec["stars"]:
        assert_same(L.sharpen_amount_for(tool, "stars", _level(key)), rec["stars"][key], f"{tool}/stars/{key}")
        assert_same(L.sharpen_amount_for(tool, "detail", _level(key)), rec["detail"][key], f"{tool}/detail/{key}")
    assert_same(L.aberration_corrector(tool, True), rec["corrector"]["true"], f"{tool}/corrector/true")
    assert_same(L.aberration_corrector(tool, False), rec["corrector"]["false"], f"{tool}/corrector/false")


def test_studio_family_of_matches_loom(cases: dict[str, Any]) -> None:
    for family, value in cases["studio_family_of"].items():
        assert L.studio_family_of(family) == value


def test_golden_denoise_params_match_loom(cases: dict[str, Any]) -> None:
    for tool, rows in cases["golden/params"].items():
        for level, stretch, which, composite, linear, stretched in rows:
            cfg = {"noiseTool": tool, "noiseLevel": level, "noiseLevelL": "low", "stretch": stretch}
            w = None if which == "undefined" else which
            where = f"{tool}/{level}/{stretch}/{which}"
            assert_same(L.composite_denoise_params(cfg, w), composite, where + "/composite")
            assert_same(L.linear_denoise_params(cfg, w), linear, where + "/linear")
            assert_same(L.stretched_denoise_params(cfg, w), stretched, where + "/stretched")


# ---- configuration matrix ---------------------------------------------------------------------------------------------


def _bundle_cases(cases: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    return [(n, c, c["config"]) for n, c in cases.items() if isinstance(c, dict) and "config" in c]


def test_every_config_bundle_matches_loom(cases: dict[str, Any]) -> None:
    rows = _bundle_cases(cases)
    assert len(rows) >= 100, "the configuration matrix is missing"
    for name, rec, cfg in rows:
        w = name
        assert_same(L.composite_sharpen_params(cfg), rec["composite_sharpen"], w + "/composite_sharpen")
        assert_same(L.aberration_params(cfg, True), rec["aberration"]["true"], w + "/aberration/true")
        assert_same(L.aberration_params(cfg, False), rec["aberration"]["false"], w + "/aberration/false")
        assert_same(L.studio_family(cfg), rec["studio_family"], w + "/studio_family")
        assert_same(L.star_extraction_params(cfg), rec["star_extraction"], w + "/star_extraction")
        assert_same(L.stretch_params(cfg, True), rec["stretch"]["linked"], w + "/stretch/linked")
        assert_same(L.stretch_params(cfg, False), rec["stretch"]["unlinked"], w + "/stretch/unlinked")
        for which, key in ((None, "colour"), ("L", "L")):
            assert_same(L.denoise_level_for(cfg, which), rec["denoise_level"][key], f"{w}/level/{key}")
            assert_same(L.composite_denoise_params(cfg, which), rec["composite_denoise"][key], f"{w}/comp/{key}")
            assert_same(L.linear_denoise_params(cfg, which), rec["linear_denoise"][key], f"{w}/lin/{key}")
            assert_same(L.stretched_denoise_params(cfg, which), rec["stretched_denoise"][key], f"{w}/str/{key}")
        assert L.linear_denoise_pass(cfg) == rec["linear_denoise_pass"], w
        assert_same(L.studio_models_for(cfg, True), rec["studio_models"]["true"], w + "/models/true")
        assert_same(L.studio_models_for(cfg, False), rec["studio_models"]["false"], w + "/models/false")
        assert L.gradient_tool_of(cfg) == rec["gradient_tool"], w
        assert_same(L.gradient_stage_params(cfg), rec["gradient_stage"], w + "/gradient")
        assert_same(L.finishing_params(L.RGB_FINISHING, cfg), rec["finishing"]["RGB"], w + "/fin/RGB")
        assert_same(L.finishing_params(L.PALETTE_FINISHING, cfg), rec["finishing"]["PALETTE"], w + "/fin/PAL")
        assert_same(L.finishing_params(L.L_FINISHING, cfg), rec["finishing"]["L"], w + "/fin/L")


# ---- Siril denoise (new in the port) ----------------------------------------------------------------------------------


def test_siril_denoise_levels_and_amounts() -> None:
    assert L.SIRIL_DENOISE_MOD == {"low": 0.6, "medium": 1.0, "high": 1.0}
    assert [L.noise_amount_for("Siril denoise", lv) for lv in ("low", "medium", "high")] == [0.6, 1.0, 1.0]
    assert L.noise_amount_for("Siril denoise", "none") is None
    assert L.noise_amount_for("Siril denoise", "extreme") is None
    for stretch in (True, False):
        assert L.noise_levels_for("Siril denoise", stretch) == ["low", "medium", "high"]


def test_siril_denoise_runs_in_the_linear_slot() -> None:
    assert L.denoise_is_linear("Siril denoise") is True
    assert L.NR_TOOL_SIRIL == "Siril denoise"
    cfg = {"noiseTool": "Siril denoise", "noiseLevel": "low", "noiseLevelL": "", "stretch": True}
    composite = {"tool": "Siril denoise", "level": "low", "stretched": True, "amount": 0.6}
    assert list(L.composite_denoise_params(cfg, None).items()) == list(composite.items())
    assert list(L.linear_denoise_params(cfg, None).items()) == list(composite.items())
    assert L.stretched_denoise_params(cfg, None) is None
    fin = L.finishing_params(L.RGB_FINISHING, cfg)
    assert "denoiseLinearRGB" in fin and "denoiseRGB" not in fin
    assert fin["denoiseLinearRGB"] == composite


def test_siril_denoise_without_stretch_and_with_l_level() -> None:
    cfg = {"noiseTool": "Siril denoise", "noiseLevel": "medium", "noiseLevelL": "high", "stretch": False}
    assert L.linear_denoise_params(cfg, None) == {"tool": "Siril denoise", "level": "medium", "stretched": False,
                                                  "amount": 1.0}
    assert L.linear_denoise_params(cfg, "L") == {"tool": "Siril denoise", "level": "high", "stretched": False,
                                                 "amount": 1.0}
    assert L.linear_denoise_pass(cfg) == "all"
    assert L.finishing_params(L.L_FINISHING, cfg)["denoiseLinearL"]["amount"] == 1.0


# ---- ghs stretch (new in the port) ------------------------------------------------------------------------------------

GHS_EXPECTED = {"command": "autoghs", "k": 0, "D": 4.5, "b": 13, "hp": 0.7, "lp": 0}


def test_ghs_stretch_params_exact_shape_and_order() -> None:
    cfg = {"stretch": True, "stretchMethod": "ghs", "keepLinear": 1}
    p = L.stretch_params(cfg, True)
    assert list(p.items()) == [("method", "ghs"), ("linked", True), ("keepLinear", True), ("ghs", GHS_EXPECTED)]
    assert list(p["ghs"].items()) == list(GHS_EXPECTED.items())
    unlinked = L.stretch_params({"stretch": True, "stretchMethod": "ghs"}, False)
    assert unlinked == {"method": "ghs", "linked": False, "keepLinear": False, "ghs": GHS_EXPECTED}


def test_ghs_stretch_off_is_none() -> None:
    assert L.stretch_params({"stretch": False, "stretchMethod": "ghs"}, True) is None


def test_ghs_command_linked_and_unlinked_are_exact() -> None:
    assert L.ghs_command(True) == ("autoghs", "-linked", "0", "4.5", "-b=13", "-hp=0.7", "-lp=0")
    assert L.ghs_command(False) == ("autoghs", "0", "4.5", "-b=13", "-hp=0.7", "-lp=0")


def test_unknown_stretch_method_raises_value_error() -> None:
    with pytest.raises(ValueError):
        L.stretch_params({"stretch": True, "stretchMethod": "MultiscaleAdaptiveStretch"}, True)
    with pytest.raises(ValueError):
        L.stretch_params({"stretch": True, "stretchMethod": "foo"}, False)


def test_ghs_keys_differ_from_mtf_keys() -> None:
    mtf = L.stretch_params({"stretch": True, "stretchMethod": "mtf"}, True)
    ghs = L.stretch_params({"stretch": True, "stretchMethod": "ghs"}, True)
    assert set(mtf) != set(ghs)
    assert list(mtf) == ["target", "linked", "keepLinear", "blackPoint"]
    assert "ghs" in ghs and "target" not in ghs


def test_ghs_flows_through_the_finishing_tail() -> None:
    cfg = {"stretch": True, "stretchMethod": "ghs", "keepLinear": False}
    fin = L.finishing_params(L.RGB_FINISHING, cfg)
    assert fin == {"stretchRGB": {"method": "ghs", "linked": True, "keepLinear": False, "ghs": GHS_EXPECTED}}
    assert list(L.finishing_params(L.L_FINISHING, cfg)) == ["stretchL"]
    assert L.finishing_params(L.L_FINISHING, cfg)["stretchL"]["linked"] is False


# ---- small contracts --------------------------------------------------------------------------------------------------


def test_config_defaults_are_the_read_fields_at_loom_defaults() -> None:
    from loomsiril.loom import config as config_mod

    defaults = config_mod.defaults()
    assert defaults["noiseLevel"] == "medium"
    assert defaults["stretchMethod"] == "mtf"
    assert defaults["gradientTool"] == "GraXpert"


def test_gradient_tool_resolution() -> None:
    assert L.gradient_tool_of({"gradientTool": "SyQon Studio Deep Gradient"}) == "SyQon Studio Deep Gradient"
    assert L.gradient_tool_of({"gradientTool": "", "useGraXpert": True}) == "GraXpert"
    assert L.gradient_tool_of({"useGraXpert": True}) == "GraXpert"
    assert L.gradient_tool_of({"useGraXpert": False}) == "none"
    assert L.gradient_tool_of({}) == "none"
    assert L.gradient_stage_params({"gradientTool": "SyQon Studio Deep Gradient", "smoothing": 0.5}) == {
        "enabled": True, "tool": "SyQon Studio Deep Gradient", "model": "deep-gradient"}


def test_prism2_default_flag_keeps_prism2_out_of_the_linear_slot() -> None:
    assert L.denoise_is_linear("SyQon Studio Prism 2.0") is False
    assert L.denoise_is_linear("SyQon Studio Prism 2.0", prism2_linear_pass=True) is True
    assert L.noise_levels_for("SyQon Studio Prism 2.0", False) == ["medium"]


def test_stretch_target_defaults_to_loom_and_follows_a_valid_option() -> None:
    from loomsiril.loom import levels as lv
    base = {"stretch": True, "stretchMethod": lv.STRETCH_METHOD_MTF}
    assert lv.stretch_params(base, True)["target"] == 0.25
    assert lv.stretch_params({**base, "stretchTarget": 0.15}, True)["target"] == 0.15
    for bad in (0, 1.5, "x", True, None):
        assert lv.stretch_params({**base, "stretchTarget": bad}, True)["target"] == 0.25
