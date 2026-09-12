#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
postgres_image='docker.io/library/postgres:15-alpine@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b'
umami_image='ghcr.io/umami-software/umami:3.3.1@sha256:fa32d116cf20cad52cbc3fad9a63b46e7fa02299d8f967168eb453d49c476b4a'
operation="dev34-$PPID-$$"
work="$(mktemp -d "$repo_root/.dev34-empty-target.XXXXXX")"
network="${operation}-source"
source_db="${operation}-source-db"
source_app="${operation}-source-app"

cleanup() {
  docker compose --project-name "$operation" --project-directory "$work/app" \
    --file "$work/app/compose.yml" down --volumes >/dev/null 2>&1 || true
  docker rm --force "$source_app" "$source_db" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT

docker info >/dev/null
mkdir -p "$work/source-postgres" "$work/payload/files" "$work/app" "$work/secrets" \
  "$work/data/postgres" "$work/caddy/sites" "$work/journal" "$work/staging" "$work/maintenance"
chmod 0700 "$work/source-postgres" "$work/payload" "$work/secrets" "$work/data/postgres" \
  "$work/journal" "$work/staging" "$work/maintenance"

cat >"$work/payload/files/postgres-env" <<'EOF'
POSTGRES_DB=umami
POSTGRES_USER=umami
POSTGRES_PASSWORD=dev34-isolated-only
TZ=UTC
EOF
cat >"$work/payload/files/umami-env" <<'EOF'
DATABASE_URL=postgresql://umami:dev34-isolated-only@db:5432/umami
APP_SECRET=dev34-isolated-app-secret
TWO_FACTOR_ENCRYPTION_KEY=dev34-isolated-two-factor-key
DISABLE_TELEMETRY=1
EOF
printf '%s\n' 'dev34-isolated-only' >"$work/payload/files/database-password"
printf '%s\n' 'dev34-isolated-app-secret' >"$work/payload/files/app-secret"
printf '%s\n' 'dev34-isolated-two-factor-key' >"$work/payload/files/two-factor-encryption-key"
printf '%s\n' 'dev34-isolated-admin' >"$work/payload/files/admin-password"
chmod 0600 "$work/payload/files/"*

docker network create --internal "$network" >/dev/null
docker run --detach --name "$source_db" --network "$network" --network-alias db \
  --env-file "$work/payload/files/postgres-env" \
  --mount "type=bind,source=$work/source-postgres,target=/var/lib/postgresql/data" \
  --security-opt no-new-privileges:true "$postgres_image" >/dev/null

for _ in {1..60}; do
  if docker exec "$source_db" sh -eu -c \
    'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="select 1"' \
    >/dev/null 2>&1; then break; fi
  sleep 1
done
docker run --detach --name "$source_app" --network "$network" \
  --env-file "$work/payload/files/umami-env" --security-opt no-new-privileges:true \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m "$umami_image" >/dev/null
for _ in {1..90}; do
  if docker exec "$source_app" node -e \
    'fetch("http://127.0.0.1:3000/api/heartbeat").then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))' \
    >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$source_app" node -e \
  'fetch("http://127.0.0.1:3000/api/heartbeat").then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))'
printf '%s\n' \
  'create table restore_rehearsal_marker(id integer primary key, proof text not null);' \
  "insert into restore_rehearsal_marker values (34, 'empty-target');" \
  | docker exec -i "$source_db" sh -eu -c \
      'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' >/dev/null
docker exec "$source_db" sh -eu -c \
  'exec pg_dump --format=custom --username="$POSTGRES_USER" --file=/tmp/database.dump "$POSTGRES_DB"'
docker cp "$source_db:/tmp/database.dump" "$work/payload/database.dump" >/dev/null
[[ -s "$work/payload/database.dump" ]] || {
  echo "isolated source dump is empty" >&2
  exit 1
}
docker rm --force "$source_app" "$source_db" >/dev/null
docker network rm "$network" >/dev/null

cat >"$work/payload/files/compose" <<EOF
services:
  db:
    image: $postgres_image
    user: '70:70'
    restart: unless-stopped
    env_file: [$work/secrets/postgres.env]
    volumes: [$work/data/postgres:/var/lib/postgresql/data]
    healthcheck:
      test: [CMD-SHELL, 'pg_isready -U "\$\$POSTGRES_USER" -d "\$\$POSTGRES_DB"']
      interval: 2s
      timeout: 2s
      retries: 30
    networks: [data]
  umami:
    image: $umami_image
    restart: unless-stopped
    env_file: [$work/secrets/umami.env]
    depends_on:
      db: {condition: service_healthy}
    read_only: true
    tmpfs: ['/tmp:rw,noexec,nosuid,size=64m']
    networks: [data]
networks:
  data: {internal: true}
EOF

PYTHONDONTWRITEBYTECODE=1 python3 - "$repo_root" "$work" "$operation" <<'PY'
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

repo, root = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo / "backup"))
import production_restore_target as target

payload = root / "payload"
inputs = []
for source in sorted(path for path in payload.rglob("*") if path.is_file()):
    relative = source.relative_to(payload).as_posix()
    inputs.append({"name": relative, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                   "bytes": source.stat().st_size})
manifest = {"schema_version": 1, "artifact_id": "12345678-1234-4123-8123-123456789abc",
            "host_slug": "tuinstra-prod-01", "app_id": "umami", "adapter": "postgres-compose-v1",
            "created_at": "2026-09-12T00:00:00Z", "database_service": "db",
            "images": sorted(target.APPROVED_IMAGES), "inputs": inputs}
(payload / "backup-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

lock = root / "operations.host.tuinstra-prod-01.lock"
lock.touch(); lock.chmod(0o660)
route = root / "caddy/sites/umami.caddy"
route.write_text("umami.test { reverse_proxy umami:3000 }\n", encoding="utf-8")
(root / "caddy/compose.yml").write_text("services: {}\n", encoding="utf-8")
config = target.TargetConfig(stage_root=root / "staging", journal_root=root / "journal",
    app_root=root / "app", secret_root=root / "secrets", data_root=root / "data",
    caddy_route=route, caddy_compose=root / "caddy/compose.yml",
    producer_engine=root / "unused", producer_config=root / "unused.json", host_lock=lock,
    maintenance_root=root / "maintenance", maximum_stage_bytes=1024 * 1024 * 1024,
    docker_bin=Path(shutil.which("docker") or "/usr/bin/docker"), compose_project=sys.argv[3],
    postgres_uid=os.getuid(), postgres_gid=os.getgid())
target.ROOT_UID = os.getuid()
def rehearsal_run(argv, *, stdin=None, timeout=7200):
    result = subprocess.run(argv, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        sys.stderr.write(result.stderr.decode(errors="replace"))
        raise target.TargetError(f"isolated rehearsal command failed: {Path(argv[0]).name}")
    return result

adapter = target.UmamiAdapter(config, rehearsal_run)
assert adapter.target_state() == "empty"
adapter.validate_restore(payload)
evidence = adapter.apply(payload, "isolated-01")
assert evidence == {"health": "passed", "data": "passed"}
assert (root / "secrets/admin-bootstrap.complete").read_text() == "restored:isolated-01\n"

# A structurally valid bundle with an invalid pg_dump must fail before the empty
# target is replaced. This exercises the real PostgreSQL image and adapter path.
bad_payload = root / "bad-payload"
shutil.copytree(payload, bad_payload)
(bad_payload / "database.dump").write_bytes(b"not-a-postgresql-dump")
bad_manifest = json.loads((bad_payload / "backup-manifest.json").read_text(encoding="utf-8"))
for item in bad_manifest["inputs"]:
    if item["name"] == "database.dump":
        item["sha256"] = hashlib.sha256((bad_payload / "database.dump").read_bytes()).hexdigest()
        item["bytes"] = (bad_payload / "database.dump").stat().st_size
(bad_payload / "backup-manifest.json").write_text(json.dumps(bad_manifest), encoding="utf-8")
bad_data = root / "bad-data"
(bad_data / "postgres").mkdir(parents=True)
bad_config = target.TargetConfig(stage_root=root / "bad-staging", journal_root=root / "bad-journal",
    app_root=root / "app", secret_root=root / "secrets", data_root=bad_data,
    caddy_route=route, caddy_compose=root / "caddy/compose.yml",
    producer_engine=root / "unused", producer_config=root / "unused.json", host_lock=lock,
    maintenance_root=root / "maintenance", maximum_stage_bytes=1024 * 1024 * 1024,
    docker_bin=Path(shutil.which("docker") or "/usr/bin/docker"), compose_project=sys.argv[3] + "-bad",
    postgres_uid=os.getuid(), postgres_gid=os.getgid())
try:
    target.UmamiAdapter(bad_config, rehearsal_run).apply(bad_payload, "isolated-bad")
except target.TargetError:
    pass
else:
    raise AssertionError("corrupt database dump unexpectedly restored")
assert target.UmamiAdapter(bad_config, rehearsal_run).target_state() == "empty"
PY

marker="$(docker compose --project-name "$operation" --project-directory "$work/app" \
  --file "$work/app/compose.yml" exec -T db sh -eu -c \
  'psql --tuples-only --no-align --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="select proof from restore_rehearsal_marker where id=34"')"
[[ "$marker" == empty-target ]]
echo "production restore empty-target rehearsal passed"
