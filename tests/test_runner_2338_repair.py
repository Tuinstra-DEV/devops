"""Bounded checks for the operator's immutable image repair, with no host mutations."""
import importlib.util
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('runner2338', ROOT / 'scripts/repair-runner-image-2338.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class RunnerRepairTests(unittest.TestCase):
    def test_guest_program_is_valid_shell_and_reviewed_repair_remains_immutable(self):
        result = subprocess.run(['bash', '-n'], input=repair.GUEST, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # This one-purpose repair and its embedded contract are the reviewed
        # 5 November artifact. Later Packer requirements (such as PDO MySQL)
        # must not silently expand that historical runner-only repair.
        self.assertEqual(hashlib.sha256((ROOT / 'scripts/repair-runner-image-2338.py').read_bytes()).hexdigest(),
                         '8ab894681f9afcc3eb739b12411bd6d1b4f97a67f6f92cafefcd613aa3d50333')

    def test_active_domain_blocks_image_rotation(self):
        with patch.object(repair, 'run', return_value='sanctuary-ci-gh-123'):
            with self.assertRaisesRegex(RuntimeError, 'runner-domain-remains'):
                repair.idle()

    def test_atomic_link_preserves_existing_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'current'
            link.write_text('existing work')
            with self.assertRaisesRegex(RuntimeError, 'refusing-non-symlink'):
                repair.atomic_link(link, repair.OLD)
            self.assertEqual(link.read_text(), 'existing work')

    def test_atomic_link_rotates_and_retains_previous_image(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / 'current'
            previous = Path(directory) / 'previous'
            current.symlink_to(repair.OLD)
            repair.atomic_link(previous, repair.OLD)
            repair.atomic_link(current, '/new-image')
            self.assertEqual(str(current.readlink()), '/new-image')
            self.assertEqual(previous.readlink(), repair.OLD)

    def test_failed_rotation_leaves_admission_paused(self):
        with tempfile.TemporaryDirectory() as directory:
            drain = Path(directory) / 'manager.service.d/drain.conf'
            def command(args, **kwargs):
                if '--property=Type' in args:
                    return 'oneshot'
                if '--property=Restart' in args:
                    return 'no'
                return ''
            with patch.object(repair, 'DRAIN', drain), patch.object(repair, 'run', side_effect=command), \
                    patch.object(repair, 'idle'), patch('builtins.open', unittest.mock.mock_open()), patch.object(repair.fcntl, 'flock') :
                with self.assertRaisesRegex(RuntimeError, 'injected-rotation-failure'):
                    with repair.drained():
                        raise RuntimeError('injected-rotation-failure')
            self.assertEqual(drain.read_text(), repair.DRAIN_TEXT)

    def test_github_path_rejects_unrelated_repository_or_endpoint(self):
        for path in ('repos/other/repo/actions/runs/1/jobs?per_page=100',
                     'orgs/Tuinstra-DEV/actions/runner-groups/4'):
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, 'github-path'):
                repair.github(path)

class SmokeTests(unittest.TestCase):
    def smoke_fixture(self, runner, actual_job):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            data = {'lease': 'gh-1', 'repo': 'Tuinstra-DEV/notify', 'runner_id': 42,
                    'runner_name': 'sanctuary-1', 'trigger_run_id': 10,
                    'actual_job_id': 99, 'actual_run_id': 20, 'scope': 'organization'}
            (state / 'lease-gh-1.json').write_text(__import__('json').dumps(data))
            target = repair.ROOT / ('ubuntu-24.04-runner-' + 'a' * 64 + '.qcow2')
            def api(path):
                if path.startswith('orgs/'):
                    return runner
                if '/actions/jobs/' in path:
                    return actual_job
                return {'jobs': []}
            with patch.object(repair, 'STATE', state), patch.object(repair, 'run', return_value=str(target)), \
                    patch.object(repair, 'github', side_effect=api), patch.object(repair.time, 'sleep'), \
                    patch.object(repair.time, 'monotonic', side_effect=[0, 1, 601]):
                repair.smoke(target)

    def test_different_actual_run_is_accepted_with_exact_runner_identity(self):
        self.smoke_fixture({'id': 42, 'name': 'sanctuary-1', 'status': 'online', 'busy': False},
                           {'id': 99, 'run_id': 20, 'runner_id': 42,
                            'runner_name': 'sanctuary-1', 'started_at': '2026-11-05T10:00:00Z'})

    def test_online_busy_is_accepted_without_trigger_run_assignment(self):
        self.smoke_fixture({'id': 42, 'name': 'sanctuary-1', 'status': 'online', 'busy': True}, None)

    def test_reused_runner_name_with_different_numeric_id_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'no-verified-online-runner'):
            self.smoke_fixture({'id': 43, 'name': 'sanctuary-1', 'status': 'online', 'busy': True},
                               {'id': 99, 'run_id': 20, 'runner_id': 43,
                                'runner_name': 'sanctuary-1', 'started_at': '2026-11-05T10:00:00Z'})


if __name__ == '__main__':
    unittest.main()
