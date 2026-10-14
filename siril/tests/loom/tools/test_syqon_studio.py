"""The SyQon Studio adapter: argument building (oracle replay), exit messages, account refusal, the entitlement probe,
the Prism 2.0 flag, the Keychain notice, the declared output and the plate round trip. Every run uses a fake
executable or an injected runner; no real tool and no real wait."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.tools import syqon_studio as studio
from loomsiril.loom.tools.exchange import declared_output, sanitize_studio, write_exchange_fits
from loomsiril.loom.tools.runner import RunResult, ToolCancelled, ToolTimeout
from loomsiril.loom.tools.store import ToolStore
from loomsiril.loom.tools.syqon_studio import (
    KEYCHAIN_NOTE,
    PRISM2_FLAG,
    SIGNIN_WAIT_TEXT,
    StudioNoOutput,
    SyqonStudio,
    ToolContext,
    account_refused,
    build_args,
    domain_for,
    exit_message,
    failure_text,
    is_prism,
    is_prism2,
    model_label,
    model_role,
    prism2_flag_after_check,
    probe_problem,
)

from .fakebin import make_fake, read_argv

# --- the oracle inputs, named as in tools/oracle/loom_studio.js ---------------------------------------------------

MODELS = ["prism-essential", "prism-advanced", "prism-ultra", "prism-max", "parallax", "axiom", "deep-gradient"]
DOMAINS = ["linear", "nonlinear"]
SETTINGS: dict[str, dict[str, Any]] = {
    "plain": {"application": 1.0},
    "blend": {"application": 0.5},
    "correct": {"application": 1.0, "parallax": {"correction": True}},
    "stages": {"application": 1.0, "parallax": {"correction": False, "reduction": 3, "deblur": 0.5, "family": "aesthetics"}},
    "fractional": {"application": 0.8125, "parallax": {"correction": True, "reduction": 2.5, "deblur": 0.25, "family": "classic"}},
    "badFamily": {"application": 1.0, "parallax": {"correction": True, "reduction": 0, "deblur": 0, "family": "bogus"}},
}
REFUSAL_INPUTS: dict[str, tuple[int | None, str | None]] = {
    "code4": (4, ""),
    "licence": (5, "Secure license check failed for axiom"),
    "session": (0, "error: no secure SyQon session"),
    "upperCase": (1, "NO SECURE SYQON SESSION"),
    "other": (5, "inference failed"),
    "nothing": (None, None),
}
PROBE_INPUTS: dict[str, tuple[str, int | None, str | None]] = {
    "prismCode4": ("prism-advanced", 4, ""),
    "prismStderr": ("prism-max", 1, "secure license check failed"),
    "parallaxCode4": ("parallax", 4, ""),
    "axiomStderr": ("axiom", 5, "no secure SyQon session"),
    "deepNoCode": ("deep-gradient", None, "no secure SyQon session"),
    "ok": ("prism-essential", 0, ""),
    "inference": ("prism-ultra", 5, "boom"),
}
FAILURE_INPUTS: dict[str, tuple[str, int | None, str | None]] = {
    "code5Stderr": ("prism-max", 5, "  gpu error \n"),
    "noOutput": ("axiom", 0, ""),
    "code3NoErr": ("parallax", 3, None),
    "code9": ("deep-gradient", 9, "x"),
}
FLAG_INPUTS: dict[str, list[tuple[str, bool]]] = {
    "flag_empty": [],
    "flag_advanced_ran": [("prism-advanced", False)],
    "flag_max_refused": [("prism-max", True), ("prism-ultra", False)],
    "flag_ran_then_refused": [("prism-ultra", False), ("prism-advanced", True)],
    "flag_parallax_only": [("parallax", True)],
}


def test_build_args_replays_loom_oracle(oracle: Any) -> None:
    cases = oracle("loom_studio")["cases"]
    checked = 0
    for model in MODELS:
        for domain in DOMAINS:
            for name, settings in SETTINGS.items():
                expected = cases[f"args_{model}_{domain}_{name}"]
                got = build_args(model, domain, settings, "IN", "OUT")
                assert got == expected, (model, domain, name)
                checked += 1
    assert checked == len(MODELS) * len(DOMAINS) * len(SETTINGS)


def test_build_args_file_arguments_are_last(tmp_path: Path) -> None:
    args = build_args("axiom", "linear", {"application": 1.0}, tmp_path / "in.fits", tmp_path / "out.fits")
    assert args[-3:] == ["--overwrite", str(tmp_path / "in.fits"), str(tmp_path / "out.fits")]
    assert "--quiet" not in args and "--stars-output" not in args and "--gradient-output" not in args


def test_build_args_fixed_constants_and_tiling() -> None:
    prism = build_args("prism-essential", "linear", {"application": 1.0}, "i", "o")
    assert prism[:8] == ["--model", "prism-essential", "--domain", "linear", "--precision", "f32",
                         "--tile-size", "512"]
    assert prism[8:10] == ["--overlap", "64"]
    gradient = build_args("deep-gradient", "linear", {}, "i", "o")
    assert "--tile-size" not in gradient and "--application" not in gradient
    assert gradient == ["--model", "deep-gradient", "--domain", "linear", "--precision", "f32",
                        "--overwrite", "i", "o"]


@pytest.mark.parametrize(
    ("model", "stretched", "expected"),
    [
        ("deep-gradient", False, "linear"),
        ("deep-gradient", True, "linear"),
        ("parallax", True, "linear"),
        ("prism-advanced", True, "linear"),
        ("prism-essential", False, "linear"),
        ("prism-essential", True, "nonlinear"),
        ("axiom", False, "linear"),
        ("axiom", True, "nonlinear"),
        ("prism-ultra", False, "linear"),
        ("prism-ultra", True, "nonlinear"),
        ("prism-max", True, "nonlinear"),
    ],
)
def test_domain_rules(model: str, stretched: bool, expected: str) -> None:
    assert domain_for(model, stretched=stretched) == expected


def test_domain_rules_refuse_unknown_model() -> None:
    with pytest.raises(ValueError):
        domain_for("prism-nope", stretched=False)


def test_build_args_domain_is_passed_as_given() -> None:
    args = build_args("axiom", "nonlinear", {}, "i", "o")
    assert args[args.index("--axiom-stretch") + 1] == "identity"


@pytest.mark.parametrize(
    ("code", "text"),
    [
        (1, "General CLI error."),
        (2, "Invalid option or incompatible input domain."),
        (3, "Input/output path error (existence, permissions, or overwrite policy)."),
        (4, "Authentication or entitlement denied. Sign in through SyQon Studio and confirm this model is "
            "available to your account."),
        (5, "Inference failed; no output was produced."),
        (6, "Output encoding failed."),
        (7, "Output was saved, but the Studio handoff step failed."),
        (130, "Interrupted."),
        (0, "syqon-cli exited with code 0."),
        (9, "syqon-cli exited with code 9."),
    ],
)
def test_every_exit_code_message(code: int, text: str, oracle: Any) -> None:
    assert exit_message(code) == text
    assert exit_message(code) == oracle("loom_studio")["cases"][f"exit_{code}"]


@pytest.mark.parametrize("name", list(REFUSAL_INPUTS))
def test_account_refusal_matches_loom(name: str, oracle: Any) -> None:
    code, stderr = REFUSAL_INPUTS[name]
    assert account_refused(code, stderr) == oracle("loom_studio")["cases"][f"refused_{name}"]


@pytest.mark.parametrize("model", MODELS)
def test_model_helpers_match_loom(model: str, oracle: Any) -> None:
    cases = oracle("loom_studio")["cases"]
    assert model_role(model) == cases[f"role_{model}"]
    assert model_label(model) == cases[f"label_{model}"]
    assert is_prism2(model) == cases[f"prism2_{model}"]
    assert is_prism(model) == cases[f"prism_{model}"]


@pytest.mark.parametrize("name", list(PROBE_INPUTS))
def test_probe_problem_matches_loom(name: str, oracle: Any) -> None:
    model, code, stderr = PROBE_INPUTS[name]
    assert probe_problem(model, code, stderr) == oracle("loom_studio")["cases"][f"problem_{name}"]


def test_probe_problem_names_prism2_fallback() -> None:
    text = probe_problem("prism-max", 4, "")
    assert text is not None and "Until a check succeeds" in text and "SyQon Studio Prism Essential" in text
    assert probe_problem("prism-essential", 0, "") is None


@pytest.mark.parametrize("name", list(FAILURE_INPUTS))
def test_failure_text_matches_loom(name: str, oracle: Any) -> None:
    model, code, stderr = FAILURE_INPUTS[name]
    assert failure_text(model, code, stderr) == oracle("loom_studio")["cases"][f"failure_{name}"]


@pytest.mark.parametrize("name", list(FLAG_INPUTS))
def test_prism2_flag_after_check_matches_loom(name: str, oracle: Any) -> None:
    assert prism2_flag_after_check(FLAG_INPUTS[name]) == oracle("loom_studio")["cases"][name]


def test_oracle_recorded_every_case(oracle: Any) -> None:
    cases = oracle("loom_studio")["cases"]
    assert len(cases) == 146
    assert cases["family_bogus"] == "classic"


# --- declared output -------------------------------------------------------------------------------------------


def test_declared_output_prefers_last_existing_file(tmp_path: Path) -> None:
    default = tmp_path / "out.fits"
    first = tmp_path / "first.fits"
    last = tmp_path / "last.fits"
    for p in (first, last):
        p.write_bytes(b"x")
    stdout = f"progress 10%\n{first}\nnot a file\n{last}\n"
    assert declared_output(stdout, default) == str(last)


def test_declared_output_ignores_short_and_missing_lines(tmp_path: Path) -> None:
    default = tmp_path / "out.fits"
    assert declared_output(["ab", str(tmp_path / "gone.fits")], default) == str(default)
    assert declared_output(None, default) == str(default)


# --- sanitising ----------------------------------------------------------------------------------------------


def test_studio_sanitise_clips_and_reports_unit_scale(tmp_path: Path) -> None:
    plate = np.array([[np.nan, -0.5, 2.0], [np.inf, 0.25, 1e31]], dtype=np.float32)
    planes, scale = sanitize_studio([plate])
    assert scale == 1.0
    assert planes[0].tolist() == [[0.0, 0.0, 1.0], [0.0, 0.25, 0.0]]
    write_exchange_fits(tmp_path / "x.fits", [plate], sanitize="studio")


# --- the probe and the entitlement cache --------------------------------------------------------------------------


@pytest.fixture
def fake_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "system-temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(folder))
    return folder


@pytest.fixture
def store(tmp_path: Path, fake_temp: Path) -> ToolStore:
    return ToolStore(tmp_path / "config")


def _fits_bytes(tmp_path: Path, planes: list[np.ndarray], name: str = "fixture.fits") -> bytes:
    path = tmp_path / name
    write_fits(path, [np.asarray(p, dtype=np.float32) for p in planes], bitpix=-32)
    return path.read_bytes()


def _ctx(tmp_path: Path, platform: str = "linux", **kwargs: Any) -> ToolContext:
    return ToolContext(work_dir=tmp_path / "work", platform=platform, **kwargs)


def test_probe_refused_by_exit_code_sets_prism2_flag(tmp_path: Path, store: ToolStore) -> None:
    exe = make_fake(tmp_path / "bin", "syqon-cli", exit_code=4, stderr_lines=["Authentication required"])
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    entitled = studio_.check_entitlement(["prism-advanced", "parallax"])
    assert entitled == {"prism-advanced": False, "parallax": False}
    assert len(studio_.problems) == 2
    assert "Prism Deep Advanced" in studio_.problems[0]
    assert store.get_flag(PRISM2_FLAG) is True
    assert studio_.prism2_unavailable() is True


def test_probe_refused_by_licence_stderr(tmp_path: Path, store: ToolStore) -> None:
    exe = make_fake(tmp_path / "bin", "syqon-cli", exit_code=5, stderr_lines=["Secure license check failed"])
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    assert studio_.check_entitlement(["axiom"]) == {"axiom": False}
    assert studio_.problems and "Axiom V3" in studio_.problems[0]
    assert store.get_flag(PRISM2_FLAG) is None  # not a Prism 2.0 model: nothing stored


def test_successful_probe_is_cached_per_session_and_clears_prism2_flag(
    tmp_path: Path, store: ToolStore
) -> None:
    store.set_flag(PRISM2_FLAG, True)
    out_bytes = _fits_bytes(tmp_path, [np.zeros((64, 64), dtype=np.float32)])
    exe = make_fake(
        tmp_path / "bin",
        "syqon-cli",
        exit_code=0,
        write_files={"probe_out.fits": out_bytes},
    )
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    assert studio_.check_entitlement(["prism-advanced"]) == {"prism-advanced": True}
    assert store.get_flag(PRISM2_FLAG) is False
    assert len(read_argv(tmp_path / "bin", "syqon-cli")) == 1
    argv = read_argv(tmp_path / "bin", "syqon-cli")[0]
    assert argv[:6] == ["--model", "prism-advanced", "--domain", "linear", "--precision", "f32"]
    assert argv[argv.index("--application") + 1] == "1.0000"

    # the second check answers from the cache: no further run
    assert studio_.check_entitlement(["prism-advanced"]) == {"prism-advanced": True}
    assert len(read_argv(tmp_path / "bin", "syqon-cli")) == 1


def test_probe_without_output_is_inconclusive_and_changes_nothing(tmp_path: Path, store: ToolStore) -> None:
    exe = make_fake(tmp_path / "bin", "syqon-cli", exit_code=5, stderr_lines=["inference failed"])
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    assert studio_.check_entitlement(["prism-max"]) == {"prism-max": False}
    assert studio_.problems == []
    assert store.get_flag(PRISM2_FLAG) is None


def test_probe_timeout_is_inconclusive(tmp_path: Path, store: ToolStore) -> None:
    def runner(*args: Any, **kwargs: Any) -> RunResult:
        raise ToolTimeout("syqon-cli", 300)

    studio_ = SyqonStudio("/nonexistent/syqon-cli", _ctx(tmp_path), store, runner=runner)
    assert studio_.check_entitlement(["prism-ultra"]) == {"prism-ultra": False}
    assert store.get_flag(PRISM2_FLAG) is None


def test_probe_uses_the_probe_timeout(tmp_path: Path, store: ToolStore) -> None:
    seen: dict[str, Any] = {}

    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        seen.update(kwargs)
        return RunResult(returncode=0, stdout_lines=[], stderr_tail=[])

    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    studio_.check_entitlement(["parallax"])
    assert seen["timeout"] == 5 * 60


def test_prism2_flag_is_not_rewritten_when_unchanged(tmp_path: Path, store: ToolStore) -> None:
    store.set_flag(PRISM2_FLAG, True)
    writes: list[Any] = []
    original = store.set_flag

    def counting(name: str, value: Any) -> None:
        writes.append((name, value))
        original(name, value)

    store.set_flag = counting  # type: ignore[method-assign]

    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        return RunResult(returncode=4, stdout_lines=[], stderr_tail=[])

    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    studio_.check_entitlement(["prism-ultra"])
    assert writes == []


# --- the Keychain notice (macOS only, after 10 s of silence, once per session) -------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _silent_runner(clock: _FakeClock, seconds_per_poll: float, polls: int, emit_line: bool = False):  # type: ignore[no-untyped-def]
    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        if emit_line:
            kwargs["on_line"]("stdout", "starting")
        for _ in range(polls):
            clock.now += seconds_per_poll
            if kwargs["cancelled"]():
                raise ToolCancelled(tool)
        return RunResult(returncode=0, stdout_lines=[], stderr_tail=[])

    return runner


def test_keychain_notice_after_silence_on_macos(tmp_path: Path, store: ToolStore) -> None:
    clock = _FakeClock()
    logs: list[str] = []
    progress: list[tuple[float, str]] = []
    ctx = ToolContext(work_dir=tmp_path / "work", log=logs.append, on_progress=lambda p, t: progress.append((p, t)),
                      platform="darwin")
    studio_ = SyqonStudio("/x/syqon-cli", ctx, store, runner=_silent_runner(clock, 4.0, 4), clock=clock)
    studio_.check_entitlement(["parallax"])  # 16 s of silence
    assert KEYCHAIN_NOTE in logs
    assert (0.0, SIGNIN_WAIT_TEXT) in progress

    # once per session: a later silent run shows the wait text again but not the note
    logs.clear()
    progress.clear()
    studio_.check_entitlement(["axiom"])
    assert KEYCHAIN_NOTE not in logs
    assert (0.0, SIGNIN_WAIT_TEXT) in progress


def test_keychain_notice_not_before_ten_seconds(tmp_path: Path, store: ToolStore) -> None:
    clock = _FakeClock()
    logs: list[str] = []
    ctx = ToolContext(work_dir=tmp_path / "work", log=logs.append, platform="darwin")
    studio_ = SyqonStudio("/x/syqon-cli", ctx, store, runner=_silent_runner(clock, 2.5, 4), clock=clock)
    studio_.check_entitlement(["parallax"])  # 10 s exactly is not more than 10 s
    assert KEYCHAIN_NOTE not in logs


def test_keychain_notice_not_when_the_tool_speaks(tmp_path: Path, store: ToolStore) -> None:
    clock = _FakeClock()
    logs: list[str] = []
    ctx = ToolContext(work_dir=tmp_path / "work", log=logs.append, platform="darwin")
    runner = _silent_runner(clock, 6.0, 4, emit_line=True)
    studio_ = SyqonStudio("/x/syqon-cli", ctx, store, runner=runner, clock=clock)
    studio_.check_entitlement(["parallax"])
    assert KEYCHAIN_NOTE not in logs


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_keychain_notice_is_macos_only(platform: str, tmp_path: Path, store: ToolStore) -> None:
    clock = _FakeClock()
    logs: list[str] = []
    progress: list[tuple[float, str]] = []
    ctx = ToolContext(work_dir=tmp_path / "work", log=logs.append,
                      on_progress=lambda p, t: progress.append((p, t)), platform=platform)
    studio_ = SyqonStudio("/x/syqon-cli", ctx, store, runner=_silent_runner(clock, 6.0, 5), clock=clock)
    studio_.check_entitlement(["parallax"])
    assert KEYCHAIN_NOTE not in logs
    assert all(text != SIGNIN_WAIT_TEXT for _, text in progress)


# --- one run on a plate ------------------------------------------------------------------------------------------


def _plate(tmp_path: Path, planes: list[np.ndarray], name: str = "plate.fits") -> Path:
    path = tmp_path / name
    header = {"CRVAL1": 83.8, "CTYPE1": "RA---TAN", "OBJECT": "synthetic"}
    write_fits(path, [np.asarray(p, dtype=np.float32) for p in planes], header=header, bitpix=-32)
    return path


def test_run_round_trip_keeps_header_and_orientation(tmp_path: Path, store: ToolStore) -> None:
    rng = np.random.default_rng(7)
    plate = rng.random((6, 8), dtype=np.float32) * 0.5
    in_path = _plate(tmp_path, [plate])
    result = np.clip(plate * 0.5 + 0.1, 0.0, 1.0).astype(np.float32)
    out_bytes = _fits_bytes(tmp_path, [result], "result.fits")
    exe = make_fake(tmp_path / "bin", "syqon-cli", exit_code=0, write_files={"out.fits": out_bytes})
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    out_path = tmp_path / "out.fits"
    studio_.run("prism-essential", "linear", {"application": 1.0}, in_path, out_path)

    planes, header = read_fits(out_path)
    np.testing.assert_allclose(planes[0], result, rtol=0, atol=1e-6)
    assert header["CRVAL1"] == pytest.approx(83.8)
    assert header["OBJECT"] == "synthetic"
    argv = read_argv(tmp_path / "bin", "syqon-cli")[0]
    assert argv[:6] == ["--model", "prism-essential", "--domain", "linear", "--precision", "f32"]
    assert argv[-3] == "--overwrite"
    assert not list((tmp_path / "work" / "tools").glob("syqon-studio-*"))


def test_run_takes_channel_zero_for_a_mono_target(tmp_path: Path, store: ToolStore) -> None:
    plate = np.full((4, 4), 0.2, dtype=np.float32)
    in_path = _plate(tmp_path, [plate])
    colour = [np.full((4, 4), v, dtype=np.float32) for v in (0.3, 0.4, 0.5)]
    out_bytes = _fits_bytes(tmp_path, colour, "colour.fits")
    exe = make_fake(tmp_path / "bin", "syqon-cli", write_files={"out.fits": out_bytes})
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    out_path = tmp_path / "out.fits"
    studio_.deep_gradient({}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    assert len(planes) == 1
    np.testing.assert_allclose(planes[0], 0.3, atol=1e-6)


def _folder_runner(
    write: dict[str, np.ndarray],
    stdout: Any,
    seen: dict[str, Any] | None = None,
) -> Any:
    """A runner that writes float planes into the tool's folder (its cwd) and reports ``stdout``.

    ``stdout`` is a list of lines, or a callable taking the folder and returning one.
    """

    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        folder = Path(kwargs["cwd"])
        if seen is not None:
            seen["argv"] = argv
            seen["folder"] = folder
        for name, planes in write.items():
            if name.endswith(".fits"):
                write_fits(folder / name, [np.asarray(planes, dtype=np.float32)], bitpix=-32)
            else:
                (folder / name).write_text("text\n", encoding="utf-8")
        lines = stdout(folder) if callable(stdout) else stdout
        return RunResult(returncode=0, stdout_lines=list(lines), stderr_tail=[])

    return runner


def test_declared_fits_inside_the_folder_is_applied(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)])
    runner = _folder_runner({"named.fits": np.full((4, 4), 0.7)}, lambda f: [str(f / "named.fits")])
    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    out_path = tmp_path / "out.fits"
    studio_.axiom("linear", {}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    np.testing.assert_allclose(planes[0], 0.7, atol=1e-6)


def test_unrelated_existing_fits_on_stdout_is_ignored(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)])
    unrelated = tmp_path / "elsewhere.fits"
    write_fits(unrelated, [np.full((4, 4), 0.9, dtype=np.float32)], bitpix=-32)
    runner = _folder_runner({"out.fits": np.full((4, 4), 0.3)}, [str(unrelated)])
    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    out_path = tmp_path / "out.fits"
    studio_.axiom("linear", {}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    np.testing.assert_allclose(planes[0], 0.3, atol=1e-6)


def test_stray_text_line_is_ignored(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)])
    runner = _folder_runner({"out.fits": np.full((4, 4), 0.4), "named.txt": None},
                            lambda f: [str(f / "named.txt")])
    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    out_path = tmp_path / "out.fits"
    studio_.axiom("linear", {}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    np.testing.assert_allclose(planes[0], 0.4, atol=1e-6)


def test_declared_input_file_is_never_the_result(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)])
    runner = _folder_runner({"out.fits": np.full((4, 4), 0.6)}, lambda f: [str(f / "in.fits")])
    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    out_path = tmp_path / "out.fits"
    studio_.axiom("linear", {}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    np.testing.assert_allclose(planes[0], 0.6, atol=1e-6)


def test_run_passes_the_studio_argv(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)])
    exe = make_fake(tmp_path / "bin", "syqon-cli", write_files={"out.fits": _fits_bytes(
        tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)], "same.fits")})
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    studio_.axiom("linear", {"application": 1.0}, str(in_path), str(tmp_path / "out.fits"))
    argv = read_argv(tmp_path / "bin", "syqon-cli")[0]
    assert argv[:6] == ["--model", "axiom", "--domain", "linear", "--precision", "f32"]
    assert argv[argv.index("--axiom-stretch") + 1] == "auto"
    assert argv[-3] == "--overwrite"


def test_result_is_clipped_to_unit_range(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.zeros((4, 4), np.float32), np.zeros((4, 4), np.float32),
                                np.zeros((4, 4), np.float32)])
    values = [np.full((4, 4), 1.5), np.full((4, 4), -0.2), np.full((4, 4), 0.5)]

    def runner_with_planes(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        write_fits(Path(kwargs["cwd"]) / "out.fits", [v.astype(np.float32) for v in values], bitpix=-32)
        return RunResult(returncode=0, stdout_lines=[], stderr_tail=[])

    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner_with_planes)
    out_path = tmp_path / "out.fits"
    studio_.deep_gradient({}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    assert len(planes) == 3
    np.testing.assert_allclose(planes[0], 1.0, atol=1e-6)
    np.testing.assert_allclose(planes[1], 0.0, atol=1e-6)
    np.testing.assert_allclose(planes[2], 0.5, atol=1e-6)


def test_scale_is_multiplied_back_then_clipped(tmp_path: Path, store: ToolStore, monkeypatch: pytest.MonkeyPatch) -> None:
    in_path = _plate(tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)])
    # one mono plane: left half 0.9 (x2 = 1.8, clipped to 1.0), right half 0.3 (x2 = 0.6)
    values = [np.concatenate([np.full((4, 2), 0.9), np.full((4, 2), 0.3)], axis=1)]

    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        write_fits(Path(kwargs["cwd"]) / "out.fits", [v.astype(np.float32) for v in values], bitpix=-32)
        return RunResult(returncode=0, stdout_lines=[], stderr_tail=[])

    def scaled_exchange(path: Any, planes: Any, **kwargs: Any) -> float:
        write_exchange_fits(path, planes, **kwargs)
        return 2.0

    monkeypatch.setattr(studio, "write_exchange_fits", scaled_exchange)
    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    out_path = tmp_path / "out.fits"
    studio_.deep_gradient({}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    np.testing.assert_allclose(planes[0][:, :2], 1.0, atol=1e-6)
    np.testing.assert_allclose(planes[0][:, 2:], 0.6, atol=1e-6)


def test_run_exchange_input_is_the_sanitised_plate(tmp_path: Path, store: ToolStore) -> None:
    plate = np.array([[np.nan, -0.5, 2.0], [np.inf, 0.25, 1e31]], dtype=np.float32)
    in_path = _plate(tmp_path, [plate])
    seen: dict[str, Any] = {}

    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        seen["in"] = read_fits(argv[argv.index("--overwrite") + 1])[0][0].copy()
        write_fits(Path(kwargs["cwd"]) / "out.fits", [np.zeros((2, 3), np.float32)], bitpix=-32)
        return RunResult(returncode=0, stdout_lines=[], stderr_tail=[])

    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    studio_.deep_gradient({}, str(in_path), str(tmp_path / "out.fits"))
    assert seen["in"].tolist() == [[0.0, 0.0, 1.0], [0.0, 0.25, 0.0]]


def test_probe_image_is_64_by_64_of_0_01(tmp_path: Path, store: ToolStore) -> None:
    seen: dict[str, Any] = {}

    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        planes, _ = read_fits(argv[argv.index("--overwrite") + 1])
        seen["planes"] = planes
        seen["args"] = argv
        return RunResult(returncode=5, stdout_lines=[], stderr_tail=[])

    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    studio_.check_entitlement(["parallax"])
    assert len(seen["planes"]) == 1
    assert seen["planes"][0].shape == (64, 64)
    assert np.all(seen["planes"][0] == np.float32(0.01))
    assert seen["args"][seen["args"].index("--correction") + 1] == "true"


def test_probe_exit_zero_without_output_neither_entitles_nor_clears_flag(
    tmp_path: Path, store: ToolStore
) -> None:
    store.set_flag(PRISM2_FLAG, True)
    stray = tmp_path / "stray.fits"
    write_fits(stray, [np.zeros((4, 4), dtype=np.float32)], bitpix=-32)

    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        return RunResult(returncode=0, stdout_lines=[str(stray)], stderr_tail=[])

    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    assert studio_.check_entitlement(["prism-advanced"]) == {"prism-advanced": False}
    assert store.get_flag(PRISM2_FLAG) is True
    assert studio_.check_entitlement(["prism-advanced"]) == {"prism-advanced": False}


def test_run_without_output_names_the_model_and_exit_message(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.zeros((4, 4), dtype=np.float32)])
    exe = make_fake(tmp_path / "bin", "syqon-cli", exit_code=5, stderr_lines=["gpu lost"])
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    with pytest.raises(StudioNoOutput) as info:
        studio_.run("prism-max", "nonlinear", {"application": 1.0}, in_path, tmp_path / "out.fits")
    message = str(info.value)
    assert "Prism Deep Max (prism-max)" in message
    assert "Inference failed; no output was produced." in message
    assert "stderr: gpu lost" in message
    assert not (tmp_path / "out.fits").exists()


def test_run_with_unreadable_output_is_not_applied(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.zeros((4, 4), dtype=np.float32)])
    exe = make_fake(tmp_path / "bin", "syqon-cli", exit_code=0, write_files={"out.fits": b"not a fits file"})
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    with pytest.raises(StudioNoOutput):
        studio_.run("axiom", "linear", {}, in_path, tmp_path / "out.fits")
    assert not (tmp_path / "out.fits").exists()


def test_run_scale_is_multiplied_back(tmp_path: Path, store: ToolStore, monkeypatch: pytest.MonkeyPatch) -> None:
    plate = np.full((4, 4), 0.2, dtype=np.float32)
    in_path = _plate(tmp_path, [plate])
    out_bytes = _fits_bytes(tmp_path, [np.full((4, 4), 0.25, dtype=np.float32)], "scaled.fits")
    exe = make_fake(tmp_path / "bin", "syqon-cli", write_files={"out.fits": out_bytes})

    def scaled_exchange(path: Any, planes: Any, **kwargs: Any) -> float:
        write_exchange_fits(path, planes, **kwargs)
        return 2.0

    monkeypatch.setattr(studio, "write_exchange_fits", scaled_exchange)
    studio_ = SyqonStudio(exe, _ctx(tmp_path), store)
    out_path = tmp_path / "out.fits"
    studio_.deep_gradient({}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    np.testing.assert_allclose(planes[0], 0.5, atol=1e-6)


def test_run_timeout_and_cancel_propagate_unchanged(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.zeros((4, 4), dtype=np.float32)])
    exe = make_fake(tmp_path / "bin", "syqon-cli", sleep=30)
    cancelled = SyqonStudio(exe, _ctx(tmp_path, cancelled=lambda: True), store)
    with pytest.raises(ToolCancelled):
        cancelled.run("deep-gradient", "linear", {}, in_path, tmp_path / "out.fits")

    def timing_out(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        raise ToolTimeout(tool, kwargs["timeout"])

    timed = SyqonStudio(exe, _ctx(tmp_path), store, runner=timing_out)
    with pytest.raises(ToolTimeout):
        timed.run("deep-gradient", "linear", {}, in_path, tmp_path / "out.fits")


def test_run_uses_the_thirty_minute_timeout(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.zeros((4, 4), dtype=np.float32)])
    seen: dict[str, Any] = {}

    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        seen.update(kwargs)
        return RunResult(returncode=5, stdout_lines=[], stderr_tail=[])

    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    with pytest.raises(StudioNoOutput):
        studio_.run("deep-gradient", "linear", {}, in_path, tmp_path / "out.fits")
    assert seen["timeout"] == 30 * 60
    assert seen["accept_nonzero"] is True


def test_path_on_another_drive_is_skipped_not_fatal(
    tmp_path: Path, store: ToolStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """commonpath raises ValueError for a path on another Windows drive; that line is skipped, out.fits is used."""
    in_path = _plate(tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)])

    def other_drive(paths: Any) -> str:
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(studio.os.path, "commonpath", other_drive)
    assert studio._is_within("D:/x/y.fits", "C:/folder") is False
    runner = _folder_runner({"named.fits": np.full((4, 4), 0.9), "out.fits": np.full((4, 4), 0.3)},
                            lambda f: [str(f / "named.fits")])
    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    out_path = tmp_path / "out.fits"
    studio_.axiom("linear", {}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    np.testing.assert_allclose(planes[0], 0.3, atol=1e-6)


def test_last_declared_line_wins(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)])
    runner = _folder_runner({"a.fits": np.full((4, 4), 0.1), "b.fits": np.full((4, 4), 0.8)},
                            lambda f: [str(f / "a.fits"), str(f / "b.fits")])
    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    out_path = tmp_path / "out.fits"
    studio_.axiom("linear", {}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    np.testing.assert_allclose(planes[0], 0.8, atol=1e-6)


def test_declared_missing_file_falls_back_to_out(tmp_path: Path, store: ToolStore) -> None:
    in_path = _plate(tmp_path, [np.full((4, 4), 0.2, dtype=np.float32)])
    runner = _folder_runner({"out.fits": np.full((4, 4), 0.5)}, lambda f: [str(f / "missing.fits")])
    studio_ = SyqonStudio("/x/syqon-cli", _ctx(tmp_path), store, runner=runner)
    out_path = tmp_path / "out.fits"
    studio_.axiom("linear", {}, str(in_path), str(out_path))
    planes, _ = read_fits(out_path)
    np.testing.assert_allclose(planes[0], 0.5, atol=1e-6)


def test_probe_exit_zero_without_output_is_logged_inconclusive(tmp_path: Path, store: ToolStore) -> None:
    logs: list[str] = []
    ctx = ToolContext(work_dir=tmp_path / "work", log=logs.append, platform="linux")

    def runner(tool: str, argv: list[str], **kwargs: Any) -> RunResult:
        return RunResult(returncode=0, stdout_lines=[], stderr_tail=[])

    studio_ = SyqonStudio("/x/syqon-cli", ctx, store, runner=runner)
    assert studio_.check_entitlement(["parallax"]) == {"parallax": False}
    assert any("parallax: test run inconclusive" in m for m in logs)
    assert not any("available to this account" in m for m in logs)
