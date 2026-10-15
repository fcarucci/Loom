"""Master flats on Siril: the engine of pixinsight/script/lib/MasterFlat.js, lines 1088-1398, ported.

PixInsight makes the masters with ImageCalibration and ImageIntegration in open windows. Siril has no windows: every
step is a command run in a work folder, and its result is a named file. The port keeps the JavaScript engine's
method names (in snake_case), its arguments and its result shapes (``{ok, reason}``, plus the extras of each method),
so the protocol the master-flat run drives is the same on both sides.

Ported, by JavaScript line range (pixinsight/script/lib/MasterFlat.js):

* ``MasterFlat.engine`` methods (1262-1397) -> :class:`SirilMasterFlatEngine`: ``exists`` (1263), ``remove`` (1264),
  ``removeDir`` (1265-1275) -> ``remove_dir``, ``tempDir`` (1276-1282) -> ``temp_dir``, ``calibrate`` (1283-1321),
  ``masterDark`` (1322-1351) -> ``master_dark``, ``integrate`` (1352-1367), ``removePartials`` (1368-1375) ->
  ``remove_partials``, ``resolve`` (1376-1385), ``publish`` (1386), ``verify`` (1387-1397).
* ``integrationApplyRejection`` (1110-1119) -> :func:`integration_apply_rejection`.
* ``integrationSetupParams`` (1121-1139) -> :func:`integration_setup_params`: the spec as the Siril ``stack`` command.
* ``integrationApplyHeader`` (1153-1163) -> :func:`integration_apply_header`.
* ``integration`` (1165-1188) -> ``SirilMasterFlatEngine._integration``.
* ``sizeOf`` (1190-1194) -> :func:`size_of`; ``meanOf`` (1196-1213) -> :func:`mean_of`; ``move`` (1215-1222) ->
  :func:`publish_exclusive`, which never replaces an existing file.
* ``masterDarkMissing`` (1224-1231) -> :func:`master_dark_missing`; ``masterDarkSources`` (1233-1238) ->
  :func:`master_dark_sources`; ``masterDarkProblem`` (1240-1248) -> :func:`master_dark_problem`;
  ``masterDarkStore`` (1250-1260) -> :func:`master_dark_store`.

The checks of a written master (MasterFlat.js 564-609) are ``master_flat_run.verify_master``; ``verify`` passes it
the master's geometry, keyword and mean.

Not ported: ``open``, ``ids``, ``closeNew``, ``integrationFindWindow`` (1090-1109, 1141-1151). They manage PixInsight
windows; Siril writes named files.

Differences from the JavaScript, each deliberate:

* Work folders. Every step runs in its own folder: a master dark in ``<cache>/master-flat-work/masterDark_<key>``, a
  job's calibration in the job folder (``temp_dir``), a job's integration in ``integrate`` below the job folder. The
  inputs are linked in as ``<prefix>00001.fit``, ``<prefix>00002.fit``... (symbolic links), the dark as ``dark.fit``.
  Commands use the relative names. The Siril working directory is restored in a ``finally``.
* FITS compression. When ``compression.enabled`` is true it is switched off for the step
  (``set compression.enabled=false``) and switched back on afterwards, in a ``finally``, with the same command. The
  method and quantization are never touched. When the state cannot be read, nothing is changed and a warning is
  logged; when it is off, nothing is sent.
* Stacking writes ``integrated.fit`` in the step folder. The header is then rewritten in Python (read with
  ``io_fits.read_fits``, written with ``write_fits`` at -32 bits) to the partial path. Nothing is loaded into the
  user's open Siril image, and the partial never survives a failure.
* A master is published only under a name nothing holds (a hard link, or an exclusive copy when links are not
  possible); an existing file is never replaced.
* Rejection normalisation (JS ``equalizeFluxes``) has no separate Siril switch: ``stack`` uses its own scaling.
* Names. Calibrated frames are ``pp_f_<n>.fit`` (Siril's output for the ``f_`` input prefix). A job folder drops a
  trailing ``.fit`` where the JavaScript drops ``.xisf``.
* Exceptions from the Siril command interface, and a command Siril reports as failed, are a failed step whose reason
  is the exception's text.
"""

from __future__ import annotations

import errno
import math
import os
import re
import shutil
import warnings
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace
from typing import Any, Optional, TypeVar

import numpy as np

from loomsiril import master_flat as mf
from loomsiril.io_fits import read_fits, write_fits
from loomsiril.jsmath import js_string
from loomsiril.loom.siril_ops import _text_set_compression, command_text, quote_path
from loomsiril.master_flat_run import verify_master

T = TypeVar("T")

#: The keywords the integration takes over: dropped from the stacked header, then the job's own are added
#: (MasterFlat.js integrationApplyHeader, 1153-1163).
RESERVED_KEYWORDS = ("FILTER", "IMAGETYP", "XBINNING", "YBINNING", "EXPTIME", "GAIN", "CCD-TEMP", "DATE-OBS")

#: The name Siril gives the stacked image, relative to the step folder.
STACK_OUT = "integrated"
#: The name a calibrated flat's link and the master dark's link take in the folder of their step.
DARK_LINK = "dark.fit"
#: The image extension a job name carries, dropped for its work folder (JS: ".xisf", MasterFlat.js tempDir, 1276-1282).
_IMAGE_EXT = re.compile(r"\.(xisf|fit)\Z")
_PARTIAL_FLAT = re.compile(r"\.partial_masterFlat_.*\.fit")

ReadHeader = Callable[[str], Any]
Log = Optional[Callable[[str], Any]]


class _StepError(Exception):
    """A step failed; the message is its reason."""


def _fail(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason}


def _header_value(value: Any) -> Any:
    """``String(value)`` as the header writes it: a whole float is written as an integer (JS String(1) is "1")."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# ---------------------------------------------------------------------------
# Pure helpers: the integration, the header and the files
# ---------------------------------------------------------------------------


def integration_apply_rejection(rejection: Mapping[str, Any]) -> list[Any]:
    """MasterFlat.js integrationApplyRejection (1110-1119): the Siril ``rej`` arguments of a rejection spec.

    Winsorized sigma clipping is ``rej w <sigmaLow> <sigmaHigh>``, percentile clipping ``rej p <pcLow> <pcHigh>``, and
    anything else (the JS NoRejection) ``rej n``. The numbers are written as JS writes them (4, not 4.0).
    """
    method = rejection.get("method")
    if method == "winsorized":
        return ["rej", "w", js_string(rejection["sigmaLow"]), js_string(rejection["sigmaHigh"])]
    if method == "percentile":
        return ["rej", "p", js_string(rejection["pcLow"]), js_string(rejection["pcHigh"])]
    return ["rej", "n"]


def integration_setup_params(prefix: str, spec: Mapping[str, Any], out_name: str) -> str:
    """MasterFlat.js integrationSetupParams (1121-1139) as the Siril command: ``stack <prefix> <rej...> <norm>
    -out=<name>``. Multiplicative normalisation is ``-norm=mul`` (JS Multiplicative); anything else ``-nonorm``
    (JS NoNormalization). The JS rejection normalisation has no Siril switch and is not sent."""
    norm = "-norm=mul" if spec.get("normalization") == "multiplicative" else "-nonorm"
    return command_text("stack", prefix, *integration_apply_rejection(spec["rejection"]), norm, "-out=" + out_name)


def integration_apply_header(header: Mapping[str, Any], add: Mapping[str, Any]) -> dict[str, Any]:
    """MasterFlat.js integrationApplyHeader (1153-1163): the stacked header without the reserved keywords, then each
    keyword of ``add`` that is not None. ``add`` values are the JS ones; a string is written as it is."""
    kept = {k: v for k, v in header.items() if k not in RESERVED_KEYWORDS}
    for name, value in add.items():
        if value is not None:
            kept[name] = _header_value(value)
    return kept


def size_of(read_header: ReadHeader, path: str) -> Any:
    """MasterFlat.js sizeOf (1190-1194): the header object of a readable file, or None."""
    h = read_header(path)
    return h if h is not None and h.info is not None else None


def mean_of(path: str) -> Optional[float]:
    """MasterFlat.js meanOf (1196-1213): the mean of every pixel of a FITS file, or None when it cannot be read or
    holds no pixels. Read with io_fits; no Siril image is touched."""
    try:
        planes, _header = read_fits(path)
        if not planes:
            return None
        values = np.concatenate([np.asarray(p, dtype=np.float64).ravel() for p in planes])
        if values.size == 0:
            return None
        return float(values.mean())
    except Exception:  # noqa: BLE001 -- the JS catches every error here and reports null
        return None


def publish_exclusive(partial: str, final: str) -> bool:
    """MasterFlat.js move (1215-1222), without the overwrite: the partial becomes ``final`` only when no file is at
    ``final``, and then the partial is gone. A hard link does it atomically; where links are not possible, an exclusive
    copy (``open(final, "xb")``) does. Returns False and leaves the partial when ``final`` exists or the move fails."""
    try:
        os.link(partial, final)
    except FileExistsError:
        return False
    except OSError:
        return _copy_exclusive(partial, final)
    try:
        os.remove(partial)
    except OSError:
        pass
    return True


def _copy_exclusive(partial: str, final: str) -> bool:
    created = False
    try:
        with open(partial, "rb") as src, open(final, "xb") as dst:
            created = True
            shutil.copyfileobj(src, dst)
    except OSError as exc:
        if created and exc.errno != errno.EEXIST:
            _discard(final)
        return False
    try:
        os.remove(partial)
    except OSError:
        pass
    return True


def master_dark_missing(group: Mapping[str, Any]) -> Optional[str]:
    """MasterFlat.js masterDarkMissing (1224-1231): the first dark of the group that is no longer there, as a reason."""
    for p in group["paths"]:
        if not os.path.exists(p):
            return "a dark is no longer there: " + js_string(p)
    return None


def master_dark_sources(group: Mapping[str, Any]) -> list[dict[str, Any]]:
    """MasterFlat.js masterDarkSources (1233-1238): each dark as ``{path, size, modified}``, the cache key's parts."""
    out: list[dict[str, Any]] = []
    for p in group["paths"]:
        size, modified = mf.file_info_of(p)
        out.append({"path": p, "size": size, "modified": modified})
    return out


def master_dark_problem(read_header: ReadHeader, group: Mapping[str, Any], partial: str) -> Optional[str]:
    """MasterFlat.js masterDarkProblem (1240-1248): what is wrong with a fresh master dark, or None. It must have the
    geometry of its darks and a finite mean."""
    first = size_of(read_header, group["paths"][0])
    got = size_of(read_header, partial)
    mean = mean_of(partial)
    if (got is None or first is None or got.info.width != first.info.width or got.info.height != first.info.height
            or mean is None or not math.isfinite(mean)):
        return "the master dark does not match its darks"
    return None


def master_dark_store(partial: str, path: str) -> dict[str, Any]:
    """MasterFlat.js masterDarkStore (1250-1260): move the sound partial into the cache under its final name. The
    partial does not survive a failed move."""
    if os.path.exists(path):
        try:
            os.remove(path)  # a cache file that failed its read above
        except OSError:
            pass
    if not publish_exclusive(partial, path):
        _discard(partial)
        return _fail("could not store the master dark in the cache")
    return {"ok": True, "path": path, "reason": ""}


# ---------------------------------------------------------------------------
# Siril command helpers
# ---------------------------------------------------------------------------


def _cd_text(path: str) -> str:
    try:
        return command_text("cd", quote_path(path))
    except ValueError as exc:
        raise _StepError(str(exc)) from exc


def _prepare_folder(folder: str) -> None:
    """A fresh, empty folder: an old one (with its links) is removed first."""
    if os.path.isdir(folder) and not os.path.islink(folder):
        shutil.rmtree(folder)
    elif os.path.lexists(folder):
        os.remove(folder)
    os.makedirs(folder)


def _replace_link(src: str, dst: str) -> None:
    if os.path.lexists(dst):
        os.remove(dst)
    os.symlink(src, dst)


def _link_inputs(paths: Sequence[str], prefix: str, folder: str) -> None:
    """Link each input into ``folder`` as ``<prefix>00001.fit``, ``<prefix>00002.fit``... (absolute symbolic links)."""
    for i, p in enumerate(paths, start=1):
        _replace_link(os.path.abspath(os.fspath(p)), os.path.join(folder, f"{prefix}{i:05d}.fit"))


def _discard(path: str) -> None:
    try:
        if os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class SirilMasterFlatEngine:
    """The engine of MasterFlat.engine (1088-1398) on siril-cli, with the JS method names in snake_case.

    ``siril`` is the connected Siril interface (``cmd``, ``get_siril_wd``, ``get_siril_config``). ``read_header(path)``
    returns an object with ``info`` (None when unreadable: ``width``, ``height``) and ``keyword(name)``; production
    passes ``master_flat_run.quiet_header_obj``. ``cache_dir`` holds the work folders and the master dark cache.
    """

    def __init__(self, siril: Any, read_header: ReadHeader, cache_dir: str, log: Log = None) -> None:
        self._siril = siril
        self._read_header = read_header
        self._cache_dir = os.fspath(cache_dir)
        self._log = log

    # -- folders -----------------------------------------------------------

    def _folder_for(self, name: str) -> str:
        return os.path.join(self._cache_dir, "master-flat-work", mf.safe_name(name))

    def _job_dir(self, job: Mapping[str, Any]) -> str:
        return self._folder_for(_IMAGE_EXT.sub("", js_string(job["name"])))

    def exists(self, p: str) -> bool:
        return os.path.exists(p)

    def remove(self, p: str) -> bool:
        try:
            if os.path.lexists(p):
                os.remove(p)
        except OSError:
            pass
        return not os.path.exists(p)

    def remove_dir(self, d: str) -> bool:
        """MasterFlat.js removeDir (1265-1275), recursive: a job folder holds its step folders."""
        try:
            if os.path.isdir(d) and not os.path.islink(d):
                shutil.rmtree(d)
            elif os.path.lexists(d):
                os.remove(d)
        except OSError:
            pass
        return not os.path.isdir(d)

    def temp_dir(self, job: Mapping[str, Any]) -> str:
        """MasterFlat.js tempDir (1276-1282): a fresh job folder under the work root."""
        d = self._job_dir(job)
        if os.path.isdir(d):
            self.remove_dir(d)
        os.makedirs(d, exist_ok=True)
        return d

    # -- Siril steps -------------------------------------------------------

    def _warn(self, text: str) -> None:
        if self._log is not None:
            self._log("master flat: warning: " + text)
        else:
            warnings.warn(text, stacklevel=2)

    def _send(self, text: str) -> None:
        if self._log is not None:
            self._log("master flat: siril: " + text)
        try:
            ok = self._siril.cmd(text)
        except Exception as exc:  # noqa: BLE001 -- any command failure is a failed step with its text
            raise _StepError(str(exc) or text) from exc
        if ok is False:
            raise _StepError("Siril reported that the command failed: " + text)

    def _compression_state(self) -> Optional[bool]:
        """``compression.enabled`` as Siril reports it (siril_ops fits_compression); None, with a warning, when it
        cannot be read or is not a bool."""
        try:
            value = self._siril.get_siril_config("compression", "enabled")
        except Exception as exc:  # noqa: BLE001 -- an older Siril without the config call
            self._warn("FITS compression state could not be read (" + str(exc) + "); left as it is")
            return None
        if isinstance(value, bool):
            return value
        self._warn("FITS compression state is not a bool (" + js_string(value) + "); left as it is")
        return None

    def _in_folder(self, folder: str, step: Callable[[], T]) -> T:
        """Run ``step`` in ``folder``. The Siril working directory is remembered and restored, and when FITS
        compression is on it is switched off for the step and switched back on, all in a ``finally``: the
        switch back happens also when the switch off or ``step`` raised."""
        wd0 = os.fspath(self._siril.get_siril_wd())
        self._send(_cd_text(folder))
        restore = False
        try:
            if self._compression_state() is True:
                restore = True
                self._send(_text_set_compression(False))
            return step()
        finally:
            try:
                if restore:
                    self._send(_text_set_compression(True))
            finally:
                self._send(_cd_text(wd0))

    def _integration(self, paths: Sequence[str], prefix: str, spec: Mapping[str, Any], folder: str,
                     out_path: str, header: Mapping[str, Any]) -> dict[str, Any]:
        """MasterFlat.js integration (1165-1188): stack the linked inputs in ``folder``, rewrite the header and write
        ``out_path``. On failure ``out_path`` is removed; a partial never survives."""
        text = integration_setup_params(prefix, spec, STACK_OUT)
        try:
            _prepare_folder(folder)
            _link_inputs(paths, prefix, folder)
            self._in_folder(folder, lambda: self._send(text))
            raw = os.path.join(folder, STACK_OUT + ".fit")
            if not os.path.isfile(raw):
                raise _StepError("no integrated image")
            planes, stacked = read_fits(raw)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            _discard(out_path)
            try:
                write_fits(out_path, planes, header=integration_apply_header(stacked, header), bitpix=-32)
            except (OSError, ValueError) as exc:
                raise _StepError("could not write " + out_path + ": " + str(exc)) from exc
            return {"ok": True, "reason": ""}
        except (_StepError, OSError) as exc:
            _discard(out_path)
            return _fail(str(exc))

    def calibrate(self, paths: Sequence[str], dark_path: str, out_dir: str) -> dict[str, Any]:
        """MasterFlat.js calibrate (1283-1321): ``calibrate f_ -dark=dark.fit`` in ``out_dir``, the folder the
        run made for the job. Returns the calibrated frames, ``pp_f_<n>.fit`` in input order."""
        work = os.fspath(out_dir)
        try:
            os.makedirs(work, exist_ok=True)
            _link_inputs(paths, "f_", work)
            _replace_link(os.path.abspath(os.fspath(dark_path)), os.path.join(work, DARK_LINK))
            self._in_folder(work, lambda: self._send(command_text("calibrate", "f_", "-dark=" + DARK_LINK)))
        except (_StepError, OSError) as exc:
            return _fail(str(exc))
        made = [os.path.join(work, f"pp_f_{i:05d}.fit") for i in range(1, len(paths) + 1)]
        made = [p for p in made if os.path.isfile(p)]
        if len(made) != len(paths):
            return _fail(f"{len(made)} of {len(paths)} flats calibrated")
        return {"ok": True, "paths": made, "reason": ""}

    def master_dark(self, group: Mapping[str, Any]) -> dict[str, Any]:
        """MasterFlat.js masterDark (1322-1351): the cached master dark of a raw-dark group, or a new one. The master is
        built under a ``.partial_`` name and moved into the cache only when sound."""
        missing = master_dark_missing(group)
        if missing is not None:
            return _fail(missing)
        path = mf.dark_cache_path(group, master_dark_sources(group), self._cache_dir)
        if os.path.exists(path) and size_of(self._read_header, path) is not None:
            return {"ok": True, "path": path, "reason": "cached"}
        partial = os.path.join(os.path.dirname(path), ".partial_" + os.path.basename(path))
        made = self._integration(group["paths"], "d_", mf.dark_integration_spec(len(group["paths"])),
                                 self._folder_for("masterDark_" + js_string(group["key"])), partial,
                                 {"IMAGETYP": "Master Dark"})
        if not made["ok"]:
            return made
        problem = master_dark_problem(self._read_header, group, partial)
        if problem is not None:
            _discard(partial)
            return _fail(problem)
        return master_dark_store(partial, path)

    def integrate(self, paths: Sequence[str], spec: Mapping[str, Any], out_path: str,
                  job: Mapping[str, Any]) -> dict[str, Any]:
        """MasterFlat.js integrate (1352-1367): stack the calibrated flats into ``out_path`` (a partial name) with the
        job's header. EXPTIME is kept only when every flat shared one exposure."""
        f0 = job["flats"][0]
        header = {
            "FILTER": job.get("filter"), "IMAGETYP": "Master Flat",
            "XBINNING": job.get("binning"), "YBINNING": job.get("binning"),
            "EXPTIME": f0.get("exposure") if len(job["parts"]) == 1 else None,
            "GAIN": f0.get("gain"), "CCD-TEMP": f0.get("temp"),
        }
        return self._integration(paths, "pp_f_", spec, os.path.join(self._job_dir(job), "integrate"), out_path,
                                 header)

    def remove_partials(self, folder: str) -> None:
        """MasterFlat.js removePartials (1368-1375): the flat partials of a folder, never a symbolic link."""
        if not os.path.isdir(folder):
            return
        with os.scandir(folder) as it:
            for e in it:
                if e.is_file(follow_symlinks=False) and _PARTIAL_FLAT.fullmatch(e.name):
                    try:
                        os.remove(e.path)
                    except OSError:
                        pass

    def resolve(self, p: str) -> str:
        """MasterFlat.js resolve (1376-1385): the real path of a folder or a file in one; a symbolic link is reported
        as itself with the ``symlink:`` marker, so it never equals the expected real path."""
        if os.path.isdir(p):
            return os.path.realpath(p)
        cut = p.rfind("/")
        dir_, name = (p[:cut], p[cut + 1:]) if cut >= 0 else ("", p)
        if os.path.islink(p):
            return "symlink:" + p
        return os.path.realpath(dir_ or ".") + "/" + name

    def publish(self, partial: str, final_path: str) -> bool:
        """MasterFlat.js publish (1386): move the partial to its final name, never over an existing file."""
        return publish_exclusive(partial, final_path)

    def verify(self, path: str, job: Mapping[str, Any]) -> Optional[str]:
        """MasterFlat.js verify (1387-1397): the reason the written master is wrong, or None, by
        ``master_flat_run.verify_master`` (MasterFlat.js 564-609)."""
        got = self._read_header(path)
        src = self._read_header(job["flats"][0]["path"])
        if got is None or got.info is None or src is None or src.info is None:
            return "the master could not be read"
        return verify_master(
            SimpleNamespace(width=got.info.width, height=got.info.height, keyword=got.keyword, mean=mean_of(path)),
            SimpleNamespace(width=src.info.width, height=src.info.height),
            job.get("filter"))
