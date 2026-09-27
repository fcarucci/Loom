#!/bin/sh
# The release notes for version $1: its "## [$1]" section of CHANGELOG.md
# (without the heading), then how to install. Fails when the section is
# missing or empty, so a release cannot ship without its changelog.
set -eu
v="$1"
notes="$( awk -v v="$v" '
  index( $0, "## [" v "]" ) == 1 { on = 1; next }
  on && /^## \[/ { exit }
  on { print }
' CHANGELOG.md )"
if [ -z "$( printf '%s' "$notes" | tr -d '[:space:]' )" ]; then
  echo "CHANGELOG.md has no section for $v" >&2
  exit 1
fi
printf '%s\n\n' "$notes"
cat <<'INSTALL'
---

Install from the update repository: **Resources → Updates → Manage
Repositories**, add `https://fcarucci.github.io/Loom/` (with the final `/`),
then **Check for Updates**. Or unzip the archive into your PixInsight scripts
folder, so that you have `<scripts>/Loom`, then **Script → Feature Scripts →
Add** and point it at that folder.

A release install does not update itself: use **Check for Updates** in
PixInsight's update repository to get the next release.
INSTALL
