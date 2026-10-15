"""The import glue of FrameSelector.js 1259-1290 and 2034-2290, on Siril's side of the card.

Fake card and destination folders live under ``tmp_path``; the frames are FITS written by ``loomsiril.io_fits``.
Nothing here needs Siril, PixInsight, node or a display: the master-flat engine and run function are fakes.

Covered: the manifest written and verified; a copy corrupted after writing is deleted and reported; cancel after
the first file; destinations inside the card, onto it by symlink, and a ``Light`` folder that is a link onto it are
unsafe; the outcome texts; the import log written to a temp folder; the master-flat notes in the JavaScript's order,
card darks preferred when found and the box ticked; the import summary and log carry no XISF wording.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril import asiair_import as AI
from loomsiril import io_fits


def write_frame(path: Path, header: dict[str, Any], shape: tuple[int, int] = (4, 6)) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    plane = np.arange(shape[0] * shape[1], dtype=np.float32).reshape(shape)
    io_fits.write_fits(path, [plane], header)
    return str(path)


def light_header(filt: str = "Ha") -> dict[str, Any]:
    return {"IMAGETYP": "Light Frame", "FILTER": filt, "EXPTIME": 30.0,
            "DATE-OBS": "2026-10-01T22:00:00", "XBINNING": 1, "GAIN": 100, "CCD-TEMP": -10.0}


def flat_header(filt: str = "Ha") -> dict[str, Any]:
    return {"IMAGETYP": "Flat Field", "FILTER": filt, "EXPTIME": 2.0,
            "DATE-OBS": "2026-10-01T21:00:00", "XBINNING": 1, "GAIN": 100, "CCD-TEMP": -10.0}


def dark_header() -> dict[str, Any]:
    return {"IMAGETYP": "Dark Frame", "EXPTIME": 2.0, "DATE-OBS": "2026-10-01T20:00:00",
            "XBINNING": 1, "GAIN": 100, "CCD-TEMP": -10.0}


@pytest.fixture
def card(tmp_path: Path) -> str:
    root = tmp_path / "ASIAIR"
    root.mkdir()
    return str(root)


@pytest.fixture
def lights(card: str) -> list[str]:
    return [write_frame(Path(card) / "Light" / f"L_{i}.fit", light_header()) for i in range(3)]


def manifest_for(dest: str, srcs: list[str], flats: list[str] = ()) -> dict[str, Any]:
    return {
        "lights": [{"src": s, "dst": f"{dest}/Light/{os.path.basename(s)}"} for s in srcs],
        "flats": [{"src": s, "dst": f"{dest}/Flat/{os.path.basename(s)}", "filter": "Ha"} for s in flats],
    }


# ---- the import log (FrameSelector.js 1259-1277) ---------------------------------------------------------------

def test_import_log_path_is_named_from_the_iso_time(tmp_path: Path) -> None:
    stamp = dt.datetime(2026, 10, 15, 11, 8, 0, 123000, tzinfo=dt.timezone.utc)
    path = AI.import_log_path(stamp, str(tmp_path))
    assert path == os.path.join(str(tmp_path), "Loom-import-logs", "import-2026-10-15T11-08-00-123Z.log")


def test_import_log_path_converts_other_zones_to_utc(tmp_path: Path) -> None:
    zone = dt.timezone(dt.timedelta(hours=2))
    stamp = dt.datetime(2026, 10, 15, 13, 8, 0, 5000, tzinfo=zone)
    assert AI.import_log_path(stamp, str(tmp_path)).endswith("import-2026-10-15T11-08-00-005Z.log")


def test_import_log_written_to_a_temp_folder(tmp_path: Path) -> None:
    temp = tmp_path / "system-temp"
    stamp = dt.datetime(2026, 10, 15, 11, 8, 0, 123000, tzinfo=dt.timezone.utc)
    path = AI.import_log_path(stamp, str(temp))
    lines = ["Loom import", "copied: 3", "flats read: 0"]
    assert AI.write_import_log(path, lines) == path
    assert Path(path).read_text(encoding="utf-8") == "Loom import\ncopied: 3\nflats read: 0\n"
    # A second write replaces the same file, as the JavaScript does.
    assert AI.write_import_log(path, ["again"]) == path
    assert Path(path).read_text(encoding="utf-8") == "again\n"


def test_import_log_failure_warns_and_returns_none(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    warnings: list[str] = []
    path = str(blocker / "import.log")  # the parent is a file, so the folder cannot be made
    assert AI.write_import_log(path, ["line"], warn=warnings.append) is None
    assert len(warnings) == 1 and warnings[0].startswith("could not write the import log:")


# ---- the copy and its check (FrameSelector.js 2034-2104) -------------------------------------------------------

def test_manifest_is_written_and_every_copy_verified(card: str, lights: list[str], tmp_path: Path) -> None:
    dest = str(tmp_path / "out")
    manifest = manifest_for(dest, lights)
    result = AI.write_manifest(manifest, card_root=card)
    assert result == {"written": 3, "failed": [], "cancelled": False}
    for src, item in zip(lights, manifest["lights"]):
        assert Path(item["dst"]).read_bytes() == Path(src).read_bytes()
        assert AI.verify_imported(src, item["dst"]) == {"ok": True, "reason": ""}


def test_a_corrupted_copy_is_deleted_and_reported(card: str, lights: list[str], tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    dest = str(tmp_path / "out")
    manifest = manifest_for(dest, lights)
    bad_dst = manifest["lights"][1]["dst"]
    real_convert = AI.convert_one

    def convert_then_damage(src: str, dst: str) -> dict[str, Any]:
        made = real_convert(src, dst)
        if dst == bad_dst:
            data = bytearray(Path(dst).read_bytes())
            data[-1] ^= 0xFF
            Path(dst).write_bytes(bytes(data))
        return made

    monkeypatch.setattr(AI, "convert_one", convert_then_damage)
    result = AI.write_manifest(manifest, card_root=card)
    assert result["written"] == 2
    assert result["cancelled"] is False
    assert result["failed"] == [{"src": lights[1], "reason": "the copy is not the card's file (SHA-1 differs)"}]
    assert not os.path.exists(bad_dst)
    assert os.path.exists(manifest["lights"][0]["dst"]) and os.path.exists(manifest["lights"][2]["dst"])


def test_a_copy_that_lost_its_geometry_or_a_keyword_is_deleted(tmp_path: Path) -> None:
    src = write_frame(tmp_path / "card" / "L.fit", light_header())
    wrong_shape = write_frame(tmp_path / "out" / "wrong-shape.fit", light_header(), shape=(5, 6))
    assert AI.verify_imported(src, wrong_shape) == {"ok": False, "reason": "geometry changed"}
    assert not os.path.exists(wrong_shape)

    no_date = dict(light_header())
    del no_date["DATE-OBS"]
    lost = write_frame(tmp_path / "out" / "lost-keyword.fit", no_date)
    assert AI.verify_imported(src, lost) == {"ok": False, "reason": "DATE-OBS did not survive"}
    assert not os.path.exists(lost)


def test_a_missing_destination_is_nothing_written(tmp_path: Path) -> None:
    src = write_frame(tmp_path / "card" / "L.fit", light_header())
    assert AI.verify_imported(src, str(tmp_path / "out" / "none.fit")) == {"ok": False, "reason": "nothing was written"}


def test_convert_one_refuses_an_existing_name_and_leaves_it(tmp_path: Path) -> None:
    src = write_frame(tmp_path / "card" / "L.fit", light_header())
    dst = tmp_path / "out" / "L.fit"
    dst.parent.mkdir()
    dst.write_bytes(b"earlier copy")
    assert AI.convert_one(src, str(dst)) == {"ok": False,
                                              "reason": "a file of that name is already there; not overwritten"}
    assert dst.read_bytes() == b"earlier copy"


def test_convert_one_reports_a_gone_source(tmp_path: Path) -> None:
    assert AI.convert_one(str(tmp_path / "gone.fit"), str(tmp_path / "out.fit")) == {"ok": False,
                                                                                     "reason": "source is gone"}
    assert not (tmp_path / "out.fit").exists()


def test_cancel_after_the_first_file_writes_no_more(card: str, lights: list[str], tmp_path: Path) -> None:
    dest = str(tmp_path / "out")
    manifest = manifest_for(dest, lights)
    seen: list[tuple[int, int]] = []

    def stop_after_first(done: int, total: int) -> bool:
        seen.append((done, total))
        return False

    result = AI.write_manifest(manifest, on_progress=stop_after_first, card_root=card)
    assert result == {"written": 1, "failed": [], "cancelled": True}
    assert seen == [(1, 3)]
    assert os.path.exists(manifest["lights"][0]["dst"])
    assert not os.path.exists(manifest["lights"][1]["dst"])
    assert not os.path.exists(manifest["lights"][2]["dst"])


def test_a_destination_on_the_card_is_refused_not_written(card: str, lights: list[str]) -> None:
    manifest = manifest_for(os.path.join(card, "export"), lights)
    result = AI.write_manifest(manifest, card_root=card)
    assert result["written"] == 0
    assert [f["reason"] for f in result["failed"]] == ["the destination is on the ASIAIR card; not written"] * 3
    assert not os.path.exists(os.path.join(card, "export"))


# ---- where the output may go (FrameSelector.js 2259-2290) ------------------------------------------------------

def test_destination_inside_the_card_is_unsafe(card: str) -> None:
    assert AI.outputs_are_safe(os.path.join(card, "export"), card) is False
    assert AI.outputs_are_safe(card, card) is False


def test_destination_that_is_a_symlink_into_the_card_is_unsafe(card: str, tmp_path: Path) -> None:
    link = tmp_path / "link"
    os.symlink(os.path.join(card, "Light"), link)
    assert AI.outputs_are_safe(str(link), card) is False


def test_light_folder_that_is_a_link_onto_the_card_is_unsafe(card: str, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    os.symlink(card, dest / "Light")
    assert AI.outputs_are_safe(str(dest), card) is False


def test_flat_folder_that_is_a_link_onto_the_card_is_unsafe(card: str, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    os.symlink(card, dest / "Flat")
    assert AI.outputs_are_safe(str(dest), card) is False


def test_a_sibling_sharing_the_card_name_prefix_is_safe(card: str, tmp_path: Path) -> None:
    assert AI.outputs_are_safe(card + "-backup", card) is True
    assert AI.outputs_are_safe(str(tmp_path / "out"), card) is True


def test_no_destination_or_no_card_is_unsafe(card: str) -> None:
    assert AI.outputs_are_safe(None, card) is False
    assert AI.outputs_are_safe(card + "-backup", None) is False


# ---- the outcome and the summary (FrameSelector.js 2233-2247; AsiairNames.js 562-579) ---------------------------

def test_import_outcome_success() -> None:
    assert AI.import_outcome({"written": 3, "failed": [], "cancelled": False}) == {
        "text": "Imported 3 frame(s).", "icon": "information"}


def test_import_outcome_with_a_failure_names_the_first() -> None:
    result = {"written": 2, "cancelled": False,
              "failed": [{"src": "/card/Light/L_9.fit", "reason": "geometry changed"}]}
    assert AI.import_outcome(result) == {
        "text": "Imported 2 frame(s); 1 failed:\n\n/card/Light/L_9.fit\ngeometry changed", "icon": "warning"}


def test_import_outcome_cancelled() -> None:
    assert AI.import_outcome({"written": 1, "failed": [], "cancelled": True}) == {
        "text": "Import cancelled after 1 frame(s) were imported. The rest were not written.", "icon": "warning"}


def test_import_outcome_cancelled_with_a_failure() -> None:
    result = {"written": 0, "cancelled": True,
              "failed": [{"src": "/card/Flat/F.fit", "reason": "nothing was written"}]}
    assert AI.import_outcome(result) == {
        "text": ("Import cancelled after 0 frame(s) were imported; 1 failed:\n\n/card/Flat/F.fit\nnothing was written"
                 "\n\nThe rest were not written."),
        "icon": "warning"}


def test_import_summary_says_fits_copies_and_never_xisf(tmp_path: Path) -> None:
    manifest = {"lights": [{"src": "a", "dst": "b"}] * 2, "flats": [{"src": "c", "dst": "d"}]}
    text = AI.import_summary(manifest, str(tmp_path / "Light"), None)
    assert "Written as FITS copies:" in text
    assert "XISF" not in text
    assert text.splitlines()[0].startswith("Using ")
    masters = {"card": True, "count": 2, "exposures": [2.0]}
    with_masters = AI.import_summary(manifest, str(tmp_path / "out"), masters)
    assert "XISF" not in with_masters
    assert "Written as FITS copies:" in with_masters


# ---- card darks (FrameSelector.js 2140-2163) -------------------------------------------------------------------

def test_card_darks_state_helpers(card: str, tmp_path: Path) -> None:
    state: dict[str, Any] = {"cardRoot": card, "cardDarks": [], "cardDarkMatch": None,
                             "onlyMasterFlats": False, "darksFolder": ""}
    assert AI.card_darks_found(state) is False
    assert AI.card_darks_used(state) is False
    darks = [write_frame(Path(card) / "Dark" / f"D_{i}.fit", dark_header()) for i in range(2)]
    flats = [write_frame(Path(card) / "Flat" / f"F_{i}.fit", flat_header()) for i in range(2)]
    AI.read_card_darks(state, [{"path": p} for p in darks], [{"path": p} for p in flats])
    assert state["cardDarkMatch"]["found"] is True
    assert AI.card_darks_found(state) is True
    assert AI.card_darks_used(state) is False  # the box is not ticked
    state["onlyMasterFlats"] = True
    assert AI.card_darks_used(state) is True
    assert AI.dark_source_of(state) == {"card": True, "count": 2, "exposures": [2.0]}
    state["cardRoot"] = None
    assert AI.card_darks_found(state) is False
    assert AI.dark_source_of(state) == {"darksFolder": ""}


# ---- the master flats (FrameSelector.js 2171-2226) -------------------------------------------------------------

class FakeEngine:
    pass


def master_flat_fixture(card: str, tmp_path: Path, ticked: bool) -> tuple[dict[str, Any], dict[str, Any], str,
                                                                        dict[str, Any], str]:
    dest = str(tmp_path / "out")
    flat_srcs = [write_frame(Path(card) / "Flat" / f"F_{i}.fit", flat_header()) for i in range(2)]
    dark_srcs = [write_frame(Path(card) / "Dark" / f"D_{i}.fit", dark_header()) for i in range(2)]
    manifest = manifest_for(dest, [], flat_srcs)
    written = AI.write_manifest(manifest, card_root=card)
    assert written["written"] == 2
    darks_folder = tmp_path / "darks-folder"
    darks_folder.mkdir()
    write_frame(darks_folder / "dark_folder_1.fit", dark_header())
    state: dict[str, Any] = {"cardRoot": card, "cardDarks": [], "cardDarkMatch": None,
                             "onlyMasterFlats": ticked, "darksFolder": str(darks_folder)}
    AI.read_card_darks(state, [{"path": p} for p in dark_srcs], [{"path": p} for p in flat_srcs])
    return manifest, written, dest, state, str(darks_folder)


def test_master_flats_notes_in_order_with_card_darks_preferred(card: str, tmp_path: Path) -> None:
    manifest, written, dest, state, _folder = master_flat_fixture(card, tmp_path, ticked=True)
    notes: list[str] = []
    calls: list[Any] = []

    def run(jobs: list[dict[str, Any]], engine: Any, opts: dict[str, Any]) -> str:
        calls.append((jobs, engine, opts))
        opts["onProgress"](1, 2, "Ha")
        opts["onProgress"](2, 2, "")
        assert opts["shouldStop"]() is False
        return "run-result"

    engine = FakeEngine()
    progress: list[tuple[int, int, str]] = []
    result = AI.make_master_flats(manifest, written, dest, state, engine=engine, run=run, note=notes.append,
                                  on_progress=lambda d, t, s: progress.append((d, t, s)))
    assert result == "run-result"
    assert notes[0] == "flats read: 2"
    assert notes[1].startswith("  F_0.fit  filter=Ha")
    assert notes[2].startswith("  F_1.fit  filter=Ha")
    assert notes[3].startswith("darks source: the ") and "dark(s) on the ASIAIR card" in notes[3]
    assert notes[3].endswith("(read-only; subfolders not searched)")
    assert notes[4].startswith("darks found: ") and notes[4].endswith(" on the card")
    assert not any(n.startswith("darks folder:") for n in notes)
    plan_at = notes.index("plan:")
    assert plan_at > 4
    jobs, got_engine, opts = calls[0]
    assert got_engine is engine
    assert opts["destFlatDir"] == os.path.join(dest, "Flat")
    assert opts["cardRoot"] == card
    assert jobs and all(j["skip"] is None for j in jobs)
    dark_paths = []
    for job in jobs:
        for part in job["parts"]:
            dark = part["dark"]
            dark_paths += dark["paths"] if dark.get("kind") == "raw" else [dark["path"]]
    assert dark_paths and all(p.startswith(card) for p in dark_paths)
    assert progress == [(1, 2, "Ha (Cancel keeps the raw flats not yet done)"), (2, 2, "")]


def test_master_flats_darks_folder_when_the_box_is_not_ticked(card: str, tmp_path: Path) -> None:
    manifest, written, dest, state, folder = master_flat_fixture(card, tmp_path, ticked=False)
    notes: list[str] = []
    AI.make_master_flats(manifest, written, dest, state, engine=None,
                         run=lambda jobs, engine, opts: None, note=notes.append)
    assert notes[0] == "flats read: 2"
    assert f"darks folder: {folder} (subfolders to 4 levels, names containing \"dark\")" in notes
    assert not any("on the ASIAIR card" in n for n in notes)


def test_master_flats_skip_a_filter_with_a_failed_copy(card: str, tmp_path: Path) -> None:
    manifest, written, dest, state, _folder = master_flat_fixture(card, tmp_path, ticked=True)
    lost = manifest["flats"][0]["src"]
    written = {"written": 1, "failed": [{"src": lost, "reason": "x"}], "cancelled": False}
    seen: list[list[dict[str, Any]]] = []
    AI.make_master_flats(manifest, written, dest, state, engine=None,
                         run=lambda jobs, engine, opts: seen.append(jobs), note=lambda _l: None)
    # Both flats are filter Ha, so the one filter with a failed copy is skipped as a whole.
    assert seen and len(seen[0]) > 0
    assert all(j["skip"] is not None for j in seen[0])


def test_master_flat_work_folder_is_removed(card: str, tmp_path: Path) -> None:
    manifest, written, dest, state, _folder = master_flat_fixture(card, tmp_path, ticked=True)
    cache = tmp_path / "cache"
    (cache / "master-flat-work").mkdir(parents=True)
    (cache / "master-flat-work" / "scratch.fit").write_bytes(b"x")
    AI.make_master_flats(manifest, written, dest, state, engine=None,
                         run=lambda jobs, engine, opts: None, note=lambda _l: None, cache_dir=str(cache))
    assert not (cache / "master-flat-work").exists()


def test_no_xisf_wording_in_the_master_flat_notes_or_the_log(card: str, tmp_path: Path) -> None:
    manifest, written, dest, state, _folder = master_flat_fixture(card, tmp_path, ticked=True)
    notes: list[str] = []
    AI.make_master_flats(manifest, written, dest, state, engine=None,
                         run=lambda jobs, engine, opts: None, note=notes.append)
    stamp = dt.datetime(2026, 10, 15, 11, 8, 0, tzinfo=dt.timezone.utc)
    log = AI.import_log_path(stamp, str(tmp_path / "temp"))
    AI.write_import_log(log, notes + [AI.import_summary(manifest, dest, None)])
    text = Path(log).read_text(encoding="utf-8")
    assert "XISF" not in text
    assert ".xisf" not in text
    assert "Written as FITS copies:" in text
