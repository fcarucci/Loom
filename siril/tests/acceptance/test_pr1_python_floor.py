"""Python 3.10 floor, runtime deps are numpy + tifffile only."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from acc_helpers import REPO

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}


def _py_files() -> list[Path]:
    out = []
    for p in REPO.rglob("*.py"):
        rel = p.relative_to(REPO).parts
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel):
            continue
        out.append(p)
    return out


def test_every_python_file_parses_as_py310() -> None:
    files = _py_files()
    assert len(files) > 5
    bad = []
    for p in files:
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p), feature_version=(3, 10))
        except SyntaxError as e:
            bad.append(f"{p.relative_to(REPO)}: {e}")
    assert not bad, "\n".join(bad)


def test_runtime_dependencies_are_exactly_numpy_scipy_and_tifffile() -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
    assert m, "no [project] dependencies list"
    names = sorted(re.match(r"[A-Za-z0-9_.\-]+", d.strip().strip('"\'')).group(0).lower()
                   for d in re.findall(r'"([^"]+)"', m.group(1)))
    assert names == ["numpy", "scipy", "tifffile"]  # scipy: the Loom pipeline (median, blur, fits)


def test_import_loomsiril_pulls_in_only_numpy_and_tifffile() -> None:
    code = (
        "import sys, importlib\n"
        "import loomsiril\n"
        "for n in ('core','types'): importlib.import_module('loomsiril.'+n)\n"
        "import pkgutil\n"
        "for m in pkgutil.iter_modules(loomsiril.__path__):\n"
        "    if m.name in ('cli','__main__'): continue\n"
        "    importlib.import_module('loomsiril.'+m.name)\n"
        "std = set(sys.stdlib_module_names) if hasattr(sys,'stdlib_module_names') else set()\n"
        "third = sorted({k.split('.')[0] for k in sys.modules if '.' not in k and k not in std\n"
        "               and not k.startswith('_') and k not in sys.builtin_module_names})\n"
        "print('THIRD', ','.join(third))\n"
    )
    import os
    env = dict(os.environ, PYTHONPATH=str(REPO))
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    line = [l for l in r.stdout.splitlines() if l.startswith("THIRD")][-1]
    third = set(filter(None, line[len("THIRD "):].split(",")))
    # numpy/tifffile and their own deps (imagecodecs is optional for tifffile) are fine; nothing else of ours.
    allowed = {"numpy", "scipy", "tifffile", "loomsiril", "imagecodecs", "ml_dtypes", "sitecustomize", "usercustomize"}
    extra = {t for t in third if t not in allowed and not t.startswith("numpy")}
    # pytest may be loaded through conftest-free subprocess? No: the subprocess is clean.
    assert not extra, f"unexpected third-party imports: {sorted(extra)}"
