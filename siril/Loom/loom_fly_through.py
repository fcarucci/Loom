"""Loom Fly-Through for Siril: a thin Siril 1.4 script over loomsiril (E7). Asks for the starless and stars layers (two
files, or a star-extraction tool found on this machine: StarNet2, SyQon Studio, StarXTerminator), a target and presets,
then renders with loomsiril.pipeline.run on a worker thread behind a progress window. Needs Siril
1.4 (sirilpy), numpy and loomsiril (Loom/README.md). THIS SCRIPT HAS NOT BEEN RUN INSIDE SIRIL: it is tested against a
stand-in sirilpy only. Importing it does nothing.
"""

import os
import sys
from typing import Any, Callable, Optional

from sirilpy import LogColor, SirilInterface, ensure_installed  # noqa: F401  (LogColor kept for the log lines)

#: Optional: a directory that contains the ``loomsiril`` package (a checkout of this repository).
LOOMSIRIL_SRC_ENV = "LOOMSIRIL_SRC"
def _import_loomsiril() -> Any:
    """Import loomsiril.siril_bridge: $LOOMSIRIL_SRC first on sys.path when it is set, then this script's checkout."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <root>/Loom/loom_fly_through.py
    checkout = root if os.path.isfile(os.path.join(root, "loomsiril", "__init__.py")) else None
    for src in (checkout, os.environ.get(LOOMSIRIL_SRC_ENV)):
        if src and src not in sys.path:
            sys.path.insert(0, src)
    from loomsiril import siril_bridge  # imported here, not at module level: nothing happens on import

    return siril_bridge


def __getattr__(name: str) -> Any:
    """The dialog classes live in loomsiril.fly_dialog (imported when first asked for, not on import of this script)."""
    if name in ("DialogValues", "LoomFlyDialog", "run_dialog", "_form_fields", "DEFAULT_PRESET", "_FROM_TARGET"):
        _import_loomsiril()
        from loomsiril import fly_dialog

        return getattr(fly_dialog, name)
    raise AttributeError(name)



def remember_speed(result: Any, bridge: Any, config_dir: Any) -> None:
    """Keep the render speed measured on this run (ms per megapixel) for the dialog's render estimate; never fails."""
    speed = getattr(result, "ms_per_mp", None)
    if isinstance(speed, (int, float)) and speed > 0:
        try:
            bridge.save_options({"ms_per_mp": float(speed)}, config_dir)
        except OSError:
            pass


def summary(result: Any, out_dir: Any = None) -> str:
    """The result text: what was written and where (frames, videos, or the ffmpeg command when no encoder was found)."""
    lines = []
    if getattr(result, "distance", None) and getattr(result, "distance_label", ""):
        from loomsiril.pipeline import distance_text

        lines.append(distance_text(result.distance, result.distance_label))
    lines += [f"Frames written: {getattr(result, 'written', 0)}, reused: {getattr(result, 'reused', 0)}."]
    if out_dir:
        lines.append(f"Output folder: {out_dir} (frames in one sub-folder per preset)")
    if getattr(result, "cancelled", False):
        lines.insert(0, "Cancelled: the frames so far are kept.")
    videos = list(getattr(result, "videos", []) or [])
    if videos:
        lines.append("Videos:")
        lines.extend(videos)
    commands = list(getattr(result, "commands", []) or [])
    if commands:
        lines.append("ffmpeg was not found. Run this command to encode the video:")
        lines.extend(commands)
    failed = list(getattr(result, "failed", []) or [])
    if failed:
        lines.append(f"{len(failed)} frame(s) failed; see the log.")
    return "\n".join(lines)


def _report(siril: SirilInterface, text: str) -> None:
    """Show an error box on a connected Siril; with no connection, print it to stderr instead."""
    try:
        siril.error_messagebox(text)
    except Exception:  # the box needs a live connection; the message must still get out
        print(text, file=sys.stderr)


def main(dialog: Optional[Callable[..., Optional[dict]]] = None,
         progress_ui: Optional[Callable[..., bool]] = None) -> None:
    """Connect, check the image, ask, then render behind a progress window. ``dialog`` and ``progress_ui`` are
    replaceable (tests: a fake dialog gets the headless loop through Siril's own progress bar, no window)."""
    ask = dialog if dialog is not None else __getattr__("run_dialog")
    siril = SirilInterface()
    try:
        connected = siril.connect()   # the real connect() raises SirilConnectionError; the stub may return False
    except Exception as exc:
        print(f"Loom Fly-Through: cannot connect to Siril ({exc})", file=sys.stderr)
        return
    if not connected:
        print("Loom Fly-Through: cannot connect to Siril", file=sys.stderr)
        return
    try:
        if not siril.is_image_loaded():
            siril.error_messagebox("Load a plate-solved image first.")
            return
        ensure_installed("numpy", "tifffile", "ttkthemes")
        bridge = _import_loomsiril()
        from loomsiril import encode, fly_progress, fly_tools, fly_ui, pipeline

        header = siril.get_image_fits_header(return_as="dict") or {}
        b = bridge.bridge_image(siril.get_image_pixeldata(), header, siril.get_image_stars(),
                                siril.get_image_iccprofile())
        if b.wcs is None:
            siril.error_messagebox("The image has no plate solution: run plate solving first.")
            return
        config_dir = siril.get_siril_configdir()
        options = bridge.load_options(config_dir)
        data_dir = _siril_data_dir(siril)

        def draft_log(text: str) -> None:                    # called on the Tk thread (the one that talks to Siril)
            siril.log(f"Loom Fly-Through: {text}", LogColor.SALMON if ": failed" in text else LogColor.DEFAULT)

        def draft(window: Any, chosen: dict) -> Any:
            from loomsiril import draft as draft_mod

            def make_inputs(p: Any) -> Any:                  # on the draft's worker thread
                from loomsiril import fly_distance

                v = dict(chosen)
                fly_progress.ensure_distance(v, b, p, fly_distance.resolve, "Draft")   # before any slow work
                v.update(fly_progress.layers(v, b, bridge, p))
                v["detections"] = b.detections
                return bridge.job_from_dialog(v, b.planes, b.header, b.icc)

            # the image file's identity (its stamp) was read by the dialog's worker, not here on the Tk thread
            image_key = f"{b.width}x{b.height} {b.wcs} {chosen.get('image_path') or ''} {chosen.get('image_stamp')}"
            return draft_mod.show_draft(window, make_inputs, {**chosen, "image_key": image_key},
                                 cache_dir=draft_mod.cache_dir(config_dir), log=draft_log)

        def discover() -> Any:                               # on the dialog's worker thread
            return fly_tools.discover(fly_tools.make_resolver(config_dir, data_dir), encode.find_ffmpeg)

        names = _name_context(siril, bridge, b, header)
        extra = {}
        if dialog is None:                                   # the real dialog remembers its options as they change
            extra["persist"] = lambda opts: bridge.save_options(opts, config_dir)
        values = ask(options, discover, b.target, siril, draft=draft, names=names, **extra)
        if values is None:
            return                                           # Cancel: the dialog saved what was typed when it closed
        try:
            # the dialog already saved the file boxes as typed (an Image file kept while in "files" mode); values
            # blank that one, so the paths are not written again from here
            bridge.save_options({k: v for k, v in values.items() if k not in bridge.PATH_KEYS}, config_dir)
        except OSError as exc:  # the options are a convenience: a render must not fail on them
            siril.log(f"Loom Fly-Through: options not saved ({exc})", LogColor.SALMON)
        siril.log("Loom Fly-Through: starting", LogColor.DEFAULT)
        progress = fly_progress.FlyProgress()
        progress.image_title = os.path.basename(names.open_path) if names.open_path else ""
        out_dir = values["out_dir"]
        from loomsiril import fly_distance

        thread = fly_progress.start(lambda: fly_progress.work(
            values, b, bridge, pipeline, progress, lambda result: (remember_speed(result, bridge, config_dir),
                                                                   summary(result, out_dir))[1],
            resolve=fly_distance.resolve))
        show = progress_ui or (fly_ui.show_progress if dialog is None else fly_ui.show_headless)
        shown = show(progress, thread, siril)
        progress.pump()
        if progress.error:
            if not shown:
                raise RuntimeError(progress.error)
            siril.log(f"Loom Fly-Through: {progress.error}", LogColor.SALMON)
            return
        siril.log(progress.done_text, LogColor.GREEN)
        if not shown:
            siril.info_messagebox(progress.done_text)
    except Exception as exc:  # every failure is shown in a box, never a raw traceback in Siril
        _report(siril, f"Loom Fly-Through: {exc}")
    finally:
        try:
            siril.disconnect()
        except Exception:
            pass


def _name_context(siril: SirilInterface, bridge: Any, b: Any, header: dict) -> Any:
    """What the dialog's online name lookup needs: the solved field, the OBJECT keyword and the open image's file."""
    from loomsiril import skyname

    try:
        path = siril.get_image_filename() or ""
    except Exception:  # an unsaved image has no file: the lookup then has the header only
        path = ""
    return skyname.NameContext(field=skyname.sky_field(b.wcs, b.width, b.height) if b.wcs else None,
                               header_object=bridge.header_object(header), open_path=str(path),
                               open_grid=(b.width, b.height, len(b.planes)))


def _siril_data_dir(siril: SirilInterface) -> Optional[str]:
    """Siril's data folder, for the tool resolver (None when Siril cannot say)."""
    try:
        from loomsiril.loom import siril_ops

        return siril_ops.siril_data_dir(siril)
    except Exception:
        return None


if __name__ == "__main__":
    main()
