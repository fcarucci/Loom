"""The distance the Fly-Through dialog finds and shows (loomsiril.fly_distance) and how the dialog uses it.

Gaia is always a stub (an injected fetch_text or a cache folder in tmp_path); nothing reaches the network and no window
opens (recording tk stand-ins). The pure resolver is tested on its own, then the dialog: the lookup runs on a worker
thread, a stale answer is dropped, the grey line and the placeholder say what was found, and the Draft and Render
options carry the distance the dialog showed."""

from __future__ import annotations

import math
import random
import threading
from pathlib import Path

import pytest

from loomsiril import fly_distance as FD
from loomsiril import fly_model, gaia, parallax, pipeline, siril_bridge
from test_fly_names import _dialog, _finish
from test_siril_script import _fake_tkinter, blocked_discover, script, stub  # noqa: F401
from test_skyname import FIELD

TARGET = {"ra": 334.7875, "dec": 56.0791}
HEADER = ",".join(gaia.GAIA_ONLINE_COLUMNS)


def csv(rows: list[tuple]) -> str:
    """Gaia rows (ra, dec, plx, pmra, pmdec, G) in the online CSV shape (BP, RP blank)."""
    return "\n".join([HEADER, *(f"{r[0]!r},{r[1]!r},{r[2]!r},{r[3]!r},{r[4]!r},{r[5]!r},," for r in rows)]) + "\n"


def cluster_rows(n: int = 80, plx: float = 0.5) -> list[tuple]:
    """A tight clump of stars sharing proper motion and parallax round TARGET, in a sparse random field."""
    rnd = random.Random(7)
    rows = []
    for _ in range(n):
        rows.append((TARGET["ra"] + rnd.gauss(0, 0.03), TARGET["dec"] + rnd.gauss(0, 0.03),
                     plx + rnd.gauss(0, 0.01), -3.0 + rnd.gauss(0, 0.05), -2.0 + rnd.gauss(0, 0.05), 12 + rnd.random() * 3))
    for _ in range(300):
        rows.append((TARGET["ra"] + rnd.uniform(-0.3, 0.3), TARGET["dec"] + rnd.uniform(-0.3, 0.3),
                     rnd.uniform(0.2, 3), rnd.uniform(-8, 8), rnd.uniform(-8, 8), 12 + rnd.random() * 3))
    return rows


STAR_ROW = (TARGET["ra"] + 0.01, TARGET["dec"], 1.5, 1.0, 1.0, 7.0)       # a bright star with a good parallax
EMPTY = csv([])


class Fetch:
    """A stub online fetch: one table for every request, every call recorded (and the calling thread)."""

    def __init__(self, text: str = EMPTY, error: Exception | None = None) -> None:
        self.text, self.error, self.urls, self.threads = text, error, [], []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        self.threads.append(threading.current_thread())
        if self.error is not None:
            raise self.error
        return self.text


@pytest.fixture(autouse=True)
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "gaia-cache"
    monkeypatch.setattr(gaia, "default_cache_dir", lambda: d)
    return d


def seed_empty_cache(cache_dir: Path, field_radius: float = 0.5) -> None:
    """A cache with a file (and no stars) for each cone the lookup asks for."""
    r = parallax.cluster_radius(field_radius)
    for q in (gaia.GaiaQuery.from_centre(TARGET, 3 * r, pipeline.GAIA_MAG_LIMIT),
              gaia.GaiaQuery.from_centre(TARGET, parallax.illuminator_radius(TARGET), parallax.ILLUMINATOR_MAX_G)):
        gaia._write_atomic(cache_dir / (gaia.cache_key(q) + ".csv"), EMPTY)


# ---------------------------------------------------------------- the pure resolver

def test_a_cluster_gives_its_distance_and_says_how_many_stars():
    res = FD.resolve(TARGET, "nebula", "online", None, 0.1, Fetch(csv(cluster_rows())))
    assert res.code == "cluster" and res.reason == ""
    assert res.distance == pytest.approx(1000 / (0.5 + 0.017), rel=0.01)      # the parallax zero point is added
    assert res.source == "cluster of 80 stars, Gaia online"
    assert res.found_by_lookup


def test_the_cluster_comes_from_the_same_lookup_the_render_uses(monkeypatch):
    """fly_distance asks pipeline._find_distance; a stubbed cluster record is what it reports."""
    monkeypatch.setattr(pipeline, "cluster_distance", lambda *a: {"distance": 1950.4, "members": 37})
    res = FD.resolve(TARGET, "nebula", "online", None, 0.5, Fetch())
    assert (res.distance, res.code, res.source) == (1950.4, "cluster", "cluster of 37 stars, Gaia online")
    assert fly_model.distance_line(res) == "Distance: ~1,950 pc (cluster of 37 stars, Gaia online)"


def test_with_no_cluster_the_bright_star_that_lights_it_gives_the_distance():
    res = FD.resolve(TARGET, "nebula", "online", None, 0.5, Fetch(csv([STAR_ROW])))
    assert res.code == "star" and res.distance == pytest.approx(1000 / 1.517, rel=1e-3)
    assert res.source == "illuminating star G 7.0, Gaia online"


def test_a_galaxy_is_a_fixed_backdrop_and_asks_no_one():
    fetch = Fetch(error=AssertionError("a galaxy must not ask Gaia"))
    res = FD.resolve(TARGET, "galaxy", "cache", "", 0.5, fetch)
    assert (res.distance, res.code, res.source) == (math.inf, "galaxy", "galaxy (fixed backdrop)")
    assert fly_model.distance_line(res) == "Distance: galaxy (fixed backdrop)" and not fetch.urls


def test_a_typed_distance_wins_and_asks_no_one():
    fetch = Fetch(error=AssertionError("a typed distance must not ask Gaia"))
    res = FD.resolve(TARGET, "nebula", "online", "2000", 0.5, fetch)
    assert (res.distance, res.code, res.source) == (2000.0, "typed", "typed")
    assert fly_model.distance_line(res) == "Distance: 2000 pc (typed)" and not res.found_by_lookup


@pytest.mark.parametrize("typed", ["abc", "0", "-5", "nan"])
def test_a_bad_typed_distance_is_said_plainly(typed):
    res = FD.resolve(TARGET, "nebula", "online", typed, 0.5, Fetch())
    assert res.distance is None and typed in res.reason
    assert fly_model.distance_line(res).endswith("Type one (pc).")


def test_nothing_found_online_says_why():
    res = FD.resolve(TARGET, "nebula", "online", None, 0.5, Fetch())
    assert res.distance is None
    assert res.reason == ("no star cluster and no bright illuminating star found: searched 1.35 deg round RA 334.7875, "
                          "Dec +56.0791 (the image field is 0.50 deg): Gaia online returned no stars there")
    assert fly_model.distance_line(res) == f"No distance found: {res.reason}. Type one (pc)."


def test_no_target_position_says_so():
    for target in (None, {"ra": "x", "dec": 1}, {"ra": 10, "dec": 95}, {"ra": 400, "dec": 5}):
        res = FD.resolve(target, "nebula", "online", None, 0.5, Fetch(error=AssertionError("no position, no query")))
        assert res.distance is None and "position" in res.reason


def test_an_unknown_gaia_source_is_a_reason_not_a_crash():
    res = FD.resolve(TARGET, "nebula", "bogus", None, 0.5, Fetch())
    assert res.distance is None and "bogus" in res.reason


def test_online_unreachable_says_gaia_could_not_be_reached():
    res = FD.resolve(TARGET, "nebula", "online", None, 0.5, Fetch(error=gaia.GaiaError("no route")))
    assert res.distance is None and res.reason == "Gaia could not be reached (no route)"


def test_a_cache_miss_goes_online_and_offline_says_so():
    res = FD.resolve(TARGET, "nebula", "cache", None, 0.1, Fetch(csv(cluster_rows())))
    assert res.code == "cluster" and res.note == ""                 # no cache file: gaia.sources asked online itself
    res = FD.resolve(TARGET, "nebula", "cache", None, 0.5, Fetch(error=gaia.GaiaError("offline")))
    assert res.distance is None and "could not be reached" in res.reason


def test_an_empty_cache_falls_back_to_online_and_the_line_says_so(cache_dir):
    seed_empty_cache(cache_dir)
    fetch = Fetch(csv([STAR_ROW]))
    res = FD.resolve(TARGET, "nebula", "cache", None, 0.5, fetch)
    assert res.code == "star" and res.note == "Gaia: cache empty here, used online" and fetch.urls
    assert res.source.endswith("Gaia online")
    line = fly_model.distance_line(res)
    assert line.startswith("Distance: ~659 pc (illuminating star G 7.0, Gaia online)")
    assert line.endswith("Gaia: cache empty here, used online.")


def test_an_empty_cache_and_no_network_says_both(cache_dir):
    seed_empty_cache(cache_dir)
    res = FD.resolve(TARGET, "nebula", "cache", None, 0.5, Fetch(error=gaia.GaiaError("offline")))
    assert res.distance is None
    assert res.reason == "the Gaia cache has nothing for this field and Gaia could not be reached (offline)"


def test_an_empty_cache_and_an_empty_sky_online_says_both_were_checked(cache_dir):
    seed_empty_cache(cache_dir)
    res = FD.resolve(TARGET, "nebula", "cache", None, 0.5, Fetch())
    assert res.distance is None and res.note == "" and "the Gaia cache and Gaia online" in res.reason


def test_a_cache_with_stars_is_used_without_asking_online(cache_dir):
    seed_empty_cache(cache_dir)
    q = gaia.GaiaQuery.from_centre(TARGET, parallax.illuminator_radius(TARGET), parallax.ILLUMINATOR_MAX_G)
    gaia._write_atomic(cache_dir / (gaia.cache_key(q) + ".csv"), csv([STAR_ROW]))
    fetch = Fetch(error=AssertionError("the cache had it"))
    res = FD.resolve(TARGET, "nebula", "cache", None, 0.5, fetch)
    assert res.code == "star" and res.source == "illuminating star G 7.0, Gaia" and not fetch.urls


def test_a_superseded_lookup_stops_and_reports_cancelled():
    res = FD.resolve(TARGET, "nebula", "online", None, 0.5, Fetch(csv([STAR_ROW])), is_cancelled=lambda: True)
    assert res.distance is None and res.reason == "cancelled"


def test_the_found_distance_reaches_the_job_unless_one_was_typed():
    base = {"starless": [[0]], "stars": [[0]], "target": TARGET}
    got = siril_bridge._given_distance({**base, "distance": None, "found_distance": 1950.0, "found_source": "cluster",
                                        "found_label": "cluster of 37 stars, Gaia"})
    assert got == {"distance": 1950.0, "distance_source": "cluster", "distance_label": "cluster of 37 stars, Gaia"}
    assert siril_bridge._given_distance({"distance": 800.0, "found_distance": 1950.0}) == {"distance": 800.0}
    assert siril_bridge._given_distance({"distance": None}) == {"distance": None}


def test_the_render_uses_the_dialogs_distance_and_names_its_source(monkeypatch):
    """pipeline._find_distance with a job carrying the dialog's value: no Gaia lookup, the label kept."""
    job = pipeline.FlyJob(starless=[], stars=[], wcs={}, target=TARGET, distance=1950.0, distance_source="cluster",
                          distance_label="cluster of 37 stars, Gaia")
    ctx = pipeline.RunContext(lambda *a: None, lambda: False, Fetch(error=AssertionError("no second lookup")))
    found = pipeline._find_distance(job, [], TARGET, 0.5, ctx)
    assert (found.distance, found.source, found.label) == (1950.0, "cluster", "cluster of 37 stars, Gaia")
    assert pipeline.distance_text(found.distance, found.label) == "Distance: ~1,950 pc (cluster of 37 stars, Gaia)"
    typed = pipeline.FlyJob(starless=[], stars=[], wcs={}, target=TARGET, distance=2000.0)
    assert pipeline.distance_text(*[getattr(pipeline._find_distance(typed, [], TARGET, 0.5, ctx), k)
                                    for k in ("distance", "label")]) == "Distance: 2000 pc (typed)"


# ---------------------------------------------------------------- the dialog

class Resolver:
    """Stands for fly_distance.resolve in the dialog: records its calls and threads; ``gate`` holds the answer back."""

    def __init__(self, answer: FD.DistanceResult, gate: threading.Event | None = None) -> None:
        self.answer, self.gate, self.calls, self.threads = answer, gate, [], []

    def __call__(self, target, kind, gaia_source, typed, radius=None, **kw):
        self.calls.append((target, kind, gaia_source, typed, radius))
        self.threads.append(threading.current_thread())
        if self.gate is not None:
            self.gate.wait(10)
        return self.answer


CLUSTER = FD.DistanceResult(1950.4, "cluster", "cluster of 37 stars, Gaia")


def open_dialog(script, monkeypatch, blocked, resolver, **options):
    monkeypatch.setattr(FD, "resolve", resolver)
    names = __import__("loomsiril.skyname", fromlist=["x"]).NameContext(field=FIELD)
    return _dialog(script, monkeypatch, blocked, names, dict(TARGET), **options)


def line(dlg) -> str:
    return dlg._distance_note.get()


def test_opening_finds_the_distance_on_a_worker_and_shows_it_without_filling_the_box(stub, script, monkeypatch, blocked_discover):
    resolver = Resolver(CLUSTER)
    dlg = open_dialog(script, monkeypatch, blocked_discover, resolver)
    assert line(dlg) == "Finding the distance…"                       # shown at once, before the answer
    _finish(dlg, "distance")
    assert resolver.calls[0][:4] == (TARGET, "nebula", "cache", "")
    assert resolver.calls[0][4] == FIELD["radius_deg"]
    assert all(t is not threading.current_thread() for t in resolver.threads)      # off the Tk thread
    assert line(dlg) == "Distance: ~1,950 pc (cluster of 37 stars, Gaia)"
    assert dlg._distance_hint.get() == "~1,950 pc (auto)"
    assert dlg._vars["distance"].get() == ""                                # an empty box stays 'auto'
    assert dlg.values(__import__("loomsiril.presets", fromlist=["x"]))["distance"] is None


def test_the_dialog_does_not_wait_for_the_lookup(stub, script, monkeypatch, blocked_discover):
    gate = threading.Event()
    dlg = open_dialog(script, monkeypatch, blocked_discover, Resolver(CLUSTER, gate))
    assert line(dlg) == "Finding the distance…"                       # construction returned while Gaia is busy
    gate.set()
    _finish(dlg, "distance")
    assert line(dlg).startswith("Distance: ~1,950")


def test_nothing_found_says_why_and_asks_for_a_distance(stub, script, monkeypatch, blocked_discover):
    none = FD.DistanceResult(None, reason="Gaia could not be reached (offline)")
    dlg = open_dialog(script, monkeypatch, blocked_discover, Resolver(none))
    _finish(dlg, "distance")
    assert line(dlg) == "No distance found: Gaia could not be reached (offline). Type one (pc)."
    assert dlg._distance_hint.get() == "auto" and dlg._vars["distance"].get() == ""


def test_a_typed_distance_wins_at_once_with_no_lookup(stub, script, monkeypatch, blocked_discover):
    resolver = Resolver(CLUSTER)
    dlg = open_dialog(script, monkeypatch, blocked_discover, resolver)
    _finish(dlg, "distance")
    dlg._vars["distance"].set("2000")
    dlg._cancel_after("distance")
    dlg._start_distance()                                                   # what the debounce runs
    assert line(dlg) == "Distance: 2000 pc (typed)"
    assert len(resolver.calls) == 1                                         # typed: Gaia was not asked again


def test_a_galaxy_shows_the_fixed_backdrop(stub, script, monkeypatch, blocked_discover):
    resolver = Resolver(CLUSTER)
    dlg = open_dialog(script, monkeypatch, blocked_discover, resolver, target_type="galaxy")
    assert line(dlg) == "Distance: galaxy (fixed backdrop)" and not resolver.calls


@pytest.mark.parametrize("name,value", [("ra", "10.5"), ("dec", "20"), ("target_type", "galaxy"), ("gaia", "online")])
def test_a_changed_target_box_looks_again_after_a_short_debounce(stub, script, monkeypatch, blocked_discover, name, value):
    resolver = Resolver(CLUSTER)
    dlg = open_dialog(script, monkeypatch, blocked_discover, resolver)
    _finish(dlg, "distance")
    dlg._vars[name].set(value)
    assert "distance" in dlg._afters and dlg.top.afters[dlg._afters["distance"]][0] <= 500      # debounced, not at once
    dlg._cancel_after("distance")
    dlg._start_distance()
    if name != "target_type":
        _finish(dlg, "distance")
        assert len(resolver.calls) == 2 and resolver.calls[1][{"ra": 0, "dec": 0, "gaia": 2}[name]] is not None
    else:
        assert line(dlg) == "Distance: galaxy (fixed backdrop)"


def test_an_object_found_by_name_moves_the_target_and_the_distance_follows(stub, script, monkeypatch, blocked_discover):
    from loomsiril import skyname
    from test_skyname import SESAME

    monkeypatch.setattr(skyname, "resolved_names", {})
    monkeypatch.setattr(skyname, "fetch_text", lambda url, what="x": SESAME)
    resolver = Resolver(CLUSTER)
    monkeypatch.setattr(FD, "resolve", resolver)
    dlg = _dialog(script, monkeypatch, blocked_discover, skyname.NameContext(field=FIELD), {"ra": 1.0, "dec": 2.0},
                  image_path="/d/Sh2-132.tif")
    _finish(dlg, "distance", "names")
    assert (dlg._vars["ra"].get(), dlg._vars["dec"].get()) == ("334.7875", "56.0791")
    dlg._cancel_after("distance")
    dlg._start_distance()
    _finish(dlg, "distance")
    assert resolver.calls[-1][0] == {"ra": 334.7875, "dec": 56.0791}
    assert line(dlg).startswith("Distance: ~1,950")


def test_a_stale_answer_is_dropped(stub, script, monkeypatch, blocked_discover):
    gate = threading.Event()
    resolver = Resolver(CLUSTER, gate)
    dlg = open_dialog(script, monkeypatch, blocked_discover, resolver)
    first = [j for j in dlg._jobs if j["thread"].name == "loomsiril-distance"][0]
    dlg._vars["ra"].set("11.0")                                              # the target changed meanwhile ...
    assert dlg._dist_cancel.is_set()                                         # ... so the running lookup is told to stop
    gate.set()
    first["thread"].join(5)
    for key, (_ms, fn) in list(dlg.top.afters.items()):
        if fn is first["poll"]:
            del dlg.top.afters[key]
    first["poll"]()                                                          # its answer lands now
    assert line(dlg) == "Finding the distance…" or line(dlg) == ""      # and changes nothing
    assert dlg._dist_result is None


def test_the_second_lookup_supersedes_the_first(stub, script, monkeypatch, blocked_discover):
    """Two lookups in flight: only the newest one's answer is shown."""
    answers = [FD.DistanceResult(500.0, "cluster", "cluster of 40 stars, Gaia"), CLUSTER]
    gates = [threading.Event(), threading.Event()]
    order = iter(range(2))

    def resolver(target, kind, gaia_source, typed, radius=None, **kw):
        i = next(order)
        gates[i].wait(10)
        return answers[i]

    dlg = open_dialog(script, monkeypatch, blocked_discover, resolver)
    old = [j for j in dlg._jobs if j["thread"].name == "loomsiril-distance"][0]
    dlg._vars["dec"].set("21")
    dlg._cancel_after("distance")
    dlg._start_distance()
    new = [j for j in dlg._jobs if j["thread"].name == "loomsiril-distance" and j is not old][0]
    for gate in gates:
        gate.set()
    for job in (old, new):
        job["thread"].join(5)
        for key, (_ms, fn) in list(dlg.top.afters.items()):
            if fn is job["poll"]:
                del dlg.top.afters[key]
        job["poll"]()
    assert line(dlg) == "Distance: ~1,950 pc (cluster of 37 stars, Gaia)"


def test_draft_and_render_options_carry_the_distance_the_dialog_found(stub, script, monkeypatch, blocked_discover):
    from loomsiril import presets

    dlg = open_dialog(script, monkeypatch, blocked_discover, Resolver(CLUSTER))
    assert "found_distance" not in dlg.values(presets)                      # still looking: the render finds its own
    _finish(dlg, "distance")
    v = dlg.values(presets)
    assert (v["found_distance"], v["found_source"], v["found_label"]) == (1950.4, "cluster", "cluster of 37 stars, Gaia")
    assert v["distance"] is None
    dlg._vars["ra"].set("11.0")                                              # the boxes changed since: not passed on
    assert "found_distance" not in dlg.values(presets)
    dlg._vars["ra"].set(str(TARGET["ra"]))
    dlg._vars["distance"].set("2500")                                        # typed: the typed value is the job's
    v = dlg.values(presets)
    assert v["distance"] == 2500.0 and "found_distance" not in v


def test_a_lookup_that_found_nothing_passes_nothing_on(stub, script, monkeypatch, blocked_discover):
    from loomsiril import presets

    dlg = open_dialog(script, monkeypatch, blocked_discover, Resolver(FD.DistanceResult(None, reason="x")))
    _finish(dlg, "distance")
    assert "found_distance" not in dlg.values(presets)


def test_the_placeholder_is_shown_only_while_the_box_is_empty(stub, script, monkeypatch, blocked_discover):
    dlg = open_dialog(script, monkeypatch, blocked_discover, Resolver(CLUSTER))
    assert dlg._hint_label.place_kw is not None
    dlg._vars["distance"].set("2000")
    assert dlg._hint_label.place_kw is None
    dlg._vars["distance"].set("")
    assert dlg._hint_label.place_kw is not None


def test_the_options_saved_for_next_time_never_keep_a_found_distance(tmp_path):
    values = {"distance": None, "found_distance": 1950.0, "found_source": "cluster", "found_label": "x", "gaia": "cache"}
    saved = siril_bridge.save_options(values, tmp_path)
    assert "found" not in saved.read_text()


def test_the_final_text_names_the_distance_source(script):
    class Result:
        written, reused, distance, distance_label = 3, 0, 1950.4, "cluster of 37 stars, Gaia"

    text = script.summary(Result(), "/out")
    assert text.splitlines()[0] == "Distance: ~1,950 pc (cluster of 37 stars, Gaia)"
