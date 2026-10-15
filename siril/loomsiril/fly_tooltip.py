"""A hover tip for a tk widget (Tk has none): a small borderless window under the pointer after a short pause.

``Tooltip(widget, text)`` binds <Enter>, <Leave>, <ButtonPress> and <Destroy> on the widget (adding to any binding it
has) and never raises: a tip that cannot be shown is just not shown. tkinter is imported when a tip is shown.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DELAY_MS", "Tooltip"]

DELAY_MS = 500
_WRAP = 340                                                     # px: a long tooltip wraps to this width


class Tooltip:
    """The tip of ``widget``: ``text`` appears ``DELAY_MS`` after the pointer rests on it, gone when it leaves."""

    def __init__(self, widget: Any, text: str, delay: int = DELAY_MS) -> None:
        self.widget, self.text, self.delay = widget, text, delay
        self._after: Any = None
        self._window: Any = None
        for sequence, handler in (("<Enter>", self._enter), ("<Leave>", self._hide), ("<ButtonPress>", self._hide),
                                  ("<Destroy>", self._hide)):
            widget.bind(sequence, handler, add="+")

    def _enter(self, _event: Any = None) -> None:
        self._hide()
        try:
            self._after = self.widget.after(self.delay, self._show)
        except Exception:
            self._after = None

    def _show(self) -> None:
        self._after = None
        if self._window is not None or not self.text:
            return
        try:
            import tkinter as tk
            from tkinter import ttk

            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            window = tk.Toplevel(self.widget)
            window.wm_overrideredirect(True)
            window.wm_geometry(f"+{x}+{y}")
            ttk.Label(window, text=self.text, wraplength=_WRAP, justify="left", padding=(6, 3),
                      background="#ffffe0", foreground="#222222", relief="solid", borderwidth=1).pack()
            self._window = window
        except Exception:                                       # no display, widget gone: no tip
            self._window = None

    def _hide(self, _event: Any = None) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
