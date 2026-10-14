#!/usr/bin/env bash
# One command for the tests.
#   scripts/test.sh [pytest args]          venv run (pure Python), PYTHONPATH = repo root
#   scripts/test.sh --docker [pytest args] full suite in the Docker image (docker/run-tests.sh)
# Venv: $LOOMSIRIL_VENV, else .venv in the repo root (gitignored); created with python3 (3.10+) and
# numpy/tifffile/pytest if missing. The package is NOT pip-installed into it: PYTHONPATH
# points at this checkout.
# On a small machine, pass explicit test files to run a subset.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "--docker" ]]; then
  shift
  [[ -x docker/run-tests.sh ]] || { echo "test.sh: docker/run-tests.sh not present" >&2; exit 2; }
  exec docker/run-tests.sh "$@"
fi

VENV="${LOOMSIRIL_VENV:-$ROOT/.venv}"
if [[ ! -x "$VENV/bin/python" ]]; then
  PY="${PYTHON:-python3}"
  "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "test.sh: need Python 3.10+ ($PY)" >&2; exit 2; }
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q numpy tifffile pytest
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m pytest -q "$@"
