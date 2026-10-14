"""The ICC module: parsing (v2 and v4 tags), the installed scan, the profile plan, the profile folders, the
assignment fallback and the PSB profile. Oracle replay uses tools/oracle/loom_icc.js (lib/StepsIcc.js);
the other tests build synthetic profile bytes here."""

from __future__ import annotations

import re
import struct
from typing import Any

import pytest

from loomsiril.loom import icc

MAX = icc.ICC_MAX_FILE_BYTES


# --- synthetic profiles (built here, never a vendor file) ---


def desc_v2(text: str) -> bytes:
    body = text.encode("latin-1") + b"\0"
    return b"desc" + b"\0" * 4 + struct.pack(">I", len(body)) + body


def mluc_v4(records: list[tuple[str, str]]) -> bytes:
    off = 16 + 12 * len(records)
    directory = b""
    data = b""
    for lang, text in records:
        s = text.encode("utf-16-be")
        directory += lang.encode("ascii") + b"\0\0" + struct.pack(">II", len(s), off)
        data += s
        off += len(s)
    return b"mluc" + b"\0" * 4 + struct.pack(">II", len(records), 12) + directory + data


def make_profile(tags: list[tuple[str, bytes]], cls: str = "mntr", space: str = "RGB", version: int = 2) -> bytes:
    table_len = 132 + 12 * len(tags)
    off = table_len
    table = b""
    body = b""
    for sig, data in tags:
        table += sig.encode("ascii") + struct.pack(">II", off, len(data))
        body += data
        off += len(data)
    header = bytearray(128)
    header[0:4] = struct.pack(">I", table_len + len(body))
    header[8] = version
    header[12:16] = cls.ljust(4).encode("ascii")
    header[16:20] = space.ljust(4).encode("ascii")
    header[20:24] = b"Lab "
    return bytes(header) + struct.pack(">I", len(tags)) + table + body


def profile_named(name: str, cls: str = "mntr", space: str = "RGB") -> bytes:
    return make_profile([("desc", desc_v2(name))], cls=cls, space=space)


def entry(desc: str, cls: str = "mntr", space: str = "RGB", path: str | None = None) -> dict[str, str]:
    e = {"deviceClass": cls, "colorSpace": space, "description": desc}
    if path is not None:
        e["path"] = path
    return e


ROMM = "ROMM RGB: ISO 22028-2:2013"


# --- oracle replay ---


def _fake_tree(case: dict[str, Any]) -> tuple[Any, Any]:
    files = {p: bytes.fromhex(h) for p, h in case["files"].items()}
    sizes = case["sizes"]

    def list_dir(folder: str) -> list[str]:
        return list(case["dirEntries"].get(folder, []))

    def read_bytes(path: str) -> bytes:
        data = files[path]
        declared = sizes.get(path, len(data))
        if declared > len(data):
            data = data + b"\0" * (declared - len(data))
        return data[: MAX + 1]

    return list_dir, read_bytes


def test_parse_matches_oracle(oracle) -> None:
    cases = oracle("loom_icc")["cases"]
    names = [k for k in cases if k.startswith("parse_")]
    assert len(names) >= 10
    for name in names:
        c = cases[name]
        assert icc.parse_icc_profile(bytes.fromhex(c["hex"])) == c["out"], name


def test_profile_plan_matches_oracle(oracle) -> None:
    cases = oracle("loom_icc")["cases"]
    names = [k for k in cases if k.startswith("plan_")]
    assert len(names) >= 6
    for name in names:
        c = cases[name]
        plan = icc.profile_plan(c["installed"])
        assert plan["rgb"] == c["rgb"], name
        assert plan["gray"] == c["gray"], name


def test_profile_directories_match_oracle(oracle) -> None:
    cases = oracle("loom_icc")["cases"]
    names = [k for k in cases if k.startswith("dirs_")]
    assert len(names) == 6
    for name in names:
        c = cases[name]
        got = icc.icc_profile_directories(c["platform"], {"SystemRoot": c["systemRoot"]}, c["home"])
        assert got == c["out"], name


@pytest.mark.parametrize("name", ["installed_macos", "installed_windows", "installed_unix"])
def test_installed_scan_matches_oracle(oracle, name: str) -> None:
    c = oracle("loom_icc")["cases"][name]
    dirs = icc.icc_profile_directories(c["platform"], {"SystemRoot": c["systemRoot"]}, c["home"])
    assert dirs == c["dirs"]
    list_dir, read_bytes = _fake_tree(c)
    found = icc.installed_icc_profiles(dirs, list_dir, read_bytes)
    got = [{k: p[k] for k in ("deviceClass", "colorSpace", "description")} for p in found]
    assert got == c["out"]


# --- parsing ---


def test_v2_and_v4_descriptions_parse() -> None:
    v2 = profile_named("sRGB IEC61966-2.1")
    assert icc.parse_icc_profile(v2) == {
        "deviceClass": "mntr",
        "colorSpace": "RGB",
        "description": "sRGB IEC61966-2.1",
    }
    v4 = make_profile([("desc", mluc_v4([("de", "Deutsch"), ("en", "Display P3")]))], version=4)
    assert icc.parse_icc_profile(v4)["description"] == "Display P3"
    assert icc.icc_description(v4) == "Display P3"


def test_v4_without_english_takes_first_record() -> None:
    v4 = make_profile([("desc", mluc_v4([("fr", "Premier"), ("de", "Zweiter")]))], version=4)
    assert icc.icc_description(v4) == "Premier"


def test_short_and_tagless_profiles_are_rejected() -> None:
    v2 = profile_named("sRGB IEC61966-2.1")
    assert icc.parse_icc_profile(v2[:131]) is None
    assert icc.parse_icc_profile(bytes(132)) is None
    assert icc.parse_icc_profile(make_profile([("cprt", desc_v2("Copyright"))])) is None


def test_wrong_class_parses_but_is_not_planned() -> None:
    scnr = make_profile([("desc", desc_v2("Wide Gamut RGB"))], cls="scnr")
    parsed = icc.parse_icc_profile(scnr)
    assert parsed is not None and parsed["deviceClass"] == "scnr"
    plan = icc.profile_plan([parsed])
    assert "Wide Gamut RGB" not in plan["rgb"]


def test_linear_descriptions_are_rejected() -> None:
    linear = entry("ProPhoto RGB Linear")
    assert icc.profiles_matching([linear], "RGB", [icc.RGB_PROFILE_PREFERENCE[1][0]]) == []
    plan = icc.profile_plan([linear])
    assert plan["rgb"] == [icc.PROFILE_SRGB]


def test_monitor_profiles_never_tag() -> None:
    plan = icc.profile_plan([entry("Dell U2415"), entry("Dell U2415 gray", space="GRAY")])
    assert plan["rgb"] == [icc.PROFILE_SRGB]
    assert plan["gray"] == []


def test_plan_prefers_romm_then_prophoto_without_repeats() -> None:
    plan = icc.profile_plan([entry(ROMM), entry("ProPhoto RGB"), entry(ROMM), entry("Generic Gray Profile", space="GRAY")])
    assert plan["rgb"] == [ROMM, "ProPhoto RGB", icc.PROFILE_SRGB]
    assert plan["gray"] == ["Generic Gray Profile"]


def test_gray_order_follows_the_rgb_gamma() -> None:
    plan = icc.profile_plan(
        [entry("Rec. 2020 RGB"), entry("Gray Gamma 1.8", space="GRAY"), entry("sGray", cls="spac", space="GRAY")]
    )
    assert plan["rgb"][0] == "Rec. 2020 RGB"
    assert plan["gray"] == ["sGray", "Gray Gamma 1.8"]


# --- the installed scan ---


def test_installed_scan_skips_hidden_non_profile_and_unreadable_entries() -> None:
    good = profile_named("Display P3")
    dirs = ["/d"]
    listing = {"/d": [".hidden.icc", "notes.txt", "broken.icc", "err.icc", "Display P3.icm"]}

    def list_dir(folder: str) -> list[str]:
        return listing[folder]

    def read_bytes(path: str) -> bytes:
        if path == "/d/err.icc":
            raise OSError("unreadable")
        if path == "/d/broken.icc":
            return b"\x01\x02"
        return good

    found = icc.installed_icc_profiles(dirs, list_dir, read_bytes)
    assert [p["path"] for p in found] == ["/d/Display P3.icm"]


def test_installed_scan_survives_a_failing_listing() -> None:
    def list_dir(folder: str) -> list[str]:
        raise OSError("no such folder")

    assert icc.installed_icc_profiles(["/missing"], list_dir, lambda p: None) == []


def test_two_megabyte_cap() -> None:
    exact = profile_named("Display P3") + bytes(MAX - len(profile_named("Display P3")))
    over = profile_named("Adobe RGB (1998)") + bytes(MAX + 1 - len(profile_named("Adobe RGB (1998)")))
    files = {"/d/exact.icc": exact, "/d/over.icc": over}

    def list_dir(folder: str) -> list[str]:
        return ["exact.icc", "over.icc"]

    def read_bytes(path: str) -> bytes:
        return files[path][: MAX + 1]

    found = icc.installed_icc_profiles(["/d"], list_dir, read_bytes)
    assert [p["description"] for p in found] == ["Display P3"]


# --- folders per platform ---


def test_windows_folder_uses_system_root_and_forward_slashes() -> None:
    assert icc.icc_profile_directories("windows", {}, "") == ["C:/Windows/System32/spool/drivers/color"]
    assert icc.icc_profile_directories("windows", {"SystemRoot": "D:\\WINNT\\"}, "C:\\Users\\x") == [
        "D:/WINNT/System32/spool/drivers/color"
    ]


def test_macos_and_unix_folders_add_the_home_only_when_given() -> None:
    assert icc.icc_profile_directories("macos", {}, None) == [
        "/System/Library/ColorSync/Profiles",
        "/Library/ColorSync/Profiles",
    ]
    assert icc.icc_profile_directories("macos", {}, "/Users/a")[-1] == "/Users/a/Library/ColorSync/Profiles"
    assert icc.icc_profile_directories("unix", {}, "/opt/tester") == [
        "/usr/share/color/icc",
        "/usr/local/share/color/icc",
        "/opt/tester/.color/icc",
        "/opt/tester/.local/share/icc",
    ]
    assert len(icc.icc_profile_directories("linux", {}, "")) == 2


# --- assignment and the PSB ---


def test_colour_plate_falls_back_to_builtin_srgb_with_a_warning() -> None:
    a = icc.assignment_for(True, icc.profile_plan([]))
    assert a.target == "sRGB" and a.name == icc.PROFILE_SRGB
    assert a.warning is not None and "built-in sRGB" in a.warning
    assert a.skipped == tuple(icc.profile_plan([])["rgb"])


def test_mono_plate_falls_back_to_builtin_srgb_even_with_a_colour_file() -> None:
    plan = icc.profile_plan([entry(ROMM, path="/p/romm.icc")])
    a = icc.assignment_for(True, plan)
    assert (a.target, a.name, a.warning) == ("/p/romm.icc", ROMM, None)
    m = icc.assignment_for(False, plan)
    assert m.target == "sRGB" and m.warning is not None


def test_mono_plate_uses_an_installed_gray_file() -> None:
    plan = icc.profile_plan([entry("Generic Gray Profile", space="GRAY", path="/p/gray.icc")])
    a = icc.assignment_for(False, plan)
    assert (a.target, a.name, a.warning) == ("/p/gray.icc", icc.PROFILE_GRAY, None)


def test_assignment_never_names_graysrgb() -> None:
    for installed in ([], [entry("Gray Gamma 2.2", space="GRAY")]):
        for colour in (True, False):
            assert icc.assignment_for(colour, icc.profile_plan(installed)).target != "graysrgb"


def test_prophoto_family_detection() -> None:
    assert icc.is_prophoto_family(ROMM)
    assert icc.is_prophoto_family("ProPhoto RGB")
    assert not icc.is_prophoto_family("sRGB IEC61966-2.1")
    assert not icc.is_prophoto_family(None)


def test_psb_uses_adobe_prophoto_file_when_present() -> None:
    adobe = b"adobe-prophoto-bytes"
    files = {icc.PROFILE_RGB_FILES[0]: adobe}

    data, warning = icc.psb_profile_bytes(
        None, lambda n: b"named", lambda p: files[p], lambda p: p in files
    )
    assert data == adobe and warning is None


def test_psb_carries_the_profile_in_use_when_it_is_not_prophoto() -> None:
    data, warning = icc.psb_profile_bytes(
        "sRGB IEC61966-2.1", lambda n: n.encode(), lambda p: b"", lambda p: False
    )
    assert data == b"sRGB IEC61966-2.1" and warning is None


def test_psb_warns_and_uses_romm_when_adobe_file_is_missing() -> None:
    data, warning = icc.psb_profile_bytes(None, lambda n: n.encode(), lambda p: b"", lambda p: False)
    assert data == ROMM.encode()
    assert warning is not None and "ProPhoto" in warning


def test_psb_ignores_an_empty_adobe_file() -> None:
    data, warning = icc.psb_profile_bytes(None, lambda n: b"named", lambda p: b"", lambda p: True)
    assert data == b"named" and warning is not None


def test_assignment_names_the_closest_available_profile() -> None:
    plan = icc.profile_plan([entry(ROMM), entry(icc.PROFILE_SRGB, path="/p/srgb.icc")])
    a = icc.assignment_for(True, plan)
    assert (a.target, a.name, a.skipped) == ("/p/srgb.icc", icc.PROFILE_SRGB, (ROMM,))
    assert icc.assignment_log("doc", a) == (
        "doc -> sRGB IEC61966-2.1 (ROMM RGB: ISO 22028-2:2013 not installed; closest available)"
    )
    first = icc.assignment_for(True, icc.profile_plan([entry(ROMM, path="/p/romm.icc")]))
    assert icc.assignment_log("doc", first) == "doc -> " + ROMM


# --- cases that single out individual branches ---


def test_gray_order_with_rgb_gamma22_puts_the_preferred_gray_last() -> None:
    plan = icc.profile_plan(
        [entry("Display P3"), entry("Generic Gray Profile", space="GRAY"), entry("Gray Gamma 2.2", space="GRAY")]
    )
    assert plan["gray"] == ["Gray Gamma 2.2", "Generic Gray Profile"]


def test_no_rgb_with_both_gray_gammas_takes_gamma18_first() -> None:
    plan = icc.profile_plan([entry("Gray Gamma 2.2", space="GRAY"), entry("Gray Gamma 1.8", space="GRAY")])
    assert plan["rgb"] == [icc.PROFILE_SRGB]
    assert plan["gray"] == ["Gray Gamma 1.8", "Gray Gamma 2.2"]


def test_same_description_in_two_folders_first_path_wins() -> None:
    files = {"/a/x.icc": profile_named("Display P3"), "/b/y.icc": profile_named("Display P3")}
    listing = {"/a": ["x.icc"], "/b": ["y.icc"]}
    found = icc.installed_icc_profiles(["/a", "/b"], lambda d: listing[d], lambda p: files[p])
    assert len(found) == 2
    assert icc.profile_plan(found)["paths"]["RGB"]["Display P3"] == "/a/x.icc"


def test_mluc_odd_length_string_drops_the_trailing_byte() -> None:
    raw = b"\x00A\x00B\x00"  # five bytes: the last one is not a code unit
    record = b"en\0\0" + struct.pack(">II", len(raw), 28) + raw
    body = b"mluc" + b"\0" * 4 + struct.pack(">II", 1, 12) + record + b"\x00C"
    assert icc.icc_description(make_profile([("desc", body)], version=4)) == "AB"


def test_tag_count_smaller_than_the_desc_position_is_not_read() -> None:
    p = bytearray(make_profile([("cprt", desc_v2("Copyright")), ("desc", desc_v2("Display P3"))]))
    p[128:132] = struct.pack(">I", 1)
    assert icc.parse_icc_profile(bytes(p)) is None


def test_profiles_matching_checks_the_colour_space() -> None:
    assert icc.profiles_matching([entry("Display P3", space="GRAY")], "RGB", [re.compile(r"^Display P3")]) == []
    gray = re.compile(r"^Generic Gray Profile$")
    assert icc.profiles_matching([entry("Generic Gray Profile", space="RGB")], "GRAY", [gray]) == []


def test_prophoto_family_is_case_insensitive() -> None:
    assert icc.is_prophoto_family("romm rgb: iso 22028-2:2013")
    assert icc.is_prophoto_family("prophoto rgb")


# --- platform spellings and the environment ---


@pytest.mark.parametrize(
    "token,family",
    [("darwin", "macos"), ("macos", "macos"), ("win32", "windows"), ("cygwin", "windows"),
     ("windows", "windows"), ("linux", "unix"), ("freebsd", "unix")],
)
def test_sys_platform_spellings_pick_the_same_folders(token: str, family: str) -> None:
    env = {"SystemRoot": "D:\\WINNT"}
    assert icc.icc_profile_directories(token, env, "/u") == icc.icc_profile_directories(family, env, "/u")


def test_darwin_and_win32_do_not_fall_back_to_unix_folders() -> None:
    assert icc.icc_profile_directories("darwin", {}, None)[0] == "/System/Library/ColorSync/Profiles"
    assert icc.icc_profile_directories("win32", {}, None) == ["C:/Windows/System32/spool/drivers/color"]


def test_system_root_name_is_matched_case_insensitively() -> None:
    assert icc.icc_profile_directories("win32", {"SYSTEMROOT": "D:\\WINNT"}, "") == [
        "D:/WINNT/System32/spool/drivers/color"
    ]


def test_v4_lone_surrogate_becomes_replacement_and_encodes() -> None:
    body = b"mluc" + b"\0" * 4 + struct.pack(">II", 1, 12) + b"en\0\0" + struct.pack(">II", 2, 28) + b"\xd8\x00"
    text = icc.icc_description(make_profile([("desc", body)], version=4))
    assert text == "\ufffd"
    text.encode("utf-8")


def test_mluc_with_huge_record_count_and_zero_size_is_bounded() -> None:
    body = b"mluc" + b"\0" * 4 + struct.pack(">II", 0xFFFFFFFF, 0) + b"\0" * 64
    assert icc.icc_description(make_profile([("desc", body)], version=4)) == ""
