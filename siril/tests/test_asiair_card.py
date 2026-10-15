"""The ASIAIR card reader (``loomsiril.asiair_card``) on fake card trees: detection, the walk, the header adapter.

The trees are built in ``tmp_path`` from empty files that carry ASIAIR names, plus one FITS header where the header
adapter is under test. Nothing here needs Siril, PixInsight, node or a display.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Optional

import pytest

from loomsiril import asiair_card as C

LIGHT = "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215716_180deg_-7.0C_0001.fit"
LIGHT2 = "Light_IC 1396A_180.0s_Bin1_2600MM_H_gain100_20260807-215816_180deg_-7.0C_0002.fit"
LIGHT_M31 = "Light_M 31_120.0s_Bin1_2600MM_L_gain100_20260807-225716_90deg_-7.0C_0001.fit"
FLAT = "Flat_180.0s_Bin1_2600MM_H_gain100_20260807-215516_0deg_-7.0C_0001.fit"
DARK = "Dark_180.0s_Bin1_2600MM_H_gain100_20260807-215416_0deg_-7.0C_0001.fit"
UNPARSEABLE = "Light_broken_name.fit"
APPLEDOUBLE = "._" + LIGHT


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def make_card(root: Path, layout: str = "Plan") -> Path:
    """A card under ``root``: lights in two target folders (one with a space), a flat and a dark, and a
    folder-level flat in the other source, so both sources are walked."""
    _touch(root / layout / "Light" / "IC 1396A" / LIGHT)
    _touch(root / layout / "Light" / "IC 1396A" / LIGHT2)
    _touch(root / layout / "Light" / "M 31" / LIGHT_M31)
    _touch(root / layout / "Flat" / FLAT)
    _touch(root / layout / "Dark" / DARK)
    return root


def make_full_card(root: Path) -> Path:
    make_card(root, "Plan")
    _touch(root / "Autorun" / "Flat" / FLAT)
    return root


def test_looks_like_card_by_shape_only(tmp_path: Path) -> None:
    plan = tmp_path / "plan"
    (plan / "Plan" / "Light").mkdir(parents=True)
    autorun = tmp_path / "autorun"
    (autorun / "Autorun" / "Light").mkdir(parents=True)
    flats_only = tmp_path / "flats"
    (flats_only / "Plan" / "Flat").mkdir(parents=True)
    file_named = tmp_path / "file"
    file_named.mkdir()
    (file_named / "Plan").write_text("")

    assert C.looks_like_card(str(plan))
    assert C.looks_like_card(str(autorun))
    assert not C.looks_like_card(str(flats_only))
    assert not C.looks_like_card(str(file_named))
    assert not C.looks_like_card(str(tmp_path / "missing"))


def test_detect_finds_only_the_card_one_level_deep(tmp_path: Path) -> None:
    media = tmp_path / "media"
    make_card(media / "EMMC Images")
    (media / "Backups" / "deep").mkdir(parents=True)
    make_card(media / "Backups" / "deep" / "nested-card")  # two levels down: not a volume
    (media / "readme.txt").parent.mkdir(parents=True, exist_ok=True)
    (media / "readme.txt").touch()
    mnt = tmp_path / "mnt"
    make_card(mnt / "USB Images", layout="Autorun")
    (mnt / "Time Machine" / "Plan" / "Light").mkdir(parents=True)  # a Plan folder with no frames is still a card
    (mnt / "Plain").mkdir()
    (mnt / ".hidden" / "Plan" / "Light").mkdir(parents=True)  # dot names are never volumes

    roots = [str(media), str(mnt), str(tmp_path / "not-mounted")]
    found = C.detect(roots)

    assert found == [
        os.path.join(str(media), "EMMC Images"),
        os.path.join(str(mnt), "Time Machine"),
        os.path.join(str(mnt), "USB Images"),
    ]


def test_detect_stops_between_volumes(tmp_path: Path) -> None:
    mnt = tmp_path / "mnt"
    for name in ("A", "B", "C"):
        make_card(mnt / name)

    calls: list[tuple[int, int, str]] = []

    def on_volume(k: int, n: int, root: str) -> None:
        calls.append((k, n, root))

    found = C.detect([str(mnt)], should_stop=lambda: len(calls) >= 1, on_volume=on_volume)

    assert found == [os.path.join(str(mnt), "A")]
    assert [c[:2] for c in calls] == [(1, 3)]


def test_detect_stop_before_start_checks_nothing(tmp_path: Path) -> None:
    mnt = tmp_path / "mnt"
    make_card(mnt / "A")
    calls: list[str] = []
    assert C.detect([str(mnt)], should_stop=lambda: True, on_volume=lambda k, n, r: calls.append(r)) == []
    assert calls == []


def test_entries_in_skips_dot_names_and_sorts(tmp_path: Path) -> None:
    d = tmp_path / "d"
    (d / "b folder").mkdir(parents=True)
    (d / "a folder").mkdir()
    (d / ".hidden").mkdir()
    _touch(d / "._x.fit")
    _touch(d / "x.fit")

    assert C.entries_in(str(d), True) == ["a folder", "b folder"]
    assert C.entries_in(str(d), False) == ["x.fit"]
    assert C.entries_in(str(tmp_path / "missing"), True) == []


def test_frame_from_names_and_targets(tmp_path: Path) -> None:
    light = C.frame_from(str(tmp_path), LIGHT, "M 31", "Plan")
    assert light is not None
    assert light["target"] == "M 31"  # the folder wins over the name, as the firmware omits the name's target
    assert light["path"] == os.path.join(str(tmp_path), LIGHT)
    assert light["source"] == "Plan"
    assert isinstance(light["key"], int)

    flat = C.frame_from(str(tmp_path), FLAT, None, "Autorun")
    assert flat is not None
    assert flat["target"] == ""  # no folder target: the name's own (empty) target is kept
    assert flat["source"] == "Autorun"

    assert C.frame_from(str(tmp_path), UNPARSEABLE, None, "Plan") is None
    impossible = LIGHT.replace("20260807-215716", "20260230-215716")
    assert C.frame_from(str(tmp_path), impossible, "IC 1396A", "Plan") is None


def test_scan_lists_lights_flats_and_darks_with_parsed_fields(tmp_path: Path) -> None:
    card = make_full_card(tmp_path / "card")
    _touch(card / "Plan" / "Light" / "IC 1396A" / UNPARSEABLE)
    _touch(card / "Plan" / "Light" / "IC 1396A" / APPLEDOUBLE)

    progress: list[int] = []
    out = C.scan_card(str(card), on_progress=progress.append)

    assert out["cancelled"] is False and out["removed"] is False
    assert [os.path.basename(f["path"]) for f in out["lights"]] == [LIGHT, LIGHT2, LIGHT_M31]
    assert [f["target"] for f in out["lights"]] == ["IC 1396A", "IC 1396A", "M 31"]
    assert {f["source"] for f in out["lights"]} == {"Plan"}
    first = out["lights"][0]
    assert (first["filter"], first["camera"], first["rotation"]) == ("H", "2600MM", "180deg")
    assert first["exposure"] == "180.0s"
    assert [os.path.basename(f["path"]) for f in out["flats"]] == [FLAT, FLAT]
    assert [f["source"] for f in out["flats"]] == ["Plan", "Autorun"]
    assert [os.path.basename(f["path"]) for f in out["darks"]] == [DARK]
    assert out["unparseable"] == [os.path.join(str(card), "Plan", "Light", "IC 1396A", UNPARSEABLE)]
    # AppleDouble twins are not frames and are not reported as unparseable either.
    assert all("._" not in f["path"] for f in out["lights"])
    # Progress is reported after every file taken (the unparseable one and the dark included), with the count of
    # lights and flats only: lights 1, 2, unparseable 2, light 3, flat 4, dark 4, Autorun flat 5.
    assert progress == [1, 2, 2, 3, 4, 4, 5]


def test_scan_of_a_missing_card_is_removed(tmp_path: Path) -> None:
    out = C.scan_card(str(tmp_path / "gone"))
    assert out["removed"] is True and out["cancelled"] is False
    assert out["lights"] == [] and out["flats"] == [] and out["darks"] == []


def test_card_removed_during_scan_reads_as_removed(tmp_path: Path) -> None:
    card = make_card(tmp_path / "card")

    def pull_the_card(n: int) -> None:
        shutil.rmtree(card)

    out = C.scan_card(str(card), on_progress=pull_the_card)

    assert out["removed"] is True
    assert out["cancelled"] is False
    assert len(out["lights"]) == 1  # the file being processed when the card went away was already read


def test_cancel_stops_the_walk(tmp_path: Path) -> None:
    card = make_card(tmp_path / "card")
    seen: list[int] = []

    def on_progress(n: int) -> None:
        seen.append(n)

    out = C.scan_card(str(card), on_progress=on_progress, should_stop=lambda: bool(seen))

    assert out["cancelled"] is True
    assert out["removed"] is False
    assert len(out["lights"]) == 1
    assert out["flats"] == [] and out["darks"] == []


def test_describe_reads_header_first_and_falls_back_to_the_name(tmp_path: Path) -> None:
    with_header = tmp_path / LIGHT
    _write_fits_header(with_header, {"FILTER": "Ha", "XBINNING": 1, "INSTRUME": "ZWO ASI2600MM Pro"})
    frame = C.frame_from(str(tmp_path), LIGHT, "IC 1396A", "Plan")
    assert frame is not None
    assert C.describe(frame) == {
        "path": str(with_header),
        "filter": "Ha",  # the header is authoritative: the name says H
        "binning": "1",
        "camera": "ZWO ASI2600MM Pro",
        "rotation": "180deg",  # the filename only: no header keyword for the rotator
    }

    no_camera = tmp_path / LIGHT2
    _write_fits_header(no_camera, {"FILTER": "H", "XBINNING": 1})
    frame2 = C.frame_from(str(tmp_path), LIGHT2, "IC 1396A", "Plan")
    assert frame2 is not None
    assert C.describe(frame2)["camera"] == "2600MM"  # INSTRUME absent: the filename token

    unreadable = tmp_path / LIGHT_M31
    unreadable.touch()
    frame3 = C.frame_from(str(tmp_path), LIGHT_M31, "M 31", "Plan")
    assert frame3 is not None
    described = C.describe(frame3)
    assert described["filter"] is None and described["binning"] is None
    assert described["camera"] == "2600MM" and described["rotation"] == "90deg"


def _write_fits_header(path: Path, values: dict[str, Any]) -> None:
    """A FITS primary header with the given keywords and no data: 80-column cards in 2880-byte blocks."""
    cards = ["SIMPLE  =                    T", "BITPIX  =                   16", "NAXIS   =                    0"]
    for key, value in values.items():
        if isinstance(value, str):
            text = f"'{value:<8}'".rjust(20)
        else:
            text = str(value).rjust(20)
        cards.append(f"{key:<8}= {text}")
    cards.append("END")
    blob = "".join(card.ljust(80) for card in cards).encode("ascii")
    blob += b" " * (-len(blob) % 2880)
    path.write_bytes(blob)


@pytest.mark.parametrize(
    ("platform", "env", "user", "expected"),
    [
        ("darwin", {}, None, ["/Volumes"]),
        ("linux", {"USER": "ada"}, None, ["/media/ada", "/run/media/ada", "/mnt"]),
        ("linux", {"USER": "ada", "LOGNAME": "bob"}, "carol", ["/media/carol", "/run/media/carol", "/mnt"]),
        ("linux", {"LOGNAME": "bob"}, None, ["/media/bob", "/run/media/bob", "/mnt"]),
        ("linux", {}, None, ["/mnt"]),
        ("freebsd13", {"USER": "ada"}, None, []),
    ],
)
def test_mount_roots_per_platform(platform: str, env: dict[str, str], user: Optional[str], expected: list[str]) -> None:
    assert C.mount_roots(platform=platform, env=env, user=user) == expected


def test_mount_roots_windows_lists_existing_drive_letters(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = {"C:\\", "D:\\", "Z:\\"}
    monkeypatch.setattr(C, "_exists_dir", lambda path: path in existing)
    assert C.mount_roots(platform="win32", env={}) == ["C:\\", "D:\\", "Z:\\"]

