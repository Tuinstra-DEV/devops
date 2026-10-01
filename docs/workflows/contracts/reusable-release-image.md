# Workflow Contract: reusable-release-image.yml

**Version:** v10  
**Status:** Stable  
**Last Updated:** 2026-07-20

## Purpose

Builds and pushes an OCI image, generates an SBOM attestation, signs GitHub build provenance for the immutable manifest digest, pushes the provenance to the registry, and uploads the provenance bundle as a deterministic workflow artifact.

## Public interface

| Input | Type | Required | Default |
|---|---|---:|---|
| `execution-class` | string | No | `hosted` |
| `registry` | string | No | `ghcr.io` |
| `image-name` | string | Yes | — |
| `workdir` | string | No | `.` |
| `dockerfile` | string | No | `Dockerfile` |
| `build-args` | string | No | empty |
| `platforms` | string | No | `linux/amd64` |
| `artifact-retention-days` | number | No | `30` |

| Secret | Required | Purpose |
|---|---:|---|
| `registry-username` | No | Explicit non-GHCR registry username |
| `registry-password` | No | Explicit non-GHCR registry token |

GHCR uses `github.actor` and the job-scoped `github.token` by default. Callers must grant the reusable job `packages: write`, `id-token: write`, and `attestations: write`. External registries require the two explicitly named secrets; callers must not use `secrets: inherit`.

| Output | Guarantee |
|---|---|
| `image-ref` | Immutable `<registry>/<name>@sha256:...` reference |
| `image-digest` | OCI manifest digest returned by BuildKit |
| `provenance-artifact` | `release-provenance-<github.sha>` |
| `provenance-url` | GitHub attestation record URL |

Only `linux/amd64` and `linux/arm64` are accepted. `build-args` are the sole build-argument source and must never contain credentials or sensitive values; use BuildKit secret mounts in a separately reviewed contract if a build truly requires secrets.

The default runner is GitHub-hosted. A caller that explicitly requests `trusted-heavy` uses Sanctuary only for a human-triggered push to `main`, a tag push, or a published GitHub release, and only from the existing app runner allowlist (Gate, WODIQ, Tracker, Notify, Console, wodiq-site, marcel-site, and tuinstra-site). The DevOps control plane is excluded. Pull requests, pushes to `develop` or other branches, manual dispatches, schedules, and bot-triggered events always use the hosted runner.
