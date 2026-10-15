"""Frame Selector: deleting the rejected frames of a manifest, ported from ``pixinsight/script/FrameSelector.js``.

The line numbers are that file's. Each JS function has a Python twin with the same meaning, named in snake_case:

    logDir               1246-1249  -> log_dir (the folder under the home directory; file name by ``log_path``)
    writeManifestLog     1279-1307  -> write_manifest_log
    appendOutcome        1313-1336  -> append_outcome
    execute              1350-1418  -> execute

The manifest and its rules are ported in loomsiril.frames (``build_manifest`` from Frames.js, ``manifest_pending``,
``record_outcome`` and ``identity_matches``, the last two from lib/Frames.js 1270-1294); the fresh identity of a file
is loomsiril.frames_cache.file_identity (FrameSelector.js 653). The rules, all from the JavaScript comments:

* The log is written BEFORE any file is removed, and nothing is removed when it cannot be written.
* Each outcome is appended and flushed before the next file; the run STOPS if the journal cannot be appended.
* A frame whose identity changed (digest or size), vanished or became unreadable is skipped and reported.
* A removal error is recorded as "failed".
* A verdict is never recomputed here, and only manifest entries are touched: kept frames are left as they are.

Dict keys keep the JS camelCase (``logPath``, ``autoVerdict``) so a manifest reads the same on both sides.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loomsiril import frames
from loomsiril import frames_cache

Manifest = dict[str, Any]
Entry = dict[str, Any]
Log = Optional[Callable[[str], None]]

_log = logging.getLogger("loomsiril.frames_delete")

LOG_FOLDER = os.path.join("Siril", "Loom-frame-selector")


def log_dir(home: Optional[str] = None) -> str:
    """``<home>/Siril/Loom-frame-selector``, the audit log folder (not under the cache, not under the temp folder)."""
    return os.path.join(home if home is not None else os.path.expanduser("~"), LOG_FOLDER)


def _iso_utc(when: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SS.mmmZ`` as JavaScript's Date.toISOString writes it (UTC, milliseconds)."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    return f"{when:%Y-%m-%dT%H:%M:%S}.{when.microsecond // 1000:03d}Z"


def _iso_from_ms(ms: int) -> str:
    """Milliseconds since the epoch as ``toISOString`` writes them: ``new Date(ms).toISOString()``."""
    seconds, millis = divmod(int(ms), 1000)
    base = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds, milliseconds=millis)
    return _iso_utc(base)


def log_path(folder: str, when: datetime) -> str:
    """``deleted-<ISO time with : and . replaced by ->.log`` inside ``folder`` (FrameSelector.js 1348-1349)."""
    return os.path.join(folder, "deleted-" + _iso_utc(when).replace(":", "-").replace(".", "-") + ".log")


def write_manifest_log(manifest: Manifest, path: str, log: Log = None) -> bool:
    """Write the header and one line per entry, before anything is removed (FrameSelector.js 1279-1307).

    Each entry line is ``path TAB digest TAB verdict TAB reasons``, where the verdict is the override when one was
    chosen (so the record says who chose it) and the automatic verdict otherwise. Returns False, and reports, when
    the file cannot be written.
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        lines = ["# Loom Frame Selector",
                 "# built " + _iso_from_ms(manifest["created"]),
                 f"# {len(manifest['entries'])} frame(s) condemned"]
        for e in manifest["entries"]:
            lines.append("\t".join([e["path"], _js_text(e["digest"]), e["override"] or e["autoVerdict"],
                                    "; ".join(e["reasons"])]))
        _write_text(path, "\n".join(lines) + "\n")
        return True
    except OSError as ex:
        _report(log, "error", f"could not write the deletion log: {ex}")
        return False


def append_outcome(path: str, manifest: Manifest, file_path: str, log: Log = None) -> bool:
    """Append one entry's fate to the journal and flush it (FrameSelector.js 1313-1336).

    Returns False, and reports, when the record cannot be written or when no entry has that path; the caller stops.
    """
    try:
        entry = next((e for e in manifest["entries"] if e["path"] == file_path), None)
        if entry is None:
            return False
        line = "\t".join([entry["outcome"], entry["path"], _js_text(entry["digest"]), entry["detail"]]) + "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        return True
    except OSError as ex:
        _report(log, "error", f"could not append to the journal: {ex}")
        return False


def _js_text(v: Optional[str]) -> str:
    """A string field as JavaScript concatenation prints it: null becomes "null"."""
    return "null" if v is None else v


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _remove(path: str) -> None:
    os.remove(path)


def _report(log: Log, level: str, message: str) -> None:
    """Route a diagnostic to ``log`` when given, else to the module logger at ``level``."""
    if log is not None:
        log(message)
    else:
        getattr(_log, level)(message)


def execute(manifest: Manifest, on_progress: Optional[Callable[[int, int, str], Any]] = None, *,
            home: Optional[str] = None, now: Optional[datetime] = None, log: Log = None) -> dict[str, Any]:
    """Execute a manifest (FrameSelector.js 1350-1418). Never recomputes a verdict; never touches a file whose identity
    has changed since it was measured.

    ``on_progress(done, total, path)`` is called before each pending file. ``home`` defaults to the user's home
    folder, ``now`` to the current time (UTC) and names the log, ``log`` receives diagnostics (default: the module
    logger). Returns ``{"deleted", "skipped", "failed", "stopped", "logPath"}``; ``logPath`` is None when nothing was
    pending or the log could not be written.
    """
    result: dict[str, Any] = {"deleted": 0, "skipped": 0, "failed": 0, "stopped": False, "logPath": None}
    pending = frames.manifest_pending(manifest)
    if not pending:
        return result

    folder = log_dir(home)
    path = log_path(folder, now if now is not None else datetime.now(timezone.utc))
    if not write_manifest_log(manifest, path, log):
        _report(log, "error", "nothing deleted: the log could not be written")
        return result
    result["logPath"] = path

    total = len(pending)
    for i, e in enumerate(pending):
        if on_progress is not None:
            on_progress(i + 1, total, e["path"])
        # Recomputed immediately before the removal. This narrows the window to microseconds rather than closing it;
        # the log is what survives if a replacement slips through.
        current = frames_cache.file_identity(e["path"])
        if not frames.identity_matches(e, current):
            frames.record_outcome(manifest, e["path"], "skipped", "changed on disk since it was measured")
            _report(log, "warning", f"skipped {e['path']}: it changed since measurement")
            result["skipped"] += 1
            if not append_outcome(path, manifest, e["path"], log):
                result["stopped"] = True
                return result
            continue
        try:
            _remove(e["path"])
            frames.record_outcome(manifest, e["path"], "deleted", "")
            result["deleted"] += 1
        except OSError as ex:
            frames.record_outcome(manifest, e["path"], "failed", str(ex))
            result["failed"] += 1
        # Journalled one entry at a time, before moving on: a run interrupted midway keeps the record of what died.
        # If the journal cannot be appended the run stops, since it would otherwise delete files nothing records.
        if not append_outcome(path, manifest, e["path"], log):
            _report(log, "error", "stopping: the journal could not be appended")
            result["stopped"] = True
            return result

    _report(log, "info", f"{result['deleted']} deleted, {result['skipped']} skipped, "
                         f"{result['failed']} failed; log {path}")
    return result
