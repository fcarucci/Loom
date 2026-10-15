"""Anomaly tags: ALTITUDE / FOCUS / SEEING / TRACKING / CLOUD / DROPPED (A/F/S/T/C/D).

Every channel is a synthetic one of 20 frames, five minutes apart, built by hand so that each rule of the
documented table fires on its own frame. The altitude-from-header tests skip until that module is on the branch.
"""

from __future__ import annotations

import copy

import pytest

from fs_helpers import metrics, sharp_channel, times_for, channel, states

frames = pytest.importorskip("loomsiril.frames")
fl = pytest.importorskip("loomsiril.frames_flags")

BADGE = {"altitude": "A", "focus": "F", "seeing": "S", "tracking": "T", "cloud": "C", "dropped": "D"}
LOW_ALT_FLUX = 830.0     # 1000 dimmed by extinction alone at 25 degrees (airmass 2.37): 1000 / 10**(0.06 * 1.366)


def flags(lst, times="auto", comparable: bool = True) -> list[list[str]]:
    return fl.anomaly_flags(lst, comparable, times_for(len(lst)) if times == "auto" else times)


def only(f: list[list[str]], at: dict[int, list[str]]) -> None:
    """Exactly these frames carry exactly these flags."""
    assert {i: x for i, x in enumerate(f) if x} == at


def test_a_clean_channel_has_no_tags() -> None:
    """Frames that look wrong against the rest of their channel are tagged: a channel of sharp, round, bright
    frames is not tagged at all."""
    only(flags(sharp_channel()), {})
    only(flags(sharp_channel(altitude=60.0)), {})


def test_tags_and_letters() -> None:
    """ALTITUDE A, FOCUS F, SEEING S, TRACKING T, CLOUD C, DROPPED D."""
    assert fl.FLAG_BADGE == BADGE
    assert fl.FLAG_TAG == {k: k.upper() for k in BADGE}


# ---- TRACKING ------------------------------------------------------------------------------------------------------


def test_tracking_eccentricity_more_than_three_sigma_above_the_median() -> None:
    """TRACKING: eccentricity more than 3σ above the channel's median (median 0.30, σ 0.0148 -> cut at 0.3445)."""
    lst = sharp_channel()
    lst[6]["eccentricity"] = 0.36
    only(flags(lst), {6: ["tracking"]})
    lst[6]["eccentricity"] = 0.33
    only(flags(lst), {})


# ---- CLOUD ---------------------------------------------------------------------------------------------------------


def test_cloud_flux_25_percent_below_the_median() -> None:
    """CLOUD: star flux 25% below the median beyond what extinction explains (median 1000)."""
    lst = sharp_channel()
    lst[4]["psfFlux"] = 740.0
    only(flags(lst), {4: ["cloud"]})
    lst[4]["psfFlux"] = 760.0
    only(flags(lst), {})


def test_cloud_background_5_percent_above_the_median() -> None:
    """CLOUD: ... or background 5% above it (median 100)."""
    lst = sharp_channel()
    lst[8]["background"] = 106.0
    only(flags(lst), {8: ["cloud"]})
    lst[8]["background"] = 104.0
    only(flags(lst), {})


def test_cloud_means_beyond_extinction() -> None:
    """CLOUD: star flux 25% below the median beyond what extinction explains: the same dimming is cloud at high
    altitude, but not at 25 degrees where the airmass is 2.37 (700 raw is 845 corrected, 17% down)."""
    lst = sharp_channel(altitude=60.0)
    lst[10]["psfFlux"] = 700.0
    lst[11]["psfFlux"] = 700.0
    lst[11]["altitude"] = 25.0
    f = flags(lst)
    assert f[10] == ["cloud"]
    assert f[11] == []


# ---- DROPPED ---------------------------------------------------------------------------------------------------------


def test_dropped_below_ten_percent_of_the_median_star_count() -> None:
    """DROPPED: fewer than 10% of the channel's median star count (median 200 -> fewer than 20)."""
    lst = sharp_channel()
    lst[2]["stars"] = 15
    only(flags(lst), {2: ["dropped"]})
    lst[2]["stars"] = 25
    only(flags(lst), {})


def test_a_low_star_count_alone_names_nothing() -> None:
    """A low star count alone names nothing: half the usual stars, with everything else normal, is not tagged."""
    lst = sharp_channel()
    lst[2]["stars"] = 100
    only(flags(lst), {})


# ---- FOCUS / SEEING / ALTITUDE -----------------------------------------------------------------------------------


def test_seeing_one_wide_frame_between_sharp_ones() -> None:
    """SEEING: FWHM 20% above after the correction, on one frame between sharp neighbours."""
    lst = sharp_channel()
    lst[10]["fwhm"] = 4.2
    only(flags(lst), {10: ["seeing"]})


def test_not_quite_wide_enough_is_not_tagged() -> None:
    """FWHM 20% above the sharpest quarter of the channel (sharpest quarter 3.02 -> wide above 3.62)."""
    lst = sharp_channel()
    lst[10]["fwhm"] = 3.5
    only(flags(lst), {})


def test_focus_wide_across_consecutive_frames() -> None:
    """FOCUS: FWHM 20% above even after the airmass correction, across consecutive frames."""
    lst = sharp_channel()
    for i in (10, 11, 12, 13):
        lst[i]["fwhm"] = 4.2
    only(flags(lst), {10: ["focus"], 11: ["focus"], 12: ["focus"], 13: ["focus"]})


def test_without_frame_times_a_blurred_frame_is_focus() -> None:
    """Without frame times a blurred frame is FOCUS."""
    lst = sharp_channel()
    lst[10]["fwhm"] = 4.2
    only(flags(lst, times=None), {10: ["focus"]})
    partial = times_for(20)
    partial[3] = None
    only(flags(lst, times=partial), {10: ["focus"]})


def test_altitude_a_run_of_wide_frames_at_low_altitude() -> None:
    """ALTITUDE: FWHM 20% above the sharpest quarter of the channel, not once corrected for airmass, and the frames
    beside it in time are wide too."""
    lst = sharp_channel(altitude=60.0)
    for i in (14, 15, 16, 17):
        lst[i].update(altitude=25.0, fwhm=5.0, psfFlux=LOW_ALT_FLUX)
    only(flags(lst), {i: ["altitude"] for i in (14, 15, 16, 17)})


def test_one_wide_frame_is_never_altitude() -> None:
    """Altitude moves slowly, so one wide frame is never ALTITUDE; it is SEEING."""
    lst = sharp_channel(altitude=60.0)
    lst[10].update(altitude=25.0, fwhm=5.0, psfFlux=LOW_ALT_FLUX)
    only(flags(lst), {10: ["seeing"]})


def test_the_airmass_correction_is_skipped_without_an_altitude() -> None:
    """Without an altitude the airmass correction is skipped: a run of wide frames with no altitude is FOCUS."""
    lst = sharp_channel()
    for i in (14, 15, 16, 17):
        lst[i]["fwhm"] = 5.0
    only(flags(lst), {i: ["focus"] for i in (14, 15, 16, 17)})


def test_neighbours_are_taken_in_time_order_not_listing_order() -> None:
    """The frames beside it in time: the same night listed backwards (times falling) is tagged the same."""
    lst = sharp_channel(altitude=60.0)
    lst[3]["fwhm"] = 4.2
    for i in (12, 13):
        lst[i]["fwhm"] = 4.2
    forward = flags(lst)
    only(forward, {3: ["seeing"], 12: ["focus"], 13: ["focus"]})
    back = flags(list(reversed(lst)), times=list(reversed(times_for(20))))
    assert back == list(reversed(forward))


# ---- every tag on its own frame, in one channel ----------------------------------------------------------------------


def _night() -> list[dict]:
    lst = sharp_channel(altitude=60.0)
    lst[1]["eccentricity"] = 0.6                                  # T
    lst[3]["psfFlux"] = 700.0                                     # C
    lst[5]["stars"] = 15                                          # D
    lst[8]["fwhm"] = 4.2                                          # S
    lst[12]["fwhm"] = lst[13]["fwhm"] = 4.2                       # F, F
    for i in (16, 17, 18):
        lst[i].update(altitude=25.0, fwhm=5.0, psfFlux=LOW_ALT_FLUX)   # A, A, A
    return lst


def test_each_tag_fires_on_its_own_frame() -> None:
    """Six rules, one frame each (the focus and altitude ones as runs): T, C, D, S, F F, A A A and nothing else."""
    f = flags(_night())
    only(f, {1: ["tracking"], 3: ["cloud"], 5: ["dropped"], 8: ["seeing"], 12: ["focus"], 13: ["focus"],
             16: ["altitude"], 17: ["altitude"], 18: ["altitude"]})
    letters = {i: "".join(BADGE[k] for k in x) for i, x in enumerate(f) if x}
    assert letters == {1: "T", 3: "C", 5: "D", 8: "S", 12: "F", 13: "F", 16: "A", 17: "A", 18: "A"}


def test_tags_show_on_the_preview_and_in_the_summary() -> None:
    """Tags show on the preview, as letters on the filmstrip, and in the summary line."""
    lst = _night()
    f = flags(lst)
    s = fl.flag_summary(f)
    assert s.startswith("9 flagged: ")
    for part in ("1 cloud", "2 focus", "1 seeing", "3 altitude", "1 tracking", "1 dropped"):
        assert part in s
    assert fl.flag_summary(flags(sharp_channel())) == ""
    row = {"path": "/x/a.fit", "state": "approved", "override": None, "reasons": []}
    tags = fl.frame_tags(row, f[3], True, False)
    assert [t["text"] for t in tags] == ["CLOUD"]


# ---- advisory only ---------------------------------------------------------------------------------------------------


def test_tags_never_change_a_verdict() -> None:
    """Tags are advisory: they never change a verdict."""
    lst = _night()
    before = copy.deepcopy(lst)
    ch_before = channel("Ha", lst)
    verdicts = states(ch_before)
    f = flags(lst)
    assert lst == before                                          # computing tags changes no measurement
    ch_after = channel("Ha", lst)
    assert states(ch_after) == verdicts
    # a frame tagged CLOUD on its background alone is still approved: background is not a rejecting criterion
    bg = sharp_channel()
    bg[8]["background"] = 110.0
    assert flags(bg)[8] == ["cloud"]
    assert states(channel("Ha", bg))[8] == "approved"
    assert any(f)


# ---- when nothing is tagged ----------------------------------------------------------------------------------------


def test_fewer_than_five_usable_values_gives_no_tag() -> None:
    """A metric needs 5 usable values: four frames, however wrong one is, are not tagged; five are."""
    four = [metrics(3.0, 0.30, 200, 50, psfFlux=1000.0, background=100.0) for _ in range(3)]
    four.append(metrics(3.0, 0.30, 5, 50, psfFlux=300.0, background=200.0))
    only(flags(four), {})
    five = four + [metrics(3.0, 0.30, 200, 50, psfFlux=1000.0, background=100.0)]
    f = flags(five)
    assert set(f[3]) == {"dropped", "cloud"}


def test_a_metric_with_only_four_usable_values_cannot_fire() -> None:
    """A metric needs 5 usable values: 20 frames of which only 4 have a flux figure give no CLOUD from flux."""
    lst = sharp_channel()
    for i, m in enumerate(lst):
        if i >= 4:
            m.pop("psfFlux")
    lst[0]["psfFlux"] = 300.0
    only(flags(lst), {})


def test_a_channel_that_is_not_comparable_is_not_tagged() -> None:
    """Channels not comparable or without a filter are not tagged."""
    assert flags(_night(), comparable=False) == [[] for _ in range(20)]


def test_frames_without_measurements_are_skipped() -> None:
    """An unmeasurable frame has no tags and does not stop the others being tagged."""
    lst = sharp_channel()
    lst[7] = None
    lst[2]["stars"] = 15
    f = flags(lst)
    assert f[7] == []
    assert f[2] == ["dropped"]


# ---- altitude, from the header -------------------------------------------------------------------------------------


def _kw(**values):
    return lambda name: values.get(name)


def test_altitude_from_airmass_keyword() -> None:
    """With an altitude the airmass correction runs: an AIRMASS of 2.0 is an altitude of 30 degrees."""
    fa = pytest.importorskip("loomsiril.frames_altitude")
    assert fa.altitude_from_header(_kw(AIRMASS="2.0")) == pytest.approx(30.0, abs=1e-9)
    assert fa.altitude_from_header(_kw(CENTALT="45.5")) == pytest.approx(45.5)
    assert fa.altitude_from_header(_kw()) is None


def test_altitude_computed_from_the_sky_position() -> None:
    """The altitude of the celestial pole is the observer's latitude, at any time of night."""
    fa = pytest.importorskip("loomsiril.frames_altitude")
    for stamp in ("2026-10-01T20:00:00", "2026-10-02T03:30:00"):
        a = fa.altitude_from_header(_kw(RA="0.0", DEC="90.0", SITELAT="41.2", SITELONG="-73.0", **{"DATE-OBS": stamp}))
        assert a == pytest.approx(41.2, abs=0.05)
