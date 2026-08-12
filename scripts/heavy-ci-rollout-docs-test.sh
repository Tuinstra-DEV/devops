#!/usr/bin/env bash
set -euo pipefail

matrix="docs/workflows/heavy-ci-rollout-matrix.md"
evidence="docs/workflows/heavy-ci-evidence-template.md"
runbook="docs/playbooks/heavy-ci-consumer-cutover.md"

for file in "$matrix" "$evidence" "$runbook"; do
  [[ -f "$file" ]] || { echo "Missing DEV-8 artifact: $file"; exit 1; }
done

require_text() {
  local file="$1"
  local needle="$2"
  grep -Fqi "$needle" "$file" || {
    echo "$file must document: $needle"
    exit 1
  }
}

for repo in console notify tuinstra-site marcel-site wodiq-site devops; do
  require_text "$matrix" "\`$repo\`"
done

for field in "Expected benefit" "Owner" "Dependencies" "Blockers" "Required-check review" "Fallback path"; do
  require_text "$matrix" "$field"
done

for class in Heavy Medium Light; do
  require_text "$matrix" "**$class**"
done

for metric in "before" "candidate" "Cache state" "Queue s" "estimated_cost_delta" "failure_rate" "flake_rate" "Trust and isolation" "Rollback readiness"; do
  require_text "$evidence" "$metric"
done

require_text "$matrix" "At least five comparable successful first attempts"
require_text "$matrix" "DEV-3"
require_text "$matrix" "DEV-5"
require_text "$matrix" "heavy-ci-consumer-cutover.md"
require_text "$matrix" "heavy-ci-evidence-template.md"
require_text "$runbook" "Hosted rollback procedure"
require_text "$runbook" "Never force-push"

echo "heavy CI rollout documentation test passed"
