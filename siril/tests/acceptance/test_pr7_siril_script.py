"""Loom/loom_fly_through.py imports against the sirilpy stub and says it was NOT run inside Siril."""

from __future__ import annotations

import importlib.util
import re
import sys

import pytest

from acc_helpers import REPO

SCRIPT = REPO / "Loom" / "loom_fly_through.py"
STUB = REPO / "tests" / "sirilpy_stub"


@pytest.fixture
def script_module(monkeypatch: pytest.MonkeyPatch):
    assert SCRIPT.is_file() and (STUB / "sirilpy").exists(), "siril script and sirilpy stub must exist (E7)"
    for name in [n for n in sys.modules if n.split(".")[0] in ("sirilpy", "tksiril")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(STUB))
    monkeypatch.syspath_prepend(str(REPO))
    spec = importlib.util.spec_from_file_location("loom_fly_through_acc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # importing must not open a dialog or connect to Siril
    return mod


def test_script_imports_against_stub(script_module) -> None:
    assert script_module is not None
    assert "sirilpy" in sys.modules
    assert str(STUB) in (sys.modules["sirilpy"].__file__ or "")


def test_script_is_thin(script_module) -> None:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 700, "the Siril script should delegate to loomsiril, not reimplement it"


def test_not_run_inside_siril_is_stated() -> None:
    assert SCRIPT.is_file(), "siril script must exist (E7)"
    import ast

    doc = ast.get_docstring(ast.parse(SCRIPT.read_text(encoding="utf-8"))) or ""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    pat = re.compile(r"not\s+(?:been\s+)?(?:run|tested|executed)[^.\n]{0,40}inside\s+siril", re.I)
    assert pat.search(doc) or pat.search(readme), "neither the script docstring nor README.md says it was NOT run inside Siril"
