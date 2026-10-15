"""Frame Selector filmstrip (loomsiril.frames_filmstrip): the slot and paging arithmetic, the tile layout, the loader's
queue, ticks, cache and release, and a window smoke test.

The pure tests and the loader tests need no display: the loader is driven by a fake timer and a fake host. The window
tests create widgets under a hidden root and skip when tkinter is not importable or there is no display.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Optional

import numpy as np
import pytest

from loomsiril import frames_filmstrip as fs
from loomsiril.frames import APPROVED, REJECTED, UNMEASURABLE
from loomsiril.frames_files import thumbnail_order
from loomsiril.frames_plot import display_value


# ---- pure: slots, paging and the hit test

def test_tile_slot_and_visible_count() -> None:
    assert fs.tile_w() == 128
    assert fs.visible_count(1000) == 7
    assert fs.visible_count(127) == 1
    assert fs.visible_count(50) == 1                 # at least one tile, however narrow
    assert fs.slot_w(1000) == pytest.approx(1000 / 7)  # the whole width is shared out
    assert fs.slot_w(50) == 128.0                    # never narrower than a tile


def test_paging_moves_by_whole_pages_and_stops_at_the_ends() -> None:
    assert fs.page_first(0, 1, 1000, 30) == 7
    assert fs.page_first(21, 1, 1000, 30) == 23      # the last page ends at the last tile
    assert fs.page_first(23, 1, 1000, 30) == 23
    assert fs.page_first(3, -1, 1000, 30) == 0
    assert fs.page_first(0, -1, 1000, 30) == 0
    assert fs.page_first(2, 1, 1000, 5) == 0         # fewer tiles than fit: no paging


def test_index_at_hits_only_drawn_tiles() -> None:
    slot = fs.slot_w(1000)
    assert fs.index_at(0, 1000, 2, 20) == 2
    assert fs.index_at(150, 1000, 2, 20) == 3        # second slot
    assert fs.index_at(999, 1000, 2, 20) == 8        # the seventh slot
    assert fs.index_at(7 * slot, 1000, 2, 20) == -1  # past the last visible slot
    assert fs.index_at(-1, 1000, 2, 20) == -1
    assert fs.index_at(600, 1000, 0, 4) == -1        # slot 4 is past the four frames
    assert fs.index_at(600, 1000, 0, 5) == 4


def test_thumbnail_ppm_is_binary_p6_and_repeats_grey() -> None:
    grey = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)
    data = fs.thumbnail_ppm(grey)
    assert data.startswith(b"P6\n3 2\n255\n")
    payload = data[len(b"P6\n3 2\n255\n"):]
    assert payload == bytes([1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 5, 6, 6, 6])


def test_thumbnail_ppm_keeps_rgb_and_rejects_other_shapes() -> None:
    rgb = np.zeros((1, 2, 3), dtype=np.uint8)
    rgb[0, 0] = (10, 20, 30)
    payload = fs.thumbnail_ppm(rgb)[len(b"P6\n2 1\n255\n"):]
    assert payload == bytes([10, 20, 30, 0, 0, 0])
    with pytest.raises(ValueError):
        fs.thumbnail_ppm(np.zeros((4,), dtype=np.uint8))


# ---- pure: what each visible tile shows

ROWS = [
    {"path": "a.fit", "metrics": {"fwhm": 2.5}},
    {"path": "b.fit", "metrics": None},
    {"path": "c.fit", "metrics": {"fwhm": 3.0}},
    {"path": "d.fit", "metrics": {}},
]


def _layout(**over: Any) -> list[fs.Tile]:
    args: dict[str, Any] = dict(rows=ROWS, thumbs=[None] * 4, failed=[False] * 4, flags=[[], [], [], []],
                                crossed=[False] * 4, selected_index=-1, metric="fwhm", first=0, visible=4,
                                slot=130.0)
    args.update(over)
    return fs.tile_layout(**args)


def test_an_unread_tile_is_grey_with_its_metric_or_a_dash() -> None:
    tiles = _layout()
    assert [t.state for t in tiles] == ["unread"] * 4
    assert all(t.picture is None for t in tiles)
    assert tiles[0].metric_text == display_value("fwhm", 2.5) == "2.50"
    assert tiles[1].metric_text == "-"               # no metrics at all
    assert tiles[3].metric_text == "-"               # metrics without this metric


def test_a_failed_tile_is_marked_and_a_picture_is_centred() -> None:
    thumbs = [np.zeros((80, 120), dtype=np.uint8), None, None, np.zeros((40, 60), dtype=np.uint8)]
    tiles = _layout(thumbs=thumbs, failed=[False, False, True, False])
    assert tiles[0].state == "picture"
    assert tiles[0].box == (5, 6, 125, 86)           # round((130 - 120) / 2) = 5 in from the slot
    assert tiles[0].picture == (5, 6, 120, 80)
    assert tiles[1].state == "unread"
    assert tiles[2].state == "failed"
    assert tiles[3].state == "picture"
    assert tiles[3].box[0] == 395                    # round(3 * 130 + 5)
    assert tiles[3].picture == (425, 26, 60, 40)     # centred both ways in the box


def test_the_selected_tile_and_the_cross_are_marked() -> None:
    tiles = _layout(selected_index=1, crossed=[False, False, True, False])
    assert [t.selected for t in tiles] == [False, True, False, False]
    assert [t.crossed for t in tiles] == [False, False, True, False]


def test_flag_letters_sit_in_their_boxes_under_the_picture_corner() -> None:
    tiles = _layout(flags=[["focus", "cloud"], [], ["dropped"], []])
    assert [letter for letter, _box in tiles[0].flags] == ["F", "C"]
    assert tiles[0].flags[0][1] == (8, 9, 24, 25)     # x + 3, y + 3, 16 pixels square
    assert tiles[0].flags[1][1] == (26, 9, 42, 25)    # the next box 18 pixels on
    assert [letter for letter, _box in tiles[2].flags] == ["D"]
    assert tiles[1].flags == ()


def test_the_metric_text_band_is_the_bottom_sixteen_pixels() -> None:
    tiles = _layout()
    assert tiles[0].metric_box == (5, 70, 125, 86)


def test_only_the_visible_window_is_laid_out() -> None:
    tiles = _layout(first=1, visible=2)
    assert [t.index for t in tiles] == [1, 2]
    assert tiles[0].box[0] == 5                      # the first visible slot, not the first frame


def test_a_short_channel_draws_only_what_exists() -> None:
    tiles = _layout(first=0, visible=10)
    assert [t.index for t in tiles] == [0, 1, 2, 3]


# ---- the loader: a fake host, a fake timer and a fake strip

class FakeTimer:
    """Records the ticks the loader asks for; ``fire`` runs the oldest one."""

    def __init__(self) -> None:
        self.pending: list[tuple[int, Any, Any]] = []
        self.cancelled: list[Any] = []
        self._next = 0

    def schedule(self, ms: int, fn: Any) -> int:
        self._next += 1
        self.pending.append((ms, fn, self._next))
        return self._next

    def cancel(self, handle: Any) -> None:
        self.cancelled.append(handle)
        self.pending = [p for p in self.pending if p[2] != handle]

    def fire(self) -> Optional[int]:
        if not self.pending:
            return None
        ms, fn, _handle = self.pending.pop(0)
        fn()
        return ms


class FakeStrip:
    def __init__(self, first: int = 0, visible: int = 3) -> None:
        self.first = first
        self._visible = visible
        self.thumbs: list[Any] = []
        self.failed: list[bool] = []
        self.set_rows_calls: list[tuple[Any, ...]] = []
        self.updates = 0

    def visible_count(self) -> int:
        return self._visible

    def set_rows(self, rows: Any, flags: Any, crossed: Any, selected: int) -> None:
        self.set_rows_calls.append((rows, flags, crossed, selected))

    def update(self) -> None:
        self.updates += 1


class FakeHost:
    def __init__(self, channels: list[dict[str, Any]], shown: int = 0, selected: int = -1) -> None:
        self._channels = channels
        self.shown = shown
        self.selected = selected
        self.copying = False
        self.visible = True
        self.reads: list[str] = []
        self.unreadable: set[str] = set()

    def channel(self) -> Optional[dict[str, Any]]:
        return self._channels[self.shown] if self._channels else None

    def channels(self) -> list[dict[str, Any]]:
        return list(self._channels)

    def channel_for_key(self, key: str) -> Optional[dict[str, Any]]:
        return next((c for c in self._channels if c["key"] == key), None)

    def channel_stretch(self, ch: Optional[dict[str, Any]]) -> Any:
        if ch is None:
            return None
        if "stretch" not in ch:
            ch["stretch"] = "stretch-" + ch["key"]
        return ch["stretch"]

    def thumbnail_of(self, path: str, stretch: Any) -> Optional[np.ndarray]:
        self.reads.append(path)
        if path in self.unreadable:
            return None
        return np.full((4, 6), len(self.reads) % 250, dtype=np.uint8)

    def selected_row_index(self) -> int:
        return self.selected

    def copying_out(self) -> bool:
        return self.copying

    def is_visible(self) -> bool:
        return self.visible


def _channel(key: str, paths: list[str], enabled: bool = True, states: Optional[list[str]] = None) -> dict[str, Any]:
    rows = []
    for i, p in enumerate(paths):
        rows.append({"path": p, "metrics": None, "state": (states[i] if states else UNMEASURABLE),
                     "override": None})
    return {"key": key, "rows": rows, "flags": [], "settings": {"enabled": enabled}}


def _loader(host: FakeHost, strip: Optional[FakeStrip] = None) -> tuple[fs.ThumbLoader, FakeTimer, FakeStrip]:
    timer = FakeTimer()
    strip = strip if strip is not None else FakeStrip()
    loader = fs.ThumbLoader(strip, host, schedule=timer.schedule, cancel=timer.cancel)  # type: ignore[arg-type]
    return loader, timer, strip


def _paths(n: int, prefix: str) -> list[str]:
    return [f"{prefix}{i}.fit" for i in range(n)]


def test_the_queue_has_the_stretch_then_the_selected_frame_then_the_rest() -> None:
    a = _channel("A", _paths(10, "a"))
    b = _channel("B", _paths(3, "b"))
    host = FakeHost([a, b], shown=0, selected=4)
    loader, _timer, _strip = _loader(host, FakeStrip(first=3, visible=3))
    loader.shown = True
    loader.rebuild_queue()
    queue = loader.queue
    assert queue[0] == {"kind": "stretch", "key": "A", "generation": 1}
    assert queue[1]["path"] == "a4.fit"              # the selected frame first
    order = thumbnail_order(10, 4, 3, 3)
    assert [q["path"] for q in queue[1:11]] == [f"a{i}.fit" for i in order]
    # the other channel comes after the whole of the channel on screen, marked background
    background = [q for q in queue if q.get("background")]
    assert queue.index(background[0]) > 10
    assert all(q["key"] == "B" for q in background)
    assert queue[-1]["path"] == "b2.fit"


def test_the_queue_has_no_stretch_for_a_channel_that_has_one() -> None:
    a = _channel("A", _paths(2, "a"))
    a["stretch"] = None                              # measured, and there is none: still not undefined
    host = FakeHost([a])
    loader, _timer, _strip = _loader(host)
    loader.shown = True
    loader.rebuild_queue()
    assert all(q.get("kind") != "stretch" for q in loader.queue)


def test_the_loader_ticks_fast_for_the_channel_on_screen_and_slow_for_the_rest() -> None:
    a = _channel("A", _paths(1, "a"))
    b = _channel("B", _paths(1, "b"))
    host = FakeHost([a, b])
    loader, timer, _strip = _loader(host)
    loader.on_show()
    assert timer.pending == []                       # nothing queued yet, nothing ticks
    loader.rebuild_queue()
    assert timer.pending[-1][0] == 50
    timer.fire()                                     # A's stretch
    timer.fire()                                     # A's thumbnail
    assert timer.pending and timer.pending[-1][0] == 250   # now the background channel


def test_loading_fills_the_thumbnails_and_the_strip_and_stops_when_done() -> None:
    a = _channel("A", _paths(3, "a"))
    host = FakeHost([a], selected=0)
    loader, timer, strip = _loader(host, FakeStrip(first=0, visible=3))
    loader.on_show()
    loader.rebuild_queue()
    while timer.fire() is not None:
        pass
    assert set(loader.thumbs) == {"a0.fit", "a1.fit", "a2.fit"}
    assert len(strip.thumbs) == 3 and all(t is not None for t in strip.thumbs)
    assert timer.pending == []                       # nothing left to tick for


def test_a_frame_that_cannot_be_read_is_marked_and_not_retried() -> None:
    a = _channel("A", _paths(2, "a"))
    host = FakeHost([a])
    host.unreadable.add("a0.fit")
    loader, timer, strip = _loader(host)
    loader.on_show()
    loader.rebuild_queue()
    while timer.fire() is not None:
        pass
    assert loader.failed == {"a0.fit": True}
    assert strip.failed == [True, False]
    reads = list(host.reads)
    loader.rebuild_queue()
    while timer.fire() is not None:
        pass
    assert "a0.fit" not in host.reads[len(reads):]


def test_a_stale_result_is_dropped_by_its_generation() -> None:
    a = _channel("A", _paths(1, "a"))
    host = FakeHost([a])
    loader, _timer, _strip = _loader(host)
    loader.shown = True
    loader.rebuild_queue()
    loader.queue = [{"path": "a0.fit", "key": "A", "generation": loader.generation - 1}]
    loader.loader_tick()
    assert "a0.fit" not in loader.thumbs


def test_a_failure_while_loading_empties_the_queue_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    a = _channel("A", _paths(2, "a"))
    host = FakeHost([a])

    def boom(path: str, stretch: Any) -> None:
        raise RuntimeError("disk gone")

    host.thumbnail_of = boom  # type: ignore[method-assign]
    loader, timer, _strip = _loader(host)
    loader.on_show()
    loader.rebuild_queue()
    with caplog.at_level(logging.WARNING, logger="loomsiril.frames_filmstrip"):
        timer.fire()                                 # the stretch: fine
        timer.fire()                                 # the first thumbnail: raises
    assert loader.queue == []
    assert timer.pending == []                       # nothing re-armed
    assert "thumbnail loading stopped" in caplog.text


def test_the_thumbnail_cache_drops_the_least_recently_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs, "THUMB_CAP", 2)
    a = _channel("A", ["p1", "p2", "p3"])
    host = FakeHost([a])
    loader, _timer, strip = _loader(host)
    arr = np.zeros((2, 2), dtype=np.uint8)
    loader.store_thumb("p1", arr)
    loader.store_thumb("p2", arr)
    host._channels[0]["rows"] = [{"path": "p1", "metrics": None, "state": UNMEASURABLE, "override": None}]
    loader.push_thumbs()                              # p1 is shown again: it is now the newest
    loader.store_thumb("p3", arr)
    assert set(loader.thumbs) == {"p1", "p3"}


def test_sync_strip_crosses_what_run_leaves_out() -> None:
    a = _channel("A", ["a0", "a1", "a2"], states=[APPROVED, REJECTED, UNMEASURABLE])
    host = FakeHost([a], selected=2)
    loader, _timer, strip = _loader(host)
    loader.sync_strip()
    rows, flags, crossed, selected = strip.set_rows_calls[-1]
    assert crossed == [False, True, False]           # the rejected frame, and nothing else
    assert selected == 2
    assert flags == []
    host.copying = True
    loader.sync_strip()
    assert strip.set_rows_calls[-1][2] == [False, True, False]  # copying leaves the same ones out here
    a["settings"]["enabled"] = False                  # a switched-off channel
    loader.sync_strip()
    assert strip.set_rows_calls[-1][2] == [True, True, True]    # copying: all of it goes
    host.copying = False
    loader.sync_strip()
    assert strip.set_rows_calls[-1][2] == [False, False, False]  # culling: nothing of a switched-off channel


def test_sync_strip_shows_the_thumbnails_of_the_channel_on_screen() -> None:
    a = _channel("A", ["a0", "a1"])
    b = _channel("B", ["b0", "b1"])
    host = FakeHost([a, b], shown=0, selected=0)
    loader, _timer, strip = _loader(host)
    loader.store_thumb("a0", np.zeros((2, 2), dtype=np.uint8))
    loader.store_thumb("b0", np.ones((3, 3), dtype=np.uint8))
    loader.sync_strip()
    assert strip.thumbs[0] is loader.thumbs["a0"]["arr"]
    assert strip.thumbs[1] is None
    host.shown = 1                                   # the user switches to channel B
    loader.sync_strip()
    assert len(strip.thumbs) == 2
    assert strip.thumbs[0] is loader.thumbs["b0"]["arr"]   # B's picture, not A's
    assert strip.thumbs[1] is None
    assert strip.failed == [False, False]
    assert strip.updates >= 2


def test_release_stops_the_loader_for_good() -> None:
    a = _channel("A", _paths(3, "a"))
    host = FakeHost([a])
    loader, timer, _strip = _loader(host)
    loader.on_show()
    loader.rebuild_queue()
    assert timer.pending                              # a tick is waiting
    loader.release()
    assert timer.cancelled and timer.pending == []    # it is cancelled
    reads = len(host.reads)
    loader.loader_tick()                              # a stray call does nothing
    loader.arm_loader()
    assert len(host.reads) == reads and timer.pending == []


def test_nothing_loads_while_the_dialog_is_not_on_screen() -> None:
    a = _channel("A", _paths(2, "a"))
    host = FakeHost([a])
    host.visible = False
    loader, timer, _strip = _loader(host)
    loader.on_show()
    loader.rebuild_queue()
    assert timer.pending == []
    loader.loader_tick()
    assert host.reads == []


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


def test_the_strip_is_a_themed_frame_holding_a_canvas(tk_root) -> None:
    import tkinter as tk
    from tkinter import ttk

    strip = fs.Filmstrip(tk_root)
    assert isinstance(strip.frame, ttk.Frame)
    assert isinstance(strip.canvas, tk.Canvas)
    strip.release()


def test_painting_draws_the_tiles_that_are_visible(tk_root) -> None:
    strip = fs.Filmstrip(tk_root)
    strip.frame.pack(fill="both", expand=True)
    tk_root.deiconify()                              # a withdrawn root is never laid out
    tk_root.geometry("600x120")
    tk_root.update()
    rows = [{"path": f"f{i}.fit", "metrics": {"fwhm": 2.0 + i}} for i in range(3)]
    strip.set_rows(rows, [[], ["focus"], []], [False, False, True], 1)
    strip.thumbs = [np.zeros((40, 60), dtype=np.uint8), None, None]
    strip.failed = [False, False, True]
    strip.update()
    kinds = [strip.canvas.type(item) for item in strip.canvas.find_all()]
    assert kinds.count("image") == 1                 # the one picture
    texts = [strip.canvas.itemcget(item, "text") for item in strip.canvas.find_all()
             if strip.canvas.type(item) == "text"]
    assert "!" in texts and "F" in texts and {"2.00", "3.00", "4.00"} <= set(texts)
    strip.release()


def test_a_click_picks_the_tile_under_it(tk_root) -> None:
    strip = fs.Filmstrip(tk_root)
    strip.frame.pack(fill="both", expand=True)
    tk_root.deiconify()                              # a withdrawn root is never laid out
    tk_root.geometry("600x120")
    tk_root.update()
    picked: list[int] = []
    strip.on_pick = picked.append
    strip.set_rows([{"path": "a", "metrics": None}, {"path": "b", "metrics": None}], [], [], -1)
    strip._on_press(SimpleNamespace(x=10))
    strip._on_press(SimpleNamespace(x=-5))
    assert picked == [0]
    strip.release()
    strip._on_press(SimpleNamespace(x=10))           # released: no pick reaches the dialog
    assert picked == [0]
