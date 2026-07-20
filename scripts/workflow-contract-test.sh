#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "workflow contract test failed: $*" >&2
  exit 1
}

required_paths=(
  ".github/workflows/reusable-browser-quality.yml"
  ".github/workflows/reusable-ci-docker.yml"
  ".github/workflows/reusable-release-image.yml"
  "docs/workflows/contracts/reusable-browser-quality.md"
  "docs/workflows/contracts/reusable-ci-docker.md"
  "docs/workflows/contracts/reusable-release-image.md"
)

for path in "${required_paths[@]}"; do
  [[ -f "$path" ]] || fail "missing $path"
done

ruby -e '
  require "yaml"
  Dir[".github/workflows/*.{yml,yaml}"].sort.each do |file|
    YAML.safe_load(File.read(file), aliases: true)
  rescue Psych::SyntaxError => error
    warn "#{file}: #{error.message}"
    exit 1
  end
' || fail "workflow YAML is invalid"

while IFS= read -r use_line; do
  reference="${use_line#*uses:}"
  reference="${reference%%#*}"
  reference="${reference//[[:space:]]/}"
  [[ "$reference" =~ @([0-9a-f]{40})$ ]] || fail "external action is not full-SHA pinned: $use_line"
done < <(grep -nHE '^[[:space:]]*uses:[[:space:]]+[^.]' .github/workflows/reusable-*.yml)

if grep -ERq --include='*.yml' --include='*.yaml' '^[[:space:]]*secrets:[[:space:]]*inherit[[:space:]]*$' .github/workflows; then
  fail "secrets: inherit is prohibited"
fi

contract_files=(
  ".github/workflows/reusable-browser-quality.yml"
  ".github/workflows/reusable-ci-docker.yml"
  ".github/workflows/reusable-release-image.yml"
)

for file in "${contract_files[@]}"; do
  grep -Fq 'execution-class:' "$file" || fail "$file does not declare execution-class"
  grep -Fq 'default: hosted' "$file" || fail "$file does not default to hosted"
  grep -Fq 'hosted|trusted-heavy' "$file" || fail "$file does not reject arbitrary execution classes"
  grep -Fq "fromJSON('[\"self-hosted\",\"trusted-heavy\"]')" "$file" || fail "$file does not use the constrained trusted runner labels"
  grep -Fq "|| 'ubuntu-24.04'" "$file" || fail "$file does not use the safe hosted fallback"
  grep -Fq "github.event_name != 'pull_request_target'" "$file" || fail "$file does not keep pull_request_target off trusted runners"
  grep -Fq '!github.event.pull_request.head.repo.fork' "$file" || fail "$file does not keep forks off trusted runners"
  grep -Fq "github.actor != 'dependabot[bot]'" "$file" || fail "$file does not keep Dependabot off trusted runners"
done

docker_workflow=".github/workflows/reusable-ci-docker.yml"
for legacy_input in workdir dockerfile build-args trivy-severity trivy-exit-code upload-sarif; do
  grep -Eq "^      ${legacy_input}:" "$docker_workflow" || fail "Docker compatibility input missing: $legacy_input"
done
for docker_output in image-digest scan-artifact sarif-artifact; do
  grep -Eq "^      ${docker_output}:" "$docker_workflow" || fail "Docker output missing: $docker_output"
done
grep -Fq 'security-events: write' "$docker_workflow" || fail "optional SARIF upload permission missing"

browser_workflow=".github/workflows/reusable-browser-quality.yml"
grep -Fq 'report-artifact:' "$browser_workflow" || fail "browser report output missing"
grep -Fq 'if-no-files-found: error' "$browser_workflow" || fail "browser artifact is not enforced"

release_workflow=".github/workflows/reusable-release-image.yml"
for release_output in image-ref image-digest provenance-artifact provenance-url; do
  grep -Eq "^      ${release_output}:" "$release_workflow" || fail "release output missing: $release_output"
done
for permission in 'attestations: write' 'contents: read' 'id-token: write' 'packages: write'; do
  grep -Fq "$permission" "$release_workflow" || fail "release permission missing: $permission"
done
grep -Eq 'subject-digest:.*steps.build.outputs.digest' "$release_workflow" || fail "provenance is not bound to the immutable digest"
grep -Eq 'image-ref=.*@.*IMAGE_DIGEST' "$release_workflow" || fail "immutable image-ref output missing"

echo "workflow contract test passed"
