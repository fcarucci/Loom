"""Fly-Through's video encoding: formats, ffmpeg arguments, audio and ffmpeg discovery (E1b).

Ported from Loom ``script/lib/Fly.js`` (v0.4.1): the video formats and their
codec arguments, the Rec.709 and BT.2020 (PQ, HLG) filters, the HDR10 metadata,
the ffmpeg command line for a folder of frames, the video's name, the music
under the video, the ffmpeg candidates and the install hints, the command line
the dialog shows, and the progress count ffmpeg prints.

Replaced (R): ``Render.js`` ran ffmpeg through Siril's ``ExternalProcess`` and
PixInsight's event loop. :func:`run_process` does the same with ``subprocess``
(no shell), reading the output as it arrives, and :func:`encode` and
:func:`find_ffmpeg` sit on it. The Loom settings file that remembered the path
is not kept here: the caller passes the saved path in.
"""

from __future__ import annotations

import codecs
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from loomsiril.core import js_round
from loomsiril.presets import js_num_str

__all__ = [
    "AUDIO_FADE",
    "AUDIO_LOOP_FADE",
    "HDR_PEAK_DEFAULT",
    "HDR_TRC",
    "REC709_FILTER",
    "VIDEO_ENCODING_TAGS",
    "VIDEO_FORMATS",
    "audio_args",
    "audio_fade",
    "available_formats",
    "codec_args",
    "command_line",
    "default_format",
    "encode",
    "ffmpeg_args",
    "ffmpeg_candidates",
    "ffmpeg_encoders",
    "ffmpeg_frame_progress",
    "ffmpeg_install_hint",
    "find_ffmpeg",
    "hdr_filter",
    "is_hdr",
    "is_working_ffmpeg",
    "loop_audio",
    "platform_name",
    "run_process",
    "video_name",
    "x265_hdr10",
]

# ---------------------------------------------------------------------------
# Formats (Fly.VIDEO_FORMATS, Fly.availableFormats, Fly.defaultFormat).
# ---------------------------------------------------------------------------

#: The formats Fly-Through offers; ``encoder`` is the ffmpeg encoder that must be present.
VIDEO_FORMATS: list[dict[str, str]] = [
    {"id": "h264", "label": "MP4 \u00b7 H.264", "ext": "mp4", "encoder": "libx264"},
    {"id": "hevc", "label": "MP4 \u00b7 H.265/HEVC", "ext": "mp4", "encoder": "libx265"},
    {"id": "prores", "label": "MOV \u00b7 ProRes 422 HQ", "ext": "mov", "encoder": "prores_ks"},
    {"id": "vp9", "label": "WebM \u00b7 VP9", "ext": "webm", "encoder": "libvpx-vp9"},
]


def available_formats(encoders_text: str, hdr: bool = False) -> list[dict[str, str]]:
    """``Fly.availableFormats``: the formats this ffmpeg can encode, from its ``-encoders`` text.

    H.264 is SDR only, so an HDR video never offers it.
    """
    out = []
    for f in VIDEO_FORMATS:
        if hdr and f["id"] == "h264":
            continue
        pattern = r"\b" + re.escape(f["encoder"]) + r"\b"
        if re.search(pattern, encoders_text, re.ASCII):
            out.append(f)
    return out


def default_format(formats: Sequence[Mapping[str, str]], width: float) -> str | None:
    """``Fly.defaultFormat``: HEVC for a 4K-wide video when there is one, else H.264, else the first."""
    ids = [f["id"] for f in formats]
    if width > 1920 and "hevc" in ids:
        return "hevc"
    if "h264" in ids:
        return "h264"
    return ids[0] if ids else None


# ---------------------------------------------------------------------------
# Colour (Fly.REC709_FILTER, Fly.isHdr, Fly.HDR_TRC, Fly.hdrFilter, Fly.x265Hdr10).
# ---------------------------------------------------------------------------

#: Tag the frames BT.709 and convert RGB to YUV with the BT.709 matrix. ffmpeg 9
#: takes the stream's colour tags from the frames: output flags alone left the
#: primaries "unknown" (probed 2026-09-23). The explicit scale makes older builds,
#: whose default is BT.601, convert the same way.
REC709_FILTER: str = (
    "scale=out_color_matrix=bt709:out_range=tv,"
    "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
)


def is_hdr(transfer: str | None) -> bool:
    """``Fly.isHdr``: an output transfer "pq" or "hlg" (not "sdr")."""
    return transfer in ("pq", "hlg")


#: ffmpeg's names for the HDR transfer curves.
HDR_TRC: dict[str, str] = {"pq": "smpte2084", "hlg": "arib-std-b67"}

#: The HDR peak in nits when none is given (Fly.HDR_PEAK_DEFAULT).
HDR_PEAK_DEFAULT: int = 1000


def hdr_filter(transfer: str) -> str:
    """``Fly.hdrFilter``: the BT.2020 equivalent of REC709_FILTER for transfer "pq" or "hlg"."""
    return (
        "scale=out_color_matrix=bt2020:out_range=tv,"
        f"setparams=color_primaries=bt2020:color_trc={HDR_TRC[transfer]}:colorspace=bt2020nc:range=tv"
    )


def x265_hdr10(hdr: Mapping[str, Any]) -> str:
    """``Fly.x265Hdr10``: HDR10 static metadata for x265: BT.2020 primaries, D65, 0.0001 nits to the peak."""
    peak = hdr.get("peak") or HDR_PEAK_DEFAULT
    return (
        "hdr10=1:repeat-headers=1:master-display="
        "G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)"
        f"L({js_num_str(js_round(peak * 10000))},1)"
        f":max-cll={js_num_str(js_round(hdr.get('maxCll') or 0))},{js_num_str(js_round(hdr.get('maxFall') or 0))}"
    )


# ---------------------------------------------------------------------------
# Codecs and the ffmpeg command (Fly.codecArgs, Fly.ffmpegArgs).
# ---------------------------------------------------------------------------


def codec_args(format_id: str, hi: bool, hdr: Mapping[str, Any] | None = None) -> list[str] | None:
    """``Fly.codecArgs``: the codec arguments of a format; ``hi`` is the High quality setting.

    With ``hdr`` the picture is 10-bit; PQ adds the HDR10 metadata to HEVC, and VP9
    takes profile 2. None for a format id that is not known.
    """
    has_hdr = hdr is not None
    ten = "yuv420p10le" if has_hdr else "yuv420p"
    hevc = ["-c:v", "libx265", "-crf", "20" if hi else "26", "-tag:v", "hvc1", "-pix_fmt", ten]
    if has_hdr and hdr.get("transfer") == "pq":
        hevc = hevc + ["-x265-params", x265_hdr10(hdr)]
    table: dict[str, list[str]] = {
        "h264": ["-c:v", "libx264", "-crf", "18" if hi else "23", "-pix_fmt", "yuv420p"],
        "hevc": hevc,
        "prores": ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"],
        "vp9": ["-c:v", "libvpx-vp9"] + (["-profile:v", "2"] if has_hdr else [])
        + ["-crf", "24" if hi else "32", "-b:v", "0", "-pix_fmt", ten],
    }
    return table.get(format_id)


def ffmpeg_args(
    frames_dir: str,
    fps: float,
    out_base: str,
    format_id: str,
    quality: str,
    hdr: Mapping[str, Any] | None = None,
    audio: Mapping[str, Any] | None = None,
) -> list[str]:
    """``Fly.ffmpegArgs``: the ffmpeg arguments for a folder of ``frame_%05d.tif`` frames.

    ``hdr`` (optional) is ``{transfer: "pq" | "hlg", peak, maxCll, maxFall}``: 10-bit,
    BT.2020 tags, and for PQ the HDR10 metadata; the frames already hold the HDR
    signal. ``audio`` (optional) is the music, see :func:`audio_args`. The output
    is ``out_base`` with the format's extension, last.
    """
    fmt = next(x for x in VIDEO_FORMATS if x["id"] == format_id)
    if hdr is not None:
        tags = ["-colorspace", "bt2020nc", "-color_primaries", "bt2020", "-color_trc", HDR_TRC[hdr["transfer"]]]
    else:
        tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    music = audio_args(audio, format_id) if (audio and audio.get("path")) else None
    args = ["-y", "-framerate", js_num_str(fps), "-i", frames_dir + "/frame_%05d.tif"]
    args += music["input"] if music else []
    args += ["-vf", hdr_filter(hdr["transfer"]) if hdr is not None else REC709_FILTER]
    args += codec_args(format_id, quality == "high", hdr) or []
    args += music["output"] if music else []
    args += tags
    args += [out_base + "." + fmt["ext"]]
    return args


# ---------------------------------------------------------------------------
# The video's name (Fly.VIDEO_ENCODING_TAGS, Fly.videoName).
# ---------------------------------------------------------------------------

VIDEO_ENCODING_TAGS: dict[str, str] = {"sdr": "SDR", "pq": "HDR-PQ", "hlg": "HDR-HLG"}


def _clean_name_part(t: Any) -> str:
    """One part of a file name: no apostrophes, runs of other characters become "_", no edge "_"."""
    s = str(t) if t else ""
    s = re.sub("['\u2019]", "", s)
    s = re.sub(r"[^A-Za-z0-9-]+", "_", s)
    return re.sub(r"^_+|_+$", "", s)


def video_name(object_name: str | None, preset_id: str, transfer: str | None) -> str:
    """``Fly.videoName``: the video's file name without extension, e.g. ``NGC7023_Iris_Nebula_youtube_1080_vertical_HDR-PQ``.

    Just the preset and the encoding when there is no object.
    """
    tag = VIDEO_ENCODING_TAGS.get(transfer) if transfer is not None else None
    if not tag:
        tag = str(transfer or "SDR").upper()
    parts = [_clean_name_part(object_name), preset_id, tag]
    return "_".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# The music under the video (Fly.AUDIO_FADE, Fly.AUDIO_LOOP_FADE, Fly.audioFade,
# Fly.audioArgs, Fly.loopAudio).
# ---------------------------------------------------------------------------

#: s: the music's fade in and out, at most a quarter of the clip.
AUDIO_FADE: float = 2
#: s: a looping video's music crossfading end into beginning, as quick as the video's.
AUDIO_LOOP_FADE: float = 1


def audio_fade(duration: float) -> float:
    """``Fly.audioFade``: the music's fade, at most a quarter of the clip."""
    return min(AUDIO_FADE, duration / 4)


def _fixed3(v: float) -> str:
    """``+v.toFixed(3)`` as JS writes it: three decimals, halves away from zero, no trailing zeros."""
    d = Decimal(v).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return js_num_str(float(d))


def audio_args(audio: Mapping[str, Any], format_id: str) -> dict[str, list[str]]:
    """``Fly.audioArgs``: the music's ffmpeg input and output arguments.

    ``audio`` is ``{path, fade, duration, loop}``: looped if it is shorter, cut to the
    video (``-shortest``), faded in and out unless ``fade`` is off, or for a looping
    video crossfaded end into beginning. AAC in MP4 and MOV, Opus in WebM.
    """
    duration = audio["duration"]
    d = audio_fade(duration)
    codec = ["-c:a", "libopus", "-b:a", "160k"] if format_id == "vp9" else ["-c:a", "aac", "-b:a", "192k"]
    input_args = ["-stream_loop", "-1", "-i", audio["path"]]
    if audio.get("loop"):
        return {
            "input": input_args,
            "output": loop_audio(duration, min(AUDIO_LOOP_FADE, duration / 4)) + codec + ["-shortest"],
        }
    out = ["-map", "0:v", "-map", "1:a"]
    if audio.get("fade") and d > 0:
        d_text = js_num_str(d)
        out += ["-af", f"afade=t=in:st=0:d={d_text},afade=t=out:st={js_num_str(duration - d)}:d={d_text}"]
    out += codec + ["-shortest"]
    return {"input": input_args, "output": out}


def loop_audio(duration: float, x: float) -> list[str]:
    """``Fly.loopAudio``: a looping video's music, ``duration`` seconds, blended at the end like the picture.

    The video's music starts ``x`` seconds into the song and fades out over its last
    ``x`` seconds, while the song's first ``x`` seconds fade in over them, so the loop
    point runs on into second ``x``, where the video's music began.
    """
    delay_ms = js_round((duration - x) * 1000)
    graph = (
        f"[1:a]atrim=0:{_fixed3(duration + x)},asetpts=PTS-STARTPTS,asplit=2[a][b];"
        f"[a]atrim={_fixed3(x)}:{_fixed3(duration + x)},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={_fixed3(duration - x)}:d={_fixed3(x)}[body];"
        f"[b]atrim=0:{_fixed3(x)},asetpts=PTS-STARTPTS,afade=t=in:st=0:d={_fixed3(x)},"
        f"adelay={delay_ms}:all=1[head];"
        "[body][head]amix=inputs=2:duration=first:normalize=0[aout]"
    )
    return ["-filter_complex", graph, "-map", "0:v", "-map", "[aout]"]


# ---------------------------------------------------------------------------
# ffmpeg discovery and the command line (Fly.ffmpegCandidates, Fly.ffmpegInstallHint,
# Fly.commandLine).
# ---------------------------------------------------------------------------


def platform_name(sys_platform: str) -> str:
    """Loom's platform names from ``sys.platform``: "macos", "windows", or "linux" for the rest."""
    if sys_platform.startswith("darwin"):
        return "macos"
    if sys_platform.startswith("win"):
        return "windows"
    return "linux"


@dataclass(frozen=True)
class _FfmpegPlace:
    """Where one platform keeps ffmpeg: its fixed places, and the rules for the PATH variable."""

    #: Fixed paths to try after the saved one; an entry with "{home}" is skipped when there is no home.
    fixed: tuple[str, ...]
    #: The separator between the directories of the PATH variable.
    path_sep: str
    #: The program's name inside each PATH directory.
    exe: str
    #: PATH entries are Windows paths: backslashes become slashes and surrounding quotes go.
    windows_paths: bool


_UNIX_PLACE = _FfmpegPlace(
    fixed=("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"),
    path_sep=":",
    exe="/ffmpeg",
    windows_paths=False,
)

#: Per platform (Loom's names): the places ``ffmpeg_candidates`` tries. Any other name gets the Unix rules.
_FFMPEG_PLACES: dict[str, _FfmpegPlace] = {
    "windows": _FfmpegPlace(
        fixed=(
            "{home}/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe",
            "C:/ProgramData/chocolatey/bin/ffmpeg.exe",
            "{home}/scoop/shims/ffmpeg.exe",
            "C:/ffmpeg/bin/ffmpeg.exe",
            "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
        ),
        path_sep=";",
        exe="/ffmpeg.exe",
        windows_paths=True,
    ),
    "macos": _UNIX_PLACE,
    "linux": _UNIX_PLACE,
}


def _path_dirs(path_var: str | None, place: _FfmpegPlace) -> list[str]:
    """The non-empty directories of the PATH variable, cleaned for the platform (no quotes, no trailing slash)."""
    out: list[str] = []
    for d in str(path_var or "").split(place.path_sep):
        d = d.strip()
        d = re.sub(r'^"|"\Z', "", d)
        if place.windows_paths:
            d = d.replace("\\", "/")
        d = re.sub(r"/+$", "", d)
        if d:
            out.append(d)
    return out


def _unique(paths: Iterable[str | None]) -> list[str]:
    """The non-empty paths, each once, in first-seen order."""
    out: list[str] = []
    for p in paths:
        if p and p not in out:
            out.append(p)
    return out


def ffmpeg_candidates(platform: str, home: str | None, path_var: str | None, saved: str | None) -> list[str]:
    """``Fly.ffmpegCandidates``: the places ffmpeg may be, in the order to try them, without duplicates.

    The saved path first, then the platform's usual places (Windows: winget, chocolatey,
    scoop, C:\\ffmpeg, Program Files; macOS: Homebrew, /usr/local, /usr/bin; other:
    /usr/bin and friends), then every directory of ``PATH``. The places are in ``_FFMPEG_PLACES``.
    """
    place = _FFMPEG_PLACES.get(platform, _UNIX_PLACE)
    fixed = [p.format(home=home) for p in place.fixed if home or "{home}" not in p]
    on_path = [d + place.exe for d in _path_dirs(path_var, place)]
    return _unique([saved, *fixed, *on_path])


def ffmpeg_install_hint(platform: str) -> str:
    """``Fly.ffmpegInstallHint``: how to install ffmpeg on this platform."""
    if platform == "windows":
        return "install ffmpeg, e.g. winget install Gyan.FFmpeg"
    if platform == "macos":
        return "install ffmpeg, e.g. brew install ffmpeg"
    return "install ffmpeg with your package manager"


_BARE_ARG = re.compile(r"[A-Za-z0-9_\-.,:/=%+]+")


def command_line(program: str, args: Sequence[Any], platform: str) -> str:
    """``Fly.commandLine``: the command as the dialog shows it, quoted for the platform's shell.

    Windows quotes with double quotes; macOS (and Linux) with single quotes. An
    argument of safe characters is bare. This is the JS rule, not shlex's.
    """
    win = platform == "windows"

    def quote(a: Any) -> str:
        s = js_num_str(a) if isinstance(a, float) else str(a)
        if _BARE_ARG.fullmatch(s):
            return s
        if win:
            return '"' + s.replace('"', '\\"') + '"'
        return "'" + s.replace("'", "'\\''") + "'"

    return " ".join(quote(a) for a in [program, *args])


def ffmpeg_frame_progress(text: str) -> int | None:
    """``Fly.ffmpegFrameProgress``: the last ``frame= N`` ffmpeg printed, or None."""
    last: int | None = None
    for m in re.finditer(r"frame=\s*([0-9]+)", text):
        last = int(m.group(1))
    return last


# ---------------------------------------------------------------------------
# Running ffmpeg (replaces Render.runProcess, Render.isWorkingFfmpeg,
# Render.findFfmpeg, Render.ffmpegEncoders, Render.encode).
# ---------------------------------------------------------------------------


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """Terminate a process, and kill it if it does not go within five seconds."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class _Output:
    """The process output so far: bytes decoded as UTF-8 (a bad byte becomes U+FFFD), passed on chunk by chunk."""

    def __init__(self, on_output: Callable[[str], None] | None) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._parts: list[str] = []
        self._on_output = on_output

    def feed(self, data: bytes) -> None:
        """Add the bytes of one read."""
        self._emit(self._decoder.decode(data))

    def finish(self) -> None:
        """The pipe is closed: decode what is left in the decoder (an incomplete character at the end)."""
        self._emit(self._decoder.decode(b"", final=True))

    def _emit(self, text: str) -> None:
        if text:
            self._parts.append(text)
            if self._on_output is not None:
                self._on_output(text)

    def text(self) -> str:
        """Everything decoded so far."""
        return "".join(self._parts)


def _start_pump(proc: subprocess.Popen[bytes], chunks: "queue.Queue[bytes | None]") -> threading.Thread:
    """A daemon thread that moves what the process writes into ``chunks`` as it arrives; None ends the stream."""

    def pump() -> None:
        """Reads what the process writes, as it arrives, until the pipe closes."""
        try:
            while True:
                data = proc.stdout.read1(65536)  # type: ignore[union-attr]
                if not data:
                    break
                chunks.put(data)
        finally:
            chunks.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    return reader


def _past_deadline_or_cancelled(until: float | None, is_cancelled: Callable[[], bool] | None) -> bool:
    """True once the monotonic deadline has passed or ``is_cancelled()`` says so (the deadline is checked first)."""
    if until is not None and time.monotonic() > until:
        return True
    return is_cancelled is not None and is_cancelled()


def _drain(chunks: "queue.Queue[bytes | None]", out: _Output, until: float | None,
           is_cancelled: Callable[[], bool] | None) -> bool:
    """Feed the output to ``out`` until the stream ends (True), or stop early on the deadline or a cancel (False).

    Polls the queue every 50 ms, so a deadline or a cancel is seen even while the process is silent.
    """
    while True:
        if _past_deadline_or_cancelled(until, is_cancelled):
            return False
        try:
            item = chunks.get(timeout=0.05)
        except queue.Empty:
            continue
        if item is None:
            return True
        out.feed(item)


def run_process(
    program: str,
    args: Sequence[str],
    deadline_s: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_output: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Run ``program`` with ``args`` (no shell); return ``(exit_code, output)``.

    stdout and stderr are merged, read as they arrive, and passed to ``on_output``
    chunk by chunk (ffmpeg writes its progress to stderr, with carriage returns).
    The exit code is -1 when the deadline passes or ``is_cancelled()`` says so (the
    process is terminated), or when the program cannot be started (the output is then
    the error text).
    """
    try:
        proc = subprocess.Popen(
            [program, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        return -1, str(exc)

    chunks: "queue.Queue[bytes | None]" = queue.Queue()
    pipe = _Pipe(proc, chunks, _start_pump(proc, chunks))
    out = _Output(on_output)
    try:
        return _collect(pipe, out, deadline_s, is_cancelled)
    finally:
        if proc.poll() is None:
            _stop(proc)


@dataclass(frozen=True)
class _Pipe:
    """A started process with its output pump: the process, the queue the pump fills, and the pump thread."""

    proc: subprocess.Popen[bytes]
    chunks: "queue.Queue[bytes | None]"
    reader: threading.Thread


def _collect(pipe: _Pipe, out: _Output, deadline_s: float | None, is_cancelled: Callable[[], bool] | None) -> tuple[int, str]:
    """Wait for the process's output and its exit: ``(exit_code, output)``, or -1 when a deadline or a cancel stops it."""
    until = time.monotonic() + deadline_s if deadline_s else None
    if not _drain(pipe.chunks, out, until, is_cancelled):
        _stop(pipe.proc)
        pipe.reader.join(timeout=2)
        return -1, out.text()
    out.finish()
    code = pipe.proc.wait()
    pipe.reader.join()
    return code, out.text()


def is_working_ffmpeg(path: str) -> bool:
    """``Render.isWorkingFfmpeg``: the file exists and ``-version`` starts with "ffmpeg version"."""
    if not os.path.isfile(path):
        return False
    code, out = run_process(path, ["-version"], deadline_s=10)
    return code == 0 and out.strip().startswith("ffmpeg version")


def find_ffmpeg(platform: str | None = None, saved: str = "") -> str | None:
    """``Render.findFfmpeg``: the first candidate that answers ``-version``, or None.

    ``platform`` is "macos", "windows" or "linux" (default: this machine's); ``saved``
    is the path kept from an earlier run, tried first.
    """
    name = platform or platform_name(sys.platform)
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    for candidate in ffmpeg_candidates(name, home, os.environ.get("PATH", ""), saved):
        if is_working_ffmpeg(candidate):
            return candidate
    return None


def ffmpeg_encoders(path: str) -> str:
    """``Render.ffmpegEncoders``: the text of ``ffmpeg -encoders``."""
    return run_process(path, ["-hide_banner", "-encoders"], deadline_s=10)[1]


def encode(
    path: str,
    args: Sequence[str],
    is_cancelled: Callable[[], bool] | None = None,
    on_output: Callable[[str], None] | None = None,
) -> bool:
    """``Render.encode``: run ffmpeg with ``args``; True when it succeeded and the video (the last argument) exists."""
    code, _ = run_process(path, args, None, is_cancelled, on_output)
    return code == 0 and bool(args) and os.path.exists(args[-1])
