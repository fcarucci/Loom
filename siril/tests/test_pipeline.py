"""The render driver (E6: loomsiril/pipeline.py) on a synthetic scene.

Ports the intent of the JS checks fly.render (selftest.js 15241-15272: the preset key resolves, only our
frame files are cleared, frame i is a five-digit file, every frame is written and numbered from 0, a stale
frame is gone, without video the exact ffmpeg command is given, cancel keeps the frames and makes no video,
resume reuses frames with the same signature) and fly.working (15846-15850: SDR frames carry the sRGB
profile, HDR frames none). The scene is small (160 x 90); the frames are youtube_1080 (1920 x 1080).

Gaia is always a TSV written under tmp_path or a stub fetch: no test reaches the network.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("loomsiril.frame")

import tifffile

from loomsiril import encode, gaia, parallax, presets
from loomsiril import pipeline as P
from loomsiril.core import screen, tan_project, tan_unproject
from loomsiril.frame import RenderOptions
from loomsiril.frame import frame as render_frame
from loomsiril.io_tiff import write_tiff16

import synth  # tests/synth.py (on sys.path via conftest's rootdir)

ICC_TAG = 34675
COLS = ["ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP"]
PRESET = "youtube_1080"
#: IC 1396's catalogue position (the public fixture's field), used by the cluster checks.
IC1396 = {"ra": 324.745, "dec": 57.514}


def _write_tsv(path: Path, catalogue: list[dict[str, float]]) -> Path:
    """A Gaia TSV in the public fixture's shape (blanks for NaN)."""
    lines = ["\t".join(COLS)]
    for rec in catalogue:
        lines.append("\t".join("" if rec[c] != rec[c] else repr(float(rec[c])) for c in COLS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _tags(path: Path) -> set[int]:
    """The TIFF tag codes of a frame's first page."""
    with tifffile.TiffFile(str(path)) as tf:
        return {t.code for t in tf.pages[0].tags.values()}


def _job(tmp: Path, field: Any, **kw: Any) -> P.FlyJob:
    """A small job on a synthetic field: typed distance, a TSV Gaia sample, travel 0 (the driver's tests do not need motion)."""
    tsv = _write_tsv(tmp / "gaia.tsv", field.catalogue)
    base: dict[str, Any] = dict(
        starless=field.starless, stars=field.stars, wcs=field.wcs, target=None, distance=500.0,
        gaia=f"tsv:{tsv}", presets=[PRESET], duration=0.3, fps=10, out_dir=str(tmp / "out"),
        options={"travel": 0.0},
    )
    base.update(kw)
    return P.FlyJob(**base)


@pytest.fixture(scope="module")
def field() -> Any:
    return synth.make_field(w=160, h=90, n_stars=12, nc=3, seed=3)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory, field: Any) -> dict[str, Any]:
    """One SDR render of 3 frames, shared by the checks that only read its output."""
    tmp = tmp_path_factory.mktemp("pipe")
    job = _job(tmp, field)
    res = P.run(job)
    return {"tmp": tmp, "job": job, "res": res, "out": tmp / "out", "field": field}


# ---- the frame files, the record, the command --------------------------------------------------

def test_frames_are_numbered_five_digit_16_bit_and_complete(rendered: dict[str, Any]) -> None:
    folder = rendered["out"] / PRESET
    names = sorted(p.name for p in folder.glob("frame_*.tif"))
    assert names == [f"frame_{i:05d}.tif" for i in range(3)]
    assert rendered["res"].written == 3 and rendered["res"].reused == 0
    arr = tifffile.imread(str(folder / "frame_00000.tif"))
    assert arr.dtype == np.uint16
    assert arr.shape == (1080, 1920, 3)
    assert not list(folder.glob("*.part"))


def test_frames_json_records_the_signature(rendered: dict[str, Any]) -> None:
    record = (rendered["out"] / PRESET / P.FRAMES_RECORD).read_text(encoding="utf-8")
    assert json.loads(record)["renderer"] == presets.RENDERER_VERSION


def test_no_video_gives_the_exact_ffmpeg_command(rendered: dict[str, Any]) -> None:
    res = rendered["res"]
    assert res.videos == []
    assert len(res.commands) == 1
    folder = str(rendered["out"] / PRESET)
    video = str(rendered["out"] / encode.video_name("", PRESET, "sdr"))
    args = encode.ffmpeg_args(folder, 10, video, "h264", "high", None, None)
    expected = encode.command_line("ffmpeg", args, encode.platform_name(sys.platform))
    assert res.commands[0] == expected
    assert "frame_%05d.tif" in res.commands[0] and "-framerate 10" in res.commands[0]


def test_sdr_frames_carry_the_srgb_profile(rendered: dict[str, Any]) -> None:
    for f in sorted((rendered["out"] / PRESET).glob("frame_*.tif")):
        assert ICC_TAG in _tags(f), f.name


def test_result_counts_and_distance(rendered: dict[str, Any]) -> None:
    res = rendered["res"]
    assert res.distance == 500.0 and res.distance_source == "typed"
    assert set(res.counts) == {"detected", "placed", "blended", "backdrop"}


def test_second_run_reuses_then_only_a_missing_frame_is_written(rendered: dict[str, Any]) -> None:
    folder = rendered["out"] / PRESET
    before = {f.name: f.stat().st_mtime_ns for f in folder.glob("frame_*.tif")}
    again = P.run(rendered["job"])
    assert (again.written, again.reused) == (0, 3)
    assert {f.name: f.stat().st_mtime_ns for f in folder.glob("frame_*.tif")} == before
    # resume: a frame removed from a finished render is the only one drawn again
    (folder / "frame_00001.tif").unlink()
    resumed = P.run(rendered["job"])
    assert (resumed.written, resumed.reused) == (1, 2)
    assert (folder / "frame_00001.tif").exists()


def test_stale_frames_are_cleared_and_other_files_kept(tmp_path: Path, field: Any) -> None:
    folder = tmp_path / "out" / PRESET
    folder.mkdir(parents=True)
    (folder / "frame_00099.tif").write_text("stale", encoding="utf-8")
    (folder / "notes.txt").write_text("keep", encoding="utf-8")
    res = P.run(_job(tmp_path, field))
    assert res.written == 3
    assert not (folder / "frame_00099.tif").exists()
    assert (folder / "notes.txt").exists()


# ---- cancel, HDR, galaxy, portrait, default distance ------------------------------------------

def test_cancel_after_two_frames_keeps_them_and_makes_no_video(tmp_path: Path, field: Any) -> None:
    state = {"done": 0}

    def progress(stage: str, done: int, total: int) -> None:
        if stage.startswith("Rendering"):
            state["done"] = done

    job = _job(tmp_path, field, video=True, ffmpeg="/nonexistent/ffmpeg")
    res = P.run(job, progress=progress, is_cancelled=lambda: state["done"] >= 2)
    folder = tmp_path / "out" / PRESET
    assert res.cancelled is True
    assert res.written == 2
    assert (folder / "frame_00000.tif").exists() and (folder / "frame_00001.tif").exists()
    assert not (folder / "frame_00002.tif").exists()
    assert res.videos == [] and res.failed == []


def test_hdr_frames_carry_no_profile(tmp_path: Path, field: Any) -> None:
    res = P.run(_job(tmp_path, field, dynamic="hdr", duration=0.1))
    assert res.written == 1
    frame = tmp_path / "out" / PRESET / "frame_00000.tif"
    assert ICC_TAG not in _tags(frame)
    assert tifffile.imread(str(frame)).dtype == np.uint16


def test_galaxy_is_a_fixed_backdrop(tmp_path: Path, field: Any) -> None:
    res = P.run(_job(tmp_path, field, target_type="galaxy", distance=None, duration=0.1))
    assert res.distance == math.inf
    assert res.distance_source == "galaxy"


def test_no_distance_found_is_an_error_asking_for_the_distance(tmp_path: Path, field: Any) -> None:
    """FlyThrough.findDistance leaves D null and the dialog refuses to render: no silent default (review F1)."""
    with pytest.raises(ValueError, match="give --distance"):
        P.run(_job(tmp_path, field, distance=None, duration=0.1))
    assert not (tmp_path / "out").exists()


def test_portrait_turns_a_landscape_preset_into_its_own_folder(tmp_path: Path, field: Any) -> None:
    res = P.run(_job(tmp_path, field, orientation="portrait", duration=0.1))
    assert res.written == 1
    frame = tmp_path / "out" / (PRESET + "_vertical") / "frame_00000.tif"
    assert tifffile.imread(str(frame)).shape == (1920, 1080, 3)


def test_validation_refuses_before_any_render(tmp_path: Path, field: Any) -> None:
    with pytest.raises(ValueError, match="too long"):
        P.run(_job(tmp_path, field, duration=600.0, fps=200))          # 120000 frames: five digits would collide
    with pytest.raises(ValueError, match="at most"):
        P.run(_job(tmp_path, field, duration=601.0))
    with pytest.raises(ValueError, match="unknown Gaia source"):
        P.run(_job(tmp_path, field, gaia="ftp://nowhere"))
    with pytest.raises(ValueError, match="unknown preset"):
        P.run(_job(tmp_path, field, presets=["no_such_preset"]))
    assert not (tmp_path / "out").exists()


# ---- the Gaia sources ----------------------------------------------------------------------------

def test_cluster_distance_on_ic1396(gaia_tsv_path: Path) -> None:
    sources = gaia.read_tsv(gaia_tsv_path)
    d = P.cluster_distance(sources, IC1396, 85 / 60)
    assert d is not None
    assert 870 <= d["distance"] <= 1000, d


def test_online_uses_the_injected_fetch_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: Any) -> None:
    """gaia 'online' asks the injected fetch (never the network) and caches the answer under tmp_path."""
    monkeypatch.setattr(gaia, "default_cache_dir", lambda: tmp_path / "cache")
    calls: list[str] = []
    header = "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag"

    def fetch(url: str) -> str:
        calls.append(url)
        rows = [f"{r['ra']!r},{r['dec']!r},{r['plx']!r},{r['pmra']!r},{r['pmdec']!r},{r['G']!r},{r['BP']!r},{r['RP']!r}"
                for r in field.catalogue if r["plx"] == r["plx"]]
        return "\n".join([header, *rows]) + "\n"

    job = _job(tmp_path, field, gaia="online", distance=500.0, out_dir=str(tmp_path / "out"), duration=0.1)
    res = P.run(job, fetch_text=fetch)
    assert calls and all(u.startswith("https://") for u in calls)
    assert res.written == 1
    assert any((tmp_path / "cache").rglob("*.csv"))


def test_build_scene_neighbours_share_the_placed_source_objects(field: Any) -> None:
    """starlayer matches by identity: the sources placed and the neighbours are the same dicts (E3d)."""
    proj = lambda ra, dec: tan_project(ra, dec, field.wcs)  # noqa: E731
    built = P.build_scene(P.SceneRequest(field.starless, field.stars, proj, field.catalogue,
                                         {"ra": 324.745, "dec": 57.514}, 500.0, None))
    assert built.scene.w == 160 and built.scene.h == 90
    assert built.counts["placed"] + built.counts["backdrop"] == len(built.scene.catalogue)
    assert built.counts["placed"] == len(built.scene.sprites)


# ---- the working copy, the split, the crossfade ----------------------------------------------

def test_working_copy_shrinks_a_wide_image_and_scales_the_wcs() -> None:
    w, h = 4000, 2250                       # wider than 3840 at 16:9: presets.working_scale < 1
    plane = np.full((h, w), 0.25, dtype=np.float32)
    wcs = {"crval1": 10.0, "crval2": 20.0, "crpix1": 2000.5, "crpix2": 1125.5, "cd": [-0.0005, 0, 0, 0.0005]}
    wk = P.working_copy([plane], [plane * 0.0], wcs, fwhm=4.0)
    f = presets.working_scale(w, h)
    assert wk["scale"] == pytest.approx(f) and f < 1
    out = wk["starless"][0]
    assert out.shape[1] == pytest.approx(w * f, abs=2)
    assert float(out.mean()) == pytest.approx(0.25, abs=1e-4)           # area average keeps a flat image flat
    assert wk["wcs"]["cd"][0] == pytest.approx(-0.0005 / f)
    assert wk["fwhm"] == pytest.approx(4.0 * f)


def test_working_copy_leaves_a_small_image_alone(field: Any) -> None:
    wk = P.working_copy(field.starless, field.stars, field.wcs)
    assert wk["scale"] == 1.0
    assert wk["starless"] is field.starless


def test_clamp_to_image_takes_the_minimum() -> None:
    rng = np.random.default_rng(7)
    image = [rng.uniform(0, 1, (20, 30)).astype(np.float32)]
    starless = [np.minimum(rng.uniform(0, 1, (20, 30)), 1.0).astype(np.float32)]
    got = P.clamp_to_image(image, starless)
    np.testing.assert_array_equal(got[0], np.minimum(starless[0], image[0]))


def test_loop_image_crossfades_into_the_pre_roll(field: Any) -> None:
    """Fly.loopImage: clean frames before the fade; over the fade, frame i mixes frame i's flight with the pre-roll."""
    built = P.build_scene(P.SceneRequest(field.starless, field.stars, lambda ra, dec: tan_project(ra, dec, field.wcs),
                                         field.catalogue, {"ra": 324.745, "dec": 57.514}, 500.0, None))
    sc = built.scene
    ro = RenderOptions(travel=0.0, easing="smoothstep", growth=0.15, backdrop_motion=0.4, star_quality="high",
                       twinkle=0.0, motion_blur=False, bloom=0.0, duration=1.0, frame_dt=0.1)
    w, h = 32, 18
    crop = presets.preset_crop(sc.w, sc.h, sc.tp.x, sc.tp.y, w, h)
    n, F = 6, 2
    pr = P.PresetRender(spec={"w": w, "h": h}, n=n, F=F, scene=sc, po={}, crop=crop, folder="", out_base="")
    cache: dict[str, Any] = {}
    for i in range(n - F):
        f = presets.loop_frame(i, n, F)
        got = P.loop_image(pr, i, ro, cache)
        want = render_frame(sc, f["a"], ro, w, h, crop)
        for g, e in zip(got, want):
            np.testing.assert_array_equal(g, e)
    last = presets.loop_frame(n - 1, n, F)
    got = P.loop_image(pr, n - 1, ro, cache)
    a = render_frame(sc, last["a"], ro, w, h, crop)
    b = cache["still"]                                                  # the pre-roll is frame 0 (any easing but linear)
    from loomsiril.scene import blend
    for g, e in zip(got, blend(a, b, last["alpha"])):
        np.testing.assert_allclose(g, e, atol=1e-6)


def test_clear_frames_removes_only_loom_files(tmp_path: Path) -> None:
    folder = tmp_path / PRESET
    folder.mkdir()
    (folder / "frame_00000.tif").write_text("x", encoding="utf-8")
    (folder / "frame_00001.tif.part").write_text("x", encoding="utf-8")
    (folder / P.FRAMES_RECORD).write_text("sig", encoding="utf-8")
    (folder / "notes.txt").write_text("keep", encoding="utf-8")
    assert P.clear_frames(str(tmp_path), count_only=True) == {"frames": 1, "folders": 1}
    assert (folder / "frame_00000.tif").exists()                        # counting deletes nothing
    assert P.clear_frames(str(tmp_path)) == {"frames": 1, "folders": 1}
    assert not (folder / "frame_00000.tif").exists()
    assert (folder / "notes.txt").exists()


# ---- characterization (R1-b, on the pre-split run): the distance chain, the exits, the notes -------------

def _tsv_free_online_fetch(field: Any, urls: list[str]) -> Any:
    """A stub online Gaia fetch: the synthetic catalogue in the online CSV shape, every call recorded."""
    header = "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag"

    def fetch(url: str) -> str:
        urls.append(url)
        rows = [f"{r['ra']!r},{r['dec']!r},{r['plx']!r},{r['pmra']!r},{r['pmdec']!r},{r['G']!r},{r['BP']!r},{r['RP']!r}"
                for r in field.catalogue if r["plx"] == r["plx"]]
        return "\n".join([header, *rows]) + "\n"

    return fetch


def test_distance_cluster_found_is_used(tmp_path: Path, field: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """run: no typed distance, a cluster found round the target -> its distance, source 'cluster'."""
    seen: dict[str, Any] = {}

    def fake_cluster(sources: Any, target: Any, radius_deg: float) -> dict[str, float]:
        seen["n"] = len(sources)
        seen["radius"] = radius_deg
        return {"distance": 900.0}

    monkeypatch.setattr(P, "cluster_distance", fake_cluster)
    res = P.run(_job(tmp_path, field, distance=None, duration=0.1))
    assert (res.distance, res.distance_source) == (900.0, "cluster")
    assert seen["n"] > 0 and seen["radius"] > 0
    assert res.written == 1


def test_distance_illuminator_from_the_tsv_is_used_without_a_fetch(tmp_path: Path, field: Any,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """run: no cluster, the illuminating star in the TSV sample -> its distance, source 'star', no online query."""
    monkeypatch.setattr(P, "cluster_distance", lambda *a: None)
    calls: list[int] = []

    def fake_illuminator(sources: Any, target: Any) -> dict[str, float]:
        calls.append(len(sources))
        return {"distance": 720.0}

    monkeypatch.setattr(parallax, "illuminator_distance", fake_illuminator)
    res = P.run(_job(tmp_path, field, distance=None, duration=0.1))
    assert (res.distance, res.distance_source) == (720.0, "star")
    assert len(calls) == 1


def test_distance_illuminator_from_the_injected_fetch_when_the_sample_has_none(tmp_path: Path, field: Any,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """run: no cluster, no star in the sample -> the online fetch round the target finds one, marked online."""
    monkeypatch.setattr(gaia, "default_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(P, "cluster_distance", lambda *a: None)
    urls: list[str] = []
    star: dict[str, Any] = {"distance": 650.0}
    calls: list[int] = []

    def fake_illuminator(sources: Any, target: Any) -> Any:
        calls.append(len(sources))
        return None if len(calls) == 1 else star

    monkeypatch.setattr(parallax, "illuminator_distance", fake_illuminator)
    job = _job(tmp_path, field, gaia="online", distance=None, duration=0.1)
    res = P.run(job, fetch_text=_tsv_free_online_fetch(field, urls))
    assert (res.distance, res.distance_source) == (650.0, "star")
    assert len(calls) == 2 and urls
    assert star.get("online") is True


def test_cancel_before_the_scene_returns_the_distance_and_no_frames(tmp_path: Path, field: Any) -> None:
    res = P.run(_job(tmp_path, field, duration=0.1), is_cancelled=lambda: True)
    assert res.cancelled is True
    assert (res.distance, res.distance_source) == (500.0, "typed")
    assert res.written == 0 and not (tmp_path / "out").exists()


def test_a_travel_past_nine_tenths_of_the_distance_is_capped_and_noted(tmp_path: Path, field: Any) -> None:
    res = P.run(_job(tmp_path, field, duration=0.1, options={"travel": 1000.0}))
    assert "travel capped at 0.9 x the distance" in res.notes
    assert res.notes.index("travel capped at 0.9 x the distance") < len(res.notes) - 1


def test_a_resampled_working_copy_is_noted_and_drops_detections(tmp_path: Path, field: Any,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(P.preset_mod, "working_scale", lambda w, h: 0.5)
    res = P.run(_job(tmp_path, field, duration=0.1, detections=[]))
    assert any(n.startswith("the image was resampled to its working size (x0.500)") for n in res.notes)
    assert res.written == 1


@pytest.mark.parametrize("change, text", [
    ({"target_type": "moon"}, "unknown target type"),
    ({"fps": 0}, "fps must be at least 1"),
    ({"loop": "spin"}, "unknown loop"),
    ({"orientation": "square"}, "unknown orientation"),
    ({"star_quality": "ultra"}, "unknown star quality"),
    ({"dynamic": "xdr"}, "unknown dynamic range"),
    ({"presets": []}, "no preset given"),
])
def test_each_job_value_is_checked_before_any_render(tmp_path: Path, field: Any, change: dict[str, Any],
                                                     text: str) -> None:
    with pytest.raises(ValueError, match=text):
        P.run(_job(tmp_path, field, **change))
    assert not (tmp_path / "out").exists()


def test_the_layers_must_match_in_channels_and_size(tmp_path: Path, field: Any) -> None:
    with pytest.raises(ValueError, match="same number of channels"):
        P.run(_job(tmp_path, field, stars=field.stars[:1]))
    with pytest.raises(ValueError, match="differ in size"):
        P.run(_job(tmp_path, field, stars=[p[:-1] for p in field.stars]))
    assert not (tmp_path / "out").exists()


def _fake_ffmpeg(tmp: Path, body: str) -> str:
    """A shell script standing in for ffmpeg: it runs ``body`` (the last argument is the video path)."""
    path = tmp / "fake-ffmpeg"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.mark.skipif(sys.platform == "win32", reason="a shell script stands in for ffmpeg")
def test_a_video_made_by_ffmpeg_is_reported(tmp_path: Path, field: Any) -> None:
    ff = _fake_ffmpeg(tmp_path, 'for last; do :; done\necho made > "$last"')
    res = P.run(_job(tmp_path, field, video=True, ffmpeg=ff, duration=0.1))
    assert res.failed == [] and res.commands == []
    assert len(res.videos) == 1 and os.path.exists(res.videos[0])


@pytest.mark.skipif(sys.platform == "win32", reason="a shell script stands in for ffmpeg")
def test_a_failed_ffmpeg_is_reported_with_its_output_and_command(tmp_path: Path, field: Any) -> None:
    ff = _fake_ffmpeg(tmp_path, 'echo "boom" 1>&2\nexit 3')
    res = P.run(_job(tmp_path, field, video=True, ffmpeg=ff, duration=0.1))
    assert res.videos == [] and len(res.failed) == 1
    failure = res.failed[0]
    assert set(failure) == {"video", "output", "command"}
    assert "boom" in failure["output"] and ff in failure["command"]
    assert not os.path.exists(failure["video"])


def test_clear_frames_removes_a_folder_it_empties(tmp_path: Path) -> None:
    folder = tmp_path / PRESET
    folder.mkdir()
    (folder / "frame_00000.tif").write_text("x", encoding="utf-8")
    (folder / P.FRAMES_RECORD).write_text("sig", encoding="utf-8")
    assert P.clear_frames(str(tmp_path)) == {"frames": 1, "folders": 1}
    assert not folder.exists()


# ---- the package exports: lazy, so that loomsiril.core does not drag the pipeline in (R1-b) -----------

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_importing_core_does_not_import_the_pipeline() -> None:
    code = ("import sys, loomsiril.core\n"
            "assert 'loomsiril.pipeline' not in sys.modules, 'core pulled in the pipeline'\n")
    subprocess.run([sys.executable, "-c", code], cwd=_repo_root(), check=True)


def test_package_run_and_job_types_are_the_pipeline_objects() -> None:
    import loomsiril

    assert loomsiril.run is P.run
    assert loomsiril.FlyJob is P.FlyJob and loomsiril.Result is P.Result
    assert {"run", "FlyJob", "Result"} <= set(loomsiril.__all__)


# Characterization (R3-a, recorded on fly/final@23c0860 before the _scene_key change): the scene key is the frame
# signature's sceneKey, so these literal digests pin its bytes (the planes, the JSON of wcs and target, repr(D), fwhm, count).
_SCENE_KEYS = [
    (
        {"crval1": 10.5, "crval2": -3.25, "crpix1": 4.0, "crpix2": 3.0, "cd": [-1e-4, 0.0, 0.0, 1e-4]},
        {"ra": 10.5, "dec": -3.25}, 500.0, None, 3,
        "852094931b1c6f0ea1fa22bc02bb9eebf2cb793ba6d648ac4eaba49bd18e7796",
    ),
    (
        {"crval1": 10.5, "crval2": -3.25, "crpix1": 4.0, "crpix2": 3.0, "cd": [-1e-4, 0.0, 0.0, 1e-4]},
        {"ra": 10.5, "dec": -3.25, "diameter": 12.0}, float("inf"), 2.5, 0,
        "db7ca288801aec290e0f2657880d9b45c87674be86c6ab0b20985539b0d64032",
    ),
    (
        {"crval1": 1.0}, {}, 123.456, 1.25, 0,
        "9d4e29202c6743fd636064384ff3aee90a023747f05be0e086811a30bd5c022d",
    ),
]


@pytest.mark.parametrize("wcs,target,D,fwhm,n,want", _SCENE_KEYS)
def test_scene_key_is_unchanged(wcs: dict, target: dict, D: float, fwhm: Any, n: int, want: str) -> None:
    rng = np.random.default_rng(3)
    a = rng.uniform(0, 1, (6, 8)).astype(np.float32)
    b = rng.uniform(0, 1, (6, 8)).astype(np.float32)
    assert P._scene_key([a, b], P.SceneTerms(wcs, target, D, fwhm, n)) == want


# ---- R3-a B1: an angle outside [0, 360) is the same sky position (Gaia centre normalised) ----------

@pytest.mark.parametrize("ra,queried", [(360.0, "CIRCLE('ICRS',0,"), (-10.0, "CIRCLE('ICRS',350,"),
                                        (370.0, "CIRCLE('ICRS',10,")])
def test_target_outside_zero_to_360_queries_the_wrapped_position(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  field: Any, ra: float, queried: str) -> None:
    """A target ra of 360, -10 or 370 with no typed distance runs its cluster query at the wrapped ra (no ValueError)."""
    monkeypatch.setattr(P, "cluster_distance", lambda *a: None)
    monkeypatch.setattr(parallax, "illuminator_distance", lambda *a: None)
    urls: list[str] = []
    job = _job(tmp_path, field, gaia="online", distance=None, target={"ra": ra, "dec": 57.514}, duration=0.1)
    with pytest.raises(ValueError, match="no distance found"):
        P.run(job, fetch_text=_tsv_free_online_fetch(field, urls))
    decoded = [urllib.parse.unquote(u) for u in urls]
    assert any(queried in d for d in decoded), (queried, decoded)


def test_negative_crval1_field_runs_and_queries_the_wrapped_centre(tmp_path: Path, field: Any) -> None:
    """A CRVAL1 of 360 less than the field's (fmod keeps the sign: BASE ran it) renders; the centre query is wrapped."""
    urls: list[str] = []
    wcs = dict(field.wcs, crval1=field.wcs["crval1"] - 360.0)
    job = _job(tmp_path, field, wcs=wcs, gaia="online", distance=500.0, duration=0.1)
    res = P.run(job, fetch_text=_tsv_free_online_fetch(field, urls))
    assert res.written >= 1 and urls
    assert all("CIRCLE(" in urllib.parse.unquote(u) for u in urls)
    for u in urls:
        ra = float(urllib.parse.unquote(u).split("CIRCLE('ICRS',")[1].split(",")[0])
        assert 0.0 <= ra < 360.0


# ---- the music under the video (the dialog's Music row) -------------------------------------------------------

def test_a_job_with_music_gives_the_ffmpeg_command_with_the_audio(tmp_path: Path, field: Any) -> None:
    """No ffmpeg is run: without one the exact command is returned; the music is its second input, cut to the clip."""
    job = _job(tmp_path, field, music_path="/m/song.mp3", music_fade=True)
    res = P.run(job)
    folder, video = str(tmp_path / "out" / PRESET), str(tmp_path / "out" / encode.video_name("", PRESET, "sdr"))
    audio = {"path": "/m/song.mp3", "fade": True, "duration": 3 / 10, "loop": False}
    args = encode.ffmpeg_args(folder, 10, video, "h264", "high", None, audio)
    assert res.commands == [encode.command_line("ffmpeg", args, encode.platform_name(sys.platform))]
    assert "-stream_loop -1 -i /m/song.mp3" in res.commands[0] and "afade=t=in:st=0:d=0.075" in res.commands[0]
    assert "-shortest" in res.commands[0]


def test_a_looping_clip_gives_the_music_crossfaded_end_into_start(tmp_path: Path, field: Any) -> None:
    job = _job(tmp_path, field, music_path="/m/song.mp3", loop="pingpong")
    res = P.run(job)
    assert "amix=inputs=2" in res.commands[0] and "afade=t=in:st=0:d=0.075" not in res.commands[0]
    clip = presets.frame_count(0.3, 10, True) / 10                 # there and back: the clip's own length
    audio = {"path": "/m/song.mp3", "fade": True, "duration": clip, "loop": True}
    args = encode.ffmpeg_args(str(tmp_path / "out" / PRESET), 10, str(tmp_path / "out" / encode.video_name("", PRESET, "sdr")),
                              "h264", "high", None, audio)
    assert res.commands == [encode.command_line("ffmpeg", args, encode.platform_name(sys.platform))]
