"""SPCC with an OSC sensor, measured on Siril 1.4.4 (siril-cli): the syntax, the database names and the session rules.

Each test is one siril-cli run on a small synthetic field of public Gaia stars that Siril plate-solves online. The
tests need LOOMSIRIL_NETWORK=1, Siril's online catalogues and the SPCC database (the module fetches the pinned
database with docker/network-setup.sh when none is installed); markers ``siril`` and ``network``.

Measured rules (recorded in the module docstring of ``loomsiril.loom.siril_ops``):

* ``spcc "-oscsensor=<model>" "-oscfilter=<name>" "-osclpf=<name>"``; the sensor is the database *model*
  ("Sony IMX571"), never an entry's name ("Sony IMX571 Red": Argument error).
* The database's "no front filter" entry is the OSC_FILTER "No filter"; its "no low-pass filter" is the OSC_LPF
  "Full spectrum (no filter)".
* ``-osclpf`` is the entry's name. A DSLR sensor without it, or with an unknown name or a model, kills Siril
  (SIGSEGV) from the second ``spcc`` of a session on; the first call of a session neither logs nor checks it. A
  complete OSC trio of database names is always sent.
* A broadband call carries the complete set of its own mode; a partial mono set after an OSC call is refused.
* Narrowband: no names in a fresh session; after any broadband call it needs a complete set (the OSC trio after an OSC
  call, the four mono names after a mono call), and a partial set is refused (or kills Siril with a DSLR sensor).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from loomsiril.loom.siril_ops import spcc_database_candidates

from .. import synth
from ..siril_run import SirilRun, run_pyscript, siril_cli
from .test_siril_ops_cli import FIELD_DEC, FIELD_RA, _real_sky_field

pytestmark = pytest.mark.siril

REPO = Path(__file__).resolve().parents[3]
NETWORK = pytest.mark.network

SONY = '"-oscsensor=Sony IMX571"'
CANON = '"-oscsensor=Canon EOS 20D"'
NO_FRONT = '"-oscfilter=No filter"'
NO_LPF = '"-osclpf=Full spectrum (no filter)"'
TRIBAND = '"-oscfilter=Antlia Triband RGB Ultra II"'
OSC_SONY = f"{SONY} {NO_FRONT} {NO_LPF}"
OSC_CANON = f"{CANON} {TRIBAND} {NO_LPF}"
MONO = '"-monosensor=Generic mono sensor" "-rfilter=Antlia R" "-gfilter=Antlia G" "-bfilter=Antlia B"'
NARROW = "-narrowband -rwl=656.28 -rbw=7 -gwl=500.7 -gbw=25 -bwl=495.9 -bbw=20"
SIGSEGV = -11

SCRIPT = r'''
import json, os, time
import sirilpy as s
from loomsiril.loom.siril_ops import SirilCommandOps

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = siril.get_siril_wd()
p = lambda name: os.path.join(root, name)
commands = json.load(open(p("commands.json")))
ops.rgbcomp(p("field_nowcs_r.fits"), p("field_nowcs_g.fits"), p("field_nowcs_b.fits"), p("field_rgb"))
ops.load(p("field_rgb.fit"))
ops.platesolve()
for text in commands:
    siril.log("STEP> " + text)
    try:
        siril.cmd(text)
        step = {"ok": True, "error": ""}
    except Exception as exc:
        step = {"ok": False, "error": str(exc)}
    print("STEP=" + json.dumps(step), flush=True)
time.sleep(3)
'''


def _database_folder() -> Path:
    for candidate in spcc_database_candidates(sys.platform, os.environ, os.path.expanduser("~")):
        if os.path.isdir(candidate):
            return Path(candidate)
    setup = REPO / "docker" / "network-setup.sh"
    env = dict(os.environ, LOOMSIRIL_NETWORK="1")
    proc = subprocess.run(["bash", str(setup)], capture_output=True, text=True, env=env, timeout=600, check=False)
    assert proc.returncode == 0, f"network-setup.sh failed:\n{proc.stdout}\n{proc.stderr}"
    for candidate in spcc_database_candidates(sys.platform, os.environ, os.path.expanduser("~")):
        if os.path.isdir(candidate):
            return Path(candidate)
    pytest.fail("the SPCC database is not installed here and network-setup.sh did not install it")


@pytest.fixture(scope="module")
def database() -> Path:
    return _database_folder()


def _entries(folder: Path) -> list[dict]:
    found: list[dict] = []
    for path in sorted(folder.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            found.extend(e for e in data if isinstance(e, dict))
    return found


def _session(tmp_path: Path, gaia_tsv_path: Path, commands: list[str]) -> tuple[SirilRun, list[dict], list[str]]:
    """Run ``commands`` in one siril-cli session on a plate-solved colour field.

    Returns the run, one {ok, error} per command (empty when Siril died) and Siril's "SPCC will use ..." lines.
    """
    data = np.genfromtxt(gaia_tsv_path, delimiter="\t", names=True)
    near = np.hypot((data["ra"] - FIELD_RA) * np.cos(np.radians(FIELD_DEC)), data["dec"] - FIELD_DEC) < 0.15
    rows = np.column_stack([data["ra"][near], data["dec"][near], data["G"][near]])
    rows = rows[rows[:, 2] < 13.0]
    assert len(rows) >= 30, "too few bright Gaia stars in the field"
    for channel, tint in (("R", 1.0), ("G", 0.8), ("B", 0.6)):
        synth.write_master(tmp_path / f"field_nowcs_{channel.lower()}.fits",
                           _real_sky_field(channel, 0.05, tint, rows, wcs=False))
    (tmp_path / "commands.json").write_text(json.dumps(commands), encoding="utf-8")
    run = run_pyscript(SCRIPT, tmp_path)
    steps = [json.loads(line[len("STEP="):]) for line in run.lines("STEP=")]
    used = [line for line in run.printed() if line.startswith("SPCC will use")]
    return run, steps, used


def test_help_spcc_pins_the_osc_syntax() -> None:
    """``help spcc`` lists the OSC option group exactly as the builders use it."""
    proc = subprocess.run([siril_cli(), "-s", "-"], input="requires 1.4.0\nhelp spcc\n", capture_output=True,
                          text=True, timeout=120, check=False)
    text = proc.stdout
    assert "-oscsensor= [-oscfilter=] [-osclpf=]" in text
    assert "-monosensor= [ -rfilter= ] [-gfilter=] [-bfilter=]" in text
    assert "If one of the spectral data argument is omitted, the previously used value will be used." in text
    assert "in which case the previous filter arguments are ignored" in text


@NETWORK
def test_database_kinds_and_the_no_filter_names(database: Path) -> None:
    """OSC sensors are three OSC_SENSOR entries (one per channel) of one model; filters are OSC_FILTER, low-pass
    filters OSC_LPF; both "no filter" entries exist."""
    entries = _entries(database)
    sensors = [e for e in entries if e.get("type") == "OSC_SENSOR"]
    assert {e.get("channel") for e in sensors} == {"RED", "GREEN", "BLUE"}
    by_model: dict[str, set[str]] = {}
    for e in sensors:
        by_model.setdefault(e["model"], set()).add(e["channel"])
    assert by_model["Sony IMX571"] == {"RED", "GREEN", "BLUE"}
    assert by_model["Canon EOS 20D"] == {"RED", "GREEN", "BLUE"}
    assert {e["name"] for e in sensors if e["model"] == "Sony IMX571"} == {
        "Sony IMX571 Red", "Sony IMX571 Green", "Sony IMX571 Blue"}
    filters = {e["name"] for e in entries if e.get("type") == "OSC_FILTER"}
    lpfs = {e["name"] for e in entries if e.get("type") == "OSC_LPF"}
    assert {"No filter", "Antlia Triband RGB Ultra II"} <= filters
    assert {"Full spectrum (no filter)", "Canon EOS 350D blocking filter"} <= lpfs
    assert not filters & lpfs


@NETWORK
def test_the_sensor_is_the_model_not_a_channel_entry_name(database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [
        f"spcc {SONY} {NO_FRONT} {NO_LPF}",
        f'spcc "-oscsensor=Sony IMX571 Red" {NO_FRONT} {NO_LPF}',
    ])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True, False]
    assert "Argument error" in steps[1]["error"]
    assert used == ['SPCC will use OSC sensor "Sony IMX571" and filter "No filter"']


@NETWORK
def test_none_names_are_accepted_for_both_filters(database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {OSC_SONY}", f"spcc {CANON} {NO_FRONT} {NO_LPF}"])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True, True]
    assert used[-1] == 'SPCC will use OSC sensor "Canon EOS 20D", filter "No filter" and DSLR LPF "Full spectrum (no filter)"'


@NETWORK
def test_without_the_low_pass_filter_a_dslr_sensor_kills_siril(database: Path, tmp_path: Path,
                                                               gaia_tsv_path: Path) -> None:
    """A flag cannot be omitted: after an earlier call ``-osclpf`` left out with a DSLR sensor segfaults siril-cli."""
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {OSC_CANON}", f"spcc {CANON} {NO_FRONT}"])
    assert run.returncode == SIGSEGV
    assert steps == []
    assert used[-1] == 'SPCC will use OSC sensor "Canon EOS 20D", filter "No filter" and DSLR LPF "(null)"'


@NETWORK
@pytest.mark.parametrize("lpf", ['"-osclpf=bogus"', '"-osclpf=Canon LPF"'], ids=["unknown", "model-not-name"])
def test_an_unknown_low_pass_name_kills_siril_after_a_first_call(database: Path, tmp_path: Path, gaia_tsv_path: Path,
                                                                 lpf: str) -> None:
    """The low-pass filter must be the entry's name ("Canon EOS 350D blocking filter"), not its model."""
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {OSC_CANON}", f"spcc {CANON} {NO_FRONT} {lpf}"])
    assert run.returncode == SIGSEGV
    assert steps == []
    assert used[-1].startswith('SPCC will use OSC sensor "Canon EOS 20D", filter "No filter" and DSLR LPF ')


@NETWORK
@pytest.mark.parametrize("lpf", ["", ' "-osclpf=bogus"'], ids=["omitted", "unknown"])
def test_the_first_call_of_a_session_does_not_look_at_the_low_pass_filter(
        database: Path, tmp_path: Path, gaia_tsv_path: Path, lpf: str) -> None:
    """In a fresh session Siril neither logs nor validates ``-osclpf``: the call runs without it or with a bogus name."""
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {CANON} {NO_FRONT}{lpf}"])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True]
    assert used == ['SPCC will use OSC sensor "Canon EOS 20D" and filter "No filter"']


@NETWORK
def test_without_the_low_pass_filter_a_non_dslr_sensor_still_runs(database: Path, tmp_path: Path,
                                                                  gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {SONY} {NO_FRONT}"])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True]


@NETWORK
def test_osc_then_mono_each_with_its_full_set(database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {OSC_CANON}", f"spcc {MONO}", f"spcc {OSC_CANON}"])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True, True, True]
    assert used == [
        'SPCC will use OSC sensor "Canon EOS 20D" and filter "Antlia Triband RGB Ultra II"',
        'SPCC will use mono senor "Generic mono sensor" and filters "Antlia R", "Antlia G" and "Antlia B',
        'SPCC will use OSC sensor "Canon EOS 20D", filter "Antlia Triband RGB Ultra II" and DSLR LPF '
        '"Full spectrum (no filter)"',
    ]


@NETWORK
def test_mono_then_osc_each_with_its_full_set(database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {MONO}", f"spcc {OSC_CANON}", f"spcc {MONO}"])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True, True, True]
    assert used[0].startswith('SPCC will use mono senor "Generic mono sensor"')
    assert used[1] == ('SPCC will use OSC sensor "Canon EOS 20D", filter "Antlia Triband RGB Ultra II" and DSLR LPF '
                       '"Full spectrum (no filter)"')
    assert used[2].startswith('SPCC will use mono senor "Generic mono sensor"')


@NETWORK
def test_a_partial_mono_set_after_osc_is_refused_not_completed(database: Path, tmp_path: Path,
                                                               gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [
        f"spcc {OSC_CANON}",
        'spcc "-monosensor=Generic mono sensor"',
        'spcc "-monosensor=Generic mono sensor" "-rfilter=Antlia R"',
    ])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True, False, False]
    assert all("Argument error" in s["error"] for s in steps[1:])
    assert len(used) == 1


@NETWORK
def test_a_sensor_only_osc_call_after_mono_takes_defaults_not_the_mono_filters(
        database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {MONO}", f"spcc {SONY}"])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True, True]
    assert used[1] == 'SPCC will use OSC sensor "Sony IMX571" and filter "No filter"'


@NETWORK
def test_narrowband_in_a_fresh_session_takes_no_names(database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {NARROW}"])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True]


@NETWORK
def test_narrowband_after_osc_needs_a_complete_set(database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [
        f"spcc {OSC_CANON}",
        f"spcc {NARROW}",
        f"spcc {OSC_CANON} {NARROW}",
        f"spcc {MONO} {NARROW}",
    ])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True, False, True, True]
    assert "Argument error" in steps[1]["error"]
    assert used[1] == ('SPCC will use OSC sensor "Canon EOS 20D", filter "Antlia Triband RGB Ultra II" and DSLR LPF '
                       '"Full spectrum (no filter)"')
    assert used[2].startswith('SPCC will use mono senor "Generic mono sensor"')


@NETWORK
def test_narrowband_after_mono_needs_a_complete_set(database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [
        f"spcc {MONO}",
        f"spcc {NARROW}",
        f'spcc "-monosensor=Generic mono sensor" {NARROW}',
        f'spcc "-monosensor=Generic mono sensor" "-rfilter=Antlia R" "-gfilter=Antlia G" {NARROW}',
        f"spcc {MONO} {NARROW}",
    ])
    assert run.returncode == 0
    assert [s["ok"] for s in steps] == [True, False, False, False, True]
    assert all("Argument error" in s["error"] for s in steps[1:4])
    assert used[1].startswith('SPCC will use mono senor "Generic mono sensor"')


@NETWORK
def test_a_partial_osc_set_in_narrowband_kills_siril_with_a_dslr_sensor(
        database: Path, tmp_path: Path, gaia_tsv_path: Path) -> None:
    run, steps, used = _session(tmp_path, gaia_tsv_path, [f"spcc {OSC_CANON}", f"spcc {CANON} {NARROW}"])
    assert run.returncode == SIGSEGV
    assert used[-1] == 'SPCC will use OSC sensor "Canon EOS 20D", filter "No filter" and DSLR LPF "(null)"'
