#!/usr/bin/env bash
#
# Builds the installable zip.
#
# The archive contains ONE top-level folder, `Loom/`, so that extracting it
# into ~/PixInsight/scripts produces ~/PixInsight/scripts/Loom -- the layout
# PixInsight's Feature Scripts dialog expects to be pointed at. An archive
# of loose files would scatter script/ and lib/ into whatever folder the
# user happened to extract it in.
#
# Runs the same way on a Mac and on a CI runner; the workflow is a thin
# caller so that what ships can be built and inspected locally.
#
# Usage: ci/package.sh [version]
#   version  overrides the version read from Util.LOOM_VERSION, so a tagged
#            build can name the file after its tag.

set -euo pipefail

root="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$root"

# The single source of truth for the release number, read rather than
# duplicated -- a version in two places is a version that disagrees.
version="${1:-}"
if [ -z "$version" ]; then
   version="$( sed -n 's/^Util\.LOOM_VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
               script/lib/Util.js )"
fi
if [ -z "$version" ]; then
   echo "package: could not read Util.LOOM_VERSION from script/lib/Util.js" >&2
   exit 1
fi
version="${version#v}"

out="$root/dist"
stage="$out/stage"
name="Loom-$version"
zip_path="$out/$name.zip"

rm -rf "$stage" "$zip_path"
mkdir -p "$stage/Loom"

# What a user needs to RUN Loom, and nothing else. The design specs and
# the agent scratch under .claude are development history, not payload.
cp -R script "$stage/Loom/script"
cp README.md LICENSE "$stage/Loom/"

# The marker that tells the updater this installation came from a release
# and may therefore be replaced wholesale. Update.installKind refuses to
# touch a directory carrying neither this nor a .git, precisely so that a
# stray folder is never swapped out from under someone.
printf '%s\n' "$version" > "$stage/Loom/RELEASE"

# Hidden directories are development apparatus, never payload: agent
# worktrees under .claude, editor state, a stray .git. Dropped as a CLASS
# rather than by name, so the next tool to leave a dot-folder in the tree
# does not quietly ship inside the release. A .claude folder did exactly
# that on the first build of this script.
find "$stage" -type d -name '.*' -prune -exec rm -rf {} +

# macOS writes AppleDouble sidecars and .DS_Store into archives given half
# a chance; they would land in the user's install as ._Loom.js and be read
# by nothing.
find "$stage" \( -name '._*' -o -name '.DS_Store' \) -delete

( cd "$stage" && zip -q -r -X "$zip_path" Loom \
     -x '*.DS_Store' -x '*/._*' )

rm -rf "$stage"

echo "$zip_path"
