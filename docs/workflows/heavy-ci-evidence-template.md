# Heavy CI rollout evidence template

Copy this template into the consumer repository or its delivery record for each
candidate rollout. One record covers one repository, one incumbent/candidate
pair, and one decision. Link raw GitHub run and artifact evidence; do not paste
secrets, credentials, full logs, or untrusted job payloads.

Treat the incumbent as the **before** path and the candidate as the **after**
path. The heavy-CI workflow emits stage status/timing and artifact integrity data.
Queue time, cost, retries, required-check settings, trust review, and rollback
evidence must be added at the rollout layer.

## 1. Identity and ownership

| Field | Value |
|---|---|
| Repository and default branch | |
| Classification (`heavy`, `medium`, `light`) | |
| Decision (`evidence-only`, `deferred`, `hosted-canary`, `ready`) | |
| Repository owner | |
| Fallback approver | |
| DEV story / migration scope | |
| Incumbent workflow file and full SHA | |
| Candidate workflow file and full SHA | |
| Adapter content SHA | |
| Toolchain and runner image | |
| Lockfile SHA | |
| Event and trust class | |
| Evidence window (UTC) | |
| Evidence retention expiry (minimum 30 days) | |

## 2. Predeclared hypothesis

Record before running the canary:

- affected stage(s):
- expected benefit and target percentage:
- critical-path ceiling:
- queue/capacity ceiling:
- compute or Actions-minute ceiling:
- allowed failure/flake delta:
- required-check contexts that must remain stable:
- security/isolation assertions:
- stop condition and last known-good fallback SHA:

## 3. Comparable run data

Use one row per workflow attempt. Keep reruns as separate rows. `eligible_at` is
the later of workflow creation and completion of all `needs` dependencies;
`queue_seconds = job_started_at - eligible_at`. Do not mistake concurrency or
dependency wait for runner queue.

| Path | Run / attempt | Source SHA | Trigger | Trust class | Runner class/image | Cache state/key | Eligible / started / completed UTC | Queue s | Bootstrap s | Build s | Artifact transfer s | Unit s | Integration s | E2E prepare s | Browser s | Live smoke s | Wall s | Compute s | Result | Failure/flake cause | Rerun? |
|---|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| incumbent | | | | | | hit/miss/off/unknown | | | | | | | | | | | | | | | no |
| candidate | | | | | | hit/miss/off/unknown | | | | | | | | | | | | | | | no |

Attach the heavy-CI `metrics/stages.ndjson` artifact when applicable. A cache
hit without a lower comparable stage time is not proof of acceleration.

## 4. Derived metrics

Calculate over successful first attempts only; count every attempt in failure
and flake rates.

```text
critical_path_seconds = bootstrap + build +
  max(unit, integration, live-smoke, e2e-prepare + browser)

compute_seconds = bootstrap + build + unit + integration +
  e2e-prepare + browser + live-smoke

improvement_pct =
  1 - median(candidate_critical_path) / median(incumbent_critical_path)

cache_hit_rate = cache_hits / cache_restore_eligible_runs
failure_rate = failed_attempts / all_attempts
flake_rate = nondeterministic_or_infrastructure_failures / all_attempts

estimated_cost_delta =
  candidate_compute_minutes * candidate_runner_rate -
  incumbent_compute_minutes * incumbent_runner_rate
```

If runner rates are unavailable, report hosted job-minutes and trusted-runner
occupation separately; do not invent a currency value.

| Summary | Incumbent | Candidate | Delta |
|---|---:|---:|---:|
| Valid successful first attempts | | | |
| Median critical path | | | |
| Median compute time | | | |
| Median queue time | | | |
| Cache hit rate | | | |
| Failures / all attempts | | | |
| Flakes / all attempts | | | |
| Hosted job-minutes | | | |
| Trusted-runner occupation | | | |

## 5. Sample and performance gate

- [ ] At least 5 comparable successful first attempts exist for the incumbent.
- [ ] At least 5 comparable successful first attempts exist for the candidate.
- [ ] Workflow/adapter version, trigger, stage set, lockfile, toolchain, and
      runner class are controlled or every difference is explained.
- [ ] Any cache claim has at least 3 comparable misses and 3 comparable hits.
- [ ] A p95 claim uses at least 20 valid samples; otherwise only median/range
      are reported.
- [ ] The candidate meets the predeclared target; a cache-speed claim shows at
      least 20% median improvement in the affected stage.
- [ ] Median critical path does not regress more than 10%.
- [ ] Queue time does not erase the execution gain or exceed the two-runner
      capacity envelope.
- [ ] Compute/cost and failure/flake deltas are acceptable and explained.

If a required field is unknown or too few valid samples remain, mark the result
`insufficient evidence`. Do not convert unknown values to zero or silently drop
cancelled, failed, or rerun attempts.

## 6. Required-check compatibility

- [ ] Classic branch protection and repository/organization rulesets were both
      read on the decision date.
- [ ] Current required contexts are recorded exactly, including workflow and
      caller-owned aggregate job names.
- [ ] The candidate preserves those names on every trigger and path.
- [ ] Internal matrix/fan-out jobs are not individually required.
- [ ] Report-only canary evidence is green before a new context is enforced.
- [ ] The rollback can complete without being blocked by the new context.
- [ ] Release, deploy, vulnerability, and artifact evidence remain equivalent
      or stronger.

## 7. Trust and isolation

- [ ] Fork, Dependabot, and `pull_request_target` runs resolve to hosted.
- [ ] Untrusted events cannot write canonical caches.
- [ ] Cache keys include repository, contract, trust, OS/architecture, image,
      toolchain, schema, lockfile, and adapter/content identity.
- [ ] No broad restore prefix, installed dependency tree, build output, secret,
      or generated credential is cached.
- [ ] Artifacts are same-run, immutable, identity-checked, and path-checked.
- [ ] Heavy jobs use `contents: read`, receive no inherited secrets, and have
      no package, deployment, OIDC, attestation, or security-event write scope.
- [ ] `trusted-heavy` eligibility, runner allowlist, audit attribution, and
      runner acceptance checks are recorded where applicable.

## 8. Rollback readiness and decision

- [ ] Last known-good hosted workflow and full SHA are recorded.
- [ ] The owner completed the dry-run in
      [the consumer cutover runbook](../playbooks/heavy-ci-consumer-cutover.md).
- [ ] Required-check rollback order is recorded.
- [ ] Runner-group disablement and reconciliation are understood where used.
- [ ] Evidence will be retained for at least 30 days.

Decision: `GO` / `NO-GO` / `INSUFFICIENT EVIDENCE`

Decision owner and UTC timestamp:

Rationale, residual risks, and follow-up story:
