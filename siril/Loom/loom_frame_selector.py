"""Loom Frame Selector for Siril: a thin Siril 1.4 script over loomsiril. Looks for an ASIAIR card, else asks for a
folder of subframes, measures them and opens the review dialog. Needs Siril 1.4 (sirilpy), numpy, scipy, tifffile,
ttkthemes and loomsiril (Loom/README.md). THIS SCRIPT HAS NOT BEEN RUN INSIDE SIRIL: it is tested against a stand-in
sirilpy only. Importing it does nothing.
"""

import os
import sys
from typing import Any

from sirilpy import SirilInterface, ensure_installed

#: Optional: a directory that contains the ``loomsiril`` package (a checkout of this repository).
LOOMSIRIL_SRC_ENV = "LOOMSIRIL_SRC"
#: Python packages needed in Siril's Python environment (ttkthemes: Siril's theme applies only to a ThemedTk window).
PACKAGES = ("numpy", "scipy", "tifffile", "ttkthemes")
TITLE = "Loom Frame Selector"
#: The oldest Siril the script runs on.
MIN_SIRIL = "1.4.0"


def _import_frames_main() -> Any:
    """Import loomsiril.frames_main: $LOOMSIRIL_SRC first on sys.path when it is set, then this script's checkout."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <root>/Loom/loom_frame_selector.py
    checkout = root if os.path.isfile(os.path.join(root, "loomsiril", "__init__.py")) else None
    for src in (checkout, os.environ.get(LOOMSIRIL_SRC_ENV)):
        if src and src not in sys.path:
            sys.path.insert(0, src)
    from loomsiril import frames_main  # imported here, not at module level: nothing happens on import

    return frames_main


def _report(siril: SirilInterface, text: str) -> None:
    """Show an error box on a connected Siril; with no connection, print it to stderr instead."""
    try:
        siril.error_messagebox(text)
    except Exception:  # the box needs a live connection; the message must still get out
        print(text, file=sys.stderr)


def _siril_is_new_enough(siril: SirilInterface) -> bool:
    """Siril 1.4 or later (its ``requires`` command); an older one is told so in one box and nothing is installed."""
    try:
        ok = siril.cmd("requires", MIN_SIRIL) is not False
    except Exception:  # the command fails on an older Siril
        ok = False
    if not ok:
        siril.error_messagebox(f"{TITLE} needs Siril {MIN_SIRIL} or later. Please update Siril and run the script again.")
    return ok


def main() -> None:
    siril = SirilInterface()
    try:
        connected = siril.connect()   # the real connect() raises SirilConnectionError; the stub may return False
    except Exception as exc:
        print(f"{TITLE}: cannot connect to Siril ({exc})", file=sys.stderr)
        return
    if not connected:
        print(f"{TITLE}: cannot connect to Siril", file=sys.stderr)
        return
    try:
        if not _siril_is_new_enough(siril):  # before any package is installed
            return
        ensure_installed(*PACKAGES)  # before loomsiril is imported: it needs numpy
        _import_frames_main().main(siril)
    except Exception as exc:  # every failure is shown in a box, never a raw traceback in Siril
        _report(siril, f"{TITLE}: {exc}")
    finally:
        try:
            siril.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
