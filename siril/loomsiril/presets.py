"""Fly-Through's presets, timing, frame naming, signatures and progress (E1b).

Ported from Loom ``script/lib/Fly.js`` (v0.4.1): the star quality levels, the
frame count and timing of a clip, the draft plan, the output presets and their
crops, the loops, the frame signature, the crossfade, the file names of the
frames, the header centre, the typed angles, the progress text and the
working-image rules. Each function's docstring names the JS function it ports.

Dict results keep the JS object's keys (``pingPong``, ``outScale``...) because
they are written to disk (the frame signature) or compared with the oracle;
the names of the functions and constants themselves are snake_case.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from loomsiril.core import js_round
from loomsiril.jsmath import js_string, json_string, number_str, parse_float

__all__ = [
    "CROSSFADE_SECONDS",
    "DRAFT_BYTES",
    "DRAFT_LONG",
    "ENCODE_ONLY",
    "FPS",
    "LINEAR_SKY",
    "LOOPS",
    "PRESETS",
    "PRESET_LABELS",
    "PRESET_ORDER",
    "RENDERER_VERSION",
    "STAR_QUALITY",
    "STAR_QUALITIES",
    "STAR_QUALITY_DEFAULT",
    "WORKING_HEIGHT",
    "WORKING_WIDTH",
    "crossfade_frames",
    "draft_plan",
    "format_elapsed",
    "frame_count",
    "frame_path",
    "frame_signature",
    "frame_step",
    "header_centre",
    "is_frame_file",
    "js_json",
    "js_num_str",
    "loop_frame",
    "looks_linear",
    "needs_rotation",
    "parse_angle",
    "preset_crop",
    "preset_loops",
    "preset_spec",
    "preset_turns",
    "progress_fraction",
    "progress_text",
    "quality_scale",
    "sequence_frame",
    "sequence_length",
    "star_quality",
    "time_at",
    "working_scale",
]

# ---------------------------------------------------------------------------
# Star quality (Fly.STAR_QUALITY, Fly.starQuality, Fly.qualityScale).
# ---------------------------------------------------------------------------

#: How the star sprites are drawn. Highest is the full renderer from the full-size
#: (4K working) scene; High and Medium draw from the scene shrunk to the video's size
#: (one scene pixel per output pixel); Medium also draws the stars at half the
#: video's resolution (a quarter of the fill) and upsamples them: softer stars.
STAR_QUALITY: dict[str, dict[str, Any]] = {
    "highest": {"footprint": False, "outScale": 0, "starRes": 1},
    "high": {"footprint": True, "outScale": 1, "starRes": 1},
    "medium": {"footprint": True, "outScale": 1, "starRes": 0.5},
}


def star_quality(level: str | None = None) -> dict[str, Any]:
    """``Fly.starQuality``: the level's settings; an unknown or missing level is Highest.

    ``hasOwnProperty`` in the JS: "constructor" and friends are not levels either.
    """
    if isinstance(level, str) and level in STAR_QUALITY:
        return STAR_QUALITY[level]
    return STAR_QUALITY["highest"]


def quality_scale(level: str | None, crop_w: float, out_w: float) -> float:
    """``Fly.qualityScale``: the scale a level draws its scene at for a crop cropW wide drawn outW wide.

    High and Medium shrink the scene until one output pixel spans ``outScale`` scene
    pixels, never enlarging it; Highest keeps the full-size scene (1).
    """
    target = star_quality(level)["outScale"]
    return min(1, target * out_w / crop_w) if target > 0 else 1


# ---------------------------------------------------------------------------
# Frames and timing (Fly.frameCount, Fly.timeAt, Fly.draftPlan).
# ---------------------------------------------------------------------------


def frame_count(duration: float, fps: float, ping_pong: bool) -> int:
    """``Fly.frameCount``: frames in a clip, doubled for a there-and-back loop."""
    return int(js_round(duration * fps)) * (2 if ping_pong else 1)


def time_at(frame: int, frames: int, ping_pong: bool) -> float:
    """``Fly.timeAt``: the flight's time (0..1) at a frame; a ping-pong turns at the middle."""
    if not ping_pong:
        return 0 if frames <= 1 else frame / (frames - 1)
    half = frames / 2
    return frame / half if frame <= half else (frames - frame) / half


#: The draft's byte budget: a draft is for judging, so it stays small whatever the clip.
DRAFT_BYTES: float = 400e6
#: px: the draft's long side.
DRAFT_LONG: int = 480


def draft_plan(duration: float, fps: float, long_side: float, aspect: float) -> dict[str, Any]:
    """``Fly.draftPlan``: the draft's size, rate, frame count and bytes.

    A quarter of the frames (a draft is for judging, and fast); fewer when they
    would exceed DRAFT_BYTES. The rate is kept so the clip keeps its length.
    """
    w = long_side if aspect >= 1 else js_round(long_side * aspect)
    h = js_round(long_side / aspect) if aspect >= 1 else long_side
    per = w * h * 4
    want = fps / 4
    frames = js_round(duration * want)
    if frames * per > DRAFT_BYTES:
        frames = math.floor(DRAFT_BYTES / per)
        want = frames / duration
    return {"width": w, "height": h, "fps": want, "frames": frames, "bytes": frames * per}


# ---------------------------------------------------------------------------
# Output presets (Fly.PRESETS, Fly.presetCrop).
# ---------------------------------------------------------------------------

#: Output size of each preset, (width, height).
PRESETS: dict[str, tuple[int, int]] = {
    "social_vertical": (1080, 1920),
    "social_square": (1080, 1080),
    "youtube_4k": (3840, 2160),
    "youtube_1080": (1920, 1080),
    "exhibition": (3840, 2160),
}


def preset_crop(img_w: float, img_h: float, tx: float, ty: float, out_w: float, out_h: float) -> dict[str, int]:
    """``Fly.presetCrop``: the largest crop of the output's aspect, centred on (tx, ty), kept inside the image.

    Returns the JS ``{x, y, w, h}``; ``x``/``y`` are clamped so the crop never leaves the image.
    """
    aspect = out_w / out_h
    w = img_w
    h = js_round(img_w / aspect)
    if h > img_h:
        h = img_h
        w = js_round(img_h * aspect)
    x = js_round(tx - w / 2)
    y = js_round(ty - h / 2)
    x = max(0, min(img_w - w, x))
    y = max(0, min(img_h - h, y))
    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}


# ---------------------------------------------------------------------------
# Orientation and loops (Fly.presetTurns, Fly.presetLoops, Fly.presetSpec, Fly.frameStep).
# ---------------------------------------------------------------------------


def preset_turns(p: str, orientation: str | None, size: Iterable[float]) -> bool:
    """``Fly.presetTurns``: a landscape preset is turned for a vertical orientation (social ones are not)."""
    w, h = list(size)[:2]
    return orientation == "vertical" and not p.startswith("social_") and w > h


def preset_loops(p: str, loop: str | None = None) -> bool:
    """``Fly.presetLoops``: the Exhibition always loops; the others by the loop choice (pingpong or crossfade)."""
    return p == "exhibition" or loop in ("pingpong", "crossfade")


def preset_spec(p: Any, orientation: str | None = None, loop: str | None = None) -> Any:
    """``Fly.presetSpec``: a preset key as its frame size and loop, ``{id, preset, w, h, pingPong, crossfade}``.

    Vertical turns the widescreen presets portrait, in a folder of their own; the
    social presets keep their shape. A dict is returned as it is (the JS: "for the suite").
    """
    if not isinstance(p, str):
        return p
    size = PRESETS[p]
    turn = preset_turns(p, orientation, size)
    loops = preset_loops(p, loop)
    return {
        "id": p + "_vertical" if turn else p,
        "preset": p,
        "w": size[1] if turn else size[0],
        "h": size[0] if turn else size[1],
        "pingPong": loops and loop != "crossfade",
        "crossfade": loops and loop == "crossfade",
    }


def frame_step(n: int, ping_pong: bool) -> float:
    """``Fly.frameStep``: the flight time between two frames (0..1) of n frames, or of there and back."""
    steps = n / 2 if ping_pong else n - 1  # a crossfade's flight is its clip (loop_frame)
    return 1 / steps if steps > 0 else 0


# ---------------------------------------------------------------------------
# Frame signature (Fly.RENDERER_VERSION, Fly.ENCODE_ONLY, Fly.frameSignature).
# ---------------------------------------------------------------------------

#: Bumped whenever the same options make different frames, so frames kept from an
#: older renderer are rendered again, not reused or resumed.
#: 2: HDR headroom as a bump on each star; crossfade loops dissolve at the end.
#: 3: the headroom bumps follow the backdrop's zoom.
#: 4: a crossfade's pre-roll holds the clock (twinkle, bloom, logo) at 0.
#: 5: vertical High/Medium drafts could be drawn from the unturned shrunk scene.
#: 6: the crossfade is 1 s.
RENDERER_VERSION: int = 6

#: Options that change only the encode, never the frames.
ENCODE_ONLY: list[str] = ["format", "quality", "music", "video", "ffmpeg", "dir", "presets", "logoImage", "output", "objectName"]


def frame_signature(opts: Mapping[str, Any], spec: Mapping[str, Any], scene_key: str) -> str:
    """``Fly.frameSignature``: what a preset's frames were made from, as the JS JSON text.

    Every option but the encode's, keys sorted, with the frame's spec and the
    scene's key. Equal signatures, same frames. The text is ``JSON.stringify`` of
    the same object in the same key order (see :func:`js_json`).
    """
    keep: dict[str, Any] = {}
    for k in sorted(opts):
        if k not in ENCODE_ONLY:
            keep[k] = opts[k]
    obj = {
        "opts": keep,
        "spec": {
            "w": spec["w"],
            "h": spec["h"],
            "pingPong": bool(spec.get("pingPong")),
            "crossfade": bool(spec.get("crossfade")),
        },
        "scene": scene_key,
        "renderer": RENDERER_VERSION,
    }
    return js_json(obj)


#: A crossfade loop's fade, in seconds: at most a quarter of the clip (was 2: slow).
CROSSFADE_SECONDS: float = 1


def crossfade_frames(duration: float, fps: float) -> int:
    """``Fly.crossfadeFrames``: the fade's frame count, at least 1."""
    return int(max(1, js_round(min(CROSSFADE_SECONDS, duration / 4) * fps)))


def loop_frame(i: int, n: int, F: int) -> dict[str, Any]:
    """``Fly.loopFrame``: frame i of an n-frame crossfade loop fading over F frames.

    Returns ``{a, b, alpha}``: the flight time ``a`` of the frame, the flight time
    ``b`` it is mixed with (None outside the fade), and the mix ``alpha`` of ``a``.
    It starts clean (frame 0 is the photograph), and over the last F frames
    dissolves into the moments just before the start, so the loop runs on into frame 0.
    """
    step = 1 / (n - 1) if n > 1 else 0
    if i < n - F:
        return {"a": i * step, "b": None, "alpha": 1}
    return {"a": i * step, "b": (i - n) * step, "alpha": (n - i) / (F + 1)}


# ---------------------------------------------------------------------------
# Frame files (Fly.isFrameFile, Fly.framePath).
# ---------------------------------------------------------------------------

_FRAME_NAME = re.compile(r"frame_[0-9]{5}\.tif")


def is_frame_file(name: str) -> bool:
    """``Fly.isFrameFile``: a file this script writes into a preset folder, and nothing else."""
    return _FRAME_NAME.fullmatch(name) is not None


def frame_path(folder: str, i: int) -> str:
    """``Fly.framePath``: ``folder/frame_00007.tif``, five digits.

    As the JS does it, the number is cut to its last five digits, so frame 100000
    is written as ``frame_00000.tif``; the sequence is never longer than that.
    """
    return folder + "/frame_" + ("0000" + str(i))[-5:] + ".tif"


# ---------------------------------------------------------------------------
# Sequences (Fly.sequenceLength, Fly.sequenceFrame).
# ---------------------------------------------------------------------------


def sequence_length(n: int, ping_pong: bool) -> int:
    """``Fly.sequenceLength``: frames in a played sequence: n forward, or there and back (ends once)."""
    return 2 * n - 2 if (ping_pong and n > 1) else n


def sequence_frame(i: int, n: int, ping_pong: bool) -> int:
    """``Fly.sequenceFrame``: which of the n frames plays at play position i."""
    k = i % sequence_length(n, ping_pong)
    return k if k < n else 2 * n - 2 - k


# ---------------------------------------------------------------------------
# The image's centre and typed angles (Fly.headerCentre, Fly.parseAngle).
# ---------------------------------------------------------------------------

def header_centre(header: Mapping[str, Any] | Iterable[tuple[str, Any]] | None) -> dict[str, float] | None:
    """``Fly.headerCentre``: the image's centre from its FITS keywords, or None.

    Takes a mapping ``name -> value`` or an iterable of ``(name, value)`` pairs.
    Names are trimmed and upper-cased; quotes around a value are dropped. Order of
    preference: CRVAL1/2 on an RA axis, RA/DEC in degrees, OBJCTRA/OBJCTDEC in
    sexagesimal. A position must have RA in [0, 360) and |DEC| <= 90.
    """
    pairs = header.items() if isinstance(header, Mapping) else (header or [])
    kw: dict[str, str] = {}
    for name, value in pairs:
        kw[js_string(name).strip().upper()] = re.sub(r"^'|'\Z", "", js_string(value)).strip()

    def ok(ra: float | None, dec: float | None) -> dict[str, float] | None:
        if ra is None or dec is None:
            return None
        if math.isfinite(ra) and math.isfinite(dec) and 0 <= ra < 360 and abs(dec) <= 90:
            return {"ra": ra, "dec": dec}
        return None

    r: dict[str, float] | None = None
    if re.match(r"RA", kw.get("CTYPE1", "")):
        r = ok(parse_float(kw.get("CRVAL1", "nan")), parse_float(kw.get("CRVAL2", "nan")))
    if not r and kw.get("RA") is not None and kw.get("DEC") is not None:
        r = ok(parse_float(kw["RA"]), parse_float(kw["DEC"]))
    if not r and kw.get("OBJCTRA") is not None and kw.get("OBJCTDEC") is not None:
        r = ok(parse_angle(kw["OBJCTRA"], True), parse_angle(kw["OBJCTDEC"], False))
    return r


_DECIMAL_ANGLE = re.compile(r"[-+]?[0-9]+(\.[0-9]+)?")
_SEXAGESIMAL = re.compile(r"([-+]?)([0-9]+)[\s:]+([0-9]+)(?:[\s:]+([0-9]+(?:\.[0-9]+)?))?")


def parse_angle(text: Any, is_ra: bool) -> float | None:
    """``Fly.parseAngle``: an angle typed by a person, in degrees, or None when it is not one.

    Decimal degrees ("324.18"), or sexagesimal ("21 36 42", "21:36:42", "-57 30 00"):
    hours for RA (times 15), degrees for Dec.
    """
    t = js_string(text).strip()
    if _DECIMAL_ANGLE.fullmatch(t):
        return float(t)
    m = _SEXAGESIMAL.fullmatch(t)
    if not m:
        return None
    seconds = float(m.group(4)) if m.group(4) else 0
    v = int(m.group(2)) + int(m.group(3)) / 60 + seconds / 3600
    return (-1 if m.group(1) == "-" else 1) * v * (15 if is_ra else 1)


# ---------------------------------------------------------------------------
# Progress (Fly.formatElapsed, Fly.progressText, Fly.progressFraction).
# ---------------------------------------------------------------------------


def format_elapsed(ms: float) -> str:
    """``Fly.formatElapsed``: "1 min 15 s", or "42 s"."""
    s = int(js_round(ms / 1000))
    m = s // 60
    return f"{m} min {s - 60 * m} s" if m > 0 else f"{s} s"


def progress_text(stage: str, done: float, total: float, elapsed_ms: float, kept: float = 0) -> str:
    """``Fly.progressText``: the bar's text, with the count, the percentage and the time left.

    A stage with no count gets the time so far. ``kept`` of the done ones were
    already there (a resumed render), so they do not set the pace.
    """
    if not total > 0:
        return stage if elapsed_ms < 1000 else stage + " — " + format_elapsed(elapsed_ms)
    t = f"{stage} — {number_str(done)} of {number_str(total)} ({number_str(js_round(100 * done / total))}%)"
    fresh = done - (kept or 0)
    if fresh > 0 and done < total:
        left = elapsed_ms / fresh * (total - done)
        t += " — " + ("less than a minute left" if left < 60000 else f"about {number_str(js_round(left / 60000))} min left")
    return t


def progress_fraction(done: float, total: float) -> float | None:
    """``Fly.progressFraction``: the bar's fill, 0..1, or None without a total."""
    return max(0, min(1, done / total)) if total > 0 else None


# ---------------------------------------------------------------------------
# The working image (Fly.WORKING_WIDTH, Fly.WORKING_HEIGHT, Fly.workingScale, Fly.needsRotation).
# ---------------------------------------------------------------------------

#: The largest preset: the working image serves it at 1:1.
WORKING_WIDTH: int = 3840
WORKING_HEIGHT: int = 2160


def working_scale(w: float, h: float) -> float:
    """``Fly.workingScale``: the factor the input is resampled by, once, up front.

    The largest 16:9 rectangle that fits the image becomes 3840x2160. Never above 1.
    By the long side: a vertical image is turned for landscape presets.
    """
    long_side, short_side = max(w, h), min(w, h)
    aspect = WORKING_WIDTH / WORKING_HEIGHT
    cw = short_side * aspect if long_side / short_side >= aspect else long_side
    return min(1, WORKING_WIDTH / cw)


def needs_rotation(img_w: float, img_h: float, out_w: float, out_h: float) -> bool:
    """``Fly.needsRotation``: a preset of the other orientation renders the image turned 90 degrees.

    Square presets fit either way and never rotate.
    """
    if out_w == out_h or img_w == img_h:
        return False
    return (img_w > img_h) != (out_w > out_h)


# ---------------------------------------------------------------------------
# Sky (Fly.LINEAR_SKY, Fly.looksLinear).
# ---------------------------------------------------------------------------

#: A sky median below this is a linear (unstretched) image.
LINEAR_SKY: float = 0.02


def looks_linear(median: float) -> bool:
    """``Fly.looksLinear``: whether an image whose sky median is ``median`` looks linear."""
    return median < LINEAR_SKY


# ---------------------------------------------------------------------------
# FlyThrough's dialog constants (FlyThrough.PRESET_LABELS, PRESET_ORDER, FPS,
# LOOPS, STAR_QUALITIES, STAR_QUALITY_DEFAULT). They are the CLI's and the Siril
# dialog's choices, not Fly's; pinned from FlyThrough.js 698-702 and 774-776.
# ---------------------------------------------------------------------------

PRESET_LABELS: dict[str, str] = {
    "social_vertical": "Social 1080×1920",
    "social_square": "Social 1080×1080",
    "youtube_4k": "YouTube 3840×2160",
    "youtube_1080": "YouTube 1920×1080",
    "exhibition": "Exhibition 3840×2160 (loop)",
}
PRESET_ORDER: list[str] = ["social_vertical", "social_square", "youtube_4k", "youtube_1080", "exhibition"]
FPS: list[int] = [24, 25, 30, 60]
#: The Loop choices, (id, label).
LOOPS: list[tuple[str, str]] = [("none", "None"), ("pingpong", "Back and forth"), ("crossfade", "Crossfade")]
#: The Star quality combo's items, in order.
STAR_QUALITIES: list[str] = ["highest", "high", "medium"]
#: The dialog's default; code without a level gets Highest.
STAR_QUALITY_DEFAULT: str = "high"


# ---------------------------------------------------------------------------
# JSON text as JavaScript writes it (JSON.stringify and Number#toString).
# ---------------------------------------------------------------------------


#: The public name from before R1-a: ``number_str`` lives in jsmath now.
js_num_str = number_str


def js_json(v: Any) -> str:
    """``JSON.stringify`` of plain data: dicts in insertion order, JS number text, non-finite as null."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return number_str(v) if math.isfinite(v) else "null"
    if isinstance(v, str):
        return json_string(v)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(js_json(x) for x in v) + "]"
    if isinstance(v, Mapping):
        return "{" + ",".join(json_string(str(k)) + ":" + js_json(x) for k, x in v.items()) + "}"
    raise TypeError(f"not plain JSON data: {type(v).__name__}")
