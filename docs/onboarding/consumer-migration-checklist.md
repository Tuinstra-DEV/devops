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
FROM node:24-alpine AS builder
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

Create the production GitHub Environment in your consumer repo settings:

| Environment | Purpose |
|---|---|
| `production` | Auto-deploys on push to `main` |

## 4. Secrets and Variables (Environment-level)

The reusable CD workflow reads SSH configuration directly from the production environment.

### Production Environment

| Type | Name | Value |
|---|---|---|
| Secret | `SSH_PRIVATE_KEY` | SSH private key for the production server |
| Variable | `SSH_HOST` | DNS hostname of the production server |
| Variable (PHP DEV-51 contract) | `SSH_HOST_ED25519_FINGERPRINT` | Independently approved ED25519 `SHA256:` fingerprint; mandatory when adopting the revised PHP workflow |

> **Important:** These must be configured at the **environment level**, not repository level. The workflow sets `environment: production` on the deploy job, and GitHub resolves the vars/secrets from that environment context.

## 5. Caller Workflows

Copy the caller workflow templates from this repo into your consumer repo:

```bash
# From the consumer repo root:
mkdir -p .github/workflows

# Copy and customize:
cp <devops-repo>/templates/workflows/caller-cd-nuxt-production.yml .github/workflows/deploy-production.yml
```

Replace all `<YOUR-...>` placeholders with actual values for your project.

Note: The caller workflows do NOT pass SSH host or private key — these are read automatically from the environment configuration you set in step 4.

### PHP deployment host identity (DEV-51)

The revised PHP workflow also reads `SSH_HOST_ED25519_FINGERPRINT` from its selected protected Environment. Vite/Nuxt workflows are outside DEV-51 and do not gain this check from this documentation. Missing or invalid fingerprint, failed/empty/ambiguous scan, or a key mismatch blocks PHP deployment before remote actions.

1. Obtain the public ED25519 fingerprint through an independently trusted server console or already verified administrative session (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256`). Never use an unverified scan as the source of the expected fingerprint.
2. Review and configure the protected Environment variable. Preserve the configured DNS hostname and SSH port; do not substitute an internal IP.
3. Validate the reviewed immutable workflow SHA in a safe hosted canary, including a rejected mismatched pin. Local synthetic harness results do not prove hosted permissions or production access.
4. Update the consumer workflow to the approved full SHA in a separate reviewed consumer change. Notify's owning Story is NTF-2.
5. Treat the mandatory variable as a breaking contract migration requiring the next immutable major release. Do not move an existing tag or silently update consumers.

If deployment rejects host identity, stop and investigate the expected DNS/port, protected variable and independently confirmed host-key changes. Keep strict checking enabled. Preserve deployed images, schema and data while correcting the configuration or adopting a reviewed forward fix; an older workflow without this check is not evidence of restored host trust.

For CI (lint + typecheck + build):

```yaml
# .github/workflows/ci.yml
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  ci:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-ci.yml@<APPROVED_40_CHARACTER_COMMIT_SHA>
```

Replace the placeholder with a reviewed, immutable full commit SHA. Production
workflow callers must follow the
[pipeline security policy](../security/pipeline-security-policy.md); do not pin
them to a moving major tag.

## 6. Compose Files

Add a production compose file to your consumer repo root:

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

Each project gets a production host port:

| Project | Production |
|---|---|
| site-marcel | 3000 |
| site-subtrack | 3002 |
| airporttoday-nuxt | 3003 |

The CD workflow automatically uploads the appropriate compose file to the server at `remote-path` before deploying. You do **not** need to manually place it on the server.

### Directory Structure on Server

Production deploys to its own project directory:

```
/mnt/ssd1000-01/projects/<project>/
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

Use a mainline production model:

| Branch | Deploys to | Trigger |
|---|---|---|
| `main` | Production | Push |

Typical flow: work on feature branches, open a PR to `main`, and merge when CI and review pass.

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

## 10. Gate Assurance Migration

Copy the Gate baseline workflow template into your consumer repo:

```bash
cp <devops-repo>/templates/workflows/caller-gate-baseline.yml .github/workflows/gate-baseline.yml
```

Read the [Gate baseline and assurance standard](../standards/gate-baseline.md)
and the normative
[Gate assurance contract](../workflows/contracts/gate-assurance-v1.md). Use
[`contracts/gate-assurance/v1/inventory.json`](../../contracts/gate-assurance/v1/inventory.json)
and [`schema.json`](../../contracts/gate-assurance/v1/schema.json) to determine
scope and validate inventory changes. Configure application observability from
[`templates/gate/observability.example.json`](../../templates/gate/observability.example.json).

All assurance facilities are planned; this checklist does not enroll a repo or
make a check required. The Gate baseline workflow reports repository
file/configuration presence only. It does not scan for security findings or
prove end-to-end operation.

### Gate PR Security: all 13 repositories

The security rollout covers `gate`, `devops`, `notify`, `console`, `status`,
`tracker`, `wodiq-app`, `wodiq-platform`, `openairco`, `marcel-site`,
`tuinstra-site`, `wodiq-site`, and `openairco-site`. For each repository:

- Run a green pilot and a red pilot against ordinary and fork/bot pull requests
  before requiring `Gate PR Security` on `main` and `develop` where present.
- Compare the exact base and head revisions with matching scanner versions,
  rules, and advisory snapshots. New or worsened high/critical findings block;
  old debt stays visible. Exception expiry by itself does not make old debt new.
- Use trusted policy without executing pull request scripts, plugins, or
  configuration. Scans are deterministic and use no AI. Missing tools,
  timeouts, malformed output, or incomplete coverage fail closed.
- Publish through the dedicated GitHub App and OIDC from an approved,
  full-SHA-pinned reusable workflow. Keep publisher credentials separate from
  Gate Heal credentials and retain stricter existing checks.

### Gate Observability at production promotion: eight applications

Gate Observability applies at production promotion only for `gate`, `notify`,
`console`, `status`, `tracker`, `wodiq-app`, `wodiq-platform`, and `openairco`.
It excludes `devops` and the four site repositories. Keep `Gate Observability`
distinct from `Gate PR Security`.

- New applications meet the Sentry standard before their first production
  release. Existing applications migrate and prove coverage before enforcement.
- Each application owns its runtime instrumentation. Send minimal, redacted
  diagnostics only; do not send replay data, content, or user identifiers.
  Unknown components or sources require human review.
- Probe the verification environment during onboarding and relevant integration
  changes. Every release verifies configuration, source maps, uploaded
  artifacts, symbols, and evidence. Verify production configuration separately
  and do not make release success depend synchronously on Sentry availability.
- Roll back to a previously verified artifact when needed. An ordinary rollback
  does not require an emergency bypass. An emergency bypass requires a human
  decision with the reason and exact version recorded.

### Gate Heal QA: automatically generated fix PRs only

`Gate Heal QA` applies only to automatically generated fix PRs in the eight
applications above. Preserve Gate's existing QA checks and controls. Automatic
fix generation remains disabled; per-stack activation follows migration, with
human approval still required for merges. Normal observability migration,
production promotion, and application releases do not wait for a Gate Heal QA
pilot.

Do not make checks required until both green and red pilots pass for the
applicable scope. The baseline evidence workflow is not a substitute for a
pilot, scanner result, or observability proof.

## 11. DNS and Reverse Proxy

- **DNS**: Add a wildcard A-record `*.<your-domain>` pointing to your server.
- **Production URL**: `<your-domain>` (e.g. `marcel.tuinstra.dev`)
- **Reverse proxy**: Configure Nginx Proxy Manager (or similar) to proxy each hostname to the corresponding local port with SSL.

## 12. Validation

After setup, verify end-to-end:

1. **CI**: Open a PR and confirm the reusable CI workflow runs and passes.
2. **Production CD**: Push to `main` and confirm production deployment succeeds.
3. **Health check**: Verify the production health URL returns HTTP 200.
4. **Gate baseline evidence**: Confirm the file/configuration evidence artifact
   is uploaded; treat it as presence evidence only.
5. **Gate assurance pilots**: For an enrolled repository, retain green and red
   pilot evidence for `Gate PR Security`. For one of the eight applications,
   complete the `Gate Observability` production-promotion evidence before
   enforcing that check. Gate Heal QA applies only to generated fix PRs under
   its existing controls; per-stack activation follows migration. It is not a
   prerequisite for ordinary observability migration or production promotion.

## 13. Rollback

If a deployment fails:

- The reusable CD workflow includes automatic rollback on health check failure.
- Manual rollback: SSH to the server and run `docker compose up -d` with the previous image digest.
- Pin caller workflow to a reviewed, known-good full commit SHA if a workflow
  regression is suspected, following the
  [pipeline security policy](../security/pipeline-security-policy.md).

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
