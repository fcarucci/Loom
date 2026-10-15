"""The Fly-Through options dialog (tkinter, Siril's theme): Input, Target, Clip and Output groups, then the collapsible
Advanced sections (Video details, Logo, Stars and motion, HDR), with what does not apply greyed live. The rules are
loomsiril.fly_model; the Siril script (Loom/loom_fly_through.py) opens it.

The form scrolls (``FormScroll``); the reason line, the draft note and the Draft/Render/Cancel buttons are in a footer
outside the scroll area, always visible. The window is at most 85 % of the screen high (``window_height``).

Adding a control (the section API)
----------------------------------
A section is a frame the dialog creates collapsed (``SECTIONS``); its ``_build_<id>(self, frame, d)`` method fills it:

    def _build_logo(self, frame, d):                       # d: the DialogValues
        self._check_row(frame, "logo_fade", "Fade in", d.logo_fade, name="logo")          # a tick box
        self._text_row(frame, "logo_opacity", "Opacity (%)", d.logo_opacity, name="logo")  # a text box
        self._choice_row(frame, "logo_place", "Placement", d.logo_place, ["top left", "centre"], name="logo")

Primitives (all in LoomFlyDialog):

* ``self._section(sid, title) -> frame``: the collapsible frame (header row ``▸ title`` in the form, body gridded
  below it while open). Called for the four built-in ids by __init__; call it only to add a new section.
* ``self._add_row(frame, label, widget_factory, name=None, on="normal") -> _Row(row, label, widget)``: a grid row in
  ``frame``: the label in column 0, ``widget_factory(frame)`` in column 1 (put extras, a Browse... button or a grey
  note, in column 2 of ``row.row`` yourself). ``name`` is the greying group (below); ``on`` the widget's state while the
  group applies ("readonly" for a combo). ``self._next(frame)`` is the next free row of ``frame`` (no argument: of
  the main form).
* ``self._control(key, var, getter=None, rules=False)``: registers the tk variable ``var`` under ``key``. ``getter()``
  (default: the text stripped, or the bool) gives the value in ``values()[key]``; it may raise ValueError("message") for
  a bad entry (shown in a box on Render/Draft; the autosave skips that key only). The variable is traced: a change
  saves the options. ``rules=True``: a change also re-runs the enable rules (``_sync``) and the file checks.
  ``_text_row`` / ``_choice_row`` / ``_check_row`` = ``_add_row`` + ``_control`` for the usual widgets.

(a) Value: ``values()[key]`` is ``getter()`` (the job reads the key). (b) Greying and reasons: give the row
``name="logo"``; add ``"logo"`` to ``fly_model.GROUPS`` and a branch in ``FlyDialogModel.enabled`` (it reads the model's
``controls``: the raw values of the ``rules=True`` controls, e.g. ``self.controls.get("logo_file")``) and a line in
``FlyDialogModel.disabled_reason`` ("needs Encode video"); ``_sync`` greys every registered group by ``enabled`` and the
section header shows the reason when all its named rows are greyed. Render blockers go in ``FlyDialogModel.reasons``.
(c) Saved: the key is also a ``DialogValues`` field (its default) and in ``siril_bridge.OPTION_KEYS`` (and
``PATH_KEYS`` when it is a file path); ``_snapshot`` saves ``values()`` and the open sections (key ``sections``).
"""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any, Callable, NamedTuple, Optional

from loomsiril import fly_model
from loomsiril import fly_option_rows, fly_options

#: The default preset and the dialog's default values (the pipeline's defaults; FlyJob.presets).
DEFAULT_PRESET = "youtube_1080"
#: The Browse... file picker: what read_layer reads (XISF is not read by the Fly-Through, so it is not offered).
LAYER_FILETYPES = [("TIFF or FITS", "*.tif *.tiff *.fit *.fits *.fts"), ("TIFF", "*.tif *.tiff"),
                   ("FITS", "*.fit *.fits *.fts"), ("All files", "*.*")]
#: The Music... file picker: the audio ffmpeg reads (the same list as PixInsight's Music... button).
AUDIO_FILETYPES = [("Audio", "*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus"), ("All files", "*.*")]
#: The Logo Browse... picker: the pictures loomsiril.logo reads (PNG keeps its transparency; JPEG is not read).
LOGO_FILETYPES = [("Pictures (PNG, TIFF, FITS)", "*.png *.tif *.tiff *.fit *.fits *.fts"), ("PNG", "*.png"),
                  ("TIFF", "*.tif *.tiff"), ("FITS", "*.fit *.fits *.fts"), ("All files", "*.*")]


#: Stands for "not in the saved options": the dialog then shows the target's RA or Dec (not an empty box).
_FROM_TARGET: Any = object()


def _default_star_quality() -> str:
    """The star quality the dialog starts on (presets.STAR_QUALITY_DEFAULT, read when the dialog opens)."""
    from loomsiril import presets

    return presets.STAR_QUALITY_DEFAULT


@dataclass
class DialogValues:
    """What the dialog starts from: the saved options, or the pipeline's defaults for the keys they lack.

    ``from_options`` takes each field from the options dict when the key is there, else keeps the default
    here (the values ``dict.get(key, default)`` gave before). ``ra`` and ``dec`` default to the target's.
    """

    starless_path: Any = ""
    stars_path: Any = ""
    ra: Any = _FROM_TARGET
    dec: Any = _FROM_TARGET
    target_type: Any = "nebula"
    distance: Any = None
    duration: Any = 8.0
    fps: Any = 30
    loop: Any = "none"
    star_quality: Any = field(default_factory=_default_star_quality)
    dynamic: Any = "sdr"
    gaia: Any = "cache"
    object_name: Any = ""
    out_dir: Any = field(default_factory=os.getcwd)
    presets: Any = None
    video: Any = True   # encode by default (greyed and unticked where ffmpeg is not found)
    starnet_licence: Any = None
    star_tool: Any = ""                                          # the last extraction tool chosen (a resolver id)
    image_path: Any = ""                                         # the file to extract stars from ("": the open image)
    mode: Any = None                                             # "extract" or "files" as last used (None: not saved yet)
    last_folder: Any = ""                                        # the folder of the last Browse...
    music_path: Any = ""                                         # the music under the video ("": none)
    music_fade: Any = True                                       # fade the music in and out (PixInsight's default)
    logo_path: Any = ""                                          # the logo drawn over every frame ("": none)
    logo_place: Any = "off"                                      # "off" or a loomsiril.logo.LOGO_PLACES id (PixInsight's default: Off)
    logo_opacity: Any = 100                                      # percent, 0-100 (PixInsight's slider: 100)
    logo_delay: Any = 0                                          # fade in after this many seconds, 0-600 (0: from the start)
    format: Any = None                                           # the video format id (None: the default for the ffmpeg and sizes)
    quality: Any = "high"                                        # the video quality: "high" or "standard"
    ffmpeg_path: Any = ""                                        # a chosen ffmpeg ("": the one found)
    ms_per_mp: Any = None                                        # render speed measured on the last render (the estimate)
    sections: Any = None                                         # the ids of the Advanced sections left open (None: all collapsed)
    fly: Any = field(default_factory=fly_options.defaults)       # Stars and motion + HDR, by saved key (fly_options): engine values

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> "DialogValues":
        """The dialog's starting values from a saved (or default) options dict."""
        values = cls(**{f.name: options[f.name] for f in fields(cls) if f.name in options})
        values.fly = fly_options.load_values(options)            # a missing or invalid saved value is the default
        return values


def run_dialog(defaults: dict, discover: Optional[Callable[[], Any]], target: Optional[dict],
               siril: Any = None, draft: Optional[Callable[[Any, dict], Any]] = None, names: Any = None,
               persist: Optional[Callable[[dict], Any]] = None) -> Optional[dict]:
    """Show the options dialog (tkinter, Siril's theme). Returns the chosen values, or None on Cancel.

    ``discover()`` returns a loomsiril.fly_tools.Discovery; the dialog calls it on a worker thread. ``draft(window,
    values)`` shows the draft preview (the Draft button; None hides the button). ``names`` (a
    loomsiril.skyname.NameContext, or None) turns on the online name lookup of the Object name. ``persist(options)``
    (or None) saves the options: shortly after a change, on a worker thread, and once more when the window closes."""
    from loomsiril.tk_theme import close_root, themed_root

    root = themed_root(siril, log=getattr(siril, "log", None))
    try:
        dlg = LoomFlyDialog(root, DialogValues.from_options(defaults), discover, target, draft, names, persist)
        root.wait_window(dlg.top)
        return dlg.result
    finally:
        close_root(root)


def _same_name(typed: str, hit: dict) -> bool:
    """True when the Object box's text names the found object (its id or its name, ignoring case, spaces and dashes)."""
    def squash(text: Any) -> str:
        return "".join(ch for ch in str(text or "").lower() if ch.isalnum())

    return bool(squash(typed)) and squash(typed) in (squash(hit.get("id")), squash(hit.get("name")))


def _form_fields(d: DialogValues, target: Optional[dict], presets: Any, orientation: str = "landscape") -> list["_Field"]:
    """Every text box and drop-down of the form (the dialog places them in its Input, Target, Clip and Output groups)."""
    ra, dec = (target or {}).get("ra", ""), (target or {}).get("dec", "")
    loop_labels = [label for _id, label in presets.LOOPS]
    orientation_labels = [label for _id, label in fly_model.ORIENTATIONS]
    return [
        _Field("image_path", "Image file (TIFF/FITS)", d.image_path, None),
        _Field("starless_path", "Starless file (TIFF/FITS)", d.starless_path, None),
        _Field("stars_path", "Stars file (TIFF/FITS)", d.stars_path, None),
        _Field("ra", "Target RA (deg)", ra if d.ra is _FROM_TARGET else d.ra, None),
        _Field("dec", "Target Dec (deg)", dec if d.dec is _FROM_TARGET else d.dec, None),
        _Field("target_type", "Target type", d.target_type, ["nebula", "galaxy"]),
        _Field("distance", "Distance (pc, blank = auto)", d.distance, None),
        _Field("duration", "Duration (s)", d.duration, None),
        _Field("fps", "Frames per second", d.fps, [str(f) for f in presets.FPS]),
        _Field("orientation", "Orientation", fly_model.orientation_label(orientation), orientation_labels),
        _Field("loop", "Loop", dict(presets.LOOPS).get(d.loop, loop_labels[0]), loop_labels),
        _Field("star_quality", "Star quality", d.star_quality, presets.STAR_QUALITIES),
        _Field("dynamic", "Dynamic range", d.dynamic, ["sdr", "hdr"]),
        _Field("gaia", "Gaia source", d.gaia, ["cache", "online"]),
        _Field("object_name", "Object name", d.object_name, None),
        _Field("out_dir", "Output folder", d.out_dir, None),
        _Field("music_path", "Music", d.music_path, None),
    ]


#: Which fields sit in which dialog group, in order.
TARGET_FIELDS = ("ra", "dec", "target_type", "distance", "gaia")
CLIP_FIELDS = ("duration", "fps", "orientation", "loop", "star_quality", "dynamic")
OUTPUT_FIELDS = ("object_name", "out_dir")
#: Browse... buttons: the file pickers (and the group they are greyed with), and the folder picker.
FILE_PICKERS = {"image_path": "extract", "starless_path": "files", "stars_path": "files", "music_path": "music"}
GREY = "gray50"

#: The Advanced sections, in order: ``(id, title)``. Collapsed by default; the open ones are saved as ``sections``.
SECTIONS: tuple[tuple[str, str], ...] = (("video", "Video details"), ("logo", "Logo"), ("motion", "Stars and motion"),
                                         ("hdr", "HDR"))
#: The window is at most this fraction of the screen's height (the form scrolls when it is taller).
SCREEN_FRACTION = 0.85
CLOSED_MARK, OPEN_MARK = "\u25b8", "\u25be"                       # the header's triangle: right (closed), down (open)


def window_height(content: int, screen: int, fraction: float = SCREEN_FRACTION) -> int:
    """The window's height: ``content`` (what the form and its footer ask for), at most ``fraction`` of ``screen``
    (an unknown screen, 0 or less, leaves ``content``)."""
    if screen <= 0:
        return int(content)
    return int(min(content, int(screen * fraction)))


class FormScroll:
    """A vertically scrolling frame: ``inner`` holds the form, ``outer`` is gridded into the window (scrollbar on the
    right; the mouse wheel on macOS, Windows and Linux scrolls it while the pointer is over it, and a widget that takes
    the keyboard focus is scrolled into view). The idea of loom/dialog.py's _ScrollArea, but sized by ``window_height``
    and with a wheel that does not depend on Enter/Leave (those also fire when the pointer enters a child widget)."""

    def __init__(self, parent: Any) -> None:
        import tkinter as tk
        from tkinter import ttk

        from loomsiril.loom.dialog import wheel_units

        self._wheel_units = wheel_units
        self._dead = False
        self.outer = ttk.Frame(parent)
        self.outer.columnconfigure(0, weight=1)
        self.outer.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self.outer, highlightthickness=0, borderwidth=0)
        try:
            background = ttk.Style().lookup("TFrame", "background")
            if background:
                self.canvas.configure(background=background)
        except Exception:
            pass
        self.bar = ttk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar.grid(row=0, column=1, sticky="ns")
        self.inner = ttk.Frame(self.canvas, padding=10)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self._update())
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.outer.bind("<Destroy>", self._on_destroy, add="+")
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(sequence, self._on_wheel, add="+")
        self.canvas.bind_all("<FocusIn>", self._on_focus, add="+")

    def fit(self, footer_height: int = 0) -> None:
        """Sizes the visible area to the form, so that form and footer together are at most 85 % of the screen high."""
        self.inner.update_idletasks()
        request = self.inner.winfo_reqheight()
        limit = window_height(request + footer_height, self.canvas.winfo_screenheight()) - footer_height
        self.canvas.configure(width=self.inner.winfo_reqwidth(), height=max(120, min(request, limit)))
        self._update()

    def _update(self) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_destroy(self, event: Any = None) -> None:
        if event is None or getattr(event, "widget", self.outer) is self.outer:
            self._dead = True

    def _inside(self, widget: Any) -> bool:
        """True when ``widget`` is the scroll area or one of its descendants."""
        while widget is not None:
            if widget is self.outer:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_wheel(self, event: Any) -> None:
        """The wheel scrolls the form while the pointer is over it (a drop-down list or another window keeps its own)."""
        if self._dead:
            return
        try:
            if not self._inside(self.canvas.winfo_containing(event.x_root, event.y_root)):
                return
            number = getattr(event, "num", None)
            units = -1 if number == 4 else 1 if number == 5 else self._wheel_units(event.delta)
            self.canvas.yview_scroll(units, "units")
        except Exception:
            pass

    def _on_focus(self, event: Any) -> None:
        if not self._dead and self._inside(getattr(event, "widget", None)):
            self.reveal(event.widget)

    def reveal(self, widget: Any) -> None:
        """Scrolls so that ``widget`` is in view (a Tab to a control below the fold, a section just opened)."""
        try:
            self.inner.update_idletasks()
            total = max(1, self.inner.winfo_height())
            top = widget.winfo_rooty() - self.inner.winfo_rooty()
            bottom = top + widget.winfo_height()
            view_top = self.canvas.canvasy(0)
            view_h = self.canvas.winfo_height()
            if top < view_top:
                self.canvas.yview_moveto(max(0.0, top / total))
            elif bottom > view_top + view_h:
                self.canvas.yview_moveto(max(0.0, (bottom - view_h) / total))
        except Exception:
            pass


class _Row(NamedTuple):
    """What ``_add_row`` made: its grid ``row`` in the frame, the ``label`` widget and the control ``widget``."""
    row: int
    label: Any
    widget: Any


@dataclass
class _SectionState:
    """One Advanced section: its header, the grey reason in the header, its body ``frame`` and which groups its rows are in."""
    sid: str
    title: str
    frame: Any
    header: Any
    reason: Any
    row: int
    open: bool = False
    groups: list = field(default_factory=list)
    ungated: bool = False                                        # a row with no greying group: the section is never all grey


class _Field(NamedTuple):
    """A form row: the variable ``name``, its ``label``, its starting ``value``, drop-down ``choices`` (None: text box)."""
    name: str
    label: str
    value: Any
    choices: Optional[list]


class LoomFlyDialog:
    """The Fly-Through options window. ``result`` is the dict main() reads, or None when cancelled.

    Groups: Input, Target, Clip, Output. What does not apply is greyed live (``_sync`` after every change; the rules
    are loomsiril.fly_model.FlyDialogModel). ``discover`` (a callable returning loomsiril.fly_tools.Discovery, or None)
    runs on a worker thread; the tool list fills in when it returns. Built only where tkinter can open a window.
    """

    def __init__(self, master: Any, defaults: DialogValues, discover: Optional[Callable[[], Any]],
                 target: Optional[dict], draft: Optional[Callable[[Any, dict], Any]] = None,
                 names: Any = None, persist: Optional[Callable[[dict], Any]] = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        from loomsiril import presets
        from loomsiril.fly_model import IMAGE_HINT, window_title

        self.result: Optional[dict] = None
        self.top = tk.Toplevel(master)
        self._names = names                                       # skyname.NameContext or None: no name lookup
        self.top.title(window_title(getattr(names, "open_path", "")))
        self._vars: dict[str, Any] = {}
        d = defaults
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(0, weight=1)
        self._scroll = FormScroll(self.top)                       # the form scrolls; the footer below stays in view
        self._scroll.outer.grid(row=0, column=0, sticky="nsew")
        frame = self._scroll.inner
        self._frame = frame
        self._footer = ttk.Frame(self.top, padding=(10, 0, 10, 10))
        self._footer.grid(row=1, column=0, sticky="we")
        self._footer.columnconfigure(0, weight=1)
        self._ctl: dict[str, tuple[Any, Callable[[], Any]]] = {}  # key -> (tk variable, getter): the section controls
        self._rule_vars: dict[str, Any] = {}                      # key -> variable the enable rules read (rules=True)
        self._sections: dict[str, _SectionState] = {}
        self._frame_rows: dict[int, int] = {}                     # id(section frame) -> its last used grid row
        self._open_saved = [sid for sid in (d.sections if isinstance(d.sections, (list, tuple)) else ())
                            if sid in dict(SECTIONS)]
        self._licence = defaults.starnet_licence                  # the accepted StarNet licence revision, if any
        self._saved_tool = d.star_tool
        self._draft_cb = draft
        self._tools: list = []
        self._looking = True
        self._ffmpeg: Optional[bool] = None
        self._closed = False
        self._acting = False                                      # a Render or Draft click is being prepared
        self._checks: dict[tuple[str, str], str] = {}             # file checks done by the worker: (kind, path) -> message
        self._grid_notes: dict[str, str] = {}                     # Image file path -> the worker's size note or refusal
        self._afters: dict[str, Any] = {}                         # every pending after id, by name
        self._jobs: list[dict] = []                               # running worker jobs: thread and poll
        self._seq = 0
        self._draft_runs: list = []                               # DraftRuns started from this window
        self._saved_mode = d.mode if d.mode in ("extract", "files") else None
        self._mode_touched = self._saved_mode is not None        # a remembered mode is not overridden by discovery
        self._persist = persist                                   # saves the options (None: nothing is remembered)
        self._save_lock = threading.Lock()                        # one save at a time; an older save never lands last
        self._save_seq = 0                                        # the newest save asked for
        self._saved_seq = 0                                       # the newest save written
        self._last_dir = str(d.last_folder or "")  # the folder of the last Browse..., for every picker
        self._row_no = -1
        self._named_for: Optional[tuple] = None                   # the sources the last name lookup was started for
        self._typed_seq = 0                                       # the newest Object "Find", older answers are dropped
        self._dist_seq = 0                                        # the newest distance lookup, older answers are dropped
        self._dist_result: Any = None                             # the last finished lookup (a DistanceResult) ...
        self._dist_key: Optional[tuple] = None                    # ... and the inputs it was made for
        self._dist_asked: Optional[tuple] = None                  # the inputs of the newest lookup started (or answered)
        self._dist_cancel: Optional[threading.Event] = None       # stops the running lookup when a newer one starts
        self._w: dict[str, list] = {}                             # group name -> (widget, enabled state) pairs
        self._hints: dict[tuple, Any] = {}                        # (group, frame, row) -> (label, var) of its grey reason
        self._file_grids: dict[str, tuple] = {}                   # Image file path -> (width, height, channels), by the worker
        self._orientation_touched = False                         # the user chose the Orientation (until the image changes)
        self._orientation_setting = False                         # the dialog itself is setting the combo
        self._natural = fly_model.natural_orientation(self._open_grid())
        fields = {f.name: f for f in _form_fields(d, target, presets, self._natural)}
        self._mode = tk.StringVar(value="files")
        self._vars["mode"] = self._mode
        self._tool = tk.StringVar(value="")
        self._status = tk.StringVar(value="Looking for star-extraction tools...")
        # Input
        self._heading("Input")
        r = self._next()
        self._radio = ttk.Radiobutton(frame, text="Extract stars from the image with:", value="extract",
                                      variable=self._mode, state="disabled", command=self._touch_mode)
        self._radio.grid(row=r, column=0, sticky="w")
        self._tool_box = ttk.Combobox(frame, textvariable=self._tool, values=[], state="disabled", width=31)
        self._tool_box.grid(row=r, column=1, sticky="we", padx=4, pady=2)
        self._reg("extract", self._tool_box, "readonly")
        self._busy = ttk.Progressbar(frame, mode="indeterminate", length=60)
        self._busy.grid(row=r, column=2, padx=4)
        self._busy.start(60)
        self._entry(fields["image_path"], self._next())
        self._image_note = tk.StringVar(value=IMAGE_HINT)         # the hint, or "Image file 2000 x 1222 is the same field..."
        note = ttk.Label(frame, textvariable=self._image_note, wraplength=320, foreground=GREY, justify="left")
        note.grid(row=self._next(), column=1, columnspan=2, sticky="w", padx=4)
        self._reg("extract", note)
        ttk.Label(frame, textvariable=self._status, wraplength=320, foreground=GREY).grid(
            row=self._next(), column=1, columnspan=2, sticky="w", padx=4)
        ttk.Radiobutton(frame, text="Starless and stars files", value="files", variable=self._mode,
                        command=self._touch_mode).grid(row=self._next(), column=0, sticky="w")
        self._entry(fields["starless_path"], self._next())
        self._entry(fields["stars_path"], self._next())
        # Target, Clip
        self._heading("Target")
        self._distance_note = tk.StringVar(value="")              # "Distance: ~1,950 pc (cluster of 37 stars, Gaia)"
        self._distance_hint = tk.StringVar(value="auto")          # the Distance box's placeholder (never its value)
        for name in TARGET_FIELDS:
            self._field(fields[name])
            if name == "distance":
                ttk.Label(frame, textvariable=self._distance_note, wraplength=320, foreground=GREY).grid(
                    row=self._next(), column=1, columnspan=2, sticky="w", padx=4)
        self._name_note = tk.StringVar(value="")                  # "Found: ... (from the file name via CDS Sesame)"
        ttk.Label(frame, textvariable=self._name_note, wraplength=320, foreground=GREY).grid(
            row=self._next(), column=1, columnspan=2, sticky="w", padx=4)
        self._initial_target = (self._vars["ra"].get(), self._vars["dec"].get())
        self._heading("Clip")
        for name in CLIP_FIELDS:
            self._field(fields[name])
        self._presets: dict[str, Any] = {}
        self._preset_boxes: dict[str, Any] = {}
        chosen = set(d.presets or [DEFAULT_PRESET])
        box = ttk.Frame(frame)
        box.grid(row=self._next(), column=0, columnspan=3, sticky="w", pady=4)
        for i, pid in enumerate(presets.PRESET_ORDER):
            var = tk.BooleanVar(value=pid in chosen)
            self._presets[pid] = var
            check = ttk.Checkbutton(box, text=fly_model.preset_label(pid, self._natural), variable=var)
            check.grid(row=i // 2, column=i % 2, sticky="w")
            self._preset_boxes[pid] = check
        # Output
        self._heading("Output")
        for name in OUTPUT_FIELDS:
            self._field(fields[name])
        self._video = tk.BooleanVar(value=bool(d.video))
        video_box = ttk.Checkbutton(frame, text="Encode video (ffmpeg)", variable=self._video)
        video_box.grid(row=self._next(), column=0, sticky="w")
        self._reg("video", video_box)
        self._video_note = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._video_note, foreground=GREY).grid(row=self._row_no, column=1, sticky="w",
                                                                                 padx=4)
        self._entry(fields["music_path"], self._next())          # the music under the video, with its Browse...
        self._fade = tk.BooleanVar(value=bool(d.music_fade))
        fade_box = ttk.Checkbutton(frame, text="Fade in/out", variable=self._fade)
        fade_box.grid(row=self._next(), column=1, sticky="w", padx=4)
        self._reg("music", fade_box)
        from loomsiril.fly_tooltip import Tooltip

        for w, _on in self._w["music"]:                          # the Music row: label, box, Browse..., and the Fade box
            Tooltip(w, "Music under the video: looped if it is shorter, cut to the video's length." if w is not fade_box else
                    "Fade the music in and out over 2 s (a quarter of a short clip).")
        self._music_note = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._music_note, wraplength=240, foreground=GREY).grid(
            row=self._row_no, column=2, sticky="w", padx=4)
        for sid, title in SECTIONS:                               # Advanced: collapsed unless left open last time
            getattr(self, f"_build_{sid}")(self._section(sid, title), d)
        footer = self._footer                                     # outside the scroll area: always visible
        self._reason = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self._reason, wraplength=420, foreground=GREY).grid(
            row=0, column=0, sticky="w", padx=4)
        ttk.Label(footer, text="A draft is always SDR, 480 px, at a reduced frame rate.", foreground=GREY).grid(
            row=1, column=0, sticky="w", padx=4)
        btns = ttk.Frame(footer)
        btns.grid(row=2, column=0, sticky="e", pady=6)
        ttk.Button(btns, text="Cancel", command=self._cancel).grid(row=0, column=0, padx=4)
        self._draft_btn = None
        if draft is not None:
            self._draft_btn = ttk.Button(btns, text="Draft", command=self._draft)
            self._draft_btn.grid(row=0, column=1, padx=4)
        self._render_btn = ttk.Button(btns, text="Render", command=self._ok)
        self._render_btn.grid(row=0, column=2 if draft is not None else 1, padx=4)
        for button in (self._draft_btn, self._render_btn):        # a click on a greyed button says why, never nothing
            if button is not None:
                button.bind("<Button-1>", self._blocked_click, add="+")
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self._report_callback_errors()
        self._vars["orientation"].trace_add("write", lambda *_: self._orientation_chosen())  # before _changed's _sync
        for var in [self._mode, self._tool, self._video, self._vars["orientation"], *self._presets.values(),
                    *(self._vars[n] for n in ("image_path", "starless_path", "stars_path", "out_dir", "music_path"))]:
            var.trace_add("write", lambda *_: self._changed())
        self._fade.trace_add("write", lambda *_: self._schedule_save())
        for name in ("dynamic", "target_type"):                   # these grey the HDR and Nebula motion controls
            self._vars[name].trace_add("write", lambda *_: self._sync())
        for var in self._vars.values():                           # every box and drop-down is remembered
            var.trace_add("write", lambda *_: self._schedule_save())
        self._vars["image_path"].trace_add("write", lambda *_: self._names_soon())
        for name in ("ra", "dec", "target_type", "gaia", "distance"):
            self._vars[name].trace_add("write", lambda *_: self._distance_soon())
        self._vars["distance"].trace_add("write", lambda *_: self._show_hint())
        self.top.bind("<Destroy>", self._on_destroy, add="+")
        self._sync()
        self._fit()
        self._start_discovery(discover)
        self._run_validation()
        self._start_names()
        self._start_distance()

    # live enabling ----------------------------------------------------------------------------------------------------

    def _next(self, frame: Any = None) -> int:
        """The next free grid row of the main form, or of the section ``frame`` when one is given."""
        if frame is None or frame is self._frame:
            self._row_no += 1
            return self._row_no
        self._frame_rows[id(frame)] = self._frame_rows.get(id(frame), -1) + 1
        return self._frame_rows[id(frame)]

    def _reg(self, group: str, widget: Any, on: str = "normal") -> None:
        """Puts ``widget`` in the greyed-together ``group`` (``on`` is the state it has when the group applies)."""
        self._w.setdefault(group, []).append((widget, on))

    def _grey(self, text: str, r: int, column: int) -> Any:
        """A small grey hint label."""
        from tkinter import ttk

        label = ttk.Label(self._frame, text=text, foreground=GREY)
        label.grid(row=r, column=column, sticky="w", padx=4)
        return label

    def _heading(self, title: str) -> None:
        """A group title of the form (Input, Target, Clip, Output)."""
        from tkinter import ttk

        ttk.Label(self._frame, text=title, font=("TkDefaultFont", 10, "bold")).grid(
            row=self._next(), column=0, columnspan=3, sticky="w", pady=(8, 2))

    # the Advanced sections (the API is in the module docstring) -------------------------------------------------------

    def _section(self, sid: str, title: str) -> Any:
        """A collapsible section: a header row (a flat button ``\u25b8 title``, Space or Enter toggles it, plus a grey line
        with why its controls are all greyed) and the body frame, returned for ``_add_row``. Collapsed unless ``sid`` was
        left open last time (the ``sections`` option)."""
        import tkinter as tk
        from tkinter import ttk

        is_open = sid in self._open_saved
        header_row = self._next()
        try:
            header = ttk.Button(self._frame, text="", style="Toolbutton", command=lambda: self._toggle_section(sid))
        except tk.TclError:                                       # a theme without the flat style: the plain button
            header = ttk.Button(self._frame, text="", command=lambda: self._toggle_section(sid))
        header.grid(row=header_row, column=0, sticky="w", pady=(8, 2))
        header.bind("<Return>", lambda _e: self._toggle_section(sid) or "break")
        reason = tk.StringVar(value="")
        ttk.Label(self._frame, textvariable=reason, foreground=GREY).grid(
            row=header_row, column=1, columnspan=2, sticky="w", padx=4)
        body = ttk.Frame(self._frame)
        state = _SectionState(sid, title, body, header, reason, row=self._next(), open=is_open)
        self._sections[sid] = state
        self._show_section(state)
        return body

    def _show_section(self, st: _SectionState) -> None:
        """Header text and body visibility follow ``st.open``."""
        st.header.configure(text=f"{OPEN_MARK if st.open else CLOSED_MARK} {st.title}")
        if st.open:
            st.frame.grid(row=st.row, column=0, columnspan=3, sticky="we")
        else:
            st.frame.grid_remove()

    def _toggle_section(self, sid: str, open_: Optional[bool] = None) -> None:
        """Opens or closes section ``sid`` (``open_`` None: the other way), refits the window and remembers the choice."""
        st = self._sections[sid]
        st.open = (not st.open) if open_ is None else bool(open_)
        self._show_section(st)
        self._fit()
        if st.open:
            self._scroll.reveal(st.frame)
        self._schedule_save()

    def _fit(self) -> None:
        """The window's height: the form and the footer, at most 85 % of the screen (the form scrolls past that)."""
        self._scroll.fit(self._footer.winfo_reqheight())

    def _section_of(self, frame: Any) -> Optional[_SectionState]:
        return next((st for st in self._sections.values() if st.frame is frame), None)

    def _add_row(self, frame: Any, label: str, widget_factory: Callable[[Any], Any], name: Optional[str] = None,
                 on: str = "normal") -> _Row:
        """A row in the section ``frame``: ``label`` in column 0, ``widget_factory(frame)`` in column 1. ``name`` puts both
        in that greying group (``fly_model.FlyDialogModel.enabled(name)`` decides; ``on`` is the widget's state when it
        applies, "readonly" for a combo); no name: always usable. Returns the row number, the label and the widget."""
        from tkinter import ttk

        r = self._next(frame)
        lab = ttk.Label(frame, text=label)
        lab.grid(row=r, column=0, sticky="w", padx=4, pady=2)
        widget = widget_factory(frame)
        widget.grid(row=r, column=1, sticky="we", padx=4, pady=2)
        self._register_row(frame, name, ((lab, "normal"), (widget, on)), row=r)
        return _Row(r, lab, widget)

    def _register_row(self, frame: Any, name: Optional[str], widgets: Any, row: Optional[int] = None) -> None:
        """Puts ``widgets`` (``(widget, state when enabled)`` pairs) in greying group ``name``. ``row``: the grid row of
        ``frame`` they sit in; with it the row shows the group's reason in grey (column 3) while it is greyed."""
        st = self._section_of(frame)
        if name:
            if row is not None:
                self._hints.setdefault((name, frame, row), None)       # the label is made the first time it is needed
            for widget, on in widgets:
                self._reg(name, widget, on)
            if st is not None and name not in st.groups:
                st.groups.append(name)
        elif st is not None:
            st.ungated = True

    def _control(self, key: str, var: Any, getter: Optional[Callable[[], Any]] = None, rules: bool = False) -> None:
        """Registers the tk variable ``var`` as the option ``key``: ``values()[key]`` is ``getter()`` (default: the text
        stripped, or the bool); a change saves the options, and with ``rules`` also re-runs the enable rules."""
        if key in self._ctl:
            raise ValueError(f"control {key!r} is registered twice")
        if getter is None:
            def getter() -> Any:
                value = var.get()
                return value.strip() if isinstance(value, str) else value
        self._ctl[key] = (var, getter)
        if rules:
            self._rule_vars[key] = var
        var.trace_add("write", (lambda *_: self._changed()) if rules else (lambda *_: self._schedule_save()))

    def _control_values(self, lenient: bool = False) -> dict:
        """``{key: getter()}`` of every section control. ``lenient`` leaves out one whose getter raises ValueError (the
        autosave: a half-typed number must not stop the rest being saved)."""
        out: dict = {}
        for key, (_var, getter) in self._ctl.items():
            try:
                out[key] = getter()
            except ValueError:
                if not lenient:
                    raise
        return out

    def _text_row(self, frame: Any, key: str, label: str, value: Any, name: Optional[str] = None,
                  getter: Optional[Callable[[], Any]] = None, rules: bool = False, width: int = 34) -> _Row:
        """A labelled text box for option ``key`` starting at ``value``."""
        import tkinter as tk
        from tkinter import ttk

        var = tk.StringVar(value="" if value is None else str(value))
        row = self._add_row(frame, label, lambda p: ttk.Entry(p, textvariable=var, width=width), name)
        self._control(key, var, getter, rules)
        return row

    def _choice_row(self, frame: Any, key: str, label: str, value: Any, choices: Any, name: Optional[str] = None,
                    getter: Optional[Callable[[], Any]] = None, rules: bool = False) -> _Row:
        """A labelled read-only drop-down for option ``key`` (the shown text is the value unless ``getter`` maps it)."""
        import tkinter as tk
        from tkinter import ttk

        var = tk.StringVar(value="" if value is None else str(value))
        row = self._add_row(frame, label, lambda p: ttk.Combobox(p, textvariable=var, values=list(choices),
                                                                   state="readonly", width=31), name, on="readonly")
        self._control(key, var, getter, rules)
        return row

    def _check_row(self, frame: Any, key: str, label: str, value: Any, name: Optional[str] = None,
                   rules: bool = False) -> _Row:
        """A tick box (``label`` is its text, in columns 0 and 1) for the bool option ``key``."""
        import tkinter as tk
        from tkinter import ttk

        var = tk.BooleanVar(value=bool(value))
        r = self._next(frame)
        box = ttk.Checkbutton(frame, text=label, variable=var)
        box.grid(row=r, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        self._register_row(frame, name, ((box, "normal"),), row=r)
        self._control(key, var, lambda: bool(var.get()), rules)
        return _Row(r, None, box)

    def _build_video(self, frame: Any, d: DialogValues) -> None:
        """Video details (FlyThrough.js buildVideo): the ffmpeg path and Browse, Format (only what this ffmpeg can encode),
        Quality, Clear rendered frames, and the render estimate. The logic is in loomsiril.fly_video."""
        import tkinter as tk
        from tkinter import ttk

        from loomsiril import fly_video

        self._vid_saved_format = d.format if isinstance(d.format, str) else ""   # kept while no ffmpeg can say what is offered
        self._vid_touched = bool(self._vid_saved_format)           # a format the user chose (or saved) is not replaced by the default
        self._vid_ids: dict[str, str] = {}                         # format label -> id, for the formats offered now
        self._vid_probe: Any = None                                # the FfmpegProbe of the ffmpeg in use (None: not known yet)
        self._ffmpeg_found = ""                                    # the path discovery found
        self._ms_per_mp = d.ms_per_mp if isinstance(d.ms_per_mp, (int, float)) and d.ms_per_mp > 0 else None
        self._vid_note = tk.StringVar(value="")
        self._clear_note = tk.StringVar(value="")
        self._estimate = tk.StringVar(value="")
        row = self._text_row(frame, "ffmpeg_path", "ffmpeg", d.ffmpeg_path or "", rules=True)
        self._ffmpeg_browse = ttk.Button(frame, text="Browse...", command=self._browse_ffmpeg)
        self._ffmpeg_browse.grid(row=row.row, column=2, sticky="w", padx=4, pady=2)
        self._note_row(frame, self._vid_note)
        self._fmt_var = tk.StringVar(value="")
        fmt = self._add_row(frame, "Format", lambda p: ttk.Combobox(p, textvariable=self._fmt_var, values=[], state="readonly",
                                                                       width=31), name="encode", on="readonly")
        self._fmt_box = fmt.widget
        self._fmt_box.bind("<<ComboboxSelected>>", lambda _e=None: setattr(self, "_vid_touched", True))
        self._control("format", self._fmt_var, self._format_id)
        labels = dict(fly_video.QUALITIES)
        self._quality_var = tk.StringVar(value=labels[fly_video.quality_id(d.quality)])
        qrow = self._add_row(frame, "Quality", lambda p: ttk.Combobox(p, textvariable=self._quality_var, values=list(labels.values()),
                                                                 state="readonly", width=31), name="encode", on="readonly")
        self._control("quality", self._quality_var, lambda: fly_video.quality_id(self._quality_var.get()))
        self._clear_btn = ttk.Button(frame, text="Clear rendered frames...", command=self._clear_frames_click)
        r = self._next(frame)
        self._clear_btn.grid(row=r, column=1, sticky="w", padx=4, pady=2)
        self._note_row(frame, self._clear_note)
        self._note_row(frame, self._estimate)
        for name in ("dynamic", "duration", "fps", "loop", "orientation"):    # what the formats and the estimate follow
            self._vars[name].trace_add("write", lambda *_: self._video_changed())
        for var in self._presets.values():
            var.trace_add("write", lambda *_: self._video_changed())
        self._vars["out_dir"].trace_add("write", lambda *_: self._count_frames_soon())
        self._ctl["ffmpeg_path"][0].trace_add("write", lambda *_: self._video_probe_soon(400))
        from loomsiril.fly_tooltip import Tooltip

        for widgets, tip in (
                ((row.widget, row.label, self._ffmpeg_browse),
                 "The ffmpeg program that encodes the video. Empty: the one found on this machine. Browse... to choose "
                 "another; it is checked by running it with -version."),
                ((fmt.widget, fmt.label),
                 "The video format. Only the formats this ffmpeg can encode are listed; H.264 is SDR only, so an HDR "
                 "video is offered HEVC, ProRes or VP9."),
                ((qrow.widget, qrow.label),
                 "High keeps more detail and makes a larger file; Standard is smaller and quicker to encode."),
                ((self._clear_btn,),
                 "Deletes the frames kept in the output folder's preset folders, so the next render draws every frame "
                 "afresh instead of reusing or resuming them. The videos are kept.")):
            for w in widgets:
                Tooltip(w, tip)
        self._video_changed()
        self._count_frames_soon(0)

    def _note_row(self, frame: Any, var: Any) -> None:
        """A grey line in column 1 of a section."""
        from tkinter import ttk

        ttk.Label(frame, textvariable=var, foreground=GREY, wraplength=320, justify="left").grid(
            row=self._next(frame), column=1, columnspan=2, sticky="w", padx=4)

    def _format_id(self) -> Optional[str]:
        """The Format combo's value as an id: the one shown, else the saved one (None for none): an ffmpeg that is not
        known yet (or missing) must not erase the remembered format."""
        shown = self._vid_ids.get(self._fmt_var.get())
        return shown or self._vid_saved_format or None

    def _ffmpeg_in_use(self) -> str:
        """The ffmpeg the video uses: the typed path, else the one found on this machine ("": none)."""
        typed = str(self._ctl["ffmpeg_path"][1]()).strip()
        return os.path.expanduser(typed) if typed else self._ffmpeg_found

    def _video_changed(self) -> None:
        """A choice the formats or the estimate follow changed (Tk thread, no disk): list the formats, redo the estimate."""
        self._refresh_formats()
        self._refresh_estimate()

    def _chosen_presets(self) -> list:
        from loomsiril import presets

        return [pid for pid in presets.PRESET_ORDER if self._presets[pid].get()]

    def _refresh_formats(self) -> None:
        """FlyThrough.refreshFormats: the formats this ffmpeg can encode (H.264 not for HDR); the one chosen stays while it
        is offered, else the default (HEVC for a 4K-wide video, else H.264) until the user chooses."""
        from loomsiril import fly_video

        formats = fly_video.formats_for(self._vid_probe, self._vars["dynamic"].get() == "hdr")
        turn = "vertical" if fly_model.orientation_id(self._vars["orientation"].get()) == "portrait" else "horizontal"
        width = fly_video.widest_side(self._chosen_presets(), turn)
        wanted = (self._format_id() or "") if self._vid_touched else ""
        chosen = fly_video.choose_format(formats, wanted, width)
        self._vid_ids = {f["label"]: f["id"] for f in formats}
        self._fmt_box.configure(values=[f["label"] for f in formats])
        self._fmt_var.set(fly_video.format_label(chosen) if chosen else "")
        if chosen and self._vid_touched:
            self._vid_saved_format = chosen

    def _refresh_estimate(self) -> None:
        """FlyThrough.refreshEstimate: "Estimated render: about N min" once a render speed is known (the last render's)."""
        from loomsiril import fly_video, presets

        try:
            duration, fps = float(self._vars["duration"].get()), float(self._vars["fps"].get())
        except ValueError:
            self._estimate.set("")
            return
        loop = {label: lid for lid, label in presets.LOOPS}.get(self._vars["loop"].get(), "none")
        turn = "vertical" if fly_model.orientation_id(self._vars["orientation"].get()) == "portrait" else "horizontal"
        self._estimate.set(fly_video.estimate_text(self._ms_per_mp, self._chosen_presets(), duration, fps,
                                                   self._vars["dynamic"].get() == "hdr", turn, loop))

    def _video_probe_soon(self, ms: int = 0) -> None:
        """Look at the ffmpeg in use (its -version and -encoders) on a worker, ``ms`` after the last change."""
        if self._closed:
            return
        self._cancel_after("probe")
        self._afters["probe"] = self.top.after(ms, self._video_probe)

    def _video_probe(self) -> None:
        from loomsiril import fly_video

        self._afters.pop("probe", None)
        path = self._ffmpeg_in_use()
        if not path:
            self._on_probe(None, path)
            return
        self._background(lambda: fly_video.probe(path), lambda r: self._on_probe(r, path), "probe")

    def _on_probe(self, result: Any, path: str) -> None:
        """The probe's answer (Tk thread). One for an ffmpeg no longer in use is dropped. A typed ffmpeg that works when none
        was found ticks Encode video, as PixInsight's Browse does."""
        import sys

        from loomsiril import encode, fly_video

        if self._closed or path != self._ffmpeg_in_use():
            return
        if isinstance(result, Exception):
            result = None
        self._vid_probe = result
        typed = str(self._ctl["ffmpeg_path"][1]()).strip()
        if typed and result is not None:
            self._checks[("ffmpeg", typed)] = "" if result.ok else f"ffmpeg: {result.reason}"
        if result is not None and result.ok:
            offered = ", ".join(f["label"] for f in fly_video.formats_for(result))
            self._vid_note.set(f"{result.version}: " + (offered or "cannot encode any video format"))
        elif result is not None:
            self._vid_note.set(result.reason)
        else:
            self._vid_note.set(encode.ffmpeg_install_hint(encode.platform_name(sys.platform)) if self._ffmpeg is False else "")
        if typed and result is not None and result.ok and self._ffmpeg is False and not self._video.get():
            self._video.set(True)
        self._video_changed()
        self._sync()

    def _browse_ffmpeg(self) -> None:
        """FlyThrough.browseFfmpeg: pick the ffmpeg binary; the worker checks that it runs (a bad one is reported, not taken)."""
        from tkinter import filedialog

        current = str(self._ctl["ffmpeg_path"][1]()).strip()
        chosen = filedialog.askopenfilename(parent=self.top, title="ffmpeg", filetypes=[("ffmpeg", "ffmpeg ffmpeg.exe"),
                                                                                         ("All files", "*")],
                                            initialdir=self._last_dir or os.path.dirname(current) or None)
        if chosen:
            self._ctl["ffmpeg_path"][0].set(chosen)
            self._last_dir = os.path.dirname(chosen)

    def _count_frames_soon(self, ms: int = 600) -> None:
        """Count Loom's frames in the output folder (on a worker) for the grey line under Clear rendered frames."""
        if self._closed:
            return
        self._cancel_after("frames")
        self._afters["frames"] = self.top.after(ms, self._count_frames)

    def _count_frames(self) -> None:
        from loomsiril import fly_video

        self._afters.pop("frames", None)
        out = str(self._vars["out_dir"].get()).strip()
        self._background(lambda: fly_video.clear_frames(out, True), lambda r: self._on_counted(r, out), "frames")

    def _on_counted(self, counts: Any, out: str) -> None:
        from loomsiril import fly_video

        if self._closed or out != str(self._vars["out_dir"].get()).strip():
            return
        if isinstance(counts, Exception) or not counts["folders"]:
            self._clear_note.set("" if isinstance(counts, Exception) else "No rendered frames in the output folder")
            return
        self._clear_note.set(fly_video.counted_text(counts))

    def _clear_frames_click(self) -> None:
        """Clear rendered frames...: count (worker), ask, delete (worker); only Loom's own frames and records go."""
        from loomsiril import fly_video

        out = str(self._vars["out_dir"].get()).strip()
        self._clear_btn.configure(state="disabled")
        self._clear_note.set("Counting the frames...")
        self._background(lambda: fly_video.clear_frames(out, True), lambda r: self._clear_asked(r, out), "clear-count")

    def _clear_asked(self, counts: Any, out: str) -> None:
        from tkinter import messagebox

        from loomsiril import fly_video

        if self._closed:
            return
        self._clear_btn.configure(state="normal")
        if isinstance(counts, Exception):
            self._clear_note.set(f"Could not look at {out}: {counts}")
            return
        if not counts["folders"]:
            self._clear_note.set(fly_video.nothing_text(out))
            return
        if not messagebox.askyesno("Loom Fly-Through", fly_video.confirm_text(counts, out), parent=self.top):
            self._clear_note.set(fly_video.counted_text(counts))
            return
        self._clear_btn.configure(state="disabled")
        self._background(lambda: fly_video.clear_frames(out), lambda r: self._cleared(r, out), "clear")

    def _cleared(self, done: Any, out: str) -> None:
        from loomsiril import fly_video

        if self._closed:
            return
        self._clear_btn.configure(state="normal")
        self._clear_note.set(f"Could not clear the frames: {done}" if isinstance(done, Exception) else fly_video.cleared_text(done))

    def _build_logo(self, frame: Any, d: DialogValues) -> None:
        """Logo (FlyThrough.js 1399-1426): the file with Browse..., Placement (Off, Center, the corners and the top and
        bottom middles; default Off), Opacity (a 0-100 % slider; default 100) and Fade in after (s) (0-600; default 0).
        Placement, Opacity and Fade are greyed with their reason until a logo file is set (Opacity and Fade also while the
        placement is Off). The file is opened on the validation worker (fly_checks), never here; a remembered file that is
        gone keeps its box, says why under it and stops Render."""
        import tkinter as tk
        from tkinter import ttk

        from loomsiril import logo

        row = self._text_row(frame, "logo_path", "Logo file", d.logo_path, rules=True)          # always usable
        browse = ttk.Button(frame, text="Browse...", command=self._browse_logo)
        browse.grid(row=row.row, column=2, padx=4)
        self._logo_note = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._logo_note, wraplength=240, foreground=GREY).grid(
            row=self._next(frame), column=1, columnspan=2, sticky="w", padx=4)
        prow = self._choice_row(frame, "logo_place", "Placement", logo.place_label(str(d.logo_place)), logo.LOGO_PLACE_LABELS,
                                name="logo", getter=lambda: logo.place_id(self._ctl["logo_place"][0].get()), rules=True)
        try:
            percent = max(0.0, min(100.0, float(d.logo_opacity)))
        except (TypeError, ValueError):
            percent = 100.0
        opacity = tk.DoubleVar(value=percent)
        shown = tk.StringVar(value=f"{round(percent)}%")
        orow = self._add_row(frame, "Opacity", lambda p: ttk.Scale(p, from_=0, to=100, variable=opacity), name="logo_look")
        pct = ttk.Label(frame, textvariable=shown)
        pct.grid(row=orow.row, column=2, padx=4)
        self._reg("logo_look", pct)
        opacity.trace_add("write", lambda *_: shown.set(f"{round(max(0.0, min(100.0, float(opacity.get()))))}%"))
        self._control("logo_opacity", opacity, lambda: int(round(max(0.0, min(100.0, float(opacity.get()))))))
        delay = tk.StringVar(value=str(d.logo_delay if d.logo_delay not in (None, "") else 0))
        drow = self._add_row(frame, "Fade in after (s)", lambda p: ttk.Spinbox(p, from_=0, to=600, increment=1, textvariable=delay, width=8),
                             name="logo_look")
        from loomsiril.fly_tooltip import Tooltip

        for widgets, tip in (                                     # FlyThrough.js 1400, 1418, 1425, adapted
                ((row.widget, row.label, browse), "A logo drawn on every frame (PNG transparency is kept; PNG, TIFF and FITS "
                                                  "are read), sized and spaced for each preset."),
                ((prow.widget, prow.label), "Where the logo goes: Off, the centre, a corner, or the middle of the top or bottom edge."),
                ((orow.widget, orow.label, pct), "The logo's opacity; its own transparency is kept."),
                ((drow.widget, drow.label), "Fade the logo in after this many seconds (over one second). 0 shows it from the start.")):
            for w in widgets:
                Tooltip(w, tip)

        def seconds() -> int:
            text = delay.get().strip()
            if not text.isdigit() or int(text) > 600:
                raise ValueError("Fade in after must be a whole number of seconds from 0 to 600.")
            return int(text)

        self._control("logo_delay", delay, seconds)

    def _browse_logo(self) -> None:
        """The logo's Browse...: a picker for the pictures the engine reads. A file chosen while the placement is Off puts the
        logo at the bottom right (FlyThrough.useLogo): choosing a file means wanting it drawn."""
        from loomsiril import logo

        path, place = self._ctl["logo_path"][0], self._ctl["logo_place"][0]
        before = str(path.get())
        self._browse(path, "Loom Fly-Through: Logo", LOGO_FILETYPES)
        if str(path.get()).strip() and str(path.get()) != before and logo.place_id(place.get()) == "off":
            place.set(logo.place_label("bottom-right"))

    def _build_motion(self, frame: Any, d: DialogValues) -> None:
        """Stars and motion (travel, easing, growth, backdrop, brighten, twinkle, saturation, bloom, motion blur): the rows
        are generated from the table in fly_options (fly_option_rows)."""
        fly_option_rows.build(self, frame, "motion", d)

    def _build_hdr(self, frame: Any, d: DialogValues) -> None:
        """HDR (peak nits, star HDR, per-preset transfer): generated from the table in fly_options."""
        fly_option_rows.build(self, frame, "hdr", d)

    def _model(self, trust_checks: bool = False) -> Any:
        """The model of the widgets now. ``trust_checks``: take every wanted path as fine (a Render or Draft click:
        the worker looks again, so an old "not found" can never block it)."""
        from loomsiril.fly_model import FlyDialogModel

        tool = self._chosen_tool()
        model = FlyDialogModel(
            mode=self._mode.get(), tools="looking" if self._looking else ("found" if self._tools else "none"),
            tool_id=tool.id if tool else "", image_path=str(self._vars["image_path"].get()),
            starless_path=str(self._vars["starless_path"].get()), stars_path=str(self._vars["stars_path"].get()),
            out_dir=str(self._vars["out_dir"].get()), presets=[p for p, v in self._presets.items() if v.get()],
            ffmpeg=self._ffmpeg, video=bool(self._video.get()), music_path=str(self._vars["music_path"].get()),
            checked=self._checks, busy=self._acting, grid_notes=self._grid_notes,
            open_grid=self._open_grid(), file_grids=self._file_grids,
            orientation=fly_model.orientation_id(self._vars["orientation"].get()),
            controls={k: var.get() for k, var in self._rule_vars.items()},
            dynamic=str(self._vars["dynamic"].get()), target_type=str(self._vars["target_type"].get()))
        if trust_checks:
            model.checked = {p: "" for p in model.wanted_paths()}
        return model

    def _show_reasons(self, m: Any) -> None:
        """Every greyed row of a section says why, in grey beside it (column 3): ``m.disabled_reason(group)``; the text is
        cleared and the label removed when the row is enabled. Made the first time a row is greyed."""
        import tkinter as tk
        from tkinter import ttk

        for (group, frame, row), made in list(self._hints.items()):
            try:
                reason = m.disabled_reason(group)
            except KeyError:
                reason = ""
            if made is None and not reason:
                continue
            if made is None:
                var = tk.StringVar(value="")
                label = ttk.Label(frame, textvariable=var, foreground=GREY)
                made = self._hints[(group, frame, row)] = (label, var)
            label, var = made
            var.set(reason)
            if reason:
                label.grid(row=row, column=3, sticky="w", padx=4)
            else:
                label.grid_remove()

    def _sync(self) -> None:
        """Applies the model's decisions to the widgets: called after every change."""
        m = self._model()
        for group, widgets in self._w.items():                    # every registered group (see fly_model.GROUPS)
            on = m.enabled(group)
            for widget, normal in widgets:
                widget.configure(state=normal if on else "disabled")
        self._show_reasons(m)
        for st in self._sections.values():                        # all of a section's controls grey: the header says why
            grey = bool(st.groups) and not st.ungated and not any(m.enabled(g) for g in st.groups)
            st.reason.set(next((m.disabled_reason(g) for g in st.groups if m.disabled_reason(g)), "") if grey else "")
        self._radio.configure(state="normal" if m.enabled("extract_radio") else "disabled")
        if not m.enabled("video") and self._video.get():
            self._video.set(False)
        self._video_note.set(m.video_note())
        self._music_note.set(m.music_note())
        self._logo_note.set(m.logo_note())
        self._loop_note.set(m.loop_note())
        fly_option_rows.update_notes(self)
        self._follow_image(m)
        self._image_note.set(m.image_note())
        reasons = m.reasons()
        self._reason.set("Preparing..." if self._acting else (reasons[0] if reasons else ""))
        for button in (self._render_btn, self._draft_btn):
            if button is not None:
                button.configure(state="normal" if m.can_render() else "disabled")

    def _orientation_chosen(self) -> None:
        """The Orientation combo changed: a change the user made is kept until the image changes."""
        if not self._orientation_setting:
            self._orientation_touched = True

    def _follow_image(self, m: Any) -> None:
        """Orientation follows the image (a portrait one is Vertical, else Horizontal) until the user chooses; a new image
        orientation (another Image file, or its header just read) takes the choice back. The labels show the frames."""
        natural = m.image_orientation()
        if natural != self._natural:
            self._natural = natural
            self._orientation_touched = False
        if not self._orientation_touched:
            label = fly_model.orientation_label(natural)
            if self._vars["orientation"].get() != label:
                self._orientation_setting = True
                try:
                    self._vars["orientation"].set(label)
                finally:
                    self._orientation_setting = False
            m.orientation = natural
        self._orientation_note.set(m.orientation_note())
        for pid, box in self._preset_boxes.items():
            box.configure(text=fly_model.preset_label(pid, m.orientation))

    # background work and pending callbacks ---------------------------------------------------------------------------

    def _changed(self) -> None:
        """Any variable changed: grey at once; the file checks follow 250 ms after the last change, on a worker."""
        self._sync()
        if not self._closed:
            self._cancel_after("debounce")
            self._afters["debounce"] = self.top.after(250, self._run_validation)
            self._schedule_save()

    def _snapshot(self) -> dict:
        """What is remembered now: every valid option plus the raw path boxes (as typed, not checked: a missing file stays
        in its box), the mode and the Browse... folder. Taken on the Tk thread."""
        from loomsiril import presets

        snap: dict = {}
        try:
            snap.update(self.values(presets))
        except Exception:                                     # a half-typed number: the paths are still kept
            pass
        for name in ("image_path", "starless_path", "stars_path", "music_path"):
            snap[name] = str(self._vars[name].get()).strip()
        snap["music_fade"] = bool(self._fade.get())
        snap.update(self._control_values(lenient=True))
        snap["sections"] = [sid for sid, _title in SECTIONS if self._sections[sid].open]
        snap["mode"] = self._mode.get()
        snap["last_folder"] = self._last_dir
        snap["starnet_licence"] = self._licence
        return {k: v for k, v in snap.items() if not (k == "starnet_licence" and v is None)}

    def _save_now(self, options: dict) -> None:
        """Writes ``options`` through ``persist``; a save older than one already written is dropped (the final save on
        close wins over a slow autosave). A failed write never reaches the user: the options are a convenience."""
        if self._persist is None:
            return
        self._save_seq += 1
        seq = self._save_seq
        self._write(options, seq)

    def _write(self, options: dict, seq: int) -> None:
        with self._save_lock:
            if seq < self._saved_seq:
                return
            self._saved_seq = seq
            try:
                self._persist(options)
            except Exception:
                pass

    def _schedule_save(self) -> None:
        """Saves the options 500 ms after the last change, on a worker thread (never the Tk thread), so a remembered
        file survives a window that is not closed by Render or Cancel."""
        if self._persist is None or self._closed:
            return
        self._cancel_after("save")

        def fire() -> None:
            self._afters.pop("save", None)
            if self._closed:
                return
            options = self._snapshot()
            self._save_seq += 1
            seq = self._save_seq
            threading.Thread(target=lambda: self._write(options, seq), name="loomfly-save", daemon=True).start()

        self._afters["save"] = self.top.after(500, fire)

    def _recheck(self) -> None:
        """Forgets the "not found" results (a file may have appeared since) and asks the worker again, debounced."""
        for key in [k for k, v in self._checks.items() if v]:
            del self._checks[key]
        self._changed()

    def _cancel_after(self, name: str) -> None:
        after_id = self._afters.pop(name, None)
        if after_id is not None:
            try:
                self.top.after_cancel(after_id)
            except Exception:
                pass

    def _on_destroy(self, event: Any = None) -> None:
        if event is None or getattr(event, "widget", self.top) is self.top:
            self._closed = True
            if self._dist_cancel is not None:                 # a distance lookup still running stops with its window
                self._dist_cancel.set()
            for name in list(self._afters):
                self._cancel_after(name)
            for run in self._draft_runs:                      # a draft still running stops with its window
                try:
                    run.close()
                except Exception:
                    pass
            self._draft_runs.clear()

    def _close(self) -> None:
        """Cancels every pending callback and destroys the window."""
        if self._persist is not None and not self._closed:
            self._cancel_after("save")
            self._save_now(self._snapshot())                  # the final save, in order: it is the last word
        self._on_destroy()
        self.top.destroy()

    def _background(self, work: Callable[[], Any], done: Callable[[Any], None], name: str = "job") -> dict:
        """``work`` on a worker thread; ``done(result)`` on the Tk thread (the exception when ``work`` raised). The Tk
        thread only polls a queue, and the poll is cancelled when the window goes."""
        box: "queue.Queue[Any]" = queue.Queue()

        def target() -> None:
            try:
                box.put(work())
            except Exception as exc:
                box.put(exc)

        self._seq += 1
        key = f"{name}-{self._seq}"
        thread = threading.Thread(target=target, name=f"loomsiril-{name}", daemon=True)
        job: dict = {"thread": thread}

        def poll() -> None:
            self._afters.pop(key, None)
            if self._closed:
                return
            try:
                got = box.get_nowait()
            except queue.Empty:
                self._afters[key] = self.top.after(100, poll)
                return
            if job in self._jobs:
                self._jobs.remove(job)
            done(got)

        job["poll"] = poll
        self._jobs.append(job)
        thread.start()
        self._afters[key] = self.top.after(100, poll)
        return job

    def _run_validation(self) -> None:
        """Looks at the paths the current mode needs that have no result yet (on a worker thread)."""
        from loomsiril import fly_checks

        self._afters.pop("debounce", None)
        todo = [p for p in self._model().wanted_paths() if p not in self._checks]
        if todo and not self._closed:
            grid = self._open_grid()
            self._background(lambda: self._check_paths(todo, grid), lambda r: self._apply_checks(r, todo), "validate")

    def _open_grid(self) -> Any:
        """The open image's (width, height, channels), None when the script did not say."""
        return getattr(self._names, "open_grid", None)

    @staticmethod
    def _check_paths(todo: list, grid: Any) -> tuple[dict, dict]:
        """On the worker: the file checks, and the grey size note of each Image file (its header is read here)."""
        from loomsiril import fly_checks, fly_grid

        results = fly_checks.validate(todo) if grid is None else fly_checks.validate(todo, grid)
        notes, grids = {}, {}
        for kind, path in todo:
            if kind == "image" and not fly_checks.check_file(kind, path):
                if grid is not None:
                    notes[path] = fly_grid.grid_line(path, grid)[1]
                try:
                    grids[path] = fly_grid.file_grid(path)
                except Exception:                                 # unreadable header: the file check says so
                    pass
            elif kind == "image" and grid is not None:
                notes[path] = ""
        return results, notes, grids

    def _apply_checks(self, result: Any, todo: list) -> None:
        notes: dict = {}
        grids: dict = {}
        if isinstance(result, tuple):
            result, notes, *rest = result
            grids = rest[0] if rest else {}
        if isinstance(result, Exception):
            result = {p: f"Could not check {p[1]}: {result}" for p in todo}
        self._checks.update(result)
        self._grid_notes.update(notes)
        self._file_grids.update(grids)
        self._sync()

    # names, online (worker threads) ----------------------------------------------------------------------------------

    def _names_soon(self) -> None:
        """The Image file changed: ask the names again 600 ms after the last change."""
        if self._names is not None and not self._closed:
            self._cancel_after("names")
            self._afters["names"] = self.top.after(600, self._start_names)

    def _name_sources(self) -> list:
        from loomsiril.fly_model import name_sources

        n = self._names
        return name_sources(str(self._vars["image_path"].get()), getattr(n, "header_object", ""),
                            getattr(n, "open_path", ""))

    def _start_names(self) -> None:
        """Asks CDS Sesame what the Image file's names (and the open image's OBJECT keyword and file) call, on a worker:
        a hit inside the solved field fills an empty Object name. Silent without a network."""
        from loomsiril import skyname

        self._afters.pop("names", None)
        if self._names is None or self._closed:
            return
        sources = self._name_sources()
        key = (tuple(sources), repr(self._names.field))
        if not sources or key == self._named_for:
            return
        self._named_for = key
        fld = self._names.field
        self._background(lambda: skyname.find_named(sources, fld), lambda r: self._on_named(r, key), "names")

    def _on_named(self, result: Any, key: tuple) -> None:
        """The lookup's answer, on the Tk thread. An old answer (the file changed meanwhile) is dropped."""
        from loomsiril.fly_model import OFFLINE_NOTE, found_note

        if self._closed or key != self._named_for:
            return
        if isinstance(result, Exception) or result is None:
            self._name_note.set("")
            return
        if result.hit is None:
            self._name_note.set(OFFLINE_NOTE if result.offline else "")
            return
        self._name_note.set(found_note(result.hit, result.how))
        typed = str(self._vars["object_name"].get()).strip()
        if not typed:                                              # never overwrite what the user typed
            self._vars["object_name"].set(result.hit["name"] or result.hit["id"])
        if not typed or _same_name(typed, result.hit):
            # The Object name is this very object (the box is empty, or holds what an earlier run filled in and the
            # options remembered): it is the target the distance is looked up round, as in Loom. A different typed
            # name keeps its own target (the Find button sets that one).
            self._target_from(result.hit, force=False)

    def _target_from(self, hit: dict, force: bool) -> None:
        """The found object becomes the target (RA, Dec: its distance is looked up round it, as in Loom). Boxes the user
        has edited are left alone unless ``force`` (the Find button is an explicit ask)."""
        for name, value, initial in (("ra", hit["ra"], self._initial_target[0]),
                                     ("dec", hit["dec"], self._initial_target[1])):
            var = self._vars[name]
            if force or str(var.get()).strip() in ("", str(initial).strip()):
                var.set(f"{value:.4f}")

    def _find_typed(self) -> None:
        """The Object name box, Enter or Find: the name is asked of CDS Sesame on a worker; it must lie inside the
        solved field, else it is refused with the reason."""
        from loomsiril import skyname

        query = str(self._vars["object_name"].get()).strip()
        if not query:
            self._name_note.set("")
            return
        if self._names is None or self._names.field is None:
            self._name_note.set("Name lookup needs a plate-solved image")
            return
        self._typed_seq += 1
        seq, fld = self._typed_seq, self._names.field
        self._name_note.set(f"Looking up {query}...")
        self._background(lambda: skyname.check_typed(query, fld), lambda r: self._on_typed(r, query, seq), "find")

    def _on_typed(self, result: Any, query: str, seq: int) -> None:
        from loomsiril.fly_model import OFFLINE_NOTE, found_note, outside_note

        if self._closed or seq != self._typed_seq:
            return
        if isinstance(result, Exception) or result is None or result.offline:
            self._name_note.set(OFFLINE_NOTE)
        elif result.hit is not None:
            self._name_note.set(found_note(result.hit, "the Object box"))
            self._target_from(result.hit, force=True)
        elif result.outside:
            self._name_note.set(outside_note(result.outside[0][0], result.outside[0][1], self._names.field["radius_deg"]))
        else:
            self._name_note.set(f"{query}: not found")

    # the distance, as the render will find it (worker thread) --------------------------------------------------------

    def _distance_key(self) -> tuple:
        """What the distance depends on, from the boxes now: RA, Dec, Target type, Gaia source and the typed distance."""
        return tuple(str(self._vars[n].get()).strip() for n in ("ra", "dec", "target_type", "gaia", "distance"))

    def _distance_soon(self) -> None:
        """A box the distance depends on changed: look again 400 ms after the last change (the old answer is stale now)."""
        if self._closed:
            return
        if self._dist_asked == self._distance_key():              # written again with the same text (Tk fires the trace
            return                                                # for that too): the answer, or the lookup, still stands
        self._dist_seq += 1                                       # an answer in flight is dropped when it lands
        self._dist_result = None
        if self._dist_cancel is not None:
            self._dist_cancel.set()
        self._cancel_after("distance")
        self._afters["distance"] = self.top.after(400, self._start_distance)

    def _start_distance(self) -> None:
        """Finds the distance of the target now: a galaxy or a typed value at once, else Gaia on a worker thread."""
        from loomsiril import fly_distance
        from loomsiril.fly_model import FINDING_DISTANCE

        self._afters.pop("distance", None)
        if self._closed:
            return
        self._dist_seq += 1
        seq, key = self._dist_seq, self._distance_key()
        self._dist_asked = key
        if self._dist_cancel is not None:
            self._dist_cancel.set()
        ra, dec, kind, gaia_source, typed = key
        quick = fly_distance.immediate(kind, typed)
        if quick is not None:
            self._apply_distance(quick, seq, key)
            return
        self._dist_result = None
        self._distance_note.set(FINDING_DISTANCE)
        self._distance_hint.set("auto")
        self._show_hint()
        try:
            target = {"ra": float(ra), "dec": float(dec)}
        except ValueError:
            target = None
        fld = getattr(self._names, "field", None)
        radius = fld["radius_deg"] if fld else None
        cancel = self._dist_cancel = threading.Event()
        self._background(
            lambda: fly_distance.resolve(target, kind, gaia_source, typed, radius, is_cancelled=cancel.is_set),
            lambda r: self._apply_distance(r, seq, key), "distance")

    def _apply_distance(self, result: Any, seq: int, key: tuple) -> None:
        """The lookup's answer, on the Tk thread: the grey line and the placeholder. A stale answer is dropped."""
        from loomsiril import fly_distance
        from loomsiril.fly_model import distance_hint, distance_line

        if self._closed or seq != self._dist_seq:
            return
        if isinstance(result, Exception):
            result = fly_distance.DistanceResult(None, reason=f"the lookup failed ({result})")
        self._dist_result, self._dist_key = result, key
        self._distance_note.set(distance_line(result))
        self._distance_hint.set(distance_hint(result))
        self._show_hint()

    def _show_hint(self) -> None:
        """The placeholder shows only while the Distance box is empty."""
        hint = getattr(self, "_hint_label", None)
        if hint is None:
            return
        try:
            if str(self._vars["distance"].get()).strip():
                hint.place_forget()
            else:
                hint.place(x=5, rely=0.5, anchor="w")
        except Exception:
            pass

    def _placeholder(self, entry: Any) -> None:
        """A grey label laid over the empty Distance box (a ttk.Entry has no placeholder; the box itself stays empty)."""
        from tkinter import ttk

        label = ttk.Label(entry, textvariable=self._distance_hint, foreground=GREY)
        try:
            bg = ttk.Style().lookup("TEntry", "fieldbackground")
            if bg:
                label.configure(background=bg)
        except Exception:
            pass
        label.bind("<Button-1>", lambda _e: entry.focus_set())
        self._hint_label = label
        self._show_hint()

    def _found_distance(self, typed: Optional[float]) -> dict:
        """What the Draft and Render are told: the distance the lookup found and showed, so the render does not find
        another. Nothing when one was typed (the typed value is the job's), or when the answer is not for these boxes."""
        res = self._dist_result
        if typed is not None or res is None or not getattr(res, "found_by_lookup", False) \
                or self._dist_key != self._distance_key():
            return {}
        return {"found_distance": float(res.distance), "found_source": res.code, "found_label": res.source}

    # tool discovery (worker thread) ---------------------------------------------------------------------------------

    def _start_discovery(self, discover: Optional[Callable[[], Any]]) -> None:
        """Runs ``discover`` on a worker thread; the Tk thread only polls (loom/dialog.py's _background idea)."""
        if discover is None:
            self._on_discovered(None)
            return
        self._background(discover, self._on_discovered, "discover")

    def _on_discovered(self, found: Any) -> None:
        """The discovery result (or the exception it raised, or None): fill the drop-down, or disable the option."""
        from loomsiril import fly_tools

        self._busy.stop()
        self._busy.grid_remove()
        if isinstance(found, Exception):
            found = fly_tools.Discovery(notes=[f"Looking for tools failed: {found}"])
        found = found or fly_tools.Discovery()
        self._looking = False
        self._ffmpeg = found.ffmpeg
        self._ffmpeg_found = getattr(found, "ffmpeg_path", "") or ""
        self._video_probe_soon()
        self._tools = list(found.tools)
        tool_id, note = fly_tools.choose(self._tools, self._saved_tool)
        extra = " ".join(found.notes)
        if tool_id is None:
            self._tool_box.configure(values=[])
            self._tool.set("")
            self._mode.set("files")
        else:
            self._tool_box.configure(values=[t.label for t in self._tools])
            self._tool.set(next(t.label for t in self._tools if t.id == tool_id))
            if not self._mode_touched:
                self._mode.set("extract")
            elif self._saved_mode == "extract":
                self._mode.set("extract")
        self._status.set(" ".join(x for x in (note, extra) if x))
        self._sync()

    def _touch_mode(self) -> None:
        self._mode_touched = True
        self._saved_mode = None                                   # the user's click replaces the remembered mode

    def _chosen_tool(self) -> Any:
        """The ExtractTool the drop-down shows, or None."""
        return next((t for t in self._tools if t.label == self._tool.get()), None)

    # form rows ------------------------------------------------------------------------------------------------------

    def _field(self, field: "_Field") -> None:
        if field.choices is None:
            self._entry(field, self._next())
        else:
            self._combo(field, self._next())

    def _row(self, r: int, label: str, widget: Any) -> Any:
        """One grid row of the form: the label in column 0, the widget in column 1. Returns the label."""
        from tkinter import ttk

        lab = ttk.Label(self._frame, text=label)
        lab.grid(row=r, column=0, sticky="w", padx=4, pady=2)
        widget.grid(row=r, column=1, sticky="we", padx=4, pady=2)
        return lab

    def _entry(self, field: "_Field", r: int) -> None:
        """A text box for the variable ``field.name``, starting at ``field.value`` (None shows an empty box).

        The image, starless and stars boxes get a Browse... file picker and the output folder a folder picker, in
        column 2; the three file rows are greyed with their mode's group."""
        import tkinter as tk
        from tkinter import ttk

        var = tk.StringVar(value="" if field.value is None else str(field.value))
        self._vars[field.name] = var
        entry = ttk.Entry(self._frame, textvariable=var, width=34)
        entry.bind("<FocusIn>", lambda _e: self._recheck())
        label = self._row(r, field.label, entry)
        if field.name == "distance":
            self._placeholder(entry)
        if field.name == "object_name":
            entry.bind("<Return>", lambda _e: self._find_typed())
            ttk.Button(self._frame, text="Find", command=self._find_typed).grid(row=r, column=2, padx=4)
            return
        group = FILE_PICKERS.get(field.name)
        if group is not None:
            types = AUDIO_FILETYPES if field.name == "music_path" else LAYER_FILETYPES
            command = lambda: self._browse(var, field.label, types)  # noqa: E731
        elif field.name == "out_dir":
            command = lambda: self._browse_folder(var, field.label)  # noqa: E731
        else:
            return
        button = ttk.Button(self._frame, text="Browse...", command=command)
        button.grid(row=r, column=2, padx=4)
        if group is not None:
            for w in (label, entry, button):
                self._reg(group, w)

    def _browse(self, var: Any, title: str, filetypes: Any = None) -> None:
        """A file picker (TIFF/FITS, or ``filetypes``) that fills ``var``; it starts in the folder last used by any picker."""
        from tkinter import filedialog

        current = os.path.dirname(str(var.get()).strip())
        chosen = filedialog.askopenfilename(parent=self.top, title=title, filetypes=filetypes or LAYER_FILETYPES,
                                            initialdir=self._last_dir or current or None)
        if chosen:
            var.set(chosen)
            self._last_dir = os.path.dirname(chosen)
            self._recheck()

    def _browse_folder(self, var: Any, title: str) -> None:
        """Loom's folder picker (loom/dialog.ask_folder), starting in the current value; it fills ``var``."""
        from loomsiril.loom import dialog as loom_dialog

        current = str(var.get()).strip()          # the picker itself falls back when the folder is not there
        chosen = loom_dialog.ask_folder(title, current or self._last_dir, parent=self.top)
        if chosen:
            var.set(chosen)
            self._last_dir = chosen
            self._recheck()

    def _combo(self, field: "_Field", r: int) -> None:
        """A read-only drop-down for the variable ``field.name``, showing ``field.value`` first."""
        import tkinter as tk
        from tkinter import ttk

        self._vars[field.name] = var = tk.StringVar(value=str(field.value))
        box = ttk.Combobox(self._frame, textvariable=var, values=field.choices, state="readonly", width=31)
        label = self._row(r, field.label, box)
        if field.name == "loop":
            self._reg("loop", label)
            self._reg("loop", box, "readonly")
            self._loop_note = tk.StringVar(value="")
            ttk.Label(self._frame, textvariable=self._loop_note, foreground=GREY).grid(row=r, column=2, sticky="w")
        if field.name == "orientation":
            self._orientation_note = tk.StringVar(value="")
            ttk.Label(self._frame, textvariable=self._orientation_note, foreground=GREY).grid(row=r, column=2, sticky="w")

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _blocked_click(self, event: Any) -> None:
        """A click on Draft or Render while it is greyed: the reason in a box (the grey line says it too)."""
        widget = event.widget
        try:
            greyed = widget.instate(["disabled"])
        except Exception:
            greyed = False
        if greyed and not self._closed:
            reasons = self._model().reasons()
            self._refuse("Preparing the inputs: one moment." if self._acting else
                         (reasons[0] if reasons else "Not available now."))

    def _report_callback_errors(self) -> None:
        """An exception in any Tk callback of this window is shown in a box and written to Siril's console (Tk's own
        report goes to a stderr that Siril users never see, so a failing button looked like a dead one)."""
        import traceback

        def report(exc: Any, val: Any, tb: Any) -> None:
            traceback.print_exception(exc, val, tb)
            if not self._closed:
                try:
                    self._refuse(f"{getattr(exc, '__name__', 'Error')}: {val}")
                except Exception:
                    pass

        try:
            self.top._root().report_callback_exception = report
        except Exception:
            pass

    def _refuse(self, text: str) -> None:
        from tkinter import messagebox

        messagebox.showerror("Loom Fly-Through", text, parent=self.top)

    def _start_action(self, then: Callable[[dict], None], create_folder: bool) -> None:
        """Render or Draft was clicked: the pure checks now, then the disk work (fresh file checks, the output folder,
        the StarNet licence text) on a worker; ``then(values)`` runs on the Tk thread once it is all fine."""
        from loomsiril import fly_checks, presets, starnet_licence

        if self._acting:
            self._refuse("Still preparing the previous click: one moment.")
            return
        model = self._model(trust_checks=True)
        reasons = model.reasons()
        if reasons:
            self._refuse(reasons[0])
            return
        try:
            values = self.values(presets)
        except ValueError as exc:
            self._refuse(str(exc))
            return
        wanted = model.wanted_paths()
        grid = self._open_grid()
        image = values["image_path"].strip()
        binary = values["star_tool_path"] if (values["mode"] == "extract" and values["star_tool"] == "starnet2"
                                              and self._licence != starnet_licence.STARNET_LICENCE_REV) else ""

        def work() -> dict:
            fresh = fly_checks.validate(wanted) if grid is None else fly_checks.validate(wanted, grid)
            for result in fresh.values():
                if result:
                    return {"error": next(r for r in fresh.values() if r), "checks": fresh}
            if create_folder:
                problem = fly_checks.check_out_dir(values["out_dir"], create=True)
                if problem:
                    return {"error": problem}
            return {"checks": fresh, "stamp": fly_checks.file_stamp(image) if image else None,
                    "licence_text": starnet_licence.display_text(binary) if binary else None, "binary": binary}

        self._acting = True
        self._sync()
        self._background(work, lambda r: self._prepared(r, values, then), "prepare")

    def _prepared(self, result: Any, values: dict, then: Callable[[dict], None]) -> None:
        from loomsiril import starnet_licence

        self._acting = False
        if isinstance(result, Exception):
            result = {"error": f"Could not check the inputs: {result}"}
        self._checks.update(result.get("checks", {}))                  # the fresh answers replace the old ones
        if "error" in result:
            self._sync()
            self._refuse(result["error"])
            return
        values["image_stamp"] = result["stamp"]
        if result["binary"]:
            self._licence = starnet_licence.check(self.top, self._licence, result["binary"], result["licence_text"])
            if self._licence is None:
                self._mode.set("files")
                self._sync()
                return
        values["starnet_licence"] = self._licence
        try:
            then(values)
        except Exception as exc:                  # a Tk callback's exception would only reach a stderr nobody reads
            self._refuse(f"{type(exc).__name__}: {exc}")
        if not self._closed:
            self._sync()

    def _finish_render(self, values: dict) -> None:
        self.result = values
        self._close()

    def _ok(self) -> None:
        self._start_action(self._finish_render, create_folder=True)

    def _draft(self) -> None:
        """The Draft button: a quick look at the clip; this window stays open (the draft's work is on its own thread)."""
        if self._draft_cb is not None:
            self._start_action(self._start_draft, create_folder=False)

    def _start_draft(self, values: dict) -> None:
        run = self._draft_cb(self.top, values)
        if hasattr(run, "close"):
            self._draft_runs.append(run)

    def values(self, presets: Any) -> dict:
        """The chosen values, checked. Raises ValueError with the message to show."""
        v = {k: var.get().strip() for k, var in self._vars.items() if k != "mode"}
        target = None
        if v["ra"] or v["dec"]:
            try:
                ra, dec = float(v["ra"]), float(v["dec"])
            except ValueError as exc:
                raise ValueError("Target RA and Dec must be numbers (degrees).") from exc
            target = {"ra": ra, "dec": dec}
        distance = float(v["distance"]) if v["distance"] else None
        try:
            duration = float(v["duration"])
        except ValueError as exc:
            raise ValueError("Duration must be a number of seconds.") from exc
        if duration <= 0:
            raise ValueError("Duration must be positive.")
        if not v["out_dir"]:
            raise ValueError("Choose an output folder.")
        presets_chosen = [pid for pid in presets.PRESET_ORDER if self._presets[pid].get()]
        if not presets_chosen:
            raise ValueError("Choose at least one preset.")
        loop_id = {label: lid for lid, label in presets.LOOPS}[v["loop"]]
        tool = self._chosen_tool()
        return {
            "mode": self._mode.get(), "starless_path": v["starless_path"], "stars_path": v["stars_path"],
            "star_tool": tool.id if tool else "", "star_tool_path": tool.path if tool else "",
            "image_path": v["image_path"] if self._mode.get() == "extract" else "",
            "target": target, "target_type": v["target_type"], "distance": distance, "duration": duration,
            "fps": int(v["fps"]), "loop": loop_id,
            "orientation": fly_model.orientation_id(v["orientation"]), "star_quality": v["star_quality"], "dynamic": v["dynamic"],
            "gaia": v["gaia"], "object_name": v["object_name"], "out_dir": v["out_dir"],
            "presets": presets_chosen, "video": bool(self._video.get()) and self._model().ffmpeg_ok(),
            "music_path": v["music_path"], "music_fade": bool(self._fade.get()),
            **self._control_values(),
            **self._found_distance(distance),
        }
