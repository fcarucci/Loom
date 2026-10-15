"""Frame Selector: the review state and the dialog's decisions that are not drawing (loomsiril.frames_state).

Ported from ``pixinsight/script/FrameSelector.js`` (the review state, lines 1930-2025 and the dialog members named
below) and from ``pixinsight/script/lib/Frames.js`` and ``lib/Util.js`` where a member calls them. Pure over plain
dicts: no widget, no file except what the injected scan reads, no Siril call. Pinned by ``tests/test_frames_state.py``.

The state is the dict the JavaScript builds: ``folder``, ``channels`` (by filter), ``order`` (the sorted filter
names), ``destination``, ``phase``, ``locked``, ``unstable``, ``skipped``, ``cancelled`` and the import fields
``cardRoot``, ``candidateFlats``, ``darksFolder``, ``onlyMasterFlats``, ``cardDarks``, ``cardDarkMatch``. Keys keep the
JS camelCase, as in :mod:`loomsiril.frames`.

Functions, JS name to Python name, with the JS lines:

* ``FrameSelector.emptyState`` (FrameSelector.js 1938-1964) -> :func:`empty_state`
* ``FrameSelector.buildStateFrom`` (1973-1978) -> :func:`build_state_from`; ``scanPaths`` is the injected
  ``scan_paths(paths, progress)``.
* ``FrameSelector.stateFromScan`` (1980-1995) -> :func:`state_from_scan`. Also sets each channel's ``flags``
  (``Frames.newChannel``, lib/Frames.js 1607-1609, which :mod:`loomsiril.frames` leaves to the caller).
* ``FrameSelector.buildState`` (1997-2001) -> :func:`build_state`; ``scan`` is the injected ``scan(folder, progress)``.
* ``FrameSelector.approvedLightRecords`` (2010-2025) -> :func:`approved_light_records`
* ``FrameSelector.cardDarksFound`` (2148-2151) -> :func:`card_darks_found`
* ``Dialog.currentTags`` (FrameSelector.js 2492-2499) -> :func:`current_tags`
* ``Dialog.editable`` (3072-3074) -> :func:`editable`
* ``Dialog.committableRows`` (3237-3251) -> :func:`committable_rows`
* ``Dialog.recomputeAll`` (3286-3303) -> :func:`recompute_all`
* ``Dialog.summaryText`` (3306-3315) -> :func:`summary_text`
* ``Dialog.destinationText`` (3337-3350) -> :func:`destination_text`. Text only. After a delete in place the kept
  frames are not converted to XISF, so the text does not say they are (the JS clause for that is left out).
* ``Dialog.allPaths`` (3354-3363) -> :func:`all_paths`
* ``Dialog.destinationIsSource`` (3370-3372) -> :func:`destination_is_source`; ``Frames.destinationIsSource``
  (lib/Frames.js 1512-1520) is :func:`loomsiril.frames_files.destination_is_source`.
* ``Dialog.copyingOut`` (3433-3435) -> :func:`copying_out`
* ``Dialog.approvedPaths`` (3439-3452) -> :func:`approved_paths`
* ``Dialog.importing`` (3606-3608) -> :func:`importing`
* ``Dialog.masterFlatsMode`` (3578-3583) -> :func:`master_flats_mode`
* ``Dialog.matchedFlats`` (3732-3750) -> :func:`matched_flats`; ``entryFor`` and ``Asiair.describe`` are passed in as
  ``entry_for`` and ``describe_frame``, since they read headers.
* ``Dialog.flagLine`` (3167-3177) -> :func:`flag_line`

Helpers ported with them: ``Dialog.channel`` (3069-3071) is :func:`channel_of`; ``Util.elideHead`` (lib/Util.js
963-970) is :func:`elide_head`.

``SHOWN_METRICS`` is :data:`loomsiril.frames.DISPLAY_METRICS` without ``snrWeight``: the Siril measurement has no
SNR weight, so the dialog never shows that column.

Where a JS member reads ``this.current`` or ``this.state``, the Python function takes ``current`` (the selected
filter name, or None) and ``state`` explicitly. ``recompute_all`` changes the channels in place, as the JS does.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Optional

from loomsiril import frames as F
from loomsiril import frames_files
from loomsiril.asiair_names import match_flats
from loomsiril.frames_flags import anomaly_flags, flag_summary, frame_tags, obs_time
from loomsiril.jsmath import js_string
from loomsiril.master_flat import active as master_flat_active

State = dict[str, Any]
Scan = dict[str, Any]
Totals = dict[str, Any]

SHOWN_METRICS: list[str] = [m for m in F.DISPLAY_METRICS if m != "snrWeight"]

_PATH_WIDTH = 40  # the destination's width in the dialog, in characters (destinationText)


def empty_state(folder: Optional[str]) -> State:
    """A review with no channels yet (FrameSelector.js 1938-1964). The destination starts as the folder itself."""
    return {"folder": folder, "channels": {}, "order": [],
            "destination": folder,
            "preset": F.DEFAULT_PRESET,
            "phase": F.PHASE["REVIEW"], "locked": False,
            "manifest": None, "unstable": [],
            "cardRoot": None, "candidateFlats": [],
            "darksFolder": "", "onlyMasterFlats": False,
            "cardDarks": [], "cardDarkMatch": None}


def build_state_from(paths: list[str], label: Optional[str], progress: Any,
                     scan_paths: Callable[[list[str], Any], Scan]) -> State:
    """The review of an explicit list of frames, shown under ``label`` (FrameSelector.js 1973-1978)."""
    return state_from_scan(empty_state(label), scan_paths(paths, progress))


def state_from_scan(state: State, scan: Scan) -> State:
    """Fill ``state`` from a scan (FrameSelector.js 1980-1995).

    ``scan`` is ``{channels: {filter: {entries, metrics, problems}}, unstable, skipped, cancelled}``. Channels are
    added in sorted filter order. Each one's flags are computed from its measurements; a channel that is not
    comparable (it has problems, or it is the group with no FILTER) gets none.
    """
    state["cancelled"] = bool(scan.get("cancelled"))
    state["unstable"] = scan.get("unstable") or []
    state["skipped"] = scan.get("skipped") or []
    keys = sorted(scan["channels"])
    for key in keys:
        c = scan["channels"][key]
        ch = F.recompute(F.new_channel(key, c["entries"], c["metrics"], c["problems"]))
        comparable = len(ch["problems"]) == 0 and F.auto_reject_allowed(key)
        ch["flags"] = anomaly_flags([r["metrics"] for r in ch["rows"]], comparable,
                                    [obs_time(r["time"]) for r in ch["rows"]])
        state["channels"][key] = ch
        state["order"].append(key)
    return state


def build_state(folder: str, progress: Any, scan: Callable[[str, Any], Scan]) -> State:
    """The review of a folder (FrameSelector.js 1997-2001). ``scan(folder, progress)`` returns the scan dict."""
    return state_from_scan(empty_state(folder), scan(folder, progress))


def approved_light_records(state: State) -> list[dict[str, str]]:
    """``{path, filter}`` for every frame of an enabled channel that is not rejected (FrameSelector.js 2010-2025)."""
    out: list[dict[str, str]] = []
    for key in state["order"]:
        ch = state["channels"][key]
        if not ch["settings"]["enabled"]:
            continue
        for row in ch["rows"]:
            if F.final_state(row["state"], row.get("override")) != F.REJECTED:
                out.append({"path": row["path"], "filter": key})
    return out


def card_darks_found(state: State) -> bool:
    """Do the card's darks stand in for a darks folder? (FrameSelector.js 2148-2151)."""
    match = state["cardDarkMatch"]
    return state["cardRoot"] is not None and match is not None and bool(match.get("found"))


def channel_of(state: State, current: Optional[str]) -> Optional[dict[str, Any]]:
    """The channel the dialog shows, or None (lookups by a missing name give None, as ``channels[undefined]``)."""
    if current is None:
        return None
    return state["channels"].get(current)


def current_tags(state: State, current: Optional[str], index: int) -> list[dict[str, str]]:
    """The selected frame's tags: what Run does with it, then each anomaly (FrameSelector.js 2492-2499)."""
    ch = channel_of(state, current)
    if ch is None or index < 0 or index >= len(ch["rows"]):
        return []
    flags = ch["flags"][index] if ch.get("flags") else []
    return frame_tags(ch["rows"][index], flags, ch["settings"]["enabled"], copying_out(state))


def editable(state: State) -> bool:
    """Locked once a run has started, and past the review phase (FrameSelector.js 3072-3074)."""
    return F.can_edit(state["phase"]) and not state["locked"]


def committable_rows(state: State) -> dict[str, Any]:
    """The rows a run would act on, and the per-channel rejected counts for the confirmation (3237-3251).

    A channel switched off is left out entirely. A condemned override is a rejected row; a rescued one is not.
    """
    rows: list[dict[str, Any]] = []
    per_channel: list[str] = []
    for key in state["order"]:
        ch = state["channels"][key]
        if not ch["settings"]["enabled"]:
            continue
        c = F.counts(ch["rows"])
        if c["rejected"]:
            per_channel.append(key + ": " + str(c["rejected"]))
        rows.extend(ch["rows"])
    return {"rows": rows, "perChannel": per_channel}


def recompute_all(state: State) -> Totals:
    """Every channel's verdicts from its cohort, and the totals a run acts on (FrameSelector.js 3286-3303).

    Recomputed from the cohort, never from the survivors, so a frame nobody looked at is not condemned by a
    tighter spread. ``approved`` is ``total - condemned`` over every channel, switched-off ones included.
    """
    t: Totals = {"total": 0, "condemned": 0, "mixed": []}
    for key in state["order"]:
        ch = state["channels"][key]
        F.recompute(ch)
        c = F.counts(ch["rows"])
        t["total"] += c["total"]
        if not ch["settings"]["enabled"]:
            continue
        t["condemned"] += c["rejected"]
        if ch["problems"] and c["rejected"]:
            t["mixed"].append(key)
    t["approved"] = t["total"] - t["condemned"]
    return t


def summary_text(state: State, t: Totals, current: Optional[str]) -> str:
    """The one-line summary under the review (FrameSelector.js 3306-3315)."""
    unstable = len(state["unstable"])
    text = "%d frame(s), %d to remove" % (t["total"], t["condemned"])
    if unstable:
        text += "; %d changed while being measured and were excluded" % unstable
    if t["mixed"]:
        text += "; " + ", ".join(t["mixed"]) + " not comparable -- check before running"
    if copying_out(state):
        text += "; %d approved to copy" % t["approved"]
    return text + flag_line(state, current)


def elide_head(text: Any, max_len: Any) -> str:
    """``Util.elideHead``: the end of a long text, behind "..." (lib/Util.js 963-970)."""
    t = "" if text is None else js_string(text)
    try:
        n = float(max_len)
    except (TypeError, ValueError):
        return t
    if not math.isfinite(n) or n < 4 or len(t) <= n:
        return t
    return "..." + t[len(t) - (int(n) - 3):]


def destination_text(state: State, t: Totals) -> str:
    """What the destination means for the run, as the dialog says it (FrameSelector.js 3337-3350).

    The destructive sentence is only written when the destination is the folder the frames are in.
    """
    if state["destination"] is None:
        return "<i>no folder chosen</i>"
    where = "<i>" + elide_head(state["destination"], _PATH_WIDTH) + "</i>"
    if not destination_is_source(state):
        return where + " &mdash; the " + str(t["approved"]) + " kept frame(s) are copied " + \
            "there; nothing is deleted."
    return where + " &mdash; <b>the folder the frames are in.</b> Nothing is " + \
        "copied; the " + str(t["condemned"]) + " rejected frame(s) are " + \
        "<b>deleted</b> where they are."


def all_paths(state: State) -> list[str]:
    """Every frame in the review, whatever its verdict (FrameSelector.js 3354-3363)."""
    out: list[str] = []
    for key in state["order"]:
        for row in state["channels"][key]["rows"]:
            out.append(row["path"])
    return out


def destination_is_source(state: State) -> bool:
    """The chosen folder is where the frames already are (FrameSelector.js 3370-3372). Compared as written."""
    return frames_files.destination_is_source(all_paths(state), state["destination"])


def copying_out(state: State) -> bool:
    """True when the run writes copies rather than deleting (FrameSelector.js 3433-3435)."""
    return state["destination"] is not None and not destination_is_source(state)


def approved_paths(state: State) -> list[str]:
    """The frames that survive, across every enabled channel (FrameSelector.js 3439-3452)."""
    out: list[str] = []
    for key in state["order"]:
        ch = state["channels"][key]
        if not ch["settings"]["enabled"]:
            continue
        for row in ch["rows"]:
            if F.final_state(row["state"], row.get("override")) != F.REJECTED:
                out.append(row["path"])
    return out


def importing(state: State) -> bool:
    """The frames came off a card, so nothing may be written back to it (FrameSelector.js 3606-3608)."""
    return state["cardRoot"] is not None


def master_flats_mode(state: State) -> bool:
    """Is the run going to make master flats? (FrameSelector.js 3578-3583)."""
    return importing(state) and master_flat_active(state["darksFolder"], state["onlyMasterFlats"],
                                                  card_darks_found(state))


def matched_flats(state: State, lights: list[dict[str, Any]], entry_for: Callable[[str], dict[str, Any]],
                  describe_frame: Callable[[dict[str, Any]], dict[str, Any]],
                  on_flat: Optional[Callable[[int, int, str], Any]] = None) -> list[dict[str, Any]]:
    """Which candidate flats suit each light filter (FrameSelector.js 3732-3750).

    One header is read per light filter (``entry_for``) and per candidate flat (``describe_frame``).
    ``on_flat(done, total, path)`` is told before each flat, 1-based.
    """
    wanted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for light in lights:
        key = js_string(light["filter"])  # a JS object key: null and "null" are the same key
        if key in seen:
            continue
        seen.add(key)
        e = entry_for(light["path"])
        wanted.append({"filter": e["filter"], "binning": e["binning"], "camera": None, "rotation": None})

    records: list[dict[str, Any]] = []
    flats = state["candidateFlats"] or []
    for f, flat in enumerate(flats):
        if on_flat is not None:
            on_flat(f + 1, len(flats), flat["path"])
        records.append(describe_frame(flat))
    return match_flats(wanted, records)


def flag_line(state: State, current: Optional[str]) -> str:
    """The current channel's flags, for the summary (FrameSelector.js 3167-3177).

    A channel that is not comparable says why it has none rather than looking clean.
    """
    ch = channel_of(state, current)
    if ch is None:
        return ""
    if ch["problems"] or not F.auto_reject_allowed(ch["key"]):
        return "  |  not flagged: frames are not comparable"
    fs = flag_summary(ch.get("flags") or [])
    return "  |  " + fs if fs else ""
