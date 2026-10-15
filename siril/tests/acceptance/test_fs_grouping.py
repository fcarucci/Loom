"""Folder and grouping: frames grouped by the raw FILTER string, the no-filter group, comparability, non-lights.

The header-reading tests need ``loomsiril.frames_headers`` and skip until it is on the branch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fs_helpers import ladder, channel, rejected_indices, write_light

frames = pytest.importorskip("loomsiril.frames")
groups = pytest.importorskip("loomsiril.frames_groups")


def _entry(path: str, filt, **kw) -> dict:
    e = {"path": path, "filter": filt, "exposure": "120", "binning": "1", "binningY": "1", "imageType": "Light",
         "width": 400, "height": 300, "calibrated": "unknown"}
    e.update(kw)
    return e


# ---- grouping by the raw FILTER string -----------------------------------------------------------------------------


def test_frames_are_grouped_by_the_raw_filter_string() -> None:
    """Light subs are grouped by the raw `FILTER` string ("Ha" and "HA" are different groups)."""
    es = [_entry("a", "Ha"), _entry("b", "HA"), _entry("c", "Ha"), _entry("d", "OIII"), _entry("e", "HA")]
    g = groups.group_by_filter(es)
    assert {k: [e["path"] for e in v] for k, v in g.items()} == {"Ha": ["a", "c"], "HA": ["b", "e"], "OIII": ["d"]}


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_frames_without_a_filter_form_their_own_group(missing) -> None:
    """Frames with no readable FILTER form their own group."""
    es = [_entry("a", "Ha"), _entry("b", missing), _entry("c", missing)]
    g = groups.group_by_filter(es)
    assert set(g) == {"Ha", frames.NO_FILTER}
    assert [e["path"] for e in g[frames.NO_FILTER]] == ["b", "c"]


def test_a_frame_with_the_filter_key_absent_is_in_the_no_filter_group() -> None:
    """Frames with no readable FILTER form their own group (the keyword is not in the header at all)."""
    e = _entry("a", "x")
    del e["filter"]
    assert list(groups.group_by_filter([e])) == [frames.NO_FILTER]


def test_no_filter_frames_are_never_rejected_automatically() -> None:
    """Frames with no readable filter are shown but never rejected automatically, even when terrible."""
    rows = ladder(20)
    rows[3]["fwhm"] = 12.0
    rows[9]["stars"] = 3
    assert rejected_indices(channel("Ha", rows)) == [3, 9]            # the control: a filter group rejects them
    ch = channel(frames.NO_FILTER, rows)
    assert rejected_indices(ch) == []
    assert all(r["state"] == "approved" for r in ch["rows"])
    assert groups.auto_reject_allowed(frames.NO_FILTER) is False
    assert groups.auto_reject_allowed("Ha") is True


def test_a_hand_decision_still_works_in_the_no_filter_group() -> None:
    """Never rejected automatically: the owner can still condemn one by hand."""
    ch = channel(frames.NO_FILTER, ladder(20))
    ch["rows"][4]["override"] = "condemned"
    assert frames.counts(ch["rows"])["rejected"] == 1


# ---- comparability --------------------------------------------------------------------------------------------------


def _group(**diff) -> list[dict]:
    """Three frames, the second one differing in one field."""
    es = [_entry(f"f{i}", "Ha") for i in range(3)]
    es[1].update(diff)
    return es


def test_a_uniform_group_is_comparable() -> None:
    """A group whose frames agree in exposure, binning, geometry and calibration state is comparable."""
    assert groups.comparability([_entry(f"f{i}", "Ha") for i in range(5)]) == {"uniform": True, "problems": []}


@pytest.mark.parametrize("diff", [
    {"exposure": "300"},
    {"binning": "2"},
    {"binningY": "2"},
    {"width": 800},
    {"height": 600},
    {"calibrated": "yes"},
    {"calibrated": "history"},
])
def test_a_group_that_differs_is_not_comparable(diff: dict) -> None:
    """A group whose frames differ in exposure, binning, geometry or calibration state (unknown counts as a
    mismatch) is flagged "not comparable"."""
    c = groups.comparability(_group(**diff))
    assert c["uniform"] is False
    assert len(c["problems"]) >= 1


def test_unknown_calibration_state_mixed_with_a_known_one_is_a_mismatch() -> None:
    """Calibration state (unknown counts as a mismatch)."""
    es = [_entry("a", "Ha", calibrated="yes"), _entry("b", "Ha", calibrated="unknown")]
    assert groups.comparability(es)["uniform"] is False
    # ... and the mixture is reported as a calibration-state problem
    assert any("calibration" in p for p in groups.comparability(es)["problems"])


def test_not_comparable_is_a_warning_only() -> None:
    """A channel whose frames differ is flagged as not comparable; that is a warning, and Run still acts on it
    (verdicts are still computed and a terrible frame is still rejected)."""
    es = _group(exposure="300")
    c = groups.comparability(es)
    assert not c["uniform"]
    rows = ladder(20)
    rows[8]["fwhm"] = 11.0
    ch = channel("Ha", rows, problems=c["problems"])
    assert ch["problems"] == c["problems"]
    assert rejected_indices(ch) == [8]
    assert frames.keep_count(ch["rows"], True, False) == {"keep": 19, "total": 20}


# ---- non-light frames ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name,image_type,kind", [
    ("x.fit", "Flat Field", "flat"),
    ("x.fit", "'FLAT'", "flat"),
    ("x.fit", "Dark Frame", "dark"),
    ("x.fit", "Bias Frame", "bias"),
    ("x.fit", "Master Flat", "master"),
    ("masterFlat_Ha.xisf", None, "master"),
    ("integration_L.fit", None, "master"),
    ("Flat_001.fit", None, "flat"),
    ("Dark_300s_002.fit", None, "dark"),
])
def test_calibration_frames_and_masters_are_skipped(name: str, image_type, kind: str) -> None:
    """Flats, darks, bias and masters are skipped as non-lights."""
    assert groups.not_subframe(name, image_type) == kind


@pytest.mark.parametrize("name,image_type", [
    ("NGC7000_Ha_001.fit", "Light Frame"),
    ("NGC7000_Ha_001.fit", "'LIGHT '"),
    ("NGC7000_Ha_001.fit", "light"),
    ("Light_001.fit", None),
    ("M31_L_120s_001.fit", None),
])
def test_light_frames_are_not_skipped(name: str, image_type) -> None:
    """Light subs are grouped; only calibration frames and masters are left out."""
    assert groups.not_subframe(name, image_type) is None


# ---- real files through the header reader --------------------------------------------------------------------------


def _headers():
    return pytest.importorskip("loomsiril.frames_headers")


def _entries_for(folder: Path) -> list[dict]:
    fh = _headers()
    return [fh.entry_for(p) for p in fh.frame_files_in(str(folder))]


def test_real_fits_files_group_by_filter(tmp_path: Path) -> None:
    """Frames are grouped by the raw FILTER string: files on disk with those headers give the same groups."""
    base = {"EXPTIME": 120.0, "XBINNING": 1, "YBINNING": 1, "IMAGETYP": "Light Frame"}
    spec = {"a1.fit": "Ha", "a2.fit": "Ha", "b1.fit": "HA", "c1.fit": "OIII", "n1.fit": None, "n2.fit": None}
    for name, filt in spec.items():
        write_light(tmp_path, name, dict(base, **({"FILTER": filt} if filt else {})))
    g = groups.group_by_filter(_entries_for(tmp_path))
    assert {k: sorted(Path(e["path"]).name for e in v) for k, v in g.items()} == {
        "Ha": ["a1.fit", "a2.fit"], "HA": ["b1.fit"], "OIII": ["c1.fit"], frames.NO_FILTER: ["n1.fit", "n2.fit"]}


def test_real_files_with_different_exposure_or_binning_are_not_comparable(tmp_path: Path) -> None:
    """A group whose frames differ in exposure, binning, geometry or calibration state is flagged "not comparable"."""
    base = {"FILTER": "Ha", "EXPTIME": 120.0, "XBINNING": 1, "YBINNING": 1, "IMAGETYP": "Light Frame"}
    ok, mixed = tmp_path / "ok", tmp_path / "mixed"
    for i in range(3):
        write_light(ok, f"f{i}.fit", base)
    write_light(mixed, "f0.fit", base)
    write_light(mixed, "f1.fit", dict(base, EXPTIME=300.0))
    write_light(mixed, "f2.fit", dict(base, XBINNING=2, YBINNING=2))
    assert groups.comparability(_entries_for(ok))["uniform"] is True
    c = groups.comparability(_entries_for(mixed))
    assert c["uniform"] is False
    assert {"mixed exposure times", "mixed binning"} <= set(c["problems"])


def test_real_files_with_different_geometry_are_not_comparable(tmp_path: Path) -> None:
    """A group whose frames differ in ... geometry ... is flagged "not comparable"."""
    base = {"FILTER": "Ha", "EXPTIME": 120.0, "XBINNING": 1, "YBINNING": 1, "IMAGETYP": "Light Frame"}
    write_light(tmp_path, "a.fit", base, w=16, h=12)
    write_light(tmp_path, "b.fit", base, w=20, h=12)
    assert "mixed image geometry" in groups.comparability(_entries_for(tmp_path))["problems"]


def test_real_calibrated_name_or_history_is_a_calibration_mismatch(tmp_path: Path) -> None:
    """Calibration state (unknown counts as a mismatch): a calibrated frame beside an unknown one is a mixture."""
    base = {"FILTER": "Ha", "EXPTIME": 120.0, "XBINNING": 1, "YBINNING": 1, "IMAGETYP": "Light Frame"}
    write_light(tmp_path, "a.fit", base)
    write_light(tmp_path, "b_c.fit", base)
    assert "mixed calibration state" in groups.comparability(_entries_for(tmp_path))["problems"]


def test_real_calibration_files_are_skipped_by_type_and_by_name(tmp_path: Path) -> None:
    """Flats, darks, bias and masters are skipped as non-lights (read from real headers)."""
    fh = _headers()
    write_light(tmp_path, "light1.fit", {"FILTER": "Ha", "IMAGETYP": "Light Frame"})
    write_light(tmp_path, "flat1.fit", {"FILTER": "Ha", "IMAGETYP": "Flat Field"})
    write_light(tmp_path, "dark1.fit", {"IMAGETYP": "Dark Frame"})
    write_light(tmp_path, "bias1.fit", {"IMAGETYP": "Bias Frame"})
    write_light(tmp_path, "masterFlat_Ha.fit", {"FILTER": "Ha"})
    kinds = {}
    for p in fh.frame_files_in(str(tmp_path)):
        e = fh.entry_for(p)
        kinds[Path(p).name] = groups.not_subframe(Path(p).name, e["imageType"])
    assert kinds == {"light1.fit": None, "flat1.fit": "flat", "dark1.fit": "dark", "bias1.fit": "bias",
                     "masterFlat_Ha.fit": "master"}
