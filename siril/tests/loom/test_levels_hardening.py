"""Hardening tests for the tool tables and parameter builders (loomsiril/loom/levels.py, config.py).

The oracle cases added to tests/oracle/loom_levels.json (recorded from Loom's JavaScript by tools/oracle/loom_levels.js)
are replayed here: Prism 2.0 with its linear pass on, a level outside low, medium and high, "none" strengths, a
configuration without `stretch`, several Studio tools at once, and the truthy values of the legacy gradient checkbox.
"""

from __future__ import annotations

from typing import Any

import pytest

import loomsiril.loom.config as config_mod
from loomsiril.loom import levels as L
from loomsiril.loom.cache import chain_key

PASSES = [None, "linear", "stretched", "all"]
LEVELS = ["low", "medium", "high", "none", None, "extreme"]
STUDIO2 = L.NR_TOOL_STUDIO2


@pytest.fixture(scope="module")
def cases(oracle: Any) -> dict[str, Any]:
    return oracle("loom_levels")["cases"]


def _same(actual: Any, expected: Any, where: str = "root") -> None:
    """Equal in value and in key order, recursively. bool is compared as bool (true is not 1), null as None."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: expected a dict, got {type(actual).__name__}"
        assert list(actual.keys()) == list(expected.keys()), f"{where}: key order {list(actual)} != {list(expected)}"
        for k in expected:
            _same(actual[k], expected[k], f"{where}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, (list, tuple)), f"{where}: expected a list, got {type(actual).__name__}"
        assert len(actual) == len(expected), f"{where}: length {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            _same(a, e, f"{where}[{i}]")
    elif expected is None or isinstance(expected, bool):
        assert actual is expected, f"{where}: {actual!r} is not {expected!r}"
    else:
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"


def _replay(cfg: dict[str, Any], prism2: bool) -> dict[str, Any]:
    """Every builder the oracle bundles record, on one configuration, with Prism 2.0's linear pass as given."""
    kw = {"prism2_linear_pass": prism2}
    got: dict[str, Any] = {
        "composite_sharpen": L.composite_sharpen_params(cfg),
        "aberration": {"true": L.aberration_params(cfg, True), "false": L.aberration_params(cfg, False)},
        "studio_family": L.studio_family(cfg),
        "star_extraction": L.star_extraction_params(cfg),
        "stretch": {"linked": L.stretch_params(cfg, True), "unlinked": L.stretch_params(cfg, False)},
        "denoise_level": {"colour": L.denoise_level_for(cfg, None, **kw), "L": L.denoise_level_for(cfg, "L", **kw)},
        "composite_denoise": {"colour": L.composite_denoise_params(cfg, None, **kw),
                              "L": L.composite_denoise_params(cfg, "L", **kw)},
        "linear_denoise": {"colour": L.linear_denoise_params(cfg, None, **kw),
                           "L": L.linear_denoise_params(cfg, "L", **kw)},
        "stretched_denoise": {"colour": L.stretched_denoise_params(cfg, None, **kw),
                              "L": L.stretched_denoise_params(cfg, "L", **kw)},
        "linear_denoise_pass": L.linear_denoise_pass(cfg),
        "studio_models": {"true": L.studio_models_for(cfg, True, **kw), "false": L.studio_models_for(cfg, False, **kw)},
        "gradient_tool": L.gradient_tool_of(cfg),
        "gradient_stage": L.gradient_stage_params(cfg),
        "finishing": {"RGB": L.finishing_params(L.RGB_FINISHING, cfg, **kw),
                      "PALETTE": L.finishing_params(L.PALETTE_FINISHING, cfg, **kw),
                      "L": L.finishing_params(L.L_FINISHING, cfg, **kw)},
    }
    return got


def _check_bundle(name: str, rec: dict[str, Any], prism2: bool) -> None:
    got = _replay(rec["config"], prism2)
    for key, value in got.items():
        _same(value, rec[key], f"{name}/{key}")


# ---- Prism 2.0 with its linear pass on (Steps.setPrism2LinearPass) ---------------------------------------------------


def test_prism2_linear_pass_flag_and_tables_match_loom(cases: dict[str, Any]) -> None:
    assert cases["prism2_linear_pass/flag"] is True
    assert cases["prism2_linear_pass/denoise_is_linear"] is True
    assert L.denoise_is_linear(STUDIO2, prism2_linear_pass=True) is True
    assert L.denoise_is_linear(STUDIO2) is False  # the port's default is Loom's default at load time
    _same(L.noise_levels_for(STUDIO2, True, prism2_linear_pass=True), cases["prism2_linear_pass/levels"]["true"])
    _same(L.noise_levels_for(STUDIO2, False, prism2_linear_pass=True), cases["prism2_linear_pass/levels"]["false"])


def test_prism2_linear_pass_amounts_and_supported_levels_match_loom(cases: dict[str, Any]) -> None:
    amounts = cases["prism2_linear_pass/amount"]
    supported = cases["prism2_linear_pass/supported"]
    for lv in LEVELS:
        key = "null" if lv is None else str(lv)  # String(null) is "null" in the recorder
        _same([L.noise_amount_for(STUDIO2, lv, p, prism2_linear_pass=True) for p in PASSES], amounts[key], "amount/" + key)
        _same({"true": L.supported_noise_level(STUDIO2, lv, True, prism2_linear_pass=True),
               "false": L.supported_noise_level(STUDIO2, lv, False, prism2_linear_pass=True)},
              supported[key], "supported/" + key)


@pytest.mark.parametrize("name", [
    "linear_pass/noise/level=low/stretch=true",
    "linear_pass/noise/level=low/stretch=false",
    "linear_pass/noise/level=medium/stretch=true",
    "linear_pass/noise/level=medium/stretch=false",
    "linear_pass/noise/level=high/stretch=true",
    "linear_pass/noise/level=high/stretch=false",
    "linear_pass/noise/level=extreme/stretch=true",
    "linear_pass/noise/level=extreme/stretch=false",
    "linear_pass/noise/levelL=high/level=medium/stretch=true",
])
def test_prism2_linear_pass_configurations_match_loom(cases: dict[str, Any], name: str) -> None:
    rec = cases[name]
    assert rec["prism2_linear_pass"] is True
    _check_bundle(name, rec["bundle"], prism2=True)


def test_prism2_linear_pass_is_restored_after_the_recording(cases: dict[str, Any]) -> None:
    # the recorder sets the pass on and restores it in finally: every later case was recorded with it off
    assert L.denoise_is_linear(STUDIO2) is False
    assert cases["noise/SyQon Studio Prism 2.0/level=high/levelL=/stretch=true"]["linear_denoise_pass"] == "linear"


# ---- a level outside low, medium and high, and "none" for the strength and the L strength ----------------------------


@pytest.mark.parametrize("tool", [L.NR_TOOL_NXT, L.NR_TOOL_PRISM, L.NR_TOOL_STUDIO, STUDIO2])
@pytest.mark.parametrize("stretch", [True, False])
def test_none_strength_configurations_match_loom(cases: dict[str, Any], tool: str, stretch: bool) -> None:
    name = f"noise/{tool}/level=none/levelL=none/stretch={'true' if stretch else 'false'}"
    _check_bundle(name, cases[name], prism2=False)


@pytest.mark.parametrize("tool", [L.NR_TOOL_NXT, L.NR_TOOL_PRISM, L.NR_TOOL_STUDIO, STUDIO2])
def test_extreme_level_configurations_match_loom(cases: dict[str, Any], tool: str) -> None:
    name = f"noise/{tool}/level=extreme/levelL=extreme/stretch=true"
    _check_bundle(name, cases[name], prism2=False)


def test_extreme_is_passed_through_and_has_no_amount() -> None:
    for tool in (L.NR_TOOL_NXT, L.NR_TOOL_PRISM, L.NR_TOOL_STUDIO, STUDIO2):
        assert L.supported_noise_level(tool, "extreme", True) == "extreme"
        assert L.noise_amount_for(tool, "extreme") is None
        assert L.noise_amount_for(tool, "none") is None


# ---- a configuration without `stretch` ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["stretch/absent/noise=studio2", "stretch/absent/noise=prism"])
def test_configuration_without_stretch_matches_loom(cases: dict[str, Any], name: str) -> None:
    rec = cases[name]
    assert "stretch" not in rec["config"]  # Loom's undefined: the key is absent
    _check_bundle(name, rec, prism2=False)


def test_absent_stretch_is_off_for_the_stretch_but_on_for_the_noise_levels() -> None:
    cfg: dict[str, Any] = {"noiseTool": STUDIO2, "noiseLevel": "high"}
    assert L.stretch_params(cfg, True) is None  # !config.stretch
    assert L.noise_levels_for(STUDIO2, None) == L.noise_levels_for(STUDIO2, True)
    assert L.noise_levels_for(STUDIO2, None) != L.noise_levels_for(STUDIO2, False)  # only an explicit false is off


# ---- several Studio tools at once: the order of studioModelsFor ------------------------------------------------------


@pytest.mark.parametrize("name", ["studio_models/all", "studio_models/all_studio2"])
def test_several_studio_tools_at_once_match_loom_order(cases: dict[str, Any], name: str) -> None:
    rec = cases[name]
    _check_bundle(name, rec, prism2=False)
    expected = rec["studio_models"]["true"]
    assert len(expected) == len(set(expected))
    assert L.studio_models_for(rec["config"], True) == expected


def test_studio_models_order_is_pipeline_order() -> None:
    cfg = {"gradientTool": L.GRADIENT_TOOL_STUDIO, "sharpenTool": L.SHARPEN_TOOL_BXT, "starTool": L.STAR_TOOL_STUDIO,
           "noiseTool": L.NR_TOOL_STUDIO, "stretch": True}
    assert L.studio_models_for(cfg, True) == ["deep-gradient", "parallax", "axiom", "prism-essential"]
    assert L.studio_models_for(cfg, False) == ["deep-gradient", "axiom", "prism-essential"]


# ---- the legacy gradient checkbox with truthy values that are not true ----------------------------------------------


@pytest.mark.parametrize("name", ["gradient/legacy/string_true", "gradient/legacy/one"])
def test_legacy_truthy_gradient_flag_is_none_as_in_loom(cases: dict[str, Any], name: str) -> None:
    rec = cases[name]
    assert rec["gradient_tool"] == "none"
    assert L.gradient_tool_of(rec["config"]) == "none"
    assert L.gradient_tool_of({"gradientTool": "", "useGraXpert": True}) == "GraXpert"


# ---- the Siril stretch: a returned parameter dict is a copy, and its key differs from the MAS shape ----------------


def test_ghs_params_returned_are_a_copy_of_ghs_params() -> None:
    before = dict(L.GHS_PARAMS)
    p = L.stretch_params({"stretch": True, "stretchMethod": "ghs"}, True)
    assert p is not None
    p["ghs"]["k"] = 99
    p["ghs"]["extra"] = 1
    p["linked"] = False
    assert L.GHS_PARAMS == before
    fresh = L.stretch_params({"stretch": True, "stretchMethod": "ghs"}, True)
    assert fresh["ghs"] == before
    assert fresh["linked"] is True


def test_chain_key_of_ghs_params_differs_from_a_mas_shaped_dict() -> None:
    ghs = L.stretch_params({"stretch": True, "stretchMethod": "ghs", "keepLinear": False}, True)
    mas = {"method": "MultiscaleAdaptiveStretch", "linked": True, "keepLinear": False,
           "mas": {"target": 0.25, "linked": True, "keepLinear": False}}
    assert chain_key("prev", "stretchRGB", ghs) != chain_key("prev", "stretchRGB", mas)
    assert chain_key("prev", "stretchRGB", ghs) == chain_key("prev", "stretchRGB", dict(ghs))


def test_stretch_off_returns_none_whatever_the_method() -> None:
    assert L.stretch_params({"stretch": False, "stretchMethod": "MultiscaleAdaptiveStretch"}, True) is None
    assert L.stretch_params({"stretch": False, "stretchMethod": "foo"}, False) is None
    assert L.stretch_params({"stretchMethod": "foo"}, True) is None  # stretch absent: off
    with pytest.raises(ValueError, match="unknown stretch method"):
        L.stretch_params({"stretch": True, "stretchMethod": "foo"}, True)


# ---- every offered noise and sharpen tool has an amount at every level it offers -----------------------------------


@pytest.mark.parametrize("found", [
    {"nxt": True, "prism": True, "studio": True, "prism2Unavailable": False},
    {"nxt": True, "prism": True, "studio": True, "prism2Unavailable": True},
    {"nxt": True, "prism": True, "studio": True, "prism2Unavailable": False, "mldenoise": True},
])
@pytest.mark.parametrize("prism2", [False, True])
def test_every_offered_noise_tool_has_an_amount_at_every_offered_level(found: dict[str, bool], prism2: bool) -> None:
    tools = L.noise_tools_from(found)
    assert tools[-1] == L.NR_TOOL_SIRIL
    assert L.NR_TOOL_SIRIL in tools
    assert "MLDenoise" not in tools
    for tool in tools:
        for stretch in (True, False, None):
            for level in L.noise_levels_for(tool, stretch, prism2_linear_pass=prism2):
                amount = L.noise_amount_for(tool, level, None, prism2_linear_pass=prism2)
                assert amount is not None, (tool, level, stretch, prism2)


def test_siril_denoise_has_its_amount_at_every_level() -> None:
    for level in ("low", "medium", "high"):
        assert L.noise_amount_for(L.NR_TOOL_SIRIL, level) == L.SIRIL_DENOISE_MOD[level]
    assert L.noise_levels_for(L.NR_TOOL_SIRIL, True) == ["low", "medium", "high"]
    assert L.denoise_is_linear(L.NR_TOOL_SIRIL) is True


def test_every_offered_sharpen_tool_has_an_amount_at_every_ladder_level() -> None:
    tools = L.sharpen_tools_from({"bxt": True, "parallax": True, "studio": True})
    assert tools == [L.SHARPEN_TOOL_BXT, L.SHARPEN_TOOL_SYQON, L.SHARPEN_TOOL_STUDIO]
    for tool in tools:
        for kind in ("stars", "detail"):
            for level in ("low", "medium", "high"):
                assert L.sharpen_amount_for(tool, kind, level) is not None, (tool, kind, level)


# ---- config.py shares the tool names with levels.py -----------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "NR_TOOL_STUDIO", "NR_TOOL_STUDIO2", "NR_TOOL_STUDIO_OLD", "SHARPEN_TOOL_BXT", "SHARPEN_TOOL_STUDIO_CORRECT_OLD",
    "GRADIENT_TOOL_NONE", "GRADIENT_TOOL_GRAXPERT", "STRETCH_METHOD_MTF", "STRETCH_METHOD_GHS",
    "STUDIO_PARALLAX_FAMILY", "STUDIO_PARALLAX_FAMILIES",
])
def test_config_uses_the_names_of_levels(name: str) -> None:
    assert getattr(config_mod, name) is getattr(L, name)


def test_config_keeps_only_the_names_levels_does_not_have() -> None:
    assert config_mod.NR_TOOL_MLDENOISE == "MLDenoise"
    assert config_mod.STRETCH_METHOD_MAS == "MultiscaleAdaptiveStretch"
    assert not hasattr(L, "NR_TOOL_MLDENOISE")
    assert not hasattr(L, "STRETCH_METHOD_MAS")
