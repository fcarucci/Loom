"""The channel every other channel registers to: Loom's registration reference.

Ported from Loom lib/Pipeline.js:1973-2101 (REGISTRATION_FALLBACK_ORDER,
registrationFallbackRanking, usableStarMeasure, bestMeasuredChannel,
registrationReference). Pure: no image and no Siril call. The quality record of a
channel is ``{"fwhm": pixels, "stars": count}``; ``quality_from_analysis`` fills it
from Siril's per-channel analysis.
"""

from __future__ import annotations

import math
import numbers
from decimal import ROUND_HALF_UP, Decimal
from collections.abc import Mapping, Sequence
from typing import Any

# lib/Pipeline.js:1979-1990: broadband before narrowband, G first among broadband.
REGISTRATION_FALLBACK_ORDER: tuple[str, ...] = ("G", "R", "B", "H", "S", "O")


def registration_fallback_ranking(present: Sequence[str]) -> list[str]:
    """The channels present, in REGISTRATION_FALLBACK_ORDER; any other channel follows, sorted, once each."""
    ranked = [key for key in REGISTRATION_FALLBACK_ORDER if key in present]
    extra = sorted({key for key in present if key not in REGISTRATION_FALLBACK_ORDER})
    return ranked + extra


def _finite_number(value: Any) -> float | None:
    """The value as a float when it is a finite real number (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def usable_star_measure(quality: Mapping[str, Any] | None) -> bool:
    """A measurement counts when FWHM is finite and positive and there is at least one star."""
    if quality is None:
        return False
    fwhm = _finite_number(quality.get("fwhm"))
    stars = _finite_number(quality.get("stars"))
    return fwhm is not None and fwhm > 0 and stars is not None and stars >= 1


def best_measured_channel(ranked: Sequence[str], quality: Mapping[str, Mapping[str, Any] | None]) -> str | None:
    """The ranked channel with the lowest FWHM / sqrt(stars); an exact tie keeps the earlier one; None if none is usable."""
    best: str | None = None
    best_score = math.inf
    for key in ranked:
        measure = quality.get(key)
        if not usable_star_measure(measure):
            continue
        assert measure is not None
        score = float(measure["fwhm"]) / math.sqrt(float(measure["stars"]))
        if score < best_score:
            best = key
            best_score = score
    return best


def _fixed2(value: float) -> str:
    """JavaScript's Number.prototype.toFixed(2): exact value, ties rounded up."""
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _js_round(value: float) -> int:
    """JavaScript's Math.round for the positive star counts used here."""
    return int(math.floor(value + 0.5))


def registration_choice(
    present: Sequence[str], quality: Mapping[str, Mapping[str, Any] | None] | None = None
) -> tuple[str | None, str]:
    """The reference channel and the reason text Loom logs for it (lib/Pipeline.js:2056-2089)."""
    quality = quality or {}
    if "L" in present:
        return "L", "L is present, and L is always the reference"
    ranked = registration_fallback_ranking(present)
    if not ranked:
        return None, "no channels"
    best = best_measured_channel(ranked, quality)
    if best is None:
        return ranked[0], (
            "no L, and no usable star measurements; " + ranked[0]
            + " by the fixed order " + " > ".join(ranked)
        )
    others: list[str] = []
    for key in ranked:
        if key == best:
            continue
        measure = quality.get(key)
        if usable_star_measure(measure):
            assert measure is not None
            others.append(
                f"{key} {_fixed2(float(measure['fwhm']))} px / {_js_round(float(measure['stars']))} stars"
            )
        else:
            others.append(f"{key} unmeasured")
    winner = quality[best]
    assert winner is not None
    reason = (
        f"no L; {best} has the best FWHM/sqrt(stars): {_fixed2(float(winner['fwhm']))} px FWHM, "
        f"{_js_round(float(winner['stars']))} stars"
    )
    if others:
        reason += " (against " + ", ".join(others) + ")"
    return best, reason


def registration_reference(
    present: Sequence[str], quality: Mapping[str, Mapping[str, Any] | None] | None = None
) -> str | None:
    """The channel everything else registers to, or None when no channel is present.

    L whenever it is present. Otherwise the best measured channel, and when nothing is
    usable the first channel of the fixed fallback ranking.
    """
    return registration_choice(present, quality)[0]


def _field(source: Any, name: str) -> Any:
    """A field of a mapping, or an attribute of an object (such as sirilpy's ImageAnalysis)."""
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def quality_from_analysis(analysis: Any) -> dict[str, Any] | None:
    """The quality record for one channel from Siril's analysis ({fwhm, nbstars, ...} or an object with those attributes), or None."""
    if analysis is None:
        return None
    return {"fwhm": _field(analysis, "fwhm"), "stars": _field(analysis, "nbstars")}
