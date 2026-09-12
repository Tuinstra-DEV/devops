#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
role="$repo_root/infra/ansible/roles/production_umami"

bash -n "$repo_root/scripts/production-umami"
grep -q '^#!/usr/bin/env bash$' "$repo_root/scripts/production-umami"

grep -q 'ghcr.io/umami-software/umami:3.3.1@sha256:' "$role/defaults/main.yml"
grep -q 'docker.io/library/postgres:15-alpine@sha256:' "$role/defaults/main.yml"
grep -q 'TWO_FACTOR_ENCRYPTION_KEY=' "$role/templates/umami.env.j2"
grep -q '/api/admin/2fa/global' "$role/templates/admin-bootstrap.mjs.j2"
grep -q "await login('umami')" "$role/templates/admin-bootstrap.mjs.j2"
grep -q 'internal: true' "$role/templates/compose.yml.j2"
grep -q 'external: true' "$role/templates/compose.yml.j2"
if grep -q '^      - port$' "$role/tasks/verify.yml"; then
  echo "docker compose port is ambiguous for unpublished ports on Compose 5.5.1" >&2
  exit 1
fi
grep -q 'HostConfig.PortBindings' "$role/tasks/verify.yml"

if grep -R -E '(PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY)' \
  "$repo_root/infra/ansible/production-umami"* \
  "$role" >/dev/null; then
  echo "secret key material found in Umami deployment" >&2
  exit 1
fi

ansible_playbook="${ANSIBLE_PLAYBOOK:-}"
if [[ -z "$ansible_playbook" ]]; then
  if command -v ansible-playbook >/dev/null 2>&1; then
    ansible_playbook="$(command -v ansible-playbook)"
  elif [[ -x "$repo_root/../.tooling/ansible/bin/ansible-playbook" ]]; then
    ansible_playbook="$repo_root/../.tooling/ansible/bin/ansible-playbook"
  else
    echo "ansible-playbook is required for Umami contract tests" >&2
    exit 69
  fi
fi

export ANSIBLE_CONFIG="$repo_root/infra/ansible/ansible.cfg"
export ANSIBLE_HOME="${ANSIBLE_HOME:-${TMPDIR:-/tmp}/tuinstra-ansible-${UID}}"
export ANSIBLE_REMOTE_TEMP="${ANSIBLE_REMOTE_TEMP:-${TMPDIR:-/tmp}/tuinstra-ansible-local-${UID}}"
/bin/mkdir -p "$ANSIBLE_HOME" "$ANSIBLE_REMOTE_TEMP"
/bin/chmod 0700 "$ANSIBLE_HOME" "$ANSIBLE_REMOTE_TEMP"

for playbook in \
  production-umami.yml \
  production-umami-verify.yml \
  production-umami-template-test.yml; do
  "$ansible_playbook" \
    --inventory "$repo_root/infra/ansible/production/inventory.yml" \
    --syntax-check "$repo_root/infra/ansible/$playbook"
done
"$ansible_playbook" "$repo_root/infra/ansible/production-umami-template-test.yml"

echo "production Umami contract passed"
