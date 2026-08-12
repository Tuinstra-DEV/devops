# Dependency Policy Baseline

Captured on 2026-08-12 for the rolling window 2026-07-14 through 2026-08-12.

## Dependency pull-request bursts

| Repository | Dependabot PRs created | Configured ecosystems | Previous maximum ordinary open PRs |
| --- | ---: | ---: | ---: |
| Tuinstra-DEV/tracker | 17 | 5 | 25 |
| Tuinstra-DEV/gate | 10 | 5 | 25 |

The previous maximum is five PRs multiplied by five ecosystems. All ecosystems
ran monthly without explicit times. The other nine repositories had no matching
Dependabot or Renovate PRs in this window. A live installation check also
confirmed that no Renovate App was installed for Tuinstra-DEV, so a Renovate
rollout would not have been operational.

On 2026-08-12 the repository settings preflight found Dependabot security
updates enabled only for Gate. Dependency alerts and automated security fixes
were then enabled and API-verified for all 11 rollout repositories. No token,
alert payload or vulnerability detail is retained in this evidence.
The sanitized result is
docs/evidence/DEV-13-security-settings-preflight-2026-08-12.json (SHA-256
87466c2f39534e0d6e389ae62d11df7f6e3bd130a586bdcf2be16752043e2c6e).

## Short-job and rounding risk

Gate and Tracker have the largest required-check surfaces. Consolidating those
jobs without coordinated branch-rule changes would risk lost required contexts
and failure evidence. DEV-13 instead reduces dependency-triggered run count
while preserving job topology.

The DEV-11 collector produced
docs/evidence/DEV-13-ci-billing-baseline-2026-08-12.md (SHA-256
53a0831a1e3177a0146a68e0bdea50c57fb5c065b04569ba1685fb655f2a37ec).
The report is degraded: billing returned 3,152.667 net minutes, but its
job-derived duration, rounding and runner-attribution totals are unavailable.
One stale inventory entry (subtrack-site) returned 404. Keep that caveat in the
after comparison and do not infer job totals from the degraded collector.

A separate actor-filtered Actions extraction covers 30 complete UTC days
(2026-07-13 through 2026-08-11) and all 11 repositories:
docs/evidence/DEV-13-dependabot-actions-baseline-2026-08-11.json (SHA-256
e74277bf72634ee2e4136c9bfdcf455d8d67b34d67e13bb2eeca94352d1538a0).
It records 201 Dependabot workflow runs and 1,029 per-job-rounded hosted
minutes: Gate contributed 32 runs/122 minutes and Tracker 169 runs/907 minutes.
The other nine repositories had zero Dependabot runs. All job API requests
completed. The committed collector and invocation make the extraction
reproducible. It also found 14 merged Dependabot PRs containing 14 dependency
rows, all in Tracker; baseline normalized values are therefore 14.36 runs and
73.50 rounded hosted minutes per merged update. The 35 jobs with unknown runner
attribution all had zero rounded duration; an after-window with any
positive-duration unknown job cannot pass.

## Modelled configuration effect

| Control | Before | After |
| --- | --- | --- |
| Gate/Tracker ordinary PR concurrency | Up to 5 per ecosystem | 2 per ecosystem |
| Patch/minor shape | One PR per dependency | One grouped PR per ecosystem |
| Schedule | Unspecified monthly time | Explicit monthly stagger |
| Major visibility | Individual but unbounded | Separate, second slot, 14-day disposition |
| Security fixes | Separate GitHub queue | Unchanged separate queue |
| Runner trust | Dependabot-specific in some workflows | Generic bot boundary |

## Completion gate

DEV-13 remains Started until the staged configs have been accepted by GitHub,
the first complete 30-day after-window is recorded, both reduction thresholds
pass and no guardrail is breached.
