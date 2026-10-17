"""Local Gaia data for the Fly-Through: a folder of Loom cone files, and Siril's own HEALpix catalogue.

Two sources, both read from disk and both optional (the default stays Gaia online):

* **A folder** (:func:`folder_sources`): Loom's own cone files, ``.csv`` or ``.tsv``, the format the online cache already
  writes and ``gaia.read_tsv`` already reads (columns ``ra dec plx pmra pmdec G BP RP``; see the README). Returns the
  records of a query, or None when nothing in the folder covers the field, so the caller falls back to the online path.
* **Siril's local catalogue** (:class:`SirilCatalogue`, :func:`siril_cone`): ``siril_cat_healpix8_astro.dat``, Siril's
  astrometry extract (Siril 1.4, https://ar5iv.labs.arxiv.org/html/2501.12466). It holds position, proper motion, Teff
  and G, but no parallax and no BP/RP, so its records carry NaN there. Fly-Through places and times every star by its
  parallax, so these records cannot drive it: the reader is here and tested, but the dialog and the job refuse it with
  that reason (see ``SIRIL_NO_PARALLAX``).

The HEALpix cone (:func:`ang2pix_nest`, :func:`disc_pixels`) is a small numpy implementation of the nested scheme; no
healpy is needed.
"""

from __future__ import annotations

import functools
import math
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

import numpy as np

from loomsiril import gaia
from loomsiril.core import separation

__all__ = ["SIRIL_CATALOGUE_NAME", "SIRIL_NO_PARALLAX", "SirilCatalogue", "ang2pix_nest", "disc_pixels", "folder_sources",
           "folder_status", "siril_catalogue_path", "siril_cone", "siril_note"]

SIRIL_CATALOGUE_NAME = "siril_cat_healpix8_astro.dat"
SIRIL_NO_PARALLAX = ("Siril's local Gaia catalogue holds no parallax, which Fly-Through needs to place and time every star: "
                     "use Gaia online, or a folder of Loom Gaia files")

_NAMED = re.compile(r"^ra(?P<ra>[0-9.]+)_dec(?P<dec>-?[0-9.]+)_r(?P<r>[0-9.]+)_g(?P<g>[0-9.]+)\.(?:csv|tsv)$", re.IGNORECASE)
_SHORT_HEADER = "ra,dec,plx,pmra,pmdec,G,BP,RP"
#: A deeper query than a cone file's limit, by up to this many magnitudes, is still covered (cache_key rounds g to 0.1).
_G_SLACK = 0.05


# ---------------------------------------------------------------- HEALpix (nested)

def _spread(v: np.ndarray) -> np.ndarray:
    """The bits of ``v`` moved to the even bit positions (the nested scheme interleaves ix and iy)."""
    v = v.astype(np.int64)
    out = np.zeros_like(v)
    for b in range(16):
        out |= ((v >> b) & 1) << (2 * b)
    return out


def ang2pix_nest(nside: int, ra_deg: Any, dec_deg: Any) -> np.ndarray:
    """The nested HEALpix pixel of each (RA, Dec) in degrees, for a power-of-two ``nside``."""
    ra = np.asarray(ra_deg, dtype=float) % 360.0
    z = np.sin(np.radians(np.asarray(dec_deg, dtype=float)))
    za = np.abs(z)
    tt = ra / 90.0                                           # phi / (pi/2), in [0, 4)
    equatorial = za <= 2.0 / 3.0
    # equatorial belt
    t1 = nside * (0.5 + tt)
    t2 = nside * z * 0.75
    jp = np.floor(t1 - t2).astype(np.int64)
    jm = np.floor(t1 + t2).astype(np.int64)
    ifp, ifm = jp // nside, jm // nside
    face_eq = np.where(ifp == ifm, np.where(ifp == 4, 4, ifp + 4), np.where(ifp < ifm, ifp, ifm + 8))
    ix_eq = jm & (nside - 1)
    iy_eq = nside - (jp & (nside - 1)) - 1
    # polar caps
    ntt = np.minimum(np.floor(tt).astype(np.int64), 3)
    tp = tt - ntt
    tmp = nside * np.sqrt(3.0 * (1.0 - za))
    jp_c = np.minimum(np.floor(tp * tmp).astype(np.int64), nside - 1)
    jm_c = np.minimum(np.floor((1.0 - tp) * tmp).astype(np.int64), nside - 1)
    north = z >= 0
    face_c = np.where(north, ntt, ntt + 8)
    ix_c = np.where(north, nside - jm_c - 1, jp_c)
    iy_c = np.where(north, nside - jp_c - 1, jm_c)
    face = np.where(equatorial, face_eq, face_c)
    ix = np.where(equatorial, ix_eq, ix_c)
    iy = np.where(equatorial, iy_eq, iy_c)
    return face * nside * nside + _spread(ix) + 2 * _spread(iy)


def disc_pixels(level: int, ra_deg: float, dec_deg: float, radius_deg: float) -> np.ndarray:
    """The nested pixels (at ``level``) that a disc touches, sorted.

    The disc is sampled on rings every eighth of a pixel (plus one sample step of margin); a pixel is missed only if the
    disc clips it by less than that. The caller filters the stars it reads by their exact distance."""
    nside = 1 << level
    pix_deg = math.degrees(math.sqrt(4 * math.pi / (12 * nside * nside)))
    step = pix_deg / 8
    dec0 = math.radians(dec_deg)
    out = [ang2pix_nest(nside, np.array([ra_deg]), np.array([dec_deg]))]
    reach = radius_deg + step
    for d in np.arange(step, reach + step, step):
        n = max(8, int(math.ceil(2 * math.pi * math.sin(math.radians(min(d, 180.0))) / math.radians(step))))
        a = np.linspace(0, 2 * math.pi, n, endpoint=False)
        dr = math.radians(d)
        dec = np.arcsin(np.clip(math.sin(dec0) * math.cos(dr) + math.cos(dec0) * math.sin(dr) * np.cos(a), -1, 1))
        ra = ra_deg + np.degrees(np.arctan2(np.sin(a) * math.sin(dr) * math.cos(dec0),
                                            math.cos(dr) - math.sin(dec0) * np.sin(dec)))
        out.append(ang2pix_nest(nside, ra % 360.0, np.degrees(dec)))
    return np.unique(np.concatenate(out))


# ---------------------------------------------------------------- Siril's catalogue

_RECORD = np.dtype([("ra", "<i4"), ("dec", "<i4"), ("pmra", "<i2"), ("pmdec", "<i2"), ("teff", "<u2"), ("g", "<i2")])
_HEADER_BYTES = 128
_SCALE = 360.0 / (2**31 - 1)


class SirilCatalogue:
    """Siril's astrometry extract: a 128-byte header (title, then version, HEALpix level, type), one cumulative record
    count per pixel (uint32), then 16-byte records (RA, Dec as int32 scaled by 360/(2^31-1) degrees, proper motions in
    mas/yr, Teff, G x 1000), the brightest stars of each pixel first. ``ValueError`` when the file is not that."""

    def __init__(self, path: "str | os.PathLike[str]") -> None:
        self.path = str(path)
        size = os.path.getsize(self.path)
        if size < _HEADER_BYTES:
            raise ValueError(f"{self.path}: not a Siril Gaia catalogue (too short)")
        with open(self.path, "rb") as fh:
            head = fh.read(_HEADER_BYTES)
        if not head.startswith(b"Siril Gaia"):
            raise ValueError(f"{self.path}: not a Siril Gaia catalogue (title)")
        self.level = head[49]
        if not 1 <= self.level <= 13:
            raise ValueError(f"{self.path}: unsupported HEALpix level {self.level}")
        self.npix = 12 * 4 ** self.level
        self._records_at = _HEADER_BYTES + 4 * self.npix
        if size < self._records_at:
            raise ValueError(f"{self.path}: the pixel index is cut short")
        self._index = np.memmap(self.path, dtype="<u4", mode="r", offset=_HEADER_BYTES, shape=(self.npix,))
        self.count = int(self._index[-1])
        if size != self._records_at + 16 * self.count:
            raise ValueError(f"{self.path}: its size does not match its index (is the download complete?)")
        self._rec = np.memmap(self.path, dtype=_RECORD, mode="r", offset=self._records_at, shape=(self.count,))

    def pixel(self, pix: int) -> dict[str, np.ndarray]:
        """The stars of one nested pixel as arrays: ra, dec (deg), pmra, pmdec (mas/yr), G."""
        start = int(self._index[pix - 1]) if pix > 0 else 0
        r = self._rec[start:int(self._index[pix])]
        return {"ra": r["ra"] * _SCALE, "dec": r["dec"] * _SCALE, "pmra": r["pmra"].astype(float),
                "pmdec": r["pmdec"].astype(float), "G": r["g"] / 1000.0}


def siril_cone(path: "str | os.PathLike[str]", query: "gaia.GaiaQuery") -> list[dict[str, float]]:
    """The records of ``query`` (a cone and a G limit) from Siril's catalogue: ``{ra, dec, plx, pmra, pmdec, G, BP, RP}``
    with plx, BP and RP NaN, as the catalogue holds none."""
    cat = SirilCatalogue(path)
    ra0, dec0 = query.centre
    out: list[dict[str, float]] = []
    for pix in disc_pixels(cat.level, ra0, dec0, query.radius_deg):
        p = cat.pixel(int(pix))
        if not len(p["ra"]):
            continue
        d1, d0 = np.radians(p["dec"]), math.radians(dec0)
        cosd = np.sin(d1) * math.sin(d0) + np.cos(d1) * math.cos(d0) * np.cos(np.radians(p["ra"] - ra0))
        keep = (np.degrees(np.arccos(np.clip(cosd, -1, 1))) <= query.radius_deg) & (p["G"] < query.g_max) & (p["G"] > 0)
        for i in np.nonzero(keep)[0]:
            out.append({"ra": float(p["ra"][i]), "dec": float(p["dec"][i]), "plx": math.nan, "pmra": float(p["pmra"][i]),
                        "pmdec": float(p["pmdec"][i]), "G": float(p["G"][i]), "BP": math.nan, "RP": math.nan})
    return out


def siril_catalogue_path(home: "str | os.PathLike[str] | None" = None, platform: Optional[str] = None,
                         env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """Where Siril keeps its local Gaia astrometry catalogue on this machine, when the file is there (else None)."""
    home_p = Path(home) if home is not None else Path.home()
    plat = platform or sys.platform
    e = os.environ if env is None else env
    if plat == "darwin":
        base = home_p / "Library/Application Support/org.siril.Siril/siril"
    elif plat.startswith("win"):
        base = Path(e.get("LOCALAPPDATA") or home_p / "AppData/Local") / "siril"
    else:
        data = e.get("XDG_DATA_HOME", "")
        base = (Path(data) if data and os.path.isabs(data) else home_p / ".local/share") / "siril"
    path = base / SIRIL_CATALOGUE_NAME
    return path if path.is_file() else None


def siril_note(path: Optional[Path] = None) -> str:
    """The grey line under the Gaia data row about Siril's own catalogue: whether it is installed, and why it is not used."""
    found = path if path is not None else siril_catalogue_path()
    if found is None:
        return "Siril's local Gaia catalogue: not installed (Siril > Preferences > Catalogues)"
    return f"Siril's local Gaia catalogue found, not used: it has no parallax, which Fly-Through needs for every star"


# ---------------------------------------------------------------- a folder of Loom cone files

def _table_files(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.suffix.lower() in (".csv", ".tsv") and p.is_file())
    except OSError:
        return []


@functools.lru_cache(maxsize=4)
def _read_cached(path: str, mtime_ns: int, size: int) -> tuple[dict[str, float], ...]:
    return tuple(_read_table(path))


def _read_table(path: str) -> list[dict[str, float]]:
    """A cone file: tab separated (``ra dec ...``), comma separated with that short header, or VizieR's header."""
    with open(path, encoding="utf-8") as fh:
        first = fh.readline().rstrip("\r\n")
    if first.split("\t")[:1] == ["ra"] and "\t" in first:
        return gaia.read_tsv(path)
    text = Path(path).read_text(encoding="utf-8")
    if first == _SHORT_HEADER:
        text = ",".join(gaia.GAIA_ONLINE_COLUMNS) + text[len(first):]
    recs = gaia.parse_csv(text)
    if recs is None:
        raise ValueError(f"{path}: not a Gaia cone file (header must be ra,dec,plx,pmra,pmdec,G,BP,RP)")
    return recs


def _load(path: Path) -> tuple[dict[str, float], ...]:
    st = path.stat()
    return _read_cached(str(path), st.st_mtime_ns, st.st_size)


def _inside(recs: "tuple[dict[str, float], ...]", query: "gaia.GaiaQuery") -> list[dict[str, float]]:
    ra0, dec0 = query.centre
    centre = {"ra": ra0, "dec": dec0}
    out = []
    for s in recs:
        g = s["G"]
        if not (math.isfinite(s["ra"]) and math.isfinite(s["dec"]) and g < query.g_max):
            continue
        if separation(s, centre) <= query.radius_deg:
            out.append(s)
    return out


def folder_sources(query: "gaia.GaiaQuery", folder: "str | os.PathLike[str]") -> Optional[list[dict[str, float]]]:
    """The records of ``query`` from the cone files of ``folder``, or None when the folder does not cover the field.

    A file named like the online cache's (``ra100.0000_dec10.0000_r1.000_g17.6.csv``) is a cone: it covers a query whose
    cone lies inside it and whose G limit it reaches; the smallest covering one is read. Any other ``.csv`` or ``.tsv`` is a
    table: it counts when it holds a star in the query's cone, and the tables that do are merged (a star in two of them
    once). The stars returned are those inside the query's cone and under its G limit. A broken file raises ValueError."""
    root = Path(os.path.expanduser(str(folder)))
    files = _table_files(root)
    ra0, dec0 = query.centre
    centre = {"ra": ra0, "dec": dec0}
    cones: list[tuple[float, Path]] = []
    tables: list[Path] = []
    for p in files:
        m = _NAMED.match(p.name)
        if m is None:
            tables.append(p)
            continue
        c = {"ra": float(m["ra"]), "dec": float(m["dec"])}
        r, g = float(m["r"]), float(m["g"])
        if separation(c, centre) + query.radius_deg <= r + 1e-9 and g >= query.g_max - _G_SLACK:
            cones.append((r, p))
    if cones:
        return _inside(_load(min(cones)[1]), query)
    merged: dict[tuple[float, float], dict[str, float]] = {}
    for p in tables:
        hits = _inside(_load(p), query)
        for s in hits:
            merged.setdefault((round(s["ra"], 7), round(s["dec"], 7)), s)
    return list(merged.values()) if merged else None


def folder_status(folder: str) -> str:
    """The grey line under the folder box: what the folder holds, in words (no file is read)."""
    text = (folder or "").strip()
    if not text:
        return "Browse... for a folder of Gaia cone files"
    root = Path(os.path.expanduser(text))
    if not root.is_dir():
        return f"The Gaia folder is not there: {text}"
    files = _table_files(root)
    if not files:
        return "no .csv or .tsv files in this folder: Gaia online is used"
    cones = sum(1 for p in files if _NAMED.match(p.name))
    tables = len(files) - cones
    parts = ([f"{cones} cone file{'s' if cones != 1 else ''}"] if cones else []) + \
            ([f"{tables} table{'s' if tables != 1 else ''}"] if tables else [])
    return f"{' and '.join(parts)}; a field they do not cover is asked of Gaia online"
