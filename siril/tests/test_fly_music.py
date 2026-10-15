"""The Fly-Through dialog's Music row (a text box, Browse..., Fade in/out): the rules, the check of the file, what is
remembered, and how the music reaches the job and the ffmpeg arguments (compared with the arguments PixInsight's
Fly.ffmpegArgs makes for the same inputs, recorded in tests/oracle/encode.json). Recording tk stand-ins; tiny files;
no window, and ffmpeg is never run."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from loomsiril import encode, fly_checks
from loomsiril import pipeline as P
from loomsiril import siril_bridge as sb
from loomsiril.fly_model import FlyDialogModel
from test_siril_script import (_buttons, _dialog, _fake_filedialog, _fake_tkinter, _settle, _tools,  # noqa: F401
                               blocked_discover, fake_pipeline, script, stub)

AUDIO_TYPES = "*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus"


def model(**kw) -> FlyDialogModel:
    base = dict(mode="files", tools="found", tool_id="starnet2", presets=("youtube_1080",), ffmpeg=True, out_dir="/out",
                starless_path="a.tif", stars_path="b.tif",
                checked={("starless", "a.tif"): "", ("stars", "b.tif"): "", ("dir", "/out"): ""})
    base.update(kw)
    return FlyDialogModel(**base)


# ---- the rules -----------------------------------------------------------------------------------------------------

def test_the_music_row_applies_only_with_ffmpeg_and_a_ticked_encode_video_box():
    assert model(video=True, ffmpeg=True).enabled("music") and model(video=True, ffmpeg=None).enabled("music")
    assert not model(video=False).enabled("music")
    assert not model(ffmpeg=False).enabled("music")


def test_the_grey_reason_says_ffmpeg_or_the_untick_or_the_file_problem():
    assert "ffmpeg" in model(ffmpeg=False).music_note()
    assert "Encode video" in model(video=False).music_note()
    assert model(music_path="").music_note() == ""
    bad = model(music_path="/m/a.mp3", checked={("music", "/m/a.mp3"): "The music file does not exist"})
    assert bad.music_note() == "The music file does not exist"


def test_a_missing_music_file_blocks_render_only_while_the_music_is_used():
    gone = {("starless", "a.tif"): "", ("stars", "b.tif"): "", ("dir", "/out"): "",
            ("music", "/m/a.mp3"): "The music file does not exist or cannot be read: /m/a.mp3"}
    assert model(music_path="/m/a.mp3", checked=gone).reasons() == [gone[("music", "/m/a.mp3")]]
    assert not model(music_path="/m/a.mp3", checked=gone).can_render()
    assert model(music_path="/m/a.mp3", checked=gone, video=False).reasons() == []      # greyed: not used
    assert model(music_path="/m/a.mp3", checked=gone, ffmpeg=False).reasons() == []
    assert ("music", "/m/a.mp3") not in model(music_path="/m/a.mp3", video=False).wanted_paths()
    assert model(music_path=" /m/a.mp3 ").wanted_paths()[-1] == ("music", "/m/a.mp3")
    assert model(music_path="/m/a.mp3").reasons() == ["Checking the files..."]          # not looked at yet


def test_the_worker_checks_the_music_file(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3")
    assert fly_checks.validate([("music", str(song))]) == {("music", str(song)): ""}
    gone = str(tmp_path / "gone.mp3")
    assert fly_checks.validate([("music", gone)])[("music", gone)] == \
        f"The music file does not exist or cannot be read: {gone}"
    if os.name == "posix" and os.geteuid() != 0:
        song.chmod(0)
        try:
            assert "cannot be read" in fly_checks.check_file("music", str(song))
        finally:
            song.chmod(0o600)


# ---- what is remembered ----------------------------------------------------------------------------------------------

def test_the_music_path_and_the_fade_round_trip_and_an_empty_box_is_forgotten(tmp_path):
    sb.save_options({"music_path": "/m/a.mp3", "music_fade": False, "fps": 30}, tmp_path)
    assert sb.load_options(tmp_path) == {"music_path": "/m/a.mp3", "music_fade": False, "fps": 30}
    assert "music_path" in sb.PATH_KEYS
    sb.save_options({"music_path": " ", "fps": 30}, tmp_path)
    assert sb.load_options(tmp_path) == {"music_fade": False, "fps": 30}
    sb.options_path(tmp_path).write_text('{"music_path": 5, "music_fade": true}')
    assert sb.load_options(tmp_path) == {"music_fade": True}                 # a path that is not text is not loaded


def test_saved_music_reaches_the_boxes_and_defaults_to_fade_on(stub, script, monkeypatch, blocked_discover, tmp_path):
    sb.save_options({"music_path": "/m/a.mp3", "music_fade": False}, tmp_path)
    dlg, _ = _dialog(script, monkeypatch, blocked_discover, **sb.load_options(tmp_path))
    assert dlg._vars["music_path"].get() == "/m/a.mp3" and dlg._fade.get() is False
    fresh, _ = _dialog(script, monkeypatch, blocked_discover)
    assert fresh._vars["music_path"].get() == "" and fresh._fade.get() is True       # PixInsight's default: fade on


def test_the_snapshot_keeps_the_typed_music_path_even_when_it_is_not_there(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    dlg._vars["music_path"].set("/m/gone.mp3")
    dlg._fade.set(False)
    snap = dlg._snapshot()
    assert snap["music_path"] == "/m/gone.mp3" and snap["music_fade"] is False


# ---- the controls --------------------------------------------------------------------------------------------------

def _states(dlg) -> list:
    return [w.kw.get("state") for w, _on in dlg._w["music"]]


def test_the_controls_exist_and_grey_with_encode_video_and_ffmpeg(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    from loomsiril import fly_tools

    assert len(dlg._w["music"]) == 4                                    # label, box, Browse..., Fade in/out
    dlg._on_discovered(fly_tools.Discovery(ffmpeg=True))
    assert set(_states(dlg)) <= {"normal"} and dlg._music_note.get() == ""
    dlg._video.set(False)
    assert set(_states(dlg)) == {"disabled"} and "Encode video" in dlg._music_note.get()
    dlg._video.set(True)
    assert set(_states(dlg)) == {"normal"}
    dlg._on_discovered(fly_tools.Discovery(ffmpeg=False))
    assert set(_states(dlg)) == {"disabled"} and "ffmpeg" in dlg._music_note.get()


def test_browse_fills_the_music_box_with_audio_files_and_shares_the_last_folder(stub, script, monkeypatch,
                                                                              blocked_discover):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    calls = _fake_filedialog(monkeypatch, ["/data/music/song.flac", ""])
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({}), blocked_discover, None)
    music_btn = _buttons(dlg, "Browse...")[-1]
    music_btn.kw["command"]()
    assert dlg._vars["music_path"].get() == "/data/music/song.flac" and dlg._last_dir == "/data/music"
    patterns = " ".join(p for _label, p in calls[0]["filetypes"])
    assert AUDIO_TYPES in patterns and "*.tif" not in patterns
    music_btn.kw["command"]()                                           # cancelled: the box stays as it was
    assert dlg._vars["music_path"].get() == "/data/music/song.flac" and calls[1]["initialdir"] == "/data/music"


def test_a_missing_remembered_music_file_stays_in_its_box_shows_the_reason_and_blocks_render(
        stub, script, monkeypatch, blocked_discover, tmp_path):
    from loomsiril import fly_tools

    (tmp_path / "a.tif").write_text("x")
    gone = str(tmp_path / "gone.mp3")
    dlg, _ = _dialog(script, monkeypatch, blocked_discover, starless_path=str(tmp_path / "a.tif"),
                     stars_path=str(tmp_path / "a.tif"), out_dir=str(tmp_path), music_path=gone)
    dlg._on_discovered(fly_tools.Discovery(ffmpeg=True))
    _settle(dlg)
    assert dlg._vars["music_path"].get() == gone                          # kept, not dropped
    assert "music file does not exist or cannot be read" in dlg._reason.get()
    assert "music file does not exist" in dlg._music_note.get()
    assert dlg._render_btn.kw["state"] == "disabled"
    dlg._video.set(False)                                               # not used any more: no longer a reason
    assert dlg._reason.get() == "" and dlg._render_btn.kw["state"] == "normal"
    dlg._video.set(True)
    (tmp_path / "gone.mp3").write_bytes(b"ID3")                           # fixed
    dlg._recheck()
    _settle(dlg)
    assert dlg._reason.get() == "" and dlg._render_btn.kw["state"] == "normal"


def test_values_carry_the_music_and_the_fade(stub, script, monkeypatch, blocked_discover):
    from loomsiril import presets

    dlg, _ = _dialog(script, monkeypatch, blocked_discover, music_path="/m/a.mp3", music_fade=False)
    v = dlg.values(presets)
    assert v["music_path"] == "/m/a.mp3" and v["music_fade"] is False


# ---- the job and the ffmpeg arguments --------------------------------------------------------------------------------

def _job_values(**kw):
    import numpy as np

    planes = [np.zeros((4, 4), np.float32)]
    values = {"starless": planes, "stars": planes, "video": True, "music_path": " /m/a.mp3 ", "music_fade": False}
    values.update(kw)
    return values


def _job(**kw):
    header = {"CRVAL1": 10.0, "CRVAL2": 20.0, "CRPIX1": 2.0, "CRPIX2": 2.0, "CD1_1": -0.001, "CD1_2": 0.0,
              "CD2_1": 0.0, "CD2_2": 0.001, "NAXIS1": 4, "NAXIS2": 4}
    values = _job_values(**kw)
    return sb.job_from_dialog(values, values["starless"], header, None)


def test_the_job_carries_the_music_and_the_fade_only_when_the_video_is_encoded():
    job = _job()
    assert job.music_path == "/m/a.mp3" and job.music_fade is False
    assert _job(music_fade=True).music_fade is True
    assert _job(video=False).music_path == ""                              # no video, no music
    assert _job(music_path="").music_path == "" and _job(music_path=None).music_path == ""


def _opts(job) -> dict:
    return P._render_opts(job, 500.0, {"note": "n", "colour": "c"}, [])


def _preset_render(spec_id="youtube_1080", loop="none", n=600, transfer="sdr", folder="/f", out="/o/x"):
    from loomsiril import presets

    spec = presets.preset_spec(spec_id, "horizontal", loop)
    po = {"transfer": transfer}
    if transfer != "sdr":
        po["output"] = SimpleNamespace(stats={"max_cll": 850, "max_fall": 120})
        po["peak"] = 1000
    return P.PresetRender(spec=spec, n=n, F=0, scene=None, po=po, crop={}, folder=folder, out_base=out)


def _ffmpeg_case(oracle, index):
    return oracle("encode")["cases"]["ffmpegArgs"][index]


def _oracle_with(oracle, path, fade, duration, loop, fmt):
    for c in oracle("encode")["cases"]["ffmpegArgs"]:
        a = c["args"][6] if len(c["args"]) > 6 else None
        if a and a["path"] == path and a["fade"] == fade and a["duration"] == duration and a["loop"] == loop \
                and c["args"][3] == fmt:
            return c
    raise AssertionError("no recorded case")


def test_the_music_reaches_the_encode_arguments_like_pixinsights_for_a_plain_clip(oracle):
    opts = {**_opts(_job(music_fade=True, music_path="/m/a.mp3")), "fps": 30}
    got = " ".join(P._encode_args(_preset_render(n=600), opts))           # 600 frames at 30 fps: a 20 s clip
    case = _oracle_with(oracle, "/m/a.mp3", True, 20, False, "h264")
    assert got == case["out"]
    assert "-stream_loop -1 -i /m/a.mp3" in got and "afade=t=in:st=0:d=2,afade=t=out:st=18:d=2" in got


def test_a_short_clip_fades_over_a_quarter_of_it_like_pixinsight(oracle):
    opts = {**_opts(_job(music_fade=True, music_path="/m/a.mp3")), "fps": 30}
    got = P._audio(_preset_render(n=75), opts)                             # 2.5 s
    assert got == {"path": "/m/a.mp3", "fade": True, "duration": 2.5, "loop": False}
    want = oracle("encode")["cases"]["audioArgs"][3]                       # the 2.5 s case: d = 0.625
    assert encode.audio_args(got, "prores") == want["out"]
    assert "afade=t=in:st=0:d=0.625,afade=t=out:st=1.875:d=0.625" in " ".join(
        P._encode_args(_preset_render(n=75), opts))


def test_a_looping_preset_crossfades_the_music_end_into_start_like_pixinsight(oracle):
    opts = {**_opts(_job(music_fade=True, music_path="/m/a.mp3")), "fps": 30, "dynamic": "hdr"}
    got = " ".join(P._encode_args(_preset_render(loop="crossfade", n=300, transfer="pq"), opts))   # 10 s, HDR10
    case = _oracle_with(oracle, "/m/a.mp3", True, 10, True, "hevc")
    assert got == case["out"] and "afade=t=in:st=0:d=2" not in got and "amix=inputs=2" in got
    for loop in ("pingpong", "crossfade"):                                  # both loops, and the Exhibition (always)
        assert P._audio(_preset_render(loop=loop, n=300), opts)["loop"] is True
    assert P._audio(_preset_render("exhibition", loop="none", n=300), opts)["loop"] is True
    assert P._audio(_preset_render(loop="none", n=300), opts)["loop"] is False


def test_fade_off_and_no_music_leave_the_arguments_as_they_were():
    opts = {**_opts(_job(music_fade=False, music_path="/m/a.mp3")), "fps": 30}
    assert "afade" not in " ".join(P._encode_args(_preset_render(n=600), opts))
    silent = {**_opts(_job(music_path="")), "fps": 30}
    assert P._audio(_preset_render(), silent) is None
    assert P._encode_args(_preset_render(), silent) == encode.ffmpeg_args("/f", 30, "/o/x", "h264", "high", None, None)


def test_the_music_is_an_encode_only_option_for_the_frame_signature():
    from loomsiril import presets

    a, b = _opts(_job(music_path="/m/a.mp3")), _opts(_job(music_path="/m/b.mp3", music_fade=True))
    spec = presets.preset_spec("youtube_1080", "horizontal", "none")
    assert presets.frame_signature(a, spec, "k") == presets.frame_signature(b, spec, "k")
