"""Execute the compact pilot's selection and fail-closed verdict contracts."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / '.github/workflows/reusable-product-ci.yml').read_text()


class ProductCiTests(unittest.TestCase):
    def matrix(self, keys, native='false', schema=1):
        script = textwrap.dedent(re.search(
            r"^          python3 - <<'PY'\n(.*?)^          PY$", SOURCE, re.M | re.S
        ).group(1))
        selection = {'schemaVersion': schema, 'jobs': [
            {'job-key': key, 'plan': {'quality': True}} for key in keys
        ]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            result = subprocess.run(['python3', '-c', script], env=os.environ | {
                'SELECTION': json.dumps(selection), 'NATIVE_DATABASE': native,
                'GITHUB_OUTPUT': str(output),
            }, capture_output=True, text=True)
            values = dict(line.split('=', 1) for line in output.read_text().splitlines()) if output.exists() else {}
        return result, values

    def test_selected_jobs_and_database_share_only_two_fixed_profiles(self):
        for keys in [[], ['gate-php'], ['gate-frontend-static'], ['gate-php', 'gate-frontend-static']]:
            for native in ['false', 'true']:
                with self.subTest(keys=keys, native=native):
                    result, values = self.matrix(keys, native)
                    if native == 'true' and 'gate-php' not in keys:
                        self.assertNotEqual(result.returncode, 0)
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(values['count'], str(len(keys)))
                    jobs = json.loads(values['matrix'])['include']
                    self.assertEqual([job['key'] for job in jobs], keys)
                    for job in jobs:
                        backend = job['key'] == 'gate-php'
                        self.assertEqual(job['name'], 'Backend CI' if backend else 'Frontend CI')
                        self.assertEqual(job['native'], backend and native == 'true')
                        self.assertEqual('pdo_mysql' in job['extensions'], backend and native == 'true')

    def test_invalid_selections_cannot_start_arbitrary_or_duplicate_jobs(self):
        for keys, native, schema in [(['tracker-backend-postgres'], 'false', 1),
                                     (['wodiq-core'], 'false', 1),
                                     (['gate-php', 'gate-php'], 'false', 1),
                                     (['gate-php'], 'unknown', 1),
                                     (['gate-php'], 'false', 2)]:
            with self.subTest(keys=keys, native=native, schema=schema):
                result, values = self.matrix(keys, native, schema)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(values, {})

    def test_verdict_rejects_failed_cancelled_skipped_or_missing_selected_work(self):
        verdict = SOURCE.split('  verdict:\n', 1)[1]
        script = textwrap.dedent(verdict.split('        run: |\n', 1)[1])
        for source in ['success', 'failure', 'cancelled', 'skipped']:
            for count in ['', '0', '1', '2', '3']:
                for verification in ['', 'success', 'failure', 'cancelled', 'skipped']:
                    with self.subTest(source=source, count=count, verification=verification):
                        result = subprocess.run(['bash', '-c', script], env=os.environ | {
                            'SOURCE': source, 'COUNT': count, 'VERIFICATION': verification,
                        }, capture_output=True)
                        expected = source == 'success' and (
                            (count == '0' and verification == 'skipped') or
                            (count in {'1', '2'} and verification == 'success')
                        )
                        self.assertEqual(result.returncode == 0, expected)

    def test_graph_has_no_generic_bundle_or_separate_database_job(self):
        jobs = SOURCE.split('jobs:\n', 1)[1]
        self.assertEqual(re.findall(r'^  ([a-z][a-z0-9-]*):$', jobs, re.M), ['source', 'verify', 'verdict'])
        self.assertIn('name: Bronselectie', SOURCE)
        self.assertIn('name: CI-verdict', SOURCE)
        self.assertIn('needs: [source, verify]\n    if: always()', SOURCE)
        self.assertIn('fail-fast: false', SOURCE)
        self.assertIn('if: matrix.native', SOURCE)
        self.assertNotIn('continue-on-error', SOURCE)
        self.assertNotIn('reusable-trusted-verification', SOURCE)
        self.assertNotIn('self-hosted', SOURCE)


if __name__ == '__main__':
    unittest.main()
