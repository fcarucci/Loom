"""Finding what an image shows by NAME, online: the port of Loom 0.4.2's name resolver (Fly.parseSesame, nameQueries,
viewIdFromPath, imageTitle; Sky.fetchText, resolvedNames, resolveName, namedInField).

The names come from the user's own files (an image file's name and folders, the OBJECT keyword) and are asked of CDS
Sesame (SIMBAD, NED, VizieR). No object name is written in this code. A hit is accepted ONLY when it lies inside the
solved field. Nothing here touches Tk; the dialog calls :func:`find_named` on a worker thread. Offline is silent: a
failed request is "no answer" (and is not remembered, so a later try can work).

Deviations from the JS (documented in docs/PARITY.md): the Siril port has no NGC/IC catalogue, so ``named_in_field``
takes an optional ``local`` callable for the "catalogue first" step and Sesame is the only source by default; a
transport failure is not cached (the JS keeps it as a miss); the request has a short timeout (``NAME_TIMEOUT``), the
JS the shared transfer watch; a typed name is checked against the field with :func:`check_typed` (the PixInsight Object
box does not check the field, the Siril one does).
"""

from __future__ import annotations

import logging
import math
import re
import threading
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

from loomsiril import core

__all__ = [
    "NAME_QUERY_MAX", "NAME_RESOLVER_URL", "NAME_TIMEOUT", "NameContext", "NameResult", "check_typed", "fetch_text",
    "find_named", "image_title", "name_queries", "named_in_field", "parse_sesame", "resolve_name", "resolved_names",
    "sky_field", "view_id_from_path",
]

#: CDS Sesame: resolves any name or catalogue id SIMBAD, NED or VizieR know (Sh2-132, LBN 473, vdB 152...) to a J2000 position.
NAME_RESOLVER_URL = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/SNV?"
NAME_QUERY_MAX = 6          # names tried online for one image
NAME_TIMEOUT = 8.0          # s: connect and read limit of one request (a dialog helper, not a render step)

_log = logging.getLogger("loomsiril.skyname")


def _parse_float(text: str) -> float:
    """JS ``parseFloat``: the longest numeric prefix, NaN when there is none."""
    m = re.match(r"\s*[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", text)
    return float(m.group(0)) if m else math.nan


def parse_sesame(text: Any) -> Optional[dict[str, Any]]:
    """``Fly.parseSesame``: Sesame's answer as ``{id, ra, dec}`` (degrees), or None when it found nothing."""
    t = "" if text is None else str(text)
    p = re.search(r"^%J\s+(-?[\d.]+)\s+([+-]?[\d.]+)", t, re.M)
    n = re.search(r"^#\s*([^\t\r\n]+)", t, re.M)
    if not p or not n:
        return None
    ra, dec = _parse_float(p.group(1)), _parse_float(p.group(2))
    if not (math.isfinite(ra) and math.isfinite(dec)):
        return None
    return {"id": n.group(1).strip(), "ra": ra, "dec": dec}


_ID_LIKE = re.compile(r"[A-Za-z]{1,5}-?[0-9]{1,5}(?:-[0-9]{1,5})?[A-Za-z]?")


def name_queries(path: Any) -> list[str]:
    """``Fly.nameQueries``: what a path's names might be called, best first, to ask a name resolver.

    Catalogue-looking words (Sh2-132, LBN473) of the file and then each folder (the last four path parts, innermost
    first), then runs of two words, then single words of four letters or more; no repeats (any case); at most
    NAME_QUERY_MAX.
    """
    parts = [p for p in re.split(r"[/\\]", "" if path is None else str(path)) if p][-4:][::-1]
    ids: list[str] = []
    runs: list[str] = []
    singles: list[str] = []
    for i, part in enumerate(parts):
        base = re.sub(r"\.[^.]*$", "", part) if i == 0 else part
        ids.extend(w for w in re.split(r"[\s_]+", base) if _ID_LIKE.fullmatch(w))
        words = [w for w in re.split(r"[^A-Za-z0-9']+", base) if w]
        runs.extend(f"{words[k]} {words[k + 1]}" for k in range(len(words) - 1))
        singles.extend(w for w in words if len(w) >= 4 and not re.fullmatch(r"[0-9]+", w))
    seen: set[str] = set()
    out: list[str] = []
    for q in ids + runs + singles:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:NAME_QUERY_MAX]


def view_id_from_path(path: Any) -> str:
    """``Fly.viewIdFromPath``: a view id from an image file's name (letters, digits, underscores; never starting with a
    digit), or "" when it has none. (Siril views have no ids: nothing in the Siril port calls this.)"""
    name = re.split(r"[/\\]", "" if path is None else str(path))[-1]
    ident = re.sub(r"^_+|_+$", "", re.sub(r"[^A-Za-z0-9_]+", "_", re.sub(r"\.[^.]*$", "", name)))
    return "_" + ident if ident and ident[0].isdigit() else ident


def image_title(file_path: Any, view_id: str) -> str:
    """``Fly.imageTitle``: how an image is called to the user: its file's name when it has one and its view is only
    PixInsight's ``Image<number>``, else the view id."""
    file = re.split(r"[/\\]", "" if file_path is None else str(file_path))[-1]
    return file if file and re.fullmatch(r"Image[0-9]+", view_id or "") else view_id


# ---------------------------------------------------------------- the request

def fetch_text(url: str, what: str = "Name lookup") -> Optional[str]:
    """``Sky.fetchText``: the body of a GET, or None on any failure (logged with ``what``). Replaceable, so the suite
    never touches the network."""
    try:
        with urllib.request.urlopen(url, timeout=NAME_TIMEOUT) as resp:  # noqa: S310 (https, a fixed host)
            status = getattr(resp, "status", 200)
            if status != 200:
                _log.warning("%s: HTTP %s", what, status)
                return None
            return resp.read(1 << 20).decode("utf-8", errors="replace")
    except Exception as exc:  # offline, DNS, timeout, TLS: no answer, never an error in the dialog
        _log.warning("%s: %s", what, exc)
        return None


#: Names already asked of the resolver this session (a miss is kept too), by lower-case query.
resolved_names: dict[str, Optional[dict[str, Any]]] = {}
_lock = threading.Lock()


def _resolve(query: str) -> tuple[Optional[dict[str, Any]], bool]:
    """``(hit, failed)``: the answer for one name, and whether the request itself failed (then nothing is kept)."""
    q = (query or "").strip()
    key = q.lower()
    if not q:
        return None, False
    with _lock:
        if key in resolved_names:
            return resolved_names[key], False
    text = fetch_text(NAME_RESOLVER_URL + quote(q, safe="-_.!~*'()"), "Name lookup")
    if text is None:
        return None, True
    r = parse_sesame(text)
    entry = {"id": r["id"], "ra": r["ra"], "dec": r["dec"], "diameter": 0, "name": ""} if r else None
    with _lock:
        resolved_names[key] = entry
    return entry, False


def resolve_name(query: Any) -> Optional[dict[str, Any]]:
    """``Sky.resolveName``: an object by name from CDS Sesame, ``{id, ra, dec, diameter: 0, name: ""}`` -- or None
    (unknown, or offline)."""
    return _resolve("" if query is None else str(query))[0]


# ---------------------------------------------------------------- the field

def sky_field(wcs: Mapping[str, Any], width: int, height: int) -> dict[str, Any]:
    """``Sky.field``: ``{centre: {ra, dec}, radius_deg}`` of an image: its middle and the farthest corner from it."""
    centre = core.tan_unproject(width / 2, height / 2, dict(wcs))
    r = 0.0
    for x, y in ((0, 0), (width, 0), (0, height), (width, height)):
        r = max(r, core.separation(centre, core.tan_unproject(x, y, dict(wcs))))
    return {"centre": centre, "radius_deg": r}


def _inside(hit: Mapping[str, Any], fld: Mapping[str, Any]) -> bool:
    return core.separation(dict(hit), dict(fld["centre"])) <= fld["radius_deg"]


@dataclass
class NameResult:
    """What a lookup found. ``hit``: the object inside the field (``{id, ra, dec, diameter, name}``) or None;
    ``query``: the name that found it; ``offline``: a request failed and nothing was found; ``outside``: the hits that
    lie outside the field, ``(id, separation in degrees)``; ``how``: where the name came from (find_named)."""

    hit: Optional[dict[str, Any]] = None
    query: str = ""
    offline: bool = False
    outside: list[tuple[str, float]] = field(default_factory=list)
    how: str = ""


def _scan(path: Any, fld: Mapping[str, Any], local: Optional[Callable[[Any], Optional[dict]]] = None) -> NameResult:
    out = NameResult()
    if local is not None:
        got = local(path)
        if got and _inside(got, fld):
            out.hit, out.query = got, str(got.get("id", ""))
            return out
    for q in name_queries(path):
        hit, failed = _resolve(q)
        if failed:                      # no network: asking the next name would wait as long again
            out.offline = True
            return out
        if hit is None:
            continue
        if _inside(hit, fld):
            out.hit, out.query = hit, q
            return out
        out.outside.append((hit["id"], core.separation(hit, dict(fld["centre"]))))
    return out


def named_in_field(path: Any, fld: Mapping[str, Any], local: Optional[Callable[[Any], Optional[dict]]] = None
                   ) -> Optional[dict[str, Any]]:
    """``Sky.namedInField``: the object an image's file or folder names (``path``) when the solved field holds it:
    ``local`` (a catalogue, optional) first, else the first the online name resolver knows. None when nothing is named,
    it lies elsewhere, or the network is down."""
    return _scan(path, fld, local).hit


def check_typed(query: str, fld: Optional[Mapping[str, Any]]) -> NameResult:
    """A name typed in the Object box: resolved online, accepted only inside the solved field. ``outside`` holds the
    refused hit and how far it is; ``offline`` is set when the request failed; no hit and neither: unknown name."""
    out = NameResult()
    hit, failed = _resolve(query)
    if failed:
        out.offline = True
    elif hit is not None and fld is not None:
        if _inside(hit, fld):
            out.hit, out.query = hit, query.strip()
        else:
            out.outside.append((hit["id"], core.separation(hit, dict(fld["centre"]))))
    elif hit is not None:
        out.outside.append((hit["id"], math.inf))
    return out


# ---------------------------------------------------------------- what the dialog asks

@dataclass
class NameContext:
    """What the Fly-Through dialog knows about the open image: its solved ``field`` (None when unsolved), the OBJECT
    keyword of its header, the path of its file (Siril's ``get_image_filename``; "" when unsaved) and its pixel grid."""

    field: Optional[dict[str, Any]] = None
    header_object: str = ""
    open_path: str = ""
    open_grid: Optional[tuple[int, int, int]] = None    # the open image's (width, height, channels), for the Image file


def _where(path: str, query: str) -> str:
    """"file" when ``query`` is spelled in the file's own name, else "folder"."""
    def squash(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    base = re.sub(r"\.[^.]*$", "", re.split(r"[/\\]", path)[-1])
    return "file" if squash(query) and squash(query) in squash(base) else "folder"


def find_named(sources: Sequence[tuple[str, str]], fld: Optional[Mapping[str, Any]]) -> NameResult:
    """The first object any of ``sources`` names that lies inside ``fld``. ``sources``: ``(kind, text)`` with kind
    ``"file"`` (the Image file's path), ``"open"`` (the open image's path) or ``"header"`` (an OBJECT keyword); see
    fly_model.name_sources. ``how`` says which: "the file name", "the folder name", "the open image's file name",
    "the image header's OBJECT". Stops at the first network failure."""
    final = NameResult()
    if fld is None:
        return final
    for kind, text in sources:
        if kind == "header":
            r = check_typed(text, fld)
            how = "the image header's OBJECT"
        else:
            r = _scan(text, fld)
            how = f"the {_where(text, r.query)} name" if r.hit else ""
            if kind == "open" and r.hit:
                how = "the open image's " + how[4:]
        final.outside.extend(r.outside)
        if r.hit:
            r.how = how
            r.outside = final.outside
            return r
        if r.offline:
            final.offline = True
            return final
    return final
