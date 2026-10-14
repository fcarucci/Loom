"""Fake external executables for the tool-layer tests.

``make_fake(dir, name, **options)`` writes an executable script ``<dir>/<name>`` (shebang ``sys.executable``,
mode 0755). When run, the script:

* appends its arguments (``sys.argv[1:]``, without the program name) as one JSON list per line to
  ``<dir>/<name>.argv.jsonl`` (unless ``record_argv=False``); ``read_argv(dir, name)`` reads them back;
* sets SIGTERM to ignored when ``ignore_sigterm=True``;
* writes each file of ``write_files``: a key starting with ``-`` is an argv flag and the file is the argument
  that follows it (nothing is written when the flag is absent); any other key is a literal path, relative to the
  working directory. Values are ``str`` (UTF-8) or ``bytes``;
* prints ``stdout_lines`` and ``stderr_lines`` (UTF-8, each followed by ``line_end``, default ``"\\n"``; use
  ``"\\r"`` for in-place progress bars), flushing after each stream;
* when ``orphan_child=True``, spawns a ``sleep 30`` child before printing (it keeps the pipes open after the
  fake exits) and records its pid like ``hang=True``;
* when ``hang=True``, spawns a ``sleep 3600`` child, writes its pid to ``<dir>/<name>.pid`` (see
  ``read_child_pid``) and waits forever; otherwise sleeps ``sleep`` seconds;
* exits with ``exit_code``.

The factory does not run the script. Tests import it READ-ONLY from later tool tasks; keep the options stable.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
from pathlib import Path
from typing import Mapping, Sequence

_BODY = r'''
import base64, json, os, signal, subprocess, sys, time

CFG = json.loads(__CONFIG__)
argv = sys.argv[1:]
if CFG["record_argv"]:
    with open(os.path.join(CFG["dir"], CFG["name"] + ".argv.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\n")
if CFG["ignore_sigterm"]:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
for key, payload in CFG["write_files"].items():
    if key.startswith("-"):
        if key not in argv or argv.index(key) + 1 >= len(argv):
            continue
        path = argv[argv.index(key) + 1]
    else:
        path = key
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(payload))
if CFG["orphan"]:
    # a grandchild that outlives the tool and keeps its stdout/stderr pipes open
    child = subprocess.Popen(["sleep", "30"])
    with open(os.path.join(CFG["dir"], CFG["name"] + ".pid"), "w", encoding="utf-8") as fh:
        fh.write(str(child.pid))
for stream, lines in ((sys.stdout, CFG["stdout_lines"]), (sys.stderr, CFG["stderr_lines"])):
    for line in lines:
        stream.buffer.write((line + CFG["line_end"]).encode("utf-8"))
        stream.flush()
if CFG["hang"]:
    child = subprocess.Popen(["sleep", "3600"])
    with open(os.path.join(CFG["dir"], CFG["name"] + ".pid"), "w", encoding="utf-8") as fh:
        fh.write(str(child.pid))
    while True:
        time.sleep(0.05)
elif CFG["sleep"] > 0:
    time.sleep(CFG["sleep"])
sys.exit(CFG["exit_code"])
'''


def make_fake(
    dir: "str | Path",
    name: str,
    *,
    exit_code: int = 0,
    stdout_lines: Sequence[str] = (),
    stderr_lines: Sequence[str] = (),
    sleep: float = 0.0,
    hang: bool = False,
    write_files: Mapping[str, "str | bytes"] | None = None,
    record_argv: bool = True,
    ignore_sigterm: bool = False,
    line_end: str = "\n",
    orphan_child: bool = False,
) -> Path:
    """Write a fake executable ``<dir>/<name>`` and return its path (see the module docstring)."""
    folder = Path(dir)
    folder.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for key, content in (write_files or {}).items():
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        files[key] = base64.b64encode(raw).decode("ascii")
    config = {
        "dir": str(folder),
        "name": name,
        "exit_code": int(exit_code),
        "stdout_lines": list(stdout_lines),
        "stderr_lines": list(stderr_lines),
        "sleep": float(sleep),
        "hang": bool(hang),
        "write_files": files,
        "record_argv": bool(record_argv),
        "ignore_sigterm": bool(ignore_sigterm),
        "line_end": line_end,
        "orphan": bool(orphan_child),
    }
    script = folder / name
    source = f"#!{sys.executable}\n" + _BODY.replace("__CONFIG__", repr(json.dumps(config)))
    script.write_text(source, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH | 0o755)
    return script


def read_argv(dir: "str | Path", name: str) -> list[list[str]]:
    """The argument lists the fake ``name`` was called with, oldest first (empty when never called)."""
    path = Path(dir) / f"{name}.argv.jsonl"
    if not path.exists():
        return []
    calls: list[list[str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            calls.append(json.loads(raw))
    return calls


def read_child_pid(dir: "str | Path", name: str) -> int | None:
    """The pid of the ``sleep`` child a ``hang=True`` fake spawned, or None if it never started."""
    path = Path(dir) / f"{name}.pid"
    if not path.exists():
        return None
    return int(path.read_text(encoding="utf-8").strip())


def pid_alive(pid: int) -> bool:
    """True when a process with this pid exists and is not a zombie (POSIX)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return True  # no procfs here, or the process was reaped between the two reads
    return not (fields and fields[0] == "Z")


__all__: list[str] = ["make_fake", "read_argv", "read_child_pid", "pid_alive"]
