# DEV-50 job routing rollout

The four-pool host is active. New organization routing remains disabled until a restricted runner group and its separate least-privilege credential have been verified. Existing repository-scoped trusted release runners remain available during migration.

## Proposed destination per repository

These are selected migration targets, not completed changes or measured memory profiles. Retain existing test commands, dependency services and required verdict names when converting consumers. The central workflow is an admission and verification foundation; consumer adapters, toolchain setup and same-run artifact handoff still need repository-specific implementation before jobs can move.

| Repository | Heavy | Medium | Hosted |
| --- | --- | --- | --- |
| wodiq-platform | API, Management, Worker PostgreSQL proofs | shared runtime build, core, migration, client | routing, basic/policy, security, publication |
| gate | existing release images, release scan, browser | PHP, API contract, frontend static | immutable security producer, ARM scanner, policy, deploy |
| tracker | PostgreSQL backend, verification Docker builds | frontend static | security, policy, publication, deploy |
| notify | Docker quality and applicable image validation | none initially | routing/verdict and publication/deploy |
| console | PostgreSQL PHP tests and release Docker verification | PHP lint | security/verdict and deploy |
| wodiq-app | protected Docker verification after separate release review | Linux typecheck/unit | certification and deploy |
| marcel-site, tuinstra-site, wodiq-site, openairco-site | Docker verification | Node verification | release/deploy control |
| status | Docker/PostgreSQL CI | none initially | scheduled host monitor |
| openairco | none initially | Linux Flutter verification | macOS Apple checks and security |
| devops | no self-execution on Sanctuary | no self-execution on Sanctuary | all control-plane tests and security |
| agent-lab | no active CI | no active CI | no active CI |

Two heavy slots queue WODIQ's third concurrent PostgreSQL job. Medium jobs have 3 GiB RAM and 4 GiB reserved backing; workload fit needs actual measurements. Docker quality belongs on heavy initially, but its 12 GiB reservation also needs a real guest run before broad activation.

## Required checks and consumer evidence

Reusable calls add nested job contexts. Preserve existing required check names through always-running hosted verdict jobs. Require a successful reusable-call result plus exactly one route proof matching `preflight-trusted`: `true` requires `trusted-proof=true` and an empty hosted proof; `false` requires `hosted-proof=true` and an empty trusted proof. Each route emits its literal proof only in a normal final step after the fixed adapter succeeds. Missing, duplicate, failed, cancelled or entirely skipped execution cannot satisfy the verdict. Do not replace required names with conditional jobs, or remove branch protections. WODIQ can enforce the same contract in its existing Workspace baseline rather than add a check solely for naming. There is no extra hosted finalizer, which would consume another rounded job minute.

WODIQ runtime output is bound to the synthetic PR merge SHA, run ID and run attempt. Keep its artifact name `ci-runtime-${github.sha}-${github.run_id}-${github.run_attempt}` and the receipt validation in `scripts/ci/runtime-artifact.mjs`. Preserve all suite commands, selected proof requirements, Node/PHP versions and database services when moving adapters. Missing output must fail; cross-PR build artifacts and cached test outcomes are forbidden.

## Required organization boundary

Create a new group named `sanctuary-trusted-verification`, with visibility `selected`, public access disabled, `restricted_to_workflows=true`, and exactly one selected workflow: `Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@<reviewed-full-commit-SHA>`.

The selected private repository IDs are checked in at [org-routing-repositories.json](../runner/policy/org-routing-repositories.json). Public DevOps and inactive Agent Lab are excluded. The group permits only jobs directly defined in the immutable central workflow, not arbitrary PR workflow jobs that copy a runner label. The workflow checks GitHub event and REST identities before queueing a group job; only the human `marcel-tuinstra` author/actor/triggering actor on a same-repository non-fork PR qualifies. Untrusted source retains hosted checks. There is no caller-supplied trust boolean, command string, or arbitrary runs-on input.

Do not repurpose the existing Default group or assume repository JIT group ID 1 inherits organization restrictions. Use organization JIT registration and organization runner lookup/deletion for the new group. Per-repository labels retain attribution; labels alone are not an admission boundary. The manager must verify the exact group policy, workflow commit and private repository IDs before registering anything.

The separate organization credential needs organization **Self-hosted runners: write**, plus **Actions: read** and **Pull requests: read** for the selected consumer repositories. A fine-grained token or scoped App is preferable to a classic PAT with broad `admin:org`. Keep it out of chat, source, Tracker, logs and PRs; provision it through a root-only host credential and systemd LoadCredential. It is never sent to the guest, which receives only its single-use JIT configuration. Credential creation/access expansion requires Marcel's explicit approval and interactive handling; no scope was changed by preparing this implementation.

The opt-in Ansible variables record the organization group, exact workflow reference and repository-ID map. Enabling them requires the independently provisioned `/etc/ci-runner/org-github.token` to exist as root:root mode 0600; Ansible must never receive or copy its value. A separate persistent unit drop-in adds only the `org_github_token` credential. The existing repository credential and unit stay intact. Do not run the whole runner-host provisioning role merely to activate this route: it also handles images, networks and firewall state. A targeted, recoverable activation must preserve the actual installed four-pool policy and let existing leases drain.

## Rollout order

1. Locally verify the central workflow and manager, independent review, then normal protected PR merge.
2. Prepare the exact full-SHA group configuration and obtain the required access approval. Verify the created group policy; provision the separate host credential privately.
3. Deploy optional org routing in four-pool mode with default disabled; enable only after exact API group evidence passes. Preserve a byte-identical backup and let active jobs drain.
4. Convert the four major consumers with fixed adapters and current-run artifact handoff where required, preserving suite commands and required final-check names. Run local tests before one required PR validation.
5. Convert the remaining active Linux consumers; keep security, publication, macOS and immutable ARM workflows hosted. No product release is started by this rollout.
6. Observe actual assigned jobs, Docker behavior, peak backing usage, memory reserve and successful lease cleanup. Record hosted minutes against the audit baseline; do not claim money saved before billing evidence supports it.

If organization access or a restricted workflow is missing, leave new routing disabled and keep runnable hosted consumers. Never queue medium jobs into the default unrestricted group, silently reuse the repository JIT path, or bypass required checks. Rollback restores consumer workflow pins and disables organization routing after active leases finish; existing release routing remains available.
