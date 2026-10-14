"""Mirror of sirilpy.utility.ensure_installed (signature as in Siril 1.4). Records the request; never runs pip."""

from __future__ import annotations

from typing import List, Optional, Union

from .connection import STATE


def ensure_installed(*packages: Union[str, List[str]],
                     version_constraints: Optional[Union[str, List[str]]] = None,
                     reinstall: Optional[bool] = False) -> bool:
    """Ensure packages are installed (the stub only records the names; the real one runs pip)."""
    if isinstance(packages[0], list):
        packages = tuple(packages[0])
    STATE.installs.append((tuple(packages), version_constraints, reinstall))
    return True
