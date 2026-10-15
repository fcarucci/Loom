"""Frame Selector: the progress window, the message boxes, the row marks and the pure helpers they use (tkinter,
imported when a window or an image is made, never at import).

Ported from ``pixinsight/script/FrameSelector.js`` and ``pixinsight/script/lib/Util.js``. The line numbers are those
files', and each JS function has a Python twin with the same meaning, named in snake_case:

* ``FrameSelector.title`` (FrameSelector.js 44-49) -> :func:`frame_selector_title`. The Python title has no git
  commit in it: the caller passes one when it has one.
* ``FrameSelector.LABEL_GAP`` (1441), ``SCROLL_LINE`` (1448), ``TAG_COLOURS`` (1454-1455), ``TAG`` (1456),
  ``AUTO_STYLE`` (1459), ``THUMB`` (1462), ``THUMB_CAP`` (1463), ``PICK_RADIUS`` (1466) -> module constants of the
  same names. Colours are the JS ARGB integers; :func:`tk_colour` turns one into the ``#rrggbb`` Tk takes.
* ``FrameSelector.REJECT_COLOUR`` (4233), ``MARK_SIZE`` (4234) -> constants of the same names.
* ``FrameSelector.rejectIcon`` (4236-4262) -> :func:`reject_icon`: a red cross drawn pixel by pixel from
  :data:`REJECT_MASK`; it is None when the image cannot be made, which is what the JS null is.
* ``FrameSelector.blankIcon`` (4272-4284) -> :func:`blank_icon`: a transparent image of the same size.
* ``FrameSelector.markIcon`` (4287-4290) -> :func:`mark_icon`.
* ``FrameSelector.PLOT_COLOURS`` (4297-4307) -> :data:`PLOT_COLOURS`.
* ``FrameSelector.SCAN_NAME_CHARS`` (4704) -> :data:`SCAN_NAME_CHARS`.
* ``FrameSelector.ScanWindow`` (4706-4887) -> :class:`ScanWindow`: ``report`` (4809), ``display`` (4828),
  ``announce`` (4851), ``release`` (4857), ``callbacks`` (4868), and the pulse block when the total is unknown (the
  ``indeterminate`` branch of ``display``, 4832-4836). The window's widgets are laid out at 4724-4745, Cancel at 4777. The JS paints the bar in ``onPaint``; here :meth:`ScanWindow._paint_bar` draws it on a canvas.
  Cancel is :meth:`ScanWindow.cancel`. The window runs on the thread that owns Tk: long work calls ``report``,
  which updates the widgets and processes pending events, and reads ``cancelled``.
* ``FrameSelector.tell`` (4895-4898) and ``FrameSelector.ask`` (4900-4904) -> :func:`tell` and :func:`ask`, the
  tkinter message boxes. Their title is :func:`frame_selector_title` (the JS passes the literal "Loom Frame
  Selector"; the Python default has the same text).
* ``FrameSelector.withProgress`` (4916-4936) -> :func:`with_progress`: the window is shown for one step, always
  taken down and released again, and the step's result is returned.
* ``Util.elideHead`` (lib/Util.js 963-980) -> :func:`elide_head`. Lengths and cuts are in UTF-16 code units, as
  JS counts them.
* ``Util.pulseBlock`` (lib/Util.js 982-998) -> :func:`pulse_block`; ``Util.PULSE_PERIOD_MS`` (980) ->
  :data:`PULSE_PERIOD_MS`.

Nothing here reads a file or calls Siril. :class:`ScanWindow` takes a ``clock`` (milliseconds) so the pulse can be
tested without waiting.
"""

from __future__ import annotations

import math
import time
from types import SimpleNamespace
from typing import Any, Callable, Optional, TypeVar

from loomsiril import __version__
from loomsiril.core import js_round
from loomsiril.frames_files import measuring_lines
from loomsiril.jsmath import js_max, js_min, js_string

T = TypeVar("T")

__all__ = [
    "AUTO_STYLE", "LABEL_GAP", "MARK_SIZE", "PICK_RADIUS", "PLOT_COLOURS", "PULSE_PERIOD_MS", "REJECT_COLOUR",
    "REJECT_MASK", "SCAN_NAME_CHARS", "SCROLL_LINE", "TAG", "TAG_COLOURS", "THUMB", "THUMB_CAP", "ScanWindow",
    "ask", "blank_icon", "elide_head", "frame_selector_title", "mark_icon", "pulse_block", "reject_icon", "tell",
    "tk_colour", "with_progress",
]

#: How close two of the plot's numbers may come before one is dropped (FrameSelector.js 1441).
LABEL_GAP = 13
#: Pixels one scrolling unit moves the preview (FrameSelector.js 1448).
SCROLL_LINE = 48
#: The preview's tags: red for what Run leaves out, amber for an anomaly (FrameSelector.js 1454-1455).
TAG_COLOURS = {"reject": {"fill": 0xffb02222, "ink": 0xffffffff},
               "flag": {"fill": 0xffc08a1e, "ink": 0xff1a1a1a}}
#: The tag box (FrameSelector.js 1456).
TAG = {"W": 150, "H": 26, "GAP": 8, "MARGIN": 10}
#: A criterion box while it shows the automatic limit (FrameSelector.js 1459): the JS stylesheet, verbatim.
AUTO_STYLE = "QLineEdit { color: #8a8a8a; font-style: italic; }"
#: The same look for a Tk entry: the grey of AUTO_STYLE (italic is set on the font by the caller).
AUTO_FOREGROUND = "#8a8a8a"
#: A filmstrip tile's picture (FrameSelector.js 1462) and how many thumbnails are kept (1463).
THUMB = {"W": 120, "H": 80}
THUMB_CAP = 600
#: Radius of the ring round the selected frame (FrameSelector.js 1466).
PICK_RADIUS = 6

#: The colour of the reject cross (FrameSelector.js 4233) and the side of the row marks (4234).
REJECT_COLOUR = 0xffcc2222
MARK_SIZE = 12
#: The pen of the cross: its box starts ``_MARK_PAD`` pixels in (FrameSelector.js 4242, ``pad = 3``).
_MARK_PAD = 3

#: The cross as drawn on the MARK_SIZE square: one row per line, ``X`` where the pen covers the pixel. It is the
#: JS two-pixel pen from (pad, pad) to (s - pad - 1, s - pad - 1) and back, traced on the grid (FrameSelector.js
#: 4250-4251).
REJECT_MASK = (
    "............",
    "............",
    "............",
    "...XX..XX...",
    "....XXXX....",
    ".....XX.....",
    ".....XX.....",
    "....XXXX....",
    "...XX..XX...",
    "............",
    "............",
    "............",
)

#: The plot's palette, on paper (FrameSelector.js 4297-4307).
PLOT_COLOURS = {
    "PAPER": 0xffffffff,
    "BAND": 0xffe6e6e6,    # the range that keeps a frame
    "AXIS": 0xff9a9a9a,
    "EDGE": 0xff6e6e6e,    # the band's own limits, drawn and labelled
    "INK": 0xff404040,     # the axis numbers
    "LINE": 0xff1f6fb4,
    "REJECT": 0xffcc2222,
    "PICKED": 0xffe07000,  # the selected frame's ring, matching the table's highlight
}

#: How much of a frame's name the scan window shows (FrameSelector.js 4704).
SCAN_NAME_CHARS = 58
#: The moving block's period in milliseconds (lib/Util.js 980).
PULSE_PERIOD_MS = 1600

#: The scan bar's size in pixels (FrameSelector.js 4737-4742: 460 wide, 16 high).
_BAR_WIDTH = 460
_BAR_HEIGHT = 16
_BAR_BACKGROUND = 0xff202020
_BAR_FILL = 0xff3c8cd8
_BAR_BORDER = 0xff606060


def tk_colour(argb: int) -> str:
    """An ARGB integer as the ``#rrggbb`` string Tk takes (the alpha byte is dropped; Tk widgets are opaque)."""
    return "#%06x" % (argb & 0xFFFFFF)


def frame_selector_title(suffix: Optional[str] = None, version: str = __version__, sha: str = "") -> str:
    """The window title: "Loom Frame Selector <version> (<sha>) - <suffix>" (FrameSelector.js 44-49).

    The JS reads the commit from git; here the caller passes it, and an empty ``sha`` leaves it out as the JS does.
    """
    return ("Loom Frame Selector " + js_string(version) + (" (" + sha + ")" if sha else "")
            + (" - " + suffix if suffix else ""))


def _to_number(v: Any) -> float:
    """``Number(v)``: null is 0, a bool 0 or 1, a string its number or NaN."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        text = str(v).strip()
        return float(text) if text else 0.0
    except ValueError:
        return math.nan


def elide_head(text: Any, max_: Any) -> str:
    """Keeps the end of ``text`` and marks the cut with "...", within ``max_`` UTF-16 units (Util.js 963-980).

    The tail is what identifies a subframe (its timestamp and sequence number), so the head is the part dropped.
    Text that fits, a limit below 4 and a limit that is not a number are returned whole.
    """
    t = "" if text is None else js_string(text)
    n = _to_number(max_)
    units = t.encode("utf-16-le", "surrogatepass")
    length = len(units) // 2
    if not math.isfinite(n) or n < 4 or length <= n:
        return t
    start = int(length - (n - 3))  # JS substring() truncates a fractional start
    return "..." + units[2 * start:].decode("utf-16-le", "surrogatepass")


def pulse_block(elapsed_ms: Any, width: Any) -> dict[str, Any]:
    """The moving block of an unknown-total bar: ``{x, width}`` at ``elapsed_ms`` after the step began (Util.js
    982-998).

    The block is a quarter of the bar (at least 8 pixels) and goes to the end and back once per
    :data:`PULSE_PERIOD_MS`. A negative or non-number time puts it at the start.
    """
    w = js_max(8, _round(_to_number(width) / 4))
    travel = js_max(0, _to_number(width) - w)
    t = _to_number(elapsed_ms)
    if not math.isfinite(t) or t < 0:
        return {"x": 0, "width": w}
    phase = math.fmod(t, PULSE_PERIOD_MS) / PULSE_PERIOD_MS
    along = 2 * phase if phase < 0.5 else 2 - 2 * phase
    return {"x": _round(along * travel), "width": w}


def _round(v: float) -> float:
    """``Math.round`` that lets NaN through (js_round would fail on it)."""
    return v if math.isnan(v) else js_round(v)


def _draw_mark(master: Any, mask: tuple[str, ...], colour: int) -> Any:
    """A MARK_SIZE square PhotoImage: ``colour`` where the mask has an X, transparent elsewhere (None when Tk
    cannot make it)."""
    import tkinter as tk

    try:
        size = MARK_SIZE
        image = tk.PhotoImage(master=master, width=size, height=size)
        hex_colour = tk_colour(colour)
        for y, row in enumerate(mask):
            for x, cell in enumerate(row):
                if cell == "X":
                    image.put(hex_colour, to=(x, y))
                else:
                    image.transparency_set(x, y, True)
        return image
    except Exception:
        # A mark that cannot be drawn must not cost the review its rows.
        return None


_BLANK_MASK = ("." * MARK_SIZE,) * MARK_SIZE


def _cached(cache: Optional[dict[str, Any]], key: str, make: Callable[[], Any]) -> Any:
    """The image for ``key``, made once into ``cache`` (a None is kept too, as the JS keeps its null)."""
    if cache is None:
        return make()
    if key not in cache:
        cache[key] = make()
    return cache[key]


def reject_icon(master: Any, cache: Optional[dict[str, Any]] = None) -> Any:
    """The red cross for a rejected row (FrameSelector.js 4236-4262). Built once per ``cache``; None if it cannot
    be drawn. The caller keeps the returned image alive: Tk drops an image that nothing refers to."""
    return _cached(cache, "reject", lambda: _draw_mark(master, REJECT_MASK, REJECT_COLOUR))


def blank_icon(master: Any, cache: Optional[dict[str, Any]] = None) -> Any:
    """An empty mark of the same size, for a kept row (FrameSelector.js 4272-4284)."""
    return _cached(cache, "blank", lambda: _draw_mark(master, _BLANK_MASK, REJECT_COLOUR))


def mark_icon(master: Any, rejected: bool, cache: Optional[dict[str, Any]] = None) -> Any:
    """The mark a row carries: the cross when it is going, a spacer when not (FrameSelector.js 4287-4290)."""
    return reject_icon(master, cache) if rejected else blank_icon(master, cache)


class ScanWindow:
    """The scan's progress window: two lines (the count, then the elided name or the step's detail), a bar, and
    Cancel. Built withdrawn; :meth:`show` puts it up and :meth:`release` destroys it (FrameSelector.js 4706-4887).

    ``clock`` gives the time in milliseconds for the pulse (default: the monotonic clock).
    """

    def __init__(self, master: Any, *, title: Optional[str] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        import tkinter as tk

        self.cancelled = False
        self.fraction = 0.0
        #: A total nobody knows yet: the bar shows a moving block placed by the time since the first such report.
        self.indeterminate = False
        self.pulse_start: Optional[float] = None
        self.pulse_elapsed = 0.0
        self._clock = clock if clock is not None else (lambda: time.monotonic() * 1000.0)
        self.window_title = title if title is not None else frame_selector_title("scanning")

        self.top = tk.Toplevel(master)
        self.top.title(self.window_title)
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", self.cancel)
        self.top.withdraw()

        frame = tk.Frame(self.top, padx=8, pady=8)
        frame.pack(fill="both", expand=True)
        #: Two lines, never wrapped: the count on its own line, the name elided to fit under it.
        self.title_label = tk.Label(frame, text="starting...", anchor="w", justify="left", wraplength=0)
        self.title_label.pack(fill="x")
        self.detail_label = tk.Label(frame, text="", anchor="w", justify="left", wraplength=0)
        self.detail_label.pack(fill="x")
        self.bar = tk.Canvas(frame, width=_BAR_WIDTH, height=_BAR_HEIGHT, highlightthickness=0,
                             bd=0, bg=tk_colour(_BAR_BACKGROUND))
        self.bar.pack(fill="x", pady=(6, 0))
        row = tk.Frame(frame)
        row.pack(fill="x", pady=(4, 0))
        #: No confirmation, unlike the pipeline's run window: a scan has written nothing and thrown nothing away.
        self.cancel_button = tk.Button(row, text="Cancel", command=self.cancel)
        self.cancel_button.pack(side="right")

    def set_title(self, text: str) -> None:
        """The window's title (withProgress sets the title of the step)."""
        self.window_title = text
        self.top.title(text)

    def set_cancellable(self, enabled: bool) -> None:
        """Cancel is offered only for a step that can be stopped half way (withProgress' opts.cancellable)."""
        self.cancel_button.configure(state="normal" if enabled else "disabled")

    def show(self) -> None:
        """Puts the window up and lets it paint."""
        self.top.deiconify()
        self._pump()

    def hide(self) -> None:
        self.top.withdraw()

    def cancel(self) -> None:
        """Cancel: the scan reads ``cancelled`` and stops at its next report."""
        self.cancelled = True
        self.title_label.configure(text="stopping...")
        self.detail_label.configure(text="")
        self.cancel_button.configure(state="disabled")

    def report(self, phase: str, done: Any, total: Any, label: Any) -> bool:
        """One step of work: the count, then the name elided from the front (FrameSelector.js 4809-4826).

        An unknown total (``total`` 0) shows the count alone, never "of 0", and a moving block. Returns False once
        Cancel has been pressed.
        """
        count = js_string(done) + " of " + js_string(total) if total > 0 else js_string(done)
        return self.display(phase + " (" + count + ")", elide_head(label, SCAN_NAME_CHARS),
                            (done / total) if total > 0 else None)

    def display(self, title: str, detail: Optional[str], fraction: Optional[float]) -> bool:
        """Both lines and the bar. A fraction of None is a total nobody knows: the block moves on by the time since
        the first such call (FrameSelector.js 4828-4849). Returns False once Cancel has been pressed."""
        try:
            self.indeterminate = fraction is None
            if self.indeterminate:
                now = self._clock()
                if self.pulse_start is None:
                    self.pulse_start = now
                self.pulse_elapsed = now - self.pulse_start
            else:
                self.fraction = fraction
            self.title_label.configure(text=title)
            self.detail_label.configure(text=detail or "")
            self._paint_bar()
            self._pump()
        except Exception:
            pass  # as the JS: a window that cannot be updated must not stop the scan
        return not self.cancelled

    def announce(self, text: str) -> None:
        """A message with no count, for a step that has none yet (starting up)."""
        self.display(text, "", 0)

    def release(self) -> None:
        """Destroys the window. Detached first, as the JS releases its handlers (the window may already be gone)."""
        try:
            self.cancel_button.configure(command=lambda: None)
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass

    def callbacks(self) -> SimpleNamespace:
        """The pair of callbacks frames_scan expects: ``reading(done, total, name)`` and
        ``measuring(done, total, filter, overall)``, each returning False once Cancel has been pressed."""
        return SimpleNamespace(
            reading=lambda done, total, name: self.report("Reading frames", done, total, name),
            measuring=self._measuring,
        )

    def _measuring(self, done: int, total: int, filter_: str, overall: Optional[dict[str, Any]]) -> bool:
        lines = measuring_lines(filter_, done, total, overall)
        return self.display(lines["title"], lines["detail"], lines["fraction"])

    def _paint_bar(self) -> None:
        """The bar: the filled part (or the moving block), over the background, with its border."""
        w, h = _BAR_WIDTH, _BAR_HEIGHT
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, w, h, fill=tk_colour(_BAR_BACKGROUND), outline="")
        if self.indeterminate:
            block = pulse_block(self.pulse_elapsed, w)
            self.bar.create_rectangle(block["x"], 0, block["x"] + block["width"], h,
                                      fill=tk_colour(_BAR_FILL), outline="")
        else:
            filled = _round(w * js_max(0, js_min(1, self.fraction)))
            if filled > 0:
                self.bar.create_rectangle(0, 0, filled, h, fill=tk_colour(_BAR_FILL), outline="")
        self.bar.create_rectangle(0, 0, w - 1, h - 1, outline=tk_colour(_BAR_BORDER))

    def _pump(self) -> None:
        """Processes the pending Tk events, so the window repaints while the work runs (CoreApplication.processEvents)."""
        self.top.update()


def with_progress(master: Any, title: Optional[str], work: Callable[[ScanWindow], T], *,
                  cancellable: bool = False, before: Optional[Callable[[ScanWindow], None]] = None) -> T:
    """Runs one step under its own progress window, which is always taken down again, then released (FrameSelector.js
    4916-4936). Cancel is offered only when ``cancellable``. A None ``title`` keeps the window's own. ``before(w)``
    runs before the window is shown, so its first paint already says something. Returns ``work``'s result; an
    exception from ``work`` propagates after the window is released.
    """
    w = ScanWindow(master)
    try:
        if title is not None:
            w.set_title(title)
        w.set_cancellable(cancellable)
        if before is not None:
            before(w)
        w.show()
        return work(w)
    finally:
        try:
            w.hide()
        except Exception:
            pass
        try:
            w.release()
        except Exception:
            pass


def tell(text: str, icon: str = "info", *, master: Any = None, title: Optional[str] = None) -> None:
    """An information, warning or error box with OK (FrameSelector.js 4895-4898: StdIcon_Information,
    StdIcon_Warning, StdIcon_Error)."""
    from tkinter import messagebox

    shown = {"warning": messagebox.showwarning, "error": messagebox.showerror}.get(icon, messagebox.showinfo)
    shown(title or frame_selector_title(), text, parent=master)


def ask(text: str, *, master: Any = None, title: Optional[str] = None) -> bool:
    """A Yes/No question with Yes the default; True when Yes is chosen (FrameSelector.js 4900-4904)."""
    from tkinter import messagebox

    return messagebox.askyesno(title or frame_selector_title(), text, parent=master, default=messagebox.YES)
