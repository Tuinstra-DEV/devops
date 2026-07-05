# Branching Strategy

## Overview

Consumer repositories deploy production from `main`.

## Branches

| Branch | Purpose | Deploys to | Trigger |
|---|---|---|---|
| `main` | Production-ready code | Production | Push |
| `feat/SC-<id>` | Feature work | — | PR to `main` |
| `chore/SC-<id>` | Chores / maintenance | — | PR to `main` |
| `bug/SC-<id>` | Bug fixes | — | PR to `main` |

## Flow

```
feature branch ──PR──> main
                       │
                  Deploy Production
```

1. Create a feature branch from `main` (e.g. `feat/SC-123`).
2. Open a PR targeting `main`. CI runs automatically.
3. Merge to `main` — triggers Deploy Production.
4. Push to `main` triggers the release tag workflow, when configured.

## Release PR

Each consumer repo has a `.github/workflows/release.yml` workflow that creates a release PR:

- **Trigger**: Manual via `workflow_dispatch` (GitHub Actions "Run workflow" button)
- **Title**: `release: vX.Y.Z` (computed from latest semver tag + bump strategy)
- **Bump strategy**: `auto` uses merged PR titles (`feat` => minor, otherwise patch); `major` is manual via workflow input
- **Body**: Auto-generated list of merged PRs (`#number title`) on `develop` not yet in `main`
- **Effect of merge**: Pushes to `main` → triggers Deploy Production and creates git tag `vX.Y.Z`

Consumer repos use lightweight git tags (`vX.Y.Z`) for release traceability. GitHub Releases are optional. The release PR remains the audit trail, and the Docker image digest (pinned during CD) remains the deployment rollback unit.

See `templates/workflows/caller-release-pr.yml` and `templates/workflows/caller-release-tag.yml` for the workflow templates.

## Repositories using this strategy

| Repo | Develop | Main | Notes |
|---|---|---|---|
| site-marcel | — | Production (port 3000) | Example/reference |
| site-tuinstra | — | Production (port 3001) | Example/reference |

## Exceptions

The **devops** repo itself does NOT use this model. It uses story-driven branches merged directly to `main`, with tag-based releases (`v1`, `v2`). See `release-procedure.md`.

## Branch protection

Branch protection rules on `main` (require PR, require CI) are recommended but not currently enforced — GitHub free plan for non-org accounts does not support branch protection on private repos. This will be revisited when repos move to an organization.
