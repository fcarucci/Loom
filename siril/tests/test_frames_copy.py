"""The Frame Selector's copy-out: byte-identical FITS copies, Siril-read sources written as FITS, emptying the output
folder, the refusals, and the per-frame outcomes. Runs on tmp_path trees with a stub for the Siril connection, so it
needs neither Siril nor PixInsight.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from loomsiril import frames_copy as C
from loomsiril import io_fits


def _fits(path: Path, value: float = 0.25) -> Path:
    """A small real FITS file, written by io_fits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    io_fits.write_fits(path, [np.full((4, 6), value, dtype=np.float32)], {"OBJECT": "M31", "EXPTIME": 120.0})
    return path


class StubSiril:
    """Stands in for the Siril connection: load_image_from_file returns the pixels and header given for a path."""

    def __init__(self, frames: dict[str, tuple[np.ndarray, str]] | None = None, fail: str | None = None) -> None:
        self.frames = frames or {}
        self.fail = fail
        self.loaded: list[str] = []

    def load_image_from_file(self, path: str, with_pixels: bool = False) -> Any:
        assert with_pixels is True
        self.loaded.append(path)
        if self.fail is not None:
            raise RuntimeError(self.fail)
        data, header = self.frames[os.path.basename(path)]
        return SimpleNamespace(data=data, header=header)


def _folds_case(folder: Path) -> bool:
    """Does the file system under ``folder`` fold case (default APFS on macOS, NTFS)? Probed with a real file."""
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / "case-probe"
    probe.write_bytes(b"")
    try:
        return (folder / "CASE-PROBE").exists()
    finally:
        probe.unlink()


def _strict_alias_samefile(alias_of: str) -> Any:
    """os.path.samefile, except that ``alias_of`` is also the same directory as every other existing directory (a
    simulated bind mount, standing in even before the destination is created). Like the real one it fails when the
    folder it is compared with does not exist, so a caller that hands it a spelling of a folder that is not on disk (a
    case-folded one on a case-sensitive volume) gets no match."""
    real_samefile = os.path.samefile

    def samefile(x: str, y: str) -> bool:
        os.stat(y)
        if os.path.realpath(y) == alias_of:
            return True
        return real_samefile(x, y)

    return samefile


def _names(folder: Path) -> list[str]:
    return sorted(os.listdir(folder))


# ---- writing one frame -------------------------------------------------------------------------------------

def test_fits_copy_is_byte_identical_and_keeps_the_original(tmp_path: Path) -> None:
    src = _fits(tmp_path / "src" / "a.fit")
    dst = tmp_path / "out" / "a.fit"
    dst.parent.mkdir()
    before = src.read_bytes()
    os.utime(src, (1_600_000_000, 1_600_000_000))

    C.write_output(str(src), str(dst))

    assert dst.read_bytes() == before
    assert src.read_bytes() == before
    assert dst.stat().st_mtime == pytest.approx(1_600_000_000)


@pytest.mark.parametrize("name", ["a.fits", "a.fts", "a.FIT", "a.fits.fz"])
def test_fits_names_and_compressed_sources_are_copied_as_they_are(tmp_path: Path, name: str) -> None:
    src = tmp_path / "src" / name
    src.parent.mkdir()
    src.write_bytes(b"\x1f\x8b opaque tile-compressed bytes " * 50)
    dst = tmp_path / "out" / name
    dst.parent.mkdir()

    C.write_output(str(src), str(dst))

    assert dst.read_bytes() == src.read_bytes()
    assert C.is_fits_source(str(src))


def test_xisf_source_is_read_through_siril_and_written_as_fit(tmp_path: Path) -> None:
    pixels = (np.arange(12, dtype=np.uint16).reshape(3, 4) * 1000)
    siril = StubSiril({"a.xisf": (pixels, "OBJECT  = 'M42'\nEXPTIME =                 30.0\nCRPIX1  =                   2.0\n")})
    src = str(tmp_path / "src" / "a.xisf")
    dst = tmp_path / "out" / "a.fit"
    dst.parent.mkdir()

    C.write_output(src, str(dst), siril)

    assert siril.loaded == [src]
    planes, header = io_fits.read_fits(dst)
    assert len(planes) == 1
    # read_fits returns top-down rows and scales uint16 by 65535: the stored values are the pixels, row-flipped.
    np.testing.assert_array_equal(np.rint(planes[0] * 65535).astype(np.int64), pixels[::-1].astype(np.int64))
    assert header["OBJECT"] == "M42"
    assert header["EXPTIME"] == pytest.approx(30.0)


def test_xisf_float_source_is_written_as_32_bit_float(tmp_path: Path) -> None:
    pixels = np.linspace(0.0, 1.0, 20, dtype=np.float32).reshape(4, 5)
    siril = StubSiril({"f.xisf": (pixels, "")})
    dst = tmp_path / "f.fit"

    C.write_output(str(tmp_path / "f.xisf"), str(dst), siril)

    planes, _ = io_fits.read_fits(dst)
    np.testing.assert_array_equal(planes[0], pixels[::-1])


def test_xisf_source_without_a_connection_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        C.write_output(str(tmp_path / "a.xisf"), str(tmp_path / "a.fit"), None)


def test_export_names_follow_the_source(tmp_path: Path) -> None:
    fits = _fits(tmp_path / "in" / "a.fits")
    siril = StubSiril({"b.xisf": (np.zeros((2, 2), dtype=np.uint16), "")})
    xisf = str(tmp_path / "in" / "b.xisf")
    dest = tmp_path / "out"

    result = C.export_approved([str(fits), xisf], str(dest), siril, home=str(tmp_path / "home"))

    assert result["refused"] is None
    assert _names(dest) == ["a.fits", "b.fit"]
    assert result["outcomes"] == {str(fits): "written", xisf: "written"}


# ---- emptying the output folder ---------------------------------------------------------------------------

def test_destination_is_emptied_first_hidden_entries_subfolders_and_symlinks_included(tmp_path: Path) -> None:
    src = _fits(tmp_path / "in" / "a.fit")
    dest = tmp_path / "out"
    (dest / "deep" / "deeper").mkdir(parents=True)
    (dest / "deep" / "deeper" / "old.fit").write_bytes(b"old")
    (dest / ".hidden").write_bytes(b"hidden")
    (dest / "old.fit").write_bytes(b"old")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"not ours")
    os.symlink(outside, dest / "link")

    result = C.export_approved([str(src)], str(dest), None, home=str(tmp_path / "home"))

    assert result["refused"] is None
    assert result["emptied"] == 6  # .hidden, old.fit, link, and deep with its folder deeper and deeper's old.fit
    assert _names(dest) == ["a.fit"]
    assert (outside / "keep.txt").read_bytes() == b"not ours"
    assert _names(outside) == ["keep.txt"]


def test_empty_directory_removes_a_link_without_following_it(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.fit").write_bytes(b"x")
    folder = tmp_path / "folder"
    folder.mkdir()
    os.symlink(outside, folder / "ln")

    out = C.empty_directory(str(folder))

    assert out == {"removed": 1, "failed": []}
    assert _names(folder) == []
    assert _names(outside) == ["x.fit"]


def test_a_folder_that_cannot_be_emptied_refuses_and_nothing_is_copied(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _fits(tmp_path / "in" / "a.fit")
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "stuck.fit").write_bytes(b"stuck")
    (dest / "loose.fit").write_bytes(b"loose")
    real_unlink = C._unlink

    def refuses_stuck(path: str) -> None:
        if path.endswith("stuck.fit"):
            raise PermissionError(13, "denied", path)
        real_unlink(path)

    monkeypatch.setattr(C, "_unlink", refuses_stuck)

    result = C.export_approved([str(src)], str(dest), None, home=str(tmp_path / "home"))

    assert result["refused"] is not None and "could not empty the output folder" in result["refused"]
    assert "nothing was copied" in result["refused"]
    assert result["written"] == 0 and result["outcomes"] == {}
    assert not (dest / "a.fit").exists()
    assert (dest / "stuck.fit").read_bytes() == b"stuck"
    assert not (dest / "loose.fit").exists()


# ---- refusals ------------------------------------------------------------------------------------------

def _frames_in(tmp_path: Path) -> list[str]:
    return [str(_fits(tmp_path / "frames" / "a.fit")), str(_fits(tmp_path / "frames" / "b.fit", 0.5))]


def test_refuses_the_source_folder_itself_without_touching_it(tmp_path: Path) -> None:
    frames = _frames_in(tmp_path)
    before = _names(tmp_path / "frames")

    result = C.export_approved(frames, str(tmp_path / "frames"), None, home=str(tmp_path / "home"))

    assert result["refused"] == "the destination is one of the source folders"
    assert _names(tmp_path / "frames") == before


def test_refuses_the_parent_of_a_source_folder(tmp_path: Path) -> None:
    frames = _frames_in(tmp_path)
    (tmp_path / "keep.txt").write_bytes(b"keep")

    result = C.export_approved(frames, str(tmp_path), None, home=str(tmp_path / "home"))

    assert result["refused"] is not None and "contains the frames being copied" in result["refused"]
    assert (tmp_path / "keep.txt").read_bytes() == b"keep"
    assert result["emptied"] == 0


def test_refuses_the_filesystem_root(tmp_path: Path) -> None:
    result = C.export_approved(_frames_in(tmp_path), "/", None, home=str(tmp_path / "home"))
    assert result["refused"] == "refusing to empty the filesystem root"


def test_refuses_the_home_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "notes.txt").write_bytes(b"mine")
    monkeypatch.setenv("HOME", str(home))

    result = C.export_approved(_frames_in(tmp_path), str(home), None)

    assert result["refused"] == "refusing to empty the home folder"
    assert (home / "notes.txt").read_bytes() == b"mine"


def test_refuses_when_two_frames_would_share_a_name(tmp_path: Path) -> None:
    a = _fits(tmp_path / "one" / "a.fit")
    b = _fits(tmp_path / "two" / "a.fit", 0.9)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "survivor.fit").write_bytes(b"survivor")

    result = C.export_approved([str(a), str(b)], str(dest), None, home=str(tmp_path / "home"))

    assert result["refused"] == "two or more frames would be written to the same name: a.fit"
    assert _names(dest) == ["survivor.fit"]


def test_a_fits_and_an_xisf_that_both_become_a_fit_collide(tmp_path: Path) -> None:
    a = _fits(tmp_path / "one" / "a.fit")
    x = tmp_path / "two" / "a.xisf"
    x.parent.mkdir()
    x.write_bytes(b"xisf")
    dest = tmp_path / "out"
    dest.mkdir()

    result = C.export_approved([str(a), str(x)], str(dest), StubSiril(), home=str(tmp_path / "home"))

    assert result["refused"] == "two or more frames would be written to the same name: a.fit"
    assert _names(dest) == []


def test_a_fits_and_a_fits_with_another_extension_do_not_collide(tmp_path: Path) -> None:
    a = _fits(tmp_path / "one" / "a.fit")
    b = _fits(tmp_path / "two" / "a.fits")
    dest = tmp_path / "out"

    result = C.export_approved([str(a), str(b)], str(dest), None, home=str(tmp_path / "home"))

    assert result["refused"] is None
    assert _names(dest) == ["a.fit", "a.fits"]


def test_no_destination_or_no_frames_does_nothing(tmp_path: Path) -> None:
    assert C.export_approved([], str(tmp_path / "out"))["refused"] is None
    assert C.export_approved(_frames_in(tmp_path), "  ", None)["refused"] == "no output folder chosen"
    assert C.export_approved(_frames_in(tmp_path), None, None)["refused"] == "no output folder chosen"
    assert not (tmp_path / "out").exists()


# ---- outcomes ------------------------------------------------------------------------------------------

def test_a_frame_the_routine_did_not_write_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frames = _frames_in(tmp_path)

    def writes_first_only(sources: list[str], mapping: dict[str, str], siril: Any = None) -> bool:
        C.write_output(sources[0], mapping[sources[0]], siril)
        return True

    monkeypatch.setattr(C, "run_output_routine", writes_first_only)
    dest = tmp_path / "out"

    result = C.export_approved(frames, str(dest), None, home=str(tmp_path / "home"))

    assert result["refused"] is None
    assert result["outcomes"] == {frames[0]: "written", frames[1]: "missing"}
    assert (result["written"], result["failed"]) == (1, 1)


def test_a_failed_read_is_the_refusal_and_has_no_outcomes(tmp_path: Path) -> None:
    x = tmp_path / "in" / "a.xisf"
    x.parent.mkdir()
    x.write_bytes(b"xisf")
    dest = tmp_path / "out"

    result = C.export_approved([str(x)], str(dest), StubSiril(fail="siril went away"), home=str(tmp_path / "home"))

    assert result["refused"] == "siril went away"
    assert result["outcomes"] == {str(x): "missing"}


# ---- convert in place: not done by the Siril port ------------------------------------------------------

def test_convert_in_place_is_a_no_op_that_leaves_the_frames_as_they_are(tmp_path: Path) -> None:
    fits = _fits(tmp_path / "in" / "a.fit")
    xisf = tmp_path / "in" / "b.xisf"
    xisf.write_bytes(b"xisf bytes")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (fits, xisf)}

    result = C.convert_in_place([str(fits), str(xisf)])

    assert result == {"converted": 0, "failed": 0, "alreadyXisf": 0, "refused": None, "outcomes": {}}
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (fits, xisf)} == before
    assert not (tmp_path / "in" / "a.xisf").exists()
    assert C.convert_in_place(None)["outcomes"] == {}


def test_the_module_docstring_names_the_ported_functions_and_the_deviation() -> None:
    doc = C.__doc__ or ""
    for name in ["runOutputRoutine", "convertInPlace", "convertGroup", "settleConverted", "exportApproved",
                 "emptyDirectory", "removeEntry", "exportRefusal", "confirmWritten"]:
        assert name in doc
    assert "FrameSelector.js" in doc
    assert "3954-3981" in doc and "4211" in doc
    assert "NOT ported" in doc


# ---- the safety layer: resolved paths, links, case, root spellings -------------------------------------

def _sources_bytes(frames: list[str]) -> dict[str, bytes]:
    return {f: Path(f).read_bytes() for f in frames}


SOURCE_SPELLINGS = {
    "parent-dotdot": lambda t, f: str(t / "out" / ".." / "frames"),
    "dot-segment": lambda t, f: str(f) + "/.",
    "double-slash": lambda t, f: "/" + str(f),
    "trailing-slash": lambda t, f: str(f) + "/",
    "parent-of-source": lambda t, f: str(f) + "/..",
    "source-parent": lambda t, f: str(t),
}


@pytest.mark.parametrize("spelling", sorted(SOURCE_SPELLINGS))
def test_resolved_spellings_of_a_source_folder_or_its_parent_are_refused(tmp_path: Path, spelling: str) -> None:
    frames = _frames_in(tmp_path)
    before = _sources_bytes(frames)

    result = C.export_approved(frames, SOURCE_SPELLINGS[spelling](tmp_path, tmp_path / "frames"), None,
                               home=str(tmp_path / "home"))

    assert result["refused"] is not None
    assert _sources_bytes(frames) == before


def test_a_symbolic_link_to_the_source_folder_or_its_parent_is_refused(tmp_path: Path) -> None:
    frames = _frames_in(tmp_path)
    before = _sources_bytes(frames)
    os.symlink(tmp_path / "frames", tmp_path / "to_source")
    os.symlink(tmp_path, tmp_path / "to_parent")

    for dest in (tmp_path / "to_source", tmp_path / "to_parent"):
        result = C.export_approved(frames, str(dest), None, home=str(tmp_path / "home"))
        assert result["refused"] is not None and "symbolic link" in result["refused"]
    assert _sources_bytes(frames) == before
    assert _names(tmp_path / "frames") == ["a.fit", "b.fit"]


@pytest.mark.parametrize("root", ["/", "/.", "/..", "//", "/usr/.."])
def test_root_spellings_are_refused(tmp_path: Path, root: str) -> None:
    frames = _frames_in(tmp_path)
    before = _sources_bytes(frames)

    result = C.export_approved(frames, root, None, home=str(tmp_path / "home"))

    assert result["refused"] is not None
    assert _sources_bytes(frames) == before


def test_the_home_folder_by_symlink_or_dot_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "realhome"
    home.mkdir()
    (home / "notes.txt").write_bytes(b"mine")
    link = tmp_path / "homelink"
    os.symlink(home, link)
    monkeypatch.setenv("HOME", str(link))
    frames = _frames_in(tmp_path)

    for dest in (str(home), str(link), str(home) + "/.", str(home) + "//"):
        assert C.export_approved(frames, dest, None)["refused"] is not None
    result = C.export_approved(frames, str(home) + "/.", None, home=str(link))
    assert result["refused"] == "refusing to empty the home folder"
    assert (home / "notes.txt").read_bytes() == b"mine"


def test_a_destination_that_contains_the_home_folder_is_refused(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "notes").mkdir(parents=True)
    (home / "notes" / "x.txt").write_bytes(b"x")

    result = C.export_approved(_frames_in(tmp_path), str(tmp_path), None, home=str(home))

    assert result["refused"] is not None
    assert (home / "notes" / "x.txt").read_bytes() == b"x"


def test_a_relative_destination_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frames = _frames_in(tmp_path)
    before = _sources_bytes(frames)
    monkeypatch.chdir(tmp_path / "frames")

    for dest in ("frames", ".", "./", "../frames", "out"):
        result = C.export_approved(frames, dest, None, home=str(tmp_path / "home"))
        assert result["refused"] == "the output folder must be an absolute path", dest
    assert _sources_bytes(frames) == before


def test_relative_source_paths_with_an_absolute_destination_are_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _frames_in(tmp_path)
    monkeypatch.chdir(tmp_path)
    frames = ["frames/a.fit", "frames/b.fit"]
    before = _sources_bytes(frames)

    result = C.export_approved(frames, str(tmp_path / "frames"), None, home=str(tmp_path / "home"))

    assert result["refused"] is not None
    assert _sources_bytes(frames) == before


def test_case_different_spellings_are_refused_where_case_folds(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "_CASE_FOLD", True)
    frames = _frames_in(tmp_path)
    before = _sources_bytes(frames)
    folder = str(tmp_path / "frames")

    result = C.export_approved(frames, folder.upper(), None, home=str(tmp_path / "home"))

    assert result["refused"] is not None
    assert _sources_bytes(frames) == before


def test_output_names_collide_case_folded_where_case_folds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(C, "_CASE_FOLD", True)
    a = _fits(tmp_path / "one" / "a.fit")
    b = _fits(tmp_path / "two" / "A.fit", 0.9)
    dest = tmp_path / "out"
    result = C.export_approved([str(a), str(b)], str(dest), None, home=str(tmp_path / "home"))
    assert result["refused"] == "two or more frames would be written to the same name: A.fit"

    x = tmp_path / "three" / "a.xisf"
    x.parent.mkdir()
    x.write_bytes(b"xisf")
    c = _fits(tmp_path / "four" / "a.FIT", 0.1)
    result = C.export_approved([str(c), str(x)], str(dest), StubSiril(), home=str(tmp_path / "home"))
    assert result["refused"] is not None and "two or more frames" in result["refused"]


def test_output_names_differing_only_in_case_stay_distinct_where_case_is_kept(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if _folds_case(tmp_path / "probe"):
        pytest.skip("this file system folds case: a.fit and A.fit are one file here (default APFS on macOS)")
    monkeypatch.setattr(C, "_CASE_FOLD", False)
    a = _fits(tmp_path / "one" / "a.fit")
    b = _fits(tmp_path / "two" / "A.fit", 0.9)
    dest = tmp_path / "out"

    result = C.export_approved([str(a), str(b)], str(dest), None, home=str(tmp_path / "home"))

    assert result["refused"] is None
    assert _names(dest) == ["A.fit", "a.fit"]


def test_names_differing_only_in_case_are_refused_where_the_destination_folds_case(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux (no folding by platform) writing to an exFAT card or a mounted APFS/SMB share: the probe says the output
    folder folds case, so a.fit and A.fit would be one file. Refused before the folder is emptied."""
    monkeypatch.setattr(C, "_CASE_FOLD", False)
    probed: list[str] = []
    monkeypatch.setattr(C, "folds_case", lambda folder: probed.append(folder) or True)
    a = _fits(tmp_path / "one" / "a.fit")
    b = _fits(tmp_path / "two" / "A.fit", 0.9)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "old.fit").write_bytes(b"old")

    result = C.export_approved([str(a), str(b)], str(dest), None, home=str(tmp_path / "home"))

    assert result["refused"] == ("two or more frames would be written to the same name (the output folder's file "
                                 "system ignores letter case): A.fit")
    assert probed == [str(dest)]
    assert _names(dest) == ["old.fit"]
    assert (result["written"], result["failed"]) == (0, 0)


def test_names_differing_only_in_case_are_written_where_the_destination_keeps_case(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if _folds_case(tmp_path / "probe"):
        pytest.skip("this file system folds case: a.fit and A.fit are one file here (default APFS on macOS)")
    monkeypatch.setattr(C, "_CASE_FOLD", False)
    monkeypatch.setattr(C, "folds_case", lambda folder: False)
    a = _fits(tmp_path / "one" / "a.fit")
    b = _fits(tmp_path / "two" / "A.fit", 0.9)

    result = C.export_approved([str(a), str(b)], str(tmp_path / "out"), None, home=str(tmp_path / "home"))

    assert result["refused"] is None
    assert (result["written"], result["failed"]) == (2, 0)


def test_the_case_probe_finds_a_folding_file_system_and_leaves_nothing_behind(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "kept.fit").write_bytes(b"kept")
    assert C.folds_case(str(folder)) is _folds_case(tmp_path / "probe")
    assert _names(folder) == ["kept.fit"]

    # A folding file system, simulated: any spelling of an existing name is that file.
    real_samefile = os.path.samefile

    def folding_samefile(x: str, y: str) -> bool:
        def on_disk(p: str) -> str:
            d, n = os.path.split(p)
            for e in os.listdir(d):
                if e.casefold() == n.casefold():
                    return os.path.join(d, e)
            return p
        return real_samefile(on_disk(x), on_disk(y))

    monkeypatch.setattr(os.path, "samefile", folding_samefile)
    assert C.folds_case(str(folder)) is True
    assert _names(folder) == ["kept.fit"]


def test_a_case_probe_that_cannot_be_made_answers_folding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*a: Any, **k: Any) -> int:
        raise PermissionError("read-only")

    monkeypatch.setattr(os, "open", refuse)
    assert C.folds_case(str(tmp_path)) is True


def test_outputs_that_are_one_file_on_disk_report_the_overwritten_frames_missing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe missed (or could not see) a folding file system: a.fit and A.fit land in one file, the later write
    replacing the earlier one. Simulated with a hard link: the outputs share device and inode. Only the frame written
    last is reported written."""
    monkeypatch.setattr(C, "_CASE_FOLD", False)
    monkeypatch.setattr(C, "folds_case", lambda folder: False)
    a = _fits(tmp_path / "one" / "a.fit")
    b = _fits(tmp_path / "two" / "A.fit", 0.9)
    c = _fits(tmp_path / "three" / "c.fit", 0.1)

    def folding_writes(sources: list[str], mapping: dict[str, str], siril: Any = None) -> bool:
        first = mapping[sources[0]]
        for src in sources:
            target = first if src == sources[1] else mapping[src]
            C.write_output(src, target, siril)
            if src == sources[1] and not os.path.exists(mapping[src]):  # already one file where case folds (APFS)
                os.link(first, mapping[src])
        return True

    monkeypatch.setattr(C, "run_output_routine", folding_writes)

    result = C.export_approved([str(a), str(b), str(c)], str(tmp_path / "out"), None, home=str(tmp_path / "home"))

    assert result["refused"] is None
    assert result["outcomes"] == {str(a): "missing", str(b): "written", str(c): "written"}
    assert (result["written"], result["failed"]) == (2, 1)


def test_a_junction_is_removed_as_a_link_and_its_target_is_not_emptied(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "folder"
    (folder / "jn").mkdir(parents=True)
    (folder / "jn" / "kept.fit").write_bytes(b"kept")
    monkeypatch.setattr(C, "_is_junction", lambda p: p.endswith(os.sep + "jn"))

    out = C.empty_directory(str(folder))

    assert out["failed"] == [str(folder / "jn")]
    assert (folder / "jn" / "kept.fit").read_bytes() == b"kept"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_a_real_junction_is_removed_as_a_link_on_windows(tmp_path: Path) -> None:
    import subprocess

    target = tmp_path / "target"
    target.mkdir()
    (target / "kept.fit").write_bytes(b"kept")
    folder = tmp_path / "folder"
    folder.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(folder / "jn"), str(target)], check=True)

    out = C.empty_directory(str(folder))

    assert out["failed"] == []
    assert _names(folder) == []
    assert (target / "kept.fit").read_bytes() == b"kept"


def test_row_order_and_checksum_keywords_are_not_copied(tmp_path: Path) -> None:
    header = "OBJECT  = 'M1'\nROWORDER= 'BOTTOM-UP'\nCHECKSUM= 'abc'\nDATASUM = '12'\n"
    siril = StubSiril({"a.xisf": (np.zeros((2, 3), dtype=np.uint16), header)})
    dst = tmp_path / "a.fit"

    C.write_output(str(tmp_path / "a.xisf"), str(dst), siril)

    _, out = io_fits.read_fits(dst)
    assert out["OBJECT"] == "M1"
    for key in ("ROWORDER", "CHECKSUM", "DATASUM"):
        assert key not in out


def test_a_source_that_cannot_be_read_is_refused_with_its_path(tmp_path: Path) -> None:
    class NoImage:
        def load_image_from_file(self, path: str, with_pixels: bool = False) -> None:
            return None

    x = str(tmp_path / "in" / "a.xisf")
    with pytest.raises(RuntimeError, match="could not read "):
        C.write_output(x, str(tmp_path / "a.fit"), NoImage())

    result = C.export_approved([x], str(tmp_path / "out"), NoImage(), home=str(tmp_path / "home"))
    assert result["refused"] == "could not read " + x
    assert result["outcomes"] == {x: "missing"}


def test_a_failure_part_way_keeps_the_outputs_already_written(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    frames = _frames_in(tmp_path)

    def writes_first_then_fails(sources: list[str], mapping: dict[str, str], siril: Any = None) -> bool:
        C.write_output(sources[0], mapping[sources[0]], siril)
        raise RuntimeError("boom")

    monkeypatch.setattr(C, "run_output_routine", writes_first_then_fails)

    result = C.export_approved(frames, str(tmp_path / "out"), None, home=str(tmp_path / "home"))

    assert result["refused"] == "boom"
    assert result["outcomes"] == {frames[0]: "written", frames[1]: "missing"}
    assert (result["written"], result["failed"]) == (1, 1)


@pytest.mark.parametrize("ancestor_depth", [2, 3])
def test_an_alias_of_a_source_ancestor_is_refused_and_sources_survive(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ancestor_depth: int) -> None:
    """The destination is a second name for the grandparent (depth 2) or a higher ancestor (depth 3) of the source
    folder. samefile is simulated: it says the two are the same directory."""
    folder = tmp_path / "a" / "b" / "frames"
    a = _fits(folder / "a.fit")
    b = _fits(folder / "b.fit", 0.5)
    frames = [str(a), str(b)]
    before = _sources_bytes(frames)
    alias_of = os.path.realpath(str(folder.parents[ancestor_depth - 1]))
    monkeypatch.setattr(os.path, "samefile", _strict_alias_samefile(alias_of))
    dest = tmp_path / "other"

    result = C.export_approved(frames, str(dest), None, home=str(tmp_path / "home"))

    assert result["refused"] == "the output folder is the same folder as " + alias_of
    assert _sources_bytes(frames) == before
    assert not dest.exists()


@pytest.mark.parametrize("case_fold", [False, True])
def test_an_alias_of_a_mixed_case_ancestor_is_refused_on_a_case_sensitive_volume(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case_fold: bool) -> None:
    """macOS (case_fold True) with the frames on a case-sensitive volume, which the Linux tmp folder stands in for:
    the alias check must hand samefile the folders as they are spelled on disk, not their case-folded comparison keys
    (".../astro" does not exist there, so it never matched and the alias was let through). The refusal names the
    folder in its own case."""
    folder = tmp_path / "Astro" / "M31" / "frames"
    frames = [str(_fits(folder / "a.fit")), str(_fits(folder / "b.fit", 0.5))]
    before = _sources_bytes(frames)
    alias_of = os.path.realpath(str(tmp_path / "Astro"))
    monkeypatch.setattr(C, "_CASE_FOLD", case_fold)
    monkeypatch.setattr(os.path, "samefile", _strict_alias_samefile(alias_of))
    dest = tmp_path / "other"

    result = C.export_approved(frames, str(dest), None, home=str(tmp_path / "home"))

    assert result["refused"] == "the output folder is the same folder as " + alias_of
    assert _sources_bytes(frames) == before
    assert not dest.exists()


@pytest.mark.parametrize("case_fold", [False, True])
def test_frames_under_a_symlinked_root_are_reported_by_their_resolved_path(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case_fold: bool) -> None:
    """macOS keeps temporary and user folders under /var, a symbolic link to /private/var: the frames are named
    through the link, the checks run on the resolved folders and the refusal names the folder as resolved, in its
    own case. Simulated with tmp/var -> tmp/private/var."""
    (tmp_path / "private" / "var").mkdir(parents=True)
    (tmp_path / "var").symlink_to(tmp_path / "private" / "var", target_is_directory=True)
    folder = tmp_path / "var" / "Folders" / "frames"
    frames = [str(_fits(folder / "a.fit")), str(_fits(folder / "b.fit", 0.5))]
    alias_of = os.path.realpath(str(tmp_path / "var" / "Folders"))
    assert alias_of.startswith(os.path.realpath(str(tmp_path / "private")))
    monkeypatch.setattr(C, "_CASE_FOLD", case_fold)
    monkeypatch.setattr(os.path, "samefile", _strict_alias_samefile(alias_of))

    result = C.export_approved(frames, str(tmp_path / "other"), None, home=str(tmp_path / "home"))
    assert result["refused"] == "the output folder is the same folder as " + alias_of

    # The destination spelled through the resolved path, the frames through the link: only the resolved check sees it.
    inside = tmp_path / "private" / "var" / "Folders"
    result = C.export_approved(frames, str(inside), None, home=str(tmp_path / "home"))
    assert result["refused"] == ("the output folder contains the frames being copied ("
                                 + os.path.realpath(str(folder)) + ")")


def _stat_with_inode_zero(paths: set[str]) -> Any:
    """os.stat, except that the given paths report st_ino 0 as some network and FUSE mounts do."""
    real_stat = os.stat

    def stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        st = real_stat(path, *args, **kwargs)
        if os.fspath(path) in paths:
            fields = list(st)
            fields[1] = 0  # st_ino
            return os.stat_result(fields)
        return st

    return stat


def test_outputs_on_a_file_system_without_inode_numbers_are_all_reported_written(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    one = tmp_path / "one.fit"
    two = tmp_path / "two.fit"
    one.write_bytes(b"1")
    two.write_bytes(b"2")
    mapping = {"a": str(one), "b": str(two)}
    monkeypatch.setattr(C.os, "stat", _stat_with_inode_zero(set(mapping.values())))
    result: dict[str, Any] = {"outcomes": {}, "written": 0, "failed": 0}

    C.confirm_written(["a", "b"], {"mapping": mapping}, result)

    assert result["outcomes"] == {"a": "written", "b": "written"}
    assert (result["written"], result["failed"]) == (2, 0)
