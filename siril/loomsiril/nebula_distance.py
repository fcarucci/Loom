"""Published nebula distance estimates from SIMBAD's mesDistance table.

Match a nebula within two arcminutes of the target, never a field star or a
cluster. Ambiguous matches are refused. Use the newest referenced, positive,
non-limit measurement in pc/kpc/Mpc; an empty/failed lookup leaves Gaia as the
fallback. No object-specific distances are embedded here.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from loomsiril import gaia
from loomsiril.core import separation

URL = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
RADIUS = 2 / 60
MAX_ROWS = 200
TYPES = ("PN", "HII", "RNe", "SNR", "Neb", "EmO", "MoC", "DNe", "BNe", "ISM", "CGb")
COLUMNS = ("main_id", "ra", "dec", "otype", "dist", "unit", "method", "bibcode", "qual")
_log = logging.getLogger(__name__)


def query_url(target: Mapping[str, Any]) -> str:
    ra, dec = float(target["ra"]), float(target["dec"])
    if not (math.isfinite(ra) and 0 <= ra < 360 and math.isfinite(dec) and -90 <= dec <= 90):
        raise ValueError("invalid nebula position")
    cols = "b.main_id,b.ra,b.dec,b.otype,d.dist,d.unit,d.method,d.bibcode,d.qual"
    types = ",".join(f"'{t}'" for t in TYPES)
    query = (f"SELECT {cols} FROM basic AS b JOIN mesDistance AS d ON b.oid=d.oidref "
             f"WHERE b.otype IN ({types}) AND "
             f"1=CONTAINS(POINT('ICRS',b.ra,b.dec),CIRCLE('ICRS',{ra},{dec},{RADIUS}))")
    return URL + "?" + urlencode(dict(REQUEST="doQuery", LANG="ADQL", FORMAT="json", MAXREC=MAX_ROWS, QUERY=query))


def parse(text: Any, target: Mapping[str, Any]) -> dict | None:
    """Validate TAP JSON, match one nebula and choose its newest usable estimate."""
    try:
        table = json.loads(text)
        names = [m["name"] for m in table["metadata"]]
        if not all(c in names for c in COLUMNS) or len(table["data"]) >= MAX_ROWS:
            return None
        records, objects = [], set()
        for row in table["data"]:
            r = dict(zip(names, row))
            if not (isinstance(r["ra"], (int, float)) and math.isfinite(r["ra"]) and 0 <= r["ra"] < 360
                    and isinstance(r["dec"], (int, float)) and math.isfinite(r["dec"]) and -90 <= r["dec"] <= 90):
                continue
            if r["otype"].strip() not in TYPES or separation(r, target) > RADIUS:
                continue
            objects.add(r["main_id"].strip())
            factor = {"pc": 1, "kpc": 1000, "Mpc": 1000000}.get((r["unit"] or "").strip())
            if factor is None or type(r["dist"]) not in (int, float):
                continue
            distance = float(r["dist"]) * factor
            bib = (r["bibcode"] or "").strip()
            if not math.isfinite(distance) or distance <= 0 or not bib[:4].isdigit():
                continue
            if (r["qual"] or "").strip() not in ("", "A", "B", "C"):
                continue
            records.append(dict(distance=distance, id=r["main_id"].strip(), bibcode=bib,
                                method=(r["method"] or "").strip()))
        if len(objects) != 1 or not records:
            return None
        # Same deterministic rule as Fly.parseNebulaDistance. Never average different methods.
        return sorted(records, key=lambda r: (-int(r["bibcode"][:4]), r["bibcode"], r["distance"]))[0]
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None


def lookup(target: Mapping[str, Any], fetch_text: Callable[[str], str] | None = None) -> dict | None:
    """A network miss is nonfatal; the caller can still infer a distance from Gaia."""
    try:
        url = query_url(target)
        text = fetch_text(url) if fetch_text else gaia.fetch_text(url, first_byte_s=8, stall_s=8, total_s=12)
        found = parse(text, target)
    except (gaia.GaiaError, OSError, ValueError) as exc:
        _log.info("SIMBAD nebula distance lookup failed: %s; trying Gaia", exc)
        return None
    if found:
        _log.info("SIMBAD %s: estimate %.1f pc (%s)", found["id"], found["distance"], found["bibcode"])
    else:
        _log.info("No unambiguous SIMBAD nebula distance estimate; trying Gaia")
    return found


def label(record: Mapping[str, Any]) -> str:
    return f"SIMBAD {record['id']}, {record['bibcode']}"
