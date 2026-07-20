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
done < <(rg --no-heading --line-number '^\s*uses:\s+[^.]' .github/workflows/reusable-*.yml)

if rg --quiet --hidden '^\s*secrets:\s*inherit\s*$' .github/workflows; then
  fail "secrets: inherit is prohibited"
fi

contract_files=(
  ".github/workflows/reusable-browser-quality.yml"
  ".github/workflows/reusable-ci-docker.yml"
  ".github/workflows/reusable-release-image.yml"
)

for file in "${contract_files[@]}"; do
  rg --quiet 'execution-class:' "$file" || fail "$file does not declare execution-class"
  rg --quiet 'default: hosted' "$file" || fail "$file does not default to hosted"
  rg --quiet 'hosted\|trusted-heavy' "$file" || fail "$file does not reject arbitrary execution classes"
  rg --quiet 'fromJSON\('\''\["self-hosted","trusted-heavy"\]\'\''\)' "$file" || fail "$file does not use the constrained trusted runner labels"
  rg --quiet "\|\| 'ubuntu-24.04'" "$file" || fail "$file does not use the safe hosted fallback"
  rg --quiet "github.event_name != 'pull_request_target'" "$file" || fail "$file does not keep pull_request_target off trusted runners"
  rg --quiet '!github.event.pull_request.head.repo.fork' "$file" || fail "$file does not keep forks off trusted runners"
  rg --quiet "github.actor != 'dependabot\[bot\]'" "$file" || fail "$file does not keep Dependabot off trusted runners"
done

docker_workflow=".github/workflows/reusable-ci-docker.yml"
for legacy_input in workdir dockerfile build-args trivy-severity trivy-exit-code upload-sarif; do
  rg --quiet "^      ${legacy_input}:" "$docker_workflow" || fail "Docker compatibility input missing: $legacy_input"
done
for docker_output in image-digest scan-artifact sarif-artifact; do
  rg --quiet "^      ${docker_output}:" "$docker_workflow" || fail "Docker output missing: $docker_output"
done
rg --quiet 'security-events: write' "$docker_workflow" || fail "optional SARIF upload permission missing"

browser_workflow=".github/workflows/reusable-browser-quality.yml"
rg --quiet 'report-artifact:' "$browser_workflow" || fail "browser report output missing"
rg --quiet 'if-no-files-found: error' "$browser_workflow" || fail "browser artifact is not enforced"

release_workflow=".github/workflows/reusable-release-image.yml"
for release_output in image-ref image-digest provenance-artifact provenance-url; do
  rg --quiet "^      ${release_output}:" "$release_workflow" || fail "release output missing: $release_output"
done
for permission in 'attestations: write' 'contents: read' 'id-token: write' 'packages: write'; do
  rg --quiet "$permission" "$release_workflow" || fail "release permission missing: $permission"
done
rg --quiet 'subject-digest:.*steps.build.outputs.digest' "$release_workflow" || fail "provenance is not bound to the immutable digest"
rg --quiet 'image-ref=.*@.*IMAGE_DIGEST' "$release_workflow" || fail "immutable image-ref output missing"

echo "workflow contract test passed"
