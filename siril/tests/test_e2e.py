"""End to end (E6): a synthetic scene through ``python -m loomsiril`` in a subprocess, down to the frames on disk.

* ``test_e2e_frames_and_command``: a 320 x 180 three-channel field -> 8 frames of youtube_1080 (16-bit, sRGB
  profile) -> the ffmpeg command printed (on a machine without ffmpeg the exact command is asserted).
* ``test_fits_in_frame_not_mirrored`` (row order, required): an ASYMMETRIC field (a TAN WCS with a 17 degree
  rotation, a bright marker in the top-left quadrant only). The FITS inputs with --wcs FITS and the TIFF
  inputs with --solution give the same frame 0 within 1/65535; the marker is in the top-left of frame 0; a
  FITS starless mixed with a TIFF stars layer gives the same frame 0 too (the two layers are aligned).
* ``test_video_is_encoded_when_ffmpeg_exists`` (marker ffmpeg): a real video in tmp_path, run in Docker.

Frames are youtube_1080 (1920 x 1080). Every run is a subprocess with PYTHONPATH = the repo; no network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("loomsiril.cli")

import tifffile

from loomsiril import encode, io_fits
from loomsiril.core import tan_unproject
from loomsiril.io_tiff import write_tiff16

import synth

REPO = Path(__file__).resolve().parents[1]
ICC_TAG = 34675
COLS = ["ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP"]
Q = 1.0 / 65535.0


def _quantise(planes: list[np.ndarray]) -> list[np.ndarray]:
    """Planes as 16-bit TIFF and FITS would hold them: the same numbers in both files."""
    return [(np.rint(np.clip(p, 0, 1) * 65535) / 65535).astype(np.float32) for p in planes]


def _tsv(path: Path, rows: list[dict[str, float]]) -> Path:
    lines = ["\t".join(COLS)]
    for r in rows:
        lines.append("\t".join("" if r[c] != r[c] else repr(float(r[c])) for c in COLS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(cwd: Path, args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """python -m loomsiril <args> in cwd, PYTHONPATH = the repo; stdout and stderr captured as text."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "loomsiril", *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=900)


def _text(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _frames(out: Path) -> list[Path]:
    return sorted((out / "youtube_1080").glob("frame_*.tif"))


def _tags(path: Path) -> set[int]:
    with tifffile.TiffFile(str(path)) as tf:
        return {t.code for t in tf.pages[0].tags.values()}


def _base_args(starless: str, stars: str, out: Path, tsv: Path, *extra: str, duration: str = "0.1") -> list[str]:
    return ["--starless", starless, "--stars", stars, "--target-pixel", "80,45", "--type", "nebula",
            "--distance", "1000", "--gaia", f"tsv:{tsv}", "--preset", "youtube_1080",
            "--duration", duration, "--fps", "10", "--out", str(out), *extra]


# ---- the end-to-end run ---------------------------------------------------------------------------

def test_e2e_frames_and_command(tmp_path: Path) -> None:
    field = synth.make_field(w=320, h=180, n_stars=20, nc=3, seed=11)
    starless, stars = tmp_path / "starless.tif", tmp_path / "stars.tif"
    write_tiff16(starless, field.starless)
    write_tiff16(stars, field.stars)
    sol = tmp_path / "solution.json"
    sol.write_text(json.dumps({k: field.wcs[k] for k in ("crval1", "crval2", "crpix1", "crpix2", "cd")}), encoding="utf-8")
    tsv = _tsv(tmp_path / "gaia.tsv", field.catalogue)
    out = tmp_path / "out"
    args = _base_args(str(starless), str(stars), out, tsv, "--solution", str(sol), duration="0.8")
    proc = _run(tmp_path, args)
    text = _text(proc)
    assert proc.returncode == 0, text

    frames = _frames(out)
    assert [f.name for f in frames] == [f"frame_{i:05d}.tif" for i in range(8)], text
    first = tifffile.imread(str(frames[0]))
    assert first.dtype == np.uint16 and first.shape == (1080, 1920, 3)
    for f in frames:
        assert ICC_TAG in _tags(f), f.name
        assert tifffile.imread(str(f)).dtype == np.uint16
    json.loads((out / "youtube_1080" / "frames.json").read_text(encoding="utf-8"))
    assert not list((out / "youtube_1080").glob("*.part"))

    # no --video: the exact ffmpeg command is printed (ffmpeg is not on this box)
    video_base = str(out / encode.video_name("", "youtube_1080", "sdr"))
    args_ff = encode.ffmpeg_args(str(out / "youtube_1080"), 10, video_base, "h264", "high", None, None)
    expected = encode.command_line("ffmpeg", args_ff, encode.platform_name(sys.platform))
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("ffmpeg command: ")]
    assert lines == ["ffmpeg command: " + expected], text
    assert not list(out.glob("*.mp4"))


@pytest.mark.ffmpeg
def test_video_is_encoded_when_ffmpeg_exists(tmp_path: Path) -> None:
    field = synth.make_field(w=320, h=180, n_stars=20, nc=3, seed=11)
    starless, stars = tmp_path / "starless.tif", tmp_path / "stars.tif"
    write_tiff16(starless, field.starless)
    write_tiff16(stars, field.stars)
    sol = tmp_path / "solution.json"
    sol.write_text(json.dumps({k: field.wcs[k] for k in ("crval1", "crval2", "crpix1", "crpix2", "cd")}), encoding="utf-8")
    tsv = _tsv(tmp_path / "gaia.tsv", field.catalogue)
    out = tmp_path / "out"
    assert shutil.which("ffmpeg"), "the Docker image has ffmpeg"
    proc = _run(tmp_path, _base_args(str(starless), str(stars), out, tsv, "--solution", str(sol),
                                     "--video", duration="0.5"))
    assert proc.returncode == 0, _text(proc)
    videos = [p for p in out.iterdir() if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")]
    assert videos and all(v.stat().st_size > 0 for v in videos), _text(proc)


# ---- the orientation: FITS and TIFF rows agree, and the image is not mirrored ----------------------

def _asymmetric_field() -> dict[str, Any]:
    """A 160 x 90 field: a gradient, a marker in the top-left quadrant only, a few stars; rotated WCS (17 degrees)."""
    w, h = 160, 90
    header = synth.tan_header(w, h, 324.745, 57.514, 2.0, rot_deg=17.0)
    wcs = synth.wcs_from_header(header)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    marker = 0.9 * np.exp(-((xx - 40.0) ** 2 + (yy - 22.0) ** 2) / (2 * 5.0 ** 2))   # top-left (rows < h/2, cols < w/2)
    starless = np.clip(0.06 + 0.10 * xx / w + 0.04 * yy / h + marker, 0, 1).astype(np.float32)
    star_planes = np.zeros((h, w), dtype=np.float64)
    rows: list[dict[str, float]] = []
    for x, y, peak in ((120.0, 70.0, 0.5), (25.0, 60.0, 0.4), (100.0, 15.0, 0.45), (60.0, 35.0, 0.35)):
        star_planes += synth.gaussian(w, h, x, y, 2.5, peak)
        ra, dec = tan_unproject(x, y, wcs)["ra"], tan_unproject(x, y, wcs)["dec"]
        rows.append({"ra": ra, "dec": dec, "plx": 4.0, "pmra": 0.0, "pmdec": 0.0, "G": 8.0,
                     "BP": float("nan"), "RP": float("nan")})
    stars = np.clip(star_planes, 0, 1).astype(np.float32)
    image = (1 - (1 - starless) * (1 - stars)).astype(np.float32)
    s_q, t_q, i_q = _quantise([starless, stars, image])
    return {"starless": s_q, "stars": t_q, "image": i_q, "header": header, "wcs": wcs, "rows": rows}


def _frame0(out: Path) -> np.ndarray:
    frames = _frames(out)
    assert frames, out
    return tifffile.imread(str(frames[0])).astype(np.float64) / 65535.0


def test_fits_in_frame_not_mirrored(tmp_path: Path) -> None:
    f = _asymmetric_field()
    fits_s, fits_t = tmp_path / "starless.fits", tmp_path / "stars.fits"
    io_fits.write_fits(fits_s, [f["starless"]], f["header"])
    io_fits.write_fits(fits_t, [f["stars"]], f["header"])
    tif_s, tif_t = tmp_path / "starless.tif", tmp_path / "stars.tif"
    write_tiff16(tif_s, [f["starless"]])
    write_tiff16(tif_t, [f["stars"]])
    sol = tmp_path / "solution.json"
    sol.write_text(json.dumps({k: f["wcs"][k] for k in ("crval1", "crval2", "crpix1", "crpix2", "cd")}), encoding="utf-8")
    tsv = _tsv(tmp_path / "gaia.tsv", f["rows"])

    fits_out, tiff_out, mixed_out = tmp_path / "fits", tmp_path / "tiff", tmp_path / "mixed"
    p1 = _run(tmp_path, _base_args(str(fits_s), str(fits_t), fits_out, tsv, "--wcs", str(fits_s)))
    p2 = _run(tmp_path, _base_args(str(tif_s), str(tif_t), tiff_out, tsv, "--solution", str(sol)))
    p3 = _run(tmp_path, _base_args(str(fits_s), str(tif_t), mixed_out, tsv, "--wcs", str(fits_s)))
    for p in (p1, p2, p3):
        assert p.returncode == 0, _text(p)

    a, b, c = _frame0(fits_out), _frame0(tiff_out), _frame0(mixed_out)
    assert a.shape == (1080, 1920)
    assert np.abs(a - b).max() <= Q, "FITS in and TIFF in must give the same frame 0"
    assert np.abs(c - b).max() <= Q, "a FITS starless with a TIFF stars layer must be aligned with them"

    # the marker sits in the top-left quadrant of frame 0 (a flipped read would put it bottom-left)
    level = 0.8 * a.max()
    rows, cols = np.nonzero(a >= level)
    assert rows.mean() < a.shape[0] / 2, "the marker is in the bottom half: the FITS rows were flipped"
    assert cols.mean() < a.shape[1] / 2, "the marker is in the right half: the FITS columns were mirrored"
