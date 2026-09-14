#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m json.tool "$repo_root/backup/profiles/prod01.json" >/dev/null
python3 -m json.tool "$repo_root/backup/profiles/prod02.json" >/dev/null
python3 -m json.tool "$repo_root/backup/profiles/sanctuary.json" >/dev/null
python3 -m json.tool "$repo_root/install-input/manifest.json" >/dev/null
python3 -c 'import pathlib,sys; [compile(pathlib.Path(p).read_text(encoding="utf-8"), p, "exec") for p in sys.argv[1:]]' \
  "$repo_root/backup/tuinstra_backup.py" "$repo_root/scripts/test_production_backup.py" \
  "$repo_root/scripts/bootstrap_backup_credentials.py" "$repo_root/scripts/test_backup_credential_bootstrap.py" \
  "$repo_root/scripts/stage_backup_escrow.py" "$repo_root/scripts/test_stage_backup_escrow.py" \
  "$repo_root/scripts/verify-install-inputs.py" \
  "$repo_root/scripts/test_sanctuary_installer_contract.py" \
  "$repo_root/backup/onepassword-escrow.py" "$repo_root/scripts/test_backup_onepassword_escrow.py"
bash -n "$repo_root/backup/restore-umami"
python3 -c 'compile(open("backup/production_restore.py", encoding="utf-8").read(), "backup/production_restore.py", "exec")'
python3 -c 'compile(open("backup/production_restore_target.py", encoding="utf-8").read(), "backup/production_restore_target.py", "exec")'
bash -n "$repo_root/backup/restore-tracker"
bash -n "$repo_root/backup/restore-tracker"
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
grep -q 'tuinstra:production-restore-safety' "$repo_root/backup/production_restore.py"
grep -q 'production restore requires a full snapshot id' "$repo_root/backup/production_restore.py"
grep -q 'tuinstra-restore' "$repo_root/infra/ansible/roles/production_backup/tasks/main.yml"
grep -q 'production_restore_target.py' "$repo_root/infra/ansible/roles/production_backup/tasks/main.yml"
grep -q 'application is in a production restore maintenance window' \
  "$repo_root/infra/ansible/roles/production_host_baseline/templates/tuinstra-compose-deploy.j2"
grep -q "confirmation='umami / tuinstra-prod-01 / production'" \
  "$repo_root/backup/tuinstra-production-restore"
grep -q 'timestamp_timeout=0' "$repo_root/infra/ansible/roles/sanctuary_backup/tasks/main.yml"
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
grep -q 'postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73' "$repo_root/backup/restore-tracker"
grep -q 'quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e' "$repo_root/backup/restore-tracker"
grep -q 'quay.io/minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727' "$repo_root/backup/restore-tracker"
grep -q 'files/object-manifest.json' "$repo_root/backup/restore-tracker"
grep -q 'object_store_reconciliation' "$repo_root/backup/restore-tracker"
grep -q -- '--network none' "$repo_root/backup/restore-tracker"
grep -q 'tracker-doctrine-migrations-v1' "$repo_root/backup/restore-tracker"
grep -q 'isolated Tracker restore cleanup failed' "$repo_root/backup/restore-tracker"
grep -q 'JOIN \\"user\\" AS u ON u.user_id = t.user_id' "$repo_root/backup/restore-umami"
PYTHONDONTWRITEBYTECODE=1 python3 - "$repo_root/backup/restore-umami" <<'PY'
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

restore = Path(sys.argv[1]).read_text(encoding="utf-8")
start = '/usr/bin/python3 - "$payload/files/postgres-env" "$work/loopback.env" <<\'PY\'\n'
if restore.count(start) != 1:
    raise SystemExit("postgres environment parser marker is ambiguous")
parser, separator, _ = restore.partition(start)[2].partition("\nPY\n")
if not separator:
    raise SystemExit("postgres environment parser terminator is missing")

required = (
    "POSTGRES_DB=umami db\n"
    "POSTGRES_USER=umami\n"
    "POSTGRES_PASSWORD=synthetic-p@ss/word\n"
)
cases = {
    "required only": (required, True),
    "deployed optional timezone": (required + "TZ=UTC\n", True),
    "wrong timezone": (required + "TZ=Europe/Amsterdam\n", False),
    "timezone is case sensitive": (required + "TZ=utc\n", False),
    "duplicate timezone": (required + "TZ=UTC\nTZ=UTC\n", False),
    "unknown key": (required + "PGTZ=UTC\n", False),
    "malformed line": (required + "TZ\n", False),
    "missing credential": (required.replace("POSTGRES_PASSWORD=synthetic-p@ss/word\n", ""), False),
}

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    for name, (contents, should_pass) in cases.items():
        source = root / "postgres.env"
        target = root / "loopback.env"
        source.write_text(contents, encoding="utf-8")
        target.unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, "-c", parser, str(source), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if "synthetic-p@ss/word" in result.stdout + result.stderr:
            raise SystemExit(f"{name}: parser exposed a database credential")
        if should_pass:
            if result.returncode != 0:
                raise SystemExit(f"{name}: valid environment was rejected")
            expected = "DATABASE_URL=postgresql://umami:synthetic-p%40ss%2Fword@127.0.0.1:5432/umami%20db\n"
            if target.read_text(encoding="utf-8") != expected:
                raise SystemExit(f"{name}: loopback URL is incorrect")
            if stat.S_IMODE(target.stat().st_mode) != 0o600:
                raise SystemExit(f"{name}: loopback environment permissions are unsafe")
        elif result.returncode == 0 or target.exists():
            raise SystemExit(f"{name}: invalid environment was accepted")
PY
PYTHONDONTWRITEBYTECODE=1 python3 - "$repo_root/backup/restore-umami" <<'PY'
from pathlib import Path
import subprocess
import sys

restore = Path(sys.argv[1]).read_text(encoding="utf-8")
start = '| /usr/bin/docker exec -i "$app_container" node -e \'\n'
end = "\n' >/dev/null 2>&1; then"
if restore.count(start) != 1:
    raise SystemExit("two-factor validation script marker is ambiguous")
validator, separator, _ = restore.partition(start)[2].partition(end)
if not separator:
    raise SystemExit("two-factor validation script terminator is missing")

# Empty synthetic input must reach the validator's fixed input-shape rejection.
# This catches synchronous Promise-wiring failures without requiring a database,
# secret, token, or HTTP request.
result = subprocess.run(
    ["node", "-e", validator], input="", capture_output=True, text=True, check=False,
)
if result.returncode != 2:
    raise SystemExit("two-factor validator failed before processing bounded input")
PY
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
if grep -q 'sha-required' "$repo_root/backup/profiles/prod02.json"; then
  echo 'prod-02 Tracker backup profile must not contain placeholder image digests' >&2
  exit 1
fi
grep -q '"app_id": "tracker",[[:space:]]*$' "$repo_root/backup/profiles/prod02.json"
grep -q '"enabled": true' "$repo_root/backup/profiles/prod02.json"
grep -q 'ghcr.io/tuinstra-dev/tracker@sha256:706ff506fc62a5bddfd1b94c176d91be424474454b8ff93ade34b13e5c8b50ba' \
  "$repo_root/backup/profiles/prod02.json"
grep -q 'ghcr.io/tuinstra-dev/tracker@sha256:24768ca0e506acf1ae707f88d6ad6c5c0f66f472917f54f11f13abcfd83147cb' \
  "$repo_root/backup/profiles/prod02.json"
grep -q '"database": database_versions' "$repo_root/backup/tuinstra_backup.py"
grep -q 'run-active --host "$host" --app "$app" --trigger manual' \
  "$repo_root/backup/tuinstra-backup-admin"
grep -q 'tuinstra-prod-02' "$repo_root/backup/tuinstra-backup-admin"
grep -q 'backup admin failed: target must be umami or tracker' \
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
grep -q 'restic-passwords/tuinstra-prod-02/tracker.password' \
  "$repo_root/scripts/install-sanctuary-backups"
grep -q -- '--plan-hash 4da29a1c175b1d5f234644da763e8509c7c02ff6c5e83f5f748346d49a5af725' \
  "$repo_root/scripts/install-sanctuary-backups"
grep -q -- '--hour 3 --minute 0 --daily 7 --weekly 4 --monthly 12' \
  "$repo_root/scripts/install-sanctuary-backups"
grep -q 'ensure_restic_password("tuinstra-prod-02", "tracker")' \
  "$repo_root/scripts/bootstrap_backup_credentials.py"
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
