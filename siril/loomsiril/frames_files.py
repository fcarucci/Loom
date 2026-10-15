"""Frame Selector: the digest cache, output names and mapping, and the arithmetic of the file list and the filmstrip.

Ported from ``pixinsight/script/lib/Frames.js`` (digest cache 1318-1385, names and destinations 1387-1570, the
filmstrip 1971-2025, batching 2027-2060 and 2141-2179). Pure over strings and plain dicts: nothing here reads or
writes a file. Pinned to the JavaScript by ``tests/oracle/fs_flags.json``.

The ``extract_*`` helpers stand in for PJSR's ``File.extractDirectory``, ``extractName`` and ``extractExtension``.
The recorder runs them on node's shim (``pjsr-shim.js``), which is POSIX path semantics, so these follow the same
rules: only "/" separates directories, and a name starts a new extension at its last dot. Frames are always full
paths here, so the one case where node and PJSR differ (a bare name with no folder) does not come up.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from loomsiril.frames_flags import is_number
from loomsiril.jsmath import js_string

Entry = dict[str, Any]

#: A digest is the SHA-1 hex of the file: exactly 40 lowercase hex digits. fullmatch, so a trailing newline is not one.
DIGEST_HEX = re.compile(r"[0-9a-f]{40}")
#: A file changed this recently (by mtime or ctime) cannot vouch for its own digest yet.
DIGEST_SETTLE_MS = 2000

#: Characters that separate the tokens of a frame name, for shortNames.
SEPARATORS = "_-."


# ---- the digest cache ------------------------------------------------------------------------------------

def digest_cache_entry(stat: Optional[Entry], hex_: Any) -> Optional[Entry]:
    """``{size, mtime, created, digest}`` with created None when absent, or None when the parts are not usable."""
    if stat is None or not isinstance(hex_, str) or not DIGEST_HEX.fullmatch(hex_):
        return None
    created = stat.get("created")
    if not is_number(stat.get("size")) or not is_number(stat.get("mtime")) or not (created is None or is_number(created)):
        return None
    return {"size": stat["size"], "mtime": stat["mtime"], "created": created, "digest": hex_}


def digest_cache_stored(e: Any) -> Optional[Entry]:
    """The manifest entry as stored, validated; None when absent, malformed or from before "created"."""
    if e is None or not isinstance(e, dict) or "created" not in e:
        return None
    return digest_cache_entry(e, e.get("digest"))


def digest_cache_hit(table: Optional[dict[str, Any]], path: str, stat: Entry) -> Optional[str]:
    """The stored digest for a path when its size, mtime and created still match the file, else None."""
    if table is None or path not in table:
        return None
    stored = digest_cache_stored(table[path])
    now = digest_cache_entry(stat, stored["digest"]) if stored is not None else None
    if (now is not None and now["size"] == stored["size"] and now["mtime"] == stored["mtime"]
            and now["created"] == stored["created"]):
        return stored["digest"]
    return None


def digest_racy(stat: Entry, e: Entry) -> bool:
    """Did the file change too recently for its times to vouch for it? A clock that does not read is racy too."""
    at = stat.get("at")
    if not is_number(at):
        return True

    def recent(t: Optional[float]) -> bool:
        return t is not None and not (at - t >= DIGEST_SETTLE_MS)

    return recent(e["mtime"]) or recent(e.get("created"))


def digest_cache_record(table: dict[str, Any], path: str, stat: Entry, hex_: Any) -> None:
    """A record replaces the path's entry; one that cannot be trusted removes it. Changes ``table`` in place."""
    e = digest_cache_entry(stat, hex_)
    if e is not None and not digest_racy(stat, e):
        table[path] = e
    else:
        table.pop(path, None)


# ---- names and directories --------------------------------------------------------------------------------

def extract_directory(path: str) -> str:
    """PJSR ``File.extractDirectory`` as the node shim has it: everything before the last "/"."""
    stripped = path.rstrip("/")
    if stripped == "":
        return "/" if path.startswith("/") else "."
    i = stripped.rfind("/")
    if i < 0:
        return "."
    return "/" if i == 0 else stripped[:i]


def _base(path: str) -> str:
    """The last path component, trailing slashes ignored (node's basename)."""
    stripped = path.rstrip("/")
    return stripped[stripped.rfind("/") + 1:]


def extract_extension(path: str) -> str:
    """PJSR ``File.extractExtension``: the last dot of the file name and what follows, dot included; "" if none."""
    base = _base(path)
    i = base.rfind(".")
    if i <= 0 or base == "..":
        return ""
    return base[i:]


def extract_name(path: str) -> str:
    """PJSR ``File.extractName``: the file name without its extension."""
    base = _base(path)
    ext = extract_extension(path)
    return base[:-len(ext)] if ext else base


def output_name(path: str) -> str:
    """The file name of a path, Windows or POSIX separators."""
    i = max(path.rfind("/"), path.rfind("\\"))
    return path if i < 0 else path[i + 1:]


def common_prefix(names: list[str]) -> str:
    """The longest start every name shares."""
    prefix = names[0]
    for other in names[1:]:
        if not prefix:
            break
        j = 0
        while j < len(prefix) and j < len(other) and prefix[j] == other[j]:
            j += 1
        prefix = prefix[:j]
    return prefix


def last_separator_in(text: str) -> int:
    """Index of the last name separator in a string, or -1."""
    cut = -1
    for k, ch in enumerate(text):
        if ch in SEPARATORS:
            cut = k
    return cut


def short_names(paths: list[str]) -> list[str]:
    """The part of each name that actually differs: the common head is dropped, cut at a separator so no name
    starts mid-token. A single name, or no shared head, comes back whole."""
    names = [output_name(p) for p in paths]
    if len(names) < 2:
        return names
    cut = last_separator_in(common_prefix(names))
    if cut < 0:
        return names
    # A remainder that is empty keeps the whole name rather than becoming an empty cell.
    return [(name[cut + 1:] or name) for name in names]


def needing_xisf(paths: list[str]) -> list[str]:
    """The frames that are not already XISF: converting an XISF in place is a no-op."""
    return [p for p in paths if extract_extension(p).lower() != ".xisf"]


def by_directory(paths: list[str]) -> dict[str, Any]:
    """The frames grouped by the folder they live in: ``{"dirs": {folder: [paths]}, "order": [folders]}``."""
    dirs: dict[str, list[str]] = {}
    order: list[str] = []
    for p in paths:
        d = extract_directory(p)
        if d not in dirs:
            dirs[d] = []
            order.append(d)
        dirs[d].append(p)
    return {"dirs": dirs, "order": order}


def destination_is_source(paths: list[str], destination: Optional[str]) -> bool:
    """Is the chosen destination one of the folders the frames came from? Compared as written, not resolved."""
    if destination is None:
        return False
    return any(extract_directory(p) == destination for p in paths)


def without_trailing_slash(path: Any) -> str:
    return re.sub(r"/+\Z", "", js_string(path))


def empty_refusal(source_paths: list[str], destination: Optional[str], home: Optional[str]) -> Optional[str]:
    """Why the output folder must not be emptied, or None.

    Copying out empties the folder first, so a folder that is, or contains, the folder of any frame would take the
    originals with it. The filesystem root and the home folder are refused outright.
    """
    if destination is None or js_string(destination).strip() == "":
        return "no output folder chosen"
    dest = without_trailing_slash(destination)
    if dest == "":
        return "refusing to empty the filesystem root"
    if home is not None and dest == without_trailing_slash(home):
        return "refusing to empty the home folder"
    for p in source_paths:
        d = without_trailing_slash(extract_directory(p))
        if d == dest or d.startswith(dest + "/"):
            return "the output folder contains the frames being copied (" + d + ")"
    return None


def output_mapping(approved: list[str], destination: str, extension: Optional[str]) -> dict[str, Any]:
    """Where each approved frame will be written, computed before anything is. Two frames sharing an output name
    are a collision. The extension is part of the key, since converting on output makes a.fit and a.xisf collide.
    ``aliased`` says the destination is one of the source folders."""
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    collisions: list[str] = []
    aliased = False
    for src in approved:
        if extract_directory(src) == destination:
            aliased = True
        name = extract_name(src) + extension if extension else output_name(src)
        if name in taken:
            collisions.append(name)
        taken.add(name)
        mapping[src] = destination + "/" + name
    return {"mapping": mapping, "collisions": collisions, "aliased": aliased}


# ---- the filmstrip --------------------------------------------------------------------------------------

def strip_first(count: int, selected: int, visible: int, current: Optional[int]) -> int:
    """The first tile the strip shows. When the selection is already on screen at ``current``, the view does not
    move; otherwise the selection is centred, clamped to the ends."""
    if count <= visible:
        return 0
    last = count - visible
    s = 0 if selected < 0 else selected
    if current is not None and 0 <= current <= last and current <= s < current + visible:
        return current
    return max(0, min(s - visible // 2, last))


def tile_at(x: float, tile_w: float, visible: int, first: int, count: int) -> int:
    """The frame a click at x lands on, or -1. Only a drawn tile counts."""
    if x < 0:
        return -1
    slot = math.floor(x / tile_w)
    if slot >= visible:
        return -1
    i = first + slot
    return i if i < count else -1


def thumbnail_order(count: int, selected: int, first: int, visible: int) -> list[int]:
    """The order thumbnails are loaded in: the visible tiles first, nearest the selection first, then the rest of
    the channel outward from it. Ties go to the lower index. Every index appears exactly once."""
    s = first if selected < 0 else selected
    in_view = [i for i in range(count) if first <= i < first + visible]
    rest = [i for i in range(count) if not (first <= i < first + visible)]

    def by_distance(a: int) -> tuple[int, int]:
        return (abs(a - s), a)

    return sorted(in_view, key=by_distance) + sorted(rest, key=by_distance)


# ---- batches --------------------------------------------------------------------------------------------

_JS_DECIMAL = re.compile(r"[+-]?(?:Infinity|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)")
_JS_RADIX = re.compile(r"0([xXoObB])([0-9a-fA-F]+)")


def _js_number(v: Any) -> float:
    """``Number(v)`` for a batch size: NaN for what JS calls non-numeric, 0 for null and an empty string."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip()
        if t == "":
            return 0.0
        m = _JS_RADIX.fullmatch(t)
        if m:
            return float(int(m.group(2), {"x": 16, "o": 8, "b": 2}[m.group(1).lower()]))
        return float(t) if _JS_DECIMAL.fullmatch(t) else math.nan
    return math.nan


def _batch_size(size: Any) -> float:
    """``Math.max(1, Math.floor(Number(size)) || 1)``: an int of at least 1, or infinity for an infinite size."""
    x = _js_number(size)
    if math.isinf(x):
        return x if x > 0 else 1
    if math.isnan(x):
        return 1
    return max(1, math.floor(x))


def batches_of(lst: list[Any], size: Any) -> list[list[Any]]:
    """A list cut into consecutive batches of ``size``, the last one short. A size below one, or not a number, is
    one; an infinite size is one batch of everything (as ``Math.floor(Infinity)`` is in JS)."""
    n = _batch_size(size)
    if math.isinf(n):
        return [list(lst)] if len(lst) else []
    return [lst[i:i + n] for i in range(0, len(lst), n)]


def merge_batch(batch: list[str], got: Optional[dict[str, Any]], measured: dict[str, Any]) -> bool:
    """One batch's results into ``measured``; False when they cannot be used: no result at all, or one for a path
    the batch did not ask for or that is already measured. A refusal can leave earlier paths merged, as the JS does."""
    if got is None:
        return False
    asked = set(batch)
    for p in got:
        if p not in asked or measured.get(p) is not None:
            return False
        measured[p] = got[p]
    return True


def measuring_lines(filter_: str, done: int, total: int, overall: Optional[dict[str, Any]]) -> dict[str, Any]:
    """What the scan window shows while a channel is measured: the title counts the channel's frames; the bar is
    the whole scan when ``overall`` is given, else the channel."""
    title = "Measuring " + js_string(filter_) + " (" + js_string(done) + " of " + js_string(total) + " frames)"
    if overall is None:
        return {"title": title, "detail": "", "fraction": done / total if total > 0 else 0}
    where = "channel " + js_string(overall["channel"]) + " of " + js_string(overall["channels"]) + ", "
    if not (overall.get("total") is not None and overall["total"] > 0):
        return {"title": title, "detail": where + "every frame already measured", "fraction": 1}
    return {"title": title,
            "detail": where + js_string(overall["done"]) + " of " + js_string(overall["total"])
                      + " frames measured in all",
            "fraction": overall["done"] / overall["total"]}
