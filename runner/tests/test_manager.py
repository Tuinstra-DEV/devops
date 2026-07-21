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
    def test_token_file_requires_private_permissions_outside_systemd_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "github.token"
            path.write_text("opaque-token\n", encoding="utf-8")
            os.chmod(path, 0o640)
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(manager.RunnerError):
                manager.read_token(path)

    def test_token_file_accepts_systemd_credential_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            credential_directory = Path(directory) / "credentials"
            credential_directory.mkdir(mode=0o700)
            path = credential_directory / "github_token"
            path.write_text("opaque-token\n", encoding="utf-8")
            os.chmod(path, 0o440)
            with mock.patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(credential_directory)}, clear=True):
                self.assertEqual(manager.read_token(path), "opaque-token")

    def test_systemd_credential_boundary_does_not_cover_sibling_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential_directory = root / "credentials"
            credential_directory.mkdir(mode=0o700)
            path = root / "github.token"
            path.write_text("opaque-token\n", encoding="utf-8")
            os.chmod(path, 0o640)
            with mock.patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(credential_directory)}, clear=True), self.assertRaises(manager.RunnerError):
                manager.read_token(path)

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

    def test_delete_runner_treats_not_found_as_already_clean(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(side_effect=manager.NotFound("gone"))
        client.delete_runner("Tuinstra-DEV/gate", 30)

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

    @mock.patch.object(manager, "launch")
    def test_dispatch_persists_github_runner_identity_with_lease(self, launch):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = mock.Mock()
            client.candidate_jobs.return_value = [{"repo": "Tuinstra-DEV/gate", "run_id": 10, "job_id": 20}]
            client.generate_jit.return_value = {
                "encoded_jit_config": base64.b64encode(b"jit").decode(),
                "runner": {"id": 30},
            }
            cfg = {"state_dir": root / "state", "runtime_dir": root / "run",
                   "repositories": ["Tuinstra-DEV/gate"], "runner_label": "trusted-heavy"}
            (root / "run").mkdir()

            self.assertTrue(manager.dispatch_once(cfg, client))

            launch.assert_called_once_with(
                cfg,
                "gh-20",
                mock.ANY,
                metadata={"repo": "Tuinstra-DEV/gate", "runner_id": 30, "run_id": 10, "job_id": 20},
            )

    @mock.patch.object(manager, "launch", side_effect=manager.RunnerError("launch failed"))
    def test_dispatch_defers_runner_cleanup_when_launch_and_delete_fail(self, _launch):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = mock.Mock()
            client.candidate_jobs.return_value = [{"repo": "Tuinstra-DEV/gate", "run_id": 10, "job_id": 20}]
            client.generate_jit.return_value = {
                "encoded_jit_config": base64.b64encode(b"jit").decode(),
                "runner": {"id": 30},
            }
            client.delete_runner.side_effect = manager.RunnerError("cleanup unavailable")
            cfg = {"state_dir": root / "state", "runtime_dir": root / "run",
                   "repositories": ["Tuinstra-DEV/gate"], "runner_label": "trusted-heavy"}
            (root / "run").mkdir()

            with self.assertRaisesRegex(manager.RunnerError, "launch failed"):
                manager.dispatch_once(cfg, client)

            cleanup = manager.StateStore(root / "state").cleanup_items()
            self.assertEqual(cleanup[0]["repo"], "Tuinstra-DEV/gate")
            self.assertEqual(cleanup[0]["runner_id"], 30)

    @mock.patch.object(manager, "launch", side_effect=manager.RunnerError("launch failed"))
    def test_repeated_failed_job_registrations_keep_distinct_cleanup_obligations(self, _launch):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = mock.Mock()
            client.candidate_jobs.return_value = [{"repo": "Tuinstra-DEV/gate", "run_id": 10, "job_id": 20}]
            client.generate_jit.side_effect = [
                {"encoded_jit_config": base64.b64encode(b"jit").decode(), "runner": {"id": 30}},
                {"encoded_jit_config": base64.b64encode(b"jit").decode(), "runner": {"id": 31}},
            ]
            client.delete_runner.side_effect = manager.RunnerError("cleanup unavailable")
            cfg = {"state_dir": root / "state", "runtime_dir": root / "run",
                   "repositories": ["Tuinstra-DEV/gate"], "runner_label": "trusted-heavy"}
            (root / "run").mkdir()

            for _ in range(2):
                with self.assertRaisesRegex(manager.RunnerError, "launch failed"):
                    manager.dispatch_once(cfg, client)

            cleanup = manager.StateStore(root / "state").cleanup_items()
            self.assertEqual({item["runner_id"] for item in cleanup}, {30, 31})

    def test_malformed_jit_cleanup_failure_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = mock.Mock()
            client.candidate_jobs.return_value = [{"repo": "Tuinstra-DEV/gate", "run_id": 10, "job_id": 20}]
            client.generate_jit.return_value = {"encoded_jit_config": None, "runner": {"id": 30}}
            client.delete_runner.side_effect = manager.RunnerError("cleanup unavailable")
            cfg = {"state_dir": root / "state", "runtime_dir": root / "run",
                   "repositories": ["Tuinstra-DEV/gate"], "runner_label": "trusted-heavy"}

            with self.assertRaisesRegex(manager.RunnerError, "invalid JIT"):
                manager.dispatch_once(cfg, client)

            cleanup = manager.StateStore(root / "state").cleanup_items()
            self.assertEqual(cleanup[0]["runner_id"], 30)

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

    @mock.patch.object(manager, "helper")
    def test_reconcile_deletes_persisted_github_runner_before_state(self, helper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock", "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            store.write("finished", {
                "lease": "finished", "launched_at": 1,
                "repo": "Tuinstra-DEV/gate", "runner_id": 30,
            })
            helper.side_effect = [mock.Mock(stdout=b'{"finished":"shut off"}'), mock.Mock()]
            client = mock.Mock()

            manager.reconcile(cfg, client)

            client.delete_runner.assert_called_once_with("Tuinstra-DEV/gate", 30)
            self.assertEqual(store.leases(), [])

    @mock.patch.object(manager, "helper")
    def test_reconcile_moves_failed_github_cleanup_to_tombstone(self, helper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock", "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            store.write("finished", {
                "lease": "finished", "launched_at": 1,
                "repo": "Tuinstra-DEV/gate", "runner_id": 30,
            })
            helper.side_effect = [mock.Mock(stdout=b'{"finished":"shut off"}'), mock.Mock()]
            client = mock.Mock()
            client.delete_runner.side_effect = manager.RunnerError("cleanup unavailable")

            manager.reconcile(cfg, client)

            self.assertEqual(store.leases(), [])
            self.assertEqual(store.cleanup_items()[0]["runner_id"], 30)
            self.assertGreater(store.cleanup_items()[0]["next_retry_at"], 0)

    def test_due_cleanup_tombstone_is_retried_and_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = manager.StateStore(Path(directory))
            store.defer_cleanup("finished", {
                "lease": "finished", "repo": "Tuinstra-DEV/gate", "runner_id": 30,
            }, 1)
            client = mock.Mock()

            manager.retry_pending_cleanup(store, client, 100)

            client.delete_runner.assert_called_once_with("Tuinstra-DEV/gate", 30)
            self.assertEqual(store.cleanup_items(), [])

    def test_registration_obligation_transfers_to_matching_active_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            store = manager.StateStore(Path(directory))
            state = {"lease": "running", "repo": "Tuinstra-DEV/gate", "runner_id": 30}
            store.write_cleanup_obligation("running", state, 100)
            client = mock.Mock()

            manager.retry_pending_cleanup(store, client, 100, [state])

            client.delete_runner.assert_not_called()
            self.assertEqual(store.cleanup_items(), [])

    def test_registration_obligation_waits_for_launch_grace_then_cleans(self):
        with tempfile.TemporaryDirectory() as directory:
            store = manager.StateStore(Path(directory))
            state = {"lease": "pending", "repo": "Tuinstra-DEV/gate", "runner_id": 30}
            store.write_cleanup_obligation("pending", state, 100)
            client = mock.Mock()

            manager.retry_pending_cleanup(store, client, 399)
            client.delete_runner.assert_not_called()
            self.assertEqual(len(store.cleanup_items()), 1)

            manager.retry_pending_cleanup(store, client, 400)
            client.delete_runner.assert_called_once_with("Tuinstra-DEV/gate", 30)
            self.assertEqual(store.cleanup_items(), [])


if __name__ == "__main__":
    unittest.main()
