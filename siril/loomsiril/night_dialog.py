"""The night picker's data, ported from ``pixinsight/script/lib/NightDialog.js``: the pure half, without any widget.

Ported from the JavaScript, lines 1-119:

* ``clockOf`` (NightDialog.js 24-27): the local clock of a stamp key, ``HH:MM``, for labelling a span.
* ``rowFor`` (33-41): what one night reads as in the tree (date, span, count, filters).
* ``flatSummary`` (48-64): how the flats for a night read; a filter with no flats stays visible as "missing".
* ``surveyOf`` (71-99): a card as plain data: its sessions, nights, targets, the flat batches owned by each
  session, and the unparseable names carried through unchanged.
* ``RECENT`` (106): the number of nights the picker shows per target.
* ``recentNights`` (107-113): a target's newest nights, newest first.
* ``flatsForNight`` (116-119): the flats offered for one night, before headers are consulted.

The Tk picker (NightDialog.js 127-294) is not ported here. It lives in its own module, so this one imports nothing
from tkinter and runs under any Python 3.10+ with the package's dependencies.

``surveyOf`` calls the ASIAIR name functions (``loomsiril.asiair_names``: ``sessions``, ``nights``, ``flat_batches``,
``assign_batches``), which are ported from ``pixinsight/script/lib/AsiairNames.js``.

Semantics kept exactly:

* A JS object used as a map has its keys as strings. ``flatsBySession`` is keyed by the session index as text, so a
  lookup by index and a lookup by the string of that index agree, and the survey holds string keys like the JS does.
* ``surveyOf`` tracks the targets it has seen in a plain JS object (``seen = {}``), so a target whose name is a
  property every plain object inherits (``constructor``, ``toString``, ...) is never listed as a target. The
  picker's tree and a card scan therefore omit such a target from ``targets`` while ``nights`` still holds it.
  ``_PLAIN_OBJECT_KEYS`` lists those names.
* ``recentNights`` takes ``k || RECENT``: a zero, a missing or a false ``k`` gives the default, and a negative ``k``
  slices from the end as ``Array.prototype.slice`` does.
* Sorting is stable, as the JS sort is; the comparisons are plain string comparisons on ASCII dates and spans.
* ``String(n)`` for a count, and ``String(x)`` for any value printed, come from ``loomsiril.jsmath.js_string``.
"""

from __future__ import annotations

from typing import Any, Optional

from loomsiril import asiair_names as A
from loomsiril.jsmath import js_string

Survey = dict[str, Any]
Night = dict[str, Any]
Row = dict[str, str]

#: How many nights the picker shows for a target (NightDialog.js 106).
RECENT = 3

#: The names every plain JS object inherits from Object.prototype. ``key in {}`` is true for each of them.
_PLAIN_OBJECT_KEYS = frozenset({
    "constructor", "__proto__", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable", "toString",
    "valueOf", "toLocaleString", "__defineGetter__", "__defineSetter__", "__lookupGetter__", "__lookupSetter__",
})


def clock_of(stamp: str) -> str:
    """The local clock time of a stamp key, as ``HH:MM`` (NightDialog.js 24-27)."""
    return stamp[9:11] + ":" + stamp[11:13]


def row_for(night: Night) -> Row:
    """What one night reads as in the tree: its date, its span, its frame count and its filters (NightDialog.js 33-41)."""
    frames = night["frames"]
    start = clock_of(frames[0]["stamp"])
    end = clock_of(frames[-1]["stamp"])
    return {
        "date": night["date"],
        "span": start + "-" + end,
        "count": js_string(night["count"]),
        "filters": " ".join(night["filters"]),
    }


def flat_summary(matches: list[Any]) -> list[dict[str, Any]]:
    """How the flats for a night read, one entry per filter in the order given (NightDialog.js 48-64).

    ``missing`` is a warning and not an absence: a filter with no flats must stay visible, because that is what the
    observer needs to see before an import. A ``weak`` match is marked as a partial match; one flat is singular.
    """
    out: list[dict[str, Any]] = []
    for m in matches:
        n = len(m["flats"])
        filter_text = js_string(m["filter"])
        if m["strength"] == "missing":
            text = filter_text + ": no flats"
        else:
            text = (filter_text + ": " + str(n) + " flat" + ("" if n == 1 else "s")
                    + (" (partial match)" if m["strength"] == "weak" else ""))
        out.append({"filter": m["filter"], "count": n, "strength": m["strength"], "text": text})
    return out


def survey_of(scan: dict[str, Any], gap_hours: Optional[float] = None) -> Survey:
    """A card as plain data: the targets, their nights, and which flat batches belong to each session (NightDialog.js 71-99).

    ``scan`` holds ``lights`` and ``flats`` (frame records as the ASIAIR name parser makes them) and ``unparseable``.
    Each flat batch goes whole to its nearest session, and a batch with no session to go to is owned by nobody.
    """
    sessions = A.sessions(scan["lights"], gap_hours)
    nights = A.nights(sessions)
    batches = A.flat_batches(scan["flats"], gap_hours)
    owners = A.assign_batches(batches, sessions)

    flats_by_session: dict[str, list[Any]] = {}
    for b, batch in enumerate(batches):
        owner = owners[b]
        if owner < 0:
            continue
        key = str(owner)
        flats_by_session[key] = flats_by_session.get(key, []) + batch["frames"]

    targets: list[Any] = []
    seen: set[str] = set()
    for n in nights:
        t = n["target"]
        if t in seen or t in _PLAIN_OBJECT_KEYS:
            continue
        seen.add(t)
        targets.append(t)

    return {
        "sessions": sessions,
        "nights": nights,
        "targets": targets,
        "flatsBySession": flats_by_session,
        "unparseable": scan.get("unparseable"),
    }


def recent_nights(survey: Survey, target: Any, k: Optional[int] = None) -> list[Night]:
    """A target's most recent ``k`` nights, newest first (NightDialog.js 107-113).

    ``k`` falls back to :data:`RECENT` when it is missing or falsy, as ``k || RECENT`` does.
    """
    mine = [n for n in survey["nights"] if n["target"] == target]

    def key(n: Night) -> str:
        r = row_for(n)
        return r["date"] + " " + r["span"]

    mine.sort(key=key, reverse=True)
    return mine[: (k if k else RECENT)]


def flats_for_night(survey: Survey, night: Night) -> list[Any]:
    """The flats offered for one night, before headers are consulted (NightDialog.js 116-119)."""
    return survey["flatsBySession"].get(js_string(night["sessionIndex"])) or []
