"""Parallax quality, illuminators, the ionising-cluster finder and star placement (E1a).

Ported from Loom ``script/lib/Fly.js`` (v0.4.1), lines 293-349, 362-461 and
678-692. Pure maths over Gaia records (plain dicts ``{ra, dec, plx, pmra,
pmdec, G, BP, RP}`` with NaN for blanks). The JS is the oracle
(tests/oracle/parallax.json); the IC 1396 regression reads the public
fixture (tests/fixtures/ic1396-gaia-g16.tsv).
"""

from __future__ import annotations

import functools
import math
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from loomsiril.core import Point, mad, median, separation
from loomsiril.jsmath import js_div, js_max, js_min
from loomsiril.types import GaiaSource, Placed, Projector

__all__ = [
    "CLUSTER_GROW",
    "CLUSTER_MAX_RADIUS",
    "CLUSTER_MIN_CONTRAST",
    "CLUSTER_MIN_MEMBERS",
    "CLUSTER_MIN_SIGNIFICANCE",
    "CLUSTER_PLX_SCALE",
    "CLUSTER_PM_SCALE",
    "ILLUMINATOR_MAX_G",
    "ILLUMINATOR_MIN_RADIUS",
    "PARALLAX_ZERO_POINT",
    "SIGMA_TABLE",
    "clamp_travel",
    "cluster_radius",
    "cluster_samples",
    "default_travel",
    "find_cluster",
    "illuminator_distance",
    "illuminator_radius",
    "parallax_sigma",
    "place_stars",
    "refine_cluster",
    "usable_parallax",
]

#: mas, DR3 global parallax offset (added to the catalogue parallax).
PARALLAX_ZERO_POINT: float = 0.017

#: Gaia DR3 median parallax uncertainty by magnitude (Lindegren et al. 2021): ``(G, sigma mas)`` knots.
#: A population model, not a per-star error: DR3 rows carry no per-source parallax error here.
SIGMA_TABLE: list[tuple[float, float]] = [(15, 0.02), (17, 0.07), (17.6, 0.10)]


def parallax_sigma(G: float) -> float:
    """``Fly.parallaxSigma``: the parallax uncertainty (mas) at magnitude G, interpolated in SIGMA_TABLE, flat at both ends."""
    t = SIGMA_TABLE
    if not (G > t[0][0]):
        return t[0][1]
    for i in range(1, len(t)):
        if G <= t[i][0]:
            return t[i - 1][1] + (G - t[i - 1][0]) * (t[i][1] - t[i - 1][1]) / (t[i][0] - t[i - 1][0])
    return t[-1][1]


def _field(s: Mapping[str, Any], key: str) -> float:
    """A record field as a float: a missing or None value is NaN, as the JS undefined compares."""
    v = s.get(key)
    return math.nan if v is None else float(v)


def usable_parallax(s: Mapping[str, Any]) -> Optional[float]:
    """``Fly.usableParallax``: the zero-point-corrected parallax (mas) when it is finite, positive and at least 5 sigma; else None."""
    plx = s.get("plx")
    if isinstance(plx, bool) or not isinstance(plx, (int, float)) or not math.isfinite(plx):
        return None
    p = plx + PARALLAX_ZERO_POINT
    return p if (p > 0 and p >= 5 * parallax_sigma(_field(s, "G"))) else None


#: A star must be at least this bright (G) to light a nebula.
ILLUMINATOR_MAX_G: float = 10
#: Degrees: the smallest area searched about the target.
ILLUMINATOR_MIN_RADIUS: float = 0.1


def illuminator_radius(target: Mapping[str, Any]) -> float:
    """``Fly.illuminatorRadius``: the search radius (deg) about a nebula: its catalogued radius (diameter/120), at least ILLUMINATOR_MIN_RADIUS."""
    d = target.get("diameter")
    return js_max(ILLUMINATOR_MIN_RADIUS, d / 120 if (d is not None and d > 0) else 0)


def illuminator_distance(sources: Optional[Sequence[GaiaSource]], target: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """``Fly.illuminatorDistance``: distance of the star that lights a nebula with no ionising cluster (a reflection nebula such as NGC 7023).

    The brightest star within the nebula with a reliable parallax and
    G < ILLUMINATOR_MAX_G. Returns ``{distance (pc), G, source}`` or None.
    """
    r = illuminator_radius(target)
    best: Optional[dict[str, Any]] = None
    for s in sources or []:
        if not (_field(s, "G") < ILLUMINATOR_MAX_G) or separation(s, target) > r:
            continue
        p = usable_parallax(s)
        if p is not None and (best is None or _field(s, "G") < best["G"]):
            best = {"distance": 1000 / p, "G": _field(s, "G"), "source": s}
    return best


# ---------------------------------------------------------------------------
# The ionising cluster: shared motion and distance.
# ---------------------------------------------------------------------------

#: Stars needed inside and in the comparison annulus.
CLUSTER_MIN_MEMBERS: int = 30
#: Membership contrast: members per field star (scaled) must reach this.
CLUSTER_MIN_CONTRAST: float = 3
#: Excess over the field, in Poisson sigmas.
CLUSTER_MIN_SIGNIFICANCE: float = 5
#: Degrees; the annulus reaches 3x this.
CLUSTER_MAX_RADIUS: float = 0.45
#: Kernel in proper motion, x the field's MAD.
CLUSTER_PM_SCALE: float = 0.3
#: Kernel in parallax, x sigma(G).
CLUSTER_PLX_SCALE: float = 3
#: Membership kernel, x the seed kernel.
CLUSTER_GROW: float = 1.5


def cluster_radius(target_radius_deg: float) -> float:
    """``Fly.clusterRadius``: the inside radius for a target of catalogued radius, capped at CLUSTER_MAX_RADIUS.

    A big nebula's cluster sits near its centre; capped, the query stays within
    a degree or two instead of the whole of IC 1396's 4 degrees.
    """
    return js_min(target_radius_deg, CLUSTER_MAX_RADIUS)


def cluster_samples(sources: Sequence[GaiaSource], target: Mapping[str, Any], radius_deg: float) -> dict[str, list[dict[str, float]]]:
    """``Fly.clusterSamples``: stars with a usable parallax, G < 16 and finite proper motion, split into ``inside`` (within radius) and ``field`` (1.5-3 radii).

    Each record is ``{p, a, d, sp}``: parallax (mas, corrected), pmra, pmdec and sigma(G).
    A None proper motion counts as NaN (not finite), which is what Gaia's blanks mean here.
    """
    inside: list[dict[str, float]] = []
    field: list[dict[str, float]] = []
    for s in sources:
        p = usable_parallax(s)
        pmra = _field(s, "pmra")
        pmdec = _field(s, "pmdec")
        if p is None or not (_field(s, "G") < 16) or not math.isfinite(pmra) or not math.isfinite(pmdec):
            continue
        sep = separation(s, target)
        rec = {"p": p, "a": pmra, "d": pmdec, "sp": parallax_sigma(_field(s, "G"))}
        if sep <= radius_deg:
            inside.append(rec)
        elif 1.5 * radius_deg <= sep <= 3 * radius_deg:
            field.append(rec)
    return {"inside": inside, "field": field}


NearFactory = Callable[[dict[str, float], float], Callable[[dict[str, float]], bool]]


class _ClusterFinder:
    """The per-seed counts of Fly.findCluster: inside and field stars as numpy columns, scaled by ``sa``/``sd``.

    ``sa`` and ``sd`` are the field's proper-motion sigmas (MAD, scaled); ``scale`` takes the field count to the
    inside stars' area. The vector and scalar tests use the same float operations in the same order.
    """

    def __init__(self, inside: Sequence[dict[str, float]], field: Sequence[dict[str, float]], sa: float, sd: float) -> None:
        self.sa = sa
        self.sd = sd
        self.scale = len(inside) / len(field)
        self.cols_in = _columns(inside)
        self.cols_field = _columns(field)

    def hits(self, cols: tuple[np.ndarray, ...], c: dict[str, float], k: float) -> np.ndarray:
        """Which star columns lie within ``k`` of seed ``c`` (in proper-motion field sigmas and parallax error)."""
        a, d, p, sp = cols
        da = (a - c["a"]) / self.sa
        dd = (d - c["d"]) / self.sd
        dp = (p - c["p"]) / (CLUSTER_PLX_SCALE * np.maximum(c["sp"], sp))
        return da * da + dd * dd + dp * dp <= k * k

    def within(self, x: dict[str, float], c: dict[str, float], k: float) -> bool:
        """The scalar twin of :meth:`hits` for one star record."""
        da = (x["a"] - c["a"]) / self.sa
        dd = (x["d"] - c["d"]) / self.sd
        dp = (x["p"] - c["p"]) / (CLUSTER_PLX_SCALE * js_max(c["sp"], x["sp"]))
        return da * da + dd * dd + dp * dp <= k * k

    def near(self, c: dict[str, float], k: float) -> Callable[[dict[str, float]], bool]:
        """The ``near`` factory of :func:`refine_cluster`: a test for the records within ``k`` of ``c``."""
        return functools.partial(self.within, c=c, k=k)

    def counts(self, c: dict[str, float], k: float) -> dict[str, float]:
        """Inside stars near ``c`` (``n``) and the field's, scaled to the inside area (``f``)."""
        return {"n": float(np.count_nonzero(self.hits(self.cols_in, c, k))),
                "f": float(np.count_nonzero(self.hits(self.cols_field, c, k))) * self.scale}


def _quantile(ps: Sequence[float], f: float) -> float:
    """The sorted value at index floor(f * n), clamped to the last one (the JS quartile rule)."""
    return ps[min(len(ps) - 1, math.floor(f * len(ps)))]


def find_cluster(sources: Sequence[GaiaSource], target: Mapping[str, Any], radius_deg: float) -> Optional[dict[str, float]]:
    """``Fly.findCluster``: the ionising cluster by shared motion and distance, or None.

    Each inside star is scored by how far the inside stars near it (in proper motion and
    parallax) exceed what the field predicts, in Poisson sigmas: an excess,
    not a ratio, because a ratio lets a chance clump of five stars over an
    empty field outrank a real cluster of fifty. The best seed's centre is
    refined to the median of its neighbours; members are taken with a wider
    kernel, since a real cluster's parallaxes spread by its depth too.

    Validated on IC 1396: 922 pc (Trumpler 37; published ~925 pc).
    Returns ``{distance, members, lo, hi}`` (pc; lo/hi the inter-quartile range).
    The per-seed counts are numpy over the star columns (same float operations
    in the same order as the JS, so the same numbers).
    """
    samples = cluster_samples(sources, target, radius_deg)
    inside, field = samples["inside"], samples["field"]
    if len(inside) < CLUSTER_MIN_MEMBERS or len(field) < CLUSTER_MIN_MEMBERS:
        return None
    sa = mad([x["a"] for x in field]) * CLUSTER_PM_SCALE
    sd = mad([x["d"] for x in field]) * CLUSTER_PM_SCALE
    finder = _ClusterFinder(inside, field, sa, sd)

    best: Optional[dict[str, Any]] = None
    for c in inside:
        n = finder.counts(c, 1)
        sig = (n["n"] - n["f"]) / math.sqrt(n["f"] + 1)
        if best is None or sig > best["sig"]:
            best = {"centre": c, "sig": sig}
    assert best is not None  # inside is non-empty here
    centre = refine_cluster(inside, best["centre"], finder.near)
    member_hits = finder.hits(finder.cols_in, centre, CLUSTER_GROW)
    members = [x for x, keep in zip(inside, member_hits) if keep]
    n = finder.counts(centre, CLUSTER_GROW)
    if (len(members) < CLUSTER_MIN_MEMBERS or js_div(n["n"], js_max(1, n["f"])) < CLUSTER_MIN_CONTRAST
            or best["sig"] < CLUSTER_MIN_SIGNIFICANCE):
        return None
    ps = sorted(x["p"] for x in members)
    return {"distance": 1000 / _quantile(ps, 0.5), "members": len(ps),
            "lo": 1000 / _quantile(ps, 0.75), "hi": 1000 / _quantile(ps, 0.25)}


def _columns(recs: Sequence[dict[str, float]]) -> tuple[np.ndarray, ...]:
    """The (pmra, pmdec, parallax, sigma) columns of cluster records as float64 arrays."""
    return (np.array([x["a"] for x in recs], dtype=np.float64), np.array([x["d"] for x in recs], dtype=np.float64),
            np.array([x["p"] for x in recs], dtype=np.float64), np.array([x["sp"] for x in recs], dtype=np.float64))


def refine_cluster(inside: Sequence[dict[str, float]], seed: dict[str, float], near: NearFactory) -> dict[str, float]:
    """``Fly.refineCluster``: move the seed to the median of its members, up to five times (stops when none are near)."""
    c = seed
    for _ in range(5):
        m = [x for x in inside if near(c, CLUSTER_GROW)(x)]
        if not m:
            break
        c = {"a": median([x["a"] for x in m]),
             "d": median([x["d"] for x in m]),
             "p": median([x["p"] for x in m]),
             "sp": seed["sp"]}
    return c


# ---------------------------------------------------------------------------
# Travel and placement.
# ---------------------------------------------------------------------------


def default_travel(type_: str, D: float) -> float:
    """``Fly.defaultTravel``: the default camera travel (pc): 200 for a galaxy, else 20% of the target distance."""
    return 200 if type_ == "galaxy" else 0.2 * D


def clamp_travel(travel: float, type_: str, D: float) -> dict[str, Any]:
    """``Fly.clampTravel``: a nebula's travel is capped at 90% of its distance (never past the star); a galaxy is never capped."""
    if type_ == "galaxy" or not (travel > 0.9 * D):
        return {"travel": travel, "clamped": False}
    return {"travel": 0.9 * D, "clamped": True}


def place_stars(sources: Sequence[GaiaSource], project: Projector, w: int, h: int) -> list[Placed]:
    """``Fly.placeStars``: sources with a usable parallax that land inside a w x h image, with their distance in parsecs.

    ``project(ra, dec)`` is the image's projection and returns a Point, or None off the projection.
    """
    out: list[Placed] = []
    for s in sources:
        p = usable_parallax(s)
        if p is None:
            continue
        q: Optional[Point] = project(_field(s, "ra"), _field(s, "dec"))
        if q is None or not (0 <= q.x < w and 0 <= q.y < h):
            continue
        out.append(Placed(source=s, d=1000 / p, x=q.x, y=q.y))
    return out
