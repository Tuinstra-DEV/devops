# Consumer Migration Checklist

Step-by-step guide for integrating a consumer repository with the devops reusable workflows.

## Prerequisites

- Consumer repo hosted on GitHub under `marcel-tuinstra` (or an org with access).
- Docker installed locally for testing container builds.
- SSH access to the target deployment server.

## 1. Dockerfile

Create a `Dockerfile` in the consumer repo root. Use the template at
`templates/docker/nuxt-ssg-nginx.Dockerfile` as a starting point:

```dockerfile
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run generate

FROM nginx:1.27-alpine
COPY --from=builder /app/.output/public /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

Adjust the build command and output path to match your project.

## 2. Health Endpoint

Create a static file at `public/health` in your consumer repo containing `ok`:

```
ok
```

This gets included in the generated output and served by nginx automatically.

The CD workflow health check runs **via SSH** on the server, curling `localhost:<host-port>/health` directly. This means:
- No external DNS or reverse proxy configuration is needed for the health check to pass
- First-time deploys to new environments work immediately
- The health check verifies the container is actually serving traffic

## 3. GitHub Environments

Create two GitHub Environments in your consumer repo settings:

| Environment | Purpose |
|---|---|
| `staging` | Auto-deploys on push to `develop` |
| `production` | Auto-deploys on push to `main` |

## 4. Secrets and Variables (Environment-level)

The reusable CD workflow reads SSH configuration directly from each environment. Configure these **per environment**:

### Staging Environment

| Type | Name | Value |
|---|---|---|
| Secret | `SSH_PRIVATE_KEY` | SSH private key for the staging server |
| Variable | `SSH_HOST` | Hostname or IP of the staging server |

### Production Environment

| Type | Name | Value |
|---|---|---|
| Secret | `SSH_PRIVATE_KEY` | SSH private key for the production server |
| Variable | `SSH_HOST` | Hostname or IP of the production server |

> **Important:** These must be configured at the **environment level**, not repository level. The workflow sets `environment: staging` or `environment: production` on the deploy job, and GitHub resolves the vars/secrets from that specific environment context.

## 5. Caller Workflows

Copy the caller workflow templates from this repo into your consumer repo:

```bash
# From the consumer repo root:
mkdir -p .github/workflows

# Copy and customize:
cp <devops-repo>/templates/workflows/caller-cd-nuxt-staging.yml .github/workflows/deploy-staging.yml
cp <devops-repo>/templates/workflows/caller-cd-nuxt-production.yml .github/workflows/deploy-production.yml
```

Replace all `<YOUR-...>` placeholders with actual values for your project.

Note: The caller workflows do NOT pass SSH host or private key — these are read automatically from the environment configuration you set in step 4.

For CI (lint + typecheck + build):

```yaml
# .github/workflows/ci.yml
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  ci:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-ci.yml@v1
```

## 6. Compose Files

Add **two** compose files to your consumer repo root — one per environment:

**`docker-compose.staging.yml`** (staging port):
```yaml
services:
  <service-name>:
    image: ghcr.io/<org>/<repo>:latest
    ports:
      - "<staging-port>:80"
    restart: unless-stopped
```

**`docker-compose.production.yml`** (production port):
```yaml
services:
  <service-name>:
    image: ghcr.io/<org>/<repo>:latest
    ports:
      - "<production-port>:80"
    restart: unless-stopped
```

### Port Schema

Each project gets a pair of ports. Production ports start at 3000, staging at 3100:

| Project | Production | Staging |
|---|---|---|
| site-marcel | 3000 | 3100 |
| site-subtrack | 3001 | 3101 |
| airporttoday-nuxt | 3002 | 3102 |

The CD workflow automatically uploads the appropriate compose file to the server at `remote-path` before deploying. You do **not** need to manually place it on the server.

### Directory Structure on Server

Each environment deploys to its own subdirectory:

```
/mnt/ssd1000-01/projects/<project>/
├── staging/
│   └── docker-compose.staging.yml
└── production/
    └── docker-compose.production.yml
```

## 7. Server Preparation

On each target server:

1. Create the deploy user: `sudo adduser deploy`
2. Add the deploy user's SSH public key to `~/.ssh/authorized_keys`
3. Install Docker and Docker Compose
4. Ensure the deploy user can write to the `remote-path` directory (the workflow creates it via `mkdir -p` if it doesn't exist)

## 8. Branching Strategy

Use a two-branch model:

| Branch | Deploys to | Trigger |
|---|---|---|
| `develop` | Staging | Push |
| `main` | Production | Push |

Typical flow: work on feature branches, merge to `develop` for staging verification, then create a release PR to promote `develop` → `main` for production.

See `docs/workflows/branching-strategy.md` for the full branching model.

## 9. Release Workflows

Copy the release PR template into your consumer repo:

```bash
cp <devops-repo>/templates/workflows/caller-release-pr.yml .github/workflows/release.yml
```

Copy the release tag template into your consumer repo:

```bash
cp <devops-repo>/templates/workflows/caller-release-tag.yml .github/workflows/release-tag.yml
```

`release.yml` adds a **Create Release PR** workflow triggered via `workflow_dispatch` (manual "Run workflow" button in GitHub Actions). It creates a PR from `develop` → `main` with a version title (`release: vX.Y.Z`) and an auto-generated PR-based changelog. In `auto` mode, `feat` PR titles produce a minor bump, otherwise patch; use workflow input `major` for breaking releases.

`release-tag.yml` triggers on push to `main` and creates the same `vX.Y.Z` git tag when the merged PR title matches `release: vX.Y.Z`.

Git tags are used for release traceability. GitHub Releases are optional. The release PR remains the audit trail and the Docker image digest remains the rollback unit.

## 10. DNS and Reverse Proxy

- **DNS**: Add a wildcard A-record `*.<your-domain>` pointing to your server.
- **Staging URL**: `staging.<your-domain>` (e.g. `staging.marcel.tuinstra.dev`)
- **Production URL**: `<your-domain>` (e.g. `marcel.tuinstra.dev`)
- **Reverse proxy**: Configure Nginx Proxy Manager (or similar) to proxy each hostname to the corresponding local port with SSL.

## 11. Validation

After setup, verify end-to-end:

1. **CI**: Open a PR and confirm the reusable CI workflow runs and passes.
2. **Staging CD**: Push to `develop` and confirm staging deployment succeeds.
3. **Health check**: Verify the staging health URL returns HTTP 200.
4. **Production CD**: Push to `main` and confirm production deployment succeeds.
5. **Health check**: Verify the production health URL returns HTTP 200.

## 12. Rollback

If a deployment fails:

- The reusable CD workflow includes automatic rollback on health check failure.
- Manual rollback: SSH to the server and run `docker compose up -d` with the previous image digest.
- Pin caller workflow to a known-good commit SHA if a workflow regression is suspected.

## Troubleshooting

| Symptom | Likely Cause |
|---|---|
| `image not found` during deploy | GHCR token missing or insufficient permissions |
| SSH connection refused | SSH key not configured or wrong host variable |
| `SSH_HOST` or `SSH_PRIVATE_KEY` not found | Secrets/vars configured at repo level instead of environment level |
| Health check timeout | Health endpoint not reachable or returns non-200 |
| `dockerfile not found` | Missing `Dockerfile` in consumer repo root |

## Breaking Changes from Earlier Versions

### v1.0 → v1.1

- **Removed**: `ssh-host` input and `ssh-private-key` secret from workflow interface
- **Changed**: SSH configuration now read directly from environment-level `SSH_HOST` (variable) and `SSH_PRIVATE_KEY` (secret)
- **Action required**: Rename environment variables/secrets and remove ssh-host/ssh-private-key from caller workflows
