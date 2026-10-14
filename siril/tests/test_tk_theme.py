"""loomsiril.tk_theme: the scripts' Tk root, in Siril's theme when it can be applied.

tkinter and ttkthemes are replaced by fakes (no display needed); sirilpy is the stub, whose match_theme_to_siril
behaves as Siril 1.4's: set_theme on a ThemedTk, configure(theme=...) otherwise, failures raised as RuntimeError.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any, List

import pytest

REPO = Path(__file__).resolve().parents[1]
STUB = REPO / "tests" / "sirilpy_stub"


class FakeTclError(Exception):
    pass


class PlainTk:
    """Like tk.Tk: configure() refuses the -theme option, as the real Tk does."""

    withdrawn = False

    def withdraw(self) -> None:
        self.withdrawn = True

    def configure(self, **kw: Any) -> None:
        if "theme" in kw:
            raise ValueError('unknown option "-theme"')

    def winfo_class(self) -> str:
        return "Tk"


class FakeThemedTk(PlainTk):
    def __init__(self) -> None:
        self.themes: List[str] = []

    def set_theme(self, name: str) -> None:
        self.themes.append(name)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(STUB))
    for name in [m for m in sys.modules if m == "sirilpy" or m.startswith("sirilpy.")]:
        monkeypatch.delitem(sys.modules, name)
    tkinter = ModuleType("tkinter")
    tkinter.Tk = PlainTk
    tkinter.TclError = FakeTclError
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    ttkthemes = ModuleType("ttkthemes")
    ttkthemes.ThemedTk = FakeThemedTk
    monkeypatch.setitem(sys.modules, "ttkthemes", ttkthemes)
    from loomsiril import tk_theme

    return tk_theme


def test_the_stub_refuses_a_plain_tk_like_siril_does(env):
    from sirilpy import tksiril

    with pytest.raises(RuntimeError, match='Failed to set theme: unknown option "-theme"'):
        tksiril.match_theme_to_siril(PlainTk(), object())


def test_with_siril_the_root_is_a_themed_tk_in_siril_theme(env):
    logged: List[str] = []
    root = env.themed_root(object(), log=logged.append)
    assert isinstance(root, FakeThemedTk)
    assert root.themes == ["equilux"]
    assert logged == []


def test_without_siril_the_root_is_a_plain_tk(env):
    root = env.themed_root(None)
    assert type(root) is PlainTk


def test_without_ttkthemes_the_window_opens_unthemed_and_says_why(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "ttkthemes", None)  # import fails
    logged: List[str] = []
    root = env.themed_root(object(), log=logged.append)
    assert type(root) is PlainTk
    assert logged and "ttkthemes is not installed" in logged[0]


def test_a_theme_failure_never_stops_the_window(env, monkeypatch):
    def broken(self: Any, name: str) -> None:
        raise ValueError("no such theme")

    monkeypatch.setattr(FakeThemedTk, "set_theme", broken)
    logged: List[str] = []
    root = env.themed_root(object(), log=logged.append)
    assert isinstance(root, FakeThemedTk)
    assert logged and "Siril's theme was not applied" in logged[0]


@pytest.mark.parametrize("siril", [None, object()])
def test_the_root_is_hidden_so_no_empty_tk_window_shows(env, siril):
    """Every script window is a Toplevel on the root; a visible root is an extra empty window titled "tk"."""
    assert env.themed_root(siril).withdrawn is True


def test_closing_a_root_already_destroyed_is_not_an_error(env):
    """Closing the root window shuts Tk down; the cleanup must not raise 'application has been destroyed'."""

    class Gone(PlainTk):
        def destroy(self) -> None:
            raise FakeTclError('can\'t invoke "destroy" command: application has been destroyed')

    env.close_root(Gone())


def test_close_root_destroys_a_live_root(env):
    calls = []

    class Live(PlainTk):
        def destroy(self) -> None:
            calls.append("destroy")

    env.close_root(Live())
    assert calls == ["destroy"]
