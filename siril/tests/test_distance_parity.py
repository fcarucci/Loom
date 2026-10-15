"""The distance lookup is the one PixInsight's Loom makes, checked on a frozen real Gaia field (Sh2-132, the Lion).

The bug: for the open image's centre (RA 334.855821518434, Dec 56.027845769519) the Fly-Through dialog said "No distance
found ... Type one (pc)" while PixInsight finds the distance. tests/oracle/distance.json holds what Loom's own JavaScript
(Fly.findCluster, Fly.illuminatorDistance, Fly.gaiaOnlineUrl, run by tools/oracle/distance.js in node) returns on
tests/fixtures/sh2-132-gaia.json.gz: Gaia DR3 rows fetched live once (VizieR, G < 17.6, 1.35 deg round the image centre
and round the Sesame position), trimmed to the stars the distance search can use. The JS finds NOTHING when the target is
the image centre (the nearest star brighter than G 10 with a good parallax is 0.106 deg away, the G 8.8 one that
PixInsight's target finds 0.135 deg, and a nebula without a catalogued size is searched out to 0.1 deg) and 4,562 pc when the target is the Sesame position of Sh2-132 (0.072 deg away): the Gaia query
and both searches are identical in the port, what differed is the position searched round. Here the port is replayed on
the same stars and must give the same answers. Gaia is only ever a stub: no network."""

from __future__ import annotations

import gzip
import json
import math
import re
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from loomsiril import fly_distance as FD
from loomsiril import fly_model, fly_progress, gaia, parallax, pipeline, siril_bridge, skyname
from loomsiril import wcs as wcs_mod
from test_fly_distance import cache_dir  # noqa: F401
from test_fly_names import _dialog, _finish
from test_siril_script import _fake_tkinter, blocked_discover, script, stub  # noqa: F401
from test_skyname import SESAME

from conftest import FIXTURES_DIR

CENTRE = {"ra": 334.855821518434, "dec": 56.027845769519}
SESAME_POS = {"ra": 334.7875, "dec": 56.0791}

#: The FITS header of the user's image (Siril 1.4.4 writes CDELT + PC, no CD): the keys the WCS needs.
HEADER = {"NAXIS1": 11799, "NAXIS2": 7211, "CRPIX1": 5899.5, "CRPIX2": 3605.5,
          "CRVAL1": 334.855821518434, "CRVAL2": 56.027845769519, "CTYPE1": "RA---TAN-SIP", "CTYPE2": "DEC--TAN-SIP",
          "CDELT1": -0.000134129637957971, "CDELT2": 0.000134132199439419,
          "PC1_1": 0.999955968473764, "PC1_2": -0.00938408832523805,
          "PC2_1": 0.00951318470499197, "PC2_2": 0.999954748634541}


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with gzip.open(FIXTURES_DIR / "sh2-132-gaia.json.gz", "rt", encoding="utf-8") as fh:
        c = json.load(fh)["cols"]
    nan = lambda v: math.nan if v is None else v  # noqa: E731
    return [{"ra": c["ra"][i], "dec": c["dec"][i], "plx": nan(c["plx"][i]), "pmra": nan(c["pmra"][i]),
             "pmdec": nan(c["pmdec"][i]), "G": nan(c["G"][i]), "BP": math.nan, "RP": math.nan} for i in range(len(c["ra"]))]


@pytest.fixture(scope="module")
def js(request):
    from conftest import ORACLE_DIR

    path = ORACLE_DIR / "distance.json"
    if not path.exists():
        pytest.skip("oracle distance.json not recorded (run tools/record_oracle.js)")
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


class Stub:
    """A Gaia online stand-in over a star list: answers each cone query of the request with the stars inside it."""

    def __init__(self, stars: list[dict]) -> None:
        self.stars, self.urls = stars, []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        q = unquote(url.split("QUERY=", 1)[1])
        ra, dec, r = (float(v) for v in re.search(r"CIRCLE\('ICRS',([^,]+),([^,]+),([^)]+)\)", q).groups())
        gmax = float(re.search(r"Gmag < ([0-9.]+)", q).group(1))
        centre = {"ra": ra, "dec": dec}
        out = [",".join(gaia.GAIA_ONLINE_COLUMNS)]
        for s in self.stars:
            if s["G"] < gmax and parallax.separation(s, centre) <= r:
                out.append(",".join("" if math.isnan(s[k]) else repr(s[k]) for k in
                                    ("ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP")))
        return "\n".join(out) + "\n"


def lookup(stars, target, gaia_source="online", radius=None):
    return FD.resolve(target, "nebula", gaia_source, None, radius or 0.9273533489889884, fetch_text=Stub(stars))


# ---------------------------------------------------------------- the real field radius

def test_the_field_radius_of_the_real_header(js):
    wcs = siril_bridge.top_down_wcs(HEADER, 7211)
    centre, radius = pipeline._field(wcs, 11799, 7211)
    assert radius == pytest.approx(js["field"]["radiusDeg"], abs=1e-12)           # Sky.field, as Loom's JS computes it
    assert 0.92 < radius < 0.93                                                    # about 1.58 x 0.97 deg, half-diagonal 0.93
    assert radius == pytest.approx(0.5 * math.hypot(11799, 7211) * 0.000134131, rel=2e-3)
    assert (centre["ra"], centre["dec"]) == pytest.approx((js["field"]["centre"]["ra"], js["field"]["centre"]["dec"]), abs=1e-9)


def test_the_dialog_the_render_and_the_draft_use_one_field_radius(js):
    wcs = siril_bridge.top_down_wcs(HEADER, 7211)
    radius = pipeline._field(wcs, 11799, 7211)[1]
    assert FD.field_radius(wcs, 11799, 7211) == radius                              # what the Render / Draft worker passes
    assert skyname.sky_field(wcs, 11799, 7211)["radius_deg"] == radius              # what the dialog passes
    small = wcs_mod.scaled_wcs(wcs, 0.5)                                            # the render's working copy (Sky.workingCopy)
    assert pipeline._field(small, 5900, 3606)[1] == pytest.approx(radius, abs=2e-4)


# ---------------------------------------------------------------- the Gaia query is the JavaScript's

def test_the_gaia_query_parameters_are_the_javascripts(js):
    lim = js["limits"]
    assert pipeline.GAIA_MAG_LIMIT == lim["gaiaMagLimit"] == 17.6
    assert (parallax.ILLUMINATOR_MAX_G, parallax.ILLUMINATOR_MIN_RADIUS) == (lim["illuminatorMaxG"], lim["illuminatorMinRadius"])
    assert (parallax.CLUSTER_MAX_RADIUS, parallax.CLUSTER_MIN_MEMBERS) == (lim["clusterMaxRadius"], lim["clusterMinMembers"])
    assert (parallax.CLUSTER_MIN_CONTRAST, parallax.CLUSTER_MIN_SIGNIFICANCE) == (lim["clusterMinContrast"], lim["clusterMinSignificance"])
    assert (gaia.GAIA_ONLINE_URL, list(gaia.GAIA_ONLINE_COLUMNS), gaia.GAIA_ONLINE_MAX_ROWS) == \
        (lim["onlineUrl"], lim["onlineColumns"], lim["onlineMaxRows"])
    radius = js["field"]["radiusDeg"]
    for case in js["urls"]:
        t = case["target"]
        assert gaia.online_url(gaia.GaiaQuery.from_centre(t, 3 * parallax.cluster_radius(radius), pipeline.GAIA_MAG_LIMIT)) \
            == case["cluster"]
        assert gaia.online_url(gaia.GaiaQuery.from_centre(t, parallax.illuminator_radius(t), parallax.ILLUMINATOR_MAX_G)) \
            == case["illuminator"]
    fc = js["field"]["centre"]
    assert gaia.online_url(gaia.GaiaQuery.from_centre(fc, radius, pipeline.GAIA_MAG_LIMIT)) == js["urls"][0]["field"]


def test_the_requests_of_a_lookup_are_the_javascripts_cones(rows, js, cache_dir):
    stub_fetch = Stub(rows)
    FD.resolve(CENTRE, "nebula", "online", None, js["field"]["radiusDeg"], fetch_text=stub_fetch)
    assert stub_fetch.urls == [js["urls"][0]["cluster"], js["urls"][0]["illuminator"]]     # no row cap in the way: 500000


# ---------------------------------------------------------------- the same stars, the same answers

def close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.005 * b


@pytest.mark.parametrize("case,target", [("imageCentre", CENTRE), ("sesame", SESAME_POS)])
def test_the_port_finds_what_the_javascript_finds_on_the_frozen_gaia_rows(rows, js, case, target):
    want = js[case]
    inside = [s for s in rows if parallax.separation(s, target) <= 3 * parallax.cluster_radius(want["r"])]
    assert len(inside) == want["clusterSources"]
    got_cluster = parallax.find_cluster(inside, target, parallax.cluster_radius(want["r"]))
    field = [s for s in rows if parallax.separation(s, js["field"]["centre"]) <= js["field"]["radiusDeg"]]
    got_star = parallax.illuminator_distance(field, target)
    assert (got_cluster is None) == (want["cluster"] is None)
    assert (got_star is None) == (want["illuminator"] is None)
    if got_star is not None:
        assert close(got_star["distance"], want["illuminator"]["distance"]) and got_star["G"] == want["illuminator"]["G"]
    samples = parallax.cluster_samples(inside, target, parallax.cluster_radius(want["r"]))
    assert (len(samples["inside"]), len(samples["field"])) == (want["inside"], want["ring"])


def test_the_image_centre_has_nothing_and_the_javascript_agrees(js):
    c = js["imageCentre"]
    assert c["cluster"] is None and c["illuminator"] is None and c["illuminatorCandidates"] == 0
    assert js["sesame"]["illuminator"]["G"] == 8.819 and js["sesame"]["illuminator"]["distance"] == pytest.approx(4562.04, abs=0.01)


def test_the_resolver_gives_the_illuminating_star_at_the_object_like_the_javascript(rows, js, cache_dir):
    res = lookup(rows, SESAME_POS)
    assert res.code == "star" and res.source == "illuminating star G 8.8, Gaia online"
    assert close(res.distance, js["sesame"]["illuminator"]["distance"])
    assert fly_model.distance_line(res) == "Distance: ~4,562 pc (illuminating star G 8.8, Gaia online)"


def test_the_resolver_gives_a_cluster_like_the_javascript(rows, js, cache_dir):
    want = js["sesameWithCluster"]
    with_cluster = rows + [{**s, "BP": math.nan, "RP": math.nan} for s in want["injected"]]
    res = lookup(with_cluster, SESAME_POS)
    assert res.code == "cluster" and close(res.distance, want["out"]["cluster"]["distance"])
    assert res.source == f"cluster of {want['out']['cluster']['members']} stars, Gaia online"
    found = pipeline._find_distance(pipeline.FlyJob(starless=[], stars=[], wcs={}, target=SESAME_POS, gaia="online"), [],
                                    SESAME_POS, js["field"]["radiusDeg"],
                                    pipeline.RunContext(lambda *a: None, lambda: False, Stub(with_cluster)))
    assert found.cluster["members"] == want["out"]["cluster"]["members"]


# ---------------------------------------------------------------- when nothing is found, say what was searched

def test_the_failure_line_gives_the_radius_the_counts_and_the_limits(rows, js, cache_dir):
    res = lookup(rows, CENTRE)
    c = js["imageCentre"]
    assert res.distance is None
    assert res.reason == (
        "no star cluster and no bright illuminating star found: searched 1.35 deg round RA 334.8558, Dec +56.0278 "
        f"(the image field is 0.93 deg): {c['clusterSources']:,} Gaia sources (from Gaia online), {c['usable']:,} with a usable "
        f"parallax: no cluster of >= 30 members ({c['inside']:,} stars within 0.45 deg, {c['ring']:,} in the ring round it) "
        "and no star brighter than G 10 with a good parallax within 0.10 deg of the target (the nearest, G 8.0, is 0.106 deg away)")
    line = fly_model.distance_line(res)
    assert line.startswith("No distance found: no star cluster") and line.endswith("Type one (pc).")


def test_the_render_and_the_draft_say_the_same_thing(rows, js, cache_dir):
    res = lookup(rows, CENTRE)
    text = fly_progress.no_distance_text(res.reason, "Render")
    assert "searched 1.35 deg round RA 334.8558" in text and text.endswith("press Render again.")
    assert fly_progress.no_distance_text(res.reason, "Draft").endswith("press Draft again.")
    with pytest.raises(pipeline.NoDistanceError) as err:
        raise pipeline.NoDistanceError(res.reason)
    assert err.value.reason == res.reason


# ---------------------------------------------------------------- the dialog, Render and Draft ask the same question

class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, target, kind, gaia_source, typed, radius=None, **kw):
        self.calls.append((dict(target), kind, gaia_source, radius))
        return FD.DistanceResult(None, reason="x")


def test_dialog_render_and_draft_search_the_real_field_radius_round_the_same_target(stub, script, monkeypatch, blocked_discover):
    wcs = siril_bridge.top_down_wcs(HEADER, 7211)
    radius = pipeline._field(wcs, 11799, 7211)[1]
    rec = Recorder()
    monkeypatch.setattr(FD, "resolve", rec)
    names = skyname.NameContext(field=skyname.sky_field(wcs, 11799, 7211))
    dlg = _dialog(script, monkeypatch, blocked_discover, names, CENTRE)
    dlg._cancel_after("distance")
    dlg._start_distance()
    _finish(dlg, "distance")
    assert rec.calls[-1][3] == radius and rec.calls[-1][3] != FD.DEFAULT_FIELD_RADIUS      # not the 0.5 deg default
    for action in ("Render", "Draft"):                                                      # the worker's own call
        progress = fly_progress.FlyProgress()
        b = SimpleNamespace(wcs=wcs, width=11799, height=7211, target=CENTRE)
        values = {"target": CENTRE, "target_type": "nebula", "gaia": "cache", "distance": None}
        with pytest.raises(fly_progress.NoDistance):
            fly_progress.ensure_distance(values, b, progress, rec, action)
        assert rec.calls[-1] == (CENTRE, "nebula", "cache", radius)


# ---------------------------------------------------------------- the target is the object the name finds

def test_an_object_name_remembered_from_an_earlier_run_still_moves_the_target_to_the_object(stub, script, monkeypatch,
                                                                                        blocked_discover):
    """The dialog fills the Object name with what Sesame found, and the options remember it; next time the box is not empty
    and the found object must still become the target (it used to stay at the image centre: no star within 0.1 deg)."""
    monkeypatch.setattr(skyname, "resolved_names", {})
    monkeypatch.setattr(skyname, "fetch_text", lambda url, what="x": SESAME)
    names = skyname.NameContext(field={"centre": {"ra": 334.8555814931035, "dec": 56.0278470453104},
                                       "radius_deg": 0.9273533489889884}, header_object="SH2-132")
    dlg = _dialog(script, monkeypatch, blocked_discover, names, CENTRE, object_name="SH2-132")
    _finish(dlg, "names")
    assert (dlg._vars["ra"].get(), dlg._vars["dec"].get()) == ("334.7875", "56.0791")
    assert dlg._vars["object_name"].get() == "SH2-132"


def test_a_different_typed_object_name_keeps_its_own_target(stub, script, monkeypatch, blocked_discover):
    monkeypatch.setattr(skyname, "resolved_names", {})
    monkeypatch.setattr(skyname, "fetch_text", lambda url, what="x": SESAME)
    names = skyname.NameContext(field={"centre": {"ra": 334.8555814931035, "dec": 56.0278470453104},
                                       "radius_deg": 0.9273533489889884}, header_object="SH2-132")
    dlg = _dialog(script, monkeypatch, blocked_discover, names, CENTRE, object_name="My target")
    _finish(dlg, "names")
    assert (dlg._vars["ra"].get(), dlg._vars["dec"].get()) == (str(CENTRE["ra"]), str(CENTRE["dec"]))
