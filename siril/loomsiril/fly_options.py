"""The Fly-Through dialog's "Stars and motion" and "HDR" controls as one declarative table (pure: no tkinter).

Every control the PixInsight dialog has there (pixinsight/script/FlyThrough.js, ``buildMotion`` 1281-1338 and
``buildOutput`` 1340-1400) is one :class:`Option`: its ``FlyJob.options`` key (the JS ``opts`` name), the key it is
remembered under, the widget, range, step, default and units (copied from the JS, with the line), the tooltip, the
rule that greys it and why, and how a typed value is parsed, validated, formatted and mapped into the job. The dialog
builds its rows from :data:`OPTIONS` in one loop; the tests compare the table with the engine and the JS.

Units: an option's *engine value* is what the renderer takes (a fraction for the percent controls); the *shown
value* is what the widget displays (``engine * scale``). :func:`parse` takes a widget value (a number, or typed text)
and returns the engine value or raises :class:`OptionError` naming the control, so an invalid number is held and
named, never a crash.

Every option here changes the pictured frames, so each is a key of the render options and therefore of the frame
signature (``presets.frame_signature`` keeps every option but ``presets.ENCODE_ONLY``): changing one re-renders, an
equal value reuses the frames. In SDR the three HDR-only options (peak, star HDR, the transfers) are left out of the
job's options, since they cannot change an SDR frame, so toggling them does not re-render an SDR clip.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from loomsiril import colour, geometry, parallax
from loomsiril import presets as preset_mod

__all__ = ["OPTIONS", "SECTIONS", "Option", "OptionError", "Context", "by_saved", "defaults", "enabled", "format_value",
           "job_options", "load_values", "options_from_values", "options_in", "parse", "problems", "reason", "saved_keys", "shown",
           "travel_note", "transfer_default"]

#: Section ids and titles, as the dialog's container names them.
SECTIONS: dict[str, str] = {"motion": "Stars and motion", "hdr": "HDR"}

SPIN, SLIDER, CHECK, COMBO, EDIT = "spin", "slider", "check", "combo", "edit"

#: Enable rules (``Option.enabled_when``) and why a control is grey.
HDR_ONLY = "hdr"
NEBULA_ONLY = "nebula"
_REASONS: dict[str, str] = {
    HDR_ONLY: "Dynamic range is SDR: choose HDR to use this",
    NEBULA_ONLY: "A galaxy is a fixed backdrop: it does not grow",
}
_PRESET_NOT_CHOSEN = "This output size is not ticked"


class OptionError(ValueError):
    """A typed value the control does not take; the text names the control and says what is allowed."""


@dataclass(frozen=True)
class Option:
    """One control: what it is, what it holds and how it reaches the render options.

    ``key`` the ``FlyJob.options`` name (the JS ``opts`` name; the pipeline also takes snake_case). ``saved`` the key the
    value is remembered under (``siril_bridge.OPTION_KEYS``; None: not remembered). ``default`` the engine value
    (None for travel: automatic). ``minimum``/``maximum``/``step`` are in shown units. ``scale`` is shown/engine.
    ``choices`` are ``(engine value, label)`` for a combo. ``enabled_when`` names the rule that greys the control
    (``HDR_ONLY``, ``NEBULA_ONLY`` or empty); ``preset`` ties a transfer combo to its output size. ``js`` cites the JS
    line of the control, ``js_default`` where the default is defined.
    """

    key: str
    saved: Optional[str]
    section: str
    label: str
    kind: str
    default: Any
    tooltip: str
    js: str
    units: str = ""
    scale: float = 1.0
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: float = 1.0
    choices: tuple[tuple[Any, str], ...] = ()
    enabled_when: str = ""
    preset: str = ""
    check_text: str = ""        # a check box's own text (its label cell stays empty, as in the JS)
    row: int = 0                # the dialog's row within its section (the JS rows, 1334-1337 and 1398)


def _transfer_options() -> list[Option]:
    out = []
    for i, pid in enumerate(preset_mod.PRESET_ORDER):
        out.append(Option(
            key="hdrTransfer", saved="hdr_transfer_" + pid, section="hdr", label="Transfer for " + preset_mod.PRESET_LABELS[pid],
            kind=COMBO, default=transfer_default(pid), choices=(("hlg", "HLG"), ("pq", "PQ (HDR10)")),
            tooltip="HDR transfer for this preset: HLG looks right on SDR screens too; PQ is for a display you control.",
            js="FlyThrough.js:1370-1380 (default Fly.HDR_DEFAULT_TRANSFER, lib/Fly.js:1508)",
            enabled_when=HDR_ONLY, preset=pid, row=10 + i // 2))
    return out


def transfer_default(preset_id: str) -> str:
    """The default HDR transfer of a preset: ``colour.HDR_DEFAULT_TRANSFER`` (Fly.HDR_DEFAULT_TRANSFER, lib/Fly.js:1508; the pipeline's is the same table)."""
    return colour.HDR_DEFAULT_TRANSFER[preset_id]


#: The table. Order is the dialog's order (rows are the JS rows: 1334-1337 for motion, 1383-1398 for HDR).
OPTIONS: tuple[Option, ...] = (
    Option(key="travel", saved=None, section="motion", label="Travel (pc)", kind=EDIT, default=None, units="pc",
           minimum=0.0, step=1.0, row=0,
           tooltip="How far the camera moves towards the target, in parsecs (at most 0.9 of a nebula's distance). "
                   "Empty: 0.2 of the distance for a nebula, 200 pc for a galaxy.",
           js="FlyThrough.js:1284-1289 (note 1917; default Fly.defaultTravel, parallax.default_travel; not remembered: 2043)"),
    Option(key="easing", saved="easing", section="motion", label="Easing", kind=COMBO, default="smoothstep", row=0,
           choices=(("smoothstep", "Smooth"), ("linear", "Linear")),
           tooltip="How the camera speeds up and slows down: Smooth eases in and out, Linear moves at one speed.",
           js="FlyThrough.js:1290-1292 (values 1768; lib/Fly.js Fly.ease)"),
    Option(key="growth", saved="growth", section="motion", label="Growth", kind=EDIT, default=geometry.GROWTH_DEFAULT,
           minimum=0.0, maximum=1.0, step=0.05, row=1,
           tooltip="How much approaching stars grow, as a share of their change in distance (0.15 by default; "
                   "0 keeps their size, 1 is the size the distance alone would give).",
           js="FlyThrough.js:1293 (default Fly.GROWTH_DEFAULT = 0.15, lib/Fly.js:240; options 1769; the JS takes any "
              "number, this table limits it to 0-1)"),
    Option(key="backdropMotion", saved="backdrop_motion", section="motion", label="Nebula motion (%)", kind=SPIN,
           default=geometry.BACKDROP_MOTION_DEFAULT, scale=100.0, minimum=0, maximum=100, step=1, units="%", row=1,
           enabled_when=NEBULA_ONLY,
           tooltip="How much the nebula grows as the camera moves in, as a share of the growth its real distance "
                   "would give. 100% is physical and usually far too much to watch; the stars always keep their "
                   "real motion.",
           js="FlyThrough.js:1294-1299 (default Fly.BACKDROP_MOTION_DEFAULT = 0.4, lib/Fly.js:58; options 1770; "
              "Render.js:948)"),
    Option(key="brightening", saved="brightening", section="motion", label="", kind=CHECK, default=True, row=1,
           check_text="Brighten approaching stars",
           tooltip="Approaching stars brighten with the inverse square of their distance. Off keeps their light.",
           js="FlyThrough.js:1300-1302 (options 1769; Fly.pixelScale lib/Fly.js:256)"),
    Option(key="twinkle", saved="twinkle", section="motion", label="Twinkle (%)", kind=SPIN,
           default=geometry.TWINKLE_DEFAULT, scale=100.0, minimum=0, maximum=10, step=1, units="%", row=2,
           tooltip="A slow wobble in each star's brightness, in percent. Not physical -- in space stars do not "
                   "twinkle -- but a touch of life up close. 0 is physical.",
           js="FlyThrough.js:1303-1307 (default Fly.TWINKLE_DEFAULT = 0.03, lib/Fly.js:210; options 1771)"),
    Option(key="bloom", saved="bloom", section="motion", label="Bloom (%)", kind=SPIN, default=1.0, scale=100.0,
           minimum=0, maximum=200, step=1, units="%", row=2,
           tooltip="A near star's light past white glows instead of clipping flat: a round core, a soft halo and a "
                   "faint glare, whitening as a camera's sensor does. 0 turns it off.",
           js="FlyThrough.js:1316-1319 (default 100, line 1317; options 1771; Render.js:968)"),
    Option(key="motionBlur", saved="motion_blur", section="motion", label="", kind=CHECK, default=True, row=2,
           check_text="Motion blur",
           tooltip="Stars streak slightly along their path, as a camera with a 180° shutter records them.",
           js="FlyThrough.js:1320-1323 (options 1771; Render.js:1293)"),
    Option(key="starSaturation", saved="star_saturation", section="motion", label="Star colour", kind=SLIDER,
           default=1.0, scale=100.0, minimum=0, maximum=200, step=1, units="%", row=3,
           tooltip="The stars' colour saturation: 100% as in the image, less toward white, more for richer colour. "
                   "Their brightness is kept.",
           js="FlyThrough.js:1308-1315 (range 0-200 and default 100: 1309-1310; options 1772; Render.js:966)"),
    Option(key="peak", saved="hdr_peak", section="hdr", label="Peak (nits)", kind=SPIN, default=1000,
           minimum=400, maximum=4000, step=100, units="nits", enabled_when=HDR_ONLY, row=20,
           tooltip="Peak brightness for PQ, in nits. HLG is relative to a 1000-nit display.",
           js="FlyThrough.js:1388-1391 (default Fly.HDR_PEAK_DEFAULT = 1000, lib/Fly.js:1506; options 1782)"),
    Option(key="starHdr", saved="star_hdr", section="hdr", label="", kind=CHECK, default=True,
           enabled_when=HDR_ONLY, row=20, check_text="Stars into HDR headroom",
           tooltip="In HDR, the bright stars reach past SDR white towards the peak, each by its Gaia magnitude (the "
                   "brightest to the peak, 5 magnitudes fainter not at all); the nebula or galaxy keeps its tone.",
           js="FlyThrough.js:1393-1397 (rule 1466; options 1773; Render.js:972)"),
) + tuple(_transfer_options())

_BY_SAVED: dict[str, Option] = {o.saved: o for o in OPTIONS if o.saved}


def by_saved(name: str) -> Option:
    """The control remembered under ``name``."""
    return _BY_SAVED[name]


def saved_keys() -> tuple[str, ...]:
    """The keys to add to ``siril_bridge.OPTION_KEYS``: every remembered control."""
    return tuple(o.saved for o in OPTIONS if o.saved)


def options_in(section: str) -> list[Option]:
    """The controls of a section, in dialog order."""
    return [o for o in OPTIONS if o.section == section]


def defaults(section: Optional[str] = None) -> dict[str, Any]:
    """Default engine values by remembered key (the 'Reset to defaults' of a section; travel is not in it)."""
    return {o.saved: o.default for o in OPTIONS if o.saved and (section is None or o.section == section)}


# ---------------------------------------------------------------------------
# Parse, validate, format.
# ---------------------------------------------------------------------------

def _number(opt: Option, raw: Any) -> float:
    if isinstance(raw, bool):
        raise OptionError(f"{opt.label}: a number is needed")
    if isinstance(raw, str):
        text = raw.strip().rstrip("%").strip()
        if not text:
            raise OptionError(f"{opt.label}: a number is needed")
        try:
            value = float(text)
        except ValueError:
            raise OptionError(f"{opt.label}: {raw.strip()!r} is not a number") from None
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise OptionError(f"{opt.label}: a number is needed") from None
    if not math.isfinite(value):
        raise OptionError(f"{opt.label}: a finite number is needed")
    return value


def parse(opt: Option, raw: Any) -> Any:
    """The engine value of a widget value (a number or typed text; a combo's value or label; a check's bool).

    Raises :class:`OptionError` (naming the control and its range) for anything the control does not take.
    Travel: an empty text is ``None`` (automatic), else above 0. A spin or slider is a whole number in its range,
    as in the JS; growth is a number in 0-1.
    """
    if opt.kind == CHECK:
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if opt.kind == COMBO:
        for value, label in opt.choices:
            if raw == value or (isinstance(raw, str) and raw.strip().lower() in (value, label.lower())):
                return value
        raise OptionError(f"{opt.label}: choose " + " or ".join(label for _v, label in opt.choices))
    if opt.key == "travel":
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        value = _number(opt, raw)
        if not value > 0:
            raise OptionError("Travel (pc): must be above 0 (empty for automatic)")
        return value
    value = _number(opt, raw)
    if opt.kind in (SPIN, SLIDER):
        if value != round(value):
            value = float(round(value))
    low, high = opt.minimum, opt.maximum
    if (low is not None and value < low) or (high is not None and value > high):
        raise OptionError(f"{opt.label}: {_range_text(opt)}")
    engine = value / opt.scale
    return int(round(engine)) if isinstance(opt.default, int) and not isinstance(opt.default, bool) else engine


def _range_text(opt: Option) -> str:
    unit = opt.units if opt.units == "%" else (" " + opt.units if opt.units else "")
    return f"between {opt.minimum:g}{unit} and {opt.maximum:g}{unit}"


def shown(opt: Option, value: Any) -> Any:
    """The widget's number or choice for an engine value (a spin's whole number; ``None`` stays None)."""
    if opt.kind in (CHECK, COMBO) or value is None or opt.key == "travel" or opt.key == "growth":
        return value
    return int(round(float(value) * opt.scale))


def format_value(opt: Option, value: Any) -> str:
    """The text a control shows for an engine value: "40", "100%" for the slider, "0.15" for growth, "" for auto travel."""
    if value is None:
        return ""
    if opt.kind == CHECK:
        return "on" if value else "off"
    if opt.kind == COMBO:
        return dict(opt.choices).get(value, str(value))
    if opt.key in ("travel", "growth"):
        return f"{float(value):g}"
    text = str(shown(opt, value))
    return text + "%" if opt.kind == SLIDER else text


def problems(values: Mapping[str, Any]) -> list[str]:
    """The controls whose value does not parse, one message each, naming the control (empty: all are good).

    ``values`` maps a remembered key (and ``"travel"``) to a widget value; a key not given is skipped."""
    out = []
    for opt in OPTIONS:
        name = opt.saved or opt.key
        if name in values:
            try:
                parse(opt, values[name])
            except OptionError as exc:
                out.append(str(exc))
    return out


def load_values(saved: Mapping[str, Any]) -> dict[str, Any]:
    """Engine values from remembered ones: an entry that is missing or no longer valid is the default."""
    out: dict[str, Any] = {}
    for opt in OPTIONS:
        if not opt.saved:
            continue
        raw = saved.get(opt.saved, opt.default)
        try:
            if opt.kind in (SPIN, SLIDER) and not isinstance(raw, bool):
                raw = float(raw) * opt.scale            # remembered in engine units, parsed in shown ones
            out[opt.saved] = parse(opt, raw)
        except (OptionError, TypeError, ValueError):
            out[opt.saved] = opt.default
    return out


# ---------------------------------------------------------------------------
# Enable rules, and the mapping into the job.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Context:
    """What the rules look at: ``dynamic`` "sdr" or "hdr" (the Dynamic range combo), the ticked ``presets``, the
    ``target_type`` ("nebula" or "galaxy")."""

    dynamic: str = "sdr"
    presets: Sequence[str] = ()
    target_type: str = "nebula"


def reason(opt: Option, ctx: Context) -> str:
    """Why the control is grey ("" when it is enabled). HDR-only controls need Dynamic range HDR (FlyThrough.js 1462-1467);
    a transfer combo is also grey for an output size that is not ticked; Nebula motion is grey for a galaxy
    (its backdrop is fixed: geometry.backdrop_zoom is 1)."""
    if opt.enabled_when == HDR_ONLY and ctx.dynamic != "hdr":
        return _REASONS[HDR_ONLY]
    if opt.enabled_when == NEBULA_ONLY and ctx.target_type == "galaxy":
        return _REASONS[NEBULA_ONLY]
    if opt.preset and opt.preset not in ctx.presets:
        return _PRESET_NOT_CHOSEN
    return ""


def enabled(opt: Option, ctx: Context) -> bool:
    """Whether the control takes input."""
    return not reason(opt, ctx)


def job_options(values: Mapping[str, Any], ctx: Context, travel: Optional[float] = None) -> dict[str, Any]:
    """The ``FlyJob.options`` of the controls: ``{JS option name: engine value}``.

    ``values`` maps remembered keys to engine values (missing: the default). Every option goes in (explicit values equal
    to the engine's defaults leave the frame signature as it was). SDR leaves out peak, star HDR and the transfers (they
    cannot change an SDR frame); HDR gives ``hdrTransfer`` for the ticked presets only. ``travel`` None or not given
    leaves it out (the pipeline takes its default for the target and distance).
    """
    hdr = ctx.dynamic == "hdr"
    out: dict[str, Any] = {}
    transfer: dict[str, str] = {}
    for opt in OPTIONS:
        if opt.key == "travel":
            continue
        value = values.get(opt.saved, opt.default)
        if opt.preset:
            if hdr and opt.preset in ctx.presets:
                transfer[opt.preset] = value
        elif hdr or opt.enabled_when != HDR_ONLY:
            out[opt.key] = value
    if hdr and transfer:
        out["hdrTransfer"] = transfer
    if travel is not None:
        out["travel"] = float(travel)
    return out


def options_from_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """The ``FlyJob.options`` of a dialog's values (``siril_bridge.job_from_dialog``): the controls' keys, plus ``dynamic``,
    ``presets`` and ``travel``. Values with none of the controls' keys (the CLI's, a test's) give ``{}``."""
    present = {k: values[k] for k in saved_keys() if k in values}
    travel = values.get("travel")
    if not present and travel is None:
        return {}
    ctx = Context(str(values.get("dynamic", "sdr")), tuple(values.get("presets") or ["youtube_1080"]),
                  str(values.get("target_type", "nebula")))
    return job_options({**defaults(), **present}, ctx, travel)


def travel_note(travel: Optional[float], target_type: str, distance: Optional[float]) -> str:
    """The grey note beside Travel (FlyThrough.js 1917): "capped at 0.9 x the distance" when a nebula's travel is capped;
    for an empty Travel the automatic one ("automatic: 390 pc", parallax.default_travel), or "automatic" while the
    distance is not known."""
    known = distance is not None and (target_type == "galaxy" or math.isfinite(distance))
    if travel is None:
        if not known:
            return "automatic"
        return f"automatic: {parallax.default_travel(target_type, distance):.3g} pc"
    if known and parallax.clamp_travel(travel, target_type, distance)["clamped"]:
        return "capped at 0.9 \u00d7 the distance"
    return ""
