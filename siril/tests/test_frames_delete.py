"""Frame Selector deletion: loomsiril.frames_delete, ported from FrameSelector.js 1246-1418.

These tests need neither Siril, PixInsight, node nor a display. Files and the audit log live under pytest's tmp_path.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from loomsiril import frames as F
from loomsiril import frames_cache as C
from loomsiril import frames_delete as D

CREATED_MS = 1_700_000_000_123  # 2023-11-14T22:13:20.123Z
NOW = datetime(2026, 10, 15, 10, 52, 11, 123000, tzinfo=timezone.utc)


def _write(path: Path, data: bytes) -> str:
    path.write_bytes(data)
    return str(path)


def _row(path: str, state: str, override: str | None = None, reasons: list[str] | None = None) -> dict[str, Any]:
    """A row as the Frame Selector holds it, with the identity read from the file as the scan would have."""
    ident = C.file_identity(path)
    assert ident is not None
    return {"path": path, "channel": "L", "state": state, "override": override,
            "reasons": reasons or [], "digest": ident["digest"], "size": ident["size"], "mtime": ident["mtime"]}


def _manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = F.build_manifest(rows)
    manifest["created"] = CREATED_MS
    return manifest


def _lines(path: str) -> list[str]:
    return Path(path).read_text(encoding="utf-8").split("\n")[:-1]


@pytest.fixture
def home(tmp_path: Path) -> str:
    folder = tmp_path / "home"
    folder.mkdir()
    return str(folder)


@pytest.fixture
def frames_in(tmp_path: Path) -> dict[str, str]:
    """Four frames: three rejected, one approved (kept)."""
    folder = tmp_path / "frames"
    folder.mkdir()
    return {name: _write(folder / name, name.encode() * 10)
            for name in ["a.fit", "b.fit", "c.fit", "keep.fit"]}


def _rejected_rows(frames_in: dict[str, str]) -> list[dict[str, Any]]:
    return [_row(frames_in["a.fit"], F.REJECTED, reasons=["FWHM 4.1 > 3.0"]),
            _row(frames_in["b.fit"], F.REJECTED, reasons=["eccentricity 0.9 > 0.6", "stars 3 < 20"]),
            _row(frames_in["c.fit"], F.REJECTED),
            _row(frames_in["keep.fit"], F.APPROVED)]


def test_deletes_only_the_rejected_frames(frames_in: dict[str, str], home: str) -> None:
    manifest = _manifest(_rejected_rows(frames_in))
    result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    assert result["deleted"] == 3
    assert result["skipped"] == 0 and result["failed"] == 0 and result["stopped"] is False
    for name in ["a.fit", "b.fit", "c.fit"]:
        assert not os.path.exists(frames_in[name])
    assert os.path.exists(frames_in["keep.fit"])


def test_log_path_and_header_then_entries_then_outcomes(frames_in: dict[str, str], home: str) -> None:
    manifest = _manifest(_rejected_rows(frames_in))
    result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    log_path = result["logPath"]
    assert log_path == os.path.join(home, "Siril", "Loom-frame-selector", "deleted-2026-10-15T10-52-11-123Z.log")
    lines = _lines(log_path)
    digest_a = manifest["entries"][0]["digest"]
    digest_b = manifest["entries"][1]["digest"]
    digest_c = manifest["entries"][2]["digest"]
    assert lines == [
        "# Loom Frame Selector",
        "# built 2023-11-14T22:13:20.123Z",
        "# 3 frame(s) condemned",
        f"{frames_in['a.fit']}\t{digest_a}\trejected\tFWHM 4.1 > 3.0",
        f"{frames_in['b.fit']}\t{digest_b}\trejected\teccentricity 0.9 > 0.6; stars 3 < 20",
        f"{frames_in['c.fit']}\t{digest_c}\trejected\t",
        f"deleted\t{frames_in['a.fit']}\t{digest_a}\t",
        f"deleted\t{frames_in['b.fit']}\t{digest_b}\t",
        f"deleted\t{frames_in['c.fit']}\t{digest_c}\t",
    ]


def test_unwritable_log_folder_deletes_nothing(frames_in: dict[str, str], home: str) -> None:
    (Path(home) / "Siril").write_text("a file where the log folder should be")  # makedirs cannot succeed
    manifest = _manifest(_rejected_rows(frames_in))
    messages: list[str] = []
    result = D.execute(manifest, home=home, now=NOW, log=messages.append)
    assert result["deleted"] == 0 and result["logPath"] is None and result["stopped"] is False
    assert all(os.path.exists(frames_in[n]) for n in ["a.fit", "b.fit", "c.fit", "keep.fit"])
    assert any("the log could not be written" in m for m in messages)
    assert all(e["outcome"] is None for e in manifest["entries"])


def test_log_folder_made_read_only_deletes_nothing(frames_in: dict[str, str], home: str) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    folder = Path(home) / "Siril" / "Loom-frame-selector"
    folder.mkdir(parents=True)
    folder.chmod(0o500)
    try:
        result = D.execute(_manifest(_rejected_rows(frames_in)), home=home, now=NOW, log=lambda _m: None)
    finally:
        folder.chmod(0o700)
    assert result["deleted"] == 0 and result["logPath"] is None
    assert os.path.exists(frames_in["a.fit"])


def test_journal_failing_after_the_first_file_stops_the_run(frames_in: dict[str, str], home: str,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def failing(path: str, manifest: dict[str, Any], file_path: str, log: Any = None) -> bool:
        calls.append(file_path)
        return False

    monkeypatch.setattr(D, "append_outcome", failing)
    manifest = _manifest(_rejected_rows(frames_in))
    result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    assert result["stopped"] is True
    assert result["deleted"] == 1
    assert calls == [frames_in["a.fit"]]
    assert not os.path.exists(frames_in["a.fit"])
    assert os.path.exists(frames_in["b.fit"]) and os.path.exists(frames_in["c.fit"])
    assert os.path.exists(frames_in["keep.fit"])
    assert [e["outcome"] for e in manifest["entries"]] == ["deleted", None, None]


def test_changed_file_is_skipped_and_reported(frames_in: dict[str, str], home: str) -> None:
    manifest = _manifest(_rejected_rows(frames_in))
    Path(frames_in["b.fit"]).write_bytes(b"rewritten with other bytes")
    messages: list[str] = []
    result = D.execute(manifest, home=home, now=NOW, log=messages.append)
    assert result["skipped"] == 1 and result["deleted"] == 2
    assert os.path.exists(frames_in["b.fit"])
    entry = manifest["entries"][1]
    assert entry["outcome"] == "skipped"
    assert entry["detail"] == "changed on disk since it was measured"
    assert any(m.startswith("skipped " + frames_in["b.fit"]) for m in messages)
    last_lines = _lines(result["logPath"])[-3:]
    assert last_lines[0].startswith(f"deleted\t{frames_in['a.fit']}")
    assert last_lines[1] == f"skipped\t{frames_in['b.fit']}\t{entry['digest']}\tchanged on disk since it was measured"


def test_vanished_file_is_skipped_and_reported(frames_in: dict[str, str], home: str) -> None:
    manifest = _manifest(_rejected_rows(frames_in))
    os.remove(frames_in["c.fit"])
    result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    assert result["skipped"] == 1 and result["deleted"] == 2
    assert manifest["entries"][2]["outcome"] == "skipped"


def test_unreadable_file_is_skipped_and_reported(frames_in: dict[str, str], home: str) -> None:
    manifest = _manifest(_rejected_rows(frames_in))
    # A directory where the frame was: it is there, so it is stat-ed, but it cannot be read as a file.
    os.remove(frames_in["a.fit"])
    os.mkdir(frames_in["a.fit"])
    result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    assert result["skipped"] == 1 and result["deleted"] == 2
    assert os.path.isdir(frames_in["a.fit"])
    assert manifest["entries"][0]["outcome"] == "skipped"


def test_permission_denied_file_is_skipped_and_reported(frames_in: dict[str, str], home: str) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads files whatever their mode")
    manifest = _manifest(_rejected_rows(frames_in))
    os.chmod(frames_in["c.fit"], 0o000)
    try:
        result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    finally:
        os.chmod(frames_in["c.fit"], 0o600)
    assert result["skipped"] == 1 and result["deleted"] == 2
    assert manifest["entries"][2]["outcome"] == "skipped"


def test_removal_error_is_recorded_as_failed(frames_in: dict[str, str], home: str,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    def remove(path: str) -> None:
        if path == frames_in["b.fit"]:
            raise PermissionError("denied")
        os.unlink(path)

    monkeypatch.setattr(D, "_remove", remove)
    manifest = _manifest(_rejected_rows(frames_in))
    result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    assert result["deleted"] == 2 and result["failed"] == 1 and result["stopped"] is False
    failed = manifest["entries"][1]
    assert failed["outcome"] == "failed" and "denied" in failed["detail"]
    assert os.path.exists(frames_in["b.fit"])
    assert _lines(result["logPath"])[-1] == "deleted\t" + frames_in["c.fit"] + "\t" + manifest["entries"][2]["digest"] + "\t"


def test_override_is_recorded_beside_the_automatic_verdict(frames_in: dict[str, str], home: str) -> None:
    rows = [_row(frames_in["a.fit"], F.APPROVED, override=F.CONDEMNED, reasons=["passed"]),
            _row(frames_in["b.fit"], F.REJECTED, override=F.RESCUED, reasons=["FWHM 4.1 > 3.0"])]
    manifest = _manifest(rows)
    assert [e["path"] for e in manifest["entries"]] == [frames_in["a.fit"]]
    result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    assert result["deleted"] == 1
    lines = _lines(result["logPath"])
    assert lines[3] == f"{frames_in['a.fit']}\t{manifest['entries'][0]['digest']}\tcondemned\tpassed"


def test_on_progress_is_called_before_each_file_after_the_log_is_written(frames_in: dict[str, str], home: str) -> None:
    seen: list[tuple[int, int, str, bool]] = []

    def progress(done: int, total: int, path: str) -> None:
        log_exists = any(name.endswith(".log") for name in os.listdir(os.path.join(home, "Siril", "Loom-frame-selector")))
        seen.append((done, total, path, os.path.exists(path) and log_exists))

    result = D.execute(_manifest(_rejected_rows(frames_in)), progress, home=home, now=NOW, log=lambda _m: None)
    assert result["deleted"] == 3
    assert seen == [(1, 3, frames_in["a.fit"], True), (2, 3, frames_in["b.fit"], True), (3, 3, frames_in["c.fit"], True)]


def test_nothing_pending_writes_no_log(frames_in: dict[str, str], home: str) -> None:
    rows = [_row(frames_in["keep.fit"], F.APPROVED)]
    result = D.execute(_manifest(rows), home=home, now=NOW, log=lambda _m: None)
    assert result == {"deleted": 0, "skipped": 0, "failed": 0, "stopped": False, "logPath": None}
    assert not os.path.exists(os.path.join(home, "Siril"))


def test_log_dir_is_under_the_home_folder(home: str) -> None:
    assert D.log_dir(home) == os.path.join(home, "Siril", "Loom-frame-selector")


def test_same_time_twice_never_overwrites_the_first_log(frames_in: dict[str, str], home: str,
                                                        tmp_path: Path) -> None:
    first = D.execute(_manifest(_rejected_rows(frames_in)), home=home, now=NOW, log=lambda _m: None)
    first_bytes = Path(first["logPath"]).read_bytes()
    second_frames = {n: _write(tmp_path / ("again-" + n), n.encode() * 3) for n in ["a.fit", "b.fit", "c.fit"]}
    rows = [_row(second_frames[n], F.REJECTED) for n in ["a.fit", "b.fit", "c.fit"]]
    second = D.execute(_manifest(rows), home=home, now=NOW, log=lambda _m: None)
    assert second["deleted"] == 3
    assert second["logPath"] != first["logPath"]
    assert second["logPath"].endswith("deleted-2026-10-15T10-52-11-123Z-2.log")
    assert Path(first["logPath"]).read_bytes() == first_bytes
    assert "frame(s) condemned" in Path(second["logPath"]).read_text(encoding="utf-8")


def test_non_utf8_file_name_is_written_back_as_its_bytes(tmp_path: Path, home: str) -> None:
    folder = tmp_path / "frames"
    folder.mkdir()
    name = os.fsdecode(b"caf\xe9-light.fit")
    path = os.path.join(str(folder), name)
    Path(path).write_bytes(b"x" * 40)
    rows = [_row(path, F.REJECTED)]
    messages: list[str] = []
    result = D.execute(_manifest(rows), home=home, now=NOW, log=messages.append)
    assert result["deleted"] == 1 and result["stopped"] is False
    assert not os.path.exists(path)
    raw = Path(result["logPath"]).read_bytes()
    assert os.fsencode(path) in raw
    assert all("could not" not in m for m in messages)


def test_unencodable_file_name_deletes_nothing(frames_in: dict[str, str], home: str,
                                               tmp_path: Path) -> None:
    lone = os.path.join(str(tmp_path / "frames"), "\ud800lone.fit")
    rows = [_row(frames_in["a.fit"], F.REJECTED), {**_row(frames_in["b.fit"], F.REJECTED), "path": lone}]
    messages: list[str] = []
    result = D.execute(_manifest(rows), home=home, now=NOW, log=messages.append)
    assert result["deleted"] == 0 and result["logPath"] is None
    assert os.path.exists(frames_in["a.fit"]) and os.path.exists(frames_in["b.fit"])
    assert any("could not write the deletion log" in m for m in messages)


def test_symlink_frame_removes_the_link_and_keeps_the_target(tmp_path: Path, home: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = _write(outside / "real.fit", b"real frame" * 9)
    folder = tmp_path / "frames"
    folder.mkdir()
    link = str(folder / "link.fit")
    os.symlink(target, link)
    result = D.execute(_manifest([_row(link, F.REJECTED)]), home=home, now=NOW, log=lambda _m: None)
    assert result["deleted"] == 1
    assert not os.path.lexists(link)
    assert os.path.exists(target) and Path(target).read_bytes() == b"real frame" * 9


def test_journal_failing_mid_run_on_a_read_only_log_stops_it(frames_in: dict[str, str], home: str) -> None:
    if os.geteuid() == 0:
        pytest.skip("root writes read-only files")
    holder: dict[str, str] = {}
    original_remove = D._remove

    def remove_then_lock(path: str) -> None:
        original_remove(path)
        if "log" not in holder:
            holder["log"] = os.path.join(home, "Siril", "Loom-frame-selector")
            log = [os.path.join(holder["log"], n) for n in os.listdir(holder["log"])][0]
            os.chmod(log, 0o400)

    D._remove = remove_then_lock  # type: ignore[assignment]
    try:
        manifest = _manifest(_rejected_rows(frames_in))
        result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    finally:
        D._remove = original_remove
        for n in os.listdir(holder["log"]):
            os.chmod(os.path.join(holder["log"], n), 0o600)
    assert result["stopped"] is True and result["deleted"] == 1
    assert not os.path.exists(frames_in["a.fit"])
    assert os.path.exists(frames_in["b.fit"]) and os.path.exists(frames_in["c.fit"])
    assert [e["outcome"] for e in manifest["entries"]] == ["deleted", None, None]


def test_journal_failure_in_the_skip_branch_stops_the_run(frames_in: dict[str, str], home: str,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    original = D.append_outcome

    def fail_on_skip(path: str, manifest: dict[str, Any], file_path: str, log: Any = None) -> bool:
        entry = next(e for e in manifest["entries"] if e["path"] == file_path)
        if entry["outcome"] == "skipped":
            return False
        return original(path, manifest, file_path, log)

    monkeypatch.setattr(D, "append_outcome", fail_on_skip)
    manifest = _manifest(_rejected_rows(frames_in))
    Path(frames_in["a.fit"]).write_bytes(b"changed bytes here")
    result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    assert result["stopped"] is True
    assert result["skipped"] == 1 and result["deleted"] == 0
    assert os.path.exists(frames_in["a.fit"]) and os.path.exists(frames_in["b.fit"])
    assert os.path.exists(frames_in["c.fit"])


def test_resumed_manifest_acts_only_on_pending_entries(frames_in: dict[str, str], home: str) -> None:
    manifest = _manifest(_rejected_rows(frames_in))
    F.record_outcome(manifest, frames_in["a.fit"], "deleted", "")  # already done in an earlier run
    F.record_outcome(manifest, frames_in["b.fit"], "failed", "denied")  # already failed; not retried
    result = D.execute(manifest, home=home, now=NOW, log=lambda _m: None)
    assert result["deleted"] == 1 and result["failed"] == 0 and result["skipped"] == 0
    assert os.path.exists(frames_in["a.fit"]) and os.path.exists(frames_in["b.fit"])
    assert not os.path.exists(frames_in["c.fit"])
    assert [e["outcome"] for e in manifest["entries"]] == ["deleted", "failed", "deleted"]
    lines = _lines(result["logPath"])
    assert lines[2] == "# 3 frame(s) condemned"
    assert lines[-1].startswith(f"deleted\t{frames_in['c.fit']}\t")


def test_folder_fsync_failure_does_not_stop_the_deletion(frames_in: dict[str, str], home: str,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    def no_folder(folder: str) -> int:
        raise PermissionError("cannot open a directory here")

    monkeypatch.setattr(D, "_open_folder", no_folder)
    result = D.execute(_manifest(_rejected_rows(frames_in)), home=home, now=NOW, log=lambda _m: None)
    assert result["deleted"] == 3 and result["stopped"] is False and result["logPath"] is not None
    assert not any(os.path.exists(frames_in[n]) for n in ["a.fit", "b.fit", "c.fit"])
    lines = _lines(result["logPath"])
    assert len(lines) == 3 + 3 + 3
    assert lines[-1].startswith(f"deleted\t{frames_in['c.fit']}\t")


def test_log_folder_that_is_a_regular_file_is_reported_once(frames_in: dict[str, str], home: str) -> None:
    folder = Path(home) / "Siril"
    folder.mkdir()
    (folder / "Loom-frame-selector").write_text("not a folder")
    messages: list[str] = []
    result = D.execute(_manifest(_rejected_rows(frames_in)), home=home, now=NOW, log=messages.append)
    assert result["deleted"] == 0 and result["logPath"] is None and result["stopped"] is False
    assert all(os.path.exists(frames_in[n]) for n in ["a.fit", "b.fit", "c.fit", "keep.fit"])
    assert sum("could not write the deletion log" in m for m in messages) == 1
    assert sum("nothing deleted" in m for m in messages) == 1
    assert len(messages) == 2
    assert (folder / "Loom-frame-selector").read_text() == "not a folder"
