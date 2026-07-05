# Pilot Repo Controls (Reference)

This reference shows the minimum controls required for a pilot repository adopting
the DevOps security governance policy.

## Repository

- Pilot repository: `site-marcel`.
- Workflows consume reusable workflows from `marcel-tuinstra/devops`.

## Active Controls

- `production` GitHub Environment is configured.
- `production` has required reviewers enabled.
- Production environment secrets are configured:
  - `SSH_PRODUCTION_PRIVATE_KEY`
  - `GHCR_PRODUCTION_TOKEN`
- Workflow token permissions are minimal by default.
- Deploy jobs explicitly request only:
  - `contents: read`
  - `packages: write`
  - `id-token: write`

## Evidence Checklist

- Screenshot or export of environment protection rules.
- Secret inventory by environment with owner.
- Workflow YAML showing scoped permissions and SHA-pinned actions.
