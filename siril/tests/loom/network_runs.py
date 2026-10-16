"""Shared helper for the network-marked siril-cli tests: Siril's SPCC database.

* ``ensure_spcc_database()``: the SPCC database folder, installed with docker/network-setup.sh when it is missing
  (the test image never holds it). Every network-marked test gets it first (tests/loom/conftest.py), whatever order
  the tests run in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from loomsiril.loom.siril_ops import spcc_database_candidates

#: The siril/ checkout (docker/network-setup.sh lives under it).
REPO = Path(__file__).resolve().parents[2]


def _installed_spcc_database() -> Optional[Path]:
    for candidate in spcc_database_candidates(sys.platform, os.environ, os.path.expanduser("~")):
        if os.path.isdir(candidate):
            return Path(candidate)
    return None


def ensure_spcc_database() -> Path:
    """Siril's SPCC database, installed with docker/network-setup.sh when it is missing."""
    found = _installed_spcc_database()
    if found is not None:
        return found
    env = dict(os.environ, LOOMSIRIL_NETWORK="1")
    proc = subprocess.run(["bash", str(REPO / "docker" / "network-setup.sh")], capture_output=True, text=True,
                          env=env, timeout=600, check=False)
    found = _installed_spcc_database()
    if proc.returncode != 0 or found is None:
        pytest.fail(f"the SPCC database is missing and network-setup.sh did not install it "
                    f"(exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return found
