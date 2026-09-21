#!/usr/bin/env python3
"""Focused safety contract for the production Umami retention job."""

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
ROLE = REPO_ROOT / "infra/ansible/roles/production_umami"
RUNNER = (ROLE / "templates/umami-retention.j2").read_text(encoding="utf-8")
SERVICE = (ROLE / "templates/umami-retention.service.j2").read_text(encoding="utf-8")
TIMER = (ROLE / "templates/umami-retention.timer.j2").read_text(encoding="utf-8")
TASKS = (ROLE / "tasks/main.yml").read_text(encoding="utf-8")
VERIFY = (ROLE / "tasks/verify.yml").read_text(encoding="utf-8")


class UmamiRetentionContractTest(unittest.TestCase):
    def test_runner_is_fail_closed_and_schema_pinned(self) -> None:
        self.assertIn("psql -X --set=ON_ERROR_STOP=1", RUNNER)
        self.assertIn("verify_umami_331_schema", RUNNER)
        self.assertIn("24_lowercase_username", RUNNER)
        self.assertIn("server_version_num", RUNNER)
        self.assertIn("schema verification failed", RUNNER)
        self.assertGreaterEqual(
            RUNNER.count("PERFORM pg_temp.verify_umami_331_schema();"), 10
        )

    def test_runner_has_fixed_cutoff_bounded_batches_and_dry_run(self) -> None:
        self.assertEqual(RUNNER.count("CREATE TEMP TABLE retention_context"), 1)
        self.assertEqual(
            RUNNER.count("LIMIT {{ production_umami_retention_batch_size }}"), 9
        )
        self.assertIn("readonly retention_days={{ production_umami_retention_days }}", RUNNER)
        self.assertIn("--check) mode=check", RUNNER)
        self.assertIn("\\if :dry_run", RUNNER)
        self.assertNotIn("created_at <= cutoff", RUNNER)
        self.assertGreaterEqual(RUNNER.count("created_at IS NULL"), 9)

    def test_only_analytics_tables_are_deleted(self) -> None:
        delete_targets = set(
            re.findall(r"DELETE FROM\s+([a-z_]+)", RUNNER, flags=re.IGNORECASE)
        )
        self.assertEqual(
            delete_targets,
            {
                "event_data",
                "heatmap_event",
                "revenue",
                "session",
                "session_data",
                "session_link",
                "session_replay",
                "session_replay_saved",
                "website_event",
            },
        )
        for protected in (
            "user",
            "website",
            "team",
            "report",
            "segment",
            "board",
            "share",
            "link",
            "pixel",
            "app_setting",
        ):
            self.assertNotRegex(RUNNER, rf"DELETE FROM\s+{protected}\b")

    def test_parent_and_session_guards_are_present(self) -> None:
        self.assertIn("child.website_event_id = doomed.event_id", RUNNER)
        self.assertIn("child.event_id = doomed.event_id", RUNNER)
        self.assertIn("parent.event_id = item.website_event_id", RUNNER)
        for child in (
            "website_event",
            "revenue",
            "session_data",
            "session_link",
            "session_replay",
            "heatmap_event",
        ):
            self.assertIn(
                f"SELECT 1 FROM {child} child WHERE child.session_id = item.session_id",
                RUNNER,
            )

    def test_host_lock_status_and_schedule_contract(self) -> None:
        self.assertIn("/usr/bin/flock -n", RUNNER)
        self.assertIn("pg_try_advisory_lock", RUNNER)
        self.assertIn("statement_timeout = '10min'", RUNNER)
        self.assertIn("TimeoutStartSec=60min", SERVICE)
        self.assertIn("last-success.json", RUNNER)
        self.assertIn("/usr/bin/chmod 0600", RUNNER)
        self.assertIn("ProtectSystem=strict", SERVICE)
        self.assertIn("NoNewPrivileges=true", SERVICE)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", SERVICE)
        self.assertIn("OnCalendar=*-*-* 04:15:00 Europe/Amsterdam", TIMER)
        self.assertIn("Persistent=true", TIMER)
        self.assertIn("RandomizedDelaySec=15m", TIMER)

    def test_ansible_installs_and_verifies_root_only_runtime(self) -> None:
        self.assertIn("mode: '0700'", TASKS)
        self.assertIn("umami-retention.timer", TASKS)
        self.assertIn("daemon_reload: true", TASKS)
        self.assertIn("systemd-analyze", VERIFY)
        self.assertIn("--check", VERIFY)


if __name__ == "__main__":
    unittest.main()
