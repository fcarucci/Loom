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

The JS string rules (output names and their collisions, a destination that is or contains a source folder, the home
folder or the filesystem root) are in :mod:`loomsiril.frames_files` and run first, unchanged. On top of them this
module adds a safety layer that works on resolved paths, because the string rules see only the spelling:

- the destination must be absolute and must not be a symbolic link (or a Windows junction); it is not followed;
- the destination, every source folder, its parent and the home folder are resolved with ``os.path.realpath`` and
  compared after ``os.path.normcase``; on macOS and Windows the comparison is also case-folded. ``samefile`` and the
  refusal messages use the resolved paths in their own case, never the folded comparison keys. A destination that is
  the filesystem root, the home folder, or equal to or an ancestor of a source folder, its parent or the home folder
  is refused; so is one that is the same directory (``os.path.samefile``) as any of them;
- the same checks run again right before anything is removed;
- output names are compared case-folded on macOS and Windows, so ``a.fit`` and ``A.fit`` collide there; elsewhere the
  created destination is probed (a uniquely named file looked up under its swapped case, then removed by its exact
  name) and, when that file system folds case (exFAT, a mounted APFS or SMB share), the names are compared folded too,
  before anything is removed;
- an output that turns out to be the same file (device and inode) as an earlier output of the run is reported
  "missing" for the earlier frame(s): only the last one written survives in it;
- emptying never follows a symbolic link or a junction; a junction is removed as a directory link;
- a copied non-FITS source drops the keywords that describe Siril's own row order and checksums (ROWORDER, CHECKSUM,
  DATASUM), which would be wrong for the new file;
- if writing stops part way, the outputs already on disk are still reported per frame.

A FITS source is copied with its modification time kept.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import uuid
from typing import Any, Optional

import numpy as np

from loomsiril import frames_files as Fi
from loomsiril import io_fits
from loomsiril.siril_bridge import planes_from_pixeldata, top_down_header

#: A FITS frame: .fit, .fits or .fts, optionally tile-compressed (.fz). Matched on the whole path, case-blind.
FITS_SOURCE = re.compile(r"\.(fit|fits|fts)(\.fz)?\Z", re.IGNORECASE)

#: Names compare case-folded on the platforms whose file systems usually fold case (macOS, Windows).
_CASE_FOLD = sys.platform in ("darwin", "win32")

#: Header keywords that describe the file layout or Siril's own row order and checksums: not copied to a new file.
_STRUCTURAL = {"SIMPLE", "BITPIX", "EXTEND", "BZERO", "BSCALE", "END", "COMMENT", "HISTORY", "LOOMROWS",
               "ROWORDER", "CHECKSUM", "DATASUM"}

_INT = re.compile(r"[+-]?\d+")

#: Windows FILE_ATTRIBUTE_REPARSE_POINT: junctions and other reparse points.
_REPARSE_POINT = 0x400


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
    """Keyword -> value from a FITS header as text, one 80-character card per line. The structural keywords (see
    ``_STRUCTURAL``) and the ones that cannot be written back (non-ASCII text, non-finite numbers) are left out."""
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
    is the one the loaded file carries, minus the keywords in ``_STRUCTURAL``.
    """
    if is_fits_source(src):
        shutil.copyfile(src, dst)
        shutil.copystat(src, dst)
        return
    if siril is None:
        raise ValueError("no Siril connection to read " + src)
    loaded = siril.load_image_from_file(src, with_pixels=True)
    if loaded is None or getattr(loaded, "data", None) is None:
        raise RuntimeError("could not read " + src)
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


# ---- paths -----------------------------------------------------------------------------------------------

def _resolve(path: str) -> str:
    """The path with symbolic links, "." and ".." resolved and normalised, keeping its letter case: a path that can
    still be opened, given to ``samefile`` and shown to the user."""
    return os.path.normcase(os.path.realpath(path))


def _fold(resolved: str) -> str:
    """The comparison key of a resolved path: case-folded where names fold case. Only for comparing strings: on a
    case-sensitive volume (possible on macOS) the folded spelling may name no folder at all."""
    return resolved.casefold() if _CASE_FOLD else resolved


def _canon(path: str) -> str:
    """The path with symbolic links, "." and ".." resolved, normalised, and case-folded where names fold case."""
    return _fold(_resolve(path))


def _inside(child: str, parent: str) -> bool:
    """Is ``child`` the same path as ``parent`` or below it? Both canonical."""
    if child == parent:
        return True
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def _same_dir(a: str, b: str) -> bool:
    """Are the two paths the same existing directory (same device and inode)? False when either does not exist."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _is_junction(path: str) -> bool:
    """A Windows junction (or another reparse point). Always False off Windows."""
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        return bool(isjunction(path))
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _is_link(path: str) -> bool:
    """A symbolic link or a junction: removed as a link, never followed."""
    return os.path.islink(path) or _is_junction(path)


def _path_refusal(approved: list[str], destination: str, home: Optional[str]) -> Optional[str]:
    """Why the destination must not be emptied, checked on resolved paths, or None. Run before anything is removed,
    and again just before the emptying."""
    if not os.path.isabs(destination):
        return "the output folder must be an absolute path"
    if _is_link(destination):
        return "the output folder is a symbolic link or junction; it is not followed"
    # Strings are compared on the folded keys; samefile and the messages get the resolved paths, in their own case:
    # a folded path may not exist on a case-sensitive volume, and samefile on it would always say "no".
    dst = _canon(destination)
    if os.path.dirname(dst) == dst:
        return "refusing to empty the filesystem root"
    home_dir = home if home is not None else os.path.expanduser("~")
    home_real = _resolve(home_dir)
    home_canon = _fold(home_real)
    if dst == home_canon:
        return "refusing to empty the home folder"
    if _inside(home_canon, dst):
        return "refusing to empty a folder that contains the home folder"
    folders: list[str] = []
    for src in approved:
        folder_real = _resolve(os.path.dirname(src))
        folder = _fold(folder_real)
        folders.append(folder_real)
        if _inside(folder, dst) or _inside(os.path.dirname(folder), dst):
            return "the output folder contains the frames being copied (" + folder_real + ")"
    # Same directory as the source folder or any ancestor of it (up to the root), or of the home folder: an alias
    # (bind mount, another spelling on a case-insensitive mount) of an ancestor is as dangerous as the folder itself.
    root = os.path.abspath(os.sep)
    candidates: list[str] = []
    for base in folders + [home_real]:
        candidates += _ancestors(base)
    candidates.append(root)
    for other in candidates:
        if other and _same_dir(destination, other):
            return "the output folder is the same folder as " + other
    return None


def _ancestors(path: str) -> list[str]:
    """The path itself and each of its ancestors, up to and including the filesystem root."""
    out = [path]
    while True:
        parent = os.path.dirname(out[-1])
        if parent == out[-1]:
            return out
        out.append(parent)


def _mapping(approved: list[str], destination: str) -> dict[str, Any]:
    """Where each approved frame will be written. FITS sources keep their own names; the others become ``.fit``.
    Computed by output_mapping for each kind. Collisions are then checked over all the names together, case-folded
    where names fold case, so ``a.fit`` and ``A.fit`` (or ``a.FIT`` and ``a.xisf``) collide on macOS and Windows."""
    fits = [p for p in approved if is_fits_source(p)]
    other = [p for p in approved if not is_fits_source(p)]
    a = Fi.output_mapping(fits, destination, None) if fits else {"mapping": {}, "aliased": False}
    b = Fi.output_mapping(other, destination, ".fit") if other else {"mapping": {}, "aliased": False}
    mapping = {**a["mapping"], **b["mapping"]}
    collisions = _collisions(approved, mapping, _CASE_FOLD)
    return {"mapping": mapping, "collisions": collisions, "aliased": a["aliased"] or b["aliased"]}


def _collisions(approved: list[str], mapping: dict[str, str], fold: bool) -> list[str]:
    """The output names that repeat an earlier one, compared case-folded when ``fold``."""
    seen: set[str] = set()
    collisions: list[str] = []
    for p in approved:
        name = Fi.output_name(mapping[p])
        key = name.casefold() if fold else name
        if key in seen:
            collisions.append(name)
        seen.add(key)
    return collisions


def folds_case(folder: str) -> bool:
    """Does the file system of the existing ``folder`` fold case? A uniquely named probe file is created there, looked
    up under its swapped case and removed by its exact name. A probe that cannot be made answers True, the safe side:
    names are then compared folded."""
    name = ".loom-case-probe-" + uuid.uuid4().hex
    probe = os.path.join(folder, name)
    try:
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return True
    try:
        os.close(fd)
        other = os.path.join(folder, name.swapcase())
        try:
            return os.path.samefile(probe, other)
        except OSError:
            return False
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


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
    this run produced. Outputs are written in order, so when two of them are one file on disk (same device and inode:
    a file system that folds case or otherwise aliases names) only the last one survives; the earlier ones are
    "missing"."""
    last: dict[tuple[Any, ...], str] = {}
    for s in written:
        try:
            st = os.stat(m["mapping"][s])
        except OSError:
            continue
        if st.st_ino == 0:
            # Some network and FUSE mounts do not report inodes; without one the outputs cannot be told apart
            # by identity, so each is taken as its own file.
            last[(st.st_dev, 0, s)] = s
            continue
        last[(st.st_dev, st.st_ino)] = s
    survivors = set(last.values())
    for s in written:
        if s in survivors:
            result["outcomes"][s] = "written"
            result["written"] += 1
        else:
            result["outcomes"][s] = "missing"
            result["failed"] += 1


# ---- emptying ---------------------------------------------------------------------------------------------

def _unlink(path: str) -> None:
    """Remove one file or symbolic link. Never follows it."""
    os.remove(path)


def _rmdir(path: str) -> None:
    """Remove one empty directory (or a junction)."""
    os.rmdir(path)


def remove_entry(path: str, folder: bool, out: dict[str, Any], junction: bool = False) -> None:
    """Remove one entry of an output folder: a folder is emptied and removed; a junction is removed as a directory
    link; a file or a symbolic link is unlinked, never followed. A folder that is not empty stays, and its leftovers
    are reported."""
    try:
        if folder:
            inner = empty_directory(path)
            out["removed"] += inner["removed"]
            out["failed"] += inner["failed"]
            if inner["failed"]:
                return
            _rmdir(path)
        elif junction:
            _rmdir(path)
        else:
            _unlink(path)
        out["removed"] += 1
    except OSError:
        out["failed"].append(path)


def empty_directory(path: str) -> dict[str, Any]:
    """Remove everything inside a folder (hidden entries and subfolders too), leaving the folder itself. A symbolic
    link or a junction is removed as a link and never followed. Returns what was removed and what could not be."""
    out: dict[str, Any] = {"removed": 0, "failed": []}
    with os.scandir(path) as it:
        entries = list(it)
    for e in entries:
        child = os.path.join(path, e.name)
        junction = _is_junction(child)
        link = junction or e.is_symlink()
        remove_entry(child, not link and e.is_dir(follow_symlinks=False), out, junction=junction)
    return out


def export_approved(approved: Optional[list[str]], destination: Optional[str], siril: Any = None,
                    home: Optional[str] = None) -> dict[str, Any]:
    """Write the approved frames into ``destination``, emptying it first.

    Refused before anything is removed when two frames would share an output name (case-folded where names fold
    case, or where the output folder's file system folds case), when the destination is a source folder, or when it
    is, or contains, a source folder, the filesystem root or the home folder, on the JS string rules and on resolved
    paths (see the module docstring). The checks run again
    just before the emptying. If the folder cannot be fully emptied nothing is copied. ``outcomes`` says per frame
    whether it was "written" or is "missing"; a failure part way keeps the outputs already written as "written".
    """
    result: dict[str, Any] = {"written": 0, "failed": 0, "emptied": 0, "refused": None, "outcomes": {}}
    if approved is None or len(approved) == 0:
        return result
    if destination is None or str(destination).strip() == "":
        result["refused"] = Fi.empty_refusal(approved, destination, home)
        return result
    m = _mapping(approved, destination)
    result["refused"] = (export_refusal(m) or Fi.empty_refusal(
        approved, destination, home if home is not None else os.path.expanduser("~")))
    result["refused"] = result["refused"] or _path_refusal(approved, destination, home)
    if result["refused"] is not None:
        return result
    try:
        os.makedirs(destination, exist_ok=True)
        result["refused"] = _path_refusal(approved, destination, home)
        if result["refused"] is not None:
            return result
        if not _CASE_FOLD and folds_case(destination):
            folded = _collisions(approved, m["mapping"], True)
            if folded:
                result["refused"] = ("two or more frames would be written to the same name (the output folder's file "
                                     "system ignores letter case): " + ", ".join(folded))
                return result
        emptied = empty_directory(destination)
        result["emptied"] = emptied["removed"]
        if emptied["failed"]:
            result["refused"] = ("could not empty the output folder (" + str(len(emptied["failed"])) +
                                 " item(s), e.g. " + emptied["failed"][0] + "); nothing was copied")
            return result
        run_output_routine(approved, m["mapping"], siril)
    except Exception as e:  # noqa: BLE001 - reported to the caller as the refusal, as the JS does
        result["refused"] = str(e)
        confirm_written(approved, m, result)
        return result
    confirm_written(approved, m, result)
    return result
