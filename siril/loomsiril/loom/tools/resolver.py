"""Find the external tools Loom uses, in a fixed order, without running anything the resolver did not find.

Order (the first candidate whose probe passes wins; see ``Resolver.resolve``):

1. configured path, then the tool's environment variable (macOS ``X.app`` resolves to its executable);
2. the remembered path, accepted without a probe only while its size and mtime are unchanged;
3. Windows App Paths registry (HKCU, then HKLM), legacy SyQon CSV hints (hints are still probed);
4. known default locations;
5. a bounded scan of application roots (Linux: after PATH; elsewhere: before PATH);
6. PATH.

Every path is a platform-native string ("logical" path). ``root`` maps logical absolute paths
onto a folder of the real file system so that tests can simulate macOS, Windows and Linux
install trees under a temporary directory.
"""

from __future__ import annotations

import fnmatch
import json
import ntpath
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from .specs import PLATFORMS, TOOLS, ProbeResult, ToolSpec, Verdict, refused

SCAN_SECONDS = 2.0
SCAN_ENTRIES = 5000
TEXT_LIMIT = 4 * 1024 * 1024
APP_PATHS = "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"
CSV_FOLDERS: dict[str, str] = {
    "parallax_cli": "SyQonParallaxCLI",
    "prism_cli": "SyQonPrismCLI",
    "SyQonStarless": "SyQonStarlessCLI",
    "syqon-cli": "SyQonStudioCLI",
}
CSV_PATTERN = "syqon_*_config.csv"

Prober = Callable[[str, ToolSpec], ProbeResult]
Registry = Callable[[str, str], "str | None"]


@dataclass(frozen=True)
class Found:
    tool_id: str
    path: str
    source: str  # configured | env | remembered | registry | siril-script | csv-hint | known | scan | path
    version: str = ""
    probe_summary: str = ""


def pathmod(platform: str) -> Any:
    return ntpath if platform == "win32" else posixpath


def map_path(root: str | os.PathLike[str] | None, platform: str, logical: str) -> str:
    """The real file-system path of a logical path: identity without ``root``, else under ``root``."""
    if root is None:
        return logical
    if platform == "win32":
        drive, rest = ntpath.splitdrive(logical)
        parts = [drive.replace(":", "")] if drive else []
        parts += [p for p in re.split(r"[\\/]+", rest) if p]
    else:
        parts = [p for p in logical.split("/") if p]
    return str(Path(root).joinpath(*parts))


def default_prober(path: str, spec: ToolSpec, platform: str | None = None) -> ProbeResult:
    """Run ``path`` with the spec's probe argv: no shell, bounded by the probe timeout."""
    if spec.probe is None:
        raise ValueError(f"tool {spec.id!r} has no probe")
    plat = platform or sys.platform
    extra: dict[str, Any] = {}
    if plat == "win32":
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        proc = subprocess.run(
            [path, *spec.probe.argv],
            cwd=os.path.dirname(path) or None,   # as Siril's RC-Astro scripts run it: from its own folder
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=spec.probe.timeout,
            shell=False,
            check=False,
            **extra,
        )
    except subprocess.TimeoutExpired as exc:
        return ProbeResult(None, _text(exc.stdout), _text(exc.stderr), timed_out=True)
    except OSError as exc:
        return ProbeResult(None, error=str(exc))
    return ProbeResult(proc.returncode, proc.stdout or "", proc.stderr or "")


#: Siril scripts (siril-scripts repository) that save the executable of a Loom tool in their settings.json.
SIRIL_SCRIPT_SETTINGS: dict[str, tuple[str, ...]] = {
    "rcastro": ("BlurXTerminator", "NoiseXTerminator", "StarXTerminator"),
}


def winreg_registry(hive: str, key: str) -> str | None:
    """The default value of ``key`` under HKCU or HKLM, read with the Windows registry API.

    ``winreg`` is imported here, not at module level, so the module imports on every platform;
    off Windows, or when the key is absent, the answer is None.
    """
    try:
        import winreg  # noqa: PLC0415  (Windows only; imported lazily on purpose)
    except ImportError:
        return None
    roots = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    root = roots.get(hive)
    if root is None:
        return None
    try:
        with winreg.OpenKey(root, key) as handle:
            value, _kind = winreg.QueryValueEx(handle, "")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _read_text(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            data = fh.read(TEXT_LIMIT)
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


class _Missing(Exception):
    pass


class _Budget:
    """Entry and time budget of one scanned root; ``take`` is False once either is spent."""

    def __init__(self, clock: Callable[[], float], seconds: float, entries: int) -> None:
        self._clock = clock
        self._seconds = seconds
        self._entries = entries
        self._used = 0
        self._start = clock()
        self.done = False

    def take(self) -> bool:
        if not self.done and self._used < self._entries and self._clock() - self._start < self._seconds:
            self._used += 1
            return True
        self.done = True
        return False


class Resolver:
    def __init__(
        self,
        platform: str,
        home: str,
        env: Mapping[str, str],
        store: Any = None,
        prober: Prober | None = None,
        registry: Registry | None = None,
        clock: Callable[[], float] = time.monotonic,
        temp_dir: str | None = None,
        script_paths: Iterable[str] = (),
        root: str | os.PathLike[str] | None = None,
        now: Callable[[], str] | None = None,
        siril_data_dir: str | None = None,
        siril_config_dir: str | None = None,
    ) -> None:
        if platform not in PLATFORMS:
            raise ValueError(f"unknown platform {platform!r}")
        self._platform = platform
        self._pm = pathmod(platform)
        self._home = home
        self._env = dict(env)
        self._store = store
        self._prober: Prober = prober or (lambda path, spec: default_prober(path, spec, platform=platform))
        # On Windows the App Paths lookup is the real registry unless a reader is injected.
        self._registry = registry if registry is not None else (winreg_registry if platform == "win32" else None)
        self._now = now if now is not None else _utc_now
        self._reasons: dict[str, str] = {}
        self._clock = clock
        self._temp_dir = temp_dir
        self._script_paths = tuple(script_paths)
        # The base of Siril's data folders as the running Siril reports it (siril_ops.siril_data_dir); searched first.
        self._siril_data_dir = siril_data_dir
        # Siril's config folder: Siril's own tool scripts save the executable they use there (_siril_script_hits).
        self._siril_config_dir = siril_config_dir
        self._root = root
        self._scan_cache: dict[str, list[str]] | None = None

    # public API

    def resolve(self, tool_id: str, configured: str | None = None) -> Found | None:
        spec = self._spec(tool_id)
        for source, path in self._candidates(spec, configured):
            outcome = self._try(spec, source, path)
            if outcome is not None:
                found, probed = outcome
                if probed:
                    self._remember(found)
                return found
        return None

    def rejection(self, tool_id: str) -> str | None:
        """Why the last candidate for ``tool_id`` was refused (for the dialog), or None."""
        return self._reasons.get(tool_id)

    def candidates(self, tool_id: str, configured: str | None = None) -> list[tuple[str, str]]:
        """(source, path) pairs in the order resolve() tries them, existing files only; nothing is probed."""
        spec = self._spec(tool_id)
        return [(s, p) for s, p in self._candidates(spec, configured) if os.path.isfile(self._real(p))]

    def graxpert_model(self) -> tuple[str | None, bool]:
        """The GraXpert model folder and whether any ``<version>/model.onnx`` exists in it."""
        data = self._data_dir()
        if data is None:
            return None, False
        parts = ("GraXpert", "GraXpert") if self._platform == "win32" else ("GraXpert",)
        model = self._pm.join(data, *parts, "bge-ai-models")
        real = self._real(model)
        ready = any(os.path.isfile(os.path.join(real, name, "model.onnx")) for name in self._listing(real))
        return model, ready

    # candidate generation

    def _spec(self, tool_id: str) -> ToolSpec:
        try:
            return TOOLS[tool_id]
        except KeyError:
            raise ValueError(f"unknown tool {tool_id!r}") from None

    def _candidates(self, spec: ToolSpec, configured: str | None) -> Iterator[tuple[str, str]]:
        seen: set[str] = set()

        def fresh(path: str | None) -> bool:
            if not path or path in seen:
                return False
            seen.add(path)
            return True

        env_value = self._env_get(spec.env_var) if spec.env_var else None
        for source, raw in (("configured", configured), ("env", env_value)):
            if raw:
                path = self._entry(spec, raw)
                if fresh(path):
                    yield source, path
        if self._platform not in spec.platforms:
            return
        if self._store is not None:
            rec = self._store.remembered(spec.id)
            if rec and isinstance(rec.get("path"), str) and fresh(rec["path"]):
                yield "remembered", rec["path"]
        if spec.kind == "script":
            for path in self._graxpert_locations(spec):
                if fresh(path):
                    yield "known", path
            return
        for raw in self._siril_script_hits(spec):
            path = self._entry(spec, raw)
            if fresh(path):
                yield "siril-script", path
        if self._platform == "win32" and self._registry is not None:
            for hive in ("HKCU", "HKLM"):
                for name in spec.names("win32"):
                    path = _registry_path(self._registry(hive, APP_PATHS + name))
                    if path and fresh(path):
                        yield "registry", path
        for raw in self._csv_hints(spec):
            path = self._entry(spec, raw)
            if fresh(path):
                yield "csv-hint", path
        for raw in spec.known_paths.get(self._platform, ()):
            path = self._expand(raw)
            if fresh(path):
                yield "known", path
        later: tuple[tuple[str, Iterator[str]], ...] = (
            ("path", self._path_hits(spec)),
            ("scan", self._scan_hits(spec)),
        )
        if self._platform != "linux":
            later = (later[1], later[0])
        for source, paths in later:
            for path in paths:
                if fresh(path):
                    yield source, path

    def _entry(self, spec: ToolSpec, raw: str) -> str | None:
        """A configured or env value as a candidate: expanded, and macOS ``X.app`` resolved."""
        path = self._expand(raw)
        if path is None:
            return None
        if self._platform == "darwin" and path.lower().endswith(".app"):
            names = spec.names("darwin")
            if names:
                return self._pm.join(path, "Contents", "MacOS", names[0])
        return path

    def _expand(self, raw: str) -> str | None:
        text = raw
        if text == "~" or text.startswith("~/") or text.startswith("~\\"):
            text = self._home + text[1:]

        def sub(match: re.Match[str]) -> str:
            value = self._env_get(match.group(1))
            if value is None:
                raise _Missing
            return value

        try:
            return re.sub(r"%([^%]+)%", sub, text)
        except _Missing:
            return None

    def _env_get(self, name: str) -> str | None:
        if name in self._env:
            return self._env[name]
        low = name.lower()
        for key, value in self._env.items():
            if key.lower() == low:
                return value
        return None

    def _data_dir(self) -> str | None:
        if self._platform == "darwin":
            return self._pm.join(self._home, "Library", "Application Support")
        if self._platform == "win32":
            return self._env_get("LOCALAPPDATA") or None
        xdg = self._env_get("XDG_DATA_HOME")
        if xdg:
            return xdg
        return self._pm.join(self._home, ".local", "share")

    def _graxpert_locations(self, spec: ToolSpec) -> list[str]:
        name = spec.names(self._platform)[0]
        locations: list[str] = []
        if self._siril_data_dir:
            repo = self._pm.join(self._siril_data_dir, "siril-scripts")
            locations.append(self._pm.join(repo, "processing", name))
            # The repository files scripts under category folders; GraXpert-AI may move to another one.
            for folder in self._listing(self._real(repo)):
                if folder != "processing" and not folder.startswith("."):
                    locations.append(self._pm.join(repo, folder, name))
        data = self._data_dir()
        if data:
            locations.append(self._pm.join(data, "siril-scripts", "processing", name))
            locations.append(self._pm.join(data, "siril", "siril-scripts", "processing", name))
        for folder in self._script_paths:
            locations.append(self._pm.join(folder, name))
            locations.append(self._pm.join(folder, "processing", name))
        return locations

    def _csv_hints(self, spec: ToolSpec) -> Iterator[str]:
        folder = CSV_FOLDERS.get(spec.id)
        if not folder:
            return
        temp = self._temp_dir if self._temp_dir is not None else tempfile.gettempdir()
        real_dir = self._real(self._pm.join(temp, folder))
        for name in self._listing(real_dir):
            if not fnmatch.fnmatchcase(name.casefold(), CSV_PATTERN):
                continue
            text = _read_text(os.path.join(real_dir, name))
            if text is None:
                continue
            for line in text.splitlines():
                first = line.split(",", 1)[0].strip().strip('"').strip()
                if first:
                    yield first

    def _path_hits(self, spec: ToolSpec) -> Iterator[str]:
        sep = ";" if self._platform == "win32" else ":"
        for folder in (self._env_get("PATH") or "").split(sep):
            if not folder.strip():
                continue
            for name in spec.names(self._platform):
                yield self._pm.join(folder, name)

    def _siril_script_hits(self, spec: ToolSpec) -> Iterator[str]:
        """The executable that Siril's own script for this tool saved (``<config>/<script>/settings.json``, key
        ``exe``): Siril's RC-Astro scripts keep the rc-astro path there once the user has located it."""
        if not self._siril_config_dir:
            return
        for folder in SIRIL_SCRIPT_SETTINGS.get(spec.id, ()):
            settings = self._pm.join(self._siril_config_dir, folder, "settings.json")
            text = _read_text(self._real(settings))
            if text is None:
                continue
            try:
                exe = json.loads(text).get("exe")
            except (ValueError, AttributeError):
                continue
            if isinstance(exe, str) and exe.strip():
                yield os.path.expanduser(exe.strip())

    def _scan_hits(self, spec: ToolSpec) -> Iterator[str]:
        if spec.kind != "exe" or self._platform not in spec.platforms:
            return
        yield from self._scan_results().get(spec.id, [])

    # bounded scan (once per resolver; one pass serves every tool)

    def _scan_roots(self) -> Iterator[str]:
        if self._platform == "darwin":
            raws: tuple[str, ...] = ("/Applications", "~/Applications")
        elif self._platform == "win32":
            raws = ("%ProgramFiles%", "%ProgramFiles(x86)%", "%LOCALAPPDATA%\\Programs")
        else:
            raws = ("/opt",)
        for raw in raws:
            path = self._expand(raw)
            if path:
                yield path

    def _name_lookup(self) -> dict[str, list[str]]:
        lookup: dict[str, list[str]] = {}
        for spec in TOOLS.values():
            if spec.kind != "exe" or self._platform not in spec.platforms:
                continue
            for name in spec.names(self._platform):
                ids = lookup.setdefault(self._key(name), [])
                if spec.id not in ids:
                    ids.append(spec.id)
        return lookup

    def _key(self, name: str) -> str:
        return name.casefold() if self._platform in ("darwin", "win32") else name

    def _scan_results(self) -> dict[str, list[str]]:
        if self._scan_cache is None:
            lookup = self._name_lookup()
            hits: dict[str, list[str]] = {}
            for root in self._scan_roots():
                budget = _Budget(self._clock, SCAN_SECONDS, SCAN_ENTRIES)
                self._scan_root(root, lookup, hits, budget)
            self._scan_cache = hits
        return self._scan_cache

    def _scan_root(self, root: str, lookup: dict[str, list[str]], hits: dict[str, list[str]], budget: _Budget) -> None:
        for name in self._listing(self._real(root)):
            if not budget.take():
                return
            child = self._pm.join(root, name)
            if not os.path.isdir(self._real(child)):
                continue
            if self._platform == "darwin" and name.lower().endswith(".app"):
                # a bundle is never walked, except its executable folder
                macos = self._pm.join(child, "Contents", "MacOS")
                if os.path.isdir(self._real(macos)):
                    self._visit(macos, lookup, hits, budget)
                continue
            # E/<exe>, then E/<F>/<exe> for each subfolder F of E (depth 3 counting the root)
            for sub in self._visit(child, lookup, hits, budget):
                if budget.done:
                    return
                self._visit(sub, lookup, hits, budget)
            if budget.done:
                return

    def _visit(self, folder: str, lookup: dict[str, list[str]], hits: dict[str, list[str]], budget: _Budget) -> list[str]:
        subfolders: list[str] = []
        for name in self._listing(self._real(folder)):
            if not budget.take():
                break
            path = self._pm.join(folder, name)
            real = self._real(path)
            ids = lookup.get(self._key(name))
            if ids and os.path.isfile(real):
                for tool_id in ids:
                    bucket = hits.setdefault(tool_id, [])
                    if path not in bucket:
                        bucket.append(path)
            elif os.path.isdir(real):
                subfolders.append(path)
        return subfolders

    # probing

    def _try(self, spec: ToolSpec, source: str, path: str) -> tuple[Found, bool] | None:
        """The found tool and whether it was probed now (False: accepted from the remembered record)."""
        real = self._real(path)
        if not os.path.isfile(real):
            return None
        if spec.kind == "script":
            # read as text only; the script is never executed
            text = _read_text(real)
            verdict = spec.text_check(text) if (text is not None and spec.text_check) else refused("unreadable")
            found = self._found(spec, path, source, verdict)
            return (found, True) if found is not None else None
        if self._platform != "win32" and not os.access(real, os.X_OK):
            return None
        if spec.requires_any and not self._model_present(spec, real):
            self._reasons[spec.id] = "the model file was not found next to the executable"
            return None
        if source == "remembered" and not spec.always_probe and self._store is not None:
            rec = self._store.remembered(spec.id)
            st = os.stat(real)
            if rec and rec.get("size") == st.st_size and rec.get("mtime") == st.st_mtime_ns:
                return Found(spec.id, path, "remembered", str(rec.get("version", "")),
                             str(rec.get("probe_summary", "remembered"))), False
        if spec.probe is None:
            return None
        found = self._found(spec, path, source, spec.probe.check(self._prober(real, spec)))
        return (found, True) if found is not None else None

    def _model_present(self, spec: ToolSpec, real: str) -> bool:
        folder = os.path.dirname(real)
        return any(os.path.exists(os.path.join(folder, *rel.split("/"))) for rel in spec.requires_any)

    def _found(self, spec: ToolSpec, path: str, source: str, verdict: Verdict) -> Found | None:
        if not verdict.passed:
            self._reasons[spec.id] = verdict.reason
            return None
        self._reasons.pop(spec.id, None)
        return Found(spec.id, path, source, verdict.version, verdict.summary)

    def _remember(self, found: Found) -> None:
        """Best effort: an unwritable config folder must not hide a tool that was found."""
        if self._store is None:
            return
        try:
            st = os.stat(self._real(found.path))
            self._store.remember(found, st.st_size, st.st_mtime_ns, probed=self._now())
        except OSError:
            return

    # file system through the root mapping

    def _real(self, logical: str) -> str:
        return map_path(self._root, self._platform, logical)

    def _listing(self, real_dir: str) -> list[str]:
        try:
            return sorted(os.listdir(real_dir))
        except OSError:
            return []


def _registry_path(value: str | None) -> str | None:
    if value is None:
        return None
    path = value.strip().strip('"').strip()
    return path or None
