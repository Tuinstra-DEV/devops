import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('selected_verdict', ROOT / '.github/actions/gate-ci-verdict/verify.py')
verdict = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verdict)


class SelectedVerdictTests(unittest.TestCase):
    def check(self, keys, jobs=None, source='success', aggregate=None, count=None):
        if jobs is None:
            jobs = [{'id': i + 1, 'name': verdict.NAMES[k], 'status': 'completed', 'conclusion': 'success', 'run_id': 11, 'run_attempt': 2, 'head_sha': 'a' * 40} for i, k in enumerate(keys)]
        return verdict.verify(source, aggregate or ('success' if keys else 'skipped'), str(len(keys)) if count is None else count, keys, {'total_count': len(jobs), 'jobs': jobs}, 11, 2, 'a' * 40)

    def test_empty_frontend_backend_and_full_selections(self):
        for keys in ([], ['gate-php'], ['gate-frontend-static'], list(verdict.NAMES)):
            self.assertEqual(len(self.check(keys)['verified']), len(keys))

    def test_missing_skipped_failed_cancelled_or_stale_selected_task_rejects(self):
        original = {'id': 1, 'name': verdict.NAMES['gate-php'], 'status': 'completed', 'conclusion': 'success', 'run_id': 11, 'run_attempt': 2, 'head_sha': 'a' * 40}
        for jobs in ([], [original, original], [{**original, 'conclusion': 'skipped'}], [{**original, 'conclusion': 'failure'}], [{**original, 'status': 'in_progress'}], [{**original, 'run_attempt': 1}], [{**original, 'head_sha': 'b' * 40}]):
            with self.subTest(jobs=jobs), self.assertRaises(ValueError): self.check(['gate-php'], jobs)

    def test_empty_selection_requires_successful_source_and_skipped_matrix(self):
        for source, aggregate, count in [('failure', 'skipped', '0'), ('success', 'failure', '0'), ('success', 'success', '0'), ('success', 'skipped', '1')]:
            with self.subTest(source=source, aggregate=aggregate), self.assertRaises(ValueError): self.check([], source=source, aggregate=aggregate, count=count)

    def test_unselected_created_task_is_not_a_green_padding_job(self):
        jobs = [{'name': verdict.NAMES['gate-php'], 'conclusion': 'success'}]
        with self.assertRaisesRegex(ValueError, 'unselected'): self.check([], jobs)
