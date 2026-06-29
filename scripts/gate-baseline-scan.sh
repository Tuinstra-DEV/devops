#!/usr/bin/env bash
set -euo pipefail

repo="."
output_dir="gate-baseline-evidence"
fail_on_missing="${GATE_BASELINE_FAIL_ON_MISSING:-false}"
require_deploy="${GATE_BASELINE_REQUIRE_DEPLOY:-true}"
require_renovate="${GATE_BASELINE_REQUIRE_RENOVATE:-true}"
gate_contract_path="${GATE_BASELINE_CONTRACT_PATH:-.gate/baseline.yml}"

usage() {
  cat <<'USAGE'
Usage: gate-baseline-scan.sh [options]

Options:
  --repo PATH                  Repository to scan (default: .)
  --output-dir PATH            Evidence output directory (default: gate-baseline-evidence)
  --fail-on-missing true|false Exit non-zero when required checks fail (default: false)
  --require-deploy true|false  Require reusable CD workflow callers (default: true)
  --require-renovate true|false Require Renovate preset configuration (default: true)
  --gate-contract-path PATH    Gate contract path relative to repo (default: .gate/baseline.yml)
  -h, --help                   Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo="$2"
      shift 2
      ;;
    --output-dir)
      output_dir="$2"
      shift 2
      ;;
    --fail-on-missing)
      fail_on_missing="$2"
      shift 2
      ;;
    --require-deploy)
      require_deploy="$2"
      shift 2
      ;;
    --require-renovate)
      require_renovate="$2"
      shift 2
      ;;
    --gate-contract-path)
      gate_contract_path="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$repo" ]]; then
  echo "Repository path does not exist: $repo" >&2
  exit 2
fi

mkdir -p "$output_dir"
summary_file="$output_dir/gate-baseline-summary.md"
machine_file="$output_dir/gate-baseline-summary.tsv"

failures=0
warnings=0
checks=0

repo_rel() {
  local path="$1"
  path="${path#"$repo"/}"
  printf '%s' "$path"
}

bool_is_true() {
  [[ "$1" == "true" || "$1" == "1" || "$1" == "yes" ]]
}

workflow_files() {
  if [[ -d "$repo/.github/workflows" ]]; then
    find "$repo/.github/workflows" -type f \( -name '*.yml' -o -name '*.yaml' \) | sort
  fi
}

workflow_matches() {
  local pattern="$1"
  local matches=""
  local file

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    if grep -Eq "$pattern" "$file"; then
      matches="${matches}$(repo_rel "$file") "
    fi
  done < <(workflow_files)

  printf '%s' "$matches"
}

record() {
  local status="$1"
  local name="$2"
  local details="$3"

  checks=$((checks + 1))
  case "$status" in
    FAIL) failures=$((failures + 1)) ;;
    WARN) warnings=$((warnings + 1)) ;;
  esac

  printf '| %s | %s | %s |\n' "$status" "$name" "$details" >> "$summary_file"
  printf '%s\t%s\t%s\n' "$status" "$name" "$details" >> "$machine_file"
}

require_file() {
  local path="$1"
  local name="$2"

  if [[ -f "$repo/$path" ]]; then
    record "PASS" "$name" "$path exists"
  else
    record "FAIL" "$name" "$path is missing"
  fi
}

require_workflow_pattern() {
  local name="$1"
  local pattern="$2"
  local matches

  matches="$(workflow_matches "$pattern")"
  if [[ -n "$matches" ]]; then
    record "PASS" "$name" "$matches"
  else
    record "FAIL" "$name" "No workflow matched pattern: $pattern"
  fi
}

warn_workflow_pattern() {
  local name="$1"
  local pattern="$2"
  local matches

  matches="$(workflow_matches "$pattern")"
  if [[ -n "$matches" ]]; then
    record "PASS" "$name" "$matches"
  else
    record "WARN" "$name" "No workflow matched pattern: $pattern"
  fi
}

{
  echo "# Gate baseline evidence"
  echo
  echo "- Repository: $repo"
  echo "- Gate contract path: $gate_contract_path"
  echo "- Require deploy: $require_deploy"
  echo "- Require Renovate: $require_renovate"
  echo
  echo "| Status | Check | Evidence |"
  echo "|---|---|---|"
} > "$summary_file"
: > "$machine_file"

if [[ -d "$repo/.github/workflows" ]]; then
  record "PASS" "workflow directory" ".github/workflows exists"
else
  record "FAIL" "workflow directory" ".github/workflows is missing"
fi

require_workflow_pattern \
  "reusable CI caller" \
  'marcel-tuinstra/devops/\.github/workflows/reusable-(ci|php-(lint|test))\.yml@'

warn_workflow_pattern \
  "container scan caller" \
  'marcel-tuinstra/devops/\.github/workflows/reusable-ci-docker\.yml@'

require_workflow_pattern \
  "release PR caller" \
  'marcel-tuinstra/devops/\.github/workflows/reusable-release-pr\.yml@'

require_workflow_pattern \
  "release tag caller" \
  'marcel-tuinstra/devops/\.github/workflows/reusable-release-tag\.yml@'

if bool_is_true "$require_deploy"; then
  require_workflow_pattern \
    "deploy caller" \
    'marcel-tuinstra/devops/\.github/workflows/reusable-cd-[a-z0-9-]+\.yml@'
else
  warn_workflow_pattern \
    "deploy caller" \
    'marcel-tuinstra/devops/\.github/workflows/reusable-cd-[a-z0-9-]+\.yml@'
fi

main_channel_matches="$(workflow_matches 'marcel-tuinstra/devops/\.github/workflows/[^[:space:]]+@main')"
if [[ -n "$main_channel_matches" ]]; then
  record "FAIL" "devops workflow pinning" "Uses @main in: $main_channel_matches"
else
  record "PASS" "devops workflow pinning" "No devops reusable workflow caller is pinned to @main"
fi

renovate_config=""
for candidate in "$repo/renovate.json" "$repo/.github/renovate.json"; do
  if [[ -f "$candidate" ]]; then
    renovate_config="$candidate"
    break
  fi
done

if [[ -n "$renovate_config" ]] && grep -q 'github>marcel-tuinstra/devops:renovate/' "$renovate_config"; then
  record "PASS" "Renovate preset" "$(repo_rel "$renovate_config") extends devops preset"
elif bool_is_true "$require_renovate"; then
  record "FAIL" "Renovate preset" "renovate.json or .github/renovate.json must extend github>marcel-tuinstra/devops:renovate/*"
else
  record "WARN" "Renovate preset" "No devops Renovate preset found"
fi

artifact_issues=""
while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  if grep -q 'actions/upload-artifact' "$file" && ! grep -q 'retention-days:' "$file"; then
    artifact_issues="${artifact_issues}$(repo_rel "$file") "
  fi
done < <(workflow_files)

if [[ -n "$artifact_issues" ]]; then
  record "FAIL" "artifact retention" "Upload artifact steps missing retention-days in: $artifact_issues"
else
  record "PASS" "artifact retention" "No upload-artifact steps without retention-days were found"
fi

require_file "$gate_contract_path" "Gate integration contract"

if [[ -f "$repo/.github/CODEOWNERS" || -f "$repo/CODEOWNERS" || -f "$repo/docs/branch-protection.md" ]]; then
  record "PASS" "branch protection evidence" "CODEOWNERS or docs/branch-protection.md exists"
else
  record "WARN" "branch protection evidence" "Branch protection cannot be verified locally; attach settings evidence during rollout"
fi

{
  echo
  echo "## Summary"
  echo
  echo "- Checks: $checks"
  echo "- Failures: $failures"
  echo "- Warnings: $warnings"
} >> "$summary_file"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "$summary_file" >> "$GITHUB_STEP_SUMMARY"
fi

echo "Gate baseline evidence written to $summary_file"

if bool_is_true "$fail_on_missing" && [[ "$failures" -gt 0 ]]; then
  echo "Gate baseline failed with $failures missing requirement(s)." >&2
  exit 1
fi
