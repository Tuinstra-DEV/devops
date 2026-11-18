"""Permanent Gate admission keeps identity checks and separates release smoke."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

import ci_runner_manager as manager
from test_gate_backend_pilot import GateBackendPilotTests, HEAD, BASE, MERGE, REPO

PIN = 'd' * 40
CORE = 'Tuinstra-DEV/devops/.github/workflows/reusable-gate-ci.yml@' + PIN
RELEASE = 'Tuinstra-DEV/devops/.github/workflows/reusable-gate-release-ci.yml@' + PIN


class PermanentGateTests(unittest.TestCase):
    def setUp(self):
        self.f = GateBackendPilotTests()
        self.f.setUp()
        self.f.run['head_branch'] = 'feature/IN-999-example'
        self.f.pr['head']['ref'] = self.f.run['head_branch']
        self.f.pr['number'] = 555
        self.f.run['pull_requests'][0]['number'] = 555
        self.f.run['referenced_workflows'] = [{'path': CORE, 'sha': PIN}]
        self.f.policy['workflow_ref'] = CORE
        self.f.cfg['org_execution_roles'] = {'gate-backend': self.f.policy}
        self.f.job['labels'][1] = 'ci-gate-backend'
        self.route_path = Path(__file__).resolve().parents[2] / '.github/actions/gate-ci-trust/select_runner.py'

    def api(self, path):
        if path.endswith('/pulls/555'):
            return self.f.pr
        if path.endswith('/branches/main'):
            return {**self.f.branch, 'name': 'main'}
        return self.f.response('GET', path)

    def client(self):
        return self.f.client()

    def candidates(self, role='gate-backend'):
        client = self.client()
        with mock.patch.object(client, 'request', side_effect=lambda method, path, body=None: self.api(path)):
            return client.candidate_role_jobs(REPO, role)

    def route(self, enabled='true', role='gate-backend'):
        spec = importlib.util.spec_from_file_location('permanent_gate_route', self.route_path)
        route = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(route)
        env = {'GITHUB_REPOSITORY': REPO, 'GITHUB_EVENT_NAME': 'pull_request',
               'GITHUB_ACTOR': 'marcel-tuinstra', 'GITHUB_TRIGGERING_ACTOR': 'marcel-tuinstra',
               'GITHUB_SHA': MERGE, 'GITHUB_RUN_ID': '11', 'GITHUB_RUN_ATTEMPT': '2',
               'GITHUB_REF': 'refs/pull/555/merge', 'SANCTUARY_GATE_BACKEND_ENABLED': enabled,
               'GATE_CI_ROLE': role, 'GATE_CI_MODE': 'backend' if role == 'gate-backend' else 'release'}
        event = {'pull_request': copy.deepcopy(self.f.pr), 'repository': self.f.metadata,
                 'sender': {'login': 'marcel-tuinstra', 'type': 'User'}}
        return route.select(env, event, self.api)

    def release(self):
        self.f.run['head_branch'] = 'develop'
        self.f.pr['head']['ref'] = 'develop'
        self.f.pr['base']['ref'] = 'main'
        self.f.run['referenced_workflows'] = [{'path': RELEASE, 'sha': PIN}]

    def test_arbitrary_current_owner_develop_pr_is_admitted(self):
        candidate, = self.candidates()
        self.assertEqual(candidate['pr_number'], 555)
        self.assertEqual(candidate['execution_sha'], MERGE)
        self.assertEqual(self.route()['labels'][1], 'ci-gate-backend')

    def test_not_enabled_uses_hosted_without_any_api_calls(self):
        with mock.patch.object(self, 'api', side_effect=AssertionError('unexpected API')):
            self.assertEqual(self.route('false'), 'ubuntu-24.04')

    def test_bots_forks_unknown_authors_stale_heads_and_non_develop_bases_reject(self):
        for mutation in ['fork', 'bot', 'author', 'stale', 'base', 'pin', 'nested']:
            self.setUp()
            if mutation == 'fork': self.f.pr['head']['repo']['fork'] = True
            elif mutation == 'bot': self.f.run['actor']['type'] = 'Bot'
            elif mutation == 'author': self.f.pr['user']['login'] = 'unknown'
            elif mutation == 'stale': self.f.pr['head']['sha'] = 'f' * 40
            elif mutation == 'base': self.f.pr['base']['ref'] = 'other'
            elif mutation == 'pin': self.f.run['referenced_workflows'][0]['sha'] = 'f' * 40
            elif mutation == 'nested': self.f.run['referenced_workflows'] = [{'path': RELEASE, 'sha': PIN}]
            with self.subTest(mutation=mutation):
                self.assertEqual(self.candidates(), [])
                self.assertEqual(self.route(), 'ubuntu-24.04')

    def test_release_build_and_verify_are_separate_heavy_roles(self):
        for role, name in [('gate-release-build', 'release / Build kandidaat'), ('gate-release-verify', 'release / Verifieer kandidaat')]:
            self.setUp()
            self.release()
            self.f.cfg['org_execution_roles'][role] = {**self.f.policy, 'workflow_ref': RELEASE}
            self.f.job['labels'][1] = 'ci-' + role
            self.f.job['name'] = name
            with self.subTest(role=role):
                self.assertEqual(manager.EXECUTION_ROLES[role]['profile'], 'heavy')
                self.assertEqual(len(self.candidates(role)), 1)
                self.assertEqual(self.route(role=role)['labels'][1], 'ci-' + role)
                self.assertEqual(self.candidates(), [])
                self.assertEqual(self.route(), 'ubuntu-24.04')
                self.f.run['referenced_workflows'][0]['sha'] = 'f' * 40
                self.assertEqual(self.candidates(role), [])
                self.assertEqual(self.route(role=role), 'ubuntu-24.04')

    def test_release_roles_reject_non_release_and_nested_workflows(self):
        self.release()
        for role in ['gate-release-build', 'gate-release-verify']:
            self.f.cfg['org_execution_roles'][role] = {**self.f.policy, 'workflow_ref': RELEASE}
            self.f.job['labels'][1] = 'ci-' + role
            self.f.job['name'] = manager.EXECUTION_ROLES[role]['job_name'] if 'job_name' in manager.EXECUTION_ROLES[role] else 'release / Build kandidaat'
            self.f.pr['base']['ref'] = 'develop'
            self.assertEqual(self.candidates(role), [])
            self.assertEqual(self.route(role=role), 'ubuntu-24.04')

    def test_rechecks_current_source_before_jit(self):
        candidate, = self.candidates()
        client = self.client()
        self.f.pr['head']['sha'] = 'f' * 40
        with mock.patch.object(client, 'request', side_effect=lambda method, path, body=None: self.api(path)), \
                self.assertRaises(manager.RunnerError):
            client.verify_role_job(candidate, self.f.job)

    def test_backend_role_cannot_admit_frontend_or_legacy_labels(self):
        self.f.job['name'] = 'ci / Frontend CI'
        self.assertEqual(self.candidates(), [])
        self.f.job['name'] = 'ci / Backend CI'
        self.f.job['labels'].append('trusted-heavy')
        self.assertEqual(self.candidates(), [])
