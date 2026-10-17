"""The colour-master loader: one master per colour slot, split once into cached one-channel plates.

The port of Loom's ``Pipeline.oscGroupLoader`` with its cache policy (``Pipeline.oscCacheIo``), the group's log line
(``Pipeline.oscGroupLogLine``) and the plane extraction (``Steps.extractPlane``). One :class:`ColourGroupLoader`
serves the sibling channels of a slot (R, G, B for ``OSC``; H, O for ``OSCNB``). It hands out a plate, a one-channel
FITS file, per plane:

* a plate handed out earlier in the run is handed out again;
* else the cache entry under the sibling key;
* else the master is read, once for all its planes: every plane is measured and checked, the verdict goes to a sidecar
  next to the cache entries, and each plane the slot uses is stored under its sibling key and kept for its sibling.

So the master is read at most once per run, and not at all when every plane the run asks for is cached. A master that
is not an uncompressed FITS file (a ``.fz`` or an XISF) is first converted once through Siril; the converter is passed
in, so this module does not touch Siril.

Cache policy, as the stages have it: with the cache off nothing is read from or written to the cache (the plates are
files in the work folder and live for the run); with ``ignore_cache`` nothing is read, everything is written.
"""

from __future__ import annotations

import json
import os
import re
from decimal import ROUND_HALF_UP, Decimal
from collections.abc import Sequence
from typing import Any, Callable, Mapping, Optional, Union

import numpy as np

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import cache, channels, osc

__all__ = ["ColourGroupLoader", "group_log_line", "plane_statistics", "is_plain_fits", "fit_loomsrc",
           "composite_loomsrc"]

#: The FITS FILTER value of each logical channel of a colour master, as Loom's exports name them.
_PLANE_NAMES = {"H": "Ha", "O": "OIII"}

#: Longest LOOMSRC string once its apostrophes are doubled: a card holds 80 characters, 12 of them are the keyword,
#: the equals sign and the quotes.
_LOOMSRC_MAX = 68
#: What replaces the start of a name too long for the card.
_ELLIPSIS = "..."

_SEPARATORS = re.compile(r"[\\/]")
#: Brackets the token that stands for a file name while the oracle text is split (a file name cannot hold NUL).
_MARK = "\x00"


def is_plain_fits(path: str) -> bool:
    """True when the file is a FITS file with its image in the primary HDU: the ones ``read_fits`` takes directly.

    Decided by content, not by name: an XISF, a tile-compressed FITS (empty primary HDU, image in an extension) and a
    file that cannot be read as FITS are not plain, whatever their extension says.
    """
    try:
        with open(path, "rb") as fh:
            if fh.read(6) != b"SIMPLE":
                return False
            fh.seek(0)
            header = channels._read_hdu_header(fh, path)
    except (OSError, ValueError):
        return False
    naxis = header.get("NAXIS")
    return isinstance(naxis, int) and not isinstance(naxis, bool) and naxis > 0


def plane_statistics(plane: np.ndarray) -> dict[str, float]:
    """The minimum, maximum and median of one plane (Python floats)."""
    return {"min": float(plane.min()), "max": float(plane.max()), "median": float(np.median(plane))}


def _to_fixed5(v: Any) -> str:
    """JavaScript's ``Number.prototype.toFixed(5)``: the exact value rounded half up (away from zero), no ``-0``."""
    x = float(v)
    if x != x or x in (float("inf"), float("-inf")):
        return "NaN" if x != x else ("Infinity" if x > 0 else "-Infinity")
    d = Decimal(x).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    return format(abs(d) if d == 0 else d, "f")


def group_log_line(slot: str, path: str, kind: str, filter_value: Optional[str], rec: Mapping[str, Any]) -> str:
    """``Pipeline.oscGroupLogLine``: the log line of one colour group, from its validation record."""
    name = _SEPARATORS.split(path)[-1]
    members = "/".join(osc.OSC_RECIPES[slot])
    values = ", ".join(
        _to_fixed5(p["min"]) + "/" + _to_fixed5(p["max"]) + "/" + _to_fixed5(p["median"]) for p in rec["planes"])
    return ("OSC " + name + ": " + kind + ", filter " + ("null" if filter_value is None else filter_value)
            + ", planes " + members + ", min/max/median " + values)


def _ascii(text: str) -> str:
    """``text`` as FITS holds it: ASCII, each other character replaced by ``?``."""
    return text.encode("ascii", "replace").decode("ascii")


def _card_width(text: str) -> int:
    """What ``text`` takes in a FITS string card: its length plus one more for each apostrophe (FITS doubles them)."""
    return len(text) + text.count("'")


def _trim_name(name: str, share: int) -> str:
    """``name`` within ``share`` card characters: whole when it fits, else ``...`` and the end of the name."""
    if _card_width(name) <= share:
        return name
    if share <= len(_ELLIPSIS):
        return _ELLIPSIS[:max(share, 0)]
    kept: list[str] = []
    used = len(_ELLIPSIS)
    for ch in reversed(name):
        used += 2 if ch == "'" else 1
        if used > share:
            break
        kept.append(ch)
    return _ELLIPSIS + "".join(reversed(kept))


def fit_loomsrc(parts: Sequence[Union[str, tuple[str]]]) -> str:
    """A LOOMSRC value from its parts, as one FITS string that fits a card (68 characters once apostrophes are doubled).

    A part that is a string is kept as it is (the group word, the plane list, the separators); a part that is a
    one-item tuple holds a file name and may be trimmed. Only file names are trimmed, never the rest: a name that is
    too long loses its start (replaced by ``...``) and keeps its end, which carries the filter, the suffix and the
    extension that tell two masters of one session apart. When several names are given, each gets an equal share of
    what the fixed text leaves. FITS strings are ASCII, so every other character becomes ``?``. Never raises, whatever
    the names hold.
    """
    fixed = sum(_card_width(_ascii(p)) for p in parts if isinstance(p, str))
    names = [p for p in parts if not isinstance(p, str)]
    share = (_LOOMSRC_MAX - fixed) // len(names) if names else 0
    return "".join(_ascii(p) if isinstance(p, str) else _trim_name(_ascii(p[0]), share) for p in parts)


def composite_loomsrc(chans: Mapping[str, Any], keys: Sequence[str], per_plane: bool) -> Optional[str]:
    """The LOOMSRC value of a composite or a palette, or None when none of its channels comes from a colour master.

    The text is ``osc.osc_source_text`` (the one Loom writes), with each master's file name trimmed by
    :func:`fit_loomsrc` when the whole does not fit a card. ``chans`` maps a channel to ``{"group", "path", "plane"}``.
    """
    tokens: dict[str, str] = {}
    marked: dict[str, Any] = {}
    for key, chan in chans.items():
        if not (isinstance(chan, Mapping) and chan.get("group")):
            marked[key] = chan
            continue
        name = _SEPARATORS.split(os.fspath(chan["path"]))[-1]
        marked[key] = {**chan, "path": tokens.setdefault(name, f"{_MARK}{len(tokens)}{_MARK}")}
    text = osc.osc_source_text(marked, list(keys), per_plane)
    if text is None:
        return None
    names = {token: name for name, token in tokens.items()}
    parts: list[Union[str, tuple[str]]] = []
    for piece in re.split(f"({_MARK}[0-9]+{_MARK})", text):
        if piece in names:
            parts.append((names[piece],))
        elif piece:
            parts.append(piece)
    return fit_loomsrc(parts)


def _loomsrc(path: str, plane: int) -> str:
    """The LOOMSRC value of a channel plate: the master's file name and the plane, as one FITS string that fits a card.

    A name that is too long loses its start, never its end (see :func:`fit_loomsrc`). Never raises.
    """
    return fit_loomsrc([(os.path.basename(path),), f"|plane:{plane}"])


class ColourGroupLoader:
    """The plates of one colour master. See the module text for the order in which a plate is found.

    ``store`` is the run's :class:`~loomsiril.loom.cache.CacheStore`, or None with the cache off. ``work_dir`` holds
    the plates that are not cached and the converted master. ``convert(path, name)`` turns a master that is not a plain
    FITS file into one, named ``name`` in the work folder, and returns its path (the run passes ``ingest_channel``).
    ``warn`` receives the complaints about a cache write that failed.
    """

    def __init__(self, slot: str, kind: str, path: str, fingerprint: str, *, store: Optional[cache.CacheStore],
                 work_dir: str, ignore_cache: bool = False,
                 convert: Optional[Callable[[str, str], str]] = None,
                 warn: Optional[Callable[[str], None]] = None) -> None:
        self.slot = slot
        self.kind = kind
        self.path = path
        self.identity = osc.osc_group_identity(fingerprint, kind)
        self.opens = 0
        self._store = store
        self._work = work_dir
        self._reads_cache = store is not None and not ignore_cache
        self._convert = convert
        self._warn = warn if warn is not None else (lambda text: None)
        self._names = {plane: member for member, plane in osc.OSC_RECIPES[slot].items()}
        self._handed: dict[int, str] = {}   # plane -> plate given out this run
        self._kept: dict[int, str] = {}     # plane -> plate nobody has taken yet
        self._record: Optional[dict[str, Any]] = None   # this run's own record, when no sidecar can be read
        self._waiting: list[Callable[[dict[str, Any]], None]] = []

    # -- the plates -----------------------------------------------------------------------------------------------

    def plate(self, plane: int) -> str:
        """The path of the one-channel plate of ``plane``."""
        if plane in self._handed:
            return self._handed[plane]
        if plane not in self._kept:
            hit = self._cache_hit(plane)
            if hit is not None:
                self._kept[plane] = hit
            else:
                self._open_and_split()
        self._handed[plane] = self._kept.pop(plane)
        return self._handed[plane]

    def sibling_key(self, plane: int) -> str:
        return osc.osc_sibling_key(self.identity, plane)

    def _cache_hit(self, plane: int) -> Optional[str]:
        if not self._reads_cache or self._store is None:
            return None
        key = self.sibling_key(plane)
        return self._store.path_for(key) if self._store.load(key) is not None else None

    def _readable_master(self) -> str:
        if is_plain_fits(self.path):
            return self.path
        if self._convert is None:
            raise ValueError(self.path + ": a colour master of this format needs the converter")
        return self._convert(self.path, "osc_" + cache.hash_str(self.identity)[:16])

    def _forget(self, converted: str) -> None:
        """Deletes the converted copy of the master once it has been read (it is only a stepping stone)."""
        if converted != self.path:
            try:
                os.remove(converted)
            except OSError:
                pass

    def _open_and_split(self) -> None:
        self.opens += 1
        readable = self._readable_master()
        try:
            planes, header = read_fits(readable)
        finally:
            self._forget(readable)
        if len(planes) < 3:
            raise ValueError(f"{self.path} is not a colour master: it has {len(planes)} channel"
                             + ("" if len(planes) == 1 else "s"))
        checked = osc.OSC_VALIDATED_PLANES[self.slot]
        stats = [plane_statistics(planes[q]) for q in checked]
        for q, s in zip(checked, stats):
            problem = osc.osc_plane_problem(s, q, self.path)
            if problem is not None:
                raise ValueError(problem)
        self._record = osc.osc_validation_record(stats)
        self._write_sidecar(self._record)
        for plane in sorted(self._names):
            if plane in self._handed:
                continue  # a sibling already holds it, and its cache entry exists
            self._kept[plane] = self._store_plate(plane, planes[plane], header)
        self._announce()

    def _store_plate(self, plane: int, pixels: np.ndarray, master_header: Mapping[str, Any]) -> str:
        member = self._names[plane]
        header = {k: v for k, v in master_header.items() if k not in ("FILTER", "LOOMSRC")}
        header["FILTER"] = _PLANE_NAMES.get(member, member)
        header["LOOMSRC"] = _loomsrc(self.path, plane)
        if self._store is not None:
            key = self.sibling_key(plane)
            meta = {"channel": member, "stage": "split", "params": {"recipe": "plane:" + str(plane)}}
            stored = self._store.store(key, [pixels], header, meta)
            if stored is not None:
                return stored
            self._warn(f"[cache] plane {plane} of {os.path.basename(self.path)} could not be cached; "
                       "the run uses a copy in the work folder")
        out = os.path.join(self._work, f"{member}_src.fits")
        write_fits(out, [pixels], header, bitpix=-32)
        return out

    # -- the validation record and the log line -------------------------------------------------------------------

    def _sidecar_path(self) -> Optional[str]:
        if self._store is None:
            return None
        return os.path.join(self._store.folder, osc.osc_validate_name(self.identity))

    def _write_sidecar(self, record: Mapping[str, Any]) -> None:
        path = self._sidecar_path()
        if path is None:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(record))
        except (OSError, TypeError, ValueError) as exc:
            self._warn(f"could not write {os.path.basename(path)}: {exc}")

    def _read_sidecar(self) -> Optional[dict[str, Any]]:
        path = self._sidecar_path()
        if path is None or not self._reads_cache or not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
            planes = rec["planes"]
            if len(planes) != len(osc.OSC_VALIDATED_PLANES[self.slot]):
                return None
            for p in planes:
                float(p["min"]), float(p["max"]), float(p["median"])
            return rec
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def validation(self) -> Optional[dict[str, Any]]:
        """The sidecar's record, or this run's own when the sidecar cannot be read (None before any record)."""
        rec = self._read_sidecar()
        return rec if rec is not None else self._record

    def when_validated(self, fn: Callable[[dict[str, Any]], None]) -> None:
        """Calls ``fn(record)`` once, as soon as a record exists: now (a sidecar), or after the first split."""
        self._waiting.append(fn)
        self._announce()

    def _announce(self) -> None:
        rec = self.validation()
        if rec is None:
            return
        waiting, self._waiting = self._waiting, []
        for fn in waiting:
            fn(rec)
