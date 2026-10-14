"""Stand-in for sirilpy.connection.SirilInterface: the same method names and signatures, state from ``STATE``.

``STATE`` is what a test sets: ``loaded``, ``pixeldata`` (as Siril hands it: bottom-up, uint16 / float32, (h, w) or
(c, h, w)), ``header`` (FITS keywords as a dict, values int/float/str), ``stars`` (PSFStar list or None), ``icc``,
``filename``, ``configdir``. The stub records ``messages``, ``progress``, ``logs``, ``commands`` and ``connects``.

Conventions, as measured with siril-cli 1.4.4: PSFStar.xpos = column + 0.5 and
PSFStar.ypos = distance from the TOP of the display + 0.5 (pixel-edge convention); pix2radec/radec2pix take the same
edge-convention pixel with y from the top. The header Siril returns has no CD keys: a CD matrix comes back as
CDELT1/2 + PC1_1..PC2_2 (CD = CDELT_i * PC_ij), and the stub does the same. ``connect()`` raises
SirilConnectionError on failure, as the real one does.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, Union

import numpy as np

from .enums import LogColor
from .exceptions import NoImageError, SirilConnectionError, SirilError
from .models import PSFStar

__all__ = ["STATE", "PSFStar", "SirilInterface", "LogColor"]


@dataclass
class _State:
    """Module-level state the tests set and read (see the package docstring)."""

    loaded: bool = False
    pixeldata: Any = None
    header: Optional[dict] = None
    stars: Optional[list] = None
    icc: Optional[bytes] = None
    filename: Optional[str] = None
    configdir: str = "/nonexistent-siril-config"
    userdatadir: str = "/nonexistent-siril-data/siril"
    connects: int = 0
    disconnects: int = 0
    connect_ok: bool = True
    messages: List[Tuple[str, str]] = field(default_factory=list)
    progress: List[Tuple[str, float]] = field(default_factory=list)
    logs: List[Tuple[str, int]] = field(default_factory=list)
    commands: List[Tuple[str, ...]] = field(default_factory=list)
    installs: List[Any] = field(default_factory=list)
    workdir: str = "/nonexistent-siril-wd"
    cli: bool = True
    analysis: Optional[dict] = None
    analysed: List[str] = field(default_factory=list)
    lock_depth: int = 0
    locks: int = 0
    cmds_under_lock: List[str] = field(default_factory=list)

    def reset(self) -> None:
        """Back to a fresh stub (tests call this in a fixture)."""
        self.__init__()  # type: ignore[misc]


STATE = _State()


def _header_string(header: dict) -> str:
    """A FITS-like text header (one card per line), as get_image_fits_header(return_as='str') gives."""
    return "\n".join(f"{k:<8}= {v!r}" for k, v in header.items())


class SirilInterface:
    """Mirror of sirilpy.SirilInterface (the methods the bridge and script use, with the real signatures)."""

    def __init__(self) -> None:
        self._connected = False

    def connect(self) -> bool:
        """Open the (stub) connection: True, or SirilConnectionError when the stub is set to fail."""
        STATE.connects += 1
        if not STATE.connect_ok:
            raise SirilConnectionError("cannot connect to Siril (stub)")
        self._connected = True
        return True

    def disconnect(self) -> None:
        """Close the (stub) connection."""
        STATE.disconnects += 1
        self._connected = False

    def log(self, my_string: str, color: LogColor = LogColor.DEFAULT) -> bool:
        """Record a log line."""
        STATE.logs.append((my_string, int(color)))
        return True

    def _messagebox(self, kind: str, my_string: str, modal: Optional[bool] = False) -> bool:
        STATE.messages.append((kind, my_string))
        return True

    def info_messagebox(self, my_string: str, modal: Optional[bool] = False) -> bool:
        """Record an information message box."""
        return self._messagebox("info", my_string, modal)

    def warning_messagebox(self, my_string: str, modal: Optional[bool] = False) -> bool:
        """Record a warning message box."""
        return self._messagebox("warning", my_string, modal)

    def error_messagebox(self, my_string: str, modal: Optional[bool] = False) -> bool:
        """Record an error message box."""
        return self._messagebox("error", my_string, modal)

    def confirm_messagebox(self, title: str, message: str, confirm_label: str) -> bool:
        """Record a confirmation box; the stub always answers yes."""
        STATE.messages.append(("confirm", f"{title}: {message}"))
        return True

    def update_progress(self, message: str, progress: float) -> bool:
        """Record a progress update (progress in [0, 1])."""
        STATE.progress.append((message, float(progress)))
        return True

    def cmd(self, *args: str) -> bool:
        """Record a Siril command (the stub runs nothing)."""
        STATE.commands.append(tuple(str(a) for a in args))
        if STATE.lock_depth:
            STATE.cmds_under_lock.append(" ".join(str(a) for a in args))
        return True

    @contextmanager
    def image_lock(self):
        """Hold the image lock for the block (the stub records the depth; cmd() notes sends made under it)."""
        STATE.lock_depth += 1
        STATE.locks += 1
        try:
            yield
        finally:
            STATE.lock_depth -= 1

    def is_cli(self) -> bool:
        """True when the stub stands for siril-cli (``STATE.cli``)."""
        return bool(STATE.cli)

    def get_siril_wd(self) -> str:
        """Siril's working directory (the stub's ``STATE.workdir``; a ``cd`` is not interpreted)."""
        return STATE.workdir

    def set_image_pixeldata(self, arr: np.ndarray) -> bool:
        """Replace the loaded image's pixels (bottom-up, as Siril takes them)."""
        self._require_image()
        STATE.pixeldata = np.array(arr, copy=True)
        return True

    def analyse_image_from_file(self, path: str) -> dict:
        """Statistics of an image file: the dict ``STATE.analysis`` (``SirilError`` when none is set)."""
        STATE.analysed.append(path)
        if STATE.analysis is None:
            raise SirilError("no analysis set in the stub")
        return dict(STATE.analysis)

    def is_image_loaded(self) -> bool:
        """True when a single image is loaded."""
        return bool(STATE.loaded)

    def get_image_filename(self) -> Optional[str]:
        """The loaded image's file name, or None."""
        self._require_image()
        return STATE.filename

    def get_image_pixeldata(self, shape: Optional[list] = None, preview: Optional[bool] = False,
                            linked: Optional[bool] = False) -> Optional[np.ndarray]:
        """The loaded image's pixels: (h, w) mono or (c, h, w) colour, uint16 or float32, bottom-up (FITS order)."""
        self._require_image()
        if preview:
            raise SirilError("the stub has no autostretched preview")
        if shape is not None:
            raise SirilError("the stub does not support regions")
        return np.array(STATE.pixeldata, copy=True)

    def get_image_iccprofile(self) -> Optional[bytes]:
        """The loaded image's ICC profile bytes, or None."""
        self._require_image()
        return STATE.icc

    def get_image_fits_header(self, return_as: str = "str") -> Union[str, dict, None]:
        """The loaded image's FITS header: a dict (return_as='dict') or card text (return_as='str')."""
        self._require_image()
        if STATE.header is None:
            return None
        header = _siril_form(STATE.header)
        if return_as == "dict":
            return dict(header)
        if return_as == "str":
            return _header_string(header)
        raise ValueError("return_as must be 'str' or 'dict'")

    def get_image_stars(self, channel: Optional[int] = None) -> List[PSFStar]:
        """The loaded image's PSF stars, or None when there are none (channel 0 for mono, 1 for colour by default)."""
        self._require_image()
        if channel is not None and (channel < 0 or channel > 2):
            raise ValueError("Channel must be 0 (Red/Mono), 1 (Green), or 2 (Blue)")
        return STATE.stars

    def get_siril_configdir(self) -> str:
        """Siril's user config directory (the stub's ``STATE.configdir``)."""
        return STATE.configdir

    def get_siril_userdatadir(self) -> str:
        """Siril's user data directory, ``<base>/siril`` (the stub's ``STATE.userdatadir``)."""
        return STATE.userdatadir

    def pix2radec(self, x: float, y: float) -> Optional[Tuple[float, float]]:
        """Pixel to (RA, Dec) in degrees with the header's TAN keywords.

        Siril's pixel-edge convention (verified with siril-cli 1.4.4): the centre of column i is x = i + 0.5, x is
        measured from the left, and y is measured from the TOP of the display (row r of the bottom-up array is
        y = H - r - 0.5). This is not FITS 1-based coordinates.
        """
        self._require_wcs()
        w = _tan(STATE.header)
        h = self._height()
        # edge convention: x - 0.5 is the 0-based column; (H - y) - 0.5 is the 0-based bottom-up row
        xi, eta = _tan_inverse_pixel(float(x) - 0.5, (h - float(y)) - 0.5, w)
        return _tan_to_sky(xi, eta, w)

    def radec2pix(self, ra: float, dec: float) -> Optional[Tuple[float, float]]:
        """(RA, Dec) in degrees to pixel with the header's TAN keywords, in the convention of pix2radec.

        Returns Siril's pixel-edge coordinates: x = i + 0.5 for column i (from the left), y measured from the
        top of the display (see pix2radec). Not FITS 1-based coordinates.
        """
        self._require_wcs()
        w = _tan(STATE.header)
        h = self._height()
        x0, y0 = _tan_forward(float(ra), float(dec), w)           # 0-based centres, bottom-up rows
        x_edge, y_edge = x0 + 0.5, y0 + 0.5                       # pixel-edge coordinates, y bottom-up
        return x_edge, h - y_edge                                 # y from the top

    def _require_image(self) -> None:
        if not STATE.loaded:
            raise NoImageError("no image is currently loaded in Siril")

    def _height(self) -> int:
        return int(np.shape(STATE.pixeldata)[-2])

    def _require_wcs(self) -> None:
        self._require_image()
        if not STATE.header or "CRVAL1" not in STATE.header:
            raise ValueError("the image is not plate solved")


def _siril_form(header: dict) -> dict:
    """The header as Siril hands it out: a CD matrix becomes CDELT1/2 + PC1_1..PC2_2 (CDELT1 < 0 < CDELT2)."""
    if not header or "CD1_1" not in header:
        return dict(header or {})
    cd = [float(header[k]) for k in ("CD1_1", "CD1_2", "CD2_1", "CD2_2")]
    cdelt1 = -math.hypot(cd[0], cd[2])
    cdelt2 = math.hypot(cd[1], cd[3])
    out = {k: v for k, v in header.items() if not k.startswith("CD")}
    out.update({"CDELT1": cdelt1, "CDELT2": cdelt2, "PC1_1": cd[0] / cdelt1, "PC1_2": cd[1] / cdelt1,
                "PC2_1": cd[2] / cdelt2, "PC2_2": cd[3] / cdelt2})
    return out


def _tan(header: dict) -> dict:
    """The TAN keywords as a plain dict of floats (CRPIX 1-based, CD as in the header)."""
    h = {k: float(header[k]) for k in ("CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2", "CD1_1", "CD1_2", "CD2_1", "CD2_2")}
    return h


def _tan_inverse_pixel(x0: float, y0: float, h: dict) -> Tuple[float, float]:
    """Standard-coordinate (xi, eta) in degrees for a 0-based pixel (x0, y0) of the stub's rows."""
    dx, dy = x0 + 1.0 - h["CRPIX1"], y0 + 1.0 - h["CRPIX2"]
    xi = h["CD1_1"] * dx + h["CD1_2"] * dy
    eta = h["CD2_1"] * dx + h["CD2_2"] * dy
    return xi, eta


def _tan_to_sky(xi: float, eta: float, h: dict) -> Tuple[float, float]:
    a0, d0 = math.radians(h["CRVAL1"]), math.radians(h["CRVAL2"])
    xr, er = math.radians(xi), math.radians(eta)
    rho = math.hypot(xr, er)
    if rho == 0:
        return h["CRVAL1"], h["CRVAL2"]
    c = math.atan(rho)
    dec = math.asin(math.cos(c) * math.sin(d0) + er * math.sin(c) * math.cos(d0) / rho)
    ra = a0 + math.atan2(xr * math.sin(c), rho * math.cos(d0) * math.cos(c) - er * math.sin(d0) * math.sin(c))
    return math.degrees(ra) % 360.0, math.degrees(dec)


def _tan_forward(ra: float, dec: float, h: dict) -> Tuple[float, float]:
    a, d = math.radians(ra), math.radians(dec)
    a0, d0 = math.radians(h["CRVAL1"]), math.radians(h["CRVAL2"])
    cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    xi = math.degrees(math.cos(d) * math.sin(a - a0) / cosc)
    eta = math.degrees((math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cosc)
    det = h["CD1_1"] * h["CD2_2"] - h["CD1_2"] * h["CD2_1"]
    dx = (h["CD2_2"] * xi - h["CD1_2"] * eta) / det
    dy = (-h["CD2_1"] * xi + h["CD1_1"] * eta) / det
    return h["CRPIX1"] - 1.0 + dx, h["CRPIX2"] - 1.0 + dy
