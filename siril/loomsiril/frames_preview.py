"""Frame Selector: the 1:1 preview of one frame (tkinter, Siril's theme), ported from ``pixinsight/script/FrameSelector.js``.

The preview is ``FrameSelector.PreviewControl`` (FrameSelector.js 1489-1932). Its geometry is pure and lives in
:class:`View` (no Tk, so it is tested without a display); :class:`PreviewControl` is the widget: a Tk canvas with two
ttk scroll bars, and the handlers that the JavaScript binds to the viewport. The JS function names are kept in
snake_case, each with its line range in FrameSelector.js:

    PreviewControl constructor (the handlers)  1491-1614 -> PreviewControl.__init__, _on_press, _on_motion,
                                                             _on_release, _on_double, _on_key
    setFit                  1616-1627 -> View.set_fit, PreviewControl.set_fit
    centreOn                1629-1651 -> View.centre_on, PreviewControl.centre_on
    fittedPixelAt           1653-1671 -> View.fitted_pixel_at
    layOutScroll            1673-1739 -> View.lay_out, PreviewControl.lay_out_scroll
    pan                     1741-1752 -> View.pan, PreviewControl.pan
    paintViewport           1754-1805 -> PreviewControl.paint_viewport (its geometry: View.fitted_rect, View.origin)
    dispose                 1807-1811 -> PreviewControl.dispose
    setTags                 1813-1821 -> PreviewControl.set_tags
    tagRects                1823-1833 -> View.tag_rects
    paintTags               1835-1846 -> PreviewControl.paint_tags (colours: tag_colours)
    thumbnail               1848-1867 -> PreviewControl.thumbnail
    release                 1869-1891 -> PreviewControl.release
    load                    1893-1932 -> PreviewControl.load (the frame itself: frames_render.render_frame)

Differences from the JavaScript, all deliberate:

* There is no mouse wheel handler, as in FrameSelector.js 1497-1512: a wheel gives no sideways scroll. Panning is the
  scroll bars, a drag, or the arrow keys.
* Tk photo images cannot be scaled by a fraction, so the fitted frame is drawn from nearest-neighbour samples of the
  rendered frame, at the size :meth:`View.fitted_rect` gives. The 1:1 view is the whole frame, offset on the canvas.
* The scroll bars are shown only on an axis that has something to scroll (layOutScroll 1708-1738 does the same).
* Frames are read through :func:`loomsiril.frames_render.render_frame`; :meth:`PreviewControl.show` takes a rendered
  array directly, which is how a frame that is already in memory is shown.
* The JS Util.warn for a missing file is a log warning.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import numpy as np

from loomsiril.core import js_round
from loomsiril.frames_render import render_frame, resample_area
from loomsiril.frames_ui import SCROLL_LINE, TAG, TAG_COLOURS, THUMB, tk_colour

__all__ = ["ARROW_KEYS", "BACKDROP", "MIN_HEIGHT", "MIN_WIDTH", "PreviewControl", "View", "tag_colours"]

log = logging.getLogger(__name__)

#: The keysyms of the arrows that pan the view (onKeyPress, FrameSelector.js 1618-1621 of the constructor).
ARROW_KEYS = ("Left", "Right", "Up", "Down")
#: The viewport's backdrop (paintViewport, FrameSelector.js 1760: 0xff101010).
BACKDROP = "#101010"
#: The widget's minimum size (the constructor's setScaledMinSize(420, 360)).
MIN_WIDTH = 420
MIN_HEIGHT = 360


def tag_colours(kind: Any) -> dict[str, int]:
    """The colours of one tag (paintTags, FrameSelector.js 1840): the kind's, else the anomaly colours."""
    return TAG_COLOURS.get(kind) or TAG_COLOURS["flag"]


@dataclass
class View:
    """The preview's geometry in pixels, with no Tk: the viewport, the loaded frame, whether the frame is fitted, and the
    scroll position ``x``, ``y`` (the image pixel at the viewport's top left, used at 1:1).

    An absent frame, or a fitted one, has no scroll range: the position is then 0 (layOutScroll sets the range to zero).
    """

    viewport_w: int = 0
    viewport_h: int = 0
    image_w: int = 0
    image_h: int = 0
    fit: bool = False
    x: int = 0
    y: int = 0

    @property
    def has_image(self) -> bool:
        return self.image_w > 0 and self.image_h > 0

    def max_x(self) -> int:
        """maxHorizontalScrollPosition: the frame beyond the viewport, or 0 (fitted, or no frame)."""
        if not self.has_image or self.fit:
            return 0
        return max(0, self.image_w - self.viewport_w)

    def max_y(self) -> int:
        """maxVerticalScrollPosition: the frame beyond the viewport, or 0 (fitted, or no frame)."""
        if not self.has_image or self.fit:
            return 0
        return max(0, self.image_h - self.viewport_h)

    def lay_out(self) -> None:
        """layOutScroll (FrameSelector.js 1673-1739): the position is clamped to the range, so a position kept from an
        earlier frame stays where it was when the new frame is as big, and is pulled in when it is smaller."""
        self.x = min(max(self.x, 0), self.max_x())
        self.y = min(max(self.y, 0), self.max_y())

    def set_viewport(self, width: int, height: int) -> None:
        self.viewport_w = int(width)
        self.viewport_h = int(height)
        self.lay_out()

    def set_image(self, width: int, height: int) -> None:
        """A frame of this size is loaded (0 by 0 for none); the position is kept and clamped."""
        self.image_w = int(width)
        self.image_h = int(height)
        self.lay_out()

    def set_fit(self, on: Any) -> None:
        """setFit (FrameSelector.js 1616-1627)."""
        self.fit = bool(on)
        self.lay_out()

    def pan(self, dx: int, dy: int) -> None:
        """pan (FrameSelector.js 1741-1752): moves the position by (dx, dy), clamped; nothing while fitted or with no
        frame."""
        if not self.has_image or self.fit:
            return
        self.x = min(max(self.x + int(dx), 0), self.max_x())
        self.y = min(max(self.y + int(dy), 0), self.max_y())

    def move_to(self, axis: str, fraction: float) -> None:
        """A scroll bar's ``moveto``: the position at ``fraction`` of the frame's size on ``axis`` ("x" or "y")."""
        if axis == "x":
            self.x = min(max(js_round(fraction * self.image_w), 0), self.max_x())
        else:
            self.y = min(max(js_round(fraction * self.image_h), 0), self.max_y())

    def centre_on(self, point: Optional[tuple[float, float]]) -> None:
        """centreOn (FrameSelector.js 1629-1651): puts the image pixel ``point`` under the middle of the viewport. It is
        clamped to the range rather than refused, so a point near an edge is as central as the frame allows."""
        if point is None or not self.has_image or self.fit:
            return
        self.x = min(max(js_round(point[0] - self.viewport_w / 2), 0), self.max_x())
        self.y = min(max(js_round(point[1] - self.viewport_h / 2), 0), self.max_y())

    def fitted_scale(self) -> float:
        """The scale that shows the whole frame in the viewport: the smaller of the two ratios; 0 with no frame."""
        if not self.has_image:
            return 0.0
        return min(self.viewport_w / self.image_w, self.viewport_h / self.image_h)

    def fitted_rect(self) -> Optional[tuple[int, int, int, int]]:
        """Where the fitted frame is drawn, as (x, y, width, height): scaled as fitted_scale, centred on both axes, so a
        band is left over on the long axis (paintViewport, FrameSelector.js 1754-1805, the fitted branch). None with no
        frame."""
        if not self.has_image:
            return None
        s = self.fitted_scale()
        fw = js_round(self.image_w * s)
        fh = js_round(self.image_h * s)
        return (js_round((self.viewport_w - fw) / 2), js_round((self.viewport_h - fh) / 2), fw, fh)

    def fitted_pixel_at(self, x: float, y: float) -> Optional[tuple[float, float]]:
        """fittedPixelAt (FrameSelector.js 1653-1671): the image pixel under viewport point (x, y) while fitted, allowing
        for the centring band. None with no frame, or when the scale is not positive."""
        if not self.has_image:
            return None
        s = self.fitted_scale()
        if not s > 0:
            return None
        fx = (self.viewport_w - self.image_w * s) / 2
        fy = (self.viewport_h - self.image_h * s) / 2
        return ((x - fx) / s, (y - fy) / s)

    def origin(self) -> tuple[int, int]:
        """Where the 1:1 frame's top left goes on the canvas (paintViewport, the 1:1 branch): offset by the scroll position
        on an axis that scrolls, and centred on an axis that does not."""
        ox = -self.x if self.max_x() > 0 else js_round((self.viewport_w - self.image_w) / 2)
        oy = -self.y if self.max_y() > 0 else js_round((self.viewport_h - self.image_h) / 2)
        return (ox, oy)

    def scroll_fractions(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """The visible part of the frame on each axis as the (first, last) fractions a scroll bar shows; (0.0, 1.0) on an
        axis with nothing to scroll."""

        def part(pos: int, view: int, size: int, top: int) -> tuple[float, float]:
            if top <= 0 or size <= 0:
                return (0.0, 1.0)
            return (pos / size, min(1.0, (pos + view) / size))

        return (part(self.x, self.viewport_w, self.image_w, self.max_x()),
                part(self.y, self.viewport_h, self.image_h, self.max_y()))

    def key_step(self, key: str, shift: bool = False) -> Optional[tuple[int, int]]:
        """The pan an arrow key asks for (onKeyPress, FrameSelector.js constructor): a quarter of the viewport's width per
        press, or the whole width with Shift, in the arrow's direction. The step is the width for both axes, as in the
        JavaScript. None for any other key."""
        step = self.viewport_w if shift else js_round(self.viewport_w / 4)
        return {"Left": (-step, 0), "Right": (step, 0), "Up": (0, -step), "Down": (0, step)}.get(key)

    def tag_rects(self, count: int, width: Optional[int] = None) -> list[tuple[int, int, int, int]]:
        """tagRects (FrameSelector.js 1823-1833): the boxes of ``count`` tags, stacked from the top right of the viewport
        (``width`` defaults to the viewport's), as (x0, y0, x1, y1)."""
        vw = self.viewport_w if width is None else width
        out = []
        for i in range(count):
            y = TAG["MARGIN"] + i * (TAG["H"] + TAG["GAP"])
            out.append((vw - TAG["MARGIN"] - TAG["W"], y, vw - TAG["MARGIN"], y + TAG["H"]))
        return out


def _ppm(img: np.ndarray) -> bytes:
    """A uint8 frame as a binary PGM (one channel) or PPM (three): the formats a Tk photo image reads from bytes."""
    if img.ndim == 2:
        magic = b"P5"
    elif img.ndim == 3 and img.shape[2] == 3:
        magic = b"P6"
    else:
        raise ValueError(f"a preview needs (h, w) or (h, w, 3), not {img.shape}")
    h, w = img.shape[0], img.shape[1]
    return magic + b"\n%d %d\n255\n" % (w, h) + np.ascontiguousarray(img, dtype=np.uint8).tobytes()


def _sample(img: np.ndarray, width: int, height: int) -> np.ndarray:
    """``img`` resized to ``width`` by ``height`` by nearest-neighbour sampling (the centre of each output pixel)."""
    h, w = img.shape[0], img.shape[1]
    rows = np.minimum(((np.arange(height) + 0.5) * h / height).astype(np.int64), h - 1)
    cols = np.minimum(((np.arange(width) + 0.5) * w / width).astype(np.int64), w - 1)
    return img[rows][:, cols]


class PreviewControl:
    """The preview widget (PreviewControl): a canvas, a vertical and a horizontal scroll bar, and the frame on it.

    Build it with a master widget, pack or grid ``outer``. Frames are shown with :meth:`load` (a file, through the
    render cache) or :meth:`show` (a rendered uint8 array). Its state is in :attr:`view`.
    """

    def __init__(self, master: Any) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.view = View()
        #: The rendered frame (uint8, (h, w) or (h, w, 3)), or None.
        self.image: Optional[np.ndarray] = None
        #: What the frame is: each tag is {"text": str, "kind": str}, as frames_flags builds them.
        self.tags: list[dict[str, Any]] = []
        self._photo: Any = None
        self._fit_photo: Any = None
        self._fit_key: Optional[tuple[int, int, int]] = None
        #: The last pointer position while dragging, or None (JS dragging, lx, ly).
        self._drag: Optional[tuple[int, int]] = None
        self._bound: list[str] = []

        self.outer = ttk.Frame(master)
        self.outer.columnconfigure(0, weight=1)
        self.outer.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self.outer, width=MIN_WIDTH, height=MIN_HEIGHT, background=BACKDROP,
                                highlightthickness=0, borderwidth=0, takefocus=True)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar = ttk.Scrollbar(self.outer, orient="vertical", command=lambda *a: self._scroll("y", *a))
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar = ttk.Scrollbar(self.outer, orient="horizontal", command=lambda *a: self._scroll("x", *a))
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.vbar.grid_remove()
        self.hbar.grid_remove()

        self._bind("<Configure>", self._on_configure)
        self._bind("<ButtonPress-1>", self._on_press)
        self._bind("<B1-Motion>", self._on_motion)
        self._bind("<ButtonRelease-1>", self._on_release)
        self._bind("<Double-Button-1>", self._on_double)
        for key in ARROW_KEYS:
            self._bind(f"<{key}>", lambda e, k=key: self._on_key(k, False))
            self._bind(f"<Shift-{key}>", lambda e, k=key: self._on_key(k, True))

    def _bind(self, sequence: str, handler: Any) -> None:
        self.canvas.bind(sequence, handler)
        self._bound.append(sequence)

    # ---- events

    def _on_configure(self, event: Any) -> None:
        self.view.set_viewport(event.width, event.height)
        self.lay_out_scroll()

    def _on_press(self, event: Any) -> None:
        self.canvas.focus_set()          # the arrows need focus: it is taken on a click (focusStyle Click)
        self._drag = (event.x, event.y)

    def _on_motion(self, event: Any) -> None:
        if self._drag is None:
            return
        lx, ly = self._drag
        self.pan(lx - event.x, ly - event.y)
        self._drag = (event.x, event.y)

    def _on_release(self, event: Any) -> None:
        self._drag = None

    def _on_double(self, event: Any) -> str:
        """Toggles the whole frame and 1:1. Zooming in goes to the clicked pixel, which is worked out while still fitted
        (the only moment fittedPixelAt describes the layout). The double click's own press does not start a drag."""
        target = self.view.fitted_pixel_at(event.x, event.y) if self.view.fit else None
        self.set_fit(not self.view.fit)
        if not self.view.fit:
            self.centre_on(target)
        self._drag = None
        return "break"

    def _on_key(self, key: str, shift: bool) -> Optional[str]:
        step = self.view.key_step(key, shift)
        if step is None:
            return None
        self.pan(*step)
        return "break"                   # consumed, so the frame table does not also move on the same press

    def _scroll(self, axis: str, action: str, *args: Any) -> None:
        """A scroll bar's command: ``moveto`` a fraction, or ``scroll`` by units (a line) or pages (the viewport)."""
        if action == "moveto":
            self.view.move_to(axis, float(args[0]))
        elif action == "scroll":
            count = int(args[0])
            if axis == "x":
                step = self.view.viewport_w if args[1] == "pages" else SCROLL_LINE
                self.view.pan(count * step, 0)
            else:
                step = self.view.viewport_h if args[1] == "pages" else SCROLL_LINE
                self.view.pan(0, count * step)
        self._refresh()

    # ---- the view

    def set_fit(self, on: Any) -> None:
        self.view.set_fit(on)
        self.lay_out_scroll()

    def centre_on(self, point: Optional[tuple[float, float]]) -> None:
        self.view.centre_on(point)
        self._refresh()

    def fitted_pixel_at(self, x: float, y: float) -> Optional[tuple[float, float]]:
        return self.view.fitted_pixel_at(x, y)

    def pan(self, dx: int, dy: int) -> None:
        self.view.pan(dx, dy)
        self._refresh()

    def lay_out_scroll(self) -> None:
        """layOutScroll: the range is laid out, the bars shown on the axes that scroll, and the picture drawn."""
        self.view.lay_out()
        self._refresh()

    def _refresh(self) -> None:
        """The scroll bars' positions and visibility, then the picture."""
        (x0, x1), (y0, y1) = self.view.scroll_fractions()
        if self.view.max_x() > 0:
            self.hbar.grid()
            self.hbar.set(x0, x1)
        else:
            self.hbar.grid_remove()
        if self.view.max_y() > 0:
            self.vbar.grid()
            self.vbar.set(y0, y1)
        else:
            self.vbar.grid_remove()
        self.paint_viewport()

    def paint_viewport(self) -> None:
        """paintViewport (FrameSelector.js 1754-1805): the frame (fitted, or 1:1 offset by the scroll position) and then
        the tags, in viewport coordinates on every path."""
        try:
            self.canvas.delete("all")
            if self.image is not None:
                if self.view.fit:
                    rect = self.view.fitted_rect()
                    if rect is not None and rect[2] > 0 and rect[3] > 0:
                        fx, fy, fw, fh = rect
                        key = (id(self.image), fw, fh)
                        if self._fit_key != key:
                            self._fit_photo = self._photo_of(_sample(self.image, fw, fh))
                            self._fit_key = key
                        self.canvas.create_image(fx, fy, image=self._fit_photo, anchor="nw")
                else:
                    ox, oy = self.view.origin()
                    self.canvas.create_image(ox, oy, image=self._photo, anchor="nw")
            self.paint_tags()
        except Exception as e:  # a preview that cannot paint must not stop the review
            log.debug("the preview could not be painted: %s", e)

    def paint_tags(self) -> None:
        """paintTags (FrameSelector.js 1835-1846): one box per tag at the top right, its text centred."""
        rects = self.view.tag_rects(len(self.tags))
        for tag, (x0, y0, x1, y1) in zip(self.tags, rects):
            colours = tag_colours(tag.get("kind"))
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=tk_colour(colours["fill"]), outline="")
            self.canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=str(tag.get("text", "")),
                                    fill=tk_colour(colours["ink"]))

    def _photo_of(self, img: np.ndarray) -> Any:
        import tkinter as tk

        return tk.PhotoImage(master=self.canvas, data=_ppm(img))

    def set_tags(self, tags: Optional[list[dict[str, Any]]]) -> None:
        """setTags (FrameSelector.js 1813-1821): what the frame is; None is no tags."""
        self.tags = [] if tags is None else list(tags)
        self.paint_viewport()

    def dispose(self) -> None:
        """dispose (FrameSelector.js 1807-1811): drops the frame; the photo images go with it."""
        self.image = None
        self._photo = None
        self._fit_photo = None
        self._fit_key = None
        self.view.set_image(0, 0)
        try:
            self.paint_viewport()
        except Exception:
            pass

    def show(self, image: np.ndarray) -> None:
        """Shows a rendered frame (uint8, (h, w) or (h, w, 3)): the whole frame's photo is made once here, the scroll
        range is laid out, and the position is kept so the same part of the sky stays in view."""
        img = np.asarray(image)
        if img.dtype != np.uint8:
            raise ValueError(f"a preview is 8-bit, not {img.dtype}")
        img = np.ascontiguousarray(img)
        self._photo = self._photo_of(img)
        self._fit_photo = None
        self._fit_key = None
        self.image = img
        self.view.set_image(img.shape[1], img.shape[0])
        self.lay_out_scroll()

    def load(self, path: str, stretch: Optional[Mapping[str, float]], cache: Any, siril: Any = None) -> bool:
        """load (FrameSelector.js 1893-1932): shows the frame at ``path``, stretched by ``stretch`` and taken from the
        render cache when it is there. False, with the preview left empty, when the file is gone or cannot be read."""
        self.dispose()
        if not os.path.exists(path):
            log.warning("no preview, the file is gone: %s", path)
            return False
        img = render_frame(path, "preview", stretch, cache, siril)
        if img is None:
            return False
        self.show(img)
        return True

    def thumbnail(self) -> Optional[np.ndarray]:
        """thumbnail (FrameSelector.js 1848-1867): the loaded frame scaled to fit a filmstrip tile (THUMB), aspect kept,
        as a uint8 array; None with no frame."""
        if self.image is None:
            return None
        h, w = self.image.shape[0], self.image.shape[1]
        s = min(THUMB["W"] / w, THUMB["H"] / h)
        x = self.image.astype(np.float32) / 255.0
        if x.ndim == 3:
            x = np.transpose(x, (2, 0, 1))
        y = resample_area(x, s)
        if y.ndim == 3:
            y = np.transpose(y, (1, 2, 0))
        return np.floor(np.clip(y, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)

    def release(self) -> None:
        """release (FrameSelector.js 1869-1891): the frame, its tags and photos go, and the canvas's bindings are removed,
        so no event reaches the widget while it is being destroyed. Never raises."""
        import tkinter as tk

        self.image = None
        self._photo = None
        self._fit_photo = None
        self._fit_key = None
        self.tags = []
        self.view.set_image(0, 0)
        try:
            for sequence in self._bound:
                self.canvas.unbind(sequence)
        except tk.TclError:
            pass
        self._bound = []
