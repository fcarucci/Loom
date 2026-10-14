"""The run context every pipeline stage receives (one object per run, created by the orchestrator).

It bundles what stages need besides their input and output plates: the Siril operations wrapper, the run's work
folder, logging, cancel and progress callbacks, the platform, and the external tools resolved for this run. Stage
modules take a ``RunContext`` instead of a growing list of arguments; tool adapters get their own small
``ToolContext`` from :meth:`RunContext.tool_context`.

Callbacks (log, warn, error, cancelled, on_progress) may be called from tool reader threads: they must be
thread-safe (the dialog's progress callback only enqueues). Not to be confused with the Fly-Through's
``loomsiril.pipeline`` module: this is ``loomsiril.loom.runctx.RunContext``.

Importing this module has no side effects and does not import sirilpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

__all__ = ["RunContext", "StageOutput"]


def _no_log(_text: str) -> None:
    return None


def _never() -> bool:
    return False


@dataclass
class RunContext:
    """Everything a stage needs from the run.

    ``ops``: a loomsiril.loom.siril_ops.SirilOps. ``work_dir``: the run's work folder (stages write their plates
    and tool exchange folders under it). ``log``/``warn``/``error``: one-line messages (keep them short; never dump
    large payloads). ``cancelled``: polled at checkpoints and inside tool waits. ``on_progress(percent, text)``.
    ``platform``: sys.platform value used for tool behaviour. ``tools``: resolved executable path per tool id
    (loomsiril.loom.tools.specs ids, e.g. "rcastro", "syqon-cli", "SyQonStarless", "prism_cli", "parallax_cli",
    "starnet2", "graxpert-ai"). ``tool_store``: the tools.json store (session flags). ``starnet_revision``: the
    accepted StarNet licence revision or None. ``residuals``: a residuals.ResidualChecker or None (check off).
    ``extra``: free slot for orchestration data a stage needs but that has no field yet.
    """

    ops: Any
    work_dir: str
    log: Callable[[str], None] = _no_log
    warn: Callable[[str], None] = _no_log
    error: Callable[[str], None] = _no_log
    cancelled: Callable[[], bool] = _never
    on_progress: Optional[Callable[[float, str], None]] = None
    platform: str = "linux"
    tools: Mapping[str, str] = field(default_factory=dict)
    tool_store: Any = None
    starnet_revision: Optional[str] = None
    residuals: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    def tool(self, tool_id: str) -> str:
        """The resolved executable path of ``tool_id``; KeyError naming the tool when it was not resolved."""
        try:
            return self.tools[tool_id]
        except KeyError:
            raise KeyError(f"tool {tool_id!r} was not found or did not pass its probe") from None

    def tool_context(self, cls: Callable[..., Any]) -> Any:
        """Build an adapter's ToolContext (every adapter defines one with these five fields)."""
        return cls(work_dir=self.work_dir, log=self.log, cancelled=self.cancelled,
                   on_progress=self.on_progress, platform=self.platform)


@dataclass
class StageOutput:
    """What a stage runner returns: the output plate, companion plates stored with the stage (``"stars"`` for star
    extraction, ``"linear"`` for a stretch with keep-linear), and ``skip_cache`` when the stage kept the plate as it
    was after a non-fatal failure (Loom's SKIP_CACHE: the result must not be stored in the cache). ``skip_cache=True``
    is how a runner reports what Loom signals by returning ``stagekit.SKIP_CACHE``; the orchestrator maps it."""

    path: str
    companions: dict[str, str] = field(default_factory=dict)
    skip_cache: bool = False
