# Release Procedure

## Overview

Reusable workflows in this repository are consumed by external repos via major version tags (e.g. `@v1`). This document describes how to create and manage those tags.

## Tag Convention

| Tag | Purpose |
|---|---|
| `v1` | Current stable major version |
| `v2` | Next major (only after breaking changes) |
| `@main` | Canary / bleeding edge (not recommended for production) |

Consumer repos reference workflows like:

```yaml
uses: marcel-tuinstra/devops/.github/workflows/reusable-ci.yml@v1
```

## Releasing a Patch or Minor Change

After merging a backward-compatible change to `main`:

```bash
make release-tag TAG=v1
```

This force-moves the `v1` tag to current `HEAD` and pushes it. All consumer repos using `@v1` automatically pick up the change on their next workflow run.

## Releasing a Major Version

When introducing breaking changes (input renames, removed inputs, behavior changes):

1. Merge the breaking change to `main`.
2. Create the new major tag:

```bash
git tag v2 HEAD
git push origin v2
```

3. Announce the new version (see `versioning-policy.md` for deprecation rules).
4. Keep `v1` pointed at its last compatible commit — do NOT move it forward.

## Safety Rules

- **Never force-push `main`.**
- Only force-push version tags via `make release-tag`.
- Always run `make lint && make test` before tagging.
- After tagging, verify at least one consumer repo picks up the new version.

## Rollback

If a tagged release causes issues in consumer repos:

1. Identify the last known-good commit on `main`.
2. Move the tag back:

```bash
git tag -f v1 <good-commit-sha>
git push origin v1 --force
```

3. Document the rollback and root cause.

## Makefile Target

```bash
# Move existing tag to HEAD and push
make release-tag TAG=v1

# The target validates TAG is provided and runs:
# git tag -f $TAG HEAD && git push origin $TAG --force
```

---

## Consumer Repo Releases

Consumer repos (site-marcel, site-tuinstra, etc.) use a different release model from this repository.

### How it works

1. Code flows through feature branches → `develop` (staging) → `main` (production).
2. To promote `develop` to production, run the **Create Release PR** workflow via `workflow_dispatch`.
3. This creates a PR from `develop` → `main` with title `release: vX.Y.Z` and an auto-generated PR-based changelog.
4. Merging the PR pushes to `main`, triggers Deploy Production, and triggers the release tag workflow.
5. The release tag workflow creates tag `vX.Y.Z` from the merged release PR title.

### Why lightweight version tags?

- The release PR title is already a semantic release unit (`release: vX.Y.Z`).
- The matching git tag gives a stable reference for diffs, rollbacks, and audit history.
- The Docker image digest (pinned during CD) remains the deployment artifact and rollback unit.

### Template

See `templates/workflows/caller-release-pr.yml` and `templates/workflows/caller-release-tag.yml` for the workflow templates.

### Future: product repos

Product repos with external users (airporttoday, subtrack) may need proper semantic versioning, changelogs, and GitHub Releases. This will be implemented under SC-210 when those repos are onboarded.

See `branching-strategy.md` for the full branching model.
