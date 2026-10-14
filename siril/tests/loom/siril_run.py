"""Run Python code inside Siril (``siril-cli`` + ``pyscript``) from a test; shared helper (read-only for tasks).

``run_pyscript(script, workdir, args)`` writes nothing but the script itself (into ``workdir``) and runs::

    printf 'requires 1.4.0\\npyscript <script> <args>\\n' | siril-cli -d <workdir> -s -

siril-cli's exit status does not reflect errors inside a Python script (an exception or ``sys.exit(n)`` in
``pyscript`` still ends with status 0), so a script reports through stdout lines: every line it prints arrives in
siril-cli's output prefixed ``log: ``. ``SirilRun.lines(prefix)`` returns the printed lines that start with
``prefix``; ``SirilRun.value(key)`` parses a ``key=<json>`` line. A Python traceback in the output fails
``check()``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

#: Lines siril-cli prints for each line a Python script prints.
LOG_PREFIX = "log: "


@dataclass
class SirilRun:
    returncode: int
    stdout: str
    stderr: str

    def printed(self) -> list[str]:
        """Every line Siril logged (the ``log: `` prefix removed)."""
        return [ln[len(LOG_PREFIX):] for ln in self.stdout.splitlines() if ln.startswith(LOG_PREFIX)]

    def lines(self, prefix: str) -> list[str]:
        return [ln for ln in self.printed() if ln.startswith(prefix)]

    def value(self, key: str) -> Any:
        """The JSON value of the last printed line ``<key>=<json>``; KeyError if none."""
        found = [ln[len(key) + 1:] for ln in self.printed() if ln.startswith(key + "=")]
        if not found:
            raise KeyError(f"{key}= not printed by the Siril script; output tail:\n{self.tail()}")
        return json.loads(found[-1])

    def tail(self, n: int = 40) -> str:
        return "\n".join((self.stdout + self.stderr).splitlines()[-n:])

    def check(self) -> "SirilRun":
        """Raise AssertionError when siril-cli failed or the script raised (a traceback was logged)."""
        text = self.stdout + self.stderr
        if self.returncode != 0 or "Traceback (most recent call last)" in text:
            raise AssertionError(f"siril-cli run failed (exit {self.returncode}):\n{self.tail()}")
        return self


def siril_cli() -> str:
    exe = shutil.which("siril-cli")
    if not exe:
        raise RuntimeError("siril-cli not on PATH (mark the test with @pytest.mark.siril)")
    return exe


def run_pyscript(script: str, workdir: "str | Path", args: Sequence[str] = (), timeout: float = 600,
                 name: str = "probe_script.py") -> SirilRun:
    """Write ``script`` (Python source) to ``workdir/name`` and run it under siril-cli with ``workdir`` as the
    working directory. The repository root is put on PYTHONPATH so the script can import loomsiril."""
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    path = wd / name
    path.write_text(script, encoding="utf-8")
    quoted = " ".join([f'"{path}"'] + [f'"{a}"' if " " in str(a) else str(a) for a in args])
    siril_script = f"requires 1.4.0\npyscript {quoted}\n"
    repo = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ)
    env["PYTHONPATH"] = repo + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run([siril_cli(), "-d", str(wd), "-s", "-"], input=siril_script, text=True,
                          capture_output=True, timeout=timeout, env=env, check=False)
    return SirilRun(proc.returncode, proc.stdout, proc.stderr)
