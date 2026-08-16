# Cross-repository heavy CI baseline and stage taxonomy

This report is the durable DEV-9 evidence record. It supersedes the first
baseline interpretation from 2026-08-11 and keeps the corrected measurements,
stage model, cache conclusions, and capacity decisions together.

The report is descriptive. It does not change required checks, consumer
workflows, runner routing, or release policy.

## Measurement rules

Compare only runs with the same workflow version, trigger, attempt policy,
enabled job set, and runner class. Record reruns as separate attempts. Do not
combine GitHub's attempt-one `created_at` with a later attempt's
`run_started_at`; that overstates queue time.

For each job record:

- repository, workflow, run ID, attempt, source SHA, trigger, and trust class;
- workflow creation, dependency completion, eligibility, job start, and job
  completion timestamps;
- runner class and image, cache key/state, stage status, and artifact identity;
- failure classification and whether an unchanged rerun passed.

Derive timing consistently:

```text
eligible_at = max(workflow_created_at, all_required_dependencies_completed_at)
queue_seconds = job_started_at - eligible_at
execution_seconds = job_completed_at - job_started_at
workflow_wall_seconds = final_required_job_completed_at - workflow_started_at
hosted_job_minutes = sum(hosted_job_execution_seconds) / 60
trusted_runner_minutes = sum(trusted_heavy_execution_seconds) / 60
```

Use successful first attempts for timing medians. Keep failed, cancelled, and
rerun attempts in failure/flake evidence. Five comparable successes are the
minimum for a p50 decision and twenty for p95. A cache effectiveness claim also
requires at least three comparable misses, three hits, and a 20% median improvement
in the affected stage.

## Shared stage taxonomy

The taxonomy separates shared platform cost from repository-owned product test
semantics. A repository may omit a stage, but it must not relabel one cost as
another to make comparisons look better.

| Stage | Start and finish boundary | Shared platform responsibility | Repository responsibility | Required evidence |
|---|---|---|---|---|
| `checkout/setup` | Job eligibility through exact source checkout and declared toolchain readiness | Runner admission, base image, checkout/action integrity | Exact source SHA, runtime versions, service declarations | eligible/start/end timestamps, runner class/image, source SHA |
| `dependency-restore/install` | First cache restore through locked dependency installation | Isolated cache mechanism and safe key dimensions | Lockfile, package manager, install command, cache contents | cache key, hit/miss/off, restore and install duration |
| `build` | First compilation/generation step through completed application or image output | Build runner and reusable orchestration | Build command and output semantics | duration, status, toolchain, output path/digest |
| `e2e-preparation` | Browser/runtime/database preparation through ready test environment | Typed `e2e-prepare` stage and runner image tools | Browser version, migrations, fixtures, local services | duration, versions, readiness result |
| `browser/test-execution` | First unit/integration/browser assertion through final deterministic result | Typed fan-out and failure evidence | Product assertions, suites, service topology | suite/stage, duration, status, failure class |
| `live-smoke` | Credential-gated external check start through attested result | Hosted isolation and evidence envelope | Provider calls, budgets, redaction, release semantics | duration, call budget, attestation, redacted result |
| `artifacts` | Artifact capture through verified downstream restoration | Immutable ID handoff, manifest, retention, digest/path verification | Artifact contents and declared path | capture/transfer duration, artifact ID/digest, retention |
| `release/deploy-gates` | Candidate resolution through release or deploy decision | Immutable workflow/reference policy | Required checks, attestations, image equivalence, readiness | candidate SHA, check contexts, decision, rollback SHA/digest |

`bootstrap` in heavy-ci/v2 is the executable combination of checkout-adjacent
repository setup and dependency installation after the shared checkout step.
It is reported against the two taxonomy rows above when comparing consumers.

## Corrected baseline summary

| Repository | Comparable population | Baseline and queue evidence | Confidence | Decision |
|---|---|---|---|---|
| `WODIQ` | Checks n=12; current release first attempts n=3 | Checks p50 1:41.5 (1:21-1:50), queue p50 2s. Release p50 35:02 (27:16-38:17), but no clean successful current series. Run 31489328375 attempt 1 ran 38:17; build eligible wait 6:55, E2E wait 3:59, hosted live wait 3s. | High for checks; medium for release | Measurement/A-B pilot only |
| `tracker` | Hosted PR quality n=8; trusted-heavy n=8, kept separate | Hosted p50 11:00 (10:34-11:31), queue p50 3s. Trusted-heavy p50 37:25 (36:31-61:25), queue p50 65s (41s-26:29). Backend integration p50 2:16 hosted versus 31:25 trusted-heavy. | High | No-go for backend cache rollout; diagnose runner |
| `gate` | Hosted CI n=5; release n=4 | CI p50 7:03 (6:19-7:19), queue p50 2s. Release p50 10:22 (10:13-10:41), queue 2-3s. | High for CI; medium for release | Conditional hosted cache experiment after fifth release |
| `console` | Successful default-branch CI; 29 successes in 40 records, with five current runs used for drivers | p50 2:21. Queue was not retained as a separate decision signal. | Medium | Later selected-contract refresh; no heavy rollout |
| `notify` | 18 CI records; 7 successful, 8 cancelled; five successful runs used for drivers | Successful p50 4:06. Cancellation/reliability intervals cannot be counted as runner queue. | Medium | Reliability before speed |
| `tuinstra-site` | Five successful CI runs | Site-class wall time approximately 27-43s; dependency install p50 8s. Queue was not a material driver. | High for light classification | Contract/pin refresh only |
| `marcel-site` | Five successful CI runs | Site-class wall time approximately 27-43s; dependency install p50 8s. Queue was not a material driver. | High for light classification | Contract/pin refresh only |
| `wodiq-site` | Five successful CI runs | Site-class wall time approximately 27-43s; build p50 12s and install p50 7s. | High for light classification | Contract/pin refresh only |
| `devops` | Five successful PR Checks runs; no representative recent main run | Test p50 10s; lint p50 1s. PR-only evidence is sufficient for the control-plane classification, not a main-run performance claim. | Medium | Hosted-only contract validation |

The original interpretation of WODIQ run 31489328375 incorrectly reported a
46:54 queue by combining timestamps from two attempts. Attempt 1 started
immediately; attempt 2 reran only live quality for about 15:10. The earlier
WODIQ release p50 also mixed workflow versions and is excluded. Hosted and
trusted-heavy Tracker runs are separate populations.

## Top three time-cost drivers

Durations are p50 step durations across comparable successful runs. Percentages
are the median share of the containing job and are not additive across parallel
jobs.

| Repository | Driver 1 | Driver 2 | Driver 3 |
|---|---|---|---|
| `WODIQ` | OpenAI workout smoke 15:25 (95%) | Playwright E2E 5:09 (64%) | Nuxt build 1:12 (47%) |
| `tracker` | Frontend browser tests 7:18 (67%) | PostgreSQL browser tests 5:18 (76%) | Backend integration 2:21 (56%); trusted-heavy p50 31:25 is separate |
| `gate` | PHP Docker image 4:11 (77%) | Gate scan runtime 3:19 (69%) | Frontend Docker image 2:41 (83%) |
| `console` | Browser quality gate 1:09 (50%) | Browser dependency install 0:29 (21%) | Container initialization 0:24 (31%) |
| `notify` | Production targets build 2:54 (74%) | Local stack build 2:51 (83%) | Isolated browser runtime 2:46 (83%) |
| `tuinstra-site` | Dependency install 0:08 (28%) | Lint 0:07 (27%) | Build 0:04 (14%) |
| `marcel-site` | Dependency install 0:08 (30%) | Lint 0:06 (24%) | Build 0:04 (15%) |
| `wodiq-site` | Build 0:12 (31%) | Test 0:09 (23%, n=4) | Dependency install 0:07 (21%) |
| `devops` | Test 0:10 (67%) | Lint 0:01 (20%) | Checkout 0:01 (8%) |

## Cache findings versus opportunities

| Repository | Proven now | Not proven / remaining opportunity |
|---|---|---|
| `WODIQ` | Exact npm restore, Nuxt cache restore, and pre-baked Playwright availability were observed | Net acceleration is not proven: npm restore still cost 24-27s, `npm ci` 30-38s, and Nuxt build 71-87s. Serial E2E/live work dominates. |
| `tracker` | pnpm cache effectiveness is consistent with short hosted installs | Composer or Playwright misses cost seconds, not the roughly 30-minute trusted-heavy backend regression. Diagnose CPU/I/O/Docker/runner configuration. |
| `gate` | Buildx is configured and the hosted baseline is stable | Cache import/export effect and scan-image equivalence are unproven. The scan-runtime image rebuild is the larger candidate. |
| `console` | Existing package-manager caches remain compatible with hosted workflows | Browser setup is the only material candidate; full heavy orchestration is not justified by a 2:21 baseline. |
| `notify` | Existing Docker layer reuse may occur on a runner | Cancellation rate, exact BuildKit telemetry, and image equivalence must be resolved before claiming a benefit. |
| `tuinstra-site` | Short dependency installation shows the existing path is already effective | No meaningful heavy-CI cache opportunity. |
| `marcel-site` | Short dependency installation shows the existing path is already effective | No meaningful heavy-CI cache opportunity. |
| `wodiq-site` | Short install/build stages show the existing path is already effective | No meaningful heavy-CI cache opportunity. |
| `devops` | PR checks are already short | Control-plane and PR CI remains hosted and never enters `trusted-heavy`. DEV-21's separately restricted scheduled/manual billing report is the only Sanctuary exception. |

A configured cache or a runtime hit is only a signal. It becomes an effective
cache only when a comparable miss/hit set proves the stage-time reduction.

## DEV-7 pilot evidence and revised targets

This section keeps the later pilot observations separate from the DEV-9
baseline populations. A single after-run is diagnostic evidence, not a new
median.

| Repository | Before | Observed pilot evidence | Hosted/shared-runner effect | Decision and revised target |
|---|---|---|---|---|
| `WODIQ` | Checks p50 1:41.5 | [Checks run 31571567705](https://github.com/Tuinstra-DEV/WODIQ/actions/runs/31571567705) completed its unit job in 1:34 and typecheck job in 1:30. Dependency installation still took 35-36s; unit execution took 42s. | Unit remained hosted; typecheck occupied the selected heavy route for about 1:30. This is routing evidence, not shared-contract evidence. | Roughly 7% wall improvement is below 30%. The Heavy CI v2 canary target is first actionable feedback within 1:11; repeated fresh-job installation is the explicit blocker to measure. |
| `tracker` | Hosted p50 11:00; all-heavy 43:24 | [Hybrid run 31521497281](https://github.com/Tuinstra-DEV/tracker/actions/runs/31521497281) completed in 8:32. Static feedback arrived 56% earlier than the all-heavy comparison. | Hosted execution was about 18.5 job-minutes versus 21 (about 12% lower); heavy-runner occupation fell from about 56:52 to 2:09 (about 96% lower). | The routing pilot meets the feedback and capacity intent. The shared-contract canary target is deterministic frontend feedback within 7:42; service-aware backend/browser replacement remains blocked. |
| `gate` | Hosted CI p50 7:03 | [PR run 31573285381, attempt 2](https://github.com/Tuinstra-DEV/gate/actions/runs/31573285381/attempts/2) completed in about 5:30. Frontend static took 2:04 and browser 3:03. | One rerun attempt does not establish hosted-minutes or queue medians; attempt and dependency eligibility remain separate fields. | Roughly 22% wall improvement is below 30%. Collect five comparable successful first attempts and prove BuildKit import/export plus scan-image equivalence before changing cache policy. |

The WODIQ and Tracker consumer canaries are additive and hosted-only. They pin
Heavy CI v2 by full commit SHA, receive no secrets, preserve required checks,
and write canonical download caches only on a trusted default-branch push.
Gate remains evidence-only until its open dependency stream settles and its
container-cache equivalence is demonstrated.

## DEV-7 final operational verification

The merged consumer changes were checked once more after the failure-evidence
and package-manager corrections. These runs prove routing, cache behavior,
artifact handoff, failure handling, and repository semantics. They do not
replace the minimum sample gate above and are not used as new medians.

| Repository | Final evidence | Cache, queue, and capacity | Failure, semantics, and rollback conclusion |
|---|---|---|---|
| `WODIQ` | [Hosted canary run 31580093134](https://github.com/Tuinstra-DEV/WODIQ/actions/runs/31580093134) passed on the failure-safe contract pin. Preflight completed in about 5s, build in 2:03, unit/typecheck fan-out in 2:11, and summary in 7s; the workflow wall interval was about 4:34. | Aggregate job queue was about 9s. The preceding canary run 31578877704 seeded the exact npm download cache; 31580093134 restored that primary key and skipped the save. Total job occupation was about 4:24, not a billing claim. | All sampled DEV-7 WODIQ runs were successful first attempts. The additive canary receives no secrets and changes no existing check or release gate. Rollback is a normal commit restoring the previous full workflow SHA; `CI_HEAVY_EXECUTION_CLASS=hosted` remains the routing fallback. The canary stage set is not comparable to the incumbent required-check set, so no 30% or Actions-minute claim is made. |
| `tracker` | Initial canary run 31578890215 failed closed because the hosted image selected the wrong pnpm version and exposed a missing failure-evidence path. DevOps [run 31579263539](https://github.com/Tuinstra-DEV/devops/actions/runs/31579263539) then passed both consumer shapes, and [Tracker run 31580953872](https://github.com/Tuinstra-DEV/tracker/actions/runs/31580953872) passed with exact pnpm 11.1.1: preflight about 3s, build 45s, frontend fan-out 2:45, and summary 7s. | The final run used hosted execution, wrote the canonical cache only from trusted `develop`, and retained payload, build evidence, unit evidence, and metrics artifacts. The existing quality workflow remains the measured production path; its roughly 12% hosted-minute and 96% heavy-runner-occupation reductions remain the portfolio's defensible cost result. | PRs #86 and #136 preserve failed-stage evidence and exact toolchain selection. Existing backend and service-aware browser semantics remain outside the additive canary. Manual hosted rollback run 31522407850 bypassed `trusted-heavy`; its unchanged failed-job retry passed. |
| `gate` | [PR run 31573285381 attempt 2](https://github.com/Tuinstra-DEV/gate/actions/runs/31573285381/attempts/2) passed before merge: approximately 5:30 wall, frontend static 2:04, frontend browser 3:03, and the caller-owned frontend aggregate green. | The critical frontend-static queue was about 2s and the downstream browser queue about 3s. Composer restored successfully and frontend static reported a primary-key pnpm cache hit. One passing rerun cannot establish an Actions-minute or queue median. | The aggregate frontend context, backend/API coverage, and release semantics were preserved. Earlier failed/cancelled attempts remain part of the reliability evidence. Hosted routing was observed, but no separate Gate rollback exercise exists; the documented fallback is `CI_HEAVY_EXECUTION_CLASS=hosted`. Gate therefore remains evidence-only until five comparable successful first attempts and BuildKit/image-equivalence proof exist. |

GitHub's sampled billing endpoint did not provide non-zero billable minutes for
the WODIQ and Gate evidence. Runner occupation above is calculated from job
timestamps and is labelled separately. WODIQ and Gate therefore satisfy the
documented compatibility, trust, cache-signal, failure, and revised-target
parts of the pilot; Tracker supplies the measured Actions-minute and capacity
reduction. No repository is promoted to a broader rollout on these single-run
observations.

## Pilot decisions, order, and capacity envelope

1. **WODIQ measurement:** capture attempt, concurrency, dependency eligibility,
   runner start, execution, and cache evidence over at least five same-SHA paired
   trials. Do not change live-smoke or required-check policy.
2. **Gate hosted cache proof:** collect a fifth comparable release plus explicit
   BuildKit import/export telemetry and prove scan-image equivalence. Only
   trusted main/tag work may write a canonical cache.
3. **Tracker runner diagnosis:** run at least five same-SHA hosted and five
   trusted-heavy trials. Keep backend hosted if the greater-than-tenfold gap
   persists; do not call that a cache problem.
4. **Console and Notify:** consume only proven contracts after their repository
   blockers are resolved. Sites refresh immutable pins only.

Sanctuary admits at most two isolated jobs, each with 4 vCPU and 6,144 MiB RAM.
The 4,096 MiB host reserve, load ceiling, disk threshold, and two-slot maximum
are hard admission controls. A rollout stops when runner queue erases the
execution gain, a third concurrent job is needed for the claimed target, or any
trust/cache/artifact isolation check fails.

Never promote fork or Dependabot caches or artifacts into privileged,
self-hosted, release, or deploy paths. Keep full-SHA pins, minimal permissions,
stable required-check names, vulnerability freshness, and the previous hosted
caller as the rollback path.

## Final conclusion

Caching is secondary for WODIQ and Tracker. WODIQ is dominated by serial E2E
and live-smoke work; Tracker has a severe trusted-runner backend regression.
Gate has the cleanest stable container baseline for a hosted cache experiment.
The evidence-backed order is WODIQ measurement, Gate hosted cache proof, then
Tracker runner diagnosis—not a blanket migration to `trusted-heavy`.
