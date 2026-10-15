"""Star-extraction tools for the Fly-Through script: which ones this machine has, and running the chosen one.

Discovery reuses Loom's resolver (loomsiril.loom.tools.resolver.Resolver, ids from tools.specs: "starnet2",
"syqon-cli", "rcastro"); the extraction reuses the star-removal code of Loom's pipeline
(loomsiril.loom.finishing._star_removal), so every tool runs exactly as it does in Loom. No path is hard-coded here.

Both :func:`discover` and :func:`extract_starless` are slow (they probe or run programs): callers run them on a worker
thread, never on the Tk thread. This module has no GUI code and no sirilpy import.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from loomsiril import io_fits

__all__ = ["Discovery", "ExtractTool", "NONE_FOUND", "TOOL_IDS", "choose", "discover", "extract_starless",
           "make_resolver"]

#: (resolver id, label) in the order the drop-down lists them. The labels are Loom's star-tool names.
TOOL_IDS: tuple[tuple[str, str], ...] = (
    ("starnet2", "StarNet2"),
    ("syqon-cli", "SyQon Studio Axiom"),
    ("rcastro", "StarXTerminator"),
)
#: Loom's star-tool name of each resolver id (what finishing._star_removal dispatches on).
_LOOM_NAME = {"starnet2": "STAR_TOOL_STARNET", "syqon-cli": "STAR_TOOL_STUDIO", "rcastro": "STAR_TOOL_SXT"}

NONE_FOUND = "No star-extraction tool found: StarNet2, SyQon Studio or StarXTerminator"


@dataclass(frozen=True)
class ExtractTool:
    id: str
    label: str
    path: str


@dataclass
class Discovery:
    """The tools found (in drop-down order) and the plain-text notes for tools that exist but were refused."""

    tools: list[ExtractTool] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ffmpeg: Optional[bool] = None            # whether ffmpeg was found; None: not looked for
    ffmpeg_path: str = ""                    # where it was found ("" when not found, or when the finder gave only a yes or no)

    def by_id(self, tool_id: str) -> Optional[ExtractTool]:
        return next((t for t in self.tools if t.id == tool_id), None)


def make_resolver(config_dir: Any, data_dir: Optional[str] = None, environ: Optional[Mapping[str, str]] = None,
                  platform: Optional[str] = None) -> Any:
    """Loom's Resolver for this machine, with the tools store of ``config_dir`` (no store when that is refused)."""
    from loomsiril.loom.tools.resolver import Resolver
    from loomsiril.loom.tools.store import ToolStore

    try:
        store: Any = ToolStore(config_dir) if config_dir else None
    except (ValueError, OSError):
        store = None
    return Resolver(platform or sys.platform, os.path.expanduser("~"), dict(os.environ if environ is None else environ),
                    store=store, siril_data_dir=data_dir, siril_config_dir=str(config_dir) if config_dir else None)


def discover(resolver: Any, find_ffmpeg: Optional[Callable[[], Any]] = None) -> Discovery:
    """Ask ``resolver`` for each extraction tool; only those it found (and that passed their probe) are listed.
    ``find_ffmpeg`` (slow: it runs ffmpeg), when given, says whether the video can be encoded."""
    out = Discovery()
    if find_ffmpeg is not None:
        try:
            found_ffmpeg = find_ffmpeg()
            out.ffmpeg = bool(found_ffmpeg)
            out.ffmpeg_path = found_ffmpeg if isinstance(found_ffmpeg, str) else ""
        except Exception:
            out.ffmpeg = False
    for tool_id, label in TOOL_IDS:
        try:
            found = resolver.resolve(tool_id)
        except Exception as exc:  # one broken probe must not hide the other tools
            out.notes.append(f"{label} could not be checked: {exc}")
            continue
        if found is not None:
            out.tools.append(ExtractTool(tool_id, label, found.path))
            continue
        why = resolver.rejection(tool_id)
        if why:
            out.notes.append(f"{label} is not listed: {why}")
    return out


def choose(tools: Sequence[ExtractTool], saved: Any) -> tuple[Optional[str], str]:
    """(selected tool id, note): the saved choice when it is still found, else the first tool, saying so."""
    if not tools:
        return None, NONE_FOUND
    if saved:
        if any(t.id == saved for t in tools):
            return str(saved), ""
        label = dict(TOOL_IDS).get(str(saved), str(saved))
        return tools[0].id, f"The saved tool {label} was not found; using {tools[0].label}."
    return tools[0].id, ""


class _MemoryFlags:
    """The tools-store flags the SyQon adapter reads, kept in memory when there is no store."""

    def __init__(self) -> None:
        self._flags: dict[str, Any] = {}

    def get_flag(self, name: str) -> Any:
        return self._flags.get(name)

    def set_flag(self, name: str, value: Any) -> None:
        self._flags[name] = value


def extract_starless(tool_id: str, exe: str, planes: Sequence[np.ndarray], *, licence: Optional[str] = None,
                     log: Callable[[str], None] = lambda _t: None,
                     on_progress: Optional[Callable[[float, str], None]] = None,
                     cancelled: Callable[[], bool] = lambda: False, store: Any = None,
                     platform: Optional[str] = None) -> list[np.ndarray]:
    """The starless planes of the image ``planes`` made by the tool ``tool_id`` at ``exe`` (Loom's star removal).

    Raises the adapter's own errors unchanged (a licence refusal says so: ToolNotRunnable / StarNetLicenceNotAccepted;
    a cancel is ToolCancelled). The temporary FITS files are removed on return and on error.
    """
    from loomsiril.loom import finishing, levels
    from loomsiril.loom.runctx import RunContext

    if tool_id not in _LOOM_NAME:
        raise ValueError(f"unknown star extraction tool: {tool_id!r}")
    with tempfile.TemporaryDirectory(prefix="loomsiril-extract-") as tmp:
        ctx = RunContext(ops=None, work_dir=tmp, log=log, warn=log, error=log, cancelled=cancelled,
                         on_progress=on_progress, platform=platform or sys.platform, tools={tool_id: exe},
                         tool_store=store if store is not None else _MemoryFlags(), starnet_revision=licence)
        src, dst = os.path.join(tmp, "image.fits"), os.path.join(tmp, "starless.fits")
        io_fits.write_fits(src, [np.clip(np.asarray(p, dtype=np.float32), 0.0, 1.0) for p in planes], bitpix=-32)
        finishing._star_removal(ctx, getattr(levels, _LOOM_NAME[tool_id]), src, dst, studio_domain="linear")
        got, _ = io_fits.read_fits(dst)
    if len(got) != len(planes) or any(g.shape != planes[0].shape for g in got):
        raise ValueError(f"the tool returned {len(got)} channel(s) of shape {got[0].shape}; "
                         f"expected {len(planes)} of {planes[0].shape}")
    return [np.asarray(g, dtype=np.float32) for g in got]
