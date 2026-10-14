"""loomsiril.wcs against the JS oracle (tests/oracle/wcs.json), the synthetic TAN header, and known answers."""

from __future__ import annotations

import math

import pytest

from loomsiril import core, wcs
from loomsiril.core import separation, tan_project


def test_scaled_wcs_matches_js(oracle):
    for case in oracle("wcs")["cases"]["scaledWcs"]:
        w, f = case["args"]
        got = wcs.scaled_wcs(w, f)
        want = case["out"]
        assert got["crval1"] == want["crval1"] and got["crval2"] == want["crval2"]
        assert got["crpix1"] == pytest.approx(want["crpix1"], rel=0, abs=1e-12)
        assert got["crpix2"] == pytest.approx(want["crpix2"], rel=0, abs=1e-12)
        assert got["cd"] == pytest.approx(want["cd"], rel=0, abs=1e-15)


def test_tan_project_of_scaled_wcs_matches_js(oracle):
    for case in oracle("wcs")["cases"]["tanProjectScaled"]:
        a, b, w = case["args"]
        got = tan_project(a, b, w)
        assert got.x == pytest.approx(case["out"]["x"], rel=0, abs=1e-9)
        assert got.y == pytest.approx(case["out"]["y"], rel=0, abs=1e-9)


def test_tan_unproject_of_scaled_wcs_matches_js(oracle):
    for case in oracle("wcs")["cases"]["tanUnprojectScaled"]:
        x, y, w = case["args"]
        got = wcs.unprojector(w)(x, y)
        assert got["ra"] == pytest.approx(case["out"]["ra"], rel=0, abs=1e-9)
        assert got["dec"] == pytest.approx(case["out"]["dec"], rel=0, abs=1e-9)


def test_keyword_number_strips_quotes_like_js():
    hdr = {"CRVAL1": "'324.745'", "CRVAL2": 57.514, "CD1_1": "-5.0E-4", "BAD": "'abc'", "FLAG": True,
           "PAD": "  12.5  "}
    assert wcs.keyword_number(hdr, "CRVAL1") == 324.745
    assert wcs.keyword_number(hdr, "CRVAL2") == 57.514
    assert wcs.keyword_number(hdr, "CD1_1") == -5.0e-4
    assert wcs.keyword_number(hdr, "BAD") is None
    assert wcs.keyword_number(hdr, "FLAG") is None
    assert wcs.keyword_number(hdr, "PAD") == 12.5
    assert wcs.keyword_number(hdr, "MISSING") is None


def test_keyword_wcs_from_header_and_missing_key():
    hdr = {"CRVAL1": "'324.745'", "CRVAL2": "'57.514'", "CRPIX1": 200.5, "CRPIX2": "150.5",
           "CD1_1": "-0.0005", "CD1_2": "0.00001", "CD2_1": "0.00002", "CD2_2": "0.0005"}
    w = wcs.keyword_wcs(hdr)
    assert w == {"crval1": 324.745, "crval2": 57.514, "crpix1": 200.5, "crpix2": 150.5,
                 "cd": [-0.0005, 0.00001, 0.00002, 0.0005]}
    del hdr["CD2_2"]
    assert wcs.keyword_wcs(hdr) is None


def test_projector_puts_crval_on_crpix(synth_field):
    f = synth_field(w=160, h=90, n_stars=3, seed=2)
    proj = wcs.projector(f.wcs)
    p = proj(f.wcs["crval1"], f.wcs["crval2"])
    assert p.x == pytest.approx(f.wcs["crpix1"] - 1, abs=1e-9)
    assert p.y == pytest.approx(f.wcs["crpix2"] - 1, abs=1e-9)
    # and the unprojector is its inverse
    back = wcs.unprojector(f.wcs)(p.x, p.y)
    assert back["ra"] == pytest.approx(f.wcs["crval1"], abs=1e-9)
    assert back["dec"] == pytest.approx(f.wcs["crval2"], abs=1e-9)


def test_field_radius_reaches_every_corner(synth_field):
    f = synth_field(w=160, h=90, n_stars=3, seed=3)
    fld = wcs.field(f.wcs, 160, 90)
    assert set(fld) == {"centre", "radius_deg"}
    corners = wcs.field_corners(f.wcs, 160, 90)
    assert len(corners) == 4
    far = max(separation(fld["centre"], c) for c in corners)
    assert fld["radius_deg"] == pytest.approx(far, rel=0, abs=1e-12)
    for c in corners:
        assert separation(fld["centre"], c) <= fld["radius_deg"] + 1e-12
    # the centre is the image centre (pixel (w/2, h/2))
    centre_px = wcs.unprojector(f.wcs)(80, 45)
    assert separation(fld["centre"], centre_px) < 1e-9


def test_field_corners_are_the_pixel_corners(synth_field):
    f = synth_field(w=160, h=90, n_stars=3, seed=4)
    un = wcs.unprojector(f.wcs)
    assert wcs.field_corners(f.wcs, 160, 90) == [un(0, 0), un(160, 0), un(0, 90), un(160, 90)]


def test_solved_scale_of_synth_header(synth_field):
    f = synth_field(w=160, h=90, n_stars=3, seed=5, scale_arcsec=2.0)
    s = wcs.solved_scale(f.wcs, 160, 90)
    assert s == pytest.approx(2.0, abs=0.01)


def test_solved_scale_matches_formula():
    w = {"crval1": 10.0, "crval2": 41.0, "crpix1": 50.5, "crpix2": 40.5, "cd": [-0.001, 0.0, 0.0, 0.001]}
    un = wcs.unprojector(w)
    expect = 3600 * separation(un(0, 20), un(100, 20)) / 100
    assert wcs.solved_scale(w, 100, 40) == pytest.approx(expect, rel=0, abs=1e-12)


def test_scaled_wcs_keeps_the_sky_at_pixel_centres():
    w = {"crval1": 324.745, "crval2": 57.514, "crpix1": 200.5, "crpix2": 150.5, "cd": [-0.0005, 1e-5, 2e-5, 0.0005]}
    s = wcs.scaled_wcs(w, 2.0)
    # the resampled pixel centre j sits at old pixel (j + 0.5) / 2 - 0.5
    for old_x, old_y in [(10.0, 20.0), (199.5, 149.5)]:
        new_x, new_y = (old_x + 0.5) * 2.0 - 0.5, (old_y + 0.5) * 2.0 - 0.5
        a = wcs.unprojector(w)(old_x, old_y)
        b = wcs.unprojector(s)(new_x, new_y)
        assert separation(a, b) < 1e-9


def test_wcs_from_solution_accepts_cli_json():
    d = {"crval1": 10, "crval2": 41, "crpix1": 50.5, "crpix2": 40.5, "cd": [-0.001, 0, 0, 0.001]}
    w = wcs.wcs_from_solution(d)
    assert w == {"crval1": 10.0, "crval2": 41.0, "crpix1": 50.5, "crpix2": 40.5, "cd": [-0.001, 0.0, 0.0, 0.001]}
    assert all(isinstance(v, float) for v in w["cd"])


@pytest.mark.parametrize("bad", [
    {"crval1": 10, "crval2": 41, "crpix1": 50, "cd": [1, 0, 0, 1]},            # missing crpix2
    {"crval1": 10, "crval2": 41, "crpix1": 50, "crpix2": 40, "cd": [1, 0, 0]},  # cd not four numbers
    {"crval1": "x", "crval2": 41, "crpix1": 50, "crpix2": 40, "cd": [1, 0, 0, 1]},
    {"crval1": math.nan, "crval2": 41, "crpix1": 50, "crpix2": 40, "cd": [1, 0, 0, 1]},
    {"crval1": 10, "crval2": 41, "crpix1": 50, "crpix2": 40, "cd": [1, 0, 0, math.inf]},
])
def test_wcs_from_solution_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        wcs.wcs_from_solution(bad)


# ---------------------------------------------------------------- row order (top-down inside loomsiril)


def _asym_wcs(w: int = 160, h: int = 90) -> dict:
    """An asymmetric TAN wcs: 17 degree rotation, CRPIX off-centre, so a row or column mix-up cannot pass."""
    import synth

    header = synth.tan_header(w, h, 324.745, 57.514, 2.0, rot_deg=17)
    header["CRPIX1"] = header["CRPIX1"] + 13.25
    header["CRPIX2"] = header["CRPIX2"] - 21.5
    return synth.wcs_from_header(header)


def test_flip_rows_is_an_involution():
    w = _asym_wcs()
    back = wcs.flip_rows(wcs.flip_rows(w, 90), 90)
    assert back["crpix1"] == w["crpix1"] and back["crval1"] == w["crval1"] and back["crval2"] == w["crval2"]
    assert back["crpix2"] == pytest.approx(w["crpix2"], rel=0, abs=1e-12)
    assert back["cd"] == pytest.approx(w["cd"], rel=0, abs=1e-15)


def test_flip_rows_flips_the_sky_mapping_for_an_asymmetric_wcs():
    h = 90
    w = _asym_wcs(160, h)
    flipped = wcs.flip_rows(w, h)
    worst = 0.0
    for x in range(0, 160, 7):
        for y in range(0, h, 5):
            a = core.tan_unproject(x, y, w)
            b = core.tan_unproject(x, h - 1 - y, flipped)
            worst = max(worst, abs(a["ra"] - b["ra"]) * math.cos(math.radians(a["dec"])), abs(a["dec"] - b["dec"]))
    assert worst < 1e-9


def test_flip_rows_negating_cd2_1_instead_of_cd1_2_is_wrong():
    # The wrong variant negates cd[2] (CD2_1, which multiplies dx) where the flip needs cd[1] (CD1_2, dy).
    h = 90
    w = _asym_wcs(160, h)
    c = w["cd"]
    wrong = dict(wcs.flip_rows(w, h), cd=[c[0], c[1], -c[2], -c[3]])
    worst = 0.0
    for x in range(0, 160, 7):
        for y in range(0, h, 5):
            a = core.tan_unproject(x, y, w)
            b = core.tan_unproject(x, h - 1 - y, wrong)
            worst = max(worst, abs(a["ra"] - b["ra"]) * math.cos(math.radians(a["dec"])), abs(a["dec"] - b["dec"]))
    assert worst > 1e-3
