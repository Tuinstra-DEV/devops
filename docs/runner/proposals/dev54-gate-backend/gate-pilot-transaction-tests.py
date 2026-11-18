import importlib.util
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name('gate-pilot-transaction.py')
SPEC = importlib.util.spec_from_file_location('gate_pilot_transaction', SCRIPT)
transaction = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = transaction
SPEC.loader.exec_module(transaction)


class FakeGitHub:
    def __init__(self, *, compact_status='in_progress', compact_jobs=None,
                 baseline_status='in_progress', baseline_jobs=None, pull_head=None,
                 parent_shas=None):
        self.compact_status = compact_status
        self.baseline_status = baseline_status
        self.compact_jobs = compact_jobs
        self.baseline_jobs = baseline_jobs
        self.pull_head = pull_head or transaction.HEAD_SHA
        self.parent_shas = parent_shas or [transaction.HEAD_SHA, transaction.BASE_SHA]
        self.requests = []
        self.run_records = {
            419883688: self.run(419883688, 5101, self.compact_status),
            298278418: self.run(298278418, 5102, self.baseline_status),
        }

    @staticmethod
    def run(workflow_id, run_id, status):
        path, name, _job = transaction.WORKFLOWS[workflow_id]
        return {'id': run_id, 'workflow_id': workflow_id, 'event': 'pull_request',
                'head_sha': transaction.HEAD_SHA, 'head_branch': 'chore/DEV-54-compact-ci-pilot',
                'path': path + '@refs/pull/444/merge', 'name': name, 'run_attempt': 1,
                'status': status, 'conclusion': 'success' if status == 'completed' else None,
                'created_at': '2026-11-17T22:00:00Z', 'pull_requests': [{'number': 444}]}

    def request(self, method, path, body=None):
        self.requests.append((method, path, body))
        if path.endswith('/pulls/444'):
            return {'state': 'open', 'head': {'sha': self.pull_head, 'ref': 'chore/DEV-54-compact-ci-pilot'},
                    'base': {'sha': transaction.BASE_SHA, 'ref': 'develop'},
                    'merge_commit_sha': 'c' * 40}
        if '/git/commits/' in path:
            return {'sha': 'c' * 40, 'parents': [{'sha': value} for value in self.parent_shas]}
        if path.endswith('/actions/runs/5101'):
            return self.run_records[419883688]
        if path.endswith('/actions/runs/5102'):
            return self.run_records[298278418]
        if '/workflows/419883688/runs?' in path:
            value = self.run_records[419883688]
            return {'workflow_runs': value if isinstance(value, list) else [value]}
        if '/workflows/298278418/runs?' in path:
            value = self.run_records[298278418]
            return {'workflow_runs': value if isinstance(value, list) else [value]}
        if path.endswith('/5101/jobs?per_page=100'):
            return {'jobs': [] if self.compact_jobs is None else self.compact_jobs}
        if path.endswith('/5102/jobs?per_page=100'):
            return {'jobs': [] if self.baseline_jobs is None else self.baseline_jobs}
        raise AssertionError(f'unexpected API request {method} {path}')


class WatchGitHub:
    def __init__(self, *, publish=True, fail_source=False, assign_runner=True):
        self.publish = publish
        self.fail_source = fail_source
        self.assign_runner = assign_runner
        self.pr_reads = 0
        self.round = 0
        self.group_before = json.loads((Path(__file__).with_name('group-before.json')).read_text())
        self.group_after = json.loads((Path(__file__).with_name('group-after.json')).read_text())
        self.group = {'id': 4, 'name': 'sanctuary-trusted-verification', 'visibility': 'selected',
                      'default': False, 'inherited': False, 'allows_public_repositories': False,
                      'restricted_to_workflows': True,
                      'selected_workflows': list(self.group_after['selected_workflows'])}
        self.repositories = json.loads((Path(__file__).with_name('repositories-before.json')).read_text())
        self.repo_api = {'total_count': len(self.repositories), 'repositories': [
            {'full_name': name, 'id': repo_id, 'private': True}
            for name, repo_id in self.repositories.items()]}

    def _run(self, workflow_id, run_id):
        path, name, _job = transaction.WORKFLOWS[workflow_id]
        status = ('queued' if self.round == 1 else 'in_progress' if self.round < 4 else 'completed')
        conclusion = None if status != 'completed' else ('failure' if self.fail_source else 'success')
        return {'id': run_id, 'workflow_id': workflow_id, 'event': 'pull_request',
                'head_sha': transaction.HEAD_SHA, 'head_branch': 'chore/DEV-54-compact-ci-pilot',
                'path': path + '@refs/pull/444/merge', 'name': name, 'run_attempt': 1,
                'status': status, 'conclusion': conclusion,
                'created_at': '2026-11-17T22:00:00Z', 'pull_requests': [{'number': 444}]}

    def _job_rows(self, workflow_id):
        if self.round == 1:
            return []
        if workflow_id == 419883688:
            if self.round == 2:
                return [compact_job(status='queued')]
            assigned = self.assign_runner
            return [compact_job(status='completed' if self.round >= 4 else 'in_progress',
                                runner_id=1825 if assigned else 0,
                                runner_name='sanctuary-6101' if assigned else None,
                                group_id=4 if assigned else None,
                                group_name='sanctuary-trusted-verification' if assigned else None,
                                labels=['self-hosted', 'ci-gate-backend-pilot', 'sanctuary-gate']
                                if assigned else [])]
        return [baseline_job('completed' if self.round >= 4 else 'in_progress')]

    def request(self, method, path, body=None):
        if path.endswith('/pulls/444'):
            self.pr_reads += 1
            head = transaction.HEAD_SHA if self.publish and self.pr_reads >= 2 else transaction.PREACTIVATION_HEAD_SHA
            return {'state': 'open', 'head': {'sha': head, 'ref': 'chore/DEV-54-compact-ci-pilot'},
                    'base': {'sha': transaction.BASE_SHA, 'ref': 'develop'},
                    'merge_commit_sha': 'c' * 40}
        if '/git/commits/' in path:
            parent = transaction.HEAD_SHA if self.publish and self.pr_reads >= 2 else transaction.PREACTIVATION_HEAD_SHA
            return {'sha': 'c' * 40, 'parents': [{'sha': parent}, {'sha': transaction.BASE_SHA}]}
        if path.endswith('/workflows/419883688/runs?event=pull_request&per_page=100'):
            self.round += 1
            return {'workflow_runs': [self._run(419883688, 5101)] if self.publish else []}
        if path.endswith('/workflows/298278418/runs?event=pull_request&per_page=100'):
            return {'workflow_runs': [self._run(298278418, 5102)] if self.publish else []}
        if path.endswith('/actions/runs/5101/jobs?per_page=100'):
            return {'jobs': self._job_rows(419883688)}
        if path.endswith('/actions/runs/5102/jobs?per_page=100'):
            return {'jobs': self._job_rows(298278418)}
        if path.endswith('/actions/runs/5101'):
            self.round = min(4, self.round + 1)
            return self._run(419883688, 5101)
        if path.endswith('/actions/runs/5102'):
            return self._run(298278418, 5102)
        if path == transaction.GROUP_PATH and method == 'GET':
            return dict(self.group)
        if path == transaction.GROUP_PATH and method == 'PATCH':
            self.group = dict(self.group, selected_workflows=list(body['selected_workflows']))
            return self.group
        if path == transaction.GROUP_PATH + '/repositories?per_page=100&page=1':
            return self.repo_api
        raise AssertionError(f'unexpected API request {method} {path}')


def compact_job(*, status='queued', runner_id=0, runner_name=None, group_id=None,
                group_name=None, labels=None):
    return {'id': 6101, 'name': 'ci / Backend CI', 'status': status,
            'conclusion': 'success' if status == 'completed' else None,
            'runner_id': runner_id, 'runner_name': runner_name,
            'runner_group_id': group_id, 'runner_group_name': group_name,
            'labels': labels or []}


def baseline_job(status='queued'):
    return {'id': 6201,
            'name': 'lint / gate-quality / gate-php trusted verification',
            'status': status, 'conclusion': 'success' if status == 'completed' else None,
            'runner_id': 0, 'runner_name': None, 'runner_group_id': None,
            'runner_group_name': None, 'labels': ['ubuntu-latest']}


class GatePilotTransactionTests(unittest.TestCase):
    def test_candidate_readiness_requires_current_vm_proof_and_readonly_php_probe(self):
        class ProbeBlocked(Exception):
            code, http_status = 'candidate_isolated_validation_evidence_differs', None

        checker = SimpleNamespace(
            Blocked=ProbeBlocked,
            gate_candidate_image=mock.Mock(return_value=(transaction.CANDIDATE_SOURCE,
                                                        {'sha256': transaction.CANDIDATE_HASH})),
            candidate_image_php_check=mock.Mock(return_value={'pdo_mysql': True}),
        )
        transaction.verify_candidate_readiness(transaction.PACKAGE, checker)
        checker.gate_candidate_image.assert_called_once_with(transaction.PACKAGE)
        checker.candidate_image_php_check.assert_called_once_with(transaction.CANDIDATE_SOURCE)
        checker.gate_candidate_image.side_effect = ProbeBlocked()
        checker.candidate_image_php_check.reset_mock()
        with self.assertRaises(transaction.Blocked) as caught:
            transaction.verify_candidate_readiness(transaction.PACKAGE, checker)
        self.assertEqual(caught.exception.code, ProbeBlocked.code)
        checker.candidate_image_php_check.assert_not_called()

    def run_watch_scenario(self, *, publish=True, fail_source=False, assign_runner=True,
                           idle_races=(), hup=False):
        client = WatchGitHub(publish=publish, fail_source=fail_source, assign_runner=assign_runner)
        new_module = SimpleNamespace(GitHubClient=lambda *_args, **_kwargs: client)
        statuses = []
        clock = {'now': 0.0}
        empty_calls = {'count': 0}

        def ensure_empty():
            empty_calls['count'] += 1
            if empty_calls['count'] in idle_races:
                raise transaction.Blocked('runner_window_not_empty')
            return {}

        def monotonic():
            return clock['now']

        def sleep(seconds):
            clock['now'] += seconds
            if hup and clock['now'] >= 3 and transaction.SIGNAL_CONTROL['requested'] is None:
                transaction.SIGNAL_CONTROL['requested'] = transaction.signal.SIGHUP

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'backup'
            backup.mkdir()
            (backup / 'group-before.json').write_text(json.dumps(client.group_before))
            (backup / 'group-after.json').write_text(json.dumps(client.group_after))
            (backup / 'repositories-before.json').write_text(json.dumps(client.repositories))
            journal = root / 'active-transaction.json'
            journal.write_text(json.dumps({'config_after_sha256': 'd' * 64,
                                           'candidate_promotion': {'mode': 'same_filesystem_move'}}))
            state = {'activated_at': 1, 'before_run_ids': {419883688: 5000, 298278418: 5000}}
            before = {}
            active = {'manager_sha256': transaction.NEW_MANAGER_HASH,
                      'helper_sha256': transaction.HELPER_HASH,
                      'config_sha256': 'd' * 64,
                      'selector_target': str(transaction.NEW_IMAGE),
                      'active_image_sha256': transaction.CANDIDATE_HASH}

            def write_json(path, value, mode=0o600):
                statuses.append((Path(path), dict(value)))
                if Path(path) == journal:
                    journal.write_text(json.dumps(value))

            old_signal_state = dict(transaction.SIGNAL_CONTROL)
            transaction.SIGNAL_CONTROL.update({'abort_enabled': False, 'requested': None})

            with mock.patch.object(transaction, 'MAX_WAIT_SECONDS', 60), \
                 mock.patch.object(transaction, 'POLL_SECONDS', 1), \
                 mock.patch.object(transaction, 'JOURNAL', journal), \
                 mock.patch.object(transaction.time, 'monotonic', side_effect=monotonic), \
                 mock.patch.object(transaction.time, 'sleep', side_effect=sleep), \
                 mock.patch.object(transaction, 'ensure_empty', side_effect=ensure_empty), \
                 mock.patch.object(transaction, 'source_fingerprints', return_value=active), \
                 mock.patch.object(transaction, 'safe_command', return_value=''), \
                 mock.patch.object(transaction, 'wait_service'), \
                 mock.patch.object(transaction, 'verify_running_state'), \
                 mock.patch.object(transaction, 'restore_old_files'), \
                 mock.patch.object(transaction, 'restore_candidate_source'), \
                 mock.patch.object(transaction, 'write_json', side_effect=write_json):
                try:
                    with redirect_stdout(io.StringIO()):
                        transaction.watch_and_rollback(root, state, backup, before, object(), new_module, 'fake-token')
                finally:
                    transaction.SIGNAL_CONTROL.update(old_signal_state)
        report = statuses[-1][1]
        return report, statuses, empty_calls['count'], client

    def test_full_watcher_waits_for_caller_runs_and_idle_then_rolls_back(self):
        report, statuses, empty_calls, _client = self.run_watch_scenario(
            idle_races=(1, 4), hup=True)
        phases = [value.get('phase') for _path, value in statuses]
        self.assertIn('awaiting_caller_publication', phases)
        self.assertIn('active_watching', phases)
        self.assertIn('rollback_waiting_for_idle', phases)
        self.assertEqual(report['phase'], 'rolled_back_after_gate_comparison')
        self.assertFalse(report['timed_out'])
        self.assertEqual(report['watch_failure'], 'operator_or_terminal_interrupt')
        self.assertEqual(set(report['runs']), {'419883688', '298278418'})
        self.assertTrue(report['runs']['419883688']['backend_role_label_verified'])
        self.assertEqual(empty_calls, 7)  # busy trial, then race-safe stop/reopen/recheck

    def test_source_failure_is_terminal_and_still_rolls_back(self):
        report, _statuses, _empty_calls, _client = self.run_watch_scenario(fail_source=True)
        self.assertEqual(report['phase'], 'rolled_back_after_gate_comparison')
        self.assertEqual(report['runs']['419883688']['conclusion'], 'failure')
        self.assertEqual(report['runs']['298278418']['conclusion'], 'failure')

    def test_terminal_backend_without_runner_assignment_is_failed_trial_then_rolls_back(self):
        report, _statuses, _empty_calls, _client = self.run_watch_scenario(assign_runner=False)
        self.assertEqual(report['phase'], 'rolled_back_after_gate_comparison')
        self.assertFalse(report['runs']['419883688']['backend_role_label_verified'])

    def test_no_run_after_caller_publication_timeout_rolls_back_after_idle(self):
        report, statuses, _empty_calls, _client = self.run_watch_scenario(publish=False)
        self.assertEqual(report['phase'], 'rolled_back_after_gate_comparison')
        self.assertTrue(report['timed_out'])
        self.assertEqual(report['runs'], {})
        self.assertIn('awaiting_caller_publication', [value.get('phase') for _path, value in statuses])

    def test_queued_workflow_without_matrix_jobs_waits_instead_of_aborting(self):
        client = FakeGitHub(compact_jobs=[], baseline_jobs=[])
        found = transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertEqual(found[419883688]['backend_job_status'], 'not_created_yet')
        self.assertEqual(found[298278418]['backend_job_status'], 'not_created_yet')

    def test_queued_backend_without_runner_assignment_waits_for_the_job(self):
        client = FakeGitHub(compact_jobs=[compact_job()], baseline_jobs=[baseline_job()])
        found = transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertIsNone(found[419883688]['backend_role_label_verified'])
        self.assertEqual(found[419883688]['backend_job_status'], 'queued')

    def test_assigned_compact_job_requires_actual_sanctuary_runner_and_pilot_labels(self):
        job = compact_job(status='in_progress', runner_id=1825, runner_name='sanctuary-6101',
                          group_id=4, group_name='sanctuary-trusted-verification',
                          labels=['self-hosted', 'ci-gate-backend-pilot', 'sanctuary-gate'])
        client = FakeGitHub(compact_jobs=[job], baseline_jobs=[baseline_job('completed')])
        found = transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertTrue(found[419883688]['backend_role_label_verified'])
        self.assertEqual(found[419883688]['backend_runner_id'], 1825)
        self.assertEqual(found[298278418]['backend_job_status'], 'completed')

    def test_wrong_sanctuary_group_or_missing_role_label_is_terminal_evidence_not_early_abort(self):
        job = compact_job(status='completed', runner_id=1825, runner_name='sanctuary-6101',
                          group_id=4, group_name='sanctuary-trusted-verification',
                          labels=['self-hosted', 'trusted-medium'])
        client = FakeGitHub(compact_status='completed', compact_jobs=[job],
                            baseline_status='completed', baseline_jobs=[baseline_job('completed')])
        found = transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertFalse(found[419883688]['backend_role_label_verified'])
        self.assertEqual(found[419883688]['status'], 'completed')
        self.assertEqual(found[298278418]['status'], 'completed')

    def test_completed_compact_workflow_missing_backend_is_recorded_as_failed_trial(self):
        client = FakeGitHub(compact_status='completed', compact_jobs=[],
                            baseline_status='completed', baseline_jobs=[baseline_job('completed')])
        found = transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertEqual(found[419883688]['backend_job_status'], 'missing_at_terminal')
        self.assertFalse(found[419883688]['backend_role_label_verified'])

    def test_source_and_merge_parent_drift_never_match_a_comparison(self):
        client = FakeGitHub(parent_shas=[transaction.PREACTIVATION_HEAD_SHA, transaction.BASE_SHA])
        with self.assertRaises(transaction.Blocked) as caught:
            transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertEqual(caught.exception.code, 'gate_merge_parents_changed')
        client = FakeGitHub(pull_head='f' * 40)
        with self.assertRaises(transaction.Blocked) as caught:
            transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertEqual(caught.exception.code, 'gate_candidate_changed')

    def test_prepublication_gate_head_waits_for_caller_instead_of_aborting(self):
        client = FakeGitHub(pull_head=transaction.PREACTIVATION_HEAD_SHA,
                            parent_shas=[transaction.PREACTIVATION_HEAD_SHA, transaction.BASE_SHA])
        with self.assertRaises(transaction.Blocked) as caught:
            transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertEqual(caught.exception.code, 'gate_caller_not_published')

    def test_non_github_api_origin_is_rejected_before_credential_read_or_client_creation(self):
        class Checker:
            @staticmethod
            def loaded_manager_credential(_cfg, _module):
                raise AssertionError('credential must not be read')

        class Manager:
            @staticmethod
            def GitHubClient(*_args, **_kwargs):
                raise AssertionError('client must not be constructed')

        with self.assertRaises(transaction.Blocked) as caught:
            transaction.existing_manager_client({'github_api_url': 'https://attacker.example'},
                                                Checker(), Manager())
        self.assertEqual(caught.exception.code, 'github_api_origin_not_allowed')

    def test_operator_signal_handler_aborts_only_during_activation_mutation(self):
        old_state = dict(transaction.SIGNAL_CONTROL)
        old_handlers = {sig: transaction.signal.getsignal(sig)
                        for sig in (transaction.signal.SIGINT, transaction.signal.SIGHUP,
                                    transaction.signal.SIGTERM)}
        try:
            transaction.SIGNAL_CONTROL.update({'abort_enabled': False, 'requested': None})
            handlers = transaction.install_operator_signal_handlers()
            handler = transaction.signal.getsignal(transaction.signal.SIGHUP)
            handler(transaction.signal.SIGHUP, None)
            self.assertEqual(transaction.SIGNAL_CONTROL['requested'], transaction.signal.SIGHUP)
            transaction.SIGNAL_CONTROL.update({'abort_enabled': True, 'requested': None})
            with self.assertRaises(transaction.OperatorInterrupt):
                handler(transaction.signal.SIGHUP, None)
            self.assertEqual(transaction.SIGNAL_CONTROL['requested'], transaction.signal.SIGHUP)
            for sig, previous in handlers.items():
                transaction.signal.signal(sig, previous)
        finally:
            transaction.SIGNAL_CONTROL.update(old_state)
            for sig, previous in old_handlers.items():
                transaction.signal.signal(sig, previous)

    def test_post_wait_config_drift_blocks_before_any_github_request(self):
        class NoRequestClient:
            def __init__(self): self.requests = []
            def request(self, *args, **kwargs):
                self.requests.append((args, kwargs))
                raise AssertionError('drift must be detected before GitHub reads')

        client = NoRequestClient()
        hashes = {transaction.MANAGER: transaction.OLD_MANAGER_HASH,
                  transaction.HELPER: transaction.HELPER_HASH}
        selector = SimpleNamespace(resolve=lambda strict: transaction.SOURCE_IMAGE)
        with mock.patch.object(transaction, 'sha256_file', side_effect=lambda path: hashes[path]), \
             mock.patch.object(transaction, 'secure_regular', return_value=b'concurrently changed config'), \
             mock.patch.object(transaction, 'SELECTOR', selector):
            with self.assertRaises(transaction.Blocked) as caught:
                transaction.verify_pre_activation_state(client, Path('/unused'), object(), object(),
                                                        expected_config_bytes=b'approved config')
        self.assertEqual(caught.exception.code, 'manager_config_changed_while_waiting')
        self.assertEqual(client.requests, [])

    def test_ambiguous_multiple_new_workflow_runs_block_comparison_selection(self):
        client = FakeGitHub()
        extra = dict(client.run_records[419883688]); extra['id'] = 5103
        client.run_records[419883688] = [client.run_records[419883688], extra]
        with self.assertRaises(transaction.Blocked) as caught:
            transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertEqual(caught.exception.code, 'multiple_gate_candidate_runs')

    def test_run_without_explicit_gate_pull_request_link_is_not_selected(self):
        client = FakeGitHub()
        client.run_records[419883688]['pull_requests'] = []
        found = transaction.validate_pr_and_runs(client, 1, {419883688: 5000, 298278418: 5000})
        self.assertNotIn(419883688, found)
        self.assertIn(298278418, found)

    def test_latched_run_is_refreshed_by_immutable_id_after_pr_head_moves(self):
        client = FakeGitHub(pull_head=transaction.PREACTIVATION_HEAD_SHA)
        client.run_records[419883688]['status'] = 'completed'
        client.run_records[419883688]['conclusion'] = 'failure'
        # The initial list response linked this ID to PR #444. GitHub's single
        # run endpoint may omit pull_requests, so identity is pinned by ID plus
        # workflow/head/attempt fields after discovery.
        client.run_records[419883688]['pull_requests'] = []
        client.compact_jobs = []
        run = transaction.refresh_latched_run(client, 419883688, 5101)
        self.assertEqual(run['id'], 5101)
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['backend_job_status'], 'missing_at_terminal')
        self.assertIn(('GET', '/repos/Tuinstra-DEV/gate/actions/runs/5101', None), client.requests)

    def test_missing_lease_state_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'state-does-not-exist'
            with self.assertRaises(transaction.Blocked) as caught:
                transaction.lease_inventory(missing)
        self.assertEqual(caught.exception.code, 'lease_state_directory_unavailable')

    def test_active_lease_and_cleanup_temp_names_are_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory); state.chmod(0o700)
            (state / 'lease-123.tmp').write_text('{}')
            with self.assertRaises(transaction.Blocked) as caught:
                leases, _ = transaction.lease_inventory(state)
                if leases: raise transaction.Blocked('runner_window_not_empty')
        self.assertEqual(caught.exception.code, 'runner_window_not_empty')

    def test_atomic_write_preserves_preexisting_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'protected.json'
            temp = path.with_name('.protected.json.dev54-tmp')
            temp.write_text('preserve me')
            with self.assertRaises(transaction.Blocked) as caught:
                transaction.atomic_write(path, b'new data', 0, 0, 0o600)
            self.assertEqual(caught.exception.code, 'transaction_temp_already_exists')
            self.assertEqual(temp.read_text(), 'preserve me')

    def test_status_write_preserves_preexisting_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            temp = path.with_name('.status.json.tmp')
            temp.write_text('preserve me')
            with self.assertRaises(transaction.Blocked) as caught:
                transaction.write_json(path, {'phase': 'test'})
            self.assertEqual(caught.exception.code, 'status_write_failed')
            self.assertEqual(temp.read_text(), 'preserve me')

    def test_preexisting_symlink_candidate_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'candidate.qcow2'
            target.write_bytes(b'not the sealed candidate')
            link = Path(directory) / 'sealed.qcow2'
            link.symlink_to(target)
            with mock.patch.object(transaction, 'NEW_IMAGE', link):
                with self.assertRaises(transaction.Blocked) as caught:
                    transaction.prepare_sealed_candidate()
        self.assertEqual(caught.exception.code, 'sealed_image_metadata_or_hash_differs')

    def test_candidate_restore_is_idempotent_after_move_or_copy_was_already_reversed(self):
        for mode in ('same_filesystem_move', 'cross_filesystem_copy'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'candidate.qcow2'
                source.write_bytes(b'validated candidate bytes')
                source.chmod(0o640)
                info = source.stat()
                promotion = {'mode': mode, 'source': str(source), 'source_uid': info.st_uid,
                             'source_gid': info.st_gid, 'source_mode': 0o640,
                             'source_atime_ns': info.st_atime_ns, 'source_mtime_ns': info.st_mtime_ns,
                             'source_size': info.st_size}
                missing_sealed_path = Path(directory) / 'sealed.qcow2'
                with mock.patch.object(transaction, 'CANDIDATE_SOURCE', source), \
                     mock.patch.object(transaction, 'NEW_IMAGE', missing_sealed_path), \
                     mock.patch.object(transaction, 'CANDIDATE_HASH', __import__('hashlib').sha256(
                         b'validated candidate bytes').hexdigest()):
                    transaction.restore_candidate_source(promotion)
                self.assertEqual(source.read_bytes(), b'validated candidate bytes')

    def test_interrupted_rollback_restarts_old_manager_if_candidate_is_already_home(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'candidate.qcow2'
            source.write_bytes(b'validated candidate bytes')
            source.chmod(0o444)  # crash immediately after rename, before source chown/chmod
            info = source.stat()
            sealed = root / 'sealed.qcow2'  # models crash after the move back
            backup = root / 'backup'; backup.mkdir()
            before_refs = json.loads((Path(__file__).with_name('group-before.json')).read_text())['selected_workflows']
            after_refs = json.loads((Path(__file__).with_name('group-after.json')).read_text())['selected_workflows']
            repositories = json.loads((Path(__file__).with_name('repositories-before.json')).read_text())
            (backup / 'group-before.json').write_text(json.dumps({'selected_workflows': before_refs}))
            (backup / 'group-after.json').write_text(json.dumps({'selected_workflows': after_refs}))
            (backup / 'repositories-before.json').write_text(json.dumps(repositories))
            cfg = root / 'manager.toml'; cfg.write_text('[manager]\n')
            promotion = {'mode': 'same_filesystem_move', 'source': str(source), 'source_uid': info.st_uid,
                         'source_gid': info.st_gid, 'source_mode': 0o640,
                         'source_atime_ns': info.st_atime_ns, 'source_mtime_ns': info.st_mtime_ns,
                         'source_size': info.st_size}
            before = {'manager_sha256': transaction.OLD_MANAGER_HASH,
                      'config_sha256': 'a' * 64, 'selector_target': str(transaction.SOURCE_IMAGE),
                      'config_uid': 0, 'config_gid': 0, 'config_mode': 0o600,
                      'manager_uid': 0, 'manager_gid': 0, 'manager_mode': 0o755}
            state = {'phase': 'active_watching', 'backup': str(backup), 'before': before,
                     'config_after_sha256': 'd' * 64, 'candidate_promotion': promotion,
                     'sealed_image_preexisting': False}
            api_repositories = {'total_count': len(repositories), 'repositories': [
                {'full_name': name, 'id': repo_id, 'private': True}
                for name, repo_id in repositories.items()]}
            group = {'selected_workflows': before_refs}
            events = []

            class Checker:
                @staticmethod
                def loaded_manager_credential(_cfg, _module): return 'opaque-test-token'

            old_module = SimpleNamespace(GitHubClient=lambda *_args, **_kwargs: object())
            real_lstat = Path.lstat
            lstat_calls = {'source': 0}

            def interrupted_move_lstat(path):
                if path == source and lstat_calls['source'] == 0:
                    lstat_calls['source'] += 1
                    return SimpleNamespace(st_mode=0o100444, st_nlink=1, st_uid=12345, st_gid=23456,
                                           st_size=info.st_size, st_mtime_ns=info.st_mtime_ns)
                return real_lstat(path)

            def host_command(args, **_kwargs):
                if args[:2] == ['id', '-u']:
                    return '12345'
                if args[:2] == ['getent', 'group']:
                    return 'kvm:x:23456:'
                events.append(args)
                return ''

            with mock.patch.object(transaction, 'SOURCE_IMAGE', root / 'old.qcow2'), \
                 mock.patch.object(transaction, 'CANDIDATE_SOURCE', source), \
                 mock.patch.object(transaction, 'NEW_IMAGE', sealed), \
                 mock.patch.object(transaction, 'CANDIDATE_HASH', hashlib.sha256(b'validated candidate bytes').hexdigest()), \
                 mock.patch.object(transaction, 'CONFIG', cfg), \
                 mock.patch.object(transaction, 'secure_regular', return_value=b'old protected bytes'), \
                 mock.patch.object(transaction, 'source_fingerprints', return_value={
                     'manager_sha256': transaction.NEW_MANAGER_HASH,
                     'config_sha256': 'd' * 64, 'selector_target': str(sealed),
                     'helper_sha256': transaction.HELPER_HASH,
                     'active_image_sha256': hashlib.sha256(b'validated candidate bytes').hexdigest()}), \
                 mock.patch.object(transaction, 'ensure_empty'), \
                 mock.patch.object(transaction, 'atomic_write'), \
                 mock.patch.object(transaction, 'atomic_selector'), \
                 mock.patch.object(transaction, 'safe_command', side_effect=host_command), \
                 mock.patch.object(Path, 'lstat', new=interrupted_move_lstat), \
                 mock.patch.object(transaction.os, 'chown'), \
                 mock.patch.object(transaction, 'wait_service'), \
                 mock.patch.object(transaction, 'load_module', return_value=old_module), \
                 mock.patch.object(transaction, 'load_operator_checker', return_value=Checker()), \
                 mock.patch.object(transaction, 'read_group', return_value=(group, api_repositories)), \
                 mock.patch.object(transaction, 'verify_running_state'), \
                 mock.patch.object(transaction, 'write_json', side_effect=lambda _path, value, *_args, **_kw: events.append(value)):
                transaction.rollback_stopped_transaction(root, state, backup, before)

            self.assertTrue(any(isinstance(event, list) and event[:2] == ['systemctl', 'start']
                                for event in events))
            self.assertEqual(source.read_bytes(), b'validated candidate bytes')
            self.assertEqual(events[-1]['phase'], 'rolled_back_after_interrupted_activation')

    def test_already_restored_active_manager_can_finalize_interrupted_rollback_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'backup'; backup.mkdir()
            before_refs = json.loads((Path(__file__).with_name('group-before.json')).read_text())['selected_workflows']
            after_refs = json.loads((Path(__file__).with_name('group-after.json')).read_text())['selected_workflows']
            repositories = json.loads((Path(__file__).with_name('repositories-before.json')).read_text())
            (backup / 'group-before.json').write_text(json.dumps({'selected_workflows': before_refs}))
            (backup / 'group-after.json').write_text(json.dumps({'selected_workflows': after_refs}))
            (backup / 'repositories-before.json').write_text(json.dumps(repositories))
            old_image = Path(directory) / ('ubuntu-24.04-runner-' + '66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4.qcow2')
            active = {'manager_sha256': transaction.OLD_MANAGER_HASH,
                      'config_sha256': 'a' * 64, 'selector_target': str(old_image),
                      'active_image_sha256': old_image.name.removeprefix('ubuntu-24.04-runner-').removesuffix('.qcow2'),
                      'helper_sha256': transaction.HELPER_HASH}
            group = {'id': 4, 'name': 'sanctuary-trusted-verification', 'visibility': 'selected',
                     'default': False, 'inherited': False, 'allows_public_repositories': False,
                     'restricted_to_workflows': True, 'selected_workflows': before_refs}
            api_repositories = {'total_count': len(repositories), 'repositories': [
                {'full_name': name, 'id': repo_id, 'private': True}
                for name, repo_id in repositories.items()]}
            journal = Path(directory) / 'active-transaction.json'
            transaction_state = {'phase': 'active_watching', 'sealed_image_preexisting': True,
                                 'candidate_promotion': {'mode': 'preexisting'}}
            journal.write_text(json.dumps(transaction_state))
            writes = []
            with mock.patch.object(transaction, 'SOURCE_IMAGE', old_image), \
                 mock.patch.object(transaction, 'JOURNAL', journal), \
                 mock.patch.object(transaction, 'source_fingerprints', return_value=active), \
                 mock.patch.object(transaction, 'read_group', return_value=(group, api_repositories)), \
                 mock.patch.object(transaction, 'verify_running_state'), \
                 mock.patch.object(transaction, 'write_json', side_effect=lambda path, value, *_a, **_k: writes.append((path, value))):
                self.assertTrue(transaction.finish_already_restored_rollback(
                    object(), transaction_state, backup, {'config_sha256': 'a' * 64}, object()))
            self.assertEqual(transaction_state['phase'], 'rolled_back')
            self.assertEqual(writes[-1][1]['phase'], 'rolled_back_after_interrupted_rollback')

    def test_failed_source_and_missing_or_unassigned_backend_are_terminal_trial_evidence(self):
        # The watcher gates rollback on the workflow run, not on success of
        # its source/backend job. A failed source run and a terminal run with
        # no backend job must therefore be allowed to reach rollback.
        failed_source = {419883688: {'status': 'completed', 'conclusion': 'failure'},
                         298278418: {'status': 'completed', 'conclusion': 'success'}}
        self.assertTrue(transaction.terminal_runs_ready(failed_source, discovery_expired=False))
        missing_backend = {419883688: {'status': 'completed', 'backend_job_status': 'missing_at_terminal'},
                           298278418: {'status': 'completed', 'backend_job_status': 'completed'}}
        self.assertTrue(transaction.terminal_runs_ready(missing_backend, discovery_expired=False))
        unassigned_terminal = {419883688: {'status': 'completed', 'backend_job_status': 'completed',
                                           'backend_role_label_verified': False},
                              298278418: {'status': 'completed', 'backend_job_status': 'completed'}}
        self.assertTrue(transaction.terminal_runs_ready(unassigned_terminal, discovery_expired=False))

    def test_discovery_timeout_rolls_back_missing_runs_only_after_latched_runs_finish(self):
        self.assertFalse(transaction.terminal_runs_ready({}, discovery_expired=False))
        self.assertTrue(transaction.terminal_runs_ready({}, discovery_expired=True))
        partial_running = {419883688: {'status': 'in_progress'}}
        self.assertFalse(transaction.terminal_runs_ready(partial_running, discovery_expired=True))
        partial_terminal = {419883688: {'status': 'completed', 'conclusion': 'failure'}}
        self.assertTrue(transaction.terminal_runs_ready(partial_terminal, discovery_expired=True))


if __name__ == '__main__':
    unittest.main()
