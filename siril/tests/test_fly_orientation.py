"""The Fly-Through dialog's Orientation: the combo and its engine values, the default that follows the image (a portrait
image is Vertical), the live preset labels, what reaches the job and the draft key, and the four source x orientation
cases on the preset specs. Fake widgets, tiny synthetic images; nothing run inside Siril."""

from __future__ import annotations

import numpy as np
import pytest

from loomsiril import draft as D
from loomsiril import io_tiff, presets, siril_bridge as sb, skyname
from loomsiril.fly_model import (ORIENTATIONS, FlyDialogModel, natural_orientation, orientation_id, orientation_label,
                                 preset_label)

from test_draft import _options
from test_fly_names import _dialog, sesame  # noqa: F401
from test_fly_imagefile_grid import _gradient
from test_siril_bridge import _asymmetric_field, fake_pipeline  # noqa: F401
from test_siril_script import _settle, _tools, blocked_discover, script, stub  # noqa: F401

LANDSCAPE, PORTRAIT = (160, 100, 3), (100, 160, 3)


def _labels(dlg) -> dict:
    return {pid: box.kw["text"] for pid, box in dlg._preset_boxes.items()}


def _open(script, monkeypatch, blocked, grid=None, **options):
    names = skyname.NameContext(open_grid=grid) if grid else None
    return _dialog(script, monkeypatch, blocked, names, None, **options)


# ---- the rule (pure) ---------------------------------------------------------------------------------------------------


def test_the_natural_orientation_is_portrait_only_for_a_taller_image():
    assert natural_orientation((100, 160, 3)) == "portrait"
    assert natural_orientation((160, 100, 3)) == "landscape"
    assert natural_orientation((100, 100, 3)) == "landscape" and natural_orientation(None) == "landscape"


def test_the_combo_values_map_to_the_engine_and_an_unknown_one_is_horizontal():
    assert ORIENTATIONS == [("landscape", "Horizontal"), ("portrait", "Vertical")]
    assert orientation_id("Vertical") == "portrait" and orientation_id("Horizontal") == "landscape"
    assert orientation_label("portrait") == "Vertical" and orientation_label("weird") == "Horizontal"
    assert orientation_id("weird") == "landscape"


def test_the_model_follows_the_open_image_and_the_image_file_when_it_is_in_use():
    m = FlyDialogModel(open_grid=LANDSCAPE)
    assert m.image_orientation() == "landscape" and m.orientation_note() == "follows the image: landscape"
    assert FlyDialogModel(open_grid=PORTRAIT, orientation="portrait").orientation_note() == "follows the image: portrait"
    assert FlyDialogModel(open_grid=PORTRAIT, orientation="landscape").orientation_note() == "the image is portrait"
    assert FlyDialogModel().orientation_note() == ""
    extract = FlyDialogModel(mode="extract", image_path=" /a.tif ", file_grids={"/a.tif": PORTRAIT}, open_grid=LANDSCAPE)
    assert extract.image_orientation() == "portrait"
    assert FlyDialogModel(mode="files", image_path="/a.tif", file_grids={"/a.tif": PORTRAIT},
                          open_grid=LANDSCAPE).image_orientation() == "landscape"


def test_the_preset_labels_show_the_frame_of_each_orientation():
    for pid in presets.PRESET_ORDER:
        assert preset_label(pid, "landscape") == presets.PRESET_LABELS[pid]
    assert preset_label("youtube_1080", "portrait") == "YouTube 1080×1920"
    assert preset_label("youtube_4k", "portrait") == "YouTube 2160×3840"
    assert preset_label("exhibition", "portrait") == "Exhibition 2160×3840 (loop)"
    assert preset_label("social_vertical", "portrait") == "Social 1080×1920"
    assert preset_label("social_square", "portrait") == "Social 1080×1080"


@pytest.mark.parametrize("source, orientation, rotates, size", [
    ((160, 100), "landscape", False, (1920, 1080)),
    ((160, 100), "portrait", True, (1080, 1920)),
    ((100, 160), "landscape", True, (1920, 1080)),       # a portrait image on a landscape preset is turned, never cut
    ((100, 160), "portrait", False, (1080, 1920)),       # on a portrait preset it stays upright
])
def test_the_four_source_by_orientation_cases(source, orientation, rotates, size):
    js = {"landscape": "horizontal", "portrait": "vertical"}[orientation]          # the JS spelling presets.py uses
    spec = presets.preset_spec("youtube_1080", js, "none")
    assert (spec["w"], spec["h"]) == size
    assert presets.needs_rotation(*source, spec["w"], spec["h"]) is rotates
    social = presets.preset_spec("social_vertical", js, "none")
    assert (social["w"], social["h"]) == (1080, 1920)     # the social one keeps its shape either way
    assert presets.needs_rotation(*source, social["w"], social["h"]) is (source[0] > source[1])


# ---- the dialog --------------------------------------------------------------------------------------------------------


def test_the_combo_exists_after_fps_before_loop_and_defaults_to_horizontal(stub, script, monkeypatch, blocked_discover):
    dlg = _open(script, monkeypatch, blocked_discover)
    assert list(dlg._vars)[list(dlg._vars).index("fps") + 1:][:2] == ["orientation", "loop"]
    assert dlg._vars["orientation"].get() == "Horizontal"
    assert dlg.values(presets)["orientation"] == "landscape"
    assert _labels(dlg) == presets.PRESET_LABELS


def test_a_portrait_image_defaults_to_vertical_with_a_hint_and_portrait_labels(stub, script, monkeypatch, blocked_discover):
    dlg = _open(script, monkeypatch, blocked_discover, PORTRAIT, presets=["youtube_1080", "exhibition", "social_vertical"])
    assert dlg._vars["orientation"].get() == "Vertical" and dlg.values(presets)["orientation"] == "portrait"
    dlg._sync()
    assert dlg._orientation_note.get() == "follows the image: portrait"
    labels = _labels(dlg)
    assert labels["youtube_1080"] == "YouTube 1080×1920" and labels["youtube_4k"] == "YouTube 2160×3840"
    assert labels["exhibition"] == "Exhibition 2160×3840 (loop)"
    assert labels["social_vertical"] == "Social 1080×1920" and labels["social_square"] == "Social 1080×1080"


def test_a_landscape_image_defaults_to_horizontal(stub, script, monkeypatch, blocked_discover):
    dlg = _open(script, monkeypatch, blocked_discover, LANDSCAPE)
    assert dlg._vars["orientation"].get() == "Horizontal"
    dlg._sync()
    assert dlg._orientation_note.get() == "follows the image: landscape"


def test_choosing_the_other_orientation_updates_the_labels_at_once_and_sticks(stub, script, monkeypatch, blocked_discover):
    dlg = _open(script, monkeypatch, blocked_discover, LANDSCAPE)
    dlg._vars["orientation"].set("Vertical")
    assert _labels(dlg)["youtube_1080"] == "YouTube 1080×1920"
    assert _labels(dlg)["social_square"] == "Social 1080×1080"
    assert dlg._orientation_note.get() == "the image is landscape"
    dlg._sync()
    assert dlg._vars["orientation"].get() == "Vertical"            # a later sync does not take the choice back
    dlg._vars["orientation"].set("Horizontal")
    assert _labels(dlg) == presets.PRESET_LABELS


def test_a_saved_orientation_is_not_remembered_and_does_not_override_the_image(stub, script, monkeypatch, blocked_discover,
                                                                                tmp_path):
    assert "orientation" not in sb.OPTION_KEYS
    sb.save_options({"orientation": "portrait", "fps": 24}, tmp_path)
    assert sb.load_options(tmp_path) == {"fps": 24}
    dlg = _open(script, monkeypatch, blocked_discover, LANDSCAPE, orientation="portrait")
    assert dlg._vars["orientation"].get() == "Horizontal"
    assert "orientation" not in {k for k in dlg._snapshot() if k in sb.OPTION_KEYS}


def test_a_new_image_file_takes_the_default_back_from_the_user(script, monkeypatch, blocked_discover, sesame, tmp_path):
    names = skyname.NameContext(open_grid=None)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None, out_dir=str(tmp_path))
    dlg._on_discovered(_tools("starnet2"))
    dlg._mode.set("extract")
    assert dlg._vars["orientation"].get() == "Horizontal"
    dlg._vars["orientation"].set("Vertical")                        # the user chose Vertical (a landscape image so far)
    wide, tall = tmp_path / "wide.tif", tmp_path / "tall.tif"
    io_tiff.write_tiff16(wide, _gradient(40, 25))
    io_tiff.write_tiff16(tall, _gradient(25, 40))
    dlg._vars["image_path"].set(str(wide))
    _settle(dlg)
    assert dlg._vars["orientation"].get() == "Vertical"            # the file is landscape like before: the choice sticks
    dlg._vars["image_path"].set(str(tall))
    _settle(dlg)
    assert dlg._vars["orientation"].get() == "Vertical" and dlg._orientation_note.get() == "follows the image: portrait"
    dlg._vars["image_path"].set(str(wide))
    _settle(dlg)                                                    # the image changed to landscape: the default returns
    assert dlg._vars["orientation"].get() == "Horizontal"
    assert dlg._orientation_note.get() == "follows the image: landscape"


def test_the_image_files_grid_is_read_off_the_tk_thread(script, monkeypatch, blocked_discover, sesame, tmp_path):
    import threading
    from loomsiril import fly_grid

    seen, real = [], fly_grid.file_grid
    monkeypatch.setattr(fly_grid, "file_grid", lambda p: seen.append(threading.current_thread()) or real(p))
    dlg = _dialog(script, monkeypatch, blocked_discover, skyname.NameContext(open_grid=None), None, out_dir=str(tmp_path))
    dlg._on_discovered(_tools("starnet2"))
    dlg._mode.set("extract")
    path = tmp_path / "t.tif"
    io_tiff.write_tiff16(path, _gradient(25, 40))
    dlg._vars["image_path"].set(str(path))
    assert seen == []
    _settle(dlg)
    assert seen and all(t is not threading.current_thread() for t in seen)
    assert dlg._vars["orientation"].get() == "Vertical"


# ---- the job and the draft ---------------------------------------------------------------------------------------------


def test_the_value_reaches_the_job(stub, script, monkeypatch, blocked_discover, fake_pipeline):
    disp, header_top, _ = _asymmetric_field()
    dlg = _open(script, monkeypatch, blocked_discover, LANDSCAPE)
    dlg._vars["orientation"].set("Vertical")
    values = dlg.values(presets)
    job = sb.job_from_dialog({**values, "starless": [disp], "stars": [disp]}, [disp], header_top, None)
    assert job.orientation == "portrait"


def test_the_draft_key_follows_the_frame_not_the_orientation_word(tmp_path):
    land, port = _options(tmp_path, orientation="landscape"), _options(tmp_path, orientation="portrait")
    assert D.draft_key(land) != D.draft_key(port)                    # YouTube: 1920x1080 against 1080x1920
    for social in ("social_vertical", "social_square"):
        a = _options(tmp_path, orientation="landscape", presets=[social])
        b = _options(tmp_path, orientation="portrait", presets=[social])
        assert D.draft_key(a) == D.draft_key(b)                      # the frame is the same: the draft is kept
