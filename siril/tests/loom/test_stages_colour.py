"""Colour stages against the recording fake of ``SirilOps`` (no Siril, no network).

The transcripts pin the exact commands sent: the broadband SPCC names, the narrowband wavelengths of each palette and the
four names that go with a narrowband SPCC once RGB is supplied. The clean white balance is checked with a simulated SPCC
that applies k*x+b per channel, so the recovered factors must be k. Failures keep the plate and skip the cache.
"""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import cache, channels, imageops, nbnorm, stages_colour
from loomsiril.loom.runctx import RunContext
from loomsiril.loom.siril_ops import RecordingOps
from loomsiril.loom.stagekit import StageFailed

SHAPE = (48, 64)
FILTERS = {"R": "Baader Red", "G": "Baader Green", "B": "Baader Blue"}
INSTRUME = "ZWO ASI294MC"
SOLVED = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 83.8, "CRVAL2": -5.4}
PALETTE_SHAPES = {
    "SHO": ("671.60", "656.28", "500.70"),
    "HOO": ("656.28", "500.70", "500.70"),
    "HSO": ("656.28", "671.60", "500.70"),
}


class Log:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.warnings: list[str] = []


def _plane(seed: int, lo: float = 0.05, hi: float = 0.4) -> np.ndarray:
    return np.random.default_rng(seed).uniform(lo, hi, SHAPE).astype(np.float32)


def _write(path: Path, planes: list[np.ndarray], header: dict[str, Any]) -> str:
    write_fits(path, planes, header, bitpix=-32)
    return str(path)


def _channel_header(filter_name: str, exptime: float, **extra: Any) -> dict[str, Any]:
    header = {"OBJECT": "M42", "FILTER": filter_name, "EXPTIME": exptime, "INSTRUME": INSTRUME}
    header.update(extra)
    return header


def _composite_handler(ops: RecordingOps, r: Any, g: Any, b: Any, out: Any) -> str:
    """rgbcomp: stacks the three plates (R, G, B) and keeps the first plate's header, as Siril does."""
    planes = [read_fits(os.fspath(p))[0][0] for p in (r, g, b)]
    header = read_fits(os.fspath(r))[1]
    path = os.fspath(out) + ".fit"
    write_fits(path, planes, header, bitpix=-32)
    return path


def _register_handler(ops: RecordingOps, name: str) -> None:
    """register: every numbered plate of the sequence folder comes back unchanged, as r_<name>_NNNNN.fit."""
    for path in sorted(glob.glob(os.path.join(ops.workdir, "[0-9][0-9]_*.fits"))):
        index = int(os.path.basename(path)[:2])
        shutil.copyfile(path, os.path.join(ops.workdir, f"r_{name}_{index:05d}.fit"))


def _affine_spcc(gains: tuple[float, float, float], offsets: tuple[float, float, float]):
    """A simulated broadband SPCC: each channel becomes k*x + b, the relation the white-balance recovery assumes."""

    def handler(ops: RecordingOps, sensor: str, r: str, g: str, b: str) -> None:
        planes = ops.pixels()
        ops.set_pixels([k * p.astype(np.float64) + o for k, o, p in zip(gains, offsets, planes)])

    return handler


def _ctx(tmp_path: Path, handlers: dict | None = None, fail: dict | None = None) -> tuple[RunContext, RecordingOps, Log]:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    ops = RecordingOps(work, handlers=handlers, fail=fail)
    log = Log()
    ctx = RunContext(ops=ops, work_dir=str(work), log=log.lines.append, warn=log.warnings.append)
    return ctx, ops, log


def _rgb_plates(tmp_path: Path, headers: list[dict[str, Any]] | None = None) -> dict[str, str]:
    headers = headers or [_channel_header("R", 60.0), _channel_header("G", 60.0), _channel_header("B", 120.0)]
    out = {}
    for ch, seed, header in zip(("R", "G", "B"), (1, 2, 3), headers):
        out[ch] = _write(tmp_path / f"{ch}_in.fits", [_plane(seed)], header)
    return out


def _palette_plates(tmp_path: Path, channels_: tuple[str, ...] = ("H", "S", "O")) -> dict[str, str]:
    out = {}
    for seed, ch in enumerate(channels_, start=10):
        out[ch] = _write(tmp_path / f"{ch}_pal.fits", [_plane(seed)], dict(SOLVED, FILTER=ch))
    return out


def _config(**extra: Any) -> dict[str, Any]:
    config: dict[str, Any] = {"filters": dict(FILTERS), "sharpenTool": "bxt", "narrowbandBandwidth": 3.0,
                              "narrowbandNormalize": False}
    config.update(extra)
    return config


# ---- parameters (cache keys) ------------------------------------------------------------------------------------------


def test_rgb_params_without_factors_have_loom_shape() -> None:
    params = stages_colour.rgb_stage_params(_config(), INSTRUME, None)
    assert list(params) == ["combine", "solveRGB", "spccRGB"]
    assert params == {
        "combine": {},
        "solveRGB": {},
        "spccRGB": {"filters": FILTERS, "instrume": INSTRUME, "whiteBalance": "direct"},
    }


def test_rgb_params_with_factors_carry_the_gains_in_order() -> None:
    params = stages_colour.rgb_stage_params(_config(), INSTRUME, (1.25, 1.0, 0.8))
    assert params["spccRGB"] == {"filters": FILTERS, "instrume": INSTRUME, "whiteBalance": [1.25, 1.0, 0.8]}


def test_palette_params_without_normalisation_have_loom_shape() -> None:
    params = stages_colour.palette_stage_params("SHO", _config(narrowbandBandwidth=2.5))
    assert params == {"paletteCombine": {}, "paletteSpcc": {"palette": "SHO", "bandwidth": 2.5}}


def test_palette_params_with_normalisation_add_the_norm_stage_last() -> None:
    params = stages_colour.palette_stage_params("HOO", _config(narrowbandNormalize=True))
    assert list(params) == ["paletteCombine", "paletteSpcc", "paletteNorm"]
    assert params["paletteNorm"] == nbnorm.stage_params("HOO")
    assert params["paletteSpcc"] == {"palette": "HOO", "bandwidth": 3.0}


# ---- RGB composite -----------------------------------------------------------------------------------------------------


def test_combine_removes_only_the_keywords_that_differ(tmp_path: Path) -> None:
    ctx, ops, _ = _ctx(tmp_path, handlers={"rgbcomp": _composite_handler, "register": _register_handler})
    plates = _rgb_plates(tmp_path)
    out = stages_colour.rgb_runners(ctx, plates, _config(), INSTRUME, None)["combine"](None, str(tmp_path / "RGB.fits"))

    planes, header = read_fits(out.path)
    assert len(planes) == 3
    for ch, plane in zip(("R", "G", "B"), planes):
        np.testing.assert_array_equal(plane, read_fits(plates[ch])[0][0])
    assert "FILTER" not in header and "EXPTIME" not in header
    assert header["OBJECT"] == "M42" and header["INSTRUME"] == INSTRUME
    assert ops.transcript[0].startswith("rgbcomp ")


def test_spcc_rgb_direct_sends_the_quoted_broadband_names(tmp_path: Path) -> None:
    ctx, ops, _ = _ctx(tmp_path)
    src = _write(tmp_path / "RGB_solved.fits", [_plane(4) for _ in range(3)], SOLVED)
    stages_colour.rgb_runners(ctx, {"R": src, "G": src, "B": src}, _config(), INSTRUME, None)[
        "spccRGB"](src, str(tmp_path / "RGB_spcc.fits"))

    sensor = channels.spcc_sensor_for(INSTRUME)
    assert ops.transcript == [
        f'load "{src}"',
        f'spcc "-monosensor={sensor}" "-rfilter=Baader Red" "-gfilter=Baader Green" "-bfilter=Baader Blue"',
        f'save "{tmp_path / "RGB_spcc.fits"}"',
    ]


def test_spcc_rgb_honours_an_explicit_sensor(tmp_path: Path) -> None:
    ctx, ops, _ = _ctx(tmp_path)
    src = _write(tmp_path / "RGB_solved.fits", [_plane(4) for _ in range(3)], SOLVED)
    stages_colour.rgb_runners(ctx, {"R": src, "G": src, "B": src}, _config(spccSensor="Custom Sensor"), INSTRUME,
                              None)["spccRGB"](src, str(tmp_path / "RGB_spcc.fits"))
    assert '"-monosensor=Custom Sensor"' in ops.transcript[1]


def test_spcc_rgb_without_a_filter_for_a_channel_stops_the_stage(tmp_path: Path) -> None:
    ctx, _, _ = _ctx(tmp_path)
    src = _write(tmp_path / "RGB_solved.fits", [_plane(4) for _ in range(3)], SOLVED)
    config = _config(filters={"R": "Baader Red", "B": "Baader Blue"})
    with pytest.raises(StageFailed, match="No filter curve for the G channel"):
        stages_colour.rgb_runners(ctx, {"R": src, "G": src, "B": src}, config, INSTRUME, None)["spccRGB"](
            src, str(tmp_path / "RGB_spcc.fits"))


def test_spcc_rgb_with_clean_factors_applies_them_in_numpy(tmp_path: Path) -> None:
    ctx, ops, _ = _ctx(tmp_path)
    planes = [_plane(seed) for seed in (5, 6, 7)]
    src = _write(tmp_path / "RGB_solved.fits", planes, SOLVED)
    factors = [1.25, 1.0, 0.8]
    out = stages_colour.rgb_runners(ctx, {"R": src, "G": src, "B": src}, _config(), INSTRUME, factors)[
        "spccRGB"](src, str(tmp_path / "RGB_wb.fits"))

    expected = imageops.apply_white_balance(read_fits(src)[0], factors)
    got = read_fits(out.path)[0]
    for e, g in zip(expected, got):
        np.testing.assert_array_equal(g, e)
    assert ops.transcript == []  # no Siril command at all on the factor path


def test_solve_is_skipped_when_the_composite_already_has_a_solution(tmp_path: Path) -> None:
    ctx, ops, _ = _ctx(tmp_path)
    src = _write(tmp_path / "RGB.fits", [_plane(8) for _ in range(3)], SOLVED)
    stages_colour.rgb_runners(ctx, {"R": src, "G": src, "B": src}, _config(), INSTRUME, None)["solveRGB"](
        src, str(tmp_path / "RGB_solved.fits"))
    assert not any(t.startswith("platesolve") for t in ops.transcript)


def test_solve_runs_when_the_composite_has_no_solution(tmp_path: Path) -> None:
    ctx, ops, _ = _ctx(tmp_path)
    src = _write(tmp_path / "RGB.fits", [_plane(8) for _ in range(3)], {"OBJECT": "M42"})
    stages_colour.rgb_runners(ctx, {"R": src, "G": src, "B": src}, _config(), INSTRUME, None)["solveRGB"](
        src, str(tmp_path / "RGB_solved.fits"))
    assert any(t.startswith("platesolve") for t in ops.transcript)


# ---- palettes ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("pal", ["SHO", "HOO", "HSO"])
def test_palette_combine_maps_channels_to_r_g_b(tmp_path: Path, pal: str) -> None:
    ctx, _, _ = _ctx(tmp_path, handlers={"rgbcomp": _composite_handler, "register": _register_handler})
    plates = _palette_plates(tmp_path)
    out = stages_colour.palette_runners(ctx, pal, plates, _config(), None)["paletteCombine"](
        None, str(tmp_path / f"{pal}.fits"))
    planes = read_fits(out.path)[0]
    mapping = channels.PALETTES[pal]
    for plane, src in zip(planes, mapping):
        np.testing.assert_array_equal(plane, read_fits(plates[src])[0][0])


@pytest.mark.parametrize("pal", ["SHO", "HOO", "HSO"])
def test_palette_spcc_uses_each_palette_wavelengths_without_names(tmp_path: Path, pal: str) -> None:
    ctx, ops, log = _ctx(tmp_path)
    plate = _write(tmp_path / f"{pal}_in.fits", [_plane(9) for _ in range(3)], SOLVED)
    out = stages_colour.palette_runners(ctx, pal, {"H": plate, "S": plate, "O": plate}, _config(), None)[
        "paletteSpcc"](plate, str(tmp_path / f"{pal}_spcc.fits"))
    rwl, gwl, bwl = PALETTE_SHAPES[pal]
    assert ops.transcript[1] == f"spcc -narrowband -rwl={rwl} -rbw=3.00 -gwl={gwl} -gbw=3.00 -bwl={bwl} -bbw=3.00"
    assert out.skip_cache is False and log.warnings == []


def test_palette_spcc_with_rgb_names_passes_all_four(tmp_path: Path) -> None:
    ctx, ops, _ = _ctx(tmp_path)
    plate = _write(tmp_path / "SHO_in.fits", [_plane(9) for _ in range(3)], SOLVED)
    names = ("Generic mono sensor", "Baader Red", "Baader Green", "Baader Blue")
    stages_colour.palette_runners(ctx, "SHO", {"H": plate, "S": plate, "O": plate}, _config(narrowbandBandwidth=2.5),
                                  names)["paletteSpcc"](plate, str(tmp_path / "SHO_spcc.fits"))
    assert ops.transcript[1] == (
        'spcc "-monosensor=Generic mono sensor" "-rfilter=Baader Red" "-gfilter=Baader Green" '
        '"-bfilter=Baader Blue" -narrowband -rwl=671.60 -rbw=2.50 -gwl=656.28 -gbw=2.50 -bwl=500.70 -bbw=2.50'
    )


def test_palette_spcc_borrows_the_solution_of_a_solved_plate_instead_of_solving(tmp_path: Path) -> None:
    ctx, ops, log = _ctx(tmp_path)
    solved = _write(tmp_path / "L.fits", [_plane(1)], {**SOLVED, "CRPIX1": 32.5, "CD1_1": -1e-4, "CD2_2": 1e-4})
    bare = _write(tmp_path / "H.fits", [_plane(2)], {"OBJECT": "M42"})
    combo = _write(tmp_path / "HSO_in.fits", [_plane(3) for _ in range(3)], {"OBJECT": "M42"})
    out = stages_colour.palette_runners(ctx, "HSO", {"L": solved, "H": bare, "S": bare, "O": bare}, _config(), None)[
        "paletteSpcc"](combo, str(tmp_path / "HSO_spcc.fits"))
    assert not any(t.startswith("platesolve") for t in ops.transcript)
    assert any(t.startswith("spcc -narrowband") for t in ops.transcript)
    assert out.skip_cache is False and log.warnings == []
    assert read_fits(os.path.join(os.fspath(ctx.work_dir), "HSO_solved.fits"))[1]["CRVAL1"] == 83.8


@pytest.mark.parametrize("bandwidth", [0, -1.0, "abc", None])
def test_palette_spcc_bandwidth_falls_back_to_three_nm(tmp_path: Path, bandwidth: Any) -> None:
    ctx, ops, _ = _ctx(tmp_path)
    plate = _write(tmp_path / "HOO_in.fits", [_plane(9) for _ in range(3)], SOLVED)
    stages_colour.palette_runners(ctx, "HOO", {"H": plate, "O": plate}, _config(narrowbandBandwidth=bandwidth),
                                  None)["paletteSpcc"](plate, str(tmp_path / "HOO_spcc.fits"))
    assert "-rbw=3.00 -gwl=500.70 -gbw=3.00" in ops.transcript[1]


def test_palette_spcc_failure_keeps_the_plate_and_skips_the_cache(tmp_path: Path) -> None:
    ctx, _, log = _ctx(tmp_path, fail={"spcc_narrowband": RuntimeError("Argument error")})
    plate = _write(tmp_path / "SHO_in.fits", [_plane(9) for _ in range(3)], SOLVED)
    out = stages_colour.palette_runners(ctx, "SHO", {"H": plate, "S": plate, "O": plate}, _config(), None)[
        "paletteSpcc"](plate, str(tmp_path / "SHO_spcc.fits"))
    assert out.skip_cache is True
    for got, exp in zip(read_fits(out.path)[0], read_fits(plate)[0]):
        np.testing.assert_array_equal(got, exp)
    assert len(log.warnings) == 1
    assert log.warnings[0].startswith("SHO could not be calibrated (")
    assert log.warnings[0].endswith("; the palette is kept uncalibrated")


def test_palette_norm_runs_the_normalisation_when_enabled(tmp_path: Path) -> None:
    ctx, _, _ = _ctx(tmp_path)
    planes = [_plane(seed, 0.1, 0.5) for seed in (21, 22, 23)]
    src = _write(tmp_path / "HOO_cal.fits", planes, SOLVED)
    runners = stages_colour.palette_runners(ctx, "HOO", {"H": src, "O": src}, _config(narrowbandNormalize=True), None)
    assert list(runners) == ["paletteCombine", "paletteSpcc", "paletteNorm"]
    out = runners["paletteNorm"](src, str(tmp_path / "HOO_norm.fits"))
    assert out.skip_cache is False
    expected = nbnorm.normalize(read_fits(src)[0], "HOO")
    for got, exp in zip(read_fits(out.path)[0], expected):
        np.testing.assert_allclose(got, exp, rtol=0, atol=1e-6)


def test_palette_norm_is_absent_when_disabled(tmp_path: Path) -> None:
    ctx, _, _ = _ctx(tmp_path)
    plate = _write(tmp_path / "HOO_in.fits", [_plane(9) for _ in range(3)], SOLVED)
    runners = stages_colour.palette_runners(ctx, "HOO", {"H": plate, "O": plate}, _config(), None)
    assert "paletteNorm" not in runners


def test_palette_norm_failure_keeps_the_plate_and_skips_the_cache(tmp_path: Path) -> None:
    ctx, _, log = _ctx(tmp_path)
    bad = np.full(SHAPE, np.nan, dtype=np.float32)  # a plane the normalisation cannot read
    src = _write(tmp_path / "SHO_flat.fits", [bad, _plane(31), _plane(32)], SOLVED)
    runners = stages_colour.palette_runners(ctx, "SHO", {"H": src, "S": src, "O": src},
                                            _config(narrowbandNormalize=True), None)
    out = runners["paletteNorm"](src, str(tmp_path / "SHO_norm.fits"))
    assert out.skip_cache is True
    for got, exp in zip(read_fits(out.path)[0], read_fits(src)[0]):
        np.testing.assert_array_equal(got, exp)
    assert len(log.warnings) == 1
    assert log.warnings[0].startswith("SHO could not be normalised (")
    assert log.warnings[0].endswith("; the palette is kept as it is")


def test_unknown_palette_and_missing_channels_are_refused(tmp_path: Path) -> None:
    ctx, _, _ = _ctx(tmp_path)
    plate = _write(tmp_path / "x.fits", [_plane(1)], SOLVED)
    with pytest.raises(ValueError, match="Unknown palette: XYZ"):
        stages_colour.palette_runners(ctx, "XYZ", {"H": plate}, _config(), None)
    with pytest.raises(ValueError, match="needs S and O, which were not supplied"):
        stages_colour.palette_runners(ctx, "SHO", {"H": plate}, _config(), None)


# ---- clean white balance -----------------------------------------------------------------------------------------------


def _clean_setup(tmp_path: Path, pre_keys: tuple[Any, Any, Any] = ("kR", "kG", "kB")) -> tuple[dict, str]:
    headers = [_channel_header("R", 60.0), _channel_header("G", 60.0), _channel_header("B", 60.0)]
    paths = _rgb_plates(tmp_path, headers)
    clean = {ch: (key, paths[ch]) for ch, key in zip(("R", "G", "B"), pre_keys)}
    return clean, paths["R"]


RECT = (0, 0, SHAPE[1], SHAPE[0])
GAINS = (0.8, 1.0, 1.25)
OFFSETS = (0.01, 0.0, -0.005)


def _clean_key(pre_keys: tuple[str, str, str] = ("kR", "kG", "kB")) -> str:
    common = dict(zip(("x0", "y0", "x1", "y1"), RECT))
    return cache.clean_white_balance_key(dict(zip(("R", "G", "B"), pre_keys)), common, FILTERS, INSTRUME)


def _measure(ctx: RunContext, clean: dict, ref: str, store: Any = None, use_cache: bool = False,
             ignore_cache: bool = False, config: dict | None = None) -> Any:
    return stages_colour.measure_clean_white_balance(ctx, clean, ref, RECT, config or _config(), INSTRUME, store,
                                                     use_cache, ignore_cache)


def _wb_handlers() -> dict[str, Any]:
    return {"rgbcomp": _composite_handler, "register": _register_handler,
            "spcc_broadband": _affine_spcc(GAINS, OFFSETS)}


def test_clean_white_balance_recovers_the_simulated_factors(tmp_path: Path) -> None:
    ctx, ops, log = _ctx(tmp_path, handlers=_wb_handlers())
    clean, ref = _clean_setup(tmp_path)
    factors = _measure(ctx, clean, ref)
    assert factors == pytest.approx(list(GAINS), rel=1e-5)
    assert log.warnings == []
    assert any(t.startswith("spcc ") for t in ops.transcript)


def test_clean_white_balance_is_taken_from_the_cache_when_present(tmp_path: Path) -> None:
    ctx, ops, log = _ctx(tmp_path, handlers=_wb_handlers())
    clean, ref = _clean_setup(tmp_path)
    store = cache.CacheStore(str(tmp_path / "cache"))
    store.write_wb(_clean_key(), [1.1, 1.0, 0.9])
    assert _measure(ctx, clean, ref, store, use_cache=True) == [1.1, 1.0, 0.9]
    assert ops.transcript == [] and any("HIT" in line for line in log.lines)


def test_clean_white_balance_ignores_the_cache_when_asked(tmp_path: Path) -> None:
    ctx, _, _ = _ctx(tmp_path, handlers=_wb_handlers())
    clean, ref = _clean_setup(tmp_path)
    store = cache.CacheStore(str(tmp_path / "cache"))
    store.write_wb(_clean_key(), [1.1, 1.0, 0.9])
    factors = _measure(ctx, clean, ref, store, use_cache=True, ignore_cache=True)
    assert factors == pytest.approx(list(GAINS), rel=1e-5)
    assert store.read_wb(_clean_key()) == pytest.approx(list(GAINS), rel=1e-5)


def test_clean_white_balance_is_stored_only_when_the_cache_is_on(tmp_path: Path) -> None:
    ctx, _, _ = _ctx(tmp_path, handlers=_wb_handlers())
    clean, ref = _clean_setup(tmp_path)
    store = cache.CacheStore(str(tmp_path / "cache"))
    _measure(ctx, clean, ref, store, use_cache=False)
    assert store.read_wb(_clean_key()) is None
    _measure(ctx, clean, ref, store, use_cache=True)
    assert store.read_wb(_clean_key()) == pytest.approx(list(GAINS), rel=1e-5)


def test_clean_white_balance_failure_falls_back_to_direct(tmp_path: Path) -> None:
    ctx, _, log = _ctx(tmp_path, handlers={"rgbcomp": _composite_handler, "register": _register_handler},
                       fail={"spcc_broadband": RuntimeError("no database")})
    clean, ref = _clean_setup(tmp_path)
    assert _measure(ctx, clean, ref) is None
    assert len(log.warnings) == 1
    assert log.warnings[0].startswith("could not build the uncorrected reference (")
    assert "colour cast" in log.warnings[0]


def test_clean_white_balance_without_a_pre_correction_key_falls_back(tmp_path: Path) -> None:
    ctx, ops, log = _ctx(tmp_path, handlers=_wb_handlers())
    clean, ref = _clean_setup(tmp_path, pre_keys=(None, "kG", "kB"))
    assert _measure(ctx, clean, ref) is None
    assert log.warnings and "no pre-correction cache key for R" in log.warnings[0]


def test_clean_white_balance_is_not_measured_without_a_sharpen_tool(tmp_path: Path) -> None:
    ctx, ops, log = _ctx(tmp_path, handlers=_wb_handlers())
    clean, ref = _clean_setup(tmp_path)
    assert _measure(ctx, clean, ref, config=_config(sharpenTool="none")) is None
    assert ops.transcript == [] and log.warnings == []


def test_clean_white_balance_needs_all_three_channels(tmp_path: Path) -> None:
    ctx, ops, _ = _ctx(tmp_path, handlers=_wb_handlers())
    clean, ref = _clean_setup(tmp_path)
    del clean["B"]
    assert _measure(ctx, clean, ref) is None
    assert ops.transcript == []


# ---- review additions: clean white balance flow, validity, cancel, solved plate, keys, messages ------------------------

BIG = (60, 80)
CROP = (6, 5, 70, 52)  # x0, y0, x1, y1 (end-exclusive): a 64 x 47 crop of the 80 x 60 plates
SHIFT = (1, 2)  # rows, columns: the fake registration moves every non-reference frame by this much


def _big_plate(path: Path, seed: int, header: dict[str, Any]) -> str:
    rng = np.random.default_rng(seed)
    return _write(path, [rng.uniform(0.05, 0.4, BIG).astype(np.float32)], header)


def _shifting_register_handler(ops: RecordingOps, name: str) -> None:
    """register: the reference (frame 1) is kept; every other frame is rolled by SHIFT, as a registration moves it."""
    for path in sorted(glob.glob(os.path.join(ops.workdir, "[0-9][0-9]_*.fits"))):
        index = int(os.path.basename(path)[:2])
        planes, header = read_fits(path)
        if index > 1:
            planes = [np.roll(p, SHIFT, axis=(0, 1)) for p in planes]
        write_fits(os.path.join(ops.workdir, f"r_{name}_{index:05d}.fit"), planes, header, bitpix=-32)


def _recording_spcc(gains: tuple[float, float, float], offsets: tuple[float, float, float], seen: list):
    def handler(ops: RecordingOps, sensor: str, r: str, g: str, b: str) -> None:
        planes = ops.pixels()
        seen.append(planes[0].shape)
        ops.set_pixels([k * p.astype(np.float64) + o for k, o, p in zip(gains, offsets, planes)])

    return handler


def _clean_big_setup(tmp_path: Path, pre_keys: tuple[Any, Any, Any] = ("kR", "kG", "kB")):
    """Pre-correction plates of the big size, and the reference, which is the R plate (the whole frame)."""
    headers = {"R": _channel_header("R", 60.0), "G": _channel_header("G", 60.0), "B": _channel_header("B", 60.0)}
    paths = {ch: _big_plate(tmp_path / f"{ch}_pre.fits", seed, headers[ch])
             for seed, ch in zip((41, 42, 43), ("R", "G", "B"))}
    clean = {ch: (key, paths[ch]) for ch, key in zip(("R", "G", "B"), pre_keys)}
    return clean, paths["R"], paths


def _cancelled_ctx(tmp_path: Path, handlers: dict | None = None, fail: dict | None = None):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    ops = RecordingOps(work, handlers=handlers, fail=fail)
    log = Log()
    ctx = RunContext(ops=ops, work_dir=str(work), log=log.lines.append, warn=log.warnings.append,
                     cancelled=lambda: True)
    return ctx, ops, log


def _subsequence(needles: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(any(h.startswith(n) for h in it) for n in needles)


def test_clean_white_balance_registers_crops_combines_solves_and_calibrates_in_order(tmp_path: Path) -> None:
    seen: list = []
    handlers = {"rgbcomp": _composite_handler, "register": _shifting_register_handler,
                "spcc_broadband": _recording_spcc(GAINS, OFFSETS, seen)}
    ctx, ops, log = _ctx(tmp_path, handlers=handlers)
    clean, ref, paths = _clean_big_setup(tmp_path)
    work = Path(ctx.work_dir)
    run_register = work / "R_register.fits"
    run_register.write_bytes(b"run-owned register output")

    factors = stages_colour.measure_clean_white_balance(ctx, clean, ref, CROP, _config(), INSTRUME, None, False, False)

    assert factors == pytest.approx(list(GAINS), rel=1e-5)
    assert run_register.read_bytes() == b"run-owned register output"
    clean_dir = work / "clean-wb"
    for ch in ("REF", "R", "G", "B"):
        assert (clean_dir / f"{ch}_register.fits").exists(), ch
    assert _subsequence(["register", "crop 6 5 64 47", "crop 6 5 64 47", "crop 6 5 64 47", "rgbcomp", "platesolve",
                         "spcc \"-monosensor="], ops.transcript)
    sensor = channels.spcc_sensor_for(INSTRUME)
    assert f'spcc "-monosensor={sensor}" "-rfilter=Baader Red" "-gfilter=Baader Green" "-bfilter=Baader Blue"' \
        in ops.transcript
    assert seen == [(47, 64)]
    # the R plate of the composite is the registered R plate, rolled and cropped
    cropped = read_fits(str(clean_dir / "RGB_clean.fits"))[0][0]
    rolled = np.roll(read_fits(paths["R"])[0][0], SHIFT, axis=(0, 1))
    np.testing.assert_array_equal(cropped, rolled[5:52, 6:70])


def test_clean_white_balance_factors_that_are_not_positive_fall_back(tmp_path: Path) -> None:
    cases = (((0.8, 0.0, 1.2), (0.0, 0.2, 0.0)), ((0.8, -0.5, 1.2), (0.0, 0.5, 0.0)))
    for index, (gains, offsets) in enumerate(cases):
        sub = tmp_path / f"case{index}"
        sub.mkdir()
        ctx, _, log = _ctx(sub, handlers={"rgbcomp": _composite_handler, "register": _register_handler,
                                          "spcc_broadband": _affine_spcc(gains, offsets)})
        clean, ref = _clean_setup(sub)
        assert _measure(ctx, clean, ref) is None, gains
        assert len(log.warnings) == 1 and log.warnings[0].startswith("could not build the uncorrected reference (")
        assert "did not record white balance factors" in log.warnings[0]


def test_clean_white_balance_identical_spcc_gives_unit_factors(tmp_path: Path) -> None:
    ctx, _, log = _ctx(tmp_path, handlers={"rgbcomp": _composite_handler, "register": _register_handler,
                                           "spcc_broadband": _affine_spcc((1.0, 1.0, 1.0), (0.0, 0.0, 0.0))})
    clean, ref = _clean_setup(tmp_path)
    assert _measure(ctx, clean, ref) == pytest.approx([1.0, 1.0, 1.0], rel=1e-9)
    assert log.warnings == []


def test_cancel_is_not_turned_into_a_warning_in_the_clean_measurement(tmp_path: Path) -> None:
    ctx, _, log = _cancelled_ctx(tmp_path, handlers={"rgbcomp": _composite_handler, "register": _register_handler},
                                 fail={"spcc_broadband": RuntimeError("cancelled")})
    clean, ref = _clean_setup(tmp_path)
    with pytest.raises(Exception, match="cancelled"):
        _measure(ctx, clean, ref)
    assert log.warnings == []


def test_cancel_is_not_turned_into_a_warning_in_palette_spcc(tmp_path: Path) -> None:
    ctx, _, log = _cancelled_ctx(tmp_path, fail={"spcc_narrowband": RuntimeError("cancelled")})
    plate = _write(tmp_path / "SHO_in.fits", [_plane(9) for _ in range(3)], SOLVED)
    with pytest.raises(Exception, match="cancelled"):
        stages_colour.palette_runners(ctx, "SHO", {"H": plate, "S": plate, "O": plate}, _config(), None)[
            "paletteSpcc"](plate, str(tmp_path / "SHO_spcc.fits"))
    assert log.warnings == []


def test_cancel_is_not_turned_into_a_warning_in_palette_norm(tmp_path: Path) -> None:
    ctx, _, log = _cancelled_ctx(tmp_path)
    bad = np.full(SHAPE, np.nan, dtype=np.float32)  # a plane the normalisation cannot read
    src = _write(tmp_path / "SHO_flat.fits", [bad, _plane(31), _plane(32)], SOLVED)
    runners = stages_colour.palette_runners(ctx, "SHO", {"H": src, "S": src, "O": src},
                                            _config(narrowbandNormalize=True), None)
    with pytest.raises(ValueError):
        runners["paletteNorm"](src, str(tmp_path / "SHO_norm.fits"))
    assert log.warnings == []


def test_palette_spcc_failure_after_a_solve_keeps_the_solved_plate(tmp_path: Path) -> None:
    def solve_doubles(ops: RecordingOps, *args: Any) -> None:
        ops.set_pixels([2.0 * p for p in ops.pixels()])

    ctx, _, log = _ctx(tmp_path, handlers={"platesolve": solve_doubles},
                       fail={"spcc_narrowband": RuntimeError("Argument error")})
    plate = _write(tmp_path / "HOO_in.fits", [_plane(9) for _ in range(3)], {"OBJECT": "M42"})
    out = stages_colour.palette_runners(ctx, "HOO", {"H": plate, "O": plate}, _config(), None)[
        "paletteSpcc"](plate, str(tmp_path / "HOO_spcc.fits"))
    assert out.skip_cache is True
    for got, exp in zip(read_fits(out.path)[0], read_fits(plate)[0]):
        np.testing.assert_allclose(got, 2.0 * exp, rtol=0, atol=1e-6)
    assert log.warnings and log.warnings[0].startswith("HOO could not be calibrated (")


@pytest.mark.parametrize("bandwidth", [0, -1.0, "abc", None, True, float("inf"), float("nan")])
def test_palette_key_keeps_the_raw_bandwidth(bandwidth: Any) -> None:
    params = stages_colour.palette_stage_params("SHO", _config(narrowbandBandwidth=bandwidth))
    assert params["paletteSpcc"] == {"palette": "SHO", "bandwidth": bandwidth}


@pytest.mark.parametrize("bandwidth", [True, float("inf"), float("nan")])
def test_palette_bandwidth_that_is_not_a_positive_number_is_three_nm(tmp_path: Path, bandwidth: Any) -> None:
    ctx, ops, _ = _ctx(tmp_path)
    plate = _write(tmp_path / "HOO_in.fits", [_plane(9) for _ in range(3)], SOLVED)
    stages_colour.palette_runners(ctx, "HOO", {"H": plate, "O": plate}, _config(narrowbandBandwidth=bandwidth),
                                  None)["paletteSpcc"](plate, str(tmp_path / "HOO_spcc.fits"))
    assert "-rbw=3.00 -gwl=500.70 -gbw=3.00" in ops.transcript[1]


def test_rgb_key_hashes_empty_filters_when_none_are_configured() -> None:
    params = stages_colour.rgb_stage_params({}, INSTRUME, None)
    assert params["spccRGB"]["filters"] == {}


def test_rgb_spcc_names_is_none_for_incomplete_filters_and_honours_the_sensor() -> None:
    assert stages_colour.rgb_spcc_names(_config(), INSTRUME) == (
        channels.spcc_sensor_for(INSTRUME), "Baader Red", "Baader Green", "Baader Blue")
    assert stages_colour.rgb_spcc_names(_config(spccSensor="Custom"), INSTRUME)[0] == "Custom"
    assert stages_colour.rgb_spcc_names(_config(filters={"R": "Baader Red", "B": "Baader Blue"}), INSTRUME) is None
    assert stages_colour.rgb_spcc_names({}, INSTRUME) is None


def test_one_missing_channel_is_reported_as_was(tmp_path: Path) -> None:
    ctx, _, _ = _ctx(tmp_path)
    plate = _write(tmp_path / "x.fits", [_plane(1)], SOLVED)
    with pytest.raises(ValueError, match="needs O, which was not supplied"):
        stages_colour.palette_runners(ctx, "SHO", {"H": plate, "S": plate}, _config(), None)


def test_unknown_palette_is_refused_even_when_it_has_a_mapping(tmp_path: Path, monkeypatch) -> None:
    ctx, _, _ = _ctx(tmp_path)
    plate = _write(tmp_path / "x.fits", [_plane(1)], SOLVED)
    monkeypatch.setattr(channels, "palette_wavelengths", lambda pal: None)
    with pytest.raises(ValueError, match="Unknown palette: SHO"):
        stages_colour.palette_runners(ctx, "SHO", {"H": plate, "S": plate, "O": plate}, _config(), None)


def test_palette_combine_removes_keywords_that_differ(tmp_path: Path) -> None:
    ctx, _, _ = _ctx(tmp_path, handlers={"rgbcomp": _composite_handler, "register": _register_handler})
    plates = {}
    for seed, ch, exptime in ((51, "H", 300.0), (52, "S", 300.0), (53, "O", 600.0)):
        plates[ch] = _write(tmp_path / f"{ch}_p.fits", [_plane(seed)],
                            {"OBJECT": "M42", "FILTER": ch, "EXPTIME": exptime, "INSTRUME": INSTRUME})
    out = stages_colour.palette_runners(ctx, "SHO", plates, _config(), None)["paletteCombine"](
        None, str(tmp_path / "SHO.fits"))
    _, header = read_fits(out.path)
    assert "FILTER" not in header and "EXPTIME" not in header
    assert header["OBJECT"] == "M42"
