# Workflow Contract: reusable-browser-quality.yml

**Version:** v10  
**Status:** Stable  
**Last Updated:** 2026-07-20

## Purpose

Runs the locked npm install and named Playwright script only for a same-repository human pull request from `develop` to `main`. The browser job uses the Sanctuary `[self-hosted, trusted-heavy]` runner and publishes a predictably named report artifact.

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

A small policy job runs on `ubuntu-24.04` and skips ordinary pull requests, pushes, schedules, and manual dispatches. It enables browser execution only when the event is a pull request with base `main` and head `develop`, the head repository is the same repository, the head is not a fork, the PR author is a human, and both the actor and triggering actor are human. The Sanctuary job repeats those checks directly before runner assignment. A separate always-running hosted `release-verdict` fails if a release candidate is untrusted, the policy did not enable the suite, or the browser job was skipped or failed. Require `release-verdict` as the caller's branch-protection context when adopting this workflow.

The browser job is pinned to `[self-hosted, trusted-heavy]`; it has no hosted fallback. Sanctuary runner images provision browser operating-system dependencies. The workflow installs the requested Playwright browser without `--with-deps`, which would make privileged system package changes on the persistent runner. `execution-class` remains as a deprecated compatibility input, but it does not change this routing.

`execution-class` remains accepted for caller compatibility but no longer changes browser routing.

Manual dispatches are not eligible for browser execution. Keep the input for compatibility while migrating consumers; do not use it to route browser suites to a hosted runner.
