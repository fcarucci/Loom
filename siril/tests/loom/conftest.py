"""Fixtures for the Loom pipeline tests (the suite-wide rules of tests/conftest.py apply: no network, tool markers).

* ``loom_master``: factory ``loom_master(channel="L", **kw)`` -> ``synth.Master`` (tests/loom/synth.py).
* ``loom_master_file``: factory ``loom_master_file(tmp_dir, channel="L", name=None, **kw)`` -> path of the master
  written as 32-bit float FITS.
* Every network-marked test gets Siril's SPCC database first (``network_runs.ensure_spcc_database``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from . import synth
from .network_runs import ensure_spcc_database


@pytest.fixture
def loom_master() -> Callable[..., synth.Master]:
    return synth.make_master


@pytest.fixture
def loom_master_file() -> Callable[..., Path]:
    def make(tmp_dir: "str | Path", channel: str = "L", name: "str | None" = None, **kw: Any) -> Path:
        m = synth.make_master(channel, **kw)
        return synth.write_master(Path(tmp_dir) / (name or f"master_{channel}.fits"), m)

    return make


@pytest.fixture(autouse=True)
def _spcc_database_for_network_tests(request: pytest.FixtureRequest) -> None:
    """Installs the SPCC database before any network-marked test (skipped network tests never get here)."""
    if request.node.get_closest_marker("network") is not None:
        ensure_spcc_database()
