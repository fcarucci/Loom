"""Loom's decision tables and cache-key parameter builders for the noise, sharpen, star, stretch, gradient and
finishing stages.

Ports Loom's ``lib/Steps.js``, ``lib/StepsSyqon.js`` and ``lib/Pipeline.js`` (tag v0.4.1; read-only reference):

* which tools are offered, and in what order (``noise_tools_from`` and friends);
* the strength ladders (semantic level -> tool numbers) and the amounts a key carries;
* which denoise slot a tool runs in (``denoise_is_linear``);
* every function that builds the parameter object a stage hashes into its cache key.

Parameter objects are equal to Loom's: same keys, same key order, same values, because they are hashed into cache keys.
Port changes against Loom: the MLDenoise tool is gone (never offered, never produced); "Siril denoise" is a new noise
tool, always offered, listed last, run in the linear slot; the stretch method "MultiscaleAdaptiveStretch" is replaced
by "ghs".

Where a Loom function reads global state (``Steps.PRISM2_LINEAR_PASS``, which the JavaScript sets at load time), the
port takes it as a keyword argument, ``prism2_linear_pass``, whose default is Loom's value at load time (False). A
``config`` is a plain dict with Loom's field names (``Config.defaults()``); absent keys read as ``None``, as an
undefined property does in Loom.

Pure: no I/O, no side effects at import, no sirilpy or tkinter.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# ---- tool names -------------------------------------------------------------------------------------------------------

NR_TOOL_NXT = "NoiseXTerminator"
NR_TOOL_PRISM = "SyQon Prism"
# SyQon Studio's Prism: Essential (the ``_OLD`` name is Essential's from before Prism 2.0 was offered beside it)
NR_TOOL_STUDIO = "SyQon Studio Prism Essential"
NR_TOOL_STUDIO2 = "SyQon Studio Prism 2.0"
NR_TOOL_STUDIO_OLD = "SyQon Studio Prism"
NR_TOOL_SIRIL = "Siril denoise"

SHARPEN_TOOL_BXT = "BlurXTerminator"
SHARPEN_TOOL_SYQON = "SyQon Parallax"
SHARPEN_TOOL_STUDIO = "SyQon Studio Parallax"
SHARPEN_TOOL_STUDIO_CORRECT_OLD = "SyQon Studio Parallax (correct only)"

STAR_TOOL_STARNET = "StarNet2"
STAR_TOOL_SXT = "StarXTerminator"
STAR_TOOL_SYQON = "SyQon Starless"
STAR_TOOL_STUDIO = "SyQon Studio Axiom"

GRADIENT_TOOL_NONE = "none"
GRADIENT_TOOL_GRAXPERT = "GraXpert"
GRADIENT_TOOL_STUDIO = "SyQon Studio Deep Gradient"

STRETCH_METHOD_MTF = "mtf"
STRETCH_METHOD_GHS = "ghs"

# The CLI's stable model identifiers (syqon-cli --list-models)
STUDIO_MODEL_DENOISE = "prism-essential"
STUDIO_MODEL_PARALLAX = "parallax"
STUDIO_MODEL_STARLESS = "axiom"
STUDIO_MODEL_GRADIENT = "deep-gradient"

# Parallax family: "classic", never Studio's default "aesthetics" (Loom makes no artistic choices)
STUDIO_PARALLAX_FAMILY = "classic"
STUDIO_PARALLAX_FAMILIES = ["classic", "aesthetics"]

# Targets of the stretch and of star extraction (Steps.js STRETCH_SKY_TARGET, STRETCH_STARS_TARGET)
STRETCH_SKY_TARGET = 0.25
STRETCH_STARS_TARGET = 0.5

# Prism 2.0's linear Advanced pass: off (Steps.js PRISM2_LINEAR_PASS, set false since 2026-09-27)
PRISM2_LINEAR_PASS = False

# Siril's denoise (``denoise -mod=``) for each semantic level. No Loom counterpart.
SIRIL_DENOISE_MOD = {"low": 0.6, "medium": 1.0, "high": 1.0}

# The stretch method "ghs": Siril's ``autoghs`` parameters, fixed (see ghs_command).
GHS_PARAMS = {"command": "autoghs", "k": 0, "D": 4.5, "b": 13, "hp": 0.7, "lp": 0}

# Stage names of the finishing tail, per composite (cache stage names: renaming one orphans its entries)
RGB_FINISHING = {"sharpen": "sharpenRGB", "extract": "extractRGB", "denoiseLinear": "denoiseLinearRGB",
                 "stretch": "stretchRGB", "denoise": "denoiseRGB"}
PALETTE_FINISHING = {"sharpen": "paletteSharpen", "extract": "paletteExtract", "denoiseLinear": "paletteDenoiseLinear",
                     "stretch": "paletteStretch", "denoise": "paletteDenoise"}
L_FINISHING = {"extract": "extractL", "denoiseLinear": "denoiseLinearL", "stretch": "stretchL",
               "denoise": "denoiseL", "which": "L", "unlinked": True}

# ---- tables -----------------------------------------------------------------------------------------------------------

_STUDIO2_LADDER_LINEAR: dict[str, Any] = {
    "low": {"linear": {"model": "prism-advanced", "application": 1.0, "fineScale": 4}, "stretched": None},
    "medium": {"linear": {"model": "prism-advanced", "application": 1.0, "fineScale": 4},
               "stretched": {"model": "prism-ultra", "application": 1.0}},
    "high": {"linear": {"model": "prism-advanced", "application": 1.0, "fineScale": 4},
             "stretched": {"model": "prism-max", "application": 1.0}},
}

# Prism 2.0's ladder without the linear pass (the default): Ultra or Max after the stretch.
PRISM2_LADDER_STRETCHED: dict[str, Any] = {
    "medium": {"linear": None, "stretched": {"model": "prism-ultra", "application": 1.0},
               "unstretched": {"model": "prism-ultra", "application": 1.0}},
    "high": {"linear": None, "stretched": {"model": "prism-max", "application": 1.0}, "unstretched": None},
}

PRISM2_LADDER_LINEAR: dict[str, Any] = _STUDIO2_LADDER_LINEAR

PRISM2_LEVEL_NAMES: dict[str, str] = {"medium": "ultra", "high": "max"}

# Semantic level -> the tool's own number. Medium is each tool's default (lib/Steps.js NOISE_LEVELS).
NOISE_LEVELS: dict[str, Any] = {
    "nxt": {"low": {"denoise": 0.70, "detail": 0.25},
            "medium": {"denoise": 0.90, "detail": 0.15},
            "high": {"denoise": 0.95, "detail": 0.10}},
    "prism": {"low": 0.50, "medium": 0.85, "high": 0.95},
    "studio": {"low": 0.60, "medium": 1.00, "high": 1.00},
    "studio2": PRISM2_LADDER_STRETCHED,
}

SHARPEN_LEVELS: dict[str, Any] = {
    "bxt": {"stars": {"none": 0.00, "low": 0.25, "medium": 0.50, "high": 0.70},
            "detail": {"none": 0.00, "low": 0.25, "medium": 0.50, "high": 0.75}},
    "syqon": {"stars": {"none": 0, "low": 2, "medium": 3, "high": 5},
              "detail": {"none": 0.0, "low": 0.4, "medium": 0.8, "high": 1.0}},
    "studio": {"stars": {"none": 0, "low": 3, "medium": 5, "high": 7},
               "detail": {"none": 0.0, "low": 0.25, "medium": 0.50, "high": 0.75}},
}

_STUDIO_PARALLAX_FAMILY_SET = frozenset(STUDIO_PARALLAX_FAMILIES)
_LEVELS3 = ("low", "medium", "high")


def _studio2_ladder(prism2_linear_pass: bool) -> dict[str, Any]:
    """Prism 2.0's ladder for the linear-pass setting (lib/Steps.js NOISE_LEVELS.studio2)."""
    return PRISM2_LADDER_LINEAR if prism2_linear_pass else PRISM2_LADDER_STRETCHED


# ---- small helpers ----------------------------------------------------------------------------------------------------


def tool_chosen(tool: Any) -> bool:
    """A tool is chosen when it is set and is not "none" (lib/Steps.js toolChosen)."""
    return bool(tool) and tool != "none"


def studio_family_of(family: Any) -> str:
    """A stored or passed Parallax profile as Studio offers it; anything else is Classic."""
    return family if family in _STUDIO_PARALLAX_FAMILY_SET else STUDIO_PARALLAX_FAMILY


def gradient_tool_of(config: Mapping[str, Any] | None) -> str:
    """The gradient tool a configuration asks for.

    ``gradientTool`` wins when it is a non-empty string; otherwise the legacy ``useGraXpert`` checkbox decides
    (true is GraXpert, anything else none), as lib/StepsSyqon.js gradientToolOf does.
    """
    if config:
        tool = config.get("gradientTool")
        if isinstance(tool, str) and len(tool) > 0:
            return tool
        if config.get("useGraXpert") is True:
            return GRADIENT_TOOL_GRAXPERT
    return GRADIENT_TOOL_NONE


def studio_family(config: Mapping[str, Any]) -> str:
    """The Parallax profile a run hands to Studio (lib/Pipeline.js studioFamily)."""
    if config.get("sharpenTool") == SHARPEN_TOOL_STUDIO:
        return studio_family_of(config.get("parallaxFamily"))
    return STUDIO_PARALLAX_FAMILY


def _with_studio_family(params: dict[str, Any], tool: Any, family: Any) -> dict[str, Any]:
    """A Studio stage keys on its profile; no other tool's key changes (lib/Pipeline.js withStudioFamily)."""
    if tool == SHARPEN_TOOL_STUDIO:
        params["family"] = studio_family_of(family)
    return params


# ---- noise tools ------------------------------------------------------------------------------------------------------


def noise_tools_from(found: Mapping[str, bool]) -> list[str]:
    """The noise tools this installation can run, in dropdown order (lib/Steps.js noiseToolsFrom, port form).

    Keys read: ``nxt``, ``prism``, ``studio``, ``prism2Unavailable``. Any other key (Loom's ``mldenoise``) is ignored:
    MLDenoise is gone. Studio's Prism is one entry: 2.0, or Essential once 2.0 is refused. "Siril denoise" is always
    offered, last.
    """
    out: list[str] = []
    if found.get("nxt"):
        out.append(NR_TOOL_NXT)
    if found.get("prism"):
        out.append(NR_TOOL_PRISM)
    if found.get("studio"):
        out.append(NR_TOOL_STUDIO if found.get("prism2Unavailable") else NR_TOOL_STUDIO2)
    out.append(NR_TOOL_SIRIL)
    return out


def _nxt_amount(lv: Mapping[str, Any], pass_: Any) -> list[Any]:
    # an array, not the object: the key serialises it verbatim, in this order
    return [lv["denoise"], lv["detail"]]


def _studio2_amount(m: Mapping[str, Any], pass_: str | None) -> dict[str, Any] | None:
    """Per pass, the model and its blend (and fineScale where the level carries one); see lib/Steps.js."""

    def one(p: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if p is None:
            return None
        a: dict[str, Any] = {"model": p["model"], "application": p["application"]}
        if p.get("fineScale") is not None:
            a["fineScale"] = p["fineScale"]
        return a

    if pass_ is None:
        return {"linear": one(m.get("linear")), "stretched": one(m.get("stretched"))}
    return one(m.get(pass_))


def _noise_entry(tool: Any, prism2_linear_pass: bool) -> tuple[bool, Mapping[str, Any], Any] | None:
    """(runs in the linear slot, its levels, its amount function or None) for a tool, or None if it is unknown.

    Only own entries: a name such as "toString" is unknown. ``MLDenoise`` is unknown here.
    """
    if tool == NR_TOOL_NXT:
        return True, NOISE_LEVELS["nxt"], _nxt_amount
    if tool == NR_TOOL_PRISM:
        return False, NOISE_LEVELS["prism"], None
    if tool == NR_TOOL_STUDIO:
        return True, NOISE_LEVELS["studio"], None
    if tool == NR_TOOL_STUDIO2:
        return bool(prism2_linear_pass), _studio2_ladder(prism2_linear_pass), _studio2_amount
    if tool == NR_TOOL_SIRIL:
        return True, SIRIL_DENOISE_MOD, None
    return None


def denoise_is_linear(tool: Any, *, prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> bool:
    """Whether a tool runs on LINEAR data (lib/Steps.js denoiseIsLinear).

    Prism 2.0 is linear only with its linear pass on; the port's default is off.
    """
    entry = _noise_entry(tool, prism2_linear_pass)
    return entry is not None and entry[0]


def noise_levels_for(tool: Any, stretch: Any, *, prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> list[str]:
    """The strengths a noise tool offers, in dropdown order, with the stretch on or off (lib/Steps.js noiseLevelsFor).

    ``stretch`` is off only when it is exactly False; anything else (True, None) is on, as in Loom's ``stretch !== false``.
    Prism 2.0 without its linear pass has no Low, and without a stretch only Medium.
    """
    if tool != NR_TOOL_STUDIO2:
        return list(_LEVELS3)
    ladder = _studio2_ladder(prism2_linear_pass)
    out: list[str] = []
    for lv in _LEVELS3:
        step = ladder.get(lv)
        if step is not None and (stretch is not False or step.get("linear") is not None
                                 or step.get("unstretched") is not None):
            out.append(lv)
    return out


def supported_noise_level(tool: Any, level: Any, stretch: Any, *,
                          prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> Any:
    """A strength the tool does not offer here is Medium; "none" and unknown levels are returned as they are."""
    if level not in _LEVELS3:
        return level
    return level if level in noise_levels_for(tool, stretch, prism2_linear_pass=prism2_linear_pass) else "medium"


def noise_level_name(tool: Any, level: Any) -> Any:
    """What a strength is called on screen: Prism 2.0's Medium and High are Ultra and Max (lib/Steps.js)."""
    if tool == NR_TOOL_STUDIO2 and level in PRISM2_LEVEL_NAMES:
        return PRISM2_LEVEL_NAMES[level]
    return level


def noise_amount_for(tool: Any, level: Any, pass_: str | None = None, *,
                     prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> Any:
    """The number a noise tool's cache key carries for a level (lib/Steps.js noiseAmountFor).

    ``pass_`` matters only to Prism 2.0 (``linear``, ``stretched``, ``unstretched``, ``all``, or None for both slots).
    None for an unknown tool or level, rather than raising: the caller has already decided not to run such a stage.
    """
    entry = _noise_entry(tool, prism2_linear_pass)
    if entry is None or level is None or level == "none":
        return None
    _linear, levels, amount = entry
    v = levels.get(level)
    if v is None:
        return None
    return amount(v, pass_) if amount is not None else v


def aberration_corrector(sharpen_tool: Any, studio_found: bool) -> Any:
    """The tool that really runs the aberration pass: with BlurXTerminator and Studio found, Studio Parallax."""
    return SHARPEN_TOOL_STUDIO if (sharpen_tool == SHARPEN_TOOL_BXT and studio_found) else sharpen_tool


# ---- sharpen and star tools -------------------------------------------------------------------------------------------


def sharpen_tools_from(found: Mapping[str, bool]) -> list[str]:
    """The sharpening tools in dropdown order (lib/Steps.js sharpenToolsFrom). Keys: bxt, parallax, studio."""
    tools: list[str] = []
    if found.get("bxt"):
        tools.append(SHARPEN_TOOL_BXT)
    if found.get("parallax"):
        tools.append(SHARPEN_TOOL_SYQON)
    if found.get("studio"):
        tools.append(SHARPEN_TOOL_STUDIO)
    return tools


def sharpen_amount_for(tool: Any, kind: str, level: Any) -> Any:
    """The amount a sharpen stage keys on for ``kind`` ("stars" or "detail") at a level (lib/Steps.js sharpenAmountFor).

    None for "none", an unknown tool or level. Keying on the number that reaches the tool makes a ladder remap
    invalidate exactly the stages it alters.
    """
    if level is None or level == "none":
        return None
    ladders = {SHARPEN_TOOL_BXT: "bxt", SHARPEN_TOOL_SYQON: "syqon", SHARPEN_TOOL_STUDIO: "studio"}
    name = ladders.get(tool)
    if name is None:
        return None
    kinds = SHARPEN_LEVELS[name].get(kind)
    if kinds is None:
        return None
    return kinds.get(level)


def star_tools_from(found: Mapping[str, bool]) -> list[str]:
    """The star extraction tools in dropdown order (lib/Steps.js starToolsFrom).

    Keys: starnet, sxt, starless, studio.
    """
    out: list[str] = []
    if found.get("starnet"):
        out.append(STAR_TOOL_STARNET)
    if found.get("sxt"):
        out.append(STAR_TOOL_SXT)
    if found.get("starless"):
        out.append(STAR_TOOL_SYQON)
    if found.get("studio"):
        out.append(STAR_TOOL_STUDIO)
    return out


# ---- cache-key parameter builders -------------------------------------------------------------------------------------


def composite_sharpen_params(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """The composite's sharpen stage parameters, or None when it would do nothing (lib/Pipeline.js compositeSharpenParams)."""
    tool = config.get("sharpenTool")
    if not tool_chosen(tool):
        return None
    s = config.get("starReduction")
    stars = s if (s and s != "none") else None
    d = config.get("detailLevel")
    detail = d if (d and d != "none") else None
    if stars is None and detail is None:
        return None
    p = {"tool": tool, "stars": stars, "detail": detail,
         "starsAmount": sharpen_amount_for(tool, "stars", stars),
         "detailAmount": sharpen_amount_for(tool, "detail", detail)}
    return _with_studio_family(p, tool, config.get("parallaxFamily"))


def aberration_params(config: Mapping[str, Any], studio_found: bool) -> dict[str, Any]:
    """The aberration stage's parameters, keyed on the tool that really corrects (lib/Pipeline.js aberrationParams).

    ``studio_found`` is Loom's global Studio availability; passed explicitly.
    """
    tool = config.get("sharpenTool") or "none"
    p: dict[str, Any] = {"tool": tool, "photometry": "linearfit-v1"}
    corrector = aberration_corrector(tool, bool(studio_found))
    if corrector != tool:
        p["corrector"] = corrector
        p["family"] = studio_family(config)
        return p
    return _with_studio_family(p, tool, config.get("parallaxFamily"))


def star_extraction_params(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Star extraction parameters, or None when no tool is selected (lib/Pipeline.js starExtractionParams)."""
    tool = config.get("starTool")
    if not tool_chosen(tool):
        return None
    return {"tool": tool, "starsTarget": STRETCH_STARS_TARGET, "stretchStars": bool(config.get("stretch"))}


def stretch_params(config: Mapping[str, Any], linked: bool) -> dict[str, Any] | None:
    """The stretch stage's parameters, or None when the stretch is off (lib/Pipeline.js stretchParams).

    ``mtf`` (Loom's stretch) keys on the target, the link, keepLinear and the clip-safe black point. ``ghs`` keys on
    its link, keepLinear and the Siril ``autoghs`` parameters. With the stretch off the result is None, as in Loom
    (lib/Pipeline.js:438-442), and the method is not looked at. With the stretch on, any other method is refused
    by name with ValueError.
    """
    if not config.get("stretch"):
        return None
    method = config.get("stretchMethod") or STRETCH_METHOD_MTF
    if method not in (STRETCH_METHOD_MTF, STRETCH_METHOD_GHS):
        raise ValueError(f"unknown stretch method: {method!r}")
    if method == STRETCH_METHOD_GHS:
        return {"method": STRETCH_METHOD_GHS, "linked": bool(linked), "keepLinear": bool(config.get("keepLinear")),
                "ghs": dict(GHS_PARAMS)}
    return {"target": _sky_target(config), "linked": bool(linked), "keepLinear": bool(config.get("keepLinear")),
            "blackPoint": "clip-safe"}


def _sky_target(config: Mapping[str, Any]) -> float:
    """The sky level the stretch aims for: ``config["stretchTarget"]`` when it is a number in (0, 1), else Loom's 0.25.

    Loom's own histogram stretch uses 0.25 (``STRETCH_SKY_TARGET``, PixInsight's autostretch level). The Siril port's
    dialog defaults ask for 0.15, the background level of MultiscaleAdaptiveStretch, the method Siril does not have and
    that the PixInsight runs it is compared with use.
    """
    value = config.get("stretchTarget")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 < float(value) < 1.0:
        return float(value)
    return STRETCH_SKY_TARGET


def ghs_command(linked: bool) -> tuple[str, ...]:
    """The Siril command for the GHS stretch, linked or unlinked. The values are GHS_PARAMS, fixed."""
    head: tuple[str, ...] = ("autoghs", "-linked") if linked else ("autoghs",)
    return head + ("0", "4.5", "-b=13", "-hp=0.7", "-lp=0")


def denoise_level_for(config: Mapping[str, Any], which: str | None, *,
                      prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> Any:
    """The strength a plate is denoised at: ``which == "L"`` takes noiseLevelL when set (lib/Pipeline.js denoiseLevelFor).

    A strength the tool does not offer here runs as Medium.
    """
    level_l = config.get("noiseLevelL")
    if which == "L" and level_l and level_l != "none":
        level = level_l
    else:
        level = config.get("noiseLevel")
    return supported_noise_level(config.get("noiseTool"), level, bool(config.get("stretch")),
                                 prism2_linear_pass=prism2_linear_pass)


def composite_denoise_params(config: Mapping[str, Any], which: str | None, *,
                             prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> dict[str, Any] | None:
    """The denoise stage parameters for a plate, or None when no tool or level (lib/Pipeline.js compositeDenoiseParams)."""
    tool = config.get("noiseTool")
    if not tool_chosen(tool):
        return None
    level = denoise_level_for(config, which, prism2_linear_pass=prism2_linear_pass)
    if not level or level == "none":
        return None
    return {"tool": tool, "level": level, "stretched": bool(config.get("stretch")),
            "amount": noise_amount_for(tool, level, None, prism2_linear_pass=prism2_linear_pass)}


def prism2_pass_params(config: Mapping[str, Any], which: str | None, pass_: str, *,
                       prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> dict[str, Any] | None:
    """One Prism 2.0 pass's parameters: the pass's model and blend, keyed on the slot (lib/Pipeline.js prism2PassParams)."""
    level = denoise_level_for(config, which, prism2_linear_pass=prism2_linear_pass)
    amount = noise_amount_for(NR_TOOL_STUDIO2, level, pass_, prism2_linear_pass=prism2_linear_pass)
    if amount is None:
        return None
    return {"tool": NR_TOOL_STUDIO2, "pass": pass_, "amount": amount}


def linear_denoise_params(config: Mapping[str, Any], which: str | None, *,
                          prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> dict[str, Any] | None:
    """The denoise parameters for the LINEAR slot, or None when the tool runs after the stretch (lib/Pipeline.js)."""
    kw = {"prism2_linear_pass": prism2_linear_pass}
    p = composite_denoise_params(config, which, **kw)
    # Prism 2.0 without its linear pass and without a stretch: Medium's Ultra runs on the linear plate
    if p is not None and p["tool"] == NR_TOOL_STUDIO2 and not denoise_is_linear(p["tool"], **kw) \
            and not config.get("stretch"):
        q = prism2_pass_params(config, which, "unstretched", **kw)
        return q if q is not None else p
    if p is None or not denoise_is_linear(p["tool"], **kw):
        return None
    if p["tool"] == NR_TOOL_STUDIO2:
        q = prism2_pass_params(config, which, "linear", **kw)
        return q if q is not None else p
    return p


def stretched_denoise_params(config: Mapping[str, Any], which: str | None, *,
                             prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> dict[str, Any] | None:
    """The denoise parameters for the STRETCHED slot, or None when the tool runs on linear data (lib/Pipeline.js)."""
    kw = {"prism2_linear_pass": prism2_linear_pass}
    p = composite_denoise_params(config, which, **kw)
    if p is None:
        return None
    if p["tool"] == NR_TOOL_STUDIO2:
        if not config.get("stretch"):
            return None
        q = prism2_pass_params(config, which, "stretched", **kw)
        if q is not None:
            return q
        unknown = _studio2_ladder(prism2_linear_pass).get(p["level"]) is None
        return p if (unknown and not denoise_is_linear(p["tool"], **kw)) else None
    return None if denoise_is_linear(p["tool"], **kw) else p


def linear_denoise_pass(config: Mapping[str, Any]) -> str:
    """Which pass a linear-slot runner asks for: its own when a stretched slot follows, else the whole level."""
    return "linear" if config.get("stretch") else "all"


def studio_models_for(config: Mapping[str, Any], studio_found: bool, *,
                      prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> list[str]:
    """Every Studio model a configuration will run, each once, in pipeline order (lib/StepsSyqon.js studioModelsFor).

    ``studio_found`` matters only to BlurXTerminator, whose aberration pass Studio then runs.
    """
    out: list[str] = []

    def add(m: str) -> None:
        if m not in out:
            out.append(m)

    if gradient_tool_of(config) == GRADIENT_TOOL_STUDIO:
        add(STUDIO_MODEL_GRADIENT)
    if aberration_corrector(config.get("sharpenTool"), bool(studio_found)) == SHARPEN_TOOL_STUDIO:
        add(STUDIO_MODEL_PARALLAX)
    if config.get("starTool") == STAR_TOOL_STUDIO:
        add(STUDIO_MODEL_STARLESS)
    if config.get("noiseTool") == NR_TOOL_STUDIO:
        add(STUDIO_MODEL_DENOISE)
    if config.get("noiseTool") == NR_TOOL_STUDIO2:
        stretch = config.get("stretch") is not False
        ladder = _studio2_ladder(prism2_linear_pass)
        level_l = config.get("noiseLevelL") or config.get("noiseLevel")
        for lv in (config.get("noiseLevel"), level_l):
            level = supported_noise_level(NR_TOOL_STUDIO2, lv, stretch, prism2_linear_pass=prism2_linear_pass)
            step = ladder.get(level)
            if step is None:
                continue
            if step.get("linear"):
                add(step["linear"]["model"])
            if stretch and step.get("stretched"):
                add(step["stretched"]["model"])
            elif not stretch and not step.get("linear") and step.get("unstretched"):
                add(step["unstretched"]["model"])
    return out


def gradient_stage_params(config: Mapping[str, Any]) -> dict[str, Any]:
    """The gradient stage's cache parameters (lib/StepsSyqon.js gradientStageParams).

    GraXpert and none keep the shape of the old checkbox (enabled, smoothing) so existing cache entries still hit.
    """
    tool = gradient_tool_of(config)
    if tool == GRADIENT_TOOL_STUDIO:
        return {"enabled": True, "tool": tool, "model": STUDIO_MODEL_GRADIENT}
    return {"enabled": tool == GRADIENT_TOOL_GRAXPERT, "smoothing": config.get("smoothing")}


def finishing_params(names: Mapping[str, Any], config: Mapping[str, Any], *,
                     prism2_linear_pass: bool = PRISM2_LINEAR_PASS) -> dict[str, Any]:
    """The params half of Loom's finishingStages (lib/Pipeline.js): stage name -> parameters, in the chain's order.

    Order is sharpen, extract, linear denoise, stretch, stretched denoise. A role without a name, or whose parameters
    are None, is not in the result. ``names`` is one of RGB_FINISHING, PALETTE_FINISHING or L_FINISHING. The runners
    of the finishing tail are not ported here.
    """
    kw = {"prism2_linear_pass": prism2_linear_pass}
    linked = not names.get("unlinked")
    which = names.get("which")
    builders = [
        ("sharpen", lambda: composite_sharpen_params(config)),
        ("extract", lambda: star_extraction_params(config)),
        ("denoiseLinear", lambda: linear_denoise_params(config, which, **kw)),
        ("stretch", lambda: stretch_params(config, linked)),
        ("denoise", lambda: stretched_denoise_params(config, which, **kw)),
    ]
    params: dict[str, Any] = {}
    for role, build in builders:
        stage = names.get(role)
        if not stage:
            continue
        value = build()
        if value is not None:
            params[stage] = value
    return params
