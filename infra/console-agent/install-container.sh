#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

INSTALL_DIR="${INSTALL_DIR:-/opt/console-agent-container}"
COMPOSE_FILE="${COMPOSE_FILE:-${INSTALL_DIR}/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${INSTALL_DIR}/.env}"

HUB_URL="https://console.tuinstra.dev"
HOST_SLUG=""
AGENT_ID=""
ENVIRONMENT="production"
IMAGE=""
BACKUP_ROOT="/var/backups"
DOCKER_DATA_ROOT="/var/lib/docker"
INTERVAL_SECONDS="300"
FORCE=0
START=1
TOKEN_FROM_STDIN=0
TOKEN=""

usage() {
    cat <<'USAGE'
Install the Console Agent container stack on a Linux Docker host.

Usage:
  printf '%s\n' '<token>' | sudo ./agent/install-container.sh --host-slug web-01 --token-stdin

Options:
  --hub-url URL          Console hub URL. Defaults to https://console.tuinstra.dev.
  --host-slug SLUG      Runtime host slug, for example web-01. Required.
  --agent-id ID         Agent id. Defaults to agent:<host-slug>.
  --environment NAME    Host environment label. Defaults to production.
  --image IMAGE         Required immutable agent image ID or repository digest.
  --backup-root PATH    Host backup root mounted read-only. Defaults to /var/backups.
  --docker-data-root PATH
                        Host Docker data-root mounted read-only. Defaults to /var/lib/docker.
  --interval SECONDS    Push interval. Defaults to 300.
  --token-stdin         Read token from stdin.
  --force               Overwrite an existing install directory.
  --no-start            Write files but do not start the compose stack.
  -h, --help            Show this help.

The installed stack contains:
  - docker-proxy: private GET-only Docker API proxy
  - agent: outbound inventory reporter

It publishes no inbound ports. The Symfony hub app never gets Docker socket access.
Docker logs use the local driver with five 10 MB files per container by default.
Override with DOCKER_LOG_MAX_SIZE and DOCKER_LOG_MAX_FILE before installation.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hub-url)
            HUB_URL="${2:-}"
            shift 2
            ;;
        --host-slug)
            HOST_SLUG="${2:-}"
            shift 2
            ;;
        --agent-id)
            AGENT_ID="${2:-}"
            shift 2
            ;;
        --environment)
            ENVIRONMENT="${2:-}"
            shift 2
            ;;
        --image)
            IMAGE="${2:-}"
            shift 2
            ;;
        --backup-root)
            BACKUP_ROOT="${2:-}"
            shift 2
            ;;
        --docker-data-root)
            DOCKER_DATA_ROOT="${2:-}"
            shift 2
            ;;
        --interval)
            INTERVAL_SECONDS="${2:-}"
            shift 2
            ;;
        --token-stdin)
            TOKEN_FROM_STDIN=1
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --no-start)
            START=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

slug() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_.-]+/-/g; s/^-+//; s/-+$//'
}

stack_name() {
    printf 'console-agent-%s' "$1" | sed -E 's/[^a-zA-Z0-9_-]+/_/g'
}

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Console Agent container installer is Linux-only." >&2
    exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root, for example with sudo." >&2
    exit 1
fi

require_command docker

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin is required: docker compose version failed." >&2
    exit 1
fi

if [[ "${TOKEN_FROM_STDIN}" -eq 1 ]]; then
    IFS= read -r TOKEN
fi

HOST_SLUG="$(slug "${HOST_SLUG}")"
if [[ -z "${HOST_SLUG}" ]]; then
    echo "--host-slug is required." >&2
    exit 1
fi

if [[ -z "${AGENT_ID}" ]]; then
    AGENT_ID="agent:${HOST_SLUG}"
fi

if [[ -z "${TOKEN}" ]]; then
    echo "Agent token is required via --token-stdin." >&2
    exit 1
fi

if [[ "${TOKEN}" =~ [[:space:]] ]]; then
    echo "Agent token must not contain whitespace." >&2
    exit 1
fi

if [[ ! "${INTERVAL_SECONDS}" =~ ^[0-9]+$ || "${INTERVAL_SECONDS}" -lt 60 ]]; then
    echo "--interval must be an integer of at least 60 seconds." >&2
    exit 1
fi

if [[ -e "${INSTALL_DIR}" && "${FORCE}" -ne 1 ]]; then
    echo "${INSTALL_DIR} already exists. Re-run with --force to overwrite generated files." >&2
    exit 1
fi

if [[ ! "$IMAGE" =~ ^(sha256:[a-f0-9]{64}|[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64})$ ]]; then
    echo "An immutable image ID or repository digest is required." >&2
    exit 64
fi
if [[ "$HUB_URL" != https://console.tuinstra.dev ||
      ! "$AGENT_ID" =~ ^agent:[a-z0-9-]+$ || "$ENVIRONMENT" != production ||
      "$INSTALL_DIR" != /var/www/_platform/console-agent ||
      "$DOCKER_DATA_ROOT" != /var/lib/docker || "$BACKUP_ROOT" != /var/backups ]]; then
    echo "Installer values differ from the reviewed production policy." >&2
    exit 64
fi
if [[ ! "${DOCKER_LOG_MAX_SIZE:-10m}" =~ ^[0-9]+[kmg]$ ||
      ! "${DOCKER_LOG_MAX_FILE:-5}" =~ ^[1-9][0-9]?$ ]]; then
    echo "Invalid Docker log bounds." >&2
    exit 64
fi
install -d -m 0755 /var/lib/tuinstra/console-capacity
if [[ "$(stat -c %d /var/lib/tuinstra/console-capacity)" != "$(stat -c %d /var/lib/docker)" ||
      "$(stat -c %d /var/lib/tuinstra/console-capacity)" != "$(stat -c %d /)" ]]; then
    echo "Capacity marker must share the root and Docker filesystems; review mounts first." >&2
    exit 64
fi
install -d -m 0750 "${INSTALL_DIR}"

cat > "${ENV_FILE}.tmp" <<ENV
CONSOLE_AGENT_TOKEN=${TOKEN}
CONSOLE_AGENT_HUB_URL=${HUB_URL%/}
CONSOLE_AGENT_HOST_SLUG=${HOST_SLUG}
CONSOLE_AGENT_ID=${AGENT_ID}
CONSOLE_AGENT_ENVIRONMENT=${ENVIRONMENT}
CONSOLE_AGENT_INTERVAL_SECONDS=${INTERVAL_SECONDS}
CONSOLE_AGENT_COLLECT_DOCKER=true
CONSOLE_AGENT_COLLECT_HOST_CAPACITY=true
CONSOLE_AGENT_COLLECT_BACKUPS=false
CONSOLE_AGENT_COLLECT_LISTENERS=false
CONSOLE_AGENT_PROC_PATH=/host/proc
CONSOLE_AGENT_DOCKER_DATA_ROOT_PATH=/host/var/lib/docker
CONSOLE_AGENT_CAPACITY_MOUNT_ID=root
CONSOLE_AGENT_CAPACITY_MOUNT_PATH=/host/rootfs
CONSOLE_AGENT_CAPACITY_MOUNT_HOST_PATH=/
CONSOLE_AGENT_IMAGE=${IMAGE}
CONSOLE_AGENT_BACKUP_ROOT=${BACKUP_ROOT}
CONSOLE_AGENT_DOCKER_DATA_ROOT=${DOCKER_DATA_ROOT}
CONSOLE_AGENT_STACK_NAME=$(stack_name "${HOST_SLUG}")
DOCKER_LOG_MAX_SIZE=${DOCKER_LOG_MAX_SIZE:-10m}
DOCKER_LOG_MAX_FILE=${DOCKER_LOG_MAX_FILE:-5}
ENV
chmod 0600 "${ENV_FILE}.tmp"
mv "${ENV_FILE}.tmp" "${ENV_FILE}"

cat > "${COMPOSE_FILE}" <<'YAML'
name: ${CONSOLE_AGENT_STACK_NAME}

x-console-agent-logging: &console_agent_logging
  driver: local
  options:
    max-size: ${DOCKER_LOG_MAX_SIZE:-10m}
    max-file: "${DOCKER_LOG_MAX_FILE:-5}"

services:
  docker-proxy:
    image: ${CONSOLE_AGENT_IMAGE}
    command: php -S 0.0.0.0:2375 docker/agent/docker-socket-proxy.php
    environment:
      HOME: /tmp
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    read_only: true
    tmpfs:
      - /tmp
      - /run
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
    logging: *console_agent_logging

  agent:
    image: ${CONSOLE_AGENT_IMAGE}
    command: sh docker/agent/run.sh
    env_file:
      - .env
    environment:
      DOCKER_HOST: tcp://docker-proxy:2375
      HOME: /tmp
    volumes:
      - /proc/loadavg:/host/proc/loadavg:ro
      - /proc/cpuinfo:/host/proc/cpuinfo:ro
      - /proc/meminfo:/host/proc/meminfo:ro
      - /var/lib/tuinstra/console-capacity:/host/rootfs:ro
      - /var/lib/tuinstra/console-capacity:/host/var/lib/docker:ro
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    depends_on:
      - docker-proxy
    restart: unless-stopped
    logging: *console_agent_logging
YAML

echo "Console Agent container files written:"
echo "  ${COMPOSE_FILE}"
echo "  ${ENV_FILE}"

if [[ "${START}" -eq 1 ]]; then
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d
    echo "Console Agent container stack started."
    echo "Recent logs:"
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs --tail 20 agent
else
    echo "Not started. Start later with:"
    echo "  docker compose --env-file '${ENV_FILE}' -f '${COMPOSE_FILE}' up -d"
fi
