"""JS semantics that Python lacks: one home for them (Loom ``script/lib/*.js``).

The JS never throws on a bad number and Python does: division by zero, ``sqrt`` of
a negative, ``acos`` out of [-1, 1] and ``sin`` of infinity raise in Python. Also
``Math.max``/``Math.min`` propagate NaN where Python's ``max``/``min`` skip it,
``parseFloat`` reads a prefix, ``String(number)`` and ``JSON.stringify`` have their
own digit rules. Every module that needs one of these imports it from here.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from loomsiril.core import js_round

__all__ = [
    "js_acos",
    "js_ceil",
    "js_div",
    "is_falsy",
    "js_floor",
    "js_max",
    "js_min",
    "js_or",
    "js_round",
    "js_sin",
    "js_sqrt",
    "json_string",
    "js_string",
    "number_str",
    "parse_float",
]


def js_div(a: float, b: float) -> float:
    """``a / b`` as JS computes it: Infinity, -Infinity or NaN for ``b == 0``, never an exception."""
    if b == 0:
        if a != a or a == 0:
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def js_max(a: float, b: float) -> float:
    """``Math.max(a, b)`` for two numbers: NaN in, NaN out (Python's ``max`` ignores it)."""
    if a != a or b != b:
        return math.nan
    return a if a >= b else b


def js_min(a: float, b: float) -> float:
    """``Math.min(a, b)`` for two numbers: NaN in, NaN out (Python's ``min`` ignores it)."""
    if a != a or b != b:
        return math.nan
    return a if a <= b else b


def js_sqrt(x: float) -> float:
    """``Math.sqrt``: NaN for a negative argument instead of ValueError."""
    return math.sqrt(x) if x >= 0 else math.nan


def js_acos(x: float) -> float:
    """``Math.acos``: NaN outside [-1, 1] instead of ValueError."""
    return math.acos(x) if -1.0 <= x <= 1.0 else math.nan


def js_sin(x: float) -> float:
    """``Math.sin``: NaN for an infinite argument instead of ValueError."""
    return math.sin(x) if math.isfinite(x) else math.nan


def js_floor(x: float) -> float:
    """``Math.floor`` as a float: non-finite values pass through as in JS."""
    return float(math.floor(x)) if math.isfinite(x) else x


def js_ceil(x: float) -> float:
    """``Math.ceil`` as a float: non-finite values pass through as in JS."""
    return float(math.ceil(x)) if math.isfinite(x) else x


# ---------------------------------------------------------------------------
# Text: parseFloat, String(value), String(number), JSON strings.
# ---------------------------------------------------------------------------

_JS_FLOAT = re.compile(r"\s*([+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)")


def parse_float(text: str) -> float:
    """``parseFloat``: the number at the start of the text, or NaN."""
    m = _JS_FLOAT.match(text)
    return float(m.group(1)) if m else math.nan


def js_string(v: Any) -> str:
    """``String(v)`` for the header's values: None is "null", a bool "true"/"false"."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return number_str(v)
    return str(v)


def number_str(v: float) -> str:
    """``String(number)`` in JS (Number#toString): the shortest round-trip digits, JS's exponent rules.

    ``30.0`` is "30", ``0.00001`` is "0.00001", ``1e-7`` is "1e-7", ``1e21`` is "1e+21".
    NaN and infinities are "NaN", "Infinity", "-Infinity".
    """
    x = float(v)
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    text = repr(abs(x))  # Python's repr is the shortest round-trip too
    mantissa, _, exp_text = text.partition("e")
    exp10 = int(exp_text) if exp_text else 0
    int_part, _, frac_part = mantissa.partition(".")
    digits_all = int_part + frac_part
    lead = len(digits_all) - len(digits_all.lstrip("0"))
    digits = digits_all.lstrip("0").rstrip("0")
    n = len(int_part) + exp10 - lead  # the decimal point sits n digits into `digits`
    k = len(digits)
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        exp_s = ("+" if e >= 0 else "-") + str(abs(e))
        body = (digits + "e" if k == 1 else digits[0] + "." + digits[1:] + "e") + exp_s
    return sign + body


_JSON_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def json_string(s: str) -> str:
    """A JSON string as JSON.stringify writes it: the short escapes, \\uXXXX (lower-case hex) for other controls
    and for a lone surrogate (U+D800 to U+DFFF). A high surrogate followed by a low one is one character, written
    as it is, as JSON.stringify does."""
    out = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        code = ord(ch)
        if 0xD800 <= code <= 0xDBFF and i + 1 < n and 0xDC00 <= ord(s[i + 1]) <= 0xDFFF:
            # a well-formed pair (two code units of one JS string) is one character, written as it is
            out.append(chr(0x10000 + ((code - 0xD800) << 10) + (ord(s[i + 1]) - 0xDC00)))
            i += 2
            continue
        if ch in _JSON_ESCAPES:
            out.append(_JSON_ESCAPES[ch])
        elif code < 0x20 or 0xD800 <= code <= 0xDFFF:
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
        i += 1
    return '"' + "".join(out) + '"'


# ---------------------------------------------------------------------------
# Falsiness: JS ``v || fallback`` and ``!v`` for a number (null, undefined, 0 and NaN are falsy).


def is_falsy(v: Optional[float]) -> bool:
    """JS falsiness of a number: null (None), undefined, 0 and NaN are falsy."""
    return v is None or v != v or v == 0


def js_or(v: Optional[float], fallback: float) -> float:
    """JS ``v || fallback`` for a number: ``fallback`` when ``v`` is falsy (see :func:`is_falsy`).

    Only numbers and None reach this function: its callers pass a detection's flux (a float),
    a spike_sigma (a float or None) or a MAD (a float). The JS truthiness of ''/[]/{} is
    therefore not modelled: a non-number is returned unchanged, as before.
    """
    return fallback if is_falsy(v) else v
