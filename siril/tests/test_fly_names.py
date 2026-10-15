"""The Fly-Through dialog's online name lookup, on recording tk stand-ins (no window, no network): the lookup runs on a
worker thread, fills an EMPTY Object name and never a typed one, refuses a hit outside the solved field with the
reason, and is silent offline. fetch_text is always a stub."""

from __future__ import annotations

import threading

import pytest

from loomsiril import skyname
from test_siril_script import _fake_tkinter, blocked_discover, script, stub  # noqa: F401
from test_skyname import FAR, FIELD, SESAME

GREY_FOUND = "Found: Sh2-132 (from the file name via CDS Sesame)"


@pytest.fixture
def sesame(monkeypatch: pytest.MonkeyPatch):
    """A recording fetch stand-in: Sesame's answer for Sh2-132, 'no object' for the rest; ``threads`` are the callers'."""
    state = {"asked": [], "threads": [], "offline": False}
    monkeypatch.setattr(skyname, "resolved_names", {})

    def fetch(url: str, what: str = "Name lookup"):
        state["threads"].append(threading.current_thread())
        state["asked"].append(url.split("?", 1)[1])
        if state["offline"]:
            return None
        return SESAME if "sh2-132" in url.lower() else "#! no object\n"

    monkeypatch.setattr(skyname, "fetch_text", fetch)
    return state


def _dialog(script, monkeypatch, blocked, names, target=None, **options):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options(options), blocked, target, None, names)
    return dlg


def _finish(dlg, *kinds: str) -> None:
    """Lets the named worker jobs end and delivers their answers, as Tk's loop would."""
    for job in list(dlg._jobs):
        if job["thread"].name in [f"loomsiril-{k}" for k in kinds]:
            job["thread"].join(5)
            for key, (_ms, fn) in list(dlg.top.afters.items()):
                if fn is job["poll"]:
                    del dlg.top.afters[key]
            job["poll"]()


def _note(dlg) -> str:
    return dlg._name_note.get()


def test_opening_with_a_named_file_asks_a_worker_and_fills_an_empty_object_name(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, {"ra": 334.0, "dec": 56.0},
                  image_path="/d/Sh2-132 - Lion_Nebula.tif")
    _finish(dlg, "names")
    assert sesame["asked"][0] == "Sh2-132"
    assert all(t is not threading.current_thread() for t in sesame["threads"])      # off the calling (Tk) thread
    assert dlg._vars["object_name"].get() == "Sh2-132"
    assert _note(dlg) == GREY_FOUND
    # the found object is the target (its distance is looked up round it, as in Loom): RA and Dec were the header's
    assert (dlg._vars["ra"].get(), dlg._vars["dec"].get()) == ("334.7875", "56.0791")


def test_the_dialog_does_not_wait_for_the_network(stub, script, monkeypatch, blocked_discover):
    """Construction returns at once while the resolver is still busy; the answer is applied later."""
    gate = threading.Event()
    monkeypatch.setattr(skyname, "resolved_names", {})
    monkeypatch.setattr(skyname, "fetch_text", lambda url, what="x": gate.wait(10) and SESAME)
    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None, image_path="/d/Sh2-132.tif")
    assert dlg._vars["object_name"].get() == "" and _note(dlg) == ""
    gate.set()
    _finish(dlg, "names")
    assert dlg._vars["object_name"].get() == "Sh2-132"


def test_a_typed_object_name_is_never_overwritten(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, {"ra": 1.0, "dec": 2.0},
                  image_path="/d/Sh2-132.tif", object_name="My target")
    _finish(dlg, "names")
    assert dlg._vars["object_name"].get() == "My target"
    assert (dlg._vars["ra"].get(), dlg._vars["dec"].get()) == ("1.0", "2.0")        # the target stays too
    assert _note(dlg) == GREY_FOUND                                                  # but the finding is shown


def test_a_hit_outside_the_solved_field_is_not_used(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FAR)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None, image_path="/d/Sh2-132.tif")
    _finish(dlg, "names")
    assert dlg._vars["object_name"].get() == "" and _note(dlg) == ""


def test_offline_is_silent_with_a_grey_note_and_the_box_stays_empty(stub, script, monkeypatch, blocked_discover, sesame):
    sesame["offline"] = True
    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None, image_path="/d/Sh2-132.tif")
    _finish(dlg, "names")
    assert dlg._vars["object_name"].get() == "" and _note(dlg) == "Name lookup needs the network"
    assert len(sesame["asked"]) == 1


def test_a_picked_file_asks_again_and_the_session_remembers_answers(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None)
    assert not dlg._jobs or all(j["thread"].name != "loomsiril-names" for j in dlg._jobs)   # nothing to ask yet
    dlg._vars["image_path"].set("/d/Sh2-132 - Lion_Nebula.tif")                                # Browse... or typing
    assert "names" in dlg._afters                                                              # debounced, not at once
    dlg._cancel_after("names")
    dlg._start_names()
    _finish(dlg, "names")
    assert dlg._vars["object_name"].get() == "Sh2-132"
    first = len(sesame["asked"])
    dlg._vars["object_name"].set("")
    dlg._named_for = None
    dlg._start_names()
    _finish(dlg, "names")
    assert len(sesame["asked"]) == first and dlg._vars["object_name"].get() == "Sh2-132"     # answered from the cache


def test_the_header_object_and_the_open_image_are_asked_too(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FIELD, header_object="Sh2-132", open_path="/d/light.fit")
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None)
    _finish(dlg, "names")
    assert dlg._vars["object_name"].get() == "Sh2-132"
    assert _note(dlg) == "Found: Sh2-132 (from the image header's OBJECT via CDS Sesame)"


def test_an_old_answer_is_dropped_when_the_file_changed_meanwhile(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None, image_path="/d/Sh2-132.tif")
    dlg._vars["image_path"].set("/d/Other Thing.tif")
    dlg._cancel_after("names")
    dlg._start_names()
    _finish(dlg, "names")
    assert dlg._vars["object_name"].get() == ""


def test_no_name_context_no_lookup(stub, script, monkeypatch, blocked_discover, sesame):
    dlg = _dialog(script, monkeypatch, blocked_discover, None, None, image_path="/d/Sh2-132.tif")
    dlg._vars["image_path"].set("/d/Sh2-132 - x.tif")
    assert "names" not in dlg._afters and sesame["asked"] == []
    dlg._vars["object_name"].set("Sh2-132")
    dlg._find_typed()
    assert _note(dlg) == "Name lookup needs a plate-solved image" and sesame["asked"] == []


# ---------------------------------------------------------------- the Object box: Enter or Find

def test_find_resolves_a_typed_name_inside_the_field_and_fills_the_target(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, {"ra": 1.0, "dec": 2.0})
    dlg._vars["object_name"].set("Sh2-132")
    dlg._find_typed()
    assert _note(dlg) == "Looking up Sh2-132..."
    _finish(dlg, "find")
    assert _note(dlg) == "Found: Sh2-132 (from the Object box via CDS Sesame)"
    assert dlg._vars["object_name"].get() == "Sh2-132"
    assert (dlg._vars["ra"].get(), dlg._vars["dec"].get()) == ("334.7875", "56.0791")
    assert all(t is not threading.current_thread() for t in sesame["threads"])


def test_enter_in_the_object_box_does_the_same(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None)
    box = [w for w in dlg._frame.children if w.kind == "Entry" and getattr(w, "bindings", {}).get("<Return>")]
    assert len(box) == 1
    dlg._vars["object_name"].set("Sh2-132")
    box[0].bindings["<Return>"][0](None)
    _finish(dlg, "find")
    assert _note(dlg).startswith("Found: Sh2-132")


def test_find_refuses_a_name_outside_the_field_with_the_reason(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FAR)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, {"ra": 1.0, "dec": 2.0})
    dlg._vars["object_name"].set("Sh2-132")
    dlg._find_typed()
    _finish(dlg, "find")
    assert _note(dlg).startswith("Sh2-132 is not in this image, ") and "not used as the target" in _note(dlg)
    assert (dlg._vars["ra"].get(), dlg._vars["dec"].get()) == ("1.0", "2.0")


def test_find_unknown_and_offline(stub, script, monkeypatch, blocked_discover, sesame):
    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None)
    dlg._vars["object_name"].set("Nothing Here")
    dlg._find_typed()
    _finish(dlg, "find")
    assert _note(dlg) == "Nothing Here: not found"
    sesame["offline"] = True
    dlg._vars["object_name"].set("Another Name")
    dlg._find_typed()
    _finish(dlg, "find")
    assert _note(dlg) == "Name lookup needs the network"
    dlg._vars["object_name"].set("")
    dlg._find_typed()
    assert _note(dlg) == ""


def test_the_object_name_reaches_the_video_name(stub, script, monkeypatch, blocked_discover, sesame):
    from loomsiril import encode

    names = skyname.NameContext(field=FIELD)
    dlg = _dialog(script, monkeypatch, blocked_discover, names, None, image_path="/d/Sh2-132.tif", out_dir="/w")
    _finish(dlg, "names")
    from loomsiril import presets

    values = dlg.values(presets)
    assert values["object_name"] == "Sh2-132"
    assert encode.video_name(values["object_name"], "youtube_1080", "sdr") == "Sh2-132_youtube_1080_SDR"


# ---------------------------------------------------------------- main

def test_main_hands_the_dialog_the_field_the_header_object_and_the_file(stub, script, tmp_path, monkeypatch):
    from test_siril_script import _field_and_header, _load_siril_image

    field, pixels, header_fits, _ = _field_and_header()
    header_fits["OBJECT"] = "'Sh2-132'"
    _load_siril_image(stub, pixels, header_fits)
    stub.STATE.filename = "/data/Sh2-132 - Lion.fit"
    seen = {}

    def dialog(options, discover, target, siril=None, **kw):
        seen.update(kw)
        return None

    script.main(dialog=dialog)
    n = seen["names"]
    assert n.header_object == "Sh2-132" and n.open_path == "/data/Sh2-132 - Lion.fit"
    assert n.field["radius_deg"] > 0 and set(n.field["centre"]) == {"ra", "dec"}
