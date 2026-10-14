"""README, CHANGELOG, CI file and repository hygiene."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys

import pytest

from acc_helpers import REPO

SKIP = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}


def _tracked_files() -> list:
    try:
        r = subprocess.run(["git", "ls-files"], cwd=str(REPO), capture_output=True, text=True, check=True)
        files = [REPO / ln for ln in r.stdout.splitlines() if ln]
        if files:
            return [f for f in files if f.exists()]
    except Exception:
        pass
    out = []
    for p in REPO.rglob("*"):
        rel = p.relative_to(REPO).parts
        if p.is_file() and not any(x in SKIP or x.endswith(".egg-info") for x in rel):
            out.append(p)
    return out


def _readme() -> str:
    return (REPO / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "label,pattern",
    [
        ("install", r"install"),
        ("usage", r"usage|how to use|quick ?start|command line"),
        ("limits", r"limit"),
        ("docker", r"docker"),
        ("licence", r"licen[cs]e"),
    ],
)
def test_readme_has_section(label: str, pattern: str) -> None:
    heads = [ln for ln in _readme().splitlines() if ln.lstrip().startswith("#")]
    assert any(re.search(pattern, h, re.I) for h in heads), f"README.md has no '{label}' heading; headings: {heads}"


def test_readme_mentions_starnet_and_gaia_attribution() -> None:
    text = _readme()
    assert re.search(r"starnet", text, re.I)
    assert re.search(r"gaia", text, re.I) and re.search(r"esa|dpac|attribution|acknowledg", text, re.I)


def test_changelog_has_unreleased() -> None:
    assert re.search(r"^#+\s*\[?Unreleased", (REPO / "CHANGELOG.md").read_text(encoding="utf-8"), re.I | re.M)


def test_ci_workflow_present_and_yamlish() -> None:
    ci = REPO.parent / ".github" / "workflows" / "siril.yml"  # the workflow lives at the root of the shared repository
    if not ci.parent.is_dir():
        pytest.skip("the workflow lives at the root of the shared repository, which this copy (Docker image) lacks")
    assert ci.is_file()
    text = ci.read_text(encoding="utf-8")
    assert "\t" not in text.replace("\r", ""), "YAML must not be indented with tabs"
    assert re.search(r"^name\s*:", text, re.M)
    assert re.search(r"^on\s*:|^\"on\"\s*:|^true\s*:", text, re.M)
    assert re.search(r"^jobs\s*:", text, re.M)
    assert re.search(r"^\s+runs-on\s*:", text, re.M) and re.search(r"^\s+(steps|run)\s*:", text, re.M)


def test_no_large_files_except_gaia_fixture_and_oracles() -> None:
    """1 MB cap; exceptions: the Gaia fixture, and synthetic oracle JSON (tests/oracle/*.json, 5 MB cap)."""
    big = []
    for p in _tracked_files():
        rel = p.relative_to(REPO).as_posix()
        size = p.stat().st_size
        if rel == "tests/fixtures/ic1396-gaia-g16.tsv":
            continue
        if rel.startswith("tests/oracle/") and rel.endswith(".json"):
            if size > 5_000_000:
                big.append(f"{rel} ({size} > 5 MB oracle cap)")
            continue
        if size > 1_000_000:
            big.append(rel)
    assert not big, big


def test_no_images_committed_outside_fixtures() -> None:
    bad = []
    for p in _tracked_files():
        rel = p.relative_to(REPO).as_posix()
        if p.suffix.lower() in {".fits", ".fit", ".xisf", ".tif", ".tiff", ".png"} and not rel.startswith("tests/fixtures/"):
            bad.append(rel)
    assert not bad, bad


def test_nothing_vendored_under_docker_vendor_starnet_except_readme() -> None:
    bad = []
    for p in _tracked_files():
        rel = p.relative_to(REPO).as_posix()
        if rel.startswith("docker/vendor/starnet/") and p.name not in ("README.md", ".gitignore"):
            bad.append(rel)
    assert not bad, bad


# Tracked text must not name the machines, local paths or team process the project was built with.
# The names themselves are not written here (this file is scanned like every other tracked file): words and
# host-like tokens are compared by a 16-hex-digit sha256 prefix. Generic patterns are regular expressions.
_HOST_HASHES = {
    '0ae651735db3e2fa', '1149610810ec10e0', '1bc3ad87ba71aa77', '1e1b93cb41301fec', '2a8792da674dca4c',
    '373bcee8b7035618', '4af93ce97ca9eee1', '6577b979a3043ef2', '74947c1f8527e2aa', '960bee785e1a11b5',
    'a524f1df325fecac', 'a5951d481a5c4049', 'c39cab9c0825dfdf', 'cc9f3dcca81fe7e2', 'e9d8dcb5f692406c',
    'f11338ea9bbd669a', 'fdd9f42db8c184d0', '26a52d0938cd1ee2',
}  # host-like tokens, matched against a token and each of its dot-suffixes
_NAME_HASHES = {
    '078cd40e77a9bbe9', '095eec9087fd3367', '0a1f098af0dda3cd', '0ba602920de5fbe5', '0e26467131a9a447',
    '112f65c934eb85ad', '17d1d067a2bd519a', '1a0cc3f8227698f4', '1c102ca4ece4e82f', '1e1b93cb41301fec',
    '2199517b099eacb7', '257133883a159e97', '2c10dccadaf2a476', '2f040aa70afbfcfa', '2ff998c9f0dc8be7',
    '30bb4bf488fb949c', '345ecf72bdf22997', '373ca259b79c359b', '37581dfd0e3ffa14', '3ba01682cd5c0264',
    '3bed717d06835e17', '3c2650c9526a2856', '3d51bb1cab5d043b', '4387345cf7b7ad03', '44eb92b46360c22a',
    '5073fac86dfc63c2', '525cdaca729ff505', '536f12b7e8ea5d93', '538517d687730599', '549a7863fc9b69c6',
    '5647c06276c10cbd', '5b9aaffa93082142', '686c2169955177b6', '6e39add720fb9b9c', '7238ff56b274c677',
    '72dbeb0298cc6b21', '73d5cd178305701a', '74a545120525b321', '76b54e681d3cdee5', '76f1b4e5478e037e',
    '78193ef266c1e3c2', '7955ec274b288219', '7b3866620d9f88fd', '828b0f0ad660d767', '83d15dec6c82566b',
    '869aef433a36fa63', '8c24deeddcf12bec', '912037460950d5a8', '9187591306bc738e', '982de085db999019',
    'a222f362c2e74e34', 'a5951d481a5c4049', 'a5e0a450b0e89283', 'a68088694ca2f481', 'a9778b735e6a9ffd',
    'aa475625981493dc', 'aae8647810e26538', 'abcab8ae4c26298a', 'b15d80784443ca6c', 'b2ab30864a891028',
    'b632cedee0284520', 'b6751f39b19e9d45', 'ba103fc0bc6125a6', 'be273005658a7384', 'c29f16979e7ef4e4',
    'c82f992d4a03d9f0', 'c8869105e6cab691', 'c976d6caa1b9169b', 'cf98421fe5fb5775', 'db94348b2902274b',
    'e484013bea280d28', 'e92517369b06839e', 'ea03efd1e21ea07b', 'ed3d6776be7908c0', 'f0a184ef9691553a',
    'f311b4da575cd067', 'f3901c1d3b4b0d37', 'f3fcf6369e54b0bc', 'f57f1c5100f0d104', 'f91fbda220d23715',
    'fb275d6bb065a4cd', 'fbc236439652dc3c', 'fc076ca0b13deada', 'fd3c8cd511f115b9', 'fe54279d5fb295a9',
}  # lower-case word sequences of 1 to 4 words
_UPPER_HASHES = {
    '6b987654a4116384', '737fdab9cd604c40',
}  # two upper-case role abbreviations ("pm" is proper motion in the code)
_FORBIDDEN_RE = [re.compile(p, f) for p, f in [
    (r"\.home\.arpa\b", re.I),
    (r"~/(?:src|work)\b|/var/t[m]p\b|/home/(?!loom\b|u\b)[\w.-]+", 0),  # allowed: container HOME /home/loom, synthetic /home/u
    (r"\bevidence/|\bbriefs/|\bboard\b", re.I),
    (r"\breview-(?:e|r|qa|t)[0-9a-z-]*\.md\b|\breview-e[0-9]", re.I),
    # assistant model names (allowed only in commit trailers, which this test does not scan)
    (r"\bclaude[\s_-]+(?:haiku|sonnet|opus|fable)\b", re.I),
    (r"\b(?:haiku|sonnet|opus|fable)[\s_-]*[0-9]", re.I),
    (r"\bER-[0-9]+\b|\bPR-[0-9]+\b", re.I),
    # requirement, task, probe, decision, question and risk IDs of documents kept outside the repository
    (r"(?<![A-Za-z0-9_-])[RNTPDQK]-[0-9]+\b", 0),
]]


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _line_hit(line: str) -> bool:
    """True when a line names something from the lists above."""
    if any(rx.search(line) for rx in _FORBIDDEN_RE):
        return True
    for tok in re.findall(r"[A-Za-z0-9_.\-]+", line):
        parts = tok.lower().strip(".-").split(".")
        if any(_h(".".join(parts[i:])) in _HOST_HASHES for i in range(len(parts))):
            return True
    words = re.findall(r"[a-z0-9]+", line.lower())
    for n in range(1, 5):
        if any(_h(" ".join(words[i:i + n])) in _NAME_HASHES for i in range(len(words) - n + 1)):
            return True
    # whole words only: CLUSTER_PM_SCALE is not a role abbreviation
    return any(_h(w) in _UPPER_HASHES for w in re.findall(r"(?<![A-Za-z0-9_])[A-Z]+(?![A-Za-z0-9_])", line))


def _hits_in(paths) -> list[str]:
    hits = []
    for p in paths:
        rel = p.relative_to(REPO).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary
        for n, line in enumerate(text.splitlines(), 1):
            if _line_hit(line):
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    return hits


def test_no_internal_lab_or_process_references_in_tracked_files() -> None:
    hits = _hits_in(_tracked_files())
    assert not hits, "internal references found (rewrite them neutrally):\n" + "\n".join(hits)


def test_hygiene_check_is_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each rule catches a synthetic example (none is a real name); legitimate text passes; no list entry is lost.

    The examples are assembled from pieces so this file's own source does not match the rules it tests.
    """
    tilde, var, ev, rv = "~" + "/", "/var/" + "tmp", "evid" + "ence/", "review-" + "e5.md"
    assert _line_hit(tilde + "work/x") and _line_hit("cd " + tilde + "src/thing") and _line_hit("host.home" + ".arpa")
    assert _line_hit("tmp in " + var + "/x") and _line_hit("/home/" + "someone/x") and _line_hit("PR-" + "3 and ER-" + "2")
    assert _line_hit("see " + ev + "x.md") and _line_hit(rv)
    assert _line_hit("see review-" + "t12.md") and _line_hit("Claude " + "Sonnet wrote it") and not _line_hit("claude code")
    assert _line_hit("Sonnet " + "5.5 did it") and _line_hit("model claude-" + "son" + "net-5-5") and _line_hit("Claude-" + "Hai" + "ku 5.5")
    assert _line_hit("claude_" + "sonnet") and not _line_hit("a sonnet and a haiku")
    assert not _line_hit("HOME=/home/loom") and not _line_hit("pm and plx columns") and not _line_hit("a Docker host")
    assert not _line_hit("CLUSTER_PM_SCALE = 0.3")
    for ident in ("R", "N", "T", "P", "D", "Q", "K"):
        assert _line_hit("see " + ident + "-" + "12 for details"), ident  # outside-doc IDs
    assert not _line_hit("x-1 and t-test, a SHA-1 key, UTF-8 text, (R-G)/B")  # lower-case or part of a word
    # the exact list sizes: dropping an entry fails here
    assert (len(_HOST_HASHES), len(_NAME_HASHES), len(_UPPER_HASHES)) == (18, 85, 2)
    # each hashed path fires on a synthetic entry (monkeypatched sets, so no real name is needed)
    monkeypatch.setattr(sys.modules[__name__], "_HOST_HASHES", {_h("zz-host.example")})
    assert _line_hit("ssh zz-host.example") and _line_hit("ssh a.b.zz-host.example:22")  # token and dot-suffix
    assert not _line_hit("ssh other.example")
    for words in ("qwerty", "qwerty zxcvb", "qwerty zxcvb asdfg", "qwerty zxcvb asdfg hjkl"):
        monkeypatch.setattr(sys.modules[__name__], "_NAME_HASHES", {_h(words)})
        assert _line_hit("said " + words.title() + " today"), words  # 1 to 4 word sequences, case-folded
    assert not _line_hit("qwerty-free text")
    monkeypatch.setattr(sys.modules[__name__], "_UPPER_HASHES", {_h("QQ")})
    assert _line_hit("the QQ said") and not _line_hit("the qq said") and not _line_hit("X_QQ_Y")
