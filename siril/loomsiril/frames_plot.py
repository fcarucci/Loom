"""Frame Selector plot geometry, ported from ``pixinsight/script/lib/Frames.js`` 613-724.

``point_at`` (JS 623-638), ``spaced_labels`` (650-670), ``plot_bounds`` (678-706), ``metric_column`` (709-716) and
``display_value`` (719-724): pure arithmetic over the review plot's coordinates and the table's value text. The
metric list, the three-figure rounding and the score column come from :mod:`loomsiril.frames`, so the table and the
plot agree on which metrics show and how a number reads.

Every function is pinned to the JavaScript by the golden cases in ``tests/oracle/fs_plot.json`` (recorded by
``tools/oracle/fs_plot.js`` from the real Frames.js). The JS comments on each function state its edge cases; the
ports keep them: a click is rejected before it is rounded, a threshold claims its label position before an extreme
does, and a flat channel still occupies a height.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from loomsiril.frames import DISPLAY_METRICS, round_display
from loomsiril.jsmath import js_max, js_min, js_round, number_str

Entry = dict[str, Any]
Bounds = dict[str, float]

#: The trailing score column, after the display metrics (``Frames.SCORE_COLUMN`` in JS).
SCORE_COLUMN = len(DISPLAY_METRICS) + 1


def _finite(v: Any) -> bool:
    """JS ``v != null && isFinite(v)`` for a plotted number: None and bool are not numbers here."""
    return v is not None and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _js_round(v: float) -> float:
    """JS ``Math.round``, also for NaN and the infinities, which Python's ``math.floor`` would raise on."""
    if math.isnan(v) or math.isinf(v):
        return v
    return js_round(v)


def point_at(x: float, left: float, plot_width: float, count: int) -> float:
    """The index of the point nearest a click at ``x``, or -1 when there is none or the click is outside.

    Only x matters: the points are one per frame along the axis. The range test runs before rounding, so a click
    in the left margin is rejected rather than pulled back to the first frame.
    """
    if count <= 0 or plot_width <= 0:
        return -1
    if x < left or x > left + plot_width:
        return -1
    if count == 1:
        return 0
    t = (x - left) / plot_width
    return js_max(0, js_min(count - 1, _js_round(t * (count - 1))))


def spaced_labels(entries: list[Entry], gap: Optional[float] = None) -> list[Entry]:
    """The labels that can be drawn without landing on each other, in the order given.

    Each entry is a dict with a ``y``. The first to claim a position keeps it, so the caller lists the most
    important label first. ``gap`` is the minimum distance between kept labels (12 when omitted).
    """
    out: list[Entry] = []
    used: list[float] = []
    g = 12 if gap is None else gap
    for entry in entries:
        y = entry["y"]
        if not any(abs(u - y) < g for u in used):
            used.append(y)
            out.append(entry)
    return out


def plot_bounds(values: list[Optional[float]], band: Optional[Entry] = None) -> Bounds:
    """The vertical extent ``{"lo", "hi"}`` that shows every finite value and both band ends, with a margin.

    A band end far outside the data is included on purpose. Nothing finite gives ``{"lo": 0, "hi": 1}``. Values
    that are all equal still get a height: 5 percent of the value, or 0.5 around zero.
    """
    seen: list[float] = [v for v in values if _finite(v)]
    if band is not None:
        seen.extend(v for v in (band.get("lo"), band.get("hi")) if _finite(v))
    if not seen:
        return {"lo": 0, "hi": 1}
    lo, hi = min(seen), max(seen)
    if hi == lo:
        d = 0.5 if hi == 0 else abs(hi) * 0.05
        return {"lo": lo - d, "hi": hi + d}
    pad = (hi - lo) * 0.08
    return {"lo": lo - pad, "hi": hi + pad}


def metric_column(metric: Optional[str]) -> Optional[int]:
    """The table column that shows ``metric`` (the first column is the frame name), or None when it has none."""
    if metric not in DISPLAY_METRICS:
        return None
    return DISPLAY_METRICS.index(metric) + 1


def display_value(metric: Optional[str], value: Optional[float]) -> str:
    """A measurement as the table shows it: "-" when there is none; a whole number for stars, else three figures."""
    if not _finite(value):
        return "-"
    if metric == "stars":
        return number_str(_js_round(value))
    return round_display(value)
