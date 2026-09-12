#!/usr/bin/env bash
set -euo pipefail

readonly image='docker.io/library/postgres:15-alpine@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b'
readonly operation="dev30-network-test-$PPID-$$"
readonly first="${operation}-db"
readonly second="${operation}-app"
readonly dump_source="${operation}-dump-source"
readonly fixture_root="$(mktemp -d)"

cleanup() {
  docker rm --force "$second" "$first" "$dump_source" >/dev/null 2>&1 || true
  rm -rf -- "$fixture_root"
}
trap cleanup EXIT

# A real custom-format archive proves the restore preflight consumes redirected
# stdin. Without docker --interactive this exact check sees an empty stream.
docker run --detach --name "$dump_source" --network none \
  --env POSTGRES_DB=umami --env POSTGRES_USER=umami --env POSTGRES_PASSWORD=integration-only \
  "$image" >/dev/null
ready=false
for _ in {1..60}; do
  if docker exec "$dump_source" pg_isready --username=umami --dbname=umami >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
test "$ready" = true
docker exec "$dump_source" psql --username=umami --dbname=umami --set=ON_ERROR_STOP=1 \
  --command='create table restore_contract (id integer primary key); insert into restore_contract values (1)' \
  >/dev/null
docker exec "$dump_source" pg_dump --format=custom --username=umami --dbname=umami \
  >"$fixture_root/database.dump"
docker run --rm --interactive --network none --entrypoint pg_restore "$image" \
  --list <"$fixture_root/database.dump" >/dev/null

docker run --detach --name "$first" --network none "$image" sleep 120 >/dev/null
first_id="$(docker inspect --format '{{.Id}}' "$first")"
docker run --detach --name "$second" --network "container:$first_id" "$image" sleep 120 >/dev/null

test "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$first")" = none
test "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$second")" = "container:$first_id"
for container in "$first" "$second"; do
  test "$(docker exec "$container" sh -eu -c 'ls -A /sys/class/net')" = lo
  test "$(docker exec "$container" sh -eu -c 'wc -l < /proc/net/route')" = 1
  if docker exec "$container" sh -eu -c 'wget -q -T 2 -O /dev/null http://1.1.1.1' >/dev/null 2>&1; then
    echo 'loopback-only restore container unexpectedly reached the internet' >&2
    exit 1
  fi
done

echo 'restore network isolation passed: shared namespace exposes loopback only; no route or internet access'
