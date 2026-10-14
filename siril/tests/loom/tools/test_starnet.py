"""StarNet2 adapter: the licence gate, the runner-based process, cancel and progress, the exchange folder, and the
output written with the input header.

The StarNet2 binary is a fake (tests/loom/tools/fakebin.py). The real CLI runs in the marked test at the end.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.tools import starnet
from loomsiril.loom.tools.runner import ToolCancelled
from loomsiril.siril_bridge import StarnetError
from loomsiril.starnet_licence import STARNET_LICENCE_REV

from .. import synth
from .fakebin import make_fake, read_argv

STARLESS_VALUE = 0.05


def _starless_bytes(tmp_path: Path, shape: tuple[int, int] = (48, 64)) -> bytes:
    """The FITS plate the fake writes as its output: a flat image at STARLESS_VALUE, as float32 bytes."""
    path = tmp_path / "starless_src.fits"
    write_fits(path, [np.full(shape, STARLESS_VALUE, dtype=np.float32)], bitpix=-32)
    return path.read_bytes()


def _input(tmp_path: Path) -> Path:
    synth.write_master(tmp_path / "linear.fits", synth.make_master("L", w=64, h=48))
    return tmp_path / "linear.fits"


def _ctx(work: Path, cancelled=lambda: False, on_progress=None, log=None) -> starnet.ToolContext:
    return starnet.ToolContext(work_dir=work, log=log, cancelled=cancelled, on_progress=on_progress,
                               platform="linux")


def _fake(tmp_path: Path, **kw) -> Path:
    return make_fake(tmp_path / "bin", "starnet2", **kw)


def test_licence_not_accepted_refuses_before_anything_runs(tmp_path: Path) -> None:
    src = _input(tmp_path)
    exe = _fake(tmp_path, write_files={"--output": _starless_bytes(tmp_path)})
    lines: list[str] = []
    for revision in (None, "", "2020-01-01", STARNET_LICENCE_REV.upper() + "x"):
        with pytest.raises(starnet.StarNetLicenceNotAccepted) as err:
            starnet.remove_stars(exe, lines.append, src, tmp_path / "out.fits", revision)
        assert f"revision {STARNET_LICENCE_REV}" in str(err.value)
    assert read_argv(exe.parent, "starnet2") == []
    assert lines == []
    assert not (tmp_path / "out.fits").exists()


def test_accepted_revision_runs_starnet_and_writes_the_starless_plate_with_the_header(tmp_path: Path) -> None:
    src = _input(tmp_path)
    exe = _fake(tmp_path, write_files={"--output": _starless_bytes(tmp_path)})
    out = tmp_path / "starless.fits"
    lines: list[str] = []
    saved = starnet.remove_stars(exe, lines.append, src, out, STARNET_LICENCE_REV)
    assert saved == str(out)
    planes, header = read_fits(out)
    assert planes[0].shape == (48, 64)
    assert np.allclose(planes[0], STARLESS_VALUE)
    assert header["CRVAL1"] == read_fits(src)[1]["CRVAL1"]
    calls = read_argv(exe.parent, "starnet2")
    assert len(calls) == 1
    argv = calls[0]
    assert argv[argv.index("--input") + 1].endswith("starnet_in.fits")
    assert argv[argv.index("--output") + 1].endswith("starnet_out.fits")
    assert any(ln.startswith("starnet2: ") and "--input" in ln for ln in lines)


def test_the_input_plate_is_clipped_to_the_unit_range(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "hot.fits"
    write_fits(src, [np.full((48, 64), 1.7, dtype=np.float32)], bitpix=-32)
    exe = _fake(tmp_path, write_files={"--output": _starless_bytes(tmp_path)})
    sent: list[float] = []
    real_run_tool = starnet.run_tool

    def spy(tool, argv, **kw):
        # the exchange folder is removed after the run, so the input is read while the run is in progress
        sent.append(float(read_fits(argv[argv.index("--input") + 1])[0][0].max()))
        return real_run_tool(tool, argv, **kw)

    monkeypatch.setattr(starnet, "run_tool", spy)
    starnet.remove_stars(exe, None, src, tmp_path / "out.fits", STARNET_LICENCE_REV)
    assert sent == [1.0]


def test_a_progress_line_from_starnet_reaches_on_progress(tmp_path: Path) -> None:
    src = _input(tmp_path)
    exe = _fake(tmp_path, stdout_lines=["[ 45%] Removing stars (1/2)"],
                write_files={"--output": _starless_bytes(tmp_path)})
    seen: list[tuple[float, str]] = []
    starnet.remove_stars(exe, _ctx(tmp_path / "w", on_progress=lambda p, t: seen.append((p, t))), src,
                         tmp_path / "out.fits", STARNET_LICENCE_REV)
    assert seen and seen[0][0] == 45.0 and seen[0][1] == "Removing stars"


def test_exit_zero_without_output_raises_starnet_error(tmp_path: Path) -> None:
    src = _input(tmp_path)
    exe = _fake(tmp_path)  # a frame smaller than the window: exits 0, writes nothing
    with pytest.raises(StarnetError, match="exited with 0 and no output"):
        starnet.remove_stars(exe, None, src, tmp_path / "out.fits", STARNET_LICENCE_REV)
    assert not (tmp_path / "out.fits").exists()


def test_nonzero_exit_raises_starnet_error(tmp_path: Path) -> None:
    src = _input(tmp_path)
    exe = _fake(tmp_path, exit_code=3, stderr_lines=["model missing"])
    with pytest.raises(StarnetError, match="exited with 3 and no output: model missing"):
        starnet.remove_stars(exe, None, src, tmp_path / "out.fits", STARNET_LICENCE_REV)


def test_exchange_folder_is_removed_after_the_run(tmp_path: Path) -> None:
    src = _input(tmp_path)
    work = tmp_path / "work"
    exe = _fake(tmp_path, write_files={"--output": _starless_bytes(tmp_path)})
    starnet.remove_stars(exe, _ctx(work), src, tmp_path / "out.fits", STARNET_LICENCE_REV)
    assert list((work / "tools").iterdir()) == []


def test_exchange_folder_is_removed_when_starnet_fails(tmp_path: Path) -> None:
    src = _input(tmp_path)
    work = tmp_path / "work"
    exe = _fake(tmp_path)
    with pytest.raises(StarnetError):
        starnet.remove_stars(exe, _ctx(work), src, tmp_path / "out.fits", STARNET_LICENCE_REV)
    assert list((work / "tools").iterdir()) == []


def test_cancelled_before_start_runs_nothing(tmp_path: Path) -> None:
    src = _input(tmp_path)
    exe = _fake(tmp_path, write_files={"--output": _starless_bytes(tmp_path)})
    with pytest.raises(ToolCancelled):
        starnet.remove_stars(exe, _ctx(tmp_path / "w", cancelled=lambda: True), src, tmp_path / "out.fits",
                             STARNET_LICENCE_REV)
    assert read_argv(exe.parent, "starnet2") == []


def test_cancel_stops_a_hanging_starnet_within_five_seconds(tmp_path: Path) -> None:
    src = _input(tmp_path)
    work = tmp_path / "work"
    exe = _fake(tmp_path, hang=True)
    started = time.monotonic()
    cancel_after = started + 0.5
    with pytest.raises(ToolCancelled):
        starnet.remove_stars(exe, _ctx(work, cancelled=lambda: time.monotonic() > cancel_after), src,
                             tmp_path / "out.fits", STARNET_LICENCE_REV)
    assert time.monotonic() - started < 5.0
    assert list((work / "tools").iterdir()) == []
    assert not (tmp_path / "out.fits").exists()


@pytest.mark.starnet
def test_real_starnet_removes_the_stars_of_a_640x512_linear_master(tmp_path: Path) -> None:
    exe = os.environ.get("LOOMSIRIL_STARNET") or shutil.which("starnet2") or shutil.which("starnet++")
    assert exe is not None
    src = tmp_path / "linear.fits"
    synth.write_master(src, synth.make_master("L", w=640, h=512))
    out = tmp_path / "starless.fits"
    starnet.remove_stars(exe, None, src, out, STARNET_LICENCE_REV)
    image = read_fits(src)[0][0].astype(np.float64)
    starless = read_fits(out)[0][0].astype(np.float64)
    # Star signal left after removal, above the background, as a fraction of the stars' peak in the input.
    residual_peak = float(starless.max() - np.median(starless))
    input_peak = float(image.max() - np.median(image))
    assert residual_peak / input_peak < 0.05
    # The background is kept: an all-zeros or all-ones stub fails here.
    assert abs(float(np.median(starless)) - float(np.median(image))) < 0.1 * float(np.median(image))
