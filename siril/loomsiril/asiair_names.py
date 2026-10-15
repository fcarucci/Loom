"""ASIAIR frame names, sessions, nights, flats matching and the import manifest: ``pixinsight/script/lib/AsiairNames.js``.

Ported line for line from the JavaScript, lines 1-331: the token patterns (STAMP, EXPOSURE, BIN, GAIN, ROTATION,
TEMP, SEQUENCE, EXTENSION), TYPES, ``tailFields``, ``lastMatch``, ``onlyMatch``, ``isKnownType``, ``parseName``,
``stampInRange``, ``stampRolled``, ``stampKey``, ``GAP_HOURS``, ``sessions``, ``filtersOf``, ``byTarget``,
``nights`` and ``dateOf``. Every name in the port is the JS name in snake_case; the frame, session and night
records keep the JS camelCase keys (``binToken``, ``sessionIndex``) so a record reads the same on both sides.

Ported from the JavaScript, lines 333-703 (appended below the first half):

* ``flatBatches`` (343-349), ``assignBatches`` (364-381), ``attributeFit`` (388-393), ``flatSuits`` (416-433),
  ``mustMatch`` (436-439) and ``matchFlats`` (441-463): flats are clustered among themselves, each batch goes whole
  to the nearest session, and each flat is matched per light filter by filter, binning, camera and rotation.
* ``isInside`` (472-489): containment on path components, never on string prefixes.
* ``importRoot`` (499-507): a chosen Light or Flat folder means its parent.
* ``exposureList`` (510-514) and ``darkSource`` (521-527): the phrases used in the import summary.
* ``masterFlatsProblem`` (537-555): whether the master-flats set-up can be used before anything is written.
* ``importSummary`` (562-579): the confirmation text. Its wording is the JavaScript's, which says XISF; the
  Siril port writes FITS copies, and a caller that shows this text must account for that.
* ``skipFiltersWithFailedCopies`` (590-608): a filter with a failed flat copy is skipped whole.
* ``manifest`` (620-703): the copy schedule for lights and flats, with collisions refused. Its ``extension``
  parameter is the destination extension; ``None`` keeps each source's own extension (a byte copy), and ``".xisf"``
  reproduces the JavaScript, which is what the golden cases record.
* The constants ``_ROOT_FOLDER`` and ``_TRAILING_SLASHES`` (the regular expressions of ``importRoot``) and the
  helpers ``_loose_eq`` and ``_str_to_number`` (JS ``==`` between null, booleans, numbers and strings).

The golden cases in ``tests/oracle/fs_asiair_names.json`` and ``tests/oracle/fs_asiair_plan.json`` were recorded by
``tools/oracle/fs_asiair_names.js`` and ``tools/oracle/fs_asiair_plan.js`` from the real library, and
``tests/test_asiair_names.py`` and ``tests/test_asiair_plan.py`` replay each one here.

Semantics kept exactly:

* JS regular expressions are ASCII: ``\\d`` is written ``[0-9]``, ``$`` (without the multiline flag) is ``\\Z``,
  and the extension match is case-insensitive over ASCII only, written out letter by letter.
* ``Date.UTC`` is reproduced by arithmetic (``_date_utc``): years 0-99 map to 1900 + year, and a day past the end
  of a month rolls over into the next month. ``_utc_fields`` reads the components back as ``getUTC*`` does.
* String comparison and default ``Array.prototype.sort`` order by UTF-16 code units (``_utf16_key``).
* ``null`` and ``undefined`` both arrive here as ``None``. A missing key on a frame is ``None`` too.
* The sort in ``sessions`` and ``nights`` is stable, as the JS sort is.

Semantics added for the second half:

* JS ``==`` and ``!=`` between values that may be strings or numbers go through ``_loose_eq``.
* ``toLowerCase`` is Python's ``str.lower``, which agrees with it on the names an ASIAIR writes.
* ``File.exists`` is a regular file and ``File.directoryExists`` a directory, as the PJSR shim reads them.
* ``null`` and ``undefined`` both arrive as ``None``; where the JS would print one or the other (``String(x)``), the
  port prints ``"null"``.
"""

from __future__ import annotations

import functools
import math
import os
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


# ---- the second half: flats matching, the import root and the manifest (AsiairNames.js 333-703) ------------------

_TRAILING_SLASHES = re.compile(r"/+\Z")
_ROOT_FOLDER = re.compile(r"(?:[lL][iI][gG][hH][tT]|[fF][lL][aA][tT])\Z")  # /^(light|flat)$/i, ASCII only


def _str_to_number(s: str) -> float:
    """JS ``Number(string)``: blank is 0, hex and the Infinity spellings parse, anything else not decimal is NaN."""
    t = s.strip()
    if t == "":
        return 0.0
    if re.match(r"0[xX][0-9a-fA-F]+\Z", t):
        return float(int(t[2:], 16))
    if t in ("Infinity", "+Infinity"):
        return math.inf
    if t == "-Infinity":
        return -math.inf
    if re.match(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z", t):
        return float(t)
    return math.nan


def _loose_eq(a: Any, b: Any) -> bool:
    """JS ``a == b`` for the values a header or a name can carry: null, booleans, numbers and strings."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool):
        a = float(a)
    if isinstance(b, bool):
        b = float(b)
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, str):
        a = _str_to_number(a)
    if isinstance(b, str):
        b = _str_to_number(b)
    return bool(a == b)  # NaN is unequal to itself, as in JS


def _js_round(x: float) -> float:
    """JS ``Math.round``: the nearest integer, a tie going toward +Infinity (Python's round goes to even)."""
    if not math.isfinite(x):
        return x
    f = math.floor(x)
    return float(f + 1) if x - f >= 0.5 else float(f)


def _ext_swap(name: str, extension: Optional[str]) -> str:
    """``name.replace(EXTENSION, extension)``; a None extension keeps the name's own (matched) extension text."""
    m = EXTENSION.search(name)
    if m is None:
        return name
    own = m.group(0) if extension is None else extension
    return name[:m.start()] + own + name[m.end():]


# ---- batches of flats (AsiairNames.js 333-381) -----------------------------------------------------------------

def flat_batches(flats: list[Record], gap_hours: Optional[float] = None) -> list[Record]:
    """Flats clustered among THEMSELVES by the gap, each batch with its midpoint ``mid``.

    Clustering the flats together with the lights would bridge two nights through a run of daytime flats.
    """
    raw = sessions(flats, gap_hours)
    for r in raw:
        r["mid"] = math.floor((r["first"] + r["last"]) / 2)
    return raw


def assign_batches(batches: list[Record], sessions_list: list[Record]) -> list[int]:
    """The index of the session each batch goes WHOLE to: the nearest to its midpoint, -1 when there is none.

    A batch exactly equidistant from two sessions goes to the earlier one (``<`` in the loop, not ``<=``).
    """
    out: list[int] = []
    for b in batches:
        best = -1
        best_dist = math.inf
        for s, sess in enumerate(sessions_list):
            mid = b["mid"]
            if mid < sess["first"]:
                d = sess["first"] - mid
            else:
                d = mid - sess["last"] if mid > sess["last"] else 0
            if d < best_dist:
                best_dist = d
                best = s
        out.append(best)
    return out


# ---- matching flats to lights (AsiairNames.js 383-463) ---------------------------------------------------------

def attribute_fit(a: Any, b: Any) -> str:
    """"same" (both stated and equal, or neither stated), "soft" (stated on one side only) or "clash"."""
    if a is not None and b is not None:
        return "same" if _loose_eq(a, b) else "clash"
    return "soft" if (a is not None or b is not None) else "same"


def must_match(a: Any, b: Any) -> bool:
    """An attribute that has to be stated on both sides, and equal."""
    return a is not None and b is not None and _loose_eq(a, b)


def flat_suits(want: Record, flat: Record) -> Optional[str]:
    """Does one flat suit one light filter? "exact", "weak" (camera or rotation stated on one side only), or None.

    Filter and binning must both be present and equal. Camera and rotation must agree where both sides state them.
    Gain is not compared: a flat is a ratio, and WBPP groups flats by gain itself.
    """
    if not must_match(want.get("filter"), flat.get("filter")) or not must_match(want.get("binning"), flat.get("binning")):
        return None

    soft = False
    for a, b in [(want.get("camera"), flat.get("camera")), (want.get("rotation"), flat.get("rotation"))]:
        fit = attribute_fit(a, b)
        if fit == "clash":
            return None
        if fit == "soft":
            soft = True
    return "weak" if soft else "exact"


def match_flats(light_filters: list[Record], flat_records: list[Record]) -> list[Record]:
    """Per light filter, the flats that suit it and the strength: "exact", "weak" or "missing"."""
    out: list[Record] = []
    for want in light_filters:
        hits: list[Record] = []
        weak = False
        for flat in flat_records:
            how = flat_suits(want, flat)
            if how is None:
                continue
            if how == "weak":
                weak = True
            hits.append(flat)
        out.append({
            "filter": want.get("filter"),
            "flats": hits,
            "strength": "missing" if not hits else ("weak" if weak else "exact"),
        })
    return out


# ---- paths and the import (AsiairNames.js 465-527) -------------------------------------------------------------

def _path_parts(p: Any) -> list[str]:
    out = js_string(p).split("/")
    while out and out[-1] == "":
        out.pop()
    return out


def is_inside(path: Any, root: Any) -> bool:
    """Containment on path COMPONENTS: ``/Volumes/ASIAIR-backup`` is not inside ``/Volumes/ASIAIR``."""
    a = _path_parts(path)
    b = _path_parts(root)
    if len(a) < len(b):
        return False
    for i in range(len(b)):
        if a[i] != b[i]:
            return False
    return True


def import_root(chosen: Any) -> Record:
    """The folder an import writes into: a chosen Light or Flat folder means its parent, with the name kept."""
    path = _TRAILING_SLASHES.sub("", js_string(chosen))
    cut = path.rfind("/")
    name = path[cut + 1:]
    if cut > 0 and _ROOT_FOLDER.match(name):
        return {"root": path[:cut], "chosen": name}
    return {"root": path, "chosen": None}


def exposure_list(exposures: list[Any]) -> str:
    """"15 s", "15 s and 30 s", "1.5 s, 15 s and 30 s"."""
    t = [js_string(_js_round(float(e) * 1000.0) / 1000.0) + " s" for e in exposures]
    if len(t) < 2:
        return "".join(t)
    return ", ".join(t[:-1]) + " and " + t[-1]


def dark_source(source: Record) -> str:
    """Where the flat-darks come from, as a phrase: the darks on the card, or the darks folder."""
    if _truthy(source.get("card")):
        exposures = source.get("exposures") or []
        listed = exposure_list(exposures) if len(exposures) > 0 else "no exposure stated"
        return "the " + js_string(source.get("count")) + " dark(s) on the ASIAIR card (" + listed + ")"
    return "the darks in " + js_string(source.get("darksFolder"))


# ---- the import itself (AsiairNames.js 529-703) ----------------------------------------------------------------

def master_flats_problem(darks_folder: Any, only_master_flats: Any, card_dark_paths: Optional[list[Any]]) -> Optional[str]:
    """The problem with the master-flats set-up, as a sentence, or None; checked before anything is written."""
    if not _truthy(only_master_flats):
        return None
    if card_dark_paths is not None:
        for p in card_dark_paths:
            if not os.path.isfile(js_string(p)):
                return ("A dark on the ASIAIR card cannot be found any more:\n\n" + js_string(p) +
                        "\n\nIs the card still in? Nothing has been written.")
        return None
    if darks_folder is None or len(js_string(darks_folder)) == 0:
        return None
    if not os.path.isdir(js_string(darks_folder)):
        return ("The darks folder cannot be found:\n\n" + js_string(darks_folder) +
                "\n\nChoose it again, or untick \"Combine flats to masters\". Nothing has been written.")
    return None


def import_summary(manifest: Record, chosen: Any, master_flats: Any) -> str:
    """The import's confirmation: the two folders frames land in, with their counts and the parent note."""
    r = import_root(chosen)
    lines: list[str] = []
    if r["chosen"] is not None:
        lines += ["Using " + r["root"] + " (the folder you chose is its " + r["chosen"] + " folder)", ""]
    lines += ["Written as XISF copies:",
              "Lights (" + str(len(manifest["lights"])) + ") → " + r["root"] + "/Light"]
    if _truthy(master_flats):
        lines += ["Flats (" + str(len(manifest["flats"])) + ") → one master flat per filter in " + r["root"] + "/Flat,",
                  "calibrated with " + dark_source(master_flats) + ";",
                  "the raw flat copies are deleted once each master is written and checked.",
                  "A filter with no matching dark, or whose master fails, keeps its raw flats.",
                  "Nothing on the card is changed."]
    else:
        lines.append("Flats (" + str(len(manifest["flats"])) + ") → " + r["root"] + "/Flat")
    return "\n".join(lines)


def skip_filters_with_failed_copies(manifest_flats: list[Record], failed_srcs: list[Any], jobs: list[Record]) -> list[Record]:
    """A master is built from ALL of a filter's flats: a filter with any failed copy is skipped, changed in place.

    A failed flat with no filter recorded cannot be placed, so it skips every filter.
    """
    failed = {js_string(s) for s in failed_srcs}
    lost: dict[str, int] = {}
    everything = 0
    for f in manifest_flats:
        if js_string(f.get("src")) not in failed:
            continue
        if f.get("filter") is None:
            everything += 1
        else:
            key = js_string(f.get("filter"))
            lost[key] = lost.get(key, 0) + 1
    for j in jobs:
        n = everything + lost.get(js_string(j.get("filter")), 0)
        if n > 0 and j.get("skip") is None:
            j["skip"] = js_string(n) + " flat copy(ies) failed to write; a master from the rest would be incomplete"
    return jobs


def manifest(approved_lights: list[Record], flat_matches: list[Record], destination: Any,
             extension: Optional[str] = None) -> Record:
    """The copy schedule: lights to ``Light/``, flats of the surviving filters to ``Flat/``, collisions refused.

    ``extension=None`` keeps each source's own extension (a byte copy); ``".xisf"`` is the JavaScript's output.
    Two DIFFERENT sources landing on one name (compared case-insensitively, as the destination folds case) refuse the
    whole schedule; the same source twice is written once.
    """
    dest = js_string(destination)

    surviving: set[str] = set()
    lights: list[Record] = []
    for a in approved_lights:
        surviving.add(js_string(a.get("filter")))
        lights.append({"src": a.get("path"), "dst": dest + "/Light/" + _ext_swap(js_string(a.get("path")).split("/")[-1], extension)})

    flats: list[Record] = []
    for m in flat_matches:
        if js_string(m.get("filter")) not in surviving:
            continue
        for fl in m.get("flats") or []:
            src = fl.get("path")
            flats.append({"src": src, "dst": dest + "/Flat/" + _ext_swap(js_string(src).split("/")[-1], extension),
                          "filter": m.get("filter")})

    seen: dict[str, Any] = {}
    collisions: list[Record] = []
    for item in lights + flats:
        key = js_string(item["dst"]).lower()
        if key in seen:
            if not _loose_eq(seen[key], item["src"]):
                collisions.append({"dst": item["dst"], "a": seen[key], "b": item["src"]})
        else:
            seen[key] = item["src"]

    if collisions:
        return {"lights": [], "flats": [], "collisions": collisions}

    def dedupe(items: list[Record]) -> list[Record]:
        out: list[Record] = []
        had: set[str] = set()
        for it in items:
            k2 = js_string(it["dst"]).lower()
            if k2 in had:
                continue
            had.add(k2)
            out.append(it)
        return out

    return {"lights": dedupe(lights), "flats": dedupe(flats), "collisions": []}
