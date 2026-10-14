"""The Loom-main oracle set loads (tools/record_oracle.js, recorders named loom_*) and agrees with Python."""

from __future__ import annotations

import hashlib


def test_loom_smoke_oracle_matches_python(oracle) -> None:
    c = oracle("loom_smoke")["cases"]
    assert c["hash_abc"] == hashlib.sha1(b"abc").hexdigest()
    assert c["hash_utf8"] == hashlib.sha1("M42 ° Orion".encode("utf-8")).hexdigest()
    assert c["stage_order"][:5] == ["solve", "spfc", "mgc", "graxpert", "aberration"]
    assert len(c["stage_order"]) == 27
    assert "views" in c["default_keys"] and "marsPath" in c["default_keys"]
    assert dict(c["channel_from_filter"])["Ha"] == "H" and dict(c["channel_from_filter"])["xyz"] is None


def test_oracle_records_the_loom_version(oracle) -> None:
    about = oracle("loom_smoke")["about"]
    assert about["recorder"] == "tools/oracle/loom_smoke.js" and about["loom"] == "v0.4.1"
