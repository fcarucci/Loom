"""loomsiril.gaia against the JS oracle (tests/oracle/gaia.json), the public TSV fixture, and stub fetches.

No test opens a socket: online access is only ever reached through a stub ``fetch_text``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import math
import socket
import urllib.parse

import pytest

from loomsiril import gaia


def _same_record(a: dict, b: dict) -> None:
    assert a.keys() == b.keys()
    for k in b:
        if isinstance(b[k], float) and math.isnan(b[k]):
            assert math.isnan(a[k]), k
        else:
            assert a[k] == b[k], k


def test_constants_match_js(oracle):
    c = oracle("gaia")["cases"]["constants"]
    assert gaia.GAIA_ONLINE_URL == c["url"]
    assert list(gaia.GAIA_ONLINE_COLUMNS) == c["columns"]
    assert gaia.GAIA_ONLINE_MAX_ROWS == c["maxRows"]
    # Sky.js keeps the connect timeout in seconds and the others in ms; the port uses seconds throughout
    assert gaia.GAIA_ONLINE_TIMEOUT == c["timeoutS"]
    assert gaia.GAIA_ONLINE_STALL == c["stallMs"] / 1000
    assert gaia.GAIA_ONLINE_FIRST_BYTE == c["firstByteMs"] / 1000
    assert gaia.GAIA_ONLINE_TOTAL == c["totalMs"] / 1000


def test_online_url_matches_js(oracle):
    for case in oracle("gaia")["cases"]["url"]:
        centre, radius, g_max = case["args"]
        got = gaia.online_url(gaia.GaiaQuery.from_centre(centre, radius, g_max))
        assert got == case["out"]


# Characterization (R2-b, recorded on fly/final@a242afd before the GaiaQuery change): the URL and cache key of
# each request form, and the URL sources() actually queries (the radius rounded up). The URLs are pinned by
# SHA-256 of their text, the cache keys literally. (centre, radius, g_max, cache key, URL sha256, sources() URL sha256)
_CHARACTERIZED = [
    ({"ra": 315.4, "dec": 68.16}, 0.1, 10, "ra315.4000_dec68.1600_r0.100_g10.0",
     "2ed74e82d0855e0e75941509ab51121fe9bd47f5acafe777f931b1368692b49c",
     "2ed74e82d0855e0e75941509ab51121fe9bd47f5acafe777f931b1368692b49c"),
    ((315.4, 68.16), 0.1, 10, "ra315.4000_dec68.1600_r0.100_g10.0",
     "2ed74e82d0855e0e75941509ab51121fe9bd47f5acafe777f931b1368692b49c",
     "2ed74e82d0855e0e75941509ab51121fe9bd47f5acafe777f931b1368692b49c"),
    ([315.4, 68.16], 0.1004, 12.0, "ra315.4000_dec68.1600_r0.101_g12.0",
     "645ce0f71eabb50d4cdfc6d37b9de9ed800b8f4328cb0b6a8e069e81d455d65a",
     "80255180288ab15ee7047b05ebf03adc287e9417970f4fe864065817b7578f50"),
    ({"ra": 324.745, "dec": 57.514}, 1.4, 16, "ra324.7450_dec57.5140_r1.400_g16.0",
     "d89e9c630ae46e8adf8ebd697b9483b6747109812a3d6c7f8ca1da22fc1058a1",
     "d89e9c630ae46e8adf8ebd697b9483b6747109812a3d6c7f8ca1da22fc1058a1"),
    ((0.0001, -89.5), 0.5, 12.5, "ra0.0001_dec-89.5000_r0.500_g12.5",
     "8b638042795eb4cd4c5b8df0b50ce2d057768cce264a48171f77b04073de61b9",
     "8b638042795eb4cd4c5b8df0b50ce2d057768cce264a48171f77b04073de61b9"),
    ((10.5, -3.25), 0.5, 12.0, "ra10.5000_dec-3.2500_r0.500_g12.0",
     "f593b50faba05aa19e34c2cdf1740ad39a1afe785841dda8ad1a71fa1e73292f",
     "f593b50faba05aa19e34c2cdf1740ad39a1afe785841dda8ad1a71fa1e73292f"),
    ((1.00001, 2.0), 0.5, 12.02, "ra1.0000_dec2.0000_r0.500_g12.0",
     "3dc44cf11f27612a3d139904b6ad0c19ef4d1ac32725d736bd026c23163b663d",
     "3dc44cf11f27612a3d139904b6ad0c19ef4d1ac32725d736bd026c23163b663d"),
    ((10.0, 20.0), 0.1004, 12.0, "ra10.0000_dec20.0000_r0.101_g12.0",
     "b30f826da1ec17a4845dd7e04eeccb266fa1718ab37e104b0304ef20567251f1",
     "4114cd0c62636788262014e265ed167b56323d4b04c29f88759235ec6e34a66c"),
    ((10.0, 20.0), 0.1, 12.0, "ra10.0000_dec20.0000_r0.100_g12.0",
     "8130fb8c58e02febd3b8503e960abecfc77c43473837d67b384977647c005f67",
     "8130fb8c58e02febd3b8503e960abecfc77c43473837d67b384977647c005f67"),
    ((315.40001, 68.15999), 0.1, 10.04, "ra315.4000_dec68.1600_r0.100_g10.0",
     "f1067e9f298cfe082ec91e102ea43750f65ad997489eca04f1902ea375443d9f",
     "f1067e9f298cfe082ec91e102ea43750f65ad997489eca04f1902ea375443d9f"),
]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("centre,radius,g_max,key,url_sha,sources_sha", _CHARACTERIZED)
def test_characterized_urls_and_cache_keys_are_unchanged(centre, radius, g_max, key, url_sha, sources_sha, tmp_path):
    assert gaia.cache_key(gaia.GaiaQuery.from_centre(centre, radius, g_max)) == key
    assert _sha(gaia.online_url(gaia.GaiaQuery.from_centre(centre, radius, g_max))) == url_sha
    asked: list[str] = []

    def stub(url: str) -> str:
        asked.append(url)
        return "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag\n1,2,3,4,5,6,7,8\n"

    gaia.sources(gaia.GaiaQuery.from_centre(centre, radius, g_max), source="online", cache_dir=tmp_path, fetch_text=stub)
    assert _sha(asked[0]) == sources_sha
    assert [p.name for p in tmp_path.iterdir()] == [key + ".csv"]


def test_gaia_query_is_a_frozen_value_object():
    q = gaia.GaiaQuery.from_centre({"ra": 315.4, "dec": 68.16}, 0.1, 10)
    assert q == gaia.GaiaQuery((315.4, 68.16), 0.1, 10)
    assert q == gaia.GaiaQuery.from_centre([315.4, 68.16], 0.1, 10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.radius_deg = 1.0  # type: ignore[misc]


def test_online_url_accepts_centre_dict():
    url = gaia.online_url(gaia.GaiaQuery.from_centre({"ra": 315.4, "dec": 68.16}, 0.1, 10))
    assert url == gaia.online_url(gaia.GaiaQuery.from_centre((315.4, 68.16), 0.1, 10))
    assert "CIRCLE('ICRS',315.4,68.16,0.1)" in url.replace("%2C", ",").replace("%27", "'")


def test_parse_csv_matches_js(oracle):
    for case in oracle("gaia")["cases"]["parseGaiaCsv"]:
        got = gaia.parse_csv(case["args"][0])
        want = case["out"]
        if want is None:
            assert got is None, case["name"]
            continue
        assert got is not None and len(got) == len(want), case["name"]
        for g, w in zip(got, want):
            _same_record(g, w)


def test_parse_csv_rules():
    header = "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag\r\n"
    assert gaia.parse_csv("RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag\n1,2,3,4,5,6,7\n") is None
    assert gaia.parse_csv("") is None
    assert gaia.parse_csv(None) is None
    assert gaia.parse_csv("<VOTABLE/>") is None
    assert gaia.parse_csv(header) == []
    rows = gaia.parse_csv(header + "1,2,3,4,5,6,7\r\n1,2,3,4,5,6,7,8\r\n")
    assert len(rows) == 1  # the 7-field row is skipped, the \r\n line ends are fine
    assert rows[0] == {"ra": 1.0, "dec": 2.0, "plx": 3.0, "pmra": 4.0, "pmdec": 5.0, "G": 6.0, "BP": 7.0, "RP": 8.0}


def test_read_tsv_fixture(gaia_tsv_path):
    recs = gaia.read_tsv(gaia_tsv_path)
    assert len(recs) == 8093
    first = gaia_tsv_path.read_text(encoding="utf-8").splitlines()[1].split("\t")
    expect = {"ra": float(first[0]), "dec": float(first[1]), "plx": float(first[2]), "pmra": float(first[3]),
              "pmdec": float(first[4]), "G": float(first[5]), "BP": float(first[6]), "RP": float(first[7])}
    assert recs[0] == expect
    assert recs[0]["ra"] == 324.740052 and recs[0]["G"] == 5.612


def test_read_tsv_blank_is_nan(tmp_path):
    p = tmp_path / "g.tsv"
    p.write_text("ra\tdec\tplx\tpmra\tpmdec\tG\tBP\tRP\n1\t2\t\t4\t5\t6\t\t8\n", encoding="utf-8")
    (rec,) = gaia.read_tsv(p)
    assert math.isnan(rec["plx"]) and math.isnan(rec["BP"])
    assert rec["RP"] == 8.0 and rec["pmra"] == 4.0


def test_query_online_records_url_and_parses():
    seen: list[str] = []
    csv = "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag\n315.4,68.16,2.8,7.1,-2.2,7.2,7.3,6.9\n"

    def stub(url: str) -> str:
        seen.append(url)
        return csv

    recs = gaia.query_online(gaia.GaiaQuery.from_centre((315.4, 68.16), 0.1, 10), fetch_text=stub)
    assert seen == [gaia.online_url(gaia.GaiaQuery.from_centre((315.4, 68.16), 0.1, 10))]
    assert recs == [{"ra": 315.4, "dec": 68.16, "plx": 2.8, "pmra": 7.1, "pmdec": -2.2, "G": 7.2, "BP": 7.3, "RP": 6.9}]


@pytest.mark.parametrize("answer", ["<VOTABLE><INFO name=\"QUERY_STATUS\" value=\"ERROR\"/></VOTABLE>", "", None])
def test_query_online_garbage_is_a_clear_error(answer):
    with pytest.raises(gaia.GaiaError, match="Gaia online"):
        gaia.query_online(gaia.GaiaQuery.from_centre((315.4, 68.16), 0.1, 10), fetch_text=lambda url: answer)


def test_sources_tsv(gaia_tsv_path, tmp_path):
    recs = gaia.sources(gaia.GaiaQuery.from_centre((324.745, 57.514), 1.4, 16), source="tsv", tsv_path=gaia_tsv_path, cache_dir=tmp_path, fetch_text=lambda url: pytest.fail("tsv must not query"))
    assert len(recs) == 8093


def test_sources_cache_round_trip(tmp_path):
    calls: list[str] = []
    csv = "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag\n10.5,-3.25,,,,12.0,,\n"

    def stub(url: str) -> str:
        calls.append(url)
        return csv

    first = gaia.sources(gaia.GaiaQuery.from_centre((10.5, -3.25), 0.5, 12.0), cache_dir=tmp_path, fetch_text=stub)
    assert len(calls) == 1 and first[0]["G"] == 12.0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [gaia.cache_key(gaia.GaiaQuery.from_centre((10.5, -3.25), 0.5, 12.0)) + ".csv"]
    assert not any(p.name.endswith(".part") for p in tmp_path.iterdir())

    second = gaia.sources(gaia.GaiaQuery.from_centre((10.5, -3.25), 0.5, 12.0), cache_dir=tmp_path, fetch_text=lambda url: pytest.fail("a cache hit must not query"))
    assert second == first


def test_sources_online_writes_cache_and_cache_key_rounds(tmp_path):
    csv = "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag\n1,2,3,4,5,6,7,8\n"
    recs = gaia.sources(gaia.GaiaQuery.from_centre((1.00001, 2.0), 0.5, 12.02), source="online", cache_dir=tmp_path, fetch_text=lambda u: csv)
    assert len(recs) == 1
    # the key rounds centre to 1e-4 deg, radius to 1e-3 and g_max to 0.1: this request hits the same file
    assert gaia.cache_key(gaia.GaiaQuery.from_centre((1.00001, 2.0), 0.5, 12.02)) == gaia.cache_key(gaia.GaiaQuery.from_centre((1.0, 2.0), 0.5, 12.0))
    assert (tmp_path / (gaia.cache_key(gaia.GaiaQuery.from_centre((1.0, 2.0), 0.5, 12.0)) + ".csv")).exists()


def test_sources_cache_dir_default_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert gaia.default_cache_dir() == tmp_path / "loomsiril" / "gaia"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert gaia.default_cache_dir().name == "gaia"


def test_stub_only_run_opens_no_socket(tmp_path, monkeypatch):
    opened: list[object] = []
    real = socket.socket.connect

    def spy(self, address):  # pragma: no cover - must never run
        opened.append(address)
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    csv = "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag\n1,2,3,4,5,6,7,8\n"
    gaia.sources(gaia.GaiaQuery.from_centre((1.0, 2.0), 0.5, 12.0), cache_dir=tmp_path, fetch_text=lambda url: csv)
    gaia.query_online(gaia.GaiaQuery.from_centre((1.0, 2.0), 0.5, 12.0), fetch_text=lambda url: csv)
    assert opened == []


class _FakeResponse:
    """A urlopen() stand-in: yields the given chunks, advancing a fake clock by each chunk's delay.

    ``read1`` returns one chunk per call (one recv on a real socket). ``read`` must never be called here:
    fetch_text has to pick read1 when the response has one (the stall timer depends on it), so a call to read
    fails the test with AssertionError, which fetch_text does not catch.
    """

    def __init__(self, chunks, clock, delays):
        self._chunks = list(chunks)
        self._clock = clock
        self._delays = list(delays)

    def _next(self, n: int = -1) -> bytes:
        self._clock[0] += self._delays.pop(0) if self._delays else 0.0
        if self._chunks and isinstance(self._chunks[0], BaseException):
            raise self._chunks.pop(0)
        return self._chunks.pop(0) if self._chunks else b""

    def read1(self, n: int = -1) -> bytes:
        return self._next(n)

    def read(self, n: int = -1) -> bytes:
        raise AssertionError("read used")

    def close(self) -> None:
        pass


class _ReadOnlyResponse(_FakeResponse):
    """No read1: fetch_text must fall back to read()."""

    read1 = None

    def read(self, n: int = -1) -> bytes:
        return self._next(n)


def _install(monkeypatch, chunks, delays, cls=_FakeResponse):
    clock = [0.0]
    monkeypatch.setattr(gaia, "_clock", lambda: clock[0])
    monkeypatch.setattr(gaia, "_urlopen", lambda url, timeout=None: cls(chunks, clock, delays))


def test_fetch_text_reads_chunks(monkeypatch):
    _install(monkeypatch, [b"RA_ICRS,", b"DE_ICRS\n"], [1.0, 1.0])
    assert gaia.fetch_text("https://example.invalid/x") == "RA_ICRS,DE_ICRS\n"


def test_fetch_text_gives_up_on_a_stall(monkeypatch):
    _install(monkeypatch, [b"a", b"b"], [0.0, 40.0])  # 40 s without a byte, stall limit 30 s
    with pytest.raises(gaia.GaiaError, match="stall"):
        gaia.fetch_text("https://example.invalid/x", stall_s=30, first_byte_s=120, total_s=600)


def test_fetch_text_gives_up_at_the_total_deadline(monkeypatch):
    _install(monkeypatch, [b"a"] * 30, [5.0] * 30)  # a trickle never stalls, but never ends either
    with pytest.raises(gaia.GaiaError, match="total"):
        gaia.fetch_text("https://example.invalid/x", stall_s=30, first_byte_s=120, total_s=60)


def test_fetch_text_first_byte_limit(monkeypatch):
    _install(monkeypatch, [b"a"], [150.0])  # the first byte after 150 s, first-byte limit 120 s
    with pytest.raises(gaia.GaiaError, match="first byte"):
        gaia.fetch_text("https://example.invalid/x", stall_s=30, first_byte_s=120, total_s=600)


def test_gaia_error_is_a_runtime_error():
    assert issubclass(gaia.GaiaError, RuntimeError)


def test_cache_key_is_stable_and_documented():
    assert gaia.cache_key(gaia.GaiaQuery.from_centre((315.4, 68.16), 0.1, 10)) == gaia.cache_key(gaia.GaiaQuery.from_centre((315.40001, 68.15999), 0.1, 10.04))
    assert gaia.cache_key(gaia.GaiaQuery.from_centre((315.4, 68.16), 0.1, 10)) != gaia.cache_key(gaia.GaiaQuery.from_centre((315.4, 68.16), 0.2, 10))


def test_fetch_text_slow_trickle_with_read1_is_not_a_stall(monkeypatch):
    # 40 small read1 chunks, 10 s apart: 400 s in all (over the 30 s stall limit as a whole), but every gap is
    # 10 s. read() raises in this fake, so passing means fetch_text pinned read1.
    _install(monkeypatch, [b"x" * 100] * 40, [10.0] * 40)
    text = gaia.fetch_text("https://example.invalid/x", stall_s=30, first_byte_s=120, total_s=600)
    assert len(text) == 4000


def test_fetch_text_read_fallback_still_works(monkeypatch):
    _install(monkeypatch, [b"ab", b"cd"], [1.0, 1.0], cls=_ReadOnlyResponse)
    assert gaia.fetch_text("https://example.invalid/x") == "abcd"


def test_fetch_text_incomplete_read_is_a_gaia_error(monkeypatch):
    _install(monkeypatch, [b"RA_ICRS,", http.client.IncompleteRead(b"partial")], [1.0, 1.0])
    with pytest.raises(gaia.GaiaError):
        gaia.fetch_text("https://example.invalid/x")


def test_fetch_text_incomplete_read_on_fallback_read_is_a_gaia_error(monkeypatch):
    _install(monkeypatch, [http.client.IncompleteRead(b"")], [1.0], cls=_ReadOnlyResponse)
    with pytest.raises(gaia.GaiaError):
        gaia.fetch_text("https://example.invalid/x")


def test_fetch_text_bad_status_line_on_urlopen_is_a_gaia_error(monkeypatch):
    def refuse(url, timeout=None):
        raise http.client.BadStatusLine("garbage")

    monkeypatch.setattr(gaia, "_urlopen", refuse)
    with pytest.raises(gaia.GaiaError):
        gaia.fetch_text("https://example.invalid/x")


def test_cache_radius_rounds_up_and_the_query_uses_it(tmp_path):
    # a request for 0.1004 deg must not be served from (or stored as) a 0.100 deg answer
    assert gaia.cache_key(gaia.GaiaQuery.from_centre((10.0, 20.0), 0.1004, 12.0)).endswith("_r0.101_g12.0")
    assert gaia.cache_key(gaia.GaiaQuery.from_centre((10.0, 20.0), 0.1, 12.0)).endswith("_r0.100_g12.0")
    urls: list[str] = []
    csv = "RA_ICRS,DE_ICRS,Plx,pmRA,pmDE,Gmag,BPmag,RPmag\n1,2,3,4,5,6,7,8\n"

    def stub(url: str) -> str:
        urls.append(url)
        return csv

    gaia.sources(gaia.GaiaQuery.from_centre((10.0, 20.0), 0.1004, 12.0), source="online", cache_dir=tmp_path, fetch_text=stub)
    assert "0.101" in urllib.parse.unquote(urls[0])
    assert "0.1004" not in urllib.parse.unquote(urls[0])


# ---- R1-e characterization (old code): fetch_text branches no other test reaches ----------------


class _Recorder:
    """A socket stand-in: records each settimeout value."""

    def __init__(self):
        self.timeouts: list[float] = []

    def settimeout(self, seconds: float) -> None:
        self.timeouts.append(seconds)


class _SocketResponse(_FakeResponse):
    """A _FakeResponse whose socket is visible to _set_recv_timeout, and which counts close() calls."""

    def __init__(self, chunks, clock, delays, sock):
        super().__init__(chunks, clock, delays)
        self.fp = type("FP", (), {"raw": type("Raw", (), {"_sock": sock})()})()
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def _install_response(monkeypatch, response):
    clock = [0.0]
    monkeypatch.setattr(gaia, "_clock", lambda: clock[0])
    monkeypatch.setattr(gaia, "_urlopen", lambda url, timeout=None: response(clock))


def test_fetch_text_empty_answer_is_empty_text(monkeypatch):
    _install(monkeypatch, [], [])
    assert gaia.fetch_text("https://example.invalid/x") == ""


def test_fetch_text_socket_timeout_before_the_first_byte_is_a_first_byte_error(monkeypatch):
    _install(monkeypatch, [socket.timeout("timed out")], [0.0])
    with pytest.raises(gaia.GaiaError, match="first byte"):
        gaia.fetch_text("https://example.invalid/x")


def test_fetch_text_socket_timeout_after_data_is_a_stall(monkeypatch):
    _install(monkeypatch, [b"a", socket.timeout("timed out")], [1.0, 1.0])
    with pytest.raises(gaia.GaiaError, match="stall"):
        gaia.fetch_text("https://example.invalid/x")


def test_fetch_text_urlopen_error_is_a_gaia_error(monkeypatch):
    import urllib.error

    def unreachable(url, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(gaia, "_urlopen", unreachable)
    with pytest.raises(gaia.GaiaError, match="no route to host") as info:
        gaia.fetch_text("https://example.invalid/x")
    assert isinstance(info.value.__cause__, urllib.error.URLError)


def test_fetch_text_sets_the_socket_timeout_before_each_read(monkeypatch):
    """First read: the first-byte limit; later reads: the stall limit; both capped by the time left in total."""
    sock = _Recorder()
    _install_response(monkeypatch, lambda clock: _SocketResponse([b"a", b"b"], clock, [1.0, 1.0], sock))
    assert gaia.fetch_text("https://example.invalid/x", stall_s=30, first_byte_s=120, total_s=600) == "ab"
    assert sock.timeouts == [120.0, 30.0, 30.0]


def test_fetch_text_socket_timeout_is_capped_by_the_total_time_left(monkeypatch):
    sock = _Recorder()
    _install_response(monkeypatch, lambda clock: _SocketResponse([b"a"], clock, [0.0], sock))
    gaia.fetch_text("https://example.invalid/x", stall_s=30, first_byte_s=120, total_s=10)
    assert sock.timeouts[0] == 10.0


def test_fetch_text_closes_the_response_once_on_success_and_on_error(monkeypatch):
    seen: list[_SocketResponse] = []

    def make(chunks, delays):
        def factory(clock):
            r = _SocketResponse(chunks, clock, delays, _Recorder())
            seen.append(r)
            return r

        return factory

    _install_response(monkeypatch, make([b"a", socket.timeout("late")], [1.0, 40.0]))
    with pytest.raises(gaia.GaiaError):
        gaia.fetch_text("https://example.invalid/x", stall_s=30)
    assert seen[-1].closed == 1

    _install_response(monkeypatch, make([b"ok"], [1.0]))
    assert gaia.fetch_text("https://example.invalid/x") == "ok"
    assert seen[-1].closed == 1


@pytest.mark.parametrize("centre", [(float("nan"), 0.0), (float("inf"), 0.0), (-float("inf"), 0.0),
                                    (10.0, 90.5), (10.0, -90.5), (10.0, float("nan"))])
def test_centre_off_the_sky_is_a_value_error(centre: tuple[float, float]) -> None:
    with pytest.raises(ValueError, match="Gaia centre"):
        gaia.GaiaQuery.from_centre(centre, 0.1, 10)
    with pytest.raises(ValueError, match="Gaia centre"):
        gaia.GaiaQuery.from_centre({"ra": centre[0], "dec": centre[1]}, 0.1, 10)


@pytest.mark.parametrize("centre", [(0.0, -90.0), (359.999999, 90.0), (0.0, 0.0), (315.4, 68.16)])
def test_centre_on_the_sky_is_accepted(centre: tuple[float, float]) -> None:
    assert gaia.GaiaQuery.from_centre(centre, 0.1, 10).centre == centre


@pytest.mark.parametrize("ra,want", [(360.0, 0.0), (-10.0, 350.0), (370.0, 10.0), (-370.0, 350.0),
                                     (-0.0, 0.0), (-1e-20, 0.0), (359.5, 359.5), (0.0, 0.0)])
def test_from_centre_wraps_a_finite_ra_into_zero_to_360(ra: float, want: float) -> None:
    q = gaia.GaiaQuery.from_centre((ra, 12.5), 0.1, 10)
    assert q.centre == (want, 12.5)
    assert math.copysign(1.0, q.centre[0]) == 1.0                  # -0.0 is not kept


def test_from_centre_still_refuses_nan_inf_and_dec_off_the_sky() -> None:
    for centre in [(float("nan"), 0.0), (float("inf"), 0.0), (-float("inf"), 0.0), (10.0, 90.5), (10.0, float("nan"))]:
        with pytest.raises(ValueError, match="Gaia centre"):
            gaia.GaiaQuery.from_centre(centre, 0.1, 10)
    with pytest.raises(ValueError, match="Gaia centre ra"):           # direct construction stays strict
        gaia.GaiaQuery((360.0, 0.0), 0.1, 10)
