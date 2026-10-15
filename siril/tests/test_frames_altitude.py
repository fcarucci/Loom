"""Frame altitude from a FITS header: the keyword order, the sky geometry and the sexagesimal reads.

Pure arithmetic: no Siril, no file, no node. The rotation-matrix check is a second implementation of the same
spherical geometry, so a slip in either one shows as a disagreement.
"""

from __future__ import annotations

import math
import random

import pytest

from loomsiril.frames_altitude import altitude_deg, altitude_from_header, parse_sexagesimal

UNIX_MS_2024 = 1717243200000  # 2024-06-01T12:00:00Z


def header(**values):
    """A keyword lookup over a dict (underscores in the Python names stand for hyphens); counts the calls."""
    calls = []
    cards = {key.replace("_", "-"): value for key, value in values.items()}

    def keyword(name):
        calls.append(name)
        return cards.get(name)

    keyword.calls = calls
    return keyword


def lst_deg(unix_ms, lon_east_deg):
    """Local sidereal time in degrees, from the same formula altitude_deg documents."""
    jd = unix_ms / 86400000.0 + 2440587.5
    gmst = (18.697374558 + 24.06570982441908 * (jd - 2451545.0)) % 24.0
    return ((gmst + lon_east_deg / 15.0) % 24.0) * 15.0


def altitude_rotation(ra_deg, dec_deg, lat_deg, lon_east_deg, unix_ms):
    """Independent check: the equatorial unit vector rotated into the horizon frame, then its z component."""
    lst = math.radians(lst_deg(unix_ms, lon_east_deg))
    ra, dec, lat = math.radians(ra_deg), math.radians(dec_deg), math.radians(lat_deg)
    v = (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec))

    def rot_z(a, p):
        x, y, z = p
        return (x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a), z)

    def rot_y(a, p):
        x, y, z = p
        return (x * math.cos(a) + z * math.sin(a), y, -x * math.sin(a) + z * math.cos(a))

    p = rot_z(-lst, v)  # hour-angle frame: x toward the meridian, z toward the pole
    p = rot_y(lat - math.pi / 2, p)  # tilt the pole so that the zenith lies on the z axis
    return math.degrees(math.asin(max(-1.0, min(1.0, p[2]))))


# keyword order

def test_airmass_gives_altitude():
    assert altitude_from_header(header(AIRMASS="2.0")) == pytest.approx(30.0, abs=1e-9)


def test_airmass_below_one_is_ignored():
    assert altitude_from_header(header(AIRMASS="0.9")) is None


def test_airmass_text_is_ignored():
    assert altitude_from_header(header(AIRMASS="abc")) is None


def test_airmass_quoted_fits_string_is_read():
    assert altitude_from_header(header(AIRMASS="'2.0'")) == pytest.approx(30.0, abs=1e-9)


def test_centalt_is_used():
    assert altitude_from_header(header(CENTALT="45.5")) == 45.5


def test_objctalt_is_used_when_centalt_absent():
    assert altitude_from_header(header(OBJCTALT="61.25")) == 61.25


def test_altitude_above_ninety_is_ignored():
    assert altitude_from_header(header(CENTALT="95")) is None
    assert altitude_from_header(header(OBJCTALT="95")) is None


def test_zero_altitude_is_ignored():
    assert altitude_from_header(header(CENTALT="0")) is None


def test_airmass_wins_over_centalt():
    assert altitude_from_header(header(AIRMASS="2.0", CENTALT="45.5")) == pytest.approx(30.0, abs=1e-9)


def test_lookups_stop_after_a_result():
    keyword = header(CENTALT="45.5", RA="10", DEC="20", DATE_OBS="2024-06-01")
    altitude_from_header(keyword)
    assert keyword.calls == ["AIRMASS", "CENTALT"]


def test_keyword_that_raises_is_skipped():
    def keyword(name):
        if name == "AIRMASS":
            raise RuntimeError("header unreadable")
        return {"CENTALT": "33.0"}.get(name)

    assert altitude_from_header(keyword) == 33.0


def test_computed_altitude_from_header():
    lon = -122.5
    ra, dec, lat = 83.0, 22.0, 37.0
    alt = altitude_from_header(header(RA=str(ra), DEC=str(dec), DATE_OBS="2024-06-01T12:00:00",
                                      SITELAT=str(lat), SITELONG=str(lon)))
    assert alt == pytest.approx(altitude_deg(ra, dec, lat, lon, UNIX_MS_2024), abs=1e-9)


def test_computed_altitude_from_sexagesimal_coordinates():
    alt = altitude_from_header(header(OBJCTRA="05 32 58.7", OBJCTDEC="+22 00 52", DATE_OBS="2024-06-01T12:00:00",
                                      SITELAT="+37:00:00", SITELONG="-122:30:00"))
    expected = altitude_deg(5 * 15 + 32 * 15 / 60 + 58.7 * 15 / 3600, 22 + 52 / 3600, 37.0, -122.5, UNIX_MS_2024)
    assert alt == pytest.approx(expected, abs=1e-9)


def test_computed_needs_site():
    assert altitude_from_header(header(RA="10", DEC="20", DATE_OBS="2024-06-01T12:00:00")) is None


def test_computed_needs_time():
    assert altitude_from_header(header(RA="10", DEC="20", SITELAT="37", SITELONG="-122")) is None


def test_computed_needs_valid_time():
    assert altitude_from_header(header(RA="10", DEC="20", DATE_OBS="not a time", SITELAT="37",
                                       SITELONG="-122")) is None


def test_computed_needs_pointing():
    assert altitude_from_header(header(DATE_OBS="2024-06-01T12:00:00", SITELAT="37", SITELONG="-122")) is None


def test_computed_rejects_dec_out_of_range():
    assert altitude_from_header(header(RA="10", DEC="95", DATE_OBS="2024-06-01T12:00:00", SITELAT="37",
                                       SITELONG="-122")) is None


def test_computed_rejects_bad_site_latitude():
    assert altitude_from_header(header(RA="10", DEC="20", DATE_OBS="2024-06-01T12:00:00", SITELAT="120",
                                       SITELONG="-122")) is None


# altitude_deg

def test_zenith_is_ninety():
    lat, lon = 40.0, -105.0
    ra = lst_deg(UNIX_MS_2024, lon)
    assert altitude_deg(ra, lat, lat, lon, UNIX_MS_2024) == pytest.approx(90.0, abs=1e-6)


def test_celestial_pole_altitude_is_latitude_at_any_time():
    for unix_ms in (UNIX_MS_2024, UNIX_MS_2024 + 3 * 3600_000, UNIX_MS_2024 - 111 * 86400_000):
        assert altitude_deg(123.0, 90.0, 52.5, 13.4, unix_ms) == pytest.approx(52.5, abs=1e-9)


def test_matches_rotation_matrices_on_random_cases():
    rng = random.Random(20240601)
    for _ in range(20):
        ra = rng.uniform(0.0, 360.0)
        dec = rng.uniform(-89.0, 89.0)
        lat = rng.uniform(-80.0, 80.0)
        lon = rng.uniform(-180.0, 180.0)
        unix_ms = UNIX_MS_2024 + rng.randint(-5 * 365 * 86400_000, 5 * 365 * 86400_000)
        assert altitude_deg(ra, dec, lat, lon, unix_ms) == pytest.approx(
            altitude_rotation(ra, dec, lat, lon, unix_ms), abs=1e-6)


# external references and header rules

MEEUS_1987_APR10_1921_UT_MS = 545080860000  # 1987-04-10T19:21:00Z


def test_meeus_example_13b_altitude():
    """Meeus, Astronomical Algorithms, example 13.b: altitude 15.1249 degrees.

    Star RA 23h 09m 16.641s, Dec -6d 43' 11.61"; observer 38d 55' 17" N, 77d 03' 56" W; 1987 Apr 10, 19h 21m UT.
    """
    ra = (23 + 9 / 60 + 16.641 / 3600) * 15.0
    dec = -(6 + 43 / 60 + 11.61 / 3600)
    alt = altitude_deg(ra, dec, 38.9213, -77.0656, MEEUS_1987_APR10_1921_UT_MS)
    assert alt == pytest.approx(15.1249, abs=0.01)


def test_star_at_transit_from_published_sidereal_time():
    """Meeus example 12.a: GMST at 1987 Apr 10 19h 21m UT is 8h 34m 57.0896s. At lon -77.0656 that gives LST
    = GMST - 77.0656 / 15 h. A star on the meridian (RA = LST) at dec 10.0 is at altitude 90 - (38.9213 - 10.0).
    """
    gmst_h = 8 + 34 / 60 + 57.0896 / 3600
    ra = (gmst_h - 77.0656 / 15.0) * 15.0
    alt = altitude_deg(ra, 10.0, 38.9213, -77.0656, MEEUS_1987_APR10_1921_UT_MS)
    assert alt == pytest.approx(90.0 - (38.9213 - 10.0), abs=0.1)
    assert alt == pytest.approx(61.0787, abs=0.001)


def test_date_only_gives_no_computed_altitude():
    assert altitude_from_header(header(RA="10", DEC="20", DATE_OBS="2024-06-01", SITELAT="37",
                                       SITELONG="-122")) is None


def test_date_with_time_gives_computed_altitude():
    assert altitude_from_header(header(RA="10", DEC="20", DATE_OBS="2024-06-01T00:00:00", SITELAT="37",
                                       SITELONG="-122")) is not None


def test_sexagesimal_in_ra_or_dec_is_not_read():
    assert altitude_from_header(header(RA="05 32 58.7", DEC="22.0", DATE_OBS="2024-06-01T12:00:00",
                                       SITELAT="37", SITELONG="-122")) is None
    assert altitude_from_header(header(RA="83", DEC="+22:00:00", DATE_OBS="2024-06-01T12:00:00",
                                       SITELAT="37", SITELONG="-122")) is None


def test_pointing_families_are_not_mixed():
    site = dict(DATE_OBS="2024-06-01T12:00:00", SITELAT="37", SITELONG="-122.5")
    # RA+DEC numbers are one family; OBJCTRA is not used with them.
    alt = altitude_from_header(header(RA="83", DEC="22", OBJCTRA="05 32 58", OBJCTDEC="+22 00 00", **site))
    assert alt == pytest.approx(altitude_deg(83.0, 22.0, 37.0, -122.5, UNIX_MS_2024), abs=1e-9)
    # A numeric RA with only OBJCTDEC is not a pointing.
    assert altitude_from_header(header(RA="83", OBJCTDEC="+22 00 00", **site)) is None


def test_obsgeo_fallback_for_site():
    alt = altitude_from_header(header(RA="83", DEC="22", DATE_OBS="2024-06-01T12:00:00", OBSGEO_B="37",
                                      OBSGEO_L="-122.5"))
    assert alt == pytest.approx(altitude_deg(83.0, 22.0, 37.0, -122.5, UNIX_MS_2024), abs=1e-9)


def test_sitelat_wins_over_obsgeo():
    alt = altitude_from_header(header(RA="83", DEC="22", DATE_OBS="2024-06-01T12:00:00", SITELAT="37",
                                      SITELONG="-122.5", OBSGEO_B="10", OBSGEO_L="0"))
    assert alt == pytest.approx(altitude_deg(83.0, 22.0, 37.0, -122.5, UNIX_MS_2024), abs=1e-9)


def test_site_suffix_is_not_parsed():
    assert altitude_from_header(header(RA="83", DEC="22", DATE_OBS="2024-06-01T12:00:00", SITELAT="37 N",
                                       SITELONG="122.5 W")) is None


# sexagesimal

def test_hours_sexagesimal_is_degrees():
    assert parse_sexagesimal("00 42 44", True) == pytest.approx(10.6833, abs=1e-4)


def test_negative_degrees_sexagesimal():
    assert parse_sexagesimal("-05:30:00", False) == pytest.approx(-5.5, abs=1e-12)


def test_space_separated_degrees_sexagesimal():
    assert parse_sexagesimal("+41 16 09", False) == pytest.approx(41.2692, abs=1e-4)


def test_plain_decimal_degrees():
    assert parse_sexagesimal("41.27", False) == 41.27


def test_plain_decimal_hours_is_converted_to_degrees():
    assert parse_sexagesimal("0.5", True) == 7.5


def test_sexagesimal_rejects_bad_fields():
    assert parse_sexagesimal("10:61:00", False) is None
    assert parse_sexagesimal("10:00:60", False) is None
    assert parse_sexagesimal("ten", False) is None
    assert parse_sexagesimal("", False) is None
    assert parse_sexagesimal(None, True) is None


def test_bare_quoted_value_is_read():
    assert parse_sexagesimal("'+41:16:09'", False) == pytest.approx(41.2692, abs=1e-4)
