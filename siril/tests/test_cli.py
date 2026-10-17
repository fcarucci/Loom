"""The command line (E6: loomsiril/cli.py): argparse errors, the WCS sources, the target, the refusals and the printed command.

The argument resolution is tested through ``_prepare`` (no render); the printed command through one
short render (one frame). Gaia is a TSV under tmp_path; ``--gaia online`` is refused under pytest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("loomsiril.cli")

from loomsiril import cli, io_fits, wcs as wcs_mod
from loomsiril.core import screen, tan_unproject
from loomsiril.io_tiff import write_tiff16

import synth

COLS = ["ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP"]


def _tsv(path: Path, catalogue: list[dict[str, float]]) -> Path:
    lines = ["\t".join(COLS)]
    for rec in catalogue:
        lines.append("\t".join("" if rec[c] != rec[c] else repr(float(rec[c])) for c in COLS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Any]:
    """The synthetic field written as the CLI reads it: TIFF layers, a solution JSON and a TSV."""
    field = synth.make_field(w=160, h=90, n_stars=12, nc=3, seed=5)
    paths = {
        "starless": tmp_path / "starless.tif",
        "stars": tmp_path / "stars.tif",
        "solution": tmp_path / "solution.json",
        "gaia": _tsv(tmp_path / "gaia.tsv", field.catalogue),
    }
    write_tiff16(paths["starless"], field.starless)
    write_tiff16(paths["stars"], field.stars)
    paths["solution"].write_text(json.dumps({k: field.wcs[k] for k in ("crval1", "crval2", "crpix1", "crpix2", "cd")}),
                                 encoding="utf-8")
    return {"field": field, **paths, "out": tmp_path / "out"}


def _args(d: dict[str, Any], *extra: str, skip: tuple[str, ...] = ()) -> list[str]:
    """The documented invocation for the synthetic inputs (the target at the image middle, a typed distance)."""
    base = {
        "--starless": str(d["starless"]), "--stars": str(d["stars"]), "--solution": str(d["solution"]),
        "--target-pixel": "80,45", "--type": "nebula", "--distance": "500", "--gaia": f"tsv:{d['gaia']}",
        "--preset": "youtube_1080", "--duration": "0.1", "--fps": "10", "--out": str(d["out"]),
    }
    argv: list[str] = []
    for k, v in base.items():
        if k not in skip:
            argv += [k, v]
    return argv + list(extra)


def _parse(argv: list[str]) -> Any:
    return cli.build_parser().parse_args(argv)


# ---- argparse errors (exit 2 with a usage message) -------------------------------------------

@pytest.mark.parametrize("extra, skip, text", [
    ([], ("--starless",), "--starless"),
    (["--wcs", "x.fits"], (), "not allowed with"),                       # --solution and --wcs together
    (["--target", "10,10"], (), "not allowed with"),                      # --target-pixel and --target together
    (["--duration", "601"], (), "at most 600"),
    (["--duration", "0"], (), "above 0"),
    (["--fps", "0"], (), "at least 1"),
    (["--preset", "no_such_preset"], (), "unknown preset"),
    (["--target", "400,10"], (), "RA in"),
    (["--gaia", "ftp://x"], (), "cache, online, folder:DIR or tsv"),
    (["--gaia", "tsv:/no/such/file.tsv"], (), "not there"),
    (["--distance", "-3"], (), "above 0"),
    (["--quality", "ultra"], (), "invalid choice"),
])
def test_argument_errors_exit_2(capsys: pytest.CaptureFixture[str], inputs: dict[str, Any], extra: list[str],
                                skip: tuple[str, ...], text: str) -> None:
    argv = _args(inputs, *extra, skip=skip)
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2
    assert text in capsys.readouterr().err


def test_a_wcs_source_is_required(capsys: pytest.CaptureFixture[str], inputs: dict[str, Any]) -> None:
    argv = _args(inputs, skip=("--solution",))
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2
    assert "required" in capsys.readouterr().err


def test_online_gaia_is_refused_under_pytest(capsys: pytest.CaptureFixture[str], inputs: dict[str, Any]) -> None:
    """PYTEST_CURRENT_TEST is set by pytest in this process: the refusal is a clear message, before any frame."""
    argv = _args(inputs, skip=("--gaia",)) + ["--gaia", "online"]
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "online" in err and "tests" in err
    assert "Traceback" not in err
    assert not inputs["out"].exists()


# ---- the WCS sources and the target --------------------------------------------------------------

def test_solution_json_gives_the_wcs(inputs: dict[str, Any]) -> None:
    job, _ = cli._prepare(_parse(_args(inputs)))
    field = inputs["field"]
    for k in ("crval1", "crval2", "crpix1", "crpix2"):
        assert job.wcs[k] == pytest.approx(field.wcs[k])
    assert job.wcs["cd"] == pytest.approx(field.wcs["cd"])


def test_wcs_fits_goes_through_read_fits(tmp_path: Path, inputs: dict[str, Any]) -> None:
    """--wcs FITS: the header's TAN keywords come back top-down from read_fits, equal to the solution."""
    field = inputs["field"]
    fits = tmp_path / "field.fits"
    io_fits.write_fits(fits, field.starless, field.header)
    argv = _args(inputs, skip=("--solution",)) + ["--wcs", str(fits)]
    job, _ = cli._prepare(_parse(argv))
    assert job.wcs["crpix2"] == pytest.approx(field.wcs["crpix2"])
    assert job.wcs["cd"] == pytest.approx(field.wcs["cd"], abs=1e-12)
    assert job.object_name == "SYNTHETIC"


def test_target_pixel_is_unprojected(inputs: dict[str, Any]) -> None:
    job, _ = cli._prepare(_parse(_args(inputs)))
    want = tan_unproject(80, 45, inputs["field"].wcs)
    assert job.target == pytest.approx({"ra": want["ra"], "dec": want["dec"]})


def test_target_ra_dec_is_taken_as_it_is(inputs: dict[str, Any]) -> None:
    argv = _args(inputs, skip=("--target-pixel",)) + ["--target", "324.7,57.5"]
    job, _ = cli._prepare(_parse(argv))
    assert job.target == {"ra": 324.7, "dec": 57.5}


def test_no_target_takes_the_header_centre_or_the_image_centre(tmp_path: Path, inputs: dict[str, Any]) -> None:
    field = inputs["field"]
    fits = tmp_path / "field.fits"
    io_fits.write_fits(fits, field.starless, field.header)
    argv = _args(inputs, skip=("--solution", "--target-pixel")) + ["--wcs", str(fits)]
    job, _ = cli._prepare(_parse(argv))
    assert job.target is not None and job.target["ra"] == pytest.approx(field.wcs["crval1"], abs=1e-6)
    job2, _ = cli._prepare(_parse(_args(inputs, skip=("--target-pixel",))))
    assert job2.target is None                                             # a TIFF has no header: the pipeline takes the image centre


def test_a_linear_input_is_warned_about(tmp_path: Path, inputs: dict[str, Any]) -> None:
    field = inputs["field"]
    dark = [p * 0.02 for p in field.starless]
    write_tiff16(inputs["starless"], dark)
    _, warnings = cli._prepare(_parse(_args(inputs)))
    assert any("linear" in w for w in warnings)


def test_image_clamps_the_starless_layer_and_keeps_the_stars(tmp_path: Path, inputs: dict[str, Any]) -> None:
    """--image: starless := min(starless, image); the --stars file is used as given (deviation D2)."""
    field = inputs["field"]
    image = [p.copy() for p in field.image]
    image[0][10:20, 10:20] = 0.01                  # a patch where the image is darker than the starless layer
    image_path = tmp_path / "image.tif"
    write_tiff16(image_path, image)
    before, _, _ = cli._load(str(inputs["starless"]))
    job, _ = cli._prepare(_parse(_args(inputs, "--image", str(image_path))))
    assert float(np.max(before[0][10:20, 10:20])) > 0.05                # the clamp has something to change
    assert float(job.starless[0][10:20, 10:20].max()) <= 0.01 + 1 / 65535 + 1e-6
    assert not np.array_equal(job.starless[0], before[0])               # and it did change those pixels
    np.testing.assert_array_equal(job.starless[0][:5], before[0][:5])   # untouched elsewhere
    np.testing.assert_allclose(job.stars[0], field.stars[0], atol=1 / 65535 + 1e-6)   # --stars as given


def test_no_distance_is_one_line_and_exit_1(capsys: pytest.CaptureFixture[str], inputs: dict[str, Any]) -> None:
    """No cluster, no bright star and no --distance: the CLI prints one line and exits 1 (review F1)."""
    assert cli.main(_args(inputs, skip=("--distance",))) == 1
    err = capsys.readouterr().err
    assert "give --distance" in err and "Traceback" not in err
    errors = [ln for ln in err.splitlines() if ln.startswith("loomsiril: error:")]
    assert len(errors) == 1 and "give --distance" in errors[0]
    assert not inputs["out"].exists()


def test_mismatched_inputs_are_an_error(capsys: pytest.CaptureFixture[str], inputs: dict[str, Any]) -> None:
    """--starless and --stars of different sizes: one line on stderr, exit 1, no traceback."""
    small = synth.make_field(w=80, h=45, n_stars=4, nc=3, seed=2)
    write_tiff16(inputs["stars"], small.stars)
    assert cli.main(_args(inputs)) == 1
    err = capsys.readouterr().err
    assert "differ" in err and "Traceback" not in err


# ---- the printed command (no ffmpeg on PATH) ------------------------------------------------------

def test_video_without_ffmpeg_prints_the_command_and_exits_0(monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str],
                                                              inputs: dict[str, Any], tmp_path: Path) -> None:
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert cli.main(_args(inputs, "--video")) == 0
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.startswith("ffmpeg command: ")]
    assert len(lines) == 1
    assert "frame_%05d.tif" in lines[0] and "-framerate" in lines[0]
    assert "video:" not in captured.out
    assert len(list((inputs["out"] / "youtube_1080").glob("frame_*.tif"))) == 1
    assert not list((inputs["out"] / "youtube_1080").glob("*.mp4"))
