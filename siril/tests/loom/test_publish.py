"""Publishing the final plates and exporting them (loomsiril.loom.publish), against the recording fake of Siril.

Synthetic plates are written with loomsiril.io_fits; the TIFF and PSB outputs are read back from disk.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import tifffile

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.io_tiff import ICC_TAG
from loomsiril.loom import icc, publish
from loomsiril.loom.siril_ops import RecordingOps

H, W = 8, 12
PROFILE = b"\x00\x00\x00\x0cFAKEPROFILE-BYTES" * 4  # what Siril reports for the built-in sRGB in these tests


def _plane(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).random((H, W)).astype(np.float32)


def _write_plate(folder: Path, name: str, planes: int, seed: int, shape: tuple[int, int] = (H, W)) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    data = [np.random.default_rng(seed + c).random(shape).astype(np.float32) for c in range(planes)]
    path = folder / f"{name}_plate.fits"
    write_fits(path, data, {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 10.0, "CRVAL2": 20.0,
                            "CRPIX1": shape[1] / 2, "CRPIX2": shape[0] / 2, "CDELT1": -1e-4, "CDELT2": 1e-4})
    return os.fspath(path)


def _ops(tmp_path: Path) -> RecordingOps:
    return RecordingOps(tmp_path / "siril", icc=PROFILE)


def _log() -> tuple[list[tuple[str, str]], Any]:
    lines: list[tuple[str, str]] = []

    def log(level: str, text: str) -> None:
        lines.append((level, text))

    return lines, log


def _texts(lines: list[tuple[str, str]], level: str) -> list[str]:
    return [text for lv, text in lines if lv == level]


def _builtin_plan() -> dict[str, Any]:
    return icc.profile_plan([])


def _verbs(ops: RecordingOps) -> list[str]:
    return [cmd.split(" ", 1)[0] for cmd in ops.transcript]


def _publish(tmp_path: Path, plates: dict[str, str], plan: dict[str, Any] | None = None,
             out: str = "out", config: dict[str, Any] | None = None) -> tuple[RecordingOps, dict[str, str], list]:
    ops = _ops(tmp_path)
    lines, log = _log()
    outputs = publish.publish(ops, plates, config or {}, tmp_path / out, plan or _builtin_plan(), log)
    return ops, outputs, lines


# ---- publish ---------------------------------------------------------------------------------------------------


def test_transcript_per_result_in_output_order(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb, "L": mono})
    assert list(outputs) == ["L", "RGB"]  # OUTPUT_ORDER, not dict order
    assert _verbs(ops) == ["load", "update_key", "icc_assign", "save"] * 2
    assert ops.transcript[0] == f'load "{mono}"'
    assert ops.transcript[1] == 'update_key LOOMOUT "L" "Loom pipeline output"'
    assert ops.transcript[3] == f'save "{outputs["L"]}"'
    assert ops.transcript[4] == f'load "{rgb}"'


def test_loomout_keyword_is_set_on_every_result(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    ops, _outputs, _ = _publish(tmp_path, {"L": mono})
    update = [cmd for cmd in ops.transcript if cmd.startswith("update_key")]
    assert update == ['update_key LOOMOUT "L" "Loom pipeline output"']


def test_unique_suffixes_on_a_populated_folder(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    out = tmp_path / "out"
    out.mkdir()
    (out / "L.fits").write_bytes(b"existing result")
    (out / "L_1.fits").write_bytes(b"also existing")
    _ops, outputs, _ = _publish(tmp_path, {"L": mono})
    assert outputs["L"] == os.fspath(out / "L_2.fits")
    assert (out / "L.fits").read_bytes() == b"existing result"
    assert (out / "L_1.fits").read_bytes() == b"also existing"


def test_mono_plate_gets_builtin_srgb_not_graysrgb(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    ops, _outputs, lines = _publish(tmp_path, {"L": mono})
    assert "icc_assign sRGB" in ops.transcript
    assert not any("graysrgb" in cmd.lower() for cmd in ops.transcript)
    assert _texts(lines, "warn")  # no installed profile qualifies: the built-in is used, and that is said


def test_colour_plate_uses_profile_file_from_the_plan(tmp_path: Path) -> None:
    profile_file = tmp_path / "rgb.icc"
    profile_file.write_bytes(PROFILE)
    plan = {"rgb": ["Fake RGB"], "gray": [], "paths": {"RGB": {"Fake RGB": os.fspath(profile_file)}, "GRAY": {}}}
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, _outputs, lines = _publish(tmp_path, {"RGB": rgb}, plan=plan)
    assert f'icc_assign "{profile_file}"' in ops.transcript
    assert _texts(lines, "info") == ["RGB -> Fake RGB"]
    assert _texts(lines, "warn") == []


def test_main_result_is_loaded_last(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, lines = _publish(tmp_path, {"L": mono, "RGB": rgb})
    publish.leave_main_result(ops, outputs, lines.append)
    name, args, _kw = ops.calls[-1]
    assert name == "load" and args[0] == outputs["RGB"]


def test_no_main_result_loads_nothing(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    ops, outputs, _ = _publish(tmp_path, {"L": mono})
    before = len(ops.transcript)
    publish.leave_main_result(ops, {"L_stars_low": outputs["L"]}, [].append)
    assert len(ops.transcript) == before


# ---- export: refusal, order, PSB and TIFF ----------------------------------------------------------------------


def test_tiff_refused_when_stretch_is_off(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    ops, outputs, _ = _publish(tmp_path, {"L": mono})
    export_dir = tmp_path / "export"
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": False, "exportPsb": True},
                   _builtin_plan(), log)
    assert lines == [("warn", "not exporting: the results are linear, and 16-bit TIFF would posterise them. "
                              "Enable the stretch, or save from Siril in a format that holds floating point.")]
    assert not export_dir.exists()


def test_no_export_without_a_folder(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    ops, outputs, _ = _publish(tmp_path, {"L": mono})
    before = len(ops.transcript)
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": "", "stretch": True, "exportPsb": True}, _builtin_plan(), log)
    assert lines == [] and len(ops.transcript) == before


def test_psb_written_before_tiffs(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"L": mono, "RGB": rgb})
    export_dir = tmp_path / "export"
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": True,
                                  "projectName": "My Project"}, _builtin_plan(), log)
    texts = [text for _lv, text in lines]
    psb_at = next(i for i, t in enumerate(texts) if t.startswith("wrote "))
    tiff_at = [i for i, t in enumerate(texts) if t.startswith("16-bit TIFF ")]
    assert tiff_at and psb_at < min(tiff_at)
    assert (export_dir / "My_Project.psb").read_bytes()[:4] == b"8BPS"
    assert sorted(p.name for p in export_dir.glob("*.tif")) == ["L.tif", "RGB.tif"]
    assert texts[-1] == f"2 of 2 result(s) written to {export_dir}"


def test_psb_failure_still_writes_tiffs(tmp_path: Path) -> None:
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2, shape=(H, W))
    mono = _write_plate(tmp_path / "in", "L_stars", 1, 1, shape=(H + 2, W))
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb, "L_stars": mono})
    export_dir = tmp_path / "export"
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": True},
                   _builtin_plan(), log)
    errors = _texts(lines, "error")
    assert len(errors) == 1
    assert errors[0].startswith("the layered PSB was not written (plates differ in size (12x10 against 12x8)")
    assert errors[0].endswith("); the individual TIFFs are unaffected")
    assert not (export_dir / "Loom.psb").exists()
    assert sorted(p.name for p in export_dir.glob("*.tif")) == ["L_stars.tif", "RGB.tif"]


def test_empty_psb_document_is_logged_and_tiffs_still_written(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    ops, outputs, _ = _publish(tmp_path, {"L": mono})
    export_dir = tmp_path / "export"
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": True},
                   _builtin_plan(), log)
    assert _texts(lines, "error") == [
        "the layered PSB was not written (there is nothing to put in a PSB); "
        "the individual TIFFs are unaffected"]
    assert (export_dir / "L.tif").exists()
    assert not list(export_dir.glob("*.psb"))


def test_existing_psb_is_replaced(tmp_path: Path) -> None:
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb})
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "Loom.psb").write_bytes(b"stale")
    _lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": True},
                   _builtin_plan(), log)
    data = (export_dir / "Loom.psb").read_bytes()
    assert data[:4] == b"8BPS" and data != b"stale"


def test_psb_geometry_comes_from_the_first_layer(tmp_path: Path) -> None:
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2, shape=(H, W))
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb})
    export_dir = tmp_path / "export"
    _lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": True},
                   _builtin_plan(), log)
    data = (export_dir / "Loom.psb").read_bytes()
    height = int.from_bytes(data[14:18], "big")
    width = int.from_bytes(data[18:22], "big")
    assert (width, height) == (W, H)


def test_icc_failure_gives_untagged_psb_with_warning(tmp_path: Path) -> None:
    gone = tmp_path / "gone.icc"  # listed in the plan, but the file has vanished
    plan = {"rgb": ["Fake RGB"], "gray": [], "paths": {"RGB": {"Fake RGB": os.fspath(gone)}, "GRAY": {}}}
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb}, plan=plan)
    export_dir = tmp_path / "export"
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": True,
                                  "projectName": "Loom"}, plan, log)
    warnings = _texts(lines, "warn")
    assert any(w.startswith("the PSB will be untagged (") for w in warnings)
    assert (export_dir / "Loom.psb").read_bytes()[:4] == b"8BPS"
    assert _texts(lines, "error") == []


def test_published_fits_are_unchanged_by_export(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"L": mono, "RGB": rgb})

    def digests() -> dict[str, str]:
        return {n: hashlib.sha256(Path(p).read_bytes()).hexdigest() for n, p in outputs.items()}

    before = digests()
    _lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(tmp_path / "export"), "stretch": True, "exportPsb": True},
                   _builtin_plan(), log)
    assert digests() == before


# ---- TIFF content ----------------------------------------------------------------------------------------------


def test_tiffs_are_16_bit_with_profile_and_scaled_values(tmp_path: Path) -> None:
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb, "L": mono})
    export_dir = tmp_path / "export"
    _lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True}, _builtin_plan(), log)

    planes, _header = read_fits(outputs["RGB"])
    with tifffile.TiffFile(os.fspath(export_dir / "RGB.tif")) as tf:
        page = tf.pages[0]
        assert page.tags.get(ICC_TAG).value == PROFILE
        arr = page.asarray()
    assert arr.dtype == np.uint16 and arr.shape == (H, W, 3)
    for c in range(3):
        expected = np.floor(planes[c].astype(np.float64) * 65535 + 0.5).astype(np.uint16)
        np.testing.assert_array_equal(arr[..., c], expected)

    mono_planes, _ = read_fits(outputs["L"])
    with tifffile.TiffFile(os.fspath(export_dir / "L.tif")) as tf:
        mono_arr = tf.pages[0].asarray()
        assert tf.pages[0].tags.get(ICC_TAG).value == PROFILE
    assert mono_arr.dtype == np.uint16 and mono_arr.shape == (H, W)
    np.testing.assert_array_equal(
        mono_arr, np.floor(mono_planes[0].astype(np.float64) * 65535 + 0.5).astype(np.uint16))


def test_tiff_replaces_an_existing_file(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    ops, outputs, _ = _publish(tmp_path, {"L": mono})
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "L.tif").write_bytes(b"stale")
    _lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True}, _builtin_plan(), log)
    assert (export_dir / "L.tif").read_bytes()[:2] in (b"II", b"MM")


def test_a_failed_tiff_is_a_warning_and_the_rest_are_written(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"L": mono, "RGB": rgb})
    Path(outputs["L"]).unlink()  # the plate vanished before the export
    export_dir = tmp_path / "export"
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True}, _builtin_plan(), log)
    warnings = _texts(lines, "warn")
    assert len(warnings) == 1 and warnings[0].startswith("L could not be exported (")
    assert (export_dir / "RGB.tif").exists() and not (export_dir / "L.tif").exists()


# ---- review additions: PSB profile, PSB switch, distinct gray and RGB files, PSB path blocked, TIFF order -----------


def _u16(data: bytes, pos: int) -> int:
    return int.from_bytes(data[pos:pos + 2], "big")


def _u32(data: bytes, pos: int) -> int:
    return int.from_bytes(data[pos:pos + 4], "big")


def _psb_resource(data: bytes, res_id: int) -> bytes | None:
    """The payload of one image resource of a PSB file (the header, colour mode data, then 8BIM resources)."""
    assert data[:4] == b"8BPS"
    pos = 26
    pos += 4 + _u32(data, pos)  # colour mode data
    end = pos + 4 + _u32(data, pos)
    pos += 4
    while pos < end:
        assert data[pos:pos + 4] == b"8BIM"
        rid = _u16(data, pos + 4)
        pos += 6
        name_total = 1 + data[pos]
        pos += name_total + (name_total % 2)  # Pascal name padded to an even length
        size = _u32(data, pos)
        pos += 4
        payload = data[pos:pos + size]
        pos += size + (size % 2)
        if rid == res_id:
            return payload
    return None


ICC_RESOURCE = 1039


def test_tagged_psb_carries_the_assigned_profile_bytes(tmp_path: Path) -> None:
    profile_file = tmp_path / "rgb.icc"
    profile_file.write_bytes(PROFILE + b"-from-file")
    plan = {"rgb": ["Fake RGB"], "gray": [], "paths": {"RGB": {"Fake RGB": os.fspath(profile_file)}, "GRAY": {}}}
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb}, plan=plan)
    export_dir = tmp_path / "export"
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": True,
                                  "projectName": "Loom"}, plan, _log()[1])
    data = (export_dir / "Loom.psb").read_bytes()
    assert _psb_resource(data, ICC_RESOURCE) == PROFILE + b"-from-file"


def test_builtin_psb_profile_is_siril_srgb_bytes(tmp_path: Path) -> None:
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb})
    export_dir = tmp_path / "export"
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": True,
                                  "projectName": "Loom"}, _builtin_plan(), _log()[1])
    assert _psb_resource((export_dir / "Loom.psb").read_bytes(), ICC_RESOURCE) == PROFILE


def test_no_psb_when_export_psb_is_off(tmp_path: Path) -> None:
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb})
    export_dir = tmp_path / "export"
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": False,
                                  "projectName": "Loom"}, _builtin_plan(), log)
    assert not list(export_dir.glob("*.psb"))
    assert (export_dir / "RGB.tif").exists()
    assert not any("PSB" in text for _lv, text in lines)


def _distinct_plan(tmp_path: Path) -> tuple[dict[str, Any], str, str]:
    gray = tmp_path / "gray.icc"
    rgb = tmp_path / "rgb.icc"
    gray.write_bytes(b"GRAY-PROFILE-BYTES" * 3)
    rgb.write_bytes(b"RGB-PROFILE-BYTES" * 5)
    plan = {"rgb": ["RGB file"], "gray": ["Gray file"],
            "paths": {"RGB": {"RGB file": os.fspath(rgb)}, "GRAY": {"Gray file": os.fspath(gray)}}}
    return plan, os.fspath(gray), os.fspath(rgb)


def test_mono_takes_the_gray_file_and_colour_the_rgb_file(tmp_path: Path) -> None:
    plan, gray, rgb_file = _distinct_plan(tmp_path)
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    colour = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, lines = _publish(tmp_path, {"L": mono, "RGB": colour}, plan=plan)
    assert f'icc_assign "{gray}"' in ops.transcript
    assert f'icc_assign "{rgb_file}"' in ops.transcript
    assert _texts(lines, "info") == ["L -> Gray file", "RGB -> RGB file"]
    export_dir = tmp_path / "export"
    _lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True}, plan, log)
    assert tifffile_icc(export_dir / "L.tif") == Path(gray).read_bytes()
    assert tifffile_icc(export_dir / "RGB.tif") == Path(rgb_file).read_bytes()


def tifffile_icc(path: Path) -> bytes:
    with tifffile.TiffFile(os.fspath(path)) as tf:
        return bytes(tf.pages[0].tags.get(ICC_TAG).value)


def test_psb_path_that_is_a_directory_is_reported_and_tiffs_still_written(tmp_path: Path) -> None:
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"RGB": rgb})
    export_dir = tmp_path / "export"
    (export_dir / "Loom.psb").mkdir(parents=True)
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True, "exportPsb": True,
                                  "projectName": "Loom"}, _builtin_plan(), log)
    errors = _texts(lines, "error")
    blocked = os.fspath(export_dir / "Loom.psb")
    assert len(errors) == 1
    assert errors[0].startswith(f"the layered PSB was not written (cannot overwrite {blocked}: ")
    assert errors[0].endswith("); the individual TIFFs are unaffected")
    assert (export_dir / "RGB.tif").exists()


def test_tiffs_are_written_in_name_order(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    colour = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops, outputs, _ = _publish(tmp_path, {"RGB": colour, "L": mono})
    export_dir = tmp_path / "export"
    lines, log = _log()
    publish.export(ops, outputs, {"exportDir": os.fspath(export_dir), "stretch": True}, _builtin_plan(), log)
    written = [text for _lv, text in lines if text.startswith("16-bit TIFF ")]
    assert written == [f"16-bit TIFF {export_dir / 'L.tif'}", f"16-bit TIFF {export_dir / 'RGB.tif'}"]


# ---- provenance ---------------------------------------------------------------------------------------------------


def test_sources_are_written_as_loomsrc_on_the_named_results_only(tmp_path: Path) -> None:
    mono = _write_plate(tmp_path / "in", "L", 1, 1)
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops = _ops(tmp_path)
    _lines, log = _log()
    publish.publish(ops, {"L": mono, "RGB": rgb}, {}, tmp_path / "out", _builtin_plan(), log,
                    sources={"RGB": "OSC m.xisf planes 0,1,2"})
    update = [cmd for cmd in ops.transcript if cmd.startswith("update_key")]
    assert update == ['update_key LOOMOUT "L" "Loom pipeline output"',
                      'update_key LOOMOUT "RGB" "Loom pipeline output"',
                      'update_key LOOMSRC "OSC m.xisf planes 0,1,2" "colour master and planes"']


def test_a_source_that_cannot_be_quoted_is_a_warning_and_the_result_is_still_saved(tmp_path: Path) -> None:
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    ops = _ops(tmp_path)
    lines, log = _log()
    outputs = publish.publish(ops, {"RGB": rgb}, {}, tmp_path / "out", _builtin_plan(), log,
                              sources={"RGB": 'OSC a"b.xisf planes 0,1,2'})
    assert os.path.exists(outputs["RGB"])
    assert any("LOOMSRC" in text for text in _texts(lines, "warn"))


def test_tiff_and_psb_exports_carry_no_loomsrc(tmp_path: Path) -> None:
    rgb = _write_plate(tmp_path / "in", "RGB", 3, 2)
    planes, header = read_fits(rgb)
    write_fits(rgb, planes, {**header, "LOOMSRC": "OSC m.xisf planes 0,1,2"}, bitpix=-32)
    ops = _ops(tmp_path)
    _lines, log = _log()
    export_dir = tmp_path / "exp"
    cfg = {"exportDir": str(export_dir), "stretch": True, "exportPsb": True}
    publish.export(ops, {"RGB": rgb}, cfg, _builtin_plan(), log)
    files = sorted(os.listdir(export_dir))
    assert any(f.endswith(".tif") for f in files)
    for f in files:
        assert b"LOOMSRC" not in (export_dir / f).read_bytes()
    assert not any(f.endswith((".fits", ".fit", ".json", ".txt", ".xml")) for f in files)
