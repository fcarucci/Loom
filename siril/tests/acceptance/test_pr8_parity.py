"""docs/PARITY.md names every JS function/constant of Fly/Render/Sky/FlyThrough (tests/acceptance/js_inventory.txt)."""

from __future__ import annotations

import re


from acc_helpers import REPO

INVENTORY = REPO / "tests" / "acceptance" / "js_inventory.txt"


def _names() -> list[str]:
    return [ln.strip() for ln in INVENTORY.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def test_inventory_is_sane() -> None:
    names = _names()
    assert len(names) > 300
    assert names == sorted(set(names))
    assert {"Fly.tanProject", "Render.axisWeights"} <= set(names) or any(n.startswith("Render.") for n in names)
    assert any(n.startswith("Sky.") for n in names) and any(n.startswith("FlyThrough.") for n in names)


def test_every_js_name_appears_in_parity_md() -> None:
    text = (REPO / "docs" / "PARITY.md").read_text(encoding="utf-8")
    missing = []
    for qualified in _names():
        _, bare = qualified.split(".", 1)
        # rows may list several names of one object ("Fly.vec, radec, dot"), so the bare name must appear as a token
        if not re.search(r"(?<![\w.])(?:\w+\.)?" + re.escape(bare) + r"(?!\w)", text):
            missing.append(qualified)
    assert not missing, f"{len(missing)} JS names missing from docs/PARITY.md, e.g. {missing[:15]}"
