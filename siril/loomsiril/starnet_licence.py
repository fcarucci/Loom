"""The StarNet2 licence gate for the Siril script (E7c).

StarNet2 is a separate program by its author (StarNet Astro). Loom runs its command-line tool as a separate process and
never bundles, downloads or redistributes it. Its licence (revision 2026-09-07, LICENSE.txt in the official Linux zip,
next to the binary) must be shown in full and accepted before Loom runs it. The accepted revision is kept in the saved
options, so the question is asked again only when the licence changes.

The window is tkinter, so it is imported lazily and only the Siril script's dialog calls it; the rest of loomsiril
stays free of GUI code.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

__all__ = ["display_text", "STARNET_DOCS_URL", "STARNET_LICENCE_REV", "accepted", "ask", "check", "licence_text", "require"]

#: The licence revision in the StarNet2 zip (LICENSE.txt). A new revision asks for acceptance again.
STARNET_LICENCE_REV = "2026-09-07"
#: The official documentation (the apex domain: the www. host redirects to the homepage and drops the path).
STARNET_DOCS_URL = "https://starnetastro.com/documentation/starnet/command-line-tool/"
#: The licence file that ships next to the binary.
LICENCE_FILE = "LICENSE.txt"


def licence_text(binary: Any) -> Optional[str]:
    """The LICENSE.txt that sits next to the StarNet2 binary, or None when it is not there."""
    if not binary:
        return None
    path = Path(os.fspath(binary)).resolve().parent / LICENCE_FILE
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def accepted(values: Mapping[str, Any]) -> bool:
    """True when the dialog values carry the current licence revision as accepted."""
    return values.get("starnet_licence") == STARNET_LICENCE_REV


def require(values: Mapping[str, Any]) -> None:
    """Raise ValueError (shown in a box) unless the StarNet licence is accepted: StarNet mode stays off without it."""
    if not accepted(values):
        raise ValueError("StarNet needs its licence accepted first: choose StarNet2 in the dialog and accept it.")


def display_text(binary: Any) -> str:
    """The text the licence window shows: the licence file (a disk read: call it off the Tk thread) or a pointer to it."""
    return licence_text(binary) or (
        f"{LICENCE_FILE} was not found next to the StarNet2 binary. Read the licence at {STARNET_DOCS_URL} "
        "before you accept.")


def check(parent: Any, accepted_rev: Optional[str], binary: Any, text: Optional[str] = None) -> Optional[str]:
    """The licence revision for a StarNet run: the saved one when it is current, else asked now.

    Returns None when the user declines; StarNet mode then stays off (a box says so).
    """
    if accepted_rev == STARNET_LICENCE_REV:
        return accepted_rev
    rev = ask(parent, binary) if text is None else ask(parent, binary, text)
    if rev is None:
        from tkinter import messagebox

        messagebox.showinfo("Loom Fly-Through", "StarNet mode stays off until its licence is accepted.", parent=parent)
    return rev


def ask(parent: Any, binary: Any, text: Optional[str] = None) -> Optional[str]:
    """A modal window with the full licence and an acceptance tick box. Returns the accepted revision, or None.

    Accept stays disabled until the box is ticked; Decline, the close button or closing the window return None.
    """
    import tkinter as tk
    from tkinter import ttk

    if text is None:
        text = display_text(binary)
    result: dict[str, Optional[str]] = {"rev": None}
    win = tk.Toplevel(parent)
    win.title("StarNet2 licence")

    def close(rev: Optional[str]) -> None:
        result["rev"] = rev
        win.destroy()

    intro = ("Loom Fly-Through runs the StarNet2 command-line tool, a separate program by StarNet Astro, to remove "
             "the stars from your image. StarNet2 does that processing, not Loom. Its licence is shown in full below. "
             f"Official documentation: {STARNET_DOCS_URL}")
    ttk.Label(win, text=intro, wraplength=560, justify="left", padding=8).pack(anchor="w")
    box = tk.Text(win, width=80, height=24, wrap="word")
    box.insert("1.0", text)
    box.configure(state="disabled")
    box.pack(fill="both", expand=True, padx=8)
    agree = tk.BooleanVar(value=False)
    accept_btn = ttk.Button(win, text="Accept and continue", command=lambda: close(STARNET_LICENCE_REV))
    accept_btn.state(["disabled"])
    agree.trace_add("write", lambda *_: accept_btn.state(["!disabled"] if agree.get() else ["disabled"]))
    ttk.Checkbutton(win, text="I have read the StarNet2 licence and accept its terms", variable=agree).pack(
        anchor="w", padx=8, pady=4)
    row = ttk.Frame(win, padding=8)
    row.pack(fill="x")
    ttk.Button(row, text="Decline", command=lambda: close(None)).pack(side="right", padx=4)
    accept_btn.pack(side="right", padx=4)
    win.protocol("WM_DELETE_WINDOW", lambda: close(None))
    win.transient(parent)
    win.grab_set()
    parent.wait_window(win)
    return result["rev"]
