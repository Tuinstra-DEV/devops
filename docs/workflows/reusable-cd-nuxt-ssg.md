# Reusable CD Workflow for Nuxt SSG

Workflow: `.github/workflows/reusable-cd-nuxt-ssg.yml`

## What it does

- Builds static Nuxt output into an nginx runtime image.
- Pushes image to GHCR.
- In restricted mode, deploys through the host's fixed `deploy <application>`
  SSH command as the dedicated `deploy` user; it does not upload compose files
  or stream shell scripts.
- The root-owned host compose contract must already be staged and contains the
  immutable image reference to deploy.
- Verifies deployment health via SSH by curling `localhost:<host-port>/health` directly on the server (no external DNS/proxy required).

## Required inputs

- `service-name` — Docker Compose service name on target host
- `image-name` — Full GHCR image name without tag
- `environment` — GitHub Environment name (`production`)
- `remote-path` — Deploy path on target host (compose file is uploaded here automatically)
- `ssh-user` — SSH user for deploy host

## Optional inputs

- `health-url` — Public health URL (informational only, not used for health checks)
- `port` — Container-internal port (default: `80`, used to resolve the mapped host port)
- `compose-file` — Compose file path relative to consumer repo root (default: `docker-compose.yml`)
- `dockerfile` — Dockerfile path relative to consumer repo root (default: `Dockerfile`)
- `ssh-port` — SSH port (default: `22`)
- `workdir` — Project working directory (default: `.`)

## Required environment configuration

The deploy job runs within the specified GitHub Environment and reads SSH configuration directly from environment-level vars/secrets:

| Type | Name | Description |
|---|---|---|
| Variable | `SSH_HOST` | Hostname or IP of the deployment target |
| Secret | `SSH_PRIVATE_KEY` | SSH private key for authentication |

Configure these on the production environment in your repository settings.

## Why environment-level?

GitHub Actions resolves `vars` and `secrets` at the workflow level where they're referenced. By setting the `environment` on the deploy job and reading SSH config directly within that job's steps, GitHub correctly resolves production-scoped values without relying on repository-level secrets.

## Example callers

- Production example: `templates/workflows/caller-cd-nuxt-production.yml`

## Migration from v1.0 (breaking change)

v1.1 removes the `ssh-host` input and `ssh-private-key` secret from the workflow interface. Update your caller workflows:

**Before (v1.0):**
```yaml
jobs:
  deploy:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-cd-nuxt-ssg.yml@v1
    with:
      ssh-host: ${{ vars.SSH_PRODUCTION_HOST }}
    secrets:
      ssh-private-key: ${{ secrets.SSH_PRODUCTION_PRIVATE_KEY }}
```

**After (v1.1):**
```yaml
jobs:
  deploy:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-cd-nuxt-ssg.yml@v1
    secrets:
      SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}
    # No ssh-host input. SSH_HOST is read from environment variables.
```

Rename your environment variables/secrets:
- `SSH_PRODUCTION_HOST` → `SSH_HOST` (in production environment)
- `SSH_PRODUCTION_PRIVATE_KEY` → `SSH_PRIVATE_KEY` (in production environment)
