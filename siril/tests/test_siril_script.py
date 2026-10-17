"""Tests for Loom/loom_fly_through.py against the sirilpy stub (E7).

The stub (tests/sirilpy_stub) is put first on sys.path; the state the script reads from Siril is set in
``sirilpy.STATE``. The dialog is bypassed (main takes a replaceable ``dialog``) and loomsiril.pipeline is a fake
module that records the FlyJob. Passing these tests does NOT mean the script has run inside Siril.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import threading
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
    # the script settles the distance with the dialog's resolver before the layers: a canned answer, never the network
    from loomsiril import fly_distance

    mod.resolved = []

    def resolve(target, kind="nebula", gaia_source="cache", typed=None, radius=None, **kw):
        mod.resolved.append({"target": target, "kind": kind, "gaia": gaia_source, "radius": radius})
        return fly_distance.DistanceResult(1500.0, "star", "illuminating star G 8.8, Gaia online")

    mod.resolver = resolve
    monkeypatch.setattr(fly_distance, "resolve", resolve)
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

    def dialog(options, discover, target, siril=None, **kw):
        seen.update(options=options, discover=discover, target=target, siril=siril)
        return values

    script.main(dialog=dialog)

    assert callable(seen["discover"]) and seen["siril"] is not None
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
    assert stub.STATE.progress[-1] == ("rendering frames", 1.0)      # the run's last state (done: 100 %)
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
    script.main(dialog=lambda *a, **k: _values(tmp_path, mode="extract"))
    assert "No star-extraction tool is chosen" in stub.STATE.messages[-1][1]
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
    cli = _fake_cli_fail(tmp_path / "starnet2") if case == "starnet_error" else _fake_cli_copy(tmp_path / "starnet2")
    if case == "cancelled":
        fake_pipeline.result = SimpleNamespace(written=0, reused=0, cancelled=True, videos=[], commands=[], failed=[])
    _, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    script.main(dialog=lambda *a, **k: _values(tmp_path, mode="extract", star_tool="starnet2", star_tool_path=cli,
                                               starnet_licence=STARNET_LICENCE_REV))
    assert list(root.iterdir()) == []
    if case == "starnet_error":
        assert fake_pipeline.calls == [] and "StarNet exited" in stub.STATE.messages[-1][1]
    else:
        assert len(fake_pipeline.calls) == 1


def test_starnet_mode_runs_the_cli_and_splits(stub, script, fake_pipeline, tmp_path, monkeypatch):
    cli = _fake_cli_copy(tmp_path / "starnet2")
    field, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    script.main(dialog=lambda *a, **k: _values(tmp_path, mode="extract", star_tool="starnet2", star_tool_path=cli,
                                               starnet_licence=STARNET_LICENCE_REV))
    job = fake_pipeline.calls[0]
    image = pixels[::-1].astype(np.float32) / 65535            # the top-down image the script built
    assert np.allclose(job.starless[0], image, atol=2 / 65535)  # copy StarNet: starless = image ...
    assert np.allclose(job.stars[0], 0.0, atol=1e-6)            # ... so there are no stars left


def test_starnet_mode_without_the_licence_accepted_is_refused(stub, script, fake_pipeline, tmp_path, monkeypatch):
    """Without the current licence revision, StarNet never runs (the dialog gate is backed up here too)."""
    cli = _fake_cli_copy(tmp_path / "starnet2")
    _, pixels, header_fits, _ = _field_and_header()
    _load_siril_image(stub, pixels, header_fits)
    for licence in (None, "2025-01-01"):
        script.main(dialog=lambda *a, **k: _values(tmp_path, mode="extract", star_tool="starnet2", star_tool_path=cli,
                                                   starnet_licence=licence))
        kind, text = stub.STATE.messages[-1]
        assert kind == "error" and "licence accepted" in text
    assert fake_pipeline.calls == []


def test_licence_check_asks_only_for_a_new_revision(monkeypatch):
    asked = []
    monkeypatch.setattr(starnet_licence, "ask", lambda parent, binary, text=None: asked.append(binary) or STARNET_LICENCE_REV)
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


def test_dialog_render_in_starnet_mode_needs_the_licence(stub, script, monkeypatch, tmp_path):
    """_ok: declining keeps the dialog open with StarNet off; accepting remembers the revision in the values. The
    licence file is read on the worker, not on the Tk thread."""
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    shown = []
    msg = ModuleType("tkinter.messagebox")
    msg.showinfo = msg.showerror = lambda *a, **k: shown.append(a[1])
    tk.messagebox = msg
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", msg)
    from loomsiril import fly_tools

    texts = []
    monkeypatch.setattr(starnet_licence, "display_text", lambda binary: texts.append(threading.current_thread())
                        or "the licence")
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({"out_dir": str(tmp_path)}), None, None)
    dlg._on_discovered(fly_tools.Discovery([fly_tools.ExtractTool("starnet2", "StarNet2", "/x/starnet2")]))
    dlg._mode.set("extract")
    asked = []
    monkeypatch.setattr(starnet_licence, "ask", lambda parent, binary, text=None: asked.append(text) or None)
    _settle(dlg)
    dlg._ok()
    _settle(dlg)
    assert dlg.result is None and dlg._mode.get() == "files" and asked == ["the licence"]
    assert shown == ["StarNet mode stays off until its licence is accepted."]
    assert texts and all(t is not threading.current_thread() for t in texts)
    dlg._mode.set("extract")
    monkeypatch.setattr(starnet_licence, "ask", lambda parent, binary, text=None: STARNET_LICENCE_REV)
    _settle(dlg)
    dlg._ok()
    _settle(dlg)
    assert dlg.result["mode"] == "extract" and dlg.result["star_tool"] == "starnet2"
    assert dlg.result["starnet_licence"] == STARNET_LICENCE_REV


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
    assert v.fps == 30 and v.loop == "none" and v.video is True and v.distance is None
    assert v.dec is script._FROM_TARGET                 # not saved: the dialog shows the target's Dec


def test_dialog_values_defaults_come_from_presets_and_the_working_folder(stub, script, monkeypatch):
    from loomsiril import presets

    monkeypatch.chdir(Path(__file__).resolve().parent)
    v = script.DialogValues.from_options({})
    assert v.star_quality == presets.STAR_QUALITY_DEFAULT
    assert v.out_dir == str(Path(__file__).resolve().parent)
    assert v.starless_path == "" and v.target_type == "nebula" and v.dynamic == "sdr" and v.gaia == "cache"


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
        dlg = script.LoomFlyDialog(root, script.DialogValues.from_options({"duration": 3.0}), None,
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
        self._traces: list = []

    def get(self) -> object:
        return self._value

    def set(self, value: object) -> None:
        self._value = value
        for callback in list(self._traces):
            callback()

    def trace_add(self, mode: str, callback: object) -> None:
        self._traces.append(callback)


class _FakeWidget:
    """A tkinter widget stand-in: remembers its parent, its options and where it was gridded."""

    def __init__(self, master: object = None, *args: object, **kw: object) -> None:
        self.kind = type(self).__name__
        self.kw = kw
        self.grid_kw: dict | None = None
        self.children: list[_FakeWidget] = []
        self.master = master
        if isinstance(master, _FakeWidget):
            master.children.append(self)

    def grid(self, **kw: object) -> None:
        self.grid_kw = kw

    def place(self, **kw: object) -> None:
        self.place_kw = kw

    def place_forget(self) -> None:
        self.place_kw = None

    def focus_set(self) -> None:
        pass

    def protocol(self, *args: object) -> None:
        pass

    def title(self, *args: object) -> None:
        pass

    def destroy(self) -> None:
        pass

    def configure(self, **kw: object) -> None:
        self.kw.update(kw)

    def __setitem__(self, key: str, value: object) -> None:
        self.kw[key] = value

    def state(self, *args: object) -> None:
        pass

    def start(self, *args: object) -> None:
        pass

    def stop(self) -> None:
        pass

    def grid_remove(self) -> None:
        self.grid_kw = None

    def after(self, ms: object, fn: object = None, *args: object) -> str:
        """Records the callback instead of scheduling it: ``afters`` maps each pending id to (ms, fn)."""
        self.afters = getattr(self, "afters", {})
        self._after_seq = getattr(self, "_after_seq", 0) + 1
        self.afters[f"after#{self._after_seq}"] = (ms, fn)
        return f"after#{self._after_seq}"

    def after_cancel(self, after_id: str) -> None:
        getattr(self, "afters", {}).pop(after_id, None)

    def bind(self, sequence: object = None, func: object = None, add: object = None) -> None:
        self.bindings = getattr(self, "bindings", {})
        self.bindings.setdefault(sequence, []).append(func)

    def bind_all(self, sequence: object = None, func: object = None, add: object = None) -> None:
        self.all_bindings = getattr(self, "all_bindings", {})
        self.all_bindings.setdefault(sequence, []).append(func)

    # what the scroll area asks of a Canvas, a Scrollbar and the screen (sizes are fixed numbers)
    screen_height = 1000
    req_height = 400
    req_width = 300

    def columnconfigure(self, *args: object, **kw: object) -> None:
        pass

    rowconfigure = columnconfigure

    def update_idletasks(self) -> None:
        pass

    def winfo_screenheight(self) -> int:
        return self.screen_height

    def winfo_reqheight(self) -> int:
        return self.req_height

    def winfo_reqwidth(self) -> int:
        return self.req_width

    def create_window(self, *args: object, **kw: object) -> int:
        return 1

    def itemconfigure(self, *args: object, **kw: object) -> None:
        pass

    def bbox(self, *args: object) -> tuple:
        return (0, 0, self.req_width, self.req_height)

    def yview(self, *args: object) -> None:
        pass

    def set(self, *args: object) -> None:
        pass

    def invoke(self) -> None:
        """A click on a button: runs its command."""
        cmd = self.kw.get("command")
        if cmd:
            cmd()


class _FakeStyle:
    def lookup(self, *args: object) -> str:
        return ""


def _fake_tkinter(monkeypatch: pytest.MonkeyPatch) -> None:
    """tkinter and tkinter.ttk replaced by recording stand-ins: the dialog's layout is built with no display."""
    ttk = ModuleType("tkinter.ttk")
    tk = ModuleType("tkinter")
    for name in ("Frame", "Label", "Entry", "Combobox", "Radiobutton", "Checkbutton", "Button", "Progressbar",
                 "Scrollbar", "Spinbox", "Scale"):
        setattr(ttk, name, type(name, (_FakeWidget,), {}))
    ttk.Style = _FakeStyle
    tk.Canvas = type("Canvas", (_FakeWidget,), {})
    tk.Tk = type("Tk", (_FakeWidget,), {})
    tk.Toplevel = type("Toplevel", (_FakeWidget,), {})
    tk.StringVar = tk.BooleanVar = tk.DoubleVar = _FakeVar
    tk.ttk = ttk
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", ttk)


#: Stands for the text of a label bound to a variable (a status line): it is dynamic, so the layout only records that
#: the label is there. (Under real Tk, cget("text") of such a label is the variable's current value.)
DYNAMIC = "<variable text>"


def _fake_grid_rows(parent: _FakeWidget) -> list[tuple]:
    """(row, column, class, text) of each gridded widget under ``parent``, in grid order; a frame's children follow it."""
    out: list[tuple] = []
    kids = [w for w in parent.children if w.grid_kw is not None]
    for w in sorted(kids, key=lambda w: (w.grid_kw["row"], w.grid_kw["column"])):
        text = DYNAMIC if (w.kind == "Label" and "textvariable" in w.kw) else w.kw.get("text")
        out.append((w.grid_kw["row"], w.grid_kw["column"], w.kind, text))
        out.extend(_fake_grid_rows(w))
    return out


def _real_grid_rows(parent: object) -> list[tuple]:
    """The same tuples as _fake_grid_rows, read from a real Tk widget tree."""
    out: list[tuple] = []
    kids = [w for w in parent.winfo_children() if w.grid_info()]
    for w in sorted(kids, key=lambda w: (w.grid_info()["row"], w.grid_info()["column"])):
        kind = w.winfo_class().removeprefix("T")
        if kind in ("Entry", "Combobox", "Frame", "Progressbar", "Spinbox", "Scale"):
            text = None
        elif str(w.cget("textvariable")):          # a status label: its text is the variable's, and changes
            text = DYNAMIC
        else:
            text = w.cget("text") or None
        out.append((w.grid_info()["row"], w.grid_info()["column"], kind, text))
        out.extend(_real_grid_rows(w))
    return out


#: The dialog's layout (grid order; a frame's children follow the frame). The fake and the real tk must both show it.
DIALOG_LAYOUT = [
    (0, 0, "Label", "Input"),
    (1, 0, "Radiobutton", "Extract stars from the image with:"),
    (1, 1, "Combobox", None),
    (1, 2, "Progressbar", None),
    (2, 0, "Label", "Image file (TIFF/FITS)"),
    (2, 1, "Entry", None),
    (2, 2, "Button", "Browse..."),
    (3, 1, "Label", DYNAMIC),                              # the hint, or the Image file's size note
    (4, 1, "Label", DYNAMIC),
    (5, 0, "Radiobutton", "Starless and stars files"),
    (6, 0, "Label", "Starless file (TIFF/FITS)"),
    (6, 1, "Entry", None),
    (6, 2, "Button", "Browse..."),
    (7, 0, "Label", "Stars file (TIFF/FITS)"),
    (7, 1, "Entry", None),
    (7, 2, "Button", "Browse..."),
    (8, 0, "Label", "Target"),
    (9, 0, "Label", "Target RA (deg)"),
    (9, 1, "Entry", None),
    (10, 0, "Label", "Target Dec (deg)"),
    (10, 1, "Entry", None),
    (11, 0, "Label", "Target type"),
    (11, 1, "Combobox", None),
    (12, 0, "Label", "Distance (pc, blank = auto)"),
    (12, 1, "Entry", None),
    (13, 1, "Label", DYNAMIC),
    (14, 0, "Label", "Gaia data"),
    (14, 1, "Combobox", None),
    (15, 0, "Label", "Gaia folder"),
    (15, 1, "Entry", None),
    (15, 2, "Button", "Browse..."),
    (16, 1, "Label", DYNAMIC),                             # what the Gaia folder holds, and Siril's own catalogue
    (17, 1, "Label", DYNAMIC),
    (18, 0, "Label", "Clip"),
    (19, 0, "Label", "Duration (s)"),
    (19, 1, "Entry", None),
    (20, 0, "Label", "Frames per second"),
    (20, 1, "Combobox", None),
    (21, 0, "Label", "Orientation"),
    (21, 1, "Combobox", None),
    (21, 2, "Label", DYNAMIC),
    (22, 0, "Label", "Loop"),
    (22, 1, "Combobox", None),
    (22, 2, "Label", DYNAMIC),
    (23, 0, "Label", "Star quality"),
    (23, 1, "Combobox", None),
    (24, 0, "Label", "Dynamic range"),
    (24, 1, "Combobox", None),
    (25, 0, "Frame", None),
    (0, 0, "Checkbutton", "Social 1080×1920"),
    (0, 1, "Checkbutton", "Social 1080×1080"),
    (1, 0, "Checkbutton", "YouTube 3840×2160"),
    (1, 1, "Checkbutton", "YouTube 1920×1080"),
    (2, 0, "Checkbutton", "Exhibition 3840×2160 (loop)"),
    (26, 0, "Label", "Output"),
    (27, 0, "Label", "Object name"),
    (27, 1, "Entry", None),
    (27, 2, "Button", "Find"),
    (28, 0, "Label", "Output folder"),
    (28, 1, "Entry", None),
    (28, 2, "Button", "Browse..."),
    (29, 0, "Checkbutton", "Encode video (ffmpeg)"),
    (29, 1, "Label", DYNAMIC),
    (30, 0, "Label", "Music"),
    (30, 1, "Entry", None),
    (30, 2, "Button", "Browse..."),
    (31, 1, "Checkbutton", "Fade in/out"),
    (31, 2, "Label", DYNAMIC),                             # why the music is greyed, or what is wrong with its file
    (32, 0, "Button", "\u25b8 Video details"),               # the Advanced sections: collapsed, so no body frame
    (32, 1, "Label", DYNAMIC),                             # why all its controls are greyed (empty while some apply)
    (34, 0, "Button", "\u25b8 Logo"),
    (34, 1, "Label", DYNAMIC),
    (36, 0, "Button", "\u25b8 Stars and motion"),
    (36, 1, "Label", DYNAMIC),
    (38, 0, "Button", "\u25b8 HDR"),
    (38, 1, "Label", DYNAMIC),
]

#: The footer, outside the scroll area: the reason line, the draft note and the buttons.
FOOTER_LAYOUT = [
    (0, 0, "Label", DYNAMIC),
    (1, 0, "Label", "A draft is always SDR, 480 px, at a reduced frame rate."),
    (2, 0, "Frame", None),
    (0, 0, "Button", "Cancel"),
    (0, 1, "Button", "Render"),
]

#: The Stars-and-motion and HDR controls in values(): their defaults (engine units); Travel is None (automatic).
FLY_OPTION_VALUES = {"travel": None, "easing": "smoothstep", "growth": 0.15, "backdrop_motion": 0.4, "brightening": True,
                     "twinkle": 0.03, "bloom": 1.0, "motion_blur": True, "star_saturation": 1.0, "hdr_peak": 1000,
                     "star_hdr": True, "hdr_transfer_social_vertical": "hlg", "hdr_transfer_social_square": "hlg",
                     "hdr_transfer_youtube_4k": "hlg", "hdr_transfer_youtube_1080": "hlg", "hdr_transfer_exhibition": "pq"}

#: The Video details controls in values(): no ffmpeg is known yet (blocked discovery), so no format is offered.
VIDEO_OPTION_VALUES = {"ffmpeg_path": "", "format": None, "quality": "high"}

#: The Logo controls in values(): no logo, Placement Off, Opacity 100 %, Fade in after 0 s (PixInsight's defaults).
LOGO_OPTION_VALUES = {"logo_path": "", "logo_place": "off", "logo_opacity": 100, "logo_delay": 0}

#: Two starting points: the defaults (with the target's RA and Dec), and every saved option set.
DIALOG_CASES = [
    pytest.param(
        {"duration": 3.0, "out_dir": "/w"}, None, {"ra": 10.0, "dec": -5.0},
        {"mode": "files", "image_path": "", "starless_path": "", "stars_path": "", "ra": "10.0", "dec": "-5.0",
         "target_type": "nebula", "distance": "", "duration": "3.0", "fps": "30", "orientation": "Horizontal", "loop": "None",
         "star_quality": "high", "dynamic": "sdr", "gaia": "cache", "gaia_folder": "", "object_name": "", "out_dir": "/w",
         "music_path": ""},
        ["youtube_1080"],
        True,
        {"mode": "files", "starless_path": "", "stars_path": "", "star_tool": "", "star_tool_path": "", "image_path": "", "target": {"ra": 10.0, "dec": -5.0},
         "target_type": "nebula", "distance": None, "duration": 3.0, "fps": 30, "orientation": "landscape", "loop": "none",
         "star_quality": "high", "dynamic": "sdr", "gaia": "cache", "object_name": "", "out_dir": "/w",
         "presets": ["youtube_1080"], "video": True,   # encode by default
         "music_path": "", "music_fade": True, **FLY_OPTION_VALUES, **VIDEO_OPTION_VALUES, **LOGO_OPTION_VALUES},
        id="defaults"),
    pytest.param(
        {"starless_path": "/a.tif", "stars_path": "/b.tif", "ra": 83.8, "dec": -5.4, "distance": 12.5,
         "loop": "pingpong", "fps": 60, "presets": ["social_vertical", "exhibition"], "video": True,
         "dynamic": "hdr", "star_quality": "medium", "target_type": "galaxy", "gaia": "online",
         "object_name": "M 42", "out_dir": "/w", "music_path": "/m/song.mp3", "music_fade": False}, None, None,
        {"mode": "files", "image_path": "", "starless_path": "/a.tif", "stars_path": "/b.tif", "ra": "83.8", "dec": "-5.4",
         "target_type": "galaxy", "distance": "12.5", "duration": "8.0", "fps": "60", "orientation": "Horizontal", "loop": "Back and forth",
         "star_quality": "medium", "dynamic": "hdr", "gaia": "online", "gaia_folder": "", "object_name": "M 42", "out_dir": "/w",
         "music_path": "/m/song.mp3"},
        ["social_vertical", "exhibition"],
        True,
        {"mode": "files", "starless_path": "/a.tif", "stars_path": "/b.tif", "star_tool": "", "star_tool_path": "", "image_path": "", "target": {"ra": 83.8, "dec": -5.4},
         "target_type": "galaxy", "distance": 12.5, "duration": 8.0, "fps": 60, "orientation": "landscape", "loop": "pingpong",
         "star_quality": "medium", "dynamic": "hdr", "gaia": "online", "object_name": "M 42", "out_dir": "/w",
         "presets": ["social_vertical", "exhibition"], "video": True,
         "music_path": "/m/song.mp3", "music_fade": False, **FLY_OPTION_VALUES, **VIDEO_OPTION_VALUES, **LOGO_OPTION_VALUES},
        id="saved-options"),
]


@pytest.fixture
def blocked_discover():
    """A discover() that does not return until the test is over: the dialog stays in its "looking" state."""
    gate = threading.Event()
    yield lambda: gate.wait(10)
    gate.set()


def _dialog_facts(dlg: object) -> tuple[dict, list, bool, dict]:
    """The variables' values (the mode too), the preset boxes that are ticked, the video box, and values()."""
    from loomsiril import presets

    ticked = [pid for pid in presets.PRESET_ORDER if dlg._presets[pid].get()]
    return ({k: var.get() for k, var in dlg._vars.items()}, ticked, dlg._video.get(), dlg.values(presets))


@pytest.mark.parametrize("options, discover, target, want_vars, want_ticked, want_video, want_values", DIALOG_CASES)
def test_dialog_layout_and_values_are_characterized_without_a_display(
        stub, script, monkeypatch, blocked_discover, options, discover, target, want_vars, want_ticked, want_video, want_values):
    """The dialog built on recording tk stand-ins: widget grid and texts, variables, values() result."""
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options(options), blocked_discover, target)
    assert _fake_grid_rows(dlg._frame) == DIALOG_LAYOUT
    assert _fake_grid_rows(dlg._footer) == FOOTER_LAYOUT
    vars_, ticked, video, values = _dialog_facts(dlg)
    assert vars_ == want_vars
    assert ticked == want_ticked and video is want_video and values == want_values


@pytest.mark.parametrize("options, discover, target, want_vars, want_ticked, want_video, want_values", DIALOG_CASES)
def test_dialog_layout_and_values_under_a_real_display(
        stub, script, blocked_discover, options, discover, target, want_vars, want_ticked, want_video, want_values):
    """The same characterization on a real Tk window (skips where Tk cannot open one; CI runs it under Xvfb)."""
    tk = _tkinter()
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    try:
        dlg = script.LoomFlyDialog(root, script.DialogValues.from_options(options), blocked_discover, target)
        dlg.top.update_idletasks()
        assert _real_grid_rows(dlg._frame) == DIALOG_LAYOUT
        assert _real_grid_rows(dlg._footer) == FOOTER_LAYOUT
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


# ---------------------------------------------------------------- Browse buttons, tool list, discovery thread

def _fake_filedialog(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[dict]:
    """tkinter.filedialog.askopenfilename replaced: returns ``answers`` in turn, records the options it was given."""
    import tkinter as tk

    calls: list[dict] = []
    fd = ModuleType("tkinter.filedialog")

    def ask(**kw):
        calls.append(kw)
        return answers.pop(0)

    fd.askopenfilename = ask
    tk.filedialog = fd
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fd)
    return calls


def _settle(dlg) -> None:
    """Runs the dialog's pending debounce and the disk-work jobs (validate, prepare) to the end, as Tk's loop would."""
    dlg._cancel_after("debounce")
    dlg._run_validation()
    for job in list(dlg._jobs):
        if job["thread"].name in ("loomsiril-validate", "loomsiril-prepare"):
            job["thread"].join(5)
            for key, (_ms, fn) in list(dlg.top.afters.items()):     # the timer "fires": it leaves Tk's queue
                if fn is job["poll"]:
                    del dlg.top.afters[key]
            job["poll"]()


def _buttons(dlg, text: str) -> list:
    return [w for w in dlg._frame.children if w.kind == "Button" and w.kw.get("text") == text]


def _tools(*ids: str):
    from loomsiril import fly_tools

    labels = dict(fly_tools.TOOL_IDS)
    return fly_tools.Discovery([fly_tools.ExtractTool(i, labels[i], f"/opt/{i}") for i in ids])


def test_browse_buttons_fill_the_boxes_and_share_the_last_folder(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    calls = _fake_filedialog(monkeypatch, ["/data/m42/starless.tif", "", "/data/m42/stars.fits"])
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({}), blocked_discover, None)
    _image_btn, starless_btn, stars_btn, _gaia_btn, _folder_btn, _music_btn = _buttons(dlg, "Browse...")
    starless_btn.kw["command"]()
    assert dlg._vars["starless_path"].get() == "/data/m42/starless.tif"
    stars_btn.kw["command"]()                                   # cancelled: the box stays as it was
    assert dlg._vars["stars_path"].get() == ""
    stars_btn.kw["command"]()
    assert dlg._vars["stars_path"].get() == "/data/m42/stars.fits"
    assert calls[1]["initialdir"] == "/data/m42"                # the second picker opens where the first one ended
    patterns = " ".join(p for _label, p in calls[0]["filetypes"])
    assert "*.tif" in patterns and "*.fits" in patterns and "xisf" not in patterns


def test_tool_list_shows_only_the_tools_found_and_the_saved_choice_is_kept(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({"star_tool": "syqon-cli"}),
                               blocked_discover, None)
    assert dlg._radio.kw["state"] == "disabled" and dlg._mode.get() == "files"      # nothing offered while looking
    dlg._on_discovered(_tools("starnet2", "syqon-cli"))
    assert dlg._tool_box.kw["values"] == ["StarNet2", "SyQon Studio Axiom"]        # StarXTerminator: not found, not listed
    assert dlg._tool.get() == "SyQon Studio Axiom" and dlg._radio.kw["state"] == "normal"
    assert dlg._mode.get() == "extract"
    values = dlg.values(__import__("loomsiril.presets", fromlist=["x"]))
    assert values["star_tool"] == "syqon-cli" and values["star_tool_path"] == "/opt/syqon-cli"   # the chosen one runs


def test_a_saved_tool_that_vanished_falls_back_to_the_first_and_says_so(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({"star_tool": "rcastro"}),
                               blocked_discover, None)
    dlg._on_discovered(_tools("starnet2", "syqon-cli"))
    assert dlg._tool.get() == "StarNet2"
    assert "StarXTerminator was not found; using StarNet2" in dlg._status.get()


def test_no_tool_found_disables_the_option_with_a_reason_and_keeps_the_files(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    import tkinter as tk
    from loomsiril import fly_tools

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({}), blocked_discover, None)
    dlg._on_discovered(fly_tools.Discovery())
    assert dlg._radio.kw["state"] == "disabled" and dlg._tool_box.kw["state"] == "disabled"
    assert dlg._status.get() == "No star-extraction tool found: StarNet2, SyQon Studio or StarXTerminator"
    assert dlg._mode.get() == "files"
    assert dlg.values(__import__("loomsiril.presets", fromlist=["x"]))["mode"] == "files"


def test_discovery_runs_on_a_worker_thread_and_the_dialog_does_not_wait(stub, script, monkeypatch):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    gate, ran_on = threading.Event(), []

    def slow_discover():
        ran_on.append(threading.current_thread())
        gate.wait(10)
        return _tools("starnet2")

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({}), slow_discover, None)
    assert gate.is_set() is False and dlg._tools == []              # the constructor returned with the search running
    job = next(j for j in dlg._jobs if j["thread"].name == "loomsiril-discover")
    poll = job["poll"]                                                # the Tk thread only polls
    poll()
    assert dlg._tools == []
    gate.set()
    for _ in range(200):
        poll()
        if dlg._tools:
            break
        threading.Event().wait(0.01)
    assert [t.id for t in dlg._tools] == ["starnet2"]
    assert ran_on[0] is not threading.current_thread() and ran_on[0].name == "loomsiril-discover"


def test_a_failing_discovery_is_a_status_line_not_a_crash(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({}), blocked_discover, None)
    dlg._on_discovered(RuntimeError("boom"))
    assert "boom" in dlg._status.get() and dlg._radio.kw["state"] == "disabled"


def test_draft_button_hands_the_checked_values_to_the_draft_and_keeps_the_dialog_open(
        stub, script, monkeypatch, blocked_discover, tmp_path):
    tmp = tmp_path
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    got = []
    msg = ModuleType("tkinter.messagebox")
    msg.showinfo = msg.showerror = lambda *a, **k: got.append(("box", a))
    tk.messagebox = msg
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", msg)
    (tmp / "a.tif").write_text("x")
    (tmp / "b.tif").write_text("x")
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options(
        {"out_dir": str(tmp), "starless_path": str(tmp / "a.tif"), "stars_path": str(tmp / "b.tif")}),
        blocked_discover, None, lambda window, values: got.append((window, values)))
    _settle(dlg)
    (draft_btn,) = _buttons_in(dlg, "Draft")
    draft_btn.kw["command"]()
    assert dlg._acting and dlg._draft_btn.kw["state"] == "disabled"          # preparing: on a worker
    _settle(dlg)
    assert got and got[0][0] is dlg.top and got[0][1]["mode"] == "files" and got[0][1]["out_dir"] == str(tmp)
    assert got[0][1]["image_stamp"] is None
    assert dlg.result is None and dlg._closed is False and not dlg._acting


def _buttons_in(dlg, text: str) -> list:
    out = []

    def walk(w):
        for c in w.children:
            if c.kind == "Button" and c.kw.get("text") == text:
                out.append(c)
            walk(c)

    walk(dlg.top)
    return out


# ---------------------------------------------------------------- live greying, Image file, output folder

def _states(dlg, group: str) -> set:
    return {w.kw.get("state", "normal") for w, _on in dlg._w[group]}


def _dialog(script, monkeypatch, discover, **options):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    msg = ModuleType("tkinter.messagebox")
    shown: list = []
    msg.showinfo = msg.showerror = lambda *a, **k: shown.append(a[1])
    tk.messagebox = msg
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", msg)
    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options(options), discover, None)
    return dlg, shown


def test_changing_the_mode_greys_the_other_group_at_once(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    dlg._on_discovered(_tools("starnet2"))
    assert dlg._mode.get() == "extract"
    assert _states(dlg, "extract") == {"normal", "readonly"} and _states(dlg, "files") == {"disabled"}
    dlg._mode.set("files")                                   # a radio changed: no re-open, no explicit sync
    assert _states(dlg, "extract") == {"disabled"} and _states(dlg, "files") == {"normal"}
    # the Image file row (label, box, Browse) is part of the extract group, the two layer rows of the files group
    assert len(dlg._w["extract"]) == 1 + 3 + 1 and len(dlg._w["files"]) == 6


def test_without_a_tool_the_extract_group_stays_grey_with_the_reason(stub, script, monkeypatch, blocked_discover):
    from loomsiril import fly_tools

    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    dlg._on_discovered(fly_tools.Discovery())
    assert _states(dlg, "extract") == {"disabled"} and _states(dlg, "files") == {"normal"}
    assert dlg._radio.kw["state"] == "disabled" and "No star-extraction tool found" in dlg._status.get()


def test_render_and_draft_follow_the_inputs_live_with_a_reason(stub, script, monkeypatch, blocked_discover, tmp_path):
    _fake_tkinter(monkeypatch)
    import tkinter as tk

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options({"out_dir": str(tmp_path)}),
                               blocked_discover, None, lambda w, v: None)
    assert dlg._render_btn.kw["state"] == "disabled" and dlg._draft_btn.kw["state"] == "disabled"
    assert dlg._reason.get() == "Choose the starless file."
    (tmp_path / "a.tif").write_text("x")
    (tmp_path / "b.tif").write_text("x")
    dlg._vars["starless_path"].set(str(tmp_path / "a.tif"))
    assert dlg._reason.get() == "Choose the stars file."
    dlg._vars["stars_path"].set(str(tmp_path / "b.tif"))
    assert dlg._reason.get() == "Checking the files..."                       # the disk is asked on a worker
    assert dlg._render_btn.kw["state"] == "disabled" and "debounce" in dlg._afters
    _settle(dlg)
    assert dlg._reason.get() == "" and dlg._render_btn.kw["state"] == "normal" and dlg._draft_btn.kw["state"] == "normal"
    dlg._vars["stars_path"].set(str(tmp_path / "gone.tif"))
    _settle(dlg)
    assert "does not exist or cannot be read" in dlg._reason.get() and dlg._render_btn.kw["state"] == "disabled"


def test_no_file_access_runs_on_the_tk_thread(stub, script, monkeypatch, blocked_discover, tmp_path):
    """Typing, syncing and clicking Render do their disk work (checks, makedirs, licence text) on worker threads."""
    from loomsiril import fly_checks

    seen = []
    real_validate, real_out = fly_checks.validate, fly_checks.check_out_dir
    monkeypatch.setattr(fly_checks, "validate", lambda reqs: seen.append(("validate", threading.current_thread()))
                        or real_validate(reqs))
    monkeypatch.setattr(fly_checks, "check_out_dir", lambda *a, **k: seen.append(("out", threading.current_thread()))
                        or real_out(*a, **k))
    made = []
    real_makedirs = os.makedirs
    monkeypatch.setattr(fly_checks.os, "makedirs", lambda path, *a, **k: made.append((str(path), threading.current_thread()))
                        or real_makedirs(path, *a, **k))
    dlg, _ = _dialog(script, monkeypatch, blocked_discover, out_dir=str(tmp_path / "new"))
    (tmp_path / "a.tif").write_text("x")
    dlg._vars["starless_path"].set(str(tmp_path / "a.tif"))
    dlg._vars["stars_path"].set(str(tmp_path / "a.tif"))
    assert seen == [] or all(t is not threading.current_thread() for _n, t in seen)      # nothing so far, or elsewhere
    _settle(dlg)
    dlg._ok()
    _settle(dlg)
    assert (tmp_path / "new").is_dir() and dlg.result is not None
    ours = [t for path, t in made if path == str(tmp_path / "new")]
    assert ours and all(t is not threading.current_thread() for t in ours)               # makedirs ran, and never on Tk
    assert seen and all(t is not threading.current_thread() for _n, t in seen)
    assert {n for n, _t in seen} == {"validate", "out"}


def test_extract_mode_without_a_tool_chosen_blocks_render(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    dlg._mode.set("extract")
    assert dlg._render_btn.kw["state"] == "disabled" and "Still looking" in dlg._reason.get()


def test_video_is_greyed_with_a_reason_when_ffmpeg_is_missing(stub, script, monkeypatch, blocked_discover):
    from loomsiril import fly_tools

    dlg, _ = _dialog(script, monkeypatch, blocked_discover, video=True)
    dlg._on_discovered(fly_tools.Discovery(ffmpeg=False))
    assert _states(dlg, "video") == {"disabled"} and dlg._video.get() is False
    assert "ffmpeg was not found" in dlg._video_note.get()
    dlg2, _ = _dialog(script, monkeypatch, blocked_discover, video=True)
    dlg2._on_discovered(fly_tools.Discovery(ffmpeg=True))
    assert _states(dlg2, "video") == {"normal"} and dlg2._video.get() is True


def test_loop_is_greyed_when_only_the_exhibition_preset_is_chosen(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    assert _states(dlg, "loop") == {"normal", "readonly"}
    for pid, var in dlg._presets.items():
        var.set(pid == "exhibition")
    assert _states(dlg, "loop") == {"disabled"} and "always loops" in dlg._loop_note.get()


def test_an_image_file_that_does_not_exist_is_refused_with_a_message(stub, script, monkeypatch, blocked_discover,
                                                                       tmp_path):
    dlg, shown = _dialog(script, monkeypatch, blocked_discover, out_dir=str(tmp_path))
    dlg._on_discovered(_tools("starnet2"))
    dlg._vars["image_path"].set(str(tmp_path / "missing.fits"))
    _settle(dlg)
    assert "The image file does not exist or cannot be read" in dlg._reason.get()
    assert dlg._render_btn.kw["state"] == "disabled"
    dlg._ok()                                                  # clicked anyway: a message, no crash, no result
    _settle(dlg)
    assert dlg.result is None and "The image file does not exist or cannot be read" in shown[-1]
    (tmp_path / "m42.fits").write_text("x")
    dlg._vars["image_path"].set(str(tmp_path / "m42.fits"))
    _settle(dlg)
    assert dlg._reason.get() == "" and dlg._render_btn.kw["state"] == "normal"
    assert dlg.values(__import__("loomsiril.presets", fromlist=["x"]))["image_path"] == str(tmp_path / "m42.fits")


def test_each_browse_button_fills_its_own_box(stub, script, monkeypatch, blocked_discover, tmp_path):
    """Image, starless, stars and output-folder Browse...: each writes the picked path into its own variable and
    nothing else, so a swapped or missing variable fails here."""
    from loomsiril.loom import dialog as loom_dialog

    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    picks = {"image": "/p/image.fits", "starless": "/p/starless.tif", "stars": "/q/stars.tif", "music": "/q/song.mp3"}  # the folders go through ask_folder
    answers = list(picks.values())
    _fake_filedialog(monkeypatch, answers)
    monkeypatch.setattr(loom_dialog, "ask_folder", lambda title, initial="", parent=None: "/p/renders")
    image_btn, starless_btn, stars_btn, gaia_btn, folder_btn, music_btn = _buttons(dlg, "Browse...")
    names = ("image_path", "starless_path", "stars_path", "out_dir", "music_path")
    before = {n: dlg._vars[n].get() for n in names}
    picked: set = set()
    for button, name, want in ((image_btn, "image_path", "/p/image.fits"), (starless_btn, "starless_path",
                                                                           "/p/starless.tif"),
                               (stars_btn, "stars_path", "/q/stars.tif"), (folder_btn, "out_dir", "/p/renders"),
                               (music_btn, "music_path", "/q/song.mp3")):
        button.kw["command"]()
        assert dlg._vars[name].get() == want
        for other in names:
            if other != name and other not in picked:
                assert dlg._vars[other].get() == before[other]
        picked.add(name)
    assert {n: dlg._vars[n].get() for n in names} == {"image_path": "/p/image.fits",
                                                      "starless_path": "/p/starless.tif",
                                                      "stars_path": "/q/stars.tif", "out_dir": "/p/renders",
                                                      "music_path": "/q/song.mp3"}


def test_output_folder_browse_fills_the_box_and_a_bad_folder_is_refused(stub, script, monkeypatch, blocked_discover,
                                                                        tmp_path):
    from loomsiril.loom import dialog as loom_dialog

    (tmp_path / "a.tif").write_text("x")
    dlg, shown = _dialog(script, monkeypatch, blocked_discover, out_dir=str(tmp_path),
                         starless_path=str(tmp_path / "a.tif"), stars_path=str(tmp_path / "a.tif"))
    asked = []
    monkeypatch.setattr(loom_dialog, "ask_folder", lambda title, initial="", parent=None: asked.append(initial)
                        or str(tmp_path / "renders"))
    _buttons(dlg, "Browse...")[-2].kw["command"]()   # the output folder (the music one follows it)
    assert asked == [str(tmp_path)]                          # it opens on the current value
    assert dlg._vars["out_dir"].get() == str(tmp_path / "renders")
    _settle(dlg)
    dlg._ok()                                                # a missing folder is created (on the worker)
    _settle(dlg)
    assert dlg.result is not None and (tmp_path / "renders").is_dir()
    dlg2, shown2 = _dialog(script, monkeypatch, blocked_discover, out_dir=str(tmp_path / "a.tif" / "sub"),
                           starless_path=str(tmp_path / "a.tif"), stars_path=str(tmp_path / "a.tif"))
    _settle(dlg2)
    dlg2._ok()
    _settle(dlg2)
    assert dlg2.result is None and "output folder" in shown2[-1].lower()


def test_no_callback_is_left_pending_after_the_dialog_closes(stub, script, monkeypatch, blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    dlg._vars["starless_path"].set("/some/where.tif")             # a debounce is pending
    assert dlg.top.afters and dlg._afters                         # the discovery poll and the debounce
    dlg._cancel()
    assert dlg.top.afters == {} and dlg._afters == {} and dlg._closed
    dlg._vars["stars_path"].set("/other.tif")                     # a late change schedules nothing
    assert dlg.top.afters == {}


def test_destroying_the_window_from_outside_cancels_the_pending_callbacks_too(stub, script, monkeypatch,
                                                                              blocked_discover):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover)
    assert dlg.top.afters
    dlg._on_destroy(SimpleNamespace(widget=dlg.top))              # what the <Destroy> binding calls
    assert dlg.top.afters == {}


# ---------------------------------------------------------------- stale results, focus-in, draft run lifetime

def test_a_file_that_appears_after_a_negative_check_is_picked_up_on_focus_in(stub, script, monkeypatch,
                                                                              blocked_discover, tmp_path):
    dlg, _ = _dialog(script, monkeypatch, blocked_discover, out_dir=str(tmp_path),
                     starless_path=str(tmp_path / "a.tif"), stars_path=str(tmp_path / "b.tif"))
    _settle(dlg)
    assert "does not exist or cannot be read" in dlg._reason.get()          # cached as not found
    (tmp_path / "a.tif").write_text("x")
    (tmp_path / "b.tif").write_text("x")
    entry = next(w for w, _on in dlg._w["files"] if getattr(w, "bindings", None) and "<FocusIn>" in w.bindings)
    entry.bindings["<FocusIn>"][0](None)                                     # the box got the focus: ask again
    assert dlg._reason.get() == "Checking the files..." and "debounce" in dlg._afters
    _settle(dlg)
    assert dlg._reason.get() == "" and dlg._render_btn.kw["state"] == "normal"


def test_a_stale_not_found_result_can_never_block_render_when_the_file_now_exists(
        stub, script, monkeypatch, blocked_discover, tmp_path):
    dlg, shown = _dialog(script, monkeypatch, blocked_discover, out_dir=str(tmp_path),
                         starless_path=str(tmp_path / "a.tif"), stars_path=str(tmp_path / "b.tif"))
    _settle(dlg)
    assert dlg._checks[("starless", str(tmp_path / "a.tif"))]                # a stale negative is cached
    (tmp_path / "a.tif").write_text("x")
    (tmp_path / "b.tif").write_text("x")
    dlg._ok()                                                                # clicked: the worker looks again
    _settle(dlg)
    assert dlg.result is not None and shown == []
    assert dlg.result["starless_path"] == str(tmp_path / "a.tif")


def test_browsing_the_same_path_again_rechecks_it(stub, script, monkeypatch, blocked_discover, tmp_path):
    (tmp_path / "a.tif").write_text("x")
    dlg, _ = _dialog(script, monkeypatch, blocked_discover, out_dir=str(tmp_path),
                     starless_path=str(tmp_path / "late.tif"), stars_path=str(tmp_path / "a.tif"))
    _settle(dlg)
    assert "late.tif" in dlg._reason.get()
    (tmp_path / "late.tif").write_text("x")
    _fake_filedialog(monkeypatch, [str(tmp_path / "late.tif")])               # the same value: no variable change
    _buttons(dlg, "Browse...")[1].kw["command"]()
    _settle(dlg)
    assert dlg._reason.get() == ""


def test_browse_folder_does_no_file_check_on_the_tk_thread(stub, script, monkeypatch, blocked_discover, tmp_path):
    import os
    from loomsiril.loom import dialog as loom_dialog

    dlg, _ = _dialog(script, monkeypatch, blocked_discover, out_dir=str(tmp_path))
    monkeypatch.setattr(os.path, "isdir", lambda *a: pytest.fail("isdir on the Tk thread"))
    monkeypatch.setattr(loom_dialog, "ask_folder", lambda title, initial="", parent=None: "")
    _buttons(dlg, "Browse...")[-2].kw["command"]()   # the output folder (the music one follows it)


def test_a_draft_run_is_closed_with_the_dialog_and_leaves_no_callback(stub, script, monkeypatch, blocked_discover,
                                                                      tmp_path):
    from loomsiril import draft as D
    from loomsiril.fly_progress import FlyProgress

    _fake_tkinter(monkeypatch)
    import tkinter as tk

    msg = ModuleType("tkinter.messagebox")
    msg.showinfo = msg.showerror = lambda *a, **k: None
    tk.messagebox = msg
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", msg)
    (tmp_path / "a.tif").write_text("x")
    gate, runs = threading.Event(), []

    def draft_cb(window, values):
        runs.append(D.show_draft(window, lambda p: gate.wait(5), {**values, "image_key": "k"}, FlyProgress(),
                                 cache_dir=None))
        return runs[-1]

    dlg = script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options(
        {"out_dir": str(tmp_path), "starless_path": str(tmp_path / "a.tif"), "stars_path": str(tmp_path / "a.tif")}),
        blocked_discover, None, draft_cb)
    _settle(dlg)
    dlg._draft()
    _settle(dlg)
    run = runs[0]
    assert dlg._draft_runs == [run] and any("poll" in repr(fn) for _ms, fn in dlg.top.afters.values())
    dlg._cancel()                                                            # the user closes the dialog
    gate.set()
    assert run.progress.cancel_event.is_set() and run.finished
    assert dlg.top.afters == {} and dlg._draft_runs == []


# --- the Stars-and-motion and HDR sections (rows generated from loomsiril.fly_options) --------------------------------------


def _open_dialog(script, blocked_discover, options=None, target=None):
    import tkinter as tk

    return script.LoomFlyDialog(tk.Tk(), script.DialogValues.from_options(options or {}), blocked_discover, target)


#: The two sections' rows, open: one per fly_options control, then Reset to defaults (the fake and the real tk agree).
FLY_SECTION_LAYOUT = {
    "motion": [
        (0, 0, "Label", "Travel (pc)"), (0, 1, "Entry", None), (0, 2, "Label", DYNAMIC),
        (1, 0, "Label", "Easing"), (1, 1, "Combobox", None),
        (2, 0, "Label", "Growth"), (2, 1, "Entry", None),
        (3, 0, "Label", "Nebula motion (%)"), (3, 1, "Spinbox", None),
        (4, 0, "Checkbutton", "Brighten approaching stars"),
        (5, 0, "Label", "Twinkle (%)"), (5, 1, "Spinbox", None),
        (6, 0, "Label", "Bloom (%)"), (6, 1, "Spinbox", None),
        (7, 0, "Checkbutton", "Motion blur"),
        (8, 0, "Label", "Star colour"), (8, 1, "Scale", None), (8, 2, "Label", DYNAMIC),
        (9, 1, "Button", "Reset to defaults")],
    "hdr": [                                           # SDR, so every row is greyed and says why in column 3
        (0, 0, "Label", "Peak (nits)"), (0, 1, "Spinbox", None), (0, 3, "Label", DYNAMIC),
        (1, 0, "Checkbutton", "Stars into HDR headroom"), (1, 3, "Label", DYNAMIC),
        (2, 0, "Label", "Transfer for Social 1080\u00d71920"), (2, 1, "Combobox", None), (2, 3, "Label", DYNAMIC),
        (3, 0, "Label", "Transfer for Social 1080\u00d71080"), (3, 1, "Combobox", None), (3, 3, "Label", DYNAMIC),
        (4, 0, "Label", "Transfer for YouTube 3840\u00d72160"), (4, 1, "Combobox", None), (4, 3, "Label", DYNAMIC),
        (5, 0, "Label", "Transfer for YouTube 1920\u00d71080"), (5, 1, "Combobox", None), (5, 3, "Label", DYNAMIC),
        (6, 0, "Label", "Transfer for Exhibition 3840\u00d72160 (loop)"), (6, 1, "Combobox", None), (6, 3, "Label", DYNAMIC),
        (7, 1, "Button", "Reset to defaults")],
}


def _state(dlg, key):
    """The state the widgets of control ``key`` were last given (None: never configured)."""
    return {w.kw.get("state") for w, _on in dlg._w["opt:" + key]}


def test_motion_and_hdr_rows_are_generated_from_the_table(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    for sid, layout in FLY_SECTION_LAYOUT.items():
        dlg._toggle_section(sid, True)
        assert _fake_grid_rows(dlg._sections[sid].frame) == layout
    tips = [w for sid in FLY_SECTION_LAYOUT for w in dlg._sections[sid].frame.children
            if "<Enter>" in getattr(w, "bindings", {})]
    assert len(tips) == 29                                # a tip on each label and widget: 16 in motion, 13 in HDR
    for w in tips:
        for handler in w.bindings["<Enter>"] + w.bindings["<Leave>"]:
            handler()                                       # no display: showing the tip is skipped, nothing raises


def test_motion_and_hdr_rows_under_a_real_display(stub, script, blocked_discover):
    tk = _tkinter()
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    try:
        dlg = script.LoomFlyDialog(root, script.DialogValues.from_options({}), blocked_discover, None)
        for sid, layout in FLY_SECTION_LAYOUT.items():
            dlg._toggle_section(sid, True)
            dlg.top.update_idletasks()
            assert _real_grid_rows(dlg._sections[sid].frame) == layout
    finally:
        root.destroy()


def test_hdr_controls_are_grey_until_dynamic_range_is_hdr_and_say_why(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    for key in ("hdr_peak", "star_hdr", "hdr_transfer_youtube_1080"):
        assert _state(dlg, key) == {"disabled"}
    assert dlg._sections["hdr"].reason.get() == "Dynamic range is SDR: choose HDR to use this"
    assert "disabled" not in {w.kw.get("state") for w, _ in dlg._w.get("opt:backdrop_motion", [])}
    dlg._vars["dynamic"].set("hdr")
    assert _state(dlg, "hdr_peak") == {"normal"} and _state(dlg, "star_hdr") == {"normal"}
    assert _state(dlg, "hdr_transfer_youtube_1080") == {"normal", "readonly"}   # the ticked preset: label and combo
    assert _state(dlg, "hdr_transfer_exhibition") == {"disabled"}             # not ticked
    assert dlg._sections["hdr"].reason.get() == ""
    dlg._vars["target_type"].set("galaxy")
    assert _state(dlg, "backdrop_motion") == {"disabled"}
    assert not dlg._model().disabled_reason("opt:hdr_peak") and "galaxy" in dlg._model().disabled_reason("opt:backdrop_motion")


def test_typed_values_reach_values_and_the_job_options(stub, script, monkeypatch, blocked_discover):
    from loomsiril import fly_options, presets

    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover, {"dynamic": "hdr", "presets": ["youtube_1080", "exhibition"]})
    for key, text in (("bloom", "50"), ("twinkle", "5"), ("growth", "0.3"), ("travel", "12.5"), ("hdr_peak", "2000"),
                      ("backdrop_motion", "80")):
        dlg._ctl[key][0].set(text)
    dlg._ctl["star_saturation"][0].set(150.0)
    dlg._ctl["easing"][0].set("Linear")
    dlg._ctl["brightening"][0].set(False)
    dlg._ctl["hdr_transfer_exhibition"][0].set("HLG")
    v = dlg.values(presets)
    assert (v["bloom"], v["twinkle"], v["growth"], v["travel"], v["hdr_peak"], v["backdrop_motion"]) == (0.5, 0.05, 0.3, 12.5, 2000, 0.8)
    assert (v["star_saturation"], v["easing"], v["brightening"], v["hdr_transfer_exhibition"]) == (1.5, "linear", False, "hlg")
    opts = fly_options.options_from_values(v)
    assert opts["bloom"] == 0.5 and opts["travel"] == 12.5 and opts["peak"] == 2000 and opts["easing"] == "linear"
    assert opts["hdrTransfer"] == {"youtube_1080": "hlg", "exhibition": "hlg"} and opts["starHdr"] is True


def test_a_bad_number_is_named_and_held_never_a_crash(stub, script, monkeypatch, blocked_discover):
    from loomsiril import presets

    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    dlg._ctl["bloom"][0].set("abc")
    dlg._ctl["hdr_peak"][0].set("99999")
    with pytest.raises(ValueError, match="Bloom"):
        dlg.values(presets)
    dlg._ctl["growth"][0].set("")
    got = dlg._control_values(lenient=True)                                    # the autosave: the rest is still kept
    assert "bloom" not in got and "hdr_peak" not in got and "growth" not in got and got["twinkle"] == 0.03
    dlg._ctl["bloom"][0].set("100")
    with pytest.raises(ValueError, match="Growth"):
        dlg.values(presets)
    dlg._ctl["growth"][0].set("0.15")
    with pytest.raises(ValueError, match=r"Peak \(nits\)"):
        dlg.values(presets)


def test_saved_values_come_back_and_reset_to_defaults_restores_them(stub, script, monkeypatch, blocked_discover):
    from loomsiril import presets

    _fake_tkinter(monkeypatch)
    saved = {"bloom": 0.5, "twinkle": 0.07, "easing": "linear", "motion_blur": False, "star_saturation": 1.2,
             "hdr_peak": 2500, "star_hdr": False, "hdr_transfer_exhibition": "hlg", "growth": "junk"}
    dlg = _open_dialog(script, blocked_discover, saved)
    v = dlg.values(presets)
    assert (v["bloom"], v["twinkle"], v["easing"], v["motion_blur"], v["hdr_peak"], v["star_hdr"]) == (0.5, 0.07, "linear", False, 2500, False)
    assert v["hdr_transfer_exhibition"] == "hlg" and v["growth"] == 0.15           # a bad saved value is the default
    dlg._ctl["travel"][0].set("30")
    next(w for w in dlg._sections["motion"].frame.children if w.kw.get("text") == "Reset to defaults").invoke()
    v = dlg.values(presets)
    assert {k: v[k] for k in ("bloom", "twinkle", "easing", "motion_blur", "star_saturation", "travel")} == {
        "bloom": 1.0, "twinkle": 0.03, "easing": "smoothstep", "motion_blur": True, "star_saturation": 1.0, "travel": None}
    assert v["hdr_peak"] == 2500                                                   # the other section is untouched
    next(w for w in dlg._sections["hdr"].frame.children if w.kw.get("text") == "Reset to defaults").invoke()
    v = dlg.values(presets)
    assert v["hdr_peak"] == 1000 and v["star_hdr"] is True and v["hdr_transfer_exhibition"] == "pq"


def test_travel_note_follows_the_distance_and_the_typed_travel(stub, script, monkeypatch, blocked_discover):
    from types import SimpleNamespace

    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    assert dlg._travel_note.get() == "automatic"
    dlg._dist_result = SimpleNamespace(distance=1000.0)
    dlg._ctl["travel"][0].set("950")
    assert dlg._travel_note.get() == "capped at 0.9 \u00d7 the distance"
    dlg._ctl["travel"][0].set("")
    assert dlg._travel_note.get() == "automatic: 200 pc"
    dlg._ctl["travel"][0].set("x")
    assert dlg._travel_note.get() == ""


# ---- the Logo section ----------------------------------------------------------------------------------------------------

#: The Logo section's rows, open: the file (with Browse... and its note), Placement, Opacity (with its percent), Fade in after.
LOGO_SECTION_LAYOUT = [
    (0, 0, "Label", "Logo file"), (0, 1, "Entry", None), (0, 2, "Button", "Browse..."),
    (1, 1, "Label", DYNAMIC),
    (2, 0, "Label", "Placement"), (2, 1, "Combobox", None), (2, 3, "Label", DYNAMIC),      # column 3: why the row is grey
    (3, 0, "Label", "Opacity"), (3, 1, "Scale", None), (3, 2, "Label", DYNAMIC), (3, 3, "Label", DYNAMIC),
    (4, 0, "Label", "Fade in after (s)"), (4, 1, "Spinbox", None), (4, 3, "Label", DYNAMIC),
]


def _logo_state(dlg):
    return {key: {w.kw.get("state") for w, _on in dlg._w[group]} for key, group in (("place", "logo"), ("look", "logo_look"))}


def test_logo_rows_are_laid_out(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    dlg._toggle_section("logo", True)
    assert _fake_grid_rows(dlg._sections["logo"].frame) == LOGO_SECTION_LAYOUT


def test_logo_rows_under_a_real_display(stub, script, blocked_discover):
    tk = _tkinter()
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display: tkinter.Tk() does not work here")
    try:
        dlg = script.LoomFlyDialog(root, script.DialogValues.from_options({}), blocked_discover, None)
        dlg._toggle_section("logo", True)
        dlg.top.update_idletasks()
        assert _real_grid_rows(dlg._sections["logo"].frame) == LOGO_SECTION_LAYOUT
    finally:
        root.destroy()


def test_logo_defaults_are_pixinsights_and_the_rows_are_grey_with_a_reason(stub, script, monkeypatch, blocked_discover):
    from loomsiril import presets

    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    v = dlg.values(presets)
    assert (v["logo_path"], v["logo_place"], v["logo_opacity"], v["logo_delay"]) == ("", "off", 100, 0)
    assert _logo_state(dlg) == {"place": {"disabled"}, "look": {"disabled"}}          # no file: all but the file's box are grey
    m = dlg._model()
    assert m.disabled_reason("logo") == m.disabled_reason("logo_look") == "needs a logo file"
    assert "Browse" in dlg._logo_note.get()                                       # the grey line says what to do


def test_logo_file_set_enables_placement_and_a_placed_logo_enables_opacity_and_fade(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    dlg._ctl["logo_path"][0].set("/l/logo.png")
    assert _logo_state(dlg) == {"place": {"normal", "readonly"}, "look": {"disabled"}}
    assert dlg._model().disabled_reason("logo_look") == "placement is Off"
    dlg._ctl["logo_place"][0].set("Top left")
    assert _logo_state(dlg) == {"place": {"normal", "readonly"}, "look": {"normal"}}
    assert dlg._model().disabled_reason("logo_look") == ""
    dlg._ctl["logo_path"][0].set("")
    assert _logo_state(dlg) == {"place": {"disabled"}, "look": {"disabled"}}


def test_logo_values_are_saved_keys_and_reach_the_job(stub, script, monkeypatch, blocked_discover, tmp_path):
    import numpy as np

    from loomsiril import presets, siril_bridge

    _fake_tkinter(monkeypatch)
    saved = {"logo_path": str(tmp_path / "l.png"), "logo_place": "bottom-mid", "logo_opacity": 40, "logo_delay": 7}
    dlg = _open_dialog(script, blocked_discover, saved)
    v = dlg.values(presets)
    assert (v["logo_path"], v["logo_place"], v["logo_opacity"], v["logo_delay"]) == (saved["logo_path"], "bottom-mid", 40, 7)
    assert dlg._ctl["logo_place"][0].get() == "Bottom middle"                            # the combo shows the label
    for key in ("logo_path", "logo_place", "logo_opacity", "logo_delay"):
        assert key in siril_bridge.OPTION_KEYS
    assert "logo_path" in siril_bridge.PATH_KEYS
    snap = dlg._snapshot()
    assert (snap["logo_path"], snap["logo_place"], snap["logo_opacity"], snap["logo_delay"]) == (saved["logo_path"], "bottom-mid", 40, 7)
    siril_bridge.save_options(snap, tmp_path)
    assert siril_bridge.load_options(tmp_path)["logo_place"] == "bottom-mid"
    planes = [np.zeros((6, 8), np.float32)]
    header = {"CRVAL1": 10.0, "CRVAL2": 20.0, "CRPIX1": 4.0, "CRPIX2": 3.0, "CD1_1": -1e-3, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 1e-3}
    job = siril_bridge.job_from_dialog(dict(v, starless=planes, stars=planes), planes, header, None)
    assert (job.logo_path, job.logo_place, job.logo_opacity, job.logo_delay) == (saved["logo_path"], "bottom-mid", 0.4, 7.0)


def test_a_bad_fade_is_named_and_held(stub, script, monkeypatch, blocked_discover):
    from loomsiril import presets

    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover, {"logo_delay": "junk"})
    for bad in ("abc", "-1", "601", "1.5", ""):
        dlg._ctl["logo_delay"][0].set(bad)
        with pytest.raises(ValueError, match="Fade in after"):
            dlg.values(presets)
    assert "logo_delay" not in dlg._control_values(lenient=True)
    dlg._ctl["logo_delay"][0].set("600")
    assert dlg.values(presets)["logo_delay"] == 600


def test_the_logo_file_is_checked_on_the_worker_and_a_missing_remembered_file_blocks_render(stub, script, monkeypatch, blocked_discover,
                                                                                         tmp_path):
    from loomsiril import fly_checks

    _fake_tkinter(monkeypatch)
    gone = str(tmp_path / "gone.png")
    dlg = _open_dialog(script, blocked_discover, {"logo_path": gone, "logo_place": "center", "starless_path": "/a.tif",
                                                  "stars_path": "/b.tif", "out_dir": str(tmp_path)})
    assert dlg._ctl["logo_path"][0].get() == gone                                        # the box keeps it
    assert ("logo", gone) in dlg._model().wanted_paths()
    assert ("logo", gone) not in dlg._checks                                             # nothing was read on the Tk thread
    result = fly_checks.validate([("logo", gone)])
    assert "not there" in result[("logo", gone)]
    dlg._checks.update(result)
    dlg._sync()
    assert dlg._logo_note.get() == result[("logo", gone)]
    assert result[("logo", gone)] in dlg._model().reasons() and dlg._model().can_render() is False
    # Off: the file is not used, so it does not block
    dlg._ctl["logo_place"][0].set("Off")
    assert ("logo", gone) not in dlg._model().wanted_paths()
    assert result[("logo", gone)] not in dlg._model().reasons()


def test_the_logo_check_reads_the_picture(tmp_path):
    import numpy as np
    import tifffile

    from loomsiril import fly_checks

    good = tmp_path / "ok.tif"
    tifffile.imwrite(good, np.zeros((4, 4, 3), np.uint8), photometric="rgb")
    jpg = tmp_path / "x.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 30)
    res = fly_checks.validate([("logo", str(good)), ("logo", str(jpg)), ("logo", str(tmp_path / "no.png"))])
    assert res[("logo", str(good))] == ""
    assert "PNG, TIFF or FITS" in res[("logo", str(jpg))] and "not there" in res[("logo", str(tmp_path / "no.png"))]


def test_browsing_a_logo_places_it_bottom_right_when_the_placement_was_off(stub, script, monkeypatch, blocked_discover, tmp_path):
    import tkinter as tk

    _fake_tkinter(monkeypatch)
    chosen = str(tmp_path / "pick.png")
    tk.filedialog = type("FD", (), {"askopenfilename": staticmethod(lambda **kw: chosen)})
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", tk.filedialog)
    dlg = _open_dialog(script, blocked_discover)
    dlg._browse_logo()
    assert dlg._ctl["logo_path"][0].get() == chosen and dlg._ctl["logo_place"][0].get() == "Bottom right"
    dlg._ctl["logo_place"][0].set("Top left")                                            # a placement chosen is kept
    dlg._ctl["logo_path"][0].set("")
    dlg._browse_logo()
    assert dlg._ctl["logo_place"][0].get() == "Top left"


def test_with_placement_off_no_logo_value_keys_the_draft_or_the_frames(stub, script, monkeypatch, blocked_discover, tmp_path):
    """Through the real dialog values(): an Off logo (whatever its file, opacity and fade) changes neither key nor options."""
    from loomsiril import draft as D
    from loomsiril import logo, presets
    from loomsiril import pipeline as P

    _fake_tkinter(monkeypatch)
    f = tmp_path / "l.png"
    f.write_bytes(b"x")
    dlg = _open_dialog(script, blocked_discover, {"logo_path": str(f), "logo_place": "off", "logo_opacity": 30, "logo_delay": 9})
    off = dlg.values(presets)
    bare = _open_dialog(script, blocked_discover).values(presets)
    assert (off["logo_path"], off["logo_place"]) == (str(f), "off")
    extra = {"image_key": "k", "starless_path": str(f), "stars_path": str(f)}
    assert D.draft_key({**off, **extra}) == D.draft_key({**bare, **extra})
    assert "logo" not in D.draft_signature({**off, **extra}).lower()
    opts = {**P._logo_opts(P.FlyJob(starless=[], stars=[], wcs={}, logo_path=str(f), logo_place="off", logo_opacity=0.3, logo_delay=9.0))}
    logo.attach(opts)
    assert not any(k.lower().startswith("logo") for k in opts)
    # placed: the same values do key the draft
    on = dict(off, logo_place="center")
    assert D.draft_key({**on, **extra}) != D.draft_key({**bare, **extra})


def test_logo_music_and_fade_rows_have_hover_tips(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    dlg._toggle_section("logo", True)

    def tipped(widget):
        return any(b for bs in getattr(widget, "bindings", {}).values() for b in bs)

    for w, _on in dlg._w["logo"] + dlg._w["logo_look"] + dlg._w["music"]:
        assert tipped(w), w.kind
    frame = dlg._sections["logo"].frame
    assert all(tipped(w) for w in frame.children if w.kind in ("Entry", "Button", "Scale", "Spinbox", "Combobox"))


def _hints(dlg, sid):
    """{row: text} of the grey reasons shown in column 3 of a section's rows (a removed one is not shown)."""
    out = {}
    for w in dlg._sections[sid].frame.children:
        if w.kind == "Label" and w.grid_kw and w.grid_kw.get("column") == 3:
            out[w.grid_kw["row"]] = w.kw["textvariable"].get()
    return out


def test_every_greyed_row_shows_its_reason_and_clears_it_when_enabled(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    for sid in ("motion", "hdr", "logo", "video"):
        dlg._toggle_section(sid, True)
    sdr = "Dynamic range is SDR: choose HDR to use this"
    assert _hints(dlg, "hdr") == {0: sdr, 1: sdr, 2: sdr, 3: sdr, 4: sdr, 5: sdr, 6: sdr}
    assert _hints(dlg, "motion") == {}                                      # nothing greyed
    dlg._vars["dynamic"].set("hdr")
    nt = "This output size is not ticked"
    assert _hints(dlg, "hdr") == {2: nt, 3: nt, 4: nt, 6: nt}
    dlg._vars["target_type"].set("galaxy")
    assert _hints(dlg, "motion") == {3: "A galaxy is a fixed backdrop: it does not grow"}
    dlg._vars["target_type"].set("nebula")
    assert _hints(dlg, "motion") == {}
    assert _hints(dlg, "logo") and all(_hints(dlg, "logo").values())        # Placement Off: its rows say why


def test_video_rows_say_why_when_encode_is_off(stub, script, monkeypatch, blocked_discover):
    _fake_tkinter(monkeypatch)
    dlg = _open_dialog(script, blocked_discover)
    dlg._toggle_section("video", True)
    assert not any(_hints(dlg, "video").values())
    dlg._video.set(False)
    got = _hints(dlg, "video")
    assert got and set(got.values()) == {"needs Encode video"}
    dlg._video.set(True)
    assert _hints(dlg, "video") == {}
