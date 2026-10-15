"""Frame Selector: a frame's altitude above the horizon, read from its FITS header or computed from it.

New code, with no JavaScript to port. It replaces the Altitude column (SubframeSelector column 20) that the
PixInsight Frame Selector reads from SubframeSelector. Siril gives a script no per-frame altitude, so the altitude
comes from the header. The airmass correction in ``loomsiril.frames_flags`` takes this value.

Keyword order in :func:`altitude_from_header`; the first usable number wins:

1. ``AIRMASS`` >= 1: altitude = asin(1 / AIRMASS), in degrees.
2. ``CENTALT``, then ``OBJCTALT``: a number in (0, 90].
3. Computed by :func:`altitude_deg` from the pointing and the time: ``RA`` and ``DEC`` (degrees), else
   ``OBJCTRA`` (hours, sexagesimal) and ``OBJCTDEC`` (degrees, sexagesimal); ``DATE-OBS`` (UTC, through
   :func:`loomsiril.frames_flags.obs_time`); site ``SITELAT`` and ``SITELONG`` (degrees, east positive; a number or
   sexagesimal). The result is in (-90, 90].
4. Otherwise None. An unparsable value is skipped, never raised.

Accuracy: :func:`altitude_deg` applies no precession, nutation, aberration or refraction. For J2000 coordinates in
the 2020s it is within about 0.3 degree of the true altitude, which the airmass correction tolerates.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any, Optional

from loomsiril.frames_flags import obs_time

Keyword = Callable[[str], Any]

_J2000_JD = 2451545.0
_UNIX_EPOCH_JD = 2440587.5
_MS_PER_DAY = 86400000.0

_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\Z")
_SEXAGESIMAL = re.compile(r"([+-]?)(\d+)(?:[ :](\d+)(?:[ :](\d+(?:\.\d*)?|\.\d+))?)?\Z")


def _bare(value: Any) -> Optional[str]:
    """A keyword's value as stripped text: single quotes and surrounding blanks removed, empty as None."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace("'", "").strip()
    return text or None


def _number(value: Any) -> Optional[float]:
    """A keyword's value as a finite number, or None when it is not one."""
    text = _bare(value)
    if text is None or _NUMBER.match(text) is None:
        return None
    x = float(text)
    return x if math.isfinite(x) else None


def parse_sexagesimal(text: Any, hours: bool) -> Optional[float]:
    """A sexagesimal angle as degrees, or None.

    ``hours`` says the field is in hours (``"00 42 44.3"`` is 10.6833 degrees); otherwise it is degrees
    (``"+41:16:09"`` is 41.2692). Fields are separated by a blank or a colon; a sign applies to the whole value; the
    minutes and seconds must be below 60. A plain decimal number is read in the same unit as the field. Anything else
    is None.
    """
    text = _bare(text)
    if text is None:
        return None
    if _NUMBER.match(text) is not None:
        value = float(text)
        if not math.isfinite(value):
            return None
        return value * 15.0 if hours else value
    m = _SEXAGESIMAL.match(text)
    if m is None:
        return None
    sign = -1.0 if m.group(1) == "-" else 1.0
    whole = int(m.group(2))
    minutes = int(m.group(3)) if m.group(3) is not None else 0
    seconds = float(m.group(4)) if m.group(4) is not None else 0.0
    if minutes >= 60 or seconds >= 60:
        return None
    value = sign * (whole + minutes / 60.0 + seconds / 3600.0)
    return value * 15.0 if hours else value


def _lookup(keyword: Keyword, name: str) -> Any:
    """The keyword's value, or None when the lookup raises."""
    try:
        return keyword(name)
    except Exception:
        return None


def _pointing_degrees(keyword: Keyword, name: str, fallback: str, hours_fallback: bool) -> Optional[float]:
    """A pointing coordinate in degrees: ``name`` as a number, else ``fallback`` as sexagesimal (hours when asked)."""
    direct = _number(_lookup(keyword, name))
    if direct is not None:
        return direct
    return parse_sexagesimal(_lookup(keyword, fallback), hours_fallback)


def altitude_deg(ra_deg: float, dec_deg: float, lat_deg: float, lon_east_deg: float, unix_ms: float) -> float:
    """The altitude in degrees of a sky position for an observer, at a time.

    ``ra_deg`` and ``dec_deg`` are J2000 equatorial coordinates in degrees; ``lat_deg`` is the geodetic latitude and
    ``lon_east_deg`` the longitude, east positive; ``unix_ms`` is the time in ms since the Unix epoch (UTC). Uses
    GMST = 18.697374558 + 24.06570982441908 * (JD - 2451545.0) hours (mod 24), LST = GMST + longitude / 15, hour
    angle H = LST * 15 - RA, and sin(alt) = sin(lat) sin(dec) + cos(lat) cos(dec) cos(H). No precession, nutation,
    aberration or refraction: about 0.3 degree for J2000 coordinates in the 2020s.
    """
    jd = unix_ms / _MS_PER_DAY + _UNIX_EPOCH_JD
    gmst_hours = (18.697374558 + 24.06570982441908 * (jd - _J2000_JD)) % 24.0
    lst_hours = gmst_hours + lon_east_deg / 15.0
    hour_angle = math.radians(lst_hours * 15.0 - ra_deg)
    lat = math.radians(lat_deg)
    dec = math.radians(dec_deg)
    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    return math.degrees(math.asin(sin_alt))


def altitude_from_header(keyword: Keyword) -> Optional[float]:
    """A frame's altitude in degrees from its header, or None.

    ``keyword(name)`` returns the keyword's bare value string, or None when it is absent (as the header readers in
    ``loomsiril.frames_headers`` give it). Lookups are lazy: once a result is found, no further keyword is read.
    The order and rules are in the module docstring.
    """
    airmass = _number(_lookup(keyword, "AIRMASS"))
    if airmass is not None and airmass >= 1:
        return math.degrees(math.asin(1.0 / airmass))

    for name in ("CENTALT", "OBJCTALT"):
        alt = _number(_lookup(keyword, name))
        if alt is not None and 0 < alt <= 90:
            return alt

    ra = _pointing_degrees(keyword, "RA", "OBJCTRA", True)
    if ra is None:
        return None
    dec = _pointing_degrees(keyword, "DEC", "OBJCTDEC", False)
    if dec is None or not -90 <= dec <= 90:
        return None

    time_ms = obs_time(_lookup(keyword, "DATE-OBS"))
    if time_ms is None:
        return None
    lat = parse_sexagesimal(_lookup(keyword, "SITELAT"), False)
    lon = parse_sexagesimal(_lookup(keyword, "SITELONG"), False)
    if lat is None or lon is None or not -90 <= lat <= 90:
        return None

    alt = altitude_deg(ra, dec, lat, lon, time_ms)
    return alt if -90 < alt <= 90 else None
