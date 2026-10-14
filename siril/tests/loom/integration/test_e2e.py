"""End-to-end headless runs of Loom/loom_preprocess.py under siril-cli, on synthetic masters: files, names, keywords and pixels.

Each test drives the script the way a job file does (docs/loom-job.md) and checks the status file, the output folder,
the FITS keywords, the pixel statistics, the 16-bit TIFFs with their ICC profile and the layered PSB.

Offline (marker ``siril`` only): a luminance-only run, narrowband without a palette, the validate-only check and a
preflight refusal. These need no SPCC database. Network (marker ``network``, LOOMSIRIL_NETWORK=1 after
docker/network-setup.sh): every run with an RGB composite or a palette, because the composite is colour-calibrated
with SPCC. StarNet2 runs also carry marker ``starnet``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

import numpy as np
import pytest
import tifffile
from scipy import ndimage

from loomsiril.io_fits import read_fits
from loomsiril.io_tiff import read_image as read_tiff
from loomsiril.starnet_licence import STARNET_LICENCE_REV

from ..test_psb import _read_psb
from .conftest import FILTERS, LoomRun, job_paths

pytestmark = pytest.mark.siril

#: Median of the stretched composite's channels, averaged (the stretch target of the MTF).
SKY_TARGET = 0.25
SKY_TOLERANCE = 0.01
#: Layers in the PSB of an RGB composite with no stars and no palette: the RGB group (its pixel layer and the two
#: divider records) and the three background, nebulosity and vibrance adjustment layers.
RGB_PSB_LAYERS = 6


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _keyword(path: Path, key: str) -> object:
    _planes, header = read_fits(path)
    return header.get(key)


def _star_peaks(plane: np.ndarray) -> int:
    """Stars as local maxima at least five robust sigmas above the median (the noise sigma from the MAD)."""
    data = plane.astype(np.float64)
    med = float(np.median(data))
    sigma = 1.4826 * float(np.median(np.abs(data - med)))
    local = ndimage.maximum_filter(data, size=7) == data
    return int(np.count_nonzero(local & (data > med + 5.0 * sigma)))


def _assert_ok(result: LoomRun) -> None:
    assert result.status["ok"] is True, f"{result.status}\n--- siril output tail ---\n{result.run.tail(60)}"
    assert result.status["exitCode"] == 0, result.status
    assert result.status["problems"] == []
    assert result.status["error"] is None


def _assert_refused(result: LoomRun) -> None:
    assert result.status["ok"] is False
    assert result.status["exitCode"] == 2, result.status


# ---- offline (no SPCC: narrowband without a palette, validate-only, a preflight refusal) --------------------------


def _nb_job(paths: dict[str, str], tmp_path: Path, **extra: object) -> dict[str, object]:
    job: dict[str, object] = {
        "paths": job_paths(H=paths["H"], S=paths["S"], O=paths["O"]),
        "gradientTool": "none",
        "stretch": True,
        "outputDir": str(tmp_path / "out"),
        "cacheDir": str(tmp_path / "cache"),
    }
    job.update(extra)
    return job


def test_validate_only_reports_and_writes_nothing(tmp_path: Path, masters, run_loom) -> None:
    """validateOnly: the checks run, the verdict is printed, and no output or export folder is written."""
    paths = masters(("H", "S", "O"))
    export_dir = tmp_path / "export"
    job = _nb_job(paths, tmp_path, validateOnly=True, exportDir=str(export_dir), exportPsb=True)
    result = run_loom(job, "validate")

    _assert_ok(result)
    assert result.status["produced"] == []
    assert result.status["outputs"] == {}
    assert "All checks passed. Nothing executed." in result.run.printed()
    assert not export_dir.exists()
    out_dir = tmp_path / "out"
    assert not out_dir.exists() or [p for p in out_dir.rglob("*") if p.is_file()] == []


def test_preflight_refusal_names_the_missing_channel_and_runs_nothing(tmp_path: Path, masters, run_loom) -> None:
    """R and G given, B missing: exit code 2, the problem is listed, and no output is written."""
    paths = masters(("R", "G"))
    job = {
        "paths": job_paths(R=paths["R"], G=paths["G"]),
        "filters": dict(FILTERS),
        "gradientTool": "none",
        "stretch": True,
        "outputDir": str(tmp_path / "out"),
        "cacheDir": str(tmp_path / "cache"),
    }
    result = run_loom(job, "missing-b")

    _assert_refused(result)
    assert result.status["produced"] == []
    assert any("missing B" in problem for problem in result.status["problems"]), result.status["problems"]
    assert any(ln.startswith("Preflight found") for ln in result.run.printed())
    out_dir = tmp_path / "out"
    assert not out_dir.exists() or [p for p in out_dir.rglob("*") if p.is_file()] == []


def test_narrowband_without_palette_writes_h_s_o_with_normalisation(tmp_path: Path, masters, run_loom) -> None:
    """H, S and O with no palette: each narrowband plate is kept (H.fits, S.fits, O.fits), registered and normalised.

    Checks the names, the LOOMOUT keywords, the shared geometry and that no SHO plate is written.
    """
    paths = masters(("H", "S", "O"))
    out_dir = tmp_path / "out"
    result = run_loom(_nb_job(paths, tmp_path, narrowbandNormalize=True, palettes=[]), "narrowband")

    _assert_ok(result)
    assert sorted(result.status["produced"]) == ["H", "O", "S"]
    shapes = set()
    for name in ("H", "S", "O"):
        fits = out_dir / f"{name}.fits"
        assert fits.is_file(), name
        assert _keyword(fits, "LOOMOUT") == name
        planes, _header = read_fits(fits)
        assert len(planes) == 1
        shapes.add(planes[0].shape)
        assert float(np.min(planes[0])) >= 0.0
    assert len(shapes) == 1, f"registered and cropped plates must share one shape, got {shapes}"
    assert not (out_dir / "SHO.fits").exists()


def test_narrowband_tiff_export_is_16_bit_with_icc_and_no_psb(tmp_path: Path, masters, run_loom) -> None:
    """Export folder with stretch on: one 16-bit TIFF per result, each with the embedded sRGB ICC profile.

    The export names are the result names. No PSB is written: a narrowband set without a palette has no PSB layers,
    so the layered export is logged and skipped while the TIFFs are still written.
    """
    paths = masters(("H", "S", "O"))
    export_dir = tmp_path / "tif"
    result = run_loom(_nb_job(paths, tmp_path, exportDir=str(export_dir), exportPsb=True), "tiff")

    _assert_ok(result)
    assert sorted(p.name for p in export_dir.iterdir()) == ["H.tif", "O.tif", "S.tif"]
    for name in ("H", "O", "S"):
        tiff = export_dir / f"{name}.tif"
        raw = tiff.read_bytes()
        assert raw[:4] in (b"II*\x00", b"MM\x00*"), name
        assert tifffile.imread(tiff).dtype == np.uint16, name
        _planes, icc = read_tiff(tiff)
        assert icc is not None and len(icc) > 128, name
        assert icc[36:40] == b"acsp", name  # ICC profile header signature
    assert not any(p.suffix == ".psb" for p in export_dir.iterdir())


def _assert_cached_rerun(first: LoomRun, second: LoomRun, out_dir: Path, before: Mapping[str, str]) -> None:
    """The second run of one job on one cache folder: every chain hits, outputs are numbered and pixels match."""
    for name, digest in before.items():
        assert _sha256(out_dir / f"{name}.fits") == digest, f"the first run's {name}.fits was overwritten"
        rerun = out_dir / f"{name}_1.fits"
        assert rerun.is_file(), name
        assert second.status["outputs"][name].endswith(f"{name}_1.fits")
        first_planes, _ = read_fits(out_dir / f"{name}.fits")
        again_planes, _ = read_fits(rerun)
        assert len(first_planes) == len(again_planes)
        for a, b in zip(first_planes, again_planes):
            assert np.array_equal(a, b), f"{name}: a cache hit must give the same pixels"

    cache_lines = [ln for ln in second.log_lines() if "[cache]" in ln]
    assert [ln for ln in cache_lines if " HIT " in ln], "the rerun logged no cache hit"
    misses_run = [ln for ln in cache_lines if " MISS (" in ln and "skipped" not in ln]
    assert misses_run == [], "a stage ran again on the rerun: " + misses_run[0]
    assert second.pipeline_s() < 0.5 * first.pipeline_s(), (
        f"the cached rerun took {second.pipeline_s():.2f} s against {first.pipeline_s():.2f} s"
    )


def test_second_narrowband_run_with_the_same_cache_hits_and_never_overwrites(tmp_path: Path, masters,
                                                                             run_loom) -> None:
    """The same narrowband job twice on one cache folder: the second run hits the cache, its outputs are numbered
    (H_1.fits, S_1.fits, O_1.fits), the first outputs are untouched and the pixels match."""
    paths = masters(("H", "S", "O"))
    out_dir = tmp_path / "out"
    job = _nb_job(paths, tmp_path)
    first = run_loom(job, "nb-first")
    _assert_ok(first)
    before = {name: _sha256(out_dir / f"{name}.fits") for name in ("H", "S", "O")}
    second = run_loom(job, "nb-second")
    _assert_ok(second)
    _assert_cached_rerun(first, second, out_dir, before)


# ---- network: SPCC (the composite and the palettes) ----------------------------------------------------------------


@pytest.mark.network
def test_rgb_run_with_stretch_writes_rgb_with_wcs_and_stretched_sky(tmp_path: Path, masters, run_loom) -> None:
    """RGB composite: gradient tool none (the background is still subtracted), stretch on.

    RGB.fits holds three planes, the LOOMOUT keyword is RGB, the WCS is present, and the mean of the channel medians
    is the stretch target (0.25 +/- 0.01).
    """
    paths = masters(("R", "G", "B"))
    out_dir = tmp_path / "out"
    job = {
        "paths": job_paths(R=paths["R"], G=paths["G"], B=paths["B"]),
        "filters": dict(FILTERS),
        "gradientTool": "none",
        "stretch": True,
        "outputDir": str(out_dir),
        "cacheDir": str(tmp_path / "cache"),
    }
    result = run_loom(job, "rgb")

    _assert_ok(result)
    assert result.status["produced"] == ["RGB"]
    fits = out_dir / "RGB.fits"
    assert fits.is_file()
    planes, header = read_fits(fits)
    assert len(planes) == 3
    assert header["LOOMOUT"] == "RGB"
    assert "CTYPE1" in header and "CRVAL1" in header
    sky = float(np.mean([np.median(p) for p in planes]))
    assert abs(sky - SKY_TARGET) <= SKY_TOLERANCE, f"sky median after stretch {sky:.4f}"


@pytest.mark.network
def test_second_rgb_run_with_the_same_cache_hits_every_chain(tmp_path: Path, masters, run_loom) -> None:
    """The RGB run twice: the same job on one cache folder; the second run hits every chain."""
    paths = masters(("R", "G", "B"))
    out_dir = tmp_path / "out"
    job = {
        "paths": job_paths(R=paths["R"], G=paths["G"], B=paths["B"]),
        "filters": dict(FILTERS),
        "gradientTool": "none",
        "stretch": True,
        "outputDir": str(out_dir),
        "cacheDir": str(tmp_path / "cache"),
    }
    first = run_loom(job, "rgb-first")
    _assert_ok(first)
    before = {"RGB": _sha256(out_dir / "RGB.fits")}
    second = run_loom(job, "rgb-second")
    _assert_ok(second)
    _assert_cached_rerun(first, second, out_dir, before)


@pytest.mark.network
def test_tiff_and_layered_psb_export_of_the_rgb_composite(tmp_path: Path, masters, run_loom) -> None:
    """Export TIFF + PSB of an RGB composite: 16-bit RGB TIFF with ICC, and a PSB named from projectName.

    projectName "Field 1" gives "Field_1.psb". The PSB has the RGB group and the three adjustment layers
    (RGB_PSB_LAYERS), the group named RGB and the pixel layer named RGB.
    """
    paths = masters(("R", "G", "B"))
    out_dir = tmp_path / "out"
    export_dir = tmp_path / "export"
    job = {
        "paths": job_paths(R=paths["R"], G=paths["G"], B=paths["B"]),
        "filters": dict(FILTERS),
        "gradientTool": "none",
        "stretch": True,
        "exportDir": str(export_dir),
        "exportPsb": True,
        "projectName": "Field 1",
        "outputDir": str(out_dir),
        "cacheDir": str(tmp_path / "cache"),
    }
    result = run_loom(job, "export")

    _assert_ok(result)
    tiff = export_dir / "RGB.tif"
    assert tiff.is_file()
    planes, icc = read_tiff(tiff)
    assert len(planes) == 3
    assert icc is not None and icc[36:40] == b"acsp"
    assert tifffile.imread(tiff).dtype == np.uint16
    raw = tiff.read_bytes()
    assert raw[:4] in (b"II*\x00", b"MM\x00*")

    psb = export_dir / "Field_1.psb"
    assert psb.is_file()
    doc = _read_psb(psb.read_bytes())
    assert doc["depth"] == 16
    assert len(doc["layers"]) == RGB_PSB_LAYERS
    names = [layer["name"] for layer in doc["layers"]]
    assert "RGB" in names
    assert "Color Vibrance" in names


@pytest.mark.network
@pytest.mark.starnet
def test_lrgb_with_starnet2_writes_starless_and_stars_plates(tmp_path: Path, masters, run_loom) -> None:
    """LRGB with StarNet2 and stretch: RGB_starless, RGB_stars, L_starless and L_stars.

    The stars plates are non-negative. Each starless plate has fewer detected stars than its input (the starless plus
    the stars plate). The masters are MASTER_W x MASTER_H (see conftest.py).
    """
    paths = masters(("L", "R", "G", "B"))
    out_dir = tmp_path / "out"
    job = {
        "paths": job_paths(L=paths["L"], R=paths["R"], G=paths["G"], B=paths["B"]),
        "filters": dict(FILTERS),
        "gradientTool": "none",
        "stretch": True,
        "starTool": "StarNet2",
        "starnetLicenceAccepted": STARNET_LICENCE_REV,
        "outputDir": str(out_dir),
        "cacheDir": str(tmp_path / "cache"),
    }
    result = run_loom(job, "starnet")

    _assert_ok(result)
    plates = {}
    for name in ("RGB_starless", "RGB_stars", "L_starless", "L_stars"):
        fits = out_dir / f"{name}.fits"
        assert fits.is_file(), name
        assert _keyword(fits, "LOOMOUT") == name
        plates[name] = read_fits(fits)[0]
    for name in ("RGB_stars", "L_stars"):
        assert all(float(np.min(p)) >= 0.0 for p in plates[name]), f"{name} has negative pixels"

    for starless, stars in (("RGB_starless", "RGB_stars"), ("L_starless", "L_stars")):
        combined = plates[starless][0].astype(np.float64) + plates[stars][0].astype(np.float64)
        assert _star_peaks(plates[starless][0]) < _star_peaks(combined), starless


@pytest.mark.network
def test_sho_palette_with_narrowband_normalisation(tmp_path: Path, masters, run_loom) -> None:
    """SHO palette from H, S and O with narrowband normalisation: SHO.fits, and no separate H, S or O plates."""
    paths = masters(("H", "S", "O"))
    out_dir = tmp_path / "out"
    job = {
        "paths": job_paths(H=paths["H"], S=paths["S"], O=paths["O"]),
        "gradientTool": "none",
        "stretch": True,
        "narrowbandNormalize": True,
        "palettes": ["SHO"],
        "outputDir": str(out_dir),
        "cacheDir": str(tmp_path / "cache"),
    }
    result = run_loom(job, "sho")

    _assert_ok(result)
    fits = out_dir / "SHO.fits"
    assert fits.is_file()
    assert _keyword(fits, "LOOMOUT") == "SHO"
    planes, _header = read_fits(fits)
    assert len(planes) == 3
    assert float(np.min(planes[0])) >= 0.0
    for name in ("H", "S", "O"):
        assert not (out_dir / f"{name}.fits").exists(), f"{name} is kept only when no palette was built"
