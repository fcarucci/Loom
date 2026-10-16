"""The local star catalogue of the SPCC tests (tests/loom/gaia_xpsamp.py): its fields and its HEALPix pixels."""

from __future__ import annotations

import json
from pathlib import Path

from . import gaia_xpsamp as G
from . import synth
from .integration.test_siril_ops_cli import FIELD_DEC, FIELD_RA


def test_the_catalogue_covers_the_fields_the_spcc_tests_calibrate() -> None:
    centres = {(ra, dec) for ra, dec, _radius in G.TEST_FIELDS}
    assert centres == {(FIELD_RA, FIELD_DEC), (synth.RA0, synth.DEC0)}
    assert all(radius >= 0.5 for _ra, _dec, radius in G.TEST_FIELDS)   # Siril asks for about 0.35 degrees


def test_pixels_are_level_8_nest_and_the_disc_is_whole() -> None:
    assert G.ang2pix_nest(0.0, 90.0) < G.NSIDE ** 2              # north pole: base face 0
    assert 8 * G.NSIDE ** 2 <= G.ang2pix_nest(0.0, -90.0) < 9 * G.NSIDE ** 2   # south pole: base face 8
    assert all(0 <= G.ang2pix_nest(ra, dec) < 12 * G.NSIDE ** 2 for ra in range(0, 360, 7) for dec in range(-89, 90, 7))
    near = G.pixels_near(FIELD_RA, FIELD_DEC, 1.0)
    assert G.ang2pix_nest(FIELD_RA, FIELD_DEC) in near
    assert G.ang2pix_nest(FIELD_RA, FIELD_DEC + 1.2) in near and G.ang2pix_nest(FIELD_RA, FIELD_DEC + 3) not in near
    assert 50 < len(near) < 400   # level-8 pixels are about 0.05 square degrees


def test_covered_needs_every_wanted_pixel_and_its_chunk_file(tmp_path: Path) -> None:
    wanted = G.wanted_pixels()
    assert not G.covered(tmp_path)
    (tmp_path / G.MANIFEST).write_text(json.dumps({str(c): p for c, p in wanted.items()}), encoding="utf-8")
    assert not G.covered(tmp_path)   # no chunk files
    for chunk in wanted:
        (tmp_path / G.chunk_name(chunk)).write_bytes(b"")
    assert G.covered(tmp_path)
    first = next(iter(wanted))
    (tmp_path / G.MANIFEST).write_text(json.dumps({str(first): wanted[first][1:]}), encoding="utf-8")
    assert not G.covered(tmp_path)


def test_the_default_folder_is_the_one_siril_reads(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert G.default_folder() == tmp_path / "siril" / "gaia_photometric.dat"
