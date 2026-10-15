"""The Fly-Through dialog's Video details, as plain functions (no Tk).

PixInsight's Video group (FlyThrough.js buildVideo, browseFfmpeg, refreshFormats, clearRenderedFrames,
refreshEstimate, estimateMs) and the Fly.js / Render.js helpers behind it:

* :func:`probe` runs ``ffmpeg -version`` and ``ffmpeg -encoders`` once for a binary (cached per path, size and
  modification time) and :func:`formats_for` lists the formats THIS ffmpeg can encode (Fly.availableFormats);
* :func:`validate_ffmpeg` checks a user-chosen binary the way Render.isWorkingFfmpeg does (it runs ``-version``);
* :func:`choose_format` keeps the saved format while the ffmpeg still offers it, else Fly.defaultFormat;
* :func:`encode_format` is the format an encode uses (``opts.format || (hdr ? "hevc" : "h264")``, H.264 never for HDR);
* :func:`estimate_ms` / :func:`estimate_text` port FlyThrough.estimateMs and refreshEstimate;
* :func:`clear_frames`, :func:`confirm_text`, :func:`cleared_text` are FlyThrough.clearFrames and the texts around it.

Everything here may touch disk or run a process, so the dialog calls it off the Tk thread.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from loomsiril import encode
from loomsiril import presets as preset_mod
from loomsiril.core import js_round

__all__ = [
    "ESTIMATE_HDR_FACTOR",
    "QUALITIES",
    "FfmpegProbe",
    "choose_format",
    "cleared_text",
    "counted_text",
    "clear_frames",
    "confirm_text",
    "encode_format",
    "estimate_ms",
    "estimate_text",
    "format_label",
    "formats_for",
    "forget_probes",
    "ms_per_mp",
    "probe",
    "quality_id",
    "validate_ffmpeg",
    "widest_side",
]

#: The Quality combo's items (PixInsight: High, Standard) and the ids the encode takes (opts.quality).
QUALITIES: list[tuple[str, str]] = [("high", "High"), ("standard", "Standard")]

#: FlyThrough.estimateMs: an HDR render measured about 1.3x an SDR one.
ESTIMATE_HDR_FACTOR = 1.3


# ---------------------------------------------------------------------------
# ffmpeg: validation and the encoder probe.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FfmpegProbe:
    """What one ffmpeg binary is: whether it works, its version line and the text of ``-encoders``."""

    path: str
    ok: bool
    version: str = ""
    encoders: str = ""
    reason: str = ""


_cache: dict[tuple[str, int, int], FfmpegProbe] = {}
_lock = threading.Lock()


def _key(path: str) -> tuple[str, int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.realpath(path), st.st_mtime_ns, st.st_size)


def forget_probes() -> None:
    """Empty the probe cache (the suite; or to look at a binary again)."""
    with _lock:
        _cache.clear()


def _version(path: str) -> tuple[bool, str, str]:
    """Run ``-version``: ``(ok, why not, the version line)``."""
    path = (path or "").strip()
    if not path:
        return False, "no ffmpeg path", ""
    if not os.path.isfile(path):
        return False, f"{path} is not a file", ""
    if not os.access(path, os.X_OK):
        return False, f"{path} is not executable", ""
    code, out = encode.run_process(path, ["-version"], deadline_s=10)
    if code == 0 and out.strip().startswith("ffmpeg version"):
        return True, "", out.strip().splitlines()[0]
    return False, f"{path} did not answer -version like ffmpeg", ""


def validate_ffmpeg(path: str) -> tuple[bool, str]:
    """``Render.isWorkingFfmpeg``, with the reason: ``(ok, why not)``. Runs ``-version``; call it off the Tk thread."""
    ok, why, _ = _version(path)
    return ok, why


def probe(path: str) -> FfmpegProbe:
    """Run ``-version`` and ``-encoders`` once for this binary; later calls answer from the cache.

    The cache key is the binary's real path, size and modification time, so a replaced ffmpeg is probed again.
    A binary that does not work is not cached (the user may fix it and look again). Call it off the Tk thread.
    """
    path = (path or "").strip()
    key = _key(path) if path else None
    if key is None:
        return FfmpegProbe(path, False, reason=f"{path or 'no path'} is not a file")
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    ok, why, version = _version(path)
    if not ok:
        return FfmpegProbe(path, False, reason=why)
    result = FfmpegProbe(path, True, version=version, encoders=encode.ffmpeg_encoders(path))
    with _lock:
        _cache[key] = result
    return result


def formats_for(found: FfmpegProbe | None, hdr: bool = False) -> list[dict[str, str]]:
    """``Fly.availableFormats``: the formats this ffmpeg can encode (H.264 never for HDR); none without a working ffmpeg."""
    if found is None or not found.ok:
        return []
    return encode.available_formats(found.encoders, hdr)


def format_label(format_id: str | None) -> str:
    """A format's combo label, or "" for an unknown id."""
    for f in encode.VIDEO_FORMATS:
        if f["id"] == format_id:
            return f["label"]
    return ""


def widest_side(presets: Sequence[str], orientation: str | None = None) -> int:
    """The longest side among the chosen presets' frames (refreshFormats: the width that picks the default format)."""
    side = 0
    for pid in presets:
        spec = preset_mod.preset_spec(pid, orientation)
        side = max(side, int(spec["w"]), int(spec["h"]))
    return side


def choose_format(formats: Sequence[Mapping[str, str]], saved: str | None, width: float) -> str | None:
    """The format to show: the saved one while this ffmpeg still offers it, else ``Fly.defaultFormat`` (None if none)."""
    ids = [f["id"] for f in formats]
    if saved in ids:
        return saved
    return encode.default_format(formats, width)


def quality_id(value: Any) -> str:
    """"high" or "standard" (opts.quality; anything else, or a label such as "High", reads as high unless it is Standard)."""
    return "standard" if str(value or "").strip().lower() == "standard" else "high"


def encode_format(format_id: str | None, hdr: bool) -> str:
    """The format an encode uses: ``opts.format || (hdr ? "hevc" : "h264")``; HDR never takes H.264 (it is SDR only)."""
    if format_id not in {f["id"] for f in encode.VIDEO_FORMATS}:
        format_id = None
    if hdr and format_id in (None, "h264"):
        return "hevc"
    return format_id or "h264"


# ---------------------------------------------------------------------------
# The render estimate (FlyThrough.estimateMs, refreshEstimate, measureSpeed).
# ---------------------------------------------------------------------------


def ms_per_mp(elapsed_ms: float, width: float, height: float) -> float:
    """Render speed, ms per output megapixel, from one frame of ``width`` x ``height`` (FlyThrough.measureSpeed)."""
    return float(elapsed_ms) / (float(width) * float(height) / 1e6)


def estimate_ms(
    ms_per_mp_value: float,
    presets: Sequence[str],
    duration: float,
    fps: float,
    hdr: bool,
    orientation: str | None,
    loop: str | None,
) -> float:
    """``FlyThrough.estimateMs``: milliseconds for a final render of the presets (HDR measured about 1.3x SDR)."""
    total = 0.0
    for pid in presets:
        p = preset_mod.preset_spec(pid, orientation, loop)
        total += preset_mod.frame_count(duration, fps, bool(p["pingPong"])) * p["w"] * p["h"] / 1e6 * ms_per_mp_value
    return total * (ESTIMATE_HDR_FACTOR if hdr else 1)


def estimate_text(
    ms_per_mp_value: float | None,
    presets: Sequence[str],
    duration: float,
    fps: float,
    hdr: bool,
    orientation: str | None,
    loop: str | None,
) -> str:
    """``FlyThrough.refreshEstimate``: "Estimated render: about N min", or "" until a speed is measured.

    PixInsight measures the speed once per scene (one 1920x1080 frame) and shows nothing before that, so the
    default here is the same: no measurement, no line. N is at least 1.
    """
    if not (ms_per_mp_value and ms_per_mp_value > 0) or not presets:
        return ""
    ms = estimate_ms(ms_per_mp_value, presets, duration, fps, hdr, orientation, loop)
    return f"Estimated render: about {max(1, int(js_round(ms / 60000)))} min"


# ---------------------------------------------------------------------------
# Clear rendered frames (FlyThrough.clearFrames, clearRenderedFrames).
# ---------------------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def clear_frames(out_dir: str, count_only: bool = False) -> dict[str, int]:
    """Delete (or with ``count_only`` just count) Loom's own frames, half-written frames and frame records.

    Only the preset folders of ``out_dir`` are looked at, and in them only ``frame_NNNNN.tif``, ``.part`` leftovers and
    the record file; a folder left empty goes too. Videos and any other file stay. Returns ``{frames, folders}``.
    """
    if not out_dir or not os.path.isdir(out_dir):
        return {"frames": 0, "folders": 0}
    from loomsiril import pipeline  # late: the pipeline pulls numpy in

    return pipeline.clear_frames(out_dir, count_only)


def confirm_text(counts: Mapping[str, int], out_dir: str) -> str:
    """The question the dialog asks before deleting (clearRenderedFrames)."""
    return (f"Delete {_plural(counts['frames'], 'rendered frame')} in {_plural(counts['folders'], 'preset folder')} of {out_dir}?"
            "\n\nThe next render draws them afresh. The videos are kept.")


def nothing_text(out_dir: str) -> str:
    """The line shown when there is nothing to clear."""
    if not out_dir or not os.path.isdir(out_dir):
        return "Choose an output folder first."
    return f"No rendered frames to clear in {out_dir}."


def counted_text(counts: Mapping[str, int]) -> str:
    """The grey line under the button: what is there to clear."""
    return f"{_plural(counts['frames'], 'rendered frame')} in {_plural(counts['folders'], 'preset folder')}"


def cleared_text(counts: Mapping[str, int]) -> str:
    """The line shown after clearing."""
    return f"Cleared {_plural(counts['frames'], 'rendered frame')}; the next render draws every frame afresh."
