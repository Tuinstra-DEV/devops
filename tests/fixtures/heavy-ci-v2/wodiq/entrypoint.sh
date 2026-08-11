#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  bootstrap) mkdir -p .cache/heavy-ci ;;
  build) mkdir -p .heavy-ci/payload && printf 'wodiq-fixture\n' > .heavy-ci/payload/marker ;;
  unit|e2e-prepare|browser) grep -Fqx 'wodiq-fixture' .heavy-ci/payload/marker ;;
  *) echo "unsupported WODIQ fixture stage: ${1:-missing}" >&2; exit 2 ;;
esac
