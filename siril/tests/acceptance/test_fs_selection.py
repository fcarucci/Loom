"""Selection: presets, the relative cut, criteria, typed limits, the keep count and per-channel judging.

Expected values are worked out by hand (numpy median and MAD in ``fs_helpers``), never by calling the code under
test a second time.
"""

from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from fs_helpers import ladder, mad_sigma, metrics, channel, rejected_indices, states

frames = pytest.importorskip("loomsiril.frames")

PRESET_K = {"lenient": 3.0, "balanced": 2.5, "strict": 2.0}


def _limit_hi(values: list[float], k: float) -> float:
    return float(np.median(values) + k * mad_sigma(values))


def _limit_lo(values: list[float], k: float) -> float:
    return float(np.median(values) - k * mad_sigma(values))


def _with(settings: dict, **kw) -> dict:
    s = copy.deepcopy(settings)
    s.update(kw)
    return s


# ---- presets ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name,k", sorted(PRESET_K.items()))
def test_preset_sets_k(name: str, k: float) -> None:
    """Each channel has its own preset, which sets `k`, the width of the cut in normalised MADs (Lenient 3.0,
    Balanced 2.5, Strict 2.0)."""
    out = frames.apply_preset(frames.default_settings(), name)
    assert out["preset"] == name
    assert out["k"] == k


def test_preset_change_keeps_a_k_edited_by_hand() -> None:
    """Editable k: with k edited by hand a preset change keeps k."""
    s = frames.default_settings()
    s["k"], s["kEdited"] = 2.2, True
    for name in PRESET_K:
        out = frames.apply_preset(s, name)
        assert out["k"] == 2.2
        assert out["preset"] == name


@pytest.mark.parametrize("name,k", sorted(PRESET_K.items()))
def test_preset_decides_which_frame_is_cut(name: str, k: float) -> None:
    """The dialog shows the actual count: the cut for a channel moves with the preset's k."""
    rows = ladder(20)
    rows[19]["fwhm"] = 3.55   # between the Strict (about 3.49) and Balanced (about 3.56) cuts
    fw = [m["fwhm"] for m in rows]
    limit = _limit_hi(fw, k)
    ch = channel("Ha", rows, frames.apply_preset(frames.default_settings(), name))
    assert (19 in rejected_indices(ch)) == (3.55 > limit)
    if name == "strict":
        assert 19 in rejected_indices(ch)
    if name == "lenient":
        assert 19 not in rejected_indices(ch)


# ---- no cut below ten measurements -------------------------------------------------------------------------


def test_no_relative_cut_below_ten_valid_measurements() -> None:
    """Below 10 valid measurements in a channel the cut does not run (9 valid frames, one terrible: none rejected)."""
    rows = ladder(8) + [metrics(fwhm=20.0)]
    assert len(rows) == 9
    ch = channel("Ha", rows)
    assert rejected_indices(ch) == []
    assert states(ch) == ["approved"] * 9


def test_the_cut_runs_at_ten_valid_measurements() -> None:
    """At 10 valid measurements the cut runs (10 frames, one terrible: it is rejected, the good ones are kept)."""
    rows = ladder(9) + [metrics(fwhm=20.0)]
    assert len(rows) == 10
    ch = channel("Ha", rows)
    assert rejected_indices(ch) == [9]


def test_unmeasurable_frames_do_not_count_towards_ten() -> None:
    """Below 10 valid measurements the cut does not run: frames with no usable measurement are not valid ones."""
    rows = ladder(8) + [metrics(fwhm=20.0)] + [metrics(stars=0) for _ in range(5)] + [None]
    ch = channel("Ha", rows)
    assert rejected_indices(ch) == []


# ---- default criteria ----------------------------------------------------------------------------------------


def test_default_rejecting_criteria() -> None:
    """By default only FWHM, eccentricity and star count reject. PSF SNR does not."""
    g = frames.default_settings()["gating"]
    assert g["fwhm"] and g["eccentricity"] and g["stars"]
    assert not g["psfSNR"]


def test_a_frame_terrible_only_in_psf_snr_is_kept_by_default() -> None:
    """PSF SNR ranks frames but does not reject by default."""
    rows = ladder(20)
    rows[7]["psfSNR"] = 5.0
    ch = channel("Ha", rows)
    assert rejected_indices(ch) == []


def test_psf_snr_rejects_once_switched_on() -> None:
    """You can turn PSF SNR on per channel."""
    rows = ladder(20)
    rows[7]["psfSNR"] = 5.0
    s = frames.default_settings()
    s["gating"] = dict(s["gating"], psfSNR=True)
    ch = channel("Ha", rows, s)
    assert rejected_indices(ch) == [7]
    other = channel("OIII", rows)          # another channel with default knobs is unaffected
    assert rejected_indices(other) == []


@pytest.mark.parametrize("which,bad", [("fwhm", 9.0), ("eccentricity", 0.9), ("stars", 20)])
def test_each_default_criterion_rejects(which: str, bad: float) -> None:
    """By default only FWHM, eccentricity and star count reject: each one does reject a frame terrible in it."""
    rows = ladder(20)
    rows[5][which] = bad
    ch = channel("Ha", rows)
    assert rejected_indices(ch) == [5]
    assert ch["rows"][5]["failing"] == [which]


# ---- typed limits ----------------------------------------------------------------------------------------------


def _typed_fwhm(limit: float) -> dict:
    return frames.with_limit(frames.default_settings(), "fwhm", limit)


def test_typed_limit_replaces_the_automatic_cut_for_that_metric() -> None:
    """Type a number to replace the automatic cut for that metric only."""
    rows = ladder(20)
    rows[12]["fwhm"] = 3.50                       # inside the automatic cut (about 3.56)
    assert 3.50 < _limit_hi([m["fwhm"] for m in rows], 2.5)
    assert rejected_indices(channel("Ha", rows)) == []
    ch = channel("Ha", rows, _typed_fwhm(3.45))
    assert 12 in rejected_indices(ch)
    # a typed LOOSE limit replaces the cut too: the frame the automatic cut would reject is now kept
    rows2 = ladder(20)
    rows2[12]["fwhm"] = 6.0
    assert rejected_indices(channel("Ha", rows2)) == [12]
    assert rejected_indices(channel("Ha", rows2, _typed_fwhm(10.0))) == []


def test_typed_limit_leaves_the_other_metrics_automatic() -> None:
    """A typed limit replaces the automatic cut for that metric only."""
    rows = ladder(20)
    rows[3]["eccentricity"] = 0.9
    rows[4]["stars"] = 20
    ch = channel("Ha", rows, _typed_fwhm(10.0))
    assert rejected_indices(ch) == [3, 4]


@pytest.mark.parametrize("preset", ["lenient", "balanced", "strict"])
def test_typed_limit_survives_a_preset_change(preset: str) -> None:
    """A typed limit stays when the preset changes."""
    rows = ladder(20)
    rows[12]["fwhm"] = 3.50
    s = frames.apply_preset(_typed_fwhm(3.45), preset)
    assert frames.limit_value(s, "fwhm") == 3.45
    assert 12 in rejected_indices(channel("Ha", rows, s))


@pytest.mark.parametrize("k", [1.0, 2.0, 3.0, 4.5])
def test_typed_limit_survives_a_k_change(k: float) -> None:
    """A typed limit stays when `k` changes."""
    rows = ladder(20)
    rows[12]["fwhm"] = 3.50
    s = _with(_typed_fwhm(3.45), k=k, kEdited=True)
    assert 12 in rejected_indices(channel("Ha", rows, s))


def test_clearing_a_typed_limit_goes_back_to_automatic() -> None:
    """Clear it to go back."""
    rows = ladder(20)
    rows[12]["fwhm"] = 3.50
    s = frames.with_limit(_typed_fwhm(3.45), "fwhm", None)
    assert frames.limit_value(s, "fwhm") is None
    assert rejected_indices(channel("Ha", rows, s)) == []


def test_unticking_a_criterion_disables_it() -> None:
    """Untick a criterion to switch it off."""
    rows = ladder(20)
    rows[6]["fwhm"] = 9.0
    assert rejected_indices(channel("Ha", rows)) == [6]
    s = frames.default_settings()
    s["gating"] = dict(s["gating"], fwhm=False)
    assert rejected_indices(channel("Ha", rows, s)) == []
    # also switches off a typed limit on that metric
    s2 = frames.with_limit(s, "fwhm", 3.2)
    assert rejected_indices(channel("Ha", rows, s2)) == []


def test_display_limit_reports_automatic_and_typed() -> None:
    """An untouched limit is automatic: it shows the cut `k` gives on this night; a typed one is reported as typed."""
    rows = ladder(20)
    ch = channel("Ha", rows)
    fw = [m["fwhm"] for m in rows]
    sn = [m["stars"] for m in rows]
    d = frames.display_limit("fwhm", ch["gates"], ch["settings"])
    assert d["auto"] is True
    assert d["value"] == pytest.approx(_limit_hi(fw, 2.5), rel=1e-9)
    d = frames.display_limit("stars", ch["gates"], ch["settings"])
    assert d["auto"] is True
    assert d["value"] == pytest.approx(_limit_lo(sn, 2.5), rel=1e-9)
    s = _typed_fwhm(3.45)
    d = frames.display_limit("fwhm", channel("Ha", rows, s)["gates"], s)
    assert d == {"value": 3.45, "auto": False}


def test_automatic_limit_has_no_value_below_ten_frames() -> None:
    """Below 10 valid measurements in a channel the cut does not run: there is no automatic limit to show."""
    ch = channel("Ha", ladder(9))
    d = frames.display_limit("fwhm", ch["gates"], ch["settings"])
    assert d["auto"] is True and d["value"] is None


# ---- keep count --------------------------------------------------------------------------------------------------


def _three_bad() -> list[dict]:
    rows = ladder(20)
    rows[2]["fwhm"] = 9.0
    rows[7]["eccentricity"] = 0.9
    rows[15]["stars"] = 20
    return rows


def test_keep_count_in_delete_mode_is_the_frames_left_in_place() -> None:
    """The keep count means what Run does: the frames left in place (delete mode)."""
    ch = channel("Ha", _three_bad())
    assert rejected_indices(ch) == [2, 7, 15]
    assert frames.keep_count(ch["rows"], True, False) == {"keep": 17, "total": 20}
    # a hand decision changes what Run does, and the count follows it
    ch["rows"][2]["override"] = "rescued"
    ch["rows"][0]["override"] = "condemned"
    assert frames.keep_count(ch["rows"], True, False) == {"keep": 17, "total": 20}   # 17 + 1 rescued - 1 condemned
    ch["rows"][1]["override"] = "condemned"
    assert frames.keep_count(ch["rows"], True, False)["keep"] == 16


def test_keep_count_in_copy_mode_is_the_frames_copied() -> None:
    """The keep count means what Run does: ... or when copying, the frames copied."""
    ch = channel("Ha", _three_bad())
    assert frames.keep_count(ch["rows"], True, True) == {"keep": 17, "total": 20}


def test_a_channel_switched_off_is_not_copied_at_all() -> None:
    """A channel switched off is not copied at all (and in delete mode nothing of it is removed)."""
    s = frames.default_settings()
    s["enabled"] = False
    ch = channel("Ha", _three_bad(), s)
    assert frames.keep_count(ch["rows"], False, True) == {"keep": 0, "total": 20}
    assert frames.keep_count(ch["rows"], False, False) == {"keep": 20, "total": 20}
    assert rejected_indices(ch) == []


def test_delete_list_matches_the_keep_count() -> None:
    """The keep count is the number of frames Run keeps: what the delete list names is the rest."""
    ch = channel("Ha", _three_bad())
    listed = [e["path"] for e in frames.build_manifest(ch["rows"])["entries"]]
    assert len(listed) == 3
    assert 20 - len(listed) == frames.keep_count(ch["rows"], True, False)["keep"]


# ---- per-channel normalisation -----------------------------------------------------------------------------------


def _scaled(rows: list[dict], f: float) -> list[dict]:
    return [metrics(m["fwhm"] * f, m["eccentricity"], m["stars"] / f, m["psfSNR"] * 3) for m in rows]


def test_channels_with_different_scales_are_judged_independently() -> None:
    """Per-channel normalisation by the channel's own medians: two channels on different scales are each judged
    against their own spread."""
    a = ladder(20)
    a[19]["fwhm"] = 6.2                      # terrible in a channel of 3 px stars
    b = _scaled(ladder(20), 2.0)             # a channel whose ordinary FWHM runs from 6.0 to 6.76
    b[19]["fwhm"] = 6.2                      # the same number, ordinary there
    assert rejected_indices(channel("Ha", a)) == [19]
    assert rejected_indices(channel("OIII", b)) == []


def test_scaling_a_channel_does_not_change_its_verdicts() -> None:
    """Per-channel normalisation: the same pattern on a different scale gives the same verdicts."""
    a = ladder(20)
    a[4]["fwhm"] = 9.0
    a[11]["stars"] = 20
    b = _scaled(a, 2.0)
    assert states(channel("Ha", a)) == states(channel("OIII", b))
    assert rejected_indices(channel("OIII", b)) == [4, 11]


def test_score_is_normalised_by_the_channels_own_medians() -> None:
    """Score for ranking is per channel: the frame at the channel median scores the sum of the weights (1.0) on any
    scale."""
    a = ladder(21)
    for rows in (a, _scaled(a, 2.0), _scaled(a, 0.5)):
        ch = channel("X", rows)
        assert ch["rows"][10]["score"] == pytest.approx(1.0, rel=1e-12)
        # a better frame scores higher than a worse one
        assert ch["rows"][0]["score"] > ch["rows"][20]["score"]
        assert all(math.isfinite(r["score"]) for r in ch["rows"])
