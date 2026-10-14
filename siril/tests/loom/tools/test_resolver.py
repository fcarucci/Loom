"""Discovery of external tools on simulated macOS, Windows and Linux install trees.

Nothing here runs a real tool: trees are built under tmp_path, the prober and the registry are fakes,
and the default prober is exercised only on a shell script written into tmp_path.
"""

from __future__ import annotations

import ntpath
import json
import os
import posixpath
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from loomsiril.loom.tools import resolver as resolver_module
from loomsiril.loom.tools.resolver import Found, default_prober
from loomsiril.loom.tools.specs import (
    TOOLS,
    ProbeResult,
    exit_zero,
    exit_zero_with_output,
    graxpert_text_check,
    rc_astro_json,
)
from loomsiril.loom.tools.store import ToolStore

from .trees import (
    APP_PATHS,
    TEMP_DIRS,
    Clock,
    FakeProber,
    FakeRegistry,
    default_env,
    make_tree,
    real_path,
    sim_resolver,
)


@pytest.fixture
def fake_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The system temporary folder, moved under tmp_path so the store's temp-folder guard is exercised."""
    folder = tmp_path / "system-temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(folder))
    return folder


@pytest.fixture
def store(tmp_path: Path, fake_temp: Path) -> ToolStore:
    return ToolStore(tmp_path / "config")


def _pm(platform: str):  # type: ignore[no-untyped-def]
    return ntpath if platform == "win32" else posixpath


KNOWN_ROWS = [
    ("darwin", "rcastro", "/Applications/RC-Astro/CLI/rc-astro"),
    ("darwin", "syqon-cli", "/Applications/SyQon Studio.app/Contents/MacOS/syqon-cli"),
    ("darwin", "syqon-cli", "/Users/tester/Applications/SyQon Studio.app/Contents/MacOS/syqon-cli"),
    ("darwin", "SyQonStarless", "/Applications/SyQonStarless.app/Contents/MacOS/SyQonStarless"),
    ("darwin", "prism_cli", "/Applications/prism_cli/prism_cli"),
    ("darwin", "parallax_cli", "/Applications/ParallaxAI/parallax_cli"),
    ("win32", "rcastro", "C:\\Program Files\\RC-Astro\\CLI\\rc-astro.exe"),
    ("win32", "syqon-cli", "C:\\Users\\tester\\AppData\\Local\\Programs\\SyQon Studio\\syqon-cli.exe"),
    ("win32", "syqon-cli", "C:\\Program Files\\SyQon Studio\\syqon-cli.exe"),
    ("win32", "SyQonStarless", "C:\\Program Files\\SyQon\\Starless\\SyQonStarless.exe"),
    ("linux", "rcastro", "/opt/rc-astro/rc-astro"),
    ("linux", "rcastro", "/usr/local/bin/rc-astro"),
    ("linux", "syqon-cli", "/usr/bin/syqon-cli"),
    ("linux", "syqon-cli", "/usr/local/bin/syqon-cli"),
    ("linux", "syqon-cli", "/opt/SyQonStudio/syqon-cli"),
    ("linux", "syqon-cli", "/opt/syqon-studio/syqon-cli"),
    ("linux", "syqon-cli", "/home/u/.local/bin/syqon-cli"),
]


@pytest.mark.parametrize("platform, tool, path", KNOWN_ROWS)
def test_every_known_default_path_is_found(tmp_path: Path, store: ToolStore, platform: str, tool: str, path: str) -> None:
    entries: dict[str, object] = {path: "exe"}
    if tool == "SyQonStarless":
        entries[_pm(platform).join(_pm(platform).dirname(path), "axiom3.mlmodelc")] = "dir"
    root = make_tree(tmp_path, platform, entries)  # type: ignore[arg-type]
    prober = FakeProber()
    found = sim_resolver(platform, root, store=store, prober=prober).resolve(tool)
    assert found is not None
    assert (found.path, found.source) == (path, "known")
    assert [p for _, p in prober.calls] == [str(real_path(root, platform, path))]


def test_starless_is_not_run_without_its_model(tmp_path: Path, store: ToolStore) -> None:
    path = "/Applications/SyQonStarless.app/Contents/MacOS/SyQonStarless"
    root = make_tree(tmp_path, "darwin", {path: "exe"})
    prober = FakeProber()
    assert sim_resolver("darwin", root, store=store, prober=prober).resolve("SyQonStarless") is None
    assert prober.calls == []


def test_starless_model_next_to_the_executable_is_accepted(tmp_path: Path, store: ToolStore) -> None:
    path = "/Applications/SyQonStarless.app/Contents/MacOS/SyQonStarless"
    root = make_tree(tmp_path, "darwin", {path: "exe", "/Applications/SyQonStarless.app/Contents/MacOS/axiom3.mlmodelc": "dir"})
    found = sim_resolver("darwin", root, store=store).resolve("SyQonStarless")
    assert found is not None and found.path == path


SCAN_ROWS = [
    ("darwin", "prism_cli", "/Applications/Vendor Suite/Deep/prism_cli"),
    ("darwin", "parallax_cli", "/Applications/Direct/parallax_cli"),
    ("darwin", "starnet2", "/Applications/Tools/bin/starnet2"),
    ("darwin", "starnet2", "/Applications/Tools/starnet++"),
    ("win32", "prism_cli", "C:\\Program Files\\Vendor\\Tool\\prism_cli.exe"),
    ("win32", "starnet2", "C:\\Program Files\\Vendor\\bin\\starnet2.exe"),
    ("win32", "rcastro", "C:\\Users\\tester\\AppData\\Local\\Programs\\Vendor\\CLI\\rc-astro.exe"),
    ("linux", "starnet2", "/opt/vendor/bin/starnet2"),
    ("linux", "rcastro", "/opt/RC/CLI/rc-astro"),
    ("linux", "rcastro", "/opt/rc-direct/rc-astro"),
]


@pytest.mark.parametrize("platform, tool, path", SCAN_ROWS)
def test_scan_finds_depth_one_to_three_folders(tmp_path: Path, store: ToolStore, platform: str, tool: str, path: str) -> None:
    root = make_tree(tmp_path, platform, {path: "exe"})
    found = sim_resolver(platform, root, store=store).resolve(tool)
    assert found is not None
    assert (found.path, found.source) == (path, "scan")


def test_scan_stops_at_depth_three(tmp_path: Path, store: ToolStore) -> None:
    path = "/opt/a/b/c/rc-astro"
    root = make_tree(tmp_path, "linux", {path: "exe"})
    assert sim_resolver("linux", root, store=store).resolve("rcastro") is None


@pytest.mark.parametrize("via", ["env", "configured"])
def test_app_bundle_path_resolves_to_its_executable(tmp_path: Path, store: ToolStore, via: str) -> None:
    bundle = "/Users/tester/Downloads/SyQon Studio.app"
    exe = bundle + "/Contents/MacOS/syqon-cli"
    root = make_tree(tmp_path, "darwin", {exe: "exe"})
    env = default_env("darwin")
    configured = None
    if via == "env":
        env["SYQON_CLI_PATH"] = bundle
    else:
        configured = bundle
    found = sim_resolver("darwin", root, store=store, env=env).resolve("syqon-cli", configured=configured)
    assert found is not None
    assert (found.path, found.source) == (exe, via)


def test_configured_path_beats_environment_variable(tmp_path: Path, store: ToolStore) -> None:
    conf = "/srv/conf/rc-astro"
    env_exe = "/srv/env/rc-astro"
    root = make_tree(tmp_path, "linux", {conf: "exe", env_exe: "exe"})
    env = {"LOOMSIRIL_RCASTRO": env_exe}
    found = sim_resolver("linux", root, store=store, env=env).resolve("rcastro", configured=conf)
    assert found is not None and (found.path, found.source) == (conf, "configured")


KEY_SYQON_APP_PATHS = APP_PATHS + "syqon-cli.exe"


def test_app_paths_registry_prefers_hkcu_over_hklm(tmp_path: Path, store: ToolStore) -> None:
    cu = "C:\\Tools\\cu\\syqon-cli.exe"
    lm = "C:\\Tools\\lm\\syqon-cli.exe"
    root = make_tree(tmp_path, "win32", {cu: "exe", lm: "exe"})
    registry = FakeRegistry({("HKCU", KEY_SYQON_APP_PATHS): cu, ("HKLM", KEY_SYQON_APP_PATHS): lm})
    found = sim_resolver("win32", root, store=store, registry=registry).resolve("syqon-cli")
    assert found is not None and (found.path, found.source) == (cu, "registry")
    assert registry.calls[0] == ("HKCU", KEY_SYQON_APP_PATHS)


def test_app_paths_registry_falls_back_to_hklm_and_strips_quotes(tmp_path: Path, store: ToolStore) -> None:
    lm = "C:\\Tools\\lm\\syqon-cli.exe"
    root = make_tree(tmp_path, "win32", {lm: "exe"})
    registry = FakeRegistry({("HKLM", KEY_SYQON_APP_PATHS): f'"{lm}"'})
    found = sim_resolver("win32", root, store=store, registry=registry).resolve("syqon-cli")
    assert found is not None and (found.path, found.source) == (lm, "registry")


CSV_PATH = "C:\\Users\\tester\\AppData\\Local\\Temp\\SyQonStudioCLI\\syqon_studio_config.csv"
HINTED = "C:\\Vendor\\SyQon\\syqon-cli.exe"
CSV_TEXT = f"{HINTED},extra\n\nC:\\Missing\\syqon-cli.exe,x\n"


def test_csv_hint_is_found(tmp_path: Path, store: ToolStore) -> None:
    root = make_tree(tmp_path, "win32", {CSV_PATH: ("file", CSV_TEXT), HINTED: "exe"})
    found = sim_resolver("win32", root, store=store).resolve("syqon-cli")
    assert found is not None and (found.path, found.source) == (HINTED, "csv-hint")


def test_csv_hint_is_still_probed(tmp_path: Path, store: ToolStore) -> None:
    root = make_tree(tmp_path, "win32", {CSV_PATH: ("file", CSV_TEXT), HINTED: "exe"})
    prober = FakeProber(fail=("Vendor",))
    assert sim_resolver("win32", root, store=store, prober=prober).resolve("syqon-cli") is None
    assert prober.calls == [("syqon-cli", str(real_path(root, "win32", HINTED)))]


def test_linux_path_comes_before_scan(tmp_path: Path, store: ToolStore) -> None:
    scanned = "/opt/vendor/bin/starnet2"
    on_path = "/home/u/bin/starnet2"
    root = make_tree(tmp_path, "linux", {scanned: "exe", on_path: "exe"})
    env = {"PATH": "/home/u/bin:/usr/bin"}
    resolver = sim_resolver("linux", root, store=store, env=env)
    assert resolver.candidates("starnet2") == [("path", on_path), ("scan", scanned)]
    found = sim_resolver("linux", root, store=store, env=env).resolve("starnet2")
    assert found is not None and (found.path, found.source) == (on_path, "path")


def test_darwin_scan_comes_before_path(tmp_path: Path, store: ToolStore) -> None:
    scanned = "/Applications/Tools/bin/starnet2"
    on_path = "/usr/local/bin/starnet2"
    root = make_tree(tmp_path, "darwin", {scanned: "exe", on_path: "exe"})
    resolver = sim_resolver("darwin", root, store=store, env={"PATH": "/usr/local/bin"})
    assert resolver.candidates("starnet2") == [("scan", scanned), ("path", on_path)]


def test_order_is_env_then_remembered_then_scan(tmp_path: Path, store: ToolStore) -> None:
    env_exe = "/srv/env/syqon-cli"
    remembered = "/srv/remembered/syqon-cli"
    scanned = "/opt/vendor/syqon-cli"
    root = make_tree(tmp_path, "linux", {env_exe: "exe", remembered: "exe", scanned: "exe"})

    def seed() -> None:
        st = real_path(root, "linux", remembered).stat()
        store.remember(Found("syqon-cli", remembered, "known", "1", "exit 0"), st.st_size, st.st_mtime_ns)

    seed()
    found = sim_resolver("linux", root, store=store, env={"SYQON_CLI_PATH": env_exe}).resolve("syqon-cli")
    assert found is not None and (found.path, found.source) == (env_exe, "env")

    seed()  # the env hit above overwrote the remembered entry
    prober = FakeProber()
    found = sim_resolver("linux", root, store=store, env={}, prober=prober).resolve("syqon-cli")
    assert found is not None and (found.path, found.source) == (remembered, "remembered")
    assert prober.calls == []  # accepted from the record without a probe

    store.forget("syqon-cli")
    found = sim_resolver("linux", root, store=store, env={}).resolve("syqon-cli")
    assert found is not None and (found.path, found.source) == (scanned, "scan")


@pytest.mark.parametrize("mode", ["fail", "timeout"])
def test_candidate_whose_probe_fails_or_times_out_is_skipped(tmp_path: Path, store: ToolStore, mode: str) -> None:
    env_exe = "/srv/env/rc-astro"
    known = "/opt/rc-astro/rc-astro"
    root = make_tree(tmp_path, "linux", {env_exe: "exe", known: "exe"})
    prober = FakeProber(**{mode: ("/srv/env",)})
    found = sim_resolver(
        "linux", root, store=store, env={"LOOMSIRIL_RCASTRO": env_exe}, prober=prober
    ).resolve("rcastro")
    assert found is not None and (found.path, found.source) == (known, "known")
    assert [p for _, p in prober.calls] == [str(real_path(root, "linux", env_exe)), str(real_path(root, "linux", known))]


def test_unsupported_platform_honours_only_configured_and_env(tmp_path: Path, store: ToolStore) -> None:
    scanned = "/opt/vendor/prism_cli"
    env_exe = "/srv/env/prism_cli"
    root = make_tree(tmp_path, "linux", {scanned: "exe", env_exe: "exe"})
    assert sim_resolver("linux", root, store=store).resolve("prism_cli") is None
    found = sim_resolver("linux", root, store=store, env={"LOOMSIRIL_PRISM_CLI": env_exe}).resolve("prism_cli")
    assert found is not None and (found.path, found.source) == (env_exe, "env")


def test_scan_entry_cap_holds_on_a_10000_entry_root(tmp_path: Path, store: ToolStore) -> None:
    dirs = {f"/opt/d{i:05d}": "dir" for i in range(10000)}
    beyond = dict(dirs, **{"/opt/zz-late/rc-astro": "exe"})
    root = make_tree(tmp_path, "linux", beyond)
    assert sim_resolver("linux", root, store=store).resolve("rcastro") is None

    within = dict(dirs, **{"/opt/d00100/rc-astro": "exe", "/opt/zz-late/rc-astro": "exe"})
    root2 = make_tree(tmp_path / "second", "linux", within)
    resolver = sim_resolver("linux", root2, store=store)
    assert resolver.candidates("rcastro") == [("scan", "/opt/d00100/rc-astro")]


def test_scan_time_cap_with_a_jumping_clock(tmp_path: Path, store: ToolStore) -> None:
    root = make_tree(tmp_path, "linux", {"/opt/vendor/rc-astro": "exe"})
    assert sim_resolver("linux", root, store=store, clock=Clock(0.0, 3.0)).resolve("rcastro") is None
    found = sim_resolver("linux", root, store=store, clock=Clock(0.0, 0.0)).resolve("rcastro")
    assert found is not None and found.source == "scan"


def test_scan_runs_once_per_resolver(tmp_path: Path, store: ToolStore) -> None:
    root = make_tree(tmp_path, "linux", {"/opt/vendor/rc-astro": "exe"})
    resolver = sim_resolver("linux", root, store=store)
    assert resolver.resolve("rcastro") is not None
    make_tree(tmp_path, "linux", {"/opt/late/starnet2": "exe"})
    assert resolver.resolve("starnet2") is None  # the cached scan does not see the new file
    fresh = sim_resolver("linux", root, store=store).resolve("starnet2")
    assert fresh is not None and fresh.source == "scan"


def test_nothing_inside_an_app_bundle_is_listed_except_macos(tmp_path: Path, store: ToolStore) -> None:
    bundle = "/Applications/Weird.app"
    exe = bundle + "/Contents/MacOS/prism_cli"
    others = [
        bundle + "/Contents/Resources/prism_cli",
        bundle + "/Contents/Frameworks/Inner/prism_cli",
        bundle + "/Helper/prism_cli",
    ]
    root = make_tree(tmp_path, "darwin", {exe: "exe", **{o: "exe" for o in others}})
    scanned = [p for s, p in sim_resolver("darwin", root, store=store).candidates("prism_cli") if s == "scan"]
    assert scanned == [exe]


def test_remembered_entry_is_reprobed_when_size_or_mtime_change(tmp_path: Path, store: ToolStore) -> None:
    path = "/usr/bin/syqon-cli"
    root = make_tree(tmp_path, "linux", {path: "exe"})
    real = real_path(root, "linux", path)
    st = real.stat()
    store.remember(Found("syqon-cli", path, "known", "1", "exit 0"), st.st_size, st.st_mtime_ns)

    prober = FakeProber()
    found = sim_resolver("linux", root, store=store, prober=prober).resolve("syqon-cli")
    assert found is not None and found.source == "remembered"
    assert prober.calls == []

    os.utime(real, (st.st_atime, st.st_mtime + 10))  # same size, new mtime
    prober = FakeProber()
    found = sim_resolver("linux", root, store=store, prober=prober).resolve("syqon-cli")
    assert found is not None and found.source == "remembered"
    assert prober.calls == [("syqon-cli", str(real))]

    real.write_text("#!/bin/sh\nexit 0\n# a longer body\n", encoding="utf-8")
    real.chmod(0o755)
    prober = FakeProber()
    found = sim_resolver("linux", root, store=store, prober=prober).resolve("syqon-cli")
    assert found is not None
    assert prober.calls == [("syqon-cli", str(real))]


def test_rc_astro_is_always_reprobed_even_when_the_file_is_unchanged(tmp_path: Path, store: ToolStore) -> None:
    path = "/opt/rc-astro/rc-astro"
    root = make_tree(tmp_path, "linux", {path: "exe"})
    real = real_path(root, "linux", path)
    st = real.stat()
    store.remember(Found("rcastro", path, "known", "1.4.0", "schemaVersion 6, licensed"), st.st_size, st.st_mtime_ns)
    prober = FakeProber(answers={"rcastro": ProbeResult(77, "")})
    assert sim_resolver("linux", root, store=store, prober=prober).resolve("rcastro") is None
    assert prober.calls == [("rcastro", str(real))]


def test_remembered_starless_is_refused_when_its_model_is_gone(tmp_path: Path, store: ToolStore) -> None:
    path = "/Applications/SyQonStarless.app/Contents/MacOS/SyQonStarless"
    model = "/Applications/SyQonStarless.app/Contents/Resources/axiom3.mlmodelc"
    root = make_tree(tmp_path, "darwin", {path: "exe", model: "dir"})
    st = real_path(root, "darwin", path).stat()
    store.remember(Found("SyQonStarless", path, "known", "1", "exit 0"), st.st_size, st.st_mtime_ns)
    real_path(root, "darwin", model).rmdir()
    prober = FakeProber()
    assert sim_resolver("darwin", root, store=store, prober=prober).resolve("SyQonStarless") is None
    assert prober.calls == []


GRAX_TEXT = 'import sys\nVERSION = "2.6.1"\n# GraXpert AI script for Siril\n'
GRAX_LOCATIONS = [
    ("linux", "/home/u/.local/share/siril-scripts/processing/GraXpert-AI.py", ()),
    ("linux", "/home/u/.local/share/siril/siril-scripts/processing/GraXpert-AI.py", ()),
    ("linux", "/scripts/siril/GraXpert-AI.py", ("/scripts/siril",)),
    ("linux", "/scripts/siril/processing/GraXpert-AI.py", ("/scripts/siril",)),
    ("darwin", "/Users/tester/Library/Application Support/siril-scripts/processing/GraXpert-AI.py", ()),
    ("darwin", "/Users/tester/Library/Application Support/siril/siril-scripts/processing/GraXpert-AI.py", ()),
    ("win32", "C:\\Users\\tester\\AppData\\Local\\siril-scripts\\processing\\GraXpert-AI.py", ()),
    ("win32", "C:\\Users\\tester\\AppData\\Local\\siril\\siril-scripts\\processing\\GraXpert-AI.py", ()),
]


@pytest.mark.parametrize("platform, path, script_paths", GRAX_LOCATIONS)
def test_graxpert_script_is_found_in_each_location_and_never_executed(
    tmp_path: Path, store: ToolStore, platform: str, path: str, script_paths: tuple[str, ...]
) -> None:
    root = make_tree(tmp_path, platform, {path: ("file", GRAX_TEXT)})
    prober = FakeProber()
    found = sim_resolver(platform, root, store=store, prober=prober, script_paths=script_paths).resolve("graxpert-ai")
    assert found is not None
    assert (found.path, found.version, found.source) == (path, "2.6.1", "known")
    assert prober.calls == []
    assert not os.access(real_path(root, platform, path), os.X_OK)


def test_graxpert_script_with_an_executable_bit_is_still_never_run(tmp_path: Path, store: ToolStore) -> None:
    path = "/home/u/.local/share/siril-scripts/processing/GraXpert-AI.py"
    root = make_tree(tmp_path, "linux", {path: ("exe", GRAX_TEXT)})
    prober = FakeProber()
    found = sim_resolver("linux", root, store=store, prober=prober).resolve("graxpert-ai")
    assert found is not None and found.version == "2.6.1"
    assert prober.calls == []


def test_graxpert_text_without_version_is_rejected(tmp_path: Path, store: ToolStore) -> None:
    path = "/home/u/.local/share/siril-scripts/processing/GraXpert-AI.py"
    root = make_tree(tmp_path, "linux", {path: ("file", "# GraXpert but no version here\n")})
    assert sim_resolver("linux", root, store=store).resolve("graxpert-ai") is None
    assert not graxpert_text_check('VERSION = ""\nGraXpert').passed


@pytest.mark.parametrize(
    "platform, model_dir, entries",
    [
        ("linux", "/home/u/.local/share/GraXpert/bge-ai-models",
         {"/home/u/.local/share/GraXpert/bge-ai-models/1.0.0/model.onnx": "file"}),
        ("darwin", "/Users/tester/Library/Application Support/GraXpert/bge-ai-models",
         {"/Users/tester/Library/Application Support/GraXpert/bge-ai-models/1.0/model.onnx": "file"}),
        ("win32", "C:\\Users\\tester\\AppData\\Local\\GraXpert\\GraXpert\\bge-ai-models",
         {"C:\\Users\\tester\\AppData\\Local\\GraXpert\\GraXpert\\bge-ai-models\\2.0.0\\model.onnx": "file"}),
    ],
)
def test_graxpert_model_ready_when_a_version_has_a_model(tmp_path: Path, platform: str, model_dir: str, entries: dict) -> None:
    root = make_tree(tmp_path, platform, entries)
    assert sim_resolver(platform, root).graxpert_model() == (model_dir, True)


def test_graxpert_model_not_ready_without_model_onnx(tmp_path: Path) -> None:
    model = "/home/u/.local/share/GraXpert/bge-ai-models"
    root = make_tree(tmp_path, "linux", {model + "/1.0.0/readme.txt": "file"})
    assert sim_resolver("linux", root).graxpert_model() == (model, False)
    empty = make_tree(tmp_path / "empty", "linux", {})
    assert sim_resolver("linux", empty).graxpert_model() == (model, False)


def test_unknown_tool_id_is_an_error(tmp_path: Path) -> None:
    root = make_tree(tmp_path, "linux", {})
    with pytest.raises(ValueError):
        sim_resolver("linux", root).resolve("no-such-tool")


UNLICENSED = "RC-Astro CLI is not licensed on this computer"


@pytest.mark.parametrize(
    "result, outcome",
    [
        (ProbeResult(77, '{"schemaVersion": 6, "license": {"valid": false}}'), UNLICENSED),
        (ProbeResult(77, '{"schemaVersion": 6, "license": {"valid": true}}'), UNLICENSED),
        (ProbeResult(0, '{"schemaVersion": 6, "license": {"valid": false}}'), UNLICENSED),
        (ProbeResult(0, '{"schemaVersion": 6, "version": "1.4.0", "license": {"valid": true}}'), ("1.4.0", "schemaVersion 6, licensed")),
        # No license object: accepted, as Siril's RC-Astro scripts do (only valid: false refuses).
        (ProbeResult(0, '{"schemaVersion": 6, "version": "1.4.0"}'), ("1.4.0", "schemaVersion 6, license not reported")),
        (ProbeResult(0, '{"schemaVersion": 6, "license": {"valid": "yes"}}'), UNLICENSED),
        (ProbeResult(0, '{"schemaVersion": 4, "license": {"valid": true}}'), "the RC-Astro CLI schemaVersion is too old"),
        (ProbeResult(0, "not json"), "the RC-Astro CLI probe printed no JSON"),
        (ProbeResult(1, '{"schemaVersion": 6, "license": {"valid": true}}'), "the RC-Astro CLI probe failed"),
        (ProbeResult(None, timed_out=True), "the RC-Astro CLI did not answer its probe"),
    ],
)
def test_rc_astro_probe_requires_exit_zero_schema_five_and_no_invalid_licence(result: ProbeResult, outcome: Any) -> None:
    verdict = rc_astro_json(result)
    if isinstance(outcome, tuple):
        assert verdict.passed and (verdict.version, verdict.summary) == outcome
    else:
        assert not verdict.passed and verdict.reason == outcome


def test_unlicensed_rc_astro_is_not_offered_and_the_reason_is_recorded(tmp_path: Path, store: ToolStore) -> None:
    path = "/opt/rc-astro/rc-astro"
    root = make_tree(tmp_path, "linux", {path: "exe"})
    resolver = sim_resolver("linux", root, store=store, prober=FakeProber(answers={"rcastro": ProbeResult(77, "")}))
    assert resolver.resolve("rcastro") is None
    assert resolver.rejection("rcastro") == UNLICENSED


def test_starnet_with_empty_output_is_not_offered(tmp_path: Path, store: ToolStore) -> None:
    path = "/opt/vendor/bin/starnet2"
    root = make_tree(tmp_path, "linux", {path: "exe"})
    prober = FakeProber(answers={"starnet2": ProbeResult(0, "")})
    assert sim_resolver("linux", root, store=store, prober=prober).resolve("starnet2") is None
    found = sim_resolver("linux", root, store=store).resolve("starnet2")
    assert found is not None and found.version == "starnet2 1.0"


def test_exit_zero_probe() -> None:
    verdict = exit_zero(ProbeResult(0, "syqon 3.1\nmore\n"))
    assert (verdict.passed, verdict.version, verdict.summary) == (True, "syqon 3.1", "exit 0")
    assert not exit_zero(ProbeResult(2, "")).passed
    assert not exit_zero(ProbeResult(None, timed_out=True)).passed


def test_starnet_probe_needs_exit_zero_and_non_empty_output() -> None:
    assert not exit_zero_with_output(ProbeResult(0, "")).passed
    assert not exit_zero_with_output(ProbeResult(0, "   \n")).passed
    assert not exit_zero_with_output(ProbeResult(3, "StarNet2 1.0")).passed
    verdict = exit_zero_with_output(ProbeResult(0, "StarNet2 1.0\nmore"))
    assert verdict.passed and verdict.version == "StarNet2 1.0"


def test_default_prober_uses_argv_without_shell_and_hides_the_console_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen.clear()
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    spec = TOOLS["syqon-cli"]
    result = default_prober("C:\\Tools\\syqon-cli.exe", spec, platform="win32")
    assert result == ProbeResult(0, "ok", "")
    assert seen["argv"] == ["C:\\Tools\\syqon-cli.exe", "--version"]
    assert seen["shell"] is False
    assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    default_prober("/opt/syqon-cli", spec, platform="linux")
    assert "creationflags" not in seen


@pytest.mark.skipif(sys.platform == "win32", reason="posix shell script")
def test_default_prober_runs_a_real_script_and_reports_its_output(tmp_path: Path) -> None:
    script = tmp_path / "syqon-cli"
    script.write_text("#!/bin/sh\necho 'syqon-cli 3.1'\n", encoding="utf-8")
    script.chmod(0o755)
    result = default_prober(str(script), TOOLS["syqon-cli"], platform="linux")
    assert result.returncode == 0 and result.stdout.strip() == "syqon-cli 3.1"
    verdict = exit_zero(result)
    assert (verdict.version, verdict.summary) == ("syqon-cli 3.1", "exit 0")


@pytest.mark.skipif(sys.platform == "win32", reason="posix shell script")
def test_default_prober_times_out(tmp_path: Path) -> None:
    script = tmp_path / "slow"
    script.write_text("#!/bin/sh\nexec sleep 5\n", encoding="utf-8")
    script.chmod(0o755)
    spec = TOOLS["syqon-cli"]
    spec = replace(spec, probe=replace(spec.probe, timeout=0.3))  # type: ignore[arg-type]
    result = default_prober(str(script), spec, platform="linux")
    assert result.timed_out and result.returncode is None


@pytest.mark.perf
@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_resolving_every_tool_on_an_empty_tree_is_fast(tmp_path: Path, platform: str) -> None:
    root = make_tree(tmp_path, platform, {})
    resolver = sim_resolver(platform, root, env=default_env(platform), temp_dir=TEMP_DIRS[platform])
    start = time.perf_counter()
    for tool_id in TOOLS:
        assert resolver.resolve(tool_id) is None
    assert time.perf_counter() - start < 3.0


class _FakeHKey:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def __enter__(self) -> _FakeHKey:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_winreg(monkeypatch: pytest.MonkeyPatch, hkcu: dict[str, str], hklm: dict[str, str]) -> list[tuple]:
    """A stand-in winreg module; returns the list of OpenKey calls it received."""
    import types

    calls: list[tuple] = []
    module = types.ModuleType("winreg")
    module.HKEY_CURRENT_USER = "HKCU-root"  # type: ignore[attr-defined]
    module.HKEY_LOCAL_MACHINE = "HKLM-root"  # type: ignore[attr-defined]
    tables = {"HKCU-root": hkcu, "HKLM-root": hklm}

    def open_key(root: str, key: str) -> _FakeHKey:
        calls.append((root, key))
        if key not in tables[root]:
            raise FileNotFoundError(key)
        return _FakeHKey(tables[root])

    def query(handle: _FakeHKey, name: str) -> tuple[str, int]:
        assert name == ""  # the default value
        return "C:\\Tools\\syqon-cli.exe", 1

    module.OpenKey = open_key  # type: ignore[attr-defined]
    module.QueryValueEx = query  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "winreg", module)
    return calls


def test_default_registry_on_windows_reads_app_paths_through_winreg(
    tmp_path: Path, store: ToolStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = "C:\\Tools\\syqon-cli.exe"
    root = make_tree(tmp_path, "win32", {value: "exe"})
    calls = _fake_winreg(monkeypatch, hkcu={KEY_SYQON_APP_PATHS: value}, hklm={})
    found = sim_resolver("win32", root, store=store).resolve("syqon-cli")  # no registry injected
    assert found is not None and (found.path, found.source) == (value, "registry")
    assert calls[0] == ("HKCU-root", KEY_SYQON_APP_PATHS)


def test_default_registry_falls_back_to_hklm(tmp_path: Path, store: ToolStore, monkeypatch: pytest.MonkeyPatch) -> None:
    value = "C:\\Tools\\syqon-cli.exe"
    root = make_tree(tmp_path, "win32", {value: "exe"})
    calls = _fake_winreg(monkeypatch, hkcu={}, hklm={KEY_SYQON_APP_PATHS: value})
    found = sim_resolver("win32", root, store=store).resolve("syqon-cli")
    assert found is not None and found.source == "registry"
    assert [c[0] for c in calls] == ["HKCU-root", "HKLM-root"]


def test_winreg_is_not_touched_off_windows(tmp_path: Path, store: ToolStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_winreg(monkeypatch, hkcu={KEY_SYQON_APP_PATHS: "C:\\x.exe"}, hklm={})
    root = make_tree(tmp_path, "linux", {})
    assert sim_resolver("linux", root, store=store).resolve("syqon-cli") is None
    assert calls == []


def test_winreg_missing_module_reads_as_no_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "winreg", None)  # importing it raises ImportError
    assert resolver_module.winreg_registry("HKCU", KEY_SYQON_APP_PATHS) is None


def test_probe_date_is_stored_with_the_record(tmp_path: Path, store: ToolStore) -> None:
    path = "/usr/bin/syqon-cli"
    root = make_tree(tmp_path, "linux", {path: "exe"})
    resolver = sim_resolver("linux", root, store=store)
    resolver._now = lambda: "2026-10-13T09:00:00+00:00"  # the injected clock
    assert resolver.resolve("syqon-cli") is not None
    assert store.remembered("syqon-cli")["probed"] == "2026-10-13T09:00:00+00:00"


def test_resolve_still_returns_the_tool_when_the_config_folder_is_unwritable(tmp_path: Path, store: ToolStore) -> None:
    import stat as stat_module

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    config = tmp_path / "config"
    path = "/usr/bin/syqon-cli"
    root = make_tree(tmp_path, "linux", {path: "exe"})
    config.mkdir()
    config.chmod(stat_module.S_IRUSR | stat_module.S_IXUSR)
    try:
        found = sim_resolver("linux", root, store=store).resolve("syqon-cli")
    finally:
        config.chmod(0o755)
    assert found is not None and found.path == path


@pytest.mark.parametrize("category", ["processing", "utility"])
def test_graxpert_is_found_in_siril_scripts_repository_under_the_data_dir_siril_reports(
        tmp_path: Path, store: ToolStore, category: str) -> None:
    """macOS: Siril's data base is ~/Library/Application Support/org.siril.Siril, not ~/Library/Application Support."""
    base = "/Users/tester/Library/Application Support/org.siril.Siril"
    script = f"{base}/siril-scripts/{category}/GraXpert-AI.py"
    root = make_tree(tmp_path, "darwin", {script: ("file", GRAX_TEXT)})
    assert sim_resolver("darwin", root, store=store).resolve("graxpert-ai") is None   # the guesses miss it
    found = sim_resolver("darwin", root, store=store, siril_data_dir=base).resolve("graxpert-ai")
    assert found is not None and found.path == script


@pytest.mark.parametrize("script", ["BlurXTerminator", "NoiseXTerminator", "StarXTerminator"])
def test_rc_astro_is_found_where_siril_rc_astro_scripts_saved_it(tmp_path: Path, store: ToolStore, script: str) -> None:
    """Not at the default /Applications/RC-Astro/CLI: Siril's BXT/NXT/SXT scripts saved its path in their settings."""
    config = "/Users/tester/Library/Application Support/org.siril.Siril"
    exe = "/Users/tester/Tools/RC-Astro/rc-astro"
    settings = f"{config}/{script}/settings.json"
    root = make_tree(tmp_path, "darwin", {exe: "exe", settings: ("file", json.dumps({"exe": exe, "mlversion": 0}))})
    assert sim_resolver("darwin", root, store=store).resolve("rcastro") is None
    found = sim_resolver("darwin", root, store=store, siril_config_dir=config).resolve("rcastro")
    assert found is not None and (found.path, found.source) == (exe, "siril-script")


def test_unreadable_siril_script_settings_are_skipped(tmp_path: Path, store: ToolStore) -> None:
    config = "/Users/tester/Library/Application Support/org.siril.Siril"
    root = make_tree(tmp_path, "darwin", {f"{config}/BlurXTerminator/settings.json": ("file", "not json"),
                                          f"{config}/NoiseXTerminator/settings.json": ("file", '{"exe": ""}')})
    assert sim_resolver("darwin", root, store=store, siril_config_dir=config).resolve("rcastro") is None
