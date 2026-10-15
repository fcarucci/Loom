"""The logo layer (loomsiril/logo.py): the maths against the JS oracle, the readers, the frame, the signatures, the draft.

Oracle: tests/oracle/logo.json, recorded from Fly.logoFade / logoRect and Render.logoLayer / addLogo by
tools/oracle/logo.js. The readers are checked on tiny synthetic PNG (every filter, every colour type and depth, palette,
tRNS, interlaced), TIFF and FITS files written here.
"""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import tifffile

from loomsiril import draft as D
from loomsiril import io_fits
from loomsiril import logo as L
from loomsiril import pipeline as P
from loomsiril import presets
from loomsiril.frame import RenderOptions, frame_image
from loomsiril.sprites import Camera, FrameTarget

import synth
from test_draft import PRESET, _job, _options

TOL = 1e-6


# ---- the maths against the oracle -----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lo(oracle: Any) -> Any:
    return oracle("logo")["cases"]


def test_the_constants_are_the_js(lo: Any) -> None:
    c = lo["constants"]
    assert c["LOGO_PLACES"] == L.LOGO_PLACES and c["LOGO_SIZE"] == L.LOGO_SIZE and c["LOGO_MARGIN"] == L.LOGO_MARGIN


def test_logo_fade_matches_the_js(lo: Any) -> None:
    for c in lo["logoFade"]:
        assert L.logo_fade(c["seconds"], c["delay"]) == pytest.approx(c["out"], abs=1e-12), c


def test_logo_rect_matches_the_js(lo: Any) -> None:
    assert len(lo["logoRect"]) == 45
    for c in lo["logoRect"]:
        got = L.logo_rect(c["W"], c["H"], c["lw"], c["lh"], c["place"])
        if c["out"] is None:
            assert got is None, c
        else:
            assert got == pytest.approx(c["out"], abs=1e-12), c


def _logo_of(case: dict[str, Any]) -> L.LogoImage:
    s = case["spec"]
    planes = [np.asarray(p, dtype=np.float32).reshape(s["h"], s["w"]) for p in case["colour"]]
    alpha = None if case["alpha"] is None else np.asarray(case["alpha"], dtype=np.float32).reshape(s["h"], s["w"])
    return L.LogoImage(planes, alpha)


def test_logo_layer_matches_the_js(lo: Any) -> None:
    for case in lo["logoLayer"]:
        s = case["spec"]
        got = L.logo_layer(_logo_of(case), s["W"], s["H"], s["place"], s["channels"], s["opacity"])
        exp = case["out"]
        if exp is None:
            assert got is None, s["name"]
            continue
        assert (got.x, got.y, got.w, got.h) == (exp["x"], exp["y"], exp["w"], exp["h"]), s["name"]
        assert len(got.p) == s["channels"]
        for c, plane in enumerate(got.p):
            np.testing.assert_allclose(plane.ravel(), exp["p"][c], atol=TOL, err_msg=s["name"])
        np.testing.assert_allclose(got.a.ravel(), exp["a"], atol=TOL, err_msg=s["name"])


def test_add_logo_matches_the_js(lo: Any) -> None:
    a = lo["addLogo"]
    fw, fh, src = a["frame"]["W"], a["frame"]["H"], a["source"]
    layer = L.logo_layer(L.LogoImage([np.asarray(p, dtype=np.float32).reshape(src["h"], src["w"]) for p in src["colour"]],
                                     np.asarray(src["alpha"], dtype=np.float32).reshape(src["h"], src["w"])),
                         fw, fh, src["place"], 3, src["opacity"])
    for case in a["cases"]:
        out = [np.asarray(p, dtype=np.float32).reshape(fh, fw) for p in a["frame"]["planes"]]
        exc = [np.asarray(p, dtype=np.float32).reshape(fh, fw) for p in a["frame"]["excess"]]
        L.add_logo(layer, out, exc, case["fade"])
        for c in range(3):
            np.testing.assert_allclose(out[c].ravel(), case["out"][c], atol=TOL, err_msg=str(case["fade"]))
            np.testing.assert_allclose(exc[c].ravel(), case["excess"][c], atol=TOL, err_msg=str(case["fade"]))
        # SDR: no excess planes, the same picture
        sdr = [np.asarray(p, dtype=np.float32).reshape(fh, fw) for p in a["frame"]["planes"]]
        L.add_logo(layer, sdr, [None, None, None], case["fade"])
        for c in range(3):
            np.testing.assert_array_equal(sdr[c], out[c])


def test_a_logo_of_a_shape_other_than_the_frames_keeps_its_aspect() -> None:
    r = L.logo_rect(1920, 1080, 400, 100, "bottom-right")
    assert (r["w"], r["h"]) == (216, 54) and r["x"] == 1920 - 43 - 216 and r["y"] == 1080 - 43 - 54


# ---- PNG / TIFF / FITS --------------------------------------------------------------------------------------------------------


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _filtered(rows: list[bytes], bpp: int, filters: list[int]) -> bytes:
    """Scanlines with the PNG filter of each row applied (the encoder side of loomsiril.logo._unfilter)."""
    out = b""
    prev = bytes(len(rows[0]))
    for row, ft in zip(rows, filters):
        r, p = list(row), list(prev)
        f = []
        for i, x in enumerate(r):
            a = r[i - bpp] if i >= bpp else 0
            b = p[i]
            c = p[i - bpp] if i >= bpp else 0
            if ft == 0:
                pred = 0
            elif ft == 1:
                pred = a
            elif ft == 2:
                pred = b
            elif ft == 3:
                pred = (a + b) >> 1
            else:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            f.append((x - pred) & 255)
        out += bytes([ft]) + bytes(f)
        prev = row
    return out


def _png(path: Path, w: int, h: int, ctype: int, depth: int, rows: list[bytes], extra: list[bytes] | None = None,
         filters: list[int] | None = None) -> None:
    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = max(1, nch * depth // 8)
    data = _filtered(rows, bpp, filters or [i % 5 for i in range(h)])
    ihdr = struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + b"".join(extra or []) + _chunk(b"IDAT", zlib.compress(data))
                     + _chunk(b"IEND", b""))


def test_png_rgba_8_every_filter_and_alpha(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    arr = rng.integers(0, 256, (7, 5, 4), dtype=np.uint8)
    p = tmp_path / "a.png"
    _png(p, 5, 7, 6, 8, [arr[y].tobytes() for y in range(7)])
    img = L.read_logo(p)
    assert (img.width, img.height) == (5, 7) and len(img.planes) == 3 and img.alpha is not None
    for c in range(3):
        np.testing.assert_allclose(img.planes[c], arr[..., c] / 255.0, atol=1e-7)
    np.testing.assert_allclose(img.alpha, arr[..., 3] / 255.0, atol=1e-7)


def test_png_grey_16_and_grey_alpha_and_rgb(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    g16 = rng.integers(0, 65536, (4, 6), dtype=np.uint16)
    _png(tmp_path / "g.png", 6, 4, 0, 16, [g16[y].astype(">u2").tobytes() for y in range(4)])
    img = L.read_logo(tmp_path / "g.png")
    assert len(img.planes) == 1 and img.alpha is None
    np.testing.assert_allclose(img.planes[0], g16 / 65535.0, atol=1e-7)
    ga = rng.integers(0, 256, (3, 3, 2), dtype=np.uint8)
    _png(tmp_path / "ga.png", 3, 3, 4, 8, [ga[y].tobytes() for y in range(3)])
    img = L.read_logo(tmp_path / "ga.png")
    np.testing.assert_allclose(img.alpha, ga[..., 1] / 255.0, atol=1e-7)
    rgb = rng.integers(0, 256, (3, 4, 3), dtype=np.uint8)
    _png(tmp_path / "rgb.png", 4, 3, 2, 8, [rgb[y].tobytes() for y in range(3)])
    img = L.read_logo(tmp_path / "rgb.png")
    assert img.alpha is None
    np.testing.assert_allclose(img.planes[1], rgb[..., 1] / 255.0, atol=1e-7)


def test_png_palette_with_transparency_and_low_bit_depth(tmp_path: Path) -> None:
    pal = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    idx = np.array([[0, 1, 2, 3], [3, 2, 1, 0]], dtype=np.uint8)
    rows = [bytes([(int(r[0]) << 6) | (int(r[1]) << 4) | (int(r[2]) << 2) | int(r[3])]) for r in idx]   # 2 bits each
    _png(tmp_path / "p.png", 4, 2, 3, 2, rows, [_chunk(b"PLTE", pal), _chunk(b"tRNS", bytes([0, 128]))])
    img = L.read_logo(tmp_path / "p.png")
    assert img.planes[0][0].tolist() == [1.0, 0.0, 0.0, 1.0] and img.planes[2][0].tolist() == [0.0, 0.0, 1.0, 1.0]
    np.testing.assert_allclose(img.alpha[0], [0.0, 128 / 255, 1.0, 1.0], atol=1e-7)


def test_png_one_transparent_colour(tmp_path: Path) -> None:
    rows = [bytes([10, 20, 30, 200, 100, 50]), bytes([200, 100, 50, 10, 20, 30])]
    _png(tmp_path / "k.png", 2, 2, 2, 8, rows, [_chunk(b"tRNS", struct.pack(">HHH", 10, 20, 30))])
    img = L.read_logo(tmp_path / "k.png")
    assert img.alpha.tolist() == [[0.0, 1.0], [1.0, 0.0]]


def test_png_interlaced_matches_pillow(tmp_path: Path) -> None:
    pil = pytest.importorskip("PIL.Image")
    rng = np.random.default_rng(3)
    arr = rng.integers(0, 256, (9, 11, 4), dtype=np.uint8)
    p = tmp_path / "plain.png"
    pil.fromarray(arr, "RGBA").save(p)
    img = L.read_logo(p)
    np.testing.assert_allclose(img.planes[0], arr[..., 0] / 255.0, atol=1e-7)
    # an interlaced file, made by the encoder of PNG's own libpng port: Pillow cannot write Adam7, so write it by hand
    passes = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
    raw = b""
    for x0, y0, dx, dy in passes:
        sub = arr[y0::dy, x0::dx]
        if sub.size:
            raw += _filtered([sub[y].tobytes() for y in range(sub.shape[0])], 4, [0] * sub.shape[0])
    ihdr = struct.pack(">IIBBBBB", 11, 9, 8, 6, 0, 0, 1)
    q = tmp_path / "adam7.png"
    q.write_bytes(b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))
    img7 = L.read_logo(q)
    for c in range(3):
        np.testing.assert_array_equal(img7.planes[c], img.planes[c])
    np.testing.assert_array_equal(img7.alpha, img.alpha)


def test_tiff_rgba_16_grey_and_float(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    rgba = rng.integers(0, 65536, (5, 6, 4), dtype=np.uint16)
    tifffile.imwrite(tmp_path / "a.tif", rgba, photometric="rgb", extrasamples=["unassalpha"])
    img = L.read_logo(tmp_path / "a.tif")
    assert len(img.planes) == 3 and (img.width, img.height) == (6, 5)
    np.testing.assert_allclose(img.planes[2], rgba[..., 2] / 65535.0, atol=1e-6)
    np.testing.assert_allclose(img.alpha, rgba[..., 3] / 65535.0, atol=1e-6)
    grey = rng.random((4, 3), dtype=np.float32)
    tifffile.imwrite(tmp_path / "g.tiff", grey)
    img = L.read_logo(tmp_path / "g.tiff")
    assert len(img.planes) == 1 and img.alpha is None
    np.testing.assert_array_equal(img.planes[0], grey)
    rgb8 = rng.integers(0, 256, (4, 3, 3), dtype=np.uint8)
    tifffile.imwrite(tmp_path / "c.tif", rgb8, photometric="rgb")
    img = L.read_logo(tmp_path / "c.tif")
    np.testing.assert_allclose(img.planes[0], rgb8[..., 0] / 255.0, atol=1e-7)


def test_fits_is_read_top_down(tmp_path: Path) -> None:
    plane = np.linspace(0, 1, 6 * 4, dtype=np.float32).reshape(4, 6)
    io_fits.write_fits(tmp_path / "l.fits", [plane], bitpix=-32)
    img = L.read_logo(tmp_path / "l.fits")
    assert len(img.planes) == 1 and img.alpha is None
    np.testing.assert_array_equal(img.planes[0], plane)


def test_the_file_decides_not_the_extension(tmp_path: Path) -> None:
    arr = np.full((2, 2, 3), 255, dtype=np.uint8)
    _png(tmp_path / "logo.dat", 2, 2, 2, 8, [arr[y].tobytes() for y in range(2)])
    assert L.read_logo(tmp_path / "logo.dat").planes[0].tolist() == [[1.0, 1.0], [1.0, 1.0]]


def test_the_errors_say_what_is_wrong(tmp_path: Path) -> None:
    with pytest.raises(L.LogoError, match="not there"):
        L.read_logo(tmp_path / "missing.png")
    jpg = tmp_path / "x.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 20)
    with pytest.raises(L.LogoError, match="PNG, TIFF or FITS"):
        L.read_logo(jpg)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"junk")
    with pytest.raises(L.LogoError):
        L.read_logo(bad)
    assert issubclass(L.LogoError, ValueError)


# ---- the options -----------------------------------------------------------------------------------------------------------


def test_settings_read_either_spelling_and_clamp() -> None:
    assert L.logo_settings({}) == {"path": "", "place": "off", "opacity": 1.0, "delay": 0.0}
    s = L.logo_settings({"logo_path": " /a.png ", "logo_place": "top-left", "logo_opacity": 40, "logo_delay": 3})
    assert s == {"path": "/a.png", "place": "top-left", "opacity": 0.4, "delay": 3.0}
    s = L.logo_settings({"logoPath": "/b.png", "logoPlace": "nowhere", "logoOpacity": 7, "logoDelay": -2})
    assert s == {"path": "/b.png", "place": "off", "opacity": 1.0, "delay": 0.0}


def _rgba_png(path: Path, rgba: tuple[int, int, int, int], w: int = 8, h: int = 4) -> Path:
    arr = np.tile(np.array(rgba, dtype=np.uint8), (h, w, 1))
    _png(path, w, h, 6, 8, [arr[y].tobytes() for y in range(h)])
    return path


def test_attach_reads_once_and_stamps(tmp_path: Path) -> None:
    f = _rgba_png(tmp_path / "l.png", (255, 255, 255, 255))
    opts: dict[str, Any] = {"logo_path": str(f), "logo_place": "bottom-right", "logo_opacity": 50, "logo_delay": 2}
    L.attach(opts)
    assert isinstance(opts["logoImage"], L.LogoImage) and opts["logoOpacity"] == 0.5 and opts["logoPlace"] == "bottom-right"
    assert opts["logoStamp"].startswith(str(f.resolve()) + "|")
    img = opts["logoImage"]
    L.attach(opts)
    assert opts["logoImage"] is img                                   # not read again
    opts["logo_place"] = opts["logoPlace"] = "off"
    L.attach(opts)
    assert "logoImage" not in opts and "logoStamp" not in opts
    with pytest.raises(L.LogoError):
        L.attach({"logoPath": str(tmp_path / "gone.png"), "logoPlace": "center"})
    L.attach({"logoPath": str(tmp_path / "gone.png"), "logoPlace": "off"})     # off: the file is not needed


def test_a_run_with_a_missing_logo_stops_before_the_scene(tmp_path: Path) -> None:
    field = synth.make_field(w=40, h=30, n_stars=3, nc=3, seed=1)
    job = P.FlyJob(starless=field.starless, stars=field.stars, wcs=field.wcs,
                   options={"logo_path": str(tmp_path / "gone.png"), "logo_place": "center"})
    with pytest.raises(ValueError, match="logo file is not there"):
        P.run(job)


# ---- the signatures: a changed logo renders again, an unchanged one reuses ------------------------------------------------


def _sig(opts: dict[str, Any]) -> str:
    o = {"duration": 2.0, "fps": 10, **opts}
    L.attach(o)
    return presets.frame_signature(o, {"w": 1920, "h": 1080, "pingPong": False, "crossfade": False}, "scene")


def test_the_frame_signature_follows_the_logo(tmp_path: Path) -> None:
    f = _rgba_png(tmp_path / "l.png", (255, 0, 0, 255))
    base = {"logoPath": str(f), "logoPlace": "bottom-right", "logoOpacity": 1.0, "logoDelay": 0.0}
    sig = _sig(base)
    assert _sig(dict(base)) == sig                                                         # unchanged: reused
    assert _sig({**base, "logoPlace": "top-left"}) != sig
    assert _sig({**base, "logoOpacity": 0.5}) != sig
    assert _sig({**base, "logoDelay": 3.0}) != sig
    assert _sig({**base, "logoPlace": "off"}) != sig
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))                        # the logo file touched
    touched = _sig(base)
    assert touched != sig
    _rgba_png(f, (255, 0, 0, 255), w=9)                                                     # the logo file changed size
    assert _sig(base) != touched
    assert "LogoImage" not in sig                                                           # the picture is not in the signature


def test_the_draft_key_follows_the_logo(tmp_path: Path) -> None:
    f = _rgba_png(tmp_path / "l.png", (255, 0, 0, 255))
    base = _options(tmp_path, logo_path=str(f), logo_place="bottom-right", logo_opacity=100, logo_delay=0)
    key = D.draft_key(base)
    assert D.draft_key(dict(base)) == key
    assert D.draft_key(_options(tmp_path)) != key                                          # no logo
    for change in ({"logo_place": "center"}, {"logo_opacity": 60}, {"logo_delay": 2}):
        assert D.draft_key({**base, **change}) != key
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))
    assert D.draft_key(base) != key
    key2 = D.draft_key(base)
    _rgba_png(f, (0, 255, 0, 255), w=10)
    assert D.draft_key(base) != key2


# ---- the frame ---------------------------------------------------------------------------------------------------------------


def _target(w: int, h: int) -> FrameTarget:
    return FrameTarget(cam=Camera(x=0, y=0, fx=1, fy=1), out_w=w, out_h=h)


def _planes(w: int, h: int, v: float, nc: int = 3) -> list[np.ndarray]:
    return [np.full((h, w), v, dtype=np.float32) for _ in range(nc)]


def test_frame_image_paints_the_logo_where_it_goes_and_nowhere_else() -> None:
    w, h = 96, 54
    white = L.LogoImage([np.ones((10, 20), np.float32)] * 3)
    layer = L.logo_layer(white, w, h, "bottom-right", 3, 1.0)
    got = frame_image(_planes(w, h, 0.2), _planes(w, h, 0.0), RenderOptions(logo=layer), _target(w, h), 0.5)
    inside = np.zeros((h, w), bool)
    inside[layer.y:layer.y + layer.h, layer.x:layer.x + layer.w] = True
    assert inside.sum() == layer.w * layer.h
    for p in got:
        np.testing.assert_allclose(p[inside], 1.0, atol=1e-6)
        np.testing.assert_allclose(p[~inside], 0.2, atol=1e-6)
    assert layer.x + layer.w == w - 2 and layer.y + layer.h == h - 2                       # 4 % of 54 = 2 px margin


def test_frame_image_opacity_and_own_transparency() -> None:
    w, h = 64, 36
    half = L.LogoImage([np.ones((8, 8), np.float32)] * 3, np.full((8, 8), 0.5, np.float32))
    layer = L.logo_layer(half, w, h, "center", 3, 0.5)             # alpha 0.5 x opacity 0.5 = 0.25
    got = frame_image(_planes(w, h, 0.4), _planes(w, h, 0.0), RenderOptions(logo=layer), _target(w, h), 0.0)
    y, x = layer.y + layer.h // 2, layer.x + layer.w // 2
    assert got[0][y, x] == pytest.approx(0.25 + 0.4 * 0.75, abs=1e-6)


def test_frame_image_fades_in_after_the_delay() -> None:
    w, h = 64, 36
    layer = L.logo_layer(L.LogoImage([np.ones((8, 8), np.float32)] * 3), w, h, "center", 3, 1.0)
    y, x = layer.y + 1, layer.x + 1
    base = RenderOptions(logo=layer, logo_delay=3.0, duration=10.0)
    shown = []
    for clock in (0.0, 0.3, 0.35, 0.4, 0.8):                         # 0, 3, 3.5, 4, 8 seconds in
        shown.append(float(frame_image(_planes(w, h, 0.0), _planes(w, h, 0.0), base, _target(w, h), clock)[0][y, x]))
    assert shown[0] == 0.0 and shown[1] == 0.0
    assert shown[2] == pytest.approx(L.logo_fade(3.5, 3.0)) and 0 < shown[2] < 1
    assert shown[3] == pytest.approx(1.0) and shown[4] == pytest.approx(1.0)
    # no delay: shown from the first frame
    first = frame_image(_planes(w, h, 0.0), _planes(w, h, 0.0), RenderOptions(logo=layer, duration=10.0), _target(w, h), 0.0)
    assert first[0][y, x] == pytest.approx(1.0)


def test_the_logo_goes_in_before_the_output_transform() -> None:
    from loomsiril.colour import SRGB_COLOUR, output_transform

    w, h = 64, 36
    layer = L.logo_layer(L.LogoImage([np.full((8, 8), 0.5, np.float32)] * 3), w, h, "center", 3, 1.0)
    out = output_transform(SRGB_COLOUR, "pq", {"peak": 1000.0})
    plain = frame_image(_planes(w, h, 0.0), _planes(w, h, 0.0), RenderOptions(logo=layer, output=out), _target(w, h), 0.0)
    out2 = output_transform(SRGB_COLOUR, "pq", {"peak": 1000.0})
    raw = frame_image(_planes(w, h, 0.5), _planes(w, h, 0.0), RenderOptions(output=out2), _target(w, h), 0.0)
    y, x = layer.y + 2, layer.x + 2
    assert plain[0][y, x] == pytest.approx(raw[0][y, x], abs=1e-6)       # the logo's 0.5 went through the same transform
    assert plain[0][0, 0] != plain[0][y, x]


def test_a_hdr_logo_covers_the_star_light_above_white() -> None:
    w, h = 64, 36
    layer = L.logo_layer(L.LogoImage([np.zeros((8, 8), np.float32)] * 3), w, h, "center", 3, 1.0)     # an opaque black logo
    from loomsiril.colour import SRGB_COLOUR, output_transform

    out = output_transform(SRGB_COLOUR, "pq", {"peak": 1000.0})
    got = frame_image(_planes(w, h, 0.1), _planes(w, h, 3.0), RenderOptions(logo=layer, output=out), _target(w, h), 0.0)
    y, x = layer.y + 2, layer.x + 2
    out2 = output_transform(SRGB_COLOUR, "pq", {"peak": 1000.0})
    black = frame_image(_planes(w, h, 0.0), _planes(w, h, 0.0), RenderOptions(output=out2), _target(w, h), 0.0)
    assert got[0][y, x] == pytest.approx(black[0][0, 0], abs=1e-6)        # no star light left under the logo
    assert got[0][0, 0] > got[0][y, x]                                    # while the rest of the frame still has it


# ---- the draft and the full render use it ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def field() -> Any:
    return synth.make_field(w=160, h=90, n_stars=10, nc=3, seed=5)


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory, field: Any) -> D.PreparedScene:
    return D.prepare_scene(_job(tmp_path_factory.mktemp("logodraft"), field))


def test_the_draft_has_the_logo_at_its_size_and_fades_it_in(prepared: D.PreparedScene, tmp_path: Path) -> None:
    f = _rgba_png(tmp_path / "w.png", (255, 255, 255, 255), w=40, h=20)
    plain = D.render_draft(prepared)
    with_logo = D.render_draft(prepared, {"logo_path": str(f), "logo_place": "bottom-right", "logo_opacity": 100, "logo_delay": 0})
    assert (with_logo.width, with_logo.height) == (plain.width, plain.height) == (480, 270)
    r = L.logo_rect(480, 270, 40, 20, "bottom-right")
    x, y, w, h = int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
    for a, b in zip(plain.frames, with_logo.frames):
        assert (b[y + 2:y + h - 2, x + 2:x + w - 2] == 255).all()      # the rim is the bilinear edge (zero outside, as the JS)
        mask = np.ones(a.shape[:2], bool)
        mask[y:y + h, x:x + w] = False
        assert np.array_equal(a[mask], b[mask])
    # opacity: halfway between the picture and white
    half = D.render_draft(prepared, {"logo_path": str(f), "logo_place": "bottom-right", "logo_opacity": 50})
    mid = half.frames[0][y + 2:y + h - 2, x + 2:x + w - 2].astype(int)
    base = plain.frames[0][y + 2:y + h - 2, x + 2:x + w - 2].astype(int)
    assert np.abs(mid - (base + 255) / 2).max() <= 1.5
    # delay: hidden at the start, in full from delay + 1 s on (the clip is 2 s)
    late = D.render_draft(prepared, {"logo_path": str(f), "logo_place": "bottom-right", "logo_delay": 1})
    assert np.array_equal(late.frames[0], plain.frames[0]) and (late.frames[-1][y + 2:y + h - 2, x + 2:x + w - 2] == 255).all()
    # off: nothing, whatever the file
    off = D.render_draft(prepared, {"logo_path": str(f), "logo_place": "off"})
    assert all(np.array_equal(a, b) for a, b in zip(plain.frames, off.frames))
    assert "logoImage" not in prepared.options                                             # the prepared scene is not changed


def test_the_final_frames_have_the_logo_and_reuse_follows_it(tmp_path: Path, prepared: D.PreparedScene) -> None:
    f = _rgba_png(tmp_path / "w.png", (255, 255, 255, 255), w=40, h=20)
    out_dir = tmp_path / "out"
    opts = dict(prepared.options, duration=0.3, fps=10, video=False, logoPath=str(f), logoPlace="top-left", logoOpacity=1.0)
    opts["colour"] = prepared.options["colour"]
    L.attach(opts)
    P.render_final(P.FinalRender(prepared.scene, [PRESET], opts, str(out_dir)), P.RunContext(lambda *a: None, lambda: False))
    frame = tifffile.imread(out_dir / "youtube_1080" / "frame_00000.tif")
    r = L.logo_rect(1920, 1080, 40, 20, "top-left")
    assert (frame[int(r["y"]) + 3:int(r["y"] + r["h"]) - 3, int(r["x"]) + 3:int(r["x"] + r["w"]) - 3] == 65535).all()
    # the same options again: the frames are reused; the logo file touched: rendered again
    res = P.render_final(P.FinalRender(prepared.scene, [PRESET], dict(opts), str(out_dir)), P.RunContext(lambda *a: None, lambda: False))
    assert res.reused == 3 and res.written == 0
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 9_000_000_000))
    again = dict(opts)
    again.pop("logoStamp")
    L.attach(again)
    res = P.render_final(P.FinalRender(prepared.scene, [PRESET], again, str(out_dir)), P.RunContext(lambda *a: None, lambda: False))
    assert res.written == 3 and res.reused == 0


def test_a_job_with_a_logo_gives_the_draft_its_logo(tmp_path: Path, field: Any) -> None:
    """The dialog's path: show_draft's make_inputs returns a FlyJob whose logo fields carry the Logo section's values."""
    f = _rgba_png(tmp_path / "w.png", (255, 255, 255, 255), w=40, h=20)
    job = _job(tmp_path, field, logo_path=str(f), logo_place="top-right", logo_opacity=1.0, logo_delay=0.0)
    drafted = D.render_draft(job)
    r = L.logo_rect(480, 270, 40, 20, "top-right")
    x, y, w, h = int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
    assert (drafted.frames[0][y + 2:y + h - 2, x + 2:x + w - 2] == 255).all()
    plain = D.render_draft(_job(tmp_path, field))
    assert not np.array_equal(plain.frames[0], drafted.frames[0])
    with pytest.raises(ValueError, match="logo file is not there"):
        D.render_draft(_job(tmp_path, field, logo_path=str(tmp_path / "gone.png"), logo_place="center"))
