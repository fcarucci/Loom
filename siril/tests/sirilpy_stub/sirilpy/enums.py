"""Mirror of sirilpy.enums.LogColor (member names and values as in Siril 1.4)."""

from __future__ import annotations

from enum import IntEnum


class LogColor(IntEnum):
    """Colours for ``SirilInterface.log()``: Default, Red (errors), Salmon (warnings), Green, Blue (technical)."""

    DEFAULT = 0
    RED = 1
    SALMON = 2
    GREEN = 3
    BLUE = 4
