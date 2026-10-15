"""Acceptance: the stage cache (keys equal Loom's, store and verify, hit resolution, folders).

Black box through ``loomsiril.loom.cache``. Skipped until that module exists. Expected values are literals recorded
once from Loom's JavaScript (lib/Cache.js, lib/Pipeline.js) or derived here with ``hashlib``.
"""

from __future__ import annotations

import itertools
import os
import random
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from loom_acc_helpers import chain_key as ref_chain_key
from loom_acc_helpers import import_loom, iso_ms, js_json, params_string as ref_params_string, sha1_hex

cache = pytest.importorskip("loomsiril.loom.cache")

STAGE_ORDER = [
    "solve", "spfc", "mgc", "graxpert", "aberration", "register", "combine", "solveRGB", "spfcRGB", "spccRGB",
    "sharpenRGB", "extractRGB", "denoiseLinearRGB", "stretchRGB", "denoiseRGB", "paletteCombine", "paletteSpcc",
    "paletteNorm", "paletteSharpen", "paletteExtract", "paletteDenoiseLinear", "paletteStretch", "paletteDenoise",
    "extractL", "denoiseLinearL", "stretchL", "denoiseL",
]
COMPANIONS = {
    "extractL": "stars", "extractRGB": "stars", "paletteExtract": "stars",
    "stretchRGB": "linear", "paletteStretch": "linear",
}

COMMON = {"x0": 1, "y0": 2, "x1": 300, "y1": 400}


# ---------------------------------------------------------------- keys


def test_stage_order_and_companions_are_loom_s() -> None:
    assert list(cache.STAGE_ORDER) == STAGE_ORDER
    assert dict(cache.STAGE_COMPANIONS) == COMPANIONS
    assert cache.FORMAT_VERSION == "1"


@pytest.mark.parametrize(
    "text,digest",
    [
        ("", "da39a3ee5e6b4b0d3255bfef95601890afd80709"),
        ("abc", "a9993e364706816aba3e25717850c26c9cd0d89d"),
        ("Ha \u00b0 \u00e9", "b608902ec20da91b0083f0f96b13fa8d56869417"),
    ],
)
def test_hash_is_sha1_of_utf8(text: str, digest: str) -> None:
    assert cache.hash_str(text) == digest == sha1_hex(text)


PARAM_CASES = [
    (None, ""),
    ({}, ""),
    ({"b": 2, "a": "x"}, "a=x;b=2"),
    ({"z": {"y": 1, "x": [1, 2]}, "a": True, "n": None}, 'a=true;n=null;z={"y":1,"x":[1,2]}'),
    ({"f": 1.0, "g": 0.5, "h": 0.1 + 0.2, "i": 1e21, "j": 1e-7, "k": -0.0},
     "f=1;g=0.5;h=0.30000000000000004;i=1e+21;j=1e-7;k=0"),
    ({"q": 'a"b\\c', "u": "\u00e9\u00b0"}, 'q=a"b\\c;u=\u00e9\u00b0'),
    ({"o": {"n": -0.0, "f": 1.5, "s": 'x"y'}}, 'o={"n":0,"f":1.5,"s":"x\\"y"}'),
]


@pytest.mark.parametrize("params,expected", PARAM_CASES)
def test_params_string_matches_loom(params, expected) -> None:
    assert cache.params_string(params) == expected
    assert ref_params_string(params) == expected  # the helper agrees with the recorded value too


def test_params_string_sorts_top_level_only() -> None:
    a = {"outer": {"b": 1, "a": 2}, "alpha": 1}
    assert cache.params_string(a) == 'alpha=1;outer={"b":1,"a":2}'
    b = {"outer": {"a": 2, "b": 1}, "alpha": 1}
    assert cache.params_string(b) != cache.params_string(a)  # nested order is significant, as in Loom


def test_js_json_stringify_basics() -> None:
    value = {"a": [1, 2.5, "x", None, True], "b": {"c": -0.0}, "s": 'q"\\'}
    assert cache.js_json_stringify(value) == js_json(value) == '{"a":[1,2.5,"x",null,true],"b":{"c":0},"s":"q\\"\\\\"}'


def test_chain_key_goldens() -> None:
    assert cache.chain_key(None, "solve", {"a": 1}) == "99d4214344d6372e7a1c4bdf165921f691d5dd9e"
    assert cache.chain_key("", "solve", {"a": 1}) == "99d4214344d6372e7a1c4bdf165921f691d5dd9e"
    p = {"transf": "homography", "interp": "lanczos4", "d": 0.5}
    assert cache.chain_key("abc", "register", p) == "41b279454c58a625e0622da881fbde958a7087c1"
    assert cache.chain_key("abc", "register", p) == ref_chain_key("abc", "register", p)


def test_keys_are_stable_and_insensitive_to_parameter_order() -> None:
    params = {"alpha": 1, "beta": "x", "gamma": 0.25, "delta": True, "eps": None}
    base = cache.chain_key("prev", "mgc", params)
    keys = list(params)
    rng = random.Random(7)
    for _ in range(10):
        rng.shuffle(keys)
        assert cache.chain_key("prev", "mgc", {k: params[k] for k in keys}) == base
    assert cache.chain_key("prev", "mgc", dict(params)) == base  # repeatable


@pytest.mark.parametrize("change", [
    ("prev", "other"), ("stage", "solve"), ("alpha", 2), ("beta", "y"), ("delta", False), ("eps", 0),
])
def test_any_input_change_changes_the_key(change) -> None:
    params = {"alpha": 1, "beta": "x", "delta": True, "eps": None}
    base = cache.chain_key("prev", "mgc", params)
    name, value = change
    if name == "prev":
        assert cache.chain_key(value, "mgc", params) != base
    elif name == "stage":
        assert cache.chain_key("prev", value, params) != base
    else:
        assert cache.chain_key("prev", "mgc", {**params, name: value}) != base


@pytest.mark.parametrize("ms,iso,digest", [
    (0, "1970-01-01T00:00:00.000Z", "1396626f820e0da01aa59cb515bc949bb5bec743"),
    (951782400000, "2000-02-29T00:00:00.000Z", "6917a937c05128c3b6281ba5375ea7178fcb9265"),
])
def test_file_source_key_goldens(ms, iso, digest) -> None:
    assert cache.iso_ms(ms / 1000) == iso
    assert cache.file_source_key("/data/M42_L.fits", 123456, ms / 1000) == digest


def test_file_source_key_formula_and_iso_truncation() -> None:
    assert cache.iso_ms(0.9999) == "1970-01-01T00:00:00.999Z"
    assert cache.iso_ms(1700000000.5) == "2023-11-14T22:13:20.500Z"
    for mtime in (1700000000.5, 1700000000.25, 1234567890.0):
        iso = iso_ms(mtime)
        assert cache.iso_ms(mtime) == iso
        assert cache.file_source_key("C:\\data\\x.fits", 99, mtime) == sha1_hex("file|C:\\data\\x.fits|99|" + iso)
    a = cache.file_source_key("/d/a.fits", 10, 5.0)
    assert cache.file_source_key("/d/a.fits", 11, 5.0) != a
    assert cache.file_source_key("/d/a.fits", 10, 6.0) != a
    assert cache.file_source_key("/d/b.fits", 10, 5.0) != a


BIG_PARAMS = {
    "solve": {"a": 1}, "mgc": {"b": 2}, "graxpert": {"c": 3}, "register": {"d": 4},
    "stretchRGB": {"keepLinear": True, "m": 1}, "extractRGB": {"e": 1}, "stretchL": {"keepLinear": False},
    "bogus": {"x": 1}, "extractL": {},
}


def test_build_stage_keys_matches_loom() -> None:
    chain = cache.build_stage_keys("SRC", BIG_PARAMS)
    got = [(e["stage"], e["key"], e["params"], e.get("companion")) for e in chain]
    assert got == [
        ("solve", "e8d94e24ce7a1a2fb64b83d5b5e115580f976a9b", {"a": 1}, None),
        ("mgc", "62cdbcdad9567305342e3002a00f94da9376aa14", {"b": 2}, None),
        ("graxpert", "de45cd557d0aa4345d7ce809eb99d1e8bac3e3d8", {"c": 3}, None),
        ("register", "827e30f7dab5670797166b3c6cb10e332b871817", {"d": 4}, None),
        ("extractRGB", "2e499e71acf03c93d8175753a6d1356d1296f136", {"e": 1}, "stars"),
        ("stretchRGB", "1e01deb8cb79384be668d444da9338624eeeab32", {"keepLinear": True, "m": 1}, "linear"),
        ("extractL", "6254aca7996aab47f75f90466be8ea7cb8ef973c", {}, "stars"),
        ("stretchL", "2add4fc245aeb286e9c9c58fbfcbf5d34bc2b0f3", {"keepLinear": False}, None),
    ]


def test_build_stage_keys_chain_and_order() -> None:
    chain = cache.build_stage_keys("SRC", BIG_PARAMS)
    prev = "SRC"
    stages = []
    for e in chain:
        assert e["key"] == ref_chain_key(prev, e["stage"], e["params"])
        prev = e["key"]
        stages.append(e["stage"])
    assert stages == [s for s in STAGE_ORDER if s in BIG_PARAMS]  # pipeline order, unknown stage ignored
    assert cache.build_stage_keys("SRC", None) == []
    assert cache.build_stage_keys("SRC", {}) == []


def test_linear_companion_only_when_keep_linear_is_truthy() -> None:
    for keep, expected in ((True, "linear"), (False, None), (None, None)):
        chain = cache.build_stage_keys("S", {"stretchRGB": {"keepLinear": keep}})
        assert chain[0].get("companion") == expected
    assert cache.build_stage_keys("S", {"stretchRGB": None})[0].get("companion") is None
    assert cache.build_stage_keys("S", {"extractL": None})[0]["companion"] == "stars"


def test_changed_parameter_invalidates_that_stage_and_everything_after_only() -> None:
    base = cache.build_stage_keys("SRC", BIG_PARAMS)
    for i, entry in enumerate(base):
        changed = {**BIG_PARAMS, entry["stage"]: {**(entry["params"] or {}), "touched": 1}}
        other = cache.build_stage_keys("SRC", changed)
        assert [e["stage"] for e in other] == [e["stage"] for e in base]
        assert [e["key"] for e in other[:i]] == [e["key"] for e in base[:i]], "upstream keys must not move"
        assert all(o["key"] != b["key"] for o, b in zip(other[i:], base[i:])), "this stage and later ones must move"


def test_source_change_invalidates_everything() -> None:
    a = cache.build_stage_keys("SRC1", BIG_PARAMS)
    b = cache.build_stage_keys("SRC2", BIG_PARAMS)
    assert all(x["key"] != y["key"] for x, y in zip(a, b))


def test_tags_and_derived_source_keys_match_loom() -> None:
    assert cache.crop_tag(COMMON) == "|crop:1,2,300,400"
    assert cache.crop_halos_tag(COMMON, True) == "|crop:1,2,300,400|halos:1"
    assert cache.crop_halos_tag(COMMON, False) == "|crop:1,2,300,400|halos:0"
    cur = {"R": "cr", "G": "cg", "B": "cb", "L": "cl"}
    assert cache.rgb_source_key(cur, COMMON, True) == "f32cf4dedeb300ffdeb124fd66c42375e4f414b1"
    assert cache.palette_source_key("SHO", ["cr", "cg", "cb"], COMMON, False) == "c1db00824d9bcac246903fa368e00c1d96efe269"
    assert cache.lstars_source_key("cl", COMMON, False) == "97ced09314b1a3beed4823f0953118d52cac6749"
    # halos flag and crop both take part
    assert cache.rgb_source_key(cur, COMMON, False) != cache.rgb_source_key(cur, COMMON, True)
    assert cache.rgb_source_key(cur, {**COMMON, "x1": 301}, True) != cache.rgb_source_key(cur, COMMON, True)


def test_clean_white_balance_key_matches_loom_and_checks_inputs() -> None:
    keys = {"R": "kr", "G": "kg", "B": "kb"}
    # Loom's key text: cleanwb|R|G|B + crop + filters JSON + instrument + |ref:<reference key>
    assert cache.clean_white_balance_key(keys, COMMON, {}, "ZWO ASI2600MM Pro", "kref") == \
        "2eed33ed3e9cf282ffe1556c86feb94c25cb219f"
    filters = {"R": "Astronomik Red", "G": "Astronomik Green"}
    assert cache.clean_white_balance_key(keys, COMMON, filters, None, "kref") == "82a622958346e80b2895f0862144ce691d99d90e"
    assert cache.clean_white_balance_key(keys, COMMON, {}, "ZWO ASI2600MM Pro", "kref2") != \
        cache.clean_white_balance_key(keys, COMMON, {}, "ZWO ASI2600MM Pro", "kref")
    for missing in ("R", "G", "B"):
        broken = {**keys, missing: ""}
        with pytest.raises(ValueError, match="no pre-correction cache key for " + missing):
            cache.clean_white_balance_key(broken, COMMON, {}, "x", "kref")


# ---------------------------------------------------------------- hit resolution


def _chain(n: int, companions: dict[int, str] | None = None) -> list[dict]:
    companions = companions or {}
    out = []
    for i in range(n):
        e = {"stage": f"s{i}", "key": f"k{i}", "params": {}}
        if i in companions:
            e["companion"] = companions[i]
        out.append(e)
    return out


def _resolve(chain, lookups, complete=None, loadable=None, comp_ok=None):
    complete = set(range(len(chain))) if complete is None else complete
    loadable = set(range(len(chain))) if loadable is None else loadable
    comp_ok = set(range(len(chain))) if comp_ok is None else comp_ok
    idx = {e["key"]: i for i, e in enumerate(chain)}
    return cache.resolve_cache_hit(
        chain,
        lookups,
        lambda e: idx[e["key"]] in complete,
        lambda e: ("img", e["key"]) if idx[e["key"]] in loadable else None,
        lambda e: ("stars", e["key"]) if idx[e["key"]] in comp_ok else None,
    )


def test_hit_resolution_latest_complete_entry_wins() -> None:
    chain = _chain(4)
    assert _resolve(chain, [True, True, True, True])[0] == 3
    assert _resolve(chain, [True, True, False, False])[0] == 1
    assert _resolve(chain, [False, True, False, True])[0] == 3
    assert _resolve(chain, [False] * 4) == (-1, None, None)
    assert _resolve([], []) == (-1, None, None)


def test_hit_resolution_falls_back_never_forward() -> None:
    chain = _chain(4)
    # the latest exists but is half written: use the one before
    assert _resolve(chain, [True] * 4, complete={0, 1, 2})[0] == 2
    # the latest does not load: use the one before
    idx, loaded, comp = _resolve(chain, [True] * 4, loadable={0, 1})
    assert idx == 1 and loaded == ("img", "k1") and comp is None
    # nothing usable at all: run everything
    assert _resolve(chain, [True] * 4, loadable=set()) == (-1, None, None)


def test_hit_resolution_companion_rules() -> None:
    chain = _chain(3, {1: "stars", 2: "linear"})
    idx, loaded, comp = _resolve(chain, [True] * 3)
    assert (idx, loaded, comp) == (2, ("img", "k2"), ("stars", "k2"))
    # the companion cannot be read: step back to the entry before it
    idx, loaded, comp = _resolve(chain, [True] * 3, comp_ok={0})
    assert idx == 0 and comp is None
    # an entry without a companion never asks for one
    asked: list[str] = []
    cache.resolve_cache_hit(chain, [True, False, False], lambda e: True, lambda e: object(),
                            lambda e: asked.append(e["key"]))
    assert asked == []


def test_hit_resolution_does_not_load_entries_it_skips() -> None:
    chain = _chain(3)
    loaded: list[str] = []
    cache.resolve_cache_hit(chain, [True, True, True], lambda e: True,
                            lambda e: loaded.append(e["key"]) or object(), lambda e: None)
    assert loaded == ["k2"]


# ---------------------------------------------------------------- store


def _planes(seed: int = 3, n: int = 3, h: int = 6, w: int = 9) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [rng.random((h, w)).astype(np.float32) * 3 - 1 for _ in range(n)]


def _files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_store_round_trip_is_bit_identical(tmp_path: Path) -> None:
    store = cache.CacheStore(str(tmp_path))
    planes = _planes()
    key = "a" * 40
    assert store.lookup(key) is None
    path = store.store(key, planes, {"FILTER": "R", "OBJECT": "M42"}, {"channel": "R", "stage": "solve", "params": {"a": 1}})
    assert path == store.path_for(key) == str(tmp_path / (key + ".fits"))
    assert store.lookup(key) == path
    got, header = store.load(key)
    assert len(got) == 3
    for a, b in zip(planes, got):
        assert b.dtype == np.float32 and b.shape == a.shape
        assert a.tobytes() == b.tobytes()
    assert header["FILTER"] == "R"
    assert _files(tmp_path) == sorted([key + ".fits", key + ".json"])  # no .part left behind
    assert store.entry_complete({"key": key, "companion": None}) is True


def test_sidecar_describes_the_entry(tmp_path: Path) -> None:
    import json

    store = cache.CacheStore(str(tmp_path))
    meta = {"channel": "L", "stage": "mgc", "params": {"x": 1}}
    store.store("b" * 40, _planes(n=1), {}, meta)
    side = json.loads((tmp_path / ("b" * 40 + ".json")).read_text(encoding="utf-8"))
    assert side == meta
    assert store.meta_path_for("b" * 40) == str(tmp_path / ("b" * 40 + ".json"))


def test_failed_readback_leaves_no_entry_and_no_part_file(tmp_path: Path, monkeypatch, caplog) -> None:
    from loomsiril import io_fits

    store = cache.CacheStore(str(tmp_path), log=lambda level, text: logged.append((level, text)))
    logged: list = []

    def broken(*a, **k):
        raise OSError("cannot read back")

    monkeypatch.setattr(io_fits, "read_fits", broken)
    monkeypatch.setattr(cache, "read_fits", broken, raising=False)
    key = "c" * 40
    assert store.store(key, _planes(), {}, {"channel": "R", "stage": "s", "params": {}}) is None
    assert _files(tmp_path) == []
    assert store.lookup(key) is None
    assert logged and "discard" in logged[-1][1].lower()


def test_wrong_geometry_on_readback_is_discarded(tmp_path: Path, monkeypatch) -> None:
    from loomsiril import io_fits

    real = io_fits.read_fits

    def shrunk(path):
        planes, header = real(path)
        return [p[:-1] for p in planes], header

    monkeypatch.setattr(io_fits, "read_fits", shrunk)
    monkeypatch.setattr(cache, "read_fits", shrunk, raising=False)
    store = cache.CacheStore(str(tmp_path))
    assert store.store("d" * 40, _planes(), {}, {"channel": "R", "stage": "s", "params": {}}) is None
    assert [n for n in _files(tmp_path) if n.endswith(".fits") or n.endswith(".part")] == []


def test_corrupt_entry_is_deleted_and_reported_missing(tmp_path: Path) -> None:
    store = cache.CacheStore(str(tmp_path))
    key = "e" * 40
    store.store(key, _planes(), {}, {"channel": "R", "stage": "s", "params": {}})
    Path(store.path_for(key)).write_bytes(b"this is not a FITS file")
    assert store.load(key) is None
    assert not Path(store.path_for(key)).exists()
    assert store.load("f" * 40) is None  # a missing key is simply None


def test_truncated_entry_is_discarded(tmp_path: Path) -> None:
    store = cache.CacheStore(str(tmp_path))
    key = "1" * 40
    store.store(key, _planes(), {}, {"channel": "R", "stage": "s", "params": {}})
    p = Path(store.path_for(key))
    p.write_bytes(p.read_bytes()[:3000])
    assert store.load(key) is None
    assert not p.exists()


def test_companions_round_trip_and_complete_entries(tmp_path: Path) -> None:
    store = cache.CacheStore(str(tmp_path))
    key = "2" * 40
    store.store(key, _planes(1), {}, {"channel": "R", "stage": "extractRGB", "params": {}})
    entry = {"key": key, "companion": "stars"}
    assert store.entry_complete(entry) is False  # stage image without its companion is a half entry
    stars = _planes(9)
    path = store.store_companion(key, "stars", stars, {})
    assert path == store.companion_path_for(key, "stars") == str(tmp_path / (key + ".stars.fits"))
    assert store.entry_complete(entry) is True
    got, _ = store.load_companion(key, "stars")
    assert all(a.tobytes() == b.tobytes() for a, b in zip(stars, got))
    assert store.load_companion(key, "linear") is None
    assert store.entry_complete({"key": "9" * 40, "companion": None}) is False


def test_white_balance_factors_round_trip(tmp_path: Path) -> None:
    store = cache.CacheStore(str(tmp_path))
    key = "3" * 40
    assert store.read_wb(key) is None
    store.write_wb(key, [1.0, 0.8125, 1.25])
    assert store.read_wb(key) == [1.0, 0.8125, 1.25]
    assert store.wb_path_for(key) == str(tmp_path / (key + ".wb.json"))


def test_total_bytes_and_clear_keep_subfolders(tmp_path: Path) -> None:
    store = cache.CacheStore(str(tmp_path))
    store.store("4" * 40, _planes(), {}, {"channel": "R", "stage": "s", "params": {}})
    store.write_wb("5" * 40, [1, 1, 1])
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "run.log").write_text("keep me", encoding="utf-8")
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "scratch.fits").write_bytes(b"x" * 100)
    top = sum(p.stat().st_size for p in tmp_path.iterdir() if p.is_file())
    assert store.total_bytes() == top > 0
    assert store.clear() == top
    assert [p.name for p in tmp_path.iterdir()] == ["logs", "work"] or sorted(p.name for p in tmp_path.iterdir()) == ["logs", "work"]
    assert (tmp_path / "logs" / "run.log").read_text(encoding="utf-8") == "keep me"
    assert (tmp_path / "work" / "scratch.fits").exists()
    assert store.total_bytes() == 0


def test_second_run_hits_the_latest_stage(tmp_path: Path) -> None:
    """End to end: store the stages of a first run, rebuild the keys, the second run resumes from the last stage."""
    store = cache.CacheStore(str(tmp_path))
    params = {"solve": {"a": 1}, "graxpert": {"b": 2}, "register": {"c": 3}}
    src = cache.file_source_key("/data/R.fits", 1000, 1700000000.5)
    first = cache.build_stage_keys(src, params)
    for i, e in enumerate(first):
        store.store(e["key"], _planes(i, n=1), {}, {"channel": "R", "stage": e["stage"], "params": e["params"]})

    def resume(chain):
        return cache.resolve_cache_hit(chain, [store.lookup(e["key"]) is not None for e in chain],
                                       store.entry_complete, lambda e: store.load(e["key"]), lambda e: None)[0]

    assert resume(cache.build_stage_keys(src, params)) == 2
    changed = {**params, "register": {"c": 4}}
    assert resume(cache.build_stage_keys(src, changed)) == 1  # only the changed stage reruns
    changed = {**params, "solve": {"a": 9}}
    assert resume(cache.build_stage_keys(src, changed)) == -1  # everything after a changed stage reruns
    assert resume(cache.build_stage_keys(cache.file_source_key("/data/R.fits", 1001, 1700000000.5), params)) == -1


# ---------------------------------------------------------------- folders


def test_default_cache_dir_per_platform_is_never_a_temp_folder() -> None:
    mac = cache.default_cache_dir("darwin", {}, "/Users/me")
    assert mac == "/Users/me/Library/Application Support/loomsiril/cache"
    lin = cache.default_cache_dir("linux", {}, "/home/loom")
    assert lin == "/home/loom/.local/share/loomsiril/cache"
    assert cache.default_cache_dir("linux", {"XDG_DATA_HOME": "/xdg"}, "/home/loom") == "/xdg/loomsiril/cache"
    win = cache.default_cache_dir("win32", {"LOCALAPPDATA": "C:\\Users\\me\\AppData\\Local"}, "C:\\Users\\me")
    assert win.replace("/", "\\") == "C:\\Users\\me\\AppData\\Local\\loomsiril\\cache"
    win2 = cache.default_cache_dir("win32", {}, "C:\\Users\\me")
    assert win2.replace("/", "\\") == "C:\\Users\\me\\AppData\\Local\\loomsiril\\cache"
    for d in (mac, lin, win, win2):
        low = d.lower()
        assert "/tmp" not in low and "\\temp" not in low and "/var/folders" not in low


def test_folder_missing_only_for_a_chosen_nonexistent_folder(tmp_path: Path) -> None:
    assert cache.folder_missing("") is False  # default folder: created on demand, never "missing"
    assert cache.folder_missing(str(tmp_path)) is False
    assert cache.folder_missing(str(tmp_path / "nope")) is True


def test_low_space_threshold_is_twenty_gib(tmp_path: Path, monkeypatch) -> None:
    assert cache.LOW_SPACE_BYTES == 20 * 1024 ** 3
    store = cache.CacheStore(str(tmp_path / "not-created-yet"))
    Usage = shutil._ntuple_diskusage

    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10**12, 10**12 - 5 * 1024**3, 5 * 1024**3))
    assert store.free_bytes() == 5 * 1024**3
    assert store.low_space() == 5 * 1024**3
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10**12, 0, 40 * 1024**3))
    assert store.low_space() is None


# ---------------------------------------------------------------- budget


def test_key_computation_for_a_full_option_set_is_fast() -> None:
    """Budget: a full option set (every stage) keys in under 50 ms; the best of three runs absorbs a busy machine."""
    params = {s: {"tool": "x", "level": "medium", "amount": 0.5, "stretched": False} for s in STAGE_ORDER}
    best = 1e9
    for i in range(3):
        t0 = time.perf_counter()
        chain = cache.build_stage_keys("SRC%d" % i, params)
        cache.rgb_source_key({"R": "a", "G": "b", "B": "c"}, COMMON, True)
        best = min(best, time.perf_counter() - t0)
        assert len(chain) == len(STAGE_ORDER)
    assert best < 0.05, f"{best * 1000:.1f} ms for {len(STAGE_ORDER)} chain keys"
