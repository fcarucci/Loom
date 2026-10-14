"""loomsiril.encode against the JS oracle (tests/oracle/encode.json) and against a fake ffmpeg.

ffmpeg itself is never needed for these: the command building is pinned by the
oracle, and the process runner is exercised with an executable shell script
written into ``tmp_path`` that prints ``frame=`` lines to stderr and creates
the output file (or fails, or sleeps for the cancel and deadline paths). The
one test that runs the real ``ffmpeg -version`` carries the ``ffmpeg`` marker
and skips here.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from loomsiril import encode
from loomsiril.encode import (
    AUDIO_FADE,
    AUDIO_LOOP_FADE,
    HDR_PEAK_DEFAULT,
    HDR_TRC,
    REC709_FILTER,
    VIDEO_ENCODING_TAGS,
    VIDEO_FORMATS,
)

ENC = " V....D libx264  H.264\n V....D libx265  H.265\n V....D prores_ks ProRes\n"
ENC_HDR = " V....D libx264 \n V....D libx265 \n V....D prores_ks \n V....D libvpx-vp9 \n"
HDR_PQ = {"transfer": "pq", "peak": 1000, "maxCll": 850, "maxFall": 120}
HDR_HLG = {"transfer": "hlg"}


def _format_ids(formats: list[dict]) -> list[str]:
    return [f["id"] for f in formats]


def _rep(v: Any) -> Any:
    """Tuples to lists, so results compare with the JSON twins."""
    if isinstance(v, (list, tuple)):
        return [_rep(x) for x in v]
    if isinstance(v, dict):
        return {k: _rep(x) for k, x in v.items()}
    return v


def _formats_from(encoders: str, hdr: bool = False) -> list[dict]:
    return encode.available_formats(encoders, hdr)


FUNCS: dict[str, Callable[..., Any]] = {
    "availableFormats": encode.available_formats,
    "defaultFormat": lambda formats, width: encode.default_format(formats, width),
    "isHdr": encode.is_hdr,
    "hdrFilter": encode.hdr_filter,
    "x265Hdr10": encode.x265_hdr10,
    "codecArgs": encode.codec_args,
    "ffmpegArgs": lambda *a: " ".join(encode.ffmpeg_args(*a)),
    "videoName": encode.video_name,
    "audioFade": encode.audio_fade,
    "audioArgs": encode.audio_args,
    "loopAudio": encode.loop_audio,
    "ffmpegCandidates": encode.ffmpeg_candidates,
    "ffmpegInstallHint": encode.ffmpeg_install_hint,
    "commandLine": encode.command_line,
    "ffmpegFrameProgress": encode.ffmpeg_frame_progress,
}


def _close(got: Any, want: Any, where: str) -> None:
    got = _rep(got)
    if want is None or isinstance(want, (str, bool)):
        assert got == want and type(got) is type(want), (where, got, want)
    elif isinstance(want, dict):
        assert isinstance(got, dict) and set(got) == set(want), (where, got, want)
        for k in want:
            _close(got[k], want[k], f"{where}.{k}")
    elif isinstance(want, list):
        assert isinstance(got, list) and len(got) == len(want), (where, got, want)
        for i, (g, w) in enumerate(zip(got, want)):
            _close(g, w, f"{where}[{i}]")
    else:
        assert isinstance(got, (int, float)) and not isinstance(got, bool), (where, got, want)
        assert abs(got - want) <= 1e-12, (where, got, want)


@pytest.mark.parametrize("name", sorted(FUNCS))
def test_oracle_replay(oracle, name):
    cases = oracle("encode")["cases"][name]
    assert cases, f"no recorded cases for {name}"
    for case in cases:
        got = FUNCS[name](*case["args"])
        _close(got, case["out"], f"{name}{case['args']!r}"[:200])


def test_constants_match_js(oracle):
    c = oracle("encode")["cases"]["constants"]
    assert _rep(VIDEO_FORMATS) == c["VIDEO_FORMATS"]
    assert REC709_FILTER == c["REC709_FILTER"]
    assert HDR_TRC == c["HDR_TRC"]
    assert HDR_PEAK_DEFAULT == c["HDR_PEAK_DEFAULT"] == 1000
    assert VIDEO_ENCODING_TAGS == c["VIDEO_ENCODING_TAGS"]
    assert AUDIO_FADE == c["AUDIO_FADE"] == 2 and AUDIO_LOOP_FADE == c["AUDIO_LOOP_FADE"] == 1


def test_4k_defaults_to_hevc_and_1080_to_h264():
    av = _formats_from(ENC)
    assert _format_ids(av) == ["h264", "hevc", "prores"]
    assert encode.default_format(av, 3840) == "hevc"
    assert encode.default_format(av, 1920) == "h264"
    assert encode.default_format(_formats_from(" libx264 "), 3840) == "h264"


def test_hdr_offers_no_h264_and_defaults_to_hevc_at_1080():
    av = _formats_from(ENC_HDR, True)
    assert _format_ids(av) == ["hevc", "prores", "vp9"]
    assert encode.default_format(av, 1920) == "hevc"


def test_ffmpeg_args_sdr_h264_high():
    a = encode.ffmpeg_args("/f", 25, "/o/youtube", "h264", "high")
    assert a[:5] == ["-y", "-framerate", "25", "-i", "/f/frame_%05d.tif"]
    assert " ".join(a).find("-c:v libx264 -crf 18") >= 0
    assert a[-1] == "/o/youtube.mp4"


def test_ffmpeg_args_hevc_tag_and_pq_metadata():
    hevc = " ".join(encode.ffmpeg_args("/f", 30, "/o/x", "hevc", "standard"))
    assert "-tag:v hvc1" in hevc
    pq = " ".join(encode.ffmpeg_args("/f", 30, "/o/x", "hevc", "standard", HDR_PQ))
    assert "master-display=G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(10000000,1)" in pq
    assert "max-cll=850,120" in pq and "yuv420p10le" in pq


def test_command_line_quoting_macos_and_windows():
    assert encode.command_line("/opt/homebrew/bin/ffmpeg", ["-y", "/Volumes/My Disk/x.mp4"], "macos") == \
        "/opt/homebrew/bin/ffmpeg -y '/Volumes/My Disk/x.mp4'"
    assert encode.command_line("C:/Program Files/ffmpeg/bin/ffmpeg.exe", ["-i", "D:/My Frames/frame_%05d.tif"], "windows") == \
        '"C:/Program Files/ffmpeg/bin/ffmpeg.exe" -i "D:/My Frames/frame_%05d.tif"'
    # a single quote inside a macOS argument closes, escapes and reopens the quotes; no shlex
    assert encode.command_line("ff", ["it's"], "macos") == "ff 'it'\\''s'"
    assert encode.command_line("ff", ['a "b"'], "windows") == 'ff "a \\"b\\""'


def test_ffmpeg_candidates_order_macos():
    mac = encode.ffmpeg_candidates("macos", "/Users/u", "/usr/bin:/opt/x/bin:", "")
    assert mac == ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "/opt/x/bin/ffmpeg"]
    assert encode.ffmpeg_candidates("macos", "/Users/u", "", "/s/ffmpeg")[0] == "/s/ffmpeg"


def test_ffmpeg_candidates_order_windows():
    win = encode.ffmpeg_candidates(
        "windows", "C:/Users/u", 'C:\\Windows\\system32;"C:\\Tools\\ffmpeg\\bin";;C:\\Tools\\ffmpeg\\bin\\', ""
    )
    assert win[0] == "C:/Users/u/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe"
    assert win[1:5] == [
        "C:/ProgramData/chocolatey/bin/ffmpeg.exe",
        "C:/Users/u/scoop/shims/ffmpeg.exe",
        "C:/ffmpeg/bin/ffmpeg.exe",
        "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
    ]
    assert win[5:] == ["C:/Windows/system32/ffmpeg.exe", "C:/Tools/ffmpeg/bin/ffmpeg.exe"]
    assert encode.ffmpeg_candidates("windows", "", "", "")[0] == "C:/ProgramData/chocolatey/bin/ffmpeg.exe"


def test_install_hints():
    assert "winget install Gyan.FFmpeg" in encode.ffmpeg_install_hint("windows")
    assert "brew install ffmpeg" in encode.ffmpeg_install_hint("macos")


def test_frame_progress_is_the_last_count():
    assert encode.ffmpeg_frame_progress("frame=   12 fps=3.1\rframe=   37 fps=3.2") == 37
    assert encode.ffmpeg_frame_progress("Input #0") is None


# ---- process running against a fake ffmpeg -----------------------------------


def _fake(tmp_path: Path, body: str, name: str = "ffmpeg") -> str:
    """An executable shell script standing in for ffmpeg, written under tmp_path."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


PROGRESS = (
    "printf 'frame=    1 fps=0.0 q=28.0 size=0kB\\r' >&2\n"
    "printf 'frame=    3 fps=2.0 q=28.0 size=1kB\\r' >&2\n"
    "printf 'frame=    3 fps=2.0 q=28.0 size=1kB\\n' >&2\n"
)


def test_run_process_streams_stderr_and_returns_the_exit_code(tmp_path):
    fake = _fake(tmp_path, PROGRESS + "printf 'on stdout\\n'\nexit 0\n")
    chunks: list[str] = []
    code, out = encode.run_process(fake, ["-i", "x"], on_output=chunks.append)
    assert code == 0
    assert "frame=    3" in out and "on stdout" in out
    assert "".join(chunks) == out
    assert len(chunks) >= 1


def test_run_process_reports_a_nonzero_exit(tmp_path):
    fake = _fake(tmp_path, "echo failing >&2\nexit 3\n")
    code, out = encode.run_process(fake, [])
    assert code == 3 and "failing" in out


def test_run_process_passes_arguments_without_a_shell(tmp_path):
    """No shell: a $HOME, a space and %05d reach the program exactly as written."""
    fake = _fake(tmp_path, 'printf "%s\\n" "$@"\n')
    code, out = encode.run_process(fake, ["-i", "/x y/frame_%05d.tif", "$HOME"])
    assert code == 0
    assert out.splitlines() == ["-i", "/x y/frame_%05d.tif", "$HOME"]


def test_run_process_cancel_terminates_and_gives_minus_one(tmp_path):
    fake = _fake(tmp_path, "exec sleep 30\n")
    t0 = time.monotonic()
    code, _ = encode.run_process(fake, [], is_cancelled=lambda: True)
    assert code == -1
    assert time.monotonic() - t0 < 10


def test_run_process_deadline_gives_minus_one(tmp_path):
    fake = _fake(tmp_path, "exec sleep 30\n")
    t0 = time.monotonic()
    code, _ = encode.run_process(fake, [], deadline_s=0.5)
    assert code == -1
    assert time.monotonic() - t0 < 10


def test_run_process_without_a_deadline_or_cancel_runs_to_the_end(tmp_path):
    """deadline_s=0 means no deadline (the check is truthy), and an is_cancelled that says False never stops the run."""
    fake = _fake(tmp_path, "printf 'done\\n'\nexit 5\n")
    code, out = encode.run_process(fake, [], deadline_s=0, is_cancelled=lambda: False)
    assert code == 5 and out == "done\n"


def test_run_process_missing_program_gives_minus_one(tmp_path):
    code, out = encode.run_process(str(tmp_path / "no-such-ffmpeg"), ["-version"])
    assert code == -1 and out


def test_encode_true_when_exit_zero_and_output_exists(tmp_path):
    out = tmp_path / "video.mp4"
    fake = _fake(tmp_path, PROGRESS + 'for last; do :; done\n: > "$last"\nexit 0\n')
    seen: list[str] = []
    assert encode.encode(fake, ["-y", "-i", "x", str(out)], on_output=seen.append) is True
    assert out.exists()
    assert "frame=    3" in "".join(seen)


def test_encode_false_when_ffmpeg_exits_one(tmp_path):
    out = tmp_path / "video.mp4"
    fake = _fake(tmp_path, 'for last; do :; done\n: > "$last"\nexit 1\n')
    assert encode.encode(fake, ["-y", str(out)]) is False


def test_encode_false_when_no_output_file(tmp_path):
    out = tmp_path / "video.mp4"
    fake = _fake(tmp_path, "exit 0\n")
    assert encode.encode(fake, ["-y", str(out)]) is False


def test_encode_false_when_cancelled(tmp_path):
    out = tmp_path / "video.mp4"
    fake = _fake(tmp_path, "exec sleep 30\n")
    assert encode.encode(fake, ["-y", str(out)], is_cancelled=lambda: True) is False


def test_is_working_ffmpeg_answers_version_only_for_ffmpeg(tmp_path):
    good = _fake(tmp_path, 'echo "ffmpeg version 7.1-fake"\n', name="good")
    bad = _fake(tmp_path, 'echo "something else"\n', name="bad")
    assert encode.is_working_ffmpeg(good) is True
    assert encode.is_working_ffmpeg(bad) is False
    assert encode.is_working_ffmpeg(str(tmp_path / "absent")) is False


def test_ffmpeg_encoders_is_the_encoder_listing(tmp_path):
    fake = _fake(tmp_path, 'case "$*" in *-encoders*) echo " V....D libx264  H.264";; esac\n')
    assert "libx264" in encode.ffmpeg_encoders(fake)


def test_find_ffmpeg_takes_the_first_working_candidate(tmp_path, monkeypatch):
    """Windows candidates are fixed paths that do not exist here, so PATH decides."""
    fake = _fake(tmp_path, 'echo "ffmpeg version 7.1-fake"\n', name="ffmpeg.exe")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert encode.find_ffmpeg("windows", saved="") == fake


def test_find_ffmpeg_none_when_nothing_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert encode.find_ffmpeg("windows", saved="") is None


def test_find_ffmpeg_prefers_the_saved_path(tmp_path, monkeypatch):
    saved = _fake(tmp_path, 'echo "ffmpeg version 7.1-fake"\n', name="saved")
    monkeypatch.setenv("PATH", "")
    assert encode.find_ffmpeg("linux", saved=saved) == saved


@pytest.mark.ffmpeg
def test_real_ffmpeg_answers_version():
    path = shutil.which("ffmpeg")
    assert path is not None
    assert encode.is_working_ffmpeg(path) is True


def test_platform_names_map_sys_platform():
    assert encode.platform_name("darwin") == "macos"
    assert encode.platform_name("win32") == "windows"
    assert encode.platform_name("linux") == "linux"


# ---- R1-e characterization (old code): process runner branches no other test reaches ----------------


def test_run_process_flushes_an_incomplete_utf8_tail(tmp_path):
    """An output that ends inside a multi-byte character: the tail is decoded with replacement, not dropped."""
    fake = _fake(tmp_path, "printf '\\303' >&2\nexit 0\n")
    chunks: list[str] = []
    code, out = encode.run_process(fake, [], on_output=chunks.append)
    assert code == 0
    assert out == "�" and "".join(chunks) == out


def test_run_process_deadline_keeps_the_output_so_far(tmp_path):
    fake = _fake(tmp_path, "printf 'partial line\\n' >&2\nexec sleep 30\n")
    t0 = time.monotonic()
    code, out = encode.run_process(fake, [], deadline_s=1.0)
    assert code == -1
    assert "partial line" in out
    assert time.monotonic() - t0 < 10


def test_run_process_callback_error_stops_the_process_and_propagates(tmp_path):
    fake = _fake(tmp_path, "printf 'x\\n' >&2\nexec sleep 30\n")

    def boom(_text: str) -> None:
        raise RuntimeError("boom")

    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="boom"):
        encode.run_process(fake, [], on_output=boom)
    assert time.monotonic() - t0 < 10


def test_stop_on_a_finished_process_does_nothing():
    import subprocess

    proc = subprocess.Popen(["true"])
    proc.wait()
    encode._stop(proc)  # returns at once: poll() is not None
    assert proc.returncode == 0


def test_stop_kills_a_process_that_ignores_terminate(tmp_path):
    """SIGTERM is ignored by the shell; after five seconds the process is killed (the test takes about five)."""
    import subprocess

    fake = _fake(tmp_path, "trap '' TERM\nsleep 7\n")
    proc = subprocess.Popen([fake], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.2)
    encode._stop(proc)
    assert proc.poll() is not None
    assert proc.returncode != 0
