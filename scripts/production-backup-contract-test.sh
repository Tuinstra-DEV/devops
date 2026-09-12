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
grep -q -- '--network "container:' "$repo_root/backup/restore-umami"
if grep -q -- 'network create --internal' "$repo_root/backup/restore-umami"; then
  echo 'restore adapter must not use a bridge-backed internal network' >&2
  exit 1
fi
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
