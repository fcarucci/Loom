"""Seeded synthetic masters for the Loom pipeline tests (no real frame is ever used).

One fixed sky (a seeded star catalogue around RA 83.82, Dec -5.39) is rendered into each master through its own TAN
WCS, so masters of different channels, shifts, rotations and drizzle scales show the same stars and register onto
each other. ``make_master`` returns the planes (top-down float32, as ``loomsiril.io_fits`` holds them), the header
(top-down TAN keywords plus FILTER, INSTRUME, XPIXSZ, FOCALLEN, RA, DEC, IMAGETYP) and the true star positions;
``write_master`` writes it as a 32-bit float FITS file.

Conventions (as ``loomsiril.io_fits``): pixel (x, y) is 0-based with y counted from the top; FITS pixel
coordinates are ``(x + 1, y + 1)`` in that top-down frame; ``write_fits`` turns the header into FITS row order.

Everything is deterministic for given arguments. The TAN projection here is written independently of the package
code on purpose, so tests that use it check the port rather than agree with it.

StarNet2 refuses frames smaller than its model window: masters for StarNet tests must be at least 512 px on each
side (the default 640 x 512 is).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from loomsiril.io_fits import write_fits

RAD = math.pi / 180

#: Sky centre and the native pixel size the drizzle factor is measured against (Loom: 3.76 um).
RA0, DEC0 = 83.82, -5.39
NATIVE_PIXEL_UM = 3.76
#: FITS FILTER value written for each channel slot.
FILTER_NAMES = {"L": "L", "R": "R", "G": "G", "B": "B", "H": "Ha", "S": "SII", "O": "OIII"}
#: Per-channel sky level so the channels are not identical (linear, 0..1).
SKY_LEVELS = {"L": 0.05, "R": 0.04, "G": 0.045, "B": 0.048, "H": 0.03, "S": 0.02, "O": 0.025}


@dataclass
class Master:
    """A synthetic master: ``planes`` top-down float32, FITS ``header`` (top-down TAN), true star positions."""

    channel: str
    planes: list[np.ndarray]
    header: dict[str, Any]
    #: (n, 3): x, y (0-based, top-down) and peak of each star inside the frame
    stars: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))

    @property
    def shape(self) -> tuple[int, int]:
        return self.planes[0].shape  # type: ignore[return-value]


def sky_catalogue(n_stars: int = 260, seed: int = 42, radius_deg: float = 0.45) -> np.ndarray:
    """The fixed sky: (n, 3) array of ra, dec (degrees) and peak (0.05..0.8), seeded."""
    rng = np.random.default_rng(seed)
    ra = RA0 + rng.uniform(-radius_deg, radius_deg, n_stars) / math.cos(DEC0 * RAD)
    dec = DEC0 + rng.uniform(-radius_deg * 0.8, radius_deg * 0.8, n_stars)
    peak = rng.uniform(0.05, 0.8, n_stars)
    return np.column_stack([ra, dec, peak])


def standard_coords(ra: np.ndarray, dec: np.ndarray, ra0: float = RA0, dec0: float = DEC0) -> tuple[np.ndarray, np.ndarray]:
    """Gnomonic (TAN) standard coordinates xi, eta in degrees of (ra, dec) about (ra0, dec0)."""
    ra_r, dec_r, ra0_r, dec0_r = ra * RAD, dec * RAD, ra0 * RAD, dec0 * RAD
    cosc = math.sin(dec0_r) * np.sin(dec_r) + math.cos(dec0_r) * np.cos(dec_r) * np.cos(ra_r - ra0_r)
    xi = np.cos(dec_r) * np.sin(ra_r - ra0_r) / cosc
    eta = (math.cos(dec0_r) * np.sin(dec_r) - math.sin(dec0_r) * np.cos(dec_r) * np.cos(ra_r - ra0_r)) / cosc
    return xi / RAD, eta / RAD


def tan_header(w: int, h: int, scale_arcsec: float, rotation_deg: float = 0.0,
               shift: tuple[float, float] = (0.0, 0.0)) -> dict[str, Any]:
    """Top-down TAN keywords: north up, east left, rotated by ``rotation_deg``, sky centre moved by ``shift`` px."""
    s = scale_arcsec / 3600.0
    r = rotation_deg * RAD
    # top-down rows: y grows downwards, so eta = -s * dy before rotation
    cd = np.array([[-s * math.cos(r), s * math.sin(r)], [-s * math.sin(r), -s * math.cos(r)]])
    return {
        "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": RA0, "CRVAL2": DEC0,
        "CRPIX1": w / 2 + 0.5 + shift[0], "CRPIX2": h / 2 + 0.5 + shift[1],
        "CD1_1": float(cd[0, 0]), "CD1_2": float(cd[0, 1]), "CD2_1": float(cd[1, 0]), "CD2_2": float(cd[1, 1]),
        "EQUINOX": 2000.0,
    }


def sky_to_pixel(header: Mapping[str, Any], ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    """0-based top-down pixel (x, y) of sky positions through a top-down TAN header: (n, 2)."""
    xi, eta = standard_coords(np.asarray(ra, float), np.asarray(dec, float), header["CRVAL1"], header["CRVAL2"])
    cd = np.array([[header["CD1_1"], header["CD1_2"]], [header["CD2_1"], header["CD2_2"]]])
    p = np.linalg.solve(cd, np.vstack([xi, eta])).T
    return p + np.array([header["CRPIX1"], header["CRPIX2"]]) - 1.0


def make_master(
    channel: str = "L",
    w: int = 640,
    h: int = 512,
    seed: int = 1,
    drizzle: int = 1,
    shift: tuple[float, float] = (0.0, 0.0),
    rotation_deg: float = 0.0,
    fwhm: float = 3.0,
    sky: Optional[float] = None,
    noise: float = 0.002,
    gradient: tuple[float, float, float] = (0.0, 0.0, 0.0),
    blob: Optional[tuple[float, float, float, float]] = None,
    scale_arcsec: float = 2.0,
    instrume: Optional[str] = "ZWO ASI2600MM Pro",
    filter_name: Optional[str] = None,
    with_wcs: bool = True,
    catalogue: Optional[np.ndarray] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Master:
    """Render one mono master of ``channel``.

    ``drizzle`` multiplies the size and the FWHM and divides the scale and XPIXSZ (a drizzled master of the same
    sky). ``shift`` (px, before drizzle) and ``rotation_deg`` move the frame on the sky. ``gradient`` = (gx, gy, q)
    adds ``gx*u + gy*v + q*(u^2 + v^2)`` with u, v in [-0.5, 0.5] across the frame; ``blob`` = (x, y, sigma, amp)
    adds a faint Gaussian "nebula" (px in the rendered frame). ``with_wcs=False`` leaves only the solve hints
    (RA, DEC, FOCALLEN, XPIXSZ). ``instrume=None`` or ``filter_name=""`` omit those keywords.
    """
    cat = sky_catalogue() if catalogue is None else catalogue
    d = int(drizzle)
    if d < 1:
        raise ValueError("drizzle must be >= 1")
    W, H = w * d, h * d
    hdr = tan_header(W, H, scale_arcsec / d, rotation_deg, (shift[0] * d, shift[1] * d))
    pts = sky_to_pixel(hdr, cat[:, 0], cat[:, 1])
    level = SKY_LEVELS.get(channel, 0.05) if sky is None else sky
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    img = np.full((H, W), level, dtype=np.float64)
    gx, gy, q = gradient
    if gx or gy or q:
        u, v = xx / W - 0.5, yy / H - 0.5
        img += gx * u + gy * v + q * (u * u + v * v)
    if blob is not None:
        bx, by, bs, ba = blob
        img += ba * np.exp(-((xx - bx) ** 2 + (yy - by) ** 2) / (2 * bs * bs))
    sig = fwhm * d / 2.35482
    half = int(math.ceil(5 * sig))
    inside = []
    for (x, y), peak in zip(pts, cat[:, 2]):
        if not (2 <= x < W - 2 and 2 <= y < H - 2):
            continue
        x0, y0 = int(x), int(y)
        sl = (slice(max(0, y0 - half), min(H, y0 + half + 1)), slice(max(0, x0 - half), min(W, x0 + half + 1)))
        img[sl] += peak * np.exp(-((xx[sl] - x) ** 2 + (yy[sl] - y) ** 2) / (2 * sig * sig))
        inside.append((x, y, peak))
    if noise > 0:
        img += np.random.default_rng(seed).normal(0.0, noise, img.shape)
    img = np.clip(img, 0.0, 1.0).astype(np.float32)

    header: dict[str, Any] = dict(hdr) if with_wcs else {}
    pixel_um = NATIVE_PIXEL_UM / d
    header.update({
        "RA": RA0, "DEC": DEC0,
        "FOCALLEN": round(206.265 * NATIVE_PIXEL_UM / scale_arcsec, 3),
        "XPIXSZ": pixel_um, "YPIXSZ": pixel_um,
        "IMAGETYP": "Light",
    })
    fname = FILTER_NAMES.get(channel, channel) if filter_name is None else filter_name
    if fname:
        header["FILTER"] = fname
    if instrume:
        header["INSTRUME"] = instrume
    if extra:
        header.update(extra)
    return Master(channel=channel, planes=[img], header=header, stars=np.array(inside, dtype=np.float64).reshape(-1, 3))


def make_master_set(channels: Sequence[str] = ("L", "R", "G", "B"), **common: Any) -> dict[str, Master]:
    """One master per channel with small known shifts and rotations (the first channel is unshifted)."""
    offsets = [(0.0, 0.0, 0.0), (6.3, -4.7, 0.4), (-7.1, 3.2, -0.3), (2.5, 2.5, 0.0), (-3.9, -5.2, 0.2),
               (4.4, 6.1, -0.2), (-5.5, 1.7, 0.1)]
    out: dict[str, Master] = {}
    for i, ch in enumerate(channels):
        dx, dy, rot = offsets[i % len(offsets)]
        kw = dict(common)
        kw.setdefault("seed", i + 1)
        out[ch] = make_master(ch, shift=(dx, dy), rotation_deg=rot, **kw)
    return out


def write_master(path: "str | Path", master: Master) -> Path:
    """Write ``master`` as a 32-bit float FITS file (FITS row order, as Siril reads it). Returns the path."""
    p = Path(path)
    write_fits(p, master.planes, master.header, bitpix=-32)
    return p
