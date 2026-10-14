"""Legacy SyQon adapters: argument lists against the recorded oracle, the Starless model and TIFF exchange, the
no-output and non-zero-exit rules, the temporary stretch round trips with a copying fake, and the timeouts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.tools import syqon_legacy as sl
from loomsiril.loom.tools.exchange import write_exchange_tiff
from loomsiril.loom.tools.runner import ToolError
from loomsiril.loom.tools.syqon_legacy import ToolContext, ToolNoOutput
from loomsiril.loom.tools.tempstretch import prism_mtf_target

from .fakebin import make_fake, read_argv

IN = "/in/plate.fits"
OUT = "/out/plate_parallax.fits"
INFO = "/info/parallax.json"


def _ctx(tmp_path: Path, log: list[str] | None = None, platform: str = "linux") -> ToolContext:
    return ToolContext(
        work_dir=tmp_path / "work",
        log=(log.append if log is not None else None),
        cancelled=lambda: False,
        on_progress=None,
        platform=platform,
    )


def _fits_bytes(tmp_path: Path, planes: list[np.ndarray]) -> bytes:
    path = tmp_path / "_scratch.fits"
    write_fits(path, planes)
    return path.read_bytes()


def _tiff_bytes(tmp_path: Path, planes: list[np.ndarray]) -> bytes:
    path = tmp_path / "_scratch.tif"
    write_exchange_tiff(path, planes)
    return path.read_bytes()


def _plate(tmp_path: Path, planes: list[np.ndarray], name: str = "plate.fits") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_fits(path, planes, header={"OBJECT": "M42"})
    return path


def _synthetic(nch: int, h: int = 12, w: int = 16, lo: float = 0.05, hi: float = 0.9) -> list[np.ndarray]:
    """Deterministic float32 planes in [lo, hi] with a clear median (no RNG)."""
    rng = np.random.default_rng(7)
    return [
        (lo + (hi - lo) * rng.random((h, w))).astype(np.float32) for _ in range(nch)
    ]


def _copy_tool(folder: Path, name: str, in_flag: str, out_flag: str) -> Path:
    """A fake executable that records its arguments (as make_fake does) and copies input to output."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / name
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, shutil, sys\n"
        "a = sys.argv[1:]\n"
        f"with open({str(folder / (name + '.argv.jsonl'))!r}, 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(a) + '\\n')\n"
        f"shutil.copyfile(a[a.index({in_flag!r}) + 1], a[a.index({out_flag!r}) + 1])\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _spy_run_tool(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Wrap the module's run_tool so each call's keyword arguments are recorded; the real runner still runs."""
    calls: list[dict[str, Any]] = []
    real = sl.run_tool

    def spy(tool: str, argv: Any, **kwargs: Any) -> Any:
        calls.append({"tool": tool, **kwargs})
        return real(tool, argv, **kwargs)

    monkeypatch.setattr(sl, "run_tool", spy)
    return calls


# --- argument lists and constants against the oracle --------------------------------------------------------------


def test_constants_match_oracle(oracle: Any) -> None:
    rec = oracle("loom_syqon_legacy")["cases"]["constants"]
    assert sl.PARALLAX_TIMEOUT_SECONDS * 1000 == rec["timeout_ms"] == 1200 * 1000
    assert sl.PARALLAX_TIMEOUT_SECONDS == 1200.0
    from loomsiril.loom.tools import tempstretch as ts

    assert ts.SYQON_MTF_TARGET == rec["mtf_target"]
    assert ts.SYQON_TILE_SIZE == rec["tile_size"]
    assert ts.SYQON_OVERLAP == rec["overlap"]
    assert ts.SYQON_PAD == rec["pad"]


def test_parallax_build_args_match_oracle(oracle: Any) -> None:
    cases = oracle("loom_syqon_legacy")["cases"]
    checked = 0
    for name, case in cases.items():
        if isinstance(case, list):
            entries = [(f"{name}[{i}]", c) for i, c in enumerate(case)]
        else:
            entries = [(name, case)]
        for label, entry in entries:
            if not (isinstance(entry, dict) and entry.get("stage") == "parallax"):
                continue
            settings: dict[str, Any] = {}
            if entry["amount"] is not None:
                settings["amount"] = entry["amount"]
            got = sl.build_args(
                "parallax", entry["operation"], settings, IN, OUT, {"json_info": INFO}
            )
            assert got == entry["args"], label
            checked += 1
    assert checked == 4 * 2 + 1 + 5  # four levels for stars and detail, aberration, five amounts


def test_parallax_argv_for_each_operation() -> None:
    args = sl.build_args("parallax", "aberration", {}, IN, OUT, {"json_info": INFO})
    assert args == ["--i", IN, "--o", OUT, "--correct-aberration", "--tile", "512", "--overlap", "128",
                    "--pad", "512", "--json-info", INFO]
    stars = sl.build_args("parallax", "star_reduction", {"amount": 3}, IN, OUT)
    assert stars == ["--i", IN, "--o", OUT, "--star-reduction", "3", "--tile", "512", "--overlap", "128",
                     "--pad", "512"]
    sharpen = sl.build_args("parallax", "sharpen", {"amount": 0.8}, IN, OUT)
    assert "--sharpen" in sharpen and sharpen[sharpen.index("--sharpen") + 1] == "0.80"
    assert "--correct-aberration" not in sharpen and "--mode" not in sharpen
    with pytest.raises(ValueError):
        sl.build_args("parallax", "mode", {}, IN, OUT)


def test_starless_candidates_and_message_match_oracle(oracle: Any) -> None:
    cases = oracle("loom_syqon_legacy")["cases"]
    exe = "/Applications/SyQonStarless.app/Contents/MacOS/SyQonStarless"
    assert sl.starless_model_candidates(exe, "darwin") == cases["starless_candidates_darwin"]
    assert sl.starless_model_candidates("/opt/syqon/SyQonStarless", "linux") == cases["starless_candidates_linux"]
    assert sl.starless_no_output_message("plate", "  boom: no GPU \n") == cases["starless_message_stderr"]
    assert sl.starless_no_output_message("plate", "") == cases["starless_message_no_stderr"]
    assert sl.starless_no_output_message("plate", None) == cases["starless_message_null"]


# --- SyQon Starless ----------------------------------------------------------------------------------------------


def _starless_app(tmp_path: Path) -> Path:
    """A fake SyQon Starless inside an app-bundle layout (model in Resources), writing a 3-channel TIFF."""
    contents = tmp_path / "SyQonStarless.app" / "Contents"
    (contents / "Resources" / "axiom3.mlmodelc").mkdir(parents=True)
    planes = [np.full((12, 16), 0.1 * (c + 1), dtype=np.float32) for c in range(3)]
    return make_fake(contents / "MacOS", "SyQonStarless", write_files={"-o": _tiff_bytes(tmp_path, planes)})


def test_starless_argv_uses_model_and_auto_device(tmp_path: Path) -> None:
    exe = _starless_app(tmp_path)
    plate = _plate(tmp_path, _synthetic(1))
    sl.starless(exe, _ctx(tmp_path), plate, tmp_path / "out.fits")
    (argv,) = read_argv(exe.parent, "SyQonStarless")
    model = str(exe.parent.parent / "Resources" / "axiom3.mlmodelc")
    assert argv[0] == "-i" and argv[2] == "-o"
    assert argv[4:] == ["-c", "pixinsight", "-m", model, "-d", "Auto"]


def test_model_prefers_resources_then_beside_exe(tmp_path: Path) -> None:
    exe = _starless_app(tmp_path)
    contents = exe.parent.parent
    beside = contents / "MacOS" / "axiom3.mlmodelc"
    beside.mkdir()
    assert sl.starless_model_path(exe, "linux") == str(contents / "Resources" / "axiom3.mlmodelc")
    (contents / "Resources" / "axiom3.mlmodelc").rmdir()
    assert sl.starless_model_path(exe, "linux") == str(beside)


def test_missing_model_raises(tmp_path: Path) -> None:
    exe = make_fake(tmp_path / "bin", "SyQonStarless", write_files={})
    with pytest.raises(sl.ModelNotFound, match="SyQon Starless model not found"):
        sl.starless(exe, _ctx(tmp_path), _plate(tmp_path, _synthetic(1)), tmp_path / "out.fits")


def test_three_channel_result_takes_channel_zero_on_mono_plate(tmp_path: Path) -> None:
    ch = [np.full((12, 16), 0.2 * (c + 1), dtype=np.float32) + np.linspace(0, 0.05, 16, dtype=np.float32)
          for c in range(3)]
    contents = tmp_path / "SyQonStarless.app" / "Contents"
    (contents / "Resources" / "axiom3.mlmodelc").mkdir(parents=True)
    exe = make_fake(contents / "MacOS", "SyQonStarless", write_files={"-o": _tiff_bytes(tmp_path, ch)})
    plate = _plate(tmp_path, _synthetic(1))
    out = tmp_path / "out.fits"
    sl.starless(exe, _ctx(tmp_path), plate, out)
    planes, header = read_fits(out)
    assert len(planes) == 1
    np.testing.assert_allclose(planes[0], ch[0], atol=1e-6)
    assert header.get("OBJECT") == "M42"


def test_starless_nonzero_exit_with_output_is_accepted_and_logged(tmp_path: Path) -> None:
    contents = tmp_path / "SyQonStarless.app" / "Contents"
    (contents / "Resources" / "axiom3.mlmodelc").mkdir(parents=True)
    tiff = _tiff_bytes(tmp_path, [np.zeros((12, 16), np.float32)] * 3)
    exe = make_fake(contents / "MacOS", "SyQonStarless", exit_code=4, write_files={"-o": tiff},
                    stderr_lines=["warning: something"])
    log: list[str] = []
    out = tmp_path / "out.fits"
    sl.starless(exe, _ctx(tmp_path, log), _plate(tmp_path, _synthetic(1)), out)
    assert out.exists()
    assert any("exit 4" in line for line in log)


def test_starless_no_output_raises_loom_message(tmp_path: Path) -> None:
    contents = tmp_path / "SyQonStarless.app" / "Contents"
    (contents / "Resources" / "axiom3.mlmodelc").mkdir(parents=True)
    exe = make_fake(contents / "MacOS", "SyQonStarless", exit_code=1, stderr_lines=["boom"])
    out = tmp_path / "out.fits"
    with pytest.raises(ToolNoOutput) as info:
        sl.starless(exe, _ctx(tmp_path), _plate(tmp_path, _synthetic(1, h=12, w=16), "plate.fits"), out)
    assert str(info.value) == "SyQon Starless produced no output for plate stderr: boom"
    assert not out.exists()
    assert not any((tmp_path / "work" / "tools").iterdir())  # exchange folder removed


def test_unreadable_starless_result_is_no_output(tmp_path: Path) -> None:
    contents = tmp_path / "SyQonStarless.app" / "Contents"
    (contents / "Resources" / "axiom3.mlmodelc").mkdir(parents=True)
    exe = make_fake(contents / "MacOS", "SyQonStarless", write_files={"-o": b"not a tiff"})
    with pytest.raises(ToolNoOutput, match="produced no output for plate"):
        sl.starless(exe, _ctx(tmp_path), _plate(tmp_path, _synthetic(1)), tmp_path / "out.fits")


# --- Prism and Parallax ------------------------------------------------------------------------------------------


def test_prism_argv_and_strength(tmp_path: Path) -> None:
    exe = _copy_tool(tmp_path / "bin", "prism_cli", "--input", "--output")
    sl.prism(exe, _ctx(tmp_path), _plate(tmp_path, _synthetic(1)), tmp_path / "out.fits", 0.85, True)
    (argv,) = read_argv(exe.parent, "prism_cli")
    assert argv[0] == "--input" and argv[2] == "--output"
    assert argv[4:] == [
        "--model-kind", "prism_deep", "--tile", "512", "--overlap", "128", "--pad", "512",
        "--strength", "0.85", "--use-amp", "--amp-dtype", "fp16",
    ]


def test_parallax_argv_has_json_info_and_no_mode(tmp_path: Path) -> None:
    exe = make_fake(tmp_path / "bin", "parallax_cli", write_files={"--o": _fits_bytes(tmp_path, _synthetic(1))})
    sl.parallax(exe, _ctx(tmp_path), _plate(tmp_path, _synthetic(1)), tmp_path / "out.fits",
                "sharpen", 0.4, False)
    (argv,) = read_argv(exe.parent, "parallax_cli")
    assert argv[0:2] == ["--i", argv[1]] and argv[2:4] == ["--o", argv[3]]
    assert argv[4:6] == ["--sharpen", "0.40"]
    assert "--json-info" in argv and "--mode" not in argv


@pytest.mark.parametrize(
    ("tool", "message"),
    [
        ("prism", "SyQon Prism produced no output for plate stderr: boom"),
        ("parallax", "SyQon Parallax star reduction failed on plate: no output file was produced. stderr: boom"),
    ],
)
def test_no_output_messages(tmp_path: Path, tool: str, message: str) -> None:
    name = "prism_cli" if tool == "prism" else "parallax_cli"
    exe = make_fake(tmp_path / "bin", name, exit_code=1, stderr_lines=["boom"])
    plate = _plate(tmp_path, _synthetic(1))
    out = tmp_path / "out.fits"
    with pytest.raises(ToolNoOutput) as info:
        if tool == "prism":
            sl.prism(exe, _ctx(tmp_path), plate, out, 0.85, True)
        else:
            sl.parallax(exe, _ctx(tmp_path), plate, out, "star_reduction", 3, False)
    assert str(info.value) == message
    assert not out.exists()


def test_nonzero_exit_with_parallax_output_is_accepted(tmp_path: Path) -> None:
    exe = make_fake(tmp_path / "bin", "parallax_cli", exit_code=3, stderr_lines=["spurious"],
                    write_files={"--o": _fits_bytes(tmp_path, _synthetic(1))})
    log: list[str] = []
    out = tmp_path / "out.fits"
    sl.parallax(exe, _ctx(tmp_path, log), _plate(tmp_path, _synthetic(1)), out, "aberration", 0.0, False)
    assert out.exists()
    assert any("exit 3" in line for line in log)


def _copy_round_trip(tmp_path: Path, nch: int, call: str, linked: bool = False, stretched: bool = True) -> tuple[
        list[np.ndarray], list[np.ndarray]]:
    if call == "prism":
        exe = _copy_tool(tmp_path / "bin", "prism_cli", "--input", "--output")
    else:
        exe = _copy_tool(tmp_path / "bin", "parallax_cli", "--i", "--o")
    planes = _synthetic(nch)
    plate = _plate(tmp_path, planes)
    out = tmp_path / "out.fits"
    if call == "prism":
        sl.prism(exe, _ctx(tmp_path), plate, out, 0.85, stretched)
    else:
        sl.parallax(exe, _ctx(tmp_path), plate, out, "sharpen", 0.8, linked)
    back, _ = read_fits(out)
    return planes, back


@pytest.mark.parametrize("nch", [1, 3])
@pytest.mark.parametrize("call", ["prism", "parallax"])
def test_stretch_round_trip_restores_the_plate(tmp_path: Path, nch: int, call: str) -> None:
    planes, back = _copy_round_trip(tmp_path, nch, call, linked=True, stretched=True)
    assert len(back) == nch
    for before, after in zip(planes, back):
        np.testing.assert_allclose(after, before, atol=1e-6)


def test_linked_only_for_colour_stretches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[tuple[int, float, bool]] = []
    real = sl.stretch_forward

    def spy(planes: Any, target: float, linked: bool) -> Any:
        seen.append((len(planes), target, linked))
        return real(planes, target, linked)

    monkeypatch.setattr(sl, "stretch_forward", spy)
    _copy_round_trip(tmp_path / "a", 3, "prism")
    _copy_round_trip(tmp_path / "b", 1, "prism")
    _copy_round_trip(tmp_path / "c", 3, "parallax", linked=True)
    _copy_round_trip(tmp_path / "d", 1, "parallax", linked=True)
    _copy_round_trip(tmp_path / "e", 3, "parallax", linked=False)
    assert [linked for _n, _t, linked in seen] == [True, False, True, False, False]


def test_stretch_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[float] = []
    real = sl.stretch_forward

    def spy(planes: Any, target: float, linked: bool) -> Any:
        seen.append(target)
        return real(planes, target, linked)

    monkeypatch.setattr(sl, "stretch_forward", spy)
    _copy_round_trip(tmp_path / "a", 1, "prism", stretched=True)
    assert seen[-1] == 0.15
    _copy_round_trip(tmp_path / "b", 1, "prism", stretched=False)
    assert seen[-1] == prism_mtf_target([np.asarray(p, dtype=np.float64) for p in _synthetic(1)])
    _copy_round_trip(tmp_path / "c", 1, "parallax")
    assert seen[-1] == 0.12


def test_runs_use_the_twenty_minute_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _spy_run_tool(monkeypatch)
    _copy_round_trip(tmp_path / "a", 1, "prism")
    _copy_round_trip(tmp_path / "b", 1, "parallax")
    assert [c["timeout"] for c in calls] == [1200.0, 1200.0]
    assert all(c["accept_nonzero"] is True for c in calls)


def test_exchange_folder_is_removed_after_a_run(tmp_path: Path) -> None:
    _copy_round_trip(tmp_path, 1, "parallax")
    tools = tmp_path / "work" / "tools"
    assert not tools.exists() or not any(tools.iterdir())


def test_sl_errors_are_tool_errors() -> None:
    assert issubclass(ToolNoOutput, ToolError)
    assert issubclass(sl.ModelNotFound, ToolError)


# --- review findings: prism target choice, header, Windows candidates, passthroughs, orientation ------------------


def _flat_plate(tmp_path: Path) -> list[np.ndarray]:
    """A linear plate near 0.02 whose measured PRISM target is not the 0.15 default."""
    rng = np.random.default_rng(1)
    return [(0.02 + 0.001 * rng.standard_normal((64, 64))).astype(np.float32)]


def test_prism_target_is_measured_only_for_linear_input(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    flat = _flat_plate(tmp_path)
    measured = prism_mtf_target([np.asarray(p, dtype=np.float64) for p in flat])
    assert measured == pytest.approx(0.2) and measured != 0.15
    seen: list[float] = []
    real = sl.stretch_forward

    def spy(planes: Any, target: float, linked: bool) -> Any:
        seen.append(target)
        return real(planes, target, linked)

    monkeypatch.setattr(sl, "stretch_forward", spy)
    exe = _copy_tool(tmp_path / "bin", "prism_cli", "--input", "--output")
    sl.prism(exe, _ctx(tmp_path), _plate(tmp_path / "lin", flat, "flat.fits"), tmp_path / "lin" / "a.fits", 0.85,
             False)
    assert seen[-1] == measured
    sl.prism(exe, _ctx(tmp_path), _plate(tmp_path / "st", flat, "flat.fits"), tmp_path / "st" / "b.fits", 0.85,
             True)
    assert seen[-1] == 0.15


def test_header_and_wcs_survive_prism_and_parallax(tmp_path: Path) -> None:
    planes = _synthetic(1)
    plate = tmp_path / "plate.fits"
    write_fits(plate, planes, header={"OBJECT": "M42", "CRVAL1": 83.8221, "CRVAL2": -5.3911})
    prism_exe = _copy_tool(tmp_path / "bin", "prism_cli", "--input", "--output")
    parallax_exe = _copy_tool(tmp_path / "bin", "parallax_cli", "--i", "--o")
    sl.prism(prism_exe, _ctx(tmp_path), plate, tmp_path / "p.fits", 0.85, True)
    sl.parallax(parallax_exe, _ctx(tmp_path), plate, tmp_path / "q.fits", "sharpen", 0.8, False)
    for out in (tmp_path / "p.fits", tmp_path / "q.fits"):
        _back, header = read_fits(out)
        assert header["OBJECT"] == "M42"
        assert header["CRVAL1"] == pytest.approx(83.8221)
        assert header["CRVAL2"] == pytest.approx(-5.3911)


def test_windows_model_candidates_have_no_bundle_fallback_and_one_separator_style() -> None:
    exe = r"C:\Apps\SyQon\SyQonStarless.exe"
    got = sl.starless_model_candidates(exe, "win32")
    assert got == [r"C:\Apps\Resources\axiom3.mlmodelc", r"C:\Apps\SyQon\axiom3.mlmodelc"]
    assert all("/" not in c for c in got)


def test_starless_round_trip_keeps_orientation(tmp_path: Path) -> None:
    contents = tmp_path / "SyQonStarless.app" / "Contents"
    (contents / "Resources" / "axiom3.mlmodelc").mkdir(parents=True)
    exe = _copy_tool(contents / "MacOS", "SyQonStarless", "-i", "-o")
    rows = np.linspace(0.05, 0.9, 12, dtype=np.float32)[:, None]
    plate_planes = [np.repeat(rows, 16, axis=1) + np.linspace(0, 0.02, 16, dtype=np.float32)[None, :]]
    plate = _plate(tmp_path, plate_planes)
    out = tmp_path / "out.fits"
    sl.starless(exe, _ctx(tmp_path), plate, out)
    back, _ = read_fits(out)
    np.testing.assert_allclose(back[0], plate_planes[0], atol=1e-6)


def test_progress_cwd_and_cancel_are_passed_to_the_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _spy_run_tool(monkeypatch)
    progress: list[tuple[float, str]] = []
    exe = make_fake(tmp_path / "bin", "parallax_cli", write_files={"--o": _fits_bytes(tmp_path, _synthetic(1))},
                    stdout_lines=["[ 50%] Sharpen (1/2)"])
    ctx = ToolContext(work_dir=tmp_path / "work", log=None, cancelled=lambda: False,
                      on_progress=lambda pct, text: progress.append((pct, text)), platform="linux")
    sl.parallax(exe, ctx, _plate(tmp_path, _synthetic(1)), tmp_path / "out.fits", "aberration", 0.0, False)
    assert progress and progress[0][0] == 50.0
    (call,) = calls
    assert call["cancelled"] is ctx.cancelled and call["on_progress"] is ctx.on_progress
    assert Path(call["cwd"]).parent == tmp_path / "work" / "tools"


def test_cancel_stops_the_tool_and_leaves_no_folder(tmp_path: Path) -> None:
    exe = make_fake(tmp_path / "bin", "parallax_cli", sleep=30.0)
    ctx = ToolContext(work_dir=tmp_path / "work", log=None, cancelled=lambda: True, on_progress=None,
                      platform="linux")
    from loomsiril.loom.tools.runner import ToolCancelled

    with pytest.raises(ToolCancelled):
        sl.parallax(exe, ctx, _plate(tmp_path, _synthetic(1)), tmp_path / "out.fits", "sharpen", 0.8, False)
    assert not any((tmp_path / "work" / "tools").iterdir())


@pytest.mark.parametrize(
    ("operation", "label"),
    [
        ("aberration", "aberration correction"),
        ("star_reduction", "star reduction"),
        ("sharpen", "detail sharpening"),
    ],
)
def test_parallax_labels_in_failure_messages(tmp_path: Path, operation: str, label: str) -> None:
    exe = make_fake(tmp_path / "bin", "parallax_cli", exit_code=1)
    with pytest.raises(ToolNoOutput) as info:
        sl.parallax(exe, _ctx(tmp_path), _plate(tmp_path, _synthetic(1)), tmp_path / "out.fits", operation, 3, False)
    assert str(info.value) == f"SyQon Parallax {label} failed on plate: no output file was produced."
