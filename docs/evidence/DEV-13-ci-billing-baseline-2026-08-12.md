# GitHub Actions billing report — 2026-08-12

> Historical baseline only. The automated CI billing reporting capability was
> retired in August 2026 and this evidence does not describe a live workflow.

**Status:** `degraded`  
**Organization:** `Tuinstra-DEV`  
**Generated (UTC):** `2026-08-12T18:41:16.323693Z`

> Billing facts and job-derived telemetry are deliberately separate. Job estimates are not billed truth.

## Completeness and freshness

- `incomplete` `jobs_service_error` — subtrack-site: GitHub API request failed (HTTP 404)
- `degraded` `job_rows_invalid` — 358 completed jobs lacked usable timestamps and were excluded.
- `degraded` `runner_attribution_unknown` — 263 jobs could not be attributed to hosted or self-hosted runners.
- `degraded` `current_day_partial` — The current UTC day has no documented billing freshness SLA and is explicitly partial.

## GitHub billing facts

### Current UTC billing period (2026-08-01 through 2026-08-12)

Status: `degraded`

| SKU | Category | Unit | Gross qty | Discount qty | Net qty | Qty provenance (G/D/N) | Gross USD | Discount USD | Net USD |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|
| Actions Linux | linux | Minutes | 3873 | 3219 | 654 | explicit_api_quantity/derived_from_amount/derived_from_amount | 23.238 | 19.314 | 3.924 |
| Actions storage | storage | GigabyteHours | 294.664 | 294.663 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0.099 | 0.099 | 0 |

GitHub-billed net minutes: **654** (`degraded`; summarized billing fact).

Per repository and SKU:

| Repository | Consumer | SKU | Gross qty | Discount qty | Net qty | Qty provenance (G/D/N) | Net USD |
|---|---|---|---:|---:|---:|---|---:|
| console | Other: console | Actions storage | 21.945 | 21.944 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| devops | Other: devops | Actions Linux | 49 | 49 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| devops | Other: devops | Actions storage | 0 | 0 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| gate | Gate | Actions Linux | 282 | 282 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| gate | Gate | Actions storage | 12.678 | 12.678 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| notify | Other: notify | Actions storage | 0.325 | 0.325 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| openairco-site | Other: openairco-site | Actions Linux | 10 | 10 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| openairco-site | Other: openairco-site | Actions storage | 0.001 | 0.001 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| tracker | Tracker | Actions Linux | 3158 | 2504 | 654 | explicit_api_quantity/derived_from_amount/derived_from_amount | 3.924 |
| tracker | Tracker | Actions storage | 128.189 | 128.189 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| WODIQ | WODIQ | Actions Linux | 368 | 368 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| WODIQ | WODIQ | Actions storage | 131.484 | 131.483 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| wodiq-site | Other: wodiq-site | Actions Linux | 6 | 6 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| wodiq-site | Other: wodiq-site | Actions storage | 0.042 | 0.042 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |

### Rolling 7 UTC days (2026-08-06 through 2026-08-12)

Status: `degraded`

| SKU | Category | Unit | Gross qty | Discount qty | Net qty | Qty provenance (G/D/N) | Gross USD | Discount USD | Net USD |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|
| Actions Linux | linux | Minutes | 3508 | 2854 | 654 | explicit_api_quantity/derived_from_amount/derived_from_amount | 21.048 | 17.124 | 3.924 |
| Actions storage | storage | GigabyteHours | 140.52 | 140.519 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0.047 | 0.047 | 0 |

GitHub-billed net minutes: **654** (`degraded`; summarized billing fact).

Per repository and SKU:

| Repository | Consumer | SKU | Gross qty | Discount qty | Net qty | Qty provenance (G/D/N) | Net USD |
|---|---|---|---:|---:|---:|---|---:|
| console | Other: console | Actions storage | 11.486 | 11.485 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| devops | Other: devops | Actions Linux | 49 | 49 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| devops | Other: devops | Actions storage | 0 | 0 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| gate | Gate | Actions Linux | 188 | 188 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| gate | Gate | Actions storage | 6.712 | 6.712 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| notify | Other: notify | Actions storage | 0.187 | 0.187 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| openairco-site | Other: openairco-site | Actions Linux | 10 | 10 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| openairco-site | Other: openairco-site | Actions storage | 0.001 | 0.001 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| tracker | Tracker | Actions Linux | 2893 | 2239 | 654 | explicit_api_quantity/derived_from_amount/derived_from_amount | 3.924 |
| tracker | Tracker | Actions storage | 74.488 | 74.488 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| WODIQ | WODIQ | Actions Linux | 362 | 362 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| WODIQ | WODIQ | Actions storage | 47.621 | 47.621 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| wodiq-site | Other: wodiq-site | Actions Linux | 6 | 6 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| wodiq-site | Other: wodiq-site | Actions storage | 0.024 | 0.024 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |

### Rolling 30 UTC days (2026-07-14 through 2026-08-12)

Status: `degraded`

| SKU | Category | Unit | Gross qty | Discount qty | Net qty | Qty provenance (G/D/N) | Gross USD | Discount USD | Net USD |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|
| Actions Linux | linux | Minutes | 8779.667 | 5627 | 3152.667 | explicit_api_quantity/derived_from_amount/derived_from_amount | 52.678 | 33.762 | 18.916 |
| Actions storage | storage | GigabyteHours | 665.367 | 665.362 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0.224 | 0.224 | 0 |

GitHub-billed net minutes: **3152.667** (`degraded`; summarized billing fact).

Per repository and SKU:

| Repository | Consumer | SKU | Gross qty | Discount qty | Net qty | Qty provenance (G/D/N) | Net USD |
|---|---|---|---:|---:|---:|---|---:|
| console | Other: console | Actions Linux | 70 | 0 | 70 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0.42 |
| console | Other: console | Actions storage | 81.446 | 81.445 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| devops | Other: devops | Actions Linux | 117 | 117 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| devops | Other: devops | Actions storage | 0 | 0 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| gate | Gate | Actions Linux | 413 | 288 | 125 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0.75 |
| gate | Gate | Actions storage | 63.139 | 63.138 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| marcel-site | Other: marcel-site | Actions Linux | 2 | 0 | 2 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0.012 |
| notify | Other: notify | Actions Linux | 356 | 219 | 137 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0.822 |
| notify | Other: notify | Actions storage | 0.678 | 0.677 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| openairco-site | Other: openairco-site | Actions Linux | 10 | 10 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| openairco-site | Other: openairco-site | Actions storage | 0.001 | 0.001 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| tracker | Tracker | Actions Linux | 6272.667 | 4171 | 2101.667 | explicit_api_quantity/derived_from_amount/derived_from_amount | 12.61 |
| tracker | Tracker | Actions storage | 234.389 | 234.388 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| tuinstra-site | Other: tuinstra-site | Actions Linux | 3 | 0 | 3 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0.018 |
| WODIQ | WODIQ | Actions Linux | 1519 | 816 | 703 | explicit_api_quantity/derived_from_amount/derived_from_amount | 4.218 |
| WODIQ | WODIQ | Actions storage | 285.625 | 285.624 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |
| wodiq-site | Other: wodiq-site | Actions Linux | 17 | 6 | 11 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0.066 |
| wodiq-site | Other: wodiq-site | Actions storage | 0.09 | 0.089 | 0 | explicit_api_quantity/derived_from_amount/derived_from_amount | 0 |

## Job-derived telemetry (not billing)

Status: `degraded`. These values are not GitHub-billed truth and must not be substituted for billing usage.

- Raw execution duration: **unavailable seconds**.
- Per-job rounded estimate: **unavailable minutes**.
- Self-hosted occupation (inferred): **unavailable seconds**.
- Unknown-runner duration: **unavailable seconds**.

## Top consumers and daily trend

Top consumers are ranked by observed current-period net GitHub Actions amount. The machine-readable JSON contains rankings and UTC daily trend for all three windows.

- Tracker: USD 3.924 (`degraded`)
- Other: wodiq-site: USD 0 (`degraded`)
- Other: notify: USD 0 (`degraded`)
- Gate: USD 0 (`degraded`)
- WODIQ: USD 0 (`degraded`)
- Other: console: USD 0 (`degraded`)
- Other: devops: USD 0 (`degraded`)
- Other: openairco-site: USD 0 (`degraded`)
- Other: marcel-site: USD unavailable (`degraded`)
- Other: subtrack-site: USD unavailable (`degraded`)

## Month-end projection

**LINEAR MONTH-END BURN PROJECTION (not an invoice forecast)**

- Status: `degraded`; method: `linear_daily_run_rate`; confidence: `low`.
- Projected net amount: **USD 10.137**.
- Observed current-period net amount divided by elapsed UTC calendar days, multiplied by month length.

## Evidence handling

This is a sanitized aggregate. API credentials and raw API payloads are not retained. Use the documented append-only archive command for month-over-month evidence.
