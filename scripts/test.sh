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

if ! grep -q "Auth0" "README.md"; then
  echo "README must document Auth0 direction"
  exit 1
fi

./scripts/workflow-contract-test.sh

echo "test passed"
