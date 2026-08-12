# Change-aware CI routing contract

`classify-ci-changes` is a fail-closed composite action for consumers that can
prove some expensive CI stages irrelevant. The consumer owns a reviewed JSON
policy; the platform owns parsing, non-overridable full-safe paths, evidence and
rollback semantics.

## Consumer policy

The `devops.ci-change-routing/v1` policy declares `repository`, `owner`,
`reviewedAt`, `expiresAt`, all eight classes (`runtime`, `frontend`, `backend`,
`container`, `contract`, `security`, `workflow`, `documentation`) and route
booleans. A path may match multiple classes. Routes are unioned. Runtime,
container, contract, security and workflow classes always select the full safe
path regardless of consumer configuration.

The action additionally owns non-overridable rules for workflow/action/policy,
runner, authentication, secrets, credentials, lockfiles, package manifests,
build scripts, containers, API contracts, release and deployment paths.
Missing or malformed policies, stale ownership metadata, unsafe or unknown
paths, invalid SHAs, unavailable or malformed diffs, and classifier errors emit
a `full-safe` decision with all route outputs set to `true`.

## Invocation and evidence

Callers check out the exact source revision with enough history and pin this
action by the reviewed DevOps commit SHA. They pass full base/head SHAs and a
repository policy path. The action parses `git diff --name-status -z`, including
both sides of renames and deleted paths, and emits stable class, reason,
policy-digest, evidence-path and stage outputs. The JSON evidence records sorted
changed-file records, selected classes, routes and a deterministic digest.

Only `mode: selective` can narrow work. `full`, a missing or unknown mode, or
`force-full: true` selects every stage. Organization variable
`CI_CHANGE_ROUTING_MODE=full` is therefore the one-change rollback; scheduled
and manual backstops also pass `force-full: true`. Permanent rollback is a
normal revert commit. Never use workflow-level path filters for required CI
contexts because an absent workflow cannot provide a deterministic check.
