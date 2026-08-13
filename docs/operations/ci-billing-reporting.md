# GitHub Actions billing reporting

## Purpose and scope

`scripts/ci_billing_report.py` produces sanitized JSON and Markdown evidence for
the current UTC calendar month, rolling 7 UTC days, and rolling 30 UTC days. It
covers GitHub Actions only. Alerts, enforcement, chargeback, price negotiation,
and non-GitHub costs remain out of scope.

The authoritative billing source is the organization detailed usage endpoint:

```text
GET /organizations/{org}/settings/billing/usage?year=YYYY&month=M
X-GitHub-Api-Version: 2026-03-10
```

The command deliberately does not depend on the preview `/usage/summary`
endpoint. It fetches each calendar month needed by the 30-day interval and
aggregates detailed rows locally, so rolling windows work across month and year
boundaries. Product matching is case-insensitive, repository names may be bare
or owner-qualified, fractional quantities are preserved, and timestamps are
bucketed by their UTC date.

## Authentication and least privilege

Configure the repository Actions secret `CI_BILLING_REPORT_TOKEN` with a
fine-grained token or GitHub App credential limited to `Tuinstra-DEV` and to
read operations:

- organization **Administration: read** for enhanced billing usage;
- repository **Actions: read** and **Metadata: read** for every active consumer;
- no contents, administration, workflow, or billing write permission.

The scheduled workflow itself declares only `contents: read`. The credential is
read from the named environment variable, is used only in an HTTP Authorization
header, and is never written to a report, log, fixture, or archive. A missing or
unauthorized credential produces an explicit non-zero, incomplete report when
the command can write one; it never becomes a zero-cost report.

Manual collection uses the same path as the daily schedule:

```sh
export CI_BILLING_REPORT_TOKEN='set outside shell history where possible'
python3 scripts/ci_billing_report.py collect \
  --organization Tuinstra-DEV \
  --output-dir evidence/ci-billing
```

For an operator workstation that is already authenticated with `gh`, use the
credential-preserving mode below. It invokes `gh api` for read-only requests;
the credential remains in the system keychain and is never exported to an
environment variable, report, log, fixture, or archive:

```sh
python3 scripts/ci_billing_report.py collect \
  --organization Tuinstra-DEV \
  --output-dir evidence/ci-billing \
  --auth-mode gh-cli
```

The existing `gh` account still needs organization Administration read and
repository Actions/Metadata read access. This mode is for attended operational
collection. The scheduled workflow continues to require the dedicated,
least-privilege `CI_BILLING_REPORT_TOKEN`; it never falls back to `GITHUB_TOKEN`.
The child process is pinned to `github.com` and removes `GH_TOKEN`,
`GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, and `GITHUB_ENTERPRISE_TOKEN` from its
environment so those variables cannot silently override the reviewed keychain
identity.

The command exits `0` for complete or explicitly degraded evidence and `2` for
incomplete/unauthorized evidence or a command error. Raw API responses are held
only in process memory. Output contains aggregates, source coverage, request
counts, definitions, and issue codes—not credentials or unnecessary raw billing
or job records.

## Active consumers and absence semantics

`config/ci-billing-consumers.json` is the minimum explicit consumer inventory.
It names Tracker, WODIQ, and Gate plus the other known CI consumers. Collection
unions that file with a fully paginated active organization-repository listing.
This keeps every additional active repository as an individual `Other: <repo>`
consumer before ranking or aggregation.

If repository listing or access is partial, inventory status is degraded or
incomplete. An active repository without a detailed billing row is emitted as
`usageObservation: no_usage_observed`, with amount and quantity values `null`.
It is not emitted as a confirmed numeric zero. A real zero is shown only when a
returned billing row carries zero values.

Review and update the inventory when a consumer is added, renamed, archived, or
removed. A stale configured repository causes job collection to degrade instead
of disappearing silently.

## Metric boundaries

The report intentionally has separate sections and machine-readable provenance:

- **GitHub billing facts**: per repository, total, and Actions SKU gross,
  discount, and net quantities and USD amounts. Minute SKUs, Linux, premium OS,
  storage, and unknown future Actions SKUs remain separate. These rows are the
  summarized billed facts returned by GitHub. Amounts are explicit API facts.
  Quantity fields carry machine-readable provenance: `explicit_api_quantity`,
  `derived_from_amount`, `derived_from_quantities`, or
  `mixed_explicit_and_derived`. Detailed usage rows that omit discount/net
  quantities require reconstruction from amount divided by unit price; those
  reconstructed values are explicitly derived and are not described as direct
  API quantities.
- **Raw execution duration**: `completed_at - started_at` for completed jobs.
- **Per-job rounded estimate**: each completed GitHub-hosted job duration rounded
  up to a whole minute, then summed. It has no OS multiplier and is never
  presented as billed truth.
- **Self-hosted occupation**: job wall time inferred from the `self-hosted`
  label. Runner attribution is explicitly inferred; unknown runners remain in a
  separate metric.

Runs and jobs use `per_page=100` pagination until a short page is received.
GitHub permits rerunning a workflow for 30 days after its initial run, while the
workflow-run `created` filter refers to that initial run. Collection therefore
searches initial runs from 30 days before the earliest metric date through the
report date. It adaptively splits that UTC date range when GitHub's 1,000-result
filtered-search cap is reached. A cap that still occurs for one UTC day, or any
failed partition/page, sets rerun completeness to `not_guaranteed` and degrades
or invalidates the job source instead of silently omitting executions.

Jobs use `filter=all`, distinct job IDs preserve rerun attempts, and duplicate
job IDs from repeated pages are discarded. After the lookback search, only jobs
whose actual `started_at` UTC date is inside the requested metric range are
admitted. Jobs from old attempts outside the range are explicitly counted as
discarded. A malformed timestamp cannot be proven out of range, so it is kept
for validation and degrades telemetry. Jobs concluded as `skipped` without
runner timestamps are not counted as execution and do not create a false
telemetry error.

Every source, metric block, aggregate, window, and projection carries one of:
`complete`, `degraded`, `incomplete`, `unauthorized`, or `not_applicable`.
Unknown totals are `null`, not zero. Aggregate status inherits the worst child
status. HTTP 401 and ordinary HTTP 403 are unauthorized. HTTP 403 with
`X-RateLimit-Remaining: 0` or `Retry-After`, and all HTTP 429 responses, are
rate limits. The collector honors `Retry-After` or `X-RateLimit-Reset` when the
wait fits its 25-minute API budget inside the 30-minute workflow timeout;
otherwise it emits `rate_limited`. Exhausted rate-limit and 5xx retries are
incomplete; mixed success is degraded.

The current UTC day has no documented billing freshness SLA and is always
marked degraded/partial. Newer hosted-job telemetry than the latest billing row
adds a billing-lag issue. The month-end figure is labelled
`LINEAR MONTH-END BURN PROJECTION (not an invoice forecast)` and includes method,
assumptions, and low/medium confidence.

## Evidence, rendering, and retention

The daily workflow uploads JSON and Markdown for 90 days, enough for monthly
comparison. Artifact retention is not the durable archive. Once per month,
download the latest successful artifact and append its sanitized JSON to the
access-controlled operations archive mounted at `/srv/ci-billing-archive`:

```sh
python3 scripts/ci_billing_report.py archive \
  --report ~/Downloads/ci-billing-YYYY-MM-DD.json \
  --archive-root /srv/ci-billing-archive
```

The archive layout is
`/srv/ci-billing-archive/Tuinstra-DEV/YYYY/MM/ci-billing-YYYY-MM-DD.{json,md}`.
The command validates the schema and sensitive-field names, writes files mode
`0600`, and refuses to replace an existing date with different content. Back up
that access-controlled, append-only mount and retain at least 13 monthly
snapshots. Access and deletion should follow the normal operations change log.

Markdown can be reproduced from any retained JSON without contacting GitHub:

```sh
python3 scripts/ci_billing_report.py render \
  --input /srv/ci-billing-archive/Tuinstra-DEV/YYYY/MM/ci-billing-YYYY-MM-DD.json \
  --output /tmp/ci-billing-YYYY-MM-DD.md
```

## Operational response

- `degraded`: inspect issue codes and source/metric status. Current-day partial
  is expected; pagination, invalid rows, unknown runner attribution, or billing
  lag require follow-up.
- `incomplete` or `unauthorized`: do not use totals or projections for a cost
  decision. Correct scope/permissions, rate limiting, service availability, or
  inventory access and rerun.
- Never substitute job-rounded minutes or inferred self-hosted occupation for
  GitHub-billed minute quantities.

## Official references

- [GitHub billing usage REST API](https://docs.github.com/en/rest/billing/usage)
- [GitHub REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- [GitHub workflow rerun window](https://docs.github.com/en/actions/how-tos/manage-workflow-runs)
- [`actions/upload-artifact` releases](https://github.com/actions/upload-artifact/releases)
- [`actions/checkout` releases](https://github.com/actions/checkout/releases)
