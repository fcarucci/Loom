"""The SyQon Studio command-line adapter: Deep Gradient, Parallax, Axiom and Prism models through ``syqon-cli``.

Port of Loom's Studio integration (lib/StepsSyqon.js:1418-1470 constants, :1574-1593 labels, :1638-1748 account
and Prism 2.0 helpers, :1749-1786 the Keychain note, :1787-1866 the entitlement probe, :1937-2017 argument building
and exit messages, :2092-2162 one run, :2215-2238 applying the result). The executable is found elsewhere (see
resolver.py); this module only builds argument lists and runs the executable it is given through the shared runner.

Plate contract (shared by the tool adapters): ``in_fits`` and ``out_fits`` are 32-bit float FITS plates. The input
is read top-down with ``read_fits``, written as an exchange file with ``sanitize="studio"`` (the returned scale is
multiplied back onto the result), the tool runs on the exchange file, and its declared output is read back and
written to ``out_fits`` keeping the input header (WCS included). A result applies only when the output file exists
and loads.

Not ported: Loom's keepFineScaleChange (lib/StepsSyqon.js:2163-2190). It runs only after a Prism 2.0 linear pass,
which exists only when Steps.PRISM2_LINEAR_PASS is on; the shipped Loom never enables it, and it needs PixInsight
image objects, so it has no place in this adapter.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.levels import (
    NR_TOOL_STUDIO,
    STUDIO_MODEL_DENOISE,
    STUDIO_MODEL_GRADIENT,
    STUDIO_MODEL_PARALLAX,
    STUDIO_MODEL_STARLESS,
    STUDIO_PARALLAX_FAMILIES,
    STUDIO_PARALLAX_FAMILY,
)
from loomsiril.loom.tools.exchange import read_exchange_fits, write_exchange_fits
from loomsiril.loom.tools.runner import (
    RunResult,
    ToolError,
    ToolNotRunnable,
    ToolTimeout,
    exchange_folder,
    run_tool,
)
from loomsiril.loom.tools.store import ToolStore

__all__ = [
    "TOOL_NAME",
    "EXCHANGE_TOOL",
    "PRECISION",
    "TILE_SIZE",
    "OVERLAP",
    "RUN_TIMEOUT_SECONDS",
    "PROBE_TIMEOUT_SECONDS",
    "PRISM2_MODELS",
    "PRISM2_FLAG",
    "KEYCHAIN_WAIT_SECONDS",
    "KEYCHAIN_NOTE",
    "SIGNIN_WAIT_TEXT",
    "ToolContext",
    "StudioNoOutput",
    "SyqonStudio",
    "build_args",
    "domain_for",
    "exit_message",
    "failure_text",
    "account_refused",
    "is_prism",
    "is_prism2",
    "model_label",
    "model_role",
    "probe_problem",
    "prism2_flag_after_check",
]

# lib/StepsSyqon.js:1418-1470
TOOL_NAME = "syqon-cli"
EXCHANGE_TOOL = "syqon-studio"
PRECISION = "f32"
TILE_SIZE = 512
OVERLAP = 64
RUN_TIMEOUT_SECONDS = 30 * 60
PROBE_TIMEOUT_SECONDS = 5 * 60

# Prism 2.0 is the two-pass ladder of Steps.NOISE_LEVELS.studio2 (lib/Steps.js:1825-1842): Advanced, Ultra, Max.
PRISM2_MODELS = frozenset({"prism-advanced", "prism-ultra", "prism-max"})
# Settings key for "Prism 2.0 found refused by the account" (lib/StepsSyqon.js:1681-1748).
PRISM2_FLAG = "studioPrism2Unavailable"

# lib/StepsSyqon.js:1574-1581
MODEL_LABELS = {
    "prism-essential": "Prism Essential",
    "prism-advanced": "Prism Deep Advanced",
    "prism-ultra": "Prism Deep Ultra",
    "prism-max": "Prism Deep Max",
    "parallax": "Parallax",
    "axiom": "Axiom V3",
    "deep-gradient": "Deep Gradient",
}

# lib/StepsSyqon.js:1767-1786
KEYCHAIN_WAIT_SECONDS = 10.0
KEYCHAIN_NOTE = (
    "SyQon Studio may ask for your Mac password to reach its sign-in in the Keychain: enter it and choose "
    "Always Allow, or it will ask again every run."
)
SIGNIN_WAIT_TEXT = "Waiting for SyQon Studio's sign-in (check for a Keychain prompt)"

# lib/StepsSyqon.js:1823-1826: the 64x64 mono test image
PROBE_SIZE = 64
PROBE_VALUE = 0.01

_ACCOUNT_STDERR = re.compile(r"secure license check failed|no secure SyQon session", re.IGNORECASE)
_PRISM = re.compile(r"^prism-")


@dataclass
class ToolContext:
    """What every tool adapter receives from the pipeline (identical fields in every adapter)."""

    work_dir: str | os.PathLike[str]
    log: Callable[[str], None] = field(default=lambda _message: None)
    cancelled: Callable[[], bool] = field(default=lambda: False)
    on_progress: Callable[[float, str], None] | None = None
    platform: str = sys.platform


class StudioNoOutput(ToolError):
    """The run produced no readable output (no declared file and no output file, or it does not load)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class _Store(Protocol):
    def get_flag(self, name: str) -> Any: ...

    def set_flag(self, name: str, value: Any) -> None: ...


# --- pure helpers (ports of the Steps.studio* functions) --------------------------------------------------------


def exit_message(code: int | None) -> str:
    """Studio's failure contract for an exit code (lib/StepsSyqon.js:1994-2014)."""
    if code == 1:
        return "General CLI error."
    if code == 2:
        return "Invalid option or incompatible input domain."
    if code == 3:
        return "Input/output path error (existence, permissions, or overwrite policy)."
    if code == 4:
        return (
            "Authentication or entitlement denied. Sign in through SyQon Studio and confirm this model is "
            "available to your account."
        )
    if code == 5:
        return "Inference failed; no output was produced."
    if code == 6:
        return "Output encoding failed."
    if code == 7:
        return "Output was saved, but the Studio handoff step failed."
    if code == 130:
        return "Interrupted."
    return f"syqon-cli exited with code {code}."


def account_refused(exit_code: int | None, stderr: str | None) -> bool:
    """True when the account is not entitled to the model: exit 4, or a licence message on stderr."""
    if exit_code == 4:
        return True
    return _ACCOUNT_STDERR.search(str(stderr or "")) is not None


def is_prism(model: str | None) -> bool:
    return _PRISM.search(str(model or "")) is not None


def is_prism2(model: str | None) -> bool:
    return model in PRISM2_MODELS


def model_label(model: str) -> str:
    return MODEL_LABELS.get(model, model)


def model_role(model: str) -> str:
    """The dropdown a model is chosen from, for "choose another ..." (lib/StepsSyqon.js:1648-1656)."""
    if is_prism(model):
        return "noise reduction tool"
    if model == STUDIO_MODEL_PARALLAX:
        return "sharpening tool"
    if model == STUDIO_MODEL_STARLESS:
        return "star extraction tool"
    return "gradient removal tool"


def probe_problem(model: str, exit_code: int | None, stderr: str | None) -> str | None:
    """A preflight probe's answer as a problem to report, or None (lib/StepsSyqon.js:1663-1680)."""
    if not account_refused(exit_code, stderr):
        return None
    shown = "?" if exit_code is None else str(exit_code)
    msg = (
        f"SyQon Studio: {model_label(model)} ({model}) is not available to your SyQon account "
        f"(syqon-cli exit {shown}). Sign in through SyQon Studio, or choose another {model_role(model)}"
    )
    msg += "."
    if is_prism2(model):
        msg += f" Until a check succeeds, Loom offers {NR_TOOL_STUDIO} (included) in place of Prism 2.0."
    return msg


def failure_text(model: str, exit_code: int | None, stderr: str | None) -> str:
    """Why a run produced nothing, naming the model (lib/StepsSyqon.js:1738-1746)."""
    why = exit_message(exit_code) if exit_code not in (None, 0) else "no output was produced."
    text = f"{model_label(model)} ({model}): {why}"
    trimmed = str(stderr or "").strip()
    if stderr:
        text += f" stderr: {trimmed}"
    return text


def prism2_flag_after_check(results: Iterable[tuple[str, bool]]) -> bool | None:
    """The remembered Prism 2.0 refusal after a check: True if one was refused, False if one ran, else None.

    ``results`` is ``(model, refused)`` pairs (lib/StepsSyqon.js:1694-1708).
    """
    flag: bool | None = None
    for model, refused in results:
        if not is_prism2(model):
            continue
        if refused:
            return True
        flag = False
    return flag


def domain_for(model: str, *, stretched: bool) -> str:
    """The ``--domain`` a model runs in, as Loom's call sites declare it.

    Deep Gradient, Parallax and Prism Advanced always run linear (lib/StepsSyqon.js:1518, lib/Steps.js:2534).
    Prism Essential, Axiom and Prism Ultra run non-linear when the plate is already stretched (lib/Steps.js:1961-1968,
    lib/Steps.js:3290-3298); Prism Ultra runs linear otherwise. Prism Max runs non-linear (never on linear data).
    """
    if model in ("prism-essential", "axiom", "prism-ultra"):
        return "nonlinear" if stretched else "linear"
    if model == "prism-max":
        return "nonlinear"
    if model in (STUDIO_MODEL_GRADIENT, STUDIO_MODEL_PARALLAX, "prism-advanced"):
        return "linear"
    raise ValueError(f"not a SyQon Studio model: {model!r}")


def _num(value: float | int) -> str:
    """JavaScript's String() for the numbers used here: 3.0 prints as 3."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _family(name: Any) -> str:
    return name if name in STUDIO_PARALLAX_FAMILIES else STUDIO_PARALLAX_FAMILY


def _parallax_args(px: Mapping[str, Any]) -> list[str]:
    """Parallax's stages, each switched on or off explicitly; a level or strength follows only a stage that is on."""
    reduction = px.get("reduction") or 0
    deblur = px.get("deblur") or 0
    args = [
        "--family", _family(px.get("family")),
        "--correction", "true" if px.get("correction") else "false",
        "--reduction", "true" if reduction > 0 else "false",
    ]
    if reduction > 0:
        args += ["--reduction-level", _num(reduction)]
    args += ["--deblur", "true" if deblur > 0 else "false"]
    if deblur > 0:
        args += ["--deblur-strength", f"{float(deblur):.4f}"]
    return args


def _model_args(model: str, domain: str, settings: Mapping[str, Any]) -> list[str]:
    """The arguments only one model takes: Prism's blend, Parallax's stages, Axiom's stretch."""
    if is_prism(model):
        return ["--application", f"{float(settings.get('application', 1.0)):.4f}"]
    if model == STUDIO_MODEL_PARALLAX:
        return _parallax_args(settings.get("parallax") or {})
    if model == STUDIO_MODEL_STARLESS:
        # "auto" is Axiom's own stretch, for linear input; stretched input is declared "identity" (not stretched twice)
        return ["--axiom-stretch", "auto" if domain == "linear" else "identity"]
    return []


def build_args(
    model: str,
    domain: str,
    settings: Mapping[str, Any],
    in_fits: str | os.PathLike[str],
    out_fits: str | os.PathLike[str],
) -> list[str]:
    """The ``syqon-cli`` arguments after the executable (lib/StepsSyqon.js:1937-1992).

    ``settings`` holds ``application`` (Prism's blend, default 1.0) and ``parallax`` (a mapping with ``correction``,
    ``reduction`` (0-10, 0 = off), ``deblur`` (0-1 strength, 0 = off) and ``family``).
    """
    args = ["--model", model, "--domain", domain, "--precision", PRECISION]
    if is_prism(model) or model == STUDIO_MODEL_PARALLAX:
        args += ["--tile-size", str(TILE_SIZE), "--overlap", str(OVERLAP)]
    args += _model_args(model, domain, settings)
    # no --quiet (progress is wanted); --stars-output and --gradient-output are never asked for
    args += ["--overwrite", os.fspath(in_fits), os.fspath(out_fits)]
    return args


def _is_within(real: str, real_folder: str) -> bool:
    """True when ``real`` lies inside ``real_folder``. Paths on another Windows drive make commonpath raise ValueError:
    such a path is not inside the folder."""
    try:
        return os.path.commonpath([real, real_folder]) == real_folder
    except ValueError:
        return False


def _result_path(
    stdout_lines: Sequence[str],
    folder: Path,
    in_path: Path,
    out_path: Path,
) -> Path:
    """The file the run produced: the last stdout line naming a ``.fits`` file inside the exchange folder, other than
    the input, if it exists; otherwise ``out_path``. Lines of three characters or fewer are ignored.
    """
    real_folder = os.path.realpath(folder)
    real_in = os.path.realpath(in_path)
    for line in reversed(list(stdout_lines)):
        text = line.strip()
        if len(text) <= 3 or not text.lower().endswith(".fits"):
            continue
        candidate = Path(text) if os.path.isabs(text) else folder / text
        real = os.path.realpath(candidate)
        if real == real_in or not _is_within(real, real_folder):
            continue
        if os.path.isfile(real):
            return Path(real)
    return out_path


# --- the adapter -------------------------------------------------------------------------------------------------


class _Watch:
    """One run's watch: the Keychain notice when the tool shows nothing for 10 s (macOS only), and cancellation."""

    def __init__(self, owner: SyqonStudio, clock: Callable[[], float]) -> None:
        self._owner = owner
        self._clock = clock
        self._started = clock()
        self._saw_output = False
        self._announced = False

    def on_line(self, _stream: str, _line: str) -> None:
        self._saw_output = True

    def cancelled(self) -> bool:
        owner = self._owner
        if (
            not self._announced
            and not self._saw_output
            and owner._ctx.platform == "darwin"
            and self._clock() - self._started > KEYCHAIN_WAIT_SECONDS
        ):
            self._announced = True
            owner._keychain_note()
            if owner._ctx.on_progress is not None:
                owner._ctx.on_progress(0.0, SIGNIN_WAIT_TEXT)
        return bool(owner._ctx.cancelled())


class SyqonStudio:
    """The SyQon Studio adapter for one found executable.

    ``runner`` and ``clock`` are injectable so the tests need no real process or wait. The entitlement cache and the
    Keychain note are per instance (one instance per session).
    """

    def __init__(
        self,
        exe: str | os.PathLike[str],
        ctx: ToolContext,
        store: ToolStore | _Store,
        runner: Callable[..., RunResult] = run_tool,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._exe = os.fspath(exe)
        self._ctx = ctx
        self._store = store
        self._runner = runner
        self._clock = clock
        self._entitled: set[str] = set()
        self._keychain_noted = False
        self.problems: list[str] = []

    # --- helpers ---

    def prism2_unavailable(self) -> bool:
        """The remembered refusal of Prism 2.0 (the store flag)."""
        return self._store.get_flag(PRISM2_FLAG) is True

    def _keychain_note(self) -> None:
        if self._keychain_noted:
            return
        self._keychain_noted = True
        self._ctx.log(KEYCHAIN_NOTE)
        if self._ctx.on_progress is not None:
            self._ctx.on_progress(0.0, KEYCHAIN_NOTE)

    def _invoke(self, args: Sequence[str], *, timeout: float, cwd: Path) -> RunResult:
        watch = _Watch(self, self._clock)
        return self._runner(
            TOOL_NAME,
            [self._exe, *args],
            cwd=cwd,
            timeout=timeout,
            cancelled=watch.cancelled,
            on_progress=self._ctx.on_progress,
            log=self._ctx.log,
            platform=self._ctx.platform,
            accept_nonzero=True,
            on_line=watch.on_line,
        )

    # --- entitlement ---

    def _probe(self, model: str) -> tuple[int | None, str, bool]:
        """One run on the 64x64 test image: ``(exit_code, stderr, ran)``; a cached model answers without a run."""
        if model in self._entitled:
            return 0, "", True
        with exchange_folder(self._ctx.work_dir, EXCHANGE_TOOL) as folder:
            in_path = folder / "probe_in.fits"
            out_path = folder / "probe_out.fits"
            write_exchange_fits(in_path, [np.full((PROBE_SIZE, PROBE_SIZE), PROBE_VALUE, dtype=np.float32)])
            args = build_args(
                model, "linear", {"application": 1.0, "parallax": {"correction": True}}, in_path, out_path
            )
            try:
                result = self._invoke(args, timeout=PROBE_TIMEOUT_SECONDS, cwd=folder)
            except (ToolTimeout, ToolNotRunnable):
                return None, "", False  # inconclusive: changes nothing
            produced = _result_path(result.stdout_lines, folder, in_path, out_path)
            ran = produced.is_file()
            stderr = "\n".join(result.stderr_tail)
            if ran and not account_refused(result.returncode, stderr):
                self._entitled.add(model)
            return result.returncode, stderr, ran

    def check_entitlement(self, models: Sequence[str]) -> dict[str, bool]:
        """Run each model once on the test image: ``{model: True}`` where the model ran (cached per session).

        Refusals are collected in ``self.problems``. Prism 2.0 answers are stored under ``PRISM2_FLAG`` through the
        store (lib/StepsSyqon.js:1714-1748). A cancelled run propagates ToolCancelled.
        """
        problems: list[str] = []
        results: list[tuple[str, bool]] = []
        entitled: dict[str, bool] = {}
        for model in models:
            exit_code, stderr, ran = self._probe(model)
            problem = probe_problem(model, exit_code, stderr)
            refused = problem is not None
            if refused or (exit_code == 0 and ran):
                results.append((model, refused))
            if problem is not None:
                problems.append(problem)
                self._ctx.log(f"studio: {model}: refused by the account")
            elif exit_code == 0 and ran:
                self._ctx.log(f"studio: {model}: available to this account")
            else:
                self._ctx.log(f"studio: {model}: test run inconclusive ({exit_message(exit_code)})")
            entitled[model] = ran and not refused
        flag = prism2_flag_after_check(results)
        if flag is not None and flag != self.prism2_unavailable():
            self._store.set_flag(PRISM2_FLAG, flag)
            self._ctx.log(
                "studio: Prism 2.0 is not available to this account: Loom offers "
                f"{NR_TOOL_STUDIO} in its place until a check succeeds"
                if flag
                else "studio: Prism 2.0 is available to this account again"
            )
        self.problems = problems
        return entitled

    # --- one run ---

    def run(
        self,
        model: str,
        domain: str,
        settings: Mapping[str, Any],
        in_fits: str | os.PathLike[str],
        out_fits: str | os.PathLike[str],
    ) -> None:
        """Run ``model`` on the plate ``in_fits`` and write the result to ``out_fits`` (see the module docstring)."""
        planes, header = read_fits(in_fits)
        n_channels = len(planes)
        with exchange_folder(self._ctx.work_dir, EXCHANGE_TOOL) as folder:
            x_in = folder / "in.fits"
            x_out = folder / "out.fits"
            scale = write_exchange_fits(x_in, planes, sanitize="studio")
            args = build_args(model, domain, settings, x_in, x_out)
            result = self._invoke(args, timeout=RUN_TIMEOUT_SECONDS, cwd=folder)
            produced = _result_path(result.stdout_lines, folder, x_in, x_out)
            if not produced.is_file():
                raise StudioNoOutput(failure_text(model, result.returncode, "\n".join(result.stderr_tail)))
            try:
                result_planes, _ = read_exchange_fits(produced, n_channels)
            except (OSError, ValueError) as exc:
                raise StudioNoOutput(
                    f"{failure_text(model, result.returncode, '')} could not read the result: {exc}"
                ) from exc
            self._entitled.add(model)
            # Loom's PixelMath truncates to [0, 1] after the scale is multiplied back (lib/StepsSyqon.js:2140-2160)
            result_planes = [np.clip(p * scale, 0.0, 1.0) for p in result_planes]
            write_fits(out_fits, [np.asarray(p, dtype=np.float32) for p in result_planes], header=header, bitpix=-32)

    # --- convenience ---

    def deep_gradient(self, settings: Mapping[str, Any], in_fits: str, out_fits: str) -> None:
        self.run(STUDIO_MODEL_GRADIENT, "linear", settings, in_fits, out_fits)

    def parallax(self, domain: str, settings: Mapping[str, Any], in_fits: str, out_fits: str) -> None:
        self.run(STUDIO_MODEL_PARALLAX, domain, settings, in_fits, out_fits)

    def axiom(self, domain: str, settings: Mapping[str, Any], in_fits: str, out_fits: str) -> None:
        self.run(STUDIO_MODEL_STARLESS, domain, settings, in_fits, out_fits)

    def prism(self, model: str, domain: str, settings: Mapping[str, Any], in_fits: str, out_fits: str) -> None:
        if not is_prism(model):
            raise ValueError(f"not a Prism model: {model!r}")
        self.run(model, domain, settings, in_fits, out_fits)
