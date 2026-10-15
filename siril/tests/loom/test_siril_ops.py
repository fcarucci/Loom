"""Siril operations: command texts, quoting, thread and lock rules, pixel flips, the fake, SPCC database helpers.

No Siril here. SirilCommandOps runs against the sirilpy stub (put first on sys.path, as tests/test_siril_script.py
does); RecordingOps needs nothing. The stub's STATE is reset for every test.
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import siril_ops
from loomsiril.loom.siril_ops import (
    RecordingOps,
    SirilCommandOps,
    SirilOpsError,
    command_text,
    quote_path,
    spcc_database_candidates,
    spcc_names,
)

STUB = Path(__file__).resolve().parents[1] / "sirilpy_stub"


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
def siril(stub):
    return stub.SirilInterface()


def write_spcc_db(folder: Path) -> Path:
    """A small SPCC database: one camera and a red, green and blue filter (as Siril's JSON files are laid out)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sensors.json").write_text(
        '[{"name": "Sony IMX571", "type": "MONO_SENSOR"}]', encoding="utf-8")
    (folder / "filters.json").write_text(
        '[{"name": "Baader R", "type": "MONO_FILTER", "channel": "RED"},'
        ' {"name": "Baader G", "type": "MONO_FILTER", "channel": "GREEN"},'
        ' {"name": "Baader B", "type": "MONO_FILTER", "channel": "BLUE"}]', encoding="utf-8")
    return folder


@pytest.fixture
def spcc_db(tmp_path: Path) -> Path:
    return write_spcc_db(tmp_path / "spcc")


@pytest.fixture
def logged(siril, spcc_db):
    lines: list[str] = []
    return SirilCommandOps(siril, log=lines.append, spcc_database=spcc_db), lines


def image(h: int = 6, w: int = 5, seed: int = 3) -> np.ndarray:
    return np.random.default_rng(seed).random((h, w)).astype(np.float32)


# ---- command texts (the measured forms) ---------------------------------------------------------------------


def test_every_command_text_matches_the_measured_form(siril, logged, stub):
    ops, _ = logged
    st = stub.STATE
    ops.cd("/data/My Frames")
    ops.load("/data/a.fits")
    assert ops.save("/data/out.fits") == "/data/out.fits"
    ops.set32bits()
    ops.platesolve()
    ops.platesolve(force=True, focal=1200, pixel_size=3.76)
    ops.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader B")
    ops.spcc_narrowband(656.28, 7.0, 500.7, 25.0, 495.9, 20.0)
    ops.link_sequence("lights")
    ops.setref("lights", 2)
    ops.register("lights")
    ops.crop(10, 20, 300, 200)
    assert ops.rgbcomp("/w/r.fit", "/w/g.fit", "/w/b.fit", "/w/rgb") == "/w/rgb.fit"
    ops.ghs(("autoghs", "-linked", "0", "4.5", "-b=13", "-hp=0.7", "-lp=0"))
    ops.linear_match("/w/ref.fits")
    ops.mtf(0.01, 0.2)
    ops.mtf(0.01, 0.2, "G")
    ops.icc_assign("/w/profile.icc")
    ops.icc_assign("sRGB")
    ops.update_key("loomout", "RGB starless", "Loom pipeline output")
    ops.denoise(0.6)
    ops.subsky()
    ops.pyscript("/w/s.py", ["a b", 3])
    assert list(st.commands) == [
        ("cd \"/data/My Frames\"",),
        ("load \"/data/a.fits\"",),
        ("save \"/data/out.fits\"",),
        ("set32bits",),
        ("platesolve",),
        ("platesolve -force -focal=1200 -pixelsize=3.76",),
        ('spcc "-monosensor=Sony IMX571" "-rfilter=Baader R" "-gfilter=Baader G" "-bfilter=Baader B"',),
        ("spcc -narrowband -rwl=656.28 -rbw=7.00 -gwl=500.70 -gbw=25.00 -bwl=495.90 -bbw=20.00",),
        (f'link lights "-out={st.workdir}"',),
        ("setref lights 2",),
        ("register lights -transf=homography -interp=lanczos4",),
        ("crop 10 20 300 200",),
        ('rgbcomp "/w/r.fit" "/w/g.fit" "/w/b.fit" "-out=/w/rgb"',),
        ("autoghs -linked 0 4.5 -b=13 -hp=0.7 -lp=0",),
        ('linear_match "/w/ref.fits" 0 0.92',),
        ("mtf 0.01 0.2 1",),
        ("mtf 0.01 0.2 1 G",),
        ('icc_assign "/w/profile.icc"',),
        ("icc_assign sRGB",),
        ('update_key LOOMOUT "RGB starless" "Loom pipeline output"',),
        ("denoise -mod=0.6",),
        ("subsky 2 -samples=20 -tolerance=1.0",),
        ('pyscript "/w/s.py" "a b" 3',),
    ]


def test_find_stars_sends_the_two_commands_then_reads_stars(siril, logged, stub):
    ops, _ = logged
    stub.STATE.loaded = True
    stub.STATE.stars = [stub.PSFStar(A=0.5, xpos=10.5, ypos=3.5, fwhmx=3.1, fwhmy=2.9, rmse=0.01, mag=12.0,
                                     beta=3.0, angle=-4.0),
                        stub.PSFStar(A=0.2, xpos=40.5, ypos=9.5, fwhmx=3.0, fwhmy=3.0)]
    stars = ops.find_stars()
    assert [c[0] for c in stub.STATE.commands] == ["setfindstar reset -gaussian", "findstar",
                                                   "setfindstar -focal=0.0 -pixelsize=0.0"]
    assert stars[0] == {"xpos": 10.5, "ypos": 3.5, "fwhmx": 3.1, "fwhmy": 2.9, "amplitude": 0.5, "A": 0.5,
                        "mag": 12.0, "rmse": 0.01, "beta": 3.0, "angle": -4.0}
    assert stars[1]["mag"] == 0.0 and stars[1]["xpos"] == 40.5
    stub.STATE.stars = None
    assert ops.find_stars() == []


def test_analyse_is_no_command_and_returns_the_fields(siril, logged, stub):
    ops, _ = logged
    fields = {"fwhm": 3.0, "roundness": 0.9, "bgnoise": 0.001, "nbstars": 120, "channels": 1, "filter": "L",
              "width": 128, "height": 96, "wfwhm": 3.1, "imagetype": "Light", "timestamp": "2026-10-13"}
    stub.STATE.analysis = fields
    assert ops.analyse("/w/a.fits") == fields
    assert stub.STATE.analysed == ["/w/a.fits"]
    assert stub.STATE.commands == []


def test_icc_profile_header_is_cli_and_logs(siril, logged, stub):
    ops, lines = logged
    stub.STATE.loaded = True
    stub.STATE.pixeldata = np.zeros((2, 2), dtype=np.float32)
    stub.STATE.icc = b"profile-bytes"
    stub.STATE.header = {"CRVAL1": 83.8}
    assert ops.icc_profile() == b"profile-bytes"
    assert "CRVAL1" in ops.header()
    assert ops.is_cli() is True
    ops.log("hello")
    ops.progress("working", 0.25)
    ops.set32bits()
    assert stub.STATE.logs == [("hello", 0)]
    assert stub.STATE.progress == [("working", 0.25)]
    assert re.fullmatch(r"siril: set32bits \(\d+\.\d{3} s\)", lines[-1])


def test_log_callback_gets_text_and_duration_for_each_command(logged):
    ops, lines = logged
    ops.set32bits()
    ops.crop(1, 2, 3, 4)
    assert [re.fullmatch(r"siril: (.*) \((\d+\.\d{3}) s\)", ln).group(1) for ln in lines] == [
        "set32bits", "crop 1 2 3 4"]


# ---- quoting and refusals ------------------------------------------------------------------------------------


@pytest.mark.parametrize("call", [
    lambda o: o.cd('/x/"bad"'),
    lambda o: o.load('/x/"a".fits'),
    lambda o: o.save('/x/"a".fits'),
    lambda o: o.icc_assign('/x/"p".icc'),
    lambda o: o.analyse('/x/"a".fits'),
    lambda o: o.rgbcomp("/x/r.fit", "/x/g.fit", "/x/b.fit", '/x/"out"'),
    lambda o: o.linear_match('/x/"ref".fits'),
    lambda o: o.update_key("LOOMOUT", 'say "hi"'),
    lambda o: o.spcc_broadband('Sony "X"', "R", "G", "B"),
])
def test_a_double_quote_is_refused_before_anything_is_sent(siril, logged, stub, call):
    ops, _ = logged
    with pytest.raises(ValueError):
        call(ops)
    assert stub.STATE.commands == [] and stub.STATE.analysed == []


def test_paths_with_spaces_are_quoted(logged, stub):
    ops, _ = logged
    ops.load("/data/night one/light 1.fits")
    assert stub.STATE.commands[-1] == ('load "/data/night one/light 1.fits"',)
    assert quote_path("/a b/c") == '"/a b/c"'


def test_save_needs_a_fits_suffix(logged, stub):
    ops, _ = logged
    with pytest.raises(ValueError):
        ops.save("/data/out")
    assert stub.STATE.commands == []


def test_one_line_only(logged, stub):
    ops, _ = logged
    with pytest.raises(ValueError):
        ops.cd("/data/a\nset32bits")
    assert stub.STATE.commands == []


def test_narrowband_never_takes_filter_names(logged, stub):
    ops, _ = logged
    ops.spcc_narrowband(656.28, 7, 500.7, 25, 495.9, 20)
    assert "monosensor" not in stub.STATE.commands[-1][0] and "filter" not in stub.STATE.commands[-1][0]


def test_save_needs_a_lower_case_fits_suffix(logged, stub):
    ops, _ = logged
    with pytest.raises(ValueError):
        ops.save("/data/up.FITS")
    assert stub.STATE.commands == []


def test_spcc_refuses_a_missing_database_before_sending(siril, stub, tmp_path):
    ops = SirilCommandOps(siril, spcc_database=tmp_path / "absent")
    with pytest.raises(SirilOpsError) as info:
        ops.spcc_narrowband(656.28, 7, 500.7, 25, 495.9, 20)
    assert "not installed" in str(info.value.original)
    with pytest.raises(SirilOpsError):
        ops.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader B")
    assert stub.STATE.commands == []


def test_spcc_refuses_names_that_are_not_in_the_database(logged, stub):
    ops, _ = logged
    with pytest.raises(SirilOpsError) as info:
        ops.spcc_broadband("Nonexistent Camera", "Baader R", "Baader G", "Baader B")
    assert info.value.command.startswith("spcc ") and "sensor 'Nonexistent Camera'" in str(info.value.original)
    with pytest.raises(SirilOpsError) as info:
        ops.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader R2")
    assert "blue filter 'Baader R2'" in str(info.value.original)
    assert stub.STATE.commands == []


def test_spcc_narrowband_sends_once_the_database_exists(logged, stub):
    ops, _ = logged
    ops.spcc_narrowband(656.28, 7, 500.7, 25, 495.9, 20)
    assert stub.STATE.commands[-1][0].startswith("spcc -narrowband -rwl=656.28")


def test_a_command_inside_the_image_lock_is_refused(siril, logged, stub, monkeypatch):
    ops, _ = logged
    monkeypatch.setattr(siril, "set_image_pixeldata", lambda arr: ops.set32bits())
    with pytest.raises(RuntimeError, match="image lock"):
        ops.set_pixels([image()])
    assert stub.STATE.commands == []


def test_recording_load_of_a_missing_file_raises_sirilops_error(tmp_path):
    rec = RecordingOps(tmp_path)
    with pytest.raises(SirilOpsError) as info:
        rec.load("missing.fits")
    assert info.value.command.startswith("load ") and "missing.fits" in info.value.command


def test_recording_update_key_changes_the_header_and_the_saved_file(tmp_path):
    write_fits(tmp_path / "a.fits", [image()], {"FILTER": "L"}, bitpix=-32)
    rec = RecordingOps(tmp_path)
    rec.load("a.fits")
    rec.update_key("loomout", "RGB starless", "Loom pipeline output")
    assert "LOOMOUT" in rec.header()
    rec.save(str(tmp_path / "b.fits"))
    assert read_fits(tmp_path / "b.fits")[1]["LOOMOUT"] == "RGB starless"


def test_ghs_takes_only_autoghs_tuples(logged, stub):
    ops, _ = logged
    with pytest.raises(ValueError):
        ops.ghs(("ghs", "-linked", "0"))
    assert stub.STATE.commands == []


def test_mtf_channel_letters_and_indexes(logged, stub):
    ops, _ = logged
    ops.mtf(0.1, 0.25, 2)
    ops.mtf(0.1, 0.25, "r")
    with pytest.raises(ValueError):
        ops.mtf(0.1, 0.25, "X")
    assert [c[0] for c in stub.STATE.commands] == ["mtf 0.1 0.25 1 B", "mtf 0.1 0.25 1 R"]


def test_setref_is_one_based(logged, stub):
    ops, _ = logged
    with pytest.raises(ValueError):
        ops.setref("lights", 0)
    ops.setref("lights", 1)
    assert stub.STATE.commands[-1] == ("setref lights 1",)


def test_update_key_uses_the_upper_case_keyword(logged, stub):
    ops, _ = logged
    ops.update_key("loomout", "x")
    assert stub.STATE.commands[-1] == ('update_key LOOMOUT "x"',)


def test_command_text_joins_parts_and_formats_numbers():
    assert command_text("mtf", 0.01, 0.2, 1, "G") == "mtf 0.01 0.2 1 G"
    assert command_text("subsky", 2, "-tolerance=1.0") == "subsky 2 -tolerance=1.0"


# ---- thread and lock rules -----------------------------------------------------------------------------------


def test_calls_from_another_thread_are_refused(siril, logged, stub):
    ops, _ = logged
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            ops.set32bits()
        except BaseException as exc:  # noqa: BLE001 - recorded and asserted below
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == "Siril commands must come from the pipeline thread"
    assert stub.STATE.commands == []


def test_no_command_is_sent_inside_the_image_lock(logged, stub):
    ops, _ = logged
    stub.STATE.loaded = True
    stub.STATE.pixeldata = np.zeros((4, 3), dtype=np.float32)
    ops.set_pixels([image(4, 3)])
    ops.crop(0, 0, 2, 2)
    ops.mtf(0.0, 0.5)
    assert stub.STATE.locks == 1
    assert stub.STATE.lock_depth == 0
    assert stub.STATE.cmds_under_lock == []


# ---- pixels ----------------------------------------------------------------------------------------------------


def test_pixels_flip_bottom_up_to_top_down(logged, stub):
    ops, _ = logged
    bottom_up = np.arange(12, dtype=np.float32).reshape(3, 4)
    stub.STATE.loaded = True
    stub.STATE.pixeldata = bottom_up
    (plane,) = ops.pixels()
    assert plane.dtype == np.float32 and plane.shape == (3, 4)
    assert np.array_equal(plane, bottom_up[::-1])


def test_set_pixels_flips_back_and_keeps_colour_shape(logged, stub):
    ops, _ = logged
    stub.STATE.loaded = True
    stub.STATE.pixeldata = np.zeros((3, 2, 4), dtype=np.float32)
    top = [image(2, 4, seed=s) for s in (1, 2, 3)]
    ops.set_pixels(top)
    stored = stub.STATE.pixeldata
    assert stored.shape == (3, 2, 4) and stored.dtype == np.float32
    for c in range(3):
        assert np.array_equal(stored[c], top[c][::-1])
    back = ops.pixels()
    assert len(back) == 3 and all(np.array_equal(back[c], top[c]) for c in range(3))


def test_set_pixels_mono_is_two_dimensional(logged, stub):
    ops, _ = logged
    stub.STATE.loaded = True
    stub.STATE.pixeldata = np.zeros((2, 3), dtype=np.float32)
    top = image(2, 3)
    ops.set_pixels([top])
    assert stub.STATE.pixeldata.shape == (2, 3)
    assert np.array_equal(stub.STATE.pixeldata, top[::-1])


def test_set_pixels_rejects_wrong_plane_count(logged, stub):
    ops, _ = logged
    with pytest.raises(ValueError):
        ops.set_pixels([image(), image()])


# ---- SirilOpsError ---------------------------------------------------------------------------------------------


def test_a_failing_command_raises_sirilops_error_with_the_text(siril, logged, stub, monkeypatch):
    ops, lines = logged
    original = stub.CommandError("Argument error")

    def boom(*args):
        raise original

    monkeypatch.setattr(siril, "cmd", boom)
    with pytest.raises(SirilOpsError) as info:
        ops.register("lights")
    err = info.value
    assert isinstance(err, RuntimeError)
    assert err.command == "register lights -transf=homography -interp=lanczos4"
    assert err.original is original
    assert re.fullmatch(r"siril: register .* \(\d+\.\d{3} s\)", lines[-1])


def test_a_command_reported_as_failed_raises_too(siril, logged, monkeypatch):
    ops, _ = logged
    monkeypatch.setattr(siril, "cmd", lambda *a: False)
    with pytest.raises(SirilOpsError) as info:
        ops.set32bits()
    assert info.value.command == "set32bits"


# ---- RecordingOps ----------------------------------------------------------------------------------------------


def test_transcript_and_calls_record_the_sequence(tmp_path):
    rec = RecordingOps(tmp_path)
    rec.set32bits()
    rec.denoise(0.6)
    rec.register("lights")
    assert rec.transcript == ["set32bits", "denoise -mod=0.6", "register lights -transf=homography -interp=lanczos4"]
    assert rec.calls[1] == ("denoise", (0.6,), {})
    assert rec.calls[0] == ("set32bits", (), {})


def test_recording_link_uses_the_working_directory(tmp_path):
    rec = RecordingOps(tmp_path)
    rec.link_sequence("lights")
    assert rec.transcript[-1] == f'link lights "-out={tmp_path}"'


def test_load_and_save_round_trip_on_tmp_path(tmp_path):
    src = tmp_path / "a.fits"
    planes = [image(6, 5, seed=1)]
    write_fits(src, planes, {"CRVAL1": 83.8, "FILTER": "L"}, bitpix=-32)
    rec = RecordingOps(tmp_path)
    rec.load(str(src))
    assert rec.pixels()[0].shape == (6, 5)
    assert np.array_equal(rec.pixels()[0], planes[0])
    out = rec.save(str(tmp_path / "out.fits"))
    assert out == str(tmp_path / "out.fits")
    back, header = read_fits(out)
    assert np.array_equal(back[0], planes[0])
    assert header["FILTER"] == "L"
    assert rec.transcript[-1] == f'save "{tmp_path / "out.fits"}"'


def test_pixels_without_a_loaded_image_fail(tmp_path):
    rec = RecordingOps(tmp_path)
    with pytest.raises(SirilOpsError):
        rec.pixels()
    with pytest.raises(SirilOpsError):
        rec.save(str(tmp_path / "x.fits"))


def test_relative_paths_resolve_against_the_working_directory(tmp_path):
    sub = tmp_path / "lights dir"
    sub.mkdir()
    write_fits(sub / "a.fits", [image()], {}, bitpix=-32)
    rec = RecordingOps(tmp_path)
    rec.cd("lights dir")
    assert rec.workdir == str(sub)
    rec.load("a.fits")
    assert rec.pixels()[0].shape == (6, 5)


def test_crop_matches_the_top_down_slice(tmp_path):
    src = tmp_path / "a.fits"
    planes = [image(12, 10, seed=4)]
    write_fits(src, planes, {"CRPIX1": 5.0, "CRPIX2": 6.0}, bitpix=-32)
    rec = RecordingOps(tmp_path)
    rec.load(str(src))
    rec.crop(2, 3, 4, 5)
    (cropped,) = rec.pixels()
    assert cropped.shape == (5, 4)
    assert np.array_equal(cropped, planes[0][3:8, 2:6])
    assert rec.transcript[-1] == "crop 2 3 4 5"
    with pytest.raises(SirilOpsError):
        rec.crop(8, 0, 4, 2)


def test_mtf_applies_the_formula_per_channel(tmp_path):
    x = image(4, 4, seed=7)
    rec = RecordingOps(tmp_path)
    rec._planes = [x.copy(), x.copy(), x.copy()]
    rec._header = {}
    c0, m = 0.02, 0.3
    rec.mtf(c0, m, "G")
    p = rec.pixels()
    assert np.array_equal(p[0], x) and np.array_equal(p[2], x)
    y = np.clip((x.astype(np.float64) - c0) / (1 - c0), 0, 1)
    expect = (m - 1) * y / ((2 * m - 1) * y - m)
    assert np.max(np.abs(p[1].astype(np.float64) - expect)) < 2e-6
    rec.mtf(c0, m)
    assert np.max(np.abs(rec.pixels()[0].astype(np.float64) - expect)) < 2e-6


def test_handlers_replace_the_default_and_receive_the_parameters(tmp_path):
    seen = []

    def denoise(ops, mod):
        seen.append((ops, mod))
        return "denoised"

    rec = RecordingOps(tmp_path, handlers={"denoise": denoise})
    assert rec.denoise(0.6) == "denoised"
    assert seen and seen[0][0] is rec and seen[0][1] == 0.6
    assert rec.transcript[-1] == "denoise -mod=0.6"


def test_fail_raises_sirilops_error_after_recording(tmp_path):
    rec = RecordingOps(tmp_path, fail={"load": OSError("gone")})
    with pytest.raises(SirilOpsError) as info:
        rec.load("/x/a.fits")
    assert info.value.command == 'load "/x/a.fits"'
    assert isinstance(info.value.original, OSError)
    assert rec.transcript == ['load "/x/a.fits"']


def test_recording_stars_analyse_icc_and_header(tmp_path):
    stars = [{"xpos": 1.5, "ypos": 2.5, "fwhmx": 3.0, "fwhmy": 3.0, "A": 0.4}]
    rec = RecordingOps(tmp_path, stars=stars, analyse=lambda p: {"fwhm": 3.0, "path": p}, icc=b"icc")
    got = rec.find_stars()
    assert got[0]["xpos"] == 1.5 and got[0]["amplitude"] == 0.4 and got[0]["mag"] is None
    assert rec.transcript[-3:] == ["setfindstar reset -gaussian", "findstar", "setfindstar -focal=0.0 -pixelsize=0.0"]
    assert rec.analyse("/x/a.fits") == {"fwhm": 3.0, "path": "/x/a.fits"}
    assert rec.icc_profile() == b"icc"
    assert rec.is_cli() is True
    rec.log("note")
    rec.progress("step", 0.5)
    assert rec.logs == ["note"] and rec.progress_events == [("step", 0.5)]


def test_recording_and_real_ops_send_the_same_texts(siril, logged, stub, tmp_path):
    real, _ = logged
    rec = RecordingOps(stub.STATE.workdir)

    def script(ops):
        ops.set32bits()
        ops.link_sequence("lights")
        ops.setref("lights", 1)
        ops.register("lights")
        ops.spcc_broadband("Sony IMX571", "Baader R", "Baader G", "Baader B")
        ops.icc_assign("sRGB")
        ops.pyscript("/w/s.py", [])

    script(real)
    script(rec)
    assert [c[0] for c in stub.STATE.commands] == rec.transcript


# ---- SPCC database ---------------------------------------------------------------------------------------------


def _write_db(folder: Path) -> None:
    folder.mkdir(parents=True)
    (folder / "a.json").write_text(
        '[{"name": "Sony IMX571", "type": "MONO_SENSOR"},'
        ' {"name": "Baader R", "type": "MONO_FILTER", "channel": "RED"},'
        ' {"name": "Baader G", "type": "MONO_FILTER", "channel": "GREEN"},'
        ' {"name": "Sony IMX571", "type": "MONO_SENSOR"}]', encoding="utf-8")
    (folder / "b.json").write_text(
        '[{"name": "ZWO ASI2600MM", "type": "MONO_SENSOR"},'
        ' {"name": "Chroma R", "type": "MONO_FILTER", "channel": "RED"},'
        ' {"name": "Chroma B", "type": "MONO_FILTER", "channel": "BLUE"},'
        ' {"name": "Ignored", "type": "COLOR_SENSOR"}]', encoding="utf-8")
    (folder / "notes.txt").write_text("not json", encoding="utf-8")


def test_spcc_names_by_kind_sorted_and_unique(tmp_path):
    db = tmp_path / "db"
    _write_db(db)
    assert spcc_names("monosensor", str(db)) == ["Sony IMX571", "ZWO ASI2600MM"]
    assert spcc_names("redfilter", str(db)) == ["Baader R", "Chroma R"]
    assert spcc_names("greenfilter", str(db)) == ["Baader G"]
    assert spcc_names("bluefilter", str(db)) == ["Chroma B"]


def test_spcc_names_missing_folder_is_empty(tmp_path):
    assert spcc_names("monosensor", str(tmp_path / "absent")) == []


def test_spcc_names_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        spcc_names("colorsensor", str(tmp_path))


def test_spcc_database_candidates_per_platform(tmp_path):
    assert spcc_database_candidates("linux", {}, "/home/u") == ["/home/u/.local/share/siril-spcc-database"]
    assert spcc_database_candidates("linux", {"XDG_DATA_HOME": "/data/x"}, "/home/u") == [
        "/data/x/siril-spcc-database"]
    assert spcc_database_candidates("linux", {"XDG_DATA_HOME": "rel"}, "/home/u") == [
        "/home/u/.local/share/siril-spcc-database"]
    assert spcc_database_candidates("darwin", {}, "/Users/u") == [
        "/Users/u/Library/Application Support/siril-spcc-database"]
    assert spcc_database_candidates("win32", {"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"}, "C:\\Users\\u") == [
        "C:\\Users\\u\\AppData\\Local\\siril-spcc-database"]
    assert spcc_database_candidates("freebsd", {}, "/home/u") == []


def test_module_has_no_sirilpy_import_at_load():
    source = Path(siril_ops.__file__).read_text(encoding="utf-8")
    top_level = [ln for ln in source.splitlines() if ln.startswith(("import ", "from "))]
    assert not any("sirilpy" in ln or "tkinter" in ln for ln in top_level)


def test_spcc_names_walks_siril_s_real_layout(tmp_path):
    """The database is a tree: MONO entries sit in sub-folders, and a schema file and OSC filter files sit at the top
    (one of them is not JSON). Only the MONO_SENSOR and MONO_FILTER entries count, and no bad file raises."""
    db = tmp_path / "siril-spcc-database"
    (db / "mono_sensors").mkdir(parents=True)
    (db / "mono_filters").mkdir()
    (db / "osc_sensors").mkdir()
    (db / "wb_refs").mkdir()
    (db / "utils").mkdir()
    (db / "mono_sensors" / "cameras.json").write_text(
        '[{"name": "Sony IMX571", "type": "MONO_SENSOR"}, {"name": "ZWO ASI2600MM", "type": "MONO_SENSOR"}]',
        encoding="utf-8")
    (db / "mono_filters" / "baader.json").write_text(
        '[{"name": "Baader R", "type": "MONO_FILTER", "channel": "RED"},'
        ' {"name": "Baader G", "type": "MONO_FILTER", "channel": "GREEN"},'
        ' {"name": "Baader B", "type": "MONO_FILTER", "channel": "BLUE"}]', encoding="utf-8")
    (db / "osc_sensors" / "colour.json").write_text(
        '[{"name": "Colour camera", "type": "OSC_SENSOR"}, "not an object", 7]', encoding="utf-8")
    (db / "wb_refs" / "refs.json").write_text('[{"name": "G2V", "type": "WB_REF"}]', encoding="utf-8")
    (db / "utils" / "odd.json").write_text('{"not": "an array of entries"}', encoding="utf-8")
    # top level: the schema (a JSON object, as in the real database), an OSC filter file, and a broken file
    (db / "spcc-database-schema.json").write_text('{"$schema": "x", "type": "object", "properties": {}}',
                                                  encoding="utf-8")
    (db / "OSC_filters.json").write_text(
        '[{"name": "Optolong L-Pro", "type": "OSC_FILTER"}]', encoding="utf-8")
    (db / "broken.json").write_text("[{\"name\": ", encoding="utf-8")

    assert spcc_names("monosensor", str(db)) == ["Sony IMX571", "ZWO ASI2600MM"]
    assert spcc_names("redfilter", str(db)) == ["Baader R"]
    assert spcc_names("greenfilter", str(db)) == ["Baader G"]
    assert spcc_names("bluefilter", str(db)) == ["Baader B"]


def test_siril_data_dir_is_the_parent_of_siril_user_data_folder():
    from loomsiril.loom.siril_ops import siril_data_dir

    class S:
        def __init__(self, answer):
            self.answer = answer

        def get_siril_userdatadir(self):
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

    mac = "/Users/u/Library/Application Support/org.siril.Siril/siril"
    assert siril_data_dir(S(mac)) == "/Users/u/Library/Application Support/org.siril.Siril"
    assert siril_data_dir(S(mac + "/")) == "/Users/u/Library/Application Support/org.siril.Siril"
    assert siril_data_dir(S(RuntimeError("no connection"))) is None
    assert siril_data_dir(S("")) is None


def test_spcc_candidates_put_siril_own_data_folder_first():
    base = "/Users/u/Library/Application Support/org.siril.Siril"
    got = spcc_database_candidates("darwin", {}, "/Users/u", base)
    assert got[0] == base + "/siril-spcc-database"
    assert "/Users/u/Library/Application Support/siril-spcc-database" in got[1:]
