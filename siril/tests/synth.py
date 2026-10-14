"""Synthetic star fields with known answers (no real frame is ever used).

``make_field()`` builds an image the way Fly-Through sees one:

* a smooth ``starless`` background (a gentle gradient plus a faint blob, so a
  "nebula" exists for the backdrop),
* Gaussian stars of known position (0-based, pixel i's centre at i), FWHM and
  peak, drawn into a ``stars`` layer,
* ``image = screen(starless, stars)`` (+ optional noise), so
  ``unscreen(image, starless)`` gives the stars layer back,
* FITS TAN keywords (``header``) and the equivalent ``wcs`` dict
  ``{crval1, crval2, crpix1, crpix2, cd: [cd11, cd12, cd21, cd22]}``,
* a Gaia-like catalogue: one source per injected star (ra/dec from the TAN
  inverse, a parallax, G from the flux) plus a few faint sources with no star,
  as ``dict(ra, dec, plx, pmra, pmdec, G, BP, RP)`` (NaN for blanks), exactly the
  record shape of ``Fly.parseGaiaCsv``.

The TAN inverse here is written independently of ``loomsiril.geometry`` on
purpose, so tests that use it check the port rather than agree with it.

Everything is deterministic for a given ``seed``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from loomsiril.core import screen, unscreen

RAD = math.pi / 180


@dataclass
class SynthStar:
    """One injected star: centre (0-based px), Gaussian FWHM (px), peak (0..1), catalogue values."""

    x: float
    y: float
    fwhm: float
    peak: float
    ra: float
    dec: float
    plx: float  # mas
    G: float

    @property
    def flux(self) -> float:
        """Total light of the Gaussian: peak * 2 pi sigma^2."""
        sigma = self.fwhm / 2.3548200450309493
        return self.peak * 2 * math.pi * sigma * sigma


@dataclass
class SynthField:
    """A synthetic field: planes are lists of (h, w) float32 arrays in [0, 1], one per channel."""

    w: int
    h: int
    nc: int
    starless: list[np.ndarray]
    stars: list[np.ndarray]
    image: list[np.ndarray]
    truth: list[SynthStar]
    header: dict[str, Any]
    wcs: dict[str, Any]
    catalogue: list[dict[str, float]] = field(default_factory=list)
    seed: int = 0


def tan_unproject(x: float, y: float, wcs: dict[str, Any]) -> tuple[float, float]:
    """0-based pixel -> (ra, dec) degrees through FITS TAN keywords (independent of the port)."""
    c = wcs["cd"]
    dx, dy = x - (wcs["crpix1"] - 1), y - (wcs["crpix2"] - 1)
    xi = (c[0] * dx + c[1] * dy) * RAD
    eta = (c[2] * dx + c[3] * dy) * RAD
    a0, d0 = wcs["crval1"] * RAD, wcs["crval2"] * RAD
    den = math.cos(d0) - eta * math.sin(d0)
    a = a0 + math.atan2(xi, den)
    d = math.atan2(math.sin(d0) + eta * math.cos(d0), math.hypot(xi, den))
    return (a / RAD + 360) % 360, d / RAD


def tan_header(w: int, h: int, ra: float, dec: float, scale_arcsec: float, rot_deg: float = 0.0) -> dict[str, Any]:
    """FITS TAN keywords centred on the image (CRPIX at the centre, 1-based), north = +y for +CD2_2."""
    s = scale_arcsec / 3600.0
    r = rot_deg * RAD
    # RA increases to the left (east left) as on the sky: CD1_1 negative.
    cd11, cd12 = -s * math.cos(r), s * math.sin(r)
    cd21, cd22 = s * math.sin(r), s * math.cos(r)
    return {
        "SIMPLE": True,
        "NAXIS": 2,
        "NAXIS1": w,
        "NAXIS2": h,
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRVAL1": float(ra),
        "CRVAL2": float(dec),
        "CRPIX1": (w + 1) / 2.0,
        "CRPIX2": (h + 1) / 2.0,
        "CD1_1": cd11,
        "CD1_2": cd12,
        "CD2_1": cd21,
        "CD2_2": cd22,
        "OBJECT": "SYNTHETIC",
    }


def wcs_from_header(header: dict[str, Any]) -> dict[str, Any]:
    """The Fly.js ``wcs`` dict from TAN keywords."""
    return {
        "crval1": float(header["CRVAL1"]),
        "crval2": float(header["CRVAL2"]),
        "crpix1": float(header["CRPIX1"]),
        "crpix2": float(header["CRPIX2"]),
        "cd": [float(header["CD1_1"]), float(header["CD1_2"]), float(header["CD2_1"]), float(header["CD2_2"])],
    }


def gaussian(w: int, h: int, x: float, y: float, fwhm: float, peak: float) -> np.ndarray:
    """A (h, w) float64 Gaussian of the given peak centred at (x, y), pixel centres at integers."""
    sigma = fwhm / 2.3548200450309493
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    return peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma * sigma))


def make_field(
    w: int = 160,
    h: int = 90,
    n_stars: int = 30,
    nc: int = 1,
    seed: int = 1,
    fwhm: float = 2.5,
    background: float = 0.08,
    noise: float = 0.0,
    ra: float = 324.745,
    dec: float = 57.514,
    scale_arcsec: float = 2.0,
    margin: float = 4.0,
    n_faint_extra: int = 5,
) -> SynthField:
    """Build a deterministic synthetic field (see module docstring).

    Stars are placed at least ``2*fwhm`` apart (no blends unless a test adds
    them) and ``margin`` px inside the frame. Peaks span 0.15..0.9; G maps
    from flux as ``G = 14 - 2.5 log10(flux / flux_ref)``. Parallaxes span
    0.3..3 mas (330..3300 pc). Colour planes (nc=3) get slightly different
    star colours and backgrounds so channels are not identical.
    """
    rng = np.random.default_rng(seed)
    header = tan_header(w, h, ra, dec, scale_arcsec)
    wcs = wcs_from_header(header)

    truth: list[SynthStar] = []
    tries = 0
    while len(truth) < n_stars and tries < n_stars * 200:
        tries += 1
        x = float(rng.uniform(margin, w - 1 - margin))
        y = float(rng.uniform(margin, h - 1 - margin))
        if any((s.x - x) ** 2 + (s.y - y) ** 2 < (2 * fwhm) ** 2 for s in truth):
            continue
        peak = float(rng.uniform(0.15, 0.9))
        sra, sdec = tan_unproject(x, y, wcs)
        truth.append(SynthStar(x, y, fwhm, peak, sra, sdec, plx=float(rng.uniform(0.3, 3.0)), G=0.0))
    flux_ref = truth[0].flux if truth else 1.0
    for s in truth:
        s.G = 14.0 - 2.5 * math.log10(s.flux / flux_ref)

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    tint = [1.0] if nc == 1 else [1.0, 0.92, 0.85]
    starless: list[np.ndarray] = []
    stars: list[np.ndarray] = []
    image: list[np.ndarray] = []
    for c in range(nc):
        bg = background * tint[c] * (1 + 0.3 * xx / max(1, w - 1))
        bg += 0.05 * tint[c] * np.exp(-((xx - 0.6 * w) ** 2 + (yy - 0.4 * h) ** 2) / (2 * (0.15 * w) ** 2))
        st = np.zeros((h, w), dtype=np.float64)
        for s in truth:
            st += gaussian(w, h, s.x, s.y, s.fwhm, s.peak * (tint[c] if c else 1.0))
        st = np.clip(st, 0, 1)
        sl = np.clip(bg, 0, 1).astype(np.float32)
        img = screen(sl, st.astype(np.float32)).astype(np.float64)
        if noise > 0:
            img = np.clip(img + rng.normal(0, noise, img.shape), 0, 1)
        img32 = img.astype(np.float32)
        starless.append(sl)
        image.append(img32)
        stars.append(unscreen(img32, sl))

    catalogue = [
        {"ra": s.ra, "dec": s.dec, "plx": s.plx, "pmra": 0.0, "pmdec": 0.0, "G": s.G, "BP": s.G + 0.4, "RP": s.G - 0.5}
        for s in truth
    ]
    for _ in range(n_faint_extra):
        fra, fdec = tan_unproject(float(rng.uniform(0, w - 1)), float(rng.uniform(0, h - 1)), wcs)
        catalogue.append(
            {"ra": fra, "dec": fdec, "plx": float("nan"), "pmra": float("nan"), "pmdec": float("nan"),
             "G": 19.5, "BP": float("nan"), "RP": float("nan")}
        )
    return SynthField(w, h, nc, starless, stars, image, truth, header, wcs, catalogue, seed)
