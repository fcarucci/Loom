"""WCS helpers: FITS TAN keywords, projection of a header's sky, the field and its scale (Sky.js 461-517, 1232-1250, 1356-1363).

A wcs is the Fly.js dict ``{"crval1", "crval2", "crpix1", "crpix2", "cd": [cd11, cd12, cd21, cd22]}``. The JS
versions take a PixInsight window; these take the wcs dict and the image size instead. Pixel i's centre is at i
(0-based); CRPIX is 1-based and core.tan_project handles the shift.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from typing import Any

from loomsiril.core import Point, separation, tan_project, tan_unproject

__all__ = [
    "field",
    "field_corners",
    "flip_rows",
    "keyword_number",
    "keyword_wcs",
    "projector",
    "scaled_wcs",
    "solved_scale",
    "unprojector",
    "wcs_from_solution",
]

# parseFloat's longest numeric prefix (after leading whitespace), as the JS Sky.keywordNumber reads a value.
_PARSE_FLOAT = re.compile(r"\s*([+-]?(?:Infinity|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?))")


def keyword_number(header: Mapping[str, Any], name: str) -> float | None:
    """Sky.keywordNumber: a FITS keyword's value as a finite number, or None when absent or not numeric.

    The JS strips the single quotes of a FITS string (``'324.745'``) and takes parseFloat of the rest, so a
    prefix counts (``"12.5 deg"`` is 12.5). A boolean is not a number here, as ``String(true)`` is not in the JS.
    """
    if name not in header:
        return None
    v = header[name]
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    m = _PARSE_FLOAT.match(str(v).replace("'", ""))
    if m is None:
        return None
    f = float(m.group(1))
    return f if math.isfinite(f) else None


def keyword_wcs(header: Mapping[str, Any]) -> dict[str, Any] | None:
    """Sky.keywordWcs: the TAN wcs dict from CRVAL1/2, CRPIX1/2 and CD1_1, CD1_2, CD2_1, CD2_2; None if any is missing."""
    n = lambda k: keyword_number(header, k)  # noqa: E731  (mirrors the JS closure)
    w = {"crval1": n("CRVAL1"), "crval2": n("CRVAL2"), "crpix1": n("CRPIX1"), "crpix2": n("CRPIX2"),
         "cd": [n("CD1_1"), n("CD1_2"), n("CD2_1"), n("CD2_2")]}
    values = [w["crval1"], w["crval2"], w["crpix1"], w["crpix2"], *w["cd"]]
    if any(v is None for v in values):
        return None
    return w


def flip_rows(wcs: Mapping[str, Any], h: int) -> dict[str, Any]:
    """The same sky on vertically flipped rows (loomsiril is top-down; FITS and Siril are bottom-up).

    A pixel at row y of an h-row image is row h - 1 - y after the flip. With CRPIX2 1-based, the offset
    from the reference row changes sign (crpix2' = h + 1 - crpix2), so the CD terms that multiply dy change
    sign: CD1_2 and CD2_2 (cd[1], cd[3]). CD1_1 and CD2_1 multiply dx and are kept. It is its own inverse.
    """
    out = dict(wcs)
    out["crpix2"] = h + 1 - wcs["crpix2"]
    c = wcs["cd"]
    out["cd"] = [c[0], -c[1], c[2], -c[3]]
    return out


def projector(wcs: Mapping[str, Any]) -> Callable[[float, float], Point]:
    """Sky.projector for a wcs: (ra, dec) degrees -> pixel Point (0-based; CRPIX 1-based lands on crpix - 1)."""
    w = dict(wcs)

    def project(ra: float, dec: float) -> Point:
        return tan_project(ra, dec, w)

    return project


def unprojector(wcs: Mapping[str, Any]) -> Callable[[float, float], dict[str, float]]:
    """Sky.unprojector for a wcs: pixel (x, y), 0-based -> ``{ra, dec}`` degrees."""
    w = dict(wcs)

    def unproject(x: float, y: float) -> dict[str, float]:
        return tan_unproject(x, y, w)

    return unproject


def field(wcs: Mapping[str, Any], w: int, h: int) -> dict[str, Any]:
    """Sky.field: ``{centre: {ra, dec}, radius_deg}``, the image centre and the radius reaching its farthest corner."""
    un = unprojector(wcs)
    centre = un(w / 2, h / 2)
    radius = 0.0
    for x, y in ((0, 0), (w, 0), (0, h), (w, h)):
        radius = max(radius, separation(centre, un(x, y)))
    return {"centre": centre, "radius_deg": radius}


def field_corners(wcs: Mapping[str, Any], w: int, h: int) -> list[dict[str, float]]:
    """Sky.fieldCorners: the corners' ``{ra, dec}``: top left, top right, bottom left, bottom right (JS order)."""
    un = unprojector(wcs)
    return [un(0, 0), un(w, 0), un(0, h), un(w, h)]


def solved_scale(wcs: Mapping[str, Any], w: int, h: int) -> float:
    """Sky.solvedScale: the image's scale in arcsec per pixel, measured across its width along the middle row."""
    un = unprojector(wcs)
    return 3600 * separation(un(0, h / 2), un(w, h / 2)) / w


def scaled_wcs(wcs: Mapping[str, Any], f: float) -> dict[str, Any]:
    """Sky.scaledWcs: the TAN wcs after a resample by f; pixel centres map to ``(x + 0.5) f - 0.5``."""
    return {
        "crval1": wcs["crval1"],
        "crval2": wcs["crval2"],
        "crpix1": (wcs["crpix1"] - 0.5) * f + 0.5,
        "crpix2": (wcs["crpix2"] - 0.5) * f + 0.5,
        "cd": [v / f for v in wcs["cd"]],
    }


def wcs_from_solution(d: Mapping[str, Any]) -> dict[str, Any]:
    """The wcs dict from the CLI's solve JSON ``{crval1, crval2, crpix1, crpix2, cd: [4 numbers]}``, validated.

    Raises ValueError for a missing key, a non-numeric or non-finite value, or a cd that is not four numbers.
    """
    out: dict[str, Any] = {}
    for k in ("crval1", "crval2", "crpix1", "crpix2"):
        out[k] = _finite(d, k)
    cd = d.get("cd") if isinstance(d, Mapping) else None
    if not isinstance(cd, (list, tuple)) or len(cd) != 4:
        raise ValueError("wcs solution: cd must be a list of four numbers")
    out["cd"] = [_finite({"cd": v}, "cd") for v in cd]
    return out


def _finite(d: Mapping[str, Any], k: str) -> float:
    """``d[k]`` as a finite float, or ValueError naming the key."""
    if not isinstance(d, Mapping) or k not in d:
        raise ValueError(f"wcs solution: missing {k}")
    v = d[k]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"wcs solution: {k} is not a number: {v!r}")
    if not math.isfinite(float(v)):
        raise ValueError(f"wcs solution: {k} is not finite: {v!r}")
    return float(v)
