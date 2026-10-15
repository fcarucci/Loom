"""Narrowband SPCC after a broadband SPCC in one Siril session: the optional sensor and filter names.

No Siril here. SirilCommandOps runs against the sirilpy stub (as tests/loom/test_siril_ops.py does) and RecordingOps
needs nothing. The narrowband command takes all four names (sensor, red, green and blue filter) or none; the four are
checked against the database exactly like the broadband names.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from loomsiril.loom import siril_ops
from loomsiril.loom.siril_ops import RecordingOps, SirilCommandOps, SirilOpsError

STUB = Path(__file__).resolve().parents[1] / "sirilpy_stub"

NARROW = (656.28, 7.0, 500.7, 25.0, 495.9, 20.0)
NARROW_TEXT = "spcc -narrowband -rwl=656.28 -rbw=7.00 -gwl=500.70 -gbw=25.00 -bwl=495.90 -bbw=20.00"
NAMED_TEXT = ('spcc "-monosensor=Sony IMX571" "-rfilter=Baader R" "-gfilter=Baader G" "-bfilter=Baader B" '
              "-narrowband -rwl=656.28 -rbw=7.00 -gwl=500.70 -gbw=25.00 -bwl=495.90 -bbw=20.00")
BROADBAND_TEXT = 'spcc "-monosensor=Sony IMX571" "-rfilter=Baader R" "-gfilter=Baader G" "-bfilter=Baader B"'
NAMES = {"sensor": "Sony IMX571", "r": "Baader R", "g": "Baader G", "b": "Baader B"}


OSC_DB = (
    '[{"name": "Sony IMX571 Red", "model": "Sony IMX571", "type": "OSC_SENSOR", "channel": "RED"},'
    ' {"name": "No filter", "model": "None", "type": "OSC_FILTER"},'
    ' {"name": "Optolong L-Pro", "model": "Optolong L-Pro Light Pollution", "type": "OSC_FILTER"},'
    ' {"name": "Full spectrum (no filter)", "model": "Generic / None", "type": "OSC_LPF"}]'
)
OSC = ("Sony IMX571", "Optolong L-Pro", "Full spectrum (no filter)")
OSC_TEXT = 'spcc "-oscsensor=Sony IMX571" "-oscfilter=Optolong L-Pro" "-osclpf=Full spectrum (no filter)"'
NARROW_TAIL = "-narrowband -rwl=656.28 -rbw=7.00 -gwl=500.70 -gbw=25.00 -bwl=495.90 -bbw=20.00"


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
    (folder / "osc.json").write_text(OSC_DB, encoding="utf-8")
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


def test_recording_call_records_all_eleven_parameters(stub):
    rec = RecordingOps(".")
    rec.spcc_narrowband(*NARROW)
    rec.spcc_narrowband(*NARROW, **NAMES)
    assert rec.calls == [
        ("spcc_narrowband", (*NARROW, None, None, None, None, None), {}),
        ("spcc_narrowband", (*NARROW, "Sony IMX571", "Baader R", "Baader G", "Baader B", None), {}),
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


# ---- OSC and mono calls in one session: each call carries the full set of its own mode -----------------------


def test_mono_after_osc_in_one_session_sends_the_full_mono_set(stub, ops):
    rec = RecordingOps(".")
    for o in (rec, ops):
        o.spcc_osc(*OSC)
        o.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader B")
    assert rec.transcript == [OSC_TEXT, BROADBAND_TEXT]
    assert [c[0] for c in stub.STATE.commands] == [OSC_TEXT, BROADBAND_TEXT]


def test_osc_after_mono_in_one_session_sends_the_full_osc_trio(stub, ops):
    rec = RecordingOps(".")
    for o in (rec, ops):
        o.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader B")
        o.spcc_osc(*OSC)
    assert rec.transcript == [BROADBAND_TEXT, OSC_TEXT]
    assert [c[0] for c in stub.STATE.commands] == [BROADBAND_TEXT, OSC_TEXT]


def test_osc_mono_osc_alternation_never_drops_a_flag(stub, ops):
    for _ in range(2):
        ops.spcc_osc(*OSC)
        ops.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader B")
    sent = [c[0] for c in stub.STATE.commands]
    assert sent == [OSC_TEXT, BROADBAND_TEXT] * 2
    assert all(t.count("=") == 3 for t in sent[0::2]) and all(t.count("=") == 4 for t in sent[1::2])


def test_osc_with_no_front_filter_and_no_lpf_still_sends_all_three_flags_in_a_session(stub, ops):
    ops.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader B")
    ops.spcc_osc("Sony IMX571", None, None)
    assert stub.STATE.commands[-1][0] == ('spcc "-oscsensor=Sony IMX571" "-oscfilter=No filter" '
                                          '"-osclpf=Full spectrum (no filter)"')


def test_narrowband_after_osc_carries_the_osc_trio(stub, ops):
    rec = RecordingOps(".")
    for o in (rec, ops):
        o.spcc_osc(*OSC)
        o.spcc_narrowband(*NARROW, **siril_ops.spcc_narrowband_names("osc", None, OSC))
    assert rec.transcript == [OSC_TEXT, OSC_TEXT + " " + NARROW_TAIL]
    assert [c[0] for c in stub.STATE.commands] == [OSC_TEXT, OSC_TEXT + " " + NARROW_TAIL]


def test_narrowband_after_mono_then_osc_follows_the_latest_mode(stub, ops):
    mono = ("Sony IMX571", "Baader R", "Baader G", "Baader B")
    ops.spcc_broadband(*mono)
    ops.spcc_narrowband(*NARROW, **siril_ops.spcc_narrowband_names("mono", mono, None))
    ops.spcc_osc(*OSC)
    ops.spcc_narrowband(*NARROW, **siril_ops.spcc_narrowband_names("osc", mono, OSC))
    assert [c[0] for c in stub.STATE.commands] == [BROADBAND_TEXT, NAMED_TEXT, OSC_TEXT, OSC_TEXT + " " + NARROW_TAIL]


def test_narrowband_in_a_fresh_session_needs_no_names(stub, ops):
    ops.spcc_narrowband(*NARROW, **siril_ops.spcc_narrowband_names(None, None, None))
    assert stub.STATE.commands == [(NARROW_TEXT,)]
