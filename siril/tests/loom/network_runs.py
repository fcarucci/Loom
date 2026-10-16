"""Shared helpers for the network-marked siril-cli tests: Siril's SPCC database and its local star catalogue.

* ``ensure_spcc_database()``: the SPCC database folder, installed with docker/network-setup.sh when it is missing
  (the test image never holds it). Every network-marked test gets it first (tests/loom/conftest.py), whatever order
  the tests run in.
* ``ensure_local_gaia_catalogue()``: SPCC must read its stars from a local catalogue, never from Siril's online
  service (one failed request there kills siril-cli; tests/loom/gaia_xpsamp.py). The test image holds a local copy
  of the test fields; elsewhere the test fails with the command that installs it, since that copy becomes the local
  catalogue of every Siril run on the machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from loomsiril.loom.siril_ops import spcc_database_candidates

from . import gaia_xpsamp

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


def ensure_local_gaia_catalogue() -> Path:
    """The local Gaia xp_sampled catalogue Siril's SPCC reads (Siril's default folder), with the test fields' stars.

    A folder that holds catalogue chunks but no test-field manifest is a full catalogue installed by hand: it is
    used as it is.
    """
    folder = gaia_xpsamp.default_folder()
    if gaia_xpsamp.covered(folder):
        return folder
    own = folder / gaia_xpsamp.MANIFEST
    if not own.exists() and any(folder.glob("siril_cat*_healpix*_xpsamp_*.dat")):
        return folder
    pytest.fail(f"no local Gaia catalogue for the test fields in {folder}: SPCC would ask Siril's online service, "
                f"whose failed requests kill siril-cli. Install it with: python3 {Path(gaia_xpsamp.__file__)} "
                f"(the test image does this at build time)")
