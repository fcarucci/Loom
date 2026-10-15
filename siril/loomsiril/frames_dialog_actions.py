"""Frame Selector: what Run does, as the mixin :class:`DialogActions` that ``frames_dialog.Dialog`` uses as its base.

Ported from ``pixinsight/script/FrameSelector.js`` 3436-3828 (members of ``FrameSelector.Dialog``):

    approvedPaths                3438-3452 -> DialogActions.approved_paths (frames_state.approved_paths)
    reportOutcome                3454-3474 -> DialogActions.report_outcome (the in-place conversion is not done by the
                                              Siril port, so it only ever has something to say if a result carries it)
    commitCopy                   3479-3509 -> DialogActions.commit_copy
    buildMasterFlatControls      3510-3567 -> DialogActions.build_master_flat_controls (built only for an import)
    setMasterFlatSettings        3569-3576 -> DialogActions.set_master_flat_settings (remembered at once)
    masterFlatsMode              3578-3583 -> DialogActions.master_flats_mode (frames_state.master_flats_mode)
    syncMasterFlatControls       3585-3604 -> DialogActions.sync_master_flat_controls
    importing                    3606-3608 -> DialogActions.importing (frames_state.importing)
    commitImport                 3615-3730 -> DialogActions.commit_import
    matchedFlats                 3732-3750 -> DialogActions.matched_flats (frames_state.matched_flats)
    commit                       3762-3826 -> DialogActions.commit

The order of ``commit`` is the JavaScript's: not editable -> nothing; pending edits are committed; an import goes to
``commit_import`` (decided by the mode, never by a path comparison, so deleting in place is unreachable for a card);
a copy goes to ``commit_copy``; anything else is a delete. Whether it is a copy is asked of
``frames_state.copying_out`` afresh at every Run, never of a value remembered for a destination.

Differences from the JavaScript, all deliberate:

* Kept frames are NOT converted after a delete in place (the Siril port leaves them as they are);
  ``frames_copy.convert_in_place`` is called all the same and says nothing.
* After a delete the outcome is told in a box (how many were deleted, skipped, failed, and where the log is); when
  the log could not be written nothing was deleted, the box says so and the run is marked stopped, not done.
* Master flats are made only for a copy that was not cancelled. A cancelled copy reports "master flats cancelled"
  and keeps the raw flats; the master-flat step's own cancel (which returns nothing) is reported the same way.
* Settings are written to the options store (when the dialog has one) and kept in the dialog's options, so the
  value written when the window closes is the same.

This module must not import ``loomsiril.frames_dialog`` (it imports this one). tkinter is imported when a control is
made, never at import.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import os
import tempfile
from typing import Any, Optional

from loomsiril import asiair_card
from loomsiril import asiair_import as AI
from loomsiril import asiair_names as AN
from loomsiril import frames as F
from loomsiril import frames_copy as FC
from loomsiril import frames_delete as FDel
from loomsiril import frames_headers
from loomsiril import frames_state as FS
from loomsiril import master_flat as MF
from loomsiril import master_flat_run
from loomsiril.frames_ui import ask, tell, with_progress

_DARKS_ELIDE = 40


def _iso_now(stamp: _dt.datetime) -> str:
    """``Date#toISOString``: UTC with milliseconds."""
    s = stamp.astimezone(_dt.timezone.utc)
    return f"{s:%Y-%m-%dT%H:%M:%S}.{s.microsecond // 1000:03d}Z"


def _icon(name: str) -> str:
    return "warning" if name == "warning" else ("error" if name == "error" else "info")


def delete_question(count: int, per_channel: list[str]) -> str:
    """The deletion's confirmation (FrameSelector.js 3779-3781): the exact count per channel, and that it is final."""
    return ("Delete " + str(count) + " frame(s)?\n\n" + "\n".join(per_channel) + "\n\nThis cannot be undone.")


def delete_outcome(result: dict[str, Any]) -> tuple[str, str]:
    """``(text, icon)`` of a finished deletion. No log means nothing was deleted."""
    if result.get("logPath") is None:
        return ("Nothing was deleted: the log of what would be deleted could not be written.", "error")
    text = (str(result["deleted"]) + " frame(s) deleted"
            + ("; " + str(result["skipped"]) + " skipped (changed since they were measured)" if result["skipped"] else "")
            + ("; " + str(result["failed"]) + " could not be deleted" if result["failed"] else "")
            + ("; stopped early: the log could not be updated" if result["stopped"] else "")
            + ".\n\nLog: " + str(result["logPath"]))
    bad = result["skipped"] or result["failed"] or result["stopped"]
    return (text, "warning" if bad else "info")


class DialogActions:
    """Run, Copy, Import and the master-flat controls. Uses the dialog's ``state``, ``top``, ``siril``, ``cache``,
    ``options`` and ``options_store`` and its ``refresh``, ``editable`` and ``commit_pending_edits``."""

    darks_button: Any = None

    # ---- the frames that survive

    def approved_paths(self) -> list[str]:
        """The frames that survive, across every channel being acted on (3438-3452)."""
        return FS.approved_paths(self.state)

    def importing(self) -> bool:
        """The frames came off a card, so nothing may be written back to it (3606-3608)."""
        return FS.importing(self.state)

    def master_flats_mode(self) -> bool:
        """Is Run going to make master flats? (3578-3583)"""
        return FS.master_flats_mode(self.state)

    # ---- outcomes

    def report_outcome(self, result: Optional[dict[str, Any]]) -> None:
        """What a finished run converted, said plainly; nothing to convert says nothing (3454-3474)."""
        c = result.get("converted") if result else None
        if c is None or (c["converted"] == 0 and c["failed"] == 0 and c["refused"] is None):
            return
        if c["refused"] is not None:
            message = "Nothing was converted: " + str(c["refused"])
        else:
            message = (str(c["converted"]) + " frame(s) converted in place"
                       + ("; " + str(c["alreadyXisf"]) + " already were" if c["alreadyXisf"] else "")
                       + ("\n\n" + str(c["failed"]) + " could not be converted." if c["failed"] else ""))
        tell(message, "info", master=self.top)

    # ---- Run

    def commit(self) -> Any:
        """Run (3762-3826): an import, a copy or a delete in place, decided here and now."""
        if not self.editable():
            return None
        # A limit typed but not yet confirmed is part of what Run acts on.
        self.commit_pending_edits()
        # Import mode first, gated on the mode rather than on a path comparison.
        if self.importing():
            return self.commit_import()
        # Asked afresh at every Run: the destination may have changed since the dialog last looked.
        if FS.copying_out(self.state):
            return self.commit_copy()
        return self._commit_delete()

    def _commit_delete(self) -> Any:
        committable = FS.committable_rows(self.state)
        manifest = F.build_manifest(committable["rows"])
        if len(manifest["entries"]) == 0:
            return None
        if not ask(delete_question(len(manifest["entries"]), committable["perChannel"]), master=self.top):
            return None

        self.state["phase"] = F.next_phase(self.state["phase"], "commit")
        self.state["locked"] = True
        self.state["manifest"] = manifest

        def work(w: Any) -> dict[str, Any]:
            return FDel.execute(manifest, lambda done, total, path:
                                w.report("Deleting rejected frames", done, total, os.path.basename(path)))

        result = with_progress(self.top, "Loom Frame Selector - deleting", work)
        if FS.destination_is_source(self.state):
            # Kept frames are left as they are; the call is kept so the outcome shape is the JavaScript's.
            result["converted"] = FC.convert_in_place(self.approved_paths())
        stopped = bool(result["stopped"]) or result.get("logPath") is None
        self.state["phase"] = F.next_phase(self.state["phase"], "stop" if stopped else "finish")
        self.refresh()
        text, icon = delete_outcome(result)
        tell(text, icon, master=self.top)
        self.report_outcome(result)
        return result

    # ---- copy

    def commit_copy(self) -> Any:
        """Write the approved frames to the chosen folder (3479-3509). Nothing is deleted from the sources."""
        approved = self.approved_paths()
        if len(approved) == 0:
            return None
        dest = self.state["destination"]

        def work(w: Any) -> dict[str, Any]:
            w.display("Writing " + str(len(approved)) + " approved frame(s)", dest, None)
            return FC.export_approved(approved, dest, self.siril)

        result = with_progress(self.top, "Loom Frame Selector - writing", work)
        if result["refused"] is None:
            self.state["locked"] = True
        if result["refused"] is not None:
            message = "Nothing was written: " + str(result["refused"])
        else:
            message = (str(result["written"]) + " frame(s) written to\n" + str(dest)
                       + ("\n\n" + str(result["failed"]) + " could not be written." if result["failed"] else ""))
        tell(message, "info", master=self.top)
        self.refresh()
        return result

    # ---- import

    def matched_flats(self, lights: list[dict[str, Any]], on_flat: Any = None) -> list[dict[str, Any]]:
        """Which candidate flats suit the surviving light filters (3732-3750)."""
        return FS.matched_flats(self.state, lights, frames_headers.entry_for, asiair_card.describe, on_flat)

    def commit_import(self) -> Any:
        """Write the approved lights and this night's flats into the chosen folder (3615-3730); the card is never
        written to."""
        state = self.state
        dest = state["destination"]
        if dest is None or dest == state["folder"]:
            tell("Choose a destination folder first.\n\nFrames are never written back to the card.", "info",
                 master=self.top)
            return None
        if not AI.outputs_are_safe(dest, state["cardRoot"]):
            tell("That destination is on the card.\n\n" + dest + "\n\nPick somewhere else.", "error", master=self.top)
            return None
        lights = FS.approved_light_records(state)
        if len(lights) == 0:
            tell("Every frame is rejected; there is nothing to import.", "info", master=self.top)
            return None

        matches = with_progress(self.top, "Loom Frame Selector - flats", lambda w: self.matched_flats(
            lights, lambda done, total, path: w.report("Reading flat headers", done, total, os.path.basename(path))))
        manifest = AN.manifest(lights, matches, AN.import_root(dest)["root"])
        if len(manifest["collisions"]) > 0:
            tell("Two source frames would be written to one name, which would lose one of them:\n\n"
                 + manifest["collisions"][0]["dst"] + "\n\nNothing has been written.", "error", master=self.top)
            return None

        master = self.master_flats_mode()
        from_card = master and FS.card_darks_found(state)
        problem = (AN.master_flats_problem(state["darksFolder"], state["onlyMasterFlats"],
                                           [d["path"] for d in state["cardDarkMatch"]["used"]] if from_card else None)
                   if master else None)
        if problem is not None:
            tell(problem, "warning", master=self.top)
            return None
        summary = AN.import_summary(manifest, dest, AI.dark_source_of(state) if master else None)
        if not ask(summary, master=self.top):
            return None

        # Locked before the first write: the review can no longer change, and Run cannot start a second import.
        state["locked"] = True
        stamp = _dt.datetime.now(_dt.timezone.utc)
        log_path = AI.import_log_path(stamp, tempfile.gettempdir())
        log: list[str] = []
        note = log.append
        note("# Loom Frame Selector import, " + _iso_now(stamp))
        note("destination: " + dest)
        note("lights: " + str(len(manifest["lights"])) + ", flats: " + str(len(manifest["flats"])))
        note("master flats: " + ("on, darks from " + AN.dark_source(AI.dark_source_of(state)) if master else "off"))

        def copy_work(w: Any) -> dict[str, Any]:
            def progress(done: int, total: int) -> bool:
                try:
                    return bool(w.report("Importing", done, total, ""))
                except Exception:  # noqa: BLE001 - a progress window that fails must not stop the copy
                    return True
            return AI.write_manifest(manifest, state["cardRoot"], progress)

        result = with_progress(self.top, "Loom Frame Selector - importing", copy_work, cancellable=True)
        outcome = AI.import_outcome(result)
        note("copied: " + str(result["written"]) + ", failed: " + str(len(result["failed"]))
             + (", CANCELLED" if result["cancelled"] else ""))
        for f in result["failed"]:
            note("  FAILED " + str(f["src"]) + ": " + str(f["reason"]))
        note("result: " + " | ".join(p for p in outcome["text"].split("\n") if p != ""))
        AI.write_import_log(log_path, log)
        icon = _icon(outcome["icon"])
        master_text = ""
        if master and not result["cancelled"]:
            note("master flats:")
            try:
                made = self._make_master_flats(manifest, result, dest, note)
            except Exception as exc:  # noqa: BLE001
                note("EXCEPTION: " + str(exc))
                AI.write_import_log(log_path, log)
                raise
            if made is None:
                master_text = "\n\nMaster flats cancelled: the raw flats were kept."
                note("master flats: cancelled; the raw flats were kept")
                icon = "warning"
            else:
                report = master_flat_run.report(made)
                master_text = "\n\n" + report
                note("outcome:")
                for line in report.split("\n"):
                    note("  " + line)
                if made["kept"] or made["cancelled"] or made["notes"]:
                    icon = "warning"
        elif master:
            master_text = "\n\nCancelled before the master flats: the raw flats were kept."
            note("master flats: cancelled before they started; the raw flats were kept")
        if AI.write_import_log(log_path, log) is not None:
            master_text += "\n\nLog: " + log_path
        tell(outcome["text"] + master_text, icon, master=self.top)
        self.refresh()
        return result

    def _make_master_flats(self, manifest: dict[str, Any], written: dict[str, Any], dest: str, note: Any) -> Any:
        from loomsiril.master_flat_siril import SirilMasterFlatEngine

        cache_dir = getattr(self.cache, "cache_dir", None) or tempfile.gettempdir()
        engine = SirilMasterFlatEngine(self.siril, master_flat_run.quiet_header_obj, cache_dir)

        def work(w: Any) -> Any:
            w.announce("Reading headers")

            def should_stop() -> bool:
                w.top.update()
                return bool(w.cancelled)

            return AI.make_master_flats(
                manifest, written, dest, self.state, engine=engine, run=master_flat_run.execute, note=note,
                should_stop=should_stop, cache_dir=cache_dir,
                on_progress=lambda done, total, text: w.report("Master flats", done, total, text))

        return with_progress(self.top, "Loom Frame Selector - master flats", work, cancellable=True)

    # ---- the master-flat controls

    def build_master_flat_controls(self) -> None:
        """Where the darks are, and whether the night's flats become one master per filter (3510-3567). Built only for
        an import."""
        if not self.importing():
            return
        import tkinter as tk
        from tkinter import ttk

        from loomsiril.fly_tooltip import Tooltip

        frame = self.master_flat_frame
        self.darks_prefix = ttk.Label(frame, text="darks:")
        self.darks_button = ttk.Button(frame, text="Folder...", command=self._on_darks_folder)
        Tooltip(self.darks_button, "The folder your darks are in, master or raw. Used to calibrate the flats (matched "
                                   "by exposure, binning, gain and temperature) when \"Combine flats to masters\" is "
                                   "ticked. Raw darks are integrated into a master dark first, which is cached. May "
                                   "be empty.")
        self.darks_clear = ttk.Button(frame, text="Clear", command=self._on_darks_clear)
        Tooltip(self.darks_clear, "Forget the darks folder.")
        self.darks_label = ttk.Label(frame, text="")
        self.master_only_var = tk.BooleanVar(master=self.top, value=False)
        self.master_only_box = ttk.Checkbutton(frame, text="Combine flats to masters", variable=self.master_only_var,
                                               command=self._on_master_only)
        Tooltip(self.master_only_box, "Instead of copying the night's flats, calibrate them with the matching dark, "
                                      "integrate each filter into one master flat (masterFlat_<filter>.fit in the Flat "
                                      "folder) and delete the raw flat copies once the master is written and checked. "
                                      "A filter with no matching dark, or whose master fails, keeps its raw flats. "
                                      "Nothing on the ASIAIR is touched. Needs a darks folder, unless the ASIAIR card "
                                      "holds darks that suit the flats.")
        self.master_only_box.grid(row=0, column=0, sticky="w")
        self.darks_prefix.grid(row=0, column=1, padx=(16, 4))
        self.darks_button.grid(row=0, column=2)
        self.darks_clear.grid(row=0, column=3, padx=4)
        self.darks_label.grid(row=0, column=4, sticky="w")

    def _on_darks_folder(self) -> None:
        from tkinter import filedialog

        if self.editable():
            path = filedialog.askdirectory(parent=self.top, title="Folder of darks")
            if path:
                self.set_master_flat_settings(path, self.state["onlyMasterFlats"])
        self.refresh()

    def _on_darks_clear(self) -> None:
        if self.editable():
            self.set_master_flat_settings("", self.state["onlyMasterFlats"])
        self.refresh()

    def _on_master_only(self) -> None:
        if self.editable():
            self.set_master_flat_settings(self.state["darksFolder"], bool(self.master_only_var.get()))
        self.refresh()

    def set_master_flat_settings(self, folder: Any, only: Any) -> None:
        """Remember the choice, in the state Run reads and in the options (3569-3576)."""
        self.state["darksFolder"] = folder or ""
        self.state["onlyMasterFlats"] = bool(only)
        store = getattr(self, "options_store", None)
        if store is not None:
            MF.save_settings(store, self.state["darksFolder"], self.state["onlyMasterFlats"])
        options = getattr(self, "options", None)
        if options is not None:
            self.options = dataclasses.replace(options, darks_folder=self.state["darksFolder"],
                                               only_master_flats=self.state["onlyMasterFlats"])

    def sync_master_flat_controls(self) -> None:
        """The controls follow the state (3585-3604)."""
        if self.darks_button is None:
            return
        folder = self.state["darksFolder"] or ""
        ui = MF.card_darks_ui(FS.card_darks_found(self.state), folder, self.state["onlyMasterFlats"], self.editable())
        for widget, grid in ((self.darks_button, {"row": 0, "column": 2}),
                             (self.darks_clear, {"row": 0, "column": 3, "padx": 4}),
                             (self.darks_label, {"row": 0, "column": 4, "sticky": "w"})):
            if ui["darksVisible"]:
                widget.grid(**grid)
            else:
                widget.grid_remove()
        self.darks_prefix.configure(text="darks: on the ASIAIR card" if ui["found"] else "darks:")
        self.darks_button.state(["!disabled"] if ui["folderEnabled"] else ["disabled"])
        self.darks_clear.state(["!disabled"] if ui["clearEnabled"] else ["disabled"])
        self.master_only_box.state(["!disabled"] if ui["optionAvailable"] else ["disabled"])
        self.master_only_var.set(bool(ui["optionChecked"]))
        self.darks_label.configure(text=FS.elide_head(folder, _DARKS_ELIDE) if folder else "no folder chosen")
