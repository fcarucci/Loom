"""Finding and reading an ASIAIR card: ``pixinsight/script/lib/Asiair.js`` 1-255, read-only.

Ported from the JavaScript:

* ``MOUNTS`` and ``SOURCES`` (Asiair.js 13-14): :data:`MOUNT_ROOTS` (macOS) and :data:`SOURCES`. The Siril port
  takes the mount roots as a list, see :func:`mount_roots`.
* ``looksLikeCard`` (Asiair.js 29-37): :func:`looks_like_card`.
* ``detect`` (Asiair.js 47-66): :func:`detect`. The JavaScript walks one mount point; this walks each root it is
  given, and reports ``k of n`` over all the volumes of all of them.
* ``entriesIn`` (Asiair.js 75-79): :func:`entries_in`, over ``Util.findEntries`` (lib/Util.js 1209-1222), whose
  default leaves out every name that starts with ``.``: the AppleDouble ``._`` twins too. Those are therefore never
  frames and never reported as unparseable, as in the JavaScript.
* ``frameFrom`` (Asiair.js 88-103): :func:`frame_from`.
* ``scanCard`` (Asiair.js 113-221): :func:`scan_card`, with its inner functions as nested functions of the same
  names: ``alive`` (Asiair.js 130-136), ``processFrame`` (138-145), ``reportProgress`` (147-151), ``take``
  (153-168), ``scanLights`` (170-179), ``scanFlats`` (181-188), ``scanDarks`` (190-198), and the final removed check
  (209-218).
* ``describe`` (Asiair.js 246-255): :func:`describe`, reading the header through ``frames_headers.quiet_header``.

New here, with no JavaScript counterpart: :func:`mount_roots`, which says where the mount points are on each
platform (macOS ``/Volumes``, Linux ``/media/<user>``, ``/run/media/<user>`` and ``/mnt``, Windows drive letters).

Read-only: nothing in this module writes to a card or to a frame. The JavaScript's ``processEvents`` calls have no
counterpart here and are dropped; cancellation is the caller's ``should_stop`` callback, checked between volumes and
before each directory and each frame, as in the JavaScript.

Semantics kept exactly:

* The detection is by shape only: ``Plan/Light`` or ``Autorun/Light`` directly under the volume. Two directory tests
  per volume, short-circuited as in the JavaScript, and no walking: a stalled network mount costs those two calls.
* A card removed while it is being scanned reads as ``removed``, never as a short but successful scan.
* The walk is depth-limited and not recursive.

Deliberate differences from the JavaScript, all in the directory listing and not in any decision about a name:

* Directory listings are sorted by UTF-16 code units. ``FileFind`` yields the operating system's order, which the
  JavaScript does not promise, so a stable order is chosen here.
* An unreadable directory lists as empty (``Util.findEntries`` on a missing folder), not as an error.
"""

from __future__ import annotations

import os
import string
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Optional

from loomsiril import asiair_names as A
from loomsiril.frames_headers import quiet_header

Record = dict[str, Any]
Card = dict[str, Any]

MOUNT_ROOTS: tuple[str, ...] = ("/Volumes",)  # Asiair.js 22: the macOS mount point
SOURCES: tuple[str, ...] = ("Plan", "Autorun")  # Asiair.js 23


def _utf16_sort_key(name: str) -> bytes:
    """Sort key that orders names by UTF-16 code units, as JavaScript string comparison does."""
    return name.encode("utf-16-be", "surrogatepass")


def _exists_dir(path: str) -> bool:
    """Whether ``path`` is an existing directory; a stat that fails reads as no."""
    try:
        return os.path.isdir(path)
    except (OSError, ValueError):
        return False


def mount_roots(
    platform: str = sys.platform, env: Mapping[str, str] = os.environ, user: Optional[str] = None
) -> list[str]:
    """The directories whose children are candidate volumes, for ``platform``.

    macOS: ``["/Volumes"]``. Linux: ``/media/<user>``, ``/run/media/<user>`` and ``/mnt``; the per-user roots are
    left out when no user name is known (``user``, else ``env["USER"]``, else ``env["LOGNAME"]``), because
    ``/media/`` itself would be listed as a volume. Windows: each drive letter ``A:\\`` to ``Z:\\`` that exists.
    Any other platform: no roots, so nothing is detected.
    """
    if platform == "darwin":
        return list(MOUNT_ROOTS)
    if platform.startswith("linux"):
        who = user if user is not None else (env.get("USER") or env.get("LOGNAME") or "")
        roots = [f"/media/{who}", f"/run/media/{who}"] if who else []
        roots.append("/mnt")
        return roots
    if platform == "win32":
        return [f"{letter}:\\" for letter in string.ascii_uppercase if _exists_dir(f"{letter}:\\")]
    return []


def looks_like_card(root: str) -> bool:
    """Whether ``root`` has the ASIAIR layout: ``Plan/Light`` or ``Autorun/Light`` (Asiair.js 29-37)."""
    try:
        return os.path.isdir(os.path.join(root, "Plan", "Light")) or os.path.isdir(
            os.path.join(root, "Autorun", "Light"))
    except Exception:  # noqa: BLE001 -- the JavaScript returns false on any exception here
        return False


def entries_in(directory: str, want_directories: bool) -> list[str]:
    """Names of the entries of ``directory``, one level, of the wanted kind, without names starting with ``.``.

    Asiair.js 75-79 over Util.findEntries. Sorted (see the module docstring). A directory that cannot be listed
    gives no names.
    """
    try:
        with os.scandir(directory) as it:
            entries = list(it)
    except OSError:
        return []

    names: list[str] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            is_dir = False
        if is_dir == bool(want_directories):
            names.append(entry.name)
    names.sort(key=_utf16_sort_key)
    return names


def detect(roots: Iterable[str], should_stop: Optional[Callable[[], bool]] = None,
           on_volume: Optional[Callable[[int, int, str], None]] = None) -> list[str]:
    """The volumes under ``roots`` that carry the ASIAIR layout (Asiair.js 47-66).

    Every root is listed first, so each volume can be reported as ``k of n``. ``should_stop`` is checked before each
    root is listed and before each volume is looked at; a stop returns the cards found so far. ``on_volume(k, n,
    path)`` is called before each volume is checked. Returns the card roots in listing order.
    """
    volumes: list[str] = []
    for mount in roots:
        if should_stop and should_stop():
            break
        volumes.extend(os.path.join(mount, name) for name in entries_in(mount, True))

    found: list[str] = []
    for index, root in enumerate(volumes):
        if should_stop and should_stop():
            break
        if on_volume:
            on_volume(index + 1, len(volumes), root)
        if looks_like_card(root):
            found.append(root)
    return found


def frame_from(directory: str, name: str, target: Optional[str], source: str) -> Optional[Record]:
    """One filename as a frame, or None when it is not one (Asiair.js 88-103).

    A name that parses but carries an impossible date gives a None key, so it is refused here, while the filename is
    still in hand to report. ``target`` is the folder, not the name: the firmware leaves the target out of the name,
    so a folder target replaces the one parsed from it. ``None`` keeps the parsed one.
    """
    f = A.parse_name(name)
    if f is None:
        return None
    key = A.stamp_key(f["stamp"])
    if key is None:
        return None

    f["path"] = os.path.join(directory, name)
    f["source"] = source
    f["key"] = key
    if target is not None:
        f["target"] = target
    return f


def scan_card(root: str, on_progress: Optional[Callable[[int], None]] = None,
              should_stop: Optional[Callable[[], bool]] = None) -> Card:
    """Walk a card and list its lights, flats and darks (Asiair.js 113-221).

    Returns ``{lights, flats, darks, unparseable, cancelled, removed}``, as the JavaScript does. ``on_progress(n)``
    is called after each file with the number of lights and flats read so far (darks are not counted, as in the
    JavaScript). ``should_stop()`` is checked before each directory and each file.
    """
    out: Card = {"lights": [], "flats": [], "darks": [], "unparseable": [], "cancelled": False, "removed": False}

    if not looks_like_card(root):
        out["removed"] = True
        return out

    def alive() -> bool:
        """Re-check the card before each step: False when the walk must stop (cancelled, or the card went away)."""
        if should_stop and should_stop():
            out["cancelled"] = True
            return False
        if not looks_like_card(root):
            out["removed"] = True
            return False
        return True

    def process_frame(directory: str, name: str, target: Optional[str], source: str, into: list[Record]) -> None:
        f = frame_from(directory, name, target, source)
        if f is None:
            out["unparseable"].append(os.path.join(directory, name))
        else:
            into.append(f)

    def report_progress() -> None:
        if on_progress:
            on_progress(len(out["lights"]) + len(out["flats"]))

    def take(directory: str, target: Optional[str], source: str, into: list[Record]) -> bool:
        if not alive():
            return False
        for name in entries_in(directory, False):
            process_frame(directory, name, target, source, into)
            report_progress()
            if not alive():
                return False
        return True

    def scan_lights(src: str) -> bool:
        light_dir = os.path.join(root, src, "Light")
        targets = entries_in(light_dir, True) if _exists_dir(light_dir) else []
        for target in targets:
            if not take(os.path.join(light_dir, target), target, src, out["lights"]):
                return False
        return True

    def scan_flats(src: str) -> bool:
        flat_dir = os.path.join(root, src, "Flat")
        if _exists_dir(flat_dir):
            if not take(flat_dir, None, src, out["flats"]):
                return False
        return True

    def scan_darks(src: str) -> bool:
        # Darks sit in one folder, like flats: the master-flats step may use them in place of a darks folder.
        dark_dir = os.path.join(root, src, "Dark")
        if _exists_dir(dark_dir):
            if not take(dark_dir, None, src, out["darks"]):
                return False
        return True

    for src in SOURCES:
        if not scan_lights(src):
            return out
        if not scan_flats(src):
            return out
        if not scan_darks(src):
            return out

    # A card removed after the last directory was walked would otherwise read as a clean scan.
    if not looks_like_card(root):
        out["removed"] = True

    return out


def describe(frame: Mapping[str, Any]) -> Record:
    """A card frame as the flat matcher needs it (Asiair.js 246-255).

    ``filter`` and ``binning`` come from the header, the authoritative source. ``camera`` is the header ``INSTRUME``,
    falling back to the filename token. ``rotation`` is the filename token only: the ASIAIR writes no standard FITS
    keyword for it, and both sides of a flat comparison come off the same card with the same naming. A header that
    cannot be read gives None for its keywords, as the JavaScript does.
    """
    _info, keyword = quiet_header(frame["path"])
    return {
        "path": frame["path"],
        "filter": keyword("FILTER"),
        "binning": keyword("XBINNING"),
        "camera": keyword("INSTRUME") or frame.get("camera"),
        "rotation": frame.get("rotation"),
    }
