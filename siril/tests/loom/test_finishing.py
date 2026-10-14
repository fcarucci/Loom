"""The finishing tail's runners (loomsiril.loom.finishing) with fake tools and the in-memory Siril fake.

The adapters (RcAstro, SyqonStudio, the SyQon legacy tools, StarNet2) are replaced by recording fakes, so no program
runs. The Siril side is RecordingOps: its ``mtf`` and ``denoise`` transcripts are checked, its pixels are the numpy
MTF. Synthetic plates only; everything is written under pytest's tmp_path.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import finishing, imageops, levels, stretch
from loomsiril.loom.runctx import RunContext, StageOutput
from loomsiril.loom.siril_ops import RecordingOps
from loomsiril.loom.stagekit import StageFailed
from loomsiril.loom.tools import starnet, syqon_legacy
from loomsiril.loom.tools.runner import ToolCancelled

BG = 0.1
H, W = 64, 80
STAR_CENTRES = [(12, 15), (30, 50), (45, 20), (20, 66), (55, 60)]
STAR_FWHM = 3.0


# ---- fixtures and helpers ---------------------------------------------------------------------------------------------


def _background() -> np.ndarray:
    """A sky that is not flat: a gradient from 0.08 to 0.12 (a flat sky has no signal to stretch)."""
    _, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    return 0.08 + 0.04 * xx / (W - 1)


def _stars_plane(amplitude: float = 0.6) -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    out = np.zeros((H, W))
    for cy, cx in STAR_CENTRES:
        out += amplitude * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 1.5 ** 2))
    return np.clip(out, 0.0, 1.0)


def _composite(channels: int) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """(composite planes, true stars planes): composite = screen(background, stars), so unscreen recovers stars."""
    stars = []
    planes = []
    for c in range(channels):
        s = _stars_plane(0.5 + 0.1 * c)
        planes.append(1.0 - (1.0 - _background()) * (1.0 - s))
        stars.append(s)
    return [p.astype(np.float32) for p in planes], stars


def _write(path: Path, planes: list[np.ndarray]) -> str:
    write_fits(path, planes, bitpix=-32)
    return os.fspath(path)


def _digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _starless_of(src: str, dst: str) -> None:
    planes, header = read_fits(src)
    write_fits(dst, [_background().astype(np.float32) for _ in planes], header=header, bitpix=-32)


class FakeTools:
    """Records every adapter call; each call copies its input (or writes a constant starless plate)."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.fail: dict[str, BaseException] = {}

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise self.fail[name]

    # RcAstro
    def sharpen(self, src: str, dst: str, stars: float, detail: float) -> None:
        self.calls.append(("bxt.sharpen", stars, detail))
        self._maybe_fail("sharpen")
        shutil.copyfile(src, dst)

    def denoise(self, src: str, dst: str, amount: float, detail: float) -> None:
        self.calls.append(("nxt.denoise", amount, detail))
        self._maybe_fail("denoise")
        shutil.copyfile(src, dst)

    def bxt_remove_stars(self, src: str, dst: str) -> None:
        self.calls.append(("sxt.remove_stars",))
        _starless_of(src, dst)

    # SyqonStudio
    def studio_run(self, model: str, domain: str, settings: dict, src: str, dst: str) -> None:
        self.calls.append(("studio.run", model, domain, dict(settings)))
        self._maybe_fail("studio")
        shutil.copyfile(src, dst)

    def studio_parallax(self, domain: str, settings: dict, src: str, dst: str) -> None:
        self.calls.append(("studio.parallax", domain, dict(settings)))
        shutil.copyfile(src, dst)

    def studio_axiom(self, domain: str, settings: dict, src: str, dst: str) -> None:
        self.calls.append(("studio.axiom", domain))
        _starless_of(src, dst)

    # SyQon legacy and StarNet2 module functions
    def legacy_parallax(self, exe, ctx, src, dst, operation, amount, linked) -> None:
        self.calls.append(("parallax", operation, amount, linked))
        self._maybe_fail("parallax:" + operation)
        if operation == "star_reduction":
            # a visible effect, so a later failure can be told apart from the original plate
            planes, header = read_fits(src)
            write_fits(dst, [np.clip(p + 0.05, 0.0, 1.0).astype(np.float32) for p in planes], header=header,
                       bitpix=-32)
            return
        shutil.copyfile(src, dst)

    def legacy_prism(self, exe, ctx, src, dst, strength, stretched) -> None:
        self.calls.append(("prism", strength, stretched))
        self._maybe_fail("prism")
        shutil.copyfile(src, dst)

    def legacy_starless(self, exe, ctx, src, dst) -> None:
        self.calls.append(("legacy.starless", os.path.basename(os.fspath(exe))))
        _starless_of(src, dst)

    def starnet(self, exe, ctx, src, dst, revision) -> str:
        self.calls.append(("starnet", revision, _digest(src)))
        _starless_of(src, dst)
        return dst


@pytest.fixture
def fake() -> FakeTools:
    return FakeTools()


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch, fake: FakeTools) -> FakeTools:
    class _Rc:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        sharpen = staticmethod(fake.sharpen)
        denoise = staticmethod(fake.denoise)
        remove_stars = staticmethod(fake.bxt_remove_stars)

    class _Studio:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        run = staticmethod(fake.studio_run)
        parallax = staticmethod(fake.studio_parallax)
        axiom = staticmethod(fake.studio_axiom)

    monkeypatch.setattr(finishing, "_rcastro", lambda ctx: _Rc())
    monkeypatch.setattr(finishing, "_studio", lambda ctx: _Studio())
    monkeypatch.setattr(syqon_legacy, "parallax", fake.legacy_parallax)
    monkeypatch.setattr(syqon_legacy, "prism", fake.legacy_prism)
    monkeypatch.setattr(syqon_legacy, "starless", fake.legacy_starless)
    monkeypatch.setattr(starnet, "remove_stars", fake.starnet)
    return fake


class _Store:
    def get_flag(self, name: str) -> Any:
        return None

    def set_flag(self, name: str, value: Any) -> None:
        return None


@pytest.fixture
def make_ctx(tmp_path: Path):
    def build(**kw: Any) -> RunContext:
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        warnings: list[str] = []
        logs: list[str] = []
        ops = kw.pop("ops", None) or RecordingOps(work)
        tools = {"rcastro": "/fake/rc-astro", "syqon-cli": "/fake/syqon-cli", "prism_cli": "/fake/prism",
                 "parallax_cli": "/fake/parallax", "SyQonStarless": "/fake/starless", "starnet2": "/fake/starnet2"}
        tools.update(kw.pop("tools_extra", {}))
        ctx = RunContext(ops=ops, work_dir=os.fspath(work), warn=warnings.append, log=logs.append,
                         tools=tools, tool_store=_Store(), starnet_revision=kw.pop("rev", "rev-test"))
        ctx.warnings = warnings  # type: ignore[attr-defined]
        ctx.logs = logs  # type: ignore[attr-defined]
        return ctx

    return build


def _cfg(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "stretch": False, "stretchMethod": "mtf", "keepLinear": False,
        "sharpenTool": levels.SHARPEN_TOOL_BXT, "starReduction": "low", "detailLevel": "medium",
        "starTool": levels.STAR_TOOL_STARNET,
        "noiseTool": levels.NR_TOOL_NXT, "noiseLevel": "medium", "noiseLevelL": "",
        "gradientTool": "none", "parallaxFamily": "classic",
    }
    base.update(over)
    return base


def _run(runner, src: Path, dst: Path) -> StageOutput:
    return runner(os.fspath(src), os.fspath(dst))


# ---- presence and order -----------------------------------------------------------------------------------------------

_SHARPEN = [None, levels.SHARPEN_TOOL_BXT, levels.SHARPEN_TOOL_SYQON, levels.SHARPEN_TOOL_STUDIO]
_STARS = [None, levels.STAR_TOOL_STARNET, levels.STAR_TOOL_SXT, levels.STAR_TOOL_SYQON, levels.STAR_TOOL_STUDIO]
_NOISE = [None, levels.NR_TOOL_NXT, levels.NR_TOOL_PRISM, levels.NR_TOOL_STUDIO, levels.NR_TOOL_STUDIO2,
          levels.NR_TOOL_SIRIL]


@pytest.mark.parametrize("names", [levels.RGB_FINISHING, levels.PALETTE_FINISHING, levels.L_FINISHING],
                         ids=["RGB", "PALETTE", "L"])
def test_runners_match_finishing_params_for_every_tool_and_level(make_ctx, names):
    count = 0
    for sharpen in _SHARPEN:
        for star in _STARS:
            for noise in _NOISE:
                for stretch_on in (False, True):
                    for method in (levels.STRETCH_METHOD_MTF, levels.STRETCH_METHOD_GHS):
                        for level in ("low", "medium", "high"):
                            cfg = _cfg(sharpenTool=sharpen or "none", starTool=star or "none",
                                       noiseTool=noise or "none", noiseLevel=level, noiseLevelL="",
                                       stretch=stretch_on, stretchMethod=method, keepLinear=True,
                                       starReduction="medium", detailLevel="low")
                            want = list(levels.finishing_params(names, cfg).keys())
                            got = list(finishing.finishing_runners(make_ctx(), "plate", names, cfg).keys())
                            assert got == want
                            count += 1
    assert count == 4 * 5 * 6 * 2 * 2 * 3


def test_stage_order_for_nxt_and_prism(make_ctx):
    nxt = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING, _cfg(stretch=True))
    assert list(nxt) == ["sharpenRGB", "extractRGB", "denoiseLinearRGB", "stretchRGB"]
    prism = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                        _cfg(stretch=True, noiseTool=levels.NR_TOOL_PRISM))
    assert list(prism) == ["sharpenRGB", "extractRGB", "stretchRGB", "denoiseRGB"]
    bare = finishing.finishing_runners(make_ctx(), "L", levels.L_FINISHING,
                                       _cfg(sharpenTool="none", starTool="none", noiseTool="none", stretch=False))
    assert list(bare) == []


# ---- sharpen ----------------------------------------------------------------------------------------------------------


def test_bxt_stars_and_detail_are_one_pass(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = _write(tmp_path / "in.fits", planes)
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=False, starTool="none", noiseTool="none"))
    out = _run(runners["sharpenRGB"], Path(src), tmp_path / "out.fits")
    assert out.skip_cache is False
    assert tools.calls == [("bxt.sharpen", 0.25, 0.5)]  # low stars, medium detail: one call with both


def test_bxt_stars_only_passes_zero_detail(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = _write(tmp_path / "in.fits", planes)
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(detailLevel="none", starTool="none", noiseTool="none"))
    _run(runners["sharpenRGB"], Path(src), tmp_path / "out.fits")
    assert tools.calls == [("bxt.sharpen", 0.25, 0.0)]


def test_syqon_parallax_is_star_reduction_then_sharpen(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = _write(tmp_path / "in.fits", planes)
    runners = finishing.finishing_runners(
        make_ctx(), "RGB", levels.RGB_FINISHING,
        _cfg(sharpenTool=levels.SHARPEN_TOOL_SYQON, starTool="none", noiseTool="none"))
    _run(runners["sharpenRGB"], Path(src), tmp_path / "out.fits")
    assert tools.calls == [("parallax", "star_reduction", 2, True), ("parallax", "sharpen", 0.8, True)]


def test_studio_parallax_uses_linear_domain_and_family(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = _write(tmp_path / "in.fits", planes)
    runners = finishing.finishing_runners(
        make_ctx(), "RGB", levels.RGB_FINISHING,
        _cfg(sharpenTool=levels.SHARPEN_TOOL_STUDIO, parallaxFamily="aesthetics", starTool="none", noiseTool="none"))
    _run(runners["sharpenRGB"], Path(src), tmp_path / "out.fits")
    assert tools.calls == [
        ("studio.parallax", "linear", {"parallax": {"reduction": 3, "family": "aesthetics"}}),
        ("studio.parallax", "linear", {"parallax": {"deblur": 0.5, "family": "aesthetics"}}),
    ]


def test_sharpen_failure_keeps_the_plate(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    tools.fail["sharpen"] = RuntimeError("boom")
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(starTool="none", noiseTool="none"), noun="composite")
    out = _run(runners["sharpenRGB"], src, tmp_path / "out.fits")
    assert out.skip_cache is True
    assert out.path == os.fspath(tmp_path / "out.fits")
    assert Path(out.path).read_bytes() == src.read_bytes()
    assert ctx.warnings == ["RGB could not be corrected (boom); the composite is kept as it is"]


def test_cancel_propagates_from_a_sharpen_tool(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    tools.fail["sharpen"] = ToolCancelled("rc-astro")
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(starTool="none", noiseTool="none"))
    with pytest.raises(ToolCancelled):
        _run(runners["sharpenRGB"], src, tmp_path / "out.fits")


# ---- extraction -------------------------------------------------------------------------------------------------------


def test_extract_recovers_the_stars_when_stretch_is_off(make_ctx, tools, tmp_path):
    planes, stars = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=False, sharpenTool="none", noiseTool="none",
                                               starTool=levels.STAR_TOOL_STARNET))
    out = _run(runners["extractRGB"], src, tmp_path / "starless.fits")
    assert out.skip_cache is False
    assert out.companions.keys() == {"stars"}
    got_stars, _ = read_fits(out.companions["stars"])
    for g, t in zip(got_stars, stars):
        assert np.allclose(g, t, atol=1e-5)
    starless, _ = read_fits(out.path)
    assert all(np.allclose(p, _background(), atol=1e-6) for p in starless)
    # StarNet2 ran twice: the stars copy (with the stored licence revision), then the composite
    assert [c[0] for c in tools.calls] == ["starnet", "starnet"]
    assert all(c[1] == "rev-test" for c in tools.calls)


@pytest.mark.parametrize("method", [levels.STRETCH_METHOD_MTF, levels.STRETCH_METHOD_GHS])
def test_stars_plate_is_mtf_stretched_to_half_even_with_ghs(make_ctx, tools, tmp_path, method):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=True, stretchMethod=method, sharpenTool="none",
                                               noiseTool="none"))
    out = _run(runners["extractRGB"], src, tmp_path / "starless.fits")
    # the stars copy went through Siril's mtf with the target 0.5 parameters, never through autoghs
    want = stretch.stretch_parameters_for_planes(planes, True, stretch.STRETCH_STARS_TARGET)
    commands = [c for c in ctx.ops.transcript if c.startswith("mtf")]
    assert len(commands) == 1
    _, c0, m, _ = commands[0].split()
    assert float(c0) == pytest.approx(want["c0"], abs=1e-12)
    assert float(m) == pytest.approx(want["m"], abs=1e-12)
    assert not any("ghs" in c for c in ctx.ops.transcript)
    stretched = stretch.apply_mtf(planes, want, True)
    got_stars, _ = read_fits(out.companions["stars"])
    # the fake star tool writes the sky whatever it is given, so the starless it returns is that sky
    expected = [np.clip(imageops.unscreen_stars(s, _background().astype(np.float32)), 0, 1) for s in stretched]
    for g, e in zip(got_stars, expected):
        assert np.allclose(g, e, atol=2e-6)


def test_stars_plate_ignores_the_ghs_method_for_its_numbers(make_ctx, tools, tmp_path):
    planes, _ = _composite(1)
    src = Path(_write(tmp_path / "in.fits", planes))
    results = {}
    for method in (levels.STRETCH_METHOD_MTF, levels.STRETCH_METHOD_GHS):
        ctx = make_ctx()
        runners = finishing.finishing_runners(ctx, "L", levels.L_FINISHING,
                                              _cfg(stretch=True, stretchMethod=method, sharpenTool="none",
                                                   noiseTool="none", starTool=levels.STAR_TOOL_STARNET))
        results[method] = _run(runners["extractL"], src, tmp_path / f"s_{method}.fits")
    a, _ = read_fits(results["mtf"].companions["stars"])
    b, _ = read_fits(results["ghs"].companions["stars"])
    assert all(np.array_equal(x, y) for x, y in zip(a, b))


def test_extract_failure_raises(make_ctx, tools, tmp_path, monkeypatch):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=False, sharpenTool="none", noiseTool="none"))

    def broken(exe, c, s, d, rev):
        raise RuntimeError("starnet exploded")

    monkeypatch.setattr(starnet, "remove_stars", broken)
    with pytest.raises(RuntimeError, match="starnet exploded"):
        _run(runners["extractRGB"], src, tmp_path / "out.fits")


@pytest.mark.parametrize(
    ("tool", "expected"),
    [(levels.STAR_TOOL_SXT, ("sxt.remove_stars",)),
     (levels.STAR_TOOL_SYQON, ("legacy.starless", "starless")),
     (levels.STAR_TOOL_STUDIO, ("studio.axiom", "linear"))],
)
def test_each_star_tool_is_called_for_the_starless_plate(make_ctx, tools, tmp_path, tool, expected):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=False, sharpenTool="none", noiseTool="none", starTool=tool))
    out = _run(runners["extractRGB"], src, tmp_path / "out.fits")
    assert out.companions.keys() == {"stars"}
    assert tools.calls[-1][0] == expected[0]


def test_studio_axiom_is_nonlinear_on_the_stretched_stars_plate(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=True, sharpenTool="none", noiseTool="none",
                                               starTool=levels.STAR_TOOL_STUDIO))
    _run(runners["extractRGB"], src, tmp_path / "out.fits")
    assert tools.calls == [("studio.axiom", "nonlinear"), ("studio.axiom", "linear")]


# ---- linear and stretched denoise -------------------------------------------------------------------------------------


def test_siril_denoise_low_is_mod_0_6_in_the_linear_slot(make_ctx, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    ctx = make_ctx()
    cfg = _cfg(sharpenTool="none", starTool="none", noiseTool=levels.NR_TOOL_SIRIL, noiseLevel="low", stretch=False)
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING, cfg)
    assert list(runners) == ["denoiseLinearRGB"]
    out = _run(runners["denoiseLinearRGB"], src, tmp_path / "out.fits")
    assert out.skip_cache is False
    assert "denoise -mod=0.6" in ctx.ops.transcript


def test_prism_denoise_runs_on_the_stretched_plate(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(sharpenTool="none", starTool="none", stretch=True,
                                               noiseTool=levels.NR_TOOL_PRISM, noiseLevel="medium"))
    _run(runners["denoiseRGB"], src, tmp_path / "out.fits")
    assert tools.calls == [("prism", 0.85, True)]


def test_studio_prism_essential_runs_in_the_linear_slot(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(sharpenTool="none", starTool="none", stretch=True,
                                               noiseTool=levels.NR_TOOL_STUDIO, noiseLevel="high"))
    # Essential is a linear-data tool: it runs before the stretch, whatever the stretch setting
    assert list(runners) == ["denoiseLinearRGB", "stretchRGB"]
    _run(runners["denoiseLinearRGB"], src, tmp_path / "out.fits")
    assert tools.calls == [("studio.run", "prism-essential", "linear", {"application": 1.0})]


def test_denoise_failure_keeps_the_plate(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    tools.fail["studio"] = RuntimeError("no licence")
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(sharpenTool="none", starTool="none", stretch=True,
                                               noiseTool=levels.NR_TOOL_STUDIO, noiseLevel="low"), noun="composite")
    out = _run(runners["denoiseLinearRGB"], src, tmp_path / "out.fits")
    assert out.skip_cache is True
    assert Path(out.path).read_bytes() == src.read_bytes()
    assert ctx.warnings == ["RGB could not be denoised (no licence); the composite is kept as it is"]


# ---- stretch ----------------------------------------------------------------------------------------------------------


def test_mtf_stretch_through_siril_matches_the_numpy_reference(make_ctx, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=True, sharpenTool="none", starTool="none", noiseTool="none"))
    out = _run(runners["stretchRGB"], src, tmp_path / "out.fits")
    want = stretch.stretch_parameters_for_planes(planes, True, stretch.STRETCH_SKY_TARGET)
    ref = stretch.apply_mtf(planes, want, True)
    got, _ = read_fits(out.path)
    for g, r in zip(got, ref):
        assert np.allclose(g, r, atol=2e-6)  # siril marker: the in-memory fake is the numpy MTF by construction


def test_keep_linear_companion_only_for_rgb_and_palette_stretch(make_ctx, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    cfg = _cfg(stretch=True, keepLinear=True, sharpenTool="none", starTool="none", noiseTool="none")
    rgb = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING, cfg)
    out = _run(rgb["stretchRGB"], src, tmp_path / "rgb.fits")
    assert set(out.companions) == {"linear"}
    assert Path(out.companions["linear"]).read_bytes() == src.read_bytes()
    pal = finishing.finishing_runners(make_ctx(), "palette", levels.PALETTE_FINISHING, cfg)
    assert set(_run(pal["paletteStretch"], src, tmp_path / "pal.fits").companions) == {"linear"}
    mono = _write(tmp_path / "mono.fits", planes[:1])
    lum = finishing.finishing_runners(make_ctx(), "L", levels.L_FINISHING, cfg)
    assert _run(lum["stretchL"], Path(mono), tmp_path / "L.fits").companions == {}
    off = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING, {**cfg, "keepLinear": False})
    assert _run(off["stretchRGB"], src, tmp_path / "off.fits").companions == {}


def test_stretch_failure_keeps_the_plate_linear(make_ctx, tmp_path, monkeypatch):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=True, sharpenTool="none", starTool="none", noiseTool="none"),
                                          noun="composite")

    def no_signal(*_a: Any, **_k: Any) -> dict:
        raise ValueError(stretch.NO_SIGNAL_MESSAGE)

    monkeypatch.setattr(stretch, "stretch_parameters_for_planes", no_signal)
    out = _run(runners["stretchRGB"], src, tmp_path / "out.fits")
    assert out.skip_cache is True
    assert Path(out.path).read_bytes() == src.read_bytes()
    assert ctx.warnings == [f"RGB could not be stretched ({stretch.NO_SIGNAL_MESSAGE}); the composite is kept linear"]


def test_ghs_stretch_failure_is_kept_linear(make_ctx, tmp_path, monkeypatch):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=True, stretchMethod="ghs", sharpenTool="none",
                                               starTool="none", noiseTool="none"))

    def failing(*_a: Any, **_k: Any) -> None:
        raise StageFailed("GHS stretch failed")

    monkeypatch.setattr(finishing.ghs, "run_ghs_stretch", failing)
    out = _run(runners["stretchRGB"], src, tmp_path / "out.fits")
    assert out.skip_cache is True
    assert Path(out.path).read_bytes() == src.read_bytes()


def test_no_module_level_side_effects_in_finishing():
    source = Path(finishing.__file__).read_text(encoding="utf-8")
    assert "sirilpy" not in source and "tkinter" not in source


# ---- L stars frequency separation -------------------------------------------------------------------------------------


def test_separate_l_stars_outputs_low_and_high(make_ctx, tmp_path):
    x = _stars_plane(0.8).astype(np.float32)
    path = _write(tmp_path / "L_stars.fits", [x])
    fwhm = STAR_FWHM
    ops = RecordingOps(tmp_path / "work", stars=[{"xpos": 10.0, "ypos": 10.0, "fwhmx": fwhm, "fwhmy": fwhm,
                                                  "A": 0.5}] * 5)
    ctx = make_ctx(ops=ops)
    low_path, high_path = finishing.separate_l_stars(ctx, path, os.fspath(tmp_path / "work"))
    sigma = (fwhm / imageops.FWHM_PER_SIGMA) * imageops.FS_SIGMA_FACTOR
    low, _ = read_fits(low_path)
    high, _ = read_fits(high_path)
    want_low = imageops.gaussian_blur([x.astype(np.float64)], sigma)[0]
    assert np.allclose(low[0], want_low, atol=1e-5)
    assert np.allclose(high[0], np.clip((x - want_low) / 2 + 0.5, 0, 1), atol=1e-5)
    # the high layer recombines to the input wherever it was not clipped
    unclipped = (high[0] > 0) & (high[0] < 1)
    assert np.allclose(low[0][unclipped] + (high[0][unclipped] - 0.5) * 2, x[unclipped], atol=1e-5)


def test_separate_l_stars_without_stars_raises(make_ctx, tmp_path):
    path = _write(tmp_path / "L_stars.fits", [_stars_plane(0.8).astype(np.float32)])
    ops = RecordingOps(tmp_path / "work", stars=[])
    with pytest.raises(StageFailed):
        finishing.separate_l_stars(make_ctx(ops=ops), path, os.fspath(tmp_path / "work"))


# ---- review additions: cancels, input identity, GHS, L unlinked, detail failure, fineScale -----------------------------


def _raise_cancel(*_a: Any) -> None:
    raise ToolCancelled("fake")


def test_cancel_propagates_from_linear_denoise(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    tools.fail["denoise"] = ToolCancelled("rc-astro")
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(sharpenTool="none", starTool="none", noiseTool=levels.NR_TOOL_NXT))
    with pytest.raises(ToolCancelled):
        _run(runners["denoiseLinearRGB"], src, tmp_path / "out.fits")


def test_cancel_propagates_from_stretched_denoise(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    tools.fail["prism"] = ToolCancelled("prism_cli")
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(sharpenTool="none", starTool="none", stretch=True,
                                               noiseTool=levels.NR_TOOL_PRISM))
    with pytest.raises(ToolCancelled):
        _run(runners["denoiseRGB"], src, tmp_path / "out.fits")


def test_cancel_propagates_from_mtf_and_ghs_stretch(make_ctx, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    for method, command in ((levels.STRETCH_METHOD_MTF, "mtf"), (levels.STRETCH_METHOD_GHS, "ghs")):
        ops = RecordingOps(tmp_path / "work", handlers={command: _raise_cancel})
        runners = finishing.finishing_runners(make_ctx(ops=ops), "RGB", levels.RGB_FINISHING,
                                              _cfg(stretch=True, stretchMethod=method, sharpenTool="none",
                                                   starTool="none", noiseTool="none"))
        with pytest.raises(ToolCancelled):
            _run(runners["stretchRGB"], src, tmp_path / f"out_{method}.fits")


def test_starless_pass_gets_the_untouched_linear_composite(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    composite_digest = _digest(os.fspath(src))
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=True, sharpenTool="none", noiseTool="none",
                                               starTool=levels.STAR_TOOL_STARNET))
    _run(runners["extractRGB"], src, tmp_path / "starless.fits")
    stars_pass, starless_pass = tools.calls
    assert stars_pass[2] != composite_digest  # the stretched stars copy
    assert starless_pass[2] == composite_digest  # the linear composite, untouched


def test_failed_stretch_keeps_the_linear_companion(make_ctx, tmp_path, monkeypatch):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    runners = finishing.finishing_runners(make_ctx(), "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=True, keepLinear=True, sharpenTool="none", starTool="none",
                                               noiseTool="none"), noun="composite")

    def no_signal(*_a: Any, **_k: Any) -> dict:
        raise ValueError("no signal")

    monkeypatch.setattr(stretch, "stretch_parameters_for_planes", no_signal)
    out = _run(runners["stretchRGB"], src, tmp_path / "out.fits")
    assert out.skip_cache is True
    assert set(out.companions) == {"linear"}
    assert Path(out.companions["linear"]).read_bytes() == src.read_bytes()


def _ghs_ctx(make_ctx, tmp_path, name: str):
    """A recording Siril whose ghs command really changes the pixels (a square root), so the output shows it ran."""
    def apply_ghs(ops, _command):
        ops.set_pixels([np.sqrt(p) for p in ops.pixels()])

    ops = RecordingOps(tmp_path / name, handlers={"ghs": apply_ghs})
    return make_ctx(ops=ops), ops


@pytest.mark.parametrize("keep", [True, False])
def test_ghs_stretch_success_path_with_and_without_keep_linear(make_ctx, tmp_path, keep):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    ctx, ops = _ghs_ctx(make_ctx, tmp_path, "work_" + str(keep))
    runners = finishing.finishing_runners(ctx, "RGB", levels.RGB_FINISHING,
                                          _cfg(stretch=True, stretchMethod="ghs", keepLinear=keep,
                                               sharpenTool="none", starTool="none", noiseTool="none"),
                                          noun="composite")
    out = _run(runners["stretchRGB"], src, tmp_path / "out.fits")
    assert out.skip_cache is False
    assert "autoghs -linked 0 4.5 -b=13 -hp=0.7 -lp=0" in ops.transcript
    got, _ = read_fits(out.path)
    want, _ = read_fits(src)
    assert all(not np.allclose(g, w) for g, w in zip(got, want))
    if keep:
        assert set(out.companions) == {"linear"}
        assert Path(out.companions["linear"]).read_bytes() == src.read_bytes()
    else:
        assert out.companions == {}


def test_l_plate_stretch_is_unlinked_per_channel(make_ctx, tmp_path):
    planes, _ = _composite(3)  # a three-plane plate on the L names: the stretch must not link it
    src = Path(_write(tmp_path / "in.fits", planes))
    ctx = make_ctx()
    runners = finishing.finishing_runners(ctx, "L", levels.L_FINISHING,
                                          _cfg(stretch=True, sharpenTool="none", starTool="none", noiseTool="none"))
    _run(runners["stretchL"], src, tmp_path / "out.fits")
    mtf = [c for c in ctx.ops.transcript if c.startswith("mtf")]
    assert len(mtf) == 3
    assert [c.split()[-1] for c in mtf] == ["R", "G", "B"]


def test_detail_failure_keeps_the_star_reduced_plate(make_ctx, tools, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    tools.fail["parallax:sharpen"] = RuntimeError("boom")
    ctx = make_ctx()
    runners = finishing.finishing_runners(
        ctx, "RGB", levels.RGB_FINISHING,
        _cfg(sharpenTool=levels.SHARPEN_TOOL_SYQON, starTool="none", noiseTool="none"), noun="composite")
    out = _run(runners["sharpenRGB"], src, tmp_path / "out.fits")
    assert out.skip_cache is True
    got, _ = read_fits(out.path)
    for g, p in zip(got, planes):
        assert np.allclose(g, np.clip(p + 0.05, 0.0, 1.0), atol=1e-6)  # the star-reduced plate, not the input
    assert ctx.warnings == ["RGB could not be corrected (boom); the composite is kept as it is"]


def test_prism2_fine_scale_is_refused_so_the_plate_is_kept(make_ctx, tmp_path):
    planes, _ = _composite(3)
    src = Path(_write(tmp_path / "in.fits", planes))
    entry = {"tool": levels.NR_TOOL_STUDIO2,
             "amount": {"model": "prism-advanced", "application": 1.0, "fineScale": 4}}
    with pytest.raises(ValueError, match="fineScale"):
        finishing._denoise_dispatch(make_ctx(), entry, os.fspath(src), os.fspath(tmp_path / "x.fits"), False)
