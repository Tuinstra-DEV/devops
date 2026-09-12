#!/usr/bin/env bash
set -euo pipefail

readonly image='docker.io/library/postgres:15-alpine@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b'
readonly operation="dev30-network-test-$PPID-$$"
readonly first="${operation}-db"
readonly second="${operation}-app"

cleanup() {
  docker rm --force "$second" "$first" >/dev/null 2>&1 || true
}
trap cleanup EXIT

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
