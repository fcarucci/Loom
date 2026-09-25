#!/usr/bin/env bash
#
# Builds a PixInsight UPDATE REPOSITORY: an updates.xri manifest and the
# package it describes.
#
# This is a SECOND artefact, not a replacement for ci/package.sh. The two
# differ in layout because they are installed by different machinery:
#
#   package.sh     Loom/script/...        extracted by hand into
#                                         ~/PixInsight/scripts, then added
#                                         through Feature Scripts -> Add
#
#   repository.sh  src/scripts/Loom/...   deployed by PixInsight's updater
#                                         into the INSTALLATION directory,
#                                         where scripts are found without
#                                         being registered by hand
#
# A user who has both ends up with Loom twice in the Script menu, from one
# feature-id. That is worth knowing before publishing this anywhere.
#
# Usage: ci/repository.sh [version]

set -euo pipefail

root="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$root"

version="${1:-}"
if [ -z "$version" ]; then
   version="$( sed -n 's/^Util\.LOOM_VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
               script/lib/Util.js )"
fi
version="${version#v}"
if [ -z "$version" ]; then
   echo "repository: could not read Util.LOOM_VERSION" >&2
   exit 1
fi

# Release dates are UTC, per the repository reference. Using local time
# would put a package in tomorrow for anyone east of here.
today="$( date -u +%Y%m%d )"

# Read from Util.MIN_CORE rather than written here: Loom accepts that
# version OR LATER (coreVersionAtLeast), so a range pinned to one release
# would silently stop offering updates the day the next core ships. The
# upper bound is deliberately generous for the same reason.
min_core="$( sed -n 's/^Util\.MIN_CORE = { major:[[:space:]]*\([0-9]*\), minor:[[:space:]]*\([0-9]*\), release:[[:space:]]*\([0-9]*\).*/\1.\2.\3/p' \
             script/lib/Util.js )"
if [ -z "$min_core" ]; then
   echo "repository: could not read Util.MIN_CORE" >&2
   exit 1
fi
core_range="$min_core:1.9.99"

out="$root/dist/repo"
stage="$out/stage"
archive="$today-loom-$version.zip"

# The whole directory, not just this archive: a leftover package from an
# earlier build would be published alongside the manifest that does not
# mention it, and the next build would ship two.
rm -rf "$out"
mkdir -p "$stage/src/scripts/Loom"

# Everything a user needs to RUN Loom. selftest.js is deliberately left
# out: it carries its own #feature-id, so shipping it would put
# "LoomSelfTest" in the Script menu of everyone who subscribes.
cp -R script/lib "$stage/src/scripts/Loom/lib"
cp script/Loom.js script/FrameSelector.js script/FlyThrough.js "$stage/src/scripts/Loom/"
cp README.md LICENSE "$stage/src/scripts/Loom/"
printf '%s\n' "$version" > "$stage/src/scripts/Loom/RELEASE"

# As package.sh: dot-directories are development apparatus, and macOS
# sidecars would install as ._Loom.js and be read by nothing.
find "$stage" -type d -name '.*' -prune -exec rm -rf {} +
find "$stage" \( -name '._*' -o -name '.DS_Store' \) -delete

( cd "$stage" && zip -q -r -X "$out/$archive" src \
     -x '*.DS_Store' -x '*/._*' )
rm -rf "$stage"

# Lowercase hex, which is what the reference requires and what the core
# compares against.
sha1="$( shasum -a 1 "$out/$archive" | cut -d' ' -f1 | tr 'A-F' 'a-f' )"

# NO XML NAMESPACE, deliberately.
#
# PixInsight's own etc/update/installed.xri carries
# xmlns="http://www.pixinsight.com/xri", and copying that here was a
# mistake that cost a release: installed.xri is a file the application
# WRITES, not one it reads from a repository. With the namespace present
# the parser finds <description> but not <platform> or <package>, so the
# repository loads, shows its description, and offers "Available packages:
# 0". Every working third-party manifest in the wild -- cosmicphotons,
# ideviceapps -- opens with a bare <xri version="1.0">.
#
# `remove` is not optional either: without it a library deleted in a later
# version stays on disk, and since the scripts resolve #include relative to
# themselves, the stale copy would still be found.
cat > "$out/updates.xri" <<XRIEOF
<?xml version="1.0" encoding="UTF-8"?>
<xri version="1.0">
   <description>
      <p>
         Loom — the mechanical first steps, from integrated masters to
         plates ready for creative work.
      </p>
      <p>
         Copyright © 2026 Francesco Carucci. Released under the terms in
         the LICENSE file accompanying the package.
      </p>
   </description>

   <platform os="all" arch="noarch" version="$core_range">
      <package fileName="$archive"
               sha1="$sha1"
               type="script"
               releaseDate="$today"
               remove="src/scripts/Loom"
               title="Loom $version">
         <description>
            <p>
               Solve, flux-calibrate, remove gradients, correct aberration,
               register, crop, combine and colour-calibrate RGB and narrowband
               palettes. Includes the Frame Selector, which measures a folder
               of subframes or imports a night from an ASIAIR card.
            </p>
         </description>
      </package>
   </platform>
</xri>
XRIEOF

# SIGNING, when there is a certificate to sign with.
#
# The <Signature> element is generated by PixInsight's CodeSign script or by
# the core's --sign-xml-file argument, and requires Certified PixInsight
# Developer status. It must be produced AFTER the manifest is final: any
# later edit, however small, invalidates it.
#
# Until then the repository is unsigned. That works -- PixInsight warns once
# and the user confirms, and "Allow unsigned update repositories" is enabled
# by default -- but it is a warning every subscriber sees.
#
#   PixInsight --sign-xml-file="$out/updates.xri" --developer-id="..."

# Parsed before it is published. XML predefines only five entities, and
# &mdash; is not one of them -- a manifest written with it is rejected by
# every reader, including the core's.
python3 - "$out/updates.xri" <<'CHECK'
import sys, xml.dom.minidom
xml.dom.minidom.parse( sys.argv[1] )
CHECK

# The licence, served at the repository URL as well as inside the package,
# so it can be read before anything is installed.
cp LICENSE "$out/LICENSE"

echo "$out/updates.xri"
echo "$out/$archive"
echo "$out/LICENSE"
