"""Colour-camera (OSC) support: filter detection, colour-master slots, selection rules, the remembered-kind key
and the cache keys of a colour group.

Ported from Loom's JavaScript (script/lib/Util.js and script/lib/Pipeline.js). Each ``snake_case`` function is the
port of the JavaScript function of the same name, with the same positional arguments and JS objects as dicts; the
values are pinned by tests/oracle/loom_osc.json. Messages are the JavaScript's, byte for byte (Greek letters
included), and keys are hashed through ``loomsiril.loom.cache`` so they equal the JavaScript's.

Nothing here opens a file: a master's header is read by the caller, which passes what it found (``infoOf``).

Siril-only helpers: ``colour_run`` and ``js_config``.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from loomsiril.jsmath import number_str
from loomsiril.loom import cache
from loomsiril.loom.channels import CHANNELS, channel_from_filter, is_autocrop_name, master_variant_rank

# ---------------------------------------------------------------------------
# JavaScript semantics the rules rely on
# ---------------------------------------------------------------------------

#: What JavaScript's ``\s``, ``trim`` and ``/\S/`` take as white space (Python's ``\s`` differs at the edges).
_WS = "\t\n\u000b\u000c\r              " \
      "    　﻿"
_WS_CLASS = "[" + _WS + "]"
_NOT_WS_CLASS = "[^" + _WS + "]"


class _Undefined:
    """JavaScript's undefined: a key a JSON object lacks (JSON has only null)."""

    def __repr__(self) -> str:
        return "undefined"


UNDEFINED = _Undefined()


def _truthy(v: Any) -> bool:
    """JavaScript truthiness of a JSON value: containers are always true, ``0``, ``NaN``, ``""`` and null are not."""
    if v is None or v is UNDEFINED:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return not (v == 0 or (isinstance(v, float) and math.isnan(v)))
    if isinstance(v, str):
        return len(v) > 0
    return True


def _str(v: Any) -> str:
    """``String(v)`` of a JSON value (null is "null"; an array joins its items, null items being empty)."""
    if v is None:
        return "null"
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return number_str(float(v))
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return ",".join("" if x is None else _str(x) for x in v)
    return "[object Object]"


_DECIMAL = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def _to_number(v: Any) -> float:
    """JavaScript's ToNumber for the values a count can hold."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip(_WS)
        if t == "":
            return 0.0
        if _DECIMAL.match(t):
            return float(t)
        if t in ("Infinity", "+Infinity"):
            return math.inf
        if t == "-Infinity":
            return -math.inf
        return math.nan
    return math.nan


def _eq_num(v: Any, k: int) -> bool:
    """``v == k`` in JavaScript, for a number ``k`` (null equals no number)."""
    if v is None:
        return False
    if isinstance(v, (list, tuple, Mapping)):
        return _to_number(_str(v)) == k
    return _to_number(v) == k


def _has_length(v: Any) -> bool:
    """``v != undefined && v.length > 0``: a non-empty string or array."""
    return isinstance(v, (str, list, tuple)) and len(v) > 0


def _get(obj: Any, key: str) -> Any:
    """``obj[key]`` of a JSON object that may be null or lack the key (null when it does)."""
    return obj.get(key) if isinstance(obj, Mapping) else None


def _member(obj: Any, key: str) -> Any:
    """``obj && obj[key]`` where the difference between null and undefined shows (in a message)."""
    if not _truthy(obj):
        return obj
    return obj[key] if isinstance(obj, Mapping) and key in obj else UNDEFINED


def _or(*values: Any) -> Any:
    """``a || b || c``: the first truthy value, else the last."""
    for v in values[:-1]:
        if _truthy(v):
            return v
    return values[-1]


def _trim(s: str) -> str:
    return s.strip(_WS)


# ---------------------------------------------------------------------------
# Constants (Util.js, Pipeline.js)
# ---------------------------------------------------------------------------

#: Colour-camera filters by full normalised model name, with the manufacturer's Ha and OIII bandwidths in nm.
#: An entry whose line pair is not Ha/OIII is "unsupported".
OSC_FILTERS: list[dict[str, Any]] = [
    {"match": "lextreme", "name": "L-eXtreme", "kind": "dualband", "haNm": 7, "oiiiNm": 7},
    {"match": "lultimate", "name": "L-Ultimate", "kind": "dualband", "haNm": 3, "oiiiNm": 3},
    {"match": "lenhance", "name": "L-eNhance", "kind": "dualband", "haNm": 10, "oiiiNm": 24},
    {"match": "lenhanced", "name": "L-eNhance", "kind": "dualband", "haNm": 10, "oiiiNm": 24},
    {"match": "alpthaoiii", "name": "ALP-T Ha/OIII", "kind": "dualband", "haNm": 5, "oiiiNm": 5},
    {"match": "alpthaoiii5nm", "name": "ALP-T Ha/OIII", "kind": "dualband", "haNm": 5, "oiiiNm": 5},
    {"match": "alptsiihb", "name": "ALP-T SII/Hb", "kind": "unsupported", "haNm": None, "oiiiNm": None},
    {"match": "alptsiihbeta", "name": "ALP-T SII/Hb", "kind": "unsupported", "haNm": None, "oiiiNm": None},
    {"match": "nbzii", "name": "NBZ-II", "kind": "dualband", "haNm": 10, "oiiiNm": 8},
    {"match": "nbz", "name": "NBZ", "kind": "dualband", "haNm": 12, "oiiiNm": 12},
    {"match": "nbzex", "name": "NBZ", "kind": "dualband", "haNm": 12, "oiiiNm": 12},
]
OSC_BROADBAND: list[str] = ["uvir", "uvircut", "ircut", "uvirblock", "lpro", "cls", "clsccd", "lrgb"]
OSC_FAMILIES: list[str] = ["duo", "dual", "dualband", "tri", "triad", "quad", "alpt"]

#: The two colour-master slots and the logical channels each one stands for.
COLOUR_SLOTS: list[str] = ["OSC", "OSCNB"]
COLOUR_MEMBERS: dict[str, list[str]] = {"OSC": ["R", "G", "B"], "OSCNB": ["H", "O"]}

#: The plane each colour master contributes, per kind: the index is the plane in the master's pixel order, the key
#: the logical channel it becomes.
OSC_RECIPES: dict[str, dict[str, int]] = {"OSC": {"R": 0, "G": 1, "B": 2}, "OSCNB": {"H": 0, "O": 1}}

# ---------------------------------------------------------------------------
# Filter names (Util.js)
# ---------------------------------------------------------------------------

_SIZE_SUFFIX = re.compile(_WS_CLASS + r"*[0-9]+(?:\.[0-9]+)?" + _WS_CLASS + r"*(?:\"|''|mm|in)\Z",
                          re.IGNORECASE | re.ASCII)
_BRAND_PREFIX = re.compile(r"(?:optolong|antlia|idas|astronomik|zwo|baader|svbony|stc|radian)[" + _WS + r"_-]+(?="
                           + _NOT_WS_CLASS + ")", re.IGNORECASE | re.ASCII)
_NOT_ALNUM = re.compile(r"[^a-z0-9]")
_ALPT_SII_HB = re.compile(r"alpt.*(?:sii|hb)")


def normalise_filter(value: Any) -> str:
    """``Util.normaliseFilter``: lower case, Greek line names spelt out, everything but a-z and 0-9 dropped."""
    if value is None:
        return ""
    return _NOT_ALNUM.sub("", _str(value).lower().replace("α", "a").replace("β", "b"))


def osc_model_name(value: Any) -> str:
    """``Util.oscModelName``: the model part of a filter name (a leading brand and a trailing size dropped).

    A brand on its own is kept, so that it is not read as a broadband filter.
    """
    if value is None:
        return ""
    s = _SIZE_SUFFIX.sub("", _trim(_str(value)), count=1)
    return _BRAND_PREFIX.sub("", s, count=1) if _BRAND_PREFIX.match(s) else s


def osc_filter_info(filter_value: Any) -> Optional[dict[str, Any]]:
    """``Util.oscFilterInfo``: ``{kind, name, confident, haNm, oiiiNm}`` for a filter, or None when it is unknown."""
    f = normalise_filter(osc_model_name(filter_value))
    for m in OSC_FILTERS:
        if m["match"] == f:
            return {"kind": m["kind"], "name": m["name"], "confident": m["kind"] != "unsupported",
                    "haNm": m["haNm"], "oiiiNm": m["oiiiNm"]}
    # Any ALP-T name that mentions SII or Hb is the unsupported line pair, whatever else it says.
    if _ALPT_SII_HB.match(f):
        return {"kind": "unsupported", "name": _trim(_str(filter_value)), "confident": False,
                "haNm": None, "oiiiNm": None}
    if len(f) == 0 or f in OSC_BROADBAND:
        return {"kind": "broadband", "name": "" if len(f) == 0 else _trim(_str(filter_value)),
                "confident": True, "haNm": None, "oiiiNm": None}
    for fam in OSC_FAMILIES:
        if f.startswith(fam):
            return {"kind": "dualband", "name": _trim(_str(filter_value)), "confident": False,
                    "haNm": None, "oiiiNm": None}
    return None


# ---------------------------------------------------------------------------
# Colour masters by name and header (Util.js)
# ---------------------------------------------------------------------------

_FILTER_TOKEN = re.compile(r"FILTER-([^_.]*)", re.IGNORECASE | re.ASCII)
_DRIZZLE_TOKEN = re.compile(r"drizzle_([0-9]+)x", re.IGNORECASE | re.ASCII)
_COLOUR_MARK = re.compile(r"_(?:color|colour|RGB)(?:_|\.|\Z)", re.IGNORECASE | re.ASCII)
_DIRECTORY = re.compile(r"^[^\n\r  ]*[\\/]")


def parse_colour_master_name(name: Any) -> dict[str, Any]:
    """``Util.parseColourMasterName``: the filter token, colour mark, drizzle and autocrop flag of a WBPP colour name.

    The FILTER token runs to the next underscore, hyphens included. The header stays authoritative.
    """
    n = _str(name) if _truthy(name) else ""
    m = _FILTER_TOKEN.search(n)
    dm = _DRIZZLE_TOKEN.search(n)
    return {"filter": m.group(1) if m else "",
            "colour": _COLOUR_MARK.search(n) is not None,
            "drizzle": dm.group(1) + "x" if dm else "",
            "autocrop": is_autocrop_name(n)}


def channel_count_problem(path: Any, n: Any) -> Optional[str]:
    """``Util.channelCountProblem``: the complaint about a channel count other than 1 or 3, or None."""
    if _eq_num(n, 1) or _eq_num(n, 3):
        return None
    return _str(path) + " has " + _str(n) + " channels; Loom takes mono (1) or colour (3) masters"


# ---------------------------------------------------------------------------
# What is supplied (Util.js)
# ---------------------------------------------------------------------------


def channel_supplied(paths: Mapping[str, Any], views: Mapping[str, Any], k: str) -> bool:
    """``Util.channelSupplied``: a channel counts as supplied with a file path or an open view."""
    p, v = _get(paths, k), _get(views, k)
    return _has_length(p) or (v is not None and len(_str(v)) > 0)


def _paths_views(config: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    paths = _get(config, "paths")
    views = _get(config, "views")
    return (paths if _truthy(paths) else {}), (views if _truthy(views) else {})


def colour_supplied(config: Any) -> list[str]:
    """``Util.colourSupplied``: the colour slots with a path or an open view, in COLOUR_SLOTS order."""
    paths, views = _paths_views(config)
    return [k for k in COLOUR_SLOTS if channel_supplied(paths, views, k)]


def mono_supplied(config: Any) -> list[str]:
    """``Util.monoSupplied``: the mono channels with a path or an open view."""
    paths, views = _paths_views(config)
    return [k for k in CHANNELS if channel_supplied(paths, views, k)]


def supplied_channels(config: Any) -> list[str]:
    """``Util.suppliedChannels``: the logical channels supplied (mono ones plus the members of each colour slot)."""
    have = mono_supplied(config)
    for slot in colour_supplied(config):
        for k in COLOUR_MEMBERS[slot]:
            if k not in have:
                have.append(k)
    return [k for k in CHANNELS if k in have]


def colour_mix_problem(config: Any) -> Optional[str]:
    """``Util.colourMixProblem``: colour and mono masters are two kinds of run; the message names the inputs."""
    colour, mono = colour_supplied(config), mono_supplied(config)
    if not colour or not mono:
        return None
    paths, views = _paths_views(config)

    def named(k: str) -> str:
        p = paths.get(k)
        shown = _DIRECTORY.sub("", _str(p), count=1) if _has_length(p) else _str(views.get(k))
        return k + "=" + shown

    return ("colour masters cannot be combined with mono masters; "
            "choose either the colour masters or the mono masters"
            " (colour: " + ", ".join(named(k) for k in colour) + "; mono: " + ", ".join(named(k) for k in mono) + ")")


# ---------------------------------------------------------------------------
# The kind of a colour master (Util.js)
# ---------------------------------------------------------------------------


def osc_kind_key(filter_value: Any, path: Any) -> str:
    """``Util.oscKindKey``: the key a remembered kind choice is stored under.

    The model name that detection uses (a brand and a size are dropped), so one choice covers every spelling of a
    model. A blank filter is keyed by the path exactly as given: never resolved or case-folded.
    """
    f = normalise_filter(osc_model_name(filter_value))
    return "filter:" + f if len(f) > 0 else "path:" + _str(path)


def effective_osc_kind(slot: Any, filter_value: Any, path: Any, choices: Any) -> dict[str, Any]:
    """``Util.effectiveOscKind``: the kind a colour master is treated as, and where that came from.

    An unsupported filter is never overridable; then a remembered choice; then a confident detection; otherwise no
    kind (source "none") and the caller must ask.
    """
    info = osc_filter_info(filter_value)
    name = info["name"] if info else ("" if filter_value is None else _str(filter_value))
    if info and info["kind"] == "unsupported":
        return {"kind": info["kind"], "name": info["name"], "confident": info["confident"],
                "haNm": info["haNm"], "oiiiNm": info["oiiiNm"], "source": "detected"}

    key = osc_kind_key(filter_value, path)
    choice = choices[key] if isinstance(choices, Mapping) and key in choices else None
    if choice == "broadband" or choice == "dualband":
        return {"kind": choice, "name": name, "confident": True,
                "haNm": info["haNm"] if info else None, "oiiiNm": info["oiiiNm"] if info else None,
                "source": "choice"}

    if info and info["confident"]:
        return {"kind": info["kind"], "name": info["name"], "confident": info["confident"],
                "haNm": info["haNm"], "oiiiNm": info["oiiiNm"], "source": "detected"}

    return {"kind": None, "name": name, "confident": False, "haNm": None, "oiiiNm": None, "source": "none"}


def entry_channel(filter_value: Any, number_of_channels: Any, path: Any, choices: Any) -> dict[str, Any]:
    """``Util.entryChannel``: where a masters-list entry goes, from its filter and header channel count.

    One channel is a mono master (its channel key); three are a colour master whose slot follows its kind
    (broadband is OSC, dual-band is OSCNB; an undetected or unsupported kind has no slot yet). Any other count has
    none either.
    """
    if _eq_num(number_of_channels, 1):
        return {"channel": channel_from_filter(filter_value), "colour": False, "kindSource": None}
    if not _eq_num(number_of_channels, 3):
        return {"channel": None, "colour": False, "kindSource": None}
    k = effective_osc_kind(None, filter_value, path, choices)
    channel = "OSC" if k["kind"] == "broadband" else "OSCNB" if k["kind"] == "dualband" else None
    return {"channel": channel, "colour": True, "kindSource": k["source"]}


def select_colour_masters(candidates: Sequence[Any], choices: Any) -> dict[str, Any]:
    """``Util.selectColourMasters``: the best colour master per slot, and the candidates with no slot yet.

    The variant rank first (drizzle, then autocrop), the newest within a variant. The rank is written onto each pick.
    A candidate with no slot (kind undetected or unsupported) goes to ``unresolved``, in input order; a candidate
    that is not a colour image is not a colour pick.
    """
    best: dict[str, Any] = {"OSC": None, "OSCNB": None, "unresolved": []}
    for c in candidates:
        if not _truthy(c):
            continue
        n = _get(c, "numberOfChannels")
        r = entry_channel(_get(c, "filter"), 3 if n is None else n, _get(c, "path"), choices)
        if r["channel"] is None:
            best["unresolved"].append(c)
            continue
        if not r["colour"]:
            continue
        rank = master_variant_rank(_get(c, "drizzle"), _get(c, "autocrop"))
        cur = best[r["channel"]]
        if cur is None or rank > cur["rank"] or (
                rank == cur["rank"] and (_get(c, "mtime") if _truthy(_get(c, "mtime")) else 0) >
                (_get(cur, "mtime") if _truthy(_get(cur, "mtime")) else 0)):
            c["rank"] = rank
            best[r["channel"]] = c
    return best


def colour_candidate_correction(c: Any) -> Optional[str]:
    """``Util.colourCandidateCorrection``: the line for a name and a header that disagree about colour, or None."""
    if not _truthy(c):
        return None
    n = _get(c, "numberOfChannels")
    if _truthy(_get(c, "colour")) and _eq_num(n, 1):
        return _str(_member(c, "path")) + ": the name says colour but the image has 1 channel; it is used as a mono master"
    if not _truthy(_get(c, "colour")) and _eq_num(n, 3):
        return _str(_member(c, "path")) + ": the name says mono but the image has 3 channels; it is used as a colour master"
    return None


# ---------------------------------------------------------------------------
# Pipeline checks (Pipeline.js)
# ---------------------------------------------------------------------------

InfoOf = Union[Mapping[str, Any], Callable[[str], Any]]


def _info_of(info_of: InfoOf) -> Callable[[str], Any]:
    if callable(info_of):
        return info_of
    return lambda slot: info_of.get(slot)


def osc_palette_problem(config: Any) -> Optional[str]:
    """``Pipeline.oscPaletteProblem``: SII palettes cannot be built from a colour master."""
    if not colour_supplied(config):
        return None
    pals = _get(config, "palettes")
    pals = pals if _truthy(pals) else []
    if "SHO" in pals or "HSO" in pals:
        return "SHO and HSO need SII, which a colour camera cannot separate from Ha: choose HOO"
    return None


def _info_path(info: Any, config: Any, key: str) -> Any:
    def own(name: str) -> Any:
        return config[name] if isinstance(config, Mapping) and name in config else UNDEFINED

    return _or(_member(info, "path"), _member(own("paths"), key), _member(own("views"), key))


def input_channel_problems(config: Any, info_of: InfoOf) -> list[str]:
    """``Pipeline.inputChannelProblems``: channel-count problems of the mono slots.

    ``info_of`` is a dict ``slot -> {path, numberOfChannels, ...}`` or a callable giving the same (None for a slot
    with nothing known). The colour slots are left to ``osc_kind_problems``.
    """
    get = _info_of(info_of)
    problems: list[str] = []
    for key in mono_supplied(config):
        info = get(key)
        if info is None:
            continue
        path = _info_path(info, config, key)
        n = _get(info, "numberOfChannels")
        if not isinstance(n, (int, float)) or isinstance(n, bool):
            problems.append("could not read the channel count of " + _str(path))
        elif n == 3:
            problems.append(_str(path) + " is a colour master (3 channels); a mono slot needs a one-channel master")
        elif n != 1:
            problems.append(channel_count_problem(path, n))  # type: ignore[arg-type]
    return problems


def osc_kind_problems(config: dict[str, Any], info_of: InfoOf) -> list[str]:
    """``Pipeline.oscKindProblems``: the checks of the colour slots (three channels, a known kind matching the slot).

    The kind found for each good slot is left in ``config["oscKinds"]`` for the run (the dict is made when absent).
    """
    get = _info_of(info_of)
    problems: list[str] = []
    for slot in colour_supplied(config):
        info = get(slot)
        if info is None:
            continue
        # never an empty key: a blank-filter choice is stored under the path
        path = _info_path(info, config, slot)
        n = _get(info, "numberOfChannels")
        if not isinstance(n, (int, float)) or isinstance(n, bool):
            problems.append("could not read the channel count of " + _str(path))
            continue
        if n == 1:
            problems.append(_str(path) + " is a mono master (1 channel); a colour slot needs a three-channel master")
            continue
        if n != 3:
            problems.append(channel_count_problem(path, n))  # type: ignore[arg-type]
            continue
        k = effective_osc_kind(slot, _get(info, "filter"), path, _get(config, "oscKindChoices"))
        if k["kind"] == "unsupported":
            problems.append("this filter passes SII and Hβ; only Hα/OIII dual-band filters are supported")
        elif k["source"] == "none":
            problems.append("choose Broadband or Dual-band Hα + OIII for " + _str(path))
        elif slot == "OSCNB" and k["kind"] == "broadband":
            problems.append(_str(path) + " is a broadband colour master; the dual-band slot needs a Dual-band Hα + OIII master")
        elif slot == "OSC" and k["kind"] == "dualband":
            problems.append(_str(path) + " is a dual-band colour master; the broadband slot needs a Broadband master")
        else:
            if not _truthy(config.get("oscKinds")):
                config["oscKinds"] = {}
            config["oscKinds"][slot] = k["kind"]
    return problems


# ---------------------------------------------------------------------------
# The keys of a colour group (Pipeline.js)
# ---------------------------------------------------------------------------


def osc_group_identity(fingerprint: Any, kind: Any) -> str:
    """``Pipeline.oscGroupIdentity``: the master's fingerprint plus its kind; every key of the group starts here."""
    return _str(fingerprint) + "|osc:" + _str(kind)


def osc_sibling_key(group_identity: Any, plane: Any) -> str:
    """``Pipeline.oscSiblingKey``: the source key of one sibling plane (hashed: a hexadecimal file name)."""
    return cache.hash_str(_str(group_identity) + "|plane:" + _str(plane))


def osc_validate_name(group_identity: Any) -> str:
    """``Pipeline.oscValidateName``: the name of the group's validation sidecar."""
    return cache.hash_str(_str(group_identity) + "|validate") + ".osc.json"


def osc_group_register_key(stage: str, sibling_keys_in_plane_order: Sequence[Any], params: Any) -> str:
    """``Pipeline.oscGroupRegisterKey``: the key of a grouped registration, from the sibling keys in plane order."""
    joined = "|".join("" if k is None else _str(k) for k in sibling_keys_in_plane_order)
    return cache.chain_key(cache.hash_str(joined), stage, params)


def osc_registered_sibling_key(group_key: Any, plane: Any) -> str:
    """``Pipeline.oscRegisteredSiblingKey``: the key of one registered sibling plane."""
    return cache.hash_str(_str(group_key) + "|plane:" + _str(plane))


def osc_plane_problem(stats: Mapping[str, Any], plane: Any, path: Any) -> Optional[str]:
    """``Pipeline.oscPlaneProblem``: the complaint about a constant plane of a colour master, or None."""
    lo, hi = stats.get("min"), stats.get("max")
    if hi == lo:
        return _str(path) + ": plane " + _str(plane) + " is constant (all values " + _str(lo) + \
               "); the master looks broken"
    return None


def osc_validation_record(stats_list: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``Pipeline.oscValidationRecord``: the sidecar record of a first load (min, max and median of each plane)."""
    return {"planes": [{"min": s.get("min"), "max": s.get("max"), "median": s.get("median")} for s in stats_list]}


def osc_narrowband_bandwidths(config: Mapping[str, Any], info: Any) -> dict[str, Any]:
    """``Pipeline.oscNarrowbandBandwidths``: the SPCC bandwidth of each output channel of a dual-band colour master.

    HOO is R = Ha, G = OIII, B = OIII. The filter table gives each line's bandwidth; a filter it does not know (or
    that has none) falls back to the run's ``narrowbandBandwidth``. ``info["filter"]`` is the master's FILTER keyword.
    """
    fi = osc_filter_info(_get(info, "filter") if _truthy(info) else None)

    def pick(nm: Any) -> Any:
        if isinstance(nm, (int, float)) and not isinstance(nm, bool) and math.isfinite(nm) and nm > 0:
            return nm
        return config.get("narrowbandBandwidth")

    ha = pick(fi["haNm"] if fi else None)
    oiii = pick(fi["oiiiNm"] if fi else None)
    return {"R": ha, "G": oiii, "B": oiii}


#: The name the SPCC stages use for ``osc_narrowband_bandwidths``.
narrowband_bandwidths = osc_narrowband_bandwidths


# ---------------------------------------------------------------------------
# Siril only
# ---------------------------------------------------------------------------


def colour_run(config: Any) -> bool:
    """True when the run takes a colour master: the ``OSC`` or ``OSCNB`` slot is supplied."""
    return len(colour_supplied(config)) > 0


def js_config(config: Any) -> dict[str, Any]:
    """A copy of the Siril config in the shape Loom's JavaScript functions take.

    The Siril config names one OSC sensor, ``oscSensor = {"sensor", "frontFilter", "lpf"}``; the JavaScript keeps a
    name per channel, so ``sensor`` is given to R, G and B (the functions that use it only compare names). Every
    other key is copied as it is.
    """
    out: dict[str, Any] = copy.deepcopy(dict(config)) if isinstance(config, Mapping) else {}
    sensor = out.get("oscSensor")
    if isinstance(sensor, Mapping):
        name = sensor.get("sensor")
        out["oscSensor"] = {"R": name, "G": name, "B": name}
    return out
