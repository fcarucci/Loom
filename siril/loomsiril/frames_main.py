"""The Frame Selector's start-up flow for Siril: the ASIAIR offer, the folder chooser, the scan, the review dialog.

Ports of ``pixinsight/script/FrameSelector.js``:

* ``title`` (44-49): the window titles come from :func:`loomsiril.frames_ui.frame_selector_title`.
* ``detectCard`` (4942-4961) -> :func:`detect_card`.
* ``readCard`` (4967-4990) -> :func:`read_card`.
* ``pickNight`` (4993-5002) -> :func:`pick_night`.
* ``offerCard`` (5011-5055) -> :func:`offer_card`.
* ``main`` (5057-5097) -> :func:`main` (with :func:`choose_folder` for the folder branch and :func:`run_review` for
  the dialog, released on every way out).
* ``CORE_CHECK`` (5106-5120) -> :func:`check_siril`: Siril 1.4 or later instead of PixInsight 1.9.5.

Differences that come from the platform: one themed, withdrawn Tk root (:mod:`loomsiril.tk_theme`) is the parent of
every window and is destroyed on every way out; the folder chooser is ``tkinter.filedialog.askdirectory`` starting in
the remembered source folder; the cache folder is Loom's (its ``cacheDir`` setting, else the per-user default) with the
Frame Selector's files under ``frame-selector``. A review whose card night has no frames says why (the JS shows
nothing there).

Importing this module does nothing: every Loom module, and Tk, is imported when :func:`main` needs it.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

#: The oldest Siril the Frame Selector runs on (the Siril commands and Python module it relies on).
MIN_SIRIL = "1.4.0"
#: The Frame Selector's folder under Loom's cache folder.
CACHE_SUBFOLDER = "frame-selector"
STARTUP_TITLE = "Loom Frame Selector - starting up"
CARD_GONE = "The card went away while it was being read."
CARD_EMPTY = "No readable light frames on that card."


def too_old_message() -> str:
    """``CORE_CHECK.message`` (FrameSelector.js 5108-5119) for Siril."""
    return ("The Loom Frame Selector needs Siril " + MIN_SIRIL + " or later.\n\n"
            "Siril refused the version check. Please update Siril and run the script again.")


def check_siril(siril: Any) -> bool:
    """``CORE_CHECK`` (FrameSelector.js 5106-5120): True when Siril is 1.4 or later. When it is not, one message box
    says so (Siril's own, no window of ours) and False is returned."""
    try:
        ok = siril.cmd("requires", MIN_SIRIL) is not False
    except Exception:  # the command fails on an older Siril
        ok = False
    if not ok:
        siril.error_messagebox(too_old_message())
    return ok


def cache_folder(siril: Any) -> str:
    """Where the Frame Selector keeps its files: Loom's cache folder (its ``cacheDir``, else the per-user default,
    as ``loomsiril.loom.pipeline.run`` resolves it) and ``frame-selector`` under it."""
    import sys

    from loomsiril.loom import cache, config

    chosen = ""
    try:
        chosen = str(config.load(siril.get_siril_configdir()).get("cacheDir") or "").strip()
    except Exception:  # the cache is a convenience: a config that cannot be read means the default folder
        chosen = ""
    if not chosen or cache.folder_missing(chosen):
        chosen = cache.default_cache_dir(sys.platform, os.environ, os.path.expanduser("~"))
    return os.path.join(chosen, CACHE_SUBFOLDER)


def ask_directory(root: Any, initial_dir: str) -> str:
    """The folder chooser: the chosen folder, or "" when it was cancelled."""
    from tkinter import filedialog

    chosen = filedialog.askdirectory(parent=root, title="Select a folder of subframes",
                                     initialdir=initial_dir or None, mustexist=True)
    return chosen if isinstance(chosen, str) else ""


def detect_card(root: Any) -> Optional[str]:
    """``detectCard`` (FrameSelector.js 4942-4961): the first ASIAIR card found, or None. Cancellable; a cancel, or a
    search that fails, means no card."""
    from loomsiril import asiair_card, frames_ui

    def look(w: Any) -> list[str]:
        roots = asiair_card.mount_roots()
        return asiair_card.detect(roots, lambda: w.cancelled,
                                  lambda k, n, path: w.report("Looking for an ASIAIR", k, n, path))

    try:
        cards = frames_ui.with_progress(root, STARTUP_TITLE, look, cancellable=True,
                                        before=lambda w: w.announce("Starting up: looking for an ASIAIR..."))
    except Exception:
        cards = []
    return cards[0] if cards else None


def read_card(root: Any, card: str) -> Optional[dict[str, Any]]:
    """``readCard`` (FrameSelector.js 4967-4990): the card's scan, or None (after saying why, where there is
    something to say) when there is nothing to review."""
    from loomsiril import asiair_card, frames_ui

    def read(w: Any) -> dict[str, Any]:
        def found(n: int) -> None:
            try:
                w.report("Reading the ASIAIR card: files found", n, 0, card)
            except Exception:
                pass

        return asiair_card.scan_card(card, found, lambda: w.cancelled)

    scan = frames_ui.with_progress(root, None, read, cancellable=True)
    if scan is None or scan.get("removed"):
        frames_ui.tell(CARD_GONE, "warning", master=root)
        return None
    if scan.get("cancelled"):  # a read the user stopped is no card: the folder chooser follows
        return None
    if len(scan.get("lights") or []) == 0:
        frames_ui.tell(CARD_EMPTY, "info", master=root)
        return None
    return scan


def pick_night(siril: Any, survey: Any, card: str, log: Optional[Callable[[str], None]] = None) -> Any:
    """``pickNight`` (FrameSelector.js 4993-5002): the night chosen from the card's survey, or None."""
    from loomsiril import night_dialog_tk

    return night_dialog_tk.pick_night(survey, card, siril=siril, log=log)


def offer_card(siril: Any, root: Any, store: Any, cache_dir: str,
               log: Optional[Callable[[str], None]] = None) -> Optional[dict[str, Any]]:
    """``offerCard`` (FrameSelector.js 5011-5055): a state to review from the night chosen on an ASIAIR card, or None
    to fall through to the folder chooser (no card, a cancel anywhere, an unreadable or empty card)."""
    from loomsiril import asiair_import, frames_scan, frames_state, frames_ui, master_flat, night_dialog
    from loomsiril.asiair_names import GAP_HOURS

    card = detect_card(root)
    if card is None:
        return None
    scan = read_card(root, card)  # no question first: every target's nights are shown (Cancel opens the chooser)
    if scan is None:
        return None
    survey = night_dialog.survey_of(scan, GAP_HOURS)
    night = pick_night(siril, survey, card, log)
    if night is None:
        return None
    paths = [f["path"] for f in night["frames"]]

    def measure(paths_: list[str], progress: Any) -> Any:
        return frames_scan.scan_paths(paths_, progress, siril=siril, cache_dir=cache_dir)

    state = frames_ui.with_progress(
        root, None,
        lambda w: frames_state.build_state_from(paths, f"{night['target']} {night['date']}", w.callbacks(), measure),
        cancellable=True)
    if state is None or state.get("cancelled"):
        return None
    # import mode lives on the state, which the review dialog is handed
    state["cardRoot"] = card
    state["candidateFlats"] = night_dialog.flats_for_night(survey, night)

    def darks(w: Any) -> None:
        w.announce("Looking for darks on the card")
        asiair_import.read_card_darks(state, scan.get("darks") or [], state["candidateFlats"])

    frames_ui.with_progress(root, None, darks)
    remembered = master_flat.load_settings(store)
    state["darksFolder"] = remembered["darksFolder"]
    state["onlyMasterFlats"] = remembered["onlyMasterFlats"]
    return state


def choose_folder(siril: Any, root: Any, options: Any, cache_dir: str) -> Optional[dict[str, Any]]:
    """The folder branch of ``main`` (FrameSelector.js 5072-5093): the chooser, the scan under a cancellable progress
    window, and the "no subframes" message. Returns the state to review, or None."""
    from loomsiril import frames_groups, frames_scan, frames_state, frames_ui

    folder = ask_directory(root, options.source_folder)
    if not folder:
        return None

    def measure(folder_: str, progress: Any) -> Any:
        return frames_scan.scan(folder_, progress, siril=siril, cache_dir=cache_dir)

    state = frames_ui.with_progress(
        root, None, lambda w: frames_state.build_state(folder, w.callbacks(), measure), cancellable=True)
    if state is None or state.get("cancelled"):
        return None
    if len(state["order"]) == 0:
        frames_ui.tell(frames_groups.no_subframes_message(state.get("skipped")), "info", master=root)
        return None
    return state


def run_review(siril: Any, root: Any, state: dict[str, Any], store: Any, cache_dir: str) -> None:
    """The review dialog, released on every way out (FrameSelector.js 5094-5096): the loader's timer must never
    outlive the dialog."""
    from loomsiril import frames_dialog, frames_preview_cache

    dialog = frames_dialog.Dialog(root, state, cache=frames_preview_cache.PreviewCache(cache_dir), siril=siril,
                                  options_store=store)
    try:
        dialog.run()
    finally:
        dialog.release()


def main(siril: Any) -> None:
    """``main`` (FrameSelector.js 5057-5097) on a connected Siril: the version check, then the ASIAIR offer, else the
    folder chooser, then the review. The script has installed the Python packages before it imports this module."""
    if not check_siril(siril):
        return
    from loomsiril import frames_options, frames_ui, tk_theme

    def log(text: str) -> None:
        siril.log("Loom Frame Selector: " + text)

    store = frames_options.OptionsStore(siril.get_siril_configdir())
    options = frames_options.load(store)
    cache_dir = cache_folder(siril)
    root = tk_theme.themed_root(siril, log=log)  # withdrawn: no stray empty window
    try:
        state = offer_card(siril, root, store, cache_dir, log)
        if state is not None:
            if len(state["order"]) == 0:
                frames_ui.tell("No readable light frames in that night.", "info", master=root)
                return
        else:
            state = choose_folder(siril, root, options, cache_dir)
            if state is None:
                return
        run_review(siril, root, state, store, cache_dir)
    finally:
        tk_theme.close_root(root)
