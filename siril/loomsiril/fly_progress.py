"""The Fly-Through run behind the progress window: the worker's body, the progress model and the headless driver.

Everything slow (reading the layers, star extraction, the pipeline: Gaia, frames, ffmpeg) runs on ONE worker thread
(:func:`start`). The worker only puts events on the model's queue; the thread that owns the UI (the Tk thread, which is
also the thread that talks to Siril) reads them with :meth:`FlyProgress.pump`. The worker never touches Tk or Siril.

No tkinter here (the window is loomsiril.fly_ui); this module is testable without a display.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Optional

__all__ = ["FlyProgress", "NoDistance", "clock_text", "drive", "ensure_distance", "fit_image_file", "layers",
           "no_distance_text", "start", "work"]


#: The phases of a run in order, with the share of the whole each is given by default (extract 15, Gaia 5, the scene 5,
#: render 70, encode 10); the worker narrows the plan to the phases that will run (``FlyProgress.plan``).
KINDS: tuple[str, ...] = ("extract", "gaia", "scene", "render", "encode")
DEFAULT_WEIGHTS: dict[str, float] = {"extract": 15.0, "gaia": 5.0, "scene": 5.0, "render": 70.0, "encode": 10.0}


def kind_of(stage: str) -> str:
    """The phase a pipeline or tool stage text belongs to (anything unrecognised is scene building)."""
    text = stage.lower()
    if text.startswith(("extracting", "reading", "finding the distance")):
        return "extract"
    if "gaia" in text:
        return "gaia"
    if text.startswith("rendering"):
        return "render"
    if text.startswith("encoding"):
        return "encode"
    return "scene"


def clock_text(seconds: float) -> str:
    """``m:ss`` (or ``h:mm:ss``) for a duration in seconds."""
    s = max(0, int(round(seconds)))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class FlyProgress:
    """The progress state and the queue that feeds it.

    Worker side (any thread): :meth:`report`, :meth:`tool_percent`, :meth:`log`, :meth:`finish`, :meth:`fail`.
    UI side: :meth:`pump`, then read ``stage``, ``percent``, ``indeterminate``, ``done``, ``error``, ``cancelled``,
    :meth:`label` and :meth:`timing`. ``cancel_event`` is what the worker polls; :meth:`request_cancel` sets it.
    """

    STARTING = "Starting..."
    CANCELLING = "Cancelling after the current frame or step..."

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.messages: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.cancel_event = threading.Event()
        self._clock = clock
        self._started = clock()
        self._stage_started = self._started
        self.image_title: str = ""                         # the image's file name, shown in the window's title
        self.stage: str = self.STARTING
        self.count: Optional[tuple[int, int]] = None      # (done, total) of the stage, None when it reports none
        self.percent: float = 0.0                          # of the current stage: it may start again at 0
        self.overall: float = 0.0                          # of the whole run: never decreases, 100 only on success
        self._weights: dict[str, float] = dict(DEFAULT_WEIGHTS)
        self._fraction: dict[str, float] = {}
        self._kind = ""
        self.indeterminate: bool = True
        self.done = False
        self.ok = False
        self.cancelled = False
        self.error: str = ""
        self.done_text: str = ""
        self.log_lines: list[str] = []
        self.console_lines: list[str] = []                 # lines for Siril's console: the UI thread writes them (forward_to_siril)
        self.cancel_requested = False
        self._finished_at: Optional[float] = None

    # worker side ------------------------------------------------------------------------------------------------

    def report(self, stage: str, done: int = 0, total: int = 0) -> None:
        """A stage text with its count (the pipeline's progress callback; ``total`` 0 means no count)."""
        self.messages.put(("progress", (str(stage), int(done), int(total))))

    def tool_percent(self, percent: float, text: str = "") -> None:
        """A tool's own percentage (the star tools' ``on_progress``): the bar follows it."""
        self.messages.put(("percent", (float(percent), str(text))))

    def plan(self, weights: Mapping[str, float]) -> None:
        """The phases this run will go through, with their weights (normalised here): the overall bar follows them."""
        self.messages.put(("plan", dict(weights)))

    def log(self, text: str) -> None:
        self.messages.put(("log", str(text)))

    def announce(self, text: str) -> None:
        """A line for Siril's console (the distance in use, say): the UI thread logs it, the worker never touches Siril."""
        self.messages.put(("console", str(text)))

    def finish(self, text: str, cancelled: bool = False) -> None:
        self.messages.put(("done", (str(text), bool(cancelled))))

    def fail(self, text: str) -> None:
        self.messages.put(("error", str(text)))

    # UI side ----------------------------------------------------------------------------------------------------

    def request_cancel(self) -> None:
        self.cancel_requested = True
        self.cancel_event.set()

    def pump(self) -> int:
        """Applies every queued event, in order; returns how many. Never blocks."""
        applied = 0
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                return applied
            applied += 1
            if kind == "progress":
                self._progress(*value)
            elif kind == "plan":
                self._weights = {k: float(w) for k, w in value.items() if k in KINDS and w > 0} or dict(DEFAULT_WEIGHTS)
                self._recompute()
            elif kind == "percent":
                self.percent, text = min(100.0, max(0.0, value[0])), value[1]
                self.indeterminate = False
                self.count = None
                if text:
                    self.stage = text
                if self._kind:
                    self._fraction[self._kind] = max(self._fraction.get(self._kind, 0.0), self.percent / 100.0)
                    self._recompute()
            elif kind == "log":
                self.log_lines.append(value)
            elif kind == "console":
                self.console_lines.append(value)
            elif kind == "done":
                self.done, self.ok, self.done_text, self.cancelled = True, not value[1], value[0], value[1]
                self.indeterminate = False
                if self.ok:
                    self.percent = self.overall = 100.0
                self._finished_at = self._clock()
            elif kind == "error":
                self.done, self.ok, self.error = True, False, value
                self.indeterminate = False
                self._finished_at = self._clock()

    def _progress(self, stage: str, done: int, total: int) -> None:
        if stage != self.stage:
            self._stage_started = self._clock()
        self.stage = stage
        if total > 0:
            self.count = (min(done, total), total)
            self.percent = 100.0 * min(done, total) / total
            self.indeterminate = False
        else:
            self.count, self.indeterminate = None, True
        self._kind = kind_of(stage)
        if total > 0:
            self._fraction[self._kind] = max(self._fraction.get(self._kind, 0.0), min(done, total) / total)
        self._recompute()

    def _recompute(self) -> None:
        """The overall percent: the weighted share of the phases done (earlier phases count as finished), never down."""
        if not self._kind:
            return
        here = KINDS.index(self._kind)
        total = sum(self._weights.values())
        got = sum(w * (1.0 if KINDS.index(k) < here else self._fraction.get(k, 0.0)) for k, w in self._weights.items())
        self.overall = max(self.overall, min(99.0, 100.0 * got / total))

    @property
    def failed(self) -> bool:
        """The run ended with an error (not a success, not a cancel)."""
        return self.done and not self.ok and not self.cancelled

    def overall_text(self) -> str:
        """``Overall 19%`` while it runs, ``Stopped`` after an error, ``Cancelled`` after a cancel."""
        if self.failed:
            return "Stopped"
        if self.cancelled:
            return "Cancelled"
        return f"Overall {round(self.overall)}%"

    def stage_value(self) -> float:
        """What the stage bar shows: its percent, empty once the run failed or was cancelled."""
        return 0.0 if (self.failed or self.cancelled) else self.percent

    def overall_value(self) -> float:
        """What the overall bar shows: its percent, empty once the run failed or was cancelled."""
        return 0.0 if (self.failed or self.cancelled) else self.overall

    def label(self) -> str:
        """The stage line: the text, and ``n of N`` with the percent when the stage counts."""
        if self.done:
            return self.error or self.done_text
        if self.cancel_requested:
            return self.CANCELLING
        if self.count is not None:
            return f"{self.stage}: {self.count[0]} of {self.count[1]} ({round(self.percent)}%)"
        if not self.indeterminate:
            return f"{self.stage} ({round(self.percent)}%)"
        return self.stage + " (working...)"

    def timing(self) -> str:
        """``elapsed m:ss`` and, while a counted stage runs, ``about m:ss left``; empty when the run succeeded,
        ``Failed at: <stage>`` or ``Cancelled at: <stage>`` when it did not."""
        if self.done:
            if self.failed:
                return f"Failed at: {self.stage}"
            return f"Cancelled at: {self.stage}" if self.cancelled else ""
        now = self._clock()
        text = f"elapsed {clock_text(now - self._started)}"
        if self.count is not None and self.count[0] > 0 and self.count[0] < self.count[1]:
            per = (now - self._stage_started) / self.count[0]
            text += f", about {clock_text(per * (self.count[1] - self.count[0]))} left"
        return text


# ---- the worker's body -----------------------------------------------------------------------------------------------


def fit_image_file(image: list, b: Any, progress: FlyProgress) -> list:
    """The Image file's planes on the open image's grid: as they are when the size is the same, resampled when the file
    is the same field at another size (:func:`loomsiril.fly_grid.verdict`), else a ValueError that says what to do.
    The plate solution and the star positions stay the open image's. Runs on the worker."""
    from loomsiril import fly_grid

    file = (image[0].shape[1], image[0].shape[0], len(image))
    usable, text = fly_grid.verdict(file, (b.width, b.height, len(b.planes)))
    if not usable:
        raise ValueError(text)
    if text:
        progress.report(f"Reading the image file: resampling {file[0]} x {file[1]} to {b.width} x {b.height}")
        image = fly_grid.fit_planes(image, b.width, b.height)
    return image


class NoDistance(Exception):
    """No distance was typed, passed on by the dialog or found: the message is the one to show (see no_distance_text)."""


def no_distance_text(reason: str, action: str = "Render") -> str:
    """What a GUI says when the distance cannot be found (the CLI says "give --distance PC" instead)."""
    why = reason or "no star cluster and no bright illuminating star were found"
    return (f"No distance found for this target ({why}). Type a distance in pc in the Distance box and press "
            f"{action} again.")


def _distance_line(distance: float, label: str) -> str:
    """The Siril console line for the distance in use: "distance 4562 pc (illuminating star G 8.8, Gaia online)"."""
    if distance == float("inf"):
        return f"distance: {label}"
    plain = f"{distance:.0f}" if (distance == int(distance) or distance >= 100) else f"{distance:g}"
    return f"distance {plain} pc ({label})"


def ensure_distance(values: dict, b: Any, progress: FlyProgress, resolve: Callable[..., Any],
                    action: str = "Render") -> None:
    """The distance this run uses, settled BEFORE any slow work (star extraction, Gaia field, frames).

    A typed distance and a galaxy need no lookup. The distance the dialog found for the boxes as they are now arrives in
    ``values`` as ``found_distance`` (+ ``found_source``, ``found_label``) and is used as it is. Without one (the lookup
    was still running, the boxes changed since, or it found nothing) the same resolver the dialog uses
    (:func:`loomsiril.fly_distance.resolve`, with its cache-empty -> Gaia online fallback) is run here, on the worker,
    for the target the render will use (the dialog's, else the image's), the Gaia source and the image's field radius;
    its answer is put in ``values`` as ``found_*``. The line for Siril's console and the stage text name the distance.
    Raises :class:`NoDistance` (with the GUI message) when none can be found, and for a cancel (message "cancelled").
    """
    from loomsiril import fly_distance

    typed = values.get("distance")
    if typed is not None:
        progress.report(f"Distance: {typed:g} pc (typed)", 0, 0)
        progress.announce(_distance_line(float(typed), "typed"))
        return
    if values.get("target_type") == "galaxy":
        progress.announce(_distance_line(float("inf"), "galaxy (fixed backdrop)"))
        return
    if values.get("found_distance") is not None:
        distance, label = float(values["found_distance"]), values.get("found_label") or values.get("found_source") or ""
    else:
        progress.report("Finding the distance…", 0, 0)
        target = values.get("target") or getattr(b, "target", None)
        radius = fly_distance.field_radius(b.wcs, b.width, b.height)
        found = resolve(target, values.get("target_type", "nebula"), values.get("gaia", "cache"), None, radius,
                        is_cancelled=progress.cancel_event.is_set)
        if found.distance is None:
            if found.reason == "cancelled":
                raise NoDistance("cancelled")
            raise NoDistance(no_distance_text(found.reason, action))
        distance, label = float(found.distance), found.source
        values.update({"found_distance": distance, "found_source": found.code, "found_label": label})
    progress.report(f"Distance: ~{distance:,.0f} pc ({label})", 0, 0)
    progress.announce(_distance_line(distance, label))


def layers(values: Mapping[str, Any], b: Any, bridge: Any, progress: FlyProgress,
           extract: Optional[Callable[..., Any]] = None) -> dict:
    """The starless and stars planes: from the chosen extraction tool, or read from the two files."""
    if values["mode"] == "extract":
        from loomsiril import fly_tools, starnet_licence

        tool_id, exe = values.get("star_tool"), values.get("star_tool_path")
        if not tool_id or not exe:
            raise ValueError("No star-extraction tool is chosen: pick one in the dialog or use the files.")
        if tool_id == "starnet2":
            starnet_licence.require(values)
        label = dict(fly_tools.TOOL_IDS).get(tool_id, tool_id)
        image = b.planes
        image_path = str(values.get("image_path") or "").strip()
        if image_path:                                    # extract from this file; the solution is the open image's
            progress.report("Reading the image file")
            image = bridge.read_layer(os.path.expanduser(image_path))
            image = fit_image_file(image, b, progress)
        progress.report(f"Extracting stars with {label}")
        run = extract or fly_tools.extract_starless
        starless = run(tool_id, exe, image, licence=values.get("starnet_licence"), log=progress.log,
                       on_progress=progress.tool_percent, cancelled=progress.cancel_event.is_set)
        starless, stars = bridge.split_stars(image, starless)
        return {"starless": starless, "stars": stars}
    if not values["starless_path"] or not values["stars_path"]:
        raise ValueError("Choose the starless and the stars files.")
    progress.report("Reading the starless and stars files")
    starless = bridge.read_layer(values["starless_path"])
    stars = bridge.read_layer(values["stars_path"])
    for name, planes in (("starless", starless), ("stars", stars)):
        if len(planes) != len(b.planes) or planes[0].shape != b.planes[0].shape:
            raise ValueError(f"The {name} file does not match the image ({b.width} x {b.height}, "
                             f"{len(b.planes)} channel(s)): the plate solution comes from the open image. Export the "
                             "starless and stars layers at the open image's size, or open the files in Siril and solve them.")
    return {"starless": starless, "stars": stars}


def work(values: dict, b: Any, bridge: Any, pipeline: Any, progress: FlyProgress,
         summarise: Callable[[Any], str], extract: Optional[Callable[..., Any]] = None,
         resolve: Optional[Callable[..., Any]] = None) -> None:
    """Distance, layers, job and render, reporting to ``progress``; ends with ``finish`` or ``fail`` (never raises).

    ``resolve`` (the dialog's :func:`loomsiril.fly_distance.resolve`; None: the pipeline finds it itself, as the CLI does)
    settles the distance first, so a target with none fails at once with a message for the dialog, before the star tool
    has run."""
    weights = dict(DEFAULT_WEIGHTS)
    if values.get("mode") != "extract":
        weights["extract"] = 3.0                      # reading two files is quick
    if not values.get("video"):
        del weights["encode"]
    progress.plan(weights)
    try:
        from loomsiril.loom.tools.runner import ToolCancelled
    except ImportError:  # the Loom package is not importable: only a pipeline cancel is then possible
        ToolCancelled = type("ToolCancelled", (Exception,), {})  # noqa: N806
    try:
        if resolve is not None:
            ensure_distance(values, b, progress, resolve)
        values.update(layers(values, b, bridge, progress, extract))
        values["detections"] = b.detections
        job = bridge.job_from_dialog(values, b.planes, b.header, b.icc)
        result = pipeline.run(job, progress.report, progress.cancel_event.is_set)
        progress.finish(summarise(result), cancelled=bool(getattr(result, "cancelled", False)))
    except ToolCancelled:
        progress.finish("Cancelled while extracting the stars. Nothing was written.", cancelled=True)
    except NoDistance as exc:
        if str(exc) == "cancelled":
            progress.finish("Cancelled while finding the distance. Nothing was written.", cancelled=True)
        else:
            progress.fail(str(exc))
    except Exception as exc:
        if exc.__class__.__name__ == "NoDistanceError":      # the pipeline's own (a run that was not given a distance)
            progress.fail(no_distance_text(getattr(exc, "reason", "")))
            return  # shown by the window, or by main() on the Siril thread
        progress.fail(str(exc))


def start(target: Callable[[], None]) -> threading.Thread:
    """``target`` on a daemon worker thread."""
    thread = threading.Thread(target=target, name="loomsiril-fly", daemon=True)
    thread.start()
    return thread


def drive(progress: FlyProgress, thread: threading.Thread, on_update: Callable[[FlyProgress], None],
          interval: float = 0.2) -> None:
    """The headless UI loop, on the calling thread: pump, forward (``on_update``), until the worker is done."""
    while thread.is_alive():
        if progress.pump():
            on_update(progress)
        thread.join(interval)
    progress.pump()
    on_update(progress)
