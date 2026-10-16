"""The run orchestrator: Loom's ``Pipeline.run`` for files (``lib/Pipeline.js:2762-2888``) as one function.

``run(config, ctx_factory)`` executes Loom's fixed order over the merged stage modules: channel records, broadband
correction (L, R, G, B), narrowband gradient removal (H, S, O), registration to the reference, the crop to the common
area, halo matching, narrowband balance, the clean white balance, the RGB composite, the palettes, the L finishing,
then publish, export and the main-result hand-off. Every chain runs through :func:`chain.process_chain`, so a cached
stage is reused and the latest usable entry is the restart point. No image maths lives here.

Plates are never modified in place: a chain result may be a cache file, a work-folder file or a source plate, and every
runner writes a new output. The work folder ``<cache>/work/<stamp>/`` is removed when the run ends, except after an
error with ``keepWindowsOnError``. The run log ``<cache>/logs/loom-run-<stamp>.log`` holds every logged line.

Importing this module has no side effects and does not import sirilpy or tkinter.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional, TextIO

from loomsiril.io_fits import write_fits

from . import cache, channels, colour_load, finishing, icc, levels, naming, reference, stages_colour, stages_correct
from . import osc, stages_geometry
from . import publish as publish_steps
from .chain import CANCEL_MESSAGE, ChainResult, RunCancelled, process_chain
from .runctx import RunContext, StageOutput
from .stagekit import StageFailed

__all__ = ["RunReport", "run"]

#: Channel letters in Loom's order (lib/Util.js:436).
CHANNEL_ORDER: tuple[str, ...] = ("L", "R", "G", "B", "H", "S", "O")
BROADBAND_ORDER: tuple[str, ...] = ("L", "R", "G", "B")
NARROWBAND_ORDER: tuple[str, ...] = ("H", "S", "O")
RGB_ORDER: tuple[str, ...] = ("R", "G", "B")

#: Longest text written to the run log or to the callbacks for one line.
_MAX_LINE = 400

_GIB = 1024 ** 3


@dataclass
class RunReport:
    """What a run produced. ``outputs`` maps each published result name to its saved file."""

    produced: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    exports: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    log_path: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "produced": list(self.produced),
            "outputs": dict(self.outputs),
            "exports": list(self.exports),
            "duration_s": self.duration_s,
            "log_path": self.log_path,
            "warnings": list(self.warnings),
        }


def _js_number(value: Any) -> str:
    """A number as JavaScript prints it in a log line: no ``.0`` on a whole number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else str(number)


def _short(text: Any) -> str:
    """One line, at most ``_MAX_LINE`` characters: large payloads must never reach the log or the console."""
    s = str(text).replace("\r", " ").replace("\n", " | ")
    return s if len(s) <= _MAX_LINE else s[:_MAX_LINE] + "..."


class _RunLog:
    """The run log file, plus the warnings the report carries. Writes are serialised (tool threads log too)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.warnings: list[str] = []
        self._lock = threading.Lock()
        self._fh: Optional[TextIO] = None

    def open(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf-8")
        except OSError:
            self._fh = None
            self.path = ""

    def line(self, level: str, text: Any) -> None:
        short = _short(text)
        with self._lock:
            if level == "warn":
                self.warnings.append(short)
            if self._fh is not None:
                self._fh.write(f"[{level}] {short}\n")
                self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def _file_stamp(path: str) -> Optional[tuple[int, int]]:
    """(modification time in ns, size) of a file, or None when it does not exist."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _checkpoint(ctx: RunContext) -> None:
    """A cancel checkpoint between two steps (``Pipeline.checkAbort``)."""
    if ctx.cancelled():
        raise RunCancelled(CANCEL_MESSAGE)


def _unique_work_folder(root: str, stamp: str) -> str:
    base = os.path.join(root, "work", stamp)
    path = base
    n = 1
    while os.path.exists(path):
        path = f"{base}-{n}"
        n += 1
    os.makedirs(path)
    return path


def _format_gib(num_bytes: int) -> str:
    return f"{num_bytes / _GIB:.1f} GiB"


@dataclass
class _Chan:
    """One channel record: its source file and fingerprint, its camera, and the running state of its chain."""

    key: str
    path: str
    source_key: str
    instrume: Optional[str] = None
    current_key: str = ""
    clean_key: Optional[str] = None
    plate: Optional[str] = None  # the plate as the latest chain left it (None until something needs the pixels)
    #: A colour member (a plane of a colour master) names its slot and plane; ``path`` stays the master's path, and
    #: ``source`` gives the member's one-channel plate. All three are None for a mono channel.
    group: Optional[str] = None
    plane: Optional[int] = None
    source: Optional[Callable[[], str]] = None


class _Run:
    """The state of one run: the context, the (copied) configuration, the store and the channel records."""

    def __init__(self, ctx: RunContext, config: dict[str, Any], store: Optional[cache.CacheStore], root: str,
                 plan: Mapping[str, Any]) -> None:
        self.ctx = ctx
        self.cfg = config
        self.store = store
        self.root = root
        self.plan = plan
        self.work = ctx.work_dir
        self.chans: dict[str, _Chan] = {}
        self.group_filters: dict[str, Optional[str]] = {}  # colour slot -> the master's FILTER keyword

    # -- helpers --------------------------------------------------------------------------------------------------

    def plate_of(self, key: str) -> str:
        """The channel's plate, loaded from its source file the first time something needs its pixels."""
        ch = self.chans[key]
        if ch.plate is None:
            ch.plate = ch.source() if ch.source is not None else stages_correct.ingest_channel(self.ctx, key, ch.path)
        return ch.plate

    def publish_log(self, level: str, text: str) -> None:
        if level == "warn":
            self.ctx.warn(text)
        elif level == "error":
            self.ctx.error(text)
        else:
            self.ctx.log(text)

    def cache_log(self, level: str, text: str) -> None:
        if level == "warn":
            self.ctx.warn(text)
        else:
            self.ctx.log(text)

    # -- steps ----------------------------------------------------------------------------------------------------

    def _colour_loaders(self, paths: Mapping[str, Any]) -> dict[str, tuple[colour_load.ColourGroupLoader, str, str]]:
        """One loader per supplied colour slot: slot -> (loader, the master's INSTRUME, the master's FILTER)."""
        loaders: dict[str, tuple[colour_load.ColourGroupLoader, str, str]] = {}
        for slot in osc.colour_supplied(self.cfg):
            kind = (self.cfg.get("oscKinds") or {}).get(slot)
            if not kind:
                raise ValueError(f"internal: the kind of {slot} was not resolved by preflight")
            path = str(paths.get(slot) or "")
            keywords = channels.read_header(path).get("keywords", [])
            loader = colour_load.ColourGroupLoader(
                slot, kind, path, stages_correct.source_key(path),
                store=self.store, work_dir=self.work, ignore_cache=bool(self.cfg.get("ignoreCache")),
                convert=lambda src, name: stages_correct.ingest_channel(self.ctx, name, src), warn=self.ctx.warn)
            filter_value = channels.keyword_value(keywords, "FILTER")
            loader.when_validated(self._group_logger(slot, kind, path, filter_value))
            loaders[slot] = (loader, channels.keyword_value(keywords, "INSTRUME"), filter_value)
        return loaders

    def _group_logger(self, slot: str, kind: str, path: str, filter_value: Optional[str]) -> Callable[[Any], None]:
        def log_group(rec: Any) -> None:
            self.ctx.log(colour_load.group_log_line(slot, path, kind, filter_value, rec))

        return log_group

    def load_channels(self) -> None:
        paths = self.cfg.get("paths") or {}
        records: dict[str, dict[str, Any]] = {}
        loaders = self._colour_loaders(paths)
        member_of = {member: slot for slot in loaders for member in osc.COLOUR_MEMBERS[slot]}
        for key in CHANNEL_ORDER:
            path = str(paths.get(key) or "")
            if not path and key in member_of:
                slot = member_of[key]
                loader, instrume, filter_value = loaders[slot]
                self.group_filters[slot] = filter_value
                plane = osc.OSC_RECIPES[slot][key]
                sibling = loader.sibling_key(plane)
                self.chans[key] = _Chan(key=key, path=str(paths[slot]), source_key=sibling, instrume=instrume,
                                        current_key=sibling, group=slot, plane=plane,
                                        source=lambda ld=loader, pl=plane: ld.plate(pl))
                self.ctx.log(f"load {key} <- {paths[slot]} (plane {plane})")
                records[key] = {"key": key, "instrume": instrume}
                continue
            if not path:
                continue
            header = channels.read_header(path)
            instrume = channels.keyword_value(header.get("keywords", []), "INSTRUME")
            source_key = stages_correct.source_key(path)
            self.chans[key] = _Chan(key=key, path=path, source_key=source_key, instrume=instrume,
                                    current_key=source_key)
            self.ctx.log(f"load {key} <- {path}")
            records[key] = {"key": key, "instrume": instrume}
        if not self.chans:
            raise ValueError("no channel files are selected")
        for line in channels.inherit_instrument(records):
            self.ctx.log(line)
        for key, record in records.items():
            self.chans[key].instrume = record.get("instrume")
        _checkpoint(self.ctx)

    def correct_broadband(self) -> None:
        studio = stages_correct.studio_found(self.ctx)
        for key in BROADBAND_ORDER:
            ch = self.chans.get(key)
            if ch is None:
                continue
            params = stages_correct.broadband_stage_params(key, self.cfg, studio)
            chain = cache.build_stage_keys(ch.source_key, params)
            ch.current_key = chain[-1]["key"] if chain else ch.source_key
            for entry in chain:
                if entry["stage"] == "graxpert":
                    ch.clean_key = str(entry["key"])
            runners = stages_correct.broadband_runners(self.ctx, key, self.cfg)
            result = process_chain(self.ctx, key, self._ingest_source(key), chain, runners, self.store, self.cfg)
            ch.plate = result.path
            _checkpoint(self.ctx)

    def _ingest_source(self, key: str) -> Callable[[], str]:
        ch = self.chans[key]
        if ch.source is not None:
            return ch.source

        def source() -> str:
            return stages_correct.ingest_channel(self.ctx, key, ch.path)

        return source

    def correct_narrowband(self) -> None:
        params = stages_correct.narrowband_stage_params(self.cfg)
        if params is None:
            return  # no chain: the channels stay lazy, loaded only when a later stage needs them
        for key in NARROWBAND_ORDER:
            ch = self.chans.get(key)
            if ch is None:
                continue
            chain = cache.build_stage_keys(ch.source_key, params)
            ch.current_key = chain[-1]["key"]
            runners = stages_correct.narrowband_runners(self.ctx, key, self.cfg)
            result = process_chain(self.ctx, key, self._ingest_source(key), chain, runners, self.store, self.cfg)
            ch.plate = result.path
            _checkpoint(self.ctx)

    def _registration_quality(self, present: Sequence[str]) -> dict[str, Optional[dict[str, Any]]]:
        """The star measures of each channel's master, used only when L is absent (``registrationQuality``)."""
        if "L" in self.chans:
            return {}
        paths = [self.chans[key].path for key in present]
        measured = stages_correct.master_quality(self.ctx.ops, paths,
                                                 self.root if self.cfg.get("useCache") else self.work,
                                                 self.ctx.warn)
        return {key: measured.get(self.chans[key].path) for key in present}

    def register(self) -> str:
        present = [key for key in CHANNEL_ORDER if key in self.chans]
        # Colour groups first: their reference (OSC's G, else OSCNB's H) needs no star measurement, and the siblings
        # of a colour master share the path the measurement reads.
        groups = osc.osc_channel_groups({k: {"group": ch.group} for k, ch in self.chans.items()})
        plan = osc.osc_registration_plan(present, groups) if groups else None
        quality = {} if groups else self._registration_quality(present)
        if plan is not None and plan["ref"] is not None:
            ref_key, reason = plan["ref"], "colour group reference"
        else:
            ref_key, reason = reference.registration_choice(present, quality)
        if ref_key is None:
            raise StageFailed("No channels to register.")
        self.ctx.log(f"register: reference is {ref_key}: {reason}")
        ref_fingerprint = self.chans[ref_key].current_key
        grouped: set[str] = set()
        if plan is not None:
            grouped.update(plan["identity"])  # already on the reference grid
            for entry in plan["groups"]:
                self._register_group(entry, ref_key, ref_fingerprint)
                grouped.update(entry["members"])
                _checkpoint(self.ctx)
        for key in present:
            if key == ref_key or key in grouped:
                continue
            ch = self.chans[key]
            chain = cache.build_stage_keys(ch.current_key, {"register": stages_geometry.register_stage_params(
                ref_fingerprint)})
            runners = {"register": self._register_runner(key, ref_key)}
            result = process_chain(self.ctx, key, self._plate_source(key), chain, runners, self.store, self.cfg)
            ch.plate = result.path
            ch.current_key = str(chain[-1]["key"])
            _checkpoint(self.ctx)
        return ref_key

    def _register_group(self, entry: Mapping[str, Any], ref_key: str, ref_fingerprint: str) -> None:
        """Registers one colour group onto the reference as a single image, so its members share one transform.

        The group image is what the cache keeps; each registered member plate is stored under a key derived from it.
        With the cache off nothing is read or written; with the cache ignored nothing is read and everything is
        written. A member's plate and ``current_key`` become its registered split. The unregistered plates are
        never stored again: they were handed out by the colour loader.
        """
        members: list[str] = list(entry["members"])
        plane_names: list[str] = list(entry["planes"])
        chans = [self.chans[k] for k in members]
        label = "/".join(members)
        params = stages_geometry.group_register_params(ref_fingerprint, plane_names)
        key = osc.osc_group_register_key("registerGroup", [c.current_key for c in chans], params)
        split_keys = [osc.osc_registered_sibling_key(key, i) for i in range(len(members))]
        store = self.store
        action = "register"
        if store is not None and not self.cfg.get("ignoreCache"):
            action = osc.osc_group_cache_action(store.lookup(key) is not None,
                                                [store.lookup(sk) is not None for sk in split_keys],
                                                {"useCache": True, "ignoreCache": False})
        parts: Optional[list[str]] = None
        if action == "load":
            loaded = [self._usable_split(chans[i], split_keys[i]) for i in range(len(members))]
            if all(p is not None for p in loaded):
                self.ctx.log(f"[cache] {label} registerGroup HIT")
                parts = [str(p) for p in loaded]
            else:
                action = "split"
        if parts is None and action == "split" and store is not None:
            parts = self._split_cached_group(chans, key, split_keys, label, list(entry["planes"]))
        if parts is None:
            self.ctx.log(f"[cache] {label} registerGroup MISS")
            parts = self._register_and_split(entry, ref_key, chans, key, split_keys, label, params)
        for ch, path, split_key in zip(chans, parts, split_keys):
            ch.plate = path
            ch.current_key = split_key

    def _usable_split(self, ch: _Chan, split_key: str) -> Optional[str]:
        """The cached registered plate of a member, or None when it is absent, will not read or has other provenance."""
        assert self.store is not None and ch.plane is not None
        loaded = self.store.load(split_key)
        if loaded is None:
            return None
        want = stages_geometry.member_split_header({}, ch.key, ch.path, ch.plane)
        header = loaded[1]
        if header.get("FILTER") != want["FILTER"] or header.get("LOOMSRC") != want["LOOMSRC"]:
            return None
        return self.store.path_for(split_key)

    def _split_cached_group(self, chans: list[_Chan], key: str, split_keys: list[str], label: str,
                            plane_names: Sequence[str]) -> Optional[list[str]]:
        assert self.store is not None
        loaded = self.store.load(key)
        if loaded is None or len(loaded[0]) != 3:
            return None
        planes, header = loaded
        self.ctx.log(f"[cache] {label} registerGroup HIT (split again)")
        parts: list[str] = []
        for i, ch in enumerate(chans):
            cached = self._usable_split(ch, split_keys[i])
            parts.append(cached if cached is not None
                         else self._place_split(ch, i, planes[i], header, split_keys[i], plane_names))
        return parts

    def _register_and_split(self, entry: Mapping[str, Any], ref_key: str, chans: list[_Chan], key: str,
                            split_keys: list[str], label: str, params: Mapping[str, Any]) -> list[str]:
        slot = str(entry["slot"])
        self.ctx.log(f"register: {label} -> {ref_key} (colour group {slot}, one transform)")
        ref_plate = self.plate_of(ref_key)
        group_plates = [self.plate_of(k) for k in entry["planes"]]
        planes, header = stages_geometry.register_group_image(
            self.ctx.ops, ref_plate, group_plates, stages_geometry.GROUP_REGISTER_LAYER[slot], self.work,
            self.ctx.log)
        if self.store is not None:
            meta = {"channel": label, "stage": "registerGroup", "params": dict(params)}
            if self.store.store(key, planes, header, meta) is None:
                self.ctx.warn(f"[cache] the registered {label} image could not be cached")
        return [self._place_split(ch, i, planes[i], header, split_keys[i], list(entry["planes"]))
                for i, ch in enumerate(chans)]

    def _place_split(self, ch: _Chan, plane: int, pixels: Any, group_header: Mapping[str, Any], split_key: str,
                     planes: Sequence[str]) -> str:
        """Stores one registered plane under its split key (or in the work folder); the keywords are the member's."""
        assert ch.plane is not None
        header = stages_geometry.member_split_header(group_header, ch.key, ch.path, ch.plane)
        if self.store is not None:
            meta = {"channel": ch.key, "stage": "registerGroup", "params": {"plane": plane, "planes": ",".join(planes)}}
            stored = self.store.store(split_key, [pixels], header, meta)
            if stored is not None:
                return stored
            self.ctx.warn(f"[cache] the registered {ch.key} could not be cached; the run uses a copy in the work folder")
        out = os.path.join(self.work, f"{ch.key}_register.fits")
        write_fits(out, [pixels], header, bitpix=-32)
        return out

    def _plate_source(self, key: str) -> Callable[[], str]:
        def source() -> str:
            return self.plate_of(key)

        return source

    def _register_runner(self, key: str, ref_key: str) -> Callable[[Any, Any], StageOutput]:
        def run_register(in_path: Any, out_path: Any) -> StageOutput:
            ref_path = self.plate_of(ref_key)
            placed = stages_geometry.register_channels(self.ctx.ops, {ref_key: ref_path, key: in_path}, ref_key,
                                                       self.work, self.ctx.log)
            return StageOutput(path=placed[key])

        return run_register

    def crop(self, ref_key: str) -> tuple[dict[str, str], dict[str, str], tuple[int, int, int, int], dict[str, int]]:
        present = [key for key in CHANNEL_ORDER if key in self.chans]
        uncropped = {key: self.plate_of(key) for key in present}
        rect = stages_geometry.common_crop_rect(uncropped, ref_key, self.ctx.log)
        cropped = {key: stages_geometry.crop_plate(self.ctx.ops, uncropped[key],
                                                   os.path.join(self.work, f"{key}_crop.fits"), rect)
                   for key in present}
        common = {"x0": rect[0], "y0": rect[1], "x1": rect[2], "y1": rect[3]}
        _checkpoint(self.ctx)
        return uncropped, cropped, rect, common

    def halos_and_balance(self, plates: dict[str, str]) -> dict[str, str]:
        if self.cfg.get("reduceHalos"):
            plates = stages_geometry.match_halos(self.ctx.ops, plates, self.work, self.ctx.log)
            _checkpoint(self.ctx)
        plates = stages_geometry.balance_narrowband(self.ctx.ops, plates, bool(self.cfg.get("palettes")), self.work,
                                                    self.ctx.log)
        _checkpoint(self.ctx)
        return plates

    def clean_white_balance(self, ref_key: str, uncropped: Mapping[str, str], rect: tuple[int, int, int, int],
                            lum_instrume: Any) -> Optional[list[float]]:
        if not all(key in self.chans for key in RGB_ORDER) or not levels.tool_chosen(self.cfg.get("sharpenTool")):
            return None
        clean: dict[str, tuple[str, str]] = {}
        missing: list[str] = []
        for key in RGB_ORDER:
            entry_key = self.chans[key].clean_key
            path = self.store.lookup(entry_key) if (self.store is not None and entry_key) else None
            if entry_key and path is not None:
                clean[key] = (entry_key, path)
            else:
                missing.append(key)
        if missing:
            self.ctx.warn("could not build the uncorrected reference (the pre-correction result for "
                          + ", ".join(missing) + " is not in the cache"
                          + (" for this run" if self.store is None else "")
                          + "). Falling back to SPCC on the corrected composite, which is known to produce a colour "
                          "cast on aberration-corrected data.")
            return None
        use_cache = bool(self.cfg.get("useCache"))
        factors = stages_colour.measure_clean_white_balance(
            self.ctx, clean, uncropped[ref_key], rect, self.cfg, lum_instrume,
            self.store if use_cache else None, use_cache, bool(self.cfg.get("ignoreCache")),
            ref_key=self.chans[ref_key].current_key, ref_channel=ref_key)
        _checkpoint(self.ctx)
        return factors

    def build_rgb(self, plates: Mapping[str, str], common: Mapping[str, int], factors: Optional[list[float]],
                  lum_instrume: Any) -> Optional[ChainResult]:
        if not all(key in plates for key in RGB_ORDER):
            self.ctx.log("combine: skipped: no RGB group supplied")
            return None
        reduce_halos = bool(self.cfg.get("reduceHalos"))
        source = cache.rgb_source_key({key: self.chans[key].current_key for key in RGB_ORDER}, common, reduce_halos)
        params: dict[str, Any] = stages_colour.rgb_stage_params(self.cfg, lum_instrume, factors)
        params.update(levels.finishing_params(levels.RGB_FINISHING, self.cfg))
        chain = cache.build_stage_keys(source, params)
        runners: dict[str, Callable[[Any, Any], StageOutput]] = dict(
            stages_colour.rgb_runners(self.ctx, plates, self.cfg, lum_instrume, factors,
                                      self._sources_of(RGB_ORDER, False)))
        runners.update(finishing.finishing_runners(self.ctx, "RGB", levels.RGB_FINISHING, self.cfg,
                                                   noun="composite"))
        result = process_chain(self.ctx, "RGB", lambda: plates["R"], chain, runners, self.store, self.cfg)
        _checkpoint(self.ctx)
        return result

    def build_palettes(self, plates: Mapping[str, str], common: Mapping[str, int], rgb_names: Any
                       ) -> dict[str, ChainResult]:
        for line in channels.report_skipped_palettes(self.cfg):
            self.ctx.warn(line)
        reduce_halos = bool(self.cfg.get("reduceHalos"))
        built: dict[str, ChainResult] = {}
        for pal in channels.wanted_palettes(self.cfg):
            mapping = channels.PALETTES[pal]
            for line in self.palette_source_lines(pal, mapping):
                self.ctx.log(line)
            keys3 = [self.chans[letter].current_key for letter in mapping]
            source = cache.palette_source_key(pal, keys3, common, reduce_halos)
            params: dict[str, Any] = stages_colour.palette_stage_params(pal, self.cfg)
            params.update(levels.finishing_params(levels.PALETTE_FINISHING, self.cfg))
            chain = cache.build_stage_keys(source, params)
            runners: dict[str, Callable[[Any, Any], StageOutput]] = dict(
                stages_colour.palette_runners(self.ctx, pal, plates, self.cfg, rgb_names,
                                          self._sources_of(mapping, True)))
            runners.update(finishing.finishing_runners(self.ctx, pal, levels.PALETTE_FINISHING, self.cfg,
                                                       noun="palette"))
            first = mapping[0]
            built[pal] = process_chain(self.ctx, pal, (lambda p=plates[first]: p), chain, runners, self.store,
                                       self.cfg)
            _checkpoint(self.ctx)
        return built

    def split_luminance(self, plate: str, common: Mapping[str, int]) -> Optional[ChainResult]:
        if "L" not in self.chans or levels.star_extraction_params(self.cfg) is None:
            return None
        reduce_halos = bool(self.cfg.get("reduceHalos"))
        source = cache.lstars_source_key(self.chans["L"].current_key, common, reduce_halos)
        params = levels.finishing_params(levels.L_FINISHING, self.cfg)
        chain = cache.build_stage_keys(source, params)
        runners = finishing.finishing_runners(self.ctx, "L", levels.L_FINISHING, self.cfg)
        result = process_chain(self.ctx, "L", (lambda p=plate: p), chain, runners, self.store, self.cfg)
        _checkpoint(self.ctx)
        return result

    def _sources_of(self, keys: Sequence[str], per_plane: bool) -> Optional[str]:
        """The LOOMSRC value of a composite or a palette made of the channels ``keys``: None unless every one of them
        is a plane of a colour master (a mono channel, or a mix, has none)."""
        if not all(key in self.chans and self.chans[key].group for key in keys):
            return None
        records = {key: {"group": self.chans[key].group, "path": self.chans[key].path, "plane": self.chans[key].plane}
                   for key in keys}
        return colour_load.composite_loomsrc(records, list(keys), per_plane)

    def result_sources(self, names: Sequence[str]) -> dict[str, str]:
        """The LOOMSRC value of each result among ``names`` that comes from a colour master: the RGB composite and the
        palettes, with their starless, stars and linear companions. A channel plate carries its own from the load, and
        a mono result has none."""
        values: dict[str, Optional[str]] = {"RGB": self._sources_of(RGB_ORDER, False)}
        for pal, mapping in channels.PALETTES.items():
            values[pal] = self._sources_of(mapping, True)
        out: dict[str, str] = {}
        for name in names:
            for base, value in values.items():
                if value is not None and name in (base, base + "_starless", base + "_stars", base + "_linear"):
                    out[name] = value
        return out

    def palette_source_lines(self, pal: str, mapping: Sequence[str]) -> list[str]:
        """The run-log lines of a palette built from a dual-band colour master: where its channels come from, and that
        its bandwidths approximate (H and O are not single lines there). Nothing for any other palette."""
        if not any(key in self.chans and self.chans[key].group == "OSCNB" for key in mapping):
            return []
        js = osc.js_config(self.cfg)
        filter_value = self.group_filters.get("OSCNB")
        info = osc.osc_filter_info(filter_value)
        lines = [osc.osc_palette_sources(js, {"OSCNB": {"name": info["name"] if info else ""}}, pal)]
        bw = osc.osc_narrowband_bandwidths(js, {"filter": filter_value})
        lines.append(f"{pal}: approximation: H and O from a colour master are not single lines; bandwidths "
                     f"R {_js_number(bw['R'])}nm, G {_js_number(bw['G'])}nm, B {_js_number(bw['B'])}nm")
        return [line for line in lines if line]

    def publish_results(self, named: Mapping[str, str]) -> dict[str, str]:
        available = {name: True for name in named}
        kept = naming.kept_results(available, self.cfg)
        to_publish: dict[str, str] = {}
        for name in kept:
            base = name[:-len("_starless")] if name.endswith("_starless") else name
            to_publish[name] = named[base]
        _checkpoint(self.ctx)
        outputs = publish_steps.publish(self.ctx.ops, to_publish, self.cfg, self.cfg["outputDir"], self.plan,
                                        self.publish_log, self.result_sources(to_publish))
        return outputs

    def export_results(self, outputs: Mapping[str, str]) -> list[str]:
        """The export files this run wrote: a file counts only when it is new or changed by the export, so a stale
        file of the same name from an earlier run is never listed."""
        export_dir = str(self.cfg.get("exportDir") or "")
        if not export_dir:
            return []
        expected = [os.path.join(export_dir, naming.export_file_name(name) + ".tif") for name in sorted(outputs)]
        if self.cfg.get("exportPsb"):
            expected.append(os.path.join(export_dir, naming.psb_base_name(self.cfg) + ".psb"))
        before = {path: _file_stamp(path) for path in expected}
        publish_steps.export(self.ctx.ops, outputs, self.cfg, self.plan, self.publish_log)
        if not self.cfg.get("stretch"):
            return []
        return [path for path in expected if _file_stamp(path) is not None and _file_stamp(path) != before[path]]


def _execute(run_state: _Run) -> tuple[dict[str, str], list[str]]:
    """The steps of Loom's run, in order. Returns the published outputs and the export files."""
    run_state.load_channels()
    run_state.correct_broadband()
    run_state.correct_narrowband()
    ref_key = run_state.register()
    uncropped, cropped, rect, common = run_state.crop(ref_key)
    plates = run_state.halos_and_balance(cropped)
    lum = run_state.chans["L"].instrume if "L" in run_state.chans else run_state.chans[ref_key].instrume
    factors = run_state.clean_white_balance(ref_key, uncropped, rect, lum)
    rgb = run_state.build_rgb(plates, common, factors, lum)
    rgb_names = stages_colour.rgb_spcc_names(run_state.cfg, lum) if rgb is not None else None
    palettes = run_state.build_palettes(plates, common, rgb_names)
    l_tail = run_state.split_luminance(cropped["L"], common) if "L" in cropped else None
    named = _named_results(run_state, cropped, plates, l_tail, rgb, palettes)
    outputs = run_state.publish_results(named)
    exports = run_state.export_results(outputs)
    publish_steps.leave_main_result(run_state.ctx.ops, outputs, run_state.publish_log)
    return outputs, exports


def _named_results(run_state: _Run, cropped: Mapping[str, str], plates: Mapping[str, str],
                   l_tail: Optional[ChainResult], rgb: Optional[ChainResult],
                   palettes: Mapping[str, ChainResult]) -> dict[str, str]:
    """Every plate the run can publish, by result name (the names of ``naming.kept_results``)."""
    named: dict[str, str] = {}
    if "L" in cropped:
        base = l_tail.path if l_tail is not None else cropped["L"]
        named["L"] = base
        stars = l_tail.companions.get("stars") if l_tail is not None else None
        if stars:
            named["L_stars"] = stars
            _separate(run_state, named)
    for key in NARROWBAND_ORDER:
        if key in plates:
            named[key] = plates[key]
    if rgb is not None:
        named["RGB"] = rgb.path
        if rgb.companions.get("stars"):
            named["RGB_stars"] = rgb.companions["stars"]
        if rgb.companions.get("linear"):
            named["RGB_linear"] = rgb.companions["linear"]
    for pal, result in palettes.items():
        named[pal] = result.path
        if result.companions.get("stars"):
            named[pal + "_stars"] = result.companions["stars"]
        if result.companions.get("linear"):
            named[pal + "_linear"] = result.companions["linear"]
    return named


def _separate(run_state: _Run, named: dict[str, str]) -> None:
    """Frequency separation of the L stars plate, only on request. A failure costs the two layers, nothing else."""
    if not run_state.cfg.get("separateLStars"):
        return
    try:
        low, high = finishing.separate_l_stars(run_state.ctx, named["L_stars"], run_state.work)
    except RunCancelled:
        raise
    except Exception as exc:
        if run_state.ctx.cancelled():
            raise RunCancelled(CANCEL_MESSAGE) from exc
        run_state.ctx.warn(f"L_stars was not separated ({exc}); the plate itself is unaffected")
        return
    named["L_stars_low"] = low
    named["L_stars_high"] = high


def run(config: Mapping[str, Any], ctx_factory: Callable[[str], RunContext], *,
        installed_icc: Optional[Sequence[Mapping[str, str]]] = None,
        platform: Optional[str] = None, env: Optional[Mapping[str, str]] = None,
        home: Optional[str] = None, stamp: Optional[str] = None) -> RunReport:
    """Run Loom's pipeline for the files in ``config["paths"]`` and return the report.

    ``ctx_factory(work_dir)`` builds the run's :class:`RunContext`; the work folder already exists when it is called.
    ``config["outputDir"]`` must be set (preflight checks the rest and is not run here). ``installed_icc`` is the
    scan of the installed profiles (``icc.installed_icc_profiles``); none means the built-in sRGB is assigned.
    ``platform``, ``env`` and ``home`` locate the default cache folder; ``stamp`` names the work folder and the log.

    Raises :class:`RunCancelled` at a checkpoint after the run is cancelled, and whatever a step raises otherwise.
    In both cases the work folder is removed (an error keeps it with ``keepWindowsOnError``).
    """
    cfg: dict[str, Any] = dict(config)
    if not cfg.get("outputDir"):
        raise ValueError("run needs config['outputDir'] (the output folder is set by preflight)")
    started = time.monotonic()
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    home = os.path.expanduser("~") if home is None else home
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S") if stamp is None else stamp

    chosen = str(cfg.get("cacheDir") or "").strip()
    missing = cache.folder_missing(chosen)
    root = cache.default_cache_dir(platform, env, home) if (not chosen or missing) else chosen
    rlog = _RunLog(os.path.join(root, "logs", "loom-run-" + stamp + ".log"))
    rlog.open()

    report = RunReport()
    work: Optional[str] = None
    ctx: Optional[RunContext] = None
    saved_wd: Optional[str] = None
    compression_was_on = False
    failure: Optional[BaseException] = None
    try:
        work = _unique_work_folder(root, stamp)
        ctx = ctx_factory(work)
        ctx.work_dir = work
        ctx.log, ctx.warn, ctx.error = _wrap_logs(ctx, rlog)
        if cfg.get("useCache") and missing:
            cfg["useCache"] = False
            ctx.warn("the cache folder is not there (" + chosen + "); running without the cache. If that is an "
                     "external drive, mount it and start Loom again.")
        store: Optional[cache.CacheStore] = None
        if cfg.get("useCache"):
            store = cache.CacheStore(root, log=lambda level, text: ctx.warn(text) if level == "warn" else ctx.log(text))
            free = store.low_space()
            if free is not None:
                ctx.warn("the cache folder's drive has only " + _format_gib(free) + " free (a run needs about "
                         + _format_gib(cache.LOW_SPACE_BYTES) + "). Writes will fail once it is full, and a broken "
                         "entry is discarded, so this run will not be cached. Free some space, clear Loom's cache, or "
                         "untick Use cache.")
        plan = icc.profile_plan(list(installed_icc or []))
        run_state = _Run(ctx, cfg, store, root, plan)
        saved_wd = ctx.ops.wd()
        # Siril's FITS compression would make every save write <name>.fits.fz (and quantise 32-bit data): off for
        # the run, restored in the finally block.
        if ctx.ops.fits_compression():
            ctx.ops.set_fits_compression(False)
            compression_was_on = True
        outputs, exports = _execute(run_state)
        report.outputs = dict(outputs)
        report.produced = naming.ordered_output_keys(list(outputs))
        report.exports = exports
        ctx.log(_done_line(cfg, exports, time.monotonic() - started))
    except RunCancelled as exc:
        failure = exc
        if ctx is not None:
            ctx.log(CANCEL_MESSAGE)
        raise
    except BaseException as exc:
        failure = exc
        text = "pipeline: " + type(exc).__name__ + ": " + str(exc)
        if ctx is not None:
            ctx.error(text)
        else:
            rlog.line("error", text)
        raise
    finally:
        if ctx is not None and compression_was_on:
            try:
                ctx.ops.set_fits_compression(True)
            except Exception as exc:  # best effort, like the working folder below
                ctx.warn(f"could not turn Siril's FITS compression back on ({exc})")
        if ctx is not None and saved_wd is not None:
            try:
                ctx.ops.cd(saved_wd)
            except Exception as exc:  # the cwd is best effort; the run's result does not depend on it
                ctx.warn(f"could not restore the Siril working folder ({exc})")
        keep = failure is not None and not isinstance(failure, RunCancelled) and bool(cfg.get("keepWindowsOnError"))
        if work is not None and not keep:
            shutil.rmtree(work, ignore_errors=True)
        report.duration_s = round(time.monotonic() - started, 3)
        report.log_path = rlog.path
        report.warnings = list(rlog.warnings)
        rlog.close()
    return report


def _done_line(cfg: Mapping[str, Any], exports: Sequence[str], seconds: float) -> str:
    """The last line of a successful run: that it is done, how long it took, and what was saved where."""
    minutes, secs = divmod(int(round(seconds)), 60)
    took = f"{minutes} min {secs} s" if minutes else f"{secs} s"
    psb = [path for path in exports if path.lower().endswith(".psb")]
    export_dir = str(cfg.get("exportDir") or "")
    if not psb and cfg.get("exportPsb") and export_dir:
        guess = os.path.join(export_dir, naming.psb_base_name(cfg) + ".psb")
        if os.path.isfile(guess):
            psb = [guess]
    tiffs = len(exports) - len(psb)
    saved = ""
    if psb:
        saved = f" Saved the PSB: {psb[0]}"
        if tiffs:
            saved += f" and {tiffs} TIFF file(s)"
    elif exports:
        saved = f" Saved {tiffs} TIFF file(s) to {os.path.dirname(exports[0])}"
    return f"Loom is done in {took}.{saved}"


def _wrap_logs(ctx: RunContext, rlog: _RunLog) -> tuple[Callable[[str], None], Callable[[str], None],
                                                        Callable[[str], None]]:
    """Log, warn and error callbacks that write every line to the run log and then to the context's callbacks."""
    log0, warn0, error0 = ctx.log, ctx.warn, ctx.error

    def log(text: str) -> None:
        short = _short(text)
        rlog.line("log", short)
        log0(short)

    def warn(text: str) -> None:
        short = _short(text)
        rlog.line("warn", short)
        warn0(short)

    def error(text: str) -> None:
        short = _short(text)
        rlog.line("error", short)
        error0(short)

    return log, warn, error
