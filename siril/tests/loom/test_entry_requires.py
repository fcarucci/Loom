"""The Siril entry script (Loom/loom_preprocess.py) asks for Siril 1.4.0 first, in both modes.

Dialog mode (Siril's window) and headless mode (siril-cli with a job file) must each send ``requires 1.4.0`` through
``siril.cmd`` before any other Siril command. The sirilpy stub (tests/sirilpy_stub) records the commands; the pipeline,
the preflight, the resolver and the dialog are the fakes from tests/loom/test_entry.py.
"""

from __future__ import annotations

from pathlib import Path

from .test_entry import (  # noqa: F401  (pytest fixtures, shared with the entry tests)
    FakeRun,
    _ask_returning,
    _job,
    _progress_free,
    _rgb_paths_config,
    _status,
    fake_run,
    loomsiril_modules,
    preflight_calls,
    script,
    stub,
    temp_is_elsewhere,
    tools,
)

REQUIRES = ("requires", "1.4.0")


def test_headless_mode_requires_siril_1_4_before_anything_else(
        stub, script, fake_run, preflight_calls, tools, tmp_path: Path):
    stub.STATE.cli = True
    job = _job(tmp_path)
    assert script.main(["--job", str(job)]) == 0
    assert stub.STATE.commands, "no Siril command was sent"
    assert stub.STATE.commands[0] == REQUIRES
    assert _status(job)["ok"] is True


def test_dialog_mode_requires_siril_1_4_before_anything_else(
        stub, script, fake_run, preflight_calls, tools, tmp_path: Path):
    stub.STATE.cli = False
    ask = _ask_returning(_rgb_paths_config(tmp_path), tmp_path / "out")
    assert script.main(argv=[], ask=ask, show=_progress_free) == 0
    assert ask.calls, "the dialog was not shown"
    assert stub.STATE.commands, "no Siril command was sent"
    assert stub.STATE.commands[0] == REQUIRES


def test_requires_is_sent_once_per_headless_run(
        stub, script, fake_run, preflight_calls, tools, tmp_path: Path):
    stub.STATE.cli = True
    script.main(["--job", str(_job(tmp_path))])
    assert stub.STATE.commands.count(REQUIRES) == 1
