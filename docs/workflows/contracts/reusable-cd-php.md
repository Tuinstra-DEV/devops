# Workflow Contract: reusable-cd-php.yml

**Version:** v1  
**Status:** Stable  
**Last Updated:** 2026-02-10

## Purpose

Reusable CD workflow for deploying PHP/Symfony applications via Docker to a remote host. Builds separate PHP-FPM and nginx images, includes security scanning with Trivy, and supports Doctrine migrations and Messenger worker restarts.

## Trigger

```yaml
on:
  workflow_call:
```

## Inputs

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `service-name` | string | Yes | - | Docker Compose PHP service name on target host |
| `nginx-service-name` | string | No | `"nginx"` | Docker Compose nginx service name on target host |
| `image-name` | string | Yes | - | Full GHCR image name without tag (used for both php and nginx images) |
| `health-url` | string | No | `""` | Public health URL (informational only) |
| `health-path` | string | No | `"/health"` | Path checked on localhost during health check |
| `health-timeout-seconds` | number | No | `180` | Maximum seconds to wait for a healthy response |
| `port` | number | No | `80` | Exposed nginx service port (internal container port) |
| `environment` | string | Yes | - | GitHub Environment name (staging/production) |
| `workdir` | string | No | `.` | Project working directory |
| `dockerfile` | string | No | `Dockerfile` | Dockerfile path relative to repo root |
| `remote-path` | string | Yes | - | Deploy path on target host containing compose file |
| `ssh-user` | string | Yes | - | SSH user for deploy host |
| `ssh-port` | number | No | `22` | SSH port |
| `compose-file` | string | No | `docker-compose.yml` | Compose file path relative to repo root |
| `build-args` | string | No | `""` | Newline-separated Docker build args |
| `trivy-severity` | string | No | `"CRITICAL,HIGH"` | Comma-separated severity levels to scan |
| `trivy-exit-code` | string | No | `"1"` | Exit code on findings (1 = fail, 0 = warn) |
| `run-migrations` | boolean | No | `false` | Run Doctrine migrations after deploy |
| `restart-worker` | boolean | No | `false` | Restart messenger worker service after deploy |
| `worker-services` | string | No | `""` | Comma-separated worker services to pin to PHP image |
| `pre-deploy-cleanup` | string | No | `"none"` | Optional cleanup before deploy (`none` or `down-remove-orphans`) |

## Outputs

None exposed to caller. Internal outputs:

| Output | Source Job | Description |
|--------|-----------|-------------|
| `php-image-ref` | `build-scan-push` | Immutable PHP image reference with digest |
| `nginx-image-ref` | `build-scan-push` | Immutable nginx image reference with digest |

## Required Secrets

These must be configured in the GitHub Environment:

| Secret | Description |
|--------|-------------|
| `SSH_PRIVATE_KEY` | Ed25519 private key for deploy host access |

## Required Environment Variables

| Variable | Description |
|----------|-------------|
| `SSH_HOST` | Target host for deployment |

## Required Permissions

```yaml
permissions:
  contents: read
  packages: write
  security-events: write
```

## Jobs

| Job | Description |
|-----|-------------|
| `build-scan-push` | Build PHP and nginx images, scan with Trivy, push to GHCR |
| `deploy` | Deploy images to remote host via SSH |

## Expected Behavior

### build-scan-push
1. Checkout code
2. Setup Docker Buildx
3. Login to GHCR
4. Build PHP image (`php_prod` target) locally
5. Run Trivy security scan on PHP image
6. Upload PHP SARIF results to GitHub Security tab
7. **Fail if CRITICAL/HIGH vulnerabilities found** (configurable)
8. Push PHP image to GHCR with tags: `image:sha`, `image:latest`
9. Build nginx image (`nginx_prod` target) locally
10. Run Trivy security scan on nginx image
11. Push nginx image to GHCR with tags: `image:nginx-sha`, `image:nginx-latest`
12. Output immutable image references (digests)

### deploy
1. Checkout code
2. Configure SSH with environment secrets
3. Upload compose file to remote host
4. Create override file with pinned image digests for php, nginx, and worker services
5. Pull and deploy using docker compose
6. **Optionally run Doctrine migrations** (if `run-migrations: true`)
7. **Optionally restart worker service** (if `restart-worker: true`)
8. Run health check on localhost (via SSH tunnel)

## Dockerfile Requirements

The Dockerfile must define these targets:

```dockerfile
# PHP-FPM production image
FROM php:8.3-fpm-alpine AS php_prod
# ... your PHP-FPM setup

# nginx production image (copies static assets from php_prod)
FROM nginx:1.27-alpine AS nginx_prod
COPY docker/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=php_prod /app/public /app/public
```

## Image Tagging

| Image | Tags |
|-------|------|
| PHP | `image:latest`, `image:<sha>` |
| nginx | `image:nginx-latest`, `image:nginx-<sha>` |

Deployments use immutable digest references, not tags.

## Security Scanning

The workflow uses [Trivy](https://trivy.dev/) to scan Docker images for vulnerabilities before deployment.

**Default behavior:**
- Scans both PHP and nginx images
- Scans for `CRITICAL` and `HIGH` severity vulnerabilities
- Fails the pipeline if any are found (exit code 1)
- Ignores unfixed vulnerabilities (only actionable findings)
- Uploads PHP results to GitHub Security tab (SARIF format)

**Customization:**
```yaml
with:
  trivy-severity: "CRITICAL"        # Only fail on CRITICAL
  trivy-exit-code: "0"              # Warn only, don't fail
```

## Health Check Contract

The deployed container must:
- Expose a health endpoint on the nginx service (default `/health`, configurable via `health-path`)
- Return HTTP 200 within configured timeout (default 180 seconds)
- Health check runs via SSH on localhost, not public URL

## Doctrine Migrations

When `run-migrations: true`:
1. Waits 5 seconds for PHP container to be ready
2. Runs `php bin/console doctrine:migrations:migrate --no-interaction --allow-no-migration`
3. Fails deployment if migrations fail

## Worker Service

When `restart-worker: true`:
- Restarts the service specified by `worker-service` input
- Default service name: `worker`
- Useful for Symfony Messenger consumers

## Breaking Change Policy

See [versioning-policy.md](../versioning-policy.md) for major version upgrade procedures.

Changes that require a major version bump:
- Removing or renaming existing inputs
- Changing required secrets or environment variables
- Modifying Dockerfile target names (`php_prod`, `nginx_prod`)
- Changing health check behavior
- Changing image tagging strategy
- Changing default security scan behavior

## Example Caller

```yaml
name: Deploy Production

on:
  push:
    branches: [main]

jobs:
  deploy:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-cd-php.yml@v1
    secrets:
      SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}
    with:
      service-name: php
      nginx-service-name: nginx
      image-name: ghcr.io/my-org/my-app
      environment: production
      remote-path: /opt/my-app/production
      ssh-user: deploy
      compose-file: docker-compose.production.yml
      run-migrations: true
      restart-worker: true
```

## Example Compose File

```yaml
services:
  php:
    image: ghcr.io/my-org/my-app:latest
    environment:
      DATABASE_URL: postgresql://user:pass@postgres:5432/app
    depends_on:
      - postgres

  nginx:
    image: ghcr.io/my-org/my-app:nginx-latest
    ports:
      - "8000:80"
    depends_on:
      - php

  worker:
    image: ghcr.io/my-org/my-app:latest
    command: php bin/console messenger:consume async --time-limit=3600
    depends_on:
      - php
      - postgres

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: app
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```
