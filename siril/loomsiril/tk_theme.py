"""The Tk root window of a Siril script, in Siril's theme when that can be applied.

The root is hidden: every script window is a Toplevel on it, so a visible root would show as an extra, empty window
titled "tk".

sirilpy.tksiril.match_theme_to_siril needs a themed window (ttkthemes' ThemedTk, which has ``set_theme``); on a plain
``tk.Tk()`` it fails with "unknown option -theme". The theme is cosmetic: when ttkthemes is missing or the theme cannot
be applied, the window opens unthemed and the reason is logged.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

#: The package that provides ThemedTk; the scripts install it with sirilpy.ensure_installed.
THEME_PACKAGE = "ttkthemes"


def themed_root(siril: Any = None, log: Optional[Callable[[str], None]] = None) -> Any:
    """A new, hidden Tk root: a ThemedTk in Siril's theme when ``siril`` is given, else a plain tk.Tk."""
    root = _new_root(siril, log)
    root.withdraw()
    return root


def close_root(root: Any) -> None:
    """Destroys the root; a root already destroyed (its window closed, or Tk shut down) is left alone."""
    import tkinter as tk

    try:
        root.destroy()
    except tk.TclError:
        pass


def _new_root(siril: Any, log: Optional[Callable[[str], None]]) -> Any:
    import tkinter as tk

    if siril is None:
        return tk.Tk()
    try:
        from ttkthemes import ThemedTk
    except ImportError:
        _note(log, f"{THEME_PACKAGE} is not installed: the window does not use Siril's theme")
        return tk.Tk()
    root = ThemedTk()
    try:
        from sirilpy import tksiril

        tksiril.match_theme_to_siril(root, siril)
    except (RuntimeError, TypeError) as exc:
        _note(log, f"Siril's theme was not applied: {exc}")
    return root


def _note(log: Optional[Callable[[str], None]], text: str) -> None:
    if log is not None:
        log(text)
