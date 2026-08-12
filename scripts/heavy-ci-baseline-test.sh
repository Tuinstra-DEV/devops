#!/usr/bin/env bash
set -euo pipefail

report="docs/workflows/heavy-ci-baseline.md"
[[ -f "$report" ]] || { echo "Missing DEV-9 baseline report"; exit 1; }

require_text() {
  local needle="$1"
  grep -Fqi "$needle" "$report" || {
    echo "DEV-9 baseline must document: $needle"
    exit 1
  }
}

for stage in checkout/setup dependency-restore/install build e2e-preparation browser/test-execution live-smoke artifacts release/deploy-gates; do
  require_text "\`$stage\`"
done

for repo in WODIQ tracker gate console notify tuinstra-site marcel-site wodiq-site devops; do
  require_text "\`$repo\`"
done

for section in "Corrected baseline summary" "Top three time-cost drivers" "Cache findings versus opportunities" "DEV-7 pilot evidence and revised targets" "Pilot decisions, order, and capacity envelope"; do
  require_text "## $section"
done

require_text "five comparable successes"
require_text "three comparable misses"
require_text "20% median improvement"
require_text "Sanctuary admits at most two isolated jobs"

echo "heavy CI baseline documentation test passed"
