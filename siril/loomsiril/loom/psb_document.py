"""The layered PSB document: which groups and layers the export contains, in which order, with which blend modes,
visibility, clipping and names.

A port of the document builder in Loom's ``lib/Steps.js`` (cited by line range in each docstring). Pixels are not
involved here. Each pixel layer refers to a result by name, through an opaque handle the caller supplies; the
caller attaches the planes later with :func:`to_psb_tree`, which produces the node shape that
:func:`loomsiril.loom.psb.write_psb` accepts.

Every node built here is a dict with the same nine keys, so trees compare by equality:

* ``name``: the layer or group name.
* ``blend``: the four-character blend key, or ``None`` for the writer's default (pass-through for groups, normal
  for layers).
* ``visible``, ``clipping``: booleans.
* ``curves``: a list of channel ids (0 = composite, 1 = red, 2 = green, 3 = blue) for a Curves layer, else ``None``.
* ``hue_saturation``, ``vibrance``: booleans, for the two adjustment layers that carry no curve data.
* ``window``: the caller's handle for a pixel layer, else ``None``.
* ``group``: the children, bottom first, for a group; ``None`` otherwise.

The result names are the keys of the ``results`` mapping: ``RGB``, ``RGB_starless``, ``RGB_stars``, ``SHO_starless``,
``HOO``, ``L_stars``, ``L_stars_low``, ``L_stars_high`` and so on. A value that is ``None`` counts as absent.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from loomsiril.loom.psb import (
    BLEND_LUMINOSITY,
    BLEND_PASS_THROUGH,
    BLEND_SCREEN,
    BLEND_SOFT_LIGHT,
)

# lib/Steps.js:3726 Steps.CURVE_CHANNEL_LETTERS: index 0 is the composite, then red, green, blue.
CURVE_CHANNEL_LETTERS = ("RGB", "R", "G", "B")

# lib/Util.js:1461-1465 Util.PALETTES: the 1-based channel order each palette puts its lines in (H, S or O).
PALETTES: dict[str, list[str]] = {
    "SHO": ["S", "H", "O"],
    "HOO": ["H", "O", "O"],
    "HSO": ["H", "S", "O"],
}

_SIZE_MESSAGE = "plates differ in size ({w}x{h} against {W}x{H}); they cannot share one document"


def _present(results: Mapping[str, Any], key: str) -> bool:
    return results.get(key) is not None


def _node(
    name: str,
    *,
    blend: str | None = None,
    visible: bool = True,
    clipping: bool = False,
    curves: list[int] | None = None,
    hue_saturation: bool = False,
    vibrance: bool = False,
    window: Any = None,
    group: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One node with every key present (lib/Steps.js node literals, defaults made explicit)."""
    return {
        "name": name,
        "blend": blend,
        "visible": bool(visible),
        "clipping": bool(clipping),
        "curves": None if curves is None else list(curves),
        "hue_saturation": bool(hue_saturation),
        "vibrance": bool(vibrance),
        "window": window,
        "group": group,
    }


def _pixel(name: str, window: Any, **kw: Any) -> dict[str, Any]:
    return _node(name, window=window, **kw)


def _curves(name: str, channels: list[int], **kw: Any) -> dict[str, Any]:
    return _node(name, curves=channels, **kw)


def _group(name: str, children: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return _node(name, group=children, **kw)


def curve_layer_name(label: str, channel_ids: Sequence[int]) -> str:
    """``label[R]``, ``label[G,B]``: the channel is in the name because the panel cannot show it.

    lib/Steps.js:3728-3735 Steps.curveLayerName.
    """
    letters = [CURVE_CHANNEL_LETTERS[int(c)] for c in channel_ids]
    return label + "[" + ",".join(letters) + "]"


def dso_adjustments() -> list[dict[str, Any]]:
    """Background tone, faint nebulosity and Vibrance, bottom to top; all neutral and visible.

    lib/Steps.js:3806-3812 Steps.psbDsoAdjustments.
    """
    return [
        _curves("Background Curve", [0]),
        _curves("Faint Nebulosity Curve", [0]),
        _node("Color Vibrance", vibrance=True),
    ]


def palette_names(results: Mapping[str, Any]) -> list[str]:
    """Every palette with a plate in ``results``, in :data:`PALETTES` order.

    lib/Steps.js:3823-3834 Steps.psbPaletteNames.
    """
    names: list[str] = []
    for pk in PALETTES:
        if _present(results, pk + "_starless") or _present(results, pk):
            names.append(pk)
    return names


def palette_name(results: Mapping[str, Any]) -> str | None:
    """The first palette with a plate, or None. lib/Steps.js:3817-3821 Steps.psbPaletteName."""
    names = palette_names(results)
    return names[0] if names else None


def palette_channels_of(palette_map: Sequence[str], key: str) -> list[int]:
    """The 1-based channels (1 red, 2 green, 3 blue) a palette puts an emission line in.

    lib/Steps.js:3836-3845 Steps.paletteChannelsOf.
    """
    return [i + 1 for i, k in enumerate(palette_map) if k == key]


def palette_group(results: Mapping[str, Any], name: str | None = None) -> dict[str, Any] | None:
    """The palette group: the plate, then one Curves layer per emission line, bottom to top.

    The channel of each line comes from :data:`PALETTES`, so Ha is green under SHO and red under HOO and HSO.
    Under HOO, OIII fills green and blue and gets one curve covering both. None without a palette plate.

    lib/Steps.js:3856-3887 Steps.psbPaletteGroup.
    """
    pal = name or palette_name(results)
    if pal is None:
        return None
    plate = pal + "_starless" if _present(results, pal + "_starless") else pal
    layers = [_pixel(plate, results.get(plate))]
    palette_map = PALETTES.get(pal, [])
    # Bottom to top: OIII, SII, Ha (the panel reads Ha, SII, OIII downwards).
    for label, key in (("OIII", "O"), ("SII", "S"), ("Ha", "H")):
        chans = palette_channels_of(palette_map, key)
        if chans:
            layers.append(_curves(curve_layer_name(label, chans), chans))
    return _group(pal, layers)


def broadband_group(results: Mapping[str, Any], shown: bool) -> dict[str, Any] | None:
    """The broadband RGB group: the starless plate if there is one, else the RGB plate; None without either.

    Both the group and its plate are hidden when a palette carries the colour (``shown`` false).

    lib/Steps.js:3890-3903 Steps.psbBroadbandGroup.
    """
    if _present(results, "RGB_starless"):
        plate = "RGB_starless"
    elif _present(results, "RGB"):
        plate = "RGB"
    else:
        return None
    return _group("RGB", [_pixel(plate, results[plate], visible=bool(shown))], visible=bool(shown))


def l_stars_layer(results: Mapping[str, Any]) -> dict[str, Any] | None:
    """The L stars layer: a Luminosity layer, or the separated pair in a pass-through group.

    With both halves, the group holds L_stars_low (hidden) and L_stars_high in Soft Light; the plain L_stars plate
    is then ignored. Without them, L_stars is a Luminosity layer. None without either.

    lib/Steps.js:3934-3946 Steps.psbLStarsLayer.
    """
    if _present(results, "L_stars_low") and _present(results, "L_stars_high"):
        return _group(
            "L Stars",
            [
                _pixel("L_stars_low", results["L_stars_low"], visible=False),
                _pixel("L_stars_high", results["L_stars_high"], blend=BLEND_SOFT_LIGHT),
            ],
            blend=BLEND_PASS_THROUGH,
        )
    if _present(results, "L_stars"):
        return _pixel("L_stars", results["L_stars"], blend=BLEND_LUMINOSITY)
    return None


def star_layers(results: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The layers of the Stars group, bottom to top: RGB stars with a clipped saturation layer, then L stars.

    Empty when there is no RGB star plate and no L star plate.

    lib/Steps.js:3905-3932 Steps.psbStarLayers.
    """
    stars: list[dict[str, Any]] = []
    if _present(results, "RGB_stars"):
        stars.append(_pixel("RGB_stars", results["RGB_stars"]))
        # Clipped to RGB_stars directly below, so it colours the star plate alone.
        stars.append(_node("Stars Saturation", hue_saturation=True, clipping=True))
    lstars = l_stars_layer(results)
    if lstars is not None:
        stars.append(lstars)
    return stars


def build_psb_document(results: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The whole document, bottom layer first, for the result names present in ``results``.

    Order: one group per palette (the first visible, the others hidden); the broadband RGB group; the DSO adjustment
    layers (only when a palette or broadband group exists); then the Stars group in Screen with a Stars Curve clipped
    to it. An empty mapping gives an empty document.

    lib/Steps.js:3763-3804 Steps.buildPsbDocument.
    """
    doc: list[dict[str, Any]] = []
    palettes = palette_names(results)
    for pi, pal in enumerate(palettes):
        group = palette_group(results, pal)
        if group is None:
            continue
        if pi > 0:
            group["visible"] = False
        doc.append(group)
    # Without a palette, the RGB plate is the picture and is shown; with one, the palette carries the colour.
    broadband = broadband_group(results, len(palettes) == 0)
    if broadband is not None:
        doc.append(broadband)
    if doc:
        doc.extend(dso_adjustments())
    stars = star_layers(results)
    if stars:
        doc.append(_group("Stars", stars, blend=BLEND_SCREEN))
        # Clipped to the Stars group: it curves the stars as they come out of the screen blend, and nothing beneath.
        doc.append(_curves("Stars Curve", [0], clipping=True))
    return doc


def to_psb_tree(
    doc: Sequence[dict[str, Any]],
    planes_for: Callable[[Any], Sequence[Any]],
) -> list[dict[str, Any]]:
    """Convert a document from :func:`build_psb_document` to the node shape :func:`psb.write_psb` accepts.

    ``planes_for(handle)`` returns the one or three top-down 2-D planes of a pixel layer's handle. Groups become
    ``{"kind": "group", ...}``, Curves layers ``{"kind": "curves", "channels": ...}``, and the two adjustment flags
    ``{"kind": "hue"}`` and ``{"kind": "vibrance"}``. ``blend`` is omitted when the document leaves it to the
    writer's default.
    """

    def convert(node: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"name": node["name"], "visible": node["visible"], "clipping": node["clipping"]}
        if node["blend"] is not None:
            out["blend"] = node["blend"]
        if node["group"] is not None:
            out["kind"] = "group"
            out["children"] = [convert(child) for child in node["group"]]
        elif node["curves"] is not None:
            out["kind"] = "curves"
            out["channels"] = list(node["curves"])
        elif node["hue_saturation"]:
            out["kind"] = "hue"
        elif node["vibrance"]:
            out["kind"] = "vibrance"
        else:
            out["kind"] = "pixel"
            out["planes"] = list(planes_for(node["window"]))
        return out

    return [convert(node) for node in doc]


def assert_plates_share_size(
    sizes: Sequence[tuple[int, int]],
    width: int | None = None,
    height: int | None = None,
) -> None:
    """Every plate after the first must match the document size; they cannot share one document otherwise.

    ``sizes`` holds ``(width, height)`` per plate, first plate first. The size to match is ``width`` by ``height``
    when given, else the first plate's size. The first entry is compared only in the second form, as in the JS.

    lib/Steps.js:3948-3959 Steps.assertPlatesShareSize (the message is the same).
    """
    if not sizes:
        return
    if width is None or height is None:
        width, height = sizes[0]
    for w, h in sizes[1:]:
        if int(w) != int(width) or int(h) != int(height):
            raise ValueError(_SIZE_MESSAGE.format(w=w, h=h, W=width, H=height))


def psb_profile_or_none(profile: bytes | bytearray | Sequence[int] | None) -> bytes | None:
    """The ICC profile bytes to embed, or None for an untagged document.

    The choice of profile belongs to the ICC module: the caller passes its bytes in. An empty profile means
    untagged. lib/Steps.js:3960-3977 Steps.psbProfileOrNull (the JS catches a failure to read the profile and warns;
    the caller here does that by passing None).
    """
    if profile is None or len(profile) == 0:
        return None
    return bytes(profile)
