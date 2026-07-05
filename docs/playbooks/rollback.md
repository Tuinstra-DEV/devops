# Rollback Playbook

**Trigger:** Need to revert to a previous working version after a failed
or problematic deploy.

## Rollback Options

### Option 1: Automatic Rollback (CD Workflow)

The CD workflow automatically rolls back when health check fails:

1. Stores last-known-good image digest in `.deploy-state.json`
2. On health check failure, redeploys the previous image
3. Logs rollback status in the workflow output

No manual action needed if auto-rollback succeeds.

### Option 2: Redeploy Previous Commit

Trigger a new deploy from a known-good commit:

```bash
# Find the last successful deploy commit
gh run list --workflow=deploy-production.yml --status=success --limit=5

# Get the commit SHA from a successful run
gh run view <run-id> --json headSha

# Trigger a new deploy from that commit
gh workflow run deploy-production.yml --ref <commit-sha>
```

### Option 3: Manual Image Rollback

When you need to rollback immediately without CI:

```bash
ssh -p <port> <user>@<host>
cd /mnt/ssd1000-01/projects/<project>/<environment>

# Find previous image digest
cat .deploy-state.json
# Or check Docker history
docker images --digests ghcr.io/<owner>/<repo>

# Create override with previous image
cat > .deploy-override.yml <<EOF
services:
  <service>:
    image: ghcr.io/<owner>/<repo>@sha256:<previous-digest>
EOF

# Deploy the rollback
docker compose -f docker-compose.<env>.yml -f .deploy-override.yml pull <service>
docker compose -f docker-compose.<env>.yml -f .deploy-override.yml up -d --no-deps <service>

# Verify health
curl http://localhost:<port>/health

# Clean up override
rm .deploy-override.yml
```

## Finding Previous Image Digests

### From GitHub Container Registry

```bash
# List recent images
gh api /user/packages/container/<repo>/versions --jq '.[].metadata.container.tags'

# Get specific digest
gh api /user/packages/container/<repo>/versions --jq '.[] | select(.metadata.container.tags | index("sha-<commit>")) | .name'
```

### From deploy state file

```bash
ssh -p <port> <user>@<host>
cat /mnt/ssd1000-01/projects/<project>/<environment>/.deploy-state.json
```

### From Docker on server

```bash
docker images --digests | grep <repo>
```

## Rollback Verification

After rollback, verify:

1. **Health endpoint:** `curl http://localhost:<port>/health`
2. **Application functionality:** Manual smoke test
3. **Logs:** `docker compose logs --tail=50 <service>`

## Post-Rollback

1. Do NOT immediately retry the failed deploy
2. Investigate root cause in a separate branch
3. Fix and validate through CI and local smoke tests before retrying production
4. Document the incident in Shortcut

## Nuxt SSG Rollback Example

```bash
# SSH to production server
ssh -p 2222 deploy@server.example.com
cd /mnt/ssd1000-01/projects/site-marcel/production

# Check current image
docker compose ps
docker images --digests ghcr.io/marcel-tuinstra/site-marcel

# Rollback to previous digest
cat > .rollback.yml <<EOF
services:
  web:
    image: ghcr.io/marcel-tuinstra/site-marcel@sha256:abc123...
EOF

docker compose -f docker-compose.production.yml -f .rollback.yml pull web
docker compose -f docker-compose.production.yml -f .rollback.yml up -d --no-deps web

# Verify
curl http://localhost:3000/health

# Clean up
rm .rollback.yml
```
