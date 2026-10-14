"""Shared helpers for the black-box acceptance tests. Only public CLI/file formats are used."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import tifffile

REPO = Path(__file__).resolve().parents[2]
ICC_TAG = 34675


def write_tiff16(path: Path, planes: list[np.ndarray]) -> None:
    """Write float planes in [0, 1] as a 16-bit TIFF, (h, w) for mono or (h, w, c) for colour."""
    arr = np.stack([np.clip(p.astype(np.float64), 0, 1) for p in planes], axis=-1)
    arr = np.rint(arr * 65535).astype(np.uint16)
    if arr.shape[-1] == 1:
        arr = arr[..., 0]
    tifffile.imwrite(str(path), arr)


def read_planes(path: Path) -> np.ndarray:
    """Read a TIFF and return a float64 (h, w, c) array in 0..1 (uint16 scaled)."""
    arr = tifffile.imread(str(path))
    assert arr.dtype == np.uint16, f"{path.name}: expected 16-bit, got {arr.dtype}"
    if arr.ndim == 2:
        arr = arr[..., None]
    return arr.astype(np.float64) / 65535.0


def write_gaia_tsv(path: Path, catalogue: list[dict[str, float]]) -> None:
    """Tab separated, header ra dec plx pmra pmdec G BP RP, blanks for NaN."""
    cols = ["ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP"]

    def cell(v: float) -> str:
        return "" if v != v else repr(float(v))

    lines = ["\t".join(cols)]
    for rec in catalogue:
        lines.append("\t".join(cell(rec[c]) for c in cols))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_inputs(tmp: Path, field: Any) -> dict[str, Path]:
    """Write starless/stars/image TIFFs, the solution JSON and the Gaia TSV for a synthetic field."""
    paths = {
        "starless": tmp / "starless.tif",
        "stars": tmp / "stars.tif",
        "image": tmp / "image.tif",
        "solution": tmp / "solution.json",
        "gaia": tmp / "gaia.tsv",
    }
    write_tiff16(paths["starless"], field.starless)
    write_tiff16(paths["stars"], field.stars)
    write_tiff16(paths["image"], field.image)
    wcs = field.wcs
    paths["solution"].write_text(
        json.dumps({k: wcs[k] for k in ("crval1", "crval2", "crpix1", "crpix2", "cd")}), encoding="utf-8"
    )
    write_gaia_tsv(paths["gaia"], field.catalogue)
    return paths


def cli_args(paths: dict[str, Path], out: Path, field: Any, *extra: str, duration: str = "0.3", fps: str = "10",
             distance: bool = True) -> list[str]:
    """The documented CLI invocation for a synthetic field, centred on the image middle.

    The synthetic field has no cluster and no bright star, so the CLI cannot find a distance itself
    (as in the JS); pass --distance unless ``distance`` is False.
    """
    args = [
        "--starless", str(paths["starless"]),
        "--stars", str(paths["stars"]),
        "--image", str(paths["image"]),
        "--solution", str(paths["solution"]),
        "--target-pixel", f"{field.w / 2},{field.h / 2}",
        "--type", "nebula",
        "--distance", "1000",
        "--gaia", f"tsv:{paths['gaia']}",
        "--preset", "youtube_1080",
        "--duration", duration,
        "--fps", fps,
        "--out", str(out),
        *extra,
    ]
    if not distance:
        i = args.index("--distance")
        del args[i:i + 2]
    return args


def run_cli(cwd: Path, args: list[str], env_extra: dict[str, str] | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    """python -m loomsiril <args> in cwd with PYTHONPATH = repo root; stdout+stderr captured as text."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "loomsiril", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
    )


def combined(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def frame_files(out: Path, preset: str = "youtube_1080") -> list[Path]:
    return sorted((out / preset).glob("frame_*.tif"))


def tiff_tags(path: Path) -> set[int]:
    with tifffile.TiffFile(str(path)) as tf:
        return {t.code for t in tf.pages[0].tags.values()}
