"""The Video details section of the Fly-Through dialog (fake tk, no window): Format from the probe, Quality, the ffmpeg
path and its checks, Clear rendered frames with its confirmation, the estimate line, what is saved and what the job gets.

The dialog's worker calls are made synchronous (``_background`` runs the work at once), so each click runs end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from loomsiril import fly_model, fly_video
from test_fly_video import ENCODERS_ALL, ENCODERS_NO_X265, fake_ffmpeg, posix_only
from test_siril_script import _fake_grid_rows, _fake_tkinter, _open_dialog, blocked_discover, script, stub  # noqa: F401


@pytest.fixture
def dlg(stub, script, monkeypatch, blocked_discover, tmp_path):  # noqa: F811
    _fake_tkinter(monkeypatch)
    d = _open_dialog(script, blocked_discover, {"out_dir": str(tmp_path / "out"), "duration": 4.0, "fps": 25})
    d._background = lambda work, done, name: done(_run(work))
    fly_video.forget_probes()
    return d


def _run(work: Any) -> Any:
    try:
        return work()
    except Exception as exc:  # the dialog's worker hands the exception to the callback
        return exc


def _messagebox(monkeypatch: pytest.MonkeyPatch, answer: bool) -> list:
    asked: list = []
    mb = ModuleType("tkinter.messagebox")
    mb.askyesno = lambda title, text, **kw: asked.append(text) or answer
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", mb)
    sys.modules["tkinter"].messagebox = mb
    return asked


def _found(dlg: Any, path: str) -> Any:
    """Discovery found ``path``: the probe runs and the dialog takes its answer."""
    dlg._ffmpeg_found = path
    dlg._ffmpeg = True
    dlg._video_probe()
    return dlg._vid_probe


def _labels(dlg: Any) -> list[str]:
    return list(dlg._fmt_box.kw["values"])


def test_the_section_has_the_pixinsight_controls(dlg) -> None:
    dlg._toggle_section("video", True)
    rows = _fake_grid_rows(dlg._sections["video"].frame)
    texts = [(c, kind, text) for _r, c, kind, text in rows]
    assert (0, "Label", "ffmpeg") in texts and (2, "Button", "Browse...") in texts
    assert (0, "Label", "Format") in texts and (0, "Label", "Quality") in texts
    assert (1, "Button", "Clear rendered frames...") in texts
    assert [k for _r, _c, k, _t in rows].count("Combobox") == 2


@posix_only
def test_format_lists_only_what_this_ffmpeg_can_encode(dlg, tmp_path: Path) -> None:
    probe = _found(dlg, fake_ffmpeg(tmp_path, ENCODERS_NO_X265))
    assert probe.ok
    assert _labels(dlg) == ["MP4 · H.264", "WebM · VP9"]
    assert dlg._fmt_var.get() == "MP4 · H.264"                      # 1080 wide: H.264 is the default
    assert "ffmpeg version 7.1-fake" in dlg._vid_note.get()
    dlg._vars["dynamic"].set("hdr")                                      # H.264 is SDR only
    assert _labels(dlg) == ["WebM · VP9"] and dlg._fmt_var.get() == "WebM · VP9"
    dlg._vars["dynamic"].set("sdr")


@posix_only
def test_a_wide_video_defaults_to_hevc_until_the_user_chooses(dlg, tmp_path: Path) -> None:
    _found(dlg, fake_ffmpeg(tmp_path, ENCODERS_ALL))
    dlg._presets["youtube_4k"].set(True)
    assert dlg._fmt_var.get() == "MP4 · H.265/HEVC"
    dlg._fmt_var.set("MOV · ProRes 422 HQ")                         # the user picks
    dlg._fmt_box.bindings["<<ComboboxSelected>>"][0]()
    dlg._presets["youtube_4k"].set(False)
    assert dlg._fmt_var.get() == "MOV · ProRes 422 HQ" and dlg._control_values()["format"] == "prores"


@posix_only
def test_the_saved_format_returns_when_this_ffmpeg_offers_it(stub, script, monkeypatch, blocked_discover, tmp_path) -> None:  # noqa: F811
    _fake_tkinter(monkeypatch)
    d = _open_dialog(script, blocked_discover, {"format": "vp9", "quality": "standard", "out_dir": str(tmp_path)})
    d._background = lambda work, done, name: done(_run(work))
    assert d._format_id() == "vp9"                                       # before any ffmpeg is known the saved one is kept
    assert d._control_values()["quality"] == "standard" and d._quality_var.get() == "Standard"
    _found(d, fake_ffmpeg(tmp_path, ENCODERS_ALL))
    assert d._fmt_var.get() == "WebM · VP9" and d._control_values()["format"] == "vp9"
    d2 = _open_dialog(script, blocked_discover, {"format": "vp9", "out_dir": str(tmp_path)})
    d2._background = d._background
    _found(d2, fake_ffmpeg(tmp_path, ENCODERS_NO_X265, name="ff2"))      # VP9 is there too: kept
    assert d2._format_id() == "vp9"
    d3 = _open_dialog(script, blocked_discover, {"format": "prores", "out_dir": str(tmp_path)})
    d3._background = d._background
    _found(d3, fake_ffmpeg(tmp_path, ENCODERS_NO_X265, name="ff3"))      # no ProRes here: the default, and that is what is saved
    assert d3._format_id() == "h264"


def test_no_ffmpeg_shows_the_install_hint_and_keeps_the_saved_format(stub, script, monkeypatch, blocked_discover, tmp_path) -> None:  # noqa: F811
    _fake_tkinter(monkeypatch)
    d = _open_dialog(script, blocked_discover, {"format": "hevc", "out_dir": str(tmp_path)})
    d._ffmpeg = False
    d._on_probe(None, "")
    assert "ffmpeg" in d._vid_note.get() and _labels(d) == [] and d._format_id() == "hevc"


def test_format_and_quality_grey_with_the_reason_unless_encoding_with_an_ffmpeg() -> None:
    m = fly_model.FlyDialogModel(ffmpeg=True, video=True)
    assert m.enabled("encode") and m.disabled_reason("encode") == ""
    assert not fly_model.FlyDialogModel(ffmpeg=True, video=False).enabled("encode")
    assert fly_model.FlyDialogModel(ffmpeg=True, video=False).disabled_reason("encode") == "needs Encode video"
    gone = fly_model.FlyDialogModel(ffmpeg=False, video=True)
    assert not gone.enabled("encode") and gone.disabled_reason("encode") == "ffmpeg was not found"
    assert "encode" in fly_model.GROUPS


def test_a_typed_ffmpeg_is_checked_and_a_working_one_makes_up_for_none_found() -> None:
    typed = "/opt/ff/ffmpeg"
    bad = fly_model.FlyDialogModel(ffmpeg=True, video=True, controls={"ffmpeg_path": typed},
                                   checked={("ffmpeg", typed): "ffmpeg: /opt/ff/ffmpeg is not a file"},
                                   starless_path="/a", stars_path="/b", out_dir="/o", presets=["youtube_1080"])
    assert ("ffmpeg", typed) in bad.wanted_paths()
    assert "ffmpeg: /opt/ff/ffmpeg is not a file" in bad.reasons() and not bad.can_render()
    idle = fly_model.FlyDialogModel(ffmpeg=True, video=False, controls={"ffmpeg_path": typed},
                                    checked={("ffmpeg", typed): "ffmpeg: nope"}, mode="files")
    assert "ffmpeg: nope" not in idle.reasons()                          # not encoding: the path does not matter
    good = fly_model.FlyDialogModel(ffmpeg=False, video=True, controls={"ffmpeg_path": typed}, checked={("ffmpeg", typed): ""})
    assert good.enabled("video") and good.enabled("music") and good.video_note() == ""
    assert not fly_model.FlyDialogModel(ffmpeg=False, video=True).enabled("video")
    unchecked = fly_model.FlyDialogModel(ffmpeg=None, video=True, controls={"ffmpeg_path": ""})
    assert unchecked.enabled("video")                                    # still looking: counts as there


@posix_only
def test_the_validation_worker_runs_the_typed_ffmpeg(tmp_path: Path) -> None:
    from loomsiril import fly_checks

    good, absent = fake_ffmpeg(tmp_path), str(tmp_path / "absent")
    got = fly_checks.validate([("ffmpeg", good), ("ffmpeg", absent)])
    assert got[("ffmpeg", good)] == ""
    assert got[("ffmpeg", absent)].startswith("ffmpeg: ") and "not a file" in got[("ffmpeg", absent)]


@posix_only
def test_a_typed_ffmpeg_that_works_ticks_encode_video_when_none_was_found(dlg, tmp_path: Path) -> None:
    ff = fake_ffmpeg(tmp_path, ENCODERS_ALL)
    dlg._ffmpeg = False
    dlg._video.set(False)
    dlg._ctl["ffmpeg_path"][0].set(ff)
    dlg._video_probe()
    assert dlg._video.get() is True and dlg._checks[("ffmpeg", ff)] == ""
    assert dlg._model().ffmpeg_ok() and len(_labels(dlg)) == 4
    assert dlg._control_values()["ffmpeg_path"] == ff


@posix_only
def test_a_typed_ffmpeg_that_does_not_work_is_reported_and_blocks_render(dlg, tmp_path: Path) -> None:
    liar = tmp_path / "liar"
    liar.write_text('#!/bin/sh\necho hello\n', encoding="utf-8")
    liar.chmod(0o755)
    dlg._ffmpeg = True
    dlg._ctl["ffmpeg_path"][0].set(str(liar))
    dlg._video_probe()
    assert "did not answer -version" in dlg._vid_note.get() and _labels(dlg) == []
    assert any(r.startswith("ffmpeg: ") for r in dlg._model().reasons())


def test_a_probe_for_an_ffmpeg_no_longer_in_use_is_dropped(dlg, tmp_path: Path) -> None:
    dlg._ffmpeg_found = "/b"
    dlg._on_probe(fly_video.FfmpegProbe("/a", True, "v", ENCODERS_ALL), "/a")
    assert dlg._vid_probe is None


def test_browse_puts_the_chosen_binary_in_the_box(dlg, monkeypatch) -> None:
    fd = ModuleType("tkinter.filedialog")
    fd.askopenfilename = lambda **kw: "/opt/ff/ffmpeg"
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fd)
    sys.modules["tkinter"].filedialog = fd
    dlg._ffmpeg_browse.invoke()
    assert dlg._control_values()["ffmpeg_path"] == "/opt/ff/ffmpeg" and dlg._last_dir == "/opt/ff"


def test_the_estimate_line_follows_every_change(dlg) -> None:
    assert dlg._estimate.get() == ""                                     # no speed measured yet: no line (as PixInsight)
    dlg._ms_per_mp = 50.0
    dlg._video_changed()
    assert dlg._estimate.get() == "Estimated render: about 1 min"       # 100 frames x 2.07 MP x 50 ms = 10 s -> at least 1
    dlg._vars["duration"].set("600")
    assert dlg._estimate.get() == "Estimated render: about 26 min"      # 15000 frames x 2.0736 MP x 50 ms = 25.9 min
    dlg._vars["dynamic"].set("hdr")
    assert dlg._estimate.get() == "Estimated render: about 34 min"
    dlg._vars["duration"].set("abc")
    assert dlg._estimate.get() == ""


def test_clear_rendered_frames_confirms_deletes_only_ours_and_counts(dlg, tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    folder = out / "youtube_1080"
    folder.mkdir(parents=True)
    for i in range(3):
        (folder / f"frame_{i:05d}.tif").write_text("x", encoding="utf-8")
    (folder / "keep.txt").write_text("k", encoding="utf-8")
    (out / "M42_youtube_1080_SDR.mp4").write_text("v", encoding="utf-8")
    dlg._count_frames()
    assert dlg._clear_note.get() == "3 rendered frames in 1 preset folder"
    asked = _messagebox(monkeypatch, False)
    dlg._clear_btn.invoke()                                              # no: nothing is deleted
    assert len(asked) == 1 and "Delete 3 rendered frames in 1 preset folder" in asked[0] and len(list(folder.glob("frame_*"))) == 3
    assert dlg._clear_btn.kw["state"] == "normal"
    _messagebox(monkeypatch, True)
    dlg._clear_btn.invoke()                                              # yes
    assert not list(folder.glob("frame_*")) and (folder / "keep.txt").exists() and (out / "M42_youtube_1080_SDR.mp4").exists()
    assert dlg._clear_note.get() == "Cleared 3 rendered frames; the next render draws every frame afresh."
    asked = _messagebox(monkeypatch, True)
    dlg._clear_btn.invoke()                                              # nothing left: no question
    assert asked == [] and dlg._clear_note.get().startswith("No rendered frames to clear")


def test_the_values_carry_format_quality_and_ffmpeg_path(dlg, tmp_path: Path) -> None:
    dlg._quality_var.set("Standard")
    dlg._ctl["ffmpeg_path"][0].set("  /x/ffmpeg ")
    vals = dlg._control_values()
    assert vals["quality"] == "standard" and vals["ffmpeg_path"] == "/x/ffmpeg" and vals["format"] is None


def test_the_render_speed_is_kept_and_starts_the_next_estimate(stub, script, monkeypatch, blocked_discover, tmp_path) -> None:  # noqa: F811
    from types import SimpleNamespace

    from loomsiril import siril_bridge as bridge

    script.remember_speed(SimpleNamespace(ms_per_mp=None), bridge, tmp_path)         # nothing drawn (all frames reused)
    assert "ms_per_mp" not in bridge.load_options(tmp_path)
    script.remember_speed(SimpleNamespace(ms_per_mp=50.0), bridge, tmp_path)
    saved = bridge.load_options(tmp_path)
    assert saved["ms_per_mp"] == 50.0
    _fake_tkinter(monkeypatch)
    d = _open_dialog(script, blocked_discover, {**saved, "duration": 4.0, "fps": 25})
    assert d._estimate.get() == "Estimated render: about 1 min"


def test_the_video_rows_have_hover_tips(dlg) -> None:
    for widget in (dlg._fmt_box, dlg._clear_btn, dlg._ffmpeg_browse):
        assert "<Enter>" in widget.bindings and "<Leave>" in widget.bindings
