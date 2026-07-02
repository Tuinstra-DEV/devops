# Workflow Contract: reusable-ci-docker.yml

**Version:** v1  
**Status:** Stable  
**Last Updated:** 2026-02-08

## Purpose

Reusable CI workflow for Docker-based projects. Builds the Docker image and scans it with Trivy for security vulnerabilities during pull requests. This enables developers to catch and fix vulnerabilities before merging.

## Trigger

```yaml
on:
  workflow_call:
```

## Inputs

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `workdir` | string | No | `.` | Working directory containing Dockerfile |
| `dockerfile` | string | No | `Dockerfile` | Dockerfile path relative to workdir |
| `build-args` | string | No | `""` | Newline-separated Docker build args |
| `trivy-severity` | string | No | `"CRITICAL,HIGH"` | Comma-separated severity levels to scan |
| `trivy-exit-code` | string | No | `"1"` | Exit code on findings (1 = fail, 0 = warn) |

## Outputs

None.

## Required Secrets

None. This workflow does not push images or access external services.

## Required Permissions

```yaml
permissions:
  contents: read
  security-events: write
```

## Jobs

| Job | Description |
|-----|-------------|
| `build-and-scan` | Build Docker image locally and scan with Trivy |

## Expected Behavior

1. Checkout code
2. Setup Docker Buildx
3. Build image locally (no push)
4. Run Trivy vulnerability scan
5. Upload SARIF results to GitHub Security tab
6. **Fail PR if CRITICAL/HIGH vulnerabilities found** (configurable)

## Security Scanning

The workflow uses [Trivy](https://trivy.dev/) to scan Docker images.

**Default behavior:**
- Scans for `CRITICAL` and `HIGH` severity vulnerabilities
- Fails the pipeline if any are found (blocks merge)
- Ignores unfixed vulnerabilities (only actionable findings)
- Uploads results to GitHub Security tab (SARIF format)

**Use cases:**
- Shift security left: developers fix vulnerabilities in PRs
- Consistent scanning across CI and CD pipelines
- Visibility via GitHub Security tab

## Breaking Change Policy

See [versioning-policy.md](../versioning-policy.md) for major version upgrade procedures.

Changes that require a major version bump:
- Removing or renaming existing inputs
- Changing default security scan behavior
- Changing required permissions

## Example Caller

```yaml
name: CI

on:
  pull_request:

jobs:
  docker-scan:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-ci-docker.yml@v1
    with:
      dockerfile: Dockerfile
```

### Combined with Node.js CI

```yaml
name: CI

on:
  pull_request:

jobs:
  ci:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-ci.yml@v1
    with:
      node-version: "24"
  
  docker-scan:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-ci-docker.yml@v1
    with:
      dockerfile: Dockerfile
```
