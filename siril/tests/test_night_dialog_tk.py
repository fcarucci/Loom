"""The night picker's rows and window (loomsiril.night_dialog_tk): the tree rows as plain data, the detail text, and the
picker's selection, OK and Cancel on a real Tk root.

The pure tests run anywhere. The Tk tests create widgets under a hidden root and skip cleanly where tkinter cannot be
imported or ``tkinter.Tk()`` raises TclError (no display).
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from loomsiril import night_dialog as D
from loomsiril import night_dialog_tk as T


def _night(target: str, date: str, session: int, count: int, filters: list[str], first: str, last: str) -> dict[str, Any]:
    """A night as ``survey_of`` makes it: its frames carry the stamps the clock and the span are read from."""
    return {
        "target": target,
        "date": date,
        "sessionIndex": session,
        "count": count,
        "filters": filters,
        "frames": [{"stamp": first}, {"stamp": last}],
    }


def _survey() -> dict[str, Any]:
    """Two targets. M42 has four nights (the oldest is not shown) and its newest night has a filter with no flat. IC 1396A
    has one night, with no flats at all."""
    nights = [
        _night("M42", "2026-08-08", 0, 4, ["L"], "20260808T201500", "20260808T213000"),
        _night("M42", "2026-08-09", 1, 4, ["L"], "20260809T201500", "20260809T213000"),
        _night("M42", "2026-08-10", 2, 4, ["L", "Ha"], "20260810T201500", "20260810T213000"),
        _night("M42", "2026-08-11", 3, 4, ["L", "Ha"], "20260811T201500", "20260811T213000"),
        _night("IC 1396A", "2026-08-07", 4, 2, ["Ha"], "20260807T223000", "20260807T233000"),
    ]
    flats = {"0": [{"filter": "L", "stamp": "20260808T190000"}],
             "1": [{"filter": "L", "stamp": "20260809T190000"}],
             "2": [{"filter": "L", "stamp": "20260810T190000"}, {"filter": "L", "stamp": "20260810T190100"}],
             "3": [{"filter": "L", "stamp": "20260811T190000"}, {"filter": "Ha", "stamp": "20260811T190100"}]}
    return {"sessions": [], "nights": nights, "targets": ["M42", "IC 1396A"], "flatsBySession": flats,
            "unparseable": []}


def _rows() -> dict[str, dict[str, Any]]:
    return {r["iid"]: r for r in T.tree_rows(_survey())}


# ---- pure: the rows the tree shows ----------------------------------------------------------------------------------

def test_rows_keep_target_order_and_each_night_under_its_target() -> None:
    rows = T.tree_rows(_survey())
    assert [r["iid"] for r in rows] == ["t0", "t0n0", "t0n1", "t0n2", "t1", "t1n0"]
    assert [r["parent"] for r in rows] == ["", "t0", "t0", "t0", "", "t1"]
    assert [r["kind"] for r in rows] == ["target", "night", "night", "night", "target", "night"]


def test_target_row_totals_its_recent_nights_and_has_no_night() -> None:
    m42 = _rows()["t0"]
    assert (m42["text"], m42["frames"], m42["filters"], m42["flats"]) == ("M42", "12", "", "")
    assert m42["night"] is None and m42["missing"] is False


def test_night_row_texts_are_date_span_count_filters_and_flat_count() -> None:
    night = _rows()["t0n0"]
    assert night["text"] == "2026-08-11  20:15-21:30"
    assert (night["frames"], night["filters"], night["flats"]) == ("4", "L Ha", "2")


def test_recent_nights_only_newest_first() -> None:
    rows = _rows()
    dates = [rows[i]["text"].split("  ")[0] for i in ("t0n0", "t0n1", "t0n2")]
    assert dates == ["2026-08-11", "2026-08-10", "2026-08-09"]
    assert "t0n3" not in rows


def test_a_night_with_a_filter_that_has_no_flat_is_flagged_missing() -> None:
    rows = _rows()
    assert rows["t0n0"]["missing"] is False       # 2026-08-11: L and Ha both have flats in session 3
    assert rows["t0n1"]["missing"] is True        # 2026-08-10: Ha has no flat in session 2
    assert rows["t0n2"]["missing"] is False       # 2026-08-09: L is flatted and it is the only filter
    assert rows["t1n0"]["missing"] is True        # the target's only night has no flats at all


def test_a_night_with_every_filter_flatted_is_not_flagged() -> None:
    survey = _survey()
    assert {r["iid"]: r["missing"] for r in T.tree_rows(survey)}["t0n1"] is True
    survey["flatsBySession"]["2"] = survey["flatsBySession"]["2"] + [{"filter": "Ha", "stamp": "20260810T190200"}]
    flagged = {r["iid"]: r["missing"] for r in T.tree_rows(survey)}
    assert flagged["t0n1"] is False


def test_describe_marks_only_the_missing_filter_and_reads_the_flats() -> None:
    survey = _survey()
    night = survey["nights"][3]                  # M42, 2026-08-11: L flatted, Ha flatted in session 3
    segments = T.describe(survey, night)
    text = "".join(s for s, _ in segments)
    assert text == ("M42 — 2026-08-11, 4 frames in L, Ha.\n"
                    "2 flats in this observing session: L: 1 flat, Ha: 1 flat.")
    assert not any(missing for _, missing in segments)

    night2 = survey["nights"][2]                 # 2026-08-10: session 2 has only L
    segs2 = T.describe(survey, night2)
    assert ("Ha: no flats", True) in segs2
    assert [s for s, m in segs2 if m] == ["Ha: no flats"]


def test_describe_without_a_night_says_to_select_one() -> None:
    assert T.describe(_survey(), None) == [("Select a night.", False)]


# ---- Tk: the picker on a hidden root --------------------------------------------------------------------------------

@pytest.fixture
def tk_root() -> Iterator[Any]:
    try:
        import tkinter
    except (ImportError, OSError) as exc:
        pytest.skip(f"tkinter cannot be imported here: {exc}")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tkinter.TclError:
        pass


def _picker(root: Any) -> Any:
    return T.NightDialog(root, _survey(), "/card")


def _select(dlg: Any, iid: str) -> None:
    dlg.tree.selection_set(iid)
    dlg.top.update()
    dlg._on_select()


def test_the_picker_opens_with_no_night_chosen(tk_root: Any) -> None:
    dlg = _picker(tk_root)
    assert dlg.top.title() == T.WINDOW_TITLE
    assert "disabled" in dlg.ok_button.state()
    assert dlg.detail.get("1.0", "end").strip() == "Select a night."
    dlg._cancel()


def test_selecting_a_night_and_ok_returns_it(tk_root: Any) -> None:
    dlg = _picker(tk_root)
    _select(dlg, "t0n1")
    assert "disabled" not in dlg.ok_button.state()
    assert dlg.detail.get("1.0", "end").startswith("M42 — 2026-08-10")
    dlg._ok()
    assert dlg.result is not None
    assert (dlg.result["target"], dlg.result["date"]) == ("M42", "2026-08-10")
    assert dlg.result is dlg._nights["t0n1"]


def test_cancel_returns_none(tk_root: Any) -> None:
    dlg = _picker(tk_root)
    _select(dlg, "t0n0")
    dlg._cancel()
    assert dlg.result is None


def test_a_target_row_is_not_selectable_as_a_night(tk_root: Any) -> None:
    dlg = _picker(tk_root)
    _select(dlg, "t1")
    assert dlg.tree.selection() == ()
    assert "disabled" in dlg.ok_button.state()
    dlg._ok()                                   # no night chosen: OK does nothing
    assert dlg.result is None
    dlg._cancel()
    assert dlg.result is None
