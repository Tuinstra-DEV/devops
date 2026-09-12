#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m json.tool "$repo_root/backup/profiles/prod01.json" >/dev/null
python3 -m json.tool "$repo_root/backup/profiles/prod02.json" >/dev/null
python3 -m json.tool "$repo_root/backup/profiles/sanctuary.json" >/dev/null
python3 -c 'import pathlib,sys; [compile(pathlib.Path(p).read_text(encoding="utf-8"), p, "exec") for p in sys.argv[1:]]' \
  "$repo_root/backup/tuinstra_backup.py" "$repo_root/scripts/test_production_backup.py" \
  "$repo_root/scripts/bootstrap_backup_credentials.py" "$repo_root/scripts/test_backup_credential_bootstrap.py" \
  "$repo_root/scripts/stage_backup_escrow.py" "$repo_root/scripts/test_stage_backup_escrow.py" \
  "$repo_root/scripts/test_sanctuary_installer_contract.py" \
  "$repo_root/backup/onepassword-escrow.py" "$repo_root/scripts/test_backup_onepassword_escrow.py"
bash -n "$repo_root/backup/restore-umami"
bash -n "$repo_root/backup/tuinstra-backup-admin"
bash -n "$repo_root/scripts/install-sanctuary-backups"
bash -n "$repo_root/scripts/restic-retention-integration-test.sh"
bash -n "$repo_root/scripts/restore-network-isolation-integration-test.sh"

grep -q 'umami:3.3.1@sha256:' "$repo_root/backup/restore-umami"
grep -q 'postgres:15-alpine@sha256:' "$repo_root/backup/restore-umami"
grep -q -- '--network none' "$repo_root/backup/restore-umami"
grep -q -- 'docker run --rm --interactive --network none --entrypoint pg_restore' \
  "$repo_root/backup/restore-umami"
grep -q -- '--network "container:' "$repo_root/backup/restore-umami"
grep -q '/usr/bin/install -d -m 0700 "$work/postgres"' \
  "$repo_root/backup/restore-umami"
grep -q '/usr/bin/chown 70:70 "$work/postgres"' \
  "$repo_root/backup/restore-umami"
if grep -Eq '/usr/bin/install[[:space:]].*(-o 70|-g 70)' "$repo_root/backup/restore-umami"; then
  echo 'numeric postgres ownership must use chown, not install user-name lookup' >&2
  exit 1
fi
if grep -q -- 'network create --internal' "$repo_root/backup/restore-umami"; then
  echo 'restore adapter must not use a bridge-backed internal network' >&2
  exit 1
fi
if grep -Eq 'docker rm[^\n]*\|\| true' "$repo_root/backup/restore-umami"; then
  echo 'restore adapter must not suppress container cleanup failures' >&2
  exit 1
fi
grep -q 'isolated restore cleanup failed' "$repo_root/backup/restore-umami"
grep -q '"containers_removed": True' "$repo_root/backup/restore-umami"
grep -q '"workspace_removed": True' "$repo_root/backup/restore-umami"
grep -q 'encrypted_secret_validation' "$repo_root/backup/restore-umami"
grep -q 'database_content_marker' "$repo_root/backup/restore-umami"
grep -q 'files/two-factor-encryption-key' "$repo_root/backup/restore-umami"
grep -q 'restored database content marker does not match the export' "$repo_root/backup/restore-umami"
grep -q 'JOIN \\"user\\" AS u ON u.user_id = t.user_id' "$repo_root/backup/restore-umami"
grep -q 'duration_seconds' "$repo_root/backup/tuinstra_backup.py"
grep -q 'external_effects_blocked' "$repo_root/backup/tuinstra_backup.py"
grep -q 'commands.add_parser("safety-ingest")' "$repo_root/backup/tuinstra_backup.py"
grep -q 'commands.add_parser("safety-pull")' "$repo_root/backup/tuinstra_backup.py"
grep -q 'commands.add_parser("materialize")' "$repo_root/backup/tuinstra_backup.py"
if grep -Eq 'commands.add_parser\("(ingest|attempt-start|attempt-finish)"\)' \
    "$repo_root/backup/tuinstra_backup.py"; then
  echo 'privileged internal evidence and ingest primitives must not be public CLI commands' >&2
  exit 1
fi
if grep -q 'tuinstra-backup ALL=(root)' "$repo_root/scripts/install-sanctuary-backups"; then
  echo 'scheduled backup account must not have wildcard-shaped root sudo commands' >&2
  exit 1
fi
grep -q '"User=root\\nGroup=root\\nUMask=0077\\n"' "$repo_root/backup/tuinstra_backup.py"
grep -q '"approved_images"' "$repo_root/backup/profiles/prod01.json"
grep -q '"database": database_versions' "$repo_root/backup/tuinstra_backup.py"
grep -q 'run-active --host "$host" --app "$app" --trigger manual' \
  "$repo_root/backup/tuinstra-backup-admin"
grep -q 'exec "$engine" --config "$config" escrow-recovery-test' \
  "$repo_root/backup/tuinstra-backup-admin"
if grep -q -- '--plan-hash "$plan_hash"' "$repo_root/backup/tuinstra-backup-admin"; then
  echo 'manual recovery CLI must resolve the trusted active policy instead of hardcoding a plan hash' >&2
  exit 1
fi
grep -q '"materialized_root": "/var/lib/tuinstra-backup/materialized"' \
  "$repo_root/backup/profiles/sanctuary.json"
grep -q 'spool_quota_bytes.*10737418240' "$repo_root/backup/profiles/prod01.json"
grep -q '658abb88b4e65d37c45bd97bcaa9f523911321ee1131d4367d544a2292b05e8e' \
  "$repo_root/infra/ansible/roles/sanctuary_backup/defaults/main.yml"
grep -q '"--keep-tag", "tuinstra:production-restore-safety"' "$repo_root/backup/tuinstra_backup.py"
grep -q 'CATALOG_LIMIT = 500' "$repo_root/backup/tuinstra_backup.py"
grep -q 'fallback_total_minutes.*75' "$repo_root/backup/tuinstra_backup.py"
PYTHONDONTWRITEBYTECODE=1 python3 - "$repo_root" <<'PY'
import importlib.util
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("backup", root / "backup/tuinstra_backup.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
document = module.policy_document("tuinstra-prod-01", "umami", "production-v1", 2, 0, 7, 4, 12)
assert module.document_hash(document) == "658abb88b4e65d37c45bd97bcaa9f523911321ee1131d4367d544a2292b05e8e"
PY
grep -q 'env_keep.*SSH_ORIGINAL_COMMAND' \
  "$repo_root/infra/ansible/roles/production_backup/tasks/main.yml"

if grep -R -E '(BEGIN (OPENSSH|AGE) PRIVATE KEY|AGE-SECRET-KEY-|POSTGRES_PASSWORD=|APP_SECRET=)' \
  "$repo_root/backup" "$repo_root/infra/ansible/roles/production_backup" \
  "$repo_root/infra/ansible/roles/sanctuary_backup" >/dev/null; then
  echo 'secret material found in production backup sources' >&2
  exit 1
fi

echo 'production backup contract passed'
