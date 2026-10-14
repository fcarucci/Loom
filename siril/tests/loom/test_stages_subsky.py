"""stages_subsky: the mgc slot when no gradient tool is chosen (Siril background extraction on broadband channels),
checked against the transcript a fake Siril records and against the stage keys recorded from Loom.
"""

from __future__ import annotations

import pytest

from loomsiril.loom import cache, levels
from loomsiril.loom.siril_ops import RecordingOps, SirilOpsError
from loomsiril.loom.stagekit import StageFailed
from loomsiril.loom.stages_subsky import mgc_params, run_mgc

from . import synth

CHANNELS = ("L", "H", "R", "S", "G", "O", "B")
GRADIENT_CONFIGS = {
    "no config": None,
    "empty config": {},
    "legacy checkbox off": {"useGraXpert": False},
    "legacy checkbox on": {"useGraXpert": True},
    "GraXpert": {"gradientTool": "GraXpert"},
    "Studio gradient": {"gradientTool": levels.GRADIENT_TOOL_STUDIO},
    "explicit none": {"gradientTool": "none"},
    "explicit none beats the checkbox": {"gradientTool": "none", "useGraXpert": True},
    "narrowband GraXpert": {"graxpertNarrowband": True},
    "narrowband GraXpert with GraXpert": {"graxpertNarrowband": True, "useGraXpert": True},
}
SUBSKY = {"tool": "subsky", "model": "poly", "degree": 2, "samples": 20, "tolerance": 1.0}


@pytest.mark.parametrize("name", list(GRADIENT_CONFIGS))
@pytest.mark.parametrize("channel", CHANNELS)
def test_mgc_params_for_every_channel_and_gradient_tool(name: str, channel: str) -> None:
    config = GRADIENT_CONFIGS[name]
    tool = levels.gradient_tool_of(config)
    params = mgc_params(config, channel)
    if channel in ("L", "R", "G", "B") and tool == levels.GRADIENT_TOOL_NONE:
        assert params == SUBSKY
    else:
        assert params is None


def test_mgc_params_key_order_is_the_cache_key_order() -> None:
    params = mgc_params(None, "L")
    assert params is not None
    assert list(params) == ["tool", "model", "degree", "samples", "tolerance"]


def test_run_mgc_transcript_and_saved_path(tmp_path) -> None:
    src = synth.write_master(tmp_path / "in.fits", synth.make_master("L", w=64, h=48, gradient=(0.04, 0.0, 0.0)))
    ops = RecordingOps(tmp_path, handlers={"subsky": lambda o, d, s, t: o.set_pixels([p * 0.5 for p in o.pixels()])})
    logged: list[str] = []
    saved = run_mgc(ops, str(src), str(tmp_path / "out.fits"), mgc_params(None, "L"), logged.append)
    assert saved == str(tmp_path / "out.fits")
    assert ops.transcript == [f'load "{src}"', "subsky 2 -samples=20 -tolerance=1.0", f'save "{tmp_path / "out.fits"}"']
    assert logged == ["mgc: subsky 2 -samples=20 -tolerance=1.0"]


def test_run_mgc_refuses_a_silent_no_op(tmp_path) -> None:
    src = synth.write_master(tmp_path / "in.fits", synth.make_master("R", w=64, h=48))
    ops = RecordingOps(tmp_path)  # subsky changes nothing
    with pytest.raises(StageFailed) as err:
        run_mgc(ops, str(src), str(tmp_path / "out.fits"), mgc_params(None, "R"), label="R")
    assert str(err.value) == ("R did not change the image: Siril reported success but the image is identical to the "
                              "one it was given (its own output is in the console just above)")
    assert not (tmp_path / "out.fits").exists()


def test_run_mgc_names_the_image_when_no_label_is_given(tmp_path) -> None:
    src = synth.write_master(tmp_path / "in.fits", synth.make_master("R", w=64, h=48))
    with pytest.raises(StageFailed) as err:
        run_mgc(RecordingOps(tmp_path), str(src), str(tmp_path / "out.fits"), mgc_params(None, "R"))
    assert str(err.value).startswith("image did not change the image: ")


def test_run_mgc_transcript_carries_non_default_parameters(tmp_path) -> None:
    src = synth.write_master(tmp_path / "in.fits", synth.make_master("G", w=64, h=48))
    params = dict(mgc_params(None, "G"), degree=3, samples=30, tolerance=2.0)
    ops = RecordingOps(tmp_path, handlers={"subsky": lambda o, d, s, t: o.set_pixels([p * 0.5 for p in o.pixels()])})
    logged: list[str] = []
    run_mgc(ops, str(src), str(tmp_path / "out.fits"), params, logged.append, label="G")
    assert ops.transcript[1] == "subsky 3 -samples=30 -tolerance=2.0"
    assert [c for c in ops.calls if c[0] == "subsky"] == [("subsky", (3, 30, 2.0), {})]
    assert logged == ["mgc: subsky 3 -samples=30 -tolerance=2.0"]


def test_run_mgc_reports_a_failed_command(tmp_path) -> None:
    src = synth.write_master(tmp_path / "in.fits", synth.make_master("G", w=64, h=48))
    ops = RecordingOps(tmp_path, fail={"subsky": RuntimeError("no stars")})
    with pytest.raises(SirilOpsError):
        run_mgc(ops, str(src), str(tmp_path / "out.fits"), mgc_params(None, "G"))


def test_stage_keys_match_loom_for_the_recorded_mgc_parameters(oracle) -> None:
    c = oracle("loom_mgc_slot")["cases"]
    keys = cache.build_stage_keys(c["key_source"], c["key_params_empty"])
    assert [{"stage": e["stage"], "key": e["key"]} for e in keys] == c["keys_empty"]
    keys = cache.build_stage_keys(c["key_source"], c["key_params_one"])
    assert [{"stage": e["stage"], "key": e["key"]} for e in keys] == c["keys_one"]


def test_subsky_params_give_another_key_and_the_same_stage_order(oracle) -> None:
    c = oracle("loom_mgc_slot")["cases"]
    loom_chain = cache.build_stage_keys(c["key_source"], c["key_params_empty"])
    params = dict(c["key_params_empty"])
    params["mgc"] = mgc_params(None, "L")
    chain = cache.build_stage_keys(c["key_source"], params)
    assert [e["stage"] for e in chain] == [e["stage"] for e in loom_chain]
    mgc_new = next(e for e in chain if e["stage"] == "mgc")
    mgc_loom = next(e for e in loom_chain if e["stage"] == "mgc")
    assert mgc_new["key"] != mgc_loom["key"]
    # the stages before mgc keep their keys; the ones after it follow the new mgc key
    assert chain[0]["key"] == loom_chain[0]["key"]
    assert chain[-1]["key"] != loom_chain[-1]["key"]


def test_absent_mgc_stage_is_skipped_by_build_stage_keys() -> None:
    params = {"solve": {}, "spfc": {"channel": "L"}, "graxpert": {"enabled": True}}
    chain = cache.build_stage_keys("src", params)
    assert [e["stage"] for e in chain] == ["solve", "spfc", "graxpert"]
