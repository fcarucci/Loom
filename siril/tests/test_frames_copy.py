"""The Frame Selector's copy-out: byte-identical FITS copies, Siril-read sources written as FITS, emptying the output
folder, the refusals, and the per-frame outcomes. Runs on tmp_path trees with a stub for the Siril connection, so it
needs neither Siril nor PixInsight.
"""

from __future__ import annotations

import os
import stat
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


def test_a_folder_that_cannot_be_emptied_refuses_and_nothing_is_copied(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root removes files in read-only folders")
    src = _fits(tmp_path / "in" / "a.fit")
    dest = tmp_path / "out"
    locked = dest / "locked"
    locked.mkdir(parents=True)
    (locked / "stuck.fit").write_bytes(b"stuck")
    (dest / "loose.fit").write_bytes(b"loose")
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = C.export_approved([str(src)], str(dest), None, home=str(tmp_path / "home"))
    finally:
        locked.chmod(stat.S_IRWXU)

    assert result["refused"] is not None and "could not empty the output folder" in result["refused"]
    assert "nothing was copied" in result["refused"]
    assert result["written"] == 0 and result["outcomes"] == {}
    assert not (dest / "a.fit").exists()
    assert (locked / "stuck.fit").read_bytes() == b"stuck"


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
    assert result["outcomes"] == {}


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
