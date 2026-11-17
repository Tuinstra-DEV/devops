import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/reusable-tessera-hosted-verification.yml'


class TesseraHostedTests(unittest.TestCase):
    def workflow(self):
        return yaml.safe_load(WORKFLOW.read_text())

    def guard(self, changes=None, fork=False):
        repo = {'full_name': 'Tuinstra-DEV/sudoku-tessera'}
        event = {'repository': repo, 'pull_request': {'number': 181,
                 'head': {'sha': 'a' * 40, 'repo': {'full_name': 'fork/tessera'} if fork else repo},
                 'base': {'sha': 'b' * 40, 'repo': repo}}}
        with tempfile.TemporaryDirectory() as directory:
            event_path, output = Path(directory) / 'event', Path(directory) / 'output'
            event_path.write_text(json.dumps(event))
            env = {'GITHUB_REPOSITORY': repo['full_name'], 'GITHUB_EVENT_NAME': 'pull_request',
                   'GITHUB_REF': 'refs/pull/181/merge', 'GITHUB_SHA': 'c' * 40,
                   'GITHUB_EVENT_PATH': str(event_path), 'GITHUB_OUTPUT': str(output),
                   'PROFILE': 'heavy', 'JOB_KEY': 'tessera-flutter', 'RUNNER_ENVIRONMENT': 'github-hosted'}
            env.update(changes or {})
            source = self.workflow()['jobs']['verification']['steps'][0]['run']
            script = source.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
            with patch.dict(os.environ, env, clear=True):
                exec(compile(script, str(WORKFLOW), 'exec'), {'__name__': '__main__'})
            return dict(row.split('=', 1) for row in output.read_text().splitlines())

    def test_owner_and_untrusted_fork_prs_receive_only_hosted_proof_route(self):
        for fork in (False, True):
            out = self.guard(fork=fork)
            self.assertEqual(out['trusted'], 'false')
            self.assertEqual(out['source-sha'], 'c' * 40)
            self.assertEqual(out['head-sha'], 'a' * 40)

    def test_wrong_repository_event_contract_or_revision_fails_closed(self):
        for change in [{'GITHUB_REPOSITORY': 'Tuinstra-DEV/gate'}, {'GITHUB_EVENT_NAME': 'pull_request_target'},
                       {'JOB_KEY': 'gate-php'}, {'PROFILE': 'medium'}, {'GITHUB_REF': 'refs/heads/develop'},
                       {'GITHUB_SHA': 'invalid'}, {'RUNNER_ENVIRONMENT': 'self-hosted'}]:
            with self.subTest(change=change), self.assertRaises(SystemExit):
                self.guard(change)

    def test_fixed_tools_adapter_and_proof_keep_checks_intact_without_sanctuary(self):
        data = self.workflow()
        self.assertEqual(set(data['jobs']), {'verification'})
        job = data['jobs']['verification']
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertEqual(job['permissions'], {'contents': 'read'})
        self.assertEqual(job['steps'][-1]['id'], 'proof')
        self.assertNotIn('if', job['steps'][-1])
        steps = job['steps']
        self.assertTrue(any(s.get('run') == 'bash .github/ci/sanctuary-ci --strict tessera-flutter' for s in steps))
        flutter = next(s for s in steps if s.get('uses', '').startswith('subosito/flutter-action@'))
        self.assertEqual(str(flutter['with']['flutter-version']), '3.47.1')
        self.assertFalse(flutter['with']['cache'])
        for step in steps:
            if 'uses' in step:
                self.assertRegex(step['uses'], r'@[a-f0-9]{40}$')
            self.assertNotIn('continue-on-error', step)
        self.assertEqual(data[True]['workflow_call']['outputs']['trusted-proof']['value'], "${{ '' }}")
        self.assertNotIn('self-hosted', WORKFLOW.read_text())


if __name__ == '__main__':
    unittest.main()
