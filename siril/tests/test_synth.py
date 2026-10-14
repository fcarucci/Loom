"""The synthetic field builder and the suite rules (network guard, fixture)."""

from __future__ import annotations

import socket

import numpy as np
import pytest

import synth
from loomsiril.core import unscreen


def test_field_is_deterministic_and_consistent(synth_field):
    a = synth_field(seed=3)
    b = synth_field(seed=3)
    assert np.array_equal(a.image[0], b.image[0])
    assert len(a.truth) == 30 and len(a.catalogue) == 35
    assert a.image[0].shape == (90, 160) and a.image[0].dtype == np.float32
    # unscreen(image, starless) gives back the stars layer
    assert np.max(np.abs(unscreen(a.image[0], a.starless[0]) - a.stars[0])) < 1e-6
    # each star's peak sits at its injected position
    for s in a.truth:
        assert a.stars[0][round(s.y), round(s.x)] > 0.5 * s.peak


def test_catalogue_round_trips_through_tan(synth_field):
    f = synth_field(seed=5, nc=3)
    assert f.nc == 3 and len(f.image) == 3
    s = f.truth[0]
    assert synth.tan_unproject(s.x, s.y, f.wcs) == (s.ra, s.dec)
    centre = synth.tan_unproject(f.wcs["crpix1"] - 1, f.wcs["crpix2"] - 1, f.wcs)
    assert abs(centre[0] - f.wcs["crval1"]) < 1e-12 and abs(centre[1] - f.wcs["crval2"]) < 1e-12
    assert set(f.catalogue[0]) == {"ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP"}


def test_network_is_blocked():
    with pytest.raises(Exception, match="network access is not allowed"):
        socket.create_connection(("192.0.2.1", 80), timeout=0.1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(Exception, match="network access is not allowed"):
            sock.connect(("192.0.2.1", 80))
    finally:
        sock.close()


def test_gaia_fixture_present(gaia_tsv_path):
    lines = gaia_tsv_path.read_text().splitlines()
    assert lines[0].split("\t") == ["ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP"]
    assert len(lines) == 8094
