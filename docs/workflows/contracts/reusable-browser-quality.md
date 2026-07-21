# Workflow Contract: reusable-browser-quality.yml

**Version:** v10  
**Status:** Stable  
**Last Updated:** 2026-07-20

## Purpose

Runs a locked npm install and one named Playwright package script on either a GitHub-hosted runner or the controlled `trusted-heavy` runner class. The workflow installs exactly one of Chromium, Firefox, or WebKit and publishes a predictably named report artifact.

## Public interface

| Input | Type | Required | Default |
|---|---|---:|---|
| `execution-class` | string | No | `hosted` |
| `node-version` | string | No | `24` |
| `workdir` | string | No | `.` |
| `browser` | string | No | `chromium` |
| `test-script` | string | No | `test:ui` |
| `report-path` | string | No | `playwright-report` |
| `artifact-retention-days` | number | No | `14` |

Output `report-artifact` is `browser-quality-<github.sha>`. The workflow accepts no secrets and grants only `contents: read`.

`test-script` is an npm script name, not a shell command. Paths must be repository-relative and may not traverse through `..`. Retention is limited to 1–90 days.

## Execution trust boundary

`execution-class` accepts only `hosted` or `trusted-heavy`. Omitted and invalid values select `ubuntu-24.04`; invalid values then fail validation. `trusted-heavy` resolves only to `[self-hosted, trusted-heavy]` and is rejected for fork pull requests, `pull_request_target`, and Dependabot. Callers must preserve a hosted path for untrusted changes.

Consumer repositories can use a controlled repository variable while retaining the safe default:

```yaml
jobs:
  browser:
    uses: Tuinstra-DEV/devops/.github/workflows/reusable-browser-quality.yml@<full-v10-commit-sha>
    with:
      execution-class: ${{ vars.CI_EXECUTION_CLASS || 'hosted' }}
```

For manual callers, expose a `workflow_dispatch` choice containing only `hosted` and `trusted-heavy`. Never pass a free-form runner label.

