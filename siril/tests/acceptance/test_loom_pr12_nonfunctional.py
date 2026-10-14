"""Acceptance: non-functional rules of the Loom pipeline modules.

* importing a module has no side effects (no output, no files, no threads, no network, no environment changes);
* only the Siril script and the dialog import sirilpy / tkinter at module level (the Siril adapter imports sirilpy
  lazily inside functions);
* nothing but the Gaia residual check may open a network connection;
* the same inputs give the same cache keys in every interpreter run (hash seed independent);
* documentation: a "Loom" README section, the script README, and a parity table once the pipeline is documented.

Everything here works on whatever modules exist, so it stays green while the package is still being merged.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from acc_helpers import REPO
from loom_acc_helpers import LOOM_PKG, imported_roots, public_modules, top_level_imported_roots

GUI_MODULES = {"dialog.py"}  # the dialog may import tkinter
NETWORK_ROOTS = {"socket", "urllib", "http", "requests", "ftplib", "smtplib", "ssl", "httpx", "aiohttp", "telnetlib"}
NETWORK_ALLOWED = {"residuals.py"}  # the Gaia residual check (cache first)


def _rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


def _modules() -> list:
    return [p for p in public_modules() if p.name != "__init__.py" or p.parent == LOOM_PKG]


def _module_name(p: Path) -> str:
    parts = p.relative_to(REPO).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def test_the_package_exists() -> None:
    assert (LOOM_PKG / "__init__.py").is_file()


def test_pure_modules_do_not_import_sirilpy_or_tkinter() -> None:
    bad = []
    for p in _modules():
        roots = imported_roots(p)
        if p.name == "siril_ops.py":
            if "tkinter" in roots:
                bad.append(f"{_rel(p)} imports tkinter")
            if "sirilpy" in top_level_imported_roots(p):
                bad.append(f"{_rel(p)} imports sirilpy at module level (must be lazy, inside functions)")
        elif p.name in GUI_MODULES:
            continue
        elif roots & {"sirilpy", "tkinter"}:
            bad.append(f"{_rel(p)} imports {sorted(roots & {'sirilpy', 'tkinter'})}")
    assert not bad, "\n".join(bad)


def test_sirilpy_is_imported_only_by_the_siril_layer() -> None:
    allowed_prefixes = ("Loom/", "tests/", "loomsiril/tk_theme.py", "loomsiril/loom/siril_ops.py",
                        "loomsiril/loom/dialog", "loomsiril/siril_bridge.py")
    bad = []
    for p in (REPO / "loomsiril").rglob("*.py"):
        if _rel(p).startswith(allowed_prefixes):
            continue
        if "sirilpy" in imported_roots(p):
            bad.append(_rel(p))
    assert not bad, bad


def test_only_the_residual_check_may_use_the_network() -> None:
    bad = []
    for p in _modules():
        if p.name in NETWORK_ALLOWED:
            continue
        hit = imported_roots(p) & NETWORK_ROOTS
        if hit:
            bad.append(f"{_rel(p)}: {sorted(hit)}")
    assert not bad, "\n".join(bad)


_PROBE = r'''
import builtins, json, os, sys, threading, importlib, socket, io, contextlib

class Blocked(Exception):
    pass

class Guard:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("sirilpy", "tkinter", "_tkinter"):
            raise ImportError("blocked: " + name)
        return None
sys.meta_path.insert(0, Guard())

def no_connect(*a, **k):
    raise Blocked("network use at import")
socket.socket.connect = no_connect
socket.socket.connect_ex = no_connect
socket.create_connection = no_connect

names = json.loads(sys.argv[1])
home = os.environ["HOME"]
problems = []
def snapshot():
    return (sorted(os.listdir(os.getcwd())), sorted(os.listdir(home)), threading.active_count(), dict(os.environ))
for name in names:
    before = snapshot()
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            importlib.import_module(name)
    except Exception as e:
        problems.append([name, "import failed: %r" % (e,)])
        continue
    after = snapshot()
    if out.getvalue() or err.getvalue():
        problems.append([name, "printed %r" % (out.getvalue() + err.getvalue())[:80]])
    if before[0] != after[0] or before[1] != after[1]:
        problems.append([name, "created files"])
    if before[2] != after[2]:
        problems.append([name, "started a thread"])
    if before[3] != after[3]:
        problems.append([name, "changed the environment"])
bad = [m for m in ("sirilpy", "tkinter") if m in sys.modules]
if bad:
    problems.append(["*", "pulled in " + ",".join(bad)])
print("RESULT=" + json.dumps(problems))
'''


def test_importing_every_module_is_free_of_side_effects(tmp_path: Path) -> None:
    names = [_module_name(p) for p in _modules() if p.name not in GUI_MODULES]
    assert names
    home = tmp_path / "home"
    work = tmp_path / "cwd"
    home.mkdir()
    work.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    env.update({"HOME": str(home), "USERPROFILE": str(home), "PYTHONPATH": str(REPO), "XDG_DATA_HOME": str(home / "x")})
    proc = subprocess.run([sys.executable, "-c", _PROBE, json.dumps(names)], cwd=str(work), env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT=")]
    assert line, proc.stdout + proc.stderr
    assert json.loads(line[-1][len("RESULT="):]) == []


# ---- determinism


_KEYS = r'''
import json, sys
from loomsiril.loom import cache
params = {"solve": {"b": 1, "a": [1, 2, {"z": None}]}, "mgc": {"x": 0.5}, "stretchRGB": {"keepLinear": True, "t": 0.25},
          "extractL": {}, "stretchL": {"keepLinear": False}}
chain = cache.build_stage_keys(cache.file_source_key("/d/a.fits", 12345, 1700000000.5), params)
print("KEYS=" + json.dumps([[e["stage"], e["key"], e.get("companion")] for e in chain]))
'''


def test_cache_keys_do_not_depend_on_the_interpreter_run() -> None:
    pytest.importorskip("loomsiril.loom.cache")
    results = []
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONHASHSEED": seed}
        proc = subprocess.run([sys.executable, "-c", _KEYS], env=env, capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr[-1500:]
        results.append([ln for ln in proc.stdout.splitlines() if ln.startswith("KEYS=")][-1])
    assert len(set(results)) == 1


def test_stretch_parameters_are_repeatable() -> None:
    stretch = pytest.importorskip("loomsiril.loom.stretch")
    import numpy as np

    rng = np.random.default_rng(3)
    planes = [(0.05 + rng.normal(0, 0.003, (60, 80))).astype(np.float32).clip(0, 1) for _ in range(3)]
    a = stretch.stretch_parameters_for_planes(planes, False)
    b = stretch.stretch_parameters_for_planes([p.copy() for p in planes], False)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    oa = stretch.apply_mtf(planes, a, False)
    ob = stretch.apply_mtf(planes, b, False)
    assert all(x.tobytes() == y.tobytes() for x, y in zip(oa, ob))


# ---- documentation (checked once the pipeline documentation exists)

PARITY = REPO / "docs" / "PARITY-LOOM.md"


def _docs_written() -> bool:
    return PARITY.is_file()


def test_readme_has_a_loom_section() -> None:
    if not _docs_written():
        pytest.skip("pipeline documentation not written yet")
    heads = [ln for ln in (REPO / "README.md").read_text(encoding="utf-8").splitlines() if re.match(r"#+\s", ln)]
    assert any(re.fullmatch(r"#+\s*Loom\b.*", h) and "fly" not in h.lower() for h in heads), heads


def test_script_readme_describes_the_pipeline_script() -> None:
    if not _docs_written():
        pytest.skip("pipeline documentation not written yet")
    text = (REPO / "Loom" / "README.md").read_text(encoding="utf-8")
    assert "loom_preprocess.py" in text
    assert (REPO / "Loom" / "loom_preprocess.py").is_file()


def test_parity_table_rows_are_well_formed() -> None:
    if not _docs_written():
        pytest.skip("pipeline documentation not written yet")
    text = PARITY.read_text(encoding="utf-8")
    assert re.search(r"\bP\b.*\bV\b.*\bR\b.*\bD\b.*\bS\b", text, re.S), "status legend missing"
    rows = [ln for ln in text.splitlines() if ln.startswith("|") and not re.match(r"\|\s*[-:| ]+\|?\s*$", ln)]
    body = [r for r in rows if not re.match(r"\|\s*(JS|Loom|Function)\b", r, re.I)]
    assert len(body) >= 100, f"only {len(body)} rows: one row per ported JavaScript function is expected"
    bad = []
    for r in body:
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[2] not in {"P", "V", "R", "D", "S"}:
            bad.append(r[:100])
    assert not bad, bad[:10]


def test_parity_table_names_every_module_and_its_tests_exist() -> None:
    if not _docs_written():
        pytest.skip("pipeline documentation not written yet")
    text = PARITY.read_text(encoding="utf-8")
    missing = [p.name for p in _modules() if p.name not in ("__init__.py",) and p.stem not in text]
    assert not missing, f"modules not mentioned in the parity document: {missing}"
    paths = set(re.findall(r"tests/[\w/.\-]+\.py", text))
    gone = sorted(p for p in paths if not (REPO / p).is_file())
    assert not gone, gone
