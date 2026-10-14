"""loomsiril.starmodel against the JS oracle (tests/oracle/starmodel.json) and the selftest literals.

Every case is replayed from the inputs stored in the JSON; nothing is rebuilt here.
Tolerances: 1e-12 for float64 rules, 1e-6 for values computed from float32 buffers.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from loomsiril import jsmath
from loomsiril import starmodel as sm
from loomsiril.core import Rect, js_round
from loomsiril.types import Detection, Halo, SpikeReach, Spike, SpriteMatch, StarModel

F64 = 1e-12
F32 = 1e-6


@pytest.fixture(scope="module")
def cases(oracle):
    return oracle("starmodel")["cases"]


def _close(a, b, tol: float) -> bool:
    """``a`` matches the JS value ``b`` (None and NaN compare as themselves)."""
    if b is None:
        return a is None
    if isinstance(b, float) and math.isnan(b):
        return math.isnan(a)
    return abs(a - b) <= tol


def _plane(flat, w: int, h: int, dtype=np.float32) -> np.ndarray:
    return np.asarray(flat, dtype=dtype).reshape(h, w)


def _det(rec: dict, index: int = 0) -> Detection:
    return Detection(index=index, x=rec["x"], y=rec["y"], flux=rec["flux"],
                     rect=Rect.from_dict(rec["rect"]), from_gaia=bool(rec.get("fromGaia", False)))


def _stellar(x: float, y: float, core: float = 0.0) -> StarModel:
    return StarModel(x=x, y=y, core=core, prof=np.zeros(1), rmax=0.0, spikes=[], col=[], support=0.0)


# ---------------------------------------------------------------- constants


def test_constants_match_js(cases):
    js = cases["constants"][0]["out"]
    assert sm.FWHM_DEFAULT == js["FWHM_DEFAULT"]
    assert sm.HALO_MAX == js["HALO_MAX"]
    assert sm.SPIKE_MAX == js["SPIKE_MAX"]
    assert sm.SPIKE_WINDOW == js["SPIKE_WINDOW"]
    assert sm.SPIKE_FIT == js["SPIKE_FIT"]
    assert sm.SETTLE_WINDOW == js["SETTLE_WINDOW"]
    assert sm.DEBLEND_CAP == js["DEBLEND_CAP"]


# ---------------------------------------------------------------- spikes


def test_spike_angles_oracle(cases):
    for case in cases["spikeAngles"]:
        prof, noise = case["args"]
        assert sm.spike_angles(prof, noise) == case["out"]


def test_spike_angles_selftest_literals(cases):
    prof, noise = cases["spikeAngles"][0]["args"]
    assert sm.spike_angles(prof, noise) == [45, 135, 225, 315]
    flat, noise = cases["spikeAngles"][1]["args"]
    assert sm.spike_angles(flat, noise) == []                       # an image without spikes has none
    one_sided, noise = cases["spikeAngles"][2]["args"]
    assert sm.spike_angles(one_sided, noise) == []                  # a peak with no opposite is a neighbour


def test_lateral_sigma_oracle(cases):
    for case in cases["lateralSigma"]:
        p, step = case["args"]
        got = sm.lateral_sigma(p, step)
        if case["out"] is None:
            assert got is None
        else:
            assert _close(got, case["out"], F64)


def test_lateral_sigma_widths_close_to_truth(cases):
    # the selftest sigmas 0.7, 1.6, 2.5 come back within 12% (+0.05 px) of themselves
    for case, sig in zip(cases["lateralSigma"][:3], (0.7, 1.6, 2.5)):
        assert abs(sm.lateral_sigma(*case["args"]) - sig) < 0.12 * sig + 0.05


# ---------------------------------------------------------------- peaks and fragments


def test_local_peak_oracle(cases):
    for case in cases["localPeak"]:
        buf, w, h, x, y, rad = case["args"]
        got = sm.local_peak(sm.reader(_plane(buf, w, h)), x, y, rad)
        assert got is case["out"]


def test_is_fragment_oracle(cases):
    for case in cases["isFragment"]:
        d, dets, buf, w, h, fwhm = case["args"]
        dobj = _det(d)
        others = [_det(r, i) for i, r in enumerate(dets)]
        got = sm.is_fragment(dobj, others, sm.reader(_plane(buf, w, h)), fwhm)
        assert got is case["out"]


def test_is_fragment_expected_verdicts(cases):
    verdicts = [c["out"] for c in cases["isFragment"]]
    assert verdicts == [False, True, False, False, True, False]


def test_local_peak_ties_are_peaks():
    flat = np.ones((9, 9), dtype=np.float32)
    assert sm.local_peak(sm.reader(flat), 4, 4, 2) is True    # ties are peaks


# ---------------------------------------------------------------- cores and borrowed spikes


def test_attach_to_cores_oracle(cases):
    for case in cases["attachToCores"]:
        sprites_rec, extras_rec = case["args"]
        extras = [_stellar(e["x"], e["y"]) for e in extras_rec]
        sprites = [SpriteMatch(placed=None, det=Detection(index=k, x=m["x"], y=m["y"], flux=1.0,
                                                          rect=Rect(0, 0, 1, 1),
                                                          model=_stellar(m["x"], m["y"], m["core"])),
                               rect=None)
                   for k, m in enumerate(sprites_rec)]
        kept = sm.attach_to_cores(sprites, extras)
        assert [next(i for i, e in enumerate(extras) if e is k) for k in kept] == case["out"]["kept"]
        parts = []
        for s in sprites:
            p = s.det.model.parts
            parts.append(None if p is None else [next(i for i, e in enumerate(extras) if e is q) for q in p])
        assert parts == case["out"]["parts"]


def test_attach_to_cores_selftest_pair():
    model = _stellar(100, 100, 6)
    sprites = [SpriteMatch(placed=None, det=Detection(0, 100, 100, 1.0, Rect(0, 0, 1, 1), model=model), rect=None)]
    inside, outside = _stellar(104.5, 100), _stellar(112, 100)
    stay = sm.attach_to_cores(sprites, [inside, outside])
    assert model.parts is not None and model.parts[0] is inside
    assert len(stay) == 1 and stay[0] is outside


def test_drop_borrowed_spikes_oracle(cases):
    for case in cases["dropBorrowedSpikes"]:
        dets = []
        for k, r in enumerate(case["args"][0]):
            m = r["model"]
            model = StarModel(x=m["x"], y=m["y"], core=0.0, prof=np.zeros(1), rmax=m["rmax"], spikes=[
                Spike(angle=s["angle"], start=s["start"], amp=np.zeros(0), reach=s["reach"]) for s in m["spikes"]],
                col=[], support=m["support"], spike_sigma=m.get("spikeSigma"))
            halo = None
            if r.get("halo") is not None:
                h = r["halo"]
                halo = Halo(radius=h["radius"], halo=None, outer=h["outer"],
                            spikes=[SpikeReach(angle=s["angle"], reach=s["reach"]) for s in h["spikes"]])
            dets.append(Detection(index=k, x=m["x"], y=m["y"], flux=r["flux"], rect=Rect(0, 0, 1, 1),
                                  model=model, halo=halo))
        sm.drop_borrowed_spikes(dets)
        for d, want in zip(dets, case["out"]):
            assert [{"angle": s.angle, "reach": s.reach} for s in d.model.spikes] == [
                {"angle": s["angle"], "reach": s["reach"]} for s in want["spikes"]]
            assert _close(d.model.support, want["support"], F64)
            if want["halo"] is None:
                assert d.halo is None
            else:
                assert _close(d.halo.radius, want["halo"]["radius"], F64)
                assert _close(d.halo.outer, want["halo"]["outer"], F64)
                assert [(s.angle, s.reach) for s in d.halo.spikes] == [
                    (s["angle"], s["reach"]) for s in want["halo"]["spikes"]]


def _spiked(x, y, flux, spikes, support, rmax=5.0, spike_sigma=1.0, halo=None):
    model = StarModel(x=x, y=y, core=0.0, prof=np.zeros(1), rmax=rmax, col=[], support=support,
                      spikes=[Spike(angle=a, start=0.0, amp=np.zeros(0), reach=reach) for a, reach in spikes],
                      spike_sigma=spike_sigma)
    return Detection(index=0, x=x, y=y, flux=flux, rect=Rect(0, 0, 1, 1), model=model, halo=halo)


def test_drop_borrowed_spikes_no_spiky_star_changes_nothing():
    plain = _spiked(10, 0, 1.0, [], 7.0)
    bright = _spiked(0, 0, 9.0, [(0.0, 50.0)], 60.0)
    sm.drop_borrowed_spikes([bright, plain])                 # the faint one has no spikes: nothing to drop
    assert [(a.angle, a.reach) for a in bright.model.spikes] == [(0.0, 50.0)]
    assert plain.model.spikes == [] and plain.model.support == 7.0


def test_drop_borrowed_spikes_keeps_a_spike_that_is_not_on_a_brighter_one():
    bright = _spiked(0, 0, 9.0, [(0.0, 50.0)], 60.0)
    faint = _spiked(0, 10, 1.0, [(0.0, 20.0)], 25.0)         # 10 px off the bright spike's axis: not borrowed
    halo = Halo(radius=25.0, halo=None, outer=25.0, spikes=[SpikeReach(angle=0.0, reach=20.0)])
    faint.halo = halo
    sm.drop_borrowed_spikes([bright, faint])
    assert [(a.angle, a.reach) for a in faint.model.spikes] == [(0.0, 20.0)]
    assert faint.model.support == 25.0
    assert (halo.radius, halo.outer) == (25.0, 25.0)


def test_drop_borrowed_spikes_all_borrowed_leaves_no_spike_and_the_rmax_support():
    bright = _spiked(0, 0, 9.0, [(0.0, 50.0)], 60.0)
    faint = _spiked(10, 0.5, 1.0, [(0.0, 20.0)], 25.0, rmax=5.0)   # on the bright spike, parallel: borrowed
    halo = Halo(radius=25.0, halo=None, outer=25.0, spikes=[SpikeReach(angle=0.0, reach=20.0)])
    faint.halo = halo
    sm.drop_borrowed_spikes([bright, faint])
    assert faint.model.spikes == []
    assert faint.model.support == 6.0                       # max(rmax 5, reach 0) + 1
    assert (halo.radius, halo.outer, halo.spikes) == (6.0, 6.0, [])


def test_drop_borrowed_spikes_without_a_halo_only_changes_the_model():
    bright = _spiked(0, 0, 9.0, [(0.0, 50.0)], 60.0)
    faint = _spiked(10, 0.5, 1.0, [(0.0, 20.0)], 25.0)
    sm.drop_borrowed_spikes([bright, faint])
    assert faint.halo is None and faint.model.spikes == [] and faint.model.support == 6.0


def test_drop_borrowed_spikes_selftest_case(cases):
    # the star on the bright star's spike loses the parallel one; the perpendicular spike stays
    case = cases["dropBorrowedSpikes"][0]
    assert [s["angle"] for s in case["out"][0]["spikes"]] == [pytest.approx(math.pi / 2)]
    assert case["out"][0]["support"] == 13
    assert len(case["out"][1]["spikes"]) == 2
    assert len(case["out"][2]["spikes"]) == 1


# ---------------------------------------------------------------- spike fits


def _fit_out_close(got: dict, want: dict) -> None:
    assert _close(got["scale"], want["scale"], F64)
    assert got["reach"] == want["reach"]
    assert len(got["amp"]) == len(want["amp"])
    assert np.allclose(got["amp"], np.asarray(want["amp"], dtype=np.float64), rtol=0, atol=F64)


def test_fit_spike_oracle(cases):
    for case in cases["fitSpike"]:
        e, T, start, sigma = case["args"]
        _fit_out_close(sm.fit_spike(e, T, start, sigma), case["out"])


def test_fit_spike_selftest_literals(cases):
    e, T, start, sigma = cases["fitSpike"][0]["args"]
    f = sm.fit_spike(e, T, start, sigma)
    assert abs(f["scale"] - 0.2) < 0.02                              # the scale comes from the inner part
    assert 58 <= f["reach"] <= 70                                    # borrowed light further out does not lengthen it
    assert abs(f["amp"][20] - 0.2 * T[30]) < 0.02 * T[30] + 1e-4    # the amplitude follows the falloff
    none, T, start, sigma = cases["fitSpike"][1]["args"]
    assert sm.fit_spike(none, T, start, sigma)["reach"] == -1         # noise is no spike


def test_fit_spike_pair_oracle(cases):
    for case in cases["fitSpikePair"]:
        a, b, T, start, sigma = case["args"]
        _fit_out_close(sm.fit_spike_pair(a, b, T, start, sigma), case["out"])


def test_fit_spike_pair_takes_stronger_side(cases):
    good, masked, T, start, sigma = cases["fitSpikePair"][0]["args"]
    pair = sm.fit_spike_pair(good, masked, T, start, sigma)
    alone = sm.fit_spike(good, T, start, sigma)
    assert abs(pair["scale"] - alone["scale"]) < F64 and pair["reach"] == alone["reach"]


# ---------------------------------------------------------------- profiles and the deblend


def test_local_profile_oracle(cases):
    for case in cases["localProfile"]:
        raw, min_r, noise = case["args"]
        got = sm.local_profile(raw, min_r, noise)
        want = case["out"]
        assert got["rmax"] == want["rmax"] and got["settled"] is want["settled"]
        assert _close(got["sky"], want["sky"], F64)
        assert len(got["prof"]) == len(want["prof"])
        assert np.allclose(got["prof"], np.asarray(want["prof"]), rtol=0, atol=F64)


def test_local_profile_selftest_literals(cases):
    raw, min_r, noise = cases["localProfile"][0]["args"]
    p = sm.local_profile(raw, min_r, noise)
    assert p["rmax"] < 15                                            # the profile ends where it stops falling
    assert abs(p["sky"] - 0.003) < 0.0005                            # and stands on that local sky
    assert p["prof"][p["rmax"] - 1] < 0.001                          # so the model falls to zero at its edge


def test_deblend_oracle(cases):
    for case in cases["deblend"]:
        assert _close(sm.deblend(*case["args"]), case["out"], F64)


def test_deblend_selftest_rules():
    assert abs(sm.deblend(0.6, 0.8, 1.0, 0.004) - 0.6 / 0.8 * 1.0) < 1e-9        # bright: the model's share of the data
    assert abs(sm.deblend(0.001, 0.001, 0.0045, 0.004) - 0.001) < 1e-9          # faded: only the model
    shares = sm.deblend(0.3, 0.5, 0.7, 0.004) + sm.deblend(0.2, 0.5, 0.7, 0.004)
    assert abs(shares - 0.7) < 1e-9                                              # the shares add up to the light
    assert sm.deblend(0, 0, 0.5, 0.004) == 0                                     # no model, no light
    assert sm.deblend(0.01, 0.01, 1.0, 0.004) <= 0.01 * sm.DEBLEND_CAP + 1e-12   # never far beyond the model


# ---------------------------------------------------------------- image helpers


def test_intensity_oracle(cases):
    for case in cases["intensity"]:
        planes, w, h = case["args"]
        got = sm.intensity([_plane(p, w, h) for p in planes])
        assert got.dtype == np.float32 and got.shape == (h, w)
        assert np.allclose(got.ravel(), np.asarray(case["out"], dtype=np.float32), rtol=0, atol=F32)


def test_noise_sigma_oracle(cases):
    for case in cases["noiseSigma"]:
        assert _close(sm.noise_sigma(np.asarray(case["args"][0], dtype=np.float32)), case["out"], F32)


def test_noise_sigma_constant_is_zero(cases):
    assert sm.noise_sigma(np.full(500, 0.2, dtype=np.float32)) == 0.0


def test_other_star_oracle(cases):
    for case in cases["otherStar"]:
        lab, w, y, x, self_ = case["args"]
        labels = None if lab is None else _plane(lab, w, len(lab) // w, dtype=np.int32)
        assert sm.other_star(labels, (y, x), self_) is case["out"]


def test_ring_start_oracle(cases):
    for case in cases["ringStart"]:
        lo, dy = case["args"]
        assert sm.ring_start(lo, dy) == case["out"]


def test_ring_stats_oracle(cases):
    for case in cases["ringStats"]:
        buf, w, h, cx, cy, r, labels, self_ = case["args"]
        lab = None if labels is None else _plane(labels, w, h, dtype=np.int32)
        got = sm.ring_stats(_plane(buf, w, h), cx, cy, r, lab, self_)
        want = case["out"]
        if want is None:
            assert got is None
        else:
            assert got is not None
            assert _close(got["med"], want["med"], F32) and _close(got["max"], want["max"], F32)


def test_ring_stats_skips_other_stars_and_is_none_off_image():
    buf = np.ones((41, 41), dtype=np.float32)
    buf[17:24, 25:32] = 100.0                                      # another star's core, label 3
    labels = np.full((41, 41), -1, dtype=np.int32)
    labels[17:24, 25:32] = 3
    labels[20, 20] = 0                                             # our own pixel
    got = sm.ring_stats(buf, 20, 20, 6, labels, 0)
    assert got["max"] == 1.0 and got["med"] == 1.0                 # the bright pixels are other stars'
    assert sm.ring_stats(buf, 20, 20, 6, None, 0)["max"] == 100.0   # without labels they count
    assert sm.ring_stats(buf, -30, 20, 5, None, 0) is None
    assert sm.ring_stats(buf, 20, 20, 60, None, 0) is None          # a ring around the image


def test_ring_stats_fast_on_200x200():
    rng = np.random.default_rng(5)
    buf = rng.random((200, 200), dtype=np.float64).astype(np.float32)
    start = time.perf_counter()
    for r in range(1, 61):
        sm.ring_stats(buf, 100, 100, r, None, 0)
    assert time.perf_counter() - start < 1.0


def test_core_labels_oracle(cases):
    for case in cases["coreLabels"]:
        dets_rec, w, h, fwhm = case["args"]
        dets = [_det(r, k) for k, r in enumerate(dets_rec)]
        got = sm.core_labels(dets, w, h, fwhm)
        assert got.shape == (h, w) and got.dtype == np.int32
        assert got.ravel().tolist() == case["out"]


def test_core_labels_first_detection_wins():
    a = Detection(0, 10, 10, 50.0, Rect(7, 7, 14, 14))
    b = Detection(1, 12, 10, 5.0, Rect(11, 9, 14, 12))
    labels = sm.core_labels([a, b], 30, 24, 3)
    assert labels[10, 12] == 0                                    # in both cores: the first one
    assert labels[10, 20] == -1                                   # in neither


def test_reader_oracle(cases):
    for case in cases["reader"]:
        buf, w, h, x, y = case["args"]
        assert sm.reader(_plane(buf, w, h))(x, y) == case["out"]


def test_sparse_median_oracle(cases):
    for case in cases["sparseMedian"]:
        assert _close(sm.sparse_median(np.asarray(case["args"][0], dtype=np.float32)), case["out"], F32)


def test_bilinear_oracle(cases):
    for case in cases["bilinear"]:
        buf, w, h, x, y = case["args"]
        assert _close(sm.bilinear(_plane(buf, w, h), x, y), case["out"], F32)


def test_bilinear_edges_keep_only_the_in_image_neighbours():
    buf = np.arange(9, dtype=np.float32).reshape(3, 3)            # rows 0-2, values 0..8
    assert sm.bilinear(buf, -0.5, 1.0) == 3.0                      # only x=0 is in: buf[1, 0]
    assert sm.bilinear(buf, -2.5, 0.0) == 0.0                      # no neighbour in the image
    assert sm.bilinear(buf, 2.0, 2.0) == 8.0                       # on the last pixel: no weight spills out
    assert sm.bilinear(buf, math.nan, 1.0) == 0.0                  # a non-finite coordinate gives 0


def test_smooth_profile_oracle(cases):
    for case in cases["smoothProfile"]:
        prof, from_ = case["args"]
        got = sm.smooth_profile(prof, from_)
        assert np.allclose(got, np.asarray(case["out"]), rtol=0, atol=F64)


def test_smooth_profile_is_non_increasing_from_index_one(cases):
    prof, _ = cases["smoothProfile"][0]["args"]
    got = sm.smooth_profile(prof, 0)
    assert np.all(np.diff(got[1:]) <= 0)


# ---- E3e: the fragment filter is subquadratic and keeps the exact predicate ------------------
#
# _old_is_fragment is the JS-shaped scan as it stood before E3e. fragment_filter must keep exactly the
# detections that filter kept, in the same order, and must follow the data when a list is edited in place.


def _old_is_fragment(d, dets, value_at, px):
    if d.from_gaia:
        return False
    if not sm.local_peak(value_at, d.x, d.y, jsmath.js_max(2, px)):
        return True
    X = int(js_round(d.x))
    Y = int(js_round(d.y))
    v0 = -float("inf")
    for j in (-1, 0, 1):
        for i in (-1, 0, 1):
            v0 = jsmath.js_max(v0, value_at(X + i, Y + j))
    for b in dets:
        if b is d or not (b.flux > d.flux):
            continue
        core = 0.5 * (b.rect.x1 - b.rect.x0) + px
        if math.hypot(b.x - d.x, b.y - d.y) <= core and value_at(int(js_round(b.x)), int(js_round(b.y))) >= v0:
            return True
    return False


def _old_filter(dets, value_at, px):
    """The old starlayer filter. A NaN detection position raises (js_round) in both filters: returns None then."""
    try:
        return [d for d in dets if not _old_is_fragment(d, dets, value_at, px)]
    except ValueError:
        return None


def _random_dets(rng, w, h, n):
    dets = []
    for k in range(n):
        if rng.random() < 0.03:  # NaN position: never a brighter neighbour; as a candidate it raises (both filters)
            x, y = float("nan"), float(rng.randint(0, h - 1))
        elif rng.random() < 0.5:  # integer grid: coincident positions are common
            x, y = float(rng.randint(-2, w + 1)), float(rng.randint(-2, h + 1))
        else:
            x, y = rng.uniform(-2.0, w + 1.0), rng.uniform(-2.0, h + 1.0)
        half = rng.choice([0, 1, 2, 3, 6, 9])
        cx = 0 if x != x else math.floor(x)
        cy = 0 if y != y else math.floor(y)
        rect = Rect(cx - half, cy - half, cx + half + 1, cy + half + 1 + rng.randint(0, 2))
        flux = rng.choice([0.5, 1.0, 2.0, 3.5, rng.uniform(0.0, 4.0)])  # ties are common
        dets.append(Detection(index=k, x=x, y=y, flux=flux, rect=rect, from_gaia=rng.random() < 0.08))
    return dets


def _random_image(rng, w, h):
    return np.array([[rng.choice([0.0, 0.25, 0.5, 1.0, rng.random()]) for _ in range(w)] for _ in range(h)],
                    dtype=np.float32)


@pytest.mark.parametrize("seed", range(60))
def test_fragment_filter_matches_the_old_quadratic_filter(seed):
    import random

    rng = random.Random(seed)
    w, h = 36, 28
    value_at = sm.reader(_random_image(rng, w, h))
    dets = _random_dets(rng, w, h, rng.randint(0, 80))
    px = rng.choice([1, 2, 3, 5.5, 9])
    old = _old_filter(dets, value_at, px)
    if old is None:  # a NaN candidate: the old scan raises, so must the new filter
        with pytest.raises(ValueError):
            sm.fragment_filter(dets, value_at, px)
    else:
        assert [id(d) for d in sm.fragment_filter(dets, value_at, px)] == [id(d) for d in old]


def test_fragment_filter_is_right_for_each_px_on_one_list():
    import random

    rng = random.Random(99)
    w, h = 30, 24
    value_at = sm.reader(_random_image(rng, w, h))
    dets = [d for d in _random_dets(rng, w, h, 70) if d.x == d.x]
    for px in (1, 2, 4, 7.5, 0):
        old = _old_filter(dets, value_at, px)
        assert [id(d) for d in sm.fragment_filter(dets, value_at, px)] == [id(d) for d in old], px


def test_fragment_rules_on_a_hand_made_pair():
    img = np.zeros((12, 12), dtype=np.float32)
    img[6, 6] = 1.0
    img[6, 7] = 0.5
    value_at = sm.reader(img)
    bright = Detection(index=0, x=6.0, y=6.0, flux=5.0, rect=Rect(4, 4, 9, 9))
    faint = Detection(index=1, x=7.0, y=6.0, flux=1.0, rect=Rect(6, 5, 8, 7))
    tie = Detection(index=2, x=6.0, y=6.0, flux=5.0, rect=Rect(4, 4, 9, 9))  # equal flux: not brighter
    gaia = Detection(index=3, x=7.0, y=6.0, flux=1.0, rect=Rect(6, 5, 8, 7), from_gaia=True)
    dets = [bright, faint, tie, gaia]
    assert sm.is_fragment(faint, dets, value_at, 2) is True
    assert sm.is_fragment(bright, dets, value_at, 2) is False
    assert sm.is_fragment(gaia, dets, value_at, 2) is False
    assert sm.fragment_filter(dets, value_at, 2) == [bright, tie, gaia]


def _mutation_image():
    img = np.zeros((40, 40), dtype=np.float32)
    img[10, 10] = 1.0   # the brighter star
    img[10, 14] = 0.5   # its fainter neighbour, a local peak of its own
    img[30, 30] = 1.0
    return sm.reader(img)


def test_fragment_filter_follows_a_detection_moved_into_range_in_place():
    value_at = _mutation_image()
    bright = Detection(index=0, x=30.0, y=30.0, flux=5.0, rect=Rect(28, 28, 33, 33))
    faint = Detection(index=1, x=14.0, y=10.0, flux=1.0, rect=Rect(13, 9, 15, 11))
    dets = [bright, faint]
    assert sm.fragment_filter(dets, value_at, 2) == [bright, faint]     # far away: a star
    bright.x, bright.y, bright.rect = 10.0, 10.0, Rect(8, 8, 13, 13)    # the same list object, edited in place
    assert sm.fragment_filter(dets, value_at, 2) == [bright]            # now inside its core: a fragment
    assert _old_filter(dets, value_at, 2) == [bright]


def test_fragment_filter_follows_a_brighter_rect_grown_in_place():
    value_at = _mutation_image()
    bright = Detection(index=0, x=10.0, y=10.0, flux=5.0, rect=Rect(10, 10, 11, 11))
    faint = Detection(index=1, x=14.0, y=10.0, flux=1.0, rect=Rect(13, 9, 15, 11))
    dets = [bright, faint]
    assert sm.fragment_filter(dets, value_at, 2) == [bright, faint]     # core too small to reach the faint one
    bright.rect = Rect(5, 5, 16, 16)                                    # the same object, grown in place
    assert sm.fragment_filter(dets, value_at, 2) == [bright]
    assert _old_filter(dets, value_at, 2) == [bright]


@pytest.mark.parametrize("delta_deg", [1.99, -1.99, 2.01, -2.01])
def test_parallel_two_degree_tolerance_on_both_sides(delta_deg):
    """Fly.dropBorrowedSpikes: spikes agree within 2 degrees (lines, not rays), checked on both sides of the limit."""
    a = 0.3
    d = math.radians(delta_deg)
    expect = abs(delta_deg) < 2
    assert sm._parallel(a, a + d) is expect
    assert sm._parallel(a, a + math.pi + d) is expect  # the opposite ray is the same line


def test_parallel_two_degree_tolerance_across_the_wrap():
    """The line test folds the angle difference into [0, pi/2]: just inside and just outside 2 degrees, near pi."""
    a = 3.1
    assert sm._parallel(a, a + math.pi - math.radians(1.99))
    assert not sm._parallel(a, a + math.pi - math.radians(2.01))
