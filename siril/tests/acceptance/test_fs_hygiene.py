"""Public hygiene of the Frame Selector files: no local paths, lab host names, process words or task IDs.

Only tracked files are scanned (``git ls-files``, as the repository-wide hygiene test does; a plain tree walk
stands in where there is no repository). The forbidden words are assembled from fragments so that this file does
not match itself.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

import pytest

from fs_helpers import REPO

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}

#: Tracked files that belong to the Frame Selector, relative to the siril folder (a ``*`` stays inside one folder).
FILE_PATTERNS = [
    "loomsiril/frames*.py",
    "loomsiril/asiair*.py",
    "loomsiril/master_flat*.py",
    "Loom/loom_frame_selector.py",
    "tests/test_frames*.py",
    "tests/test_asiair*.py",
    "tests/test_master_flat*.py",
    "tests/acceptance/test_fs_*.py",
    "tests/acceptance/fs_helpers.py",
    "tools/oracle/fs_*.js",
    "tests/oracle/fs_*.json",
    "docs/*frame*",
]


def _join(*parts: str) -> str:
    return "".join(parts)


# Built from fragments: this file is itself scanned.
# A synthetic "/home/u" (a made-up user in path-handling cases) and the container's "/home/loom" are allowed, as in the
# repository-wide hygiene test.
LOCAL_PATHS = [_join("/ho", r"me/(?!loom\b|u\b)"), re.escape(_join("~/", "src")), re.escape(_join("/var/", "tmp"))]
HOSTS = [_join("docker", "dev")]
PROCESS_WORDS = [_join("sw", "arm"), _join("jud", "ge"), _join("se", "at"), _join("bri", "ef")]
PROCESS_WORDS_UPPER = [_join("P", "M"), _join("E", "L")]
ID_PATTERNS = [_join(r"\bT", r"\d+[a-z]?\b"), _join(r"\bPR", r"-\d+\b"), _join(r"\bD", "[1-3]", r"\b")]

RULES: list[tuple[str, re.Pattern[str]]] = (
    [("local path", re.compile(p)) for p in LOCAL_PATHS]
    + [("lab host name", re.compile(r"\b" + h + r"\b", re.I)) for h in HOSTS]
    + [("process word", re.compile(r"\b" + w + r"\b", re.I)) for w in PROCESS_WORDS]
    + [("process word", re.compile(r"\b" + w + r"\b")) for w in PROCESS_WORDS_UPPER]
    + [("task or requirement id", re.compile(p)) for p in ID_PATTERNS]
)


def _tracked() -> list[str]:
    try:
        r = subprocess.run(["git", "ls-files"], cwd=str(REPO), capture_output=True, text=True, check=True)
        files = [ln for ln in r.stdout.splitlines() if ln]
        if files:
            return files
    except Exception:
        pass
    out = []
    for p in REPO.rglob("*"):
        rel = p.relative_to(REPO)
        if p.is_file() and not any(x in SKIP_DIRS or x.endswith(".egg-info") for x in rel.parts):
            out.append(rel.as_posix())
    return out


def frame_selector_files() -> list[str]:
    files = _tracked()
    return sorted({f for f in files for pat in FILE_PATTERNS
                   if fnmatch.fnmatchcase(f, pat) and f.count("/") == pat.count("/")})


def scan(text: str) -> list[tuple[int, str, str]]:
    """(line number, rule, line) for every forbidden thing in a text."""
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        for label, rx in RULES:
            if rx.search(line):
                hits.append((n, label, line.strip()[:100]))
    return hits


def test_the_rules_catch_what_they_name() -> None:
    """The patterns find each kind of leak (samples are assembled from fragments)."""
    samples = {
        "local path": _join("see /ho", "me/x/y.py"),
        "lab host name": _join("ssh docker", "dev"),
        "process word": _join("the sw", "arm job"),
        "task or requirement id": _join("ported in T", "1a"),
    }
    for label, sample in samples.items():
        assert any(lab == label for _, lab, _ in scan(sample)), (label, sample)
    assert any(lab == "task or requirement id" for _, lab, _ in scan(_join("PR", "-3 says")))
    assert any(lab == "task or requirement id" for _, lab, _ in scan(_join("decision D", "2 is")))
    assert scan(_join("see ", "judge_row and a 2 D array")) == []
    assert scan("a frame with 12 stars and a D tag") == []


def test_there_are_frame_selector_files_to_scan() -> None:
    """The scan is not vacuous: the files that exist on this branch are found."""
    files = frame_selector_files()
    assert "loomsiril/frames.py" in files
    assert "tests/acceptance/test_fs_hygiene.py" in files


def test_no_local_paths_hosts_process_words_or_task_ids_in_frame_selector_files() -> None:
    """Nothing committed may contain lab host names, local paths, process words or task and requirement IDs."""
    bad = []
    for rel in frame_selector_files():
        try:
            text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        bad += [f"{rel}:{n}: {label}: {line}" for n, label, line in scan(text)]
    assert not bad, f"{len(bad)} hit(s):\n" + "\n".join(bad[:40])


def test_the_repository_wide_hygiene_check_also_passes_for_these_files() -> None:
    """The repository-wide list of internal names (hashed there) finds nothing in the Frame Selector files."""
    try:
        import test_pr9_docs_hygiene as repo_wide
    except ImportError:                                           # pragma: no cover
        pytest.skip("repository-wide hygiene test not importable")
    bad = []
    for rel in frame_selector_files():
        for n, line in enumerate((REPO / rel).read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if repo_wide._line_hit(line):
                bad.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not bad, f"{len(bad)} hit(s):\n" + "\n".join(bad[:40])
