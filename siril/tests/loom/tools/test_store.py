"""The remembered-tools store (tools.json): location, round trip, tolerance and the temp-folder guard."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from loomsiril.loom.tools.resolver import Found
from loomsiril.loom.tools.store import ToolStore


@pytest.fixture
def fake_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "system-temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(folder))
    return folder


def _found(path: str = "/opt/rc-astro/rc-astro") -> Found:
    return Found(tool_id="rcastro", path=path, source="known", version="9.9", probe_summary="schemaVersion 5")


def test_store_lives_at_loomsiril_tools_json_under_the_config_dir(tmp_path: Path, fake_temp: Path) -> None:
    store = ToolStore(tmp_path / "config")
    assert store.path == tmp_path / "config" / "loomsiril" / "tools.json"
    store.remember(_found(), 123, 456)
    assert store.path.is_file()


def test_remember_then_remembered_round_trip(tmp_path: Path, fake_temp: Path) -> None:
    store = ToolStore(tmp_path / "config")
    assert store.remembered("rcastro") is None
    store.remember(_found(), 123, 456789)
    record = store.remembered("rcastro")
    assert record == {
        "path": "/opt/rc-astro/rc-astro",
        "source": "known",
        "version": "9.9",
        "probe_summary": "schemaVersion 5",
        "size": 123,
        "mtime": 456789,
        "probed": "",
    }
    assert json.loads(store.path.read_text(encoding="utf-8"))["tools"]["rcastro"]["size"] == 123


def test_forget_removes_only_that_tool(tmp_path: Path, fake_temp: Path) -> None:
    store = ToolStore(tmp_path / "config")
    store.remember(_found(), 1, 2)
    store.remember(Found("syqon-cli", "/usr/bin/syqon-cli", "known", "1", "exit 0"), 3, 4)
    store.forget("rcastro")
    assert store.remembered("rcastro") is None
    assert store.remembered("syqon-cli") is not None
    store.forget("never-stored")  # no error


def test_flags_round_trip(tmp_path: Path, fake_temp: Path) -> None:
    store = ToolStore(tmp_path / "config")
    assert store.get_flag("studioPrism2Unavailable") is None
    store.set_flag("studioPrism2Unavailable", True)
    assert store.get_flag("studioPrism2Unavailable") is True
    store.remember(_found(), 1, 2)
    assert store.get_flag("studioPrism2Unavailable") is True


def test_missing_or_corrupt_file_reads_as_empty(tmp_path: Path, fake_temp: Path) -> None:
    store = ToolStore(tmp_path / "config")
    assert store.remembered("rcastro") is None
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{ not json", encoding="utf-8")
    assert store.remembered("rcastro") is None
    store.remember(_found(), 1, 2)  # a corrupt file is replaced, not fatal
    assert store.remembered("rcastro") is not None


def test_refused_under_the_temporary_folder(tmp_path: Path, fake_temp: Path) -> None:
    with pytest.raises(ValueError):
        ToolStore(fake_temp / "config")
    assert not (fake_temp / "config").exists()


def test_refused_for_the_real_temporary_folder() -> None:
    with pytest.raises(ValueError):
        ToolStore(tempfile.gettempdir())


def test_probe_date_is_recorded(tmp_path: Path, fake_temp: Path) -> None:
    store = ToolStore(tmp_path / "config")
    store.remember(_found(), 1, 2, probed="2026-10-13T09:00:00+00:00")
    assert store.remembered("rcastro")["probed"] == "2026-10-13T09:00:00+00:00"


def test_unchanged_record_is_not_rewritten(tmp_path: Path, fake_temp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ToolStore(tmp_path / "config")
    store.remember(_found(), 1, 2, probed="d1")
    saves: list[dict] = []
    original = ToolStore._save

    def counting(self: ToolStore, data: dict) -> None:
        saves.append(data)
        original(self, data)

    monkeypatch.setattr(ToolStore, "_save", counting)
    store.remember(_found(), 1, 2, probed="d1")  # identical record
    assert saves == []
    store.remember(_found(), 1, 2, probed="d2")  # the probe date changed
    assert len(saves) == 1


def test_save_leaves_no_temporary_files_behind(tmp_path: Path, fake_temp: Path) -> None:
    store = ToolStore(tmp_path / "config")
    store.remember(_found(), 1, 2)
    store.set_flag("x", 1)
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["tools.json"]


def test_store_write_errors_reach_the_caller(tmp_path: Path, fake_temp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ToolStore(tmp_path / "config")

    def broken(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(ToolStore, "_save", broken)
    with pytest.raises(OSError):
        store.remember(_found(), 1, 2)  # the store itself reports the error; the resolver handles it
