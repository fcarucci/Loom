"""The night picker window: the tkinter half of the ASIAIR import, ported from ``pixinsight/script/lib/NightDialog.js``.

The data it shows comes from :mod:`loomsiril.night_dialog` (survey_of, row_for, flat_summary, recent_nights,
flats_for_night); this module decides how the survey reads in the tree and how the picker behaves, and nothing else.
Ported from the JavaScript, lines 127-294:

* the constructor (NightDialog.js 127-211): the window, its tree of targets and nights, the detail box, Use this night
  and Cancel; :class:`NightDialog` (``__init__``) builds them, and the title, minimum width and column headers are the
  JS values.
* ``fill`` (214-242): one parent row per target with its total frame count, one child row per recent night (date and
  span, count, filters, number of flats); :func:`tree_rows` returns these rows as plain data, and
  :meth:`NightDialog._fill` puts them in the tree.
* ``remember`` (248-254): what the current row means. A target row selects nothing; Use this night is enabled only
  for a night. :meth:`NightDialog._remember` reads the selection, and :meth:`NightDialog._selected_night` answers the
  same question on demand.
* ``describe`` (262-280): the chosen night and its flats filter by filter, a filter with no flats marked as missing
  (the JS draws it in red). :func:`describe` returns the text as (segment, missing) pairs.
* ``release`` (283-293): the handlers are detached before the window goes; :meth:`NightDialog._release`.

:func:`pick_night` is the modal entry point (the JS ``Dialog`` run): it returns the chosen night, or None on Cancel,
after which the folder chooser follows. Importing this module creates no window; tkinter is imported when a picker is
made. The theme comes from :mod:`loomsiril.tk_theme`, so the ttk widgets take Siril's look when Siril is given.

Semantics kept exactly:

* A night is "missing" when one of its filters has no flat in the survey's flats for its session (the same matching
  as the JS: a flat belongs to the filter whose name equals its ``filter``). ``flat_summary`` keeps such a filter in
  the output as "missing", and that is what is shown in red.
* Totals and counts are printed with ``String(...)`` (:func:`loomsiril.jsmath.js_string`).
* Only the first recent nights of each target are listed (``RECENT``, NightDialog.js 106 and 36-37).
"""

from __future__ import annotations

from typing import Any, Optional

from loomsiril import night_dialog as D
from loomsiril.jsmath import js_string

#: The window's title and minimum width (NightDialog.js 129-135 and 141).
WINDOW_TITLE = "Import from ASIAIR"
MIN_WIDTH = 640
#: The colour of a missing flat (NightDialog.js 277): the same red in the tree and in the detail box.
MISSING_COLOUR = "#ff5555"

#: The text of the detail box before a night is chosen (NightDialog.js 129-263, remember/describe).
SELECT_A_NIGHT = "Select a night."

Night = dict[str, Any]
#: A piece of the detail text and its tag: "" plain, "bold" the target, "missing" a filter with no flats.
Segment = tuple[str, str]


def _filter_matches(survey: D.Survey, night: Night) -> list[dict[str, Any]]:
    """The night's flats matched filter by filter, as ``describe`` and ``fill`` do (NightDialog.js 264-270, 226-236).

    A flat belongs to a filter when its ``filter`` equals the filter name; a filter with no flat is "missing".
    """
    flats = D.flats_for_night(survey, night)
    out: list[dict[str, Any]] = []
    for f in night["filters"]:
        hits = [x for x in flats if x["filter"] == f]
        out.append({"filter": f, "flats": hits, "strength": "exact" if hits else "missing"})
    return out


def _is_missing(survey: D.Survey, night: Night) -> bool:
    """True when some filter of the night has no flats (the row is drawn in red)."""
    return any(s["strength"] == "missing" for s in D.flat_summary(_filter_matches(survey, night)))


def tree_rows(survey: D.Survey) -> list[dict[str, Any]]:
    """The rows the tree shows, in order: each target, then its recent nights (NightDialog.js 214-242).

    A target row has ``kind`` "target", its name in ``text``, the sum of its recent nights' counts in ``frames`` and
    no ``night``. A night row has ``kind`` "night", its date and span in ``text``, its frame count, its filters and
    the number of flats for its session in ``flats``, and ``missing`` set when one of its filters has no flats.
    ``parent`` is the iid of the target row ("" for a target), and every row's ``iid`` is unique.
    """
    rows: list[dict[str, Any]] = []
    for ti, target in enumerate(survey["targets"]):
        t_iid = f"t{ti}"
        recent = D.recent_nights(survey, target, D.RECENT)
        total: Any = 0
        for night in recent:
            total = total + night["count"]
        rows.append({"iid": t_iid, "parent": "", "kind": "target", "text": js_string(target),
                     "frames": js_string(total), "filters": "", "flats": "", "missing": False, "night": None})
        for ni, night in enumerate(recent):
            row = D.row_for(night)
            rows.append({
                "iid": f"{t_iid}n{ni}",
                "parent": t_iid,
                "kind": "night",
                "text": row["date"] + "  " + row["span"],
                "frames": row["count"],
                "filters": row["filters"],
                "flats": js_string(len(D.flats_for_night(survey, night))),
                "missing": _is_missing(survey, night),
                "night": night,
            })
    return rows


def describe(survey: D.Survey, night: Optional[Night]) -> list[Segment]:
    """The detail text for the chosen night, as (text, tag) segments (NightDialog.js 262-280).

    With no night, the text is "Select a night.". The target name is tagged "bold" (the JS bolds it), and a filter
    with no flats is tagged "missing" so the window draws it in red; the other segments are untagged.
    """
    if night is None:
        return [(SELECT_A_NIGHT, "")]
    flats = D.flats_for_night(survey, night)
    head = (" \u2014 " + js_string(night["date"]) + ", " + js_string(night["count"])
            + " frames in " + ", ".join(night["filters"]) + ".\n")
    segments: list[Segment] = [(js_string(night["target"]), "bold"), (head, "")]
    n = len(flats)
    segments.append((str(n) + " flat" + ("" if n == 1 else "s") + " in this observing session: ", ""))
    summary = D.flat_summary(_filter_matches(survey, night))
    for i, s in enumerate(summary):
        if i:
            segments.append((", ", ""))
        segments.append((s["text"], "missing" if s["strength"] == "missing" else ""))
    segments.append((".", ""))
    return segments


def pick_night(survey: D.Survey, card_root: str, siril: Any = None,
               log: Any = None, master: Any = None) -> Optional[Night]:
    """Shows the picker and returns the chosen night, or None on Cancel (the JS Dialog run).

    With ``master`` (the caller's root) the picker is a window of that root, transient to it, and the root is left
    alone. Without it a hidden root of its own is made and destroyed.
    """
    if master is not None:
        dlg = NightDialog(master, survey, card_root)
        try:
            dlg.top.transient(master)
        except Exception:  # a withdrawn root cannot always be a transient parent: the picker is still usable
            pass
        master.wait_window(dlg.top)
        return dlg.result

    from loomsiril.tk_theme import close_root, themed_root

    root = themed_root(siril, log=log)
    try:
        dlg = NightDialog(root, survey, card_root)
        root.wait_window(dlg.top)
        return dlg.result
    finally:
        close_root(root)


def _bold_font(font: Any) -> Any:
    """The detail font in bold: the theme's font with its weight set to bold; the plain font when that cannot be made."""
    import tkinter.font as tkfont

    try:
        base = tkfont.nametofont(font) if isinstance(font, str) else font
        bold = tkfont.Font(font=base)
        bold.configure(weight="bold")
        return bold
    except Exception:  # an unnamed or unusual font: the text stays plain
        return font


class NightDialog:
    """The picker: a Toplevel on ``master`` with the target and night tree, the detail box, and Use this night / Cancel.

    ``result`` is the night chosen when the window closes with Use this night, else None. Built visible on the master;
    :func:`pick_night` waits for it.
    """

    def __init__(self, master: Any, survey: D.Survey, card_root: str) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.survey = survey
        self.card_root = card_root
        self.result: Optional[Night] = None
        self.selected_night: Optional[Night] = None
        self._nights: dict[str, Night] = {}

        self.top = tk.Toplevel(master)
        self.top.title(WINDOW_TITLE)
        self.top.minsize(MIN_WIDTH, 0)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        frame = ttk.Frame(self.top, padding=8)
        frame.pack(fill="both", expand=True)

        self.where = ttk.Label(frame, text="Card: " + card_root, anchor="w", justify="left", wraplength=MIN_WIDTH - 40)
        self.where.pack(fill="x")

        tree_box = ttk.Frame(frame)
        tree_box.pack(fill="both", expand=True, pady=(6, 0))
        self.tree = ttk.Treeview(tree_box, columns=("frames", "filters", "flats"), show="tree headings",
                                 selectmode="browse", height=12)
        self.tree.heading("#0", text="Target / night", anchor="w")
        self.tree.heading("frames", text="Frames", anchor="w")
        self.tree.heading("filters", text="Filters", anchor="w")
        self.tree.heading("flats", text="Flats", anchor="w")
        self.tree.column("#0", width=260, stretch=True)
        self.tree.column("frames", width=70, stretch=False, anchor="e")
        self.tree.column("filters", width=160, stretch=True)
        self.tree.column("flats", width=60, stretch=False, anchor="e")
        self.tree.tag_configure("missing", foreground=MISSING_COLOUR)
        scroll = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._fill()

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        #: The detail box takes Siril's colours and font from the theme (as frames_ui.py does for its canvas), so it
        #: does not show white under a dark theme.
        style = ttk.Style()
        background = style.lookup("TFrame", "background") or None
        foreground = style.lookup("TLabel", "foreground") or None
        font = style.lookup("TLabel", "font") or "TkDefaultFont"
        self.detail = tk.Text(frame, height=4, wrap="word", relief="sunken", borderwidth=1, background=background,
                              foreground=foreground, font=font)
        self.detail.tag_configure("missing", foreground=MISSING_COLOUR)
        self.detail.tag_configure("bold", font=_bold_font(font))
        self.detail.pack(fill="x", pady=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(6, 0))
        self.ok_button = ttk.Button(buttons, text="Use this night", command=self._ok, state="disabled")
        self.ok_button.pack(side="right")
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self._cancel)
        self.cancel_button.pack(side="right", padx=(0, 6))

        self._remember()
        self.top.bind("<Escape>", lambda _event: self._cancel())

    def _fill(self) -> None:
        """Puts :func:`tree_rows` in the tree; each target is open, and a night's iid maps to its night."""
        for row in tree_rows(self.survey):
            parent = row["parent"]
            tags = ("missing",) if row["missing"] else ()
            self.tree.insert(parent, "end", iid=row["iid"], text=row["text"],
                             values=(row["frames"], row["filters"], row["flats"]), tags=tags,
                             open=(row["kind"] == "target"))
            if row["night"] is not None:
                self._nights[row["iid"]] = row["night"]

    def _current_night(self) -> Optional[Night]:
        """The night under the selection, or None when a target (or nothing) is selected."""
        sel = self.tree.selection()
        if not sel:
            return None
        return self._nights.get(sel[0])

    def _remember(self) -> None:
        """Reads the selection: the chosen night, Use this night enabled for a night, and the detail box."""
        self.selected_night = self._current_night()
        self.ok_button.state(["!disabled"] if self.selected_night is not None else ["disabled"])
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        for text, tag in describe(self.survey, self.selected_night):
            self.detail.insert("end", text, (tag,) if tag else ())
        self.detail.configure(state="disabled")

    def _on_select(self, _event: Any = None) -> None:
        """A target row selects nothing: it is cleared, so only a night can be chosen."""
        sel = self.tree.selection()
        if sel and sel[0] not in self._nights:
            self.tree.selection_remove(sel[0])
        self._remember()

    def _on_double_click(self, _event: Any = None) -> None:
        self._remember()
        if self.selected_night is not None:
            self._ok()

    def _ok(self) -> None:
        """Use this night: the chosen night is the result, and the window closes."""
        night = self._current_night()
        if night is None:
            return
        self.selected_night = night
        self.result = night
        self._release()

    def _cancel(self) -> None:
        """Cancel (and closing the window): no night, so the folder chooser follows."""
        self.result = None
        self._release()

    def _release(self) -> None:
        """Detaches the handlers, then destroys the window; a window already gone is left alone."""
        import tkinter as tk

        try:
            self.tree.unbind("<<TreeviewSelect>>")
            self.tree.unbind("<Double-1>")
            self.ok_button.configure(command="")
            self.cancel_button.configure(command="")
            self.top.destroy()
        except tk.TclError:
            pass
