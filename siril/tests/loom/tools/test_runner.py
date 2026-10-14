"""The external process runner: progress parsing (oracle replay), process control, exchange folders, logging."""

from __future__ import annotations

import io
import os
import signal
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from loomsiril.loom.tools import runner
from loomsiril.loom.tools.runner import (
    Progress,
    ToolCancelled,
    ToolFailed,
    ToolNotRunnable,
    ToolTimeout,
    exchange_folder,
    parse_ndjson_progress,
    parse_progress,
    parse_progress_line,
    progress_milestone_text,
    run_folder,
    run_tool,
)

from .fakebin import make_fake, pid_alive, read_argv, read_child_pid


def _progress_from_json(value: dict[str, Any] | None) -> Progress | None:
    if value is None:
        return None
    return Progress(float(value["percent"]), value["text"], value["done"], value["total"])


def _assert_gone(pid: int | None) -> None:
    """The child is gone once SIGKILL has been delivered; delivery is asynchronous, so allow a moment."""
    assert pid is not None
    deadline = time.monotonic() + 3.0
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not pid_alive(pid)


def _cancel_once_started(folder: Path, name: str, record: dict[str, float]) -> Any:
    """A cancel request that fires once the fake has started its child (or after 10 s, if it never does)."""

    def cancelled() -> bool:
        if "at" not in record and (read_child_pid(folder, name) is not None or time.monotonic() > record["t0"] + 10):
            record["at"] = time.monotonic()
        return "at" in record

    return cancelled


def _collector() -> tuple[list[tuple[float, str]], list[str], Any, Any]:
    progress: list[tuple[float, str]] = []
    log: list[str] = []
    return progress, log, (lambda pct, text: progress.append((pct, text))), (lambda line: log.append(line))


# --- progress parsing: replay of Loom's parseProgressLine and progressMilestoneText -----------------------------


def test_parse_progress_matches_recorded_loom_lines(oracle) -> None:
    cases = oracle("loom_progress")["cases"]
    parse_cases = {k: v for k, v in cases.items() if k.startswith("parse_")}
    assert len(parse_cases) >= 30
    for name, case in sorted(parse_cases.items()):
        got = parse_progress(case["line"])
        want = _progress_from_json(case["result"])
        assert got == want, f"{name}: {case['line']!r}"
        assert parse_progress_line(case["line"]) == (None if want is None else want.percent), name


def test_milestone_text_matches_recorded_loom_text(oracle) -> None:
    cases = oracle("loom_progress")["cases"]
    milestones = {k: v for k, v in cases.items() if k.startswith("milestone_")}
    assert len(milestones) >= 5
    for name, case in sorted(milestones.items()):
        progress = _progress_from_json(case["progress"])
        assert progress is not None, name
        assert progress_milestone_text(case["milestone"], progress) == case["result"], name


def test_stage_without_space_before_text_is_not_a_progress_line(oracle) -> None:
    case = oracle("loom_progress")["cases"]["parse_35"]
    assert case["line"] == "[ 45%]Stage (1/2)"
    assert parse_progress(case["line"]) is None


def test_ndjson_progress_lines() -> None:
    assert parse_ndjson_progress('{"type": "progress", "done": 42.5, "stage": "stars"}') == 42.5
    assert parse_ndjson_progress('{"type":"progress","done":0}') == 0.0
    assert parse_ndjson_progress('{"type": "progress", "done": 100}') == 100.0
    assert parse_ndjson_progress('{"type": "progress", "done": 101}') is None
    assert parse_ndjson_progress('{"type": "progress", "done": -1}') is None
    assert parse_ndjson_progress('{"type": "progress", "done": true}') is None
    assert parse_ndjson_progress('{"type": "progress", "done": "50"}') is None
    assert parse_ndjson_progress('{"type": "progress", "done": NaN}') is None
    assert parse_ndjson_progress('{"type": "log", "done": 50}') is None
    assert parse_ndjson_progress('{"type": "progress"') is None
    assert parse_ndjson_progress("[ 45%] Stage") is None
    assert parse_ndjson_progress("") is None
    assert parse_ndjson_progress(None) is None


def test_progress_formats_have_no_tile_counter_in_text() -> None:
    assert parse_progress("[ 45%] Stage name") == Progress(45.0, "Stage name", None, None)
    assert progress_milestone_text(50, Progress(52.0, "Stage name", None, None)) == "50% - Stage name"


# --- running tools ----------------------------------------------------------------------------------------------


def test_cr_separated_progress_and_crlf(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "crtool", stdout_lines=["[ 10%] Stage (1/10)", "[ 20%] Stage (2/10)"],
                     stderr_lines=["[ 30%] Stage (3/10)"], line_end="\r")
    progress, log, on_progress, log_fn = _collector()
    lines: list[tuple[str, str]] = []
    result = run_tool("crtool", [str(fake)], timeout=10, on_progress=on_progress, log=log_fn,
                      on_line=lambda stream, line: lines.append((stream, line)))
    assert result.stdout_lines == ["[ 10%] Stage (1/10)", "[ 20%] Stage (2/10)"]
    # the two streams are read concurrently, so only the set of updates is fixed, not their interleaving
    assert sorted(progress) == [(10.0, "Stage"), (20.0, "Stage"), (30.0, "Stage")]
    assert ("stderr", "[ 30%] Stage (3/10)") in lines
    assert len(lines) == 3


def test_crlf_is_one_line_break_and_split_across_reads(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "eol", stdout_lines=["a", "b", "", "c"], line_end="\r\n")
    result = run_tool("eol", [str(fake)], timeout=10)
    assert result.stdout_lines == ["a", "b", "", "c"]


def test_milestones_log_one_line_per_25_percent_step(tmp_path: Path) -> None:
    lines = ["[ 10%] Stage (1/100)", "[ 26%] Stage (26/100)", "[ 27%] Stage (27/100)", "[ 52%] Stage (52/100)",
             "[ 100%] Stage (100/100)"]
    fake = make_fake(tmp_path / "bin", "miles", stdout_lines=lines)
    progress, log, on_progress, log_fn = _collector()
    run_tool("miles", [str(fake)], timeout=10, on_progress=on_progress, log=log_fn)
    assert [p[0] for p in progress] == [10.0, 26.0, 27.0, 52.0, 100.0]
    milestone_lines = [ln for ln in log if "% - " in ln]
    assert milestone_lines == ["25% - Stage (26/100)", "50% - Stage (52/100)", "100% - Stage (100/100)"]


def test_ndjson_progress_on_stdout_feeds_the_callback(tmp_path: Path) -> None:
    lines = ['{"type": "progress", "done": 30}', '{"type": "log", "done": 99}', '{"type": "progress", "done": 30}',
             '{"type": "progress", "done": 60, "stage": "x"}']
    fake = make_fake(tmp_path / "bin", "ndjson", stdout_lines=lines)
    progress, log, on_progress, log_fn = _collector()
    run_tool("ndjson", [str(fake)], timeout=10, on_progress=on_progress, log=log_fn)
    assert progress == [(30.0, "working"), (60.0, "working")]
    assert [ln for ln in log if "% - " in ln] == ["25% - working", "50% - working"]


def test_no_shell_argv_is_passed_verbatim_and_logged(tmp_path: Path) -> None:
    argv_in = ["arg with spaces", "a;b", "$HOME", "`id`", "x|y", "*", ""]
    fake = make_fake(tmp_path / "bin", "shellcheck")
    _, log, _, log_fn = _collector()
    result = run_tool("shellcheck", [str(fake), *argv_in], timeout=10, log=log_fn)
    assert read_argv(tmp_path / "bin", "shellcheck") == [argv_in]
    assert result.returncode == 0
    assert len(log) == 1
    assert shlex.join([str(fake), *argv_in]) in log[0]
    assert "exit 0" in log[0] and " s" in log[0]


def test_non_zero_exit_raises_with_code_and_stderr_tail(tmp_path: Path) -> None:
    stderr = [f"err {i}" for i in range(25)]
    fake = make_fake(tmp_path / "bin", "bad", exit_code=3, stderr_lines=stderr)
    with pytest.raises(ToolFailed) as info:
        run_tool("bad", [str(fake)], timeout=10)
    message = str(info.value)
    assert "exit code 3" in message
    message_lines = message.splitlines()
    assert "err 24" in message_lines and "err 5" in message_lines
    assert "err 4" not in message_lines and "err 0" not in message_lines
    assert info.value.code == 3 and info.value.stderr_tail == stderr[-20:]


def test_accept_nonzero_returns_the_result(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "legacy", exit_code=2, stdout_lines=["out"], stderr_lines=["warn"])
    result = run_tool("legacy", [str(fake)], timeout=10, accept_nonzero=True)
    assert result.returncode == 2
    assert result.stdout_lines == ["out"]
    assert result.stderr_tail == ["warn"]
    assert result.duration >= 0


def test_timeout_kills_the_hanging_tool_and_its_child(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "hang", hang=True)
    started = time.monotonic()
    with pytest.raises(ToolTimeout) as info:
        run_tool("hang", [str(fake)], timeout=2.0)
    assert time.monotonic() - started < 7.0
    assert info.value.seconds == 2.0
    _assert_gone(read_child_pid(tmp_path / "bin", "hang"))


def test_tool_ignoring_sigterm_is_killed_after_the_grace_period(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "stubborn", hang=True, ignore_sigterm=True)
    started = time.monotonic()
    with pytest.raises(ToolTimeout):
        run_tool("stubborn", [str(fake)], timeout=2.0)
    elapsed = time.monotonic() - started
    assert 6.5 <= elapsed < 12.0
    _assert_gone(read_child_pid(tmp_path / "bin", "stubborn"))


def test_cancel_stops_the_tool_within_five_seconds(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "cancelme", hang=True)
    record = {"t0": time.monotonic()}
    with pytest.raises(ToolCancelled) as info:
        run_tool("cancelme", [str(fake)], timeout=60, cancelled=_cancel_once_started(tmp_path / "bin", "cancelme", record))
    assert info.value.tool == "cancelme"
    assert time.monotonic() - record["at"] < 5.0
    _assert_gone(read_child_pid(tmp_path / "bin", "cancelme"))


def test_exchange_folder_removed_on_success(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "ok", stdout_lines=["done"])
    with exchange_folder(tmp_path / "work", "ok") as folder:
        assert folder.is_dir()
        run_tool("ok", [str(fake), str(folder)], timeout=10)
    assert not folder.exists()


def test_exchange_folder_removed_on_failure(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "fail", exit_code=1)
    with pytest.raises(ToolFailed):
        with exchange_folder(tmp_path / "work", "fail") as folder:
            run_tool("fail", [str(fake), str(folder)], timeout=10)
    assert not folder.exists()


def test_exchange_folder_removed_on_timeout(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "slow", hang=True)
    with pytest.raises(ToolTimeout):
        with exchange_folder(tmp_path / "work", "slow") as folder:
            run_tool("slow", [str(fake), str(folder)], timeout=2.0)
    assert not folder.exists()


def test_exchange_folder_removed_on_cancel(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "stop", hang=True)
    record = {"t0": time.monotonic()}
    with pytest.raises(ToolCancelled):
        with exchange_folder(tmp_path / "work", "stop") as folder:
            run_tool("stop", [str(fake), str(folder)], timeout=60,
                     cancelled=_cancel_once_started(tmp_path / "bin", "stop", record))
    assert not folder.exists()


def test_run_folder_numbers_are_the_next_free_integers(tmp_path: Path) -> None:
    first = run_folder(tmp_path, "tesseract")
    second = run_folder(tmp_path, "tesseract")
    assert first == tmp_path / "tools" / "tesseract-1" and second == tmp_path / "tools" / "tesseract-2"
    first.rmdir()
    assert run_folder(tmp_path, "tesseract") == first
    with pytest.raises(ValueError):
        run_folder(tmp_path, "../escape")


def test_windows_path_uses_create_no_window_and_no_session(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    class FakeProc:
        pid = 4242
        returncode = 0

        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"[ 50%] Stage (5/10)\r\n")
            self.stderr = io.BytesIO(b"")

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def popen(argv: list[str], **kwargs: Any) -> FakeProc:
        calls.append((list(argv), dict(kwargs)))
        return FakeProc()

    progress, log, on_progress, log_fn = _collector()
    result = run_tool("wintool", ["C:\\Tools\\a b.exe", "x y"], timeout=10, platform="win32", popen=popen,
                      on_progress=on_progress, log=log_fn)
    argv, kwargs = calls[0]
    assert argv == ["C:\\Tools\\a b.exe", "x y"]
    assert kwargs["creationflags"] == 0x08000000
    assert "start_new_session" not in kwargs
    assert kwargs["shell"] is False and kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["stderr"] == subprocess.PIPE
    assert progress == [(50.0, "Stage")]
    assert result.stdout_lines == ["[ 50%] Stage (5/10)"]
    assert subprocess.list2cmdline(["C:\\Tools\\a b.exe", "x y"]) in log[-1] and "exit 0" in log[-1]


def test_posix_path_starts_a_new_session(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class QuietProc:
        pid = 999999
        returncode = 0
        stdout = io.BytesIO(b"")
        stderr = io.BytesIO(b"")

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def popen(argv: list[str], **kwargs: Any) -> QuietProc:
        seen.update(kwargs)
        return QuietProc()

    run_tool("posix", ["/bin/true"], timeout=10, platform="linux", popen=popen)
    assert seen["start_new_session"] is True
    assert "creationflags" not in seen


def test_popen_failure_is_logged_and_wrapped(tmp_path: Path) -> None:
    _, log, _, log_fn = _collector()

    def popen(argv: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    with pytest.raises(ToolNotRunnable) as info:
        run_tool("missing", [str(tmp_path / "nope")], timeout=10, popen=popen, log=log_fn)
    assert isinstance(info.value.__cause__, FileNotFoundError)
    assert len(log) == 1 and "nope" in log[0]


def test_missing_tool_names_the_tool_and_path(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-tool"
    with pytest.raises(ToolNotRunnable) as info:
        run_tool("ghost", [str(missing)], timeout=10)
    assert info.value.tool == "ghost" and info.value.path == str(missing)
    assert isinstance(info.value.__cause__, FileNotFoundError)
    assert "ghost" in str(info.value) and str(missing) in str(info.value)


def test_non_executable_file_raises_tool_not_runnable(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.write_text("not a program\n", encoding="utf-8")
    plain.chmod(0o644)
    with pytest.raises(ToolNotRunnable) as info:
        run_tool("plain", [str(plain)], timeout=10)
    assert isinstance(info.value.__cause__, PermissionError)
    assert str(plain) in str(info.value)


def test_cancel_ends_within_five_seconds_when_sigterm_is_ignored(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "stubcancel", hang=True, ignore_sigterm=True)
    record = {"t0": time.monotonic()}
    with pytest.raises(ToolCancelled):
        run_tool("stubcancel", [str(fake)], timeout=60,
                 cancelled=_cancel_once_started(tmp_path / "bin", "stubcancel", record))
    assert time.monotonic() - record["at"] < 5.0
    _assert_gone(read_child_pid(tmp_path / "bin", "stubcancel"))


def test_grandchild_holding_the_pipes_does_not_delay_return(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "orphan", orphan_child=True, stdout_lines=["[ 50%] Stage (1/2)"])
    started = time.monotonic()
    result = run_tool("orphan", [str(fake)], timeout=10)
    assert time.monotonic() - started < 4.0
    assert result.returncode == 0 and result.stdout_lines == ["[ 50%] Stage (1/2)"]
    _assert_gone(read_child_pid(tmp_path / "bin", "orphan"))


class _NeverExitsProc:
    """A Windows-style process that exits only once kill() has been called."""

    pid = 4343

    def __init__(self) -> None:
        self.stdout = io.BytesIO(b"")
        self.stderr = io.BytesIO(b"")
        self.calls: list[str] = []
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")
        self.returncode = 1

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("never-exits", timeout or 0)
        return self.returncode


def test_windows_timeout_terminates_then_kills_without_posix_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(signal, "SIGKILL")  # the Windows build of signal has no SIGKILL
    proc = _NeverExitsProc()
    with pytest.raises(ToolTimeout):
        run_tool("winhang", ["winhang.exe"], timeout=0.3, platform="win32", popen=lambda argv, **kw: proc)
    assert proc.calls == ["terminate", "kill"]


def test_windows_cancel_terminates_then_kills_without_posix_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(signal, "SIGKILL")
    proc = _NeverExitsProc()
    started = time.monotonic()
    with pytest.raises(ToolCancelled):
        run_tool("wincancel", ["wincancel.exe"], timeout=60, platform="win32", popen=lambda argv, **kw: proc,
                 cancelled=lambda: time.monotonic() - started > 0.2)
    assert proc.calls == ["terminate", "kill"]


def test_line_splitter_handles_crlf_split_between_chunks() -> None:
    splitter = runner._LineSplitter()
    assert splitter.feed("a\r") == ["a"]
    assert splitter.feed("\nb\r\nc") == ["b"]
    assert splitter.feed("") == []
    assert splitter.flush() == ["c"]
    assert splitter.flush() == []


class _TaskkillRecorder:
    """Stands in for subprocess.run: records the call in the process's call log, then behaves as configured."""

    def __init__(self, proc: _NeverExitsProc, raises: BaseException | None = None, delay: float = 0.0) -> None:
        self.proc = proc
        self.raises = raises
        self.delay = delay
        self.argv: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.proc.calls.append("taskkill")
        self.argv.append(list(argv))
        self.kwargs.append(dict(kwargs))
        if self.delay:
            time.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, 1)


def test_win32_timeout_runs_taskkill_tree_before_terminate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(signal, "SIGKILL")
    monkeypatch.delenv("SystemRoot", raising=False)
    proc = _NeverExitsProc()
    rec = _TaskkillRecorder(proc)
    with pytest.raises(ToolTimeout):
        run_tool("winhang", ["winhang.exe"], timeout=0.3, platform="win32", popen=lambda argv, **kw: proc,
                 taskkill_runner=rec)
    assert proc.calls == ["taskkill", "terminate", "kill"]
    assert rec.argv == [["taskkill", "/PID", "4343", "/T", "/F"]]
    kw = rec.kwargs[0]
    assert kw["shell"] is False and kw["creationflags"] == 0x08000000
    assert kw["stdin"] == subprocess.DEVNULL and kw["stdout"] == subprocess.DEVNULL
    assert kw["stderr"] == subprocess.DEVNULL and kw["timeout"] == 3.0


def test_win32_taskkill_uses_system_root_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(signal, "SIGKILL")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    proc = _NeverExitsProc()
    rec = _TaskkillRecorder(proc)
    with pytest.raises(ToolTimeout):
        run_tool("winhang", ["winhang.exe"], timeout=0.3, platform="win32", popen=lambda argv, **kw: proc,
                 taskkill_runner=rec)
    assert rec.argv == [["C:\\Windows\\System32\\taskkill.exe", "/PID", "4343", "/T", "/F"]]
    assert proc.calls[:2] == ["taskkill", "terminate"]


@pytest.mark.parametrize("error", [OSError("no taskkill"), subprocess.TimeoutExpired("taskkill", 3.0)])
def test_win32_failing_taskkill_does_not_break_the_cancel(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    monkeypatch.delattr(signal, "SIGKILL")
    proc = _NeverExitsProc()
    rec = _TaskkillRecorder(proc, raises=error)
    started = time.monotonic()
    with pytest.raises(ToolCancelled):
        run_tool("wincancel", ["wincancel.exe"], timeout=60, platform="win32", popen=lambda argv, **kw: proc,
                 cancelled=lambda: time.monotonic() - started > 0.2, taskkill_runner=rec)
    assert proc.calls == ["taskkill", "terminate", "kill"]


def test_win32_cancel_with_slow_taskkill_still_ends_within_five_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(signal, "SIGKILL")
    proc = _NeverExitsProc()
    rec = _TaskkillRecorder(proc, delay=3.0)
    started = time.monotonic()
    requested = {"at": None}

    def cancelled() -> bool:
        if requested["at"] is None and time.monotonic() - started > 0.2:
            requested["at"] = time.monotonic()
        return requested["at"] is not None

    with pytest.raises(ToolCancelled):
        run_tool("slowkill", ["slowkill.exe"], timeout=60, platform="win32", popen=lambda argv, **kw: proc,
                 cancelled=cancelled, taskkill_runner=rec)
    assert time.monotonic() - requested["at"] < 5.0
    assert proc.calls == ["taskkill", "terminate", "kill"]


def test_taskkill_is_not_called_on_posix(tmp_path: Path) -> None:
    fake = make_fake(tmp_path / "bin", "posixhang", hang=True)
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> None:
        calls.append(list(argv))

    with pytest.raises(ToolTimeout):
        run_tool("posixhang", [str(fake)], timeout=1.0, taskkill_runner=runner)
    assert calls == []


def test_taskkill_is_not_called_for_a_process_that_already_exited() -> None:
    class Exited(_NeverExitsProc):
        def poll(self) -> int | None:
            return 0

    proc = Exited()
    calls: list[list[str]] = []
    runner._taskkill_tree(proc, lambda argv, **kw: calls.append(list(argv)))
    assert calls == []


def test_win32_no_kill_when_the_process_exits_after_terminate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process that exits after terminate() within the grace period is not killed."""
    monkeypatch.delattr(signal, "SIGKILL")

    class ExitsOnTerminate(_NeverExitsProc):
        def terminate(self) -> None:
            super().terminate()
            self.returncode = 0

    proc = ExitsOnTerminate()
    rec = _TaskkillRecorder(proc)
    with pytest.raises(ToolTimeout):
        run_tool("winterm", ["winterm.exe"], timeout=0.3, platform="win32", popen=lambda argv, **kw: proc,
                 taskkill_runner=rec)
    assert proc.calls == ["taskkill", "terminate"]
