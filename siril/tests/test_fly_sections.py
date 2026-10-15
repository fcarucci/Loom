"""The Fly-Through dialog's container: the scrolling form, the fixed footer, the collapsible Advanced sections, the
section API (rows, controls, greying, saving) and the window-height rule. Recording tk stand-ins: no window, and not run
inside Siril."""

from __future__ import annotations

import json

import pytest

from loomsiril import fly_dialog, fly_model
from loomsiril import siril_bridge as sb
from test_siril_script import (_dialog, _fake_tkinter, _settle, blocked_discover, script, stub)  # noqa: F401

IDS = ["video", "logo", "motion", "hdr"]


def section(dlg, sid):
    return dlg._sections[sid]


def header(dlg, sid):
    return section(dlg, sid).header


def descendants(w):
    for c in w.children:
        yield c
        yield from descendants(c)


def test_the_sections_exist_in_order_with_their_titles_and_start_collapsed(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    assert list(dlg._sections) == IDS
    assert [section(dlg, s).title for s in IDS] == ["Video details", "Logo", "Stars and motion", "HDR"]
    assert [header(dlg, s).kw["text"] for s in IDS] == ["▸ Video details", "▸ Logo", "▸ Stars and motion",
                                                       "▸ HDR"]
    assert all(section(dlg, s).frame.grid_kw is None and not section(dlg, s).open for s in IDS)
    rows = [header(dlg, s).grid_kw["row"] for s in IDS]
    assert rows == sorted(rows) and len(set(rows)) == 4
    out_row = max(w.grid_kw["row"] for w in descendants(dlg._frame) if w.grid_kw and w.kind == "Checkbutton"
                  and w.kw.get("text") == "Fade in/out")
    assert rows[0] > out_row                                  # after Output


def test_clicking_the_header_shows_and_hides_the_frame_and_flips_the_triangle(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    h = header(dlg, "logo")
    h.invoke()
    assert section(dlg, "logo").frame.grid_kw["row"] == header(dlg, "logo").grid_kw["row"] + 1
    assert h.kw["text"] == "▾ Logo" and section(dlg, "logo").open
    h.invoke()
    assert section(dlg, "logo").frame.grid_kw is None and h.kw["text"] == "▸ Logo"


def test_the_header_is_keyboard_accessible(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    h = header(dlg, "hdr")
    assert h.kind == "Button"                                 # a ttk button takes Tab focus and Space; Enter is bound
    assert h.bindings["<Return>"][0](None) == "break"
    assert section(dlg, "hdr").open


def test_the_open_sections_are_saved_and_restored(stub, script, monkeypatch, blocked_discover, tmp_path):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    assert dlg._snapshot()["sections"] == []
    header(dlg, "motion").invoke()
    header(dlg, "video").invoke()
    assert dlg._snapshot()["sections"] == ["video", "motion"]          # in the form's order
    sb.save_options(dlg._snapshot(), tmp_path)
    saved = json.loads(sb.options_path(tmp_path).read_text())
    assert saved["sections"] == ["video", "motion"]
    again, _ = _dialog(script, monkeypatch, blocked_discover, **sb.load_options(tmp_path))
    assert [s for s in IDS if section(again, s).open] == ["video", "motion"]
    assert section(again, "video").frame.grid_kw is not None and section(again, "logo").frame.grid_kw is None
    assert again._sections["video"].header.kw["text"].startswith("▾")


def test_a_bad_saved_sections_value_is_ignored(stub, script, monkeypatch, blocked_discover, tmp_path):
    sb.options_path(tmp_path).write_text(json.dumps({"sections": "video", "duration": 4}))
    assert "sections" not in sb.load_options(tmp_path)
    sb.options_path(tmp_path).write_text(json.dumps({"sections": ["nope", "hdr"]}))
    dlg, _ = _dialog(script, monkeypatch, blocked_discover, **sb.load_options(tmp_path))
    assert [s for s in IDS if section(dlg, s).open] == ["hdr"]       # an unknown id is dropped


def test_opening_a_section_schedules_a_save(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    saved = []
    dlg._persist = saved.append
    dlg._toggle_section("logo")
    assert "save" in dlg._afters                              # the autosave is armed (it writes sections too)


def test_the_button_bar_and_the_reason_line_are_outside_the_scroll_area(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    scroll_widgets = set(map(id, descendants(dlg._scroll.outer)))
    assert dlg.top.children == [dlg._scroll.outer, dlg._footer]
    assert dlg._footer.grid_kw["row"] == 1 and dlg._scroll.outer.grid_kw["row"] == 0
    for button in (dlg._render_btn, dlg._draft_btn):
        if button is not None:
            assert id(button) not in scroll_widgets
    assert id(dlg._render_btn) in set(map(id, descendants(dlg._footer)))
    labels = [w for w in descendants(dlg._footer) if w.kind == "Label"]
    assert any(w.kw.get("textvariable") is dlg._reason for w in labels)
    assert dlg._frame is dlg._scroll.inner and dlg._scroll.inner.master is dlg._scroll.canvas


def test_the_scroll_area_binds_the_wheel_for_every_platform_and_has_a_scrollbar(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    bound = dlg._scroll.canvas.all_bindings
    assert {"<MouseWheel>", "<Button-4>", "<Button-5>"} <= set(bound)
    assert [w.kind for w in dlg._scroll.outer.children] == ["Canvas", "Scrollbar"]
    assert dlg._scroll.canvas.kw["yscrollcommand"] == dlg._scroll.bar.set


def test_wheel_units_are_those_of_the_loom_dialog_and_x11_buttons_scroll_one_line(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    area = dlg._scroll
    moved = []
    area.canvas.yview_scroll = lambda n, unit: moved.append((n, unit))
    area.canvas.winfo_containing = lambda x, y: area.inner               # the pointer is over the form
    area._on_wheel(type("E", (), {"x_root": 1, "y_root": 1, "num": 4, "delta": 0})())
    area._on_wheel(type("E", (), {"x_root": 1, "y_root": 1, "num": 5, "delta": 0})())
    area._on_wheel(type("E", (), {"x_root": 1, "y_root": 1, "num": 0, "delta": -120})())
    from loomsiril.loom.dialog import wheel_units

    assert moved == [(-1, "units"), (1, "units"), (wheel_units(-120), "units")]
    moved.clear()
    area.canvas.winfo_containing = lambda x, y: object()                 # over another window: not ours
    area._on_wheel(type("E", (), {"x_root": 1, "y_root": 1, "num": 4, "delta": 0})())
    assert moved == []


@pytest.mark.parametrize("content, screen, want", [(500, 1000, 500), (900, 1000, 850), (850, 1000, 850), (2000, 800, 680),
                                                   (700, 0, 700), (100, 100, 85)])
def test_window_height_is_the_content_at_most_85_percent_of_the_screen(content, screen, want):
    assert fly_dialog.window_height(content, screen) == want


def test_the_form_is_fitted_to_the_window_height(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    canvas = dlg._scroll.canvas
    dlg._scroll.inner.req_height = 3000                       # the form asks for more than the screen (1000 high)
    dlg._fit()
    footer = dlg._footer.winfo_reqheight()
    assert canvas.kw["height"] == int(1000 * 0.85) - footer
    dlg._scroll.inner.req_height = 400
    dlg._fit()
    assert canvas.kw["height"] == 400                          # a short form is not stretched


# ---- the section API --------------------------------------------------------------------------------------------------
# A test section is added to the finished dialog with the public helpers, so these tests do not depend on what the
# other sections hold.

def _with_rows(dlg):
    """A new section "t" whose rows are made with the helpers, greyed with the ``music`` group."""
    frame = dlg._section("t", "Test")
    dlg._check_row(frame, "t_flag", "Flag", True, name="music")
    dlg._text_row(frame, "t_text", "Text", "abc ", name="music")
    dlg._choice_row(frame, "t_pick", "Pick", "b", ["a", "b"], name="music")

    def number():
        try:
            return float(dlg._ctl["t_num"][0].get())
        except ValueError:
            raise ValueError("Number must be a number.")

    dlg._text_row(frame, "t_num", "Number", "5", name="music", getter=number)
    dlg._sync()
    return frame


def test_rows_register_their_controls_values_and_greying(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    frame = _with_rows(dlg)
    from loomsiril import presets

    values = dlg.values(presets)
    assert (values["t_flag"], values["t_text"], values["t_pick"], values["t_num"]) == (True, "abc", "b", 5.0)
    assert {w.kw.get("text") for w in frame.children if w.kind == "Label"} == {"Text", "Pick", "Number"}
    assert [w.grid_kw["row"] for w in frame.children if w.kind == "Label"] == [1, 2, 3]       # the frame's own rows
    assert {w.kind for w in frame.children} >= {"Checkbutton", "Entry", "Combobox"}
    # greying: the "music" group applies with Encode video ticked, so all is usable; unticked greys all and says why
    assert section(dlg, "t").reason.get() == ""
    dlg._video.set(False)
    assert section(dlg, "t").reason.get() == "needs Encode video"
    entry = next(w for w in frame.children if w.kind == "Entry")
    assert entry.kw["state"] == "disabled"
    combo = next(w for w in frame.children if w.kind == "Combobox")
    dlg._video.set(True)
    assert combo.kw["state"] == "readonly" and entry.kw["state"] == "normal"
    assert section(dlg, "t").reason.get() == ""


def test_one_row_without_a_group_keeps_the_header_reason_empty(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    frame = dlg._section("t", "Test")
    dlg._check_row(frame, "t_a", "A", True, name="music")
    dlg._check_row(frame, "t_b", "B", False)                            # always usable
    dlg._video.set(False)
    assert section(dlg, "t").reason.get() == ""


def test_a_bad_entry_raises_with_its_message_but_the_autosave_skips_only_that_key(stub, script, monkeypatch,
                                                                                 blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    _with_rows(dlg)
    from loomsiril import presets

    dlg._ctl["t_num"][0].set("x")
    with pytest.raises(ValueError, match="Number must be a number"):
        dlg.values(presets)
    snap = dlg._snapshot()
    assert "t_num" not in snap and snap["t_text"] == "abc" and snap["sections"] == []


def test_a_control_change_arms_the_autosave_and_a_rules_control_reaches_the_model(stub, script, monkeypatch,
                                                                                  blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    frame = dlg._section("t", "Test")
    dlg._check_row(frame, "t_on", "On", False, rules=True)
    dlg._check_row(frame, "t_off", "Off", False)
    dlg._persist = lambda options: None
    dlg._cancel_after("save")
    dlg._ctl["t_off"][0].set(True)
    assert "save" in dlg._afters and dlg._model().controls["t_on"] is False and "t_off" not in dlg._model().controls
    dlg._ctl["t_on"][0].set(True)
    assert dlg._model().controls["t_on"] is True
    with pytest.raises(ValueError):
        dlg._control("t_on", dlg._ctl["t_on"][0])                       # a key is registered once


def test_every_group_the_model_knows_has_a_reason_when_it_is_greyed():
    for group in fly_model.GROUPS:
        for kw in ({}, {"ffmpeg": False}, {"video": False}, {"mode": "extract", "tools": "none"},
                   {"presets": ("exhibition",)}):
            m = fly_model.FlyDialogModel(**kw)
            assert bool(m.disabled_reason(group)) == (not m.enabled(group))
    with pytest.raises(KeyError):
        fly_model.FlyDialogModel().enabled("nope")


def test_sections_is_an_option_key_and_a_dialog_default():
    assert "sections" in sb.OPTION_KEYS and "sections" not in sb.PATH_KEYS
    assert fly_dialog.DialogValues().sections is None
    assert fly_dialog.DialogValues.from_options({"sections": ["hdr"]}).sections == ["hdr"]
