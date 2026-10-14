"""loomsiril/loom/residuals.py: the residual verdict (oracle replay) and the measurement on synthetic stars.

The verdict is pinned to Loom's residualVerdict through tests/oracle/loom_residuals.json (lib/Steps.js:656-783). The
measurement is checked on synthetic fields whose truth is known exactly: plain TAN, a forward SIP distortion of
order 3 (the Siril default), noisy deviations with chance matches, and deviations chosen to pin the clipping. The run
level skip (an unreachable catalogue, a header outside the contract, a bad centre, a SIP header that cannot be used)
is tested with fake catalogue sources. No socket is opened; the suite's guard enforces it.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from loomsiril import wcs as W
from loomsiril.loom import residuals as R

# The inputs of tools/oracle/loom_residuals.js, by the case names recorded there (JSON cannot spell them).
ORACLE_INPUTS: dict[str, Any] = {
    "published_recursive_spline": 0.091,
    "published_old_solver_bar": 0.315,
    "just_past_bar": 0.316,
    "well_past_bar": 1.5,
    "at_matching_tolerance": 3.0,
    "beyond_matching_tolerance": 12,
    "perfect": 0,
    "nan": math.nan,
    "undefined": None,
    "null": None,
    "string_0_1": "0.1",
    "negative_one": -1,
    "infinity": math.inf,
    "negative_infinity": -math.inf,
    "wbpp_master_real": 0.0190,
    "recursive_spline_master_real": 0.0157,
    "tiny_positive": 1e-9,
    "just_below_bar": 0.31499999,
    "just_below_bad": 2.9999999,
}

# Forward SIP terms (p, q, coefficient) as the FITS file stores them (bottom-up rows). The distortion is 1 to 2 px at
# the corners of the synthetic 1000 x 800 field, so a solution that ignores it reads "poor" (median about 0.8 px).
SIP_A = [(2, 0, 8.0e-6), (0, 2, -5.0e-6), (1, 1, 3.0e-6), (3, 0, 3.0e-9)]
SIP_B = [(2, 0, 3.0e-6), (1, 1, -6.0e-6), (0, 2, 6.0e-6), (0, 3, -3.0e-9)]


# ---------------------------------------------------------------- headers and synthetic fields


def _cd(scale_arcsec: float, rot_deg: float) -> tuple[float, float, float, float]:
    s = scale_arcsec / 3600.0
    t = math.radians(rot_deg)
    return (s * math.cos(t), -s * math.sin(t), s * math.sin(t), s * math.cos(t))


def _header(crval1: float = 150.0, crval2: float = 2.0, crpix1: float = 500.5, crpix2: float = 400.5,
            nx: int = 1000, ny: int = 800) -> dict[str, Any]:
    """A top-down TAN header (rotated CD matrix), as loomsiril.io_fits.read_fits returns it (LOOMROWS set)."""
    cd = _cd(1.2, 20.0)
    # io_fits flips the CD keywords for the top-down rows; the CD below is the top-down one for a bottom-up CD.
    top = W.flip_rows({"crval1": crval1, "crval2": crval2, "crpix1": crpix1, "crpix2": crpix2, "cd": list(cd)}, ny)
    return {
        "NAXIS1": nx, "NAXIS2": ny, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
        "CRVAL1": crval1, "CRVAL2": crval2, "CRPIX1": top["crpix1"], "CRPIX2": top["crpix2"],
        "CD1_1": top["cd"][0], "CD1_2": top["cd"][1], "CD2_1": top["cd"][2], "CD2_2": top["cd"][3],
        "LOOMROWS": "top-down",
    }


def _sip_keys(a: list[tuple[int, int, float]], b: list[tuple[int, int, float]], order: int = 3) -> dict[str, Any]:
    keys: dict[str, Any] = {"A_ORDER": order, "B_ORDER": order, "CTYPE1": "RA---TAN-SIP", "CTYPE2": "DEC--TAN-SIP"}
    for p, q, c in a:
        keys[f"A_{p}_{q}"] = c
    for p, q, c in b:
        keys[f"B_{p}_{q}"] = c
    return keys


def _sip_header_top_down(nx: int = 1000, ny: int = 800) -> dict[str, Any]:
    """The SIP-3 field as a Siril file gives it, read onto top-down rows: CD and CRPIX2 flipped, SIP kept as stored."""
    hdr = _header(nx=nx, ny=ny)
    hdr.update(_sip_keys(SIP_A, SIP_B))
    return hdr


def _forward_bottom(x: np.ndarray, y: np.ndarray) -> list[dict[str, float]]:
    """Truth, written independently of the module: bottom-up pixels -> forward SIP -> CD -> TAN sky (FITS convention)."""
    crpix1, crpix2 = 500.5, 400.5
    hb = {"crval1": 150.0, "crval2": 2.0, "crpix1": crpix1, "crpix2": crpix2, "cd": list(_cd(1.2, 20.0))}
    u = x + 1.0 - crpix1
    v = y + 1.0 - crpix2
    up = u + sum(c * u ** p * v ** q for p, q, c in SIP_A)
    vp = v + sum(c * u ** p * v ** q for p, q, c in SIP_B)
    un = W.unprojector(hb)
    return [un(float(xx), float(yy)) for xx, yy in zip(up + crpix1 - 1.0, vp + crpix2 - 1.0)]


def _sip_field(n: int = 200, seed: int = 4) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """Detections and catalogue on the SIP-3 field, the detections as a top-down reader sees them: (stars, catalogue).

    The truth is a bottom-up pixel (x, y); its top-down row is H - 1 - y, and the Siril pixel-edge convention adds 0.5.
    """
    rng = np.random.default_rng(seed)
    nx, ny = 1000, 800
    cells = np.array([(x, y) for x in range(40, nx - 39, 40) for y in range(40, ny - 39, 40)], dtype=float)
    pick = cells[rng.choice(len(cells), size=n, replace=False)]
    xb = pick[:, 0] + rng.uniform(-5, 5, n)              # 40 px cells with 5 px jitter: no two stars are close
    yb = pick[:, 1] + rng.uniform(-5, 5, n)
    cat = _forward_bottom(xb, yb)
    stars = [{"xpos": float(x) + 0.5, "ypos": float(ny - 1 - y) + 0.5} for x, y in zip(xb, yb)]
    return stars, cat


def _synthetic(n: int, header: dict[str, Any], seed: int = 1) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """n catalogue stars in the frame of a plain TAN header and the Siril detections at their true pixel centres."""
    rng = np.random.default_rng(seed)
    w = W.keyword_wcs(header)
    assert w is not None
    un = W.unprojector(w)
    nx, ny = int(header["NAXIS1"]), int(header["NAXIS2"])
    xs = rng.uniform(5, nx - 5, n)
    ys = rng.uniform(5, ny - 5, n)
    cat = []
    stars = []
    for x, y in zip(xs, ys):
        radec = un(float(x), float(y))
        cat.append({"ra": radec["ra"], "dec": radec["dec"], "G": 12.0})
        stars.append({"xpos": float(x) + 0.5, "ypos": float(y) + 0.5})
    return stars, cat


def _grid_field(devs: list[float], header: dict[str, Any], seed: int = 0) -> tuple[list[dict[str, float]],
                                                                                  list[dict[str, float]]]:
    """One catalogue star per grid cell (60 px apart, so no two are close) and a detection displaced by devs[i].

    The displacement direction is random and its length is devs[i], so each pair's deviation is exactly devs[i] and
    no other catalogue star is within the tolerance of a detection.
    """
    rng = np.random.default_rng(seed)
    w = W.keyword_wcs(header)
    assert w is not None
    un = W.unprojector(w)
    cat, stars = [], []
    for i, d in enumerate(devs):
        x = 30.0 + 60.0 * (i % 16)
        y = 30.0 + 60.0 * (i // 16)
        radec = un(x, y)
        cat.append({"ra": radec["ra"], "dec": radec["dec"], "G": 12.0})
        th = rng.uniform(0, 2 * math.pi)
        stars.append({"xpos": x + d * math.cos(th) + 0.5, "ypos": y + d * math.sin(th) + 0.5})
    return stars, cat


def _reference_median(stars: list[dict[str, float]], cat: list[dict[str, float]], header: dict[str, Any],
                      tol: float = 3.0, k: float = 5.0) -> tuple[float, int]:
    """Brute-force reference of the TAN-only statistic: nearest catalogue star, tolerance, clip, median (no cKDTree)."""
    w = W.keyword_wcs(header)
    assert w is not None
    p = W.projector(w)
    cxy = np.array([[p(c["ra"], c["dec"]).x, p(c["ra"], c["dec"]).y] for c in cat])
    sxy = np.array([[s["xpos"] - 0.5, s["ypos"] - 0.5] for s in stars])
    d = np.sqrt(((sxy[:, None, :] - cxy[None, :, :]) ** 2).sum(-1)).min(axis=1)
    dev = d[d <= tol]
    keep = np.ones(dev.shape, bool)
    for _ in range(100):
        sub = dev[keep]
        m = float(np.median(sub))
        sig = max(1.4826 * float(np.median(np.abs(sub - m))), 0.01)
        new = dev <= m + k * sig
        if np.array_equal(new, keep):
            break
        keep = new
    return float(np.median(dev[keep])), int(keep.sum())


def _shift_catalogue(cat: list[dict[str, float]], header: dict[str, Any], dx: float) -> list[dict[str, float]]:
    """The catalogue moved by dx px on the undistorted grid of the header (the header itself is left alone).

    A shift of CRPIX would also move the SIP distortion, which is taken about CRPIX, so the shift is put in the catalogue.
    """
    w = W.keyword_wcs(header)
    assert w is not None
    p = W.projector(w)
    un = W.unprojector(w)
    out = []
    for c in cat:
        q = p(c["ra"], c["dec"])
        radec = un(q.x + dx, q.y)
        out.append(dict(c, ra=radec["ra"], dec=radec["dec"]))
    return out


def _rayleigh_field(n_pairs: int, n_chance: int, seed: int) -> tuple[list[dict[str, float]], list[dict[str, float]],
                                                                     dict[str, Any]]:
    """A 200 x 200 field: n_pairs detections with Gaussian centroid noise (0.2 px per axis) and n_chance detections
    that have no partner (they pair with their nearest catalogue star when it is within 3 px, which is a chance match)."""
    rng = np.random.default_rng(seed)
    header = _header(nx=200, ny=200, crpix1=100.5, crpix2=100.5)
    un = W.unprojector(W.keyword_wcs(header) or {})
    cat, stars = [], []
    for _ in range(n_pairs):
        x, y = rng.uniform(10, 190), rng.uniform(10, 190)
        radec = un(float(x), float(y))
        cat.append({"ra": radec["ra"], "dec": radec["dec"], "G": 13.0})
        dx, dy = rng.normal(0, 0.2, 2)
        stars.append({"xpos": x + dx + 0.5, "ypos": y + dy + 0.5})
    for _ in range(n_chance):
        x, y = rng.uniform(10, 190), rng.uniform(10, 190)
        stars.append({"xpos": x + 0.5, "ypos": y + 0.5})
    return stars, cat, header


# ---------------------------------------------------------------- the verdict (oracle replay)


def test_thresholds_are_loom_values(oracle: Any) -> None:
    c = oracle("loom_residuals")["cases"]
    assert R.RESIDUALS_WARN_PX == c["warn_px"] == 0.315
    assert R.RESIDUALS_BAD_PX == c["bad_px"] == 3.0


def test_verdicts_match_loom_residual_verdict(oracle: Any) -> None:
    c = oracle("loom_residuals")["cases"]
    recorded = {k: v for k, v in c.items() if k not in ("warn_px", "bad_px")}
    assert set(recorded) == set(ORACLE_INPUTS)
    for name, want in recorded.items():
        assert R.residual_verdict(ORACLE_INPUTS[name]) == want, name


@pytest.mark.parametrize("value", [True, False, "0.1", None, math.nan, -0.5, -math.inf, math.inf, [0.1]])
def test_values_without_a_number_have_no_verdict(value: Any) -> None:
    assert R.residual_verdict(value) == "unknown"


def test_numpy_scalars_are_numbers() -> None:
    assert R.residual_verdict(np.float64(0.4)) == "poor"
    assert R.residual_verdict(np.float32(0.05)) == "ok"
    assert R.residual_verdict(np.int64(3)) == "bad"


def test_config_values_are_pinned_by_literal() -> None:
    assert R.RESIDUALS_CONFIG == {"magnitude": 16.0, "matchingTolerance": 3.0, "rejectionSigma": 5.0}
    assert R.MIN_RETAINED == 5 and R.SIGMA_FLOOR_PX == 0.01 and R.CATALOGUE_FACTOR == 1.5


# ---------------------------------------------------------------- the measurement, plain TAN


def test_known_crpix_offset_is_measured_to_0_01_px() -> None:
    truth = _header()
    stars, cat = _synthetic(300, truth)
    shifted = dict(truth, CRPIX1=truth["CRPIX1"] + 0.4)     # the solution is off by 0.4 px in x
    r = R.measure_residual(stars, shifted, cat)
    assert r["n_stars"] == 300 and r["n_matched"] == 300 and r["n_retained"] == 300
    assert abs(r["median_px"] - 0.4) < 0.01


def test_an_exact_solution_measures_zero() -> None:
    truth = _header()
    stars, cat = _synthetic(120, truth, seed=7)
    r = R.measure_residual(stars, truth, cat)
    assert r["n_matched"] == 120
    assert r["median_px"] < 1e-6


def test_outliers_within_the_tolerance_are_clipped_out_of_the_median() -> None:
    truth = _header()
    stars, cat = _synthetic(300, truth, seed=3)
    bad = [{"xpos": s["xpos"] + 2.0, "ypos": s["ypos"]} for s in stars[:10]]   # 2.0 px off, inside 3.0 px
    r = R.measure_residual(stars + bad, truth, cat)
    assert r["n_stars"] == 310 and r["n_matched"] == 310
    assert r["n_retained"] == 300                       # the ten displaced detections are clipped
    assert r["median_px"] < 0.01


def test_stars_outside_the_tolerance_do_not_match() -> None:
    truth = _header()
    stars, cat = _synthetic(50, truth, seed=5)
    far = [{"xpos": s["xpos"] + 3.2, "ypos": s["ypos"]} for s in stars[:5]]     # 3.2 px off, beyond 3.0 px
    r = R.measure_residual(stars[5:] + far, truth, cat)
    assert r["n_stars"] == 50 and r["n_matched"] == 45


def test_no_catalogue_or_no_stars_gives_no_median() -> None:
    truth = _header()
    stars, cat = _synthetic(20, truth, seed=9)
    for stars_in, cat_in in ((stars, []), ([], cat)):
        r = R.measure_residual(stars_in, truth, cat_in)
        assert math.isnan(r["median_px"]) and r["n_matched"] == 0
        assert R.residual_verdict(r["median_px"]) == "unknown"


def test_a_header_without_a_tan_wcs_is_refused() -> None:
    stars, cat = _synthetic(5, _header(), seed=2)
    with pytest.raises(ValueError, match="TAN WCS"):
        R.measure_residual(stars, {"NAXIS1": 1000, "NAXIS2": 800}, cat)


def test_fewer_than_five_retained_pairs_give_no_statistic() -> None:
    truth = _header()
    shifted = dict(truth, CRPIX1=truth["CRPIX1"] + 0.4)
    stars, cat = _synthetic(4, truth, seed=21)
    r = R.measure_residual(stars, shifted, cat)
    assert r["n_matched"] == 4 and r["n_retained"] == 4
    assert math.isnan(r["median_px"])
    assert R.residual_verdict(r["median_px"]) == "unknown"
    stars5, cat5 = _synthetic(5, truth, seed=21)
    r5 = R.measure_residual(stars5, shifted, cat5)
    assert abs(r5["median_px"] - 0.4) < 0.01


def test_a_non_finite_catalogue_record_is_ignored() -> None:
    truth = _header()
    stars, cat = _synthetic(60, truth, seed=31)
    dirty = cat + [{"ra": math.nan, "dec": 2.0, "G": 9.0}, {"ra": 150.0, "dec": math.inf, "G": 9.0},
                   {"ra": math.inf, "dec": 2.0, "G": 9.0}]
    shifted = dict(truth, CRPIX1=truth["CRPIX1"] + 0.4)
    assert R.measure_residual(stars, shifted, dirty) == R.measure_residual(stars, shifted, cat)


def test_sirilpy_style_objects_are_read_by_attribute() -> None:
    class PSF:
        def __init__(self, xpos: float, ypos: float) -> None:
            self.xpos, self.ypos = xpos, ypos

    truth = _header()
    stars, cat = _synthetic(40, truth, seed=41)
    objs = [PSF(s["xpos"], s["ypos"]) for s in stars]
    shifted = dict(truth, CRPIX1=truth["CRPIX1"] + 0.4)
    assert R.measure_residual(objs, shifted, cat) == R.measure_residual(stars, shifted, cat)


# ---------------------------------------------------------------- the measurement, pinned by constructed deviations


def test_median_is_taken_after_clipping_not_the_mean() -> None:
    # 50 at 0.05, 20 at 0.2, 15 at 0.3, 15 at 0.6 px: median 0.125, mean 0.20; nothing is clipped at 5 sigma.
    devs = [0.05] * 50 + [0.2] * 20 + [0.3] * 15 + [0.6] * 15
    stars, cat = _grid_field(devs, _header())
    r = R.measure_residual(stars, _header(), cat)
    assert r["n_matched"] == 100 and r["n_retained"] == 100
    assert r["median_px"] == pytest.approx(0.125, abs=1e-6)


def test_rejection_sigma_is_five_not_one() -> None:
    # At 1 sigma the 0.3 and 0.6 deviations would go (and the median would fall to 0.05); at 5 sigma they stay.
    devs = [0.05] * 50 + [0.2] * 20 + [0.3] * 15 + [0.6] * 15
    stars, cat = _grid_field(devs, _header())
    r = R.measure_residual(stars, _header(), cat)
    assert r["n_retained"] == 100


def test_clipping_is_one_sided_on_the_large_tail_only() -> None:
    # 70 pairs at 0.5 px and 30 perfect pairs: the perfect ones are below the median, so they are kept.
    devs = [0.5] * 70 + [0.0] * 30
    stars, cat = _grid_field(devs, _header())
    r = R.measure_residual(stars, _header(), cat)
    assert r["n_retained"] == 100
    assert r["median_px"] == pytest.approx(0.5, abs=1e-6)


def test_the_robust_sigma_is_floored_at_a_hundredth_of_a_pixel() -> None:
    # Identical deviations give MAD 0, so only the floor (0.01 px) keeps the 0.52 px pairs (0.5 + 5 * 0.01 = 0.55).
    devs = [0.5] * 70 + [0.52] * 30
    stars, cat = _grid_field(devs, _header())
    r = R.measure_residual(stars, _header(), cat)
    assert r["n_retained"] == 100
    assert r["median_px"] == pytest.approx(0.5, abs=1e-6)


def test_the_clipping_iterates_to_a_fixed_point() -> None:
    # One clipping round keeps 9 pairs (median 0.17); iterated to a fixed point it keeps 8 (median 0.155).
    devs = [0.01, 0.05, 0.09, 0.14, 0.17, 0.2, 0.23, 0.39, 0.8, 1.97, 2.11, 2.28]
    stars, cat = _grid_field(devs, _header())
    r = R.measure_residual(stars, _header(), cat)
    assert r["n_matched"] == 12
    assert r["n_retained"] == 8
    assert r["median_px"] == pytest.approx(0.155, abs=1e-6)


def test_noisy_field_with_chance_matches_agrees_with_the_brute_force_reference() -> None:
    stars, cat, header = _rayleigh_field(n_pairs=150, n_chance=200, seed=11)
    r = R.measure_residual(stars, header, cat)
    want_median, want_kept = _reference_median(stars, cat, header)
    assert r["n_matched"] > 150                          # chance matches are really in the statistic
    assert r["median_px"] == pytest.approx(want_median, abs=1e-9)
    assert r["n_retained"] == want_kept


# ---------------------------------------------------------------- SIP (Siril writes SIP order 3 by default)


def test_sip_field_with_its_own_distortion_reads_zero() -> None:
    stars, cat = _sip_field()
    r = R.measure_residual(stars, _sip_header_top_down(), cat)
    assert r["n_matched"] == 200 and r["n_retained"] == 200
    assert r["median_px"] < 1e-6


def test_a_known_shift_on_the_sip_field_reads_the_shift() -> None:
    stars, cat = _sip_field()
    hdr = _sip_header_top_down()
    r = R.measure_residual(stars, hdr, _shift_catalogue(cat, hdr, 0.4))
    assert r["n_matched"] == 200
    assert abs(r["median_px"] - 0.4) < 0.01


def test_a_shift_is_read_back_through_the_local_inverse_jacobian_of_the_sip_map() -> None:
    # A 2 px shift on the undistorted grid reads on the detector as J^-1 (2, 0) at each star, where J is the Jacobian
    # of the forward SIP map (bottom-up rows; the row flip leaves this length unchanged). Its spread over the field is
    # about 1 %, so ignoring J moves the median by about 0.02 px.
    stars, cat = _sip_field()
    hdr = _sip_header_top_down()
    r = R.measure_residual(stars, hdr, _shift_catalogue(cat, hdr, 2.0))
    xs = np.array([s["xpos"] - 0.5 for s in stars])
    ys = np.array([800 - 1 - (s["ypos"] - 0.5) for s in stars])          # bottom-up rows
    u = xs + 1.0 - 500.5
    v = ys + 1.0 - 400.5
    resid = []
    for i in range(len(stars)):
        j = np.eye(2)
        for p, q, c in SIP_A:
            if p:
                j[0, 0] += c * p * u[i] ** (p - 1) * v[i] ** q
            if q:
                j[0, 1] += c * q * u[i] ** p * v[i] ** (q - 1)
        for p, q, c in SIP_B:
            if p:
                j[1, 0] += c * p * u[i] ** (p - 1) * v[i] ** q
            if q:
                j[1, 1] += c * q * u[i] ** p * v[i] ** (q - 1)
        resid.append(float(np.hypot(*np.linalg.solve(j, np.array([2.0, 0.0])))))
    assert r["n_retained"] == 200
    assert r["median_px"] == pytest.approx(float(np.median(resid)), abs=1e-6)


def test_ignoring_the_sip_distortion_reads_poor_on_the_same_field() -> None:
    stars, cat = _sip_field()
    hdr = _sip_header_top_down()
    tan_only = {k: v for k, v in hdr.items() if not (k[:2] in ("A_", "B_") or k in ("CTYPE1", "CTYPE2"))}
    r = R.measure_residual(stars, tan_only, cat)
    assert 0.315 < r["median_px"] < 3.0
    assert R.residual_verdict(r["median_px"]) == "poor"
    with_sip = R.measure_residual(stars, hdr, cat)
    assert R.residual_verdict(with_sip["median_px"]) == "ok"


@pytest.mark.parametrize("edit", [
    lambda h: h.pop("A_ORDER"),                          # forward order missing
    lambda h: h.__setitem__("A_ORDER", 2),               # A_3_0 is above the order
    lambda h: h.__setitem__("B_ORDER", 1.5),             # not an integer order
    lambda h: h.__setitem__("B_0_3", math.nan),          # not finite
])
def test_a_malformed_sip_header_is_refused(edit: Any) -> None:
    stars, cat = _sip_field(n=20)
    hdr = _sip_header_top_down()
    edit(hdr)
    with pytest.raises(ValueError):
        R.measure_residual(stars, hdr, cat)


def test_a_siril_shaped_sip_header_is_read_with_its_zero_low_terms_and_inverse_terms() -> None:
    # Siril writes A_0_0, A_1_0, A_0_1 (and B_) as zeros next to the order 2-3 terms, plus inverse AP/BP terms, one of
    # them nonzero. The zeros are harmless and the inverse terms are not used: the field reads as ok, and ignoring the
    # distortion reads as poor (the pattern measured on a real Siril 1.4.4 solve).
    stars, cat = _sip_field()
    hdr = _sip_header_top_down()
    for k in ("A_0_0", "A_1_0", "A_0_1", "B_0_0", "B_1_0", "B_0_1"):
        hdr[k] = 0.0
    hdr.update({"AP_ORDER": 3, "BP_ORDER": 3, "AP_0_0": 2.0e-4, "BP_0_0": -1.0e-4, "AP_2_0": 1e-6, "BP_0_2": 1e-6})
    r = R.measure_residual(stars, hdr, cat)
    assert r["n_matched"] == 200 and r["median_px"] < 1e-6
    assert R.residual_verdict(r["median_px"]) == "ok"
    tan_only = {k: v for k, v in hdr.items() if not (k[:2] in ("A_", "B_", "AP", "BP") or k in ("CTYPE1", "CTYPE2"))}
    poor = R.measure_residual(stars, tan_only, cat)
    assert R.residual_verdict(poor["median_px"]) == "poor"


def test_inverse_only_sip_is_refused() -> None:
    stars, cat = _sip_field(n=20)
    hdr = {k: v for k, v in _sip_header_top_down().items() if k[:2] not in ("A_", "B_")}
    hdr.update({"AP_ORDER": 3, "BP_ORDER": 3, "AP_2_0": 1e-6, "BP_0_2": 1e-6})
    with pytest.raises(ValueError, match="SIP"):
        R.measure_residual(stars, hdr, cat)


# ---------------------------------------------------------------- the run-level checker


def _truth_gaia(cat: list[dict[str, float]], calls: list[Any] | None = None) -> Any:
    def gaia(centre: Any, radius: float, g_max: float) -> list[dict[str, float]]:
        if calls is not None:
            calls.append((centre, radius, g_max))
        return cat
    return gaia


def test_checker_reports_the_verdict_of_a_measured_offset() -> None:
    truth = _header()
    stars, cat = _synthetic(200, truth, seed=11)
    shifted = dict(truth, CRPIX1=truth["CRPIX1"] + 0.4)
    calls: list[Any] = []
    lines: list[str] = []
    out = R.ResidualChecker(_truth_gaia(cat, calls), lines.append).check("L", stars, shifted)
    assert out is not None and out["name"] == "L" and out["verdict"] == "poor"
    assert abs(out["median_px"] - 0.4) < 0.01
    assert lines == []
    (centre, radius, g_max), = calls
    assert set(centre) == {"ra", "dec"} and radius > 0
    assert g_max == 16.0


def test_checker_on_a_sip_field_gives_ok_for_the_exact_solution() -> None:
    stars, cat = _sip_field()
    lines: list[str] = []
    out = R.ResidualChecker(_truth_gaia(cat), lines.append).check("L", stars, _sip_header_top_down())
    assert out is not None and out["verdict"] == "ok" and lines == []


def test_unreachable_catalogue_is_skipped_once_per_run_and_never_raises() -> None:
    truth = _header()
    stars, _ = _synthetic(20, truth, seed=13)
    calls: list[int] = []
    lines: list[str] = []

    def unreachable(centre: Any, radius: float, g_max: float) -> None:
        calls.append(1)
        return None

    checker = R.ResidualChecker(unreachable, lines.append)
    assert checker.check("L", stars, truth) is None
    assert checker.check("R", stars, truth) is None
    assert len(lines) == 1 and "skipped" in lines[0]
    assert len(calls) == 1                       # the second image does not ask again
    assert checker.skipped


def test_a_raising_catalogue_counts_as_unreachable() -> None:
    truth = _header()
    stars, _ = _synthetic(20, truth, seed=17)
    lines: list[str] = []

    def broken(centre: Any, radius: float, g_max: float) -> None:
        raise OSError("connection refused")

    checker = R.ResidualChecker(broken, lines.append)
    assert checker.check("L", stars, truth) is None
    assert checker.check("L", stars, truth) is None
    assert len(lines) == 1 and "connection refused" in lines[0]


def test_a_header_outside_the_contract_is_skipped_once_per_run() -> None:
    truth = _header()
    stars, cat = _synthetic(20, truth, seed=19)
    calls: list[Any] = []
    lines: list[str] = []
    checker = R.ResidualChecker(_truth_gaia(cat, calls), lines.append)
    not_top_down = {k: v for k, v in truth.items() if k != "LOOMROWS"}
    assert checker.check("L", stars, not_top_down) is None
    assert checker.check("R", stars, not_top_down) is None
    assert len(lines) == 1 and calls == [] and checker.skipped
    # A raw CDELT + PC header (no CD keywords) is the same kind of skip, also logged once.
    raw = {"NAXIS1": 1000, "NAXIS2": 800, "CRVAL1": 150.0, "CRVAL2": 2.0, "CRPIX1": 500.5, "CRPIX2": 400.5,
           "CDELT1": -3e-4, "CDELT2": 3e-4, "PC1_1": 1.0, "PC2_2": 1.0, "LOOMROWS": "top-down"}
    lines2: list[str] = []
    checker2 = R.ResidualChecker(_truth_gaia(cat, calls), lines2.append)
    assert checker2.check("L", stars, raw) is None
    assert checker2.check("L", stars, raw) is None
    assert len(lines2) == 1 and calls == []


def test_a_bad_centre_skips_only_that_image(monkeypatch: pytest.MonkeyPatch) -> None:
    truth = _header()
    stars, cat = _synthetic(20, truth, seed=23)
    calls: list[Any] = []
    lines: list[str] = []
    real_field = W.field
    state = {"bad": True}

    def field(wcs: Any, w: int, h: int) -> dict[str, Any]:
        out = real_field(wcs, w, h)
        if state["bad"]:
            return {"centre": {"ra": 1.0, "dec": 120.0}, "radius_deg": out["radius_deg"]}
        return out

    monkeypatch.setattr(R.W, "field", field)
    checker = R.ResidualChecker(_truth_gaia(cat, calls), lines.append)
    assert checker.check("L", stars, truth) is None
    assert not checker.skipped and calls == [] and len(lines) == 1
    state["bad"] = False
    out = checker.check("R", stars, truth)
    assert out is not None and out["verdict"] == "ok" and len(calls) == 1


def test_a_sip_header_that_cannot_be_used_gives_unknown_without_a_query() -> None:
    stars, cat = _sip_field(n=20)
    hdr = _sip_header_top_down()
    del hdr["A_ORDER"]
    calls: list[Any] = []
    lines: list[str] = []
    out = R.ResidualChecker(_truth_gaia(cat, calls), lines.append).check("L", stars, hdr)
    assert out is not None and out["verdict"] == "unknown" and math.isnan(out["median_px"])
    assert calls == [] and len(lines) == 1 and "SIP" in lines[0]


def test_the_checker_keeps_the_brightest_one_and_a_half_times_the_stars(monkeypatch: pytest.MonkeyPatch) -> None:
    truth = _header()
    stars, cat = _synthetic(10, truth, seed=29)
    faint = [{"ra": c["ra"], "dec": c["dec"], "G": 15.0 + i * 0.01} for i, c in enumerate(cat * 3)]
    bright = [dict(c, G=10.0 + 0.01 * i) for i, c in enumerate(cat)]
    records = faint + [{"ra": 1.0, "dec": math.nan, "G": 5.0}] + bright
    seen: list[list[Any]] = []
    real = R.measure_residual

    def spy(stars_in: Any, header: Any, catalogue: Any, tolerance_px: float = 3.0) -> dict[str, Any]:
        seen.append(list(catalogue))
        return real(stars_in, header, catalogue, tolerance_px)

    monkeypatch.setattr(R, "measure_residual", spy)
    R.ResidualChecker(lambda c, r, g: records, lambda line: None).check("L", stars, truth)
    kept = seen[0]
    assert len(kept) == math.ceil(1.5 * 10) == 15
    assert [c["G"] for c in kept] == sorted(c["G"] for c in kept)
    assert all(math.isfinite(c["ra"]) for c in kept)
    assert sum(1 for c in kept if c["G"] < 12.0) == 10       # all ten bright records are there
