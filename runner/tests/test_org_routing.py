"""Contract tests for opt-in, fail-closed organization JIT dispatch."""

import tempfile
from pathlib import Path
import unittest
from unittest import mock

import ci_runner_manager as manager


ORG = "Tuinstra-DEV"
REPO = f"{ORG}/tracker"
REF = (f"{ORG}/devops/.github/workflows/reusable-trusted-verification.yml@"
       + "a" * 40)
POLICY = {
    "org_routing_enabled": True, "pool_mode": "four",
    "repositories": [REPO],
    "github_token_file": "/run/credentials/ci-runner-manager.service/github_token",
    "org_github_token_file": "/run/credentials/ci-runner-manager.service/org_github_token",
    "org_runner_group_id": 42, "org_workflow_ref": REF,
    "org_repository_ids": {REPO: 123},
}


class OrgRoutingTests(unittest.TestCase):
    @staticmethod
    def empty_association_evidence():
        sha = "b" * 40
        repository = {"full_name": REPO, "private": True, "fork": False}
        run = {"id": 11, "event": "pull_request", "head_sha": sha,
               "head_branch": "feat/verified-head", "repository": repository,
               "head_repository": repository,
               "actor": {"login": "marcel-tuinstra", "type": "User"},
               "triggering_actor": {"login": "marcel-tuinstra", "type": "User"},
               "pull_requests": []}
        pr = {"number": 7, "state": "open", "merged": False,
              "user": {"login": "marcel-tuinstra", "type": "User"},
              "head": {"ref": "feat/verified-head", "sha": sha,
                       "repo": repository},
              "base": {"repo": repository}}
        job = {"id": 22, "status": "queued", "run_id": 11,
               "labels": ["self-hosted", "trusted-medium", "sanctuary-tracker"]}
        return run, pr, job

    def test_empty_run_association_resolves_one_current_private_pr_for_admission(self):
        run, pr, job = self.empty_association_evidence()
        client = manager.GitHubClient("repo-token")
        paths = []

        def response(_method, path):
            paths.append(path)
            if "status=queued" in path:
                return {"workflow_runs": [run]}
            if "status=in_progress" in path:
                return {"workflow_runs": []}
            if "/jobs?" in path:
                return {"jobs": [job]}
            if "/pulls?" in path:
                return [{"number": 7}]
            if path.endswith("/pulls/7"):
                return pr
            raise AssertionError(path)

        with mock.patch.object(client, "request", side_effect=response):
            self.assertEqual(client.candidate_trusted_jobs(REPO, "medium"),
                             [{"repo": REPO, "run_id": 11, "job_id": 22}])
        self.assertIn(f"/repos/{REPO}/pulls?state=open&head=Tuinstra-DEV%3Afeat%2Fverified-head&per_page=100", paths)
        self.assertIn(f"/repos/{REPO}/pulls/7", paths)
        self.assertFalse(any("/git/ref/" in path for path in paths))

    def test_empty_run_association_revalidates_current_pr_at_assignment(self):
        run, pr, job = self.empty_association_evidence()
        assigned = {**job, "runner_id": 30, "runner_name": "sanctuary-22",
                    "runner_group_id": 42,
                    "runner_group_name": "sanctuary-trusted-verification"}
        state = {"repo": REPO, "scope": "organization", "org": ORG,
                 "profile": "medium", "runner_group_id": 42, "runner_id": 30,
                 "runner_name": "sanctuary-22"}
        assignment = {"repo": REPO, "run_id": 11, "job_id": 22}
        client = manager.GitHubClient("repo-token")
        with mock.patch.object(client, "request", side_effect=[assigned, run,
                               [{"number": 7}], pr]) as request:
            client.verify_org_assignment(state, assignment)
        self.assertTrue(any("/pulls?" in call.args[1]
                            for call in request.call_args_list))

    def test_one_dispatch_shares_only_workflow_list_reads_across_profiles(self):
        run, pr, medium_job = self.empty_association_evidence()
        heavy_job = {**medium_job, "id": 23,
                     "labels": ["self-hosted", "trusted-heavy", "sanctuary-tracker"]}
        client = manager.GitHubClient("org-token")
        paths = []

        def response(_method, path):
            paths.append(path)
            if "status=queued" in path:
                return {"workflow_runs": [run]}
            if "status=in_progress" in path:
                return {"workflow_runs": []}
            if "/jobs?" in path:
                return {"jobs": [medium_job, heavy_job]}
            if "/pulls?" in path:
                return [{"number": 7}]
            if path.endswith("/pulls/7"):
                return pr
            raise AssertionError(path)

        def inspect_adapter(_cfg, adapter, *, profile, scope):
            self.assertEqual(scope, "organization")
            expected = 23 if profile == "heavy" else 22
            self.assertEqual(adapter.candidate_jobs(REPO, f"trusted-{profile}"),
                             [{"repo": REPO, "run_id": 11, "job_id": expected}])
            return False

        with mock.patch.object(client, "request", side_effect=response), \
                mock.patch.object(client, "verify_org_group") as group, \
                mock.patch.object(manager, "dispatch_once", side_effect=inspect_adapter):
            for cycle in (1, 2):
                self.assertFalse(manager.dispatch_organization_once(POLICY,
                                                                     mock.Mock(), client))
                self.assertEqual(sum("status=queued" in path for path in paths), cycle)
                self.assertEqual(sum("status=in_progress" in path for path in paths), cycle)
                self.assertEqual(sum("/jobs?" in path for path in paths), cycle)
                # PR list and detail are security reads, so both profiles revalidate.
                self.assertEqual(sum("/pulls?" in path for path in paths), 2 * cycle)
                self.assertEqual(sum(path.endswith("/pulls/7") for path in paths),
                                 2 * cycle)
                self.assertEqual(group.call_count, cycle)

    def test_idle_dispatch_reads_two_run_lists_per_repository(self):
        repositories = {f"{ORG}/repo-{number}": number
                        for number in range(1, 13)}
        cfg = {**POLICY, "org_repository_ids": repositories}
        client = manager.GitHubClient("org-token")
        paths = []

        def response(_method, path):
            paths.append(path)
            if "/actions/runs?status=" in path:
                return {"workflow_runs": []}
            raise AssertionError(path)

        def inspect_adapter(_cfg, adapter, *, profile, scope):
            self.assertEqual(scope, "organization")
            for repo in repositories:
                self.assertEqual(adapter.candidate_jobs(repo, f"trusted-{profile}"), [])
            return False

        with mock.patch.object(client, "request", side_effect=response), \
                mock.patch.object(client, "verify_org_group") as group, \
                mock.patch.object(manager, "dispatch_once", side_effect=inspect_adapter):
            self.assertFalse(manager.dispatch_organization_once(cfg, mock.Mock(), client))

        self.assertEqual(len(paths), 24)
        self.assertEqual(len(set(paths)), 24)
        self.assertEqual(group.call_count, 1)

    def test_cached_lists_do_not_cache_current_pr_at_assignment(self):
        run, pr, job = self.empty_association_evidence()
        client = manager.GitHubClient("org-token")
        snapshot = manager.WorkflowListSnapshot()
        assigned = {**job, "runner_id": 30, "runner_name": "sanctuary-22",
                    "runner_group_id": 42,
                    "runner_group_name": "sanctuary-trusted-verification"}
        state = {"repo": REPO, "scope": "organization", "org": ORG,
                 "profile": "medium", "runner_group_id": 42, "runner_id": 30,
                 "runner_name": "sanctuary-22"}
        assignment = {"repo": REPO, "run_id": 11, "job_id": 22}
        detail_reads = 0

        def response(_method, path):
            nonlocal detail_reads
            if "/actions/runs?status=queued" in path:
                return {"workflow_runs": [run]}
            if "/actions/runs?status=in_progress" in path:
                return {"workflow_runs": []}
            if "/actions/runs/11/jobs?" in path:
                return {"jobs": [job]}
            if path.endswith("/actions/jobs/22"):
                return assigned
            if path.endswith("/actions/runs/11"):
                return run
            if "/pulls?" in path:
                return [{"number": 7}]
            if path.endswith("/pulls/7"):
                detail_reads += 1
                if detail_reads == 1:
                    return pr
                return {**pr, "head": {**pr["head"], "sha": "c" * 40}}
            raise AssertionError(path)

        with mock.patch.object(client, "request", side_effect=response):
            self.assertEqual(client.candidate_trusted_jobs(REPO, "medium",
                                                            snapshot=snapshot),
                             [assignment])
            with self.assertRaises(manager.RunnerError):
                client.verify_org_assignment(state, assignment)
        self.assertEqual(detail_reads, 2)

    def test_empty_run_association_rejects_bad_or_ambiguous_current_pr(self):
        run, pr, job = self.empty_association_evidence()
        client = manager.GitHubClient("repo-token")
        for changed_run, returned_prs in (
                ({}, []),
                ({}, [pr, {**pr, "number": 8}]),
                ({}, [{**pr, "head": {**pr["head"], "ref": "other"}}]),
                ({}, [{**pr, "head": {**pr["head"], "sha": "c" * 40}}]),
                ({}, [{**pr, "user": {"login": "someone", "type": "User"}}]),
                ({}, [{**pr, "head": {**pr["head"], "repo":
                                  {"full_name": "other/fork", "private": True,
                                   "fork": True}}}]),
                ({}, [{**pr, "head": {**pr["head"], "repo":
                                  {"full_name": REPO, "private": False,
                                   "fork": False}}}]),
                ({}, [{**pr, "merged": True}]),
                ({"head_branch": "other"}, [pr]),
                ({"repository": {"full_name": REPO, "private": False}}, [pr]),
                ({"head_repository": {"full_name": REPO, "private": False,
                                        "fork": False}}, [pr]),
        ):
            with self.subTest(run=changed_run, prs=returned_prs):
                def response(_method, path):
                    if "status=queued" in path:
                        return {"workflow_runs": [{**run, **changed_run}]}
                    if "status=in_progress" in path:
                        return {"workflow_runs": []}
                    if "/jobs?" in path:
                        return {"jobs": [job]}
                    if "/pulls?" in path:
                        return returned_prs
                    if "/pulls/" in path:
                        return returned_prs[0]
                    raise AssertionError(path)
                with mock.patch.object(client, "request", side_effect=response):
                    self.assertEqual(client.candidate_trusted_jobs(REPO, "medium"), [])

        def unavailable(_method, path):
            if "status=queued" in path:
                return {"workflow_runs": [run]}
            if "status=in_progress" in path:
                return {"workflow_runs": []}
            if "/jobs?" in path:
                return {"jobs": [job]}
            raise manager.RunnerError("PR lookup unavailable")
        with mock.patch.object(client, "request", side_effect=unavailable), \
                self.assertRaisesRegex(manager.RunnerError, "PR lookup unavailable"):
            client.candidate_trusted_jobs(REPO, "medium")

    def test_empty_run_association_assignment_rejects_changed_or_ambiguous_pr(self):
        run, pr, job = self.empty_association_evidence()
        assigned = {**job, "runner_id": 30, "runner_name": "sanctuary-22",
                    "runner_group_id": 42,
                    "runner_group_name": "sanctuary-trusted-verification"}
        state = {"repo": REPO, "scope": "organization", "org": ORG,
                 "profile": "medium", "runner_group_id": 42, "runner_id": 30,
                 "runner_name": "sanctuary-22"}
        assignment = {"repo": REPO, "run_id": 11, "job_id": 22}
        client = manager.GitHubClient("repo-token")
        for returned in ([], [pr, {**pr, "number": 8}],
                         [{**pr, "head": {**pr["head"], "sha": "c" * 40}}]):
            detail = [returned[0]] if len(returned) == 1 else []
            with self.subTest(prs=returned), \
                    mock.patch.object(client, "request", side_effect=[assigned, run,
                                                                       returned, *detail]), \
                    self.assertRaisesRegex(manager.RunnerError, "not a trusted PR"):
                client.verify_org_assignment(state, assignment)
        with mock.patch.object(client, "request", side_effect=[assigned, run,
                               manager.RunnerError("PR lookup unavailable")]), \
                self.assertRaisesRegex(manager.RunnerError, "PR lookup unavailable"):
            client.verify_org_assignment(state, assignment)

    def test_nonempty_run_association_never_falls_back_to_branch_lookup(self):
        run, pr, job = self.empty_association_evidence()
        run["pull_requests"] = [{"number": 8}]
        client = manager.GitHubClient("repo-token")
        paths = []

        def response(_method, path):
            paths.append(path)
            if "status=queued" in path:
                return {"workflow_runs": [run]}
            if "status=in_progress" in path:
                return {"workflow_runs": []}
            if "/jobs?" in path:
                return {"jobs": [job]}
            if path.endswith("/pulls/8"):
                return {**pr, "number": 8, "head": {**pr["head"],
                                                    "sha": "c" * 40}}
            raise AssertionError(path)

        with mock.patch.object(client, "request", side_effect=response):
            self.assertEqual(client.candidate_trusted_jobs(REPO, "medium"), [])
        self.assertNotIn(f"/repos/{REPO}/pulls?state=open&head=Tuinstra-DEV%3Afeat%2Fverified-head&per_page=100", paths)

        for association in (
                {"number": 7, "head": {"sha": "c" * 40}},
                {"number": 7, "head": {"sha": "b" * 40,
                                       "ref": "other"}},
                {"number": 7, "head": {"sha": "b" * 40,
                                       "repo": {"full_name": "other/fork"}}},
                {"number": 7, "base": {"repo": {"full_name": "other/fork"}}},
        ):
            with self.subTest(association=association):
                run["pull_requests"] = [association]
                with mock.patch.object(client, "request", side_effect=response):
                    self.assertEqual(client.candidate_trusted_jobs(REPO, "medium"), [])

    def test_policy_is_disabled_by_default_and_requires_distinct_credential(self):
        self.assertFalse(manager.org_routing_enabled({}))
        for change in ({"pool_mode": "legacy"},
                       {"org_github_token_file": "/tmp/token"},
                       {"org_workflow_ref": REF[:-40] + "refs/heads/main"},
                       {"org_repository_ids": {REPO: True}}):
            with self.subTest(change=change), self.assertRaises(manager.RunnerError):
                manager.validate_org_config({**POLICY, **change,
                                             "repositories": [REPO],
                                             "github_token_file": "/run/credentials/ci-runner-manager.service/github_token"})
        for excluded in ("Tuinstra-DEV/devops", "Tuinstra-DEV/agent-lab"):
            with self.subTest(excluded=excluded), self.assertRaises(manager.RunnerError):
                manager.validate_org_config({**POLICY,
                                             "org_repository_ids": {excluded: 123}})

    def test_org_repositories_are_independent_of_legacy_heavy_allowlist(self):
        org_repo = "Tuinstra-DEV/status"
        cfg = {**POLICY, "repositories": [REPO],
               "org_repository_ids": {org_repo: 456}}
        manager.validate_org_config(cfg)
        with tempfile.TemporaryDirectory() as temporary:
            cfg = {**cfg, "state_dir": str(Path(temporary) / "state"),
                   "max_concurrency": 4, "medium_runner_label": "trusted-medium"}
            client = mock.Mock()
            client.candidate_jobs.return_value = []
            self.assertFalse(manager.dispatch_once(cfg, client, profile="medium",
                                                   scope="organization"))
            client.candidate_jobs.assert_called_once_with(org_repo, "trusted-medium")

    def test_group_metadata_and_selected_repo_ids_must_match_exactly(self):
        group = {"id": 42, "name": "sanctuary-trusted-verification",
                 "visibility": "selected", "default": False, "inherited": False,
                 "allows_public_repositories": False,
                 "restricted_to_workflows": True, "selected_workflows": [REF]}
        repos = {"total_count": 1, "repositories": [
            {"id": 123, "full_name": REPO, "private": True}]}
        client = manager.GitHubClient("org-token")
        with mock.patch.object(client, "request", side_effect=[group, repos]) as request:
            client.verify_org_group(POLICY)
        self.assertEqual(request.call_count, 2)
        for group_change in ({"visibility": "all"},
                             {"allows_public_repositories": True},
                             {"restricted_to_workflows": False},
                             {"selected_workflows": [REF, REF + "extra"]},
                             {"name": "Default"}):
            with self.subTest(group_change=group_change), \
                    mock.patch.object(client, "request", return_value={**group, **group_change}), \
                    self.assertRaises(manager.RunnerError):
                client.verify_org_group(POLICY)
        for repo_change in ({"total_count": 2},
                            {"repositories": [{"id": 123, "full_name": REPO,
                                                "private": False}]},
                            {"repositories": [{"id": 124, "full_name": REPO,
                                                "private": True}]}):
            with self.subTest(repo_change=repo_change), \
                    mock.patch.object(client, "request", side_effect=[group, {**repos, **repo_change}]), \
                    self.assertRaises(manager.RunnerError):
                client.verify_org_group(POLICY)

    def test_trusted_job_requires_human_same_repo_pr_and_fixed_labels(self):
        run, pr, job = self.empty_association_evidence()
        run["pull_requests"] = [{"number": 7}]
        client = manager.GitHubClient("repo-token")
        def candidates(candidate_run=run, candidate_job=job, candidate_pr=pr):
            def response(_method, path):
                if "status=queued" in path:
                    return {"workflow_runs": [candidate_run]}
                if "status=in_progress" in path:
                    return {"workflow_runs": []}
                if "/jobs?" in path:
                    return {"jobs": [candidate_job]}
                if "/pulls/" in path:
                    return candidate_pr
                raise AssertionError(path)
            with mock.patch.object(client, "request", side_effect=response):
                return client.candidate_trusted_jobs(REPO, "medium")
        self.assertEqual(candidates(), [{"repo": REPO, "run_id": 11, "job_id": 22}])
        for changed_run, changed_pr in (
                ({"event": "pull_request_target"}, {}),
                ({"actor": {"login": "dependabot[bot]", "type": "Bot"}}, {}),
                ({"triggering_actor": {"login": "someone", "type": "User"}}, {}),
                ({"head_repository": {"full_name": "other/fork"}}, {}),
                ({}, {"head": {"sha": "b" * 40,
                               "repo": {"full_name": "other/fork"}}}),
                ({}, {"head": {"sha": "c" * 40,
                               "repo": {"full_name": REPO}}}),
                ({}, {"user": {"login": "someone", "type": "User"}}),
        ):
            with self.subTest(changed_run=changed_run, changed_pr=changed_pr):
                self.assertEqual(candidates({**run, **changed_run}, job,
                                            {**pr, **changed_pr}), [])
        self.assertEqual(candidates(run, {**job, "labels": ["trusted-medium"]}), [])
        self.assertEqual(candidates(run, {**job, "labels": job["labels"] +
                                    ["trusted-heavy"]}), [])
        self.assertEqual(candidates(run, {**job, "labels": job["labels"] +
                                    ["sanctuary-other"]}), [])

    def test_org_registration_and_cleanup_use_org_endpoints_only(self):
        client = manager.GitHubClient("org-token")
        with mock.patch.object(client, "request", side_effect=[
                {"runner": {"id": 30}}, {"id": 30, "status": "online", "busy": False},
                None]) as request:
            client.generate_org_jit(22, 42, REPO, "medium")
            client.get_org_runner(30)
            client.delete_org_runner(30)
        self.assertEqual([call.args[1] for call in request.call_args_list], [
            "/orgs/Tuinstra-DEV/actions/runners/generate-jitconfig",
            "/orgs/Tuinstra-DEV/actions/runners/30",
            "/orgs/Tuinstra-DEV/actions/runners/30"])
        labels = request.call_args_list[0].args[2]["labels"]
        self.assertEqual(labels, ["self-hosted", "linux", "x64", "trusted-medium",
                                  "sanctuary-tracker"])
        state = {"repo": REPO, "runner_id": 30, "scope": "organization",
                 "org": ORG}
        with mock.patch.object(client, "delete_org_runner") as org_delete, \
                mock.patch.object(client, "delete_runner") as repo_delete:
            manager.cleanup_github_runner(None, state, client)
        org_delete.assert_called_once_with(30)
        repo_delete.assert_not_called()

    def test_two_heavy_leases_do_not_block_medium_org_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            cfg = {**POLICY, "state_dir": str(Path(temporary) / "state"),
                   "max_concurrency": 4, "runner_label": "trusted-heavy",
                   "medium_runner_label": "trusted-medium", "repositories": [REPO]}
            store = manager.StateStore(Path(cfg["state_dir"]))
            for index in (1, 2):
                store.write(f"heavy-{index}", {"lease": f"heavy-{index}",
                                               "phase": "running", "profile": "heavy"})
            client = mock.Mock()
            client.candidate_jobs.return_value = []
            self.assertFalse(manager.dispatch_once(cfg, client, profile="medium",
                                                   scope="organization"))
            client.candidate_jobs.assert_called_once_with(REPO, "trusted-medium")

    def test_org_actual_assignment_requires_same_group_profile_repo_and_pr(self):
        client = manager.GitHubClient("repo-token")
        state = {"repo": REPO, "scope": "organization", "org": ORG,
                 "profile": "medium", "runner_group_id": 42, "runner_id": 30,
                 "runner_name": "sanctuary-22"}
        assignment = {"repo": REPO, "run_id": 11, "job_id": 22}
        job = {"id": 22, "run_id": 11, "runner_id": 30,
               "runner_name": "sanctuary-22", "runner_group_id": 42,
               "runner_group_name": "sanctuary-trusted-verification",
               "labels": ["self-hosted", "trusted-medium", "sanctuary-tracker"]}
        run, pr, _ = self.empty_association_evidence()
        run["pull_requests"] = [{"number": 7}]
        with mock.patch.object(client, "request", side_effect=[job, run, pr]):
            client.verify_org_assignment(state, assignment)
        for altered in ({"labels": ["self-hosted", "trusted-medium"]},
                        {"labels": ["self-hosted", "trusted-heavy", "sanctuary-tracker"]},
                        {"labels": job["labels"] + ["sanctuary-other"]},
                        {"runner_group_id": 1},
                        {"runner_id": 31},
                        {"runner_group_name": "other"}):
            with self.subTest(altered=altered), \
                    mock.patch.object(client, "request", return_value={**job, **altered}), \
                    self.assertRaisesRegex(manager.RunnerError, "assignment policy mismatch"):
                client.verify_org_assignment(state, assignment)
        with mock.patch.object(client, "request", side_effect=[job, run,
                {**pr, "head": {"sha": "c" * 40,
                                 "repo": {"full_name": REPO}}}]), \
                self.assertRaisesRegex(manager.RunnerError, "not a trusted PR"):
            client.verify_org_assignment(state, assignment)

    def test_org_assignment_is_not_recorded_when_validation_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = manager.StateStore(Path(temporary))
            state = {"lease": "gh-22", "repo": REPO, "scope": "organization", "org": ORG,
                     "runner_id": 30, "runner_name": "sanctuary-22",
                     "trigger_run_id": 11, "trigger_job_id": 22,
                     "profile": "medium", "runner_group_id": 42}
            store.write("gh-22", state)
            legacy_client = mock.Mock()
            org_client = mock.Mock()
            org_client.find_assigned_job.return_value = {"repo": REPO, "run_id": 11,
                                                         "job_id": 22}
            org_client.verify_org_assignment.side_effect = manager.RunnerError("policy mismatch")
            with self.assertRaisesRegex(manager.RunnerError, "policy mismatch"):
                manager.record_verified_assignment(store, legacy_client, state,
                                                   include_completed=False,
                                                   org_client=org_client)
            self.assertNotIn("actual_job_id", store.leases()[0])
            legacy_client.find_assigned_job.assert_not_called()

    def test_org_candidate_reads_use_only_separate_org_credential(self):
        legacy_client = mock.Mock()
        org_client = mock.Mock()
        adapter = manager.OrganizationDispatchClient(POLICY, legacy_client,
                                                     org_client, "medium")
        org_client.candidate_trusted_jobs.return_value = []
        self.assertEqual(adapter.candidate_jobs(REPO, "trusted-medium"), [])
        org_client.candidate_trusted_jobs.assert_called_once_with(REPO, "medium")
        legacy_client.candidate_trusted_jobs.assert_not_called()

    def test_org_trigger_and_runner_lookup_use_org_credential(self):
        legacy_client = mock.Mock()
        org_client = mock.Mock()
        state = {"lease": "gh-22", "repo": REPO, "scope": "organization", "org": ORG,
                 "runner_id": 30, "trigger_job_id": 22, "launched_at": 100}
        org_client.get_job.return_value = {"id": 22, "status": "queued"}
        org_client.get_org_runner.return_value = {"id": 30, "status": "offline",
                                                 "busy": False}
        self.assertTrue(manager.unassigned_lease_is_stale(
            legacy_client, state, 100 + manager.REGISTRATION_GRACE_SECONDS + 1,
            org_client))
        org_client.get_job.assert_called_once_with(REPO, 22)
        org_client.get_org_runner.assert_called_once_with(30)
        legacy_client.get_job.assert_not_called()
        legacy_client.get_runner.assert_not_called()

    def test_pre_jit_group_failure_releases_only_its_dispatch_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            cfg = {**POLICY, "state_dir": str(Path(temporary) / "state"),
                   "max_concurrency": 4, "medium_runner_label": "trusted-medium"}
            org_client = mock.Mock()
            org_client.candidate_trusted_jobs.return_value = [
                {"repo": REPO, "run_id": 11, "job_id": 22}]
            org_client.verify_org_group.side_effect = manager.RunnerError("group unavailable")
            adapter = manager.OrganizationDispatchClient(cfg, mock.Mock(),
                                                         org_client, "medium")
            def healthy_helper(_cfg, operation, **_kwargs):
                return mock.Mock(stdout=b"{}" if operation == "list" else b"[]")
            with mock.patch.object(manager, "helper", side_effect=healthy_helper), \
                    mock.patch.object(manager, "capacity_errors", return_value=[]), \
                    self.assertRaisesRegex(manager.OrgPreflightRejected, "group preflight failed"):
                manager.dispatch_once(cfg, adapter, profile="medium",
                                      scope="organization")
            self.assertFalse(manager.DispatchHistory(Path(cfg["state_dir"])).contains(
                f"{REPO}:22", 10000))
            org_client.generate_org_jit.assert_not_called()

    def test_ambiguous_org_jit_post_keeps_dispatch_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            cfg = {**POLICY, "state_dir": str(Path(temporary) / "state"),
                   "max_concurrency": 4, "medium_runner_label": "trusted-medium"}
            org_client = mock.Mock()
            org_client.candidate_trusted_jobs.return_value = [
                {"repo": REPO, "run_id": 11, "job_id": 22}]
            org_client.generate_org_jit.side_effect = manager.RunnerError("POST ambiguous")
            adapter = manager.OrganizationDispatchClient(cfg, mock.Mock(),
                                                         org_client, "medium")
            def healthy_helper(_cfg, operation, **_kwargs):
                return mock.Mock(stdout=b"{}" if operation == "list" else b"[]")
            with mock.patch.object(manager, "helper", side_effect=healthy_helper), \
                    mock.patch.object(manager, "capacity_errors", return_value=[]), \
                    self.assertRaisesRegex(manager.RunnerError, "POST ambiguous"):
                manager.dispatch_once(cfg, adapter, profile="medium",
                                      scope="organization")
            self.assertTrue(manager.DispatchHistory(Path(cfg["state_dir"])).contains(
                f"{REPO}:22", 10000))
            org_client.verify_org_group.assert_called_once_with(cfg)

    def test_legacy_candidate_does_not_claim_org_labeled_job(self):
        client = manager.GitHubClient("repo-token")
        with mock.patch.object(client, "request", side_effect=[
                {"workflow_runs": [{"id": 11}]},
                {"jobs": [{"id": 22, "status": "queued",
                           "labels": ["self-hosted", "trusted-heavy",
                                      "sanctuary-tracker"]}]},
                {"workflow_runs": []}]):
            self.assertEqual(client.candidate_jobs(REPO, "trusted-heavy"), [])

    def test_org_cleanup_obligation_has_separate_identity(self):
        legacy = {"repo": REPO, "runner_id": 30}
        org = {**legacy, "scope": "organization", "org": ORG}
        self.assertNotEqual(manager.StateStore.cleanup_key(legacy),
                            manager.StateStore.cleanup_key(org))
        with self.assertRaises(manager.RunnerError):
            manager.StateStore.cleanup_key({**org, "org": "other"})

    @mock.patch.object(manager, "helper")
    @mock.patch.object(manager.time, "time", return_value=10000)
    def test_expired_org_vm_is_destroyed_before_healthy_assignment_api_error(
            self, _time, helper):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfg = {"state_dir": root / "state", "lock_file": root / "lock",
                   "max_lease_seconds": 7200}
            store = manager.StateStore(cfg["state_dir"])
            store.write("expired", {"lease": "expired", "phase": "running",
                                    "launched_at": 1, "repo": REPO,
                                    "runner_id": 30, "scope": "organization", "org": ORG,
                                    "runner_name": "sanctuary-22", "trigger_job_id": 22,
                                    "trigger_run_id": 11, "profile": "medium"})
            store.write("healthy", {"lease": "healthy", "phase": "running",
                                    "launched_at": 9000, "repo": REPO,
                                    "runner_id": 31, "scope": "organization", "org": ORG,
                                    "runner_name": "sanctuary-23", "trigger_job_id": 23,
                                    "trigger_run_id": 12, "profile": "medium"})
            helper.return_value = mock.Mock(stdout=b'{"expired":"running","healthy":"running"}')
            legacy_client = mock.Mock()
            org_client = mock.Mock()
            org_client.delete_org_runner.side_effect = manager.RunnerError("org API down")
            org_client.find_assigned_job.side_effect = manager.RunnerError("org API down")
            with self.assertRaisesRegex(manager.RunnerError, "org API down"):
                manager.reconcile(cfg, legacy_client, org_client)
            self.assertIn(("destroy", "expired"),
                          [call.args[1:] for call in helper.call_args_list])
            self.assertEqual([state["lease"] for state in store.leases()], ["healthy"])
            self.assertEqual([item["lease"] for item in store.cleanup_items()],
                             ["expired"])
            org_client.find_assigned_job.assert_called_once()
            self.assertEqual(org_client.find_assigned_job.call_args.args[1], 31)
            legacy_client.find_assigned_job.assert_not_called()
