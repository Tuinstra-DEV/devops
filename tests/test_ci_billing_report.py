from datetime import date, datetime, timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ci_billing_report as report


class BillingNormalizationTests(unittest.TestCase):
    def test_normalizes_live_api_shape_case_fractional_quantity_and_utc_date(self):
        item = report.normalize_billing_item(
            {
                "date": "2026-08-11T23:30:00-02:00",
                "product": "actions",
                "sku": "Actions storage",
                "quantity": 1.25,
                "unitType": "gigabyte-hours",
                "pricePerUnit": 0.25,
                "grossAmount": 0.3125,
                "discountAmount": 0.0625,
                "netAmount": 0.25,
                "repositoryName": "tracker",
            },
            "Tuinstra-DEV",
        )

        self.assertEqual(item["date"], date(2026, 8, 12))
        self.assertEqual(item["repository"], "tracker")
        self.assertEqual(item["product"], "Actions")
        self.assertEqual(item["category"], "storage")
        self.assertEqual(item["gross_quantity"], Decimal("1.25"))
        self.assertEqual(item["discount_quantity"], Decimal("0.25"))
        self.assertEqual(item["net_quantity"], Decimal("1"))
        self.assertEqual(item["gross_quantity_provenance"], "explicit_api_quantity")
        self.assertEqual(item["discount_quantity_provenance"], "derived_from_amount")
        self.assertEqual(item["net_quantity_provenance"], "derived_from_amount")

    def test_prefers_explicit_summary_quantities_when_present(self):
        item = report.normalize_billing_item(
            {
                "date": "2026-08-01T00:00:00Z",
                "product": "Actions",
                "sku": "Actions Linux",
                "unitType": "minutes",
                "pricePerUnit": 0.008,
                "grossQuantity": 2.5,
                "discountQuantity": 1.5,
                "netQuantity": 1,
                "grossAmount": 0.02,
                "discountAmount": 0.012,
                "netAmount": 0.008,
                "repositoryName": "Tuinstra-DEV/wodiq",
            },
            "Tuinstra-DEV",
        )
        self.assertEqual(item["repository"], "wodiq")
        self.assertEqual(item["gross_quantity"], Decimal("2.5"))
        self.assertEqual(item["discount_quantity"], Decimal("1.5"))
        self.assertEqual(item["net_quantity"], Decimal("1"))
        self.assertEqual(item["gross_quantity_provenance"], "explicit_api_quantity")
        self.assertEqual(item["discount_quantity_provenance"], "explicit_api_quantity")
        self.assertEqual(item["net_quantity_provenance"], "explicit_api_quantity")

    def test_aggregate_exposes_derived_quantity_provenance(self):
        normalized = report.normalize_billing_item(
            {
                "date": "2026-08-01T00:00:00Z",
                "product": "actions",
                "sku": "Actions Linux",
                "quantity": 10,
                "unitType": "minutes",
                "pricePerUnit": 0.01,
                "grossAmount": 0.1,
                "discountAmount": 0.04,
                "netAmount": 0.06,
                "repositoryName": "tracker",
            },
            "Tuinstra-DEV",
        )
        aggregate = report.aggregate_billing([normalized], "complete", True)
        sku = aggregate["totalsBySku"][0]
        self.assertEqual(sku["quantityProvenance"]["gross"], "explicit_api_quantity")
        self.assertEqual(sku["quantityProvenance"]["discount"], "derived_from_amount")
        self.assertEqual(sku["quantityProvenance"]["net"], "derived_from_amount")
        self.assertIn("derived_from_amount", aggregate["githubBilledMinutes"]["net"]["provenance"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.as_of = date(2026, 8, 12)
        self.generated_at = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        self.billing = [
            {
                "date": "2026-08-01T00:00:00Z",
                "product": "actions",
                "sku": "Actions Linux",
                "quantity": 10.5,
                "unitType": "minutes",
                "pricePerUnit": 0.008,
                "grossAmount": 0.084,
                "discountAmount": 0.04,
                "netAmount": 0.044,
                "repositoryName": "tracker",
            },
            {
                "date": "2026-08-10T14:00:00Z",
                "product": "Actions",
                "sku": "Actions macOS",
                "quantity": 2,
                "unitType": "minutes",
                "pricePerUnit": 0.08,
                "grossAmount": 0.16,
                "discountAmount": 0,
                "netAmount": 0.16,
                "repositoryName": "wodiq",
            },
            {
                "date": "2026-08-12T00:00:00Z",
                "product": "ACTIONS",
                "sku": "Actions storage",
                "quantity": 3.5,
                "unitType": "gigabyte-hours",
                "pricePerUnit": 0.25,
                "grossAmount": 0.875,
                "discountAmount": 0,
                "netAmount": 0.875,
                "repositoryName": "gate",
            },
            {
                "date": "2026-08-12T00:00:00Z",
                "product": "Packages",
                "sku": "Packages storage",
                "quantity": 100,
                "unitType": "gigabyte-hours",
                "pricePerUnit": 1,
                "grossAmount": 100,
                "discountAmount": 0,
                "netAmount": 100,
                "repositoryName": "tracker",
            },
        ]
        self.jobs = [
            {
                "repository": "tracker",
                "started_at": "2026-08-10T10:00:00Z",
                "completed_at": "2026-08-10T10:01:01Z",
                "runner_type": "github_hosted",
            },
            {
                "repository": "wodiq",
                "started_at": "2026-08-11T10:00:00Z",
                "completed_at": "2026-08-11T10:02:30Z",
                "runner_type": "self_hosted",
            },
        ]

    def build(self, jobs=None, job_status="complete", issues=None):
        return report.build_report(
            organization="Tuinstra-DEV",
            billing_items=self.billing,
            job_records=self.jobs if jobs is None else jobs,
            as_of=self.as_of,
            generated_at=self.generated_at,
            billing_source={"status": "complete", "requests": 1, "failedRequests": 0},
            job_source={
                "status": job_status,
                "repositoriesRequested": 3,
                "repositoriesComplete": 3 if job_status == "complete" else 0,
            },
            source_issues=[] if issues is None else issues,
        )

    def test_builds_all_windows_per_repo_sku_consumers_trend_and_projection(self):
        result = self.build()

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(
            set(result["windows"]),
            {"currentBillingPeriod", "rolling7Days", "rolling30Days"},
        )
        current = result["windows"]["currentBillingPeriod"]
        repos = {row["repository"]: row for row in current["billingFacts"]["repositories"]}
        self.assertEqual(set(repos), {"tracker", "wodiq", "gate"})
        self.assertEqual(repos["tracker"]["consumer"], "Tracker")
        self.assertEqual(repos["wodiq"]["consumer"], "WODIQ")
        self.assertEqual(repos["gate"]["consumer"], "Gate")
        self.assertEqual(repos["tracker"]["bySku"][0]["grossQuantity"], 10.5)
        self.assertEqual(repos["tracker"]["bySku"][0]["netQuantity"], 5.5)
        self.assertEqual(repos["tracker"]["bySku"][0]["netAmount"], 0.044)
        categories = {row["category"] for row in current["billingFacts"]["totalsBySku"]}
        self.assertEqual(categories, {"linux", "premium_os", "storage"})
        self.assertEqual(current["topConsumers"][0]["consumer"], "Gate")
        self.assertEqual(current["dailyTrend"][-1]["date"], "2026-08-12")
        self.assertEqual(
            current["jobDerivedMetrics"]["totals"]["rawExecutionDuration"]["value"],
            211,
        )
        self.assertEqual(
            current["jobDerivedMetrics"]["totals"]["perJobRoundedEstimate"]["value"],
            2,
        )
        self.assertEqual(
            current["jobDerivedMetrics"]["totals"]["selfHostedOccupation"]["value"],
            150,
        )
        self.assertIn("LINEAR MONTH-END BURN PROJECTION", result["burnProjection"]["label"])
        self.assertNotIn("Packages", str(result))

    def test_named_consumers_do_not_absorb_other_active_repositories(self):
        self.assertEqual(report.consumer_for("tracker"), "Tracker")
        self.assertEqual(report.consumer_for("WODIQ"), "WODIQ")
        self.assertEqual(report.consumer_for("gate"), "Gate")
        self.assertEqual(report.consumer_for("wodiq-site"), "Other: wodiq-site")
        self.assertEqual(report.consumer_for("tracker-tools"), "Other: tracker-tools")

    def test_job_telemetry_unavailable_is_null_and_degraded_not_zero(self):
        result = self.build(
            jobs=[],
            job_status="unauthorized",
            issues=[
                report.issue(
                    "job_telemetry_unauthorized",
                    "unauthorized",
                    "jobs",
                    "GitHub job telemetry was unauthorized.",
                )
            ],
        )

        self.assertEqual(result["status"], "incomplete")
        metrics = result["windows"]["rolling7Days"]["jobDerivedMetrics"]
        self.assertEqual(metrics["status"], "unauthorized")
        self.assertIsNone(metrics["totals"]["rawExecutionDuration"]["value"])
        self.assertIsNone(metrics["totals"]["perJobRoundedEstimate"]["value"])
        self.assertIsNone(metrics["totals"]["selfHostedOccupation"]["value"])

    def test_unauthorized_billing_is_explicitly_incomplete(self):
        result = report.build_report(
            organization="Tuinstra-DEV",
            billing_items=[],
            job_records=[],
            as_of=self.as_of,
            generated_at=self.generated_at,
            billing_source={"status": "unauthorized", "requests": 1, "failedRequests": 1},
            job_source={"status": "complete", "repositoriesRequested": 0, "repositoriesComplete": 0},
            source_issues=[
                report.issue(
                    "billing_unauthorized",
                    "unauthorized",
                    "billing",
                    "GitHub billing usage was unauthorized.",
                )
            ],
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["windows"]["currentBillingPeriod"]["status"], "incomplete")
        self.assertIsNone(result["burnProjection"]["projectedNetAmount"])

    def test_successful_empty_billing_response_is_no_observation_not_zero(self):
        result = report.build_report(
            organization="Tuinstra-DEV",
            billing_items=[],
            job_records=[],
            as_of=self.as_of,
            generated_at=self.generated_at,
            billing_source={"status": "complete", "requests": 1,
                            "successfulRequests": 1, "failedRequests": 0},
            job_source={"status": "complete", "repositoriesRequested": 1,
                        "repositoriesComplete": 1, "repositoryListingComplete": True,
                        "activeRepositories": ["tracker"]},
            source_issues=[],
        )
        facts = result["windows"]["currentBillingPeriod"]["billingFacts"]
        self.assertEqual(facts["usageObservation"], "no_usage_observed")
        self.assertIsNone(facts["amounts"]["net"]["value"])
        self.assertTrue(any(problem["code"] == "no_actions_usage_observed"
                            for problem in result["issues"]))

    def test_duplicate_pages_do_not_double_count_but_distinct_attempts_do(self):
        jobs = [
            {
                "job_id": 10,
                "run_id": 1,
                "run_attempt": 1,
                "repository": "tracker",
                "started_at": "2026-08-10T10:00:00Z",
                "completed_at": "2026-08-10T10:01:00Z",
                "runner_type": "github_hosted",
            },
            {
                "job_id": 10,
                "run_id": 1,
                "run_attempt": 1,
                "repository": "tracker",
                "started_at": "2026-08-10T10:00:00Z",
                "completed_at": "2026-08-10T10:01:00Z",
                "runner_type": "github_hosted",
            },
            {
                "job_id": 11,
                "run_id": 1,
                "run_attempt": 2,
                "repository": "tracker",
                "started_at": "2026-08-10T10:02:00Z",
                "completed_at": "2026-08-10T10:03:00Z",
                "runner_type": "github_hosted",
            },
        ]
        result = self.build(jobs=jobs)
        totals = result["windows"]["rolling7Days"]["jobDerivedMetrics"]["totals"]
        self.assertEqual(totals["completedJobs"]["value"], 2)
        self.assertEqual(totals["workflowRunAttemptsIncluded"]["value"], 2)
        self.assertEqual(totals["rawExecutionDuration"]["value"], 120)

    def test_skipped_job_without_runner_timestamps_is_not_invalid_telemetry(self):
        jobs, duplicates, invalid = report.normalize_jobs([
            {
                "id": 12,
                "conclusion": "skipped",
                "started_at": None,
                "completed_at": None,
                "labels": ["ubuntu-24.04"],
            }
        ])
        self.assertEqual(jobs, [])
        self.assertEqual(duplicates, 0)
        self.assertEqual(invalid, 0)

    def test_projection_declares_method_confidence_and_inherits_status(self):
        projection = self.build()["burnProjection"]
        self.assertEqual(projection["status"], "degraded")
        self.assertEqual(projection["method"], "linear_daily_run_rate")
        self.assertIn(projection["confidence"], {"low", "medium"})

    def test_active_repo_without_billing_row_is_no_usage_observed_not_zero(self):
        result = report.build_report(
            organization="Tuinstra-DEV",
            billing_items=self.billing,
            job_records=self.jobs,
            as_of=self.as_of,
            generated_at=self.generated_at,
            billing_source={"status": "complete", "requests": 1, "failedRequests": 0},
            job_source={
                "status": "complete",
                "repositoriesRequested": 4,
                "repositoriesComplete": 4,
                "repositoryListingComplete": True,
                "activeRepositories": ["tracker", "WODIQ", "gate", "notify"],
            },
            source_issues=[],
        )
        repositories = {
            row["repository"]: row
            for row in result["windows"]["currentBillingPeriod"]["billingFacts"]["repositories"]
        }
        self.assertEqual(repositories["notify"]["usageObservation"], "no_usage_observed")
        self.assertIsNone(repositories["notify"]["amounts"]["net"]["value"])
        self.assertEqual(repositories["tracker"]["usageObservation"], "usage_rows_observed")
        top = {row["consumer"]: row for row in result["windows"]["currentBillingPeriod"]["topConsumers"]}
        self.assertIn("Other: notify", top)
        self.assertIsNone(top["Other: notify"]["netAmount"])

    def test_markdown_has_separate_fact_and_estimate_sections(self):
        markdown = report.render_markdown(self.build())
        self.assertIn("## GitHub billing facts", markdown)
        self.assertIn("## Job-derived telemetry (not billing)", markdown)
        self.assertIn("GitHub-billed net minutes", markdown)
        self.assertIn("Per-job rounded estimate", markdown)
        self.assertIn("Self-hosted occupation", markdown)
        self.assertIn("LINEAR MONTH-END BURN PROJECTION", markdown)


class ApiCollectionTests(unittest.TestCase):
    def test_month_periods_cross_calendar_month_boundary(self):
        self.assertEqual(
            report.month_periods(date(2026, 7, 14), date(2026, 8, 12)),
            [(2026, 7), (2026, 8)],
        )

    def test_collection_start_covers_day_one_of_a_31_day_current_period(self):
        self.assertEqual(report.collection_start(date(2026, 8, 31)), date(2026, 8, 1))
        self.assertEqual(report.collection_start(date(2026, 8, 12)), date(2026, 7, 14))

    def test_partial_repository_page_names_are_preserved(self):
        client = object.__new__(report.GitHubClient)
        partial = [
            {"name": "tracker", "archived": False, "disabled": False},
            {"name": "old", "archived": True, "disabled": False},
        ]
        client.api_version = "2026-03-10"
        client.get_paginated = mock.Mock(side_effect=[
            report.PaginationFailure(
                report.SourceFailure("service_error", "incomplete", "HTTP 503", 503),
                partial,
                1,
            ),
            ([], 1),
        ])
        _jobs, source, problems = report.collect_jobs(
            client, "Tuinstra-DEV", date(2026, 8, 1), date(2026, 8, 31), [])
        self.assertEqual(source["activeRepositories"], ["tracker"])
        self.assertFalse(source["repositoryListingComplete"])
        self.assertEqual(source["status"], "degraded")
        self.assertEqual(source["pagesFetched"], 2)
        self.assertTrue(any(problem["code"] == "jobs_service_error" for problem in problems))

    def test_rerun_created_before_window_is_found_and_filtered_by_job_execution_time(self):
        client = object.__new__(report.GitHubClient)
        client.api_version = "2026-03-10"

        def paginate(path, key, params=None, accessible_cap=None):
            if path.endswith("/repos"):
                return ([{"name": "tracker", "archived": False, "disabled": False}], 1)
            if path.endswith("/actions/runs"):
                self.assertEqual(params["created"], "2026-07-02..2026-08-31")
                self.assertEqual(accessible_cap, 1000)
                return ([{"id": 42, "run_attempt": 2, "created_at": "2026-07-20T00:00:00Z"}], 1)
            self.assertEqual(params["filter"], "all")
            return ([
                {"id": 100, "started_at": "2026-08-05T10:00:00Z",
                 "completed_at": "2026-08-05T10:01:00Z", "conclusion": "success"},
                {"id": 101, "started_at": "2026-07-25T10:00:00Z",
                 "completed_at": "2026-07-25T10:01:00Z", "conclusion": "success"},
            ], 1)

        client.get_paginated = mock.Mock(side_effect=paginate)
        jobs, source, problems = report.collect_jobs(
            client, "Tuinstra-DEV", date(2026, 8, 1), date(2026, 8, 31), [])
        self.assertEqual([job["job_id"] for job in jobs], [100])
        self.assertEqual(source["runCreatedLookbackStartUtc"], "2026-07-02")
        self.assertEqual(source["rerunLookbackDays"], 30)
        self.assertEqual(source["rerunCompleteness"],
                         "bounded_by_official_30_day_rerun_window")
        self.assertEqual(source["jobsOutsideWindowDiscarded"], 1)
        self.assertEqual(source["status"], "complete")
        self.assertEqual(problems, [])

    def test_run_search_cap_is_adaptively_partitioned(self):
        client = object.__new__(report.GitHubClient)

        def paginate(_path, _key, params=None, accessible_cap=None):
            if params["created"] == "2026-07-01..2026-07-04":
                raise report.PaginationFailure(
                    report.SourceFailure("search_cap_exceeded", "incomplete", "cap"),
                    [{"id": 999}], 10)
            return ([{"id": int(params["created"][-2:])}], 1)

        client.get_paginated = mock.Mock(side_effect=paginate)
        runs, pages, partitions, failures = report.collect_runs_partitioned(
            client, "/runs", date(2026, 7, 1), date(2026, 7, 4))
        self.assertEqual(pages, 12)
        self.assertEqual(partitions, 2)
        self.assertEqual(len(runs), 2)
        self.assertEqual(failures, [])

    def test_single_day_run_search_cap_explicitly_degrades(self):
        client = object.__new__(report.GitHubClient)
        client.get_paginated = mock.Mock(side_effect=report.PaginationFailure(
            report.SourceFailure("search_cap_exceeded", "incomplete", "cap"),
            [{"id": 1}], 10))
        runs, pages, partitions, failures = report.collect_runs_partitioned(
            client, "/runs", date(2026, 7, 1), date(2026, 7, 1))
        self.assertEqual(runs, [{"id": 1}])
        self.assertEqual((pages, partitions), (10, 1))
        self.assertEqual(failures[0].code, "search_cap_exceeded")

    def test_unresolved_run_partition_marks_job_source_degraded(self):
        client = object.__new__(report.GitHubClient)
        client.api_version = "2026-03-10"
        client.get_paginated = mock.Mock(side_effect=[
            ([{"name": "tracker", "archived": False, "disabled": False}], 1),
            ([{"id": 100, "started_at": "2026-08-02T00:00:00Z",
               "completed_at": "2026-08-02T00:01:00Z", "conclusion": "success"}], 1),
        ])
        failure = report.SourceFailure("search_cap_exceeded", "incomplete", "cap")
        with mock.patch.object(
                report, "collect_runs_partitioned",
                return_value=([{"id": 42, "run_attempt": 1}], 10, 1, [failure])):
            jobs, source, problems = report.collect_jobs(
                client, "Tuinstra-DEV", date(2026, 8, 1), date(2026, 8, 31), [])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(source["status"], "degraded")
        self.assertEqual(source["rerunCompleteness"], "not_guaranteed")
        self.assertTrue(any(problem["code"] == "jobs_search_cap_exceeded"
                            for problem in problems))

    def test_pagination_reads_every_page_without_silent_truncation(self):
        client = object.__new__(report.GitHubClient)
        first = [{"id": index} for index in range(100)]
        client.get = mock.Mock(side_effect=[{"jobs": first}, {"jobs": [{"id": 100}]}])
        rows, pages = client.get_paginated("/jobs", "jobs", {"filter": "all"})
        self.assertEqual(len(rows), 101)
        self.assertEqual(pages, 2)
        self.assertEqual(client.get.call_args_list[0].args[1]["per_page"], 100)
        self.assertEqual(client.get.call_args_list[0].args[1]["filter"], "all")
        self.assertEqual(client.get.call_args_list[1].args[1]["page"], 2)

    def test_pagination_preserves_partial_rows_and_failure_status(self):
        client = object.__new__(report.GitHubClient)
        first = [{"id": index} for index in range(100)]
        client.get = mock.Mock(side_effect=[
            {"jobs": first},
            report.SourceFailure("unauthorized", "unauthorized", "HTTP 403", 403),
        ])
        with self.assertRaises(report.PaginationFailure) as raised:
            client.get_paginated("/jobs", "jobs")
        self.assertEqual(len(raised.exception.partial), 100)
        self.assertEqual(raised.exception.pages, 1)
        self.assertEqual(raised.exception.status, "unauthorized")

    def test_pagination_surfaces_github_filtered_search_cap(self):
        client = object.__new__(report.GitHubClient)
        first = [{"id": index} for index in range(100)]
        client.get = mock.Mock(return_value={"total_count": 101, "workflow_runs": first})
        with self.assertRaises(report.PaginationFailure) as raised:
            client.get_paginated(
                "/runs", "workflow_runs", {"created": "2026-08-01..2026-08-31"},
                accessible_cap=100,
            )
        self.assertEqual(raised.exception.code, "search_cap_exceeded")
        self.assertEqual(raised.exception.status, "incomplete")
        self.assertEqual(len(raised.exception.partial), 100)
        self.assertEqual(raised.exception.pages, 1)

    def test_403_is_unauthorized_and_not_retried(self):
        error = report.urllib.error.HTTPError("https://api.github.test/x", 403, "", {}, None)
        opener = mock.Mock(side_effect=error)
        client = report.GitHubClient("opaque", "https://api.github.test", "2026-03-10",
                                     opener=opener, sleeper=mock.Mock())
        with self.assertRaises(report.SourceFailure) as raised:
            client.get("/x")
        self.assertEqual(raised.exception.status, "unauthorized")
        self.assertEqual(opener.call_count, 1)

    def test_rate_limit_403_honors_reset_header_instead_of_reporting_unauthorized(self):
        error = report.urllib.error.HTTPError(
            "https://api.github.test/x", 403, "", {
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1004",
            }, None)
        opener = mock.Mock(side_effect=[error, io.BytesIO(b'{"ok": true}')])
        sleeper = mock.Mock()
        client = report.GitHubClient(
            "opaque", "https://api.github.test", "2026-03-10",
            opener=opener, sleeper=sleeper, wall_clock=lambda: 1000,
            monotonic_clock=mock.Mock(side_effect=[0, 0]))
        self.assertEqual(client.get("/x"), {"ok": True})
        sleeper.assert_called_once_with(5)

    def test_rate_limit_wait_beyond_client_budget_emits_rate_limited(self):
        error = report.urllib.error.HTTPError(
            "https://api.github.test/x", 403, "", {
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "2000",
            }, None)
        sleeper = mock.Mock()
        client = report.GitHubClient(
            "opaque", "https://api.github.test", "2026-03-10",
            opener=mock.Mock(side_effect=error), sleeper=sleeper,
            wall_clock=lambda: 1000, monotonic_clock=mock.Mock(side_effect=[0, 1]),
            max_elapsed_seconds=100)
        with self.assertRaises(report.SourceFailure) as raised:
            client.get("/x")
        self.assertEqual(raised.exception.code, "rate_limited")
        self.assertEqual(raised.exception.status, "incomplete")
        sleeper.assert_not_called()

    def test_429_and_5xx_retry_then_surface_incomplete(self):
        for status in (429, 503):
            with self.subTest(status=status):
                error = report.urllib.error.HTTPError(
                    "https://api.github.test/x", status, "", {}, None)
                opener = mock.Mock(side_effect=error)
                sleeper = mock.Mock()
                client = report.GitHubClient(
                    "opaque", "https://api.github.test", "2026-03-10",
                    opener=opener, sleeper=sleeper)
                with self.assertRaises(report.SourceFailure) as raised:
                    client.get("/x")
                self.assertEqual(raised.exception.status, "incomplete")
                self.assertEqual(opener.call_count, 3)
                self.assertEqual(sleeper.call_count, 2)


class EvidenceTests(unittest.TestCase):
    def test_degraded_report_is_usable_but_incomplete_and_unauthorized_are_not(self):
        self.assertEqual(report.exit_code_for_status("complete"), 0)
        self.assertEqual(report.exit_code_for_status("degraded"), 0)
        self.assertEqual(report.exit_code_for_status("incomplete"), 2)
        self.assertEqual(report.exit_code_for_status("unauthorized"), 2)

    def test_append_only_archive_is_sanitized_and_reproducible(self):
        payload = {
            "schemaVersion": report.SCHEMA_VERSION,
            "status": "degraded",
            "organization": "Tuinstra-DEV",
            "generatedAt": "2026-08-12T12:00:00Z",
            "asOfDateUtc": "2026-08-12",
            "issues": [],
            "windows": {
                "currentBillingPeriod": {
                    "label": "Current", "startDate": "2026-08-01", "endDate": "2026-08-12",
                    "billingFacts": {"status": "degraded", "totalsBySku": [],
                                     "repositories": [], "githubBilledMinutes": {
                                         "net": {"status": "degraded", "value": 1}}},
                    "jobDerivedMetrics": {"status": "complete", "warning": "not billing",
                                          "totals": {
                                              "rawExecutionDuration": {"value": 1},
                                              "perJobRoundedEstimate": {"value": 1},
                                              "selfHostedOccupation": {"value": 0},
                                              "unknownRunnerDuration": {"value": 0}}},
                    "topConsumers": [],
                },
                "rolling7Days": {"label": "7", "startDate": "2026-08-06", "endDate": "2026-08-12",
                                 "billingFacts": {"status": "degraded", "totalsBySku": [],
                                                  "repositories": [], "githubBilledMinutes": {
                                                      "net": {"status": "degraded", "value": 1}}}},
                "rolling30Days": {"label": "30", "startDate": "2026-07-14", "endDate": "2026-08-12",
                                  "billingFacts": {"status": "degraded", "totalsBySku": [],
                                                   "repositories": [], "githubBilledMinutes": {
                                                       "net": {"status": "degraded", "value": 1}}}},
            },
            "burnProjection": {"label": "LINEAR MONTH-END BURN PROJECTION",
                               "status": "degraded", "method": "linear_daily_run_rate",
                               "confidence": "low", "projectedNetAmount": 1,
                               "assumptions": "test"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "report.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            first = report.archive_report(source, root / "archive")
            second = report.archive_report(source, root / "archive")
            self.assertEqual(first, second)
            self.assertTrue(first[0].is_file())
            self.assertEqual(first[0].stat().st_mode & 0o777, 0o600)

    def test_archive_rejects_sensitive_field_names(self):
        with self.assertRaises(report.ReportError):
            report.ensure_sanitized({"githubToken": "do-not-store"})

    def test_scheduled_workflow_is_read_only_and_retains_monthly_evidence(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" /
                    "ci-billing-report.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("secrets.CI_BILLING_REPORT_TOKEN", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertIn(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
            workflow,
        )
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1",
            workflow,
        )
        self.assertNotIn("runs-on: ubuntu-latest", workflow)
        self.assertNotIn("contents: write", workflow)

if __name__ == "__main__":
    unittest.main()
