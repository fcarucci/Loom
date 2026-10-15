"""Frame Selector: reading a folder of frames into a cohort, measuring it channel by channel, and cancelling.

Ported from ``pixinsight/script/FrameSelector.js``. The line numbers are that file's, and each JS function has a
Python twin with the same meaning, named in snake_case:

    cohortFrom          957-995   -> cohort_from
    readHeaders        1002-1030  -> read_headers
    MEASURE_BATCH      1043       -> MEASURE_BATCH
    measureBatched     1050-1057  -> measure_batched
    cachedSplit        1060-1063  -> loomsiril.frames_cache.cached_split (the store is passed in)
    measureGroup       1094-1124  -> measure_group
    settler            1132-1145  -> settler
    READING_EVERY_MS   1166       -> READING_EVERY_MS
    scanPaths          1168-1216  -> scan_paths
    measuringReporter  1223-1234  -> measuring_reporter
    scan               1236-1239  -> scan

The JS helpers this module calls are ported elsewhere: ``FrameSelector.entryFor`` (806-836) and ``quietHeader``
(790-804) in :mod:`loomsiril.frames_headers`; ``FrameSelector.Identities`` (700-779) in
:mod:`loomsiril.frames_cache`; ``Frames.measureInBatches``, ``runBatches`` and ``throttle`` (Frames.js
2062-2134) in :mod:`loomsiril.frames_batches`; ``Frames.groupByFilter``, ``notSubframe``, ``comparability`` and the
skipped and unreadable lines in :mod:`loomsiril.frames_groups`; ``FrameSelector.frameFilesIn`` (928-934) in
:mod:`loomsiril.frames_headers`.

The measurement itself is injected: ``measure(siril, paths, work_dir, altitude_of=...)`` returns
``(metrics_by_path, problem)``, as :func:`loomsiril.frames_measure.measure_batch` does. It is imported only when
not given. A measurement that reports a problem, or raises, abandons its channel, as FrameSelector.js 1199-1204
does for a failed SubframeSelector call.

Behaviour kept from the JavaScript, each with a comment at the place it applies:

* identities are taken before and after measuring; a frame whose identity changed is unstable and is dropped;
* a channel whose measurement fails is listed with ``problems: ["measurement failed"]`` and no metrics;
* cancellation is a progress callback returning False, read while reading, before a channel and after each batch;
* a channel stopped part way is left out of the result, but its finished batches are in the cache;
* frames already in the measurement cache count as done for their channel, and are not measured again;
* the cache is written once per settled batch; nothing is written except the cache (under ``cache_dir``).

Deliberate differences from the JavaScript: the work directory of a batch is ``<cache_dir>/measure-work/<n>``, the
batch number ``n`` counting from 1 over the whole scan; the lines that JS prints with ``Util.log`` go to the
``loomsiril.frames_scan`` logger at INFO; a batch measurement that raises is logged and abandons its channel
rather than ending the scan.
"""

from __future__ import annotations

import itertools
import logging
import os
from collections.abc import Callable
from typing import Any, Optional

from loomsiril.frames import stored_metrics
from loomsiril.frames_altitude import altitude_from_header
from loomsiril.frames_batches import measure_in_batches, throttle
from loomsiril.frames_cache import Hasher, Identities, MeasurementStore, cached_split
from loomsiril.frames_files import extract_name
from loomsiril.frames_groups import comparability, group_by_filter, not_subframe, skipped_line, unreadable_line
from loomsiril.frames_headers import entry_for, frame_files_in, quiet_header

Entry = dict[str, Any]
Metrics = Optional[dict[str, Any]]
#: ``measure(siril, paths, work_dir, altitude_of=...) -> (metrics_by_path, problem)``.
MeasureFn = Callable[..., tuple[dict[str, Metrics], Optional[str]]]
#: Called with a batch (list of paths); the metrics by path, or None to abandon the channel.
BatchMeasurer = Callable[[list[str]], Optional[dict[str, Metrics]]]

#: Frames per measurement call. See FrameSelector.js 1036-1043 for the timing that chose it.
MEASURE_BATCH = 8
#: The most often the scan window hears about reading; see Frames.throttle (Frames.js 2119).
READING_EVERY_MS = 100
#: Directory, under the cache directory, where each batch's measurement works.
MEASURE_WORK_DIR = "measure-work"

_log = logging.getLogger("loomsiril.frames_scan")


# ---- the cohort: read every header, fingerprint every light subframe ---------------------------------------

def cohort_from(paths: list[str], reading: Optional[Callable[..., Any]], hasher: Hasher,
                cache_dir: Optional[str]) -> dict[str, Any]:
    """The cohort of ``paths``: ``{entries, before, skipped, unreadable, cancelled}`` (FrameSelector.js 957-995).

    Each entry carries the fingerprint taken BEFORE anything is measured, as ``identity``. A file that cannot be
    fingerprinted is left out of the cohort (listed in ``unreadable``) rather than carried with an unknown identity:
    there would be nothing to compare against afterwards. A master or calibration frame is left out on its header
    alone, BEFORE it is digested (listed in ``skipped``). A frame that looks as it did when last read is not read
    again: its digest comes from the digest cache in ``cache_dir``.
    """
    ids = Identities(hasher, cache_dir=cache_dir, use_cache=True)
    read = read_headers(paths, reading, ids)
    cohort: dict[str, Any] = {"entries": [], "before": {}, "skipped": read["skipped"],
                              "unreadable": read["unreadable"], "cancelled": read["cancelled"]}
    if read["cancelled"]:
        ids.abandon()
        return cohort
    # The digests finish after the last header is read. The window keeps showing the last file meanwhile, and
    # asking it is what reads Cancel.
    last = extract_name(paths[-1]) if paths else ""

    def stop() -> bool:
        return reading is not None and reading(len(paths), len(paths), last) is False

    done = ids.finish(stop)
    if done["cancelled"]:
        cohort["cancelled"] = True
        return cohort
    for e in read["subs"]:
        ident = done["ids"].get(e["path"])
        if ident is None:
            cohort["unreadable"].append(e["path"])
            continue
        cohort["before"][e["path"]] = ident
        e["identity"] = ident
        cohort["entries"].append(e)
    return cohort


def read_headers(paths: list[str], reading: Optional[Callable[..., Any]], ids: Identities) -> dict[str, Any]:
    """The header of each path, and a digest asked for as soon as a light subframe's header says so.

    ``{subs, skipped, unreadable, cancelled}`` (FrameSelector.js 1002-1030). ``reading(done, total, name)`` is told
    once per file BEFORE the file is read, so a path that turns out unreadable still advances the count; returning
    False stops the loop.
    """
    out: dict[str, Any] = {"subs": [], "skipped": [], "unreadable": [], "cancelled": False}
    for i, path in enumerate(paths):
        if reading is not None and reading(i + 1, len(paths), extract_name(path)) is False:
            out["cancelled"] = True
            break
        ids.poll()
        if not os.path.exists(path):
            out["unreadable"].append(path)
            continue
        e = entry_for(path)
        kind = not_subframe(extract_name(path), e["imageType"])
        if kind is not None:
            out["skipped"].append({"path": path, "kind": kind})
            continue
        ids.request(path)
        out["subs"].append(e)
    return out


# ---- measuring: batch by batch, each batch settled while the next is measured -------------------------------

def measure_batched(paths: list[str], size: int, measure: BatchMeasurer,
                    on_batch: Optional[Callable[[int], Any]], settle: Any) -> dict[str, Any]:
    """Measure ``paths`` in batches of ``size`` through ``measure`` (FrameSelector.js 1050-1057).

    Returns ``{measured, cancelled, abandoned}`` as :func:`loomsiril.frames_batches.measure_in_batches` does.
    """
    return measure_in_batches(paths, size, measure, on_batch, settle)


class _Settler:
    """A measured batch's identities are asked for at once and finished later (FrameSelector.js 1132-1145).

    ``start`` takes fresh identities (never the digest cache: a settle must digest afresh), ``finish`` hands
    ``check(batch, measured, now)`` with ``now = {path: identity or None}``, and ``kill`` stops the digests.
    """

    def __init__(self, hasher: Hasher, check: Callable[[list[str], Any, dict[str, Any]], None]) -> None:
        self._hasher = hasher
        self._check = check

    def start(self, batch: list[str], measured: Any) -> dict[str, Any]:
        ids = Identities(self._hasher)
        for p in batch:
            ids.request(p)
        ids.poll()
        return {"batch": batch, "measured": measured, "ids": ids}

    def finish(self, token: dict[str, Any]) -> None:
        now = token["ids"].finish(None)["ids"]
        self._check(token["batch"], token["measured"], now)

    def kill(self, token: dict[str, Any]) -> None:
        token["ids"].kill()


def settler(hasher: Hasher, check: Callable[[list[str], Any, dict[str, Any]], None]) -> _Settler:
    """The settle for :func:`measure_batched` (FrameSelector.js 1132-1145)."""
    return _Settler(hasher, check)


def measure_group(group: list[Entry], before: dict[str, Entry], on_batch: Optional[Callable[[int], Any]],
                  split: Optional[dict[str, Any]], hasher: Hasher, store: MeasurementStore,
                  measure: BatchMeasurer) -> Optional[dict[str, Any]]:
    """Fingerprint, measure, fingerprint again, for one channel (FrameSelector.js 1094-1124).

    Returns ``{metrics, unstable, cancelled}``, or None when the channel must be abandoned, which discards the
    channel's numbers too. A frame whose identity differs across the measurement is UNSTABLE: something rewrote it
    while it was being read, so its numbers describe bytes that are no longer there, and it is dropped from the
    channel. Each batch's numbers are stored per settled batch, so a scan cancelled half way keeps the half it
    measured. ``cancelled`` is set when ``on_batch`` returned False.
    """
    plan = split if split is not None else cached_split(group, store)
    metrics: dict[str, Any] = plan["metrics"]
    unstable: list[str] = []
    if len(plan["need"]) == 0:
        return {"metrics": metrics, "unstable": [], "cancelled": False}

    def check(batch: list[str], measured: Any, now: dict[str, Any]) -> None:
        for path in batch:
            if now.get(path) is None or now[path]["digest"] != before[path]["digest"]:
                unstable.append(path)
                continue  # the measured bytes are gone
            if measured.get(path) is not None:
                stored = stored_metrics(measured[path])
                store.store(before[path], stored)
                metrics[path] = stored
        store.save()

    run = measure_batched(plan["need"], MEASURE_BATCH, measure, on_batch, settler(hasher, check))
    if run["abandoned"]:
        return None
    return {"metrics": metrics, "unstable": unstable, "cancelled": run["cancelled"]}


# ---- progress --------------------------------------------------------------------------------------------

class _Reporter:
    """One channel's report (FrameSelector.js 1223-1234): ``self(n)`` says n of this channel's frames to measure
    are done, moves the whole-scan count on, and answers whether to go on. ``base`` is where the count started.
    """

    def __init__(self, measuring: Optional[Callable[..., Any]], filter_: str, size: int, cached: int,
                 overall: dict[str, Any]) -> None:
        self._measuring = measuring
        self._filter = filter_
        self._size = size
        self._cached = cached
        self._overall = overall
        self.base: int = overall["done"]

    def __call__(self, measured: int) -> bool:
        self._overall["done"] = self.base + measured
        if self._measuring is None:
            return True
        # the channel's count includes the frames that were cached already: they are done too
        return self._measuring(self._cached + measured, self._size, self._filter, self._overall) is not False


def measuring_reporter(measuring: Optional[Callable[..., Any]], filter_: str, size: int, cached: int,
                       overall: dict[str, Any]) -> _Reporter:
    """A reporter for one channel, created before the channel starts (FrameSelector.js 1223-1234)."""
    return _Reporter(measuring, filter_, size, cached, overall)


def _batch_measurer(siril: Any, cache_dir: str, measure: MeasureFn) -> BatchMeasurer:
    """The FrameSelector.measure equivalent: one measurement call per batch, in its own work directory."""
    numbers = itertools.count(1)

    def run(batch: list[str]) -> Optional[dict[str, Metrics]]:
        work_dir = os.path.join(cache_dir, MEASURE_WORK_DIR, str(next(numbers)))
        try:
            results, problem = measure(siril, batch, work_dir, altitude_of=_altitude_of)
        except Exception as e:  # noqa: BLE001 -- a failed measurement abandons its channel, the scan goes on
            _log.warning("measurement failed: %s", e)
            return None
        if problem is not None:
            _log.warning("measurement failed: %s", problem)
            return None
        return results

    return run


def _altitude_of(path: str) -> Optional[float]:
    """A frame's altitude from its header (see :mod:`loomsiril.frames_altitude`); None when it has none."""
    _info, keyword = quiet_header(path)
    return altitude_from_header(keyword)


def _measure_module() -> Any:
    """The measurement and its configuration, imported when a scan needs them."""
    from loomsiril import frames_measure  # lazy: a scan of cached frames needs no Siril route
    return frames_measure


# ---- the scan --------------------------------------------------------------------------------------------

def _result(channels: dict[str, Any], unstable: list[str], skipped: list[dict[str, Any]],
            cancelled: bool) -> dict[str, Any]:
    return {"channels": channels, "unstable": unstable, "skipped": skipped, "cancelled": cancelled}


def scan_paths(paths: list[str], progress: Any, *, siril: Any, cache_dir: str,
               measure: Optional[MeasureFn] = None, hasher: Optional[Hasher] = None,
               config_text: Optional[str] = None) -> dict[str, Any]:
    """Scan an explicit list of frames (FrameSelector.js 1168-1216).

    ``progress`` is None or an object whose optional ``reading(done, total, name)`` and
    ``measuring(done, total, filter, overall)`` callables are used; a callable returning False cancels the scan.
    ``overall`` is ``{done, total, channel, channels}`` over the frames still to be measured in the whole scan
    (cached frames are not in it); ``done`` and ``total`` in the channel's call count its cached frames too.
    ``measure`` and ``config_text`` default to :mod:`loomsiril.frames_measure`.

    Returns ``{channels: {filter: {entries, metrics, problems}}, unstable, skipped, cancelled}``. A cancelled
    channel is not in ``channels``. Nothing is written except the digest cache and the measurement cache, both in
    ``cache_dir``, and the work directories of the measurements, which are under ``cache_dir`` too.
    """
    if measure is None or config_text is None:
        frames_measure = _measure_module()
        measure = measure if measure is not None else frames_measure.measure_batch
        config_text = config_text if config_text is not None else frames_measure.MEASURE_CONFIG
    own_hasher = hasher is None
    pool = hasher if hasher is not None else Hasher()
    reading_cb = getattr(progress, "reading", None) if progress is not None else None
    measuring_cb = getattr(progress, "measuring", None) if progress is not None else None
    try:
        return _scan(paths, reading_cb, measuring_cb, pool, siril, cache_dir, measure, config_text)
    finally:
        if own_hasher:
            pool.close()


def _scan(paths: list[str], reading_cb: Optional[Callable[..., Any]], measuring_cb: Optional[Callable[..., Any]],
          hasher: Hasher, siril: Any, cache_dir: str, measure: MeasureFn, config_text: str) -> dict[str, Any]:
    reading = throttle(reading_cb, READING_EVERY_MS)
    cohort = cohort_from(paths, reading, hasher, cache_dir)
    if cohort["cancelled"]:
        return _result({}, [], cohort["skipped"], True)
    for line in (skipped_line(cohort["skipped"]), unreadable_line(cohort["unreadable"])):
        if line:
            _log.info(line)

    groups = group_by_filter(cohort["entries"])
    keys = list(groups)
    store = MeasurementStore(cache_dir, config_text)
    splits = [cached_split(groups[k], store) for k in keys]
    overall: dict[str, Any] = {"done": 0, "total": sum(len(s["need"]) for s in splits), "channel": 0,
                               "channels": len(keys)}
    measure_fn = _batch_measurer(siril, cache_dir, measure)
    channels: dict[str, Any] = {}
    unstable: list[str] = []

    for g, key in enumerate(keys):
        group = groups[key]
        tell = measuring_reporter(measuring_cb, key, len(group), len(group) - len(splits[g]["need"]), overall)
        overall["channel"] = g + 1
        if not tell(0):
            return _result(channels, unstable, cohort["skipped"], True)
        measured = measure_group(group, cohort["before"], tell, splits[g], hasher, store, measure_fn)
        overall["done"] = tell.base + len(splits[g]["need"])
        if measured is None:  # the channel was abandoned
            channels[key] = {"entries": group, "metrics": {}, "problems": ["measurement failed"]}
            continue
        unstable.extend(measured["unstable"])
        # A channel stopped half way is not in the result: its statistics would describe a set that is not the
        # channel. Its finished batches are already in the cache.
        if measured["cancelled"]:
            return _result(channels, unstable, cohort["skipped"], True)
        channels[key] = {"entries": group, "metrics": measured["metrics"],
                         "problems": comparability(group)["problems"]}
    return _result(channels, unstable, cohort["skipped"], False)


def scan(folder: str, progress: Any, *, siril: Any, cache_dir: str, measure: Optional[MeasureFn] = None,
         hasher: Optional[Hasher] = None, config_text: Optional[str] = None) -> dict[str, Any]:
    """Scan a folder's frame files (FrameSelector.js 1236-1239): :func:`scan_paths` over :func:`frame_files_in`."""
    return scan_paths(frame_files_in(folder), progress, siril=siril, cache_dir=cache_dir, measure=measure,
                      hasher=hasher, config_text=config_text)
