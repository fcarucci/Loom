"""Stand-in for Siril's ``sirilpy`` package, for tests only (E7).

Mirrors the names and signatures of the real package (Siril 1.4 python_module, read from the Siril 1.4.4 AppImage
in the Docker image; no code copied). The real package talks to a running Siril over a pipe; this one keeps the state
in the module-level ``STATE`` that tests set up (a loaded image, its header, stars, ICC) and records what the script
asked for (messages, progress, logs, installs). Nothing here touches Siril, a socket or pip.

NOT the real sirilpy: a script that passes these tests has NOT been run inside Siril.
"""

from __future__ import annotations

from .connection import STATE, PSFStar, SirilInterface
from .enums import LogColor
from .exceptions import CommandError, DataError, NoImageError, SirilConnectionError, SirilError
from .utility import ensure_installed

__all__ = [
    "STATE",
    "CommandError",
    "DataError",
    "LogColor",
    "NoImageError",
    "PSFStar",
    "SirilConnectionError",
    "SirilError",
    "SirilInterface",
    "ensure_installed",
]
