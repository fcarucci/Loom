"""Hardening tests for the stage cache (loomsiril/loom/cache.py) and the JSON string writer it keys on (jsmath.py).

Synthetic arrays and a temporary folder only. The write and read-back steps are wrapped with monkeypatch so the
order of their file operations and their failures can be observed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

import loomsiril.loom.cache as cache_mod
from loomsiril.jsmath import json_string
from loomsiril.loom.cache import CacheStore, chain_key, resolve_cache_hit

KEY = "ab" * 20  # a 40-digit key, as hash_str returns
PLANES = [np.arange(48, dtype=np.float32).reshape(6, 8)]


def _store(tmp_path: Path, log: Any = None) -> CacheStore:
    return CacheStore(str(tmp_path / "cache"), log=log)


# ---- store: write through <key>.fits.part, read back, then publish ---------------------------------------------------


def test_store_reads_back_the_part_file_while_no_final_entry_exists(tmp_path: Path, monkeypatch: Any) -> None:
    store = _store(tmp_path)
    final = store.path_for(KEY)
    part = final + ".part"
    real_read = cache_mod.read_fits
    seen: list[tuple[str, bool, bool]] = []

    def spy_read(path: Any) -> Any:
        seen.append((str(path), os.path.exists(final), os.path.exists(part)))
        return real_read(path)

    monkeypatch.setattr(cache_mod, "read_fits", spy_read)
    assert store.store(KEY, PLANES, {"OBJECT": "synthetic"}, None) == final

    # the read-back saw the .part file, and no <key>.fits existed while it ran
    assert seen == [(part, False, True)]
    # afterwards the entry is published and the .part file is gone
    assert os.path.isfile(final)
    assert not os.path.exists(part)
    back, _header = real_read(final)
    assert np.array_equal(back[0], PLANES[0])


def test_write_that_raises_half_way_leaves_neither_entry_nor_part(tmp_path: Path, monkeypatch: Any) -> None:
    logged: list[tuple[str, str]] = []
    store = _store(tmp_path, log=lambda level, text: logged.append((level, text)))
    final = store.path_for(KEY)
    part = final + ".part"

    def half_written(path: Any, planes: Any, header: Any, bitpix: int = -32) -> None:
        Path(path).write_bytes(b"SIMPLE  =                    T")  # a truncated header block
        raise OSError("disk full")

    monkeypatch.setattr(cache_mod, "write_fits", half_written)
    assert store.store(KEY, PLANES, {}, {"stage": "solve"}) is None
    assert not os.path.exists(final)
    assert not os.path.exists(part)
    assert not os.path.exists(store.meta_path_for(KEY))  # no sidecar for an entry that was not published
    assert [level for level, _text in logged] == ["error"]
    assert "disk full" in logged[0][1]


def test_read_back_that_raises_leaves_neither_entry_nor_part(tmp_path: Path, monkeypatch: Any) -> None:
    store = _store(tmp_path, log=lambda level, text: None)
    final = store.path_for(KEY)
    part = final + ".part"

    def unreadable(path: Any) -> Any:
        raise ValueError("truncated data block")

    monkeypatch.setattr(cache_mod, "read_fits", unreadable)
    assert store.store(KEY, PLANES, {}, None) is None
    assert not os.path.exists(final)
    assert not os.path.exists(part)


# ---- resolve_cache_hit: an incomplete entry is never loaded ----------------------------------------------------------


def test_load_is_not_called_for_an_incomplete_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old_key, new_key = "1" * 40, "2" * 40
    store.store(old_key, PLANES, {}, None)  # complete: the image, no companion
    store.store(new_key, PLANES, {}, None)  # the image is here, its companion is not

    chain = [
        {"stage": "solve", "key": old_key},
        {"stage": "extractL", "key": new_key, "companion": "stars"},
    ]
    lookups = [True, True]
    loads: list[str] = []
    companion_loads: list[str] = []

    def load(entry: Any) -> Any:
        loads.append(entry["key"])
        return store.load(entry["key"])

    def load_companion(entry: Any) -> Any:
        companion_loads.append(entry["key"])
        return store.load_companion(entry["key"], entry["companion"])

    assert store.entry_complete(chain[1]) is False
    index, loaded, stars = resolve_cache_hit(chain, lookups, store.entry_complete, load, load_companion)
    assert index == 0
    assert loaded is not None and stars is None
    assert loads == [old_key]  # the incomplete entry was skipped without a load
    assert companion_loads == []


def test_load_is_not_called_when_every_entry_is_incomplete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    key = "3" * 40
    store.store(key, PLANES, {}, None)
    chain = [{"stage": "extractL", "key": key, "companion": "stars"}]
    loads: list[str] = []

    def load(entry: Any) -> Any:
        loads.append(entry["key"])
        return None

    result = resolve_cache_hit(chain, [True], store.entry_complete, load, lambda e: None)
    assert result == (-1, None, None)
    assert loads == []


def test_chain_key_depends_on_the_previous_key() -> None:
    # a sanity pin for the chain the incomplete-entry walk above relies on
    assert chain_key(None, "solve", {"a": 1}) == chain_key("", "solve", {"a": 1})
    assert chain_key("x", "solve", {"a": 1}) != chain_key("y", "solve", {"a": 1})


# ---- json_string: lone surrogates as JSON.stringify writes them -------------------------------------------------------


def test_lone_surrogates_are_escaped_with_lower_case_hex() -> None:
    assert json_string("\ud800") == '"\\ud800"'
    assert json_string("\udfff") == '"\\udfff"'
    assert json_string("a\udc00b") == '"a\\udc00b"'
    assert json_string("\udbff") == '"\\udbff"'  # a high surrogate on its own


def test_a_surrogate_pair_is_one_character_as_in_javascript() -> None:
    # JSON.stringify("😀") is the emoji itself, not two escapes
    assert json_string("😀") == '"\U0001F600"'


def test_other_controls_still_use_the_short_and_hex_escapes() -> None:
    assert json_string('q"\\\n\x01') == '"q\\"\\\\\\n\\u0001"'
