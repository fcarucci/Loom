"""Publish the final plates as the run's results, and export them as 16-bit TIFFs and a layered PSB.

A port of Loom's publishOutputs and export path: lib/Pipeline.js:592-626 (publish), :627-732 (ensureExportDir,
exportPsbLayer, usableResultNames, exportResults), lib/Steps.js:225-244 (LOOM_OUTPUT_KEYWORD, markAsOutput),
:2754-2819 (exportTiff16), :3960-4037 (psbProfileOrNull, exportPsb) and lib/StepsIcc.js:248-322 (profile plan and
assignment, with the log texts). The naming, ICC, PSB and TIFF pieces come from their own modules; nothing here
re-implements them.

Every function takes ``log``: a callable ``log(level, text)`` with level ``"info"``, ``"warn"`` or ``"error"``.

Integrator note: :func:`export` loads the plates in Siril (it reads the built-in profile from them), so call
:func:`leave_main_result` only AFTER :func:`export`, or the main result is no longer the loaded image.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, Optional

from loomsiril.io_fits import _parse_header, read_fits
from loomsiril.io_tiff import write_tiff16
from loomsiril.loom import icc, naming, psb_document
from loomsiril.loom.psb import write_psb

__all__ = ["export", "leave_main_result", "publish"]

#: The FITS keyword that marks a plate as a Loom output (lib/Steps.js:225-244).
LOOM_OUTPUT_KEYWORD = "LOOMOUT"
LOOM_OUTPUT_COMMENT = "Loom pipeline output"
#: The FITS keyword that names the colour master and planes a result comes from, as Loom writes it.
LOOM_SOURCE_KEYWORD = "LOOMSRC"
LOOM_SOURCE_COMMENT = "colour master and planes"

#: Loom's text when the results are linear and the stretch is off (lib/Pipeline.js:exportResults).
LINEAR_REFUSAL = (
    "not exporting: the results are linear, and 16-bit TIFF would posterise them. Enable the stretch, or "
    "save from Siril in a format that holds floating point."
)

Log = Callable[[str, str], Any]


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


_FITS_BLOCK = 2880


def _is_colour(path: str) -> bool:
    """True when the FITS file has three or more planes. Reads the header blocks only, never the pixels."""
    data = b""
    with open(path, "rb") as fh:
        while True:
            block = fh.read(_FITS_BLOCK)
            if not block:
                raise ValueError(f"{path}: FITS header has no END card")
            data += block
            try:
                header, _offset = _parse_header(data)
                break
            except ValueError:
                continue
    return int(header.get("NAXIS", 0)) == 3 and int(header.get("NAXIS3", 0)) >= 3


def publish(
    ops: Any,
    plates: Mapping[str, str],
    config: Optional[Mapping[str, Any]],
    output_dir: str | os.PathLike[str],
    plan: Mapping[str, Any],
    log: Log,
    sources: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Load, mark, assign a profile to and save each plate as a result. Returns ``{name: output path}``.

    ``sources`` maps a result name to its LOOMSRC value (the colour master's file and planes: the composite and the
    palettes of a colour run). The keyword is written on those results in their FITS form, replacing any earlier
    value; a value Siril cannot take is a warning and the result is still saved. The TIFF and PSB exports hold no FITS
    keywords and carry none.

    The plates are visited in :func:`naming.ordered_output_keys` order. Each is saved under a unique name in
    ``output_dir``: an existing file is never overwritten (``_1``, ``_2``, ... are appended). Companions (RGB_linear,
    the palette _linear plates, L_stars_low and L_stars_high) are plates like the others.
    """
    folder = os.fspath(output_dir)
    os.makedirs(folder, exist_ok=True)
    outputs: dict[str, str] = {}
    for name in naming.ordered_output_keys(list(plates)):
        plate = os.fspath(plates[name])
        ops.load(plate)
        ops.update_key(LOOM_OUTPUT_KEYWORD, name, LOOM_OUTPUT_COMMENT)
        if sources and name in sources:
            try:
                ops.update_key(LOOM_SOURCE_KEYWORD, sources[name], LOOM_SOURCE_COMMENT)
            except ValueError as exc:
                log("warn", f"{name}: the source (LOOMSRC) was not recorded ({exc})")
        assignment = icc.assignment_for(_is_colour(plate), plan)
        ops.icc_assign(assignment.target)
        log("info", icc.assignment_log(name, assignment))
        if assignment.warning:
            log("warn", assignment.warning)
        outputs[name] = os.fspath(ops.save(naming.unique_file_name(folder, name)))
    return outputs


def leave_main_result(ops: Any, outputs: Mapping[str, str], log: Log) -> None:
    """Load the main result last, so Siril shows it (:func:`naming.main_result`). Nothing when there is none."""
    name = naming.main_result(outputs.keys())
    if name is not None:
        ops.load(outputs[name])


def export(
    ops: Any,
    outputs: Mapping[str, str],
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    log: Log,
) -> None:
    """Write the layered PSB (when asked), then one 16-bit TIFF per result, into ``config["exportDir"]``.

    Nothing is exported when no folder is set, or when the stretch is off (the linear refusal is logged and nothing is
    written). A failed PSB is logged and the TIFFs still run; a failed TIFF is a warning.
    """
    export_dir = config.get("exportDir")
    if not export_dir:
        return
    export_dir = os.fspath(export_dir)
    if not config.get("stretch"):
        log("warn", LINEAR_REFUSAL)
        return
    try:
        os.makedirs(export_dir, exist_ok=True)
    except OSError as exc:
        log("error", f"could not create {export_dir}: {exc}")
        return

    if config.get("exportPsb"):
        _export_psb_layer(ops, outputs, config, plan, log)

    names = sorted(outputs)
    written = 0
    for name in names:
        try:
            path = os.path.join(export_dir, naming.export_file_name(name) + ".tif")
            plate = os.fspath(outputs[name])
            planes, _header = read_fits(plate)
            assignment = icc.assignment_for(len(planes) >= 3, plan)
            profile = _plate_profile(ops, plate, assignment.target)
            write_tiff16(path, planes, icc=profile)
            log("info", f"16-bit TIFF {path}")
            written += 1
        except Exception as exc:  # a failed TIFF is a warning; the other results are still written
            log("warn", f"{name} could not be exported ({exc})")
    log("info", f"{written} of {len(names)} result(s) written to {export_dir}")


# ---------------------------------------------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------------------------------------------


def _plate_profile(ops: Any, plate: str, target: str) -> Optional[bytes]:
    """The profile bytes a plate carries: the file's bytes, or for the built-in sRGB what Siril reports after
    assigning it."""
    if target == icc.BUILTIN_SRGB:
        return _builtin_profile(ops, plate)
    return _read_bytes(target)


def _builtin_profile(ops: Any, plate: str) -> bytes:
    ops.load(plate)
    ops.icc_assign(icc.BUILTIN_SRGB)
    data = ops.icc_profile()
    if not data:
        raise ValueError("Siril reports no profile for the built-in sRGB")
    return bytes(data)


def _iter_pixel_nodes(nodes: Sequence[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]:
    """Pixel layers of a converted PSB tree, in document order (bottom first, depth first)."""
    for node in nodes:
        if node.get("kind") == "group":
            yield from _iter_pixel_nodes(node["children"])
        elif node.get("kind") == "pixel":
            yield node


# ---------------------------------------------------------------------------------------------------------------
# Layered PSB
# ---------------------------------------------------------------------------------------------------------------


def _export_psb_layer(ops: Any, outputs: Mapping[str, str], config: Mapping[str, Any], plan: Mapping[str, Any],
                      log: Log) -> None:
    try:
        export_psb(ops, outputs, config, plan, log)
    except Exception as exc:
        log("error", f"the layered PSB was not written ({exc}); the individual TIFFs are unaffected")


def export_psb(ops: Any, outputs: Mapping[str, str], config: Mapping[str, Any], plan: Mapping[str, Any],
               log: Log) -> str:
    """Write ``<exportDir>/<psb base name>.psb`` from the results (lib/Steps.js:3960-4037 exportPsb).

    Raises when the document is empty, the path cannot be cleared, or the plates differ in size; in each case nothing
    is written. Every pixel layer is read from its published FITS as top-down planes; the write quantises them to 16
    bits, and the FITS files are not modified.
    """
    doc = psb_document.build_psb_document(outputs)
    if not doc:
        raise ValueError("there is nothing to put in a PSB")
    export_dir = os.fspath(config.get("exportDir"))
    path = os.path.join(export_dir, naming.export_file_name(naming.psb_base_name(config)) + ".psb")
    if os.path.lexists(path):
        try:
            os.remove(path)
        except OSError as exc:
            raise RuntimeError(f"cannot overwrite {path}: {exc}") from exc

    cache: dict[str, list[Any]] = {}

    def planes_for(handle: Any) -> list[Any]:
        key = os.fspath(handle)
        if key not in cache:
            cache[key] = read_fits(key)[0]
        return cache[key]

    tree = psb_document.to_psb_tree(doc, planes_for)
    pixels = list(_iter_pixel_nodes(tree))
    if not pixels:
        raise ValueError("there is nothing to put in a PSB")
    # Geometry comes from the first pixel layer; every layer must match it (Psb.write requires that).
    first = pixels[0]["planes"][0]
    height, width = int(first.shape[0]), int(first.shape[1])
    sizes = [(int(n["planes"][0].shape[1]), int(n["planes"][0].shape[0])) for n in pixels]
    psb_document.assert_plates_share_size(sizes, width, height)

    icc_bytes = _psb_icc(ops, outputs, plan, log)
    write_psb(path, tree, width, height, icc_bytes)
    log("info", f"wrote {path} ({width}x{height}, {len(pixels)} layers)")
    return path


def _psb_icc(ops: Any, outputs: Mapping[str, str], plan: Mapping[str, Any], log: Log) -> Optional[bytes]:
    """The profile the PSB carries (lib/Steps.js psbProfileOrNull). A failure is a warning and an untagged PSB."""
    colour = [outputs[name] for name in outputs if _is_colour(os.fspath(outputs[name]))]
    rgb_in_use: Optional[str] = icc.assignment_for(True, plan).name if colour else None

    def profile_bytes_named(name: str) -> Optional[bytes]:
        path = plan.get("paths", {}).get("RGB", {}).get(name)
        if path:
            return _read_bytes(path)
        if name == icc.PROFILE_SRGB and colour:
            return _builtin_profile(ops, os.fspath(colour[0]))
        raise ValueError(f"no installed profile named {name}")

    try:
        profile, warning = icc.psb_profile_bytes(rgb_in_use, profile_bytes_named, _read_bytes, os.path.exists)
    except Exception as exc:
        log("warn", f"the PSB will be untagged ({exc})")
        return None
    if warning:
        log("warn", warning)
    return psb_document.psb_profile_or_none(profile)
