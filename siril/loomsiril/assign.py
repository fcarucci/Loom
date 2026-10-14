"""Which Gaia stars become moving sprites, and which pixels each one owns (E3a).

Ported from Loom ``script/lib/Fly.js`` (v0.4.1): matching of placed Gaia stars to
detections (694-760), blends (779-797, 806-822), sprite selection and pixel
ownership (823-927), and the reach / bright-star rules (1735-1802).

Records: detections are :class:`loomsiril.types.Detection`, placed Gaia stars
:class:`loomsiril.types.Placed`, sprite matches :class:`loomsiril.types.SpriteMatch`.
Catalogue neighbours stay plain dicts ``{source, x, y}`` (the JS shape), and Gaia
records are plain dicts ``{ra, dec, plx, pmra, pmdec, G, BP, RP}``.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Callable, Iterable, NamedTuple, Optional, Sequence

import numpy as np

from loomsiril.core import Rect, detection_rect, grown_rect, square_rect
from loomsiril.jsmath import js_or
from loomsiril.types import Detection, Halo, Placed, SpriteMatch

__all__ = [
    "MATCH_RADIUS",
    "GRID",
    "MATCH_SEARCH",
    "BLEND_COMPANION_MAG",
    "SPIKE_WIDTH",
    "BRIGHT_G",
    "BRIGHT_MISS_RADIUS",
    "STILL_REACH_SEARCH",
    "MatchPair",
    "rects_intersect",
    "detection_grid",
    "detections_in",
    "match_radius",
    "match_pairs",
    "catalogue_blend",
    "is_blend",
    "one_star_one_sprite",
    "sprite_ownership",
    "assign_sprites",
    "within_reach",
    "add_missing_bright",
    "drop_inside_brighter",
]

#: px, placed star to detection (a saturated core may widen it, see :func:`match_radius`).
MATCH_RADIUS: float = 1.5
#: px, spatial hash cell.
GRID: int = 16
#: px: the largest radius any detection may have.
MATCH_SEARCH: float = 6

#: A catalogue companion this close in magnitude (or brighter) makes a blend.
BLEND_COMPANION_MAG: float = 0.3

#: px either side of a spike's ray that count as on it.
SPIKE_WIDTH: float = 3

#: Gaia stars this bright are always given a detection.
BRIGHT_G: float = 9.5
#: px: no detection this close means the detector missed it.
BRIGHT_MISS_RADIUS: float = 6

#: px around a candidate to look for brighter stars whose reach it is in.
STILL_REACH_SEARCH: float = 150

#: Marks a pixel reserved for the residual while ownership is being built.
_RESERVED: int = -2
#: Gaia magnitude used for a placed star with no usable G (sorts it last), as the JS.
_NO_G: float = 99.0

Grid = dict  # dict[tuple[int, int], list[int]]


class MatchPair(NamedTuple):
    """A (placed star, detection) pair within match radius: JS ``{pi, di, r2, G}``."""

    pi: int
    di: int
    r2: float
    G: float


def _num(v: Any) -> float:
    """A Gaia field as a float: None (a missing key) becomes NaN, which compares false like JS undefined."""
    return math.nan if v is None else float(v)


def _gaia_g(source: Optional[dict]) -> float:
    """The G magnitude of a placed star's source, or 99 when missing or not finite."""
    if not source:
        return _NO_G
    g = source.get("G")
    if g is None or not math.isfinite(g):
        return _NO_G
    return float(g)


def _window(r: Rect, w: int, h: int) -> tuple[slice, slice]:
    """A rect clipped to the w x h image as a (rows, cols) slice pair (the JS ``fill`` loops)."""
    return slice(max(0, r.y0), min(h, r.y1)), slice(max(0, r.x0), min(w, r.x1))


def rects_intersect(a: Rect, b: Rect) -> bool:
    """``Fly.rectsIntersect``: open-interval overlap of two rects (x1/y1 exclusive)."""
    return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1


def detection_grid(dets: Sequence[Detection]) -> Grid:
    """``Fly.detectionGrid``: spatial hash, cell (gx, gy) -> indices of detections whose raw rect covers it.

    Insertion order is the JS order (per detection: rows outer, columns inner),
    which :func:`detections_in` and the match order depend on.
    """
    grid: Grid = {}
    for i, d in enumerate(dets):
        r = d.rect
        for gy in range(r.y0 // GRID, (r.y1 - 1) // GRID + 1):
            for gx in range(r.x0 // GRID, (r.x1 - 1) // GRID + 1):
                grid.setdefault((gx, gy), []).append(i)
    return grid


def detections_in(grid: Grid, r: Rect) -> list[int]:
    """``Fly.detectionsIn``: indices of detections whose raw rect meets ``r`` (deduplicated, grid order)."""
    seen: set[int] = set()
    out: list[int] = []
    for gy in range(r.y0 // GRID, (r.y1 - 1) // GRID + 1):
        for gx in range(r.x0 // GRID, (r.x1 - 1) // GRID + 1):
            for i in grid.get((gx, gy), ()):
                if i not in seen:
                    seen.add(i)
                    out.append(i)
    return out


def match_radius(det: Detection) -> float:
    """``Fly.matchRadius``: 1.5 px, or 0.45 of the core's half-width when that is more.

    A saturated core's centroid is uncertain by pixels (the 40 brightest stars of a
    real image sat 1-4 px from their Gaia stars, and none moved at 1.5 px).
    """
    return max(MATCH_RADIUS, 0.45 * (det.rect.x1 - det.rect.x0) / 2)


def match_pairs(dets: Sequence[Detection], placed: Sequence[Placed], grid: Grid) -> list[MatchPair]:
    """``Fly.matchPairs``: every (placed, detection) pair within the detection's match radius.

    Sorted brightest Gaia star first (G 99 when missing), then nearest; the sort is
    stable, so equal keys keep the JS search order.
    """
    pairs: list[MatchPair] = []
    R = MATCH_SEARCH
    for pi, p in enumerate(placed):
        box = Rect(math.floor(p.x - R), math.floor(p.y - R), math.ceil(p.x + R) + 1, math.ceil(p.y + R) + 1)
        g = _gaia_g(p.source)
        for di in detections_in(grid, box):
            dx = dets[di].x - p.x
            dy = dets[di].y - p.y
            r2 = dx * dx + dy * dy
            mr = match_radius(dets[di])
            if r2 <= mr * mr:
                pairs.append(MatchPair(pi, di, r2, g))
    pairs.sort(key=lambda m: (m.G, m.r2))
    return pairs


def catalogue_blend(det: Detection, placed: Placed, neighbours: Optional[Iterable[dict]]) -> bool:
    """``Fly.catalogueBlend``: a second catalogue source inside the core, about as bright or brighter.

    ``neighbours`` are projected catalogue sources ``{source, x, y}``. The companion
    must be no more than :data:`BLEND_COMPANION_MAG` fainter than the placed star
    (a source with no usable G is never excluded by brightness, as in the JS).
    """
    limit = (_num(placed.source.get("G")) + BLEND_COMPANION_MAG
             if placed.source and _finite(placed.source.get("G")) else math.inf)
    for n in neighbours or ():
        src = n.get("source")
        if src is not placed.source and det.rect.x0 <= n["x"] < det.rect.x1 and det.rect.y0 <= n["y"] < det.rect.y1:
            # JS: !(n.source && n.source.G > limit); NaN G compares false, so it counts as a blend
            if not (src is not None and _num(src.get("G")) > limit):
                return True
    return False


def is_blend(dets: Sequence[Detection], di: int, rect: Rect, grid: Grid) -> bool:
    """``Fly.isBlend``: several maxima, or a brighter-or-equal detection's core inside ``rect``.

    Judged near the core (one FWHM), not across the halo. Without finite fluxes any
    neighbour counts. Of a pair the brighter one moves and carries the fainter.
    """
    if dets[di].nmax > 1:
        return True
    f = dets[di].flux
    for j in detections_in(grid, rect):
        if j == di or not rects_intersect(rect, dets[j].rect):
            continue
        g = dets[j].flux
        if not (_finite(f) and _finite(g)) or g >= f:
            return True
    return False


def _finite(v: Any) -> bool:
    """JS ``isFinite`` for a number (NaN and None are not finite)."""
    return v is not None and math.isfinite(v)


def _flux_order(sprites: Sequence[SpriteMatch]) -> list[int]:
    """Sprite indices brightest first (``(b.flux || 0) - (a.flux || 0) || a - b``), stable on index."""
    return sorted(range(len(sprites)), key=lambda k: (-js_or(sprites[k].det.flux, 0.0), k))


def one_star_one_sprite(sprites: Sequence[SpriteMatch]) -> list[SpriteMatch]:
    """``Fly.oneStarOneSprite``: brightest first; a candidate inside an accepted brighter star's reach is dropped.

    A bright star's spikes cross faint Gaia stars; as sprites of their own they
    tore the bright star apart. The reach test uses the accepted star's halo when
    it has one, else a circle of its rect half-width.
    """
    accepted: list[SpriteMatch] = []
    for i in _flux_order(sprites):
        s = sprites[i]
        if not any(within_reach(a.det if a.det.halo is not None else replace(a.det, rect=a.rect), s.det)
                   for a in accepted):
            accepted.append(s)
    keep = {id(a) for a in accepted}
    return [s for s in sprites if id(s) in keep]


def sprite_ownership(dets: Sequence[Detection], sprites: Sequence[SpriteMatch], w: int, h: int,
                     halo_mode: bool) -> np.ndarray:
    """``Fly.spriteOwnership``: int32 (h, w) owner of each pixel: a sprite index, or -1 (the residual).

    Without ``halo_mode`` every other detection's core is reserved for the residual;
    each sprite then takes its own core, and footprints are filled brightest first,
    so a bright star's halo is not cut by a fainter neighbour's. With ``halo_mode``
    a star moves everything within its outer radius (a hole tore the star apart).
    """
    owner = np.full((h, w), -1, dtype=np.int32)
    own_ids = {id(s.det) for s in sprites}
    if not halo_mode:
        for d in dets:
            if id(d) not in own_ids:
                owner[_window(d.rect, w, h)] = _RESERVED
    for k, s in enumerate(sprites):
        sub = owner[_window(s.det.rect, w, h)]
        sub[sub != _RESERVED] = k  # `sub` is a view: this writes into owner
    for k in _flux_order(sprites):
        sp = sprites[k]
        ys, xs = _window(sp.rect, w, h)
        sub = owner[ys, xs]
        if halo_mode:
            H = sp.det.halo
            outer = H.outer if H is not None else sp.rect.width / 2
            yy = np.arange(ys.start, ys.stop, dtype=np.float64)[:, None]
            xx = np.arange(xs.start, xs.stop, dtype=np.float64)[None, :]
            near = np.hypot(xx - sp.det.x, yy - sp.det.y) < outer
            sub[(sub == -1) & near] = k
        else:
            sub[sub == -1] = k
    owner[owner == _RESERVED] = -1
    return owner


class _Footprint:
    """A candidate's footprint: a square of half-width ``halo_of(det)`` (halo mode) or the rect grown by ``grow``.

    ``core`` is always the rect grown by ``grow`` (one FWHM): blends are judged there.
    """

    def __init__(self, w: int, h: int, grow: int, halo_of: Optional[Callable[[Detection], float]]) -> None:
        self.w = w
        self.h = h
        self.grow = grow
        self.halo_of = halo_of

    def rect(self, det: Detection) -> Rect:
        """The footprint of ``det`` (calls ``halo_of`` in halo mode)."""
        if self.halo_of is not None:
            return square_rect(det.x, det.y, self.halo_of(det), self.w, self.h)
        return grown_rect(det.rect, self.grow, self.w, self.h)

    def core(self, det: Detection) -> Rect:
        """The rect grown by one FWHM: where blends are judged, not across the whole halo."""
        return grown_rect(det.rect, self.grow, self.w, self.h)


def _one_to_one(pairs: Sequence[MatchPair]) -> list[MatchPair]:
    """Each placed star and each detection in at most one pair: the first pair in order wins."""
    used_p: set[int] = set()
    used_d: set[int] = set()
    kept: list[MatchPair] = []
    for m in pairs:
        if m.pi in used_p or m.di in used_d:
            continue
        used_p.add(m.pi)
        used_d.add(m.di)
        kept.append(m)
    return kept


class _Matching(NamedTuple):
    """What the matched pairs are judged against: the detections, the placed stars, their grid and the catalogue neighbours."""

    dets: Sequence[Detection]
    placed: Sequence[Placed]
    grid: Grid
    neighbours: Optional[Iterable[dict]]


def _candidates(pairs: Sequence[MatchPair], match: _Matching, foot: _Footprint) -> tuple[list[SpriteMatch], int]:
    """The sprites of the matched pairs, and how many pairs are blends (they stay in the backdrop)."""
    sprites: list[SpriteMatch] = []
    blended = 0
    for m in pairs:
        det = match.dets[m.di]
        rect = foot.rect(det)
        core = foot.core(det)
        if (is_blend(match.dets, m.di, core, match.grid)
                or catalogue_blend(det, match.placed[m.pi], match.neighbours)):
            blended += 1
            continue
        sprites.append(SpriteMatch(placed=match.placed[m.pi], det=det, rect=rect))
    return sprites, blended


def assign_sprites(dets: Sequence[Detection], placed: Sequence[Placed], w: int, h: int, grow: float,
                   neighbours: Optional[Sequence[dict]], halo_of: Optional[Callable[[Detection], float]] = None
                   ) -> dict[str, Any]:
    """``Fly.assignSprites``: match placed stars to detections and pick the sprites.

    Each detection goes to the nearest placed star within its match radius, brightest
    star first. The footprint is the detection's rect grown by ``grow`` (one FWHM),
    or with ``halo_of`` a square of half-width ``halo_of(det)``. Blends (see
    :func:`is_blend`, :func:`catalogue_blend`) stay in the backdrop. With ``halo_of``,
    candidates inside a brighter star's reach are dropped (:func:`drop_inside_brighter`,
    :func:`one_star_one_sprite`). Returns ``{sprites, owner, blended, absorbed, unmatched}``.
    """
    # Sky.js 688 passes px = max(1, round(fwhm)): a whole number. Rect bounds must stay ints for the slices.
    if grow != int(grow):
        raise ValueError(f"grow must be a whole number of px, got {grow!r}")
    grow = int(grow)
    grid = detection_grid(dets)
    pairs = _one_to_one(match_pairs(dets, placed, grid))
    sprites, blended = _candidates(pairs, _Matching(dets, placed, grid, neighbours), _Footprint(w, h, grow, halo_of))
    absorbed = 0
    if halo_of is not None:
        kept = one_star_one_sprite(drop_inside_brighter(sprites, dets, grid, halo_of))
        absorbed = len(sprites) - len(kept)
        sprites = kept
    owner = sprite_ownership(dets, sprites, w, h, halo_of is not None)
    return {
        "sprites": sprites,
        "owner": owner,
        "blended": blended,
        "absorbed": absorbed,
        "unmatched": len(placed) - len(pairs),
    }


def within_reach(a: Detection, s: Detection) -> bool:
    """``Fly.withinReach``: is detection ``s`` inside bright star ``a``'s reach (halo, or along a spike)?

    Without a halo the reach is a circle of the rect's half-width. A circle as long as
    the longest spike would carry off every faint star around a bright one, so spikes
    count only within their own length and :data:`SPIKE_WIDTH` of their ray.
    """
    d = math.hypot(s.x - a.x, s.y - a.y)
    if a.halo is None:
        return d < (a.rect.x1 - a.rect.x0) / 2
    halo: Halo = a.halo
    r = halo.halo if halo.halo is not None else halo.radius
    if d < r:
        return True
    th = math.atan2(s.y - a.y, s.x - a.x)
    for sp in halo.spikes:
        dth = abs(math.atan2(math.sin(th - sp.angle), math.cos(th - sp.angle)))
        if d <= sp.reach + SPIKE_WIDTH and d * math.sin(min(dth, math.pi / 2)) <= SPIKE_WIDTH:
            return True
    return False


def add_missing_bright(dets: Sequence[Detection], neighbours: Optional[Iterable[dict]],
                       measure: Callable[[float, float], dict[str, float]]) -> list[Detection]:
    """``Fly.addMissingBright``: a detection at the Gaia position of each bright star the detector missed.

    Every Gaia star with G below :data:`BRIGHT_G` and no detection within
    :data:`BRIGHT_MISS_RADIUS` px gets one, ``from_gaia=True``. ``measure(x, y)``
    returns ``{"flux", "area"}`` from the image (see :func:`bright_measure`).
    Returns a new list; the input is not changed.
    """
    out = list(dets)
    for n in neighbours or ():
        src = n.get("source")
        if not (src and _num(src.get("G")) < BRIGHT_G):
            continue
        seen = any(abs(d.x - n["x"]) < BRIGHT_MISS_RADIUS and abs(d.y - n["y"]) < BRIGHT_MISS_RADIUS
                   and math.hypot(d.x - n["x"], d.y - n["y"]) < BRIGHT_MISS_RADIUS for d in out)
        if seen:
            continue
        m = measure(n["x"], n["y"])
        out.append(Detection(index=len(out), x=n["x"], y=n["y"], flux=m["flux"], nmax=0,
                             rect=detection_rect(n["x"], n["y"], m["area"]), from_gaia=True))
    return out


def _brighter_reaches(s: SpriteMatch, dets: Sequence[Detection], grid: Grid,
                      halo_of: Callable[[Detection], float]) -> bool:
    """Is candidate ``s`` inside the reach of a detection clearly brighter than it (more than twice its flux)?

    The detections are looked up in the grid around ``s``; one without a halo gets one from ``halo_of``.
    """
    box = Rect(math.floor(s.det.x - STILL_REACH_SEARCH), math.floor(s.det.y - STILL_REACH_SEARCH),
               math.ceil(s.det.x + STILL_REACH_SEARCH), math.ceil(s.det.y + STILL_REACH_SEARCH))
    for j in detections_in(grid, box):
        d = dets[j]
        if d is s.det or not (d.flux > 2 * js_or(s.det.flux, 0.0)):
            continue
        if d.halo is None:
            halo_of(d)
        if within_reach(d, s.det):
            return True
    return False


def drop_inside_brighter(sprites: Sequence[SpriteMatch], dets: Sequence[Detection], grid: Grid,
                         halo_of: Callable[[Detection], float]) -> list[SpriteMatch]:
    """``Fly.dropInsideBrighter``: drop candidates inside the reach of any clearly brighter star.

    Brighter means more than twice the candidate's flux, moving or not. A detection
    with no ``halo`` yet gets one from ``halo_of`` (the production modeller sets
    ``det.halo`` as a side effect; a pure rule leaves it None, as in the JS).
    """
    return [s for s in sprites if not _brighter_reaches(s, dets, grid, halo_of)]
