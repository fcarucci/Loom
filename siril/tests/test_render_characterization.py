"""Render characterization (R1-c): the frames of frame.frame are bit-identical to the recorded hashes.

The refactor of loomsiril/sprites.py and frame.py (parameter objects, the shared JS-semantics helpers) must not
change one output value. This file renders the synthetic scenes of tests/test_frame.py at t = 0, 0.3, 1 for
SDR, PQ and HLG output, nc 1 and 3, star quality "highest" and "medium", motion blur on and off, and compares the
SHA-256 of each frame's float32 bytes with tests/oracle/render_characterization.json (hashes only, no frames).

The hashes were recorded on the code before R1-c (fly/final@ecc3f26); every later step must keep them equal.
Regenerate only on purpose, with ``python tests/test_render_characterization.py --record`` from the worktree
root with PYTHONPATH set, and say why in the commit.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from loomsiril.colour import SRGB_COLOUR, output_transform
from loomsiril.core import Rect
from loomsiril.frame import RenderOptions, frame
from loomsiril.types import Scene

from test_frame import _gauss_patch, _scene_of, _sprite, _synthetic_backdrop

HASH_FILE = Path(__file__).resolve().parent / "oracle" / "render_characterization.json"

W, H = 40, 30
TIMES = (0.0, 0.3, 1.0)
MODES = ("sdr", "pq", "hlg")
NCS = (1, 3)
QUALITIES = ("highest", "medium")
BLURS = (False, True)


@dataclass(frozen=True)
class Case:
    """One rendered frame: its time, output mode, channel count, star quality and motion blur."""

    t: float
    mode: str
    nc: int
    quality: str
    blur: bool

    @property
    def name(self) -> str:
        return "t%s-%s-nc%d-%s-%s" % (self.t, self.mode, self.nc, self.quality, "blur" if self.blur else "still")


def all_cases() -> list[Case]:
    """Every combination of the characterization matrix, in a fixed order."""
    return [Case(t, m, nc, q, b) for t in TIMES for m in MODES for nc in NCS for q in QUALITIES for b in BLURS]


def _scene(nc: int) -> Scene:
    """The two-sprite scene of test_frame, plus a third sprite with a core and spikes, in nc channels."""
    S = [_synthetic_backdrop(W, H, seed=c) for c in range(nc)]
    R = [np.zeros((H, W), np.float32) for _ in range(nc)]
    specs = [(9.3, 7.6, 2.0, 0.6, 180.0, 0.0, 6.0), (21.4, 9.8, 1.6, 0.9, 260.0, 1.5, 7.0), (30.2, 20.5, 1.2, 0.8, 120.0, 0.0, 9.0)]
    sprites = []
    for dx, dy, sigma, peak, d, core, g_mag in specs:
        r = int(math.ceil(3 * sigma)) + 1
        rect = Rect(int(dx) - r, int(dy) - r, int(dx) + r + 1, int(dy) + r + 1)
        patches = [_gauss_patch(rect, dx, dy, sigma, peak * (1.0 - 0.2 * c)) for c in range(nc)]
        sp = _sprite(dx, dy, rect, patches, d, G_mag=g_mag, core=core)
        sp.spike_angles = [0.0, 1.2, 2.5]
        sp.model_spikes = [_gauss_patch(rect, dx, dy, 0.6, 0.3 * peak) for _ in range(nc)]
        sprites.append(sp)
    return _scene_of(W, H, S, R, sprites, D=900.0)


def _options(case: Case) -> RenderOptions:
    """The frame options of a case: motion, twinkle, bloom and the HDR headroom switched on together."""
    opts = {"peak": 1000.0} if case.mode == "pq" else None
    tr = output_transform(SRGB_COLOUR, case.mode, opts)
    return RenderOptions(travel=60.0, easing="linear", growth=0.15, brightening=True, twinkle=0.5, duration=2.0,
                         frame_dt=1.0 / 12.0, motion_blur=case.blur, star_quality=case.quality, output=tr,
                         star_hdr=case.mode != "sdr", bloom=0.2)


def render_hash(case: Case) -> list[str]:
    """SHA-256 of each output plane of one case's frame (one hash per channel)."""
    sc = _scene(case.nc)
    planes = frame(sc, case.t, _options(case), W, H, {"x": 0, "y": 0, "w": W, "h": H})
    assert len(planes) == case.nc
    return [hashlib.sha256(np.ascontiguousarray(p).tobytes()).hexdigest() for p in planes]


def render_all() -> dict[str, list[str]]:
    """The hashes of the whole matrix, keyed by case name."""
    return {case.name: render_hash(case) for case in all_cases()}


def _expected() -> dict[str, Any]:
    with HASH_FILE.open() as fh:
        return json.load(fh)


def test_matrix_is_the_recorded_one() -> None:
    assert sorted(_expected()["frames"]) == sorted(c.name for c in all_cases())


def test_frames_are_bit_identical_to_the_recorded_hashes() -> None:
    want = _expected()["frames"]
    bad = []
    for case in all_cases():
        got = render_hash(case)
        if got != want[case.name]:
            bad.append(case.name)
    assert not bad, bad


if __name__ == "__main__":  # pragma: no cover - recording only
    if "--record" not in sys.argv:
        raise SystemExit("usage: test_render_characterization.py --record")
    out = {"about": "SHA-256 of each frame plane (float32 bytes), frame.frame on the test_frame synthetic scene",
           "frames": render_all()}
    HASH_FILE.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print("wrote", HASH_FILE, len(out["frames"]), "cases")
