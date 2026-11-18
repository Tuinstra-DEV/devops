"""Execute fixed graph selection/verdict and read-only activation planning."""
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
    def test_selective_and_empty_work_still_have_exactly_four_jobs(self):
        script = textwrap.dedent(re.search(r"^          python3 - <<'PY'\n(.*?)^          PY$", SOURCE, re.M | re.S).group(1))
        for keys, native, okay in [([], 'false', True), (['gate-php'], 'true', True),
                                   (['gate-frontend-static'], 'false', True),
                                   (['gate-frontend-static', 'gate-php'], 'true', True),
                                   ([], 'true', False), (['arbitrary'], 'false', False),
                                   (['gate-php', 'gate-php'], 'false', False)]:
            with self.subTest(keys=keys, native=native), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'output'
                result = subprocess.run(['python3', '-c', script], capture_output=True, text=True, env=os.environ | {
                    'SELECTION': json.dumps({'schemaVersion': 1, 'jobs': [{'job-key': k, 'plan': {'quality': True}} for k in keys]}),
                    'NATIVE_DATABASE': native, 'BACKEND_RUNNER': json.dumps({'group': 'sanctuary-trusted-verification', 'labels': ['self-hosted']}),
                    'GITHUB_OUTPUT': str(output)})
                self.assertEqual(result.returncode == 0, okay, result.stderr)
                if okay:
                    values = dict(line.split('=', 1) for line in output.read_text().splitlines())
                    jobs = json.loads(values['matrix'])['include']
                    self.assertEqual(values['count'], '2')
                    self.assertEqual([j['name'] for j in jobs], ['Frontend CI', 'Backend CI'])
                    for job in jobs:
                        self.assertEqual(job['selected'], job['key'] in keys)
                        if not job['selected']:
                            self.assertEqual(job['runner'], 'ubuntu-24.04')

    def test_failed_or_skipped_verification_never_passes_verdict(self):
        script = textwrap.dedent(SOURCE.split('  verdict:\n')[1].split('        run: |\n')[1])
        for source, count, verification, okay in [('success', '2', 'success', True),
                ('success', '0', 'skipped', False), ('success', '2', 'skipped', False),
                ('failure', '2', 'success', False), ('success', '2', 'failure', False),
                ('success', '2', 'cancelled', False)]:
            result = subprocess.run(['bash', '-c', script], capture_output=True, env=os.environ | {
                'SOURCE': source, 'COUNT': count, 'VERIFICATION': verification})
            self.assertEqual(result.returncode == 0, okay)

    def test_no_generic_bundle_or_extra_database_or_trusted_jobs(self):
        self.assertEqual(re.findall(r'^  ([a-z][a-z0-9-]*):$', SOURCE.split('jobs:\n', 1)[1], re.M), ['source', 'verify', 'verdict'])
        verify = SOURCE.split('  verify:\n')[1].split('    steps:')[0]
        self.assertNotIn('    if:', verify)
        self.assertIn('runs-on: ${{ matrix.runner }}', verify)
        self.assertIn('if: matrix.native', SOURCE)
        self.assertNotIn('reusable-trusted-verification', SOURCE)
        self.assertNotIn('continue-on-error', SOURCE)
        self.assertIn("force-full: ${{ github.event_name != 'pull_request' || steps.event.outputs.release == 'true' }}", SOURCE)

    def test_release_proves_quality_before_current_attempt_artifact(self):
        browser = (ROOT / '.github/workflows/reusable-gate-release-browser.yml').read_text()
        self.assertLess(browser.index('Require current-attempt compact quality'), browser.index('Download immutable frontend output'))
        self.assertIn('attempts/$GITHUB_RUN_ATTEMPT/jobs?per_page=100', browser)
        self.assertIn('data[\'total_count\'] != len(jobs)', browser)
        self.assertIn('ci / Backend CI', browser)
        self.assertIn('ci-execution-sha-${{ github.sha }}', browser)
        self.assertIn('node scripts/visual-smoke.mjs', browser)
        self.assertIn('node scripts/visual-authenticated.mjs', browser)
        self.assertIn('gate-frontend-output-${{ github.run_id }}-${{ github.run_attempt }}', browser)

    def test_planner_preserves_existing_policy_and_does_not_apply(self):
        spec = importlib.util.spec_from_file_location('gate_plan', ROOT / 'scripts/plan-gate-compact-admission.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        group = {'id': 4, 'name': 'sanctuary-trusted-verification', 'visibility': 'selected',
                 'allows_public_repositories': False, 'restricted_to_workflows': True, 'selected_workflows': ['old@' + 'a' * 40]}
        repos = {'total_count': 1, 'repositories': [{'id': 1273386924, 'full_name': 'Tuinstra-DEV/gate'}]}
        pin = json.loads((ROOT / 'docs/runner/proposals/dev54-gate-permanent/manager-manifest.json').read_text())['workflow_pin']
        result = module.plan(group, repos, pin)
        self.assertEqual(result['runner_group_before'], group)
        self.assertEqual(result['runner_group_after']['selected_workflows'][0], group['selected_workflows'][0])
        self.assertEqual(len(result['runner_group_after']['selected_workflows']), 3)
        self.assertTrue(result['read_only_plan'])
        self.assertEqual(result['repository_ids_unchanged'], [1273386924])
        for invalid in ['main', 'b' * 39]:
            with self.assertRaises(ValueError): module.plan(group, repos, invalid)
