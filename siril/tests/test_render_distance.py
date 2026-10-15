"""Render and Draft use the distance the dialog showed, and the CLI, Draft, Render and the preview find it the same way.

The bug: a render failed with "no distance found for the target ... give --distance PC" after the dialog had shown
"Distance: ~4,562 pc (illuminating star G 8.8, Gaia)". Two gaps: (1) the dialog's answer is passed on only when it is
valid for the boxes as they stand at the click (still running, or stale: nothing was passed) and the render then did its
own lookup, after the star tool had run; (2) the cache-empty -> Gaia online fallback lived only in the dialog's resolver.
Now the fallback is in ``pipeline._find_distance`` and the worker settles the distance (``fly_progress.ensure_distance``,
with the dialog's own resolver) before any slow work, with a message worded for a dialog. Gaia is always a stub here:
no network, no window (recording tk stand-ins).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

import synth
from loomsiril import draft as D
from loomsiril import fly_distance as FD
from loomsiril import fly_progress, fly_ui, gaia, pipeline, presets, siril_bridge, skyname
from test_draft import _options
from test_fly_distance import (EMPTY, STAR_ROW, TARGET, Fetch, cache_dir, csv, seed_empty_cache)  # noqa: F401
from test_fly_names import _dialog, _finish
from test_siril_script import (FakeJob, _field_and_header, _load_siril_image, _values, _write_layers,  # noqa: F401
                               _fake_tkinter, blocked_discover, fake_pipeline, script, stub)
from test_skyname import FIELD, SESAME

STAR_LABEL = "illuminating star G 7.0, Gaia online"
GUI_TEXT = ("No distance found for this target ({why}). Type a distance in pc in the Distance box and press "
            "{action} again.")


def ctx_with(fetch):
    return pipeline.RunContext(lambda *a: None, lambda: False, fetch)


def cache_job(spec="cache"):
    return pipeline.FlyJob(starless=[], stars=[], wcs={}, target=TARGET, distance=None, gaia=spec)


# ---------------------------------------------------------------- the shared lower layer (pipeline)

def test_an_empty_cache_falls_back_to_online_in_the_pipeline_lookup(cache_dir):
    seed_empty_cache(cache_dir, 0.5)                                       # a cache file for each cone, no stars in it
    fetch = Fetch(csv([STAR_ROW]))
    found = pipeline._find_distance(cache_job(), [], TARGET, 0.5, ctx_with(fetch))
    assert found.source == "star" and found.distance == pytest.approx(659.2, abs=1)
    assert found.label == STAR_LABEL and found.note == pipeline.CACHE_EMPTY_NOTE and fetch.urls


def test_a_missing_cache_file_goes_online_once_and_says_so(cache_dir):
    fetch = Fetch(csv([STAR_ROW]))
    found = pipeline._find_distance(cache_job(), [], TARGET, 0.5, ctx_with(fetch))
    assert found.source == "star" and found.label == STAR_LABEL and found.note == ""
    n = len(fetch.urls)
    again = pipeline._find_distance(cache_job(), [], TARGET, 0.5, ctx_with(Fetch(error=AssertionError("cached now"))))
    assert again.distance == found.distance and n >= 1                      # the answer was cached, no second ask


def test_the_fallback_is_asked_once_not_after_every_online_answer(cache_dir):
    fetch = Fetch(EMPTY)
    found = pipeline._find_distance(cache_job(), [], TARGET, 0.5, ctx_with(fetch))
    assert found.distance is None and "Gaia online" in found.reason
    first = len(fetch.urls)
    online = Fetch(EMPTY)
    pipeline._find_distance(cache_job("online"), [], TARGET, 0.5, ctx_with(online))
    assert len(online.urls) == first                                        # an online lookup does not repeat itself


def test_an_empty_cache_and_an_unreachable_gaia_is_a_reason_not_a_crash(cache_dir):
    seed_empty_cache(cache_dir, 0.5)
    found = pipeline._find_distance(cache_job(), [], TARGET, 0.5, ctx_with(Fetch(error=gaia.GaiaError("offline"))))
    assert found.distance is None
    assert found.reason == "the Gaia cache has nothing for this field and Gaia could not be reached (offline)"
    assert pipeline._resolve_distance(cache_job(), [], TARGET, 0.5, ctx_with(Fetch(error=gaia.GaiaError("x")))) \
        == (None, "", None)                                                 # the public 3-tuple is unchanged


def test_no_distance_error_keeps_the_cli_text_and_carries_the_reason():
    err = pipeline.NoDistanceError("nothing round it")
    assert isinstance(err, ValueError) and err.reason == "nothing round it"
    assert str(err) == "no distance found for the target (no cluster and no illuminating star): give --distance PC"


def test_the_dialog_preview_and_the_pipeline_lookup_are_one_function(cache_dir):
    seed_empty_cache(cache_dir, 0.5)
    found = pipeline._find_distance(cache_job(), [], TARGET, 0.5, ctx_with(Fetch(csv([STAR_ROW]))))
    seed_empty_cache(cache_dir, 0.5)                                       # the same empty cache again
    preview = FD.resolve(TARGET, "nebula", "cache", None, 0.5, Fetch(csv([STAR_ROW])))
    assert (preview.distance, preview.code, preview.source, preview.note) == \
        (found.distance, found.source, found.label, found.note)


# ---------------------------------------------------------------- the exact scenario, through the dialog

def _scenario_dialog(script, monkeypatch, blocked, fetch, mode):
    monkeypatch.setattr(skyname, "resolved_names", {})
    monkeypatch.setattr(skyname, "fetch_text", lambda url, what="x": SESAME)
    monkeypatch.setattr(gaia, "fetch_text", fetch)
    dlg = _dialog(script, monkeypatch, blocked, skyname.NameContext(field=FIELD), {"ra": 1.0, "dec": 2.0},
                  image_path="/d/Sh2-132 - Lion.tif", gaia="cache")
    dlg._mode.set(mode)
    _finish(dlg, "names", "distance")                                       # the name is found; the target moves
    dlg._cancel_after("distance")
    dlg._start_distance()
    _finish(dlg, "distance")
    return dlg


@pytest.mark.parametrize("mode", ["extract", "files"])
def test_render_uses_the_value_the_dialog_showed_in_both_modes(stub, script, monkeypatch, blocked_discover, mode):
    """Extract or files, Image file set, target found by name, Gaia 'cache' with an empty cache: Render's job carries the
    previewed value with its source and its label, and the render's lookup then asks no one."""
    fetch = Fetch(csv([STAR_ROW]))
    dlg = _scenario_dialog(script, monkeypatch, blocked_discover, fetch, mode)
    shown = dlg._dist_result
    assert shown.code == "star" and (dlg._vars["ra"].get(), dlg._vars["dec"].get()) == ("334.7875", "56.0791")
    values = dlg.values(presets)
    assert values["mode"] == mode and values["distance"] is None
    assert (values["found_distance"], values["found_source"], values["found_label"]) == \
        (shown.distance, "star", shown.source)
    field, _pixels, _fits, header_top = _field_and_header()
    values.update(starless=field.starless, stars=field.stars)
    job = siril_bridge.job_from_dialog(values, field.image, header_top, None)
    assert (job.distance, job.distance_source, job.distance_label) == (shown.distance, "star", shown.source)
    found = pipeline._find_distance(job, [], TARGET, 0.5, ctx_with(Fetch(error=AssertionError("no second lookup"))))
    assert (found.distance, found.source, found.label) == (shown.distance, "star", shown.source)


def test_writing_the_same_text_again_keeps_the_answer(stub, script, monkeypatch, blocked_discover):
    """Tk fires a write trace for a same-value set (Find, a re-selected drop-down): that must not drop a valid answer."""
    dlg = _scenario_dialog(script, monkeypatch, blocked_discover, Fetch(csv([STAR_ROW])), "extract")
    for name in ("ra", "dec", "gaia", "target_type"):
        dlg._vars[name].set(dlg._vars[name].get())
    assert dlg._dist_result is not None and "found_distance" in dlg.values(presets)
    dlg._vars["ra"].set("11.0")                                              # a real change still drops it
    assert dlg._dist_result is None and "found_distance" not in dlg.values(presets)


# ---------------------------------------------------------------- the worker settles the distance first

def make_b(field=None):
    field = field or synth.make_field(w=160, h=90, n_stars=6, nc=1, seed=4)
    return SimpleNamespace(planes=field.image, detections=[], header={}, icc=None, wcs=field.wcs, width=160, height=90,
                           target={"ra": TARGET["ra"], "dec": TARGET["dec"]})


class Recorder:
    """Stands for fly_distance.resolve in the worker: records the call, ``order`` shows what ran before and after it."""

    def __init__(self, answer, order):
        self.answer, self.order, self.calls = answer, order, []

    def __call__(self, target, kind, gaia_source, typed, radius=None, **kw):
        self.order.append("resolve")
        self.calls.append((target, kind, gaia_source, typed, radius))
        return self.answer


def run_work(values, resolve, order, b=None):
    """fly_progress.work with stub layers/job/pipeline; returns (progress, the job's values)."""
    seen = {}

    def extract(tool_id, exe, planes, **kw):
        order.append("extract")
        return [np.zeros_like(p) for p in planes]

    class Bridge:
        split_stars = staticmethod(lambda planes, starless: (starless, planes))

        @staticmethod
        def job_from_dialog(v, planes, header, icc):
            seen.update(v)
            return SimpleNamespace(**siril_bridge._given_distance(v))

    class Pipe:
        @staticmethod
        def run(job, progress, is_cancelled):
            order.append("run")
            seen["job"] = job
            return SimpleNamespace(written=1, reused=0, cancelled=False, videos=[], commands=[], failed=[])

    progress = fly_progress.FlyProgress()
    values = {"mode": "extract", "star_tool": "syqon-cli", "star_tool_path": "/x", "target_type": "nebula",
              "gaia": "cache", "distance": None, "target": {"ra": 334.7875, "dec": 56.0791}, **values}
    fly_progress.work(values, b or make_b(), Bridge, Pipe, progress, lambda r: "done", extract, resolve=resolve)
    progress.pump()
    return progress, seen


FOUND = FD.DistanceResult(4562.04, "star", "illuminating star G 8.8, Gaia online")


def test_the_dialogs_value_is_used_as_it_is_with_no_second_lookup():
    order = []
    rec = Recorder(FD.DistanceResult(1.0, "star", "other"), order)
    progress, seen = run_work({"found_distance": 4562.04, "found_source": "star",
                               "found_label": "illuminating star G 8.8, Gaia"}, rec, order)
    assert rec.calls == [] and order == ["extract", "run"] and progress.ok
    assert seen["job"].distance == 4562.04 and seen["job"].distance_label == "illuminating star G 8.8, Gaia"
    assert progress.console_lines == ["distance 4562 pc (illuminating star G 8.8, Gaia)"]


def test_no_value_from_the_dialog_means_the_worker_resolves_it_first_with_the_same_function():
    """Lookup still running at the click, the boxes changed since, or it failed: nothing came in ``found_*``."""
    order = []
    rec = Recorder(FOUND, order)
    b = make_b()
    progress, seen = run_work({"target": {"ra": 11.0, "dec": 22.0}}, rec, order, b)
    assert order == ["resolve", "extract", "run"]                            # before the slow work
    target, kind, source, typed, radius = rec.calls[0]
    assert (target, kind, source, typed) == ({"ra": 11.0, "dec": 22.0}, "nebula", "cache", None)
    assert radius == pytest.approx(FD.field_radius(b.wcs, b.width, b.height))
    assert (seen["found_distance"], seen["found_source"], seen["found_label"]) == (4562.04, "star", FOUND.source)
    assert seen["job"].distance == 4562.04 and seen["job"].distance_label == FOUND.source
    assert progress.console_lines == ["distance 4562 pc (illuminating star G 8.8, Gaia online)"]
    assert progress.ok


def test_the_image_centre_is_the_target_when_the_dialog_has_none():
    order = []
    rec = Recorder(FOUND, order)
    run_work({"target": None}, rec, order)
    assert rec.calls[0][0] == {"ra": TARGET["ra"], "dec": TARGET["dec"]}


def test_a_typed_distance_wins_and_is_not_looked_up():
    order = []
    rec = Recorder(FOUND, order)
    progress, seen = run_work({"distance": 2500.0, "found_distance": 4562.04, "found_source": "star"}, rec, order)
    assert rec.calls == [] and progress.console_lines == ["distance 2500 pc (typed)"]
    assert siril_bridge._given_distance(seen) == {"distance": 2500.0}


def test_a_galaxy_is_a_fixed_backdrop_and_not_looked_up():
    order = []
    rec = Recorder(FOUND, order)
    progress, _ = run_work({"target_type": "galaxy"}, rec, order)
    assert rec.calls == [] and progress.console_lines == ["distance: galaxy (fixed backdrop)"] and progress.ok


def test_the_stage_text_names_the_distance():
    stages = []
    progress = fly_progress.FlyProgress()
    orig = progress.report
    progress.report = lambda stage, done=0, total=0: (stages.append(stage), orig(stage, done, total))[1]
    values = {"target_type": "nebula", "gaia": "cache", "distance": None, "target": dict(TARGET)}
    fly_progress.ensure_distance(values, make_b(), progress, Recorder(FOUND, []))
    assert stages == ["Finding the distance…", "Distance: ~4,562 pc (illuminating star G 8.8, Gaia online)"]


@pytest.mark.parametrize("action", ["Render", "Draft"])
def test_nothing_found_fails_before_any_slow_work_with_a_message_for_the_dialog(action):
    order = []
    none = FD.DistanceResult(None, reason="Gaia could not be reached (offline)")
    rec = Recorder(none, order)
    if action == "Draft":
        with pytest.raises(fly_progress.NoDistance) as info:
            fly_progress.ensure_distance({"target": dict(TARGET), "target_type": "nebula"}, make_b(),
                                         fly_progress.FlyProgress(), rec, "Draft")
        text = str(info.value)
    else:
        progress, _ = run_work({}, rec, order)
        assert order == ["resolve"] and progress.failed                      # no extraction, no run
        text = progress.error
        assert progress.timing() == "Failed at: Finding the distance…" and progress.overall_text() == "Stopped"
    assert text == GUI_TEXT.format(why="Gaia could not be reached (offline)", action=action)
    assert "--distance" not in text and "give" not in text


def test_a_pipeline_no_distance_error_is_worded_for_the_dialog_too():
    class Pipe:
        NoDistanceError = pipeline.NoDistanceError

        @staticmethod
        def run(job, progress, is_cancelled):
            raise pipeline.NoDistanceError("nothing round it")

    class Bridge:
        split_stars = staticmethod(lambda planes, starless: (starless, planes))
        job_from_dialog = staticmethod(lambda *a: SimpleNamespace())

    progress = fly_progress.FlyProgress()
    fly_progress.work({"mode": "extract", "star_tool": "x", "star_tool_path": "/x"}, make_b(), Bridge, Pipe, progress,
                      str, lambda *a, **k: [np.zeros((90, 160), np.float32)])
    progress.pump()
    assert progress.error == GUI_TEXT.format(why="nothing round it", action="Render")


def test_a_cancel_while_finding_the_distance_is_a_cancel_not_an_error():
    progress, _ = run_work({}, Recorder(FD.DistanceResult(None, reason="cancelled"), []), [])
    assert progress.cancelled and not progress.failed


def test_a_lookup_still_running_is_awaited_and_its_cache_is_used(cache_dir):
    """The dialog's lookup is in flight (Gaia slow) when Render is clicked: the worker's lookup waits for it and reads
    the cache it filled, so Gaia is asked once and both give the same value."""
    inside, release = threading.Event(), threading.Event()
    fetch = Fetch(csv([STAR_ROW]))
    slow = lambda url: (inside.set(), release.wait(10), fetch(url))[2]          # noqa: E731
    first = {}
    t1 = threading.Thread(target=lambda: first.update(r=FD.resolve(TARGET, "nebula", "cache", None, 0.5, slow)))
    t1.start()
    assert inside.wait(5)
    asked_by_dialog = len(fetch.urls)
    order, second = [], {}

    def worker():
        progress = fly_progress.FlyProgress()
        values = {"target_type": "nebula", "gaia": "cache", "distance": None, "target": dict(TARGET)}
        fly_progress.ensure_distance(values, make_b(), progress,
                                     lambda t, kind, g, typed, radius, **k: FD.resolve(      # the dialog's field radius
                                         t, kind, g, typed, 0.5, fetch_text=Fetch(error=AssertionError("cached")), **k))
        second.update(values)
        order.append("done")

    t2 = threading.Thread(target=worker)
    t2.start()
    t2.join(0.3)
    assert t2.is_alive() and order == []                                       # waiting for the running lookup
    release.set()
    t1.join(10)
    t2.join(10)
    assert order == ["done"] and second["found_distance"] == first["r"].distance
    assert len(fetch.urls) >= asked_by_dialog and first["r"].code == "star"


def test_a_stale_dialog_value_is_dropped_and_the_worker_finds_the_current_one(stub, script, monkeypatch, blocked_discover):
    dlg = _scenario_dialog(script, monkeypatch, blocked_discover, Fetch(csv([STAR_ROW])), "extract")
    assert "found_distance" in dlg.values(presets)
    dlg._vars["dec"].set("21.5")                                              # boxes changed after the answer
    values = dlg.values(presets)
    assert "found_distance" not in values
    order = []
    rec = Recorder(FOUND, order)
    progress, seen = run_work({"target": values["target"], "gaia": values["gaia"]}, rec, order)
    assert rec.calls[0][0] == {"ra": 334.7875, "dec": 21.5} and seen["job"].distance == 4562.04


# ---------------------------------------------------------------- the Siril console, the script, the draft

def test_the_distance_line_reaches_siril_s_console_once():
    logged = []
    siril = SimpleNamespace(log=lambda text, *a: logged.append(text), update_progress=lambda *a: None)
    update = fly_ui.forward_to_siril(siril)
    progress = fly_progress.FlyProgress()
    progress.announce("distance 4562 pc (illuminating star G 8.8, Gaia online)")
    progress.pump()
    update(progress)
    update(progress)
    assert logged == ["Loom Fly-Through: distance 4562 pc (illuminating star G 8.8, Gaia online)"]


def test_the_script_resolves_the_distance_before_the_layers_and_logs_it(stub, script, fake_pipeline, tmp_path, monkeypatch):
    monkeypatch.setenv("LOOMSIRIL_STARNET", str(tmp_path / "absent"))
    monkeypatch.setenv("PATH", str(tmp_path))
    field, pixels, header_fits, header_top = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    starless_path, stars_path = _write_layers(tmp_path, field)
    values = _values(tmp_path, starless_path=starless_path, stars_path=stars_path)     # the dialog found nothing
    script.main(dialog=lambda *a, **k: values)
    assert len(fake_pipeline.resolved) == 1 and fake_pipeline.resolved[0]["gaia"] == "cache"
    job = fake_pipeline.calls[0]
    assert (job.distance, job.distance_source, job.distance_label) == (1500.0, "star", "illuminating star G 8.8, Gaia online")
    assert ("Loom Fly-Through: distance 1500 pc (illuminating star G 8.8, Gaia online)", 0) in stub.STATE.logs


def test_the_script_stops_with_the_dialog_message_when_no_distance_can_be_found(stub, script, fake_pipeline, tmp_path,
                                                                                monkeypatch):
    monkeypatch.setenv("LOOMSIRIL_STARNET", str(tmp_path / "absent"))
    monkeypatch.setenv("PATH", str(tmp_path))
    field, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    starless_path, stars_path = _write_layers(tmp_path, field)
    monkeypatch.setattr(FD, "resolve", lambda *a, **k: FD.DistanceResult(None, reason="no star found"))
    script.main(dialog=lambda *a, **k: _values(tmp_path, starless_path=starless_path, stars_path=stars_path))
    assert fake_pipeline.calls == []
    texts = [t for t, _c in stub.STATE.logs] + [t for _k, t in stub.STATE.messages]
    assert any(GUI_TEXT.format(why="no star found", action="Render") in t for t in texts)


def test_the_draft_key_still_follows_the_distance(tmp_path):
    base = _options(tmp_path, distance=None)
    a = D.draft_key({**base, "found_distance": 4562.0, "found_source": "star", "found_label": "x"})
    assert a != D.draft_key(base)
    assert a != D.draft_key({**base, "found_distance": 4000.0, "found_source": "star", "found_label": "x"})
    assert D.draft_key({**base, "distance": 2500.0}) != D.draft_key(base)
    assert a == D.draft_key({**base, "found_distance": 4562.0, "found_source": "star", "found_label": "x"})


def test_the_draft_scene_says_the_same_words_when_it_finds_nothing(tmp_path):
    field = synth.make_field(w=160, h=90, n_stars=6, nc=1, seed=4)
    job = pipeline.FlyJob(starless=field.starless, stars=field.stars, wcs=field.wcs, target=dict(TARGET), distance=None,
                          gaia="online")
    with pytest.raises(ValueError) as info:
        D.prepare_scene(job, fetch_text=Fetch(EMPTY))
    assert str(info.value).startswith("No distance found for this target (") and str(info.value).endswith("press Draft again.")
