"""Execute the real selector, admission plan, and fixed release graph contract."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / '.github/workflows/reusable-gate-ci.yml').read_text()

class GateCompactTests(unittest.TestCase):
    def test_only_selected_jobs_are_created(self):
        script = textwrap.dedent(re.search(r"^          python3 - <<'PY'\n(.*?)^          PY$", SOURCE, re.M | re.S).group(1))
        for keys, native, php, frontend, api, okay in [
            ([], 'false', False, False, False, True),
            (['gate-php'], 'true', True, False, False, True),
            (['gate-frontend-static'], 'false', False, True, False, True),
            (['gate-frontend-static', 'gate-php'], 'true', True, True, True, True),
            ([], 'true', True, False, False, False),
            ([], 'false', True, False, False, False),
            (['gate-php'], 'false', True, False, True, False),
            (['gate-php', 'gate-php'], 'false', True, False, False, False),
            (['arbitrary'], 'false', True, False, False, False)]:
            with self.subTest(keys=keys, native=native), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / 'output'
                result = subprocess.run(['python3', '-c', script], capture_output=True, text=True, env=os.environ | {
                    'SELECTION': json.dumps({'schemaVersion': 1, 'jobs': [{'job-key': k, 'plan': {'quality': True}} for k in keys]}),
                    'NATIVE_DATABASE': native, 'BACKEND_RUNNER': '"ubuntu-24.04"', 'GITHUB_OUTPUT': str(out),
                    'RUN_PHP': str(php).lower(), 'RUN_FRONTEND_STATIC': str(frontend).lower(), 'RUN_API_CONTRACT': str(api).lower()})
                self.assertEqual(result.returncode == 0, okay, result.stderr)
                if okay:
                    values = dict(line.split('=', 1) for line in out.read_text().splitlines())
                    jobs = json.loads(values['matrix'])['include']
                    self.assertEqual(int(values['count']), len(keys))
                    self.assertEqual({j['key'] for j in jobs}, set(keys))
                    self.assertEqual(json.loads(values['selected']), keys)
                    self.assertFalse(any('selected' in j for j in jobs))

    def test_no_generic_bundle_or_extra_database_or_padding(self):
        self.assertEqual(re.findall(r'^  ([a-z][a-z0-9-]*):$', SOURCE.split('jobs:\n', 1)[1], re.M), ['source', 'verify', 'verdict'])
        self.assertIn("if: needs.source.outputs.count != '0'", SOURCE)
        self.assertIn('runs-on: ${{ matrix.runner }}', SOURCE)
        self.assertIn('if: matrix.native', SOURCE)
        self.assertIn('/gate-ci-verdict@', SOURCE)
        self.assertNotIn('continue-on-error', SOURCE)
        self.assertNotIn('reusable-trusted-verification', SOURCE)

    def test_release_builds_once_then_verifies_without_rebuild(self):
        wf = json.loads(subprocess.check_output(['ruby', '-ryaml', '-rjson', '-e', 'puts JSON.generate(YAML.safe_load(File.read(ARGV[0]), aliases: true))', str(ROOT / '.github/workflows/reusable-gate-release-ci.yml')], text=True))
        self.assertEqual(list(wf['jobs']), ['build', 'verify'])
        self.assertEqual(wf['jobs']['build']['name'], 'Build kandidaat')
        self.assertEqual(wf['jobs']['verify']['name'], 'Verifieer kandidaat')
        self.assertEqual(wf['jobs']['verify']['needs'], 'build')
        build = wf['jobs']['build']['steps']; verify = wf['jobs']['verify']['steps']
        self.assertEqual(sum('release-candidate.mjs build' in s.get('run', '') for s in build), 1)
        self.assertFalse(any('buildx' in s.get('uses', '') or 'mjs build' in s.get('run', '') for s in verify))
        self.assertTrue(any('measure-release-resources.py' in step.get('run', '') for step in build))
        self.assertTrue(any('release-promotion-dry-run.mjs' in step.get('run', '') for step in verify))
        self.assertEqual(sum('trivy-action@' in s.get('uses', '') for s in verify), 8)
        self.assertLess(next(i for i,s in enumerate(verify) if s.get('id') == 'trust'), next(i for i,s in enumerate(verify) if s.get('id') == 'receive'))
        self.assertTrue(any('prepare-decision' in s.get('run', '') for s in verify))
        self.assertNotIn('--user 1000:1000', str(verify))
        self.assertTrue(any('gate-native-mariadb-fixture@' in s.get('uses', '') for s in verify))
        for phase in ['quality', 'paired-scans', 'browser', 'independent', 'finalize']:
            self.assertTrue(any('release-candidate.mjs '+phase in s.get('run', '') for s in verify))

    def test_only_release_verify_can_read_private_check_results(self):
        wf = json.loads(subprocess.check_output(['ruby', '-ryaml', '-rjson', '-e', 'puts JSON.generate(YAML.safe_load(File.read(ARGV[0]), aliases: true))', str(ROOT / '.github/workflows/reusable-gate-release-ci.yml')], text=True))
        baseline = {'contents': 'read', 'actions': 'read', 'pull-requests': 'read'}
        self.assertEqual(wf['permissions'], baseline)
        self.assertEqual(wf['jobs']['build']['permissions'], baseline)
        self.assertEqual(wf['jobs']['verify']['permissions'], baseline | {'checks': 'read'})

    def test_planner_preserves_policy_and_does_not_apply(self):
        spec = importlib.util.spec_from_file_location('gate_plan', ROOT / 'scripts/plan-gate-compact-admission.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        group = {'id': 4, 'name': 'sanctuary-trusted-verification', 'visibility': 'selected', 'allows_public_repositories': False,
                 'restricted_to_workflows': True, 'selected_workflows': ['old@'+'a'*40]}
        repos = {'total_count': 1, 'repositories': [{'id': 1273386924, 'full_name': 'Tuinstra-DEV/gate'}]}
        pin = json.loads((ROOT / 'docs/runner/proposals/dev54-gate-permanent/manager-manifest.json').read_text())['workflow_pin']
        result = module.plan(group, repos, pin)
        self.assertEqual(result['runner_group_before'], group)
        self.assertEqual(len(result['runner_group_after']['selected_workflows']), 3)
        self.assertEqual(len(result['merge_into_existing_org_execution_roles']), 3)
        self.assertTrue(result['read_only_plan'])
