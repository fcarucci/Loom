"""Frame Selector: the decisions, ported from ``pixinsight/script/lib/Frames.js``.

Pure arithmetic over measurements: no file, no display, no Siril call. A decision here is the one that
deletes or keeps someone's subframes, so every function is pinned to the JavaScript by the golden cases in
``tests/oracle/fs_core.json`` (recorded by ``tools/oracle/fs_core.js`` from the real Frames.js).

Names are the JS names in snake_case. Dict keys keep the JS camelCase (``psfSNR``, ``eccentricity``, ``kEdited``)
so a row or a settings object reads the same on both sides. JS ``undefined`` has no Python twin: where the JS
returns it (``parseLimit`` on invalid text) this returns :data:`UNDEFINED`.

Out of this module, by design: anomaly flags, grouping by filter and the file lists (T1b), plot geometry (T4).
``new_channel`` returns the channel without ``flags``; the caller adds them. ``NO_FILTER`` and
``auto_reject_allowed`` live here because ``recompute`` needs them; T1b's grouping imports them.
"""

from __future__ import annotations

import math
import re
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from loomsiril.jsmath import is_falsy, js_max, js_min, js_or, js_round, js_string, number_str

Row = dict[str, Any]
Metrics = dict[str, Any]
Gate = dict[str, Any]


class _Undefined:
    """JS ``undefined`` where a function returns it (not ``null``, which is a value of its own)."""

    def __repr__(self) -> str:
        return "UNDEFINED"


UNDEFINED = _Undefined()

MEASURE_VERSION = "v3"  # v3: adds altitude and star flux

COL = {"path": 3, "fwhm": 5, "eccentricity": 6, "snr": 9, "median": 10,
       "noise": 12, "stars": 14, "altitude": 20, "psfFlux": 21, "psfSNR": 28}

OPTIONAL = ["background", "snrWeight", "altitude", "psfFlux"]

STORED_KEYS = ["fwhm", "eccentricity", "noise", "stars", "psfSNR",
               "background", "snrWeight", "altitude", "psfFlux"]

PLAUSIBLE = {
    "fwhm": {"lo": 1, "hi": 60},
    "eccentricity": {"lo": 0.01, "hi": 0.95},
    "psfSNR": {"lo": 1, "hi": 1e9},
    "stars": {"lo": 200, "hi": 500000},
}

METRICS = ["psfSNR", "fwhm", "eccentricity", "stars"]

STATE = {"APPROVED": "approved", "REJECTED": "rejected", "UNMEASURABLE": "unmeasurable"}
APPROVED, REJECTED, UNMEASURABLE = STATE["APPROVED"], STATE["REJECTED"], STATE["UNMEASURABLE"]

MIN_FRAMES = 10
SIGMA_FLOOR_FRACTION = 0.01
WORSE_WHEN = {"psfSNR": "lower", "fwhm": "higher", "eccentricity": "higher", "stars": "lower"}

DEFAULT_WEIGHTS = {"psfSNR": 0.5, "fwhm": 0.3, "eccentricity": 0.1, "stars": 0.1}
DEFAULT_GATING = {"psfSNR": False, "fwhm": True, "eccentricity": True, "stars": True}
CLAMP = {"lo": 0.25, "hi": 4}

PRESETS = {"lenient": 3.0, "balanced": 2.5, "strict": 2.0}
PRESET_NAMES = ["lenient", "balanced", "strict"]
DEFAULT_PRESET = "balanced"

METRIC_LABEL = {"psfSNR": "PSF SNR", "fwhm": "FWHM", "eccentricity": "eccentricity", "stars": "stars",
                "snrWeight": "SNR estimate"}
METRIC_HEADING = {"psfSNR": "PSF SNR", "fwhm": "FWHM", "eccentricity": "ecc", "stars": "stars", "snrWeight": "SNR"}
DISPLAY_METRICS = ["psfSNR", "snrWeight", "fwhm", "eccentricity", "stars"]
FRAME_COLUMNS = ["Frame"] + [METRIC_HEADING[m] for m in DISPLAY_METRICS] + ["score"]

OPERATOR = {"fwhm": "<=", "eccentricity": "<=", "stars": ">=", "psfSNR": ">="}
LIMIT_DECIMALS = {"fwhm": 2, "eccentricity": 3, "stars": 0, "psfSNR": 2}
CRITERIA_ORDER = ["fwhm", "eccentricity", "stars", "psfSNR"]

OVERRIDE = {"RESCUED": "rescued", "CONDEMNED": "condemned"}
RESCUED, CONDEMNED = OVERRIDE["RESCUED"], OVERRIDE["CONDEMNED"]

PHASE = {"REVIEW": "review", "EXECUTING": "executing", "STOPPED": "stopped", "DONE": "done"}
_TRANSITIONS = {
    PHASE["REVIEW"]: {"commit": PHASE["EXECUTING"]},
    PHASE["EXECUTING"]: {"stop": PHASE["STOPPED"], "finish": PHASE["DONE"]},
    PHASE["STOPPED"]: {"resume": PHASE["EXECUTING"], "abandon": PHASE["REVIEW"]},
    PHASE["DONE"]: {"review": PHASE["REVIEW"]},
}

NO_FILTER = "(no filter)"

_LIMIT_TEXT = re.compile(r"[0-9]*\.?[0-9]+(e[+-]?[0-9]+)?", re.IGNORECASE)


# ---- JS number semantics ---------------------------------------------------------------------------

def _finite(v: Any) -> bool:
    """JS ``typeof v == "number" && isFinite(v)``: a bool is not a number here."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def to_fixed(x: float, digits: int) -> str:
    """``Number#toFixed``: exact binary value, ties away from zero (the JS picks the larger n)."""
    if not math.isfinite(x):
        return number_str(x)
    if x == 0:
        x = 0.0  # toFixed drops the sign of -0
    q = Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return format(q, "f")


def to_precision(x: float, p: int) -> str:
    """``Number#toPrecision(p)`` for finite x: fixed notation while the exponent is in [-6, p), else exponential."""
    if x == 0:
        return "0" + ("." + "0" * (p - 1) if p > 1 else "")
    sign = "-" if x < 0 else ""
    d = Decimal(abs(x))
    e = d.adjusted()
    q = d.quantize(Decimal(1).scaleb(e - p + 1), rounding=ROUND_HALF_UP)
    if q.adjusted() != e:  # rounding carried into a new leading digit (9.996 -> 10.0)
        e = q.adjusted()
        q = d.quantize(Decimal(1).scaleb(e - p + 1), rounding=ROUND_HALF_UP)
    digits = str(int(q.scaleb(-(e - p + 1))))
    if e < -6 or e >= p:
        mantissa = digits[0] + ("." + digits[1:] if p > 1 else "")
        return sign + mantissa + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    if e >= 0:
        frac = digits[e + 1:]
        return sign + digits[:e + 1] + ("." + frac if frac else "")
    return sign + "0." + "0" * (-e - 1) + digits


# ---- metric dicts -----------------------------------------------------------------------------------

def metrics_from_row(row: list[Any]) -> Metrics:
    """SubframeSelector's measurement row, by position (Frames.js ``metricsFromRow``)."""
    return {"path": row[COL["path"]], "fwhm": row[COL["fwhm"]], "eccentricity": row[COL["eccentricity"]],
            "noise": row[COL["noise"]], "stars": row[COL["stars"]], "psfSNR": row[COL["psfSNR"]],
            "background": row[COL["median"]], "snrWeight": row[COL["snr"]],
            "altitude": row[COL["altitude"]], "psfFlux": row[COL["psfFlux"]]}


def optional_ok(v: Any) -> bool:
    return _finite(v) and v >= 0


def optional_problems(m: Metrics) -> list[str]:
    return [f for f in OPTIONAL if not optional_ok(m.get(f))]


def sanitize_optional(m: Metrics, disabled: Optional[dict[str, Any]]) -> Metrics:
    """Blank the optional figures that failed: all of them named in ``disabled``, else one row's bad readings."""
    for f in OPTIONAL:
        if (disabled and disabled.get(f)) or not optional_ok(m.get(f)):
            m[f] = None
    return m


def stored_metrics(m: Metrics) -> Metrics:
    """Stored without the path: the key is the digest, so identical bytes elsewhere must not name this file."""
    return {k: m.get(k) for k in STORED_KEYS}


def cache_entry_usable(e: Any) -> bool:
    """A null optional is a cached "unavailable", not a miss."""
    if not isinstance(e, dict):
        return False
    return all(k in e for k in STORED_KEYS)


def metric_in_range(name: str, value: Any) -> bool:
    r = PLAUSIBLE.get(name)
    return r is not None and _finite(value) and r["lo"] <= value <= r["hi"]


def is_whole_count(v: Any) -> bool:
    return _finite(v) and v >= 0 and math.floor(v) == v


def looks_like_count(v: Any) -> bool:
    return (not _finite(v)) or math.floor(v) == v


def meaning_problems(metrics: Metrics) -> list[str]:
    """Does this row still mean what it is read as? A swapped star count and PSF SNR is caught here."""
    bad = []
    path = metrics.get("path")
    if not isinstance(path, str) or path == "":
        bad.append("path is not a non-empty string")
    ecc = metrics.get("eccentricity")
    if not (_finite(ecc) and 0 < ecc < 1):
        bad.append("eccentricity is not a fraction in (0,1)")
    if not metric_in_range("fwhm", metrics.get("fwhm")):
        bad.append("FWHM outside a plausible range")
    if not is_whole_count(metrics.get("stars")):
        bad.append("star count is not a whole number")
    if looks_like_count(metrics.get("psfSNR")):
        bad.append("PSF SNR is a whole number, so it is probably the star count")
    return bad


def metric_valid(value: Any) -> bool:
    """Finite AND positive: zero is a denominator (ecc 0 makes the score infinite)."""
    return _finite(value) and value > 0


def frame_valid(metrics: Optional[Metrics]) -> bool:
    if metrics is None:
        return False
    return all(metric_valid(metrics.get(m)) for m in METRICS)


# ---- statistics and the relative clip --------------------------------------------------------------

def median(values: Optional[list[float]]) -> float:
    if values is None or len(values) == 0:
        return math.nan
    v = sorted(values)
    n = len(v)
    h = n >> 1
    return v[h] if n % 2 else (v[h - 1] + v[h]) / 2


def sigma(values: list[float]) -> float:
    """MAD normalised to a Gaussian sigma: a few disasters must not widen the gate meant to catch them."""
    m = median(values)
    if not math.isfinite(m):
        return math.nan
    return 1.4826 * median([abs(v - m) for v in values])


def gate(values: list[float], metric: str, k: float) -> Gate:
    """One metric's rejection bound, with a reason when it cannot reject."""
    med, sd = median(values), sigma(values)
    g: Gate = {"active": False, "limit": None, "median": med, "sigma": sd, "reason": None}
    if not math.isfinite(med) or not math.isfinite(sd):
        g["reason"] = "no usable measurements"
        return g
    if sd <= 0 or sd < SIGMA_FLOOR_FRACTION * abs(med):
        g["reason"] = "no usable spread"
        return g
    g["limit"] = med + k * sd if WORSE_WHEN[metric] == "higher" else med - k * sd
    if WORSE_WHEN[metric] == "lower" and g["limit"] <= 0:
        g["reason"] = "lower bound is not positive"
        g["limit"] = None
        return g
    g["active"] = True
    return g


def valid_only(frames: list[Metrics]) -> list[Metrics]:
    return [f for f in frames if frame_valid(f)]


def column_of(frames: list[Metrics], metric: str) -> list[Any]:
    return [f[metric] for f in frames]


def relative_gates(frames: list[Metrics], k: float, gating: Optional[dict[str, Any]]) -> dict[str, Gate]:
    """Every gate for a channel, or none below MIN_FRAMES valid frames. ``gating`` names the metrics allowed to reject."""
    valid = valid_only(frames)
    if len(valid) < MIN_FRAMES:
        return {}
    gates: dict[str, Gate] = {}
    for name in METRICS:
        if gating is not None and not gating.get(name):
            gates[name] = {"active": False, "limit": None, "median": median(column_of(valid, name)),
                           "sigma": None, "reason": "not used as a gate"}
            continue
        gates[name] = gate(column_of(valid, name), name, k)
    return gates


def medians(frames: list[Metrics]) -> dict[str, float]:
    valid = valid_only(frames)
    return {name: median(column_of(valid, name)) for name in METRICS}


def score(metrics: Metrics, meds: dict[str, float], weights: Optional[dict[str, float]]) -> Optional[float]:
    """Higher is better in every term; None for an unmeasurable frame (a 0 would invite its deletion)."""
    if not frame_valid(metrics):
        return None
    w = DEFAULT_WEIGHTS if weights is None else weights
    total = 0.0
    for name in METRICS:
        med = meds[name]
        if not metric_valid(med):
            return None
        ratio = med / metrics[name] if WORSE_WHEN[name] == "higher" else metrics[name] / med
        ratio = js_max(CLAMP["lo"], js_min(CLAMP["hi"], ratio))
        total += js_or(w.get(name), 0) * ratio
    return total


# ---- settings and presets --------------------------------------------------------------------------

def default_settings() -> dict[str, Any]:
    """Per channel, not per folder: a night's L and its Ha are not the same population."""
    return {"preset": DEFAULT_PRESET, "k": PRESETS[DEFAULT_PRESET], "kEdited": False,
            "weights": {m: DEFAULT_WEIGHTS[m] for m in METRICS}, "limits": {},
            "gating": {m: DEFAULT_GATING[m] for m in METRICS}, "enabled": True}


def copy_of(o: Optional[dict[str, Any]]) -> dict[str, Any]:
    return {} if o is None else dict(o)


def apply_preset(settings: dict[str, Any], preset: str) -> dict[str, Any]:
    """Sets k for every channel whose k was not edited by hand. Copies, so channels never share knobs."""
    out = copy_of(settings)
    if settings.get("weights") is not None:
        out["weights"] = copy_of(settings["weights"])
    if settings.get("limits") is not None:
        out["limits"] = copy_of(settings["limits"])
        for key in list(out["limits"]):
            out["limits"][key] = copy_of(out["limits"][key])
    if settings.get("gating") is not None:
        out["gating"] = copy_of(settings["gating"])
    out["preset"] = preset
    if not settings.get("kEdited") and PRESETS.get(preset) is not None:
        out["k"] = PRESETS[preset]
    return out


def accepted_band(metric: str, gates: Optional[dict[str, Gate]], settings: dict[str, Any]) -> dict[str, Optional[float]]:
    """The range a frame may sit in and still be kept. A typed limit replaces the relative gate for its metric."""
    open_band: dict[str, Optional[float]] = {"lo": None, "hi": None}
    gating = settings.get("gating")
    if gating is not None and gating.get(metric) is False:
        return open_band
    limits = settings.get("limits")
    lim = limits.get(metric) if limits else None
    if lim is not None:
        return narrow_band(open_band, lim)
    return relative_band(metric, gates)


def relative_band(metric: str, gates: Optional[dict[str, Gate]]) -> dict[str, Optional[float]]:
    g = gates.get(metric) if gates else None
    if g is None or not g["active"]:
        return {"lo": None, "hi": None}
    if WORSE_WHEN[metric] == "higher":
        return {"lo": None, "hi": g["limit"]}
    return {"lo": g["limit"], "hi": None}


def narrow_band(band: dict[str, Optional[float]], lim: Optional[dict[str, Any]]) -> dict[str, Optional[float]]:
    """A band narrowed by a limit's ends; the tighter end wins on each side."""
    if lim is None:
        return band
    lo, hi = band["lo"], band["hi"]
    if lim.get("lo") is not None:
        lo = lim["lo"] if lo is None else js_max(lo, lim["lo"])
    if lim.get("hi") is not None:
        hi = lim["hi"] if hi is None else js_min(hi, lim["hi"])
    return {"lo": lo, "hi": hi}


# ---- failures and verdicts ---------------------------------------------------------------------------

def relative_failures(metrics: Metrics, gates: dict[str, Gate]) -> list[dict[str, str]]:
    """Rejections from the channel's own spread. Only an ACTIVE gate can reject."""
    out = []
    for name in METRICS:
        g = (gates or {}).get(name)
        if g is None or not g["active"]:
            continue
        v = metrics[name]
        bad = v > g["limit"] if WORSE_WHEN[name] == "higher" else v < g["limit"]
        if bad:
            out.append({"metric": name,
                        "text": METRIC_LABEL[name] + " " + round_display(v) +
                                ", median " + round_display(g["median"]) +
                                ", limit " + round_display(g["limit"])})
    return out


def absolute_failures(metrics: Metrics, limits: Optional[dict[str, Any]],
                      gating: Optional[dict[str, Any]]) -> list[dict[str, str]]:
    """Rejections from a typed limit on a ticked criterion."""
    out = []
    for name in METRICS:
        lim = (limits or {}).get(name)
        if lim is None or (gating is not None and not gating.get(name)):
            continue
        if lim.get("hi") is not None and metrics[name] > lim["hi"]:
            out.append({"metric": name,
                        "text": METRIC_LABEL[name] + " " + round_display(metrics[name]) +
                                " above the limit of " + round_display(lim["hi"])})
        if lim.get("lo") is not None and metrics[name] < lim["lo"]:
            out.append({"metric": name,
                        "text": METRIC_LABEL[name] + " " + round_display(metrics[name]) +
                                " below the limit of " + round_display(lim["lo"])})
    return out


def auto_gates(gates: Optional[dict[str, Gate]], limits: Optional[dict[str, Any]]) -> dict[str, Gate]:
    """A metric with a typed limit is judged on that limit alone; its relative gate is set aside."""
    out = {}
    for key, g in (gates or {}).items():
        if limits and limits.get(key) is not None:
            out[key] = {"active": False, "limit": None, "median": g["median"], "sigma": g["sigma"],
                        "reason": "replaced by a typed limit"}
        else:
            out[key] = g
    return out


def verdict(metrics: Metrics, gates: Optional[dict[str, Gate]], settings: dict[str, Any]) -> dict[str, Any]:
    if not frame_valid(metrics):
        return {"state": UNMEASURABLE, "reasons": ["a metric is missing or not positive"]}
    failures = (relative_failures(metrics, auto_gates(gates, settings.get("limits")))
                + absolute_failures(metrics, settings.get("limits") or {}, settings.get("gating")))
    reasons, failing, seen = [], [], set()
    for f in failures:
        reasons.append(f["text"])
        if f["metric"] not in seen:
            seen.add(f["metric"])
            failing.append(f["metric"])
    return {"state": REJECTED if reasons else APPROVED, "reasons": reasons, "failing": failing}


# ---- the criteria panel's arithmetic ------------------------------------------------------------------

def parse_limit(text: Any) -> Any:
    """A typed threshold: a positive number, None for blank, UNDEFINED for anything else.

    A comma is not a decimal point here: guessing would move a limit by a factor of a thousand.
    """
    t = ("" if text is None else js_string(text)).strip()
    if t == "":
        return None
    if not _LIMIT_TEXT.fullmatch(t):
        return UNDEFINED
    v = float(t)
    return v if (math.isfinite(v) and v > 0) else UNDEFINED


def format_limit(metric: str, value: Any) -> str:
    if not _finite(value):
        return ""
    d = LIMIT_DECIMALS.get(metric)
    return to_fixed(value, 2 if d is None else d)


def limit_value(settings: dict[str, Any], metric: str) -> Optional[float]:
    """The stored worse-side bound, or None."""
    lim = (settings.get("limits") or {}).get(metric)
    if lim is None:
        return None
    return lim.get("hi") if WORSE_WHEN[metric] == "higher" else lim.get("lo")


def with_limit(settings: dict[str, Any], metric: str, value: Optional[float]) -> dict[str, Any]:
    """Settings with one metric's limit typed, or cleared back to automatic with None. Returns a copy."""
    out = copy_of(settings)
    out["limits"] = copy_of(settings.get("limits"))
    if value is None:
        out["limits"].pop(metric, None)
    else:
        out["limits"][metric] = {"hi": value} if WORSE_WHEN[metric] == "higher" else {"lo": value}
    return out


def display_limit(metric: str, gates: Optional[dict[str, Gate]], settings: dict[str, Any]) -> dict[str, Any]:
    typed = limit_value(settings, metric)
    if typed is not None:
        return {"value": typed, "auto": False}
    g = gates.get(metric) if gates else None
    return {"value": g["limit"] if (g is not None and g["active"]) else None, "auto": True}


def round_display(v: float) -> str:
    """Three significant figures, so a reason reads as a sentence."""
    if not math.isfinite(v):
        return number_str(v)
    a = abs(v)
    if a >= 100:
        return str(int(js_round(v)))
    if a >= 1:
        return to_fixed(v, 2)
    return to_precision(v, 3)


# ---- review: outcomes, counts, phases ----------------------------------------------------------------

def final_state(verdict_state: str, override: Optional[str]) -> str:
    """A hand decision outranks the formula. An unmeasurable frame may be condemned, never approved."""
    if verdict_state == UNMEASURABLE:
        return REJECTED if override == CONDEMNED else UNMEASURABLE
    if override == RESCUED:
        return APPROVED
    if override == CONDEMNED:
        return REJECTED
    return verdict_state


def left_out(row: Row, channel_enabled: bool, copying: bool) -> bool:
    """Whether Run leaves this frame out: culling drops rejected frames of enabled channels, copying leaves out
    everything of a switched-off channel and every rejected frame."""
    rejected = final_state(row["state"], row.get("override")) == REJECTED
    return (not channel_enabled or rejected) if copying else (bool(channel_enabled) and rejected)


def keep_count(rows: list[Row], channel_enabled: bool, copying: bool) -> dict[str, int]:
    keep = sum(1 for r in rows if not left_out(r, channel_enabled, copying))
    return {"keep": keep, "total": len(rows)}


def counts(rows: list[Row]) -> dict[str, int]:
    c = {"total": len(rows), "kept": 0, "rejected": 0, "unmeasurable": 0, "rescued": 0, "condemned": 0}
    for r in rows:
        final = final_state(r["state"], r.get("override"))
        if final == APPROVED:
            c["kept"] += 1
        elif final == REJECTED:
            c["rejected"] += 1
        else:
            c["unmeasurable"] += 1
        if r.get("override") == RESCUED:
            c["rescued"] += 1
        if r.get("override") == CONDEMNED:
            c["condemned"] += 1
    return c


def summary_line(channel: str, c: dict[str, int]) -> str:
    """Hand edits are named, so a summary never hides one."""
    extra = []
    if c["rescued"]:
        extra.append("+%d rescued" % c["rescued"])
    if c["condemned"]:
        extra.append("-%d condemned" % c["condemned"])
    if c["unmeasurable"]:
        extra.append("%d unmeasurable" % c["unmeasurable"])
    return "%s: %d kept of %d" % (channel, c["kept"], c["total"]) + (" (%s)" % ", ".join(extra) if extra else "")


def can_edit(phase: str) -> bool:
    return phase == PHASE["REVIEW"]


def next_phase(phase: str, event: str) -> Optional[str]:
    return _TRANSITIONS.get(phase, {}).get(event)


def build_manifest(rows: list[Row]) -> dict[str, Any]:
    """A snapshot of what Apply will delete, taken with the FINAL state so rescues are kept and condemnations go."""
    entries = []
    for r in rows:
        if final_state(r["state"], r.get("override")) != REJECTED:
            continue
        entries.append({"path": r["path"], "channel": r.get("channel") or "", "digest": r.get("digest"),
                        "size": r.get("size"), "mtime": r.get("mtime"), "autoVerdict": r["state"],
                        "override": r.get("override") or None, "reasons": list(r.get("reasons") or []),
                        "outcome": None, "detail": ""})
    return {"entries": entries, "created": int(time.time() * 1000)}


def manifest_pending(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in manifest["entries"] if e["outcome"] is None]


def record_outcome(manifest: dict[str, Any], path: str, outcome: str, detail: Optional[str]) -> None:
    for e in manifest["entries"]:
        if e["path"] == path:
            e["outcome"] = outcome
            e["detail"] = detail or ""
            return


def identity_matches(entry: Optional[dict[str, Any]], current: Optional[dict[str, Any]]) -> bool:
    """Identity is the digest. Path, size and mtime can all be preserved by a replacement, so none authorises a deletion."""
    return (current is not None and entry is not None
            and current.get("digest") == entry.get("digest") and current.get("size") == entry.get("size"))


# ---- channels ---------------------------------------------------------------------------------------

def auto_reject_allowed(filter_key: str) -> bool:
    """A group with no readable FILTER is shown, never clipped: there is no evidence its frames belong together."""
    return filter_key != NO_FILTER


def new_channel(key: str, entries: list[dict[str, Any]], metrics: dict[str, Metrics],
                problems: Optional[list[str]]) -> dict[str, Any]:
    rows = []
    for e in entries:
        ident = e.get("identity")
        rows.append({"path": e["path"], "channel": key,
                     "metrics": metrics.get(e["path"]),
                     "time": e.get("time") or None,
                     "digest": ident.get("digest") if ident else None,
                     "size": ident.get("size") if ident else 0,
                     "mtime": ident.get("mtime") if ident else 0,
                     "state": UNMEASURABLE, "reasons": [], "override": None, "score": None})
    return {"key": key, "entries": entries, "metrics": metrics,
            "problems": [] if problems is None else problems,
            "settings": default_settings(), "rows": rows}


def recompute(ch: dict[str, Any]) -> dict[str, Any]:
    """Every verdict in a channel, from its cohort. Overrides stand: a knob never discards a hand decision."""
    cohort = [r["metrics"] for r in ch["rows"] if r["metrics"] is not None]
    settings = ch["settings"]
    gates = relative_gates(cohort, settings["k"], settings.get("gating"))
    ch["gates"] = gates
    meds = medians(cohort)
    may_reject = bool(settings.get("enabled")) and auto_reject_allowed(ch["key"])
    for row in ch["rows"]:
        judge_row(row, gates, meds, settings, may_reject)
    return ch


def judge_row(row: Row, gates: dict[str, Gate], meds: dict[str, float], settings: dict[str, Any],
              may_reject: bool) -> None:
    """One row's score and verdict. A rejection is suppressed when the channel may not reject."""
    if row["metrics"] is None:
        row["score"] = None
        row["state"] = UNMEASURABLE
        row["reasons"] = ["no measurement"]
        row["failing"] = []
        return
    row["score"] = score(row["metrics"], meds, settings.get("weights"))
    v = verdict(row["metrics"], gates, settings)
    suppressed = not may_reject and v["state"] == REJECTED
    row["state"] = APPROVED if suppressed else v["state"]
    row["reasons"] = [] if suppressed else v["reasons"]
    if suppressed:
        row["failing"] = []
    elif "failing" in v:
        row["failing"] = v["failing"]
    else:
        row.pop("failing", None)
