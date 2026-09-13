#!/usr/bin/env bash
set -euo pipefail
umask 077

# Disposable native gate for DEV-30. It creates a PostgreSQL 17 custom dump and
# a real MinIO object with the reviewed images, then feeds the paired payload to
# restore-tracker. It never uses production credentials, mounts, or networks.
if [[ "$EUID" -ne 0 ]]; then
  echo 'run this fixture gate as root on the isolated Sanctuary host' >&2
  exit 2
fi

readonly postgres_image='docker.io/library/postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73'
readonly minio_image='quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e'
readonly mc_image='quay.io/minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727'
readonly php_image='ghcr.io/tuinstra-dev/tracker@sha256:0000000000000000000000000000000000000000000000000000000000000000'
readonly root_dir="/run/tuinstra-backup/tracker-native-fixture-$$"
readonly source_db="tracker-fixture-db-$$"
readonly source_minio="tracker-fixture-minio-$$"
readonly restore_adapter="${RESTORE_TRACKER_ADAPTER:-/usr/local/libexec/tuinstra-backup/restore-tracker}"

cleanup() {
  /usr/bin/docker rm --force "$source_minio" "$source_db" >/dev/null 2>&1 || true
  /usr/bin/rm -rf -- "$root_dir"
}
trap cleanup EXIT

/usr/bin/install -d -m 0700 "$root_dir/files" "$root_dir/minio-data"
/usr/bin/install -d -m 0700 "$root_dir/seed"
/usr/bin/chown 70:70 "$root_dir/seed"

/usr/bin/docker run --detach --name "$source_db" --network none \
  --env POSTGRES_DB=tracker_fixture --env POSTGRES_USER=tracker_fixture \
  --env POSTGRES_PASSWORD=fixture-password \
  --mount "type=bind,source=$root_dir/seed,target=/var/lib/postgresql/data" \
  --security-opt no-new-privileges:true "$postgres_image" >/dev/null
for _ in {1..60}; do
  if /usr/bin/docker exec "$source_db" pg_isready -U tracker_fixture -d tracker_fixture >/dev/null 2>&1; then break; fi
  sleep 1
done
/usr/bin/docker exec "$source_db" pg_isready -U tracker_fixture -d tracker_fixture >/dev/null
/usr/bin/docker exec -i "$source_db" psql -X -v ON_ERROR_STOP=1 -U tracker_fixture -d tracker_fixture <<'SQL'
CREATE TABLE doctrine_migration_versions (version VARCHAR(255) PRIMARY KEY);
INSERT INTO doctrine_migration_versions VALUES
  ('DoctrineMigrations\Version20260813120000'),
  ('DoctrineMigrations\Version20260912160000');
CREATE TABLE organization (id INTEGER PRIMARY KEY);
CREATE TABLE project (id INTEGER PRIMARY KEY);
CREATE TABLE story (id INTEGER PRIMARY KEY);
CREATE TABLE attachment (id INTEGER PRIMARY KEY, object_key VARCHAR(255) NOT NULL);
INSERT INTO organization VALUES (1);
INSERT INTO project VALUES (1);
INSERT INTO story VALUES (1);
INSERT INTO attachment VALUES (1, 'fixture.txt');
SQL
/usr/bin/docker exec "$source_db" pg_dump -Fc -U tracker_fixture -d tracker_fixture >"$root_dir/database.dump"

/usr/bin/docker run --detach --name "$source_minio" --network none \
  --env MINIO_ROOT_USER=fixture-root --env MINIO_ROOT_PASSWORD=fixture-password \
  --mount "type=bind,source=$root_dir/minio-data,target=/data" \
  --security-opt no-new-privileges:true "$minio_image" server /data --address :9000 >/dev/null
for _ in {1..30}; do
  if /usr/bin/docker run --rm --network "container:$source_minio" \
    --env MC_HOST_local=http://fixture-root:fixture-password@127.0.0.1:9000 "$mc_image" \
    ls local >/dev/null 2>&1; then break; fi
  sleep 1
done
/usr/bin/docker run --rm --network "container:$source_minio" \
  --env MC_HOST_local=http://fixture-root:fixture-password@127.0.0.1:9000 "$mc_image" \
  mb local/tracker-attachments >/dev/null
/usr/bin/printf 'native tracker attachment fixture\n' >"$root_dir/fixture.txt"
/usr/bin/docker run --rm --network "container:$source_minio" \
  --env MC_HOST_local=http://fixture-root:fixture-password@127.0.0.1:9000 \
  --mount "type=bind,source=$root_dir/fixture.txt,target=/fixture.txt,readonly" "$mc_image" \
  cp /fixture.txt local/tracker-attachments/fixture.txt >/dev/null
/usr/bin/docker run --rm --network "container:$source_minio" \
  --env MC_HOST_local=http://fixture-root:fixture-password@127.0.0.1:9000 "$mc_image" \
  cat local/tracker-attachments/fixture.txt | /usr/bin/sha256sum >"$root_dir/object.sha256"
[[ "$(/usr/bin/awk '{print $1}' "$root_dir/object.sha256")" == "$(/usr/bin/sha256sum "$root_dir/fixture.txt" | /usr/bin/awk '{print $1}')" ]] || {
  echo 'native MinIO object checksum did not match the fixture' >&2
  exit 1
}
/usr/bin/docker stop "$source_minio" >/dev/null
# snapshot_directory's first component is an archive namespace. Keep the real
# MinIO bucket below it so restore-tracker strips only that namespace.
/usr/bin/install -d -m 0700 "$root_dir/archive-root/attachments"
/usr/bin/cp -a "$root_dir/minio-data/." "$root_dir/archive-root/attachments/"
/usr/bin/tar -cf "$root_dir/files/attachments.tar" -C "$root_dir/archive-root" attachments
/usr/bin/printf 'POSTGRES_DB=tracker_fixture\nPOSTGRES_USER=tracker_fixture\nPOSTGRES_PASSWORD=fixture-password\n' >"$root_dir/files/env"
/usr/bin/chmod 0600 "$root_dir/files/env"

/usr/bin/python3 - "$root_dir/database.dump" "$root_dir/backup-manifest.json" "$postgres_image" "$minio_image" "$php_image" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).parent
migrations = ["DoctrineMigrations\\Version20260813120000", "DoctrineMigrations\\Version20260912160000"]
images = {"postgres": sys.argv[3], "php": sys.argv[5], "minio": sys.argv[4]}
fixture = (root / "fixture.txt").read_bytes()
s3_sha = (root / "object.sha256").read_text(encoding="utf-8").split()[0]
if s3_sha != hashlib.sha256(fixture).hexdigest():
    raise SystemExit("native object manifest source checksum is invalid")
(root / "files/object-manifest.json").write_text(json.dumps({
    "algorithm": "tracker-s3-object-v1", "bucket": "tracker-attachments",
    "objects": [{"key": "fixture.txt", "bytes": len(fixture),
                  "sha256": s3_sha}],
    "object_count": 1, "total_bytes": len(fixture),
}, sort_keys=True, separators=(",", ":")) + "\n")
(root / "files/object-manifest.json").chmod(0o600)
manifest = {
    "schema_version": 1, "artifact_id": "11111111-1111-4111-8111-111111111111",
    "host_slug": "tuinstra-prod-02", "app_id": "tracker", "adapter": "tracker-compose-v1",
    "created_at": "2026-09-13T00:00:00Z", "database_service": "postgres",
    "images": list(images.values()), "image_services": images,
    "object_store_bucket": "tracker-attachments",
    "database": {"engine": "postgresql", "server_version": "17.5",
        "dump_version": "pg_dump (PostgreSQL) 17.5", "dump_format": "custom",
        "service": "postgres", "content_marker": {
            "algorithm": "tracker-doctrine-migrations-v1",
            "sha256": hashlib.sha256(("\n".join(migrations) + "\n").encode()).hexdigest(),
            "migration_count": 2,
            "row_counts": {"organization_count": 1, "project_count": 1,
                            "story_count": 1, "attachment_count": 1},
        }},
    "inputs": [{"name": "database.dump", "sha256": hashlib.sha256((root / "database.dump").read_bytes()).hexdigest(),
                 "bytes": (root / "database.dump").stat().st_size},
                {"name": "files/attachments.tar", "sha256": hashlib.sha256((root / "files/attachments.tar").read_bytes()).hexdigest(),
                 "bytes": (root / "files/attachments.tar").stat().st_size},
                {"name": "files/object-manifest.json", "sha256": hashlib.sha256((root / "files/object-manifest.json").read_bytes()).hexdigest(),
                 "bytes": (root / "files/object-manifest.json").stat().st_size},
                {"name": "files/env", "sha256": hashlib.sha256((root / "files/env").read_bytes()).hexdigest(),
                 "bytes": (root / "files/env").stat().st_size}],
}
(root / "backup-manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
(root / "backup-manifest.json").chmod(0o600)
PY

output="$("$restore_adapter" "$root_dir")"
/usr/bin/python3 -c 'import json,sys; value=json.loads(sys.argv[1]); assert value["status"] == "passed" and value["adapter"] == "tracker-compose-v1" and value["containers_removed"] and value["workspace_removed"]' "$output"
echo 'native Tracker PG17/MinIO restore fixture passed; no production data or credentials used'
