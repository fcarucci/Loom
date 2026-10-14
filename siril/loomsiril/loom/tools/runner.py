"""The one process runner for every external program the pipeline starts.

Rules shared by all tools:

* argv is passed as a list and started with ``shell=False``; nothing is ever joined into a shell string.
* On POSIX the tool gets its own session and process group (``start_new_session=True``), so a timeout or a
  cancel stops the tool together with any children it spawned. On Windows the tool is started with
  ``CREATE_NO_WINDOW`` and stopped with ``terminate``/``kill``.
* stdout and stderr are read concurrently (one reader thread per stream). Both streams are split on CR, LF and
  CRLF; every line goes to the progress parser, and the last 20 stderr lines are kept.
* Progress: ``[ 45%] Stage (12/30)``, ``[ 45%] Stage``, ``[CLI] Progress: 32%``, ``model 42%`` (Loom's formats,
  lib/StepsSyqon.js:607-646) and rc-astro NDJSON lines ``{"type": "progress", "done": 0-100, ...}``. Every change
  of percentage calls ``on_progress(percent, text)``; ``log`` gets one line at each 25% step crossed.
* Timeout: SIGTERM to the process group, then SIGKILL after a 5 s grace period. Cancel: the same with a 2 s
  grace period, so a cancelled run ends well within 5 s even when the tool ignores SIGTERM.
* Windows: a stop first runs ``taskkill /PID <pid> /T /F`` while the leader is alive (it ends the whole tree; its
  exit code is ignored), then ``Popen.terminate()``, then ``Popen.kill()`` after the grace period. Without
  taskkill, ``terminate()`` would end only the leader and leave its children running.
* Non-zero exit raises ``ToolFailed`` unless ``accept_nonzero=True``.
* Each run is logged once with its command line, duration and exit code (or the reason it was stopped).

The per-tool exchange folder ``<work_dir>/tools/<tool>-<n>/`` is created by ``run_folder`` and removed by the
``exchange_folder`` context manager on every exit path.

Lines of stdout and stderr reach ``on_line(stream, line)`` (``stream`` is ``"stdout"`` or ``"stderr"``) in the
order they arrive; ``on_progress`` and ``on_line`` run on the calling thread.
"""

from __future__ import annotations

import codecs
import json
import math
import ntpath
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

CREATE_NO_WINDOW = 0x08000000
TIMEOUT_GRACE_SECONDS = 5.0
CANCEL_GRACE_SECONDS = 2.0
TASKKILL_TIMEOUT_SECONDS = 3.0
POLL_SECONDS = 0.1
STDERR_TAIL_LINES = 20
MILESTONE_STEP = 25
_READ_CHUNK = 65536

# JavaScript's \s and String.prototype.trim() whitespace set (ECMAScript WhiteSpace and LineTerminator).
_JS_WS_CHARS = "\t\n\v\f\r              " \
    "    　﻿"
_WS = "[" + re.escape(_JS_WS_CHARS) + "]"
_DOT = r"[^\n\r  ]"
_NEWLINE = re.compile(r"\r\n|\r|\n")

# [ 45%] Stage name (12/30)
_RE_STAGE_COUNTED = re.compile(
    r"\[" + _WS + r"*([0-9]+)" + _WS + r"*%\]" + _WS + "+(" + _DOT + r"*?)" + _WS + r"+\(([0-9]+)/([0-9]+)\)"
)
# [ 45%] Stage name
_RE_STAGE = re.compile(r"\[" + _WS + r"*([0-9]+)" + _WS + r"*%\]" + _WS + "+(" + _DOT + "+)")
# [CLI] Progress: 32%   (the label is optional)
_RE_PROGRESS = re.compile(
    r"(?:\[([^\]]*)\]" + _WS + r"*)?Progress:" + _WS + r"*([0-9]+)" + _WS + r"*%" + _WS + "*", re.IGNORECASE
)
# model 42%
_RE_MODEL = re.compile(r"([A-Za-z][A-Za-z _\-]*?)" + _WS + "+([0-9]+)" + _WS + r"*%")


class ToolError(RuntimeError):
    """Base class of every failure of an external tool run."""


class ToolFailed(ToolError):
    """The tool exited with a non-zero code (and ``accept_nonzero`` was not set)."""

    def __init__(self, tool: str, code: int | None, stderr_tail: Sequence[str]) -> None:
        self.tool = tool
        self.code = code
        self.stderr_tail = list(stderr_tail)
        detail = "\n".join(self.stderr_tail) if self.stderr_tail else "(no stderr output)"
        super().__init__(f"{tool} failed with exit code {code}; last {len(self.stderr_tail)} stderr line(s):\n{detail}")


class ToolTimeout(ToolError):
    """The tool did not finish within its timeout; it was stopped."""

    def __init__(self, tool: str, seconds: float) -> None:
        self.tool = tool
        self.seconds = seconds
        super().__init__(f"{tool} timed out after {seconds:g} s and was stopped")


class ToolNotRunnable(ToolError):
    """The tool could not be started (missing or not executable); the OSError is ``__cause__``."""

    def __init__(self, tool: str, path: str, reason: str) -> None:
        self.tool = tool
        self.path = path
        super().__init__(f"{tool} could not be started from {path}: {reason}")


class ToolCancelled(ToolError):
    """The caller cancelled the run; the tool was stopped."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(f"{tool} was cancelled and stopped")


@dataclass(frozen=True)
class RunResult:
    returncode: int | None
    stdout_lines: list[str] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)
    duration: float = 0.0


@dataclass(frozen=True)
class Progress:
    percent: float
    text: str
    done: int | None = None
    total: int | None = None


# --- progress parsing -------------------------------------------------------------------------------------------


def _js_trim(text: str) -> str:
    return text.strip(_JS_WS_CHARS)


def parse_progress(line: str | None) -> Progress | None:
    """Port of Steps.parseProgressLine (lib/StepsSyqon.js:607-646). None when the line is not a progress line."""
    if line is None:
        return None
    s = _js_trim(str(line))
    if not s:
        return None

    m = _RE_STAGE_COUNTED.fullmatch(s)
    if m is not None:
        return Progress(float(int(m.group(1))), m.group(2), int(m.group(3)), int(m.group(4)))

    m = _RE_STAGE.fullmatch(s)
    if m is not None:
        return Progress(float(int(m.group(1))), _js_trim(m.group(2)))

    m = _RE_PROGRESS.fullmatch(s)
    if m is not None:
        label = m.group(1)
        return Progress(float(int(m.group(2))), label if label else "working")

    m = _RE_MODEL.fullmatch(s)
    if m is not None:
        return Progress(float(int(m.group(2))), m.group(1))

    return None


def parse_progress_line(line: str | None) -> float | None:
    """The percentage of a Loom-format progress line (0..100 for the formats Loom emits), or None."""
    progress = parse_progress(line)
    return None if progress is None else progress.percent


def parse_ndjson_progress(line: str | None) -> float | None:
    """The percentage of an rc-astro line ``{"type": "progress", "done": <0-100>, ...}``, or None.

    Lines whose ``done`` is not a finite number in 0..100 are rejected (None).
    """
    if line is None:
        return None
    s = str(line).strip()
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "progress":
        return None
    done = obj.get("done")
    if isinstance(done, bool) or not isinstance(done, (int, float)):
        return None
    if not math.isfinite(done) or not 0 <= done <= 100:
        return None
    return float(done)


def _num_text(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def progress_milestone_text(milestone: float | int, progress: Progress) -> str:
    """Port of Steps.progressMilestoneText (lib/StepsSyqon.js:647-655)."""
    counter = "" if progress.total is None else f" ({progress.done}/{progress.total})"
    return f"{_num_text(milestone)}% - {progress.text}{counter}"


# --- command line and process helpers ---------------------------------------------------------------------------


def command_line(argv: Sequence[str], platform: str = sys.platform) -> str:
    """The logged command line: shlex.join on POSIX, subprocess.list2cmdline on Windows."""
    if platform == "win32":
        return subprocess.list2cmdline(list(argv))
    return shlex.join(list(argv))


def _signal_tool(proc: Any, platform: str, kill: bool) -> None:
    """Send SIGTERM (or SIGKILL) to the tool's process group on POSIX; terminate() or kill() on Windows.

    The POSIX-only signal is referenced only on the POSIX branch: signal.SIGKILL does not exist on Windows.
    """
    if platform == "win32":
        try:
            if kill:
                proc.kill()
            else:
                proc.terminate()
        except OSError:
            pass
        return
    pid = getattr(proc, "pid", None)
    # pid 0 would address the caller's own group; only a real child pid is signalled
    if not isinstance(pid, int) or pid <= 0:
        return
    try:
        os.killpg(pid, signal.SIGKILL if kill else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def _taskkill_argv(pid: int) -> list[str]:
    """``taskkill /PID <pid> /T /F``, from %SystemRoot%\\System32 when SystemRoot is set."""
    root = os.environ.get("SystemRoot")
    exe = ntpath.join(root, "System32", "taskkill.exe") if root else "taskkill"
    return [exe, "/PID", str(pid), "/T", "/F"]


def _taskkill_tree(proc: Any, runner: Callable[..., Any]) -> None:
    """End the tool's process tree on Windows while its leader is still alive. Failures are ignored."""
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return
    if proc.poll() is not None:
        return
    try:
        runner(
            _taskkill_argv(pid),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            timeout=TASKKILL_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:  # taskkill is best effort: the terminate/kill fallback still runs
        pass


def _stop_tool(
    proc: Any,
    platform: str,
    grace: float,
    taskkill: Callable[..., Any] = subprocess.run,
) -> None:
    """Stop the tool; returns once it has been reaped.

    POSIX: SIGTERM to the group, wait up to ``grace`` seconds, then SIGKILL. Windows: taskkill /T /F, then
    terminate(), wait up to ``grace`` seconds in total (taskkill counts toward it), then kill().
    """
    end = time.monotonic() + grace
    if platform == "win32":
        _taskkill_tree(proc, taskkill)
    _signal_tool(proc, platform, kill=False)
    try:
        proc.wait(timeout=max(0.0, end - time.monotonic()))
    except subprocess.TimeoutExpired:
        _signal_tool(proc, platform, kill=True)
        proc.wait()
    if platform != "win32":
        # children that outlived the leader still hold the pipes; the group is gone once they are killed
        _signal_tool(proc, platform, kill=True)


class _LineSplitter:
    """Splits text into lines on CR, LF or CRLF; a CR at the end of a chunk may be followed by LF in the next."""

    def __init__(self) -> None:
        self._tail = ""
        self._skip_lf = False

    def feed(self, text: str) -> list[str]:
        if self._skip_lf and text.startswith("\n"):
            text = text[1:]
        self._skip_lf = False
        if not text:
            return []
        parts = _NEWLINE.split(self._tail + text)
        self._tail = parts.pop()
        if text.endswith("\r"):
            self._skip_lf = True
        return parts

    def flush(self) -> list[str]:
        tail, self._tail = self._tail, ""
        return [tail] if tail else []


class _Progress:
    """Progress state shared by both streams of one run (the JS keeps one lastPct/lastMilestone per run)."""

    def __init__(self, on_progress: Callable[[float, str], None] | None, log: Callable[[str], None] | None) -> None:
        self._on_progress = on_progress
        self._log = log
        self._last_pct: float | None = None
        self._last_milestone = -1

    def sample(self, progress: Progress) -> None:
        if progress.percent == self._last_pct:
            return
        self._last_pct = progress.percent
        if self._on_progress is not None:
            self._on_progress(progress.percent, progress.text)
        milestone = math.floor(progress.percent / MILESTONE_STEP) * MILESTONE_STEP
        if milestone > self._last_milestone and milestone > 0:
            self._last_milestone = int(milestone)
            if self._log is not None:
                self._log(progress_milestone_text(int(milestone), progress))

    def line(self, line: str) -> None:
        progress = parse_progress(line)
        if progress is None:
            ndjson = parse_ndjson_progress(line)
            if ndjson is None:
                return
            progress = Progress(ndjson, "working")
        self.sample(progress)


_END = object()


def _reader(stream: Any, name: str, sink: queue.Queue[Any]) -> None:
    """Pump one pipe into the queue as (name, line) items, then _END."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    splitter = _LineSplitter()
    read = getattr(stream, "read1", None) or stream.read
    try:
        while True:
            chunk = read(_READ_CHUNK)
            if not chunk:
                break
            for line in splitter.feed(decoder.decode(chunk)):
                sink.put((name, line))
        tail = decoder.decode(b"", final=True)
        for line in splitter.feed(tail) + splitter.flush():
            sink.put((name, line))
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass
        sink.put((name, _END))


# --- the runner -------------------------------------------------------------------------------------------------


def run_tool(
    tool: str,
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float,
    cancelled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[float, str], None] | None = None,
    log: Callable[[str], None] | None = None,
    env: Mapping[str, str] | None = None,
    platform: str = sys.platform,
    popen: Callable[..., Any] = subprocess.Popen,
    accept_nonzero: bool = False,
    on_line: Callable[[str, str], None] | None = None,
    taskkill_runner: Callable[..., Any] = subprocess.run,
) -> RunResult:
    """Run ``argv`` (never through a shell) and return its result, or raise a ToolError subclass."""
    args = [os.fspath(a) for a in argv]
    cmdline = command_line(args, platform)
    options: dict[str, Any] = {
        "shell": False,
        "cwd": None if cwd is None else os.fspath(cwd),
        "env": None if env is None else dict(env),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if platform == "win32":
        options["creationflags"] = CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True

    started = time.monotonic()
    progress = _Progress(on_progress, log)
    stdout_lines: list[str] = []
    stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    outcome = "exit"
    returncode: int | None = None
    proc: Any = None
    try:
        try:
            proc = popen(args, **options)
        except OSError as exc:
            raise ToolNotRunnable(tool, args[0], exc.strerror or str(exc)) from exc
        items: queue.Queue[Any] = queue.Queue()
        readers = [
            threading.Thread(target=_reader, args=(proc.stdout, "stdout", items), daemon=True),
            threading.Thread(target=_reader, args=(proc.stderr, "stderr", items), daemon=True),
        ]
        for reader in readers:
            reader.start()

        deadline = started + timeout
        exited_at: float | None = None
        open_streams = 2
        while True:
            now = time.monotonic()
            if exited_at is None:
                code = proc.poll()
                if code is not None:
                    exited_at = now
                    returncode = code
                    if platform != "win32":
                        # leftovers of the group would keep the pipes open; the tool itself has finished
                        _signal_tool(proc, platform, kill=True)
                elif cancelled():
                    outcome = "cancelled"
                    _stop_tool(proc, platform, CANCEL_GRACE_SECONDS, taskkill_runner)
                    raise ToolCancelled(tool)
                elif now >= deadline:
                    outcome = "timeout"
                    _stop_tool(proc, platform, TIMEOUT_GRACE_SECONDS, taskkill_runner)
                    raise ToolTimeout(tool, timeout)
            if exited_at is not None and (open_streams == 0 or now - exited_at > TIMEOUT_GRACE_SECONDS):
                break
            try:
                name, line = items.get(timeout=POLL_SECONDS)
            except queue.Empty:
                continue
            if line is _END:
                open_streams -= 1
                continue
            if name == "stdout":
                stdout_lines.append(line)
            else:
                stderr_tail.append(line)
            if on_line is not None:
                on_line(name, line)
            progress.line(line)

        returncode = proc.wait()
        for reader in readers:
            reader.join(timeout=1.0)
        if returncode != 0 and not accept_nonzero:
            outcome = "failed"
            raise ToolFailed(tool, returncode, list(stderr_tail))
        return RunResult(
            returncode=returncode,
            stdout_lines=stdout_lines,
            stderr_tail=list(stderr_tail),
            duration=time.monotonic() - started,
        )
    except BaseException:
        if proc is not None and outcome == "exit" and proc.poll() is None:
            outcome = "interrupted"
            _stop_tool(proc, platform, TIMEOUT_GRACE_SECONDS, taskkill_runner)
        raise
    finally:
        if log is not None:
            duration = time.monotonic() - started
            status = f"exit {returncode}" if outcome in ("exit", "failed") else outcome
            log(f"{tool}: {cmdline} | {status} | {duration:.2f} s")


def run_folder(work_dir: str | os.PathLike[str], tool: str) -> Path:
    """Create ``<work_dir>/tools/<tool>-<n>/`` with the lowest free n >= 1 and return it."""
    if not tool or "/" in tool or "\\" in tool or tool in (".", ".."):
        raise ValueError(f"not a tool name: {tool!r}")
    base = Path(work_dir) / "tools"
    base.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        folder = base / f"{tool}-{n}"
        try:
            folder.mkdir()
        except FileExistsError:
            n += 1
            continue
        return folder


@contextmanager
def exchange_folder(work_dir: str | os.PathLike[str], tool: str) -> Iterator[Path]:
    """The tool's private exchange folder; removed on success, failure, timeout and cancel."""
    folder = run_folder(work_dir, tool)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


__all__ = [
    "CREATE_NO_WINDOW",
    "Progress",
    "RunResult",
    "ToolCancelled",
    "ToolError",
    "ToolFailed",
    "ToolTimeout",
    "command_line",
    "exchange_folder",
    "parse_ndjson_progress",
    "parse_progress",
    "parse_progress_line",
    "progress_milestone_text",
    "run_folder",
    "run_tool",
]
