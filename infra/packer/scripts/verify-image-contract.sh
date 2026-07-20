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
test -d /opt/ms-playwright
test -s /etc/ci-runner-image-manifest

echo "immutable runner image contract passed"
