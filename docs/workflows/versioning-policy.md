# Reusable Workflow Versioning Policy

> See [Workflow Contracts](contracts/README.md) for the public interfaces.

## Stable v10 channel

The browser-quality, Docker build/scan, and immutable release-image contracts are the stable v10 interface. `v10` and `v10.0.0` are immutable annotated release tags created from the same reviewed commit. They are release markers, not moving branches.

Production callers resolve the approved v10 tag once and pin the reusable workflow to its full 40-character commit SHA:

```yaml
uses: Tuinstra-DEV/devops/.github/workflows/reusable-ci-docker.yml@<full-v10-commit-sha>
```

This prevents an upstream tag movement or compromised release from silently changing consumer CI. `@main`, floating major tags, and short SHAs are prohibited in production callers. Canary validation may pin a reviewed feature commit SHA.

## Release semantics

- Patch: implementation hardening or documentation with no public-interface or default change. Publish an immutable `v10.0.x` tag and deliberately update consumer SHAs after canary validation.
- Minor: backward-compatible optional inputs or outputs with safe defaults. Publish immutable `v10.x.0` and deliberately update consumer SHAs.
- Major: removed/renamed inputs or outputs, changed defaults, changed permissions, runner trust changes, or other behavioral breaks. Publish the next immutable major contract.

Existing required inputs are never removed within a major. New inputs must be optional or have safe defaults. Deprecations have a written migration path and at least 60 days' notice.

## Security and compatibility rules

- Every external `uses:` reference in a reusable workflow is pinned to a full commit SHA with a version comment.
- Consumer reusable-workflow callers are pinned to a full commit SHA.
- `execution-class` accepts only `hosted` and `trusted-heavy`, defaults to `hosted`, and cannot route fork, Dependabot, or `pull_request_target` work to a trusted runner.
- Build arguments are supplied only through the explicit `build-args` input and must not contain secrets.
- Reusable callers pass only explicitly declared secrets; `secrets: inherit` is prohibited.
- Workflow and contract changes require CODEOWNERS review and the contract checks in `make lint` and `make test`.

## Rollout

1. Merge the reviewed workflow and contract changes.
2. Create immutable v10 release tags using [the release procedure](release-procedure.md).
3. Resolve the release tag to a full commit SHA and validate that SHA in designated canary repositories on hosted runners.
4. Enable `trusted-heavy` only for trusted repository events after runner controls are verified.
5. Update production callers deliberately to the approved SHA and monitor them.

Repository variables may select the execution class with a safe fallback:

```yaml
execution-class: ${{ vars.CI_EXECUTION_CLASS || 'hosted' }}
```

Manual callers expose a choice with only `hosted` and `trusted-heavy`; they do not accept a free-form label.

## Rollback

Never move, delete, or force-push a release tag. Roll back a consumer by restoring its last known-good full workflow SHA, then make a normal reviewed commit. Keep image deployment rollback pinned to the previously verified `name@sha256:digest` reference. Publish a new immutable patch or major release for the correction.

Release notes record `Changed`, `Impact`, `Action Required`, verification evidence, and the exact rollback SHA/digest.
