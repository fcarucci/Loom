"""Checks run before a Loom run starts, and the validate-only report (lib/Pipeline.js:25-94, :167-203; Loom.js:58-71).

Every problem is collected and returned together; a run does not start while the list is not empty. Nothing here
touches the disk, a tool or Siril: the caller injects those (``read_header``, ``exists``, ``found``, ...), so every
check is unit-testable. ``headless`` selects the wording for the places where a run from the job file and a run from
the dialog must be told different ways to fix a problem.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from loomsiril import presets

from . import channels as ch
from . import levels as lv
from . import osc
from .siril_ops import OSC_NO_FRONT_FILTER, OSC_NO_LPF

# Loom's wording (lib/Pipeline.js:25-94, :167-203; lib/Util.js:603-669) is kept verbatim where a message names the
# same condition. Loom's "(SPFC cannot proceed; ...)" is dropped: the port has no SPFC step to name.
_NO_FILTER_TAIL = "(the script will not guess a filter)"

_GRAXPERT_TOOL = "graxpert-ai"
_STARNET_TOOL = "starnet2"
_SYQON_CLI = "syqon-cli"
_WCS_KEYWORDS = ("CTYPE1", "CRVAL1")

# Each choice in the three tool dropdowns and the gradient one, mapped to the tool that runs it (spec tool ids).
_GRADIENT_TOOLS: dict[str, str] = {
    lv.GRADIENT_TOOL_GRAXPERT: _GRAXPERT_TOOL,
    lv.GRADIENT_TOOL_STUDIO: _SYQON_CLI,
}
_SHARPEN_TOOLS: dict[str, str] = {
    lv.SHARPEN_TOOL_BXT: "rcastro",
    lv.SHARPEN_TOOL_SYQON: "parallax_cli",
    lv.SHARPEN_TOOL_STUDIO: _SYQON_CLI,
    lv.SHARPEN_TOOL_STUDIO_CORRECT_OLD: _SYQON_CLI,
}
_STAR_TOOLS: dict[str, str] = {
    lv.STAR_TOOL_SXT: "rcastro",
    lv.STAR_TOOL_STARNET: _STARNET_TOOL,
    lv.STAR_TOOL_SYQON: "SyQonStarless",
    lv.STAR_TOOL_STUDIO: _SYQON_CLI,
}
_NOISE_TOOLS: dict[str, str] = {
    lv.NR_TOOL_NXT: "rcastro",
    lv.NR_TOOL_PRISM: "prism_cli",
    lv.NR_TOOL_STUDIO: _SYQON_CLI,
    lv.NR_TOOL_STUDIO2: _SYQON_CLI,
    lv.NR_TOOL_STUDIO_OLD: _SYQON_CLI,
}

# OSC choice -> (config key, the kind Siril's database reader takes, the label ``siril_ops`` words it with).
_OSC_NAMES: tuple[tuple[str, str, str], ...] = (("sensor", "oscsensor", "sensor"),
                                                ("frontFilter", "oscfilter", "front filter"),
                                                ("lpf", "osclpf", "low-pass filter"))

# SPCC filter key -> the kind Siril's database reader takes (siril_ops.spcc_names).
_SPCC_KINDS: tuple[tuple[str, str], ...] = (("R", "redfilter"), ("G", "greenfilter"), ("B", "bluefilter"))


@dataclass
class Preflight:
    """Everything preflight reads from outside the config. Callables are the disk, the tools and Siril.

    ``found`` maps a tool id to its resolved ``Found`` (or None); ``rejection`` maps a tool id to the reason its last
    candidate was refused. ``graxpert_readiness`` returns the GraXpert-AI problems and is only called when GraXpert is
    found. ``studio_entitlement`` takes the Studio model ids and returns Loom's refusal lines (the ``problems`` of the
    Studio adapter after its probe run); an inconclusive probe returns nothing, as Loom only logs it. ``starnet_revision`` is the licence revision the job accepted; ``current_starnet_revision`` is the
    revision this build ships. ``spcc_database`` is Siril's SPCC folder, or None when it is missing; ``spcc_names``
    takes a kind ("redfilter", "greenfilter", "bluefilter", "monosensor") and returns the names in that folder.
    ``can_write_dir`` returns True when the folder can be created and a file written inside it.

    Plate-solving hints come from each channel's header, never from the config.
    """

    config: Mapping[str, Any]
    read_header: Callable[[str], Mapping[str, Any]]
    exists: Callable[[str], bool]
    readable: Callable[[str], bool]
    found: Mapping[str, Any]
    rejection: Mapping[str, str]
    graxpert_readiness: Callable[[], Sequence[str]] | None = None
    studio_entitlement: Callable[[Sequence[str]], Sequence[str]] | None = None
    starnet_revision: str | None = None
    current_starnet_revision: str = ""
    spcc_database: str | None = None
    spcc_names: Callable[[str], Sequence[str]] | None = None
    can_write_dir: Callable[[str], bool] | None = None
    local_astrometry: bool = False
    headless: bool = True


_SLOTS: list[str] = ch.CHANNELS + osc.COLOUR_SLOTS
_SENSOR_PROBLEM = "choose the sensor colour filters for the colour master (SPCC section)"


def _paths(config: Mapping[str, Any]) -> dict[str, str]:
    raw = config.get("paths") or {}
    return {k: str(raw[k]) for k in _SLOTS if raw.get(k)}


def _tool_needs(config: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(role, choice label, tool id) for each tool dropdown the config uses a tool from."""
    out: list[tuple[str, str, str]] = []
    gradient = lv.gradient_tool_of(config)
    if gradient in _GRADIENT_TOOLS:
        out.append(("gradient", gradient, _GRADIENT_TOOLS[gradient]))
    for role, key, table in (("sharpen", "sharpenTool", _SHARPEN_TOOLS),
                             ("star", "starTool", _STAR_TOOLS),
                             ("noise", "noiseTool", _NOISE_TOOLS)):
        choice = config.get(key)
        if isinstance(choice, str) and choice in table:
            out.append((role, choice, table[choice]))
    return out


def required_tools(config: Mapping[str, Any]) -> list[str]:
    """The tool ids the options need, each once, in dropdown order (gradient, sharpen, star, noise)."""
    out: list[str] = []
    for _role, _label, tool in _tool_needs(config):
        if tool not in out:
            out.append(tool)
    return out


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _pairs(header: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [(str(kw["name"]), str(kw["value"])) for kw in header.get("keywords", [])]


def _has_wcs(header: Mapping[str, Any]) -> bool:
    names = {name.strip().upper() for name, _value in _pairs(header)}
    return all(name in names for name in _WCS_KEYWORDS)


def _positive_keyword(pairs: Sequence[tuple[str, str]], name: str) -> bool:
    for key, value in pairs:
        if key.strip().upper() == name:
            try:
                number = float(value.strip().strip("'").strip())
            except ValueError:
                return False
            return 0 < number < float("inf")
    return False


def _missing_hints(header: Mapping[str, Any]) -> list[str]:
    """The plate-solving hints a header lacks: RA and DEC (presets.header_centre order), FOCALLEN, XPIXSZ."""
    pairs = _pairs(header)
    missing: list[str] = []
    if presets.header_centre(pairs) is None:
        missing.append("RA/DEC")
    if not _positive_keyword(pairs, "FOCALLEN"):
        missing.append("FOCALLEN")
    if not _positive_keyword(pairs, "XPIXSZ"):
        missing.append("XPIXSZ")
    return missing


def preflight(inputs: Preflight) -> list[str]:
    """Every problem with the run, in the order of the checks; an empty list means the run may start."""
    cfg = inputs.config
    paths = _paths(cfg)
    headless = inputs.headless

    def active(tool: str) -> bool:
        # A dialog run shows an unavailable saved tool as none, so nothing depends on it (see check 3).
        return headless or inputs.found.get(tool) is not None

    problems: list[str] = []

    # 1. Selection rules (Util.js:603-669).
    problems.extend(ch.validate_selection(paths))

    # Every input exists and is readable, and its header is read once; the problems of this pass are reported at
    # step 2, after the colour checks that need the headers.
    headers: dict[str, Mapping[str, Any]] = {}
    file_problems: list[str] = []
    for k in _SLOTS:
        p = paths.get(k)
        if not p:
            continue
        if not inputs.exists(p):
            file_problems.append(f"File not found for {k}: {p}")
            continue
        if not inputs.readable(p):
            file_problems.append(f"File not readable for {k}: {p}")
            continue
        try:
            header = inputs.read_header(p)
        except (OSError, ValueError) as exc:
            file_problems.append(f"Cannot read the header of {k}: {p} ({exc})")
            continue
        headers[k] = header
        if k in ch.BROADBAND and ch.keyword_value(header.get("keywords", []), "FILTER") is None:
            file_problems.append(f"No FILTER keyword in {k}: {p} {_NO_FILTER_TAIL}")

    # 1b. Colour masters, in Loom's order (Pipeline.js:63-72): the mix, the palette, the channel counts of every
    #     input, the kinds, then the sensor. Only the OSC master is calibrated with the sensor (a narrowband SPCC in a
    #     fresh session takes no names), so only a run that supplies OSC needs it.
    mix = osc.colour_mix_problem(cfg)
    if mix is not None:
        problems.append(mix)
    palette = osc.osc_palette_problem(cfg)
    if palette is not None:
        problems.append(palette)
    both_h = osc.osc_combination_problem(cfg)
    if both_h is not None:
        problems.append(both_h)

    def info_of(slot: str) -> dict[str, Any] | None:
        header = headers.get(slot)
        if header is None:
            return None
        return {"path": paths.get(slot), "numberOfChannels": header.get("channels"),
                "filter": ch.keyword_value(header.get("keywords", []), "FILTER"),
                "instrume": ch.keyword_value(header.get("keywords", []), "INSTRUME"),
                "width": header.get("width"), "height": header.get("height")}

    problems.extend(osc.input_channel_problems(cfg, info_of))
    problems.extend(osc.osc_kind_problems(cfg, info_of))  # type: ignore[arg-type]  # fills config["oscKinds"]
    has_osc = "OSC" in osc.colour_supplied(cfg)
    # The sensor colour filters: the user's choice, else the one the OSC master's INSTRUME names. What was decided,
    # and why, is left in config["oscSensorNote"] for the run log (never persisted).
    reason = ""
    if has_osc:
        osc_info = info_of("OSC")
        instrume = osc_info.get("instrume") if osc_info else None
        cfg["oscSensorNote"] = osc.resolve_osc_sensor(cfg, instrume)  # type: ignore[arg-type,index]
        reason = osc.auto_osc_sensor(instrume)["reason"]
    sensor = cfg.get("oscSensor")
    if has_osc and _blank(sensor.get("sensor") if isinstance(sensor, Mapping) else None):
        problems.append(_SENSOR_PROBLEM + ": " + reason)

    # 2. The files: not found, not readable, header unreadable, a broadband master without a FILTER keyword.
    problems.extend(file_problems)

    # 3. Each chosen external tool was found and passed its probe. Headless only: a dialog run shows it as none.
    if headless:
        for role, label, tool in _tool_needs(cfg):
            if inputs.found.get(tool) is None:
                reason = inputs.rejection.get(tool)
                tail = f": {reason}" if reason else ""
                problems.append(f"{label} not found (choose another {role} tool to proceed without){tail}")

    # 4. GraXpert-AI is ready to run: script, model and packages (never downloads).
    if (lv.gradient_tool_of(cfg) == lv.GRADIENT_TOOL_GRAXPERT and inputs.found.get(_GRAXPERT_TOOL) is not None
            and inputs.graxpert_readiness is not None):
        problems.extend(inputs.graxpert_readiness())

    # 5. The account may run each SyQon Studio model this run uses: Loom's refusal lines, verbatim
    #    (lib/StepsSyqon.js:1662-1680). An inconclusive probe gives no lines.
    studio_found = inputs.found.get(_SYQON_CLI) is not None
    if studio_found and inputs.studio_entitlement is not None:
        models = lv.studio_models_for(cfg, True)
        if models:
            problems.extend(inputs.studio_entitlement(models))

    # 6. StarNet2's licence is accepted at the current revision.
    if lv.STAR_TOOL_STARNET == cfg.get("starTool") and active(_STARNET_TOOL):
        rev = inputs.starnet_revision
        if not rev or rev != inputs.current_starnet_revision:
            if headless:
                problems.append("StarNet2 needs its licence accepted: set starnetLicenceAccepted in the job file")
            else:
                problems.append("StarNet2 needs its licence accepted: choose Run StarNet in the dialog and accept it")

    # 7. R, G and B are named SPCC filters that exist in Siril's database; the database folder exists whenever an
    #    SPCC runs (an RGB set, a broadband colour master, or a palette), because Siril crashes without it.
    #    The database's names are the SPCC stages' to check; a colour master has no filter names to look up.
    rgb_set = all(k in paths for k in ch.RGB_GROUP)
    spcc_runs = rgb_set or all(k in osc.supplied_channels(cfg) for k in ch.RGB_GROUP)
    if rgb_set:
        filters = cfg.get("filters") or {}
        for k, kind in _SPCC_KINDS:
            name = filters.get(k)
            if _blank(name):
                problems.append(f"No SPCC filter chosen for {k}: " + _filter_fix(headless, k))
            elif inputs.spcc_database is not None and inputs.spcc_names is not None:
                if name not in inputs.spcc_names(kind):
                    problems.append(f"SPCC filter {name} for {k} is not in Siril's SPCC database: "
                                    + _filter_fix(headless, k))
    if has_osc and isinstance(sensor, Mapping) and not _blank(sensor.get("sensor")):
        # The OSC master's sensor, front filter and low-pass filter are Siril's own database names, with its "none"
        # entries for an unchosen filter: the command checks them too (siril_ops), preflight says so before the run.
        if inputs.spcc_database is not None and inputs.spcc_names is not None:
            for key, kind, label in _OSC_NAMES:
                name = sensor.get(key)
                if _blank(name):
                    name = {"frontFilter": OSC_NO_FRONT_FILTER, "lpf": OSC_NO_LPF}.get(key)
                if name not in inputs.spcc_names(kind):
                    problems.append(f"{label} {name!r} is not in Siril's SPCC database")
    if (spcc_runs or ch.wanted_palettes(cfg)) and inputs.spcc_database is None:
        problems.append("Siril's SPCC database folder is missing: open Siril's GUI once so it downloads "
                        "its SPCC database")

    # 8. An output folder is always given, and can be created and written.
    out = cfg.get("outputDir")
    if _blank(out):
        problems.append("No output folder: set outputDir" if headless
                        else "No output folder: choose one in the dialog")
    elif inputs.can_write_dir is not None and not inputs.can_write_dir(str(out)):
        problems.append(f"Output folder cannot be created or written: {out}")

    # 9. Each channel is plate-solvable: a solution already in its header, or the hints in its header (RA and DEC,
    #    FOCALLEN, XPIXSZ), or a local astrometry.net. Config values never stand in for header hints.
    if not inputs.local_astrometry:
        for k in _SLOTS:
            if k not in headers or _has_wcs(headers[k]):
                continue
            missing = _missing_hints(headers[k])
            if missing:
                problems.append(f"Plate solving not possible for {k}: {paths[k]} "
                                f"(no solution in its header; missing hints in its header: {', '.join(missing)})")

    # 10. Not a problem: exportDir with stretch off is refused with a warning at run time.
    return problems


def _filter_fix(headless: bool, key: str) -> str:
    if headless:
        return f"set filters {key} in the job file"
    return "pick filters in the dialog"


def validate_only_report(config: Mapping[str, Any]) -> list[str]:
    """The validate-only lines for a file selection (Loom.js:58-71): the verdict, then one line per channel."""
    lines = ["All checks passed. Nothing executed."]
    paths = _paths(config)
    for k in ch.CHANNELS:
        if k in paths:
            lines.append(f"{k} <- {paths[k]}")
    return lines
