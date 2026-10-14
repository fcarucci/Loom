"""Loom Fly-Through for Siril: a thin Siril 1.4 script over loomsiril (E7). Asks for the starless and stars layers (files,
or StarNet2 once its licence is accepted), a target and presets, then renders with loomsiril.pipeline.run. Needs Siril
1.4 (sirilpy), numpy and loomsiril (Loom/README.md). THIS SCRIPT HAS NOT BEEN RUN INSIDE SIRIL: it is tested against a
stand-in sirilpy only. Importing it does nothing.
"""

import os
import queue
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any, Callable, NamedTuple, Optional

from sirilpy import LogColor, SirilInterface, ensure_installed  # noqa: F401  (LogColor kept for the log lines)

#: Optional: a directory that contains the ``loomsiril`` package (a checkout of this repository).
LOOMSIRIL_SRC_ENV = "LOOMSIRIL_SRC"
#: The default preset and the dialog's default values (the pipeline's defaults; FlyJob.presets).
DEFAULT_PRESET = "youtube_1080"


def _import_loomsiril() -> Any:
    """Import loomsiril.siril_bridge: $LOOMSIRIL_SRC first on sys.path when it is set, then this script's checkout."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <root>/Loom/loom_fly_through.py
    checkout = root if os.path.isfile(os.path.join(root, "loomsiril", "__init__.py")) else None
    for src in (checkout, os.environ.get(LOOMSIRIL_SRC_ENV)):
        if src and src not in sys.path:
            sys.path.insert(0, src)
    from loomsiril import siril_bridge  # imported here, not at module level: nothing happens on import

    return siril_bridge


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
    video: Any = False
    starnet_licence: Any = None

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> "DialogValues":
        """The dialog's starting values from a saved (or default) options dict."""
        return cls(**{f.name: options[f.name] for f in fields(cls) if f.name in options})


def run_dialog(defaults: dict, starnet_available: bool, target: Optional[dict],
               siril: Optional[SirilInterface] = None) -> Optional[dict]:
    """Show the options dialog (tkinter, Siril's theme). Returns the chosen values, or None on Cancel."""
    from loomsiril.tk_theme import close_root, themed_root

    root = themed_root(siril, log=getattr(siril, "log", None))
    try:
        dlg = LoomFlyDialog(root, DialogValues.from_options(defaults), starnet_available, target)
        root.wait_window(dlg.top)
        return dlg.result
    finally:
        close_root(root)


def _form_fields(d: DialogValues, target: Optional[dict], presets: Any) -> list["_Field"]:
    """The text boxes and drop-downs of the form, in grid order from row 1 (the mode radio buttons are row 0)."""
    ra, dec = (target or {}).get("ra", ""), (target or {}).get("dec", "")
    loop_labels = [label for _id, label in presets.LOOPS]
    return [
        _Field("starless_path", "Starless file (TIFF/FITS)", d.starless_path, None),
        _Field("stars_path", "Stars file (TIFF/FITS)", d.stars_path, None),
        _Field("ra", "Target RA (deg)", ra if d.ra is _FROM_TARGET else d.ra, None),
        _Field("dec", "Target Dec (deg)", dec if d.dec is _FROM_TARGET else d.dec, None),
        _Field("target_type", "Target type", d.target_type, ["nebula", "galaxy"]),
        _Field("distance", "Distance (pc, blank = auto)", d.distance, None),
        _Field("duration", "Duration (s)", d.duration, None),
        _Field("fps", "Frames per second", d.fps, [str(f) for f in presets.FPS]),
        _Field("loop", "Loop", dict(presets.LOOPS).get(d.loop, loop_labels[0]), loop_labels),
        _Field("star_quality", "Star quality", d.star_quality, presets.STAR_QUALITIES),
        _Field("dynamic", "Dynamic range", d.dynamic, ["sdr", "hdr"]),
        _Field("gaia", "Gaia source", d.gaia, ["cache", "online"]),
        _Field("object_name", "Object name", d.object_name, None),
        _Field("out_dir", "Output folder", d.out_dir, None),
    ]


class _Field(NamedTuple):
    """A form row: the variable ``name``, its ``label``, its starting ``value``, drop-down ``choices`` (None: text box)."""
    name: str
    label: str
    value: Any
    choices: Optional[list]


class LoomFlyDialog:
    """The Fly-Through options window. ``result`` is the dict main() reads, or None when cancelled.

    Built only where tkinter can open a window (the tests skip it otherwise).
    """

    def __init__(self, master: Any, defaults: DialogValues, starnet_available: bool, target: Optional[dict]) -> None:
        import tkinter as tk
        from tkinter import ttk

        from loomsiril import presets

        self.result: Optional[dict] = None
        self.top = tk.Toplevel(master)
        self.top.title("Loom Fly-Through")
        self._vars: dict[str, Any] = {}
        d = defaults
        frame = ttk.Frame(self.top, padding=10)
        frame.grid(sticky="nsew")
        self._frame = frame
        self._licence = defaults.starnet_licence                  # the accepted StarNet licence revision, if any
        self._mode = tk.StringVar(value="starnet" if starnet_available else "files")
        self._vars["mode"] = self._mode
        ttk.Radiobutton(frame, text="Run StarNet on the image", value="starnet", variable=self._mode,
                        state="normal" if starnet_available else "disabled").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(frame, text="Starless and stars files", value="files", variable=self._mode).grid(
            row=0, column=1, sticky="w")
        for r, row in enumerate(_form_fields(d, target, presets), start=1):
            if row.choices is None:
                self._entry(row, r)
            else:
                self._combo(row, r)
        self._presets: dict[str, Any] = {}
        chosen = set(d.presets or [DEFAULT_PRESET])
        box = ttk.Frame(frame)
        box.grid(row=15, column=0, columnspan=2, sticky="w", pady=4)
        for i, pid in enumerate(presets.PRESET_ORDER):
            var = tk.BooleanVar(value=pid in chosen)
            self._presets[pid] = var
            ttk.Checkbutton(box, text=presets.PRESET_LABELS[pid], variable=var).grid(row=i // 2, column=i % 2, sticky="w")
        self._video = tk.BooleanVar(value=bool(d.video))
        ttk.Checkbutton(frame, text="Encode video (ffmpeg)", variable=self._video).grid(row=16, column=0, sticky="w")
        btns = ttk.Frame(frame)
        btns.grid(row=17, column=0, columnspan=2, sticky="e", pady=6)
        ttk.Button(btns, text="Cancel", command=self._cancel).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="Render", command=self._ok).grid(row=0, column=1, padx=4)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

    def _row(self, r: int, label: str, widget: Any) -> None:
        """One grid row of the form: the label in column 0, the widget in column 1."""
        from tkinter import ttk

        ttk.Label(self._frame, text=label).grid(row=r, column=0, sticky="w", padx=4, pady=2)
        widget.grid(row=r, column=1, sticky="we", padx=4, pady=2)

    def _entry(self, field: "_Field", r: int) -> None:
        """A text box for the variable ``field.name``, starting at ``field.value`` (None shows an empty box)."""
        import tkinter as tk
        from tkinter import ttk

        var = tk.StringVar(value="" if field.value is None else str(field.value))
        self._vars[field.name] = var
        self._row(r, field.label, ttk.Entry(self._frame, textvariable=var, width=34))

    def _combo(self, field: "_Field", r: int) -> None:
        """A read-only drop-down for the variable ``field.name``, showing ``field.value`` first."""
        import tkinter as tk
        from tkinter import ttk

        self._vars[field.name] = var = tk.StringVar(value=str(field.value))
        self._row(r, field.label, ttk.Combobox(self._frame, textvariable=var, values=field.choices,
                                               state="readonly", width=31))

    def _cancel(self) -> None:
        self.result = None
        self.top.destroy()

    def _ok(self) -> None:
        from tkinter import messagebox

        from loomsiril import presets, starnet_licence

        try:
            values = self.values(presets)
        except ValueError as exc:
            messagebox.showerror("Loom Fly-Through", str(exc), parent=self.top)
            return
        if values["mode"] == "starnet":
            self._licence = starnet_licence.check(self.top, self._licence, _import_loomsiril().find_starnet())
            if self._licence is None:
                self._mode.set("files")
                return
        values["starnet_licence"] = self._licence
        self.result = values
        self.top.destroy()

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
        return {
            "mode": self._mode.get(), "starless_path": v["starless_path"], "stars_path": v["stars_path"],
            "target": target, "target_type": v["target_type"], "distance": distance, "duration": duration,
            "fps": int(v["fps"]), "loop": loop_id, "star_quality": v["star_quality"], "dynamic": v["dynamic"],
            "gaia": v["gaia"], "object_name": v["object_name"], "out_dir": v["out_dir"],
            "presets": presets_chosen, "video": bool(self._video.get()),
        }


def _layers(values: dict, b: Any, bridge: Any, starnet: Optional[str]) -> dict:
    """The starless and stars planes: from StarNet on the image (licence accepted), or from the two files."""
    if values["mode"] == "starnet":
        if starnet is None:
            raise ValueError("StarNet is not installed: set LOOMSIRIL_STARNET or choose the files.")
        from loomsiril import starnet_licence
        starnet_licence.require(values)
        starless = bridge.run_starnet(starnet, b.planes)
        starless, stars = bridge.split_stars(b.planes, starless)
        return {"starless": starless, "stars": stars}
    if not values["starless_path"] or not values["stars_path"]:
        raise ValueError("Choose the starless and the stars files.")
    starless = bridge.read_layer(values["starless_path"])
    stars = bridge.read_layer(values["stars_path"])
    for name, planes in (("starless", starless), ("stars", stars)):
        if len(planes) != len(b.planes) or planes[0].shape != b.planes[0].shape:
            raise ValueError(f"The {name} file does not match the image ({b.width} x {b.height}, "
                             f"{len(b.planes)} channel(s)).")
    return {"starless": starless, "stars": stars}


class _PipelineWorker:
    """The state of one pipeline.run: its progress is queued here, its result or error is kept in ``box``."""

    def __init__(self, pipeline: Any, job: Any) -> None:
        self._pipeline = pipeline
        self._job = job
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.box: dict = {}

    def progress(self, stage: str, done: int, total: int) -> None:
        """The pipeline's progress callback: queued, drawn later by the thread that talks to Siril."""
        self.events.put((stage, done, total))

    def work(self) -> None:
        """The worker thread's body: run the pipeline (never cancelled from here)."""
        try:
            self.box["result"] = self._pipeline.run(self._job, self.progress, lambda: False)
        except Exception as exc:  # reported by main() on the Siril thread
            self.box["error"] = exc


def _run_pipeline(pipeline: Any, job: Any, siril: SirilInterface) -> Any:
    """pipeline.run in a worker thread; progress is drawn here, on the thread that talks to Siril."""
    worker = _PipelineWorker(pipeline, job)
    thread = threading.Thread(target=worker.work, name="loomsiril-fly", daemon=True)
    thread.start()
    while thread.is_alive():
        _drain(worker.events, siril)
        thread.join(0.2)
    _drain(worker.events, siril)
    if "error" in worker.box:
        raise worker.box["error"]
    return worker.box.get("result")


def _drain(events: "queue.Queue[tuple]", siril: SirilInterface) -> None:
    while True:
        try:
            stage, done, total = events.get_nowait()
        except queue.Empty:
            return
        siril.update_progress(str(stage), min(1.0, done / total) if total else 0.0)


def summary(result: Any) -> str:
    """The result box text: frames, videos, or the ffmpeg command when no encoder was found."""
    lines = [f"Frames written: {getattr(result, 'written', 0)}, reused: {getattr(result, 'reused', 0)}."]
    if getattr(result, "cancelled", False):
        lines.append("Cancelled: the frames so far are kept.")
    videos = list(getattr(result, "videos", []) or [])
    if videos:
        lines.append("Videos:")
        lines.extend(videos)
    commands = list(getattr(result, "commands", []) or [])
    if commands:
        lines.append("ffmpeg was not found. Run this command to encode the video:")
        lines.extend(commands)
    failed = list(getattr(result, "failed", []) or [])
    if failed:
        lines.append(f"{len(failed)} frame(s) failed; see the log.")
    return "\n".join(lines)


def _report(siril: SirilInterface, text: str) -> None:
    """Show an error box on a connected Siril; with no connection, print it to stderr instead."""
    try:
        siril.error_messagebox(text)
    except Exception:  # the box needs a live connection; the message must still get out
        print(text, file=sys.stderr)


def main(dialog: Optional[Callable[..., Optional[dict]]] = None) -> None:
    """Connect, check the image, ask, bridge, render, report. ``dialog`` is replaceable (tests)."""
    ask = dialog if dialog is not None else run_dialog
    siril = SirilInterface()
    try:
        connected = siril.connect()   # the real connect() raises SirilConnectionError; the stub may return False
    except Exception as exc:
        print(f"Loom Fly-Through: cannot connect to Siril ({exc})", file=sys.stderr)
        return
    if not connected:
        print("Loom Fly-Through: cannot connect to Siril", file=sys.stderr)
        return
    try:
        if not siril.is_image_loaded():
            siril.error_messagebox("Load a plate-solved image first.")
            return
        ensure_installed("numpy", "tifffile", "ttkthemes")
        bridge = _import_loomsiril()
        from loomsiril import pipeline

        header = siril.get_image_fits_header(return_as="dict") or {}
        b = bridge.bridge_image(siril.get_image_pixeldata(), header, siril.get_image_stars(),
                                siril.get_image_iccprofile())
        if b.wcs is None:
            siril.error_messagebox("The image has no plate solution: run plate solving first.")
            return
        starnet = bridge.find_starnet()
        options = bridge.load_options(siril.get_siril_configdir())
        values = ask(options, starnet is not None, b.target, siril)
        if values is None:
            return
        siril.log("Loom Fly-Through: building the layers", LogColor.DEFAULT)
        values.update(_layers(values, b, bridge, starnet))
        values["detections"] = b.detections
        try:
            bridge.save_options(values, siril.get_siril_configdir())
        except OSError as exc:  # the options are a convenience: a render must not fail on them
            siril.log(f"Loom Fly-Through: options not saved ({exc})", LogColor.SALMON)
        job = bridge.job_from_dialog(values, b.planes, b.header, b.icc)
        result = _run_pipeline(pipeline, job, siril)
        text = summary(result)
        siril.log(text, LogColor.GREEN)
        siril.info_messagebox(text)
    except Exception as exc:  # every failure is shown in a box, never a raw traceback in Siril
        _report(siril, f"Loom Fly-Through: {exc}")
    finally:
        try:
            siril.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
