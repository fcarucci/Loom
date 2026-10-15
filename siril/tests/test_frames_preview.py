"""The Frame Selector's 1:1 preview (loomsiril.frames_preview): the view's geometry, checked by hand against the formulas of
FrameSelector.js (the pan clamps, the arrow steps with JavaScript's rounding, the fitted layout and its inverse, the tag
boxes), and a widget smoke test that shows a frame, toggles the fit and sends key events.

The geometry tests need no display. The widget tests create widgets under a hidden root and skip when tkinter is not
importable or there is no display.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from loomsiril import frames_preview as P
from loomsiril.frames_preview_cache import PreviewCache


# ---- pure: the view

def view(vw: int = 200, vh: int = 100, iw: int = 300, ih: int = 180, fit: bool = False) -> P.View:
    v = P.View(viewport_w=vw, viewport_h=vh, image_w=iw, image_h=ih, fit=fit)
    v.lay_out()
    return v


def test_pan_clamps_at_the_edges() -> None:
    v = view()
    assert (v.max_x(), v.max_y()) == (100, 80)
    v.pan(-50, -50)
    assert (v.x, v.y) == (0, 0)
    v.pan(1000, 1000)
    assert (v.x, v.y) == (100, 80)
    v.pan(-30, 0)
    assert (v.x, v.y) == (70, 80)


def test_an_axis_that_fits_has_no_range() -> None:
    v = view(vw=400, vh=100, iw=300, ih=180)
    assert v.max_x() == 0 and v.max_y() == 80
    v.pan(500, 500)
    assert (v.x, v.y) == (0, 80)


def test_arrow_steps_are_a_quarter_of_the_width_and_shift_is_a_page() -> None:
    v = view(vw=200)
    assert v.key_step("Left") == (-50, 0)
    assert v.key_step("Right") == (50, 0)
    assert v.key_step("Up") == (0, -50)
    assert v.key_step("Down") == (0, 50)
    assert v.key_step("Left", True) == (-200, 0)
    assert v.key_step("Down", True) == (0, 200)
    assert v.key_step("Return") is None


def test_the_quarter_step_rounds_halves_up_as_javascript_does() -> None:
    # Math.round(10 / 4) is 3 in JavaScript; Python's round(2.5) would give 2.
    assert view(vw=10).key_step("Right") == (3, 0)
    assert view(vw=6).key_step("Right") == (2, 0)


def test_an_arrow_pans_by_its_step_and_clamps() -> None:
    v = view()
    v.pan(*v.key_step("Right"))
    assert v.x == 50
    v.pan(*v.key_step("Right", True))
    assert v.x == 100
    v.pan(*v.key_step("Left", True))
    assert v.x == 0


def test_fit_toggle_resets_the_position_and_ignores_pans() -> None:
    v = view()
    v.pan(60, 40)
    v.set_fit(True)
    assert (v.x, v.y) == (0, 0)
    v.pan(60, 40)
    assert (v.x, v.y) == (0, 0)
    v.set_fit(False)
    assert (v.x, v.y) == (0, 0)
    v.pan(60, 40)
    assert (v.x, v.y) == (60, 40)


def test_the_centre_is_kept_across_a_frame_of_the_same_size() -> None:
    v = view()
    v.pan(50, 30)
    v.set_image(300, 180)
    assert (v.x, v.y) == (50, 30)


def test_the_centre_is_clamped_for_a_smaller_frame() -> None:
    v = view()
    v.pan(90, 70)
    v.set_image(250, 150)          # max x 50, max y 50
    assert (v.x, v.y) == (50, 50)
    v.set_image(220, 120)          # max x 20, max y 20
    assert (v.x, v.y) == (20, 20)


def test_centre_on_puts_the_point_in_the_middle_and_clamps() -> None:
    v = view()
    v.centre_on((100, 60))         # 100 - 100 = 0, 60 - 50 = 10
    assert (v.x, v.y) == (0, 10)
    v.centre_on((250, 150))        # 150 and 100, clamped to 100 and 80
    assert (v.x, v.y) == (100, 80)
    v.centre_on(None)
    assert (v.x, v.y) == (100, 80)


def test_centre_on_does_nothing_while_fitted() -> None:
    v = view(fit=True)
    v.centre_on((250, 150))
    assert (v.x, v.y) == (0, 0)


def test_the_fitted_frame_is_centred_with_a_band_on_the_long_axis() -> None:
    v = view(vw=300, vh=300, iw=200, ih=100, fit=True)
    assert v.fitted_scale() == 1.5
    assert v.fitted_rect() == (0, 75, 300, 150)


def test_fitted_pixel_at_inverts_the_fitted_layout() -> None:
    v = view(vw=300, vh=300, iw=200, ih=100, fit=True)
    fx, fy, fw, fh = v.fitted_rect()
    s = v.fitted_scale()
    for px, py in [(40, 20), (0, 0), (199, 99), (100, 50)]:
        screen = (fx + px * s, fy + py * s)
        got = v.fitted_pixel_at(*screen)
        assert got is not None
        assert got == pytest.approx((px, py), abs=1e-9)
    # a point in the band above the frame maps above the image
    assert v.fitted_pixel_at(10, 10) == pytest.approx(((10 - 0) / 1.5, (10 - 75) / 1.5))


def test_fitted_pixel_at_is_none_without_a_frame() -> None:
    assert P.View(viewport_w=300, viewport_h=300).fitted_pixel_at(1, 1) is None
    assert P.View(viewport_w=300, viewport_h=300).fitted_rect() is None


def test_the_origin_offsets_a_scrolling_axis_and_centres_the_other() -> None:
    v = view(vw=200, vh=100, iw=150, ih=180)     # x fits (no range), y scrolls
    v.pan(0, 30)
    assert v.origin() == (25, -30)
    v.set_image(300, 180)
    v.pan(60, 0)
    assert v.origin() == (-60, -30)


def test_scroll_fractions_show_the_visible_part() -> None:
    v = view(vw=200, vh=100, iw=400, ih=200)
    v.pan(100, 0)
    assert v.scroll_fractions() == ((0.25, 0.75), (0.0, 0.5))
    v.pan(1000, 1000)
    assert v.scroll_fractions() == ((0.5, 1.0), (0.5, 1.0))
    assert view(vw=500, vh=500, iw=400, ih=200).scroll_fractions() == ((0.0, 1.0), (0.0, 1.0))


def test_a_scroll_bar_moveto_clamps() -> None:
    v = view()
    v.move_to("x", 0.5)            # 150, clamped to 100
    assert v.x == 100
    v.move_to("y", 0.25)           # 45
    assert v.y == 45


def test_tag_rects_stack_from_the_top_right() -> None:
    v = view(vw=300)
    assert v.tag_rects(2) == [(140, 10, 290, 36), (140, 44, 290, 70)]
    assert v.tag_rects(0) == []
    assert v.tag_rects(1, width=400) == [(240, 10, 390, 36)]


def test_tag_colours_fall_back_to_the_anomaly_colours() -> None:
    assert P.tag_colours("reject") == {"fill": 0xffb02222, "ink": 0xffffffff}
    assert P.tag_colours("flag") == {"fill": 0xffc08a1e, "ink": 0xff1a1a1a}
    assert P.tag_colours("something else") == P.tag_colours("flag")
    assert P.tag_colours(None) == P.tag_colours("flag")


def test_a_frame_is_sent_to_tk_as_a_binary_pnm() -> None:
    grey = np.arange(6, dtype=np.uint8).reshape(2, 3)
    rgb = np.zeros((2, 3, 3), dtype=np.uint8)
    assert P._ppm(grey) == b"P5\n3 2\n255\n" + bytes(range(6))
    assert P._ppm(rgb).startswith(b"P6\n3 2\n255\n") and len(P._ppm(rgb)) == len(b"P6\n3 2\n255\n") + 18
    with pytest.raises(ValueError):
        P._ppm(np.zeros((2, 3, 2), dtype=np.uint8))


def test_nearest_sampling_picks_the_centre_of_each_output_pixel() -> None:
    img = np.array([[0, 1], [2, 3]], dtype=np.uint8)
    assert P._sample(img, 4, 4).tolist() == [[0, 0, 1, 1], [0, 0, 1, 1], [2, 2, 3, 3], [2, 2, 3, 3]]
    assert P._sample(img, 2, 2).tolist() == img.tolist()


# ---- Tk: a hidden root, skipped without a display

@pytest.fixture
def tk_root():
    try:
        import tkinter
    except (ImportError, OSError) as exc:
        pytest.skip(f"tkinter cannot be imported here: {exc}")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tkinter.TclError:
        pass


def _frame(w: int = 300, h: int = 200) -> np.ndarray:
    y, x = np.mgrid[0:h, 0:w]
    return np.stack([x % 256, y % 256, (x + y) % 256], axis=2).astype(np.uint8)


def _size(ctl: P.PreviewControl, w: int = 200, h: int = 100) -> None:
    ctl._on_configure(SimpleNamespace(width=w, height=h))


def test_a_frame_is_shown_toggled_to_fit_and_back_at_the_clicked_pixel(tk_root: Any) -> None:
    ctl = P.PreviewControl(tk_root)
    ctl.show(_frame())
    _size(ctl)
    assert (ctl.view.x, ctl.view.y, ctl.view.fit) == (0, 0, False)

    ctl._on_double(SimpleNamespace(x=100, y=50))          # fitted: scale 0.5, the frame at (25, 0)
    assert ctl.view.fit is True
    ctl._on_double(SimpleNamespace(x=100, y=50))          # the pixel (150, 100), centred in 200 x 100
    assert ctl.view.fit is False
    assert (ctl.view.x, ctl.view.y) == (50, 50)
    assert ctl.canvas.find_all()                          # the frame is on the canvas


def test_drag_and_keys_pan_and_stop_at_the_edges(tk_root: Any) -> None:
    ctl = P.PreviewControl(tk_root)
    ctl.show(_frame())
    _size(ctl)
    ctl._on_press(SimpleNamespace(x=100, y=50))
    ctl._on_motion(SimpleNamespace(x=60, y=40))           # drag left 40, up 10: the image moves right
    assert (ctl.view.x, ctl.view.y) == (40, 10)
    ctl._on_release(SimpleNamespace())
    ctl._on_motion(SimpleNamespace(x=0, y=0))             # no drag: nothing moves
    assert (ctl.view.x, ctl.view.y) == (40, 10)
    assert ctl._on_key("Right", False) == "break"
    assert ctl.view.x == 90
    assert ctl._on_key("Right", True) == "break"
    assert ctl.view.x == 100
    assert ctl._on_key("Return", False) is None


def test_the_arrow_keys_are_bound_and_pan_the_view(tk_root: Any) -> None:
    ctl = P.PreviewControl(tk_root)
    ctl.show(_frame())
    _size(ctl)
    for key in P.ARROW_KEYS:
        assert ctl.canvas.bind(f"<{key}>") != ""
        assert ctl.canvas.bind(f"<Shift-{key}>") != ""
    tk_root.deiconify()
    tk_root.update()
    ctl.canvas.focus_force()
    tk_root.update()
    if tk_root.focus_get() is ctl.canvas:                 # a window manager gave the canvas focus: a real key
        ctl.canvas.event_generate("<Right>")
        tk_root.update()
        assert ctl.view.x == 50
        ctl.canvas.event_generate("<Shift-Left>")
        tk_root.update()
        assert ctl.view.x == 0
    else:                                                # no focus (no window manager): the bound handler itself
        ctl._on_key("Right", False)
        assert ctl.view.x == 50
        ctl._on_key("Left", True)
        assert ctl.view.x == 0


def test_a_fitted_frame_fills_the_pane_and_the_tags_sit_in_the_corner(tk_root: Any) -> None:
    ctl = P.PreviewControl(tk_root)
    ctl.show(_frame())
    _size(ctl)
    ctl.set_fit(True)
    ctl.set_tags([{"text": "REJECTED", "kind": "reject"}, {"text": "FOCUS", "kind": "flag"}])
    rects = ctl.view.tag_rects(2)
    coords = [ctl.canvas.coords(i) for i in ctl.canvas.find_withtag("all")]
    rect_items = [c for c in coords if len(c) == 4]
    assert [tuple(int(v) for v in c) for c in rect_items] == rects
    assert ctl.canvas.find_all()


def test_the_thumbnail_fits_the_tile_with_its_aspect_kept(tk_root: Any) -> None:
    ctl = P.PreviewControl(tk_root)
    assert ctl.thumbnail() is None
    ctl.show(_frame(300, 200))
    thumb = ctl.thumbnail()
    assert thumb.shape == (80, 120, 3) and thumb.dtype == np.uint8
    ctl.show(_frame(100, 400))
    assert ctl.thumbnail().shape == (80, 20, 3)


def test_show_rejects_a_frame_that_is_not_8_bit(tk_root: Any) -> None:
    ctl = P.PreviewControl(tk_root)
    with pytest.raises(ValueError):
        ctl.show(np.zeros((4, 4), dtype=np.float32))


def test_load_of_a_missing_file_leaves_the_preview_empty(tk_root: Any, tmp_path: Path) -> None:
    ctl = P.PreviewControl(tk_root)
    ctl.show(_frame())
    cache = PreviewCache(str(tmp_path / "cache"))
    assert ctl.load(str(tmp_path / "gone.fit"), None, cache) is False
    assert ctl.image is None and ctl.view.has_image is False


def test_release_removes_the_frame_and_the_bindings(tk_root: Any) -> None:
    ctl = P.PreviewControl(tk_root)
    ctl.show(_frame())
    ctl.set_tags([{"text": "FOCUS", "kind": "flag"}])
    ctl.release()
    assert ctl.image is None and ctl.tags == [] and ctl.thumbnail() is None
    assert ctl.canvas.bind("<Right>") == ""
    assert ctl.canvas.bind("<Double-Button-1>") == ""
