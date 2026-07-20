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

### 6) Runner Trust Boundary

- Public contracts expose only `hosted` and `trusted-heavy`; arbitrary labels, groups, and `runs-on` fragments are prohibited.
- `hosted` is the default and the fallback whenever a caller omits or supplies an invalid class.
- Fork pull requests, Dependabot, and `pull_request_target` must never schedule `trusted-heavy`. The runner-selection expression enforces this before a job is queued, and contract validation fails a forbidden request on a hosted runner.
- Consumer repository variables may select the execution class only with an explicit hosted fallback. Manual inputs must be enumerated choices.

### 7) Build and Release Integrity

- Docker build arguments come only from the declared `build-args` input. Workflows do not merge arbitrary environment variables into builds.
- Secrets in Docker build arguments are prohibited. Reusable callers declare individual secrets and never use `secrets: inherit`.
- Release workflows publish and return immutable `name@sha256:digest` references.
- Release provenance is signed through GitHub OIDC, pushed alongside the image, and retained as a deterministic artifact. Deployments consume the digest, not a mutable tag.
- Browser and scan reports use commit-derived names, explicit retention, and fail when promised artifacts are absent.

### 8) Immutable Workflow References

- Every third-party action is pinned to a full 40-character commit SHA. A version comment records the reviewed upstream release.
- Production reusable-workflow callers pin the approved v10 release commit by full SHA. Tags remain immutable discovery and audit markers.
- Rollback changes caller SHAs and image digests through normal reviewed commits; it never rewrites a branch or moves a tag.

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
