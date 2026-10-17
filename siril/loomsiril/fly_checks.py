"""The file-system checks behind the Fly-Through dialog's inputs (existence, readability, writability).

They touch the disk (and, on a network share, can stall), so the dialog runs them on a worker thread and hands the
results to the pure rules in loomsiril.fly_model. Nothing here imports tkinter.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Optional

__all__ = ["KIND_LABELS", "check_file", "check_out_dir", "file_stamp", "validate"]

#: What each kind of path is called in a message.
KIND_LABELS = {"image": "image", "starless": "starless", "stars": "stars", "music": "music", "ffmpeg": "ffmpeg", "logo": "logo"}


def check_file(kind: str, path: str) -> str:
    """"" when ``path`` is a readable file, else the message to show."""
    p = os.path.expanduser(path.strip())
    if os.path.isfile(p) and os.access(p, os.R_OK):
        return ""
    return f"The {KIND_LABELS.get(kind, kind)} file does not exist or cannot be read: {path.strip()}"


def check_logo(path: str) -> str:
    """"" when ``path`` is a logo file that loomsiril.logo can read (PNG, TIFF or FITS), else the message to show. Reads
    the picture, so call it on a worker thread."""
    from loomsiril import logo

    try:
        logo.read_logo(os.path.expanduser(path.strip()))
    except logo.LogoError as exc:
        return str(exc)
    except Exception as exc:                  # anything else a damaged file can raise
        return f"The logo file cannot be read: {path.strip()} ({exc})"
    return ""


def check_out_dir(path: str, create: bool = False) -> Optional[str]:
    """A message when ``path`` cannot be the output folder, else None. ``create`` makes a missing folder (Render does;
    the live check only looks, and accepts a missing folder whose nearest existing parent is writable)."""
    path = os.path.expanduser(path.strip())
    if not path:
        return "Choose an output folder."
    if os.path.exists(path):
        if not os.path.isdir(path):
            return f"The output folder is not a folder: {path}"
        return None if os.access(path, os.W_OK) else f"The output folder is not writable: {path}"
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            return f"The output folder cannot be created ({exc.strerror or exc}): {path}"
        return None if os.access(path, os.W_OK) else f"The output folder is not writable: {path}"
    parent = os.path.dirname(os.path.abspath(path))
    while parent and not os.path.exists(parent):
        nxt = os.path.dirname(parent)
        if nxt == parent:
            break
        parent = nxt
    if not (os.path.isdir(parent) and os.access(parent, os.W_OK)):
        return f"The output folder cannot be created here: {path}"
    return None


def file_stamp(path: str) -> Optional[tuple[int, int]]:
    """(modification time in ns, size) of a file, or None when it cannot be read."""
    try:
        st = os.stat(os.path.expanduser(path.strip()))
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def validate(requests: Iterable[tuple[str, str]], open_grid: Optional[tuple[int, int, int]] = None
             ) -> dict[tuple[str, str], str]:
    """The result of each ``(kind, path)`` request: kinds image, starless, stars, music (files), logo (a picture that loomsiril.logo reads), ffmpeg (a chosen binary, run with -version) and dir (the output folder).

    ``open_grid`` (width, height, channels of the open image): an image file that exists must also fit it
    (:func:`loomsiril.fly_grid.verdict`: the same field, only scaled, with the same channels); the file's header is read."""
    from loomsiril import fly_grid

    out: dict[tuple[str, str], str] = {}
    for kind, path in requests:
        if kind == "dir":
            out[(kind, path)] = check_out_dir(path) or ""
        elif kind == "gaia_dir":                  # the folder of local Gaia files: it must be a folder (the files are read at render)
            out[(kind, path)] = "" if os.path.isdir(os.path.expanduser(path.strip())) else f"The Gaia folder is not there: {path.strip()}"
        elif kind == "ffmpeg":                    # a user-chosen ffmpeg: it must answer -version (runs it, so not on the Tk thread)
            from loomsiril import fly_video

            why = fly_video.validate_ffmpeg(os.path.expanduser(path))[1]
            out[(kind, path)] = f"ffmpeg: {why}" if why else ""
        elif kind == "logo":                      # the logo is opened and read here (a PNG, TIFF or FITS): never on the Tk thread
            out[(kind, path)] = check_logo(path)
        else:
            out[(kind, path)] = check_file(kind, path)
            if kind == "image" and open_grid is not None and not out[(kind, path)]:
                usable, text = fly_grid.grid_line(path, open_grid)
                out[(kind, path)] = "" if usable else text
    return out
