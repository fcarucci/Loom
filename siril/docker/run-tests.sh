#!/usr/bin/env bash
# Build the test image (docker/Dockerfile) and run pytest in it: the full suite, ffmpeg and Siril included.
#   docker/run-tests.sh [pytest args]     default args: -q -rs
#   scripts/test.sh --docker [pytest args] delegates here.
# Build args come from the environment when set: STARNET_URL and STARNET_SHA256 (StarNet2 CLI, fetched at
# build time), SIRIL_APPIMAGE_URL and SIRIL_APPIMAGE_SHA256 (override the pinned Siril AppImage).
# DOCKER_HOST is honoured by the docker CLI, so `DOCKER_HOST=ssh://<host> docker/run-tests.sh` runs the
# build and the tests on another machine.
# LOOMSIRIL_DOCKER_ARGS: extra arguments for both `docker build` and `docker run` (word-split, e.g. a label
# for your own bookkeeping: LOOMSIRIL_DOCKER_ARGS="--label owner=ci"). Empty by default.
# Image tag: $LOOMSIRIL_IMAGE (default loomsiril-test:local). Containers are started with --rm.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="${LOOMSIRIL_IMAGE:-loomsiril-test:local}"
# shellcheck disable=SC2206  # word splitting is wanted here
EXTRA=(${LOOMSIRIL_DOCKER_ARGS:-})

BUILD_ARGS=()
for var in STARNET_URL STARNET_SHA256 SIRIL_APPIMAGE_URL SIRIL_APPIMAGE_SHA256; do
  if [[ -n "${!var:-}" ]]; then
    BUILD_ARGS+=(--build-arg "$var=${!var}")
  fi
done

docker build ${EXTRA[@]+"${EXTRA[@]}"} ${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"} -f docker/Dockerfile -t "$IMAGE" .

if [[ $# -eq 0 ]]; then
  set -- -q -rs
fi
exec docker run --rm ${EXTRA[@]+"${EXTRA[@]}"} "$IMAGE" python -m pytest "$@"
