"""loomsiril.skyname: the checks of Loom 0.4.2's selftest.js (parseSesame, nameQueries, viewIdFromPath, imageTitle,
Sky.namedInField with a stubbed fetch) with the same inputs and expected values, then what the port adds (the typed
name, the sources, offline). The network is never touched: fetch_text is always replaced."""

from __future__ import annotations

import pytest

from loomsiril import core, fly_model, skyname

SESAME = ("# Sh2-132\t#Q10269584\n#=Sc=Simbad (CDS, via client/server):    1   105ms\n%C.0 HII\n"
          "%J 334.78749815 +56.07912137 = 22 19 09.0    +56 04 45\n%I.0 LBN 102.79-00.92\n")


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """fetch_text replaced: the Sesame answer for Sh2-132, an unknown-name answer for the rest; records the names asked."""
    log: list[str] = []
    monkeypatch.setattr(skyname, "resolved_names", {})

    def fetch(url: str, what: str = "Name lookup"):
        from urllib.parse import unquote

        log.append(unquote(url.split("?", 1)[1]))
        return SESAME if "sh2-132" in url.lower() else "#! no object\n"

    monkeypatch.setattr(skyname, "fetch_text", fetch)
    return log


def test_parse_sesame_id_and_position() -> None:
    r = skyname.parse_sesame(SESAME)
    assert [r["id"], f"{r['ra']:.3f}", f"{r['dec']:.3f}"] == ["Sh2-132", "334.787", "56.079"]


def test_parse_sesame_an_unknown_name() -> None:
    assert [skyname.parse_sesame("# Nothing\n#! no object\n"), skyname.parse_sesame(""), skyname.parse_sesame(None)] == [None] * 3


def test_name_queries_catalogue_like_word_first_then_the_names_words() -> None:
    q = skyname.name_queries("/Users/x/Downloads/Sh2-132 - Lion_Nebula Day 4_fullres.tif")
    assert q[0] == "Sh2-132"
    assert q.index("Lion Nebula") > 0
    assert len(q) <= skyname.NAME_QUERY_MAX and len(q) == len({x.lower() for x in q})
    assert skyname.name_queries("") == []


def test_name_queries_folders_follow_the_file_and_the_cap_holds() -> None:
    q = skyname.name_queries("/data/LBN473/Rosette Nebula/frame_001.tif")
    assert q[0] == "LBN473" and "Rosette Nebula" in q and len(q) <= skyname.NAME_QUERY_MAX
    assert skyname.NAME_QUERY_MAX == 6
    # the extension is not a word of the file; a folder's dots are
    assert "tif" not in skyname.name_queries("/a/Lion.tif")


def test_view_id_from_path() -> None:
    assert [skyname.view_id_from_path("/d/Sh2-132 - Lion_Nebula Day 4_fullres.tif"), skyname.view_id_from_path("/d/4k.tif"),
            skyname.view_id_from_path("/d/---.tif")] == ["Sh2_132_Lion_Nebula_Day_4_fullres", "_4k", ""]


def test_image_title_the_files_name_for_image_number_else_the_view_id() -> None:
    assert [skyname.image_title("/d/Lion.tif", "Image6559"), skyname.image_title("/d/Lion.tif", "Lion"),
            skyname.image_title("", "Image6559")] == ["Lion.tif", "Lion", "Image6559"]


FIELD = {"centre": {"ra": 334.7, "dec": 56.1}, "radius_deg": 1.5}
FAR = {"centre": {"ra": 10.0, "dec": 10.0}, "radius_deg": 1.5}


def test_named_in_field_the_resolved_object_in_the_field(asked) -> None:
    hit = skyname.named_in_field("/d/Sh2-132 - Lion_Nebula.tif", FIELD)
    assert hit["id"] == "Sh2-132" and hit["diameter"] == 0 and hit["name"] == ""


def test_named_in_field_one_outside_the_field_is_not_the_target(asked) -> None:
    assert skyname.named_in_field("/d/Sh2-132.tif", FAR) is None


def test_resolve_name_a_name_is_asked_once_the_answer_kept(asked) -> None:
    skyname.named_in_field("/d/Sh2-132 - Lion_Nebula.tif", FIELD)
    skyname.named_in_field("/d/Sh2-132.tif", FAR)
    assert asked.count("Sh2-132") == 1


def test_named_in_field_names_the_target_from_the_users_path_not_the_working_copys(asked) -> None:
    got = skyname.named_in_field("/d/Sh2-132 - Lion_Nebula.tif", FIELD, None)
    assert got["id"] == "Sh2-132"
    assert skyname.named_in_field("/cache/ab12cd34/work.xisf", FIELD, None) is None


def test_resolve_name_unknown_is_none_and_the_miss_is_kept(asked) -> None:
    assert skyname.resolve_name("Nothing Here") is None
    assert skyname.resolve_name("nothing here") is None
    assert asked == ["Nothing Here"]
    assert skyname.resolve_name("") is None and skyname.resolve_name(None) is None


def test_the_local_catalogue_comes_first(asked) -> None:
    local = {"id": "NGC 7000", "ra": 314.7, "dec": 44.3, "diameter": 120, "name": "North America"}
    fld = {"centre": {"ra": 314.7, "dec": 44.3}, "radius_deg": 2.0}
    assert skyname.named_in_field("/d/whatever.tif", fld, lambda path: local) is local
    assert asked == []
    # a catalogue object outside the field is skipped, the resolver is asked
    assert skyname.named_in_field("/d/Sh2-132.tif", FIELD, lambda path: local)["id"] == "Sh2-132"


def test_offline_is_no_answer_and_is_not_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(skyname, "resolved_names", {})
    monkeypatch.setattr(skyname, "fetch_text", lambda url, what="x": calls.append(url) or None)
    r = skyname.find_named([("file", "/d/Sh2-132 - Lion_Nebula.tif"), ("header", "Lion Nebula")], FIELD)
    assert r.hit is None and r.offline and len(calls) == 1          # stops at the first failure, not 6 timeouts
    assert skyname.resolved_names == {}                              # a retry later can work
    assert skyname.named_in_field("/d/Sh2-132.tif", FIELD) is None


def test_fetch_text_returns_none_on_any_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise OSError("no route")

    monkeypatch.setattr(skyname.urllib.request, "urlopen", boom)
    assert skyname.fetch_text("https://example.invalid/?x", "Name lookup") is None


def test_the_url_is_sesames_and_the_name_is_encoded(asked, monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []
    monkeypatch.setattr(skyname, "fetch_text", lambda url, what="x": urls.append(url) or "")
    skyname.resolve_name("Lion Nebula / x")
    assert urls == ["https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/SNV?Lion%20Nebula%20%2F%20x"]


def test_check_typed_inside_outside_unknown(asked) -> None:
    assert skyname.check_typed("Sh2-132", FIELD).hit["id"] == "Sh2-132"
    far = skyname.check_typed("Sh2-132", FAR)
    assert far.hit is None and far.outside[0][0] == "Sh2-132" and far.outside[0][1] > 40
    none = skyname.check_typed("Nothing Here", FIELD)
    assert none.hit is None and not none.outside and not none.offline
    assert skyname.check_typed("Sh2-132", None).hit is None


def test_find_named_says_where_the_name_came_from(asked) -> None:
    assert skyname.find_named([("file", "/d/Sh2-132 - Lion.tif")], FIELD).how == "the file name"
    assert skyname.find_named([("file", "/d/Sh2-132/frame.tif")], FIELD).how == "the folder name"
    assert skyname.find_named([("header", "Sh2-132")], FIELD).how == "the image header's OBJECT"
    assert skyname.find_named([("open", "/d/Sh2-132.fit")], FIELD).how == "the open image's file name"
    assert skyname.find_named([("file", "/d/Sh2-132.tif")], None).hit is None


def test_find_named_a_refused_hit_is_reported_not_taken(asked) -> None:
    r = skyname.find_named([("file", "/d/Sh2-132.tif")], FAR)
    assert r.hit is None and r.outside and not r.offline


def test_sky_field_is_the_middle_and_the_farthest_corner() -> None:
    w = {"crval1": 100.0, "crval2": 20.0, "crpix1": 100.5, "crpix2": 50.5, "cd": [-0.001, 0.0, 0.0, 0.001]}
    f = skyname.sky_field(w, 200, 100)
    assert core.separation(f["centre"], {"ra": 100.0, "dec": 20.0}) < 0.01
    assert f["radius_deg"] == pytest.approx(0.1118, abs=0.002)


def test_no_object_name_is_written_in_the_code() -> None:
    """The user's rule: names are found online, never hard-coded. (Comments and docstrings may give an example id.)"""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(skyname.__file__).read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)}
    literals = " ".join(n.value.lower() for n in ast.walk(tree)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs)
    for name in ("lion", "sh2-132", "rosette", "orion", "iris", "ic 1396", "ngc 7023", "ngc 7000"):
        assert name not in literals


# ---------------------------------------------------------------- the pure rules of the dialog

def test_name_sources_order_and_dedupe() -> None:
    assert fly_model.name_sources("/d/a.tif", "'Lion Nebula'", "/d/open.fit") == [
        ("file", "/d/a.tif"), ("header", "Lion Nebula"), ("open", "/d/open.fit")]
    assert fly_model.name_sources("/d/a.tif", "", "/d/a.tif") == [("file", "/d/a.tif")]
    assert fly_model.name_sources("", "  ", "") == []


def test_found_note_and_outside_note_and_window_title() -> None:
    assert fly_model.found_note({"id": "Sh2-132"}, "the file name") == "Found: Sh2-132 (from the file name via CDS Sesame)"
    assert fly_model.outside_note("Sh2-132", 44.04, 1.5) == (
        "Sh2-132 is not in this image, 44.0 deg from the image centre (the field reaches 1.5 deg): not used as the target")
    assert fly_model.window_title("/d/Lion.tif") == "Loom Fly-Through - Lion.tif"
    assert fly_model.window_title("") == "Loom Fly-Through"
    assert fly_model.OFFLINE_NOTE == "Name lookup needs the network"


def test_bridge_header_object() -> None:
    from loomsiril import siril_bridge as b

    assert b.header_object({"OBJECT": "'Lion Nebula '"}) == "Lion Nebula"
    assert b.header_object({"object": 7}) == "7" and b.header_object({"CRVAL1": 1}) == ""
