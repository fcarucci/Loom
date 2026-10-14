"""Mirror of sirilpy.tksiril (signatures as in Siril 1.4). Theme helpers are no-ops here: there is no Siril."""

from __future__ import annotations

from typing import Any


def create_tooltip(widget: Any, text: str, wrap_length: int = 250) -> None:
    """Attach a tooltip (no-op in the stub)."""
    return None


def match_theme_to_siril(themed_tk: Any, s: Any, on_top: bool = False) -> None:
    """Apply Siril's theme to a Tk window, as Siril 1.4 does: ``set_theme`` when the window has it (ttkthemes'
    ThemedTk), else ``configure(theme=...)``, which a plain tk.Tk refuses ("unknown option -theme"). Any failure is
    raised as RuntimeError("Failed to set theme: ..."). The theme name is the stub's fixed dark theme."""
    if not (hasattr(themed_tk, "set_theme") or (hasattr(themed_tk, "configure") and hasattr(themed_tk, "winfo_class"))):
        raise TypeError("themed_tk must be a ThemedTk instance or a Tk window")
    try:
        if hasattr(themed_tk, "set_theme"):
            themed_tk.set_theme("equilux")
        else:
            themed_tk.configure(theme="equilux")
    except Exception as exc:
        raise RuntimeError(f"Failed to set theme: {exc}") from exc


def standard_style() -> None:
    """Set the ttk style used by Siril scripts (no-op in the stub)."""
    return None


def elevate(root: Any) -> None:
    """Bring a Tk window to the front (no-op in the stub)."""
    return None
