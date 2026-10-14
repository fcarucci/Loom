"""The wiring of the run orchestrator: which keys, flags and arguments each step passes on (no Siril, no network).

The stage functions are wrapped by spies that record their arguments and call the real function. The expected values
come from Loom's source (lib/Pipeline.js line references in the assertions) or from the chain the run itself built.
Shared fixtures and the recording fake are those of tests/loom/test_pipeline.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import pytest

from loomsiril.loom import cache, levels, pipeline, stages_colour, stages_correct, stages_geometry
from loomsiril.loom import finishing
from loomsiril.loom.chain import RunCancelled
from loomsiril.loom.runctx import StageOutput

from .test_pipeline import (INSTRUME, Harness, _composite_handler, _config, _handlers, _lum_rgb_paths, _nb_paths,
                            _register_handler, _rgb_paths, _stub_sharpen_tools)


def _stub_star_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Star extraction needs a star tool: a copy stands in for it and writes a stars companion."""
    real_runners = finishing.finishing_runners

    def runners(ctx: Any, label: str, names: Any, config: Any, *, noun: Any = None) -> dict:
        built = real_runners(ctx, label, names, config, noun=noun)
        if names.get("extract"):
            def extract(in_path: str, out_path: str) -> StageOutput:
                stars = os.path.join(ctx.work_dir, f"{label}_stars.fits")
                for target in (stars, out_path):
                    with open(in_path, "rb") as src, open(target, "wb") as dst:
                        dst.write(src.read())
                return StageOutput(path=out_path, companions={"stars": stars})

            built[names["extract"]] = extract
        return built

    monkeypatch.setattr(finishing, "finishing_runners", runners)


class Spy:
    """Records the calls of each wrapped function; ``chains`` holds every chain the run built."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.mp = monkeypatch
        self.chains: list[tuple[Any, Any, list]] = []
        self.labels: list[str] = []
        self.calls: dict[str, list[tuple]] = {}

    def wrap(self, module: Any, name: str, replace: Callable[..., Any] | None = None) -> None:
        real = getattr(module, name)

        def spy(*args: Any, **kwargs: Any) -> Any:
            self.calls.setdefault(name, []).append(args)
            if replace is not None:
                return replace(*args, **kwargs)
            return real(*args, **kwargs)

        self.mp.setattr(module, name, spy)

    def install(self) -> "Spy":
        real_build = cache.build_stage_keys
        real_chain = pipeline.process_chain

        def build(source: Any, params: Any) -> list:
            chain = real_build(source, params)
            self.chains.append((source, params, chain))
            return chain

        def chain_spy(ctx: Any, label: str, *args: Any, **kwargs: Any) -> Any:
            self.labels.append(label)
            return real_chain(ctx, label, *args, **kwargs)

        self.mp.setattr(cache, "build_stage_keys", build)
        self.mp.setattr(pipeline, "process_chain", chain_spy)
        for name in ("rgb_source_key", "palette_source_key", "lstars_source_key"):
            self.wrap(cache, name)
        self.wrap(stages_geometry, "balance_narrowband")
        self.wrap(stages_geometry, "match_halos")
        self.wrap(stages_geometry, "register_channels")
        self.wrap(stages_geometry, "register_stage_params")
        self.wrap(stages_colour, "palette_runners")
        self.wrap(stages_colour, "measure_clean_white_balance", replace=lambda *a, **k: [1.0, 1.0, 1.0])
        return self

    def last_chain_key(self, source: str) -> str:
        """The last key of the first chain built from ``source`` (the chain of one channel)."""
        for src, _params, chain in self.chains:
            if src == source and chain:
                return str(chain[-1]["key"])
        raise AssertionError("no chain was built from " + source)

    def current_key(self, path: str) -> str:
        """The channel's key after registration: its register chain's last key, or its source key when it has none."""
        for src, params, chain in self.chains:
            if src == _source(path) and params and "register" in params:
                return str(chain[-1]["key"])
        return _source(path)

    def register_chains(self) -> list[list]:
        return [chain for _src, params, chain in self.chains if params and "register" in params]


def _source(path: str) -> str:
    return stages_correct.source_key(path)


def _rgb_with_L(tmp_path: Path, **over: Any) -> dict[str, Any]:
    return _config(tmp_path, _lum_rgb_paths(tmp_path), **over)


# ---- cache keys of the composite, the palettes and the luminance -----------------------------------------------------


def test_rgb_key_takes_registered_keys_crop_and_halos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    paths = _lum_rgb_paths(tmp_path)
    harness = Harness(tmp_path)
    harness.run(_config(tmp_path, paths, reduceHalos=True), "keys-rgb")
    current, common, reduce_halos = spy.calls["rgb_source_key"][0]
    registered = [chain[-1]["key"] for chain in spy.register_chains()]
    assert [current["R"], current["G"], current["B"]] == registered  # the keys after registration, in R, G, B order
    assert common == {"x0": 4, "y0": 4, "x1": 60, "y1": 44}  # the crop of the valid area (margin four)
    assert reduce_halos is True
    literal = cache.hash_str("rgb|" + registered[0] + "|" + registered[1] + "|" + registered[2]
                             + "|crop:4,4,60,44|halos:1")
    assert cache.rgb_source_key(current, common, True) == literal  # pipeline.js:1608-1610


def test_luminance_key_is_the_post_correction_key_with_halos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    _stub_star_extraction(monkeypatch)
    paths = _lum_rgb_paths(tmp_path)
    Harness(tmp_path).run(_config(tmp_path, paths, starTool="StarNet2", reduceHalos=True), "keys-lstars")
    l_key, _common, reduce_halos = spy.calls["lstars_source_key"][0]
    assert l_key == spy.last_chain_key(_source(paths["L"]))  # L's chain after its own correction
    assert l_key != _source(paths["L"])
    assert reduce_halos is True


def test_palette_key_uses_channels_in_map_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    nb = _nb_paths(tmp_path)
    Harness(tmp_path).run(_config(tmp_path, nb, palettes=["SHO"], reduceHalos=True), "keys-pal")
    palette, keys3, common, reduce_halos = spy.calls["palette_source_key"][0]
    assert palette == "SHO"
    # SHO maps S, H, O (channels.py); each channel's key is the one registration left it with
    assert keys3 == [spy.current_key(nb[ch]) for ch in ("S", "H", "O")]
    assert common == {"x0": 4, "y0": 4, "x1": 60, "y1": 44}
    assert reduce_halos is True


def test_register_reference_is_the_reference_chain_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    paths = _lum_rgb_paths(tmp_path)
    Harness(tmp_path).run(_config(tmp_path, paths), "keys-ref")
    (ref_fingerprint,) = spy.calls["register_stage_params"][0]
    assert ref_fingerprint == spy.last_chain_key(_source(paths["L"]))
    assert ref_fingerprint != _source(paths["L"])


def test_register_plates_put_the_reference_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    Harness(tmp_path).run(_config(tmp_path, _lum_rgb_paths(tmp_path)), "reg-order")
    for args in spy.calls["register_channels"]:
        plates, ref = args[1], args[2]
        assert ref == "L"
        assert list(plates)[0] == "L"  # the reference is frame 1 of the sequence


def test_palette_receives_the_run_rgb_spcc_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    paths = {**_rgb_paths(tmp_path), **_nb_paths(tmp_path)}
    cfg = _config(tmp_path, paths, palettes=["SHO"])
    Harness(tmp_path).run(cfg, "pal-names")
    names = spy.calls["palette_runners"][0][4]
    assert names is not None
    assert names == stages_colour.rgb_spcc_names(cfg, INSTRUME)  # the composite's sensor and three filters


def test_palette_without_rgb_gets_no_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    Harness(tmp_path).run(_config(tmp_path, _nb_paths(tmp_path), palettes=["SHO"]), "pal-nonames")
    assert spy.calls["palette_runners"][0][4] is None


# ---- conditions -----------------------------------------------------------------------------------------------------


def test_halos_only_with_reduce_halos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    Harness(tmp_path).run(_config(tmp_path, _lum_rgb_paths(tmp_path)), "halos-off")
    assert "match_halos" not in spy.calls
    Harness(tmp_path).run(_config(tmp_path, _lum_rgb_paths(tmp_path), reduceHalos=True), "halos-on")
    assert len(spy.calls["match_halos"]) == 1


def test_narrowband_balance_is_an_offset_only_with_palettes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    Harness(tmp_path).run(_config(tmp_path, _nb_paths(tmp_path)), "bal-linear")
    assert spy.calls["balance_narrowband"][-1][2] is False  # no palette: linear fit (pipeline.js:2360-2388)
    Harness(tmp_path).run(_config(tmp_path, _nb_paths(tmp_path), palettes=["SHO"]), "bal-offset")
    assert spy.calls["balance_narrowband"][-1][2] is True  # a palette: background offset only


def test_clean_white_balance_needs_rgb_and_a_sharpen_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    _stub_sharpen_tools(monkeypatch)
    Harness(tmp_path).run(_config(tmp_path, _rgb_paths(tmp_path)), "cwb-no-tool")
    assert "measure_clean_white_balance" not in spy.calls  # no sharpen tool: no measurement
    paths = {"L": "", **{k: v for k, v in _lum_rgb_paths(tmp_path).items() if k != "B"}}
    Harness(tmp_path).run(_config(tmp_path, paths, sharpenTool="BlurXTerminator"), "cwb-no-b")
    assert "measure_clean_white_balance" not in spy.calls  # no B channel: no RGB, no measurement
    Harness(tmp_path).run(_config(tmp_path, _nb_paths(tmp_path), sharpenTool="BlurXTerminator"), "cwb-nb")
    assert "measure_clean_white_balance" not in spy.calls  # narrowband only


def test_clean_white_balance_reads_the_graxpert_stage_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    monkeypatch.setattr(levels, "tool_chosen", lambda tool: tool not in (None, "none"))
    _stub_sharpen_tools(monkeypatch)
    paths = _lum_rgb_paths(tmp_path)
    harness = Harness(tmp_path)
    harness.run(_config(tmp_path, paths, sharpenTool="BlurXTerminator"), "cwb-keys")
    clean = spy.calls["measure_clean_white_balance"][0][1]
    store = cache.CacheStore(str(tmp_path / "cache"))
    for ch in ("R", "G", "B"):
        key, path = clean[ch]
        chain = next(c for src, params, c in spy.chains if src == _source(paths[ch]) and params and "solve" in params)
        graxpert_key = next(e["key"] for e in chain if e["stage"] == "graxpert")
        assert key == graxpert_key  # the pre-correction entry is the graxpert stage's (pipeline.js:1822)
        assert path == store.path_for(key)


def test_luminance_finishing_needs_a_star_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    Harness(tmp_path).run(_config(tmp_path, _lum_rgb_paths(tmp_path), stretch=True), "lfin-none")
    assert spy.labels.count("L") == 1  # the broadband chain only
    spy.labels.clear()
    _stub_star_extraction(monkeypatch)
    Harness(tmp_path).run(_config(tmp_path, _lum_rgb_paths(tmp_path), starTool="StarNet2"), "lfin-star")
    assert spy.labels.count("L") == 2  # plus the luminance finishing tail


def test_narrowband_chain_only_with_the_gradient_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch).install()
    nb = _nb_paths(tmp_path)
    Harness(tmp_path).run(_config(tmp_path, nb), "nb-chain-off")
    nb_sources = {_source(nb[ch]) for ch in ("H", "S", "O")}
    correction = [src for src, params, _c in spy.chains if src in nb_sources and not (params and "register" in params)]
    assert correction == []  # no gradient chain without the narrowband option


# ---- hand-off, cwd, cancel and folders ------------------------------------------------------------------------------


def test_main_result_is_loaded_after_the_export(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    cfg = _config(tmp_path, _nb_paths(tmp_path), stretch=True, exportDir=str(tmp_path / "tif"))
    report = harness.run(cfg, "main-last")
    assert report.produced == ["H", "O", "S"]
    loads = [t for t in harness.ops.transcript if t.startswith("load ")]
    assert loads[-1] == f'load "{report.outputs["H"]}"'  # main result H; the export's last load is S
    assert len(report.exports) == 3


def test_cwd_is_restored_after_a_stage_changes_it(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"

    def composite(ops: Any, r: Any, g: Any, b: Any, out: Any) -> str:
        ops.cd(str(elsewhere))  # a stage leaves Siril in another folder
        return _composite_handler(ops, r, g, b, out)

    handlers = {**_handlers(), "rgbcomp": composite}
    harness = Harness(tmp_path, handlers=handlers)
    harness.run(_config(tmp_path, _rgb_paths(tmp_path)), "cwd")
    assert harness.ops.workdir == harness.siril_wd


def test_cancel_removes_the_work_folder_even_when_kept(tmp_path: Path) -> None:
    harness = Harness(tmp_path, cancel_after=3)
    with pytest.raises(RunCancelled):
        harness.run(_config(tmp_path, _lum_rgb_paths(tmp_path), keepWindowsOnError=True), "cancel-keep")
    assert not os.path.exists(tmp_path / "cache" / "work" / "cancel-keep")


def test_cancel_after_registration_stops_before_the_crop(tmp_path: Path) -> None:
    # one channel to register: no later stage would poll for the cancel, so only the checkpoint can stop the run
    harness = Harness(tmp_path)

    def register(ops: Any, name: str) -> None:
        _register_handler(ops, name)
        harness.force = True  # cancelled as the first channel is registered

    harness.handlers["register"] = register
    with pytest.raises(RunCancelled):
        harness.run(_config(tmp_path, {ch: p for ch, p in _lum_rgb_paths(tmp_path).items() if ch in ("L", "R")}),
                    "cancel-reg")
    assert not any(t.startswith("crop") for t in harness.ops.transcript)


def test_cancel_before_publishing_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(tmp_path)
    real_named = pipeline._named_results

    def named(*args: Any, **kwargs: Any) -> Any:
        result = real_named(*args, **kwargs)
        harness.force = True  # cancelled after the last stage, just before publishing
        return result

    monkeypatch.setattr(pipeline, "_named_results", named)
    with pytest.raises(RunCancelled):
        harness.run(_config(tmp_path, _rgb_paths(tmp_path)), "cancel-pub")
    assert not os.path.isdir(tmp_path / "out")  # publishing creates the output folder; it was never reached


def test_work_folder_is_unique_and_an_existing_one_is_kept(tmp_path: Path) -> None:
    existing = tmp_path / "cache" / "work" / "unique"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("earlier run", encoding="utf-8")
    report = Harness(tmp_path).run(_config(tmp_path, _rgb_paths(tmp_path)), "unique")
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "earlier run"
    assert report.produced == ["RGB"]


def test_exports_list_only_files_this_run_wrote(tmp_path: Path) -> None:
    tif = tmp_path / "tif"
    tif.mkdir()
    (tif / "RGB.tif").write_bytes(b"stale from an earlier run")
    harness = Harness(tmp_path, icc=None)  # no profile: the export of RGB fails and writes nothing
    report = harness.run(_config(tmp_path, _rgb_paths(tmp_path), stretch=True, exportDir=str(tif)), "stale")
    assert report.exports == []
    assert (tif / "RGB.tif").read_bytes() == b"stale from an earlier run"
    assert any("could not be exported" in w for w in report.warnings)


# ---- log lines --------------------------------------------------------------------------------------------------------


def test_long_lines_are_shortened_for_the_log_and_the_callbacks() -> None:
    long = "x" * 1000 + "\nsecond line"
    short = pipeline._short(long)
    assert len(short) <= 403 and short.endswith("...") and "\n" not in short
    seen: dict[str, list[str]] = {"log": [], "warn": [], "error": []}

    class Ctx:
        log = staticmethod(lambda text: seen["log"].append(text))
        warn = staticmethod(lambda text: seen["warn"].append(text))
        error = staticmethod(lambda text: seen["error"].append(text))

    ctx = Ctx()
    rlog = pipeline._RunLog("")
    log, warn, error = pipeline._wrap_logs(ctx, rlog)  # type: ignore[arg-type]
    log(long)
    warn(long)
    error(long)
    for kind in seen:
        assert len(seen[kind]) == 1 and len(seen[kind][0]) <= 403, kind


def test_luminance_is_the_reference_whenever_it_is_present() -> None:
    """The composite's camera is the luminance's whenever L is present, so it is also the reference's: the reference
    is L (reference.py: L is always the reference). Keeps the composite-instrument rule tied to that choice."""
    from loomsiril.loom import reference

    assert reference.registration_choice(["L", "R", "G", "B"], {})[0] == "L"
    assert reference.registration_choice(["H", "L"], {"H": {"fwhm": 2.0, "stars": 50}})[0] == "L"
