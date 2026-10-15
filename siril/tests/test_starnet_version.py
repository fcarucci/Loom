"""The StarNet2 minimum version and the tolerant ``--version`` reader."""

from __future__ import annotations

import pytest

from loomsiril.starnet_version import MIN_STARNET_VERSION, parse_starnet_version, starnet_version_problem


def test_the_minimum_is_2_6_2() -> None:
    assert MIN_STARNET_VERSION == (2, 6, 2)


@pytest.mark.parametrize("text, expected", [
    ("starnet2 version: 2.5.2", (2, 5, 2)),
    ("StarNet++ v2.6", (2, 6, 0)),
    ("  StarNet2 2.10.1 (build 7)", (2, 10, 1)),
    ("no version here", None),
    ("", None),
])
def test_parse(text, expected) -> None:
    assert parse_starnet_version(text) == expected


def test_problem_text() -> None:
    assert starnet_version_problem("starnet2 version: 2.6.2") is None
    assert starnet_version_problem("starnet2 version: 2.6.3") is None
    assert starnet_version_problem("starnet2 version: 3.0") is None
    assert starnet_version_problem("starnet2 version: 2.5.2") == (
        "StarNet2 2.5.2 is too old: Loom needs 2.6.2 or newer (older builds cannot read FITS)")
    assert "could not be read" in starnet_version_problem("garbage")
