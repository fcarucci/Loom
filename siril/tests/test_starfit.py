"""loomsiril.starfit against the JS oracle (tests/oracle/starfit.json) and its own invariants.

Every case is replayed from the inputs stored in the JSON (the field planes, the
detections, the recorded models); nothing is rebuilt with a formula here.
Tolerances: 1e-12 for float64 rules on float64 inputs, 1e-6 for values computed
from float32 buffers (the JS stores Float32Array planes), exact for ints.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from loomsiril import starfit as sf
from loomsiril import starmodel as sm
from loomsiril.core import Point, Rect
from loomsiril.types import Detection, Halo, Placed, Spike, SpriteMatch, StarModel

F64 = 1e-12
F32 = 1e-6


@pytest.fixture(scope="module")
def cases(oracle):
    return oracle("starfit")["cases"]


@pytest.fixture(scope="module")
def field(cases):
    """The recorded stars layer as an env, plus its detections (no model yet)."""
    f = cases["field"]
    w, h, nc = f["w"], f["h"], f["nc"]
    env = sf.StarEnv(
        I=np.asarray(f["I"], dtype=np.float32).reshape(h, w),
        T=[np.asarray(p, dtype=np.float32).reshape(h, w) for p in f["T"]],
        w=w,
        h=h,
        nc=nc,
        fwhm=f["fwhm"],
        labels=np.asarray(f["labels"], dtype=np.int32).reshape(h, w),
        noise=f["noise"],
        sky=list(f["sky"]),
        sky_i=f["skyI"],
        spikes=list(f["spikes"]),
        spike_sigma=f["spikeSigma"],
        spike_falloff=np.asarray(f["spikeFalloff"], dtype=np.float64),
    )
    dets = [
        Detection(index=d["index"], x=d["x"], y=d["y"], flux=d["flux"], rect=Rect.from_dict(d["rect"]), nmax=d["nmax"],
                  from_gaia=d["fromGaia"])
        for d in f["dets"]
    ]
    return env, dets


def _model(d: dict) -> StarModel:
    """A recorded starModel output (JS field names) as a StarModel."""
    return StarModel(
        x=d["x"],
        y=d["y"],
        core=d["core"],
        prof=np.asarray(d["prof"], dtype=np.float64),
        rmax=d["rmax"],
        spikes=[Spike(angle=s["angle"], start=s["start"], amp=np.asarray(s["amp"], dtype=np.float64), reach=s["reach"])
                for s in d["spikes"]],
        col=list(d["col"]),
        support=d["support"],
        spike_sigma=d.get("spikeSigma"),
    )


@pytest.fixture(scope="module")
def models(cases):
    """The recorded star models, one per detection, in detection order."""
    return [_model(rec["out"]) for rec in cases["starModel"]]


def _with_models(dets, models):
    for d, m in zip(dets, models):
        d.model = m
    return dets


def _planes(flat, h, w):
    return [np.asarray(p, dtype=np.float32).reshape(h, w) for p in flat]


def _assert_model(m: StarModel, rec: dict, tol: float) -> None:
    np.testing.assert_allclose(m.prof, rec["prof"], rtol=0, atol=tol)
    assert m.rmax == pytest.approx(rec["rmax"], abs=0)
    if "core" in rec:  # JS point models carry no core
        assert m.core == rec["core"]
    assert m.support == pytest.approx(rec["support"], abs=0)
    np.testing.assert_allclose(m.col, rec["col"], rtol=0, atol=tol)
    assert len(m.spikes) == len(rec["spikes"])
    for sp, rs in zip(m.spikes, rec["spikes"]):
        assert sp.angle == pytest.approx(rs["angle"], abs=F64)
        assert sp.start == rs["start"]
        assert sp.reach == rs["reach"]
        np.testing.assert_allclose(sp.amp, rs["amp"], rtol=0, atol=tol)


# ---------------------------------------------------------------------------
# make_env (Sky.sprites 694-701) and the env scalars


def test_make_env_matches_js(cases, field):
    _, dets = field
    rec = cases["makeEnv"]
    h, w = cases["field"]["h"], cases["field"]["w"]
    planes = _planes(rec["planes"], h, w)
    env = sf.make_env(planes, dets, rec["fwhm"])
    out = rec["out"]
    assert env.fwhm == rec["fwhm"]
    np.testing.assert_allclose(env.I.ravel(), out["I"], rtol=0, atol=F32)
    np.testing.assert_array_equal(env.labels.ravel(), out["labels"])
    assert env.noise == pytest.approx(out["noise"], abs=F32)
    np.testing.assert_allclose(env.sky, out["sky"], rtol=0, atol=F32)
    assert env.sky_i == pytest.approx(out["skyI"], abs=F32)
    np.testing.assert_allclose(env.spikes, out["spikes"], rtol=0, atol=1e-9)
    assert env.spike_sigma == pytest.approx(out["spikeSigma"], abs=F32)
    np.testing.assert_allclose(env.spike_falloff, out["spikeFalloff"], rtol=0, atol=F32)


def test_sprite_px_is_js_round_with_default():
    assert sf.sprite_px(None) == 3  # fwhm || FWHM_DEFAULT
    assert sf.sprite_px(0) == 3
    assert sf.sprite_px(float("nan")) == 3
    assert sf.sprite_px(0.4) == 1  # Math.max(1, Math.round(0.4))
    assert sf.sprite_px(2.5) == 3  # Math.round halves up
    assert sf.sprite_px(4.49) == 4


# ---------------------------------------------------------------------------
# Spike directions, width, falloff (Sky.js 777-908)


def test_spike_directions_oracle(cases, field):
    env, dets = field
    got = sf.spike_directions(env, dets)
    np.testing.assert_allclose(got, cases["spikeDirections"]["out"], rtol=0, atol=1e-9)
    assert len(got) == len(cases["field"]["spikes"])


def test_spike_width_oracle(cases, field):
    env, dets = field
    assert sf.spike_width(env, dets, cases["spikeWidth"]["angles"]) == pytest.approx(cases["spikeWidth"]["out"], abs=F32)
    assert sf.spike_width(env, dets, []) == cases["spikeWidthNoAngles"]["out"] == sf.SPIKE_SIGMA


def test_spike_falloff_oracle(cases, field):
    env, dets = field
    got = sf.spike_falloff(env, dets, cases["spikeFalloff"]["angles"])
    assert got.shape == (sm.SPIKE_MAX,)
    np.testing.assert_allclose(got, cases["spikeFalloff"]["out"], rtol=0, atol=F32)


def test_spike_excess_oracle(cases, field):
    env, _ = field
    for rec in cases["spikeExcess"]:
        cx, cy, ang, r, *rest = rec["args"]
        side = rest[0] if rest else None
        got = sf.spike_excess(env.I, cx, cy, ang, r, side)
        assert got == pytest.approx(rec["out"], abs=F64)


def test_spike_side_oracle(cases):
    for rec in cases["spikeSide"]:
        sigma = rec["args"][0]
        assert sf.spike_side(sigma) == pytest.approx(rec["out"], abs=F64)


def test_smooth_falloff_oracle(cases):
    for rec in cases["smoothFalloff"]:
        T, first = rec["args"]
        np.testing.assert_allclose(sf.smooth_falloff(T, first), rec["out"], rtol=0, atol=F64)


def test_smooth_falloff_never_rises_outward():
    T = [0.0, 0.0, 0.3, 0.2, 0.9, 0.1, 0.5, 0.0]
    out = sf.smooth_falloff(T, 2)
    assert np.all(np.diff(out[2:]) <= 0)
    assert np.all(out[:2] == out[2])


# ---------------------------------------------------------------------------
# Star models (Sky.js 909-970)


def test_star_model_oracle(cases, field):
    env, dets = field
    for rec, d in zip(cases["starModel"], dets):
        m = sf.star_model(env, d)
        _assert_model(m, rec["out"], F32)
        assert d.halo.radius == pytest.approx(rec["halo"]["radius"], abs=0)
        assert d.halo.halo == rec["out"]["rmax"]
        assert d.halo.outer == rec["halo"]["outer"]
        assert d.halo.pedestal == 0.0 and d.halo.feather == 0.0
        assert [(s.angle, s.reach) for s in d.halo.spikes] == [
            (s["angle"], s["reach"]) for s in rec["halo"]["spikes"]
        ]
        assert isinstance(d.halo, Halo)


def test_star_without_spikes_has_empty_spike_list(cases, field):
    env, dets = field
    counts = cases["starModelSpikeCount"]
    assert 0 in counts, "the recorded field must hold a star with no spikes"
    for k, n in enumerate(counts):
        got = sf.star_model(env, dets[k])
        assert len(got.spikes) == n
        if n == 0:
            assert got.spikes == []


def test_point_model_has_core_zero(cases, field):
    env, _ = field
    sums = [np.asarray(p, dtype=np.float64).reshape(env.h, env.w) for p in cases["modelSums"]["out"]]
    m = sf.point_model(env, sums, 20, 15, 0.8)
    assert m.core == 0.0
    assert m.spikes == []


# ---------------------------------------------------------------------------
# Model evaluation (Sky.js 985-1015)


def test_model_at_oracle(cases, models):
    for k, rec in enumerate(cases["modelAt"]):
        m = models[k]
        for p in rec["pts"]:
            assert sf.model_at(m, p["x"], p["y"]) == pytest.approx(p["modelAt"], abs=F64)
            assert sf.radial_at(m, p["x"], p["y"]) == pytest.approx(p["radialAt"], abs=F64)
            assert sf.spike_at(m, p["x"], p["y"]) == pytest.approx(p["spikeAt"], abs=F64)


def test_window_versions_equal_scalar(models, field):
    env, _ = field
    for m in models:
        x0, y0, x1, y1 = 0, 0, env.w, env.h
        win = sf.model_window(m, x0, y0, x1, y1)
        rad = sf.radial_window(m, x0, y0, x1, y1)
        spk = sf.spike_window(m, x0, y0, x1, y1)
        assert win.shape == (y1 - y0, x1 - x0)
        for y in range(y0, y1, 3):
            for x in range(x0, x1, 3):
                j, i = y - y0, x - x0
                assert win[j, i] == pytest.approx(sf.model_at(m, x, y), abs=F64)
                assert rad[j, i] == pytest.approx(sf.radial_at(m, x, y), abs=F64)
                assert spk[j, i] == pytest.approx(sf.spike_at(m, x, y), abs=F64)


def test_window_versions_on_random_models():
    rng = np.random.default_rng(5)
    for trial in range(4):
        prof = np.clip(np.sort(rng.random(12))[::-1], 0, None)
        amp = rng.random(9) * 0.2
        m = StarModel(
            x=float(rng.uniform(3, 20)),
            y=float(rng.uniform(3, 20)),
            core=3.0,
            prof=prof,
            rmax=11.0,
            spikes=[Spike(angle=0.7 + trial, start=4.0, amp=amp, reach=12.0)],
            col=[1.0, 0.5],
            support=13.0,
            spike_sigma=1.2,
        )
        win = sf.model_window(m, 0, 0, 30, 25)
        for y in range(25):
            for x in range(30):
                assert win[y, x] == pytest.approx(sf.model_at(m, x, y), abs=F64)


def test_model_window_off_image_and_window_bounds():
    m = StarModel(x=-5.0, y=-5.0, core=2.0, prof=np.array([1.0, 0.5, 0.2]), rmax=3.0, spikes=[], col=[1.0],
                  support=4.0)
    win = sf.model_window(m, 0, 0, 4, 4)
    assert win.shape == (4, 4)
    assert np.all(win == 0.0)


# ---------------------------------------------------------------------------
# Model sums, the second pass, and the deblend (Sky.js 1016-1130)


def test_model_sums_oracle(cases, field, models):
    env, dets = field
    _with_models(dets, models)
    got = sf.model_sums(env, dets)
    for c, plane in enumerate(cases["modelSums"]["out"]):
        np.testing.assert_allclose(got[c].ravel(), plane, rtol=0, atol=F32)


def test_model_sums_of_one_star_is_its_window_times_colour(cases, field, models):
    env, dets = field
    d = dets[0]
    d.model = models[0]
    got = sf.model_sums(env, [d])
    m = models[0]
    R = m.support
    x0, y0 = max(0, math.floor(m.x - R)), max(0, math.floor(m.y - R))
    x1, y1 = min(env.w - 1, math.ceil(m.x + R)), min(env.h - 1, math.ceil(m.y + R))
    win = sf.model_window(m, x0, y0, x1 + 1, y1 + 1)
    for c in range(env.nc):
        block = got[c][y0 : y1 + 1, x0 : x1 + 1]
        expect = np.where(win <= 0, 0.0, win * m.col[c])
        np.testing.assert_allclose(block, expect, rtol=0, atol=F64)
    assert np.all(got[0][: y0, :] == 0.0) and np.all(got[0][y1 + 1 :, :] == 0.0)


def test_unexplained_sources_oracle(cases, field, models):
    env, dets = field
    _with_models(dets, models)
    sums = [np.asarray(p, dtype=np.float64).reshape(env.h, env.w) for p in cases["modelSums"]["out"]]
    sprites = [SpriteMatch(placed=Placed(source={}, d=0.0, x=d.x, y=d.y), det=d, rect=d.rect) for d in dets]
    extra = sf.unexplained_sources(env, sprites, sums)
    exp = cases["unexplainedSources"]["out"]
    assert len(extra) == len(exp) > 0, "the faint star must be found by the second pass"
    for m, rec in zip(extra, exp):
        assert m.x == rec["x"] and m.y == rec["y"]
        assert m.core == 0.0 and m.spikes == []
        _assert_model(m, rec, F32)


def test_unexplained_sources_finds_a_pixel_once_when_two_sprites_share_it(cases, field, models):
    env, dets = field
    _with_models(dets, models)
    sums = [np.asarray(p, dtype=np.float64).reshape(env.h, env.w) for p in cases["modelSums"]["out"]]
    sprites = [SpriteMatch(placed=Placed(source={}, d=0.0, x=d.x, y=d.y), det=d, rect=d.rect) for d in dets]
    once = sf.unexplained_sources(env, sprites, sums)
    twice = sf.unexplained_sources(env, sprites + sprites, sums)  # every window is visited twice: seen skips the repeats
    assert [(m.x, m.y) for m in twice] == [(m.x, m.y) for m in once] != []


def test_add_to_sums_oracle(cases, field, models):
    env, dets = field
    _with_models(dets, models)
    sums = [np.asarray(p, dtype=np.float64).reshape(env.h, env.w) for p in cases["modelSums"]["out"]]
    sprites = [SpriteMatch(placed=Placed(source={}, d=0.0, x=d.x, y=d.y), det=d, rect=d.rect) for d in dets]
    extra = sf.unexplained_sources(env, sprites, sums)
    sf.add_to_sums(env, extra, sums)
    for c, plane in enumerate(cases["addToSums"]["out"]):
        np.testing.assert_allclose(sums[c].ravel(), plane, rtol=0, atol=F32)


def test_excess_peak_recorded(cases):
    grid = lambda x, y: (0.9 if (x, y) == (5, 5) else 0.2 if (x, y) == (6, 5) else 0.1 * ((x + 2 * y) % 5))  # noqa: E731
    for rec in cases["excessPeak"]:
        x, y, e = rec["args"]
        assert sf.excess_peak(grid, x, y, e) is rec["out"]


def test_point_model_oracle(cases, field):
    env, _ = field
    sums = [np.asarray(p, dtype=np.float64).reshape(env.h, env.w) for p in cases["modelSums"]["out"]]
    for rec in cases["pointModel"]:
        x, y, e = rec["args"]
        m = sf.point_model(env, sums, x, y, e)
        out = rec["out"]
        np.testing.assert_allclose(m.prof, out["prof"], rtol=0, atol=F32)
        assert m.rmax == out["rmax"] and m.support == out["support"]
        np.testing.assert_allclose(m.col, out["col"], rtol=0, atol=F32)
        assert m.core == 0.0


def test_deblend_arrays_equal_scalar():
    rng = np.random.default_rng(9)
    Mi = rng.random((7, 9)) * 0.5
    Msum = Mi + rng.random((7, 9)) * 0.3
    L = (rng.random((7, 9)) - 0.2) * 0.4
    Mi[0, 0] = 0.0  # no model here: 0
    Msum[1, 1] = 0.0  # no models: 0
    for sigma in (0.0, 0.003, 0.05, 0.4):
        got = sf._deblend_arrays(Mi, Msum, L, sigma)
        for idx in np.ndindex(Mi.shape):
            exp = sm.deblend(float(Mi[idx]), float(Msum[idx]), float(L[idx]), sigma)
            assert got[idx] == pytest.approx(exp, abs=F64)


def test_deblend_never_negative_where_light_is_positive():
    rng = np.random.default_rng(3)
    Mi = rng.random(200)
    Msum = Mi + rng.random(200)
    L = rng.random(200)
    got = sf._deblend_arrays(Mi, Msum, L, 0.01)
    assert np.all(got >= 0.0)


def test_deblend_sprite_oracle(cases, field, models):
    env, dets = field
    _with_models(dets, models)
    sums = [np.asarray(p, dtype=np.float64).reshape(env.h, env.w) for p in cases["addToSums"]["out"]]
    residual = [p.copy() for p in env.T]
    sprites = [SpriteMatch(placed=Placed(source={"G": 10.0 + k}, d=100.0 + k, x=d.x, y=d.y), det=d, rect=d.rect)
               for k, d in enumerate(dets)]
    out = cases["deblendSprite"]["out"]
    got_sprites = [sf.deblend_sprite(env, s, sums, residual) for s in sprites]
    for g, rec in zip(got_sprites, out):
        r = rec["rect"]
        assert (g.rect.x0, g.rect.y0, g.rect.x1, g.rect.y1) == (r["x0"], r["y0"], r["x1"], r["y1"])
        assert g.radius == rec["radius"]
        assert g.centre == Point(rec["centre"]["x"], rec["centre"]["y"])
        rh, rw = g.rect.height, g.rect.width
        np.testing.assert_array_equal(g.mask, np.asarray(rec["mask"], dtype=np.uint8).reshape(rh, rw))
        assert g.spike_angles == pytest.approx(rec["spikeAngles"], abs=F64)
        for c in range(env.nc):
            np.testing.assert_allclose(g.pixels[c], np.asarray(rec["pixels"][c]).reshape(rh, rw), rtol=0, atol=F32)
            np.testing.assert_allclose(g.model_core[c], np.asarray(rec["modelCore"][c]).reshape(rh, rw), rtol=0, atol=F32)
            np.testing.assert_allclose(g.model_spikes[c], np.asarray(rec["modelSpikes"][c]).reshape(rh, rw), rtol=0,
                                       atol=F32)
            assert g.pixels[c].dtype == np.float32 and g.model_core[c].dtype == np.float32
    for c in range(env.nc):
        np.testing.assert_allclose(residual[c].ravel(), cases["deblendSprite"]["residual"][c], rtol=0, atol=F32)


def test_pixels_plus_residual_equal_the_stars_layer(cases, field, models):
    """The sum over sprites of pixels, plus the residual, is the stars layer (per channel)."""
    env, dets = field
    _with_models(dets, models)
    sums = [np.asarray(p, dtype=np.float64).reshape(env.h, env.w) for p in cases["addToSums"]["out"]]
    residual = [p.copy() for p in env.T]
    sprites = [SpriteMatch(placed=Placed(source={}, d=0.0, x=d.x, y=d.y), det=d, rect=d.rect) for d in dets]
    got = [sf.deblend_sprite(env, s, sums, residual) for s in sprites]
    for c in range(env.nc):
        canvas = np.zeros((env.h, env.w), dtype=np.float64)
        for g in got:
            r = g.rect
            canvas[r.y0 : r.y1, r.x0 : r.x1] += g.pixels[c]
        np.testing.assert_allclose(canvas + residual[c], env.T[c], rtol=0, atol=F32)


def test_sprite_mask_and_spike_angles_shape(cases, field, models):
    env, dets = field
    _with_models(dets, models)
    sums = [np.asarray(p, dtype=np.float64).reshape(env.h, env.w) for p in cases["addToSums"]["out"]]
    residual = [p.copy() for p in env.T]
    s = SpriteMatch(placed=Placed(source={}, d=0.0, x=dets[0].x, y=dets[0].y), det=dets[0], rect=dets[0].rect)
    g = sf.deblend_sprite(env, s, sums, residual)
    assert g.mask.shape == (g.rect.height, g.rect.width) and g.mask.dtype == np.uint8
    assert g.spike_angles == [sp.angle for sp in models[0].spikes]


def test_constants_match_js(cases):
    c = cases["field"]
    assert sf.SPIKE_SIDE == 3 and sf.SPIKE_SIGMA == 1.0 and sf.SPIKE_STARS == 12 and sf.SPIKE_LEVER == 100
    assert sf.MISSED_SIGMA == 5 and sf.MISSED_SHARE == 0.5
    assert c["fwhm"] == 3


def test_parts_branch_replays_the_js_sequence(cases):
    """The moving-star core case: an undetected star inside a detected star's core becomes one of its parts.

    Replays the whole Sky.sprites sequence from the recorded planes: make_env, star_model, model_sums,
    unexplained_sources, add_to_sums, attach_to_cores, deblend_sprite; the parts branch of the deblend
    (mc += the part's model) is exercised because star 0 has one part.
    """
    rec = cases["parts"]
    h, w = cases["field"]["h"], cases["field"]["w"]
    planes = _planes(rec["planes"], h, w)
    dets = [
        Detection(index=d["index"], x=d["x"], y=d["y"], flux=d["flux"], rect=Rect.from_dict(d["rect"]), nmax=d["nmax"],
                  from_gaia=d["fromGaia"])
        for d in rec["dets"]
    ]
    env = sf.make_env(planes, dets, rec["fwhm"])
    e = rec["env"]
    assert env.noise == pytest.approx(e["noise"], abs=F32)
    np.testing.assert_allclose(env.sky, e["sky"], rtol=0, atol=F32)
    assert env.sky_i == pytest.approx(e["skyI"], abs=F32)
    np.testing.assert_allclose(env.spikes, e["spikes"], rtol=0, atol=1e-9)
    np.testing.assert_array_equal(env.labels.ravel(), e["labels"])

    for d, mrec in zip(dets, rec["models"]):
        d.model = sf.star_model(env, d)
        _assert_model(d.model, mrec, F32)
    sprites = [
        SpriteMatch(placed=Placed(source={"G": 10.0 + k}, d=100.0 + k, x=d.x, y=d.y), det=d, rect=d.rect)
        for k, d in enumerate(dets)
    ]
    sums = sf.model_sums(env, dets)
    for c in range(env.nc):
        np.testing.assert_allclose(sums[c].ravel(), rec["sumsBefore"][c], rtol=0, atol=F32)

    extra = sf.unexplained_sources(env, sprites, sums)
    assert [(m.x, m.y) for m in extra] == [(p["x"], p["y"]) for p in rec["extra"]]
    sf.add_to_sums(env, extra, sums)
    left = sm.attach_to_cores(sprites, extra)
    assert len(left) == rec["leftCount"]
    assert [0 if d.model.parts is None else len(d.model.parts) for d in dets] == rec["partsCount"]
    for d, pos in zip(dets, rec["partsPos"]):
        if pos is not None:
            assert [(p.x, p.y) for p in d.model.parts] == [(q["x"], q["y"]) for q in pos]
    assert any(d.model.parts for d in dets), "the field must attach a part to a moving star"

    residual = [p.copy() for p in env.T]
    for s, out in zip(sprites, rec["deblend"]):
        g = sf.deblend_sprite(env, s, sums, residual)
        r = out["rect"]
        assert (g.rect.x0, g.rect.y0, g.rect.x1, g.rect.y1) == (r["x0"], r["y0"], r["x1"], r["y1"])
        rh, rw = g.rect.height, g.rect.width
        np.testing.assert_array_equal(g.mask, np.asarray(out["mask"], dtype=np.uint8).reshape(rh, rw))
        for c in range(env.nc):
            np.testing.assert_allclose(g.pixels[c], np.asarray(out["pixels"][c]).reshape(rh, rw), rtol=0, atol=F32)
            np.testing.assert_allclose(g.model_core[c], np.asarray(out["modelCore"][c]).reshape(rh, rw), rtol=0, atol=F32)
            np.testing.assert_allclose(g.model_spikes[c], np.asarray(out["modelSpikes"][c]).reshape(rh, rw), rtol=0,
                                       atol=F32)
    for c in range(env.nc):
        np.testing.assert_allclose(residual[c].ravel(), rec["residual"][c], rtol=0, atol=F32)


def _edge_env(w: int, h: int) -> sf.StarEnv:
    """A one-channel env with a black image: the test puts the light it needs."""
    I = np.zeros((h, w), dtype=np.float32)
    return sf.StarEnv(I=I, T=[I.copy()], w=w, h=h, nc=1, fwhm=3, labels=np.full((h, w), -1, dtype=np.int32),
                      noise=0.01, sky=[0.0], sky_i=0.0)


def _edge_sprite(x: float, y: float, support: float) -> SpriteMatch:
    model = StarModel(x=x, y=y, core=0.0, prof=np.zeros(1, dtype=np.float64), rmax=0.0, spikes=[], col=[1.0],
                      support=support)
    det = Detection(index=0, x=x, y=y, flux=1.0, rect=Rect(int(x) - 1, int(y) - 1, int(x) + 2, int(y) + 2), model=model)
    return SpriteMatch(placed=Placed(source={}, d=0.0, x=x, y=y), det=det, rect=det.rect)


@pytest.mark.parametrize("col, row, found", [(10, 5, True), (11, 5, False), (5, 10, True), (5, 11, False)])
def test_unexplained_sources_window_bound_is_one_pixel_inside_the_image(col, row, found):
    """Fly.unexplainedSources scans x in 1..w-2 and y in 1..h-2 (the last row and column are never a candidate).

    A 12 x 12 image: the sprite's support covers the whole image, and one bright pixel sits on the last allowed
    column (w-2) or row (h-2) or one beyond it (w-1, h-1).
    """
    env = _edge_env(12, 12)
    env.I[row, col] = 1.0
    sums = [np.zeros((12, 12), dtype=np.float64)]
    extra = sf.unexplained_sources(env, [_edge_sprite(5.0, 5.0, 10.0)], sums)
    assert [(m.x, m.y) for m in extra] == ([(float(col), float(row))] if found else [])


def test_window_pixels_start_one_pixel_inside_the_image():
    """The scan's lower bound: x and y start at 1 (the first column and row are never a candidate), even when the
    support square reaches past the edge; the upper bound is w-2 and h-2. A mutation of the lower bound to 0 fails here."""
    m = StarModel(x=0.0, y=0.0, core=0.0, prof=np.zeros(1, dtype=np.float64), rmax=0.0, spikes=[], col=[1.0],
                  support=5.0)
    pts = list(sf._window_pixels(m, 12, 12))
    assert min(x for x, _ in pts) == 1
    assert min(y for _, y in pts) == 1
    assert max(x for x, _ in pts) == 5
    assert max(y for _, y in pts) == 5


def _star_spikes_scalar(env, d, min_r):
    """The star_spikes of before the vectorisation: one scalar spike_excess per sample. The reference."""
    side = sf.spike_side(env.spike_sigma)

    def excess(a):
        return [sf.spike_excess(env.I, d.x, d.y, a, min_r + k, side) for k in range(sm.SPIKE_FIT)]

    out = []
    for ang in env.spikes:
        partner = next((b for b in env.spikes if abs(abs(b - ang) - math.pi) < 2 * math.pi / 180), None)
        if partner is not None:
            sp = sm.fit_spike_pair(excess(ang), excess(partner), env.spike_falloff, int(min_r), env.noise)
        else:
            sp = sm.fit_spike(excess(ang), env.spike_falloff, int(min_r), env.noise)
        if sp["reach"] > 0:
            out.append((ang, min_r, min_r + sp["reach"], sp["amp"]))
    return out


def test_star_spikes_equals_the_scalar_spike_excess_exactly(field):
    """The vectorised excess grid gives the scalar spike_excess's floats, so the spikes are identical."""
    env, dets = field
    assert env.spikes, "the recorded field has spike directions"
    for d in dets:
        min_r = math.ceil(max(d.rect.x1 - d.x, d.x - d.rect.x0)) + env.fwhm
        got = sf.star_spikes(env, d, min_r)
        ref = _star_spikes_scalar(env, d, min_r)
        assert [(s.angle, s.start, s.reach) for s in got] == [(a, s0, r) for a, s0, r, _ in ref]
        for s, (_, _, _, amp) in zip(got, ref):
            np.testing.assert_array_equal(s.amp, amp)


def test_excess_trig_grid_equals_the_scalar_spike_excess_exactly(field):
    env, dets = field
    d = dets[0]
    angs = list(env.spikes)
    rs = [float(r) for r in range(10, 30)]
    c = np.asarray([math.cos(a) for a in angs], dtype=np.float64)[:, None]
    s_ = np.asarray([math.sin(a) for a in angs], dtype=np.float64)[:, None]
    grid = sf._excess_trig(env, d, (c, s_), sf._Reads(np.asarray(rs), 3.0))
    scalar = np.array([[sf.spike_excess(env.I, d.x, d.y, a, r, 3.0) for r in rs] for a in angs])
    np.testing.assert_array_equal(grid, scalar)
