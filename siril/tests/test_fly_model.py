"""The Fly-Through dialog's enable/disable rules (pure: no tkinter, no file access) and the file checks the worker runs."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from loomsiril import fly_checks
from loomsiril.fly_model import CHECKING, FlyDialogModel


def model(**kw) -> FlyDialogModel:
    base = dict(mode="files", tools="found", tool_id="starnet2", presets=("youtube_1080",), ffmpeg=True,
                out_dir="/out", starless_path="a.tif", stars_path="b.tif",
                checked={("starless", "a.tif"): "", ("stars", "b.tif"): "", ("dir", "/out"): ""})
    base.update(kw)
    return FlyDialogModel(**base)


def test_the_model_does_no_file_access(monkeypatch):
    for name in ("stat", "listdir", "access", "makedirs"):
        monkeypatch.setattr(os, name, lambda *a, **k: pytest.fail("the model touched the disk"))
    monkeypatch.setattr(os.path, "isfile", lambda *a: pytest.fail("the model touched the disk"))
    monkeypatch.setattr(os.path, "exists", lambda *a: pytest.fail("the model touched the disk"))
    assert model().reasons() == [] and model(starless_path="").reasons() == ["Choose the starless file."]


def test_each_mode_enables_its_own_group_and_greys_the_other():
    files, extract = model(mode="files"), model(mode="extract")
    assert files.enabled("files") and not files.enabled("extract")
    assert extract.enabled("extract") and not extract.enabled("files")


def test_without_a_tool_the_extract_group_and_radio_are_off_even_in_extract_mode():
    m = model(mode="extract", tools="none", tool_id="")
    assert not m.enabled("extract") and not m.enabled("extract_radio")
    assert "No star-extraction tool found" in m.reasons()[0]
    assert not model(tools="looking").enabled("extract_radio")
    assert model(mode="extract", tools="looking").reasons()[0].startswith("Still looking")


def test_reasons_for_missing_inputs_in_files_mode():
    assert model(starless_path="").reasons() == ["Choose the starless file."]
    assert model(stars_path=" ").reasons() == ["Choose the stars file."]
    bad = model(checked={("starless", "a.tif"): "", ("stars", "b.tif"): "The stars file does not exist or cannot be "
                                                                      "read: b.tif", ("dir", "/out"): ""})
    assert bad.reasons() == ["The stars file does not exist or cannot be read: b.tif"]
    assert model().reasons() == [] and model().can_render()


def test_a_path_the_worker_has_not_checked_yet_says_so_and_blocks_render():
    m = model(checked={})
    assert m.reasons() == [CHECKING] and not m.can_render()
    assert model(busy=True).can_render() is False


def test_extract_mode_image_file_is_optional_but_must_pass_the_check_when_given():
    ok = {("dir", "/out"): ""}
    assert model(mode="extract", image_path="", checked=ok).reasons() == []        # empty: the image open in Siril
    bad = model(mode="extract", image_path="/nope/m42.fits",
                checked={**ok, ("image", "/nope/m42.fits"): "The image file does not exist or cannot be read: "
                                                            "/nope/m42.fits"})
    assert bad.reasons() == ["The image file does not exist or cannot be read: /nope/m42.fits"]
    assert model(mode="extract", image_path="m42.fits", checked={**ok, ("image", "m42.fits"): ""}).reasons() == []
    assert model(mode="extract", image_path="m42.fits", checked=ok).reasons() == [CHECKING]


def test_no_preset_and_no_output_folder_are_reasons():
    assert model(presets=()).reasons() == ["Choose at least one output size."]
    assert model(out_dir="").reasons() == ["Choose an output folder."]


def test_video_is_off_with_a_reason_only_when_ffmpeg_is_known_missing():
    assert model(ffmpeg=None).enabled("video") and model(ffmpeg=True).enabled("video")
    m = model(ffmpeg=False)
    assert not m.enabled("video") and "ffmpeg was not found" in m.video_note()


def test_loop_choice_does_not_apply_when_only_the_exhibition_preset_is_chosen():
    assert not model(presets=("exhibition",)).enabled("loop")
    assert "always loops" in model(presets=("exhibition",)).loop_note()
    assert model(presets=("exhibition", "youtube_1080")).enabled("loop")
    assert model(presets=()).enabled("loop")


# ---------------------------------------------------------------- the worker's checks (these do touch the disk)

def test_check_file_and_validate(tmp_path):
    good = tmp_path / "a.tif"
    good.write_text("x")
    got = fly_checks.validate([("image", str(good)), ("stars", str(tmp_path / "gone.tif")), ("dir", str(tmp_path))])
    assert got[("image", str(good))] == "" and got[("dir", str(tmp_path))] == ""
    assert got[("stars", str(tmp_path / "gone.tif"))].startswith("The stars file does not exist or cannot be read")
    assert fly_checks.file_stamp(str(good)) is not None and fly_checks.file_stamp(str(tmp_path / "gone")) is None


def test_output_folder_check(tmp_path):
    assert fly_checks.check_out_dir(str(tmp_path)) is None
    assert fly_checks.check_out_dir(str(tmp_path / "new" / "deeper")) is None                # can be created
    assert fly_checks.check_out_dir(str(tmp_path / "new"), create=True) is None and (tmp_path / "new").is_dir()
    f = tmp_path / "file"
    f.write_text("x")
    assert "not a folder" in fly_checks.check_out_dir(str(f))
    assert "cannot be created" in fly_checks.check_out_dir(str(f / "sub"), create=True)
    assert fly_checks.check_out_dir("  ") == "Choose an output folder."


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can write anywhere")
def test_an_unwritable_output_folder_is_refused(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert "not writable" in fly_checks.check_out_dir(str(ro))
        assert "cannot be created" in fly_checks.check_out_dir(str(ro / "sub"))
    finally:
        ro.chmod(stat.S_IRWXU)
