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
PYTHONDONTWRITEBYTECODE=1 python3 scripts/php-deploy-hostkey-test.py
ruby ./scripts/heavy-ci-v2-contract-test.rb
./scripts/heavy-ci-rollout-docs-test.sh
./scripts/heavy-ci-baseline-test.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_classify_ci_changes.py
./scripts/production-profile-executor-contract-test.sh
ruby ./scripts/dependency-update-policy-test.rb

if [[ -n "${DEPENDABOT_FLEET_ROOT:-}" ]]; then
  ruby ./scripts/dependency-update-fleet-test.rb \
    "agent-lab=${DEPENDABOT_FLEET_ROOT}/agent-lab" \
    "console=${DEPENDABOT_FLEET_ROOT}/console" \
    "devops=${DEPENDABOT_FLEET_ROOT}/devops" \
    "gate=${DEPENDABOT_FLEET_ROOT}/gate" \
    "marcel-site=${DEPENDABOT_FLEET_ROOT}/marcel-site" \
    "notify=${DEPENDABOT_FLEET_ROOT}/notify" \
    "openairco=${DEPENDABOT_FLEET_ROOT}/openairco" \
    "openairco-site=${DEPENDABOT_FLEET_ROOT}/openairco-site" \
    "status=${DEPENDABOT_FLEET_ROOT}/status" \
    "tracker=${DEPENDABOT_FLEET_ROOT}/tracker" \
    "tuinstra-site=${DEPENDABOT_FLEET_ROOT}/tuinstra-site" \
    "wodiq-app=${DEPENDABOT_FLEET_ROOT}/wodiq-app" \
    "wodiq-platform=${DEPENDABOT_FLEET_ROOT}/wodiq-platform" \
    "wodiq-site=${DEPENDABOT_FLEET_ROOT}/wodiq-site"
else
  echo "DEPENDABOT_FLEET_ROOT not set; cross-repository policy check skipped"
fi

echo "test passed"

./scripts/production-host-baseline-test.sh
node --test ./scripts/production-restricted-tracker-bridge.test.mjs
./scripts/production-host-rehearsal-test.sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_console_agent_enroll.py
./scripts/production-umami-test.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_isolated_restore_harness.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_production_backup.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_production_restore.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_backup_credential_bootstrap.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_stage_backup_escrow.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_sanctuary_installer_contract.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_backup_onepassword_escrow.py
./scripts/production-backup-contract-test.sh
./scripts/production-umami-test.sh

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_gate_pr_security_source.py tests/test_gate_pr_security_evidence.py tests/test_gate_pr_security_local_integration.py
ruby scripts/gate-pr-security-contract-test.rb
