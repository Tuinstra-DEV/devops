# Workflow Contract: reusable-cd-vite-spa.yml

**Version:** v1  
**Status:** Stable  
**Last Updated:** 2026-03-13

## Purpose

Reusable CD workflow for deploying Vite SPA (single-page application) applications via Docker to a remote host. Includes security scanning with Trivy before deployment.

## Trigger

```yaml
on:
  workflow_call:
```

## Inputs

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `service-name` | string | Yes | - | Docker Compose service name on target host |
| `image-name` | string | Yes | - | Full GHCR image name without tag |
| `health-url` | string | No | `""` | Public health URL (informational) |
| `port` | number | No | `80` | Exposed service port |
| `environment` | string | Yes | - | GitHub Environment name (staging/production) |
| `workdir` | string | No | `.` | Project working directory |
| `dockerfile` | string | No | `Dockerfile` | Dockerfile path relative to repo root |
| `remote-path` | string | Yes | - | Deploy path on target host |
| `ssh-user` | string | Yes | - | SSH user for deploy host |
| `ssh-port` | number | No | `22` | SSH port |
| `compose-file` | string | No | `docker-compose.yml` | Compose file path |
| `build-args` | string | No | `""` | Newline-separated Docker build args |
| `trivy-severity` | string | No | `"CRITICAL,HIGH"` | Comma-separated severity levels to scan |
| `trivy-exit-code` | string | No | `"1"` | Exit code on findings (1 = fail, 0 = warn) |

## Outputs

None exposed to caller. Internal outputs:

| Output | Source Job | Description |
|--------|-----------|-------------|
| `image-ref` | `build-scan-push` | Immutable image reference with digest |

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
| `build-scan-push` | Build image, scan with Trivy, push to GHCR |
| `deploy` | Deploy image to remote host via SSH |

## Expected Behavior

### build-scan-push
1. Checkout code
2. Setup Docker Buildx
3. Login to GHCR
4. Build image locally (no push)
5. Run Trivy security scan
6. Upload SARIF results to GitHub Security tab
7. **Fail if CRITICAL/HIGH vulnerabilities found** (configurable)
8. Push image to GHCR only if scan passes
9. Output immutable image reference (digest)

### deploy
1. Checkout code
2. Configure SSH with environment secrets
3. Upload compose file to remote host
4. Deploy using digest-pinned image reference
5. Run health check on localhost (via SSH tunnel)

## Security Scanning

The workflow uses [Trivy](https://trivy.dev/) to scan Docker images for vulnerabilities before deployment.

**Default behavior:**
- Scans for `CRITICAL` and `HIGH` severity vulnerabilities
- Fails the pipeline if any are found (exit code 1)
- Ignores unfixed vulnerabilities (only actionable findings)
- Uploads results to GitHub Security tab (SARIF format)

**Customization:**
```yaml
with:
  trivy-severity: "CRITICAL"        # Only fail on CRITICAL
  trivy-exit-code: "0"              # Warn only, don't fail
```

## Health Check Contract

The deployed container must:
- Expose a `/health` endpoint on the configured `port`
- Return HTTP 200 within 180 seconds of container start
- Health check runs via SSH on localhost, not public URL

## Breaking Change Policy

See [versioning-policy.md](../versioning-policy.md) for major version upgrade procedures.

Changes that require a major version bump:
- Removing or renaming existing inputs
- Changing required secrets or environment variables
- Modifying health check behavior
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
    uses: marcel-tuinstra/devops/.github/workflows/reusable-cd-vite-spa.yml@v1
    secrets:
      SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}
    with:
      service-name: my-site
      image-name: ghcr.io/my-org/my-site
      environment: production
      remote-path: /opt/my-site
      ssh-user: deploy
```
