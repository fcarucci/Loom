"""loomsiril.parallax against the JS oracle (tests/oracle/parallax.json) and the selftest checks."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from loomsiril import parallax as p
from loomsiril.core import Point
from loomsiril.jsmath import js_max

TOL = 1e-12


def _match(got: Any, want: Any, tol: float = TOL) -> bool:
    """Recursive comparison: dict keys, list lengths, NaN == NaN, None, bool before number."""
    if isinstance(want, dict):
        return isinstance(got, dict) and set(got) == set(want) and all(_match(got[k], want[k], tol) for k in want)
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and all(_match(a, b, tol) for a, b in zip(got, want))
    if want is None or isinstance(want, bool):
        return got is want or got == want
    if isinstance(want, float) and math.isnan(want):
        return isinstance(got, float) and math.isnan(got)
    if isinstance(want, float) and math.isinf(want):
        return got == want
    return math.isclose(got, want, rel_tol=tol, abs_tol=tol * 1e-3)


def _cases(oracle, name: str):
    return oracle("parallax")["cases"][name]


def _record_to_placed_dict(pl: p.Placed) -> dict[str, Any]:
    return {"source": pl.source, "d": pl.d, "x": pl.x, "y": pl.y}


def test_constants_match_js(oracle):
    assert p.PARALLAX_ZERO_POINT == 0.017
    assert [list(r) for r in p.SIGMA_TABLE] == [[15, 0.02], [17, 0.07], [17.6, 0.10]]
    assert p.ILLUMINATOR_MAX_G == 10 and p.ILLUMINATOR_MIN_RADIUS == 0.1
    assert p.CLUSTER_MIN_MEMBERS == 30 and p.CLUSTER_MIN_CONTRAST == 3 and p.CLUSTER_MIN_SIGNIFICANCE == 5
    assert p.CLUSTER_MAX_RADIUS == 0.45 and p.CLUSTER_PM_SCALE == 0.3 and p.CLUSTER_PLX_SCALE == 3
    assert p.CLUSTER_GROW == 1.5


def test_parallax_sigma_matches_js(oracle):
    for case in _cases(oracle, "parallaxSigma"):
        assert _match(p.parallax_sigma(*case["args"]), case["out"]), case


def test_parallax_sigma_table_numbers():
    assert p.parallax_sigma(12) == pytest.approx(0.02, abs=1e-9)
    assert p.parallax_sigma(17) == pytest.approx(0.07, abs=1e-9)
    assert 0.02 < p.parallax_sigma(16.5) < 0.07
    assert p.parallax_sigma(19) == pytest.approx(0.10, abs=1e-9)


def test_usable_parallax_matches_js(oracle):
    for case in _cases(oracle, "usableParallax"):
        assert _match(p.usable_parallax(*case["args"]), case["out"]), case


def test_usable_parallax_rules():
    assert p.usable_parallax({"plx": 1.0, "G": 12}) == pytest.approx(1.017, abs=1e-9)
    assert p.usable_parallax({"plx": 0.30, "G": 17}) is None      # below 5 sigma
    assert p.usable_parallax({"plx": -0.5, "G": 10}) is None      # negative
    assert p.usable_parallax({"plx": None, "G": 10}) is None      # missing
    assert p.usable_parallax({"plx": math.nan, "G": 10}) is None  # NaN blank
    assert p.usable_parallax({"plx": True, "G": 10}) is None      # a boolean is not a number


@pytest.mark.parametrize("name, fn", [
    ("illuminatorRadius", p.illuminator_radius), ("illuminatorDistance", p.illuminator_distance),
    ("clusterRadius", p.cluster_radius), ("defaultTravel", p.default_travel), ("clampTravel", p.clamp_travel),
])
def test_scalar_replays_oracle(oracle, name, fn):
    for case in _cases(oracle, name):
        assert _match(fn(*case["args"]), case["out"]), (name, case)


def test_cluster_samples_matches_js(oracle):
    for case in _cases(oracle, "clusterSamples"):
        got = p.cluster_samples(*case["args"])
        assert _match(got, case["out"]), case


def test_find_cluster_matches_js(oracle):
    for case in _cases(oracle, "findCluster"):
        got = p.find_cluster(*case["args"])
        assert _match(got, case["out"]), case


def test_refine_cluster_matches_js(oracle):
    for case in _cases(oracle, "refineCluster"):
        inside, seed, sa, sd = case["args"]

        def near(c, k, sa=sa, sd=sd):
            def test(x):
                da = (x["a"] - c["a"]) / sa
                dd = (x["d"] - c["d"]) / sd
                dp = (x["p"] - c["p"]) / (p.CLUSTER_PLX_SCALE * js_max(c["sp"], x["sp"]))
                return da * da + dd * dd + dp * dp <= k * k
            return test

        assert _match(p.refine_cluster(inside, seed, near), case["out"]), case


def test_place_stars_matches_js(oracle):
    for case in _cases(oracle, "placeStars"):
        sources, positions, w, h = case["args"]
        table = {(s["ra"], s["dec"]): (None if q is None else Point(q["x"], q["y"]))
                 for s, q in zip(sources, positions)}
        got = p.place_stars(sources, lambda ra, dec: table[(ra, dec)], w, h)
        assert _match([_record_to_placed_dict(x) for x in got], case["out"]), case


# ---------------------------------------------------------------------------
# Behaviour checks.
# ---------------------------------------------------------------------------


def test_constructed_cluster_is_found_at_its_distance(oracle):
    case = _cases(oracle, "findCluster")[0]
    sources, target, radius = case["args"]
    c = p.find_cluster(sources, target, radius)
    assert c is not None
    assert abs(c["distance"] - 1000 / 1.117) < 40
    assert c["members"] >= 40


def test_uniform_field_has_no_cluster(oracle):
    assert p.find_cluster(*_cases(oracle, "findCluster")[1]["args"]) is None


def test_cluster_radius_caps_big_nebulae():
    assert p.cluster_radius(0.2) == 0.2
    assert p.cluster_radius(85 / 60) == p.CLUSTER_MAX_RADIUS


def test_illuminator_picks_the_brightest_reliable_star():
    target = {"ra": 315.4, "dec": 68.16, "diameter": 18}
    sources = [
        {"ra": 315.41, "dec": 68.165, "G": 7.3, "plx": 2.8},
        {"ra": 316.9, "dec": 68.36, "G": 5.0, "plx": 9},
        {"ra": 315.38, "dec": 68.17, "G": 8.0, "plx": 0.01},
        {"ra": 315.43, "dec": 68.16, "G": 12.5, "plx": 1.1},
    ]
    d = p.illuminator_distance(sources, target)
    assert d is not None and d["G"] == 7.3
    assert d["distance"] == pytest.approx(1000 / (2.8 + p.PARALLAX_ZERO_POINT), abs=0.5)
    assert p.illuminator_distance([sources[3]], target) is None


def test_default_and_clamped_travel():
    assert p.default_travel("nebula", 900) == 180
    assert p.default_travel("galaxy", math.inf) == 200
    assert p.clamp_travel(950, "nebula", 900) == {"travel": 810, "clamped": True}
    assert p.clamp_travel(950, "galaxy", math.inf) == {"travel": 950, "clamped": False}


def test_place_stars_keeps_only_inside_usable_stars():
    srcs = [{"ra": 1, "dec": 1, "plx": 2.0, "G": 12}, {"ra": 2, "dec": 2, "plx": 0.01, "G": 17},
            {"ra": 3, "dec": 3, "plx": 1.0, "G": 12}]
    at = {1: Point(10, 10), 2: Point(20, 20), 3: Point(-5, 10)}
    placed = p.place_stars(srcs, lambda ra, dec: at[ra], 100, 100)
    assert len(placed) == 1
    assert placed[0].d == pytest.approx(1000 / 2.017, abs=1e-9)
    assert (placed[0].x, placed[0].y) == (10, 10)


# ---------------------------------------------------------------------------
# IC 1396 regression on the public Gaia fixture (selftest 14596-14606).
# ---------------------------------------------------------------------------

_FIELDS = ("ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP")


def _parse_gaia_tsv(path: Path) -> list[dict[str, float]]:
    """Gaia TSV (header ``ra dec plx pmra pmdec G BP RP``, tab separated): blank fields become NaN."""
    rows: list[dict[str, float]] = []
    with path.open(encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        assert header == list(_FIELDS)
        for line in fh:
            cells = line.rstrip("\n").split("\t")
            if len(cells) < len(_FIELDS):
                continue
            rows.append({k: (float(v) if v.strip() else math.nan) for k, v in zip(_FIELDS, cells)})
    return rows


def test_ic1396_cluster_is_870_to_1000_pc(gaia_tsv_path):
    src = _parse_gaia_tsv(gaia_tsv_path)
    assert len(src) > 8000
    c = p.find_cluster(src, {"ra": 324.745, "dec": 57.514}, p.cluster_radius(85 / 60))
    assert c is not None, "IC 1396 cluster not found"
    assert 870 <= c["distance"] <= 1000, c
