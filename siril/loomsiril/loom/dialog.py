"""Loom's options dialog for Siril: a pure view model and thin tkinter views.

All decisions live in :class:`DialogModel` (what each drop-down offers, which controls are enabled, what a saved
value turns into when its tool is gone, when Run is allowed, what the run receives) and :class:`ProgressModel`
(what the progress window shows and when a cancel takes effect). Neither imports tkinter or sirilpy, so both are
unit-tested without a display. :class:`LoomDialog` and :class:`ProgressWindow` only bind widgets to the models and
call their methods; tkinter is imported inside them, never at module import.

The options follow Loom's names (lib/Config.js:68-98). ``outputDir`` is not an option: the output folder starts
empty every time the dialog opens and is never written to the options file.

Ported from Loom's dialog (lib/UI.js: progress window :531-600, master list :879-897, tool drop-downs :1050, :1184,
:1231, interlocks :1103-1110 and :1401-1415, Scan Masters Folder :1961).
"""

from __future__ import annotations

import copy
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from loomsiril import starnet_licence
from loomsiril.loom import channels as ch
from loomsiril.loom import config as cfg
from loomsiril.loom import levels as lv
from loomsiril.loom.cache import CacheStore, default_cache_dir

#: The message the dialog shows before a run replaces the image loaded in Siril.
REPLACE_CONFIRM_TEXT = "The image loaded in Siril will be replaced"

#: The saved-options key (and ToolStore flag) that remembers the accepted StarNet2 licence revision.
STARNET_FLAG = "starnetLicenceAccepted"

#: The choice every drop-down offers first.
NONE = "none"

#: Tool id -> what the dialog calls it (the ids are loomsiril.loom.tools.specs.TOOLS keys).
TOOL_LABELS: dict[str, str] = {
    "rcastro": "RC-Astro CLI (BlurXTerminator, NoiseXTerminator, StarXTerminator)",
    "syqon-cli": "SyQon Studio CLI",
    "prism_cli": "SyQon Prism CLI",
    "parallax_cli": "SyQon Parallax CLI",
    "SyQonStarless": "SyQon Starless CLI",
    "starnet2": "StarNet2",
    "graxpert-ai": "GraXpert-AI",
}

#: Drop-down groups, in the order they appear on screen.
GROUP_GRADIENT = "gradient"
GROUP_SHARPEN = "sharpen"
GROUP_STAR = "star"
GROUP_NOISE = "noise"
GROUPS: tuple[str, ...] = (GROUP_GRADIENT, GROUP_SHARPEN, GROUP_STAR, GROUP_NOISE)

#: The option that holds each group's tool choice.
GROUP_KEYS: dict[str, str] = {
    GROUP_GRADIENT: "gradientTool",
    GROUP_SHARPEN: "sharpenTool",
    GROUP_STAR: "starTool",
    GROUP_NOISE: "noiseTool",
}

#: Choice value -> the tool that runs it, for the tools whose drop-down choices the tool probe decides.
_CHOICE_TOOL: dict[str, str] = {
    lv.NR_TOOL_NXT: "rcastro",
    lv.NR_TOOL_PRISM: "prism_cli",
    lv.NR_TOOL_STUDIO: "syqon-cli",
    lv.NR_TOOL_STUDIO2: "syqon-cli",
    lv.NR_TOOL_STUDIO_OLD: "syqon-cli",
    lv.SHARPEN_TOOL_BXT: "rcastro",
    lv.SHARPEN_TOOL_SYQON: "parallax_cli",
    lv.SHARPEN_TOOL_STUDIO: "syqon-cli",
    lv.STAR_TOOL_STARNET: "starnet2",
    lv.STAR_TOOL_SXT: "rcastro",
    lv.STAR_TOOL_SYQON: "SyQonStarless",
    lv.STAR_TOOL_STUDIO: "syqon-cli",
    lv.GRADIENT_TOOL_GRAXPERT: "graxpert-ai",
    lv.GRADIENT_TOOL_STUDIO: "syqon-cli",
}

#: The levels a star reduction or detail drop-down offers (the keys of the sharpening ladders).
SHARPEN_LEVELS: tuple[str, ...] = ("none", "low", "medium", "high")

#: SPCC filter key -> the kind Siril's database reader takes.
_FILTER_KINDS: dict[str, str] = {"R": "redfilter", "G": "greenfilter", "B": "bluefilter"}

#: Option names whose value is kept out of the options file (none: every option, the output folder included, is
#: saved so the dialog reopens as it was left).
_NOT_IN_CONFIG: tuple[str, ...] = ()

#: The option names this model holds as values (Loom's names), with the config's defaults.
#: outputDir is not one of them: the model keeps it apart (output_dir / set_output_dir) and to_config adds it.
_VALUE_KEYS: tuple[str, ...] = tuple(k for k in cfg.FIELDS if k != "outputDir") + ("gradientTool", "validateOnly",
                                                                                   "ignoreCache")

#: Loom's ranges for the two numeric options (lib/UI.js: narrowband bandwidth 0.5-30 nm, GraXpert smoothing 0-1).
BANDWIDTH_RANGE: tuple[float, float] = (0.5, 30.0)
SMOOTHING_RANGE: tuple[float, float] = (0.0, 1.0)

#: The SPCC sensor kind Siril's database reader takes for the camera sensor.
SENSOR_KIND = "monosensor"

#: Options whose enabled state depends on another option (stretch, sharpening, noise, cache).
_STRETCH_CONTROLS = ("keepLinear", "stretchMethod", "exportDir", "exportPsb")

#: Labels for the stretch methods (Loom's stretch drop-down, lib/UI.js:1286).
STRETCH_METHOD_LABELS: dict[str, str] = {
    lv.STRETCH_METHOD_MTF: "Histogram (deterministic MTF)",
    lv.STRETCH_METHOD_GHS: "Generalised hyperbolic (Siril GHS)",
}

#: The tool-probe result the model reads, per tool id: a Found, or None when not found.
FoundMap = Mapping[str, Any]
#: Injected run check: the run request in, the problems out (empty when the run may start).
PreflightFn = Callable[["RunRequest"], Sequence[str]]
#: Injected SPCC lookup: a kind ("redfilter", "greenfilter", "bluefilter") in, the names in Siril's folder out.
SpccFn = Callable[[str], Sequence[str]]


@dataclass(frozen=True)
class ToolRow:
    """One line of the Tools section: the tool, its found path (None when not found) and the reason it is missing."""

    tool_id: str
    label: str
    path: Optional[str]
    status: str


@dataclass(frozen=True)
class RunRequest:
    """What the orchestrator receives when the run starts: the options, the output folder and the StarNet revision."""

    config: dict[str, Any]
    output_dir: str
    starnet_revision: Optional[str]


#: The reason Run gives while the preflight runs in the background.
CHECKING_TEXT = "Checking the inputs..."


class RunBlocked(ValueError):
    """The run cannot start. ``reasons`` lists every problem at once, so they are shown together."""

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons: list[str] = list(reasons)
        super().__init__("\n".join(self.reasons))


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


class DialogModel:
    """The dialog's state and rules. Every control reads from and writes to this object; nothing else decides.

    ``found_tools`` maps each tool id to its resolver result (a ``Found``, or None when not found or not passed).
    ``store`` is the ToolStore that remembers located tools and the accepted StarNet licence (both optional).
    ``resolver`` is the Resolver used by ``locate`` and ``rescan`` (both are no-ops without it).
    ``preflight`` runs the run checks and returns their problems; ``spcc_names`` lists Siril's filter names.
    ``platform``, ``env`` and ``home`` locate the default cache folder (the running platform by default).
    ``measure`` measures masters for the list: (paths, cache folder) -> {path: quality}; slow (Siril reads each
    file), so the window calls it off the Tk thread. Without it the measurement cells stay empty.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        found_tools: Optional[FoundMap] = None,
        *,
        store: Any = None,
        resolver: Any = None,
        preflight: Optional[PreflightFn] = None,
        spcc_names: Optional[SpccFn] = None,
        studio_prism2_unavailable: bool = False,
        platform: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        home: Optional[str] = None,
        now: Callable[[], str] = _now_text,
        measure: Optional[Callable[[Sequence[str], str], Mapping[str, Mapping[str, Any]]]] = None,
    ) -> None:
        self._store = store
        self._measure = measure
        self._quality: dict[str, dict[str, Any]] = {}
        self._measuring = False
        self._resolver = resolver
        self._preflight = preflight
        self._spcc_names = spcc_names
        self._prism2_unavailable = bool(studio_prism2_unavailable)
        self._platform = platform
        self._env = env
        self._home = home
        self._now = now
        self._notices: list[str] = []
        self._problems_key: Optional[str] = None
        self._problems_cache: list[str] = []
        #: The window sets this: the preflight (SyQon test runs, file reads) then never runs on the UI thread.
        self.async_preflight = False
        #: The window calls this with the current options (to_config) shortly after any change, so a window that is
        #: never closed properly (Siril quit, a crash) still leaves what was chosen.
        self.persist: Optional[Callable[[dict[str, Any]], None]] = None
        self._spcc_cache: dict[str, list[str]] = {}
        self._scan: Optional[dict[str, Any]] = None
        self._output_dir = ""

        self._found: dict[str, Any] = {tool_id: None for tool_id in TOOL_LABELS}
        for tool_id, found in (found_tools or {}).items():
            if tool_id in self._found:
                self._found[tool_id] = found

        source = dict(config)
        for key in _NOT_IN_CONFIG:
            source.pop(key, None)
        defaults = cfg.defaults()
        merged = {**defaults, **source}
        self._base: dict[str, Any] = {k: v for k, v in source.items() if k != STARNET_FLAG}
        self._paths: dict[str, Any] = dict(source.get("paths") or {})
        self._output_dir = str(source.get("outputDir") or "")
        self._filters: dict[str, Any] = dict(source.get("filters") or {})
        self._palettes: list[str] = list(source.get("palettes") or [])

        migrated = cfg.migrate_config(copy.deepcopy(merged), warn=self._notices.append,
                                      studio_found=self._has("syqon-cli"),
                                      studio_prism2_unavailable=self._prism2_unavailable)
        self._v = {key: copy.deepcopy(migrated.get(key)) for key in _VALUE_KEYS}
        self._settle()

        # A stored acceptance counts only when it is the current revision; a stale one is asked again.
        stored = store.get_flag(STARNET_FLAG) if store is not None else None
        current = starnet_licence.STARNET_LICENCE_REV
        self._starnet_revision: Optional[str] = next(
            (v for v in (stored, source.get(STARNET_FLAG)) if v == current), None)

    @classmethod
    def from_config(cls, config: Mapping[str, Any], found_tools: Optional[FoundMap] = None, **kwargs: Any) -> "DialogModel":
        """The dialog for a loaded options dict (see :meth:`__init__` for the keyword arguments).

        Migrates old tool names the way config.migrate_config does; a saved tool that is not available shows as
        none and is named in :meth:`take_notices`.
        """
        return cls(config, found_tools, **kwargs)

    # ---- values -----------------------------------------------------------------------------------------------

    def get(self, name: str) -> Any:
        """The current value of option ``name`` (Loom's name)."""
        if name not in self._v:
            raise KeyError(f"not a dialog option: {name}")
        return self._v[name]

    def set_field(self, name: str, value: Any) -> None:
        """Sets option ``name``. A tool choice must be none or one the dialog offers for its group."""
        if name not in self._v:
            raise KeyError(f"not a dialog option: {name}")
        group = next((g for g, key in GROUP_KEYS.items() if key == name), None)
        if group is not None and value != NONE and value not in self._choice_values(group):
            raise ValueError(f"{value} is not offered here")
        if name == "stretchMethod" and value not in STRETCH_METHOD_LABELS:
            raise ValueError(f"unknown stretch method: {value}")
        self._v[name] = value
        self._settle()
        self._invalidate()

    def path(self, channel: str) -> str:
        """The file chosen for ``channel`` ("" when none)."""
        return str(self._paths.get(channel) or "")

    def set_path(self, channel: str, path: str) -> None:
        """Chooses the file for ``channel``; an empty path clears it."""
        if channel not in ch.CHANNELS:
            raise KeyError(f"not a channel: {channel}")
        self._paths[channel] = str(path or "")
        self._invalidate()

    def filter_name(self, key: str) -> str:
        """The SPCC filter chosen for R, G or B ("" when none)."""
        return str(self._filters.get(key) or "")

    def set_filter(self, key: str, name: str) -> None:
        """Chooses the SPCC filter for R, G or B; an empty name clears it."""
        if key not in _FILTER_KINDS:
            raise KeyError(f"not a filter key: {key}")
        if name:
            self._filters[key] = str(name)
        else:
            self._filters.pop(key, None)
        self._invalidate()

    def filter_choices(self, key: str) -> list[str]:
        """The filter names Siril's SPCC folder holds for R, G or B (empty without a lookup)."""
        if key not in _FILTER_KINDS:
            raise KeyError(f"not a filter key: {key}")
        return self._choices(_FILTER_KINDS[key])

    def _choices(self, kind: str) -> list[str]:
        """The names of one SPCC kind, read from Siril's database once (the folder holds hundreds of files)."""
        if self._spcc_names is None:
            return []
        if kind not in self._spcc_cache:
            self._spcc_cache[kind] = list(self._spcc_names(kind))
        return list(self._spcc_cache[kind])

    def sensor_choices(self) -> list[str]:
        """The camera sensors Siril's SPCC folder holds (empty without a lookup)."""
        return self._choices(SENSOR_KIND)

    def sensor(self) -> str:
        """The sensor chosen; "" is auto (from the camera's INSTRUME keyword)."""
        return str(self._v["spccSensor"] or "")

    def set_sensor(self, name: str) -> None:
        """Chooses the SPCC sensor; "" is auto. A name Siril's folder does not hold is refused."""
        if name and self._spcc_names is not None and name not in self.sensor_choices():
            raise ValueError(f"{name} is not in Siril's SPCC database")
        self._v["spccSensor"] = str(name or "")
        self._invalidate()

    def auto_sensor(self) -> str:
        """The sensor auto selects: from the INSTRUME the scanned masters agree on (channels.spcc_sensor_for)."""
        instruments = [e.get("instrume") for e in (self._scan or {}).get("confirmed", {}).values()]
        return ch.spcc_sensor_for(ch.common_instrument(instruments))

    def effective_sensor(self) -> str:
        """The sensor a run uses: the chosen one, else the automatic one."""
        return self.sensor() or self.auto_sensor()

    def set_smoothing(self, value: Any) -> None:
        """Sets GraXpert's smoothing; Loom's range is 0 to 1."""
        self._v["smoothing"] = _in_range(value, SMOOTHING_RANGE, "Smoothing")
        self._invalidate()

    def set_bandwidth(self, value: Any) -> None:
        """Sets the narrowband bandwidth in nanometres; Loom's range is 0.5 to 30."""
        self._v["narrowbandBandwidth"] = _in_range(value, BANDWIDTH_RANGE, "Narrowband bandwidth")
        self._invalidate()

    def palette_selected(self, palette: str) -> bool:
        return palette in self._palettes

    def set_palette(self, palette: str, on: bool) -> None:
        """Ticks or unticks a palette; only palettes the dialog offers are kept."""
        if palette not in ch.palette_names():
            raise ValueError(f"not a palette Loom knows: {palette}")
        if on and palette not in self._palettes:
            self._palettes.append(palette)
        elif not on and palette in self._palettes:
            self._palettes.remove(palette)
        self._invalidate()

    def output_dir(self) -> str:
        """The output folder for this run: the saved one when the dialog opens, "" when none was ever chosen."""
        return self._output_dir

    def set_output_dir(self, folder: str) -> None:
        self._output_dir = str(folder or "")
        self._invalidate()

    def to_config(self) -> dict[str, Any]:
        """The options as Loom's config keys, ready for config.save, the output folder included."""
        out = copy.deepcopy(self._base)
        for key in _NOT_IN_CONFIG:
            out.pop(key, None)
        for key in _VALUE_KEYS:
            out[key] = copy.deepcopy(self._v[key])
        out["paths"] = dict(self._paths)
        out["outputDir"] = self._output_dir
        out["filters"] = dict(self._filters)
        out["palettes"] = list(self._palettes)
        return out

    def take_notices(self) -> list[str]:
        """The notices raised since the last call (a saved tool that is gone, a migrated option). Shown once."""
        out, self._notices = self._notices, []
        return out

    # ---- tools ------------------------------------------------------------------------------------------------

    def _has(self, tool_id: str) -> bool:
        return self._found.get(tool_id) is not None

    def _flags(self, group: str) -> dict[str, Any]:
        if group == GROUP_NOISE:
            return {"nxt": self._has("rcastro"), "prism": self._has("prism_cli"), "studio": self._has("syqon-cli"),
                    "prism2Unavailable": self._prism2_unavailable}
        if group == GROUP_SHARPEN:
            return {"bxt": self._has("rcastro"), "parallax": self._has("parallax_cli"),
                    "studio": self._has("syqon-cli")}
        if group == GROUP_STAR:
            return {"starnet": self._has("starnet2"), "sxt": self._has("rcastro"),
                    "starless": self._has("SyQonStarless"), "studio": self._has("syqon-cli")}
        return {}

    def _choice_values(self, group: str) -> list[str]:
        """The tool choices a group offers, without none, in Loom's order (built-ins last)."""
        if group == GROUP_NOISE:
            return lv.noise_tools_from(self._flags(group))
        if group == GROUP_SHARPEN:
            return lv.sharpen_tools_from(self._flags(group))
        if group == GROUP_STAR:
            return lv.star_tools_from(self._flags(group))
        if group == GROUP_GRADIENT:
            out: list[str] = []
            if self._has("graxpert-ai"):
                out.append(lv.GRADIENT_TOOL_GRAXPERT)
            if self._has("syqon-cli"):
                out.append(lv.GRADIENT_TOOL_STUDIO)
            return out
        raise KeyError(f"not a tool group: {group}")

    def visible_groups(self) -> list[str]:
        """The groups with at least one tool to offer; the others are hidden. Noise always has Siril's denoise."""
        return [g for g in GROUPS if self._choice_values(g)]

    def choices(self, group: str) -> list[tuple[str, str]]:
        """The (value, label) pairs of a group's drop-down: none first, then the offered tools."""
        none_label = "Siril background extraction only" if group == GROUP_GRADIENT else "None"
        return [(NONE, none_label)] + [(value, value) for value in self._choice_values(group)]

    def noise_levels(self) -> list[str]:
        """The strengths the chosen noise tool offers, with the current stretch setting."""
        return lv.noise_levels_for(self._v["noiseTool"], self._v["stretch"])

    def noise_level_label(self, level: str) -> str:
        """What a strength is called on screen (Prism 2.0's Medium and High are Ultra and Max)."""
        return _capitalised(str(lv.noise_level_name(self._v["noiseTool"], level)))

    def tool_rows(self) -> list[ToolRow]:
        """Every tool with its found path, or "not found" and the reason the last candidate was refused."""
        rows: list[ToolRow] = []
        for tool_id, label in TOOL_LABELS.items():
            found = self._found.get(tool_id)
            if found is not None:
                rows.append(ToolRow(tool_id, label, found.path, found.path))
                continue
            reason = self._resolver.rejection(tool_id) if self._resolver is not None else None
            status = "not found" if not reason else f"not found ({reason})"
            rows.append(ToolRow(tool_id, label, None, status))
        return rows

    def locate(self, tool_id: str, path: str) -> tuple[bool, str]:
        """Probes the file at ``path`` as ``tool_id``. On success it is used and remembered; returns (ok, message)."""
        if tool_id not in TOOL_LABELS:
            raise KeyError(f"not a tool: {tool_id}")
        label = TOOL_LABELS[tool_id]
        path = str(path or "").strip()
        if not path:
            return False, "Choose the program's file."
        if self._resolver is None:
            return False, "This dialog cannot probe tools."
        found = self._resolver.resolve(tool_id, configured=path)
        if found is None or found.source != "configured":
            reason = self._resolver.rejection(tool_id) or "it did not pass the program's check"
            return False, f"{path} is not {label}: {reason}"
        self._found[tool_id] = found
        self._remember(found)
        self._settle()
        self._invalidate()
        return True, f"{label} found at {found.path}"

    def rescan(self) -> list[str]:
        """Looks for every tool again; returns the tool ids found now. A tool that vanished is named in the notices."""
        self._spcc_cache.clear()
        if self._resolver is None:
            return [t for t in TOOL_LABELS if self._has(t)]
        for tool_id in TOOL_LABELS:
            self._found[tool_id] = self._resolver.resolve(tool_id)
        self._settle()
        self._invalidate()
        return [t for t in TOOL_LABELS if self._has(t)]

    def _remember(self, found: Any) -> None:
        if self._store is None:
            return
        try:
            st = os.stat(found.path)
        except OSError:
            return
        self._store.remember(found, st.st_size, st.st_mtime_ns, probed=self._now())

    def _settle(self) -> None:
        """Brings every value into what the dialog offers. A tool that is gone becomes none and is named in a notice."""
        for group, key in GROUP_KEYS.items():
            value = self._v[key]
            if value != NONE and value not in self._choice_values(group):
                self._notices.append(f"{value} is no longer available; set to none")
                self._v[key] = NONE
        if not self._choice_values(GROUP_SHARPEN):
            self._v["sharpenTool"] = NONE
            self._v["starReduction"] = NONE
            self._v["detailLevel"] = NONE
        for key in ("starReduction", "detailLevel"):
            if self._v[key] not in SHARPEN_LEVELS:
                self._notices.append(f"{self._v[key]} is not available; set to none")
                self._v[key] = NONE
        if self._v["stretchMethod"] not in STRETCH_METHOD_LABELS:
            self._notices.append(f"{self._v['stretchMethod']} is not available; set to histogram (MTF)")
            self._v["stretchMethod"] = lv.STRETCH_METHOD_MTF
        if self._v["parallaxFamily"] not in lv.STUDIO_PARALLAX_FAMILIES:
            self._v["parallaxFamily"] = lv.STUDIO_PARALLAX_FAMILY
        if not self._v["useCache"]:
            self._v["ignoreCache"] = False
        if self._spcc_names is not None:
            sensors = self.sensor_choices()
            if self._v["spccSensor"] and sensors and self._v["spccSensor"] not in sensors:
                self._notices.append(f"{self._v['spccSensor']} is not in Siril's SPCC database; set to auto")
                self._v["spccSensor"] = ""
            for key in [k for k in self._filters if k in _FILTER_KINDS]:
                choices = self.filter_choices(key)
                # An empty list means the database could not be read: keep what was saved rather than lose it.
                if choices and self._filters[key] not in choices:
                    self._notices.append(f"{self._filters[key]} is not in Siril's SPCC database; filter {key} cleared")
                    del self._filters[key]
        stretch = self._v["stretch"]
        tool = self._v["noiseTool"]
        self._v["noiseLevel"] = lv.supported_noise_level(tool, self._v["noiseLevel"], stretch)
        if self._v["noiseLevelL"]:
            self._v["noiseLevelL"] = lv.supported_noise_level(tool, self._v["noiseLevelL"], stretch)

    # ---- enabled state and visibility -------------------------------------------------------------------------

    def enabled(self, name: str) -> bool:
        """Whether the control for option ``name`` is live (a disabled control keeps its value)."""
        if name in _STRETCH_CONTROLS:
            return bool(self._v["stretch"])
        if name in ("noiseLevel", "noiseLevelL"):
            return self._v["noiseTool"] != NONE
        if name in ("starReduction", "detailLevel"):
            return self._v["sharpenTool"] != NONE
        if name == "parallaxFamily":
            return self._v["sharpenTool"] == lv.SHARPEN_TOOL_STUDIO
        if name in ("cacheDir", "ignoreCache"):
            return bool(self._v["useCache"])
        return True

    def palettes_visible(self) -> bool:
        """The palette checkboxes show only when a narrowband channel (H, S or O) has a file."""
        return any(self.path(k) for k in ch.NARROWBAND)

    def palette_choices(self) -> list[str]:
        return ch.palette_names()

    # ---- StarNet2 licence --------------------------------------------------------------------------------------

    def starnet_needed(self) -> bool:
        return self._v["starTool"] == lv.STAR_TOOL_STARNET

    def starnet_accepted(self) -> bool:
        """True when the accepted licence revision is the current one."""
        return self._starnet_revision == starnet_licence.STARNET_LICENCE_REV

    def starnet_licence_text(self) -> Optional[str]:
        """The licence file that ships next to the StarNet2 program, or None when it is not there."""
        found = self._found.get("starnet2")
        return starnet_licence.licence_text(found.path) if found is not None else None

    def accept_starnet(self, revision: str) -> None:
        """Stores the acceptance of the current licence revision (kept in the ToolStore, so it is asked again only
        when the revision changes)."""
        if revision != starnet_licence.STARNET_LICENCE_REV:
            raise ValueError(f"the StarNet2 licence revision is {starnet_licence.STARNET_LICENCE_REV}, not {revision}")
        self._starnet_revision = revision
        if self._store is not None:
            self._store.set_flag(STARNET_FLAG, revision)
        self._invalidate()

    # ---- cache -------------------------------------------------------------------------------------------------

    def cache_folder(self) -> str:
        """The cache folder in use: the chosen one, else the per-user default."""
        chosen = str(self._v["cacheDir"] or "")
        if chosen:
            return chosen
        env = self._env if self._env is not None else os.environ
        home = self._home if self._home is not None else os.path.expanduser("~")
        return default_cache_dir(self._platform or sys.platform, env, home)

    def cache_bytes(self) -> int:
        return CacheStore(self.cache_folder()).total_bytes()

    def clear_cache(self) -> int:
        """Removes the cache entries; returns how many were removed."""
        return CacheStore(self.cache_folder()).clear()

    # ---- masters -----------------------------------------------------------------------------------------------

    def scan_masters(self, folder: str, scanner: Optional[Callable[[str], Any]] = None) -> bool:
        """Reads a master folder and uses what it found; False when it is not a folder (nothing changes then).

        Every channel the scan confirmed gets its file; a channel the folder has no master for is cleared, so the
        selection is the folder's masters and nothing left over from before.
        """
        result = (scanner or ch.scan_masters)(folder)
        if result is None:
            return False
        self._scan = result
        confirmed = result.get("confirmed", {})
        for channel in ch.CHANNELS:
            entry = confirmed.get(channel)
            self._paths[channel] = entry["path"] if entry is not None else ""
        self._invalidate()
        return True

    def restore_masters(self, scanner: Optional[Callable[[str], Any]] = None) -> bool:
        """Rebuilds the master list from the saved paths when the dialog opens, keeping those paths as they are.

        Each saved file that still exists gets a row: the entry the scan of its folder found for it when there is one
        (filter, size, drizzle), else just its name and date. False when no saved master exists.
        """
        if self._scan:
            return False
        found: dict[str, dict[str, Any]] = {}
        for folder in {os.path.dirname(str(p)) for p in self._paths.values() if p}:
            result = (scanner or ch.scan_masters)(folder) or {}
            for entry in result.get("confirmed", {}).values():
                found[entry["path"]] = entry
        confirmed: dict[str, dict[str, Any]] = {}
        for channel in ch.CHANNELS:
            path = str(self._paths.get(channel) or "")
            if not path or not os.path.isfile(path):
                continue
            confirmed[channel] = found.get(path) or {"path": path, "name": os.path.basename(path),
                                                     "mtime": os.path.getmtime(path), "filter": channel}
        if not confirmed:
            return False
        self._scan = {"confirmed": confirmed, "corrected": [], "skipped": [], "total": len(confirmed)}
        return True

    def scanned(self) -> Optional[dict[str, Any]]:
        return self._scan

    def unmeasured_masters(self) -> list[str]:
        """The scanned masters with no measurement yet (empty without a ``measure`` function)."""
        if self._measure is None or not self._scan:
            return []
        return [e["path"] for c in ch.CHANNELS
                if (e := self._scan.get("confirmed", {}).get(c)) is not None and e["path"] not in self._quality]

    def measure_masters(self, paths: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Measures ``paths`` (slow: call it off the Tk thread). A master that cannot be measured is left out."""
        if self._measure is None or not paths:
            return {}
        return {p: dict(q) for p, q in self._measure(list(paths), self.cache_folder()).items()}

    def set_measuring(self, on: bool) -> None:
        self._measuring = bool(on)

    def add_quality(self, quality: Mapping[str, Mapping[str, Any]]) -> None:
        self._quality.update({p: dict(q) for p, q in quality.items()})

    def master_rows(self, quality: Optional[Mapping[str, Mapping[str, Any]]] = None) -> list[dict[str, str]]:
        """The master list rows, one per confirmed channel, with Loom's columns.

        ``quality`` maps a master's path to its measurements (``fwhm``, ``eccentricity``, ``noise``, ``stars``, as
        stages_correct.master_quality gives them); a missing measurement is an empty cell.
        """
        if not self._scan:
            return []
        rows = []
        for channel in ch.CHANNELS:
            entry = self._scan.get("confirmed", {}).get(channel)
            if entry is None:
                continue
            q = (quality if quality is not None else self._quality).get(entry["path"], {})
            pending = "..." if self._measuring and not q else ""
            size = "" if not entry.get("width") or not entry.get("height") else f"{entry['width']} x {entry['height']}"
            created = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.get("mtime") or 0))
            rows.append({
                "channel": channel,
                "path": entry["path"],
                "Filter": str(entry.get("filter") or ""),
                "Size": size,
                "Drizzle": str(entry.get("drizzle") or ""),
                "Source": entry["name"],
                "Created": created,
                "FWHM": _fmt(q.get("fwhm"), "{:.2f}") or pending,
                "Ecc": _fmt(q.get("eccentricity"), "{:.2f}") or pending,
                "Noise": _fmt(q.get("noise"), "{:.4g}") or pending,
                "Stars": _fmt(q.get("stars"), "{:.0f}") or pending,
            })
        return rows

    def project_name_hint(self) -> str:
        """The project name guessed from the first source file (used when the name is left empty)."""
        return ch.project_name_for(self.to_config())

    # ---- run ---------------------------------------------------------------------------------------------------

    def _invalidate(self) -> None:
        self._problems_key = None

    def _request(self) -> RunRequest:
        return RunRequest(self.to_config(), self._output_dir, self._starnet_revision)

    def run_reasons(self) -> list[str]:
        """Why Run is disabled, every reason at once: no output folder, StarNet2 licence, preflight problems."""
        reasons: list[str] = []
        if not self._output_dir:
            reasons.append("Choose an output folder first.")
        if self.starnet_needed() and not self.starnet_accepted():
            reasons.append("StarNet2 needs its licence accepted first.")
        reasons.extend(self._preflight_problems())
        return reasons

    def _preflight_key(self) -> str:
        request = self._request()
        return json.dumps([request.config, request.output_dir, request.starnet_revision], sort_keys=True, default=str)

    def _preflight_problems(self) -> list[str]:
        if self._preflight is None:
            return []
        key = self._preflight_key()
        if key != self._problems_key:
            if self.async_preflight:
                return [CHECKING_TEXT]   # the window computes it off the UI thread (preflight_job) and stores it
            self._problems_cache = list(self._preflight(self._request()))
            self._problems_key = key
        return list(self._problems_cache)

    def preflight_stale(self) -> bool:
        """True when the window must start a background preflight: asynchronous, with no answer for the current options."""
        return self._preflight is not None and self.async_preflight and self._preflight_key() != self._problems_key

    def preflight_job(self) -> tuple[str, Callable[[], list[str]]]:
        """The key of the current options and a function that computes their preflight problems (slow: run it off the
        UI thread, then pass both to :meth:`store_preflight`)."""
        request = self._request()
        preflight = self._preflight
        return self._preflight_key(), (lambda: list(preflight(request)) if preflight is not None else [])

    def store_preflight(self, key: str, problems: Sequence[str]) -> None:
        self._problems_key = key
        self._problems_cache = list(problems)

    def can_run(self) -> bool:
        return not self.run_reasons()

    def run_request(self) -> RunRequest:
        """The config, output folder and StarNet revision for the orchestrator. Raises RunBlocked with every reason."""
        reasons = self.run_reasons()
        if reasons:
            raise RunBlocked(reasons)
        return self._request()

    def needs_replace_confirmation(self, image_loaded: bool) -> bool:
        """True when the run must first ask the user to confirm replacing the image loaded in Siril."""
        return bool(image_loaded)


def _in_range(value: Any, bounds: tuple[float, float], what: str) -> float:
    """``value`` as a number within ``bounds``; ValueError with the message to show otherwise."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be a number.") from exc
    low, high = bounds
    if not (low <= number <= high) or number != number:
        raise ValueError(f"{what} must be between {low:g} and {high:g}.")
    return number


def _fmt(value: Any, pattern: str) -> str:
    if value is None:
        return ""
    try:
        return pattern.format(value)
    except (TypeError, ValueError):
        return ""


class ProgressModel:
    """The progress window's state: the stage text, the percent, and the cancel decision.

    The pipeline thread puts ``(kind, value)`` tuples on ``messages``: ``("stage", text)``, ``("percent", number)``
    or ``("done", text)``. :meth:`pump` drains them on the window's thread. Cancelling sets ``cancel_event``, which
    the pipeline polls at its checkpoints, only after the user answers Yes.
    """

    STAGE = "stage"
    PERCENT = "percent"
    DONE = "done"

    STARTING = "starting..."
    CANCELLING = "cancelling at the next checkpoint..."
    CANCEL_TITLE = "Loom - cancel the run?"
    CANCEL_QUESTION = "Stop this run at the next checkpoint?"
    CANCEL_DETAIL = "Stages already finished stay in the cache and will be reused, but the step now running is lost."

    def __init__(self, messages: "queue.Queue[Any]", cancel_event: Any) -> None:
        self._queue = messages
        self._event = cancel_event
        self.stage: str = self.STARTING
        self.percent: float = 0.0
        self.done: bool = False
        self.done_text: str = ""
        self.cancel_requested: bool = False

    def pump(self) -> int:
        """Applies every queued message; returns how many were applied. Never blocks."""
        applied = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return applied
            applied += 1
            try:
                kind, value = item
            except (TypeError, ValueError):
                continue
            if kind == self.STAGE:
                self.stage = str(value)
            elif kind == self.PERCENT:
                try:
                    self.percent = min(100.0, max(0.0, float(value)))
                except (TypeError, ValueError):
                    pass
            elif kind == self.DONE:
                self.done = True
                self.done_text = str(value)

    def label(self) -> str:
        """The stage line with the percent, as the window shows it."""
        if self.done:
            return self.done_text or "finished"
        return f"{self.stage}  {round(self.percent)}%"

    def confirm_cancel(self, answer_yes: bool) -> bool:
        """Applies the user's answer to "Stop this run at the next checkpoint?" (default No). Returns True when set."""
        if not answer_yes or self.cancel_requested:
            return self.cancel_requested
        self.cancel_requested = True
        self.stage = self.CANCELLING
        self._event.set()
        return True


def ask_folder(title: str, initial: str = "", parent: Any = None) -> Optional[str]:
    """A folder chooser (tkinter); None when cancelled."""
    from tkinter import filedialog

    chosen = filedialog.askdirectory(title=title, initialdir=initial or None, parent=parent)
    return chosen or None


def show_dialog(model: DialogModel, siril: Any = None, image_loaded: bool = False) -> Optional[RunRequest]:
    """Shows the options window until Run or Cancel. Returns the RunRequest for Run, None for Cancel or closing.

    ``image_loaded`` says whether Siril has an image open: Run then asks before the image is replaced.
    """
    from loomsiril.tk_theme import close_root, themed_root

    root = themed_root(siril, log=getattr(siril, "log", None))
    try:
        dlg = LoomDialog(root, model, image_loaded)
        root.wait_window(dlg.top)
        return dlg.result
    finally:
        close_root(root)


class LoomDialog:
    """The options window. Binds each control to the model; ``result`` is the RunRequest, or None when cancelled.

    Built only where tkinter can open a window. A change calls the model, then :meth:`_sync` re-reads every control
    from the model, so the window never shows a value the model does not hold.
    """

    def __init__(self, master: Any, model: DialogModel, image_loaded: bool = False) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.model = model
        self._image_loaded = bool(image_loaded)
        self._closed = False
        self._save_timer: Optional[str] = None
        self._busy = 0
        self._checking = False
        self.result: Optional[RunRequest] = None
        self.top = tk.Toplevel(master)
        self.top.title("Loom")
        # The options scroll; the Run/Cancel bar below them always stays in view (the form is taller than a laptop
        # screen).
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(0, weight=1)
        self._scroll = _ScrollArea(self.top)
        self._scroll.outer.grid(row=0, column=0, sticky="nsew")
        self._frame = self._scroll.inner
        footer = ttk.Frame(self.top, padding=(10, 4, 10, 10))
        footer.grid(row=1, column=0, sticky="we")
        footer.columnconfigure(0, weight=1)
        self._row = 0
        self._texts: dict[str, tuple[Any, Callable[[], str]]] = {}
        self._checks: dict[str, tuple[Any, Any]] = {}
        self._combos: dict[str, tuple[Any, Callable[[], list[tuple[str, str]]], Callable[[], Any]]] = {}
        self._palettes: dict[str, Any] = {}
        self._invalid: dict[str, str] = {}
        self._rendered: dict[str, str] = {}
        self._shown: list[str] = model.take_notices()
        self._measure_thread: Any = None
        self._notice = tk.StringVar(value="\n".join(self._shown))
        ttk.Label(self._frame, textvariable=self._notice, foreground="#b35c00", wraplength=560).grid(
            row=self._next(), column=0, columnspan=3, sticky="w")

        self._section("Input files")
        # The masters list, the scan button right under it, then the master paths the scan fills.
        self._masters = ttk.Treeview(self._frame, columns=MASTER_COLUMNS, show="headings", height=7)
        for column in MASTER_COLUMNS:
            self._masters.heading(column, text=column)
            self._masters.column(column, width=80, stretch=True)
        self._masters.grid(row=self._next(), column=0, columnspan=3, sticky="we")
        bar = ttk.Frame(self._frame)
        bar.grid(row=self._next(), column=0, columnspan=3, sticky="w", pady=2)
        ttk.Button(bar, text="Scan Masters Folder...", command=self._scan).grid(row=0, column=0, padx=2)
        for channel in ch.CHANNELS:
            self._text(f"path:{channel}", f"{channel} master", lambda c=channel: model.path(c),
                       lambda value, c=channel: model.set_path(c, value), file_for=channel)

        self._section("Photometric calibration (SPCC)")
        for key in ("R", "G", "B"):
            self._combo(f"filter:{key}", f"{key} filter",
                        lambda k=key: [("", "(none)")] + [(n, n) for n in model.filter_choices(k)],
                        get=lambda k=key: model.filter_name(k),
                        put=lambda v, k=key: model.set_filter(k, v))
        self._combo("spccSensor", "Sensor",
                    lambda: [("", f"Auto ({model.auto_sensor()})")] + [(n, n) for n in model.sensor_choices()],
                    get=model.sensor, put=model.set_sensor)

        self._section("Processing")
        self._combo("gradientTool", "Gradient removal", lambda: model.choices(GROUP_GRADIENT))
        self._check("graxpertNarrowband", "Narrowband with GraXpert")   # beside the gradient tool it qualifies
        self._combo("sharpenTool", "Sharpening", lambda: model.choices(GROUP_SHARPEN))
        self._combo("starReduction", "Star reduction", lambda: _level_pairs(SHARPEN_LEVELS))
        self._combo("detailLevel", "Detail", lambda: _level_pairs(SHARPEN_LEVELS))
        self._combo("parallaxFamily", "Parallax family", lambda: [(f, _capitalised(f)) for f in lv.STUDIO_PARALLAX_FAMILIES])
        self._combo("starTool", "Star extraction", lambda: model.choices(GROUP_STAR))
        self._combo("noiseTool", "Noise reduction", lambda: model.choices(GROUP_NOISE))
        self._combo("noiseLevel", "Noise strength", lambda: [(v, model.noise_level_label(v)) for v in model.noise_levels()])
        self._combo("noiseLevelL", "Noise strength for L", lambda: [("", "Follow colour level")] +
                    [(v, model.noise_level_label(v)) for v in model.noise_levels()])
        self._text("smoothing", "GraXpert smoothing (0 to 1)", lambda: str(model.get("smoothing")), model.set_smoothing)
        self._text("narrowbandBandwidth", "Narrowband bandwidth (0.5 to 30 nm)",
                   lambda: str(model.get("narrowbandBandwidth")), model.set_bandwidth)
        self._check("reduceHalos", "Reduce halos")
        self._check("separateLStars", "Separate L stars")
        self._check("stretch", "Stretch")
        self._combo("stretchMethod", "Stretch method", lambda: list(STRETCH_METHOD_LABELS.items()))
        self._check("keepLinear", "Keep linear")
        self._check("exportPsb", "Export PSB")
        self._text("exportDir", "Export folder", lambda: str(model.get("exportDir") or ""),
                   lambda value: model.set_field("exportDir", value), folder=True)
        self._text("projectName", "Project name (blank: from the file name)", lambda: str(model.get("projectName") or ""),
                   lambda value: model.set_field("projectName", value))
        # Narrowband: its own section, shown only when an H, S or O master is chosen (palettes_visible).
        self._palette_frame = ttk.Frame(self._frame)
        self._palette_frame.grid(row=self._next(), column=0, columnspan=3, sticky="w")
        ttk.Label(self._palette_frame, text="Narrowband", font=("TkDefaultFont", 10, "bold")).pack(
            anchor="w", pady=(8, 2))
        self._check("narrowbandNormalize", "Normalise narrowband", parent=self._palette_frame)
        ttk.Label(self._palette_frame, text="Palettes").pack(anchor="w", padx=4, pady=(4, 0))
        for pal in model.palette_choices():
            var = tk.BooleanVar(value=False)
            self._palettes[pal] = var
            ttk.Checkbutton(self._palette_frame, text=pal, variable=var,
                            command=lambda p=pal, v=var: self._palette(p, bool(v.get()))).pack(anchor="w")

        self._section("Tools")
        self._tools = ttk.Frame(self._frame)
        self._tools.grid(row=self._next(), column=0, columnspan=3, sticky="we")

        self._section("StarNet2 licence")
        self._licence = ttk.Frame(self._frame)
        self._licence.grid(row=self._next(), column=0, columnspan=3, sticky="we")

        self._section("Cache")
        self._cache_bar = ttk.Frame(self._frame)
        self._cache_bar.grid(row=self._next(), column=0, columnspan=3, sticky="we")
        self._check("useCache", "Use the cache")
        self._check("ignoreCache", "Ignore the cache for this run")
        self._check("validateOnly", "Validate only (check everything, run nothing)")
        self._text("cacheDir", "Cache folder (blank: default)", lambda: str(model.get("cacheDir") or ""),
                   lambda value: model.set_field("cacheDir", value), folder=True)
        self._cache_label = tk.StringVar(value="")
        self._show_cache()
        ttk.Label(self._cache_bar, textvariable=self._cache_label).grid(row=0, column=0, sticky="w")
        ttk.Button(self._cache_bar, text="Clear cache", command=self._clear_cache).grid(row=0, column=1, padx=6)

        self._section("Output")   # last: the folder is chosen after the options, just before Run
        self._text("output", "Output folder", model.output_dir, model.set_output_dir, folder=True)

        self._error = tk.StringVar(value="")
        ttk.Label(self._frame, textvariable=self._error, foreground="#b35c00", wraplength=560).grid(
            row=self._next(), column=0, columnspan=3, sticky="w")
        self._reasons = tk.StringVar(value="")   # why Run is disabled: shown next to it, in the footer
        ttk.Label(footer, textvariable=self._reasons, foreground="#b35c00", wraplength=560).grid(
            row=0, column=0, sticky="w", pady=4)
        buttons = ttk.Frame(footer)
        buttons.grid(row=0, column=1, sticky="e", pady=6)
        ttk.Button(buttons, text="Cancel", command=self._cancel).grid(row=0, column=0, padx=4)
        self._run_button = ttk.Button(buttons, text="Run", command=self._run)
        self._run_button.grid(row=0, column=1, padx=4)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.model.async_preflight = True
        self._sync()
        self._scroll.fit()
        self._background(self.model.restore_masters, lambda _result: (self._sync(), self._measure_masters()))

    # -- building blocks --------------------------------------------------------------------------------------

    def _next(self) -> int:
        self._row += 1
        return self._row

    def _section(self, title: str) -> None:
        from tkinter import ttk

        ttk.Label(self._frame, text=title, font=("TkDefaultFont", 10, "bold")).grid(
            row=self._next(), column=0, columnspan=3, sticky="w", pady=(8, 2))

    def _text(self, key: str, label: str, read: Callable[[], str], write: Callable[[str], None],
              folder: bool = False, file_for: Optional[str] = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        row = self._next()
        ttk.Label(self._frame, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        var = tk.StringVar(value=read())
        ttk.Entry(self._frame, textvariable=var, width=46).grid(row=row, column=1, sticky="we", padx=4, pady=2)
        var.trace_add("write", lambda *_: self._edited(key, write, var))
        self._texts[key] = (var, read)
        self._rendered[key] = read()
        if folder or file_for is not None:
            command = (lambda: self._pick_folder(write, label)) if folder else (lambda: self._pick_file(file_for))
            ttk.Button(self._frame, text="Browse...", command=command).grid(row=row, column=2, padx=4)

    def _check(self, name: str, label: str, parent: Any = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        var = tk.BooleanVar(value=bool(self.model.get(name)))
        box = ttk.Checkbutton(parent if parent is not None else self._frame, text=label, variable=var,
                              command=lambda: self._set(name, bool(var.get())))
        if parent is not None:   # a sub-frame lays its children out with pack
            box.pack(anchor="w", padx=4)
        else:
            box.grid(row=self._next(), column=0, columnspan=3, sticky="w", padx=4)
        self._checks[name] = (var, box)

    def _combo(self, name: str, label: str, pairs: Callable[[], list[tuple[str, str]]],
               get: Optional[Callable[[], Any]] = None, put: Optional[Callable[[Any], None]] = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        row = self._next()
        ttk.Label(self._frame, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        var = tk.StringVar(value="")
        combo = ttk.Combobox(self._frame, textvariable=var, state="readonly", width=44)
        combo.grid(row=row, column=1, sticky="we", padx=4, pady=2)
        current = get if get is not None else (lambda: self.model.get(name))
        setter = put if put is not None else (lambda value: self.model.set_field(name, value))
        self._combos[name] = (combo, pairs, current)

        def picked(_event: Any = None) -> None:
            values = [value for value, text in pairs() if text == combo.get()]
            if values:
                self._apply(lambda: setter(values[0]))

        combo.bind("<<ComboboxSelected>>", picked)

    # -- actions ----------------------------------------------------------------------------------------------

    def _set(self, name: str, value: Any) -> None:
        self._apply(lambda: self.model.set_field(name, value))

    def _apply(self, action: Callable[[], None]) -> None:
        from tkinter import messagebox

        try:
            action()
        except ValueError as exc:
            messagebox.showerror("Loom", str(exc), parent=self.top)
        self._sync()

    def _edited(self, key: str, write: Callable[[str], None], var: Any) -> None:
        """A typed or pasted value: written to the model at once (so Run and the reasons follow), or held if invalid."""
        try:
            write(var.get())
        except ValueError as exc:
            self._invalid[key] = str(exc)
        else:
            self._invalid.pop(key, None)
            # what the entry shows now is what the model holds: nothing to rewrite while the user types
            self._rendered[key] = self._texts[key][1]()
        self._sync()

    def _pick_folder(self, write: Callable[[str], None], title: str) -> None:
        chosen = ask_folder(title, parent=self.top)
        if chosen:
            self._apply(lambda: write(chosen))

    def _pick_file(self, channel: str) -> None:
        from tkinter import filedialog

        chosen = filedialog.askopenfilename(parent=self.top, title=f"{channel} master")
        if chosen:
            self.model.set_path(channel, chosen)
            self._sync()

    def _background(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        """Runs ``work`` on a worker thread; ``done(result)`` runs on the UI thread when it returns (``done`` gets the
        exception instead of a result when ``work`` raised). The window stays responsive in between."""
        box: "queue.Queue[Any]" = queue.Queue()

        def target() -> None:
            try:
                box.put(work())
            except Exception as exc:  # handed to ``done`` on the UI thread
                box.put(exc)

        threading.Thread(target=target, name="loom-background", daemon=True).start()
        self._busy += 1

        def poll() -> None:
            if self._closed:
                return
            try:
                result = box.get_nowait()
            except queue.Empty:
                self.top.after(100, poll)
                return
            self._busy -= 1
            done(result)

        self.top.after(100, poll)

    def _schedule_save(self) -> None:
        """Saves the options a moment after the last change (off the UI thread), so nothing chosen is lost when the
        window is not closed by Run or Cancel."""
        if self.model.persist is None:
            return
        if self._save_timer is not None:
            self.top.after_cancel(self._save_timer)

        def fire() -> None:
            self._save_timer = None
            if self._closed:
                return
            options = self.model.to_config()
            save = self.model.persist
            threading.Thread(target=lambda: save(options) if save else None, name="loom-save", daemon=True).start()

        self._save_timer = self.top.after(1000, fire)

    def _start_preflight(self) -> None:
        """The preflight of the current options on a worker thread; when it returns the window is refreshed (and runs
        it again when the options changed meanwhile)."""
        if self._checking:
            return
        key, job = self.model.preflight_job()
        self._checking = True

        def done(result: Any) -> None:
            self._checking = False
            problems = [f"The input check failed: {result}"] if isinstance(result, Exception) else result
            self.model.store_preflight(key, problems)
            self._sync()

        self._background(job, done)

    def _scan(self) -> None:
        from tkinter import messagebox

        folder = ask_folder("Scan Masters Folder", parent=self.top)
        if not folder:
            return
        self._notice.set("Scanning the masters folder...")

        def done(result: Any) -> None:
            if isinstance(result, Exception) or not self.model.scan_masters(folder, scanner=lambda _f: result):
                messagebox.showerror("Loom", f"{folder} is not a folder.", parent=self.top)
            self._sync()
            self._measure_masters()

        self._background(lambda: ch.scan_masters(folder), done)

    def _measure_masters(self) -> None:
        """Measure the scanned masters on a worker thread; the table fills in when the results arrive."""
        import threading

        paths = self.model.unmeasured_masters()
        if not paths or self._measure_thread is not None:
            return
        box: "queue.Queue[Any]" = queue.Queue()

        def work() -> None:
            try:
                box.put(self.model.measure_masters(paths))
            except Exception as exc:  # the list is informative: a failed measurement must not break the dialog
                box.put(exc)

        self.model.set_measuring(True)
        self._measure_thread = threading.Thread(target=work, name="loom-measure", daemon=True)
        self._measure_thread.start()
        self._sync()
        self.top.after(250, lambda: self._collect_measures(box))

    def _collect_measures(self, box: "queue.Queue[Any]") -> None:
        try:
            result = box.get_nowait()
        except queue.Empty:
            self.top.after(250, lambda: self._collect_measures(box))
            return
        self._measure_thread = None
        self.model.set_measuring(False)
        if isinstance(result, Exception):
            self._shown.append(f"Could not measure the masters: {result}")
        else:
            self.model.add_quality(result)
        self._sync()

    def _palette(self, palette: str, on: bool) -> None:
        self.model.set_palette(palette, on)
        self._sync()

    def _locate(self, tool_id: str) -> None:
        from tkinter import filedialog, messagebox

        chosen = filedialog.askopenfilename(parent=self.top, title=f"Locate {TOOL_LABELS[tool_id]}")
        if not chosen:
            return
        self._notice.set("Checking the tool...")

        def done(result: Any) -> None:
            if isinstance(result, Exception):
                messagebox.showerror("Loom", str(result), parent=self.top)
            elif not result[0]:
                messagebox.showerror("Loom", result[1], parent=self.top)
            self._sync()

        self._background(lambda: self.model.locate(tool_id, chosen), done)

    def _rescan(self) -> None:
        self._notice.set("Looking for the tools...")
        self._background(self.model.rescan, lambda _result: self._sync())

    def _accept_starnet(self) -> None:
        self.model.accept_starnet(starnet_licence.STARNET_LICENCE_REV)
        self._sync()

    def _clear_cache(self) -> None:
        from tkinter import messagebox

        def done(result: Any) -> None:
            if isinstance(result, Exception):
                messagebox.showerror("Loom", f"The cache could not be cleared: {result}", parent=self.top)
            else:
                messagebox.showinfo("Loom", f"Removed {result} cached stage results.", parent=self.top)
            self._show_cache()

        self._cache_label.set("Clearing the cache...")
        self._background(self.model.clear_cache, done)

    def _run(self) -> None:
        from tkinter import messagebox

        try:
            request = self.model.run_request()
        except RunBlocked as exc:
            messagebox.showerror("Loom", "\n".join(exc.reasons), parent=self.top)
            self._sync()
            return
        if self.model.needs_replace_confirmation(self._image_loaded):
            if not messagebox.askokcancel("Loom", REPLACE_CONFIRM_TEXT, parent=self.top):
                return
        self.result = request
        self._closed = True
        self.top.destroy()

    def _cancel(self) -> None:
        self.result = None
        self._closed = True
        self.top.destroy()

    # -- re-reading the model ---------------------------------------------------------------------------------

    def _sync(self) -> None:
        model = self.model
        for key, (var, read) in self._texts.items():
            current = read()
            if current != self._rendered.get(key) and key not in self._invalid:
                # the model changed from outside (a browse, a rescan): show it; typed text is never rewritten
                # the entry's trace records the new text in _rendered (see _edited)
                var.set(current)
        for name, (var, box) in self._checks.items():
            var.set(bool(model.get(name)))
            box.state(["!disabled"] if model.enabled(name) else ["disabled"])
        for name, (combo, pairs, current_of) in self._combos.items():
            options = pairs()
            combo["values"] = [text for _value, text in options]
            current = current_of()
            combo.set(next((text for value, text in options if value == current), ""))
            combo.state(["!disabled"] if model.enabled(name) else ["disabled"])
        self._shown.extend(model.take_notices())
        self._notice.set("\n".join(self._shown))
        self._error.set("\n".join(self._invalid.values()))
        for pal, var in self._palettes.items():
            var.set(model.palette_selected(pal))
        if model.palettes_visible():
            self._palette_frame.grid()
        else:
            self._palette_frame.grid_remove()
        self._masters.delete(*self._masters.get_children())
        for index, row in enumerate(model.master_rows()):
            self._masters.insert("", "end", iid=str(index), values=[row[c] for c in MASTER_COLUMNS])
        self._render_tools()
        self._render_licence()
        reasons = model.run_reasons()
        if model.preflight_stale():
            self._start_preflight()
        self._schedule_save()
        self._reasons.set("\n".join(reasons))
        self._run_button.state(["disabled"] if reasons else ["!disabled"])

    def _show_cache(self) -> None:
        """The cache's size, measured on a worker thread (a big cache takes a while to walk)."""
        folder = self.model.cache_folder()
        self._cache_label.set(f"Cache: measuring {folder}...")

        def done(result: Any) -> None:
            if not isinstance(result, Exception) and not self._closed:
                self._cache_label.set(f"Cache: {_mib(result)} in {folder}")

        self._background(self.model.cache_bytes, done)

    def _render_tools(self) -> None:
        from tkinter import ttk

        for child in self._tools.winfo_children():
            child.destroy()
        rows = self.model.tool_rows()
        for row, tool in enumerate(rows):
            ttk.Label(self._tools, text=tool.label).grid(row=row, column=0, sticky="w", padx=4)
            ttk.Label(self._tools, text=tool.status, wraplength=300).grid(row=row, column=1, sticky="w", padx=4)
            ttk.Button(self._tools, text="Locate...", command=lambda t=tool.tool_id: self._locate(t)).grid(
                row=row, column=2, padx=2)
        ttk.Button(self._tools, text="Rescan", command=self._rescan).grid(row=len(rows), column=2, padx=2, pady=4)

    def _render_licence(self) -> None:
        from tkinter import ttk

        for child in self._licence.winfo_children():
            child.destroy()
        if not self.model.starnet_needed():
            ttk.Label(self._licence, text="Not needed for the current choices.").grid(row=0, column=0, sticky="w")
            return
        if self.model.starnet_accepted():
            ttk.Label(self._licence, text="Accepted.").grid(row=0, column=0, sticky="w")
            return
        import tkinter as tk

        text = tk.Text(self._licence, height=8, width=60, wrap="word")
        text.insert("1.0", self.model.starnet_licence_text() or "The licence file was not found next to StarNet2.")
        text.configure(state="disabled")
        text.grid(row=0, column=0, sticky="we")
        ttk.Button(self._licence, text="I accept this licence", command=self._accept_starnet).grid(
            row=1, column=0, sticky="w", pady=4)


#: The master list's columns, as Loom's (lib/UI.js:879-897).
def wheel_units(delta: int, platform: str = sys.platform) -> int:
    """Scroll units for one mouse-wheel event: macOS reports small deltas, Windows multiples of 120."""
    if not delta:
        return 0
    if platform == "darwin":
        return -delta
    steps = -int(delta / 120)
    return steps if steps else (-1 if delta > 0 else 1)


class _ScrollArea:
    """A vertically scrolling frame: ``inner`` holds the widgets, ``outer`` is gridded into the window.

    The visible height is the form's own height, capped so the window fits on the screen with its footer.
    """

    SCREEN_MARGIN = 220   # pixels of screen height left for the title bar, the footer and the menu bar

    def __init__(self, parent: Any) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.outer = ttk.Frame(parent)
        self.outer.columnconfigure(0, weight=1)
        self.outer.rowconfigure(0, weight=1)
        background = ttk.Style().lookup("TFrame", "background") or None
        self.canvas = tk.Canvas(self.outer, highlightthickness=0, borderwidth=0, background=background)
        self.bar = ttk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar.grid(row=0, column=1, sticky="ns")
        self.inner = ttk.Frame(self.canvas, padding=10)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self._update())
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.bind("<Enter>", lambda _e: self._wheel(True))
        self.canvas.bind("<Leave>", lambda _e: self._wheel(False))

    def fit(self) -> None:
        """Size the visible area to the form, at most the screen height less the margin."""
        self.inner.update_idletasks()
        limit = max(200, self.canvas.winfo_screenheight() - self.SCREEN_MARGIN)
        self.canvas.configure(width=self.inner.winfo_reqwidth(), height=min(self.inner.winfo_reqheight(), limit))
        self._update()

    def _update(self) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _wheel(self, on: bool) -> None:
        # Bound only while the pointer is over the form, so the scan table and the drop-down lists keep their own.
        if on:
            self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(wheel_units(e.delta), "units"))
            self.canvas.bind_all("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"))
            self.canvas.bind_all("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"))
        else:
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.canvas.unbind_all(sequence)


MASTER_COLUMNS: tuple[str, ...] = ("Filter", "Size", "Drizzle", "Source", "Created", "FWHM", "Ecc", "Noise", "Stars")


def _level_pairs(levels: Sequence[str]) -> list[tuple[str, str]]:
    return [(v, "None" if v == NONE else _capitalised(v)) for v in levels]


def _mib(count: int) -> str:
    return f"{count / (1024 * 1024):.1f} MB"


class ProgressWindow:
    """The progress window: the stage line, a bar and Cancel (default No). Polls :class:`ProgressModel` every 200 ms."""

    POLL_MS = 200

    def __init__(self, master: Any, model: ProgressModel) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.model = model
        self.top = tk.Toplevel(master)
        self.top.title("Loom - running")
        self.top.resizable(False, False)
        frame = ttk.Frame(self.top, padding=10)
        frame.grid(sticky="nsew")
        self._stage = tk.StringVar(value=model.label())
        ttk.Label(frame, textvariable=self._stage, width=64).grid(row=0, column=0, sticky="w")
        self._bar = ttk.Progressbar(frame, maximum=100.0, length=460)
        self._bar.grid(row=1, column=0, sticky="we", pady=6)
        self._button = ttk.Button(frame, text="Cancel", command=self._cancel)
        self._button.grid(row=2, column=0, sticky="e")
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self._poll()

    def _poll(self) -> None:
        self.model.pump()
        self._stage.set(self.model.label())
        self._bar["value"] = self.model.percent
        if self.model.done:
            self._button.configure(text="Close", command=self.top.destroy)
            return
        self.top.after(self.POLL_MS, self._poll)

    def _cancel(self) -> None:
        from tkinter import messagebox

        if self.model.done:
            self.top.destroy()
            return
        if self.model.cancel_requested:
            return
        answer = messagebox.askyesno(ProgressModel.CANCEL_TITLE,
                                     f"{ProgressModel.CANCEL_QUESTION}\n\n{ProgressModel.CANCEL_DETAIL}",
                                     default="no", parent=self.top)
        if self.model.confirm_cancel(bool(answer)):
            self._button.state(["disabled"])
