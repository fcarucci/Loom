"""ghs: the GHS stretch stage against a fake Siril (RecordingOps): transcripts, the linear companion, failures and the
cache-key parameters that come from levels.stretch_params.
"""

from __future__ import annotations

import numpy as np
import pytest

from loomsiril.io_fits import read_fits
from loomsiril.loom import cache, ghs, levels
from loomsiril.loom.siril_ops import RecordingOps, SirilOpsError, quote_path
from loomsiril.loom.stagekit import StageFailed

from . import synth

LINKED_CMD = 'autoghs -linked 0 4.5 -b=13 -hp=0.7 -lp=0'
UNLINKED_CMD = 'autoghs 0 4.5 -b=13 -hp=0.7 -lp=0'


def _ghs_params(linked: bool, keep_linear: bool = False) -> dict:
    config = {"stretch": True, "stretchMethod": "ghs", "keepLinear": keep_linear}
    params = levels.stretch_params(config, linked)
    assert params is not None
    return params


def _mono_input(tmp_path, name: str = "in.fits"):
    return synth.write_master(tmp_path / name, synth.make_master("L", w=64, h=48))


def _stretch_to_brighter(o: RecordingOps, cmd) -> None:
    o.set_pixels([np.clip(p * 2.0, 0.0, 1.0) for p in o.pixels()])


def test_linked_transcript(tmp_path) -> None:
    src = _mono_input(tmp_path)
    out = tmp_path / "out.fits"
    ops = RecordingOps(tmp_path)
    res = ghs.run_ghs_stretch(ops, str(src), str(out), _ghs_params(True), True, False)
    assert ops.transcript == [f"load {quote_path(str(src))}", LINKED_CMD, f"save {quote_path(str(out))}"]
    assert res == {"out": str(out), "linear": None}
    assert out.is_file()


def test_unlinked_transcript(tmp_path) -> None:
    src = _mono_input(tmp_path)
    out = tmp_path / "out.fits"
    ops = RecordingOps(tmp_path)
    res = ghs.run_ghs_stretch(ops, str(src), str(out), _ghs_params(False), False, False)
    assert ops.transcript[1] == UNLINKED_CMD
    assert ops.calls[1] == ("ghs", (("autoghs", "0", "4.5", "-b=13", "-hp=0.7", "-lp=0"),), {})
    assert res["out"] == str(out)


def test_stretch_writes_the_handler_result_to_the_output(tmp_path) -> None:
    src = _mono_input(tmp_path)
    out = tmp_path / "out.fits"
    ops = RecordingOps(tmp_path, handlers={"ghs": _stretch_to_brighter})
    ghs.run_ghs_stretch(ops, str(src), str(out), _ghs_params(False), False, False)
    before = read_fits(src)[0][0]
    after = read_fits(out)[0][0]
    np.testing.assert_allclose(after, np.clip(before * 2.0, 0.0, 1.0), rtol=0, atol=1e-6)


def test_keep_linear_copies_the_input_before_the_stretch(tmp_path) -> None:
    src = _mono_input(tmp_path)
    out = tmp_path / "out.fits"
    linear = tmp_path / "linear.fits"
    seen: dict = {}

    def handler(o: RecordingOps, cmd) -> None:
        seen["exists"] = linear.is_file()
        seen["bytes"] = linear.read_bytes() if linear.is_file() else b""
        _stretch_to_brighter(o, cmd)

    ops = RecordingOps(tmp_path, handlers={"ghs": handler})
    res = ghs.run_ghs_stretch(ops, str(src), str(out), _ghs_params(False, True), False, True, linear_path=str(linear))
    assert seen["exists"] is True
    assert seen["bytes"] == src.read_bytes()
    assert linear.read_bytes() == src.read_bytes()
    assert res == {"out": str(out), "linear": str(linear)}


def test_keep_linear_needs_a_linear_path(tmp_path) -> None:
    src = _mono_input(tmp_path)
    with pytest.raises(ValueError):
        ghs.run_ghs_stretch(RecordingOps(tmp_path), str(src), str(tmp_path / "out.fits"), _ghs_params(False, True),
                            False, True)


def test_params_must_name_the_ghs_method(tmp_path) -> None:
    src = _mono_input(tmp_path)
    mtf = {"target": 0.25, "linked": False, "keepLinear": False, "blackPoint": "clip-safe"}
    with pytest.raises(ValueError):
        ghs.run_ghs_stretch(RecordingOps(tmp_path), str(src), str(tmp_path / "out.fits"), mtf, False, False)


def test_ghs_failure_raises_stagefailed_and_writes_no_output(tmp_path) -> None:
    src = _mono_input(tmp_path)
    out = tmp_path / "out.fits"
    ops = RecordingOps(tmp_path, fail={"ghs": RuntimeError("autoghs: no solution")})
    with pytest.raises(StageFailed) as err:
        ghs.run_ghs_stretch(ops, str(src), str(out), _ghs_params(False), False, False)
    assert isinstance(err.value.__cause__, SirilOpsError)
    assert "autoghs: no solution" in str(err.value)
    assert not out.exists()
    assert not any(c.startswith("save") for c in ops.transcript)


def test_missing_output_raises_stagefailed(tmp_path) -> None:
    src = _mono_input(tmp_path)
    out = tmp_path / "out.fits"
    # save reports a path but writes nothing
    ops = RecordingOps(tmp_path, handlers={"save": lambda o, path: str(path)})
    with pytest.raises(StageFailed) as err:
        ghs.run_ghs_stretch(ops, str(src), str(out), _ghs_params(False), False, False)
    assert "wrote no output" in str(err.value)


def test_load_failure_raises_stagefailed(tmp_path) -> None:
    ops = RecordingOps(tmp_path, fail={"load": OSError("gone")})
    with pytest.raises(StageFailed):
        ghs.run_ghs_stretch(ops, str(tmp_path / "missing.fits"), str(tmp_path / "out.fits"), _ghs_params(True),
                            True, False)


def test_stretch_params_from_levels_key_differently_from_mas_shaped_params() -> None:
    source = "src-key"
    ghs_stage = _ghs_params(True)
    mas_stage = {"target": levels.STRETCH_SKY_TARGET, "linked": True, "keepLinear": False, "blackPoint": "clip-safe"}
    with_ghs = cache.build_stage_keys(source, {"stretchRGB": ghs_stage})
    with_mas = cache.build_stage_keys(source, {"stretchRGB": mas_stage})
    assert len(with_ghs) == len(with_mas) == 1
    assert with_ghs[0]["key"] != with_mas[0]["key"]
    assert ghs_stage["ghs"] == levels.GHS_PARAMS


def test_stretch_params_key_depends_on_linked_and_keep_linear() -> None:
    keys = {}
    for linked in (True, False):
        for keep in (True, False):
            chain = cache.build_stage_keys("src", {"stretchRGB": _ghs_params(linked, keep)})
            keys[(linked, keep)] = chain[0]["key"]
    assert len(set(keys.values())) == 4
