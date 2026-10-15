"""Frame Selector options remembered between runs: ``loomsiril.frames_options``.

Ported from ``pixinsight/script/lib/MasterFlat.js`` lines 55-77 (``MasterFlat.loadSettings`` and
``MasterFlat.saveSettings``, whose key names and fallbacks are kept by :func:`loomsiril.master_flat.load_settings` and
:func:`loomsiril.master_flat.save_settings`, called here unchanged) and from ``pixinsight/script/lib/Frames.js`` lines
474-500 (``Frames.defaultSettings``, reached through :func:`loomsiril.frames.default_settings`), 502-508
(``Frames.copyOf``, reached through :func:`loomsiril.frames.apply_preset`, which copies) and 520-538
(``Frames.applyPreset``). The dialog's use of these is ``FrameSelector.js`` 3573-3575 (``setMasterFlatSettings``, which
saves the darks folder and the master-flats box) and 5051-5053 (the remembered values read when the dialog starts).

Deviation from the JavaScript: this port deliberately remembers more than the JavaScript does. PixInsight remembers
only the darks folder and the master-flats box (the two ``Loom/...`` Settings keys). This port also remembers, between
runs, the source and destination folders, the preset, k, the weights of the score and the criteria (the gating of each
metric and its typed limits).

A second deviation: the preset and k are remembered once, for the whole session of folders, and applied to every new
channel. PixInsight keeps them per channel for the length of the session, so a channel's preset and k are lost when the
dialog closes. Here the last values chosen are what every new channel starts with; a channel's own k still counts as
edited only when it was edited by hand (kEdited), as in the JavaScript.

Storage: :class:`OptionsStore` is a JSON file, ``loomsiril-frame-selector.json`` in the Siril configuration folder
(``siril.get_siril_configdir()``, as loomsiril-fly-drafts is kept). The darks folder and the master-flats box are
stored under the same keys as the PixInsight Settings (``Loom/importDarksFolder``, ``Loom/importMasterFlatsOnly``), so
:mod:`loomsiril.master_flat` reads and writes them through the store unchanged. The rest is one object under the key
``FrameSelector``, which carries ``"version": 1``. A missing, unreadable or corrupt file reads as the defaults; an object
of another or unknown version (including one without a version) reads as the defaults with a warning; a bad value is
dropped on its own and the rest is kept. Writes are atomic (a temporary file in the same folder, then ``os.replace``); a failed write logs a
warning and does not raise.

Names are the JS names in snake_case. Keys in the stored object follow the JS camelCase (``sourceFolder``), and the
criteria keep the metric names of :mod:`loomsiril.frames` (``psfSNR``, ``fwhm``, ``eccentricity``, ``stars``).

Pure apart from the file: no Siril call, no display. The functions here that have no JavaScript counterpart
(:class:`OptionsStore`, :class:`FrameSelectorOptions`, :func:`load`, :func:`save`, :func:`remember_from_state`) are
additions of the Siril port that carry the remembered values; their behaviour is the one stated above.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional, Union

from loomsiril import frames
from loomsiril import master_flat as mf

_log = logging.getLogger(__name__)

FILE_NAME = "loomsiril-frame-selector.json"
SECTION = "FrameSelector"

FORMAT_VERSION = 1

DATA_TYPE_STRING = mf.DATA_TYPE_STRING
DATA_TYPE_BOOLEAN = mf.DATA_TYPE_BOOLEAN

Band = dict[str, float]


def _finite_number(v: Any) -> Optional[float]:
    """A finite JSON number as a float; anything else (bool, text, null, NaN, overflow) is None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _clean_preset(v: Any) -> str:
    return v if isinstance(v, str) and v in frames.PRESET_NAMES else frames.DEFAULT_PRESET


def _clean_k(v: Any) -> Optional[float]:
    """k as remembered: a positive finite number, or None for the preset's own k."""
    f = _finite_number(v)
    return f if f is not None and f > 0 else None


def _clean_weights(v: Any) -> dict[str, float]:
    """The score weights: every metric, each a finite number; a bad or missing one is the default."""
    out = dict(frames.DEFAULT_WEIGHTS)
    if isinstance(v, dict):
        for metric, w in v.items():
            if metric in frames.METRICS:
                f = _finite_number(w)
                if f is not None:
                    out[metric] = f
    return {m: out[m] for m in frames.METRICS}


def _known_version(v: Any) -> bool:
    return type(v) is int and v == FORMAT_VERSION


def _clean_gating(v: Any) -> dict[str, bool]:
    out = dict(frames.DEFAULT_GATING)
    if isinstance(v, dict):
        for metric, on in v.items():
            if metric in frames.METRICS and isinstance(on, bool):
                out[metric] = on
    return {m: out[m] for m in frames.METRICS}


def _clean_limits(v: Any) -> dict[str, Band]:
    out: dict[str, Band] = {}
    if not isinstance(v, dict):
        return out
    for metric, entry in v.items():
        if metric not in frames.METRICS or not isinstance(entry, dict):
            continue
        band: Band = {}
        for side in ("lo", "hi"):
            bound = _finite_number(entry.get(side))
            if bound is not None:
                band[side] = bound
        if band:
            out[metric] = band
    return out


def _clean_text(v: Any) -> str:
    return v if isinstance(v, str) else ""


class OptionsStore:
    """``{ read(key, type), write(key, type, value) }`` over a JSON file, plus raw values for the options object.

    The file is read once, when the store is made. ``read`` returns None for a key that is absent or of another type,
    as the PixInsight Settings do for a missing key. ``write`` updates the copy in memory and then the file; a failed
    write warns and leaves the file as it was.
    """

    def __init__(self, config_dir: Union[str, "os.PathLike[str]"]) -> None:
        self.path = Path(config_dir) / FILE_NAME
        self._values = _read_file(self.path)

    def read(self, key: str, data_type: str) -> Any:
        value = self._values.get(key)
        if data_type == DATA_TYPE_STRING:
            return value if isinstance(value, str) else None
        if data_type == DATA_TYPE_BOOLEAN:
            return value if isinstance(value, bool) else None
        return None

    def write(self, key: str, data_type: str, value: Any) -> None:
        if data_type == DATA_TYPE_BOOLEAN:
            self._values[key] = bool(value)
        else:
            self._values[key] = value if isinstance(value, str) else ""
        self._flush()

    def read_value(self, key: str) -> Any:
        """The stored value under ``key`` as it is (any JSON type), or None."""
        return self._values.get(key)

    def write_value(self, key: str, value: Any) -> None:
        self._values[key] = value
        self._flush()

    def _flush(self) -> None:
        tmp: Optional[str] = None
        try:
            folder = self.path.parent
            folder.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(folder), prefix=FILE_NAME + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._values, f, indent=2, sort_keys=True, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            tmp = None
        except (OSError, TypeError, ValueError) as e:
            _log.warning("Frame Selector options were not saved to %s: %s", self.path, e)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def _read_file(path: Path) -> dict[str, Any]:
    """The stored object, or {} when the file is missing, unreadable, corrupt or not an object."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        _log.warning("Frame Selector options in %s could not be read; using the defaults: %s", path, e)
        return {}
    if not isinstance(data, dict):
        _log.warning("Frame Selector options in %s are not an object; using the defaults", path)
        return {}
    return data


@dataclass
class FrameSelectorOptions:
    """Everything the dialog remembers between runs. Defaults are the dialog's own (``frames`` defaults)."""

    preset: str = frames.DEFAULT_PRESET
    k: Optional[float] = None
    gating: dict[str, bool] = field(default_factory=lambda: dict(frames.DEFAULT_GATING))
    limits: dict[str, Band] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=lambda: dict(frames.DEFAULT_WEIGHTS))
    source_folder: str = ""
    destination_folder: str = ""
    darks_folder: str = ""
    only_master_flats: bool = False


def load(store: OptionsStore) -> FrameSelectorOptions:
    """Reads the remembered options. Any value of the wrong type, or an unknown metric, is dropped on its own."""
    raw = store.read_value(SECTION)
    if raw is None:
        raw = {}
    elif not isinstance(raw, dict) or not _known_version(raw.get("version")):
        _log.warning("Frame Selector options were written by another version; using the defaults")
        raw = {}
    remembered = mf.load_settings(store)
    return FrameSelectorOptions(
        preset=_clean_preset(raw.get("preset")),
        k=_clean_k(raw.get("k")),
        gating=_clean_gating(raw.get("gating")),
        limits=_clean_limits(raw.get("limits")),
        weights=_clean_weights(raw.get("weights")),
        source_folder=_clean_text(raw.get("sourceFolder")),
        destination_folder=_clean_text(raw.get("destinationFolder")),
        darks_folder=remembered["darksFolder"],
        only_master_flats=remembered["onlyMasterFlats"],
    )


def save(store: OptionsStore, options: FrameSelectorOptions) -> None:
    """Writes the options; values are cleaned the way :func:`load` reads them, so a save and a load agree."""
    store.write_value(SECTION, {
        "version": FORMAT_VERSION,
        "preset": _clean_preset(options.preset),
        "k": _clean_k(options.k),
        "gating": _clean_gating(options.gating),
        "limits": _clean_limits(options.limits),
        "weights": _clean_weights(options.weights),
        "sourceFolder": _clean_text(options.source_folder),
        "destinationFolder": _clean_text(options.destination_folder),
    })
    mf.save_settings(store, options.darks_folder, options.only_master_flats)


def settings_for_new_channel(options: FrameSelectorOptions) -> dict[str, Any]:
    """The settings of a channel the dialog has not seen yet: the defaults, with the remembered preset, k, criteria.

    k is the preset's unless it was remembered as edited by hand, in which case kEdited is True. Gating and limits are
    new objects, so two channels never share a knob (as ``Frames.applyPreset`` copies them).
    """
    preset = _clean_preset(options.preset)
    settings = frames.apply_preset(frames.default_settings(), preset)
    k = _clean_k(options.k)
    if k is not None:
        settings["k"] = k
        settings["kEdited"] = True
    settings["gating"] = _clean_gating(options.gating)
    settings["limits"] = {m: dict(band) for m, band in _clean_limits(options.limits).items()}
    settings["weights"] = _clean_weights(options.weights)
    return settings


def remember_from_state(
    options: FrameSelectorOptions,
    settings: dict[str, Any],
    *,
    source_folder: Optional[str] = None,
    destination_folder: Optional[str] = None,
    darks_folder: Optional[str] = None,
    only_master_flats: Optional[bool] = None,
) -> FrameSelectorOptions:
    """What the dialog remembers on close, as a new options object. ``settings`` is the current channel's settings.

    The preset, the criteria, the limits and the weights come from the channel. k is kept only when the channel's k was edited by
    hand (kEdited); otherwise the preset decides it next time. A folder or the master-flats box that is passed as None
    keeps the value in ``options``. Nothing is written: :func:`save` does that.
    """
    return replace(
        options,
        preset=_clean_preset(settings.get("preset")),
        k=_clean_k(settings.get("k")) if settings.get("kEdited") else None,
        gating=_clean_gating(settings.get("gating")),
        limits=_clean_limits(settings.get("limits")),
        weights=_clean_weights(settings.get("weights")),
        source_folder=options.source_folder if source_folder is None else _clean_text(source_folder),
        destination_folder=(options.destination_folder if destination_folder is None
                            else _clean_text(destination_folder)),
        darks_folder=options.darks_folder if darks_folder is None else _clean_text(darks_folder),
        only_master_flats=(options.only_master_flats if only_master_flats is None
                           else bool(only_master_flats)),
    )
