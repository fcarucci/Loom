"""The correction stages (loomsiril/loom/stages_correct.py) against RecordingOps: no Siril, no executable.

Covered: the stage parameters (Loom's shape and order for every gradient x sharpen combination), the runners that
exist for each combination, the solve stage (a solved input is copied, not solved again; the residual verdict is
logged; the checker's skip line appears once per run), the GraXpert silent-failure guard, the aberration routing,
ingest, and the master-quality cache. The adapter classes that run executables are replaced by recorders.
"""

from __future__ import annotations

import filecmp
import json
import math
import os
from pathlib import Path
from typing import Any

import pytest

from loomsiril.io_fits import read_fits
from loomsiril.loom import cache, levels, stages_correct as sc
from loomsiril.loom.runctx import RunContext, StageOutput
from loomsiril.loom.siril_ops import RecordingOps, SirilOpsError
from loomsiril.loom.stagekit import StageFailed
from loomsiril.loom import preflight
from loomsiril.loom.residuals import ResidualChecker
from loomsiril.loom.tools import rcastro, syqon_legacy, syqon_studio
from loomsiril.loom.tools.runner import ToolCancelled

from . import synth

BXT = "BlurXTerminator"
SYQON = "SyQon Parallax"
STUDIO_AB = "SyQon Studio Parallax"
DEEP = "SyQon Studio Deep Gradient"
MGC = {"tool": "subsky", "model": "poly", "degree": 2, "samples": 20, "tolerance": 1.0}
GRAXPERT_EXE = "/opt/graxpert/GraXpert-AI.py"
STUDIO_EXE = "/opt/syqon/syqon-cli"
RC_EXE = "/opt/rc-astro/rc-astro"
PARALLAX_EXE = "/opt/syqon/parallax_cli"


def _config(gradient: str | None = "none", sharpen: str | None = None, **extra: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {"gradientTool": gradient, "smoothing": 0.5, "sharpenTool": sharpen,
                           "parallaxFamily": "classic"}
    cfg.update(extra)
    return cfg


def _ctx(tmp_path: Path, tools: dict[str, str] | None = None, ops: RecordingOps | None = None,
         **kw: Any) -> tuple[RunContext, RecordingOps, list[str], list[str]]:
    logs: list[str] = []
    warns: list[str] = []
    ops = ops or RecordingOps(tmp_path)
    ctx = RunContext(ops=ops, work_dir=str(tmp_path), log=logs.append, warn=warns.append,
                     tools=dict(tools or {}), **kw)
    return ctx, ops, logs, warns


def _names(ops: RecordingOps) -> list[str]:
    return [name for name, _args, _kw in ops.calls]


def _master(tmp_path: Path, channel: str = "L", name: str = "in.fits", **kw: Any) -> Path:
    kw.setdefault("w", 64)
    kw.setdefault("h", 48)
    return synth.write_master(tmp_path / name, synth.make_master(channel, **kw))


# ---- stage parameters -------------------------------------------------------------------------------------------------

GRADIENTS = [
    ({"gradientTool": "none"}, levels.GRADIENT_TOOL_NONE),
    ({"gradientTool": "GraXpert"}, levels.GRADIENT_TOOL_GRAXPERT),
    ({"gradientTool": DEEP}, levels.GRADIENT_TOOL_STUDIO),
    ({"useGraXpert": True}, levels.GRADIENT_TOOL_GRAXPERT),
]
SHARPENS = [None, "none", BXT, SYQON, STUDIO_AB]


@pytest.mark.parametrize("studio_found", [False, True])
@pytest.mark.parametrize("sharpen", SHARPENS)
@pytest.mark.parametrize("grad_fields,gradient", GRADIENTS)
def test_broadband_params_have_loom_shape_and_order(grad_fields, gradient, sharpen, studio_found) -> None:
    config = {"smoothing": 0.25, "sharpenTool": sharpen, "parallaxFamily": "classic", **grad_fields}
    for channel in ("L", "R", "G", "B"):
        params = sc.broadband_stage_params(channel, config, studio_found)
        expected_keys = ["solve"]
        if gradient == levels.GRADIENT_TOOL_NONE:
            expected_keys.append("mgc")
        expected_keys += ["graxpert", "aberration"]
        assert list(params) == expected_keys
        assert "spfc" not in params
        assert params["solve"] == {}
        assert params["graxpert"] == levels.gradient_stage_params(config)
        assert params["aberration"] == levels.aberration_params(config, studio_found)
        if gradient == levels.GRADIENT_TOOL_NONE:
            assert params["mgc"] == MGC


def test_gradient_params_keep_loom_shapes() -> None:
    assert sc.broadband_stage_params("L", _config("GraXpert"), False)["graxpert"] == {"enabled": True,
                                                                                    "smoothing": 0.5}
    assert sc.broadband_stage_params("L", _config("none"), False)["graxpert"] == {"enabled": False,
                                                                                 "smoothing": 0.5}
    assert sc.broadband_stage_params("L", _config(DEEP), False)["graxpert"] == {
        "enabled": True, "tool": DEEP, "model": "deep-gradient"}


@pytest.mark.parametrize("sharpen,studio_found,expected", [
    (None, False, {"tool": "none", "photometry": "linearfit-v1"}),
    (BXT, False, {"tool": BXT, "photometry": "linearfit-v1"}),
    (BXT, True, {"tool": BXT, "photometry": "linearfit-v1", "corrector": STUDIO_AB, "family": "classic"}),
    (SYQON, True, {"tool": SYQON, "photometry": "linearfit-v1"}),
    (STUDIO_AB, False, {"tool": STUDIO_AB, "photometry": "linearfit-v1", "family": "classic"}),
])
def test_aberration_params_are_keyed_on_the_real_corrector(sharpen, studio_found, expected) -> None:
    params = sc.broadband_stage_params("G", _config("none", sharpen), studio_found)
    assert params["aberration"] == expected
    assert list(params["aberration"]) == list(expected)


def test_mgc_is_absent_for_graxpert_and_studio_gradients() -> None:
    for gradient in ("GraXpert", DEEP):
        assert "mgc" not in sc.broadband_stage_params("R", _config(gradient), False)


def test_no_stage_params_for_narrowband_channels_in_the_broadband_builder() -> None:
    for channel in ("H", "S", "O", "X"):
        with pytest.raises(ValueError):
            sc.broadband_stage_params(channel, _config("none"), False)
        with pytest.raises(ValueError):
            sc.broadband_runners(RunContext(ops=None, work_dir="."), channel, _config("none"))


@pytest.mark.parametrize("graxpert_narrowband,gradient,expected", [
    (False, "GraXpert", None),
    (True, "none", None),
    (True, "GraXpert", {"graxpert": {"enabled": True, "smoothing": 0.5}}),
    (True, DEEP, {"graxpert": {"enabled": True, "tool": DEEP, "model": "deep-gradient"}}),
])
def test_narrowband_params_are_the_gradient_stage_only(graxpert_narrowband, gradient, expected) -> None:
    config = _config(gradient, graxpertNarrowband=graxpert_narrowband)
    assert sc.narrowband_stage_params(config) == expected
    if expected is not None:
        assert "mgc" not in expected


def test_narrowband_params_are_none_without_a_config() -> None:
    assert sc.narrowband_stage_params(None) is None


def test_source_key_is_the_cache_file_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "src.fits"
    path.write_bytes(b"x" * 17)
    st = os.stat(path)
    assert sc.source_key(path) == cache.file_source_key(os.fspath(path), st.st_size, st.st_mtime)


# ---- runner presence --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("gradient,sharpen,expected", [
    ("none", None, {"solve", "mgc"}),
    ("none", BXT, {"solve", "mgc", "aberration"}),
    ("GraXpert", None, {"solve", "graxpert"}),
    ("GraXpert", SYQON, {"solve", "graxpert", "aberration"}),
    (DEEP, STUDIO_AB, {"solve", "graxpert", "aberration"}),
])
def test_broadband_runners_exist_where_there_is_work(tmp_path: Path, gradient: str, sharpen: Any,
                                                     expected: set[str]) -> None:
    ctx, _ops, _logs, _warns = _ctx(tmp_path)
    runners = sc.broadband_runners(ctx, "L", _config(gradient, sharpen))
    assert set(runners) == expected


def test_narrowband_runners_follow_the_narrowband_params(tmp_path: Path) -> None:
    ctx, _ops, _logs, _warns = _ctx(tmp_path, tools={"graxpert-ai": GRAXPERT_EXE})
    assert sc.narrowband_runners(ctx, "H", _config("GraXpert", graxpertNarrowband=False)) == {}
    assert set(sc.narrowband_runners(ctx, "S", _config("GraXpert", graxpertNarrowband=True))) == {"graxpert"}
    with pytest.raises(ValueError):
        sc.narrowband_runners(ctx, "R", _config("GraXpert", graxpertNarrowband=True))


# ---- ingest -----------------------------------------------------------------------------------------------------------


def test_ingest_loads_and_saves_a_working_plate_and_leaves_the_source_alone(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "work").mkdir()
    src = _master(tmp_path / "src", "L")
    before = src.read_bytes()
    ctx, ops, _logs, _warns = _ctx(tmp_path / "work")
    out = sc.ingest_channel(ctx, "L", src)
    assert out == os.path.join(str(tmp_path / "work"), "L_src.fits")
    assert os.path.isfile(out)
    assert _names(ops) == ["set32bits", "load", "save"]
    assert src.read_bytes() == before
    planes, _header = read_fits(out)
    assert planes[0].shape == (48, 64)


# ---- solve ------------------------------------------------------------------------------------------------------------


class _StubChecker:
    """A residual checker with a fixed answer (the real one needs a catalogue)."""

    skipped = False

    def __init__(self, verdict: str, median: float = 0.1) -> None:
        self.verdict = verdict
        self.median = median
        self.calls: list[tuple[str, int]] = []
        self.headers: list[Any] = []

    def check(self, name: str, stars: Any, header: Any) -> dict[str, Any]:
        self.calls.append((name, len(stars)))
        self.headers.append(header)
        return {"name": name, "median_px": self.median, "n_matched": 9, "n_stars": len(stars), "n_retained": 8,
                "verdict": self.verdict}


def _stars(n: int = 12) -> list[dict[str, float]]:
    return [{"xpos": float(10 + i), "ypos": float(5 + 2 * i), "fwhmx": 3.0, "fwhmy": 3.0} for i in range(n)]


def test_solved_input_is_copied_and_not_solved_again(tmp_path: Path) -> None:
    src = _master(tmp_path, "L", with_wcs=True)
    ctx, ops, logs, _warns = _ctx(tmp_path, ops=RecordingOps(tmp_path, stars=_stars()))
    out = tmp_path / "out.fits"
    result = sc.broadband_runners(ctx, "L", _config("none"))["solve"](src, out)
    assert isinstance(result, StageOutput)
    assert result.path == os.fspath(out)
    assert "platesolve" not in _names(ops)
    assert filecmp.cmp(src, out, shallow=False)
    assert any("already has an astrometric solution" in line for line in logs)


def test_unsolved_input_is_plate_solved_with_the_header_hints(tmp_path: Path) -> None:
    src = _master(tmp_path, "L", with_wcs=False)
    wcs = synth.make_master("L", w=64, h=48, seed=1).header
    _planes, src_header = read_fits(src)

    def solve(ops: RecordingOps, force: bool, focal: Any, pixel_size: Any) -> None:
        ops._header.update({k: v for k, v in wcs.items() if k.startswith(("CTYPE", "CRVAL", "CRPIX", "CD"))})

    ctx, ops, _logs, _warns = _ctx(tmp_path, ops=RecordingOps(tmp_path, handlers={"platesolve": solve},
                                                             stars=_stars()))
    out = tmp_path / "solved.fits"
    sc.broadband_runners(ctx, "L", _config("none"))["solve"](src, out)
    solve_calls = [args for name, args, _kw in ops.calls if name == "platesolve"]
    assert len(solve_calls) == 1
    force, focal, pixel_size = solve_calls[0]
    assert force is False
    assert focal == pytest.approx(src_header["FOCALLEN"])
    assert pixel_size == pytest.approx(src_header["XPIXSZ"])
    _planes, header = read_fits(out)
    assert "CTYPE1" in header


def test_failed_plate_solve_fails_the_run(tmp_path: Path) -> None:
    src = _master(tmp_path, "L", with_wcs=False)
    ops = RecordingOps(tmp_path, fail={"platesolve": RuntimeError("no solution")}, stars=_stars())
    ctx, _ops, _logs, _warns = _ctx(tmp_path, ops=ops)
    with pytest.raises(StageFailed, match="plate solving failed on L"):
        sc.broadband_runners(ctx, "L", _config("none"))["solve"](src, tmp_path / "x.fits")


@pytest.mark.parametrize("verdict,level,text", [
    ("ok", "log", "median deviation 0.100 px"),
    ("poor", "warn", "POOR"),
    ("bad", "warn", "THE SOLUTION IS WRONG"),
    ("unknown", "warn", "no usable deviation statistic"),
])
def test_residual_verdict_is_logged_as_loom_does(tmp_path: Path, verdict: str, level: str, text: str) -> None:
    src = _master(tmp_path, "L", with_wcs=True)
    checker = _StubChecker(verdict, median=0.1 if verdict == "ok" else 3.5)
    ctx, _ops, logs, warns = _ctx(tmp_path, ops=RecordingOps(tmp_path, stars=_stars()), residuals=checker)
    sc.broadband_runners(ctx, "L", _config("none"))["solve"](src, tmp_path / "out.fits")
    assert checker.calls == [("L", 12)]
    target = logs if level == "log" else warns
    assert any(text in line for line in target)


def test_residual_checker_skip_line_appears_once_per_run(tmp_path: Path) -> None:
    src = _master(tmp_path, "L", with_wcs=True)
    lines: list[str] = []
    checker = ResidualChecker(lambda centre, radius, g_max: None, lines.append)
    ctx, _ops, _logs, _warns = _ctx(tmp_path, ops=RecordingOps(tmp_path, stars=_stars()), residuals=checker)
    runner = sc.broadband_runners(ctx, "L", _config("none"))["solve"]
    runner(src, tmp_path / "a.fits")
    runner(src, tmp_path / "b.fits")
    skips = [line for line in lines if "is unreachable" in line]
    assert len(skips) == 1


def test_a_failing_residual_check_never_fails_the_run(tmp_path: Path) -> None:
    src = _master(tmp_path, "L", with_wcs=True)

    class _Broken(_StubChecker):
        def check(self, name: str, stars: Any, header: Any) -> Any:
            raise ValueError("boom")

    ctx, _ops, _logs, warns = _ctx(tmp_path, ops=RecordingOps(tmp_path, stars=_stars()), residuals=_Broken("ok"))
    result = sc.broadband_runners(ctx, "L", _config("none"))["solve"](src, tmp_path / "out.fits")
    assert result.path.endswith("out.fits")
    assert any("could not be verified" in line for line in warns)


# ---- mgc --------------------------------------------------------------------------------------------------------------


def test_mgc_runner_calls_subsky_on_the_channel(tmp_path: Path) -> None:
    src = _master(tmp_path, "G")

    def subsky(ops: RecordingOps, degree: int, samples: int, tolerance: float) -> None:
        ops.set_pixels([p * 0.5 for p in ops.pixels()])

    ctx, ops, _logs, _warns = _ctx(tmp_path, ops=RecordingOps(tmp_path, handlers={"subsky": subsky}))
    runner = sc.broadband_runners(ctx, "G", _config("none"))["mgc"]
    out = runner(src, tmp_path / "g_out.fits").path
    assert ("subsky", (2, 20, 1.0), {}) in ops.calls
    assert os.path.isfile(out)


# ---- graxpert ---------------------------------------------------------------------------------------------------------


def test_graxpert_runner_passes_the_smoothing_and_the_script(tmp_path: Path) -> None:
    src = _master(tmp_path, "R")

    def script(ops: RecordingOps, path: str, args: Any) -> None:
        ops.set_pixels([p - 0.01 for p in ops.pixels()])

    ctx, ops, _logs, _warns = _ctx(tmp_path, tools={"graxpert-ai": GRAXPERT_EXE},
                                   ops=RecordingOps(tmp_path, handlers={"pyscript": script}))
    out = sc.broadband_runners(ctx, "R", _config("GraXpert", smoothing=0.25))["graxpert"](
        src, tmp_path / "r_out.fits").path
    assert ("pyscript", (GRAXPERT_EXE, ("-bge", "-correction=subtraction", "-smoothing=0.25")), {}) in ops.calls
    assert os.path.isfile(out)


def test_graxpert_silent_failure_is_a_stage_failure(tmp_path: Path) -> None:
    src = _master(tmp_path, "R")
    ctx, _ops, _logs, _warns = _ctx(tmp_path, tools={"graxpert-ai": GRAXPERT_EXE})
    with pytest.raises(StageFailed, match="GraXpert did not change R"):
        sc.broadband_runners(ctx, "R", _config("GraXpert"))["graxpert"](src, tmp_path / "x.fits")


def test_graxpert_reported_failure_is_a_stage_failure(tmp_path: Path) -> None:
    src = _master(tmp_path, "R")
    ops = RecordingOps(tmp_path, fail={"pyscript": RuntimeError("exit 1")})
    ctx, _ops, _logs, _warns = _ctx(tmp_path, tools={"graxpert-ai": GRAXPERT_EXE}, ops=ops)
    with pytest.raises(StageFailed, match="GraXpert failed on R"):
        sc.broadband_runners(ctx, "R", _config("GraXpert"))["graxpert"](src, tmp_path / "x.fits")


def test_studio_deep_gradient_routes_to_the_studio_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    class _Studio:
        def __init__(self, exe: Any, ctx: Any, store: Any) -> None:
            calls.append(("init", exe))

        def deep_gradient(self, settings: Any, in_fits: str, out_fits: str) -> None:
            calls.append(("deep_gradient", settings, os.path.basename(in_fits), os.path.basename(out_fits)))

    monkeypatch.setattr(syqon_studio, "SyqonStudio", _Studio)
    src = _master(tmp_path, "R")
    ctx, _ops, _logs, _warns = _ctx(tmp_path, tools={"syqon-cli": STUDIO_EXE})
    out = sc.broadband_runners(ctx, "R", _config(DEEP))["graxpert"](src, tmp_path / "d.fits").path
    assert calls == [("init", STUDIO_EXE), ("deep_gradient", {}, "in.fits", "d.fits")]
    assert out.endswith("d.fits")


# ---- aberration routing -----------------------------------------------------------------------------------------------


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    class _RcAstro:
        def __init__(self, exe: Any, ctx: Any) -> None:
            calls.append(("rcastro", exe))

        def aberration(self, in_fits: Any, out_fits: Any) -> None:
            calls.append(("bxt-correct-only", os.path.basename(os.fspath(in_fits))))

    class _Studio:
        def __init__(self, exe: Any, ctx: Any, store: Any) -> None:
            calls.append(("studio", exe))

        def parallax(self, domain: str, settings: Any, in_fits: Any, out_fits: Any) -> None:
            calls.append(("studio-parallax", domain, settings))

    def _parallax(exe: Any, ctx: Any, in_fits: Any, out_fits: Any, operation: str, amount: float,
                  linked: bool) -> None:
        calls.append(("syqon-parallax", exe, operation, amount, linked))

    monkeypatch.setattr(rcastro, "RcAstro", _RcAstro)
    monkeypatch.setattr(syqon_studio, "SyqonStudio", _Studio)
    monkeypatch.setattr(syqon_legacy, "parallax", _parallax)
    return calls


@pytest.mark.parametrize("studio_found,sharpen,expected", [
    (False, BXT, [("rcastro", RC_EXE), ("bxt-correct-only", "in.fits")]),
    (True, BXT, [("studio", STUDIO_EXE), ("studio-parallax", "linear",
                                          {"parallax": {"correction": True, "reduction": 0, "deblur": 0,
                                                        "family": "classic"}})]),
    (False, SYQON, [("syqon-parallax", PARALLAX_EXE, "aberration", 0.0, False)]),
    (True, SYQON, [("syqon-parallax", PARALLAX_EXE, "aberration", 0.0, False)]),
    (True, STUDIO_AB, [("studio", STUDIO_EXE), ("studio-parallax", "linear",
                                                 {"parallax": {"correction": True, "reduction": 0, "deblur": 0,
                                                               "family": "classic"}})]),
])
def test_each_aberration_corrector_is_routed_to_its_tool(tmp_path: Path, routed: list, studio_found: bool,
                                                         sharpen: str, expected: list) -> None:
    src = _master(tmp_path, "L")
    tools = {"rcastro": RC_EXE, "parallax_cli": PARALLAX_EXE}
    if studio_found:
        tools["syqon-cli"] = STUDIO_EXE
    ctx, _ops, _logs, _warns = _ctx(tmp_path, tools=tools)
    runner = sc.broadband_runners(ctx, "L", _config("none", sharpen))["aberration"]
    runner(src, tmp_path / "ab.fits")
    assert routed == expected


def test_no_aberration_runner_without_a_sharpen_tool(tmp_path: Path) -> None:
    ctx, _ops, _logs, _warns = _ctx(tmp_path)
    assert "aberration" not in sc.broadband_runners(ctx, "L", _config("none", None))
    assert "aberration" not in sc.broadband_runners(ctx, "L", _config("none", "none"))


# ---- master quality ---------------------------------------------------------------------------------------------------


def _analysis(path: Any) -> dict[str, Any]:
    return {"fwhm": 3.2, "roundness": 0.6, "bgnoise": 0.001, "nbstars": 250}


def test_master_quality_key_has_size_and_mtime_in_milliseconds(tmp_path: Path) -> None:
    path = tmp_path / "m.fits"
    path.write_bytes(b"abc")
    st = os.stat(path)
    assert sc.master_quality_key(path) == f"siril1|{os.fspath(path)}|3|{st.st_mtime_ns // 1_000_000}"
    assert sc.master_quality_key(tmp_path / "missing.fits").endswith("|0|0")


def test_master_quality_measures_then_answers_from_the_cache(tmp_path: Path) -> None:
    path = _master(tmp_path, "L")
    cache_dir = tmp_path / "cache"
    ops = RecordingOps(tmp_path, analyse=_analysis)
    first = sc.master_quality(ops, [path], cache_dir)
    assert set(first) == {os.fspath(path)}
    rec = first[os.fspath(path)]
    assert rec["fwhm"] == pytest.approx(3.2)
    assert rec["eccentricity"] == pytest.approx(math.sqrt(1 - 0.36))
    assert rec["noise"] == pytest.approx(0.001)
    assert rec["stars"] == 250
    assert [name for name, _a, _k in ops.calls].count("analyse") == 1

    table = json.loads((cache_dir / "master-quality.json").read_text(encoding="utf-8"))
    assert list(table) == [sc.master_quality_key(path)]

    def boom(_path: Any) -> dict[str, Any]:
        raise AssertionError("the cache should answer")

    again = sc.master_quality(RecordingOps(tmp_path, analyse=boom), [path], cache_dir)
    assert again == first


def test_master_quality_measures_again_when_the_file_changes(tmp_path: Path) -> None:
    path = _master(tmp_path, "L")
    cache_dir = tmp_path / "cache"
    sc.master_quality(RecordingOps(tmp_path, analyse=_analysis), [path], cache_dir)
    _master(tmp_path, "L", w=80)  # a re-stack: new size, new time
    ops = RecordingOps(tmp_path, analyse=lambda p: {"fwhm": 4.0, "roundness": 1.0, "bgnoise": 0.0, "nbstars": 9})
    again = sc.master_quality(ops, [path], cache_dir)
    assert again[os.fspath(path)]["fwhm"] == 4.0
    assert [name for name, _a, _k in ops.calls].count("analyse") == 1


def test_master_quality_skips_what_cannot_be_measured(tmp_path: Path) -> None:
    good = _master(tmp_path, "L", name="good.fits")
    bad = _master(tmp_path, "R", name="bad.fits")
    cache_dir = tmp_path / "cache"
    warns: list[str] = []

    def analyse(path: Any) -> dict[str, Any]:
        if os.fspath(path) == os.fspath(bad):
            raise SirilOpsError("analyse", RuntimeError("no stars"))
        return _analysis(path)

    result = sc.master_quality(RecordingOps(tmp_path, analyse=analyse), [good, bad], cache_dir, warn=warns.append)
    assert set(result) == {os.fspath(good)}
    assert any("could not measure" in line for line in warns)
    table = json.loads((cache_dir / "master-quality.json").read_text(encoding="utf-8"))
    assert len(table) == 1


def test_master_quality_ignores_a_damaged_cache_file(tmp_path: Path) -> None:
    path = _master(tmp_path, "L")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "master-quality.json").write_text("{not json", encoding="utf-8")
    result = sc.master_quality(RecordingOps(tmp_path, analyse=_analysis), [path], cache_dir)
    assert result[os.fspath(path)]["stars"] == 250


# ---- review round: solved rule, unknown tools, cancellation, skip_cache, post-solve header ------------------------


@pytest.mark.parametrize("keywords,solved", [
    ({"CTYPE1": "RA---TAN"}, False),
    ({"CD1_1": 1.0}, False),
    ({"CDELT1": 1.0}, False),
    ({"PC1_1": 1.0}, False),
    ({"CTYPE1": "RA---TAN", "CRVAL1": 10.0}, True),
    ({"ctype1": "RA---TAN", "crval1": 10.0}, True),
    ({"CRVAL1": 10.0}, False),
])
def test_solved_rule_is_preflights_rule(keywords: dict[str, Any], solved: bool) -> None:
    assert sc._has_astrometric_solution(keywords) is solved
    pairs = {"keywords": [{"name": k, "value": str(v)} for k, v in keywords.items()]}
    assert preflight._has_wcs(pairs) is solved


def test_unsolved_solve_hands_the_post_solve_header_to_the_checker(tmp_path: Path) -> None:
    src = _master(tmp_path, "L", with_wcs=False)
    wcs = synth.make_master("L", w=64, h=48, seed=1).header

    def solve(ops: RecordingOps, force: bool, focal: Any, pixel_size: Any) -> None:
        ops._header.update({k: v for k, v in wcs.items() if k.startswith(("CTYPE", "CRVAL", "CRPIX", "CD"))})

    checker = _StubChecker("ok")
    ctx, _ops, _logs, _warns = _ctx(tmp_path, ops=RecordingOps(tmp_path, handlers={"platesolve": solve},
                                                              stars=_stars()), residuals=checker)
    sc.broadband_runners(ctx, "L", _config("none"))["solve"](src, tmp_path / "solved.fits")
    assert len(checker.headers) == 1
    header = checker.headers[0]
    assert header["CTYPE1"] == wcs["CTYPE1"]
    assert header["CRVAL1"] == pytest.approx(wcs["CRVAL1"])


@pytest.mark.parametrize("gradient_fields", [{"gradientTool": "Bogus"}, {"gradientTool": "graxpert"}])
def test_unknown_gradient_tool_raises_as_loom_does(tmp_path: Path, gradient_fields: dict[str, Any]) -> None:
    ctx, _ops, _logs, _warns = _ctx(tmp_path)
    config = {"smoothing": 0.5, "sharpenTool": None, **gradient_fields}
    name = gradient_fields["gradientTool"]
    with pytest.raises(ValueError, match=f"Unknown gradient removal tool: {name}"):
        sc.broadband_runners(ctx, "L", config)
    with pytest.raises(ValueError, match="Unknown gradient removal tool"):
        sc.narrowband_runners(ctx, "H", dict(config, graxpertNarrowband=True))
    assert sc.narrowband_runners(ctx, "H", dict(config, graxpertNarrowband=False)) == {}


def test_cancellation_propagates_out_of_the_graxpert_runner(tmp_path: Path) -> None:
    src = _master(tmp_path, "R")

    def cancelled(ops: RecordingOps, path: Any, args: Any) -> None:
        raise ToolCancelled("cancelled")

    ctx, _ops, _logs, _warns = _ctx(tmp_path, tools={"graxpert-ai": GRAXPERT_EXE},
                                    ops=RecordingOps(tmp_path, handlers={"pyscript": cancelled}))
    with pytest.raises(ToolCancelled):
        sc.broadband_runners(ctx, "R", _config("GraXpert"))["graxpert"](src, tmp_path / "x.fits")


def test_cancellation_propagates_out_of_the_studio_gradient_runner(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    class _Studio:
        def __init__(self, exe: Any, ctx: Any, store: Any) -> None:
            pass

        def deep_gradient(self, settings: Any, in_fits: str, out_fits: str) -> None:
            raise ToolCancelled("cancelled")

    monkeypatch.setattr(syqon_studio, "SyqonStudio", _Studio)
    src = _master(tmp_path, "R")
    ctx, _ops, _logs, _warns = _ctx(tmp_path, tools={"syqon-cli": STUDIO_EXE})
    with pytest.raises(ToolCancelled):
        sc.broadband_runners(ctx, "R", _config(DEEP))["graxpert"](src, tmp_path / "x.fits")


@pytest.mark.parametrize("sharpen,studio", [(BXT, False), (SYQON, False), (STUDIO_AB, True)])
def test_cancellation_propagates_out_of_each_aberration_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               sharpen: str, studio: bool) -> None:
    def cancel(*_args: Any, **_kw: Any) -> None:
        raise ToolCancelled("cancelled")

    class _Adapter:
        def __init__(self, *args: Any, **kw: Any) -> None:
            pass

        aberration = cancel
        parallax = cancel
        deep_gradient = cancel

    monkeypatch.setattr(rcastro, "RcAstro", _Adapter)
    monkeypatch.setattr(syqon_legacy, "parallax", cancel)
    monkeypatch.setattr(syqon_studio, "SyqonStudio", _Adapter)
    src = _master(tmp_path, "L")
    tools = {"rcastro": RC_EXE, "parallax_cli": PARALLAX_EXE}
    if studio:
        tools["syqon-cli"] = STUDIO_EXE
    ctx, _ops, _logs, _warns = _ctx(tmp_path, tools=tools)
    with pytest.raises(ToolCancelled):
        sc.broadband_runners(ctx, "L", _config("none", sharpen))["aberration"](src, tmp_path / "x.fits")


def test_every_correction_runner_returns_skip_cache_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          routed: list) -> None:
    def subsky(ops: RecordingOps, degree: int, samples: int, tolerance: float) -> None:
        ops.set_pixels([p * 0.5 for p in ops.pixels()])

    def script(ops: RecordingOps, path: Any, args: Any) -> None:
        ops.set_pixels([p - 0.01 for p in ops.pixels()])

    wcs = synth.make_master("L", w=64, h=48, seed=1).header

    def solve(ops: RecordingOps, force: bool, focal: Any, pixel_size: Any) -> None:
        ops._header.update({k: v for k, v in wcs.items() if k.startswith(("CTYPE", "CRVAL", "CRPIX", "CD"))})

    handlers = {"subsky": subsky, "pyscript": script, "platesolve": solve}
    ops = RecordingOps(tmp_path, handlers=handlers, stars=_stars())
    ctx, _ops, _logs, _warns = _ctx(tmp_path, tools={"rcastro": RC_EXE, "parallax_cli": PARALLAX_EXE,
                                                     "graxpert-ai": GRAXPERT_EXE}, ops=ops)
    unsolved = _master(tmp_path, "L", name="u.fits", with_wcs=False)
    solved = _master(tmp_path, "L", name="s.fits", with_wcs=True)
    results = [
        sc.broadband_runners(ctx, "L", _config("none", BXT))["solve"](unsolved, tmp_path / "a.fits"),
        sc.broadband_runners(ctx, "L", _config("none", BXT))["solve"](solved, tmp_path / "b.fits"),
        sc.broadband_runners(ctx, "L", _config("none"))["mgc"](solved, tmp_path / "c.fits"),
        sc.broadband_runners(ctx, "R", _config("GraXpert"))["graxpert"](solved, tmp_path / "d.fits"),
        sc.broadband_runners(ctx, "L", _config("none", SYQON))["aberration"](solved, tmp_path / "e.fits"),
        sc.broadband_runners(ctx, "L", _config("none", BXT))["aberration"](solved, tmp_path / "f.fits"),
    ]
    assert [r.skip_cache for r in results] == [False] * len(results)
    assert all(isinstance(r, StageOutput) for r in results)


def test_master_quality_does_not_cache_a_zero_fwhm_and_takes_a_context(tmp_path: Path) -> None:
    path = _master(tmp_path, "L")
    cache_dir = tmp_path / "cache"
    ctx, ops, _logs, _warns = _ctx(tmp_path, ops=RecordingOps(tmp_path, analyse=lambda p: {"fwhm": 0}))
    assert sc.master_quality(ctx, [path], cache_dir) == {}
    assert not (cache_dir / "master-quality.json").exists()
    ctx2, _ops2, _l2, _w2 = _ctx(tmp_path, ops=RecordingOps(tmp_path, analyse=_analysis))
    assert sc.master_quality(ctx2, [path], cache_dir)[os.fspath(path)]["stars"] == 250


def test_master_quality_ignores_a_cache_file_that_is_not_an_object(tmp_path: Path) -> None:
    path = _master(tmp_path, "L")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "master-quality.json").write_text("[1, 2]", encoding="utf-8")
    result = sc.master_quality(RecordingOps(tmp_path, analyse=_analysis), [path], cache_dir)
    assert result[os.fspath(path)]["fwhm"] == pytest.approx(3.2)


def test_public_solve_names_and_their_private_aliases() -> None:
    from loomsiril.loom import stages_correct as sc

    assert "has_astrometric_solution" in sc.__all__ and "solve_runner" in sc.__all__
    assert sc._has_astrometric_solution is sc.has_astrometric_solution
    assert sc._solve_runner is sc.solve_runner
    assert sc.has_astrometric_solution({"CTYPE1": "RA---TAN", "CRVAL1": 10.0}) is True
    assert sc.has_astrometric_solution({"FILTER": "R"}) is False
