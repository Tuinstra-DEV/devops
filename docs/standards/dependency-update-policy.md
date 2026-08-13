# Dependency Update and CI-Minute Policy

## Goal

Keep dependencies current without letting routine bot activity consume a
disproportionate share of GitHub Actions minutes or runner capacity.

## Normal lane

- GitHub-native Dependabot is the single version-update bot in all rollout
  repositories; Renovate is not required.
- Each package ecosystem checks monthly at an explicit, repository-staggered
  Europe/Amsterdam time.
- Patch and minor updates are grouped into one routine PR per ecosystem.
- Each ecosystem is capped at two open version PRs. This normally leaves room
  for one routine group and one major, but two majors can occupy both slots;
  the 14-day major disposition prevents indefinite routine starvation.
- Dependency PRs never automerge under this policy.
- Pending majors are reviewed monthly and must have an owner or disposition
  within 14 days.
- GitHub Actions are monitored everywhere. Newly onboarded Dockerfiles remain a
  monthly human-owned review exception because runtime changes need coordinated
  validation; Gate and Tracker retain automated Docker updates.

## Fast and controlled lanes

- Dependabot security updates use GitHub's separate security queue, so ordinary
  version limits do not block actionable fixes.
- Dependency graph, alerts and Dependabot security updates must be enabled in
  repository settings before a rollout wave is promoted.
- Major updates stay outside routine groups and require explicit human review.
- A compatibility ignore that also blocks the only secure version creates a
  manual-remediation obligation for the repository owner: triage immediately
  and open a coordinated fix within 24 hours.
- Fork and bot pull requests cannot receive trusted-heavy runner access or
  canonical cache-write permissions.

## Measurement

Use the DEV-11 billing collector for the 30 days before rollout and again for
the first complete 30-day window beginning the day after the last consumer
merge. Record total and dependency-bot workflow attempts, per-job-rounded
hosted minutes, self-hosted occupation, p50/p95 first-check completion,
dependency freshness, failure rate and flaky-rerun rate.
Reproduce the bot-specific figures with
`ruby scripts/collect-dependabot-actions-baseline.rb START_DATE END_DATE`.

A successful rollout must meet both primary thresholds:

- at least 20% fewer dependency-bot workflow run attempts in Gate and Tracker
  combined; and
- at least 15% fewer per-job-rounded dependency-bot minutes in Gate and Tracker
  combined.

From the 2026-07-13 through 2026-08-11 baseline, that means no more than 160
Gate-plus-Tracker Dependabot runs and no more than 874 per-job-rounded hosted
minutes in the comparison window.

Portfolio non-regression is mandatory: all 11 repositories combined must not
exceed the baseline of 201 Dependabot runs or 1,029 rounded hosted minutes.

For normalization, a merged dependency update is one dependency row reported
in a merged Dependabot PR's metadata; a grouped PR can therefore contain
multiple updates. If that count differs by more than 20% between windows,
normalize runs and rounded minutes per merged dependency update; both normalized
values must improve by at least 10%. The baseline contains 14 merged dependency
updates, so its normalization anchors are 14.36 runs and 73.50 rounded hosted
minutes per merged update.

Unknown runner attribution is not silently treated as zero cost. The baseline's
35 unknown jobs all had zero rounded duration, but any unknown job with positive
duration or any collector/API error in an after-window blocks promotion and
completion until it is classified or the window is recollected.

Guardrails: p95 first-check completion may worsen by no more than 10%; ordinary
dependency freshness must stay within 35 days; security-fix PR creation must
stay within 24 hours of an actionable alert; failure and flaky-rerun rates may
increase by no more than 2 percentage points.
