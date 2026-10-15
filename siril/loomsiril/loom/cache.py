"""Stage-cache keys and the file-based cache store (Loom's ``lib/Cache.js`` and the key logic of ``lib/Pipeline.js``).

Keys are byte-identical to Loom's for the same inputs: the same SHA-1 of the same JavaScript-style strings
(``Cache.hash``, ``Cache.paramsString``, ``Cache.chainKey``, the source keys of the pipeline). The store keeps each
stage's image as FITS (``<key>.fits``) where Loom keeps XISF; the keys do not change with the container.

Decisions that Loom takes from the cache are ported as pure functions (``resolve_cache_hit``,
``CacheStore.entry_complete``) so they can be checked against Loom's own results.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Optional

import numpy as np

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.jsmath import json_string, number_str

__all__ = [
    "FORMAT_VERSION",
    "STAGE_ORDER",
    "STAGE_COMPANIONS",
    "LOW_SPACE_BYTES",
    "CacheStore",
    "build_stage_keys",
    "chain_key",
    "clean_white_balance_key",
    "crop_halos_tag",
    "crop_tag",
    "default_cache_dir",
    "file_source_key",
    "folder_missing",
    "hash_str",
    "iso_ms",
    "js_json_stringify",
    "lstars_source_key",
    "palette_source_key",
    "params_string",
    "resolve_cache_hit",
    "rgb_source_key",
]

# Cache.js:27 -- bumped to invalidate every cached entry after a behaviour change.
FORMAT_VERSION = "1"

# Pipeline.js:280-290 -- the stage order of a channel's chain.
STAGE_ORDER: tuple[str, ...] = (
    "solve", "spfc", "mgc", "graxpert",
    "aberration", "register",
    "combine", "solveRGB", "spfcRGB", "spccRGB",
    "sharpenRGB", "extractRGB", "denoiseLinearRGB",
    "stretchRGB", "denoiseRGB",
    "paletteCombine", "paletteSpcc", "paletteNorm",
    "paletteSharpen", "paletteExtract",
    "paletteDenoiseLinear", "paletteStretch",
    "paletteDenoise",
    "extractL", "denoiseLinearL", "stretchL",
    "denoiseL",
)

# Pipeline.js:733-739 -- the second image a stage stores under its own key.
STAGE_COMPANIONS: dict[str, str] = {
    "extractL": "stars",
    "extractRGB": "stars",
    "paletteExtract": "stars",
    "stretchRGB": "linear",
    "paletteStretch": "linear",
}

# Cache.js:295 -- below this many free bytes the cache's drive is short of room.
LOW_SPACE_BYTES = 20 * 1024**3

_ARRAY_INDEX = re.compile(r"(0|[1-9][0-9]*)\Z")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


# ---------------------------------------------------------------------------
# JavaScript text of values
# ---------------------------------------------------------------------------


def _js_str(value: Any) -> str:
    """``String(value)`` for a value of a parameter object: None is "null", a bool "true"/"false"."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return number_str(float(value))
    return str(value)


def _is_object(value: Any) -> bool:
    """A JS object or array: what ``typeof v == "object"`` admits once null is set aside."""
    return isinstance(value, (Mapping, list, tuple))


def _js_key_order(keys: Sequence[str]) -> list[str]:
    """Own enumeration order of a JS object: array-index keys ascending first, then the rest in insertion order."""
    indices = sorted((k for k in keys if _ARRAY_INDEX.match(k) and int(k) < 2**32 - 1), key=int)
    index_set = set(indices)
    return indices + [k for k in keys if k not in index_set]


def js_json_stringify(value: Any) -> str:
    """``JSON.stringify`` of a dict (insertion order), list, str, bool, None, int or float.

    A non-finite number is ``null`` and ``-0`` is ``0``, as in JS. Mappings follow the JS key order, so an
    integer-like key comes first in ascending order.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        x = float(value)
        return number_str(x) if np.isfinite(x) else "null"
    if isinstance(value, str):
        return json_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(js_json_stringify(v) for v in value) + "]"
    if isinstance(value, Mapping):
        items = {str(k): v for k, v in value.items()}
        keys = _js_key_order(list(items))
        return "{" + ",".join(json_string(k) + ":" + js_json_stringify(items[k]) for k in keys) + "}"
    raise TypeError("js_json_stringify: unsupported type %s" % type(value).__name__)


def _utf8(text: str) -> bytes:
    """UTF-8 bytes of a string, with a lone surrogate written as U+FFFD (as JS's encoder does)."""
    return _LONE_SURROGATE.sub("�", text).encode("utf-8")


def hash_str(text: str) -> str:
    """``Cache.hash``: the SHA-1 hex digest of the UTF-8 bytes of ``str(text)``."""
    return hashlib.sha1(_utf8(str(text))).hexdigest()


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def params_string(params: Optional[Mapping[str, Any]]) -> str:
    """``Cache.paramsString``: ``key=value`` pairs joined by ``;``, keys sorted by UTF-16 code units.

    A nested object or array is written as JSON; any other value as JS ``String(value)``.
    """
    if params is None:
        return ""
    keys = sorted(params.keys(), key=lambda k: str(k).encode("utf-16-be", "surrogatepass"))
    parts = []
    for k in keys:
        v = params[k]
        text = js_json_stringify(v) if _is_object(v) else _js_str(v)
        parts.append(str(k) + "=" + text)
    return ";".join(parts)


def chain_key(previous_key: Optional[str], stage: str, params: Any) -> str:
    """``Cache.chainKey``: the key of a stage from the previous stage's key (None or "" starts a chain)."""
    prev = previous_key if previous_key else ""
    return hash_str(FORMAT_VERSION + "|" + str(prev) + "|" + str(stage) + "|" + params_string(params))


def iso_ms(mtime_seconds: float) -> str:
    """``Date#toISOString`` of a file time in seconds: UTC, milliseconds truncated (a JS Date holds whole ms).

    The time is read from its shortest decimal form, so ``1700000000.123`` is .123 and not .122 from float error.
    """
    x = float(mtime_seconds)
    if not np.isfinite(x):
        raise ValueError("iso_ms: the time is not finite")
    ms = int(Decimal(repr(x)) * 1000)
    when = datetime(1970, 1, 1) + timedelta(milliseconds=ms)
    return "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ" % (
        when.year, when.month, when.day, when.hour, when.minute, when.second, when.microsecond // 1000)


def file_source_key(path: str, size: int, mtime_seconds: float) -> str:
    """``Cache.fingerprintFile``: the identity of a source file (path, size and modification time)."""
    return hash_str("file|" + path + "|" + _js_str(size) + "|" + iso_ms(mtime_seconds))


def crop_tag(common: Mapping[str, Any]) -> str:
    """``Pipeline.cropTag``: the crop's part of a source key."""
    coords = [_js_str(common[k]) if k in common else "undefined" for k in ("x0", "y0", "x1", "y1")]
    return "|crop:" + ",".join(coords)


def crop_halos_tag(common: Mapping[str, Any], reduce_halos: bool) -> str:
    """``Pipeline.cropHalosTag``: the crop and the halo-matching switch."""
    return crop_tag(common) + "|halos:" + ("1" if reduce_halos else "0")


#: Name of the quantum-efficiency curve a colour run keys on (``Util.IDEAL_QE_CURVE_NAME``).
IDEAL_QE_CURVE_NAME = "Ideal QE curve"


def clean_white_balance_key(clean_keys: Mapping[str, Optional[str]], common: Mapping[str, Any],
                            filters: Optional[Mapping[str, Any]], lum_instrume: Optional[str],
                            ref_key: Optional[str], osc_sensor: Optional[Mapping[str, Any]] = None,
                            calibration: Optional[str] = None) -> str:
    """``Pipeline.cleanWhiteBalanceKey``: the key of the white-balance measurement.

    ``ref_key`` is the registration reference's cache key (None is hashed as "null"). A colour run passes its sensor
    choice as ``osc_sensor`` (``{}`` when none is set) and is keyed on that and the ideal QE curve, not on the filters;
    a mono run passes None and is keyed on its filters and instrument. ``calibration`` is the Siril calibration
    identity (``stages_colour.calibration_identity``): None gives Loom's key byte for byte, anything else is appended
    and changes the key. Raises ValueError when one of the R, G, B channels has no pre-correction key.
    """
    for ch in ("R", "G", "B"):
        if not clean_keys.get(ch):
            raise ValueError("no pre-correction cache key for " + ch)
    if osc_sensor is not None:
        response = "|osc:" + js_json_stringify(osc_sensor if osc_sensor else {}) + "|qe:" + IDEAL_QE_CURVE_NAME
    else:
        response = "|" + js_json_stringify(filters if filters else {}) + "|" + _js_str(lum_instrume)
    text = ("cleanwb|" + _js_str(clean_keys["R"]) + "|" + _js_str(clean_keys["G"]) + "|" + _js_str(clean_keys["B"]) +
            crop_tag(common) + response + "|ref:" + _js_str(ref_key))
    if calibration is not None:
        text += "|calibration:" + calibration
    return hash_str(text)


def rgb_source_key(current_keys: Mapping[str, str], common: Mapping[str, Any], reduce_halos: bool) -> str:
    """The source key of the RGB composite (Pipeline.js:1608-1610)."""
    return hash_str("rgb|" + _js_str(current_keys["R"]) + "|" + _js_str(current_keys["G"]) + "|" +
                    _js_str(current_keys["B"]) + crop_halos_tag(common, reduce_halos))


def palette_source_key(palette: str, keys3: Sequence[str], common: Mapping[str, Any], reduce_halos: bool) -> str:
    """The source key of a palette (Pipeline.js:1487-1491): the palette name and its three channels, in map order."""
    if len(keys3) != 3:
        raise ValueError("palette_source_key needs three channel keys")
    return hash_str("palette|" + palette + "|" + _js_str(keys3[0]) + "|" + _js_str(keys3[1]) + "|" +
                    _js_str(keys3[2]) + crop_halos_tag(common, reduce_halos))


def lstars_source_key(l_key: str, common: Mapping[str, Any], reduce_halos: bool) -> str:
    """The source key of the L (stars) chain (Pipeline.js:2443)."""
    return hash_str("lstars|" + _js_str(l_key) + crop_halos_tag(common, reduce_halos))


def build_stage_keys(source_key: Optional[str], params: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``Pipeline.buildStageKeys``: the chain of ``{stage, key, params}`` (plus ``companion``), in pipeline order.

    A stage absent from ``params`` is skipped. The linear companion of stretchRGB and paletteStretch is kept only
    when the stage's params say ``keepLinear``.
    """
    chain: list[dict[str, Any]] = []
    prev = source_key
    if params is None:
        return chain
    for stage in STAGE_ORDER:
        if stage not in params:
            continue
        p = params[stage]
        key = chain_key(prev, stage, p)
        entry: dict[str, Any] = {"stage": stage, "key": key, "params": p}
        comp: Optional[str] = STAGE_COMPANIONS.get(stage)
        if comp == "linear" and not (_is_object(p) and p.get("keepLinear")):
            comp = None
        if comp:
            entry["companion"] = comp
        chain.append(entry)
        prev = key
    return chain


def resolve_cache_hit(
    chain: Sequence[Mapping[str, Any]],
    lookups: Sequence[bool],
    entry_complete: Callable[[Mapping[str, Any]], bool],
    load: Callable[[Mapping[str, Any]], Any],
    load_companion: Callable[[Mapping[str, Any]], Any],
) -> tuple[int, Any, Any]:
    """``Pipeline.resolveCacheHit``: the latest stage to restart from, as ``(index, loaded, companion)``.

    Walks the chain from the end. An entry is used when its lookup succeeded and it is complete; a loaded entry
    with a companion also needs the companion to load. A failed load falls back to an earlier entry. Returns
    ``(-1, None, None)`` when every stage must run. The caller owns (and releases) what was loaded.
    """
    for i in range(len(chain) - 1, -1, -1):
        entry = chain[i]
        if not lookups[i] or not entry_complete(entry):
            continue
        loaded = load(entry)
        if loaded is None:
            continue
        if entry.get("companion") is None:
            return (i, loaded, None)
        stars = load_companion(entry)
        if stars is not None:
            return (i, loaded, stars)
    return (-1, None, None)


def default_cache_dir(platform: str, env: Mapping[str, str], home: str) -> str:
    """The per-user folder for the cache, never the system temp folder.

    macOS: ``<home>/Library/Application Support/loomsiril/cache``; Windows: ``%LOCALAPPDATA%\\loomsiril\\cache``
    (``<home>\\AppData\\Local`` when unset); elsewhere ``$XDG_DATA_HOME/loomsiril/cache`` or
    ``<home>/.local/share/loomsiril/cache``.
    """
    if platform == "darwin":
        return home + "/Library/Application Support/loomsiril/cache"
    if platform == "win32":
        base = env.get("LOCALAPPDATA") or (home + "\\AppData\\Local")
        return base + "\\loomsiril\\cache"
    xdg = env.get("XDG_DATA_HOME") or ""
    base = xdg if xdg.startswith("/") else home + "/.local/share"
    return base + "/loomsiril/cache"


def folder_missing(chosen: Optional[str]) -> bool:
    """True when a CHOSEN cache folder does not exist. An empty choice is the default folder, created on demand."""
    path = (chosen or "").strip()
    return bool(path) and not os.path.isdir(path)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def _noop_log(level: str, text: str) -> None:
    return None


def _mismatch(saved: Sequence[np.ndarray], back: Sequence[np.ndarray]) -> Optional[str]:
    """Why a re-read stack differs from the one saved (its width, height and channel count), or None."""
    h, w = saved[0].shape[-2], saved[0].shape[-1]
    bh = back[0].shape[-2] if back else 0
    bw = back[0].shape[-1] if back else 0
    if len(back) != len(saved) or (bw, bh) != (w, h):
        return "it reads back as %dx%dx%d, not the %dx%dx%d that was saved" % (
            bw, bh, len(back), w, h, len(saved))
    return None


class CacheStore:
    """One cache folder: each stage result as ``<key>.fits``, companions and small JSON sidecars beside it.

    Entries are written to ``<key>.fits.part`` and read back before they are published with ``os.replace``, so an
    entry in the folder is one that was written in full and reads back. ``log(level, text)`` receives the warnings.
    """

    def __init__(self, folder: str, log: Optional[Callable[[str, str], None]] = None) -> None:
        self.folder = folder
        self._log = log if log is not None else _noop_log

    # -- paths -------------------------------------------------------------

    def path_for(self, key: str) -> str:
        return os.path.join(self.folder, key + ".fits")

    def meta_path_for(self, key: str) -> str:
        return os.path.join(self.folder, key + ".json")

    def companion_path_for(self, key: str, name: str) -> str:
        return os.path.join(self.folder, key + "." + name + ".fits")

    def wb_path_for(self, key: str) -> str:
        return os.path.join(self.folder, key + ".wb.json")

    # -- entries -----------------------------------------------------------

    def lookup(self, key: str) -> Optional[str]:
        path = self.path_for(key)
        return path if os.path.isfile(path) else None

    def store(self, key: str, planes: Sequence[np.ndarray], header: Mapping[str, Any],
              meta: Optional[Mapping[str, Any]]) -> Optional[str]:
        path = self.path_for(key)
        if not self._write_verified(path, key[:12], planes, header):
            return None
        try:
            with open(self.meta_path_for(key), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(meta) if meta else {}, indent=2) + "\n")
        except (OSError, TypeError, ValueError):
            pass  # the sidecar is for people; its absence is not fatal
        return path

    def load(self, key: str) -> Optional[tuple[list[np.ndarray], dict[str, Any]]]:
        path = self.lookup(key)
        if path is None:
            return None
        return self._read_verified(path, key[:12])

    def store_companion(self, key: str, name: str, planes: Sequence[np.ndarray],
                        header: Mapping[str, Any]) -> Optional[str]:
        path = self.companion_path_for(key, name)
        if not self._write_verified(path, key[:12] + "." + name, planes, header):
            return None
        return path

    def load_companion(self, key: str, name: str) -> Optional[tuple[list[np.ndarray], dict[str, Any]]]:
        path = self.companion_path_for(key, name)
        if not os.path.isfile(path):
            return None
        return self._read_verified(path, key[:12] + "." + name)

    def entry_complete(self, entry: Optional[Mapping[str, Any]]) -> bool:
        """``Pipeline.cacheEntryComplete``: the stage result is present and, when it has a companion, so is that."""
        if entry is None or self.lookup(entry["key"]) is None:
            return False
        companion = entry.get("companion")
        if companion is None:
            return True
        return os.path.isfile(self.companion_path_for(entry["key"], companion))

    # -- white balance (a few numbers as JSON) ------------------------------

    def read_wb(self, key: str) -> Optional[list[float]]:
        path = self.wb_path_for(key)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            self._log("warn", "unreadable white balance entry (%s)" % exc)
            return None
        if isinstance(data, list) and len(data) == 3 and all(isinstance(x, (int, float)) for x in data):
            return [float(x) for x in data]
        return None

    def write_wb(self, key: str, factors: Sequence[float]) -> None:
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(self.wb_path_for(key), "w", encoding="utf-8") as fh:
                fh.write(json.dumps([float(x) for x in factors]) + "\n")
        except OSError as exc:
            self._log("warn", "could not write the white balance entry (%s)" % exc)

    # -- the folder ---------------------------------------------------------

    def total_bytes(self) -> int:
        """Bytes of the files directly in the folder (subfolders, such as logs/, are not counted)."""
        if not os.path.isdir(self.folder):
            return 0
        total = 0
        with os.scandir(self.folder) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
        return total

    def clear(self) -> int:
        """Deletes the files directly in the folder and returns the bytes freed. Subfolders survive."""
        if not os.path.isdir(self.folder):
            return 0
        freed = 0
        with os.scandir(self.folder) as it:
            entries = [e for e in it if e.is_file(follow_symlinks=False)]
        for entry in entries:
            try:
                size = entry.stat(follow_symlinks=False).st_size
                os.remove(entry.path)
                freed += size
            except OSError:
                pass  # left in place; the count is best effort
        return freed

    def free_bytes(self) -> Optional[int]:
        """Free bytes on the drive the folder is (or will be) on: the nearest existing parent is asked."""
        probe = os.path.abspath(self.folder)
        while not os.path.isdir(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        try:
            return int(shutil.disk_usage(probe).free)
        except OSError:
            return None

    def low_space(self) -> Optional[int]:
        """The free bytes when the drive is below LOW_SPACE_BYTES, else None."""
        free = self.free_bytes()
        if free is not None and free < LOW_SPACE_BYTES:
            return free
        return None

    # -- internals ----------------------------------------------------------

    def _write_verified(self, path: str, label: str, planes: Sequence[np.ndarray],
                        header: Mapping[str, Any]) -> bool:
        """Writes ``path`` through ``<path>.part``, reads it back and publishes it only when the shape matches."""
        part = path + ".part"
        reason: Optional[str]
        try:
            os.makedirs(self.folder, exist_ok=True)
            write_fits(part, list(planes), dict(header), bitpix=-32)
            back, _back_header = read_fits(part)
            reason = _mismatch(planes, back)
        except Exception as exc:  # any failure to write or re-read is a discarded entry
            reason = str(exc) or type(exc).__name__
        if reason is None:
            try:
                os.replace(part, path)
                return True
            except OSError as exc:
                reason = str(exc) or type(exc).__name__
        _remove_quietly(part)
        self._log("error", "%s was written but will not read back (%s); discarding it rather than leaving a "
                           "broken entry for the next run" % (label, reason))
        return False

    def _read_verified(self, path: str, label: str) -> Optional[tuple[list[np.ndarray], dict[str, Any]]]:
        try:
            planes, header = read_fits(path)
            return planes, header
        except Exception as exc:  # an unreadable entry is deleted so the next run recomputes it
            _remove_quietly(path)
            self._log("warn", "%s is in the cache but would not read (%s); deleted it" % (label, exc))
            return None


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
