# Node Version Strategy

## Current Default

The devops platform templates default to **Node.js 24 LTS** (Alpine-based images).

This applies to:

- `templates/docker/nuxt-ssg-nginx.Dockerfile` — base image `node:24-alpine`.
- Reusable CI workflow (`reusable-ci.yml`) — uses `node-version` input, defaulting to `24`.

## Override Mechanism

Consumer repos can override the Node version in two ways:

### 1. CI Workflow Input

Pass `node-version` when calling the reusable CI workflow:

```yaml
jobs:
  ci:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-ci.yml@v1
    with:
      node-version: '24'
```

### 2. Custom Dockerfile

Consumer repos provide their own `Dockerfile` and can use any base image:

```dockerfile
FROM node:24-alpine AS builder
# ...
```

The CD workflow uses whatever `Dockerfile` is in the consumer repo (default path: `Dockerfile`).

## Version Alignment

To keep Node versions consistent across CI and CD:

1. Set the same major version in your CI caller workflow (`node-version` input) and your `Dockerfile` base image.
2. When the devops platform bumps the default (e.g. 22 → 24), consumer repos can adopt at their own pace by pinning the version explicitly.

## LTS Policy

- The platform follows the [Node.js release schedule](https://nodejs.org/en/about/releases/).
- Default version tracks the current **Active LTS** line.
- Major version bumps in templates are announced in the changelog and may require consumer action.

## Update Procedure

When bumping the default Node version:

1. Update `templates/docker/nuxt-ssg-nginx.Dockerfile`.
2. Update the `node-version` default in `reusable-ci.yml` (if applicable).
3. Document the change in the workflow changelog.
4. Consumer repos with explicit version pins are unaffected until they choose to update.
