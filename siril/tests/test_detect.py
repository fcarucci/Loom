"""loomsiril.detect: the numpy star detector and the Sky.brightMeasure helpers (E3a).

Oracle: tests/oracle/assign.json (tools/oracle/assign.js) for brightMeasure and
eachInWindow. The detector itself replaces PixInsight's StarDetector, so its
checks are behavioural, on the synthetic field of tests/synth.py.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from loomsiril import core, parallax, starlayer
from loomsiril.detect import (
    _upper_half_sigma,
    bright_measure,
    detect_stars,
    each_in_window,
    intensity,
    noise_sigma,
)


def _centres_of(truth) -> np.ndarray:
    return np.array([[s.x, s.y] for s in truth], dtype=np.float64)


def _nearest(points: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Distance from each target point to the nearest of ``points`` (both (n, 2) arrays)."""
    d = np.sqrt(((target[:, None, :] - points[None, :, :]) ** 2).sum(axis=2))
    return d.min(axis=1)


# ---- the oracle ------------------------------------------------------------


def test_bright_measure_matches_js(oracle):
    case = oracle("assign")["cases"]["brightMeasure"]
    w, h = case["args"]["w"], case["args"]["h"]
    buf = np.asarray(case["args"]["buf"], dtype=np.float32).reshape(h, w)
    for (x, y), want in zip(case["args"]["cases"], case["out"]):
        got = bright_measure(buf, x, y)
        assert got["area"] == want["area"], (x, y, got, want)
        assert abs(got["flux"] - want["flux"]) <= 1e-12, (x, y, got, want)


def test_each_in_window_matches_js(oracle):
    case = oracle("assign")["cases"]["eachInWindow"]
    w, h = case["args"]["w"], case["args"]["h"]
    for (cx, cy, R), want in zip(case["args"]["cases"], case["out"]):
        rows, cols = each_in_window(w, h, cx, cy, R)
        got = [[r * w + c, c - cx, r - cy] for r in range(rows.start, rows.stop) for c in range(cols.start, cols.stop)]
        assert got == want, (cx, cy, R)


def test_each_in_window_is_clipped_square():
    rows, cols = each_in_window(10, 8, 0, 7, 3)
    assert (rows.start, rows.stop, cols.start, cols.stop) == (4, 8, 0, 4)


# ---- the detector ----------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_most_injected_stars_are_detected(synth_field, seed):
    f = synth_field(seed=seed)
    dets = detect_stars(f.image)
    pts = np.array([[d.x, d.y] for d in dets], dtype=np.float64)
    near = _nearest(pts, _centres_of(f.truth))
    assert np.mean(near <= 0.5) >= 0.9, np.sort(near)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_detection_far_from_every_star(synth_field, seed):
    f = synth_field(seed=seed)
    dets = detect_stars(f.image)
    pts = np.array([[d.x, d.y] for d in dets], dtype=np.float64)
    far = _nearest(_centres_of(f.truth), pts)
    assert far.max() <= 3.0, far


def test_detections_are_sorted_and_reindexed(synth_field):
    dets = detect_stars(synth_field(seed=4).image)
    assert len(dets) > 10
    fluxes = [d.flux for d in dets]
    assert fluxes == sorted(fluxes, reverse=True)
    assert [d.index for d in dets] == list(range(len(dets)))
    assert all(d.nmax == 0 for d in dets)


def test_rect_contains_the_centre(synth_field):
    for d in detect_stars(synth_field(seed=5).image):
        assert d.rect.contains(d.x, d.y), d
        assert d.rect.width >= 1 and d.rect.height >= 1


def test_background_only_image_gives_no_detection(synth_field):
    assert detect_stars(synth_field(seed=6, n_stars=0, noise=0.0).image) == []
    flat = [np.full((90, 160), 0.08, dtype=np.float32)]
    assert detect_stars(flat) == []


def test_noise_still_finds_the_bright_half(synth_field):
    f = synth_field(seed=7, n_stars=30, noise=0.002)
    bright = sorted(f.truth, key=lambda s: s.peak, reverse=True)[:15]
    pts = np.array([[d.x, d.y] for d in detect_stars(f.image)], dtype=np.float64)
    near = _nearest(pts, np.array([[s.x, s.y] for s in bright]))
    assert np.all(near <= 0.5), np.sort(near)


def test_intensity_is_the_channel_mean():
    a = np.full((3, 4), 0.2, dtype=np.float32)
    b = np.full((3, 4), 0.6, dtype=np.float32)
    out = intensity([a, b])
    assert out.dtype == np.float32
    assert np.allclose(out, 0.4, atol=1e-7)


def test_noise_sigma_is_zero_without_spread():
    assert noise_sigma(np.full((20, 20), 0.5, dtype=np.float32)) == 0.0


def test_noise_sigma_tracks_gaussian_noise():
    rng = np.random.default_rng(3)
    buf = rng.normal(0.1, 0.01, size=(200, 200)).astype(np.float32)
    assert math.isclose(noise_sigma(buf), 0.01, rel_tol=0.1)


def test_detector_finds_a_single_star_with_its_flux():
    img = np.zeros((40, 40), dtype=np.float32)
    yy, xx = np.mgrid[0:40, 0:40]
    img += (0.5 * np.exp(-((xx - 20.0) ** 2 + (yy - 13.0) ** 2) / (2 * 1.2 ** 2))).astype(np.float32)
    dets = detect_stars([img])
    assert len(dets) == 1
    d = dets[0]
    assert abs(d.x - 20.0) < 0.05 and abs(d.y - 13.0) < 0.05
    assert d.flux > 0
    assert d.rect.contains(d.x, d.y)


def test_detector_is_deterministic(synth_field):
    f = synth_field(seed=8)
    a = detect_stars(f.image)
    b = detect_stars(f.image)
    assert [(d.x, d.y, d.flux, d.rect) for d in a] == [(d.x, d.y, d.flux, d.rect) for d in b]



# ---- E3e: a degenerate noise estimate must not make the threshold 0 ----------------------------
#
# A noiseless stars layer (a StarNet stars layer is mostly exact zeros after unscreen) has median 0 and
# MAD 0, so noise_sigma is 0 and sky + k*noise is 0: every faint Gaussian-tail maximum became a star
# (129,080 detections for a 30-star 1920x1080 mono field, and starmodel.is_fragment is O(N^2) in those).
# detect_stars floors the noise at one 16-bit quantisation step, 1/65535.


def test_zero_median_and_mad_mono_field_detects_exactly_the_injected_stars(synth_field):
    f = synth_field(w=320, h=180, n_stars=30, nc=1, seed=1)  # noiseless: median and MAD are exactly 0
    inten = intensity(f.stars).astype(np.float64)
    assert float(np.median(inten)) == 0.0 and noise_sigma(inten) == 0.0
    dets = detect_stars(f.stars)
    assert len(dets) == len(f.truth) == 30
    pts = np.array([[d.x, d.y] for d in dets], dtype=np.float64)
    near = _nearest(pts, _centres_of(f.truth))
    assert np.all(near <= 0.5), np.sort(near)


@pytest.mark.perf
def test_pipeline_build_1080p_mono_zero_noise_completes_fast(tmp_path, synth_field, monkeypatch):
    """The stalled case (nc=1, 1920x1080, fwhm 12, 30 stars): 30 detections, and the build takes < 30 s."""
    import time

    from loomsiril import detect
    from loomsiril import pipeline as P

    f = synth_field(w=1920, h=1080, n_stars=30, nc=1, fwhm=12)
    cols = ["ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP"]
    tsv = tmp_path / "gaia.tsv"
    lines = ["\t".join(cols)]
    for rec in f.catalogue:
        lines.append("\t".join("" if rec[c] != rec[c] else repr(float(rec[c])) for c in cols))
    tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    counts: list[int] = []
    real = detect.detect_stars

    def counting(*a, **k):
        out = real(*a, **k)
        counts.append(len(out))
        return out

    monkeypatch.setattr(detect, "detect_stars", counting)
    job = P.FlyJob(starless=[np.array(p) for p in f.starless], stars=[np.array(p) for p in f.stars],
                   wcs={k: f.wcs[k] for k in ("crval1", "crval2", "crpix1", "crpix2", "cd")},
                   gaia=f"tsv:{tsv}", presets=["youtube_1080"], duration=1.0, fps=3, distance=1000.0,
                   dynamic="sdr", out_dir=str(tmp_path / "out"))
    t0 = time.perf_counter()
    P.run(job)
    elapsed = time.perf_counter() - t0
    print("pipeline build 1920x1080 nc=1 fwhm 12, 30 stars: %.2f s, detections %s" % (elapsed, counts))
    assert counts and counts[0] == 30
    assert elapsed < 30.0, elapsed


# ---- R2-0 characterization: the detection lists must not change except where the fix says so ----------
#
# Pinned on the code before the R2-0 fix (fly/final a242afd). Each digest is sha256 of repr([(x, y, flux,
# (x0, y0, x1, y1)), ...]) over detect_stars(f.stars) for synth_field(w=320, h=180, n_stars=20, seed=1, fwhm=2.5).
# A three-channel layer and a noiseless layer must stay identical after the fix (noise_sigma is not 0 there
# or the MAD already wins). A one-channel layer with noise > 0 is the intended change (R2-0), not pinned here.

_CHAR_DIGESTS = {
    (3, 1e-3): (20, "189207164a39e93491f43f8d39d0b7831286d902a801ddea056edf3a5cd445ad"),
    (1, 0.0): (20, "3a53eb449bba471a569684ab12267add4f6b1a5907487133d66d5ad9eeaed892"),
    (3, 0.0): (20, "7bc97504ae9e022bee703f1870ee420cc7b2bc811617e5609c272eb685d5871a"),
}


def _detection_digest(dets) -> tuple[int, str]:
    import hashlib

    rows = [(d.x, d.y, d.flux, (d.rect.x0, d.rect.y0, d.rect.x1, d.rect.y1)) for d in dets]
    return len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()


@pytest.mark.parametrize("nc, noise", sorted(_CHAR_DIGESTS))
def test_detection_list_is_unchanged_for_nc3_and_noiseless(synth_field, nc, noise):
    f = synth_field(w=320, h=180, n_stars=20, nc=nc, seed=1, fwhm=2.5, noise=noise)
    assert _detection_digest(detect_stars(f.stars)) == _CHAR_DIGESTS[(nc, noise)]


# ---- R2-0: a noisy one-channel stars layer is zero-inflated, so the MAD underestimates its noise ----------
#
# unscreen clips the negative noise at 0: about half the pixels of a noisy mono stars layer are exactly 0.
# The MAD then sees only the sliver around the median (3.7e-5 for a 1e-3 noise), the threshold sits in the
# noise and detect_stars reported 133,297 detections (30 stars). detect_stars now also takes P84 - P50.


def test_noisy_mono_field_detects_the_injected_stars(synth_field):
    f = synth_field(w=320, h=180, n_stars=20, nc=1, seed=1, fwhm=2.5, noise=1e-3)
    dets = detect_stars(f.stars)
    assert len(dets) <= 2 * len(f.truth), len(dets)
    pts = np.array([[d.x, d.y] for d in dets], dtype=np.float64)
    near = _nearest(pts, _centres_of(f.truth))
    assert np.mean(near <= 0.5) >= 0.9, np.sort(near)


def test_noisy_mono_detections_agree_with_three_channels(synth_field):
    mono = synth_field(w=320, h=180, n_stars=20, nc=1, seed=1, fwhm=2.5, noise=1e-3)
    tri = synth_field(w=320, h=180, n_stars=20, nc=3, seed=1, fwhm=2.5, noise=1e-3)
    a = np.array([[d.x, d.y] for d in detect_stars(mono.stars)], dtype=np.float64)
    b = np.array([[d.x, d.y] for d in detect_stars(tri.stars)], dtype=np.float64)
    assert np.mean(_nearest(b, a) <= 0.5) >= 0.9
    assert np.mean(_nearest(a, b) <= 0.5) >= 0.9


def test_noisy_mono_detection_is_fast(synth_field):
    import time

    f = synth_field(w=320, h=180, n_stars=20, nc=1, seed=1, fwhm=2.5, noise=1e-3)
    t0 = time.perf_counter()
    detect_stars(f.stars)
    assert time.perf_counter() - t0 < 1.0


def test_upper_half_sigma_recovers_a_clipped_gaussian():
    rng = np.random.default_rng(11)
    x = rng.normal(0.0, 1e-3, size=(400, 400))
    clipped = np.maximum(0.0, x).astype(np.float32)
    assert math.isclose(_upper_half_sigma(clipped), 1e-3, rel_tol=0.1)
    # the MAD collapses on the same buffer: the reason the detector takes the max
    assert noise_sigma(clipped) < 0.2 * 1e-3


def test_upper_half_sigma_is_gated_off_for_an_unclipped_gaussian():
    """No zeros (a three-channel layer): the estimate is 0 and the MAD decides (the P65 estimate would be
    above the MAD there and would lose faint stars)."""
    rng = np.random.default_rng(12)
    buf = rng.normal(0.1, 2e-3, size=(400, 400)).astype(np.float32)
    assert _upper_half_sigma(buf) == 0.0
    assert max(noise_sigma(buf), _upper_half_sigma(buf)) == noise_sigma(buf)


def test_zero_fraction_gate_sits_between_three_channels_and_mono():
    """The gate: a sampled zero fraction below 0.3 gives 0; at or above it the estimate is used.
    Built on the every-7th sample itself: 1000 sampled values, 290 or 310 of them exactly 0."""
    rng = np.random.default_rng(14)
    for zeros, want_zero in ((290, True), (310, False)):
        buf = rng.uniform(1e-3, 2e-3, size=7000)
        buf[::7][:zeros] = 0.0
        assert float(np.mean(buf[::7] == 0.0)) == zeros / 1000
        est = _upper_half_sigma(buf)
        assert (est == 0.0) is want_zero, (zeros, est)


def _placed_and_neighbours(field):
    w, h = field.w, field.h
    placed = parallax.place_stars(field.catalogue, lambda ra, dec: core.tan_project(ra, dec, field.wcs), w, h)
    nb = []
    for s in field.catalogue:
        p = core.tan_project(s["ra"], s["dec"], field.wcs)
        if 0 <= p.x < w and 0 <= p.y < h:
            nb.append({"source": s, "x": p.x, "y": p.y})
    return placed, nb


def _faint_star_layer(nc: int, n_bright: int, n_faint: int, faint_peak: float, seed: int, noise: float,
                      w: int = 320, h: int = 180, fwhm: float = 2.5) -> tuple[list[np.ndarray], np.ndarray]:
    """A stars layer: bright stars (peak 0.15-0.9) and faint stars at ``faint_peak``, each channel with
    independent Gaussian noise clipped at 0 (what unscreen leaves). Returns (planes, faint centres (n, 2))."""
    rng = np.random.default_rng(seed)
    n = n_bright + n_faint
    xs = rng.uniform(6, w - 6, n)
    ys = rng.uniform(6, h - 6, n)
    peaks = np.concatenate([rng.uniform(0.15, 0.9, n_bright), np.full(n_faint, faint_peak)])
    sig = fwhm / 2.3548200450309493
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    st = np.zeros((h, w))
    for x, y, p in zip(xs, ys, peaks):
        st += p * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sig * sig))
    planes = [np.maximum(0.0, st + rng.normal(0.0, noise, st.shape)).astype(np.float32) for _ in range(nc)]
    return planes, np.stack([xs[n_bright:], ys[n_bright:]], axis=1)


def _faint_recall(dets, faint: np.ndarray) -> int:
    """How many faint stars have a detection within 0.75 px."""
    pts = np.array([[d.x, d.y] for d in dets], dtype=np.float64).reshape(-1, 2)
    if len(pts) == 0:
        return 0
    return int(np.sum(_nearest(pts, faint) <= 0.75))


# Pinned on the code before the R2-0 fix (eb8515b, detect.py as in a242afd): faint-star recall out of 40 per seed.
# The fix must keep these within one star (the zero-fraction gate makes them identical by construction).
_FAINT_NC3_BASE = {1: 25, 2: 29}    # three channels, 150 bright, 40 faint at 5 sigma (peak 5e-3), noise 1e-3
_FAINT_NOISELESS_MONO_BASE = {1: 28, 2: 31}  # one channel, noiseless, 150 bright, 40 faint at peak 3e-3


@pytest.mark.parametrize("seed", [1, 2])
def test_faint_stars_in_a_crowded_three_channel_field_are_kept(seed):
    planes, faint = _faint_star_layer(3, 150, 40, 5e-3, seed, noise=1e-3)
    dets = detect_stars(planes)
    assert abs(_faint_recall(dets, faint) - _FAINT_NC3_BASE[seed]) <= 1


@pytest.mark.parametrize("seed", [1, 2])
def test_faint_stars_in_a_crowded_noiseless_mono_field_are_kept(seed):
    planes, faint = _faint_star_layer(1, 150, 40, 3e-3, seed, noise=0.0)
    dets = detect_stars(planes)
    assert abs(_faint_recall(dets, faint) - _FAINT_NOISELESS_MONO_BASE[seed]) <= 1


# Measured with the R2-0 estimator (P65) on noisy mono, one channel, 40 bright, 40 faint at 10 sigma, noise 1e-3:
# recall (out of 40) per seed: 31 and 21, with 62-71 detections (BASE: 2763-3102, all noise). The estimate is biased
# high by star light, so this is a floor, not the ideal (a true-sigma threshold reaches 83% on 100-star fields; see e3a.md).
_NOISY_MONO_RECALL_MEASURED = {1: 31, 2: 21}


@pytest.mark.parametrize("seed", [1, 2])
def test_faint_stars_in_a_noisy_mono_field_are_found_without_a_flood(seed):
    planes, faint = _faint_star_layer(1, 40, 40, 1e-2, seed, noise=1e-3)
    dets = detect_stars(planes)
    assert len(dets) <= 2 * 80, len(dets)
    assert _faint_recall(dets, faint) >= _NOISY_MONO_RECALL_MEASURED[seed] - 4


@pytest.mark.perf
def test_noisy_mono_1080p_sprites_build_fast(synth_field):
    """The stalled case (nc=1, 1920x1080, noise 1e-3, fwhm 12, 30 stars): the sprite build takes < 60 s, ~30 sprites."""
    import time

    f = synth_field(w=1920, h=1080, n_stars=30, nc=1, seed=1, fwhm=12, noise=1e-3)
    placed, nb = _placed_and_neighbours(f)
    t0 = time.perf_counter()
    layer = starlayer.sprites(f.stars, placed, 12, nb)
    elapsed = time.perf_counter() - t0
    print("starlayer.sprites 1920x1080 nc=1 noise 1e-3 fwhm 12: %.2f s, %d sprites" % (elapsed, len(layer.sprites)))
    assert elapsed < 60.0, elapsed
    assert 25 <= len(layer.sprites) <= 30, len(layer.sprites)
