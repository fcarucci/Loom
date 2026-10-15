"""loomsiril.core.js_round (and its jsmath re-export) against JavaScript Math.round.

Ported function: the JavaScript built-in ``Math.round`` (Fly-Through library set;
golden values recorded by tools/oracle/js_round.js into tests/oracle/js_round.json).
Replays every recorded case: NaN equals NaN, -0 is compared as 0 (JSON does not keep
the sign of zero), and infinities compare exactly.
"""

from __future__ import annotations

import math

import pytest

from loomsiril import core, jsmath


def _same(got: float, want: float) -> bool:
    if isinstance(want, float) and math.isnan(want):
        return isinstance(got, float) and math.isnan(got)
    return got == want


def test_every_recorded_case(oracle) -> None:
    cases = oracle("js_round")["cases"]
    assert len(cases) >= 200
    for name, case in cases.items():
        got = core.js_round(case["in"])
        assert _same(got, case["out"]), (name, case, got)


@pytest.mark.parametrize("x, want", [
    (0.49999999999999994, 0),   # floor(x + 0.5) gave 1: the sum rounds up to 1.0
    (0.5, 1),
    (-0.5, 0),                  # JS gives -0; printed and used as 0
    (-0.0, 0),
    (1.5, 2),
    (2.5, 3),
    (-2.5, -2),                 # halves go toward +infinity, not to even
    (-1.5000000000000002, -2),  # just below the half: the nearest is -2
    (2 ** 52 + 1, 2 ** 52 + 1),  # the integer is exact; no +0.5 addition is made
    (2 ** 53, 2 ** 53),
    (1e21, 10 ** 21),
    (5e-324, 0),
])
def test_known_answers(x: float, want: int) -> None:
    assert core.js_round(x) == want


def test_finite_input_returns_a_python_int() -> None:
    for x in (2.5, -2.5, 0.49999999999999994, 1e21, 7):
        assert type(core.js_round(x)) is int


@pytest.mark.parametrize("x", [math.nan, math.inf, -math.inf])
def test_non_finite_passes_through(x: float) -> None:
    got = core.js_round(x)
    assert type(got) is float
    assert _same(got, x)


def test_matches_floor_plus_one_on_quarters() -> None:
    # Exact binary fractions: the fraction test must agree with the textbook answer.
    for k in range(-40, 41):
        for frac in (0.0, 0.25, 0.5, 0.75):
            x = k + frac
            assert core.js_round(x) == (k + 1 if frac >= 0.5 else k), x


def test_jsmath_reexports_the_same_function() -> None:
    assert jsmath.js_round is core.js_round
