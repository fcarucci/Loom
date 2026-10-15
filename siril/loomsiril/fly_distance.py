"""The distance the Fly-Through dialog shows before anything is rendered: the render's own lookup, run on its own.

``resolve`` is pure (no Tk, no files of its own): it takes the target, the Gaia source and the typed distance and returns
a :class:`DistanceResult`. It reuses the pipeline's lookup (``pipeline._find_distance``: a galaxy is a fixed backdrop, a
typed distance wins, else the target's ionising cluster from Gaia, else the bright star that lights it) and the Gaia
fetch of gaia.py, so the dialog and the render cannot disagree. The dialog runs it on a worker thread and passes the
answer on to the Draft and Render options (``found_distance``), so the render uses exactly the value it showed.

Gaia ``cache``: gaia.sources already goes online when the cache has no file for the field. A cache file that exists
but holds nothing useful here gave "no distance" silently; the pipeline's lookup then asks Gaia online once (when the
network is up) and says so in the result's ``note``. That fallback lives in ``pipeline._find_distance``, so the CLI, the
Draft, the Render and this preview all do it.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Optional

from loomsiril import gaia, pipeline

__all__ = ["CACHE_EMPTY_NOTE", "DEFAULT_FIELD_RADIUS", "DistanceResult", "field_radius", "immediate", "parse_typed", "resolve"]

#: The field radius (deg) used when the dialog does not know the image's (the render uses the solved field's).
DEFAULT_FIELD_RADIUS = 0.5
CACHE_EMPTY_NOTE = pipeline.CACHE_EMPTY_NOTE
_ONE_LOOKUP = threading.Lock()


@dataclass(frozen=True)
class DistanceResult:
    """``distance`` in pc (inf: a galaxy; None: none found); ``code`` "typed", "galaxy", "cluster" or "star" ("" when
    none); ``source`` the same in words ("cluster of 37 stars, Gaia"); ``reason`` why none was found (plain language);
    ``note`` an extra fact to show ("Gaia: cache empty here, used online")."""

    distance: Optional[float]
    code: str = ""
    source: str = ""
    reason: str = ""
    note: str = ""

    @property
    def found_by_lookup(self) -> bool:
        """True when Gaia found it (a cluster or a star): the value the render must be told, since it is not typed."""
        return self.code in ("cluster", "star") and self.distance is not None


class _Cancelled(Exception):
    """The lookup was superseded (its cancel check turned true)."""


def parse_typed(text: Any) -> tuple[Optional[float], str]:
    """The Distance box as ``(value, problem)``: blank is ``(None, "")`` (auto); a positive finite number is the value;
    anything else is ``(None, "<what is wrong>")``."""
    if text is None or (isinstance(text, str) and not text.strip()):
        return None, ""
    try:
        v = float(str(text).strip().replace(",", "")) if not isinstance(text, (int, float)) else float(text)
    except ValueError:
        return None, f"the typed distance {str(text).strip()!r} is not a number"
    if not math.isfinite(v) or v <= 0:
        return None, f"the typed distance {str(text).strip()!r} is not a positive number"
    return v, ""


def immediate(target_type: str, typed: Any) -> Optional[DistanceResult]:
    """The answers that need no Gaia (a galaxy, a typed distance, a bad typed value), or None when Gaia must be asked.

    The galaxy comes first, as in the pipeline: a galaxy is a fixed backdrop whatever was typed."""
    if target_type == "galaxy":
        return DistanceResult(math.inf, "galaxy", pipeline.distance_label("galaxy"))
    value, problem = parse_typed(typed)
    if problem:
        return DistanceResult(None, reason=problem)
    if value is not None:
        return DistanceResult(value, "typed", "typed")
    return None


def field_radius(wcs: Mapping[str, Any], width: int, height: int) -> float:
    """The image's field radius in degrees (the centre to the farthest corner): what the render searches a cluster in."""
    return pipeline._field(wcs, width, height)[1]


def resolve(target: Optional[Mapping[str, Any]], target_type: str = "nebula", gaia_source: str = "cache",
            typed: Any = None, field_radius: Optional[float] = None,
            fetch_text: Optional[Callable[[str], str]] = None,
            is_cancelled: Optional[Callable[[], bool]] = None) -> DistanceResult:
    """The distance the render would use for this target, with where it came from.

    ``target``: ``{"ra", "dec"}`` in degrees (None or invalid: no target to look round). ``gaia_source``: "cache",
    "online" or "tsv:PATH". ``typed``: the Distance box's text or number. ``field_radius``: the image's field radius in
    degrees (the cluster search radius of a target with no catalogued size). ``fetch_text`` replaces the online fetch
    (tests never reach the network). ``is_cancelled`` is checked at each stage; a cancelled lookup raises nothing and
    returns a result with no distance and the reason "cancelled". Never raises for a Gaia or network failure: that is
    the result's ``reason``. The lookup itself (and the cache-empty -> Gaia online fallback) is
    ``pipeline._find_distance``, the one the CLI, the Draft and the Render use; one lookup runs at a time in this
    process, so a Render started while the dialog's lookup is still running waits for it and reads what it cached.
    """
    quick = immediate(target_type, typed)
    if quick is not None:
        return quick
    try:
        ra, dec = float(target["ra"]), float(target["dec"])      # type: ignore[index]
    except (TypeError, KeyError, ValueError):
        return DistanceResult(None, reason="there is no target position (fill in the target RA and Dec)")
    if not (math.isfinite(ra) and math.isfinite(dec) and 0 <= ra < 360 and -90 <= dec <= 90):
        return DistanceResult(None, reason="the target RA and Dec are not a position on the sky")
    radius = field_radius if field_radius and field_radius > 0 else DEFAULT_FIELD_RADIUS
    cancelled = is_cancelled or (lambda: False)

    def progress(_stage: str, _done: int, _total: int) -> None:
        if cancelled():
            raise _Cancelled()

    tgt = {"ra": ra, "dec": dec}
    try:
        kind = pipeline._gaia_kind(gaia_source)[0]
    except ValueError as exc:
        return DistanceResult(None, reason=str(exc))
    job = pipeline.FlyJob(starless=[], stars=[], wcs={}, target=tgt, target_type="nebula", distance=None, gaia=gaia_source)
    ctx = pipeline.RunContext(progress, cancelled, fetch_text)
    try:
        with _ONE_LOOKUP:                                       # a second lookup waits and then finds the cache filled
            if cancelled():
                return DistanceResult(None, reason="cancelled")
            sources: list[dict] = []
            if kind == "tsv":
                sources = pipeline._gaia_sources(job, gaia.GaiaQuery.from_centre(tgt, radius, pipeline.GAIA_MAG_LIMIT), ctx)
            found = pipeline._find_distance(job, sources, tgt, radius, ctx)
    except _Cancelled:
        return DistanceResult(None, reason="cancelled")
    except (gaia.GaiaError, OSError) as exc:
        return DistanceResult(None, reason=pipeline.gaia_failure(exc))
    if cancelled():
        return DistanceResult(None, reason="cancelled")
    if found.distance is None:
        return DistanceResult(None, reason=found.reason)
    return DistanceResult(found.distance, found.source, found.label, note=found.note)
