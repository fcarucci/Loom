"""StarNet2 star removal on a linear frame, behind the licence gate.

The StarNet2 command line is started through the shared process runner (:func:`runner.run_tool`): cancel, progress
and log lines behave as for every other tool, and the process group is stopped on timeout or cancel. The argument
list is the one :func:`loomsiril.siril_bridge.starnet_args` builds. The input plate is clipped to [0, 1] float32 in a
private exchange folder, as :func:`loomsiril.siril_bridge.run_starnet` does; the starless result is written with the
input header (WCS included). StarNet2 runs only when its licence revision has been accepted (see
:mod:`loomsiril.starnet_licence`); the revision is passed in by the caller.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.siril_bridge import STARNET_TIMEOUT_S, StarnetError, starnet_args
from loomsiril.starnet_licence import STARNET_LICENCE_REV

from .runner import ToolCancelled, ToolError, exchange_folder, run_tool

__all__ = ["STARNET_TOOL", "StarNetLicenceNotAccepted", "ToolContext", "remove_stars"]

#: The tool name used for the exchange folder and the runner's log lines.
STARNET_TOOL = "starnet2"


class StarNetLicenceNotAccepted(ToolError):
    """StarNet2 was asked to run without its licence revision accepted; nothing was started."""


@dataclass(frozen=True)
class ToolContext:
    """The run context of an adapter: where its exchange folder goes and how it reports (identical in every adapter)."""

    work_dir: str | os.PathLike[str]
    log: Optional[Callable[[str], Any]]
    cancelled: Callable[[], bool]
    on_progress: Optional[Callable[[float, str], None]]
    platform: str


def _context(ctx_or_log: Any, stack: ExitStack) -> ToolContext:
    """A ToolContext as given, or one built from a bare log callable (the exchange folder then lives in a temporary
    directory that the stack removes)."""
    if isinstance(ctx_or_log, ToolContext):
        return ctx_or_log
    work = stack.enter_context(tempfile.TemporaryDirectory(prefix="loom-starnet-"))
    return ToolContext(work_dir=work, log=ctx_or_log, cancelled=lambda: False, on_progress=None,
                       platform=sys.platform)


def _channels_for(got: list[np.ndarray], n: int, shape: tuple[int, ...]) -> list[np.ndarray]:
    """A grey output for a colour input is replicated and a colour output for a grey input is averaged; any other
    mismatch raises StarnetError (the real CLI never needs either mapping)."""
    if len(got) == 1 and n == 3:
        got = [got[0], got[0], got[0]]
    elif len(got) == 3 and n == 1:
        got = [np.mean(np.stack(got), axis=0)]
    if len(got) != n or any(g.shape != shape for g in got):
        raise StarnetError(f"StarNet output has {len(got)} channel(s) of shape {got[0].shape}; expected {n}")
    return [np.asarray(g, dtype=np.float32) for g in got]


def remove_stars(exe: Any, ctx_or_log: Any, in_fits: Any, out_fits: Any, accepted_revision: Optional[str]) -> str:
    """Remove the stars from the linear plate ``in_fits`` with StarNet2 at ``exe``; the starless plate goes to
    ``out_fits`` (returned as a string).

    Raises :class:`StarNetLicenceNotAccepted` before anything runs unless ``accepted_revision`` is the current
    licence revision. ToolCancelled, ToolTimeout and ToolFailed come from the runner unchanged. A nonzero exit or a
    missing output raises :class:`StarnetError` (a frame smaller than StarNet2's window exits nonzero, 255 seen, or with no output).
    """
    if accepted_revision != STARNET_LICENCE_REV:
        raise StarNetLicenceNotAccepted(
            f"StarNet2 runs only after its licence is accepted (revision {STARNET_LICENCE_REV}): "
            "accept it in the dialog, or set starnetLicenceAccepted in the job")
    with ExitStack() as stack:
        ctx = _context(ctx_or_log, stack)
        planes, header = read_fits(in_fits)
        shape = planes[0].shape
        clipped = [np.clip(np.asarray(p, dtype=np.float32), 0.0, 1.0) for p in planes]
        exe_path = os.fspath(exe)
        with exchange_folder(ctx.work_dir, STARNET_TOOL) as folder:
            inp, out = folder / "starnet_in.fits", folder / "starnet_out.fits"
            write_fits(inp, clipped, bitpix=-32)
            # The CLI loads its weights from its own folder, so it runs there; the paths are absolute.
            result = run_tool(
                STARNET_TOOL,
                starnet_args(exe_path, inp, out),
                cwd=os.path.dirname(os.path.abspath(exe_path)),
                timeout=STARNET_TIMEOUT_S,
                cancelled=ctx.cancelled,
                on_progress=ctx.on_progress,
                log=ctx.log,
                platform=ctx.platform,
                accept_nonzero=True,
            )
            if result.returncode != 0 or not out.exists():
                detail = "\n".join(result.stderr_tail)[-400:]
                raise StarnetError(f"StarNet exited with {result.returncode} and no output: {detail}")
            got, _ = read_fits(out)
            starless = _channels_for(got, len(planes), shape)
        write_fits(out_fits, starless, header=header)
    return os.fspath(out_fits)
