"""Frame Selector: which frames are light subframes, how they group, and whether a group is comparable.

Ported from ``pixinsight/script/lib/Frames.js`` (``notSubframe`` line 935, ``calibrationKind`` 945, ``skippedCounts``
956, ``skippedLine`` 972, ``unreadableLine`` 983, ``noSubframesMessage`` 992, ``groupByFilter`` 1014,
``autoRejectAllowed`` 1039, ``normalImageType`` 1058, ``comparability`` 1064). Pure over plain dicts: no file, no
display. Pinned to the JavaScript by ``tests/oracle/fs_flags.json`` (recorded by ``tools/oracle/fs_flags.js``).

Two JS details kept on purpose. A missing key and a null value differ in ``comparability``: JS ``String(undefined)``
is "undefined", ``String(null)`` is "null", and a group that mixes them is a mixed group. ``groupByFilter`` returns
a dict in first-seen order; the JS object sorts integer-like keys first, so compare the groups, not the order.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from loomsiril.frames import NO_FILTER, auto_reject_allowed
from loomsiril.jsmath import js_string

Entry = dict[str, Any]

__all__ = [
    "NO_FILTER", "auto_reject_allowed", "CALIBRATION_KINDS", "not_subframe", "calibration_kind", "skipped_counts",
    "skipped_line", "unreadable_line", "no_subframes_message", "group_by_filter", "normal_image_type",
    "comparability",
]

#: Calibration words, first match wins; a name-derived subject must be one whole word (see calibration_kind).
CALIBRATION_KINDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"flat", re.I), "flat"),
    (re.compile(r"dark", re.I), "dark"),
    (re.compile(r"bias|offset|zero", re.I), "bias"),
]

_WHOLE_WORD = re.compile(r"(flat|dark|bias|darkflat|flatdark|offset)", re.I)
_NAME_SUBJECT = re.compile(r"([A-Za-z]+)[_ -]")


def _text(v: Any) -> str:
    """``String(v || "")`` in JS: every falsy value (null, 0, "") is the empty string."""
    return js_string(v) if v else ""


def not_subframe(name: Any, image_type: Any) -> Optional[str]:
    """``"master"``, the calibration kind (``flat``, ``dark``, ``bias``), or None for a light subframe."""
    n = _text(name)
    t = _text(image_type).replace("'", "").strip()
    if re.match(r"(master|integration)", n, re.I) or re.search(r"master", t, re.I):
        return "master"
    if len(t) > 0:
        subject = t
    else:
        m = _NAME_SUBJECT.match(n)
        subject = m.group(1) if m else ""
    return calibration_kind(subject, len(t) > 0)


def calibration_kind(subject: str, from_image_type: bool) -> Optional[str]:
    """The calibration kind a subject names. A subject taken from the file name must be one whole word."""
    if not from_image_type and not _WHOLE_WORD.fullmatch(subject):
        return None
    for pattern, kind in CALIBRATION_KINDS:
        if pattern.search(subject):
            return kind
    return None


def skipped_counts(skipped: list[dict[str, Any]]) -> str:
    """``"2 flats, 1 master"``: the skipped files counted by kind, in first-seen order."""
    counts: dict[str, int] = {}
    for s in skipped:
        counts[s["kind"]] = counts.get(s["kind"], 0) + 1
    parts = []
    for kind, n in counts.items():
        plural = "" if n == 1 else ("es" if kind == "bias" else "s")
        parts.append(f"{n} {kind}{plural}")
    return ", ".join(parts)


def skipped_line(skipped: Optional[list[dict[str, Any]]]) -> str:
    """The console line for files left out of a scan; "" when none were."""
    if skipped is None or len(skipped) == 0:
        return ""
    one = len(skipped) == 1
    return (f"Skipped {len(skipped)}"
            + (" file that is not a light subframe: " if one else " files that are not light subframes: ")
            + skipped_counts(skipped) + ".")


def unreadable_line(paths: Optional[list[str]]) -> str:
    """The console line for files a scan could not read; "" when there were none."""
    if paths is None or len(paths) == 0:
        return ""
    return f"Could not read {len(paths)}" + (" file; it is left out." if len(paths) == 1 else " files; they are left out.")


def no_subframes_message(skipped: Optional[list[dict[str, Any]]]) -> str:
    """What to say when a folder gave the review nothing to show."""
    if skipped is None or len(skipped) == 0:
        return "No readable frames in that folder."
    return ("This folder holds no light subframes: " + skipped_counts(skipped) + ".\n\n"
            "The Frame Selector reviews the individual subframes of a night. "
            "Point it at the folder of lights, not at masters or calibration frames.")


def group_by_filter(entries: list[Entry]) -> dict[str, list[Entry]]:
    """Frames grouped by their RAW filter string; no readable filter is the ``NO_FILTER`` group.

    Not ``Util.channelFromFilter``: that maps to Loom's seven channels and drops unfamiliar filters, so here a
    group is a filter, whatever it is called.
    """
    out: dict[str, list[Entry]] = {}
    for e in entries:
        f = e.get("filter")
        key = NO_FILTER if (f is None or js_string(f).strip() == "") else js_string(f).strip()
        out.setdefault(key, []).append(e)
    return out


def normal_image_type(t: Any) -> str:
    """An IMAGETYP as the one word it means: "Light Frame", "'LIGHT '" and "light" are all "light"."""
    s = "" if t is None else js_string(t)
    s = re.sub(r"\s+", " ", s.replace("'", " ").strip().lower())
    return re.sub(r" (frame|field)\Z", "", s)


def comparability(group: Optional[list[Entry]]) -> dict[str, Any]:
    """Whether the frames of one group can be compared: ``{"uniform": bool, "problems": [...]}``.

    A mismatch is reported, not refused: the figures are read knowing it. A uniformly unknown calibration state is
    not a problem; only a genuine mixture of raw and calibrated is.
    """
    if group is None or len(group) == 0:
        return {"uniform": True, "problems": []}

    def distinct(field: str, norm: bool = False) -> int:
        seen: set[str] = set()
        for e in group:
            if norm:
                v = normal_image_type(e.get(field))
            elif field not in e:
                # A missing key is JS undefined ("undefined"); a present null is "null". Both are values of their own.
                v = "undefined"
            else:
                v = js_string(e[field])
            seen.add(v)
        return len(seen)

    problems: list[str] = []
    if distinct("exposure") > 1:
        problems.append("mixed exposure times")
    if distinct("binning") > 1 or distinct("binningY") > 1:
        problems.append("mixed binning")
    if distinct("imageType", norm=True) > 1:
        problems.append("mixed image types")
    if distinct("width") > 1 or distinct("height") > 1:
        problems.append("mixed image geometry")
    if distinct("calibrated") > 1:
        problems.append("mixed calibration state")
    return {"uniform": len(problems) == 0, "problems": problems}
