import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import json
import io
import subprocess
import urllib.error

import ci_runner_manager as manager


class ManagerTests(unittest.TestCase):
    def helper_connection(self, response):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        packets = []
        connection.send.side_effect = lambda packet: packets.append(packet) or len(packet)
        connection.recvmsg.return_value = (
            json.dumps(response, separators=(",", ":")).encode("utf-8"), [], 0, None
        )
        return connection, packets

    @mock.patch.object(manager.secrets, "token_hex", return_value="a" * 32)
    @mock.patch.object(manager.socket, "socket")
    def test_helper_list_uses_bounded_seqpacket_protocol(self, socket_factory, _token):
        connection, packets = self.helper_connection(
            {"v": 1, "id": "a" * 32, "ok": True, "result": {"lease": "running"}}
        )
        socket_factory.return_value = connection
        result = manager.helper({"helper_socket": "/run/helper.sock"}, "list")
        self.assertEqual(json.loads(result.stdout), {"lease": "running"})
        request = json.loads(packets[0])
        self.assertEqual(set(request), {"v", "id", "op"})
        self.assertEqual(request["op"], "list")

    @mock.patch.object(manager.secrets, "token_hex", return_value="b" * 32)
    @mock.patch.object(manager.socket, "socket")
    def test_helper_launch_sends_jit_as_separate_packet(self, socket_factory, _token):
        connection, packets = self.helper_connection(
            {"v": 1, "id": "b" * 32, "ok": True, "result": None}
        )
        socket_factory.return_value = connection
        jit = base64.b64encode(b"opaque-jit")
        manager.helper({"helper_socket": "/run/helper.sock"}, "launch", "lease-1", stdin=jit)
        self.assertEqual(packets[1], jit)
        self.assertNotIn(jit, packets[0])

    @mock.patch.object(manager.secrets, "token_hex", return_value="c" * 32)
    @mock.patch.object(manager.socket, "socket")
    def test_helper_failure_exposes_only_allowlisted_code(self, socket_factory, _token):
        connection, _packets = self.helper_connection(
            {"v": 1, "id": "c" * 32, "ok": False, "error": "operation_failed"}
        )
        socket_factory.return_value = connection
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            manager.helper({"helper_socket": "/run/helper.sock"}, "list")
        self.assertEqual(raised.exception.stderr, b"operation_failed")

    @mock.patch.object(manager.secrets, "token_hex", return_value="d" * 32)
    @mock.patch.object(manager.socket, "socket")
    def test_helper_rejects_mismatched_response_id(self, socket_factory, _token):
        connection, _packets = self.helper_connection(
            {"v": 1, "id": "e" * 32, "ok": True, "result": {}}
        )
        socket_factory.return_value = connection
        with self.assertRaisesRegex(manager.RunnerError, "invalid response"):
            manager.helper({"helper_socket": "/run/helper.sock"}, "list")

    @mock.patch.object(manager.secrets, "token_hex", return_value="f" * 32)
    @mock.patch.object(manager.socket, "socket")
    def test_helper_rejects_duplicate_response_members(self, socket_factory, _token):
        connection, _packets = self.helper_connection({})
        connection.recvmsg.return_value = (
            ('{"v":1,"id":"' + "f" * 32 +
             '","ok":true,"ok":true,"result":{}}').encode("utf-8"), [], 0, None
        )
        socket_factory.return_value = connection
        with self.assertRaisesRegex(manager.RunnerError, "invalid response"):
            manager.helper({"helper_socket": "/run/helper.sock"}, "list")

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

    @mock.patch.object(manager, "capacity_errors", return_value=[])
    @mock.patch.object(manager, "helper")
    def test_failed_launch_keeps_durable_cleanup_state(self, helper, _capacity):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock",
                   "max_lease_seconds": 7200}
            helper.side_effect = [
                mock.Mock(stdout=b"{}"),
                manager.RunnerError("ambiguous launch completion"),
            ]

            with self.assertRaisesRegex(manager.RunnerError, "ambiguous"):
                manager.launch(cfg, "new-job", base64.b64encode(b"opaque"))

            states = manager.StateStore(cfg["state_dir"]).leases()
            self.assertEqual(states[0]["lease"], "new-job")
            self.assertEqual(states[0]["phase"], "provisioning_failed")

            helper.side_effect = [mock.Mock(stdout=b'{"new-job":"running"}'), mock.Mock()]
            manager.reconcile(cfg)
            self.assertEqual(manager.StateStore(cfg["state_dir"]).leases(), [])
            self.assertIn(("destroy", "new-job"),
                          [call.args[1:] for call in helper.call_args_list])

    @mock.patch.object(manager.time, "time", return_value=1000)
    @mock.patch.object(manager, "capacity_errors", return_value=[])
    @mock.patch.object(manager, "helper")
    def test_dispatch_launch_records_history_and_lease_under_lifecycle_lock(
            self, helper, _capacity, _time):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock"}
            helper.side_effect = [mock.Mock(stdout=b"{}"), mock.Mock()]

            manager.launch(
                cfg,
                "gh-20",
                base64.b64encode(b"opaque"),
                metadata={
                    "repo": "Tuinstra-DEV/gate", "runner_id": 30,
                    "run_id": 10, "job_id": 20,
                },
                dispatch_history_key="Tuinstra-DEV/gate:20",
            )

            store = manager.StateStore(cfg["state_dir"])
            self.assertEqual(store.leases()[0]["dispatch_attempts"], 1)
            self.assertEqual(store.leases()[0]["phase"], "running")
            self.assertEqual(store.cleanup_items(), [])
            self.assertTrue(
                manager.DispatchHistory(cfg["state_dir"]).contains("Tuinstra-DEV/gate:20", 1001)
            )

    @mock.patch.object(manager.time, "time", return_value=1000)
    @mock.patch.object(manager, "capacity_errors", return_value=[])
    @mock.patch.object(manager, "helper")
    def test_dispatch_launch_rechecks_pending_cleanup_under_lifecycle_lock(
            self, helper, _capacity, _time):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock"}
            store = manager.StateStore(cfg["state_dir"])
            store.write_cleanup_state({
                "lease": "gh-20", "repo": "Tuinstra-DEV/gate", "runner_id": 30,
                "job_id": 20, "phase": "cleanup_pending", "next_retry_at": 2000,
            })

            with self.assertRaisesRegex(manager.RunnerError, "pending cleanup"):
                manager.launch(
                    cfg,
                    "gh-20",
                    base64.b64encode(b"opaque"),
                    metadata={
                        "repo": "Tuinstra-DEV/gate", "runner_id": 31,
                        "run_id": 10, "job_id": 20,
                    },
                    dispatch_history_key="Tuinstra-DEV/gate:20",
                )

            helper.assert_not_called()

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

    def test_find_assigned_job_matches_github_reported_runner_name(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(side_effect=[
            {"workflow_runs": [{"id": 10}, {"id": 11}]},
            {"jobs": [{"id": 20, "status": "queued", "runner_name": ""}]},
            {"jobs": [{"id": 83, "status": "in_progress", "runner_id": 30,
                       "runner_name": "sanctuary-20"}]},
            {"workflow_runs": []},
            {"workflow_runs": []},
        ])

        self.assertEqual(
            client.find_assigned_job("Tuinstra-DEV/gate", 30, "sanctuary-20"),
            {"repo": "Tuinstra-DEV/gate", "run_id": 11, "job_id": 83},
        )

    def test_find_assigned_job_captures_fast_completed_job_by_runner_id(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(side_effect=[
            {"workflow_runs": []},
            {"workflow_runs": [{"id": 11}]},
            {"jobs": [{"id": 83, "status": "completed", "runner_id": 30,
                       "runner_name": "sanctuary-20"}]},
            {"workflow_runs": []},
        ])

        self.assertEqual(
            client.find_assigned_job("Tuinstra-DEV/gate", 30, "sanctuary-20"),
            {"repo": "Tuinstra-DEV/gate", "run_id": 11, "job_id": 83},
        )

    def test_teardown_finds_completed_job_inside_queued_workflow_run(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(side_effect=[
            {"workflow_runs": []},
            {"workflow_runs": []},
            {"workflow_runs": [{"id": 11}]},
            {"jobs": [{"id": 77, "status": "completed", "runner_id": 36,
                       "runner_name": "sanctuary-20"}]},
        ])

        self.assertEqual(
            client.find_assigned_job("Tuinstra-DEV/gate", 36, "sanctuary-20"),
            {"repo": "Tuinstra-DEV/gate", "run_id": 11, "job_id": 77},
        )

    def test_healthy_assignment_search_does_not_scan_queued_workflow_runs(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(return_value={"workflow_runs": []})

        self.assertIsNone(client.find_assigned_job(
            "Tuinstra-DEV/gate", 36, "sanctuary-20", include_completed=False
        ))

        client.request.assert_called_once_with(
            "GET", "/repos/Tuinstra-DEV/gate/actions/runs?status=in_progress"
            f"&per_page={manager.ASSIGNMENT_RUNS_PER_STATUS}&page=1"
        )

    def test_find_assigned_job_ignores_reused_name_with_different_runner_id(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(side_effect=[
            {"workflow_runs": []},
            {"workflow_runs": [{"id": 10}, {"id": 11}]},
            {"jobs": [{"id": 21, "status": "completed", "runner_id": 29,
                       "runner_name": "sanctuary-20"}]},
            {"jobs": [{"id": 83, "status": "completed", "runner_id": 30,
                       "runner_name": "sanctuary-20"}]},
            {"workflow_runs": []},
        ])

        self.assertEqual(
            client.find_assigned_job("Tuinstra-DEV/gate", 30, "sanctuary-20"),
            {"repo": "Tuinstra-DEV/gate", "run_id": 11, "job_id": 83},
        )

    def test_find_assigned_job_ignores_single_stale_name_without_runner_id(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(side_effect=[
            {"workflow_runs": []},
            {"workflow_runs": [{"id": 10}]},
            {"jobs": [{"id": 21, "status": "completed",
                       "runner_name": "sanctuary-20"}]},
            {"workflow_runs": []},
        ])

        self.assertIsNone(
            client.find_assigned_job("Tuinstra-DEV/gate", 30, "sanctuary-20")
        )

    def test_find_assigned_job_fails_closed_for_ambiguous_runner_id(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(side_effect=[
            {"workflow_runs": []},
            {"workflow_runs": [{"id": 10}, {"id": 11}]},
            {"jobs": [{"id": 21, "status": "completed", "runner_id": 30,
                       "runner_name": "sanctuary-20"}]},
            {"jobs": [{"id": 83, "status": "completed", "runner_id": 30,
                       "runner_name": "sanctuary-20"}]},
            {"workflow_runs": []},
        ])

        with self.assertRaisesRegex(manager.RunnerError, "ambiguous"):
            client.find_assigned_job("Tuinstra-DEV/gate", 30, "sanctuary-20")

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

    def test_get_job_uses_repository_scoped_actions_endpoint(self):
        client = manager.GitHubClient("secret")
        client.request = mock.Mock(return_value={"id": 20, "status": "queued", "runner_name": ""})

        self.assertEqual(
            client.get_job("Tuinstra-DEV/gate", 20),
            {"id": 20, "status": "queued", "runner_name": ""},
        )
        client.request.assert_called_once_with("GET", "/repos/Tuinstra-DEV/gate/actions/jobs/20")

    def test_dispatch_history_deduplicates_job(self):
        with tempfile.TemporaryDirectory() as directory:
            history = manager.DispatchHistory(Path(directory))
            history.add("Tuinstra-DEV/gate:123", 1000)
            self.assertTrue(history.contains("Tuinstra-DEV/gate:123", 1001))
            self.assertFalse(history.contains("Tuinstra-DEV/gate:123", 87400))

    def test_dispatch_history_retries_unassigned_job_after_bounded_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            history = manager.DispatchHistory(Path(directory))
            history.add("Tuinstra-DEV/gate:123", 1000)

            delay = history.retry_after_cooldown("Tuinstra-DEV/gate:123", 1001)

            self.assertEqual(delay, manager.DISPATCH_RETRY_BASE_SECONDS)
            self.assertTrue(history.contains("Tuinstra-DEV/gate:123", 1060))
            self.assertFalse(history.contains("Tuinstra-DEV/gate:123", 1061))

    def test_dispatch_history_retry_backoff_is_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            history = manager.DispatchHistory(Path(directory))
            key = "Tuinstra-DEV/gate:123"
            now = 1000
            delay = 0
            for _ in range(20):
                history.add(key, now)
                delay = history.retry_after_cooldown(key, now)
                now += delay

            self.assertEqual(delay, manager.DISPATCH_RETRY_MAX_SECONDS)

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

            with self.assertLogs("ci-runner-manager", level="INFO") as logs:
                self.assertTrue(manager.dispatch_once(cfg, client))

            launch.assert_called_once_with(
                cfg,
                "gh-20",
                mock.ANY,
                metadata={
                    "repo": "Tuinstra-DEV/gate", "runner_id": 30,
                    "runner_name": "sanctuary-20", "trigger_run_id": 10,
                    "trigger_job_id": 20,
                },
                dispatch_history_key="Tuinstra-DEV/gate:20",
            )
            self.assertNotIn("actual_job_id", launch.call_args.kwargs["metadata"])
            audit = "\n".join(logs.output)
            self.assertIn("trigger_job_id=20 assignment=unverified", audit)
            self.assertNotIn("actual_job_id=20", audit)
            self.assertEqual(launch.call_args.args[2], base64.b64encode(b"jit"))
            self.assertEqual(list((root / "run").iterdir()), [])

    @mock.patch.object(manager, "launch")
    def test_dispatch_rejects_conflicting_reported_runner_name(self, launch):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = mock.Mock()
            client.candidate_jobs.return_value = [
                {"repo": "Tuinstra-DEV/gate", "run_id": 10, "job_id": 20}
            ]
            client.generate_jit.return_value = {
                "encoded_jit_config": base64.b64encode(b"jit").decode(),
                "runner": {"id": 30, "name": "unexpected-runner"},
            }
            cfg = {"state_dir": root / "state", "runtime_dir": root / "run",
                   "repositories": ["Tuinstra-DEV/gate"],
                   "runner_label": "trusted-heavy"}
            (root / "run").mkdir()

            with self.assertRaisesRegex(manager.RunnerError, "invalid JIT"):
                manager.dispatch_once(cfg, client)

            launch.assert_not_called()
            client.delete_runner.assert_called_once_with("Tuinstra-DEV/gate", 30)

    def test_dispatch_preserves_host_cleanup_when_launch_and_delete_fail(self):
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

            def ambiguous_launch(launch_cfg, lease, _jit, *, metadata=None,
                                 dispatch_history_key=None):
                state = {"lease": lease, "phase": "provisioning_failed", "launched_at": 1}
                state.update(metadata or {})
                self.assertEqual(dispatch_history_key, "Tuinstra-DEV/gate:20")
                manager.StateStore(Path(launch_cfg["state_dir"])).write(lease, state)
                raise manager.RunnerError("launch failed")

            with mock.patch.object(manager, "launch", side_effect=ambiguous_launch), \
                    self.assertRaisesRegex(manager.RunnerError, "launch failed"):
                manager.dispatch_once(cfg, client)

            store = manager.StateStore(root / "state")
            cleanup = store.cleanup_items()
            self.assertEqual(cleanup[0]["repo"], "Tuinstra-DEV/gate")
            self.assertEqual(cleanup[0]["runner_id"], 30)
            self.assertEqual(store.leases()[0]["phase"], "provisioning_failed")

    @mock.patch.object(manager, "launch", side_effect=manager.RunnerError("launch failed"))
    def test_pending_cleanup_blocks_duplicate_job_registration(self, _launch):
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

            with self.assertRaisesRegex(manager.RunnerError, "launch failed"):
                manager.dispatch_once(cfg, client)

            self.assertFalse(manager.dispatch_once(cfg, client))

            cleanup = manager.StateStore(root / "state").cleanup_items()
            self.assertEqual({item["runner_id"] for item in cleanup}, {30})
            client.generate_jit.assert_called_once()

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

    @mock.patch.object(manager.time, "time", return_value=1001)
    def test_dispatch_skips_job_with_pending_handoff_when_history_is_missing(self, _time):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            store = manager.StateStore(state)
            store.write_cleanup_state({
                "lease": "gh-20", "repo": "Tuinstra-DEV/gate", "runner_id": 30,
                "job_id": 20, "phase": "handoff_pending", "next_retry_at": 2000,
            })
            client = mock.Mock()
            client.candidate_jobs.return_value = [
                {"repo": "Tuinstra-DEV/gate", "run_id": 10, "job_id": 20}
            ]
            cfg = {"state_dir": state, "repositories": ["Tuinstra-DEV/gate"],
                   "runner_label": "trusted-heavy"}

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

    @mock.patch.object(manager.time, "time", return_value=1001)
    @mock.patch.object(manager, "helper")
    def test_reconcile_records_verified_actual_job_without_relabeling_trigger(
            self, helper, _time):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock",
                   "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            store.write("gh-20", {
                "lease": "gh-20", "phase": "running", "launched_at": 1,
                "repo": "Tuinstra-DEV/gate", "runner_id": 30,
                "runner_name": "sanctuary-20", "trigger_run_id": 10,
                "trigger_job_id": 20,
            })
            helper.return_value = mock.Mock(stdout=b'{"gh-20":"running"}')
            client = mock.Mock()
            client.find_assigned_job.return_value = {
                "repo": "Tuinstra-DEV/gate", "run_id": 11, "job_id": 83,
            }

            with self.assertLogs("ci-runner-manager", level="INFO") as logs:
                manager.reconcile(cfg, client)

            state = store.leases()[0]
            self.assertEqual(state["trigger_job_id"], 20)
            self.assertEqual(state["actual_job_id"], 83)
            self.assertEqual(state["actual_run_id"], 11)
            self.assertEqual(state["runner_id"], 30)
            self.assertEqual(state["lease"], "gh-20")
            self.assertIn("trigger_job_id=20 actual_job_id=83", "\n".join(logs.output))
            client.find_assigned_job.assert_called_once_with(
                "Tuinstra-DEV/gate", 30, "sanctuary-20", include_completed=False
            )

    @mock.patch.object(manager.time, "time", return_value=1001)
    @mock.patch.object(manager, "helper")
    def test_label_steal_cleanup_retries_trigger_and_retains_actual_audit(self, helper, _time):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock",
                   "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            history = manager.DispatchHistory(cfg["state_dir"])
            state = {
                "lease": "gh-20", "phase": "running", "launched_at": 1,
                "repo": "Tuinstra-DEV/gate", "runner_id": 30,
                "runner_name": "sanctuary-20", "trigger_run_id": 10,
                "trigger_job_id": 20, "dispatch_attempts": 1,
            }
            store.write("gh-20", state)
            history.add("Tuinstra-DEV/gate:20", 1000)
            helper.side_effect = [mock.Mock(stdout=b'{"gh-20":"shut off"}'), mock.Mock()]
            client = mock.Mock()
            client.find_assigned_job.return_value = {
                "repo": "Tuinstra-DEV/gate", "run_id": 11, "job_id": 83,
            }
            client.get_job.return_value = {
                "id": 20, "status": "queued", "runner_name": "",
            }

            manager.reconcile(cfg, client)

            pending = store.cleanup_items()[0]
            self.assertEqual(pending["trigger_job_id"], 20)
            self.assertEqual(pending["actual_job_id"], 83)
            self.assertEqual(pending["runner_id"], 30)
            self.assertEqual(pending["lease"], "gh-20")
            client.find_assigned_job.assert_called_once_with(
                "Tuinstra-DEV/gate", 30, "sanctuary-20", include_completed=True
            )
            manager.retry_pending_cleanup(store, client, 1031)

            client.get_job.assert_called_once_with("Tuinstra-DEV/gate", 20)
            self.assertEqual(store.cleanup_items(), [])
            self.assertFalse(history.contains("Tuinstra-DEV/gate:20", 1091))

    @mock.patch.object(manager.time, "time", return_value=1001)
    @mock.patch.object(manager, "helper")
    def test_manual_destroy_preserves_dispatch_handoff_until_queued_retry(self, helper, _time):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock"}
            store = manager.StateStore(cfg["state_dir"])
            history = manager.DispatchHistory(cfg["state_dir"])
            self.write_finished_dispatch(store, history)
            client = mock.Mock()
            client.get_job.return_value = {"id": 20, "status": "queued", "runner_name": ""}

            manager.destroy(cfg, "gh-20", client)

            helper.assert_called_once_with(cfg, "destroy", "gh-20")
            client.delete_runner.assert_called_once_with("Tuinstra-DEV/gate", 30)
            self.assertEqual(store.leases(), [])
            self.assertEqual(store.cleanup_items()[0]["phase"], "handoff_pending")

            manager.retry_pending_cleanup(store, client, 1031)

            self.assertEqual(store.cleanup_items(), [])
            self.assertFalse(history.contains("Tuinstra-DEV/gate:20", 1091))

    @staticmethod
    def write_finished_dispatch(store, history, *, now=1000):
        store.write("gh-20", {
            "lease": "gh-20", "launched_at": 1,
            "repo": "Tuinstra-DEV/gate", "runner_id": 30, "job_id": 20,
            "dispatch_attempts": 1,
        })
        history.add("Tuinstra-DEV/gate:20", now)

    @mock.patch.object(manager, "helper")
    @mock.patch.object(manager.time, "time", return_value=1001)
    def test_reconcile_defers_unassigned_handoff_until_grace_then_cooldown(self, _time, helper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock", "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            history = manager.DispatchHistory(cfg["state_dir"])
            self.write_finished_dispatch(store, history)
            helper.side_effect = [mock.Mock(stdout=b'{"gh-20":"shut off"}'), mock.Mock()]
            client = mock.Mock()
            client.get_job.return_value = {
                "id": 20, "status": "queued", "runner_name": "stale-runner-name",
            }

            manager.reconcile(cfg, client)

            self.assertEqual(store.leases(), [])
            self.assertEqual(store.cleanup_items()[0]["phase"], "handoff_pending")
            client.get_job.assert_not_called()
            self.assertTrue(history.contains("Tuinstra-DEV/gate:20", 1030))

            manager.retry_pending_cleanup(store, client, 1031)

            self.assertEqual(store.cleanup_items(), [])
            self.assertTrue(history.contains("Tuinstra-DEV/gate:20", 1090))
            self.assertFalse(history.contains("Tuinstra-DEV/gate:20", 1091))
            client.delete_runner.assert_called_once_with("Tuinstra-DEV/gate", 30)

    @mock.patch.object(manager, "helper")
    @mock.patch.object(manager.time, "time", return_value=1001)
    def test_reconcile_completed_target_keeps_terminal_history(self, _time, helper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock", "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            history = manager.DispatchHistory(cfg["state_dir"])
            self.write_finished_dispatch(store, history)
            helper.side_effect = [mock.Mock(stdout=b'{"gh-20":"shut off"}'), mock.Mock()]
            client = mock.Mock()
            client.get_job.return_value = {
                "id": 20, "status": "completed", "conclusion": "cancelled",
                "runner_name": "sanctuary-20",
            }

            manager.reconcile(cfg, client)
            manager.retry_pending_cleanup(store, client, 1031)

            self.assertEqual(store.cleanup_items(), [])
            self.assertTrue(history.contains("Tuinstra-DEV/gate:20", 1031))

    @mock.patch.object(manager, "helper")
    @mock.patch.object(manager.time, "time", return_value=1001)
    def test_reconcile_api_failure_preserves_handoff_for_retry(self, _time, helper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock", "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            history = manager.DispatchHistory(cfg["state_dir"])
            self.write_finished_dispatch(store, history)
            helper.side_effect = [mock.Mock(stdout=b'{"gh-20":"shut off"}'), mock.Mock()]
            client = mock.Mock()
            client.get_job.side_effect = manager.RunnerError("GitHub API unavailable")

            manager.reconcile(cfg, client)
            manager.retry_pending_cleanup(store, client, 1031)

            pending = store.cleanup_items()[0]
            self.assertEqual(pending["phase"], "handoff_pending")
            self.assertGreater(pending["next_retry_at"], 1031)
            self.assertTrue(history.contains("Tuinstra-DEV/gate:20", 1031))

    @mock.patch.object(manager, "helper")
    @mock.patch.object(manager.time, "time", return_value=1001)
    def test_reconcile_in_progress_handoff_is_rechecked(self, _time, helper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock", "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            history = manager.DispatchHistory(cfg["state_dir"])
            self.write_finished_dispatch(store, history)
            helper.side_effect = [mock.Mock(stdout=b'{"gh-20":"shut off"}'), mock.Mock()]
            client = mock.Mock()
            client.get_job.return_value = {
                "id": 20, "status": "in_progress", "runner_name": "another-runner",
            }

            manager.reconcile(cfg, client)
            manager.retry_pending_cleanup(store, client, 1031)

            self.assertEqual(store.cleanup_items()[0]["phase"], "handoff_pending")
            self.assertTrue(history.contains("Tuinstra-DEV/gate:20", 1031))

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

    @mock.patch.object(manager, "helper")
    @mock.patch.object(manager.time, "time", return_value=1001)
    def test_cleanup_retry_preserves_dispatch_handoff_until_queued_retry(self, _time, helper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock", "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            history = manager.DispatchHistory(cfg["state_dir"])
            self.write_finished_dispatch(store, history)
            helper.side_effect = [mock.Mock(stdout=b'{"gh-20":"shut off"}'), mock.Mock()]
            client = mock.Mock()
            client.delete_runner.side_effect = [manager.RunnerError("cleanup unavailable"), None]
            client.get_job.return_value = {"id": 20, "status": "queued", "runner_name": ""}

            manager.reconcile(cfg, client)

            self.assertEqual(store.cleanup_items()[0]["phase"], "cleanup_pending")
            manager.retry_pending_cleanup(store, client, 1031)
            self.assertEqual(store.cleanup_items()[0]["phase"], "handoff_pending")
            manager.retry_pending_cleanup(store, client, 1061)

            self.assertEqual(store.cleanup_items(), [])
            self.assertFalse(history.contains("Tuinstra-DEV/gate:20", 1121))

    def test_history_write_failure_retains_durable_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            store = manager.StateStore(Path(directory))
            history = manager.DispatchHistory(store.directory)
            state = {
                "lease": "gh-20", "repo": "Tuinstra-DEV/gate", "runner_id": 30,
                "job_id": 20, "phase": "handoff_pending", "next_retry_at": 1,
            }
            history.add("Tuinstra-DEV/gate:20", 1000)
            store.write_cleanup_state(state)
            client = mock.Mock()
            client.get_job.return_value = {"id": 20, "status": "queued", "runner_name": ""}

            with mock.patch.object(manager.DispatchHistory, "write", side_effect=OSError("disk full")), \
                    self.assertRaisesRegex(OSError, "disk full"):
                manager.retry_pending_cleanup(store, client, 1031)

            self.assertEqual(store.cleanup_items()[0]["phase"], "handoff_pending")

    def test_handoff_recreates_missing_history_with_bounded_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            store = manager.StateStore(Path(directory))
            state = {
                "lease": "gh-20", "repo": "Tuinstra-DEV/gate", "runner_id": 30,
                "job_id": 20, "dispatch_attempts": 3,
                "phase": "handoff_pending", "next_retry_at": 1,
            }
            store.write_cleanup_state(state)
            client = mock.Mock()
            client.get_job.return_value = {"id": 20, "status": "queued", "runner_name": ""}

            manager.retry_pending_cleanup(store, client, 1000)

            history = manager.DispatchHistory(store.directory)
            self.assertTrue(history.contains("Tuinstra-DEV/gate:20", 1239))
            self.assertFalse(history.contains("Tuinstra-DEV/gate:20", 1240))
            self.assertEqual(store.cleanup_items(), [])

    def test_missing_github_job_finishes_handoff_without_retry_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            store = manager.StateStore(Path(directory))
            history = manager.DispatchHistory(store.directory)
            state = {
                "lease": "gh-20", "repo": "Tuinstra-DEV/gate", "runner_id": 30,
                "job_id": 20, "phase": "handoff_pending", "next_retry_at": 1,
            }
            history.add("Tuinstra-DEV/gate:20", 1000)
            store.write_cleanup_state(state)
            client = mock.Mock()
            client.get_job.side_effect = manager.NotFound("gone")

            manager.retry_pending_cleanup(store, client, 1031)

            self.assertEqual(store.cleanup_items(), [])
            self.assertTrue(history.contains("Tuinstra-DEV/gate:20", 1031))

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
