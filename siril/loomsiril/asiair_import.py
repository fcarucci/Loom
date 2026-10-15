"""Importing a night off the ASIAIR card: the import glue of ``pixinsight/script/FrameSelector.js``, ported.

Covered, by JS line range (FrameSelector.js):

* ``importLogPath`` (1259-1262): :func:`import_log_path`. The JavaScript writes into the system temp folder; the
  caller passes that folder as ``temp_dir``.
* ``writeImportLog`` (1264-1277): :func:`write_import_log`.
* ``convertOne`` (2034-2052): :func:`convert_one`. The card holds FITS, so the copy is the file's bytes, not a
  re-encode through an image window. A destination that already exists is refused and never replaced.
* ``REQUIRED_KEYWORDS`` (2058): :data:`REQUIRED_KEYWORDS`.
* ``verifyImported`` (2072-2104): :func:`verify_imported`. The geometry and the required keywords are checked through
  ``frames_headers.read_header`` first, as the JavaScript does, so a wrong frame is named as such; then the copy is
  compared with its source by SHA-1 digest (``frames_cache.digest``), which the card's bytes cannot pass with a
  different pixel or header. A file that fails is deleted before the failure is reported.
* ``writeManifest`` (2110-2132): :func:`write_manifest`. Each file is verified as it lands; ``on_progress``
  returning ``False`` stops it after the file just written.
* ``readCardDarks`` (2140-2145): :func:`read_card_darks`, which sets ``cardDarks`` and ``cardDarkMatch`` on the state.
* ``cardDarksFound`` (2148-2151): :func:`card_darks_found`.
* ``cardDarksUsed`` (2154-2157): :func:`card_darks_used`.
* ``darkSourceOf`` (2160-2163): :func:`dark_source_of`.
* ``makeMasterFlats`` (2171-2226): :func:`make_master_flats`, without its progress window. ``note``, ``should_stop``
  and ``on_progress`` are callables, the engine is passed in, and the run function is ``run`` (``master_flat_run``'s
  ``execute(jobs, engine, opts)``). Cancel is polled through ``should_stop``.
* ``importOutcome`` (2233-2247): :func:`import_outcome`, returning ``{"text", "icon"}`` with the icon name
  ``"information"`` or ``"warning"``.
* ``outputsAreSafe`` (2259-2270): :func:`outputs_are_safe`.
* ``resolved`` (2277-2290): :func:`resolved`. A path that does not exist yet is resolved through its existing
  parents (``os.path.realpath``) instead of being returned unchanged, so a missing folder under a link onto the card
  is still refused.

The import summary (``AsiairNames.importSummary``, AsiairNames.js 562-579, in ``asiair_names.import_summary``) is
wrapped by :func:`import_summary`, which says "Written as FITS copies:" where the JavaScript says "XISF".

Nothing here writes under the card root: :func:`write_manifest` refuses a destination that resolves onto the card when
it is given ``card_root``, and :func:`outputs_are_safe` is the caller's check before any copy.

Names are the JS names in snake_case; dict keys keep the JS camelCase (``cardRoot``, ``cardDarkMatch``,
``onlyMasterFlats``, ``darksFolder``), so a state dict reads the same on both sides. JS ``null``/``undefined`` is
``None`` here.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from loomsiril import asiair_names as AN
from loomsiril import master_flat as MF
from loomsiril.frames_cache import digest
from loomsiril.frames_headers import HeaderInfo, keyword_value, read_header
from loomsiril.frames_headers import quiet_header as _quiet_header_info
from loomsiril.jsmath import js_string

_log = logging.getLogger(__name__)

Warn = Optional[Callable[[str], None]]

#: The folder under the system temp folder that holds the import logs (FrameSelector.js 1259-1262).
IMPORT_LOG_FOLDER = "Loom-import-logs"

#: Keywords an imported frame must still carry (FrameSelector.js 2058). Geometry alone proves nothing: a wrong frame
#: keeps the same dimensions.
REQUIRED_KEYWORDS: tuple[str, ...] = ("FILTER", "EXPTIME", "DATE-OBS")

#: Bytes per read when a frame is copied.
COPY_CHUNK = 4 * 1024 * 1024

#: Appended to a master-flat progress line that has text, as the progress window of FrameSelector.js 2171-2226 does.
_CANCEL_NOTE = " (Cancel keeps the raw flats not yet done)"

#: The label AsiairNames.importSummary writes, and the label this port writes for its FITS copies.
_XISF_LABEL = "Written as XISF copies:"
_FITS_LABEL = "Written as FITS copies:"

#: The master-flat work folder, under the cache folder (FrameSelector.js 2218).
MASTER_FLAT_WORK = "master-flat-work"


@dataclass(frozen=True)
class QuietHeader:
    """What ``read_header``-style callers of master_flat need: ``info`` (None when unreadable) and ``keyword(name)``."""

    info: Optional[HeaderInfo]
    keyword: Callable[[str], Optional[str]]


def _warn(warn: Warn, message: str) -> None:
    (warn or _log.warning)(message)


def _quiet_header(path: str) -> QuietHeader:
    """FrameSelector.quietHeader: the header of a frame, read quietly; never raises, never opens a window."""
    info, keyword = _quiet_header_info(path)
    return QuietHeader(info=info, keyword=keyword)


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _parent(path: str) -> str:
    """The folder part of ``path`` as ``substring(0, lastIndexOf("/"))`` gives it."""
    cut = path.rfind("/")
    return path[:cut] if cut >= 0 else ""


def _utc_iso_ms(stamp: _dt.datetime) -> str:
    """``Date#toISOString`` of a datetime: UTC, milliseconds (a naive datetime is taken as UTC)."""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    s = stamp.astimezone(_dt.timezone.utc)
    return f"{s:%Y-%m-%dT%H:%M:%S}.{s.microsecond // 1000:03d}Z"


def import_log_path(stamp: _dt.datetime, temp_dir: str) -> str:
    """The log of an import started at ``stamp``, in ``temp_dir`` (FrameSelector.js 1259-1262).

    The name is the ISO time with ``:`` and ``.`` turned into ``-``, as the JavaScript does.
    """
    name = _utc_iso_ms(stamp).replace(":", "-").replace(".", "-")
    return os.path.join(temp_dir, IMPORT_LOG_FOLDER, "import-" + name + ".log")


def write_import_log(path: str, lines: list[str], warn: Warn = None) -> Optional[str]:
    """Write the log lines over ``path`` (FrameSelector.js 1264-1277). Returns the path, or None after a warning."""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except OSError as e:
        _warn(warn, f"could not write the import log: {e}")
        return None


def convert_one(src: str, dst: str) -> dict[str, Any]:
    """Copy one frame's bytes from ``src`` to a NEW file ``dst`` (FrameSelector.js 2034-2052).

    ``{"ok": bool, "reason": str}``. A destination that already exists is refused and left as it is: a name clash is
    never overwritten. A copy that fails part way is deleted, so no partial file is left looking like a good one.
    """
    if not os.path.exists(src):
        return {"ok": False, "reason": "source is gone"}
    try:
        fout = open(dst, "xb")  # exclusive create: refuses an existing file or link, never follows one
    except FileExistsError:
        return {"ok": False, "reason": "a file of that name is already there; not overwritten"}
    except OSError as e:
        return {"ok": False, "reason": "could not write: " + str(e)}
    try:
        with fout, open(src, "rb") as fin:
            shutil.copyfileobj(fin, fout, COPY_CHUNK)
    except OSError as e:
        _remove_quietly(dst)
        return {"ok": False, "reason": "could not write: " + str(e)}
    return {"ok": True, "reason": ""}


def _copy_problem(src: str, dst: str, warn: Warn) -> Optional[str]:
    """What is wrong with the copy ``dst`` of ``src``, or None when it checks out."""
    a = read_header(src)
    b = read_header(dst)
    if a is None or b is None:
        return "could not read the header"
    if a.width != b.width or a.height != b.height:
        return "geometry changed"
    for k in REQUIRED_KEYWORDS:
        if keyword_value(a.keywords, k) is not None and keyword_value(b.keywords, k) is None:
            return k + " did not survive"
    a_digest = digest(src, warn)
    b_digest = digest(dst, warn)
    if a_digest is None or b_digest is None:
        return "could not read the file to check the copy"
    if a_digest != b_digest:
        return "the copy is not the card's file (SHA-1 differs)"
    return None


def verify_imported(src: str, dst: str, warn: Warn = None) -> dict[str, Any]:
    """Check the copy ``dst`` against its source (FrameSelector.js 2072-2104).

    ``{"ok": bool, "reason": str}``. A copy that fails is DELETED before the failure is returned, so a bad copy is
    never left looking like a good one.
    """
    if not os.path.exists(dst):
        return {"ok": False, "reason": "nothing was written"}
    try:
        problem = _copy_problem(src, dst, warn)
    except Exception as e:  # noqa: BLE001 -- a check that cannot run is a failed check, as in the JavaScript
        problem = str(e)
    if problem is not None:
        _remove_quietly(dst)
        return {"ok": False, "reason": problem}
    return {"ok": True, "reason": ""}


def resolved(path: str) -> str:
    """``path`` with every symlink followed (FrameSelector.js 2277-2290); the path, when it cannot be resolved."""
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return path


def outputs_are_safe(destination: Optional[str], card_root: Optional[str]) -> bool:
    """Is no output path on the card? (FrameSelector.js 2259-2270).

    The destination and its ``Light`` and ``Flat`` folders are each resolved, then compared with the card root on
    path components.
    """
    if destination is None or card_root is None:
        return False
    root = resolved(card_root)
    for out in (destination, destination + "/Light", destination + "/Flat"):
        if AN.is_inside(resolved(out), root):
            return False
    return True


def write_manifest(manifest: dict[str, Any], on_progress: Optional[Callable[[int, int], Any]] = None,
                   card_root: Optional[str] = None, warn: Warn = None) -> dict[str, Any]:
    """Copy and verify every file of ``manifest`` (lights, then flats) (FrameSelector.js 2110-2132).

    Returns ``{"written", "failed": [{"src", "reason"}], "cancelled"}``. ``on_progress(done, total)`` returning
    ``False`` stops after the file just written. With ``card_root`` given, a destination that resolves onto the card
    is failed, not written.
    """
    items = list(manifest["lights"]) + list(manifest["flats"])
    written = 0
    failed: list[dict[str, str]] = []
    cancelled = False
    for i, item in enumerate(items):
        dst = item["dst"]
        if card_root is not None and AN.is_inside(resolved(dst), resolved(card_root)):
            good = {"ok": False, "reason": "the destination is on the ASIAIR card; not written"}
        else:
            try:
                parent = _parent(dst)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                made = convert_one(item["src"], dst)
            except OSError as e:
                made = {"ok": False, "reason": "could not create the folder: " + str(e)}
            good = verify_imported(item["src"], dst, warn) if made["ok"] else made
        if good["ok"]:
            written += 1
        else:
            failed.append({"src": item["src"], "reason": good["reason"]})
        if on_progress is not None and on_progress(i + 1, len(items)) is False:
            cancelled = True
            break
    return {"written": written, "failed": failed, "cancelled": cancelled}


def read_card_darks(state: dict[str, Any], dark_frames: list[dict[str, Any]], flat_frames: list[dict[str, Any]]) -> None:
    """The card's darks, and their match against the night's flats, into the state (FrameSelector.js 2140-2145)."""
    got = MF.read_card_darks(dark_frames, flat_frames, _quiet_header)
    state["cardDarks"] = got["darks"]
    state["cardDarkMatch"] = got["match"]


def card_darks_found(state: dict[str, Any]) -> bool:
    """Do the card's darks stand in for a darks folder? (FrameSelector.js 2148-2151)."""
    match = state.get("cardDarkMatch")
    return state.get("cardRoot") is not None and match is not None and bool(match.get("found"))


def card_darks_used(state: dict[str, Any]) -> bool:
    """Are the flats calibrated with the card's darks: the box ticked and the card's darks found? (FrameSelector.js 2154-2157)."""
    return card_darks_found(state) and bool(state.get("onlyMasterFlats"))


def dark_source_of(state: dict[str, Any]) -> dict[str, Any]:
    """Where the darks come from, for ``AsiairNames.dark_source`` (FrameSelector.js 2160-2163)."""
    match = state.get("cardDarkMatch") if card_darks_found(state) else None
    return MF.dark_source_of(match, state.get("darksFolder"))


def make_master_flats(manifest: dict[str, Any], written: dict[str, Any], dest: str, state: dict[str, Any], *,
                      engine: Any, run: Callable[[list[dict[str, Any]], Any, dict[str, Any]], Any],
                      note: Optional[Callable[[str], None]] = None,
                      should_stop: Optional[Callable[[], bool]] = None,
                      on_progress: Optional[Callable[[int, int, str], None]] = None,
                      cache_dir: Optional[str] = None) -> Any:
    """The master-flats step of an import, after the raw flats are in ``<dest>/Flat`` (FrameSelector.js 2171-2226).

    Reads only the copies there and the darks (the card's, or the darks folder); nothing on the card is touched.
    ``note`` receives the import-log lines in the order the JavaScript writes them. ``run(jobs, engine, opts)`` is
    ``master_flat_run.execute``; ``opts`` keeps the JavaScript keys (``destFlatDir``, ``cardRoot``, ``shouldStop``,
    ``onProgress``). ``cache_dir`` is where the work folder lives; it is removed afterwards.
    """
    emit: Callable[[str], None] = note or (lambda _line: None)
    root = AN.import_root(dest)["root"]
    failed_srcs = {js_string(f["src"]) for f in written["failed"]}

    flats: list[dict[str, Any]] = []
    for f in manifest["flats"]:
        if js_string(f["src"]) in failed_srcs:
            continue
        h = _quiet_header(f["dst"])
        if h.info is not None:
            flats.append(MF.describe(f["dst"], h.keyword))

    from_card = card_darks_used(state)
    if from_card:
        # Read again now: a card pulled out since the darks were found leaves no headers, so no darks, so no master.
        darks = MF.card_darks(state.get("cardDarks") or [], _quiet_header)
    else:
        darks = MF.darks_of(MF.scan_folder(state.get("darksFolder"), _quiet_header))

    emit("flats read: " + str(len(flats)))
    for f in flats:
        emit("  " + MF.frame_line(f))
    if from_card:
        emit("darks source: " + AN.dark_source(dark_source_of(state)) + " (read-only; subfolders not searched)")
    else:
        emit("darks folder: " + js_string(state.get("darksFolder")) + " (subfolders to " + str(MF.SCAN_DEPTH)
             + " levels, names containing \"dark\")")
    emit("darks found: " + str(len(darks)) + (" on the card" if from_card else ""))
    for line in MF.dark_summary_lines(darks):
        emit(line)

    jobs = MF.build_plan(flats, darks, root + "/Flat", "on the ASIAIR card" if from_card else None)
    jobs = AN.skip_filters_with_failed_copies(manifest["flats"], [f["src"] for f in written["failed"]], jobs)
    emit("plan:")
    for line in MF.plan_lines(jobs):
        emit(line)

    def report(done: int, total: int, text: str) -> None:
        if on_progress is not None:
            on_progress(done, total, text + _CANCEL_NOTE if len(text) > 0 else text)

    opts = {
        "destFlatDir": root + "/Flat",
        "cardRoot": state.get("cardRoot"),
        "shouldStop": should_stop or (lambda: False),
        "onProgress": report,
    }
    try:
        return run(jobs, engine, opts)
    finally:
        if cache_dir is not None:
            shutil.rmtree(os.path.join(cache_dir, MASTER_FLAT_WORK), ignore_errors=True)


def import_outcome(result: dict[str, Any]) -> dict[str, str]:
    """What a finished import says (FrameSelector.js 2233-2247): ``{"text", "icon"}``, icon "information" or "warning".

    A cancelled import says the rest were never written, and so the card should not be wiped on the strength of it.
    """
    failed = result["failed"]
    cancelled = result["cancelled"]
    head = (f"Import cancelled after {result['written']} frame(s) were imported" if cancelled
            else f"Imported {result['written']} frame(s)")
    if len(failed) == 0:
        text = head + "."
    else:
        text = (head + f"; {len(failed)} failed:\n\n" + js_string(failed[0]["src"]) + "\n"
                + js_string(failed[0]["reason"]))
    if cancelled:
        text += (" " if len(failed) == 0 else "\n\n") + "The rest were not written."
    icon = "information" if (len(failed) == 0 and not cancelled) else "warning"
    return {"text": text, "icon": icon}


def import_summary(manifest: dict[str, Any], chosen: Any, master_flats: Any) -> str:
    """The import's confirmation (AsiairNames.js 562-579) with the FITS label in place of the XISF one."""
    lines = AN.import_summary(manifest, chosen, master_flats).split("\n")
    return "\n".join(line.replace(_XISF_LABEL, _FITS_LABEL) for line in lines)
