#!/usr/bin/env bash
#
# Cuts a signed release on the maintainer's Mac: version bump, tests, signing,
# one release commit, a signed annotated tag. Nothing is pushed unless --push is given.
#
# Usage: ci/release.sh <version> ( --key-dir <folder> | --keys <file.xssk> --password-file <file> ) [--slot N] [--push]
#
# --key-dir is a folder with one <name>.xssk and its password in <name>.password
# (or password, password.txt); see ci/sign.sh.
#
# Before running: the work for the release is merged on main, and the
# CHANGELOG's [Unreleased] section describes it. This script then:
#   1. checks it is on a clean main and the tag does not exist
#   2. turns [Unreleased] into the dated <version> section and sets Util.LOOM_VERSION;
#      the same for the Siril port (siril/CHANGELOG.md, loomsiril.__version__)
#   3. runs the suite as 1.9.5 and as 1.9.4
#   4. signs the entry scripts (ci/sign.sh) -- after step 2, because the version
#      lives in a file the signature covers
#   5. builds the update repository (ci/repository.sh), signs its updates.xri and
#      puts the result in repo/ -- after step 4, because the package holds the
#      signed scripts and the manifest holds the package's checksum
#   6. commits "Loom <version>" with the signatures, the signed-tree hash, repo/ and
#      the Siril changelog and version,
#      and tags v<version> (SSH-signed, verified before any push)
#   7. with --push, pushes main and the tag to origin and github; CI then checks
#      the signatures, builds the release zip and PUBLISHES repo/ as the update
#      repository -- it does not build or sign the repository itself

set -euo pipefail

root="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$root"

version="${1:-}"; [ $# -gt 0 ] && shift
sign_args=() ; push=0
while [ $# -gt 0 ]; do
   case "$1" in
      --key-dir|--keys|--password-file|--slot)
                        sign_args+=( "$1" "${2:-}" ); shift 2 ;;
      --push)           push=1; shift ;;
      *) echo "release: unknown argument: $1" >&2; exit 2 ;;
   esac
done
case "$version" in [0-9]*.[0-9]*.[0-9]*) ;; *) echo "usage: ci/release.sh <version> --key-dir <folder> [--push]" >&2; exit 2 ;; esac
[ ${#sign_args[@]} -gt 0 ] || { echo "release: give --key-dir <folder>, or --keys and --password-file" >&2; exit 2; }
# Fail on a wrong folder now, before anything is changed or tested.
ci/sign.sh "${sign_args[@]}" --dry-run >/dev/null

[ "$( git rev-parse --abbrev-ref HEAD )" = main ] || { echo "release: not on main" >&2; exit 1; }
[ -z "$( git status --porcelain )" ]               || { echo "release: the working tree is not clean" >&2; exit 1; }
git rev-parse -q --verify "refs/tags/v$version" >/dev/null && { echo "release: tag v$version already exists" >&2; exit 1; }
grep -q '^## \[Unreleased\]' CHANGELOG.md          || { echo "release: CHANGELOG.md has no [Unreleased] section" >&2; exit 1; }

# One local date for both changelogs, and the Siril files checked before anything is edited.
rdate="$( date +%Y-%m-%d )"
node ci/release-prep.js --check --date "$rdate" "$version"

python3 - "$version" "$rdate" <<'PY'
import re, sys
v = sys.argv[1]
d = sys.argv[2]
s = open("CHANGELOG.md").read()
head = "## [Unreleased]"
i = s.index(head) + len(head)
j = s.find("\n## [", i)
body = s[i:j if j >= 0 else len(s)]
if not body.strip():
    sys.exit("release: the [Unreleased] section is empty")
s = s[:i] + "\n\n## [%s] — %s" % (v, d) + s[i:]
open("CHANGELOG.md", "w").write(s)
u = open("script/lib/Util.js").read()
u2 = re.sub(r'^Util\.LOOM_VERSION = "[^"]*";', 'Util.LOOM_VERSION = "%s";' % v, u, count=1, flags=re.M)
if u2 == u:
    sys.exit("release: could not set Util.LOOM_VERSION")
open("script/lib/Util.js", "w").write(u2)
PY

# The Siril port is released with it: its changelog section and package version.
node ci/release-prep.js --date "$rdate" "$version"

node ci/run-tests.js | tail -1
LOOM_TEST_CORE_RELEASE=4 node ci/run-tests.js | tail -1

ci/sign.sh "${sign_args[@]}"
node ci/check-signatures.js

ci/repository.sh "$version" >/dev/null
ci/sign.sh "${sign_args[@]}" --xml dist/repo/updates.xri
rm -rf repo
mkdir repo
cp dist/repo/updates.xri dist/repo/LICENSE dist/repo/*.zip repo/
node ci/check-signatures.js --repo

git add CHANGELOG.md script ci/signed-tree.sha256 repo ../siril/CHANGELOG.md ../siril/loomsiril/__init__.py
git commit -q -m "Loom $version"
git tag -s "v$version" -m "Loom $version"
git tag -v "v$version" >/dev/null 2>&1 || {
   echo "release: the tag is not signed or does not verify: configure SSH signing (see docs/RELEASING.md)" >&2
   exit 1
}
echo "release: committed and tagged v$version"

if [ "$push" = 1 ]; then
   git push origin main "v$version"
   git push github main "v$version"
else
   echo "release: not pushed. To publish:  git push origin main v$version && git push github main v$version"
fi
