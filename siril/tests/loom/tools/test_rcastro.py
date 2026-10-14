"""Contract tests for the RC-Astro CLI adapter, run against fake executables (no vendor tool is ever started)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.tools.rcastro import (
    PROBE_TIMEOUT_SECONDS,
    RUN_TIMEOUT_SECONDS,
    RcAstro,
    ToolContext,
    build_argv,
    error_message,
    format_number,
)
from loomsiril.loom.tools.runner import (
    RunResult,
    ToolFailed,
    ToolNotRunnable,
    ToolTimeout,
    run_tool,
)
from loomsiril.loom.tools.specs import UNLICENSED_RC_ASTRO

from .fakebin import make_fake, read_argv

FIXTURES = Path(__file__).parent / "fixtures"
VARIANTS = ("v5", "v6", "static")


def _load_schemas(version: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"rcastro_schema_{version}.json").read_text(encoding="utf-8"))


SCHEMAS = {"v5": _load_schemas("v5"), "v6": _load_schemas("v6")}


def _schema(variant: str, sub: str) -> dict[str, Any]:
    if variant == "static":
        return {"schemaVersion": 5, "tool": sub, "license": {"valid": True}, "parameters": []}
    return SCHEMAS[variant][sub]


FLAGS = {
    "v5": {"ss": "--ss", "sn": "--sn", "ansp": "--ansp", "dn": "--dn", "dc": "--dc", "rd": "--rd"},
    "v6": {"ss": "--stars", "sn": "--sn", "ansp": "--auto-psf", "dn": "--dn", "dc": "--dc", "rd": "--detail-recovery"},
    # the static table has no detail flag for nxt: the detail amount is omitted
    "static": {"ss": "--ss", "sn": "--sn", "ansp": "--ansp", "dn": "--dn", "dc": "--dc", "rd": None},
}

# (row id, sub, adapter method, method args, expected parameter tokens for a variant's flags)
ROWS = [
    ("aberration", "bxt", "aberration", (),
     lambda f: ["--correct-only", f["ss"], "0", f["sn"], "0", f["ansp"]]),
    ("stars-and-detail", "bxt", "sharpen", (0.5, 0.25),
     lambda f: [f["ss"], "0.5", f["sn"], "0.25", f["ansp"]]),
    ("stars-only", "bxt", "sharpen", (0.5, 0.0),
     lambda f: [f["ss"], "0.5", f["sn"], "0", f["ansp"]]),
    ("detail-only", "bxt", "sharpen", (0.0, 0.25),
     lambda f: [f["ss"], "0", f["sn"], "0.25", f["ansp"]]),
    ("denoise", "nxt", "denoise", (0.4, 0.3),
     lambda f: [f["dn"], "0.4", f["dc"], "0.4"] + ([f["rd"], "0.3"] if f["rd"] else [])),
    ("star-removal", "sxt", "remove_stars", (), lambda f: []),
]


def _planes(n: int, h: int = 6, w: int = 5, offset: float = 0.0) -> list[np.ndarray]:
    base = np.linspace(0.0, 0.5, h * w, dtype=np.float32).reshape(h, w)
    return [np.clip(base + offset + 0.1 * i, 0.0, 1.0).astype(np.float32) for i in range(n)]


def _write_plate(path: Path, planes: list[np.ndarray], header: dict[str, Any] | None = None) -> Path:
    write_fits(path, planes, header=header or {}, bitpix=-32)
    return path


def _fits_bytes(scratch: Path, planes: list[np.ndarray], header: dict[str, Any] | None = None) -> bytes:
    target = scratch / f"bytes-{len(planes)}-{id(planes)}.fits"
    write_fits(target, planes, header=header or {}, bitpix=-32)
    return target.read_bytes()


def _ctx(work: Path, log: list[str] | None = None, progress: list[tuple[float, str]] | None = None) -> ToolContext:
    return ToolContext(
        work_dir=work,
        log=None if log is None else log.append,
        cancelled=lambda: False,
        on_progress=None if progress is None else (lambda pct, text: progress.append((pct, text))),
        platform="linux",
    )


class Recorder:
    """A runner that records every call, runs the real runner, and may answer the schema probe itself."""

    def __init__(self, probe_answer: dict[str, Any] | None = None, runner: Any = run_tool) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.probe_answer = probe_answer
        self._runner = runner

    def __call__(self, tool: str, argv: Any, **kwargs: Any) -> RunResult:
        self.calls.append((list(argv), dict(kwargs)))
        if self.probe_answer is not None and "-o" not in argv:
            return RunResult(returncode=0, stdout_lines=[json.dumps(self.probe_answer)])
        return self._runner(tool, argv, **kwargs)

    def probes(self) -> list[tuple[list[str], dict[str, Any]]]:
        return [c for c in self.calls if "-o" not in c[0]]

    def runs(self) -> list[tuple[list[str], dict[str, Any]]]:
        return [c for c in self.calls if "-o" in c[0]]


def _fake(folder: Path, schema: dict[str, Any] | None, *, output: bytes | None = None, exit_code: int = 0,
          stdout_lines: tuple[str, ...] = (), stderr_lines: tuple[str, ...] = ()) -> Path:
    lines = list(stdout_lines)
    if schema is not None:
        lines = [json.dumps(schema), *lines]
    return make_fake(
        folder,
        "rc-astro",
        exit_code=exit_code,
        stdout_lines=lines,
        stderr_lines=list(stderr_lines),
        write_files=None if output is None else {"-o": output},
    )


# ---- argv: every table row, from the v5 schema, the v6 schema and the static fallback ---------------------------------


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
def test_argv_matches_table(tmp_path: Path, variant: str, row: tuple) -> None:
    _name, sub, method, args, expected = row
    schema = _schema(variant, sub)
    fake_dir = tmp_path / "fake"
    exe = _fake(fake_dir, schema, output=_fits_bytes(tmp_path, _planes(1)))
    plate = _write_plate(tmp_path / "in.fits", _planes(1))
    out = tmp_path / "out.fits"
    adapter = RcAstro(exe, _ctx(tmp_path / "work"))

    getattr(adapter, method)(plate, out, *args)

    argv = read_argv(fake_dir, "rc-astro")[-1]
    assert argv[0] == sub
    depth = argv.index("--depth")
    assert argv[1:depth] == expected(FLAGS[variant])
    assert argv[depth:depth + 5] == ["--depth", "32F", "--overwrite", "--json", "-o"]
    assert argv[-1].endswith("input.fits")
    assert out.exists()


def test_detail_omitted_and_logged_when_schema_has_no_detail(tmp_path: Path) -> None:
    schema = {"schemaVersion": 5, "tool": "nxt", "license": {"valid": True}, "parameters": [
        {"name": "denoise", "flag": "--dn", "type": "number"},
        {"name": "denoise_color", "flag": "--dc", "type": "number"},
    ]}
    fake_dir = tmp_path / "fake"
    exe = _fake(fake_dir, schema, output=_fits_bytes(tmp_path, _planes(1)))
    log: list[str] = []
    adapter = RcAstro(exe, _ctx(tmp_path / "work", log=log))

    adapter.denoise(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits", 0.4, 0.3)

    argv = read_argv(fake_dir, "rc-astro")[-1]
    assert argv[:5] == ["nxt", "--dn", "0.4", "--dc", "0.4"]
    assert "0.3" not in argv
    assert [line for line in log if line.startswith("rc-astro nxt:")] == [
        "rc-astro nxt: no unambiguous detail parameter in the schema (candidates: none); the detail amount is omitted"
    ]


def test_probe_and_run_start_in_the_executable_folder(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", _schema("v5", "bxt"), output=_fits_bytes(tmp_path, _planes(1)))
    rec = Recorder()
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)

    adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits")

    assert [argv for argv, _ in rec.probes()] == [[str(exe), "bxt", "--json"]]
    assert len(rec.runs()) == 1
    for _argv, kwargs in rec.calls:
        assert Path(kwargs["cwd"]) == exe.parent
    assert rec.runs()[0][1]["timeout"] == RUN_TIMEOUT_SECONDS == 1800.0
    assert rec.probes()[0][1]["timeout"] == PROBE_TIMEOUT_SECONDS


def test_schema_is_probed_once_per_sub(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", _schema("v5", "bxt"), output=_fits_bytes(tmp_path, _planes(1)))
    rec = Recorder()
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    plate = _write_plate(tmp_path / "in.fits", _planes(1))

    adapter.sharpen(plate, tmp_path / "a.fits", 0.5, 0.25)
    adapter.aberration(plate, tmp_path / "b.fits")

    assert len(rec.probes()) == 1
    assert len(rec.runs()) == 2


# ---- schema refusals -----------------------------------------------------------------------------------------------


def test_schema_below_version_5_is_refused(tmp_path: Path) -> None:
    old = {"schemaVersion": 4, "tool": "bxt", "license": {"valid": True}, "parameters": []}
    exe = _fake(tmp_path / "bin", old)
    adapter = RcAstro(exe, _ctx(tmp_path / "work"))
    with pytest.raises(ToolNotRunnable, match=re.escape("rc-astro 2.5 or newer is needed")):
        adapter.schema("bxt")


def test_invalid_license_is_refused(tmp_path: Path) -> None:
    unlicensed = {"schemaVersion": 5, "tool": "bxt", "license": {"valid": False}, "parameters": []}
    exe = _fake(tmp_path / "bin", unlicensed)
    adapter = RcAstro(exe, _ctx(tmp_path / "work"))
    with pytest.raises(ToolNotRunnable, match=re.escape(UNLICENSED_RC_ASTRO)):
        adapter.schema("bxt")


def test_probe_exit_77_is_refused_with_the_licence_message(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", None, exit_code=77)
    adapter = RcAstro(exe, _ctx(tmp_path / "work"))
    with pytest.raises(ToolNotRunnable, match=re.escape(UNLICENSED_RC_ASTRO)):
        adapter.schema("bxt")


def test_run_exit_77_reports_the_licence_message(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", None, exit_code=77)
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    with pytest.raises(ToolFailed) as info:
        adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits")
    assert str(info.value) == UNLICENSED_RC_ASTRO


def test_last_error_event_is_the_message(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", None, exit_code=1, stdout_lines=(
        '{"type": "progress", "done": 10}',
        '{"type": "error", "message": "first problem"}',
        '{"type": "error", "message": "no stars found"}',
    ))
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    with pytest.raises(ToolFailed) as info:
        adapter.sharpen(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits", 0.5, 0.25)
    assert str(info.value) == "no stars found"


def test_failure_without_error_event_keeps_the_runner_message(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", None, exit_code=2, stderr_lines=("boom",))
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    with pytest.raises(ToolFailed, match="rc-astro failed with exit code 2"):
        adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits")


def test_timeout_from_the_runner_passes_through(tmp_path: Path) -> None:
    def timing_out(tool: str, argv: Any, **kwargs: Any) -> RunResult:
        if "-o" in argv:
            raise ToolTimeout(tool, kwargs["timeout"])
        return RunResult(returncode=0, stdout_lines=[json.dumps(_schema("v5", "bxt"))])

    adapter = RcAstro(tmp_path / "rc-astro", _ctx(tmp_path / "work"), runner=timing_out)
    with pytest.raises(ToolTimeout):
        adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits")


# ---- progress, output and exchange ---------------------------------------------------------------------------------


def test_ndjson_progress_reaches_on_progress(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", None, output=_fits_bytes(tmp_path, _planes(1)), stdout_lines=(
        '{"type": "progress", "done": 25}',
        '{"type": "progress", "done": 60, "stage": "sharpen"}',
    ))
    progress: list[tuple[float, str]] = []
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work", progress=progress), runner=rec)

    adapter.sharpen(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits", 0.5, 0.25)

    assert [pct for pct, _text in progress] == [25.0, 60.0]


def test_output_keeps_input_header_and_mono_channel(tmp_path: Path) -> None:
    header = {"OBJECT": "synthetic", "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 10.5, "CRPIX1": 2.0}
    colour = _planes(3, offset=0.05)
    exe = _fake(tmp_path / "bin", None, output=_fits_bytes(tmp_path, colour))
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    out = tmp_path / "out.fits"

    adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1), header), out)

    planes, out_header = read_fits(out)
    assert len(planes) == 1
    np.testing.assert_allclose(planes[0], colour[0], atol=1e-6)
    assert out_header["OBJECT"] == "synthetic"
    assert out_header["CTYPE1"] == "RA---TAN"
    assert out_header["CRVAL1"] == pytest.approx(10.5)


def test_colour_plate_keeps_three_channels(tmp_path: Path) -> None:
    colour = _planes(3, offset=0.02)
    exe = _fake(tmp_path / "bin", None, output=_fits_bytes(tmp_path, colour))
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    out = tmp_path / "out.fits"

    adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(3)), out)

    planes, _ = read_fits(out)
    assert len(planes) == 3
    for got, want in zip(planes, colour):
        np.testing.assert_allclose(got, want, atol=1e-6)


def test_exit_zero_without_output_is_an_error(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", None, exit_code=0)
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    out = tmp_path / "out.fits"
    with pytest.raises(ToolFailed, match="wrote no output file"):
        adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1)), out)
    assert not out.exists()


def test_unreadable_output_is_an_error(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", None, output=b"this is not a FITS file")
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    with pytest.raises(ToolFailed, match="could not be read"):
        adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits")


def test_fewer_output_channels_than_the_plate_is_an_error(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", None, output=_fits_bytes(tmp_path, _planes(1)))
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    with pytest.raises(ToolFailed, match="could not be read"):
        adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(3)), tmp_path / "out.fits")


def test_exchange_folder_is_removed_after_success_and_failure(tmp_path: Path) -> None:
    work = tmp_path / "work"
    exe_ok = _fake(tmp_path / "ok", None, output=_fits_bytes(tmp_path, _planes(1)))
    adapter = RcAstro(exe_ok, _ctx(work), runner=Recorder(probe_answer=_schema("v5", "bxt")))
    adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits")
    assert not any((work / "tools").iterdir())

    exe_bad = _fake(tmp_path / "bad", None, exit_code=3)
    adapter = RcAstro(exe_bad, _ctx(work), runner=Recorder(probe_answer=_schema("v5", "bxt")))
    with pytest.raises(ToolFailed):
        adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out2.fits")
    assert not any((work / "tools").iterdir())


# ---- helpers ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [(0, "0"), (0.0, "0"), (-0.0, "0"), (1.0, "1"), (0.25, "0.25"), (2.50000001, "2.5"), (0.12345, None), (10, "10")],
)
def test_format_number(value: float, text: str | None) -> None:
    if text is None:
        assert format_number(value) in ("0.1234", "0.1235")
    else:
        assert format_number(value) == text


@pytest.mark.parametrize("bad", [True, None, float("nan"), float("inf"), "0.5"])
def test_format_number_rejects_non_numbers(bad: Any) -> None:
    with pytest.raises(ValueError):
        format_number(bad)


def test_build_argv_skips_false_and_refuses_unknown_names() -> None:
    schema = {"parameters": []}
    argv = build_argv("bxt", {"correct_only": True, "auto_psf": False, "sharpen_stars": 0.5}, schema, "i", "o")
    assert argv[:4] == ["bxt", "--correct-only", "--ss", "0.5"]
    with pytest.raises(ValueError, match="no parameter 'mystery'"):
        build_argv("bxt", {"mystery": 1}, schema, "i", "o")


def test_error_message_rules() -> None:
    assert error_message(77, []) == UNLICENSED_RC_ASTRO
    assert error_message(1, ["not json", '{"type": "error", "message": "bad"}']) == "bad"
    assert error_message(1, ['{"type": "progress", "done": 5}']) is None
    assert error_message(1, ['{"type": "error"}']) is None


DETAIL_CASES = [
    # [detail_mask, detail_recovery]: two candidates, none named "detail": ambiguous, omitted
    ([{"name": "detail_mask", "flag": "--dm"}, {"name": "detail_recovery", "flag": "--rd"}], None),
    # [denoise_detail_hf, detail]: the exact name wins whatever the order
    ([{"name": "denoise_detail_hf", "flag": "--dhf"}, {"name": "detail", "flag": "--dt"}], "--dt"),
    # one numeric candidate is used
    ([{"name": "recover_detail", "flag": "--rd"}], "--rd"),
    # a lone non-numeric candidate is not a detail amount
    ([{"name": "detail_mode", "flag": "--dm", "type": "boolean"}], None),
    # a candidate without a flag cannot be used
    ([{"name": "detail", "type": "number"}], None),
]


@pytest.mark.parametrize(("params", "flag"), DETAIL_CASES)
def test_detail_parameter_rule(tmp_path: Path, params: list, flag: Any) -> None:
    schema = {"schemaVersion": 5, "tool": "nxt", "license": {"valid": True}, "parameters": [
        {"name": "denoise", "flag": "--dn", "type": "number"},
        {"name": "denoise_color", "flag": "--dc", "type": "number"},
        *params,
    ]}
    fake_dir = tmp_path / "fake"
    exe = _fake(fake_dir, schema, output=_fits_bytes(tmp_path, _planes(1)))
    log: list[str] = []
    adapter = RcAstro(exe, _ctx(tmp_path / "work", log=log))

    adapter.denoise(_write_plate(tmp_path / "in.fits", _planes(1)), tmp_path / "out.fits", 0.4, 0.3)

    argv = read_argv(fake_dir, "rc-astro")[-1]
    depth = argv.index("--depth")
    if flag is None:
        assert argv[1:depth] == ["--dn", "0.4", "--dc", "0.4"]
        assert [line for line in log if line.startswith("rc-astro nxt:") and "omitted" in line]
    else:
        assert argv[1:depth] == ["--dn", "0.4", "--dc", "0.4", flag, "0.3"]
        assert not [line for line in log if line.startswith("rc-astro nxt:")]


def test_schema_after_ndjson_info_line_is_read(tmp_path: Path) -> None:
    exe = make_fake(tmp_path / "bin", "rc-astro", stdout_lines=[
        '{"type": "info", "message": "starting"}', json.dumps(_schema("v5", "bxt")),
    ])
    adapter = RcAstro(exe, _ctx(tmp_path / "work"))
    assert adapter.schema("bxt")["schemaVersion"] == 5


def test_non_numeric_schema_version_is_unreadable(tmp_path: Path) -> None:
    bad = {"schemaVersion": "6", "tool": "bxt", "license": {"valid": True}, "parameters": []}
    adapter = RcAstro(_fake(tmp_path / "bin", bad), _ctx(tmp_path / "work"))
    with pytest.raises(ToolNotRunnable, match="unreadable"):
        adapter.schema("bxt")


def test_failed_probe_reports_its_last_error_event(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", None, exit_code=1, stdout_lines=(
        '{"type": "error", "message": "earlier"}',
        '{"type": "error", "message": "probe broke"}',
    ))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"))
    with pytest.raises(ToolNotRunnable, match="probe broke"):
        adapter.schema("bxt")


def test_exchange_file_carries_the_plate_header(tmp_path: Path) -> None:
    header = {"OBJECT": "synthetic", "CTYPE1": "RA---TAN", "CRVAL1": 10.5}
    seen: dict[str, Any] = {}

    def capture(tool: str, argv: Any, **kwargs: Any) -> RunResult:
        if "-o" in argv:
            seen["header"] = read_fits(argv[-1])[1]
            seen["rows"] = read_fits(argv[-1])[0]
        return run_tool(tool, argv, **kwargs)

    exe = _fake(tmp_path / "bin", None, output=_fits_bytes(tmp_path, _planes(1)))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=Recorder(probe_answer=_schema("v5", "bxt"), runner=capture))
    adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1), header), tmp_path / "out.fits")

    assert seen["header"]["OBJECT"] == "synthetic"
    assert seen["header"]["CTYPE1"] == "RA---TAN"
    assert seen["header"]["CRVAL1"] == pytest.approx(10.5)
    np.testing.assert_allclose(seen["rows"][0], _planes(1)[0], atol=1e-6)


def test_top_down_result_rows_are_put_back_in_plate_order(tmp_path: Path) -> None:
    plate = _planes(1)[0]
    # a result whose rows are stored top-down: row 0 of the file is the top row of the plate
    top_down = _fits_bytes(tmp_path, [plate[::-1].copy()], header={"ROWORDER": "TOP-DOWN"})
    exe = _fake(tmp_path / "bin", None, output=top_down)
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=Recorder(probe_answer=_schema("v5", "bxt")))
    out = tmp_path / "out.fits"

    adapter.aberration(_write_plate(tmp_path / "in.fits", [plate]), out)

    np.testing.assert_allclose(read_fits(out)[0][0], plate, atol=1e-6)


def test_argv_row_order_is_the_table_order(tmp_path: Path) -> None:
    exe = _fake(tmp_path / "bin", _schema("v5", "bxt"), output=_fits_bytes(tmp_path, _planes(1)))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"))
    plate = _write_plate(tmp_path / "in.fits", _planes(1))
    adapter.sharpen(plate, tmp_path / "a.fits", 0.5, 0.25)
    argv = read_argv(tmp_path / "bin", "rc-astro")[-1]
    assert argv[:6] == ["bxt", "--ss", "0.5", "--sn", "0.25", "--ansp"]


def test_stdout_path_to_another_fits_is_ignored(tmp_path: Path) -> None:
    decoy = _write_plate(tmp_path / "decoy.fits", _planes(1, offset=0.3))
    exe = _fake(tmp_path / "bin", None, exit_code=0, stdout_lines=(str(decoy),))
    rec = Recorder(probe_answer=_schema("v5", "bxt"))
    adapter = RcAstro(exe, _ctx(tmp_path / "work"), runner=rec)
    out = tmp_path / "out.fits"
    with pytest.raises(ToolFailed, match="wrote no output file"):
        adapter.aberration(_write_plate(tmp_path / "in.fits", _planes(1)), out)
    assert not out.exists()
