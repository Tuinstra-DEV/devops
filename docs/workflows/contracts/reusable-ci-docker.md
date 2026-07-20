# Workflow Contract: reusable-ci-docker.yml

**Version:** v10
**Status:** Stable  
**Last Updated:** 2026-07-20

## Purpose

Builds a local Docker image, enforces a Trivy vulnerability policy, and publishes deterministic table and SARIF reports. SARIF upload is isolated in a second job so `security-events: write` is granted only when requested.

## Public interface

| Input | Type | Required | Default |
|---|---|---:|---|
| `execution-class` | string | No | `hosted` |
| `workdir` | string | No | `.` |
| `dockerfile` | string | No | `Dockerfile` |
| `build-args` | string | No | empty |
| `trivy-severity` | string | No | `CRITICAL,HIGH` |
| `trivy-exit-code` | string | No | `1` |
| `upload-sarif` | boolean | No | `false` |
| `artifact-retention-days` | number | No | `14` |

The existing v1 inputs remain valid. v10 adds `execution-class`, `artifact-retention-days`, and outputs without changing the default hosted behavior.

| Output | Guarantee |
|---|---|
| `image-digest` | Digest returned by the local BuildKit build |
| `scan-artifact` | `docker-scan-<github.sha>` containing table and SARIF reports |
| `sarif-artifact` | Alias of `scan-artifact` for code-scanning consumers |

The build/scan job grants only `contents: read`. The optional upload job grants `actions: read`, `contents: read`, and `security-events: write`. No secrets are accepted. `build-args` is the only build-argument source and is for non-secret values only.

The execution trust boundary and repository-variable/manual fallback are documented in [reusable-browser-quality](reusable-browser-quality.md). Fork pull requests, `pull_request_target`, and Dependabot can never select a trusted runner.

## Example

```yaml
jobs:
  image-quality:
    permissions:
      contents: read
      security-events: write
    uses: Tuinstra-DEV/devops/.github/workflows/reusable-ci-docker.yml@<full-v10-commit-sha>
    with:
      execution-class: ${{ vars.CI_EXECUTION_CLASS || 'hosted' }}
      upload-sarif: true
```
