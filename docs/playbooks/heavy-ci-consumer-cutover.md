# Heavy CI consumer cutover and rollback

Use this runbook with the
[rollout matrix](../workflows/heavy-ci-rollout-matrix.md) and
[evidence template](../workflows/heavy-ci-evidence-template.md). It covers CI
routing and workflow rollback; application deploy rollback remains in the
[deployment rollback playbook](rollback.md).

## Owner checklist

Before opening a consumer change:

- [ ] Confirm the repository classification, decision state, owner, fallback
      approver, DEV story, dependencies, and blockers in the rollout matrix.
- [ ] Record the incumbent caller, last known-good full workflow SHA, exact
      required-check contexts, and current default branch.
- [ ] Pin the candidate reusable workflow by reviewed 40-character commit SHA.
- [ ] Keep the incumbent hosted caller or an explicit `hosted` execution-class
      fallback available.
- [ ] Keep caller-owned aggregate job names stable; do not make reusable
      fan-out job names part of branch protection.
- [ ] Confirm third-party Actions remain full-SHA pinned, permissions are least
      privilege, `secrets: inherit` is absent, and no deploy/OIDC/package-write
      scope enters heavy jobs.
- [ ] Confirm dependency caches contain downloads only and use the full trust,
      platform, toolchain, lockfile, and adapter identity.
- [ ] Prepare the evidence template and predeclare benefit, capacity, flake,
      required-check, trust, and rollback thresholds.

Before enabling `trusted-heavy`:

- [ ] Hosted canary evidence passes first.
- [ ] The repository is in the Sanctuary allowlist; `devops` is never eligible.
- [ ] Fork, Dependabot, and `pull_request_target` tests fail closed to hosted and
      cannot write canonical caches.
- [ ] Runner acceptance, two-slot admission, production-network denial, audit
      attribution, one-job teardown, and reconciliation checks are current.
- [ ] The fallback approver can disable repository runner-group access.

## Staged cutover

1. Add the candidate in report-only/canary mode on GitHub-hosted runners. Do not
   alter required checks.
2. Collect the minimum incumbent and candidate samples. Keep failed, cancelled,
   and rerun attempts in the reliability evidence.
3. Review timing, queue, cache, compute/cost, failure/flake, trust, artifact, and
   rollback evidence. A cache hit alone is not a pass.
4. Verify classic branch protection and all applicable rulesets through the
   GitHub API or settings UI. Record exact contexts and screenshots/API output.
5. If every gate passes, switch the caller deliberately in a normal reviewed
   commit. Preserve the stable aggregate check name.
6. Enable a new required check only after a green report-only period and after
   proving that rollback will not wait forever on that check.
7. Enable `trusted-heavy`, when justified, as a separate change after hosted
   evidence and runner-control approval. Never combine trust expansion with a
   contract-version or required-check change.
8. Monitor the first five merged/default-branch runs and retain artifacts and
   audit evidence for at least 30 days.

## Rollback triggers

Rollback on any stop condition in the rollout matrix, including check-name
drift, unexplained performance or flake regression, queue/capacity breach,
artifact verification failure, missing evidence, or any trust/cache isolation
failure.

## Hosted rollback procedure

1. If containment is required, disable the consumer's access to the
   `trusted-heavy` runner group. Let safe in-flight work finish or cancel it.
2. Prevent a new required check from blocking recovery: temporarily remove only
   the newly introduced context through the reviewed ruleset process. Preserve
   all incumbent security, release, and test checks.
3. Route the caller to `hosted` and restore the last known-good full workflow
   SHA in a normal reviewed commit. Never force-push, move a tag, or rewrite
   history.
4. Run the incumbent required checks and confirm their original context names
   complete successfully.
5. For a trusted-runner incident, stop new admission, reconcile the manager,
   and verify no orphan runner record, lease, domain, seed, or overlay remains.
6. Record trigger, run/job IDs, actual runner attribution, workflow SHAs,
   required-check change, UTC timeline, and outcome. Do not copy JIT material,
   credentials, seed images, or unnecessary logs.
7. Retain evidence for at least 30 days. Open a separate fix/follow-up story and
   require a fresh hosted canary before retrying.

## Completion record

- Repository / story:
- Cutover or rollback commit:
- Incumbent and candidate workflow SHAs:
- Required contexts before / after:
- Evidence record:
- Runner-group action, if any:
- Verification runs:
- Owner / fallback approver / UTC timestamp:
- Follow-up risks:
