"""CLI end to end on a synthetic field (subprocess, black box)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest


from acc_helpers import (ICC_TAG, cli_args, combined, frame_files, prepare_inputs, read_planes, run_cli, tiff_tags)

PRESET = "youtube_1080"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    import synth

    tmp = tmp_path_factory.mktemp("cli")
    field = synth.make_field(w=320, h=180, n_stars=20, nc=3)
    paths = prepare_inputs(tmp, field)
    out = tmp / "out"
    proc = run_cli(tmp, cli_args(paths, out, field))
    return {"tmp": tmp, "field": field, "paths": paths, "out": out, "proc": proc}


def test_cli_exits_zero(rendered: dict[str, Any]) -> None:
    p = rendered["proc"]
    assert p.returncode == 0, combined(p)


def test_frames_numbered_from_zero_and_contiguous(rendered: dict[str, Any]) -> None:
    files = frame_files(rendered["out"])
    assert len(files) >= 2, combined(rendered["proc"])
    assert [f.name for f in files] == [f"frame_{i:05d}.tif" for i in range(len(files))]


def test_frames_are_16bit_and_read_back_with_one_shape(rendered: dict[str, Any]) -> None:
    files = frame_files(rendered["out"])
    shapes = set()
    for f in files:
        arr = read_planes(f)  # asserts uint16
        assert arr.shape[-1] == 3
        assert 0.0 <= arr.min() and arr.max() <= 1.0
        shapes.add(arr.shape)
    assert len(shapes) == 1
    h, w, _ = shapes.pop()
    assert w * 9 == h * 16, "youtube_1080 frames are 16:9"
    assert not any(p.name.endswith(".part") for p in (rendered["out"] / PRESET).iterdir())


def test_sdr_frames_carry_icc_profile(rendered: dict[str, Any]) -> None:
    for f in frame_files(rendered["out"]):
        assert ICC_TAG in tiff_tags(f), f"{f.name} has no ICC tag"


def test_frames_json_present_and_valid(rendered: dict[str, Any]) -> None:
    rec = rendered["out"] / PRESET / "frames.json"
    assert rec.is_file()
    json.loads(rec.read_text(encoding="utf-8"))


def test_second_run_reuses_frames(rendered: dict[str, Any]) -> None:
    out, tmp, field, paths = rendered["out"], rendered["tmp"], rendered["field"], rendered["paths"]
    before = {f.name: f.stat().st_mtime_ns for f in frame_files(out)}
    proc = run_cli(tmp, cli_args(paths, out, field))
    assert proc.returncode == 0, combined(proc)
    after = {f.name: f.stat().st_mtime_ns for f in frame_files(out)}
    assert before == after, "resume must not rewrite frames with an unchanged signature"


def test_hdr_frames_carry_no_icc_profile(tmp_path: Path) -> None:
    import synth

    field = synth.make_field(w=320, h=180, n_stars=20, nc=3)
    paths = prepare_inputs(tmp_path, field)
    out = tmp_path / "hdr"
    proc = run_cli(tmp_path, cli_args(paths, out, field, "--dynamic", "hdr", duration="0.2"))
    assert proc.returncode == 0, combined(proc)
    files = frame_files(out)
    assert files
    for f in files:
        read_planes(f)
        assert ICC_TAG not in tiff_tags(f)


def test_without_ffmpeg_the_exact_command_is_printed(tmp_path: Path) -> None:
    import synth

    field = synth.make_field(w=320, h=180, n_stars=20, nc=3)
    paths = prepare_inputs(tmp_path, field)
    out = tmp_path / "out"
    empty = tmp_path / "emptybin"
    empty.mkdir()
    proc = run_cli(tmp_path, cli_args(paths, out, field, "--video", duration="0.2"), env_extra={"PATH": str(empty)})
    text = combined(proc)
    assert proc.returncode == 0, text
    assert frame_files(out)
    assert "ffmpeg" in text
    assert "frame_%05d.tif" in text, "the printed command must name the frame pattern"
    assert "-framerate" in text or "-r " in text
    assert not list((out / PRESET).glob("*.mp4")) and not list((out / PRESET).glob("*.mov"))


@pytest.mark.ffmpeg
def test_video_is_encoded_when_ffmpeg_exists(tmp_path: Path) -> None:
    import synth

    assert shutil.which("ffmpeg")
    field = synth.make_field(w=320, h=180, n_stars=20, nc=3)
    paths = prepare_inputs(tmp_path, field)
    out = tmp_path / "out"
    proc = run_cli(tmp_path, cli_args(paths, out, field, "--video", duration="0.5", fps="10"))
    assert proc.returncode == 0, combined(proc)
    vids = [p for p in out.rglob("*") if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")]
    assert vids, combined(proc)
    assert all(v.stat().st_size > 0 for v in vids)


def test_missing_distance_exits_1_and_asks_for_it(tmp_path: Path) -> None:
    """No cluster/bright star on a synthetic field: like the JS, ask for --distance instead of guessing."""
    import synth

    field = synth.make_field(w=320, h=180, n_stars=20, nc=3)
    paths = prepare_inputs(tmp_path, field)
    out = tmp_path / "out"
    proc = run_cli(tmp_path, cli_args(paths, out, field, distance=False))
    text = combined(proc)
    assert proc.returncode == 1, text
    assert "--distance" in text
    assert "Traceback" not in text, text
    assert not frame_files(out)
