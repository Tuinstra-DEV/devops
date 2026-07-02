# Reusable CI Workflow for Nuxt

The reusable CI workflow at `.github/workflows/reusable-ci.yml` provides a standardized
Nuxt CI pipeline with the following sequence:

1. `npm ci`
2. `npm run lint`
3. `npm run typecheck`
4. `npm run build`
5. optional test command

## Inputs

- `node-version`: Node.js version (default `24`).
- `workdir`: working directory for monorepos (default `.`).
- `test-command`: optional test command (default empty).

## Caller Example

```yaml
name: CI

on:
  pull_request:

jobs:
  ci:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-ci.yml@v1
    with:
      node-version: "24"
      workdir: "."
      test-command: npm test
```

## Migration Targets

This workflow is intended to replace copy-paste CI configuration in:

- `site-marcel`
- `airporttoday-nuxt`
- `site-subtrack`
- `site-tuinstra`
