"""Acceptance: finishing decisions and the deterministic stretch (levels, tool lists, stage parameters, MTF, GHS).

Black box through ``loomsiril.loom.stretch`` and ``loomsiril.loom.levels``; each part skips until its module exists.
Expected values are literals recorded once from Loom's JavaScript (lib/Steps.js, lib/Pipeline.js, Loom's own
golden tables) or follow from the written rules; none is read from the port.
"""

from __future__ import annotations

import itertools
import math
import time
from pathlib import Path

import numpy as np
import pytest

from loom_acc_helpers import import_loom

NO_SIGNAL = "This image has no signal above its darkest pixel; there is nothing to stretch."


# ====================================================================== stretch maths


@pytest.fixture(scope="module")
def stretch():
    return import_loom("stretch")


def test_constants_are_loom_s(stretch) -> None:
    assert stretch.STRETCH_SKY_TARGET == 0.25
    assert stretch.STRETCH_STARS_TARGET == 0.5
    assert stretch.STRETCH_CLIP_BUDGET == 1e-4
    assert stretch.NO_SIGNAL_MESSAGE == NO_SIGNAL


def test_mtf_formulas_match_loom_values(stretch) -> None:
    m = stretch.mtf_midtone_for(0.05, 0.25)
    assert m == pytest.approx(0.13636363636363638, rel=1e-12)
    assert stretch.mtf_apply(m, 0.05) == pytest.approx(0.25, rel=1e-12)
    assert stretch.mtf_apply(0.3, 0.7) == pytest.approx(0.8448275862068965, rel=1e-12)


def test_mtf_curve_properties(stretch) -> None:
    x = np.linspace(0, 1, 101)
    for m in (0.01, 0.1, 0.5, 0.9):
        y = stretch.mtf_apply(m, x)
        assert y[0] == pytest.approx(0.0, abs=1e-12) and y[-1] == pytest.approx(1.0, abs=1e-12)
        assert np.all(np.diff(y) > 0), "monotonic"
    assert np.allclose(stretch.mtf_apply(0.5, x), x, atol=1e-12)  # a midtone of 0.5 is the identity
    for x0 in (1e-4, 0.01, 0.2, 0.6):
        for target in (0.1, 0.25, 0.5):
            assert stretch.mtf_apply(stretch.mtf_midtone_for(x0, target), x0) == pytest.approx(target, rel=1e-9)


def test_parameters_from_statistics_match_loom(stretch) -> None:
    p = stretch.stretch_parameters_from(0.05, 0.01, 0.10)
    assert p["c0"] == 0.01
    assert p["m"] == pytest.approx(0.2748091603053435, rel=1e-12)
    assert p["skyOut"] == pytest.approx(0.1, rel=1e-12)
    d = stretch.stretch_parameters_from(0.05, 0.01)  # default target is the sky target
    assert d["skyOut"] == pytest.approx(0.25, rel=1e-12)


def test_negative_minimum_is_a_zero_black_point(stretch) -> None:
    p = stretch.stretch_parameters_from(0.05, -0.002)
    assert p["c0"] == 0.0
    assert p["skyOut"] == pytest.approx(0.25, rel=1e-12)


@pytest.mark.parametrize("median,minimum", [(0.05, 0.05), (0.02, 0.05), (0.0, 0.0)])
def test_flat_or_inverted_statistics_raise_the_loom_message(stretch, median, minimum) -> None:
    with pytest.raises(ValueError) as exc:
        stretch.stretch_parameters_from(median, minimum)
    assert str(exc.value) == NO_SIGNAL


@pytest.mark.parametrize("level", [0.0, 0.07, 1.0])
def test_a_flat_image_has_nothing_to_stretch(stretch, level) -> None:
    """A flat frame, including an all-white one (no division by zero), is refused with Loom's message."""
    mono = [np.full((40, 50), level, dtype=np.float32)]
    colour = mono * 3
    for planes in (mono, colour):
        for linked in (True, False):
            with pytest.raises(ValueError, match="nothing to stretch"):
                stretch.stretch_parameters_for_planes(planes, linked)


def _clip_reference(samples: np.ndarray, robust_min: float) -> float:
    """The clip-safe black point from its definition: keep at most floor(1e-4 * N) pixels at or below it."""
    n = samples.size
    budget = math.floor(1e-4 * n)
    below = np.sort(samples[samples < robust_min].astype(np.float64))
    if len(below) <= budget:
        return robust_min
    best = None
    for v in np.unique(below):
        if np.count_nonzero(below <= v) <= budget:
            best = float(v)
    return best if best is not None else min(0.0, float(below[0]))


def _clip_cases() -> list:
    rng = np.random.default_rng(11)
    n = 20000  # budget = 2
    base = np.full(n, 0.05, dtype=np.float32)
    cases = []
    cases.append(("none below", base.copy(), 0.05))
    one = base.copy(); one[3] = 0.001
    cases.append(("within budget", one, 0.05))
    spread = base.copy(); spread[:6] = [0.001, 0.002, 0.003, 0.004, 0.005, 0.006]
    cases.append(("over budget distinct", spread, 0.05))
    ties = base.copy(); ties[:6] = [0.001, 0.002, 0.002, 0.002, 0.004, 0.005]
    cases.append(("ties at the cut", ties, 0.05))
    eq = base.copy(); eq[:9] = 0.003
    cases.append(("all below equal", eq, 0.05))
    alone = base.copy(); alone[:5] = [-0.01, -0.01, -0.01, 0.02, 0.03]
    cases.append(("lowest value shared over budget", alone, 0.05))
    pos = base.copy(); pos[:5] = 0.01
    cases.append(("lowest shared positive", pos, 0.05))
    tail = (0.05 + rng.normal(0, 0.003, n)).astype(np.float32)
    cases.append(("noise tail", tail, float(np.sort(tail)[0]) + 0.004))
    return cases


@pytest.mark.parametrize("name,samples,robust_min", _clip_cases(), ids=[c[0] for c in _clip_cases()])
def test_clip_safe_black_point_follows_the_clip_budget(stretch, name, samples, robust_min) -> None:
    plane = samples.reshape(100, 200)
    assert stretch.clip_safe_black_point(plane, robust_min) == pytest.approx(_clip_reference(samples, robust_min), abs=1e-12)


def test_robust_minimum_ignores_a_single_cold_pixel(stretch) -> None:
    plane = np.full((30, 40), 0.05, dtype=np.float32)
    plane[10, 10] = 0.0
    (low,) = stretch.robust_minimum_per_channel([plane])
    assert low == pytest.approx(0.05, abs=1e-6)
    hot = plane.copy(); hot[5, 5] = 0.9
    assert stretch.robust_minimum_per_channel([hot])[0] == pytest.approx(0.05, abs=1e-6)


def _sky(seed: int, levels, noise: float = 0.002, shape=(120, 160), stars: int = 12) -> list:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    out = []
    for level in levels:
        img = level + rng.normal(0, noise, shape)
        for _ in range(stars):
            cx, cy = rng.uniform(5, shape[1] - 5), rng.uniform(5, shape[0] - 5)
            img = img + rng.uniform(0.1, 0.6) * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 1.5 ** 2))
        out.append(np.clip(img, 0, 1).astype(np.float32))
    return out


def test_mono_stretch_lands_the_sky_median_on_the_target(stretch) -> None:
    planes = _sky(1, [0.05])
    p = stretch.stretch_parameters_for_planes(planes, True)
    out = stretch.apply_mtf(planes, p, True)
    assert out[0].dtype == np.float32
    assert float(np.median(out[0])) == pytest.approx(0.25, abs=0.005)


def test_unlinked_colour_stretch_gives_every_channel_its_own_parameters(stretch) -> None:
    planes = _sky(2, [0.04, 0.05, 0.06])
    p = stretch.stretch_parameters_for_planes(planes, False)
    assert len(p["channels"]) == 3
    assert (p["c0"], p["m"]) == (p["channels"][0]["c0"], p["channels"][0]["m"])
    assert len({c["c0"] for c in p["channels"]}) == 3
    out = stretch.apply_mtf(planes, p, False)
    for plane in out:
        assert float(np.median(plane)) == pytest.approx(0.25, abs=0.005)


def test_linked_colour_stretch_shares_one_black_point_and_midtone(stretch) -> None:
    planes = _sky(3, [0.04, 0.05, 0.06])
    p = stretch.stretch_parameters_for_planes(planes, True)
    assert "channels" not in p
    assert p["skyOut"] == pytest.approx(0.25, abs=1e-9)
    robust = stretch.robust_minimum_per_channel(planes)
    assert p["c0"] <= max(0.0, min(robust)) + 1e-9, "the shared black point cannot clip the darkest channel"
    out = stretch.apply_mtf(planes, p, True)
    # one transform for all channels: identical inputs give identical outputs
    same = stretch.apply_mtf([planes[0], planes[0], planes[0]], p, True)
    assert same[0].tobytes() == same[1].tobytes() == same[2].tobytes()
    # channel order is preserved (brighter sky stays brighter)
    meds = [float(np.median(o)) for o in out]
    assert meds[0] < meds[1] < meds[2]


def _zeros(a: np.ndarray) -> int:
    return int(np.count_nonzero(a == 0))


@pytest.mark.parametrize("seed", range(8))
def test_stretch_clips_at_most_one_in_ten_thousand_new_pixels(stretch, seed) -> None:
    """The clipping invariant: after the stretch, new exact zeros per channel stay within floor(1e-4 * N)."""
    rng = np.random.default_rng(100 + seed)
    colour = seed % 2 == 0
    unlinked = seed % 4 in (0, 1)
    levels = [0.05, 0.03, 0.06] if colour else [0.05]
    planes = _sky(seed, levels, noise=0.003 if seed < 4 else 0.006, shape=(150, 200))
    if seed in (2, 3):  # a dark noise tail: one planted dark region
        for p in planes:
            p[:4, :40] = np.clip(p[:4, :40] - 0.03, 0, 1)
    if seed == 6:  # one channel much darker than the others
        planes[1] *= np.float32(0.4)
    p = stretch.stretch_parameters_for_planes(planes, not (colour and unlinked))
    out = stretch.apply_mtf(planes, p, not (colour and unlinked))
    budget = math.floor(1e-4 * planes[0].size)
    for before, after in zip(planes, out):
        assert _zeros(after) - _zeros(before) <= budget


def test_siril_mtf_command_text(stretch) -> None:
    p = {"c0": 0.01, "m": 0.2748091603053435, "skyOut": 0.1}
    mid = format(0.2748091603053435, ".17g")
    assert float(mid) == 0.2748091603053435  # enough digits to round-trip the double
    assert stretch.mtf_commands(p, True, 3) == [("mtf", "0.01", mid, "1")]
    assert stretch.mtf_commands(p, True, 1) == [("mtf", "0.01", mid, "1")]
    per = [{"c0": 0.01, "m": 0.2}, {"c0": 0.02, "m": 0.3}, {"c0": 0.03, "m": 0.4}]
    cmds = stretch.mtf_commands({"c0": 0.01, "m": 0.2, "skyOut": 0.25, "channels": per}, False, 3)
    assert [c[-1] for c in cmds] == ["R", "G", "B"]
    assert [c[0] for c in cmds] == ["mtf", "mtf", "mtf"]
    assert all(c[3] == "1" for c in cmds)
    for c, q in zip(cmds, per):
        assert float(c[1]) == q["c0"] and float(c[2]) == q["m"]
    # a mono image stays one command even when "unlinked" was asked for
    assert len(stretch.mtf_commands(p, False, 1)) == 1


@pytest.mark.perf
def test_stretch_parameters_for_a_full_size_colour_image_are_fast(stretch) -> None:
    rng = np.random.default_rng(5)
    planes = [(0.05 + rng.random((3000, 4000), dtype=np.float32) * 0.01) for _ in range(3)]
    t0 = time.perf_counter()
    stretch.stretch_parameters_for_planes(planes, False)
    assert time.perf_counter() - t0 < 10.0


# ====================================================================== levels, tool lists, stage parameters


@pytest.fixture(scope="module")
def levels():
    return import_loom("levels")


NXT, PRISM, ESSENTIAL, PRISM2, SIRIL = (
    "NoiseXTerminator", "SyQon Prism", "SyQon Studio Prism Essential", "SyQon Studio Prism 2.0", "Siril denoise")


def test_tool_name_constants(levels) -> None:
    assert levels.NR_TOOL_NXT == NXT and levels.NR_TOOL_PRISM == PRISM
    assert levels.NR_TOOL_STUDIO == ESSENTIAL and levels.NR_TOOL_STUDIO2 == PRISM2
    assert levels.NR_TOOL_SIRIL == SIRIL
    assert (levels.STAR_TOOL_STARNET, levels.STAR_TOOL_SXT, levels.STAR_TOOL_SYQON) == (
        "StarNet2", "StarXTerminator", "SyQon Starless")
    assert (levels.SHARPEN_TOOL_BXT, levels.SHARPEN_TOOL_SYQON, levels.SHARPEN_TOOL_STUDIO) == (
        "BlurXTerminator", "SyQon Parallax", "SyQon Studio Parallax")
    assert (levels.STRETCH_METHOD_MTF, levels.STRETCH_METHOD_GHS) == ("mtf", "ghs")


@pytest.mark.parametrize("nxt,prism,studio,p2un", list(itertools.product([False, True], repeat=4)))
def test_noise_tool_list_order_and_membership(levels, nxt, prism, studio, p2un) -> None:
    found = {"nxt": nxt, "prism": prism, "studio": studio, "prism2Unavailable": p2un}
    expected = []
    if nxt:
        expected.append(NXT)
    if prism:
        expected.append(PRISM)
    if studio:
        expected.append(ESSENTIAL if p2un else PRISM2)
    expected.append(SIRIL)  # the built-in is always offered, and always last
    got = levels.noise_tools_from(found)
    assert got == expected
    assert got[-1] == SIRIL and "MLDenoise" not in got


def test_siril_denoise_is_offered_even_when_nothing_is_found(levels) -> None:
    assert levels.noise_tools_from({}) == [SIRIL]


@pytest.mark.parametrize("bxt,parallax,studio", list(itertools.product([False, True], repeat=3)))
def test_sharpen_tool_list(levels, bxt, parallax, studio) -> None:
    expected = [t for t, on in (("BlurXTerminator", bxt), ("SyQon Parallax", parallax), ("SyQon Studio Parallax", studio)) if on]
    assert levels.sharpen_tools_from({"bxt": bxt, "parallax": parallax, "studio": studio}) == expected


@pytest.mark.parametrize("starnet,sxt,starless,studio", list(itertools.product([False, True], repeat=4)))
def test_star_tool_list(levels, starnet, sxt, starless, studio) -> None:
    names = (("StarNet2", starnet), ("StarXTerminator", sxt), ("SyQon Starless", starless), ("SyQon Studio Axiom", studio))
    found = {"starnet": starnet, "sxt": sxt, "starless": starless, "studio": studio}
    assert levels.star_tools_from(found) == [n for n, on in names if on]


NXT_AMOUNT = {"low": [0.7, 0.25], "medium": [0.9, 0.15], "high": [0.95, 0.1]}
PRISM_AMOUNT = {"low": 0.5, "medium": 0.85, "high": 0.95}
ESSENTIAL_AMOUNT = {"low": 0.6, "medium": 1, "high": 1}
SIRIL_AMOUNT = {"low": 0.6, "medium": 1.0, "high": 1.0}


@pytest.mark.parametrize("tool,table", [(NXT, NXT_AMOUNT), (PRISM, PRISM_AMOUNT), (ESSENTIAL, ESSENTIAL_AMOUNT), (SIRIL, SIRIL_AMOUNT)])
def test_noise_amounts_per_level(levels, tool, table) -> None:
    for level, amount in table.items():
        for pass_ in (None, "linear", "stretched", "all"):
            assert levels.noise_amount_for(tool, level, pass_) == amount
    for level in ("none", None, "extreme"):
        assert levels.noise_amount_for(tool, level) is None


def test_noise_amount_for_unknown_tools_is_none(levels) -> None:
    for tool in ("none", "toString", "", "Frobnicator", "MLDenoise"):
        assert levels.noise_amount_for(tool, "medium") is None


def test_prism2_amounts_name_the_model_per_pass(levels) -> None:
    ultra = {"model": "prism-ultra", "application": 1}
    mx = {"model": "prism-max", "application": 1}
    assert levels.noise_amount_for(PRISM2, "medium") == {"linear": None, "stretched": ultra}
    assert levels.noise_amount_for(PRISM2, "high") == {"linear": None, "stretched": mx}
    assert levels.noise_amount_for(PRISM2, "medium", "stretched") == ultra
    assert levels.noise_amount_for(PRISM2, "high", "stretched") == mx
    assert levels.noise_amount_for(PRISM2, "medium", "linear") is None
    assert levels.noise_amount_for(PRISM2, "low") is None  # no Low without the linear pass


def test_noise_levels_offered_per_tool_and_stretch(levels) -> None:
    for tool in (NXT, PRISM, ESSENTIAL, SIRIL):
        assert list(levels.noise_levels_for(tool, True)) == ["low", "medium", "high"]
        assert list(levels.noise_levels_for(tool, False)) == ["low", "medium", "high"]
    assert list(levels.noise_levels_for(PRISM2, True)) == ["medium", "high"]
    assert list(levels.noise_levels_for(PRISM2, False)) == ["medium"]


def test_unsupported_levels_fall_back_to_medium(levels) -> None:
    assert levels.supported_noise_level(PRISM2, "low", True) == "medium"
    assert levels.supported_noise_level(PRISM2, "high", False) == "medium"
    assert levels.supported_noise_level(PRISM2, "high", True) == "high"
    assert levels.supported_noise_level(NXT, "low", False) == "low"
    assert levels.supported_noise_level(SIRIL, "high", True) == "high"
    assert levels.supported_noise_level(NXT, "none", True) == "none"
    assert levels.supported_noise_level(NXT, "extreme", True) == "extreme"


def test_denoise_slot_is_a_property_of_the_tool(levels) -> None:
    assert levels.denoise_is_linear(NXT) is True
    assert levels.denoise_is_linear(ESSENTIAL) is True
    assert levels.denoise_is_linear(SIRIL) is True  # runs before the stretch
    assert levels.denoise_is_linear(PRISM) is False
    assert levels.denoise_is_linear(PRISM2) is False  # its linear pass is off, as in Loom
    assert levels.denoise_is_linear("none") is False


def test_level_names_for_prism2_models(levels) -> None:
    assert levels.noise_level_name(PRISM2, "medium") == "ultra"
    assert levels.noise_level_name(PRISM2, "high") == "max"
    assert levels.noise_level_name(NXT, "medium") == "medium"


def test_sharpen_amounts_per_tool(levels) -> None:
    f = levels.sharpen_amount_for
    assert [f("BlurXTerminator", "stars", lv) for lv in ("low", "medium", "high")] == [0.25, 0.5, 0.7]
    assert [f("BlurXTerminator", "detail", lv) for lv in ("low", "medium", "high")] == [0.25, 0.5, 0.75]
    assert [f("SyQon Parallax", "stars", lv) for lv in ("low", "medium", "high")] == [2, 3, 5]
    assert [f("SyQon Parallax", "detail", lv) for lv in ("low", "medium", "high")] == [0.4, 0.8, 1.0]
    assert [f("SyQon Studio Parallax", "stars", lv) for lv in ("low", "medium", "high")] == [3, 5, 7]
    assert [f("SyQon Studio Parallax", "detail", lv) for lv in ("low", "medium", "high")] == [0.25, 0.5, 0.75]
    assert f("BlurXTerminator", "stars", "none") is None and f("BlurXTerminator", "stars", None) is None
    assert f("none", "stars", "high") is None
    assert f("BlurXTerminator", "bogus", "high") is None


def test_aberration_corrector_borrows_studio_only_for_bxt(levels) -> None:
    assert levels.aberration_corrector("BlurXTerminator", True) == "SyQon Studio Parallax"
    assert levels.aberration_corrector("BlurXTerminator", False) == "BlurXTerminator"
    assert levels.aberration_corrector("SyQon Parallax", True) == "SyQon Parallax"
    assert levels.aberration_corrector("none", True) == "none"


# ---- stage parameter objects (they are hashed into cache keys, so values AND key order are pinned)


def _cfg(**kw):
    cfg = {"sharpenTool": "none", "starReduction": "none", "detailLevel": "none", "parallaxFamily": "classic",
           "starTool": "none", "noiseTool": "none", "noiseLevel": "medium", "noiseLevelL": "",
           "stretch": False, "stretchMethod": "mtf", "keepLinear": False}
    cfg.update(kw)
    return cfg


def _same(a, b) -> bool:
    """Equality that also demands the same key order, recursively."""
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def test_composite_sharpen_params(levels) -> None:
    assert levels.composite_sharpen_params(_cfg()) is None
    assert levels.composite_sharpen_params(_cfg(sharpenTool="BlurXTerminator")) is None  # no levels, no stage
    got = levels.composite_sharpen_params(_cfg(sharpenTool="BlurXTerminator", starReduction="medium", detailLevel="high"))
    assert _same(got, {"tool": "BlurXTerminator", "stars": "medium", "detail": "high", "starsAmount": 0.5, "detailAmount": 0.75})
    got = levels.composite_sharpen_params(_cfg(sharpenTool="SyQon Parallax", starReduction="high"))
    assert _same(got, {"tool": "SyQon Parallax", "stars": "high", "detail": None, "starsAmount": 5, "detailAmount": None})
    studio = levels.composite_sharpen_params(_cfg(sharpenTool="SyQon Studio Parallax", detailLevel="low", parallaxFamily="aesthetics"))
    assert list(studio)[:5] == ["tool", "stars", "detail", "starsAmount", "detailAmount"] and list(studio)[-1] == "family"
    classic = levels.composite_sharpen_params(_cfg(sharpenTool="SyQon Studio Parallax", detailLevel="low"))
    assert classic["family"] != studio["family"]
    assert "family" not in levels.composite_sharpen_params(_cfg(sharpenTool="BlurXTerminator", detailLevel="low", parallaxFamily="aesthetics"))


def test_star_extraction_params(levels) -> None:
    assert levels.star_extraction_params(_cfg()) is None
    got = levels.star_extraction_params(_cfg(starTool="StarNet2", stretch=True))
    assert _same(got, {"tool": "StarNet2", "starsTarget": 0.5, "stretchStars": True})
    assert levels.star_extraction_params(_cfg(starTool="StarNet2"))["stretchStars"] is False


def test_mtf_stretch_params(levels) -> None:
    assert levels.stretch_params(_cfg(), True) is None
    got = levels.stretch_params(_cfg(stretch=True, keepLinear=True), False)
    assert _same(got, {"target": 0.25, "linked": False, "keepLinear": True, "blackPoint": "clip-safe"})


def test_ghs_stretch_params_and_command(levels) -> None:
    cfg = _cfg(stretch=True, stretchMethod="ghs", keepLinear=True)
    want = {"method": "ghs", "linked": True, "keepLinear": True,
            "ghs": {"command": "autoghs", "k": 0, "D": 4.5, "b": 13, "hp": 0.7, "lp": 0}}
    assert _same(levels.stretch_params(cfg, True), want)
    assert levels.stretch_params(cfg, False)["linked"] is False
    assert levels.stretch_params(_cfg(stretch=False, stretchMethod="ghs"), True) is None
    assert levels.ghs_command(True) == ("autoghs", "-linked", "0", "4.5", "-b=13", "-hp=0.7", "-lp=0")
    assert levels.ghs_command(False) == ("autoghs", "0", "4.5", "-b=13", "-hp=0.7", "-lp=0")
    assert levels.GHS_PARAMS == want["ghs"]
    mtf_keys = set(levels.stretch_params(_cfg(stretch=True), True))
    assert set(want) != mtf_keys, "ghs and mtf stretches must key differently"


def test_the_replaced_stretch_method_is_not_accepted(levels) -> None:
    for method in ("MultiscaleAdaptiveStretch", "bogus"):
        with pytest.raises(ValueError):
            levels.stretch_params(_cfg(stretch=True, stretchMethod=method), True)


def _denoise_dict(tool, level, stretch, amount):
    return {"tool": tool, "level": level, "stretched": stretch, "amount": amount}


@pytest.mark.parametrize("tool,table,linear", [(NXT, NXT_AMOUNT, True), (ESSENTIAL, ESSENTIAL_AMOUNT, True),
                                               (SIRIL, SIRIL_AMOUNT, True), (PRISM, PRISM_AMOUNT, False)])
@pytest.mark.parametrize("stretch", [True, False])
@pytest.mark.parametrize("which", [None, "L"])
@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_denoise_stage_params(levels, tool, table, linear, stretch, which, level) -> None:
    cfg = _cfg(noiseTool=tool, noiseLevel=level, noiseLevelL="low", stretch=stretch)
    eff = "low" if which == "L" else level  # L has its own strength, the colour plates follow noiseLevel
    want = _denoise_dict(tool, eff, stretch, table[eff])
    assert _same(levels.composite_denoise_params(cfg, which), want)
    if linear:
        assert _same(levels.linear_denoise_params(cfg, which), want)
        assert levels.stretched_denoise_params(cfg, which) is None
    else:
        assert levels.linear_denoise_params(cfg, which) is None
        assert _same(levels.stretched_denoise_params(cfg, which), want)


@pytest.mark.parametrize("stretch", [True, False])
def test_prism2_denoise_stage_params(levels, stretch) -> None:
    ultra = {"model": "prism-ultra", "application": 1}
    amount = {"linear": None, "stretched": ultra}
    cfg = _cfg(noiseTool=PRISM2, noiseLevel="low", stretch=stretch)  # Low is not offered: runs as Medium
    assert _same(levels.composite_denoise_params(cfg, None), _denoise_dict(PRISM2, "medium", stretch, amount))
    slot = {"tool": PRISM2, "pass": "stretched" if stretch else "unstretched", "amount": ultra}
    if stretch:
        assert levels.linear_denoise_params(cfg, None) is None and _same(levels.stretched_denoise_params(cfg, None), slot)
    else:
        assert levels.stretched_denoise_params(cfg, None) is None and _same(levels.linear_denoise_params(cfg, None), slot)
    high = _cfg(noiseTool=PRISM2, noiseLevel="high", stretch=True)
    assert levels.composite_denoise_params(high, None)["amount"]["stretched"]["model"] == "prism-max"


def test_no_denoise_tool_means_no_denoise_params(levels) -> None:
    cfg = _cfg(noiseTool="none")
    assert levels.composite_denoise_params(cfg, None) is None
    assert levels.linear_denoise_params(cfg, None) is None
    assert levels.stretched_denoise_params(cfg, None) is None


def test_siril_denoise_params_shape(levels) -> None:
    cfg = _cfg(noiseTool=SIRIL, noiseLevel="medium", stretch=True)
    assert _same(levels.linear_denoise_params(cfg, None), {"tool": SIRIL, "level": "medium", "stretched": True, "amount": 1.0})
    assert levels.linear_denoise_params(_cfg(noiseTool=SIRIL, noiseLevel="low"), None)["amount"] == 0.6
    assert levels.SIRIL_DENOISE_MOD == {"low": 0.6, "medium": 1.0, "high": 1.0}


def test_finishing_name_maps_are_loom_s(levels) -> None:
    assert levels.RGB_FINISHING == {"sharpen": "sharpenRGB", "extract": "extractRGB", "denoiseLinear": "denoiseLinearRGB",
                                    "stretch": "stretchRGB", "denoise": "denoiseRGB"}
    assert levels.PALETTE_FINISHING == {"sharpen": "paletteSharpen", "extract": "paletteExtract",
                                        "denoiseLinear": "paletteDenoiseLinear", "stretch": "paletteStretch",
                                        "denoise": "paletteDenoise"}
    assert levels.L_FINISHING == {"extract": "extractL", "denoiseLinear": "denoiseLinearL", "stretch": "stretchL",
                                  "denoise": "denoiseL", "which": "L", "unlinked": True}


def test_finishing_roles_present_only_when_they_do_something(levels) -> None:
    assert levels.finishing_params(levels.RGB_FINISHING, _cfg()) == {}
    full = _cfg(sharpenTool="BlurXTerminator", detailLevel="medium", starTool="StarNet2", noiseTool=NXT, stretch=True)
    got = levels.finishing_params(levels.RGB_FINISHING, full)
    # fixed order, as the chain is keyed: sharpen, extract, linear denoise, stretch, stretched denoise
    assert list(got) == ["sharpenRGB", "extractRGB", "denoiseLinearRGB", "stretchRGB"]
    assert got["stretchRGB"]["linked"] is True
    prism = levels.finishing_params(levels.PALETTE_FINISHING, _cfg(noiseTool=PRISM, stretch=True))
    assert list(prism) == ["paletteStretch", "paletteDenoise"]
    only_stretch = levels.finishing_params(levels.RGB_FINISHING, _cfg(stretch=True))
    assert list(only_stretch) == ["stretchRGB"]


def test_luminance_finishing_has_no_sharpening_and_an_unlinked_stretch(levels) -> None:
    cfg = _cfg(sharpenTool="BlurXTerminator", detailLevel="high", starTool="StarNet2", noiseTool=NXT,
               noiseLevel="medium", noiseLevelL="high", stretch=True)
    got = levels.finishing_params(levels.L_FINISHING, cfg)
    assert list(got) == ["extractL", "denoiseLinearL", "stretchL"]
    assert got["stretchL"]["linked"] is False
    assert got["denoiseLinearL"]["level"] == "high"  # L follows its own strength


def test_ghs_only_changes_the_stretch_stage_params(levels) -> None:
    base = _cfg(stretch=True, noiseTool=NXT, starTool="StarNet2")
    a = levels.finishing_params(levels.RGB_FINISHING, base)
    b = levels.finishing_params(levels.RGB_FINISHING, {**base, "stretchMethod": "ghs"})
    assert list(a) == list(b)
    assert [k for k in a if a[k] != b[k]] == ["stretchRGB"]
    assert b["stretchRGB"]["method"] == "ghs"


def test_no_module_function_still_offers_the_removed_noise_tool(levels) -> None:
    assert not hasattr(levels, "NR_TOOL_MLDENOISE")
    for found in ({"nxt": True, "prism": True, "studio": True}, {"mldenoise": True}):
        assert "MLDenoise" not in levels.noise_tools_from(found)


# ====================================================================== Siril agreement (runs in the Docker image)

SIRIL_SCRIPT = r'''
import json
import sirilpy as s
siril = s.SirilInterface(); siril.connect()
job = json.load(open("job.json"))
for step in job["steps"]:
    siril.cmd(*step)
print("DONE=true", flush=True)
'''


def _siril_apply(tmp_path: Path, planes, steps: list) -> list:
    """Write ``planes``, run ``load in`` + ``steps`` + ``save out`` in Siril, return the planes read back."""
    import json

    from loomsiril import io_fits
    from tests.loom.siril_run import run_pyscript

    io_fits.write_fits(tmp_path / "in.fits", planes, {})
    job = {"steps": [["load", "in"]] + [list(s) for s in steps] + [["save", "out"]]}
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    run = run_pyscript(SIRIL_SCRIPT, tmp_path).check()
    assert run.lines("DONE=true"), run.tail()
    out = next(p for p in (tmp_path / "out.fit", tmp_path / "out.fits") if p.exists())
    got, _ = io_fits.read_fits(out)
    return got


@pytest.mark.siril
@pytest.mark.parametrize("n_channels,linked", [(1, True), (3, True), (3, False)])
def test_siril_mtf_matches_the_numpy_reference(tmp_path: Path, n_channels: int, linked: bool) -> None:
    stretch = import_loom("stretch")
    levels = [0.05] if n_channels == 1 else [0.04, 0.05, 0.06]
    planes = _sky(20 + n_channels, levels, shape=(96, 128))
    params = stretch.stretch_parameters_for_planes(planes, linked)
    cmds = stretch.mtf_commands(params, linked, n_channels)
    got = stretch.apply_mtf(planes, params, linked)
    siril = _siril_apply(tmp_path, planes, cmds)
    assert len(siril) == len(got) == n_channels
    for a, b in zip(siril, got):
        assert a.shape == b.shape
        assert float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64)))) < 2e-6


@pytest.mark.siril
def test_siril_ghs_lands_the_sky_near_the_target_and_is_repeatable(tmp_path: Path) -> None:
    levels_mod = import_loom("levels")
    rng = np.random.default_rng(9)
    for linked, n, label in ((False, 1, "mono"), (True, 3, "colour")):
        sub = tmp_path / label
        sub.mkdir()
        base = [0.04] if n == 1 else [0.040, 0.045, 0.048]
        planes = [np.clip(b + rng.normal(0, 0.002, (96, 128)), 0, 1).astype(np.float32) for b in base]
        cmd = list(levels_mod.ghs_command(linked))
        a = _siril_apply(sub, planes, [cmd])
        b = _siril_apply(sub, planes, [cmd])
        assert all(x.tobytes() == y.tobytes() for x, y in zip(a, b)), "autoghs is deterministic"
        med = float(np.mean([np.median(p) for p in a]))
        assert abs(med - 0.25) < 0.012, f"{label}: sky at {med:.3f}"
        for before, after in zip(planes, a):
            order = np.argsort(before, axis=None)[::997]
            assert np.all(np.diff(after.ravel()[order]) >= -1e-6), "monotonic"
