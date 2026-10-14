"""docker/network-setup.sh fetches nothing unless the network tests were asked for."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "network-setup.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_refuses_without_the_network_opt_in(tmp_path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "LOOMSIRIL_NETWORK"}
    env.update(HOME=str(tmp_path), XDG_DATA_HOME=str(tmp_path / "data"))
    proc = subprocess.run(["bash", str(SCRIPT), "true"], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2 and "refusing" in proc.stderr
    assert not (tmp_path / "data").exists()


def test_script_targets_the_folder_siril_reads() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'DEST="$DATA/siril-spcc-database"' in text and "XDG_DATA_HOME:-$HOME/.local/share" in text
    assert 'URL="https://gitlab.com/free-astro/siril-spcc-database"' in text  # exact origin, no .git suffix


def test_database_commit_is_pinned() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'COMMIT="${SPCC_DATABASE_COMMIT:-' in text and 'fetch --quiet --depth 1 origin "$COMMIT"' in text
