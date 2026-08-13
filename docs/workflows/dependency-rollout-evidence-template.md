# Dependency Policy Rollout Evidence

## Observation windows

- Baseline: YYYY-MM-DD through YYYY-MM-DD
- After: YYYY-MM-DD through YYYY-MM-DD
- Collector command/report location:
- Report SHA-256:
- Source status (billing/jobs/inventory):
- Last consumer merge date:

## Results

| Metric | Baseline | After | Delta | Guardrail/result |
| --- | ---: | ---: | ---: | --- |
| Billable GitHub Actions minutes |  |  |  | Lower |
| Dependency-bot PRs |  |  |  | Lower or equal |
| Dependency-bot CI runs |  |  |  | Lower |
| Rounded dependency-bot minutes |  |  |  | At least 15% lower |
| All-11 Dependabot CI runs | 201 |  |  | At most 201 |
| All-11 rounded hosted minutes | 1,029 |  |  | At most 1,029 |
| Merged dependency updates | 14 |  |  | Count PR metadata rows |
| CI runs per merged dependency update | 14.36 |  |  | At least 10% lower when normalized |
| Rounded minutes per merged dependency update | 73.50 |  |  | At least 10% lower when normalized |
| Unknown runner jobs / rounded duration | 35 / 0 |  |  | After must have zero positive-duration unknowns |
| PR feedback p50/p95 |  |  |  | Not worse |
| Median dependency age |  |  |  | Not worse |
| Failed runs / flaky reruns |  |  |  | Not worse |
| Pending major updates / oldest age |  |  |  | Owner or disposition within 14 days |

Definitions:

- dependency-bot run: a workflow run whose triggering actor ends in [bot] and
  whose pull request changes dependency manifests, lockfiles or action pins;
- rounded minutes: sum of each GitHub-hosted job duration rounded up to a whole
  minute; report self-hosted occupation separately and never call it billed;
- first-check completion: elapsed time from PR creation to the first terminal
  required-check set;
- freshness: days between upstream release and merged routine update;
- flaky rerun: a failed attempt followed by a successful rerun at the same SHA.
- merged dependency update: one dependency row in a merged Dependabot PR's
  metadata, including every row inside a grouped PR.
- wave projection: after the initial scheduled check and `d` complete days,
  `ceil(observed / d * 30)` for runs and rounded hosted minutes.

Pass thresholds:

- Gate plus Tracker bot workflow attempts: at least 20% lower;
- Gate plus Tracker rounded bot minutes: at least 15% lower;
- all-11 Dependabot attempts and rounded hosted minutes: no increase;
- p95 first-check completion: no more than 10% worse;
- routine dependency freshness: at most 35 days;
- actionable security alert to fix PR: at most 24 hours;
- failure and flaky-rerun rates: no increase above 2 percentage points.
- collector/API errors, unparsed dependency PRs and unknown runner jobs with
  positive duration: zero.

## Safety checks

- Security update latency:
- Security alerts blocked by compatibility ignores and manual remediation:
- Major-update visibility:
- Fork/bot trusted-runner exposure:
- Canonical cache writes from untrusted actors:

## Decision

- Keep, adjust or rollback:
- Repositories affected:
- Follow-up:

## Rollback trigger

- Guardrail breached:
- Revert commit or consumer config:
- Owner and completion time:
