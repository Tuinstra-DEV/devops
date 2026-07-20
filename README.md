# DevOps Platform Repository

This public repository is the shared DevOps foundation for reusable workflows, deployment automation, templates, and platform standards used across Marcel Tuinstra projects.

> **Why public?** GitHub requires reusable workflow repositories to be public when used from personal accounts. This repo contains only generic platform assets — no secrets or application code.

## Architecture

The repository is organized around reusable platform assets:

- `.github/workflows/`: reusable GitHub Actions workflows and workflow examples.
- `scripts/`: shared deployment and provisioning scripts.
- `templates/`: project starter templates such as Dockerfile and Compose variants.
- `docs/`: platform decision records, standards, and operational guidance.

## Auth and Identity Direction

Auth0 is the accepted central identity provider for the DevOps platform and downstream project integrations (AirportToday and Subtrack). See `docs/adr/2026-02-auth0-central-idp.md` for rationale, guardrails, and tenant strategy under Epic SC-200.

## Onboarding

1. Clone the repository.
2. Ensure GNU Make is available (`make --version`).
3. Run local quality gates:
   - `make lint`
   - `make test`
4. Reuse workflows from other repositories with:
   - `uses: marcel-tuinstra/devops/.github/workflows/reusable-ci.yml@v1`

## Access Model

- Repository visibility: public (required for reusable workflow consumption).
- No secrets, credentials, or application code are stored in this repository.
- Write access remains limited to maintainers of this DevOps platform repo.
- Consumer repos reference workflows via `uses: marcel-tuinstra/devops/.github/workflows/<workflow>@v1`.

## Consumer Onboarding

See `docs/onboarding/consumer-migration-checklist.md` for a step-by-step guide to integrating your project with these reusable workflows.
Gate rollout readiness is documented in `docs/standards/gate-baseline.md`.

## Reusable Workflow Example

An example caller exists at `.github/workflows/example-caller.yml`.
A Nuxt-focused reusable CI guide is available at `docs/workflows/reusable-ci-nuxt.md`.

## Security Governance

Pipeline and deployment control requirements are documented in
`docs/security/pipeline-security-policy.md`.

## Workflow Versioning

Reusable workflow release channels, deprecation rules, and migration guidance are
documented in `docs/workflows/versioning-policy.md`.

## Gate Baseline Evidence

Consumer repositories can add the Gate baseline evidence workflow from
`templates/workflows/caller-gate-baseline.yml`. The reusable workflow produces
an artifact with rollout evidence for CI, release, deploy, Renovate, artifact
retention, branch protection, and the repo-owned Gate integration contract.

## Deployment Automation

Shared deployment script documentation is available at
`docs/scripts/deploy-service.md`.

Reusable Nuxt SSG CD workflow documentation is available at
`docs/workflows/reusable-cd-nuxt-ssg.md`.

## Ephemeral heavy CI

The production design and host automation for the single-concurrency Sanctuary
KVM runner are documented in `docs/runner/sanctuary-kvm-runner.md`.
