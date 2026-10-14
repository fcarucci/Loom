"""Every Siril action the Loom port performs, behind one interface.

Stages are written against :class:`SirilOps`. Two implementations exist:

* :class:`SirilCommandOps` drives a connected sirilpy ``SirilInterface`` (Siril 1.4.4; the command forms are the
  ones measured with siril-cli). Every call must come from the thread that created the object, a command is never
  sent while the image lock is held, and a failing command raises :class:`SirilOpsError`.
* :class:`RecordingOps` is the fake for tests: it records the exact command texts, serves canned results, and keeps
  the loaded image in memory (FITS read and write through ``loomsiril.io_fits``).

sirilpy is never imported by this module: the connected interface is passed in.

SPCC state is kept per Siril session (measured on Siril 1.4.4 with siril-cli): after a broadband ``spcc`` with
``-monosensor``, ``-rfilter``, ``-gfilter`` and ``-bfilter``, a later ``spcc -narrowband`` in the same session fails
with "Argument error". The narrowband command succeeds when it also carries all four of those names (a partial set
fails), so :meth:`SirilOps.spcc_narrowband` takes them as an option: all four or none.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import math
import ntpath
import os
import posixpath
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

import numpy as np

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.siril_bridge import planes_from_pixeldata

__all__ = [
    "BUILTIN_ICC_PROFILES",
    "RecordingOps",
    "SirilCommandOps",
    "SirilOps",
    "SirilOpsError",
    "command_text",
    "quote_path",
    "spcc_database_candidates",
    "siril_data_dir",
    "spcc_names",
]

#: Profile names Siril knows without a file (``icc_assign`` takes them unquoted).
BUILTIN_ICC_PROFILES = ("sRGB", "sRGBlinear", "Rec2020", "Rec2020linear", "working")

_SPCC_FOLDER = "siril-spcc-database"
_SPCC_KINDS = {
    "monosensor": ("MONO_SENSOR", None),
    "redfilter": ("MONO_FILTER", "RED"),
    "greenfilter": ("MONO_FILTER", "GREEN"),
    "bluefilter": ("MONO_FILTER", "BLUE"),
}
_CHANNEL_LETTERS = ("R", "G", "B")
_KEY_RE = re.compile(r"[A-Z0-9_-]{1,8}")
_PIPELINE_THREAD_MESSAGE = "Siril commands must come from the pipeline thread"


class SirilOpsError(RuntimeError):
    """A Siril command failed: ``command`` is the text that was sent, ``original`` the exception it raised."""

    def __init__(self, command: str, original: BaseException) -> None:
        super().__init__(f"{command}: {original}")
        self.command = command
        self.original = original


# ---------------------------------------------------------------------------------------------------------------
# Command text. Every command the port sends is built by one of the functions below; the RecordingOps transcript
# and the SirilCommandOps sends use the same text.
# ---------------------------------------------------------------------------------------------------------------


def _num(value: Any) -> str:
    """A number as command text: the shortest decimal that reads back as the same float (``repr``)."""
    if isinstance(value, (bool, np.bool_)):
        raise TypeError("a boolean is not a Siril number")
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    f = float(value)
    if not math.isfinite(f):
        raise ValueError(f"not a finite number: {value!r}")
    return repr(f)


def _check_line(text: str) -> None:
    if "\n" in text or "\r" in text:
        raise ValueError("a Siril command must be one line")


def _part(value: Any) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_)):
        text = _num(value)
    else:
        text = str(value)
    _check_line(text)
    return text


def command_text(*parts: Any) -> str:
    """The text sent for one command: the parts joined by single spaces (numbers written by ``_num``)."""
    return " ".join(_part(p) for p in parts)


def _quoted(text: str) -> str:
    """``text`` in double quotes. Siril has no escape inside quotes, so a double quote is refused."""
    _check_line(text)
    if '"' in text:
        raise ValueError(f"Siril cannot take a double quote in a path or name: {text!r}")
    return f'"{text}"'


def quote_path(path: Any) -> str:
    """A path as a quoted command argument. A path containing a double quote raises ValueError."""
    text = os.fspath(path)
    if not text:
        raise ValueError("empty path")
    return _quoted(text)


def _plain_name(name: Any) -> str:
    """A sequence name Siril takes unquoted: no whitespace and no double quote."""
    text = str(name)
    if not text or any(c.isspace() or c == '"' for c in text):
        raise ValueError(f"not a plain name (no spaces or quotes): {name!r}")
    return text


def _whole(value: Any, what: str) -> int:
    f = float(value)
    if not f.is_integer():
        raise ValueError(f"{what} must be a whole number, got {value!r}")
    return int(f)


def _channel_letter(channel: Any) -> str:
    if isinstance(channel, str) and channel.upper() in _CHANNEL_LETTERS:
        return channel.upper()
    if isinstance(channel, (int, np.integer)) and 0 <= int(channel) < len(_CHANNEL_LETTERS):
        return _CHANNEL_LETTERS[int(channel)]
    raise ValueError(f"channel must be R, G, B (or 0, 1, 2), got {channel!r}")


def _text_cd(path: Any) -> str:
    return command_text("cd", quote_path(path))


def _text_load(path: Any) -> str:
    return command_text("load", quote_path(path))


def _text_save(path: Any) -> str:
    if not os.fspath(path).endswith(".fits"):
        raise ValueError(f"save needs a path ending in .fits (Siril writes x.fit for a bare x): {path!r}")
    return command_text("save", quote_path(path))


def _text_platesolve(force: bool, focal: Any, pixel_size: Any) -> str:
    parts: list[Any] = ["platesolve"]
    if force:
        parts.append("-force")
    if focal is not None:
        parts.append(f"-focal={_num(focal)}")
    if pixel_size is not None:
        parts.append(f"-pixelsize={_num(pixel_size)}")
    return command_text(*parts)


def _spcc_name_args(sensor: Any, r: Any, g: Any, b: Any) -> list[str]:
    """The four SPCC name arguments, each one quoted token: ``"-monosensor=<S>" "-rfilter=<R>" ...``."""
    for label, value in (("sensor", sensor), ("red filter", r), ("green filter", g), ("blue filter", b)):
        if not value:
            raise ValueError(f"spcc needs the {label} name")
    return [_quoted(f"-monosensor={sensor}"), _quoted(f"-rfilter={r}"), _quoted(f"-gfilter={g}"),
            _quoted(f"-bfilter={b}")]


def _spcc_checks(sensor: Any, r: Any, g: Any, b: Any) -> list[tuple[str, Any, str]]:
    """(database kind, name, label) for each SPCC name: what :meth:`SirilCommandOps._require_spcc` looks up."""
    return [("monosensor", sensor, "sensor"), ("redfilter", r, "red filter"), ("greenfilter", g, "green filter"),
            ("bluefilter", b, "blue filter")]


def _text_spcc_broadband(sensor: str, r: str, g: str, b: str) -> str:
    return command_text("spcc", *_spcc_name_args(sensor, r, g, b))


def _spcc_session_names(sensor: Any, r: Any, g: Any, b: Any) -> Optional[tuple[Any, Any, Any, Any]]:
    """The optional narrowband names: all four (returned) or none (None). A partial set raises ValueError."""
    given = [v is not None for v in (sensor, r, g, b)]
    if not any(given):
        return None
    if not all(given):
        raise ValueError("narrowband SPCC takes all four of sensor, red filter, green filter and blue filter, or none")
    return sensor, r, g, b


def _text_spcc_narrowband(rwl: Any, rbw: Any, gwl: Any, gbw: Any, bwl: Any, bbw: Any,
                          names: Optional[Sequence[Any]] = None) -> str:
    head: list[str] = [] if names is None else _spcc_name_args(*names)
    return command_text("spcc", *head, "-narrowband", f"-rwl={float(rwl):.2f}", f"-rbw={float(rbw):.2f}",
                        f"-gwl={float(gwl):.2f}", f"-gbw={float(gbw):.2f}", f"-bwl={float(bwl):.2f}",
                        f"-bbw={float(bbw):.2f}")


def _text_link(name: Any, cwd: Any) -> str:
    # the whole -out= argument quoted as one token: -out="<dir>" makes Siril write into a directory named '"'
    return command_text("link", _plain_name(name), _quoted(f"-out={os.fspath(cwd)}"))


def _text_setref(name: Any, index1: Any) -> str:
    index = _whole(index1, "reference index")
    if index < 1:
        raise ValueError(f"reference index is 1-based, got {index1!r}")
    return command_text("setref", _plain_name(name), index)


def _text_register(name: Any) -> str:
    return command_text("register", _plain_name(name), "-transf=homography", "-interp=lanczos4")


def _text_crop(x: Any, y: Any, w: Any, h: Any) -> str:
    return command_text("crop", _whole(x, "x"), _whole(y, "y"), _whole(w, "w"), _whole(h, "h"))


def _text_rgbcomp(r: Any, g: Any, b: Any, out: Any) -> str:
    # the -out= argument quoted as one token: -out="<path>" makes Siril name the file "<path>".fit (quotes kept)
    return command_text("rgbcomp", quote_path(r), quote_path(g), quote_path(b), _quoted(f"-out={os.fspath(out)}"))


def _text_ghs(cmd_tuple: Sequence[Any]) -> str:
    parts = tuple(cmd_tuple)
    if not parts or parts[0] != "autoghs":
        raise ValueError(f"ghs takes the autoghs command tuple from levels.ghs_command, got {parts[:1]!r}")
    for p in parts:
        if isinstance(p, str) and '"' in p:
            raise ValueError(f"ghs argument with a double quote: {p!r}")
    return command_text(*parts)


def _text_linear_match(ref: Any, low: Any, high: Any) -> str:
    return command_text("linear_match", quote_path(ref), _num(low), _num(high))


def _text_mtf(c0: Any, m: Any, channel: Any) -> str:
    parts: list[Any] = ["mtf", _num(c0), _num(m), 1]
    if channel is not None:
        parts.append(_channel_letter(channel))
    return command_text(*parts)


def _text_icc_assign(profile: Any) -> str:
    text = os.fspath(profile)
    if text in BUILTIN_ICC_PROFILES:
        return command_text("icc_assign", text)
    return command_text("icc_assign", quote_path(text))


def _text_update_key(key: Any, value: Any, comment: Optional[Any]) -> str:
    name = str(key).upper()
    if not _KEY_RE.fullmatch(name):
        raise ValueError(f"FITS keyword must be 1 to 8 of A-Z, 0-9, '-' or '_': {key!r}")
    parts: list[Any] = ["update_key", name, _quoted(str(value))]
    if comment is not None:
        parts.append(_quoted(str(comment)))
    return command_text(*parts)


def _text_denoise(mod: Any) -> str:
    return command_text("denoise", f"-mod={_num(mod)}")


def _text_subsky(degree: Any, samples: Any, tolerance: Any) -> str:
    return command_text("subsky", _whole(degree, "degree"), f"-samples={_whole(samples, 'samples')}",
                        f"-tolerance={_num(tolerance)}")


def _text_pyscript(path: Any, args: Sequence[Any]) -> str:
    parts: list[Any] = ["pyscript", quote_path(path)]
    for a in args:
        if isinstance(a, str) and any(c.isspace() for c in a):
            parts.append(_quoted(a))
        else:
            parts.append(a)
    return command_text(*parts)


_FIND_STARS_TEXTS = ("setfindstar reset -gaussian", "findstar")


def _star_dict(star: Any) -> dict[str, Any]:
    """One PSF star as a plain dict (positions and FWHM in pixels, Siril's coordinates)."""

    def field(name: str) -> Any:
        return star.get(name) if isinstance(star, Mapping) else getattr(star, name, None)

    def number(name: str, default: float = 0.0) -> float:
        value = field(name)
        return default if value is None else float(value)

    amplitude = number("A", number("amplitude"))
    mag = field("mag")
    return {
        "xpos": number("xpos"),
        "ypos": number("ypos"),
        "fwhmx": number("fwhmx"),
        "fwhmy": number("fwhmy"),
        "amplitude": amplitude,
        "A": amplitude,
        "mag": None if mag is None else float(mag),
        "rmse": number("rmse"),
        "beta": number("beta"),
        "angle": number("angle"),
    }


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, Mapping):
        return dict(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")}


def _planes_top_down(planes: Sequence[Any]) -> list[np.ndarray]:
    """One or three 2-D float32 planes (top-down), copied."""
    items = [np.array(p, dtype=np.float32, copy=True) for p in planes]
    if len(items) not in (1, 3):
        raise ValueError(f"expected one or three planes, got {len(items)}")
    shape = items[0].shape
    if len(shape) != 2 or any(p.shape != shape for p in items):
        raise ValueError("planes must be 2-D and all of the same shape")
    return items


def _pixeldata_from_planes(planes: Sequence[Any]) -> np.ndarray:
    """Top-down planes -> sirilpy's bottom-up pixel data: (h, w) for one plane, (3, h, w) for three."""
    bottom_up = [np.ascontiguousarray(p[::-1]) for p in _planes_top_down(planes)]
    return bottom_up[0] if len(bottom_up) == 1 else np.stack(bottom_up)


def _pipeline_only(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Assert that a SirilCommandOps method runs on the thread that created the object."""

    @functools.wraps(fn)
    def wrapper(self: "SirilCommandOps", *args: Any, **kwargs: Any) -> Any:
        self._check_thread()
        return fn(self, *args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------------------------------------------


class SirilOps:
    """The interface the stages are written against. Each method is one Siril action.

    Commands return None unless noted. Paths are plain strings or ``os.PathLike``; a path containing a double quote
    raises ValueError before anything is sent. Numbers are written as the shortest text that reads back as the same
    float. Subclasses implement every method; this base class only documents the contract.
    """

    def cd(self, path: Any) -> None:
        """Change Siril's working directory (``cd "<path>"``)."""
        raise NotImplementedError

    def wd(self) -> str:
        """Siril's current working directory (no command is sent)."""
        raise NotImplementedError

    def load(self, path: Any) -> None:
        """Load a FITS or XISF image (``load "<path>"``)."""
        raise NotImplementedError

    def save(self, path: Any) -> str:
        """Save the loaded image; ``path`` must end in ``.fits``. Returns the path written."""
        raise NotImplementedError

    def set32bits(self) -> None:
        """Set Siril's 32-bit-per-channel mode for processed images (a preference; the loaded image is untouched)."""
        raise NotImplementedError

    def fits_compression(self) -> Optional[bool]:
        """Whether Siril's FITS compression preference is on (None when Siril cannot say). With it on, ``save``
        writes ``<name>.fits.fz`` (lossy for 32-bit float), so a run turns it off and restores it afterwards."""
        raise NotImplementedError

    def set_fits_compression(self, on: bool) -> None:
        """Set Siril's FITS compression preference (``set compression.enabled=<true|false>``)."""
        raise NotImplementedError

    def platesolve(self, force: bool = False, focal: Optional[float] = None,
                   pixel_size: Optional[float] = None) -> None:
        """Plate-solve the loaded image (``platesolve [-force] [-focal=<mm>] [-pixelsize=<um>]``)."""
        raise NotImplementedError

    def spcc_broadband(self, sensor: str, r: str, g: str, b: str) -> None:
        """Spectrophotometric colour calibration with a camera and three filter names from Siril's database."""
        raise NotImplementedError

    def spcc_narrowband(self, rwl: float, rbw: float, gwl: float, gbw: float, bwl: float, bbw: float,
                        sensor: Optional[str] = None, r: Optional[str] = None, g: Optional[str] = None,
                        b: Optional[str] = None) -> None:
        """Narrowband calibration: wavelength and bandwidth (nm) of each of the three channels.

        ``sensor``, ``r``, ``g`` and ``b`` (the database names used by :meth:`spcc_broadband`) are optional and go
        together: all four or none, else ValueError before anything is sent. Pass them when a broadband ``spcc`` ran
        earlier in the same Siril session (see the module docstring).
        """
        raise NotImplementedError

    def link_sequence(self, name: str) -> None:
        """Link every FITS file in the working directory into the sequence ``name`` (sorted by file name).

        The output argument is sent as one quoted token, ``"-out=<cwd>"``: on Siril 1.4.4 the unquoted-token form
        ``-out="<cwd>"`` creates a directory named ``"`` and the sequence is not found afterwards.
        """
        raise NotImplementedError

    def setref(self, name: str, index1: int) -> None:
        """Make the 1-based frame ``index1`` of sequence ``name`` the registration reference."""
        raise NotImplementedError

    def register(self, name: str) -> None:
        """Register sequence ``name`` (homography, lanczos4; outputs ``r_<name>_NNNNN.fit``)."""
        raise NotImplementedError

    def crop(self, x: int, y: int, w: int, h: int) -> None:
        """Crop the loaded image; ``y`` is counted from the top."""
        raise NotImplementedError

    def rgbcomp(self, r: Any, g: Any, b: Any, out: Any) -> str:
        """Combine three mono images into ``<out>.fit``; returns that path."""
        raise NotImplementedError

    def ghs(self, cmd_tuple: Sequence[Any]) -> None:
        """Run an ``autoghs`` command tuple as built by ``levels.ghs_command``."""
        raise NotImplementedError

    def linear_match(self, ref: Any, low: float = 0, high: float = 0.92) -> None:
        """Linear-match the loaded image to the image at ``ref``."""
        raise NotImplementedError

    def mtf(self, c0: float, m: float, channel: Any = None) -> None:
        """Midtones transfer function with shadows ``c0`` and midtones ``m``; ``channel`` is R, G, B, or None."""
        raise NotImplementedError

    def find_stars(self) -> list[dict[str, Any]]:
        """Detect the stars of the loaded image; one dict per star (xpos, ypos, fwhmx, fwhmy in pixels, ...)."""
        raise NotImplementedError

    def analyse(self, path: Any) -> dict[str, Any]:
        """Statistics of the image file at ``path`` (no command; fields as Siril reports them)."""
        raise NotImplementedError

    def icc_assign(self, profile: Any) -> None:
        """Assign an ICC profile file, or a built-in name from :data:`BUILTIN_ICC_PROFILES`; pixels unchanged."""
        raise NotImplementedError

    def icc_profile(self) -> Optional[bytes]:
        """The loaded image's ICC profile bytes, or None."""
        raise NotImplementedError

    def update_key(self, key: str, value: str, comment: Optional[str] = None) -> None:
        """Set a FITS keyword on the loaded image."""
        raise NotImplementedError

    def denoise(self, mod: float) -> None:
        """Denoise the loaded image (``denoise -mod=<mod>``); not repeatable between runs."""
        raise NotImplementedError

    def subsky(self, degree: int = 2, samples: int = 20, tolerance: float = 1.0) -> None:
        """Background extraction with a polynomial of ``degree``."""
        raise NotImplementedError

    def pyscript(self, path: Any, args: Sequence[Any] = ()) -> None:
        """Run a Python script synchronously. An error inside it does not raise (Siril only logs it)."""
        raise NotImplementedError

    def log(self, text: str) -> None:
        """Write a line to Siril's log (no command)."""
        raise NotImplementedError

    def progress(self, text: str, fraction: float) -> None:
        """Report progress in [0, 1] to Siril (no command)."""
        raise NotImplementedError

    def pixels(self) -> list[np.ndarray]:
        """The loaded image as top-down float32 planes: one (h, w) plane, or three for colour."""
        raise NotImplementedError

    def set_pixels(self, planes: Sequence[Any]) -> None:
        """Replace the loaded image's pixels with top-down planes (one or three, all (h, w))."""
        raise NotImplementedError

    def header(self) -> str:
        """The loaded image's FITS header as card text."""
        raise NotImplementedError

    def is_cli(self) -> bool:
        """True when running under siril-cli."""
        raise NotImplementedError


# ---------------------------------------------------------------------------------------------------------------
# Real implementation
# ---------------------------------------------------------------------------------------------------------------


class SirilCommandOps(SirilOps):
    """Drives a connected sirilpy ``SirilInterface``.

    ``siril`` is the connected interface; ``log`` receives one line per command, ``"siril: <text> (<s> s)"``.
    """

    def __init__(self, siril: Any, log: Optional[Callable[[str], Any]] = None,
                 spcc_database: Optional[Any] = None) -> None:
        self._siril = siril
        self._log_to = log if log is not None else (lambda text: None)
        self._thread = threading.get_ident()
        self._locked = False
        self._spcc_database = None if spcc_database is None else os.fspath(spcc_database)

    def _spcc_folder(self) -> Optional[str]:
        """Siril's SPCC database folder (the override, else the first candidate that exists), or None."""
        if self._spcc_database is not None:
            return self._spcc_database if os.path.isdir(self._spcc_database) else None
        candidates = spcc_database_candidates(sys.platform, os.environ, os.path.expanduser("~"),
                                              siril_data_dir(self._siril))
        return next((c for c in candidates if os.path.isdir(c)), None)

    def _require_spcc(self, text: str, checks: Sequence[tuple[str, str, str]]) -> None:
        """Refuse an SPCC command before sending it: Siril 1.4.4 segfaults on a missing database or unknown names.

        ``checks`` are (database kind, name, label) triples; the database folder itself is always required.
        """
        folder = self._spcc_folder()
        if folder is None:
            raise SirilOpsError(text, RuntimeError(
                "the SPCC database is not installed (no siril-spcc-database folder found)"))
        for kind, name, label in checks:
            known = spcc_names(kind, folder)
            if name not in known:
                raise SirilOpsError(text, ValueError(
                    f"{label} {name!r} is not in Siril's SPCC database ({len(known)} {kind} names known)"))

    def _check_thread(self) -> None:
        if threading.get_ident() != self._thread:
            raise RuntimeError(_PIPELINE_THREAD_MESSAGE)

    def _send(self, text: str) -> None:
        _check_line(text)
        if self._locked:
            raise RuntimeError("a Siril command is never sent while the image lock is held")
        start = time.perf_counter()
        try:
            if self._siril.cmd(text) is False:
                raise RuntimeError("Siril reported that the command failed")
        except Exception as exc:
            raise SirilOpsError(text, exc) from exc
        finally:
            self._log_to(f"siril: {text} ({time.perf_counter() - start:.3f} s)")

    @_pipeline_only
    def cd(self, path: Any) -> None:
        self._send(_text_cd(path))

    @_pipeline_only
    def wd(self) -> str:
        return str(self._siril.get_siril_wd())

    @_pipeline_only
    def load(self, path: Any) -> None:
        self._send(_text_load(path))

    @_pipeline_only
    def save(self, path: Any) -> str:
        self._send(_text_save(path))
        return os.fspath(path)

    @_pipeline_only
    def set32bits(self) -> None:
        self._send("set32bits")

    @_pipeline_only
    def fits_compression(self) -> Optional[bool]:
        try:
            value = self._siril.get_siril_config("compression", "enabled")
        except Exception:  # an older Siril without get_siril_config: assume the default (off)
            return None
        return value if isinstance(value, bool) else None

    @_pipeline_only
    def set_fits_compression(self, on: bool) -> None:
        self._send(_text_set_compression(on))

    @_pipeline_only
    def platesolve(self, force: bool = False, focal: Optional[float] = None,
                   pixel_size: Optional[float] = None) -> None:
        self._send(_text_platesolve(force, focal, pixel_size))

    @_pipeline_only
    def spcc_broadband(self, sensor: str, r: str, g: str, b: str) -> None:
        text = _text_spcc_broadband(sensor, r, g, b)
        self._require_spcc(text, _spcc_checks(sensor, r, g, b))
        self._send(text)

    @_pipeline_only
    def spcc_narrowband(self, rwl: float, rbw: float, gwl: float, gbw: float, bwl: float, bbw: float,
                        sensor: Optional[str] = None, r: Optional[str] = None, g: Optional[str] = None,
                        b: Optional[str] = None) -> None:
        """Narrowband SPCC. After a broadband SPCC in the same Siril session, the command without the names fails
        with "Argument error" (measured on Siril 1.4.4); with all four names (sensor, R, G, B filters) it succeeds in
        either order. The names are checked against the database like :meth:`spcc_broadband` checks them.
        """
        names = _spcc_session_names(sensor, r, g, b)
        text = _text_spcc_narrowband(rwl, rbw, gwl, gbw, bwl, bbw, names)
        self._require_spcc(text, [] if names is None else _spcc_checks(sensor, r, g, b))
        self._send(text)

    @_pipeline_only
    def link_sequence(self, name: str) -> None:
        self._send(_text_link(name, self._siril.get_siril_wd()))

    @_pipeline_only
    def setref(self, name: str, index1: int) -> None:
        self._send(_text_setref(name, index1))

    @_pipeline_only
    def register(self, name: str) -> None:
        self._send(_text_register(name))

    @_pipeline_only
    def crop(self, x: int, y: int, w: int, h: int) -> None:
        self._send(_text_crop(x, y, w, h))

    @_pipeline_only
    def rgbcomp(self, r: Any, g: Any, b: Any, out: Any) -> str:
        self._send(_text_rgbcomp(r, g, b, out))
        return f"{os.fspath(out)}.fit"

    @_pipeline_only
    def ghs(self, cmd_tuple: Sequence[Any]) -> None:
        self._send(_text_ghs(cmd_tuple))

    @_pipeline_only
    def linear_match(self, ref: Any, low: float = 0, high: float = 0.92) -> None:
        self._send(_text_linear_match(ref, low, high))

    @_pipeline_only
    def mtf(self, c0: float, m: float, channel: Any = None) -> None:
        self._send(_text_mtf(c0, m, channel))

    @_pipeline_only
    def find_stars(self) -> list[dict[str, Any]]:
        for text in _FIND_STARS_TEXTS:
            self._send(text)
        stars = self._siril.get_image_stars()
        return [_star_dict(s) for s in (stars or [])]

    @_pipeline_only
    def analyse(self, path: Any) -> dict[str, Any]:
        text = os.fspath(path)
        _quoted(text)  # refuses a double quote, as every path does
        return _as_dict(self._siril.analyse_image_from_file(text))

    @_pipeline_only
    def icc_assign(self, profile: Any) -> None:
        self._send(_text_icc_assign(profile))

    @_pipeline_only
    def icc_profile(self) -> Optional[bytes]:
        return self._siril.get_image_iccprofile()

    @_pipeline_only
    def update_key(self, key: str, value: str, comment: Optional[str] = None) -> None:
        self._send(_text_update_key(key, value, comment))

    @_pipeline_only
    def denoise(self, mod: float) -> None:
        self._send(_text_denoise(mod))

    @_pipeline_only
    def subsky(self, degree: int = 2, samples: int = 20, tolerance: float = 1.0) -> None:
        self._send(_text_subsky(degree, samples, tolerance))

    @_pipeline_only
    def pyscript(self, path: Any, args: Sequence[Any] = ()) -> None:
        self._send(_text_pyscript(path, args))

    @_pipeline_only
    def log(self, text: str) -> None:
        self._siril.log(text)

    @_pipeline_only
    def progress(self, text: str, fraction: float) -> None:
        self._siril.update_progress(text, float(fraction))

    @_pipeline_only
    def pixels(self) -> list[np.ndarray]:
        return planes_from_pixeldata(self._siril.get_image_pixeldata())

    @_pipeline_only
    def set_pixels(self, planes: Sequence[Any]) -> None:
        arr = _pixeldata_from_planes(planes)
        self._locked = True
        try:
            with self._siril.image_lock():
                self._siril.set_image_pixeldata(arr)
        finally:
            self._locked = False

    @_pipeline_only
    def header(self) -> str:
        text = self._siril.get_image_fits_header()
        return text if isinstance(text, str) else ""

    @_pipeline_only
    def is_cli(self) -> bool:
        return bool(self._siril.is_cli())


# ---------------------------------------------------------------------------------------------------------------
# Fake for tests
# ---------------------------------------------------------------------------------------------------------------


class RecordingOps(SirilOps):
    """A fake: records the exact command texts and the calls, serves canned results, keeps the image in memory.

    * ``transcript``: the command texts in order (``'load "/x/a.fits"'``, ``'mtf 0.01 0.2 1'``, ...). Methods that
      send no command (analyse, pixels, log, ...) add nothing.
    * ``calls``: ``(method name, parameters, {})`` per call, where the parameters are all of the method's parameters
      in declaration order, defaults included.
    * ``load`` and ``save`` read and write FITS with ``loomsiril.io_fits``; ``crop`` and ``mtf`` act on the in-memory
      image as Siril does (crop with y from the top; mtf with ``(m-1)x / ((2m-1)x - m)`` on
      ``clip((x-c0)/(1-c0), 0, 1)``). Every other command only records, unless ``handlers[name]`` is given: it is
      called as ``handlers[name](ops, *parameters)`` and its result is returned.
    * ``fail``: ``{method name: exception}`` makes that method raise :class:`SirilOpsError` (after recording it).
    * ``stars``: a list of star dicts, or a callable returning one; ``analyse``: a dict, or a callable of the path;
      ``icc``: the bytes ``icc_profile`` returns.
    * ``cd`` changes ``workdir``; relative paths resolve against it. ``is_cli`` is True.
    """

    def __init__(self, workdir: Any, handlers: Optional[Mapping[str, Callable[..., Any]]] = None,
                 stars: Any = None, analyse: Any = None, icc: Optional[bytes] = None,
                 fail: Optional[Mapping[str, BaseException]] = None) -> None:
        self.workdir = os.fspath(workdir)
        self.compression: Optional[bool] = False   # Siril's FITS compression preference (fits_compression)
        self.handlers: dict[str, Callable[..., Any]] = dict(handlers or {})
        self.transcript: list[str] = []
        self.calls: list[tuple[str, tuple, dict]] = []
        self.logs: list[str] = []
        self.progress_events: list[tuple[str, float]] = []
        self._stars = stars
        self._analyse = analyse
        self._icc = icc
        self._fail: dict[str, BaseException] = dict(fail or {})
        self._planes: Optional[list[np.ndarray]] = None
        self._header: Optional[dict[str, Any]] = None

    # -- plumbing ----------------------------------------------------------------------------------------------

    def _resolve(self, path: Any) -> str:
        return os.path.normpath(os.path.join(self.workdir, os.fspath(path)))

    def _do(self, name: str, args: tuple, texts: list[str], default: Callable[[str], Any]) -> Any:
        self.calls.append((name, tuple(args), {}))
        self.transcript.extend(texts)
        text = texts[-1] if texts else name
        if name in self._fail:
            raise SirilOpsError(text, self._fail[name])
        if name in self.handlers:
            return self.handlers[name](self, *args)
        return default(text)

    def _image(self, text: str) -> tuple[list[np.ndarray], dict[str, Any]]:
        if self._planes is None or self._header is None:
            raise SirilOpsError(text, RuntimeError("no image is loaded"))
        return self._planes, self._header

    # -- commands ----------------------------------------------------------------------------------------------

    def cd(self, path: Any) -> None:
        def run(text: str) -> None:
            self.workdir = self._resolve(path)

        return self._do("cd", (path,), [_text_cd(path)], run)

    def wd(self) -> str:
        return self._do("wd", (), [], lambda text: self.workdir)

    def load(self, path: Any) -> None:
        def run(text: str) -> None:
            try:
                self._planes, self._header = read_fits(self._resolve(path))
            except (OSError, ValueError) as exc:
                raise SirilOpsError(text, exc) from exc

        return self._do("load", (path,), [_text_load(path)], run)

    def save(self, path: Any) -> str:
        def run(text: str) -> str:
            planes, header = self._image(text)
            write_fits(self._resolve(path), planes, header)
            return os.fspath(path)

        return self._do("save", (path,), [_text_save(path)], run)

    def set32bits(self) -> None:
        return self._do("set32bits", (), ["set32bits"], lambda text: None)

    def fits_compression(self) -> Optional[bool]:
        return self._do("fits_compression", (), [], lambda text: self.compression)

    def set_fits_compression(self, on: bool) -> None:
        def run(text: str) -> None:
            self.compression = bool(on)

        return self._do("set_fits_compression", (on,), [_text_set_compression(on)], run)

    def platesolve(self, force: bool = False, focal: Optional[float] = None,
                   pixel_size: Optional[float] = None) -> None:
        return self._do("platesolve", (force, focal, pixel_size), [_text_platesolve(force, focal, pixel_size)],
                 lambda text: None)

    def spcc_broadband(self, sensor: str, r: str, g: str, b: str) -> None:
        return self._do("spcc_broadband", (sensor, r, g, b), [_text_spcc_broadband(sensor, r, g, b)], lambda text: None)

    def spcc_narrowband(self, rwl: float, rbw: float, gwl: float, gbw: float, bwl: float, bbw: float,
                        sensor: Optional[str] = None, r: Optional[str] = None, g: Optional[str] = None,
                        b: Optional[str] = None) -> None:
        names = _spcc_session_names(sensor, r, g, b)
        text = _text_spcc_narrowband(rwl, rbw, gwl, gbw, bwl, bbw, names)
        return self._do("spcc_narrowband", (rwl, rbw, gwl, gbw, bwl, bbw, sensor, r, g, b), [text],
                        lambda text: None)

    def link_sequence(self, name: str) -> None:
        return self._do("link_sequence", (name,), [_text_link(name, self.workdir)], lambda text: None)

    def setref(self, name: str, index1: int) -> None:
        return self._do("setref", (name, index1), [_text_setref(name, index1)], lambda text: None)

    def register(self, name: str) -> None:
        return self._do("register", (name,), [_text_register(name)], lambda text: None)

    def crop(self, x: int, y: int, w: int, h: int) -> None:
        text_crop = _text_crop(x, y, w, h)

        def run(text: str) -> None:
            planes, header = self._image(text)
            height, width = planes[0].shape
            xi, yi, wi, hi = _whole(x, "x"), _whole(y, "y"), _whole(w, "w"), _whole(h, "h")
            if xi < 0 or yi < 0 or wi < 1 or hi < 1 or xi + wi > width or yi + hi > height:
                raise SirilOpsError(text, ValueError("crop rectangle outside the image"))
            self._planes = [np.ascontiguousarray(p[yi:yi + hi, xi:xi + wi]) for p in planes]
            if "CRPIX1" in header:
                header["CRPIX1"] = float(header["CRPIX1"]) - xi
            if "CRPIX2" in header:
                header["CRPIX2"] = float(header["CRPIX2"]) - yi

        return self._do("crop", (x, y, w, h), [text_crop], run)

    def rgbcomp(self, r: Any, g: Any, b: Any, out: Any) -> str:
        return self._do("rgbcomp", (r, g, b, out), [_text_rgbcomp(r, g, b, out)],
                        lambda text: f"{os.fspath(out)}.fit")

    def ghs(self, cmd_tuple: Sequence[Any]) -> None:
        return self._do("ghs", (cmd_tuple,), [_text_ghs(cmd_tuple)], lambda text: None)

    def linear_match(self, ref: Any, low: float = 0, high: float = 0.92) -> None:
        return self._do("linear_match", (ref, low, high), [_text_linear_match(ref, low, high)], lambda text: None)

    def mtf(self, c0: float, m: float, channel: Any = None) -> None:
        text_mtf = _text_mtf(c0, m, channel)

        def run(text: str) -> None:
            planes, _ = self._image(text)
            if channel is None:
                targets = list(range(len(planes)))
            else:
                index = _CHANNEL_LETTERS.index(_channel_letter(channel))
                if index >= len(planes):
                    raise SirilOpsError(text, ValueError(f"the image has no {_channel_letter(channel)} channel"))
                targets = [index]
            c0f, mf = float(c0), float(m)
            for i in targets:
                x = np.clip((planes[i].astype(np.float64) - c0f) / (1.0 - c0f), 0.0, 1.0)
                with np.errstate(divide="ignore", invalid="ignore"):
                    planes[i] = ((mf - 1.0) * x / ((2.0 * mf - 1.0) * x - mf)).astype(np.float32)

        return self._do("mtf", (c0, m, channel), [text_mtf], run)

    def find_stars(self) -> list[dict[str, Any]]:
        def run(text: str) -> list[dict[str, Any]]:
            stars = self._stars() if callable(self._stars) else self._stars
            return [_star_dict(s) for s in (stars or [])]

        return self._do("find_stars", (), list(_FIND_STARS_TEXTS), run)

    def analyse(self, path: Any) -> dict[str, Any]:
        def run(text: str) -> dict[str, Any]:
            result = self._analyse(os.fspath(path)) if callable(self._analyse) else self._analyse
            return dict(result or {})

        return self._do("analyse", (path,), [], run)

    def icc_assign(self, profile: Any) -> None:
        return self._do("icc_assign", (profile,), [_text_icc_assign(profile)], lambda text: None)

    def icc_profile(self) -> Optional[bytes]:
        return self._do("icc_profile", (), [], lambda text: self._icc)

    def update_key(self, key: str, value: str, comment: Optional[str] = None) -> None:
        def run(text: str) -> None:
            _, header = self._image(text)
            header[str(key).upper()] = str(value)

        return self._do("update_key", (key, value, comment), [_text_update_key(key, value, comment)], run)

    def denoise(self, mod: float) -> None:
        return self._do("denoise", (mod,), [_text_denoise(mod)], lambda text: None)

    def subsky(self, degree: int = 2, samples: int = 20, tolerance: float = 1.0) -> None:
        return self._do("subsky", (degree, samples, tolerance), [_text_subsky(degree, samples, tolerance)],
                 lambda text: None)

    def pyscript(self, path: Any, args: Sequence[Any] = ()) -> None:
        return self._do("pyscript", (path, tuple(args)), [_text_pyscript(path, args)], lambda text: None)

    # -- no command ----------------------------------------------------------------------------------------------

    def log(self, text: str) -> None:
        return self._do("log", (text,), [], lambda _t: self.logs.append(text))

    def progress(self, text: str, fraction: float) -> None:
        return self._do("progress", (text, fraction), [],
                 lambda _t: self.progress_events.append((text, float(fraction))))

    def pixels(self) -> list[np.ndarray]:
        def run(text: str) -> list[np.ndarray]:
            planes, _ = self._image(text)
            return [p.copy() for p in planes]

        return self._do("pixels", (), [], run)

    def set_pixels(self, planes: Sequence[Any]) -> None:
        def run(text: str) -> None:
            current, _ = self._image(text)
            new = _planes_top_down(planes)
            if [p.shape for p in new] != [p.shape for p in current]:
                raise ValueError("set_pixels must keep the image's shape and plane count")
            self._planes = new

        return self._do("set_pixels", (planes,), [], run)

    def header(self) -> str:
        def run(text: str) -> str:
            _, header = self._image(text)
            return "\n".join(f"{k:<8}= {v!r}" for k, v in header.items())

        return self._do("header", (), [], run)

    def is_cli(self) -> bool:
        return self._do("is_cli", (), [], lambda text: True)


# ---------------------------------------------------------------------------------------------------------------
# SPCC database helpers (pure file reads; no Siril needed)
# ---------------------------------------------------------------------------------------------------------------


def spcc_names(kind: str, database_dir: Any) -> list[str]:
    """Names in Siril's SPCC database folder for one kind, sorted and unique.

    The database is a tree of JSON files (Siril's layout: mono_sensors/, mono_filters/, osc_sensors/, ... plus a
    schema file and some OSC filter files at the top). Each file that is a JSON array of objects with ``name``,
    ``type`` and optionally ``channel`` contributes its entries; every other file (not JSON, or another shape) is
    skipped. ``kind`` "monosensor" selects type MONO_SENSOR; "redfilter", "greenfilter" and "bluefilter" select type
    MONO_FILTER with channel RED, GREEN and BLUE. A missing folder gives an empty list.
    """
    if kind not in _SPCC_KINDS:
        raise ValueError(f"unknown SPCC kind {kind!r}; expected one of {sorted(_SPCC_KINDS)}")
    wanted_type, wanted_channel = _SPCC_KINDS[kind]
    folder = Path(database_dir)
    if not folder.is_dir():
        return []
    names: set[str] = set()
    for path in sorted(folder.rglob("*.json")):
        if not path.is_file():
            continue
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping) or entry.get("type") != wanted_type:
                continue
            if wanted_channel is not None and entry.get("channel") != wanted_channel:
                continue
            name = entry.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return sorted(names)


def siril_data_dir(siril: Any) -> Optional[str]:
    """The folder that holds Siril's data folders, asked from Siril itself; None when Siril cannot say.

    Siril 1.4 builds its user data folder, its scripts repository and its SPCC database from the same base
    (``g_get_user_data_dir()``): ``<base>/siril``, ``<base>/siril-scripts`` and ``<base>/siril-spcc-database``.
    ``get_siril_userdatadir()`` gives ``<base>/siril``, so its parent is the base, on every OS (on macOS it is
    under ``~/Library/Application Support/org.siril.Siril``, not ``~/Library/Application Support``).
    """
    try:
        user = siril.get_siril_userdatadir()
    except Exception:  # an older Siril, or no connection: the per-OS guesses still apply
        return None
    if not isinstance(user, str) or not user.strip():
        return None
    return os.path.dirname(os.path.normpath(user.strip()))


def _text_set_compression(on: bool) -> str:
    return "set compression.enabled=" + ("true" if on else "false")


def spcc_database_candidates(platform: str, env: Mapping[str, str], home: str,
                             data_dir: Optional[str] = None) -> list[str]:
    """Where Siril keeps its SPCC database folder on ``platform`` (``sys.platform`` style names).

    ``data_dir`` (from :func:`siril_data_dir`) comes first when given: it is where the running Siril keeps it.
    Then the per-OS guesses. Linux: ``$XDG_DATA_HOME`` (when absolute) or ``~/.local/share``, then
    ``siril-spcc-database``; measured on Siril 1.4.4. macOS (``~/Library/Application Support``) and Windows
    (``%LOCALAPPDATA%``) are guesses. An unknown platform gives only the ``data_dir`` candidate.
    """
    first = [os.path.join(data_dir, _SPCC_FOLDER)] if data_dir else []
    return first + [c for c in _spcc_guesses(platform, env, home) if c not in first]


def _spcc_guesses(platform: str, env: Mapping[str, str], home: str) -> list[str]:
    name = _SPCC_FOLDER
    key = str(platform).lower()
    if key.startswith("linux"):
        xdg = env.get("XDG_DATA_HOME", "")
        base = xdg if xdg and posixpath.isabs(xdg) else posixpath.join(home, ".local", "share")
        return [posixpath.join(base, name)]
    if key.startswith("darwin") or key == "macos":
        return [posixpath.join(home, "Library", "Application Support", name)]
    if key.startswith("win"):
        local = env.get("LOCALAPPDATA") or ntpath.join(home, "AppData", "Local")
        return [ntpath.join(local, name)]
    return []
