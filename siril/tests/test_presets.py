"""loomsiril.presets against the JS oracle (tests/oracle/presets.json) and known answers.

The oracle replay covers every ported function; the explicit tests pin
the behaviour the plan names (aspect and bounds of a crop, ping-pong
doubling, the draft's byte budget, five-digit frame names, the signature's
dependence on star quality) and the FlyThrough constants, which the recorder
cannot load (it reads Util, Fly, Render and Sky only), so they are pinned here
from FlyThrough.js 698-702 and 774-776 and selftest.js 18392-18394.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import pytest

from loomsiril import presets
from loomsiril.presets import PRESETS

TOL = 1e-12


def _norm(v: Any) -> Any:
    """Tuples become lists, so a Python result compares with its JSON twin."""
    if isinstance(v, tuple):
        return [_norm(x) for x in v]
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


def _close(got: Any, want: Any, where: str = "") -> None:
    """Exact for strings, bools and None; 1e-12 (absolute) for numbers."""
    got = _norm(got)
    if want is None or isinstance(want, (str, bool)):
        assert got == want and type(got) is type(want), (where, got, want)
    elif isinstance(want, dict):
        assert isinstance(got, dict) and set(got) == set(want), (where, got, want)
        for k in want:
            _close(got[k], want[k], f"{where}.{k}")
    elif isinstance(want, list):
        assert isinstance(got, list) and len(got) == len(want), (where, got, want)
        for i, (g, w) in enumerate(zip(got, want)):
            _close(g, w, f"{where}[{i}]")
    else:
        assert isinstance(got, (int, float)) and not isinstance(got, bool), (where, got, want)
        if isinstance(want, float) and math.isnan(want):
            assert math.isnan(got), (where, got)
        else:
            assert abs(got - want) <= TOL, (where, got, want)


def _header_pairs(keywords: list) -> list[tuple[str, str]]:
    """The oracle stores FITS keywords as {name, value} records, as the JS takes them."""
    return [(k["name"], k["value"]) for k in keywords]


def _header_centre(keywords: list) -> Any:
    return presets.header_centre(_header_pairs(keywords))


FUNCS: dict[str, Callable[..., Any]] = {
    "starQuality": presets.star_quality,
    "qualityScale": presets.quality_scale,
    "frameCount": presets.frame_count,
    "timeAt": presets.time_at,
    "draftPlan": presets.draft_plan,
    "presetCrop": presets.preset_crop,
    "presetTurns": presets.preset_turns,
    "presetLoops": presets.preset_loops,
    "presetSpec": presets.preset_spec,
    "frameStep": presets.frame_step,
    "frameSignature": presets.frame_signature,
    "crossfadeFrames": presets.crossfade_frames,
    "loopFrame": presets.loop_frame,
    "framePath": presets.frame_path,
    "isFrameFile": presets.is_frame_file,
    "headerCentre": _header_centre,
    "sequenceLength": presets.sequence_length,
    "sequenceFrame": presets.sequence_frame,
    "parseAngle": presets.parse_angle,
    "formatElapsed": presets.format_elapsed,
    "progressText": presets.progress_text,
    "progressFraction": presets.progress_fraction,
    "workingScale": presets.working_scale,
    "needsRotation": presets.needs_rotation,
    "looksLinear": presets.looks_linear,
}


@pytest.mark.parametrize("name", sorted(FUNCS))
def test_oracle_replay(oracle, name):
    cases = oracle("presets")["cases"][name]
    assert cases, f"no recorded cases for {name}"
    for case in cases:
        got = FUNCS[name](*case["args"])
        _close(got, case["out"], f"{name}{case['args']}")


def test_constants_match_js(oracle):
    c = oracle("presets")["cases"]["constants"]
    assert presets.STAR_QUALITY == c["STAR_QUALITY"]
    assert {k: list(v) for k, v in PRESETS.items()} == c["PRESETS"]
    assert presets.DRAFT_BYTES == c["DRAFT_BYTES"] and presets.DRAFT_LONG == c["DRAFT_LONG"]
    assert presets.RENDERER_VERSION == c["RENDERER_VERSION"] == 6
    assert list(presets.ENCODE_ONLY) == c["ENCODE_ONLY"]
    assert presets.CROSSFADE_SECONDS == c["CROSSFADE_SECONDS"] == 1
    assert presets.LINEAR_SKY == c["LINEAR_SKY"]
    assert (presets.WORKING_WIDTH, presets.WORKING_HEIGHT) == (c["WORKING_WIDTH"], c["WORKING_HEIGHT"]) == (3840, 2160)


def test_flythrough_constants_pinned_from_source():
    """FlyThrough.js 698-702 and 774-776 (not recorded: the recorder does not load FlyThrough.js)."""
    assert presets.PRESET_LABELS == {
        "social_vertical": "Social 1080×1920",
        "social_square": "Social 1080×1080",
        "youtube_4k": "YouTube 3840×2160",
        "youtube_1080": "YouTube 1920×1080",
        "exhibition": "Exhibition 3840×2160 (loop)",
    }
    assert presets.PRESET_ORDER == ["social_vertical", "social_square", "youtube_4k", "youtube_1080", "exhibition"]
    assert presets.FPS == [24, 25, 30, 60]
    assert presets.LOOPS == [("none", "None"), ("pingpong", "Back and forth"), ("crossfade", "Crossfade")]
    assert presets.STAR_QUALITIES == ["highest", "high", "medium"]
    assert presets.STAR_QUALITY_DEFAULT == "high"


def test_star_quality_levels_pinned_from_selftest():
    """selftest.js 18392-18394 and 18641."""
    assert presets.star_quality("highest") == {"footprint": False, "outScale": 0, "starRes": 1}
    assert presets.star_quality("high") == {"footprint": True, "outScale": 1, "starRes": 1}
    assert presets.star_quality("medium") == {"footprint": True, "outScale": 1, "starRes": 0.5}
    assert presets.star_quality() == presets.star_quality("highest")
    assert presets.star_quality("constructor") == presets.star_quality("highest")


def test_frame_signature_changes_with_star_quality():
    """selftest.js 18397-18400: the level changes the frames, not only the encode."""
    spec = {"id": "youtube_1080", "w": 1920, "h": 1080, "pingPong": False}
    o = {"travel": 184, "duration": 20, "fps": 30, "starQuality": "highest"}
    base = presets.frame_signature(o, spec, "k")
    medium = presets.frame_signature({**o, "starQuality": "medium"}, spec, "k")
    assert base != medium


def test_frame_signature_ignores_encode_only_options():
    spec = {"id": "youtube_1080", "w": 1920, "h": 1080, "pingPong": False}
    o = {"travel": 184, "fps": 30, "format": "h264", "video": True, "dir": "/out", "presets": ["youtube_1080"]}
    base = presets.frame_signature(o, spec, "k")
    assert presets.frame_signature({**o, "format": "hevc", "dir": "/x", "video": False}, spec, "k") == base
    assert presets.frame_signature({**o, "fps": 24}, spec, "k") != base


def test_js_number_text_matches_json_stringify():
    """The number text of JSON.stringify: whole floats lose their '.0', exponents as JS writes them."""
    assert presets.js_num_str(30.0) == "30"
    assert presets.js_num_str(0.15) == "0.15"
    assert presets.js_num_str(0.00001) == "0.00001"
    assert presets.js_num_str(1e-7) == "1e-7"
    assert presets.js_num_str(1e21) == "1e+21"
    assert presets.js_num_str(1.5e16) == "15000000000000000"
    assert presets.js_num_str(-0.0) == "0"
    assert presets.js_num_str(123.456) == "123.456"
    assert presets.js_num_str(24.5) == "24.5"


def test_frame_signature_is_the_js_string_and_sorted():
    spec = {"w": 1920, "h": 1080, "pingPong": 1, "crossfade": ""}
    out = presets.frame_signature({"zeta": 1, "alpha": [1, 2.0]}, spec, 'a"b')
    assert out == '{"opts":{"alpha":[1,2],"zeta":1},"spec":{"w":1920,"h":1080,"pingPong":true,"crossfade":false},"scene":"a\\"b","renderer":6}'


def test_preset_crop_keeps_aspect_and_stays_inside():
    cases = [(6000, 4000, tx, ty, ow, oh) for (ow, oh) in [(1080, 1920), (3840, 2160), (1080, 1080), (1920, 1080)]
             for tx in (0, 2999, 5999) for ty in (0, 1999, 3999)]
    for img_w, img_h, tx, ty, ow, oh in cases:
        c = presets.preset_crop(img_w, img_h, tx, ty, ow, oh)
        assert abs(c["w"] / c["h"] - ow / oh) < 1e-2, c
        assert 0 <= c["x"] and c["x"] + c["w"] <= img_w, c
        assert 0 <= c["y"] and c["y"] + c["h"] <= img_h, c


def test_frame_count_ping_pong_doubles():
    for duration, fps in [(10, 30), (2.5, 24), (120, 60)]:
        assert presets.frame_count(duration, fps, True) == 2 * presets.frame_count(duration, fps, False)
    assert presets.frame_count(10, 30, False) == 300


def test_time_at_turns_and_returns():
    assert presets.time_at(300, 600, True) == 1
    assert presets.time_at(599, 600, True) < 0.01
    assert presets.time_at(0, 600, True) == 0


def test_draft_plan_budget_and_length():
    for duration in (10, 120, 600, 3600):
        dp = presets.draft_plan(duration, 60, presets.DRAFT_LONG, 16 / 9)
        assert dp["bytes"] <= presets.DRAFT_BYTES
        assert dp["bytes"] == dp["frames"] * dp["width"] * dp["height"] * 4
    dp = presets.draft_plan(120, 60, 480, 16 / 9)
    assert abs(dp["frames"] / dp["fps"] - 120) < 1
    assert presets.draft_plan(10, 60, 480, 16 / 9)["fps"] == 15


def test_frame_path_is_five_digits_and_is_a_frame_file():
    assert presets.frame_path("/f", 7) == "/f/frame_00007.tif"
    assert presets.frame_path("/f", 12345) == "/f/frame_12345.tif"
    assert presets.is_frame_file("frame_00007.tif") and presets.is_frame_file(presets.frame_path("x", 42).split("/")[-1])


@pytest.mark.parametrize("name", ["frame_1.tif", "frame_00001.tif.bak", "notes.txt", "frame_0000a.tif", "frame_000001.tif", " frame_00001.tif"])
def test_is_frame_file_rejects_other_names(name):
    assert not presets.is_frame_file(name)


def test_header_centre_takes_pairs_or_mapping_and_rejects_nothing_useful():
    assert presets.header_centre({"RA": "315.41", "DEC": "68.08"}) == {"ra": 315.41, "dec": 68.08}
    assert presets.header_centre([("RA", "315.41"), ("DEC", "68.08")]) == {"ra": 315.41, "dec": 68.08}
    assert presets.header_centre([("EXPTIME", "300")]) is None
    assert presets.header_centre([]) is None


def test_sequence_plays_forward_then_back_once():
    assert [presets.sequence_frame(i, 4, True) for i in range(7)] == [0, 1, 2, 3, 2, 1, 0]
    assert presets.sequence_length(4, True) == 6
    assert [presets.sequence_frame(i, 4, False) for i in range(5)] == [0, 1, 2, 3, 0]


def test_parse_angle_units():
    assert abs(presets.parse_angle("21 36 42", True) - 324.175) < 1e-9
    assert abs(presets.parse_angle("-57 30 00", False) + 57.5) < 1e-9
    assert presets.parse_angle("324.18", True) == 324.18
    assert presets.parse_angle("north", False) is None


def test_progress_text_with_kept_frames():
    assert presets.progress_text("Rendering", 6, 10, 30000, 5) == "Rendering — 6 of 10 (60%) — about 2 min left"
    assert presets.progress_text("Solving", 0, 0, 400) == "Solving"
    assert presets.progress_fraction(0, 0) is None


def test_working_scale_and_rotation_examples():
    assert abs(presets.working_scale(6000, 4000) - 0.64) < 1e-12
    assert presets.working_scale(3000, 2000) == 1
    assert presets.needs_rotation(3000, 5000, 3840, 2160) is True
    assert presets.needs_rotation(3000, 5000, 1080, 1080) is False


def test_preset_spec_ids_and_loops():
    v = presets.preset_spec("youtube_1080", "vertical")
    h = presets.preset_spec("youtube_1080", "horizontal")
    assert (v["w"], v["h"]) == (1080, 1920) and v["id"] != h["id"]
    assert presets.preset_spec("exhibition")["pingPong"] is True
    assert presets.preset_spec("youtube_1080", "horizontal", "crossfade")["crossfade"] is True
    obj = {"id": "x", "w": 10, "h": 20, "pingPong": False}
    assert presets.preset_spec(obj) is obj


def test_loop_frame_crossfade_starts_clean():
    f0 = presets.loop_frame(0, 100, 20)
    assert f0 == {"a": 0, "b": None, "alpha": 1}
