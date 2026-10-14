"""No network in tests; Gaia online refused under pytest; IC 1396 cluster distance 870-1000 pc."""

from __future__ import annotations

import re
import socket

import pytest

from acc_helpers import cli_args, combined, prepare_inputs, run_cli


def test_socket_connect_is_blocked() -> None:
    from conftest import NetworkBlocked

    # The guard's own error, not ConnectionRefusedError (an unguarded connect to port 9 would raise that).
    with pytest.raises(NetworkBlocked, match="network access is not allowed"):
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlocked, match="network access is not allowed"):
            s.connect(("127.0.0.1", 9))
    finally:
        s.close()


def test_gaia_online_is_refused_under_pytest(tmp_path) -> None:
    import synth

    field = synth.make_field(w=320, h=180, n_stars=20, nc=3)
    paths = prepare_inputs(tmp_path, field)
    args = [a for a in cli_args(paths, tmp_path / "out", field)]
    i = args.index("--gaia")
    args[i + 1] = "online"
    proc = run_cli(tmp_path, args, env_extra={"PYTEST_CURRENT_TEST": "acceptance::online (set by pytest)"})
    text = combined(proc)
    assert proc.returncode != 0
    assert "online" in text.lower()
    assert re.search(r"pytest|under test|in tests|test run|tests", text, re.I), "refusal must say why (pytest/tests):\n" + text
    assert "invalid choice" not in text.lower() and "usage:" not in text.lower(), "an argparse choice error is not the refusal:\n" + text
    assert "Traceback" not in text, "refusal must be a clear message, not a crash:\n" + text
    assert not list((tmp_path / "out").glob("**/frame_*.tif"))


def _read_tsv(path) -> list[dict[str, float]]:
    rows = []
    lines = path.read_text(encoding="utf-8").splitlines()
    cols = lines[0].split("\t")
    assert cols == ["ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP"]
    for ln in lines[1:]:
        cells = ln.split("\t")
        rec = {c: (float(v) if v.strip() else float("nan")) for c, v in zip(cols, cells)}
        rows.append(rec)
    return rows


def test_ic1396_cluster_distance(gaia_tsv_path) -> None:
    from loomsiril import parallax
    src = _read_tsv(gaia_tsv_path)
    assert len(src) == 8093
    cluster = parallax.find_cluster(src, {"ra": 324.745, "dec": 57.514}, parallax.cluster_radius(85 / 60))
    assert cluster is not None
    d = cluster["distance"] if isinstance(cluster, dict) else cluster.distance
    assert 870 <= d <= 1000, d
