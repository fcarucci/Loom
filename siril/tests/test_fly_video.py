"""The Fly-Through dialog's Video details (loomsiril/fly_video.py) against PixInsight's FlyThrough.js / Fly.js.

No real ffmpeg: a shell script that prints the version and the encoders stands in for it. The estimate's numbers are
FlyThrough.estimateMs worked by hand (frameCount x w x h / 1e6 x ms per megapixel, HDR x 1.3).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from loomsiril import encode, fly_video
from loomsiril import pipeline as P
from loomsiril.siril_bridge import OPTION_KEYS, PATH_KEYS, job_from_dialog

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="a shell script stands in for ffmpeg")

ENCODERS_ALL = " V....D libx264 H.264\n V....D libx265 H.265\n V....D prores_ks ProRes\n V....D libvpx-vp9 VP9\n"
ENCODERS_NO_X265 = " V....D libx264 H.264\n V....D libvpx-vp9 VP9\n"


def fake_ffmpeg(tmp: Path, encoders: str = ENCODERS_ALL, name: str = "ffmpeg", count: str | None = None,
                args_to: str | None = None) -> str:
    """An executable that answers -version and -encoders; any other call logs its arguments and writes its last argument."""
    lines = ["#!/bin/sh"]
    if count:
        lines.append(f'echo x >> "{count}"')
    lines += [
        'case "$*" in',
        '  *-version*) echo "ffmpeg version 7.1-fake";;',
        f'  *-encoders*) printf "%s" "{encoders}";;',
        "  *)",
    ]
    if args_to:
        lines.append(f'    echo "$@" > "{args_to}"')
    lines += ['    for last; do :; done', '    echo made > "$last";;', "esac"]
    path = tmp / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    fly_video.forget_probes()


# ---- (a) the capability probe -------------------------------------------------------------------------------------


@posix_only
def test_probe_lists_what_this_ffmpeg_can_encode(tmp_path: Path) -> None:
    ff = fake_ffmpeg(tmp_path, ENCODERS_NO_X265)
    found = fly_video.probe(ff)
    assert found.ok and found.version == "ffmpeg version 7.1-fake"
    assert [f["id"] for f in fly_video.formats_for(found)] == ["h264", "vp9"]
    assert [f["id"] for f in fly_video.formats_for(found, hdr=True)] == ["vp9"]       # H.264 is SDR only


@posix_only
def test_probe_runs_encoders_once_per_binary_and_again_when_the_binary_changes(tmp_path: Path) -> None:
    log = tmp_path / "calls"
    ff = fake_ffmpeg(tmp_path, ENCODERS_NO_X265, count=str(log))
    fly_video.probe(ff)
    first = len(log.read_text().splitlines())
    fly_video.probe(ff)
    fly_video.probe(ff)
    assert len(log.read_text().splitlines()) == first                                   # cached
    fake_ffmpeg(tmp_path, ENCODERS_ALL, count=str(log))                                 # replaced in place
    os.utime(ff, ns=(1, 2_000_000_000))                                                 # a new modification time
    assert [f["id"] for f in fly_video.formats_for(fly_video.probe(ff))] == ["h264", "hevc", "prores", "vp9"]


@posix_only
def test_probe_of_a_binary_that_is_not_ffmpeg_or_missing_offers_nothing(tmp_path: Path) -> None:
    bad = tmp_path / "other"
    bad.write_text('#!/bin/sh\necho "something else"\n', encoding="utf-8")
    bad.chmod(0o755)
    for path in (str(bad), str(tmp_path / "absent"), ""):
        found = fly_video.probe(path)
        assert not found.ok and found.reason
        assert fly_video.formats_for(found) == []
    assert fly_video.formats_for(None) == []


def test_choose_format_keeps_the_saved_one_else_the_default() -> None:
    av = encode.available_formats(ENCODERS_ALL)
    assert fly_video.choose_format(av, "prores", 1920) == "prores"
    assert fly_video.choose_format(av, None, 3840) == "hevc"                            # Fly.defaultFormat: 4K takes HEVC
    assert fly_video.choose_format(av, "h264", 3840) == "h264"                          # a saved choice is kept
    assert fly_video.choose_format(encode.available_formats(ENCODERS_NO_X265), "hevc", 3840) == "h264"
    assert fly_video.choose_format([], "hevc", 1920) is None


def test_widest_side_follows_the_orientation() -> None:
    assert fly_video.widest_side(["youtube_1080"], "landscape") == 1920
    assert fly_video.widest_side(["youtube_1080", "youtube_4k"], "vertical") == 3840
    assert fly_video.widest_side([]) == 0


# ---- (b) format and quality into the encode -----------------------------------------------------------------------


def test_the_encode_format_is_opts_format_or_h264_and_hevc_for_hdr() -> None:
    assert fly_video.encode_format(None, False) == "h264"
    assert fly_video.encode_format(None, True) == "hevc"                                # encodeArgs: opts.format || (hdr ? "hevc" : "h264")
    assert fly_video.encode_format("vp9", False) == "vp9"
    assert fly_video.encode_format("prores", True) == "prores"
    assert fly_video.encode_format("h264", True) == "hevc"                              # H.264 is SDR only
    assert fly_video.encode_format("bogus", False) == "h264"


def test_quality_ids() -> None:
    assert [q for q, _ in fly_video.QUALITIES] == ["high", "standard"]
    assert fly_video.quality_id("Standard") == "standard" and fly_video.quality_id("High") == "high"
    assert fly_video.quality_id(None) == "high"
    assert "-crf" in encode.ffmpeg_args("/f", 25, "/o/v", "hevc", "high") and \
        encode.ffmpeg_args("/f", 25, "/o/v", "hevc", "high")[encode.ffmpeg_args("/f", 25, "/o/v", "hevc", "high").index("-crf") + 1] == "20"
    std = encode.ffmpeg_args("/f", 25, "/o/v", "hevc", "standard")
    assert std[std.index("-crf") + 1] == "26"


@posix_only
@pytest.mark.parametrize("fmt,quality,ext,codec,crf", [
    ("prores", "high", ".mov", "prores_ks", None),
    ("vp9", "standard", ".webm", "libvpx-vp9", "32"),
    ("hevc", "standard", ".mp4", "libx265", "26"),
    (None, "high", ".mp4", "libx264", "18"),
])
def test_a_render_encodes_with_the_chosen_format_and_quality(tmp_path: Path, fmt: Any, quality: str, ext: str,
                                                              codec: str, crf: Any) -> None:
    from test_pipeline import _job  # the small synthetic field and its Gaia sample
    import synth

    field = synth.make_field(w=160, h=90, n_stars=12, nc=3, seed=3)
    log = tmp_path / "args.txt"
    ff = fake_ffmpeg(tmp_path, args_to=str(log))
    res = P.run(_job(tmp_path, field, video=True, ffmpeg=ff, duration=0.1, format=fmt, quality=quality))
    assert res.failed == [] and len(res.videos) == 1 and res.videos[0].endswith(ext)
    args = log.read_text().split()
    assert args[args.index("-c:v") + 1] == codec
    if crf:
        assert args[args.index("-crf") + 1] == crf


@posix_only
def test_a_format_this_ffmpeg_cannot_encode_falls_back_with_a_note(tmp_path: Path) -> None:
    from test_pipeline import _job
    import synth

    field = synth.make_field(w=160, h=90, n_stars=12, nc=3, seed=3)
    log = tmp_path / "args.txt"
    ff = fake_ffmpeg(tmp_path, ENCODERS_NO_X265, args_to=str(log))
    res = P.run(_job(tmp_path, field, video=True, ffmpeg=ff, duration=0.1, format="hevc"))
    assert res.videos and "libx264" in log.read_text()
    assert any("cannot encode" in n for n in res.notes)


# ---- (c) the ffmpeg path override ---------------------------------------------------------------------------------


@posix_only
def test_validate_runs_version_and_says_why_not(tmp_path: Path) -> None:
    assert fly_video.validate_ffmpeg(fake_ffmpeg(tmp_path)) == (True, "")
    ok, why = fly_video.validate_ffmpeg(str(tmp_path / "absent"))
    assert not ok and "not a file" in why
    plain = tmp_path / "plain.txt"
    plain.write_text("x", encoding="utf-8")
    plain.chmod(0o644)
    ok, why = fly_video.validate_ffmpeg(str(plain))
    assert not ok and "not executable" in why
    liar = tmp_path / "liar"
    liar.write_text('#!/bin/sh\necho "hello"\n', encoding="utf-8")
    liar.chmod(0o755)
    ok, why = fly_video.validate_ffmpeg(str(liar))
    assert not ok and "-version" in why
    assert fly_video.validate_ffmpeg("")[0] is False


def test_the_saved_keys_and_the_job_carry_format_quality_and_ffmpeg_path() -> None:
    assert {"format", "quality", "ffmpeg_path"} <= set(OPTION_KEYS) and "ffmpeg_path" in PATH_KEYS
    import synth

    field = synth.make_field(w=40, h=30, n_stars=3, nc=1, seed=1)
    wcs_header = {"CRVAL1": 10.0, "CRVAL2": 20.0, "CRPIX1": 20.0, "CRPIX2": 15.0, "CD1_1": -0.001, "CD1_2": 0.0,
                  "CD2_1": 0.0, "CD2_2": 0.001, "NAXIS1": 40, "NAXIS2": 30}
    values = {"starless": field.starless, "stars": field.stars, "format": "vp9", "quality": "standard",
              "ffmpeg_path": "  /opt/ff/ffmpeg ", "video": True}
    job = job_from_dialog(values, field.starless, wcs_header, None)
    assert (job.format, job.quality, job.ffmpeg) == ("vp9", "standard", "/opt/ff/ffmpeg")
    blank = job_from_dialog({"starless": field.starless, "stars": field.stars}, field.starless, wcs_header, None)
    assert (blank.format, blank.ffmpeg) == (None, None)


def test_the_encode_options_do_not_change_the_draft_key() -> None:
    from loomsiril import draft

    base = {"presets": ["youtube_1080"], "duration": 2.0, "fps": 10, "orientation": "landscape"}
    plain = draft.draft_signature(base)
    assert draft.draft_signature({**base, "format": "vp9", "quality": "standard", "ffmpeg_path": "/x/ffmpeg"}) == plain


# ---- (d) the estimate ---------------------------------------------------------------------------------------------


def test_estimate_ms_is_frames_times_megapixels_times_speed() -> None:
    # youtube_1080: 4 s x 25 fps = 100 frames of 1920x1080 = 2.0736 MP -> 100 x 2.0736 x 50 ms
    assert fly_video.estimate_ms(50, ["youtube_1080"], 4, 25, False, "landscape", "none") == pytest.approx(10368.0)
    assert fly_video.estimate_ms(50, ["youtube_1080"], 4, 25, True, "landscape", "none") == pytest.approx(10368.0 * 1.3)
    # the exhibition always loops there-and-back: twice the frames, 3840x2160
    assert fly_video.estimate_ms(10, ["exhibition"], 1, 10, False, "landscape", "none") == pytest.approx(
        20 * 3840 * 2160 / 1e6 * 10)
    # a loop choice doubles a preset that does not loop on its own; a crossfade does not
    base = fly_video.estimate_ms(10, ["youtube_1080"], 1, 10, False, "landscape", "none")
    assert fly_video.estimate_ms(10, ["youtube_1080"], 1, 10, False, "landscape", "pingpong") == pytest.approx(2 * base)
    assert fly_video.estimate_ms(10, ["youtube_1080"], 1, 10, False, "landscape", "crossfade") == pytest.approx(base)
    # the presets add up; vertical turns the 16:9 frame (same pixel count)
    two = fly_video.estimate_ms(10, ["youtube_1080", "social_square"], 1, 10, False, "vertical", "none")
    assert two == pytest.approx(10 * (1920 * 1080 + 1080 * 1080) / 1e6 * 10)


def test_estimate_text_rounds_like_the_js_and_is_blank_without_a_measurement() -> None:
    args = (["youtube_4k"], 60, 30, False, "landscape", "none")
    assert fly_video.estimate_text(None, *args) == ""                                    # PixInsight: no measurement, no line
    assert fly_video.estimate_text(0, *args) == ""
    assert fly_video.estimate_text(100, [], 60, 30, False, "landscape", "none") == ""
    # 1800 frames x 8.2944 MP x 100 ms = 1,492,992 ms = 24.9 min
    assert fly_video.estimate_text(100, *args) == "Estimated render: about 25 min"
    assert fly_video.estimate_text(0.001, *args) == "Estimated render: about 1 min"      # never under a minute
    assert fly_video.estimate_text(100, ["youtube_4k"], 60, 30, True, "landscape", "none") == "Estimated render: about 32 min"


def test_ms_per_mp() -> None:
    assert fly_video.ms_per_mp(207.36, 1920, 1080) == pytest.approx(100.0)


# ---- (e) clear rendered frames ------------------------------------------------------------------------------------


def _frames(out: Path, preset: str, n: int) -> Path:
    folder = out / preset
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (folder / f"frame_{i:05d}.tif").write_text("x", encoding="utf-8")
    return folder


def test_clear_frames_deletes_only_loom_frames_and_records_and_counts_them(tmp_path: Path) -> None:
    a = _frames(tmp_path, "youtube_1080", 3)
    (a / "frame_00003.tif.part").write_text("x", encoding="utf-8")
    (a / P.FRAMES_RECORD).write_text("sig", encoding="utf-8")
    (a / "notes.txt").write_text("keep", encoding="utf-8")
    b = _frames(tmp_path, "social_square_vertical", 2)
    other = _frames(tmp_path, "holiday", 2)                                              # not a preset folder: never touched
    video = tmp_path / "M31_youtube_1080_SDR.mp4"
    video.write_text("video", encoding="utf-8")
    (tmp_path / "frame_00000.tif").write_text("x", encoding="utf-8")                     # loose file in the output folder: kept

    assert fly_video.clear_frames(str(tmp_path), count_only=True) == {"frames": 5, "folders": 2}
    assert len(list(a.glob("frame_*.tif"))) == 3                                         # counting deletes nothing
    assert fly_video.clear_frames(str(tmp_path)) == {"frames": 5, "folders": 2}
    assert list(a.iterdir()) == [a / "notes.txt"]
    assert not b.exists()                                                                # emptied, so removed
    assert len(list(other.iterdir())) == 2 and video.exists() and (tmp_path / "frame_00000.tif").exists()
    assert fly_video.clear_frames(str(tmp_path)) == {"frames": 0, "folders": 0}


def test_clear_frames_on_no_folder_is_zero(tmp_path: Path) -> None:
    assert fly_video.clear_frames(str(tmp_path / "absent")) == {"frames": 0, "folders": 0}
    assert fly_video.clear_frames("") == {"frames": 0, "folders": 0}


def test_the_clear_texts() -> None:
    q = fly_video.confirm_text({"frames": 1, "folders": 1}, "/out")
    assert q.startswith("Delete 1 rendered frame in 1 preset folder of /out?") and "videos are kept" in q
    assert "2 rendered frames in 3 preset folders" in fly_video.confirm_text({"frames": 2, "folders": 3}, "/o")
    assert fly_video.cleared_text({"frames": 12, "folders": 2}) == \
        "Cleared 12 rendered frames; the next render draws every frame afresh."
    assert fly_video.nothing_text("") == "Choose an output folder first."


def test_a_render_measures_its_speed_for_the_estimate(tmp_path: Path) -> None:
    from test_pipeline import _job
    import synth

    field = synth.make_field(w=160, h=90, n_stars=12, nc=3, seed=3)
    res = P.run(_job(tmp_path, field, duration=0.2))
    assert res.written == 2 and res.ms_per_mp and res.ms_per_mp > 0
    assert res.ms_per_mp == pytest.approx(res.render_ms / res.render_mp)                 # SDR: no HDR factor
    again = P.run(_job(tmp_path, field, duration=0.2))                                   # every frame reused: nothing measured
    assert again.written == 0 and again.ms_per_mp is None
