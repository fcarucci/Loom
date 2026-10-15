"""Loom's find_stars keeps the user's star-finder focal length and pixel size.

Siril 1.4.4 ``setfindstar reset`` sets the persisted ``starfinder.focal_length`` and ``starfinder.pixel_size`` to 0,
so a find_stars run reads them first and restores them afterwards (``setfindstar -focal= -pixelsize=``), also when
findstar fails. When they cannot be read, no reset is sent and findstar runs with the current settings.

Runs against the sirilpy stub (no Siril); the Docker-only check against a real Siril is in
tests/loom/integration/test_starfinder_prefs_siril.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from loomsiril.loom.siril_ops import RecordingOps, SirilCommandOps, SirilOpsError

STUB = Path(__file__).resolve().parents[1] / "sirilpy_stub"
RESET = "setfindstar reset -gaussian"
RESTORE = "setfindstar -focal=600.0 -pixelsize=3.76"


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.syspath_prepend(str(STUB))
    for name in [m for m in sys.modules if m == "sirilpy" or m.startswith("sirilpy.")]:
        monkeypatch.delitem(sys.modules, name)
    import sirilpy

    sirilpy.STATE.reset()
    sirilpy.STATE.workdir = str(tmp_path)
    sirilpy.STATE.loaded = True
    sirilpy.STATE.stars = []
    sirilpy.STATE.config["starfinder.focal_length"] = 600.0
    sirilpy.STATE.config["starfinder.pixel_size"] = 3.76
    yield sirilpy
    sirilpy.STATE.reset()


@pytest.fixture
def ops(stub):
    lines: list[str] = []
    return SirilCommandOps(stub.SirilInterface(), log=lines.append), lines


def _prefs(stub) -> list:
    siril = stub.SirilInterface()
    return [siril.get_siril_config("starfinder", "focal_length"), siril.get_siril_config("starfinder", "pixel_size")]


def test_find_stars_restores_the_focal_length_and_pixel_size(ops, stub):
    runner, _ = ops
    runner.find_stars()
    assert [c[0] for c in stub.STATE.commands] == [RESET, "findstar", RESTORE]
    assert _prefs(stub) == [600.0, 3.76]


def test_a_failed_findstar_still_restores_the_prefs(ops, stub):
    runner, _ = ops
    stub.STATE.fail_commands = ["findstar"]
    with pytest.raises(SirilOpsError):
        runner.find_stars()
    assert [c[0] for c in stub.STATE.commands] == [RESET, "findstar", RESTORE]
    assert _prefs(stub) == [600.0, 3.76]


def test_unreadable_prefs_send_no_reset_and_warn(ops, stub):
    runner, lines = ops
    stub.STATE.config_error = True
    runner.find_stars()
    assert [c[0] for c in stub.STATE.commands] == ["findstar"]
    assert any(line.startswith("warning:") and "starfinder" in line for line in lines)


def test_a_missing_pref_counts_as_unreadable(ops, stub):
    runner, lines = ops
    del stub.STATE.config["starfinder.pixel_size"]
    runner.find_stars()
    assert [c[0] for c in stub.STATE.commands] == ["findstar"]
    assert any(line.startswith("warning:") for line in lines)


def test_recording_ops_restores_the_prefs(tmp_path):
    rec = RecordingOps(tmp_path)
    rec.starfinder = {"focal_length": 600.0, "pixel_size": 3.76}
    rec.find_stars()
    assert rec.transcript == [RESET, "findstar", RESTORE]


def test_recording_ops_without_readable_prefs_sends_only_findstar(tmp_path):
    rec = RecordingOps(tmp_path)
    rec.starfinder = None
    rec.find_stars()
    assert rec.transcript == ["findstar"]
