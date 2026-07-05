# Contract: reusable-gate-baseline.yml

## Status

- Version: v1
- Stability: Stable after the first `v1` tag that includes this workflow.
- Purpose: Produce Gate baseline evidence for consumer repositories and
  optionally fail when required rollout checks are missing.

## Caller

```yaml
jobs:
  gate-baseline:
    uses: marcel-tuinstra/devops/.github/workflows/reusable-gate-baseline.yml@v1
    with:
      fail-on-missing: false
```

## Inputs

| Input | Type | Required | Default | Description |
|---|---|---:|---|---|
| `fail-on-missing` | boolean | no | `false` | Exit non-zero when required checks fail. Use `false` for report-only rollout and `true` after onboarding. |
| `require-deploy` | boolean | no | `true` | Require at least one caller for a reusable CD workflow. Set `false` for libraries or non-deployable repos. |
| `require-renovate` | boolean | no | `true` | Require a Renovate config extending a preset from this repo. |
| `gate-contract-path` | string | no | `.gate/baseline.yml` | Path to the repo-owned Gate integration contract. |
| `devops-ref` | string | no | `v1` | Ref used to fetch the scan script from this repo. Use `main` only when canary-testing the reusable workflow from `@main`. |
| `upload-artifact` | boolean | no | `true` | Upload the generated evidence files. |
| `artifact-retention-days` | number | no | `30` | Retention period for the uploaded evidence artifact. |

## Outputs

This workflow does not expose workflow outputs.

## Permissions

The workflow requests:

```yaml
permissions:
  contents: read
```

## Evidence artifact

When `upload-artifact` is true, the workflow uploads
`gate-baseline-evidence` containing:

- `gate-baseline-summary.md`
- `gate-baseline-summary.tsv`

The Markdown summary is also appended to the GitHub Actions step summary.

## Checks

The scan verifies:

- `.github/workflows` exists.
- A reusable CI caller is present.
- Release PR and release tag callers are present.
- A reusable deploy caller is present when `require-deploy` is true.
- Devops reusable workflows are not pinned to `@main`.
- Renovate extends a shared devops preset when `require-renovate` is true.
- `actions/upload-artifact` steps set explicit `retention-days`.
- The Gate integration contract file exists.
- Branch protection evidence is present through `CODEOWNERS`,
  `docs/branch-protection.md`, or manual rollout evidence.

Branch protection settings cannot be fully verified from repository files. The
workflow records that gap as a warning unless file-based evidence exists.

## Compatibility

This is an additive workflow. Changes to defaults, required inputs, artifact
file names, or failure semantics are breaking changes and require a new major
version.
