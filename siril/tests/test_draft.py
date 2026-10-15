"""The Fly-Through draft (loomsiril/draft.py): the plan, the cache, the player and show_draft, on a tiny synthetic scene.

No network (Gaia is a TSV under tmp_path), no real tools, no blocking dialogs. The Tk tests skip cleanly where tkinter or a
display is missing; the worker/cache/render logic is also covered with a fake master that needs no Tk.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril import draft as D
from loomsiril import pipeline as P
from loomsiril import presets
from loomsiril.fly_progress import FlyProgress
from loomsiril.frame import RenderOptions, frame

import synth
from test_pipeline import _write_tsv

PRESET = "youtube_1080"


@pytest.fixture(scope="module")
def field() -> Any:
    return synth.make_field(w=160, h=90, n_stars=10, nc=3, seed=5)


def _job(tmp: Path, field: Any, **kw: Any) -> P.FlyJob:
    tsv = _write_tsv(tmp / "gaia.tsv", field.catalogue)
    base: dict[str, Any] = dict(starless=field.starless, stars=field.stars, wcs=field.wcs, target=None, distance=500.0,
                                gaia=f"tsv:{tsv}", presets=[PRESET], duration=2.0, fps=10, options={"travel": 0.0})
    base.update(kw)
    return P.FlyJob(**base)


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory, field: Any) -> D.PreparedScene:
    return D.prepare_scene(_job(tmp_path_factory.mktemp("draft"), field))


@pytest.fixture(scope="module")
def drafted(prepared: D.PreparedScene) -> D.Draft:
    return D.render_draft(prepared)


def _options(tmp_path: Path, **kw: Any) -> dict[str, Any]:
    """Dialog-style values with two real layer files (their path, mtime and size are keyed)."""
    starless, stars = tmp_path / "starless.tif", tmp_path / "stars.tif"
    for f in (starless, stars):
        if not f.exists():
            f.write_bytes(b"x" * 100)
    base: dict[str, Any] = {
        "mode": "files", "starless_path": str(starless), "stars_path": str(stars), "image_key": "m42.fit|1",
        "target": {"ra": 83.8, "dec": -5.4}, "target_type": "nebula", "distance": 500.0, "duration": 2.0, "fps": 10,
        "loop": "none", "orientation": "landscape", "star_quality": "high", "dynamic": "sdr", "gaia": "cache",
        "presets": [PRESET], "video": False, "out_dir": str(tmp_path), "object_name": "M42", "quality": "high",
    }
    base.update(kw)
    return base


def _frame(value: int, w: int = 8, h: int = 6) -> np.ndarray:
    return np.full((h, w, 3), value, dtype=np.uint8)


def _draft(n: int = 4, fps: float = 7.5, ping_pong: bool = False) -> D.Draft:
    return D.Draft(frames=[_frame(i * 10) for i in range(n)], fps=fps, ping_pong=ping_pong)


# ---- the plan -----------------------------------------------------------------------------------------------------------


def test_the_draft_follows_the_draft_plan(prepared: D.PreparedScene, drafted: D.Draft) -> None:
    plan = D.draft_plan_for(prepared.options)
    assert (drafted.width, drafted.height) == (plan["width"], plan["height"]) == (480, 270)
    assert drafted.long_side <= presets.DRAFT_LONG
    assert len(drafted.frames) == plan["frames"] == 5               # a quarter of the 20 frames
    assert drafted.fps == plan["fps"] == 2.5                         # the rate is lowered, the clip keeps its length
    assert all(f.dtype == np.uint8 and f.shape == (270, 480, 3) for f in drafted.frames)
    assert not drafted.cancelled and not drafted.from_cache and not drafted.ping_pong


def test_a_long_clip_stays_in_the_byte_budget() -> None:
    opts = {"duration": 600.0, "fps": 60, "presets": [PRESET], "orientation": "landscape", "loop": "none"}
    plan = D.draft_plan_for(opts)
    assert plan["bytes"] <= presets.DRAFT_BYTES and plan["frames"] * plan["fps"] > 0
    assert plan["frames"] / plan["fps"] == pytest.approx(600.0)      # the clip keeps its length
    assert plan["frames"] < 600 * 60 / 4                             # fewer than the quarter: the budget bit


def test_the_draft_is_sdr_whatever_the_dynamic_range(prepared: D.PreparedScene, drafted: D.Draft) -> None:
    hdr = D.render_draft(prepared, {"dynamic": "hdr", "starHdr": True, "peak": 1000.0})
    assert all(np.array_equal(a, b) for a, b in zip(hdr.frames, drafted.frames))
    assert max(int(f.max()) for f in hdr.frames) <= 255


def test_the_frames_are_the_full_renders_frames_at_the_draft_size(prepared: D.PreparedScene, drafted: D.Draft) -> None:
    from loomsiril.presets import preset_crop

    sc, o = prepared.scene, prepared.options
    ro = RenderOptions(travel=o["travel"], easing=o["easing"], growth=o["growth"], brightening=o["brightening"],
                       kernel="bilinear", backdrop_motion=o["backdropMotion"], star_quality=o["starQuality"],
                       star_saturation=o["starSaturation"], bloom=o["bloom"], twinkle=o["twinkle"],
                       motion_blur=o["motionBlur"], frame_dt=presets.frame_step(5, False), duration=o["duration"])
    spec = D.draft_spec(o)
    sc = P._quality_scene(sc, o, spec)
    crop = preset_crop(sc.w, sc.h, sc.tp.x, sc.tp.y, spec["w"], spec["h"])
    planes = frame(sc, 0.0, ro, 480, 270, crop)
    expect = D._to_rgb8(planes)
    assert np.abs(drafted.frames[0].astype(int) - expect.astype(int)).max() <= 1


def test_a_vertical_draft_is_tall_and_still_480_long(prepared: D.PreparedScene) -> None:
    d = D.render_draft(prepared, {"orientation": "vertical"})
    assert (d.width, d.height) == (270, 480) and d.long_side == presets.DRAFT_LONG


def test_the_render_options_are_not_changed(prepared: D.PreparedScene) -> None:
    before = dict(prepared.options)
    D.render_draft(prepared, {"travel": 3.0})
    assert prepared.options.keys() == before.keys() and prepared.options["travel"] == before["travel"]


def test_a_bare_scene_needs_options(prepared: D.PreparedScene) -> None:
    with pytest.raises(ValueError):
        D.render_draft(prepared.scene)
    d = D.render_draft(prepared.scene, prepared.options)
    assert len(d.frames) == 5


# ---- progress, cancel, ping-pong -----------------------------------------------------------------------------------------


def test_progress_reports_each_frame(prepared: D.PreparedScene) -> None:
    seen: list[tuple[int, int]] = []
    D.render_draft(prepared, progress=lambda done, total: seen.append((done, total)))
    assert seen == [(i, 5) for i in range(1, 6)]


def test_cancel_stops_between_frames(prepared: D.PreparedScene) -> None:
    stop = threading.Event()

    def progress(done: int, total: int) -> None:
        if done == 2:
            stop.set()

    d = D.render_draft(prepared, progress=progress, cancel=stop)
    assert d.cancelled and len(d.frames) == 2


def test_a_cancel_before_the_first_frame_draws_nothing(prepared: D.PreparedScene, monkeypatch: pytest.MonkeyPatch) -> None:
    drawn: list[int] = []
    monkeypatch.setattr(P, "_frame_planes", lambda *a, **k: drawn.append(1))
    d = D.render_draft(prepared, cancel=lambda: True)
    assert d.cancelled and d.frames == [] and drawn == []


def test_a_cancel_while_the_scene_is_prepared_gives_a_cancelled_draft(tmp_path: Path, field: Any) -> None:
    d = D.render_draft(_job(tmp_path, field), cancel=lambda: True)
    assert d.cancelled and d.frames == []


def test_a_ping_pong_preset_draws_its_forward_half_and_plays_back(prepared: D.PreparedScene) -> None:
    d = D.render_draft(prepared, {"loop": "pingpong"})
    assert d.ping_pong and len(d.frames) == 5 == D.draft_plan_for({**prepared.options, "loop": "pingpong"})["frames"]
    assert D.play_order(5, True) == [0, 1, 2, 3, 4, 3, 2, 1]
    # the flight runs forward over the frames: the last frame is the flight's end, not its start
    assert not np.array_equal(d.frames[0], d.frames[-1]) or prepared.options["travel"] == 0


def test_play_order() -> None:
    assert D.play_order(4, False) == [0, 1, 2, 3]
    assert D.play_order(4, True) == [0, 1, 2, 3, 2, 1]
    assert D.play_order(2, True) == [0, 1] and D.play_order(1, True) == [0] and D.play_order(0, True) == []


def test_a_crossfade_draft_renders(prepared: D.PreparedScene) -> None:
    d = D.render_draft(prepared, {"loop": "crossfade"})
    assert len(d.frames) == 5 and not d.ping_pong


# ---- the cache -----------------------------------------------------------------------------------------------------------


def test_the_cache_dir_is_under_the_siril_config_dir(tmp_path: Path) -> None:
    assert D.cache_dir(tmp_path) == str(tmp_path / D.CACHE_FOLDER)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    d = _draft(5, 7.5, True)
    folder = D.save_draft(tmp_path, "abc", d)
    assert folder and os.path.isfile(os.path.join(folder, "draft.json")) and os.path.isfile(os.path.join(folder, "frames.npz"))
    got = D.load_draft(tmp_path, "abc")
    assert got is not None and got.from_cache and got.fps == 7.5 and got.ping_pong
    assert len(got.frames) == 5 and all(np.array_equal(a, b) for a, b in zip(got.frames, d.frames))
    assert D.load_draft(tmp_path, "other") is None and D.load_draft(tmp_path / "none", "abc") is None
    assert [e for e in os.listdir(tmp_path) if ".tmp-" in e] == []


def test_a_cancelled_or_empty_draft_is_not_kept(tmp_path: Path) -> None:
    d = _draft()
    d.cancelled = True
    assert D.save_draft(tmp_path, "k", d) is None and D.save_draft(tmp_path, "k", D.Draft([], 1.0)) is None
    assert D.load_draft(tmp_path, "k") is None


@pytest.mark.parametrize("damage", ["truncate_frames", "garbage_json", "wrong_count", "missing_frames", "other_key",
                                    "float_frames", "no_json"])
def test_a_corrupt_entry_is_ignored_not_fatal(tmp_path: Path, damage: str) -> None:
    folder = D.save_draft(tmp_path, "k", _draft(3))
    assert folder
    frames, meta = os.path.join(folder, "frames.npz"), os.path.join(folder, "draft.json")
    if damage == "truncate_frames":
        data = open(frames, "rb").read()
        open(frames, "wb").write(data[: len(data) // 2])
    elif damage == "garbage_json":
        open(meta, "w").write("{not json")
    elif damage == "wrong_count":
        m = json.load(open(meta))
        m["n"] = 9
        json.dump(m, open(meta, "w"))
    elif damage == "missing_frames":
        os.remove(frames)
    elif damage == "other_key":
        m = json.load(open(meta))
        m["key"] = "another"
        json.dump(m, open(meta, "w"))
    elif damage == "float_frames":
        np.savez(frames, frames=np.zeros((3, 6, 8, 3), dtype=np.float32))
    elif damage == "no_json":
        os.remove(meta)
    assert D.load_draft(tmp_path, "k") is None
    D.save_draft(tmp_path, "k", _draft(3))                       # the next save replaces the bad entry
    assert D.load_draft(tmp_path, "k") is not None


def test_only_the_newest_drafts_are_kept(tmp_path: Path) -> None:
    for i in range(D.CACHE_KEEP + 3):
        folder = D.save_draft(tmp_path, f"k{i}", _draft(2))
        os.utime(folder, (1000 + i, 1000 + i))
    left = sorted(e for e in os.listdir(tmp_path) if e.startswith("draft-"))
    assert len(left) == D.CACHE_KEEP and left[-1] == f"draft-k{D.CACHE_KEEP + 2}"


# ---- the key -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("change", [
    {"duration": 4.0}, {"fps": 24}, {"loop": "pingpong"}, {"loop": "crossfade"}, {"orientation": "portrait"},
    {"star_quality": "medium"}, {"distance": 900.0}, {"target": {"ra": 1.0, "dec": 2.0}}, {"target_type": "galaxy"},
    {"presets": ["youtube_4k"]}, {"presets": ["social_vertical"]}, {"gaia": "online"}, {"image_key": "other.fit|2"},
    {"options": {"travel": 50.0}}, {"mode": "extract", "star_tool": "starnet2"},
])
def test_a_picture_changing_option_changes_the_key(tmp_path: Path, change: dict[str, Any]) -> None:
    base = _options(tmp_path)
    assert D.draft_key({**base, **change}) != D.draft_key(base)


@pytest.mark.parametrize("change", [
    {"dynamic": "hdr"}, {"video": True}, {"quality": "low"}, {"out_dir": "/elsewhere"}, {"object_name": "Orion"},
    {"starnet_licence": "r2"}, {"star_tool_path": "/usr/bin/other"}, {"presets": [PRESET, "youtube_4k"]},
])
def test_an_option_that_does_not_change_the_picture_keeps_the_key(tmp_path: Path, change: dict[str, Any]) -> None:
    base = _options(tmp_path)
    assert D.draft_key({**base, **change}) == D.draft_key(base)


def test_the_key_follows_the_layer_files_and_not_their_other_names(tmp_path: Path) -> None:
    base = _options(tmp_path)
    key = D.draft_key(base)
    assert D.draft_key(dict(base)) == key                                        # stable
    path = Path(base["starless_path"])
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))        # another modification time
    assert D.draft_key(base) != key
    key2 = D.draft_key(base)
    path.write_bytes(b"y" * 7)                                                   # another size
    assert D.draft_key(base) != key2
    other = tmp_path / "other.tif"
    other.write_bytes(b"x" * 100)
    assert D.draft_key({**base, "starless_path": str(other)}) != D.draft_key(base)
    assert D.draft_key({**base, "stars_path": str(tmp_path / "missing.tif")}) != D.draft_key(base)   # no file: no crash


def test_extract_mode_keys_the_tool_not_the_files(tmp_path: Path) -> None:
    a = _options(tmp_path, mode="extract", star_tool="starnet2")
    assert D.draft_key({**a, "starless_path": "/x", "stars_path": "/y"}) == D.draft_key(a)
    assert D.draft_key({**a, "star_tool": "syqon"}) != D.draft_key(a)


def test_numpy_and_tuple_option_values_are_keyed_like_plain_ones(tmp_path: Path) -> None:
    a = _options(tmp_path, distance=500.0, target={"ra": 83.8, "dec": -5.4})
    b = _options(tmp_path, distance=np.float64(500.0), target={"ra": np.float64(83.8), "dec": -5.4}, presets=(PRESET,))
    assert D.draft_key(a) == D.draft_key(b)


def test_the_renderer_version_is_in_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = D.draft_key(_options(tmp_path))
    monkeypatch.setattr(presets, "RENDERER_VERSION", presets.RENDERER_VERSION + 1)
    assert D.draft_key(_options(tmp_path)) != key


# ---- show_draft without Tk: a fake master -------------------------------------------------------------------------------


class FakeMaster:
    """Stands for the Tk master: ``after`` queues, ``run_until`` plays the queue (on this, the calling, thread)."""

    def __init__(self) -> None:
        self.queue: list[tuple[int, Any]] = []
        self.calls = 0

    def after(self, ms: int, fn: Any) -> int:
        self.calls += 1
        self.queue.append((self.calls, fn))
        return self.calls

    def run_until(self, done: Any, seconds: float = 20.0) -> None:
        end = time.monotonic() + seconds
        while not done():
            assert time.monotonic() < end, "the run did not finish"
            pending, self.queue = self.queue, []
            for _id, fn in pending:
                fn()
            time.sleep(0.01)


class FakePlayer:
    def __init__(self, master: Any, draft: Any) -> None:
        self.draft = draft


@pytest.fixture
def fake_player(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(D, "DraftPlayer", FakePlayer)


def test_show_draft_works_on_a_worker_and_opens_the_player(tmp_path: Path, prepared: D.PreparedScene, fake_player: None,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    main = threading.current_thread()
    seen: dict[str, Any] = {}
    gate = threading.Event()
    real = D.render_draft

    def make_inputs(progress: FlyProgress) -> Any:
        seen["make"] = threading.current_thread()
        gate.wait(10)
        return prepared

    def spy(*a: Any, **k: Any) -> Any:
        seen["render"] = threading.current_thread()
        return real(*a, **k)

    monkeypatch.setattr(D, "render_draft", spy)
    master, model = FakeMaster(), FlyProgress()
    t0 = time.monotonic()
    run = D.show_draft(master, make_inputs, _options(tmp_path), model, cache_dir=str(tmp_path / "cache"))
    assert time.monotonic() - t0 < 1.0 and not run.finished and run.player is None     # it returned while make_inputs blocks
    gate.set()
    master.run_until(lambda: run.finished)
    assert seen["make"] is not main and seen["render"] is not main
    assert isinstance(run.player, FakePlayer) and len(run.draft.frames) == 5 and not run.draft.from_cache
    assert model.done and model.ok and model.percent == 100.0
    assert D.load_draft(str(tmp_path / "cache"), run.key) is not None


def test_a_cache_hit_skips_make_inputs_and_the_render(tmp_path: Path, fake_player: None,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    opts, cache = _options(tmp_path), str(tmp_path / "cache")
    D.save_draft(cache, D.draft_key(opts), _draft(3))
    monkeypatch.setattr(D, "render_draft", lambda *a, **k: pytest.fail("rendered on a cache hit"))
    master = FakeMaster()
    run = D.show_draft(master, lambda p: pytest.fail("inputs read on a cache hit"), opts, FlyProgress(), cache_dir=cache)
    master.run_until(lambda: run.finished)
    assert run.draft.from_cache and len(run.draft.frames) == 3 and isinstance(run.player, FakePlayer)


def test_a_changed_option_misses_the_cache(tmp_path: Path, prepared: D.PreparedScene, fake_player: None) -> None:
    opts, cache = _options(tmp_path), str(tmp_path / "cache")
    D.save_draft(cache, D.draft_key(opts), _draft(3))
    master = FakeMaster()
    run = D.show_draft(master, lambda p: prepared, {**opts, "duration": 4.0}, FlyProgress(), cache_dir=cache)
    master.run_until(lambda: run.finished)
    assert not run.draft.from_cache and len(run.draft.frames) == 5


def test_a_corrupt_cache_entry_renders_again(tmp_path: Path, prepared: D.PreparedScene, fake_player: None) -> None:
    opts, cache = _options(tmp_path, duration=2.0, fps=10), str(tmp_path / "cache")
    folder = D.save_draft(cache, D.draft_key(opts), _draft(3))
    open(os.path.join(folder, "frames.npz"), "wb").write(b"not an npz")
    master = FakeMaster()
    run = D.show_draft(master, lambda p: prepared, opts, FlyProgress(), cache_dir=cache)
    master.run_until(lambda: run.finished)
    assert not run.draft.from_cache and not run.error and D.load_draft(cache, run.key) is not None


def test_cancel_opens_no_player_and_keeps_nothing(tmp_path: Path, prepared: D.PreparedScene, fake_player: None) -> None:
    master, model, cache = FakeMaster(), FlyProgress(), str(tmp_path / "cache")
    gate = threading.Event()

    def make_inputs(progress: FlyProgress) -> Any:
        gate.wait(10)
        return prepared

    run = D.show_draft(master, make_inputs, _options(tmp_path), model, cache_dir=cache)
    run.cancel()
    gate.set()
    master.run_until(lambda: run.finished)
    assert run.player is None and model.cancelled and not os.path.exists(cache)


def test_cancel_between_frames_through_the_progress_model(tmp_path: Path, prepared: D.PreparedScene, fake_player: None,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    master, model = FakeMaster(), FlyProgress()
    real = D.render_draft

    def stopping(src: Any, opts: Any = None, progress: Any = None, cancel: Any = None) -> Any:
        def after_two(done: int, total: int) -> None:
            progress(done, total)
            if done == 2:
                model.request_cancel()
        return real(src, opts, progress=after_two, cancel=cancel)

    monkeypatch.setattr(D, "render_draft", stopping)
    run = D.show_draft(master, lambda p: prepared, _options(tmp_path), model, cache_dir=str(tmp_path / "c"))
    master.run_until(lambda: run.finished)
    assert run.player is None and run.draft.cancelled and len(run.draft.frames) == 2 and not os.path.exists(tmp_path / "c")


def test_an_error_is_reported_on_the_model_and_opens_no_player(tmp_path: Path, fake_player: None) -> None:
    master, model = FakeMaster(), FlyProgress()

    def boom(progress: FlyProgress) -> Any:
        raise ValueError("The starless file does not match the image.")

    done: list[Any] = []
    run = D.show_draft(master, boom, _options(tmp_path), model, on_done=done.append)
    master.run_until(lambda: run.finished)
    assert run.player is None and run.error == "The starless file does not match the image."
    assert model.done and not model.ok and "does not match" in model.label() and done == [run]


def test_the_stages_and_frames_feed_the_progress_model(tmp_path: Path, field: Any, fake_player: None) -> None:
    master, model = FakeMaster(), FlyProgress()
    labels: list[str] = []
    real_pump = model.pump

    def pump() -> int:
        n = real_pump()
        labels.append(model.label())
        return n

    model.pump = pump  # type: ignore[method-assign]
    run = D.show_draft(master, lambda p: _job(tmp_path, field), _options(tmp_path, duration=2.0, fps=10), model)
    master.run_until(lambda: run.finished)
    assert run.player is not None and not run.error
    assert any("Rendering the draft frames" in s for s in labels)


# ---- the player and show_draft with Tk ------------------------------------------------------------------------------------


class _FakeWidget:
    """A widget that keeps what it is given (enough tkinter for the player's logic where Tk or a display is missing)."""

    def __init__(self, master: Any = None, *args: Any, **kw: Any) -> None:
        self.master, self.kw, self.children = master, kw, []
        self.alive, self.bindings, self.protocols, self.pending = True, {}, {}, {}
        self.root = master.root if hasattr(master, "root") else master
        if hasattr(master, "children"):
            master.children.append(self)
        self.command = kw.get("command")

    def grid(self, *a: Any, **k: Any) -> None: ...
    def columnconfigure(self, *a: Any, **k: Any) -> None: ...
    def title(self, *a: Any) -> None: ...
    def resizable(self, *a: Any) -> None: ...
    def protocol(self, name: str, fn: Any) -> None: self.protocols[name] = fn
    def bind(self, name: str, fn: Any, add: Any = None) -> None: self.bindings[name] = fn
    def configure(self, **kw: Any) -> None: self.kw.update(kw)
    def winfo_exists(self) -> bool: return self.alive

    def after(self, ms: int, fn: Any) -> str:
        return self.root.after(ms, fn)

    def after_cancel(self, ident: str) -> None:
        self.root.after_cancel(ident)

    def destroy(self) -> None:
        for c in list(self.children):
            c.destroy()
        if self.alive:
            self.alive = False
            fn = self.bindings.get("<Destroy>")
            if fn:
                fn(type("Event", (), {"widget": self})())


class _FakeRoot(_FakeWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.root, self.ids, self.n = self, {}, 0
        self.tk = type("Tk", (), {"call": lambda _s, *a: tuple(self.ids)})()

    def after(self, ms: int, fn: Any) -> str:
        self.n += 1
        self.ids[f"after#{self.n}"] = fn
        return f"after#{self.n}"

    def after_cancel(self, ident: str) -> None:
        self.ids.pop(ident, None)

    def update(self) -> None: ...
    def withdraw(self) -> None: ...


class _FakeVar:
    def __init__(self, master: Any = None, value: Any = None, **kw: Any) -> None:
        self.v = value

    def get(self) -> Any: return self.v
    def set(self, v: Any) -> None: self.v = v


class _FakeScale(_FakeWidget):
    pass


def _install_fake_tk(monkeypatch: pytest.MonkeyPatch) -> _FakeRoot:
    import sys
    import types

    tk = types.ModuleType("tkinter")
    ttk = types.ModuleType("tkinter.ttk")
    tk.Toplevel, tk.PhotoImage = _FakeWidget, _FakeWidget
    tk.StringVar = tk.DoubleVar = tk.BooleanVar = _FakeVar
    for name in ("Frame", "Label", "Button", "Checkbutton", "Progressbar"):
        setattr(ttk, name, _FakeWidget)
    ttk.Scale = _FakeScale
    tk.ttk = ttk
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", ttk)
    return _FakeRoot()


class TkEnv:
    """The root and the few operations the tests need, over real Tk or the fake above."""

    def __init__(self, root: Any, real: bool) -> None:
        self.root, self.real = root, real

    def afters(self) -> set:
        return set(self.root.tk.call("after", "info"))

    def toplevel(self, master: Any) -> Any:
        if self.real:
            import tkinter

            return tkinter.Toplevel(master)
        return _FakeWidget(master)


@pytest.fixture(params=["fake", "real"])
def tk_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """The player's tests run on a fake tkinter always, and on real Tk where it imports and has a display."""
    if request.param == "fake":
        yield TkEnv(_install_fake_tk(monkeypatch), False)
        return
    try:
        import tkinter
    except (ImportError, OSError) as exc:
        pytest.skip(f"tkinter cannot be imported here: {exc}")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    root.withdraw()
    yield TkEnv(root, True)
    try:
        root.destroy()
    except tkinter.TclError:
        pass


@pytest.fixture
def tk_real(request: pytest.FixtureRequest):
    """Real Tk only (the tests that need a real event loop); skips cleanly without tkinter or a display."""
    try:
        import tkinter
    except (ImportError, OSError) as exc:
        pytest.skip(f"tkinter cannot be imported here: {exc}")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    root.withdraw()
    yield TkEnv(root, True)
    try:
        root.destroy()
    except tkinter.TclError:
        pass


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_the_player_shows_steps_and_closes(tk_env: TkEnv) -> None:
    clock = Clock()
    p = D.DraftPlayer(tk_env.root, _draft(4, 10.0), autoplay=False, clock=clock)
    assert p.frame_index == 0 and not p.playing and "Draft: SDR, 8 px, 10 fps. Judge HDR in the video" == p._note.get()
    p.step()
    p.step()
    assert p.frame_index == 2 and "frame 3 of 4" in p._status.get()
    p.step(-1)
    assert p.frame_index == 1
    p.seek(3)
    assert p.frame_index == 3 and p._scale_var.get() == 3
    p.step()                                           # loop is on: wraps
    assert p.frame_index == 0
    before = tk_env.afters()
    p.close()
    p.close()                                          # twice is fine
    assert p.closed and not p.top.winfo_exists() and tk_env.afters() <= before


def test_the_note_names_the_real_size_and_rate() -> None:
    assert D.note_text(D.Draft([_frame(0, 480, 270)], 7.5)) == "Draft: SDR, 480 px, 7.5 fps. Judge HDR in the video"
    assert D.note_text(D.Draft([_frame(0, 270, 480)], 30.0)) == "Draft: SDR, 480 px, 30 fps. Judge HDR in the video"


def test_the_player_plays_by_the_clock_and_loops(tk_env: TkEnv) -> None:
    clock = Clock()
    p = D.DraftPlayer(tk_env.root, _draft(4, 10.0), autoplay=False, clock=clock)
    p.play()
    assert p.playing and p._after_id is not None and p._play_text.get() == "Pause"
    clock.t += 0.25                                    # 2.5 frames later
    p.tick()
    assert p.frame_index == 2
    clock.t += 0.2
    p.tick()
    assert p.frame_index == 0                          # 4 frames in a cycle: wrapped (loop on)
    p.pause()
    assert not p.playing and p._after_id is None and p._play_text.get() == "Play"
    p.close()


def test_without_loop_the_clip_stops_on_its_last_frame(tk_env: TkEnv) -> None:
    clock = Clock()
    p = D.DraftPlayer(tk_env.root, _draft(4, 10.0), autoplay=False, clock=clock)
    p.loop_var.set(False)
    p.play()
    clock.t += 5.0
    p.tick()
    assert p.frame_index == 3 and not p.playing and p._after_id is None
    p.play()                                           # again from the start
    assert p.playing and p.frame_index == 0
    p.close()


def test_a_ping_pong_draft_runs_back_through_the_middle(tk_env: TkEnv) -> None:
    clock = Clock()
    p = D.DraftPlayer(tk_env.root, _draft(4, 10.0, ping_pong=True), autoplay=False, clock=clock)
    seen = []
    for k in range(6):
        p.seek(k)
        seen.append(p.frame_index)
    assert seen == [0, 1, 2, 3, 2, 1]
    p.close()


def test_a_scrub_moves_the_frame_and_playback_goes_on_from_there(tk_env: TkEnv) -> None:
    clock = Clock()
    p = D.DraftPlayer(tk_env.root, _draft(10, 10.0), autoplay=False, clock=clock)
    p.play()
    p._on_scrub("6.2")
    assert p.frame_index == 6
    clock.t += 0.2
    p.tick()
    assert p.frame_index == 8
    p.close()


def test_a_real_after_callback_advances_and_a_closed_player_leaves_nothing_behind(tk_real: TkEnv) -> None:
    root = tk_real.root
    before = tk_real.afters()
    p = D.DraftPlayer(root, _draft(3, 50.0))        # real clock, autoplay
    end = time.monotonic() + 5
    while p.frame_index == 0 and time.monotonic() < end:
        root.update()
        time.sleep(0.005)
    assert p.frame_index != 0
    p.close()
    root.update()
    assert tk_real.afters() <= before


def test_destroying_the_master_stops_the_player(tk_env: TkEnv) -> None:
    host = tk_env.toplevel(tk_env.root)
    p = D.DraftPlayer(host, _draft(3, 50.0))
    host.destroy()
    tk_env.root.update()
    assert p.closed and p._after_id is None and not p.playing
    p.close()                                           # still safe


def test_an_empty_draft_cannot_be_played(tk_env: TkEnv) -> None:
    with pytest.raises(ValueError):
        D.DraftPlayer(tk_env.root, D.Draft([], 1.0))


def test_show_draft_in_tk_opens_a_player_and_closes_the_progress_window(tk_real: TkEnv, tmp_path: Path,
                                                                        prepared: D.PreparedScene) -> None:
    tk_root = tk_real.root
    gate = threading.Event()
    threads: list[threading.Thread] = []

    def make_inputs(progress: FlyProgress) -> Any:
        threads.append(threading.current_thread())
        gate.wait(10)
        return prepared

    cache = str(tmp_path / "cache")
    run = D.show_draft(tk_root, make_inputs, _options(tmp_path), cache_dir=cache)
    assert run.window is not None and run.window.top.winfo_exists() and run.player is None
    tk_root.update()                                    # the Tk thread keeps running while the worker waits
    gate.set()
    end = time.monotonic() + 30
    while not run.finished and time.monotonic() < end:
        tk_root.update()
        time.sleep(0.01)
    assert run.finished and run.player is not None and not run.window.top.winfo_exists()
    assert threads[0] is not threading.current_thread()
    assert run.player.draft.long_side == 480 and not run.player.closed
    run.player.close()
    # the second draw of the same options is read, not rendered
    run2 = D.show_draft(tk_root, lambda p: pytest.fail("rendered again"), _options(tmp_path), cache_dir=cache)
    end = time.monotonic() + 30
    while not run2.finished and time.monotonic() < end:
        tk_root.update()
        time.sleep(0.01)
    assert run2.draft.from_cache and run2.player is not None
    run2.player.close()
