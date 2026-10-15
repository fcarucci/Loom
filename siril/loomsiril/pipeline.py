"""The render driver: Gaia, distance, scene and frames, with no PixInsight (E6: loomsiril/pipeline.py).

Ported from Loom ``script/FlyThrough.js`` (v0.4.1): the preset folders and frame files
(37-48, 58-91, 93-123, 125-179), the preset options and the encode (220-306), the header
centre (307-315), the distance (427-462, Sky.js 456-462 for the cluster sample), the scene
(463-509), the loop image (511-521) and the working copy (Sky.js 1302-1338). The "run"
function is the whole CLI / Siril flow; the Siril script (E7) and the CLI call it.

Rows are top-down everywhere: the pipeline never flips a plane. FITS and Siril's
own data are flipped once at ingest, before they reach this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from loomsiril import colour, encode, fly_video, gaia, geometry, parallax, resample
from loomsiril import logo as logo_mod
from loomsiril import presets as preset_mod
from loomsiril import scene as scene_mod
from loomsiril import starlayer
from loomsiril import wcs as wcs_mod
from loomsiril.core import separation, tan_project, tan_unproject
from loomsiril.frame import RenderOptions
from loomsiril.frame import frame as render_frame
from loomsiril.frame import scene_for
from loomsiril.io_tiff import write_tiff16
from loomsiril.types import Placed, Scene

__all__ = [
    "BuiltScene",
    "FlyJob",
    "NoDistanceError",
    "Result",
    "SceneRequest",
    "build_scene",
    "cluster_distance",
    "clear_frames",
    "loop_image",
    "FinalRender",
    "SceneTerms",
    "render_final",
    "run",
    "clamp_to_image",
    "working_copy",
]

ProgressFn = Callable[[str, int, int], None]

#: Sky.GAIA_MAG_LIMIT: the magnitude limit of a field's Gaia sample.
GAIA_MAG_LIMIT: float = 17.6

#: The note when the Gaia cache held nothing for the target and the answer came from Gaia online.
CACHE_EMPTY_NOTE = "Gaia: cache empty here, used online"

#: The CLI's words for a render that found no distance (a GUI says its own: loomsiril.fly_progress.no_distance_text).
NO_DISTANCE_CLI = "no distance found for the target (no cluster and no illuminating star): give --distance PC"


class NoDistanceError(ValueError):
    """No distance was typed, given or found. ``str()`` is the CLI text (give --distance PC); ``reason`` says why, in
    plain language, for a GUI to word its own message."""

    def __init__(self, reason: str = "") -> None:
        super().__init__(NO_DISTANCE_CLI)
        self.reason = reason


#: The frame files take five digits (preset_mod.frame_path): a longer clip would collide.
MAX_FRAMES: int = 100000

#: What a preset folder's frames were made from (presets.frame_signature), and the frame being written.
FRAMES_RECORD: str = "frames.json"
PARTIAL_SUFFIX: str = ".part"

#: The longest clip the CLI and the pipeline accept, in seconds.
MAX_DURATION: float = 600.0


#: Fly.HDR_DEFAULT_TRANSFER: the transfer a preset's HDR video uses unless the dialog says otherwise.
HDR_DEFAULT_TRANSFER: dict[str, str] = {
    "social_vertical": "hlg",
    "social_square": "hlg",
    "youtube_4k": "hlg",
    "youtube_1080": "hlg",
    "exhibition": "pq",
}

#: The Dialog's initial values (FlyThrough.js Dialog constructor, 1282-1380), in the JS option names.
#: travel is not here: it is parallax.default_travel(type, D), set once D is known.
DIALOG_DEFAULTS: dict[str, Any] = {
    "easing": "smoothstep",          # the "Smooth" item (the first)
    "growth": geometry.GROWTH_DEFAULT,
    "brightening": True,
    "backdropMotion": geometry.BACKDROP_MOTION_DEFAULT,
    "twinkle": geometry.TWINKLE_DEFAULT,
    "motionBlur": True,
    "bloom": 1.0,                    # Bloom (%) 100
    "starSaturation": 1.0,           # the slider at 100%
    "starHdr": True,
    "kernel": "bicubic",
}

#: The lower-case job values the JS dialog spells differently (orientation, loop kept as the JS ids).
_ORIENTATION_JS: dict[str, str] = {"landscape": "horizontal", "portrait": "vertical"}


@dataclass
class FlyJob:
    """Everything one render needs: the two layers, the WCS, the target, the distance and the output.

    ``starless``/``stars`` are (h, w) float32 planes in [0, 1], one per channel, top-down; ``wcs``
    the Fly.js wcs dict. ``target`` {"ra", "dec"}: None takes the image centre. ``target_type``
    "nebula" or "galaxy" (a galaxy is a fixed backdrop, D = inf). ``distance`` pc: None looks it up
    (cluster, then illuminating star). ``distance_source`` / ``distance_label``: where a given ``distance`` came from
    ("typed" by default; the dialog passes the source and text of the distance it found and showed, so the render
    uses that very value) . ``gaia`` "cache", "online" or "tsv:PATH". ``options`` overrides
    the render options by their JS names (camelCase or snake_case, e.g. ``backdrop_motion``).
    """

    starless: list[np.ndarray]
    stars: list[np.ndarray]
    wcs: dict
    target: Optional[dict] = None
    target_type: str = "nebula"
    distance: Optional[float] = None
    distance_source: str = "typed"
    distance_label: str = ""
    gaia: str = "cache"
    presets: list[str] = field(default_factory=lambda: ["youtube_1080"])
    duration: float = 8.0
    fps: int = 30
    loop: str = "none"
    orientation: str = "landscape"
    star_quality: str = "high"
    quality: str = "high"
    dynamic: str = "sdr"
    video: bool = False
    music_path: str = ""
    music_fade: bool = True
    ffmpeg: Optional[str] = None
    format: Optional[str] = None   # the video format id (encode.VIDEO_FORMATS); None: H.264, or HEVC for HDR
    icc: Optional[bytes] = None
    detections: Optional[list] = None
    object_name: str = ""
    fwhm: Optional[float] = None
    options: dict = field(default_factory=dict)
    out_dir: str = "."
    logo_path: str = ""            # the logo file drawn over every frame ("": none); PNG, TIFF or FITS (loomsiril.logo)
    logo_place: str = "off"        # "off" or one of logo.LOGO_PLACES
    logo_opacity: float = 1.0      # 0-1 (the dialog's slider is 0-100 %)
    logo_delay: float = 0.0        # fade the logo in after this many seconds (over one second); 0: from the start


@dataclass
class Result:
    """What a render did: frames written and reused, the videos or the commands that would make them,
    the distance and where it came from, the star counts and the notes for the report."""

    written: int = 0
    reused: int = 0
    cancelled: bool = False
    videos: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    distance: Optional[float] = None
    distance_source: str = ""
    distance_label: str = ""
    counts: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    ms_per_mp: Optional[float] = None   # render speed measured on the frames drawn now (SDR-equivalent), for the dialog's estimate
    render_ms: float = 0.0              # time spent drawing and writing the frames drawn now, and their megapixels
    render_mp: float = 0.0


@dataclass
class BuiltScene:
    """``build_scene``'s result: the scene and the star counts (detected, placed, blended, backdrop)."""

    scene: Scene
    counts: dict


def _or(value: Any, default: Any) -> Any:
    """JS ``x || d``: the default for None, NaN, 0 and ""."""
    if value is None or value == "":
        return default
    if isinstance(value, float) and (math.isnan(value) or value == 0.0):
        return default
    if value is False or value == 0:
        return default
    return value


def _camel(key: str) -> str:
    """``backdrop_motion`` -> ``backdropMotion``; a camelCase key is returned as it is."""
    parts = key.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _projector(wcs: Mapping[str, Any]) -> Callable[[float, float], Optional[Any]]:
    """Sky.projector for a keyword WCS: (ra, dec) -> Point, or None on the far side of the sky (cos c <= 0)."""
    a0 = math.radians(wcs["crval1"])
    d0 = math.radians(wcs["crval2"])

    def project(ra: float, dec: float) -> Optional[Any]:
        a, d = math.radians(ra), math.radians(dec)
        cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
        if not cosc > 0:          # NaN fails this too, as the JS !(a > b) does
            return None
        return tan_project(ra, dec, wcs)

    return project


def _field(wcs: Mapping[str, Any], w: int, h: int) -> tuple[dict, float]:
    """Sky.field: the image centre {ra, dec} and the radius (deg) to its farthest corner."""
    centre = tan_unproject(w / 2, h / 2, wcs)
    radius = 0.0
    for x, y in ((0, 0), (w, 0), (0, h), (w, h)):
        radius = max(radius, separation(centre, tan_unproject(x, y, wcs)))
    return {"ra": centre["ra"], "dec": centre["dec"]}, radius


@dataclass(frozen=True)
class RunContext:
    """What the steps of one run share: the progress callback, the cancel check and the online fetch.

    ``fetch_text`` None is the real fetch (gaia.fetch_text); the tests pass a stub.
    """

    progress: ProgressFn
    is_cancelled: Callable[[], bool]
    fetch_text: Optional[Callable[[str], str]] = None


def _gaia_kind(spec: str) -> tuple[str, Optional[str]]:
    """The gaia source name and TSV path of a job's ``gaia`` value ("cache", "online" or "tsv:PATH")."""
    if spec in ("cache", "online"):
        return spec, None
    if isinstance(spec, str) and spec.startswith("tsv:") and len(spec) > 4:
        return "tsv", spec[4:]
    raise ValueError(f"unknown Gaia source {spec!r} (use cache, online or tsv:PATH)")


def _gaia_sources(job: FlyJob, query: gaia.GaiaQuery, ctx: RunContext) -> list[dict]:
    """Sky.querySources for one cone: from the job's Gaia source (cache, online through ctx.fetch_text, or a TSV)."""
    kind, path = _gaia_kind(job.gaia)
    kwargs: dict[str, Any] = {"source": kind, "tsv_path": path}
    if ctx.fetch_text is not None:
        kwargs["fetch_text"] = ctx.fetch_text
    return gaia.sources(query, **kwargs)


def cluster_distance(sources: Sequence[dict], target: Mapping[str, Any], radius_deg: float) -> Optional[dict]:
    """Fly.findCluster for a target: the ionising cluster's distance from the Gaia sources round it.

    ``sources`` are the Gaia stars within 3 x the cluster radius of the target (Sky.clusterSources);
    ``radius_deg`` the target's radius (its diameter / 120 when catalogued, else the field radius).
    Returns the cluster record (its ``distance`` in pc) or None.
    """
    diameter = target.get("diameter")
    r = diameter / 120 if (diameter is not None and diameter > 0) else radius_deg
    return parallax.find_cluster(sources, target, parallax.cluster_radius(r))


def _shrink_plane(plane: np.ndarray, f: float) -> np.ndarray:
    """Render.shrinkPatch on a whole plane: the area average by f, float32 (h', w')."""
    h, w = plane.shape
    return resample.shrink_patch(plane, 0, 0, w, h, f, w, h)["buf"]


def working_copy(starless: list[np.ndarray], stars: list[np.ndarray], wcs: dict,
                 fwhm: Optional[float] = None) -> dict[str, Any]:
    """Sky.workingCopy (status R): an image larger than preset_mod.working_scale says is area-shrunk once, up front.

    Returns ``{starless, stars, wcs, scale, fwhm}``; ``scale`` 1 (nothing done) or the factor. The
    WCS is scaled with wcs.scaled_wcs; a FWHM given in pixels is scaled too.
    """
    h, w = starless[0].shape
    f = preset_mod.working_scale(w, h)
    if f >= 1:
        return {"starless": starless, "stars": stars, "wcs": wcs, "scale": 1.0, "fwhm": fwhm}
    return {
        "starless": [_shrink_plane(p, f) for p in starless],
        "stars": [_shrink_plane(p, f) for p in stars],
        "wcs": wcs_mod.scaled_wcs(wcs, f),
        "scale": f,
        "fwhm": fwhm * f if fwhm is not None else None,
    }


def clamp_to_image(image: Sequence[np.ndarray], starless: Sequence[np.ndarray]) -> list[np.ndarray]:
    """Sky.splitStars clamp (Sky.js 543-567): starless := min(starless, image), per channel.

    Used by the CLI when --image is given. The JS then always derives stars := unscreen(image, starless)
    (Steps.deriveStarsByUnscreen); this port keeps the --stars file as given instead (deviation D2 in
    docs/parity/e6.md). The reason is not the LUT: image16 is rounded separately from its two layers, so
    screen(S16, T16) and image16 differ by up to 1.33 LSB on a noise-free field, and deriving the stars
    would move frame 0 off screen(S16, T16) by that much (the frame-0 acceptance check is run against the given layers).
    """
    return [np.minimum(np.asarray(s, dtype=np.float32), np.asarray(i, dtype=np.float32))
            for s, i in zip(starless, image)]


@dataclass
class SceneRequest:
    """build_scene's inputs: the two layers, the projection, the Gaia sample, the aim, the distance and the FWHM.

    ``target`` is the scene's aim (the camera's middle: the image centre in the JS); ``D`` the distance (inf
    for a galaxy); ``detections`` the star detections the stars layer is cut by (None: found again).
    """

    starless: Sequence[np.ndarray]
    stars: Sequence[np.ndarray]
    project: Callable[..., Any]
    sources: Sequence[dict]
    target: Mapping[str, float]
    D: float
    fwhm: Optional[float]
    detections: Optional[Sequence[Any]] = None


def build_scene(req: SceneRequest, stage: Optional[ProgressFn] = None) -> BuiltScene:
    """Fly.placeStars + Sky.sprites + Render.scene: the scene a render draws (FlyThrough.js build, 463-509).

    ``neighbours`` holds the SAME source dicts as the placed records (starlayer matches by identity), so
    the sources are never copied between the two lists.
    """
    starless, stars, project, sources = req.starless, req.stars, req.project, req.sources
    target, D, fwhm, detections = req.target, req.D, req.fwhm, req.detections
    h, w = np.shape(starless[0])
    placed: list[Placed] = parallax.place_stars(sources, project, w, h)
    neighbours: list[dict] = []
    for s in sources:
        q = project(s["ra"], s["dec"])
        if q is not None and q.x >= 0 and q.y >= 0 and q.x < w and q.y < h:
            neighbours.append({"source": s, "x": q.x, "y": q.y})
    layer = starlayer.sprites(stars, placed, fwhm, neighbours, stage, detections)
    if stage:
        stage("Putting the scene together", 0, 0)
    catalogue = [{"x": n["x"], "y": n["y"], "G": n["source"].get("G"), "ra": n["source"]["ra"],
                  "dec": n["source"]["dec"]} for n in neighbours]
    sc = scene_mod.scene(scene_mod.SceneInputs(starless, stars, layer.sprites, project, dict(target), D,
                                               residual=layer.residual, catalogue=catalogue))
    counts = {"detected": layer.detected, "placed": len(layer.sprites), "blended": layer.blended,
              "backdrop": len(neighbours) - len(layer.sprites)}
    return BuiltScene(scene=sc, counts=counts)


def loop_image(pr: PresetRender, i: int, opts: RenderOptions, cache: Optional[dict] = None) -> list[np.ndarray]:
    """FlyThrough.loopImage: frame i of the preset's n-frame crossfade loop fading over F frames (preset_mod.loop_frame).

    ``pr`` gives the scene, n, F, the crop and the frame size (spec w, h). One render, or two mixed. The
    pre-roll the fade dissolves into is frame 0 when the camera rests before the start (any easing but
    linear); with a ``cache`` dict it is rendered once and kept in cache["still"].
    """
    scene, w, h, crop = pr.scene, pr.spec["w"], pr.spec["h"], pr.crop
    f = preset_mod.loop_frame(i, pr.n, pr.F)
    img = render_frame(scene, f["a"], opts, w, h, crop)
    if f["alpha"] >= 1:
        return img
    still = cache is not None and f["b"] is not None and f["b"] <= 0 and opts.easing != "linear"
    if still:
        if "still" not in cache:
            cache["still"] = render_frame(scene, 0.0, opts, w, h, crop)
        b = cache["still"]
    else:
        b = render_frame(scene, f["b"], opts, w, h, crop)
    return scene_mod.blend(img, b, f["alpha"])


def _record_matches(folder: str, signature: str) -> bool:
    """FlyThrough.recordMatches: were the folder's frames made with ``signature``?"""
    record = os.path.join(folder, FRAMES_RECORD)
    try:
        with open(record, encoding="utf-8") as fh:
            return fh.read() == signature
    except OSError:
        return False


def _frames_ready(folder: str, signature: str, n: int) -> bool:
    """FlyThrough.framesReady: the record matches and all n frames are there."""
    if not _record_matches(folder, signature):
        return False
    return all(os.path.exists(preset_mod.frame_path(folder, i)) for i in range(n))


def is_partial_frame(name: str) -> bool:
    """FlyThrough.isPartialFrame: a frame caught half-written (``frame_00007.tif.part``)."""
    return len(name) > len(PARTIAL_SUFFIX) and name.endswith(PARTIAL_SUFFIX) and preset_mod.is_frame_file(name[:-len(PARTIAL_SUFFIX)])


def _remove_quietly(path: str) -> None:
    """FlyThrough.removeQuietly: remove a file if it is there; never fail."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _prepare_folder(folder: str, keep_frames: bool) -> None:
    """FlyThrough.prepareFolder: create the folder; empty it of OUR frames (all, or only half-written ones when resuming)."""
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        if is_partial_frame(name) or (not keep_frames and preset_mod.is_frame_file(name)):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def _preset_folder_names() -> list[str]:
    """The preset folders of an output directory: each preset, and its portrait twin (``<id>_vertical``)."""
    names: list[str] = []
    for pid in preset_mod.PRESET_ORDER:
        names += [pid, pid + "_vertical"]
    return names


def _loom_entries(folder: str) -> list[str]:
    """The entries of a preset folder that are Loom's: finished frames, half-written frames and the record."""
    return [e for e in os.listdir(folder) if preset_mod.is_frame_file(e) or is_partial_frame(e) or e == FRAMES_RECORD]


def _remove_if_empty(folder: str) -> None:
    """Remove a folder that is empty now; never fails."""
    if os.listdir(folder):
        return
    try:
        os.rmdir(folder)
    except OSError:
        pass


def clear_frames(out_dir: str, count_only: bool = False) -> dict[str, int]:
    """FlyThrough.clearFrames: Loom's frames, half-written frames and records in every preset folder of out_dir.

    Only those files go; a folder left empty goes too. ``count_only`` counts and deletes nothing. Returns
    ``{frames, folders}``: the finished frames found and the preset folders holding Loom's files.
    """
    frames = 0
    folders = 0
    for name in _preset_folder_names():
        folder = os.path.join(out_dir, name)
        if not os.path.isdir(folder):
            continue
        ours = _loom_entries(folder)
        if not ours:
            continue
        folders += 1
        frames += sum(1 for e in ours if preset_mod.is_frame_file(e))
        if count_only:
            continue
        for e in ours:
            _remove_quietly(os.path.join(folder, e))
        _remove_if_empty(folder)
    return {"frames": frames, "folders": folders}


def _render_options(po: Mapping[str, Any]) -> RenderOptions:
    """The frame renderer's options from a preset's options (the JS ``opts`` of Render.frame)."""
    return RenderOptions(
        travel=float(po["travel"]),
        easing=str(po["easing"]),
        growth=float(po["growth"]),
        brightening=bool(po["brightening"]),
        kernel=str(po.get("kernel") or "bicubic"),
        backdrop_motion=float(po["backdropMotion"]),
        star_quality=po["starQuality"],
        star_saturation=float(po["starSaturation"]),
        bloom=float(po["bloom"]),
        twinkle=float(po["twinkle"]),
        motion_blur=bool(po["motionBlur"]),
        frame_dt=float(po.get("frameDt") or 0.0),
        duration=float(po["duration"]),
        output=po["output"],
        star_hdr=bool(po["starHdr"]),
        peak=po.get("peak"),
        logo=po.get("logo"),
        logo_delay=float(po.get("logoDelay") or 0.0),
    )


def _preset_options(opts: Mapping[str, Any], preset: str) -> dict[str, Any]:
    """FlyThrough.presetOptions: a preset's own copy of the options, with its transfer and output transform.

    SDR is Rec.709 from the image's colour; HDR is BT.2020 with the preset's transfer (HDR_DEFAULT_TRANSFER),
    and a fresh output transform per preset so a PQ preset measures its own MaxCLL/MaxFALL.
    """
    hdr = opts.get("dynamic") == "hdr"
    chosen = (opts.get("hdrTransfer") or {}).get(preset)       # opts.hdrTransfer[id], else HDR_DEFAULT_TRANSFER (FlyThrough.js 225)
    transfer = ((chosen if chosen in ("hlg", "pq") else None) or HDR_DEFAULT_TRANSFER.get(preset, "hlg")) if hdr else "sdr"
    peak = _or(opts.get("peak"), encode.HDR_PEAK_DEFAULT)
    out = dict(opts)
    out["transfer"] = transfer
    src = opts.get("colour") or colour.SRGB_COLOUR
    if transfer == "sdr" and _is_srgb(src):
        # sRGB to sRGB is the identity by definition: skipping it avoids a 0.03-0.08 LSB LUT error (the JS
        # always calls Fly.outputTransform; deviation D1 in docs/parity/e6.md, harmless either way)
        out["output"] = None
    else:
        out["output"] = colour.output_transform(src, transfer, {"peak": peak})
    return out


def _is_srgb(c: Mapping[str, Any]) -> bool:
    """Whether a colour description is sRGB: the built-in one, or a parametric sRGB profile with its matrix and curve."""
    if c is colour.SRGB_COLOUR:
        return True
    if c.get("gray") or c.get("matrix") is None:
        return False
    if not np.allclose(c["matrix"], colour.SRGB_COLOUR["matrix"], atol=1e-4):
        return False
    return all(t.get("type") == "para" and t.get("fn") == 3
               and np.allclose(t.get("p", []), colour.SRGB_CURVE["p"], atol=1e-6) for t in c["trc"])


@dataclass
class PresetRender:
    """One preset's render, the parameter object of the preset path (was 7-9 loose arguments).

    ``spec`` the preset (preset_mod.preset_spec), ``n`` its frame count, ``F`` the crossfade frames (0 when
    none), ``scene`` the scene quality-scaled and turned for it, ``po`` its options (transfer, output
    transform, frameDt), ``crop`` the crop of the scene to its frame size. ``folder`` holds its frames,
    ``out_base`` is its video's path without the ffmpeg arguments (the last argument of the encode).
    """

    spec: Mapping[str, Any]
    n: int
    F: int
    scene: Scene
    po: dict[str, Any]
    crop: dict[str, Any]
    folder: str
    out_base: str


def _preset_job(scene: Scene, spec: Mapping[str, Any], opts: Mapping[str, Any], out_dir: str) -> PresetRender:
    """FlyThrough.presetJob: a preset's frame count, scene (quality-scaled, turned), crop and options, and where its files go."""
    n = preset_mod.frame_count(opts["duration"], opts["fps"], spec["pingPong"])
    ps = _quality_scene(scene, opts, spec)
    F = preset_mod.crossfade_frames(opts["duration"], opts["fps"]) if spec["crossfade"] else 0
    po = _preset_options(opts, spec["preset"])
    po["frameDt"] = preset_mod.frame_step(n, spec["pingPong"])
    po["logo"] = logo_mod.layer_for(opts, spec["w"], spec["h"], scene.nc)   # the logo for this preset's frame size
    crop = preset_mod.preset_crop(ps.w, ps.h, ps.tp.x, ps.tp.y, spec["w"], spec["h"])
    video_name = encode.video_name(opts.get("objectName"), spec["id"], po["transfer"])
    return PresetRender(spec=spec, n=n, F=F, scene=ps, po=po, crop=crop,
                        folder=os.path.join(out_dir, spec["id"]), out_base=os.path.join(out_dir, video_name))


def _quality_scene(scene: Scene, opts: Mapping[str, Any], spec: Mapping[str, Any]) -> Scene:
    """FlyThrough.qualityScene: the scene turned for the preset, and shrunk toward its size at High and Medium."""
    ps = scene_for(scene, spec["w"], spec["h"])
    crop = preset_mod.preset_crop(ps.w, ps.h, ps.tp.x, ps.tp.y, spec["w"], spec["h"])
    d = preset_mod.quality_scale(opts.get("starQuality"), crop["w"], spec["w"])
    if d < 0.999:
        return scene_mod.scaled_scene(ps, d)
    return ps


def _frame_planes(pr: PresetRender, i: int, ro: RenderOptions, cache: dict[str, Any]) -> list[np.ndarray]:
    """Frame i of a preset: the crossfade loop's frame when the preset loops by crossfade, else the flight at its time."""
    spec = pr.spec
    if spec["crossfade"]:
        return loop_image(pr, i, ro, cache)
    return render_frame(pr.scene, preset_mod.time_at(i, pr.n, spec["pingPong"]), ro, spec["w"], spec["h"], pr.crop)


def _render_frames(pr: PresetRender, res: Result, ctx: RunContext, total: int, resume: bool) -> None:
    """FlyThrough.renderFrameRange: frames i = 0..n-1 as five-digit TIFFs; resumed frames are kept, cancel stops."""
    icc = _frame_icc(pr.po["transfer"])
    cache: dict[str, Any] = {}
    ro = _render_options(pr.po)
    for i in range(pr.n):
        path = preset_mod.frame_path(pr.folder, i)
        if resume and os.path.exists(path):
            res.reused += 1
            continue
        if ctx.is_cancelled():
            res.cancelled = True
            return
        t0 = time.monotonic()
        planes = _frame_planes(pr, i, ro, cache)
        write_tiff16(path, planes, icc)            # write_tiff16 writes <path>.part and renames it
        res.render_ms += (time.monotonic() - t0) * 1000.0
        res.render_mp += pr.spec.get("w", 0) * pr.spec.get("h", 0) / 1e6
        res.written += 1
        ctx.progress("Rendering " + pr.spec["id"] + " frames", res.written + res.reused, total)


def _frame_icc(transfer: str) -> Optional[bytes]:
    """The frame's ICC profile: sRGB for SDR frames, none for HDR (FlyThrough.renderFrames, 15846-15850 in the JS tests)."""
    return colour.srgb_icc_bytes() if transfer == "sdr" else None


def _encode_args(pr: PresetRender, opts: Mapping[str, Any]) -> list[str]:
    """FlyThrough.encodeArgs: the ffmpeg arguments for a preset's frames (HDR tags and metadata when HDR)."""
    po = pr.po
    transfer = po["transfer"]
    hdr = None
    if colour.is_hdr(transfer):
        stats = po["output"].stats
        hdr = {"transfer": transfer, "peak": _or(po.get("peak"), encode.HDR_PEAK_DEFAULT),
               "maxCll": stats["max_cll"], "maxFall": stats["max_fall"]}
    fmt = fly_video.encode_format(opts.get("format"), hdr is not None)   # opts.format || (hdr ? "hevc" : "h264")
    return encode.ffmpeg_args(pr.folder, opts["fps"], pr.out_base, fmt, opts.get("quality") or "high", hdr,
                              _audio(pr, opts))


def _audio(pr: PresetRender, opts: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The music of a preset's video (FlyThrough.encodeArgs): ``{path, fade, duration, loop}`` or None without music.

    The music lasts the clip (frames / fps); a looping preset (back and forth, or a crossfade; the Exhibition always
    loops) gets its music crossfaded end into start (encode.loop_audio)."""
    music = opts.get("music") or {}
    path = str(music.get("path") or "").strip()
    if not path:
        return None
    return {"path": path, "fade": bool(music.get("fade")), "duration": pr.n / opts["fps"],
            "loop": bool(pr.spec["pingPong"] or pr.spec["crossfade"])}


def _finish_preset(pr: PresetRender, res: Result, ctx: RunContext, opts: Mapping[str, Any]) -> None:
    """FlyThrough.finishPreset: the video beside the frames' folder, or the command that would make it."""
    args = _encode_args(pr, opts)
    platform = encode.platform_name(sys.platform)
    if not opts.get("video") or not opts.get("ffmpeg"):
        res.commands.append(encode.command_line(opts.get("ffmpeg") or "ffmpeg", args, platform))
        return
    video = args[-1]
    _remove_quietly(video)                          # no stale video that looks current
    chunks: list[str] = []

    def on_output(chunk: str) -> None:
        chunks.append(chunk)
        k = encode.ffmpeg_frame_progress(chunk)
        if k is not None:
            ctx.progress("Encoding " + pr.spec["id"], min(k, pr.n), pr.n)

    if encode.encode(opts["ffmpeg"], args, ctx.is_cancelled, on_output):
        res.videos.append(video)
    elif ctx.is_cancelled():
        res.cancelled = True
        _remove_quietly(video)                      # a partial file is not a video
    else:
        res.failed.append({"video": video, "output": "".join(chunks),
                           "command": encode.command_line(opts["ffmpeg"], args, platform)})


@dataclass(frozen=True)
class FinalRender:
    """render_final's inputs: the scene, the preset ids, the render options (the JS ``opts``) and the output folder."""

    scene: Scene
    presets: Sequence[str]
    opts: Mapping[str, Any]
    out_dir: str


def render_final(req: FinalRender, ctx: RunContext) -> Result:
    """FlyThrough.renderFinal: every frame of every preset under req.out_dir, then each preset's video (or its command).

    Frames go to ``out_dir/<preset id>/frame_%05d.tif``; the video beside that folder, ``out_dir/<videoName>``.
    Frames made from the same options are reused (preset_mod.frame_signature in frames.json). ``ctx.is_cancelled``
    stops after the current frame and keeps what was written, with no video.
    """
    scene, presets, opts, out_dir = req.scene, req.presets, req.opts, req.out_dir
    orient = _ORIENTATION_JS.get(opts.get("orientation", "horizontal"), opts.get("orientation", "horizontal"))
    specs = [preset_mod.preset_spec(p, orient, opts.get("loop", "none")) for p in presets]
    res = Result()
    total = sum(preset_mod.frame_count(opts["duration"], opts["fps"], s["pingPong"]) for s in specs)
    scene_key = str(opts.get("sceneKey", ""))
    sig_opts = {k: v for k, v in opts.items() if k not in ("colour", "sceneKey")}
    for spec in specs:
        if res.cancelled:
            break
        pr = _preset_job(scene, spec, opts, out_dir)
        if ctx.is_cancelled():                       # before touching this preset's folder
            res.cancelled = True
            break
        # frames made from the same options are kept: only the encode changed (preset_mod.frame_signature)
        signature = preset_mod.frame_signature(sig_opts, spec, scene_key)
        if _frames_ready(pr.folder, signature, pr.n):
            res.reused += pr.n
        else:
            # a render stopped part way with the same options is resumed: its frames are kept
            resume = _record_matches(pr.folder, signature)
            _prepare_folder(pr.folder, resume)
            # the record goes first, so the frames written from here on can be resumed too
            with open(os.path.join(pr.folder, FRAMES_RECORD), "w", encoding="utf-8") as fh:
                fh.write(signature)
            _render_frames(pr, res, ctx, total, resume)
        if not res.cancelled:
            _finish_preset(pr, res, ctx, opts)
    if res.render_mp > 0:   # FlyThrough.estimateMs: HDR measured ~1.3x SDR, so the speed kept is the SDR one
        res.ms_per_mp = res.render_ms / res.render_mp / (fly_video.ESTIMATE_HDR_FACTOR if opts.get("dynamic") == "hdr" else 1)
    return res


def _validate_options(job: FlyJob) -> None:
    """Raise a clear ValueError for a job whose scalar options the renderer does not make."""
    if job.target_type not in ("nebula", "galaxy"):
        raise ValueError(f"unknown target type {job.target_type!r} (use nebula or galaxy)")
    if not (job.duration > 0) or job.duration > MAX_DURATION:
        raise ValueError(f"duration must be above 0 and at most {MAX_DURATION:g} s")
    if not job.fps or job.fps < 1:
        raise ValueError("fps must be at least 1")
    if job.loop not in ("none", "pingpong", "crossfade"):
        raise ValueError(f"unknown loop {job.loop!r} (use none, pingpong or crossfade)")
    if job.orientation not in _ORIENTATION_JS:
        raise ValueError(f"unknown orientation {job.orientation!r} (use landscape or portrait)")
    if job.star_quality not in preset_mod.STAR_QUALITY:
        raise ValueError(f"unknown star quality {job.star_quality!r} (use highest, high or medium)")
    if job.dynamic not in ("sdr", "hdr"):
        raise ValueError(f"unknown dynamic range {job.dynamic!r} (use sdr or hdr)")


def _validate_presets(job: FlyJob) -> None:
    """Raise a ValueError unless every preset exists and its clip fits the five-digit frame files."""
    if not job.presets:
        raise ValueError("no preset given")
    for p in job.presets:
        if p not in preset_mod.PRESETS:
            raise ValueError(f"unknown preset {p!r}")
        spec = preset_mod.preset_spec(p, _ORIENTATION_JS[job.orientation], job.loop)
        n = preset_mod.frame_count(job.duration, job.fps, spec["pingPong"])
        if n >= MAX_FRAMES:
            raise ValueError(f"a clip of {n} frames is too long: frame files take five digits (under {MAX_FRAMES})")


def _validate(job: FlyJob) -> None:
    """Raise a clear ValueError for a job the renderer cannot make."""
    _validate_options(job)
    _validate_presets(job)
    _gaia_kind(job.gaia)


def _logo_opts(job: FlyJob) -> dict[str, Any]:
    """The job's logo as render options (logoPath, logoPlace, logoOpacity, logoDelay); ``job.options`` (by either spelling) win."""
    out: dict[str, Any] = {"logoPath": job.logo_path, "logoPlace": job.logo_place, "logoOpacity": job.logo_opacity,
                           "logoDelay": job.logo_delay}
    out.update({k: v for k, v in ((_camel(k), v) for k, v in job.options.items()) if k.startswith("logo")})
    return out


def _validate_job(job: FlyJob) -> None:
    """The shape checks: the starless and stars layers have the same channels and the same size."""
    if len(job.starless) != len(job.stars) or not job.starless:
        raise ValueError("the starless and stars layers need the same number of channels")
    if np.shape(job.starless[0]) != np.shape(job.stars[0]):
        raise ValueError("the starless and stars layers differ in size")
    logo_mod.attach(_logo_opts(job))   # a missing or unreadable logo stops the run before Gaia, as it does in the dialog


@dataclass(frozen=True)
class SceneTerms:
    """The scene's inputs besides the planes: the wcs, the aim (``target``), the distance D, the FWHM and the detection count."""

    wcs: Mapping[str, Any]
    target: Mapping[str, Any]
    D: float
    fwhm: Optional[float]
    n_detections: int


def _scene_key(layers: Sequence[np.ndarray], terms: SceneTerms) -> str:
    """A key for the scene's inputs: frames made from other inputs are never reused (frame signature).

    ``layers`` is the starless planes followed by the stars planes (the order the key is made in).
    """
    h = hashlib.sha256()
    for p in layers:
        h.update(np.ascontiguousarray(p, dtype=np.float32).tobytes())
    h.update(json.dumps([dict(terms.wcs), dict(terms.target), repr(terms.D), terms.fwhm, terms.n_detections],
                        sort_keys=True, default=str).encode())
    return h.hexdigest()


def _working_inputs(job: FlyJob, notes: list[str]) -> dict[str, Any]:
    """Sky.workingCopy for the job, plus the detections it keeps: a resampled image has its stars found again."""
    wk = working_copy(list(job.starless), list(job.stars), job.wcs, job.fwhm)
    detections = job.detections
    if wk["scale"] < 1:
        notes.append(f"the image was resampled to its working size (x{wk['scale']:.3f}); detections are found again")
        detections = None
    wk["detections"] = detections
    return wk


@dataclass
class SearchTrace:
    """What one distance lookup looked at, kept so that "nothing found" can say why (see :func:`search_text`).

    ``target`` the position searched round (FlyThrough.clusterDistance's ``target``); ``cluster_cone`` the radius (deg)
    of the Gaia cone the cluster finder was given (3 x the cluster radius, Sky.clusterSources) and ``cluster_radius``
    its inside radius; ``cluster_sources`` / ``light_sources`` the Gaia stars the two searches read (the field's own
    sample and the small cone round the target)."""

    target: Optional[Mapping[str, float]] = None
    cluster_cone: float = 0.0
    cluster_radius: float = 0.0
    light_radius: float = 0.0
    cluster_sources: Sequence[dict] = ()
    light_sources: Sequence[dict] = ()


def _cluster_for_target(job: FlyJob, target: Mapping[str, float], field_radius: float,
                        ctx: RunContext, trace: Optional[SearchTrace] = None) -> Optional[dict]:
    """Sky.clusterSources and Fly.findCluster: the ionising cluster of the target, or None (a reflection nebula)."""
    cluster_r = target.get("diameter")
    r = cluster_r / 120 if (cluster_r is not None and cluster_r > 0) else field_radius
    cone = 3 * parallax.cluster_radius(r)
    # Sky.clusterSources: a sample of Gaia round the target, out to three cluster radii
    cl_sources = _gaia_sources(job, gaia.GaiaQuery.from_centre(target, cone, GAIA_MAG_LIMIT), ctx)
    if trace is not None:
        trace.target, trace.cluster_cone, trace.cluster_radius = target, cone, parallax.cluster_radius(r)
        trace.cluster_sources = cl_sources
    return cluster_distance(cl_sources, target, r)


def _illuminator_for_target(job: FlyJob, sources: Sequence[dict], target: Mapping[str, float],
                            ctx: RunContext, trace: Optional[SearchTrace] = None) -> Optional[dict]:
    """The bright star that lights a reflection nebula: from the field's sample, else an online search round the target."""
    star = parallax.illuminator_distance(sources, target)
    seen: list[dict] = list(sources)
    kind, _ = _gaia_kind(job.gaia)
    if star is None and kind != "tsv":
        online = _gaia_sources(job, gaia.GaiaQuery.from_centre(target, parallax.illuminator_radius(target),
                                                               parallax.ILLUMINATOR_MAX_G), ctx)
        seen += online
        star = parallax.illuminator_distance(online, target)
        if star is not None and kind == "online":
            star["online"] = True
    if trace is not None:
        trace.target, trace.light_radius, trace.light_sources = target, parallax.illuminator_radius(target), seen
    return star


def search_text(trace: SearchTrace, field_radius: float, where: str) -> str:
    """Why nothing was found, with the numbers: what was searched where, how many Gaia stars, how many were usable.

    "searched 1.35 deg round RA 334.8558, Dec +56.0278 (the image field is 0.93 deg): 116,639 Gaia sources (from Gaia online),
    36,967 with a usable parallax: no cluster of >= 30 members (4,075 stars within 0.45 deg, 27,782 in the ring round
    it) and no star brighter than G 10 with a good parallax within 0.1 deg of the target (the nearest, G 8.8, is
    0.135 deg away)". ``where``: the source in words ("Gaia online", "the Gaia cache", "the Gaia cache and Gaia online", "the Gaia table")."""
    t = trace.target
    if t is None:
        return "nothing was searched"
    pos = f"RA {t['ra']:.4f}, Dec {t['dec']:+.4f}"
    head = f"searched {trace.cluster_cone:.2f} deg round {pos} (the image field is {field_radius:.2f} deg)"
    n = len(trace.cluster_sources)
    if n == 0:
        return f"{head}: {where} returned no stars there"
    usable = sum(1 for s in trace.cluster_sources if parallax.usable_parallax(s) is not None)
    samples = parallax.cluster_samples(trace.cluster_sources, t, trace.cluster_radius)
    nearest = None
    for s in list(trace.cluster_sources) + list(trace.light_sources):
        g = s.get("G")
        if g is not None and g < parallax.ILLUMINATOR_MAX_G and parallax.usable_parallax(s) is not None:
            sep = separation(s, t)
            if nearest is None or sep < nearest[1]:
                nearest = (float(g), sep)
    hint = f" (the nearest, G {nearest[0]:.1f}, is {nearest[1]:.3f} deg away)" if nearest else ""
    return (f"{head}: {n:,} Gaia sources (from {where}), {usable:,} with a usable parallax: no cluster of >= "
            f"{parallax.CLUSTER_MIN_MEMBERS} members ({len(samples['inside']):,} stars within {trace.cluster_radius:.2f} deg, "
            f"{len(samples['field']):,} in the ring round it) and no star brighter than G {parallax.ILLUMINATOR_MAX_G:g} "
            f"with a good parallax within {trace.light_radius:.2f} deg of the target{hint}")


@dataclass(frozen=True)
class DistanceFound:
    """What FlyThrough.findDistance found: ``distance`` (pc, None when none), its ``source`` ("galaxy", "typed",
    "cluster", "star"; "" when none), the ``cluster`` record or the illuminating ``star`` record that gave it, and
    ``label``: the source in words (see :func:`distance_label`). ``note``: an extra fact to show (the Gaia cache was
    empty and Gaia online answered); ``reason``: why none was found, in plain language, with what was searched
    (:func:`search_text`); ``searched``: the :class:`SearchTrace` behind it."""

    distance: Optional[float]
    source: str
    cluster: Optional[dict] = None
    star: Optional[dict] = None
    label: str = ""
    note: str = ""
    reason: str = ""
    searched: Optional[SearchTrace] = None


def distance_label(source: str, cluster: Optional[Mapping[str, Any]] = None, star: Optional[Mapping[str, Any]] = None,
                   online: bool = False) -> str:
    """FlyThrough.describeDistance, short: "cluster of 37 stars, Gaia", "illuminating star G 7.3, Gaia online",
    "galaxy (fixed backdrop)", "typed"."""
    where = "Gaia online" if online else "Gaia"
    if source == "cluster" and cluster is not None:
        n = cluster.get("members")
        return f"cluster of {int(n)} stars, {where}" if n else f"star cluster, {where}"
    if source == "star" and star is not None:
        g = star.get("G")
        names = "Gaia online" if online or star.get("online") else "Gaia"
        return f"illuminating star G {g:.1f}, {names}" if isinstance(g, (int, float)) else f"illuminating star, {names}"
    if source == "galaxy":
        return "galaxy (fixed backdrop)"
    return source


def distance_text(distance: float, label: str) -> str:
    """The distance as the dialog, the progress window and the final text say it: "Distance: ~1,950 pc (typed)"."""
    if distance == float("inf"):
        return f"Distance: {label}"
    if label == "typed":
        plain = f"{distance:.0f}" if distance == int(distance) else f"{distance:g}"
        return f"Distance: {plain} pc (typed)"
    return f"Distance: ~{distance:,.0f} pc ({label})"


def gaia_failure(exc: Exception) -> str:
    """Gaia not answering, in plain language."""
    return f"Gaia could not be reached ({exc})"


def _lookup(job: FlyJob, sources: Sequence[dict], target: Mapping[str, float], field_radius: float,
            ctx: RunContext, trace: Optional[SearchTrace] = None) -> tuple[Optional[dict], Optional[dict]]:
    """The target's cluster, else (a reflection nebula) the bright star that lights it: ``(cluster, star)``."""
    cluster = _cluster_for_target(job, target, field_radius, ctx, trace)
    if cluster is not None:
        return cluster, None
    return None, _illuminator_for_target(job, sources, target, ctx, trace)


def _find_distance(job: FlyJob, sources: Sequence[dict], target: Mapping[str, float], field_radius: float,
                   ctx: RunContext) -> DistanceFound:
    """:func:`_resolve_distance` with the records and the label: the ONE lookup the CLI, the Draft, the Render and the
    dialog's preview all use.

    A galaxy is inf; a given ``job.distance`` wins; else the target's cluster, else the illuminating star, from the
    job's Gaia source. With Gaia ``cache`` that finds nothing without having asked online (every cone was in the cache,
    or in the cache's files), Gaia online is asked once (``note`` says so). A Gaia failure of that second ask is a
    ``reason``, not an exception; a failure of the first is raised as before.
    """
    kind = _gaia_kind(job.gaia)[0]
    if job.target_type == "galaxy":
        return DistanceFound(float("inf"), "galaxy", label=distance_label("galaxy"))
    if job.distance is not None and job.distance > 0:
        return DistanceFound(float(job.distance), job.distance_source or "typed",
                             label=job.distance_label or distance_label(job.distance_source or "typed"))
    ctx.progress("Finding the nebula's distance from its star cluster", 3, 4)
    fetched = [0]
    real_fetch = ctx.fetch_text or gaia.fetch_text

    def counting_fetch(url: str) -> str:
        fetched[0] += 1
        return real_fetch(url)

    cctx = RunContext(ctx.progress, ctx.is_cancelled, counting_fetch)
    trace = SearchTrace()
    cluster, star = _lookup(job, sources, target, field_radius, cctx, trace)
    used_online = kind == "online" or fetched[0] > 0        # a cache miss asks online by itself (gaia.sources)
    tried = {"online": "Gaia online", "tsv": "the Gaia table"}.get(kind, "Gaia online" if used_online else "the Gaia cache")
    note = ""
    if cluster is None and star is None and kind == "cache" and fetched[0] == 0:
        # every cone came from the local cache and it holds nothing useful here: ask Gaia online once
        try:
            trace = SearchTrace()
            cluster, star = _lookup(replace(job, gaia="online"), sources, target, field_radius, cctx, trace)
        except gaia.GaiaError as exc:
            return DistanceFound(None, "", reason=f"the Gaia cache has nothing for this field and {gaia_failure(exc)}")
        used_online, tried = True, "the Gaia cache and Gaia online"
        note = CACHE_EMPTY_NOTE if (cluster is not None or star is not None) else ""
    if cluster is not None:
        return DistanceFound(float(cluster["distance"]), "cluster", cluster=cluster, note=note,
                             label=distance_label("cluster", cluster, online=used_online))
    if star is not None:
        return DistanceFound(float(star["distance"]), "star", star=star, note=note,
                             label=distance_label("star", star=star, online=used_online))
    return DistanceFound(None, "", reason="no star cluster and no bright illuminating star found: "
                                          + search_text(trace, field_radius, tried), searched=trace)


def _resolve_distance(job: FlyJob, sources: Sequence[dict], target: Mapping[str, float], field_radius: float,
                      ctx: RunContext) -> tuple[Optional[float], str, Optional[dict]]:
    """FlyThrough.findDistance: D (pc, None when none is found), its source, and the cluster record when one was used.

    Galaxy: inf. Else the job's typed distance. Else the target's cluster, else the bright star that lights a
    reflection nebula. Sources: "galaxy", "typed", "cluster", "star"; "" when D is None.
    """
    found = _find_distance(job, sources, target, field_radius, ctx)
    return found.distance, found.source, found.cluster


def _render_opts(job: FlyJob, D: float, colour_info: Mapping[str, Any], notes: list[str]) -> dict[str, Any]:
    """The render options of a run: the Dialog's initial values, the job's own, travel capped to 0.9 x D (a note says so).

    The notes get the travel note, then the colour note, in that order. The job's options come last (by their
    camelCase names), then colourKey, which keys the frame signature by the colour profile.
    """
    travel_opt = job.options.get("travel")
    travel = float(travel_opt) if travel_opt is not None else parallax.default_travel(job.target_type, D)
    capped = parallax.clamp_travel(travel, job.target_type, D)
    if capped["clamped"]:
        notes.append("travel capped at 0.9 x the distance")
    notes.append(colour_info["note"])

    opts: dict[str, Any] = dict(DIALOG_DEFAULTS)
    opts.update({
        "travel": capped["travel"],
        "starQuality": job.star_quality,
        "duration": float(job.duration),
        "fps": int(job.fps),
        "loop": job.loop,
        "orientation": _ORIENTATION_JS[job.orientation],
        "dynamic": job.dynamic,
        "peak": encode.HDR_PEAK_DEFAULT,
        "video": bool(job.video),
        "quality": job.quality,
        "format": job.format,
        "music": {"path": job.music_path, "fade": bool(job.music_fade)},
        "objectName": job.object_name,
        "colour": colour_info["colour"],
    })
    for k, v in job.options.items():
        if _camel(k) != "travel":            # a typed travel was capped above: the capped one stays (FlyThrough.js 1915)
            opts[_camel(k)] = v
    opts["colourKey"] = hashlib.sha256(job.icc or b"srgb").hexdigest()[:16]
    opts.update(_logo_opts(job))
    logo_mod.attach(opts)       # the logo file read once (logoImage); its path, time and size key the frames (logoStamp)
    return opts


def run(job: FlyJob, progress: Optional[ProgressFn] = None, is_cancelled: Optional[Callable[[], bool]] = None,
        fetch_text: Optional[Callable[[str], str]] = None) -> Result:
    """The whole flow (FlyThrough.identify + build + renderFinal): Gaia, distance, scene, frames and video.

    Order: working copy (shrink if the image is larger than the working size) -> the image's field and the
    Gaia sample of it -> the distance (galaxy: inf; job.distance; the target's cluster; the bright star that
    lights a reflection nebula) -> build_scene -> render_final. ``progress(stage, done, total)`` gets the
    stage texts and the frame counts; ``fetch_text`` replaces the online fetch (tests never reach the network).
    """
    ctx = RunContext(progress or (lambda stage, done, total: None), is_cancelled or (lambda: False), fetch_text)
    _validate(job)
    _validate_job(job)
    notes: list[str] = []
    wk = _working_inputs(job, notes)
    starless, stars, wcs, fwhm, detections = wk["starless"], wk["stars"], wk["wcs"], wk["fwhm"], wk["detections"]
    h, w = np.shape(starless[0])

    field_centre, field_radius = _field(wcs, w, h)
    target = dict(job.target) if job.target else dict(field_centre)
    project = _projector(wcs)

    ctx.progress("Looking up the stars and their distances in Gaia", 2, 4)
    sources = _gaia_sources(job, gaia.GaiaQuery.from_centre(field_centre, field_radius, GAIA_MAG_LIMIT), ctx)

    found = _find_distance(job, sources, target, field_radius, ctx)
    D, distance_source = found.distance, found.source
    if D is None:
        # FlyThrough.findDistance leaves D null and Dialog.prepareScene refuses to render without it
        raise NoDistanceError(found.reason)
    ctx.progress(distance_text(D, found.label), 3, 4)
    if ctx.is_cancelled():
        return Result(cancelled=True, distance=D, distance_source=distance_source, distance_label=found.label,
                      notes=notes)

    ctx.progress("Removing the stars and building the scene", 0, 0)
    built = build_scene(SceneRequest(starless, stars, project, sources, field_centre, D, fwhm, detections),
                        ctx.progress)

    colour_info = colour.colour_of(job.icc)
    opts = _render_opts(job, D, colour_info, notes)
    opts["sceneKey"] = _scene_key(list(starless) + list(stars),
                                  SceneTerms(wcs, target, D, fwhm, len(detections or [])))
    ffmpeg = job.ffmpeg
    if job.video and ffmpeg is None:
        ffmpeg = encode.find_ffmpeg()
    if ffmpeg is None:
        opts["video"] = False
    opts["ffmpeg"] = ffmpeg
    if ffmpeg is not None and opts.get("video") and opts.get("format"):
        offered = [f["id"] for f in fly_video.formats_for(fly_video.probe(ffmpeg), job.dynamic == "hdr")]
        if offered and opts["format"] not in offered:
            notes.append(f"this ffmpeg cannot encode {fly_video.format_label(opts['format']) or opts['format']}: "
                         f"{fly_video.format_label(fly_video.encode_format(None, job.dynamic == 'hdr'))} instead")
            opts["format"] = None

    res = render_final(FinalRender(built.scene, job.presets, opts, job.out_dir), ctx)
    res.distance = D
    res.distance_source = distance_source
    res.distance_label = found.label
    res.counts = built.counts
    res.notes = notes + res.notes
    return res
