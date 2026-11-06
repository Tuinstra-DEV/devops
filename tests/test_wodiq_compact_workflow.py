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
        selection = {'schemaVersion': 1, 'jobs': [{'job-key': key, 'plan': plan} for key in ['wodiq-runtime-build','wodiq-management']]}
        def execute(p, s, release='false'):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'output'
                with patch.dict(os.environ, {'PLAN': json.dumps(p), 'SELECTION': json.dumps(s),
                                            'RELEASE_CANDIDATE': release, 'GITHUB_OUTPUT': str(output)}, clear=True):
                    exec(compile(script, str(WORKFLOW), 'exec'), {})
                return dict(line.split('=', 1) for line in output.read_text().splitlines())
        actual = execute(plan, selection)
        self.assertEqual(json.loads(actual['selected-jobs']), ['wodiq-runtime-build','wodiq-management'])
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

    def test_only_preflight_and_five_fixed_suites_exist(self):
        source = WORKFLOW.read_text()
        self.assertEqual(re.findall(r'^  ([a-z][a-z0-9-]*):$', source.split('jobs:\n', 1)[1], re.M),
                         ['preflight', 'wodiq-core', 'wodiq-runtime-build', 'wodiq-api',
                          'wodiq-management', 'wodiq-worker'])
        self.assertNotIn('tracker-', source)
        self.assertNotIn('gate-php', source)
        self.assertIn('npm run scan:secrets', source)
        self.assertIn('--release-candidate', source)
        self.assertIn('fetch-depth: 0', source)
        for key in ['core', 'runtime-build', 'api', 'management', 'worker']:
            block = source.split(f'  wodiq-{key}:\n', 1)[1].split('\n  wodiq-', 1)[0]
            self.assertRegex(block, r"runs-on: .*fromJSON\(needs.preflight.outputs.(?:medium|heavy)-runner\)")
            self.assertIn("if: needs.preflight.outputs.trusted == 'true'", block)
            self.assertIn("if: needs.preflight.outputs.trusted == 'false'", block)
            self.assertIn('trusted-bundle-trusted@7c284a1b4405d844e581ad369b754885ab2415cc', block)
            self.assertIn('trusted-bundle-hosted@7c284a1b4405d844e581ad369b754885ab2415cc', block)
            self.assertIn('route: ${{', block)

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
            self.assertEqual(json.loads(identity['medium-runner']), {'group': 'sanctuary-trusted-verification', 'labels': ['self-hosted', 'trusted-medium', 'sanctuary-wodiq-platform']})
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
