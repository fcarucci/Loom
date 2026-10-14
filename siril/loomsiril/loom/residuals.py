"""Astrometric residual check: the deviation of a plate solution from Gaia DR3, as a verdict (Loom's lib/Steps.js:656-942).

The verdict is drawn against the median deviation in pixels. ``residual_verdict`` is the JS ``Steps.residualVerdict``
(lib/Steps.js:761-775): "bad" at or above RESIDUALS_BAD_PX, "poor" above RESIDUALS_WARN_PX, "ok" otherwise, and
"unknown" for a value that is not a finite non-negative number.

``measure_residual`` is the measurement that PixInsight's AstrometricResiduals makes, reimplemented in numpy. Each
detected star (Siril's PSF centre) is taken through the header's forward SIP distortion (when the header has one) and
then the linear TAN solution (CD matrix); the catalogue is projected through the TAN solution alone. Each star is
paired with its nearest catalogue star, pairs farther than the matching tolerance are dropped, and the deviation is
the separation in detector pixels: the offset on the undistorted grid mapped back through the local inverse Jacobian
of the SIP distortion (linearised; no inverse SIP is needed). The deviations are 5-sigma clipped (one-sided, iterated
to a fixed point, robust sigma floored at SIGMA_FLOOR_PX) and their median is the statistic. Fewer than MIN_RETAINED
retained pairs give no statistic, and so the verdict "unknown".

Header contract: the header is a TAN solution on TOP-DOWN rows (the row order of ``loomsiril.io_fits.read_fits``,
which sets LOOMROWS to "top-down" and flips the CD keywords), with CD keywords (not PC/CDELT). SIP coefficients are
kept as the FITS file stores them (bottom-up); this module flips them to the top-down rows itself. A header that
fails the contract is skipped by ``ResidualChecker`` with one log line for the run.

``ResidualChecker`` is the run-level wrapper. The catalogue is reached only through an injected ``gaia_source`` (the
caller wires the cache and the online query). An unreachable catalogue skips the check for the rest of the run, with
exactly one log line; the run continues. The brightest 1.5 x n_stars catalogue records are used, which is what the
PixInsight magnitude search (autoMagnitude) converges to; the catalogue magnitude limit is the fixed
RESIDUALS_CONFIG["magnitude"].
"""

from __future__ import annotations

import math
import numbers
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from loomsiril import gaia
from loomsiril import wcs as W
from loomsiril.io_fits import LOOMROWS

__all__ = [
    "MIN_RETAINED",
    "RESIDUALS_BAD_PX",
    "RESIDUALS_CONFIG",
    "RESIDUALS_WARN_PX",
    "GaiaSource",
    "ResidualChecker",
    "gaia_catalogue_source",
    "measure_residual",
    "residual_verdict",
]

#: Steps.RESIDUALS_WARN_PX: the median deviation the previous PixInsight solver reached on a hard mosaic panel.
RESIDUALS_WARN_PX: float = 0.315
#: Steps.RESIDUALS_BAD_PX: the verifier's matching tolerance, the largest deviation the measurement can represent.
RESIDUALS_BAD_PX: float = 3.0

#: The fields of Steps.RESIDUALS_CONFIG (lib/Steps.js:656-674) that the residual measurement uses.
RESIDUALS_CONFIG: dict[str, float] = {
    "magnitude": 16.0,          # catalogue G limit, requested from the catalogue by the checker
    "matchingTolerance": 3.0,   # px: a detected star pairs with a catalogue star within this radius
    "rejectionSigma": 5.0,      # sigma clipping of the deviations
}

#: Siril's PSF positions use the pixel-edge convention: xpos = column + 0.5 (see loomsiril/siril_bridge.py:185-194).
SIRIL_PSF_ORIGIN: float = 0.5
#: Floor of the robust sigma, in px. Below this a field is measured to the pixel anyway.
SIGMA_FLOOR_PX: float = 0.01
#: The clipping stops after this many rounds at most (it stops earlier once the retained set is stable).
MAX_CLIP_ROUNDS: int = 50
#: Fewer retained pairs than this give no statistic (Loom's "too few stars": verdict "unknown").
MIN_RETAINED: int = 5
#: The catalogue kept after the fetch, per detected star, brightest first.
CATALOGUE_FACTOR: float = 1.5
#: Step of the finite difference that gives the local Jacobian of the SIP distortion, in px.
SIP_JACOBIAN_STEP: float = 0.5

#: gaia_source(centre, radius_deg, g_max) -> source records (dicts with ra, dec, G), or None when unreachable.
#: ``centre`` is ``{"ra": deg, "dec": deg}``.
GaiaSource = Callable[[Mapping[str, float], float, float], "list[dict[str, Any]] | None"]

_SIP_COEFF = re.compile(r"([AB])_(\d+)_(\d+)")
_SIP_INVERSE = re.compile(r"([AB])P_(\d+)_(\d+)")
_SIP_ORDER_MIN, _SIP_ORDER_MAX = 1, 9


def residual_verdict(median_px: Any) -> str:
    """Steps.residualVerdict: "ok", "poor" or "bad" for a median deviation in px; "unknown" for no usable number.

    A boolean, a string, None, NaN, infinity and a negative value have no verdict.
    """
    if isinstance(median_px, bool) or not isinstance(median_px, numbers.Real):
        return "unknown"
    v = float(median_px)
    if not math.isfinite(v) or v < 0:
        return "unknown"
    if v >= RESIDUALS_BAD_PX:
        return "bad"
    if v > RESIDUALS_WARN_PX:
        return "poor"
    return "ok"


def _attr(obj: Any, key: str) -> Any:
    """A field of a star or a catalogue record: a mapping key, or else an attribute (sirilpy PSFStar)."""
    if isinstance(obj, Mapping):
        return obj[key]
    return getattr(obj, key)


def _star_positions(stars: Sequence[Any]) -> np.ndarray:
    """Siril star records as 0-based top-down centres, shape (n, 2): x = xpos - 0.5, y = ypos - 0.5."""
    xy = np.empty((len(stars), 2), dtype=np.float64)
    for i, s in enumerate(stars):
        xy[i, 0] = float(_attr(s, "xpos")) - SIRIL_PSF_ORIGIN
        xy[i, 1] = float(_attr(s, "ypos")) - SIRIL_PSF_ORIGIN
    return xy


def _sip_terms(header: Mapping[str, Any]) -> Any:
    """The forward SIP terms ``(p, q, coefficient)`` of A and B as the file stores them, or None for a TAN header.

    Raises ValueError when the header carries SIP keywords that cannot be used: a forward order missing, only the
    inverse AP/BP terms, a term above its order, or a coefficient that is not finite. Terms of every order up to
    the SIP order are applied, zero ones included (Siril writes A_0_0, A_1_0, A_0_1 as zeros); AP/BP are ignored
    once A/B are present.
    """
    keys = [k for k in header if isinstance(k, str)]
    sip_ctype = any("SIP" in str(header.get(c, "")) for c in ("CTYPE1", "CTYPE2"))
    orders = {"A": W.keyword_number(header, "A_ORDER"), "B": W.keyword_number(header, "B_ORDER")}
    forward = [k for k in keys if _SIP_COEFF.fullmatch(k)]
    inverse = [k for k in keys if _SIP_INVERSE.fullmatch(k) or k in ("AP_ORDER", "BP_ORDER")]
    if orders["A"] is None and orders["B"] is None and not forward and not inverse and not sip_ctype:
        return None
    for letter, order in orders.items():
        if order is None or order != int(order) or not _SIP_ORDER_MIN <= order <= _SIP_ORDER_MAX:
            raise ValueError(f"SIP: {letter}_ORDER is missing or not an order in {_SIP_ORDER_MIN}..{_SIP_ORDER_MAX}")
    if not forward:
        raise ValueError("SIP: no forward coefficients (only the inverse AP/BP terms, or malformed keywords)")
    out: dict[str, list[tuple[int, int, float]]] = {"A": [], "B": []}
    for k in forward:
        m = _SIP_COEFF.fullmatch(k)
        assert m is not None
        letter, p, q = m.group(1), int(m.group(2)), int(m.group(3))
        if not p + q <= int(orders[letter] or 0):
            raise ValueError(f"SIP: {k} is outside the order of {letter}_ORDER")
        v = W.keyword_number(header, k)
        if v is None:
            raise ValueError(f"SIP: {k} is not a finite number")
        out[letter].append((p, q, v))
    return out["A"], out["B"]


def _top_down_sip(terms: Any) -> Any:
    """SIP coefficients on the top-down rows: with v -> -v, A_pq -> (-1)^q A_pq and B_pq -> -(-1)^q B_pq."""
    a, b = terms
    return ([(p, q, c * (-1) ** q) for p, q, c in a],
            [(p, q, -c * (-1) ** q) for p, q, c in b])


def _distort(u: np.ndarray, v: np.ndarray, a: list[tuple[int, int, float]],
             b: list[tuple[int, int, float]]) -> tuple[np.ndarray, np.ndarray]:
    """The forward SIP map on pixel offsets from CRPIX: (u, v) -> (u + f(u, v), v + g(u, v))."""
    f = np.zeros_like(u)
    g = np.zeros_like(v)
    for p, q, c in a:
        f = f + c * u ** p * v ** q
    for p, q, c in b:
        g = g + c * u ** p * v ** q
    return u + f, v + g


def _undistorted(xy: np.ndarray, w: Mapping[str, Any], terms: Any) -> tuple[np.ndarray, np.ndarray | None]:
    """Star centres after the forward SIP (0-based, on the TAN pixel grid) and the local Jacobian of the SIP map.

    Without SIP the positions are returned as they are and the Jacobian is None (the identity).
    """
    cr1, cr2 = float(w["crpix1"]), float(w["crpix2"])
    if terms is None:
        return xy.copy(), None
    a, b = _top_down_sip(terms)
    u = xy[:, 0] + 1.0 - cr1
    v = xy[:, 1] + 1.0 - cr2
    up, vp = _distort(u, v, a, b)
    out = np.column_stack([up + cr1 - 1.0, vp + cr2 - 1.0])
    h = SIP_JACOBIAN_STEP
    upu, vpu = _distort(u + h, v, a, b)
    upm, vpm = _distort(u - h, v, a, b)
    upv, vpv = _distort(u, v + h, a, b)
    upn, vpn = _distort(u, v - h, a, b)
    jac = np.empty((len(xy), 2, 2), dtype=np.float64)
    jac[:, 0, 0] = (upu - upm) / (2 * h)
    jac[:, 1, 0] = (vpu - vpm) / (2 * h)
    jac[:, 0, 1] = (upv - upn) / (2 * h)
    jac[:, 1, 1] = (vpv - vpn) / (2 * h)
    return out, jac


def _retained(dev: np.ndarray, k: float) -> np.ndarray:
    """Boolean mask of the deviations kept by iterated one-sided k-sigma clipping (robust sigma, floored).

    Deviations are distances, so only the large tail is an outlier: a value is clipped when it lies above
    median + k * sigma of the retained set. The rounds repeat until the retained set no longer changes.
    """
    keep = np.ones(dev.shape, dtype=bool)
    for _ in range(MAX_CLIP_ROUNDS):
        sub = dev[keep]
        m = float(np.median(sub))
        sigma = max(1.4826 * float(np.median(np.abs(sub - m))), SIGMA_FLOOR_PX)
        new = dev <= m + k * sigma
        if np.array_equal(new, keep):
            break
        keep = new
    return keep


def _empty(n_stars: int) -> dict[str, Any]:
    return {"median_px": math.nan, "n_matched": 0, "n_stars": n_stars, "n_retained": 0}


def measure_residual(
    stars: Sequence[Any],
    header: Mapping[str, Any],
    catalogue: Sequence[Mapping[str, Any]],
    tolerance_px: float = RESIDUALS_CONFIG["matchingTolerance"],
) -> dict[str, Any]:
    """The median deviation in px of Siril's ``stars`` from ``catalogue`` through the solution in ``header``.

    ``stars``: records (dicts or objects) with ``xpos``/``ypos`` (Siril convention). ``header``: a top-down TAN
    solution (CRVAL, CRPIX, CD keywords, optional forward SIP as stored in the file), as loomsiril.io_fits returns it.
    ``catalogue``: records with ``ra``/``dec`` in degrees; non-finite records are ignored.

    Returns ``{"median_px", "n_matched", "n_stars", "n_retained"}``: the median of the deviations that survive the
    5-sigma clipping (NaN when fewer than MIN_RETAINED survive), the stars that found a catalogue star within
    ``tolerance_px``, the stars given, and the pairs the clipping kept. Raises ValueError when the header has no TAN
    WCS or carries SIP keywords that cannot be used.
    """
    w = W.keyword_wcs(header)
    if w is None:
        raise ValueError("measure_residual: the header has no complete TAN WCS (CRVAL1/2, CRPIX1/2, CD1_1..CD2_2)")
    terms = _sip_terms(header)
    n_stars = len(stars)
    project = W.projector(w)
    cat: list[tuple[float, float]] = []
    for c in catalogue:
        ra, dec = float(_attr(c, "ra")), float(_attr(c, "dec"))
        if math.isfinite(ra) and math.isfinite(dec):
            p = project(ra, dec)
            if math.isfinite(p.x) and math.isfinite(p.y):
                cat.append((p.x, p.y))
    if n_stars == 0 or not cat:
        return _empty(n_stars)

    undist, jac = _undistorted(_star_positions(stars), w, terms)
    catxy = np.asarray(cat, dtype=np.float64)
    _, idx = cKDTree(catxy).query(undist, k=1)
    delta = undist - catxy[idx]
    if jac is not None:
        delta = np.linalg.solve(jac, delta[:, :, None])[:, :, 0]
    resid = np.hypot(delta[:, 0], delta[:, 1])
    dev = resid[resid <= tolerance_px]
    if dev.size == 0:
        return _empty(n_stars)

    keep = _retained(dev, RESIDUALS_CONFIG["rejectionSigma"])
    n_retained = int(keep.sum())
    median = float(np.median(dev[keep])) if n_retained >= MIN_RETAINED else math.nan
    return {"median_px": median, "n_matched": int(dev.size), "n_stars": n_stars, "n_retained": n_retained}


def _brightest(records: Sequence[Mapping[str, Any]], n_stars: int) -> list[Mapping[str, Any]]:
    """The records with a finite position, the brightest ``CATALOGUE_FACTOR * n_stars`` of them (G ascending)."""
    good: list[tuple[float, Mapping[str, Any]]] = []
    for r in records:
        try:
            ra, dec = float(r["ra"]), float(r["dec"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(ra) and math.isfinite(dec)):
            continue
        g = r.get("G", math.nan)
        g = float(g) if isinstance(g, numbers.Real) and not isinstance(g, bool) else math.nan
        good.append((math.inf if math.isnan(g) else g, r))
    good.sort(key=lambda t: t[0])
    limit = max(1, math.ceil(CATALOGUE_FACTOR * n_stars))
    return [r for _, r in good[:limit]]


class ResidualChecker:
    """The run's residual check: one catalogue source, and flags that skip the check for the rest of the run.

    ``gaia_source(centre, radius_deg, g_max)`` returns the Gaia records, or None when the catalogue cannot be reached
    (an exception counts as unreachable, and the run is then skipped). ``log(line)`` receives the skip lines: one for
    an unreachable catalogue and one for a header outside the contract (not top-down, or without CD keywords); each is
    logged once per run. A bad centre skips only the image it belongs to. A SIP header that cannot be used gives the
    verdict "unknown" for that image, with a note.
    """

    def __init__(self, gaia_source: GaiaSource, log: Callable[[str], Any]) -> None:
        self._gaia_source = gaia_source
        self._log = log
        self._skipped = False
        self._header_skipped = False

    @property
    def skipped(self) -> bool:
        """True once the run's check is off: the catalogue or the header was found unusable."""
        return self._skipped or self._header_skipped

    def check(self, name: str, stars: Sequence[Any], header: Mapping[str, Any]) -> dict[str, Any] | None:
        """The residual of one solved image: ``{"name", "median_px", "n_matched", "n_stars", "n_retained", "verdict"}``.

        None when the check is skipped for the image (a bad centre) or for the run (catalogue unreachable, or a
        header outside the contract, logged once).
        """
        if self.skipped:
            return None
        w = W.keyword_wcs(header)
        nx = W.keyword_number(header, "NAXIS1")
        ny = W.keyword_number(header, "NAXIS2")
        if w is None or nx is None or ny is None or str(header.get(LOOMROWS, "")) != "top-down":
            self._header_skipped = True
            self._log("the astrometric residual check is skipped for this run: the header is not a top-down TAN "
                      "solution with CD keywords and an image size (read the file with loomsiril.io_fits.read_fits, "
                      f"or tag a top-down header with {LOOMROWS}=top-down). {name} was not measured.")
            return None
        n_stars = len(stars)
        try:
            _sip_terms(header)
        except ValueError as exc:
            self._log(f"{name}: the astrometric residual check gives no verdict, the SIP header cannot be used ({exc})")
            return {"name": name, **_empty(n_stars), "verdict": "unknown"}
        fld = W.field(w, int(nx), int(ny))
        try:
            gaia.GaiaQuery.from_centre(fld["centre"], fld["radius_deg"], RESIDUALS_CONFIG["magnitude"])
        except ValueError as exc:
            self._log(f"{name}: the astrometric residual check is skipped for this image ({exc})")
            return None
        try:
            records = self._gaia_source(fld["centre"], fld["radius_deg"], RESIDUALS_CONFIG["magnitude"])
            reason = ""
        except Exception as exc:  # an unreachable catalogue must never fail the run
            records = None
            reason = f" ({exc})"
        if records is None:
            self._skipped = True
            self._log("the astrometric residual check is skipped for this run: the Gaia DR3 catalogue "
                      f"is unreachable{reason}. The solutions are unchanged.")
            return None
        result = measure_residual(stars, header, _brightest(records, n_stars))
        return {"name": name, **result, "verdict": residual_verdict(result["median_px"])}


def gaia_catalogue_source(cache_dir: Any = None, fetch_text: Callable[[str], str] | None = None) -> GaiaSource:
    """The run's Gaia DR3 source for :class:`ResidualChecker`: the local cache first, the online service otherwise.

    The cache folder defaults to ``loomsiril.gaia.default_cache_dir()`` (``$XDG_CACHE_HOME/loomsiril/gaia``). An online
    answer is written to the cache, so a field is fetched once. Any failure (no network, a timeout, an answer that is
    not a source table) raises; the checker treats that as "catalogue unreachable", logs one line and skips the check
    for the rest of the run, never failing it.
    """
    fetch = gaia.fetch_text if fetch_text is None else fetch_text

    def source(centre: Any, radius_deg: float, g_max: float) -> list[dict[str, float]]:
        query = gaia.GaiaQuery.from_centre(centre, radius_deg, g_max)
        return gaia.sources(query, source="cache", cache_dir=cache_dir, fetch_text=fetch)

    return source
