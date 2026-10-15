"""The minimum StarNet2 version Loom accepts, and a tolerant reader for the ``--version`` output.

StarNet2 builds before 2.6.2 cannot read FITS (``Unable to read input image: ...fits``, exit 255), which is the
exchange format Loom and the Fly-Through use, so an older build is refused (not offered as a star-removal tool)
rather than failing mid-run. The Docker image pins 2.6.2.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["MIN_STARNET_VERSION", "parse_starnet_version", "starnet_version_problem"]

#: The oldest StarNet2 that reads FITS input. Older builds are rejected with a warning.
MIN_STARNET_VERSION: tuple[int, int, int] = (2, 6, 2)

_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_starnet_version(text: str) -> Optional[tuple[int, int, int]]:
    """The first ``major.minor[.patch]`` in ``text`` ("starnet2 version: 2.5.2", "StarNet++ v2.6"), or None."""
    m = _VERSION.search(text or "")
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def starnet_version_problem(text: str) -> Optional[str]:
    """None when ``text`` (the ``--version`` line) names a version >= MIN_STARNET_VERSION, else the warning to show."""
    need = ".".join(map(str, MIN_STARNET_VERSION))
    got = parse_starnet_version(text)
    if got is None:
        return (f"the StarNet2 version could not be read: Loom needs {need} or newer "
                "(older builds cannot read FITS)")
    if got < MIN_STARNET_VERSION:
        return (f"StarNet2 {'.'.join(map(str, got))} is too old: Loom needs {need} or newer "
                "(older builds cannot read FITS)")
    return None
