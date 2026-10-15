"""Frame Selector filmstrip: the row of thumbnail tiles, and the loader that fills it (tkinter, imported when a window
is made, never at import).

Ported from ``pixinsight/script/FrameSelector.js``. The line numbers are that file's, and each JS function has a Python
twin with the same meaning, named in snake_case:

* ``FrameSelector.Filmstrip`` (4565-4697) -> :class:`Filmstrip`, with the constructor's click handler (4582-4587)
  -> :meth:`Filmstrip._on_press`. Its members:

  - ``tileW`` (4590) -> :func:`tile_w`, the width of one tile slot in pixels.
  - ``visibleCount`` (4591) -> :func:`visible_count`.
  - ``slotW`` (4594) -> :func:`slot_w`: the tiles share the whole width, so no gap is left at the right.
  - ``setMetric`` (4596-4600) -> :meth:`Filmstrip.set_metric`.
  - ``setRows`` (4602-4610) -> :meth:`Filmstrip.set_rows`; the view moves through
    :func:`loomsiril.frames_files.strip_first`.
  - ``page`` (4612-4618) -> :meth:`Filmstrip.page` and :func:`page_first`.
  - ``indexAt`` (4620-4623) -> :meth:`Filmstrip.index_at` and :func:`index_at`, through
    :func:`loomsiril.frames_files.tile_at`.
  - ``paintTile`` (4625-4668) -> :func:`tile_layout` (what each tile shows, pure) and
    :meth:`Filmstrip._paint_tile` (the drawing).
  - ``paint`` (4669-4684) -> :meth:`Filmstrip.paint`.
  - ``release`` (4686-4695) -> :meth:`Filmstrip.release`.

* The thumbnail loader members of ``FrameSelector.Dialog``, kept here as :class:`ThumbLoader`. The dialog provides the
  channel state through :class:`LoaderHost`:

  - ``armLoader`` (2830-2844) -> :meth:`ThumbLoader.arm_loader`; the timer ticks every 50 ms for the channel on screen
    and every 250 ms for the background channels (:data:`FOREGROUND_TICK_MS`, :data:`BACKGROUND_TICK_MS`).
  - ``mayLoad`` (2847-2851) -> :meth:`ThumbLoader.may_load`.
  - ``rebuildQueue`` (2853-2875) -> :meth:`ThumbLoader.rebuild_queue`; the order of the thumbnails is
    :func:`loomsiril.frames_files.thumbnail_order`.
  - ``queueOtherChannels`` (2882-2900) -> :meth:`ThumbLoader.queue_other_channels`.
  - ``loaderTick`` (2902-2944) -> :meth:`ThumbLoader.loader_tick`. The stretch of a channel is measured by the host.
  - ``storeThumb`` (2949-2965) -> :meth:`ThumbLoader.store_thumb`; the cap is ``THUMB_CAP``, the least recently shown
    thumbnail goes first.
  - ``pushThumbs`` (2967-2985) -> :meth:`ThumbLoader.push_thumbs`.
  - ``syncStrip`` (2987-2996) -> :meth:`ThumbLoader.sync_strip`, with :func:`loomsiril.frames.left_out` for the crosses.

* ``FrameSelector.THUMB`` (1462), ``THUMB_CAP`` (1463) and ``TAG_COLOURS`` (1454-1455) come from
  :mod:`loomsiril.frames_ui`, as does ``FrameSelector.REJECT_COLOUR`` (4233). The colours written inline in
  ``paintTile`` and ``paint`` are named below (:data:`STRIP_BACKGROUND` and the rest).

Everything the tile layout needs is pure: :func:`tile_layout` returns the boxes, states and texts of the visible tiles,
and the tests check them without a display. The widgets are a ttk.Frame holding a tk.Canvas: a canvas cannot take a ttk
style, so the strip keeps the JS dark ground as its own colour, while the frame and the other widgets of the window take
Siril's theme.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

import numpy as np

from loomsiril.frames import left_out
from loomsiril.frames_files import strip_first, thumbnail_order, tile_at
from loomsiril.frames_flags import FLAG_BADGE
from loomsiril.frames_plot import display_value
from loomsiril.frames_ui import REJECT_COLOUR, TAG_COLOURS, THUMB, THUMB_CAP, tk_colour
from loomsiril.jsmath import js_round

log = logging.getLogger(__name__)

#: The gap added to a thumbnail's width for one tile slot (FrameSelector.js 4590).
_TILE_PAD = 8
#: The top of a tile in the strip (FrameSelector.js paint, ``y = 6``), and the strip's height beyond the tile (4576).
TILE_Y = 6
STRIP_EXTRA_HEIGHT = 12
#: The tile's inks, written inline in FrameSelector.js paintTile (4625-4668) and paint (4669-4684).
STRIP_BACKGROUND = 0xff1a1a1a
EMPTY_TILE = 0xff3a3a3a
FAILED_INK = 0xffe0e0e0
METRIC_INK = 0xffe8e8e8
SELECTED_COLOUR = 0xffe0b020
#: The metric text sits in the bottom 16 pixels of the tile (paintTile).
_METRIC_BAND = 16
#: A flag badge is a 16 pixel box, 3 pixels in, every 18 pixels across (paintTile).
_FLAG_INSET = 3
_FLAG_BOX = 16
_FLAG_STEP = 18
#: The tile loader's ticks in milliseconds (armLoader 2830-2844: 0.25 s and 0.05 s).
FOREGROUND_TICK_MS = 50
BACKGROUND_TICK_MS = 250

Rect = tuple[int, int, int, int]
Channel = dict[str, Any]


# ---- pure: the slots, the paging and the hit test (Filmstrip members 4590-4623) ------------------------------------

def tile_w() -> int:
    """One tile's slot width: the thumbnail's width and the gap (FrameSelector.js 4590)."""
    return THUMB["W"] + _TILE_PAD


def visible_count(width: float) -> int:
    """How many tiles fit across ``width`` pixels, at least one (FrameSelector.js 4581)."""
    return max(1, math.floor(width / tile_w()))


def slot_w(width: float) -> float:
    """The width each visible tile is given: the strip's width shared out, never narrower than a tile (4594)."""
    return max(float(tile_w()), width / visible_count(width))


def page_first(first: int, direction: int, width: float, count: int) -> int:
    """The first tile after paging by ``direction`` pages, kept within the channel (FrameSelector.js 4612-4618)."""
    v = visible_count(width)
    return max(0, min(first + direction * v, max(0, count - v)))


def index_at(x: float, width: float, first: int, count: int) -> int:
    """The frame under a click at ``x``, or -1 (FrameSelector.js 4620-4623)."""
    return tile_at(x, slot_w(width), visible_count(width), first, count)


# ---- pure: what each visible tile shows (Filmstrip.paintTile 4616-4647) -------------------------------------------

@dataclass(frozen=True)
class Tile:
    """One visible tile, in strip pixels. ``state`` is "picture" (a thumbnail is shown), "failed" (it could not be read:
    a "!" is drawn) or "unread" (grey, not read yet). ``picture`` is the thumbnail's (x, y, width, height), or None.
    ``flags`` are the badge letters with their boxes; ``metric_text`` is the number printed under the picture."""

    index: int
    box: Rect
    state: str
    picture: Optional[tuple[int, int, int, int]]
    crossed: bool
    selected: bool
    flags: tuple[tuple[str, Rect], ...]
    metric_box: Rect
    metric_text: str


def _size(arr: Any) -> tuple[int, int]:
    """(width, height) of a thumbnail array."""
    return int(arr.shape[1]), int(arr.shape[0])


def tile_layout(rows: list[dict[str, Any]], thumbs: list[Any], failed: list[bool], flags: list[list[str]],
                crossed: list[bool], selected_index: int, metric: Optional[str], first: int, visible: int,
                slot: float) -> list[Tile]:
    """The visible tiles from ``first`` on, each where FrameSelector.js paintTile would put it. ``thumbs`` holds uint8
    arrays or None; ``crossed`` marks what Run leaves out; ``flags`` are the anomaly keys of each row."""
    T = THUMB
    out: list[Tile] = []
    last = min(len(rows), first + visible)
    for i in range(first, last):
        x = js_round((i - first) * slot + (slot - T["W"]) / 2)
        y = TILE_Y
        box: Rect = (x, y, x + T["W"], y + T["H"])
        bmp = thumbs[i] if i < len(thumbs) else None
        picture = None
        if bmp is not None:
            w, h = _size(bmp)
            picture = (x + js_round((T["W"] - w) / 2), y + js_round((T["H"] - h) / 2), w, h)
            state = "picture"
        else:
            state = "failed" if (i < len(failed) and failed[i]) else "unread"
        keys = flags[i] if i < len(flags) and flags[i] else []
        badges = tuple(
            (FLAG_BADGE[k], (x + _FLAG_INSET + b * _FLAG_STEP, y + _FLAG_INSET,
                             x + _FLAG_INSET + _FLAG_BOX + b * _FLAG_STEP, y + _FLAG_INSET + _FLAG_BOX))
            for b, k in enumerate(keys))
        measured = rows[i].get("metrics")
        text = "-" if measured is None else display_value(metric, measured.get(metric))
        out.append(Tile(
            index=i, box=box, state=state, picture=picture,
            crossed=bool(i < len(crossed) and crossed[i]), selected=(i == selected_index),
            flags=badges, metric_box=(x, y + T["H"] - _METRIC_BAND, x + T["W"], y + T["H"]),
            metric_text=text))
    return out


def thumbnail_ppm(arr: Any) -> bytes:
    """A uint8 thumbnail (grey or RGB, rows of pixels) as binary PPM, the form Tk's photo image reads. A grey frame is
    repeated over three channels."""
    a = np.asarray(arr, dtype=np.uint8)
    if a.ndim == 2:
        a = np.repeat(a[:, :, None], 3, axis=2)
    elif a.ndim == 3 and a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
    elif a.ndim == 3 and a.shape[2] >= 3:
        a = a[:, :, :3]
    else:
        raise ValueError(f"not a thumbnail: shape {a.shape}")
    h, w = a.shape[0], a.shape[1]
    return b"P6\n%d %d\n255\n" % (w, h) + np.ascontiguousarray(a).tobytes()


# ---- the widget (Filmstrip 4565-4677) -----------------------------------------------------------------------------

class Filmstrip:
    """The strip of thumbnail tiles, under a ttk frame, with a canvas that paints them. ``on_pick(i)`` is called with
    the index of a clicked tile. Built with the rows empty; the owner fills it through :meth:`set_rows` and
    :attr:`thumbs`/:attr:`failed`, then calls :meth:`update`."""

    def __init__(self, master: Any, *, on_pick: Optional[Callable[[int], None]] = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.rows: list[dict[str, Any]] = []
        self.flags: list[list[str]] = []
        self.crossed: list[bool] = []
        self.thumbs: list[Any] = []
        self.failed: list[bool] = []
        self.selected = -1
        self.first = 0
        #: The measurement printed on each tile (JS default "fwhm").
        self.metric = "fwhm"
        self.on_pick = on_pick
        self._photos: dict[int, tuple[Any, Any]] = {}
        self._released = False

        self.frame = ttk.Frame(master)
        self.canvas = tk.Canvas(self.frame, height=THUMB["H"] + STRIP_EXTRA_HEIGHT, highlightthickness=0,
                                borderwidth=0, background=tk_colour(STRIP_BACKGROUND))
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<Configure>", lambda _event: self.update())

    def width(self) -> int:
        """The strip's width in pixels, as Tk lays it out (1 before the window is shown)."""
        return int(self.canvas.winfo_width())

    def tile_w(self) -> int:
        return tile_w()

    def visible_count(self) -> int:
        return visible_count(self.width())

    def slot_w(self) -> float:
        return slot_w(self.width())

    def set_metric(self, metric: str) -> None:
        self.metric = metric
        self.update()

    def set_rows(self, rows: list[dict[str, Any]], flags: Optional[list[list[str]]],
                 crossed: Optional[list[bool]], selected: int) -> None:
        """Rows, anomaly flags, crosses and the selected index. The view stays put while the selection is on screen."""
        self.rows = list(rows or [])
        self.flags = list(flags or [])
        self.crossed = list(crossed or [])
        self.selected = selected
        self.first = strip_first(len(self.rows), selected, self.visible_count(), self.first)
        self.update()

    def page(self, direction: int) -> None:
        self.first = page_first(self.first, direction, self.width(), len(self.rows))
        self.update()

    def index_at(self, x: float) -> int:
        return index_at(x, self.width(), self.first, len(self.rows))

    def _on_press(self, event: Any) -> None:
        i = self.index_at(event.x)
        if i >= 0 and self.on_pick is not None:
            self.on_pick(i)

    def update(self) -> None:
        """Repaints the strip (JS Control.update)."""
        if not self._released:
            self.paint()

    def paint(self) -> None:
        """Clears the canvas and draws every visible tile (FrameSelector.js 4669-4684). A strip that cannot paint must
        not stop the review, so a failed draw is logged and skipped."""
        width = self.width()
        try:
            self.canvas.delete("all")
            tiles = tile_layout(self.rows, self.thumbs, self.failed, self.flags, self.crossed, self.selected,
                                self.metric, self.first, visible_count(width), slot_w(width))
            live = {id(a) for a in self.thumbs if a is not None}
            for key in [k for k in self._photos if k not in live]:
                del self._photos[key]
            for tile in tiles:
                self._paint_tile(tile)
        except Exception as exc:  # noqa: BLE001 - a strip that cannot paint must not stop the review
            log.warning("filmstrip could not be painted: %s", exc)

    def _photo(self, arr: Any) -> Any:
        """The Tk image of a thumbnail, made once and kept while the thumbnail is in the strip."""
        import tkinter as tk

        key = id(arr)
        if key not in self._photos:
            self._photos[key] = (arr, tk.PhotoImage(master=self.canvas, data=thumbnail_ppm(arr), format="ppm"))
        return self._photos[key][1]

    def _paint_tile(self, tile: Tile) -> None:
        c = self.canvas
        x0, y0, x1, y1 = tile.box
        if tile.picture is not None:
            px, py, _w, _h = tile.picture
            c.create_image(px, py, image=self._photo(self.thumbs[tile.index]), anchor="nw")
        else:
            c.create_rectangle(x0, y0, x1, y1, fill=tk_colour(EMPTY_TILE), outline="")
            if tile.state == "failed":
                c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="!", fill=tk_colour(FAILED_INK))
        if tile.crossed:
            colour = tk_colour(REJECT_COLOUR)
            c.create_line(x0, y0, x1, y1, fill=colour, width=2)
            c.create_line(x1, y0, x0, y1, fill=colour, width=2)
        for letter, (bx0, by0, bx1, by1) in tile.flags:
            c.create_rectangle(bx0, by0, bx1, by1, fill=tk_colour(TAG_COLOURS["flag"]["fill"]), outline="")
            c.create_text((bx0 + bx1) / 2, (by0 + by1) / 2, text=letter,
                          fill=tk_colour(TAG_COLOURS["flag"]["ink"]))
        mx0, my0, mx1, my1 = tile.metric_box
        c.create_text((mx0 + mx1) / 2, (my0 + my1) / 2, text=tile.metric_text, fill=tk_colour(METRIC_INK))
        if tile.selected:
            c.create_rectangle(x0, y0, x1, y1, outline=tk_colour(SELECTED_COLOUR), width=3)

    def release(self) -> None:
        """Detached before teardown: no click or repaint reaches the dialog afterwards (FrameSelector.js 4686-4695)."""
        try:
            self._released = True
            self.on_pick = None
            self.canvas.unbind("<Button-1>")
            self.canvas.unbind("<Configure>")
            self.thumbs = []
            self.rows = []
            self._photos.clear()
        except Exception:  # noqa: BLE001 - teardown must not raise
            pass


# ---- the thumbnail loader (Dialog members 2830-2997) --------------------------------------------------------------

class LoaderHost(Protocol):
    """What the dialog provides to the loader. ``channel()`` is the channel on screen (or None); ``channels()`` lists
    every channel in display order; ``channel_for_key`` finds one by key; ``channel_stretch(ch)`` measures the channel's
    shared stretch once (JS channelStretch) and returns it (None when there is none); ``thumbnail_of(path, stretch)``
    returns a uint8 thumbnail or None (FrameSelector.js thumbnailOf); ``selected_row_index()`` is the selected row or
    -1; ``copying_out()`` is the run's mode; ``is_visible()`` is True while the dialog is on screen."""

    def channel(self) -> Optional[Channel]: ...
    def channels(self) -> list[Channel]: ...
    def channel_for_key(self, key: str) -> Optional[Channel]: ...
    def channel_stretch(self, ch: Optional[Channel]) -> Any: ...
    def thumbnail_of(self, path: str, stretch: Any) -> Optional[np.ndarray]: ...
    def selected_row_index(self) -> int: ...
    def copying_out(self) -> bool: ...
    def is_visible(self) -> bool: ...


class ThumbLoader:
    """Fills the strip one thumbnail per timer tick, while the dialog is on screen. ``schedule(ms, fn)`` runs ``fn``
    once after ``ms`` milliseconds and returns a handle; ``cancel(handle)`` drops it. Nothing runs before
    :meth:`on_show`, and :meth:`release` stops it for good."""

    def __init__(self, strip: Filmstrip, host: LoaderHost, *, schedule: Callable[[int, Callable[[], None]], Any],
                 cancel: Callable[[Any], None]) -> None:
        self.strip = strip
        self.host = host
        self._schedule = schedule
        self._cancel = cancel
        self.queue: list[dict[str, Any]] = []
        self.generation = 0
        #: Path -> {"arr": thumbnail, "used": counter}; the counter is refreshed whenever a thumbnail is shown.
        self.thumbs: dict[str, dict[str, Any]] = {}
        self.failed: dict[str, bool] = {}
        self.thumb_use = 0
        self.ticks_observed = 0
        self.shown = False
        self.released = False
        self._handle: Any = None

    def on_show(self) -> None:
        self.shown = True
        self.arm_loader()

    def arm_loader(self) -> None:
        """Starts the timer for the next queued item, unless it is already running or there is nothing to do."""
        if not self.may_load() or not self.queue or self._handle is not None:
            return
        background = bool(self.queue[0].get("background"))
        self._handle = self._schedule(BACKGROUND_TICK_MS if background else FOREGROUND_TICK_MS, self.loader_tick)

    def may_load(self) -> bool:
        """Loading happens only while the dialog is on screen and not released (JS mayLoad 2847-2851)."""
        return not self.released and self.shown and bool(self.host.is_visible())

    def rebuild_queue(self) -> None:
        """Rebuilt on selection, page and channel change; stale results are dropped by their generation."""
        if self.released:
            return
        ch = self.host.channel()
        self.generation += 1
        self.queue = []
        if ch is None:
            return
        if "stretch" not in ch:
            self.queue.append({"kind": "stretch", "key": ch["key"], "generation": self.generation})
        rows = ch["rows"]
        order = thumbnail_order(len(rows), self.host.selected_row_index(), self.strip.first,
                                self.strip.visible_count())
        for i in order:
            p = rows[i]["path"]
            if p not in self.thumbs and not self.failed.get(p):
                self.queue.append({"path": p, "key": ch["key"], "generation": self.generation})
        self.queue_other_channels(ch)
        self.push_thumbs()
        self.arm_loader()

    def queue_other_channels(self, shown: Channel) -> None:
        """After the channel on screen, every other channel's stretch and thumbnails, marked background."""
        for other in self.host.channels():
            if other is None or other is shown:
                continue
            if "stretch" not in other:
                self.queue.append({"kind": "stretch", "key": other["key"], "background": True,
                                   "generation": self.generation})
            for row in other["rows"]:
                p = row["path"]
                if p not in self.thumbs and not self.failed.get(p):
                    self.queue.append({"path": p, "key": other["key"], "background": True,
                                       "generation": self.generation})

    def loader_tick(self) -> None:
        """One queued item: a channel's stretch, or one thumbnail (JS loaderTick 2902-2944). Re-arms only while
        :meth:`may_load`; a failure empties the queue and is logged."""
        self._handle = None
        if not self.may_load():
            return
        try:
            self.ticks_observed += 1
            if not self.queue:
                return
            item = self.queue.pop(0)
            ch = self.host.channel_for_key(item["key"])
            if item.get("kind") == "stretch":
                self.host.channel_stretch(ch)
                return
            arr = self.host.thumbnail_of(item["path"], self.host.channel_stretch(ch))
            if self.released or item["generation"] != self.generation:
                return
            self.store_thumb(item["path"], arr)
            self.push_thumbs()
        except Exception as exc:  # noqa: BLE001 - loading stops, the review goes on
            log.warning("thumbnail loading stopped: %s", exc)
            self.queue = []
        finally:
            self.arm_loader()

    def store_thumb(self, path: str, arr: Optional[np.ndarray]) -> None:
        """Keeps a thumbnail, capped at THUMB_CAP: the least recently shown go first. A frame that could not be read is
        kept apart and never retried (JS storeThumb 2949-2965)."""
        if arr is None:
            self.failed[path] = True
            return
        self.thumb_use += 1
        self.thumbs[path] = {"arr": arr, "used": self.thumb_use}
        if len(self.thumbs) <= THUMB_CAP:
            return
        oldest = sorted(self.thumbs, key=lambda k: self.thumbs[k]["used"])
        for k in oldest[: len(self.thumbs) - THUMB_CAP]:
            del self.thumbs[k]

    def push_thumbs(self) -> None:
        """Hands the strip what exists for the channel on screen, and marks it as shown (JS pushThumbs 2967-2985)."""
        ch = self.host.channel()
        if ch is None or self.released:
            return
        arr: list[Any] = []
        failed: list[bool] = []
        for row in ch["rows"]:
            p = row["path"]
            t = self.thumbs.get(p)
            if t is not None:
                self.thumb_use += 1
                t["used"] = self.thumb_use
            arr.append(t["arr"] if t is not None else None)
            failed.append(bool(self.failed.get(p)))
        self.strip.thumbs = arr
        self.strip.failed = failed
        self.strip.update()

    def sync_strip(self) -> None:
        """Rows, crosses (what Run leaves out), flags and selection into the strip (JS syncStrip 2987-2996)."""
        ch = self.host.channel()
        if ch is None or self.released:
            return
        copying = self.host.copying_out()
        enabled = ch["settings"]["enabled"]
        crossed = [left_out(r, enabled, copying) for r in ch["rows"]]
        self.strip.set_rows(ch["rows"], ch.get("flags") or [], crossed, self.host.selected_row_index())

    def release(self) -> None:
        """Stops the loader for good: a pending tick is cancelled and nothing re-arms (the JS mayLoad check)."""
        self.released = True
        if self._handle is not None:
            try:
                self._cancel(self._handle)
            except Exception:  # noqa: BLE001 - a timer already gone is not an error
                pass
            self._handle = None
        self.queue = []
        self.thumbs = {}
        self.failed = {}
