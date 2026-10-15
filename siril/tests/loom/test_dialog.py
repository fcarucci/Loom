"""The Siril options dialog: its pure model (DialogModel, ProgressModel), and a smoke test of the tkinter views.

The model needs no display and no tkinter. The view test skips where tkinter cannot open a window.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from loomsiril import starnet_licence
from loomsiril.loom import channels as ch
from loomsiril.loom import config as cfg
from loomsiril.loom import dialog as dl
from loomsiril.loom import levels as lv
from loomsiril.loom.tools.resolver import Found


def found(tool_id: str, path: str | None = None, source: str = "known") -> Found:
    return Found(tool_id, path or f"/opt/loom/{tool_id}", source)


def all_found() -> dict[str, Found]:
    return {tool_id: found(tool_id) for tool_id in dl.TOOL_LABELS}


def config(**overrides: Any) -> dict[str, Any]:
    out = cfg.defaults()
    out.update(overrides)
    return out


class FakeResolver:
    """Stands in for the Resolver: ``results`` maps a tool id to its Found (or None); ``reasons`` to a refusal."""

    def __init__(self, results: dict[str, Found | None] | None = None, reasons: dict[str, str] | None = None) -> None:
        self.results: dict[str, Found | None] = dict(results or {})
        self.reasons: dict[str, str] = dict(reasons or {})
        self.calls: list[tuple[str, str | None]] = []

    def resolve(self, tool_id: str, configured: str | None = None) -> Found | None:
        self.calls.append((tool_id, configured))
        return self.results.get(tool_id)

    def rejection(self, tool_id: str) -> str | None:
        return self.reasons.get(tool_id)


class FakeStore:
    """An in-memory stand-in for the ToolStore (the real one refuses paths under the temporary folder)."""

    def __init__(self) -> None:
        self.records: dict[str, tuple[str, int, int]] = {}
        self.flags: dict[str, Any] = {}

    def remember(self, f: Found, size: int, mtime: int, probed: str = "") -> None:
        self.records[f.tool_id] = (f.path, size, mtime)

    def get_flag(self, name: str) -> Any:
        return self.flags.get(name)

    def set_flag(self, name: str, value: Any) -> None:
        self.flags[name] = value


def noise_ids(model: dl.DialogModel) -> list[str]:
    return [value for value, _label in model.choices(dl.GROUP_NOISE)]


# ---- values <-> config ----------------------------------------------------------------------------------------

SAMPLE: dict[str, Any] = {
    "graxpertNarrowband": True,
    "narrowbandBandwidth": 4.5,
    "narrowbandNormalize": False,
    "reduceHalos": True,
    "keepLinear": True,
    "separateLStars": True,
    "exportPsb": True,
    "projectName": "M31",
    "exportDir": "/out/tiffs",
    "stretch": True,
    "stretchMethod": "ghs",
    "starTool": lv.STAR_TOOL_STARNET,
    "noiseTool": lv.NR_TOOL_NXT,
    "noiseLevel": "high",
    "noiseLevelL": "low",
    "sharpenTool": lv.SHARPEN_TOOL_BXT,
    "starReduction": "medium",
    "detailLevel": "high",
    "parallaxFamily": "aesthetics",
    "useCache": False,
    "cacheDir": "/cache/loom",
    "smoothing": 0.7,
    "savedList": "saved entry",
    "spccSensor": "IMX571",
    "gradientTool": lv.GRADIENT_TOOL_GRAXPERT,
}


def test_sample_covers_every_field_and_the_gradient_tool() -> None:
    assert set(SAMPLE) == (set(cfg.FIELDS) - {"outputDir", "stretchTarget"}) | {"gradientTool"}   # outputDir: output_dir()


def test_every_field_reads_from_its_config_key() -> None:
    model = dl.DialogModel.from_config(config(**SAMPLE), all_found(), studio_prism2_unavailable=False)
    for key, value in SAMPLE.items():
        assert model.get(key) == value, key


def test_round_trip_returns_the_config_it_was_given() -> None:
    source = config(**SAMPLE, paths={"L": "/m/L.fits", "R": "", "G": "", "B": "", "H": "", "S": "", "O": ""},
                    palettes=[ch.palette_names()[0]], filters={"R": "Red"}, validateOnly=True)
    model = dl.DialogModel.from_config(source, all_found())
    assert model.take_notices() == []
    assert model.to_config() == source


def test_each_field_set_is_written_back_by_to_config() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    for key in SAMPLE:
        model.set_field(key, SAMPLE[key])
    out = model.to_config()
    for key, value in SAMPLE.items():
        assert out[key] == value, key


def test_a_second_round_trip_is_stable() -> None:
    model = dl.DialogModel.from_config(config(**SAMPLE), all_found())
    once = model.to_config()
    assert dl.DialogModel.from_config(once, all_found()).to_config() == once


def test_unknown_option_names_are_rejected() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    with pytest.raises(KeyError):
        model.get("noSuchOption")
    with pytest.raises(KeyError):
        model.set_field("noSuchOption", 1)


# ---- output folder ----------------------------------------------------------------------------------------------


def test_output_folder_reopens_as_saved_and_run_waits_without_one() -> None:
    model = dl.DialogModel.from_config(config(outputDir="/old/place"), all_found())
    assert model.output_dir() == "/old/place"
    empty = dl.DialogModel.from_config(config(), all_found())
    assert empty.output_dir() == ""
    assert empty.run_reasons() == ["Choose an output folder first."]
    assert not empty.can_run()


def test_output_folder_is_saved_in_the_config() -> None:
    model = dl.DialogModel.from_config(config(outputDir="/old/place"), all_found())
    assert model.to_config()["outputDir"] == "/old/place"
    model.set_output_dir("/work/out")
    assert model.output_dir() == "/work/out"
    assert model.to_config()["outputDir"] == "/work/out"


# ---- run: reasons, request ---------------------------------------------------------------------------------------


def test_run_is_allowed_once_the_folder_is_chosen() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    model.set_output_dir("/work/out")
    assert model.run_reasons() == []
    assert model.can_run()
    request = model.run_request()
    assert request.output_dir == "/work/out"
    assert request.config == model.to_config()
    assert request.starnet_revision is None


def test_run_waits_for_the_starnet_licence() -> None:
    model = dl.DialogModel.from_config(config(starTool=lv.STAR_TOOL_STARNET), all_found())
    model.set_output_dir("/work/out")
    assert model.run_reasons() == ["StarNet2 needs its licence accepted first."]
    model.accept_starnet(starnet_licence.STARNET_LICENCE_REV)
    assert model.run_reasons() == []
    assert model.run_request().starnet_revision == starnet_licence.STARNET_LICENCE_REV


def test_preflight_problems_are_shown_with_every_other_reason() -> None:
    seen: list[dl.RunRequest] = []

    def preflight(request: dl.RunRequest) -> list[str]:
        seen.append(request)
        return ["L master has no FILTER keyword", "R master is not readable"]

    model = dl.DialogModel.from_config(config(starTool=lv.STAR_TOOL_STARNET), all_found(), preflight=preflight)
    assert model.run_reasons() == ["Choose an output folder first.", "StarNet2 needs its licence accepted first.",
                                   "L master has no FILTER keyword", "R master is not readable"]
    model.set_output_dir("/work/out")
    with pytest.raises(dl.RunBlocked) as blocked:
        model.run_request()
    assert blocked.value.reasons == ["StarNet2 needs its licence accepted first.",
                                     "L master has no FILTER keyword", "R master is not readable"]
    assert str(blocked.value).count("\n") == 2
    assert seen and seen[-1].output_dir == "/work/out"


def test_preflight_is_run_again_only_when_something_changed() -> None:
    calls: list[int] = []

    def preflight(request: dl.RunRequest) -> list[str]:
        calls.append(1)
        return []

    model = dl.DialogModel.from_config(config(), all_found(), preflight=preflight)
    model.set_output_dir("/work/out")
    model.run_reasons()
    model.run_reasons()
    assert len(calls) == 1
    model.set_field("reduceHalos", True)
    model.run_reasons()
    assert len(calls) == 2


def test_replace_confirmation_follows_whether_an_image_is_loaded() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    assert model.needs_replace_confirmation(True) is True
    assert model.needs_replace_confirmation(False) is False
    assert dl.REPLACE_CONFIRM_TEXT == "The image loaded in Siril will be replaced"


# ---- tools: only found ones, in order, built-ins last ---------------------------------------------------------


def test_only_found_tools_are_listed_in_loom_order() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    assert [v for v, _ in model.choices(dl.GROUP_SHARPEN)] == [
        "none", lv.SHARPEN_TOOL_BXT, lv.SHARPEN_TOOL_SYQON, lv.SHARPEN_TOOL_STUDIO]
    assert [v for v, _ in model.choices(dl.GROUP_STAR)] == [
        "none", lv.STAR_TOOL_STARNET, lv.STAR_TOOL_SXT, lv.STAR_TOOL_SYQON, lv.STAR_TOOL_STUDIO]
    assert [v for v, _ in model.choices(dl.GROUP_GRADIENT)] == [
        "none", lv.GRADIENT_TOOL_GRAXPERT, lv.GRADIENT_TOOL_STUDIO]


def test_siril_denoise_is_last_in_the_noise_tools() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    assert noise_ids(model) == ["none", lv.NR_TOOL_NXT, lv.NR_TOOL_PRISM, lv.NR_TOOL_STUDIO2, lv.NR_TOOL_SIRIL]


def test_studio_prism_is_essential_once_prism_two_is_refused() -> None:
    model = dl.DialogModel.from_config(config(), all_found(), studio_prism2_unavailable=True)
    assert noise_ids(model) == ["none", lv.NR_TOOL_NXT, lv.NR_TOOL_PRISM, lv.NR_TOOL_STUDIO, lv.NR_TOOL_SIRIL]


def test_a_tool_that_is_not_found_is_not_listed() -> None:
    model = dl.DialogModel.from_config(config(), {"rcastro": found("rcastro"), "prism_cli": found("prism_cli")})
    assert noise_ids(model) == ["none", lv.NR_TOOL_NXT, lv.NR_TOOL_PRISM, lv.NR_TOOL_SIRIL]
    assert [v for v, _ in model.choices(dl.GROUP_SHARPEN)] == ["none", lv.SHARPEN_TOOL_BXT]
    assert [v for v, _ in model.choices(dl.GROUP_STAR)] == ["none", lv.STAR_TOOL_SXT]


def test_groups_with_no_installed_tool_are_hidden() -> None:
    model = dl.DialogModel.from_config(config(), {"prism_cli": found("prism_cli")})
    assert model.visible_groups() == [dl.GROUP_NOISE]
    model = dl.DialogModel.from_config(config(), {})
    assert model.visible_groups() == [dl.GROUP_NOISE]
    model = dl.DialogModel.from_config(config(), all_found())
    assert model.visible_groups() == [dl.GROUP_GRADIENT, dl.GROUP_SHARPEN, dl.GROUP_STAR, dl.GROUP_NOISE]


def test_starnet_is_offered_when_its_cli_is_found() -> None:
    assert lv.STAR_TOOL_STARNET not in [v for v, _ in dl.DialogModel.from_config(config(), {}).choices(dl.GROUP_STAR)]
    model = dl.DialogModel.from_config(config(), {"starnet2": found("starnet2")})
    assert [v for v, _ in model.choices(dl.GROUP_STAR)] == ["none", lv.STAR_TOOL_STARNET]


def test_gradient_none_is_labelled_for_siril_background_extraction() -> None:
    model = dl.DialogModel.from_config(config(), {"graxpert-ai": found("graxpert-ai")})
    assert model.choices(dl.GROUP_GRADIENT)[0] == ("none", "Siril background extraction only")
    assert model.choices(dl.GROUP_SHARPEN)[0] == ("none", "None")


def test_a_choice_that_is_not_offered_is_refused() -> None:
    model = dl.DialogModel.from_config(config(), {"prism_cli": found("prism_cli")})
    with pytest.raises(ValueError):
        model.set_field("noiseTool", lv.NR_TOOL_NXT)
    model.set_field("noiseTool", lv.NR_TOOL_PRISM)
    assert model.get("noiseTool") == lv.NR_TOOL_PRISM


def test_stretch_methods_and_their_labels() -> None:
    assert dl.STRETCH_METHOD_LABELS == {"mtf": "Histogram (deterministic MTF)",
                                        "ghs": "Generalised hyperbolic (Siril GHS)"}
    model = dl.DialogModel.from_config(config(), all_found())
    with pytest.raises(ValueError):
        model.set_field("stretchMethod", "multiscale")


def test_no_starnet_licence_text_without_a_starnet_program() -> None:
    assert dl.DialogModel.from_config(config(), all_found()).starnet_licence_text() is None


# ---- a saved tool that is gone -------------------------------------------------------------------------------------


def test_a_saved_tool_that_is_gone_shows_as_none_with_a_notice_once() -> None:
    model = dl.DialogModel.from_config(config(noiseTool=lv.NR_TOOL_PRISM, starTool=lv.STAR_TOOL_SYQON,
                                              gradientTool="none"), {"rcastro": found("rcastro")})
    assert model.get("noiseTool") == "none"
    assert model.get("starTool") == "none"
    assert model.take_notices() == [
        "SyQon Starless is no longer available; set to none",
        "SyQon Prism is no longer available; set to none",
    ]
    assert model.take_notices() == []


def test_a_saved_tool_that_is_found_stays_and_raises_no_notice() -> None:
    model = dl.DialogModel.from_config(config(noiseTool=lv.NR_TOOL_NXT), all_found())
    assert model.get("noiseTool") == lv.NR_TOOL_NXT
    assert model.take_notices() == []


def test_the_notice_names_the_tool_as_it_was_saved() -> None:
    model = dl.DialogModel.from_config(config(gradientTool=lv.GRADIENT_TOOL_GRAXPERT), {})
    assert model.get("gradientTool") == "none"
    assert "GraXpert is no longer available; set to none" in model.take_notices()


def test_a_tool_that_vanishes_on_rescan_is_named_and_set_to_none() -> None:
    resolver = FakeResolver(results={t: found(t) for t in dl.TOOL_LABELS})
    model = dl.DialogModel.from_config(config(noiseTool=lv.NR_TOOL_NXT, sharpenTool=lv.SHARPEN_TOOL_BXT),
                                       all_found(), resolver=resolver)
    assert model.take_notices() == []
    resolver.results["rcastro"] = None
    assert model.rescan() == [t for t in dl.TOOL_LABELS if t != "rcastro"]
    assert model.get("noiseTool") == "none"
    assert model.get("sharpenTool") == "none"
    assert model.take_notices() == [
        "BlurXTerminator is no longer available; set to none",
        "NoiseXTerminator is no longer available; set to none",
    ]


def test_old_tool_names_are_migrated_as_config_does() -> None:
    model = dl.DialogModel.from_config(config(noiseTool=lv.NR_TOOL_STUDIO_OLD), {"syqon-cli": found("syqon-cli")})
    assert model.get("noiseTool") == lv.NR_TOOL_STUDIO2
    model = dl.DialogModel.from_config(config(noiseTool="MLDenoise"), all_found())
    assert model.get("noiseTool") == "none"
    assert model.take_notices() == ["MLDenoise is not available in Siril; denoise is off"]


def test_interlock_no_sharpen_tool_forces_the_sharpening_levels_to_none() -> None:
    model = dl.DialogModel.from_config(config(starReduction="medium", detailLevel="low"), {"rcastro": found("rcastro")})
    assert model.get("starReduction") == "medium"
    model = dl.DialogModel.from_config(config(sharpenTool=lv.SHARPEN_TOOL_BXT, starReduction="medium", detailLevel="low"),
                                       {"prism_cli": found("prism_cli")})
    assert model.get("sharpenTool") == "none"
    assert model.get("starReduction") == "none"
    assert model.get("detailLevel") == "none"


# ---- noise levels ------------------------------------------------------------------------------------------------


def test_prism_two_offers_medium_only_without_the_stretch() -> None:
    model = dl.DialogModel.from_config(config(stretch=False), all_found())
    model.set_field("noiseTool", lv.NR_TOOL_STUDIO2)
    assert model.noise_levels() == ["medium"]
    assert model.get("noiseLevel") == "medium"


def test_prism_two_offers_high_only_with_the_stretch() -> None:
    model = dl.DialogModel.from_config(config(stretch=True, noiseLevel="high"), all_found())
    model.set_field("noiseTool", lv.NR_TOOL_STUDIO2)
    assert model.noise_levels() == ["medium", "high"]
    assert model.get("noiseLevel") == "high"


def test_a_strength_the_tool_does_not_offer_becomes_medium() -> None:
    model = dl.DialogModel.from_config(config(stretch=False, noiseTool=lv.NR_TOOL_STUDIO2, noiseLevel="high",
                                              noiseLevelL="low"), all_found())
    assert model.get("noiseLevel") == "medium"
    assert model.get("noiseLevelL") == "medium"


def test_turning_the_stretch_off_coerces_prism_two_high_to_medium() -> None:
    model = dl.DialogModel.from_config(config(stretch=True, noiseTool=lv.NR_TOOL_STUDIO2, noiseLevel="high"),
                                       all_found())
    model.set_field("stretch", False)
    assert model.get("noiseLevel") == "medium"


def test_noise_levels_of_the_other_tools_are_all_three() -> None:
    model = dl.DialogModel.from_config(config(stretch=False), all_found())
    model.set_field("noiseTool", lv.NR_TOOL_NXT)
    assert model.noise_levels() == ["low", "medium", "high"]
    assert [model.noise_level_label(v) for v in model.noise_levels()] == ["Low", "Medium", "High"]


def test_prism_two_names_its_levels_ultra_and_max() -> None:
    model = dl.DialogModel.from_config(config(stretch=True), all_found())
    model.set_field("noiseTool", lv.NR_TOOL_STUDIO2)
    assert model.noise_level_label("medium") == "Ultra"
    assert model.noise_level_label("high") == "Max"


def test_noise_level_for_the_l_channel_follows_the_colour_level_when_blank() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    assert model.get("noiseLevelL") == ""
    assert model.enabled("noiseLevelL") is False  # no noise tool chosen


# ---- interlocks ----------------------------------------------------------------------------------------------------


def test_stretch_off_disables_the_stretch_controls_and_keeps_their_values() -> None:
    model = dl.DialogModel.from_config(config(stretch=False, keepLinear=True, exportPsb=True, exportDir="/o"),
                                       all_found())
    for name in ("keepLinear", "stretchMethod", "exportDir", "exportPsb"):
        assert model.enabled(name) is False, name
    assert model.get("keepLinear") is True
    model.set_field("stretch", True)
    for name in ("keepLinear", "stretchMethod", "exportDir", "exportPsb"):
        assert model.enabled(name) is True, name


def test_star_and_detail_levels_need_a_sharpen_tool_and_the_family_needs_studio() -> None:
    model = dl.DialogModel.from_config(config(sharpenTool=lv.SHARPEN_TOOL_BXT), all_found())
    assert model.enabled("starReduction") and model.enabled("detailLevel")
    assert not model.enabled("parallaxFamily")
    model.set_field("sharpenTool", lv.SHARPEN_TOOL_STUDIO)
    assert model.enabled("parallaxFamily")


def test_the_cache_folder_is_live_only_with_the_cache() -> None:
    model = dl.DialogModel.from_config(config(useCache=True), all_found())
    assert model.enabled("cacheDir")
    model.set_field("useCache", False)
    assert not model.enabled("cacheDir")


# ---- palettes -------------------------------------------------------------------------------------------------------


def test_palettes_are_hidden_without_a_narrowband_channel() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    assert model.palettes_visible() is False
    model.set_path("H", "/m/H.fits")
    assert model.palettes_visible() is True
    model.set_path("H", "")
    assert model.palettes_visible() is False


def test_a_palette_is_ticked_and_unticked_into_the_config() -> None:
    palette = ch.palette_names()[0]
    model = dl.DialogModel.from_config(config(), all_found())
    model.set_palette(palette, True)
    model.set_palette(palette, True)
    assert model.to_config()["palettes"] == [palette]
    assert model.palette_selected(palette)
    model.set_palette(palette, False)
    assert model.to_config()["palettes"] == []


def test_an_unknown_palette_is_refused() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    with pytest.raises(ValueError):
        model.set_palette("NOT-A-PALETTE", True)


# ---- files, filters and the project name -----------------------------------------------------------------------------


def test_channel_files_are_written_into_the_paths_map() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    model.set_path("L", "/m/L.fits")
    assert model.path("L") == "/m/L.fits"
    assert model.to_config()["paths"]["L"] == "/m/L.fits"
    with pytest.raises(KeyError):
        model.set_path("X", "/m/X.fits")


def test_filters_are_kept_for_r_g_and_b_only() -> None:
    model = dl.DialogModel.from_config(config(), all_found(), spcc_names=lambda kind: [kind + "-a", kind + "-b"])
    assert model.filter_choices("R") == ["redfilter-a", "redfilter-b"]
    model.set_filter("G", "Green A")
    assert model.to_config()["filters"] == {"G": "Green A"}
    model.set_filter("G", "")
    assert model.to_config()["filters"] == {}
    with pytest.raises(KeyError):
        model.set_filter("L", "x")


def test_project_name_is_guessed_from_the_first_source_when_blank() -> None:
    model = dl.DialogModel.from_config(config(paths={"L": "/data/M31/masters/L.fits"}), all_found())
    assert model.get("projectName") == ""
    assert model.project_name_hint() == "M31"


# ---- StarNet2 licence ----------------------------------------------------------------------------------------------


def test_accepting_the_licence_stores_the_revision_in_the_store() -> None:
    store = FakeStore()
    model = dl.DialogModel.from_config(config(starTool=lv.STAR_TOOL_STARNET), all_found(), store=store)
    assert not model.starnet_accepted()
    model.accept_starnet(starnet_licence.STARNET_LICENCE_REV)
    assert model.starnet_accepted()
    assert store.flags[dl.STARNET_FLAG] == starnet_licence.STARNET_LICENCE_REV
    again = dl.DialogModel.from_config(config(starTool=lv.STAR_TOOL_STARNET), all_found(), store=store)
    assert again.starnet_accepted()


def test_an_old_licence_revision_is_asked_again() -> None:
    store = FakeStore()
    store.flags[dl.STARNET_FLAG] = "2020-01-01"
    model = dl.DialogModel.from_config(config(starTool=lv.STAR_TOOL_STARNET), all_found(), store=store)
    assert not model.starnet_accepted()
    with pytest.raises(ValueError):
        model.accept_starnet("2020-01-01")


def test_the_licence_text_is_read_from_beside_the_starnet_program(tmp_path: Path) -> None:
    binary = tmp_path / "starnet2"
    binary.write_text("", encoding="utf-8")
    (tmp_path / "LICENSE.txt").write_text("the licence text", encoding="utf-8")
    model = dl.DialogModel.from_config(config(starTool=lv.STAR_TOOL_STARNET),
                                       {"starnet2": found("starnet2", str(binary))})
    assert model.starnet_licence_text() == "the licence text"


# ---- locate and rescan ---------------------------------------------------------------------------------------------


def test_locate_success_uses_and_remembers_the_tool(tmp_path: Path) -> None:
    binary = tmp_path / "prism"
    binary.write_text("", encoding="utf-8")
    store = FakeStore()
    resolver = FakeResolver(results={"prism_cli": Found("prism_cli", str(binary), "configured", "2.1")})
    model = dl.DialogModel.from_config(config(), {}, store=store, resolver=resolver)
    ok, message = model.locate("prism_cli", str(binary))
    assert ok is True
    assert str(binary) in message
    assert resolver.calls == [("prism_cli", str(binary))]
    assert store.records["prism_cli"][0] == str(binary)
    assert lv.NR_TOOL_PRISM in noise_ids(model)
    row = [r for r in model.tool_rows() if r.tool_id == "prism_cli"][0]
    assert row.path == str(binary)


def test_locate_failure_names_the_file_and_the_reason(tmp_path: Path) -> None:
    binary = tmp_path / "prism"
    binary.write_text("", encoding="utf-8")
    store = FakeStore()
    resolver = FakeResolver(results={}, reasons={"prism_cli": "it printed no version"})
    model = dl.DialogModel.from_config(config(), {}, store=store, resolver=resolver)
    ok, message = model.locate("prism_cli", str(binary))
    assert ok is False
    assert message == f"{binary} is not SyQon Prism CLI: it printed no version"
    assert store.records == {}
    assert lv.NR_TOOL_PRISM not in noise_ids(model)


def test_locate_rejects_a_different_tool_found_instead(tmp_path: Path) -> None:
    binary = tmp_path / "prism"
    binary.write_text("", encoding="utf-8")
    resolver = FakeResolver(results={"prism_cli": Found("prism_cli", "/opt/other", "known")})
    model = dl.DialogModel.from_config(config(), {}, resolver=resolver)
    ok, _message = model.locate("prism_cli", str(binary))
    assert ok is False


def test_locate_with_no_path_asks_for_one() -> None:
    model = dl.DialogModel.from_config(config(), {}, resolver=FakeResolver())
    assert model.locate("prism_cli", "") == (False, "Choose the program's file.")
    with pytest.raises(KeyError):
        model.locate("no-such-tool", "/x")


def test_rescan_finds_tools_again() -> None:
    resolver = FakeResolver(results={"graxpert-ai": found("graxpert-ai")})
    model = dl.DialogModel.from_config(config(), {}, resolver=resolver)
    assert model.rescan() == ["graxpert-ai"]
    assert model.visible_groups() == [dl.GROUP_GRADIENT, dl.GROUP_NOISE]


def test_tool_rows_show_not_found_with_the_reason() -> None:
    model = dl.DialogModel.from_config(config(), {}, resolver=FakeResolver(reasons={"rcastro": "not licensed"}))
    rows = {r.tool_id: r for r in model.tool_rows()}
    assert [r.tool_id for r in model.tool_rows()] == list(dl.TOOL_LABELS)
    assert rows["rcastro"].path is None
    assert rows["rcastro"].status == "not found (not licensed)"
    assert rows["starnet2"].status == "not found"


OLD_STARNET = ("StarNet2 2.5.2 is too old: Loom needs 2.6.2 or newer (older builds cannot read FITS)")


def test_an_old_starnet_shows_its_warning_in_the_tools_row_and_is_not_offered() -> None:
    results = {t: found(t) for t in dl.TOOL_LABELS if t != "starnet2"}
    model = dl.DialogModel.from_config(config(starTool="none"), {k: v for k, v in results.items()},
                                       resolver=FakeResolver(results=results, reasons={"starnet2": OLD_STARNET}))
    row = {r.tool_id: r for r in model.tool_rows()}["starnet2"]
    assert row.path is None and row.status == f"not found ({OLD_STARNET})"
    assert lv.STAR_TOOL_STARNET not in [v for v, _ in model.choices(dl.GROUP_STAR)]


def test_a_saved_starnet_choice_falls_back_when_starnet_is_too_old() -> None:
    results = {t: found(t) for t in dl.TOOL_LABELS if t != "starnet2"}
    model = dl.DialogModel.from_config(config(starTool=lv.STAR_TOOL_STARNET), results,
                                       resolver=FakeResolver(results=results, reasons={"starnet2": OLD_STARNET}))
    assert model.get("starTool") == "none"
    assert "StarNet2 is no longer available; set to none" in model.take_notices()


# ---- masters ------------------------------------------------------------------------------------------------------


def _entry(channel: str, path: str, **extra: Any) -> dict[str, Any]:
    base = {"path": path, "name": Path(path).name, "filter": "FILTER", "instrume": "cam", "channel": channel,
            "width": 2000, "height": 1000, "drizzle": "x1", "autocrop": False, "mtime": 1_700_000_000.0}
    base.update(extra)
    return base


def test_scan_masters_reports_a_non_folder_and_chooses_nothing() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    assert model.scan_masters("/no/such", scanner=lambda folder: None) is False
    assert model.path("L") == ""


def test_saved_master_paths_fill_the_list_when_the_dialog_opens(tmp_path) -> None:
    for name in ("L.fits", "R.fits"):
        (tmp_path / name).write_bytes(b"")
    cfg = config(paths={"L": str(tmp_path / "L.fits"), "R": str(tmp_path / "R.fits"), "O": "/gone/O.fits"})
    model = dl.DialogModel.from_config(cfg, all_found())
    assert model.master_rows() == []
    assert model.restore_masters(scanner=lambda folder: {"confirmed": {"L": _entry("L", str(tmp_path / "L.fits"))}}) is True
    rows = model.master_rows()
    assert [r["channel"] for r in rows] == ["L", "R"]  # the missing O file gets no row
    assert model.path("O") == "/gone/O.fits"           # and the saved path itself is untouched
    assert model.restore_masters() is False             # an existing list is not replaced


def test_a_scan_uses_the_folder_masters_at_once_and_clears_the_rest() -> None:
    result = {"confirmed": {"L": _entry("L", "/m/L.fits"), "R": _entry("R", "/m/R.fits")},
              "corrected": [], "skipped": 0, "total": 2}
    model = dl.DialogModel.from_config(config(), all_found())
    model.set_path("O", "/old/O.fits")
    assert model.scan_masters("/m", scanner=lambda folder: result) is True
    assert (model.path("L"), model.path("R"), model.path("O")) == ("/m/L.fits", "/m/R.fits", "")


def test_a_failed_scan_keeps_the_selection() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    model.set_path("O", "/old/O.fits")
    assert model.scan_masters("/no/such", scanner=lambda folder: None) is False
    assert model.path("O") == "/old/O.fits"


def test_master_rows_have_loom_columns_and_quality_cells() -> None:
    result = {"confirmed": {"L": _entry("L", "/m/L.fits", drizzle="x2")}, "corrected": [], "skipped": 0, "total": 1}
    model = dl.DialogModel.from_config(config(), all_found())
    model.scan_masters("/m", scanner=lambda folder: result)
    quality = {"/m/L.fits": {"fwhm": 2.5, "eccentricity": 0.4567, "noise": 0.00123, "stars": 812.0}}
    rows = model.master_rows(quality)
    assert list(rows[0]) == ["channel", "path", *dl.MASTER_COLUMNS]
    row = rows[0]
    assert row["Filter"] == "FILTER"
    assert row["Size"] == "2000 x 1000"
    assert row["Drizzle"] == "x2"
    assert row["Source"] == "L.fits"
    assert row["FWHM"] == "2.50"
    assert row["Ecc"] == "0.46"
    assert row["Noise"] == "0.00123"
    assert row["Stars"] == "812"
    assert row["Created"][:4] == "2023"


def test_master_rows_leave_unmeasured_cells_empty() -> None:
    result = {"confirmed": {"L": _entry("L", "/m/L.fits")}, "corrected": [], "skipped": 0, "total": 1}
    model = dl.DialogModel.from_config(config(), all_found())
    model.scan_masters("/m", scanner=lambda folder: result)
    row = model.master_rows()[0]
    assert (row["FWHM"], row["Ecc"], row["Noise"], row["Stars"]) == ("", "", "", "")


def test_master_rows_are_empty_before_a_scan() -> None:
    assert dl.DialogModel.from_config(config(), all_found()).master_rows() == []


def test_a_real_empty_folder_scans_to_nothing(tmp_path: Path) -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    assert model.scan_masters(str(tmp_path)) is True
    assert model.scanned() is not None and model.scanned()["total"] == 0


# ---- cache ----------------------------------------------------------------------------------------------------------


def test_the_cache_folder_defaults_to_the_per_user_folder() -> None:
    model = dl.DialogModel.from_config(config(), all_found(), platform="linux", env={}, home="/srv/cachetest")
    assert model.cache_folder() == "/srv/cachetest/.local/share/loomsiril/cache"
    model = dl.DialogModel.from_config(config(cacheDir="/data/cache"), all_found(), platform="linux", env={},
                                       home="/srv/cachetest")
    assert model.cache_folder() == "/data/cache"


def test_clear_cache_removes_nothing_from_an_empty_folder(tmp_path: Path) -> None:
    model = dl.DialogModel.from_config(config(cacheDir=str(tmp_path / "cache")), all_found())
    assert model.cache_bytes() == 0
    assert model.clear_cache() == 0


# ---- the run request is a plain dict the orchestrator can save --------------------------------------------------


def test_the_run_config_never_carries_one_run_only_keys() -> None:
    model = dl.DialogModel.from_config(config(ignoreCache=True, validateOnly=True), all_found())
    model.set_output_dir("/work/out")
    out = model.run_request().config
    assert out["ignoreCache"] is True and out["validateOnly"] is True
    assert out["outputDir"] == "/work/out"


# ---- progress model -------------------------------------------------------------------------------------------------


def test_progress_messages_update_stage_and_percent() -> None:
    messages: queue.Queue[Any] = queue.Queue()
    progress = dl.ProgressModel(messages, threading.Event())
    assert progress.label() == "starting...  0%"
    messages.put((dl.ProgressModel.STAGE, "noise reduction: SyQon Prism"))
    messages.put((dl.ProgressModel.PERCENT, 42.4))
    assert progress.pump() == 2
    assert progress.stage == "noise reduction: SyQon Prism"
    assert progress.percent == 42.4
    assert progress.label() == "noise reduction: SyQon Prism  42%"


def test_progress_percent_is_clamped_and_bad_messages_are_skipped() -> None:
    messages: queue.Queue[Any] = queue.Queue()
    progress = dl.ProgressModel(messages, threading.Event())
    messages.put((dl.ProgressModel.PERCENT, 250))
    messages.put(("nonsense",))
    messages.put((dl.ProgressModel.PERCENT, "not a number"))
    progress.pump()
    assert progress.percent == 100.0


def test_progress_done_shows_the_finish_text() -> None:
    messages: queue.Queue[Any] = queue.Queue()
    progress = dl.ProgressModel(messages, threading.Event())
    messages.put((dl.ProgressModel.DONE, "finished: 3 plates written"))
    progress.pump()
    assert progress.done is True
    assert progress.label() == "finished: 3 plates written"


def test_cancel_sets_the_event_only_on_yes() -> None:
    event = threading.Event()
    progress = dl.ProgressModel(queue.Queue(), event)
    assert progress.confirm_cancel(False) is False
    assert not event.is_set()
    assert progress.cancel_requested is False
    assert progress.confirm_cancel(True) is True
    assert event.is_set()
    assert progress.stage == dl.ProgressModel.CANCELLING
    assert progress.confirm_cancel(True) is True  # a second Yes changes nothing


def test_cancel_question_defaults_to_no_in_the_text() -> None:
    assert dl.ProgressModel.CANCEL_QUESTION == "Stop this run at the next checkpoint?"
    assert "Stages already finished stay in the cache" in dl.ProgressModel.CANCEL_DETAIL


def test_progress_works_across_a_worker_thread() -> None:
    messages: queue.Queue[Any] = queue.Queue()
    progress = dl.ProgressModel(messages, threading.Event())

    def worker() -> None:
        for step in range(1, 4):
            messages.put((dl.ProgressModel.PERCENT, step * 25))
        messages.put((dl.ProgressModel.DONE, "done"))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    progress.pump()
    assert progress.percent == 75 and progress.done


# ---- module hygiene ---------------------------------------------------------------------------------------------------


def test_importing_the_module_loads_neither_tkinter_nor_sirilpy() -> None:
    code = ("import sys\nimport loomsiril.loom.dialog\n"
            "bad = [m for m in ('sirilpy', 'tkinter') if m in sys.modules]\nassert not bad, bad\n")
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120)


def test_the_dialog_file_has_no_tkinter_import_at_module_level() -> None:
    source = Path(dl.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        if line.startswith(("import tkinter", "from tkinter", "import sirilpy", "from sirilpy")):
            raise AssertionError(line)


# ---- SPCC pickers, numeric options, per-run flags, StarNet precedence ----------------------------------------------


SPCC_NAMES = {
    "monosensor": ["IMX571", "Generic mono sensor"],
    "redfilter": ["Red A", "Red B"],
    "greenfilter": ["Green A"],
    "bluefilter": ["Blue A"],
}


def spcc(kind: str) -> list[str]:
    return list(SPCC_NAMES.get(kind, []))


def test_sensor_choices_come_from_the_monosensor_kind() -> None:
    model = dl.DialogModel.from_config(config(), all_found(), spcc_names=spcc)
    assert model.sensor_choices() == ["IMX571", "Generic mono sensor"]
    assert dl.DialogModel.from_config(config(), all_found()).sensor_choices() == []


def test_sensor_is_auto_when_blank_and_follows_the_scanned_camera() -> None:
    instrume = "ZWO ASI2600MM Pro"
    result = {"confirmed": {"L": _entry("L", "/m/L.fits", instrume=instrume)}, "corrected": [], "skipped": 0,
              "total": 1}
    model = dl.DialogModel.from_config(config(), all_found(), spcc_names=spcc)
    model.scan_masters("/m", scanner=lambda folder: result)
    assert model.sensor() == ""
    assert model.effective_sensor() == ch.spcc_sensor_for(instrume)
    model.set_sensor("IMX571")
    assert model.to_config()["spccSensor"] == "IMX571"
    assert model.effective_sensor() == "IMX571"
    model.set_sensor("")
    assert model.to_config()["spccSensor"] == ""


def test_a_sensor_the_database_does_not_hold_is_refused() -> None:
    model = dl.DialogModel.from_config(config(), all_found(), spcc_names=spcc)
    with pytest.raises(ValueError):
        model.set_sensor("Not A Sensor")


def test_a_saved_sensor_not_in_the_database_becomes_auto_with_a_notice() -> None:
    model = dl.DialogModel.from_config(config(spccSensor="Gone Sensor"), all_found(), spcc_names=spcc)
    assert model.sensor() == ""
    assert model.take_notices() == ["Gone Sensor is not in Siril's SPCC database; set to auto"]


def test_a_saved_filter_not_in_the_database_is_cleared_with_a_notice() -> None:
    model = dl.DialogModel.from_config(config(filters={"R": "Red A", "G": "Old green"}), all_found(),
                                       spcc_names=spcc)
    assert model.filter_name("R") == "Red A"
    assert model.filter_name("G") == ""
    assert model.to_config()["filters"] == {"R": "Red A"}
    assert model.take_notices() == ["Old green is not in Siril's SPCC database; filter G cleared"]


def test_filter_choices_are_the_names_for_each_channel_kind() -> None:
    model = dl.DialogModel.from_config(config(), all_found(), spcc_names=spcc)
    assert model.filter_choices("R") == ["Red A", "Red B"]
    assert model.filter_choices("G") == ["Green A"]
    assert model.filter_choices("B") == ["Blue A"]


def test_smoothing_is_kept_between_zero_and_one() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    model.set_smoothing("0.25")
    assert model.to_config()["smoothing"] == 0.25
    for bad in (1.5, -0.1, "abc", None):
        with pytest.raises(ValueError):
            model.set_smoothing(bad)
    assert model.get("smoothing") == 0.25


def test_bandwidth_is_kept_between_half_a_nanometre_and_thirty() -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    model.set_bandwidth(4)
    assert model.to_config()["narrowbandBandwidth"] == 4.0
    for bad in (0.4, 30.5, "x"):
        with pytest.raises(ValueError):
            model.set_bandwidth(bad)


def test_validate_only_is_a_model_field_and_not_persisted_by_config_save(tmp_path: Path) -> None:
    model = dl.DialogModel.from_config(config(), all_found())
    model.set_field("validateOnly", True)
    assert model.get("validateOnly") is True
    assert model.to_config()["validateOnly"] is True
    saved = cfg.save(model.to_config(), tmp_path)
    assert "validateOnly" not in Path(saved).read_text(encoding="utf-8")


def test_ignore_cache_is_off_and_disabled_when_the_cache_is_off() -> None:
    model = dl.DialogModel.from_config(config(useCache=False, ignoreCache=True), all_found())
    assert model.get("ignoreCache") is False
    assert model.enabled("ignoreCache") is False
    model.set_field("useCache", True)
    assert model.enabled("ignoreCache") is True
    model.set_field("ignoreCache", True)
    assert model.get("ignoreCache") is True
    model.set_field("useCache", False)
    assert model.get("ignoreCache") is False


def test_a_current_starnet_flag_in_the_store_beats_a_stale_config_value() -> None:
    store = FakeStore()
    store.flags[dl.STARNET_FLAG] = starnet_licence.STARNET_LICENCE_REV
    model = dl.DialogModel.from_config(config(starTool=lv.STAR_TOOL_STARNET, **{dl.STARNET_FLAG: "2020-01-01"}),
                                       all_found(), store=store)
    assert model.starnet_accepted()
    assert dl.STARNET_FLAG not in model.to_config()


def test_a_stale_store_flag_does_not_hide_a_current_config_acceptance() -> None:
    store = FakeStore()
    store.flags[dl.STARNET_FLAG] = "2020-01-01"
    model = dl.DialogModel.from_config(
        config(starTool=lv.STAR_TOOL_STARNET, **{dl.STARNET_FLAG: starnet_licence.STARNET_LICENCE_REV}),
        all_found(), store=store)
    assert model.starnet_accepted()


def test_a_stale_store_flag_is_asked_again_even_with_no_config_value() -> None:
    store = FakeStore()
    store.flags[dl.STARNET_FLAG] = "2020-01-01"
    model = dl.DialogModel.from_config(config(starTool=lv.STAR_TOOL_STARNET, **{dl.STARNET_FLAG: "2020-01-01"}),
                                       all_found(), store=store)
    assert not model.starnet_accepted()


# ---- views (need a display: skipped without one) ------------------------------------------------------------------


@pytest.fixture
def tk_root():
    try:
        import tkinter as tk

        root = tk.Tk()
    except Exception as exc:  # no tkinter, or no display (TclError)
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    yield root
    root.destroy()


def test_the_views_build_and_close(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    assert view.result is None
    view.top.destroy()
    progress = dl.ProgressWindow(tk_root, dl.ProgressModel(queue.Queue(), threading.Event()))
    progress.top.destroy()


def test_a_typed_output_folder_enables_run(tk_root: Any, tmp_path: Path) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    assert not view._run_button.instate(["!disabled"])
    var, _read = view._texts["output"]
    var.set(str(tmp_path / "out"))
    assert model.output_dir() == str(tmp_path / "out")
    assert view._run_button.instate(["!disabled"])
    assert view._reasons.get() == ""
    view.top.destroy()


def test_a_typed_master_path_is_written_at_once(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    var, _read = view._texts["path:L"]
    var.set("/m/L.fits")
    assert model.path("L") == "/m/L.fits"
    view.top.destroy()


def test_spcc_pickers_list_the_database_names(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver(), spcc_names=spcc)
    view = dl.LoomDialog(tk_root, model)
    for key in ("filter:R", "filter:G", "filter:B", "spccSensor"):
        assert key in view._combos, key
    combo, pairs, _get = view._combos["filter:R"]
    assert [v for v, _t in pairs()] == ["", "Red A", "Red B"]
    combo_s, pairs_s, _get_s = view._combos["spccSensor"]
    assert [v for v, _t in pairs_s()][1:] == ["IMX571", "Generic mono sensor"]
    model.set_filter("R", "Red B")
    view._sync()
    assert combo.get() == "Red B"
    view.top.destroy()


def test_a_bad_smoothing_is_held_and_named_not_written(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    var, _read = view._texts["smoothing"]
    var.set("2")
    assert model.get("smoothing") == 0.5
    assert "between 0 and 1" in view._error.get()
    assert var.get() == "2"
    var.set("0.75")
    assert model.get("smoothing") == 0.75
    assert view._error.get() == ""
    view.top.destroy()


def test_validate_only_and_ignore_cache_have_widgets(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(useCache=False), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    assert "validateOnly" in view._checks and "ignoreCache" in view._checks
    assert not view._checks["ignoreCache"][1].instate(["!disabled"])
    view._set("useCache", True)
    assert view._checks["ignoreCache"][1].instate(["!disabled"])
    view.top.destroy()


def _wait_idle(root: Any, view: Any, seconds: float = 10.0) -> None:
    """Runs the Tk event loop until the view's background work (rescan, scan, preflight) has been handed back."""
    deadline = time.monotonic() + seconds
    while view._busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    root.update()


def test_rescan_notices_are_shown(tk_root: Any) -> None:
    resolver = FakeResolver(results={t: found(t) for t in dl.TOOL_LABELS})
    model = dl.DialogModel.from_config(config(noiseTool=lv.NR_TOOL_NXT), all_found(), resolver=resolver)
    view = dl.LoomDialog(tk_root, model)
    resolver.results["rcastro"] = None
    view._rescan()
    _wait_idle(tk_root, view)
    assert "NoiseXTerminator is no longer available; set to none" in view._notice.get()
    view.top.destroy()


def test_closing_a_finished_progress_window_asks_nothing(tk_root: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter.messagebox as mb

    asked: list[Any] = []
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: asked.append(a) or False)
    messages: queue.Queue[Any] = queue.Queue()
    progress_model = dl.ProgressModel(messages, threading.Event())
    window = dl.ProgressWindow(tk_root, progress_model)
    messages.put((dl.ProgressModel.DONE, "finished"))
    progress_model.pump()
    window._cancel()
    assert asked == []
    assert not progress_model.cancel_requested


def test_cancel_question_is_asked_while_running_and_no_keeps_running(tk_root: Any,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter.messagebox as mb

    asked: list[Any] = []
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: asked.append(k) or False)
    event = threading.Event()
    progress_model = dl.ProgressModel(queue.Queue(), event)
    window = dl.ProgressWindow(tk_root, progress_model)
    window._cancel()
    assert asked and asked[0].get("default") == "no"
    assert not event.is_set()
    window.top.destroy()


def _type(var: Any, text: str) -> None:
    """Types ``text`` one character at a time, each appended to what the entry holds (as a keystroke does)."""
    for character in text:
        var.set(var.get() + character)


def test_typing_a_number_one_character_at_a_time_keeps_the_text(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    var, _read = view._texts["narrowbandBandwidth"]
    var.set("")
    _type(var, "12.5")
    assert var.get() == "12.5"  # the typed text is not rewritten between keystrokes
    assert var.get() == "12.5"
    assert model.get("narrowbandBandwidth") == 12.5
    view.top.destroy()


def test_typing_smoothing_one_character_at_a_time_keeps_the_text(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    var, _read = view._texts["smoothing"]
    var.set("")
    _type(var, "0.7")
    assert var.get() == "0.7"
    assert model.get("smoothing") == 0.7
    assert view._error.get() == ""
    view.top.destroy()


def test_a_pasted_number_is_written_and_kept(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    var, _read = view._texts["smoothing"]
    var.set("0.35")
    assert var.get() == "0.35"
    assert model.get("smoothing") == 0.35
    view.top.destroy()


def test_out_of_range_numbers_typed_are_refused_and_held(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    smoothing, _ = view._texts["smoothing"]
    smoothing.set("")
    _type(smoothing, "2")
    assert smoothing.get() == "2"
    assert model.get("smoothing") == 0.5
    assert "between 0 and 1" in view._error.get()
    bandwidth, _ = view._texts["narrowbandBandwidth"]
    bandwidth.set("")
    _type(bandwidth, "40")
    assert bandwidth.get() == "40"
    assert model.get("narrowbandBandwidth") == 4.0  # "4" was in range on the way; "40" is held, not written
    assert "between 0.5 and 30" in view._error.get()
    bandwidth.set("")
    _type(bandwidth, "20")
    assert model.get("narrowbandBandwidth") == 20.0
    assert "between 0 and 1" in view._error.get()  # the held smoothing "2" is still named
    smoothing.set("0.5")
    assert view._error.get() == ""
    view.top.destroy()


def test_a_model_change_from_outside_shows_in_the_entry(tk_root: Any, tmp_path: Path) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    model.set_smoothing(0.25)
    view._sync()
    smoothing, _ = view._texts["smoothing"]
    assert smoothing.get() == "0.25"
    model.set_field("projectName", "M42")
    view._sync()
    project, _ = view._texts["projectName"]
    assert project.get() == "M42"
    model.set_output_dir(str(tmp_path / "out"))
    view._sync()
    output, _ = view._texts["output"]
    assert output.get() == str(tmp_path / "out")
    view.top.destroy()


def test_an_entry_holding_typed_invalid_text_is_not_overwritten(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    smoothing, _ = view._texts["smoothing"]
    smoothing.set("")
    _type(smoothing, "2")
    assert "between 0 and 1" in view._error.get()
    view._sync()
    view._sync()
    assert smoothing.get() == "2"
    assert model.get("smoothing") == 0.5
    # a change from outside to the same option does not overwrite the typed text either
    model.set_smoothing(0.3)
    view._sync()
    assert smoothing.get() == "2"
    assert "between 0 and 1" in view._error.get()
    assert model.get("smoothing") == 0.3
    view.top.destroy()


def test_a_second_outside_change_is_shown_after_the_first(tk_root: Any) -> None:
    model = dl.DialogModel.from_config(config(), all_found(), resolver=FakeResolver())
    view = dl.LoomDialog(tk_root, model)
    smoothing, _ = view._texts["smoothing"]
    model.set_smoothing(0.25)
    view._sync()
    assert smoothing.get() == "0.25"
    model.set_smoothing(0.5)
    view._sync()
    assert smoothing.get() == "0.5"
    view.top.destroy()


def test_wheel_units_per_platform() -> None:
    from loomsiril.loom.dialog import wheel_units

    assert wheel_units(1, "darwin") == -1 and wheel_units(-3, "darwin") == 3   # macOS: small deltas, as they come
    assert wheel_units(120, "win32") == -1 and wheel_units(-240, "win32") == 2   # Windows: 120 per notch
    assert wheel_units(30, "win32") == -1   # a fine-grained wheel still moves
    assert wheel_units(0, "darwin") == 0


def test_scanned_masters_are_measured_through_the_measure_function() -> None:
    result = {"confirmed": {"L": _entry("L", "/m/L.fits"), "R": _entry("R", "/m/R.fits")},
              "corrected": [], "skipped": 0, "total": 2}
    calls = []

    def measure(paths, cache_dir):
        calls.append((list(paths), cache_dir))
        return {"/m/L.fits": {"fwhm": 3.25, "eccentricity": 0.41, "noise": 0.0012, "stars": 950}}

    model = dl.DialogModel.from_config(config(cacheDir="/c"), all_found(), measure=measure)
    model.scan_masters("/m", scanner=lambda folder: result)
    assert model.unmeasured_masters() == ["/m/L.fits", "/m/R.fits"]
    model.set_measuring(True)
    assert model.master_rows()[0]["FWHM"] == "..."   # pending while the worker runs
    model.add_quality(model.measure_masters(model.unmeasured_masters()))
    model.set_measuring(False)
    assert calls == [(["/m/L.fits", "/m/R.fits"], "/c")]
    rows = {r["channel"]: r for r in model.master_rows()}
    assert (rows["L"]["FWHM"], rows["L"]["Ecc"], rows["L"]["Stars"]) == ("3.25", "0.41", "950")
    assert rows["R"]["FWHM"] == ""   # Siril could not measure it: an empty cell, not "..."
    assert model.unmeasured_masters() == ["/m/R.fits"]


def test_without_a_measure_function_nothing_is_measured() -> None:
    result = {"confirmed": {"L": _entry("L", "/m/L.fits")}, "corrected": [], "skipped": 0, "total": 1}
    model = dl.DialogModel.from_config(config(), all_found())
    model.scan_masters("/m", scanner=lambda folder: result)
    assert model.unmeasured_masters() == []
    assert model.measure_masters(["/m/L.fits"]) == {}


def test_asynchronous_preflight_never_runs_on_the_calling_thread_and_is_stored() -> None:
    calls: list[Any] = []
    model = dl.DialogModel.from_config(config(), all_found(), preflight=lambda request: calls.append(request) or ["a problem"])
    model.async_preflight = True
    assert model.preflight_stale() is True
    assert dl.CHECKING_TEXT in model.run_reasons() and calls == []     # nothing was run just to answer the question
    key, job = model.preflight_job()
    model.store_preflight(key, job())
    assert len(calls) == 1 and model.preflight_stale() is False
    assert "a problem" in model.run_reasons() and len(calls) == 1       # answered from the stored result
    model.set_field("reduceHalos", True)                                  # an option changed: stale again
    assert model.preflight_stale() is True


def test_saved_filters_survive_an_unreadable_spcc_database_and_names_are_read_once() -> None:
    reads: list[str] = []

    def names(kind: str) -> list[str]:
        reads.append(kind)
        return []

    model = dl.DialogModel.from_config(config(filters={"R": "Baader R", "G": "Baader G"}), all_found(), spcc_names=names)
    assert model.filter_name("R") == "Baader R" and model.filter_name("G") == "Baader G"
    before = len(reads)
    model.filter_choices("R"), model.filter_choices("R")
    assert len(reads) == before                                            # cached
    other = dl.DialogModel.from_config(config(filters={"R": "Gone"}), all_found(), spcc_names=lambda kind: ["Baader R"])
    assert other.filter_name("R") == ""                                    # a database that lacks it still clears it
