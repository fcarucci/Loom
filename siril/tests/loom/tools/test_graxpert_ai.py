"""GraXpert-AI adapter: argv text, the silent-failure guard, and the readiness checks (fake Siril, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from loomsiril.io_fits import read_fits
from loomsiril.loom import stagekit
from loomsiril.loom.siril_ops import RecordingOps, quote_path
from loomsiril.loom.tools import graxpert_ai
from loomsiril.loom.tools.resolver import Resolver

from .. import synth

SCRIPT_TEXT = 'import sys\nVERSION = "2.1.0"\n# GraXpert-AI Siril script\n'


def _scaled(ops: RecordingOps, factor: float) -> None:
    ops.set_pixels([p * factor for p in ops.pixels()])


def _input(tmp_path: Path) -> Path:
    synth.write_master(tmp_path / "in.fits", synth.make_master("L", w=64, h=48))
    return tmp_path / "in.fits"


def test_bge_args_are_the_measured_arguments() -> None:
    assert graxpert_ai.bge_args(0.5) == ["-bge", "-correction=subtraction", "-smoothing=0.50"]
    assert graxpert_ai.bge_args(0) == ["-bge", "-correction=subtraction", "-smoothing=0.00"]
    assert graxpert_ai.bge_args(1) == ["-bge", "-correction=subtraction", "-smoothing=1.00"]


@pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan"), float("inf")])
def test_bge_args_refuse_a_smoothing_outside_the_unit_interval(bad: float) -> None:
    with pytest.raises(ValueError):
        graxpert_ai.bge_args(bad)


def test_run_bge_sends_the_exact_pyscript_text_and_saves_the_changed_image(tmp_path: Path) -> None:
    src = _input(tmp_path)
    script = tmp_path / "GraXpert-AI.py"
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    out = tmp_path / "out.fits"
    seen: list[tuple] = []

    def handler(ops: RecordingOps, path, args) -> None:
        seen.append((path, tuple(args)))
        _scaled(ops, 0.9)

    ops = RecordingOps(tmp_path, handlers={"pyscript": handler})
    saved = graxpert_ai.run_bge(ops, str(script), str(src), str(out), 0.5, "L master")
    assert saved == str(out)
    assert seen == [(str(script), ("-bge", "-correction=subtraction", "-smoothing=0.50"))]
    assert ops.transcript == [
        f"load {quote_path(str(src))}",
        f'pyscript {quote_path(str(script))} -bge -correction=subtraction -smoothing=0.50',
        f"save {quote_path(str(out))}",
    ]
    planes, _ = read_fits(out)
    assert planes[0].shape == (48, 64)


def test_run_bge_logs_one_line_naming_the_image(tmp_path: Path) -> None:
    src = _input(tmp_path)
    lines: list[str] = []
    ops = RecordingOps(tmp_path, handlers={"pyscript": lambda o, p, a: _scaled(o, 0.9)})
    graxpert_ai.run_bge(ops, "GraXpert-AI.py", str(src), str(tmp_path / "o.fits"), 0.25, "R master", log=lines.append)
    assert lines == ["R master: GraXpert-AI -bge -correction=subtraction -smoothing=0.25"]


def test_unchanged_image_raises_with_the_graxpert_wording(tmp_path: Path) -> None:
    src = _input(tmp_path)
    out = tmp_path / "out.fits"
    ops = RecordingOps(tmp_path)  # the script runs but the pixels stay as they were
    with pytest.raises(stagekit.StageFailed) as err:
        graxpert_ai.run_bge(ops, "GraXpert-AI.py", str(src), str(out), 0.5, "L master")
    assert str(err.value).startswith("GraXpert did not change L master: ")
    assert "its own output is in the console just above" in str(err.value)
    assert not out.exists()


def test_failure_reported_through_siril_raises_with_the_graxpert_wording(tmp_path: Path) -> None:
    src = _input(tmp_path)
    ops = RecordingOps(tmp_path, fail={"pyscript": RuntimeError("siril refused")})
    with pytest.raises(stagekit.StageFailed) as err:
        graxpert_ai.run_bge(ops, "GraXpert-AI.py", str(src), str(tmp_path / "o.fits"), 0.5, "L master")
    assert str(err.value).startswith("GraXpert failed on L master: ")


def test_script_version_reads_the_text_only(tmp_path: Path) -> None:
    script = tmp_path / "GraXpert-AI.py"
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    assert graxpert_ai.script_version(script) == "2.1.0"
    assert graxpert_ai.script_version(tmp_path / "missing.py") is None
    other = tmp_path / "other.py"
    other.write_text("print('no version here')\n", encoding="utf-8")
    assert graxpert_ai.script_version(other) is None


def _resolver(tmp_path: Path, script: str | None, model: bool) -> Resolver:
    root = tmp_path / "root"
    data = root / "sample" / "user" / ".local" / "share"
    if script is not None:
        scripts = data / "siril-scripts" / "processing"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "GraXpert-AI.py").write_text(script, encoding="utf-8")
    if model:
        version = data / "GraXpert" / "bge-ai-models" / "1.0"
        version.mkdir(parents=True, exist_ok=True)
        (version / "model.onnx").write_bytes(b"not a model, only a presence check")
    return Resolver("linux", "/sample/user", {}, root=root)


def _all_present(name: str) -> object:
    """A find_spec that finds every package."""
    return object()


def test_readiness_is_empty_when_everything_is_present(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path, SCRIPT_TEXT, model=True)
    assert graxpert_ai.readiness(resolver, find_spec=_all_present) == []


def test_readiness_names_a_missing_script(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path, None, model=True)
    problems = graxpert_ai.readiness(resolver, find_spec=_all_present)
    assert len(problems) == 1
    assert problems[0].startswith("GraXpert-AI script not found")


def test_readiness_gives_the_reason_for_a_script_that_is_not_graxpert(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path, "print('something else')\n", model=True)
    problems = graxpert_ai.readiness(resolver, find_spec=_all_present)
    assert len(problems) == 1
    assert "not the GraXpert-AI script" in problems[0]


def test_readiness_asks_for_the_model_without_downloading_it(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path, SCRIPT_TEXT, model=False)
    problems = graxpert_ai.readiness(resolver, find_spec=_all_present)
    assert len(problems) == 1
    assert problems[0].startswith("GraXpert model missing in ")
    assert "run GraXpert once (or the script's model manager) to download the background-extraction model" in problems[0]


def test_readiness_names_each_missing_package_in_one_line(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path, SCRIPT_TEXT, model=True)

    def find_spec(name: str):
        return None if name in ("astropy", "PyQt6") else object()

    problems = graxpert_ai.readiness(resolver, find_spec=find_spec)
    assert len(problems) == 1
    assert problems[0].startswith("Python packages missing in Siril's Python environment: astropy, PyQt6;")
    assert "the first run of the script in Siril's GUI installs them" in problems[0]


def test_readiness_treats_a_broken_spec_as_missing(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path, SCRIPT_TEXT, model=True)

    def find_spec(name: str):
        if name == "cv2":
            raise ValueError("broken __spec__")
        return object()

    problems = graxpert_ai.readiness(resolver, find_spec=find_spec)
    assert problems == [
        "Python packages missing in Siril's Python environment: cv2; the first run of the script in Siril's GUI "
        "installs them"
    ]


def test_readiness_lists_every_problem(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path, None, model=False)
    problems = graxpert_ai.readiness(resolver, find_spec=lambda name: None)
    assert len(problems) == 3
    assert problems[0].startswith("GraXpert-AI script not found")
    assert problems[1].startswith("GraXpert model missing in ")
    assert problems[2].startswith("Python packages missing in Siril's Python environment: ")
