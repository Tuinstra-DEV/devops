#!/usr/bin/env python3
"""Generate sanitized GitHub Actions billing and job-telemetry reports."""

from __future__ import annotations

import argparse
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Iterable
import urllib.error
import urllib.parse
import urllib.request


SCHEMA_VERSION = "devops.ci-billing-report/v1"
DEFAULT_API_VERSION = "2026-03-10"
RERUN_LOOKBACK_DAYS = 30
DEFAULT_API_TIME_BUDGET_SECONDS = 25 * 60
GH_TOKEN_OVERRIDE_ENV = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
STATUS_ORDER = {
    "not_applicable": -1,
    "complete": 0,
    "degraded": 1,
    "incomplete": 2,
    "unauthorized": 3,
}
SECRET_KEY_RE = re.compile(r"token|authorization|credential|secret", re.IGNORECASE)
DEFAULT_CONSUMER_INVENTORY = Path(__file__).parents[1] / "config" / "ci-billing-consumers.json"


class ReportError(RuntimeError):
    pass


class NotActions(ReportError):
    pass


class SourceFailure(ReportError):
    def __init__(self, code: str, status: str, message: str, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.http_status = http_status


class PaginationFailure(SourceFailure):
    def __init__(self, failure: SourceFailure, partial: list[dict[str, Any]], pages: int):
        super().__init__(failure.code, failure.status, str(failure), failure.http_status)
        self.partial = partial
        self.pages = pages


def issue(code: str, severity: str, source: str, message: str) -> dict[str, str]:
    if severity not in STATUS_ORDER:
        raise ReportError(f"invalid status: {severity}")
    return {"code": code, "severity": severity, "source": source, "message": message}


def worst_status(*statuses: str) -> str:
    relevant = [status for status in statuses if status != "not_applicable"]
    if not relevant:
        return "not_applicable"
    return max(relevant, key=lambda status: STATUS_ORDER[status])


def report_status(*statuses: str) -> str:
    status = worst_status(*statuses)
    return "incomplete" if status == "unauthorized" else status


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ReportError("timestamp is missing")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReportError("timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decimal_value(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ReportError(f"{field} is missing")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ReportError(f"{field} is invalid") from exc
    if not result.is_finite():
        raise ReportError(f"{field} is invalid")
    return result


def optional_decimal(item: dict[str, Any], field: str) -> Decimal | None:
    return decimal_value(item[field], field) if field in item and item[field] is not None else None


def repository_name(value: Any, organization: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unattributed"
    name = value.strip()
    owner, separator, repo = name.partition("/")
    if separator and owner.casefold() == organization.casefold():
        return repo
    return name


def sku_category(sku: str) -> str:
    lowered = sku.casefold()
    if "storage" in lowered:
        return "storage"
    if "linux" in lowered:
        return "linux"
    if "windows" in lowered or "macos" in lowered or "mac os" in lowered:
        return "premium_os"
    return "other_actions"


def normalize_billing_item(item: dict[str, Any], organization: str) -> dict[str, Any]:
    product = item.get("product")
    if not isinstance(product, str) or product.casefold() != "actions":
        raise NotActions("non-Actions billing item")
    sku = item.get("sku")
    unit_type = item.get("unitType")
    if not isinstance(sku, str) or not sku or not isinstance(unit_type, str) or not unit_type:
        raise ReportError("billing SKU or unit type is missing")

    price = decimal_value(item.get("pricePerUnit"), "pricePerUnit")
    gross_amount = decimal_value(item.get("grossAmount"), "grossAmount")
    discount_amount = decimal_value(item.get("discountAmount"), "discountAmount")
    net_amount = decimal_value(item.get("netAmount"), "netAmount")
    gross_quantity = optional_decimal(item, "grossQuantity")
    if gross_quantity is None:
        gross_quantity = decimal_value(item.get("quantity"), "quantity")
    gross_quantity_provenance = "explicit_api_quantity"
    discount_quantity = optional_decimal(item, "discountQuantity")
    discount_quantity_provenance = (
        "explicit_api_quantity" if discount_quantity is not None else "unavailable"
    )
    net_quantity = optional_decimal(item, "netQuantity")
    net_quantity_provenance = (
        "explicit_api_quantity" if net_quantity is not None else "unavailable"
    )
    if discount_quantity is None:
        if price:
            discount_quantity = discount_amount / price
            discount_quantity_provenance = "derived_from_amount"
        elif not discount_amount:
            discount_quantity = Decimal(0)
            discount_quantity_provenance = "derived_from_amount"
    if net_quantity is None:
        if price:
            net_quantity = net_amount / price
            net_quantity_provenance = "derived_from_amount"
        elif discount_quantity is not None:
            net_quantity = gross_quantity - discount_quantity
            net_quantity_provenance = "derived_from_quantities"

    return {
        "date": parse_timestamp(item.get("date")).date(),
        "product": "Actions",
        "sku": sku,
        "category": sku_category(sku),
        "unit_type": unit_type,
        "repository": repository_name(item.get("repositoryName"), organization),
        "price_per_unit": price,
        "gross_quantity": gross_quantity,
        "gross_quantity_provenance": gross_quantity_provenance,
        "discount_quantity": discount_quantity,
        "discount_quantity_provenance": discount_quantity_provenance,
        "net_quantity": net_quantity,
        "net_quantity_provenance": net_quantity_provenance,
        "gross_amount": gross_amount,
        "discount_amount": discount_amount,
        "net_amount": net_amount,
    }


def infer_runner_type(job: dict[str, Any]) -> str:
    explicit = job.get("runner_type")
    if explicit in {"github_hosted", "self_hosted", "unknown"}:
        return explicit
    labels = {
        str(label).casefold()
        for label in job.get("labels", [])
        if isinstance(label, str)
    }
    if "self-hosted" in labels:
        return "self_hosted"
    runner_name = str(job.get("runner_name") or "").casefold()
    hosted_prefixes = ("ubuntu", "windows", "macos", "mac os")
    if runner_name.startswith("github actions") or any(
        label.startswith(hosted_prefixes) for label in labels
    ):
        return "github_hosted"
    return "unknown"


def job_identity(job: dict[str, Any]) -> str:
    job_id = job.get("job_id", job.get("id"))
    if isinstance(job_id, int) and not isinstance(job_id, bool):
        return f"id:{job_id}"
    fields = (
        job.get("repository"), job.get("run_id"), job.get("run_attempt"),
        job.get("name"), job.get("started_at"), job.get("completed_at"),
    )
    return "shape:" + hashlib.sha256(repr(fields).encode("utf-8")).hexdigest()


def normalize_jobs(jobs: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates = 0
    invalid = 0
    for job in jobs:
        if job.get("conclusion") == "skipped" and not job.get("started_at") \
                and not job.get("completed_at"):
            continue
        identity = job_identity(job)
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        try:
            started = parse_timestamp(job.get("started_at"))
            completed = parse_timestamp(job.get("completed_at"))
            duration = int((completed - started).total_seconds())
            if duration < 0:
                raise ReportError("job completion precedes start")
        except ReportError:
            invalid += 1
            continue
        normalized.append({
            "identity": identity,
            "repository": str(job.get("repository") or "unattributed"),
            "date": started.date(),
            "started_at": started,
            "duration_seconds": duration,
            "runner_type": infer_runner_type(job),
            "run_id": job.get("run_id"),
            "run_attempt": job.get("run_attempt", 1),
        })
    return normalized, duplicates, invalid


def consumer_for(repository: str) -> str:
    leaf = repository.rsplit("/", 1)[-1].casefold()
    if leaf == "tracker":
        return "Tracker"
    if leaf == "wodiq":
        return "WODIQ"
    if leaf == "gate":
        return "Gate"
    return f"Other: {repository}"


def number(value: Decimal | int | float | None) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value.normalize())
    return value


def sum_optional(items: Iterable[dict[str, Any]], field: str) -> Decimal | None:
    values = [item[field] for item in items]
    if any(value is None for value in values):
        return None
    return sum(values, Decimal(0))


def metric(status: str, value: Decimal | int | float | None, unit: str,
           provenance: str, *, values_complete: bool = True) -> dict[str, Any]:
    result = {"status": status, "value": number(value) if values_complete else None,
              "unit": unit, "provenance": provenance}
    if not values_complete and value is not None:
        result["observedValue"] = number(value)
    return result


def aggregate_quantity_provenance(items: Iterable[dict[str, Any]], field: str) -> str:
    provenances = {str(item[f"{field}_provenance"]) for item in items}
    if len(provenances) == 1:
        return provenances.pop()
    return "mixed_explicit_and_derived"


def billing_quantity_metric_provenance(items: list[dict[str, Any]], field: str) -> str:
    origin = aggregate_quantity_provenance(items, field) if items else "no_usage_observed"
    return (
        "GitHub enhanced billing usage API summarized quantity; "
        f"quantityProvenance={origin}"
    )


def billing_row(items: list[dict[str, Any]], status: str, values_complete: bool) -> dict[str, Any]:
    first = items[0]
    return {
        "status": status,
        "sku": first["sku"],
        "category": first["category"],
        "unitType": first["unit_type"],
        "grossQuantity": number(sum_optional(items, "gross_quantity")) if values_complete else None,
        "discountQuantity": number(sum_optional(items, "discount_quantity")) if values_complete else None,
        "netQuantity": number(sum_optional(items, "net_quantity")) if values_complete else None,
        "quantityProvenance": {
            "gross": aggregate_quantity_provenance(items, "gross_quantity"),
            "discount": aggregate_quantity_provenance(items, "discount_quantity"),
            "net": aggregate_quantity_provenance(items, "net_quantity"),
        },
        "grossAmount": number(sum_optional(items, "gross_amount")) if values_complete else None,
        "discountAmount": number(sum_optional(items, "discount_amount")) if values_complete else None,
        "netAmount": number(sum_optional(items, "net_amount")) if values_complete else None,
    }


def aggregate_billing(items: list[dict[str, Any]], status: str,
                      values_complete: bool,
                      active_repositories: Iterable[str] = ()) -> dict[str, Any]:
    by_sku: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_sku[(item["sku"], item["unit_type"])].append(item)
        by_repo[item["repository"]].append(item)

    totals_by_sku = [
        billing_row(group, status, values_complete)
        for _key, group in sorted(by_sku.items())
    ]
    repositories = []
    for repository, repo_items in sorted(by_repo.items()):
        repo_skus: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in repo_items:
            repo_skus[(item["sku"], item["unit_type"])].append(item)
        repositories.append({
            "status": status,
            "repository": repository,
            "consumer": consumer_for(repository),
            "usageObservation": "usage_rows_observed",
            "amounts": {
                name: metric(status, sum_optional(repo_items, field), "USD",
                             "GitHub enhanced billing usage API",
                             values_complete=values_complete)
                for name, field in (("gross", "gross_amount"),
                                    ("discount", "discount_amount"),
                                    ("net", "net_amount"))
            },
            "bySku": [billing_row(group, status, values_complete)
                      for _key, group in sorted(repo_skus.items())],
        })

    observed_names = {row["repository"].casefold() for row in repositories}
    absence_provenance = (
        "Complete active-repository inventory contained no matching detailed billing row; "
        "this is no usage observed, not a confirmed numeric zero"
    )
    for repository in sorted(set(active_repositories), key=str.casefold):
        if repository.casefold() in observed_names:
            continue
        repositories.append({
            "status": status,
            "repository": repository,
            "consumer": consumer_for(repository),
            "usageObservation": "no_usage_observed",
            "amounts": {
                name: metric(status, None, "USD", absence_provenance)
                for name in ("gross", "discount", "net")
            },
            "bySku": [],
        })
    repositories.sort(key=lambda row: row["repository"].casefold())

    billed_minute_items = [item for item in items if item["unit_type"].casefold() == "minutes"]
    provenance = "GitHub enhanced billing usage API; authoritative summarized billing fact"
    return {
        "status": status,
        "scope": "Actions rows observed through report generation time",
        "usageObservation": "usage_rows_observed" if items else "no_usage_observed",
        "totalsBySku": totals_by_sku,
        "repositories": repositories,
        "amounts": {
            name: metric(status, sum_optional(items, field), "USD", provenance,
                         values_complete=values_complete)
            for name, field in (
                ("gross", "gross_amount"),
                ("discount", "discount_amount"),
                ("net", "net_amount"),
            )
        },
        "githubBilledMinutes": {
            "gross": metric(status, sum_optional(billed_minute_items, "gross_quantity"),
                            "minutes", billing_quantity_metric_provenance(
                                billed_minute_items, "gross_quantity"),
                            values_complete=values_complete),
            "discount": metric(status, sum_optional(billed_minute_items, "discount_quantity"),
                               "minutes", billing_quantity_metric_provenance(
                                   billed_minute_items, "discount_quantity"),
                               values_complete=values_complete),
            "net": metric(status, sum_optional(billed_minute_items, "net_quantity"),
                          "minutes", billing_quantity_metric_provenance(
                              billed_minute_items, "net_quantity"),
                          values_complete=values_complete),
        },
    }


def aggregate_jobs(jobs: list[dict[str, Any]], status: str) -> dict[str, Any]:
    values_complete = status == "complete"
    raw = sum(job["duration_seconds"] for job in jobs)
    rounded = sum(math.ceil(job["duration_seconds"] / 60)
                  for job in jobs if job["runner_type"] == "github_hosted")
    self_hosted = sum(job["duration_seconds"]
                      for job in jobs if job["runner_type"] == "self_hosted")
    unknown = sum(job["duration_seconds"] for job in jobs if job["runner_type"] == "unknown")
    attempts = {
        (job.get("run_id"), job.get("run_attempt"))
        for job in jobs if job.get("run_id") is not None
    }
    provenance = "GitHub Actions jobs API; job-derived and not a billing source"

    def job_metric(value: int, unit: str) -> dict[str, Any]:
        return metric(status, value, unit, provenance, values_complete=values_complete)

    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        by_repo[job["repository"]].append(job)
    repositories = []
    for repository, repo_jobs in sorted(by_repo.items()):
        repo_aggregate = aggregate_jobs(repo_jobs, status) if len(by_repo) > 1 else None
        if repo_aggregate is None:
            repo_raw = sum(job["duration_seconds"] for job in repo_jobs)
            repo_rounded = sum(math.ceil(job["duration_seconds"] / 60)
                               for job in repo_jobs if job["runner_type"] == "github_hosted")
            repo_self = sum(job["duration_seconds"]
                            for job in repo_jobs if job["runner_type"] == "self_hosted")
            repo_totals = {
                "rawExecutionDuration": job_metric(repo_raw, "seconds"),
                "perJobRoundedEstimate": job_metric(repo_rounded, "minutes"),
                "selfHostedOccupation": job_metric(repo_self, "seconds"),
            }
        else:
            repo_totals = {
                key: repo_aggregate["totals"][key]
                for key in ("rawExecutionDuration", "perJobRoundedEstimate", "selfHostedOccupation")
            }
        repositories.append({"status": status, "repository": repository,
                             "consumer": consumer_for(repository), "metrics": repo_totals})

    return {
        "status": status,
        "warning": "These values are not GitHub-billed truth and must not be substituted for billing usage.",
        "runnerAttribution": "inferred from job labels and runner_name; self-hosted occupation is inferred",
        "roundingMethod": "ceil each completed GitHub-hosted job raw duration to whole minutes; no SKU multiplier",
        "totals": {
            "rawExecutionDuration": job_metric(raw, "seconds"),
            "perJobRoundedEstimate": job_metric(rounded, "minutes"),
            "selfHostedOccupation": job_metric(self_hosted, "seconds"),
            "unknownRunnerDuration": job_metric(unknown, "seconds"),
            "completedJobs": job_metric(len(jobs), "jobs"),
            "workflowRunAttemptsIncluded": job_metric(len(attempts), "attempts"),
        },
        "repositories": repositories,
    }


def date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def window_payload(name: str, start: date, end: date, billing: list[dict[str, Any]],
                   jobs: list[dict[str, Any]], billing_status: str, job_status: str,
                   billing_values_complete: bool,
                   active_repositories: list[str], inventory_status: str) -> dict[str, Any]:
    window_billing = [item for item in billing if start <= item["date"] <= end]
    window_jobs = [job for job in jobs if start <= job["date"] <= end]
    window_billing_values_complete = billing_values_complete and bool(window_billing)
    facts = aggregate_billing(window_billing, billing_status, window_billing_values_complete,
                              active_repositories)
    telemetry = aggregate_jobs(window_jobs, job_status)

    consumer_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in window_billing:
        consumer_items[consumer_for(item["repository"])].append(item)
    top_consumers = []
    for consumer, rows in consumer_items.items():
        amount = sum_optional(rows, "net_amount")
        top_consumers.append({
            "status": billing_status,
            "consumer": consumer,
            "netAmount": number(amount) if billing_values_complete else None,
        })
    observed_consumers = {row["consumer"] for row in top_consumers}
    for repository in active_repositories:
        consumer = consumer_for(repository)
        if consumer in observed_consumers:
            continue
        top_consumers.append({
            "status": billing_status,
            "consumer": consumer,
            "repository": repository,
            "usageObservation": "no_usage_observed",
            "netAmount": None,
        })
        observed_consumers.add(consumer)
    top_consumers.sort(key=lambda row: (row["netAmount"] is not None,
                                        row["netAmount"] or 0), reverse=True)

    daily = []
    for day in date_range(start, end):
        day_billing = [item for item in window_billing if item["date"] == day]
        day_jobs = [job for job in window_jobs if job["date"] == day]
        day_facts = aggregate_billing(
            day_billing, billing_status, billing_values_complete and bool(day_billing))
        day_telemetry = aggregate_jobs(day_jobs, job_status)
        daily.append({
            "date": day.isoformat(),
            "status": report_status(billing_status, job_status, inventory_status),
            "billingObservation": day_facts["usageObservation"],
            "billingNetAmount": day_facts["amounts"]["net"],
            "githubBilledNetMinutes": day_facts["githubBilledMinutes"]["net"],
            "rawExecutionDuration": day_telemetry["totals"]["rawExecutionDuration"],
            "selfHostedOccupation": day_telemetry["totals"]["selfHostedOccupation"],
        })
    return {
        "label": name,
        "status": report_status(billing_status, job_status, inventory_status),
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "billingFacts": facts,
        "jobDerivedMetrics": telemetry,
        "topConsumers": top_consumers,
        "dailyTrend": daily,
    }


def build_report(*, organization: str, billing_items: list[dict[str, Any]],
                 job_records: list[dict[str, Any]], as_of: date, generated_at: datetime,
                 billing_source: dict[str, Any], job_source: dict[str, Any],
                 source_issues: list[dict[str, str]]) -> dict[str, Any]:
    normalized_billing: list[dict[str, Any]] = []
    invalid_billing = 0
    for raw in billing_items:
        try:
            normalized_billing.append(normalize_billing_item(raw, organization))
        except NotActions:
            continue
        except ReportError:
            invalid_billing += 1

    jobs, duplicates, invalid_jobs = normalize_jobs(job_records)
    billing_source = dict(billing_source)
    job_source = dict(job_source)
    issues = list(source_issues)
    billing_status = billing_source.get("status", "incomplete")
    job_status = job_source.get("status", "incomplete")

    if invalid_billing:
        billing_status = worst_status(billing_status, "degraded")
        issues.append(issue("billing_rows_invalid", "degraded", "billing",
                            f"{invalid_billing} Actions billing rows were invalid and excluded."))
    if invalid_jobs:
        job_status = worst_status(job_status, "degraded")
        issues.append(issue("job_rows_invalid", "degraded", "jobs",
                            f"{invalid_jobs} completed jobs lacked usable timestamps and were excluded."))
    unknown_jobs = sum(job["runner_type"] == "unknown" for job in jobs)
    if unknown_jobs:
        job_status = worst_status(job_status, "degraded")
        issues.append(issue("runner_attribution_unknown", "degraded", "jobs",
                            f"{unknown_jobs} jobs could not be attributed to hosted or self-hosted runners."))

    generated_utc = generated_at.astimezone(timezone.utc)
    if as_of >= generated_utc.date():
        billing_status = worst_status(billing_status, "degraded")
        issues.append(issue(
            "current_day_partial", "degraded", "billing",
            "The current UTC day has no documented billing freshness SLA and is explicitly partial.",
        ))

    latest_billing = max((item["date"] for item in normalized_billing), default=None)
    latest_hosted_job = max((job["date"] for job in jobs
                             if job["runner_type"] == "github_hosted"), default=None)
    if latest_hosted_job and (latest_billing is None or latest_hosted_job > latest_billing):
        billing_status = worst_status(billing_status, "degraded")
        issues.append(issue("billing_lags_job_telemetry", "degraded", "billing",
                            "Completed hosted jobs are newer than the latest Actions billing row."))

    successful_billing_requests = int(billing_source.get(
        "successfulRequests",
        max(int(billing_source.get("requests", 0)) - int(billing_source.get("failedRequests", 0)), 0),
    ))
    if not normalized_billing and successful_billing_requests:
        issues.append(issue("no_actions_usage_observed", "complete", "billing",
                            "The successful API response contained no Actions rows; reported zeros mean no rows observed."))

    billing_source.update({
        "status": billing_status,
        "actionsItemsAccepted": len(normalized_billing),
        "invalidActionsItems": invalid_billing,
        "latestUsageDateUtc": latest_billing.isoformat() if latest_billing else None,
        "currentUtcDayPartial": as_of >= generated_utc.date(),
    })
    job_source.update({
        "status": job_status,
        "completedJobsAccepted": len(jobs),
        "duplicateJobsDiscarded": duplicates,
        "invalidJobsDiscarded": invalid_jobs,
        "runnerAttribution": "inferred",
    })

    inventory_raw = job_source.get("activeRepositories", [])
    if isinstance(inventory_raw, list) and all(isinstance(repo, str) for repo in inventory_raw):
        active_repositories = []
        seen_repositories: set[str] = set()
        for repository in inventory_raw:
            canonical = repository_name(repository, organization)
            if canonical.casefold() not in seen_repositories:
                active_repositories.append(canonical)
                seen_repositories.add(canonical.casefold())
        inventory_status = "complete" if job_source.get("repositoryListingComplete", True) \
            else "degraded"
    else:
        active_repositories = []
        inventory_status = "incomplete"
    if not active_repositories:
        for repository in sorted({item["repository"] for item in normalized_billing}.union(
                job["repository"] for job in jobs), key=str.casefold):
            active_repositories.append(repository)
        if active_repositories and inventory_status == "complete":
            inventory_status = "degraded"

    period_start = as_of.replace(day=1)
    starts = {
        "currentBillingPeriod": period_start,
        "rolling7Days": as_of - timedelta(days=6),
        "rolling30Days": as_of - timedelta(days=29),
    }
    billing_values_complete = bool(normalized_billing) and \
        billing_status in {"complete", "degraded"} and \
        int(billing_source.get("failedRequests", 0)) == 0 and invalid_billing == 0
    windows = {
        key: window_payload(label, start, as_of, normalized_billing, jobs,
                            billing_status, job_status, billing_values_complete,
                            active_repositories, inventory_status)
        for key, start, label in (
            ("currentBillingPeriod", starts["currentBillingPeriod"], "Current UTC billing period"),
            ("rolling7Days", starts["rolling7Days"], "Rolling 7 UTC days"),
            ("rolling30Days", starts["rolling30Days"], "Rolling 30 UTC days"),
        )
    }

    current_net = windows["currentBillingPeriod"]["billingFacts"]["amounts"]["net"]["value"]
    days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
    projection_status = billing_status
    projected = None
    if current_net is not None and projection_status in {"complete", "degraded"}:
        projected = Decimal(str(current_net)) / Decimal(as_of.day) * Decimal(days_in_month)
    projection = {
        "label": "LINEAR MONTH-END BURN PROJECTION (not an invoice forecast)",
        "status": projection_status,
        "method": "linear_daily_run_rate",
        "confidence": "low" if projection_status != "complete" or as_of.day < 7 else "medium",
        "currency": "USD",
        "observedNetAmount": current_net,
        "elapsedCalendarDays": as_of.day,
        "calendarDaysInMonth": days_in_month,
        "projectedNetAmount": number(projected),
        "assumptions": "Observed current-period net amount divided by elapsed UTC calendar days, multiplied by month length.",
    }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": report_status(billing_status, job_status, inventory_status),
        "organization": organization,
        "generatedAt": generated_utc.isoformat().replace("+00:00", "Z"),
        "asOfDateUtc": as_of.isoformat(),
        "scope": "GitHub Actions only; excludes alerts, enforcement, chargeback, and non-GitHub costs",
        "sources": {
            "billing": billing_source,
            "jobs": job_source,
            "consumerInventory": {
                "status": inventory_status,
                "provenance": "GitHub active organization repository inventory",
                "activeRepositories": active_repositories,
                "absenceSemantics": "No detailed billing row is no_usage_observed, never confirmed zero.",
            },
        },
        "issues": issues,
        "metricDefinitions": {
            "githubBilledMinutes": "Actions billing quantity for minute rows; direct API quantities are explicit, while quantities reconstructed from amount and unit price are marked derived in provenance.",
            "rawExecutionDuration": "Completed-at minus started-at from the jobs API, before minute rounding.",
            "perJobRoundedEstimate": "Each completed GitHub-hosted job duration rounded up independently; estimate only, with no SKU multiplier.",
            "selfHostedOccupation": "Raw job wall time inferred as self-hosted from labels; not a GitHub billing amount.",
        },
        "windows": windows,
        "burnProjection": projection,
    }


class GitHubClient:
    def __init__(self, token: str, api_url: str, api_version: str,
                 opener: Callable[..., Any] = urllib.request.urlopen,
                 sleeper: Callable[[float], None] = time.sleep,
                 wall_clock: Callable[[], float] = time.time,
                 monotonic_clock: Callable[[], float] = time.monotonic,
                 max_elapsed_seconds: int = DEFAULT_API_TIME_BUDGET_SECONDS):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.api_version = api_version
        self.opener = opener
        self.sleeper = sleeper
        self.wall_clock = wall_clock
        self.monotonic_clock = monotonic_clock
        self.max_elapsed_seconds = max_elapsed_seconds
        self.started_monotonic = monotonic_clock()

    @staticmethod
    def rate_limit_delay(http_status: int, headers: dict[str, str], now: float) -> int | None:
        remaining = headers.get("x-ratelimit-remaining")
        retry_after = headers.get("retry-after")
        is_rate_limited = http_status == 429 or remaining == "0" or \
            (http_status == 403 and retry_after is not None)
        if not is_rate_limited:
            return None
        if retry_after and retry_after.isdigit():
            return max(1, int(retry_after))
        reset = headers.get("x-ratelimit-reset")
        if remaining == "0" and reset and reset.isdigit():
            return max(1, math.ceil(int(reset) - now) + 1)
        return 60

    def wait_for_rate_limit(self, delay: int, http_status: int) -> None:
        elapsed = self.monotonic_clock() - self.started_monotonic
        if delay > self.max_elapsed_seconds - elapsed:
            raise SourceFailure(
                "rate_limited", "incomplete",
                f"GitHub API rate-limit wait exceeds the bounded collection budget "
                f"(HTTP {http_status})", http_status,
            )
        self.sleeper(delay)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.api_url}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "devops-ci-billing-report/1",
        })
        for attempt in range(3):
            try:
                with self.opener(request, timeout=30) as response:
                    payload = json.load(response)
                return payload
            except urllib.error.HTTPError as exc:
                headers = {
                    str(key).casefold(): str(value)
                    for key, value in (exc.headers.items() if exc.headers else [])
                }
                delay = self.rate_limit_delay(exc.code, headers, self.wall_clock())
                exc.close()
                if delay is not None:
                    if attempt < 2:
                        self.wait_for_rate_limit(delay, exc.code)
                        continue
                    raise SourceFailure(
                        "rate_limited", "incomplete",
                        f"GitHub API remained rate limited after bounded retries "
                        f"(HTTP {exc.code})", exc.code,
                    ) from None
                if exc.code in {401, 403}:
                    raise SourceFailure("unauthorized", "unauthorized",
                                        f"GitHub API request was unauthorized (HTTP {exc.code})", exc.code) from None
                retryable = 500 <= exc.code <= 599
                if retryable and attempt < 2:
                    self.sleeper(2 ** attempt)
                    continue
                raise SourceFailure("service_error", "incomplete",
                                    f"GitHub API request failed (HTTP {exc.code})", exc.code) from None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt < 2:
                    self.sleeper(2 ** attempt)
                    continue
                raise SourceFailure("transport_error", "incomplete",
                                    f"GitHub API request failed: {type(exc).__name__}") from None
        raise AssertionError("unreachable")

    def get_paginated(self, path: str, key: str,
                      params: dict[str, Any] | None = None,
                      accessible_cap: int | None = None) -> tuple[list[dict[str, Any]], int]:
        records: list[dict[str, Any]] = []
        base = dict(params or {})
        base["per_page"] = 100
        for page in range(1, 1001):
            query = dict(base)
            query["page"] = page
            try:
                payload = self.get(path, query)
            except SourceFailure as exc:
                raise PaginationFailure(exc, records, page - 1) from None
            page_records = payload if key == "__root__" else (
                payload.get(key) if isinstance(payload, dict) else None)
            if not isinstance(page_records, list) or any(not isinstance(row, dict) for row in page_records):
                failure = SourceFailure("invalid_response", "incomplete",
                                        f"GitHub response did not contain a valid {key} list")
                raise PaginationFailure(failure, records, page - 1) from None
            records.extend(page_records)
            reported_total = payload.get("total_count") if isinstance(payload, dict) else None
            if accessible_cap is not None and isinstance(reported_total, int) \
                    and reported_total > accessible_cap and len(records) >= accessible_cap:
                failure = SourceFailure(
                    "search_cap_exceeded",
                    "incomplete",
                    f"GitHub reports {reported_total} records but exposes at most "
                    f"{accessible_cap} for this filtered query",
                )
                raise PaginationFailure(failure, records[:accessible_cap], page) from None
            if len(page_records) < 100:
                return records, page
        failure = SourceFailure("pagination_limit", "incomplete",
                                "GitHub pagination exceeded 1000 pages")
        raise PaginationFailure(failure, records, 1000)


class GitHubCliClient(GitHubClient):
    """Use an existing gh login without exporting or persisting its credential."""

    def __init__(self, api_version: str,
                 runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 sleeper: Callable[[float], None] = time.sleep,
                 monotonic_clock: Callable[[], float] = time.monotonic,
                 max_elapsed_seconds: int = DEFAULT_API_TIME_BUDGET_SECONDS):
        super().__init__("", "https://api.github.com", api_version,
                         sleeper=sleeper, monotonic_clock=monotonic_clock,
                         max_elapsed_seconds=max_elapsed_seconds)
        self.runner = runner

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        endpoint = path.lstrip("/") + (f"?{query}" if query else "")
        command = [
            "gh", "api", "--method", "GET",
            "--hostname", "github.com",
            "-H", f"X-GitHub-Api-Version: {self.api_version}",
            endpoint,
        ]
        environment = os.environ.copy()
        for variable in GH_TOKEN_OVERRIDE_ENV:
            environment.pop(variable, None)
        for attempt in range(3):
            try:
                completed = self.runner(
                    command, capture_output=True, text=True, timeout=30, check=False,
                    env=environment)
            except (OSError, subprocess.SubprocessError) as exc:
                if attempt < 2:
                    self.sleeper(2 ** attempt)
                    continue
                raise SourceFailure(
                    "transport_error", "incomplete",
                    f"GitHub CLI request failed: {type(exc).__name__}") from None
            if completed.returncode == 0:
                try:
                    return json.loads(completed.stdout)
                except json.JSONDecodeError:
                    raise SourceFailure(
                        "invalid_response", "incomplete",
                        "GitHub CLI response was not valid JSON") from None

            match = re.search(r"HTTP\s+(\d{3})", completed.stderr or "")
            status = int(match.group(1)) if match else None
            if status == 429:
                if attempt < 2:
                    self.wait_for_rate_limit(60, status)
                    continue
                raise SourceFailure(
                    "rate_limited", "incomplete",
                    "GitHub CLI remained rate limited after bounded retries", status) from None
            if status in {401, 403}:
                raise SourceFailure(
                    "unauthorized", "unauthorized",
                    f"GitHub CLI request was unauthorized (HTTP {status})", status) from None
            if status is not None and 500 <= status <= 599 and attempt < 2:
                self.sleeper(2 ** attempt)
                continue
            raise SourceFailure(
                "service_error", "incomplete",
                f"GitHub CLI request failed{f' (HTTP {status})' if status else ''}",
                status) from None
        raise AssertionError("unreachable")


def month_periods(start: date, end: date) -> list[tuple[int, int]]:
    periods = []
    cursor = start.replace(day=1)
    while cursor <= end:
        periods.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return periods


def collection_start(as_of: date) -> date:
    return min(as_of - timedelta(days=29), as_of.replace(day=1))


def source_issue(source: str, failure: SourceFailure) -> dict[str, str]:
    return issue(f"{source}_{failure.code}", failure.status, source, str(failure))


def collect_billing(client: GitHubClient, organization: str, start: date,
                    end: date) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    failures: list[SourceFailure] = []
    periods = month_periods(start, end)
    succeeded = 0
    path = f"/organizations/{urllib.parse.quote(organization, safe='')}/settings/billing/usage"
    for year, month in periods:
        try:
            payload = client.get(path, {"year": year, "month": month})
            items = payload.get("usageItems") if isinstance(payload, dict) else None
            if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
                raise SourceFailure("invalid_response", "incomplete",
                                    "GitHub billing response did not contain a valid usageItems list")
            rows.extend(items)
            succeeded += 1
        except SourceFailure as exc:
            failures.append(exc)
    if failures and not succeeded:
        status = "unauthorized" if all(f.status == "unauthorized" for f in failures) else "incomplete"
    elif failures:
        status = "degraded"
    else:
        status = "complete"
    metadata = {
        "status": status,
        "endpoint": "/organizations/{org}/settings/billing/usage",
        "apiVersion": client.api_version,
        "requestedPeriodsUtc": [f"{year:04d}-{month:02d}" for year, month in periods],
        "requests": len(periods),
        "successfulRequests": succeeded,
        "failedRequests": len(failures),
        "rawItemsReceived": len(rows),
        "rawPayloadRetained": False,
    }
    return rows, metadata, [source_issue("billing", failure) for failure in failures]


def active_repository_names(rows: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({str(repo["name"]) for repo in rows
                   if isinstance(repo.get("name"), str)
                   and not repo.get("archived") and not repo.get("disabled")})


def collect_repositories(client: GitHubClient, organization: str) -> tuple[list[str], int]:
    path = f"/orgs/{urllib.parse.quote(organization, safe='')}/repos"
    repos, pages = client.get_paginated(path, "__root__", {"type": "all"})
    return active_repository_names(repos), pages


def collect_runs_partitioned(client: GitHubClient, path: str, start: date,
                             end: date) -> tuple[
                                 list[dict[str, Any]], int, int, list[SourceFailure]]:
    """Collect runs with adaptive UTC date partitions under GitHub's 1,000 cap."""
    try:
        runs, pages = client.get_paginated(
            path, "workflow_runs",
            {"created": f"{start.isoformat()}..{end.isoformat()}", "status": "completed"},
            accessible_cap=1000,
        )
        return runs, pages, 1, []
    except PaginationFailure as exc:
        if exc.code == "search_cap_exceeded" and start < end:
            midpoint = start + timedelta(days=(end - start).days // 2)
            left = collect_runs_partitioned(client, path, start, midpoint)
            right = collect_runs_partitioned(client, path, midpoint + timedelta(days=1), end)
            combined = left[0] + right[0]
            deduplicated = {}
            for index, run in enumerate(combined):
                run_id = run.get("id")
                key = f"id:{run_id}" if isinstance(run_id, int) else f"shape:{index}"
                deduplicated[key] = run
            return (list(deduplicated.values()), exc.pages + left[1] + right[1],
                    left[2] + right[2], left[3] + right[3])
        return exc.partial, exc.pages, 1, [exc]


def job_executes_in_window(job: dict[str, Any], start: date, end: date) -> bool:
    if job.get("conclusion") == "skipped" and not job.get("started_at") \
            and not job.get("completed_at"):
        return False
    try:
        started = parse_timestamp(job.get("started_at"))
    except ReportError:
        return True
    return start <= started.date() <= end


def collect_jobs(client: GitHubClient, organization: str, start: date, end: date,
                 fallback_repositories: Iterable[str],
                 configured_repositories: Iterable[str] = ()) -> tuple[
                     list[dict[str, Any]], dict[str, Any], list[dict[str, str]]]:
    failures: list[tuple[str, SourceFailure]] = []
    pages = 0
    repo_listing_complete = True
    try:
        repositories, repo_pages = collect_repositories(client, organization)
        pages += repo_pages
    except PaginationFailure as exc:
        repositories = active_repository_names(exc.partial)
        pages += exc.pages
        repo_listing_complete = False
        failures.append(("repository_listing", exc))
    configured = {
        repository_name(repo, organization) for repo in configured_repositories
        if repo and repo != "unattributed"
    }
    repositories = sorted(set(repositories).union(configured).union(
        repository_name(repo, organization) for repo in fallback_repositories
        if repo and repo != "unattributed"
    ))

    records: list[dict[str, Any]] = []
    jobs_outside_window = 0
    run_search_partitions = 0
    run_search_start = start - timedelta(days=RERUN_LOOKBACK_DAYS)
    complete_repositories = 0
    for repository in repositories:
        encoded_repo = urllib.parse.quote(repository, safe="")
        runs_path = f"/repos/{urllib.parse.quote(organization, safe='')}/{encoded_repo}/actions/runs"
        runs, run_pages, partitions, run_failures = collect_runs_partitioned(
            client, runs_path, run_search_start, end)
        pages += run_pages
        run_search_partitions += partitions
        repo_complete = not run_failures
        failures.extend((repository, failure) for failure in run_failures)
        for run in runs:
            run_id = run.get("id")
            if not isinstance(run_id, int) or isinstance(run_id, bool):
                repo_complete = False
                failures.append((repository, SourceFailure(
                    "invalid_run", "incomplete", "Workflow run lacked a numeric id")))
                continue
            jobs_path = f"/repos/{urllib.parse.quote(organization, safe='')}/{encoded_repo}/actions/runs/{run_id}/jobs"
            try:
                jobs, job_pages = client.get_paginated(jobs_path, "jobs", {"filter": "all"})
                pages += job_pages
            except PaginationFailure as exc:
                jobs = exc.partial
                pages += exc.pages
                repo_complete = False
                failures.append((repository, exc))
            for job in jobs:
                if not job_executes_in_window(job, start, end):
                    jobs_outside_window += 1
                    continue
                enriched = dict(job)
                enriched.update({
                    "repository": repository,
                    "job_id": job.get("id"),
                    "run_id": run_id,
                    "run_attempt": job.get("run_attempt", run.get("run_attempt", 1)),
                })
                records.append(enriched)
        if repo_complete:
            complete_repositories += 1

    if failures and not records and not complete_repositories:
        status = "unauthorized" if all(failure.status == "unauthorized"
                                       for _repo, failure in failures) else "incomplete"
    elif failures or not repo_listing_complete:
        status = "degraded"
    else:
        status = "complete"
    metadata = {
        "status": status,
        "endpoint": "/repos/{owner}/{repo}/actions/runs + /jobs?filter=all",
        "apiVersion": client.api_version,
        "requestedRangeUtc": {"start": start.isoformat(), "end": end.isoformat()},
        "runCreatedLookbackStartUtc": run_search_start.isoformat(),
        "rerunLookbackDays": RERUN_LOOKBACK_DAYS,
        "runSearchPartitions": run_search_partitions,
        "rerunCompleteness": (
            "not_guaranteed" if failures else
            "bounded_by_official_30_day_rerun_window"
        ),
        "executionTimestampFilter": "job started_at UTC date within requestedRangeUtc",
        "jobsOutsideWindowDiscarded": jobs_outside_window,
        "repositoriesRequested": len(repositories),
        "repositoriesComplete": complete_repositories,
        "repositoryListingComplete": repo_listing_complete,
        "activeRepositories": repositories,
        "configuredRepositories": sorted(configured),
        "pagesFetched": pages,
        "failedRequests": len(failures),
        "rawJobsReceived": len(records),
        "rawPayloadRetained": False,
        "rerunPolicy": "30-day initial-run lookback; filter=all; distinct job ids retained; duplicate job ids discarded",
    }
    problems = [issue(f"jobs_{failure.code}", failure.status, "jobs",
                      f"{scope}: {failure}") for scope, failure in failures]
    return records, metadata, problems


def load_consumer_inventory(path: Path, organization: str) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("organization") != organization:
        raise ReportError("consumer inventory organization does not match the report organization")
    repositories = payload.get("activeRepositories")
    if not isinstance(repositories, list) or not repositories or any(
            not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo)
            for repo in repositories):
        raise ReportError("consumer inventory must contain safe active repository names")
    required = {"tracker", "wodiq", "gate"}
    if not required.issubset({repo.casefold() for repo in repositories}):
        raise ReportError("consumer inventory must explicitly include Tracker, WODIQ, and Gate")
    return repositories


def fmt(value: Any, decimals: int = 3) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, (int, float)):
        return f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# GitHub Actions billing report — {report['asOfDateUtc']}", "",
        f"**Status:** `{report['status']}`  ",
        f"**Organization:** `{report['organization']}`  ",
        f"**Generated (UTC):** `{report['generatedAt']}`", "",
        "> Billing facts and job-derived telemetry are deliberately separate. Job estimates are not billed truth.", "",
    ]
    if report["issues"]:
        lines.extend(["## Completeness and freshness", ""])
        for problem in report["issues"]:
            lines.append(f"- `{problem['severity']}` `{problem['code']}` — {problem['message']}")
        lines.append("")

    lines.extend(["## GitHub billing facts", ""])
    for key in ("currentBillingPeriod", "rolling7Days", "rolling30Days"):
        window = report["windows"][key]
        facts = window["billingFacts"]
        lines.extend([
            f"### {window['label']} ({window['startDate']} through {window['endDate']})", "",
            f"Status: `{facts['status']}`", "",
            "| SKU | Category | Unit | Gross qty | Discount qty | Net qty | Qty provenance (G/D/N) | Gross USD | Discount USD | Net USD |",
            "|---|---|---:|---:|---:|---:|---|---:|---:|---:|",
        ])
        if facts["totalsBySku"]:
            for row in facts["totalsBySku"]:
                quantity_provenance = row["quantityProvenance"]
                provenance_text = "/".join(
                    quantity_provenance[name] for name in ("gross", "discount", "net"))
                lines.append("| {sku} | {category} | {unitType} | {grossQuantity} | "
                             "{discountQuantity} | {netQuantity} | {provenance} | "
                             "{grossAmount} | {discountAmount} | {netAmount} |".format(
                                 provenance=provenance_text,
                                 **{field: fmt(value) for field, value in row.items()
                                    if field != "quantityProvenance"}))
        else:
            lines.append("| _No Actions rows observed_ | — | — | — | — | — | — | — | — | — |")
        billed = facts["githubBilledMinutes"]
        lines.extend([
            "",
            f"GitHub-billed net minutes: **{fmt(billed['net']['value'])}** "
            f"(`{billed['net']['status']}`; summarized billing fact).", "",
            "Per repository and SKU:", "",
            "| Repository | Consumer | SKU | Gross qty | Discount qty | Net qty | Qty provenance (G/D/N) | Net USD |",
            "|---|---|---|---:|---:|---:|---|---:|",
        ])
        if facts["repositories"]:
            for repo in facts["repositories"]:
                for row in repo["bySku"]:
                    provenance_text = "/".join(
                        row["quantityProvenance"][name]
                        for name in ("gross", "discount", "net"))
                    lines.append(f"| {repo['repository']} | {repo['consumer']} | {row['sku']} | "
                                 f"{fmt(row['grossQuantity'])} | {fmt(row['discountQuantity'])} | "
                                 f"{fmt(row['netQuantity'])} | {provenance_text} | "
                                 f"{fmt(row['netAmount'])} |")
        else:
            lines.append("| _No repository rows observed_ | — | — | — | — | — | — | — |")
        lines.append("")

    lines.extend(["## Job-derived telemetry (not billing)", ""])
    current = report["windows"]["currentBillingPeriod"]["jobDerivedMetrics"]
    totals = current["totals"]
    lines.extend([
        f"Status: `{current['status']}`. {current['warning']}", "",
        f"- Raw execution duration: **{fmt(totals['rawExecutionDuration']['value'])} seconds**.",
        f"- Per-job rounded estimate: **{fmt(totals['perJobRoundedEstimate']['value'])} minutes**.",
        f"- Self-hosted occupation (inferred): **{fmt(totals['selfHostedOccupation']['value'])} seconds**.",
        f"- Unknown-runner duration: **{fmt(totals['unknownRunnerDuration']['value'])} seconds**.",
        "",
        "## Top consumers and daily trend", "",
        "Top consumers are ranked by observed current-period net GitHub Actions amount. "
        "The machine-readable JSON contains rankings and UTC daily trend for all three windows.", "",
    ])
    for row in report["windows"]["currentBillingPeriod"]["topConsumers"][:10]:
        lines.append(f"- {row['consumer']}: USD {fmt(row['netAmount'])} (`{row['status']}`)")
    projection = report["burnProjection"]
    lines.extend([
        "", "## Month-end projection", "",
        f"**{projection['label']}**", "",
        f"- Status: `{projection['status']}`; method: `{projection['method']}`; "
        f"confidence: `{projection['confidence']}`.",
        f"- Projected net amount: **USD {fmt(projection['projectedNetAmount'])}**.",
        f"- {projection['assumptions']}", "",
        "## Evidence handling", "",
        "This is a sanitized aggregate. API credentials and raw API payloads are not retained. "
        "Use the documented append-only archive command for month-over-month evidence.", "",
    ])
    return "\n".join(lines)


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_sanitized(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEY_RE.search(str(key)):
                raise ReportError(f"report contains forbidden sensitive field at {path}.{key}")
            ensure_sanitized(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            ensure_sanitized(child, f"{path}[{index}]")


def archive_report(report_path: Path, archive_root: Path) -> tuple[Path, Path]:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ReportError("archive input is not a supported CI billing report")
    ensure_sanitized(payload)
    organization = str(payload.get("organization", "")).strip()
    as_of = date.fromisoformat(str(payload.get("asOfDateUtc", "")))
    if not organization or not re.fullmatch(r"[A-Za-z0-9_.-]+", organization):
        raise ReportError("report organization is unsafe for an archive path")
    destination = archive_root / organization / f"{as_of.year:04d}" / f"{as_of.month:02d}"
    json_path = destination / f"ci-billing-{as_of.isoformat()}.json"
    markdown_path = destination / f"ci-billing-{as_of.isoformat()}.md"
    json_content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    markdown_content = render_markdown(payload)
    for path, content in ((json_path, json_content), (markdown_path, markdown_content)):
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise ReportError(f"append-only archive entry already exists with different content: {path}")
            continue
        atomic_write(path, content)
    return json_path, markdown_path


def exit_code_for_status(status: str) -> int:
    return 0 if status in {"complete", "degraded"} else 2


def collect_command(args: argparse.Namespace) -> int:
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
    generated_at = datetime.now(timezone.utc)
    start = collection_start(as_of)
    token = os.environ.get(args.token_env, "")
    configured_repositories = load_consumer_inventory(
        Path(args.consumer_inventory), args.organization)
    problems: list[dict[str, str]] = []
    if token or args.auth_mode == "gh-cli":
        client = (GitHubClient(token, "https://api.github.com", args.api_version)
                  if token else GitHubCliClient(args.api_version))
        billing_rows, billing_source, billing_problems = collect_billing(
            client, args.organization, start, as_of)
        billing_repositories = {
            repository_name(row.get("repositoryName"), args.organization)
            for row in billing_rows if isinstance(row.get("repositoryName"), str)
        }
        jobs, job_source, job_problems = collect_jobs(
            client, args.organization, start, as_of, billing_repositories,
            configured_repositories)
        problems.extend(billing_problems)
        problems.extend(job_problems)
    else:
        billing_rows = []
        jobs = []
        message = f"Environment variable {args.token_env} is missing; no API request was made."
        billing_source = {"status": "unauthorized", "requests": 0, "failedRequests": 0,
                          "rawPayloadRetained": False}
        job_source = {"status": "unauthorized", "repositoriesRequested": 0,
                      "repositoriesComplete": 0, "failedRequests": 0,
                      "rawPayloadRetained": False,
                      "repositoryListingComplete": False,
                      "activeRepositories": configured_repositories,
                      "configuredRepositories": configured_repositories}
        problems.extend([
            issue("billing_auth_missing", "unauthorized", "billing", message),
            issue("jobs_auth_missing", "unauthorized", "jobs", message),
        ])

    result = build_report(
        organization=args.organization,
        billing_items=billing_rows,
        job_records=jobs,
        as_of=as_of,
        generated_at=generated_at,
        billing_source=billing_source,
        job_source=job_source,
        source_issues=problems,
    )
    output_dir = Path(args.output_dir)
    json_path = output_dir / f"ci-billing-{as_of.isoformat()}.json"
    markdown_path = output_dir / f"ci-billing-{as_of.isoformat()}.md"
    atomic_write(json_path, json.dumps(result, indent=2, sort_keys=True) + "\n")
    atomic_write(markdown_path, render_markdown(result))
    print(json_path)
    print(markdown_path)
    return exit_code_for_status(result["status"])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="collect and aggregate GitHub API evidence")
    collect.add_argument("--organization", required=True)
    collect.add_argument("--output-dir", required=True)
    collect.add_argument("--as-of", help="UTC date (YYYY-MM-DD); defaults to today")
    collect.add_argument("--token-env", default="CI_BILLING_REPORT_TOKEN")
    collect.add_argument(
        "--auth-mode", choices=("token", "gh-cli"), default="token",
        help="token reads --token-env; gh-cli uses the existing authenticated gh session")
    collect.add_argument("--api-version", default=DEFAULT_API_VERSION)
    collect.add_argument("--consumer-inventory", default=str(DEFAULT_CONSUMER_INVENTORY))

    render = commands.add_parser("render", help="re-render Markdown from sanitized JSON")
    render.add_argument("--input", required=True)
    render.add_argument("--output", required=True)

    archive = commands.add_parser("archive", help="append a sanitized report to durable storage")
    archive.add_argument("--report", required=True)
    archive.add_argument("--archive-root", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "collect":
            return collect_command(args)
        if args.command == "render":
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
            ensure_sanitized(payload)
            atomic_write(Path(args.output), render_markdown(payload))
            return 0
        json_path, markdown_path = archive_report(Path(args.report), Path(args.archive_root))
        print(json_path)
        print(markdown_path)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ReportError) as exc:
        print(f"ci-billing-report: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
