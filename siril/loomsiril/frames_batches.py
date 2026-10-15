"""Frame Selector: measuring in batches, the settle that overlaps the next batch, and the progress throttle.

Ported from ``pixinsight/script/lib/Frames.js``: ``measureInBatches`` (2062-2074), ``runBatches`` (2077-2107),
``NO_SETTLER`` (2109) and ``throttle`` (2119-2134). ``batchesOf``, ``mergeBatch`` and ``measuringLines`` are the
ports in ``loomsiril.frames_files``. Pinned to the JavaScript by ``tests/oracle/fs_batches.json``.

Callbacks are Python callables. Where the JavaScript tests a callback's result with ``=== false`` (onBatch), only an
explicit False stops; a result of None or any other value goes on.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from loomsiril.frames_files import batches_of, merge_batch

Batch = list[str]
Measure = Callable[[Batch], Optional[dict[str, Any]]]


class _NoSettler:
    """The settler that does nothing: start gives no token, so nothing is finished or killed."""

    def start(self, batch: Batch, got: Any) -> None:
        return None

    def finish(self, token: Any) -> None:
        return None

    def kill(self, token: Any) -> None:
        return None


#: The settler used when none is given.
NO_SETTLER = _NoSettler()


def run_batches(batches: list[Batch], measure_batch: Measure, on_batch: Optional[Callable[[int], Any]],
                s: Any, state: dict[str, Any]) -> dict[str, Any]:
    """measure_in_batches' loop; ``state["pending"]`` is the batch whose settle is still open."""
    measured = state["measured"]
    done = 0

    def finish_pending() -> None:
        if state["pending"] is not None:
            s.finish(state["pending"])
        state["pending"] = None

    for batch in batches:
        got = measure_batch(batch)
        # An abandoned channel still settles the batch before: it was measured, and caching it is what happened
        # before the settles overlapped.
        finish_pending()
        if not merge_batch(batch, got, measured):
            return {"measured": None, "cancelled": False, "abandoned": True}
        state["pending"] = s.start(batch, got)
        done += len(batch)
        if on_batch is not None and on_batch(done) is False:
            finish_pending()
            return {"measured": measured, "cancelled": True, "abandoned": False}
    finish_pending()
    return {"measured": measured, "cancelled": False, "abandoned": False}


def measure_in_batches(paths: Optional[list[str]], size: Any, measure: Measure,
                       on_batch: Optional[Callable[[int], Any]] = None, settler: Any = None) -> dict[str, Any]:
    """Measure ``paths`` batch by batch; returns ``{measured, cancelled, abandoned}``.

    ``measure(batch)`` returns a map of path to metrics, or None to abandon the channel. ``on_batch(done)`` is told
    how many frames are done and returning False stops before the next batch. ``settler`` has start(batch, got),
    finish(token) and kill(token): batch k is finished only after batch k+1 is measured. An exception kills the
    batch still open and propagates.
    """
    s = settler if settler is not None else NO_SETTLER
    state: dict[str, Any] = {"measured": {}, "pending": None}
    try:
        return run_batches(batches_of(paths or [], size), measure, on_batch, s, state)
    except BaseException:
        # nothing is left digesting, or holding temporary files, behind an error
        if state["pending"] is not None:
            s.kill(state["pending"])
        raise


def _monotonic_ms() -> float:
    return time.monotonic() * 1000.0


def throttle(fn: Optional[Callable[[int, int, Any], Any]], ms: float,
             clock: Optional[Callable[[], float]] = None) -> Optional[Callable[..., Any]]:
    """A progress callback called at most once every ``ms`` milliseconds, or None when ``fn`` is None.

    The first call always goes through, and so does the first report of the last file. A call held back returns
    True, which means "go on". ``clock`` returns milliseconds; the default is the monotonic clock.
    """
    if fn is None:
        return None
    now = clock if clock is not None else _monotonic_ms
    last: Optional[float] = None
    last_done: Any = None

    def report(done: int, total: int, name: Any = None) -> Any:
        nonlocal last, last_done
        t = now()
        edge = last is None or (done == total and last_done != total)
        if not edge and t - last < ms:
            return True
        last = t
        last_done = done
        return fn(done, total, name)

    return report
