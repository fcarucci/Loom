"""The Stars-and-motion and HDR controls' table (loomsiril.fly_options): consistency, parsing, the engine's defaults,
the mapping into FlyJob.options and the frame signature, and the JS it was copied from (when the PixInsight sources are
next to the Siril ones: the Siril Docker image sees only siril/)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from loomsiril import colour, encode, fly_options as fo, geometry, pipeline as P
from loomsiril import presets as preset_mod
from loomsiril.frame import RenderOptions

JS_DIR = Path(__file__).resolve().parents[2] / "pixinsight" / "script"
needs_js = pytest.mark.skipif(not (JS_DIR / "FlyThrough.js").exists(), reason="the PixInsight sources are not here")


def by_key(key, preset=""):
    return next(o for o in fo.OPTIONS if o.key == key and o.preset == preset)


# --- the table -------------------------------------------------------------------------------------------------

def test_table_is_consistent():
    saved = [o.saved for o in fo.OPTIONS if o.saved]
    assert len(saved) == len(set(saved))
    assert {o.section for o in fo.OPTIONS} == set(fo.SECTIONS)
    for o in fo.OPTIONS:
        assert o.kind in (fo.SPIN, fo.SLIDER, fo.CHECK, fo.COMBO, fo.EDIT)
        assert o.tooltip.strip() and o.js.startswith("FlyThrough.js:")
        assert (o.label or o.check_text), o.key
        if o.kind in (fo.SPIN, fo.SLIDER):
            assert o.minimum <= o.default * o.scale <= o.maximum
        if o.kind == fo.COMBO:
            assert o.default in dict(o.choices)
        if o.kind == fo.CHECK:
            assert isinstance(o.default, bool) and o.check_text
    # one control per JS control: 9 in Stars and motion, 2 + a transfer per output size in HDR
    assert len(fo.options_in("motion")) == 9
    assert len(fo.options_in("hdr")) == 2 + len(preset_mod.PRESET_ORDER)
    assert {o.preset for o in fo.OPTIONS if o.preset} == set(preset_mod.PRESET_ORDER)


def test_every_default_parses_back_to_itself_through_the_widget():
    for o in fo.OPTIONS:
        if o.key == "travel":
            assert fo.parse(o, "") is None
            continue
        assert fo.parse(o, fo.shown(o, o.default)) == o.default, o.key
        assert fo.parse(o, fo.format_value(o, o.default).rstrip("%")) == o.default or o.kind in (fo.CHECK, fo.COMBO)


def test_defaults_are_the_engines():
    d = P.DIALOG_DEFAULTS
    assert by_key("easing").default == d["easing"]
    assert by_key("growth").default == d["growth"] == geometry.GROWTH_DEFAULT
    assert by_key("brightening").default is d["brightening"]
    assert by_key("backdropMotion").default == d["backdropMotion"] == geometry.BACKDROP_MOTION_DEFAULT
    assert by_key("twinkle").default == d["twinkle"] == geometry.TWINKLE_DEFAULT
    assert by_key("motionBlur").default is d["motionBlur"]
    assert by_key("bloom").default == d["bloom"]
    assert by_key("starSaturation").default == d["starSaturation"]
    assert by_key("starHdr").default is d["starHdr"]
    assert by_key("peak").default == encode.HDR_PEAK_DEFAULT == colour.HDR_PEAK_DEFAULT
    for pid in preset_mod.PRESET_ORDER:
        assert by_key("hdrTransfer", pid).default == P.HDR_DEFAULT_TRANSFER[pid] == colour.HDR_DEFAULT_TRANSFER[pid]


# --- parse, validate, format -----------------------------------------------------------------------------------

@pytest.mark.parametrize("key,raw,engine", [
    ("backdropMotion", 40, 0.4), ("backdropMotion", "40", 0.4), ("backdropMotion", "40%", 0.4), ("backdropMotion", 0, 0),
    ("backdropMotion", 100, 1.0), ("twinkle", 3, 0.03), ("twinkle", 10, 0.1), ("bloom", 200, 2.0), ("bloom", "0", 0),
    ("starSaturation", 0, 0), ("starSaturation", 200, 2.0), ("starSaturation", 150.4, 1.5),
    ("peak", 400, 400), ("peak", "4000", 4000), ("peak", 1000.0, 1000), ("growth", "0.15", 0.15), ("growth", 0, 0),
    ("growth", " 1 ", 1.0), ("travel", "", None), ("travel", None, None), ("travel", "12.5", 12.5),
    ("easing", "Linear", "linear"), ("easing", "smoothstep", "smoothstep"), ("motionBlur", False, False)])
def test_parse_accepts(key, raw, engine):
    got = fo.parse(by_key(key), raw)
    assert got == engine and (engine == 0 or type(got) is type(engine))


@pytest.mark.parametrize("key,raw", [
    ("backdropMotion", 101), ("backdropMotion", -1), ("backdropMotion", "abc"), ("twinkle", 11), ("bloom", 201),
    ("starSaturation", -5), ("peak", 399), ("peak", 4001), ("peak", ""), ("peak", float("nan")), ("growth", "abc"),
    ("growth", -0.1), ("growth", 1.5), ("growth", "inf"), ("travel", "0"), ("travel", "-3"), ("travel", "x"),
    ("easing", "bouncy"), ("bloom", None), ("bloom", True)])
def test_parse_rejects_naming_the_control(key, raw):
    opt = by_key(key)
    with pytest.raises(fo.OptionError) as err:
        fo.parse(opt, raw)
    assert (opt.label or opt.key) in str(err.value)


def test_problems_names_every_bad_value_and_never_raises():
    bad = {"growth": "oops", "bloom": 500, "twinkle": 3, "travel": "-1"}
    msgs = fo.problems(bad)
    assert len(msgs) == 3 and any("Growth" in m for m in msgs) and any("Bloom" in m for m in msgs)
    assert fo.problems({"growth": "0.2", "hdr_peak": 2000}) == []


def test_format_value():
    assert fo.format_value(by_key("starSaturation"), 1.0) == "100%"
    assert fo.format_value(by_key("bloom"), 1.0) == "100"
    assert fo.format_value(by_key("growth"), 0.15) == "0.15"
    assert fo.format_value(by_key("travel"), None) == "" and fo.format_value(by_key("travel"), 12.5) == "12.5"
    assert fo.format_value(by_key("easing"), "linear") == "Linear"


def test_load_values_falls_back_to_the_default_for_a_bad_saved_value():
    got = fo.load_values({"bloom": 0.5, "twinkle": "x", "hdr_peak": 99999, "easing": "bouncy", "growth": None, "star_hdr": False})
    assert got["bloom"] == 0.5 and got["twinkle"] == 0.03 and got["hdr_peak"] == 1000 and got["easing"] == "smoothstep"
    assert got["growth"] == 0.15 and got["star_hdr"] is False
    assert fo.load_values({}) == fo.defaults()


# --- enable rules ----------------------------------------------------------------------------------------------

def test_hdr_controls_are_grey_in_sdr_with_a_reason():
    sdr, hdr = fo.Context("sdr", ("youtube_1080",)), fo.Context("hdr", ("youtube_1080",))
    for key in ("peak", "starHdr"):
        assert not fo.enabled(by_key(key), sdr) and "HDR" in fo.reason(by_key(key), sdr)
        assert fo.enabled(by_key(key), hdr) and fo.reason(by_key(key), hdr) == ""
    assert not fo.enabled(by_key("hdrTransfer", "youtube_1080"), sdr)
    assert fo.enabled(by_key("hdrTransfer", "youtube_1080"), hdr)
    assert "not ticked" in fo.reason(by_key("hdrTransfer", "exhibition"), hdr)
    for o in fo.options_in("motion"):
        if o.key != "backdropMotion":
            assert fo.enabled(o, sdr) and fo.enabled(o, hdr)


def test_nebula_motion_is_grey_for_a_galaxy():
    assert not fo.enabled(by_key("backdropMotion"), fo.Context(target_type="galaxy"))
    assert fo.enabled(by_key("backdropMotion"), fo.Context(target_type="nebula"))


# --- the job's options, the engine and the frame signature -----------------------------------------------------

def render_opts(options, **job_kw):
    job = P.FlyJob(starless=[], stars=[], wcs={}, options=options, **job_kw)
    return P._render_opts(job, 1000.0, colour.colour_of(None), [])


def signature(opts, preset="youtube_1080"):
    spec = preset_mod.preset_spec(preset, "horizontal", "none")
    sig = {k: v for k, v in opts.items() if k not in ("colour", "sceneKey")}
    return preset_mod.frame_signature(sig, spec, "scene")


def test_defaults_leave_the_render_options_and_signature_as_they_were():
    for dyn in ("sdr", "hdr"):
        ctx = fo.Context(dyn, ("youtube_1080",))
        base = render_opts({}, dynamic=dyn)
        mine = render_opts(fo.job_options(fo.defaults(), ctx), dynamic=dyn)
        for k in ("easing", "growth", "brightening", "backdropMotion", "twinkle", "motionBlur", "bloom", "starSaturation",
                  "starHdr", "peak", "kernel"):
            assert mine[k] == base[k], k
        if dyn == "sdr":
            assert signature(mine) == signature(base)


def test_job_options_in_sdr_leave_out_the_hdr_only_ones():
    sdr = fo.job_options(fo.defaults(), fo.Context("sdr", ("youtube_1080",)))
    assert not {"peak", "starHdr", "hdrTransfer"} & set(sdr)
    assert {"easing", "growth", "brightening", "backdropMotion", "twinkle", "bloom", "motionBlur", "starSaturation"} <= set(sdr)
    hdr = fo.job_options(fo.defaults(), fo.Context("hdr", ("youtube_1080", "exhibition")))
    assert hdr["peak"] == 1000 and hdr["starHdr"] is True
    assert hdr["hdrTransfer"] == {"youtube_1080": "hlg", "exhibition": "pq"}


def test_travel_goes_in_only_when_given():
    ctx = fo.Context()
    assert "travel" not in fo.job_options(fo.defaults(), ctx) and "travel" not in fo.job_options(fo.defaults(), ctx, None)
    assert fo.job_options(fo.defaults(), ctx, 12)["travel"] == 12.0
    assert render_opts({"travel": 12.0})["travel"] == 12.0
    assert render_opts({})["travel"] == 200.0          # nebula at 1000 pc: 0.2 x D, the pipeline's own default


CHANGES = {"easing": "linear", "growth": 0.3, "brightening": False, "backdrop_motion": 0.8, "twinkle": 0.05,
           "bloom": 0.5, "motion_blur": False, "star_saturation": 1.5, "hdr_peak": 2000, "star_hdr": False,
           "hdr_transfer_youtube_1080": "pq"}


@pytest.mark.parametrize("saved,value", list(CHANGES.items()))
def test_changing_a_value_changes_the_signature_and_an_equal_one_does_not(saved, value):
    ctx = fo.Context("hdr", ("youtube_1080",))
    base_values = fo.defaults()
    base = signature(render_opts(fo.job_options(base_values, ctx), dynamic="hdr"))
    assert signature(render_opts(fo.job_options(dict(base_values), ctx), dynamic="hdr")) == base
    assert signature(render_opts(fo.job_options(dict(base_values, **{saved: base_values[saved]}), ctx), dynamic="hdr")) == base
    changed = signature(render_opts(fo.job_options(dict(base_values, **{saved: value}), ctx), dynamic="hdr"))
    assert changed != base


def test_travel_changes_the_signature():
    assert signature(render_opts({"travel": 10.0})) != signature(render_opts({"travel": 11.0}))
    assert signature(render_opts({"travel": 10.0})) == signature(render_opts({"travel": 10.0}))


def test_hdr_only_values_do_not_re_render_an_sdr_clip():
    ctx = fo.Context("sdr", ("youtube_1080",))
    a = signature(render_opts(fo.job_options(dict(fo.defaults(), hdr_peak=3000, star_hdr=False), ctx)))
    assert a == signature(render_opts(fo.job_options(fo.defaults(), ctx)))


def test_the_values_reach_the_renderer():
    values = dict(fo.defaults(), easing="linear", growth=0.3, brightening=False, backdrop_motion=0.8, twinkle=0.05, bloom=0.5,
                  motion_blur=False, star_saturation=1.5, hdr_peak=2000, star_hdr=False)
    ctx = fo.Context("hdr", ("youtube_1080", "exhibition"))
    opts = render_opts(fo.job_options(values, ctx, 25), dynamic="hdr")
    po = P._preset_options(dict(opts, duration=8.0), "exhibition")
    po["travel"] = opts["travel"]
    ro = P._render_options(dict(po, frameDt=0.1))
    assert isinstance(ro, RenderOptions)
    assert (ro.easing, ro.growth, ro.brightening, ro.backdrop_motion, ro.twinkle, ro.bloom, ro.motion_blur,
            ro.star_saturation, ro.star_hdr, ro.travel) == ("linear", 0.3, False, 0.8, 0.05, 0.5, False, 1.5, False, 25.0)
    assert ro.peak == 2000


def test_per_preset_transfer_reaches_the_preset_options():
    base = dict(render_opts({}, dynamic="hdr"), duration=8.0)
    assert P._preset_options(base, "youtube_1080")["transfer"] == "hlg"
    assert P._preset_options(base, "exhibition")["transfer"] == "pq"
    chosen = dict(base, hdrTransfer={"youtube_1080": "pq", "exhibition": "hlg"})
    assert P._preset_options(chosen, "youtube_1080")["transfer"] == "pq"
    assert P._preset_options(chosen, "exhibition")["transfer"] == "hlg"
    assert P._preset_options(dict(base, hdrTransfer={"youtube_1080": "junk"}), "youtube_1080")["transfer"] == "hlg"
    assert P._preset_options(dict(chosen, dynamic="sdr"), "youtube_1080")["transfer"] == "sdr"


def test_travel_note_names_the_cap_and_the_automatic_travel():
    assert fo.travel_note(950, "nebula", 1000) == "capped at 0.9 \u00d7 the distance"
    assert fo.travel_note(500, "nebula", 1000) == "" and fo.travel_note(5000, "galaxy", float("inf")) == ""
    assert fo.travel_note(None, "nebula", 1950) == "automatic: 390 pc"
    assert fo.travel_note(None, "galaxy", float("inf")) == "automatic: 200 pc"
    assert fo.travel_note(None, "nebula", None) == "automatic" and fo.travel_note(7, "nebula", None) == ""


def test_draft_key_follows_the_picture_options_only():
    """The production path: the dialog's values (their real key names) key the draft (draft.draft_key)."""
    from loomsiril import draft

    def key(values, dyn="hdr"):
        v = {**fo.defaults(), **values, "dynamic": dyn, "presets": ["youtube_1080", "exhibition"], "travel": 20.0,
             "duration": 8.0, "fps": 30, "loop": "none", "orientation": "landscape", "star_quality": "high", "mode": "files", "starless_path": "a", "stars_path": "b", "target_type": "nebula"}
        return draft.draft_key(v)

    base = key({})
    for saved, value in (("easing", "linear"), ("growth", 0.3), ("brightening", False), ("backdrop_motion", 0.8),
                         ("twinkle", 0.05), ("bloom", 0.5), ("motion_blur", False), ("star_saturation", 1.5)):
        assert key({saved: value}) != base, saved
    for saved, value in (("hdr_peak", 2000), ("star_hdr", False), ("hdr_transfer_exhibition", "hlg"),
                         ("hdr_transfer_youtube_1080", "pq")):
        assert key({saved: value}) == base, saved                                # a draft is SDR
    assert key({}, dyn="sdr") == base


def test_a_typed_travel_above_the_cap_is_capped_and_the_note_is_true():
    notes: list[str] = []
    job = P.FlyJob(starless=[], stars=[], wcs={}, options={"travel": 5000.0})
    opts = P._render_opts(job, 1000.0, colour.colour_of(None), notes)
    assert opts["travel"] == 900.0 and "travel capped at 0.9 x the distance" in notes
    notes = []
    opts = P._render_opts(P.FlyJob(starless=[], stars=[], wcs={}, options={"travel": 500.0}), 1000.0, colour.colour_of(None), notes)
    assert opts["travel"] == 500.0 and "travel capped at 0.9 x the distance" not in notes
    galaxy = P.FlyJob(starless=[], stars=[], wcs={}, target_type="galaxy", options={"travel": 5000.0})
    assert P._render_opts(galaxy, float("inf"), colour.colour_of(None), [])["travel"] == 5000.0


def test_job_from_dialog_carries_the_controls_into_the_job_options():
    import numpy as np

    from loomsiril import siril_bridge

    plane = np.zeros((8, 8), np.float32)
    header = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 83.8, "CRVAL2": -5.4, "CRPIX1": 4.5, "CRPIX2": 4.5,
              "CD1_1": -0.001, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 0.001, "NAXIS1": 8, "NAXIS2": 8}
    values = {"starless": [plane], "stars": [plane], "dynamic": "hdr", "presets": ["exhibition"], "bloom": 0.5, "travel": 7.0,
              "hdr_peak": 3000, "hdr_transfer_exhibition": "hlg", "hdr_transfer_youtube_1080": "pq"}
    job = siril_bridge.job_from_dialog(values, [plane], header, None)
    assert job.options["bloom"] == 0.5 and job.options["travel"] == 7.0 and job.options["peak"] == 3000
    assert job.options["hdrTransfer"] == {"exhibition": "hlg"} and job.options["easing"] == "smoothstep"
    plain = siril_bridge.job_from_dialog({"starless": [plane], "stars": [plane]}, [plane], header, None)
    assert plain.options == {}                                    # no controls in the values: nothing added
    mine = siril_bridge.job_from_dialog({**values, "options": {"bloom": 2.0}}, [plane], header, None)
    assert mine.options["bloom"] == 2.0                           # an explicit options dict wins


def test_the_saved_keys_are_all_kept_between_runs(tmp_path):
    from loomsiril import siril_bridge

    assert set(fo.saved_keys()) <= set(siril_bridge.OPTION_KEYS)
    siril_bridge.save_options({**fo.defaults(), "bloom": 0.25, "travel": 5.0}, tmp_path)
    back = siril_bridge.load_options(tmp_path)
    assert back["bloom"] == 0.25 and "travel" not in back and fo.load_values(back)["bloom"] == 0.25


# --- the JS ---------------------------------------------------------------------------------------------------

def js_number(text, name):
    m = re.search(r"^" + re.escape(name) + r"\s*=\s*([0-9.]+);", text, re.M)
    assert m, name
    return float(m.group(1))


@needs_js
def test_defaults_and_ranges_are_the_js():
    fly = (JS_DIR / "lib" / "Fly.js").read_text(encoding="utf-8")
    dlg = (JS_DIR / "FlyThrough.js").read_text(encoding="utf-8")
    assert by_key("growth").default == js_number(fly, "Fly.GROWTH_DEFAULT")
    assert by_key("backdropMotion").default == js_number(fly, "Fly.BACKDROP_MOTION_DEFAULT")
    assert by_key("twinkle").default == js_number(fly, "Fly.TWINKLE_DEFAULT")
    assert by_key("peak").default == js_number(fly, "Fly.HDR_PEAK_DEFAULT")
    transfer = dict(re.findall(r'(\w+):\s*"(hlg|pq)"', fly[fly.index("Fly.HDR_DEFAULT_TRANSFER"):][:300]))
    assert transfer == {pid: by_key("hdrTransfer", pid).default for pid in preset_mod.PRESET_ORDER}
    for name, key in (("nebulaSpin", "backdropMotion"), ("twinkleSpin", "twinkle"), ("bloomSpin", "bloom"), ("peakSpin", "peak")):
        lo, hi = re.search(name + r"\.minValue = (\d+); this\." + name + r"\.maxValue = (\d+)", dlg).groups()
        assert (by_key(key).minimum, by_key(key).maximum) == (int(lo), int(hi)), name
    lo, hi = re.search(r"saturationSlider\.setRange\( (\d+), (\d+) \)", dlg).groups()
    assert (by_key("starSaturation").minimum, by_key("starSaturation").maximum) == (int(lo), int(hi))
    assert by_key("peak").step == int(re.search(r"peakSpin\.stepSize = (\d+)", dlg).group(1))
    assert by_key("bloom").default * 100 == int(re.search(r"bloomSpin\.value = (\d+)", dlg).group(1))
    assert by_key("starSaturation").default * 100 == int(re.search(r"saturationSlider\.value = (\d+)", dlg).group(1))


@needs_js
def test_tooltips_are_the_js_tooltips():
    dlg = (JS_DIR / "FlyThrough.js").read_text(encoding="utf-8")
    flat = re.sub(r'"\s*\+\s*\n\s*"', "", dlg)
    for key in ("backdropMotion", "twinkle", "starSaturation", "bloom", "motionBlur", "peak", "starHdr"):
        o = by_key(key)
        head = o.tooltip[:40].replace("°", "\\u00b0")
        assert head in flat, key
