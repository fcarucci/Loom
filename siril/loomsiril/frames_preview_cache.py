"""Frame Selector: the on-disk preview cache, with a size cap and least-recently-used eviction.

Ported from ``pixinsight/script/lib/Cache.js`` lines 501-665 (the line numbers are that file's). Each JS function has a
Python twin with the same meaning, named in snake_case:

    PREVIEW_VERSION                 501  -> PREVIEW_VERSION
    PREVIEW_CAP_BYTES               504  -> PREVIEW_CAP_BYTES
    previewDirOverride              507  -> PreviewCache.dir_override
    previewDir                      509  -> PreviewCache.preview_dir
    previewPath                     515  -> PreviewCache.preview_path
    previewIndexPath                520  -> PreviewCache.preview_index_path
    previewKeyOf                    526  -> preview_key_of
    previewKey                      534  -> PreviewCache.preview_key
    readPreviewIndex                548  -> PreviewCache.read_preview_index
    notePreviewUse                  567  -> PreviewCache.note_preview_use
    previewEntries                  582  -> PreviewCache.preview_entries
    previewBytes                    597  -> PreviewCache.preview_bytes
    previewEvictions                606  -> preview_evictions
    trimPreviews                    625  -> PreviewCache.trim_previews
    prunePreviewIndex               635  -> PreviewCache.prune_preview_index

Not ported: clearPreviews (Cache.js 654-665), which nothing in the Frame Selector calls.

Differences from the JavaScript, all deliberate:

* No globals. A PreviewCache holds its location: ``cache_dir`` plays the part of ``File.systemTempDirectory`` and the
  previews live in ``<cache_dir>/Loom-previews`` unless ``dir_override`` is set (``Cache.previewDirOverride``).
* The cache is format-blind. The renderer chooses the file extension (PNG or .npy in the Siril port), so an entry is
  any regular file in the folder except ``index.json`` and dot-files; its key is the file name without its last
  extension. The JavaScript keeps only ``*.xisf`` files. Entries are listed sorted by name so that ties in
  ``preview_evictions`` resolve the same way on every platform (the JavaScript follows directory order).
* ``preview_key`` takes the modification time from ``os.stat`` in whole milliseconds (floor), as ``FileInfo``'s
  ``lastModified`` is a Date with millisecond precision.
* A JSON array in the index file is read as an empty index. The JavaScript would keep the array and, after a
  ``note_preview_use``, write back a bare ``[]``; the index is advisory either way.
* JSON non-finite tokens (NaN, Infinity) are rejected in the index, as ``JSON.parse`` rejects them.

The pure functions (``preview_key_of``, ``preview_evictions``) are replayed against tests/oracle/fs_preview_cache.json
(recorded from the JavaScript by tools/oracle/fs_preview_cache.js). The file-system methods are tested with temporary
folders in tests/test_frames_preview_cache.py.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from functools import cmp_to_key
from typing import Any, Optional

from loomsiril.jsmath import js_string
from loomsiril.loom.cache import hash_str, params_string

#: Cache.PREVIEW_VERSION: part of every key, so a change to how previews are drawn invalidates them all.
PREVIEW_VERSION = "1"

#: Cache.PREVIEW_CAP_BYTES: about forty full-size 26 MP previews at 8 bits, and every thumbnail.
PREVIEW_CAP_BYTES = 1024 * 1024 * 1024

#: The index file's name inside the preview folder.
INDEX_NAME = "index.json"

#: The folder name under cache_dir (the JavaScript's "Loom-previews" under the system temporary folder).
PREVIEW_FOLDER = "Loom-previews"

_DECIMAL = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def _no_constant(token: str) -> Any:
    """``JSON.parse`` has no NaN or Infinity: a file with one is corrupt."""
    raise ValueError("non-finite token %s in JSON" % token)


def _js_to_number(text: str) -> float:
    """``Number(text)`` for a string: whitespace trimmed, empty is 0, anything not a decimal is NaN."""
    s = text.strip()
    if s == "":
        return 0.0
    if s in ("Infinity", "+Infinity"):
        return math.inf
    if s == "-Infinity":
        return -math.inf
    if _DECIMAL.fullmatch(s):
        return float(s)
    return math.nan


def _field(entry: Mapping[str, Any], name: str) -> float:
    """``entry.name`` as a number in arithmetic: a missing field is NaN, null is 0, a string is ``Number(...)``."""
    if name not in entry:
        return math.nan
    v = entry[name]
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return _js_to_number(v)
    return math.nan


def _number_or_zero(v: Any) -> float:
    """``Number(v) || 0``: NaN and anything that is not a number become 0."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        x = float(v)
    elif isinstance(v, str):
        x = _js_to_number(v)
    else:
        return 0.0
    return 0.0 if math.isnan(x) else x


def _iso_ms(ms: int) -> str:
    """``Date.toISOString()`` of a time in whole milliseconds since the epoch, UTC."""
    when = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%S") + ".%03dZ" % (ms % 1000)


def _json_number(x: float) -> Any:
    """A whole number written as an integer, as ``JSON.stringify`` writes it."""
    return int(x) if float(x).is_integer() else x


def preview_key_of(path: str, size: Any, modified: str, kind: str, params: Optional[Mapping[str, Any]] = None) -> str:
    """``Cache.previewKeyOf`` (Cache.js 526-531): the key for one render of one version of one file. Pure."""
    return hash_str(
        "preview|" + PREVIEW_VERSION + "|" + js_string(kind) + "|" + js_string(path) + "|" + js_string(size)
        + "|" + js_string(modified) + "|" + params_string(params)
    )


def preview_evictions(entries: Sequence[Mapping[str, Any]], cap: float, keep: Any = None) -> list[str]:
    """``Cache.previewEvictions`` (Cache.js 606-622): which keys to remove so what is left fits ``cap``.

    Least recently used first (``used`` ascending; ties keep their input order). ``keep``, the entry just stored, is
    never one of them. Pure: the entries are not changed.
    """
    total = 0.0
    for e in entries:
        total = total + _field(e, "bytes")
    order = [e for e in entries if e.get("key") != keep]

    def compare(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
        diff = _field(a, "used") - _field(b, "used")
        return 0.0 if math.isnan(diff) else diff

    order.sort(key=cmp_to_key(compare))
    out: list[str] = []
    i = 0
    while i < len(order) and total > cap:
        out.append(order[i]["key"])
        total -= _field(order[i], "bytes")
        i += 1
    return out


class PreviewCache:
    """The preview folder of one cache: where previews live, the index of when each was used, and the trimming.

    ``cache_dir`` is the folder the previews sit under; ``dir_override``, when not empty, is the folder itself
    (Cache.previewDirOverride, which the test suite sets to its own scratch folder).
    """

    def __init__(self, cache_dir: str, dir_override: str = "") -> None:
        self.cache_dir = cache_dir
        self.dir_override = dir_override

    def preview_dir(self) -> str:
        """``Cache.previewDir`` (Cache.js 509-513)."""
        if len(self.dir_override) > 0:
            return self.dir_override
        return os.path.join(self.cache_dir, PREVIEW_FOLDER)

    def preview_path(self, key: str, ext: str) -> str:
        """``Cache.previewPath`` (Cache.js 515-518), with the extension the renderer chose (".png", ".npy", ...)."""
        return os.path.join(self.preview_dir(), key + ext)

    def preview_index_path(self) -> str:
        """``Cache.previewIndexPath`` (Cache.js 520-523)."""
        return os.path.join(self.preview_dir(), INDEX_NAME)

    def preview_key(self, path: str, kind: str, params: Optional[Mapping[str, Any]]) -> Optional[str]:
        """``Cache.previewKey`` (Cache.js 534-545): the key for ``path`` as it is on disk now, or None when absent."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        modified = _iso_ms(st.st_mtime_ns // 1_000_000)
        return preview_key_of(path, st.st_size, modified, kind, params)

    def read_preview_index(self) -> dict[str, Any]:
        """``Cache.readPreviewIndex`` (Cache.js 548-562): { key: time }. A missing or unreadable index is empty."""
        try:
            path = self.preview_index_path()
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    idx = json.loads(f.read(), parse_constant=_no_constant)
                if isinstance(idx, dict):
                    return idx
        except (OSError, ValueError):
            pass
        return {}

    def note_preview_use(self, key: str) -> None:
        """``Cache.notePreviewUse`` (Cache.js 567-578): records a use, strictly later than every use already recorded."""
        try:
            os.makedirs(self.preview_dir(), exist_ok=True)
            idx = self.read_preview_index()
            latest = 0.0
            for k in idx:
                latest = max(latest, _number_or_zero(idx[k]))
            idx[key] = _json_number(max(float(time.time_ns() // 1_000_000), latest + 1))
            with open(self.preview_index_path(), "w", encoding="utf-8") as f:
                f.write(json.dumps(idx, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
        except (OSError, ValueError):
            pass  # the order is advisory; the entry itself is intact

    def preview_entries(self) -> list[dict[str, Any]]:
        """``Cache.previewEntries`` (Cache.js 582-595): every entry on disk as { key, bytes, used, name }."""
        folder = self.preview_dir()
        if not os.path.isdir(folder):
            return []
        idx = self.read_preview_index()
        out: list[dict[str, Any]] = []
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            if name.startswith(".") or name == INDEX_NAME or not os.path.isfile(full):
                continue
            key = os.path.splitext(name)[0]
            out.append({"key": key, "bytes": os.stat(full).st_size, "used": _number_or_zero(idx.get(key)),
                        "name": name})
        return out

    def preview_bytes(self) -> int:
        """``Cache.previewBytes`` (Cache.js 597-600): the bytes the previews on disk hold."""
        return sum(int(e["bytes"]) for e in self.preview_entries())

    def trim_previews(self, cap: float, keep: Optional[str] = None) -> int:
        """``Cache.trimPreviews`` (Cache.js 625-632): removes the least recently used entries until the folder fits
        ``cap``, then prunes the index. Returns how many keys were removed."""
        entries = self.preview_entries()
        gone = preview_evictions(entries, cap, keep)
        gone_set = set(gone)
        for e in entries:
            if e["key"] in gone_set:
                try:
                    os.remove(os.path.join(self.preview_dir(), e["name"]))
                except OSError:
                    pass
        self.prune_preview_index()
        return len(gone)

    def prune_preview_index(self) -> None:
        """``Cache.prunePreviewIndex`` (Cache.js 635-650): drops index entries whose preview file is gone."""
        try:
            idx = self.read_preview_index()
            present = {e["key"] for e in self.preview_entries()}
            kept: dict[str, Any] = {}
            changed = False
            for k in idx:
                if k in present:
                    kept[k] = idx[k]
                else:
                    changed = True
            if changed:
                with open(self.preview_index_path(), "w", encoding="utf-8") as f:
                    f.write(json.dumps(kept, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
        except (OSError, ValueError):
            pass  # the index is advisory; a stale line is only a stale line
