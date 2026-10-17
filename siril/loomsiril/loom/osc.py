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
from loomsiril.loom.channels import (
    CHANNELS,
    PALETTES,
    channel_from_filter,
    is_autocrop_name,
    master_variant_rank,
    palette_channels,
)

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

#: The colour-master slots and the logical channels each one stands for. ``OSCHA`` is one Hα filter on a colour
#: camera: its red plane is H.
COLOUR_SLOTS: list[str] = ["OSC", "OSCNB", "OSCHA"]
COLOUR_MEMBERS: dict[str, list[str]] = {"OSC": ["R", "G", "B"], "OSCNB": ["H", "O"], "OSCHA": ["H"]}

#: The kinds a colour master can be, and the slot each one fills.
OSC_KINDS: tuple[str, ...] = ("broadband", "dualband", "halpha")
COLOUR_SLOT_KIND: dict[str, str] = {"OSC": "broadband", "OSCNB": "dualband", "OSCHA": "halpha"}
#: The other way round: the slot a kind fills (Siril's own helper).
KIND_SLOTS: dict[str, str] = {kind: slot for slot, kind in COLOUR_SLOT_KIND.items()}

#: The plane each colour master contributes, per kind: the index is the plane in the master's pixel order, the key
#: the logical channel it becomes.
OSC_RECIPES: dict[str, dict[str, int]] = {"OSC": {"R": 0, "G": 1, "B": 2}, "OSCNB": {"H": 0, "O": 1},
                                          "OSCHA": {"H": 0}}
#: The planes checked when a colour master is split. OSCHA uses only its red plane (H): its green and blue planes
#: (interpolation and leak) are neither checked nor kept.
OSC_VALIDATED_PLANES: dict[str, list[int]] = {"OSC": [0, 1, 2], "OSCNB": [0, 1, 2], "OSCHA": [0]}

# ---------------------------------------------------------------------------
# Filter names (Util.js)
# ---------------------------------------------------------------------------

_SIZE_SUFFIX = re.compile(_WS_CLASS + r"*[0-9]+(?:\.[0-9]+)?" + _WS_CLASS + r"*(?:\"|''|mm|in)\Z",
                          re.IGNORECASE | re.ASCII)
_BRAND_PREFIX = re.compile(r"(?:optolong|antlia|idas|astronomik|zwo|baader|svbony|stc|radian)[" + _WS + r"_-]+(?="
                           + _NOT_WS_CLASS + ")", re.IGNORECASE | re.ASCII)
_NOT_ALNUM = re.compile(r"[^a-z0-9]")
_ALPT_SII_HB = re.compile(r"alpt.*(?:sii|hb)")
#: A single Hα filter, by its normalised model name: Ha or Halpha with the bandwidth on either side (6nmha, ha7nm).
_HALPHA = re.compile(r"(?:[0-9]+nm)?(?:ha|halpha)(?:[0-9]+nm)?\Z", re.ASCII)
_BANDWIDTH = re.compile(r"([0-9]+(?:\.[0-9]+)?)" + _WS_CLASS + r"*nm", re.IGNORECASE)


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
    # A single Hα filter, after every dual-band name (L-eXtreme, ALP-T Hα/OIII, Duo and the like are dual-band).
    if _HALPHA.match(f):
        nm = _BANDWIDTH.search(osc_model_name(filter_value))
        return {"kind": "halpha", "name": _trim(_str(filter_value)), "confident": True,
                "haNm": _js_float(nm.group(1)) if nm else None, "oiiiNm": None}
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
    if choice in OSC_KINDS:
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
    (broadband is OSC, dual-band is OSCNB, Hα is OSCHA; an undetected or unsupported kind has no slot yet). Any other count has
    none either.
    """
    if _eq_num(number_of_channels, 1):
        return {"channel": channel_from_filter(filter_value), "colour": False, "kindSource": None}
    if not _eq_num(number_of_channels, 3):
        return {"channel": None, "colour": False, "kindSource": None}
    k = effective_osc_kind(None, filter_value, path, choices)
    channel = KIND_SLOTS.get(k["kind"]) if k["kind"] is not None else None
    return {"channel": channel, "colour": True, "kindSource": k["source"]}


def select_colour_masters(candidates: Sequence[Any], choices: Any) -> dict[str, Any]:
    """``Util.selectColourMasters``: the best colour master per slot, and the candidates with no slot yet.

    The variant rank first (drizzle, then autocrop), the newest within a variant. The rank is written onto each pick.
    A candidate with no slot (kind undetected or unsupported) goes to ``unresolved``, in input order; a candidate
    that is not a colour image is not a colour pick.
    """
    best: dict[str, Any] = {"OSC": None, "OSCNB": None, "OSCHA": None, "unresolved": []}
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


def _js_float(text: str) -> Any:
    """``parseFloat`` of a decimal text, as a JSON number (an integer value stays an int, as 7 not 7.0)."""
    v = float(text)
    return int(v) if v == int(v) else v


_KIND_TEXT = {"broadband": "broadband", "dualband": "dual-band", "halpha": "narrowband Hα"}
_SLOT_TEXT = {"OSC": "broadband", "OSCNB": "dual-band", "OSCHA": "narrowband Hα"}
_KIND_NAME = {"OSC": "Broadband", "OSCNB": "Dual-band Hα + OIII", "OSCHA": "Narrowband Hα"}


def osc_combination_problem(config: Any) -> Optional[str]:
    """``Util.oscCombinationProblem``: a Hα colour master and a dual-band one both supply H, so one run cannot take
    both (an Hα master with a broadband one is fine). None when the combination is allowed."""
    colour = colour_supplied(config)
    if "OSCHA" not in colour or "OSCNB" not in colour:
        return None
    paths, views = _paths_views(config)

    def shown(k: str) -> str:
        p = paths.get(k)
        return _DIRECTORY.sub("", _str(p), count=1) if _has_length(p) else _str(views.get(k))

    return ("a Narrowband Hα colour master and a dual-band Hα + OIII colour master both supply H; "
            "choose one of them (Narrowband Hα: " + shown("OSCHA") + "; dual-band: " + shown("OSCNB") + ")")


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
            problems.append("choose Broadband, Dual-band Hα + OIII or Narrowband Hα for " + _str(path))
        elif KIND_SLOTS.get(k["kind"]) != slot:
            problems.append(_str(path) + " is a " + _KIND_TEXT[k["kind"]] + " colour master; the " + _SLOT_TEXT[slot]
                            + " slot needs a " + _KIND_NAME[slot] + " master")
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
# How a colour run's channels are registered and cached (Pipeline.js)
# ---------------------------------------------------------------------------

_COLOUR_REF = {"OSC": "G", "OSCNB": "H", "OSCHA": "H"}
_COLOUR_PLANES = {"OSC": ["R", "G", "B"], "OSCNB": ["H", "O", "O"], "OSCHA": ["H", "H", "H"]}


def osc_registration_plan(present: Sequence[Any], groups: Any) -> dict[str, Any]:
    """``Pipeline.oscRegistrationPlan``: how the channels of a colour run are registered.

    ``groups`` maps a colour slot to its members (``{"OSC": ["R", "G", "B"], "OSCNB": ["H", "O"]}``); only members
    in ``present`` count. The reference is OSC's G, else OSCNB's H, and its own group is already on the reference
    grid (``identity``). Any other group is one entry: its members and the planes of the temporary image that is
    registered in one go (R, G, B for OSC; H, O, O for OSCNB, whose third plane repeats O). Channels outside every
    group are listed under ``mono``.
    """
    used: set[Any] = set()
    byslot: dict[str, list[Any]] = {}
    for slot in COLOUR_SLOTS:
        listed = _get(groups, slot) if _truthy(groups) else None
        members = [k for k in listed if k in present] if _truthy(listed) else []
        if members:
            byslot[slot] = members
            used.update(members)
    ref_slot = next((slot for slot in COLOUR_SLOTS if slot in byslot), None)
    plan: dict[str, Any] = {"ref": None, "identity": [], "groups": [], "mono": [k for k in present if k not in used]}
    if ref_slot is None:
        return plan
    plan["ref"] = _COLOUR_REF[ref_slot]
    plan["identity"] = list(byslot[ref_slot])
    for slot in COLOUR_SLOTS:
        if slot in byslot and slot != ref_slot:
            plan["groups"].append({"slot": slot, "members": list(byslot[slot]), "planes": list(_COLOUR_PLANES[slot])})
    return plan


def clean_registration_plan(ref_key: Any, groups: Any) -> str:
    """``Pipeline.cleanRegistrationPlan``: how the clean planes reach the reference frame.

    ``"identity"`` when they are the OSC planes and the reference is OSC's own G (they already sit on it, so only
    the crop applies), ``"mono"`` for the per-channel registration. A colour run's reference is always its OSC G;
    any other reference there is a programming error, so it raises ``ValueError`` with the JavaScript's message.
    """
    osc_members = _get(groups, "OSC") if _truthy(groups) else None
    if not _truthy(osc_members):
        return "mono"
    if ref_key not in osc_members:
        raise ValueError("internal: with OSC in the run the reference must be its G (got " + _str(ref_key) + ")")
    return "identity"


def osc_group_cache_action(group_cached: Any, splits_cached: Sequence[Any], policy: Any) -> str:
    """``Pipeline.oscGroupCacheAction``: what a grouped registration does given the cache.

    ``"register"`` (combine, register, store, split), ``"split"`` (the group image is cached but a split is not:
    split it again) or ``"load"`` (every split is cached). With the cache off or ignored nothing is read, so it
    always registers. ``policy`` is ``{"useCache", "ignoreCache"}`` from the configuration.
    """
    if not _truthy(_get(policy, "useCache")) or _truthy(_get(policy, "ignoreCache")) or not _truthy(group_cached):
        return "register"
    return "load" if all(_truthy(c) for c in splits_cached) else "split"


def osc_channel_groups(chans: Mapping[str, Any]) -> Optional[dict[str, list[str]]]:
    """``Pipeline.oscChannelGroups``: the colour groups of a run, from the channels' own ``group`` fields.

    A map of slot to members in plane order, or None when no channel has one.
    """
    groups: Optional[dict[str, list[str]]] = None
    for slot in OSC_RECIPES:
        members = [k for k in COLOUR_MEMBERS[slot] if _truthy(_get(chans, k)) and _get(_get(chans, k), "group") == slot]
        if members:
            groups = groups or {}
            groups[slot] = members
    return groups


#: How ``osc_sensor_problems`` learns whether a sensor curve exists: a callable ``(name, channel) -> bool``, or a
#: table of channel to the curve names that exist for it.
CurveExists = Union[Callable[[str, str], Any], Mapping[str, Sequence[str]], None]

_RGB = ["R", "G", "B"]


def osc_sensor_problems(config: Any, curve_exists: CurveExists = None) -> list[str]:
    """``Pipeline.oscSensorProblems``: the colour camera's sensor checks (parity only).

    A colour master is calibrated against the three sensor colour-filter curves in ``config["oscSensor"]``, so all
    three must be chosen and each must exist. A run without OSC (mono, or OSCNB alone) has no sensor to choose.
    Loom asks its filter database when ``curve_exists`` is not given; Siril has none to ask here, so with None the
    existence of the curves is not checked. Siril's own sensor rule is in the configuration module.
    """
    if "OSC" not in colour_supplied(config):
        return []
    sensor = _or(_get(config, "oscSensor"), {})
    for channel in _RGB:
        if not _truthy(_get(sensor, channel)):
            return ["choose the sensor colour filters for the colour master (SPCC section)"]
    if curve_exists is None:
        return []
    if callable(curve_exists):
        exists = curve_exists
    else:
        table = curve_exists

        def exists(name: str, channel: str) -> bool:
            return name in (table.get(channel) or ())

    return ["sensor colour filter curve '" + _str(sensor[c]) + "' is not in filters.xspd"
            for c in _RGB if not _truthy(exists(sensor[c], c))]


def osc_control_state(config: Any) -> dict[str, Any]:
    """``Util.oscControlState``: which dialog controls a colour master changes.

    Any colour master greys the mono boxes and the palettes that need SII; the sensor colour filters replace the R,
    G and B filter boxes only while OSC (not OSCNB) is supplied. Reasons are empty while the control is enabled.
    """
    colour = colour_supplied(config)
    any_colour = len(colour) > 0
    has_osc = "OSC" in colour
    return {
        "monoEnabled": not any_colour,
        "monoReason": "colour and mono masters cannot be combined" if any_colour else "",
        "shoHsoEnabled": not any_colour,
        "shoHsoReason": "needs SII, which a colour camera cannot separate from Ha" if any_colour else "",
        "sensorRowVisible": has_osc,
        "rgbFilterRowsVisible": not has_osc,
    }


# ---------------------------------------------------------------------------
# LOOMSRC texts of the exports (Pipeline.js)
# ---------------------------------------------------------------------------

#: What each logical channel of a colour master really holds, for the run log (``Pipeline.OSC_LINE_NOTES``).
OSC_LINE_NOTES: dict[str, str] = {"H": "Ha, plus SII for quad-band", "O": "OIII with H\u03b2"}


def _file_name(path: Any) -> str:
    """The last component of ``String(path)``, split on either separator."""
    return re.split(r"[\\/]", _str(path))[-1]


def osc_palette_sources(config: Any, infos: Any, palette: Any) -> str:
    """``Pipeline.oscPaletteSources``: the sources line of a palette built from a dual-band colour master.

    ``""`` when the run has no ``OSCNB`` master or the palette is not built from one. ``infos`` is keyed by colour
    slot: ``{"OSCNB": {"name": ...}}``, the name being the filter's model name.
    """
    pal = _str(palette if _truthy(palette) else "HOO")
    if "OSCNB" not in colour_supplied(config) or pal not in PALETTES:
        return ""
    info = _get(infos, "OSCNB")
    name = _get(info, "name")
    origin = "OSCNB" + (" " + _str(name) if _truthy(name) else "")
    parts: list[str] = []
    for ch in palette_channels(pal) or []:
        note = OSC_LINE_NOTES.get(ch)
        if not note:
            return ""
        parts.append(f"{ch} from {origin} ({note})")
    return pal + ": " + ", ".join(parts)


def osc_channel_source(chan: Any) -> Optional[str]:
    """``Pipeline.oscChannelSource``: the colour master's file and plane a channel comes from; None for a mono one."""
    if chan is None or not _truthy(_get(chan, "group")):
        return None
    return _file_name(_member(chan, "path")) + "|plane:" + _str(_member(chan, "plane"))


def osc_source_text(chans: Mapping[str, Any], keys: Sequence[str], per_plane: Any) -> Optional[str]:
    """``Pipeline.oscSourceText``: one value naming the source planes of a composite or a palette.

    Only the channels ``keys`` of ``chans`` that come from a colour master count (None when none does). Per master the
    planes are listed together ("OSC f planes 0,1,2") unless ``per_plane``, when each distinct plane is its own entry
    ("OSCNB f plane 0; OSCNB f plane 1"). A missing plane joins as an empty entry, as in JavaScript's ``join``.
    """
    order: list[str] = []
    by_master: dict[str, list[Any]] = {}
    seen: set[str] = set()
    for k in keys:
        c = _get(chans, k)
        if not _truthy(_get(c, "group")):
            continue
        head = _str(_get(c, "group")) + " " + _file_name(_member(c, "path"))
        plane = _member(c, "plane")
        ident = head + "|" + _str(plane)
        if ident in seen:
            continue
        seen.add(ident)
        if head not in by_master:
            by_master[head] = []
            order.append(head)
        by_master[head].append(plane)
    if not order:
        return None
    parts: list[str] = []
    for head in order:
        planes = by_master[head]
        if _truthy(per_plane):
            parts.extend(f"{head} plane {_str(p)}" for p in planes)
        else:
            joined = ",".join("" if p is None or p is UNDEFINED else _str(p) for p in planes)
            parts.append(head + (" plane " if len(planes) == 1 else " planes ") + joined)
    return "; ".join(parts)


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


# ---------------------------------------------------------------------------
# The automatic OSC sensor (Siril only)
# ---------------------------------------------------------------------------

#: Colour cameras by their normalised INSTRUME (lower case, only a-z and 0-9), mapped to the model name Siril's SPCC
#: database holds for an OSC sensor. Colour tokens only (asi2600mc, qhy268c, a bare Sony part number): a mono model
#: (asi2600mm, qhy268m) never matches. A camera whose sensor the database lacks (ASI094MC is an IMX094, IMX461 is not
#: listed) is not guessed: it picks nothing and the user chooses. The first match wins.
OSC_SENSOR_TABLE: list[tuple[str, str]] = [
    (r"asi2600mc|qhy268c|imx571", "Sony IMX571"),
    (r"asi6200mc|qhy600c|imx455", "Sony IMX455"),
    (r"asi533mc|imx533", "Sony IMX533"),
    (r"asi294mc|qhy294c|imx294", "Sony IMX294"),
    (r"asi585mc|imx585", "Sony IMX585"),
    (r"asi2400mc|imx410", "Sony IMX410"),
    (r"asi678mc|imx678", "Sony IMX678"),
    (r"asi676mc|imx676", "Sony IMX676"),
    (r"asi662mc|imx662", "Sony IMX662"),
    (r"asi462mc|imx462", "Sony IMX462"),
    (r"asi482mc|imx482", "Sony IMX482"),
    (r"asi715mc|imx715", "Sony IMX715"),
    (r"asi385mc|imx385", "Sony IMX385"),
    (r"asi224mc|imx224", "Sony IMX224"),
    (r"asi183mc|imx183", "Sony IMX183"),
    (r"asi178mc|imx178", "Sony IMX178"),
    (r"asi071mc|imx071", "Sony IMX071"),
    (r"asi1600mc|mn34230", "Panasonic MN34230"),
]
_MONO_CAMERA = re.compile(r"asi[0-9]+mm|qhy[0-9]+m(?:$|[a-bd-z])")


def _instrume_text(instrume: Any) -> str:
    return "" if instrume is None else _trim(_str(instrume))


def auto_osc_sensor(instrume: Any) -> dict[str, str]:
    """The Siril OSC sensor a camera's INSTRUME names, and why: ``{"sensor", "reason"}``.

    ``sensor`` is "" for a camera it cannot tell (no INSTRUME, a mono camera, an unknown camera); ``reason`` always
    says what was decided, in words for the run log.
    """
    text = _instrume_text(instrume)
    if not text:
        return {"sensor": "", "reason": "the master has no INSTRUME keyword"}
    n = normalise_filter(text)
    for pattern, sensor in OSC_SENSOR_TABLE:
        if re.search(pattern, n):
            return {"sensor": sensor, "reason": "INSTRUME '" + text + "' is a " + sensor + " colour camera"}
    if _MONO_CAMERA.search(n):
        return {"sensor": "", "reason": "INSTRUME '" + text + "' is a mono camera"}
    return {"sensor": "", "reason": "INSTRUME '" + text + "' is an unknown camera"}


def common_osc_sensor(instrumes: Sequence[Any]) -> dict[str, str]:
    """``auto_osc_sensor`` of the camera every non-blank INSTRUME names; nothing when they name different cameras."""
    seen = [t for t in (_instrume_text(v) for v in instrumes) if t]
    if len(set(seen)) > 1:
        return {"sensor": "", "reason": "the masters name different cameras (" + ", ".join(sorted(set(seen))) + ")"
                                        " and differ in INSTRUME"}
    return auto_osc_sensor(seen[0] if seen else None)


def resolve_osc_sensor(config: dict[str, Any], instrume: Any) -> Optional[str]:
    """Fills the OSC sensor of a run that has none chosen from the camera, and returns the line for the run log.

    ``config["oscSensor"]["sensor"]`` is left alone when the user chose one (the line says so). Otherwise the sensor
    ``auto_osc_sensor`` names is set; an unknown camera leaves it blank (the run then asks for a choice). None
    for a run without an OSC master, which has no sensor to choose.
    """
    if "OSC" not in colour_supplied(config):
        return None
    sensor = config.get("oscSensor")
    if not isinstance(sensor, dict):
        sensor = {"sensor": "", "frontFilter": "", "lpf": ""}
    camera = _instrume_text(instrume)
    pick = auto_osc_sensor(instrume)
    shown = "INSTRUME '" + camera + "'" if camera else "no INSTRUME"
    if _truthy(sensor.get("sensor")):
        line = "OSC sensor: chosen, " + _str(sensor["sensor"]) + " (" + shown
        if pick["sensor"] and pick["sensor"] != sensor["sensor"]:
            line += "; automatic would be " + pick["sensor"]
        return line + ")"
    if pick["sensor"]:
        config["oscSensor"] = {**sensor, "sensor": pick["sensor"]}   # a new dict: the caller's own is never changed
        return "OSC sensor: automatic, " + pick["sensor"] + " (" + pick["reason"] + ")"
    return "OSC sensor: " + pick["reason"] + "; choose the sensor colour filters"
