"""The remembered-tools store: ``<config dir>/loomsiril/tools.json``.

Holds the path, version and file size/mtime of each tool found last time, plus named flags.
The store is an optimisation: a missing or unreadable file reads as empty.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .resolver import Found

STORE_NAME = "tools.json"
SCHEMA = 1


def _under(path: Path, base: Path) -> bool:
    try:
        real_path = os.path.realpath(path)
        real_base = os.path.realpath(base)
        return os.path.commonpath([real_path, real_base]) == real_base
    except ValueError:
        return False


class ToolStore:
    def __init__(self, config_dir: str | os.PathLike[str]) -> None:
        self._path = Path(config_dir) / "loomsiril" / STORE_NAME
        if _under(self._path, Path(tempfile.gettempdir())):
            raise ValueError(f"the tools store must not be under the temporary folder: {self._path}")

    @property
    def path(self) -> Path:
        return self._path

    def remembered(self, tool_id: str) -> dict[str, Any] | None:
        record = self._load()["tools"].get(tool_id)
        return dict(record) if isinstance(record, dict) else None

    def remember(self, found: Found, size: int, mtime: int, probed: str = "") -> None:
        """Record what was found; the file is not rewritten when the record is unchanged."""
        data = self._load()
        record = {
            "path": found.path,
            "source": found.source,
            "version": found.version,
            "probe_summary": found.probe_summary,
            "size": int(size),
            "mtime": int(mtime),
            "probed": probed,
        }
        if data["tools"].get(found.tool_id) == record:
            return
        data["tools"][found.tool_id] = record
        self._save(data)

    def forget(self, tool_id: str) -> None:
        data = self._load()
        if data["tools"].pop(tool_id, None) is not None:
            self._save(data)

    def get_flag(self, name: str) -> Any:
        return self._load()["flags"].get(name)

    def set_flag(self, name: str, value: Any) -> None:
        data = self._load()
        data["flags"][name] = value
        self._save(data)

    def _load(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"schema": SCHEMA, "tools": {}, "flags": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(data, dict):
            return empty
        if not isinstance(data.get("tools"), dict):
            data["tools"] = {}
        if not isinstance(data.get("flags"), dict):
            data["flags"] = {}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        data["schema"] = SCHEMA
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".tools-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
