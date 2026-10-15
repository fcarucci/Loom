"""The Fly-Through Image file may be the same field at another pixel size (resampled onto the open image's grid), and a
failed or cancelled run looks failed or cancelled in the progress window. Fakes and tiny synthetic images only: no real
window, no tool, no network, nothing run inside Siril."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from loomsiril import draft as D
from loomsiril import fly_checks, fly_grid, fly_progress, io_fits, io_tiff, siril_bridge, skyname
from loomsiril.fly_model import IMAGE_HINT, FlyDialogModel
from loomsiril.fly_progress import FlyProgress

from test_draft import _options
from test_fly_names import _dialog, sesame  # noqa: F401
from test_fly_tools import _fake_tk, _fire
from test_siril_script import _settle, _tools, blocked_discover, script, stub  # noqa: F401

OPEN = (160, 100, 3)


def _open_image(w: int = 160, h: int = 100, nc: int = 3) -> SimpleNamespace:
    rng = np.random.default_rng(3)
    return SimpleNamespace(width=w, height=h, planes=[rng.random((h, w), dtype=np.float32) for _ in range(nc)],
                           detections=[], header={}, icc=None)


def _bridge(file_planes) -> SimpleNamespace:
    return SimpleNamespace(read_layer=lambda p: file_planes, split_stars=siril_bridge.split_stars)


def _extract_values(path: str = "/d/export.tif") -> dict:
    return {"mode": "extract", "star_tool": "syqon-cli", "star_tool_path": "/x", "image_path": path}


def _gradient(w: int, h: int, nc: int = 3) -> list[np.ndarray]:
    ramp = np.linspace(0.1, 0.9, w, dtype=np.float32)[None, :].repeat(h, 0)
    return [ramp * (c + 1) / nc for c in range(nc)]


# ---- the rule --------------------------------------------------------------------------------------------------------


def test_verdict_same_size_same_field_other_shape_and_channels():
    big = (11799, 7211, 3)
    assert fly_grid.verdict(big, big) == (True, "")
    ok, line = fly_grid.verdict((2000, 1222, 3), big)
    assert ok and line == ("Image file 2000 x 1222 is the same field as the open image (11799 x 7211): "
                           "it is resampled to that size")
    ok, why = fly_grid.verdict((2000, 1500, 3), big)
    assert not ok and why.startswith("The image file's shape differs from the open image's (2000 x 1500 vs "
                                     "11799 x 7211): it must be the same field, only scaled.")
    assert "plate-solve it" in why                                        # it says what to do
    ok, why = fly_grid.verdict((2000, 1222, 1), big)
    assert not ok and "mono (1 channel)" in why and "colour (3 channels)" in why and "not converted" in why
    ok, why = fly_grid.verdict((11799, 7211, 3), (11799, 7211, 1))
    assert not ok and "colour (3 channels) but the open image is mono" in why


def test_the_aspect_tolerance_is_half_a_percent():
    assert fly_grid.verdict((1000, 1000, 3), (1000, 1000, 3)) == (True, "")
    assert fly_grid.verdict((1004, 1000, 3), (1000, 1000, 3))[0]          # 0.4 %
    assert not fly_grid.verdict((1006, 1000, 3), (1000, 1000, 3))[0]      # 0.6 %


def test_fit_planes_gives_the_open_grid_and_keeps_the_values_sensible():
    flat = fly_grid.fit_planes([np.full((25, 40), 0.3, np.float32)], 160, 100)[0]
    assert flat.shape == (100, 160) and flat.dtype == np.float32 and np.allclose(flat, 0.3, atol=1e-6)
    up = fly_grid.fit_planes(_gradient(40, 25), 160, 100)[0]
    assert up.shape == (100, 160) and 0.0 <= up.min() and up.max() <= 0.3 + 1e-6
    assert np.all(np.diff(up[50]) >= -1e-6)                               # a ramp stays a ramp
    down = fly_grid.fit_planes([up], 40, 25)[0]
    assert down.shape == (25, 40) and abs(down - _gradient(40, 25)[0]).max() < 0.02
    same = np.ones((5, 7), np.float32)
    assert fly_grid.fit_planes([same], 7, 5)[0] is same                   # nothing to do at the same size


def test_a_bright_pixel_stays_where_it_was_when_enlarged():
    src = np.zeros((10, 16), np.float32)
    src[4, 6] = 1.0
    out = fly_grid.fit_planes([src], 64, 40)[0]
    y, x = np.unravel_index(int(out.argmax()), out.shape)
    assert abs((x + 0.5) / 4 - 6.5) < 0.6 and abs((y + 0.5) / 4 - 4.5) < 0.6   # pixel centres are aligned


def test_file_grid_reads_headers_of_tiff_and_fits(tmp_path):
    t = tmp_path / "rgb.tif"
    io_tiff.write_tiff16(t, _gradient(40, 25))
    m = tmp_path / "mono.tiff"
    io_tiff.write_tiff16(m, _gradient(40, 25, 1))
    f = tmp_path / "c.fits"
    io_fits.write_fits(f, _gradient(40, 25))
    assert fly_grid.file_grid(str(t)) == (40, 25, 3) and fly_grid.file_grid(str(m)) == (40, 25, 1)
    assert fly_grid.file_grid(str(f)) == (40, 25, 3)
    with pytest.raises(ValueError, match=r"\.tif"):
        fly_grid.file_grid(str(tmp_path / "x.png"))
    assert fly_grid.grid_line(str(t), None) == (True, "")
    bad = tmp_path / "bad.fits"
    bad.write_text("not fits")
    ok, text = fly_grid.grid_line(str(bad), OPEN)
    assert not ok and "cannot be read as TIFF or FITS" in text


# ---- the worker: layers() --------------------------------------------------------------------------------------------


def test_same_field_at_another_size_is_resampled_before_the_extraction():
    b, progress, got = _open_image(), FlyProgress(), {}

    def extract(tool, exe, image, **kw):
        got["image"] = image
        return [p * 0.5 for p in image]

    out = fly_progress.layers(_extract_values(), b, _bridge(_gradient(40, 25)), progress, extract)
    assert len(got["image"]) == 3 and all(p.shape == (100, 160) for p in got["image"])       # the tool got the open grid
    assert all(p.shape == (100, 160) for p in out["starless"] + out["stars"])
    stages = [v[0] for k, v in list(progress.messages.queue) if k == "progress"]
    assert stages == ["Reading the image file", "Reading the image file: resampling 40 x 25 to 160 x 100",
                      "Extracting stars with SyQon Studio Axiom"]
    assert fly_progress.kind_of(stages[1]) == "extract"                      # it counts as the extraction phase


def test_the_resampling_runs_on_the_worker_thread(monkeypatch):
    b, progress, where = _open_image(), FlyProgress(), []
    real = fly_grid.fit_planes
    monkeypatch.setattr(fly_grid, "fit_planes", lambda *a, **k: where.append(threading.current_thread()) or real(*a, **k))
    thread = fly_progress.start(lambda: fly_progress.layers(_extract_values(), b, _bridge(_gradient(40, 25)), progress,
                                                            lambda tool, exe, image, **kw: [p * 0.5 for p in image]))
    thread.join(10)
    assert where and all(t is thread for t in where)


def test_the_same_size_is_used_as_it_is():
    b, progress, got = _open_image(), FlyProgress(), {}
    planes = [p.copy() for p in b.planes]

    def extract(tool, exe, image, **kw):
        got["image"] = image
        return [p * 0.5 for p in image]

    fly_progress.layers(_extract_values(), b, _bridge(planes), progress, extract)
    assert got["image"] is planes


def test_a_different_aspect_ratio_is_refused_with_the_message():
    b, progress = _open_image(), FlyProgress()
    with pytest.raises(ValueError) as exc:
        fly_progress.layers(_extract_values(), b, _bridge(_gradient(40, 30)), progress, lambda *a, **k: 1 / 0)
    assert str(exc.value).startswith("The image file's shape differs from the open image's (40 x 30 vs 160 x 100): "
                                     "it must be the same field, only scaled.")


@pytest.mark.parametrize("file_nc, open_nc, words", [(1, 3, "mono (1 channel) but the open image is colour"),
                                                      (3, 1, "colour (3 channels) but the open image is mono")])
def test_a_channel_mismatch_is_refused_and_says_which(file_nc, open_nc, words):
    b = _open_image(nc=open_nc)
    with pytest.raises(ValueError) as exc:
        fly_progress.layers(_extract_values(), b, _bridge(_gradient(40, 25, file_nc)), FlyProgress(),
                            lambda *a, **k: 1 / 0)
    assert words in str(exc.value) and "not converted" in str(exc.value)


def test_work_ends_with_the_refusal_as_a_failed_run_with_its_stage():
    b, progress = _open_image(), FlyProgress()
    bridge = _bridge(_gradient(40, 30))
    fly_progress.work(_extract_values(), b, bridge, SimpleNamespace(run=lambda *a: 1 / 0), progress, str)
    progress.pump()
    assert progress.failed and "shape differs" in progress.error and progress.timing() == "Failed at: Reading the image file"


def test_layer_files_of_another_size_say_what_to_do():
    b = _open_image()
    bridge = SimpleNamespace(read_layer=lambda p: _gradient(40, 25))
    with pytest.raises(ValueError) as exc:
        fly_progress.layers({"mode": "files", "starless_path": "a", "stars_path": "b"}, b, bridge, FlyProgress())
    assert "does not match the image" in str(exc.value) and "at the open image's size" in str(exc.value)


# ---- the dialog's grey line, computed on a worker ---------------------------------------------------------------------


def _extract_dialog(script, monkeypatch, blocked, tmp_path, name, planes, grid=OPEN, mode="extract"):
    path = tmp_path / name
    io_tiff.write_tiff16(path, planes)
    names = skyname.NameContext(open_grid=grid)
    dlg = _dialog(script, monkeypatch, blocked, names, None, out_dir=str(tmp_path))
    dlg._on_discovered(_tools("starnet2"))
    dlg._mode.set(mode)
    dlg._vars["image_path"].set(str(path))
    return dlg, path


def test_the_grey_line_says_the_file_is_resampled(script, monkeypatch, blocked_discover, sesame, tmp_path):
    dlg, _ = _extract_dialog(script, monkeypatch, blocked_discover, tmp_path, "a.tif", _gradient(40, 25))
    assert dlg._image_note.get() == IMAGE_HINT                                 # nothing known yet
    _settle(dlg)
    assert dlg._image_note.get() == ("Image file 40 x 25 is the same field as the open image (160 x 100): "
                                     "it is resampled to that size")
    assert dlg._reason.get() == "" and dlg._render_btn.kw["state"] == "normal"


def test_the_grey_line_is_the_refusal_and_blocks_render(script, monkeypatch, blocked_discover, sesame, tmp_path):
    dlg, _ = _extract_dialog(script, monkeypatch, blocked_discover, tmp_path, "b.tif", _gradient(40, 30))
    _settle(dlg)
    assert dlg._image_note.get().startswith("The image file's shape differs from the open image's (40 x 30 vs 160 x 100)")
    assert dlg._reason.get() == dlg._image_note.get() and dlg._render_btn.kw["state"] == "disabled"


def test_the_grey_line_for_a_channel_mismatch(script, monkeypatch, blocked_discover, sesame, tmp_path):
    dlg, _ = _extract_dialog(script, monkeypatch, blocked_discover, tmp_path, "c.tif", _gradient(40, 25, 1))
    _settle(dlg)
    assert "mono (1 channel) but the open image is colour (3 channels)" in dlg._image_note.get()
    assert dlg._render_btn.kw["state"] == "disabled"


def test_no_line_for_the_same_size_and_the_hint_returns_in_files_mode(script, monkeypatch, blocked_discover, sesame,
                                                                       tmp_path):
    dlg, _ = _extract_dialog(script, monkeypatch, blocked_discover, tmp_path, "d.tif", _gradient(160, 100))
    _settle(dlg)
    assert dlg._image_note.get() == IMAGE_HINT and dlg._reason.get() == ""
    dlg2, _ = _extract_dialog(script, monkeypatch, blocked_discover, tmp_path, "e.tif", _gradient(40, 25))
    _settle(dlg2)
    assert "is resampled" in dlg2._image_note.get()
    dlg2._mode.set("files")                                                    # the grey-out rule: not in use, no note
    assert dlg2._image_note.get() == IMAGE_HINT


def test_the_header_is_read_on_a_worker_not_the_tk_thread(script, monkeypatch, blocked_discover, sesame, tmp_path):
    seen = []
    real = fly_grid.file_grid
    monkeypatch.setattr(fly_grid, "file_grid", lambda p: seen.append(threading.current_thread()) or real(p))
    dlg, _ = _extract_dialog(script, monkeypatch, blocked_discover, tmp_path, "f.tif", _gradient(40, 25))
    assert seen == []                                                          # typing did not read the file
    _settle(dlg)
    assert seen and all(t is not threading.current_thread() for t in seen)


def test_without_the_open_grid_the_dialog_behaves_as_before(script, monkeypatch, blocked_discover, sesame, tmp_path):
    dlg, _ = _extract_dialog(script, monkeypatch, blocked_discover, tmp_path, "g.tif", _gradient(40, 30), grid=None)
    _settle(dlg)
    assert dlg._image_note.get() == IMAGE_HINT and dlg._reason.get() == ""


def test_the_model_note_follows_the_grey_out_rules():
    m = FlyDialogModel(mode="extract", tools="found", tool_id="starnet2", image_path=" /a.tif ",
                       grid_notes={"/a.tif": "note"})
    assert m.image_note() == "note"
    assert FlyDialogModel(mode="files", tools="found", image_path="/a.tif", grid_notes={"/a.tif": "note"}
                          ).image_note() == IMAGE_HINT
    assert FlyDialogModel(mode="extract", tools="none", image_path="/a.tif", grid_notes={"/a.tif": "note"}
                          ).image_note() == IMAGE_HINT


def test_validate_refuses_a_misfit_image_file_and_accepts_a_scaled_one(tmp_path):
    ok, bad = tmp_path / "ok.tif", tmp_path / "bad.tif"
    io_tiff.write_tiff16(ok, _gradient(40, 25))
    io_tiff.write_tiff16(bad, _gradient(40, 30))
    got = fly_checks.validate([("image", str(ok)), ("image", str(bad))], OPEN)
    assert got[("image", str(ok))] == "" and "shape differs" in got[("image", str(bad))]
    assert fly_checks.validate([("image", str(bad))]) == {("image", str(bad)): ""}        # no open grid: as before


# ---- the draft cache key reflects the file ---------------------------------------------------------------------------


def test_the_draft_key_follows_the_image_files_path_mtime_and_size(tmp_path):
    f = tmp_path / "export.tif"
    f.write_bytes(b"x" * 100)

    def key(path, stamp):
        image_key = f"160x100 wcs {path} {stamp}"
        return D.draft_key({**_options(tmp_path), "mode": "extract", "star_tool": "starnet2",
                            "image_path": str(path), "image_key": image_key})

    first = key(f, fly_checks.file_stamp(str(f)))
    assert first == key(f, fly_checks.file_stamp(str(f)))
    f.write_bytes(b"y" * 120)                                                  # same path, another size
    assert key(f, fly_checks.file_stamp(str(f))) != first
    assert key(tmp_path / "other.tif", fly_checks.file_stamp(str(f))) != key(f, fly_checks.file_stamp(str(f)))


# ---- a failed or cancelled run looks failed or cancelled -------------------------------------------------------------


def test_a_failed_run_shows_the_error_stopped_and_empty_bars():
    p = FlyProgress()
    for n in (2, 4):
        p.report("Extracting stars with X", n, 10)
    p.pump()
    assert p.overall > 0 and p.overall_text().startswith("Overall")
    p.fail("The image file's shape differs from the open image's (1 x 2 vs 3 x 4)")
    p.pump()
    assert p.failed and not p.cancelled
    assert p.label() == p.error and p.overall_text() == "Stopped"
    assert p.stage_value() == 0.0 and p.overall_value() == 0.0
    assert p.timing() == "Failed at: Extracting stars with X"


def test_a_cancelled_run_says_cancelled_with_empty_bars():
    p = FlyProgress()
    p.report("Rendering x frames", 2, 6)
    p.finish("Cancelled. 2 frames kept.", cancelled=True)
    p.pump()
    assert p.cancelled and not p.failed and p.overall_text() == "Cancelled"
    assert p.stage_value() == 0.0 and p.overall_value() == 0.0 and p.label() == "Cancelled. 2 frames kept."
    assert p.timing() == "Cancelled at: Rendering x frames"


def test_a_successful_run_is_unchanged():
    p = FlyProgress()
    p.report("Rendering x frames", 3, 6)
    p.finish("done")
    p.pump()
    assert not p.failed and p.overall_text() == "Overall 100%" and p.stage_value() == 100.0 and p.timing() == ""


def test_the_window_of_a_failed_run_looks_failed_and_keeps_close(monkeypatch):
    from loomsiril import fly_ui

    tk = _fake_tk(monkeypatch)
    p = FlyProgress()
    win = fly_ui.FlyProgressWindow(tk.Toplevel(), p)
    p.report("Reading the image file")
    p.report("Extracting stars with X", 19, 100)
    win.refresh()
    assert win._overall.get().startswith("Overall") and win._total_bar.kw["value"] > 0
    p.fail("The image file's shape differs from the open image's (1 x 2 vs 3 x 4): it must be the same field")
    _fire(win)
    assert "shape differs" in win._stage.get() and win._overall.get() == "Stopped"
    assert win._bar.kw["value"] == 0.0 and win._total_bar.kw["value"] == 0.0
    assert win._timing.get() == "Failed at: Extracting stars with X" and win._button.kw["text"] == "Close"


def test_the_window_of_a_cancelled_run_says_cancelled(monkeypatch):
    from loomsiril import fly_ui

    tk = _fake_tk(monkeypatch)
    p = FlyProgress()
    win = fly_ui.FlyProgressWindow(tk.Toplevel(), p)
    p.report("Rendering x frames", 3, 6)
    p.finish("Cancelled. 3 frames kept.", cancelled=True)
    _fire(win)
    assert win._overall.get() == "Cancelled" and win._total_bar.kw["value"] == 0.0 and win._bar.kw["value"] == 0.0
    assert win._button.kw["text"] == "Close"
