"""The Fly-Through progress window (tkinter, imported lazily) and the choice between it and the headless loop.

The window is driven only by :class:`loomsiril.fly_progress.FlyProgress`: it polls the model on the Tk thread (which is
the thread that talks to Siril) and never does slow work itself.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from loomsiril.fly_progress import FlyProgress, drive

__all__ = ["FlyProgressWindow", "forward_to_siril", "show_headless", "show_progress"]


def forward_to_siril(siril: Any) -> Callable[[FlyProgress], None]:
    """A headless ``on_update``: the stage and fraction go to Siril's progress bar, once per change; the console lines
    the worker announced (the distance in use) go to Siril's console, once each."""
    last: list[Any] = [None]
    logged = [0]

    def update(p: FlyProgress) -> None:
        while logged[0] < len(p.console_lines):               # the worker's console lines (the distance in use)
            line = p.console_lines[logged[0]]
            logged[0] += 1
            siril.log(f"Loom Fly-Through: {line}")
        state = (p.stage, round(p.percent, 3))
        if state != last[0] and not p.error:
            last[0] = state
            siril.update_progress(p.stage, p.percent / 100.0)

    return update


class FlyProgressWindow:
    """Stage line, two bars (the stage: n of N with its percent, or moving when it has no count; and the whole run: a
    percent that only goes up), elapsed and time left, Cancel while running and Close when done. The final line says
    what was written and where, or the error. Every ``after`` callback it schedules is cancelled by :meth:`close`."""

    POLL_MS = 200

    def __init__(self, master: Any, model: FlyProgress, on_update: Optional[Callable[[FlyProgress], None]] = None
                 ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.model = model
        self._on_update = on_update
        self._moving = False
        self._after_id: Any = None
        self._closed = False
        self.top = tk.Toplevel(master)
        self.top.title("Loom Fly-Through - rendering" + (f" - {model.image_title}" if getattr(model, "image_title", "") else ""))
        self.top.resizable(False, False)
        frame = ttk.Frame(self.top, padding=10)
        frame.grid(sticky="nsew")
        self._stage = tk.StringVar(value=model.label())
        self._overall = tk.StringVar(value=model.overall_text())
        self._timing = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._stage, width=70, wraplength=520, justify="left").grid(row=0, column=0,
                                                                                                    sticky="w")
        self._bar = ttk.Progressbar(frame, maximum=100.0, length=520, mode="indeterminate")
        self._bar.grid(row=1, column=0, sticky="we", pady=(6, 2))
        ttk.Label(frame, textvariable=self._overall).grid(row=2, column=0, sticky="w")
        self._total_bar = ttk.Progressbar(frame, maximum=100.0, length=520, mode="determinate")
        self._total_bar.grid(row=3, column=0, sticky="we", pady=(2, 6))
        ttk.Label(frame, textvariable=self._timing).grid(row=4, column=0, sticky="w")
        self._button = ttk.Button(frame, text="Cancel", command=self._cancel)
        self._button.grid(row=5, column=0, sticky="e", pady=(6, 0))
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.top.bind("<Destroy>", self._on_destroy, add="+")
        self._poll()

    def refresh(self) -> None:
        """Applies the queued events and redraws (what each poll does)."""
        m = self.model
        m.pump()
        if self._on_update is not None:
            self._on_update(m)
        self._stage.set(m.label())
        self._overall.set(m.overall_text())
        self._timing.set(m.timing())
        self._total_bar["value"] = m.overall_value()
        if m.indeterminate and not m.done:
            if not self._moving:
                self._bar.configure(mode="indeterminate")
                self._bar.start(60)
                self._moving = True
        else:
            if self._moving:
                self._bar.stop()
                self._moving = False
            self._bar.configure(mode="determinate")
            self._bar["value"] = m.stage_value()

    def _poll(self) -> None:
        self._after_id = None
        if self._closed:
            return
        self.refresh()
        if self.model.done:
            self._button.state(["!disabled"])
            self._button.configure(text="Close", command=self.close)
            return
        self._after_id = self.top.after(self.POLL_MS, self._poll)

    def _cancel(self) -> None:
        if self.model.done:
            self.close()
            return
        self.model.request_cancel()
        self._button.state(["disabled"])
        self._stage.set(self.model.label())

    def _cancel_after(self) -> None:
        if self._after_id is not None:
            try:
                self.top.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _on_destroy(self, event: Any = None) -> None:
        if event is None or getattr(event, "widget", self.top) is self.top:
            self._closed = True
            self._cancel_after()

    def close(self) -> None:
        """Cancels the pending poll and destroys the window (safe to call twice)."""
        self._closed = True
        self._cancel_after()
        try:
            self.top.destroy()
        except Exception:
            pass


def show_headless(progress: FlyProgress, thread: threading.Thread, siril: Any) -> bool:
    """No window: Siril's own progress bar follows the run. False: the caller reports the outcome in Siril's boxes."""
    drive(progress, thread, forward_to_siril(siril))
    return False


def show_progress(progress: FlyProgress, thread: threading.Thread, siril: Any) -> bool:
    """Shows the window until the run is over and closed. Returns True when the user saw the final outcome there.

    Without a display (or without tkinter) the run is driven headless, through Siril's own progress bar, and False is
    returned: the caller then reports the outcome in Siril's boxes.
    """
    try:
        from loomsiril.tk_theme import close_root, themed_root

        root = themed_root(siril, log=getattr(siril, "log", None))
    except Exception:  # no tkinter or no display
        return show_headless(progress, thread, siril)
    try:
        win = FlyProgressWindow(root, progress, forward_to_siril(siril))
        root.wait_window(win.top)
        if not progress.done:
            progress.cancel_event.set()                  # a closed window never leaves a run going
        thread.join()
        progress.pump()
    finally:
        close_root(root)
    return True
