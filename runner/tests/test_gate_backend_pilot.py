"""A separate opt-in role admits only the exact compact Gate backend job."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

import ci_runner_manager as manager
from test_org_routing import POLICY, REF

REPO = 'Tuinstra-DEV/gate'
ROLE = 'gate-backend-pilot'
PILOT = 'Tuinstra-DEV/devops/.github/workflows/reusable-product-ci.yml@' + 'd' * 40
HEAD, BASE, MERGE = 'a' * 40, 'b' * 40, 'c' * 40


class GateBackendPilotTests(unittest.TestCase):
    def setUp(self):
        self.policy = {'workflow_ref': PILOT, 'repositories': [REPO],
                       'events': ['pull_request'],
                       'actors': [{'login': 'marcel-tuinstra', 'type': 'User'}]}
        self.cfg = {**POLICY, 'org_runner_group_id': 4, 'repositories': [REPO],
                    'org_repository_ids': {REPO: 1273386924},
                    'org_execution_roles': {ROLE: self.policy}}
        repository = {'id': 1273386924, 'full_name': REPO, 'private': True,
                      'fork': False, 'default_branch': 'develop'}
        owner = {'login': 'marcel-tuinstra', 'type': 'User'}
        self.run = {'id': 11, 'run_attempt': 2, 'event': 'pull_request',
                    'head_sha': HEAD, 'head_branch': 'chore/DEV-54-compact-ci-pilot',
                    'repository': copy.deepcopy(repository), 'head_repository': copy.deepcopy(repository),
                    'actor': owner.copy(), 'triggering_actor': owner.copy(),
                    'referenced_workflows': [{'path': PILOT, 'sha': PILOT[-40:]}],
                    'pull_requests': [{'number': 444, 'head': {'sha': HEAD}, 'base': {'sha': BASE}}]}
        self.pr = {'number': 444, 'state': 'open', 'merged': False, 'user': owner.copy(),
                   'head': {'ref': self.run['head_branch'], 'sha': HEAD, 'repo': copy.deepcopy(repository)},
                   'base': {'ref': 'develop', 'sha': BASE, 'repo': copy.deepcopy(repository)},
                   'merge_commit_sha': MERGE}
        self.metadata = repository
        self.branch = {'name': 'develop', 'protected': True, 'commit': {'sha': BASE}}
        self.merge = {'sha': MERGE, 'parents': [{'sha': HEAD}, {'sha': BASE}]}
        self.job = {'id': 22, 'run_id': 11, 'run_attempt': 2, 'head_sha': HEAD,
                    'name': 'ci / Backend CI', 'status': 'queued',
                    'labels': ['self-hosted', 'ci-gate-backend-pilot', 'sanctuary-gate',
                               'ci-run-11-2', 'ci-pr-head-' + HEAD, 'ci-execution-sha-' + MERGE]}

    def client(self):
        return manager.GitHubClient('synthetic-token',
                                    org_workflow_refs=manager.configured_org_workflow_refs(self.cfg),
                                    org_execution_roles=self.cfg['org_execution_roles'])

    def response(self, method, path, body=None):
        self.assertEqual(method, 'GET')
        if 'status=queued' in path: return {'workflow_runs': [self.run]}
        if 'status=in_progress' in path: return {'workflow_runs': []}
        if '/jobs?' in path: return {'jobs': [self.job]}
        if path.endswith('/actions/jobs/22'): return self.job
        if path.endswith('/actions/runs/11') or path.endswith('/attempts/2'): return self.run
        if path.endswith('/pulls/444'): return self.pr
        if '/pulls?' in path: return [self.pr]
        if path.endswith('/branches/develop'): return self.branch
        if '/git/ref/heads/' in path:
            return {'ref': 'refs/heads/' + self.run['head_branch'], 'object': {'type': 'commit', 'sha': HEAD}}
        if path == '/repos/' + REPO: return self.metadata
        if path.endswith('/git/commits/' + MERGE): return self.merge
        raise AssertionError(path)

    def candidates(self, client=None):
        client = client or self.client()
        with mock.patch.object(client, 'request', side_effect=self.response):
            return client.candidate_role_jobs(REPO, ROLE)

    def test_opt_in_does_not_extend_legacy_trusted_pins(self):
        manager.validate_org_config(self.cfg)
        self.assertEqual(manager.configured_trusted_workflow_refs(self.cfg), (REF,))
        self.assertEqual(manager.configured_org_workflow_refs(self.cfg), (REF, PILOT))
        self.cfg['org_execution_roles'] = {}
        self.assertEqual(self.candidates(), [])

    def test_exact_backend_candidate_has_merge_and_attempt_binding(self):
        candidate, = self.candidates()
        self.assertEqual(candidate['execution_sha'], MERGE)
        self.assertEqual(candidate['pr_head_sha'], HEAD)
        self.assertEqual(candidate['workflow_ref'], PILOT)
        self.assertEqual(candidate['run_attempt'], 2)
        self.assertFalse(manager.organization_labels_match(self.job['labels'], REPO, 'medium'))
        client = self.client()
        with mock.patch.object(client, 'request', side_effect=self.response):
            self.assertEqual(client.verify_role_job(candidate, self.job), PILOT)
            self.pr['head']['sha'] = 'f' * 40
            with self.assertRaises(manager.RunnerError):
                client.verify_role_job(candidate, self.job)

    def test_wrong_repo_event_actor_or_mutable_policy_is_rejected(self):
        for key, value in [('repositories', ['Tuinstra-DEV/tracker']),
                           ('events', ['workflow_dispatch']),
                           ('actors', [{'login': '*', 'type': 'User'}]),
                           ('workflow_ref', PILOT[:-40] + 'main')]:
            self.setUp()
            self.policy[key] = value
            with self.subTest(key=key), self.assertRaises(manager.RunnerError):
                manager.validate_org_config(self.cfg)

    def test_candidate_rejections_cover_source_trust_and_job_confusion(self):
        for mutation in ['fork', 'public', 'bot', 'unknown', 'author', 'event', 'branch',
                         'base', 'unprotected', 'closed', 'stale-head', 'stale-base',
                         'pin', 'sha', 'nested', 'merge', 'parents', 'attempt',
                         'job-name', 'legacy-labels', 'other-run', 'other-execution']:
            self.setUp()
            if mutation == 'fork': self.pr['head']['repo']['fork'] = True
            elif mutation == 'public': self.run['repository']['private'] = False
            elif mutation == 'bot': self.run['actor']['type'] = 'Bot'
            elif mutation == 'unknown': self.run['triggering_actor']['login'] = 'unknown'
            elif mutation == 'author': self.pr['user']['login'] = 'unknown'
            elif mutation == 'event': self.run['event'] = 'pull_request_target'
            elif mutation == 'branch': self.run['head_branch'] = 'another-branch'
            elif mutation == 'base': self.pr['base']['ref'] = 'main'
            elif mutation == 'unprotected': self.branch['protected'] = False
            elif mutation == 'closed': self.pr['state'] = 'closed'
            elif mutation == 'stale-head': self.pr['head']['sha'] = 'f' * 40
            elif mutation == 'stale-base': self.pr['base']['sha'] = 'f' * 40
            elif mutation == 'pin': self.run['referenced_workflows'][0]['path'] = PILOT[:-40] + 'main'
            elif mutation == 'sha': self.run['referenced_workflows'][0]['sha'] = 'f' * 40
            elif mutation == 'nested': self.run['referenced_workflows'].append({'path': REF, 'sha': REF[-40:]})
            elif mutation == 'merge': self.pr['merge_commit_sha'] = 'f' * 40
            elif mutation == 'parents': self.merge['parents'][1]['sha'] = 'f' * 40
            elif mutation == 'attempt': self.job['run_attempt'] = 1
            elif mutation == 'job-name': self.job['name'] = 'ci / Frontend CI'
            elif mutation == 'legacy-labels': self.job['labels'].append('trusted-medium')
            elif mutation == 'other-run': self.job['labels'][3] = 'ci-run-12-2'
            elif mutation == 'other-execution': self.job['labels'][-1] = 'ci-execution-sha-' + 'f' * 40
            with self.subTest(mutation=mutation):
                self.assertEqual(self.candidates(), [])

    def test_assignment_rejects_another_job_run_attempt_or_role(self):
        candidate, = self.candidates()
        client = self.client()
        for key, value in [('id', 23), ('run_id', 12), ('run_attempt', 3),
                           ('name', 'ci / Frontend CI')]:
            changed = {**self.job, key: value}
            with self.subTest(key=key), self.assertRaises(manager.RunnerError):
                client.verify_role_job(candidate, changed)
        with mock.patch.object(client, 'request') as request:
            client.generate_role_jit(22, 4, REPO, ROLE, 11, 2, HEAD, MERGE)
        self.assertEqual(request.call_args.args[0], 'POST')
        self.assertEqual(set(request.call_args.args[2]['labels']), set(self.job['labels']) | {'linux', 'x64'})

    def test_group_and_manager_must_change_together(self):
        client = self.client()
        group = {'id': 4, 'name': 'sanctuary-trusted-verification', 'visibility': 'selected',
                 'default': False, 'inherited': False, 'allows_public_repositories': False,
                 'restricted_to_workflows': True, 'selected_workflows': [REF, PILOT]}
        repositories = {'total_count': 1, 'repositories': [self.metadata]}
        for refs in [[REF], [REF, PILOT], [REF, PILOT, PILOT]]:
            with mock.patch.object(client, 'request', side_effect=[{**group, 'selected_workflows': refs}, repositories]):
                if refs == [REF, PILOT]: client.verify_org_group(self.cfg)
                else:
                    with self.assertRaises(manager.RunnerError): client.verify_org_group(self.cfg)

    def test_current_identity_is_rechecked_before_any_jit_post(self):
        candidate, = self.candidates()
        client = self.client()
        adapter = manager.ExecutionRoleDispatchClient(self.cfg, client, client, ROLE, manager.WorkflowListSnapshot())
        adapter.candidates[(REPO, 22)] = candidate
        for stale in [False, True]:
            self.pr['head']['sha'] = 'f' * 40 if stale else HEAD
            with mock.patch.object(client, 'verify_org_group'), \
                    mock.patch.object(client, 'request', side_effect=self.response), \
                    mock.patch.object(client, 'generate_role_jit', return_value={'synthetic': True}) as jit:
                if stale:
                    with self.assertRaises(manager.OrgPreflightRejected):
                        adapter.generate_jit(REPO, 22, 4, 'ci-gate-backend-pilot')
                    jit.assert_not_called()
                else:
                    self.assertEqual(adapter.generate_jit(REPO, 22, 4, 'ci-gate-backend-pilot'), {'synthetic': True})


class GateWorkflowRouteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GateBackendPilotTests()
        self.fixture.setUp()
        path = Path(__file__).resolve().parents[2] / '.github/actions/gate-compact-trust/select_runner.py'
        spec = importlib.util.spec_from_file_location('compact_route', path)
        self.route = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.route)
        self.event = {'pull_request': copy.deepcopy(self.fixture.pr),
                      'repository': copy.deepcopy(self.fixture.metadata),
                      'sender': {'login': 'marcel-tuinstra', 'type': 'User'}}
        self.env = {'GITHUB_REPOSITORY': REPO, 'GITHUB_EVENT_NAME': 'pull_request',
                    'GITHUB_ACTOR': 'marcel-tuinstra', 'GITHUB_TRIGGERING_ACTOR': 'marcel-tuinstra',
                    'GITHUB_SHA': MERGE, 'GITHUB_RUN_ID': '11', 'GITHUB_RUN_ATTEMPT': '2',
                    'GITHUB_REF': 'refs/pull/444/merge'}

    def select(self):
        return self.route.select(self.env, self.event, lambda path: self.fixture.response('GET', path))

    def test_only_exact_owner_pr_gets_the_dedicated_backend_labels(self):
        selected = self.select()
        self.assertEqual(selected['group'], 'sanctuary-trusted-verification')
        self.assertEqual(selected['labels'], self.fixture.job['labels'])
        self.assertNotIn('trusted-medium', selected['labels'])

    def test_forks_bots_unknown_actors_and_other_events_keep_hosted_without_api(self):
        for mutation in ['fork', 'bot', 'unknown', 'author', 'event', 'repo', 'branch', 'pr']:
            self.setUp()
            if mutation == 'fork': self.event['pull_request']['head']['repo']['fork'] = True
            elif mutation == 'bot': self.event['sender']['type'] = 'Bot'
            elif mutation == 'unknown': self.env['GITHUB_TRIGGERING_ACTOR'] = 'unknown'
            elif mutation == 'author': self.event['pull_request']['user']['login'] = 'unknown'
            elif mutation == 'event': self.env['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
            elif mutation == 'repo': self.env['GITHUB_REPOSITORY'] = 'Tuinstra-DEV/tracker'
            elif mutation == 'branch': self.event['pull_request']['head']['ref'] = 'another'
            elif mutation == 'pr': self.event['pull_request']['number'] = 445
            with self.subTest(mutation=mutation):
                self.assertEqual(self.route.select(self.env, self.event, mock.Mock(side_effect=AssertionError)), 'ubuntu-24.04')

    def test_stale_run_source_base_merge_and_immutable_pin_cannot_gain_trusted_route(self):
        for mutation in ['head', 'base', 'merge', 'pin', 'attempt', 'sender', 'protected']:
            self.setUp()
            if mutation == 'head': self.fixture.pr['head']['sha'] = 'f' * 40
            elif mutation == 'base': self.fixture.pr['base']['sha'] = 'f' * 40
            elif mutation == 'merge': self.fixture.merge['parents'][0]['sha'] = 'f' * 40
            elif mutation == 'pin': self.fixture.run['referenced_workflows'][0]['sha'] = 'f' * 40
            elif mutation == 'attempt': self.fixture.run['run_attempt'] = 3
            elif mutation == 'sender': self.fixture.run['actor']['login'] = 'unknown'
            elif mutation == 'protected': self.fixture.branch['protected'] = False
            with self.subTest(mutation=mutation):
                self.assertEqual(self.select(), 'ubuntu-24.04')

    def test_unavailable_api_fails_closed(self):
        with self.assertRaises(TimeoutError):
            self.route.select(self.env, self.event, mock.Mock(side_effect=TimeoutError))


if __name__ == '__main__':
    unittest.main()
