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
                     use_cache: bool, ignore_cache: bool, ref_key: Any = None, ref_channel: Any = None) -> list[float]:
        seen["channels"] = sorted(clean)
        seen["paths_exist"] = all(os.path.isfile(p) for _key, p in clean.values())
        seen["rect"] = tuple(rect)
        seen["ref_key"] = ref_key
        seen["ref_channel"] = ref_channel
        return [1.0, 1.0, 1.0]

    monkeypatch.setattr(stages_colour, "measure_clean_white_balance", fake_measure)
    _stub_sharpen_tools(monkeypatch)
    paths = _lum_rgb_paths(tmp_path)
    report = Harness(tmp_path).run(_config(tmp_path, paths, sharpenTool="BlurXTerminator"), "clean-wb")
    assert seen["channels"] == ["B", "G", "R"]
    assert seen["paths_exist"] is True
    assert seen["rect"] == (4, 4, SHAPE[1] - 4, SHAPE[0] - 4)  # the valid area, four pixels inside the frame
    assert isinstance(seen["ref_key"], str) and len(seen["ref_key"]) == 40  # the reference's current cache key
    assert seen["ref_channel"] == "L"  # the run's reference letter (the luminance of this LRGB run), not left out
    assert "RGB" in report.produced


def test_lrgb_with_narrowband_and_clean_white_balance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LRGB and H, S, O with a palette and a sharpen tool: the composite and the palette both build, the palette is
    the kept result, the luminance and the RGB composite are kept, and H, S, O are not."""
    seen: dict[str, Any] = {}

    def fake_measure(ctx: RunContext, clean: dict, ref_plate: Any, rect: Any, config: Any, lum: Any, store: Any,
                     use_cache: bool, ignore_cache: bool, ref_key: Any = None, ref_channel: Any = None) -> list[float]:
        seen["channels"] = sorted(clean)
        seen["ref_channel"] = ref_channel
        return [1.1, 1.0, 0.9]

    monkeypatch.setattr(stages_colour, "measure_clean_white_balance", fake_measure)
    _stub_sharpen_tools(monkeypatch)
    paths = {**_lum_rgb_paths(tmp_path), **_nb_paths(tmp_path)}
    harness = Harness(tmp_path)
    report = harness.run(_config(tmp_path, paths, sharpenTool="BlurXTerminator", palettes=["SHO"]), "lrgb-nb")
    assert seen["channels"] == ["B", "G", "R"]
    assert seen["ref_channel"] == "L"
    assert sorted(report.produced) == ["L", "RGB", "SHO"]
    assert any("-narrowband" in t for t in harness.ops.transcript)


def test_clean_white_balance_of_an_osc_run_gets_the_green_reference_letter(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """In a run whose broadband input is an OSC master the registration reference is its green plane, and the clean
    white-balance measurement is told so (``ref_channel``)."""
    seen: dict[str, Any] = {}

    def fake_measure(ctx: RunContext, clean: dict, ref_plate: Any, rect: Any, config: Any, lum: Any, store: Any,
                     use_cache: bool, ignore_cache: bool, ref_key: Any = None, ref_channel: Any = None) -> list[float]:
        seen["channels"] = sorted(clean)
        seen["ref_key"] = ref_key
        seen["ref_channel"] = ref_channel
        return [1.0, 1.0, 1.0]

    monkeypatch.setattr(stages_colour, "measure_clean_white_balance", fake_measure)
    _stub_sharpen_tools(monkeypatch)
    (tmp_path / "masters").mkdir(exist_ok=True)
    master = str(tmp_path / "masters" / "osc.fits")
    write_fits(master, [_plane(seed) for seed in (1, 2, 3)],
               {"FILTER": "L-Pro", "INSTRUME": INSTRUME, "OBJECT": "M42"}, bitpix=-32)
    cfg = _config(tmp_path, {}, sharpenTool="BlurXTerminator", oscKinds={"OSC": "broadband"},
                  oscSensor={"sensor": "Sony IMX571", "frontFilter": "", "lpf": ""})
    cfg["paths"]["OSC"] = master
    report = Harness(tmp_path).run(cfg, "osc-clean-wb")
    assert seen["channels"] == ["B", "G", "R"]
    assert seen["ref_channel"] == "G"
    assert isinstance(seen["ref_key"], str) and len(seen["ref_key"]) == 40
    assert "RGB" in report.produced


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


# ---- colour masters: one loader per slot, channel plates split from it -------------------------------------------------

# The planes of this master have statistics that print exactly (min/max/median 0.1/0.9/0.2 and 0/1/0.5 twice), so the
# group's log line can be compared with the literal Loom's own self-test checks for the same numbers.
_COLOUR_LOG = ("OSC n.fits: dualband, filter L-eXtreme, planes H/O, min/max/median 0.10000/0.90000/0.20000, "
               "0.00000/1.00000/0.50000, 0.00000/1.00000/0.50000")


def _colour_master(tmp_path: Path, name: str = "n.fits") -> tuple[str, list[np.ndarray]]:
    flat = np.full(SHAPE, 0.2, np.float32)
    flat[0, 0], flat[0, 1] = 0.1, 0.9
    halves = np.zeros(SHAPE, np.float32)
    halves.reshape(-1)[halves.size // 2:] = 1.0
    planes = [flat, halves, halves.copy()[::-1].copy()]
    path = tmp_path / "masters"
    path.mkdir(parents=True, exist_ok=True)
    if (path / name).exists():
        return str(path / name), planes  # a second run on the same file: same size and time, so the same cache keys
    write_fits(path / name, planes, {"FILTER": "L-eXtreme", "INSTRUME": INSTRUME, "OBJECT": "NGC 7000"}, bitpix=-32)
    return str(path / name), planes


class _ColourRun:
    """A run state built by hand around one colour master, with the master's reads counted and the log kept."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slot: str = "OSCNB", kind: str = "dualband",
                 **over: Any) -> None:
        from loomsiril.loom import colour_load
        self.lines: list[str] = []
        self.master, self.planes = _colour_master(tmp_path)
        (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
        (tmp_path / "work").mkdir(exist_ok=True)
        cfg = loom_config.defaults()
        cfg.update({"paths": {**cfg["paths"], slot: self.master}, "oscKinds": {slot: kind}, "useCache": True,
                    "cacheDir": str(tmp_path / "cache"), "outputDir": str(tmp_path / "out"),
                    "gradientTool": "none"})
        cfg.update(over)
        self.cfg = cfg
        ops = RecordingOps(str(tmp_path / "wd"))
        self.ctx = RunContext(ops=ops, work_dir=str(tmp_path / "work"), log=self.lines.append,
                              warn=self.lines.append, tools=_AnyTool())
        store = pipeline.cache.CacheStore(str(tmp_path / "cache")) if cfg["useCache"] else None
        self.run = pipeline._Run(self.ctx, cfg, store, str(tmp_path / "cache"), {})
        self.reads: list[str] = []
        real = colour_load.read_fits

        def counting(path: Any) -> Any:
            self.reads.append(os.fspath(path))
            return real(path)

        monkeypatch.setattr(colour_load, "read_fits", counting)

    def master_reads(self) -> int:
        return sum(1 for p in self.reads if p == self.master)


def _channels_of(plate: str) -> int:
    return len(read_fits(plate)[0])


def test_colour_channels_keep_the_masters_path_and_take_the_sibling_key(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from loomsiril.loom import osc
    cr = _ColourRun(tmp_path, monkeypatch, "OSC", "broadband")
    cr.run.load_channels()
    gid = osc.osc_group_identity(stages_correct.source_key(cr.master), "broadband")
    assert sorted(cr.run.chans) == ["B", "G", "R"]
    for plane, key in enumerate(("R", "G", "B")):
        ch = cr.run.chans[key]
        assert ch.path == cr.master and (ch.group, ch.plane) == ("OSC", plane)
        assert ch.source_key == ch.current_key == osc.osc_sibling_key(gid, plane)
        assert ch.instrume == INSTRUME
    assert cr.master_reads() == 0  # nothing is read until a plane is wanted
    assert f"load R <- {cr.master} (plane 0)" in cr.lines


def test_a_colour_slot_without_a_resolved_kind_is_an_internal_error(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    cr = _ColourRun(tmp_path, monkeypatch, "OSC", "broadband")
    cr.cfg["oscKinds"] = {}
    with pytest.raises(ValueError, match="^internal: the kind of OSC was not resolved by preflight$"):
        cr.run.load_channels()


def test_mono_channels_keep_their_own_record(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    cfg = _config(tmp_path, _rgb_paths(tmp_path))
    (tmp_path / "work").mkdir(exist_ok=True)
    state = pipeline._Run(harness.factory()(str(tmp_path / "work")), cfg, None, str(tmp_path / "cache"), {})
    state.load_channels()
    assert all(ch.group is None and ch.plane is None and ch.source is None for ch in state.chans.values())


def _spy_chain(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Replaces the chain runner by one that asks for the source plate and returns it: label -> plate."""
    got: dict[str, str] = {}

    def fake(ctx: Any, label: str, source: Any, chain: Any, runners: Any, store: Any, config: Any) -> Any:
        from loomsiril.loom.chain import ChainResult
        got[label] = source()
        return ChainResult(path=got[label], key=None)

    monkeypatch.setattr(pipeline, "process_chain", fake)
    return got


def test_broadband_correction_gets_a_one_channel_plate_for_r_and_g(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    cr = _ColourRun(tmp_path, monkeypatch, "OSC", "broadband")
    got = _spy_chain(monkeypatch)
    cr.run.load_channels()
    cr.run.correct_broadband()
    assert sorted(got) == ["B", "G", "R"]
    for plane, key in enumerate(("R", "G", "B")):
        planes, header = read_fits(got[key])
        assert len(planes) == 1 and header["FILTER"] == key
        assert header["LOOMSRC"] == f"n.fits|plane:{plane}"
    assert cr.master_reads() == 1


def test_narrowband_correction_and_the_lazy_path_get_one_channel_plates(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    cr = _ColourRun(tmp_path, monkeypatch, graxpertNarrowband=True, gradientTool="GraXpert")
    got = _spy_chain(monkeypatch)
    cr.run.load_channels()
    cr.run.correct_narrowband()
    assert sorted(got) == ["H", "O"]
    assert all(_channels_of(p) == 1 for p in got.values())
    assert read_fits(got["H"])[1]["FILTER"] == "Ha" and read_fits(got["O"])[1]["FILTER"] == "OIII"
    assert np.array_equal(read_fits(got["O"])[0][0], cr.planes[1])
    # without the narrowband option no chain runs: the channels stay lazy and the plate is split when asked for
    lazy = _ColourRun(tmp_path / "lazy", monkeypatch)
    lazy.run.load_channels()
    lazy.run.correct_narrowband()
    assert lazy.master_reads() == 0
    plate = lazy.run.plate_of("O")
    assert _channels_of(plate) == 1 and np.array_equal(read_fits(plate)[0][0], lazy.planes[1])
    assert lazy.run.plate_of("H") != plate and lazy.master_reads() == 1


def test_the_group_line_is_logged_on_a_cold_and_on_a_warm_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cold = _ColourRun(tmp_path, monkeypatch)
    cold.run.load_channels()
    assert _COLOUR_LOG not in cold.lines  # the numbers do not exist before the master is read
    cold.run.plate_of("H")
    assert cold.lines.count(_COLOUR_LOG) == 1 and cold.master_reads() == 1
    cold.run.plate_of("O")
    assert cold.lines.count(_COLOUR_LOG) == 1 and cold.master_reads() == 1
    warm = _ColourRun(tmp_path, monkeypatch)
    warm.run.load_channels()
    assert warm.lines.count(_COLOUR_LOG) == 1  # from the sidecar, with no read
    warm.run.plate_of("H")
    warm.run.plate_of("O")
    assert warm.lines.count(_COLOUR_LOG) == 1 and warm.master_reads() == 0


def test_with_the_cache_off_the_master_is_read_once_per_run_and_nothing_is_cached(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for n in (1, 2):
        cr = _ColourRun(tmp_path, monkeypatch, useCache=False)
        cr.run.load_channels()
        cr.run.plate_of("H")
        cr.run.plate_of("O")
        assert cr.master_reads() == 1
        assert cr.lines.count(_COLOUR_LOG) == 1
        assert os.listdir(tmp_path / "cache") == []


# ---- provenance: LOOMSRC values and the palette log lines ---------------------------------------------------------------

_COMMON = {"x": 0, "y": 0, "w": 4, "h": 4}


def test_result_sources_name_the_master_for_the_composite_and_every_palette_product(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cr = _ColourRun(tmp_path, monkeypatch, "OSC", "broadband", palettes=[])
    cr.run.load_channels()
    names = ["L", "R", "RGB", "RGB_starless", "RGB_stars", "RGB_linear", "HOO", "HOO_starless", "HOO_stars"]
    got = cr.run.result_sources(names)
    value = "OSC n.fits planes 0,1,2"
    assert got == {n: value for n in ("RGB", "RGB_starless", "RGB_stars", "RGB_linear")}


def test_result_sources_of_a_dual_band_run_name_each_plane_of_the_palette(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cr = _ColourRun(tmp_path, monkeypatch, palettes=["HOO"])
    cr.run.load_channels()
    value = "OSCNB n.fits plane 0; OSCNB n.fits plane 1"
    got = cr.run.result_sources(["H", "O", "HOO", "HOO_starless", "HOO_stars", "HOO_linear", "SHO"])
    assert got == {n: value for n in ("HOO", "HOO_starless", "HOO_stars", "HOO_linear")}


def test_result_sources_of_a_mono_run_are_empty(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    cfg = _config(tmp_path, _rgb_paths(tmp_path))
    (tmp_path / "work").mkdir(exist_ok=True)
    state = pipeline._Run(harness.factory()(str(tmp_path / "work")), cfg, None, str(tmp_path / "cache"), {})
    state.load_channels()
    assert state.result_sources(["L", "R", "RGB", "RGB_linear", "HOO"]) == {}


def test_a_palette_built_from_a_dual_band_master_logs_its_sources_and_the_approximation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from loomsiril.loom import osc
    cr = _ColourRun(tmp_path, monkeypatch, palettes=["HOO"])
    _spy_chain(monkeypatch)
    cr.run.load_channels()
    cr.run.build_palettes({"H": "h.fits", "O": "o.fits"}, _COMMON, None)
    info = osc.osc_filter_info("L-eXtreme")
    sources = ("HOO: H from OSCNB L-eXtreme (Ha, plus SII for quad-band), "
               "O from OSCNB L-eXtreme (OIII with H\u03b2)")
    ha, oiii = (f"{v:g}" for v in (info["haNm"], info["oiiiNm"]))
    approx = ("HOO: approximation: H and O from a colour master are not single lines; "
              f"bandwidths R {ha}nm, G {oiii}nm, B {oiii}nm")
    assert sources in cr.lines and approx in cr.lines
    assert cr.lines.index(sources) < cr.lines.index(approx)


def test_a_dual_band_only_run_builds_hoo_without_a_sensor_or_any_spcc_names(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline cover for the OSCNB-only path: no sensor is chosen, HOO is produced, and its narrowband SPCC call
    carries no sensor, filter or low-pass filter names (a fresh session: no broadband mode ran before it)."""
    cr = _ColourRun(tmp_path, monkeypatch, palettes=["HOO"])
    # full-frame planes: the helper's half-black planes would make the common-area crop refuse the run
    write_fits(cr.master, [_plane(i + 1) for i in range(3)],
               {"FILTER": "L-eXtreme", "INSTRUME": INSTRUME, "OBJECT": "NGC 7000"}, bitpix=-32)
    cfg = dict(cr.cfg)
    cfg.pop("oscSensor", None)
    assert [slot for slot, path in cfg["paths"].items() if path] == ["OSCNB"]
    harness = Harness(tmp_path)
    report = harness.run(cfg, "oscnb-only")
    assert report.produced == ["HOO"]
    assert os.path.isfile(report.outputs["HOO"])
    calls = [c for ops in harness.opses for c in ops.calls]
    assert not [c for c in calls if c[0] in ("spcc_osc", "spcc_broadband")]
    narrowband = [c[1] for c in calls if c[0] == "spcc_narrowband"]
    assert narrowband and all(args[6:] == (None, None, None, None, None) for args in narrowband)
    texts = [t for ops in harness.opses for t in ops.transcript if t.startswith("spcc")]
    assert texts and not any(flag in t for t in texts
                             for flag in ("-oscsensor", "-oscfilter", "-osclpf", "-monosensor", "-rfilter"))


def test_a_palette_from_mono_channels_logs_neither_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(tmp_path)
    cfg = _config(tmp_path, _nb_paths(tmp_path), palettes=["HOO"])
    (tmp_path / "work").mkdir(exist_ok=True)
    lines: list[str] = []
    ctx = harness.factory()(str(tmp_path / "work"))
    ctx.log = ctx.warn = lines.append
    state = pipeline._Run(ctx, cfg, None, str(tmp_path / "cache"), {})
    _spy_chain(monkeypatch)
    state.load_channels()
    state.build_palettes({"H": "h.fits", "S": "s.fits", "O": "o.fits"}, _COMMON, None)
    assert not any("approximation" in t or "from OSCNB" in t for t in lines)


def _plate(tmp_path: Path, name: str, planes: int) -> str:
    path = tmp_path / "plates" / f"{name}.fits"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_fits(path, [np.full(SHAPE, 0.5, np.float32) for _ in range(planes)], {}, bitpix=-32)
    return str(path)


def _loomsrc_commands(cr: "_ColourRun") -> list[str]:
    return [t for t in cr.ctx.ops.transcript if t.startswith("update_key LOOMSRC")]


def test_publish_results_writes_loomsrc_on_the_composite_and_the_palette_cold_and_warm(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cr = _ColourRun(tmp_path, monkeypatch, "OSC", "broadband", palettes=["HOO"], outputDir=str(tmp_path / "out"))
    cr.cfg["paths"]["OSCNB"] = cr.master
    cr.cfg["oscKinds"] = {"OSC": "broadband", "OSCNB": "dualband"}
    from loomsiril.loom import icc
    cr.run.plan = icc.profile_plan([])
    cr.run.load_channels()
    rgb, hoo, mono = _plate(tmp_path, "rgb", 3), _plate(tmp_path, "hoo", 3), _plate(tmp_path, "l", 1)
    expected = ['update_key LOOMSRC "OSC n.fits planes 0,1,2" "colour master and planes"',
                'update_key LOOMSRC "OSCNB n.fits plane 0; OSCNB n.fits plane 1" "colour master and planes"']
    for _run in ("cold", "warm"):
        before = len(cr.ctx.ops.transcript)
        cr.run.publish_results({"RGB": rgb, "HOO": hoo, "L": mono})
        added = cr.ctx.ops.transcript[before:]
        assert [t for t in added if t.startswith("update_key LOOMSRC")] == expected
    assert len(_loomsrc_commands(cr)) == 4


def test_build_rgb_hands_the_loomsrc_to_the_rgb_runners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cr = _ColourRun(tmp_path, monkeypatch, "OSC", "broadband")
    _spy_chain(monkeypatch)
    seen: list[Any] = []
    real = pipeline.stages_colour.rgb_runners

    def spy(ctx: Any, plates: Any, config: Any, lum: Any, factors: Any, loomsrc: Any = None) -> Any:
        seen.append(loomsrc)
        return real(ctx, plates, config, lum, factors, loomsrc)

    monkeypatch.setattr(pipeline.stages_colour, "rgb_runners", spy)
    cr.run.load_channels()
    cr.run.build_rgb({"R": "r", "G": "g", "B": "b"}, _COMMON, None, INSTRUME)
    assert seen == ["OSC n.fits planes 0,1,2"]


def test_build_palettes_hands_the_loomsrc_to_the_palette_runners(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    cr = _ColourRun(tmp_path, monkeypatch, palettes=["HOO"])
    _spy_chain(monkeypatch)
    seen: list[Any] = []
    real = pipeline.stages_colour.palette_runners

    def spy(ctx: Any, pal: Any, plates: Any, config: Any, names: Any, loomsrc: Any = None) -> Any:
        seen.append(loomsrc)
        return real(ctx, pal, plates, config, names, loomsrc)

    monkeypatch.setattr(pipeline.stages_colour, "palette_runners", spy)
    cr.run.load_channels()
    cr.run.build_palettes({"H": "h.fits", "O": "o.fits"}, _COMMON, None)
    assert seen == ["OSCNB n.fits plane 0; OSCNB n.fits plane 1"]
