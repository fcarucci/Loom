"""The Frame Selector's measurement plot, ported from ``pixinsight/script/FrameSelector.js`` 4318-4556
(``FrameSelector.Plot``) as a ``tkinter.Canvas`` subclass.

Each JS method has a Python twin with the same meaning:

* constructor (4320-4351) -> :meth:`FramesPlot.__init__`; the minimum height 150 (4321) is the canvas height.
* ``onMousePress`` (4331-4339) -> :meth:`FramesPlot.mouse_press`, which maps a click through :func:`point_at`.
* ``setSeries`` (4353-4361) -> :meth:`FramesPlot.set_series`.
* ``release`` (4362-4372) -> :meth:`FramesPlot.release`.
* ``setSelected`` (4378-4386) -> :meth:`FramesPlot.set_selected`; no repaint for no change, as the JS does.
* ``paint`` (4387-4401) -> :meth:`FramesPlot.paint`, which draws the shapes :func:`layout` returns.
* ``paintOn`` (4410-4427) -> :func:`layout`: the paper, then the band, axis and labels, line, markers and ring, in
  the JS order. The shapes are plain dicts so the geometry is tested without a display.
* ``layout`` (4429-4452) -> :func:`frame_geometry` (margins, bounds, the two mappings ``yOf`` and ``xOf``).
* ``drawBand`` (4454-4467) -> :func:`_band_shapes`.
* ``drawAxisAndLabels`` (4469-4497) -> :func:`_axis_shapes`.
* ``drawLine`` (4499-4520) -> :func:`_line_shapes`: a missing value breaks the line rather than being bridged.
* ``drawMarkers`` (4522-4545) -> :func:`_marker_shapes`: a square per frame, a cross for a rejected one.
* ``drawRing`` (4547-4554) -> :func:`_ring_shapes`.

The JS ``fillRect`` and ``drawRect`` take two corners; the shapes here keep the corners too (``x0, y0, x1, y1``).
The JS ``drawText(x, y)`` puts the text's baseline at ``y``, so text shapes are anchored south-west. The JS tooltip
is not ported here; the caller owns tooltips.

Every number is in the same units as the JS (the Canvas works in pixels, as the Graphics object does). The colours
come from :data:`loomsiril.frames_ui.PLOT_COLOURS`, the pick radius from :data:`loomsiril.frames_ui.PICK_RADIUS` and
the label gap from :data:`loomsiril.frames_ui.LABEL_GAP`.

tkinter is imported when this module is imported, but no window is created; without tkinter the pure functions still
work and :class:`FramesPlot` cannot be built.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from loomsiril.frames import METRICS, REJECTED, final_state, round_display
from loomsiril.frames_plot import plot_bounds, point_at, spaced_labels
from loomsiril.frames_ui import LABEL_GAP, PICK_RADIUS, PLOT_COLOURS, tk_colour

try:  # the project venv has no tkinter; only the widget needs it
    import tkinter as tk
except ImportError:  # pragma: no cover - exercised where tkinter is missing
    tk = None

Row = dict[str, Any]
Band = dict[str, Optional[float]]
Shape = dict[str, Any]

#: Margins around the plotting area: room for the value labels on the left (FrameSelector.js 4431).
MARGIN_LEFT = 52
MARGIN_RIGHT = 8
MARGIN_TOP = 10
MARGIN_BOTTOM = 18
#: The minimum height of the plot, unscaled (FrameSelector.js 4321).
MIN_HEIGHT = 150
#: The half-size of a frame's square marker and of a rejected frame's cross (FrameSelector.js 4540, 4531).
MARKER_HALF = 2
CROSS_HALF = 4


def _number(v: Any) -> bool:
    """A plotted number: JS ``v != null && isFinite(v)``. None and booleans are not numbers here."""
    return v is not None and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _open_band(band: Optional[Band]) -> Band:
    return band if band is not None else {"lo": None, "hi": None}


def metric_value(row: Row, metric: str) -> Optional[float]:
    """``row.metrics[metric]`` as the plot reads it: None when the row has no metrics or no such value."""
    metrics = row.get("metrics")
    return metrics.get(metric) if metrics is not None else None


@dataclass(frozen=True)
class Geometry:
    """The plotting area and its two mappings: ``y_of`` (value to y) and ``x_of`` (frame index to x)."""

    left: float
    top: float
    width: float
    height: float
    values: list[Optional[float]]
    bounds: dict[str, float]
    span: float

    def y_of(self, v: float) -> float:
        return self.top + self.height - ((v - self.bounds["lo"]) / self.span) * self.height

    def x_of(self, k: int) -> float:
        n = len(self.values)
        if n < 2:
            return self.left + self.width / 2
        return self.left + (k / (n - 1)) * self.width

    def has(self, k: int) -> bool:
        return _number(self.values[k])


def frame_geometry(W: float, H: float, values: list[Optional[float]], band: Optional[Band]) -> Geometry:
    """The plotting area for a W by H canvas: the margins, the bounds that show every value and both band ends,
    and the span (1 when the bounds have none, as ``|| 1`` gives in JS)."""
    left, top = MARGIN_LEFT, MARGIN_TOP
    width = max(1, W - MARGIN_LEFT - MARGIN_RIGHT)
    height = max(1, H - MARGIN_TOP - MARGIN_BOTTOM)
    bounds = plot_bounds(values, _open_band(band))
    span = bounds["hi"] - bounds["lo"]
    if span == 0 or math.isnan(span):
        span = 1.0
    return Geometry(left=left, top=top, width=width, height=height, values=list(values), bounds=bounds, span=span)


def _rect(x0: float, y0: float, x1: float, y1: float, fill: int) -> Shape:
    return {"kind": "rect", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "fill": tk_colour(fill)}


def _frame(x0: float, y0: float, x1: float, y1: float, outline: int) -> Shape:
    return {"kind": "frame", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "outline": tk_colour(outline)}


def _line(x0: float, y0: float, x1: float, y1: float, fill: int, width: int) -> Shape:
    return {"kind": "line", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "fill": tk_colour(fill), "width": width}


def _oval(x0: float, y0: float, x1: float, y1: float, outline: int, width: int) -> Shape:
    return {"kind": "oval", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "outline": tk_colour(outline), "width": width}


def _text(x: float, y: float, text: str, fill: int) -> Shape:
    return {"kind": "text", "x": x, "y": y, "text": text, "fill": tk_colour(fill), "anchor": "sw"}


def _band_shapes(g: Geometry, band: Band) -> list[Shape]:
    """The accepted band (drawBand, 4454-4467). An open end runs to the edge of the plot."""
    top = g.y_of(band["hi"]) if band.get("hi") is not None else g.top
    bot = g.y_of(band["lo"]) if band.get("lo") is not None else g.top + g.height
    if not bot > top:
        return []
    return [_rect(g.left, max(g.top, top), g.left + g.width, min(g.top + g.height, bot), PLOT_COLOURS["BAND"])]


def _axis_shapes(g: Geometry, band: Band) -> list[Shape]:
    """The frame, the band's edges drawn and labelled, and the axis extremes (drawAxisAndLabels, 4469-4497). An edge
    is listed first, so a threshold keeps its number when an extreme would land on top of it."""
    shapes: list[Shape] = [_frame(g.left, g.top, g.left + g.width, g.top + g.height, PLOT_COLOURS["AXIS"])]
    wanted: list[dict[str, Any]] = []
    for key in ("lo", "hi"):  # JS edges = [band.lo, band.hi]
        edge = band.get(key)
        ye = g.y_of(edge) if edge is not None else None
        if ye is None or ye < g.top or ye > g.top + g.height:
            continue
        shapes.append(_line(g.left, ye, g.left + g.width, ye, PLOT_COLOURS["EDGE"], 1))
        wanted.append({"y": ye + 4, "text": round_display(edge), "edge": True})
    wanted.append({"y": g.top + 8, "text": round_display(g.bounds["hi"])})
    wanted.append({"y": g.top + g.height, "text": round_display(g.bounds["lo"])})
    for label in spaced_labels(wanted, LABEL_GAP):
        colour = PLOT_COLOURS["EDGE"] if label.get("edge") else PLOT_COLOURS["INK"]
        shapes.append(_text(2, label["y"], label["text"], colour))
    return shapes


def _line_shapes(g: Geometry) -> list[Shape]:
    """The line through the values (drawLine, 4499-4520). A gap breaks it rather than being bridged."""
    shapes: list[Shape] = []
    prev: Optional[tuple[float, float]] = None
    for j in range(len(g.values)):
        if not g.has(j):
            prev = None
            continue
        x, y = g.x_of(j), g.y_of(g.values[j])
        if prev is not None:
            shapes.append(_line(prev[0], prev[1], x, y, PLOT_COLOURS["LINE"], 1))
        prev = (x, y)
    return shapes


def _marker_shapes(g: Geometry, rows: list[Row]) -> list[Shape]:
    """A square per frame; a rejected frame gets a cross (drawMarkers, 4522-4545)."""
    shapes: list[Shape] = []
    for k in range(len(g.values)):
        if not g.has(k):
            continue
        x, y = g.x_of(k), g.y_of(g.values[k])
        row = rows[k]
        if final_state(row.get("state"), row.get("override")) == REJECTED:
            shapes.append(_line(x - CROSS_HALF, y - CROSS_HALF, x + CROSS_HALF, y + CROSS_HALF,
                                PLOT_COLOURS["REJECT"], 2))
            shapes.append(_line(x + CROSS_HALF, y - CROSS_HALF, x - CROSS_HALF, y + CROSS_HALF,
                                PLOT_COLOURS["REJECT"], 2))
            continue
        shapes.append(_rect(x - MARKER_HALF, y - MARKER_HALF, x + MARKER_HALF, y + MARKER_HALF, PLOT_COLOURS["LINE"]))
    return shapes


def _ring_shapes(g: Geometry, selected: int) -> list[Shape]:
    """The ring round the selected frame, drawn last (drawRing, 4547-4554). Nothing when it has no point."""
    if selected < 0 or selected >= len(g.values) or not g.has(selected):
        return []
    x, y = g.x_of(selected), g.y_of(g.values[selected])
    r = PICK_RADIUS
    return [_oval(x - r, y - r, x + r, y + r, PLOT_COLOURS["PICKED"], 2)]


def layout(rows: list[Row], metric: str, band: Optional[Band], selected: int, W: float, H: float) -> list[Shape]:
    """Every shape the plot draws for ``rows`` on a W by H canvas, in paint order (paintOn, 4410-4427).

    The first shape is the paper over the whole canvas. ``selected`` is the index of the ringed frame, or -1.
    """
    values = [metric_value(r, metric) for r in rows]
    open_band = _open_band(band)
    g = frame_geometry(W, H, values, open_band)
    shapes: list[Shape] = [_rect(0, 0, W, H, PLOT_COLOURS["PAPER"])]
    shapes += _band_shapes(g, open_band)
    shapes += _axis_shapes(g, open_band)
    shapes += _line_shapes(g)
    shapes += _marker_shapes(g, rows)
    shapes += _ring_shapes(g, selected)
    return shapes


def hit_geometry(rows: list[Row], metric: str, band: Optional[Band], W: float, H: float) -> tuple[float, float]:
    """The ``(left, width)`` a click is mapped through: the same geometry the points were drawn with."""
    g = frame_geometry(W, H, [metric_value(r, metric) for r in rows], _open_band(band))
    return g.left, g.width


#: The shape kinds :class:`FramesPlot` knows how to draw.
SHAPE_KINDS = ("rect", "frame", "line", "oval", "text")

_CanvasBase: Any = tk.Canvas if tk is not None else object


class FramesPlot(_CanvasBase):  # type: ignore[misc]
    """The measurement plot as a Tk canvas. ``on_pick(index)`` is called with the frame a click selects.

    The plot paints itself from :func:`layout`; the canvas background is the paper colour, so the area behind the
    shapes matches the JS paper. It is built with a height of :data:`MIN_HEIGHT` unless the caller passes one.
    """

    def __init__(self, master: Any, **kw: Any) -> None:
        if tk is None:  # pragma: no cover
            raise RuntimeError("tkinter is not available")
        kw.setdefault("height", MIN_HEIGHT)
        kw.setdefault("background", tk_colour(PLOT_COLOURS["PAPER"]))
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("borderwidth", 0)
        super().__init__(master, **kw)
        self.rows: list[Row] = []
        self.metric = METRICS[0]
        self.band: Band = {"lo": None, "hi": None}
        self.selected = -1
        self.on_pick: Optional[Callable[[int], None]] = None
        #: ``(left, width)`` of the last paint: the hit test maps a click through it (JS ``this.geom``).
        self.geom: Optional[tuple[float, float]] = None
        self._press_id = self.bind("<Button-1>", lambda event: self.mouse_press(event.x))
        self._configure_id = self.bind("<Configure>", lambda event: self.paint())

    def set_series(self, rows: Optional[list[Row]], metric: str, band: Optional[Band]) -> None:
        """Shows ``rows`` for ``metric`` with the accepted ``band`` (setSeries, 4353-4361)."""
        self.rows = list(rows or [])
        self.metric = metric
        self.band = _open_band(band)
        self.paint()

    def release(self) -> None:
        """Detaches the plot before its window goes: no rows, no callback, no bindings (release, 4362-4372)."""
        try:
            self.rows = []
            self.on_pick = None
            self.geom = None
            self.unbind("<Button-1>")
            self.unbind("<Configure>")
        except tk.TclError:
            pass

    def set_selected(self, index: Optional[int]) -> None:
        """Rings the frame at ``index``; -1 or None rings nothing (setSelected, 4378-4386)."""
        i = -1 if index is None else index
        if i == self.selected:
            return
        self.selected = i
        self.paint()

    def mouse_press(self, x: float) -> None:
        """A click at ``x`` selects the nearest frame by x, if there is one (onMousePress, 4331-4339)."""
        if self.geom is None or self.on_pick is None:
            return
        left, width = self.geom
        i = point_at(x, left, width, len(self.rows))
        if i >= 0:
            self.on_pick(int(i))

    def paint(self) -> None:
        """Clears the canvas and draws :func:`layout` for its current size (paint, 4387-4401)."""
        W = self.winfo_width()
        H = self.winfo_height()
        self.geom = hit_geometry(self.rows, self.metric, self.band, W, H)
        self.delete("all")
        for shape in layout(self.rows, self.metric, self.band, self.selected, W, H):
            self._draw(shape)

    def _draw(self, s: Shape) -> None:
        kind = s["kind"]
        if kind == "rect":
            self.create_rectangle(s["x0"], s["y0"], s["x1"], s["y1"], fill=s["fill"], outline="")
        elif kind == "frame":
            self.create_rectangle(s["x0"], s["y0"], s["x1"], s["y1"], fill="", outline=s["outline"])
        elif kind == "line":
            self.create_line(s["x0"], s["y0"], s["x1"], s["y1"], fill=s["fill"], width=s["width"])
        elif kind == "oval":
            self.create_oval(s["x0"], s["y0"], s["x1"], s["y1"], fill="", outline=s["outline"], width=s["width"])
        elif kind == "text":
            self.create_text(s["x"], s["y"], text=s["text"], fill=s["fill"], anchor=s["anchor"])
        else:  # pragma: no cover - layout only makes the kinds above
            raise ValueError(f"unknown shape kind: {kind}")


__all__ = [
    "FramesPlot", "Geometry", "MIN_HEIGHT", "SHAPE_KINDS", "frame_geometry", "hit_geometry", "layout",
    "metric_value",
]
