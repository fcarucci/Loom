"""RC-Astro CLI adapter: BlurXTerminator, NoiseXTerminator and StarXTerminator through ``rc-astro``.

Each operation runs ``rc-astro <sub> <params> --depth 32F --overwrite --json -o <out.fits> <in.fits>`` with the
tool's own folder as the working directory. ``<sub>`` is ``bxt``, ``nxt`` or ``sxt``. Parameter flags come from the
live schema (``rc-astro <sub> --json``, probed once per adapter and sub) looked up by parameter name; a static table
covers the names the schema lacks. A parameter the schema does not name at all is left out and logged, never guessed.

Loom's settings map to the CLI as follows (lib/Steps.js):

* aberration (lib/Steps.js:2509-2518): ``bxt --correct-only --ss 0 --sn 0 --ansp``
* stars and detail in one pass (:2716-2738): ``bxt --ss S --sn D --ansp``
* stars only (:2577-2588): ``bxt --ss S --sn 0 --ansp``; detail only (:2623-2634): ``bxt --ss 0 --sn D --ansp``
* denoise (:1913-1924): ``nxt --dn d --dc d`` plus the schema's detail parameter, when it has one
* star removal (:3274-3281): ``sxt`` with neither ``--unscreen`` nor ``--difference``

Callers: a sharpen amount of "none" (``levels.sharpen_amount_for`` returns None) must be passed as 0, not None;
``format_number`` refuses None.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom.tools.exchange import read_exchange_fits, write_exchange_fits
from loomsiril.loom.tools.runner import (
    RunResult,
    ToolFailed,
    ToolNotRunnable,
    exchange_folder,
    run_tool,
)
from loomsiril.loom.tools.specs import RC_ASTRO_UNLICENSED_EXIT, UNLICENSED_RC_ASTRO

__all__ = [
    "ToolContext",
    "RcAstro",
    "build_argv",
    "error_message",
    "format_number",
    "SUBS",
    "MIN_SCHEMA_VERSION",
    "RUN_TIMEOUT_SECONDS",
    "PROBE_TIMEOUT_SECONDS",
]

TOOL = "rc-astro"
EXCHANGE_TOOL = "rcastro"
SUBS = ("bxt", "nxt", "sxt")
MIN_SCHEMA_VERSION = 5
TOO_OLD_MESSAGE = "rc-astro 2.5 or newer is needed"
RUN_TIMEOUT_SECONDS = 1800.0
PROBE_TIMEOUT_SECONDS = 60.0
DETAIL_HINT = "detail"
UNREADABLE_SCHEMA = "its schema is unreadable"

# Parameter name -> flag, used when the live schema does not name the parameter.
STATIC_FLAGS: dict[str, dict[str, str]] = {
    "bxt": {
        "correct_only": "--correct-only",
        "sharpen_stars": "--ss",
        "sharpen_detail": "--sn",
        "auto_psf": "--ansp",
    },
    "nxt": {
        "denoise": "--dn",
        "denoise_color": "--dc",
    },
    "sxt": {},
}


@dataclass(frozen=True)
class ToolContext:
    """What every tool adapter receives from the pipeline (the same fields in every adapter)."""

    work_dir: str | os.PathLike[str]
    log: Callable[[str], None] | None
    cancelled: Callable[[], bool]
    on_progress: Callable[[float, str], None] | None
    platform: str


class _RcAstroFailed(ToolFailed):
    """A ToolFailed whose message names the RC-Astro cause (the licence, or the last error event)."""

    def __init__(self, code: int | None, stderr_tail: Sequence[str], message: str) -> None:
        super().__init__(TOOL, code, stderr_tail)
        self.args = (message,)


def _check_sub(sub: str) -> None:
    if sub not in SUBS:
        raise ValueError(f"not an rc-astro sub-command: {sub!r}")


def format_number(value: Any) -> str:
    """A number for the command line: up to four decimals, no trailing zeros (``0.2500`` -> ``0.25``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"rc-astro needs a finite number, got {value!r}")
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _json_object(line: str) -> dict[str, Any] | None:
    s = str(line).strip()
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def error_message(code: int | None, stdout_lines: Sequence[str]) -> str | None:
    """The message RC-Astro gives for a failed run, or None when the caller should use the runner's message.

    Exit 77 is the licence refusal. Otherwise the message of the last NDJSON ``{"type": "error", ...}`` event.
    """
    if code == RC_ASTRO_UNLICENSED_EXIT:
        return UNLICENSED_RC_ASTRO
    for line in reversed(list(stdout_lines)):
        event = _json_object(line)
        if event is not None and event.get("type") == "error":
            message = event.get("message")
            return message.strip() if isinstance(message, str) and message.strip() else None
    return None


def _flag_for(schema: Mapping[str, Any], sub: str, name: str) -> str | None:
    """The flag the live schema gives a parameter name, else the static table's flag, else None."""
    for param in schema.get("parameters") or []:
        if isinstance(param, dict) and param.get("name") == name:
            flag = param.get("flag")
            if isinstance(flag, str) and flag:
                return flag
    return STATIC_FLAGS.get(sub, {}).get(name)


def _detail_choice(schema: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    """The schema's detail parameter, and the candidate names found.

    Rule: a flag-bearing parameter named exactly "detail" wins; otherwise exactly one flag-bearing parameter whose
    name contains "detail" is used. Either must be numeric when the schema gives a type. Anything else (none, or
    several candidates) gives None, and the caller logs the candidates.
    """
    candidates: list[dict[str, Any]] = []
    for param in schema.get("parameters") or []:
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        flag = param.get("flag")
        if isinstance(name, str) and isinstance(flag, str) and flag and DETAIL_HINT in name.lower():
            candidates.append(param)

    def numeric(param: Mapping[str, Any]) -> bool:
        return param.get("type", "number") == "number"

    names = [str(p["name"]) for p in candidates]
    for param in candidates:
        if str(param["name"]).lower() == DETAIL_HINT and numeric(param):
            return str(param["name"]), names
    if len(candidates) == 1 and numeric(candidates[0]):
        return str(candidates[0]["name"]), names
    return None, names


def _parse_schema(lines: Sequence[str]) -> dict[str, Any] | None:
    """The schema object: the whole output as one JSON document, else the last line that is a JSON object with
    a schemaVersion (NDJSON info lines may come before it)."""
    try:
        data: Any = json.loads("\n".join(lines))
    except ValueError:
        data = None
        for line in lines:
            obj = _json_object(line)
            if obj is not None and "schemaVersion" in obj:
                data = obj
    return data if isinstance(data, dict) else None


def build_argv(
    sub: str,
    settings: Mapping[str, Any],
    schema: Mapping[str, Any],
    in_fits: str | os.PathLike[str],
    out_fits: str | os.PathLike[str],
) -> list[str]:
    """The argument list after the executable. ``settings`` maps parameter names to values in command order.

    ``True`` is a bare flag, ``False`` is left out, and a number is formatted by :func:`format_number`.
    """
    _check_sub(sub)
    argv: list[str] = [sub]
    for name, value in settings.items():
        if value is False:
            continue
        flag = _flag_for(schema, sub, name)
        if flag is None:
            raise ValueError(f"rc-astro {sub} has no parameter {name!r} in its schema or the static table")
        argv.append(flag)
        if value is not True:
            argv.append(format_number(value))
    argv += ["--depth", "32F", "--overwrite", "--json", "-o", os.fspath(out_fits), os.fspath(in_fits)]
    return argv


class RcAstro:
    """One rc-astro executable, already resolved by the resolver, with its per-sub schema cache."""

    def __init__(
        self,
        exe: str | os.PathLike[str],
        ctx: ToolContext,
        runner: Callable[..., RunResult] = run_tool,
    ) -> None:
        self.exe = Path(os.fspath(exe))
        self.ctx = ctx
        self._runner = runner
        self._schemas: dict[str, dict[str, Any]] = {}

    def _run(self, argv: Sequence[str], timeout: float) -> RunResult:
        return self._runner(
            TOOL,
            [os.fspath(self.exe), *argv],
            cwd=self.exe.parent,
            timeout=timeout,
            cancelled=self.ctx.cancelled,
            on_progress=self.ctx.on_progress,
            log=self.ctx.log,
            platform=self.ctx.platform,
            accept_nonzero=True,
        )

    def _log(self, line: str) -> None:
        if self.ctx.log is not None:
            self.ctx.log(line)

    def schema(self, sub: str) -> dict[str, Any]:
        """The schema of one sub-command, probed on first use and cached. Refuses what Loom cannot use."""
        _check_sub(sub)
        cached = self._schemas.get(sub)
        if cached is not None:
            return cached
        result = self._run([sub, "--json"], PROBE_TIMEOUT_SECONDS)
        path = os.fspath(self.exe)
        if result.returncode == RC_ASTRO_UNLICENSED_EXIT:
            raise ToolNotRunnable(TOOL, path, UNLICENSED_RC_ASTRO)
        if result.returncode != 0:
            reason = error_message(result.returncode, result.stdout_lines) or "its schema probe failed"
            raise ToolNotRunnable(TOOL, path, reason)
        data = _parse_schema(result.stdout_lines)
        if data is None:
            raise ToolNotRunnable(TOOL, path, UNREADABLE_SCHEMA)
        version = data.get("schemaVersion")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ToolNotRunnable(TOOL, path, f"{UNREADABLE_SCHEMA} (schemaVersion is not a whole number)")
        if version < MIN_SCHEMA_VERSION:
            raise ToolNotRunnable(TOOL, path, TOO_OLD_MESSAGE)
        license_info = data.get("license")
        if not isinstance(license_info, dict) or license_info.get("valid") is not True:
            raise ToolNotRunnable(TOOL, path, UNLICENSED_RC_ASTRO)
        self._schemas[sub] = data
        return data

    def _failure(self, result: RunResult) -> ToolFailed:
        message = error_message(result.returncode, result.stdout_lines)
        if message is None:
            return ToolFailed(TOOL, result.returncode, result.stderr_tail)
        return _RcAstroFailed(result.returncode, result.stderr_tail, message)

    def _process(
        self,
        sub: str,
        settings: Mapping[str, Any],
        in_fits: str | os.PathLike[str],
        out_fits: str | os.PathLike[str],
    ) -> None:
        schema = self.schema(sub)
        planes, header = read_fits(in_fits)
        n_channels = len(planes)
        with exchange_folder(self.ctx.work_dir, EXCHANGE_TOOL) as folder:
            src = folder / "input.fits"
            dst = folder / "output.fits"
            write_exchange_fits(src, planes, header=header, sanitize="basic")
            argv = build_argv(sub, settings, schema, src, dst)
            result = self._run(argv, RUN_TIMEOUT_SECONDS)
            if result.returncode != 0:
                raise self._failure(result)
            # rc-astro writes to the -o path only; a stdout line naming another file is never read
            produced = dst
            if not os.path.isfile(produced):
                raise _RcAstroFailed(result.returncode, result.stderr_tail, "rc-astro wrote no output file")
            try:
                out_planes, _out_header = read_exchange_fits(produced, n_channels)
            except (OSError, ValueError, KeyError) as exc:
                raise _RcAstroFailed(
                    result.returncode, result.stderr_tail, "rc-astro wrote an output file that could not be read"
                ) from exc
        write_fits(out_fits, out_planes, header=header, bitpix=-32)

    def aberration(self, in_fits: str | os.PathLike[str], out_fits: str | os.PathLike[str]) -> None:
        """Chromatic aberration correction only (no star or detail change)."""
        settings = {"correct_only": True, "sharpen_stars": 0, "sharpen_detail": 0, "auto_psf": True}
        self._process("bxt", settings, in_fits, out_fits)

    def sharpen(
        self,
        in_fits: str | os.PathLike[str],
        out_fits: str | os.PathLike[str],
        stars: float,
        detail: float,
    ) -> None:
        """Stars and detail amounts (from levels.sharpen_amount_for), one pass with the automatic PSF."""
        settings = {"sharpen_stars": stars, "sharpen_detail": detail, "auto_psf": True}
        self._process("bxt", settings, in_fits, out_fits)

    def denoise(
        self,
        in_fits: str | os.PathLike[str],
        out_fits: str | os.PathLike[str],
        amount: float,
        detail: float,
    ) -> None:
        """Luminance and colour noise amount (from levels.noise_amount_for) plus the schema's detail parameter."""
        schema = self.schema("nxt")
        settings: dict[str, Any] = {"denoise": amount, "denoise_color": amount}
        detail_name, candidates = _detail_choice(schema)
        if detail_name is None:
            found = ", ".join(candidates) if candidates else "none"
            self._log(
                f"rc-astro nxt: no unambiguous detail parameter in the schema (candidates: {found}); "
                "the detail amount is omitted"
            )
        else:
            settings[detail_name] = detail
        self._process("nxt", settings, in_fits, out_fits)

    def remove_stars(self, in_fits: str | os.PathLike[str], out_fits: str | os.PathLike[str]) -> None:
        """Star removal with neither --unscreen nor --difference."""
        self._process("sxt", {}, in_fits, out_fits)
