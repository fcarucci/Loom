"""RunContext: defaults, tool lookup and the adapter ToolContext it builds."""

from __future__ import annotations

import pytest

from loomsiril.loom.runctx import RunContext, StageOutput
from loomsiril.loom.tools import rcastro, starnet, syqon_legacy, syqon_studio


def test_defaults_are_quiet_and_never_cancelled(tmp_path) -> None:
    ctx = RunContext(ops=None, work_dir=str(tmp_path))
    assert ctx.cancelled() is False and ctx.log("x") is None and ctx.tools == {} and ctx.extra == {}


def test_tool_lookup_names_a_missing_tool(tmp_path) -> None:
    ctx = RunContext(ops=None, work_dir=str(tmp_path), tools={"rcastro": "/x/rc-astro"})
    assert ctx.tool("rcastro") == "/x/rc-astro"
    with pytest.raises(KeyError, match="starnet2"):
        ctx.tool("starnet2")


@pytest.mark.parametrize("module", [rcastro, starnet, syqon_studio, syqon_legacy])
def test_tool_context_matches_each_adapter(tmp_path, module) -> None:
    seen = []
    ctx = RunContext(ops=None, work_dir=str(tmp_path), log=seen.append, platform="win32")
    tc = ctx.tool_context(module.ToolContext)
    assert isinstance(tc, module.ToolContext)
    assert tc.work_dir == str(tmp_path) and tc.platform == "win32" and tc.cancelled() is False
    tc.log("hello")
    assert seen == ["hello"]


@pytest.mark.parametrize("module", [rcastro, starnet, syqon_studio, syqon_legacy])
def test_tool_context_passes_cancel_and_progress_through(tmp_path, module) -> None:
    flags, progress = [True], []
    ctx = RunContext(ops=None, work_dir=str(tmp_path), cancelled=lambda: flags[0],
                     on_progress=lambda pct, text: progress.append((pct, text)))
    tc = ctx.tool_context(module.ToolContext)
    assert tc.cancelled() is True
    flags[0] = False
    assert tc.cancelled() is False
    tc.on_progress(42.0, "half")
    assert progress == [(42.0, "half")]


def test_default_on_progress_is_none_and_cancel_is_false(tmp_path) -> None:
    ctx = RunContext(ops=None, work_dir=str(tmp_path))
    assert ctx.on_progress is None and ctx.cancelled() is False
    assert ctx.tool_context(rcastro.ToolContext).on_progress is None


def test_stage_output_defaults() -> None:
    out = StageOutput("/w/a.fits")
    assert out.companions == {} and out.skip_cache is False
    assert StageOutput("/w/b.fits", {"stars": "/w/s.fits"}, True).companions["stars"] == "/w/s.fits"
