"""The astrometric residual check is on for every run: Gaia DR3 from the cache first, online otherwise, skipped for
the run with one log line when the catalogue cannot be reached (never failing the run)."""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from loomsiril import gaia
from loomsiril.io_fits import read_fits
from loomsiril.loom import residuals
from loomsiril.loom.runctx import RunContext

from . import synth

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "Loom" / "loom_preprocess.py"
STUB = REPO / "tests" / "sirilpy_stub"

CSV = (",".join(gaia.GAIA_ONLINE_COLUMNS) + "\n"
       "83.80,-5.40,1.0,0.1,0.1,12.0,12.4,11.6\n83.85,-5.38,0.8,0.2,-0.1,13.5,13.9,13.0\n")


def _query():
    return gaia.GaiaQuery.from_centre((83.82, -5.39), 0.3, 16.0)


def test_cache_first_then_online_then_cached(tmp_path) -> None:
    calls = []

    def fetch(url: str) -> str:
        calls.append(url)
        return CSV

    src = residuals.gaia_catalogue_source(cache_dir=tmp_path, fetch_text=fetch)
    first = src((83.82, -5.39), 0.3, 16.0)
    second = src((83.82, -5.39), 0.3, 16.0)
    assert len(calls) == 1, "the second request must come from the cache"
    assert first == second and len(first) == 2
    assert (tmp_path / (gaia.cache_key(_query()) + ".csv")).exists()


def test_existing_cache_file_needs_no_network(tmp_path) -> None:
    (tmp_path / (gaia.cache_key(_query()) + ".csv")).write_text(CSV, encoding="utf-8")

    def fetch(url: str) -> str:
        raise AssertionError("no online request when the cache has the field")

    assert len(residuals.gaia_catalogue_source(cache_dir=tmp_path, fetch_text=fetch)((83.82, -5.39), 0.3, 16.0)) == 2


def test_unreachable_catalogue_skips_the_run_with_one_log_line(tmp_path) -> None:
    def fetch(url: str) -> str:
        raise gaia.GaiaError("no route to host")

    lines: list[str] = []
    checker = residuals.ResidualChecker(residuals.gaia_catalogue_source(cache_dir=tmp_path, fetch_text=fetch), lines.append)
    m = synth.make_master("L", noise=0.0)
    path = synth.write_master(tmp_path / "L.fits", m)
    _, header = read_fits(path)
    stars = [{"xpos": x + 0.5, "ypos": y + 0.5, "fwhmx": 3.0, "fwhmy": 3.0} for x, y, _ in m.stars[:30]]
    assert checker.check("L", stars, header) is None
    assert checker.check("R", stars, header) is None
    assert len(lines) == 1 and checker.skipped


@pytest.fixture
def script(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(STUB))
    for name in [m for m in sys.modules if m == "sirilpy" or m.startswith("sirilpy.")]:
        monkeypatch.delitem(sys.modules, name)
    import sirilpy

    sirilpy.STATE.reset()
    name = "loom_siril_residual_wiring"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    yield mod
    sirilpy.STATE.reset()


def test_every_run_context_carries_a_residual_checker(script, tmp_path) -> None:
    m = script._load_modules()
    out = SimpleNamespace(log=lambda *a, **k: None, warn=lambda *a, **k: None)
    session = SimpleNamespace(m=m, out=out, found={}, cancel=threading.Event(), progress=None, store=None,
                              siril=SimpleNamespace())
    m.siril_ops = SimpleNamespace(SirilCommandOps=lambda siril, log=None: object())
    factory = script._context_factory(session, None)
    ctx = factory(str(tmp_path))
    assert isinstance(ctx, RunContext)
    assert isinstance(ctx.residuals, residuals.ResidualChecker)
    assert factory(str(tmp_path)).residuals is ctx.residuals, "one checker per run (skip state is per run)"


NETWORK_SCRIPT = r"""
import json, sys
import numpy as np
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[1] + "/tests")
from loomsiril import gaia
from loomsiril.loom import residuals
from loom import synth
tsv = gaia.read_tsv(sys.argv[1] + "/tests/fixtures/ic1396-gaia-g16.tsv")
bright = [r for r in tsv if r["G"] < 13.0]
w, h = 800, 600
header = dict(synth.tan_header(w, h, 4.0), CRVAL1=324.745, CRVAL2=57.514, NAXIS1=w, NAXIS2=h, LOOMROWS="top-down")
pts = synth.sky_to_pixel(header, np.array([r["ra"] for r in bright]), np.array([r["dec"] for r in bright]))
stars = [{"xpos": x + 0.5, "ypos": y + 0.5, "fwhmx": 3.0, "fwhmy": 3.0}
         for x, y in pts if 10 <= x < w - 10 and 10 <= y < h - 10][:300]
lines = []
checker = residuals.ResidualChecker(residuals.gaia_catalogue_source(cache_dir=sys.argv[2]), lines.append)
print("RESULT=" + json.dumps({"result": checker.check("L", stars, header), "lines": lines}))
"""


@pytest.mark.network
def test_online_gaia_reaches_a_verdict_for_a_real_field(tmp_path) -> None:
    """Needs the network (LOOMSIRIL_NETWORK=1; runs in a child process, outside the suite's socket guard): star
    positions projected from the public IC 1396 Gaia table through an exact TAN header get the verdict "ok" against
    the online catalogue, and the answer is cached."""
    import json
    import subprocess

    proc = subprocess.run([sys.executable, "-c", NETWORK_SCRIPT, str(REPO), str(tmp_path / "gaia")],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT=")][-1]
    data = json.loads(line[len("RESULT="):])
    assert data["result"] is not None and data["result"]["verdict"] == "ok", data
    assert list((tmp_path / "gaia").glob("*.csv")), "the online answer is cached"


def _solved_master(tmp_path):
    m = synth.make_master("L", noise=0.0)
    _, header = read_fits(synth.write_master(tmp_path / "L.fits", m))
    stars = [{"xpos": x + 0.5, "ypos": y + 0.5, "fwhmx": 3.0, "fwhmy": 3.0} for x, y, _ in m.stars[:30]]
    return header, stars


def _factory_context(script, tmp_path, logged):
    m = script._load_modules()
    out = SimpleNamespace(log=lambda text, *a, **k: logged.append(text), warn=lambda *a, **k: None)
    session = SimpleNamespace(m=m, out=out, found={}, cancel=threading.Event(), progress=None, store=None,
                              siril=SimpleNamespace())
    m.siril_ops = SimpleNamespace(SirilCommandOps=lambda siril, log=None: object())
    return script._context_factory(session, None)(str(tmp_path))


def test_entry_checker_with_the_default_source_logs_one_skip_line_when_gaia_is_unreachable(
        script, tmp_path, monkeypatch) -> None:
    """The context the entry builds uses the default source (loomsiril.gaia.fetch_text, the user cache folder) and
    its skip line reaches the run log exactly once over two checks."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    fetched: list[str] = []

    def unreachable(url: str) -> str:
        fetched.append(url)
        raise gaia.GaiaError("no route to host")

    monkeypatch.setattr(gaia, "fetch_text", unreachable)
    logged: list[str] = []
    ctx = _factory_context(script, tmp_path, logged)
    header, stars = _solved_master(tmp_path)
    assert ctx.residuals.check("L", stars, header) is None
    assert ctx.residuals.check("R", stars, header) is None
    assert len(fetched) == 1, "the default source must go through loomsiril.gaia.fetch_text, once per run"
    assert len(logged) == 1 and ctx.residuals.skipped


def test_injected_failing_fetch_also_logs_one_skip_line(tmp_path) -> None:
    lines: list[str] = []

    def failing(url: str) -> str:
        raise OSError("network down")

    checker = residuals.ResidualChecker(residuals.gaia_catalogue_source(cache_dir=tmp_path, fetch_text=failing),
                                        lines.append)
    header, stars = _solved_master(tmp_path)
    checker.check("L", stars, header)
    checker.check("R", stars, header)
    assert len(lines) == 1
