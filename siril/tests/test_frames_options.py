"""Remembered Frame Selector options: loomsiril.frames_options, on a JSON file in a folder under pytest's tmp_path.

These tests need neither Siril, PixInsight, node nor a display.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from loomsiril import frames
from loomsiril import frames_options as O
from loomsiril import master_flat as mf


def _store(folder: Path) -> O.OptionsStore:
    return O.OptionsStore(folder)


def _file(folder: Path) -> Path:
    return folder / O.FILE_NAME


def _full_options(folder: Path) -> O.FrameSelectorOptions:
    return O.FrameSelectorOptions(
        preset="strict",
        k=1.75,
        gating={"psfSNR": True, "fwhm": False, "eccentricity": True, "stars": False},
        limits={"fwhm": {"hi": 3.5}, "psfSNR": {"lo": 12.0, "hi": 80.0}},
        source_folder="/data/night/lights",
        destination_folder="/data/night/kept",
        darks_folder="/data/darks",
        only_master_flats=True,
    )


def test_round_trip_keeps_every_field(tmp_path: Path) -> None:
    options = _full_options(tmp_path)
    O.save(_store(tmp_path), options)

    loaded = O.load(_store(tmp_path))

    assert loaded == options
    assert loaded.preset == "strict"
    assert loaded.k == 1.75
    assert loaded.gating == options.gating
    assert loaded.limits == options.limits
    assert loaded.source_folder == "/data/night/lights"
    assert loaded.destination_folder == "/data/night/kept"
    assert loaded.darks_folder == "/data/darks"
    assert loaded.only_master_flats is True


def test_round_trip_of_the_unedited_k_and_empty_folders(tmp_path: Path) -> None:
    O.save(_store(tmp_path), O.FrameSelectorOptions(preset="lenient"))

    loaded = O.load(_store(tmp_path))

    assert loaded.preset == "lenient"
    assert loaded.k is None
    assert loaded.limits == {}
    assert loaded.darks_folder == ""
    assert loaded.only_master_flats is False


def test_defaults_when_there_is_no_file(tmp_path: Path) -> None:
    folder = tmp_path / "config"

    loaded = O.load(_store(folder))

    assert loaded == O.FrameSelectorOptions()
    assert loaded.preset == frames.DEFAULT_PRESET
    assert loaded.gating == frames.DEFAULT_GATING
    assert not _file(folder).exists()


def test_corrupt_json_reads_as_defaults_and_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _file(tmp_path).write_text("{ this is not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=O.__name__):
        loaded = O.load(_store(tmp_path))

    assert loaded == O.FrameSelectorOptions()
    assert any("could not be read" in r.getMessage() for r in caplog.records)


def test_file_that_is_not_an_object_reads_as_defaults(tmp_path: Path) -> None:
    _file(tmp_path).write_text("[1, 2, 3]", encoding="utf-8")

    assert O.load(_store(tmp_path)) == O.FrameSelectorOptions()


def test_one_bad_field_is_dropped_and_the_rest_kept(tmp_path: Path) -> None:
    raw: dict[str, Any] = {
        "Loom/importDarksFolder": "/darks",
        "Loom/importMasterFlatsOnly": True,
        "FrameSelector": {
            "preset": "bogus",
            "k": "2.2",
            "gating": {"fwhm": "yes", "psfSNR": True, "unknownMetric": True, "stars": False},
            "limits": {
                "fwhm": {"hi": 2.5},
                "nope": {"lo": 1.0},
                "stars": {"lo": "many", "hi": 900.0},
                "eccentricity": "0.6",
            },
            "sourceFolder": 5,
            "destinationFolder": "/kept",
        },
    }
    _file(tmp_path).write_text(json.dumps(raw), encoding="utf-8")

    loaded = O.load(_store(tmp_path))

    assert loaded.preset == frames.DEFAULT_PRESET
    assert loaded.k is None
    assert loaded.gating == {"psfSNR": True, "fwhm": True, "eccentricity": True, "stars": False}
    assert loaded.limits == {"fwhm": {"hi": 2.5}, "stars": {"hi": 900.0}}
    assert loaded.source_folder == ""
    assert loaded.destination_folder == "/kept"
    assert loaded.darks_folder == "/darks"
    assert loaded.only_master_flats is True


def test_non_finite_and_non_positive_numbers_are_dropped(tmp_path: Path) -> None:
    raw = {"FrameSelector": {"k": -1.0, "limits": {"fwhm": {"hi": True, "lo": 1e400}}}}
    _file(tmp_path).write_text(json.dumps(raw), encoding="utf-8")

    loaded = O.load(_store(tmp_path))

    assert loaded.k is None
    assert loaded.limits == {}


def test_master_flat_settings_go_through_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)

    mf.save_settings(store, "/darks/folder", True)

    raw = json.loads(_file(tmp_path).read_text(encoding="utf-8"))
    assert raw["Loom/importDarksFolder"] == "/darks/folder"
    assert raw["Loom/importMasterFlatsOnly"] is True
    assert mf.load_settings(_store(tmp_path)) == {"darksFolder": "/darks/folder", "onlyMasterFlats": True}


def test_master_flat_save_of_falsy_values_stores_empty_and_false(tmp_path: Path) -> None:
    mf.save_settings(_store(tmp_path), None, None)

    assert mf.load_settings(_store(tmp_path)) == {"darksFolder": "", "onlyMasterFlats": False}


def test_store_read_returns_none_for_the_wrong_type(tmp_path: Path) -> None:
    _file(tmp_path).write_text(json.dumps({"Loom/importMasterFlatsOnly": "yes",
                                           "Loom/importDarksFolder": True}), encoding="utf-8")
    store = _store(tmp_path)

    assert store.read(mf.SETTINGS_ONLY, O.DATA_TYPE_BOOLEAN) is None
    assert store.read(mf.SETTINGS_DARKS, O.DATA_TYPE_STRING) is None
    assert mf.load_settings(store) == {"darksFolder": "", "onlyMasterFlats": False}


def test_settings_for_new_channel_applies_the_preset_and_the_criteria(tmp_path: Path) -> None:
    options = O.FrameSelectorOptions(preset="lenient", gating={"psfSNR": True, "fwhm": False,
                                                              "eccentricity": True, "stars": True},
                                     limits={"fwhm": {"hi": 2.0}})

    s = O.settings_for_new_channel(options)

    assert s["preset"] == "lenient"
    assert s["k"] == frames.PRESETS["lenient"]
    assert s["kEdited"] is False
    assert s["gating"] == {"psfSNR": True, "fwhm": False, "eccentricity": True, "stars": True}
    assert s["limits"] == {"fwhm": {"hi": 2.0}}
    assert s["weights"] == {m: frames.DEFAULT_WEIGHTS[m] for m in frames.METRICS}
    assert s["enabled"] is True


def test_settings_for_new_channel_keeps_a_remembered_edited_k() -> None:
    s = O.settings_for_new_channel(O.FrameSelectorOptions(preset="strict", k=2.9))

    assert s["preset"] == "strict"
    assert s["k"] == 2.9
    assert s["kEdited"] is True


def test_settings_for_new_channel_gives_independent_objects_per_channel() -> None:
    options = O.FrameSelectorOptions(gating={"psfSNR": True, "fwhm": True, "eccentricity": True, "stars": True},
                                     limits={"fwhm": {"hi": 2.0}})

    first = O.settings_for_new_channel(options)
    second = O.settings_for_new_channel(options)

    first["gating"]["fwhm"] = False
    first["limits"]["fwhm"]["hi"] = 9.0
    first["limits"]["stars"] = {"lo": 1.0}
    first["weights"]["fwhm"] = 0.9

    assert second["gating"]["fwhm"] is True
    assert second["limits"] == {"fwhm": {"hi": 2.0}}
    assert second["weights"]["fwhm"] == frames.DEFAULT_WEIGHTS["fwhm"]
    assert options.gating["fwhm"] is True
    assert options.limits == {"fwhm": {"hi": 2.0}}
    assert first["gating"] is not second["gating"]
    assert first["limits"]["fwhm"] is not second["limits"]["fwhm"]


def test_atomic_write_leaves_only_the_options_file(tmp_path: Path) -> None:
    O.save(_store(tmp_path), _full_options(tmp_path))
    O.save(_store(tmp_path), O.FrameSelectorOptions(preset="strict"))

    assert sorted(os.listdir(tmp_path)) == [O.FILE_NAME]


def test_failed_replace_keeps_the_old_file_and_leaves_no_temp_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    O.save(_store(tmp_path), O.FrameSelectorOptions(preset="lenient"))
    before = _file(tmp_path).read_text(encoding="utf-8")

    def refuse(src: Any, dst: Any) -> None:
        raise OSError("replace refused")

    monkeypatch.setattr(O.os, "replace", refuse)
    with caplog.at_level(logging.WARNING, logger=O.__name__):
        O.save(_store(tmp_path), O.FrameSelectorOptions(preset="strict"))

    assert _file(tmp_path).read_text(encoding="utf-8") == before
    assert sorted(os.listdir(tmp_path)) == [O.FILE_NAME]
    assert any("not saved" in r.getMessage() for r in caplog.records)


def test_unwritable_folder_warns_and_does_not_raise(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file, so the folder cannot be made", encoding="utf-8")
    store = _store(blocker)

    with caplog.at_level(logging.WARNING, logger=O.__name__):
        O.save(store, _full_options(tmp_path))
        store.write(mf.SETTINGS_DARKS, O.DATA_TYPE_STRING, "/darks")

    assert any("not saved" in r.getMessage() for r in caplog.records)
    assert O.load(_store(blocker)) == O.FrameSelectorOptions()


def test_remember_from_state_takes_k_only_when_edited_by_hand() -> None:
    base = O.FrameSelectorOptions(source_folder="/in", destination_folder="/out", darks_folder="/d",
                                  only_master_flats=True)
    channel = O.settings_for_new_channel(O.FrameSelectorOptions(preset="balanced"))
    channel["preset"] = "strict"
    channel["gating"]["stars"] = False
    channel["limits"] = {"fwhm": {"hi": 4.0}}

    kept = O.remember_from_state(base, channel)
    assert kept.preset == "strict"
    assert kept.k is None
    assert kept.gating["stars"] is False
    assert kept.limits == {"fwhm": {"hi": 4.0}}
    assert (kept.source_folder, kept.destination_folder) == ("/in", "/out")
    assert (kept.darks_folder, kept.only_master_flats) == ("/d", True)

    channel["kEdited"] = True
    channel["k"] = 1.5
    edited = O.remember_from_state(base, channel, source_folder="/new", only_master_flats=False)
    assert edited.k == 1.5
    assert edited.source_folder == "/new"
    assert edited.destination_folder == "/out"
    assert edited.only_master_flats is False
    assert base.source_folder == "/in"


def test_remember_from_state_then_save_and_load_agree(tmp_path: Path) -> None:
    channel = O.settings_for_new_channel(O.FrameSelectorOptions(preset="lenient", k=2.1))
    channel["limits"] = {"psfSNR": {"lo": 5.0}}
    remembered = O.remember_from_state(O.FrameSelectorOptions(), channel,
                                       source_folder="/lights", darks_folder="/darks",
                                       only_master_flats=True)

    O.save(_store(tmp_path), remembered)

    assert O.load(_store(tmp_path)) == remembered
