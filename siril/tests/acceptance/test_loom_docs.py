"""The Loom documentation: README and Loom/README sections, the parity table and the changelog entry.

The parity table is checked against the modules: every public function or class defined in a loomsiril.loom module
has a row, and every deviation named in the port's list is written out in the Deviations section.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re

import pytest

import loomsiril.loom as loom_pkg

from acc_helpers import REPO

README = REPO / "README.md"
SIRIL_README = REPO / "Loom" / "README.md"
PARITY = REPO / "docs" / "PARITY-LOOM.md"
CHANGELOG = REPO / "CHANGELOG.md"


def _public_callables() -> set[tuple[str, str]]:
    """(module key, name) for each public function or class defined in a loomsiril.loom module."""
    found: set[tuple[str, str]] = set()
    for info in pkgutil.walk_packages(loom_pkg.__path__, "loomsiril.loom."):
        mod = importlib.import_module(info.name)
        key = info.name[len("loomsiril.loom."):]
        for name, obj in vars(mod).items():
            if name.startswith("_") or not (inspect.isfunction(obj) or inspect.isclass(obj)):
                continue
            if obj.__module__ == mod.__name__:
                found.add((key, name))
    return found


_ROW = re.compile(r"^\| `([\w.]+)\.(\w+)` \| (.+?) \| (\S+) \| (.+?) \|$", re.M)


def _parity_table_rows() -> list[tuple[str, str, str, str, str]]:
    """(module key, name, JavaScript column, status, test column) of each row of the function tables.

    The table of Loom functions with no Python function of the same name is not included.
    """
    modules = {key for key, _name in _public_callables()}
    text = PARITY.read_text(encoding="utf-8")
    return [(m.group(1), m.group(2), m.group(3).strip("`"), m.group(4), m.group(5).strip("`"))
            for m in _ROW.finditer(text) if m.group(1) in modules]


def _parity_rows() -> set[tuple[str, str]]:
    return {(key, name) for key, name, *_rest in _parity_table_rows()}


def _section(text: str, heading: str) -> str:
    """The text under a level-2 heading, up to the next level-2 heading."""
    m = re.search(r"^## " + re.escape(heading) + r"\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert m, f"no '## {heading}' section"
    return m.group(1)


# ---- README ----------------------------------------------------------------------------------------------------------


def test_readme_has_a_loom_section() -> None:
    heads = [ln for ln in README.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]
    assert any(re.search(r"\bLoom\b", h) for h in heads), heads


def test_readme_loom_section_covers_the_run_modes_and_tools() -> None:
    text = README.read_text(encoding="utf-8")
    section = _section(text, next(h[3:] for h in text.splitlines() if h.startswith("## ") and "Loom" in h))
    for needle in ("Siril 1.4", "Scripts menu", "siril-cli", "status file", "RC-Astro", "SyQon Studio", "StarNet2",
                   "GraXpert-AI", "subsky", "GHS", "cache"):
        assert needle in section, needle


def test_readme_gaia_paragraph_states_the_wired_behaviour() -> None:
    """The residual check's thresholds, cache location, online source and skip rule, as the code has them."""
    from loomsiril.gaia import default_cache_dir
    from loomsiril.loom import residuals

    text = README.read_text(encoding="utf-8")
    para = re.search(r"^- \*\*Gaia residual check\.\*\*(.*?)(?=^- |^#)", text, re.M | re.S)
    assert para, "no Gaia residual check paragraph"
    body = " ".join(para.group(1).split())
    needles = [
        "Gaia DR3", "median deviation", f"{residuals.RESIDUALS_WARN_PX:g} px", f"{residuals.RESIDUALS_BAD_PX:.1f} px",
        "ok", "poor", "bad", "always runs", "no option to turn it off", "$XDG_CACHE_HOME/loomsiril/gaia", "~/.cache/loomsiril/gaia",
        "macOS and Windows", "VizieR", "only for a field that is not cached", "logs", "one line",
        "rest of the run", "never fails the run", "every solved channel", "already solved", "dialog and headless",
    ]
    missing = [n for n in needles if n not in body]
    assert not missing, missing
    assert "on by default" not in body  # it cannot be turned off
    assert "catalogue data" not in body  # the cache holds the Gaia answers of solved fields, not a catalogue download
    assert default_cache_dir().parts[-2:] == ("loomsiril", "gaia")


def test_readme_lists_scipy_in_the_dependency_lines() -> None:
    text = README.read_text(encoding="utf-8")
    deps = [ln for ln in text.splitlines() if "Dependencies:" in ln]
    assert deps and all("scipy" in ln for ln in deps), deps
    assert "`numpy`, `scipy` and `tifffile`" in text


# ---- Loom/README ----------------------------------------------------------------------------------------------------


def test_siril_readme_has_a_loom_py_section_with_scipy() -> None:
    text = SIRIL_README.read_text(encoding="utf-8")
    heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert any("loom_preprocess.py" in h for h in heads), heads
    section = _section(text, next(h[3:] for h in heads if "loom_preprocess.py" in h))
    for needle in ("Install", "Dependencies", "scipy", "Dialog", "Headless job", "--job", "requires 1.4.0"):
        assert needle in section, needle


# ---- parity table ----------------------------------------------------------------------------------------------------


def test_parity_has_a_row_for_every_public_function_of_the_loom_modules() -> None:
    expected = _public_callables()
    assert len(expected) > 100, "the inventory found too few callables; the walk is broken"
    missing = sorted(expected - _parity_rows())
    assert not missing, f"no PARITY-LOOM.md row for: {missing}"


def test_parity_rows_name_only_real_callables() -> None:
    extra = sorted(_parity_rows() - _public_callables())
    assert not extra, f"PARITY-LOOM.md rows for callables that do not exist: {extra}"


_STATUSES = {"P", "V", "R", "S", "D"}


def test_parity_status_letters_are_valid() -> None:
    bad = [(k, n, st) for k, n, _js, st, _t in _parity_table_rows() if st not in _STATUSES]
    assert not bad, bad


def test_parity_v_only_where_an_oracle_case_exists() -> None:
    """A V row names a test that replays a recorded oracle file and calls the function, and the module has a recorder."""
    for key, name, _js, status, test in _parity_table_rows():
        if status != "V":
            continue
        assert (REPO / "tools" / "oracle" / f"loom_{key.split('.')[-1]}.js").is_file() or key.startswith("tools."), (key, name)
        assert test != "-", f"{key}.{name} is V but names no test"
        text = (REPO / test).read_text(encoding="utf-8")
        assert "oracle(" in text, f"{key}.{name}: {test} does not replay an oracle file"
        assert re.search(r"\b" + re.escape(name) + r"\(", text), f"{key}.{name}: {test} does not call it"


def test_parity_test_column_names_an_existing_test_file_that_calls_the_function() -> None:
    bad = []
    for key, name, _js, _st, test in _parity_table_rows():
        if test == "-":
            continue
        path = REPO / test
        if not path.is_file() or not re.search(r"\b" + re.escape(name) + r"\(", path.read_text(encoding="utf-8")):
            bad.append(f"{key}.{name} -> {test}")
    assert not bad, bad


def test_scipy_is_the_same_dependency_in_pyproject_the_script_and_the_readmes() -> None:
    import ast

    # tomllib is Python 3.11+; the project supports 3.10, so read the one list by hand.
    block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", (REPO / "pyproject.toml").read_text(encoding="utf-8"), re.M | re.S)
    assert block, "no dependencies list in pyproject.toml"
    deps = re.findall(r'"([^"]+)"', block.group(1))
    assert any(re.match(r"scipy\b", d) for d in deps), deps
    tree = ast.parse((REPO / "Loom" / "loom_preprocess.py").read_text(encoding="utf-8"))
    packages = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "PACKAGES" for t in node.targets):
            packages = tuple(ast.literal_eval(node.value))
    assert packages is not None and "scipy" in packages, packages
    assert "scipy" in README.read_text(encoding="utf-8") and "scipy" in SIRIL_README.read_text(encoding="utf-8")


_DEVIATIONS = [
    "SPFC dropped",
    "which are not available offline",
    "MGC replaced by Siril subsky degree 2",
    "gradient tool is none",
    "never on H, S or O",
    "MultiscaleAdaptiveStretch replaced by Siril GHS",
    "autoghs",
    "D 4.5",
    "saved configurations migrate",
    "MLDenoise dropped",
    "Siril denoise added",
    "-mod 0.6/1.0/1.0",
    "linear slot",
    "unknown stretch method raises",
    "falls back to MTF",
    "Registration uses Siril's homography",
    "lanczos4",
    "bit-identical",
    "SPCC white-balance factors",
    "recovered by fitting",
    "TIFF export goes through `io_tiff`",
    "savetif",
    "fallback ICC profile for a mono image",
    "built-in sRGB",
    "reports its outcome in a status file",
    "no Siril `starnet` command route",
    "useCache off",
    "falls back to direct SPCC",
    "warm cache folder",
    "narrowband normalisation is a numpy port",
    "pending",
    "results are files",
    "no other workspace windows are opened",
    "headless job run never saves options",
    "defaults plus the job file only",
    "Dialog mode saves the options before the preflight",
    "StarNet2 licence acceptance",
    "flag in `tools.json`",
    "not an option field",
    "Prism Essential",
    "nonlinear when the image is already stretched",
    "detail-sharpening failure",
    "keeps the star-reduced plate",
    "keepFineScaleChange step",
]


def test_parity_has_a_deviations_section_with_every_item() -> None:
    section = _section(PARITY.read_text(encoding="utf-8"), "Deviations")
    # the wrapped lines of the document are joined so that one phrase is one line of the check
    flat = " ".join(section.split())
    missing = [phrase for phrase in _DEVIATIONS if " ".join(phrase.split()).lower() not in flat.lower()]
    assert not missing, missing


# ---- changelog -------------------------------------------------------------------------------------------------------


def test_changelog_unreleased_mentions_the_loom_pipeline() -> None:
    """The Unreleased section, or the newest release once Unreleased is emptied by a release, names the pipeline."""
    text = CHANGELOG.read_text(encoding="utf-8")
    sections = re.findall(r"^## \[?([^\]\n]+?)\]?(?: [^\n]*)?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert sections and sections[0][0].lower() == "unreleased", "no Unreleased section"
    body = sections[0][1] if sections[0][1].strip() else (sections[1][1] if len(sections) > 1 else "")
    assert re.search(r"\bLoom\b", body), "the newest changelog section does not mention the Loom pipeline"

