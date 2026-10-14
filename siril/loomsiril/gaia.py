"""Gaia DR3 catalogue access: online cone search, CSV/TSV parsing, and a disk cache (Fly.js 2039-2063, Sky.js 136-204).

Online access is reached only through an injected ``fetch_text(url) -> str`` (the default is
:func:`fetch_text` below, which uses urllib). The test suite never calls the default. Records are
plain dicts ``{ra, dec, plx, pmra, pmdec, G, BP, RP}`` with NaN for a blank, as in the JS.
"""

from __future__ import annotations

import http.client
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from loomsiril.jsmath import number_str

__all__ = [
    "GAIA_ONLINE_URL",
    "GAIA_ONLINE_COLUMNS",
    "GAIA_ONLINE_MAX_ROWS",
    "GAIA_ONLINE_TIMEOUT",
    "GAIA_ONLINE_STALL",
    "GAIA_ONLINE_FIRST_BYTE",
    "GAIA_ONLINE_TOTAL",
    "GaiaError",
    "GaiaQuery",
    "cache_key",
    "default_cache_dir",
    "fetch_text",
    "online_url",
    "parse_csv",
    "query_online",
    "read_tsv",
    "sources",
]

#: Fly.GAIA_ONLINE_URL: VizieR's TAP service (CDS), an HTTPS endpoint.
GAIA_ONLINE_URL: str = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
#: Fly.GAIA_ONLINE_COLUMNS: the columns asked for, in the order parse_csv expects them.
GAIA_ONLINE_COLUMNS: tuple[str, ...] = ("RA_ICRS", "DE_ICRS", "Plx", "pmRA", "pmDE", "Gmag", "BPmag", "RPmag")
#: Fly.GAIA_ONLINE_MAX_ROWS: the MAXREC of the request (a 2 deg field has well under this many sources).
GAIA_ONLINE_MAX_ROWS: int = 500000

# Sky.js 78-166. The JS keeps the connect timeout in seconds and the other limits in ms; this port
# uses seconds throughout (ms / 1000).
#: Sky.GAIA_ONLINE_TIMEOUT: seconds to connect.
GAIA_ONLINE_TIMEOUT: float = 60
#: Sky.GAIA_ONLINE_STALL: seconds without a byte before a transfer is abandoned.
GAIA_ONLINE_STALL: float = 30.0
#: Sky.GAIA_ONLINE_FIRST_BYTE: seconds to the first byte (VizieR works a dense field out first).
GAIA_ONLINE_FIRST_BYTE: float = 120.0
#: Sky.GAIA_ONLINE_TOTAL: seconds for a whole answer, so a trickle never stalls forever.
GAIA_ONLINE_TOTAL: float = 600.0

_CHUNK = 65536
_HEADER = ",".join(GAIA_ONLINE_COLUMNS)
_TSV_COLUMNS = ("ra", "dec", "plx", "pmra", "pmdec", "G", "BP", "RP")
# JS Number() on a string: an optional sign, decimal digits (or Infinity), an optional exponent.
_JS_DECIMAL = re.compile(r"[+-]?(?:Infinity|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")


class GaiaError(RuntimeError):
    """A Gaia query failed: no connection, a stalled or over-long transfer, or an answer that is not a source table."""


def _num(v: str) -> float:
    """A CSV/TSV field as a number: blank is NaN (the JS ``v === "" ? NaN : Number(v)``), as JS Number() otherwise."""
    if v == "":
        return math.nan
    s = v.strip()
    if s == "":
        return 0.0  # JS Number("   ") is 0
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
        return float(int(s, 16))
    if _JS_DECIMAL.fullmatch(s):
        return float(s)
    return math.nan


@dataclass(frozen=True)
class GaiaQuery:
    """One Gaia cone request: ``centre`` is ``(ra, dec)`` in degrees, ``radius_deg`` the cone radius, ``g_max`` the G limit.

    The value object the four request functions below take (online_url, query_online, cache_key, sources).
    Build it from the JS ``{ra, dec}`` shape or an ``(ra, dec)`` pair with :meth:`from_centre`.
    """

    centre: tuple[float, float]
    radius_deg: float
    g_max: float

    def __post_init__(self) -> None:
        """Refuse a centre off the sky (ra finite in [0, 360), dec finite in [-90, 90]) with a ValueError."""
        ra, dec = self.centre
        if not (math.isfinite(ra) and 0 <= ra < 360):
            raise ValueError(f"Gaia centre ra must be finite and in [0, 360), not {ra!r}")
        if not (math.isfinite(dec) and -90 <= dec <= 90):
            raise ValueError(f"Gaia centre dec must be finite and in [-90, 90], not {dec!r}")

    @classmethod
    def from_centre(cls, centre: Mapping[str, float] | Sequence[float], radius_deg: float, g_max: float) -> GaiaQuery:
        """A query from a centre given as ``{ra, dec}`` (the JS shape) or an ``(ra, dec)`` pair."""
        return cls(_centre_ra_dec(centre), radius_deg, g_max)


def _centre_ra_dec(centre: Mapping[str, float] | Sequence[float]) -> tuple[float, float]:
    """A centre as ``{ra, dec}`` (the JS shape) or an ``(ra, dec)`` pair, with a finite ra wrapped into [0, 360).

    The wrap is the JS ``%`` of the fields and the targets an angle may come from (a CRVAL1 of -370, a target
    of 360 or -10): the same sky position is queried, and -0.0 and 360 both become 0.0. nan and inf stay as they
    are for GaiaQuery to refuse; dec is never wrapped.
    """
    if isinstance(centre, Mapping):
        ra, dec = float(centre["ra"]), float(centre["dec"])
    else:
        ra, dec = float(centre[0]), float(centre[1])
    if math.isfinite(ra):
        ra = ra % 360.0 + 0.0                # -0.0 -> 0.0 (adding 0.0 clears the sign)
        if ra >= 360.0:                      # a tiny negative angle rounds up to 360 in the %
            ra = 0.0
    return ra, dec


def online_url(query: GaiaQuery) -> str:
    """Fly.gaiaOnlineUrl: the VizieR TAP URL of a Gaia DR3 cone search (ADQL, CSV answer).

    ``encodeURIComponent`` is reproduced by quoting with the same unreserved set (letters, digits and
    ``-_.!~*'()``); the numbers are written as JS writes them, so the URL matches the JS byte for byte.
    """
    ra, dec = query.centre
    q = ("SELECT " + ",".join(GAIA_ONLINE_COLUMNS) + ' FROM "I/355/gaiadr3" WHERE Gmag < ' + number_str(query.g_max) +
         " AND 1=CONTAINS(POINT('ICRS',RA_ICRS,DE_ICRS),CIRCLE('ICRS'," + number_str(ra) + "," +
         number_str(dec) + "," + number_str(query.radius_deg) + "))")
    return (GAIA_ONLINE_URL + "?REQUEST=doQuery&LANG=ADQL&FORMAT=csv&MAXREC=" + str(GAIA_ONLINE_MAX_ROWS) +
            "&QUERY=" + _quote(q))


def _quote(q: str) -> str:
    """JS encodeURIComponent: UTF-8 percent-encoding, leaving A-Z a-z 0-9 and -_.!~*'() alone."""
    return quote(q, safe="-_.!~*'()")


def parse_csv(text: str | None) -> list[dict[str, float]] | None:
    """Fly.parseGaiaCsv: the CSV answer as source records, or None when it is not one.

    The header line must match the requested columns exactly. Line ends may be \\n or \\r\\n. A line with
    the wrong number of fields is skipped (the JS ``continue``). A blank field is NaN.
    """
    if not isinstance(text, str):
        return None
    lines = re.split(r"\r?\n", text)
    if lines[0] != _HEADER:
        return None
    out: list[dict[str, float]] = []
    for line in lines[1:]:
        f = line.split(",")
        if len(f) != len(GAIA_ONLINE_COLUMNS):
            continue
        out.append({"ra": _num(f[0]), "dec": _num(f[1]), "plx": _num(f[2]), "pmra": _num(f[3]),
                    "pmdec": _num(f[4]), "G": _num(f[5]), "BP": _num(f[6]), "RP": _num(f[7])})
    return out


def read_tsv(path: str | os.PathLike[str]) -> list[dict[str, float]]:
    """A source table in the fixture format: tab separated, header ``ra dec plx pmra pmdec G BP RP``, blank is NaN."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if not lines or lines[0].split("\t") != list(_TSV_COLUMNS):
        raise ValueError(f"{path}: not a Gaia TSV (header must be {' '.join(_TSV_COLUMNS)})")
    out: list[dict[str, float]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) != len(_TSV_COLUMNS):
            raise ValueError(f"{path}: a row has {len(f)} fields, expected {len(_TSV_COLUMNS)}: {line[:60]!r}")
        out.append({k: _num(v) for k, v in zip(_TSV_COLUMNS, f)})
    return out


# ---------------------------------------------------------------- transfer

#: Test seams: the opener and the clock. Tests replace them; the defaults are the real ones.
_urlopen: Callable[..., Any] = urllib.request.urlopen
_clock: Callable[[], float] = time.monotonic


def _set_recv_timeout(resp: Any, seconds: float) -> None:
    """Bound the next blocking read on the response's socket (CPython internals; a no-op when absent)."""
    try:
        resp.fp.raw._sock.settimeout(seconds)  # type: ignore[attr-defined]
    except AttributeError:
        pass


def _open(url: str) -> Any:
    """urlopen with the connect timeout; any transport failure, a bad status line included, is a GaiaError."""
    try:
        return _urlopen(url, timeout=GAIA_ONLINE_TIMEOUT)
    except (OSError, http.client.HTTPException) as e:  # URLError is an OSError; a bad status line is HTTPException
        raise GaiaError(f"Gaia online: {e}") from e


def _read_chunk(reader: Callable[[int], bytes], wait: float, first: bool) -> bytes:
    """One read of up to _CHUNK bytes; a timeout or a transport error is a GaiaError naming the limit it broke."""
    try:
        return reader(_CHUNK)
    except (OSError, socket.timeout, http.client.HTTPException) as e:
        what = "first byte" if first else "stall"
        raise GaiaError(f"Gaia online: no data ({what} limit {wait:g} s): {e}") from e


@dataclass(frozen=True)
class _Limits:
    """The three transfer limits of fetch_text, in seconds: to the first byte, between bytes (stall), and for the whole answer."""

    stall_s: float
    first_byte_s: float
    total_s: float


def _check_gap(now: float, start: float, last: float | None, limits: _Limits) -> None:
    """The gap after a chunk: the first byte within the first-byte limit, later bytes within the stall limit."""
    if last is None and now - start > limits.first_byte_s:
        raise GaiaError(f"Gaia online: the first byte came after the first byte limit of {limits.first_byte_s:g} s")
    if last is not None and now - last > limits.stall_s:
        raise GaiaError(f"Gaia online: a stall of over {limits.stall_s:g} s")


def _read_body(resp: Any, start: float, limits: _Limits) -> bytes:
    """Read the response in chunks until its end, applying the three limits (see fetch_text)."""
    stall_s, first_byte_s, total_s = limits.stall_s, limits.first_byte_s, limits.total_s
    # read1 returns after one recv, so each loop turn is one recv and the stall timer measures gaps between
    # bytes; read(n) would fill all n bytes first, and a slow live link would look like a stall.
    reader = getattr(resp, "read1", None) or resp.read
    buf = bytearray()
    last: float | None = None
    while True:
        now = _clock()
        if now - start > total_s:
            raise GaiaError(f"Gaia online: the answer passed the total time limit of {total_s:g} s")
        wait = first_byte_s if last is None else stall_s
        _set_recv_timeout(resp, max(0.001, min(wait, total_s - (now - start))))
        chunk = _read_chunk(reader, wait, last is None)
        if not chunk:
            return bytes(buf)
        now = _clock()
        _check_gap(now, start, last, limits)
        last = now
        buf.extend(chunk)


def fetch_text(
    url: str,
    stall_s: float = GAIA_ONLINE_STALL,
    first_byte_s: float = GAIA_ONLINE_FIRST_BYTE,
    total_s: float = GAIA_ONLINE_TOTAL,
) -> str:
    """Sky.fetchText with Sky.transferWatch: the body of a GET, read in chunks.

    Gives up (GaiaError) when no first byte arrives within ``first_byte_s``, when a later gap between
    reads exceeds ``stall_s``, or when the whole transfer passes ``total_s``. Transport errors, including
    http.client.HTTPException (IncompleteRead, a bad status line), are GaiaError too. The connect timeout is
    GAIA_ONLINE_TIMEOUT. Never called by the test suite.
    """
    start = _clock()
    resp = _open(url)
    try:
        body = _read_body(resp, start, _Limits(stall_s, first_byte_s, total_s))
    finally:
        close = getattr(resp, "close", None)
        if close is not None:
            close()
    return body.decode("utf-8", errors="replace")


def query_online(query: GaiaQuery, fetch_text: Callable[[str], str] = fetch_text) -> list[dict[str, float]]:
    """Sky.queryOnline: fetch the cone (through ``fetch_text``) and parse it; GaiaError when the answer is not a table."""
    url = online_url(query)
    text = fetch_text(url)
    recs = parse_csv(text)
    if recs is None:
        head = (text or "")[:80].replace("\n", " ")
        raise GaiaError(f"Gaia online: the answer is not a Gaia source table ({head!r})")
    return recs


# ---------------------------------------------------------------- cache

def default_cache_dir() -> Path:
    """``$XDG_CACHE_HOME/loomsiril/gaia`` (``~/.cache/loomsiril/gaia`` when unset or not absolute)."""
    base = os.environ.get("XDG_CACHE_HOME", "")
    root = Path(base) if base and os.path.isabs(base) else Path.home() / ".cache"
    return root / "loomsiril" / "gaia"


def _radius_up(radius_deg: float) -> float:
    """The radius rounded UP to 1e-3 deg (the 1e-9 guards float noise such as 0.1 * 1000 = 100.00000000000001)."""
    return math.ceil(round(radius_deg * 1000, 9)) / 1000


def cache_key(query: GaiaQuery) -> str:
    """The cache file's stem: centre rounded to 1e-4 deg, radius rounded UP to 1e-3 deg, g_max to 0.1.

    Two requests whose rounded values agree share one cache file. The radius goes up, so a cached answer
    never covers less sky than was asked for; sources() queries with the same rounded-up radius, so the
    file holds exactly what its name says. The centre (up to 5e-5 deg) and g_max (up to 0.05 mag) are
    rounded to nearest, so a hit may differ from the request by that much.
    """
    ra, dec = query.centre
    r = _radius_up(query.radius_deg)
    return f"ra{round(ra, 4) + 0.0:.4f}_dec{round(dec, 4) + 0.0:.4f}_r{r:.3f}_g{round(query.g_max, 1) + 0.0:.1f}"


def _write_atomic(path: Path, text: str) -> None:
    """Write via ``<file>.part`` and os.replace, so a half-written cache file is never read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(text, encoding="utf-8")
    os.replace(part, path)


def sources(
    query: GaiaQuery,
    source: str = "cache",
    cache_dir: str | os.PathLike[str] | None = None,
    fetch_text: Callable[[str], str] = fetch_text,
    tsv_path: str | os.PathLike[str] | None = None,
) -> list[dict[str, float]]:
    """Sky.querySources for one field, with the source chosen explicitly.

    * ``"tsv"``: read ``tsv_path`` (no filtering by centre or radius; the file is the field).
    * ``"online"``: query through ``fetch_text`` and write the cache file.
    * ``"cache"``: read the cache file if present and valid, else query online and cache the answer.

    Both online paths query with the radius rounded up to 1e-3 deg (see :func:`cache_key`).
    """
    if source == "tsv":
        if tsv_path is None:
            raise ValueError("source 'tsv' needs tsv_path")
        return read_tsv(tsv_path)
    if source not in ("cache", "online"):
        raise ValueError(f"unknown Gaia source {source!r} (use 'tsv', 'online' or 'cache')")
    directory = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    path = directory / (cache_key(query) + ".csv")
    online = replace(query, radius_deg=_radius_up(query.radius_deg))
    if source == "cache" and path.exists():
        recs = parse_csv(path.read_text(encoding="utf-8"))
        if recs is not None:
            return recs
        # an unreadable cache file is a miss: it is replaced by the online answer below
    text = fetch_text(online_url(online))
    recs = parse_csv(text)
    if recs is None:
        head = (text or "")[:80].replace("\n", " ")
        raise GaiaError(f"Gaia online: the answer is not a Gaia source table ({head!r})")
    _write_atomic(path, text)
    return recs
