# Workflow Contract: reusable-ci.yml

**Version:** v1  
**Status:** Stable  
**Last Updated:** 2026-02-08

## Purpose

Reusable CI workflow for Nuxt/Node.js projects. Runs lint, typecheck, build, and optional tests.

## Trigger

```yaml
on:
  workflow_call:
```

## Inputs

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `node-version` | string | No | `"24"` | Node.js version to use |
| `workdir` | string | No | `"."` | Working directory for monorepos |
| `install-command` | string | No | `"npm ci"` | Install command |
| `test-command` | string | No | `""` | Command to run tests (empty = skip) |

## Outputs

None.

## Required Secrets

None. This workflow uses only public dependencies.

## Required Permissions

```yaml
permissions:
  contents: read
```

## Jobs

| Job | Description |
|-----|-------------|
| `ci` | Runs install, lint, typecheck, build, and optional test |

## Expected Behavior

1. Checkout code
2. Setup Node.js with npm cache
3. Install dependencies via `install-command`
4. Run `npm run lint --if-present`
5. Run `npm run typecheck --if-present`
6. Run `npm run build --if-present`
7. Run `test-command` if provided

## Breaking Change Policy

See [versioning-policy.md](../versioning-policy.md) for major version upgrade procedures.

Changes that require a major version bump:
- Removing or renaming existing inputs
- Changing default values in breaking ways
- Removing jobs or steps that callers depend on
- Changing required permissions

## Example Caller

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main, develop]

jobs:
  ci:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-ci.yml@v1
    with:
      node-version: "24"
      test-command: "npm test"
```
