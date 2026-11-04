"""Regressions for stale discovery and one-job lease completion."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ci_runner_manager as manager
import test_org_routing as org_tests

POLICY, REPO, REF = org_tests.POLICY, org_tests.REPO, org_tests.REF


class SingleUseTests(unittest.TestCase):
    def test_trusted_jit_rechecks_discovery_before_registration(self):
        run, pr, job = org_tests.OrgRoutingTests.empty_association_evidence()
        candidate = {"repo": REPO, "run_id": 11, "job_id": 22, "workflow_ref": REF}
        for change in ({}, {"status": "completed", "runner_id": 30},
                       {"runner_id": 30}, {"runner_name": "another-runner"},
                       {"run_id": 12}, {"labels": ["self-hosted", "trusted-medium"]}):
            with self.subTest(change=change):
                api = manager.GitHubClient("synthetic", org_workflow_refs=(REF,))
                api.candidate_trusted_jobs = mock.Mock(return_value=[candidate])
                api.verify_org_group = mock.Mock()
                api.get_job = mock.Mock(return_value={**job, "runner_id": 0, **change})
                api._resolve_trusted_source = mock.Mock(return_value=pr)
                api.request = mock.Mock(return_value=run)
                api.generate_org_jit = mock.Mock(return_value={"synthetic": True})
                adapter = manager.OrganizationDispatchClient(POLICY, mock.Mock(), api, "medium")
                adapter.candidate_jobs(REPO, "trusted-medium")
                if change:
                    with self.assertRaises(manager.OrgPreflightRejected):
                        adapter.generate_jit(REPO, 22, 42, "trusted-medium")
                    api.generate_org_jit.assert_not_called()
                else:
                    adapter.generate_jit(REPO, 22, 42, "trusted-medium")
                    api.get_job.assert_called_once_with(REPO, 22)
                    api.generate_org_jit.assert_called_once()

    def test_running_guest_is_reaped_after_actual_job_and_trigger_gets_fast_retry(self):
        for runner_id in (None, 0):
            with self.subTest(unassigned_id=runner_id), tempfile.TemporaryDirectory() as tmp:
                cfg = {"state_dir": Path(tmp) / "state", "lock_file": Path(tmp) / "lock",
                       "max_lease_seconds": 7200}
                store = manager.StateStore(cfg["state_dir"])
                state = {"lease": "gh-22", "phase": "running", "launched_at": 1000,
                         "repo": REPO, "runner_id": 30, "runner_name": "sanctuary-22",
                         "trigger_run_id": 11, "trigger_job_id": 22}
                store.write("gh-22", state)
                history = manager.DispatchHistory(cfg["state_dir"])
                for _ in range(5):
                    history.quarantine(f"{REPO}:22", 1000)
                client = mock.Mock()
                client.find_assigned_job.return_value = {"repo": REPO, "run_id": 12, "job_id": 23}
                actual = {"id": 23, "run_id": 12, "status": "completed",
                          "runner_id": 30, "runner_name": "sanctuary-22"}
                trigger = {"id": 22, "run_id": 11, "status": "queued", "runner_id": runner_id,
                           "runner_name": ""}
                client.get_job.side_effect = lambda repo, job: actual if job == 23 else trigger
                with mock.patch.object(manager.time, "time", return_value=2000), \
                        mock.patch.object(manager, "helper", return_value=mock.Mock(
                            stdout=json.dumps({"gh-22": "running"}).encode())) as helper:
                    manager.reconcile(cfg, client)
                self.assertEqual(store.leases(), [])
                client.find_assigned_job.assert_called_once_with(REPO, 30, "sanctuary-22",
                    preferred_run_id=11, include_completed=True)
                self.assertIn(mock.call(cfg, "destroy", "gh-22"), helper.call_args_list)
                client.delete_runner.assert_called_once_with(REPO, 30)
                manager.retry_pending_cleanup(store, client, 2030)
                self.assertFalse(history.contains(f"{REPO}:22", 2060))

    def test_trusted_source_disappearing_after_discovery_never_posts_jit(self):
        candidate = {"repo": REPO, "run_id": 11, "job_id": 22, "workflow_ref": REF}
        api = mock.Mock()
        api.candidate_trusted_jobs.side_effect = [[candidate], []]
        api.get_job.return_value = org_tests.OrgRoutingTests.empty_association_evidence()[2]
        adapter = manager.OrganizationDispatchClient(POLICY, mock.Mock(), api, "medium")
        adapter.candidate_jobs(REPO, "trusted-medium")
        with self.assertRaises(manager.OrgPreflightRejected):
            adapter.generate_jit(REPO, 22, 42, "trusted-medium")
        api.generate_org_jit.assert_not_called()

    def test_zero_runner_id_is_unassigned_for_verified_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = manager.StateStore(Path(tmp))
            history = manager.DispatchHistory(Path(tmp))
            for _ in range(5):
                history.quarantine(f"{REPO}:22", 1000)
            state = {"lease": "gh-22", "repo": REPO, "runner_id": 30,
                     "trigger_job_id": 22, "trigger_run_id": 11, "actual_job_id": 23,
                     "local_cleanup_verified": True, "dispatch_attempts": 5}
            store.write_handoff_obligation("gh-22", state, 2000)
            client = mock.Mock()
            client.get_job.return_value = {"id": 22, "run_id": 11, "status": "queued",
                                           "runner_id": 0, "runner_name": ""}
            manager.retry_dispatch_handoff(store, client, state, 2000)
            self.assertTrue(history.contains(f"{REPO}:22", 2029))
            self.assertFalse(history.contains(f"{REPO}:22", 2030))

    def test_org_completion_uses_org_credential_and_keeps_failed_deletion_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"state_dir": Path(tmp) / "state", "lock_file": Path(tmp) / "lock",
                   "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            state = {"lease": "gh-22", "phase": "running", "launched_at": 1000,
                     "scope": "organization", "org": "Tuinstra-DEV", "repo": REPO,
                     "runner_id": 30, "runner_name": "sanctuary-22", "trigger_job_id": 22,
                     "actual_job_id": 23, "actual_run_id": 12}
            store.write("gh-22", state)
            repo_client, org_client = mock.Mock(), mock.Mock()
            org_client.get_job.return_value = {"id": 23, "run_id": 12, "status": "completed",
                                               "runner_id": 30, "runner_name": "sanctuary-22"}
            org_client.delete_org_runner.side_effect = manager.RunnerError("unavailable")
            with mock.patch.object(manager.time, "time", return_value=2000), \
                    mock.patch.object(manager, "helper", return_value=mock.Mock(
                        stdout=b'{"gh-22":"running"}')):
                manager.reconcile(cfg, repo_client, org_client)
            repo_client.get_job.assert_not_called()
            org_client.get_job.assert_called_once_with(REPO, 23)
            self.assertTrue(store.cleanup_items()[0]["local_cleanup_verified"])
            org_client.delete_org_runner.assert_called_once_with(30)

    def test_completion_requires_current_runner_id_and_run_and_api_success(self):
        for change in ({"status": "in_progress"}, {"runner_id": 31}, {"run_id": 13},
                       {"runner_name": "another"}, {"error": True}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                cfg = {"state_dir": Path(tmp) / "state", "lock_file": Path(tmp) / "lock",
                       "max_lease_seconds": 7200}
                store = manager.StateStore(cfg["state_dir"])
                store.write("gh-22", {"lease": "gh-22", "phase": "running", "launched_at": 1000,
                    "repo": REPO, "runner_id": 30, "runner_name": "sanctuary-22",
                    "trigger_run_id": 11, "trigger_job_id": 22,
                    "actual_job_id": 23, "actual_run_id": 12})
                client = mock.Mock()
                client.get_job.return_value = {"id": 23, "run_id": 12, "status": "completed",
                    "runner_id": 30, "runner_name": "sanctuary-22", **change}
                if change.get("error"):
                    client.get_job.side_effect = manager.RunnerError("unavailable")
                with mock.patch.object(manager.time, "time", return_value=2000), \
                        mock.patch.object(manager, "helper", return_value=mock.Mock(
                            stdout=b'{"gh-22":"running"}')) as helper:
                    try:
                        manager.reconcile(cfg, client)
                    except manager.RunnerError:
                        pass
                self.assertEqual(len(store.leases()), 1)
                self.assertNotIn(mock.call(cfg, "destroy", "gh-22"), helper.call_args_list)
