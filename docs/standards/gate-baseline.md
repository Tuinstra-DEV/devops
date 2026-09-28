# Gate DevOps Baseline

This document defines the repository baseline and rollout boundary for Gate
assurance. The Gate baseline workflow is file/configuration evidence only: it
reports whether expected files and settings are present. It does not scan code,
prove security, or establish end-to-end enforcement.

The normative security and application observability contract is
[`gate-assurance-v1.md`](../workflows/contracts/gate-assurance-v1.md). The
machine-readable rollout inventory and schema are in
[`inventory.json`](../../contracts/gate-assurance/v1/inventory.json) and
[`schema.json`](../../contracts/gate-assurance/v1/schema.json). The onboarding
configuration example is
[`observability.example.json`](../../templates/gate/observability.example.json).

All Gate assurance facilities described here are planned. The repositories
listed below are not yet enrolled or enforced by virtue of this document or a
Gate baseline evidence artifact.

## Assurance scope

### Gate PR Security

The planned Gate PR Security requirement covers these 13 repositories: `gate`, `devops`,
`notify`, `console`, `status`, `tracker`, `wodiq-app`, `wodiq-platform`,
`openairco`, `marcel-site`, `tuinstra-site`, `wodiq-site`, and `openairco-site`.
It evaluates the exact base and head revisions with the same scanner versions,
rules, and advisory snapshots. New or worsened high/critical findings block an
ordinary pull request. Existing debt remains visible and is not reclassified as
new debt solely because an exception expired. Existing stricter checks remain
in force.

The Gate PR Security check must use trusted policy and must not execute
pull-request scripts, plugins, or configuration. Scanning is deterministic and
uses no AI. Missing tools, timeouts, malformed output, or incomplete coverage
cannot produce a green result. The dedicated publisher GitHub App uses OIDC
from an approved reusable workflow pinned to a full commit SHA; its credentials
are separate from Gate Heal credentials. Forks and bot pull requests receive
the same security guarantee.

### Gate Observability and Gate Heal QA

Gate Observability applies at production promotion for these eight applications:
`notify`, `console`, `status`, `tracker`, `wodiq-app`, `wodiq-platform`,
`openairco`, and `gate`. They do not apply to `devops` or the four site
repositories. `Gate Heal QA` applies only to automatically generated fix PRs
for these applications. Gate's existing QA checks and controls remain intact;
activation for each stack follows its migration. Treat the three decisions as
distinct checks: `Gate PR Security`, `Gate Observability`, and `Gate Heal QA`.

Each application owns its runtime instrumentation and sends only minimal,
redacted diagnostics. Do not send replay data, request or message content, or
user identifiers. An unknown component or source requires human review. New
applications must meet the Sentry standard before their first production
promotion; existing applications migrate and prove coverage before enforcement.
Automatic fix generation remains disabled. Gate Heal QA evaluates generated
fix PRs under its existing controls; per-stack activation follows migration,
and merges remain human-approved.

Probe the verification environment during onboarding and relevant integration
changes. Every release verifies configuration, source maps, uploaded artifacts,
symbols, and evidence. Production configuration is verified separately. A
release must not depend synchronously on Sentry availability. Normal rollback to
a previously verified artifact remains available. An emergency bypass requires
a human decision with the reason and exact version recorded.

## Rollout and exceptions

Run both green and red pilot pull requests before making `Gate PR Security`
required on `main` and `develop` where those branches exist. Keep existing
stricter required checks. A baseline artifact or reusable workflow call alone
does not satisfy the pilot or prove scanner coverage. Exception expiry does not
turn an unchanged finding into a newly introduced finding; compare the same
base/head policy snapshots and require human review where classification is
uncertain.

## Consumer Repository Checklist

### Required workflows

- CI workflow calls the appropriate reusable workflow from this repo:
  - Node/Nuxt/Vite: `reusable-ci.yml@<APPROVED_40_CHARACTER_COMMIT_SHA>`
  - PHP/Symfony: `reusable-php-lint.yml@<APPROVED_40_CHARACTER_COMMIT_SHA>`
    and `reusable-php-test.yml@<APPROVED_40_CHARACTER_COMMIT_SHA>`
- Containerized services run Docker build and vulnerability scanning through
  `reusable-ci-docker.yml@<APPROVED_40_CHARACTER_COMMIT_SHA>`.
- Deployable services have environment-specific deployment callers for staging
  and production using the matching `reusable-cd-*.yml` workflow pinned to an
  approved full commit SHA.
- Release automation is installed:
  - `reusable-release-pr.yml@<APPROVED_40_CHARACTER_COMMIT_SHA>` for `develop`
    to `main` promotion PRs.
  - `reusable-release-tag.yml@<APPROVED_40_CHARACTER_COMMIT_SHA>` for release
    tags on `main`.
- The optional Gate baseline evidence workflow is installed from
  `templates/workflows/caller-gate-baseline.yml`.

### Release and tag policy

- Normal product flow is feature branches to `develop`, then release PR from
  `develop` to `main`.
- Release PR titles use `release: vX.Y.Z` unless the repository has documented
  date-based releases.
- Tags are immutable release markers created after merge to `main`.
- Docker image digests are the deploy and rollback unit for services.

### Artifact retention

- Workflows that upload evidence, build outputs, scan reports, or test reports
  set explicit `retention-days`.
- Baseline evidence artifacts should be retained for at least 30 days.
- Security scan uploads may also publish SARIF to the GitHub Security tab when
  the reusable workflow supports it.

### Deploy readiness

- Deployable repos have `staging` and `production` GitHub Environments.
- Environment secrets and variables are configured at the environment level:
  - `SSH_PRIVATE_KEY` as a secret.
  - `SSH_HOST` as a variable.
- Compose files exist per environment, or the repository documents why it does
  not deploy through Docker Compose.
- Health checks use a stable path, normally `/health`, and the CD workflow
  verifies the running container over SSH before completing.
- Rollback instructions identify the previous image digest or workflow pin to
  restore.

### Renovate and devops pinning

- Renovate is enabled and extends the appropriate preset from this repo:
  - `github>marcel-tuinstra/devops:renovate/nuxt`
  - `github>marcel-tuinstra/devops:renovate/symfony`
  - or `github>marcel-tuinstra/devops:renovate/default`
- Consumer workflows pin reusable workflows to an approved immutable full
  commit SHA. Follow the
  [pipeline security policy](../security/pipeline-security-policy.md); do not
  use a major tag as a production workflow pin. In examples, replace
  `@<APPROVED_40_CHARACTER_COMMIT_SHA>` with a reviewed, real commit SHA.
- `@main` is only used for canary validation and should not be required by
  branch protection. Production workflow pins follow the
  [pipeline security policy](../security/pipeline-security-policy.md).
- If a reusable workflow regression is suspected, callers may temporarily pin
  to a known-good commit SHA while the platform fix rolls forward.

### Branch protection expectations

- `main` requires pull requests and passing required checks before merge.
- `develop` requires passing CI before merge when it is used as the staging
  branch.
- Production deployments use the `production` environment and require manual
  approval when the repo has user-facing production traffic.
- Required checks include CI and the checks approved for that repository after
  the Gate assurance rollout. The Gate baseline evidence workflow is not a
  security or end-to-end check.

### Gate integration contract

Each consumer repo should carry a small Gate contract at `.gate/baseline.yml`.
The file is repo-owned and may include more detail, but it should at least
answer:

```yaml
owner: marcel-tuinstra
repository: example-repo
production_branch: main
staging_branch: develop
release_policy: semver-release-pr
required_checks:
  - ci
  - Gate PR Security
# At production promotion for the eight applications, add after migration and
# successful pilots. Gate Heal QA applies only to generated fix PRs, under
# existing controls; activation for each stack follows migration.
application_checks:
  production_promotion:
    - Gate Observability
  automatic_fix_prs:
    - Gate Heal QA # existing controls; per-stack activation follows migration
deployments:
  staging:
    environment: staging
    health_path: /health
  production:
    environment: production
    health_path: /health
renovate:
  preset: github>marcel-tuinstra/devops:renovate/nuxt
evidence_workflow: .github/workflows/gate-baseline.yml
```

The contract is not a secret store. It should contain only routing, ownership,
release, and evidence expectations that Gate and maintainers can read safely.

## Baseline Evidence Workflow

Consumer repos may install
`templates/workflows/caller-gate-baseline.yml` as
`.github/workflows/gate-baseline.yml` for repository file/configuration
evidence. It reports presence only and is not a scanner, security check, or
end-to-end verification.

Start in report-only mode:

```yaml
jobs:
  gate-baseline:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-gate-baseline.yml@<APPROVED_40_CHARACTER_COMMIT_SHA>
    with:
      fail-on-missing: false
```

After the checklist is green, the workflow may fail on missing baseline files;
this does not enable or enforce Gate assurance checks:

```yaml
jobs:
  gate-baseline:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-gate-baseline.yml@<APPROVED_40_CHARACTER_COMMIT_SHA>
    with:
      fail-on-missing: true
```

The workflow uploads a `gate-baseline-evidence` artifact containing a Markdown
summary. It can also be run locally from a consumer repo when this devops repo
is checked out nearby:

```bash
bash /path/to/devops/scripts/gate-baseline-scan.sh --repo . --fail-on-missing false
```
