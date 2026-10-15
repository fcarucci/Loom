"""Frame Selector: frame identities and the measurement cache.

Ported from ``pixinsight/script/FrameSelector.js`` (the line numbers are that file's) and, for the hasher's job
interface, ``pixinsight/script/lib/Hasher.js`` (``Hasher.forScan``, ``Hasher.Job``, lines 180-300). Each JS function
has a Python twin with the same meaning, named in snake_case:

    digest                          602  -> digest (sha1 of the whole file, streamed)
    fileStat                        633  -> file_stat
    identityOf                      647  -> identity_of
    fileIdentity                    653  -> file_identity
    newHasher                       664  -> Hasher() (the scan's runner; Hasher.forScan's system tool is replaced by hashlib)
    digestCachePath                 675  -> DIGEST_CACHE_NAME inside the cache directory
    loadDigestCache                 680  -> _load_digest_cache
    saveDigestCache                 691  -> _save_digest_cache
    Identities                      714  -> Identities.__init__
    Identities.request              723  -> Identities.request
    Identities.poll                 735  -> Identities.poll
    Identities.kill                 740  -> Identities.kill
    Identities.abandon              746  -> Identities.abandon
    Identities.finish               752  -> Identities.finish
    Identities.remember             766  -> Identities.remember
    cachePath                       838  -> TABLE_NAME inside the cache directory
    loadTable                       845  -> MeasurementStore.load_table
    saveTable                       860  -> MeasurementStore.save_table
    configSignature                 879  -> config_signature
    measurementKey                  892  -> MeasurementStore.key
    cachedMeasurement               898  -> MeasurementStore.cached
    storeMeasurement                911  -> MeasurementStore.store
    saveMeasurements                920  -> MeasurementStore.save
    cachedSplit                    1060  -> cached_split

Differences from the JavaScript, all deliberate:

* No module globals for the cache location or the table: a MeasurementStore or an Identities holds its own.
* ``MEASURE_VERSION`` is the Siril port's version (``"siril-v1"``), not the PixInsight ``"v3"``.
* ``created`` is always ``st_ctime``, as FrameSelector.js 635 says (``FileInfo.timeCreated`` is ctime on macOS).
  It is NOT ``st_birthtime``: a birth time does not change on edit, so a same-size edit with the mtime restored
  would hit the digest cache. Times are integer milliseconds, as JS ``getTime()`` gives.
* ``digest`` warns once for a file it cannot read, including one that does not exist (FrameSelector.js 602 is silent
  for a missing file). The result is still None.
* Digests run on a thread pool of ``min(4, os.cpu_count())`` threads with ``hashlib.sha1``, not on an external
  system tool. ``Hasher`` and ``HashJob`` keep the job interface of Hasher.js: ``start``, ``add``, ``poll``,
  ``wait``, ``kill`` and ``digests``.

The pure rules of the digest cache (hit, racy, record) are in :mod:`loomsiril.frames_files` and the usability of a
cached measurement is :func:`loomsiril.frames.cache_entry_usable`; this module calls them and does not copy them.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from typing import Any, Optional

from loomsiril.frames import cache_entry_usable
from loomsiril.frames_files import digest_cache_hit, digest_cache_record

Entry = dict[str, Any]
Warn = Optional[Callable[[str], None]]

#: The measurement version in every key. Replaces Frames.MEASURE_VERSION ("v3") of the PixInsight script.
MEASURE_VERSION = "siril-v1"
#: The digest cache file, beside the measurements.
DIGEST_CACHE_NAME = "frame-digests.json"
#: The measurement table file.
TABLE_NAME = "frame-quality.json"
#: Bytes read per step of a digest.
DIGEST_CHUNK = 4 * 1024 * 1024
#: How often a waiting job checks for a stop request (seconds).
POLL_SECONDS = 0.05

_log = logging.getLogger("loomsiril.frames_cache")


def _warn(warn: Warn, message: str) -> None:
    (warn or _log.warning)(message)


# ---- identities --------------------------------------------------------------------------------------

def digest(path: str, warn: Warn = None) -> Optional[str]:
    """SHA-1 hex of the whole file, streamed in 4 MiB chunks; None (and one warning) when it cannot be read."""
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(DIGEST_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError as e:
        _warn(warn, f"could not digest {path}: {e}")
        return None


def file_stat(path: str) -> Optional[Entry]:
    """``{size, mtime, created, at}`` in integer milliseconds, or None for a file that is not there.

    ``created`` is ``st_ctime`` (the status change time, not the birth time: see the module docstring), in
    milliseconds, never None; ``at`` is the time the stat was taken, before any digest. The caller takes this
    BEFORE digesting the file.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {"size": st.st_size, "mtime": st.st_mtime_ns // 1_000_000, "created": st.st_ctime_ns // 1_000_000,
            "at": time.time_ns() // 1_000_000}


def identity_of(stat: Optional[Entry], digest_hex: Optional[str]) -> Optional[Entry]:
    """``{digest, size, mtime}``, or None when either the stat or the digest is missing."""
    if stat is None or digest_hex is None:
        return None
    return {"digest": digest_hex, "size": stat["size"], "mtime": stat["mtime"]}


def file_identity(path: str) -> Optional[Entry]:
    """The identity of a file: stat first, then its digest."""
    stat = file_stat(path)
    return None if stat is None else identity_of(stat, digest(path))


class HashJob:
    """Digests of a set of paths on a pool, started as they are added. See Hasher.Job in Hasher.js."""

    def __init__(self, pool: concurrent.futures.ThreadPoolExecutor, warn: Warn) -> None:
        self._pool = pool
        self._warn = warn
        self._futures: dict[str, concurrent.futures.Future[Optional[str]]] = {}
        self._frozen: Optional[dict[str, str]] = None

    def add(self, path: str) -> None:
        """Queue a path's digest. A path already added, or added after a kill, is ignored."""
        if path in self._futures or self._frozen is not None:
            return
        self._futures[path] = self._pool.submit(digest, path, self._warn)

    def _taken(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for path, f in self._futures.items():
            if f.done() and not f.cancelled() and f.result() is not None:
                out[path] = f.result()  # type: ignore[assignment]
        return out

    @property
    def digests(self) -> dict[str, str]:
        """The digests taken so far: a path that failed or is still running is absent."""
        return dict(self._frozen) if self._frozen is not None else self._taken()

    def poll(self) -> bool:
        """True once every added path has an answer (a digest, or None for a file that cannot be read)."""
        return all(f.done() for f in self._futures.values())

    def kill(self) -> None:
        """Stop starting digests and freeze the ones taken so far. A digest already running finishes unseen."""
        if self._frozen is not None:
            return
        self._frozen = self._taken()
        for f in self._futures.values():
            f.cancel()

    def wait(self, should_stop: Optional[Callable[[], bool]] = None) -> dict[str, Any]:
        """Block until every digest is in, or until ``should_stop()`` is true (then the job is killed).

        Returns ``{"digests": {path: hex}, "cancelled": bool}``. ``should_stop`` is asked before each wait, so a
        stop requested before the digests finish is reported as cancelled even when they had all finished.
        """
        cancelled = False
        while True:
            if should_stop is not None and should_stop():
                cancelled = True
                break
            if self.poll():
                break
            pending = [f for f in self._futures.values() if not f.done()]
            concurrent.futures.wait(pending, timeout=POLL_SECONDS,
                                    return_when=concurrent.futures.FIRST_COMPLETED)
        if cancelled:
            self.kill()
        return {"digests": self.digests, "cancelled": cancelled}


class Hasher:
    """One scan's SHA-1 runner: a thread pool shared by the jobs it starts. Replaces Hasher.forScan (Hasher.js)."""

    def __init__(self, warn: Warn = None) -> None:
        self._warn = warn
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1),
                                                           thread_name_prefix="frame-digest")

    def start(self, paths: Optional[list[str]] = None) -> HashJob:
        """A job, with ``paths`` already added. Non-blocking: the digests run while the caller goes on."""
        job = HashJob(self._pool, self._warn)
        for p in paths or []:
            job.add(p)
        return job

    def close(self) -> None:
        """Drop the queued digests and release the pool's threads once the running ones end."""
        self._pool.shutdown(wait=False, cancel_futures=True)


def _write_atomic(path: str, text: str) -> None:
    """Writes ``text`` to ``path`` through a temp file in the same directory and an os.replace.

    A reader sees the old file or the new one, never a half-written one; a failure leaves the old file in place
    and removes the temp file. Raises OSError.
    """
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _digest_cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, DIGEST_CACHE_NAME)


def _load_digest_cache(cache_dir: Optional[str]) -> dict[str, Any]:
    """The digest cache; an empty one when there is no directory, no file, or the file is not a JSON object."""
    if cache_dir is None:
        return {}
    try:
        with open(_digest_cache_path(cache_dir), encoding="utf-8") as f:
            table = json.load(f)
    except (OSError, ValueError):
        return {}
    return table if isinstance(table, dict) else {}


def _save_digest_cache(cache_dir: Optional[str], table: dict[str, Any]) -> None:
    """Writes the digest cache, creating its directory. A failure only warns."""
    if cache_dir is None:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        _write_atomic(_digest_cache_path(cache_dir), json.dumps(table, separators=(",", ":"), ensure_ascii=False))
    except (OSError, TypeError, ValueError) as e:
        _warn(None, f"could not save the frame digests: {e}")


class Identities:
    """Identities for many files at once, digested in the background (FrameSelector.js 700-779).

    ``request(path)`` takes the stat now and queues the digest; ``poll()`` and ``kill()`` move the job along;
    ``finish(should_stop)`` waits and gives ``{"ids": {path: identity or None}, "cancelled": bool}``, each identity
    exactly what :func:`file_identity` would give.

    ``use_cache`` answers from the digest cache in ``cache_dir`` where a file still looks as it did, and remembers
    what was read; it is saved once, by ``finish`` or ``abandon``. Without ``cache_dir`` the cache lives only as
    long as this object. Only a pre-scan identity should ask for the cache: a settle or a deletion must digest afresh.
    """

    def __init__(self, hasher: Optional[Hasher] = None, cache_dir: Optional[str] = None,
                 use_cache: bool = False) -> None:
        self._cache_dir = cache_dir
        self._table: Optional[dict[str, Any]] = _load_digest_cache(cache_dir) if use_cache else None
        self._stats: dict[str, Optional[Entry]] = {}
        self._hits: dict[str, str] = {}
        self._hasher = hasher if hasher is not None else Hasher()
        self._job = self._hasher.start([])

    def request(self, path: str) -> None:
        stat = self._stats[path] = file_stat(path)
        if stat is None:
            return
        hit = digest_cache_hit(self._table, path, stat)
        if hit is not None:
            self._hits[path] = hit
        else:
            self._job.add(path)

    def poll(self) -> bool:
        return self._job.poll()

    def kill(self) -> None:
        self._job.kill()

    def abandon(self) -> None:
        """Cancelled before finish: stops the digests and remembers those already taken, and no others."""
        self._job.kill()
        self.remember(self._job.digests, True)

    def finish(self, should_stop: Optional[Callable[[], bool]] = None) -> dict[str, Any]:
        r = self._job.wait(should_stop)
        ids = {path: identity_of(stat, self._hits.get(path) or r["digests"].get(path))
               for path, stat in self._stats.items()}
        self.remember(r["digests"], r["cancelled"])
        return {"ids": ids, "cancelled": r["cancelled"]}

    def remember(self, digests: dict[str, str], cancelled: bool) -> None:
        """Every path read in full goes into the cache, or out of it; saved once if any was.

        A cancelled scan leaves alone a path it never read. A path answered from the cache is not read again.
        """
        if self._table is None:
            return
        read = 0
        for path, stat in self._stats.items():
            if stat is None or path in self._hits:
                continue
            if cancelled and digests.get(path) is None:
                continue
            digest_cache_record(self._table, path, stat, digests.get(path))
            read += 1
        if read > 0:
            _save_digest_cache(self._cache_dir, self._table)


# ---- measurements ------------------------------------------------------------------------------------

def config_signature(config_text: str) -> str:
    """The first 12 hex digits of the SHA-1 of the configuration text.

    A measurement taken under other settings is not comparable with a fresh one, so the configuration is part of
    the key (as WBPP folds the settings into its own measurement cache).
    """
    return hashlib.sha1(config_text.encode("utf-8")).hexdigest()[:12]


class MeasurementStore:
    """The measurement table in ``cache_dir``, keyed by version | configuration | digest (FrameSelector.js 838-926).

    The key is the digest, not the path: a cached number whose digest no longer matches the file is not found.
    ``store`` puts an entry in memory; ``save`` writes the table once, and only when something changed.
    """

    def __init__(self, cache_dir: str, config_text: str) -> None:
        self._dir = cache_dir
        self._config = config_signature(config_text)
        self._table: Optional[dict[str, Any]] = None
        self._changed = False

    def load_table(self) -> dict[str, Any]:
        """The table, read once; an empty table when the file is missing, corrupt or not a JSON object."""
        if self._table is not None:
            return self._table
        table: dict[str, Any] = {}
        try:
            with open(os.path.join(self._dir, TABLE_NAME), encoding="utf-8") as f:
                parsed = json.load(f)
            if isinstance(parsed, dict):
                table = parsed
        except (OSError, ValueError):
            pass
        self._table = table
        return table

    def save_table(self) -> None:
        """Writes the whole table, creating its directory. A failure only warns."""
        table = self.load_table()
        try:
            os.makedirs(self._dir, exist_ok=True)
            _write_atomic(os.path.join(self._dir, TABLE_NAME),
                          json.dumps(table, separators=(",", ":"), ensure_ascii=False))
        except (OSError, TypeError, ValueError) as e:
            _warn(None, f"could not save measurements: {e}")

    def key(self, identity: Entry) -> str:
        return f"{MEASURE_VERSION}|{self._config}|{identity['digest']}"

    def cached(self, identity: Entry) -> Optional[Entry]:
        """The stored metrics for this identity, or None when there are none or the entry is not usable."""
        e = self.load_table().get(self.key(identity))
        return e if cache_entry_usable(e) else None

    def store(self, identity: Entry, metrics: Entry) -> None:
        self.load_table()[self.key(identity)] = metrics
        self._changed = True

    def save(self) -> None:
        """Writes the table if anything was stored since the last save; otherwise leaves the file alone."""
        if not self._changed:
            return
        self._changed = False
        self.save_table()


def cached_split(group: list[dict[str, Any]], store: MeasurementStore) -> dict[str, Any]:
    """A channel's frames split into those already measured and those not.

    ``group`` items carry ``path`` and ``identity``. Returns ``{"need": [paths], "metrics": {path: metrics}}``.
    """
    need: list[str] = []
    metrics: dict[str, Any] = {}
    for item in group:
        cached = store.cached(item["identity"])
        if cached is not None:
            metrics[item["path"]] = cached
        else:
            need.append(item["path"])
    return {"need": need, "metrics": metrics}
