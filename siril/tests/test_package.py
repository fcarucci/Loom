"""The package surface: ``dir(loomsiril)``, lazy submodule attributes and the import cycle.

Each check runs in a fresh interpreter: the test session has already imported every module, so only a
subprocess shows what a bare ``import loomsiril`` does.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import loomsiril

REPO_ROOT = Path(loomsiril.__file__).resolve().parent.parent

#: The submodules a bare import must list and resolve (the modules of the package, from its docstring).
SUBMODULES = ("geometry", "parallax", "presets", "encode", "colour", "gaia", "wcs", "io_fits", "io_tiff", "detect",
              "assign", "starmodel", "starfit", "starlayer", "resample", "bloom", "scene", "sprites", "frame",
              "pipeline", "cli", "siril_bridge", "core", "types")


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120, check=False)


def _ok(code: str) -> None:
    proc = _run_python(code)
    assert proc.returncode == 0, proc.stderr


def test_dir_lists_the_public_names_and_the_submodules_after_a_bare_import() -> None:
    _ok(
        "import loomsiril\n"
        "names = dir(loomsiril)\n"
        "for n in ('FlyJob', 'Result', 'run', 'Scene', 'RAD', 'clamp') + %r:\n"
        "    assert n in names, n\n"
        "assert set(loomsiril.__all__) <= set(names)\n" % (SUBMODULES,)
    )


def test_bare_import_resolves_every_submodule_and_every_public_name() -> None:
    _ok(
        "import sys\n"
        "import loomsiril\n"
        "assert 'loomsiril.pipeline' not in sys.modules\n"          # nothing imported yet
        "assert loomsiril.pipeline.FlyJob is loomsiril.FlyJob\n"
        "assert loomsiril.run is loomsiril.pipeline.run\n"
        "assert loomsiril.Result is loomsiril.pipeline.Result\n"
        "for n in %r:\n"
        "    module = getattr(loomsiril, n)\n"
        "    assert module.__name__ == 'loomsiril.' + n, n\n"
        "for n in loomsiril.__all__:\n"
        "    getattr(loomsiril, n)\n" % (SUBMODULES,)
    )


def test_core_does_not_import_pipeline() -> None:
    _ok(
        "import sys\n"
        "import loomsiril.core\n"
        "assert 'loomsiril.pipeline' not in sys.modules\n"
        "assert 'loomsiril.scene' not in sys.modules\n"
    )


def test_unknown_attribute_still_raises() -> None:
    with pytest.raises(AttributeError, match="no attribute 'not_a_name'"):
        getattr(loomsiril, "not_a_name")


def test_submodule_listing_matches_the_package_files() -> None:
    names = set(loomsiril.__dir__())
    assert set(SUBMODULES) <= names
    assert "__main__" not in names


def test_dir_lists_the_module_dunders_too() -> None:
    names = set(loomsiril.__dir__())
    assert {"__version__", "__name__", "__file__", "__doc__"} <= names
    assert loomsiril.__dir__() == sorted(loomsiril.__dir__())
