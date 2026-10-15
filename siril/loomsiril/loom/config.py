"""Loom's options: defaults, the field table, load and save, and migrate_config.

Ported from Loom's Config (lib/Config.js:68-98 FIELDS, :126-188 defaults,
:238-390 load and save) and Steps.migrateConfig (lib/StepsSyqon.js:1533-1573),
with Siril's changes: the options live in one JSON file, the output folder is
saved with them, and the tools Siril does not have are migrated to what it does.

The file is ``<config_dir>/loomsiril/loom.json``. Reading never raises for a bad
file: unknown keys, wrong types and unreadable JSON produce a warning through
the ``warn`` callback and fall back to the defaults.
"""

from __future__ import annotations

import json
import math
import os
import threading
from typing import Any, Callable, Mapping

from loomsiril.loom.levels import (
    GRADIENT_TOOL_GRAXPERT,
    GRADIENT_TOOL_NONE,
    NR_TOOL_STUDIO,
    NR_TOOL_STUDIO2,
    NR_TOOL_STUDIO_OLD,
    SHARPEN_TOOL_BXT,
    SHARPEN_TOOL_STUDIO_CORRECT_OLD,
    STRETCH_METHOD_GHS,
    STRETCH_METHOD_MTF,
    STUDIO_PARALLAX_FAMILIES,
    STUDIO_PARALLAX_FAMILY,
)

WarnFn = Callable[[str], None]

# Tool-name strings that levels.py does not keep: the migration needs them to recognise what an old file holds.
NR_TOOL_MLDENOISE = "MLDenoise"  # same strings as Loom lib/Steps.js:1537
STRETCH_METHOD_MAS = "MultiscaleAdaptiveStretch"  # same strings as Loom lib/Steps.js:3076

FILTER_KEYS = ("R", "G", "B")  # Siril SPCC filter names; L, H, S and O are not kept
NOISE_LEVELS = ("low", "medium", "high")

# Keys the file never holds: ignoreCache, validateOnly and keepWindowsOnError are for one run only (lib/Config.js:186-188,
# and Config.save writes neither validateOnly nor keepWindowsOnError).
# oscKinds is filled by preflight for one run.
_NEVER_PERSISTED = ("ignoreCache", "validateOnly", "keepWindowsOnError", "oscKinds")

# The plain settings. "type" is what a stored value must be; "load" is "set"
# (a stored value replaces the default unless null), "text" (the same, but an
# empty string is ignored too), or None (read by other code); "save" is "bool"
# (written as bool), "as is", or {"or": d} (written as the value or d when falsy).
FIELDS: dict[str, dict[str, Any]] = {
    "graxpertNarrowband": {"type": "bool", "load": "set", "save": "bool"},
    "narrowbandBandwidth": {"type": "float", "load": None, "save": {"or": 3.0}},
    "narrowbandNormalize": {"type": "bool", "load": "set", "save": "bool"},
    "reduceHalos": {"type": "bool", "load": "set", "save": "bool"},
    "keepLinear": {"type": "bool", "load": "set", "save": "bool"},
    "separateLStars": {"type": "bool", "load": "set", "save": "bool"},
    "exportPsb": {"type": "bool", "load": "set", "save": "bool"},
    "projectName": {"type": "str", "load": "set", "save": {"or": ""}},
    "exportDir": {"type": "str", "load": "set", "save": {"or": ""}},
    "outputDir": {"type": "str", "load": "set", "save": {"or": ""}},  # port: the dialog reopens with it
    "stretch": {"type": "bool", "load": "set", "save": "bool"},
    "stretchMethod": {"type": "str", "load": "text", "save": {"or": STRETCH_METHOD_MTF}},
    "stretchTarget": {"type": "float", "load": None, "save": {"or": 0.15}},
    "starTool": {"type": "str", "load": "text", "save": {"or": "none"}},
    "noiseTool": {"type": "str", "load": "text", "save": {"or": "none"}},
    "noiseLevel": {"type": "str", "load": "text", "save": {"or": "medium"}},
    # Empty is kept: it is a choice, "follow the colour level", not a gap.
    "noiseLevelL": {"type": "str", "load": "set", "save": {"or": ""}},
    "sharpenTool": {"type": "str", "load": "text", "save": {"or": "none"}},
    "starReduction": {"type": "str", "load": "text", "save": {"or": "none"}},
    "detailLevel": {"type": "str", "load": "text", "save": {"or": "none"}},
    "parallaxFamily": {"type": "str", "load": "text", "save": {"or": STUDIO_PARALLAX_FAMILY}},
    "useCache": {"type": "bool", "load": "set", "save": "as is"},
    "cacheDir": {"type": "str", "load": "set", "save": {"or": ""}},
    "smoothing": {"type": "float", "load": "set", "save": "as is"},
    "savedList": {"type": "str", "load": None, "save": {"or": ""}},
    "spccSensor": {"type": "str", "load": "set", "save": {"or": ""}},
}

# Keys outside FIELDS, with the type a stored value must have.
_EXTRA_TYPES: dict[str, str] = {
    "paths": "str_map",
    "filters": "str_map",
    "palettes": "str_list",
    "gradientTool": "str",
    "oscKindChoices": "object",
    "oscSensor": "object",
}

# What a remembered one-shot-colour answer may be, and the keys of the chosen sensor (Siril SPCC database names).
OSC_KIND_VALUES = ("broadband", "dualband")
OSC_SENSOR_KEYS = ("sensor", "frontFilter", "lpf")

# How a stored value of each type is described in a warning.
_KIND_TEXT = {
    "bool": "true or false",
    "str": "text",
    "float": "a number",
    "str_map": "an object of text values",
    "str_list": "a list of text",
    "object": "an object",
}

# A legacy checkbox, read only to derive gradientTool (lib/Config.js:238-273).
_LEGACY_GRADIENT_KEY = "useGraXpert"


def _no_warn(text: str) -> None:
    """The default warn callback: drops every message."""
    return None


def defaults() -> dict[str, Any]:
    """Loom's defaults, minus views, marsPath and autoUpdate, plus spccSensor and cacheDir."""
    return {
        "paths": {"L": "", "R": "", "G": "", "B": "", "H": "", "S": "", "O": "", "OSC": "", "OSCNB": ""},
        "savedList": "",
        "filters": {},
        "gradientTool": GRADIENT_TOOL_GRAXPERT,
        "graxpertNarrowband": False,
        "smoothing": 0.5,
        "validateOnly": False,
        "keepWindowsOnError": False,
        "palettes": [],
        "narrowbandBandwidth": 3.0,
        "narrowbandNormalize": True,
        "reduceHalos": False,
        "sharpenTool": "none",
        "parallaxFamily": STUDIO_PARALLAX_FAMILY,
        "stretch": False,
        "stretchMethod": STRETCH_METHOD_MTF,
        "stretchTarget": 0.15,
        "keepLinear": False,
        "separateLStars": False,
        "exportPsb": False,
        "projectName": "",
        "exportDir": "",
        "outputDir": "",
        "starTool": "none",
        "noiseTool": "none",
        "noiseLevel": "medium",
        "noiseLevelL": "",
        "starReduction": "none",
        "detailLevel": "none",
        "useCache": True,
        "cacheDir": "",
        "spccSensor": "",
        "ignoreCache": False,
        "oscKinds": {},
        "oscSensor": {"sensor": "", "frontFilter": "", "lpf": ""},
        "oscKindChoices": {},
    }


def config_path(config_dir: str | os.PathLike[str]) -> str:
    """The options file under Siril's configuration directory."""
    return os.path.join(os.fspath(config_dir), "loomsiril", "loom.json")


def gradient_tool_of(config: Mapping[str, Any]) -> str:
    """The gradient tool a configuration asks for (lib/StepsSyqon.js:1483-1490).

    A non-empty gradientTool wins. Otherwise the legacy useGraXpert checkbox
    decides: true is GraXpert, anything else is none.
    """
    tool = config.get("gradientTool")
    if isinstance(tool, str) and tool:
        return tool
    return GRADIENT_TOOL_GRAXPERT if config.get("useGraXpert") is True else GRADIENT_TOOL_NONE


def _noise_levels_for(tool: Any, stretch_on: bool) -> tuple[str, ...]:
    """The strengths a tool offers (lib/Steps.js:2116-2127).

    Only Prism 2.0 is restricted. Its ladder has Medium with and without the
    stretch, and High only with it (PRISM2_LINEAR_PASS is off).
    """
    if tool != NR_TOOL_STUDIO2:
        return NOISE_LEVELS
    return tuple(level for level in NOISE_LEVELS if level == "medium" or (level == "high" and stretch_on))


def supported_noise_level(tool: Any, level: Any, stretch_on: bool) -> Any:
    """`level` if the tool offers it, else Medium (lib/Steps.js:2135-2140).

    A value that is not low, medium or high is returned unchanged.
    """
    if level not in NOISE_LEVELS:
        return level
    return level if level in _noise_levels_for(tool, stretch_on) else "medium"


def migrate_config(
    config: dict[str, Any],
    warn: WarnFn = _no_warn,
    studio_found: bool = False,
    studio_prism2_unavailable: bool = False,
) -> dict[str, Any]:
    """Bring a loaded configuration up to date, in place, and return it.

    Every case of Loom's Steps.migrateConfig, plus the Siril replacements:
    MultiscaleAdaptiveStretch becomes GHS, MLDenoise becomes off, and view
    entries in savedList are dropped.

    `studio_found` and `studio_prism2_unavailable` say what SyQon Studio offers:
    Prism 2.0 is offered when Studio is found and not refused. When Studio is
    not found, Studio's own names are left alone (Loom passes undefined here).
    """
    prism2_offered: bool | None = (not studio_prism2_unavailable) if studio_found else None

    config["gradientTool"] = gradient_tool_of(config)
    # Essential's old name; the model, and so the pixels, are unchanged.
    if config.get("noiseTool") == NR_TOOL_STUDIO_OLD:
        config["noiseTool"] = NR_TOOL_STUDIO
    # Studio's Prism is one entry, 2.0 or Essential; the other choice loads as the one offered.
    if prism2_offered is True and config.get("noiseTool") == NR_TOOL_STUDIO:
        config["noiseTool"] = NR_TOOL_STUDIO2
    elif prism2_offered is False and config.get("noiseTool") == NR_TOOL_STUDIO2:
        config["noiseTool"] = NR_TOOL_STUDIO

    if config.get("noiseTool") == NR_TOOL_MLDENOISE:
        warn("MLDenoise is not available in Siril; denoise is off")
        config["noiseTool"] = "none"

    if config.get("stretchMethod") == STRETCH_METHOD_MAS:
        warn("MultiscaleAdaptiveStretch is not available in Siril; using the Siril GHS stretch instead")
        config["stretchMethod"] = STRETCH_METHOD_GHS

    # A strength the tool does not offer loads as Medium. The stretch is on
    # unless it is explicitly false (`config.stretch !== false` in Loom).
    stretch_on = config.get("stretch") is not False
    if "noiseLevel" in config:
        config["noiseLevel"] = supported_noise_level(config.get("noiseTool"), config["noiseLevel"], stretch_on)
    if config.get("noiseLevelL"):
        config["noiseLevelL"] = supported_noise_level(config.get("noiseTool"), config["noiseLevelL"], stretch_on)

    # The retired "correct only" Parallax entry is BlurXTerminator now; its
    # star reduction and detail stay off rather than switching on unseen levels.
    if config.get("sharpenTool") == SHARPEN_TOOL_STUDIO_CORRECT_OLD:
        config["sharpenTool"] = SHARPEN_TOOL_BXT
        config["starReduction"] = "none"
        config["detailLevel"] = "none"

    saved = config.get("savedList")
    if isinstance(saved, str) and saved:
        lines = saved.split("\n")
        kept = [line for line in lines if not line.startswith("view\t")]
        if len(kept) != len(lines):
            warn("view entries in the saved list are not kept in Siril; they were dropped")
            config["savedList"] = "\n".join(kept)

    return config


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _matches(kind: str, value: Any) -> bool:
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "str":
        return isinstance(value, str)
    if kind == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if kind == "str_map":
        return isinstance(value, dict) and all(isinstance(v, str) for v in value.values())
    if kind == "str_list":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if kind == "object":
        return isinstance(value, dict)
    raise ValueError(kind)


def _kind_of(key: str) -> str | None:
    if key in FIELDS:
        return FIELDS[key]["type"]
    return _EXTRA_TYPES.get(key)


def _apply_document(config: dict[str, Any], raw: dict[str, Any], warn: WarnFn, path: str) -> None:
    """Copies the stored values in `raw` over `config`, warning about each problem."""
    for key, value in raw.items():
        if key in _NEVER_PERSISTED:
            continue
        if key == _LEGACY_GRADIENT_KEY or key == "gradientTool":
            continue  # handled together below
        kind = _kind_of(key)
        if kind is None:
            warn(f"unknown option {key!r} in {path}; ignored")
            continue
        if value is None:
            continue
        if FIELDS.get(key, {}).get("load") == "text" and value == "":
            continue
        if not _matches(kind, value):
            warn(f"{key}: expected {_KIND_TEXT[kind]}, got {_type_name(value)}; using the default")
            continue
        if key == "narrowbandBandwidth" and value <= 0:
            warn("narrowbandBandwidth must be positive; using the default")
            continue
        if key == "paths":
            allowed = config["paths"]
            for channel, path_value in value.items():
                if channel in allowed:
                    allowed[channel] = path_value
                else:
                    warn(f"paths: channel {channel!r} is not kept; ignored")
            continue
        if key == "filters":
            kept: dict[str, str] = {}
            for name, filter_value in value.items():
                if name not in FILTER_KEYS:
                    warn(f"filters: {name!r} is not an R, G or B filter; ignored")
                elif filter_value:
                    kept[name] = filter_value
            config["filters"] = kept
            continue
        if key == "oscKindChoices":
            kept_choices: dict[str, str] = {}
            for name, choice in value.items():
                if choice in OSC_KIND_VALUES:
                    kept_choices[name] = choice
                else:
                    warn(f"oscKindChoices: {name!r} is not broadband or dualband; ignored")
            config[key] = kept_choices
            continue
        if key == "oscSensor":
            sensor = dict(config["oscSensor"])
            for name, text in value.items():
                if name not in OSC_SENSOR_KEYS:
                    warn(f"oscSensor: {name!r} is not kept; ignored")
                elif isinstance(text, str):
                    sensor[name] = text
                else:
                    warn(f"oscSensor: {name} expected text, got {_type_name(text)}; ignored")
            config[key] = sensor
            continue
        if key == "parallaxFamily":
            value = value if value in STUDIO_PARALLAX_FAMILIES else STUDIO_PARALLAX_FAMILY
        config[key] = value

    # The gradient tool: a stored name wins; else the legacy checkbox decides (Config.loadGradientSetting).
    tool = raw.get("gradientTool")
    legacy = raw.get(_LEGACY_GRADIENT_KEY)
    if isinstance(tool, str) and tool:
        config["gradientTool"] = tool
    elif isinstance(legacy, bool):
        config[_LEGACY_GRADIENT_KEY] = legacy
        config["gradientTool"] = ""
    elif legacy is not None:
        warn(f"useGraXpert: expected true or false, got {_type_name(legacy)}; using the default")
    if tool is not None and not isinstance(tool, str):
        warn(f"gradientTool: expected text, got {_type_name(tool)}; using the default")


def apply_options(config: dict[str, Any], raw: dict[str, Any], warn: WarnFn, source: str) -> None:
    """Copies the options in `raw` (a job file, or a saved document) over `config`, with the saved file's checks.

    Each problem is reported through `warn` (the text names `source`) and the value is skipped. The run flags that
    a saved file never holds (validateOnly, ignoreCache, keepWindowsOnError) are not copied here.
    """
    _apply_document(config, raw, warn, source)


def load(config_dir: str | os.PathLike[str], warn: WarnFn = _no_warn, **availability: Any) -> dict[str, Any]:
    """The configuration stored under `config_dir`, with the defaults for anything missing.

    A missing file gives the defaults silently. A file that cannot be read or
    parsed gives the defaults with a warning. `availability` (studio_found,
    studio_prism2_unavailable) is passed on to migrate_config.
    """
    path = config_path(config_dir)
    config = defaults()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError, RecursionError) as exc:
            warn(f"could not read {path} ({exc}); using the defaults")
            config = defaults()
        else:
            if isinstance(raw, dict):
                _apply_document(config, raw, warn, path)
            else:
                warn(f"{path} does not hold a JSON object; using the defaults")
    return migrate_config(config, warn, **availability)


def _document(config: Mapping[str, Any]) -> dict[str, Any]:
    """What is written for `config`: every persisted key, in the form Loom saves it."""
    base = defaults()
    doc: dict[str, Any] = {}
    for key in base:
        if key in _NEVER_PERSISTED:
            continue
        if key == "gradientTool":
            doc[key] = gradient_tool_of(config)
            continue
        value = config.get(key, base[key])
        spec = FIELDS.get(key)
        if spec is not None:
            save = spec["save"]
            if save == "bool":
                value = bool(value)
            elif save != "as is":
                value = value or save["or"]
        elif value is None:
            value = base[key]
        if key == "filters":
            value = {name: value[name] for name in FILTER_KEYS if value.get(name)}
        doc[key] = value
    return doc


_SAVE_LOCK = threading.Lock()   # the dialog autosaves from a worker thread while Run or Cancel saves from the main one


def save(config: Mapping[str, Any], config_dir: str | os.PathLike[str]) -> str:
    with _SAVE_LOCK:
        return _save_locked(config, config_dir)


def _save_locked(config: Mapping[str, Any], config_dir: str | os.PathLike[str]) -> str:
    """Writes `config` to the options file atomically and returns the file's path.

    The text goes to ``loom.json.tmp`` beside the file, is flushed and fsynced,
    then replaces the file in one step. A failed write leaves no temporary file.
    """
    path = config_path(config_dir)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, "loom.json.tmp")
    text = json.dumps(_document(config), indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path
