"""Loom for Siril: the script Siril runs. Thin: it builds the run's context, runs preflight and calls the orchestrator.

Two entry points in this one file:

* Dialog mode (Siril's GUI, ``is_cli()`` false): the options dialog (loomsiril.loom.dialog), then the run on a worker
  thread while a progress window shows it. Only the worker thread calls sirilpy during the run.
* Headless mode (``is_cli()`` true, siril-cli): the options come from a job file given as ``--job <job.json>``; no
  window is opened. The outcome is always written to a status file (docs/loom-job.md).

Importing this file does nothing: all work is in :func:`main`. Needs Siril 1.4 (sirilpy), numpy, scipy, tifffile and
the ``loomsiril`` package: run the script from a checkout of this repository, set ``LOOMSIRIL_SRC`` to a checkout,
or install the package.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

from sirilpy import LogColor, SirilInterface, ensure_installed

#: Optional: a directory that contains the ``loomsiril`` package (a checkout of this repository).
LOOMSIRIL_SRC_ENV = "LOOMSIRIL_SRC"
#: The Siril version the script needs (its first command).
REQUIRES_VERSION = "1.4.0"
#: Python packages the script needs in Siril's Python environment.
PACKAGES = ("numpy", "scipy", "tifffile")
#: Dialog mode also needs ttkthemes: Siril's theme applies only to a ThemedTk window (loomsiril.tk_theme).
DIALOG_PACKAGES = PACKAGES + ("ttkthemes",)
#: The status file is the job file's path plus this suffix, unless the job names ``statusFile``.
STATUS_SUFFIX = ".status.json"

#: Exit codes written to the status file (siril-cli's own exit status does not reflect Python errors).
EXIT_OK = 0
EXIT_RUN_ERROR = 1
EXIT_PREFLIGHT = 2
EXIT_CANCELLED = 130
CANCEL_TEXT = "Cancelled by user"

#: Longest text written to one log line; longer text is cut (siril-cli stalls on very long lines).
LINE_LIMIT = 300

#: Job keys the script reads itself; every other job key is an option (Loom's name, as in config.defaults()).
JOB_OUTPUT = "outputDir"
JOB_TOOLS = "tools"
JOB_STARNET = "starnetLicenceAccepted"
JOB_STATUS = "statusFile"
_JOB_RESERVED = (JOB_OUTPUT, JOB_TOOLS, JOB_STARNET, JOB_STATUS)

_JOB_FLAG = "--job"


def _checkout_root() -> Optional[str]:
    """The repository this script sits in (``<root>/Loom/loom_preprocess.py``), when that checkout holds the package."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return root if os.path.isfile(os.path.join(root, "loomsiril", "__init__.py")) else None


def _import_loomsiril() -> None:
    """Make ``loomsiril`` importable: $LOOMSIRIL_SRC first when it is set, then the checkout this script sits in."""
    for src in (_checkout_root(), os.environ.get(LOOMSIRIL_SRC_ENV)):
        if src and src not in sys.path:
            sys.path.insert(0, src)


def _load_modules() -> SimpleNamespace:
    """The loomsiril modules the script uses, imported here and not at module level (nothing happens on import)."""
    _import_loomsiril()
    from loomsiril import starnet_licence
    from loomsiril.loom import (chain, channels, config, dialog, icc, pipeline, preflight, residuals, siril_ops,
                                stages_correct)
    from loomsiril.loom.runctx import RunContext
    from loomsiril.loom.tools import graxpert_ai, syqon_studio
    from loomsiril.loom.tools.resolver import Resolver
    from loomsiril.loom.tools.store import ToolStore

    return SimpleNamespace(chain=chain, channels=channels, config=config, dialog=dialog, graxpert_ai=graxpert_ai,
                           icc=icc, pipeline=pipeline, preflight=preflight, residuals=residuals, siril_ops=siril_ops,
                           starnet_licence=starnet_licence, stages_correct=stages_correct,
                           syqon_studio=syqon_studio, RunContext=RunContext,
                           Resolver=Resolver, ToolStore=ToolStore)


class JobError(ValueError):
    """The job file cannot be used: unreadable, not JSON, or not a JSON object."""


def read_job(path: str) -> dict[str, Any]:
    """The job file as a dict. JobError when it cannot be read or does not hold a JSON object."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise JobError(f"cannot read the job file {path}: {exc}") from None
    if not isinstance(data, dict):
        raise JobError(f"the job file {path} must hold a JSON object")
    return data


def status_path_for(job: Optional[Mapping[str, Any]], job_path: str) -> str:
    """Where the status file goes: the job's ``statusFile`` when it names one, else ``<job path>.status.json``."""
    named = job.get(JOB_STATUS) if job else None
    return named if isinstance(named, str) and named else job_path + STATUS_SUFFIX


def make_status(exit_code: int, *, problems: Sequence[str] = (), produced: Sequence[str] = (),
                outputs: Optional[Mapping[str, str]] = None, log: str = "", error: Optional[str] = None) -> dict[str, Any]:
    """The status record: ``ok`` is true only for exit code 0."""
    return {
        "ok": exit_code == EXIT_OK,
        "exitCode": exit_code,
        "problems": list(problems),
        "produced": list(produced),
        "outputs": dict(outputs or {}),
        "log": log,
        "error": error,
    }


def write_status(path: str, status: Mapping[str, Any]) -> None:
    """Writes the status file atomically: a temporary file beside it, then a replace."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".loom-status-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(status), indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _short(text: Any) -> str:
    """One log line: no line breaks, at most LINE_LIMIT characters."""
    line = str(text).replace("\r", " ").replace("\n", " | ")
    return line if len(line) <= LINE_LIMIT else line[:LINE_LIMIT - 3] + "..."


class _Output:
    """Where the script's lines go: Siril's log, in both modes (siril-cli prints them to its output)."""

    def __init__(self, siril: Any) -> None:
        self._siril = siril

    def log(self, text: Any, color: LogColor = LogColor.DEFAULT) -> None:
        self._siril.log(_short(text), color)

    def warn(self, text: Any) -> None:
        self.log(text, LogColor.SALMON)


def _job_argument(args: Sequence[str]) -> Optional[str]:
    """The path after ``--job`` (or ``--job=<path>``), or None when there is none."""
    for index, arg in enumerate(args):
        if arg == _JOB_FLAG and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith(_JOB_FLAG + "="):
            return arg[len(_JOB_FLAG) + 1:]
    return None


def _can_write_dir(path: str) -> bool:
    """True when the folder can be created and a file written inside it (the probe file is removed)."""
    try:
        os.makedirs(path, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=path, prefix=".loom-write-")
        os.close(fd)
        os.remove(probe)
        return True
    except OSError:
        return False


def _listdir(folder: str) -> list[str]:
    try:
        return os.listdir(folder)
    except OSError:
        return []


def _read_head(path: str, limit: int) -> Optional[bytes]:
    """At most ``limit`` bytes of a file (icc.installed_icc_profiles reads one byte past its maximum)."""
    try:
        with open(path, "rb") as handle:
            return handle.read(limit)
    except OSError:
        return None


def _installed_icc(m: SimpleNamespace) -> list[dict[str, str]]:
    home = os.path.expanduser("~")
    dirs = m.icc.icc_profile_directories(sys.platform, os.environ, home)
    return m.icc.installed_icc_profiles(dirs, _listdir, lambda p: _read_head(p, m.icc.ICC_MAX_FILE_BYTES + 1))


def _stored_starnet_revision(m: SimpleNamespace, store: Any) -> Optional[str]:
    """The StarNet licence revision the ToolStore holds, when it is the current one (else None)."""
    stored = store.get_flag(m.dialog.STARNET_FLAG)
    return stored if stored == m.starnet_licence.STARNET_LICENCE_REV else None


def _availability(m: SimpleNamespace, found: Mapping[str, Any], store: Any) -> dict[str, bool]:
    """What SyQon Studio offers, for config.load and migrate_config."""
    return {
        "studio_found": found.get("syqon-cli") is not None,
        "studio_prism2_unavailable": store.get_flag(m.syqon_studio.PRISM2_FLAG) is True,
    }


class _Checks:
    """The preflight wiring for one run: the tools, the SPCC database, the Studio probe and the output folder checks."""

    def __init__(self, m: SimpleNamespace, out: _Output, resolver: Any, store: Any, found: Mapping[str, Any],
                 headless: bool, data_dir: Optional[str] = None) -> None:
        self.m = m
        self.out = out
        self.resolver = resolver
        self.store = store
        self.found = dict(found)
        self.headless = headless
        home = os.path.expanduser("~")
        candidates = m.siril_ops.spcc_database_candidates(sys.platform, os.environ, home, data_dir)
        self.spcc_folder: Optional[str] = next((c for c in candidates if os.path.isdir(c)), None)
        self._studio: Any = None
        self._work: Optional[str] = None

    def spcc_names(self, kind: str) -> list[str]:
        return list(self.m.siril_ops.spcc_names(kind, self.spcc_folder)) if self.spcc_folder else []

    def _studio_for_checks(self) -> Any:
        """The SyQon Studio adapter for the entitlement probe (kept for the run's checks, so each model is probed once)."""
        if self._studio is None and self.found.get("syqon-cli") is not None:
            if self._work is None:
                self._work = tempfile.mkdtemp(prefix="loom-probe-")
            context = self.m.syqon_studio.ToolContext(work_dir=self._work, log=self.out.log, platform=sys.platform)
            self._studio = self.m.syqon_studio.SyqonStudio(self.found["syqon-cli"].path, context, self.store)
        return self._studio

    def problems(self, config: Mapping[str, Any], output_dir: str, starnet_revision: Optional[str]) -> list[str]:
        """Every preflight problem for this run (empty when the run may start)."""
        m = self.m
        studio = self._studio_for_checks()
        checked = dict(config)
        checked["outputDir"] = output_dir
        inputs = m.preflight.Preflight(
            config=checked,
            read_header=m.channels.read_header,
            exists=os.path.exists,
            readable=lambda p: os.access(p, os.R_OK),
            found=self.found,
            rejection={tid: self.resolver.rejection(tid) or "" for tid in m.dialog.TOOL_LABELS},
            graxpert_readiness=lambda: m.graxpert_ai.readiness(self.resolver),
            studio_entitlement=(lambda models: (studio.check_entitlement(models), studio.problems)[1])
            if studio is not None else None,
            starnet_revision=starnet_revision,
            current_starnet_revision=m.starnet_licence.STARNET_LICENCE_REV,
            spcc_database=self.spcc_folder,
            spcc_names=self.spcc_names,
            can_write_dir=_can_write_dir,
            headless=self.headless,
        )
        return list(m.preflight.preflight(inputs))

    def close(self) -> None:
        """Removes the Studio probe's folder (the probe writes one small test image into it)."""
        if self._work is not None:
            try:
                for name in os.listdir(self._work):
                    os.remove(os.path.join(self._work, name))
                os.rmdir(self._work)
            except OSError:
                pass
            self._work = None


@dataclass
class _Session:
    """What one run needs from the surroundings: Siril, the log, the tools, the checks and the cancel flag."""

    siril: Any
    out: _Output
    m: SimpleNamespace
    config_dir: str
    store: Any
    found: Mapping[str, Any]
    checks: _Checks
    cancel: threading.Event
    progress: Optional[Callable[[float, str], None]]
    persist: bool  # True in dialog mode only: a headless job never writes the options file


def _context_factory(session: _Session, starnet_revision: Optional[str]) -> Callable[[str], Any]:
    """The pipeline's ctx_factory. It runs on the thread that calls pipeline.run, so SirilCommandOps binds there."""
    m = session.m
    out = session.out
    tools = {tid: found.path for tid, found in session.found.items() if found is not None}
    # The astrometric residual check is on (Gaia DR3, cache first, online when needed); without the catalogue it is
    # skipped for the run with one log line and never fails it. One checker per run.
    residuals = m.residuals.ResidualChecker(m.residuals.gaia_catalogue_source(), out.log)

    def factory(work_dir: str) -> Any:
        ops = m.siril_ops.SirilCommandOps(session.siril, log=out.log)
        return m.RunContext(
            ops=ops, work_dir=work_dir, log=out.log, warn=out.warn,
            error=lambda text: out.log(text, LogColor.RED), cancelled=session.cancel.is_set,
            on_progress=session.progress, platform=sys.platform, tools=tools, tool_store=session.store,
            starnet_revision=starnet_revision, residuals=residuals,
        )

    return factory


def _plan_and_run(session: _Session, config: dict[str, Any], output_dir: str, starnet_revision: Optional[str],
                  execute: Callable[[Callable[[], Any]], Any]) -> tuple[dict[str, Any], str]:
    """Save the options, run preflight, then validate-only or the run. Returns (status, text for a message box).

    ``execute(call)`` runs ``call()`` where the pipeline may run (this thread in headless mode, a worker thread in
    dialog mode) and returns its result or raises its error.
    """
    m = session.m
    out = session.out
    options = dict(config)
    options["outputDir"] = output_dir
    problems = session.checks.problems(options, output_dir, starnet_revision)
    if problems:
        out.log(f"Preflight found {len(problems)} problem(s). Nothing was run:")
        for problem in problems:
            out.log("  " + problem)
        return make_status(EXIT_PREFLIGHT, problems=problems), "\n".join(problems)

    if options.get("validateOnly"):
        lines = m.preflight.validate_only_report(options)
        for line in lines:
            out.log(line)
        return make_status(EXIT_OK), "\n".join(lines)

    factory = _context_factory(session, starnet_revision)
    installed = _installed_icc(m)
    try:
        report = execute(lambda: m.pipeline.run(options, factory, installed_icc=installed))
    except m.chain.RunCancelled:
        return make_status(EXIT_CANCELLED, error=m.chain.CANCEL_MESSAGE), m.chain.CANCEL_MESSAGE
    except Exception as exc:  # the pipeline logs the error; the status file and the box carry the text
        text = _short(f"{type(exc).__name__}: {exc}")
        out.log("Error: " + text, LogColor.RED)
        return make_status(EXIT_RUN_ERROR, error=text), text

    names = ", ".join(report.produced) or "nothing"
    out.log(f"Finished in {report.duration_s:.1f} s. Produced: {names}.", LogColor.GREEN)
    status = make_status(EXIT_OK, produced=report.produced, outputs=report.outputs, log=report.log_path)
    return status, f"Finished in {report.duration_s:.1f} s. Produced: {names}."


def _job_options(job: Mapping[str, Any]) -> dict[str, Any]:
    """The job's option keys (every key except the ones the script reads itself)."""
    return {key: value for key, value in job.items() if key not in _JOB_RESERVED}


#: Flags the saved options never hold (config.load skips them); a job file may set them.
_JOB_FLAG_KEYS = ("validateOnly", "ignoreCache", "keepWindowsOnError")


def _job_flags(job: Mapping[str, Any], options: dict[str, Any], out: _Output) -> None:
    """Copies the job's run flags into the options, when they are true or false."""
    for key in _JOB_FLAG_KEYS:
        if key not in job:
            continue
        if isinstance(job[key], bool):
            options[key] = job[key]
        else:
            out.warn(f"job option {key}: expected true or false; using the default")


def _job_tools(job: Mapping[str, Any], out: _Output, tool_ids: Sequence[str]) -> dict[str, str]:
    """The job's ``tools`` map: tool id -> configured path (a tool id the script does not know is ignored)."""
    raw = job.get(JOB_TOOLS)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        out.warn("job tools must be an object of tool id to path; ignored")
        return {}
    kept: dict[str, str] = {}
    for tid, path in raw.items():
        if tid not in tool_ids:
            out.warn(f"job tools: {tid!r} is not a Loom tool; ignored")
        elif isinstance(path, str) and path.strip():
            kept[tid] = path
    return kept


def _resolve(m: SimpleNamespace, store: Any, configured: Mapping[str, str],
             data_dir: Optional[str] = None, config_dir: Optional[str] = None) -> tuple[Any, dict[str, Any]]:
    """The resolver and every known tool's result (a Found, or None when not found or not passed)."""
    resolver = m.Resolver(sys.platform, os.path.expanduser("~"), dict(os.environ), store=store,
                          siril_data_dir=data_dir, siril_config_dir=config_dir)
    found = {tid: resolver.resolve(tid, configured=configured.get(tid)) for tid in m.dialog.TOOL_LABELS}
    return resolver, found


def _headless(siril: Any, out: _Output, job_path: str) -> int:
    """The job-file run: options from the defaults and the job, no window, the outcome in the status file.

    Once the job path is known the status file is always written: a failure before the run (the imports, the
    packages, the Siril command) gives exit code 1 with its text; an interrupt gives 130. An unreadable job file
    gets its status file beside it (``<job path>.status.json``), since the job cannot name another path.
    """
    try:
        job = read_job(job_path)
    except JobError as exc:
        return _finish_headless(job_path + STATUS_SUFFIX, make_status(EXIT_RUN_ERROR, error=_short(exc)), out)
    status_path = status_path_for(job, job_path)
    try:
        siril.cmd("requires", REQUIRES_VERSION)
        ensure_installed(*PACKAGES)
        m = _load_modules()
        status = _headless_run(siril, out, m, job)
    except KeyboardInterrupt:
        status = make_status(EXIT_CANCELLED, error=CANCEL_TEXT)
    except Exception as exc:  # every failure ends in the status file, with its text
        text = _short(f"{type(exc).__name__}: {exc}")
        out.log("Error: " + text, LogColor.RED)
        status = make_status(EXIT_RUN_ERROR, error=text)
    return _finish_headless(status_path, status, out)


def _finish_headless(status_path: str, status: Mapping[str, Any], out: _Output) -> int:
    try:
        write_status(status_path, status)
    except OSError as exc:
        out.log(f"Status file not written ({exc})", LogColor.RED)
    return int(status["exitCode"])


def _headless_run(siril: Any, out: _Output, m: SimpleNamespace, job: Mapping[str, Any]) -> dict[str, Any]:
    config_dir = siril.get_siril_configdir()   # the tools store only; the options file is not read or written
    store = m.ToolStore(config_dir)
    tool_ids = list(m.dialog.TOOL_LABELS)
    configured = _job_tools(job, out, tool_ids)
    data_dir = m.siril_ops.siril_data_dir(siril)
    resolver, found = _resolve(m, store, configured, data_dir, config_dir)
    # A job is self-contained: its options start from the defaults, not from the user's saved dialog selection.
    options = m.config.defaults()
    m.config.apply_options(options, _job_options(job), out.warn, "the job file")
    _job_flags(job, options, out)
    options = m.config.migrate_config(options, out.warn, **_availability(m, found, store))
    output_dir = job.get(JOB_OUTPUT, "")
    if not isinstance(output_dir, str):
        out.warn("outputDir must be text; the run has no output folder")
        output_dir = ""
    starnet = job.get(JOB_STARNET)
    starnet = starnet if isinstance(starnet, str) and starnet else None
    checks = _Checks(m, out, resolver, store, found, headless=True, data_dir=data_dir)
    session = _Session(siril=siril, out=out, m=m, config_dir=config_dir, store=store, found=found, checks=checks,
                       cancel=threading.Event(), progress=None, persist=False)
    try:
        status, _text = _plan_and_run(session, options, output_dir, starnet, lambda call: call())
    finally:
        checks.close()
    return status


def _progress_into(events: "queue.Queue[tuple[str, Any]]", kinds: Any) -> Callable[[float, str], None]:
    """A pipeline progress callback that only enqueues (the window reads the queue on its own thread)."""

    def report(percent: float, text: str) -> None:
        events.put((kinds.STAGE, text))
        events.put((kinds.PERCENT, percent))

    return report


def _in_worker(call: Callable[[], Any], events: "queue.Queue[tuple[str, Any]]", kinds: Any,
               show: Callable[[Any], None], model: Any) -> Any:
    """Runs ``call`` on a worker thread while ``show`` (the progress window, on this thread) waits for it.

    The worker always posts the done message, so the window's Close button appears. Its result is returned, or its
    error raised here. ``kinds`` is the dialog's ProgressModel (its message kinds).
    """
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["result"] = call()
        except BaseException as exc:  # handed back to the calling thread below
            box["error"] = exc
        finally:
            events.put((kinds.DONE, "finished"))

    thread = threading.Thread(target=work, name="loom-run", daemon=True)
    thread.start()
    try:
        show(model)
    finally:
        thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _show_progress(model: Any) -> None:
    """The progress window on the main thread; returns when the user closes it (after the run is done)."""
    from loomsiril.loom.dialog import ProgressWindow
    from loomsiril.tk_theme import close_root, themed_root

    root = themed_root()
    try:
        window = ProgressWindow(root, model)
        root.wait_window(window.top)
    finally:
        close_root(root)


def _save_dialog_options(m: SimpleNamespace, out: _Output, request: Any, model: Any, config_dir: str) -> None:
    """Run: the options the run uses, its output folder included. Cancel: what the dialog shows."""
    options = dict(request.config, outputDir=request.output_dir) if request is not None else model.to_config()
    try:
        # the last write: behind the autosave's lock, and any autosave still running is dropped after it
        model.save_final(options, lambda final: m.config.save(final, config_dir))
    except OSError as exc:  # a failed save never stops the run
        out.warn(f"Options not saved ({exc})")


def _save_options_quietly(m: SimpleNamespace, out: _Output, options: Mapping[str, Any], config_dir: str) -> None:
    """The dialog's autosave: the options as they are now; a failure is not worth a message while the window is open."""
    try:
        m.config.save(dict(options), config_dir)
    except OSError:
        pass


def _master_measure(m: SimpleNamespace, siril: Any, out: _Output) -> Callable[..., Any]:
    """The dialog's master measurement: Siril analyses each file; results are kept in the cache folder."""

    def measure(paths: Sequence[str], cache_dir: str) -> Any:
        # Built on the calling (worker) thread: SirilCommandOps checks that one thread uses it.
        ops = m.siril_ops.SirilCommandOps(siril, log=out.warn)
        return m.stages_correct.master_quality(ops, paths, cache_dir, warn=out.warn)

    return measure


def _ask_dialog(model: Any, siril: Any, image_loaded: bool) -> Any:
    from loomsiril.loom.dialog import show_dialog

    return show_dialog(model, siril, image_loaded=image_loaded)


def _dialog(siril: Any, out: _Output, m: SimpleNamespace, ask: Callable[..., Any],
            show: Callable[[Any], None]) -> int:
    """The options dialog, then the run with a progress window; the outcome in a message box."""
    config_dir = siril.get_siril_configdir()
    store = m.ToolStore(config_dir)
    data_dir = m.siril_ops.siril_data_dir(siril)
    resolver, found = _resolve(m, store, {}, data_dir, config_dir)
    options = m.config.load(config_dir, warn=out.warn, **_availability(m, found, store))
    checks = _Checks(m, out, resolver, store, found, headless=False, data_dir=data_dir)
    try:
        model = m.dialog.DialogModel(
            options, found, store=store, resolver=resolver,
            preflight=lambda request: checks.problems(request.config, request.output_dir, request.starnet_revision),
            spcc_names=checks.spcc_names,
            studio_prism2_unavailable=_availability(m, found, store)["studio_prism2_unavailable"],
            platform=sys.platform, env=dict(os.environ), home=os.path.expanduser("~"),
            measure=_master_measure(m, siril, out),
        )
        model.persist = lambda options: _save_options_quietly(m, out, options, config_dir)
        request = ask(model, siril, bool(siril.is_image_loaded()))
        # Saved however the dialog closes (Run or Cancel): it reopens as it was left.
        _save_dialog_options(m, out, request, model, config_dir)
        if request is None:
            return EXIT_OK
        events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        cancel = threading.Event()
        progress = m.dialog.ProgressModel(events, cancel)
        session = _Session(siril=siril, out=out, m=m, config_dir=config_dir, store=store, found=found,
                           checks=checks, cancel=cancel, progress=_progress_into(events, progress), persist=True)
        starnet = _stored_starnet_revision(m, store)
        status, text = _plan_and_run(session, request.config, request.output_dir, starnet,
                                     lambda call: _in_worker(call, events, progress, show, progress))
    finally:
        checks.close()
    _message(siril, status, text)
    return int(status["exitCode"])


def _message(siril: Any, status: Mapping[str, Any], text: str) -> None:
    """One message box with the outcome: the finished line, the cancel line, or every problem."""
    if status["exitCode"] == EXIT_OK:
        siril.info_messagebox(text)
    elif status["exitCode"] == EXIT_CANCELLED:
        siril.info_messagebox(text)
    else:
        siril.error_messagebox(text)


def _run(siril: Any, args: Sequence[str], ask: Callable[..., Any], show: Callable[[Any], None]) -> int:
    out = _Output(siril)
    headless = bool(siril.is_cli())
    job_path = _job_argument(args)
    if headless:
        if job_path is None:  # no job path, so no status file can be written: one log line
            out.log("Loom: headless mode needs a job file: pyscript loom_preprocess.py --job <job.json>", LogColor.RED)
            return EXIT_RUN_ERROR
        return _headless(siril, out, job_path)
    siril.cmd("requires", REQUIRES_VERSION)
    ensure_installed(*DIALOG_PACKAGES)
    m = _load_modules()
    return _dialog(siril, out, m, ask, show)


def main(argv: Optional[Sequence[str]] = None, *, ask: Optional[Callable[..., Any]] = None,
         show: Optional[Callable[[Any], None]] = None) -> Optional[int]:
    """Connect to Siril and run Loom in the mode Siril reports. Returns the exit code (None when not connected).

    ``argv`` defaults to the script's arguments (``--job <job.json>`` in headless mode). ``ask`` and ``show`` replace
    the dialog and the progress window (tests).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    siril = SirilInterface()
    try:
        connected = siril.connect()
    except Exception as exc:  # the real connect() raises when Siril is not there
        print(f"Loom: cannot connect to Siril ({exc})", file=sys.stderr)
        return None
    if not connected:
        print("Loom: cannot connect to Siril", file=sys.stderr)
        return None
    try:
        return _run(siril, args, ask if ask is not None else _ask_dialog,
                    show if show is not None else _show_progress)
    except Exception as exc:  # shown in a box (dialog) or in the log (headless); stderr when that fails too
        text = _short(f"Loom: {type(exc).__name__}: {exc}")
        try:
            if siril.is_cli():
                siril.log(text, LogColor.RED)
            else:
                siril.error_messagebox(text)
        except Exception:
            print(text, file=sys.stderr)
        return EXIT_RUN_ERROR
    finally:
        try:
            siril.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
