"""Real-tool checks for the hand-run checklist (docs/loom-real-tool-checklist.md, items 1, 2, 4 and 5).

Run on a machine with the vendor tools installed and licensed:

    LOOMSIRIL_VENDOR=1 python -m pytest -q -m vendor tests/loom/vendor/test_real_tools.py

Environment (all optional unless a test says otherwise):

- ``LOOMSIRIL_VENDOR_TOOLS``: comma-separated tool ids expected to be installed, e.g. ``rcastro,syqon-cli``.
  Default: every tool id that this platform supports.
- ``LOOMSIRIL_VENDOR_LOCATE``: a path to one tool executable, supplied the way the dialog's Locate... button does.
- ``LOOMSIRIL_VENDOR_APP_BUNDLE`` (macOS): a ``.app`` bundle path of the SyQon Studio application.
- ``LOOMSIRIL_VENDOR_SYQON_MODEL``: a SyQon Studio model the account is entitled to (default ``axiom``).
- ``LOOMSIRIL_VENDOR_UNLICENSED=1``: the machine has no RC-Astro activation (checklist item 5).
- ``LOOMSIRIL_VENDOR_UNENTITLED_MODEL``: a SyQon Studio model the account is not entitled to (checklist item 5).
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.tools import rcastro, resolver, starnet, syqon_studio
from loomsiril.loom.tools.resolver import Resolver
from loomsiril.loom.tools.specs import TOOLS, RC_ASTRO_UNLICENSED_EXIT, UNLICENSED_RC_ASTRO
from loomsiril.starnet_licence import STARNET_LICENCE_REV

pytestmark = pytest.mark.vendor

SIZE = 512
ROW_CORRELATION_MIN = 0.9  # the synthetic plate has a strong vertical ramp; row order must survive the tool
SUPPORTED = {"darwin": "darwin", "win32": "win32", "linux": "linux"}
PLATFORM = SUPPORTED.get(sys.platform, "linux")


def _expected_tools() -> list[str]:
    """Tool ids that are installed here; by default every id with a probe on this platform."""
    raw = os.environ.get("LOOMSIRIL_VENDOR_TOOLS", "")
    if raw.strip():
        return [t.strip() for t in raw.split(",") if t.strip()]
    return [tid for tid, spec in TOOLS.items() if PLATFORM in spec.platforms]


def _why(resolver_: Resolver, tool_id: str) -> str:
    return f"{tool_id}: not accepted; rejection: {resolver_.rejection(tool_id)}"


def _resolver(**kwargs: Any) -> Resolver:
    """The real resolver on this machine: real environment, real home folder, real registry on Windows, no store."""
    return Resolver(PLATFORM, os.path.expanduser("~"), dict(os.environ), store=None, **kwargs)


class _MemoryStore:
    """Flags only; the entitlement check writes Prism 2.0 answers through set_flag."""

    def __init__(self) -> None:
        self.flags: dict[str, Any] = {}

    def get_flag(self, name: str) -> Any:
        return self.flags.get(name)

    def set_flag(self, name: str, value: Any) -> None:
        self.flags[name] = value


# item 1: discovery at default paths, the macOS .app route, the Windows App Paths route, manual Locate...


@pytest.mark.parametrize("tool_id", _expected_tools())
def test_item1_discovery_finds_installed_tool(tool_id: str) -> None:
    r = _resolver()
    found = r.resolve(tool_id)
    assert found is not None, _why(r, tool_id)
    assert os.path.isfile(found.path)
    assert found.source in {"known", "registry", "path", "scan", "csv-hint", "remembered", "env", "configured"}
    if os.environ.get("LOOMSIRIL_VENDOR_EXPECT_DEFAULT") == "1":
        # set on a clean machine where the tool was installed to its default location and no env var is set
        assert found.source == "known", f"{tool_id}: found through {found.source!r}, expected a default location"


@pytest.mark.skipif(PLATFORM != "darwin", reason="macOS .app route")
def test_item1_macos_app_bundle_resolves_to_executable() -> None:
    bundle = os.environ.get("LOOMSIRIL_VENDOR_APP_BUNDLE", "")
    if not bundle:
        pytest.skip("LOOMSIRIL_VENDOR_APP_BUNDLE not set")
    r = _resolver()
    found = r.resolve("syqon-cli", configured=bundle)
    assert found is not None, _why(r, "syqon-cli")
    assert found.path.endswith("Contents/MacOS/syqon-cli")
    assert found.source == "configured"


@pytest.mark.skipif(PLATFORM != "win32", reason="Windows App Paths route")
def test_item1_windows_app_paths_registry_route() -> None:
    """A registered App Paths value that names an existing file must be offered as a "registry" candidate."""
    checked = 0
    for tool_id in _expected_tools():
        for name in TOOLS[tool_id].names("win32"):
            for hive in ("HKCU", "HKLM"):
                value = resolver.winreg_registry(hive, resolver.APP_PATHS + name)
                target = resolver._registry_path(value)
                if not target or not os.path.isfile(target):
                    continue  # no registration, or a stale one: the checklist records which
                sources = [src for src, _path in _resolver().candidates(tool_id)]
                assert "registry" in sources, f"{tool_id}: {hive} App Paths value not offered as a candidate"
                checked += 1
    if not checked:
        pytest.skip("no App Paths registration for the expected tools on this machine")


def test_item1_manual_locate_is_accepted_as_configured() -> None:
    located = os.environ.get("LOOMSIRIL_VENDOR_LOCATE", "")
    if not located:
        pytest.skip("LOOMSIRIL_VENDOR_LOCATE not set")
    tool_id = _expected_tools()[0]
    r = _resolver()
    found = r.resolve(tool_id, configured=located)
    assert found is not None, _why(r, tool_id)
    assert found.source == "configured"


# item 2: the probes answer and return; schema and version are read


@pytest.mark.parametrize("tool_id", [t for t in _expected_tools() if t != "graxpert-ai"])
def test_item2_probe_answers_without_blocking(tool_id: str) -> None:
    r = _resolver()
    found = r.resolve(tool_id)
    assert found is not None, _why(r, tool_id)
    # --help probes may print their text on stderr, so only the --version and rc-astro JSON probes must name a version
    argv = TOOLS[tool_id].probe.argv
    if "--version" in argv or argv[:1] == ("bxt",):
        assert found.version, f"{tool_id}: the probe printed no version"


@pytest.mark.parametrize("sub", ["bxt", "nxt", "sxt"])
def test_item2_rcastro_schema_and_licence(tmp_path: Path, sub: str) -> None:
    found = _resolver().resolve("rcastro")
    if found is None:
        pytest.skip("rc-astro is not installed or not licensed on this machine")
    ctx = rcastro.ToolContext(work_dir=tmp_path, log=None, cancelled=lambda: False, on_progress=None, platform=sys.platform)
    schema = rcastro.RcAstro(found.path, ctx).schema(sub)
    assert isinstance(schema, dict) and schema, f"{sub}: the schema is empty"


# item 4: one synthetic 512x512 plate through each contract; the output exists, its rows are in the same order, and
# every value is finite


def _plate() -> list[np.ndarray]:
    rows = np.linspace(0.05, 0.6, SIZE)[:, None]
    cols = np.linspace(0.0, 1.0, SIZE)[None, :]
    base = rows + 0.05 * np.sin(2 * math.pi * cols * 3)
    stars = np.zeros((SIZE, SIZE))
    rng = np.random.default_rng(34)
    for _ in range(40):
        y, x = rng.integers(8, SIZE - 8, size=2)
        stars[y - 1:y + 2, x - 1:x + 2] += 0.5
    return [np.clip(base + stars, 0.0, 1.0)]


def _row_correlation(before: np.ndarray, after: np.ndarray) -> float:
    a = before.mean(axis=1)
    b = after.mean(axis=1)
    return float(np.corrcoef(a, b)[0, 1])


def _check_output(src: list[np.ndarray], out: Path) -> None:
    assert out.is_file(), f"{out.name}: no output written"
    planes, _header = read_fits(out)
    assert len(planes) == len(src), f"{out.name}: {len(planes)} plane(s), expected {len(src)}"
    for plane in planes:
        assert plane.shape == (SIZE, SIZE), f"{out.name}: shape {plane.shape}"
        assert np.all(np.isfinite(plane)), f"{out.name}: non-finite values"
    corr = _row_correlation(src[0], planes[0])
    assert corr >= ROW_CORRELATION_MIN, f"{out.name}: row order changed (row-profile correlation {corr:.3f})"


def test_item4_rcastro_contracts(tmp_path: Path) -> None:
    found = _resolver().resolve("rcastro")
    if found is None:
        pytest.skip("rc-astro is not installed or not licensed on this machine")
    src = _plate()
    write_fits(tmp_path / "in.fits", src, bitpix=-32)
    ctx = rcastro.ToolContext(work_dir=tmp_path, log=None, cancelled=lambda: False, on_progress=None, platform=sys.platform)
    rc = rcastro.RcAstro(found.path, ctx)
    jobs = [
        ("aberration.fits", lambda i, o: rc.aberration(i, o)),
        ("sharpen.fits", lambda i, o: rc.sharpen(i, o, 0.3, 0.3)),
        ("denoise.fits", lambda i, o: rc.denoise(i, o, 0.3, 0.3)),
        ("starless.fits", lambda i, o: rc.remove_stars(i, o)),
    ]
    for name, call in jobs:
        out = tmp_path / name
        call(tmp_path / "in.fits", out)
        _check_output(src, out)


def test_item4_starnet_contract(tmp_path: Path) -> None:
    found = _resolver().resolve("starnet2")
    if found is None:
        pytest.skip("StarNet2 is not installed on this machine")
    src = _plate()
    write_fits(tmp_path / "in.fits", src, bitpix=-32)
    ctx = starnet.ToolContext(work_dir=tmp_path, log=None, cancelled=lambda: False, on_progress=None, platform=sys.platform)
    starnet.remove_stars(found.path, ctx, tmp_path / "in.fits", tmp_path / "starless.fits", STARNET_LICENCE_REV)
    _check_output(src, tmp_path / "starless.fits")


def test_item4_syqon_studio_contract(tmp_path: Path) -> None:
    found = _resolver().resolve("syqon-cli")
    if found is None:
        pytest.skip("syqon-cli is not installed on this machine")
    model = os.environ.get("LOOMSIRIL_VENDOR_SYQON_MODEL", "axiom")
    src = _plate()
    write_fits(tmp_path / "in.fits", src, bitpix=-32)
    ctx = syqon_studio.ToolContext(work_dir=tmp_path, platform=sys.platform)
    studio = syqon_studio.SyqonStudio(found.path, ctx, store=_MemoryStore())
    domain = syqon_studio.domain_for(model, stretched=False)
    studio.run(model, domain, {}, tmp_path / "in.fits", tmp_path / "studio.fits")
    _check_output(src, tmp_path / "studio.fits")


# item 5: refusals. rc-astro without activation exits 77 and the resolver refuses it; SyQon Studio exits 4 for a
# model the account is not entitled to.


@pytest.mark.skipif(os.environ.get("LOOMSIRIL_VENDOR_UNLICENSED") != "1", reason="set LOOMSIRIL_VENDOR_UNLICENSED=1")
def test_item5_rcastro_unlicensed_exit_77_is_refused() -> None:
    r = _resolver()
    path = os.environ.get("LOOMSIRIL_VENDOR_LOCATE", "") or next((c[1] for c in r.candidates("rcastro")), "")
    if not path:
        pytest.skip("no rc-astro executable found")
    probe = resolver.default_prober(path, TOOLS["rcastro"], platform=PLATFORM)
    assert probe.returncode == RC_ASTRO_UNLICENSED_EXIT, f"exit {probe.returncode}, expected {RC_ASTRO_UNLICENSED_EXIT}"
    # the same instance resolves and then reports the reason: rejection() is per instance
    assert r.resolve("rcastro", configured=path) is None
    assert r.rejection("rcastro") == UNLICENSED_RC_ASTRO


@pytest.mark.skipif(not os.environ.get("LOOMSIRIL_VENDOR_UNENTITLED_MODEL"), reason="set LOOMSIRIL_VENDOR_UNENTITLED_MODEL")
def test_item5_syqon_unentitled_model_exit_4(tmp_path: Path) -> None:
    model = os.environ["LOOMSIRIL_VENDOR_UNENTITLED_MODEL"]
    found = _resolver().resolve("syqon-cli")
    if found is None:
        pytest.skip("syqon-cli is not installed on this machine")
    ctx = syqon_studio.ToolContext(work_dir=tmp_path, platform=sys.platform)
    studio = syqon_studio.SyqonStudio(found.path, ctx, store=_MemoryStore())
    entitled = studio.check_entitlement([model])
    assert entitled.get(model) is False, f"{model} ran; the account is expected to be refused"
    assert studio.problems, "no refusal message was produced"
