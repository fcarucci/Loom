"""Tests for Loom/loom_fly_through.py against the sirilpy stub (E7).

The stub (tests/sirilpy_stub) is put first on sys.path; the state the script reads from Siril is set in
``sirilpy.STATE``. The dialog is bypassed (main takes a replaceable ``dialog``) and loomsiril.pipeline is a fake
module that records the FlyJob. Passing these tests does NOT mean the script has run inside Siril.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

import synth
from loomsiril import io_tiff, starnet_licence
from loomsiril.starnet_licence import STARNET_LICENCE_REV
from loomsiril.wcs import keyword_wcs

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "Loom" / "loom_fly_through.py"
STUB = REPO / "tests" / "sirilpy_stub"


class FakeJob:
    """Stands in for the frozen FlyJob: keyword fields only."""

    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    """The sirilpy stub, freshly reset, imported first; returns the sirilpy module."""
    monkeypatch.syspath_prepend(str(STUB))
    for name in [m for m in sys.modules if m == "sirilpy" or m.startswith("sirilpy.")]:
        monkeypatch.delitem(sys.modules, name)
    import sirilpy

    sirilpy.STATE.reset()
    sirilpy.STATE.configdir = str(tmp_path_factory.mktemp("siril-config"))
    yield sirilpy
    sirilpy.STATE.reset()


@pytest.fixture
def script(stub):
    """The script module, loaded from its file (not imported as a package)."""
    spec = importlib.util.spec_from_file_location("loom_fly_through_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake_pipeline(monkeypatch: pytest.MonkeyPatch):
    """loomsiril.pipeline replaced by a module whose run() records the job and reports progress."""
    mod = ModuleType("loomsiril.pipeline")
    mod.FlyJob = FakeJob
    mod.calls = []
    mod.result = SimpleNamespace(written=3, reused=0, cancelled=False, videos=[],
                                 commands=["ffmpeg -framerate 25 -i frame_%05d.tif out.mp4"], failed=[])
    mod.error = None

    def run(job, progress=None, is_cancelled=None):
        mod.calls.append(job)
        if progress is not None:
            progress("rendering frames", 1, 2)
        if mod.error is not None:
            raise mod.error
        return mod.result

    mod.run = run
    monkeypatch.setitem(sys.modules, "loomsiril.pipeline", mod)
    # ``from loomsiril import pipeline`` reads the package attribute first: tests/test_pipeline.py sets it when
    # the real module is imported earlier in the same run, so the fake must replace that attribute too.
    import loomsiril

    monkeypatch.setattr(loomsiril, "pipeline", mod, raising=False)
    return mod


def _field_and_header(w: int = 40, h: int = 24):
    """A synthetic field, its Siril-side pixels (bottom-up uint16) and the FITS header Siril would hold."""
    field = synth.make_field(w=w, h=h, n_stars=6, nc=1, seed=4)
    header_top = dict(field.header)
    header_fits = dict(header_top)
    header_fits["CRPIX2"] = h + 1 - header_top["CRPIX2"]
    header_fits["CD1_2"] = -header_top["CD1_2"]
    header_fits["CD2_2"] = -header_top["CD2_2"]
    pixels = (field.image[0][::-1] * 65535).round().astype(np.uint16)
    return field, pixels, header_fits, header_top


def _load_siril_image(stub, pixels, header, stars=None) -> None:
    stub.STATE.loaded = True
    stub.STATE.pixeldata = pixels
    stub.STATE.header = header
    stub.STATE.stars = stars
    stub.STATE.icc = None
    stub.STATE.filename = "light.fit"


def _write_layers(tmp_path: Path, field) -> tuple[str, str]:
    """Starless and stars files as PixInsight would write them: top-down TIFF."""
    s, t = tmp_path / "starless.tif", tmp_path / "stars.tif"
    io_tiff.write_tiff16(s, field.starless)
    io_tiff.write_tiff16(t, field.stars)
    return str(s), str(t)


def _values(tmp_path: Path, **kw) -> dict:
    base = {
        "mode": "files", "starless_path": "", "stars_path": "", "target": None, "target_type": "nebula",
        "distance": None, "duration": 4.0, "fps": 25, "loop": "none", "star_quality": "high",
        "dynamic": "sdr", "gaia": "cache", "object_name": "Test", "out_dir": str(tmp_path / "out"),
        "presets": ["youtube_1080"], "video": False,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- import and structure

def test_import_has_no_side_effects(stub, script):
    assert stub.STATE.connects == 0
    assert stub.STATE.messages == [] and stub.STATE.installs == [] and stub.STATE.commands == []
    assert callable(script.main)


def test_docstring_says_not_run_inside_siril(script):
    doc = script.__doc__ or ""
    assert "HAS NOT BEEN RUN INSIDE SIRIL" in doc
    assert "stand-in sirilpy" in doc


def test_script_is_thin(script):
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 400
    assert "loomsiril" in SCRIPT.read_text(encoding="utf-8")


def test_main_happy_path_calls_pipeline_with_the_right_job(stub, script, fake_pipeline, tmp_path, monkeypatch):
    monkeypatch.setenv("LOOMSIRIL_STARNET", str(tmp_path / "absent"))   # no StarNet: the files path is taken
    monkeypatch.setenv("PATH", str(tmp_path))
    field, pixels, header_fits, header_top = _field_and_header()
    _load_siril_image(stub, pixels, header_fits, stars=[
        stub.PSFStar(xpos=5.0, ypos=3.0, A=0.4, fwhmx=3.0, fwhmy=3.0)])
    starless_path, stars_path = _write_layers(tmp_path, field)
    values = _values(tmp_path, starless_path=starless_path, stars_path=stars_path)
    seen = {}

    def dialog(options, starnet_available, target, siril=None):
        seen.update(options=options, starnet=starnet_available, target=target, siril=siril)
        return values

    script.main(dialog=dialog)

    assert seen["starnet"] is False and seen["siril"] is not None
    assert seen["target"] is not None                       # the header centre goes to the dialog
    assert stub.STATE.installs and stub.STATE.installs[0][0] == ("numpy", "tifffile", "ttkthemes")
    assert len(fake_pipeline.calls) == 1
    job = fake_pipeline.calls[0]
    assert isinstance(job, FakeJob)
    # the job is top-down: its WCS is the top-down WCS of the image, and the layers are the files
    got_wcs = job.wcs
    assert got_wcs["crpix2"] == pytest.approx(header_top["CRPIX2"])
    assert got_wcs["cd"] == pytest.approx(keyword_wcs(header_top)["cd"])
    assert np.allclose(job.starless[0], field.starless[0], atol=2 / 65535)
    assert np.allclose(job.stars[0], field.stars[0], atol=2 / 65535)
    assert job.target == pytest.approx({"ra": header_top["CRVAL1"], "dec": header_top["CRVAL2"]})
    assert (job.duration, job.fps, job.presets, job.out_dir) == (4.0, 25, ["youtube_1080"], values["out_dir"])
    assert job.detections[0].y == pytest.approx(2.5)          # ypos 3.0 is the top-down row 2.5 (edge convention)
    # progress reached Siril, the result box names the frames and the ffmpeg command
    assert stub.STATE.progress == [("rendering frames", 0.5)]
    kind, text = stub.STATE.messages[-1]
    assert kind == "info" and "Frames written: 3" in text and "ffmpeg -framerate 25" in text
    # the options were saved (no arrays in them) and the connection was closed
    saved = json.loads((Path(stub.STATE.configdir) / "loomsiril-fly.json").read_text(encoding="utf-8"))
    assert "starless" not in saved and "starless_path" not in saved
    assert stub.STATE.disconnects == 1


def test_main_saves_options_to_siril_config_dir(stub, script, fake_pipeline, tmp_path):
    field, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    stub.STATE.configdir = str(tmp_path / "siril-config")
    starless_path, stars_path = _write_layers(tmp_path, field)
    script.main(dialog=lambda *a, **k: _values(tmp_path, starless_path=starless_path, stars_path=stars_path,
                                               fps=60, presets=["social_square"]))
    data = json.loads((tmp_path / "siril-config" / "loomsiril-fly.json").read_text(encoding="utf-8"))
    assert data["fps"] == 60 and data["presets"] == ["social_square"]
    assert "starless_path" not in data and "starless" not in data


def test_no_image_is_an_error_box(stub, script, fake_pipeline):
    stub.STATE.loaded = False
    script.main(dialog=lambda *a, **k: pytest.fail("the dialog must not open without an image"))
    assert stub.STATE.messages == [("error", "Load a plate-solved image first.")]
    assert fake_pipeline.calls == []


def test_unsolved_image_is_an_error_box(stub, script, fake_pipeline):
    _, pixels, _, _ = _field_and_header()
    _load_siril_image(stub, pixels, {"NAXIS": 2})
    script.main(dialog=lambda *a, **k: pytest.fail("no dialog without a plate solution"))
    kind, text = stub.STATE.messages[-1]
    assert kind == "error" and "plate solution" in text
    assert fake_pipeline.calls == []


def test_unwritable_options_do_not_stop_the_render(stub, script, fake_pipeline, tmp_path):
    field, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    stub.STATE.configdir = "/nonexistent-siril-config"           # cannot be created: OSError on save
    starless_path, stars_path = _write_layers(tmp_path, field)
    script.main(dialog=lambda *a, **k: _values(tmp_path, starless_path=starless_path, stars_path=stars_path))
    assert len(fake_pipeline.calls) == 1
    assert stub.STATE.messages[-1][0] == "info"
    assert any("options not saved" in text for text, _ in stub.STATE.logs)


def test_cancelled_dialog_renders_nothing(stub, script, fake_pipeline):
    _, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    script.main(dialog=lambda *a, **k: None)
    assert fake_pipeline.calls == [] and stub.STATE.messages == []
    assert stub.STATE.disconnects == 1


def test_pipeline_error_is_shown_in_a_box(stub, script, fake_pipeline, tmp_path):
    field, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    starless_path, stars_path = _write_layers(tmp_path, field)
    fake_pipeline.error = RuntimeError("no Gaia sources")
    script.main(dialog=lambda *a, **k: _values(tmp_path, starless_path=starless_path, stars_path=stars_path))
    assert stub.STATE.messages[-1] == ("error", "Loom Fly-Through: no Gaia sources")


def test_mismatched_layer_file_is_refused(stub, script, fake_pipeline, tmp_path):
    field, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    starless_path, stars_path = _write_layers(tmp_path, field)
    small = tmp_path / "small.tif"
    io_tiff.write_tiff16(small, [np.zeros((5, 5), dtype=np.float32)])
    script.main(dialog=lambda *a, **k: _values(tmp_path, starless_path=starless_path, stars_path=str(small)))
    kind, text = stub.STATE.messages[-1]
    assert kind == "error" and "does not match the image" in text
    assert fake_pipeline.calls == []


def test_starnet_mode_without_starnet_is_refused(stub, script, fake_pipeline, tmp_path, monkeypatch):
    monkeypatch.setenv("LOOMSIRIL_STARNET", str(tmp_path / "absent"))
    monkeypatch.setenv("PATH", str(tmp_path))
    _, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    script.main(dialog=lambda *a, **k: _values(tmp_path, mode="starnet"))
    assert "StarNet is not installed" in stub.STATE.messages[-1][1]
    assert fake_pipeline.calls == []


def _fake_cli_copy(path: Path) -> str:
    """A stand-in StarNet2 CLI: ``--version``, then --input/--output FITS, the output a copy of the input."""
    path.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if sys.argv[1:] == ['--version']:\n    print('StarNet2 fake 0.0'); sys.exit(0)\n"
        "args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n"
        "from loomsiril import io_fits\n"
        "planes, _ = io_fits.read_fits(args['--input'])\n"
        "io_fits.write_fits(args['--output'], planes)\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _fake_cli_fail(path: Path) -> str:
    """A stand-in StarNet2 CLI that answers --version and then exits with an error."""
    path.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if sys.argv[1:] == ['--version']:\n    print('StarNet2 fake 0.0'); sys.exit(0)\n"
        "sys.exit(3)\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.mark.parametrize("case", ["success", "starnet_error", "cancelled"])
def test_starnet_temporary_files_are_removed_in_every_case(stub, script, fake_pipeline, tmp_path, monkeypatch, case):
    """The StarNet in/out FITS files live in a temporary folder that is gone after a success, a StarNet error and a
    cancelled render (no copy of the user's image is left in the system temp folder)."""
    root = tmp_path / "tmproot"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    if case == "starnet_error":
        monkeypatch.setenv("LOOMSIRIL_STARNET", _fake_cli_fail(tmp_path / "starnet2"))
    else:
        monkeypatch.setenv("LOOMSIRIL_STARNET", _fake_cli_copy(tmp_path / "starnet2"))
    if case == "cancelled":
        fake_pipeline.result = SimpleNamespace(written=0, reused=0, cancelled=True, videos=[], commands=[], failed=[])
    _, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    script.main(dialog=lambda *a, **k: _values(tmp_path, mode="starnet", starnet_licence=STARNET_LICENCE_REV))
    assert list(root.iterdir()) == []
    if case == "starnet_error":
        assert fake_pipeline.calls == [] and "StarNet exited" in stub.STATE.messages[-1][1]
    else:
        assert len(fake_pipeline.calls) == 1


def test_starnet_mode_runs_the_cli_and_splits(stub, script, fake_pipeline, tmp_path, monkeypatch):
    monkeypatch.setenv("LOOMSIRIL_STARNET", _fake_cli_copy(tmp_path / "starnet2"))
    field, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    script.main(dialog=lambda *a, **k: _values(tmp_path, mode="starnet", starnet_licence=STARNET_LICENCE_REV))
    job = fake_pipeline.calls[0]
    image = pixels[::-1].astype(np.float32) / 65535            # the top-down image the script built
    assert np.allclose(job.starless[0], image, atol=2 / 65535)  # copy StarNet: starless = image ...
    assert np.allclose(job.stars[0], 0.0, atol=1e-6)            # ... so there are no stars left


def test_starnet_mode_without_the_licence_accepted_is_refused(stub, script, fake_pipeline, tmp_path, monkeypatch):
    """Without the current licence revision, StarNet never runs (the dialog gate is backed up here too)."""
    monkeypatch.setenv("LOOMSIRIL_STARNET", _fake_cli_copy(tmp_path / "starnet2"))
    _, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    for licence in (None, "2025-01-01"):
        script.main(dialog=lambda *a, **k: _values(tmp_path, mode="starnet", starnet_licence=licence))
        kind, text = stub.STATE.messages[-1]
        assert kind == "error" and "licence accepted" in text
    assert fake_pipeline.calls == []


def test_licence_check_asks_only_for_a_new_revision(monkeypatch):
    asked = []
    monkeypatch.setattr(starnet_licence, "ask", lambda parent, binary: asked.append(binary) or STARNET_LICENCE_REV)
    assert starnet_licence.check(None, STARNET_LICENCE_REV, "x") == STARNET_LICENCE_REV and asked == []
    assert starnet_licence.check(None, None, "x") == STARNET_LICENCE_REV and asked == ["x"]


def test_licence_check_decline_keeps_starnet_off(monkeypatch):
    shown = []
    tk_stub = ModuleType("tkinter")
    msg = ModuleType("tkinter.messagebox")
    msg.showinfo = lambda *a, **k: shown.append(a[1])
    tk_stub.messagebox = msg
    monkeypatch.setitem(sys.modules, "tkinter", tk_stub)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", msg)
    monkeypatch.setattr(starnet_licence, "ask", lambda parent, binary: None)
    assert starnet_licence.check(None, None, "x") is None
    assert shown == ["StarNet mode stays off until its licence is accepted."]


def test_licence_text_is_read_from_next_to_the_binary(tmp_path):
    (tmp_path / "starnet2").write_text("binary", encoding="utf-8")
    (tmp_path / "LICENSE.txt").write_text("the licence", encoding="utf-8")
    assert starnet_licence.licence_text(tmp_path / "starnet2") == "the licence"
    assert starnet_licence.licence_text(tmp_path / "elsewhere" / "starnet2") is None
    assert starnet_licence.licence_text(None) is None
    starnet_licence.require({"starnet_licence": STARNET_LICENCE_REV})
    with pytest.raises(ValueError, match="licence accepted"):
        starnet_licence.require({})


def test_dialog_render_in_starnet_mode_needs_the_licence(stub, script, monkeypatch):
    """_ok: declining keeps the dialog open with StarNet off; accepting remembers the revision in the values."""
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    shown = []
    msg = ModuleType("tkinter.messagebox")
    msg.showinfo = msg.showerror = lambda *a, **k: shown.append(a[1])
    tk.messagebox = msg
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", msg)
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({}), True, None)
    dlg._mode.set("starnet")
    monkeypatch.setattr(starnet_licence, "ask", lambda parent, binary: None)
    dlg._ok()
    assert dlg.result is None and dlg._mode.get() == "files"
    assert shown == ["StarNet mode stays off until its licence is accepted."]
    dlg._mode.set("starnet")
    monkeypatch.setattr(starnet_licence, "ask", lambda parent, binary: STARNET_LICENCE_REV)
    dlg._ok()
    assert dlg.result["mode"] == "starnet" and dlg.result["starnet_licence"] == STARNET_LICENCE_REV


def test_licence_window_needs_the_tick_box_then_accept(stub, script):
    """The real window: Accept stays disabled until the box is ticked; Decline returns None."""
    tk = _tkinter()
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    from tkinter import ttk

    def descendants(w):
        for kid in w.winfo_children():
            yield kid
            yield from descendants(kid)

    def window_and(text_or_class):
        win = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == "StarNet2 licence")
        for w in descendants(win):
            if isinstance(w, ttk.Button) and w.cget("text") == text_or_class:
                return win, w
            if w.winfo_class() == "TCheckbutton" and text_or_class == "tick":
                return win, w
        raise AssertionError(text_or_class)

    def drive_accept():
        _, accept = window_and("Accept and continue")
        assert "disabled" in accept.state()
        accept.invoke()                                  # disabled: nothing happens
        _, box = window_and("tick")
        box.invoke()
        assert "disabled" not in window_and("Accept and continue")[1].state()
        window_and("Accept and continue")[1].invoke()

    root.after(100, drive_accept)
    try:
        assert starnet_licence.ask(root, None) == STARNET_LICENCE_REV
        root.after(100, lambda: window_and("Decline")[1].invoke())
        assert starnet_licence.ask(root, None) is None
    finally:
        root.destroy()


def test_dialog_values_keep_saved_options_and_fill_the_rest(stub, script):
    """from_options is dict.get per field: a saved key wins (even as None), a missing key keeps its default."""
    v = script.DialogValues.from_options({"duration": 3.0, "ra": None, "presets": []})
    assert v.duration == 3.0 and v.ra is None and v.presets == []
    assert v.fps == 30 and v.loop == "none" and v.video is False and v.distance is None
    assert v.dec is script._FROM_TARGET                 # not saved: the dialog shows the target's Dec


def test_dialog_values_defaults_come_from_presets_and_the_working_folder(stub, script, monkeypatch):
    from loomsiril import presets

    monkeypatch.chdir(Path(__file__).resolve().parent)
    v = script.DialogValues.from_options({})
    assert v.star_quality == presets.STAR_QUALITY_DEFAULT
    assert v.out_dir == str(Path(__file__).resolve().parent)
    assert v.starless_path == "" and v.target_type == "nebula" and v.dynamic == "sdr" and v.gaia == "cache"


def test_run_pipeline_draws_progress_in_order_and_returns_the_result(stub, script):
    """_run_pipeline: the worker's progress reaches Siril in order (done/total, 0.0 when total is 0), the result comes back,
    and the pipeline is given an is_cancelled that answers False."""
    seen = {}

    class Pipeline:
        @staticmethod
        def run(job, progress, is_cancelled):
            seen["job"], seen["cancelled"] = job, is_cancelled()
            for stage, done, total in (("a", 0, 4), ("b", 2, 4), ("c", 4, 0)):
                progress(stage, done, total)
            return "result"

    assert script._run_pipeline(Pipeline, "the job", stub.SirilInterface()) == "result"
    assert seen == {"job": "the job", "cancelled": False}
    assert stub.STATE.progress == [("a", 0.0), ("b", 0.5), ("c", 0.0)]


def test_run_pipeline_reraises_the_worker_error(stub, script):
    class Pipeline:
        @staticmethod
        def run(job, progress, is_cancelled):
            raise ValueError("bad job")

    with pytest.raises(ValueError, match="bad job"):
        script._run_pipeline(Pipeline, None, stub.SirilInterface())


def _tkinter():
    """The tkinter module, or skip: it may be absent, or present without its Tk shared library
    (``ImportError: libtk8.6.so``), so any import failure skips."""
    try:
        import tkinter
    except (ImportError, OSError) as exc:
        pytest.skip(f"tkinter cannot be imported here: {exc}")
    return tkinter


def test_dialog_class_only_where_tk_works(stub, script):
    tk = _tkinter()
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    try:
        dlg = script.LoomFlyDialog(root, script.DialogValues.from_options({"duration": 3.0}), False,
                                   {"ra": 10.0, "dec": -5.0})
        from loomsiril import presets

        dlg.top.update_idletasks()
        values = dlg.values(presets)
        assert values["duration"] == 3.0 and values["target"] == {"ra": 10.0, "dec": -5.0}
        assert values["mode"] == "files" and values["presets"] == ["youtube_1080"]
    finally:
        root.destroy()


class _FakeVar:
    """tkinter.StringVar / BooleanVar stand-in: holds the value it was made with."""

    def __init__(self, value: object = None) -> None:
        self._value = value

    def get(self) -> object:
        return self._value

    def set(self, value: object) -> None:
        self._value = value


class _FakeWidget:
    """A tkinter widget stand-in: remembers its parent, its options and where it was gridded."""

    def __init__(self, master: object = None, *args: object, **kw: object) -> None:
        self.kind = type(self).__name__
        self.kw = kw
        self.grid_kw: dict | None = None
        self.children: list[_FakeWidget] = []
        if isinstance(master, _FakeWidget):
            master.children.append(self)

    def grid(self, **kw: object) -> None:
        self.grid_kw = kw

    def protocol(self, *args: object) -> None:
        pass

    def title(self, *args: object) -> None:
        pass

    def destroy(self) -> None:
        pass


def _fake_tkinter(monkeypatch: pytest.MonkeyPatch) -> None:
    """tkinter and tkinter.ttk replaced by recording stand-ins: the dialog's layout is built with no display."""
    ttk = ModuleType("tkinter.ttk")
    tk = ModuleType("tkinter")
    for name in ("Frame", "Label", "Entry", "Combobox", "Radiobutton", "Checkbutton", "Button"):
        setattr(ttk, name, type(name, (_FakeWidget,), {}))
    tk.Tk = type("Tk", (_FakeWidget,), {})
    tk.Toplevel = type("Toplevel", (_FakeWidget,), {})
    tk.StringVar = tk.BooleanVar = _FakeVar
    tk.ttk = ttk
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", ttk)


def _fake_grid_rows(parent: _FakeWidget) -> list[tuple]:
    """(row, column, class, text) of each gridded widget under ``parent``, in grid order; a frame's children follow it."""
    out: list[tuple] = []
    kids = [w for w in parent.children if w.grid_kw is not None]
    for w in sorted(kids, key=lambda w: (w.grid_kw["row"], w.grid_kw["column"])):
        out.append((w.grid_kw["row"], w.grid_kw["column"], w.kind, w.kw.get("text")))
        out.extend(_fake_grid_rows(w))
    return out


def _real_grid_rows(parent: object) -> list[tuple]:
    """The same tuples as _fake_grid_rows, read from a real Tk widget tree."""
    out: list[tuple] = []
    kids = [w for w in parent.winfo_children() if w.grid_info()]
    for w in sorted(kids, key=lambda w: (w.grid_info()["row"], w.grid_info()["column"])):
        kind = w.winfo_class().removeprefix("T")
        text = None if kind in ("Entry", "Combobox", "Frame") else w.cget("text")
        out.append((w.grid_info()["row"], w.grid_info()["column"], kind, text))
        out.extend(_real_grid_rows(w))
    return out


#: The dialog's layout (grid order; a frame's children follow the frame). The fake and the real tk must both show it.
DIALOG_LAYOUT = [
    (0, 0, "Radiobutton", "Run StarNet on the image"),
    (0, 1, "Radiobutton", "Starless and stars files"),
    (1, 0, "Label", "Starless file (TIFF/FITS)"),
    (1, 1, "Entry", None),
    (2, 0, "Label", "Stars file (TIFF/FITS)"),
    (2, 1, "Entry", None),
    (3, 0, "Label", "Target RA (deg)"),
    (3, 1, "Entry", None),
    (4, 0, "Label", "Target Dec (deg)"),
    (4, 1, "Entry", None),
    (5, 0, "Label", "Target type"),
    (5, 1, "Combobox", None),
    (6, 0, "Label", "Distance (pc, blank = auto)"),
    (6, 1, "Entry", None),
    (7, 0, "Label", "Duration (s)"),
    (7, 1, "Entry", None),
    (8, 0, "Label", "Frames per second"),
    (8, 1, "Combobox", None),
    (9, 0, "Label", "Loop"),
    (9, 1, "Combobox", None),
    (10, 0, "Label", "Star quality"),
    (10, 1, "Combobox", None),
    (11, 0, "Label", "Dynamic range"),
    (11, 1, "Combobox", None),
    (12, 0, "Label", "Gaia source"),
    (12, 1, "Combobox", None),
    (13, 0, "Label", "Object name"),
    (13, 1, "Entry", None),
    (14, 0, "Label", "Output folder"),
    (14, 1, "Entry", None),
    (15, 0, "Frame", None),
    (0, 0, "Checkbutton", "Social 1080×1920"),
    (0, 1, "Checkbutton", "Social 1080×1080"),
    (1, 0, "Checkbutton", "YouTube 3840×2160"),
    (1, 1, "Checkbutton", "YouTube 1920×1080"),
    (2, 0, "Checkbutton", "Exhibition 3840×2160 (loop)"),
    (16, 0, "Checkbutton", "Encode video (ffmpeg)"),
    (17, 0, "Frame", None),
    (0, 0, "Button", "Cancel"),
    (0, 1, "Button", "Render"),
]

#: Two starting points: the defaults (with the target's RA and Dec), and every saved option set.
DIALOG_CASES = [
    pytest.param(
        {"duration": 3.0, "out_dir": "/w"}, False, {"ra": 10.0, "dec": -5.0},
        {"mode": "files", "starless_path": "", "stars_path": "", "ra": "10.0", "dec": "-5.0",
         "target_type": "nebula", "distance": "", "duration": "3.0", "fps": "30", "loop": "None",
         "star_quality": "high", "dynamic": "sdr", "gaia": "cache", "object_name": "", "out_dir": "/w"},
        ["youtube_1080"],
        False,
        {"mode": "files", "starless_path": "", "stars_path": "", "target": {"ra": 10.0, "dec": -5.0},
         "target_type": "nebula", "distance": None, "duration": 3.0, "fps": 30, "loop": "none",
         "star_quality": "high", "dynamic": "sdr", "gaia": "cache", "object_name": "", "out_dir": "/w",
         "presets": ["youtube_1080"], "video": False},
        id="defaults"),
    pytest.param(
        {"starless_path": "/a.tif", "stars_path": "/b.tif", "ra": 83.8, "dec": -5.4, "distance": 12.5,
         "loop": "pingpong", "fps": 60, "presets": ["social_vertical", "exhibition"], "video": True,
         "dynamic": "hdr", "star_quality": "medium", "target_type": "galaxy", "gaia": "online",
         "object_name": "M 42", "out_dir": "/w"}, True, None,
        {"mode": "starnet", "starless_path": "/a.tif", "stars_path": "/b.tif", "ra": "83.8", "dec": "-5.4",
         "target_type": "galaxy", "distance": "12.5", "duration": "8.0", "fps": "60", "loop": "Back and forth",
         "star_quality": "medium", "dynamic": "hdr", "gaia": "online", "object_name": "M 42", "out_dir": "/w"},
        ["social_vertical", "exhibition"],
        True,
        {"mode": "starnet", "starless_path": "/a.tif", "stars_path": "/b.tif", "target": {"ra": 83.8, "dec": -5.4},
         "target_type": "galaxy", "distance": 12.5, "duration": 8.0, "fps": 60, "loop": "pingpong",
         "star_quality": "medium", "dynamic": "hdr", "gaia": "online", "object_name": "M 42", "out_dir": "/w",
         "presets": ["social_vertical", "exhibition"], "video": True},
        id="saved-options"),
]


def _dialog_facts(dlg: object) -> tuple[dict, list, bool, dict]:
    """The variables' values (the mode too), the preset boxes that are ticked, the video box, and values()."""
    from loomsiril import presets

    ticked = [pid for pid in presets.PRESET_ORDER if dlg._presets[pid].get()]
    return ({k: var.get() for k, var in dlg._vars.items()}, ticked, dlg._video.get(), dlg.values(presets))


@pytest.mark.parametrize("options, starnet, target, want_vars, want_ticked, want_video, want_values", DIALOG_CASES)
def test_dialog_layout_and_values_are_characterized_without_a_display(
        stub, script, monkeypatch, options, starnet, target, want_vars, want_ticked, want_video, want_values):
    """The dialog built on recording tk stand-ins: widget grid and texts, variables, values() result."""
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options(options), starnet, target)
    assert _fake_grid_rows(dlg.top.children[0]) == DIALOG_LAYOUT
    vars_, ticked, video, values = _dialog_facts(dlg)
    assert vars_ == want_vars
    assert ticked == want_ticked and video is want_video and values == want_values


@pytest.mark.parametrize("options, starnet, target, want_vars, want_ticked, want_video, want_values", DIALOG_CASES)
def test_dialog_layout_and_values_under_a_real_display(
        stub, script, options, starnet, target, want_vars, want_ticked, want_video, want_values):
    """The same characterization on a real Tk window (skips where Tk cannot open one; CI runs it under Xvfb)."""
    tk = _tkinter()
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    try:
        dlg = script.LoomFlyDialog(root, script.DialogValues.from_options(options), starnet, target)
        dlg.top.update_idletasks()
        assert _real_grid_rows(dlg.top.winfo_children()[0]) == DIALOG_LAYOUT
        vars_, ticked, video, values = _dialog_facts(dlg)
        assert vars_ == want_vars
        assert ticked == want_ticked and video is want_video and values == want_values
    finally:
        root.destroy()


def test_connect_failure_raises_and_main_does_not_message(stub, script, fake_pipeline, monkeypatch):
    """The real connect() raises SirilConnectionError; main() must not then call a message box on it."""
    def boom(self):
        raise stub.SirilConnectionError("no Siril pipe")
    monkeypatch.setattr(stub.SirilInterface, "connect", boom)
    script.main(dialog=lambda *a, **k: pytest.fail("no dialog without a connection"))
    assert stub.STATE.messages == [] and fake_pipeline.calls == []


def test_connect_returning_false_is_quiet_too(stub, script, fake_pipeline):
    stub.STATE.connect_ok = False
    script.main(dialog=lambda *a, **k: pytest.fail("no dialog without a connection"))
    assert stub.STATE.messages == [] and fake_pipeline.calls == []
    assert stub.STATE.disconnects == 0


def test_stub_pixel_convention_matches_siril_probe(stub):
    """siril-cli 1.4.4 on a 200x100 TAN image, CRPIX (100.5, 50.5), CRVAL (100, 20): edge convention, y from the top."""
    from sirilpy import SirilInterface as Si

    stub.STATE.loaded = True
    stub.STATE.pixeldata = np.zeros((100, 200), dtype=np.float32)
    stub.STATE.header = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 100.0, "CRVAL2": 20.0,
                         "CRPIX1": 100.5, "CRPIX2": 50.5, "CD1_1": -0.001, "CD1_2": 0.0,
                         "CD2_1": 0.0, "CD2_2": 0.001}
    si = Si()
    x, y = si.radec2pix(100.0, 20.0)
    assert (x, y) == (pytest.approx(100.0), pytest.approx(50.0))
    ra, dec = si.pix2radec(30.0, 90.0)
    assert dec == pytest.approx(19.96, abs=1e-3)              # siril-cli printed 19.96 (2 decimals)


def test_stub_header_is_handed_out_as_cdelt_and_pc(stub):
    """Siril rewrites a CD matrix as CDELT + PC: the stub does the same, so the script's CD path is tested."""
    from sirilpy import SirilInterface as Si

    stub.STATE.loaded = True
    stub.STATE.pixeldata = np.zeros((4, 4), dtype=np.float32)
    s = 0.001
    th = np.radians(30.0)
    stub.STATE.header = {"CRVAL1": 1.0, "CRVAL2": 2.0, "CRPIX1": 2.5, "CRPIX2": 2.5,
                         "CD1_1": -s * np.cos(th), "CD1_2": s * np.sin(th),
                         "CD2_1": s * np.sin(th), "CD2_2": s * np.cos(th)}
    h = Si().get_image_fits_header(return_as="dict")
    assert not any(k in h for k in ("CD1_1", "CD1_2", "CD2_1", "CD2_2"))
    assert h["PC1_2"] == pytest.approx(-0.5) and h["PC2_1"] == pytest.approx(0.5)
    assert h["CDELT1"] * h["PC1_1"] == pytest.approx(-s * np.cos(th))


def test_import_finds_the_checkout_the_script_sits_in(script, monkeypatch):
    """No LOOMSIRIL_SRC: the repository holding Loom/loom_fly_through.py goes first on sys.path."""
    monkeypatch.delenv(script.LOOMSIRIL_SRC_ENV, raising=False)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p or ".").resolve() != REPO])
    assert script._import_loomsiril().__name__ == "loomsiril.siril_bridge"
    assert Path(sys.path[0]).resolve() == REPO
