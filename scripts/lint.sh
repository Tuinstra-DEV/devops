#!/usr/bin/env bash
set -euo pipefail

required_paths=(
  ".github/workflows/reusable-ci.yml"
  ".github/workflows/reusable-gate-baseline.yml"
  ".github/workflows/reusable-browser-quality.yml"
  ".github/workflows/reusable-ci-docker.yml"
  ".github/workflows/reusable-release-image.yml"
  "README.md"
  "docs/testing.md"
  "docs/standards/gate-baseline.md"
  "docs/workflows/contracts/reusable-gate-baseline.md"
  "scripts/gate-baseline-scan.sh"
  "scripts/workflow-contract-test.sh"
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

echo "lint passed"
