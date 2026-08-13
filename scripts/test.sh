#!/usr/bin/env bash
set -euo pipefail

required_dirs=(
  ".github/workflows"
  "scripts"
  "templates"
  "docs"
)

for dir in "${required_dirs[@]}"; do
  if [[ ! -d "$dir" ]]; then
    echo "Missing required directory: $dir"
    exit 1
  fi
done

./scripts/test-runner-platform.sh

if ! grep -q "Auth0" "README.md"; then
  echo "README must document Auth0 direction"
  exit 1
fi

./scripts/workflow-contract-test.sh
ruby ./scripts/heavy-ci-v2-contract-test.rb
./scripts/heavy-ci-rollout-docs-test.sh
./scripts/heavy-ci-baseline-test.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_classify_ci_changes.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts \
  python3 -m unittest discover -s tests -p 'test_ci_billing_report.py'
ruby ./scripts/dependency-update-policy-test.rb

if [[ -n "${DEPENDABOT_FLEET_ROOT:-}" ]]; then
  ruby ./scripts/dependency-update-fleet-test.rb \
    "console=${DEPENDABOT_FLEET_ROOT}/console/.github/dependabot.yml" \
    "devops=${DEPENDABOT_FLEET_ROOT}/devops/.github/dependabot.yml" \
    "gate=${DEPENDABOT_FLEET_ROOT}/gate/.github/dependabot.yml" \
    "marcel-site=${DEPENDABOT_FLEET_ROOT}/marcel-site/.github/dependabot.yml" \
    "notify=${DEPENDABOT_FLEET_ROOT}/notify/.github/dependabot.yml" \
    "openairco-site=${DEPENDABOT_FLEET_ROOT}/openairco-site/.github/dependabot.yml" \
    "sudoku-spark-web=${DEPENDABOT_FLEET_ROOT}/sudoku-spark-web/.github/dependabot.yml" \
    "tracker=${DEPENDABOT_FLEET_ROOT}/tracker/.github/dependabot.yml" \
    "tuinstra-site=${DEPENDABOT_FLEET_ROOT}/tuinstra-site/.github/dependabot.yml" \
    "wodiq=${DEPENDABOT_FLEET_ROOT}/wodiq/.github/dependabot.yml" \
    "wodiq-site=${DEPENDABOT_FLEET_ROOT}/wodiq-site/.github/dependabot.yml"
else
  echo "DEPENDABOT_FLEET_ROOT not set; cross-repository policy check skipped"
fi

echo "test passed"
