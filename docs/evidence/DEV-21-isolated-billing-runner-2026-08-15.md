# DEV-21 isolated billing runner evidence

## Change boundary

The DevOps repository remains excluded from `trusted-heavy`. Only the report
job directly defined in `.github/workflows/ci-billing-report.yml` may request
the dedicated `[self-hosted, linux, x64, ops-billing]` route. The workflow is
limited to `schedule` and `workflow_dispatch` on `refs/heads/main`; there is no
hosted fallback.

## External runner-group policy

Verified in the GitHub organization settings on 2026-08-15 before deployment:

- organization: `Tuinstra-DEV`;
- group: `ci-billing-report`;
- group ID: `3`;
- repository access: selected, exactly `Tuinstra-DEV/devops`;
- public repositories: disabled;
- workflow access: selected, exactly
  `Tuinstra-DEV/devops/.github/workflows/ci-billing-report.yml@refs/heads/main`;
- configured runners at verification time: `0` (expected for ephemeral JIT
  registration).

The group is the scheduler-side assignment boundary. The Sanctuary manager is
the independent admission boundary: it validates repository/head-repository,
workflow ID/path, event, branch, SHA, run attempt, actor and triggering actor,
job/run binding, labels, route and group before requesting JIT configuration.

## Hosted parity baseline

Manual hosted run
[`31875979649`](https://github.com/Tuinstra-DEV/devops/actions/runs/31875979649)
completed successfully on 2026-08-15:

- job ID: `94991656316`;
- runner: `GitHub Actions 1000004149`;
- duration: 27m35s;
- artifact contract: sanitized JSON and Markdown produced;
- report status: `degraded`, with current-day partial data, one stale
  `subtrack-site` inventory entry, invalid completed-job timestamps and unknown
  runner attribution recorded explicitly rather than converted to zero.

Those data-quality issues predate DEV-21 and are out of scope. Parity means the
self-hosted run preserves the schema, report files, status semantics and
explainable source drift; it does not require identical live totals.

## Pre-merge verification

- `make lint`: passed;
- `make test`: passed;
- runner platform suite: 101 tests passed;
- workflow, heavy-CI, billing and dependency policy contract suites: passed;
- Packer formatting and Ansible syntax checks: not available on the local
  workstation; the repository's static Ansible/YAML checks passed;
- cross-repository dependency fleet check: skipped because
  `DEPENDABOT_FLEET_ROOT` was not set and is unrelated to DEV-21.

Negative tests cover DevOps rejection from `trusted-heavy`, route-scope
expansion, non-main and non-billing workflow identities, fork/head-repository
mismatch, bot actors, mismatched run/job SHA and IDs, incomplete labels,
duplicate route/group identity and bounded multi-page discovery.

## Post-merge evidence to append

After the reviewed PR is merged, deploy group ID 3 through protected inventory,
verify the manager service and then manually dispatch from `main`. Append:

- merge/deployment commit and UTC deployment time;
- workflow run and job IDs;
- `sanctuary-<job-id>` runner identity and `ci-billing-report` group name;
- queue, start, completion and total durations;
- artifact names and sanitized report status/issues;
- parity explanation versus hosted run `31875979649`;
- manager admission/audit attribution and confirmation that the JIT runner,
  VM, seed, overlay and lease were removed;
- confirmation that the trusted-heavy repository allowlist did not change.

Rollback is a normal reviewed commit restoring `runs-on: ubuntu-24.04`, after
disabling the dedicated group's repository access. No force-push, tag movement,
second label or automatic hosted fallback is permitted.
