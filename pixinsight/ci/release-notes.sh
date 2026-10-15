#!/bin/sh
# The release notes for version $1: its "## [$1]" section of CHANGELOG.md
# (without the heading), then how to install Loom in PixInsight and in
# Siril, with the version substituted. Fails when the section is
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
cat <<INSTALL
## Installing

### PixInsight

Needs PixInsight 1.9.4 or later, on macOS or Windows. Use one of these, not
several: two copies register the scripts twice.

- **Update repository (recommended).** **Resources → Updates → Manage
  Repositories → Add** \`https://fcarucci.github.io/Loom/\` (with the final
  \`/\`), then **Resources → Updates → Check for Updates**, apply the Loom
  update and restart PixInsight when asked.
- **Zip.** Unzip the attached \`Loom-$v.zip\` into your PixInsight scripts
  folder, so that you have \`<scripts>/Loom\`, then **Script → Feature
  Scripts → Add** and point it at that folder.
- **Git.** Clone the repository, then **Script → Feature Scripts → Add** and
  choose the clone's \`pixinsight\` folder.

An install from the repository or the zip does not update itself: use **Check
for Updates** to get the next release.

### Siril (experimental)

Needs Siril 1.4 with Python scripting. The Siril port is not part of the
attached zip or the update repository: it is installed from a clone. It works
with mono cameras and filters: it needs calibrated, stacked masters per filter
(R, G, B, optionally L, H, S, O). One-shot colour (OSC) cameras are not
supported yet.

1. \`git clone --branch v$v https://github.com/fcarucci/Loom\` (any checkout
   of the repository works). This creates a folder named \`Loom\` in the
   folder where you run it; cloned in your home folder, the script folder is
   \`/Users/<you>/Loom/siril/Loom\` on macOS and \`~/Loom/siril/Loom\` on
   Linux.
2. In Siril, open **Preferences → Scripts**, add the \`siril/Loom\` folder
   inside the clone (the full path to wherever you cloned it), click refresh,
   then **Apply**.
3. \`loom_preprocess.py\` and \`loom_fly_through.py\` appear under **Scripts →
   Loom**. On first run the script installs \`numpy\`, \`scipy\` and
   \`tifffile\` into Siril's Python environment, which needs the network.

Details: [pixinsight/README.md](https://github.com/fcarucci/Loom/blob/v$v/pixinsight/README.md)
and [siril/README.md](https://github.com/fcarucci/Loom/blob/v$v/siril/README.md).
INSTALL
