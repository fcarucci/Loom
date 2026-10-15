"""The measurement plot: the pure layout (shapes, geometry, labels, click mapping) without a display, and the Tk canvas
under a hidden root, skipped where there is no display.

The layout is checked against the shapes it returns, not against pixels: the Canvas only draws that list.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from loomsiril import frames as F
from loomsiril import frames_plot as FP
from loomsiril import frames_plot_widget as PW
from loomsiril import frames_ui as fu

LINE = fu.tk_colour(fu.PLOT_COLOURS["LINE"])
BAND = fu.tk_colour(fu.PLOT_COLOURS["BAND"])
EDGE = fu.tk_colour(fu.PLOT_COLOURS["EDGE"])
REJECT = fu.tk_colour(fu.PLOT_COLOURS["REJECT"])
PICKED = fu.tk_colour(fu.PLOT_COLOURS["PICKED"])
PAPER = fu.tk_colour(fu.PLOT_COLOURS["PAPER"])
METRIC = F.METRICS[0]
W, H = 200, 150
LEFT, WIDTH = PW.MARGIN_LEFT, W - PW.MARGIN_LEFT - PW.MARGIN_RIGHT
TOP, HEIGHT = PW.MARGIN_TOP, H - PW.MARGIN_TOP - PW.MARGIN_BOTTOM


def row(value: Any, state: str = F.APPROVED, override: Any = None) -> dict[str, Any]:
    return {"metrics": {METRIC: value}, "state": state, "override": override}


def of_kind(shapes: list[dict[str, Any]], kind: str, colour: str | None = None) -> list[dict[str, Any]]:
    return [s for s in shapes if s["kind"] == kind and (colour is None or s.get("fill") == colour
                                                       or s.get("outline") == colour)]


def markers(shapes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The square markers: filled rectangles in the line colour (the paper and the band are other colours)."""
    return of_kind(shapes, "rect", LINE)


def centre(s: dict[str, Any]) -> tuple[float, float]:
    return (s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2


# ---- pure geometry ---------------------------------------------------------------------------------

def test_the_module_imports_without_a_window() -> None:
    # Importing must not make a Tk root; the widget class exists only as a tkinter subclass when tkinter is there.
    assert PW.layout([], METRIC, None, -1, W, H)


def test_a_normal_series_draws_its_frames_in_order() -> None:
    rows = [row(10), row(20), row(30)]
    shapes = PW.layout(rows, METRIC, {"lo": 15, "hi": None}, -1, W, H)
    ms = markers(shapes)
    assert len(ms) == 3
    xs = [centre(m)[0] for m in ms]
    assert xs == pytest.approx([LEFT, LEFT + WIDTH / 2, LEFT + WIDTH])
    g = PW.frame_geometry(W, H, [10, 20, 30], {"lo": 15, "hi": None})
    assert [centre(m)[1] for m in ms] == pytest.approx([g.y_of(10), g.y_of(20), g.y_of(30)])
    # Larger values sit higher on the plot (smaller y).
    ys = [centre(m)[1] for m in ms]
    assert ys[0] > ys[1] > ys[2]


def test_the_line_joins_consecutive_frames() -> None:
    rows = [row(10), row(20), row(30)]
    shapes = PW.layout(rows, METRIC, None, -1, W, H)
    segments = of_kind(shapes, "line", LINE)
    assert len(segments) == 2
    ms = markers(shapes)
    assert (segments[0]["x0"], segments[0]["y0"]) == pytest.approx(centre(ms[0]))
    assert (segments[0]["x1"], segments[0]["y1"]) == pytest.approx(centre(ms[1]))
    assert (segments[1]["x0"], segments[1]["y0"]) == pytest.approx(centre(ms[1]))
    assert (segments[1]["x1"], segments[1]["y1"]) == pytest.approx(centre(ms[2]))


def test_the_band_is_a_rectangle_between_its_limits() -> None:
    values = [10, 20, 30]
    band = {"lo": 15, "hi": 25}
    shapes = PW.layout(rows_of(values), METRIC, band, -1, W, H)
    (rect,) = of_kind(shapes, "rect", BAND)
    g = PW.frame_geometry(W, H, values, band)
    assert rect["y0"] == pytest.approx(g.y_of(25))
    assert rect["y1"] == pytest.approx(g.y_of(15))
    assert (rect["x0"], rect["x1"]) == pytest.approx((LEFT, LEFT + WIDTH))


def test_an_open_band_end_runs_to_the_edge_of_the_plot() -> None:
    values = [10, 20, 30]
    (rect,) = of_kind(PW.layout(rows_of(values), METRIC, {"lo": None, "hi": 25}, -1, W, H), "rect", BAND)
    g = PW.frame_geometry(W, H, values, {"lo": None, "hi": 25})
    assert rect["y0"] == pytest.approx(g.y_of(25))
    assert rect["y1"] == pytest.approx(TOP + HEIGHT)


def test_edge_labels_are_listed_first_and_spaced() -> None:
    # 15 and 15.1 are a fraction of a pixel apart: the first to claim a position keeps it, the other is dropped.
    shapes = PW.layout(rows_of([10, 20, 30]), METRIC, {"lo": 15, "hi": 15.1}, -1, W, H)
    texts = of_kind(shapes, "text")
    edge_texts = [t for t in texts if t["fill"] == EDGE]
    assert [t["text"] for t in edge_texts] == [F.round_display(15)]
    ys = sorted(t["y"] for t in texts)
    assert all(b - a >= fu.LABEL_GAP for a, b in zip(ys, ys[1:]))
    assert len(of_kind(shapes, "line", EDGE)) == 2  # both edge lines are still drawn


def test_none_and_nan_values_are_skipped_and_break_the_line() -> None:
    rows = [row(10), row(None), row(math.nan), row(30)]
    shapes = PW.layout(rows, METRIC, None, -1, W, H)
    ms = markers(shapes)
    assert [round(centre(m)[0], 6) for m in ms] == pytest.approx([LEFT, LEFT + WIDTH])
    assert of_kind(shapes, "line", LINE) == []  # the gap is not bridged
    g = PW.frame_geometry(W, H, [10, None, math.nan, 30], None)
    assert g.bounds == FP.plot_bounds([10, 30])


def test_a_single_frame_sits_in_the_middle() -> None:
    shapes = PW.layout([row(12)], METRIC, None, -1, W, H)
    (m,) = markers(shapes)
    assert centre(m)[0] == pytest.approx(LEFT + WIDTH / 2)
    assert of_kind(shapes, "line", LINE) == []


def test_an_empty_series_draws_the_paper_and_the_axis_only() -> None:
    shapes = PW.layout([], METRIC, {"lo": None, "hi": None}, -1, W, H)
    assert shapes[0] == {"kind": "rect", "x0": 0, "y0": 0, "x1": W, "y1": H, "fill": PAPER}
    assert markers(shapes) == [] and of_kind(shapes, "oval") == []


def test_the_selection_ring_is_on_the_selected_frame_and_drawn_last() -> None:
    rows = [row(10), row(20), row(30)]
    shapes = PW.layout(rows, METRIC, None, 1, W, H)
    (ring,) = of_kind(shapes, "oval", PICKED)
    m = markers(shapes)[1]
    assert centre(ring) == pytest.approx(centre(m))
    assert ring["x1"] - ring["x0"] == pytest.approx(2 * fu.PICK_RADIUS)
    assert shapes[-1] is ring


@pytest.mark.parametrize("selected", [-1, 7])
def test_no_ring_without_a_selected_point(selected: int) -> None:
    assert of_kind(PW.layout(rows_of([10, 20, 30]), METRIC, None, selected, W, H), "oval") == []


def test_no_ring_on_a_frame_with_no_value() -> None:
    assert of_kind(PW.layout([row(10), row(None)], METRIC, None, 1, W, H), "oval") == []


def test_a_rejected_frame_is_a_cross_and_a_rescued_one_is_a_marker() -> None:
    rows = [row(10), row(20, state=F.REJECTED), row(30, state=F.REJECTED, override=F.RESCUED)]
    shapes = PW.layout(rows, METRIC, None, -1, W, H)
    crosses = of_kind(shapes, "line", REJECT)
    assert len(crosses) == 2
    assert len(markers(shapes)) == 2  # the first and the rescued third
    cross_centre = (crosses[0]["x0"] + crosses[0]["x1"]) / 2
    assert cross_centre == pytest.approx(LEFT + WIDTH / 2)


def test_the_value_labels_show_the_band_ends_and_the_axis_extremes() -> None:
    shapes = PW.layout(rows_of([10, 20, 30]), METRIC, {"lo": 15, "hi": 25}, -1, W, H)
    texts = {t["text"] for t in of_kind(shapes, "text")}
    g = PW.frame_geometry(W, H, [10, 20, 30], {"lo": 15, "hi": 25})
    assert F.round_display(g.bounds["hi"]) in texts
    assert F.round_display(g.bounds["lo"]) in texts
    assert F.round_display(25) in texts


def test_a_click_maps_to_the_nearest_frame_through_point_at() -> None:
    left, width = PW.hit_geometry(rows_of([10, 20, 30]), METRIC, None, W, H)
    assert (left, width) == pytest.approx((LEFT, WIDTH))
    assert FP.point_at(left, left, width, 3) == 0
    assert FP.point_at(left + width / 2, left, width, 3) == 1
    assert FP.point_at(left + width, left, width, 3) == 2
    assert FP.point_at(left - 1, left, width, 3) == -1  # outside: not pulled back to the first frame
    assert FP.point_at(left + width + 1, left, width, 3) == -1


def test_a_click_on_a_single_frame_selects_it() -> None:
    left, width = PW.hit_geometry([row(12)], METRIC, None, W, H)
    assert FP.point_at(left + width / 3, left, width, 1) == 0


def test_the_geometry_has_a_span_even_when_the_values_are_flat() -> None:
    g = PW.frame_geometry(W, H, [0, 0, 0], None)
    assert g.span == pytest.approx(1.0)
    assert g.bounds == {"lo": -0.5, "hi": 0.5}


def rows_of(values: list[Any]) -> list[dict[str, Any]]:
    return [row(v) for v in values]


# ---- Tk: a hidden root, skipped without a display

@pytest.fixture
def tk_root():
    try:
        import tkinter
    except (ImportError, OSError) as exc:
        pytest.skip(f"tkinter cannot be imported here: {exc}")
    from loomsiril.tk_theme import close_root, themed_root

    try:
        root = themed_root()
    except tkinter.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    yield root
    close_root(root)


def _plot(root: Any) -> PW.FramesPlot:
    plot = PW.FramesPlot(root, width=W, height=H)
    plot.pack()
    root.update()
    return plot


def test_the_canvas_draws_one_item_per_shape_of_the_layout(tk_root) -> None:
    plot = _plot(tk_root)
    rows = rows_of([10, 20, 30])
    plot.set_series(rows, METRIC, {"lo": 15, "hi": None})
    plot.set_selected(1)
    shapes = PW.layout(rows, METRIC, {"lo": 15, "hi": None}, 1, plot.winfo_width(), plot.winfo_height())
    items = plot.find_all()
    assert len(items) == len(shapes)
    by_type: dict[str, int] = {}
    for item in items:
        by_type[plot.type(item)] = by_type.get(plot.type(item), 0) + 1
    assert by_type["rectangle"] == sum(1 for s in shapes if s["kind"] in ("rect", "frame"))
    assert by_type["line"] == sum(1 for s in shapes if s["kind"] == "line")
    assert by_type["oval"] == 1
    assert by_type["text"] == sum(1 for s in shapes if s["kind"] == "text")


def test_a_click_selects_the_frame_under_it(tk_root) -> None:
    plot = _plot(tk_root)
    picked: list[int] = []
    plot.on_pick = picked.append
    plot.set_series(rows_of([10, 20, 30]), METRIC, None)
    left, width = plot.geom
    plot.mouse_press(left + width / 2)
    plot.mouse_press(left - 5)  # outside the plot: no frame
    assert picked == [1]


def test_a_click_does_nothing_after_release(tk_root) -> None:
    plot = _plot(tk_root)
    picked: list[int] = []
    plot.on_pick = picked.append
    plot.set_series(rows_of([10, 20, 30]), METRIC, None)
    left, width = plot.geom
    plot.release()
    plot.mouse_press(left)
    assert picked == [] and plot.on_pick is None and plot.rows == []


def test_set_selected_repaints_only_when_the_selection_changes(tk_root, monkeypatch) -> None:
    plot = _plot(tk_root)
    plot.set_series(rows_of([10, 20, 30]), METRIC, None)
    calls: list[int] = []
    real_paint = plot.paint
    monkeypatch.setattr(plot, "paint", lambda: (calls.append(1), real_paint()))
    plot.set_selected(-1)  # already -1: no repaint
    assert calls == []
    plot.set_selected(2)
    assert calls == [1]
    assert len(plot.find_withtag("all")) == len(
        PW.layout(rows_of([10, 20, 30]), METRIC, None, 2, plot.winfo_width(), plot.winfo_height()))
    plot.set_selected(None)  # None is -1: a change, so one more repaint, and no ring left
    assert calls == [1, 1]
    assert "oval" not in {plot.type(item) for item in plot.find_all()}


def test_paint_on_a_destroyed_widget_does_nothing(tk_root) -> None:
    plot = _plot(tk_root)
    plot.set_series(rows_of([10, 20, 30]), METRIC, None)
    plot.destroy()
    plot.paint()                                    # the widget is gone: no TclError
    plot.set_selected(2)
    plot.set_series(rows_of([1, 2]), METRIC, None)
