"""loomsiril.assign against the JS oracle (tests/oracle/assign.json) and known answers (E3a).

Records are rebuilt from the recorded plain data (Rect.from_dict, Detection, Placed,
Halo); neighbour sources keep JS object identity through the recorded ``same`` index.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from loomsiril import assign
from loomsiril.assign import (
    BLEND_COMPANION_MAG,
    GRID,
    MATCH_RADIUS,
    MATCH_SEARCH,
    add_missing_bright,
    assign_sprites,
    catalogue_blend,
    detection_grid,
    detections_in,
    drop_inside_brighter,
    is_blend,
    match_pairs,
    match_radius,
    one_star_one_sprite,
    rects_intersect,
    sprite_ownership,
    within_reach,
)
from loomsiril.core import Rect, detection_rect
from loomsiril.types import Detection, Halo, Placed, SpikeReach, SpriteMatch

FLOAT_TOL = 1e-12


# ---- rebuilding records from the oracle's plain data ------------------------


def _halo(h: Any) -> Halo | None:
    if h is None:
        return None
    return Halo(radius=h["radius"], halo=h.get("halo"), outer=h["outer"],
                spikes=[SpikeReach(s["angle"], s["reach"]) for s in h.get("spikes") or []],
                pedestal=h.get("pedestal", 0.0), feather=h.get("feather", 0.0))


def _det(d: dict) -> Detection:
    # a JS record without flux (undefined) is NaN here: isFinite and the comparisons both see "no flux"
    return Detection(index=d["index"], x=d["x"], y=d["y"], flux=d.get("flux", math.nan), rect=Rect.from_dict(d["rect"]),
                     nmax=d["nmax"], halo=_halo(d.get("halo")), from_gaia=bool(d.get("fromGaia", False)))


def _placed_list(rows: list[dict]) -> list[Placed]:
    return [Placed(source=r.get("source"), d=r["d"], x=r["x"], y=r["y"]) for r in rows]


def _neighbours(rows: list[dict], placed: list[Placed]) -> list[dict]:
    """``{source, x, y}`` dicts; a ``same`` index makes the source the very object of that placed star."""
    out = []
    for r in rows:
        src = placed[r["same"]].source if r.get("same", -1) >= 0 else r.get("source")
        out.append({"source": src, "x": r["x"], "y": r["y"]})
    return out


def _sprites(rows: list[dict], dets: list[Detection], placed: list[Placed]) -> list[SpriteMatch]:
    return [SpriteMatch(placed=placed[s["pi"]], det=dets[s["di"]], rect=Rect.from_dict(s["rect"])) for s in rows]


def _idx(seq: list, obj: Any) -> int:
    """Position by identity (dataclass equality could match a copy)."""
    return next(i for i, o in enumerate(seq) if o is obj)


def _halo_rule(d: Detection) -> float:
    """The recorder's haloOf: 3 + sqrt(flux)."""
    return 3 + math.sqrt(d.flux)


def _measure_rule(x: float, y: float) -> dict[str, float]:
    """The recorder's measure: flux 10x + y, area 60 + floor(x)."""
    return {"flux": 10 * x + y, "area": 60 + math.floor(x)}


def _grid_from_json(rows: list) -> dict:
    return {(gx, gy): list(v) for gx, gy, v in rows}


def _det_fields(d: Detection) -> dict:
    return {"index": d.index, "x": d.x, "y": d.y, "nmax": d.nmax, "flux": d.flux,
            "rect": d.rect.as_dict(), "fromGaia": bool(d.from_gaia)}


def _same_flux(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or abs(a - b) <= FLOAT_TOL


# ---- oracle replay ----------------------------------------------------------


def test_constants_match_js(oracle):
    c = oracle("assign")["cases"]
    assert MATCH_RADIUS == c["MATCH"]["MATCH_RADIUS"]
    assert GRID == c["MATCH"]["GRID"]
    assert MATCH_SEARCH == c["MATCH"]["MATCH_SEARCH"]
    assert BLEND_COMPANION_MAG == c["BLEND_COMPANION_MAG"]
    assert assign.SPIKE_WIDTH == c["SPIKE_WIDTH"]
    assert assign.BRIGHT_G == c["BRIGHT"]["BRIGHT_G"]
    assert assign.BRIGHT_MISS_RADIUS == c["BRIGHT"]["BRIGHT_MISS_RADIUS"]
    assert assign.STILL_REACH_SEARCH == c["BRIGHT"]["STILL_REACH_SEARCH"]


def test_match_radius_matches_js(oracle):
    case = oracle("assign")["cases"]["matchRadius"]
    for a, want in zip(case["args"], case["out"]):
        det = Detection(index=0, x=0.0, y=0.0, flux=1.0, rect=Rect.from_dict(a["rect"]))
        assert abs(match_radius(det) - want) <= FLOAT_TOL


def test_rects_intersect_matches_js(oracle):
    case = oracle("assign")["cases"]["rectsIntersect"]
    for (a, b), want in zip(case["args"], case["out"]):
        assert rects_intersect(Rect.from_dict(a), Rect.from_dict(b)) is want


def test_detection_grid_matches_js(oracle):
    case = oracle("assign")["cases"]["detectionGrid"]
    dets = [_det(d) for d in case["args"]]
    assert detection_grid(dets) == _grid_from_json(case["out"])


def test_detections_in_matches_js(oracle):
    case = oracle("assign")["cases"]["detectionsIn"]
    dets = [_det(d) for d in case["args"]["dets"]]
    grid = detection_grid(dets)
    for r, want in zip(case["args"]["rects"], case["out"]):
        assert detections_in(grid, Rect.from_dict(r)) == want


def test_match_pairs_matches_js(oracle):
    case = oracle("assign")["cases"]["matchPairs"]
    dets = [_det(d) for d in case["args"]["dets"]]
    placed = _placed_list(case["args"]["placed"])
    got = match_pairs(dets, placed, detection_grid(dets))
    assert len(got) == len(case["out"])
    for m, want in zip(got, case["out"]):
        assert (m.pi, m.di) == (want["pi"], want["di"])
        assert _same_flux(m.r2, want["r2"]) and _same_flux(m.G, want["G"])


def test_catalogue_blend_matches_js(oracle):
    case = oracle("assign")["cases"]["catalogueBlend"]
    for a, want in zip(case["args"], case["out"]):
        placed = _placed_list([a["placed"]])
        nb = _neighbours(a["neighbours"], placed)
        assert catalogue_blend(_det(a["det"]), placed[0], nb) is want


def test_is_blend_matches_js(oracle):
    case = oracle("assign")["cases"]["isBlend"]
    dets = [_det(d) for d in case["args"]["dets"]]
    grid = detection_grid(dets)
    for (di, rect), want in zip(case["args"]["cases"], case["out"]):
        assert is_blend(dets, di, Rect.from_dict(rect), grid) is want


def test_one_star_one_sprite_matches_js(oracle):
    case = oracle("assign")["cases"]["oneStarOneSprite"]
    dets = [_det(d) for d in case["args"]["dets"]]
    placed = _placed_list(case["args"]["placed"])
    sprites = _sprites(case["args"]["sprites"], dets, placed)
    kept = one_star_one_sprite(sprites)
    assert [[_idx(placed, s.placed), _idx(dets, s.det)] for s in kept] == case["out"]


@pytest.mark.parametrize("halo_mode", [False, True])
def test_sprite_ownership_matches_js(oracle, halo_mode):
    key = "spriteOwnershipHalo" if halo_mode else "spriteOwnership"
    case = oracle("assign")["cases"][key]
    a = case["args"]
    dets = [_det(d) for d in a["dets"]]
    placed = _placed_list(a["placed"])
    sprites = _sprites(a["sprites"], dets, placed)
    owner = sprite_ownership(dets, sprites, a["w"], a["h"], halo_mode)
    assert owner.dtype == np.int32 and owner.shape == (a["h"], a["w"])
    assert owner.ravel().tolist() == case["out"]


def test_within_reach_matches_js(oracle):
    case = oracle("assign")["cases"]["withinReach"]
    for a, want in zip(case["args"], case["out"]):
        assert within_reach(_det(a["a"]), _det(a["s"])) is want


def test_drop_inside_brighter_matches_js(oracle):
    case = oracle("assign")["cases"]["dropInsideBrighter"]
    a = case["args"]
    dets = [_det(d) for d in a["dets"]]
    placed = _placed_list(a["placed"])
    sprites = _sprites(a["sprites"], dets, placed)
    kept = drop_inside_brighter(sprites, dets, detection_grid(dets), _halo_rule)
    assert [[_idx(placed, s.placed), _idx(dets, s.det)] for s in kept] == case["out"]


def test_drop_inside_brighter_asks_for_the_halo_once_and_keeps_the_far_one():
    bright = Detection(index=0, x=50.0, y=50.0, flux=100.0, rect=Rect(45, 45, 55, 55))
    near = Detection(index=1, x=52.0, y=50.0, flux=10.0, rect=Rect(51, 49, 53, 51))     # 2 px from a 5 px reach
    far = Detection(index=2, x=500.0, y=500.0, flux=10.0, rect=Rect(499, 499, 501, 501))  # out of every search box
    dets = [bright, near, far]
    sprites = [SpriteMatch(placed=Placed(source={}, d=0.0, x=d.x, y=d.y), det=d, rect=d.rect) for d in (near, far)]
    asked: list[int] = []

    def halo_of(d: Detection) -> float:
        asked.append(d.index)                                   # the brighter star's halo is asked for, once
        return 0.0

    kept = drop_inside_brighter(sprites, dets, detection_grid(dets), halo_of)
    assert [s.det for s in kept] == [far]
    assert asked == [0]


def test_add_missing_bright_matches_js(oracle):
    case = oracle("assign")["cases"]["addMissingBright"]
    a = case["args"]
    dets = [_det(d) for d in a["dets"]]
    nb = _neighbours(a["neighbours"], [])
    got = add_missing_bright(dets, nb, _measure_rule)
    assert len(got) == len(case["out"])
    for d, want in zip(got, case["out"]):
        assert d.index == want["index"] and d.nmax == want["nmax"] and d.from_gaia == want["fromGaia"]
        assert d.x == want["x"] and d.y == want["y"] and _same_flux(d.flux, want["flux"])
        assert d.rect.as_dict() == want["rect"]


@pytest.mark.parametrize("name", ["assignSelftest", "assignHaloRule", "assignTwoSources",
                                  "assignCompanion", "assignSpike", "assignGlow"])
def test_assign_sprites_matches_js(oracle, name):
    case = oracle("assign")["cases"][name]
    a, want = case["args"], case["out"]
    dets = [_det(d) for d in a["dets"]]
    placed = _placed_list(a["placed"])
    nb = _neighbours(a["neighbours"], placed)
    halo_of = _halo_rule if a["haloOf"] else None
    got = assign_sprites(dets, placed, a["w"], a["h"], a["grow"], nb, halo_of)
    assert [[_idx(placed, s.placed), _idx(dets, s.det), s.rect.as_dict()] for s in got["sprites"]] == want["sprites"]
    assert got["owner"].ravel().tolist() == want["owner"]
    assert (got["blended"], got["absorbed"], got["unmatched"]) == (want["blended"], want["absorbed"], want["unmatched"])


# ---- known answers and behaviour (the selftest literals, and the task's list) --------


def _box(x: float, y: float, r: int, nmax: int = 1, flux: float = math.nan) -> Detection:
    """Square core of half-width r around (x, y), as the selftest's det()."""
    return Detection(index=0, x=x, y=y, flux=flux, rect=Rect(int(x - r), int(y - r), int(x + r) + 1, int(y + r) + 1),
                     nmax=nmax)


def test_match_radius_selftest_literals():
    assert match_radius(Detection(0, 10, 10, 3.0, detection_rect(10, 10, 3))) == 1.5
    big = Detection(0, 50, 50, 120.0, detection_rect(50, 50, 220))
    assert match_radius(big) >= 3.5


def test_selftest_isolated_double_blend_and_footprint():
    dets = [_box(20, 20, 2), _box(50, 20, 2, nmax=2), _box(80, 20, 2), _box(86, 20, 1), _box(20, 60, 2), _box(30, 60, 2)]
    placed = [Placed({"x": 20.5}, 100, 20.5, 20.4), Placed({"x": 50}, 100, 50, 20), Placed({"x": 80}, 100, 80, 20),
              Placed({"x": 60}, 100, 60, 80), Placed({"x": 20}, 100, 20, 60), Placed({"x": 30}, 100, 30, 60)]
    a = assign_sprites(dets, placed, 100, 100, 3, [])
    members = [s.det for s in a["sprites"]]
    assert any(d is dets[0] for d in members)
    assert not any(d is dets[1] for d in members)
    assert not any(d is dets[2] for d in members)
    assert [len(a["sprites"]), a["blended"], a["unmatched"]] == [3, 2, 1]
    s0 = next(s for s in a["sprites"] if s.det is dets[0])
    assert s0.rect == Rect(15, 15, 26, 26)


def test_brighter_gaia_star_claims_a_shared_detection():
    det = _box(20, 20, 2, flux=50.0)
    faint = Placed({"G": 14.0}, 100, 20.4, 20.0)
    bright = Placed({"G": 9.0}, 100, 19.6, 20.0)
    a = assign_sprites([det], [faint, bright], 60, 60, 3, [])
    assert len(a["sprites"]) == 1 and a["sprites"][0].placed is bright
    assert a["unmatched"] == 1


def test_placed_star_pairs_only_within_its_match_radius():
    det = _box(20, 20, 2)
    grid = detection_grid([det])
    near = Placed({"G": 10.0}, 100, 21.0, 20.0)   # 1 px: inside 1.5 px
    far = Placed({"G": 10.0}, 100, 23.0, 20.0)    # 3 px: outside
    pairs = match_pairs([det], [near, far], grid)
    assert [(p.pi, p.di) for p in pairs] == [(0, 0)]


def test_blends_are_counted():
    twin = [_box(30, 30, 2, nmax=2)]
    placed = [Placed({"G": 10.0}, 100, 30.0, 30.0)]
    a = assign_sprites(twin, placed, 60, 60, 3, [])
    assert a["blended"] == 1 and a["sprites"] == []


def test_owner_map_gives_each_sprite_its_core_and_reserves_blend_cores():
    left = _box(15, 15, 2, flux=40.0)
    right = _box(40, 40, 2, flux=40.0)
    blend = _box(50, 10, 2, nmax=3, flux=5.0)
    placed = [Placed({"G": 10.0}, 100, 15.0, 15.0), Placed({"G": 10.0}, 100, 40.0, 40.0),
              Placed({"G": 12.0}, 100, 50.0, 10.0)]
    a = assign_sprites([left, right, blend], placed, 60, 60, 3, [])
    owner = a["owner"]
    ids = [s.det for s in a["sprites"]]
    k_left, k_right = ids.index(left), ids.index(right)
    assert owner[15, 15] == k_left and owner[40, 40] == k_right
    assert owner[10, 50] == -1          # the blend's core stays with the residual
    assert owner[0, 0] == -1             # nothing there
    assert a["blended"] == 1


def test_add_missing_bright_adds_exactly_the_missed_bright_stars():
    dets = [Detection(0, 20.0, 20.0, 5.0, detection_rect(20, 20, 5))]
    nb = [{"source": {"G": 5.6}, "x": 60.4, "y": 40.2},     # bright, nothing near: added
          {"source": {"G": 14.0}, "x": 90.0, "y": 90.0},    # faint: not added
          {"source": {"G": 8.0}, "x": 20.5, "y": 20.3},     # bright but detected: not added
          {"source": {"G": 9.0}, "x": 60.4, "y": 42.0}]     # bright, near the one just added: not added
    out = add_missing_bright(dets, nb, lambda x, y: {"flux": 900.0, "area": 400})
    assert len(out) == 2
    assert out[1].from_gaia and (out[1].x, out[1].y) == (60.4, 40.2) and out[1].flux == 900.0
    assert len(dets) == 1  # input list is not changed


def test_within_reach_along_a_spike_versus_beside_it():
    halo = Halo(radius=80, halo=15, outer=92, spikes=[SpikeReach(0.0, 80.0)], feather=12.0)
    b = Detection(0, 100.0, 100.0, 120.0, Rect(95, 95, 106, 106), halo=halo)
    on_spike = Detection(0, 150.0, 100.0, 2.0, Rect(147, 97, 154, 104))
    beside = Detection(0, 100.0, 150.0, 2.0, Rect(97, 147, 104, 154))
    beyond = Detection(0, 300.0, 100.0, 2.0, Rect(297, 97, 304, 104))
    assert within_reach(b, on_spike) is True
    assert within_reach(b, beside) is False
    assert within_reach(b, beyond) is False  # on the spike's ray but past its reach


def test_one_star_one_sprite_drops_a_star_on_a_brighter_stars_spike():
    halo = Halo(radius=20.0, halo=None, outer=22.0, spikes=[SpikeReach(0.0, 40.0)])
    bright = Detection(0, 50.0, 50.0, 120.0, Rect(40, 40, 61, 61), halo=halo)
    on = Detection(0, 70.0, 50.0, 2.0, Rect(68, 48, 73, 53))
    apart = Detection(0, 90.0, 90.0, 3.0, Rect(88, 88, 93, 93))
    pl = Placed({"G": 9.0}, 400, 50.0, 50.0)
    sprites = [SpriteMatch(pl, bright, bright.rect), SpriteMatch(pl, on, on.rect), SpriteMatch(pl, apart, apart.rect)]
    kept = one_star_one_sprite(sprites)
    assert [s.det for s in kept] == [bright, apart]


def test_assign_sprites_on_a_synthetic_field(synth_field):
    from loomsiril.detect import detect_stars

    f = synth_field(seed=2, n_stars=20)
    dets = detect_stars(f.image)
    placed = [Placed(source=c, d=1000.0 / c["plx"] if c["plx"] else 1000.0, x=t.x, y=t.y)
              for c, t in zip(f.catalogue[: len(f.truth)], f.truth)]
    h, w = f.image[0].shape
    a = assign_sprites(dets, placed, w, h, 2.0, [])
    assert len(a["sprites"]) >= 15
    assert a["owner"].shape == (h, w)
    for k, s in enumerate(a["sprites"]):
        assert s.rect.contains(s.det.x, s.det.y)
        assert a["owner"][int(s.det.y), int(s.det.x)] == k


# ---- selftest literals with the JS halo stubs (they set det.halo as a side effect) --------


def _stub_halo(radius_of, pad: float, with_halo_field: bool = True):
    """A halo_of that sets ``d.halo`` like the selftest's stub and returns the footprint radius."""

    def halo_of(d: Detection) -> float:
        r = radius_of(d)
        d.halo = Halo(radius=r, halo=r if with_halo_field else None, outer=r + pad, pedestal=0.0, feather=2.0)
        return r + pad

    return halo_of


def test_selftest_halo_sized_footprints_fainter_inside_halo_stay_behind():
    # selftest.js 15068-15085: a fainter star inside a bright star's halo moves with it; a comparable one
    # in the halo, away from the core, is not a blend; a brighter star owns contested pixels
    def det(x: float, y: float, flux: float) -> Detection:
        return Detection(0, x, y, flux, detection_rect(x, y, 5), nmax=0)

    bright, faint = det(30, 30, 10), det(36, 30, 1)
    twin, twin2 = det(60, 30, 10), det(66, 30, 6)
    dets = [bright, faint, twin, twin2]
    placed = [Placed({}, 100, 30, 30), Placed({}, 100, 60, 30)]
    halo = _stub_halo(lambda d: 10.0, 0.0, with_halo_field=False)
    a = assign_sprites(dets, placed, 100, 100, 3, [], halo)
    ids = [s.det for s in a["sprites"]]
    kb = _idx(ids, bright)
    assert any(d is bright for d in ids)
    assert a["owner"][30, 36] == kb and a["owner"][30, 39] == kb
    assert a["owner"][30, 66] == _idx(ids, twin)
    close = assign_sprites([det(30, 30, 10), det(33, 30, 12)], [Placed({}, 100, 30, 30)], 100, 100, 3, [], halo)
    assert close["blended"] == 1
    lead = assign_sprites([det(30, 30, 10), det(33, 30, 8)], [Placed({}, 100, 30, 30)], 100, 100, 3, [], halo)
    assert len(lead["sprites"]) == 1


def test_selftest_saturated_star_with_companion_in_its_glow_moves():
    # selftest.js 15106-15115
    def det(x: float, y: float, flux: float, area: int) -> Detection:
        return Detection(0, x, y, flux, detection_rect(x, y, area), nmax=0)

    big = [det(60, 60, 168, 1400), det(72, 65, 161, 1400)]
    gA, gB = {"G": 5.6}, {"G": 8.0}
    placed = [Placed(gA, 700, 60, 60), Placed(gB, 900, 72, 65)]
    nb = [{"source": gA, "x": 60, "y": 60}, {"source": gB, "x": 72, "y": 65}]
    halo = _stub_halo(lambda d: 40.0 if d is big[0] else 21.0, 4.0)
    bp = assign_sprites(big, placed, 140, 140, 3, nb, halo)
    assert len(bp["sprites"]) == 1 and bp["sprites"][0].det is big[0]


def test_selftest_star_on_a_bright_stars_spike_is_not_a_sprite():
    # selftest.js 16060-16076
    def det(x: float, y: float, flux: float, area: int) -> Detection:
        return Detection(0, x, y, flux, detection_rect(x, y, area), nmax=0)

    bright, on_spike, apart = det(50, 50, 120, 150), det(64, 50, 2, 5), det(90, 90, 3, 5)
    placed = [Placed({"G": 9}, 400, 50, 50), Placed({"G": 15}, 150, 64, 50), Placed({"G": 14}, 700, 90, 90)]
    halo = _stub_halo(lambda d: 20.0 if d is bright else 3.0, 2.0, with_halo_field=False)
    a = assign_sprites([bright, on_spike, apart], placed, 120, 120, 3, [], halo)
    ids = [s.det for s in a["sprites"]]
    assert not any(d is on_spike for d in ids)
    assert any(d is bright for d in ids) and any(d is apart for d in ids)
    assert a["owner"][50, 64] == _idx(ids, bright)
    assert a["absorbed"] == 1
