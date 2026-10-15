"""The Draft button must never fail silently (the bug: a draft that ended in an error closed its progress window at once,
so the user saw no window, no message and no player). Fake widgets always; real Tk where a display exists."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from loomsiril import draft as D
from loomsiril.fly_progress import FlyProgress

from test_draft import FakeMaster, _job, _options, field, tk_real  # noqa: F401  (fixtures)
from test_siril_script import blocked_discover, script, stub  # noqa: F401


class Window:
    """Stands for fly_ui.FlyProgressWindow: a model, a top, a record of being closed and refreshed."""

    def __init__(self, model: FlyProgress) -> None:
        self.model, self.closed, self.refreshed = model, False, 0
        self.top = SimpleNamespace(title=lambda *a: None, destroy=lambda: None)

    def close(self) -> None:
        self.closed = True

    def refresh(self) -> None:
        self.refreshed += 1


def _run(tmp_path: Any, make_inputs: Any, **kw: Any) -> tuple[Any, Window, list[str]]:
    master, model, logs = FakeMaster(), FlyProgress(), []
    window = Window(model)
    run = D.show_draft(master, make_inputs, _options(tmp_path), window, log=logs.append, **kw)
    master.run_until(lambda: run.finished)
    return run, window, logs


def test_a_failed_draft_keeps_its_progress_window_with_the_message(tmp_path: Any) -> None:
    def boom(progress: FlyProgress) -> Any:
        raise ValueError("The image file does not match the image open in Siril.")

    run, window, logs = _run(tmp_path, boom)
    assert run.error and run.player is None
    assert not window.closed                                   # it used to be closed at once: nothing was ever shown
    assert "does not match" in window.model.label() and window.model.done
    assert logs[0] == "Draft: starting" and logs[-1].startswith("Draft: failed: The image file does not match")


def test_a_player_that_cannot_open_keeps_the_window_and_says_why(tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
                                                                 field: Any) -> None:
    def broken(master: Any, draft: Any) -> Any:
        raise RuntimeError("image data is not in a recognised format")

    monkeypatch.setattr(D, "DraftPlayer", broken)
    done: list[Any] = []
    run, window, logs = _run(tmp_path, lambda p: _job(tmp_path, field), on_done=done.append)
    assert run.player is None and "recognised format" in run.error and done == [run]
    assert not window.closed and "recognised format" in window.model.label()
    assert any(line.startswith("Draft: failed:") for line in logs)


def test_every_stage_of_a_draft_is_logged_for_the_console(tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
                                                         field: Any) -> None:
    monkeypatch.setattr(D, "DraftPlayer", lambda master, draft: SimpleNamespace(draft=draft))

    run, window, logs = _run(tmp_path, lambda p: _job(tmp_path, field))
    assert not run.error and window.closed                     # success: the progress window gives way to the player
    assert logs[0] == "Draft: starting"
    assert logs[-1] == "Draft: done, opening the player"
    assert any(line.startswith("Draft: rendering ") and "/" in line for line in logs)


def test_a_cancelled_draft_is_logged_and_closes_the_window(tmp_path: Any) -> None:
    master, model, logs = FakeMaster(), FlyProgress(), []
    window = Window(model)

    def cancel_now(progress: FlyProgress) -> Any:
        progress.request_cancel()
        return None

    run = D.show_draft(master, cancel_now, _options(tmp_path), window, log=logs.append)
    master.run_until(lambda: run.finished)
    assert window.closed and logs[-1] == "Draft: cancelled"


def test_clicking_a_greyed_draft_button_shows_the_reason(stub, script, monkeypatch, blocked_discover, tmp_path) -> None:
    from test_siril_script import _fake_tkinter
    from types import ModuleType
    import sys

    _fake_tkinter(monkeypatch)
    import tkinter as tk

    msg = ModuleType("tkinter.messagebox")
    shown: list = []
    msg.showinfo = msg.showerror = lambda *a, **k: shown.append(a[1])
    tk.messagebox = msg
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", msg)
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({"out_dir": str(tmp_path)}), blocked_discover,
                               None, lambda w, v: None)
    assert dlg._draft_btn.kw["state"] == "disabled"
    for button in (dlg._draft_btn, dlg._render_btn):
        assert button.bindings["<Button-1>"]                   # the click handler is bound to the button
        button.instate = lambda states: True                   # (the fake widget has no ttk state machine)
        button.bindings["<Button-1>"][0](SimpleNamespace(widget=button))
    assert shown == ["Choose the starless file."] * 2
    # a second click while the first is still being prepared is told so, never ignored
    dlg._acting = True
    dlg._start_action(lambda v: None, create_folder=False)
    assert shown[-1] == "Still preparing the previous click: one moment."
    dlg._acting = False
    # an exception in what runs after the checks is a box, not a dead button
    def boom(values: dict) -> None:
        raise KeyError("image_key")

    dlg._prepared({"checks": {}, "stamp": None, "binary": ""}, {}, boom)
    assert shown[-1] == "KeyError: 'image_key'"


def test_the_real_progress_window_stays_up_on_a_failed_draft(tk_real: Any, tmp_path: Any) -> None:
    """Real Tk: the repro of the report (the image file does not match the open image)."""
    from loomsiril.fly_ui import FlyProgressWindow

    root = tk_real.root
    model = FlyProgress()
    window = FlyProgressWindow(root, model)

    def boom(progress: FlyProgress) -> Any:
        raise ValueError("The image file does not match the image open in Siril (160 x 90, 1 channel(s)).")

    logs: list[str] = []
    run = D.show_draft(root, boom, _options(tmp_path), window, log=logs.append)
    import time

    end = time.monotonic() + 10
    while time.monotonic() < end and not (run.finished and window._button.cget("text") == "Close"):
        root.update()                                           # the window's own 200 ms poll shows the outcome
        time.sleep(0.01)
    assert run.error and window.top.winfo_exists()
    assert "does not match" in window._stage.get() and window._button.cget("text") == "Close"
    window.close()


def test_the_extraction_stage_is_logged_as_extracting() -> None:
    model, logs = FlyProgress(), []
    run = D.DraftRun(FakeMaster(), lambda p: None, {}, model, None, None, None, logs.append)
    model.report("Extracting stars with SyQon Studio Axiom")
    model.pump()
    run._log_stage()
    run._log_stage()                                           # the same stage twice is one line
    assert logs == ["Draft: extracting stars..."]
    model.report("Rendering the draft frames", 3, 12)
    model.pump()
    run._log_stage()
    assert logs[-1] == "Draft: rendering 3/12"
