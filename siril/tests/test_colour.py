"""loomsiril.colour against the JS oracle (tests/oracle/colour.json), the
selftest literals of fly.colour / fly.hdr, and known answers.

The oracle profiles are the bytes selftest.js builds; the transforms are
replayed on the recorded float32 input triples."""

from __future__ import annotations

import math

import numpy as np
import pytest

from loomsiril import colour
from loomsiril.colour import (
    BRADFORD_D50_D65,
    SRGB_COLOUR,
    SRGB_CURVE,
    XYZ_TO_RGB,
    colour_of,
    decode_lut,
    hlg_encode,
    hlg_from_display,
    mat_mul,
    output_transform,
    para_curve,
    parse_icc_colour,
    pq_encode,
    rolloff,
    source_to_target,
    srgb_encode,
    srgb_icc_bytes,
    table_curve,
    trc_decode,
)

PROPHOTO = [0.7976749, 0.1351917, 0.0313534, 0.2880402, 0.7118741, 0.0000857, 0, 0, 0.8252100]
G18 = {"type": "gamma", "g": 1.8}
PROPHOTO_COLOUR = {"matrix": PROPHOTO, "trc": [G18, G18, G18], "name": "ProPhoto"}
GRAY_COLOUR = {"gray": True, "matrix": None, "trc": [G18, G18, G18]}
COLOURS = {"srgb": SRGB_COLOUR, "prophoto": PROPHOTO_COLOUR, "gray": GRAY_COLOUR}


def _same(a: object, b: object, tol: float = 1e-12) -> bool:
    if isinstance(b, float) and math.isnan(b):
        return isinstance(a, float) and math.isnan(a)
    if isinstance(b, dict):
        return isinstance(a, dict) and a.keys() == b.keys() and all(_same(a[k], b[k], tol) for k in b)
    if isinstance(b, list):
        return isinstance(a, list) and len(a) == len(b) and all(_same(x, y, tol) for x, y in zip(a, b))
    if b is None or isinstance(b, (bool, str)):
        return a == b
    return abs(float(a) - float(b)) <= tol


# ---- scalar functions, replayed against the JS -----------------------------

def test_mat_mul_matches_js(oracle):
    for case in oracle("colour")["cases"]["matMul"]:
        assert _same(mat_mul(*case["args"]), case["out"]), case


@pytest.mark.parametrize(
    ("name", "fn"),
    [("trcDecode", lambda a: trc_decode(*a)),
     ("tableCurve", lambda a: table_curve(*a)),
     ("paraCurve", lambda a: para_curve(*a)),
     ("srgbEncode", lambda a: srgb_encode(*a)),
     ("pqEncode", lambda a: pq_encode(*a)),
     ("hlgEncode", lambda a: hlg_encode(*a)),
     ("hlgFromDisplay", lambda a: hlg_from_display(*a)),
     ("rolloff", lambda a: rolloff(*a)),
     ("sourceToTarget", lambda a: source_to_target(*a))],
)
def test_scalar_against_js(oracle, name, fn):
    cases = oracle("colour")["cases"][name]
    assert cases
    for case in cases:
        assert _same(fn(case["args"]), case["out"]), (name, case)


def test_decode_lut_against_js_lut(oracle):
    # the JS table is 65537 samples read with linear interpolation: 1e-6 of it
    for case in oracle("colour")["cases"]["decodeLut"]:
        curve, x = case["args"]
        got = float(decode_lut(curve)(np.array([x]))[0])
        assert abs(got - case["out"]) <= 1e-6, (curve, x, got)


def test_lut_is_clamped_and_nan_reads_the_first_entry():
    f = decode_lut(G18)
    assert f(np.array([-1.0, 0.0, np.nan]))[0] == 0.0
    assert f(np.array([2.0]))[0] == 1.0


# ---- ICC parsing: the bytes selftest builds ----------------------------------

def test_parse_icc_colour_matches_js(oracle):
    for case in oracle("colour")["cases"]["parseIccColour"]:
        data = bytes(case["bytes"])
        assert _same(parse_icc_colour(data), case["out"]), case["name"]


def test_matrix_profile_has_the_selftest_values(oracle):
    got = parse_icc_colour(bytes(oracle("colour")["cases"]["parseIccColour"][0]["bytes"]))
    assert got is not None and got["gray"] is False
    assert abs(got["matrix"][0] - 0.7976749) < 1e-4 and abs(got["matrix"][3] - 0.2880402) < 1e-4
    assert abs(float(trc_decode(got["trc"][1], 0.5)) - 0.5**1.8) < 1e-3


def test_no_matrix_profile_is_none():
    assert parse_icc_colour(bytes(132)) is None
    assert parse_icc_colour(b"") is None


# ---- output transforms: recorded triples -------------------------------------

@pytest.mark.parametrize("case_idx", range(9))
def test_output_transform_matches_js(oracle, case_idx):
    case = oracle("colour")["cases"]["transforms"][case_idx]
    t = output_transform(COLOURS[case["colour"]], case["mode"], case["opts"])
    inp = case["input"]
    if case["colour"] == "gray":
        k = np.array(inp["r"], dtype=np.float32)
        t.apply([k], [np.array(inp["xr"], dtype=np.float32)])
        got = [k]
        want = [np.array(case["out"]["rgb"], dtype=np.float32)]
    else:
        planes = [np.array(inp[c], dtype=np.float32) for c in ("r", "g", "b")]
        excess = [np.array(inp[c], dtype=np.float32) for c in ("xr", "xg", "xb")]
        t.apply(planes, excess)
        got = planes
        want = [np.array(p, dtype=np.float32) for p in case["out"]["rgb"]]
    for g, w in zip(got, want):
        assert np.max(np.abs(g.astype(np.float64) - w.astype(np.float64))) <= 1e-6, case["colour"] + case["mode"]
    if case["out"]["stats"] is not None:
        assert abs(t.stats["max_cll"] - case["out"]["stats"]["maxCll"]) <= 1e-6
        assert abs(t.stats["max_fall"] - case["out"]["stats"]["maxFall"]) <= 1e-6


def test_hdr_stats_of_the_selftest_frame(oracle):
    # selftest fly.hdr: white and black pixels in PQ -> MaxCLL 203 nits, MaxFALL 101.5 nits
    t = output_transform(SRGB_COLOUR, "pq", {"peak": 1000})
    planes = [np.array([1, 0], dtype=np.float32) for _ in range(3)]
    t.apply(planes, [np.zeros(2, dtype=np.float32) for _ in range(3)])
    assert abs(t.stats["max_cll"] - 203) <= 0.5
    assert abs(t.stats["max_fall"] - 101.5) <= 0.5
    st = oracle("colour")["cases"]["stats"]
    assert abs(st["maxCll"] - t.stats["max_cll"]) <= 1e-6


# ---- literal pins from selftest.js -------------------------------------------

def test_sdr_srgb_decode_literal():
    assert abs(float(trc_decode(SRGB_CURVE, 0.5)) - 0.214041) <= 1e-6


def test_parametric_type3_follows_its_formula():
    p3 = {"type": "para", "fn": 3, "p": [2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045]}
    for x in (0, 0.02, 0.04045, 0.3, 1):
        want = ((x + 0.055) / 1.055) ** 2.4 if x >= 0.04045 else x / 12.92
        assert abs(float(trc_decode(p3, x)) - want) <= 1e-9


def test_gamma_and_table_curves():
    assert abs(float(trc_decode({"type": "gamma", "g": 1.8}, 0.5)) - 0.5**1.8) <= 1e-12
    assert abs(float(trc_decode({"type": "table", "t": [0, 0.25, 1]}, 0.75)) - 0.625) <= 1e-12


def test_primaries_and_white():
    m709 = source_to_target(PROPHOTO, "rec709")
    m2020 = source_to_target(PROPHOTO, "rec2020")
    assert np.allclose(np.array(m709).reshape(3, 3) @ [1, 1, 1], [1, 1, 1], atol=2e-3)
    assert np.allclose(np.array(m2020).reshape(3, 3) @ [1, 1, 1], [1, 1, 1], atol=2e-3)
    assert np.array(m709).reshape(3, 3)[0] @ [0, 1, 0] < 0   # ProPhoto green is outside Rec.709
    srgb709 = np.array(source_to_target(SRGB_COLOUR["matrix"], "rec709")).reshape(3, 3)
    assert np.allclose(srgb709 @ [1, 0, 0], [1, 0, 0], atol=2e-3)


def test_pq_and_hlg_literals():
    assert abs(pq_encode(203) - 0.5807) <= 1e-4
    assert abs(pq_encode(10000) - 1) <= 1e-9
    assert abs(hlg_encode(1 / 12) - 0.5) <= 1e-7
    sw = hlg_from_display([0.203, 0.203, 0.203])
    assert abs(hlg_encode(sw[1]) - 0.75) <= 1e-3


def test_rolloff_identity_slope_monotone():
    p = 1000 / 203
    assert rolloff(0.6, p) == 0.6
    assert abs((rolloff(1 + 1e-6, p) - 1) / 1e-6 - 1) <= 1e-4
    prev, L = -1.0, 0.0
    while L < 1e6:
        y = rolloff(L, p)
        assert y >= prev and y <= p
        prev, L = y, L * 1.5 + 0.01


def test_hdr_pq_highlights():
    t = output_transform(SRGB_COLOUR, "pq", {"peak": 1000})
    def px(r, g, b, xr=0.0, xg=0.0, xb=0.0):
        planes = [np.array([v], dtype=np.float32) for v in (r, g, b)]
        t.apply(planes, [np.array([v], dtype=np.float32) for v in (xr, xg, xb)])
        return [float(p[0]) for p in planes]
    assert abs(px(1, 1, 1)[0] - 0.5807) <= 1e-3
    hot = px(1, 1, 1, 50, 50, 50)
    assert hot[1] > 0.6 and hot[1] <= pq_encode(1000) + 1e-6
    three = px(1, 1, 1, 2, 2, 2)[1]
    assert pq_encode(500) < three < pq_encode(540)
    hue = px(1, 0.5, 0.25, 20, 10, 5)
    assert hue[0] > hue[1] > hue[2]


def test_hlg_white_lands_at_075():
    t = output_transform(SRGB_COLOUR, "hlg", {})
    planes = [np.array([1.0], dtype=np.float32) for _ in range(3)]
    t.apply(planes, [np.zeros(1, dtype=np.float32) for _ in range(3)])
    assert abs(float(planes[1][0]) - 0.75) <= 2e-3


def test_sdr_of_srgb_is_identity():
    t = output_transform(SRGB_COLOUR, "sdr")
    v = np.linspace(0, 1, 257, dtype=np.float32)
    planes = [v.copy(), (v * 0.5).astype(np.float32), (1 - v).astype(np.float32)]
    t.apply(planes)
    assert np.max(np.abs(planes[0] - v)) <= 1e-4
    assert np.max(np.abs(planes[1] - v * 0.5)) <= 1e-4
    assert np.max(np.abs(planes[2] - (1 - v))) <= 1e-4


def test_prophoto_white_stays_white_and_green_is_clipped():
    t = output_transform(PROPHOTO_COLOUR, "sdr")
    planes = [np.array([1.0, 0.0], np.float32), np.array([1.0, 1.0], np.float32), np.array([1.0, 0.0], np.float32)]
    t.apply(planes)
    assert np.allclose([planes[0][0], planes[1][0], planes[2][0]], 1, atol=2e-3)
    assert all(0.0 <= float(p[1]) <= 1.0 for p in planes)


def test_gray_path_uses_one_plane():
    t = output_transform(GRAY_COLOUR, "sdr")
    k = np.linspace(0, 1, 9, dtype=np.float32)
    want = np.array([srgb_encode(trc_decode(G18, float(v))) for v in k], dtype=np.float64)
    t.apply([k])
    assert np.max(np.abs(k - want)) <= 1e-5


# ---- the built-in sRGB profile ------------------------------------------------

def test_srgb_icc_bytes_round_trip():
    data = srgb_icc_bytes()
    assert int.from_bytes(data[0:4], "big") == len(data)
    assert data[36:40] == b"acsp" and data[12:16] == b"mntr" and data[16:20] == b"RGB "
    col = parse_icc_colour(data)
    assert col is not None and col["gray"] is False
    assert np.max(np.abs(np.array(col["matrix"]) - np.array(SRGB_COLOUR["matrix"]))) <= 1e-4
    for c in col["trc"]:
        assert c["type"] == "para" and c["fn"] == 3
        assert np.max(np.abs(np.array(c["p"]) - np.array(SRGB_CURVE["p"]))) <= 1e-4
    off = int.from_bytes(data[128 + 4 + 4:128 + 4 + 8], "big")
    assert off % 4 == 0


def test_srgb_icc_is_an_sdr_identity():
    col = parse_icc_colour(srgb_icc_bytes())
    t = output_transform(col, "sdr")
    v = np.linspace(0, 1, 129, dtype=np.float32)
    planes = [v.copy(), v.copy(), v.copy()]
    t.apply(planes)
    assert np.max(np.abs(planes[0] - v)) <= 1e-4 and np.max(np.abs(planes[2] - v)) <= 1e-4


def test_colour_of_three_cases():
    none = colour_of(None)
    assert none["note"] == "no colour profile: taken as sRGB" and none["colour"] == SRGB_COLOUR
    junk = colour_of(b"not an icc profile at all, and it is shorter than the header would need to be for one")
    assert junk["note"] == "colour profile is not a matrix/curve profile: taken as sRGB"
    assert junk["colour"] == SRGB_COLOUR
    own = colour_of(srgb_icc_bytes())
    assert own["note"] == "colour converted from the image's own profile"
    assert own["colour"]["gray"] is False


def test_constants_are_the_js_ones():
    assert BRADFORD_D50_D65 == [0.9555766, -0.0230393, 0.0631636, -0.0282895, 1.0099416, 0.0210077,
                                0.0122982, -0.0204830, 1.3299098]
    assert XYZ_TO_RGB["rec709"][0] == 3.2404542
    assert colour.SDR_WHITE_NITS == 203 and colour.HDR_PEAK_DEFAULT == 1000 and colour.HLG_REFERENCE_PEAK == 1000
    assert colour.HDR_DEFAULT_TRANSFER == {"social_vertical": "hlg", "social_square": "hlg", "youtube_4k": "hlg",
                                           "youtube_1080": "hlg", "exhibition": "pq"}
    assert colour.DECODE_LUT == 65536
    assert colour.is_hdr("pq") and colour.is_hdr("hlg") and not colour.is_hdr("sdr")


# ---- characterization (R3-b): the HDR paths' bytes, recorded on the pre-loop code ----------------

def _hdr_case_bytes(mode: str, colour_desc: dict, gray: bool, with_excess: bool) -> bytes:
    """One deterministic HDR apply: the planes and the star excess are built from index arithmetic (no RNG)."""
    t = output_transform(colour_desc, mode, {"peak": 1000} if mode == "pq" else None)
    grid = np.arange(4 * 5, dtype=np.float64).reshape(4, 5)
    base = [(grid * (0.031 + 0.017 * c)) % 1.0 for c in range(3)]
    planes = [base[0].astype(np.float32)] if gray else [p.astype(np.float32) for p in base]
    excess = None
    if with_excess:
        excess = [((grid * (0.0007 + 0.0003 * c)) % 0.2).astype(np.float32) for c in range(len(planes))]
    t.apply(planes, excess)
    return b"".join(np.ascontiguousarray(p).tobytes() for p in planes) + repr(t.stats).encode()


#: SHA-256 of _hdr_case_bytes for each (mode, colour, gray, excess) case; recorded before the loop-3 refactor.
HDR_CHARACTERIZATION = {
    "pq-srgb-rgb-excess": "747bbd04a8c8818a56f33a11180b14b3d4c6119f903c47321575aeae3079b690",
    "pq-srgb-gray-excess": "46033e1277129cf1c8629d194dcda527b39ff23146d0effd4a18e9dbd0ca649a",
    "hlg-prophoto-rgb-noexcess": "0191a3269e194c42cbd837261a4c5c7c3940c098356397900d6d6db47172761f",
    "hlg-gray-excess": "92b630738bd9c6acebdcb01f163797bf78ea18808ee793c9e4d757af0c498e0c",
}


def _hdr_case_cases():
    return [
        ("pq-srgb-rgb-excess", "pq", SRGB_COLOUR, False, True),
        ("pq-srgb-gray-excess", "pq", SRGB_COLOUR, True, True),
        ("hlg-prophoto-rgb-noexcess", "hlg", PROPHOTO_COLOUR, False, False),
        ("hlg-gray-excess", "hlg", GRAY_COLOUR, True, True),
    ]


@pytest.mark.parametrize("name, mode, colour_desc, gray, with_excess", _hdr_case_cases())
def test_hdr_apply_bytes_are_characterized(name, mode, colour_desc, gray, with_excess):
    import hashlib

    digest = hashlib.sha256(_hdr_case_bytes(mode, colour_desc, gray, with_excess)).hexdigest()
    assert digest == HDR_CHARACTERIZATION[name], f"{name}: {digest}"
