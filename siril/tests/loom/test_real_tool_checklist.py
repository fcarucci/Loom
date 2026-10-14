"""The real-tool checklist (docs/loom-real-tool-checklist.md): every numbered item is present, in order, with a result
table, and each item names the things its check must cover (so a dropped step fails here)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CHECKLIST = Path(__file__).resolve().parents[2] / "docs" / "loom-real-tool-checklist.md"
ITEM_HEADING = re.compile(r"^### (\d+)\. \S", re.M)

# one keyword set per item: each keyword must appear (case-insensitively) in that item's section
KEYWORDS: dict[int, tuple[str, ...]] = {
    1: ("App Paths", "Contents/MacOS", "Locate", "default location"),
    2: ("--list-models", "--help", "starnet2 --version", "--json"),
    3: ("detail", "float32", "exit codes"),
    4: ("512", "SyQonStarless", "prism_cli", "parallax_cli", "finite", "row"),
    5: ("77", "exit 4", "not licensed"),
    6: ("Keychain",),
    7: ("spaces", "260", "console window", "backslash"),
    8: ("taskkill", "/T /F", "workers"),
    9: ("worker thread", "Cancel", "deadlock", "second run"),
    10: ("is_cli", "pyscript", "-smoothing=0.50", "script folder"),
    11: ("SPCC database",),
    12: ("-monosensor", "Argument error", "fresh session"),
    13: ("PSB", "Photoshop", "blend modes", "profile"),
    14: ("tests/fixtures/nbnorm", "tests/loom/test_nbnorm.py", "0.02", "not skip", "palette.txt"),
    15: ("licence", "Decline", "Accept", "remembered", "revision"),
    16: ("SIP", "ok", "poor", "residual", "0.315"),
}


def _text() -> str:
    return CHECKLIST.read_text(encoding="utf-8")


def _sections() -> dict[int, str]:
    parts = re.split(r"^### (\d+)\. ", _text(), flags=re.M)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def test_checklist_has_each_numbered_item_in_order() -> None:
    numbers = [int(n) for n in ITEM_HEADING.findall(_text())]
    assert numbers == list(range(1, 17)), numbers


def test_each_item_has_a_result_table_with_the_four_columns() -> None:
    sections = _sections()
    assert sorted(sections) == list(range(1, 17))
    for number, section in sections.items():
        assert "| OS | Tool version |" in section, number
        assert "| Date |" in section, number


@pytest.mark.parametrize("number", sorted(KEYWORDS))
def test_each_item_names_what_it_checks(number: int) -> None:
    section = _sections()[number].lower()
    missing = [kw for kw in KEYWORDS[number] if kw.lower() not in section]
    assert not missing, f"item {number} does not mention {missing}"


def test_item_14_commits_the_pair_and_does_not_forbid_it() -> None:
    section = _sections()[14].lower()
    assert "commit" in section
    assert "do not commit" not in section
