#!/usr/bin/env bash
# Prepare a test container for the network-marked tests (online plate solving and SPCC). Run inside the container,
# only for that run, with LOOMSIRIL_NETWORK=1:
#   docker run --rm -e LOOMSIRIL_NETWORK=1 <image> bash docker/network-setup.sh python -m pytest -q -rs -m network
# It fetches Siril's own published SPCC database (sensor and filter curves) into the place siril-cli reads it from
# ($XDG_DATA_HOME or ~/.local/share)/siril-spcc-database, as a git checkout whose origin is the official repository
# (Siril only uses the folder in that form). Plate solving needs nothing here: Siril queries its online catalogues
# itself (Siril warns that the local gaia_photometric.dat is missing and then uses the online Gaia service; that
# warning is expected). The database is pinned to one commit (SPCC_DATABASE_COMMIT overrides it) so a run is
# repeatable. The default offline image never contains the database. Remaining arguments are run as the test command.
set -euo pipefail
if [[ "${LOOMSIRIL_NETWORK:-}" != "1" ]]; then
  echo "network-setup.sh: LOOMSIRIL_NETWORK is not 1; refusing to fetch anything" >&2
  exit 2
fi
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/siril-spcc-database"
URL="https://gitlab.com/free-astro/siril-spcc-database"
COMMIT="${SPCC_DATABASE_COMMIT:-87af70a92ec1d0ff780bd3ea833aaa7b3b169cc2}"
if [[ ! -d "$DEST/.git" ]]; then
  mkdir -p "$DEST"
  git -C "$DEST" init --quiet
  git -C "$DEST" remote add origin "$URL"
fi
git -C "$DEST" remote set-url origin "$URL"
git -C "$DEST" fetch --quiet --depth 1 origin "$COMMIT"
git -C "$DEST" checkout --quiet --force FETCH_HEAD
echo "network-setup.sh: SPCC database $(git -C "$DEST" rev-parse HEAD) at $DEST"
if [[ $# -gt 0 ]]; then
  exec "$@"
fi
