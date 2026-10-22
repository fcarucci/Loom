"""Published nebula estimates, both ports: no live requests in the suite."""
import json
import math
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from loomsiril import fly_distance, gaia, nebula_distance as N, pipeline

HELIX = {"ra": 337.41060585298, "dec": -20.83715201178}
ROW = ["NGC  7293", HELIX["ra"], HELIX["dec"], "PN", 199.505, "pc  ", "paral   ", "2020yCat.1350....0G", None]


def table(rows=None):
    return json.dumps({"metadata": [{"name": c} for c in N.COLUMNS], "data": [ROW] if rows is None else rows})


def changed(**kw):
    row = dict(zip(N.COLUMNS, ROW))
    row.update(kw)
    return [row[c] for c in N.COLUMNS]


def test_query_is_a_small_nebula_cone_not_a_field_star_search():
    url = urlsplit(N.query_url(HELIX))
    q = parse_qs(url.query)
    assert url.netloc == "simbad.cds.unistra.fr" and q["FORMAT"] == ["json"]
    assert "JOIN mesDistance" in q["QUERY"][0] and "b.otype IN" in q["QUERY"][0]
    assert f",{N.RADIUS}))" in q["QUERY"][0]


def test_helix_uses_the_newest_published_measurement_regardless_of_row_order():
    # SIMBAD measurements retrieved 2026-10-21, including mixed units.
    rows = [changed(dist=159, bibcode="2008ApJ...689..194S"),
            changed(dist=0.16, unit="kpc ", bibcode="2010ApJ...714.1096S"),
            changed(dist=200.9646, bibcode="2018yCat.1345....0G"), ROW]
    for order in (rows, rows[::-1]):
        found = N.parse(table(order), HELIX)
        assert found["distance"] == 199.505 and found["bibcode"] == "2020yCat.1350....0G"
        assert N.label(found) == "SIMBAD NGC  7293, 2020yCat.1350....0G"


@pytest.mark.parametrize("unit,factor", [("pc", 1), ("kpc", 1000), ("Mpc", 1000000)])
def test_unit_conversion(unit, factor):
    assert N.parse(table([changed(dist=2, unit=unit)]), HELIX)["distance"] == 2 * factor


@pytest.mark.parametrize("row", [changed(dist=0), changed(dist=-1), changed(dist=math.nan), changed(dist=None),
    changed(unit="ly"), changed(qual=">"), changed(qual="E"), changed(bibcode=""), changed(otype="*"),
    changed(ra=HELIX["ra"] + 1), changed(ra=None), changed(dec=math.nan)])
def test_invalid_estimates_and_unrelated_objects_are_refused(row):
    assert N.parse(table([row]), HELIX) is None


def test_two_nebulae_are_ambiguous_even_if_one_is_closer():
    assert N.parse(table([ROW, changed(main_id="Another nebula", ra=HELIX["ra"] + 0.001)]), HELIX) is None


@pytest.mark.parametrize("text", ["", "<html>error</html>", "{}", table([]), table([ROW] * N.MAX_ROWS)])
def test_empty_error_and_truncated_answers(text):
    assert N.parse(text, HELIX) is None


def test_transport_failure_is_a_miss():
    def offline(url):
        raise gaia.GaiaError("offline")
    assert N.lookup(HELIX, offline) is None


def test_catalog_precedes_gaia_and_typed_and_galaxy_values_precede_catalog(monkeypatch):
    def no_gaia(*a):
        pytest.fail("a catalog hit must not infer a distance from Gaia")
    monkeypatch.setattr(pipeline, "_lookup", no_gaia)
    urls = []
    def fetch(url):
        urls.append(url)
        return table()
    r = fly_distance.resolve(HELIX, "nebula", "online", fetch_text=fetch)
    assert r.code == "catalog" and r.distance == 199.505 and r.found_by_lookup
    assert "SIMBAD" in r.source and len(urls) == 1
    assert fly_distance.resolve(HELIX, typed=123, fetch_text=fetch).distance == 123
    assert fly_distance.resolve(HELIX, "galaxy", fetch_text=fetch).distance == math.inf
    assert len(urls) == 1
    # Exactly the displayed estimate/source is carried into a subsequent render.
    job = pipeline.FlyJob(starless=[], stars=[], wcs={}, distance=r.distance,
                          distance_source=r.code, distance_label=r.source)
    out = pipeline._find_distance(job, [], HELIX, 0.5, pipeline.RunContext(no_gaia, lambda: False, no_gaia))
    assert (out.distance, out.source, out.label) == (r.distance, r.code, r.source)


def test_cancelled_catalog_answer_is_not_used():
    r = fly_distance.resolve(HELIX, fetch_text=lambda url: table(), is_cancelled=lambda: True)
    assert r.distance is None and r.reason == "cancelled"


def test_python_and_pixinsight_select_the_same_estimates():
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    root = Path(__file__).resolve().parents[2]
    cases = [table(), table([changed(dist=0.2, unit="kpc")]), table([changed(qual="<")]),
             table([ROW, changed(main_id="other")]), table([]), "<error/>"]
    code = """const fs=require('fs'),vm=require('vm');
const c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
const x=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(x.cases.map(t=>c.Fly.parseNebulaDistance(t,x.target))));"""
    got = subprocess.run(["node", "-e", code, str(root / "pixinsight/script/lib/Fly.js")],
                         input=json.dumps(dict(cases=cases, target=HELIX)), text=True, capture_output=True, check=True)
    assert json.loads(got.stdout) == [N.parse(t, HELIX) for t in cases]
