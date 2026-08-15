# Heavy CI evidence gate and rollout matrix

This document turns the DEV-6 contract, DEV-7 pilot, and DEV-9 baseline into a
controlled adoption path. It is a decision record for repository owners; it
does not migrate a consumer or change product test semantics.

Evidence in this document was reviewed on 2026-08-12. Revalidate repository
workflows, rulesets, and default branches before each cutover because those are
live GitHub settings.

## Classification and decision states

Classification describes the workflow shape, not the runner that must execute
it:

- **Heavy**: two or more expensive container, browser, integration, or live
  stages can reuse one build, or the workflow already has a `trusted-heavy`
  candidate path. Heavy consumers still start with a hosted canary.
- **Medium**: browser, integration, container, or release work is material, but
  build-once fan-out is not yet proven to beat the current hosted workflow.
  Consume telemetry or selected reusable contracts first.
- **Light**: a short single-path workflow with no meaningful fan-out. Keep it
  hosted and adopt only safe contract, pin, or telemetry updates.

Every matrix row also has a decision state:

- `evidence-only`: collect or validate evidence; do not change routing.
- `deferred`: a named blocker must be resolved before a canary.
- `hosted-canary`: eligible for an A/B canary on GitHub-hosted runners.
- `ready`: all gates in [the evidence template](heavy-ci-evidence-template.md)
  pass and the owner has approved cutover.

`trusted-heavy` is never implied by classification. It requires a separate
hosted canary, trust review, allowlist check, and rollback approval.

## Repository rollout matrix

The owner is the repository maintainer responsible for the rollout evidence.
For the current repositories that DRI is Marcel; a replacement must be recorded
before ownership changes.

| Repository | Class | Decision | Expected benefit and evidence basis | Owner | Dependencies | Blockers | Required-check review | Fallback path |
|---|---|---|---|---|---|---|---|---|
| `console` | Medium | Deferred | DEV-9 measured CI p50 2:21; browser quality at 1:09 is the largest current stage. A contract-pin refresh and hosted browser telemetry are more likely to help than full heavy fan-out. | Marcel / Console maintainer | DEV-3 consumer migration scope; reviewed immutable DevOps workflow SHA; hosted evidence set | Browser setup requires privileged hosted package changes; no 5+5 comparable A/B set; build-once reuse is unproven | Default `develop`; active organization ruleset has no required status check. Preserve the caller-owned job names (`Lint & Static Analysis`, `Tests`, `Browser Quality Gate`, `Security Check`) during any canary. | Restore the current hosted `ci.yml` and its last known-good full DevOps SHA in a normal reviewed commit. |
| `notify` | Heavy | Deferred | DEV-9 measured CI p50 4:06; production targets, local stack, and isolated browser runtime each cost about 2:46-2:54. Shared container/build reuse could be material. | Marcel / Notify maintainer | DEV-3 consumer migration scope; Notify reliability work; hosted heavy-ci adapter and exact BuildKit/cache telemetry | 8 of 18 sampled runs were cancelled; benefit and image equivalence are unproven. Reliability is a gate, not noise to discard. | Default `main`; active organization ruleset has no required status check. Keep existing `quality`, `production-images`, and `browser-smoke` result semantics stable. | Set `execution-class`/`CI_HEAVY_EXECUTION_CLASS` to `hosted`, then restore the prior hosted caller SHA if needed. |
| `tuinstra-site` | Light | Evidence-only | DEV-9 measured a roughly 27-43 second site class; dependency install was 8 seconds. Heavy orchestration overhead would dominate. Benefit is governance: current contract pin, telemetry, and predictable rollback. | Marcel / tuinstra-site maintainer | DEV-3 contract-refresh scope; reviewed immutable reusable-ci SHA | No performance case for heavy CI | Default `develop`; repository ruleset requires exactly `ci / ci`. A pin refresh must preserve that context. | Revert the caller SHA to the previously passing full SHA; remain on hosted `reusable-ci`. |
| `marcel-site` | Light | Evidence-only | DEV-9 measured a roughly 27-43 second site class; dependency install was 8 seconds. Use only contract refresh and evidence hygiene. | Marcel / marcel-site maintainer | DEV-3 contract-refresh scope; reviewed immutable reusable-ci SHA | No performance case for heavy CI | Default `develop`; repository ruleset requires exactly `ci / ci`. A pin refresh must preserve that context. | Revert the caller SHA to the previously passing full SHA; remain on hosted `reusable-ci`. |
| `wodiq-site` | Light | Evidence-only | DEV-9 found build 12 seconds, tests 9 seconds, and install 7 seconds. Heavy fan-out has no credible payoff. | Marcel / wodiq-site maintainer | DEV-3 contract-refresh scope; reviewed immutable reusable-ci SHA | No performance case for heavy CI | Default `develop`; active organization ruleset has no required status check. Preserve the current `ci / ci` check name for future compatibility. | Revert the caller SHA to the previously passing full SHA; remain on hosted `reusable-ci`. |
| `devops` | Light (control plane) | Evidence-only | DEV-9 measured PR test p50 10 seconds. Its role is contract fixtures, policy tests, and evidence-template validation, not consumer acceleration. | Marcel / DevOps platform maintainer | DEV-6 contract and DEV-8 evidence tests | Explicitly excluded from `trusted-heavy`; DEV-21 permits only the isolated scheduled/manual billing report route | Default `main`; active organization ruleset has no required status check. Keep `PR Checks / lint` and `PR Checks / test` stable if they become required. | Keep every PR check hosted. The billing-only rollback restores `ubuntu-24.04` by normal commit after disabling its dedicated group. Never add devops to `trusted-heavy`. |

The workflow review also found that the five consumer callers still pin the
older full DevOps SHA `a8815205609fdc709a521914bd927ba72d8d7ad5`. A pin refresh
is a reviewed DEV-3 migration activity; it is not evidence that heavy CI is
faster.

## Evidence and readiness gate

A repository is `ready` only when all of these are true:

1. The owner completed [the standard evidence template](heavy-ci-evidence-template.md).
2. The incumbent and candidate use comparable workflow/adapter versions,
   triggers, attempts, stage sets, toolchains, lockfiles, and runner classes.
3. There are at least five comparable successful first attempts per path.
   Cache claims additionally have at least three cold misses and three warm
   hits. Twenty samples are required before publishing p95 claims.
4. The candidate meets its predeclared target. A cache-speed claim requires at
   least 20% median improvement in the affected stage. Queue time must not erase
   the critical-path gain, and compute/cost may not materially regress without
   an explicit owner decision.
5. Required checks are reviewed against both classic branch protection and
   repository/organization rulesets. Existing external check names remain
   stable through a caller-owned aggregate job; internal fan-out jobs are not
   added individually.
6. Required stages and evidence are present in every counted run. Reruns are
   excluded from timing medians and included in failure/flake evidence.
7. Fork, Dependabot, and `pull_request_target` traffic is proven hosted-only;
   only trusted default-branch pushes can write canonical caches.
8. The owner and fallback approver have executed or dry-run the linked
   [cutover and rollback runbook](../playbooks/heavy-ci-consumer-cutover.md).

Missing queue, cache, trust, required-check, or rollback evidence means
`insufficient evidence`, never an assumed pass.

## Rollout order

1. **DevOps evidence-only:** validate this policy and its tests on hosted PR
   checks. DevOps never enters `trusted-heavy`; only DEV-21's
   workflow-restricted scheduled/manual billing report may use its separate
   Sanctuary route.
2. **Light sites:** refresh immutable reusable-workflow pins under DEV-3 only
   when normal repository work allows it. Preserve `ci / ci` where required.
   Do not add heavy orchestration.
3. **Console hosted telemetry:** keep the privileged browser path hosted and
   test selected contract/pin improvements under DEV-3. Promote only if the
   evidence gate proves a benefit.
4. **Notify reliability, then hosted A/B:** first separate cancellations,
   failures, and concurrency waits. Only then test build-once/container reuse
   on hosted runners. `trusted-heavy` is a later decision.
5. **Cross-pilot review:** use the completed DEV-6 through DEV-9 review to
   decide whether any additional consumer implementation story is justified.

DEV-8 owns the matrix, evidence rules, and operator runbook only. Consumer
caller or adapter changes for Console, Notify, and the sites belong to DEV-3 or
a follow-up linked to it. WODIQ, Tracker, or Gate migration corrections belong
to DEV-5/DEV-7 follow-up scope. Shared contract changes belong to a DEV-6
follow-up. Do not hide implementation work in this documentation story.

## Stop and rollback conditions

Stop the rollout immediately when any of the following occurs:

- a fork, Dependabot, or `pull_request_target` job reaches `trusted-heavy`;
- a canonical cache is written outside a trusted default-branch push;
- a required-check name disappears, duplicates, or remains pending because of
  the canary;
- artifact identity, SHA, path, or trust verification fails;
- a required evidence field or stage is missing;
- the candidate has a higher unexplained failure/flake rate, or a repeated
  infrastructure failure in the same stage/class pair;
- median critical-path time regresses by more than 10%, runner queue erases the
  intended gain, or the two-slot capacity envelope is exceeded;
- rollback requires moving a tag, rewriting history, or removing security or
  release coverage;
- runner reconciliation leaves an orphan runner, lease, domain, seed, or
  overlay, or audit attribution is incomplete.

Use the hosted fallback as soon as a stop condition is met. Preserve evidence
for at least 30 days and require a fresh hosted canary before retrying.
