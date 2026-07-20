#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_VERSION:?RUNNER_VERSION is required}"
: "${RUNNER_SHA256:?RUNNER_SHA256 is required}"
: "${DOCKER_KEY_SHA256:?DOCKER_KEY_SHA256 is required}"
: "${DOCKER_CE_VERSION:?DOCKER_CE_VERSION is required}"
: "${DOCKER_CLI_VERSION:?DOCKER_CLI_VERSION is required}"
: "${CONTAINERD_VERSION:?CONTAINERD_VERSION is required}"
: "${BUILDX_VERSION:?BUILDX_VERSION is required}"
: "${COMPOSE_VERSION:?COMPOSE_VERSION is required}"
: "${NODE_KEY_SHA256:?NODE_KEY_SHA256 is required}"
: "${NODE_VERSION:?NODE_VERSION is required}"
: "${TRIVY_KEY_SHA256:?TRIVY_KEY_SHA256 is required}"
: "${TRIVY_VERSION:?TRIVY_VERSION is required}"
: "${PHP_KEY_SHA256:?PHP_KEY_SHA256 is required}"
: "${PHP83_VERSION:?PHP83_VERSION is required}"
: "${PHP84_VERSION:?PHP84_VERSION is required}"
: "${COMPOSER_VERSION:?COMPOSER_VERSION is required}"
: "${COMPOSER_SHA384:?COMPOSER_SHA384 is required}"
: "${PLAYWRIGHT_VERSION:?PLAYWRIGHT_VERSION is required}"

if [[ "$(dpkg --print-architecture)" != amd64 ]]; then
  echo "This image contract supports Ubuntu 24.04 amd64 only" >&2
  exit 1
fi

install_key() {
  local url=$1 expected=$2 destination=$3 temporary
  temporary=$(mktemp)
  curl --fail --silent --show-error --location "$url" --output "$temporary"
  printf '%s  %s\n' "$expected" "$temporary" | sha256sum --check --strict
  sudo gpg --batch --yes --dearmor --output "$destination" "$temporary"
  sudo chmod 0644 "$destination"
  rm -f "$temporary"
}

sudo install -d -m 0755 /etc/apt/keyrings
install_key https://download.docker.com/linux/ubuntu/gpg "$DOCKER_KEY_SHA256" /etc/apt/keyrings/docker.gpg
install_key https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key "$NODE_KEY_SHA256" /etc/apt/keyrings/nodesource.gpg
install_key https://aquasecurity.github.io/trivy-repo/deb/public.key "$TRIVY_KEY_SHA256" /etc/apt/keyrings/trivy.gpg
install_key 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x14AA40EC0831756F' "$PHP_KEY_SHA256" /etc/apt/keyrings/ondrej-php.gpg

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.gpg
EOF
sudo tee /etc/apt/sources.list.d/nodesource.sources >/dev/null <<'EOF'
Types: deb
URIs: https://deb.nodesource.com/node_24.x
Suites: nodistro
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/nodesource.gpg
EOF
sudo tee /etc/apt/sources.list.d/trivy.sources >/dev/null <<'EOF'
Types: deb
URIs: https://aquasecurity.github.io/trivy-repo/deb
Suites: generic
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/trivy.gpg
EOF
sudo tee /etc/apt/sources.list.d/ondrej-php.sources >/dev/null <<'EOF'
Types: deb
URIs: https://ppa.launchpadcontent.net/ondrej/php/ubuntu
Suites: noble
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/ondrej-php.gpg
EOF

sudo apt-get update
sudo apt-get install --yes --no-install-recommends \
  "docker-ce=${DOCKER_CE_VERSION}" \
  "docker-ce-cli=${DOCKER_CLI_VERSION}" \
  "containerd.io=${CONTAINERD_VERSION}" \
  "docker-buildx-plugin=${BUILDX_VERSION}" \
  "docker-compose-plugin=${COMPOSE_VERSION}" \
  "nodejs=${NODE_VERSION}" \
  "trivy=${TRIVY_VERSION}" \
  "php8.3-cli=${PHP83_VERSION}" "php8.3-curl=${PHP83_VERSION}" \
  "php8.3-mbstring=${PHP83_VERSION}" "php8.3-xml=${PHP83_VERSION}" "php8.3-zip=${PHP83_VERSION}" \
  "php8.4-cli=${PHP84_VERSION}" "php8.4-curl=${PHP84_VERSION}" \
  "php8.4-mbstring=${PHP84_VERSION}" "php8.4-xml=${PHP84_VERSION}" "php8.4-zip=${PHP84_VERSION}"

sudo corepack enable
sudo npm install --global --ignore-scripts "playwright@${PLAYWRIGHT_VERSION}"
sudo /usr/bin/env PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright playwright install --with-deps chromium
sudo chmod -R a+rX /opt/ms-playwright

composer_installer=/tmp/composer-setup.php
curl --fail --silent --show-error --location https://getcomposer.org/installer --output "$composer_installer"
printf '%s  %s\n' "$COMPOSER_SHA384" "$composer_installer" | sha384sum --check --strict
sudo php "$composer_installer" --version="$COMPOSER_VERSION" --install-dir=/usr/local/bin --filename=composer
rm -f "$composer_installer"

archive="/tmp/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
curl --fail --silent --show-error --location \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
  --output "$archive"
printf '%s  %s\n' "$RUNNER_SHA256" "$archive" | sha256sum --check --strict

sudo useradd --system --user-group --create-home --home-dir /opt/actions-runner --shell /usr/sbin/nologin ci-runner
sudo usermod --append --groups docker ci-runner
sudo tar --extract --gzip --file "$archive" --directory /opt/actions-runner
sudo /opt/actions-runner/bin/installdependencies.sh
sudo install -d -o ci-runner -g ci-runner -m 0700 /opt/actions-runner/_diag /opt/actions-runner/_work /run/ci-runner
sudo chown -R root:root /opt/actions-runner
sudo chown ci-runner:ci-runner /opt/actions-runner/_diag /opt/actions-runner/_work
sudo install -o root -g root -m 0644 /tmp/ci-runner-job.service /etc/systemd/system/ci-runner-job.service
sudo install -o root -g root -m 0755 /tmp/run-jit-runner.sh /usr/local/bin/run-jit-runner
sudo systemctl daemon-reload
sudo systemctl enable docker.service

sudo tee /etc/ci-runner-image-manifest >/dev/null <<EOF
runner=${RUNNER_VERSION}
docker_ce=${DOCKER_CE_VERSION}
docker_cli=${DOCKER_CLI_VERSION}
containerd=${CONTAINERD_VERSION}
buildx=${BUILDX_VERSION}
compose=${COMPOSE_VERSION}
node=${NODE_VERSION}
trivy=${TRIVY_VERSION}
php83=${PHP83_VERSION}
php84=${PHP84_VERSION}
composer=${COMPOSER_VERSION}
playwright=${PLAYWRIGHT_VERSION}
EOF
sudo chmod 0444 /etc/ci-runner-image-manifest

rm -f "$archive"
