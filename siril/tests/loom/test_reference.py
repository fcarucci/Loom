"""The registration reference (loomsiril/loom/reference.py) against Loom's JavaScript and its own cases."""

from __future__ import annotations

import math

import pytest

from loomsiril.loom import reference as ref

SHARP_RICH = {"fwhm": 2.8, "stars": 3000}


def test_oracle_fallback_ranking(oracle) -> None:
    cases = oracle("loom_reference")["cases"]["ranking"]
    assert cases
    for case in cases:
        assert ref.registration_fallback_ranking(case["present"]) == case["out"], case["present"]


def test_oracle_usable_star_measure(oracle) -> None:
    cases = oracle("loom_reference")["cases"]["usable"]
    assert cases
    for case in cases:
        assert ref.usable_star_measure(case["q"]) is case["out"], case["q"]


def test_oracle_best_measured_channel(oracle) -> None:
    cases = oracle("loom_reference")["cases"]["best"]
    assert cases
    for case in cases:
        quality = case["quality"] or {}
        assert ref.best_measured_channel(case["ranked"], quality) == case["out"], case


def test_oracle_registration_reference(oracle) -> None:
    cases = oracle("loom_reference")["cases"]["reference"]
    assert cases
    for case in cases:
        assert ref.registration_reference(case["present"], case["quality"]) == case["out"], case


def test_oracle_registration_reason(oracle) -> None:
    cases = oracle("loom_reference")["cases"]["reference"]
    for case in cases:
        assert ref.registration_choice(case["present"], case["quality"]) == (case["out"], case["reason"]), case


def test_sqrt_separating_pair_picks_the_fwhm_over_sqrt_stars_winner() -> None:
    # FWHM/sqrt(stars): R 2.0/sqrt(50) = 0.283 beats G 3.0/sqrt(100) = 0.300. FWHM/stars would pick G.
    quality = {"G": {"fwhm": 3.0, "stars": 100}, "R": {"fwhm": 2.0, "stars": 50}}
    assert ref.best_measured_channel(["G", "R"], quality) == "R"
    assert ref.registration_reference(["G", "R"], quality) == "R"


def test_fallback_order_is_broadband_then_narrowband() -> None:
    assert ref.REGISTRATION_FALLBACK_ORDER == ("G", "R", "B", "H", "S", "O")


def test_unmeasured_extra_channels_follow_the_order_sorted() -> None:
    assert ref.registration_fallback_ranking(["Z", "A", "R", "G", "x"]) == ["G", "R", "A", "Z", "x"]


@pytest.mark.parametrize(
    "quality",
    [
        None,
        {},
        {"fwhm": math.nan, "stars": 10},
        {"fwhm": 3.0, "stars": 0},
        {"fwhm": 3.0},
        {"stars": 10},
    ],
)
def test_unusable_measurements(quality) -> None:
    assert ref.usable_star_measure(quality) is False


def test_missing_measurement_key_is_not_a_crash() -> None:
    assert ref.registration_reference(["R", "G"], {"R": {"fwhm": 3.0}}) == "G"


def test_reference_none_without_channels() -> None:
    assert ref.registration_reference([], {}) is None


def test_l_is_the_reference_whatever_the_measurements() -> None:
    assert ref.registration_reference(["R", "L", "G"], {"G": SHARP_RICH}) == "L"


def test_best_channel_is_the_lowest_fwhm_over_sqrt_stars() -> None:
    quality = {"H": {"fwhm": 3.2, "stars": 3000}, "S": {"fwhm": 3.4, "stars": 900}, "O": {"fwhm": 2.9, "stars": 150}}
    assert ref.best_measured_channel(["H", "S", "O"], quality) == "H"


def test_numpy_scalars_are_measurements() -> None:
    np = pytest.importorskip("numpy")
    quality = {"R": {"fwhm": np.float32(3.0), "stars": np.int64(2000)}, "G": {"fwhm": 3.5, "stars": 2000}}
    assert ref.registration_reference(["R", "G"], quality) == "R"


def test_quality_from_analysis_object_attributes() -> None:
    class Analysis:
        fwhm = 2.5
        nbstars = 300

    assert ref.quality_from_analysis(Analysis()) == {"fwhm": 2.5, "stars": 300}


def test_quality_from_siril_analysis_maps_fields() -> None:
    analysis = {"fwhm": 3.1, "nbstars": 812, "roundness": 0.9, "bgnoise": 0.002}
    record = ref.quality_from_analysis(analysis)
    assert record == {"fwhm": 3.1, "stars": 812}
    assert ref.usable_star_measure(record) is True
    assert ref.quality_from_analysis(None) is None
