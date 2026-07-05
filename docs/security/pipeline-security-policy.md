# Pipeline Security Governance Policy

## Purpose

Define mandatory security controls for reusable workflows and deployment pipelines in
the DevOps platform.

## Scope

- Reusable workflows in `.github/workflows/`.
- Deployment scripts in `scripts/`.
- Consumer repositories using shared workflows.

## Mandatory Controls

### 1) OIDC-first Cloud Access

- Cloud authentication must use GitHub OIDC federation where supported.
- Long-lived cloud credentials are prohibited for CI/CD jobs.
- Temporary credentials must be scoped to workload identity and least privilege IAM roles.

### 2) GitHub Environments Model

- Every deploy workflow must target the `production` environment explicitly.
- `production` requires manual approval from at least one maintainer.
- Secrets are production environment-scoped and may not be stored as broad repository-level deploy secrets.

### 3) Secrets Naming and Ownership

- Naming format: `<SYSTEM>_<ENV>_<PURPOSE>`.
- Example: `GHCR_PRODUCTION_TOKEN`, `SSH_PRODUCTION_PRIVATE_KEY`.
- Every secret must have an owning team documented in repository docs.
- Secret rotation minimum: every 90 days for non-OIDC credentials.

### 4) Least Privilege Permissions

- Workflows default to restrictive token permissions and only grant required scopes.
- `contents: read` as baseline for build jobs.
- Deployment jobs can add `packages: write` and `id-token: write` only when required.
- SSH deploy keys must be command-restricted on target hosts.

### 5) Actions Supply Chain Integrity

- Third-party GitHub Actions must be pinned by full commit SHA.
- Reusable workflows must declare provenance expectations in docs.
- Build artifacts should include SBOM generation for deployable images.

## Pilot Repository Baseline

The pilot model for control validation is documented in
`docs/security/pilot-repo-controls.md` and includes:

- environment approvals,
- secret separation,
- least-privilege workflow permissions.

## Compliance Checks

Required periodic checks:

- Validate action pinning on every reusable workflow update.
- Verify environment approvals for production before each release window.
- Audit secret ownership and rotation evidence monthly.
