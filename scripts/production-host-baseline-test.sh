#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
role="$repo_root/infra/ansible/roles/production_host_baseline"

bash -n "$repo_root/scripts/production-host-baseline"
for template in tuinstra-deploy-shell.j2 tuinstra-compose-deploy.j2; do
  grep -q '^#!/usr/bin/env bash$' "$role/templates/$template"
done

grep -q 'PermitRootLogin no' "$role/templates/ssh-hardening.conf.j2"
grep -q 'PasswordAuthentication no' "$role/templates/ssh-hardening.conf.j2"
grep -q 'every image must be pinned by sha256 digest' "$role/templates/tuinstra-compose-deploy.j2"
grep -q 'application is not allowlisted' "$role/templates/tuinstra-compose-deploy.j2"
workflow="$repo_root/.github/workflows/reusable-cd-nuxt-ssg.yml"
grep -q 'name: Deploy application through fixed endpoint' "$workflow"
grep -q 'deploy@"\$DEPLOY_HOST" "deploy \${{ inputs.service-name }}"' "$workflow"
if grep -q "printf 'deploy %s %s" "$workflow"; then
  echo 'reusable CD must not stream a legacy stdin protocol' >&2
  exit 1
fi
grep -q 'no-new-privileges:true' "$role/templates/caddy-compose.yml.j2"
grep -q 'production_caddy_enable_https' "$role/templates/caddy-compose.yml.j2"
grep -q 'not production_caddy_enable_https' "$role/templates/Caddyfile.j2"
grep -q 'external: true' "$role/templates/caddy-compose.yml.j2"
grep -q 'respond 404' "$role/templates/Caddyfile.j2"

if grep -R -E '(PRIVATE KEY|BEGIN OPENSSH|password[[:space:]]*:)' \
  "$repo_root/infra/ansible/production" \
  "$role" >/dev/null; then
  echo "possible secret material in production baseline" >&2
  exit 1
fi

echo "production host baseline contract passed"
