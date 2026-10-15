"""The Frame Selector's Run: delete in place, copy, ASIAIR import and master flats (Tk under a hidden root, skipped
without a display). Every refusal has a test. The deleting, copying and writing steps are fakes; nothing real is
removed or written outside ``tmp_path``."""

from __future__ import annotations

import os
from typing import Any

import pytest

from loomsiril import frames as F
from loomsiril import frames_options as FO
from loomsiril import frames_state as FS


def _scan(n: int = 12, filters: tuple[str, ...] = ("L", "R"), tmp: str = "/data/night") -> dict[str, Any]:
    channels: dict[str, Any] = {}
    for f in filters:
        entries, metrics = [], {}
        for i in range(n):
            path = "%s/%s_%03d.fit" % (tmp, f, i)
            entries.append({"path": path, "identity": {"digest": "d%s%d" % (f, i), "size": 10, "mtime": 1},
                            "time": None})
            blur = i == 3
            metrics[path] = {"fwhm": 9.0 if blur else 2.0 + 0.01 * i, "eccentricity": 0.4, "noise": 1.0,
                             "stars": 300 + i, "psfSNR": 20.0 + i, "background": 0.1 + 0.001 * i,
                             "snrWeight": None, "altitude": None, "psfFlux": None}
        channels[f] = {"entries": entries, "metrics": metrics, "problems": []}
    return {"channels": channels, "unstable": [], "skipped": [], "cancelled": False}


def _state(folder: str = "/data/night", **kw: Any) -> dict[str, Any]:
    return FS.state_from_scan(FS.empty_state(folder), _scan(tmp=folder, **kw))


@pytest.fixture
def tk_root():
    try:
        import tkinter
    except (ImportError, OSError) as exc:
        pytest.skip(f"tkinter cannot be imported here: {exc}")
    from loomsiril.tk_theme import close_root, themed_root

    try:
        root = themed_root()
    except tkinter.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    yield root
    close_root(root)


class Window:
    """The progress window's stand-in: records what is shown, never cancelled unless told."""

    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.shown: list[tuple[Any, ...]] = []

        class Top:
            def update(self) -> None:
                pass

        self.top = Top()

    def report(self, *a: Any) -> bool:
        self.shown.append(a)
        return not self.cancelled

    def display(self, *a: Any) -> bool:
        self.shown.append(a)
        return True

    def announce(self, text: str) -> None:
        self.shown.append((text,))


class Recorder:
    """The boxes, the questions and the fakes of the actions, in one place."""

    def __init__(self) -> None:
        self.told: list[tuple[str, str]] = []
        self.asked: list[str] = []
        self.answer = True
        self.calls: list[tuple[str, Any]] = []
        self.cancel_window = False
        self.pump: Any = None               # runs Tk events inside a box or a progress window, as the real ones do

    def tell(self, text: str, icon: str = "info", **kw: Any) -> None:
        self.told.append((text, icon))

    def ask(self, text: str, **kw: Any) -> bool:
        self.asked.append(text)
        if self.pump is not None:
            self.pump()
        return self.answer

    def with_progress(self, master: Any, title: Any, work: Any, **kw: Any) -> Any:
        self.calls.append(("progress", title))
        if self.pump is not None:
            self.pump()
        return work(Window(self.cancel_window))


@pytest.fixture
def rec(monkeypatch, tmp_path):
    from loomsiril import frames_dialog_actions as DA

    r = Recorder()
    monkeypatch.setattr(DA, "tell", r.tell)
    monkeypatch.setattr(DA, "ask", r.ask)
    monkeypatch.setattr(DA, "with_progress", r.with_progress)
    monkeypatch.setattr(DA.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))

    def execute(manifest: Any, on_progress: Any = None, **kw: Any) -> dict[str, Any]:
        r.calls.append(("execute", manifest))
        return {"deleted": len(manifest["entries"]), "skipped": 0, "failed": 0, "stopped": False,
                "logPath": "/log/x.log"}

    def export_approved(approved: Any, destination: Any, siril: Any = None, home: Any = None) -> dict[str, Any]:
        r.calls.append(("export", (list(approved), destination)))
        return {"written": len(approved), "failed": 0, "emptied": 0, "refused": None, "outcomes": {}}

    monkeypatch.setattr(DA.FDel, "execute", execute)
    monkeypatch.setattr(DA.FC, "export_approved", export_approved)
    return r


@pytest.fixture
def make(tk_root, tmp_path):
    from loomsiril.frames_dialog import Dialog
    from loomsiril.frames_preview_cache import PreviewCache

    made: list[Any] = []

    def build(state: Any = None, **kw: Any) -> Any:
        d = Dialog(tk_root, state if state is not None else _state(), cache=PreviewCache(str(tmp_path / "cache")), **kw)
        made.append(d)
        return d

    yield build
    for d in made:
        d.close()


def _names(rec: Recorder) -> list[str]:
    return [c[0] for c in rec.calls if c[0] != "progress"]


# ---- pure

def test_the_module_imports_without_the_dialog_or_a_window() -> None:
    import subprocess
    import sys

    code = ("import sys, loomsiril.frames_dialog_actions as m; "
            "assert 'loomsiril.frames_dialog' not in sys.modules; assert 'tkinter' not in sys.modules; "
            "print(m.DialogActions.__name__)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(__file__)), env={**os.environ, "PYTHONPATH": os.getcwd()})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "DialogActions"


def test_the_delete_question_names_the_count_per_channel_and_that_it_is_final() -> None:
    from loomsiril.frames_dialog_actions import delete_question

    q = delete_question(3, ["L: 2", "R: 1"])
    assert q == "Delete 3 frame(s)?\n\nL: 2\nR: 1\n\nThis cannot be undone."


def test_the_delete_outcome_says_nothing_was_deleted_when_there_is_no_log() -> None:
    from loomsiril.frames_dialog_actions import delete_outcome

    text, icon = delete_outcome({"deleted": 0, "skipped": 0, "failed": 0, "stopped": False, "logPath": None})
    assert text.startswith("Nothing was deleted") and icon == "error"
    text, icon = delete_outcome({"deleted": 2, "skipped": 1, "failed": 0, "stopped": False, "logPath": "/l.log"})
    assert "2 frame(s) deleted" in text and "1 skipped" in text and "Log: /l.log" in text and icon == "warning"


# ---- delete in place

def test_run_deletes_after_asking_with_the_count_per_channel(make, rec) -> None:
    d = make()
    d.commit()
    assert len(rec.asked) == 1
    assert rec.asked[0] == "Delete 2 frame(s)?\n\nL: 1\nR: 1\n\nThis cannot be undone."
    kind, manifest = [c for c in rec.calls if c[0] == "execute"][0]
    assert sorted(e["path"] for e in manifest["entries"]) == ["/data/night/L_003.fit", "/data/night/R_003.fit"]
    assert d.state["manifest"] is manifest and d.state["locked"] is True
    assert d.state["phase"] == F.PHASE["DONE"]
    assert "2 frame(s) deleted" in rec.told[-1][0] and rec.told[-1][1] == "info"


def test_saying_no_to_the_deletion_does_nothing(make, rec) -> None:
    d = make()
    rec.answer = False
    assert d.commit() is None
    assert "execute" not in _names(rec)
    assert d.state["locked"] is False and d.state["phase"] == F.PHASE["REVIEW"] and d.state["manifest"] is None
    assert rec.told == []


def test_a_channel_switched_off_is_not_in_the_question_or_the_manifest(make, rec) -> None:
    d = make()
    d.state["channels"]["R"]["settings"]["enabled"] = False
    d.commit()
    assert rec.asked[0] == "Delete 1 frame(s)?\n\nL: 1\n\nThis cannot be undone."
    manifest = [c for c in rec.calls if c[0] == "execute"][0][1]
    assert all(e["path"].startswith("/data/night/L_") for e in manifest["entries"])


def test_nothing_to_delete_asks_nothing(make, rec) -> None:
    d = make()
    for ch in d.state["channels"].values():
        ch["settings"]["enabled"] = False
    assert d.commit() is None
    assert rec.asked == [] and "execute" not in _names(rec)


def test_a_locked_review_does_not_run(make, rec) -> None:
    d = make()
    d.state["locked"] = True
    assert d.commit() is None
    assert rec.asked == [] and _names(rec) == []
    d.state["locked"] = False
    d.state["phase"] = F.PHASE["DONE"]
    assert d.commit() is None and _names(rec) == []


def test_a_deletion_whose_log_could_not_be_written_is_stopped_and_says_so(make, rec, monkeypatch) -> None:
    from loomsiril import frames_dialog_actions as DA

    monkeypatch.setattr(DA.FDel, "execute", lambda manifest, on_progress=None, **kw: {
        "deleted": 0, "skipped": 0, "failed": 0, "stopped": False, "logPath": None})
    d = make()
    d.commit()
    assert d.state["phase"] == F.PHASE["STOPPED"]
    assert rec.told[-1][0].startswith("Nothing was deleted") and rec.told[-1][1] == "error"


def test_the_deletion_reports_what_it_skipped(make, rec, monkeypatch) -> None:
    from loomsiril import frames_dialog_actions as DA

    monkeypatch.setattr(DA.FDel, "execute", lambda manifest, on_progress=None, **kw: {
        "deleted": 1, "skipped": 1, "failed": 0, "stopped": False, "logPath": "/l.log"})
    make().commit()
    assert "1 skipped" in rec.told[-1][0] and rec.told[-1][1] == "warning"


def test_the_deletion_in_place_converts_nothing_and_says_nothing_more(make, rec) -> None:
    d = make()
    assert FS.destination_is_source(d.state)
    d.commit()
    assert len(rec.told) == 1


# ---- copy

def test_run_in_copy_mode_exports_the_approved_frames_and_never_deletes(make, rec, tmp_path) -> None:
    d = make()
    d.state["destination"] = str(tmp_path / "out")
    result = d.commit()
    assert "execute" not in _names(rec) and rec.asked == []
    kind, (approved, dest) = [c for c in rec.calls if c[0] == "export"][0]
    assert dest == str(tmp_path / "out")
    assert len(approved) == 22 and "/data/night/L_003.fit" not in approved and "/data/night/R_003.fit" not in approved
    assert result["written"] == 22 and d.state["locked"] is True
    assert rec.told[-1][0] == "22 frame(s) written to\n" + str(tmp_path / "out")


def test_a_channel_switched_off_copies_nothing(make, rec, tmp_path) -> None:
    d = make()
    d.state["destination"] = str(tmp_path / "out")
    d.state["channels"]["R"]["settings"]["enabled"] = False
    d.commit()
    approved = [c for c in rec.calls if c[0] == "export"][0][1][0]
    assert approved and all("/R_" not in p for p in approved)
    d2_state = _state()
    for ch in d2_state["channels"].values():
        ch["settings"]["enabled"] = False
    d2_state["destination"] = str(tmp_path / "out")
    rec.calls.clear()
    d3 = make(d2_state)
    assert d3.commit_copy() is None and _names(rec) == []


def test_a_refused_copy_reports_it_and_stays_unlocked(make, rec, monkeypatch, tmp_path) -> None:
    from loomsiril import frames_dialog_actions as DA

    monkeypatch.setattr(DA.FC, "export_approved", lambda approved, destination, siril=None, home=None: {
        "written": 0, "failed": 0, "emptied": 0, "refused": "the destination is one of the source folders",
        "outcomes": {}})
    d = make()
    d.state["destination"] = str(tmp_path / "out")
    d.commit()
    assert rec.told[-1][0] == "Nothing was written: the destination is one of the source folders"
    assert d.state["locked"] is False and d.state["phase"] == F.PHASE["REVIEW"]


def test_a_copy_with_failures_says_how_many(make, rec, monkeypatch, tmp_path) -> None:
    from loomsiril import frames_dialog_actions as DA

    monkeypatch.setattr(DA.FC, "export_approved", lambda approved, destination, siril=None, home=None: {
        "written": 21, "failed": 1, "emptied": 0, "refused": None, "outcomes": {}})
    d = make()
    d.state["destination"] = str(tmp_path / "out")
    d.commit()
    assert "21 frame(s) written" in rec.told[-1][0] and "1 could not be written." in rec.told[-1][0]


def test_copy_or_delete_is_decided_afresh_after_the_destination_changes(make, rec, tmp_path) -> None:
    d = make()
    assert d.copying_out() is False                       # the dialog remembers: deleting in place
    d.state["destination"] = str(tmp_path / "out")
    d._copy_memo[d.state["destination"]] = False           # a stale answer for this destination must not be used
    d.commit()
    assert "export" in _names(rec) and "execute" not in _names(rec)
    # and back again to the source folder: a delete, not a copy
    rec.calls.clear()
    d.state["locked"] = False
    d.state["destination"] = d.state["folder"]
    d._copy_memo[d.state["destination"]] = True
    d.commit()
    assert "execute" in _names(rec) and "export" not in _names(rec)


# ---- import

class Card:
    """An import review: a card folder, a destination, and fakes for the copy and the master flats."""

    def __init__(self, make: Any, rec: Recorder, tmp_path: Any, monkeypatch: Any) -> None:
        from loomsiril import frames_dialog_actions as DA

        self.DA = DA
        self.card = tmp_path / "card"
        (self.card / "Plan" / "Light").mkdir(parents=True)
        self.out = tmp_path / "night"
        state = _state(str(self.card / "Plan" / "Light"))
        state["cardRoot"] = str(self.card)
        state["destination"] = str(self.out)
        self.rec = rec
        self.writes: list[dict[str, Any]] = []
        self.locked_at_write: list[bool] = []
        self.masters: list[Any] = []
        self.write_result: dict[str, Any] = {"written": 22, "failed": [], "cancelled": False,
                                             "written_paths": ["/x"]}
        self.master_result: Any = {"made": [{"name": "masterFlat_L", "flats": 5, "removed": 5}], "kept": [],
                                   "cancelled": False, "notes": []}

        def write_manifest(manifest: Any, card_root: Any, on_progress: Any = None, warn: Any = None) -> dict[str, Any]:
            self.writes.append(manifest)
            self.locked_at_write.append(self.d.state["locked"])
            return self.write_result

        def make_master_flats(*a: Any, **kw: Any) -> Any:
            self.masters.append((a, kw))
            return self.master_result

        monkeypatch.setattr(DA.AI, "write_manifest", write_manifest)
        monkeypatch.setattr(DA.AI, "make_master_flats", make_master_flats)
        self.d = make(state)

    def run(self) -> Any:
        return self.d.commit()

    def refused(self) -> bool:
        return not self.writes and self.d.state["locked"] is False


@pytest.fixture
def card(make, rec, tmp_path, monkeypatch):
    return Card(make, rec, tmp_path, monkeypatch)


def test_import_never_reaches_delete_or_copy(card, rec) -> None:
    card.run()
    assert "execute" not in _names(rec) and "export" not in _names(rec)
    assert len(card.writes) == 1
    assert len(card.writes[0]["lights"]) == 22 and card.writes[0]["flats"] == []


def test_import_is_decided_by_the_mode_not_by_the_destination(card, rec) -> None:
    card.d.state["destination"] = card.d.state["folder"]        # would be a delete in place if it were a path test
    card.run()
    assert "execute" not in _names(rec) and card.writes == []
    assert rec.told[-1][0].startswith("Choose a destination folder first.")


def test_import_without_a_destination_is_refused(card, rec) -> None:
    card.d.state["destination"] = None
    assert card.run() is None and card.refused()
    assert rec.told[-1][0] == "Choose a destination folder first.\n\nFrames are never written back to the card."
    assert rec.asked == []


def test_import_to_the_source_folder_is_refused(card, rec) -> None:
    card.d.state["destination"] = card.d.state["folder"]
    assert card.run() is None and card.refused()
    assert rec.asked == []


def test_import_to_the_card_is_refused_before_anything_else(card, rec, monkeypatch) -> None:
    card.d.state["destination"] = str(card.card / "Archive")
    monkeypatch.setattr(card.DA.FS, "approved_light_records",
                        lambda s: pytest.fail("the lights were read before the card check"))
    assert card.run() is None and card.refused()
    assert rec.told[-1][1] == "error" and rec.told[-1][0].startswith("That destination is on the card.")
    assert rec.asked == [] and "progress" not in [c[0] for c in rec.calls]


def test_import_to_a_link_into_the_card_is_refused(card, rec, tmp_path) -> None:
    link = tmp_path / "shortcut"
    os.symlink(card.card, link)
    card.d.state["destination"] = str(link / "sub")
    assert card.run() is None and card.refused()
    assert rec.told[-1][0].startswith("That destination is on the card.")


def test_import_with_every_frame_rejected_is_refused(card, rec) -> None:
    for ch in card.d.state["channels"].values():
        ch["settings"]["enabled"] = False
    assert card.run() is None and card.refused()
    assert rec.told[-1][0] == "Every frame is rejected; there is nothing to import."


def test_import_with_a_name_collision_is_refused(card, rec, monkeypatch) -> None:
    monkeypatch.setattr(card.DA.AN, "manifest", lambda lights, matches, root: {
        "lights": [], "flats": [], "collisions": [{"dst": root + "/Light/a.fit"}]})
    assert card.run() is None and card.refused()
    assert rec.told[-1][1] == "error" and "Nothing has been written." in rec.told[-1][0]
    assert rec.asked == []


def test_import_with_a_missing_darks_folder_is_refused(card, rec, tmp_path) -> None:
    card.d.state["darksFolder"] = str(tmp_path / "nodarks")
    card.d.state["onlyMasterFlats"] = True
    assert card.run() is None and card.refused()
    assert rec.told[-1][0].startswith("The darks folder cannot be found") and rec.told[-1][1] == "warning"
    assert rec.asked == []


def test_import_with_a_vanished_card_dark_is_refused(card, rec, tmp_path) -> None:
    card.d.state["onlyMasterFlats"] = True
    card.d.state["cardDarkMatch"] = {"found": True, "used": [{"path": str(tmp_path / "gone.fit")}],
                                     "exposures": [10]}
    assert card.run() is None and card.refused()
    assert rec.told[-1][0].startswith("A dark on the ASIAIR card cannot be found any more")


def test_declining_the_import_summary_writes_nothing(card, rec) -> None:
    rec.answer = False
    assert card.run() is None and card.refused()
    assert len(rec.asked) == 1 and "Lights (22)" in rec.asked[0]
    assert "FITS" in rec.asked[0] and "XISF" not in rec.asked[0]
    assert not card.out.exists()


def test_the_review_is_locked_before_the_first_write(card, rec) -> None:
    assert card.d.state["locked"] is False
    card.run()
    assert card.locked_at_write == [True]
    assert card.d.state["locked"] is True
    assert card.d.commit() is None and len(card.writes) == 1      # Run cannot start a second import


def test_the_import_log_is_written_under_the_temporary_folder(card, rec, tmp_path) -> None:
    card.run()
    logs = list((tmp_path / "tmp" / "Loom-import-logs").iterdir())
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "destination: " + str(card.out) in text and "master flats: off" in text and "copied: 22, failed: 0" in text
    assert rec.told[-1][0] == "Imported 22 frame(s).\n\nLog: " + str(logs[0])


def test_a_failed_copy_is_logged_and_told(card, rec, tmp_path) -> None:
    card.write_result = {"written": 21, "failed": [{"src": "/a.fit", "reason": "short read"}], "cancelled": False,
                         "written_paths": []}
    card.run()
    log = next((tmp_path / "tmp" / "Loom-import-logs").iterdir()).read_text(encoding="utf-8")
    assert "  FAILED /a.fit: short read" in log
    assert rec.told[-1][1] == "warning"


def _with_master_flats(card: Card, tmp_path: Any) -> None:
    (tmp_path / "darks").mkdir()
    card.d.state["darksFolder"] = str(tmp_path / "darks")
    card.d.state["onlyMasterFlats"] = True


def test_master_flats_are_made_after_a_complete_copy_and_reported(card, rec, tmp_path, monkeypatch) -> None:
    _with_master_flats(card, tmp_path)
    built: list[Any] = []

    class Engine:
        def __init__(self, siril: Any, read_header: Any, cache_dir: Any) -> None:
            built.append((siril, read_header, cache_dir))

    from loomsiril import master_flat_run, master_flat_siril

    monkeypatch.setattr(master_flat_siril, "SirilMasterFlatEngine", Engine)
    card.run()
    assert len(card.masters) == 1
    assert built == [(None, master_flat_run.quiet_header_obj, str(tmp_path / "cache"))]
    assert card.masters[0][1]["engine"].__class__ is Engine
    assert card.masters[0][1]["run"] is master_flat_run.execute
    assert "Master flat masterFlat_L from 5 flat(s); 5 raw flat(s) deleted." in rec.told[-1][0]
    assert rec.told[-1][1] == "info"
    assert "Combine" not in rec.told[-1][0]
    assert "darks from" in rec.asked[0] or "calibrated with" in rec.asked[0]


def test_kept_flats_make_the_outcome_a_warning(card, rec, tmp_path) -> None:
    _with_master_flats(card, tmp_path)
    card.master_result = {"made": [], "kept": [{"filter": "L", "binning": None, "flats": 5, "reason": "no dark"}],
                          "cancelled": False, "notes": []}
    card.run()
    assert "Raw flats kept for L (5): no dark." in rec.told[-1][0] and rec.told[-1][1] == "warning"


def test_no_master_flats_after_a_cancelled_copy(card, rec, tmp_path) -> None:
    _with_master_flats(card, tmp_path)
    card.write_result = {"written": 4, "failed": [], "cancelled": True, "written_paths": ["/x"]}
    card.run()
    assert card.masters == []
    assert "Cancelled before the master flats: the raw flats were kept." in rec.told[-1][0]
    assert rec.told[-1][1] == "warning"
    log = next((tmp_path / "tmp" / "Loom-import-logs").iterdir()).read_text(encoding="utf-8")
    assert "cancelled before they started" in log and "CANCELLED" in log


def test_a_cancelled_master_flat_step_reports_cancelled_and_never_reports_none(card, rec, tmp_path,
                                                                              monkeypatch) -> None:
    from loomsiril import master_flat_run

    _with_master_flats(card, tmp_path)
    card.master_result = None                                   # what make_master_flats returns after a cancel
    monkeypatch.setattr(master_flat_run, "report", lambda made: pytest.fail("reported None"))
    card.run()
    assert "Master flats cancelled: the raw flats were kept." in rec.told[-1][0] and rec.told[-1][1] == "warning"
    log = next((tmp_path / "tmp" / "Loom-import-logs").iterdir()).read_text(encoding="utf-8")
    assert "master flats: cancelled; the raw flats were kept" in log


def test_an_exception_in_the_master_flats_reports_the_import_and_the_log(card, rec, tmp_path, monkeypatch) -> None:
    _with_master_flats(card, tmp_path)

    flat_dir = card.out / "Flat"

    def boom(*a: Any, **kw: Any) -> Any:
        flat_dir.mkdir(parents=True)
        (flat_dir / "masterFlat_L.fit").write_bytes(b"x")          # the first filter finished, then the crash
        (flat_dir / ".partial_masterFlat_R.fit").write_bytes(b"x")
        raise RuntimeError("siril went away")

    monkeypatch.setattr(card.DA.AI, "make_master_flats", boom)
    card.run()
    logs = list((tmp_path / "tmp" / "Loom-import-logs").iterdir())
    assert "EXCEPTION: siril went away" in logs[0].read_text(encoding="utf-8")
    text, icon = rec.told[-1]
    assert text.startswith("Imported 22 frame(s).") and "The master flats failed: siril went away" in text
    assert "Log: " + str(logs[0]) in text and icon == "warning"
    assert "finished before the failure" in text and "masterFlat_L.fit." in text and "see the import log" in text and "masterFlat_R" not in text
    assert "raw flats were kept for the filters without a finished master; check " + str(flat_dir) in text
    assert "The raw flats were kept." not in text
    assert card.d.state["locked"] is True


def test_a_crash_before_any_master_says_none_was_finished(card, rec, tmp_path, monkeypatch) -> None:
    _with_master_flats(card, tmp_path)

    def boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("no siril")

    (card.out / "Flat").mkdir(parents=True)
    (card.out / "Flat" / "masterFlat_L.fit").write_bytes(b"x")     # an earlier import's master
    monkeypatch.setattr(card.DA.AI, "make_master_flats", boom)
    card.run()
    assert "No master flat was finished." in rec.told[-1][0] and "masterFlat_L" not in rec.told[-1][0]


def test_an_unexpected_error_during_the_copy_is_logged_and_raised_with_the_log_path(card, rec, tmp_path,
                                                                                  monkeypatch) -> None:
    def boom(*a: Any, **kw: Any) -> Any:
        raise OSError("disk went away")

    monkeypatch.setattr(card.DA.AI, "write_manifest", boom)
    with pytest.raises(RuntimeError) as err:
        card.run()
    logs = list((tmp_path / "tmp" / "Loom-import-logs").iterdir())
    assert "disk went away" in str(err.value) and "Log: " + str(logs[0]) in str(err.value)
    assert "EXCEPTION: disk went away" in logs[0].read_text(encoding="utf-8")
    assert card.d.state["locked"] is True


# ---- a second Run while the first is in a box or a progress window

def _queue_second_run(d: Any, rec: Recorder) -> None:
    d.top.after_idle(d._on_run)
    rec.pump = d.top.update


def test_a_second_run_during_a_copy_does_nothing(make, rec, tmp_path) -> None:
    d = make()
    d.state["destination"] = str(tmp_path / "out")
    _queue_second_run(d, rec)
    d._on_run()
    assert _names(rec).count("export") == 1


def test_a_second_run_during_an_import_does_nothing(card, rec) -> None:
    _queue_second_run(card.d, rec)
    card.d._on_run()
    assert len(card.writes) == 1 and len(rec.asked) == 1


def test_a_second_run_during_a_delete_does_nothing(make, rec) -> None:
    d = make()
    _queue_second_run(d, rec)
    d._on_run()
    assert _names(rec).count("execute") == 1 and len(rec.asked) == 1


def test_run_is_off_while_running_and_back_after(make, rec, tmp_path) -> None:
    d = make()
    d.state["destination"] = str(tmp_path / "out")
    seen: list[bool] = []
    rec.pump = lambda: seen.append(d.run_button.instate(["disabled"]))
    d.commit()
    assert seen and all(seen) and d._busy is False
    assert d.run_button.instate(["disabled"])                      # locked after the copy: nothing more to run
    d2 = make()
    rec.answer = False
    d2.commit()
    assert not d2.run_button.instate(["disabled"]) and d2._busy is False   # declined: Run is back


# ---- master-flat controls

def test_a_plain_review_has_no_master_flat_controls(make) -> None:
    d = make()
    assert d.darks_button is None
    d.sync_master_flat_controls()                                  # a no-op, not an error


def test_the_master_flat_controls_remember_their_values(make, tmp_path, monkeypatch) -> None:
    import tkinter.filedialog as fd

    store = FO.OptionsStore(tmp_path / "config")
    state = _state()
    state["cardRoot"] = str(tmp_path / "card")
    d = make(state, options_store=store)
    assert d.darks_button is not None and d.master_only_box.instate(["disabled"])    # no darks yet
    (tmp_path / "darks").mkdir()
    monkeypatch.setattr(fd, "askdirectory", lambda **kw: str(tmp_path / "darks"))
    d._on_darks_folder()
    assert state["darksFolder"] == str(tmp_path / "darks")
    assert not d.master_only_box.instate(["disabled"])
    d.master_only_var.set(True)
    d._on_master_only()
    assert state["onlyMasterFlats"] is True
    again = FO.OptionsStore(tmp_path / "config")
    from loomsiril import master_flat as MF

    assert MF.load_settings(again) == {"darksFolder": str(tmp_path / "darks"), "onlyMasterFlats": True}
    assert d.options.darks_folder == str(tmp_path / "darks") and d.options.only_master_flats is True
    d._on_darks_clear()
    assert state["darksFolder"] == "" and MF.load_settings(FO.OptionsStore(tmp_path / "config"))["darksFolder"] == ""
    assert not d.master_only_var.get()                              # no darks, so the box cannot stay ticked


def test_a_new_dialog_starts_from_the_remembered_master_flat_settings(make, tmp_path) -> None:
    from loomsiril import master_flat as MF

    store = FO.OptionsStore(tmp_path / "config")
    MF.save_settings(store, str(tmp_path / "darks"), True)
    state = _state()
    state["cardRoot"] = str(tmp_path / "card")
    d = make(state, options_store=FO.OptionsStore(tmp_path / "config"))
    assert state["darksFolder"] == str(tmp_path / "darks") and state["onlyMasterFlats"] is True
    assert d.master_only_var.get() is True
    assert str(tmp_path / "darks")[-20:] in d.darks_label.cget("text")


def test_the_controls_are_inert_while_the_review_is_locked(make, tmp_path, monkeypatch) -> None:
    import tkinter.filedialog as fd

    state = _state()
    state["cardRoot"] = str(tmp_path / "card")
    d = make(state)
    d.state["locked"] = True
    d.refresh()
    assert d.darks_button.instate(["disabled"]) and d.master_only_box.instate(["disabled"])
    assert d.darks_clear.instate(["disabled"])
    monkeypatch.setattr(fd, "askdirectory", lambda **kw: pytest.fail("the folder chooser opened while locked"))
    d.set_master_flat_settings(str(tmp_path / "kept"), False)
    d._on_darks_folder()                                            # editable() is False: nothing changes
    d._on_darks_clear()
    assert d.state["darksFolder"] == str(tmp_path / "kept")
    d.master_only_var.set(True)
    d._on_master_only()                                             # editable() is False: nothing changes
    assert d.state["onlyMasterFlats"] is False


def test_card_darks_hide_the_folder_controls_and_say_so(make, tmp_path) -> None:
    state = _state()
    state["cardRoot"] = str(tmp_path / "card")
    state["cardDarkMatch"] = {"found": True, "used": [{"path": "/d.fit"}], "exposures": [10]}
    d = make(state)
    assert "on the ASIAIR card" in d.darks_prefix.cget("text")
    assert not d.darks_button.winfo_ismapped() or d.darks_button.grid_info() == {}


def test_report_outcome_is_silent_without_a_conversion(make, rec) -> None:
    d = make()
    d.report_outcome(None)
    d.report_outcome({"converted": {"converted": 0, "failed": 0, "alreadyXisf": 0, "refused": None}})
    assert rec.told == []
    d.report_outcome({"converted": {"converted": 0, "failed": 0, "alreadyXisf": 0, "refused": "no room"}})
    assert rec.told == [("Nothing was converted: no room", "info")]
