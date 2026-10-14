"""loomsiril.starlayer (Sky.sprites) against the JS oracle and on synthetic fields.

Oracle (tests/oracle/starlayer.json, recorded by tools/oracle/starlayer.js from the real JS): the
whole driver run on two stars layers, replayed from their stored inputs. Counts, stage texts, rects,
radii, centres, distances, masks and spike angles are exact; residual and sprite planes within 1e-6.

Synthetic checks (tests/synth.py): the required invariants (residual + sprites == stars layer, most
stars become sprites, each footprint holds its star, a faint star inside a bright star's reach is
absorbed, counts, stage order, runtime, the detections argument and the identity rule).
"""

from __future__ import annotations

import copy
import math
import time

import numpy as np
import pytest

import synth
from loomsiril import core, parallax, starlayer
from loomsiril import starmodel as sm
from loomsiril.core import Rect
from loomsiril.types import Detection, Placed

W, H = 200, 120
STAGES = ["Detecting stars", "Modelling stars", "Looking for stars the detector missed", "Deblending moving stars"]


def _neighbours(field) -> list[dict]:
    """Every catalogue source projected inside the image, as {source, x, y}."""
    out = []
    for s in field.catalogue:
        p = core.tan_project(s["ra"], s["dec"], field.wcs)
        if 0 <= p.x < W and 0 <= p.y < H:
            out.append({"source": s, "x": p.x, "y": p.y})
    return out


def _placed(field) -> list[Placed]:
    return parallax.place_stars(field.catalogue, lambda ra, dec: core.tan_project(ra, dec, field.wcs), W, H)


def _gaussian_planes(stars: list[tuple[float, float, float]], w: int, h: int, fwhm: float) -> list[np.ndarray]:
    """One channel: 0.05 background plus Gaussian stars given as (x, y, peak)."""
    sig = fwhm / 2.3548200450309493
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    st = np.full((h, w), 0.05)
    for x, y, peak in stars:
        st += peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sig * sig))
    return [np.clip(st, 0, 1).astype(np.float32)]


def _pair_field(faint_source_is_copy: bool = False):
    """A bright placed star B and a faint placed star F nine px to its right (inside B's reach, not its core)."""
    B = (50.0, 40.0)
    F = (59.0, 40.0)
    src_b = {"ra": 1.0, "dec": 2.0, "plx": 2.0, "pmra": 0.0, "pmdec": 0.0, "G": 8.0, "BP": 8.4, "RP": 7.5}
    src_f = {"ra": 1.0, "dec": 2.0, "plx": 4.0, "pmra": 0.0, "pmdec": 0.0, "G": 12.0, "BP": 12.4, "RP": 11.5}
    return B, F, src_b, src_f


# ---------------------------------------------------------------------------
# Oracle replay: the whole Sky.sprites run recorded from the JS.

@pytest.fixture(scope="module", params=["field", "spiked"])
def js_case(request, oracle):
    """One recorded run, replayed. Gaia sources are one list; each Placed and neighbour refers to the SAME
    dict object (catalogueBlend compares sources by identity, so a JSON copy would change the result)."""
    rec = oracle("starlayer")["cases"]
    case = rec if request.param == "field" else rec["spiked"]
    f = case["field"]
    w, h = f["w"], f["h"]
    sources = [dict(s) for s in f["sources"]]
    planes = [np.asarray(p, dtype=np.float32).reshape(h, w) for p in f["planes"]]
    dets = [Detection(index=d["index"], x=d["x"], y=d["y"], flux=d["flux"],
                      rect=Rect.from_dict(d["rect"]), nmax=d["nmax"]) for d in f["dets"]]
    placed = [Placed(source=sources[p["src"]], d=p["d"], x=p["x"], y=p["y"]) for p in f["placed"]]
    nb = [{"source": sources[n["src"]], "x": n["x"], "y": n["y"]} for n in f["neighbours"]]
    calls: list[list] = []
    layer = starlayer.sprites(planes, placed, f["fwhm"], nb,
                              stage=lambda text, a, b: calls.append([text, a, b]), detections=dets)
    return case, layer, calls


def test_oracle_counts_and_stages_exact(js_case):
    case, layer, calls = js_case
    o = case["out"]
    assert (layer.blended, layer.unmatched, layer.detected, len(layer.sprites)) == (
        o["blended"], o["unmatched"], o["detected"], o["nspr"])
    assert calls == case["calls"]


def test_oracle_rects_radius_centre_distance_exact(js_case):
    case, layer, _ = js_case
    assert len(layer.sprites) == len(case["out"]["sprites"])
    for s, e in zip(layer.sprites, case["out"]["sprites"]):
        assert s.rect == Rect.from_dict(e["rect"])
        assert s.radius == pytest.approx(e["radius"], rel=1e-12, abs=0)
        assert (s.centre.x, s.centre.y) == pytest.approx((e["centre"]["x"], e["centre"]["y"]), rel=1e-12, abs=0)
        assert s.d == pytest.approx(e["d"], rel=1e-12, abs=0)
        assert s.source.get("plx") == pytest.approx(e["plx"], rel=1e-12, abs=0)
        assert s.spike_angles == pytest.approx(e["spikeAngles"], rel=1e-12, abs=0)


def test_oracle_masks_exact(js_case):
    case, layer, _ = js_case
    for s, e in zip(layer.sprites, case["out"]["sprites"]):
        assert np.array_equal(s.mask, np.asarray(e["mask"], dtype=np.uint8).reshape(s.mask.shape))


def test_oracle_residual_and_sprite_planes_within_1e6(js_case):
    case, layer, _ = js_case
    o = case["out"]
    nc = case["field"]["nc"]
    for c in range(nc):
        ref = np.asarray(o["residual"][c]).reshape(layer.residual[c].shape)
        assert np.abs(layer.residual[c].astype(np.float64) - ref).max() < 1e-6
    for s, e in zip(layer.sprites, o["sprites"]):
        for c in range(nc):
            shape = s.pixels[c].shape
            assert np.abs(s.pixels[c].astype(np.float64) - np.asarray(e["pixels"][c]).reshape(shape)).max() < 1e-6
            assert np.abs(s.model_core[c].astype(np.float64) - np.asarray(e["modelCore"][c]).reshape(shape)).max() < 1e-6
            assert np.abs(s.model_spikes[c].astype(np.float64) - np.asarray(e["modelSpikes"][c]).reshape(shape)).max() < 1e-6


# ---------------------------------------------------------------------------
# Synthetic invariants (tests/synth.py fields).

@pytest.fixture(scope="module", params=[(1, 1), (2, 1), (1, 3), (2, 3)], ids=lambda p: f"seed{p[0]}-nc{p[1]}")
def case(request):
    seed, nc = request.param
    field = synth.make_field(w=W, h=H, n_stars=25, nc=nc, seed=seed)
    placed = _placed(field)
    nb = _neighbours(field)
    layer = starlayer.sprites(field.stars, placed, 2.5, nb)
    return field, placed, nb, layer


def test_residual_plus_sprites_equals_stars_layer(case):
    """Residual plus every sprite's pixels, placed at its rect, is the stars plane (1e-6)."""
    field, _, _, layer = case
    for c in range(field.nc):
        recon = layer.residual[c].astype(np.float64).copy()
        for s in layer.sprites:
            recon[s.rect.slices()] += s.pixels[c]
        assert np.abs(recon - field.stars[c].astype(np.float64)).max() < 1e-6


def test_most_injected_stars_become_sprites(case):
    """At least 70% of the truth stars have a sprite centred within 1.5 px."""
    field, _, _, layer = case
    found = sum(any(math.hypot(s.centre.x - t.x, s.centre.y - t.y) <= 1.5 for s in layer.sprites) for t in field.truth)
    assert found >= 0.7 * len(field.truth), f"{found} of {len(field.truth)} stars became sprites"


def test_each_footprint_contains_its_star_and_has_its_placed_distance(case):
    """A sprite's rect holds its star's truth position; d = 1000 / (corrected) parallax."""
    field, _, _, layer = case
    assert layer.sprites, "no sprites at all"
    for s in layer.sprites:
        near = [t for t in field.truth if math.hypot(s.centre.x - t.x, s.centre.y - t.y) <= 1.5]
        assert near, f"sprite at {s.centre} matches no truth star"
        assert any(s.rect.contains(t.x, t.y) for t in near), f"rect {s.rect} misses its star"
        assert s.placed.d == s.d
        assert s.d == pytest.approx(1000.0 / parallax.usable_parallax(s.source), rel=1e-12)


def test_counts_are_consistent_with_placed(case):
    """detected >= sprites; blended + unmatched + sprites never exceeds the placed count (absorbed stars are not counted)."""
    _, placed, _, layer = case
    assert layer.detected >= len(layer.sprites)
    assert layer.blended >= 0 and layer.unmatched >= 0
    assert layer.blended + layer.unmatched + len(layer.sprites) <= len(placed)


def test_faint_placed_star_in_bright_reach_is_absorbed():
    """A faint placed star nine px from a bright placed star: it is neither a sprite nor a blend, only the bright one is.

    Fails if the reach test (drop_inside_brighter / one_star_one_sprite) is skipped, or if assignment
    runs without the halo (the square footprints and the absorption step both come from the halo)."""
    B, F, src_b, src_f = _pair_field()
    planes = _gaussian_planes([(B[0], B[1], 1.0), (F[0], F[1], 0.4)], W, H, 2.5)
    placed = [Placed(source=src_b, d=500.0, x=B[0], y=B[1]), Placed(source=src_f, d=250.0, x=F[0], y=F[1])]
    nb = [{"source": src_b, "x": B[0], "y": B[1]}, {"source": src_f, "x": F[0], "y": F[1]}]
    layer = starlayer.sprites(planes, placed, 2.5, nb)
    assert len(layer.sprites) == 1
    assert math.hypot(layer.sprites[0].centre.x - B[0], layer.sprites[0].centre.y - B[1]) <= 1.5
    assert layer.sprites[0].source is src_b
    assert layer.blended == 0 and layer.unmatched == 0 and layer.detected == 2


def test_faint_placed_star_in_reach_of_an_unplaced_bright_detection_is_dropped():
    """The bright star is detected but has no Gaia record, so it can be no sprite: the faint placed star is
    still dropped, by the reach of the bright DETECTION (drop_inside_brighter looks at detections, not sprites)."""
    B, F, _, src_f = _pair_field()
    planes = _gaussian_planes([(B[0], B[1], 1.0), (F[0], F[1], 0.4)], W, H, 2.5)
    placed = [Placed(source=src_f, d=250.0, x=F[0], y=F[1])]
    nb = [{"source": src_f, "x": F[0], "y": F[1]}]
    layer = starlayer.sprites(planes, placed, 2.5, nb)
    assert layer.sprites == []
    assert layer.detected == 2


def test_catalogue_blend_is_by_source_identity():
    """Fly.catalogueBlend compares sources by identity: a copy of the placed star's source makes it a blend."""
    B, F, src_b, src_f = _pair_field()
    planes = _gaussian_planes([(B[0], B[1], 0.9)], W, H, 2.5)
    placed = [Placed(source=src_b, d=500.0, x=B[0], y=B[1])]
    same = [{"source": src_b, "x": B[0], "y": B[1]}]
    assert len(starlayer.sprites(planes, placed, 2.5, same).sprites) == 1
    copied = [{"source": dict(src_b), "x": B[0], "y": B[1]}]
    alone = starlayer.sprites(planes, placed, 2.5, copied)
    assert alone.sprites == [] and alone.blended == 1


def test_stage_callback_gets_js_texts_in_order():
    """The stage texts arrive in the JS order (each text may repeat for progress)."""
    field = synth.make_field(w=W, h=H, n_stars=25, nc=1, seed=1)
    calls: list[tuple[str, int, int]] = []
    starlayer.sprites(field.stars, _placed(field), 2.5, _neighbours(field),
                      stage=lambda text, done, total: calls.append((text, done, total)))
    order: list[str] = []
    for text, _, _ in calls:
        if not order or order[-1] != text:
            order.append(text)
    assert order == STAGES
    assert calls[0][0] == "Detecting stars"


def test_detections_argument_replaces_the_detector():
    """An explicit empty detection list means no detections; it does not fall back to the detector."""
    field = synth.make_field(w=W, h=H, n_stars=25, nc=1, seed=1)
    layer = starlayer.sprites(field.stars, [], 2.5, [], detections=[])
    assert layer.sprites == []
    assert layer.detected == 0
    assert layer.residual_mask.dtype == np.uint8
    assert layer.residual_mask.shape == (H, W)
    assert np.all(layer.residual_mask == 1)


def test_passed_detections_are_not_changed():
    """The driver sets index, model and halo on its own copies; the caller's detections keep their fields."""
    B, F, src_b, src_f = _pair_field()
    planes = _gaussian_planes([(B[0], B[1], 1.0), (F[0], F[1], 0.4)], W, H, 2.5)
    dets = [Detection(index=7, x=B[0], y=B[1], flux=1.0, rect=Rect(47, 37, 54, 44), nmax=0),
            Detection(index=9, x=F[0], y=F[1], flux=0.4, rect=Rect(56, 37, 63, 44), nmax=0)]
    before = copy.deepcopy([(d.index, d.model, d.halo) for d in dets])
    placed = [Placed(source=src_b, d=500.0, x=B[0], y=B[1]), Placed(source=src_f, d=250.0, x=F[0], y=F[1])]
    nb = [{"source": src_b, "x": B[0], "y": B[1]}, {"source": src_f, "x": F[0], "y": F[1]}]
    layer = starlayer.sprites(planes, placed, 2.5, nb, detections=dets)
    assert [(d.index, d.model, d.halo) for d in dets] == before
    assert len(layer.sprites) == 1


def test_runtime_one_run_under_five_seconds():
    """The 200 x 120 field runs in under 5 s (one run, no burner)."""
    field = synth.make_field(w=W, h=H, n_stars=25, nc=1, seed=1)
    placed = _placed(field)
    nb = _neighbours(field)
    t0 = time.perf_counter()
    starlayer.sprites(field.stars, placed, 2.5, nb)
    assert time.perf_counter() - t0 < 5.0
