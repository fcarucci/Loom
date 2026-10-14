"""Package layout of the Loom port: imports have no side effects, dependencies and markers are declared."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import loomsiril

REPO = Path(loomsiril.__file__).resolve().parent.parent


def test_importing_the_loom_packages_pulls_in_neither_sirilpy_nor_tkinter() -> None:
    code = ("import sys\nimport loomsiril.loom, loomsiril.loom.tools\n"
            "bad = [m for m in ('sirilpy', 'tkinter') if m in sys.modules]\nassert not bad, bad\n")
    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_loom_subpackage_is_reachable_lazily_from_loomsiril() -> None:
    assert "loom" in dir(loomsiril)
    assert loomsiril.loom.__name__ == "loomsiril.loom"


def test_pyproject_declares_scipy_package_discovery_and_markers() -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    deps = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.M | re.S).group(1)
    assert "scipy" in deps and "numpy" in deps and "tifffile" in deps
    assert re.search(r'include\s*=\s*\["loomsiril\*"\]', text)
    for marker in ("siril", "starnet", "ffmpeg", "perf", "vendor", "network"):
        assert re.search(rf'^\s*"{marker}:', text, re.M), marker


def test_scipy_is_importable() -> None:
    import scipy.ndimage  # noqa: F401  (declared dependency; the image and the venv install it)
