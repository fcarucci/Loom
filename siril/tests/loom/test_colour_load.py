"""The colour-master loader (loomsiril.loom.colour_load): one master, split once into cached one-channel plates.

No Siril and no network: masters are written with ``write_fits``; the loader's reads and the Siril conversion are
counted by replacing ``read_fits`` and the converter. The expected texts are those of Loom's JavaScript
(``Pipeline.oscGroupLoader``, ``oscPlaneProblem``, ``oscGroupLogLine``) and its self-test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pytest

from loomsiril.io_fits import read_fits, write_fits
from loomsiril.loom import cache, colour_load, osc
from loomsiril.loom.colour_load import ColourGroupLoader

SHAPE = (12, 16)


def _planes(seed: int = 1, *, third: Optional[np.ndarray] = None) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    planes = [rng.uniform(0.05, 0.9, SHAPE).astype(np.float32) for _ in range(3)]
    if third is not None:
        planes[2] = third
    return planes


def _master(path: Path, planes: list[np.ndarray]) -> str:
    write_fits(path, planes, {"FILTER": "L-eXtreme", "INSTRUME": "ZWO ASI2600MC", "OBJECT": "NGC 7000"}, bitpix=-32)
    return str(path)


class Env:
    """A cache folder, a work folder, the counters, and a loader factory."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cache_dir = tmp / "cache"
        self.work = tmp / "work"
        self.cache_dir.mkdir()
        self.work.mkdir()
        self.reads: list[str] = []
        self.conversions: list[tuple[str, str]] = []
        self.store = cache.CacheStore(str(self.cache_dir))
        real = colour_load.read_fits

        def counting(path: Any) -> Any:
            self.reads.append(os.fspath(path))
            return real(path)

        monkeypatch.setattr(colour_load, "read_fits", counting)

    def convert(self, src: str, name: str) -> str:
        """Stands in for Siril: a converted copy of a FITS the test keeps next to the foreign-format master."""
        self.conversions.append((src, name))
        out = str(self.work / (name + "_src.fits"))
        planes, header = read_fits(src + ".as.fits")
        write_fits(out, planes, header, bitpix=-32)
        return out

    def loader(self, path: str, slot: str = "OSC", kind: str = "broadband", *, use_cache: bool = True,
               ignore_cache: bool = False) -> ColourGroupLoader:
        return ColourGroupLoader(slot, kind, path, "fp:" + os.path.basename(path),
                                 store=self.store if use_cache else None, work_dir=str(self.work),
                                 ignore_cache=ignore_cache, convert=self.convert)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def _plate(path: str) -> np.ndarray:
    planes, _ = read_fits(path)
    assert len(planes) == 1
    return planes[0]


def _master_reads(env: Env, master: str) -> int:
    return sum(1 for p in env.reads if p == master)


# ---- the split ---------------------------------------------------------------------------------------------------


def test_osc_planes_are_the_masters_planes_and_blue_is_stored(env: Env, tmp_path: Path) -> None:
    planes = _planes()
    master = _master(tmp_path / "rgb.fits", planes)
    loader = env.loader(master)
    for plane in range(3):
        assert np.array_equal(_plate(loader.plate(plane)), planes[plane])
    assert loader.opens == 1


def test_oscnb_o_is_plane_one_whatever_plane_two_holds(env: Env, tmp_path: Path) -> None:
    for i, third in enumerate((np.full(SHAPE, 0.3, np.float32).copy(), None)):
        if third is not None:
            third[0, 0] = 0.9  # not constant: the check only refuses a plane that holds no image
        planes = _planes(3, third=third)
        master = _master(tmp_path / f"nb{i}.fits", planes)
        loader = env.loader(master, "OSCNB", "dualband")
        assert np.array_equal(_plate(loader.plate(0)), planes[0])
        assert np.array_equal(_plate(loader.plate(1)), planes[1])
        assert not os.path.exists(env.store.path_for(osc.osc_sibling_key(loader.identity, 2)))  # blue never stored


def test_a_constant_plane_is_refused_with_the_javascript_text_and_nothing_is_stored(env: Env, tmp_path: Path) -> None:
    master = _master(tmp_path / "bad.fits", _planes(4, third=np.full(SHAPE, 0.5, np.float32)))
    loader = env.loader(master)
    with pytest.raises(ValueError) as err:
        loader.plate(0)
    assert str(err.value) == master + ": plane 2 is constant (all values 0.5); the master looks broken"
    assert os.listdir(env.cache_dir) == []


def test_the_javascript_message_for_a_constant_middle_plane(env: Env, tmp_path: Path) -> None:
    planes = _planes(5)
    planes[1] = np.full(SHAPE, 0.5, np.float32)
    master = _master(tmp_path / "bad1.fits", planes)
    with pytest.raises(ValueError, match="plane 1 is constant \\(all values 0.5\\); the master looks broken"):
        env.loader(master).plate(0)


def test_plates_carry_the_channel_name_and_the_source(env: Env, tmp_path: Path) -> None:
    master = _master(tmp_path / "m31.fits", _planes())
    rgb = env.loader(master)
    for plane, name in enumerate(("R", "G", "B")):
        header = read_fits(rgb.plate(plane))[1]
        assert header["FILTER"] == name
        assert header["LOOMSRC"] == f"m31.fits|plane:{plane}"
        assert header["OBJECT"] == "NGC 7000"  # the master's other keywords come along
    nb = env.loader(_master(tmp_path / "nb.fits", _planes(2)), "OSCNB", "dualband")
    assert read_fits(nb.plate(0))[1]["FILTER"] == "Ha"
    assert read_fits(nb.plate(1))[1]["FILTER"] == "OIII"


def test_a_plate_name_that_is_not_ascii_or_is_long_does_not_break_the_header(env: Env, tmp_path: Path) -> None:
    name = "M\u00e9ssier 31 " + "x" * 90 + ".fits"
    master = _master(tmp_path / name, _planes())
    header = read_fits(env.loader(master).plate(0))[1]
    assert header["LOOMSRC"].endswith("|plane:0") and header["LOOMSRC"].isascii()


# ---- how often the master is read --------------------------------------------------------------------------------


def test_a_cold_run_reads_the_master_once_for_all_members(env: Env, tmp_path: Path) -> None:
    master = _master(tmp_path / "rgb.fits", _planes())
    loader = env.loader(master)
    paths = [loader.plate(p) for p in range(3)]
    assert _master_reads(env, master) == 1 and loader.opens == 1
    assert all(os.path.dirname(p) == str(env.cache_dir) for p in paths)


def test_a_plate_handed_out_before_is_returned_again(env: Env, tmp_path: Path) -> None:
    loader = env.loader(_master(tmp_path / "rgb.fits", _planes()))
    first = loader.plate(1)
    assert loader.plate(1) == first
    assert loader.opens == 1


def test_a_warm_run_reads_the_master_zero_times(env: Env, tmp_path: Path) -> None:
    master = _master(tmp_path / "rgb.fits", _planes())
    env.loader(master).plate(0)
    env.reads.clear()
    warm = env.loader(master)
    for plane in range(3):
        _plate(warm.plate(plane))
    assert _master_reads(env, master) == 0 and warm.opens == 0


def test_a_deleted_cached_plate_reads_the_master_once(env: Env, tmp_path: Path) -> None:
    master = _master(tmp_path / "rgb.fits", _planes())
    cold = env.loader(master)
    cold.plate(0)
    os.remove(env.store.path_for(osc.osc_sibling_key(cold.identity, 1)))
    env.reads.clear()
    warm = env.loader(master)
    warm.plate(0)
    warm.plate(1)
    warm.plate(2)
    assert _master_reads(env, master) == 1 and warm.opens == 1


def test_cache_off_reads_once_stores_nothing_and_keeps_the_planes_for_the_run(env: Env, tmp_path: Path) -> None:
    planes = _planes()
    master = _master(tmp_path / "rgb.fits", planes)
    loader = env.loader(master, use_cache=False)
    paths = [loader.plate(p) for p in range(3)]
    assert _master_reads(env, master) == 1
    assert os.listdir(env.cache_dir) == []
    assert all(os.path.dirname(p) == str(env.work) for p in paths)
    assert np.array_equal(_plate(paths[2]), planes[2])
    again = env.loader(master, use_cache=False)  # a second run: no cache, so a second read
    again.plate(0)
    assert _master_reads(env, master) == 2


def test_ignore_cache_does_not_read_the_cache_but_writes_it(env: Env, tmp_path: Path) -> None:
    master = _master(tmp_path / "rgb.fits", _planes())
    env.loader(master).plate(0)
    env.reads.clear()
    refresh = env.loader(master, ignore_cache=True)
    stamp = {p: os.stat(env.store.path_for(osc.osc_sibling_key(refresh.identity, p))).st_mtime_ns for p in range(3)}
    paths = [refresh.plate(p) for p in range(3)]
    assert _master_reads(env, master) == 1 and refresh.opens == 1
    for plane, path in enumerate(paths):
        assert path == env.store.path_for(osc.osc_sibling_key(refresh.identity, plane))
    assert os.path.isfile(os.path.join(env.cache_dir, osc.osc_validate_name(refresh.identity)))
    assert stamp  # the entries exist before and after


def test_ignore_cache_on_a_cold_cache_stores(env: Env, tmp_path: Path) -> None:
    loader = env.loader(_master(tmp_path / "rgb.fits", _planes()), ignore_cache=True)
    loader.plate(0)
    for plane in range(3):
        assert os.path.isfile(env.store.path_for(osc.osc_sibling_key(loader.identity, plane)))


# ---- the sidecar and the log line ---------------------------------------------------------------------------------


def test_the_validation_sidecar_holds_the_statistics_of_planes_zero_to_two(env: Env, tmp_path: Path) -> None:
    planes = _planes(6)
    loader = env.loader(_master(tmp_path / "rgb.fits", planes))
    loader.plate(0)
    sidecar = os.path.join(env.cache_dir, osc.osc_validate_name(loader.identity))
    record = json.loads(Path(sidecar).read_text(encoding="utf-8"))
    assert [sorted(p) for p in record["planes"]] == [["max", "median", "min"]] * 3
    for stats, plane in zip(record["planes"], planes):
        assert stats["min"] == float(plane.min()) and stats["max"] == float(plane.max())
        assert stats["median"] == float(np.median(plane))


def test_the_log_line_is_the_javascripts() -> None:
    rec = {"planes": [{"min": 0.1, "max": 0.9, "median": 0.2}, {"min": 0, "max": 1, "median": 0.5},
                      {"min": 0, "max": 1, "median": 0.5}]}
    assert colour_load.group_log_line("OSCNB", "/m/n.xisf", "dualband", "L-eXtreme", rec) == (
        "OSC n.xisf: dualband, filter L-eXtreme, planes H/O, min/max/median 0.10000/0.90000/0.20000, "
        "0.00000/1.00000/0.50000, 0.00000/1.00000/0.50000")
    # toFixed rounds an exact half up (the larger n), as does JavaScript for 1/64
    rec = {"planes": [{"min": 0.015625, "max": 1, "median": -0.0}] * 3}
    assert "0.01563/1.00000/0.00000" in colour_load.group_log_line("OSC", "C:\\m\\n.xisf", "broadband", None, rec)


def test_validated_callbacks_fire_on_the_first_load_cold_and_at_once_warm(env: Env, tmp_path: Path) -> None:
    master = _master(tmp_path / "rgb.fits", _planes())
    cold, warm = [], []
    loader = env.loader(master)
    loader.when_validated(cold.append)
    assert cold == []  # nothing is opened until a plane is wanted
    loader.plate(1)
    assert len(cold) == 1 and len(cold[0]["planes"]) == 3
    again = env.loader(master)
    again.when_validated(warm.append)
    assert warm == cold  # read from the sidecar, before any plane is asked for
    assert again.opens == 0


def test_without_a_readable_sidecar_the_runs_own_record_is_used(env: Env, tmp_path: Path) -> None:
    master = _master(tmp_path / "rgb.fits", _planes())
    first = env.loader(master)
    first.plate(0)
    sidecar = os.path.join(env.cache_dir, osc.osc_validate_name(first.identity))
    Path(sidecar).write_text("{not json", encoding="utf-8")
    seen: list[Any] = []
    second = env.loader(master, ignore_cache=True)
    second.when_validated(seen.append)
    assert seen == []
    second.plate(0)
    assert len(seen) == 1
    off = env.loader(master, use_cache=False)
    off.plate(0)
    assert off.validation() is not None  # no sidecar at all with the cache off: the run's own record


# ---- foreign formats: one conversion per group -------------------------------------------------------------------


def _foreign(env: Env, tmp_path: Path, suffix: str, planes: list[np.ndarray]) -> str:
    """A master the loader cannot read itself; the FITS the converter yields sits beside it."""
    master = str(tmp_path / ("wbpp" + suffix))
    Path(master).write_bytes(b"not read by the loader")
    _master(Path(master + ".as.fits"), planes)
    return master


@pytest.mark.parametrize("suffix", [".xisf", ".fits.fz", ".fz", ".XISF"])
def test_other_formats_are_converted_once_then_split(env: Env, tmp_path: Path, suffix: str) -> None:
    planes = _planes(7)
    master = _foreign(env, tmp_path, suffix, planes)
    loader = env.loader(master)
    for plane in range(3):
        assert np.array_equal(_plate(loader.plate(plane)), planes[plane])
    assert len(env.conversions) == 1
    src, name = env.conversions[0]
    assert src == master
    assert name == "osc_" + cache.hash_str(loader.identity)[:16]
    assert master not in env.reads  # the foreign file itself is never read by the FITS reader


def test_a_warm_run_converts_nothing(env: Env, tmp_path: Path) -> None:
    master = _foreign(env, tmp_path, ".xisf", _planes(8))
    env.loader(master).plate(0)
    env.conversions.clear()
    warm = env.loader(master)
    for plane in range(3):
        warm.plate(plane)
    assert env.conversions == []


@pytest.mark.parametrize("suffix", [".fit", ".fits", ".fts", ".FIT", ".Fits"])
def test_uncompressed_fits_is_read_directly(env: Env, tmp_path: Path, suffix: str) -> None:
    master = _master(tmp_path / ("m" + suffix), _planes())
    env.loader(master).plate(0)
    assert env.conversions == [] and _master_reads(env, master) == 1


def test_fewer_than_three_planes_is_refused(env: Env, tmp_path: Path) -> None:
    master = str(tmp_path / "mono.fits")
    write_fits(master, [_planes()[0]], {"FILTER": "L"}, bitpix=-32)
    with pytest.raises(ValueError, match="has 1 channel"):
        env.loader(master).plate(0)


# ---- what the plate header says about the source ------------------------------------------------------------------

WBPP_LUM = "masterLight_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-L-eXtreme_RGB_autocrop.xisf"
WBPP_NONE = "masterLight_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-NoFilter_RGB_autocrop.xisf"


def _loomsrc_of(env: Env, tmp_path: Path, name: str, plane: int = 0) -> str:
    # the content is plain FITS whatever the name says, so the loader reads the file directly
    master = _master(tmp_path / name, _planes())
    return str(read_fits(env.loader(master).plate(plane))[1]["LOOMSRC"])


def test_long_wbpp_names_stay_distinct_and_keep_their_filter_and_extension(env: Env, tmp_path: Path) -> None:
    assert len(WBPP_LUM) > 69
    a = _loomsrc_of(env, tmp_path, WBPP_LUM)
    b = _loomsrc_of(env, tmp_path, WBPP_NONE)
    assert a != b
    assert len(a) <= 68 and len(b) <= 68
    assert a.startswith("...") and a.endswith("FILTER-L-eXtreme_RGB_autocrop.xisf|plane:0")
    assert b.endswith("FILTER-NoFilter_RGB_autocrop.xisf|plane:0")
    assert WBPP_LUM.endswith(a[3:].split("|plane:")[0])  # what is kept is the end of the name, unchanged


def test_a_short_name_is_written_whole(env: Env, tmp_path: Path) -> None:
    assert _loomsrc_of(env, tmp_path, "m31.fits", 2) == "m31.fits|plane:2"


def test_a_name_full_of_apostrophes_neither_raises_nor_overflows_the_card(env: Env, tmp_path: Path) -> None:
    name = "'" * 70 + "end.fits"
    value = _loomsrc_of(env, tmp_path, name)
    assert value.endswith("end.fits|plane:0") and value.startswith("...")
    assert len(value) + value.count("'") <= 68


# ---- a plane that is already in the run is not stored again --------------------------------------------------------


def test_a_split_does_not_rewrite_a_plane_already_handed_out(env: Env, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    master = _master(tmp_path / "rgb.fits", _planes())
    cold = env.loader(master)
    cold.plate(0)
    key0, key1 = cold.sibling_key(0), cold.sibling_key(1)
    os.remove(env.store.path_for(key1))
    stored: list[str] = []
    real_store = env.store.store

    def spy(key: str, *args: Any, **kwargs: Any) -> Any:
        stored.append(key)
        return real_store(key, *args, **kwargs)

    monkeypatch.setattr(env.store, "store", spy)
    warm = env.loader(master)
    mtime = os.stat(env.store.path_for(key0)).st_mtime_ns
    warm.plate(0)                       # from the cache
    warm.plate(1)                       # missing: the master is split
    assert warm.opens == 1
    assert key0 not in stored and key1 in stored
    assert os.stat(env.store.path_for(key0)).st_mtime_ns == mtime


# ---- the format is decided by the content ---------------------------------------------------------------------------


def _compressed_looking(path: Path) -> str:
    """A file with a FITS signature and an empty primary HDU, as a tile-compressed file starts; named like a plain one."""
    cards = [b"SIMPLE  =                    T", b"BITPIX  =                    8", b"NAXIS   =                    0",
             b"EXTEND  =                    T", b"END"]
    block = b"".join(c.ljust(80) for c in cards)
    path.write_bytes(block.ljust(2880))
    return str(path)


def test_a_tile_compressed_file_named_fits_is_converted(env: Env, tmp_path: Path) -> None:
    planes = _planes(9)
    master = _compressed_looking(tmp_path / "wbpp.fits")
    _master(Path(master + ".as.fits"), planes)
    loader = env.loader(master)
    assert np.array_equal(_plate(loader.plate(0)), planes[0])
    assert len(env.conversions) == 1


def test_a_plain_fits_under_another_name_is_read_directly(env: Env, tmp_path: Path) -> None:
    master = _master(tmp_path / "rgb.dat", _planes())
    env.loader(master).plate(0)
    assert env.conversions == [] and _master_reads(env, master) == 1


def test_the_converted_copy_is_deleted_once_it_has_been_read(env: Env, tmp_path: Path) -> None:
    master = _foreign(env, tmp_path, ".xisf", _planes(8))
    loader = env.loader(master)
    loader.plate(0)
    assert os.listdir(env.work) == []
    assert os.path.isfile(master)  # the master itself is never touched


def test_plane_statistics_are_python_floats_of_min_max_and_median() -> None:
    stats = colour_load.plane_statistics(np.array([[0.25, 1.0], [0.5, 0.75]], np.float32))
    assert stats == {"min": 0.25, "max": 1.0, "median": 0.625}
    assert all(type(v) is float for v in stats.values())


def test_is_plain_fits_looks_at_the_content(tmp_path: Path) -> None:
    plain = _master(tmp_path / "a.xisf", _planes())
    assert colour_load.is_plain_fits(plain)
    assert not colour_load.is_plain_fits(_compressed_looking(tmp_path / "b.fits"))
    (tmp_path / "c.fits").write_bytes(b"XISF0100" + b"\0" * 100)
    assert not colour_load.is_plain_fits(str(tmp_path / "c.fits"))
    assert not colour_load.is_plain_fits(str(tmp_path / "missing.fits"))


# ---- LOOMSRC of composites and palettes: the shared trim --------------------------------------------------------------


def _fit(*parts: Any) -> str:
    from loomsiril.loom.colour_load import fit_loomsrc
    return fit_loomsrc(list(parts))


def _card_width(value: str) -> int:
    return len(value) + value.count("'")


def test_fit_loomsrc_leaves_a_value_that_fits_untouched() -> None:
    assert _fit("OSC ", ("m31.xisf",), " planes 0,1,2") == "OSC m31.xisf planes 0,1,2"


def test_fit_loomsrc_trims_inside_the_file_name_only() -> None:
    name = "2026-01-01_session_FILTER-L-eXtreme_RGB.xisf"
    value = _fit("OSC ", (name * 2,), " planes 0,1,2")
    assert value.startswith("OSC ...") and value.endswith("_RGB.xisf planes 0,1,2")
    assert _card_width(value) == 68
    assert (name * 2).endswith(value[len("OSC ..."):-len(" planes 0,1,2")])


def test_fit_loomsrc_gives_each_file_name_an_equal_share() -> None:
    a, b = "a" * 80, "b" * 80
    value = _fit("OSC ", (a,), " plane 0; OSCNB ", (b,), " plane 1")
    left, right = value.split("; OSCNB ")
    kept_a = left[len("OSC "):-len(" plane 0")]
    kept_b = right[:-len(" plane 1")]
    assert kept_a.startswith("...") and kept_b.startswith("...")
    assert len(kept_a) == len(kept_b) and _card_width(value) <= 68


def test_fit_loomsrc_counts_doubled_apostrophes() -> None:
    value = _fit("OSC ", ("'" * 40 + "tail.fits",), " plane 0")
    assert value.endswith("tail.fits plane 0") and _card_width(value) <= 68
    assert value.startswith("OSC ...")


def test_fit_loomsrc_is_ascii_safe() -> None:
    value = _fit("OSC ", ("M\u00e9ssier_\u03b2" * 10 + ".xisf",), " planes 0,1,2")
    assert value.isascii() and value.endswith(".xisf planes 0,1,2") and _card_width(value) <= 68
    assert _fit("OSC ", ("\u00e9.xisf",), " plane 0") == "OSC ?.xisf plane 0"


def test_the_channel_plate_value_is_unchanged() -> None:
    from loomsiril.loom.colour_load import _loomsrc
    assert _loomsrc("/d/m31.fits", 2) == "m31.fits|plane:2"
    assert _loomsrc("/d/" + "x" * 90 + ".fits", 2) == "..." + "x" * 52 + ".fits|plane:2"
    assert _loomsrc("/d/" + "'" * 70 + "end.fits", 0) == "..." + "'" * 24 + "end.fits|plane:0"
    assert _loomsrc("/d/caf\u00e9.xisf", 1) == "caf?.xisf|plane:1"


def test_composite_loomsrc_names_the_master_and_planes_like_the_oracle() -> None:
    from loomsiril.loom.colour_load import composite_loomsrc
    chans = {k: {"group": "OSC", "path": "/m/sub/OSC master.xisf", "plane": i} for i, k in enumerate("RGB")}
    assert composite_loomsrc(chans, ["R", "G", "B"], False) == "OSC OSC master.xisf planes 0,1,2"
    nb = {"H": {"group": "OSCNB", "path": "C:\\m\\n.xisf", "plane": 0},
          "O": {"group": "OSCNB", "path": "C:\\m\\n.xisf", "plane": 1}}
    assert composite_loomsrc(nb, ["H", "O", "O"], True) == "OSCNB n.xisf plane 0; OSCNB n.xisf plane 1"
    assert composite_loomsrc({"L": {"path": "/m/L.xisf"}}, ["L"], False) is None


def test_composite_loomsrc_trims_a_long_name_but_keeps_the_group_and_planes() -> None:
    from loomsiril.loom.colour_load import composite_loomsrc
    long = "WBPP_master_FILTER-L-eXtreme_RGB_autocrop_registered.xisf"
    chans = {k: {"group": "OSC", "path": "/m/" + long, "plane": i} for i, k in enumerate("RGB")}
    value = composite_loomsrc(chans, ["R", "G", "B"], False)
    assert value.startswith("OSC ...") and value.endswith("_autocrop_registered.xisf planes 0,1,2")
    assert _card_width(value) <= 68


def test_composite_loomsrc_with_apostrophes_and_non_ascii_names() -> None:
    from loomsiril.loom.colour_load import composite_loomsrc
    for name in ("it's_" * 12 + "n.xisf", "n\u00f6rdlich_" * 8 + "n.xisf"):
        chans = {k: {"group": "OSCNB", "path": "/m/" + name, "plane": i} for i, k in enumerate("HO")}
        value = composite_loomsrc(chans, ["H", "O", "O"], True)
        assert value.isascii() and _card_width(value) <= 68
        assert value.startswith("OSCNB ") and "; OSCNB " in value and value.endswith(" plane 1")
