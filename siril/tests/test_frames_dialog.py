"""The Frame Selector review dialog (Tk under a hidden root, skipped without a display; the pure parts need none)."""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pytest

from loomsiril import frames as F
from loomsiril import frames_options as FO
from loomsiril import frames_state as FS

def _scan(n: int = 12, filters: tuple[str, ...] = ("L", "R"), tmp: str = "/data/night") -> dict[str, Any]:
    """A scan dict as frames_scan returns it: ``n`` measured frames per filter; frame 3 of each is a blur."""
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


def _state(**kw: Any) -> dict[str, Any]:
    state = FS.state_from_scan(FS.empty_state("/data/night"), _scan(**kw))
    return state


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


class FakeLoop:
    """A schedule/cancel pair that runs nothing until the test says so: ticks are explicit."""

    def __init__(self) -> None:
        self.jobs: list[tuple[int, Any]] = []

    def schedule(self, ms: int, fn: Any) -> Any:
        job = (ms, fn)
        self.jobs.append(job)
        return job

    def cancel(self, handle: Any) -> None:
        if handle in self.jobs:
            self.jobs.remove(handle)

    def tick(self) -> bool:
        if not self.jobs:
            return False
        _ms, fn = self.jobs.pop(0)
        fn()
        return True


@pytest.fixture
def make(tk_root, tmp_path):
    from loomsiril.frames_dialog import Dialog
    from loomsiril.frames_preview_cache import PreviewCache

    made: list[Any] = []

    def build(state: Any = None, **kw: Any) -> Any:
        loop = kw.pop("loop", None)
        cache = PreviewCache(str(tmp_path / "cache"))
        extra = {"schedule": loop.schedule, "cancel": loop.cancel} if loop is not None else {}
        d = Dialog(tk_root, state if state is not None else _state(), cache=cache, **extra, **kw)
        made.append(d)
        return d

    yield build
    for d in made:
        d.close()


def _settle(d: Any) -> None:
    d.top.update()


# ---- pure

def test_plain_text_drops_the_markup() -> None:
    from loomsiril.frames_dialog import plain_text

    assert plain_text("<i>/a/b</i> &mdash; the folder <b>x</b>") == "/a/b — the folder x"


def test_no_window_appears_on_import() -> None:
    import subprocess
    import sys

    code = ("import sys, loomsiril.frames_dialog\n"
            "assert 'tkinter' not in sys.modules or sys.modules['tkinter']._default_root is None\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    env.pop("DISPLAY", None)
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# ---- the window and the review

def test_the_table_has_one_row_per_frame_named_by_what_differs(make) -> None:
    d = make()
    tree = d.frame_tree
    assert len(tree.get_children()) == 12
    assert tree.item("0", "text") == "000.fit"
    assert tree.item("11", "text") == "011.fit"
    assert d.channel_tree.item("L", "text") == "L: 11 kept of 12"


@pytest.fixture
def stub_frames(monkeypatch):
    """The frame readers replaced: no file is read; what was asked for is recorded."""
    from loomsiril import frames_dialog as FD
    from loomsiril import frames_preview as FP

    seen: dict[str, list] = {"stretch": [], "load": [], "thumb": []}

    def reference(rows, cache, siril=None):
        seen["stretch"].append(len(rows))
        return {"shadows": 0.1, "midtone": 0.2}

    def thumb(path, stretch, cache, siril=None):
        seen["thumb"].append(path)
        return np.full((8, 12), 90, dtype=np.uint8)

    def load(self, path, stretch, cache, siril=None):
        seen["load"].append(path)
        self.show(np.full((20, 30), 120, dtype=np.uint8))
        return True

    monkeypatch.setattr(FD, "reference_stretch", reference)
    monkeypatch.setattr(FD, "thumbnail_of", thumb)
    monkeypatch.setattr(FP.PreviewControl, "load", load)
    return seen


def _kept(d: Any, key: str = "L") -> int:
    ch = d.state["channels"][key]
    return F.keep_count(ch["rows"], ch["settings"]["enabled"], d.copying_out())["keep"]


def test_the_table_row_texts_tooltip_and_no_snr_column(make) -> None:
    d = make()
    assert list(d.frame_tree["columns"]) == ["psfSNR", "fwhm", "eccentricity", "stars", "score"]
    assert "snrWeight" not in d.frame_tree["columns"]
    ch = d.channel()
    row = ch["rows"][5]
    values = d.frame_tree.item("5", "values")
    assert values[1] == "2.05" and values[3] == "305"
    assert values[4] == F.round_display(row["score"])
    tip = d.row_tooltip_text(5)
    assert tip.startswith("/data/night/L_005.fit\napproved")
    assert d.row_tooltip_text(99) == ""


def test_a_rejected_frame_has_the_cross_and_its_failing_measurement_is_marked(make) -> None:
    d = make()
    assert d.channel()["rows"][3]["state"] == F.REJECTED
    assert d.failing_cells == [(3, "fwhm")]
    assert d.frame_tree.item("3", "image") == (str(d._icons["reject"]),)
    assert d.frame_tree.item("4", "image") == (str(d._icons["blank"]),)
    d.top.deiconify()
    _settle(d)
    d.top.update()
    d._paint_marks()
    marks = [m for m in d._marks if m.cget("text") == d.frame_tree.set("3", "fwhm")]
    assert len(marks) == 1 and str(marks[0].cget("foreground")) == "#cc2222"


def test_channel_switch_shows_the_other_channel_and_selects_its_first_frame(make, stub_frames) -> None:
    d = make()
    d.channel_tree.selection_set("R")
    _settle(d)
    assert d.current == "R"
    assert d.frame_tree.item("0", "text") == "000.fit"
    assert d.plot.rows == d.state["channels"]["R"]["rows"]
    assert d.selected_row_index() == 0
    assert stub_frames["load"][-1] == "/data/night/R_000.fit"
    assert d.keep_var.get() == "11 / 12 keep"
    assert "R" in d.summary_var.get() or "frame(s)" in d.summary_var.get()


def test_a_preset_change_recomputes_the_verdicts_and_the_keep_count(make) -> None:
    d = make()
    assert d.keep_var.get() == "11 / 12 keep" and d.channel()["settings"]["k"] == 2.5
    d.preset_var.set("strict")
    d._on_preset()
    ch = d.channel()
    assert ch["settings"]["preset"] == "strict" and ch["settings"]["k"] == 2.0
    assert d.k_var.get() == "2.00"
    assert d.keep_var.get() == "%d / 12 keep" % _kept(d)
    # the other channel keeps its own settings
    assert d.state["channels"]["R"]["settings"]["preset"] == "balanced"


def test_a_k_change_recomputes_and_pins_k_against_presets(make) -> None:
    d = make()
    before = _kept(d)
    d.k_var.set("0.5")
    d._on_k()
    s = d.channel()["settings"]
    assert s["k"] == 0.5 and s["kEdited"] is True
    assert _kept(d) <= before and d.keep_var.get() == "%d / 12 keep" % _kept(d)
    assert "set by hand" in d.problems_var.get()
    d.preset_var.set("lenient")
    d._on_preset()
    assert d.channel()["settings"]["k"] == 0.5          # an edited k survives a preset change
    d.k_var.set("99")
    d._on_k()
    assert d.channel()["settings"]["k"] == 10.0          # clamped to the range
    d.k_var.set("abc")
    d._on_k()
    assert d.k_var.get() == "10.00"                      # redrawn from the settings


def test_a_typed_limit_overrides_one_metric_and_survives_a_preset_change(make) -> None:
    d = make()
    entry = d.crit_edits["fwhm"]
    assert str(entry.cget("foreground")) == "#8a8a8a"       # automatic: grey
    d.top.deiconify()
    d.crit_edits["fwhm"].focus_force()
    _settle(d)
    d.crit_vars["fwhm"].set("2.02")
    d.crit_edits["fwhm"].event_generate("<Return>")
    _settle(d)
    s = d.channel()["settings"]
    assert s["limits"] == {"fwhm": {"hi": 2.02}}
    assert str(d.crit_edits["fwhm"].cget("foreground")) != "#8a8a8a"   # typed: plain
    assert d.crit_vars["fwhm"].get() == "2.02"
    assert str(d.crit_edits["stars"].cget("foreground")) == "#8a8a8a"  # the others stay automatic
    kept = _kept(d)
    assert kept < 11                                                    # frames above 2.02 are now left out
    d.preset_var.set("lenient")
    d._on_preset()
    assert d.channel()["settings"]["limits"] == {"fwhm": {"hi": 2.02}}
    assert d.crit_vars["fwhm"].get() == "2.02" and _kept(d) == kept
    d.crit_vars["fwhm"].set("")                                         # a blank box goes back to automatic
    d.commit_edit("fwhm")
    assert d.channel()["settings"]["limits"] == {}
    assert str(d.crit_edits["fwhm"].cget("foreground")) == "#8a8a8a"


def test_a_limit_is_confirmed_on_focus_out_and_by_run(make) -> None:
    d = make()
    d.top.deiconify()
    d.crit_edits["fwhm"].focus_force()
    _settle(d)
    d.crit_vars["fwhm"].set("2.5")
    d.crit_vars["stars"].set("100")
    d.frame_tree.focus_force()                          # the focus leaves the box
    _settle(d)
    _settle(d)
    assert d.channel()["settings"]["limits"] == {"fwhm": {"hi": 2.5}}
    d.crit_vars["eccentricity"].set("0.9")
    d.crit_vars["stars"].set("100")
    d.commit_pending_edits()                                            # what Run does before it acts
    assert d.channel()["settings"]["limits"] == {"fwhm": {"hi": 2.5}, "eccentricity": {"hi": 0.9},
                                                  "stars": {"lo": 100.0}}


def test_an_invalid_limit_is_redrawn_from_the_settings(make) -> None:
    d = make()
    d.crit_vars["fwhm"].set("1,5")
    d.commit_edit("fwhm")
    assert d.channel()["settings"]["limits"] == {}
    assert d.crit_vars["fwhm"].get() == F.format_limit("fwhm", d.channel()["gates"]["fwhm"]["limit"])


def test_unticking_a_criterion_disables_it(make) -> None:
    d = make()
    assert d.channel()["rows"][3]["state"] == F.REJECTED and _kept(d) == 11
    d.crit_on["fwhm"].set(False)
    d._on_gating("fwhm")
    assert d.channel()["settings"]["gating"]["fwhm"] is False
    assert d.crit_edits["fwhm"].instate(["disabled"])
    assert d.channel()["rows"][3]["state"] == F.APPROVED and _kept(d) == 12
    assert d.keep_var.get() == "12 / 12 keep"
    d.crit_on["fwhm"].set(True)
    d._on_gating("fwhm")
    assert not d.crit_edits["fwhm"].instate(["disabled"]) and _kept(d) == 11


def test_switching_a_channel_off_leaves_it_out_and_says_so(make) -> None:
    d = make()
    d.enabled_var.set(False)
    d._on_enabled()
    assert d.channel_tree.item("L", "text").endswith("[off]")
    assert FS.approved_paths(d.state) == [r["path"] for r in d.state["channels"]["R"]["rows"] if r["state"] != F.REJECTED]
    assert d.keep_var.get() == "12 / 12 keep"


def test_space_toggles_the_override_which_is_counted_and_lasts(make, stub_frames) -> None:
    d = make()
    d.select_row(3)                                  # the rejected frame
    d.frame_tree.focus_force()
    _settle(d)
    assert d.channel()["rows"][3]["override"] is None
    assert d.frame_tree.bind("<space>")
    assert d._on_space() == "break"
    row = d.channel()["rows"][3]
    assert row["override"] == F.OVERRIDE["RESCUED"]
    assert "rescued" in d.channel_tree.item("L", "text") and "+1 rescued" in d.channel_tree.item("L", "text")
    assert d.selected_row_index() == 3 and d.keep_var.get() == "12 / 12 keep"
    assert d.row_tooltip_text(3).splitlines()[1].startswith("approved (rescued)")
    # a preset change keeps it
    d.preset_var.set("strict")
    d._on_preset()
    assert d.channel()["rows"][3]["override"] == F.OVERRIDE["RESCUED"]
    d._on_space()                                    # a second press takes it back
    assert d.channel()["rows"][3]["override"] is None
    d.select_row(5)                                  # a kept frame: the override condemns it
    d.toggle_override()
    assert d.channel()["rows"][5]["override"] == F.OVERRIDE["CONDEMNED"]
    assert "-1 condemned" in d.channel_tree.item("L", "text")
    assert F.counts(d.channel()["rows"])["condemned"] == 1
    d.clear_overrides()
    assert F.counts(d.channel()["rows"])["condemned"] == 0


def test_the_override_is_refused_once_the_review_is_locked(make) -> None:
    d = make()
    d.select_row(2)
    d.state["locked"] = True
    d.toggle_override()
    d.clear_overrides()
    d.refresh()
    assert d.channel()["rows"][2]["override"] is None
    assert d.run_button.instate(["disabled"]) and d.preset_combo.instate(["disabled"])


def test_selecting_a_row_loads_the_preview_the_ring_and_the_strip(make, stub_frames) -> None:
    d = make()
    stub_frames["load"].clear()
    d.frame_tree.selection_set("7")                    # as a click does
    _settle(d)
    assert stub_frames["load"] == ["/data/night/L_007.fit"]
    assert d.selected_row_index() == 7
    assert d.plot.selected == 7
    assert d.filmstrip.selected == 7
    assert d.preview.image is not None
    # the thumbnail comes free from the preview render
    assert "/data/night/L_007.fit" in d.loader.thumbs


def test_the_table_rebuild_keeps_the_selected_frame_without_reloading(make, stub_frames) -> None:
    d = make()
    d.select_row(4)
    stub_frames["load"].clear()
    d.refresh()
    assert d.selected_row_index() == 4 and d.frame_tree.selection() == ("4",)
    assert stub_frames["load"] == []


def test_a_click_on_the_plot_selects_the_row(make, stub_frames) -> None:
    d = make()
    d.top.deiconify()
    _settle(d)
    d.plot.paint()
    left, width = d.plot.geom
    d.plot.mouse_press(left + width * 7 / 11)
    _settle(d)
    assert d.selected_row_index() == 7 and d.frame_tree.selection() == ("7",)
    assert d.plot.selected == 7


def test_a_click_on_the_filmstrip_selects_the_row(make, stub_frames) -> None:
    d = make()
    d.filmstrip.on_pick(6)
    assert d.selected_row_index() == 6 and d.frame_tree.selection() == ("6",)


def test_the_plot_follows_its_combo_and_says_the_band_in_words(make) -> None:
    d = make()
    assert d.plot_metric() == "psfSNR"
    d.plot_combo.current(1)
    d.plot_combo.event_generate("<<ComboboxSelected>>")
    _settle(d)
    assert d.plot.metric == "fwhm"
    assert d.band_var.get().startswith("keeps ")
    band = F.accepted_band("fwhm", d.channel()["gates"], d.channel()["settings"])
    assert d.band_var.get().endswith(F.round_display(band["hi"]))


def test_the_strip_metric_combo_sets_the_tile_measurement(make) -> None:
    d = make()
    d.strip_metric.current(3)
    d.strip_metric.event_generate("<<ComboboxSelected>>")
    _settle(d)
    assert d.filmstrip.metric == "stars"


# ---- the preview hand-off and the loader

def test_the_preview_waits_for_the_stretch_when_the_loader_may_load(make, stub_frames) -> None:
    loop = FakeLoop()
    d = make(loop=loop)
    assert stub_frames["stretch"] == [12]            # before the window is shown the first frame is read at once
    d.channel_tree.selection_set("R")
    d.top.deiconify()
    d.top.update()
    d.loader.on_show()
    d.state["channels"]["R"].pop("stretch", None)
    stub_frames["load"].clear()
    d.select_row(2)                                  # the stretch is not measured and the loader may load
    assert d.pending_preview == {"path": "/data/night/R_002.fit", "key": "R"}
    assert stub_frames["load"] == []                 # nothing was read now
    assert d.loader.queue and d.loader.queue[0]["kind"] == "stretch"
    while d.pending_preview is not None and loop.tick():
        pass
    assert stub_frames["load"] == ["/data/night/R_002.fit"] and d.pending_preview is None
    assert d.preview.image is not None


def test_a_waiting_preview_is_dropped_when_another_frame_is_selected(make, stub_frames) -> None:
    loop = FakeLoop()
    d = make(loop=loop)
    d.channel_tree.selection_set("R")
    _settle(d)
    d.top.deiconify()
    d.top.update()
    d.loader.on_show()
    d.state["channels"]["R"].pop("stretch", None)
    d.select_row(2)
    assert d.pending_preview["path"].endswith("R_002.fit")
    d.frame_tree.selection_set("5")                  # the user clicks another frame before the stretch is measured
    _settle(d)
    assert d.selected_row_index() == 5 and d.pending_preview["path"].endswith("R_005.fit")
    stub_frames["load"].clear()
    while d.pending_preview is not None and loop.tick():
        pass
    assert stub_frames["load"] == ["/data/night/R_005.fit"]     # only the frame that is selected is shown


def test_the_preview_is_read_at_once_when_the_stretch_exists(make, stub_frames) -> None:
    loop = FakeLoop()
    d = make(loop=loop)
    d.top.deiconify()
    d.top.update()
    d.loader.on_show()
    stub_frames["load"].clear()
    d.select_row(9)
    assert stub_frames["load"] == ["/data/night/L_009.fit"] and d.pending_preview is None


def test_a_key_is_handled_between_two_loader_ticks(make, stub_frames) -> None:
    loop = FakeLoop()
    d = make(loop=loop)
    d.top.deiconify()
    d.top.update()
    d.loader.on_show()
    d.loader.rebuild_queue()
    d.select_row(5)
    queued = len(d.loader.queue)
    assert queued > 2 and loop.tick()                # one tick: one item
    assert len(d.loader.queue) == queued - 1
    d.frame_tree.focus_force()
    d.top.update()
    d.frame_tree.event_generate("<space>")           # the key arrives between two ticks
    d.top.update()
    assert d.channel()["rows"][5]["override"] == F.OVERRIDE["CONDEMNED"]
    before = len(d.loader.queue)
    assert loop.tick() and len(d.loader.queue) == before - 1


def test_the_loader_does_not_run_before_the_window_is_shown_or_after_it_is_released(make, stub_frames) -> None:
    loop = FakeLoop()
    d = make(loop=loop)
    d.loader.rebuild_queue()
    assert loop.jobs == []
    d.top.deiconify()
    d.top.update()
    d.show()
    assert loop.jobs
    d.release()
    assert loop.jobs == [] and d.loader.released
    d.release()                                       # twice is fine


# ---- the buttons, options and the actions

def test_run_without_the_actions_reports_instead_of_raising(make, monkeypatch) -> None:
    from loomsiril import frames_dialog as FD

    told: list[str] = []
    monkeypatch.setattr(FD, "tell", lambda text, icon="info", **kw: told.append(text))
    if FD._Actions.__module__ == "loomsiril.frames_dialog_actions":
        pytest.skip("the actions module is present: Run would do real work and open real boxes")
    d = make()
    d._on_run()
    assert told and told[0].startswith("The run failed")
    with pytest.raises(NotImplementedError):
        d.commit_copy()
    with pytest.raises(NotImplementedError):
        d.commit_import()


def test_the_run_button_follows_what_there_is_to_do(make) -> None:
    d = make()
    assert not d.run_button.instate(["disabled"])        # a frame to remove
    for key in ("L", "R"):                                # the blur of each channel no longer rejects
        d.channel_tree.selection_set(key)
        _settle(d)
        d.crit_on["fwhm"].set(False)
        d._on_gating("fwhm")
    assert d.run_button.instate(["disabled"])            # nothing to remove, nothing to copy
    assert "the folder the frames are in" in d.dest_var.get() and "deleted" in d.dest_var.get()


def test_choosing_another_folder_makes_it_a_copy(make, monkeypatch, tmp_path) -> None:
    import tkinter.filedialog as fd

    d = make()
    monkeypatch.setattr(fd, "askdirectory", lambda **kw: str(tmp_path / "out"))
    d._on_destination()
    assert d.state["destination"] == str(tmp_path / "out") and d.copying_out()
    assert "emptied first" in d.dest_var.get() and "copied there" in d.dest_var.get()
    assert "nothing is deleted" not in d.dest_var.get()
    assert "approved to copy" in d.summary_var.get()
    assert not d.run_button.instate(["disabled"])
    monkeypatch.setattr(fd, "askdirectory", lambda **kw: "")      # cancelled: unchanged
    d._on_destination()
    assert d.state["destination"] == str(tmp_path / "out")
    d.state["locked"] = True
    monkeypatch.setattr(fd, "askdirectory", lambda **kw: "/elsewhere")
    d.choose_destination()
    assert d.state["destination"] == str(tmp_path / "out")


def test_new_channels_start_from_the_remembered_options(make, tmp_path) -> None:
    store = FO.OptionsStore(str(tmp_path / "cfg"))
    FO.save(store, FO.FrameSelectorOptions(preset="strict", gating={"fwhm": False, "eccentricity": True,
                                                                   "stars": True, "psfSNR": True},
                                           limits={"stars": {"lo": 250.0}}))
    d = make(options_store=store)
    for key in ("L", "R"):
        s = d.state["channels"][key]["settings"]
        assert s["preset"] == "strict" and s["k"] == 2.0
        assert s["gating"]["fwhm"] is False and s["gating"]["psfSNR"] is True
        assert s["limits"] == {"stars": {"lo": 250.0}}
    assert d.preset_var.get() == "strict" and d.crit_vars["stars"].get() == "250"


def test_options_are_saved_on_close(make, tmp_path) -> None:
    cfg = str(tmp_path / "cfg")
    d = make(options_store=FO.OptionsStore(cfg))
    d.preset_var.set("lenient")
    d._on_preset()
    d.crit_vars["stars"].set("120")
    d.commit_edit("stars")
    d.k_var.set("3.5")
    d._on_k()
    d.state["destination"] = "/out"
    d.state["darksFolder"] = "/darks"
    d.state["onlyMasterFlats"] = True
    d.close()
    kept = FO.load(FO.OptionsStore(cfg))
    assert kept.preset == "lenient" and kept.k == 3.5
    assert kept.limits == {"stars": {"lo": 120.0}}
    assert kept.source_folder == "/data/night" and kept.destination_folder == "/out"
    assert kept.darks_folder == "/darks" and kept.only_master_flats is True


def test_the_window_close_box_saves_and_destroys(make, tmp_path) -> None:
    cfg = str(tmp_path / "cfg")
    d = make(options_store=FO.OptionsStore(cfg))
    d.preset_var.set("strict")
    d._on_preset()
    d.close()
    assert FO.load(FO.OptionsStore(cfg)).preset == "strict"
    assert not d.is_visible()
    d.close()                                          # twice is fine


def test_an_empty_review_opens(make) -> None:
    d = make(FS.empty_state("/data/empty"))
    assert d.channel() is None and d.selected_row_index() == -1
    assert d.summary_var.get().startswith("0 frame(s), 0 to remove")
    assert d.run_button.instate(["disabled"])
    d.toggle_override()
    d.clear_overrides()
    d.k_var.set("2")
    d._on_k()


def test_a_channel_that_is_not_comparable_says_why(make) -> None:
    scan = _scan(n=12, filters=("L",))
    scan["channels"]["L"]["problems"] = ["mixed binning"]
    d = make(FS.state_from_scan(FS.empty_state("/data/night"), scan))
    assert d.problems_var.get().startswith("Not comparable: mixed binning")
    assert "not flagged: frames are not comparable" in d.summary_var.get()


def test_k_rejects_non_finite_input(make) -> None:
    d = make()
    for text in ("nan", "inf", "-inf", "NaN"):
        d.k_var.set(text)
        d._on_k()
        assert d.channel()["settings"]["k"] == 2.5 and d.k_var.get() == "2.50"
        assert d.channel()["settings"]["kEdited"] is False


def test_the_override_button_toggles_and_an_override_survives_a_k_change(make, stub_frames) -> None:
    d = make()
    d.select_row(3)
    d.override_button.invoke()
    assert d.channel()["rows"][3]["override"] == F.OVERRIDE["RESCUED"]
    d.k_var.set("0.6")
    d._on_k()
    assert d.channel()["rows"][3]["override"] == F.OVERRIDE["RESCUED"]
    assert F.final_state(d.channel()["rows"][3]["state"], d.channel()["rows"][3]["override"]) == F.APPROVED
    assert "+1 rescued" in d.channel_tree.item("L", "text")
    d.override_button.invoke()
    assert d.channel()["rows"][3]["override"] is None
    d.clear_overrides_button.invoke()


def _placed(d: Any) -> dict[int, Any]:
    d.top.deiconify()
    d.top.update()
    d._paint_marks()
    return {int(m.winfo_y()): m for m in d._marks}


def test_the_red_cell_follows_a_column_resize(make) -> None:
    d = make()
    d.top.deiconify()
    d.top.update()
    d._paint_marks()
    assert len(d._marks) == 1
    d.frame_tree.column("fwhm", width=160)
    d.frame_tree.event_generate("<ButtonRelease-1>", x=1, y=1)     # the end of a header drag
    d.top.update()
    d.top.update()
    box = d.frame_tree.bbox("3", "#2")
    assert box and len(d._marks) == 1
    assert (d._marks[0].winfo_x(), d._marks[0].winfo_width()) == (box[0], box[2])


def test_a_click_on_a_red_cell_selects_the_row_and_gives_the_tree_the_keyboard(make, stub_frames) -> None:
    d = make()
    d.top.deiconify()
    d.top.update()
    d._paint_marks()
    d.select_row(0)
    d.top.update()
    d._marks[0].event_generate("<Button-1>", x=2, y=2)
    d.top.update()
    assert d.selected_row_index() == 3
    assert d.top.focus_get() is d.frame_tree
    d.frame_tree.event_generate("<space>")
    d.top.update()
    assert d.channel()["rows"][3]["override"] == F.OVERRIDE["RESCUED"]


def test_the_wheel_over_a_red_cell_scrolls_the_table_and_the_cell_has_the_row_tip(make) -> None:
    d = make(_state(n=60))
    d.top.deiconify()
    d.top.update()
    d._paint_marks()
    scrolled: list[Any] = []
    d.frame_tree.yview_scroll = lambda *a: scrolled.append(a)
    event = type("E", (), {"num": 5, "delta": 0})()
    assert d._forward_wheel(event) == "break" and scrolled == [(3, "units")]
    d._tip_at("3", type("E", (), {"x_root": 5, "y_root": 5})())
    assert d._tip_row == "3" and d._tip_job is not None
    assert d.row_tooltip_text(3).startswith("/data/night/L_003.fit")


def test_the_destination_sentence_warns_when_the_run_deletes_in_place(make, monkeypatch, tmp_path) -> None:
    import tkinter.filedialog as fd

    d = make()
    assert str(d.dest_label.cget("foreground")) == "#b02222"
    assert str(d.dest_label.cget("font")) == str(d._bold)
    monkeypatch.setattr(fd, "askdirectory", lambda **kw: str(tmp_path / "out"))
    d._on_destination()
    assert str(d.dest_label.cget("foreground")) != "#b02222"


def test_the_criteria_are_disabled_while_the_review_is_locked(make) -> None:
    d = make()
    d.state["locked"] = True
    d.refresh()
    assert all(c.instate(["disabled"]) for c in d.crit_checks.values())
    assert all(e.instate(["disabled"]) for e in d.crit_edits.values())


def test_only_a_missing_actions_module_is_tolerated(monkeypatch) -> None:
    import builtins
    import sys

    import loomsiril.frames_dialog as FD

    # Both swaps are undone by monkeypatch, so the real module stays in sys.modules for the tests that follow.
    monkeypatch.delitem(sys.modules, "loomsiril.frames_dialog_actions", raising=False)
    src = open(FD.__file__, encoding="utf-8").read()
    head = src[src.index("try:  # the run"):src.index("log = logging")]
    ns: dict[str, Any] = {"__name__": "x"}
    raised = [ModuleNotFoundError("No module named 'numpy_missing'", name="numpy_missing")]
    orig = builtins.__import__

    def guarded(name: str, *a: Any, **k: Any) -> Any:
        if name == "loomsiril.frames_dialog_actions":
            raise raised[0]
        return orig(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(ModuleNotFoundError):
        exec(head, ns)
    raised[0] = ModuleNotFoundError("x", name="loomsiril.frames_dialog_actions")
    exec(head, ns)
    assert "_Actions" in ns
