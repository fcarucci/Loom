"""The Fly-Through script and dialog joined to the REAL loomsiril.draft: the draft closure inside main(), and the Draft
button's values. Fake master, fake progress window, fake player: no Tk window is ever opened, no network, no tool."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from loomsiril import draft as D
from loomsiril import fly_progress, fly_ui
from loomsiril.fly_progress import FlyProgress

import synth
from test_draft import FakeMaster, FakePlayer, _job, _options
from test_pipeline import _write_tsv
from test_siril_script import (_buttons_in, _dialog, _field_and_header, _load_siril_image, _settle, _values,  # noqa: F401
                               _write_layers, blocked_discover, script, stub)


class FakeWindow:
    """Stands for fly_ui.FlyProgressWindow: a model, a top, and a record of being closed."""

    instances: list["FakeWindow"] = []

    def __init__(self, master: Any, model: FlyProgress, on_update: Any = None) -> None:
        self.model, self.closed = model, False
        self.top = SimpleNamespace(title=lambda *a: None, destroy=lambda: None)
        FakeWindow.instances.append(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    FakeWindow.instances = []
    monkeypatch.setattr(fly_ui, "FlyProgressWindow", FakeWindow)
    monkeypatch.setattr(D, "DraftPlayer", FakePlayer)
    runs: list[Any] = []
    real = D.show_draft
    monkeypatch.setattr(D, "show_draft", lambda *a, **k: runs.append(real(*a, **k)) or runs[-1])
    return runs


def _main_with_draft(stub, script, tmp_path: Path, field, pixels, header_fits, values: dict, master: FakeMaster,
                     runs: list) -> None:
    """Runs the real main(); its fake dialog presses Draft (the script's own closure) and plays the Tk queue."""
    _load_siril_image(stub, pixels, header_fits)

    def dialog(options, discover, target, siril=None, draft=None, names=None):
        assert callable(draft)
        draft(master, dict(values))
        master.run_until(lambda: runs[-1].finished)
        return None                                              # the user closes the dialog afterwards

    script.main(dialog=dialog)


def test_the_scripts_draft_closure_feeds_the_real_draft_and_a_second_call_is_a_cache_hit(
        stub, script, fakes, tmp_path, monkeypatch):
    field, pixels, header_fits, _ = _field_and_header(160, 90)
    starless, stars = _write_layers(tmp_path, field)
    tsv = _write_tsv(tmp_path / "gaia.tsv", field.catalogue)
    values = _values(tmp_path, starless_path=starless, stars_path=stars, gaia=f"tsv:{tsv}", distance=500.0,
                     duration=2.0, fps=10, target={"ra": 83.8, "dec": -5.4}, star_tool="", star_tool_path="",
                     image_path="", image_stamp=None)
    keyed_on: list[threading.Thread] = []
    real_key = D.draft_key
    monkeypatch.setattr(D, "draft_key", lambda o: keyed_on.append(threading.current_thread()) or real_key(o))
    layer_calls: list[Any] = []
    real_layers = fly_progress.layers
    monkeypatch.setattr(fly_progress, "layers", lambda *a, **k: layer_calls.append(1) or real_layers(*a, **k))
    master = FakeMaster()

    _main_with_draft(stub, script, tmp_path, field, pixels, header_fits, values, master, fakes)
    run = fakes[0]
    assert not run.error and len(run.draft.frames) == 5 and not run.draft.from_cache
    # the cache key is made from the dialog's actual option dict (plus the image key), and on the worker thread
    assert all(run.options[k] == v for k, v in values.items())
    assert run.options["image_key"].startswith("160x90 ") and run.options["image_key"].endswith(" None")
    assert run.key == real_key(run.options)
    assert keyed_on and all(t is not threading.current_thread() for t in keyed_on)
    for changed in ({"duration": 4.0}, {"fps": 20}, {"star_tool": "starnet2", "mode": "extract"},
                    {"image_key": run.options["image_key"] + "x"}):
        assert real_key({**run.options, **changed}) != run.key
    assert real_key({**run.options, "out_dir": "/elsewhere", "video": True, "object_name": "Other"}) == run.key
    assert FakeWindow.instances[0].closed                          # the progress window was closed, not left up
    assert D.load_draft(D.cache_dir(stub.STATE.configdir), run.key) is not None
    assert len(layer_calls) == 1

    # the same call again: the key is the same, the draft comes from the cache and the layers are not read again
    master2 = FakeMaster()
    _main_with_draft(stub, script, tmp_path, field, pixels, header_fits, values, master2, fakes)
    again = fakes[1]
    assert again.key == run.key and again.draft.from_cache and len(layer_calls) == 1


def test_the_image_file_identity_is_part_of_the_scripts_draft_key(stub, script, fakes, tmp_path):
    field, pixels, header_fits, _ = _field_and_header(160, 90)
    starless, stars = _write_layers(tmp_path, field)
    tsv = _write_tsv(tmp_path / "gaia.tsv", field.catalogue)
    base = _values(tmp_path, starless_path=starless, stars_path=stars, gaia=f"tsv:{tsv}", distance=500.0, duration=2.0,
                   fps=10, target={"ra": 83.8, "dec": -5.4}, star_tool="", star_tool_path="")
    keys = []
    for stamp in ((1, 10), (2, 10)):
        master = FakeMaster()
        _main_with_draft(stub, script, tmp_path, field, pixels, header_fits,
                         {**base, "image_path": "/data/m42.fits", "image_stamp": stamp}, master, fakes)
        keys.append(fakes[-1].key)
    assert keys[0] != keys[1]                                        # a changed image file is a different draft


def test_the_dialogs_draft_button_produces_the_real_draft_key(stub, script, monkeypatch, blocked_discover, tmp_path):
    """The Draft button's values (as the dialog's worker prepared them) go to the real show_draft; its key is
    draft_key of exactly those values."""
    field = synth.make_field(w=160, h=90, n_stars=10, nc=3, seed=5)
    prepared = D.prepare_scene(_job(tmp_path, field))
    starless, stars = _write_layers(tmp_path, field)
    seen: dict[str, Any] = {}
    runs: list[Any] = []
    monkeypatch.setattr(D, "DraftPlayer", FakePlayer)
    master = FakeMaster()

    def draft_cb(window, values):
        seen["values"] = dict(values)
        runs.append(D.show_draft(master, lambda p: prepared, {**values, "image_key": "m42|1"}, FlyProgress(),
                                 cache_dir=str(tmp_path / "cache")))

    dlg, _ = _dialog(script, monkeypatch, blocked_discover, out_dir=str(tmp_path), starless_path=starless,
                     stars_path=stars, duration=2.0, fps=10)
    dlg._draft_cb = draft_cb
    _settle(dlg)
    dlg._draft()
    _settle(dlg)
    master.run_until(lambda: runs[-1].finished)
    run = runs[0]
    assert seen["values"]["starless_path"] == starless and seen["values"]["image_stamp"] is None
    assert run.key == D.draft_key({**seen["values"], "image_key": "m42|1"}) and not run.error
    assert len(run.draft.frames) == 5 and not run.draft.from_cache
    # the same click again is a cache hit with the same key
    dlg._draft()
    _settle(dlg)
    master.run_until(lambda: runs[-1].finished)
    assert runs[1].key == run.key and runs[1].draft.from_cache


def test_draft_run_close_leaves_no_after_callback_and_closes_the_progress_window(tmp_path):
    cancelled: list[Any] = []

    class Master(FakeMaster):
        def after_cancel(self, after_id: Any) -> None:
            cancelled.append(after_id)
            self.queue = [(i, f) for i, f in self.queue if i != after_id]

    master, window = Master(), FakeWindow(None, FlyProgress())
    gate = threading.Event()
    run = D.show_draft(master, lambda p: gate.wait(5) and None, _options(tmp_path), window, cache_dir=None)
    assert master.queue                                              # its poll is pending
    run.close()
    gate.set()
    assert master.queue == [] and cancelled and run.progress.cancel_event.is_set()
    run2 = D.show_draft(master, lambda p: gate.wait(5) and None, _options(tmp_path), FakeWindow(None, FlyProgress()),
                        cache_dir=None)
    window2 = run2.window
    run2._close_window()
    assert window2.closed                                            # _close_window goes through the window's close()
