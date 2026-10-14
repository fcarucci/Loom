"""Preflight checks and the validate-only report (loomsiril/loom/preflight.py).

The expected messages are Loom's wording recorded from its JavaScript (lib/Pipeline.js:25-94 and :167-203,
lib/Util.js:603-669, lib/StepsSyqon.js:1662-1680, Loom.js:58-71). Every test uses an in-memory file system and fake
tools, so nothing touches the disk, the network or a tool.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from loomsiril.loom import levels as lv
from loomsiril.loom import preflight as pf
from loomsiril.loom.tools.resolver import Found

L = "/data/L.fits"
R = "/data/R.fits"
G = "/data/G.fits"
B = "/data/B.fits"
H = "/data/H.fits"
S = "/data/S.fits"
O = "/data/O.fits"
MISSING = "/data/missing.fits"
SOLVED_KEYWORDS = ("CTYPE1", "CRVAL1")
SPCC = {"redfilter": ["Red"], "greenfilter": ["Green"], "bluefilter": ["Blue"], "monosensor": []}


def _filter_of(path: str) -> str | None:
    return {L: "L", R: "Red", G: "Green", B: "Blue", H: "Ha"}.get(path)


HINTS = (("RA", "10.0"), ("DEC", "20.0"), ("FOCALLEN", "500"), ("XPIXSZ", "3.8"))


def header(path: str, *, filter_value: str | None = "default", solved: bool = False,
           hints: Sequence[tuple[str, str]] = HINTS) -> dict[str, Any]:
    """A synthetic FITS header: FILTER, then the plate-solving hints in the header (not in the config)."""
    keywords: list[dict[str, Any]] = []
    value = _filter_of(path) if filter_value == "default" else filter_value
    if value is not None:
        keywords.append({"name": "FILTER", "value": f"'{value}'"})
    keywords.extend({"name": n, "value": v} for n, v in hints)
    if solved:
        keywords.extend({"name": n, "value": "1"} for n in SOLVED_KEYWORDS)
    return {"format": "fits", "keywords": keywords, "width": 10, "height": 10}


def base_config(**over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "paths": {"L": L, "R": R, "G": G, "B": B, "H": "", "S": "", "O": ""},
        "views": {},
        "gradientTool": "GraXpert",
        "sharpenTool": "none",
        "starTool": "none",
        "noiseTool": "none",
        "palettes": [],
        "filters": {"R": "Red", "G": "Green", "B": "Blue"},
        "outputDir": "/out",
    }
    cfg.update(over)
    return cfg


def found_for(*tools: str) -> dict[str, Any]:
    return {t: Found(tool_id=t, path=f"/opt/{t}", source="known") for t in tools}


def make(config: Mapping[str, Any], *, present: Sequence[str] = (), missing_files: Sequence[str] = (),
         unreadable: Sequence[str] = (), headers: Mapping[str, dict[str, Any]] | None = None,
         rejection: Mapping[str, str] | None = None, headless: bool = True, **kw: Any) -> pf.Preflight:
    files = {p for p in (L, R, G, B, H, S, O, MISSING) if p not in missing_files}
    hdrs = dict(headers or {})

    def read_header(path: str) -> dict[str, Any]:
        return hdrs.get(path, header(path))

    base: dict[str, Any] = {
        "config": config,
        "read_header": read_header,
        "exists": lambda p: p in files,
        "readable": lambda p: p not in unreadable,
        "found": found_for("graxpert-ai", *present),
        "rejection": dict(rejection or {}),
        "graxpert_readiness": lambda: [],
        "spcc_database": "/db",
        "spcc_names": lambda kind: SPCC.get(kind, []),
        "can_write_dir": lambda d: True,
        "headless": headless,
    }
    base.update(kw)
    return pf.Preflight(**base)


# ---- clean configuration -------------------------------------------------------------------------------------------


def test_clean_configuration_has_no_problems() -> None:
    assert pf.preflight(make(base_config())) == []


def test_clean_configuration_dialog_mode_has_no_problems() -> None:
    assert pf.preflight(make(base_config(), headless=False)) == []


# ---- check 1: selection rules -------------------------------------------------------------------------------------


def test_incomplete_rgb_set_is_reported() -> None:
    cfg = base_config(paths={"L": "", "R": R, "G": "", "B": "", "H": "", "S": "", "O": ""}, filters={})
    assert pf.preflight(make(cfg, headless=False)) == [
        "Incomplete RGB set: missing G, B. Supply all three or none.",
    ]


def test_nothing_to_do_is_reported() -> None:
    cfg = base_config(paths={"L": "", "R": "", "G": "", "B": "", "H": "", "S": "", "O": ""})
    assert pf.preflight(make(cfg)) == ["Nothing to do: supply R, G and B, or at least one of H, S, O"]


def test_same_file_for_two_channels_is_reported() -> None:
    cfg = base_config(paths={"L": L, "R": R, "G": G, "B": B, "H": L, "S": "", "O": ""})
    assert pf.preflight(make(cfg)) == ["Same file selected for L and H: " + L]


# ---- check 2: files and FILTER keywords ----------------------------------------------------------------------------


def test_missing_file_is_reported() -> None:
    cfg = base_config(paths={"L": MISSING, "R": R, "G": G, "B": B, "H": "", "S": "", "O": ""})
    assert pf.preflight(make(cfg, missing_files=[MISSING])) == [f"File not found for L: {MISSING}"]


def test_unreadable_file_is_reported() -> None:
    assert pf.preflight(make(base_config(), unreadable=[R])) == [f"File not readable for R: {R}"]


def test_unreadable_header_is_reported() -> None:
    def broken(path: str) -> dict[str, Any]:
        raise ValueError("not an XISF or FITS file: " + path)

    preflight_inputs = make(base_config())
    preflight_inputs.read_header = broken
    assert pf.preflight(preflight_inputs) == [
        f"Cannot read the header of L: {L} (not an XISF or FITS file: {L})",
        f"Cannot read the header of R: {R} (not an XISF or FITS file: {R})",
        f"Cannot read the header of G: {G} (not an XISF or FITS file: {G})",
        f"Cannot read the header of B: {B} (not an XISF or FITS file: {B})",
    ]


def test_broadband_without_filter_is_reported() -> None:
    hdrs = {G: header(G, filter_value=None)}
    assert pf.preflight(make(base_config(), headers=hdrs)) == [
        f"No FILTER keyword in G: {G} (the script will not guess a filter)",
    ]


def test_narrowband_without_filter_is_not_reported() -> None:
    cfg = base_config(paths={"L": L, "R": R, "G": G, "B": B, "H": H, "S": "", "O": ""})
    hdrs = {H: header(H, filter_value=None)}
    # H is not in the solved set, so the plate-solving hints must be complete (they are, in base_config).
    assert pf.preflight(make(cfg, headers=hdrs)) == []


# ---- check 3: chosen external tools ------------------------------------------------------------------------------


def test_missing_gradient_tool_is_reported_with_rejection() -> None:
    inputs = make(base_config(), present=(), rejection={"graxpert-ai": "version 1.0 needed"})
    inputs.found = {"graxpert-ai": None}
    assert pf.preflight(inputs) == [
        "GraXpert not found (choose another gradient tool to proceed without): version 1.0 needed",
    ]


def test_missing_sharpen_tool_is_reported_without_rejection() -> None:
    cfg = base_config(sharpenTool="BlurXTerminator")
    inputs = make(cfg)
    inputs.found = {"graxpert-ai": Found("graxpert-ai", "/opt/g", "known"), "rcastro": None}
    assert pf.preflight(inputs) == [
        "BlurXTerminator not found (choose another sharpen tool to proceed without)",
    ]


def test_missing_tool_in_dialog_mode_is_not_reported() -> None:
    calls: list[str] = []
    inputs = make(base_config(), headless=False, rejection={"graxpert-ai": "gone"},
                  graxpert_readiness=lambda: calls.append("readiness") or [])
    inputs.found = {"graxpert-ai": None}
    assert pf.preflight(inputs) == []
    assert calls == []


# ---- check 4: GraXpert readiness ---------------------------------------------------------------------------------


def test_graxpert_readiness_problems_are_reported_verbatim() -> None:
    text = "GraXpert model missing in /models: run GraXpert once to download the background-extraction model"
    inputs = make(base_config(), graxpert_readiness=lambda: [text])
    assert pf.preflight(inputs) == [text]


def test_graxpert_readiness_not_asked_when_another_gradient_tool_runs() -> None:
    calls: list[str] = []
    cfg = base_config(gradientTool="none")
    inputs = make(cfg, graxpert_readiness=lambda: calls.append("x") or ["never"])
    assert pf.preflight(inputs) == []
    assert calls == []


# ---- check 5: SyQon Studio entitlement -------------------------------------------------------------------------


def test_studio_refusal_lines_are_reported_verbatim() -> None:
    line = ("SyQon Studio: Deep Gradient (deep-gradient) is not available to your SyQon account (syqon-cli exit 4). "
            "Sign in through SyQon Studio, or choose another gradient removal tool.")
    cfg = base_config(gradientTool="SyQon Studio Deep Gradient")
    inputs = make(cfg, present=("syqon-cli",), studio_entitlement=lambda models: [line])
    assert pf.preflight(inputs) == [line]


def test_studio_is_asked_for_the_models_the_run_uses() -> None:
    asked: list[list[str]] = []
    cfg = base_config(gradientTool="SyQon Studio Deep Gradient")
    inputs = make(cfg, present=("syqon-cli",),
                  studio_entitlement=lambda models: asked.append(list(models)) or [])
    assert pf.preflight(inputs) == []
    assert asked == [["deep-gradient"]]


def test_inconclusive_studio_probe_reports_nothing() -> None:
    # The adapter collects refusals only; an inconclusive probe leaves no line behind (Loom only logs it).
    cfg = base_config(gradientTool="SyQon Studio Deep Gradient")
    inputs = make(cfg, present=("syqon-cli",), studio_entitlement=lambda models: [])
    assert pf.preflight(inputs) == []


def test_prism2_refusal_line_is_passed_through_with_its_tail() -> None:
    cfg = base_config(noiseTool=lv.NR_TOOL_STUDIO2, noiseLevel="medium", stretch=True)
    models = lv.studio_models_for(cfg, True)
    prism2 = [m for m in models if m in ("prism-advanced", "prism-ultra", "prism-max")]
    assert prism2, "the Prism 2.0 ladder must name at least one Prism 2.0 model"
    tail = (" Until a check succeeds, Loom offers SyQon Studio Prism Essential (included) in place of Prism 2.0.")
    lines = [
        f"SyQon Studio: Prism Deep Ultra ({m}) is not available to your SyQon account (syqon-cli exit 4). "
        "Sign in through SyQon Studio, or choose another noise reduction tool." + tail
        for m in prism2
    ]
    inputs = make(cfg, present=("syqon-cli",), studio_entitlement=lambda ms: lines)
    assert pf.preflight(inputs) == lines


def test_studio_models_are_not_probed_without_syqon_cli() -> None:
    calls: list[Sequence[str]] = []
    cfg = base_config(gradientTool="SyQon Studio Deep Gradient")
    inputs = make(cfg, studio_entitlement=lambda models: calls.append(models) or [])
    problems = pf.preflight(inputs)
    assert calls == []
    assert problems == ["SyQon Studio Deep Gradient not found (choose another gradient tool to proceed without)"]


# ---- check 6: StarNet2 licence -------------------------------------------------------------------------------


def test_starnet_licence_missing_headless_wording() -> None:
    cfg = base_config(starTool=lv.STAR_TOOL_STARNET)
    inputs = make(cfg, present=("starnet2",), starnet_revision=None, current_starnet_revision="2026-09-07")
    assert pf.preflight(inputs) == [
        "StarNet2 needs its licence accepted: set starnetLicenceAccepted in the job file",
    ]


def test_starnet_licence_missing_dialog_wording() -> None:
    cfg = base_config(starTool=lv.STAR_TOOL_STARNET)
    inputs = make(cfg, present=("starnet2",), headless=False, starnet_revision="2020-01-01",
                  current_starnet_revision="2026-09-07")
    assert pf.preflight(inputs) == [
        "StarNet2 needs its licence accepted: choose Run StarNet in the dialog and accept it",
    ]


def test_starnet_licence_at_current_revision_passes() -> None:
    cfg = base_config(starTool=lv.STAR_TOOL_STARNET)
    inputs = make(cfg, present=("starnet2",), starnet_revision="2026-09-07",
                  current_starnet_revision="2026-09-07")
    assert pf.preflight(inputs) == []


# ---- check 7: SPCC filters and database --------------------------------------------------------------------------


def test_missing_spcc_filter_headless() -> None:
    cfg = base_config(filters={"G": "Green", "B": "Blue"})
    assert pf.preflight(make(cfg)) == ["No SPCC filter chosen for R: set filters R in the job file"]


def test_missing_spcc_filter_dialog() -> None:
    cfg = base_config(filters={"G": "Green", "B": "Blue"})
    assert pf.preflight(make(cfg, headless=False)) == ["No SPCC filter chosen for R: pick filters in the dialog"]


def test_spcc_filter_not_in_database() -> None:
    cfg = base_config(filters={"R": "Teal", "G": "Green", "B": "Blue"})
    assert pf.preflight(make(cfg)) == [
        "SPCC filter Teal for R is not in Siril's SPCC database: set filters R in the job file",
    ]
    assert pf.preflight(make(cfg, headless=False)) == [
        "SPCC filter Teal for R is not in Siril's SPCC database: pick filters in the dialog",
    ]


def test_missing_spcc_database_with_rgb_is_reported() -> None:
    assert pf.preflight(make(base_config(), spcc_database=None)) == [
        "Siril's SPCC database folder is missing: open Siril's GUI once so it downloads its SPCC database",
    ]


def test_missing_spcc_database_with_palette_is_reported() -> None:
    cfg = base_config(paths={"L": "", "R": "", "G": "", "B": "", "H": H, "S": S, "O": O},
                      palettes=["SHO"], filters={})
    assert pf.preflight(make(cfg, spcc_database=None)) == [
        "Siril's SPCC database folder is missing: open Siril's GUI once so it downloads its SPCC database",
    ]


def test_palette_not_buildable_does_not_demand_the_database() -> None:
    # Only H is supplied, so SHO cannot be built and the run skips it: no database is needed.
    cfg = base_config(paths={"L": "", "R": "", "G": "", "B": "", "H": H, "S": "", "O": ""},
                      palettes=["SHO"], filters={})
    assert pf.preflight(make(cfg, spcc_database=None)) == []


def test_no_spcc_database_needed_without_rgb_or_palette() -> None:
    cfg = base_config(paths={"L": L, "R": "", "G": "", "B": "", "H": H, "S": "", "O": ""}, filters={})
    assert pf.preflight(make(cfg, spcc_database=None)) == []


# ---- check 8: output folder ------------------------------------------------------------------------------------


def test_no_output_folder_headless() -> None:
    assert pf.preflight(make(base_config(outputDir=""))) == ["No output folder: set outputDir"]


def test_no_output_folder_dialog() -> None:
    assert pf.preflight(make(base_config(outputDir=""), headless=False)) == [
        "No output folder: choose one in the dialog",
    ]


def test_unwritable_output_folder_is_reported() -> None:
    inputs = make(base_config(), can_write_dir=lambda d: False)
    assert pf.preflight(inputs) == ["Output folder cannot be created or written: /out"]


# ---- check 9: plate solving --------------------------------------------------------------------------------------


def test_plate_solving_lists_the_missing_hints_per_channel() -> None:
    hdrs = {p: header(p, hints=(("FOCALLEN", "500"),)) for p in (L, R, G, B)}
    assert pf.preflight(make(base_config(), headers=hdrs)) == [
        f"Plate solving not possible for {k}: {p} (no solution in its header; "
        "missing hints in its header: RA/DEC, XPIXSZ)"
        for k, p in (("L", L), ("R", R), ("G", G), ("B", B))
    ]


def test_hints_are_read_from_the_header_not_the_config() -> None:
    cfg = base_config(RA=10.0, DEC=20.0, FOCALLEN=500.0, XPIXSZ=3.8)
    hdrs = {p: header(p, hints=()) for p in (L, R, G, B)}
    problems = pf.preflight(make(cfg, headers=hdrs))
    assert len(problems) == 4
    assert all("missing hints in its header: RA/DEC, FOCALLEN, XPIXSZ" in line for line in problems)


def test_objctra_objctdec_count_as_the_position() -> None:
    hints = (("OBJCTRA", "10 00 00"), ("OBJCTDEC", "+20 00 00"), ("FOCALLEN", "500"), ("XPIXSZ", "3.8"))
    hdrs = {p: header(p, hints=hints) for p in (L, R, G, B)}
    assert pf.preflight(make(base_config(), headers=hdrs)) == []


def test_channel_with_a_solution_needs_no_hints() -> None:
    hdrs = {p: header(p, solved=True, hints=()) for p in (L, R, G, B)}
    assert pf.preflight(make(base_config(), headers=hdrs)) == []


def test_ctype1_without_crval1_is_not_a_solution() -> None:
    hdr = header(L, hints=())
    hdr["keywords"].append({"name": "CTYPE1", "value": "'RA---TAN'"})
    problems = pf.preflight(make(base_config(), headers={L: hdr}))
    assert problems == [
        f"Plate solving not possible for L: {L} (no solution in its header; "
        "missing hints in its header: RA/DEC, FOCALLEN, XPIXSZ)",
    ]


def test_local_astrometry_skips_the_hint_check() -> None:
    hdrs = {p: header(p, hints=()) for p in (L, R, G, B)}
    assert pf.preflight(make(base_config(), headers=hdrs, local_astrometry=True)) == []


def test_six_rules_broken_gives_six_lines_in_check_order() -> None:
    cfg = base_config(
        paths={"L": L, "R": R, "G": G, "B": MISSING, "H": L, "S": "", "O": ""},
        sharpenTool="BlurXTerminator",
        starTool=lv.STAR_TOOL_STARNET,
        filters={"G": "Green", "B": "Blue"},
        outputDir="",
    )
    inputs = make(cfg, present=("starnet2",), missing_files=[MISSING],
                  starnet_revision=None, current_starnet_revision="2026-09-07")
    inputs.found = {"graxpert-ai": Found("graxpert-ai", "/opt/g", "known"), "rcastro": None, "starnet2":
                    Found("starnet2", "/opt/s", "known")}
    assert pf.preflight(inputs) == [
        "Same file selected for L and H: " + L,
        f"File not found for B: {MISSING}",
        "BlurXTerminator not found (choose another sharpen tool to proceed without)",
        "StarNet2 needs its licence accepted: set starnetLicenceAccepted in the job file",
        "No SPCC filter chosen for R: set filters R in the job file",
        "No output folder: set outputDir",
    ]


# ---- required_tools ---------------------------------------------------------------------------------------------


def test_required_tools_for_each_gradient_choice() -> None:
    assert pf.required_tools(base_config(gradientTool="GraXpert")) == ["graxpert-ai"]
    assert pf.required_tools(base_config(gradientTool="SyQon Studio Deep Gradient")) == ["syqon-cli"]
    assert pf.required_tools(base_config(gradientTool="none")) == []
    assert pf.required_tools(base_config(gradientTool="", useGraXpert=True)) == ["graxpert-ai"]


def test_required_tools_for_each_sharpen_choice() -> None:
    expect = {
        "BlurXTerminator": ["rcastro"],
        "SyQon Parallax": ["parallax_cli"],
        "SyQon Studio Parallax": ["syqon-cli"],
        "SyQon Studio Parallax (correct only)": ["syqon-cli"],
        "none": [],
    }
    for choice, tools in expect.items():
        assert pf.required_tools(base_config(gradientTool="none", sharpenTool=choice)) == tools, choice


def test_required_tools_for_each_star_choice() -> None:
    expect = {
        "StarXTerminator": ["rcastro"],
        "StarNet2": ["starnet2"],
        "SyQon Starless": ["SyQonStarless"],
        "SyQon Studio Axiom": ["syqon-cli"],
        "none": [],
    }
    for choice, tools in expect.items():
        assert pf.required_tools(base_config(gradientTool="none", starTool=choice)) == tools, choice


def test_required_tools_for_each_noise_choice() -> None:
    expect = {
        "NoiseXTerminator": ["rcastro"],
        "SyQon Prism": ["prism_cli"],
        "SyQon Studio Prism Essential": ["syqon-cli"],
        "SyQon Studio Prism 2.0": ["syqon-cli"],
        "SyQon Studio Prism": ["syqon-cli"],
        "Siril denoise": [],
        "none": [],
    }
    for choice, tools in expect.items():
        assert pf.required_tools(base_config(gradientTool="none", noiseTool=choice)) == tools, choice


def test_required_tools_are_unique_and_in_dropdown_order() -> None:
    cfg = base_config(gradientTool="GraXpert", sharpenTool="BlurXTerminator",
                      starTool="StarXTerminator", noiseTool="NoiseXTerminator")
    assert pf.required_tools(cfg) == ["graxpert-ai", "rcastro"]


# ---- validate-only report ---------------------------------------------------------------------------------------


def test_validate_only_report_matches_loom_wording() -> None:
    cfg = base_config(paths={"L": L, "R": R, "G": G, "B": B, "H": H, "S": "", "O": ""})
    assert pf.validate_only_report(cfg) == [
        "All checks passed. Nothing executed.",
        f"L <- {L}",
        f"R <- {R}",
        f"G <- {G}",
        f"B <- {B}",
        f"H <- {H}",
    ]


def test_validate_only_report_skips_unset_channels() -> None:
    cfg = base_config(paths={"L": "", "R": R, "G": G, "B": B, "H": "", "S": "", "O": ""})
    assert pf.validate_only_report(cfg) == [
        "All checks passed. Nothing executed.",
        f"R <- {R}",
        f"G <- {G}",
        f"B <- {B}",
    ]


def test_module_does_not_import_siril_or_the_gui() -> None:
    with open(pf.__file__, encoding="utf-8") as fh:
        source = fh.read()
    assert "import sirilpy" not in source and "tkinter" not in source


def test_graxpert_readiness_is_not_asked_when_the_script_is_not_found() -> None:
    # One line for one cause: the not-found line from the tool check, not a second one from readiness.
    calls: list[str] = []
    inputs = make(base_config(), graxpert_readiness=lambda: calls.append("x") or ["script missing"],
                  rejection={"graxpert-ai": "gone"})
    inputs.found = {"graxpert-ai": None}
    assert pf.preflight(inputs) == [
        "GraXpert not found (choose another gradient tool to proceed without): gone",
    ]
    assert calls == []
