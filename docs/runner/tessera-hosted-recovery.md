# DEV-54: temporary Tessera hosted verification

## Why this separate route exists

Tessera PRs #181 and #182 repeatedly lost their Sanctuary runners. The latest
attempts completed the fixed adapter and success-marker steps, but the runner
disappeared during Flutter's post-job cache phase. GitHub subsequently failed
the job and withheld its proof; the required `quality` alias correctly failed.

- PR #181: run 43874782077 attempt 4, job 133931380577; adapter ended 09:53:37
  UTC on 17 November, manager observed the missing/stopped VM at 09:54:13,
  GitHub reported communication loss at 10:03:40.
- PR #182: run 43950356975 attempt 2, job 133931391484; adapter ended 09:55:56,
  manager observed the missing/stopped VM at 10:00:38, GitHub failed it at
  10:09:32. The Flutter post-cache step was the last running step in both jobs.

The underlying VM shutdown cause is unresolved. Host kernel logs show no OOM
event in that interval; protected QEMU/guest diagnostics need operator access.
This mitigation does not claim to repair that infrastructure defect.

## Bounded recovery contract

`reusable-tessera-hosted-verification.yml` is a separate, immutable-pinned,
Tessera-only PR entrypoint. It does not change other consumers' existing refs,
the WODIQ-only current reusable, runner group, manager, image or credentials.
The existing DEV-54 Gate/provider pilot PRs remain outside this change.

The workflow accepts only repository `Tuinstra-DEV/sudoku-tessera`, normal
`pull_request`, the fixed heavy/tessera-flutter contract and a PR merge ref.
It checks out the exact event merge SHA, verifies both parents against the
event's head/base SHAs, and runs on `ubuntu-24.04` with Contents read only and
without persisted checkout credentials or secrets. Forks and unknown actors
still run on hosted infrastructure and never receive Sanctuary trust.

The caller retains the same required quality predicate: successful reusable
call, selected hosted route, hosted proof true and no trusted proof. Source
validation and adapter failure prevent the final proof. Every post-job failure
also fails the reusable call, regardless of the earlier marker step.

Node 24, Java 17, Android platform 37.0, Flutter 3.47.1 and all action commit
pins match the previous Tessera fallback. The exact consumer adapter remains:

```sh
bash .github/ci/sanctuary-ci --strict tessera-flutter
```

That adapter still checks dependency policy, formatting, analysis, generated
code, all Flutter tests with coverage, web build/non-PWA policy and debug APK.
Flutter SDK/pub cache upload is disabled temporarily to avoid the post-job
phase implicated in the drops. Gradle download caching remains unchanged.
No check is skipped or made optional to recover CI.

## Validation and rollout

Local guard tests execute the actual workflow Python for owner/fork PRs and
reject wrong repository, contract, event, revision or runner environment.
Consumer contract tests cover missing, conflicting and unsuccessful proofs.
Provider lint/full tests and actionlint must pass before publication. Require
green provider CI, then reference the exact provider commit in a dedicated
DEV-54 Tessera CI change; no mutable tag or old SHA is overwritten.

Validate the CI caller change first, then incorporate the new develop base
normally into #181/#182 for one fresh comparison round each. A rerun of the
old immutable workflow cannot use the new provider. Keep app changes and
security checks intact. Do not repeatedly rerun failed work without new evidence.

At preparation, organization billing reported Actions compute net $0 and about
$0.079 storage. Recheck before the existing-PR verification round and stop new
runs if total spending reaches the agreed $16 (80% of $20) threshold.

## Rollback

The previous Tessera caller was
`Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@ea86c62eeac615cb292a78bf50995fed81c051ee`.
After Sanctuary has passed a healthy end-to-end canary **including post-job
completion**, restore that reference and its common-pin contract test in a
normal reviewed commit. Retain the hosted recovery commit for audit. No runner
settings, tags or image selector need rollback for this mitigation.
