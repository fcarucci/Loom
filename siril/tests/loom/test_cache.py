"""Stage-cache keys and the file-based cache store, pinned to Loom's JavaScript (tests/oracle/loom_cache.json)."""

from __future__ import annotations

import math
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import loomsiril.loom.cache as cache_mod
from loomsiril.loom.cache import (
    LOW_SPACE_BYTES,
    STAGE_COMPANIONS,
    STAGE_ORDER,
    CacheStore,
    build_stage_keys,
    chain_key,
    clean_white_balance_key,
    crop_halos_tag,
    crop_tag,
    default_cache_dir,
    file_source_key,
    folder_missing,
    hash_str,
    iso_ms,
    js_json_stringify,
    lstars_source_key,
    palette_source_key,
    params_string,
    resolve_cache_hit,
    rgb_source_key,
)

NAN = float("nan")
INF = float("inf")

COMMON = {"x0": 10, "y0": 20, "x1": 4000, "y1": 3000}

# The inputs of each oracle case, written as Python values (-0.0 is JS -0; NaN and Infinity are JS's).
PARAMS_INPUTS: dict[str, Any] = {
    "params_null": None,
    "params_empty": {},
    "params_sorted": {"b": 2, "a": "x"},
    "params_nested": {"z": {"y": [1, 2, {"q": "w"}], "x": None}, "a": [True, None, 1.5]},
    "params_booleans": {"t": True, "f": False},
    "params_null_value": {"n": None},
    "params_floats": {"one": 1.0, "half": 0.5, "sum": 0.1 + 0.2, "big": 1e21, "small": 1e-7, "negz": -0.0},
    "params_quotes": {"s": 'he said "hi" \\ ok', "n": {"s": 'q"b\\'}},
    "params_unicode": {"u": "\u00e9 \u00b0 \U0001F600", "n": {"u": "\u00e9"}},
    "params_numeric_keys": {"b": 1, "10": 2, "2": 3},
    "params_nested_numeric_keys": {"n": {"b": 1, "10": 2, "2": 3}},
    "params_nested_numbers": {"n": {"z": -0.0, "f": 1.0, "e": 1e21, "h": 0.5}},
    "params_nan_infinity": {"nan": NAN, "inf": INF, "ninf": -INF, "n": {"x": NAN, "y": INF}},
}


def _full_stage_params() -> dict[str, dict[str, Any]]:
    """One parameter object per stage of STAGE_ORDER, as a full option set would give."""
    return {stage: {"option": i, "name": stage} for i, stage in enumerate(STAGE_ORDER)}


# ---------------------------------------------------------------------------
# Constants and hashing
# ---------------------------------------------------------------------------


def test_stage_tables_match_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    assert list(STAGE_ORDER) == cases["stage_order"]
    assert len(STAGE_ORDER) == 27
    assert STAGE_COMPANIONS == cases["stage_companions"]
    assert cache_mod.FORMAT_VERSION == "1"
    assert LOW_SPACE_BYTES == 20 * 1024**3


def test_hash_matches_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    assert hash_str("") == cases["hash_empty"]
    assert hash_str("abc") == cases["hash_abc"]
    assert hash_str("Ha \u00b0 \u00e9") == cases["hash_unicode"]


def test_params_string_matches_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    for name, value in PARAMS_INPUTS.items():
        assert params_string(value) == cases[name], name
    assert params_string({"n": {"z": -0.0, "f": 1.0, "e": 1e21}}) == cases["params_string_json_nested"]


def test_js_json_stringify_scalars_and_order() -> None:
    assert js_json_stringify(None) == "null"
    assert js_json_stringify(True) == "true"
    assert js_json_stringify(1.0) == "1"
    assert js_json_stringify(-0.0) == "0"
    assert js_json_stringify(NAN) == "null"
    assert js_json_stringify(INF) == "null"
    assert js_json_stringify("a\"b\n") == '"a\\"b\\n"'
    assert js_json_stringify("\x01") == '"\\u0001"'
    assert js_json_stringify({"b": 1, "a": 2}) == '{"b":1,"a":2}'
    assert js_json_stringify({"b": 1, "2": 3, "10": 2}) == '{"2":3,"10":2,"b":1}'
    assert js_json_stringify([1, [2.5, None]]) == "[1,[2.5,null]]"


def test_params_string_top_level_scalars_are_not_quoted() -> None:
    assert params_string(None) == ""
    assert params_string({"s": "plain", "t": True, "n": None, "i": 3, "f": 0.5}) == "f=0.5;i=3;n=null;s=plain;t=true"


def test_params_string_sorts_keys_by_utf16_code_units() -> None:
    # U+FF5E sorts before U+1F600 in code points, but its surrogate pair (U+D83D) sorts before U+FF5E in UTF-16.
    assert params_string({"\uff5e": 1, "\U0001F600": 2}) == "\U0001F600=2;\uff5e=1"


# ---------------------------------------------------------------------------
# Chain keys and the source keys of the pipeline
# ---------------------------------------------------------------------------


def test_chain_keys_match_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    assert chain_key(None, "solve", {"a": 1}) == cases["chain_key_null_prev"]
    assert chain_key("", "solve", None) == cases["chain_key_empty_prev"]
    assert chain_key("abc123", "mgc", {"k": "v", "n": 0.5}) == cases["chain_key_prev_params"]
    assert chain_key("abc", "stretchRGB", {"keepLinear": True, "strength": 1.0}) == cases["chain_key_keep_linear"]
    nested = {"model": {"name": "bge", "tiles": [256, 256]}, "smooth": 0.1}
    assert chain_key("0123", "graxpert", nested) == cases["chain_key_nested"]


def test_iso_ms_truncates_to_whole_milliseconds() -> None:
    assert iso_ms(0.9999) == "1970-01-01T00:00:00.999Z"
    assert iso_ms(0.0) == "1970-01-01T00:00:00.000Z"
    assert iso_ms(1700000000.123) == "2023-11-14T22:13:20.123Z"
    assert iso_ms(-1.5) == "1969-12-31T23:59:58.500Z"


def test_file_source_key_matches_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    for record in cases["file_key_cases"]:
        seconds = record["ms"] / 1000
        assert iso_ms(seconds) == record["iso"], record
        assert file_source_key(cases["file_key_path"], cases["file_key_size"], seconds) == record["key"], record


def test_crop_and_halo_tags_match_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    assert crop_tag(COMMON) == cases["crop_tag"]
    assert crop_tag({"x0": 12.0, "y0": 0.5, "x1": 1e21, "y1": -0.0}) == cases["crop_tag_float"]
    assert crop_halos_tag(COMMON, True) == cases["crop_halos_true"]
    assert crop_halos_tag(COMMON, False) == cases["crop_halos_false"]


def test_clean_white_balance_key_matches_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    keys = {"R": "kR", "G": "kG", "B": "kB"}
    assert clean_white_balance_key(keys, COMMON, {}, "ZWO ASI2600MM Pro", "refK") == cases["clean_wb_no_filters"]
    assert clean_white_balance_key(keys, COMMON, {"R": "Ha", "L": "Lum"}, None, "refK") == \
        cases["clean_wb_named_filters"]
    assert clean_white_balance_key(keys, COMMON, None, None, "refK") == cases["clean_wb_unset_filters"]
    assert clean_white_balance_key(keys, COMMON, {}, "ZWO ASI2600MM Pro", "refL") == cases["clean_wb_other_reference"]
    assert clean_white_balance_key(keys, COMMON, {}, "ZWO ASI2600MM Pro", None) == cases["clean_wb_null_reference"]


def test_clean_white_balance_key_of_a_colour_run_matches_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    keys = {"R": "kR", "G": "kG", "B": "kB"}
    sensor = {"R": "Sony Color Sensor R", "G": "Sony Color Sensor G", "B": "Sony Color Sensor B"}
    assert clean_white_balance_key(keys, COMMON, None, None, "refK", osc_sensor=sensor) == cases["clean_wb_osc"]
    assert clean_white_balance_key(keys, COMMON, None, None, "refL", osc_sensor=sensor) == \
        cases["clean_wb_osc_other_reference"]
    assert clean_white_balance_key(keys, COMMON, None, None, "refK", osc_sensor={}) == cases["clean_wb_osc_no_sensor"]


def test_the_calibration_identity_changes_the_key_and_none_leaves_it_alone(oracle) -> None:
    keys = {"R": "kR", "G": "kG", "B": "kB"}
    plain = clean_white_balance_key(keys, COMMON, {}, None, "refK")
    assert clean_white_balance_key(keys, COMMON, {}, None, "refK", calibration=None) == plain
    one = clean_white_balance_key(keys, COMMON, {}, None, "refK", calibration="osc|a")
    two = clean_white_balance_key(keys, COMMON, {}, None, "refK", calibration="osc|b")
    assert len({plain, one, two}) == 3


def test_clean_white_balance_key_raises_for_a_missing_key(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    with pytest.raises(ValueError) as info:
        clean_white_balance_key({"R": "kR", "G": "", "B": "kB"}, COMMON, {}, None, "refK")
    assert str(info.value) == cases["clean_wb_missing_error"]


def test_derived_source_keys_match_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    assert palette_source_key("SHO", ["k1", "k2", "k3"], COMMON, True) == cases["palette_source_key"]
    assert palette_source_key("HOO", ["k1", "k2", "k3"], COMMON, False) == cases["palette_source_key_hoo"]
    assert rgb_source_key({"R": "rk", "G": "gk", "B": "bk"}, COMMON, True) == cases["rgb_source_key"]
    assert lstars_source_key("lk", COMMON, False) == cases["lstars_source_key"]


def test_palette_source_key_needs_three_keys() -> None:
    with pytest.raises(ValueError):
        palette_source_key("SHO", ["k1", "k2"], COMMON, True)


# ---------------------------------------------------------------------------
# buildStageKeys
# ---------------------------------------------------------------------------


def test_build_stage_keys_matches_loom(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    source_l = hash_str("source|L")
    assert build_stage_keys(source_l, {
        "solve": {"scale": 1.2, "catalog": "gaia"},
        "mgc": {"strength": 0.5},
        "graxpert": {"smoothing": 0.1, "model": "bge"},
    }) == cases["build_broadband"]

    tail = {"extractRGB": {"x": 1}, "stretchRGB": {"keepLinear": True, "s": 1.5}, "denoiseRGB": {"d": 2}}
    assert build_stage_keys("rgbsource", tail) == cases["build_rgb_tail_keep"]
    tail["stretchRGB"] = {"keepLinear": False, "s": 1.5}
    assert build_stage_keys("rgbsource", tail) == cases["build_rgb_tail_drop"]

    assert build_stage_keys("abc", {"solve": {"a": 1}, "bogus": {"z": 9}, "mgc": {}}) == cases["build_ignores_unknown"]
    assert build_stage_keys("abc", None) == cases["build_null_params"] == []
    assert build_stage_keys("abc", {"solve": None, "mgc": {}}) == cases["build_null_stage_params"]


def test_build_stage_keys_chains_each_key_from_the_previous_one() -> None:
    chain = build_stage_keys(None, {"solve": {}, "mgc": {}})
    assert chain[0]["key"] == chain_key(None, "solve", {})
    assert chain[1]["key"] == chain_key(chain[0]["key"], "mgc", {})


# ---------------------------------------------------------------------------
# resolveCacheHit, over every combination of entry states
# ---------------------------------------------------------------------------


def _callbacks(chain: list[dict[str, Any]], states: list[str]):
    """Callbacks that answer as the JS fakes do for each entry state (see tools/oracle/loom_cache.js)."""
    by_key = {entry["key"]: state for entry, state in zip(chain, states)}

    def entry_complete(entry: dict[str, Any]) -> bool:
        state = by_key[entry["key"]]
        if state == "miss":
            return False
        if entry.get("companion") is not None:
            return state != "halfwritten"
        return True

    def load(entry: dict[str, Any]) -> Any:
        state = by_key[entry["key"]]
        return None if state == "unloadable" else {"key": entry["key"]}

    def load_companion(entry: dict[str, Any]) -> Any:
        return {"companion": entry["companion"]} if by_key[entry["key"]] == "ok" else None

    return entry_complete, load, load_companion


def test_resolve_cache_hit_matches_loom_for_every_state_combination(oracle) -> None:
    cases = oracle("loom_cache")["cases"]
    chain_a = build_stage_keys(hash_str("source|A"), {"solve": {"a": 1}, "mgc": {"b": 2}, "graxpert": {"c": "x"}})
    chain_b = build_stage_keys(hash_str("source|B"), {"solve": {}, "stretchRGB": {"keepLinear": True}, "extractL": {}})

    assert [e["key"] for e in chain_a] == cases["resolve_chain_a_keys"]
    assert [e["key"] for e in chain_b] == cases["resolve_chain_b_keys"]
    assert [e.get("companion") for e in chain_b] == cases["resolve_chain_b_companions"]

    for chain, table in ((chain_a, cases["resolve_chain_a"]), (chain_b, cases["resolve_chain_b"])):
        assert len(table) == len(cases["resolve_states"]) ** len(chain)
        for combo, expected in table.items():
            states = combo.split(",")
            lookups = [s != "miss" for s in states]
            entry_complete, load, load_companion = _callbacks(chain, states)
            index, loaded, companion = resolve_cache_hit(chain, lookups, entry_complete, load, load_companion)
            assert index == expected, (combo, index, expected)
            if index == -1:
                assert loaded is None and companion is None
            else:
                assert loaded is not None
                assert (companion is not None) == (chain[index].get("companion") is not None)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def _planes(seed: int = 1, count: int = 2, shape: tuple[int, int] = (12, 16)) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [rng.random(shape, dtype=np.float32) for _ in range(count)]


def _recorder() -> tuple[list[tuple[str, str]], Any]:
    seen: list[tuple[str, str]] = []

    def log(level: str, text: str) -> None:
        seen.append((level, text))

    return seen, log


def test_store_lookup_and_load_round_trip_bit_identical(tmp_path: Path) -> None:
    store = CacheStore(str(tmp_path / "cache"))
    key = hash_str("entry")
    planes = _planes()
    path = store.store(key, planes, {"OBJECT": "M42"}, {"channel": "R", "stage": "solve", "params": {"a": 1}})
    assert path == store.path_for(key) == str(tmp_path / "cache" / (key + ".fits"))
    assert store.lookup(key) == path
    assert store.lookup(hash_str("missing")) is None
    assert Path(store.meta_path_for(key)).exists()

    loaded = store.load(key)
    assert loaded is not None
    back, header = loaded
    assert len(back) == len(planes)
    for a, b in zip(planes, back):
        assert b.dtype == np.float32
        assert np.array_equal(a, b)
    assert header["OBJECT"] == "M42"


def test_store_publishes_nothing_when_the_entry_does_not_read_back(tmp_path: Path, monkeypatch) -> None:
    seen, log = _recorder()
    store = CacheStore(str(tmp_path / "cache"), log=log)
    key = hash_str("unreadable")

    def boom(path, *args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(cache_mod, "read_fits", boom)
    assert store.store(key, _planes(), {}, {}) is None
    folder = Path(store.folder)
    assert not (folder / (key + ".fits")).exists()
    assert not (folder / (key + ".fits.part")).exists()
    assert store.lookup(key) is None
    errors = [text for level, text in seen if level == "error"]
    assert len(errors) == 1
    assert errors[0].startswith(key[:12] + " was written but will not read back (boom); discarding it")


def test_store_discards_a_shape_mismatch(tmp_path: Path, monkeypatch) -> None:
    seen, log = _recorder()
    store = CacheStore(str(tmp_path / "cache"), log=log)
    key = hash_str("shape")
    real_read = cache_mod.read_fits

    def short_read(path, *args, **kwargs):
        planes, header = real_read(path, *args, **kwargs)
        return planes[:1], header

    monkeypatch.setattr(cache_mod, "read_fits", short_read)
    assert store.store(key, _planes(count=2), {}, {}) is None
    assert store.lookup(key) is None
    assert any("reads back as 16x12x1, not the 16x12x2" in text for _level, text in seen)


def test_load_of_a_corrupt_entry_deletes_it_and_returns_none(tmp_path: Path) -> None:
    seen, log = _recorder()
    store = CacheStore(str(tmp_path / "cache"), log=log)
    key = hash_str("corrupt")
    Path(store.folder).mkdir(parents=True)
    Path(store.path_for(key)).write_bytes(b"this is not a FITS file")
    assert store.load(key) is None
    assert not Path(store.path_for(key)).exists()
    assert any(level == "warn" and key[:12] in text for level, text in seen)


def test_companions_and_entry_completeness(tmp_path: Path) -> None:
    store = CacheStore(str(tmp_path / "cache"))
    key = hash_str("extract")
    entry = {"stage": "extractL", "key": key, "params": {}, "companion": "stars"}
    plain = {"stage": "solve", "key": key, "params": {}}
    assert store.entry_complete(entry) is False
    assert store.entry_complete(None) is False

    store.store(key, _planes(seed=2), {}, {})
    assert store.entry_complete(plain) is True
    assert store.entry_complete(entry) is False  # the stars half is missing

    stars = _planes(seed=3, count=1)
    path = store.store_companion(key, "stars", stars, {"OBJECT": "stars"})
    assert path == store.companion_path_for(key, "stars") == str(Path(store.folder) / (key + ".stars.fits"))
    assert store.entry_complete(entry) is True
    back, header = store.load_companion(key, "stars")
    assert np.array_equal(back[0], stars[0])
    assert header["OBJECT"] == "stars"
    assert store.load_companion(key, "linear") is None


def test_white_balance_json_round_trip(tmp_path: Path) -> None:
    store = CacheStore(str(tmp_path / "cache"))
    key = hash_str("wb")
    assert store.read_wb(key) is None
    store.write_wb(key, [1.0, 2.5, 0.75])
    assert Path(store.wb_path_for(key)).name == key + ".wb.json"
    assert store.read_wb(key) == [1.0, 2.5, 0.75]
    Path(store.wb_path_for(key)).write_text("[1, 2]", encoding="utf-8")
    assert store.read_wb(key) is None
    Path(store.wb_path_for(key)).write_text("{not json", encoding="utf-8")
    assert store.read_wb(key) is None


def test_clear_keeps_subfolders_and_total_bytes_counts_files_only(tmp_path: Path) -> None:
    store = CacheStore(str(tmp_path / "cache"))
    store.store(hash_str("a"), _planes(seed=4), {}, {})
    store.store(hash_str("b"), _planes(seed=5), {}, {})
    logs = Path(store.folder) / "logs"
    logs.mkdir()
    (logs / "run.log").write_text("kept", encoding="utf-8")

    listed = sum(p.stat().st_size for p in Path(store.folder).iterdir() if p.is_file())
    assert store.total_bytes() == listed
    assert listed > 0

    freed = store.clear()
    assert freed == listed
    assert store.total_bytes() == 0
    assert (logs / "run.log").read_text(encoding="utf-8") == "kept"
    assert not any(p.is_file() for p in Path(store.folder).iterdir())


def test_total_bytes_and_clear_of_a_missing_folder(tmp_path: Path) -> None:
    store = CacheStore(str(tmp_path / "never-made"))
    assert store.total_bytes() == 0
    assert store.clear() == 0


def test_low_space_reports_only_below_the_threshold(tmp_path: Path) -> None:
    store = CacheStore(str(tmp_path / "cache"))
    store.free_bytes = lambda: 5 * 1024**3  # type: ignore[method-assign]
    assert store.low_space() == 5 * 1024**3
    store.free_bytes = lambda: 40 * 1024**3  # type: ignore[method-assign]
    assert store.low_space() is None


def test_free_bytes_asks_the_nearest_existing_parent(tmp_path: Path) -> None:
    store = CacheStore(str(tmp_path / "a" / "b" / "cache"))
    free = store.free_bytes()
    assert isinstance(free, int) and free > 0


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


def test_default_cache_dir_for_each_platform() -> None:
    assert default_cache_dir("darwin", {}, "/Users/fc") == \
        "/Users/fc/Library/Application Support/loomsiril/cache"
    assert default_cache_dir("win32", {"LOCALAPPDATA": "C:\\Users\\fc\\AppData\\Local"}, "C:\\Users\\fc") == \
        "C:\\Users\\fc\\AppData\\Local\\loomsiril\\cache"
    assert default_cache_dir("win32", {}, "C:\\Users\\fc") == "C:\\Users\\fc\\AppData\\Local\\loomsiril\\cache"
    assert default_cache_dir("linux", {"XDG_DATA_HOME": "/data/xdg"}, "/opt/acct") == "/data/xdg/loomsiril/cache"
    assert default_cache_dir("linux", {}, "/opt/acct") == "/opt/acct/.local/share/loomsiril/cache"
    assert default_cache_dir("linux", {"XDG_DATA_HOME": ""}, "/opt/acct") == "/opt/acct/.local/share/loomsiril/cache"
    for platform, env in (("darwin", {}), ("win32", {}), ("linux", {})):
        assert tempfile.gettempdir() not in default_cache_dir(platform, env, "/opt/acct")


def test_folder_missing(tmp_path: Path) -> None:
    assert folder_missing(str(tmp_path)) is False
    assert folder_missing(str(tmp_path / "unmounted")) is True
    assert folder_missing("") is False
    assert folder_missing(None) is False


# ---------------------------------------------------------------------------
# Performance: one full option set, not a benchmark loop
# ---------------------------------------------------------------------------


def test_keys_for_a_full_option_set_are_fast() -> None:
    params = _full_stage_params()
    start = time.perf_counter()
    chain = build_stage_keys(hash_str("source"), params)
    palette = build_stage_keys(hash_str("palette"), params)
    elapsed = time.perf_counter() - start
    assert len(chain) == len(palette) == 27
    assert elapsed < 0.05, elapsed


def test_nan_and_infinity_have_javascript_spelling() -> None:
    assert params_string({"x": NAN}) == "x=NaN"
    assert params_string({"x": INF}) == "x=Infinity"
    assert params_string({"x": -INF}) == "x=-Infinity"
    assert math.isnan(NAN)
