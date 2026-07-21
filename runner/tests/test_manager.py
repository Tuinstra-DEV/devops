import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import json
import io
import urllib.error

import ci_runner_manager as manager


class ManagerTests(unittest.TestCase):
    def test_lease_validation_accepts_expected_identifier(self):
        self.assertEqual(manager.validate_lease_id("job-123_abc.1"), "job-123_abc.1")

    def test_lease_validation_rejects_path_traversal(self):
        for value in ("../job", "/tmp/job", "job name", "", "a" * 65):
            with self.subTest(value=value), self.assertRaises(manager.RunnerError):
                manager.validate_lease_id(value)

    def test_jit_file_requires_private_permissions_and_base64(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jit"
            path.write_bytes(base64.b64encode(b"opaque-jit-payload"))
            os.chmod(path, 0o600)
            self.assertEqual(manager.read_jit_config(path), path.read_bytes())
            os.chmod(path, 0o640)
            with self.assertRaises(manager.RunnerError):
                manager.read_jit_config(path)

    def test_jit_file_rejects_invalid_base64(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jit"
            path.write_text("not base64!", encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaises(manager.RunnerError):
                manager.read_jit_config(path)

    @mock.patch.object(manager.os, "getloadavg", return_value=(1.0, 1.0, 1.0))
    @mock.patch.object(manager.os, "cpu_count", return_value=4)
    @mock.patch.object(manager, "memory_available_mib", return_value=20000)
    @mock.patch.object(manager.shutil, "disk_usage")
    def test_capacity_rejects_small_cpu_host(self, disk, _memory, _cpu, _load):
        disk.return_value = mock.Mock(free=200 * 1024**3)
        cfg = {"min_free_memory_mib": 14000, "min_free_disk_gib": 140, "max_load_1m": 8, "overlay_root": "/x"}
        self.assertIn("host has fewer than 8 logical CPUs", manager.capacity_errors(cfg))

    @mock.patch.object(manager, "helper")
    def test_reconcile_destroys_powered_off_and_orphan_domains(self, helper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock", "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            store.write("finished", {"lease": "finished"})
            helper.side_effect = [mock.Mock(stdout=json.dumps({"finished": "shut off", "orphan": "running"}).encode()),
                                  mock.Mock(), mock.Mock()]
            manager.reconcile(cfg)
            calls = [call.args[1:] for call in helper.call_args_list]
            self.assertIn(("destroy", "finished"), calls)
            self.assertIn(("destroy", "orphan"), calls)
            self.assertEqual(store.leases(), [])

    @mock.patch.object(manager, "capacity_errors", return_value=[])
    @mock.patch.object(manager, "helper")
    def test_launch_rejects_untracked_existing_domain(self, helper, _capacity):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jit = root / "jit"
            jit.write_bytes(base64.b64encode(b"opaque"))
            os.chmod(jit, 0o600)
            helper.return_value = mock.Mock(stdout=b'{"orphan":"running"}')
            cfg = {"state_dir": root / "state", "lock_file": root / "lock"}
            with self.assertRaisesRegex(manager.RunnerError, "domain already exists"):
                manager.launch(cfg, "new-job", jit)

    def test_candidate_jobs_filter_status_and_label(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(side_effect=[
            {"workflow_runs": [{"id": 10}]},
            {"jobs": [{"id": 1, "status": "queued", "labels": ["self-hosted", "trusted-heavy"]},
                      {"id": 2, "status": "queued", "labels": ["self-hosted"]},
                      {"id": 3, "status": "in_progress", "labels": ["trusted-heavy"]}]},
            {"workflow_runs": []},
        ])
        self.assertEqual(client.candidate_jobs("Tuinstra-DEV/gate", "trusted-heavy"),
                         [{"repo": "Tuinstra-DEV/gate", "run_id": 10, "job_id": 1}])

    @mock.patch.object(manager.urllib.request, "urlopen")
    def test_rate_limit_is_fail_closed_and_does_not_expose_token(self, urlopen):
        headers = {"Retry-After": "60"}
        urlopen.side_effect = urllib.error.HTTPError("https://api.github.com", 429, "limited", headers, io.BytesIO())
        client = manager.GitHubClient("top-secret-token")
        with self.assertRaises(manager.RateLimited) as raised:
            client.request("GET", "/rate-limited")
        self.assertEqual(raised.exception.retry_after, 60)
        self.assertNotIn("top-secret-token", str(raised.exception))

    @mock.patch.object(manager.urllib.request, "urlopen")
    def test_api_error_is_sanitized(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError("https://api.github.com", 500, "body may be sensitive", {}, io.BytesIO())
        with self.assertRaisesRegex(manager.RunnerError, "HTTP 500") as raised:
            manager.GitHubClient("top-secret-token").request("GET", "/failure")
        self.assertNotIn("top-secret-token", str(raised.exception))
        self.assertNotIn("body may be sensitive", str(raised.exception))

    def test_dispatch_history_deduplicates_job(self):
        with tempfile.TemporaryDirectory() as directory:
            history = manager.DispatchHistory(Path(directory))
            history.add("Tuinstra-DEV/gate:123", 1000)
            self.assertTrue(history.contains("Tuinstra-DEV/gate:123", 1001))
            self.assertFalse(history.contains("Tuinstra-DEV/gate:123", 90000))

    @mock.patch.object(manager, "launch", side_effect=manager.RunnerError("launch failed"))
    def test_dispatch_deletes_generated_runner_when_launch_fails(self, _launch):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = mock.Mock()
            client.candidate_jobs.return_value = [{"repo": "Tuinstra-DEV/gate", "run_id": 10, "job_id": 20}]
            client.generate_jit.return_value = {"encoded_jit_config": base64.b64encode(b"jit").decode(), "runner": {"id": 30}}
            cfg = {"state_dir": root / "state", "runtime_dir": root / "run",
                   "repositories": ["Tuinstra-DEV/gate"], "runner_label": "trusted-heavy"}
            (root / "run").mkdir()
            with self.assertRaisesRegex(manager.RunnerError, "launch failed"):
                manager.dispatch_once(cfg, client)
            client.delete_runner.assert_called_once_with("Tuinstra-DEV/gate", 30)
            self.assertEqual(list((root / "run").iterdir()), [])

    @mock.patch.object(manager.time, "time", return_value=1001)
    def test_dispatch_skips_job_in_history(self, _time):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            manager.StateStore(state)
            manager.DispatchHistory(state).add("Tuinstra-DEV/gate:20", 1000)
            client = mock.Mock()
            client.candidate_jobs.return_value = [{"repo": "Tuinstra-DEV/gate", "run_id": 10, "job_id": 20}]
            cfg = {"state_dir": state, "repositories": ["Tuinstra-DEV/gate"], "runner_label": "trusted-heavy"}
            self.assertFalse(manager.dispatch_once(cfg, client))
            client.generate_jit.assert_not_called()

    @mock.patch.object(manager, "helper")
    @mock.patch.object(manager.time, "time", return_value=10000)
    def test_reconcile_destroys_expired_running_lease(self, _time, helper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock", "max_lease_seconds": 7200}
            manager.StateStore(cfg["state_dir"]).write("expired", {"lease": "expired", "launched_at": 1})
            helper.side_effect = [mock.Mock(stdout=b'{"expired":"running"}'), mock.Mock()]
            manager.reconcile(cfg)
            self.assertIn(("destroy", "expired"), [call.args[1:] for call in helper.call_args_list])


if __name__ == "__main__":
    unittest.main()
