#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  bootstrap) mkdir -p .cache/heavy-ci ;;
  build) mkdir -p .heavy-ci/payload && printf 'tracker-fixture\n' > .heavy-ci/payload/marker ;;
  unit|integration|e2e-prepare|browser) grep -Fqx 'tracker-fixture' .heavy-ci/payload/marker ;;
  *) echo "unsupported Tracker fixture stage: ${1:-missing}" >&2; exit 2 ;;
esac
