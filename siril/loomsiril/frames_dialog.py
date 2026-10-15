"""Frame Selector: the review dialog, ported from ``pixinsight/script/FrameSelector.js`` 2292-3946
(``FrameSelector.Dialog``) as a ``tkinter.Toplevel`` in Siril's theme (tkinter is imported when a dialog is made, never
at import; no window appears on import).

The pure decisions live in :mod:`loomsiril.frames_state` and the thumbnail loader in :mod:`loomsiril.frames_filmstrip`;
this module is the window and the review. Each JS member has a Python twin, in snake_case, with its line range:

    constructor                  2294-2337 -> Dialog.__init__ (everything after the base is guarded, as the JS is:
                                              a half-built dialog is released and the error raised)
    buildChannelTree             2338-2366 -> Dialog.build_channel_tree, Dialog._on_channel_selected
    buildPlotPane                2367-2402 -> Dialog.build_plot_pane
    selectedRowIndex             2404-2411 -> Dialog.selected_row_index
    selectRow                    2423-2445 -> Dialog.select_row
    rowNode                      2446-2452 -> Dialog.row_iid (a row's id in the table, or None)
    loadPreview                  2454-2472 -> Dialog.load_preview (the pending hand-off: a frame whose channel has no
                                              stretch yet waits for the loader's stretch tick)
    showPreview                  2474-2480 -> Dialog.show_preview
    channelStretch               2482-2490 -> Dialog.channel_stretch (shows a waiting preview once measured)
    currentTags                  2492-2499 -> Dialog.current_tags (frames_state.current_tags)
    chooseDestination            2506-2515 -> Dialog.choose_destination
    plotMetric                   2517-2521 -> Dialog.plot_metric
    fillPlot                     2523-2555 -> Dialog.fill_plot
    buildFrameTable              2557-2590 -> Dialog.build_frame_table (Space on the table is toggleOverride)
    buildPreviewPane             2592-2618 -> Dialog.build_preview_pane (override and clear-overrides buttons)
    buildKnobPanel               2620-2742 -> Dialog.build_knob_panel (preset, k, act-on-channel, one row per criterion)
    applyEdit                    2744-2754 -> Dialog.apply_edit
    commitEdit                   2756-2760 -> Dialog.commit_edit
    commitPendingEdits           2767-2775 -> Dialog.commit_pending_edits
    buildFilmstrip               2781-2828 -> Dialog.build_filmstrip (the loader is frames_filmstrip.ThumbLoader; the
                                              dialog is its LoaderHost: channel, channels, channel_for_key,
                                              channel_stretch, thumbnail_of, selected_row_index, copying_out,
                                              is_visible; schedule and cancel are Tk's after and after_cancel)
    buildActionRow               3000-3067 -> Dialog.build_action_row (the buttons; Run calls commit)
    editable                     3072-3074 -> Dialog.editable (frames_state.editable)
    channel                      3069-3071 -> Dialog.channel
    toggleOverride               3083-3103 -> Dialog.toggle_override
    fillChannels                 3105-3119 -> Dialog.fill_channels
    fillFrames                   3121-3140 -> Dialog.fill_frames
    fillRow                      3142-3165 -> Dialog.fill_row (the red cross is frames_ui.mark_icon; the measurement
                                              that rejected the frame is painted red over its cell)
    flagLine                     3167-3177 -> frames_state.flag_line
    syncCriteria                 3182-3196 -> Dialog.sync_criteria
    syncKnobs                    3198-3235 -> Dialog.sync_knobs
    committableRows              3237-3251 -> frames_state.committable_rows
    refresh                      3255-3284 -> Dialog.refresh
    recomputeAll                 3286-3303 -> Dialog.recompute_all (frames_state.recompute_all)
    summaryText                  3306-3315 -> Dialog.summary_text (frames_state.summary_text)
    syncActions                  3322-3333 -> Dialog.sync_actions
    destinationText              3337-3350 -> Dialog.destination_text (frames_state.destination_text, as plain text)
    release                      3380-3392 -> Dialog.release
    stopLoader                   3394-3409 -> Dialog.stop_loader
    detachHandlers               3411-3427 -> Dialog.detach_handlers
    copyingOut                   3433-3435 -> Dialog.copying_out (frames_state.copying_out, remembered per destination)
    layOutCriteria               3829-3863 -> Dialog.lay_out_criteria
    layOut                       3865-3946 -> Dialog.lay_out

Run, Copy, Import and the master-flat controls belong to ``frames_dialog_actions``: when that module provides
``DialogActions`` the dialog uses it as its base; otherwise ``commit``, ``commit_copy``, ``commit_import`` and
``set_master_flat_settings`` raise ``NotImplementedError`` and the two master-flat hooks (``build_master_flat_controls``,
``sync_master_flat_controls``) do nothing. The mixin is the base class, so a method the dialog defines itself overrides
the mixin's of the same name (the mixin must not reuse a review method's name); the master-flat controls are built into
``self.master_flat_frame``, the empty row the dialog lays out above the buttons. Only a missing ``frames_dialog_actions``
is tolerated: any other import error propagates.

Differences from the JavaScript, all deliberate:

* Tk has no per-cell colour in a tree, so the measurement that rejected a frame is painted red by a small label laid
  over its cell (``Dialog.failing_cells`` lists them).
* A channel switch selects the channel's first frame, as opening the dialog does, so the preview, the tags and the
  strip never show the previous channel's frame.
* The criteria's boxes and ticks are disabled while the review is not editable (locked or past the review phase);
  the JS leaves them enabled and ignores the change. The band sentence under the plot is italic, as the JS's is.
* The destination sentence is plain text, in red and bold while the run will delete in place (the JS bolds "deleted").
* Rich text (the destination sentence) is shown as plain text.
* The options of the last run (frames_options) are applied to every channel when the dialog opens and written back
  when it closes.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Callable, Optional

from loomsiril import frames as F
from loomsiril import frames_files
from loomsiril import frames_options as FO
from loomsiril import frames_state as FS
from loomsiril.frames_filmstrip import Filmstrip, ThumbLoader
from loomsiril.frames_flags import row_tooltip
from loomsiril.frames_plot import display_value
from loomsiril.frames_plot_widget import FramesPlot
from loomsiril.frames_preview import PreviewControl
from loomsiril.frames_render import reference_stretch, thumbnail_of
from loomsiril.frames_ui import (AUTO_FOREGROUND, REJECT_COLOUR, frame_selector_title, mark_icon, tell, tk_colour)

try:  # the run and import actions live in their own module; the review does not need them
    from loomsiril.frames_dialog_actions import DialogActions as _Actions
except ModuleNotFoundError as _missing:
    if _missing.name != "loomsiril.frames_dialog_actions":
        raise  # the module is there but one of its own imports failed: that is a real error

    class _Actions:  # type: ignore[no-redef]
        """What the actions module provides, until it does: the run and the master-flat controls."""

        def commit(self) -> Any:
            raise NotImplementedError("Dialog.commit is provided by frames_dialog_actions")

        def commit_copy(self) -> Any:
            raise NotImplementedError("Dialog.commit_copy is provided by frames_dialog_actions")

        def commit_import(self) -> Any:
            raise NotImplementedError("Dialog.commit_import is provided by frames_dialog_actions")

        def set_master_flat_settings(self, folder: Any, only: Any) -> None:
            raise NotImplementedError("Dialog.set_master_flat_settings is provided by frames_dialog_actions")

        def build_master_flat_controls(self) -> None:
            """Where the master-flat row is built (FrameSelector.js 3470-3560); nothing without the actions."""

        def sync_master_flat_controls(self) -> None:
            """Where the master-flat row is brought up to date (3585-3604); nothing without the actions."""

log = logging.getLogger(__name__)

__all__ = ["Dialog", "plain_text"]

#: The preview and the table's smallest sizes, and the strip's page of tiles are the widgets' own.
CHANNEL_WIDTH = 200
TABLE_HEIGHT = 12
SUMMARY_WRAP = 900
K_RANGE = (0.5, 10.0)

_TAG_RE = re.compile(r"<[^>]*>")


def plain_text(rich: str) -> str:
    """The dialog's rich text (``<i>``, ``<b>``, ``&mdash;``) as the plain text a Tk label shows."""
    return _TAG_RE.sub("", rich).replace("&mdash;", "—").replace("&ge;", "≥").replace("&le;", "≤")


def _band_text(band: dict[str, Any]) -> str:
    """The accepted band in words; an open end reads as "no limit" rather than as a number (fillPlot 2546-2554)."""
    lo, hi = band.get("lo"), band.get("hi")
    text = "keeps " + ("anything" if lo is None else "≥ " + F.round_display(lo))
    if hi is not None:
        text += ("≤ " if lo is None else " and ≤ ") + F.round_display(hi)
    return text


class Dialog(_Actions):
    """The review window. ``master`` is the Tk root; ``state`` is a :mod:`loomsiril.frames_state` state; ``cache`` is the
    preview cache. ``siril`` is passed to the frame readers. ``options_store`` supplies the remembered options (applied
    to every channel) and receives them again on close; ``options`` stands in for the stored ones when given.
    ``schedule(ms, fn)`` and ``cancel(handle)`` drive the loader (default: the window's ``after`` and ``after_cancel``).
    """

    def __init__(self, master: Any, state: dict[str, Any], *, cache: Any, siril: Any = None,
                 options_store: Any = None, options: Optional[FO.FrameSelectorOptions] = None,
                 schedule: Optional[Callable[[int, Callable[[], None]], Any]] = None,
                 cancel: Optional[Callable[[Any], None]] = None, sha: str = "") -> None:
        import tkinter as tk

        self.released = False
        self.top = tk.Toplevel(master)
        self.top.withdraw()
        self.top.title(frame_selector_title(sha=sha))
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        try:
            # Everything after the window exists is guarded: a half-built dialog must not keep a timer or a handler.
            # The JS fills in what a caller left out (2300-2309).
            if state.get("unstable") is None:
                state["unstable"] = []
            if state.get("phase") is None:
                state["phase"] = F.PHASE["REVIEW"]
            if state.get("channels") is None:
                state["channels"] = {}
            if state.get("order") is None:
                state["order"] = []
            self.state = state
            self.cache = cache
            self.siril = siril
            self.options_store = options_store
            self.options = options if options is not None else (FO.load(options_store) if options_store else None)
            self.current: Optional[str] = state["order"][0] if state["order"] else None
            self.pending_preview: Optional[dict[str, Any]] = None
            self._schedule = schedule if schedule is not None else self.top.after
            self._cancel = cancel if cancel is not None else self.top.after_cancel
            self._selected_row = -1
            self._copy_memo: dict[Any, bool] = {}
            self._icons: dict[str, Any] = {}
            self._typed: set[str] = set()
            self._programmatic = False
            self._marks: list[Any] = []
            self._marks_job: Any = None
            self.failing_cells: list[tuple[int, str]] = []
            self._tip: Any = None
            self._tip_job: Any = None
            self._tip_row: Optional[str] = None
            self._apply_options()

            self.build_channel_tree()
            self.build_frame_table()
            self.build_preview_pane()
            self.build_knob_panel()
            self.build_plot_pane()
            self.build_filmstrip()
            self.build_action_row()
            self.lay_out()
        except BaseException:
            try:
                self.release()
                self.top.destroy()
            except Exception:  # noqa: BLE001 - the first error is the one to raise
                pass
            raise

    # ---- starting and stopping

    def _apply_options(self) -> None:
        """New channels start from the remembered settings (frames_options.settings_for_new_channel)."""
        if self.options is None:
            return
        for key in self.state["order"]:
            self.state["channels"][key]["settings"] = FO.settings_for_new_channel(self.options)
        if self.state.get("darksFolder") in (None, "") and self.options.darks_folder:
            self.state["darksFolder"] = self.options.darks_folder
        if self.options.only_master_flats:
            self.state["onlyMasterFlats"] = True

    def show(self) -> None:
        """Puts the window up and starts the loader (the JS onShow, 2825)."""
        self.top.deiconify()
        self.top.update_idletasks()
        self.loader.on_show()

    def run(self) -> None:
        """Shows the window and returns when it is closed."""
        self.show()
        self.top.wait_window()

    def is_visible(self) -> bool:
        """Is the window on screen? (LoaderHost; false once destroyed.)"""
        try:
            return bool(self.top.winfo_viewable())
        except Exception:  # noqa: BLE001 - a destroyed window is not visible
            return False

    def save_options(self) -> None:
        """Remembers this run's settings for the next one (frames_options.remember_from_state, save)."""
        if self.options is None or self.options_store is None:
            return
        ch = self.channel()
        settings = ch["settings"] if ch is not None else F.default_settings()
        updated = FO.remember_from_state(
            self.options, settings, source_folder=self.state.get("folder") or "",
            destination_folder=self.state.get("destination") or "",
            darks_folder=self.state.get("darksFolder") or "",
            only_master_flats=bool(self.state.get("onlyMasterFlats")))
        FO.save(self.options_store, updated)
        self.options = updated

    def close(self) -> None:
        """Close button and the window's close box: the options are saved, the handlers let go, the window destroyed."""
        if self.released:
            return
        try:
            self.save_options()
        except Exception as exc:  # noqa: BLE001 - closing must not fail on a file that cannot be written
            log.warning("options were not saved: %s", exc)
        self.release()
        try:
            self.top.destroy()
        except Exception:  # noqa: BLE001
            pass

    def release(self) -> None:
        """Lets go of every control's handlers before the window is destroyed; safe to call twice (3380-3392). The
        loader goes first: a tick that ran after this would read into a window being torn down."""
        if self.released:
            return
        self.released = True
        try:
            self.stop_loader()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.detach_handlers()
        except Exception:  # noqa: BLE001
            pass

    def stop_loader(self) -> None:
        """The thumbnail loader: timer stopped, work dropped, strip let go (3394-3409)."""
        loader = getattr(self, "loader", None)
        if loader is not None:
            loader.release()
        if self._marks_job is not None:
            try:
                self.top.after_cancel(self._marks_job)
            except Exception:  # noqa: BLE001
                pass
            self._marks_job = None
        if getattr(self, "filmstrip", None) is not None:
            self.filmstrip.release()

    def detach_handlers(self) -> None:
        """Every other control's handlers, and the preview's and plot's own (3411-3427)."""
        if getattr(self, "preview", None) is not None:
            self.preview.release()
        if getattr(self, "plot", None) is not None:
            self.plot.release()
        self._hide_tip()
        for widget, sequences in ((getattr(self, "frame_tree", None), ("<<TreeviewSelect>>", "<space>")),
                                  (getattr(self, "channel_tree", None), ("<<TreeviewSelect>>",))):
            if widget is None:
                continue
            for sequence in sequences:
                try:
                    widget.unbind(sequence)
                except Exception:  # noqa: BLE001
                    pass
        for entry in getattr(self, "crit_edits", {}).values():
            for sequence in ("<Return>", "<FocusOut>"):
                try:
                    entry.unbind(sequence)
                except Exception:  # noqa: BLE001
                    pass
        for entry in (getattr(self, "k_entry", None),):
            if entry is not None:
                for sequence in ("<Return>", "<FocusOut>"):
                    try:
                        entry.unbind(sequence)
                    except Exception:  # noqa: BLE001
                        pass

    # ---- the state, as the loader and the table ask for it (LoaderHost)

    def channel(self) -> Optional[dict[str, Any]]:
        """The channel on screen, or None (3069-3071)."""
        return FS.channel_of(self.state, self.current)

    def channels(self) -> list[dict[str, Any]]:
        """Every channel, in display order."""
        return [self.state["channels"][k] for k in self.state["order"]]

    def channel_for_key(self, key: str) -> Optional[dict[str, Any]]:
        return self.state["channels"].get(key)

    def editable(self) -> bool:
        """False once a run has started and past the review phase (3072-3074)."""
        return FS.editable(self.state)

    def copying_out(self) -> bool:
        """True when Run writes copies rather than deleting (3433-3435). The destination is resolved once per value:
        every refresh asks, and resolving every frame's folder each time would cost a disk walk."""
        dest = self.state["destination"]
        if dest is None:
            return False
        if dest not in self._copy_memo:
            self._copy_memo[dest] = FS.copying_out(self.state)
        return self._copy_memo[dest]

    def selected_row_index(self) -> int:
        """Which row the table has selected, or -1 (2404-2411)."""
        ch = self.channel()
        if ch is None or not 0 <= self._selected_row < len(ch["rows"]):
            return -1
        return self._selected_row

    def channel_stretch(self, ch: Optional[dict[str, Any]]) -> Any:
        """The channel's shared stretch, measured once; None when it has none (2482-2490). When this measures the
        stretch of the channel on screen, the preview that was waiting for it is shown now (loaderTick 2914-2919)."""
        if ch is None:
            return None
        if "stretch" not in ch:
            ch["stretch"] = reference_stretch(ch["rows"], self.cache, self.siril)
            self._show_pending_preview(ch)
        return ch["stretch"]

    def _show_pending_preview(self, ch: dict[str, Any]) -> None:
        pp = self.pending_preview
        if pp is None or self.released or pp["key"] != ch["key"] or ch["key"] != self.current:
            return
        i = self.selected_row_index()
        if i < 0 or ch["rows"][i]["path"] != pp["path"]:
            return
        self.pending_preview = None
        self.show_preview(pp["path"])

    def thumbnail_of(self, path: str, stretch: Any) -> Any:
        return thumbnail_of(path, stretch, self.cache, self.siril)

    # ---- the channel list

    def build_channel_tree(self) -> None:
        """The channel list: one row per filter, kept and total (2338-2366)."""
        from tkinter import ttk

        self.main = ttk.Frame(self.top, padding=8)
        self.main.pack(fill="both", expand=True)
        self.channel_frame = ttk.Frame(self.main)
        self.channel_tree = ttk.Treeview(self.channel_frame, columns=(), selectmode="browse", height=TABLE_HEIGHT)
        self.channel_tree.heading("#0", text="Channel")
        self.channel_tree.column("#0", width=CHANNEL_WIDTH, minwidth=CHANNEL_WIDTH, stretch=True)
        self.channel_tree.pack(fill="both", expand=True)
        self.channel_tree.bind("<<TreeviewSelect>>", self._on_channel_selected)

    def _on_channel_selected(self, _event: Any = None) -> None:
        sel = self.channel_tree.selection()
        if self.released or not sel or sel[0] == self.current or sel[0] not in self.state["channels"]:
            return
        self.current = sel[0]
        self._selected_row = -1
        self.pending_preview = None
        self.refresh()
        if not self.select_row(0):
            self.preview.dispose()
            self.preview.set_tags([])
            self.loader.rebuild_queue()

    def fill_channels(self) -> None:
        """One row per filter, with what is kept; a switched-off channel says so (3105-3119)."""
        tree = self.channel_tree
        tree.delete(*tree.get_children())
        for key in self.state["order"]:
            ch = self.state["channels"][key]
            text = F.summary_line(key, F.counts(ch["rows"])) + ("" if ch["settings"]["enabled"] else "  [off]")
            tree.insert("", "end", iid=key, text=text)
        if self.current is not None and tree.exists(self.current):
            tree.selection_set(self.current)

    # ---- the table

    def table_columns(self) -> list[str]:
        """The table's columns after the name: the shown measurements, then the score (no SNR column)."""
        return list(FS.SHOWN_METRICS) + ["score"]

    def build_frame_table(self) -> None:
        """The frame table for the selected channel (2557-2590)."""
        from tkinter import ttk

        self.table_frame = ttk.Frame(self.main)
        self.table_frame.columnconfigure(0, weight=1)
        self.table_frame.rowconfigure(0, weight=1)
        columns = self.table_columns()
        self.frame_tree = ttk.Treeview(self.table_frame, columns=columns, selectmode="browse", height=TABLE_HEIGHT)
        self.frame_tree.heading("#0", text="Frame")
        self.frame_tree.column("#0", width=260, minwidth=120, stretch=True)
        for name in columns:
            self.frame_tree.heading(name, text=F.METRIC_HEADING.get(name, name))
            self.frame_tree.column(name, width=70, minwidth=50, anchor="e", stretch=False)
        self.table_bar = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.frame_tree.yview)
        self.frame_tree.configure(yscrollcommand=self._table_scrolled)
        self.frame_tree.grid(row=0, column=0, sticky="nsew")
        self.table_bar.grid(row=0, column=1, sticky="ns")
        self.frame_tree.bind("<<TreeviewSelect>>", self._on_row_selected)
        # Space toggles the override on the selected row; "break" consumes it, so the tree does not also act on it.
        self.frame_tree.bind("<space>", self._on_space)
        # The red cells are laid over the tree, so anything that moves a column or a row repaints them.
        for sequence in ("<Configure>", "<ButtonRelease-1>", "<B1-Motion>", "<MouseWheel>", "<Button-4>", "<Button-5>",
                         "<KeyRelease>"):
            self.frame_tree.bind(sequence, lambda _e: self._schedule_marks(), add="+")
        self.frame_tree.bind("<Motion>", self._on_row_motion)
        self.frame_tree.bind("<Leave>", lambda _e: self._hide_tip())

    def _table_scrolled(self, first: str, last: str) -> None:
        self.table_bar.set(first, last)
        self._schedule_marks()

    def _on_space(self, _event: Any = None) -> str:
        self.toggle_override()
        return "break"

    def _on_row_selected(self, _event: Any = None) -> None:
        """A click or an arrow key in the table; the one path is select_row (2575-2589)."""
        if self.released:
            return
        sel = self.frame_tree.selection()
        if not sel:
            self._selected_row = -1
            self.plot.set_selected(-1)
            return
        index = int(sel[0])
        if index != self._selected_row:
            self.select_row(index)
        self._schedule_marks()

    def row_iid(self, index: int) -> Optional[str]:
        """The table's id for a row, or None when there is no such row (2446-2452)."""
        ch = self.channel()
        if ch is None or index < 0 or index >= len(ch["rows"]):
            return None
        return str(index)

    def fill_frames(self) -> None:
        """The table for the channel on screen; names are shortened against the whole channel (3121-3140)."""
        tree = self.frame_tree
        tree.delete(*tree.get_children())
        self.failing_cells = []
        self._selected_row = -1
        ch = self.channel()
        if ch is None:
            self._schedule_marks()
            return
        short = frames_files.short_names([r["path"] for r in ch["rows"]])
        flags = ch.get("flags")
        for i, row in enumerate(ch["rows"]):
            self.fill_row(i, row, short[i], flags[i] if flags else [])
        self._schedule_marks()

    def row_texts(self, row: dict[str, Any]) -> list[str]:
        """A row's cells after the name: each shown measurement, "-" where there is none, then the score (3148-3153)."""
        metrics = row.get("metrics")
        out = [display_value(m, metrics.get(m)) if metrics is not None else "" for m in FS.SHOWN_METRICS]
        out.append("-" if row.get("score") is None else F.round_display(row["score"]))
        return out

    def fill_row(self, index: int, row: dict[str, Any], short_name: str, flags: list[str]) -> None:
        """One frame's row: name, measurements, score, and the cross when Run leaves it out (3142-3165)."""
        rejected = F.final_state(row["state"], row.get("override")) == F.REJECTED
        icon = mark_icon(self.top, rejected, self._icons)
        kwargs: dict[str, Any] = {"image": icon} if icon is not None else {}
        self.frame_tree.insert("", "end", iid=str(index), text=short_name, values=self.row_texts(row), **kwargs)
        if rejected:
            for metric in row.get("failing") or []:
                if metric in FS.SHOWN_METRICS:
                    self.failing_cells.append((index, metric))

    def row_tooltip_text(self, index: int) -> str:
        """The tip of a row: its full path, the verdict and why, then what it looks like (3157-3158)."""
        ch = self.channel()
        if ch is None or not 0 <= index < len(ch["rows"]):
            return ""
        flags = ch.get("flags")
        return row_tooltip(ch["rows"][index], flags[index] if flags else [])

    # -- the rejecting measurement in red (a tree cannot colour one cell)

    def _schedule_marks(self) -> None:
        if self.released or self._marks_job is not None:
            return
        try:
            self._marks_job = self.top.after_idle(self._paint_marks)
        except Exception:  # noqa: BLE001
            self._marks_job = None

    def _paint_marks(self) -> None:
        from tkinter import ttk

        self._marks_job = None
        for label in self._marks:
            try:
                label.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._marks = []
        if self.released:
            return
        import tkinter as tk

        style = ttk.Style()
        columns = self.table_columns()
        try:
            selected = self.frame_tree.selection()
            for index, metric in self.failing_cells:
                iid = str(index)
                box = self.frame_tree.bbox(iid, "#%d" % (columns.index(metric) + 1))
                if not box:
                    continue
                state = ["selected"] if iid in selected else []
                background = style.lookup("Treeview", "background", state) or "white"
                if state:
                    background = style.lookup("Treeview", "background", state) or background
                text = self.frame_tree.set(iid, metric)
                label = tk.Label(self.frame_tree, text=text, foreground=tk_colour(REJECT_COLOUR),
                                 background=background, anchor="e", borderwidth=0, padx=4)
                label.bind("<Button-1>", lambda _e, i=index: self._on_mark_click(i))
                label.bind("<MouseWheel>", self._forward_wheel)
                label.bind("<Button-4>", self._forward_wheel)
                label.bind("<Button-5>", self._forward_wheel)
                label.bind("<Motion>", lambda e, i=index: self._tip_at(str(i), e))
                label.bind("<Leave>", lambda _e: self._hide_tip())
                label.place(x=box[0], y=box[1], width=box[2], height=box[3])
                self._marks.append(label)
        except Exception as exc:  # noqa: BLE001 - a mark that cannot be drawn must not cost the review its rows
            log.debug("rejecting measurements were not marked: %s", exc)

    def _on_mark_click(self, index: int) -> None:
        """A click on a red cell is a click on its row, and the tree takes the keyboard (Space works next)."""
        self.select_row(index)
        self.frame_tree.focus_set()

    def _forward_wheel(self, event: Any) -> str:
        """The wheel over a red cell scrolls the table, as it does anywhere else over it."""
        delta = getattr(event, "delta", 0)
        units = -1 if (event.num == 4 or delta > 0) else 1
        self.frame_tree.yview_scroll(units * 3, "units")
        self._schedule_marks()
        return "break"

    # -- the row's tip

    def _on_row_motion(self, event: Any) -> None:
        self._tip_at(self.frame_tree.identify_row(event.y), event)

    def _tip_at(self, iid: str, event: Any) -> None:
        if iid == self._tip_row:
            return
        self._hide_tip()
        if not iid:
            return
        self._tip_row = iid
        x, y = event.x_root, event.y_root
        self._tip_job = self.top.after(500, lambda: self._show_tip(iid, x, y))

    def _show_tip(self, iid: str, x: int, y: int) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tip_job = None
        text = self.row_tooltip_text(int(iid))
        if not text or self.released:
            return
        try:
            self._tip = tk.Toplevel(self.top)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry("+%d+%d" % (x + 12, y + 16))
            ttk.Label(self._tip, text=text, justify="left", padding=(6, 3), background="#ffffe0",
                      foreground="#222222", relief="solid", borderwidth=1).pack()
        except Exception:  # noqa: BLE001 - no tip rather than an error
            self._tip = None

    def _hide_tip(self) -> None:
        self._tip_row = None
        if self._tip_job is not None:
            try:
                self.top.after_cancel(self._tip_job)
            except Exception:  # noqa: BLE001
                pass
            self._tip_job = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._tip = None

    # ---- selecting a frame and the preview

    def select_row(self, index: int, reload: bool = True) -> bool:
        """Moves the table's selection to a row as though it had been clicked (2423-2445). ``reload`` is False when
        only the table was rebuilt and the same frame is still selected: re-reading a large frame to show what is
        already on screen would make every knob cost a disk read. False when there is no such row."""
        iid = self.row_iid(index)
        if iid is None:
            return False
        ch = self.channel()
        assert ch is not None
        self._selected_row = index
        self.frame_tree.selection_set(iid)
        self.frame_tree.focus(iid)
        self.frame_tree.see(iid)
        if reload:
            self.load_preview(ch["rows"][index]["path"])
        self.plot.set_selected(index)
        self.preview.set_tags(self.current_tags())
        self.loader.sync_strip()
        if reload:
            self.loader.rebuild_queue()
        self._schedule_marks()
        return True

    def load_preview(self, path: str) -> None:
        """Loads a frame into the preview (2454-2472). The channel's shared stretch costs a frame read: while the window
        is on screen that happens on the loader's timer, ahead of the thumbnails, and the preview follows it, so the
        window stays responsive."""
        ch = self.channel()
        if ch is not None and "stretch" not in ch and self.loader.may_load():
            self.pending_preview = {"path": path, "key": ch["key"]}
            self.loader.rebuild_queue()
            return
        self.pending_preview = None
        self.show_preview(path)

    def show_preview(self, path: str) -> None:
        """Shows a frame, and keeps its thumbnail when the strip has none (2474-2480)."""
        stretch = self.channel_stretch(self.channel())
        if self.preview.load(path, stretch, self.cache, self.siril) and path not in self.loader.thumbs:
            self.loader.store_thumb(path, self.preview.thumbnail())

    def current_tags(self) -> list[dict[str, str]]:
        """The selected frame's tags: what Run does with it, then each anomaly (2492-2499)."""
        tags = FS.current_tags(self.state, self.current, self.selected_row_index())
        return tags

    def toggle_override(self) -> None:
        """Space and the button: keep a dropped frame or drop a kept one, or take the hand decision back (3083-3103). An
        unmeasurable frame may be condemned by hand but never approved (finalState enforces that)."""
        ch = self.channel()
        if ch is None or not self.editable():
            return
        i = self.selected_row_index()
        if i < 0:
            return
        row = ch["rows"][i]
        if row.get("override") is not None:
            row["override"] = None
        else:
            row["override"] = F.OVERRIDE["RESCUED"] if row["state"] == F.REJECTED else F.OVERRIDE["CONDEMNED"]
        self.refresh()

    def clear_overrides(self) -> None:
        """Every hand decision of this channel taken back (the button of buildPreviewPane, 2606-2617)."""
        ch = self.channel()
        if ch is None or not self.editable():
            return
        for row in ch["rows"]:
            row["override"] = None
        self.refresh()

    def build_preview_pane(self) -> None:
        """The 1:1 preview and the override buttons under it (2592-2618)."""
        import tkinter as tk
        from tkinter import ttk

        self.preview_frame = ttk.Frame(self.main)
        self.preview = PreviewControl(self.preview_frame)
        self.preview.outer.pack(fill="both", expand=True)
        row = ttk.Frame(self.preview_frame)
        row.pack(fill="x", pady=(6, 0))
        self.override_button = ttk.Button(row, text="Keep / drop this one", command=self.toggle_override)
        self.override_button.pack(side="left")
        self.clear_overrides_button = ttk.Button(row, text="Clear overrides", command=self.clear_overrides)
        self.clear_overrides_button.pack(side="left", padx=(6, 0))
        self.fit_var = tk.BooleanVar(master=self.top, value=bool(self.preview.view.fit))
        self.fit_check = ttk.Checkbutton(row, text="Whole frame", variable=self.fit_var, command=self._on_fit)
        self.fit_check.pack(side="left", padx=(12, 0))

    def _on_fit(self) -> None:
        self.preview.set_fit(self.fit_var.get())

    # ---- the plot

    def build_plot_pane(self) -> None:
        """The measurement plot, with the metric it shows chosen by a combo (2367-2402)."""
        import tkinter as tk
        from tkinter import ttk

        self.plot_frame = ttk.Frame(self.main)
        head = ttk.Frame(self.plot_frame)
        head.pack(fill="x")
        ttk.Label(head, text="Plot:").pack(side="left")
        self.plot_var = tk.StringVar(master=self.top, value=F.METRIC_LABEL[FS.SHOWN_METRICS[0]])
        self.plot_combo = ttk.Combobox(head, textvariable=self.plot_var, state="readonly", width=14,
                                       values=[F.METRIC_LABEL[m] for m in FS.SHOWN_METRICS])
        self.plot_combo.current(0)
        self.plot_combo.pack(side="left", padx=6)
        self.plot_combo.bind("<<ComboboxSelected>>", lambda _e: self.fill_plot())
        self.band_var = tk.StringVar(master=self.top, value="")
        import tkinter.font as tkfont

        self._band_font = tkfont.nametofont("TkDefaultFont").copy()
        self._band_font.configure(slant="italic")
        ttk.Label(head, textvariable=self.band_var, font=self._band_font).pack(side="left", padx=(8, 0))
        self.plot = FramesPlot(self.plot_frame)
        self.plot.on_pick = lambda index: self.select_row(index)
        self.plot.pack(fill="both", expand=True, pady=(4, 0))

    def plot_metric(self) -> str:
        """The metric the combo names (2517-2521)."""
        i = self.plot_combo.current() if getattr(self, "plot_combo", None) is not None else 0
        return FS.SHOWN_METRICS[i if 0 <= i < len(FS.SHOWN_METRICS) else 0]

    def fill_plot(self) -> None:
        """The plot for the channel on screen, with the accepted band drawn and said in words (2523-2555)."""
        if getattr(self, "plot", None) is None:
            return
        ch = self.channel()
        metric = self.plot_metric()
        if ch is None:
            self.plot.set_series([], metric, None)
            self.band_var.set("")
            return
        band = F.accepted_band(metric, ch.get("gates"), ch["settings"])
        self.plot.set_series(ch["rows"], metric, band)
        self.plot.set_selected(self.selected_row_index())
        self.band_var.set(_band_text(band))

    # ---- the approval criteria

    def build_knob_panel(self) -> None:
        """The Approval criteria panel: preset, k and the per-channel switch on the first row, one criterion per metric
        on the second (2620-2742)."""
        import tkinter as tk
        import tkinter.font as tkfont
        from tkinter import ttk

        from loomsiril.fly_tooltip import Tooltip

        self.criteria_group = ttk.LabelFrame(self.main, text="Approval criteria", padding=6)
        box = self.criteria_group
        self._italic = tkfont.nametofont("TkDefaultFont").copy()
        self._italic.configure(slant="italic")

        self.preset_var = tk.StringVar(master=self.top, value=F.DEFAULT_PRESET)
        self.preset_combo = ttk.Combobox(box, textvariable=self.preset_var, state="readonly", width=10,
                                         values=list(F.PRESET_NAMES))
        self.preset_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_preset())
        Tooltip(self.preset_combo, "How hard this channel is cut. Each channel has its own: a night's L and its Ha "
                                   "are not the same population.")

        self.k_var = tk.StringVar(master=self.top, value="")
        self.k_entry = ttk.Entry(box, textvariable=self.k_var, width=6, justify="right")
        self.k_entry.bind("<Return>", lambda _e: self._on_k())
        self.k_entry.bind("<FocusOut>", lambda _e: self._on_k())
        Tooltip(self.k_entry, "How many normalised MADs from the channel's median a frame may stray (0.5 to 10).")

        self.enabled_var = tk.BooleanVar(master=self.top, value=True)
        self.enabled_check = ttk.Checkbutton(box, text="Act on this channel", variable=self.enabled_var,
                                             command=self._on_enabled)
        self.keep_var = tk.StringVar(master=self.top, value="")
        self.keep_label = ttk.Label(box, textvariable=self.keep_var, anchor="e")
        Tooltip(self.keep_label, "Frames of this channel that Run keeps: left in place when culling, copied when the "
                                 "output is another folder.")

        self.crit_checks: dict[str, Any] = {}
        self.crit_edits: dict[str, Any] = {}
        self.crit_vars: dict[str, Any] = {}
        self.crit_on: dict[str, Any] = {}
        for metric in F.CRITERIA_ORDER:
            on = tk.BooleanVar(master=self.top, value=bool(F.DEFAULT_GATING.get(metric)))
            check = ttk.Checkbutton(box, text=F.METRIC_HEADING[metric] + "  " + F.OPERATOR[metric], variable=on,
                                    command=lambda m=metric: self._on_gating(m))
            Tooltip(check, ("Off by default. Integration weights each frame by its signal, so a faint frame already "
                            "counts for less: deleting it throws away signal that was being discounted correctly. "
                            "Turn it on to catch a frame far below the rest, which usually means cloud rather than "
                            "a dim night.") if metric == "psfSNR" else
                    ("Reject frames on " + F.METRIC_LABEL[metric] + ". No weighting repairs this one, so a bad frame "
                     "degrades the stack however little it is weighted."))
            var = tk.StringVar(master=self.top, value="")
            var.trace_add("write", lambda *_a, m=metric: self._on_typed(m))
            entry = ttk.Entry(box, textvariable=var, width=9, justify="right")
            entry.bind("<Return>", lambda _e, m=metric: self.commit_edit(m))
            entry.bind("<FocusOut>", lambda _e, m=metric: self.commit_edit(m))
            Tooltip(entry, "Grey: automatic: the limit k times this night's spread gives. Type a number to set this "
                           "metric's limit yourself; clear the box to go back to automatic.")
            self.crit_checks[metric], self.crit_edits[metric] = check, entry
            self.crit_vars[metric], self.crit_on[metric] = var, on

        self.problems_var = tk.StringVar(master=self.top, value="")
        self.problems_label = ttk.Label(self.main, textvariable=self.problems_var, wraplength=SUMMARY_WRAP,
                                        justify="left")
        self.summary_var = tk.StringVar(master=self.top, value="")
        self.summary_label = ttk.Label(self.main, textvariable=self.summary_var, wraplength=SUMMARY_WRAP,
                                       justify="left")

    def _on_preset(self) -> None:
        """A preset sets THIS channel only, and k unless k was edited by hand (2630-2652)."""
        ch = self.channel()
        if ch is None:
            return
        if not self.editable():
            self.sync_knobs()
            return
        ch["settings"] = F.apply_preset(ch["settings"], self.preset_var.get())
        self.refresh()

    def _on_k(self) -> None:
        """k, confirmed on Return or focus out; an edited k survives a preset change (2654-2668)."""
        if self.released:
            return
        ch = self.channel()
        if ch is None:
            return
        try:
            value = float(self.k_var.get().strip())
        except ValueError:
            self.sync_knobs()
            return
        if not math.isfinite(value):          # "nan" and "inf" parse as floats but are no k
            self.sync_knobs()
            return
        value = round(min(max(value, K_RANGE[0]), K_RANGE[1]), 2)
        if not self.editable() or value == round(ch["settings"]["k"], 2):
            self.sync_knobs()
            return
        ch["settings"]["k"] = value
        ch["settings"]["kEdited"] = True
        self.refresh()

    def _on_enabled(self) -> None:
        """Disabling suppresses ALL action on the channel, and re-enabling restores it unchanged (2670-2686)."""
        ch = self.channel()
        if ch is None:
            return
        if not self.editable():
            self.sync_knobs()
            return
        ch["settings"]["enabled"] = bool(self.enabled_var.get())
        self.refresh()

    def _on_gating(self, metric: str) -> None:
        """The box says whether the metric may reject at all (2706-2712)."""
        ch = self.channel()
        if ch is None:
            return
        if not self.editable():
            self.sync_knobs()
            return
        ch["settings"]["gating"][metric] = bool(self.crit_on[metric].get())
        self.refresh()

    def _on_typed(self, metric: str) -> None:
        """Typing marks the field dirty; the program's own assignment does not (Edit.modified)."""
        if not self._programmatic:
            self._typed.add(metric)

    def apply_edit(self, metric: str) -> bool:
        """Applies one field to the settings, only if the user typed in it, and without refreshing. Returns whether it
        was dirty (2744-2754). A number sets this metric's limit; a blank box goes back to automatic; an invalid entry
        is redrawn from the settings."""
        ch = self.channel()
        if ch is None or metric not in self._typed or not self.editable():
            return False
        v = F.parse_limit(self.crit_vars[metric].get())
        if v is not F.UNDEFINED:
            ch["settings"] = F.with_limit(ch["settings"], metric, v)
        return True

    def commit_edit(self, metric: str) -> None:
        """A limit confirmed on Return or focus out (2756-2760)."""
        if self.released:
            return
        if self.apply_edit(metric):
            self.refresh()
        else:
            self._typed.discard(metric)

    def commit_pending_edits(self) -> None:
        """Every dirty field first, one refresh after: refreshing between them would rewrite every field's text and
        drop every edit after the first (2767-2775). Run calls this."""
        any_ = False
        for metric in F.CRITERIA_ORDER:
            any_ = self.apply_edit(metric) or any_
        if any_:
            self.refresh()

    def sync_criteria(self, ch: dict[str, Any]) -> None:
        """The criteria row for one channel. Grey and italic while automatic, plain once a number is typed; an unticked
        metric's box is disabled (3182-3196)."""
        editable = self.editable()
        self._programmatic = True
        try:
            for metric in F.CRITERIA_ORDER:
                on = bool(ch["settings"]["gating"].get(metric))
                self.crit_on[metric].set(on)
                shown = F.display_limit(metric, ch.get("gates"), ch["settings"])
                entry = self.crit_edits[metric]
                self.crit_vars[metric].set(F.format_limit(metric, shown["value"]))
                if shown["auto"]:
                    entry.configure(foreground=AUTO_FOREGROUND, font=self._italic)
                else:
                    entry.configure(foreground="", font="TkTextFont")
                entry.state(["!disabled"] if on and editable else ["disabled"])
                self.crit_checks[metric].state(["!disabled"] if editable else ["disabled"])
                self._typed.discard(metric)
        finally:
            self._programmatic = False
        kc = F.keep_count(ch["rows"], ch["settings"]["enabled"], self.copying_out())
        self.keep_var.set("%d / %d keep" % (kc["keep"], kc["total"]))

    def sync_knobs(self) -> None:
        """The knobs for the channel on screen (3198-3235)."""
        ch = self.channel()
        if ch is None:
            self.problems_var.set("")
            return
        self.k_var.set("%.2f" % ch["settings"]["k"])
        self.enabled_var.set(bool(ch["settings"]["enabled"]))
        if ch["settings"]["preset"] in F.PRESET_NAMES:
            self.preset_var.set(ch["settings"]["preset"])
        editable = self.editable()
        self.preset_combo.state(["!disabled"] if editable else ["disabled"])
        self.k_entry.state(["!disabled"] if editable else ["disabled"])
        self.enabled_check.state(["!disabled"] if editable else ["disabled"])
        self.override_button.state(["!disabled"] if editable else ["disabled"])
        self.clear_overrides_button.state(["!disabled"] if editable else ["disabled"])
        self.sync_criteria(ch)
        if ch["problems"]:
            self.problems_var.set("Not comparable: " + ", ".join(ch["problems"]) + ". The figures for this channel "
                                  "compare frames that are not alike; look before running.")
        elif ch["settings"].get("kEdited"):
            self.problems_var.set("k has been set by hand for this channel; a preset will not change it.")
        else:
            self.problems_var.set("")

    # ---- the filmstrip

    def build_filmstrip(self) -> None:
        """The filmstrip and the loader that fills it: one frame per tick, so events get through between frames
        (2781-2828). The loader is not started here: it starts when the window is shown."""
        import tkinter as tk
        from tkinter import ttk

        self.strip_frame = ttk.Frame(self.main)
        self.strip_frame.columnconfigure(2, weight=1)
        self.filmstrip = Filmstrip(self.strip_frame, on_pick=lambda i: self.select_row(i))
        self.loader = ThumbLoader(self.filmstrip, self, schedule=self._schedule, cancel=self._cancel)
        self.strip_var = tk.StringVar(master=self.top, value=F.METRIC_HEADING["fwhm"])
        self.strip_metric = ttk.Combobox(self.strip_frame, textvariable=self.strip_var, state="readonly", width=8,
                                         values=[F.METRIC_HEADING[m] for m in FS.SHOWN_METRICS])
        self.strip_metric.bind("<<ComboboxSelected>>", lambda _e: self._on_strip_metric())
        self.strip_prev = ttk.Button(self.strip_frame, text="‹", width=2, command=lambda: self._page(-1))
        self.strip_next = ttk.Button(self.strip_frame, text="›", width=2, command=lambda: self._page(1))
        self.strip_metric.grid(row=0, column=0, padx=(0, 4))
        self.strip_prev.grid(row=0, column=1)
        self.filmstrip.frame.grid(row=0, column=2, sticky="ew", padx=4)
        self.strip_next.grid(row=0, column=3)

    def _on_strip_metric(self) -> None:
        i = self.strip_metric.current()
        if 0 <= i < len(FS.SHOWN_METRICS):
            self.filmstrip.set_metric(FS.SHOWN_METRICS[i])

    def _page(self, direction: int) -> None:
        self.filmstrip.page(direction)
        self.loader.rebuild_queue()

    # ---- the buttons

    def build_action_row(self) -> None:
        """Where the frames go, and Run and Close (3000-3067). The buttons call methods the actions provide."""
        import tkinter as tk
        from tkinter import ttk

        from loomsiril.fly_tooltip import Tooltip

        self.dest_frame = ttk.Frame(self.main)
        ttk.Label(self.dest_frame, text="output:").pack(side="left")
        self.dest_button = ttk.Button(self.dest_frame, text="Folder...", command=self._on_destination)
        self.dest_button.pack(side="left", padx=6)
        Tooltip(self.dest_button, "Leave it on the folder the frames came from to cull them where they are. Point it "
                                  "anywhere else and that folder is emptied first (everything in it, hidden files and "
                                  "subfolders included), then the kept frames are copied there; every original is "
                                  "left alone.")
        self.dest_var = tk.StringVar(master=self.top, value="")
        import tkinter.font as tkfont

        self._bold = tkfont.nametofont("TkDefaultFont").copy()
        self._bold.configure(weight="bold")
        self.dest_label = ttk.Label(self.dest_frame, textvariable=self.dest_var, wraplength=SUMMARY_WRAP)
        self.dest_label.pack(side="left")
        self.master_flat_frame = ttk.Frame(self.main)
        self.build_master_flat_controls()
        self.button_row = ttk.Frame(self.main)
        self.close_button = ttk.Button(self.button_row, text="Close", command=self.close)
        self.close_button.pack(side="right")
        self.run_button = ttk.Button(self.button_row, text="Run", command=self._on_run)
        self.run_button.pack(side="right", padx=(0, 6))

    def _on_run(self) -> None:
        """Run, wrapped: committing resolves paths, builds a manifest and writes files, and any of those can fail (3027-3043)."""
        try:
            self.commit()
        except Exception as exc:  # noqa: BLE001
            try:
                tell("The run failed:\n\n%s" % exc, "error", master=self.top)
            except Exception:  # noqa: BLE001
                log.warning("the run failed: %s", exc)

    def choose_destination(self) -> None:
        """Picks the folder the approved frames are written to (2506-2515). A destination inside the source folder is
        refused later, where the frames are copied, which checks it against every source directory."""
        from tkinter import filedialog

        if not self.editable():
            return
        path = filedialog.askdirectory(parent=self.top, title="Folder for the approved frames")
        if path:
            self.state["destination"] = path

    def _on_destination(self) -> None:
        self.choose_destination()
        self.refresh()

    def destination_text(self, t: dict[str, Any]) -> str:
        """Which of the two things Run will do, as plain text (3337-3350)."""
        return plain_text(FS.destination_text(self.state, t))

    def sync_actions(self, t: dict[str, Any]) -> None:
        """Run is enabled when there is something to do, and nothing else (3322-3333)."""
        copying = self.copying_out()
        if copying:
            ok = t["approved"] > 0 and self.state["destination"] is not None
        else:
            ok = t["condemned"] > 0
        self.run_button.state(["!disabled"] if self.editable() and ok else ["disabled"])
        self.dest_button.state(["!disabled"] if self.editable() else ["disabled"])
        self.dest_var.set(self.destination_text(t))
        deleting = self.state["destination"] is not None and not copying
        if deleting:
            self.dest_label.configure(foreground="#b02222", font=self._bold)
        else:
            self.dest_label.configure(foreground="", font="TkDefaultFont")
        self.sync_master_flat_controls()

    # ---- refreshing

    def recompute_all(self) -> dict[str, Any]:
        """Every channel's verdicts from its cohort, and the totals Run acts on (3286-3303)."""
        return FS.recompute_all(self.state)

    def summary_text(self, t: dict[str, Any]) -> str:
        return FS.summary_text(self.state, t, self.current)

    def refresh(self) -> None:
        """Recomputes every verdict from the cohort and redraws (3255-3284). The table is rebuilt, which drops the
        selection, so the row that was selected is selected again without reloading its frame."""
        t = self.recompute_all()
        keep = self.selected_row_index()
        self.fill_channels()
        self.fill_frames()
        if keep >= 0:
            self.select_row(keep, False)
        self.fill_plot()
        self.sync_knobs()
        self.summary_var.set(self.summary_text(t))
        # Criterion and override changes do not reload the bitmap, so the tags are repainted here.
        self.preview.set_tags(self.current_tags())
        self.loader.sync_strip()
        self.sync_actions(t)

    # ---- the layout

    def lay_out_criteria(self) -> None:
        """The Approval criteria group's two rows (3829-3863)."""
        from tkinter import ttk

        box = self.criteria_group
        row1 = ttk.Frame(box)
        row1.pack(fill="x")
        ttk.Label(row1, text="Preset").pack(side="left")
        self.preset_combo.pack(in_=row1, side="left", padx=6)
        ttk.Label(row1, text="k").pack(side="left", padx=(6, 2))
        self.k_entry.pack(in_=row1, side="left")
        self.enabled_check.pack(in_=row1, side="left", padx=(12, 0))
        self.keep_label.pack(in_=row1, side="right")
        row2 = ttk.Frame(box)
        row2.pack(fill="x", pady=(6, 0))
        for metric in F.CRITERIA_ORDER:
            self.crit_checks[metric].pack(in_=row2, side="left")
            self.crit_edits[metric].pack(in_=row2, side="left", padx=(4, 16))

    def lay_out(self) -> None:
        """Sizers only: what goes where (3865-3946). The window opens on the first frame rather than on an empty pane."""
        main = self.main
        main.columnconfigure(0, weight=0)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=0)
        main.rowconfigure(0, weight=3)
        main.rowconfigure(2, weight=1)
        self.lay_out_criteria()
        self.channel_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.table_frame.grid(row=0, column=1, sticky="nsew")
        self.preview_frame.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        self.strip_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=6)
        self.plot_frame.grid(row=2, column=0, columnspan=3, sticky="nsew")
        self.criteria_group.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.problems_label.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.summary_label.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.dest_frame.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.master_flat_frame.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.button_row.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.refresh()
        self.select_row(0)
