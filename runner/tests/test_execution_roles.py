"""Disabled-by-default roles do not grant trusted source execution rights."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ci_runner_manager as manager
from test_org_routing import POLICY, REF, REPO
import test_org_routing

METADATA = 'metadata-preflight'
PRODUCER = 'pr-security-producer'
META_REF = REF[:-40] + 'd' * 40
PRODUCER_REF = ('Tuinstra-DEV/devops/.github/workflows/'
                'reusable-gate-pr-security.yml@' + 'e' * 40)
ROLES = {
    METADATA: {'workflow_ref': META_REF, 'repositories': [REPO],
               'events': ['pull_request', 'push'],
               'actors': [{'login': 'marcel-tuinstra', 'type': 'User'}]},
    PRODUCER: {'workflow_ref': PRODUCER_REF, 'repositories': [REPO],
               'events': ['pull_request_target'],
               'actors': [{'login': 'marcel-tuinstra', 'type': 'User'}]},
}


class ExecutionRoleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {**POLICY, 'org_execution_roles': copy.deepcopy(ROLES)}
        self.run, self.pr, self.job = test_org_routing.OrgRoutingTests.empty_association_evidence()
        self.pr['base'].update({'sha': 'c' * 40, 'ref': 'main'})
        self.run.update({'run_attempt': 2, 'pull_requests': [{'number': 7, 'head': {'sha': self.pr['head']['sha']},
                                                          'base': {'sha': 'c' * 40}}],
                         'referenced_workflows': [{'path': META_REF, 'sha': META_REF[-40:]}]})
        self.job.update({'run_attempt': 2, 'head_sha': self.run['head_sha'], 'labels': ['self-hosted', 'ci-metadata-preflight',
                                                   'sanctuary-tracker', 'ci-run-11-2', 'ci-pr-head-' + self.pr['head']['sha']]})
        self.metadata = {'full_name': REPO, 'private': True, 'fork': False,
                         'default_branch': 'main', 'id': 123}
        self.other_branches = {}
        self.branch = {'name': 'main', 'protected': True, 'commit': {'sha': 'c' * 40}}

    def client(self, roles=None):
        return manager.GitHubClient('fixture-token', org_workflow_refs=(REF, META_REF, PRODUCER_REF),
                                    org_execution_roles=copy.deepcopy(ROLES if roles is None else roles))

    def response(self, method, path, body=None):
        self.assertEqual(method, 'GET')
        if path.endswith('status=queued&per_page=100'):
            return {'workflow_runs': [self.run]}
        if path.endswith('status=in_progress&per_page=100'):
            return {'workflow_runs': []}
        if '/jobs?' in path:
            return {'jobs': [self.job]}
        if path.endswith('/actions/jobs/22'):
            return self.job
        if path.endswith('/actions/runs/11') or path.endswith('/actions/runs/11/attempts/2'):
            return self.run
        if path.endswith('/pulls/7'):
            return self.pr
        if '/branches/' in path:
            return self.other_branches.get(path.rsplit('/', 1)[-1], self.branch)
        if path == f'/repos/{REPO}':
            return self.metadata
        raise AssertionError(path)

    def candidates(self, client=None, role=METADATA):
        client = client or self.client()
        with mock.patch.object(client, 'request', side_effect=self.response):
            return client.candidate_role_jobs(REPO, role)

    def producer(self):
        self.run.update({'event': 'pull_request_target', 'head_sha': self.pr['head']['sha'],
                         'head_branch': self.pr['head']['ref'], 'referenced_workflows':
                         [{'path': PRODUCER_REF, 'sha': PRODUCER_REF[-40:]}]})
        self.job['head_sha'] = self.run['head_sha']
        self.job['labels'] = ['self-hosted', 'ci-pr-security-producer', 'sanctuary-tracker',
                              'ci-run-11-2', 'ci-pr-head-' + self.pr['head']['sha'],
                              'ci-execution-sha-' + self.branch['commit']['sha']]

    def state(self, candidate):
        return {**candidate, 'scope': 'organization', 'org': 'Tuinstra-DEV',
                'profile': manager.EXECUTION_ROLES[candidate['execution_role']]['profile'],
                'trigger_job_id': 22, 'trigger_run_id': 11,
                'runner_id': 30, 'runner_name': 'sanctuary-22', 'runner_group_id': 42}

    def test_policy_is_opt_in_and_old_three_pins_remain_unchanged(self):
        self.assertEqual(manager.configured_org_execution_roles(POLICY), {})
        self.assertEqual(manager.configured_org_workflow_refs(POLICY), (REF,))
        manager.validate_org_config(self.cfg)
        self.assertEqual(manager.configured_org_workflow_refs(self.cfg),
                         (REF, META_REF, PRODUCER_REF))
        self.assertEqual(self.candidates(self.client({})), [])

    def test_policy_rejects_mutability_unknown_paths_repos_roles_and_actor_wildcards(self):
        for field, value in [('workflow_ref', META_REF[:-40] + 'main'),
                             ('workflow_ref', META_REF.replace('reusable-trusted-verification', 'arbitrary')),
                             ('repositories', ['Tuinstra-DEV/notify']), ('events', ['workflow_run']),
                             ('actors', [{'login': '*', 'type': 'Bot'}])]:
            cfg = copy.deepcopy(self.cfg)
            cfg['org_execution_roles'][METADATA][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(manager.RunnerError):
                manager.validate_org_config(cfg)
        cfg = copy.deepcopy(self.cfg)
        cfg['org_execution_roles']['unknown'] = cfg['org_execution_roles'].pop(METADATA)
        with self.assertRaises(manager.RunnerError):
            manager.validate_org_config(cfg)
        with self.assertRaises(manager.RunnerError):
            manager.validate_org_config({**self.cfg, 'org_routing_enabled': False})

    def test_role_confusion_is_rejected_in_both_directions(self):
        self.assertEqual(len(self.candidates()), 1)
        self.assertFalse(manager.organization_labels_match(self.job['labels'], REPO, 'medium'))
        for extra in ('trusted-medium', 'trusted-heavy', 'ci-pr-security-producer', 'sanctuary-gate'):
            self.job['labels'].append(extra)
            self.assertEqual(self.candidates(), [], extra)
            self.job['labels'].pop()
        self.job['labels'] = ['self-hosted', 'trusted-medium', 'sanctuary-tracker']
        self.assertEqual(self.candidates(), [])
        self.assertFalse(manager.organization_labels_match(
            self.job['labels'] + ['ci-metadata-preflight'], REPO, 'medium'))

    def test_exact_workflow_sha_run_attempt_and_current_source_are_required(self):
        for mutation in ('workflow', 'sha', 'attempt', 'head', 'fork', 'public', 'unprotected'):
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation == 'workflow':
                    self.run['referenced_workflows'][0]['path'] = REF
                elif mutation == 'sha':
                    self.run['referenced_workflows'][0]['sha'] = 'f' * 40
                elif mutation == 'attempt':
                    self.job['run_attempt'] = 1
                elif mutation == 'head':
                    self.pr['head']['sha'] = 'f' * 40
                elif mutation == 'fork':
                    self.pr['head']['repo'] = {**self.pr['head']['repo'], 'fork': True}
                elif mutation == 'public':
                    self.metadata['private'] = False
                else:
                    self.branch['protected'] = False
                self.assertEqual(self.candidates(), [])

    def test_bot_cannot_escape_exact_role_or_trusted_policy(self):
        bot = {'login': 'dependabot[bot]', 'type': 'Bot'}
        self.run.update({'actor': bot, 'triggering_actor': bot})
        self.pr['user'] = bot
        self.assertEqual(self.candidates(), [])
        self.assertFalse(self.client()._trusted_run(self.run, REPO))
        self.producer()
        self.assertEqual(self.candidates(role=PRODUCER), [])
        self.run['actor'] = {'login': 'unknown[bot]', 'type': 'Bot'}
        self.assertEqual(self.candidates(), [])

    def test_producer_uses_current_protected_base_and_pins_pr_head(self):
        self.producer()
        result = self.candidates(role=PRODUCER)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['source_sha'], self.pr['head']['sha'])
        self.assertEqual(result[0]['execution_sha'], 'c' * 40)
        self.assertEqual(result[0]['pr_head_sha'], self.pr['head']['sha'])
        self.assertFalse(self.client()._trusted_run(self.run, REPO))
        self.branch['commit']['sha'] = 'f' * 40
        self.assertEqual(self.candidates(role=PRODUCER), [])

    def test_assigned_job_cannot_switch_role_source_attempt_group_or_trigger(self):
        candidate = self.candidates()[0]
        state = self.state(candidate)
        self.job.update({'runner_id': 30, 'runner_name': 'sanctuary-22',
                         'runner_group_id': 42, 'runner_group_name': manager.ORG_GROUP_NAME,
                         'status': 'in_progress'})
        client = self.client()
        assignment = {'repo': REPO, 'run_id': 11, 'job_id': 22}
        with mock.patch.object(client, 'request', side_effect=self.response):
            self.assertEqual(client.verify_org_assignment(state, assignment), META_REF)
            for key, value in [('runner_group_id', 99), ('run_attempt', 1), ('id', 23),
                               ('labels', ['self-hosted', 'trusted-medium', 'sanctuary-tracker'])]:
                original = self.job[key]
                self.job[key] = value
                with self.subTest(key=key), self.assertRaises(manager.RunnerError):
                    client.verify_org_assignment(state, assignment)
                self.job[key] = original
            self.job['head_sha'] = 'f' * 40
            with self.assertRaises(manager.RunnerError):
                client.verify_org_assignment(state, assignment)

    def test_local_policy_cannot_broaden_existing_actor_grants(self):
        for role in (METADATA, PRODUCER):
            cfg = copy.deepcopy(self.cfg)
            cfg['org_execution_roles'][role]['actors'] = [{'login': 'dependabot[bot]', 'type': 'Bot'}]
            with self.assertRaises(manager.RunnerError):
                manager.validate_org_config(cfg)

    def test_job_source_and_target_event_association_must_match_current_pr(self):
        self.job['head_sha'] = 'f' * 40
        self.assertEqual(self.candidates(), [])
        self.setUp()
        self.producer()
        self.run['pull_requests'][0]['head']['sha'] = 'f' * 40
        self.assertEqual(self.candidates(role=PRODUCER), [])
        self.run['pull_requests'][0]['head']['sha'] = self.pr['head']['sha']
        del self.run['pull_requests'][0]['base']
        self.assertEqual(self.candidates(role=PRODUCER), [])

    def test_jit_advertises_only_role_and_repository_not_capacity_trust(self):
        client = self.client()
        with mock.patch.object(client, 'request', return_value={}) as request:
            client.generate_role_jit(22, 42, REPO, METADATA, 11, 2, self.pr['head']['sha'])
        labels = request.call_args.args[2]['labels']
        self.assertEqual(set(labels), {'self-hosted', 'linux', 'x64',
                                       'ci-metadata-preflight', 'sanctuary-tracker',
                                       'ci-run-11-2', 'ci-pr-head-' + self.pr['head']['sha']})
        self.assertFalse({'trusted-heavy', 'trusted-medium'} & set(labels))

    def test_role_workflows_are_additive_to_three_legacy_pins_with_exact_group_equality(self):
        cfg = copy.deepcopy(self.cfg)
        del cfg['org_workflow_ref']
        cfg['org_workflow_refs'] = [REF, REF[:-40] + 'b' * 40, REF[:-40] + 'c' * 40]
        refs = manager.configured_org_workflow_refs(cfg)
        self.assertEqual(len(refs), 5)
        group = {'id': 42, 'name': manager.ORG_GROUP_NAME, 'visibility': 'selected',
                 'default': False, 'inherited': False, 'allows_public_repositories': False,
                 'restricted_to_workflows': True, 'selected_workflows': list(refs)}
        repos = {'total_count': 1, 'repositories': [self.metadata]}
        client = self.client()
        with mock.patch.object(client, 'request', side_effect=[group, repos]):
            client.verify_org_group(cfg)
        for changed in (list(refs[:-1]), list(refs) + [REF[:-40] + 'f' * 40],
                        list(refs) + [refs[0]]):
            with mock.patch.object(client, 'request', return_value={**group, 'selected_workflows': changed}):
                with self.assertRaises(manager.RunnerError):
                    client.verify_org_group(cfg)
        with mock.patch.object(client, 'request', side_effect=[group,
                               {'total_count': 1, 'repositories': [{**self.metadata, 'id': 999}]}]):
            with self.assertRaises(manager.RunnerError):
                client.verify_org_group(cfg)

    def release(self, role):
        self.metadata['default_branch'] = 'develop'
        self.other_branches['main'] = copy.deepcopy(self.branch)
        self.branch = {'name': 'develop', 'protected': True, 'commit': {'sha': 'd' * 40}}
        self.pr['head']['ref'] = 'develop'
        self.pr['head']['sha'] = 'd' * 40
        self.run['pull_requests'][0]['head']['sha'] = 'd' * 40
        self.run['head_branch'] = 'develop'
        self.run['head_sha'] = 'd' * 40
        self.job['head_sha'] = 'd' * 40
        self.job['labels'] = [label for label in self.job['labels'] if not label.startswith('ci-pr-head-')] + ['ci-pr-head-' + self.pr['head']['sha']]
        if role == PRODUCER:
            self.producer()

    def test_release_pr_keeps_protected_main_base_separate_from_default_develop(self):
        for role in (METADATA, PRODUCER):
            with self.subTest(role=role):
                self.setUp()
                self.release(role)
                result = self.candidates(role=role)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['base_sha'], 'c' * 40)
                self.assertEqual(result[0]['base_ref'], 'main')
                self.assertEqual(result[0]['default_sha'], 'd' * 40)
                self.assertEqual(result[0]['default_branch'], 'develop')
                self.assertEqual(result[0]['source_sha'], 'd' * 40)
                self.assertEqual(result[0]['execution_sha'], 'd' * 40 if role == PRODUCER else '')

    def test_release_actual_pr_base_must_independently_be_current_and_protected(self):
        for role in (METADATA, PRODUCER):
            for mutation in ('stale', 'unprotected', 'renamed', 'association'):
                with self.subTest(role=role, mutation=mutation):
                    self.setUp()
                    self.release(role)
                    if mutation == 'stale':
                        self.other_branches['main']['commit']['sha'] = 'f' * 40
                    elif mutation == 'unprotected':
                        self.other_branches['main']['protected'] = False
                    elif mutation == 'renamed':
                        self.other_branches['main']['name'] = 'other'
                    else:
                        self.run['pull_requests'][0]['base']['sha'] = 'f' * 40
                    self.assertEqual(self.candidates(role=role), [])

    def test_release_target_execution_must_stay_on_current_protected_default(self):
        for mutation in ('source', 'branch', 'unprotected'):
            with self.subTest(mutation=mutation):
                self.setUp()
                self.release(PRODUCER)
                if mutation == 'source':
                    self.branch['commit']['sha'] = 'f' * 40
                elif mutation == 'branch':
                    self.run['head_branch'] = 'main'
                    self.run['head_sha'] = 'c' * 40
                    self.job['head_sha'] = 'c' * 40
                else:
                    self.branch['protected'] = False
                self.assertEqual(self.candidates(role=PRODUCER), [])

    def test_producer_rejects_pr_event_fork_stale_pr_base_and_wrong_source_branch(self):
        for mutation in ('event', 'fork', 'base', 'branch', 'public', 'author'):
            with self.subTest(mutation=mutation):
                self.setUp()
                self.producer()
                if mutation == 'event':
                    self.run['event'] = 'pull_request'
                elif mutation == 'fork':
                    self.pr['head']['repo'] = {**self.pr['head']['repo'], 'fork': True}
                elif mutation == 'base':
                    self.pr['base']['sha'] = 'f' * 40
                elif mutation == 'branch':
                    self.run['head_branch'] = 'unprotected'
                elif mutation == 'public':
                    self.run['repository'] = {**self.run['repository'], 'private': False}
                else:
                    self.pr['user'] = {'login': 'dependabot[bot]', 'type': 'Bot'}
                self.assertEqual(self.candidates(role=PRODUCER), [])

    def test_metadata_push_is_current_protected_default_only_and_cannot_name_deploy(self):
        self.run.update({'event': 'push', 'head_sha': 'c' * 40, 'head_branch': 'main', 'pull_requests': []})
        self.job['head_sha'] = self.run['head_sha']
        self.job['labels'] = [label for label in self.job['labels'] if not label.startswith('ci-pr-head-')]
        self.assertEqual(len(self.candidates()), 1)
        self.run['head_branch'] = 'feat/unprotected'
        self.assertEqual(self.candidates(), [])
        for role in (METADATA, PRODUCER):
            cfg = copy.deepcopy(self.cfg)
            cfg['org_execution_roles'][role]['workflow_ref'] = (
                'Tuinstra-DEV/devops/.github/workflows/deploy-production.yml@' + 'f' * 40)
            with self.assertRaises(manager.RunnerError):
                manager.validate_org_config(cfg)

    def test_pre_jit_revalidates_group_current_source_and_attempt_without_registering(self):
        client = self.client()
        adapter = manager.ExecutionRoleDispatchClient(self.cfg, mock.Mock(), client,
                                                     METADATA, manager.WorkflowListSnapshot())
        with mock.patch.object(client, 'request', side_effect=self.response), \
                mock.patch.object(client, 'verify_org_group') as group, \
                mock.patch.object(client, 'generate_role_jit', return_value={}) as jit:
            candidate = adapter.candidate_jobs(REPO, 'ci-metadata-preflight')[0]
            adapter.generate_jit(REPO, candidate['job_id'], 42, 'ci-metadata-preflight')
            jit.assert_called_once_with(22, 42, REPO, METADATA, 11, 2, self.pr['head']['sha'], '')
            jit.reset_mock()
            for mutation in ('attempt', 'source', 'assignment', 'group'):
                with self.subTest(mutation=mutation):
                    saved_run, saved_job, saved_pr = copy.deepcopy((self.run, self.job, self.pr))
                    if mutation == 'attempt':
                        self.run['run_attempt'] = 3
                    elif mutation == 'source':
                        self.pr['head']['sha'] = 'f' * 40
                    elif mutation == 'assignment':
                        self.job['runner_id'] = 88
                    else:
                        group.side_effect = manager.RunnerError('group mismatch')
                    with self.assertRaises(manager.OrgPreflightRejected):
                        adapter.generate_jit(REPO, 22, 42, 'ci-metadata-preflight')
                    jit.assert_not_called()
                    self.run, self.job, self.pr = saved_run, saved_job, saved_pr
                    group.side_effect = None

    def test_dispatch_keeps_capacity_profile_and_persists_role_source_pins(self):
        candidate = self.candidates()[0]
        with tempfile.TemporaryDirectory() as temporary:
            cfg = {**self.cfg, 'state_dir': str(Path(temporary) / 'state'),
                   'max_concurrency': 4, 'runner_label': 'trusted-heavy',
                   'medium_runner_label': 'trusted-medium'}
            adapter = mock.Mock()
            adapter.candidate_jobs.return_value = [candidate]
            adapter.generate_jit.return_value = {'runner': {'id': 30}, 'encoded_jit_config': 'aGVsbG8='}
            with mock.patch.object(manager, 'pre_jit_capacity_errors', return_value=[]) as capacity, \
                    mock.patch.object(manager, 'launch') as launch:
                self.assertTrue(manager.dispatch_once(cfg, adapter, profile='medium',
                                                     scope='organization', execution_role=METADATA))
                self.assertEqual(capacity.call_args.args[2], 'medium')
                self.assertEqual(launch.call_args.kwargs['profile'], 'medium')
                for key in manager.ROLE_PIN_FIELDS:
                    self.assertEqual(launch.call_args.kwargs['metadata'][key], candidate[key])
                adapter.generate_jit.assert_called_once_with(REPO, 22, 42, 'ci-metadata-preflight')
            with self.assertRaises(manager.RunnerError):
                manager.dispatch_once(cfg, adapter, profile='heavy',
                                      scope='organization', execution_role=METADATA)

    def test_verified_role_assignments_are_revalidated_after_policy_or_source_changes(self):
        candidate = self.candidates()[0]
        state = {**self.state(candidate), 'lease': 'gh-22', 'actual_job_id': 22, 'actual_run_id': 11}
        client = mock.Mock()
        client.verify_org_assignment.side_effect = manager.RunnerError('policy disabled')
        with tempfile.TemporaryDirectory() as temporary:
            store = manager.StateStore(Path(temporary))
            with self.assertRaises(manager.RunnerError):
                manager.record_verified_assignment(store, None, state, include_completed=True,
                                                   org_client=client)
        client.verify_org_assignment.assert_called_once()

    def test_queue_timestamp_is_validated_and_used_only_for_order(self):
        for stamp in ('2026-02-30T00:00:00Z', 'today', '', None, 3, '2026-10-18T10:00:00+00:00'):
            self.assertEqual(manager.queue_timestamp({'created_at': stamp}), {})
        stamp = '2026-10-18T10:00:00Z'
        self.assertEqual(manager.queue_timestamp({'created_at': stamp}), {'queued_at': stamp})
        self.job['created_at'] = stamp
        self.assertEqual(self.candidates()[0]['queued_at'], stamp)
        self.pr['head']['sha'] = 'f' * 40
        self.assertEqual(self.candidates(), [])

    def test_fair_queue_merges_role_and_trusted_jobs_and_continues_after_heavy_full(self):
        role_candidate = {**self.candidates()[0], 'queued_at': '2026-10-18T10:01:00Z'}
        repo = mock.Mock()
        org = mock.Mock()
        repo.candidate_jobs.return_value = [
            {'repo': REPO, 'run_id': 1, 'job_id': 1, 'queued_at': '2026-10-18T10:02:00Z'}]
        org.candidate_trusted_jobs.side_effect = lambda repo, profile, **kw: [
            {'repo': repo, 'run_id': 2, 'job_id': 2, 'workflow_ref': REF,
             'queued_at': '2026-10-18T10:00:00Z'}] if profile == 'heavy' else []
        org.candidate_role_jobs.side_effect = lambda repo, role, **kw: [role_candidate] if role == METADATA else []
        called = []

        def dispatch(cfg, selected, *, profile, scope, execution_role=None):
            called.append((selected.candidate['job_id'], profile, scope, execution_role))
            return profile == 'medium'  # Heavy full must not stop the shared queue.

        with mock.patch.object(manager, 'dispatch_once', side_effect=dispatch):
            self.assertTrue(manager.dispatch_fair_once(self.cfg, repo, org))
        self.assertEqual(called, [(2, 'heavy', 'organization', None),
                                  (22, 'medium', 'organization', METADATA),
                                  (1, 'heavy', 'repository', None)])
        org.verify_org_group.assert_called_once_with(self.cfg)

    def test_selected_candidate_cannot_reregister_a_different_job_or_label(self):
        candidate = self.candidates()[0]
        client = mock.Mock()
        selected = manager.SelectedCandidateClient(client, candidate, 'ci-metadata-preflight')
        self.assertEqual(selected.candidate_jobs(REPO, 'trusted-medium'), [])
        for repo, job, label in ((REPO, 99, 'ci-metadata-preflight'),
                                 ('Tuinstra-DEV/gate', 22, 'ci-metadata-preflight'),
                                 (REPO, 22, 'trusted-medium')):
            with self.assertRaises(manager.RunnerError):
                selected.generate_jit(repo, job, 42, label)
        client.generate_jit.assert_not_called()

    def assigned_role_fixture(self):
        candidate = self.candidates()[0]
        state = {**self.state(candidate), 'lease': 'role', 'phase': 'running', 'launched_at': 100}
        self.job.update({'runner_id': 30, 'runner_name': 'sanctuary-22',
                         'runner_group_id': 42, 'runner_group_name': manager.ORG_GROUP_NAME,
                         'status': 'in_progress'})
        return candidate, state, {'repo': REPO, 'run_id': 11, 'job_id': 22}

    def test_review_running_job_keeps_immutable_pins_when_current_tips_move(self):
        candidate, state, assignment = self.assigned_role_fixture()
        client = self.client()
        self.branch['commit']['sha'] = 'f' * 40
        self.pr['head']['sha'] = 'e' * 40
        self.pr['state'] = 'closed'
        self.metadata['default_branch'] = 'changed-after-dispatch'

        def immutable_only(method, path, body=None):
            self.assertNotIn('/branches/', path)
            self.assertNotIn('/pulls/', path)
            self.assertNotEqual(path, f'/repos/{REPO}')
            return self.response(method, path, body)

        with mock.patch.object(client, 'request', side_effect=immutable_only):
            self.assertEqual(client.verify_org_assignment(state, assignment), META_REF)
            with tempfile.TemporaryDirectory() as temporary:
                state.update({'actual_job_id': 22, 'actual_run_id': 11})
                checked = manager.record_verified_assignment(manager.StateStore(Path(temporary)), None,
                            state, include_completed=False, org_client=client)
                self.assertEqual(checked['source_sha'], candidate['source_sha'])

    def test_review_wrong_assignment_reaps_only_bad_guest_and_continues_cleanup_dispatch(self):
        import json
        import subprocess
        _, bad, _ = self.assigned_role_fixture()
        client = self.client()
        with tempfile.TemporaryDirectory() as temporary:
            cfg = {**self.cfg, 'state_dir': str(Path(temporary) / 'state'),
                   'lock_file': str(Path(temporary) / 'lock'), 'max_lease_seconds': 7200}
            store = manager.StateStore(Path(cfg['state_dir']))
            store.write('role', bad)
            store.write('healthy', {'lease': 'healthy', 'phase': 'running', 'profile': 'heavy',
                                    'launched_at': 100})
            store.write('expired', {'lease': 'expired', 'phase': 'running', 'profile': 'heavy',
                                    'launched_at': -10000})

            def host(_cfg, command, *args, **kwargs):
                payload = {'role': 'running', 'healthy': 'running', 'expired': 'running'} if command == 'list' else {}
                return subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b'')

            def cleanup(_client, state, _org):
                if state['lease'] == 'role':
                    raise manager.RunnerError('remote cleanup temporarily unavailable')

            with mock.patch.object(manager, 'helper', side_effect=host) as helper, \
                    mock.patch.object(manager.time, 'time', return_value=200), \
                    mock.patch.object(client, 'find_assigned_job', return_value={
                        'repo': REPO, 'run_id': 99, 'job_id': 22}), \
                    mock.patch.object(client, 'request', side_effect=self.response), \
                    mock.patch.object(manager, 'cleanup_github_runner', side_effect=cleanup), \
                    mock.patch.object(manager, 'dispatch_fair_once', return_value=True) as dispatch:
                manager.reconcile(cfg, None, client)
                self.assertTrue(manager.dispatch_fair_once(cfg, None, client))
            destroyed = [call.args[2] for call in helper.call_args_list if call.args[1] == 'destroy']
            self.assertEqual(sorted(destroyed), ['expired', 'role'])
            self.assertEqual([state['lease'] for state in store.leases()], ['healthy'])
            self.assertTrue(any(item.get('lease') == 'role' for item in store.cleanup_items()))
            dispatch.assert_called_once()

    def test_review_empty_association_finds_one_source_bound_open_pr(self):
        for role in (METADATA, PRODUCER):
            with self.subTest(role=role):
                self.setUp()
                if role == PRODUCER:
                    self.producer()
                self.run['pull_requests'] = []
                client = self.client()

                def response(method, path, body=None):
                    if '/pulls?' in path:
                        return [self.pr]
                    return self.response(method, path, body)

                with mock.patch.object(client, 'request', side_effect=response):
                    result = client.candidate_role_jobs(REPO, role)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['pr_number'], 7)
                self.assertEqual(result[0]['pr_head_sha'], self.pr['head']['sha'])

    def test_review_metadata_push_may_be_associated_with_open_release_pr(self):
        self.run.update({'event': 'push', 'head_sha': 'c' * 40, 'head_branch': 'main'})
        self.job['head_sha'] = self.run['head_sha']
        self.job['labels'] = [label for label in self.job['labels'] if not label.startswith('ci-pr-head-')]
        self.assertEqual(len(self.candidates()), 1)

    def test_review_fair_dispatch_skips_definitive_rejection_but_propagates_rate_limit(self):
        candidate = {**self.candidates()[0], 'queued_at': '2026-10-18T10:00:00Z'}
        repo, org = mock.Mock(), mock.Mock()
        repo.candidate_jobs.return_value = [{'repo': REPO, 'run_id': 99, 'job_id': 99,
                                            'queued_at': '2026-10-18T10:01:00Z'}]
        org.candidate_trusted_jobs.return_value = []
        org.candidate_role_jobs.side_effect = lambda repo, role, **kw: [candidate] if role == METADATA else []
        with mock.patch.object(manager, 'dispatch_once', side_effect=[
                manager.OrgPreflightRejected('queued source moved'), True]) as dispatch:
            self.assertTrue(manager.dispatch_fair_once(self.cfg, repo, org))
            self.assertEqual(dispatch.call_count, 2)
        with mock.patch.object(manager, 'dispatch_once', side_effect=manager.RateLimited(60)) as dispatch:
            with self.assertRaises(manager.RateLimited):
                manager.dispatch_fair_once(self.cfg, repo, org)
            self.assertEqual(dispatch.call_count, 1)

    def test_review_run_attempt_label_and_pr_source_label_are_mandatory_and_exact(self):
        original = list(self.job['labels'])
        for changed in ([label for label in original if not label.startswith('ci-run-')],
                        [label.replace('ci-run-11-2', 'ci-run-99-2') for label in original],
                        [label.replace('ci-run-11-2', 'ci-run-11-1') for label in original],
                        [label for label in original if not label.startswith('ci-pr-head-')],
                        original + ['ci-run-99-2'],
                        original + ['ci-pr-head-' + 'f' * 40]):
            with self.subTest(labels=changed):
                self.job['labels'] = changed
                self.assertEqual(self.candidates(), [])
        self.job['labels'] = original
        client = self.client()
        with mock.patch.object(client, 'request', return_value={}) as request:
            client.generate_role_jit(22, 42, REPO, METADATA, 11, 2, self.pr['head']['sha'])
        labels = request.call_args.args[2]['labels']
        self.assertIn('ci-run-11-2', labels)
        self.assertIn('ci-pr-head-' + self.pr['head']['sha'], labels)
        self.assertNotIn('trusted-medium', labels)
        for run, attempt in ((True, 2), (11, False), (0, 2), (11, -1), ('11', 2)):
            with self.subTest(run=run, attempt=attempt), mock.patch.object(client, 'request') as request:
                with self.assertRaises(manager.RunnerError):
                    client.generate_role_jit(22, 42, REPO, METADATA, run, attempt)
                request.assert_not_called()

    def test_review_empty_association_rejects_ambiguous_wrong_stale_and_unbounded_source(self):
        for role in (METADATA, PRODUCER):
            for mutation in ('ambiguous', 'wrong-source', 'stale-detail', 'fork', 'closed', 'unbounded'):
                with self.subTest(role=role, mutation=mutation):
                    self.setUp()
                    if role == PRODUCER:
                        self.producer()
                    self.run['pull_requests'] = []
                    found = [copy.deepcopy(self.pr)]
                    if mutation == 'ambiguous':
                        found.append({**found[0], 'number': 8})
                    elif mutation == 'wrong-source':
                        found[0]['head']['sha'] = 'f' * 40
                    elif mutation == 'stale-detail':
                        self.pr['head']['sha'] = 'f' * 40
                    elif mutation == 'fork':
                        self.pr['head']['repo'] = {**self.pr['head']['repo'], 'fork': True}
                    elif mutation == 'closed':
                        self.pr['state'] = 'closed'
                    else:
                        found = [copy.deepcopy(self.pr) for _ in range(100)]
                    client = self.client()

                    def response(method, path, body=None):
                        if '/pulls?' in path:
                            return found
                        return self.response(method, path, body)

                    with mock.patch.object(client, 'request', side_effect=response):
                        self.assertEqual(client.candidate_role_jobs(REPO, role), [])

    def test_review_running_attempt_does_not_follow_latest_rerun(self):
        _, state, assignment = self.assigned_role_fixture()
        original = copy.deepcopy(self.run)
        self.run['run_attempt'] = 3
        self.run['head_sha'] = 'f' * 40
        client = self.client()

        def response(method, path, body=None):
            if path.endswith('/attempts/2'):
                return original
            return self.response(method, path, body)

        with mock.patch.object(client, 'request', side_effect=response):
            self.assertEqual(client.verify_org_assignment(state, assignment), META_REF)
            original['head_sha'] = 'f' * 40
            with self.assertRaises(manager.AssignmentMismatch):
                client.verify_org_assignment(state, assignment)

    def test_review_pre_jit_branch_movement_still_rejects_source_base_and_default(self):
        for role in (METADATA, PRODUCER):
            for changed in ('default', 'base', 'head', 'attempt'):
                with self.subTest(role=role, changed=changed):
                    self.setUp()
                    self.release(role)
                    client = self.client()
                    adapter = manager.ExecutionRoleDispatchClient(self.cfg, mock.Mock(), client, role,
                                                                 manager.WorkflowListSnapshot())
                    label = manager.EXECUTION_ROLES[role]['label']
                    with mock.patch.object(client, 'request', side_effect=self.response), \
                            mock.patch.object(client, 'verify_org_group'), \
                            mock.patch.object(client, 'generate_role_jit') as jit:
                        self.assertEqual(len(adapter.candidate_jobs(REPO, label)), 1)
                        if changed == 'default':
                            self.branch['commit']['sha'] = 'f' * 40
                        elif changed == 'base':
                            self.other_branches['main']['commit']['sha'] = 'f' * 40
                        elif changed == 'head':
                            self.pr['head']['sha'] = 'f' * 40
                        else:
                            self.run['run_attempt'] = 3
                        with self.assertRaises(manager.OrgPreflightRejected):
                            adapter.generate_jit(REPO, 22, 42, label)
                        jit.assert_not_called()

    def actual_target_fixture(self):
        # Synthetic API fixture with the distinct source/controller SHAs from
        # successful Gate producer run 39135363284, not a live admission claim.
        source = '236bb4dbd7b02fd5a37f7204daa7bc1e5b81257b'
        execution = 'a1ab634fbabdfb8f00895863b4448624778d379d'
        self.metadata['default_branch'] = 'develop'
        self.branch = {'name': 'develop', 'protected': True, 'commit': {'sha': execution}}
        self.pr['head'].update({'sha': source, 'ref': 'feature/IN-2-heal-provider-deadline'})
        self.pr['base'].update({'sha': execution, 'ref': 'develop'})
        self.run['pull_requests'] = [{'number': 7, 'head': {'sha': source}, 'base': {'sha': execution}}]
        self.producer()
        return source, execution

    def test_actual_target_source_is_distinct_from_protected_execution_controller(self):
        source, execution = self.actual_target_fixture()
        result = self.candidates(role=PRODUCER)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['source_sha'], source)
        self.assertEqual(result[0]['head_branch'], self.pr['head']['ref'])
        self.assertEqual(result[0]['pr_head_sha'], source)
        self.assertEqual(result[0]['execution_sha'], execution)
        self.assertEqual(result[0]['default_sha'], execution)
        self.assertNotEqual(result[0]['source_sha'], result[0]['execution_sha'])

    def test_target_execution_label_is_mandatory_exact_and_current(self):
        for changed in ('missing', 'duplicate', 'wrong', 'malformed', 'source-as-execution'):
            with self.subTest(changed=changed):
                self.setUp()
                source, execution = self.actual_target_fixture()
                labels = self.job['labels']
                label = 'ci-execution-sha-' + execution
                if changed == 'missing':
                    labels.remove(label)
                elif changed == 'duplicate':
                    labels.append(label)
                else:
                    labels.remove(label)
                    labels.append('ci-execution-sha-' + ({'wrong': 'f' * 40, 'malformed': 'develop',
                                                          'source-as-execution': source}[changed]))
                self.assertEqual(self.candidates(role=PRODUCER), [])

    def test_target_api_source_sha_and_branch_must_both_match_current_pr(self):
        for changed in ('sha', 'branch'):
            with self.subTest(changed=changed):
                self.setUp()
                self.actual_target_fixture()
                if changed == 'sha':
                    self.run['head_sha'] = self.branch['commit']['sha']
                    self.job['head_sha'] = self.run['head_sha']
                else:
                    self.run['head_branch'] = 'develop'
                self.assertEqual(self.candidates(role=PRODUCER), [])

    def test_normal_pr_and_push_reject_any_execution_label(self):
        for event in ('pull_request', 'push'):
            with self.subTest(event=event):
                self.setUp()
                if event == 'push':
                    self.run.update({'event': event, 'head_sha': 'c' * 40, 'head_branch': 'main'})
                    self.job['head_sha'] = self.run['head_sha']
                    self.job['labels'] = [value for value in self.job['labels'] if not value.startswith('ci-pr-head-')]
                self.assertEqual(self.candidates()[0]['execution_sha'], '')
                self.job['labels'].append('ci-execution-sha-' + 'c' * 40)
                self.assertEqual(self.candidates(), [])

    def test_target_metadata_is_explicitly_unsupported(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['org_execution_roles'][METADATA]['events'].append('pull_request_target')
        with self.assertRaises(manager.RunnerError):
            manager.validate_org_config(cfg)
        self.run['event'] = 'pull_request_target'
        self.assertEqual(self.candidates(), [])
        self.job['labels'].append('ci-execution-sha-' + 'c' * 40)
        self.assertEqual(self.candidates(), [])

    def test_producer_jit_advertises_exact_execution_pin_and_state_persists_it(self):
        _, execution = self.actual_target_fixture()
        candidate = self.candidates(role=PRODUCER)[0]
        client = self.client()
        adapter = manager.ExecutionRoleDispatchClient(self.cfg, mock.Mock(), client, PRODUCER,
                                                     manager.WorkflowListSnapshot())
        with mock.patch.object(client, 'request', side_effect=self.response), \
                mock.patch.object(client, 'verify_org_group'), \
                mock.patch.object(client, 'generate_role_jit', return_value={}) as jit:
            adapter.candidate_jobs(REPO, 'ci-pr-security-producer')
            adapter.generate_jit(REPO, 22, 42, 'ci-pr-security-producer')
        jit.assert_called_once_with(22, 42, REPO, PRODUCER, 11, 2, candidate['pr_head_sha'], execution)
        with mock.patch.object(client, 'request', return_value={}) as request:
            client.generate_role_jit(22, 42, REPO, PRODUCER, 11, 2, candidate['pr_head_sha'], execution)
        labels = request.call_args.args[2]['labels']
        self.assertEqual(labels.count('ci-execution-sha-' + execution), 1)
        self.assertFalse({'trusted-heavy', 'trusted-medium'} & set(labels))
        for role, execution_sha in ((PRODUCER, ''), (PRODUCER, 'main'), (PRODUCER, 123), (PRODUCER, None), (METADATA, execution)):
            with self.subTest(role=role, execution=execution_sha), mock.patch.object(client, 'request') as request:
                with self.assertRaises(manager.RunnerError):
                    client.generate_role_jit(22, 42, REPO, role, 11, 2, candidate['pr_head_sha'], execution_sha)
                request.assert_not_called()
        with tempfile.TemporaryDirectory() as temporary:
            cfg = {**self.cfg, 'state_dir': str(Path(temporary) / 'state'), 'max_concurrency': 4,
                   'runner_label': 'trusted-heavy', 'medium_runner_label': 'trusted-medium'}
            selected = mock.Mock()
            selected.candidate_jobs.return_value = [candidate]
            selected.generate_jit.return_value = {'runner': {'id': 30}, 'encoded_jit_config': 'aGVsbG8='}
            with mock.patch.object(manager, 'pre_jit_capacity_errors', return_value=[]), \
                    mock.patch.object(manager, 'launch') as launch:
                self.assertTrue(manager.dispatch_once(cfg, selected, profile='heavy', scope='organization',
                                                     execution_role=PRODUCER))
            self.assertEqual(launch.call_args.kwargs['metadata']['execution_sha'], execution)
            self.assertEqual(launch.call_args.kwargs['metadata']['source_sha'], candidate['source_sha'])

    def test_running_target_keeps_execution_pin_without_reading_moving_tips(self):
        _, execution = self.actual_target_fixture()
        candidate = self.candidates(role=PRODUCER)[0]
        state = self.state(candidate)
        self.job.update({'runner_id': 30, 'runner_name': 'sanctuary-22', 'runner_group_id': 42,
                         'runner_group_name': manager.ORG_GROUP_NAME, 'status': 'in_progress'})
        assignment = {'repo': REPO, 'run_id': 11, 'job_id': 22}
        self.branch['commit']['sha'] = 'f' * 40
        self.pr['head']['sha'] = 'e' * 40
        self.pr['state'] = 'closed'
        client = self.client()

        def immutable_only(method, path, body=None):
            self.assertNotIn('/branches/', path)
            self.assertNotIn('/pulls', path)
            self.assertNotEqual(path, f'/repos/{REPO}')
            return self.response(method, path, body)

        with mock.patch.object(client, 'request', side_effect=immutable_only):
            self.assertEqual(client.verify_org_assignment(state, assignment), PRODUCER_REF)
            labels = list(self.job['labels'])
            for changed in ('missing', 'wrong', 'duplicate'):
                self.job['labels'] = list(labels)
                if changed == 'duplicate':
                    self.job['labels'].append('ci-execution-sha-' + execution)
                else:
                    self.job['labels'].remove('ci-execution-sha-' + execution)
                    if changed == 'wrong':
                        self.job['labels'].append('ci-execution-sha-' + 'f' * 40)
                with self.subTest(changed=changed), self.assertRaises(manager.AssignmentMismatch):
                    client.verify_org_assignment(state, assignment)

    def test_pre_jit_rejects_changed_execution_label_without_registering(self):
        self.actual_target_fixture()
        client = self.client()
        adapter = manager.ExecutionRoleDispatchClient(self.cfg, mock.Mock(), client, PRODUCER,
                                                     manager.WorkflowListSnapshot())
        with mock.patch.object(client, 'request', side_effect=self.response), \
                mock.patch.object(client, 'verify_org_group'), \
                mock.patch.object(client, 'generate_role_jit') as jit:
            self.assertEqual(len(adapter.candidate_jobs(REPO, 'ci-pr-security-producer')), 1)
            self.job['labels'] = [value for value in self.job['labels'] if not value.startswith('ci-execution-sha-')]
            self.job['labels'].append('ci-execution-sha-' + 'f' * 40)
            with self.assertRaises(manager.OrgPreflightRejected):
                adapter.generate_jit(REPO, 22, 42, 'ci-pr-security-producer')
            jit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
