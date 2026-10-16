"""Fixtures for the end-to-end headless runs: synthetic master sets, job files and the siril-cli runner.

Each run is one siril-cli process that runs ``Loom/loom_preprocess.py`` with ``--job <job.json>`` (docs/loom-job.md). The run's
outcome is read from the status file the script writes beside the job file. Everything is written under pytest's
tmp_path; the masters are rendered by tests/loom/synth.py, never taken from a real frame.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from loomsiril.loom.siril_ops import spcc_database_candidates

from .. import synth
from ..siril_run import SirilRun, siril_cli

#: The checkout that holds Loom/loom_preprocess.py and the loomsiril package.
REPO = Path(__file__).resolve().parents[3]
LOOM_SCRIPT = REPO / "Loom" / "loom_preprocess.py"

#: Upper bound for one siril-cli process (a full RGB run with SPCC takes minutes on a slow machine).
RUN_TIMEOUT_S = 900.0

#: SPCC filter names in Siril's pinned database (docker/network-setup.sh).
FILTERS = {"R": "Baader R", "G": "Baader G", "B": "Baader B"}
#: Master size. The registration crops every channel to the area all of them cover (the offsets and rotations of
#: RGB_SHIFTS and NB_SHIFTS take about 14 px off each side of a 640 x 512 set, which leaves less than StarNet2's
#: 512 px window), so the masters are larger than the window.
MASTER_W, MASTER_H = 704, 576
#: Sky offsets (px) of the broadband and narrowband masters: the registration has to find them.
RGB_SHIFTS = {"R": (0.0, 0.0), "G": (-2.6, 3.4), "B": (3.1, -2.2)}
NB_SHIFTS = {"H": (0.0, 0.0), "S": (-2.6, 3.4), "O": (3.1, -2.2)}


#: Siril 1.4.4 fetches the SPCC stars from its online Gaia service. A request that follows another one closely can
#: fail with "libcurl error: SSL connect error", and siril-cli then dies by a signal (measured: SIGSEGV) instead of
#: returning an error. The same request about two minutes later succeeds, so the runner waits and runs the job again
#: (the cache keeps the stages already done). Any other failure is reported at once.
CATALOGUE_ATTEMPTS = 3
CATALOGUE_PAUSE_S = 120.0


def online_catalogue_crash(returncode: int, output: str) -> bool:
    """True when siril-cli was killed by a signal after its online catalogue request failed on SSL."""
    return returncode < 0 and "libcurl error: SSL connect error" in output


def _installed_spcc_database() -> Path | None:
    for candidate in spcc_database_candidates(sys.platform, os.environ, os.path.expanduser("~")):
        if os.path.isdir(candidate):
            return Path(candidate)
    return None


def ensure_spcc_database() -> Path:
    """Siril's SPCC database, installed with docker/network-setup.sh when it is missing (the image never holds it).

    Every network-marked run calibrates with SPCC, whose preflight refuses a run without the database, so the
    database must be there whichever network test runs first.
    """
    found = _installed_spcc_database()
    if found is not None:
        return found
    env = dict(os.environ, LOOMSIRIL_NETWORK="1")
    proc = subprocess.run(["bash", str(REPO / "docker" / "network-setup.sh")], capture_output=True, text=True,
                          env=env, timeout=600, check=False)
    found = _installed_spcc_database()
    if proc.returncode != 0 or found is None:
        pytest.fail(f"the SPCC database is missing and network-setup.sh did not install it "
                    f"(exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return found


@pytest.fixture(autouse=True)
def _spcc_database_for_network_tests(request: pytest.FixtureRequest) -> None:
    """Installs the SPCC database before any network-marked test (skipped network tests never get here)."""
    if request.node.get_closest_marker("network") is not None:
        ensure_spcc_database()


@dataclass
class LoomRun:
    """One headless run: its job file, the siril-cli output, the status file's record and the wall-clock time."""

    job_path: Path
    run: SirilRun
    status: dict[str, Any]
    wall_s: float

    def pipeline_s(self) -> float:
        """The pipeline's own run time, from the script's ``Finished in N s`` line (no Siril start-up)."""
        for line in self.run.printed():
            match = re.match(r"Finished in ([0-9.]+) s\.", line)
            if match:
                return float(match.group(1))
        raise AssertionError("the run printed no 'Finished in' line:\n" + self.run.tail())

    def log_lines(self) -> list[str]:
        """The run log (only written for exit code 0; empty otherwise)."""
        log = self.status.get("log") or ""
        if not log:
            return []
        return Path(log).read_text(encoding="utf-8").splitlines()


def _status_path(job_path: Path) -> Path:
    return Path(str(job_path) + ".status.json")


@pytest.fixture
def run_loom(tmp_path: Path) -> Callable[[Mapping[str, Any], str], LoomRun]:
    """Runner: ``run_loom(job, name)`` writes the job as ``jobs/<name>.json``, runs siril-cli on it and returns the run.

    The siril-cli working folder is ``siril-work/<name>``; the job's cache folder is created when it is named. A
    missing status file fails the test with the output tail.
    """

    def run(job: Mapping[str, Any], name: str) -> LoomRun:
        jobs = tmp_path / "jobs"
        jobs.mkdir(exist_ok=True)
        job_path = jobs / f"{name}.json"
        job_path.write_text(json.dumps(dict(job), indent=2, sort_keys=True), encoding="utf-8")
        # A chosen cache folder that does not exist makes the run fall back to the default cache with caching off
        # (loomsiril.loom.cache.folder_missing), so the runner creates it first.
        if job.get("cacheDir"):
            Path(str(job["cacheDir"])).mkdir(parents=True, exist_ok=True)
        workdir = tmp_path / "siril-work" / name
        workdir.mkdir(parents=True, exist_ok=True)
        script = f'requires 1.4.0\npyscript "{LOOM_SCRIPT}" --job "{job_path}"\n'
        env = dict(os.environ)
        env["LOOMSIRIL_SRC"] = str(REPO)
        env["PYTHONPATH"] = str(REPO) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        started = time.monotonic()
        for attempt in range(1, CATALOGUE_ATTEMPTS + 1):
            proc = subprocess.run([siril_cli(), "-d", str(workdir), "-s", "-"], input=script, text=True,
                                  capture_output=True, timeout=RUN_TIMEOUT_S, env=env, check=False)
            if not online_catalogue_crash(proc.returncode, proc.stdout + proc.stderr) or \
                    attempt == CATALOGUE_ATTEMPTS:
                break
            print(f"run {name}: siril-cli died (exit {proc.returncode}) after an online catalogue SSL error; "
                  f"attempt {attempt + 1} of {CATALOGUE_ATTEMPTS} in {CATALOGUE_PAUSE_S:.0f} s", flush=True)
            time.sleep(CATALOGUE_PAUSE_S)
        wall = time.monotonic() - started
        result = SirilRun(proc.returncode, proc.stdout, proc.stderr)
        status_file = _status_path(job_path)
        if not status_file.is_file():
            pytest.fail(f"no status file for run {name} (siril-cli exit {proc.returncode}):\n{result.tail()}")
        status = json.loads(status_file.read_text(encoding="utf-8"))
        if "Traceback (most recent call last)" in proc.stdout + proc.stderr:
            pytest.fail(f"run {name} logged a Python traceback:\n{result.tail()}")
        return LoomRun(job_path=job_path, run=result, status=status, wall_s=wall)

    return run


@pytest.fixture
def masters(tmp_path: Path) -> Callable[..., dict[str, str]]:
    """Factory: ``masters(channels, folder=...)`` writes one synthetic master per channel and returns channel -> path.

    Channels: L (no shift, FILTER "L"); R, G, B (Baader filter names, the registration shifts of RGB_SHIFTS);
    H, S, O (the narrowband shifts of NB_SHIFTS, FWHM growing by channel). Masters are MASTER_W x MASTER_H.
    """

    def make(channels: tuple[str, ...], folder: str = "masters") -> dict[str, str]:
        target = tmp_path / folder
        target.mkdir(parents=True, exist_ok=True)
        out: dict[str, str] = {}
        for index, channel in enumerate(channels):
            if channel == "L":
                master = synth.make_master("L", w=MASTER_W, h=MASTER_H, seed=1)
            elif channel in RGB_SHIFTS:
                master = synth.make_master(channel, w=MASTER_W, h=MASTER_H, seed=index + 2,
                                           shift=RGB_SHIFTS[channel], filter_name=FILTERS[channel])
            elif channel in NB_SHIFTS:
                master = synth.make_master(channel, w=MASTER_W, h=MASTER_H, seed=index + 5,
                                           shift=NB_SHIFTS[channel], fwhm=3.0 + index)
            else:
                raise ValueError(f"no synthetic master for channel {channel!r}")
            out[channel] = str(synth.write_master(target / f"{channel}.fits", master))
        return out

    return make


def job_paths(**channels: str) -> dict[str, str]:
    """The ``paths`` object of a job: every channel, empty unless given (``job_paths(L=..., R=...)``)."""
    paths = {"L": "", "R": "", "G": "", "B": "", "H": "", "S": "", "O": ""}
    paths.update(channels)
    return paths
