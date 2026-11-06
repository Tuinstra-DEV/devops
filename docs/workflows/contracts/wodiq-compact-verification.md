# Compact WODIQ verification

The new immutable revision of `reusable-trusted-verification.yml` is a
**WODIQ-only entrypoint**. Other repositories must retain their existing reviewed
pins; this revision is not a drop-in upgrade for Gate, Tracker, Notify or the
single-job adapters. Their exact previous workflow source remains in
`tests/fixtures/trusted-verification-before-wodiq-compact.yml`, with a fixed hash
and its existing offline contract tests.

The WODIQ consumer has no command, path, label, profile, trust or selection input.
One GitHub-hosted preflight verifies the current PR, merge parents, source/head/
base SHA, workflow pin, run ID and attempt against GitHub. It then checks out the
verified source without persisted credentials, selects the diff, validates the
strict version-3 plan and its ordered suite closure, and runs the existing
workspace/environment, CI-policy and secret checks. A develop-to-main release
always requires the complete plan, including a documentation-only release.

Five fixed jobs cover core/migration/client, runtime build, API, Management and
workers. Each job chooses one runner and one immutable adapter from the verified
preflight decision. Trusted labels, group, profiles, toolchain and permissions
remain fixed. Hosted fallback uses Ubuntu 24.04. Consumers require a successful
build proof on the same route. Job outputs retain the selected route and literal
verified proof; source/run/attempt/selection identities remain part of the final
consumer verdict. No matrix-output aggregation or optional final verdict is used.

This produces seven job cards for a normal WODIQ PR, including its required
`Workspace baseline`, and eight for a release including its image job. Only
selected suites execute. A skipped job uses no hosted runner. Combining selection
and admission removes one short hosted execution per run; this is not a claim
about invoice savings or Sanctuary operating costs.

## Rollout boundary

1. Run `make lint`, `make test`, and the compact workflow tests locally; review
   the DevOps PR. Do not modify the running manager, resource limits or credentials.
2. After merge, a DevOps operator admits the **exact reviewed commit reference**
   through the existing runner-group/manager workflow-reference procedure. Preserve
   all pins still used by other consumers. Do not append a sixth pin or relax the
   configured policy limits; reconcile the existing consumer pins first if needed.
3. Only then update WODIQ's consumer pin and corresponding verdict identity in
   one commit, together with the version-3 selector and compact result checks.
   A missing admission is an explicit rollout blocker, not permission to route an
   unverified source to Sanctuary or silently change its proof route.
4. Verify one complete PR run before merge. Release images and promotion still
   require their own full exact-source release proof. No deployment is included.

Rollback is a normal consumer PR restoring the prior workflow pin, selector and
verdict contracts together. Existing immutable pins and image proofs are retained.
