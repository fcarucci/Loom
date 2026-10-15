"""Specifications of the external tools Loom discovers: one ToolSpec per tool id.

Pure data and probe checks. Nothing here runs a program or touches the file system.
Paths are written with ``~`` (home folder) and ``%VAR%`` (environment) placeholders and are
expanded by the resolver for the platform in use.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

from loomsiril.starnet_version import starnet_version_problem

PLATFORMS: tuple[str, ...] = ("darwin", "win32", "linux")


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of running a tool's probe command (see resolver.default_prober)."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str = ""


@dataclass(frozen=True)
class Verdict:
    """Outcome of a probe check: passed with a version, or refused with a reason for the dialog."""

    passed: bool
    version: str = ""
    summary: str = ""
    reason: str = ""


def passed(version: str, summary: str) -> Verdict:
    return Verdict(True, version, summary)


def refused(reason: str) -> Verdict:
    return Verdict(False, reason=reason)


ProbeCheck = Callable[[ProbeResult], Verdict]
TextCheck = Callable[[str], Verdict]


@dataclass(frozen=True)
class Probe:
    """How a candidate executable is proven to be the tool: argv tail, timeout, and the check."""

    argv: tuple[str, ...]
    timeout: float
    check: ProbeCheck


@dataclass(frozen=True)
class ToolSpec:
    id: str
    platforms: tuple[str, ...]
    exe_names: Mapping[str, tuple[str, ...]]
    env_var: str
    known_paths: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    probe: Probe | None = None
    # Relative to the executable's folder (POSIX separators); any one that exists satisfies the tool.
    requires_any: tuple[str, ...] = ()
    kind: str = "exe"  # "exe": probed by running it; "script": read as text, never run
    text_check: TextCheck | None = None
    # True: the remembered shortcut never applies (the result can lapse with the file unchanged).
    always_probe: bool = False

    def names(self, platform: str) -> tuple[str, ...]:
        return tuple(self.exe_names.get(platform, ()))


def exit_zero(result: ProbeResult) -> Verdict:
    """Pass when the command exited 0 in time; the version is the first line of its output."""
    if result.timed_out or result.returncode != 0:
        return refused("the tool did not answer its probe")
    first = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
    return passed(first, "exit 0")


def exit_zero_with_output(result: ProbeResult) -> Verdict:
    """Pass when the command exited 0 and printed a non-empty first line (siril_bridge.starnet_version)."""
    if result.timed_out or result.returncode != 0:
        return refused("the tool did not answer its probe")
    first = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
    if not first:
        return refused("the tool printed no version")
    return passed(first, "exit 0")


def starnet_check(result: ProbeResult) -> Verdict:
    """Pass when StarNet2 answered ``--version`` with a version at or above MIN_STARNET_VERSION (older builds cannot
    read FITS); otherwise the refusal carries the warning the dialog, preflight and Fly-Through show."""
    verdict = exit_zero_with_output(result)
    if not verdict.passed:
        return verdict
    problem = starnet_version_problem(verdict.version)
    return refused(problem) if problem else verdict


UNLICENSED_RC_ASTRO = "RC-Astro CLI is not licensed on this computer"
RC_ASTRO_UNLICENSED_EXIT = 77


def rc_astro_json(result: ProbeResult) -> Verdict:
    """`bxt --json` must exit 0, report schemaVersion >= 5, and not report license.valid false.

    Exit 77 is RC-Astro's "unlicensed" status; a license that is not valid is the same refusal.
    """
    if result.timed_out:
        return refused("the RC-Astro CLI did not answer its probe")
    if result.returncode == RC_ASTRO_UNLICENSED_EXIT:
        return refused(UNLICENSED_RC_ASTRO)
    if result.returncode != 0:
        return refused("the RC-Astro CLI probe failed")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return refused("the RC-Astro CLI probe printed no JSON")
    if not isinstance(data, dict):
        return refused("the RC-Astro CLI probe printed no JSON object")
    schema = data.get("schemaVersion")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 5:
        return refused("the RC-Astro CLI schemaVersion is too old")
    # Siril's RC-Astro scripts refuse only a license object that says valid: false; a document without one is
    # accepted (an unlicensed tool still fails its run with exit 77, which the run reports).
    license_info = data.get("license")
    if isinstance(license_info, dict) and "valid" in license_info and license_info.get("valid") is not True:
        return refused(UNLICENSED_RC_ASTRO)
    state = "licensed" if isinstance(license_info, dict) and license_info.get("valid") is True else "license not reported"
    return passed(str(data.get("version", schema)), f"schemaVersion {schema}, {state}")


GRAXPERT_SCRIPT = "GraXpert-AI.py"
_GRAXPERT_VERSION = re.compile(r'VERSION = "([^"]+)"')


def graxpert_text_check(text: str) -> Verdict:
    """The GraXpert-AI Siril script: must name GraXpert and assign a non-empty VERSION string."""
    if "GraXpert" not in text:
        return refused("not the GraXpert-AI script")
    match = _GRAXPERT_VERSION.search(text)
    if match is None:
        return refused("the GraXpert-AI script has no VERSION")
    return passed(match.group(1), "text")


_STARLESS_MODEL = ("../Resources/axiom3.mlmodelc", "axiom3.mlmodelc")

TOOLS: dict[str, ToolSpec] = {
    "rcastro": ToolSpec(
        id="rcastro",
        platforms=PLATFORMS,
        exe_names={"darwin": ("rc-astro",), "win32": ("rc-astro.exe",), "linux": ("rc-astro",)},
        env_var="LOOMSIRIL_RCASTRO",
        known_paths={
            "darwin": ("/Applications/RC-Astro/CLI/rc-astro",),
            "win32": ("%ProgramFiles%\\RC-Astro\\CLI\\rc-astro.exe",),
            "linux": ("/opt/rc-astro/rc-astro", "/usr/local/bin/rc-astro"),
        },
        probe=Probe(argv=("bxt", "--json"), timeout=20.0, check=rc_astro_json),
        always_probe=True,
    ),
    "syqon-cli": ToolSpec(
        id="syqon-cli",
        platforms=PLATFORMS,
        exe_names={"darwin": ("syqon-cli",), "win32": ("syqon-cli.exe",), "linux": ("syqon-cli",)},
        env_var="SYQON_CLI_PATH",
        known_paths={
            "darwin": (
                "/Applications/SyQon Studio.app/Contents/MacOS/syqon-cli",
                "~/Applications/SyQon Studio.app/Contents/MacOS/syqon-cli",
            ),
            "win32": (
                "%LOCALAPPDATA%\\Programs\\SyQon Studio\\syqon-cli.exe",
                "%ProgramFiles%\\SyQon Studio\\syqon-cli.exe",
            ),
            "linux": (
                "/usr/bin/syqon-cli",
                "/usr/local/bin/syqon-cli",
                "/opt/SyQonStudio/syqon-cli",
                "/opt/syqon-studio/syqon-cli",
                "~/.local/bin/syqon-cli",
            ),
        },
        probe=Probe(argv=("--version",), timeout=20.0, check=exit_zero),
    ),
    "SyQonStarless": ToolSpec(
        id="SyQonStarless",
        platforms=("darwin", "win32"),
        exe_names={"darwin": ("SyQonStarless",), "win32": ("SyQonStarless.exe",)},
        env_var="LOOMSIRIL_SYQON_STARLESS",
        known_paths={
            "darwin": ("/Applications/SyQonStarless.app/Contents/MacOS/SyQonStarless",),
            "win32": ("%ProgramFiles%\\SyQon\\Starless\\SyQonStarless.exe",),
        },
        probe=Probe(argv=("--help",), timeout=20.0, check=exit_zero),
        requires_any=_STARLESS_MODEL,
    ),
    "prism_cli": ToolSpec(
        id="prism_cli",
        platforms=("darwin", "win32"),
        exe_names={"darwin": ("prism_cli",), "win32": ("prism_cli.exe",)},
        env_var="LOOMSIRIL_PRISM_CLI",
        known_paths={"darwin": ("/Applications/prism_cli/prism_cli",)},
        probe=Probe(argv=("--help",), timeout=20.0, check=exit_zero),
    ),
    "parallax_cli": ToolSpec(
        id="parallax_cli",
        platforms=("darwin", "win32"),
        exe_names={"darwin": ("parallax_cli",), "win32": ("parallax_cli.exe",)},
        env_var="LOOMSIRIL_PARALLAX_CLI",
        known_paths={"darwin": ("/Applications/ParallaxAI/parallax_cli",)},
        probe=Probe(argv=("--help",), timeout=20.0, check=exit_zero),
    ),
    "starnet2": ToolSpec(
        id="starnet2",
        platforms=PLATFORMS,
        exe_names={
            "darwin": ("starnet2", "starnet++"),
            "win32": ("starnet2.exe", "starnet++.exe"),
            "linux": ("starnet2", "starnet++"),
        },
        env_var="LOOMSIRIL_STARNET",
        probe=Probe(argv=("--version",), timeout=20.0, check=starnet_check),
        always_probe=True,   # a remembered pre-2.6.2 record must not outlive the version rule
    ),
    "graxpert-ai": ToolSpec(
        id="graxpert-ai",
        platforms=PLATFORMS,
        exe_names={
            "darwin": (GRAXPERT_SCRIPT,),
            "win32": (GRAXPERT_SCRIPT,),
            "linux": (GRAXPERT_SCRIPT,),
        },
        env_var="LOOMSIRIL_GRAXPERT_AI",
        kind="script",
        text_check=graxpert_text_check,
    ),
}
