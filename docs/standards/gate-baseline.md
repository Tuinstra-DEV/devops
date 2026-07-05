# Gate DevOps Baseline

This baseline defines the minimum DevOps evidence expected before a consumer
repository is considered ready for Gate integration. It is intentionally small:
the goal is to make release, dependency, artifact, deployment, and ownership
signals easy to verify across repos.

## Consumer Repository Checklist

### Required workflows

- CI workflow calls the appropriate reusable workflow from this repo:
  - Node/Nuxt/Vite: `reusable-ci.yml@v1`
  - PHP/Symfony: `reusable-php-lint.yml@v1` and `reusable-php-test.yml@v1`
- Containerized services run Docker build and vulnerability scanning through
  `reusable-ci-docker.yml@v1`.
- Deployable services have environment-specific deployment callers for staging
  and production using the matching `reusable-cd-*.yml@v1` workflow.
- Release automation is installed:
  - `reusable-release-pr.yml@v1` for `develop` to `main` promotion PRs.
  - `reusable-release-tag.yml@v1` for release tags on `main`.
- The Gate baseline evidence workflow is installed from
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
- Consumer workflows pin reusable workflows to a stable major tag such as `@v1`
  for normal operation.
- `@main` is only used for canary validation and should not be required by
  branch protection.
- If a reusable workflow regression is suspected, callers may temporarily pin
  to a known-good commit SHA while the platform fix rolls forward.

### Branch protection expectations

- `main` requires pull requests and passing required checks before merge.
- `develop` requires passing CI before merge when it is used as the staging
  branch.
- Production deployments use the `production` environment and require manual
  approval when the repo has user-facing production traffic.
- Required checks include CI and, after rollout, the Gate baseline evidence
  workflow in enforcing mode.

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
  - gate-baseline
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

Consumer repos can copy
`templates/workflows/caller-gate-baseline.yml` to
`.github/workflows/gate-baseline.yml`.

Start in report-only mode:

```yaml
jobs:
  gate-baseline:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-gate-baseline.yml@v1
    with:
      fail-on-missing: false
```

After the checklist is green, switch to enforcing mode:

```yaml
jobs:
  gate-baseline:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-gate-baseline.yml@v1
    with:
      fail-on-missing: true
```

The workflow uploads a `gate-baseline-evidence` artifact containing a Markdown
summary. It can also be run locally from a consumer repo when this devops repo
is checked out nearby:

```bash
bash /path/to/devops/scripts/gate-baseline-scan.sh --repo . --fail-on-missing false
```
