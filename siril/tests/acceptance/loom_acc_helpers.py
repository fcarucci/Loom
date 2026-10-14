"""Shared helpers for the Loom acceptance tests.

Everything here is written from the behaviour Loom specifies, not from the port's code: expected values are computed
with the standard library (``hashlib``, ``json``, ``datetime``) or are literals recorded once from Loom's JavaScript.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from acc_helpers import REPO

LOOM_PKG = REPO / "loomsiril" / "loom"


def sha1_hex(text: str) -> str:
    """SHA-1 of the UTF-8 bytes of ``text``, lower-case hex."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def js_number(v: Any) -> str:
    """JavaScript ``String(v)`` for the scalars the cache parameters use."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return v
    if isinstance(v, int):
        return str(v)
    f = float(v)
    if f == 0:
        return "0"
    if f == int(f) and abs(f) < 1e21:
        return str(int(f))
    r = repr(f)
    mant, _, exp = r.partition("e")
    if exp:
        e = int(exp)
        return f"{mant}e{'+' if e >= 0 else '-'}{abs(e)}"
    return r


def js_json(v: Any) -> str:
    """``JSON.stringify`` for dict / list / str / bool / None / number (compact, insertion order, UTF-8 kept)."""
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + js_json(x) for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(js_json(x) for x in v) + "]"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return js_number(v)


def params_string(params: Any) -> str:
    """Loom's stable parameter string, re-derived here: sorted top-level keys, ``key=value`` joined by ``;``."""
    if params is None:
        return ""
    parts = []
    for k in sorted(params):
        v = params[k]
        parts.append(k + "=" + (js_json(v) if isinstance(v, (dict, list, tuple)) else js_number(v)))
    return ";".join(parts)


def chain_key(prev: Any, stage: str, params: Any) -> str:
    return sha1_hex("1|" + ("" if not prev else str(prev)) + "|" + stage + "|" + params_string(params))


def iso_ms(mtime_seconds: float) -> str:
    """JavaScript ``Date.toISOString()``: UTC, whole milliseconds, truncated."""
    ms = int(mtime_seconds * 1000)
    dt = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ".%03dZ" % (ms % 1000)


def import_loom(name: str):
    """Import ``loomsiril.loom.<name>`` or skip the test when that module has not been merged yet."""
    return pytest.importorskip("loomsiril.loom." + name)


def pick(mod: Any, *names: str) -> Any:
    """First attribute of ``mod`` among ``names``; skips the test (naming the gap) when none exists."""
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    pytest.skip(f"{mod.__name__} has none of {names} yet")


def public_modules() -> list[Path]:
    return sorted(p for p in LOOM_PKG.rglob("*.py")) if LOOM_PKG.is_dir() else []


def imported_roots(path: Path) -> set[str]:
    """Top-level module names a source file imports anywhere (module level or inside functions)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def top_level_imported_roots(path: Path) -> set[str]:
    """Like ``imported_roots`` but only imports that run at import time (not inside functions or classes' methods)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()

    def visit(nodes: Any) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                out.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                out.add(node.module.split(".")[0])
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for field in ("body", "orelse", "finalbody", "handlers"):
                    sub = getattr(node, field, [])
                    visit([h for s in sub for h in (s.body if isinstance(s, ast.ExceptHandler) else [s])])

    visit(tree.body)
    return out
