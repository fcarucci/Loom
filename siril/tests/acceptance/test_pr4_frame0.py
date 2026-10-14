"""Frame 0 of a 1:1 render equals the screen composite of the inputs within 1/65535."""

from __future__ import annotations

import numpy as np


from acc_helpers import cli_args, combined, frame_files, prepare_inputs, read_planes, run_cli


def test_frame0_equals_input_composite(tmp_path) -> None:
    import synth

    # 1920x1080 is youtube_1080's own size, so the preset crop is the whole image: a 1:1 render.
    field = synth.make_field(w=1920, h=1080, n_stars=12, nc=1)
    paths = prepare_inputs(tmp_path, field)
    out = tmp_path / "out"
    proc = run_cli(tmp_path, cli_args(paths, out, field, duration="0.1", fps="10"), timeout=1800)
    assert proc.returncode == 0, combined(proc)
    files = frame_files(out)
    assert files, combined(proc)
    frame0 = read_planes(files[0])
    assert frame0.shape[:2] == (1080, 1920), "expected a 1:1 render of a 1080p input"

    starless = read_planes(paths["starless"])
    stars = read_planes(paths["stars"])
    expected = 1.0 - (1.0 - starless) * (1.0 - stars)  # screen(S, T) of the 16-bit inputs
    got = frame0[..., :1] if expected.shape[-1] == 1 else frame0
    if got.shape[-1] == 3 and expected.shape[-1] == 1:
        got = got[..., :1]
    diff = np.abs(got - expected).max()
    assert diff <= 1.0 / 65535.0 + 1e-9, f"max |frame0 - screen(S,T)| = {diff * 65535:.2f} / 65535"
