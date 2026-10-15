"""ASIAIR frame names, sessions and nights: the first half of ``pixinsight/script/lib/AsiairNames.js``.

Ported line for line from the JavaScript, lines 1-331: the token patterns (STAMP, EXPOSURE, BIN, GAIN, ROTATION,
TEMP, SEQUENCE, EXTENSION), TYPES, ``tailFields``, ``lastMatch``, ``onlyMatch``, ``isKnownType``, ``parseName``,
``stampInRange``, ``stampRolled``, ``stampKey``, ``GAP_HOURS``, ``sessions``, ``filtersOf``, ``byTarget``,
``nights`` and ``dateOf``. Every name in the port is the JS name in snake_case; the frame, session and night
records keep the JS camelCase keys (``binToken``, ``sessionIndex``) so a record reads the same on both sides.

The golden cases in ``tests/oracle/fs_asiair_names.json`` were recorded by ``tools/oracle/fs_asiair_names.js``
from the real library, and ``tests/test_asiair_names.py`` replays each one here.

Semantics kept exactly:

* JS regular expressions are ASCII: ``\\d`` is written ``[0-9]``, ``$`` (without the multiline flag) is ``\\Z``,
  and the extension match is case-insensitive over ASCII only, written out letter by letter.
* ``Date.UTC`` is reproduced by arithmetic (``_date_utc``): years 0-99 map to 1900 + year, and a day past the end
  of a month rolls over into the next month. ``_utc_fields`` reads the components back as ``getUTC*`` does.
* String comparison and default ``Array.prototype.sort`` order by UTF-16 code units (``_utf16_key``).
* ``null`` and ``undefined`` both arrive here as ``None``. A missing key on a frame is ``None`` too.
* The sort in ``sessions`` and ``nights`` is stable, as the JS sort is.

Out of this module, by design: flats matching, the import root and the manifest (AsiairNames.js 333-703), which
are appended below the section marked at the end of this file.
"""

from __future__ import annotations

import functools
import re
from typing import Any, Optional

from loomsiril.jsmath import js_string

Record = dict[str, Any]

# ---- the token patterns (AsiairNames.js 35-42) -----------------------------------------------------------------
# JS `\d` is ASCII only and JS `$` without the multiline flag matches only at the very end of the string, so
# `\Z` is used for `$` (Python's `$` also matches before a trailing newline).

STAMP = re.compile(r"^[0-9]{8}-[0-9]{6}\Z")
EXPOSURE = re.compile(r"^[0-9]+(\.[0-9]+)?(s|ms)\Z")
BIN = re.compile(r"^Bin[0-9]+\Z")
GAIN = re.compile(r"^gain[0-9]+\Z")
ROTATION = re.compile(r"^[0-9]+deg\Z")
TEMP = re.compile(r"^-?[0-9]+(\.[0-9]+)?C\Z")
SEQUENCE = re.compile(r"^[0-9]+\Z")
# JS /\.(fit|fits)$/i, written without re.IGNORECASE: Unicode case folding would also match a long s.
EXTENSION = re.compile(r"\.[fF][iI][tT][sS]?\Z")

# The only frame types an ASIAIR writes (AsiairNames.js 43).
TYPES: list[str] = ["Light", "Flat", "Dark", "Bias"]

GAP_HOURS = 4

_MS_PER_DAY = 86_400_000


def _utf16_key(s: str) -> bytes:
    """Sort key that orders strings by UTF-16 code units, as JS string comparison does."""
    return s.encode("utf-16-be", "surrogatepass")


def _truthy(v: Any) -> bool:
    """JS truthiness: null, undefined, false, 0, NaN and the empty string are falsy."""
    if v is None or isinstance(v, bool):
        return bool(v)
    if isinstance(v, float):
        return not (v != v or v == 0.0)
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        return v != ""
    return True


# ---- tail, anchors and types (AsiairNames.js 56-105) ----------------------------------------------------------

def tail_fields(tail: list[str]) -> Optional[dict[str, Optional[str]]]:
    """Rotation, temperature and sequence from the tail after the stamp; None when ambiguous or incomplete."""
    out: dict[str, Optional[str]] = {"rotation": None, "temp": None, "sequence": None}
    kinds = [("rotation", ROTATION), ("temp", TEMP), ("sequence", SEQUENCE)]

    for tok in tail:
        for name, rx in kinds:
            if rx.search(tok):
                if out[name] is not None:
                    return None  # stated twice: which one is true?
                out[name] = tok
                break

    return None if (out["temp"] is None or out["sequence"] is None) else out


def last_match(tokens: list[str], rx: re.Pattern[str], before: int) -> int:
    """The index of the last token at or before ``before`` that matches, or -1."""
    for i in range(before, -1, -1):
        if rx.search(tokens[i]):
            return i
    return -1


def only_match(tokens: list[str], rx: re.Pattern[str]) -> int:
    """The index of the ONLY token matching, -1 for none, -2 for ambiguous."""
    at = -1
    for i in range(len(tokens)):
        if rx.search(tokens[i]):
            if at >= 0:
                return -2
            at = i
    return at


def is_known_type(token: Any) -> bool:
    """Whether the token is one of TYPES, compared case-insensitively."""
    low = js_string(token).lower()
    return any(low == t.lower() for t in TYPES)


# ---- parsing (AsiairNames.js 107-161) -------------------------------------------------------------------------

def parse_name(filename: Any) -> Optional[Record]:
    """Parse an ASIAIR frame name; None for anything that is not an ASIAIR frame or whose shape is ambiguous.

    ``binToken`` is the raw filename token ("Bin1"), not the FITS keyword value.
    """
    s = js_string(filename)
    if not EXTENSION.search(s):
        return None

    t = EXTENSION.sub("", s, count=1).split("_")
    if not is_known_type(t[0]):
        return None

    # The stamp anchors everything. Two of them, or none, is not ours.
    at = only_match(t, STAMP)
    if at < 2:
        return None

    # gain sits immediately before the stamp, the filter before gain.
    if not GAIN.search(t[at - 1]):
        return None

    # The bin token is the last before the filter; the exposure the last before the bin.
    bin_at = last_match(t, BIN, at - 3)
    if bin_at < 0:
        return None
    exp_at = last_match(t, EXPOSURE, bin_at - 1)
    if exp_at < 1:
        return None

    tail = tail_fields(t[at + 1:])
    if tail is None:
        return None

    return {
        "type": t[0],
        "target": "_".join(t[1:exp_at]),
        "exposure": t[exp_at],
        "binToken": t[bin_at],
        "camera": "_".join(t[bin_at + 1:at - 2]) if bin_at + 1 <= at - 3 else None,
        "filter": t[at - 2],
        "gain": t[at - 1],
        "stamp": t[at],
        "rotation": tail["rotation"],
        "temp": tail["temp"],
        "sequence": tail["sequence"],
    }


# ---- timestamps (AsiairNames.js 163-219) ----------------------------------------------------------------------

def stamp_in_range(mo: int, d: int, h: int, mi: int, se: int) -> bool:
    """Whether each printed component is inside its calendar range (the year has none)."""
    return not (mo < 1 or mo > 12 or d < 1 or d > 31 or h > 23 or mi > 59 or se > 59)


def _days_from_civil(y: int, m: int, d: int) -> int:
    """Days since 1970-01-01 of the proleptic Gregorian date y-m-d (m is 1-based)."""
    y -= 1 if m <= 2 else 0
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(z: int) -> tuple[int, int, int]:
    """(year, month 1-based, day) of a day count since 1970-01-01."""
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    return (y + (1 if m <= 2 else 0), m, d)


def _date_utc(y: int, mo: int, d: int, h: int, mi: int, se: int) -> int:
    """``Date.UTC(y, mo, d, h, mi, se)`` in milliseconds; ``mo`` is 0-based as in JS.

    Years 0-99 mean 1900 + year, and an out-of-range day or month rolls over, as the JS does.
    """
    if 0 <= y <= 99:
        y += 1900
    y += mo // 12
    mo = mo % 12
    days = _days_from_civil(y, mo + 1, 1) + d - 1
    return ((days * 24 + h) * 60 + mi) * 60_000 + se * 1000


def _utc_fields(ms: int) -> tuple[int, int, int]:
    """The UTC (year, month 1-based, day) of a millisecond time, as getUTCFullYear/Month/Date read it."""
    return _civil_from_days(ms // _MS_PER_DAY)


def stamp_rolled(ms: int, y: int, mo: int, d: int) -> bool:
    """Did Date.UTC roll the printed date (year y, month mo 1-based, day d) into another month or day?"""
    fy, fm, fd = _utc_fields(ms)
    return fy != y or fm != mo or fd != d


def stamp_key(stamp: Any) -> Optional[int]:
    """A comparison key in SECONDS from the wall-clock components of a stamp, via Date.UTC; None if unreadable.

    UTC arithmetic on the printed components, never a local-zone parse. A 31 February or 30 February is
    refused rather than rolled into March.
    """
    s = js_string(stamp)
    if not STAMP.search(s):
        return None

    y = int(s[0:4], 10)
    mo = int(s[4:6], 10)
    d = int(s[6:8], 10)
    h = int(s[9:11], 10)
    mi = int(s[11:13], 10)
    se = int(s[13:15], 10)

    if not stamp_in_range(mo, d, h, mi, se):
        return None

    ms = _date_utc(y, mo - 1, d, h, mi, se)

    # A rolled date is not the date that was printed on the card, so it is not accepted.
    if stamp_rolled(ms, y, mo, d):
        return None

    return ms // 1000


# ---- sessions, nights and their helpers (AsiairNames.js 221-331) ----------------------------------------------

def sessions(frames: list[Record], gap_hours: Optional[float] = None) -> list[Record]:
    """Cluster by gap: a new session wherever consecutive frames are more than ``gap_hours`` apart.

    Frames with a None key are excluded rather than sorted to the front. Only lights are meant to be passed in.
    """
    gap = (GAP_HOURS if gap_hours is None else gap_hours) * 3600

    usable = [f for f in frames if f.get("key") is not None]
    usable.sort(key=lambda f: f["key"])

    out: list[Record] = []
    for f in usable:
        last = out[-1] if out else None
        if last is None or f["key"] - last["last"] > gap:
            out.append({"first": f["key"], "last": f["key"], "frames": [f]})
        else:
            last["last"] = f["key"]
            last["frames"].append(f)
    return out


def filters_of(frames: list[Record]) -> list[Any]:
    """The distinct filters in a set of frames, sorted, without duplicates."""
    seen: set[str] = set()
    out: list[Any] = []
    for f in frames:
        flt = f.get("filter")
        if _truthy(flt) and js_string(flt) not in seen:
            seen.add(js_string(flt))
            out.append(flt)
    out.sort(key=lambda v: _utf16_key(js_string(v)))
    return out


def by_target(frames: list[Record]) -> dict[str, Any]:
    """Frames grouped by target, in the order the targets first appear: ``{"order": [...], "groups": {...}}``."""
    groups: dict[str, list[Record]] = {}
    order: list[Any] = []
    for f in frames:
        t = f.get("target")
        key = js_string(t)
        if key not in groups:
            groups[key] = []
            order.append(t)
        groups[key].append(f)
    return {"order": order, "groups": groups}


def nights(sessions_: list[Record]) -> list[Record]:
    """One night per target per session, sorted by first frame then by target (UTF-16 order)."""
    out: list[Record] = []
    for s, sess in enumerate(sessions_):
        by = by_target(sess["frames"])
        for target in by["order"]:
            g = by["groups"][js_string(target)]
            out.append({
                "sessionIndex": s,
                "target": target,
                "first": g[0]["key"],
                "last": g[-1]["key"],
                "count": len(g),
                "filters": filters_of(g),
                "date": date_of(g[0]["stamp"]),
                "frames": g,
            })

    def compare(a: Record, b: Record) -> float:
        d = a["first"] - b["first"]
        if d != 0 and d == d:  # JS: a non-zero, non-NaN difference decides; otherwise fall through
            return d
        ka, kb = _utf16_key(js_string(a["target"])), _utf16_key(js_string(b["target"]))
        return -1 if ka < kb else (1 if ka > kb else 0)

    out.sort(key=functools.cmp_to_key(compare))
    return out


def date_of(stamp: str) -> str:
    """The printed date of a stamp as ``YYYY-MM-DD``."""
    return stamp[0:4] + "-" + stamp[4:6] + "-" + stamp[6:8]


# ---- end of the first half: AsiairNames.js 1-331 ----------------------------------------------------------------
# Flats matching, the import root and the manifest (AsiairNames.js 333-703) are appended below this line.
