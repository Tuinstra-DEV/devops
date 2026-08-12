#!/usr/bin/env bash
set -euo pipefail

required_paths=(
  ".github/workflows/reusable-ci.yml"
  ".github/workflows/reusable-gate-baseline.yml"
  ".github/workflows/reusable-browser-quality.yml"
  ".github/workflows/reusable-ci-docker.yml"
  ".github/workflows/reusable-release-image.yml"
  ".github/workflows/reusable-heavy-ci-v2.yml"
  "README.md"
  "docs/testing.md"
  "docs/standards/gate-baseline.md"
  "docs/workflows/contracts/reusable-gate-baseline.md"
  "scripts/gate-baseline-scan.sh"
  "scripts/workflow-contract-test.sh"
  "scripts/heavy-ci-v2-contract-test.rb"
  "scripts/heavy-ci-rollout-docs-test.sh"
  "scripts/heavy-ci-baseline-test.sh"
  "scripts/dependency-update-policy-test.rb"
  "scripts/dependency-update-fleet-test.rb"
  ".github/dependabot.yml"
  "docs/standards/dependency-update-policy.md"
  "docs/workflows/dependency-rollout-matrix.md"
  "docs/workflows/dependency-rollout-evidence-template.md"
  "docs/workflows/dependency-rollout-baseline.md"
  "docs/evidence/DEV-13-ci-billing-baseline-2026-08-12.md"
  "docs/evidence/DEV-13-dependabot-actions-baseline-2026-08-11.json"
  "docs/evidence/DEV-13-security-settings-preflight-2026-08-12.json"
  "templates/workflows/caller-gate-baseline.yml"
  "templates/docker/nuxt-ssg-nginx.Dockerfile"
)

for path in "${required_paths[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path"
    exit 1
  fi
done

ruby -e '
  require "yaml"
  Dir[".github/workflows/*.{yml,yaml}"].sort.each { |file| YAML.safe_load(File.read(file), aliases: true) }
'

ruby -c scripts/heavy-ci-v2-contract-test.rb
ruby -c scripts/dependency-update-policy-test.rb
ruby -c scripts/dependency-update-fleet-test.rb
ruby -c scripts/collect-dependabot-actions-baseline.rb
bash -n scripts/heavy-ci-rollout-docs-test.sh
bash -n scripts/heavy-ci-baseline-test.sh
ruby scripts/dependency-update-policy-test.rb

echo "lint passed"
