"""Fly-Through star tools and progress: discovery through Loom's resolver, the chosen tool's run, the progress model
and the render loop's cancel. Fakes only: no real tool, no network, no window."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from loomsiril.starnet_licence import STARNET_LICENCE_REV
from loomsiril import fly_progress, fly_tools, fly_ui, io_fits, pipeline
from loomsiril.loom.tools.resolver import Resolver
from loomsiril.loom.tools.runner import ToolCancelled, ToolNotRunnable
from loomsiril.loom.tools.specs import UNLICENSED_RC_ASTRO, ProbeResult, ToolSpec


def _exe(folder: Path, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _resolver(tmp_path: Path, names: list[str], prober) -> Resolver:
    bin_dir = tmp_path / "bin"
    for n in names:
        _exe(bin_dir, n)
    # linux: the scan root is /opt and the known paths are system ones; a fake prober means nothing is ever run
    return Resolver("linux", str(tmp_path / "home"), {"PATH": str(bin_dir)}, prober=prober)


def _ok(path: str, spec: ToolSpec) -> ProbeResult:
    if spec.id == "starnet2":   # StarNet2 is only offered from 2.6.2 on (it must read FITS): its probe prints a version
        return ProbeResult(0, "starnet2 version: 2.6.2")
    return ProbeResult(0, '{"schemaVersion": 5, "version": "1", "license": {"valid": true}}')


def test_discovery_lists_only_the_tools_the_resolver_finds(tmp_path):
    found = fly_tools.discover(_resolver(tmp_path, ["starnet2", "syqon-cli"], _ok))
    assert [(t.id, t.label) for t in found.tools] == [("starnet2", "StarNet2"), ("syqon-cli", "SyQon Studio Axiom")]
    assert all(os.path.isfile(t.path) for t in found.tools) and found.notes == []


def test_discovery_with_nothing_installed_is_empty(tmp_path):
    found = fly_tools.discover(_resolver(tmp_path, [], _ok))
    assert found.tools == [] and found.notes == []


def test_an_unlicensed_rc_astro_is_not_listed_and_the_refusal_is_plain(tmp_path):
    def prober(path, spec):
        return ProbeResult(77) if spec.id == "rcastro" else _ok(path, spec)

    found = fly_tools.discover(_resolver(tmp_path, ["starnet2", "rc-astro"], prober))
    assert [t.id for t in found.tools] == ["starnet2"]
    assert found.notes == [f"StarXTerminator is not listed: {UNLICENSED_RC_ASTRO}"]


def test_a_licensed_rc_astro_is_listed_as_starxterminator(tmp_path):
    found = fly_tools.discover(_resolver(tmp_path, ["rc-astro"], _ok))
    assert [t.label for t in found.tools] == ["StarXTerminator"]


def test_choose_keeps_a_found_saved_tool_and_falls_back_with_a_note():
    tools = [fly_tools.ExtractTool("starnet2", "StarNet2", "/a"), fly_tools.ExtractTool("syqon-cli", "S", "/b")]
    assert fly_tools.choose(tools, "syqon-cli") == ("syqon-cli", "")
    assert fly_tools.choose(tools, "") == ("starnet2", "")
    tool_id, note = fly_tools.choose(tools, "rcastro")
    assert tool_id == "starnet2" and "StarXTerminator was not found; using StarNet2" in note
    assert fly_tools.choose([], "starnet2") == (None, fly_tools.NONE_FOUND)


def test_the_saved_tool_choice_is_persisted_in_the_options(tmp_path):
    from loomsiril import siril_bridge as bridge

    bridge.save_options({"star_tool": "syqon-cli", "star_tool_path": "/x", "fps": 30}, tmp_path)
    assert bridge.load_options(tmp_path) == {"star_tool": "syqon-cli", "fps": 30}     # the path is never saved


@pytest.mark.parametrize("tool_id, loom_name", [("starnet2", "StarNet2"), ("syqon-cli", "SyQon Studio Axiom"),
                                                ("rcastro", "StarXTerminator")])
def test_extraction_runs_loom_star_removal_with_the_chosen_tool(monkeypatch, tool_id, loom_name):
    from loomsiril.loom import finishing

    seen = {}

    def fake_removal(ctx, tool, src, dst, studio_domain):
        seen.update(tool=tool, exe=ctx.tools, domain=studio_domain)
        planes, header = io_fits.read_fits(src)
        io_fits.write_fits(dst, [p * 0.5 for p in planes], header=header, bitpix=-32)

    monkeypatch.setattr(finishing, "_star_removal", fake_removal)
    plane = np.full((6, 8), 0.8, dtype=np.float32)
    out = fly_tools.extract_starless(tool_id, "/found/exe", [plane])
    assert seen == {"tool": loom_name, "exe": {tool_id: "/found/exe"}, "domain": "linear"}
    assert np.allclose(out[0], 0.4) and out[0].shape == (6, 8)


def test_an_rc_astro_licence_refusal_is_reported_plainly():
    def extract(*a, **k):
        raise ToolNotRunnable("rcastro", "/x/rc-astro", UNLICENSED_RC_ASTRO)

    progress = fly_progress.FlyProgress()
    b = SimpleNamespace(planes=[np.zeros((4, 4), np.float32)], detections=[], header={}, icc=None)
    values = {"mode": "extract", "star_tool": "rcastro", "star_tool_path": "/x/rc-astro"}
    fly_progress.work(values, b, SimpleNamespace(), SimpleNamespace(), progress, str, extract)
    progress.pump()
    assert progress.done and not progress.ok and UNLICENSED_RC_ASTRO in progress.error


def test_work_runs_the_chosen_tool_and_the_render_gets_its_layers():
    calls = {}

    def extract(tool_id, exe, planes, **kw):
        calls.update(tool=tool_id, exe=exe, thread=threading.current_thread(), cancelled=kw["cancelled"]())
        kw["on_progress"](40.0, "removing stars")
        return [np.zeros_like(p) for p in planes]

    class Bridge:
        split_stars = staticmethod(lambda planes, starless: (starless, planes))
        job_from_dialog = staticmethod(lambda values, planes, header, icc: SimpleNamespace(starless=values["starless"],
                                                                                          stars=values["stars"]))

    class Pipe:
        @staticmethod
        def run(job, progress, is_cancelled):
            progress("Rendering x frames", 1, 2)
            progress("Rendering x frames", 2, 2)
            return SimpleNamespace(written=2, reused=0, cancelled=False, videos=["/o/v.mp4"], commands=[], failed=[])

    b = SimpleNamespace(planes=[np.ones((4, 4), np.float32)], detections=[], header={}, icc=None)
    progress = fly_progress.FlyProgress()
    values = {"mode": "extract", "star_tool": "syqon-cli", "star_tool_path": "/found/syqon-cli"}
    thread = fly_progress.start(lambda: fly_progress.work(values, b, Bridge, Pipe, progress, lambda r: "wrote /o/v.mp4",
                                                          extract))
    thread.join(5)
    assert calls["tool"] == "syqon-cli" and calls["exe"] == "/found/syqon-cli" and calls["cancelled"] is False
    assert calls["thread"] is not threading.current_thread()           # extraction ran on the worker
    progress.pump()
    assert progress.done and progress.ok and progress.percent == 100.0 and progress.done_text == "wrote /o/v.mp4"


def test_progress_events_reach_the_model_in_order():
    clock = iter(range(0, 1000, 10)).__next__
    p = fly_progress.FlyProgress(clock)
    p.report("Extracting stars with StarNet2")                       # no count: indeterminate
    p.pump()
    assert p.indeterminate and "working..." in p.label()
    p.tool_percent(25.0, "StarNet2 25%")
    p.report("Looking up the stars and their distances in Gaia", 2, 4)
    p.report("Rendering youtube_1080 frames", 1, 4)
    p.report("Rendering youtube_1080 frames", 2, 4)
    assert p.pump() == 4
    assert p.stage == "Rendering youtube_1080 frames" and p.count == (2, 4) and p.percent == 50.0
    assert p.label() == "Rendering youtube_1080 frames: 2 of 4 (50%)"
    assert "elapsed" in p.timing() and "left" in p.timing()
    p.finish("done")
    p.pump()
    assert p.done and p.ok and p.percent == 100.0 and p.timing() == ""


def test_progress_logs_percent_of_a_tool_and_error_end():
    p = fly_progress.FlyProgress()
    p.log("one")
    p.tool_percent(60.0, "SyQon 60%")
    p.fail("StarNet exited with 3")
    p.pump()
    assert p.log_lines == ["one"] and p.percent == 60.0
    assert p.done and not p.ok and p.label() == "StarNet exited with 3"


def test_clock_text():
    assert fly_progress.clock_text(5) == "0:05" and fly_progress.clock_text(125) == "2:05"
    assert fly_progress.clock_text(3725) == "1:02:05"


def test_cancel_stops_the_render_loop_between_frames(tmp_path, monkeypatch):
    progress = fly_progress.FlyProgress()
    written = []

    def frame_planes(pr, i, ro, cache):
        if i == 2:
            progress.request_cancel()                                  # the user presses Cancel during frame 2
        return [np.zeros((2, 2), np.float32)]

    monkeypatch.setattr(pipeline, "_frame_planes", frame_planes)
    monkeypatch.setattr(pipeline, "write_tiff16", lambda path, planes, icc: written.append(path))
    monkeypatch.setattr(pipeline, "_frame_icc", lambda transfer: None)
    monkeypatch.setattr(pipeline, "_render_options", lambda po: {})
    pr = SimpleNamespace(n=10, folder=str(tmp_path), spec={"id": "x", "pingPong": False}, po={"transfer": "sdr"})
    res = pipeline.Result()
    ctx = pipeline.RunContext(progress.report, progress.cancel_event.is_set, None)
    pipeline._render_frames(pr, res, ctx, 10, False)
    assert res.written == 3 and res.cancelled and len(written) == 3     # frame 2 finishes, frame 3 is never started
    progress.pump()
    assert progress.count == (3, 10)


def test_work_reports_a_cancelled_pipeline_and_a_cancelled_tool():
    progress = fly_progress.FlyProgress()
    b = SimpleNamespace(planes=[np.ones((2, 2), np.float32)], detections=[], header={}, icc=None)
    bridge = SimpleNamespace(read_layer=lambda p: [np.ones((2, 2), np.float32)],
                             job_from_dialog=lambda *a: None)
    pipe = SimpleNamespace(run=lambda job, prog, cancelled: SimpleNamespace(cancelled=True))
    values = {"mode": "files", "starless_path": "a", "stars_path": "b"}
    fly_progress.work(values, b, bridge, pipe, progress, lambda r: "Cancelled: 3 frames kept")
    progress.pump()
    assert progress.done and progress.cancelled and not progress.ok and progress.done_text.startswith("Cancelled")

    progress = fly_progress.FlyProgress()

    def extract(*a, **k):
        raise ToolCancelled("starnet2")

    fly_progress.work({"mode": "extract", "star_tool": "syqon-cli", "star_tool_path": "/x"}, b, bridge, pipe,
                      progress, str, extract)
    progress.pump()
    assert progress.cancelled and "Cancelled" in progress.done_text


def test_an_error_ends_the_run_with_its_message():
    progress = fly_progress.FlyProgress()
    b = SimpleNamespace(planes=[np.ones((2, 2), np.float32)], detections=[], header={}, icc=None)
    bridge = SimpleNamespace(read_layer=lambda p: [np.ones((2, 2), np.float32)], job_from_dialog=lambda *a: None)

    def boom(job, prog, cancelled):
        raise ValueError("bad job")

    fly_progress.work({"mode": "files", "starless_path": "a", "stars_path": "b"}, b, bridge,
                      SimpleNamespace(run=boom), progress, str)
    progress.pump()
    assert progress.done and not progress.ok and progress.error == "bad job" and progress.label() == "bad job"


def test_headless_drive_forwards_each_state_to_siril_in_order():
    seen = []
    siril = SimpleNamespace(update_progress=lambda text, frac: seen.append((text, frac)))
    progress = fly_progress.FlyProgress()
    gate = threading.Event()

    def body():
        progress.report("a", 1, 4)
        gate.wait(5)
        progress.report("b", 4, 4)
        progress.finish("ok")

    thread = fly_progress.start(body)
    drive_thread = threading.Thread(target=fly_progress.drive,
                                    args=(progress, thread, fly_ui.forward_to_siril(siril), 0.01))
    drive_thread.start()
    for _ in range(500):
        if seen:
            break
        threading.Event().wait(0.01)
    gate.set()
    drive_thread.join(5)
    assert seen[0] == ("a", 0.25) and seen[-1] == ("b", 1.0)


# ---------------------------------------------------------------- the progress window on fake widgets

class _W:
    def __init__(self, master=None, **kw):
        self.kw = dict(kw)
        self.calls = []

    def grid(self, **kw):
        pass

    def title(self, *a):
        pass

    def resizable(self, *a):
        pass

    def protocol(self, *a):
        pass

    def after(self, ms, fn=None, *a):
        self.pending = getattr(self, "pending", {})
        self.seq = getattr(self, "seq", 0) + 1
        self.pending[f"a{self.seq}"] = (ms, fn)
        return f"a{self.seq}"

    def after_cancel(self, after_id):
        self.pending.pop(after_id, None)

    def bind(self, *a, **k):
        self.bound = a

    def configure(self, **kw):
        self.kw.update(kw)

    def __setitem__(self, k, v):
        self.kw[k] = v

    def state(self, spec):
        self.calls.append(("state", tuple(spec)))

    def start(self, *a):
        self.calls.append(("start",))

    def stop(self):
        self.calls.append(("stop",))

    def destroy(self):
        self.calls.append(("destroy",))


class _Var:
    def __init__(self, value=""):
        self.v = value

    def set(self, v):
        self.v = v

    def get(self):
        return self.v


def _fake_tk(monkeypatch):
    import sys
    from types import ModuleType

    tk, ttk = ModuleType("tkinter"), ModuleType("tkinter.ttk")
    for n in ("Frame", "Label", "Progressbar", "Button"):
        setattr(ttk, n, type(n, (_W,), {}))
    tk.Toplevel, tk.StringVar, tk.ttk = type("Toplevel", (_W,), {}), _Var, ttk
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", ttk)
    return tk


def _fire(win):
    """What Tk does when the timer goes off: the pending callbacks leave the queue and run."""
    for after_id, (_ms, fn) in list(win.top.pending.items()):
        del win.top.pending[after_id]
        fn()


def test_progress_window_follows_the_model_and_ends_with_close(monkeypatch):
    tk = _fake_tk(monkeypatch)
    p = fly_progress.FlyProgress()
    win = fly_ui.FlyProgressWindow(tk.Toplevel(), p)
    assert win._bar.calls[-1] == ("start",)                                 # no count yet: the bar moves
    p.report("Rendering x frames", 3, 6)
    win.refresh()
    assert win._stage.get() == "Rendering x frames: 3 of 6 (50%)" and win._bar.kw["value"] == 50.0
    assert win._bar.calls[-1] == ("stop",) and "elapsed" in win._timing.get()
    assert win._total_bar.kw["value"] == p.overall > 0 and win._overall.get() == f"Overall {round(p.overall)}%"
    p.finish("Frames written: 6, reused: 0.\nOutput folder: /o")
    _fire(win)
    assert win._bar.kw["value"] == 100.0 and win._total_bar.kw["value"] == 100.0
    assert "Output folder: /o" in win._stage.get() and win._button.kw["text"] == "Close"
    assert win.top.pending == {}                                            # nothing scheduled once it is over


def test_progress_window_cancel_sets_the_event_and_says_so(monkeypatch):
    tk = _fake_tk(monkeypatch)
    p = fly_progress.FlyProgress()
    win = fly_ui.FlyProgressWindow(tk.Toplevel(), p)
    win._cancel()
    assert p.cancel_event.is_set() and p.cancel_requested
    assert win._stage.get() == fly_progress.FlyProgress.CANCELLING and ("state", ("disabled",)) in win._button.calls


def test_progress_window_shows_an_error_message(monkeypatch):
    tk = _fake_tk(monkeypatch)
    p = fly_progress.FlyProgress()
    win = fly_ui.FlyProgressWindow(tk.Toplevel(), p)
    p.fail("StarNet needs its licence accepted first")
    _fire(win)
    assert win._stage.get() == "StarNet needs its licence accepted first" and win._button.kw["text"] == "Close"
    assert win._bar.kw["value"] != 100.0 and win._total_bar.kw["value"] != 100.0


def test_progress_window_close_and_destroy_leave_no_pending_callback(monkeypatch):
    tk = _fake_tk(monkeypatch)
    for how in ("close", "destroy-event", "close-while-running-twice"):
        win = fly_ui.FlyProgressWindow(tk.Toplevel(), fly_progress.FlyProgress())
        assert len(win.top.pending) == 1                                     # the poll is scheduled, its id kept
        if how == "destroy-event":
            win._on_destroy(SimpleNamespace(widget=win.top))
        else:
            win.close()
            if how.endswith("twice"):
                win.close()
        assert win.top.pending == {}
        win._poll()                                                          # a late poll schedules nothing
        assert win.top.pending == {}


# ---------------------------------------------------------------- the overall bar

def test_overall_percent_never_goes_down_and_reaches_100_only_on_success():
    p = fly_progress.FlyProgress()
    p.plan({"extract": 15, "gaia": 5, "scene": 5, "render": 70, "encode": 10})
    seen = []

    def step(event):
        event()
        p.pump()
        seen.append(p.overall)

    step(lambda: p.report("Extracting stars with StarNet2"))
    for pct in (10, 50, 100):
        step(lambda pct=pct: p.tool_percent(float(pct), "StarNet2"))
    step(lambda: p.report("Looking up the stars and their distances in Gaia", 2, 4))   # the stage bar: 50 %
    gaia_stage = p.percent
    step(lambda: p.report("Putting the scene together", 0, 0))
    for n in (1, 50, 100):
        step(lambda n=n: p.report("Rendering youtube_1080 frames", n, 100))
    render_stage = p.percent
    step(lambda: p.report("Encoding youtube_1080", 0, 100))                              # the stage bar starts again
    assert p.percent == 0.0 and render_stage == 100.0 and gaia_stage == 50.0
    step(lambda: p.report("Encoding youtube_1080", 50, 100))
    assert seen == sorted(seen) and max(seen) < 100.0 and p.overall == seen[-1]
    assert p.overall == pytest.approx(100.0 * (15 + 5 + 5 + 70 + 5) / 105)
    step(lambda: p.finish("done"))
    assert p.overall == 100.0 and seen == sorted(seen)


def test_the_overall_bar_is_normalised_to_the_phases_that_run():
    p = fly_progress.FlyProgress()
    p.plan({"extract": 3, "gaia": 5, "scene": 5, "render": 70})                          # files, no video
    p.report("Rendering x frames", 100, 100)
    p.pump()
    assert 98.0 <= p.overall <= 99.0                                                     # everything but the success
    p.report("Rendering x frames", 1, 100)                                               # a restart cannot lower it
    p.pump()
    assert p.overall >= 98.0
    p.fail("boom")
    p.pump()
    assert p.overall < 100.0


def test_work_announces_its_phases_before_anything_runs():
    progress = fly_progress.FlyProgress()
    b = SimpleNamespace(planes=[np.ones((2, 2), np.float32)], detections=[], header={}, icc=None)
    bridge = SimpleNamespace(read_layer=lambda p: [np.ones((2, 2), np.float32)], job_from_dialog=lambda *a: None)
    pipe = SimpleNamespace(run=lambda job, prog, cancelled: SimpleNamespace(cancelled=False))
    fly_progress.work({"mode": "files", "starless_path": "a", "stars_path": "b", "video": True}, b, bridge, pipe,
                      progress, lambda r: "ok")
    kinds = [m for m in list(progress.messages.queue)]
    assert kinds[0][0] == "plan" and kinds[0][1] == {"extract": 3.0, "gaia": 5.0, "scene": 5.0, "render": 70.0,
                                                    "encode": 10.0}
