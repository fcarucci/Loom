"""Narrowband SPCC after a broadband SPCC in one siril-cli session, on a real-sky field (network-marked).

Each test is one siril-cli run. The narrowband command must carry all four database names (sensor, red, green and blue
filter) when a broadband SPCC ran earlier in the same session: with them both calibrations succeed; without them the
second call fails with Siril's "Argument error". Needs LOOMSIRIL_NETWORK=1, Siril's online catalogues and the SPCC
database (see docker/network-setup.sh); marker ``siril`` and ``network``.
"""

from __future__ import annotations


import numpy as np
import pytest

from loomsiril.loom.siril_ops import spcc_names

from ..siril_run import run_pyscript
from .. import synth
from .test_siril_ops_cli import FIELD_RA, FIELD_DEC, _real_sky_field, _spcc_folder

pytestmark = pytest.mark.siril

#: Narrowband wavelengths and bandwidths (nm) of the three channels.
NARROW = (656.28, 7.0, 500.7, 25.0, 495.9, 20.0)

SCRIPT_SESSION = r'''
import json, os
import numpy as np
import sirilpy as s
from loomsiril.loom.siril_ops import SirilCommandOps, SirilOpsError

siril = s.SirilInterface(); siril.connect()
ops = SirilCommandOps(siril, log=siril.log)
root = siril.get_siril_wd()
p = lambda name: os.path.join(root, name)
out = {}
NARROW = @NARROW@
NAMES = {"sensor": @SENSOR@, "r": @RNAME@, "g": @GNAME@, "b": @BNAME@}


def change(before, after):
    """Largest absolute change between two lists of planes."""
    return max(float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64)))) for a, b in zip(before, after))


ops.rgbcomp(p("field_nowcs_r.fits"), p("field_nowcs_g.fits"), p("field_nowcs_b.fits"), p("field_rgb"))
ops.load(p("field_rgb.fit"))
ops.platesolve()
out["solved"] = "CRVAL1" in ops.header()
@SESSION@
print("RESULT=" + json.dumps(out), flush=True)
'''

SESSION_BOTH_WITH_NAMES = r'''
before = ops.pixels()
ops.spcc_broadband(NAMES["sensor"], NAMES["r"], NAMES["g"], NAMES["b"])
after_broadband = ops.pixels()
out["broadband_change"] = change(before, after_broadband)
ops.spcc_narrowband(*NARROW, **NAMES)
out["narrowband_change"] = change(after_broadband, ops.pixels())
'''

SESSION_NARROWBAND_WITHOUT_NAMES = r'''
ops.spcc_broadband(NAMES["sensor"], NAMES["r"], NAMES["g"], NAMES["b"])
out["broadband_ran"] = True
try:
    ops.spcc_narrowband(*NARROW)
    out["narrowband_refused"] = False
except SirilOpsError as exc:
    out["narrowband_refused"] = True
    out["narrowband_error"] = str(exc.original)
'''


def _script(session: str, names: tuple[str, str, str, str]) -> str:
    sensor, r, g, b = (repr(n) for n in names)
    return (SCRIPT_SESSION.replace("@NARROW@", repr(NARROW)).replace("@SENSOR@", sensor)
            .replace("@RNAME@", r).replace("@GNAME@", g).replace("@BNAME@", b)
            .replace("@SESSION@", session))


def _database_names() -> tuple[str, str, str, str]:
    folder = _spcc_folder()
    sensors = spcc_names("monosensor", folder)
    reds, greens, blues = (spcc_names(k, folder) for k in ("redfilter", "greenfilter", "bluefilter"))
    if not (sensors and reds and greens and blues):
        pytest.fail(f"the SPCC database at {folder} lacks a sensor or a filter name")
    return sensors[0], reds[0], greens[0], blues[0]


def _write_field_frames(tmp_path, gaia_tsv_path) -> None:
    data = np.genfromtxt(gaia_tsv_path, delimiter="\t", names=True)
    near = np.hypot((data["ra"] - FIELD_RA) * np.cos(np.radians(FIELD_DEC)), data["dec"] - FIELD_DEC) < 0.15
    rows = np.column_stack([data["ra"][near], data["dec"][near], data["G"][near]])
    rows = rows[rows[:, 2] < 13.0]
    assert len(rows) >= 30, "too few bright Gaia stars in the field"
    for channel, tint in (("R", 1.0), ("G", 0.8), ("B", 0.6)):
        synth.write_master(tmp_path / f"field_nowcs_{channel.lower()}.fits",
                           _real_sky_field(channel, 0.05, tint, rows, wcs=False))


@pytest.mark.network
def test_broadband_then_narrowband_with_all_four_names(tmp_path, gaia_tsv_path) -> None:
    """One session: broadband SPCC, then narrowband SPCC with the four names. Both succeed and change the pixels."""
    names = _database_names()
    _write_field_frames(tmp_path, gaia_tsv_path)

    res = run_pyscript(_script(SESSION_BOTH_WITH_NAMES, names), tmp_path).check().value("RESULT")

    assert res["solved"] is True
    assert res["broadband_change"] > 0.0
    assert res["narrowband_change"] > 0.0


@pytest.mark.network
def test_narrowband_without_names_after_broadband_is_refused(tmp_path, gaia_tsv_path) -> None:
    """Pins the Siril behaviour: after a broadband SPCC, narrowband SPCC without the four names fails."""
    names = _database_names()
    _write_field_frames(tmp_path, gaia_tsv_path)

    res = run_pyscript(_script(SESSION_NARROWBAND_WITHOUT_NAMES, names), tmp_path).check().value("RESULT")

    assert res["solved"] is True
    assert res["broadband_ran"] is True
    assert res["narrowband_refused"] is True
    assert res["narrowband_error"]
