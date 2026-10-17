"""Local Gaia data: the HEALpix (nested) cone, Siril's own catalogue format, and a folder of Loom cone files.

No network: the online fetch is a stub that fails the test when it is called unexpectedly.
"""

from __future__ import annotations

import math
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from loomsiril import gaia, gaia_local, pipeline
from test_siril_script import blocked_discover, script, stub  # noqa: F401

REAL = Path.home() / "Library/Application Support/org.siril.Siril/siril/siril_cat_healpix8_astro.dat"
SCALE = 360 / (2**31 - 1)


# ---- HEALpix ---------------------------------------------------------------------------------------------------------

def test_nested_pixels_of_known_points():
    # base pixel 0 is the north cap whose southern tip is (RA 45, Dec 0): level 0 pixels are the 12 base pixels
    p = gaia_local.ang2pix_nest(1, np.array([45.0, 135.0, 0.0, 90.0, 45.0]), np.array([66.0, 66.0, 10.0, 10.0, -66.0]))
    assert list(p) == [0, 1, 4, 5, 8]
    assert gaia_local.ang2pix_nest(1, np.array([10.0]), np.array([90.0]))[0] in (0, 1, 2, 3)
    assert gaia_local.ang2pix_nest(256, np.array([45.1]), np.array([0.15]))[0] == 0


def test_disc_pixels_cover_every_point_of_the_disc():
    rng = np.random.default_rng(1)
    for ra0, dec0, r in ((10.0, 20.0, 1.2), (359.9, -45.0, 0.7), (45.0, 0.0, 0.5), (200.0, 88.5, 2.0)):
        pix = set(gaia_local.disc_pixels(8, ra0, dec0, r).tolist())
        # random points inside the disc (tangent-plane offsets, small angle) must land in a returned pixel
        a = rng.uniform(0, 2 * math.pi, 400)
        d = r * np.sqrt(rng.uniform(0, 1, 400)) * 0.999
        dec = np.degrees(np.arcsin(np.sin(math.radians(dec0)) * np.cos(np.radians(d))
                                   + np.cos(math.radians(dec0)) * np.sin(np.radians(d)) * np.cos(a)))
        ra = ra0 + np.degrees(np.arctan2(np.sin(a) * np.sin(np.radians(d)) * math.cos(math.radians(dec0)),
                                         np.cos(np.radians(d)) - math.sin(math.radians(dec0)) * np.sin(np.radians(dec))))
        got = gaia_local.ang2pix_nest(256, ra % 360, dec)
        assert set(got.tolist()) <= pix


# ---- Siril's catalogue --------------------------------------------------------------------------------------------------

def _siril_file(path: Path, stars: list[tuple[float, float, int, int, int, int]], level: int = 3) -> None:
    """A tiny file in Siril's astrometry-extract layout: 128-byte header, cumulative per-pixel counts, 16-byte records."""
    nside = 1 << level
    npix = 12 * nside * nside
    pix = gaia_local.ang2pix_nest(nside, np.array([s[0] for s in stars]), np.array([s[1] for s in stars]))
    order = np.argsort(pix, kind="stable")
    counts = np.bincount(pix, minlength=npix)
    header = bytearray(128)
    title = b"Siril Gaia DR3 astrometric extract"
    header[:len(title)] = title
    header[48], header[49], header[50] = 3, level, 1
    body = b"".join(struct.pack("<iihhHh", round(stars[i][0] / SCALE), round(stars[i][1] / SCALE), *stars[i][2:])
                    for i in order)
    path.write_bytes(bytes(header) + np.cumsum(counts).astype("<u4").tobytes() + body)


def test_read_siril_cone_returns_stars_with_nan_parallax(tmp_path):
    f = tmp_path / "cat.dat"
    _siril_file(f, [(150.0, 2.0, 11, -4, 5052, 12000), (150.05, 2.02, 0, 0, 0, 16500), (150.0, 2.0, 1, 1, 1, 19000),
                    (151.5, 2.0, 5, 5, 5000, 10000), (10.0, -40.0, 1, 2, 3000, 9000)])
    recs = gaia_local.siril_cone(f, gaia.GaiaQuery((150.0, 2.0), 0.2, 17.6))
    assert len(recs) == 2                                    # the G 19 star is fainter than the limit, the others are far
    assert sorted(round(r["G"], 3) for r in recs) == [12.0, 16.5]
    r = min(recs, key=lambda s: s["G"])
    assert r["ra"] == pytest.approx(150.0, abs=1e-6) and r["dec"] == pytest.approx(2.0, abs=1e-6)
    assert r["pmra"] == 11 and r["pmdec"] == -4
    assert all(math.isnan(s[k]) for s in recs for k in ("plx", "BP", "RP"))


def test_siril_catalogue_with_the_wrong_size_or_title_is_refused(tmp_path):
    f = tmp_path / "bad.dat"
    f.write_bytes(b"not a catalogue" + bytes(200))
    with pytest.raises(ValueError):
        gaia_local.siril_cone(f, gaia.GaiaQuery((1.0, 1.0), 0.1, 17.6))
    g = tmp_path / "trunc.dat"
    _siril_file(g, [(150.0, 2.0, 0, 0, 0, 12000)])
    g.write_bytes(g.read_bytes()[:-5])
    with pytest.raises(ValueError):
        gaia_local.siril_cone(g, gaia.GaiaQuery((150.0, 2.0), 0.1, 17.6))


def test_siril_catalogue_path_follows_the_platform(tmp_path):
    mac = tmp_path / "Library/Application Support/org.siril.Siril/siril"
    mac.mkdir(parents=True)
    (mac / gaia_local.SIRIL_CATALOGUE_NAME).write_bytes(b"x")
    assert gaia_local.siril_catalogue_path(home=tmp_path, platform="darwin") == mac / gaia_local.SIRIL_CATALOGUE_NAME
    assert gaia_local.siril_catalogue_path(home=tmp_path, platform="linux", env={}) is None


@pytest.mark.skipif(not REAL.exists(), reason="Siril's local Gaia catalogue is not installed")
def test_the_real_siril_catalogue_parses_and_has_stars_near_a_known_position():
    # the Pleiades: Alcyone (Eta Tau) RA 56.871, Dec +24.105, G ~2.9 is brighter than the catalogue's G 8 floor, but the
    # cluster's G 8-14 members are there
    recs = gaia_local.siril_cone(REAL, gaia.GaiaQuery((56.75, 24.12), 0.5, 14.0))
    assert len(recs) > 100
    assert all(0 < r["G"] < 14 and math.isfinite(r["ra"]) for r in recs)
    assert all(math.hypot((r["ra"] - 56.75) * math.cos(math.radians(24.12)), r["dec"] - 24.12) < 0.52 for r in recs)
    # every record of a pixel is in that pixel (the HEALpix scheme matches the file's)
    cat = gaia_local.SirilCatalogue(REAL)
    pix = int(gaia_local.ang2pix_nest(256, np.array([56.75]), np.array([24.12]))[0])
    rows = cat.pixel(pix)
    again = gaia_local.ang2pix_nest(256, rows["ra"], rows["dec"])
    assert (again == pix).all()


# ---- a folder of Loom cone files -----------------------------------------------------------------------------------------

TSV_HEAD = "ra\tdec\tplx\tpmra\tpmdec\tG\tBP\tRP\n"


def _no_net(url):  # pragma: no cover - only runs when a test is wrong
    raise AssertionError("no network in tests: " + url)


def _cone_csv(path: Path, rows: list[tuple]) -> None:
    lines = [",".join(gaia.GAIA_ONLINE_COLUMNS)] + [",".join("" if v is None else repr(v) for v in r) for r in rows]
    path.write_text("\n".join(lines) + "\n")


def test_a_named_cone_file_that_contains_the_query_is_used(tmp_path):
    q = gaia.GaiaQuery((100.0, 10.0), 0.5, 17.6)
    _cone_csv(tmp_path / (gaia.cache_key(gaia.GaiaQuery((100.0, 10.0), 1.0, 17.6)) + ".csv"),
              [(100.0, 10.0, 1.0, 0.1, 0.2, 12.0, 12.5, 11.5), (100.2, 10.1, None, 0, 0, 13.0, None, None),
               (103.0, 10.0, 1.0, 0, 0, 12.0, 12.0, 12.0), (100.1, 10.0, 1.0, 0, 0, 18.0, 18.0, 18.0)])
    recs = gaia_local.folder_sources(q, tmp_path)
    assert recs is not None and len(recs) == 2                # far star and the G 18 star are cut
    assert any(math.isnan(r["plx"]) for r in recs)


def test_a_cone_file_that_does_not_cover_the_field_is_not_used(tmp_path):
    _cone_csv(tmp_path / (gaia.cache_key(gaia.GaiaQuery((100.0, 10.0), 0.2, 17.6)) + ".csv"),
              [(100.0, 10.0, 1.0, 0, 0, 12.0, 12.0, 12.0)])
    assert gaia_local.folder_sources(gaia.GaiaQuery((100.0, 10.0), 0.5, 17.6), tmp_path) is None
    assert gaia_local.folder_sources(gaia.GaiaQuery((100.0, 10.0), 0.1, 17.6), tmp_path / "missing") is None


def test_a_deeper_limit_than_the_file_holds_is_not_covered(tmp_path):
    _cone_csv(tmp_path / (gaia.cache_key(gaia.GaiaQuery((100.0, 10.0), 1.0, 10.0)) + ".csv"),
              [(100.0, 10.0, 1.0, 0, 0, 9.0, 9.0, 9.0)])
    assert gaia_local.folder_sources(gaia.GaiaQuery((100.0, 10.0), 0.5, 17.6), tmp_path) is None
    assert len(gaia_local.folder_sources(gaia.GaiaQuery((100.0, 10.0), 0.5, 10.0), tmp_path)) == 1


def test_a_plain_tsv_table_is_used_when_it_has_stars_in_the_field_and_tables_merge(tmp_path):
    (tmp_path / "a.tsv").write_text(TSV_HEAD + "50.0\t5.0\t2\t1\t1\t12\t12\t12\n50.01\t5.0\t\t1\t1\t13\t\t\n")
    (tmp_path / "b.tsv").write_text(TSV_HEAD + "50.0\t5.0\t2\t1\t1\t12\t12\t12\n50.02\t5.0\t3\t1\t1\t14\t14\t14\n")
    recs = gaia_local.folder_sources(gaia.GaiaQuery((50.0, 5.0), 0.3, 17.6), tmp_path)
    assert len(recs) == 3                                     # the star in both tables counts once
    assert gaia_local.folder_sources(gaia.GaiaQuery((250.0, 5.0), 0.3, 17.6), tmp_path) is None


def test_a_short_header_csv_is_read(tmp_path):
    (tmp_path / "t.csv").write_text("ra,dec,plx,pmra,pmdec,G,BP,RP\n50.0,5.0,2,1,1,12,12,12\n")
    assert len(gaia_local.folder_sources(gaia.GaiaQuery((50.0, 5.0), 0.3, 17.6), tmp_path)) == 1


def test_folder_status_says_what_is_in_the_folder(tmp_path):
    assert "not there" in gaia_local.folder_status(str(tmp_path / "nope"))
    assert "no .csv or .tsv" in gaia_local.folder_status(str(tmp_path))
    (tmp_path / "a.tsv").write_text(TSV_HEAD)
    assert "1 table" in gaia_local.folder_status(str(tmp_path))
    assert gaia_local.folder_status("") == "Browse... for a folder of Gaia cone files"


# ---- the pipeline ---------------------------------------------------------------------------------------------------------

def test_gaia_spec_folder_parses_and_siril_is_refused_with_the_reason(tmp_path):
    assert pipeline._gaia_kind(f"folder:{tmp_path}") == ("folder", str(tmp_path))
    with pytest.raises(ValueError, match="parallax"):
        pipeline._gaia_kind("siril")


def test_folder_that_covers_is_used_and_online_is_not_asked(tmp_path):
    _cone_csv(tmp_path / (gaia.cache_key(gaia.GaiaQuery((100.0, 10.0), 1.0, 17.6)) + ".csv"),
              [(100.0, 10.0, 1.0, 0, 0, 12.0, 12.0, 12.0)])
    job = pipeline.FlyJob(starless=[], stars=[], wcs={}, target=None, target_type="nebula", distance=None,
                          gaia=f"folder:{tmp_path}")
    ctx = pipeline.RunContext(lambda *a: None, lambda: False, _no_net)
    assert len(pipeline._gaia_sources(job, gaia.GaiaQuery((100.0, 10.0), 0.5, 17.6), ctx)) == 1


def test_folder_that_does_not_cover_falls_back_to_the_cache_then_online(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    asked = []

    def fetch(url):
        asked.append(url)
        return ",".join(gaia.GAIA_ONLINE_COLUMNS) + "\n100.0,10.0,1.0,0,0,12.0,12.0,12.0\n"

    empty = tmp_path / "empty"
    empty.mkdir()
    job = pipeline.FlyJob(starless=[], stars=[], wcs={}, target=None, target_type="nebula", distance=None,
                          gaia=f"folder:{empty}")
    ctx = pipeline.RunContext(lambda *a: None, lambda: False, fetch)
    assert len(pipeline._gaia_sources(job, gaia.GaiaQuery((100.0, 10.0), 0.5, 17.6), ctx)) == 1
    assert len(asked) == 1


def test_a_missing_folder_stops_a_run_before_anything_is_fetched(tmp_path):
    job = pipeline.FlyJob(starless=[], stars=[], wcs={}, target=None, target_type="nebula", distance=None,
                          gaia=f"folder:{tmp_path / 'nope'}")
    with pytest.raises(ValueError, match="not there"):
        pipeline._validate(job)


# ---- the dialog row and the model ----------------------------------------------------------------------------------------

def test_the_gaia_folder_row_is_greyed_with_a_reason_unless_gaia_data_is_folder():
    from loomsiril.fly_model import FlyDialogModel

    off = FlyDialogModel(gaia="cache")
    assert not off.enabled("gaia_folder") and "folder" in off.disabled_reason("gaia_folder")
    on = FlyDialogModel(gaia="folder", gaia_folder="/x")
    assert on.enabled("gaia_folder") and on.disabled_reason("gaia_folder") == ""


def test_render_is_refused_until_a_gaia_folder_is_chosen_and_there():
    from loomsiril.fly_model import FlyDialogModel

    m = FlyDialogModel(gaia="folder", gaia_folder="", mode="files", starless_path="a", stars_path="b", out_dir="/o",
                       presets=("youtube_1080",), checked={("starless", "a"): "", ("stars", "b"): "", ("dir", "/o"): ""})
    assert any("Gaia folder" in r for r in m.reasons())
    m.gaia_folder = "/nope"
    assert ("gaia_dir", "/nope") in m.wanted_paths()
    m.checked = {**m.checked, ("gaia_dir", "/nope"): "The Gaia folder is not there: /nope"}
    assert "The Gaia folder is not there: /nope" in m.reasons()
    assert "not there" in m.gaia_note()


def test_the_gaia_note_always_says_about_siril_s_catalogue(monkeypatch):
    from loomsiril.fly_model import FlyDialogModel

    monkeypatch.setattr(gaia_local, "siril_catalogue_path", lambda *a, **k: None)
    assert "not installed" in FlyDialogModel().gaia_note()
    monkeypatch.setattr(gaia_local, "siril_catalogue_path", lambda *a, **k: Path("/x/siril_cat.dat"))
    assert "not used" in FlyDialogModel().gaia_note() and "parallax" in FlyDialogModel().gaia_note()


def test_the_job_spelling_folder_path_loads_into_the_drop_down_and_the_box():
    from loomsiril import fly_dialog

    v = fly_dialog.DialogValues.from_options({"gaia": "folder:/data/gaia"})
    assert (v.gaia, v.gaia_folder) == ("folder", "/data/gaia")
    assert fly_dialog.DialogValues.from_options({"gaia": "weird"}).gaia == "cache"
    assert fly_dialog.DialogValues.from_options({}).gaia == "cache"


def test_gaia_folder_is_a_remembered_path_key():
    from loomsiril import siril_bridge as sb

    assert "gaia_folder" in sb.OPTION_KEYS and "gaia_folder" in sb.PATH_KEYS


def test_the_cli_takes_a_folder_and_refuses_siril(tmp_path, capsys):
    from loomsiril import cli

    assert cli._gaia(f"folder:{tmp_path}") == f"folder:{tmp_path}"
    import argparse

    with pytest.raises(argparse.ArgumentTypeError, match="not there"):
        cli._gaia(f"folder:{tmp_path / 'x'}")
    with pytest.raises(argparse.ArgumentTypeError, match="parallax"):
        cli._gaia("siril")



def test_the_dialog_greys_the_folder_row_and_passes_folder_path_to_the_job(tmp_path, monkeypatch, stub, script,
                                                                           blocked_discover):
    from loomsiril import presets
    from test_siril_script import _dialog

    dlg, _ = _dialog(script, monkeypatch, blocked_discover, gaia="folder", gaia_folder=str(tmp_path),
                     out_dir=str(tmp_path))
    assert dlg._vars["gaia"].get() == "folder" and dlg._vars["gaia_folder"].get() == str(tmp_path)
    assert {st for _w, st in dlg._w["gaia_folder"]} == {"normal"} and len(dlg._w["gaia_folder"]) == 3
    assert dlg.values(presets)["gaia"] == f"folder:{tmp_path}"
    assert dlg._snapshot()["gaia"] == "folder" and dlg._snapshot()["gaia_folder"] == str(tmp_path)
    dlg._vars["gaia"].set("online")
    assert dlg.values(presets)["gaia"] == "online"
    assert dlg._gaia_spec() == "online"
    dlg._vars["gaia"].set("folder")
    dlg._vars["gaia_folder"].set("")
    with pytest.raises(ValueError, match="Gaia folder"):
        dlg.values(presets)
    assert dlg._gaia_spec() == "cache"                       # nothing chosen yet: the preview uses the default
