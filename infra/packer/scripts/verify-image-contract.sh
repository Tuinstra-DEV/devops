#!/usr/bin/env bash
set -euo pipefail

required_commands=(
  composer
  corepack
  curl
  docker
  git
  jq
  node
  npm
  npx
  php8.3
  php8.4
  trivy
)

for command in "${required_commands[@]}"; do
  command -v "$command" >/dev/null || {
    echo "image contract missing command: $command" >&2
    exit 1
  }
done

docker buildx version
docker compose version
node --version | grep -Eq '^v24\.'
php8.3 --version | grep -Eq '^PHP 8\.3\.'
php8.4 --version | grep -Eq '^PHP 8\.4\.'
composer --version
playwright --version
trivy --version
test -x /opt/actions-runner/run.sh
test -x /usr/local/bin/run-jit-runner
test "$(stat -c '%U:%G:%a' /opt/actions-runner)" = "root:root:755"
unsafe_runner_entry="$(find /opt/actions-runner -xdev \
  \( -path /opt/actions-runner/_diag -o -path /opt/actions-runner/_work \) -prune -o \
  \( \( ! -user root -o ! -group root \) -o \( \( -type f -o -type d \) -perm /022 \) \) \
  -print -quit)"
test -z "$unsafe_runner_entry" || {
  echo "image contract found mutable non-runtime runner entry: $unsafe_runner_entry" >&2
  exit 1
}
test -d /opt/ms-playwright
test -s /etc/ci-runner-image-manifest

echo "immutable runner image contract passed"
