"""Tests for loomsiril.frames_main and Loom/loom_frame_selector.py (the Frame Selector's start-up flow).

The Siril side is the stand-in sirilpy (tests/sirilpy_stub); Tk, the ASIAIR card, the scan and the review dialog are
replaced by fakes, so these run without a display. One test builds the real withdrawn root and skips when Tk is not
available. Passing these tests does NOT mean the script has run inside Siril.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from loomsiril import (asiair_card, asiair_import, frames_dialog, frames_main, frames_scan, frames_state,
                       frames_ui, night_dialog, night_dialog_tk, tk_theme)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "Loom" / "loom_frame_selector.py"
STUB = REPO / "tests" / "sirilpy_stub"


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
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
    spec = importlib.util.spec_from_file_location("loom_frame_selector_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeWindow:
    cancelled = False

    def __init__(self) -> None:
        self.reports: list[tuple] = []
        self.announced: list[str] = []

    def report(self, *args: Any) -> bool:
        self.reports.append(args)
        return True

    def announce(self, text: str) -> None:
        self.announced.append(text)

    def callbacks(self) -> SimpleNamespace:
        return SimpleNamespace(reading=lambda *a: True, measuring=lambda *a: True)


class Flow:
    """Everything main touches, replaced; ``calls`` is the order of events, ``told`` the boxes shown."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.told: list[tuple[str, str]] = []
        self.titles: list[Any] = []
        self.root = SimpleNamespace(name="root")
        self.cards: list[str] = []
        self.card_scan: dict[str, Any] = {}
        self.folder = ""
        self.night: Any = None
        self.order = ["L"]
        self.skipped: list[Any] = []
        self.cancel_state = False
        self.dialogs: list[Any] = []
        self.dialog_error: Any = None
        self.chooser_args: list[Any] = []
        self.scans: list[tuple] = []
        self.picker_args: list[Any] = []


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch, stub, tmp_path: Path):
    f = Flow()
    stub.STATE.configdir = str(tmp_path / "config")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    def themed_root(siril: Any = None, log: Any = None) -> Any:
        f.calls.append("root")
        return f.root

    monkeypatch.setattr(tk_theme, "themed_root", themed_root)
    monkeypatch.setattr(tk_theme, "close_root", lambda r: f.calls.append("close") if r is f.root else None)

    def with_progress(master: Any, title: Any, work: Any, *, cancellable: bool = False, before: Any = None) -> Any:
        assert master is f.root
        f.titles.append((title, cancellable))
        w = FakeWindow()
        if before:
            before(w)
        return work(w)

    monkeypatch.setattr(frames_ui, "with_progress", with_progress)
    monkeypatch.setattr(frames_ui, "tell", lambda text, icon="info", **kw: (f.told.append((icon, text)),
                                                                            f.calls.append("tell")))
    monkeypatch.setattr(asiair_card, "mount_roots", lambda: [("/media/x", False)])
    monkeypatch.setattr(asiair_card, "detect", lambda roots, stop, on_volume: list(f.cards))
    monkeypatch.setattr(asiair_card, "scan_card", lambda root, on_progress, stop: f.card_scan)

    def pick(survey: Any, root: str, siril: Any = None, log: Any = None) -> Any:
        f.picker_args.append((survey, root))
        return f.night

    monkeypatch.setattr(night_dialog_tk, "pick_night", pick)

    def chooser(root: Any, initial_dir: str) -> str:
        f.chooser_args.append((root, initial_dir))
        f.calls.append("chooser")
        return f.folder

    monkeypatch.setattr(frames_main, "ask_directory", chooser)

    def scan(folder: str, progress: Any, **kw: Any) -> dict:
        f.scans.append(("folder", folder, kw))
        return {"channels": {}, "unstable": [], "skipped": f.skipped, "cancelled": False}

    def scan_paths(paths: list[str], progress: Any, **kw: Any) -> dict:
        f.scans.append(("paths", list(paths), kw))
        return {"channels": {}, "unstable": [], "skipped": [], "cancelled": False}

    monkeypatch.setattr(frames_scan, "scan", scan)
    monkeypatch.setattr(frames_scan, "scan_paths", scan_paths)

    real_build, real_build_from = frames_state.build_state, frames_state.build_state_from

    def fill(state: dict) -> dict:
        state["order"] = list(f.order)
        state["cancelled"] = f.cancel_state
        return state

    monkeypatch.setattr(frames_state, "build_state", lambda *a: fill(real_build(*a)))
    monkeypatch.setattr(frames_state, "build_state_from", lambda *a: fill(real_build_from(*a)))
    monkeypatch.setattr(night_dialog, "survey_of", lambda scan, gap=None: {"survey": scan, "gap": gap})
    monkeypatch.setattr(night_dialog, "flats_for_night", lambda survey, night: ["flat-a", "flat-b"])
    monkeypatch.setattr(asiair_import, "read_card_darks",
                        lambda state, darks, flats: state.update(cardDarks=list(darks), cardDarkMatch={"found": True}))

    class FakeDialog:
        def __init__(self, master: Any, state: dict, **kw: Any) -> None:
            self.master, self.state, self.kw = master, state, kw
            self.released = 0
            f.dialogs.append(self)

        def run(self) -> None:
            f.calls.append("run")
            if f.dialog_error:
                raise f.dialog_error

        def release(self) -> None:
            self.released += 1
            f.calls.append("release")

    monkeypatch.setattr(frames_dialog, "Dialog", FakeDialog)
    return f


# ---- import and the script


def test_import_has_no_side_effects() -> None:
    code = ("import sys, loomsiril.frames_main as m\n"
            "assert 'tkinter' not in sys.modules\n"
            "assert 'loomsiril.frames_ui' not in sys.modules\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True,
                          env={"PYTHONPATH": str(REPO), "PATH": ""})
    assert done.returncode == 0, done.stderr


def test_script_import_connects_to_nothing(stub, script) -> None:
    assert stub.STATE.connects == 0
    assert callable(script.main)


def test_script_is_thin() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert len([ln for ln in text.splitlines() if ln.strip()]) < 60
    assert "import loomsiril" not in text.split("def _import_frames_main")[0]  # nothing from loomsiril at module level
    assert "tkinter" not in text


def test_script_runs_main_on_the_connection_and_disconnects(script, stub, monkeypatch) -> None:
    seen: dict[str, Any] = {}
    def imported() -> Any:
        seen["installed_first"] = list(stub.STATE.installs)
        return SimpleNamespace(main=lambda siril: seen.update(siril=siril))

    monkeypatch.setattr(script, "_import_frames_main", imported)
    script.main()
    assert stub.STATE.connects == 1 and isinstance(seen["siril"], stub.SirilInterface)
    assert seen["installed_first"] == [(script.PACKAGES, None, False)]   # installed before loomsiril is imported
    assert stub.STATE.messages == []


def test_script_shows_an_error_in_a_box(script, stub, monkeypatch) -> None:
    def boom(siril: Any) -> None:
        raise RuntimeError("it broke")

    monkeypatch.setattr(script, "_import_frames_main", lambda: SimpleNamespace(main=boom))
    script.main()
    assert stub.STATE.messages == [("error", "Loom Frame Selector: it broke")]


def test_script_without_a_connection_prints(script, stub, capsys) -> None:
    stub.STATE.connect_ok = False
    script.main()
    assert "cannot connect to Siril" in capsys.readouterr().err
    assert stub.STATE.messages == []


def test_script_finds_loomsiril_in_its_checkout(script) -> None:
    assert script._import_frames_main() is frames_main


# ---- version check


def test_too_old_siril_gives_one_message_and_nothing_else(flow, stub) -> None:
    stub.STATE.commands.clear()

    class Old(stub.SirilInterface):
        def cmd(self, *args: str) -> bool:
            raise stub.CommandError("requires failed")

    siril = Old()
    frames_main.main(siril)
    assert len(stub.STATE.messages) == 1 and stub.STATE.messages[0][0] == "error"
    assert "1.4" in stub.STATE.messages[0][1]
    assert flow.calls == [] and flow.dialogs == []


def test_a_requires_that_returns_false_is_too_old_too(flow, stub) -> None:
    class Old(stub.SirilInterface):
        def cmd(self, *args: str) -> bool:
            return False

    frames_main.main(Old())
    assert len(stub.STATE.messages) == 1 and flow.calls == []


def test_a_new_enough_siril_asks_for_the_minimum_version(flow, stub) -> None:
    flow.folder = ""
    frames_main.main(stub.SirilInterface())
    assert ("requires", frames_main.MIN_SIRIL) in stub.STATE.commands


# ---- no card: the folder chooser


def test_no_card_goes_to_the_folder_chooser_and_runs_the_dialog(flow, stub, tmp_path) -> None:
    folder = tmp_path / "subs"
    flow.folder = str(folder)
    frames_main.main(stub.SirilInterface())
    assert flow.calls == ["root", "chooser", "run", "release", "close"]
    assert flow.titles[0] == (frames_main.STARTUP_TITLE, True)          # the card search is cancellable
    assert flow.titles[1] == (None, True)                                # so is the scan
    kind, scanned, kw = flow.scans[0]
    assert (kind, scanned) == ("folder", str(folder))
    assert kw["siril"] is not None and kw["cache_dir"].endswith("frame-selector")
    dialog = flow.dialogs[0]
    assert dialog.master is flow.root and dialog.state["folder"] == str(folder)
    assert dialog.kw["siril"] is not None and dialog.kw["options_store"] is not None
    assert dialog.kw["cache"].cache_dir == kw["cache_dir"]
    assert dialog.released == 1


def test_the_chooser_starts_in_the_remembered_source_folder(flow, stub, tmp_path) -> None:
    from loomsiril import frames_options

    store = frames_options.OptionsStore(stub.STATE.configdir)
    frames_options.save(store, frames_options.FrameSelectorOptions(source_folder="/data/last"))
    frames_main.main(stub.SirilInterface())
    assert flow.chooser_args == [(flow.root, "/data/last")]


def test_cancel_at_the_chooser_does_nothing(flow, stub) -> None:
    flow.folder = ""
    frames_main.main(stub.SirilInterface())
    assert flow.calls == ["root", "chooser", "close"]
    assert flow.scans == [] and flow.dialogs == [] and flow.told == []


def test_empty_folder_shows_the_no_subframes_message(flow, stub) -> None:
    flow.folder = "/data/empty"
    flow.order = []
    frames_main.main(stub.SirilInterface())
    assert flow.told == [("info", "No readable frames in that folder.")]
    assert flow.dialogs == [] and flow.calls[-1] == "close"


def test_a_cancelled_scan_does_nothing(flow, stub) -> None:
    flow.folder = "/data/subs"
    flow.cancel_state = True
    frames_main.main(stub.SirilInterface())
    assert flow.dialogs == [] and flow.told == []


def test_the_dialog_is_released_when_it_raises(flow, stub) -> None:
    flow.folder = "/data/subs"
    flow.dialog_error = RuntimeError("dialog failed")
    with pytest.raises(RuntimeError, match="dialog failed"):
        frames_main.main(stub.SirilInterface())
    assert flow.dialogs[0].released == 1
    assert flow.calls[-2:] == ["release", "close"]


def test_the_root_is_closed_when_the_scan_raises(flow, stub, monkeypatch) -> None:
    flow.folder = "/data/subs"
    monkeypatch.setattr(frames_scan, "scan", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        frames_main.main(stub.SirilInterface())
    assert flow.calls[-1] == "close"


# ---- a card


NIGHT = {"target": "M31", "date": "2026-10-01", "frames": [{"path": "/card/a.fit"}, {"path": "/card/b.fit"}]}


def with_card(flow: Flow, **scan: Any) -> None:
    flow.cards = ["/media/x/ASIAIR", "/media/x/other"]
    flow.card_scan = {"lights": [{"path": "/card/a.fit"}], "flats": [], "darks": ["dark-1"], "unparseable": [],
                      "cancelled": False, "removed": False, **scan}


def test_card_found_offers_nights_and_reviews_the_chosen_one(flow, stub, tmp_path) -> None:
    with_card(flow)
    flow.night = NIGHT
    from loomsiril import frames_options, master_flat

    store = frames_options.OptionsStore(stub.STATE.configdir)
    master_flat.save_settings(store, "/darks", True)
    frames_main.main(stub.SirilInterface())
    assert "chooser" not in flow.calls and flow.calls[-3:] == ["run", "release", "close"]
    assert flow.picker_args[0][1] == "/media/x/ASIAIR"                       # the first card
    assert flow.picker_args[0][0]["gap"] is not None                         # GAP_HOURS
    kind, paths, kw = flow.scans[0]
    assert (kind, paths) == ("paths", ["/card/a.fit", "/card/b.fit"])
    assert kw["cache_dir"].endswith("frame-selector")
    state = flow.dialogs[0].state
    assert state["folder"] == "M31 2026-10-01"
    assert state["cardRoot"] == "/media/x/ASIAIR"
    assert state["candidateFlats"] == ["flat-a", "flat-b"]
    assert state["cardDarks"] == ["dark-1"]
    assert state["darksFolder"] == "/darks" and state["onlyMasterFlats"] is True
    assert flow.dialogs[0].released == 1


def test_the_card_search_is_cancellable_and_a_cancel_means_no_card(flow, stub, monkeypatch) -> None:
    with_card(flow)
    flow.folder = ""
    monkeypatch.setattr(asiair_card, "detect", lambda roots, stop, on_volume: [])   # a cancel returns what it has
    frames_main.main(stub.SirilInterface())
    assert flow.titles[0] == (frames_main.STARTUP_TITLE, True)
    assert flow.calls == ["root", "chooser", "close"]


def test_a_failing_card_search_means_no_card(flow, stub, monkeypatch) -> None:
    flow.folder = ""
    monkeypatch.setattr(asiair_card, "mount_roots", lambda: (_ for _ in ()).throw(OSError("no mounts")))
    frames_main.main(stub.SirilInterface())
    assert flow.calls == ["root", "chooser", "close"]


@pytest.mark.parametrize(("scan", "icon", "text"), [
    ({"removed": True}, "warning", "The card went away while it was being read."),
    ({"lights": []}, "info", "No readable light frames on that card."),
])
def test_a_card_with_nothing_to_review_says_why_then_the_chooser(flow, stub, scan, icon, text) -> None:
    with_card(flow, **scan)
    flow.folder = ""
    frames_main.main(stub.SirilInterface())
    assert flow.told == [(icon, text)]
    assert flow.calls == ["root", "tell", "chooser", "close"]
    assert flow.picker_args == []


def test_a_cancelled_card_read_falls_to_the_chooser_quietly(flow, stub) -> None:
    with_card(flow, cancelled=True)
    flow.folder = ""
    frames_main.main(stub.SirilInterface())
    assert flow.told == [] and "chooser" in flow.calls and flow.picker_args == []


def test_cancel_at_the_night_picker_falls_to_the_chooser(flow, stub) -> None:
    with_card(flow)
    flow.night = None
    flow.folder = ""
    frames_main.main(stub.SirilInterface())
    assert flow.calls == ["root", "chooser", "close"] and flow.scans == []


def test_a_cancelled_night_scan_falls_to_the_chooser(flow, stub) -> None:
    with_card(flow)
    flow.night = NIGHT
    flow.cancel_state = True
    flow.folder = ""
    frames_main.main(stub.SirilInterface())
    assert "chooser" in flow.calls and flow.dialogs == []


def test_a_night_with_no_measurable_frames_says_so(flow, stub) -> None:
    with_card(flow)
    flow.night = NIGHT
    flow.order = []
    frames_main.main(stub.SirilInterface())
    assert flow.told == [("info", "No readable light frames in that night.")]
    assert flow.dialogs == [] and flow.calls[-1] == "close"


# ---- the cache folder


def test_cache_folder_defaults_under_the_user_data_folder(flow, stub, tmp_path) -> None:
    got = frames_main.cache_folder(stub.SirilInterface())
    assert got.endswith("loomsiril/cache/frame-selector") or got.endswith("loomsiril\\cache\\frame-selector")


def test_cache_folder_follows_loom_cache_dir_setting(flow, stub, tmp_path) -> None:
    import json

    from loomsiril.loom import config

    chosen = tmp_path / "chosen"
    chosen.mkdir()
    Path(config.config_path(stub.STATE.configdir)).parent.mkdir(parents=True, exist_ok=True)
    config.save({**config.defaults(), "cacheDir": str(chosen)}, stub.STATE.configdir)
    assert frames_main.cache_folder(stub.SirilInterface()) == str(chosen / "frame-selector")
    config.save({**config.defaults(), "cacheDir": str(tmp_path / "missing")}, stub.STATE.configdir)
    assert "missing" not in frames_main.cache_folder(stub.SirilInterface())


# ---- the real withdrawn root


def test_the_real_root_is_withdrawn_and_destroyed(monkeypatch, stub, tmp_path) -> None:
    tk = pytest.importorskip("tkinter")
    try:
        probe = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    probe.destroy()
    seen: dict[str, Any] = {}
    real_root = tk_theme.themed_root

    def spy(siril: Any = None, log: Any = None) -> Any:
        r = real_root(siril, log)
        seen["root"], seen["state"] = r, r.state()
        return r

    monkeypatch.setattr(tk_theme, "themed_root", spy)
    monkeypatch.setattr(frames_main, "ask_directory", lambda root, initial: "")
    monkeypatch.setattr(asiair_card, "mount_roots", lambda: [])
    monkeypatch.setattr(frames_main, "cache_folder", lambda siril: str(tmp_path / "cache"))
    frames_main.main(stub.SirilInterface())
    assert seen["state"] == "withdrawn"
    with pytest.raises(tk.TclError):
        seen["root"].state()
