"""Frame Selector: the anomaly flags a channel's frames are shown with, and the text that says why.

Ported from ``pixinsight/script/lib/Frames.js`` (constants 1691-1716, ``obsTime`` 1718 ... ``frameTags`` 1951). A flag
never deletes a frame: it is a note beside the verdict, computed once from the measurements (``anomalyFlags``), so a
knob does not move it. Pure over plain dicts. Pinned to the JavaScript by ``tests/oracle/fs_flags.json``.

The median, sigma and state helpers are defined in ``loomsiril.frames``, imported rather than copied.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Optional

from loomsiril.frames import (CONDEMNED, REJECTED, RESCUED, SIGMA_FLOOR_FRACTION, final_state, left_out, median,
                              sigma, to_fixed)
from loomsiril.jsmath import js_string

Metrics = dict[str, Any]
Corrected = dict[str, Any]

# Rules. A rule that fires needs a baseline of at least FLAG_MIN_FRAMES readings, and a spread above the 1% floor.
FLAG_K = 3
FLAG_MIN_FRAMES = 5
DROPPED_FRACTION = 0.1
#: Blur: FWHM this far above the channel's sharpest quarter.
BLUR_MARGIN = 0.20
#: Seeing grows with airmass to this power (Kolmogorov turbulence).
SEEING_AIRMASS_POWER = 0.6
#: Extinction, magnitudes per airmass.
EXTINCTION = 0.15
#: CLOUD: extinction-corrected star flux this far below the channel median.
CLOUD_DIMMING = 0.25
#: CLOUD from background: this far above the channel's median, as a fraction.
CLOUD_BACKGROUND_RISE = 0.05

FLAG_ORDER = ["focus", "seeing", "altitude", "tracking", "cloud", "dropped"]
FLAG_TAG = {"focus": "FOCUS", "seeing": "SEEING", "altitude": "ALTITUDE",
            "tracking": "TRACKING", "cloud": "CLOUD", "dropped": "DROPPED"}
FLAG_BADGE = {"focus": "F", "seeing": "S", "altitude": "A", "tracking": "T", "cloud": "C", "dropped": "D"}
#: Summary order, as SubframeStudio words it.
FLAG_SUMMARY_ORDER = ["cloud", "focus", "seeing", "altitude", "tracking", "dropped"]

_ISO = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})"                                   # date
    r"(?:[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?"        # optional time, seconds and fraction optional
    r"(?:[Zz]|([+-])(\d{2}):?(\d{2}))?\Z")                       # optional zone; none means UTC


def obs_time(value: Any) -> Optional[int]:
    """An observation time (DATE-OBS, UTC) in ms, or None.

    Reads the ISO-8601 forms FITS writes, with or without a zone (no zone is UTC, as the JS appends "Z"). Other
    text is not pinned by the oracle and reads as None.
    """
    if value is None:
        return None
    t = js_string(value).replace("'", "").strip()
    if t == "":
        return None
    m = _ISO.match(t)
    if m is None:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh = int(m.group(4) or 0)
    mi = int(m.group(5) or 0)
    ss = int(m.group(6) or 0)
    frac = int(((m.group(7) or "") + "000")[:3])
    # V8's rules, pinned by the oracle: a day of 1-31 whatever the month (an overflow rolls into the next month),
    # 24:00:00 is the next midnight, and any other field out of range is not a time.
    if not (1 <= mo <= 12 and 1 <= d <= 31) or mi > 59 or ss > 59 or hh > 24:
        return None
    if hh == 24:
        if mi or ss or frac:
            return None
        hh = 0
        extra_day = 1
    else:
        extra_day = 0
    try:
        first = (date(y, mo, 1) - date(1970, 1, 1)).days
    except ValueError:
        return None
    days = first + (d - 1) + extra_day
    ms = ((days * 86400 + hh * 3600 + mi * 60 + ss) * 1000) + frac
    if m.group(8):
        offset = (int(m.group(9)) * 60 + int(m.group(10))) * 60000
        ms -= offset if m.group(8) == "+" else -offset
    return ms


def airmass(altitude: Any) -> Optional[float]:
    """Airmass from altitude in degrees, or None when there is no usable altitude."""
    if not is_number(altitude) or altitude <= 1 or altitude > 90:
        return None
    return 1 / math.sin(altitude * math.pi / 180)


def is_number(v: Any) -> bool:
    """A finite JS number: a bool is not one, and neither is a string."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def lower_quartile(values: list[float]) -> float:
    """The value a quarter of the way up a list: the channel's good end (the caller ensures values is not empty)."""
    v = sorted(values)
    return v[len(v) // 4]


def flag_baseline(lst: list[Optional[Metrics]], metric: str, allow_zero: bool) -> Optional[dict[str, Any]]:
    """One metric's baseline over the channel: every usable reading, or None below FLAG_MIN_FRAMES.

    Positive readings, or non-negative for a star COUNT where 0 is a real reading. ``spread`` says whether a sigma
    rule may use it, with the same 1% floor the rejection gates use.
    """
    v = []
    for m in lst:
        x = m.get(metric) if m is not None else None
        if is_number(x) and (x >= 0 if allow_zero else x > 0):
            v.append(x)
    if len(v) < FLAG_MIN_FRAMES:
        return None
    med, sd = median(v), sigma(v)
    return {"median": med, "sigma": sd, "values": v,
            "spread": sd > 0 and sd >= SIGMA_FLOOR_FRACTION * abs(med)}


def flag_high(base: Optional[dict[str, Any]], v: Any) -> bool:
    """Above the channel by more than FLAG_K sigma, on a baseline with spread."""
    return base is not None and bool(base["spread"]) and is_number(v) and v > base["median"] + FLAG_K * base["sigma"]


def corrected(m: Optional[Metrics]) -> Corrected:
    """Per frame: FWHM corrected for airmass (the raw FWHM stands in without an altitude), and star flux corrected
    for extinction. ``X`` is the airmass or None."""
    X = airmass(m.get("altitude")) if m is not None else None
    fwhm = (m["fwhm"] / (X or 1) ** SEEING_AIRMASS_POWER
            if m is not None and is_number(m.get("fwhm")) else None)
    flux = (m["psfFlux"] * 10 ** (0.4 * EXTINCTION * ((X or 1) - 1))
            if m is not None and is_number(m.get("psfFlux")) and m["psfFlux"] > 0 else None)
    return {"X": X, "fwhm": fwhm, "flux": flux}


def flag_references(lst: list[Optional[Metrics]], corr: list[Corrected]) -> dict[str, Any]:
    """The channel-wide references the rules compare against. A reference is None below FLAG_MIN_FRAMES values."""

    def ref(values: list[Any], pick: Any) -> Optional[float]:
        v = [x for x in values if is_number(x) and x > 0]
        return None if len(v) < FLAG_MIN_FRAMES else pick(v)

    return {
        "fwhm": ref([m.get("fwhm") if m is not None else None for m in lst], lower_quartile),
        "fwhmCorr": ref([c["fwhm"] for c in corr], lower_quartile),
        "flux": ref([c["flux"] for c in corr], median),
        "background": ref([m.get("background") if m is not None else None for m in lst], median),
        "eccentricity": flag_baseline(lst, "eccentricity", False),
        "stars": flag_baseline(lst, "stars", True),
    }


def blur_cause(i: int, lst: list[Metrics], corr: list[Corrected], refs: dict[str, Any],
               blurred_corr: list[bool], order: Optional[list[int]]) -> Optional[str]:
    """Why a blurred frame is blurred: "altitude", "seeing", "focus", or None when it is not blurred.

    ``blurred_corr[i]`` says whether frame i is still wide once airmass is taken out; ``order`` is the frames in time
    order, or None when times are not known.
    """
    m = lst[i]
    if refs["fwhm"] is None or not is_number(m.get("fwhm")) or m["fwhm"] <= (1 + BLUR_MARGIN) * refs["fwhm"]:
        return None
    if order is None:
        return "focus"                       # no time order: cannot tell a run from a blip
    at = order.index(i) if i in order else -1
    neighbours = [order[k] for k in (at - 1, at + 1) if 0 <= k < len(order)]
    explained = corr[i]["X"] is not None and not blurred_corr[i]

    def raw_wide(j: int) -> bool:
        # Altitude changes slowly, so it can only blur a run: ALTITUDE needs a time neighbour that is wide too.
        return (lst[j] is not None and is_number(lst[j].get("fwhm"))
                and lst[j]["fwhm"] > (1 + BLUR_MARGIN) * refs["fwhm"])

    if explained:
        return "altitude" if any(raw_wide(j) for j in neighbours) else "seeing"
    isolated = len(neighbours) > 0 and all(not blurred_corr[j] for j in neighbours)
    return "seeing" if isolated else "focus"


def is_cloud(m: Metrics, c: Corrected, refs: dict[str, Any]) -> bool:
    """Dimmed beyond extinction, or a brighter sky: CLOUD."""
    dim = (refs["flux"] is not None and c["flux"] is not None
           and c["flux"] < (1 - CLOUD_DIMMING) * refs["flux"])
    bright = (refs["background"] is not None and is_number(m.get("background"))
              and m["background"] > refs["background"] * (1 + CLOUD_BACKGROUND_RISE))
    return bool(dim or bright)


def time_order(lst: list[Optional[Metrics]], times: Optional[list[Optional[float]]]) -> Optional[list[int]]:
    """The measured frames in time order, or None unless every measured frame has a time."""
    if times is None:
        return None
    idx = []
    for i, m in enumerate(lst):
        if m is not None:
            t = times[i] if i < len(times) else None
            if t is None:
                return None
            idx.append(i)
    return sorted(idx, key=lambda i: times[i])


def anomaly_flags(lst: list[Optional[Metrics]], comparable: bool,
                  times: Optional[list[Optional[float]]]) -> list[list[str]]:
    """The flags of every frame of a channel, in FLAG_ORDER. Frames of an incomparable channel get none."""
    out: list[list[str]] = [[] for _ in lst]
    if not comparable:
        return out
    corr = [corrected(m) for m in lst]
    refs = flag_references(lst, corr)
    blurred_corr = [refs["fwhmCorr"] is not None and c["fwhm"] is not None
                    and c["fwhm"] > (1 + BLUR_MARGIN) * refs["fwhmCorr"] for c in corr]
    order = time_order(lst, times)
    for i, m in enumerate(lst):
        if m is None:
            continue
        st = refs["stars"]
        f: dict[str, bool] = {
            "tracking": flag_high(refs["eccentricity"], m.get("eccentricity")),
            "cloud": is_cloud(m, corr[i], refs),
            "dropped": (st is not None and is_number(m.get("stars"))
                        and m["stars"] < DROPPED_FRACTION * st["median"]),
        }
        cause = blur_cause(i, lst, corr, refs, blurred_corr, order)
        if cause is not None:
            f[cause] = True
        out[i] = [k for k in FLAG_ORDER if f.get(k)]
    return out


def flag_summary(flags_list: list[list[str]]) -> str:
    """``"N flagged: a cloud, b focus"``, N counting frames. "" when none are flagged."""
    frames = 0
    per: dict[str, int] = {}
    for fl in flags_list:
        if fl:
            frames += 1
        for k in fl:
            per[k] = per.get(k, 0) + 1
    if frames == 0:
        return ""
    parts = [f"{per[k]} {k}" for k in FLAG_SUMMARY_ORDER if per.get(k)]
    return f"{frames} flagged: " + ", ".join(parts)


def airmass_line(m: Optional[Metrics]) -> str:
    """``"\\naltitude 41.2 deg, airmass 1.52, FWHM 3.81 (2.97 corrected)"``, or "" without an altitude."""
    c = corrected(m) if m is not None else None
    if c is None or c["X"] is None or c["fwhm"] is None:
        return ""
    return ("\naltitude " + to_fixed(m["altitude"], 1) + " deg, airmass " + to_fixed(c["X"], 2)
            + ", FWHM " + to_fixed(m["fwhm"], 2) + " (" + to_fixed(c["fwhm"], 2) + " corrected)")


def row_tooltip(row: dict[str, Any], flags: Optional[list[str]]) -> str:
    """A row's tooltip: the path, the verdict and why, then what it looks like."""
    mark = (" (rescued)" if row.get("override") == RESCUED
            else " (condemned)" if row.get("override") == CONDEMNED else "")
    fl = flags or []
    reasons = row.get("reasons") or []
    return (row["path"] + "\n" + final_state(row["state"], row.get("override")) + mark
            + (": " + "; ".join(reasons) if reasons else "")
            + airmass_line(row.get("metrics"))
            + ("\nlooks like: " + ", ".join(FLAG_TAG[f].lower() for f in fl) if fl else ""))


def frame_tags(row: dict[str, Any], flags: Optional[list[str]], channel_enabled: bool,
               copying: bool) -> list[dict[str, str]]:
    """The preview's tags, top to bottom: the red one (why a frame is left out), then each flag alone."""
    tags: list[dict[str, str]] = []
    if left_out(row, channel_enabled, copying):
        text = "REJECTED" if final_state(row["state"], row.get("override")) == REJECTED else "CHANNEL OFF"
        tags.append({"text": text, "kind": "reject"})
    for f in flags or []:
        tags.append({"text": FLAG_TAG[f], "kind": "flag"})
    return tags
