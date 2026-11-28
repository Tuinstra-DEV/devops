"""WODIQ has one admitted route per fixed suite, with unchanged trust checks."""
import json
from pathlib import Path
import re
import subprocess
import unittest
import tempfile
from unittest.mock import patch

from tests import test_trusted_verification_workflow as legacy

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/reusable-trusted-verification.yml'
# CI speed step 2 job topology (wodiq-platform scripts/ci/bundle-selection.mjs).
KEYS = ['wodiq-core', 'wodiq-runtime-build', 'wodiq-migration', 'wodiq-api-unit-1', 'wodiq-api-unit-2',
        'wodiq-api-isolation', 'wodiq-api-c310', 'wodiq-api-proof-coach', 'wodiq-api-proof-other',
        'wodiq-management', 'wodiq-management-personas', 'wodiq-worker']
RUNTIME = {'wodiq-api-proof-coach': 'api', 'wodiq-api-proof-other': 'api', 'wodiq-management': 'management',
           'wodiq-management-personas': 'management', 'wodiq-worker': 'worker'}


class WodiqCompactTests(unittest.TestCase):
    def test_plan_contract_rejects_missing_suites_unknown_keys_and_release_narrowing(self):
        import copy
        import hashlib
        import os
        import textwrap
        script = textwrap.dedent(re.search(r"^          python3 - <<'PLAN'\n(.*?)^          PLAN$", WORKFLOW.read_text(), re.M | re.S).group(1))
        plan = {'version': 3, **dict.fromkeys(['core','migration','api','management','worker','client'], False),
                'builds': {'api': True, 'management': True, 'worker': False},
                'proofs': dict.fromkeys(['all','api','management','worker','activity','coach','managementAuth','garmin'], False)}
        plan['management'] = plan['proofs']['management'] = True
        selection = {'schemaVersion': 1, 'jobs': [{'job-key': key, 'plan': plan} for key in ['wodiq-runtime-build','wodiq-management','wodiq-management-personas']]}
        def execute(p, s, release='false'):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'output'
                with patch.dict(os.environ, {'PLAN': json.dumps(p), 'SELECTION': json.dumps(s),
                                            'RELEASE_CANDIDATE': release, 'GITHUB_OUTPUT': str(output)}, clear=True):
                    exec(compile(script, str(WORKFLOW), 'exec'), {})
                return dict(line.split('=', 1) for line in output.read_text().splitlines())
        actual = execute(plan, selection)
        self.assertEqual(json.loads(actual['selected-jobs']), ['wodiq-runtime-build','wodiq-management','wodiq-management-personas'])
        self.assertEqual(actual['selection-sha256'], hashlib.sha256(json.dumps(selection, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
        bad_key = copy.deepcopy(selection)
        bad_key['jobs'][0]['job-key'] = 'arbitrary-command'
        for bad in [bad_key, {'schemaVersion': 1, 'jobs': selection['jobs'][1:]},
                    {'schemaVersion': 1, 'jobs': selection['jobs'][::-1]}]:
            with self.subTest(bad=bad), self.assertRaises(SystemExit): execute(plan, bad)
        with self.assertRaises(SystemExit): execute(plan, selection, 'true')
        malformed = copy.deepcopy(plan)
        malformed['builds']['api'] = False
        with self.assertRaises(SystemExit): execute(malformed, selection)

    def test_only_preflight_and_the_twelve_fixed_step2_suites_exist(self):
        source = WORKFLOW.read_text()
        self.assertEqual(re.findall(r'^  ([a-z][a-z0-9-]*):$', source.split('jobs:\n', 1)[1], re.M),
                         ['preflight', *KEYS])
        self.assertNotIn('CI_API_SHARD', source)  # devops main's unused 36d8ad15 matrix stays out
        self.assertNotIn('matrix:', source)  # one job per key: bundle results are per job, never a collapsed matrix
        self.assertNotIn('tracker-', source)
        self.assertNotIn('gate-php', source)
        self.assertNotIn('self-hosted', source)
        self.assertNotIn('sanctuary', source)
        self.assertIn('npm run scan:secrets', source)
        self.assertIn('--release-candidate', source)
        self.assertIn('fetch-depth: 0', source)
        for key in KEYS:
            block = source.split(f'  {key}:\n', 1)[1].split('\n  wodiq-', 1)[0]
            self.assertIn(f'job-key: {key}\n', block)
            self.assertIn(f"contains(fromJSON(needs.preflight.outputs.selected-jobs || '[]'), '{key}')", block)
            if key in RUNTIME:
                self.assertIn('needs: [preflight, wodiq-runtime-build]', block)
                self.assertIn("needs.wodiq-runtime-build.outputs.verified == 'true'", block)
                self.assertIn(f'runtime-download: ${{{{ fromJSON(needs.preflight.outputs.plan).builds.{RUNTIME[key]} }}}}', block)
            elif key != 'wodiq-runtime-build':
                self.assertIn('needs: preflight\n', block)
                self.assertIn("runtime-download: 'false'", block)
            self.assertRegex(block, r"runs-on: .*fromJSON\(needs.preflight.outputs.(?:medium|heavy)-runner\)")
            self.assertIn("if: needs.preflight.outputs.trusted == 'true'", block)
            self.assertIn("if: needs.preflight.outputs.trusted == 'false'", block)
            self.assertIn('trusted-bundle-trusted@7c284a1b4405d844e581ad369b754885ab2415cc', block)
            self.assertIn('trusted-bundle-hosted@7c284a1b4405d844e581ad369b754885ab2415cc', block)
            self.assertIn('route: ${{', block)

    def test_preflight_selection_matches_the_platform_bundle_selection(self):
        # Fixture generated by wodiq-platform scripts/ci/bundle-selection.mjs: the
        # preflight must accept exactly that ordered selection for every plan.
        import os
        import textwrap
        script = textwrap.dedent(re.search(r"^          python3 - <<'PLAN'\n(.*?)^          PLAN$", WORKFLOW.read_text(), re.M | re.S).group(1))
        cases = json.loads((Path(__file__).parent / 'fixtures' / 'wodiq-step2-selection.json').read_text())['cases']
        self.assertGreaterEqual(len(cases), 9)
        for case in cases:
            selection = {'schemaVersion': 1, 'jobs': [{'job-key': key, 'plan': case['plan']} for key in case['keys']]}
            release = 'true' if case['plan']['proofs']['all'] else 'false'
            with self.subTest(paths=case['paths']), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'output'
                with patch.dict(os.environ, {'PLAN': json.dumps(case['plan']), 'SELECTION': json.dumps(selection),
                                            'RELEASE_CANDIDATE': release, 'GITHUB_OUTPUT': str(output)}, clear=True):
                    exec(compile(script, str(WORKFLOW), 'exec'), {})
                lines = dict(line.split('=', 1) for line in output.read_text().splitlines())
                self.assertEqual(json.loads(lines['selected-jobs']), case['keys'])

    def test_identity_preserves_exact_attempt_pin_current_head_and_merge_checks(self):
        # Execute the existing GitHub identity negatives against the NEW entrypoint.
        case = legacy.TrustedVerificationWorkflowTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        def script():
            match = re.search(r"^          python3 - <<'IDENTITY'\n(.*?)^          IDENTITY$", WORKFLOW.read_text(), re.M | re.S)
            import textwrap
            return textwrap.dedent(match.group(1))
        with patch.object(legacy, 'identity_script', script):
            identity = case.execute_identity()
            self.assertEqual(identity['trusted'], 'true')
            self.assertEqual(json.loads(identity['medium-runner']), 'ubuntu-24.04')
            self.assertEqual(json.loads(identity['heavy-runner']), 'ubuntu-24.04')
            case.env['GITHUB_ACTOR'] = 'untrusted-user'
            identity = case.execute_identity()
            self.assertEqual(identity['trusted'], 'false')
            self.assertEqual(json.loads(identity['heavy-runner']), 'ubuntu-24.04')
            case.env['GITHUB_ACTOR'] = 'marcel-tuinstra'
            case.run['run_attempt'] = 2
            with self.assertRaises(SystemExit):
                case.execute_identity()
            case.run['run_attempt'] = 1
            case.pr['head']['sha'] = legacy.BASE
            with self.assertRaises(SystemExit):
                case.execute_identity()

    def test_previous_multi_project_workflow_fixture_remains_exact(self):
        import hashlib
        fixture = ROOT / 'tests/fixtures/trusted-verification-before-wodiq-compact.yml'
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), '38c01851c4c53a17c04b01aac405a8bd4e9dd3fe2292bad036934385e3883b69')

    def test_yaml_parses(self):
        result = subprocess.run(['ruby', '-e', "require 'yaml'; YAML.safe_load(File.read(ARGV[0]), aliases: true)", str(WORKFLOW)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
