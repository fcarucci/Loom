"""Tests for Loom/loom_preprocess.py (the Siril entry: dialog mode and headless mode) against the sirilpy stub.

The stub (tests/sirilpy_stub) is put first on sys.path. The pipeline, the preflight and the tool resolver are replaced
by fakes that record what the script hands them; the options dialog and the progress window are replaced by fakes too
(no tkinter). Passing these tests does NOT mean the script has run inside Siril: see docs/loom-job.md for the real runs.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from . import synth

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "Loom" / "loom_preprocess.py"
STUB = REPO / "tests" / "sirilpy_stub"

FAKE_RUN_TIMEOUT = 10.0


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The sirilpy stub, freshly reset and imported first; its config dir is under tmp_path."""
    monkeypatch.syspath_prepend(str(STUB))
    for name in [m for m in sys.modules if m == "sirilpy" or m.startswith("sirilpy.")]:
        monkeypatch.delitem(sys.modules, name)
    import sirilpy

    sirilpy.STATE.reset()
    sirilpy.STATE.configdir = str(tmp_path / "siril-config")
    sirilpy.STATE.workdir = str(tmp_path / "siril-wd")
    yield sirilpy
    sirilpy.STATE.reset()


@pytest.fixture
def script(stub, monkeypatch):
    """The script module, loaded from its file (not imported as a package)."""
    name = "loom_siril_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)  # dataclasses look the module up by name while it executes
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def temp_is_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The ToolStore refuses a config folder under the system temporary folder; tests keep it elsewhere."""
    folder = tmp_path / "system-temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(folder))
    return folder


@pytest.fixture
def loomsiril_modules(temp_is_elsewhere):
    """The real loomsiril modules the script patches (none of them imports sirilpy)."""
    from loomsiril.loom import pipeline, preflight

    return SimpleNamespace(pipeline=pipeline, preflight=preflight)


class FakeRun:
    """Stands in for loomsiril.loom.pipeline.run: records the config and the context the script built."""

    def __init__(self, work: Path) -> None:
        self.work = work
        self.calls: list[dict[str, Any]] = []
        self.threads: list[str] = []
        self.ctx: Any = None
        self.error: BaseException | None = None
        self.behave: Any = None
        self.report = SimpleNamespace(produced=["RGB"], outputs={"RGB": "/out/RGB.fits"}, exports=[],
                                      duration_s=1.5, log_path="/logs/loom-run.log", warnings=[])

    def __call__(self, config: Any, factory: Any, **kwargs: Any) -> Any:
        self.calls.append({"config": dict(config), "kwargs": kwargs})
        self.threads.append(threading.current_thread().name)
        self.ctx = factory(str(self.work))
        if self.behave is not None:
            return self.behave(self.ctx)
        if self.error is not None:
            raise self.error
        return self.report


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch, loomsiril_modules, tmp_path: Path) -> FakeRun:
    work = tmp_path / "work"
    work.mkdir()
    fake = FakeRun(work)
    monkeypatch.setattr(loomsiril_modules.pipeline, "run", fake)
    return fake


@pytest.fixture
def preflight_calls(monkeypatch: pytest.MonkeyPatch, loomsiril_modules):
    """preflight.preflight replaced: returns ``answer`` (default: no problems) and records its inputs."""
    seen = SimpleNamespace(inputs=[], answer=[])

    def fake(inputs):
        seen.inputs.append(inputs)
        return list(seen.answer)

    monkeypatch.setattr(loomsiril_modules.preflight, "preflight", fake)
    return seen


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch, temp_is_elsewhere):
    """The resolver replaced: ``found`` maps tool id to a Found (anything else is not found)."""
    from loomsiril.loom.tools import resolver as resolver_mod

    state = SimpleNamespace(found={}, resolved=[], configured={}, rejections={})

    class FakeResolver:
        def __init__(self, platform, home, env, store=None, **_kw) -> None:
            self.store = store

        def resolve(self, tool_id, configured=None):
            state.resolved.append(tool_id)
            state.configured[tool_id] = configured
            return state.found.get(tool_id)

        def rejection(self, tool_id):
            return state.rejections.get(tool_id)

    monkeypatch.setattr(resolver_mod, "Resolver", FakeResolver)
    return state


def _found(tool_id: str, path: str) -> Any:
    from loomsiril.loom.tools.resolver import Found

    return Found(tool_id=tool_id, path=path, source="configured", version="1", probe_summary="ok")


def _rgb_paths(tmp_path: Path) -> dict[str, str]:
    return {"L": "", "R": str(tmp_path / "R.fits"), "G": str(tmp_path / "G.fits"), "B": str(tmp_path / "B.fits"),
            "H": "", "S": "", "O": ""}


def _job(tmp_path: Path, **extra: Any) -> Path:
    body: dict[str, Any] = {
        "paths": _rgb_paths(tmp_path),
        "filters": {"R": "Baader R", "G": "Baader G", "B": "Baader B"},
        "outputDir": str(tmp_path / "out"),
        "gradientTool": "none",
        "stretch": True,
    }
    body.update(extra)
    path = tmp_path / "job.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _status(path: Path) -> dict[str, Any]:
    return json.loads(Path(str(path) + ".status.json").read_text(encoding="utf-8"))


def _progress_free(model: Any) -> None:
    """The progress window's replacement: it returns at once (the run is joined by the caller)."""
    return None


# ---- importing and the first lines --------------------------------------------------------------------------------


def test_import_does_nothing(stub, script, fake_run, preflight_calls):
    assert callable(script.main)
    assert stub.STATE.commands == []
    assert stub.STATE.connects == 0
    assert stub.STATE.installs == []
    assert fake_run.calls == []


def test_script_is_thin_and_does_not_import_tkinter_at_module_level(script):
    assert "tkinter" not in script.__dict__
    assert "loomsiril" not in script.__dict__


def test_main_starts_with_requires_and_installs_scipy(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    script.main(["--job", str(_job(tmp_path))])
    assert stub.STATE.commands[0] == ("requires", "1.4.0")
    assert stub.STATE.installs[0][0] == ("numpy", "scipy", "tifffile")  # no window: no ttkthemes


def test_dialog_mode_also_installs_ttkthemes(stub, script, fake_run, preflight_calls, tools, tmp_path):
    """Siril's theme needs a ThemedTk window (match_theme_to_siril refuses a plain tk.Tk)."""
    stub.STATE.cli = False
    script.main(argv=[], ask=lambda m, s, i: None, show=_progress_free)
    assert stub.STATE.installs[0][0] == ("numpy", "scipy", "tifffile", "ttkthemes")


# ---- headless mode -----------------------------------------------------------------------------------------------


def test_headless_runs_the_job_and_writes_an_ok_status(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    job = _job(tmp_path)
    code = script.main(["--job", str(job)])
    assert code == 0
    assert len(fake_run.calls) == 1
    config = fake_run.calls[0]["config"]
    assert config["outputDir"] == str(tmp_path / "out")
    assert config["paths"]["R"] == str(tmp_path / "R.fits")
    assert config["filters"] == {"R": "Baader R", "G": "Baader G", "B": "Baader B"}
    assert config["stretch"] is True
    status = _status(job)
    assert status["ok"] is True and status["exitCode"] == 0
    assert status["produced"] == ["RGB"]
    assert status["outputs"] == {"RGB": "/out/RGB.fits"}
    assert status["log"] == "/logs/loom-run.log"
    assert status["problems"] == [] and status["error"] is None


def test_headless_without_a_job_logs_one_line_and_returns(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    assert script.main([]) == 1
    assert fake_run.calls == []
    assert not any(p.name.endswith(".status.json") for p in tmp_path.iterdir())
    assert any("needs a job file" in text for text, _c in stub.STATE.logs)


def test_headless_run_never_writes_the_options_file(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    preflight_calls.answer = ["No output folder: set outputDir"]
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 2
    assert not (Path(stub.STATE.configdir) / "loomsiril" / "loom.json").exists()
    assert fake_run.calls == []


def test_dialog_saves_the_options_and_the_output_folder_before_preflight(
        stub, script, fake_run, preflight_calls, tools, tmp_path, monkeypatch, loomsiril_modules):
    from loomsiril.loom import config as config_mod

    stub.STATE.cli = False
    order: list[str] = []
    real_save = config_mod.save

    def save(cfg, config_dir):
        order.append("save")
        assert "outputDir" in cfg
        return real_save(cfg, config_dir)

    def preflight(inputs):
        order.append("preflight")
        saved = json.loads((Path(stub.STATE.configdir) / "loomsiril" / "loom.json").read_text(encoding="utf-8"))
        order.append("saved-paths-R=" + saved["paths"]["R"])
        return []

    monkeypatch.setattr(config_mod, "save", save)
    monkeypatch.setattr(loomsiril_modules.preflight, "preflight", preflight)
    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=_progress_free) == 0
    assert order[:2] == ["save", "preflight"]
    assert order[2] == "saved-paths-R=" + str(tmp_path / "R.fits")
    saved = json.loads((Path(stub.STATE.configdir) / "loomsiril" / "loom.json").read_text(encoding="utf-8"))
    assert saved["outputDir"] == str(tmp_path / "out")


def test_dialog_save_failure_does_not_stop_the_checks(
        stub, script, fake_run, preflight_calls, tools, tmp_path, monkeypatch, loomsiril_modules):
    from loomsiril.loom import config as config_mod

    stub.STATE.cli = False

    def broken(cfg, config_dir):
        raise OSError("read-only")

    monkeypatch.setattr(config_mod, "save", broken)
    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=_progress_free) == 0
    assert len(preflight_calls.inputs) == 1
    assert fake_run.calls


def test_headless_job_is_self_contained_and_ignores_the_saved_paths(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    from loomsiril.loom import config as config_mod

    stub.STATE.cli = True
    saved = config_mod.defaults()
    saved["paths"]["H"] = "/saved/H.fits"
    saved["filters"] = {"R": "Saved R"}
    saved["stretch"] = True
    config_mod.save(saved, stub.STATE.configdir)
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 0
    config = fake_run.calls[0]["config"]
    assert config["paths"]["H"] == ""
    assert config["filters"] == {"R": "Baader R", "G": "Baader G", "B": "Baader B"}
    assert config["stretch"] is True          # the job sets it; defaults give False when a job omits it
    job2 = tmp_path / "job2.json"
    job2.write_text(json.dumps({"paths": _rgb_paths(tmp_path), "filters": FILTERS,
                                "outputDir": str(tmp_path / "out2"), "gradientTool": "none"}), encoding="utf-8")
    assert script.main(["--job", str(job2)]) == 0
    assert fake_run.calls[1]["config"]["stretch"] is False


def test_preflight_failure_lists_every_problem_with_exit_code_2(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    preflight_calls.answer = ["File not found for R: /x/R.fits", "No SPCC filter chosen for G: set filters G in the job file"]
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 2
    status = _status(job)
    assert status["exitCode"] == 2 and status["ok"] is False
    assert status["problems"] == preflight_calls.answer
    assert status["produced"] == []
    assert fake_run.calls == []
    logged = [text for text, _c in stub.STATE.logs]
    assert any("File not found for R" in text for text in logged)


def test_validate_only_reports_lines_and_runs_nothing(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    job = _job(tmp_path, validateOnly=True)
    assert script.main(["--job", str(job)]) == 0
    assert fake_run.calls == []
    logged = [text for text, _c in stub.STATE.logs]
    assert "All checks passed. Nothing executed." in logged
    assert f"R <- {tmp_path / 'R.fits'}" in logged
    assert _status(job)["exitCode"] == 0


def test_run_error_gives_exit_code_1_with_the_error_text(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    fake_run.error = RuntimeError("the stack broke")
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 1
    status = _status(job)
    assert status["exitCode"] == 1 and status["ok"] is False
    assert "the stack broke" in status["error"]


def test_cancelled_run_gives_exit_code_130(stub, script, fake_run, preflight_calls, tools, tmp_path):
    from loomsiril.loom.chain import CANCEL_MESSAGE, RunCancelled

    stub.STATE.cli = True
    fake_run.error = RunCancelled(CANCEL_MESSAGE)
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 130
    assert _status(job)["exitCode"] == 130


def test_status_file_can_be_named_by_the_job(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    named = tmp_path / "elsewhere" / "result.json"
    job = _job(tmp_path, statusFile=str(named))
    assert script.main(["--job", str(job)]) == 0
    assert named.is_file()
    assert json.loads(named.read_text(encoding="utf-8"))["exitCode"] == 0
    assert not Path(str(job) + ".status.json").exists()


def test_unreadable_job_still_writes_a_status_file(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    job = tmp_path / "broken.json"
    job.write_text("{not json", encoding="utf-8")
    assert script.main(["--job", str(job)]) == 1
    status = _status(job)
    assert status["exitCode"] == 1 and "cannot read the job file" in status["error"]
    assert fake_run.calls == []


def test_job_without_output_folder_is_a_preflight_problem(stub, script, fake_run, tools, tmp_path,
                                                          loomsiril_modules):
    """The real preflight: a job with no outputDir is refused with the output-folder message (exit code 2)."""
    stub.STATE.cli = True
    body = {"paths": _rgb_paths(tmp_path), "filters": {"R": "Baader R", "G": "Baader G", "B": "Baader B"}}
    job = tmp_path / "job.json"
    job.write_text(json.dumps(body), encoding="utf-8")
    assert script.main(["--job", str(job)]) == 2
    status = _status(job)
    assert "No output folder: set outputDir" in status["problems"]
    assert fake_run.calls == []


def test_pre_run_failure_still_writes_the_status_file(stub, script, fake_run, preflight_calls, tools, tmp_path,
                                                      monkeypatch):
    stub.STATE.cli = True
    job = _job(tmp_path)

    def broken_import():
        raise ImportError("loomsiril is not importable")

    monkeypatch.setattr(script, "_load_modules", broken_import)
    assert script.main(["--job", str(job)]) == 1
    status = _status(job)
    assert status["exitCode"] == 1 and "loomsiril is not importable" in status["error"]
    assert fake_run.calls == []


def test_failing_siril_command_still_writes_the_status_file(stub, script, fake_run, preflight_calls, tools, tmp_path,
                                                            monkeypatch):
    stub.STATE.cli = True
    job = _job(tmp_path)

    def refuse(self, *args):
        raise RuntimeError("siril refused the requires line")

    monkeypatch.setattr(stub.SirilInterface, "cmd", refuse)
    assert script.main(["--job", str(job)]) == 1
    assert "siril refused" in _status(job)["error"]


def test_ensure_installed_failure_still_writes_the_status_file(stub, script, fake_run, preflight_calls, tools,
                                                               tmp_path, monkeypatch):
    stub.STATE.cli = True
    job = _job(tmp_path)

    def offline(*packages, **_kw):
        raise RuntimeError("pip cannot reach the index")

    monkeypatch.setattr(script, "ensure_installed", offline)
    assert script.main(["--job", str(job)]) == 1
    assert "pip cannot reach the index" in _status(job)["error"]


def test_keyboard_interrupt_maps_to_130_in_the_status_file(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    fake_run.error = KeyboardInterrupt()
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 130
    assert _status(job)["exitCode"] == 130


def test_missing_job_path_writes_no_status_file(stub, script, fake_run, preflight_calls, tools, tmp_path, monkeypatch):
    stub.STATE.cli = True
    monkeypatch.chdir(tmp_path)
    assert script.main(["--job"]) == 1
    assert list(tmp_path.glob("*.status.json")) == []


def test_unreadable_job_writes_status_beside_the_job(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    job = tmp_path / "missing.json"
    assert script.main(["--job", str(job)]) == 1
    status = json.loads(Path(str(job) + ".status.json").read_text(encoding="utf-8"))
    assert status["exitCode"] == 1 and status["log"] == "" and "cannot read the job file" in status["error"]


def test_status_file_is_written_atomically(script, tmp_path, monkeypatch):
    path = tmp_path / "result.status.json"
    path.write_text("OLD", encoding="utf-8")
    real_replace = script.os.replace

    def broken(src, dst):
        raise OSError("disk unplugged")

    monkeypatch.setattr(script.os, "replace", broken)
    with pytest.raises(OSError):
        script.write_status(str(path), script.make_status(0))
    monkeypatch.setattr(script.os, "replace", real_replace)
    assert path.read_text(encoding="utf-8") == "OLD"          # the old file is not truncated
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".loom-status-")] == []
    script.write_status(str(path), script.make_status(0, produced=["RGB"]))
    assert json.loads(path.read_text(encoding="utf-8"))["produced"] == ["RGB"]


def test_log_lines_are_cut_at_the_limit(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True

    def behave(ctx):
        ctx.log("x" * 2000)
        return fake_run.report

    fake_run.behave = behave
    assert script.main(["--job", str(_job(tmp_path))]) == 0
    texts = [text for text, _c in stub.STATE.logs]
    assert max(len(t) for t in texts) <= script.LINE_LIMIT
    assert any(t.endswith("...") and t.startswith("xxx") for t in texts)


def test_finished_line_names_the_time_and_the_products(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    assert script.main(["--job", str(_job(tmp_path))]) == 0
    texts = [text for text, _c in stub.STATE.logs]
    assert "Finished in 1.5 s. Produced: RGB." in texts


def test_status_log_is_empty_unless_the_run_started(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    preflight_calls.answer = ["File not found for R: /x"]
    job = _job(tmp_path)
    script.main(["--job", str(job)])
    assert _status(job)["log"] == ""
    preflight_calls.answer = []
    fake_run.error = RuntimeError("boom")
    script.main(["--job", str(job)])
    assert _status(job)["log"] == ""


def test_preflight_gets_graxpert_readiness_from_the_resolver(
        stub, script, fake_run, preflight_calls, tools, tmp_path, monkeypatch):
    from loomsiril.loom.tools import graxpert_ai

    stub.STATE.cli = True
    tools.found = {"graxpert-ai": _found("graxpert-ai", "/opt/gx/script.py")}
    seen: dict[str, Any] = {}

    def readiness(resolver, *_a):
        seen["resolver"] = resolver
        return ["GraXpert model missing"]

    monkeypatch.setattr(graxpert_ai, "readiness", readiness)
    assert script.main(["--job", str(_job(tmp_path))]) == 0
    inputs = preflight_calls.inputs[0]
    assert inputs.graxpert_readiness() == ["GraXpert model missing"]
    assert seen["resolver"] is not None


def test_preflight_gets_the_resolver_rejections(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    tools.rejections = {"rcastro": "the probe failed"}
    assert script.main(["--job", str(_job(tmp_path))]) == 0
    rejection = preflight_calls.inputs[0].rejection
    assert rejection["rcastro"] == "the probe failed"
    assert rejection["starnet2"] == ""


def test_preflight_gets_the_spcc_database_and_its_names(stub, script, fake_run, preflight_calls, tools, tmp_path,
                                                        monkeypatch):
    import os as _os

    if not sys.platform.startswith("linux"):
        pytest.skip("the SPCC database path is the Linux one here")
    data = tmp_path / "data"
    folder = data / "siril-spcc-database"
    folder.mkdir(parents=True)
    (folder / "filters.json").write_text(json.dumps([
        {"name": "Baader R", "type": "MONO_FILTER", "channel": "RED"},
        {"name": "Baader B", "type": "MONO_FILTER", "channel": "BLUE"}]), encoding="utf-8")
    monkeypatch.setitem(_os.environ, "XDG_DATA_HOME", str(data))
    stub.STATE.cli = True
    assert script.main(["--job", str(_job(tmp_path))]) == 0
    inputs = preflight_calls.inputs[0]
    assert inputs.spcc_database == str(folder)
    assert inputs.spcc_names("redfilter") == ["Baader R"]
    assert inputs.spcc_names("bluefilter") == ["Baader B"]


def test_preflight_has_no_spcc_database_when_the_folder_is_missing(
        stub, script, fake_run, preflight_calls, tools, tmp_path, monkeypatch):
    import os as _os

    monkeypatch.setitem(_os.environ, "XDG_DATA_HOME", str(tmp_path / "nothing-here"))
    stub.STATE.cli = True
    assert script.main(["--job", str(_job(tmp_path))]) == 0
    inputs = preflight_calls.inputs[0]
    assert inputs.spcc_database is None
    assert inputs.spcc_names("redfilter") == []


def test_installed_icc_profiles_are_passed_to_the_pipeline(
        stub, script, fake_run, preflight_calls, tools, tmp_path, monkeypatch, loomsiril_modules):
    from loomsiril.loom import icc

    sentinel = [{"path": "/profiles/custom.icc", "space": "RGB"}]
    monkeypatch.setattr(icc, "installed_icc_profiles", lambda dirs, list_dir, read: sentinel)
    stub.STATE.cli = True
    assert script.main(["--job", str(_job(tmp_path))]) == 0
    assert fake_run.calls[0]["kwargs"]["installed_icc"] == sentinel


def test_worker_always_posts_done_and_returns_its_result_or_error(script):
    import queue as queue_mod

    events: "queue_mod.Queue[tuple[str, Any]]" = queue_mod.Queue()
    kinds = SimpleNamespace(DONE="done", STAGE="stage", PERCENT="percent")
    got = script._in_worker(lambda: 42, events, kinds, lambda model: None, None)
    assert got == 42
    assert events.get_nowait() == ("done", "finished")

    def failing():
        raise ValueError("the run broke")

    with pytest.raises(ValueError, match="the run broke"):
        script._in_worker(failing, events, kinds, lambda model: None, None)
    assert events.get_nowait() == ("done", "finished")


def test_job_options_go_through_the_migration(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    job = _job(tmp_path, noiseTool="MLDenoise", stretchMethod="MultiscaleAdaptiveStretch")
    assert script.main(["--job", str(job)]) == 0
    config = fake_run.calls[0]["config"]
    assert config["noiseTool"] == "none"
    assert config["stretchMethod"] == "ghs"


def test_job_tools_are_the_configured_paths_for_the_resolver(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    job = _job(tmp_path, tools={"rcastro": "/opt/rc/rc-astro", "not-a-tool": "/x"})
    assert script.main(["--job", str(job)]) == 0
    assert tools.configured["rcastro"] == "/opt/rc/rc-astro"


def test_headless_starnet_acceptance_comes_from_the_job_only(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    from loomsiril.loom.tools.store import ToolStore
    from loomsiril.starnet_licence import STARNET_LICENCE_REV

    from loomsiril.loom.dialog import STARNET_FLAG

    stub.STATE.cli = True
    ToolStore(stub.STATE.configdir).set_flag(STARNET_FLAG, STARNET_LICENCE_REV)
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 0
    assert fake_run.ctx.starnet_revision is None
    job = _job(tmp_path, starnetLicenceAccepted=STARNET_LICENCE_REV)
    assert script.main(["--job", str(job)]) == 0
    assert fake_run.ctx.starnet_revision == STARNET_LICENCE_REV


def test_headless_context_carries_the_resolved_tools_and_the_store(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    tools.found = {"starnet2": _found("starnet2", "/opt/starnet/starnet2")}
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 0
    assert fake_run.ctx.tools == {"starnet2": "/opt/starnet/starnet2"}
    assert fake_run.ctx.tool_store is not None
    assert fake_run.ctx.platform == sys.platform


def test_preflight_gets_the_studio_entitlement_and_spcc_injections(
        stub, script, fake_run, preflight_calls, tools, tmp_path, monkeypatch):
    stub.STATE.cli = True
    tools.found = {"syqon-cli": _found("syqon-cli", "/opt/syqon/syqon-cli")}
    from loomsiril.loom.tools import syqon_studio

    class FakeStudio:
        def __init__(self, exe, ctx, store, **_kw) -> None:
            self.exe = exe
            self.problems: list[str] = []

        def check_entitlement(self, models):
            self.problems = [f"{m} refused by the account" for m in models]
            return {m: False for m in models}

    monkeypatch.setattr(syqon_studio, "SyqonStudio", FakeStudio)
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 0
    inputs = preflight_calls.inputs[0]
    assert inputs.headless is True
    assert inputs.studio_entitlement(["prism-max"]) == ["prism-max refused by the account"]
    assert inputs.graxpert_readiness is not None or tools.found.get("graxpert-ai") is None
    assert inputs.current_starnet_revision
    assert inputs.can_write_dir is not None


def test_studio_probe_folder_is_removed_after_the_checks(
        stub, script, fake_run, preflight_calls, tools, tmp_path, monkeypatch):
    stub.STATE.cli = True
    tools.found = {"syqon-cli": _found("syqon-cli", "/opt/syqon/syqon-cli")}
    from loomsiril.loom.tools import syqon_studio

    seen: dict[str, Any] = {}

    class FakeStudio:
        def __init__(self, exe, ctx, store, **_kw) -> None:
            seen["work"] = str(ctx.work_dir)
            self.problems: list[str] = []

        def check_entitlement(self, models):
            Path(seen["work"], "probe.txt").write_text("x", encoding="utf-8")
            return {}

    monkeypatch.setattr(syqon_studio, "SyqonStudio", FakeStudio)
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 0
    assert "work" in seen
    assert not Path(seen["work"]).exists()


# ---- dialog mode -------------------------------------------------------------------------------------------------


def _ask_returning(config: dict[str, Any], out: Path, starnet: str | None = None):
    from loomsiril.loom.dialog import RunRequest

    def ask(model, siril, image_loaded):
        ask.calls.append((model, image_loaded))
        return RunRequest(dict(config), str(out), starnet)

    ask.calls = []
    return ask


def test_dialog_mode_runs_the_pipeline_on_a_worker_thread(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = False
    stub.STATE.loaded = True
    config = {**_rgb_paths_config(tmp_path)}
    ask = _ask_returning(config, tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=_progress_free) == 0
    assert len(ask.calls) == 1 and ask.calls[0][1] is True
    assert fake_run.calls[0]["config"]["outputDir"] == str(tmp_path / "out")
    assert fake_run.threads == ["loom-run"]
    assert threading.current_thread().name != "loom-run"
    assert any(kind == "info" for kind, _text in stub.STATE.messages)


def _rgb_paths_config(tmp_path: Path) -> dict[str, Any]:
    from loomsiril.loom import config as loom_config

    cfg = loom_config.defaults()
    cfg.update({"paths": _rgb_paths(tmp_path), "filters": {"R": "Baader R", "G": "Baader G", "B": "Baader B"},
                "gradientTool": "none", "stretch": True})
    return cfg


def test_dialog_cancel_returns_without_a_run(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = False
    assert script.main(argv=[], ask=lambda m, s, i: None, show=_progress_free) == 0
    assert fake_run.calls == []


def test_dialog_saves_the_options_and_the_output_folder(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = False
    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=_progress_free) == 0
    saved = json.loads((Path(stub.STATE.configdir) / "loomsiril" / "loom.json").read_text(encoding="utf-8"))
    assert saved["paths"]["G"] == str(tmp_path / "G.fits")
    assert saved["outputDir"] == str(tmp_path / "out")


def test_dialog_cancel_still_saves_what_the_dialog_shows(stub, script, fake_run, preflight_calls, tools, tmp_path):
    """The dialog reopens as it was left, even when it was cancelled."""
    stub.STATE.cli = False

    def ask(model, siril, image_loaded):
        model.set_path("R", str(tmp_path / "R.fits"))
        model.set_output_dir(str(tmp_path / "out"))
        return None

    assert script.main(argv=[], ask=ask, show=_progress_free) == 0
    saved = json.loads((Path(stub.STATE.configdir) / "loomsiril" / "loom.json").read_text(encoding="utf-8"))
    assert saved["paths"]["R"] == str(tmp_path / "R.fits")
    assert saved["outputDir"] == str(tmp_path / "out")
    assert fake_run.calls == []


def test_dialog_preflight_problems_are_one_error_box(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = False
    preflight_calls.answer = ["Output folder cannot be created or written: /ro", "File not found for R: /x"]
    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=_progress_free) == 2
    assert fake_run.calls == []
    kind, text = stub.STATE.messages[-1]
    assert kind == "error"
    assert "File not found for R: /x" in text and "Output folder cannot be created" in text


def test_dialog_cancel_during_the_run_reaches_the_pipeline_and_ends_130(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    from loomsiril.loom.chain import CANCEL_MESSAGE, RunCancelled

    stub.STATE.cli = False

    def behave(ctx):
        deadline = time.monotonic() + FAKE_RUN_TIMEOUT
        while not ctx.cancelled() and time.monotonic() < deadline:
            time.sleep(0.005)
        raise RunCancelled(CANCEL_MESSAGE)

    fake_run.behave = behave

    def show(model):
        model.confirm_cancel(True)   # the user answers Yes in the progress window

    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=show) == 130
    assert fake_run.ctx.cancelled() is True
    assert stub.STATE.messages[-1][0] == "info"


def test_dialog_progress_reaches_the_window_queue_in_order(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = False

    def behave(ctx):
        ctx.on_progress(40.0, "rendering frames")
        ctx.on_progress(80.0, "stretching")
        return fake_run.report

    fake_run.behave = behave
    seen: list[tuple[str, Any]] = []

    def show(model):
        # the window's thread: wait until the worker's done message is applied, as the window's poll does
        deadline = time.monotonic() + FAKE_RUN_TIMEOUT
        while not model.done and time.monotonic() < deadline:
            model.pump()
            time.sleep(0.005)
        seen.append((model.stage, model.percent))

    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=show) == 0
    # the later messages win: the last stage and percent the worker queued are the ones the window shows
    assert seen == [("stretching", 80.0)]
    assert stub.STATE.messages[-1][0] == "info"


def test_dialog_run_error_is_an_error_box(stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = False
    fake_run.error = RuntimeError("disk full")
    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=_progress_free) == 1
    kind, text = stub.STATE.messages[-1]
    assert kind == "error" and "disk full" in text


def test_dialog_starnet_acceptance_comes_from_the_tool_store_flag(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    from loomsiril.loom.dialog import STARNET_FLAG
    from loomsiril.loom.tools.store import ToolStore
    from loomsiril.starnet_licence import STARNET_LICENCE_REV

    stub.STATE.cli = False
    ToolStore(stub.STATE.configdir).set_flag(STARNET_FLAG, STARNET_LICENCE_REV)
    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=_progress_free) == 0
    assert fake_run.ctx.starnet_revision == STARNET_LICENCE_REV


def test_dialog_model_gets_the_saved_options_and_the_found_tools(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = False
    tools.found = {"starnet2": _found("starnet2", "/opt/starnet/starnet2")}
    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=_progress_free) == 0
    model = ask.calls[0][0]
    assert model.get("gradientTool") == "none"
    assert model.tool_rows()[0] is not None
    assert any(row.tool_id == "starnet2" and row.path == "/opt/starnet/starnet2" for row in model.tool_rows())


def test_connect_failure_returns_none_quietly(stub, script, fake_run, preflight_calls, tools, monkeypatch, capsys):
    stub.STATE.connect_ok = False
    assert script.main(["--job", "x.json"]) is None
    assert fake_run.calls == []
    assert "cannot connect" in capsys.readouterr().err


def test_outcome_is_the_same_on_the_second_run_with_the_same_job(
        stub, script, fake_run, preflight_calls, tools, tmp_path):
    stub.STATE.cli = True
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 0
    first = _status(job)
    assert script.main(["--job", str(job)]) == 0
    assert _status(job) == first
    assert len(fake_run.calls) == 2


# ---- the siril-cli runs (marker siril: skipped without siril-cli; run in the Docker image) -------------------------

FILTERS = {"R": "Baader R", "G": "Baader G", "B": "Baader B"}


def _siril_loom(workdir: Path, job: Path, config_home: Path) -> Any:
    """``printf 'requires 1.4.0\npyscript <script> --job <job>\n' | siril-cli -d <dir> -s -`, isolated config folder."""
    import os
    import shutil
    import subprocess

    exe = shutil.which("siril-cli")
    if not exe:
        raise RuntimeError("siril-cli not on PATH (the test is marked siril)")
    scratch = workdir / "scratch-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(config_home)     # the options and the tools store go here, not into a real profile
    env["TMPDIR"] = str(scratch)                  # the tools store must not sit under the temporary folder
    env["LOOMSIRIL_SRC"] = str(REPO)
    text = f"requires 1.4.0\npyscript {SCRIPT} --job {job}\n"
    return subprocess.run([exe, "-d", str(workdir), "-s", "-"], input=text, text=True, capture_output=True,
                          timeout=900, env=env, check=False)


def _synthetic_rgb(workdir: Path) -> dict[str, str]:
    shifts = {"R": (0.0, 0.0), "G": (-2.6, 3.4), "B": (3.1, -2.2)}
    paths = {}
    for index, channel in enumerate(("R", "G", "B")):
        master = synth.make_master(channel, seed=index + 2, shift=shifts[channel], filter_name=FILTERS[channel])
        paths[channel] = str(synth.write_master(workdir / f"{channel}.fits", master))
    return paths


@pytest.mark.siril
@pytest.mark.network  # an RGB set is calibrated with SPCC, which needs Siril's SPCC database (docker/network-setup.sh)
def test_siril_cli_headless_rgb_job_writes_the_result(tmp_path: Path) -> None:
    workdir = tmp_path / "run"
    workdir.mkdir()
    paths = _synthetic_rgb(workdir)
    job = workdir / "job.json"
    job.write_text(json.dumps({
        "paths": {**{k: "" for k in "LHSO"}, **paths},
        "filters": FILTERS,
        "outputDir": str(workdir / "out"),
        "cacheDir": str(workdir / "cache"),
        "gradientTool": "none",
        "stretch": True,
    }), encoding="utf-8")
    run = _siril_loom(workdir, job, tmp_path / "config")
    status = json.loads(Path(str(job) + ".status.json").read_text(encoding="utf-8"))
    assert status["exitCode"] == 0, (status, run.stdout[-2000:], run.stderr[-2000:])
    assert status["ok"] is True and status["produced"] == ["RGB"]
    assert (workdir / "out" / "RGB.fits").is_file()


@pytest.mark.siril
def test_siril_cli_job_without_output_folder_is_exit_code_2(tmp_path: Path) -> None:
    workdir = tmp_path / "run"
    workdir.mkdir()
    paths = _synthetic_rgb(workdir)
    job = workdir / "job.json"
    job.write_text(json.dumps({"paths": {**{k: "" for k in "LHSO"}, **paths}, "filters": FILTERS,
                               "gradientTool": "none"}), encoding="utf-8")
    _siril_loom(workdir, job, tmp_path / "config")
    status = json.loads(Path(str(job) + ".status.json").read_text(encoding="utf-8"))
    assert status["exitCode"] == 2
    assert "No output folder: set outputDir" in status["problems"]


def test_import_finds_the_checkout_the_script_sits_in(script, monkeypatch, tmp_path):
    """No LOOMSIRIL_SRC: the repository holding Loom/loom_preprocess.py goes on sys.path (the clone added as a script folder)."""
    monkeypatch.delenv(script.LOOMSIRIL_SRC_ENV, raising=False)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p or ".").resolve() != REPO])
    script._import_loomsiril()
    assert Path(sys.path[0]).resolve() == REPO


def test_loomsiril_src_comes_before_the_checkout(script, monkeypatch, tmp_path):
    other = tmp_path / "other-checkout"
    monkeypatch.setenv(script.LOOMSIRIL_SRC_ENV, str(other))
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p or ".").resolve() != REPO])
    script._import_loomsiril()
    assert sys.path[0] == str(other)
    assert Path(sys.path[1]).resolve() == REPO


def test_a_copy_outside_a_checkout_adds_nothing(stub, monkeypatch, tmp_path):
    copy = tmp_path / "scripts" / "loom_preprocess.py"
    copy.parent.mkdir()
    copy.write_bytes(SCRIPT.read_bytes())
    spec = importlib.util.spec_from_file_location("loom_copy_under_test", copy)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "loom_copy_under_test", mod)
    spec.loader.exec_module(mod)
    monkeypatch.delenv(mod.LOOMSIRIL_SRC_ENV, raising=False)
    before = list(sys.path)
    mod._import_loomsiril()
    assert sys.path == before
