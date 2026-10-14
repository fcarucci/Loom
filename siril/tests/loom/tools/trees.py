"""Simulated install trees and fakes for the external-tool tests. Read-only for later tasks.

``make_tree`` builds a platform's install tree under a temporary folder. Keys are logical
(platform-native) absolute paths such as ``/Applications/RC-Astro/CLI/rc-astro`` or
``C:\\Program Files\\SyQon\\syqon-cli.exe``; the resolver maps them under the same folder through
``root``. Values: ``"exe"`` (executable file), ``"file"`` (plain file), ``"dir"``, or a tuple
``("exe" | "file", text)`` for a file with content.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from loomsiril.loom.tools.resolver import Resolver, map_path
from loomsiril.loom.tools.specs import ProbeResult, ToolSpec

HOMES: dict[str, str] = {
    "darwin": "/Users/tester",
    "win32": "C:\\Users\\tester",
    "linux": "/home/u",
}
TEMP_DIRS: dict[str, str] = {
    "darwin": "/var/folders/xx/T",
    "win32": "C:\\Users\\tester\\AppData\\Local\\Temp",
    "linux": "/srv/scratch-sim",
}
APP_PATHS = "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"


def default_env(platform: str) -> dict[str, str]:
    if platform == "win32":
        return {
            "ProgramFiles": "C:\\Program Files",
            "ProgramFiles(x86)": "C:\\Program Files (x86)",
            "LOCALAPPDATA": "C:\\Users\\tester\\AppData\\Local",
        }
    return {}


def make_tree(tmp_path: Path, platform: str, entries: Mapping[str, Any]) -> Path:
    """Build the simulated tree for ``platform`` under ``tmp_path``; return the root to pass as ``root=``."""
    root = Path(tmp_path) / f"tree-{platform}"
    root.mkdir(parents=True, exist_ok=True)
    for logical, kind in entries.items():
        real = Path(map_path(root, platform, logical))
        if kind == "dir":
            real.mkdir(parents=True, exist_ok=True)
            continue
        real.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(kind, tuple):
            kind, text = kind
        else:
            text = None
        if kind == "exe":
            body = text if text is not None else ("" if platform == "win32" else "#!/bin/sh\nexit 0\n")
            real.write_text(body, encoding="utf-8")
            real.chmod(0o644 if platform == "win32" else 0o755)
        elif kind == "file":
            real.write_text(text or "", encoding="utf-8")
            real.chmod(0o644)
        else:
            raise ValueError(f"unknown tree entry kind {kind!r}")
    return root


def real_path(root: Path, platform: str, logical: str) -> Path:
    return Path(map_path(root, platform, logical))


RC_ASTRO_LICENSED = {
    "schemaVersion": 6,
    "version": "1.4.0",
    "license": {"valid": True, "product": "RC-Astro CLI", "expires": "2027-06-30"},
}


class FakeProber:
    """Stands in for the default prober. Records every call.

    ``fail`` and ``timeout`` are substrings of the real path; ``answers`` maps a tool id to the
    ProbeResult it returns. Defaults: rc-astro reports a valid licence, the others exit 0 with a version line.
    """

    def __init__(
        self,
        fail: Iterable[str] = (),
        timeout: Iterable[str] = (),
        answers: Mapping[str, ProbeResult] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = tuple(fail)
        self._timeout = tuple(timeout)
        self._answers = dict(answers or {})

    def __call__(self, path: str, spec: ToolSpec) -> ProbeResult:
        self.calls.append((spec.id, path))
        if any(s in path for s in self._timeout):
            return ProbeResult(returncode=None, timed_out=True)
        if any(s in path for s in self._fail):
            return ProbeResult(returncode=1, stdout="", stderr="refused")
        if spec.id in self._answers:
            return self._answers[spec.id]
        if spec.id == "rcastro":
            return ProbeResult(returncode=0, stdout=json.dumps(RC_ASTRO_LICENSED))
        return ProbeResult(returncode=0, stdout=f"{spec.id} 1.0\n")


class FakeRegistry:
    def __init__(self, values: Mapping[tuple[str, str], str]) -> None:
        self.values = dict(values)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, hive: str, key: str) -> str | None:
        self.calls.append((hive, key))
        return self.values.get((hive, key))


class Clock:
    """Monotonic stand-in: each call returns the current time, then advances it by ``step``."""

    def __init__(self, start: float = 0.0, step: float = 0.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def sim_resolver(
    platform: str,
    root: Path | None,
    *,
    store: Any = None,
    prober: Callable[[str, ToolSpec], ProbeResult] | None = None,
    registry: Callable[[str, str], str | None] | None = None,
    clock: Callable[[], float] | None = None,
    env: Mapping[str, str] | None = None,
    temp_dir: str | None = None,
    script_paths: Iterable[str] = (),
    siril_data_dir: str | None = None,
    siril_config_dir: str | None = None,
) -> Resolver:
    return Resolver(
        platform=platform,
        home=HOMES[platform],
        env=default_env(platform) if env is None else env,
        store=store,
        prober=prober if prober is not None else FakeProber(),
        registry=registry,
        clock=clock if clock is not None else time.monotonic,
        temp_dir=TEMP_DIRS[platform] if temp_dir is None else temp_dir,
        script_paths=script_paths,
        root=root,
        siril_data_dir=siril_data_dir,
        siril_config_dir=siril_config_dir,
    )
