"""Master flats: checking a written master, deleting raw flats, and running a plan through an engine. Ported from
``pixinsight/script/lib/MasterFlat.js``, lines 556-976, plus the header adapter of this port.

Covered, by JS line range:

* ``masterFlatVerifyGeometry`` / ``Filter`` / ``Imagetyp`` / ``Mean`` (564-597) as ``_verify_geometry``,
  ``_verify_filter``, ``_verify_imagetyp``, ``_verify_mean``; ``MasterFlat.verifyMaster`` (599-609) as
  :func:`verify_master`;
* ``MasterFlat.cleanPath`` (612-616) as :func:`clean_path`; ``masterFlatPathInside`` (619-623) as ``_path_inside``;
  ``masterFlatValidatePathStructure`` (626-636) as ``_validate_path_structure``; ``masterFlatIsExcludedPath``
  (639-646) as ``_is_excluded_path``;
* ``masterFlatFindAvailableName`` (652-662) as ``_find_available_name``; ``masterFlatDeleteRawFlats`` (668-684) as
  ``_delete_raw_flats``; ``masterFlatCheckJobSkip`` (690-700) as ``_check_job_skip``;
* ``masterFlatProcessParts`` (706-726) as ``_process_parts``; ``masterFlatVerifyAndPublish`` (732-750) as
  ``_verify_and_publish``; ``masterFlatRecordJobResult`` (757-767) as ``_record_job_result``; ``masterFlatFailureOf``
  (773-781) as ``_failure_of``; ``masterFlatExecuteOneJob`` (787-817) as ``_execute_one_job``;
* ``masterFlatProcessAllJobs`` (823-840) as ``_process_all_jobs``; ``masterFlatProcessJobParts`` (847-859) as
  ``_process_job_parts``; ``masterFlatVerifyResolvedPaths`` (862-877) as ``_verify_resolved_paths``;
* ``MasterFlat.mayDelete`` (891-907) as :func:`may_delete`; ``MasterFlat.execute`` (937-957) as :func:`execute`;
  ``MasterFlat.report`` (960-976) as :func:`report`.

Names are the JS names in snake_case without the ``masterFlat`` prefix for the private helpers. Dict keys keep the
JS camelCase (``destFlatDir``, ``cardRoot``, ``shouldStop``, ``onProgress``, ``skip``) so a plan job, an option set
and a result read the same on both sides. JS ``undefined`` is ``None`` here.

The engine is an object passed in (see :class:`MasterFlatEngine`). Its results are dicts in the JS shapes:
``{ok, reason, paths}`` for calibrate, ``{ok, path, reason}`` for master_dark, ``{ok, reason}`` for integrate, a
problem text or None for verify, and a bool for publish, remove, remove_dir and exists. An engine error is a raised
exception whose ``message`` attribute (or text) is the message; a cancel is :class:`Cancelled` (the JS
``Util.CANCELLED`` text).

Siril differences: the masters are ``.fit``, so the extension is the module constant :data:`MASTER_EXT` (passed as
``ext`` to :func:`execute` where a name is built); the JS writes ``.xisf``, and the oracle passes ``ext=".xisf"``.
The partial name ``.partial_<name>`` carries the master's own extension in both.

Pure apart from the engine: no file, no display, no Siril call. The JS ``engine.resolve`` is optional: when the
engine has no callable ``resolve``, nothing is deleted (see :func:`_check_job_skip`).
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Optional, Protocol

from loomsiril.jsmath import is_falsy, js_string
from loomsiril.frames_headers import HeaderInfo, quiet_header
from loomsiril.master_flat import flat_integration_spec

#: The extension of the masters and of the names built from them (the JS writes ".xisf").
MASTER_EXT = ".fit"

#: ``Util.CANCELLED``: the text that marks a cancelled run in an exception's message.
CANCELLED = "Cancelled by user"


class Cancelled(Exception):
    """A run stopped by the user. ``message`` is :data:`CANCELLED`, so :func:`_is_cancel` recognises it."""

    def __init__(self, message: str = CANCELLED) -> None:
        super().__init__(message)
        self.message = message


class EngineError(Exception):
    """An engine failure, with its message (``new Error( message )`` on the JS side)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class HeaderView:
    """A frame's header for the readers: ``info`` (None when unreadable) and ``keyword(name)`` (None when absent).

    The production ``read_header`` of the master flat functions: it is :func:`quiet_header_obj`'s result.
    """

    def __init__(self, info: Optional[HeaderInfo], keyword: Callable[[str], Optional[str]]) -> None:
        self.info = info
        self._keyword = keyword

    def keyword(self, name: str) -> Optional[str]:
        return self._keyword(name)


def quiet_header_obj(path: str) -> HeaderView:
    """``read_header`` for the master flat readers: :func:`loomsiril.frames_headers.quiet_header` as a
    :class:`HeaderView`.
    Never raises; a missing or unreadable file gives ``info`` None and ``keyword`` None for every name."""
    info, keyword = quiet_header(path)
    return HeaderView(info, keyword)


class MasterFlatEngine(Protocol):
    """What :func:`execute` calls, with the JS shapes of the results (see the module docstring).

    Optional, as in the JS: ``remove_partials(dir)`` (called on the destination Flat folder when present) and
    ``resolve(path)`` (the real path with symlinks followed; without it nothing is deleted).
    """

    def exists(self, path: str) -> bool: ...

    def remove(self, path: str) -> bool: ...

    def remove_dir(self, path: str) -> bool: ...

    def temp_dir(self, job: dict[str, Any]) -> str: ...

    def master_dark(self, group: dict[str, Any]) -> dict[str, Any]: ...

    def calibrate(self, paths: list[str], dark_path: str, out_dir: str) -> dict[str, Any]: ...

    def integrate(self, paths: list[str], spec: dict[str, Any], out_path: str,
                  job: dict[str, Any]) -> dict[str, Any]: ...

    def verify(self, partial_path: str, job: dict[str, Any]) -> Optional[str]: ...

    def publish(self, partial_path: str, final_path: str) -> bool: ...


# ---------------------------------------------------------------------------
# Checking a written master (MasterFlat.js 556-609)
# ---------------------------------------------------------------------------


def _verify_geometry(got: Any, want: Any) -> Optional[str]:
    """``masterFlatVerifyGeometry``: the master could be read and has the flat's width and height."""
    if got is None or got.width is None:
        return "the master could not be read"
    if got.width != want.width or got.height != want.height:
        return "geometry changed"
    return None


def _verify_filter(got: Any, filter_name: Any) -> Optional[str]:
    """``masterFlatVerifyFilter``: the FILTER keyword is the job's, when the job names one."""
    f = got.keyword("FILTER")
    if filter_name is not None and (f is None or js_string(f) != js_string(filter_name)):
        return "FILTER is " + ("missing" if f is None else js_string(f)) + ", expected " + js_string(filter_name)
    return None


def _verify_imagetyp(got: Any) -> Optional[str]:
    """``masterFlatVerifyImagetyp``: IMAGETYP says flat (any case)."""
    t = got.keyword("IMAGETYP")
    if t is None or not re.search("flat", js_string(t), re.IGNORECASE):
        return "IMAGETYP is " + ("missing" if t is None else js_string(t)) + ", expected a master flat"
    return None


def _verify_mean(got: Any) -> Optional[str]:
    """``masterFlatVerifyMean``: a finite, positive mean (an empty or NaN integration is not a master)."""
    m = got.mean
    if isinstance(m, bool) or not isinstance(m, (int, float)) or not math.isfinite(m) or m <= 0:
        return "the master is empty or not finite"
    return None


def verify_master(got: Any, want: Any, filter_name: Any) -> Optional[str]:
    """``MasterFlat.verifyMaster``: what is wrong with a written master, or None. ``got`` has ``width``, ``height``,
    ``mean`` and ``keyword(name)``; ``want`` has ``width`` and ``height`` (one flat the master came from)."""
    reason = _verify_geometry(got, want)
    if reason:
        return reason
    reason = _verify_filter(got, filter_name)
    if reason:
        return reason
    reason = _verify_imagetyp(got)
    if reason:
        return reason
    return _verify_mean(got)


# ---------------------------------------------------------------------------
# Paths and what may be deleted (MasterFlat.js 612-646, 862-907)
# ---------------------------------------------------------------------------


def clean_path(path: Any) -> str:
    """``MasterFlat.cleanPath``: repeated and trailing slashes folded away (a lone "/" stays)."""
    p = re.sub(r"/{2,}", "/", js_string(path))
    return re.sub(r"/+\Z", "", p) if len(p) > 1 else p


def _path_inside(x: str, root: str) -> bool:
    """``masterFlatPathInside``: x is root or below it, case-insensitively; everything is inside "/"."""
    x = x.lower()
    root = root.lower()
    return x == root or root == "/" or x[: len(root) + 1] == root + "/"


def _validate_path_structure(p: str, d: str) -> Optional[str]:
    """``masterFlatValidatePathStructure``: the base name when p is a plain file directly in d and not a master
    (``masterFlat_*``), with no parent references; None otherwise."""
    if re.search(r"(^|/)\.\.(/|\Z)", p):
        return None
    if p[: len(d) + 1] != d + "/" or p.find("/", len(d) + 1) >= 0:
        return None
    base = p[len(d) + 1:]
    if base == "" or re.match(r"masterFlat_", base, re.IGNORECASE):
        return None
    return base


def _is_excluded_path(p: str, master_path: Any, card: Optional[str]) -> bool:
    """``masterFlatIsExcludedPath``: p is the job's master, or inside the card."""
    if master_path is not None and p.lower() == clean_path(master_path).lower():
        return True
    if card is not None and _path_inside(p, card):
        return True
    return False


def _verify_resolved_paths(p: str, d: str, base: str, card: Optional[str], resolve: Callable[[str], Any]) -> bool:
    """``masterFlatVerifyResolvedPaths``: with symlinks followed, the file is its own name inside the Flat folder, and
    neither the file nor the folder is inside the card. A resolver that throws refuses the file."""
    try:
        real = clean_path(resolve(p))
        real_dir = clean_path(resolve(d))
        real_card = clean_path(resolve(card)) if card is not None else None
    except Exception:  # noqa: BLE001 -- an unresolvable path is refused, never followed
        return False
    if real != real_dir + "/" + base:
        return False
    if real_card is not None and (_path_inside(real, real_card) or _path_inside(real_dir, real_card)):
        return False
    return True


def may_delete(path: Any, dest_flat_dir: Any, master_path: Any, card_root: Any,
               resolve: Optional[Callable[[str], Any]] = None) -> bool:
    """``MasterFlat.mayDelete``: may this path be deleted as a raw flat? Only a file directly inside the destination's
    Flat folder, never a master (the job's, or any ``masterFlat_*``), never anything inside the card. Refusals are
    case-insensitive. With ``resolve`` (real path, symlinks followed) the file must resolve to its own name inside the
    resolved Flat folder: a symlinked flat, or a Flat folder that is a link onto the card, is refused."""
    if path is None or dest_flat_dir is None:
        return False
    p = clean_path(path)
    d = clean_path(dest_flat_dir)
    card = clean_path(card_root) if (card_root is not None and len(js_string(card_root)) > 0) else None
    base = _validate_path_structure(p, d)
    if base is None:
        return False
    if _is_excluded_path(p, master_path, card):
        return False
    if resolve is not None and not _verify_resolved_paths(p, d, base, card, resolve):
        return False
    return True


# ---------------------------------------------------------------------------
# Running a job (MasterFlat.js 652-859)
# ---------------------------------------------------------------------------


def _ok(v: Any) -> bool:
    """JS truthiness of a result's ``ok`` field."""
    return not is_falsy(v)


def _dir_of(path: str) -> str:
    """``path.substring( 0, path.lastIndexOf( "/" ) )``: the folder, "" when there is no slash."""
    return path[: max(path.rfind("/"), 0)]


def _strip_ext(name: str, ext: str) -> str:
    """``name.replace( /\\.xisf$/, "" )``, with ``ext`` in place of ``.xisf``."""
    return name[: -len(ext)] if ext and name.endswith(ext) else name


def _find_available_name(job: dict[str, Any], engine: MasterFlatEngine, ext: str) -> dict[str, Any]:
    """``masterFlatFindAvailableName``: the first free ``<name>_<n><ext>`` beside an existing master. Returns
    ``{master, name, note}``; ``note`` names the rename, or is None."""
    master = job["master"]
    name = job["name"]
    d = _dir_of(master)
    n = 2
    while engine.exists(master):
        name = _strip_ext(job["name"], ext) + "_" + str(n) + ext
        master = d + "/" + name
        n += 1
    note = job["name"] + " already exists and is kept; the new master is " + name if master != job["master"] else None
    return {"master": master, "name": name, "note": note}


def _resolver(engine: Any) -> Optional[Callable[[str], Any]]:
    """The engine's ``resolve`` when it is callable, else None (the JS ``typeof engine.resolve == "function"``)."""
    r = getattr(engine, "resolve", None)
    return r if callable(r) else None


def _delete_raw_flats(job: dict[str, Any], opts: dict[str, Any], engine: MasterFlatEngine, master: str,
                      notes: list[str]) -> int:
    """``masterFlatDeleteRawFlats``: removes each flat that may be deleted; returns how many are gone.

    The others are named in ``notes``."""
    removed = 0
    for f in job["flats"]:
        if not may_delete(f["path"], opts.get("destFlatDir"), master, opts.get("cardRoot"), _resolver(engine)):
            notes.append("not deleted (not a plain file in the destination Flat folder): " + js_string(f["path"]))
            continue
        gone: Any = False
        try:
            gone = engine.remove(f["path"])
        except Exception:  # noqa: BLE001 -- a failed remove is decided by exists() below
            pass
        if _ok(gone) or not engine.exists(f["path"]):
            removed += 1
        else:
            notes.append("could not delete " + js_string(f["path"]))
    return removed


def _check_job_skip(job: dict[str, Any], stopped: Callable[[], bool], engine: Any) -> Optional[str]:
    """``masterFlatCheckJobSkip``: why this job is not run (cancelled, its own skip reason, or no resolver), or None."""
    if stopped():
        return "cancelled"
    if job.get("skip") is not None:
        return job["skip"]
    # Without a real-path resolver the symlink and card-through-link rules cannot be applied: delete nothing.
    if _resolver(engine) is None:
        return "the engine cannot resolve paths, so nothing is deleted"
    return None


def _process_parts(job: dict[str, Any], engine: MasterFlatEngine, tmp_dir: str,
                   stopped: Callable[[], bool]) -> dict[str, Any]:
    """``masterFlatProcessParts``: each part's dark (a master, or the engine's cached one) and its calibration.
    Returns ``{calibrated, failure}``; failure is None on success, else the reason."""
    calibrated: list[str] = []
    failure: Optional[str] = None
    for part in job["parts"]:
        if failure is not None:
            break
        if stopped():
            failure = "cancelled"
            break
        if part["dark"].get("kind") == "master":
            dark_path = part["dark"]["path"]
        else:
            md = engine.master_dark(part["dark"])
            if not _ok(md.get("ok")):
                failure = "master dark failed: " + js_string(md.get("reason"))
                break
            dark_path = md["path"]
        cal = engine.calibrate([f["path"] for f in part["flats"]], dark_path, tmp_dir)
        if not _ok(cal.get("ok")):
            failure = "calibration failed: " + js_string(cal.get("reason"))
            break
        calibrated = calibrated + list(cal["paths"])
    return {"calibrated": calibrated, "failure": failure}


def _verify_and_publish(job: dict[str, Any], engine: MasterFlatEngine, partial_path: str, master_path: str,
                        calibrated: list[str], stopped: Callable[[], bool]) -> Optional[str]:
    """``masterFlatVerifyAndPublish``: integrate the calibrated flats into the partial file, verify it, and move it
    into place. Returns the failure reason, or None when the master is in place."""
    res = engine.integrate(calibrated, flat_integration_spec(len(calibrated)), partial_path, job)
    if not _ok(res.get("ok")):
        return "integration failed: " + js_string(res.get("reason"))
    if stopped():
        return "cancelled"
    bad = engine.verify(partial_path, job)
    if bad is not None:
        return "master failed verification: " + js_string(bad)
    if stopped():
        return "cancelled"
    try:
        published = engine.publish(partial_path, master_path) is True
    except Exception:  # noqa: BLE001 -- a failed move is the same as a refused one
        published = False
    if not published:
        return "could not move the master into place"
    return None


def _process_job_parts(job: dict[str, Any], engine: MasterFlatEngine, partial: dict[str, Any],
                       stopped: Callable[[], bool]) -> dict[str, Any]:
    """``masterFlatProcessJobParts``: every part calibrated, the count checked against the flats (ALL of them: a master
    from some of the flats must not stand in for, and delete, all), then verified and published. ``partial`` is
    ``{path, master, tmp}``. Returns ``{failure, calibrated}``."""
    parts = _process_parts(job, engine, partial["tmp"], stopped)
    calibrated = parts["calibrated"]
    failure = parts["failure"]
    if failure is None and stopped():
        failure = "cancelled"
    if failure is None and len(calibrated) != len(job["flats"]):
        failure = ("calibration produced " + str(len(calibrated)) + " of " + str(len(job["flats"])) + " flat(s)")
    if failure is None:
        failure = _verify_and_publish(job, engine, partial["path"], partial["master"], calibrated, stopped)
    return {"failure": failure, "calibrated": calibrated}


def _record_job_result(job: dict[str, Any], out: dict[str, Any], opts: dict[str, Any], engine: MasterFlatEngine,
                       master: str, name: str, failure: Optional[str]) -> None:
    """``masterFlatRecordJobResult``: a failure keeps the raw flats and names the reason. Only a verified master in
    place lets the raw copies go."""
    if failure is not None:
        out["kept"].append({"filter": job["filter"], "binning": job.get("binning"), "reason": failure,
                            "flats": len(job["flats"])})
        return
    removed = _delete_raw_flats(job, opts, engine, master, out["notes"])
    out["made"].append({"name": name, "filter": job["filter"], "binning": job.get("binning"),
                        "flats": len(job["flats"]), "removed": removed})


def _failure_of(e: Any, out: dict[str, Any]) -> str:
    """``masterFlatFailureOf``: the kept-reason for an exception. A cancel marks the whole run cancelled."""
    if _is_cancel(e):
        out["cancelled"] = True
        return "cancelled"
    return "error: " + js_string(getattr(e, "message", e))


def _is_cancel(e: Any) -> bool:
    """``Util.isCancel``: the exception's message (or its text) contains the cancel text."""
    return e is not None and CANCELLED in js_string(getattr(e, "message", e))


def _execute_one_job(job: dict[str, Any], engine: MasterFlatEngine, opts: dict[str, Any], out: dict[str, Any],
                     stopped: Callable[[], bool], ext: str) -> None:
    """``masterFlatExecuteOneJob``: picks a free name, builds the master under a hidden partial name beside it,
    records the outcome and always removes the temporaries."""
    name_info = _find_available_name(job, engine, ext)
    master = name_info["master"]
    name = name_info["name"]
    if name_info["note"] is not None:
        out["notes"].append(name_info["note"])
    partial = _dir_of(master) + "/.partial_" + name
    tmp: Optional[str] = None
    try:
        tmp = engine.temp_dir(job)
        result = _process_job_parts(job, engine, {"path": partial, "master": master, "tmp": tmp}, stopped)
        _record_job_result(job, out, opts, engine, master, name, result["failure"])
    except Exception as e:  # noqa: BLE001 -- any failure keeps the raw flats; a cancel is recorded as one
        _record_job_result(job, out, opts, engine, master, name, _failure_of(e, out))
    finally:
        # The half-written temporary and the intermediate calibrated flats always go, success or not.
        try:
            if partial != master and engine.exists(partial):
                engine.remove(partial)
        except Exception:  # noqa: BLE001
            pass
        if tmp is not None:
            try:
                engine.remove_dir(tmp)
            except Exception:  # noqa: BLE001
                pass


def _process_all_jobs(jobs: list[dict[str, Any]], engine: MasterFlatEngine, opts: dict[str, Any], out: dict[str, Any],
                      stopped: Callable[[], bool], ext: str) -> None:
    """``masterFlatProcessAllJobs``: each job in order: progress, skip check, then the job."""
    for j, job in enumerate(jobs):
        on_progress = opts.get("onProgress")
        if on_progress is not None:
            on_progress(j, len(jobs), js_string(job["filter"])
                        + (" (bin " + js_string(job["binning"]) + ")" if job.get("binning") is not None else ""))
        skip_reason = _check_job_skip(job, stopped, engine)
        if skip_reason is not None:
            out["kept"].append({"filter": job["filter"], "binning": job.get("binning"), "reason": skip_reason,
                                "flats": len(job["flats"])})
            continue
        _execute_one_job(job, engine, opts, out, stopped, ext)


# ---------------------------------------------------------------------------
# Running a plan (MasterFlat.js 891-976)
# ---------------------------------------------------------------------------


def execute(jobs: list[dict[str, Any]], engine: MasterFlatEngine, opts: Optional[dict[str, Any]] = None,
            ext: str = MASTER_EXT) -> dict[str, Any]:
    """``MasterFlat.execute``: runs the plan's jobs through ``engine``, one filter at a time.

    ``opts`` has ``destFlatDir``, ``cardRoot``, ``shouldStop()`` and ``onProgress(done, total, text)``, each optional.
    Returns ``{made, kept, cancelled, notes}``: a job is in ``made`` only after its master verified (its raw flats then
    removed, any that could not be named in ``notes``); anything else is in ``kept`` with its raw flats untouched.
    ``ext`` is the extension of the masters (see the module docstring).
    """
    opts = opts if opts is not None else {}
    out: dict[str, Any] = {"made": [], "kept": [], "cancelled": False, "notes": []}

    def stopped() -> bool:
        should = opts.get("shouldStop")
        if should is not None and not is_falsy(should()):
            out["cancelled"] = True
        return out["cancelled"]

    # A temporary left by a run that died: ours only, directly in the Flat folder.
    remove_partials = getattr(engine, "remove_partials", None)
    if callable(remove_partials) and opts.get("destFlatDir") is not None:
        try:
            remove_partials(opts["destFlatDir"])
        except Exception:  # noqa: BLE001
            pass

    _process_all_jobs(jobs, engine, opts, out, stopped, ext)

    on_progress = opts.get("onProgress")
    if on_progress is not None:
        on_progress(len(jobs), len(jobs), "")
    return out


def report(result: dict[str, Any]) -> str:
    """``MasterFlat.report``: the closing lines of an import, one per master, kept filter, note, and the cancel."""
    lines: list[str] = []
    for m in result["made"]:
        lines.append("Master flat " + m["name"] + " from " + js_string(m["flats"]) + " flat(s); "
                     + js_string(m["removed"]) + " raw flat(s) deleted.")
    for k in result["kept"]:
        lines.append("Raw flats kept for " + js_string(k["filter"])
                     + (" (bin " + js_string(k["binning"]) + ")" if k.get("binning") is not None else "")
                     + " (" + js_string(k["flats"]) + "): " + js_string(k["reason"]) + ".")
    for n in result["notes"]:
        lines.append(n)
    if result["cancelled"]:
        lines.append("Cancelled; the remaining filters keep their raw flats.")
    return "\n".join(lines)
