"""Channel rules, selection checks, master ranking, palettes and camera mapping (loomsiril/loom/channels.py).

The oracle replay checks every recorded case of tests/oracle/loom_channels.json against the port. The other tests
pin the behaviour the oracle cannot: the Siril sensor table, the messages verbatim, the disk-facing scan with
synthetic FITS masters, and the import-time guarantees.
"""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.loom import channels as ch

from . import synth

ORACLE_FILE = Path(__file__).resolve().parents[1] / "oracle" / "loom_channels.json"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Siril 1.4.4's SPCC sensor database (measured) names the port may return.
SIRIL_SENSORS = {
    "Generic mono sensor", "ICX694", "KAF1603ME", "KAF16200", "KAF16803", "KAF3200", "KAF8300", "KAI-11000M",
    "Panasonic MN34230ALJ", "Sony IMX178", "Sony IMX183", "Sony IMX411/455/461/533/571", "Sony IMX492/294",
    "Sony IMX585", "onsemi KAF-09000",
}

#: Loom's PixInsight curve name -> the Siril sensor name the port must return for the same camera.
CURVE_TO_SIRIL = {
    "Sony IMX411/455/461/533/571": "Sony IMX411/455/461/533/571",
    "Panasonic MN34230 (ASI1600MM)": "Panasonic MN34230ALJ",
    "Sony IMX178": "Sony IMX178",
    "Sony IMX183": "Sony IMX183",
    "Sony IMX492": "Sony IMX492/294",
    "Sony IMX585": "Sony IMX585",
    "KAF-16200": "KAF16200",
    "KAF-16803": "KAF16803",
    "KAF-8300": "KAF8300",
}


def _load_cases() -> dict[str, Any]:
    with ORACLE_FILE.open(encoding="utf-8") as fh:
        doc = json.load(fh, parse_constant=_parse_constant)
    return doc["cases"]


def _parse_constant(name: str) -> float:
    return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}[name]


CASES = _load_cases()


def _same(a: Any, b: Any) -> bool:
    """Equality where NaN equals NaN and 2 equals 2.0; bools never equal numbers."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if a is None or b is None:
        return a is b
    return a == b


def _run(case: dict[str, Any]) -> tuple[Any, Any]:
    """Call the port with the recorded arguments; returns (actual, expected)."""
    fn, args, expected = case["fn"], copy.deepcopy(case["args"]), case["result"]
    if fn == "inherit_instrument":
        logs = ch.inherit_instrument(args[0])
        return {"logs": logs, "chans": args[0]}, expected
    target = getattr(ch, fn)
    if not callable(target):
        return target, expected
    if fn == "deserialize_entries":
        # Loom restores view entries; the port drops them (with a warning), so only file entries are compared.
        expected = [e for e in expected if e["source"] == "file"]
    return target(*args), expected


@pytest.mark.parametrize("name", sorted(CASES))
def test_oracle_replay(name: str) -> None:
    actual, expected = _run(CASES[name])
    assert _same(actual, expected), f"{name}: port returned {actual!r}, Loom returned {expected!r}"


def test_oracle_covers_every_ported_function() -> None:
    fns = {c["fn"] for c in CASES.values()}
    for needed in ("channel_from_filter", "validate_selection", "serialize_entries", "deserialize_entries",
                   "factor_label", "drizzle_label", "drizzle_scale_from_history", "integration_drizzle_label",
                   "master_drizzle_label", "qe_curve_name_for_camera", "palette_wavelengths", "palette_missing",
                   "is_autocrop_name", "is_master_light", "master_variant_rank", "parse_master_name",
                   "select_masters", "common_instrument", "project_name_from_path", "project_name_for",
                   "psb_base_name", "narrowband_supplied", "wanted_palettes", "report_skipped_palettes",
                   "inherit_instrument"):
        assert needed in fns, needed
    assert len(CASES) >= 400


# ---------------------------------------------------------------------------
# Selection messages, verbatim (Util.js:603-661)
# ---------------------------------------------------------------------------


def test_selection_messages_are_loom_text() -> None:
    assert ch.validate_selection({"R": "/d/r.xisf", "G": "/d/g.xisf"}) == [
        "Incomplete RGB set: missing B. Supply all three or none."]
    assert ch.validate_selection({}) == [
        "Nothing to do: supply R, G and B, or at least one of H, S, O"]
    assert ch.validate_selection({"R": "/x", "G": "/x", "B": "/b"}) == ["Same file selected for R and G: /x"]
    assert ch.validate_selection({"R": "/x", "G": "/x", "B": "/b"}, {}) == ["Same file selected for R and G: /x"]


def test_nb_only_and_lum_only_are_valid_selections() -> None:
    assert ch.validate_selection({"H": "/h.xisf"}) == []
    assert ch.validate_selection({"L": "/l.xisf"}) == ["Nothing to do: supply R, G and B, or at least one of H, S, O"]
    assert ch.validate_selection({"R": "/r"}, {"G": "view", "B": "view"}) == []


# ---------------------------------------------------------------------------
# Saved selection list: only file entries are restored
# ---------------------------------------------------------------------------


def test_saved_list_drops_views_with_a_warning() -> None:
    warnings: list[str] = []
    entries = ch.deserialize_entries("file\t/a/b.xisf\nview\tHaView\nfile\t/c.xisf", warn=warnings.append)
    assert entries == [{"source": "file", "ref": "/a/b.xisf"}, {"source": "file", "ref": "/c.xisf"}]
    assert len(warnings) == 1 and "HaView" in warnings[0]


def test_saved_list_round_trip_keeps_paths_with_spaces() -> None:
    entries = [{"source": "file", "ref": "/data/with space/x y.xisf"}, {"source": "file", "ref": "/d/a.xisf"}]
    assert ch.deserialize_entries(ch.serialize_entries(entries)) == entries


# ---------------------------------------------------------------------------
# SPCC sensor mapping (Siril names; Loom's matching order)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("instrume, sensor", [
    ("ZWO ASI2600MM", "Sony IMX411/455/461/533/571"),
    ("ZWO ASI2600MM Pro", "Sony IMX411/455/461/533/571"),
    ("ZWO ASI533MM Pro", "Sony IMX411/455/461/533/571"),
    ("ZWO ASI6200MM", "Sony IMX411/455/461/533/571"),
    ("QHY268M", "Sony IMX411/455/461/533/571"),
    ("ZWO ASI1600MM", "Panasonic MN34230ALJ"),
    ("ZWO ASI183MM", "Sony IMX183"),
    ("ZWO ASI294MM", "Sony IMX492/294"),
    ("ZWO ASI585MM", "Sony IMX585"),
    ("ZWO ASI178MMC", "Sony IMX178"),
    ("KAF-16803", "KAF16803"),
    ("KAF-8300", "KAF8300"),
    ("KAF-16200", "KAF16200"),
    ("Canon EOS R", "Generic mono sensor"),
    ("ZWO ASI071MC Pro", "Generic mono sensor"),
    ("", "Generic mono sensor"),
    (None, "Generic mono sensor"),
])
def test_spcc_sensor_for(instrume: Any, sensor: str) -> None:
    assert ch.spcc_sensor_for(instrume) == sensor


def test_spcc_sensor_table_names_are_siril_sensors() -> None:
    assert set(ch.SPCC_SENSOR_TABLE.values()) <= SIRIL_SENSORS
    assert ch.GENERIC_SENSOR in SIRIL_SENSORS


def test_spcc_sensor_agrees_with_loom_curve_for_every_mapped_camera() -> None:
    checked = 0
    for case in CASES.values():
        if case["fn"] != "qe_curve_name_for_camera" or case["result"] is None:
            continue
        instrume = case["args"][0]
        assert ch.spcc_sensor_for(instrume) == CURVE_TO_SIRIL[case["result"]], instrume
        checked += 1
    assert checked >= 15


def test_spcc_table_has_loom_matching_order() -> None:
    """A camera in both families resolves to the first Loom entry: IMX571 before IMX492, etc."""
    assert ch.spcc_sensor_for("ZWO ASI2600MM ASI294") == "Sony IMX411/455/461/533/571"
    assert list(ch.SPCC_SENSOR_TABLE)[0].startswith("asi2600")


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------


def test_palette_wavelengths_use_loom_line_values() -> None:
    assert ch.NARROWBAND_NM == {"H": 656.28, "S": 671.60, "O": 500.70}
    assert ch.palette_wavelengths("HOO") == [
        {"channel": "H", "nm": 656.28}, {"channel": "O", "nm": 500.70}, {"channel": "O", "nm": 500.70}]


# ---------------------------------------------------------------------------
# Disk-facing: header reads (FITS) and the master-folder scan
# ---------------------------------------------------------------------------


def _write_fits_master(folder: Path, name: str, channel: str = "H", drizzle: int = 1, filter_name: str | None = None,
                       imagetyp: str | None = None) -> Path:
    extra = {"IMAGETYP": imagetyp} if imagetyp is not None else None
    master = synth.make_master(channel, w=48, h=40, drizzle=drizzle, filter_name=filter_name,
                               catalogue=synth.sky_catalogue(n_stars=12), extra=extra)
    return synth.write_master(folder / name, master)


def test_read_header_fits_gives_keywords_and_size(tmp_path: Path) -> None:
    path = _write_fits_master(tmp_path, "masterLight_FILTER-Ha_mono.fits", channel="H", drizzle=2,
                              filter_name="Ha")
    info = ch.read_header(str(path))
    assert info["format"] == "fits"
    assert (info["width"], info["height"]) == (96, 80)
    assert ch.keyword_value(info["keywords"], "FILTER") == "Ha"
    assert ch.keyword_value(info["keywords"], "INSTRUME") == "ZWO ASI2600MM Pro"
    assert ch.master_drizzle_label(info) == "2x"


def test_read_header_rejects_other_files(tmp_path: Path) -> None:
    junk = tmp_path / "x.xisf"
    junk.write_bytes(b"NOTAFITSFILE" * 100)
    with pytest.raises(ValueError):
        ch.read_header(str(junk))


def _stat_free(paths: dict[str, float]) -> Any:
    return lambda p: paths.get(os.path.basename(p), 0.0)


def test_scan_masters_picks_loom_winners(tmp_path: Path) -> None:
    base = "masterLight_BIN-1_48x40_EXPOSURE-300.00s_FILTER-{f}_mono{rest}"
    _write_fits_master(tmp_path, base.format(f="Ha", rest="_drizzle_2x_autocrop.fits"), "H", 2,
                       filter_name="Ha")
    _write_fits_master(tmp_path, base.format(f="Ha", rest="_mono.fits"), "H", 1, filter_name="Ha")
    _write_fits_master(tmp_path, base.format(f="Ha", rest="_drizzle_2x.fits"), "H", 2, filter_name="Ha")
    _write_fits_master(tmp_path, base.format(f="L", rest="_drizzle_2x_autocrop_(1).fits"), "L", 2,
                       filter_name="L")
    newer = _write_fits_master(tmp_path, base.format(f="L", rest="_drizzle_2x_autocrop_(2).fits"), "L", 2,
                               filter_name="L")
    _write_fits_master(tmp_path, "masterDark_BIN-1_48x40_EXPOSURE-300.00s_FILTER-Ha.fits", "H", 1,
                       filter_name="Ha", imagetyp="Dark")
    _write_fits_master(tmp_path, base.format(f="B", rest="_mono.fits"), "B", 1, filter_name="B",
                       imagetyp="Master Dark Frame")
    os.utime(newer, (2_000_000_000, 2_000_000_000))

    scan = ch.scan_masters(str(tmp_path))
    assert scan is not None
    assert scan["total"] == 7
    assert sorted(scan["confirmed"]) == ["H", "L"]
    assert scan["confirmed"]["H"]["name"] == base.format(f="Ha", rest="_drizzle_2x_autocrop.fits")
    assert scan["confirmed"]["H"]["drizzle"] == "2x" and scan["confirmed"]["H"]["autocrop"] is True
    assert scan["confirmed"]["L"]["name"] == "masterLight_BIN-1_48x40_EXPOSURE-300.00s_FILTER-L_mono_drizzle_2x_"\
        "autocrop_(2).fits"
    assert scan["skipped"] == 1  # the B master whose IMAGETYP is a dark frame
    assert scan["corrected"] == []


def test_scan_masters_header_filter_wins_over_the_name(tmp_path: Path) -> None:
    _write_fits_master(tmp_path, "masterLight_FILTER-R_mono.fits", "G", 1, filter_name="G")
    scan = ch.scan_masters(str(tmp_path))
    assert scan is not None
    assert scan["corrected"] == ["masterLight_FILTER-R_mono.fits: name says R, header says G"]
    assert list(scan["confirmed"]) == ["G"]


def test_scan_masters_opens_unnamed_files_only_when_nothing_is_named(tmp_path: Path) -> None:
    _write_fits_master(tmp_path, "stack_final.fits", "O", 1, filter_name="OIII")
    scan = ch.scan_masters(str(tmp_path))
    assert scan is not None
    assert scan["confirmed"]["O"]["name"] == "stack_final.fits"
    assert scan["corrected"] == []


def test_scan_masters_missing_folder_returns_none(tmp_path: Path) -> None:
    assert ch.scan_masters(str(tmp_path / "absent")) is None


def test_scan_masters_reads_only_the_winners_with_injected_io() -> None:
    names = ["masterFlat_FILTER-Ha.xisf", "masterLight_FILTER-Ha_mono_drizzle_2x.xisf",
             "masterLight_FILTER-Ha_mono.xisf", "masterLight_FILTER-S_mono_autocrop.xisf",
             "masterLight_FILTER-L.xisf", "frame.fits", ".hidden_FILTER-B.xisf", "notes.txt"]
    headers = {"masterLight_FILTER-Ha_mono_drizzle_2x.xisf": {"keywords": [{"name": "FILTER", "value": "'Ha'"},
                                                                          {"name": "XPIXSZ", "value": "1.88"}]},
               "masterLight_FILTER-S_mono_autocrop.xisf": {"keywords": [{"name": "FILTER", "value": "'SII'"}]},
               "masterLight_FILTER-L.xisf": {"keywords": [{"name": "FILTER", "value": "'L'"}]}}
    opened: list[str] = []

    def fake_header(path: str) -> dict[str, Any] | None:
        opened.append(os.path.basename(path))
        h = headers.get(os.path.basename(path))
        return None if h is None else dict(h, keywords=h["keywords"])

    scan = ch.scan_masters("/fake", read_header=fake_header, stat=lambda p: 5.0,
                           list_dir=lambda d: names, is_dir=lambda p: p == "/fake")
    assert scan is not None
    assert scan["total"] == 6
    assert sorted(scan["confirmed"]) == ["H", "L", "S"]
    assert sorted(opened) == sorted(["masterLight_FILTER-Ha_mono_drizzle_2x.xisf",
                                     "masterLight_FILTER-S_mono_autocrop.xisf", "masterLight_FILTER-L.xisf"])


# ---------------------------------------------------------------------------
# Import-time guarantees
# ---------------------------------------------------------------------------


def test_modules_import_without_siril_or_tkinter() -> None:
    code = ("import sys; import loomsiril.loom.channels, loomsiril.loom.xisf; "
            "bad = [m for m in ('sirilpy', 'tkinter') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    done = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(REPO_ROOT), capture_output=True)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")


def test_numpy_float_inputs_are_accepted() -> None:
    assert ch.drizzle_label(np.float64(1.88)) == "2x"
    assert ch.drizzle_label(np.float32(3.76)) == ""


# ---------------------------------------------------------------------------
# Separators: PJSR paths are "/"-normalised, so backslashes are folder separators too
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path, folder", [
    ("C:\\Astro\\M31\\masters\\x.fits", "M31"),
    ("C:\\Astro/M31\\masters/x.fits", "M31"),
    ("\\\\server\\share\\M42\\masters\\x.fits", "M42"),
    ("C:/Astro/M31/master/x.fits", "M31"),
])
def test_project_name_from_windows_mixed_and_unc_paths(path: str, folder: str) -> None:
    assert ch.project_name_from_path(path) == folder


def test_project_name_for_takes_a_backslash_path() -> None:
    assert ch.project_name_for({"paths": {"R": "C:\\Astro\\M31\\masters\\r.fits"}}) == "M31"


def test_duplicate_check_unifies_windows_separators_and_case() -> None:
    assert ch.validate_selection({"R": "C:\\a\\x.xisf", "G": "C:/a/X.xisf", "B": "/b.xisf"}) == [
        "Same file selected for R and G: C:/a/X.xisf"]


def test_duplicate_check_keeps_posix_paths_case_sensitive() -> None:
    assert ch.validate_selection({"R": "/a/x", "G": "/a/X", "B": "/b"}) == []


# ---------------------------------------------------------------------------
# Cases that a mutation check showed nothing pinned: one camera token alone, the name skip, directories
# ---------------------------------------------------------------------------


def test_mn34230_alone_is_the_panasonic_sensor() -> None:
    assert ch.spcc_sensor_for("MN34230") == "Panasonic MN34230ALJ"
    assert ch.qe_curve_name_for_camera("MN34230") == "Panasonic MN34230 (ASI1600MM)"


def test_imx492_alone_is_the_294_family() -> None:
    assert ch.spcc_sensor_for("IMX492") == "Sony IMX492/294"
    assert ch.qe_curve_name_for_camera("IMX492") == "Sony IMX492"


def test_calibration_name_is_skipped_even_when_it_would_outrank_the_lights(tmp_path: Path) -> None:
    """A drizzled autocropped dark named with FILTER-Ha beats a plain light; the name skip must keep it out."""
    _write_fits_master(tmp_path, "masterDark_FILTER-Ha_mono_drizzle_2x_autocrop.fits", "H", 2, filter_name="Ha")
    _write_fits_master(tmp_path, "masterFlat_FILTER-Ha_mono_drizzle_2x_autocrop.fits", "H", 2, filter_name="Ha")
    _write_fits_master(tmp_path, "masterLight_FILTER-Ha_mono.fits", "H", 1, filter_name="Ha")
    scan = ch.scan_masters(str(tmp_path))
    assert scan is not None
    assert scan["confirmed"]["H"]["name"] == "masterLight_FILTER-Ha_mono.fits"
    assert scan["total"] == 3


def test_directory_named_like_a_fits_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "weird.fits").mkdir()
    _write_fits_master(tmp_path, "masterLight_FILTER-Ha_mono.fits", "H", 1, filter_name="Ha")
    scan = ch.scan_masters(str(tmp_path))
    assert scan is not None
    assert scan["total"] == 1
    assert list(scan["confirmed"]) == ["H"]
