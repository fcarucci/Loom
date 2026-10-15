"""The Fly-Through dialog remembers its file boxes (Image, Starless, Stars), the mode and the Browse... folder with the
other options: save/load, the defaults, a missing file kept and reported, autosave off the Tk thread, the final save on
close. Recording tk stand-ins; tiny files; no window."""

from __future__ import annotations

import threading

import pytest

from loomsiril import siril_bridge as sb
from test_skyname import FIELD
from test_siril_script import _dialog, fake_pipeline, _fake_tkinter, _settle, _tools, blocked_discover, script, stub  # noqa: F401


def test_the_three_paths_the_mode_and_the_folder_round_trip(tmp_path):
    values = {"image_path": "/a/m31.tif", "starless_path": "/a/less.tif", "stars_path": "/a/stars.tif",
              "mode": "files", "last_folder": "/a", "fps": 30}
    sb.save_options(values, tmp_path)
    assert sb.load_options(tmp_path) == values


def test_an_empty_box_is_not_saved_and_clears_an_older_path(tmp_path):
    sb.save_options({"image_path": "/a/m31.tif", "stars_path": "/a/s.tif", "fps": 24}, tmp_path)
    sb.save_options({"image_path": "  ", "stars_path": "", "fps": 24}, tmp_path)
    assert sb.load_options(tmp_path) == {"fps": 24}
    assert "image_path" not in sb.options_path(tmp_path).read_text()


def test_a_partial_save_keeps_the_other_options(tmp_path):
    sb.save_options({"image_path": "/a/m31.tif", "fps": 24}, tmp_path)
    sb.save_options({"fps": 60}, tmp_path)
    assert sb.load_options(tmp_path) == {"image_path": "/a/m31.tif", "fps": 60}


def test_a_path_that_is_not_text_is_not_loaded(tmp_path):
    sb.options_path(tmp_path).write_text('{"image_path": 5, "fps": 30}')
    assert sb.load_options(tmp_path) == {"fps": 30}


def test_saved_paths_reach_the_dialog_boxes_and_the_mode(stub, script, monkeypatch, blocked_discover, tmp_path):
    sb.save_options({"image_path": "/x/a.tif", "starless_path": "/x/b.tif", "stars_path": "/x/c.tif",
                     "mode": "files", "last_folder": "/x"}, tmp_path)
    dlg, _ = _dialog(script, monkeypatch, blocked_discover, **sb.load_options(tmp_path))
    assert [dlg._vars[k].get() for k in ("image_path", "starless_path", "stars_path")] == ["/x/a.tif", "/x/b.tif", "/x/c.tif"]
    assert dlg._last_dir == "/x"
    dlg._on_discovered(_tools("starnet2"))
    assert dlg._mode.get() == "files"                       # the remembered mode wins over "extract when a tool exists"


def test_a_remembered_extract_mode_comes_back(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover, mode="extract")
    dlg._on_discovered(_tools("starnet2"))
    assert dlg._mode.get() == "extract"


def test_a_remembered_file_that_is_gone_stays_in_the_box_and_blocks_render_and_draft(stub, script, monkeypatch,
                                                                                    blocked_discover, tmp_path):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    gone = str(tmp_path / "gone.tif")
    options = {"starless_path": gone, "stars_path": gone, "mode": "files", "out_dir": str(tmp_path)}
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options(options), blocked_discover, None,
                               lambda w, v: None)
    _settle(dlg)
    assert dlg._vars["starless_path"].get() == gone         # kept, not dropped
    assert "does not exist or cannot be read" in dlg._reason.get()
    assert dlg._render_btn.kw["state"] == "disabled" and dlg._draft_btn.kw["state"] == "disabled"
    (tmp_path / "gone.tif").write_text("x")                 # fixed: the reason goes
    dlg._recheck()
    _settle(dlg)
    assert dlg._reason.get() == "" and dlg._render_btn.kw["state"] == "normal"


def test_a_missing_remembered_image_file_blocks_extract_mode(stub, script, monkeypatch, blocked_discover, tmp_path):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover, image_path=str(tmp_path / "gone.fits"), mode="extract",
                     out_dir=str(tmp_path))
    dlg._on_discovered(_tools("starnet2"))
    _settle(dlg)
    assert "does not exist or cannot be read" in dlg._reason.get() and dlg._render_btn.kw["state"] == "disabled"


def test_a_restored_image_file_starts_the_name_and_distance_lookups_like_a_chosen_one(stub, script, monkeypatch,
                                                                                    blocked_discover, tmp_path):
    from loomsiril import skyname

    asked = []
    monkeypatch.setattr(skyname, "resolved_names", {})
    monkeypatch.setattr(skyname, "fetch_text", lambda url, what="x": asked.append(url) or "#! no object\n")
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    names = skyname.NameContext(field=FIELD)
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({"image_path": "/x/Sh2-132.tif",
                                                                           "mode": "extract"}),
                               blocked_discover, None, None, names)
    assert dlg._named_for is not None and dlg._named_for[0]   # the lookup was started on open for the restored name
    for job in list(dlg._jobs):
        if job["thread"].name == "loomsiril-names":
            job["thread"].join(5)
    assert asked, "the worker asked Sesame about the restored file name"


def _persisting(script, monkeypatch, blocked, **options):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    saved: list = []
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options(options), blocked, None, None, None,
                               lambda opts: saved.append((threading.current_thread(), dict(opts))))
    return dlg, saved


def _fire_save(dlg) -> None:
    """The 500 ms save timer 'fires' (it leaves Tk's queue) and its worker thread is joined."""
    for key, (ms, fn) in list(dlg.top.afters.items()):
        if "fire" in repr(fn):
            del dlg.top.afters[key]
            fn()
    for t in threading.enumerate():
        if t.name == "loomfly-save":
            t.join(5)


def test_autosave_runs_off_the_tk_thread_and_keeps_a_missing_file(stub, script, monkeypatch, blocked_discover, tmp_path):
    dlg, saved = _persisting(script, monkeypatch, blocked_discover, out_dir=str(tmp_path))
    dlg._vars["image_path"].set(str(tmp_path / "gone.tif"))
    dlg._vars["starless_path"].set("")
    assert saved == []                                      # debounced: nothing written on the keystroke
    _fire_save(dlg)
    assert len(saved) == 1 and saved[0][0] is not threading.current_thread()
    assert saved[0][1]["image_path"] == str(tmp_path / "gone.tif") and saved[0][1]["starless_path"] == ""
    assert saved[0][1]["mode"] == "files"


def test_several_changes_make_one_pending_autosave(stub, script, monkeypatch, blocked_discover):
    dlg, _saved = _persisting(script, monkeypatch, blocked_discover)
    for text in "abc":
        dlg._vars["image_path"].set(text)
    assert sum("fire" in repr(fn) for _ms, fn in dlg.top.afters.values()) == 1


def test_closing_saves_synchronously_and_wins_over_a_late_autosave(stub, script, monkeypatch, blocked_discover, tmp_path):
    saved: list = []
    release = threading.Event()

    def slow_persist(opts):
        if opts["image_path"] == "first":
            release.wait(5)                                 # the older autosave is slow to reach the disk
        saved.append(opts["image_path"])

    _fake_tkinter(monkeypatch)
    import tkinter as tk

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({"out_dir": str(tmp_path)}), blocked_discover,
                               None, None, None, slow_persist)
    dlg._vars["image_path"].set("first")
    for key, (_ms, fn) in list(dlg.top.afters.items()):
        if "fire" in repr(fn):
            del dlg.top.afters[key]
            fn()                                            # autosave of "first" starts and waits
    dlg._vars["image_path"].set("last")
    threading.Timer(0.2, release.set).start()
    dlg._cancel()                                           # the final save: waits for the lock, then writes "last"
    for t in threading.enumerate():
        if t.name == "loomfly-save":
            t.join(5)
    assert saved[-1] == "last"


def test_the_final_save_on_cancel_is_written_even_without_a_pending_autosave(stub, script, monkeypatch, blocked_discover):
    dlg, saved = _persisting(script, monkeypatch, blocked_discover)
    dlg._vars["stars_path"].set("/x/stars.tif")
    dlg._cancel()
    assert saved and saved[-1][1]["stars_path"] == "/x/stars.tif" and dlg.top.afters == {}


def test_browse_remembers_the_last_folder(stub, script, monkeypatch, blocked_discover):
    dlg, saved = _persisting(script, monkeypatch, blocked_discover)
    dlg._last_dir = "/pics"
    dlg._cancel()
    assert saved[-1][1]["last_folder"] == "/pics"


def test_main_does_not_blank_a_remembered_path_when_it_saves_the_chosen_values(stub, script, fake_pipeline, tmp_path,
                                                                             monkeypatch):
    """The dialog returns image_path "" in files mode; main's own save must leave the remembered Image file alone."""
    from test_siril_script import _field_and_header, _load_siril_image, _values, _write_layers

    monkeypatch.setenv("LOOMSIRIL_STARNET", str(tmp_path / "absent"))
    monkeypatch.setenv("PATH", str(tmp_path))
    field, pixels, header_fits, _top = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    starless, stars = _write_layers(tmp_path, field)
    sb.save_options({"image_path": "/x/remembered.tif", "last_folder": "/x"}, stub.STATE.configdir)
    seen = {}

    def dialog(options, discover, target, siril=None, **kw):
        seen["options"] = options
        return _values(tmp_path, starless_path=starless, stars_path=stars)

    script.main(dialog=dialog)
    assert seen["options"]["image_path"] == "/x/remembered.tif"          # main hands the saved paths to the dialog
    saved = sb.load_options(stub.STATE.configdir)
    assert saved["image_path"] == "/x/remembered.tif" and saved["last_folder"] == "/x"


def test_the_draft_cache_key_still_reflects_the_file(tmp_path):
    from loomsiril import draft

    a = tmp_path / "less.tif"
    b = tmp_path / "stars.tif"
    a.write_bytes(b"1")
    b.write_bytes(b"2")
    opts = {"mode": "files", "starless_path": str(a), "stars_path": str(b), "image_key": "k"}
    before = draft.input_identity(opts)
    a.write_bytes(b"123")                                   # the file changed since it was remembered
    assert draft.input_identity(opts) != before
    assert draft.input_identity({**opts, "starless_path": str(b)}) != draft.input_identity(opts)
