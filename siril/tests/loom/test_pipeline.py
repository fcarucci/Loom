"""The run orchestrator (loomsiril.loom.pipeline.run) against the recording fake of SirilOps (no Siril, no network).

The masters are synthetic and carry the channel keywords the run reads (FILTER, INSTRUME). The stage transcripts show
which Siril commands ran; the cache folder is real and lives under pytest's tmp_path. Star extraction and the
aberration correction need tools that are not present here, so those stages are replaced by fake runners where a test
needs them, and every test says which.
"""

from __future__ import annotations

import glob
import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import config as loom_config
from loomsiril.loom import finishing, levels, pipeline, stages_colour, stages_correct
from loomsiril.loom.chain import CANCEL_MESSAGE, RunCancelled
from loomsiril.loom.runctx import RunContext, StageOutput
from loomsiril.loom.siril_ops import RecordingOps, SirilOpsError

SHAPE = (48, 64)
FILTERS = {"L": "Luminance", "R": "Baader Red", "G": "Baader Green", "B": "Baader Blue", "H": "Ha 7nm",
           "S": "SII 7nm", "O": "OIII 7nm"}
INSTRUME = "ZWO ASI294MC"
CHANNEL_FILTER = {"L": "Luminance", "R": "Baader Red", "G": "Baader Green", "B": "Baader Blue",
                  "H": "Ha 7nm", "S": "SII 7nm", "O": "OIII 7nm"}


def _plane(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).uniform(0.05, 0.4, SHAPE).astype(np.float32)


def _write_master(path: Path, channel: str, seed: int, *, instrume: Optional[str] = INSTRUME) -> str:
    header: dict[str, Any] = {"OBJECT": "M42", "FILTER": CHANNEL_FILTER[channel], "EXPTIME": 300.0}
    if instrume is not None:
        header["INSTRUME"] = instrume
    write_fits(path, [_plane(seed)], header, bitpix=-32)
    return str(path)


def _composite_handler(ops: RecordingOps, r: Any, g: Any, b: Any, out: Any) -> str:
    planes = [read_fits(os.fspath(p))[0][0] for p in (r, g, b)]
    header = read_fits(os.fspath(r))[1]
    path = os.fspath(out) + ".fit"
    write_fits(path, planes, header, bitpix=-32)
    return path


def _register_handler(ops: RecordingOps, name: str) -> None:
    for path in sorted(glob.glob(os.path.join(ops.workdir, "[0-9][0-9]_*.fits"))):
        index = int(os.path.basename(path)[:2])
        shutil.copyfile(path, os.path.join(ops.workdir, f"r_{name}_{index:05d}.fit"))


def _subsky_handler(ops: RecordingOps, degree: int, samples: int, tolerance: float) -> None:
    """Background extraction changes the pixels (a flat offset here), as the real command does."""
    ops.set_pixels([p - 0.01 for p in ops.pixels()])


def _handlers() -> dict[str, Callable[..., Any]]:
    return {"rgbcomp": _composite_handler, "register": _register_handler, "subsky": _subsky_handler}


class _AnyTool(dict):
    """Every tool id resolves to a placeholder path: the tool adapters are replaced where a test runs them."""

    def __missing__(self, key: str) -> str:
        return "/fake/" + key


class Harness:
    """Builds the run context for each run and keeps every fake that was created, so tests can read transcripts."""

    def __init__(self, tmp_path: Path, handlers: Optional[dict[str, Callable[..., Any]]] = None,
                 fail: Optional[dict[str, BaseException]] = None, analyse: Any = None,
                 cancel_after: Optional[int] = None, icc: Optional[bytes] = b"recorded-icc-profile") -> None:
        self.tmp = tmp_path
        self.handlers = _handlers() if handlers is None else handlers
        self.fail = fail
        self.analyse = analyse if analyse is not None else {"fwhm": 3.0, "roundness": 0.9, "bgnoise": 0.01,
                                                             "nbstars": 120}
        self.cancel_after = cancel_after
        self.opses: list[RecordingOps] = []
        self.contexts: list[RunContext] = []
        self.cancel_polls = 0
        self.force = False  # set by a test to cancel at the next checkpoint
        self.icc = icc
        self.siril_wd = str(tmp_path / "siril-wd")
        os.makedirs(self.siril_wd, exist_ok=True)

    def cancelled(self) -> bool:
        self.cancel_polls += 1
        return self.force or (self.cancel_after is not None and self.cancel_polls > self.cancel_after)

    compression = False   # Siril's FITS compression preference when the run starts

    def factory(self) -> Callable[[str], RunContext]:
        def make(work_dir: str) -> RunContext:
            ops = RecordingOps(self.siril_wd, handlers=self.handlers, analyse=self.analyse, fail=self.fail,
                               icc=self.icc)
            ops.compression = self.compression
            self.opses.append(ops)
            ctx = RunContext(ops=ops, work_dir=work_dir, platform="linux", tools=_AnyTool(), cancelled=self.cancelled)
            self.contexts.append(ctx)
            return ctx

        return make

    def run(self, config: dict[str, Any], stamp: str, **kw: Any) -> pipeline.RunReport:
        return pipeline.run(config, self.factory(), platform="linux", env={}, home=str(self.tmp), stamp=stamp,
                            **kw)

    @property
    def ops(self) -> RecordingOps:
        return self.opses[-1]


def _config(tmp_path: Path, paths: dict[str, str], **over: Any) -> dict[str, Any]:
    (tmp_path / "cache").mkdir(exist_ok=True)  # a chosen cache folder that is missing means "no cache"
    cfg = loom_config.defaults()
    cfg.update({
        "paths": {ch: paths.get(ch, "") for ch in ("L", "R", "G", "B", "H", "S", "O")},
        "filters": {ch: FILTERS[ch] for ch in ("R", "G", "B")},
        "outputDir": str(tmp_path / "out"),
        "cacheDir": str(tmp_path / "cache"),
        "useCache": True,
        "gradientTool": "none",
    })
    cfg.update(over)
    return cfg


def _rgb_paths(tmp_path: Path) -> dict[str, str]:
    src = tmp_path / "masters"
    src.mkdir(exist_ok=True)
    return {ch: _write_master(src / f"{ch}.fits", ch, seed=i + 1) for i, ch in enumerate(("R", "G", "B"))}


def _lum_rgb_paths(tmp_path: Path) -> dict[str, str]:
    src = tmp_path / "masters"
    src.mkdir(exist_ok=True)
    return {ch: _write_master(src / f"{ch}.fits", ch, seed=i + 1) for i, ch in enumerate(("L", "R", "G", "B"))}


def _nb_paths(tmp_path: Path) -> dict[str, str]:
    src = tmp_path / "masters"
    src.mkdir(exist_ok=True)
    return {ch: _write_master(src / f"{ch}.fits", ch, seed=10 + i) for i, ch in enumerate(("H", "S", "O"))}


def _tree_digest(folder: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            full = os.path.join(root, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, folder)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _commands(ops: RecordingOps) -> list[str]:
    return [text.split(" ", 1)[0] for text in ops.transcript]


# ---- RGB only ---------------------------------------------------------------------------------------------------------


def test_rgb_only_publishes_rgb_and_cleans_up(tmp_path: Path) -> None:
    paths = _rgb_paths(tmp_path)
    harness = Harness(tmp_path)
    before_wd = harness.siril_wd
    report = harness.run(_config(tmp_path, paths), "2026-01-01_00-00-00")
    assert report.produced == ["RGB"]
    assert os.path.isfile(report.outputs["RGB"])
    assert os.path.dirname(report.outputs["RGB"]) == str(tmp_path / "out")
    assert "rgbcomp" in _commands(harness.ops)
    assert harness.ops.workdir == before_wd  # the Siril working folder is restored
    assert not os.path.exists(tmp_path / "cache" / "work" / "2026-01-01_00-00-00")  # work folder removed
    assert os.path.isfile(report.log_path)
    text = Path(report.log_path).read_text(encoding="utf-8")
    assert "combine" in text and "[log]" in text
    assert report.exports == []


def test_fits_compression_is_off_for_the_run_and_restored(tmp_path: Path) -> None:
    """With Siril's compression on, save writes <name>.fits.fz and the next stage cannot find <name>.fits."""
    harness = Harness(tmp_path)
    harness.compression = True
    harness.run(_config(tmp_path, _rgb_paths(tmp_path)), "stamp-compress")
    commands = harness.ops.transcript
    off = commands.index("set compression.enabled=false")
    assert off < commands.index(next(c for c in commands if c.startswith("save ")))
    assert "set compression.enabled=true" in commands[off + 1:]
    assert harness.ops.compression is True


def test_fits_compression_is_restored_after_a_failed_run(tmp_path: Path) -> None:
    harness = Harness(tmp_path, fail={"rgbcomp": RuntimeError("boom")})
    harness.compression = True
    with pytest.raises(Exception):
        harness.run(_config(tmp_path, _rgb_paths(tmp_path)), "stamp-compress-fail")
    assert harness.ops.compression is True


def test_fits_compression_left_alone_when_off(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.run(_config(tmp_path, _rgb_paths(tmp_path)), "stamp-nocompress")
    assert not any(c.startswith("set compression") for c in harness.ops.transcript)


def test_report_shape(tmp_path: Path) -> None:
    report = Harness(tmp_path).run(_config(tmp_path, _rgb_paths(tmp_path)), "stamp-shape")
    d = report.to_dict()
    assert set(d) == {"produced", "outputs", "exports", "duration_s", "log_path", "warnings"}
    assert d["duration_s"] >= 0


def test_output_dir_is_required(tmp_path: Path) -> None:
    cfg = _config(tmp_path, _rgb_paths(tmp_path))
    cfg["outputDir"] = ""
    with pytest.raises(ValueError, match="outputDir"):
        Harness(tmp_path).run(cfg, "stamp-noout")


# ---- second run, cache hits -----------------------------------------------------------------------------------------


def test_second_run_is_a_full_cache_hit(tmp_path: Path) -> None:
    paths = _lum_rgb_paths(tmp_path)
    cfg = _config(tmp_path, paths)
    first = Harness(tmp_path)
    first.run(cfg, "run-1")
    cache_dir = tmp_path / "cache"
    before = _tree_digest(cache_dir)
    source_before = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in paths.values()}

    second = Harness(tmp_path)
    report = second.run(cfg, "run-2")
    commands = _commands(second.ops)
    assert "rgbcomp" not in commands and "register" not in commands and "spcc" not in commands
    assert "set32bits" not in commands  # nothing is ingested: the channels are served from the cache
    assert report.produced == ["L", "RGB"]

    after = _tree_digest(cache_dir)
    for name, digest in before.items():
        assert after.get(name) == digest, name  # every cache file is byte-identical
    assert {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in paths.values()} == source_before


def test_changed_parameter_reruns_only_its_stage(tmp_path: Path) -> None:
    paths = _rgb_paths(tmp_path)
    nb = _nb_paths(tmp_path)
    cfg = _config(tmp_path, {**paths, **nb}, palettes=["SHO"], narrowbandNormalize=False)
    Harness(tmp_path).run(cfg, "pal-1")
    changed = Harness(tmp_path)
    changed.run({**cfg, "narrowbandBandwidth": 4.0}, "pal-2")
    commands = _commands(changed.ops)
    assert any("-narrowband" in t for t in changed.ops.transcript)
    assert "rgbcomp" not in commands  # the RGB composite and its combine are still cache hits
    assert not any(t.startswith("register") for t in changed.ops.transcript)


# ---- narrowband ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("palette", ["SHO", "HOO"])
def test_narrowband_only_palette(tmp_path: Path, palette: str) -> None:
    nb = _nb_paths(tmp_path)
    harness = Harness(tmp_path)
    report = harness.run(_config(tmp_path, nb, palettes=[palette]), f"nb-{palette}")
    assert report.produced == [palette]  # H, S, O are not kept once a palette is built
    assert os.path.isfile(report.outputs[palette])
    assert any("-narrowband" in t for t in harness.ops.transcript)


def test_narrowband_without_palette_keeps_channels(tmp_path: Path) -> None:
    nb = _nb_paths(tmp_path)
    report = Harness(tmp_path).run(_config(tmp_path, nb), "nb-plain")
    assert set(report.produced) == {"H", "S", "O"}


# ---- luminance and star extraction -----------------------------------------------------------------------------------


def test_luminance_with_star_tool_is_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The star extraction needs a star tool; a fake finishing runner stands in for it: it writes a starless plate
    and a stars companion, so the naming (L_starless, L_stars) and the RGB split can be checked."""

    def fake_runners(ctx: RunContext, label: str, names: Any, config: Any, *, noun: Optional[str] = None) -> dict:
        def extract(in_path: str, out_path: str) -> StageOutput:
            stars = os.path.join(ctx.work_dir, f"{label}_stars.fits")
            shutil.copyfile(in_path, stars)
            shutil.copyfile(in_path, out_path)
            return StageOutput(path=out_path, companions={"stars": stars})

        runners: dict[str, Any] = {}
        for role, stage in names.items():
            if role == "extract":
                runners[stage] = extract
        return runners

    monkeypatch.setattr(finishing, "finishing_runners", fake_runners)
    paths = _lum_rgb_paths(tmp_path)
    cfg = _config(tmp_path, paths, starTool="StarNet2")
    report = Harness(tmp_path).run(cfg, "lum-split")
    assert "L_starless" in report.produced and "L_stars" in report.produced
    assert "RGB_starless" in report.produced and "RGB_stars" in report.produced
    assert "L" not in report.produced and "RGB" not in report.produced


# ---- clean white balance ----------------------------------------------------------------------------------------------


def _stub_sharpen_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """The aberration and the RGB sharpen stages need the RC-Astro tool: pass-throughs stand in for them here."""
    real_runners = stages_correct.broadband_runners

    def passthrough_broadband(ctx: RunContext, channel: str, config: Any) -> dict:
        runners = real_runners(ctx, channel, config)
        if "aberration" in runners:
            runners["aberration"] = lambda i, o: StageOutput(path=i)
        return runners

    real_finishing = finishing.finishing_runners

    def passthrough_finishing(ctx: RunContext, label: str, names: Any, config: Any, *,
                              noun: Optional[str] = None) -> dict:
        runners = real_finishing(ctx, label, names, config, noun=noun)
        if names.get("sharpen") in runners:
            runners[names["sharpen"]] = lambda i, o: StageOutput(path=i)
        return runners

    monkeypatch.setattr(stages_correct, "broadband_runners", passthrough_broadband)
    monkeypatch.setattr(finishing, "finishing_runners", passthrough_finishing)
    monkeypatch.setattr(levels, "tool_chosen", lambda tool: tool not in (None, "none"))


def test_clean_white_balance_is_measured_from_cached_pre_correction_plates(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """With a sharpen tool chosen, the white balance is measured on the pre-correction plates of R, G and B: the cache
    entries of their graxpert stage. The measurement is replaced by a recorder (it needs SPCC on real data)."""
    seen: dict[str, Any] = {}

    def fake_measure(ctx: RunContext, clean: dict, ref_plate: Any, rect: Any, config: Any, lum: Any, store: Any,
                     use_cache: bool, ignore_cache: bool) -> list[float]:
        seen["channels"] = sorted(clean)
        seen["paths_exist"] = all(os.path.isfile(p) for _key, p in clean.values())
        seen["rect"] = tuple(rect)
        return [1.0, 1.0, 1.0]

    monkeypatch.setattr(stages_colour, "measure_clean_white_balance", fake_measure)
    _stub_sharpen_tools(monkeypatch)
    paths = _lum_rgb_paths(tmp_path)
    report = Harness(tmp_path).run(_config(tmp_path, paths, sharpenTool="BlurXTerminator"), "clean-wb")
    assert seen["channels"] == ["B", "G", "R"]
    assert seen["paths_exist"] is True
    assert seen["rect"] == (4, 4, SHAPE[1] - 4, SHAPE[0] - 4)  # the valid area, four pixels inside the frame
    assert "RGB" in report.produced


def test_lrgb_with_narrowband_and_clean_white_balance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LRGB and H, S, O with a palette and a sharpen tool: the composite and the palette both build, the palette is
    the kept result, the luminance and the RGB composite are kept, and H, S, O are not."""
    seen: dict[str, Any] = {}

    def fake_measure(ctx: RunContext, clean: dict, ref_plate: Any, rect: Any, config: Any, lum: Any, store: Any,
                     use_cache: bool, ignore_cache: bool) -> list[float]:
        seen["channels"] = sorted(clean)
        return [1.1, 1.0, 0.9]

    monkeypatch.setattr(stages_colour, "measure_clean_white_balance", fake_measure)
    _stub_sharpen_tools(monkeypatch)
    paths = {**_lum_rgb_paths(tmp_path), **_nb_paths(tmp_path)}
    harness = Harness(tmp_path)
    report = harness.run(_config(tmp_path, paths, sharpenTool="BlurXTerminator", palettes=["SHO"]), "lrgb-nb")
    assert seen["channels"] == ["B", "G", "R"]
    assert sorted(report.produced) == ["L", "RGB", "SHO"]
    assert any("-narrowband" in t for t in harness.ops.transcript)


def test_clean_white_balance_falls_back_without_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the cache off there are no pre-correction plates: the run warns and calibrates the composite directly."""
    called: list[str] = []
    monkeypatch.setattr(stages_colour, "measure_clean_white_balance", lambda *a, **k: called.append("x"))
    _stub_sharpen_tools(monkeypatch)
    paths = _rgb_paths(tmp_path)
    report = Harness(tmp_path).run(_config(tmp_path, paths, useCache=False, sharpenTool="BlurXTerminator"),
                                   "no-cache-wb")
    assert called == []
    assert any("could not build the uncorrected reference" in w for w in report.warnings)
    assert "RGB" in report.produced


# ---- cancel and errors ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("after", list(range(0, 12)))
def test_cancel_at_each_checkpoint_keeps_cache_and_removes_work(tmp_path: Path, after: int) -> None:
    paths = _lum_rgb_paths(tmp_path)
    cfg = _config(tmp_path, paths)
    harness = Harness(tmp_path, cancel_after=after)
    with pytest.raises(RunCancelled) as info:
        harness.run(cfg, f"cancel-{after}")
    assert str(info.value) == CANCEL_MESSAGE
    assert not os.path.exists(tmp_path / "cache" / "work" / f"cancel-{after}")
    assert harness.ops.workdir == harness.siril_wd
    log = next(iter(glob.glob(str(tmp_path / "cache" / "logs" / f"loom-run-cancel-{after}.log"))))
    assert CANCEL_MESSAGE in Path(log).read_text(encoding="utf-8")


def test_error_removes_work_folder_unless_kept(tmp_path: Path) -> None:
    paths = _rgb_paths(tmp_path)
    failing = {"rgbcomp": SirilOpsError("rgbcomp", RuntimeError("boom"))}
    cfg = _config(tmp_path, paths)
    with pytest.raises(Exception):
        Harness(tmp_path, fail=failing).run(cfg, "err-remove")
    assert not os.path.exists(tmp_path / "cache" / "work" / "err-remove")

    with pytest.raises(Exception):
        Harness(tmp_path, fail=failing).run({**cfg, "keepWindowsOnError": True}, "err-keep")
    assert os.path.isdir(tmp_path / "cache" / "work" / "err-keep")


def test_cache_folder_missing_runs_without_cache(tmp_path: Path) -> None:
    paths = _rgb_paths(tmp_path)
    cfg = _config(tmp_path, paths, cacheDir=str(tmp_path / "not-mounted"))
    report = Harness(tmp_path).run(cfg, "no-folder")
    assert "RGB" in report.produced
    assert any("cache folder is not there" in w for w in report.warnings)
    assert not os.path.exists(tmp_path / "not-mounted")  # nothing is created on a missing drive
    assert os.path.isfile(report.log_path)  # the log goes to the default folder instead


# ---- sources, cwd and the main result ---------------------------------------------------------------------------------


def test_run_with_no_stages_leaves_the_source_untouched(tmp_path: Path) -> None:
    nb = _nb_paths(tmp_path)
    before = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in nb.values()}
    Harness(tmp_path).run(_config(tmp_path, nb), "untouched")
    after = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in nb.values()}
    assert before == after


def test_main_result_is_loaded_last_after_export(tmp_path: Path) -> None:
    paths = _rgb_paths(tmp_path)
    cfg = _config(tmp_path, paths, stretch=True, exportDir=str(tmp_path / "tif"))
    harness = Harness(tmp_path)
    report = harness.run(cfg, "export")
    assert report.exports and all(p.startswith(str(tmp_path / "tif")) for p in report.exports)
    loads = [t for t in harness.ops.transcript if t.startswith("load ")]
    assert loads[-1] == f'load "{report.outputs["RGB"]}"'
    assert harness.ops.workdir == harness.siril_wd


def test_done_line_names_the_psb_and_the_tiffs() -> None:
    from loomsiril.loom import pipeline
    line = pipeline._done_line({}, ["/o/HSO.tif", "/o/Loom.psb", "/o/L.tif"], 125.0)
    assert line == "Loom is done in 2 min 5 s. Saved the PSB: /o/Loom.psb and 2 TIFF file(s)"
    assert pipeline._done_line({}, [], 9.2) == "Loom is done in 9 s."
