"""Cache-aware execution of one channel's stage chain (Loom's ``Pipeline.processChain`` and its helpers, lib/Pipeline.js:814-1093).

The chain comes from :func:`loomsiril.loom.cache.build_stage_keys`. The LATEST stage whose entry and companion exist and
load is the restart point: every stage before it is skipped (its companion is still picked up), the hit is used in
place, and the stages after it run and are stored. With the cache off nothing is read or written; with ``ignoreCache``
every lookup misses but results are still stored.

Stage inputs and outputs are FITS paths. A cached entry is never copied: the next stage reads ``<cache>/<key>.fits``
directly, and its companions likewise.

Notes for callers:

* A ``ChainResult.path`` (and each companion path) may be a cache file (a full hit), a work-folder file, or the source
  plate itself (a chain whose stages all pass through). Never modify it in place; copy or convert it instead.
* Runners must never write in place to their input: cache entries are shared by later stages and later runs.
* Do not clear or discard the cache folder while a chain is running: a later stage may still read an in-place entry.

Importing this module has no side effects and does not import sirilpy.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Optional

from loomsiril.io_fits import read_fits

from .cache import CacheStore, resolve_cache_hit
from .runctx import RunContext, StageOutput

__all__ = ["RunCancelled", "ChainResult", "process_chain"]

# Pipeline.js:1055 -- the text of a cancel checkpoint's error.
CANCEL_MESSAGE = "Cancelled by user"


class RunCancelled(RuntimeError):
    """The user cancelled the run at a checkpoint between two stages. Entries stored so far are kept."""


@dataclass
class ChainResult:
    """Where a channel's chain ended: the final plate, its companions (name -> path), the key of the last stage
    (None when the chain has no stage) and the stage the chain restarted from (None when nothing was reused)."""

    path: str
    companions: dict[str, str] = field(default_factory=dict)
    key: Optional[str] = None
    hit_stage: Optional[str] = None


def _key_text(key: str) -> str:
    """The first 12 characters of a key, as Loom's log lines show them."""
    return key[:12]


def _resolve_hit(
    chain: list[Mapping[str, Any]],
    lookups: list[bool],
    store: CacheStore,
    label: str,
    warn: Callable[[str], None],
) -> tuple[int, Optional[str], Optional[str]]:
    """``Pipeline.resolveCacheHit``: the latest usable entry as ``(index, plate path, companion path)``.

    Every entry is verified by reading it (a corrupt one is deleted by the store). The plate and companion are used in
    place, so only their paths are kept. Returns ``(-1, None, None)`` when every stage must run.
    """

    def entry_complete(entry: Mapping[str, Any]) -> bool:
        return store.entry_complete(entry)

    def load(entry: Mapping[str, Any]) -> Optional[str]:
        stage_label = label + " " + str(entry["stage"])
        if store.load(entry["key"]) is None:
            warn("[cache] " + stage_label + " is in the cache but would not load; looking further back")
            return None
        return store.path_for(entry["key"])

    def load_companion(entry: Mapping[str, Any]) -> Optional[str]:
        name = str(entry["companion"])
        if store.load_companion(entry["key"], name) is None:
            warn("[cache] " + label + " " + str(entry["stage"]) + " lost its " + name
                 + " companion; looking further back")
            return None
        return store.companion_path_for(entry["key"], name)

    index, plate, companion = resolve_cache_hit(chain, lookups, entry_complete, load, load_companion)
    return index, plate, companion


def _store_plate(
    store: CacheStore,
    warn: Callable[[str], None],
    stage_label: str,
    entry: Mapping[str, Any],
    label: str,
    plate_path: str,
    companions: Mapping[str, str],
) -> None:
    """Stores a stage's plate under its key, and its companion when the entry names one. A failure is logged and
    the run goes on from the work-folder output."""
    try:
        planes, header = read_fits(plate_path)
    except Exception as exc:  # the output cannot be read back for caching; the run continues without caching it
        warn("[cache] " + stage_label + " could not be read for the cache (" + str(exc) + "); not cached")
        return
    store.store(entry["key"], planes, header,
                meta={"channel": label, "stage": entry["stage"], "params": entry.get("params")})
    name = entry.get("companion")
    if name is not None and name in companions:
        try:
            cplanes, cheader = read_fits(companions[name])
        except Exception as exc:
            warn("[cache] " + stage_label + " " + str(name) + " could not be read for the cache (" + str(exc)
                 + "); not cached")
            return
        store.store_companion(entry["key"], str(name), cplanes, cheader)


def process_chain(
    ctx: RunContext,
    label: str,
    source: Callable[[], str],
    chain: list[dict[str, Any]],
    runners: Mapping[str, Callable[[str, str], StageOutput]],
    store: Optional[CacheStore],
    config: Mapping[str, Any],
) -> ChainResult:
    """Runs a channel's (or composite's) chain with the cache: ``Pipeline.processChain``.

    ``source()`` returns the path of the input plate; it is called only when a stage has to run, never on a full hit.
    ``runners[stage](in_path, out_path)`` returns a :class:`StageOutput`; a stage that has params in the chain but no
    runner is passed through unchanged (and stored, as Loom does). ``store`` None or ``config["useCache"]`` false means
    nothing is read or written. ``config["ignoreCache"]`` makes every lookup miss, while results are still stored.
    Raises :class:`RunCancelled` at a checkpoint when ``ctx.cancelled()`` is true.
    """
    use_cache = store is not None and bool(config.get("useCache"))
    ignore_cache = bool(config.get("ignoreCache"))
    log = ctx.log
    warn = ctx.warn

    n = len(chain)
    lookups: list[bool] = [False] * n
    hit_index = -1
    hit_plate: Optional[str] = None
    hit_companion: Optional[str] = None
    if use_cache and store is not None:
        if not ignore_cache:
            lookups = [store.lookup(entry["key"]) is not None for entry in chain]
        hit_index, hit_plate, hit_companion = _resolve_hit(chain, lookups, store, label, warn)

    current: Optional[str] = None
    companions: dict[str, str] = {}

    def input_path() -> str:
        nonlocal current
        if current is None:
            current = source()
        return current

    for i, entry in enumerate(chain):
        if ctx.cancelled():
            raise RunCancelled(CANCEL_MESSAGE)
        stage = str(entry["stage"])
        key = str(entry["key"])
        stage_label = label + " " + stage

        if i < hit_index:
            # Pipeline.skipSupersededStage
            hit_stage = str(chain[hit_index]["stage"])
            if lookups[i]:
                log("[cache] " + stage_label + " HIT " + _key_text(key) + "... (superseded by later cached stage "
                    + hit_stage + ")")
                name = entry.get("companion")
                if name is not None and store is not None:
                    # An extraction stage's companion is kept even when the stage is superseded, because no later
                    # stage carries it.
                    if store.load_companion(key, str(name)) is not None:
                        companions[str(name)] = store.companion_path_for(key, str(name))
                    else:
                        warn("[cache] " + stage_label + " superseded and its " + str(name)
                             + " companion is missing; this run produces no " + str(name) + " frame for " + label)
            else:
                log("[cache] " + stage_label + " MISS (no entry; skipped -- " + hit_stage + " is cached)")
            continue

        if i == hit_index:
            # Pipeline.reuseCachedStage
            log("[cache] " + stage_label + " HIT " + _key_text(key) + "...")
            current = hit_plate
            if hit_companion is not None and entry.get("companion") is not None:
                companions[str(entry["companion"])] = hit_companion
            continue

        # Pipeline.runAndStoreStage, after the MISS line of Pipeline.processChain
        if use_cache:
            if ignore_cache:
                reason = "cache ignored for this run"
            else:
                reason = "incomplete entry" if lookups[i] else "no entry"
            log("[cache] " + stage_label + " MISS (" + reason + ")")

        in_path = input_path()
        started = time.monotonic()
        runner = runners.get(stage)
        if runner is None:
            # A stage with params but no runner does nothing in this configuration: its input passes through.
            log("[cache] " + stage_label + " has no runner; the plate passes through unchanged")
            out_plate = in_path
            out_companions: dict[str, str] = {}
            skip_cache = False
        else:
            out_path = os.path.join(ctx.work_dir, label + "_" + stage + ".fits")
            out = runner(in_path, out_path)
            out_plate = out.path
            out_companions = dict(out.companions)
            skip_cache = bool(out.skip_cache)

        elapsed = time.monotonic() - started
        log("[stage] " + stage_label + " ran in " + ("%.2f" % elapsed) + " s")

        if use_cache and store is not None:
            if skip_cache:
                # Pipeline.runAndStoreStage returned false: the failure is not cached and retries on the next run.
                log("[cache] " + stage_label + " not cached (the step did not complete)")
            else:
                _store_plate(store, warn, stage_label, entry, label, out_plate, out_companions)

        current = out_plate
        companions.update(out_companions)

    final = input_path()
    return ChainResult(
        path=current if current is not None else final,
        companions=companions,
        key=str(chain[-1]["key"]) if n else None,
        hit_stage=str(chain[hit_index]["stage"]) if hit_index >= 0 else None,
    )
