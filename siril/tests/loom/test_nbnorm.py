"""Narrowband normalisation (loomsiril.loom.nbnorm): properties on seeded synthetic palettes, and the reference pair.

The reference regression reads tests/fixtures/nbnorm/ (input.fits, output.fits, palette.txt). Until that pair is supplied,
the test is skipped with the reason "reference pair not yet supplied".
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from loomsiril.io_fits import read_fits
from loomsiril.loom import nbnorm

from . import synth

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "nbnorm"
REFERENCE_TOLERANCE = 0.02
PALETTES = ("HOO", "SHO", "HSO")


@pytest.fixture(scope="module")
def narrowband_masters() -> dict[str, np.ndarray]:
    masters = synth.make_master_set(("H", "S", "O"), w=160, h=128, seed=7)
    return {ch: m.planes[0].astype(np.float64) for ch, m in masters.items()}


def _composite(masters: dict[str, np.ndarray], palette: str) -> list[np.ndarray]:
    return [masters[src] for src in nbnorm.PALETTE_SOURCES[palette]]


def test_palette_index_and_sources() -> None:
    assert nbnorm.PALETTE_INDEX == {"HOO": 0, "SHO": 1, "HSO": 2}
    assert nbnorm.PALETTE_SOURCES == {"HOO": ("H", "O", "O"), "SHO": ("S", "H", "O"), "HSO": ("H", "S", "O")}
    

def test_stage_params_and_unknown_palette() -> None:
    assert nbnorm.stage_params("SHO") == {"palette": "SHO", "method": nbnorm.METHOD}
    with pytest.raises(ValueError):
        nbnorm.stage_params("HOS")
    with pytest.raises(ValueError):
        nbnorm.normalize([np.zeros((4, 4))] * 3, "XYZ")


def test_reference_channel_is_the_ha_carrier() -> None:
    assert [nbnorm.reference_index(p) for p in PALETTES] == [0, 1, 0]


@pytest.mark.parametrize("palette", PALETTES)
def test_backgrounds_equalised_after_scaling(narrowband_masters: dict[str, np.ndarray], palette: str) -> None:
    scaled = nbnorm.scale_to_reference(_composite(narrowband_masters, palette), palette)
    backgrounds = [float(np.median(p)) for p in scaled]
    assert max(backgrounds) - min(backgrounds) < 1e-3


@pytest.mark.parametrize("palette", PALETTES)
def test_noise_matched_to_reference_after_scaling(narrowband_masters: dict[str, np.ndarray], palette: str) -> None:
    chans = _composite(narrowband_masters, palette)
    ref = nbnorm.reference_index(palette)
    _, s_ref = nbnorm.background_and_scale(chans[ref])
    for p in nbnorm.scale_to_reference(chans, palette):
        _, s = nbnorm.background_and_scale(p)
        assert s == pytest.approx(s_ref, rel=1e-9)


@pytest.mark.parametrize("palette", PALETTES)
def test_output_is_float32_in_unit_range_and_deterministic(narrowband_masters: dict[str, np.ndarray],
                                                           palette: str) -> None:
    chans = _composite(narrowband_masters, palette)
    first = nbnorm.normalize(chans, palette)
    second = nbnorm.normalize(chans, palette)
    assert len(first) == 3
    for a, b in zip(first, second):
        assert a.dtype == np.float32
        assert a.shape == chans[0].shape
        assert float(a.min()) >= 0.0 and float(a.max()) <= 1.0
        assert np.array_equal(a, b)


@pytest.mark.parametrize("palette", PALETTES)
def test_channels_share_the_sky_level_and_keep_their_signal(narrowband_masters: dict[str, np.ndarray],
                                                            palette: str) -> None:
    chans = _composite(narrowband_masters, palette)
    out = nbnorm.normalize(chans, palette)
    ref = nbnorm.reference_index(palette)
    target = nbnorm.sky_level(chans[ref])
    for i in range(3):
        assert nbnorm.sky_level(out[i].astype(np.float64)) == pytest.approx(target, abs=1e-4)
        # a shift only: differences between pixels are untouched (where nothing was clipped)
        free = (chans[i] - nbnorm.sky_level(chans[i]) + target > 0) & (chans[i] - nbnorm.sky_level(chans[i]) + target < 1)
        d = (out[i].astype(np.float64) - chans[i])[free]
        assert float(d.max() - d.min()) < 1e-6


def test_a_strong_line_keeps_its_ratio_to_a_weak_one() -> None:
    rng = np.random.default_rng(3)
    sky = rng.normal(0.10, 0.002, (128, 128))
    h = sky.copy(); h[32:96, 32:96] += 0.30       # Ha strong
    s_ = sky.copy() + 0.03; s_[32:96, 32:96] += 0.05  # S weak, with a higher sky
    o = sky.copy(); o[32:96, 32:96] += 0.10
    out = nbnorm.normalize([h, s_, o], "HSO")
    centre = [float(np.median(c[40:90, 40:90])) for c in out]
    corner = [float(np.median(c[:16, :16])) for c in out]
    signal = [c - k for c, k in zip(centre, corner)]
    assert max(corner) - min(corner) < 0.005
    assert signal[0] / signal[1] == pytest.approx(6.0, rel=0.05)   # 0.30 : 0.05, not blended towards Ha


@pytest.mark.parametrize("palette", PALETTES)
def test_output_is_clipped_to_unit_range(palette: str) -> None:
    rng = np.random.default_rng(11)
    planes = [rng.normal(0.5, 0.4, (64, 64)) for _ in range(3)]
    out = nbnorm.normalize(planes, palette)
    for o in out:
        assert o.dtype == np.float32
        assert float(o.min()) >= 0.0 and float(o.max()) <= 1.0


def test_shape_and_plane_count_are_checked() -> None:
    a = np.zeros((8, 8))
    with pytest.raises(ValueError):
        nbnorm.normalize([a, a], "HOO")
    with pytest.raises(ValueError):
        nbnorm.normalize([a, a, np.zeros((8, 9))], "HOO")


def test_reference_pair_regression() -> None:
    inp, out_ref, pal = FIXTURE_DIR / "input.fits", FIXTURE_DIR / "output.fits", FIXTURE_DIR / "palette.txt"
    if not (inp.exists() and out_ref.exists() and pal.exists()):
        pytest.skip("reference pair not yet supplied")
    palette = pal.read_text(encoding="utf-8").strip()
    planes, _ = read_fits(inp)
    expected, _ = read_fits(out_ref)
    got = nbnorm.normalize(planes, palette)
    for g, e in zip(got, expected):
        assert float(np.max(np.abs(g.astype(np.float64) - np.asarray(e, dtype=np.float64)))) <= REFERENCE_TOLERANCE
