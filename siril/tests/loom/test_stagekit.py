"""stagekit: the failure type, the cache-skip marker, the image fingerprint, the unchanged-image guard and the Siril
stage runner, checked against recorded Loom values (tests/oracle/loom_mgc_slot.json) and a fake Siril (RecordingOps).
"""

from __future__ import annotations

import numpy as np
import pytest

from loomsiril.loom import stagekit
from loomsiril.loom.siril_ops import RecordingOps, quote_path

from . import synth


def test_skip_cache_marker_and_failure_type() -> None:
    assert stagekit.SKIP_CACHE == "loom-skip-cache"
    assert issubclass(stagekit.StageFailed, RuntimeError)


def test_format_signature_matches_loom_steps_image_signature(oracle) -> None:
    c = oracle("loom_mgc_slot")["cases"]
    for (mean, median, maximum), text in zip(c["signature_stats"], c["signature_text"]):
        assert stagekit.format_signature(mean, median, maximum) == text


def test_image_signature_uses_every_plane_in_float64() -> None:
    a = np.array([[0.2, 0.4], [0.6, 0.8]], dtype=np.float32)
    b = np.array([[0.1, 0.3], [0.5, 0.7]], dtype=np.float32)
    samples = np.concatenate([a.ravel(), b.ravel()]).astype(np.float64)
    expected = stagekit.format_signature(float(samples.mean()), float(np.median(samples)), float(samples.max()))
    assert stagekit.image_signature([a, b]) == expected
    # the same single plane alone is a different fingerprint
    assert stagekit.image_signature([a]) != expected


def test_image_signature_needs_a_plane() -> None:
    with pytest.raises(ValueError):
        stagekit.image_signature([])


def test_signature_sees_a_change_that_leaves_the_mean_alone() -> None:
    # same mean (0.375) on both, different median and maximum: a mean-only check would call these equal
    a = np.array([[0.25, 0.25], [0.25, 0.75]], dtype=np.float32)
    b = np.array([[0.125, 0.375], [0.375, 0.625]], dtype=np.float32)
    sa, sb = stagekit.image_signature([a]), stagekit.image_signature([b])
    assert sa.split("|")[0] == sb.split("|")[0]
    assert sa != sb


def test_unchanged_problem_matches_loom_graxpert_problem(oracle) -> None:
    c = oracle("loom_mgc_slot")["cases"]
    for (name, ran, before, after), text in zip(c["problem_inputs"], c["problem_text"]):
        assert stagekit.unchanged_problem(name, ran, before, after) == text


def test_unchanged_problem_substitutes_the_tool_name() -> None:
    msg = stagekit.unchanged_problem("L", True, "1|1|1", "1|1|1", tool="Siril")
    assert msg is not None and msg.startswith("Siril did not change L: ")
    assert "GraXpert" not in msg
    msg = stagekit.unchanged_problem("L", False, "1|1|1", "2|1|1", tool="Siril")
    assert msg is not None and msg.startswith("Siril failed on L: ")


def test_run_siril_stage_transcript_and_saved_path(tmp_path) -> None:
    src = synth.write_master(tmp_path / "in.fits", synth.make_master("L", w=64, h=48))
    out = tmp_path / "out.fits"
    ops = RecordingOps(tmp_path)
    saved = stagekit.run_siril_stage(ops, str(src), str(out), lambda o: o.subsky(2, 20, 1.0))
    assert saved == str(out)
    assert ops.transcript == [f"load {quote_path(str(src))}", "subsky 2 -samples=20 -tolerance=1.0",
                              f"save {quote_path(str(out))}"]
    assert out.exists()


def test_run_siril_stage_without_guard_accepts_an_unchanged_image(tmp_path) -> None:
    synth.write_master(tmp_path / "in.fits", synth.make_master("L", w=64, h=48))
    ops = RecordingOps(tmp_path)
    assert stagekit.run_siril_stage(ops, "in.fits", "out.fits", lambda o: None) == "out.fits"


def test_guard_raises_when_the_handler_leaves_the_pixels_unchanged(tmp_path) -> None:
    synth.write_master(tmp_path / "in.fits", synth.make_master("L", w=64, h=48))
    ops = RecordingOps(tmp_path)
    with pytest.raises(stagekit.StageFailed) as err:
        stagekit.run_siril_stage(ops, "in.fits", "out.fits", lambda o: o.subsky(2, 20, 1.0),
                                 guard_name="Siril background extraction")
    assert str(err.value) == ("Siril background extraction did not change the image: Siril reported success but the "
                              "image is identical to the one it was given (its own output is in the console just "
                              "above)")
    assert not (tmp_path / "out.fits").exists()


def test_guard_passes_when_the_handler_changes_the_pixels(tmp_path) -> None:
    synth.write_master(tmp_path / "in.fits", synth.make_master("L", w=64, h=48))

    def handler(ops: RecordingOps, degree: int, samples: int, tolerance: float) -> None:
        ops.set_pixels([p - 0.01 for p in ops.pixels()])

    ops = RecordingOps(tmp_path, handlers={"subsky": handler})
    saved = stagekit.run_siril_stage(ops, "in.fits", "out.fits", lambda o: o.subsky(2, 20, 1.0),
                                     guard_name="Siril background extraction")
    assert saved == "out.fits"
    assert ops.transcript[-1] == 'save "out.fits"'
