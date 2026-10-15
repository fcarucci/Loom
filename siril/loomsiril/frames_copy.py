"""Frame Selector: copying the approved frames to a folder, and the output routine that writes them.

Ported from ``pixinsight/script/FrameSelector.js``:

- ``runOutputRoutine`` (3954-3981) is replaced by :func:`run_output_routine` and :func:`write_output`. PixInsight
  writes XISF through SubframeSelector's output routine; here a FITS source is copied byte for byte and any other
  source (XISF) is read through Siril and written as FITS. The measurement pass that the JS runs first is not
  repeated here: the caller measures before it exports.
- ``convertInPlace`` (3994-4014), ``convertGroup`` (4020-4051) and ``settleConverted`` (4057-4079) are NOT ported.
  Deviation: the Siril port does not convert frames in place after a delete; kept frames stay exactly as they are.
  :func:`convert_in_place` is kept as a no-op entry point that returns no outcomes.
- ``exportApproved`` (4092-4139) is :func:`export_approved`.
- ``emptyDirectory`` (4147-4157) is :func:`empty_directory`.
- ``removeEntry`` (4159-4180) is :func:`remove_entry`.
- ``exportRefusal`` (4187-4195) is :func:`export_refusal`.
- ``confirmWritten`` (4201-4211) is :func:`confirm_written`.

The pure rules (output names and their collisions, the refusal for an output folder that is or contains a source
folder, the home folder or the filesystem root) are in :mod:`loomsiril.frames_files` and are called from there, not
copied. Output names: a FITS source keeps its own name (``.fit``, ``.fits``, ``.fts``, optionally ``.fz``); any other
source is written as ``.fit``. A FITS source is copied with its modification time kept.
"""

from __future__ import annotations

import math
import os
import re
import shutil
from typing import Any, Optional

import numpy as np

from loomsiril import frames_files as Fi
from loomsiril import io_fits
from loomsiril.siril_bridge import planes_from_pixeldata, top_down_header

#: A FITS frame: .fit, .fits or .fts, optionally tile-compressed (.fz). Matched on the whole path, case-blind.
FITS_SOURCE = re.compile(r"\.(fit|fits|fts)(\.fz)?\Z", re.IGNORECASE)

#: Header keywords that describe the file layout, not the image: io_fits writes its own.
_STRUCTURAL = {"SIMPLE", "BITPIX", "EXTEND", "BZERO", "BSCALE", "END", "COMMENT", "HISTORY", "LOOMROWS"}

_INT = re.compile(r"[+-]?\d+")


def is_fits_source(path: str) -> bool:
    """Is the frame a FITS file (optionally tile-compressed)? Anything else is written out as FITS by Siril."""
    return FITS_SOURCE.search(path) is not None


def _card_value(rest: str) -> Any:
    """The value of one header card (the text after ``= ``), or None when it is not a plain value io_fits can write."""
    s = rest.lstrip()
    if s.startswith("'"):
        chars: list[str] = []
        i = 1
        while i < len(s):
            if s[i] == "'":
                if i + 1 < len(s) and s[i + 1] == "'":
                    chars.append("'")
                    i += 2
                    continue
                break
            chars.append(s[i])
            i += 1
        text = "".join(chars).rstrip()
        return text if text.isascii() else None
    body = s.split("/", 1)[0].strip()
    if body == "T":
        return True
    if body == "F":
        return False
    if _INT.fullmatch(body):
        return int(body)
    try:
        f = float(body.replace("D", "E"))
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _header_from_text(text: str) -> dict[str, Any]:
    """Keyword -> value from a FITS header as text, one 80-character card per line. The structural keywords and the
    ones that cannot be written back (non-ASCII text, non-finite numbers) are left out."""
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if len(line) < 10 or line[8:10] != "= ":
            continue
        key = line[:8].strip()
        if not key or key in _STRUCTURAL or key.startswith("NAXIS") or not key.isascii():
            continue
        value = _card_value(line[10:])
        if value is not None:
            out[key] = value
    return out


def write_output(src: str, dst: str, siril: Any = None) -> None:
    """Write one approved frame to ``dst``.

    A FITS source is copied byte for byte and its modification time kept. Any other source is read through
    ``siril.load_image_from_file(src, with_pixels=True)`` (which leaves the image open in Siril alone), its rows are
    turned top-down, and it is written as FITS: 16-bit unsigned for uint16 data, 32-bit float otherwise. The header
    is the one the loaded file carries, with the keywords io_fits can write.
    """
    if is_fits_source(src):
        shutil.copyfile(src, dst)
        shutil.copystat(src, dst)
        return
    if siril is None:
        raise ValueError("no Siril connection to read " + src)
    loaded = siril.load_image_from_file(src, with_pixels=True)
    data = np.asarray(loaded.data)
    planes = planes_from_pixeldata(data)
    bitpix = 16 if data.dtype == np.uint16 else -32
    header = top_down_header(_header_from_text(str(getattr(loaded, "header", "") or "")), planes[0].shape[0])
    io_fits.write_fits(dst, planes, header, bitpix=bitpix)


def run_output_routine(sources: list[str], mapping: dict[str, str], siril: Any = None) -> bool:
    """Write every source to its mapped output, in order. An error from one of them propagates; the caller decides."""
    for src in sources:
        write_output(src, mapping[src], siril)
    return True


def convert_in_place(paths: Optional[list[str]]) -> dict[str, Any]:
    """Entry point kept for the state machine. The Siril port does not convert in place: the frames are left as
    they are, so this returns the result shape with nothing in it and touches no file."""
    return {"converted": 0, "failed": 0, "alreadyXisf": 0, "refused": None, "outcomes": {}}


def _mapping(approved: list[str], destination: str) -> dict[str, Any]:
    """Where each approved frame will be written. FITS sources keep their own names; the others become ``.fit``.
    Computed by output_mapping for each kind, then the two kinds are checked against each other."""
    fits = [p for p in approved if is_fits_source(p)]
    other = [p for p in approved if not is_fits_source(p)]
    a = Fi.output_mapping(fits, destination, None) if fits else {"mapping": {}, "collisions": [], "aliased": False}
    b = Fi.output_mapping(other, destination, ".fit") if other else {"mapping": {}, "collisions": [], "aliased": False}
    taken = {Fi.output_name(v) for v in a["mapping"].values()}
    cross: list[str] = []
    for p in other:
        name = Fi.output_name(b["mapping"][p])
        if name in taken:
            cross.append(name)
        taken.add(name)
    return {"mapping": {**a["mapping"], **b["mapping"]},
            "collisions": a["collisions"] + b["collisions"] + cross,
            "aliased": a["aliased"] or b["aliased"]}


def export_refusal(m: dict[str, Any]) -> Optional[str]:
    """Why an export must not start, or None. Two sources sharing an output name would overwrite each other; a
    destination that is a source folder would write over the originals."""
    if m["collisions"]:
        return "two or more frames would be written to the same name: " + ", ".join(m["collisions"])
    if m["aliased"]:
        return "the destination is one of the source folders"
    return None


def confirm_written(written: list[str], m: dict[str, Any], result: dict[str, Any]) -> None:
    """Each output is checked on disk rather than assumed from the write returning, so a retry knows which outputs
    this run produced."""
    for s in written:
        if os.path.exists(m["mapping"][s]):
            result["outcomes"][s] = "written"
            result["written"] += 1
        else:
            result["outcomes"][s] = "missing"
            result["failed"] += 1


def remove_entry(path: str, folder: bool, out: dict[str, Any]) -> None:
    """Remove one entry of an output folder: a folder is emptied and removed, a file or a symbolic link (never
    followed) is unlinked. A folder that is not empty stays, and its leftovers are reported."""
    try:
        if folder:
            inner = empty_directory(path)
            out["removed"] += inner["removed"]
            out["failed"] += inner["failed"]
            if inner["failed"]:
                return
            os.rmdir(path)
        else:
            os.remove(path)
        out["removed"] += 1
    except OSError:
        out["failed"].append(path)


def empty_directory(path: str) -> dict[str, Any]:
    """Remove everything inside a folder (hidden entries and subfolders too), leaving the folder itself. A symbolic
    link is removed as a link and never followed. Returns what was removed and what could not be."""
    out: dict[str, Any] = {"removed": 0, "failed": []}
    with os.scandir(path) as it:
        entries = list(it)
    for e in entries:
        remove_entry(os.path.join(path, e.name), e.is_dir(follow_symlinks=False), out)
    return out


def export_approved(approved: Optional[list[str]], destination: Optional[str], siril: Any = None,
                    home: Optional[str] = None) -> dict[str, Any]:
    """Write the approved frames into ``destination``, emptying it first.

    Refused before anything is removed when two frames would share an output name, when the destination is a source
    folder, or when it is, or contains, a source folder, the filesystem root or the home folder. If the folder cannot
    be fully emptied nothing is copied. ``outcomes`` says per frame whether it was "written" or is "missing".
    """
    result: dict[str, Any] = {"written": 0, "failed": 0, "emptied": 0, "refused": None, "outcomes": {}}
    if approved is None or len(approved) == 0:
        return result
    if destination is None or str(destination).strip() == "":
        result["refused"] = Fi.empty_refusal(approved, destination, home)
        return result
    m = _mapping(approved, destination)
    result["refused"] = export_refusal(m) or Fi.empty_refusal(
        approved, destination, home if home is not None else os.path.expanduser("~"))
    if result["refused"] is not None:
        return result
    try:
        os.makedirs(destination, exist_ok=True)
        emptied = empty_directory(destination)
        result["emptied"] = emptied["removed"]
        if emptied["failed"]:
            result["refused"] = ("could not empty the output folder (" + str(len(emptied["failed"])) +
                                 " item(s), e.g. " + emptied["failed"][0] + "); nothing was copied")
            return result
        run_output_routine(approved, m["mapping"], siril)
    except Exception as e:  # noqa: BLE001 - reported to the caller as the refusal, as the JS does
        result["refused"] = str(e)
        return result
    confirm_written(approved, m, result)
    return result
