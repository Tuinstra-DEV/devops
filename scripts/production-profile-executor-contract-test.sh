#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
executor="$repo_root/scripts/production-profile-executor"
bridge="$repo_root/scripts/production-profile-bridge"
installer="$repo_root/scripts/install-production-profile-executor"
runbook="$repo_root/docs/playbooks/production-profile-executor.md"

bash -n "$bridge" "$installer"
python3 -c 'import sys; compile(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1], "exec")' "$executor"
grep -Fq 'restrict,no-user-rc,command="/usr/local/libexec/tuinstra-profile-bridge"' "$installer"
grep -Fq 'NOPASSWD: /usr/local/sbin/tuinstra-profile-executor ""' "$installer"
grep -Fq 'restricted profile key must not enter the admin allowlist' "$installer"
grep -Fq 'restricted profile key must not enter the deploy allowlist' "$installer"
grep -Fq 'tuinstra-rehearsal-01:baseline_umami_restore' "$installer"
grep -Fq '/run/lock/tuinstra/operations.host.' "$installer"
grep -Fq 'SSH_ORIGINAL_COMMAND' "$bridge"
grep -Fq 'operations.host.<host>.lock' "$runbook"
if grep -Eq '(subprocess\.(run|Popen)\([^[]*request|shell[[:space:]]*=[[:space:]]*True)' "$executor"; then
  echo "executor must never turn request data into a shell command" >&2
  exit 1
fi

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_production_profile_executor.py
echo "production profile executor contract passed"
