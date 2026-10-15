"""Master flats: the decision part of ``pixinsight/script/lib/MasterFlat.js``, lines 1-447, ported.

Covered, by JS line range: the constants (28-41); the settings store protocol, load, save and ``active``
(43-90; the PixInsight ``Settings`` binding of 47-52 is replaced by the :class:`SettingsStore` the caller passes in);
``number`` and ``describe`` (91-122); the dark classification by IMAGETYP and by name, and ``darksOf`` (123-165);
the darks on the card (167-285: ``cardDarks``, ``cardDarkMatch``, ``readCardDarks``, ``darkSourceOf``,
``cardDarksUi``); matching (286-391: ``sameExposure``, ``compatible``, ``darkSuits``, ``chooseDark``, ``rawGroupKey``);
names and rejection, the integration specs and the dark cache path (392-443).

Pure: no file, no display, no Siril call. Header reads take the reader as an argument, ``read_header(path)``
returning an object with ``info`` (None when unreadable) and ``keyword(name)``; the card's file size and time are
read through ``file_info`` (default: ``os.stat``), which is how ``FileInfo`` is read in PixInsight. Every
function is pinned to the JavaScript by the golden cases in ``tests/oracle/fs_master_flat.json``, recorded by
``tools/oracle/fs_master_flat.js`` through tools/record_oracle.js from the real MasterFlat.js.

Names are the JS names in snake_case; dict keys keep the JS camelCase (``darksFolder``, ``onlyMasterFlats``,
``rejectionNormalization``) so a settings object or a spec reads the same on both sides. JS ``undefined`` is
``None`` here; where a JS object would drop an undefined key, this returns ``None`` for it.

Two Siril differences, each a parameter whose default is the Siril value; the JS value is reproduced by passing
``ext=".xisf"``, which the oracle cases do:

* ``file_name(filter_name, binning, ext=".fit")``: the JS writes ``.xisf``.
* ``dark_cache_path(group, sources, cache_dir, ext=".fit")``: the JS writes ``.xisf``.

Out of this module, by design: the plan, the execution and the engine (MasterFlat.js 449 on).
"""

from __future__ import annotations

import math
import os
import re
from functools import cmp_to_key
from typing import Any, Callable, Optional, Protocol

from loomsiril.jsmath import is_falsy, js_max, js_round, js_string, parse_float
from loomsiril.loom.cache import hash_str, iso_ms

# MasterFlat.js 28-41
SETTINGS_DARKS = "Loom/importDarksFolder"
SETTINGS_ONLY = "Loom/importMasterFlatsOnly"

# Frames of one kind needed before they are worth integrating.
MIN_FLATS = 2
MIN_DARKS = 2

# Exposure: 1% of the longer one, never less than a millisecond.
EXPOSURE_REL = 0.01
EXPOSURE_ABS = 0.001
# Sensor temperature, degrees C. Both must state it: a missing one is not a match.
TEMP_TOLERANCE = 3
# Gain, compared only when both state it.
GAIN_TOLERANCE = 0.5

# The PixInsight DataType_* arguments of the store, by name.
DATA_TYPE_BOOLEAN = "DataType_Boolean"
DATA_TYPE_STRING = "DataType_String"

# JS String.prototype.trim: WhiteSpace and LineTerminator code points.
_JS_WHITESPACE = (
    "\t\n\x0b\x0c\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)

# A file in the darks folder: the image extensions (MasterFlat.js IMAGE_EXTENSION, JS `$` is the end of input).
IMAGE_EXTENSION = re.compile(r"\.(xisf|fits?|fts)\Z", re.IGNORECASE | re.ASCII)

_NUMBER_JUNK = re.compile(r"[^0-9eE+.\-]")
_IMAGETYP_NOT_DARK = re.compile(r"flat|light|bias|offset|zero", re.IGNORECASE | re.ASCII)
_NAME_NOT_DARK = re.compile(r"flat|light|bias|offset", re.IGNORECASE | re.ASCII)
_DARK = re.compile(r"dark", re.IGNORECASE | re.ASCII)
_MASTER = re.compile(r"master", re.IGNORECASE | re.ASCII)
_MASTER_START = re.compile(r"^master", re.IGNORECASE | re.ASCII)
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_NAME_EDGE = re.compile(r"^[_.]+|[_.]+$")


class SettingsStore(Protocol):
    """``{ read(key, type), write(key, type, value) }``, as Config.pixinsightStore().settings in the JS.

    ``data_type`` is ``DATA_TYPE_STRING`` or ``DATA_TYPE_BOOLEAN``. A store may raise; the load and the save
    treat a raise as a value they cannot have, as the JS does.
    """

    def read(self, key: str, data_type: str) -> Any: ...

    def write(self, key: str, data_type: str, value: Any) -> None: ...


ReadHeader = Callable[[str], Any]
FileInfo = Callable[[str], tuple[Optional[int], Optional[str]]]


def _truthy(v: Any) -> bool:
    """JS truthiness: null, undefined, false, 0, NaN and '' are falsy; everything else is truthy."""
    if v is None or v is False:
        return False
    if isinstance(v, str):
        return len(v) > 0
    if isinstance(v, (int, float)):
        return not (v == 0 or v != v)
    return True


def _js_trim(text: str) -> str:
    """JS ``String#trim``: the WhiteSpace and LineTerminator characters at both ends."""
    return text.strip(_JS_WHITESPACE)


def _utf16_key(text: str) -> bytes:
    """A string's UTF-16 code units, big-endian: byte order is JS string order (code units, not code points)."""
    return text.encode("utf-16-be", "surrogatepass")


def _cmp_utf16(a: str, b: str) -> int:
    ka, kb = _utf16_key(a), _utf16_key(b)
    return (ka > kb) - (ka < kb)


# ---------------------------------------------------------------------------
# Settings (MasterFlat.js 43-90)
# ---------------------------------------------------------------------------


def load_settings(store: SettingsStore) -> dict[str, Any]:
    """``MasterFlat.loadSettings``: the saved darks folder ("" when none) and the master-flats box (only ``True``)."""
    folder = ""
    only = False
    try:
        f = store.read(SETTINGS_DARKS, DATA_TYPE_STRING)
        if f is not None and len(js_string(f)) > 0:
            folder = js_string(f)
        only = store.read(SETTINGS_ONLY, DATA_TYPE_BOOLEAN) is True
    except Exception:
        pass
    return {"darksFolder": folder, "onlyMasterFlats": only}


def save_settings(store: SettingsStore, darks_folder: Any = None, only_master_flats: Any = None) -> None:
    """``MasterFlat.saveSettings``: the folder as given ("" when falsy), the box as a boolean. A failed write is ignored."""
    try:
        store.write(SETTINGS_DARKS, DATA_TYPE_STRING, darks_folder if _truthy(darks_folder) else "")
        store.write(SETTINGS_ONLY, DATA_TYPE_BOOLEAN, _truthy(only_master_flats))
    except Exception:
        pass


def active(darks_folder: Any = None, only_master_flats: Any = None, card_darks_found: Any = None) -> bool:
    """``MasterFlat.active``: the box is ticked AND there is somewhere to get darks (the card, or a folder)."""
    return _truthy(only_master_flats) and (
        _truthy(card_darks_found) or (darks_folder is not None and len(js_string(darks_folder)) > 0))


# ---------------------------------------------------------------------------
# Describing frames (MasterFlat.js 91-166)
# ---------------------------------------------------------------------------


def number(text: Any) -> Optional[float]:
    """``MasterFlat.number``: the number at the start of the text after dropping every character but digits, e, +, . and -.

    None when there is none, or when it is not finite. ``"1.5e-3s"`` is 0.0015; ``"-10.0 C"`` is -10.
    """
    if text is None:
        return None
    n = parse_float(_NUMBER_JUNK.sub("", js_string(text)))
    return n if math.isfinite(n) else None


def _keyword_or(keyword: Callable[[str], Any], first: str, second: str) -> Any:
    """``keyword(first) != null ? keyword(first) : keyword(second)``."""
    v = keyword(first)
    return v if v is not None else keyword(second)


def describe(path: Any, keyword: Callable[[str], Any]) -> dict[str, Any]:
    """``MasterFlat.describe``: a frame as the plan needs it, from a path and ``keyword(name)``. Missing fields are None."""
    return {
        "path": path,
        "name": js_string(path).split("/")[-1],
        "imagetyp": keyword("IMAGETYP"),
        "filter": keyword("FILTER"),
        "exposure": number(_keyword_or(keyword, "EXPTIME", "EXPOSURE")),
        "binning": number(keyword("XBINNING")),
        "gain": number(keyword("GAIN")),
        "temp": number(_keyword_or(keyword, "CCD-TEMP", "SET-TEMP")),
    }


# ---------------------------------------------------------------------------
# Dark classification (MasterFlat.js 123-165)
# ---------------------------------------------------------------------------


def master_flat_dark_kind_from_imagetyp(t: str, n: str) -> Optional[str]:
    """The kind a non-empty IMAGETYP ``t`` decides; the name ``n`` only says master."""
    if _IMAGETYP_NOT_DARK.search(t) or not _DARK.search(t):
        return None
    return "master" if (_MASTER.search(t) or _MASTER_START.search(n)) else "raw"


def dark_kind(name: Any, imagetyp: Any) -> Optional[str]:
    """``MasterFlat.darkKind``: "master", "raw" or None (not a dark).

    IMAGETYP decides when it is present (quotes dropped, blanks trimmed); the name is the fallback.
    """
    n = js_string(name) if _truthy(name) else ""
    t = _js_trim((js_string(imagetyp) if _truthy(imagetyp) else "").replace("'", ""))
    if len(t) > 0:
        return master_flat_dark_kind_from_imagetyp(t, n)
    if _NAME_NOT_DARK.search(n) or not _DARK.search(n):
        return None
    return "master" if _MASTER_START.search(n) else "raw"


def darks_of(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``MasterFlat.darksOf``: the darks out of described frames, each a copy with ``kind``. Others are dropped."""
    out: list[dict[str, Any]] = []
    for frame in frames:
        kind = dark_kind(frame.get("name"), frame.get("imagetyp"))
        if kind is not None:
            d = dict(frame)
            d["kind"] = kind
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# Darks on the ASIAIR card (MasterFlat.js 167-285)
# ---------------------------------------------------------------------------


def file_info_of(path: str) -> tuple[Optional[int], Optional[str]]:
    """``(size, modified)`` of a file as PixInsight's FileInfo gives them, or ``(None, None)`` when it cannot be read."""
    try:
        st = os.stat(path)
        return int(st.st_size), iso_ms(st.st_mtime)
    except (OSError, ValueError):
        return None, None


def card_darks(frames: list[dict[str, Any]], read_header: ReadHeader,
               file_info: FileInfo = file_info_of) -> list[dict[str, Any]]:
    """``MasterFlat.cardDarks``: the card's darks, described, each with ``size``, ``modified`` and ``source`` "card".

    A frame whose header cannot be read (the card was pulled out) is left out.
    """
    described: list[dict[str, Any]] = []
    for frame in frames:
        h = read_header(frame["path"])
        if h is None or h.info is None:
            continue
        d = describe(frame["path"], h.keyword)
        # size and modified only key the cache; the engine reads them again and refuses a dark that has gone.
        size, modified = file_info(frame["path"])
        d["size"] = size
        d["modified"] = modified
        d["source"] = "card"
        described.append(d)
    return darks_of(described)


def card_dark_match(flats: list[dict[str, Any]], darks: list[dict[str, Any]]) -> dict[str, Any]:
    """``MasterFlat.cardDarkMatch``: does any flat have a dark (``found``); the darks that suit some flat (``used``,
    in the order of ``darks``); their exposures in seconds, ascending (``exposures``)."""
    used: dict[str, bool] = {}
    found = False
    for flat in flats:
        pick = choose_dark(flat, darks)
        if pick is None:
            continue
        found = True
        paths = [pick["path"]] if pick["kind"] == "master" else pick["paths"]
        for p in paths:
            used[p] = True
    used_list: list[dict[str, Any]] = []
    exposures: list[float] = []
    for d in darks:
        if d["path"] not in used:
            continue
        used_list.append(d)
        if d.get("exposure") is not None and d["exposure"] not in exposures:
            exposures.append(d["exposure"])
    exposures.sort()
    return {"found": found, "used": used_list, "exposures": exposures}


def read_card_darks(dark_frames: list[dict[str, Any]], flat_frames: list[dict[str, Any]], read_header: ReadHeader,
                    file_info: FileInfo = file_info_of) -> dict[str, Any]:
    """``MasterFlat.readCardDarks``: ``{darks, match}``, the card's darks and their match against the readable flats."""
    flats: list[dict[str, Any]] = []
    for f in flat_frames:
        h = read_header(f["path"])
        if h is not None and h.info is not None:
            flats.append(describe(f["path"], h.keyword))
    darks = card_darks(dark_frames, read_header, file_info)
    return {"darks": darks, "match": card_dark_match(flats, darks)}


def dark_source_of(match: Optional[dict[str, Any]], darks_folder: Any) -> dict[str, Any]:
    """``MasterFlat.darkSourceOf``: the card (with its count and exposures) when its darks were found, else the folder."""
    if match is not None and _truthy(match.get("found")):
        return {"card": True, "count": len(match["used"]), "exposures": match["exposures"]}
    return {"darksFolder": darks_folder}


def card_darks_ui(found: Any, darks_folder: Any, only_master_flats: Any, editable: Any) -> dict[str, bool]:
    """``MasterFlat.cardDarksUi``: the darks controls as plain data. With card darks the folder controls are hidden
    and the option needs no folder; without, the folder is needed. The saved folder only sets ``optionChecked``."""
    folder = darks_folder is not None and len(js_string(darks_folder)) > 0
    available = _truthy(found) or folder
    return {
        "found": _truthy(found),
        "darksVisible": not _truthy(found),
        "folderEnabled": _truthy(editable),
        "clearEnabled": _truthy(editable) and folder,
        "optionAvailable": _truthy(editable) and available,
        "optionChecked": _truthy(only_master_flats) and available,
    }


# ---------------------------------------------------------------------------
# Matching (MasterFlat.js 286-391)
# ---------------------------------------------------------------------------


def same_exposure(a: Optional[float], b: Optional[float]) -> bool:
    """``MasterFlat.sameExposure``: within 1% of the longer exposure, and at least a millisecond. False when either is None."""
    if a is None or b is None:
        return False
    return abs(a - b) <= js_max(EXPOSURE_ABS, EXPOSURE_REL * js_max(a, b))


def compatible(a: Optional[float], b: Optional[float], tolerance: float) -> bool:
    """``MasterFlat.compatible``: equal within the tolerance, or not both stated (a field one side lacks cannot contradict)."""
    return a is None or b is None or abs(a - b) <= tolerance


def dark_suits(flat: dict[str, Any], dark: dict[str, Any]) -> bool:
    """``MasterFlat.darkSuits``: the exposure and both temperatures must agree; binning and gain only where both state them."""
    return (same_exposure(flat.get("exposure"), dark.get("exposure"))
            and compatible(flat.get("binning"), dark.get("binning"), 0)
            and compatible(flat.get("gain"), dark.get("gain"), GAIN_TOLERANCE)
            and flat.get("temp") is not None and dark.get("temp") is not None
            and abs(flat["temp"] - dark["temp"]) <= TEMP_TOLERANCE)


def master_flat_closest(x: Optional[float], y: Optional[float]) -> float:
    """The distance between two values, 0 when either is missing (JS ``masterFlatClosest``)."""
    return 0 if x is None or y is None else abs(x - y)


def _rank(flat: dict[str, Any], x: dict[str, Any], y: dict[str, Any]) -> float:
    """Closest exposure first, then closest temperature (JS ``rank`` inside chooseDark)."""
    d = master_flat_closest(flat.get("exposure"), x.get("exposure")) - master_flat_closest(flat.get("exposure"), y.get("exposure"))
    if not is_falsy(d):
        return d
    return master_flat_closest(flat.get("temp"), x.get("temp")) - master_flat_closest(flat.get("temp"), y.get("temp"))


def raw_group_key(d: dict[str, Any]) -> str:
    """``MasterFlat.rawGroupKey``: what makes raw darks one master: exposure in ms, binning, gain. Not the temperature.

    Rounded as Math.round does (halves toward +infinity); a missing value is "-".
    """
    def part(v: Optional[float], scale: int) -> str:
        return "-" if v is None else js_string(js_round(v * scale))

    return "e" + part(d.get("exposure"), 1000) + "b" + part(d.get("binning"), 1) + "g" + part(d.get("gain"), 1)


def choose_dark(flat: dict[str, Any], darks: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """``MasterFlat.chooseDark``: the dark for a flat, or None.

    A suiting master wins over raw darks (closest exposure, then temperature, then path). Otherwise the raw darks
    are grouped by ``raw_group_key``, the groups with at least MIN_DARKS are ranked the same way (the larger group,
    then the smaller key, wins a tie), and the best group is returned with its paths sorted. A master comes back as
    ``{kind: "master", path}``; a group as ``{kind, key, paths, exposure, binning, gain, temp, temps}``.
    """
    masters: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for d in darks:
        if not dark_suits(flat, d):
            continue
        if d.get("kind") == "master":
            masters.append(d)
            continue
        key = raw_group_key(d)
        if key not in groups:
            groups[key] = {"kind": "raw", "key": key, "paths": [], "exposure": d.get("exposure"),
                           "binning": d.get("binning"), "gain": d.get("gain"), "temp": None, "temps": []}
            order.append(key)
        groups[key]["paths"].append(d["path"])
        groups[key]["temps"].append(d.get("temp"))
    for g in groups.values():
        # reduce, not sum(): sum() of floats is compensated since Python 3.12, and the JS adds in order.
        total = 0
        for t in g["temps"]:
            total = total + t
        g["temp"] = total / len(g["temps"])

    if masters:
        def cmp_master(x: dict[str, Any], y: dict[str, Any]) -> float:
            r = _rank(flat, x, y)
            return r if not is_falsy(r) else _cmp_utf16(x["path"], y["path"])

        masters.sort(key=cmp_to_key(cmp_master))
        return {"kind": "master", "path": masters[0]["path"]}

    raws = [groups[k] for k in order if len(groups[k]["paths"]) >= MIN_DARKS]
    if not raws:
        return None

    def cmp_raw(x: dict[str, Any], y: dict[str, Any]) -> float:
        r = _rank(flat, x, y)
        if is_falsy(r):
            r = len(y["paths"]) - len(x["paths"])
        return r if not is_falsy(r) else _cmp_utf16(x["key"], y["key"])

    raws.sort(key=cmp_to_key(cmp_raw))
    raws[0]["paths"].sort(key=_utf16_key)
    return raws[0]


# ---------------------------------------------------------------------------
# Names and rejection (MasterFlat.js 392-443)
# ---------------------------------------------------------------------------


def safe_name(text: Any) -> str:
    """``MasterFlat.safeName``: the text with every run of characters outside [A-Za-z0-9._-] as one "_", the dots and
    underscores at its ends removed; "unknown" when nothing is left."""
    s = js_string(text) if text is not None else ""
    s = _NAME_EDGE.sub("", _UNSAFE_NAME.sub("_", s))
    return s if len(s) > 0 else "unknown"


def file_name(filter_name: Any, binning: Any, ext: str = ".fit") -> str:
    """``MasterFlat.fileName``: ``masterFlat_<filter>[_bin<binning>]<ext>``. The JS writes ``.xisf``."""
    return "masterFlat_" + safe_name(filter_name) + ("" if binning is None else "_bin" + js_string(binning)) + ext


def rejection_for(count: int) -> dict[str, Any]:
    """``MasterFlat.rejectionFor``: Winsorized sigma clipping for 8 or more flats; percentile clipping for 3 to 7;
    none for 2 or fewer (too few frames for a sigma)."""
    if count >= 8:
        return {"method": "winsorized", "sigmaLow": 4.0, "sigmaHigh": 3.0}
    if count >= 3:
        return {"method": "percentile", "pcLow": 0.2, "pcHigh": 0.1}
    return {"method": "none"}


def dark_integration_spec(count: int) -> dict[str, Any]:
    """``MasterFlat.darkIntegrationSpec``: a plain average of the darks, rejection by count."""
    return {"combination": "average", "normalization": "none", "rejection": rejection_for(count)}


def flat_integration_spec(count: int) -> dict[str, Any]:
    """``MasterFlat.flatIntegrationSpec``: an average of the calibrated flats, multiplicative normalisation."""
    return {"combination": "average", "normalization": "multiplicative",
            "rejectionNormalization": "equalizeFluxes", "rejection": rejection_for(count)}


def dark_cache_path(group: dict[str, Any], sources: list[dict[str, Any]], cache_dir: str, ext: str = ".fit") -> str:
    """``MasterFlat.darkCachePath``: where a raw-dark master is cached, in a subfolder so Cache.clear (files only)
    leaves it. The name carries the group key and the first 16 hex digits of the hash of the sources' path, size and
    modified time. The JS writes ``.xisf``."""
    key = js_string(group["key"])
    parts = ["masterDark", key]
    for s in sources:
        parts.append(js_string(s.get("path")) + "|" + js_string(s.get("size")) + "|" + js_string(s.get("modified")))
    digest = hash_str("\n".join(parts))
    return js_string(cache_dir) + "/master-darks/masterDark_" + key + "_" + digest[:16] + ext
