"""Frame Selector: the review state and the dialog's decisions that are not drawing (loomsiril.frames_state).

Scans are plain dicts in the shape the JavaScript scan returns, except in the last tests, which read real FITS files
under pytest's tmp_path through loomsiril.frames_scan with a fake measurement. Nothing here needs Siril,
PixInsight, node or a display.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import numpy as np

from loomsiril import frames as F
from loomsiril import frames_scan as S
from loomsiril import frames_state as FS
from loomsiril.frames_cache import MeasurementStore
from loomsiril.io_fits import write_fits

CONFIG = "test configuration"


# ---- helpers ------------------------------------------------------------------------------------------

def _metrics(stars: float = 1000.0, fwhm: float = 3.0, ecc: float = 0.5, psf: float = 100.0) -> dict[str, Any]:
    m: dict[str, Any] = {k: None for k in F.STORED_KEYS}
    m.update({"psfSNR": psf, "fwhm": fwhm, "eccentricity": ecc, "stars": stars, "noise": 1.0})
    return m


def _entry(path: str, filter_: Optional[str] = "L", time: Optional[str] = None) -> dict[str, Any]:
    return {"path": path, "filter": filter_, "time": time,
            "identity": {"digest": "d" + path, "size": 1, "mtime": 1}}


def _channel(key: str, folder: str = "/x", n: int = 11, outlier: bool = True,
             problems: Optional[list[str]] = None) -> dict[str, Any]:
    """A channel of ``n`` frames with a spread of FWHM; the last frame is a blurred outlier when ``outlier``."""
    entries = [_entry(f"{folder}/{key}{i}.fit", key) for i in range(n)]
    metrics: dict[str, Any] = {}
    for i, e in enumerate(entries):
        if outlier and i == n - 1:
            metrics[e["path"]] = _metrics(fwhm=20.0)
        else:
            metrics[e["path"]] = _metrics(fwhm=2.9 + 0.05 * i, stars=1000.0 + 5 * i)
    return {"entries": entries, "metrics": metrics, "problems": problems or []}


def _scan(channels: dict[str, Any], unstable: Optional[list[str]] = None, skipped: Optional[list[Any]] = None,
          cancelled: bool = False) -> dict[str, Any]:
    return {"channels": channels, "unstable": unstable or [], "skipped": skipped or [], "cancelled": cancelled}


def _state(scan: dict[str, Any], folder: str = "/x") -> dict[str, Any]:
    return FS.state_from_scan(FS.empty_state(folder), scan)


def _totals(state: dict[str, Any]) -> dict[str, Any]:
    return FS.recompute_all(state)


# ---- the empty review and the scan ----------------------------------------------------------------------

def test_empty_state_starts_as_a_review_that_culls_the_folder_in_place() -> None:
    st = FS.empty_state("/x")
    assert st["folder"] == "/x" and st["destination"] == "/x"
    assert st["channels"] == {} and st["order"] == []
    assert st["phase"] == "review" and st["locked"] is False and st["manifest"] is None
    assert st["preset"] == F.DEFAULT_PRESET
    assert st["cardRoot"] is None and st["cardDarkMatch"] is None
    assert st["candidateFlats"] == [] and st["cardDarks"] == []
    assert st["darksFolder"] == "" and st["onlyMasterFlats"] is False


def test_state_from_scan_two_channels_in_sorted_order_with_their_own_settings() -> None:
    scan = _scan({"L": _channel("L"), "Ha": _channel("Ha")})
    st = _state(scan)
    assert st["order"] == ["Ha", "L"]
    assert list(st["channels"]) == ["Ha", "L"]
    ha, lum = st["channels"]["Ha"], st["channels"]["L"]
    assert ha["settings"] == F.default_settings() == lum["settings"]
    assert ha["settings"] is not lum["settings"]
    ha["settings"]["enabled"] = False            # switching one channel off does not switch the other off
    assert lum["settings"]["enabled"] is True
    assert len(ha["rows"]) == 11 and len(lum["rows"]) == 11
    assert ha["rows"][0]["channel"] == "Ha" and lum["rows"][0]["channel"] == "L"


def test_state_from_scan_keeps_the_scan_flags_the_channel_and_the_unstable_list() -> None:
    scan = _scan({"L": _channel("L")}, unstable=["/x/moved.fit"], skipped=[{"path": "/x/m.fit"}])
    st = _state(scan)
    assert st["unstable"] == ["/x/moved.fit"]
    assert st["skipped"] == [{"path": "/x/m.fit"}]
    assert st["cancelled"] is False
    ch = st["channels"]["L"]
    assert len(ch["flags"]) == len(ch["rows"])
    assert ch["flags"][10] == ["focus"]          # the blurred outlier, with the others comparable
    assert ch["flags"][0] == []


def test_a_channel_with_problems_is_kept_with_them_and_gets_no_flags() -> None:
    scan = _scan({"L": _channel("L", problems=["exposures differ"])})
    st = _state(scan)
    ch = st["channels"]["L"]
    assert ch["problems"] == ["exposures differ"]
    assert ch["flags"] == [[] for _ in ch["rows"]]
    assert FS.flag_line(st, "L") == "  |  not flagged: frames are not comparable"


def test_a_group_with_no_filter_is_not_flagged_and_not_auto_rejected() -> None:
    no_filter = F.NO_FILTER
    scan = _scan({no_filter: _channel(no_filter)})
    st = _state(scan)
    ch = st["channels"][no_filter]
    assert ch["flags"] == [[] for _ in ch["rows"]]
    t = _totals(st)
    assert t["condemned"] == 0
    assert FS.flag_line(st, no_filter) == "  |  not flagged: frames are not comparable"


def test_a_cancelled_scan_with_no_channels_is_empty_and_marked_cancelled() -> None:
    st = _state(_scan({}, cancelled=True))
    assert st["cancelled"] is True
    assert st["channels"] == {} and st["order"] == []
    assert _totals(st) == {"total": 0, "condemned": 0, "mixed": [], "approved": 0}


def test_a_cancelled_scan_keeps_the_channels_it_has() -> None:
    st = _state(_scan({"L": _channel("L")}, cancelled=True))
    assert st["cancelled"] is True and st["order"] == ["L"]


def test_build_state_from_names_the_review_with_the_label_and_scans_the_paths() -> None:
    seen: dict[str, Any] = {}

    def scan_paths(paths: list[str], progress: Any) -> dict[str, Any]:
        seen["paths"], seen["progress"] = paths, progress
        return _scan({"L": _channel("L", folder="/night")})

    st = FS.build_state_from(["/night/L0.fit"], "Night 1", "P", scan_paths)
    assert st["folder"] == "Night 1" and st["destination"] == "Night 1"
    assert seen == {"paths": ["/night/L0.fit"], "progress": "P"}
    assert st["order"] == ["L"]


def test_build_state_takes_the_scan_of_the_folder_as_a_parameter() -> None:
    calls: list[tuple[str, Any]] = []

    def scan(folder: str, progress: Any) -> dict[str, Any]:
        calls.append((folder, progress))
        return _scan({"L": _channel("L", folder=folder)}, unstable=["u"])

    st = FS.build_state("/x", None, scan)
    assert calls == [("/x", None)]
    assert st["folder"] == "/x" and st["unstable"] == ["u"]


def test_approved_light_records_lists_enabled_channels_that_are_not_rejected() -> None:
    st = _state(_scan({"L": _channel("L"), "Ha": _channel("Ha", n=6, outlier=False)}))
    st["channels"]["Ha"]["settings"]["enabled"] = False
    recs = FS.approved_light_records(st)
    assert all(r["filter"] == "L" for r in recs)
    assert len(recs) == 10                       # the blurred frame is rejected and left out
    assert {"path": "/x/L0.fit", "filter": "L"} in recs
    assert "/x/L10.fit" not in [r["path"] for r in recs]


def test_approved_light_records_keep_an_unmeasurable_frame_as_not_rejected() -> None:
    ch = _channel("L", outlier=False)
    path = "/x/L0.fit"
    ch["metrics"][path] = None
    st = _state(_scan({"L": ch}))
    assert st["channels"]["L"]["rows"][0]["state"] == "unmeasurable"
    assert {"path": path, "filter": "L"} in FS.approved_light_records(st)


# ---- the summary and the totals ----------------------------------------------------------------------

def test_recompute_all_counts_switched_off_channels_in_the_total_but_not_the_condemned_count() -> None:
    st = _state(_scan({"L": _channel("L"), "Ha": _channel("Ha")}))
    st["channels"]["Ha"]["settings"]["enabled"] = False
    t = _totals(st)
    assert t["total"] == 22
    assert t["condemned"] == 1                   # only L's blurred frame: Ha is switched off
    assert t["approved"] == 21                   # total minus condemned, as the dialog has always said
    assert t["mixed"] == []


def test_recompute_all_reports_a_channel_that_is_not_comparable_and_has_rejects() -> None:
    st = _state(_scan({"L": _channel("L", problems=["exposures differ"])}))
    t = _totals(st)
    assert t["condemned"] == 1 and t["mixed"] == ["L"]


def test_recompute_all_recomputes_the_verdicts_in_place() -> None:
    st = _state(_scan({"L": _channel("L")}))
    st["channels"]["L"]["rows"][10]["state"] = "approved"     # stale on purpose
    _totals(st)
    assert st["channels"]["L"]["rows"][10]["state"] == "rejected"


def test_summary_text_for_a_delete_in_place_and_for_a_copy_with_unstable_frames_and_a_flag_line() -> None:
    st = _state(_scan({"L": _channel("L")}, unstable=["/x/moved.fit"]))
    t = _totals(st)
    assert FS.summary_text(st, t, "L") == (
        "11 frame(s), 1 to remove; 1 changed while being measured and were excluded  |  1 flagged: 1 focus")
    st["destination"] = "/elsewhere"
    assert FS.summary_text(st, t, "L") == (
        "11 frame(s), 1 to remove; 1 changed while being measured and were excluded; 10 approved to copy"
        "  |  1 flagged: 1 focus")


def test_summary_text_names_a_channel_that_is_not_comparable_and_says_to_check_it() -> None:
    st = _state(_scan({"L": _channel("L", problems=["exposures differ"])}))
    t = _totals(st)
    assert FS.summary_text(st, t, "L") == (
        "11 frame(s), 1 to remove; L not comparable -- check before running  |  not flagged: frames are not comparable")


def test_summary_text_without_a_current_channel_has_no_flag_line() -> None:
    st = _state(_scan({"L": _channel("L")}))
    t = _totals(st)
    assert FS.summary_text(st, t, None) == "11 frame(s), 1 to remove"
    assert FS.flag_line(st, None) == ""
    assert FS.flag_line(st, "missing") == ""


def test_flag_line_names_the_flags_of_a_comparable_channel() -> None:
    st = _state(_scan({"L": _channel("L")}))
    assert FS.flag_line(st, "L") == "  |  1 flagged: 1 focus"
    clean = _state(_scan({"L": _channel("L", outlier=False)}))
    assert FS.flag_line(clean, "L") == ""


def test_shown_metrics_leave_out_the_snr_weight_the_siril_measurement_does_not_have() -> None:
    assert FS.SHOWN_METRICS == ["psfSNR", "fwhm", "eccentricity", "stars"]
    assert "snrWeight" in F.DISPLAY_METRICS and "snrWeight" not in FS.SHOWN_METRICS


# ---- the rows a run acts on ----------------------------------------------------------------------------

def _two_channel_state() -> dict[str, Any]:
    """L: 11 frames, one rejected. Ha: 11 frames, one rejected, then switched off."""
    st = _state(_scan({"L": _channel("L"), "Ha": _channel("Ha")}))
    st["channels"]["Ha"]["settings"]["enabled"] = False
    return st


def test_committable_rows_in_delete_mode_skip_a_channel_switched_off() -> None:
    st = _two_channel_state()
    st["destination"] = "/x"
    c = FS.committable_rows(st)
    assert c["perChannel"] == ["L: 1"]
    assert len(c["rows"]) == 11
    assert all(r["channel"] == "L" for r in c["rows"])


def test_committable_rows_do_not_depend_on_the_copy_destination() -> None:
    st = _two_channel_state()
    delete_mode = FS.committable_rows(st)
    st["destination"] = "/elsewhere"
    copy_mode = FS.committable_rows(st)
    assert copy_mode["perChannel"] == delete_mode["perChannel"] == ["L: 1"]
    assert [r["path"] for r in copy_mode["rows"]] == [r["path"] for r in delete_mode["rows"]]


def test_committable_rows_count_a_condemned_override_and_not_a_rescued_one() -> None:
    st = _state(_scan({"L": _channel("L")}))
    rows = st["channels"]["L"]["rows"]
    rows[0]["override"] = "condemned"            # an approved frame condemned by hand
    rows[10]["override"] = "rescued"             # the blurred frame rescued by hand
    c = FS.committable_rows(st)
    assert c["perChannel"] == ["L: 1"]           # 1 = the condemned frame; the rescued one is kept
    counts = F.counts(st["channels"]["L"]["rows"])
    assert counts["rescued"] == 1 and counts["condemned"] == 1 and counts["rejected"] == 1


def test_approved_paths_in_delete_and_copy_mode_leave_out_rejected_frames_and_switched_off_channels() -> None:
    st = _two_channel_state()
    st["destination"] = "/x"
    delete_mode = FS.approved_paths(st)
    assert len(delete_mode) == 10 and all(p.startswith("/x/L") for p in delete_mode)
    assert "/x/L10.fit" not in delete_mode
    st["destination"] = "/elsewhere"
    assert FS.approved_paths(st) == delete_mode


def test_approved_paths_keep_a_rescued_frame_and_drop_a_condemned_one() -> None:
    st = _state(_scan({"L": _channel("L")}))
    st["channels"]["L"]["rows"][0]["override"] = "condemned"
    st["channels"]["L"]["rows"][10]["override"] = "rescued"
    paths = FS.approved_paths(st)
    assert "/x/L0.fit" not in paths and "/x/L10.fit" in paths


# ---- where the output goes ----------------------------------------------------------------------------

def test_destination_is_the_scanned_folder_in_place_and_another_folder_is_a_copy() -> None:
    st = _state(_scan({"L": _channel("L", folder="/night")}), folder="/night")
    assert st["destination"] == "/night"
    assert FS.destination_is_source(st) is True
    assert FS.copying_out(st) is False
    st["destination"] = "/elsewhere"
    assert FS.destination_is_source(st) is False
    assert FS.copying_out(st) is True


def test_no_destination_is_neither_the_source_nor_a_copy() -> None:
    st = _state(_scan({"L": _channel("L")}))
    st["destination"] = None
    assert FS.destination_is_source(st) is False
    assert FS.copying_out(st) is False


def test_a_trailing_slash_is_another_folder_as_written_and_so_a_copy() -> None:
    st = _state(_scan({"L": _channel("L", folder="/night")}), folder="/night")
    st["destination"] = "/night/"
    assert FS.destination_is_source(st) is False
    assert FS.copying_out(st) is True


def test_all_paths_lists_every_frame_of_every_channel_whatever_its_verdict() -> None:
    st = _two_channel_state()
    paths = FS.all_paths(st)
    assert len(paths) == 22
    assert "/x/L10.fit" in paths and "/x/Ha10.fit" in paths


def test_destination_text_for_a_delete_in_place_says_so_and_never_mentions_conversion() -> None:
    st = _state(_scan({"L": _channel("L")}))
    t = _totals(st)
    text = FS.destination_text(st, t)
    assert text == ("<i>/x</i> &mdash; <b>the folder the frames are in.</b> Nothing is copied; the 1 rejected "
                    "frame(s) are <b>deleted</b> where they are.")
    assert "XISF" not in text and "convert" not in text


def test_destination_text_for_a_copy_says_nothing_is_deleted() -> None:
    st = _state(_scan({"L": _channel("L")}))
    st["destination"] = "/elsewhere"
    t = _totals(st)
    assert FS.destination_text(st, t) == (
        "<i>/elsewhere</i> &mdash; the 10 kept frame(s) are copied there; nothing is deleted.")


def test_destination_text_without_a_folder_and_with_a_long_one_is_elided_at_the_head() -> None:
    st = _state(_scan({"L": _channel("L")}))
    t = _totals(st)
    st["destination"] = None
    assert FS.destination_text(st, t) == "<i>no folder chosen</i>"
    long_path = "/archive/someone/" + "a" * 60 + "/night"
    st["destination"] = long_path
    text = FS.destination_text(st, t)
    shown = text[len("<i>"):text.index("</i>")]
    assert shown == "..." + long_path[-37:] and len(shown) == 40


def test_elide_head_keeps_short_texts_and_the_tail_of_long_ones() -> None:
    assert FS.elide_head("short", 40) == "short"
    assert FS.elide_head(None, 40) == ""
    assert FS.elide_head("abcdefghij", 6) == "...hij"
    assert FS.elide_head("abcdefghij", 3) == "abcdefghij"        # a width under 4 shows the text whole


# ---- import from a card -------------------------------------------------------------------------------

def test_importing_is_the_card_root_being_set() -> None:
    st = _state(_scan({"L": _channel("L")}))
    assert FS.importing(st) is False
    st["cardRoot"] = "/card"
    assert FS.importing(st) is True


def test_master_flats_mode_needs_the_import_the_box_and_somewhere_to_get_darks() -> None:
    st = _state(_scan({"L": _channel("L")}))
    st["onlyMasterFlats"] = True
    assert FS.master_flats_mode(st) is False     # not an import at all

    st["cardRoot"] = "/card"
    assert FS.master_flats_mode(st) is False     # an import, but no darks folder and no card darks

    st["darksFolder"] = "/darks"
    assert FS.master_flats_mode(st) is True      # a darks folder will do

    st["darksFolder"] = ""
    st["cardDarkMatch"] = {"found": True}
    assert FS.master_flats_mode(st) is True      # the card's darks stand in for the folder
    assert FS.card_darks_found(st) is True

    st["onlyMasterFlats"] = False
    assert FS.master_flats_mode(st) is False     # the box is not ticked


def test_card_darks_found_needs_the_card_and_a_found_match() -> None:
    st = _state(_scan({"L": _channel("L")}))
    st["cardDarkMatch"] = {"found": True}
    assert FS.card_darks_found(st) is False      # no card
    st["cardRoot"] = "/card"
    st["cardDarkMatch"] = {"found": False}
    assert FS.card_darks_found(st) is False
    st["cardDarkMatch"] = None
    assert FS.card_darks_found(st) is False


def test_matched_flats_asks_each_light_filter_once_and_reports_each_flat() -> None:
    st = _state(_scan({"L": _channel("L")}))
    st["candidateFlats"] = [{"path": "/card/f1.fit"}, {"path": "/card/f2.fit"}]
    lights = [{"path": "/x/a.fit", "filter": "L"}, {"path": "/x/b.fit", "filter": "L"},
              {"path": "/x/c.fit", "filter": "Ha"}]
    headers: list[str] = []
    described: list[str] = []
    progress: list[tuple[int, int, str]] = []

    def entry_for(path: str) -> dict[str, Any]:
        headers.append(path)
        return {"filter": "L" if path.endswith("a.fit") else "Ha", "binning": 1}

    def describe_frame(frame: dict[str, Any]) -> dict[str, Any]:
        described.append(frame["path"])
        return {"path": frame["path"], "filter": "L", "binning": 1, "camera": None, "rotation": None}

    out = FS.matched_flats(st, lights, entry_for, describe_frame, lambda d, n, p: progress.append((d, n, p)))
    assert headers == ["/x/a.fit", "/x/c.fit"]          # one header per distinct light filter
    assert described == ["/card/f1.fit", "/card/f2.fit"]
    assert progress == [(1, 2, "/card/f1.fit"), (2, 2, "/card/f2.fit")]
    assert isinstance(out, list)


# ---- the dialog's editing gate and the tags of the selected frame --------------------------------------

def test_editable_only_in_the_review_and_while_not_locked() -> None:
    st = _state(_scan({"L": _channel("L")}))
    assert FS.editable(st) is True
    st["locked"] = True
    assert FS.editable(st) is False
    st["locked"] = False
    st["phase"] = "executing"
    assert FS.editable(st) is False


def test_current_tags_put_the_reason_for_leaving_a_frame_out_before_its_flags() -> None:
    st = _state(_scan({"L": _channel("L")}))
    st["destination"] = "/x"
    tags = FS.current_tags(st, "L", 10)
    assert tags == [{"text": "REJECTED", "kind": "reject"}, {"text": "FOCUS", "kind": "flag"}]
    assert FS.current_tags(st, "L", 0) == []
    assert FS.current_tags(st, "L", 99) == []
    assert FS.current_tags(st, None, 0) == []
    st["channels"]["L"]["settings"]["enabled"] = False
    assert FS.current_tags(st, "L", 10) == [{"text": "FOCUS", "kind": "flag"}]  # a delete leaves a switched-off channel alone
    st["destination"] = "/elsewhere"
    assert FS.current_tags(st, "L", 10) == [{"text": "REJECTED", "kind": "reject"}, {"text": "FOCUS", "kind": "flag"}]
    assert FS.current_tags(st, "L", 0) == [{"text": "CHANNEL OFF", "kind": "reject"}]


# ---- measuring: a frame that could not be measured is measured again ----------------------------------

def _frame(path: Path, filter_: Optional[str] = None, imagetyp: str = "Light Frame") -> str:
    header: dict[str, Any] = {"IMAGETYP": imagetyp, "OBJECT": os.path.basename(str(path))}
    if filter_ is not None:
        header["FILTER"] = filter_
    write_fits(str(path), [np.ones((4, 4), dtype=np.float32)], header=header)
    return str(path)


def _value(path: str) -> float:
    return float(sum(map(ord, os.path.basename(path))))


class _Measure:
    """A fake measurement: ``none_for`` paths give an explicit None, ``problem_for`` abandon the channel."""

    def __init__(self, none_for: frozenset = frozenset(), problem_for: frozenset = frozenset()) -> None:
        self.none_for = none_for
        self.problem_for = problem_for
        self.measured: list[str] = []

    def __call__(self, siril: Any, paths: list[str], work_dir: str,
                 altitude_of: Any = None) -> tuple[dict[str, Any], Optional[str]]:
        self.measured.extend(paths)
        if self.problem_for & set(paths):
            return {}, "SubframeSelector failed"
        return {p: (None if p in self.none_for else {k: _value(p) for k in F.STORED_KEYS}) for p in paths}, None


def _real_scan(folder: Path, cache: Path, measure: _Measure) -> Any:
    def scan(f: str, progress: Any) -> dict[str, Any]:
        return S.scan(f, progress, siril=None, cache_dir=str(cache), measure=measure, config_text=CONFIG)
    return scan


def test_a_frame_that_could_not_be_measured_is_unmeasurable_and_never_stored_so_it_is_measured_again(
        tmp_path: Path) -> None:
    folder = tmp_path / "inputs"
    folder.mkdir()
    paths = [_frame(folder / f"light_L_{i}.fit", "L") for i in range(1, 4)]
    cache = tmp_path / "cache"

    flaky = _Measure(none_for=frozenset([paths[0]]))
    st = FS.build_state(str(folder), None, _real_scan(folder, cache, flaky))
    rows = {r["path"]: r for r in st["channels"]["L"]["rows"]}
    assert rows[paths[0]]["state"] == "unmeasurable" and rows[paths[0]]["metrics"] is None
    assert rows[paths[1]]["metrics"] is not None
    assert st["channels"]["L"]["problems"] == []

    table = MeasurementStore(str(cache), CONFIG).load_table()
    assert len(table) == 2                       # the two numbers that exist, and not the None
    assert all(isinstance(v, dict) for v in table.values())

    later = _Measure()
    st2 = FS.build_state(str(folder), None, _real_scan(folder, cache, later))
    assert sorted(later.measured) == [paths[0]]  # only the frame that had no numbers is measured again
    assert all(r["metrics"] is not None for r in st2["channels"]["L"]["rows"])


def test_a_channel_whose_measurement_reports_a_problem_is_abandoned_in_the_state(tmp_path: Path) -> None:
    folder = tmp_path / "inputs"
    folder.mkdir()
    paths = [_frame(folder / f"light_L_{i}.fit", "L") for i in range(1, 3)]
    cache = tmp_path / "cache"
    st = FS.build_state(str(folder), None, _real_scan(folder, cache, _Measure(problem_for=frozenset(paths))))
    ch = st["channels"]["L"]
    assert ch["problems"] == ["measurement failed"]
    assert all(r["state"] == "unmeasurable" for r in ch["rows"])
    assert MeasurementStore(str(cache), CONFIG).load_table() == {}


def test_build_state_from_real_files_names_the_review_and_keeps_the_channel_order(tmp_path: Path) -> None:
    folder = tmp_path / "night"
    folder.mkdir()
    paths = [_frame(folder / "light_L_1.fit", "L"), _frame(folder / "light_Ha_1.fit", "Ha")]
    cache = tmp_path / "cache"

    def scan_paths(ps: list[str], progress: Any) -> dict[str, Any]:
        return S.scan_paths(ps, progress, siril=None, cache_dir=str(cache), measure=_Measure(), config_text=CONFIG)

    st = FS.build_state_from(paths, "Night 1", None, scan_paths)
    assert st["folder"] == "Night 1" and st["order"] == ["Ha", "L"]
    assert st["cancelled"] is False and st["unstable"] == []
