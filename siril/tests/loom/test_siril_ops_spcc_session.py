"""Narrowband SPCC after a broadband SPCC in one Siril session: the optional sensor and filter names.

No Siril here. SirilCommandOps runs against the sirilpy stub (as tests/loom/test_siril_ops.py does) and RecordingOps
needs nothing. The narrowband command takes all four names (sensor, red, green and blue filter) or none; the four are
checked against the database exactly like the broadband names.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from loomsiril.loom.siril_ops import RecordingOps, SirilCommandOps, SirilOpsError

STUB = Path(__file__).resolve().parents[1] / "sirilpy_stub"

NARROW = (656.28, 7.0, 500.7, 25.0, 495.9, 20.0)
NARROW_TEXT = "spcc -narrowband -rwl=656.28 -rbw=7.00 -gwl=500.70 -gbw=25.00 -bwl=495.90 -bbw=20.00"
NAMED_TEXT = ('spcc "-monosensor=Sony IMX571" "-rfilter=Baader R" "-gfilter=Baader G" "-bfilter=Baader B" '
              "-narrowband -rwl=656.28 -rbw=7.00 -gwl=500.70 -gbw=25.00 -bwl=495.90 -bbw=20.00")
BROADBAND_TEXT = 'spcc "-monosensor=Sony IMX571" "-rfilter=Baader R" "-gfilter=Baader G" "-bfilter=Baader B"'
NAMES = {"sensor": "Sony IMX571", "r": "Baader R", "g": "Baader G", "b": "Baader B"}


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The sirilpy stub, freshly imported and reset; its working directory is tmp_path."""
    monkeypatch.syspath_prepend(str(STUB))
    for name in [m for m in sys.modules if m == "sirilpy" or m.startswith("sirilpy.")]:
        monkeypatch.delitem(sys.modules, name)
    import sirilpy

    sirilpy.STATE.reset()
    sirilpy.STATE.workdir = str(tmp_path)
    yield sirilpy
    sirilpy.STATE.reset()


@pytest.fixture
def spcc_db(tmp_path: Path) -> Path:
    folder = tmp_path / "spcc"
    folder.mkdir()
    (folder / "sensors.json").write_text('[{"name": "Sony IMX571", "type": "MONO_SENSOR"}]', encoding="utf-8")
    (folder / "filters.json").write_text(
        '[{"name": "Baader R", "type": "MONO_FILTER", "channel": "RED"},'
        ' {"name": "Baader G", "type": "MONO_FILTER", "channel": "GREEN"},'
        ' {"name": "Baader B", "type": "MONO_FILTER", "channel": "BLUE"}]', encoding="utf-8")
    return folder


@pytest.fixture
def ops(stub, spcc_db):
    return SirilCommandOps(stub.SirilInterface(), spcc_database=spcc_db)


# ---- command text ---------------------------------------------------------------------------------------------


def test_narrowband_without_names_keeps_the_text(stub, ops):
    rec = RecordingOps(".")
    rec.spcc_narrowband(*NARROW)
    ops.spcc_narrowband(*NARROW)
    assert rec.transcript == [NARROW_TEXT]
    assert stub.STATE.commands == [(NARROW_TEXT,)]


def test_narrowband_with_all_four_names_puts_them_before_narrowband(stub, ops):
    rec = RecordingOps(".")
    rec.spcc_narrowband(*NARROW, **NAMES)
    ops.spcc_narrowband(*NARROW, **NAMES)
    assert rec.transcript == [NAMED_TEXT]
    assert stub.STATE.commands == [(NAMED_TEXT,)]


def test_narrowband_after_broadband_in_one_session_transcript(stub, ops):
    rec = RecordingOps(".")
    rec.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader B")
    rec.spcc_narrowband(*NARROW, **NAMES)
    assert rec.transcript == [BROADBAND_TEXT, NAMED_TEXT]
    ops.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader B")
    ops.spcc_narrowband(*NARROW, **NAMES)
    assert [c[0] for c in stub.STATE.commands] == [BROADBAND_TEXT, NAMED_TEXT]


def test_recording_call_records_all_ten_parameters(stub):
    rec = RecordingOps(".")
    rec.spcc_narrowband(*NARROW)
    rec.spcc_narrowband(*NARROW, **NAMES)
    assert rec.calls == [
        ("spcc_narrowband", (*NARROW, None, None, None, None), {}),
        ("spcc_narrowband", (*NARROW, "Sony IMX571", "Baader R", "Baader G", "Baader B"), {}),
    ]


# ---- all four or none ----------------------------------------------------------------------------------------

PARTIAL_SETS = [
    {"sensor": "Sony IMX571"},
    {"r": "Baader R"},
    {"b": "Baader B"},
    {"sensor": "Sony IMX571", "r": "Baader R", "g": "Baader G"},
    {"r": "Baader R", "g": "Baader G", "b": "Baader B"},
]


@pytest.mark.parametrize("names", PARTIAL_SETS, ids=lambda n: ",".join(sorted(n)))
def test_a_partial_set_of_names_is_refused_before_anything_is_sent(stub, ops, names):
    rec = RecordingOps(".")
    with pytest.raises(ValueError, match="all four"):
        rec.spcc_narrowband(*NARROW, **names)
    assert rec.transcript == [] and rec.calls == []
    with pytest.raises(ValueError, match="all four"):
        ops.spcc_narrowband(*NARROW, **names)
    assert stub.STATE.commands == []


# ---- the names are checked like the broadband names ----------------------------------------------------------


def test_an_unknown_name_gives_the_same_error_as_broadband(stub, ops):
    with pytest.raises(SirilOpsError) as broadband:
        ops.spcc_broadband("Nonexistent Camera", "Baader R", "Baader G", "Baader B")
    with pytest.raises(SirilOpsError) as narrow:
        ops.spcc_narrowband(*NARROW, sensor="Nonexistent Camera", r="Baader R", g="Baader G", b="Baader B")
    assert narrow.value.command.startswith('spcc "-monosensor=Nonexistent Camera"') and " -narrowband " in narrow.value.command
    assert str(narrow.value.original) == str(broadband.value.original)
    assert "sensor 'Nonexistent Camera' is not in Siril's SPCC database" in str(narrow.value.original)
    assert stub.STATE.commands == []


def test_an_unknown_filter_name_is_refused_before_sending(stub, ops):
    with pytest.raises(SirilOpsError) as info:
        ops.spcc_narrowband(*NARROW, sensor="Sony IMX571", r="Baader R", g="Baader G", b="Baader R2")
    assert "blue filter 'Baader R2'" in str(info.value.original)
    assert stub.STATE.commands == []


def test_narrowband_with_names_refuses_a_missing_database(stub, tmp_path):
    ops = SirilCommandOps(stub.SirilInterface(), spcc_database=tmp_path / "absent")
    with pytest.raises(SirilOpsError) as info:
        ops.spcc_narrowband(*NARROW, **NAMES)
    assert "not installed" in str(info.value.original)
    assert stub.STATE.commands == []


@pytest.mark.parametrize("names", [
    {"sensor": 'Sony "X"', "r": "Baader R", "g": "Baader G", "b": "Baader B"},
    {"sensor": "Sony IMX571", "r": "Baader R", "g": "Baader G", "b": 'Baader "B"'},
])
def test_a_double_quote_in_a_name_is_refused_before_sending(stub, ops, names):
    rec = RecordingOps(".")
    with pytest.raises(ValueError):
        rec.spcc_narrowband(*NARROW, **names)
    assert rec.transcript == []
    with pytest.raises(ValueError):
        ops.spcc_narrowband(*NARROW, **names)
    assert stub.STATE.commands == []


def test_an_empty_name_is_refused_like_broadband(stub, ops):
    with pytest.raises(ValueError, match="spcc needs the sensor name"):
        RecordingOps(".").spcc_narrowband(*NARROW, sensor="", r="Baader R", g="Baader G", b="Baader B")
    with pytest.raises(ValueError, match="spcc needs the sensor name"):
        ops.spcc_narrowband(*NARROW, sensor="", r="Baader R", g="Baader G", b="Baader B")
    assert stub.STATE.commands == []
