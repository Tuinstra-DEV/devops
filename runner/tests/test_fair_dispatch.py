"""Closed scheduler regression: queue age, not repository/scope order."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ci_runner_manager as manager
from runner.tests.test_org_routing import POLICY, REF, REPO
from runner.tests import test_org_routing

GATE = "Tuinstra-DEV/gate"
WODIQ = "Tuinstra-DEV/wodiq-platform"


def job(repo, number, stamp, *, org=False):
    return {"repo": repo, "run_id": 10, "job_id": number,
            "queued_at": stamp, **({"workflow_ref": REF} if org else {})}


class FairDispatchTests(unittest.TestCase):
    def test_legacy_discovery_preserves_github_queue_timestamp(self):
        client = manager.GitHubClient("synthetic")
        stamp = "2026-10-18T13:35:39Z"
        with mock.patch.object(client, "request", side_effect=[
                {"workflow_runs": [{"id": 10}]},
                {"jobs": [{"id": 20, "status": "queued", "created_at": stamp,
                           "labels": ["self-hosted", "trusted-heavy"]}]},
                {"workflow_runs": []}]):
            self.assertEqual(client.candidate_jobs(WODIQ, "trusted-heavy"),
                             [job(WODIQ, 20, stamp)])

    def test_org_discovery_preserves_timestamp_after_real_trust_checks(self):
        run, pr, candidate = test_org_routing.OrgRoutingTests.empty_association_evidence()
        stamp = "2026-10-18T13:35:39Z"
        candidate["created_at"] = stamp
        client = manager.GitHubClient("synthetic", org_workflow_refs=(REF,))
        with mock.patch.object(client, "request", side_effect=[
                {"workflow_runs": [run]}, {"jobs": [candidate]}, run,
                [{"number": 7}], pr, {"workflow_runs": []}]):
            self.assertEqual(client.candidate_trusted_jobs(REPO, "medium"),
                             [{"repo": REPO, "run_id": 11, "job_id": 22,
                               "workflow_ref": REF, "queued_at": stamp}])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {**POLICY, "state_dir": Path(self.tmp.name) / "state",
                    "repositories": [GATE, WODIQ],
                    "org_repository_ids": {GATE: 1, WODIQ: 2}}
        self.repo = mock.Mock()
        self.org = mock.Mock()
        self.repo.candidate_jobs.return_value = []
        self.org.candidate_trusted_jobs.return_value = []
        self.selected = []

    def dispatch(self, cfg, client, *, scope="repository", profile="heavy"):
        # Exercise the same candidate narrowing API as the real dispatcher.
        repos = cfg["org_repository_ids"] if scope == "organization" else cfg["repositories"]
        for repo in repos:
            for candidate in client.candidate_jobs(repo, f"trusted-{profile}"):
                self.selected.append((candidate["job_id"], scope, profile))
        return True

    def run_cycle(self):
        with mock.patch.object(manager, "dispatch_once", side_effect=self.dispatch):
            return manager.dispatch_fair_once(self.cfg, self.repo, self.org)

    def test_older_org_job_precedes_newer_legacy_release(self):
        self.repo.candidate_jobs.side_effect = lambda repo, label: (
            [job(GATE, 20, "2026-10-18T14:25:00Z")] if repo == GATE else [])
        self.org.candidate_trusted_jobs.side_effect = lambda repo, profile, **kw: (
            [job(WODIQ, 10, "2026-10-18T13:35:00Z", org=True)]
            if repo == WODIQ and profile == "heavy" else [])
        self.run_cycle()
        self.assertEqual(self.selected, [(10, "organization", "heavy"),
                                         (20, "repository", "heavy")])

    def test_older_legacy_job_is_not_starved_by_org_work(self):
        self.repo.candidate_jobs.side_effect = lambda repo, label: (
            [job(WODIQ, 10, "2026-10-18T13:00:00Z")] if repo == WODIQ else [])
        self.org.candidate_trusted_jobs.side_effect = lambda repo, profile, **kw: (
            [job(GATE, 20, "2026-10-18T14:00:00Z", org=True)]
            if repo == GATE and profile == "heavy" else [])
        self.run_cycle()
        self.assertEqual([x[0] for x in self.selected], [10, 20])

    def test_queue_age_beats_repository_order_and_job_id(self):
        self.repo.candidate_jobs.side_effect = lambda repo, label: [
            job(repo, 10 if repo == GATE else 20,
                "2026-10-18T14:00:00Z" if repo == GATE else "2026-10-18T13:00:00Z")]
        self.run_cycle()
        self.assertEqual([x[0] for x in self.selected], [20, 10])

    def test_disabled_org_keeps_legacy_route_and_does_not_use_org_credential(self):
        self.cfg["org_routing_enabled"] = False
        self.repo.candidate_jobs.side_effect = lambda repo, label: (
            [job(WODIQ, 10, "2026-10-18T13:00:00Z")] if repo == WODIQ else [])
        with mock.patch.object(manager, "dispatch_once", side_effect=self.dispatch):
            manager.dispatch_fair_once(self.cfg, self.repo, None)
        self.assertEqual(self.selected, [(10, "repository", "heavy")])
        self.org.verify_org_group.assert_not_called()

    def test_missing_org_client_fails_before_dispatch(self):
        with mock.patch.object(manager, "dispatch_once") as dispatch:
            with self.assertRaises(manager.RunnerError):
                manager.dispatch_fair_once(self.cfg, self.repo, None)
            dispatch.assert_not_called()

    def test_group_rejection_fails_before_any_dispatch(self):
        self.org.verify_org_group.side_effect = manager.RunnerError("policy mismatch")
        with mock.patch.object(manager, "dispatch_once") as dispatch:
            with self.assertRaises(manager.RunnerError):
                manager.dispatch_fair_once(self.cfg, self.repo, self.org)
            dispatch.assert_not_called()

    def test_older_full_heavy_pool_does_not_block_medium(self):
        self.org.candidate_trusted_jobs.side_effect = lambda repo, profile, **kw: (
            [job(WODIQ, 10 if profile == "heavy" else 11,
                 "2026-10-18T13:00:00Z", org=True)] if repo == WODIQ else [])
        def dispatch(cfg, client, **kw):
            if kw["profile"] == "heavy":
                return False
            return self.dispatch(cfg, client, **kw)
        with mock.patch.object(manager, "dispatch_once", side_effect=dispatch):
            self.assertTrue(manager.dispatch_fair_once(self.cfg, self.repo, self.org))
        self.assertEqual(self.selected, [(11, "organization", "medium")])

    def test_unknown_queue_time_goes_after_dated_jobs(self):
        self.repo.candidate_jobs.side_effect = lambda repo, label: [
            job(repo, 10 if repo == GATE else 20,
                None if repo == GATE else "2026-10-18T13:00:00Z")]
        self.run_cycle()
        self.assertEqual([x[0] for x in self.selected], [20, 10])

    def test_filtered_client_keeps_jit_and_cleanup_on_original_client(self):
        candidate = job(WODIQ, 10, "2026-10-18T13:00:00Z")
        client = manager.SelectedCandidateClient(self.repo, candidate, "trusted-heavy")
        self.assertEqual(client.candidate_jobs(GATE, "trusted-heavy"), [])
        self.assertEqual(client.candidate_jobs(WODIQ, "trusted-medium"), [])
        self.assertEqual(client.candidate_jobs(WODIQ, "trusted-heavy"), [candidate])
        client.generate_jit(WODIQ, 10, 1, "trusted-heavy")
        self.repo.generate_jit.assert_called_once_with(WODIQ, 10, 1, "trusted-heavy")
        client.delete_runner(WODIQ, 22)
        self.repo.delete_runner.assert_called_once_with(WODIQ, 22)
        with self.assertRaises(manager.RunnerError):
            client.generate_jit(GATE, 10, 1, "trusted-heavy")
        self.repo.generate_jit.assert_called_once()

    def test_real_dispatch_respects_slots_claims_and_oldest_scope(self):
        # Only VM creation and external APIs are mocked, not dispatch or state.
        self.cfg.update(max_concurrency=4, lock_file=Path(self.tmp.name) / "lock")
        store = manager.StateStore(self.cfg["state_dir"])
        store.write("existing", {"lease": "existing", "profile": "heavy",
                                 "phase": "running", "launched_at": 1})
        self.repo.candidate_jobs.side_effect = lambda repo, label: (
            [job(GATE, 20, "2026-10-18T14:25:00Z")] if repo == GATE else [])
        self.org.candidate_trusted_jobs.side_effect = lambda repo, profile, **kw: (
            [job(WODIQ, 10, "2026-10-18T13:35:00Z", org=True)]
            if repo == WODIQ and profile == "heavy" else [])
        self.org.generate_org_jit.return_value = {
            "encoded_jit_config": "aml0", "runner": {"id": 42}}

        def start(cfg, lease, encoded, **kw):
            store.write(lease, {"lease": lease, "profile": kw["profile"],
                                "phase": "running", "launched_at": 1,
                                **kw["metadata"]})

        with mock.patch.object(manager, "pre_jit_capacity_errors", return_value=[]), \
                mock.patch.object(manager, "launch", side_effect=start) as launch:
            self.assertTrue(manager.dispatch_fair_once(self.cfg, self.repo, self.org))
            self.assertFalse(manager.dispatch_fair_once(self.cfg, self.repo, self.org))
        launch.assert_called_once()
        self.org.generate_org_jit.assert_called_once_with(10, 42, WODIQ, "heavy")
        self.repo.generate_jit.assert_not_called()
        self.assertEqual(len(store.leases()), 2)

    def test_capacity_denial_registers_nothing(self):
        self.repo.candidate_jobs.side_effect = lambda repo, label: (
            [job(WODIQ, 10, "2026-10-18T13:00:00Z")] if repo == WODIQ else [])
        with mock.patch.object(manager, "pre_jit_capacity_errors", return_value=["NVMe floor"]):
            self.assertFalse(manager.dispatch_fair_once(self.cfg, self.repo, self.org))
        self.repo.generate_jit.assert_not_called()
        self.org.generate_org_jit.assert_not_called()

    def test_claimed_oldest_job_does_not_starve_next_eligible_job(self):
        self.cfg["org_routing_enabled"] = False
        manager.StateStore(self.cfg["state_dir"])
        manager.DispatchHistory(self.cfg["state_dir"]).quarantine(f"{GATE}:10", 1)
        self.repo.candidate_jobs.side_effect = lambda repo, label: [
            job(repo, 10 if repo == GATE else 20, "2026-10-18T13:00:00Z")]
        self.repo.generate_jit.return_value = {"encoded_jit_config": "aml0", "runner": {"id": 42}}
        with mock.patch.object(manager, "pre_jit_capacity_errors", return_value=[]), \
                mock.patch.object(manager, "launch") as launch:
            self.assertTrue(manager.dispatch_fair_once(self.cfg, self.repo, None))
        self.assertEqual(launch.call_args.kwargs["metadata"]["repo"], WODIQ)

    def test_invalid_candidate_identity_and_timestamp(self):
        self.repo.candidate_jobs.return_value = [job("Tuinstra-DEV/unlisted", 10, None)]
        with mock.patch.object(manager, "dispatch_once") as dispatch:
            with self.assertRaises(manager.RunnerError):
                manager.dispatch_fair_once(self.cfg, self.repo, self.org)
            dispatch.assert_not_called()
        for stamp in (None, "bad", "2026-99-99T99:99:99Z", 123, "2026-01-01T00:00:00Z\n"):
            self.assertEqual(manager.queue_timestamp({"created_at": stamp}), {})
        self.assertEqual(manager.queue_timestamp({"created_at": "2026-10-18T13:35:39Z"}),
                         {"queued_at": "2026-10-18T13:35:39Z"})

    def test_duplicate_listing_is_only_dispatched_once(self):
        candidate = job(WODIQ, 10, "2026-10-18T13:00:00Z")
        self.repo.candidate_jobs.side_effect = lambda repo, label: (
            [candidate, candidate] if repo == WODIQ else [])
        self.run_cycle()
        self.assertEqual(self.selected, [(10, "repository", "heavy")])


if __name__ == "__main__":
    unittest.main()
