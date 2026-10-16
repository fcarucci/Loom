"""A small local Gaia DR3 xp_sampled catalogue for the SPCC tests, so no test run asks Siril's online service.

Siril 1.4.4 reads the stars of every SPCC call from its Gaia xp_sampled catalogue (HEALPix level 8, NEST, chunked at
level 1 into 48 files ``siril_cat1_healpix8_xpsamp_<chunk>.dat``). With no local catalogue it asks the online mirror
with HTTP range requests, for every ``spcc`` call. Those requests often fail ("libcurl error: SSL connect error"),
and siril-cli then dies by SIGSEGV: on a failed request Siril walks its list of fallback mirrors, which only the GUI
fills (``initialize_spcc_mirrors`` is called from ``main.c``, never from ``main-cli.c``). One failed request ends
the whole test session.

``install(folder)`` fetches, once and with retries, the stars of every level-8 pixel near the test fields
(``TEST_FIELDS``) and writes them as chunk files of the same format: each file has the chunk's header, an index in
which the pixels not fetched hold no star, and the fetched stars. Siril reads it like the full catalogue; a query
near a test field gets exactly the stars the online service would send.

The test image installs it at build time into the folder Siril reads by default (``default_folder()``, Siril's
``core.catalogue_gaia_photo``): ``python3 gaia_xpsamp.py [folder]``. ``covered()`` tells whether a folder holds it.

Stdlib only: the Dockerfile runs this file on its own, before the tree is copied in.
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
import time
import urllib.request
from pathlib import Path
from typing import Iterable, Optional, Sequence

MIRROR = "https://zenodo.org/records/17988559/files"
LEVEL, CHUNK_LEVEL = 8, 1
NSIDE = 1 << LEVEL
PIXELS_PER_CHUNK = 1 << (2 * (LEVEL - CHUNK_LEVEL))
HEADER_SIZE = 128
INDEX_SIZE = PIXELS_PER_CHUNK * 4
RECORD_SIZE = 701   # SourceEntryXPsamp: ra, dec (int32), pmra, pmdec, mag (int16), fexpo (uint8), 343 x int16 flux
#: Extra radius around a field: more than a level-8 pixel's width (about 0.23 degrees), so every pixel that touches
#: the field's disc is fetched whole.
MARGIN_DEG = 0.35
#: The sky the SPCC tests calibrate, (ra, dec, radius) in degrees: the public IC 1396 field
#: (integration/test_siril_ops_cli.py FIELD_RA, FIELD_DEC) and the synthetic masters (synth.py RA0, DEC0). Siril asks
#: for about 0.35 degrees around a test image's centre.
TEST_FIELDS = ((324.745, 57.514, 1.0), (83.82, -5.39, 1.0))
MANIFEST = "loomsiril-test-fields.json"


def _spread_bits(v: int) -> int:
    out = 0
    for bit in range(LEVEL):
        out |= ((v >> bit) & 1) << (2 * bit)
    return out


def ang2pix_nest(ra_deg: float, dec_deg: float) -> int:
    """The level-8 NEST HEALPix pixel of a sky position (HEALPix C++ ``loc2pix``, NEST scheme)."""
    z = math.sin(math.radians(dec_deg))
    za = abs(z)
    tt = (math.radians(ra_deg) % (2 * math.pi)) / (math.pi / 2)
    if za <= 2.0 / 3.0:
        temp1, temp2 = NSIDE * (0.5 + tt), NSIDE * z * 0.75
        jp, jm = int(temp1 - temp2), int(temp1 + temp2)
        ifp, ifm = jp >> LEVEL, jm >> LEVEL
        face = (ifp | 4) if ifp == ifm else (ifp if ifp < ifm else ifm + 8)
        ix = jm & (NSIDE - 1)
        iy = NSIDE - (jp & (NSIDE - 1)) - 1
    else:
        ntt = min(3, int(tt))
        tp = tt - ntt
        tmp = NSIDE * math.sqrt(3 * (1 - za))
        jp = min(NSIDE - 1, int(tp * tmp))
        jm = min(NSIDE - 1, int((1 - tp) * tmp))
        if z >= 0:
            face, ix, iy = ntt, NSIDE - jm - 1, NSIDE - jp - 1
        else:
            face, ix, iy = ntt + 8, jp, jm
    return face * NSIDE * NSIDE + _spread_bits(ix) + (_spread_bits(iy) << 1)


def pixels_near(ra: float, dec: float, radius_deg: float) -> set[int]:
    """Every level-8 pixel with a point within ``radius_deg + MARGIN_DEG`` of (ra, dec), on a 0.02-degree grid."""
    reach = radius_deg + MARGIN_DEG
    step = 0.02
    n = int(math.ceil(reach / step))
    found: set[int] = set()
    for i in range(-n, n + 1):
        d = dec + i * step
        if abs(d) > 90:
            continue
        cosd = max(math.cos(math.radians(d)), 1e-6)
        for j in range(-n, n + 1):
            if math.hypot(i * step, j * step) <= reach:
                found.add(ang2pix_nest(ra + j * step / cosd, d))
    return found


def wanted_pixels(fields: Iterable[tuple[float, float, float]] = TEST_FIELDS) -> dict[int, list[int]]:
    """chunk -> sorted level-8 pixels near ``fields``."""
    out: dict[int, set[int]] = {}
    for ra, dec, radius in fields:
        for p in pixels_near(ra, dec, radius):
            out.setdefault(p // PIXELS_PER_CHUNK, set()).add(p)
    return {chunk: sorted(pixels) for chunk, pixels in sorted(out.items())}


def _fetch(url: str, start: int, length: int, attempts: int = 10) -> bytes:
    """``length`` bytes of ``url`` from ``start``, retried with a growing pause (the mirror drops many requests)."""
    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{start + length - 1}"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if resp.status == 206 and len(data) == length:
                return data
            last = RuntimeError(f"HTTP {resp.status}, {len(data)} of {length} bytes")
        except Exception as exc:  # noqa: BLE001 - every network failure is retried the same way
            last = exc
        print(f"gaia_xpsamp: {url} bytes {start}+{length} failed ({last}); retrying", file=sys.stderr, flush=True)
        time.sleep(min(60, 5 * 2 ** attempt))
    raise RuntimeError(f"could not fetch bytes {start}+{length} of {url}: {last}")


def _record_pixel(record: bytes) -> int:
    ra_scaled, dec_scaled = struct.unpack_from("<ii", record)
    scale = 360.0 / 2147483647
    return ang2pix_nest(ra_scaled * scale, dec_scaled * scale)


def chunk_name(chunk: int) -> str:
    return f"siril_cat{CHUNK_LEVEL}_healpix{LEVEL}_xpsamp_{chunk}.dat"


def _write_chunk(folder: Path, chunk: int, pixels: Sequence[int], mirror: str) -> int:
    """Writes the chunk file holding the stars of ``pixels``; returns the number of stars."""
    url = f"{mirror}/{chunk_name(chunk)}"
    head = _fetch(url, 0, HEADER_SIZE + INDEX_SIZE)
    first = struct.unpack_from("<I", head, 57)[0]   # title (48), 5 one-byte fields, chunk_healpix (4)
    if head[49] != LEVEL or head[52] != CHUNK_LEVEL or first != chunk * PIXELS_PER_CHUNK:
        raise RuntimeError(f"{url}: unexpected header (level {head[49]}, chunk level {head[52]}, first {first})")
    index = struct.unpack_from(f"<{PIXELS_PER_CHUNK}I", head, HEADER_SIZE)

    def begin(q: int) -> int:   # the first record of local pixel q
        return 0 if q == 0 else index[q - 1]

    local = [p - first for p in pixels]
    # one request for the whole span (the pixels near a field lie close together in NEST order)
    lo, hi = begin(local[0]), index[local[-1]]
    span = _fetch(url, HEADER_SIZE + INDEX_SIZE + lo * RECORD_SIZE, (hi - lo) * RECORD_SIZE) if hi > lo else b""
    counts = [0] * PIXELS_PER_CHUNK
    records = bytearray()
    for q in local:
        counts[q] = index[q] - begin(q)
        part = span[(begin(q) - lo) * RECORD_SIZE:(index[q] - lo) * RECORD_SIZE]
        for k in range(counts[q]):   # each record must lie in its pixel (a wrong offset would give other stars)
            got = _record_pixel(part[k * RECORD_SIZE:(k + 1) * RECORD_SIZE]) - first
            if got != q:
                raise RuntimeError(f"{url}: a record of pixel {q} lies in pixel {got}")
        records += part
    cumulative, total = [], 0
    for c in counts:
        total += c
        cumulative.append(total)
    tmp = folder / (chunk_name(chunk) + ".part")
    tmp.write_bytes(head[:HEADER_SIZE] + struct.pack(f"<{PIXELS_PER_CHUNK}I", *cumulative) + bytes(records))
    tmp.replace(folder / chunk_name(chunk))
    return total


def default_folder() -> Path:
    """The local photometric catalogue folder Siril 1.4.4 reads by default (``core.catalogue_gaia_photo``)."""
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(data) / "siril" / "gaia_photometric.dat"


def covered(folder: Optional[Path] = None, fields: Sequence[tuple[float, float, float]] = TEST_FIELDS) -> bool:
    """True when ``folder`` holds the stars of every pixel near ``fields`` (written by ``install``)."""
    folder = folder or default_folder()
    try:
        have = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return all(set(pixels) <= set(have.get(str(chunk), [])) and (folder / chunk_name(chunk)).is_file()
               for chunk, pixels in wanted_pixels(fields).items())


def install(folder: Optional[Path] = None, fields: Sequence[tuple[float, float, float]] = TEST_FIELDS,
            mirror: str = MIRROR) -> Path:
    """Writes the catalogue of ``fields`` into ``folder`` (fetched from ``mirror``) and returns the folder."""
    folder = folder or default_folder()
    folder.mkdir(parents=True, exist_ok=True)
    wanted = wanted_pixels(fields)
    for chunk, pixels in wanted.items():
        stars = _write_chunk(folder, chunk, pixels, mirror)
        print(f"gaia_xpsamp: {chunk_name(chunk)}: {len(pixels)} pixels, {stars} stars", flush=True)
    (folder / MANIFEST).write_text(json.dumps({str(c): p for c, p in wanted.items()}), encoding="utf-8")
    return folder


if __name__ == "__main__":
    print(install(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
