"""The dialog rows of the Stars-and-motion and HDR sections, generated from the table in :mod:`loomsiril.fly_options`.

``build(dialog, frame, section, values)`` makes one row per control of the section (a spin box, a slider, a tick box, a
drop-down or a text box by the control's kind), registers each as the option ``dialog._control`` (its key is the remembered
one; Travel's is ``travel``), puts the row in its greying group ``opt:<key>`` (the rule and its reason are
``FlyDialogModel.enabled`` / ``disabled_reason``), gives it its tooltip, and ends with a "Reset to defaults" button.
A typed value that does not parse raises ``ValueError`` from the control's getter (the dialog holds it and names the
control, as it does for its other boxes).
"""

from __future__ import annotations

from typing import Any, Callable

from loomsiril import fly_options as fo
from loomsiril.fly_model import OPTION_GROUP

__all__ = ["build", "update_notes"]

GREY = "gray50"


def build(dialog: Any, frame: Any, section: str, values: Any) -> None:
    """Fills ``frame`` (the section's body) with the rows of ``section`` and its Reset button; ``values`` are the dialog's
    starting values (``DialogValues``: ``fly`` holds the engine value of each remembered control)."""
    from tkinter import ttk

    if not hasattr(dialog, "_fo_set"):
        dialog._fo_set = {}                                  # key -> setter(engine value): what Reset to defaults calls
    for opt in fo.options_in(section):
        _row(dialog, frame, opt, values.fly.get(opt.saved) if opt.saved else None)
    reset = ttk.Button(frame, text="Reset to defaults", command=lambda: reset_section(dialog, section))
    reset.grid(row=dialog._next(frame), column=1, sticky="w", padx=4, pady=(6, 2))


def reset_section(dialog: Any, section: str) -> None:
    """The section's controls back to their defaults (Travel to empty: automatic)."""
    for opt in fo.options_in(section):
        dialog._fo_set[opt.saved or opt.key](opt.default)


def _row(dialog: Any, frame: Any, opt: fo.Option, value: Any) -> None:
    import tkinter as tk
    from tkinter import ttk

    from loomsiril.fly_tooltip import Tooltip

    key = opt.saved or opt.key
    group = OPTION_GROUP + opt.saved if opt.saved and (opt.enabled_when or opt.preset) else None

    def getter() -> Any:                                     # a bad entry raises OptionError (a ValueError) naming the control
        var = dialog._ctl[key][0]
        raw = var.get()
        if opt.kind == fo.SLIDER:
            raw = round(float(raw))
        return fo.parse(opt, raw)

    if opt.kind == fo.CHECK:
        row = dialog._check_row(frame, key, opt.check_text, value, name=group)
        setter = dialog._ctl[key][0].set
    elif opt.kind == fo.COMBO:
        names = [label for _v, label in opt.choices]
        row = dialog._choice_row(frame, key, opt.label, dict(opt.choices).get(value, names[0]), names, name=group,
                                 getter=getter)
        setter = lambda v: dialog._ctl[key][0].set(dict(opt.choices).get(v, names[0]))  # noqa: E731
    elif opt.kind == fo.EDIT:
        row = dialog._text_row(frame, key, opt.label, fo.format_value(opt, value), name=group, getter=getter, width=12)
        setter = lambda v: dialog._ctl[key][0].set(fo.format_value(opt, v))  # noqa: E731
    elif opt.kind == fo.SPIN:
        var = tk.StringVar(value=str(fo.shown(opt, value)))
        row = dialog._add_row(frame, opt.label, lambda p: ttk.Spinbox(
            p, from_=opt.minimum, to=opt.maximum, increment=opt.step, textvariable=var, width=8), group)
        dialog._control(key, var, getter)
        setter = lambda v: var.set(str(fo.shown(opt, v)))  # noqa: E731
    else:                                                    # SLIDER: a scale and its value ("100%") in the third column
        var = tk.DoubleVar(value=float(fo.shown(opt, value)))
        text = tk.StringVar(value=fo.format_value(opt, value))
        row = dialog._add_row(frame, opt.label, lambda p: ttk.Scale(
            p, from_=opt.minimum, to=opt.maximum, orient="horizontal", variable=var), group)
        shown = ttk.Label(frame, textvariable=text)
        shown.grid(row=row.row, column=2, sticky="w", padx=4)
        dialog._register_row(frame, group, ((shown, "normal"),)) if group else None
        var.trace_add("write", lambda *_: text.set(f"{round(float(var.get()))}%"))
        dialog._control(key, var, getter)
        setter = lambda v: var.set(float(fo.shown(opt, v)))  # noqa: E731
    dialog._fo_set[key] = setter
    if opt.key == "travel":                                  # the grey note: the automatic travel, or that it is capped
        dialog._travel_note = tk.StringVar(value="")
        ttk.Label(frame, textvariable=dialog._travel_note, foreground=GREY).grid(
            row=row.row, column=2, sticky="w", padx=4)
        dialog._ctl[key][0].trace_add("write", lambda *_: update_notes(dialog))
    for widget in (row.widget, row.label):
        if widget is not None:
            Tooltip(widget, opt.tooltip)


def update_notes(dialog: Any) -> None:
    """The note beside Travel: what an empty box means for this target, or that a typed one is capped (the dialog calls this
    after every change and when a distance is found)."""
    note = getattr(dialog, "_travel_note", None)
    ctl = getattr(dialog, "_ctl", {}).get("travel")
    if note is None or ctl is None:
        return
    result = getattr(dialog, "_dist_result", None)
    distance = getattr(result, "distance", None)
    try:
        travel = fo.parse(fo.options_in("motion")[0], ctl[0].get())
    except ValueError:
        note.set("")                                          # half typed: the Render click names the problem
        return
    note.set(fo.travel_note(travel, str(dialog._vars["target_type"].get()), distance))
