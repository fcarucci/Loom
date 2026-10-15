"""The Fly-Through draft: a quick look at the clip before the full render (Loom for Siril).

The same idea as PixInsight Loom's Draft (``FlyThrough.renderDraft``, ``saveDraft`` / ``loadDraft``, ``FlyThrough.Player``):
the frames come from the SAME scene and frame pipeline as the full render (:func:`loomsiril.frame.frame`,
``pipeline._frame_planes``), but at the draft plan (:func:`loomsiril.presets.draft_plan`: 480 px on the long side, a
reduced frame rate, under 400 MB), always SDR in the chosen colour space, with the bilinear kernel. A ping-pong preset
renders only its forward half; the player plays it back and forth.

Three layers, the first two without tkinter:

* the core: :func:`prepare_scene`, :func:`render_draft` (small uint8 RGB frames in memory), the options-keyed cache
  (:func:`draft_key`, :func:`save_draft`, :func:`load_draft`) and :func:`play_order`;
* the player: :class:`DraftPlayer`, a Tk Toplevel (tkinter is imported when a window is made, never at import);
* the entry point for the dialog: :func:`show_draft`, called on the Tk thread. It never renders there: a worker
  thread does everything slow (reading the layers, Gaia, the frames, the cache) and feeds a
  :class:`loomsiril.fly_progress.FlyProgress` bar with Cancel; the player opens when the worker is done.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Optional, Union

import numpy as np

from loomsiril import logo as logo_mod
from loomsiril import pipeline as P
from loomsiril import presets as preset_mod
from loomsiril.fly_progress import FlyProgress, kind_of, no_distance_text
from loomsiril.types import Scene

__all__ = [
    "CACHE_FOLDER",
    "CACHE_KEEP",
    "DRAFT_VERSION",
    "Draft",
    "DraftPlayer",
    "DraftRun",
    "PreparedScene",
    "cache_dir",
    "draft_key",
    "draft_signature",
    "input_identity",
    "load_draft",
    "note_text",
    "play_order",
    "prepare_scene",
    "raise_window",
    "render_draft",
    "save_draft",
    "show_draft",
]

#: Bumped when the draft's own recipe changes (not the renderer: that is presets.RENDERER_VERSION, already in the key).
DRAFT_VERSION: int = 1

#: The drafts' folder inside Siril's config directory (beside the options file ``loomsiril-fly.json``).
CACHE_FOLDER: str = "loomsiril-fly-drafts"
#: Drafts kept in the cache: the most recently written; older ones are deleted on save.
CACHE_KEEP: int = 4

#: Options that do not change the draft's picture: the encode's and the output's (a draft is SDR whatever the dynamic
#: range), and where things are written or which tool binary ran. Beside presets.ENCODE_ONLY (the Fly.js list).
_NOT_THE_PICTURE: frozenset[str] = frozenset(
    ["out_dir", "object_name", "dynamic", "video", "music_path", "music_fade", "quality", "presets", "starnet_licence", "star_tool_path",
     "ffmpeg", "ffmpeg_path", "format", "peak", "starHdr", "star_hdr", "mode_note", "hdrTransfer", "hdr_peak"]
)

ProgressFrames = Callable[[int, int], None]
CancelCheck = Union[Callable[[], bool], threading.Event, None]


class DraftCancelled(Exception):
    """Raised inside the preparation when the user cancels; render_draft turns it into a cancelled Draft."""


# ---- the result ------------------------------------------------------------------------------------------------------


@dataclass
class Draft:
    """A draft clip: ``frames`` are uint8 ``(h, w, 3)`` RGB arrays, played at ``fps``; ``ping_pong`` plays them back and
    forth. ``cancelled`` drafts hold the frames made before the cancel and are never cached; ``from_cache`` says the
    frames were read, not rendered."""

    frames: list[np.ndarray]
    fps: float
    ping_pong: bool = False
    cancelled: bool = False
    from_cache: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def height(self) -> int:
        return int(self.frames[0].shape[0]) if self.frames else 0

    @property
    def width(self) -> int:
        return int(self.frames[0].shape[1]) if self.frames else 0

    @property
    def long_side(self) -> int:
        return max(self.width, self.height)

    @property
    def nbytes(self) -> int:
        return int(sum(f.nbytes for f in self.frames))


@dataclass
class PreparedScene:
    """The scene and the render options a draw needs (what :func:`prepare_scene` returns). Keep one to redraw
    with other ``options`` without finding the distance and placing the stars again."""

    scene: Scene
    options: dict[str, Any]
    distance: Optional[float] = None
    notes: list[str] = field(default_factory=list)


# ---- the scene, the same way pipeline.run makes it -------------------------------------------------------------------


def _cancelled(cancel: CancelCheck) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    return bool(cancel.is_set())


def prepare_scene(job: "P.FlyJob", stage: Optional[P.ProgressFn] = None, cancel: CancelCheck = None,
                  fetch_text: Optional[Callable[[str], str]] = None) -> PreparedScene:
    """The first half of ``pipeline.run`` (working copy, Gaia, distance, ``build_scene``, render options), no frames.

    Uses the pipeline's own steps, so the draft's scene is the full render's scene. ``stage(text, done, total)`` gets the
    stage texts; ``cancel`` stops it between steps (raises :class:`DraftCancelled`).
    """
    import numpy as _np

    from loomsiril import colour

    stage = stage or (lambda text, done, total: None)
    ctx = P.RunContext(stage, lambda: _cancelled(cancel), fetch_text)
    P._validate(job)
    P._validate_job(job)
    notes: list[str] = []
    wk = P._working_inputs(job, notes)
    starless, stars, wcs, fwhm, detections = wk["starless"], wk["stars"], wk["wcs"], wk["fwhm"], wk["detections"]
    h, w = _np.shape(starless[0])
    field_centre, field_radius = P._field(wcs, w, h)
    target = dict(job.target) if job.target else dict(field_centre)
    project = P._projector(wcs)
    stage("Looking up the stars and their distances in Gaia", 2, 4)
    sources = P._gaia_sources(job, P.gaia.GaiaQuery.from_centre(field_centre, field_radius, P.GAIA_MAG_LIMIT), ctx)
    found = P._find_distance(job, sources, target, field_radius, ctx)
    D = found.distance
    if D is None:
        raise ValueError(no_distance_text(found.reason, "Draft"))
    stage(P.distance_text(D, found.label), 3, 4)
    if ctx.is_cancelled():
        raise DraftCancelled()
    stage("Removing the stars and building the scene", 0, 0)
    built = P.build_scene(P.SceneRequest(starless, stars, project, sources, field_centre, D, fwhm, detections), stage)
    opts = P._render_opts(job, D, colour.colour_of(job.icc), notes)
    notes.append(P.distance_text(D, found.label))
    return PreparedScene(scene=built.scene, options=opts, distance=D, notes=notes)


# ---- the frames ------------------------------------------------------------------------------------------------------


def _to_rgb8(planes: list[np.ndarray]) -> np.ndarray:
    """One frame's float planes (1 or 3+ channels, [0, 1]) as an ``(h, w, 3)`` uint8 RGB array."""
    if len(planes) >= 3:
        rgb = np.stack([np.asarray(p, dtype=np.float32) for p in planes[:3]], axis=-1)
    else:
        rgb = np.repeat(np.asarray(planes[0], dtype=np.float32)[..., None], 3, axis=2)
    return np.clip(np.nan_to_num(rgb) * 255.0 + 0.5, 0, 255).astype(np.uint8)


def draft_spec(options: Mapping[str, Any]) -> dict[str, Any]:
    """The preset's spec (size, ping-pong, crossfade) of the draft's first preset, from dialog or render options."""
    presets = options.get("presets") or ["youtube_1080"]
    orientation = options.get("orientation", "landscape")
    orientation = P._ORIENTATION_JS.get(orientation, orientation)
    return preset_mod.preset_spec(presets[0], orientation, options.get("loop", "none"))


def draft_plan_for(options: Mapping[str, Any]) -> dict[str, Any]:
    """``presets.draft_plan`` for the options' first preset: the draft's size, frame rate, frame count and bytes."""
    spec = draft_spec(options)
    return preset_mod.draft_plan(float(options["duration"]), float(options["fps"]), preset_mod.DRAFT_LONG,
                                 spec["w"] / spec["h"])


def render_draft(scene_or_inputs: Union["P.FlyJob", PreparedScene, Scene], options: Optional[Mapping[str, Any]] = None,
                 progress: Optional[ProgressFrames] = None, cancel: CancelCheck = None) -> Draft:
    """The draft's frames, from the same scene and frame pipeline as the full render, at the draft plan.

    ``scene_or_inputs``: a ``FlyJob`` (the scene is made as ``pipeline.run`` makes it: :func:`prepare_scene`), a
    :class:`PreparedScene`, or a finished :class:`~loomsiril.types.Scene`. ``options``: the pipeline's render options
    (the JS names: ``duration``, ``fps``, ``presets``, ``orientation``, ``loop``, ``starQuality``, ``travel``,
    ``colour``, ...). Required for a bare Scene; for a FlyJob or PreparedScene they are laid over the ones the job
    gives (usually none).

    The frames are ``presets.DRAFT_LONG`` px on the long side, at the rate ``draft_plan`` gives (never above its
    400 MB budget), SDR in the chosen colour space, bilinear. A ping-pong preset gets its forward half only
    (``Draft.ping_pong`` tells the player to run it back). ``progress(done, total)`` is called after each frame;
    ``cancel`` (an Event or a callable) is checked before each frame: a cancelled draft holds the frames so far.
    """
    prepared = _prepared(scene_or_inputs, options, cancel)
    if prepared is None:
        return Draft(frames=[], fps=1.0, cancelled=True)
    opts = prepared.options
    logo_mod.attach(opts)        # the logo of the options (the dialog's own spelling too), read once for every frame
    scene = prepared.scene
    spec = draft_spec(opts)
    plan = draft_plan_for(opts)
    n = int(plan["frames"])
    dspec = dict(spec, w=int(plan["width"]), h=int(plan["height"]), pingPong=False)
    # the full spec sizes the quality shrink and the crop (renderDraft: qualityScene with the real spec)
    dscene = P._quality_scene(scene, opts, spec)
    crop = preset_mod.preset_crop(dscene.w, dscene.h, dscene.tp.x, dscene.tp.y, spec["w"], spec["h"])
    po = P._preset_options(dict(opts, dynamic="sdr"), spec["preset"])        # SDR in the chosen colour space
    po["kernel"] = "bilinear"
    po["frameDt"] = preset_mod.frame_step(n, False)
    po["logo"] = logo_mod.layer_for(opts, dspec["w"], dspec["h"], dscene.nc)     # at the draft's size, as renderDraft does
    F = preset_mod.crossfade_frames(float(opts["duration"]), plan["fps"]) if spec["crossfade"] else 0
    pr = P.PresetRender(spec=dspec, n=n, F=F, scene=dscene, po=po, crop=crop, folder="", out_base="")
    ro = P._render_options(po)
    frames: list[np.ndarray] = []
    still: dict[str, Any] = {}
    cancelled = False
    for i in range(n):
        if _cancelled(cancel):
            cancelled = True
            break
        frames.append(_to_rgb8(P._frame_planes(pr, i, ro, still)))
        if progress is not None:
            progress(i + 1, n)
    return Draft(frames=frames, fps=float(plan["fps"]), ping_pong=bool(spec["pingPong"]), cancelled=cancelled,
                 notes=list(prepared.notes))


def _prepared(src: Any, options: Optional[Mapping[str, Any]], cancel: CancelCheck) -> Optional[PreparedScene]:
    """``src`` as a PreparedScene with ``options`` laid over its own; None when cancelled while preparing."""
    if isinstance(src, PreparedScene):
        prepared = PreparedScene(src.scene, dict(src.options), src.distance, list(src.notes))
    elif isinstance(src, P.FlyJob):
        try:
            prepared = prepare_scene(src, cancel=cancel)
        except DraftCancelled:
            return None
    elif isinstance(src, Scene):
        if options is None:
            raise ValueError("a bare Scene needs the render options (duration, fps, presets, ...)")
        prepared = PreparedScene(src, {})
    else:
        raise TypeError(f"cannot draw a draft from {type(src).__name__}")
    if options:
        if any(str(k).startswith("logo") for k in options):       # a logo the options name replaces the prepared one
            for k in ("logoPath", "logoPlace", "logoOpacity", "logoDelay", "logoImage", "logoStamp"):
                prepared.options.pop(k, None)
        prepared.options.update(options)
    return prepared


# ---- the cache -------------------------------------------------------------------------------------------------------


def cache_dir(config_dir: Any) -> str:
    """The draft cache: ``<Siril config dir>/loomsiril-fly-drafts`` (``siril.get_siril_configdir()``), beside the options
    file the Fly-Through script already keeps there."""
    return os.path.join(os.fspath(config_dir), CACHE_FOLDER)


def _file_identity(path: Any) -> list[Any]:
    """Path, modification time (ns) and size of a file; missing files say so (their draft is never another file's)."""
    if not path:
        return ["", 0, 0]
    p = os.path.abspath(os.fspath(path))
    try:
        st = os.stat(p)
    except OSError:
        return [p, -1, -1]
    return [p, st.st_mtime_ns, st.st_size]


def input_identity(options: Mapping[str, Any]) -> str:
    """What the scene was made from, as text: the starless/stars files (path, mtime, size) when the layers come from
    files, the star tool when they are extracted, and ``image_key`` (any text that names the loaded Siril image and
    its plate solution: its file, time or a hash). The target and the distance are options, keyed beside it."""
    mode = options.get("mode", "files")
    parts: dict[str, Any] = {"mode": mode, "image": str(options.get("image_key", ""))}
    if mode == "extract":
        parts["tool"] = str(options.get("star_tool", ""))
    else:
        parts["starless"] = _file_identity(options.get("starless_path"))
        parts["stars"] = _file_identity(options.get("stars_path"))
    return json.dumps(parts, sort_keys=True)


def _plain(value: Any) -> Any:
    """``value`` as plain JSON data (numpy numbers, tuples, paths and the like included)."""
    return json.loads(json.dumps(value, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


def draft_signature(options: Mapping[str, Any]) -> str:
    """Everything that changes the draft's picture, as text: ``presets.frame_signature`` (the Fly.frameSignature port)
    of the options but the encode's and the output's, the draft's frame size from its plan, and the input identity."""
    spec = draft_spec(options)
    plan = draft_plan_for(options)
    picture = {k: _plain(v) for k, v in options.items()
               if k not in _NOT_THE_PICTURE and k not in preset_mod.ENCODE_ONLY and not k.startswith("hdr_transfer_")   # a draft is SDR
               and k not in ("starless_path", "stars_path", "image_key", "star_tool", "mode", "colour", "sceneKey", "orientation")}
    if not logo_mod.is_on(options):                           # no logo is drawn: nothing of it keys the draft
        picture = {k: v for k, v in picture.items() if not k.lower().startswith("logo")}
    # the logo: its file's identity (path, time, size) keys the draft too, so a changed logo is drawn again
    ls = logo_mod.logo_settings(options)
    if ls["path"] and ls["place"] != "off":
        picture["logoStamp"] = logo_mod.file_stamp(ls["path"])
    # the orientation is in the picture only through the frame it gives (dspec and spec["id"] below): a preset whose frame
    # is the same in both orientations (the social ones) keeps its draft
    dspec = {"w": int(plan["width"]), "h": int(plan["height"]), "pingPong": bool(spec["pingPong"]),
             "crossfade": bool(spec["crossfade"])}
    sig = preset_mod.frame_signature(picture, dspec, input_identity(options))
    return f"{sig}|draft{DRAFT_VERSION}|{plan['frames']}|{round(plan['fps'], 6)}|{spec['id']}"


def draft_key(options: Mapping[str, Any]) -> str:
    """The cache key: a short hash of :func:`draft_signature` (the Fly.hashKey idea)."""
    return hashlib.sha1(draft_signature(options).encode("utf-8")).hexdigest()[:20]


def _folder(directory: Any, key: str) -> str:
    return os.path.join(os.fspath(directory), "draft-" + key)


def save_draft(directory: Any, key: str, draft: Draft, keep: int = CACHE_KEEP) -> Optional[str]:
    """Keeps a finished draft under ``directory/draft-<key>/`` (``frames.npz`` and ``draft.json``), written aside and
    moved into place so a reader never sees half of it. A cancelled or empty draft is not kept (returns None). The
    ``keep`` most recently written drafts stay; older ones are deleted. Returns the folder."""
    if draft.cancelled or not draft.frames:
        return None
    folder = _folder(directory, key)
    tmp = f"{folder}.tmp-{os.getpid()}-{threading.get_ident()}"
    os.makedirs(os.fspath(directory), exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        np.savez(os.path.join(tmp, "frames.npz"), frames=np.stack(draft.frames))
        meta = {"key": key, "fps": draft.fps, "ping_pong": draft.ping_pong, "n": len(draft.frames),
                "width": draft.width, "height": draft.height, "version": DRAFT_VERSION}
        with open(os.path.join(tmp, "draft.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(tmp, folder)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _prune(directory, keep)
    return folder


def _prune(directory: Any, keep: int) -> None:
    """Deletes all but the ``keep`` newest ``draft-*`` folders (and stale ``.tmp-`` leftovers)."""
    try:
        entries = [e for e in os.scandir(os.fspath(directory)) if e.is_dir() and e.name.startswith("draft-")]
    except OSError:
        return
    final = sorted((e for e in entries if ".tmp-" not in e.name), key=lambda e: e.stat().st_mtime, reverse=True)
    for e in final[max(1, keep):]:
        shutil.rmtree(e.path, ignore_errors=True)


def load_draft(directory: Any, key: str) -> Optional[Draft]:
    """The draft kept for ``key``, or None: none kept, another key, or an unreadable or inconsistent entry (corrupt files
    are ignored, never fatal; the next save replaces them)."""
    folder = _folder(directory, key)
    try:
        with open(os.path.join(folder, "draft.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        if meta.get("key") != key or meta.get("version") != DRAFT_VERSION:
            return None
        with np.load(os.path.join(folder, "frames.npz")) as z:
            data = z["frames"]
        n, fps = int(meta["n"]), float(meta["fps"])
        if data.dtype != np.uint8 or data.ndim != 4 or data.shape[0] != n or data.shape[3] != 3 or n < 1 or not fps > 0:
            return None
        return Draft(frames=[data[i] for i in range(n)], fps=fps, ping_pong=bool(meta["ping_pong"]), from_cache=True)
    except Exception:  # a corrupt entry: unreadable json, truncated npz, wrong types
        return None


# ---- playback order and text -----------------------------------------------------------------------------------------


def play_order(n: int, ping_pong: bool) -> list[int]:
    """The frame indices of one playback cycle: 0..n-1, and for a ping-pong the way back too (n-2..1: no frame twice
    in a row, so the loop has no stutter at its ends)."""
    forward = list(range(max(0, n)))
    if ping_pong and n > 2:
        return forward + list(range(n - 2, 0, -1))
    return forward


def _fps_text(fps: float) -> str:
    return f"{fps:.1f}".rstrip("0").rstrip(".")


def note_text(draft: Draft) -> str:
    """The player's one-line note."""
    return f"Draft: SDR, {draft.long_side} px, {_fps_text(draft.fps)} fps. Judge HDR in the video"


def _ppm(frame: np.ndarray) -> bytes:
    """A binary PPM (P6) of a uint8 RGB frame: what ``tkinter.PhotoImage(data=...)`` reads without Pillow."""
    h, w = frame.shape[:2]
    return b"P6 %d %d 255\n" % (w, h) + np.ascontiguousarray(frame, dtype=np.uint8).tobytes()


# ---- the player ------------------------------------------------------------------------------------------------------


class DraftPlayer:
    """The draft's window (Tk Toplevel): the frames, Play/Pause, a scrub slider, Loop, the fps and frame label, the one-line
    note and Close. tkinter is imported here, not at module level.

    Playback uses ``after`` and never blocks. The frame shown is the one due by the clock (``clock``, seconds): a late tick
    drops frames instead of slowing the clip. Closing (Close, the window's own close, or the master being destroyed)
    cancels the pending ``after``: no callback outlives the window. Methods are for the Tk thread.
    """

    TITLE = "Loom Fly-Through - draft"

    def __init__(self, master: Any, draft: Draft, autoplay: bool = True,
                 clock: Callable[[], float] = time.monotonic) -> None:
        import tkinter as tk
        from tkinter import ttk

        if not draft.frames:
            raise ValueError("a draft with no frames cannot be played")
        self.draft = draft
        self._clock = clock
        self.order = play_order(len(draft.frames), draft.ping_pong)
        self.position = 0
        self.playing = False
        self.closed = False
        self._after_id: Optional[str] = None
        self._anchor = (0.0, 0)          # (clock time, position) playback counts from
        self._updating = False
        self.top = tk.Toplevel(master)
        self.top.title(self.TITLE)
        self.top.resizable(False, False)
        outer = ttk.Frame(self.top, padding=10)
        outer.grid(sticky="nsew")
        self._photo = tk.PhotoImage(master=self.top, data=_ppm(draft.frames[0]), format="ppm")
        self._picture = ttk.Label(outer, image=self._photo)
        self._picture.grid(row=0, column=0, columnspan=4, pady=(0, 6))
        self._scale_var = tk.DoubleVar(value=0.0)
        self._scale = ttk.Scale(outer, from_=0, to=max(1, len(self.order) - 1), orient="horizontal",
                                variable=self._scale_var, command=self._on_scrub, length=draft.width)
        self._scale.grid(row=1, column=0, columnspan=4, sticky="we")
        self._play_text = tk.StringVar(value="Play")
        self._play = ttk.Button(outer, textvariable=self._play_text, command=self.toggle)
        self._play.grid(row=2, column=0, sticky="w", pady=6)
        self.loop_var = tk.BooleanVar(master=self.top, value=True)
        self._loop = ttk.Checkbutton(outer, text="Loop", variable=self.loop_var)
        self._loop.grid(row=2, column=1, sticky="w", padx=8)
        self._status = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self._status).grid(row=2, column=2, sticky="e", padx=8)
        self._close_button = ttk.Button(outer, text="Close", command=self.close)
        self._close_button.grid(row=2, column=3, sticky="e")
        self._note = tk.StringVar(value=note_text(draft))
        ttk.Label(outer, textvariable=self._note).grid(row=3, column=0, columnspan=4, sticky="w")
        outer.columnconfigure(2, weight=1)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Destroy>", self._on_destroy, add="+")
        self._show(0)
        raise_window(self.top)
        if autoplay:
            self.play()

    # state ----------------------------------------------------------------------------------------------------------

    @property
    def frame_index(self) -> int:
        """The index (into ``draft.frames``) of the frame on screen."""
        return self.order[self.position]

    def _show(self, position: int) -> None:
        position = max(0, min(len(self.order) - 1, position))
        self.position = position
        index = self.order[position]
        self._photo.configure(data=_ppm(self.draft.frames[index]), format="ppm")
        self._updating = True
        try:
            self._scale_var.set(position)
        finally:
            self._updating = False
        self._status.set(f"{_fps_text(self.draft.fps)} fps   frame {index + 1} of {len(self.draft.frames)}")

    def _on_scrub(self, value: Any) -> None:
        if self._updating or self.closed:
            return
        self.seek(int(round(float(value))))

    # control --------------------------------------------------------------------------------------------------------

    def seek(self, position: int) -> None:
        """Shows the cycle position (a frame, or a place on the way back of a ping-pong); playback goes on from there."""
        if self.closed:
            return
        self._show(position)
        self._anchor = (self._clock(), self.position)

    def play(self) -> None:
        if self.closed or self.playing:
            return
        if self.position >= len(self.order) - 1 and not self.loop_var.get():
            self._show(0)                       # played to the end without a loop: again from the start
        self.playing = True
        self._play_text.set("Pause")
        self._anchor = (self._clock(), self.position)
        self._schedule()

    def pause(self) -> None:
        self.playing = False
        self._cancel_after()
        if not self.closed:
            self._play_text.set("Play")

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def step(self, by: int = 1) -> None:
        """Pauses and moves ``by`` positions (wrapping round when Loop is on)."""
        if self.closed:
            return
        self.pause()
        target = self.position + by
        if self.loop_var.get():
            target %= len(self.order)
        self._show(target)

    def tick(self) -> None:
        """One playback step (what ``after`` calls): shows the frame due by the clock, then arms the next tick."""
        self._after_id = None
        if self.closed or not self.playing:
            return
        now = self._clock()
        position = self._anchor[1] + int((now - self._anchor[0]) * self.draft.fps)
        last = len(self.order) - 1
        if position > last:
            if not self.loop_var.get():
                self._show(last)
                self.pause()
                return
            position %= len(self.order)
            self._anchor = (now, position)          # counts on from the wrap
        if position != self.position:
            self._show(position)
        self._schedule()

    def _schedule(self) -> None:
        self._cancel_after()
        if self.closed or not self.playing:
            return
        # the next frame is due (position + 1 - anchor) / fps after the anchor
        due = self._anchor[0] + (self.position + 1 - self._anchor[1]) / self.draft.fps
        delay = max(1, int((due - self._clock()) * 1000))
        self._after_id = self.top.after(delay, self.tick)

    def _cancel_after(self) -> None:
        if self._after_id is not None:
            try:
                self.top.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def close(self) -> None:
        """Stops playback and closes the window; safe to call twice, or after the window is already gone."""
        self.playing = False
        self._cancel_after()
        if self.closed:
            return
        self.closed = True
        try:
            self.top.destroy()
        except Exception:
            pass

    def _on_destroy(self, event: Any) -> None:
        if event.widget is self.top:          # the window went (its master was destroyed, or the user closed it)
            self.playing = False
            self.closed = True
            self._cancel_after()


# ---- the entry point for the dialog ----------------------------------------------------------------------------------


class DraftRun:
    """One ``show_draft``: the progress model, the worker thread, and (once done) the player.

    ``progress`` is the :class:`FlyProgress` the bar and Cancel follow; ``draft`` the result (None until done, or when it
    was cancelled or failed); ``player`` the window (None until it opens); ``error`` the message of a failure.
    :meth:`cancel` is what Cancel does. All methods are for the Tk thread.
    """

    POLL_MS = 100

    def __init__(self, master: Any, make_inputs: Callable[[FlyProgress], Any], options: Mapping[str, Any],
                 progress: FlyProgress, window: Any, cache_directory: Optional[str],
                 on_done: Optional[Callable[["DraftRun"], None]],
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.master = master
        self._log = log                           # Siril's console line (the Tk thread only); None: no log
        self._logged_stage = ""
        self._logged_step = -1
        self.progress = progress
        self.window = window
        self.options = dict(options)
        self.key = ""                             # set on the worker thread (it stats the layer files)
        self.cache_directory = os.fspath(cache_directory) if cache_directory else None
        self.draft: Optional[Draft] = None
        self.player: Optional[DraftPlayer] = None
        self.error: str = ""
        self.finished = False
        self._make_inputs = make_inputs
        self._on_done = on_done
        self._ended = threading.Event()
        self._after_id: Optional[str] = None
        self.thread = threading.Thread(target=self._work, name="loomsiril-draft", daemon=True)

    def start(self) -> "DraftRun":
        self.note("Draft: starting")
        self.thread.start()
        self._poll()
        return self

    def note(self, text: str) -> None:
        """One stage line in Siril's console, so a draft that stops shows where (never raises)."""
        if self._log is not None:
            try:
                self._log(text)
            except Exception:
                pass

    def _log_stage(self) -> None:
        """A console line when the stage changes, and while the frames render one per tenth (``rendering n/N``)."""
        m = self.progress
        if m.done or m.stage == FlyProgress.STARTING:
            return
        if m.stage.lower().startswith("rendering") and m.count:
            step = (10 * m.count[0]) // m.count[1]
            if step != self._logged_step:
                self._logged_step = step
                self.note(f"Draft: rendering {m.count[0]}/{m.count[1]}")
        elif m.stage != self._logged_stage:
            self._logged_stage = m.stage
            kind = kind_of(m.stage)
            self.note("Draft: extracting stars..." if kind == "extract" and m.stage.lower().startswith("extracting")
                      else f"Draft: {m.stage[:1].lower()}{m.stage[1:]}...")

    def cancel(self) -> None:
        self.progress.request_cancel()

    def close(self) -> None:
        """Stops the run and leaves no ``after`` callback pending (the master is going away)."""
        self.progress.request_cancel()
        self.finished = True
        if self._after_id is not None:
            try:
                self.master.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    # the worker thread: only the model's queue and plain attributes are touched here ---------------------------------

    def _work(self) -> None:
        model = self.progress
        try:
            self.key = draft_key(self.options)
            draft = None
            if self.cache_directory:
                model.report("Looking for the draft kept before")
                draft = load_draft(self.cache_directory, self.key)
            if draft is None:
                draft = self._render(model)
                if (self.cache_directory and not draft.cancelled and not self.progress.cancel_event.is_set()):
                    model.report("Keeping the draft")
                    try:
                        save_draft(self.cache_directory, self.key, draft)
                    except OSError as exc:            # a full disk is not a failed draft
                        model.log(f"the draft could not be kept: {exc}")
            self.draft = draft
            if draft.cancelled:
                model.finish("Draft cancelled.", cancelled=True)
            else:
                model.finish(f"Draft: {len(draft.frames)} frames." + (" (kept from before)" if draft.from_cache else ""))
        except DraftCancelled:
            model.finish("Draft cancelled.", cancelled=True)
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            model.fail(self.error)
        finally:
            self._ended.set()

    def _render(self, model: FlyProgress) -> Draft:
        source = self._make_inputs(model)
        if model.cancel_event.is_set():
            return Draft(frames=[], fps=1.0, cancelled=True)
        if isinstance(source, P.FlyJob):
            source = prepare_scene(source, stage=model.report, cancel=model.cancel_event)
        return render_draft(source, None, progress=lambda d, t: model.report("Rendering the draft frames", d, t),
                            cancel=model.cancel_event)

    # the Tk thread ---------------------------------------------------------------------------------------------------

    def _poll(self) -> None:
        self._after_id = None
        if self.finished:
            return
        try:
            self.progress.pump()
            self._log_stage()
            if self._ended.is_set():
                self._finish()
                return
            self._after_id = self.master.after(self.POLL_MS, self._poll)
        except Exception as exc:     # the master went away, or the finish failed: stop the worker, say so
            self.progress.request_cancel()
            self.finished = True
            self._fail(f"{type(exc).__name__}: {exc}")

    def _fail(self, message: str) -> None:
        """A failure on the Tk thread: kept in ``error``, written to the console, and shown by the progress window
        (which stays up with its Close button) when it still exists."""
        self.error = self.error or message
        self.note(f"Draft: failed: {self.error}")
        try:
            self.progress.fail(self.error)
            self.progress.pump()
        except Exception:
            pass
        refresh = getattr(self.window, "refresh", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass

    def _finish(self) -> None:
        self.finished = True
        self.progress.pump()
        try:
            if self.error:
                # the progress window stays: it shows the message and its Close button (it must never vanish unread)
                self.note(f"Draft: failed: {self.error}")
            elif self.draft is None or self.draft.cancelled:
                self.note("Draft: cancelled")
                self._close_window()
            else:
                if self._logged_step < 0 and not self.draft.from_cache:      # a fast render may fall between two polls
                    self.note(f"Draft: rendering {len(self.draft.frames)}/{len(self.draft.frames)}")
                self.note("Draft: done, opening the player")
                self.player = DraftPlayer(self.master, self.draft)       # before the window goes: a failure stays visible
                self._close_window()
        except Exception as exc:
            self._fail(f"The draft could not be shown: {type(exc).__name__}: {exc}")
        if self._on_done is not None:
            self._on_done(self)

    def _close_window(self) -> None:
        closer = getattr(self.window, "close", None)
        if callable(closer):                  # cancels the window's own pending poll, then destroys it
            try:
                closer()
            except Exception:
                pass
            return
        top = getattr(self.window, "top", None)
        if top is not None:
            try:
                top.destroy()
            except Exception:
                pass


def raise_window(top: Any) -> None:
    """Brings a new window in front (macOS leaves a window made from a callback behind its parent, or behind Siril):
    lift, a brief topmost, and focus (no ``after``: nothing is left pending). Never raises: a window that cannot be raised is still a window."""
    if top is None:
        return
    try:
        top.lift()
        top.attributes("-topmost", True)          # up and straight down again: in front, but not stuck over Siril
        top.update_idletasks()
        top.attributes("-topmost", False)
        top.focus_force()
    except Exception:
        pass


def show_draft(master: Any, make_inputs: Callable[[FlyProgress], Any], options: Mapping[str, Any],
               progress_window: Any = None, *, cache_dir: Optional[str] = None,
               on_done: Optional[Callable[[DraftRun], None]] = None,
               log: Optional[Callable[[str], None]] = None) -> DraftRun:
    """Draws the draft and opens the player. Call on the Tk thread; returns at once (the work is on a worker thread).

    ``make_inputs(progress)`` runs on the worker, and only when the draft is not in the cache. It reads the layers (or
    runs the star extraction, reporting to ``progress``, a :class:`FlyProgress`) and returns a ``FlyJob`` (for example
    ``siril_bridge.job_from_dialog(values, planes, header, icc)``), a :class:`PreparedScene` or a ``Scene``.
    ``options``: the dialog's values, copied now. They key the cache (:func:`draft_key`): ``mode`` ("files" or "extract"),
    ``starless_path`` and ``stars_path`` (path, mtime and size are keyed) or ``star_tool``, ``image_key`` (text naming the
    loaded image and its plate solution), ``target``, ``target_type``, ``distance``, ``duration``, ``fps``, ``loop``,
    ``orientation``, ``star_quality``, ``presets`` (the first is drawn), ``gaia`` and any ``options`` render overrides.
    Not keyed: ``dynamic`` (a draft is SDR), ``video``, ``music_path``, ``music_fade``, ``quality``, ``out_dir``, ``object_name``.
    ``progress_window``: a window with ``.model`` (a :class:`FlyProgress`) and ``.top``, such as
    ``fly_ui.FlyProgressWindow``; None makes one. It is closed when the player opens (and stays up, showing the message,
    when the draft fails). ``cache_dir``: where drafts are kept (:func:`cache_dir` of Siril's config directory); None keeps
    none. ``on_done(run)`` is called on the Tk thread when the run ends, whatever the outcome. ``log(text)``: a line for
    Siril's console at each stage (``Draft: extracting stars...``, ``Draft: rendering 3/12``, ``Draft: done, opening the
    player``, ``Draft: failed: ...``), called on the Tk thread only.
    """
    if progress_window is None:
        from loomsiril.fly_ui import FlyProgressWindow

        model = FlyProgress()
        window: Any = FlyProgressWindow(master, model)
        window.top.title("Loom Fly-Through - draft")
    elif isinstance(progress_window, FlyProgress):
        model, window = progress_window, None             # a bare model: the caller shows it (or nobody does)
    else:
        model, window = progress_window.model, progress_window
    raise_window(getattr(window, "top", None))
    run = DraftRun(master, make_inputs, options, model, window, cache_dir, on_done, log)
    return run.start()
