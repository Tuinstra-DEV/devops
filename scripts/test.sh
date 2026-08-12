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
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts \
  python3 -m unittest discover -s tests -p 'test_ci_billing_report.py'

echo "test passed"
