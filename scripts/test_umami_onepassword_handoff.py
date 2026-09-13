#!/usr/bin/env python3
"""Secret-boundary and state-machine tests for the Umami 1Password handoff."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'handoff', Path(__file__).with_name('umami-onepassword-handoff.py')
)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


PASSWORD = 'a' * 48
ITEM_ID = 'abcdefghijklmnopqrstuvwxzy'
VAULT_ID = 'zyxwvutsrqponmlkjihgfedcba'
RECOVERY_CODES = [f'{index:016X}-{index + 1:016X}' for index in range(10)]


class FakeRunner:
    def __init__(self, *, existing=False, complete=False, unmanaged=False,
                 fail_seed_edit=False, fail_recovery_edit=False,
                 uncertain_create=False):
        self.calls = []
        self.item = None
        self.server_two_factor = complete
        self.pending_seed = False
        self.fail_seed_edit = fail_seed_edit
        self.fail_recovery_edit = fail_recovery_edit
        self.uncertain_create = uncertain_create
        self.otp_values = ['123456', '123456', '654321', '654321']
        self.rpc_operations = []
        if existing:
            self.item = handoff.build_login_item(PASSWORD)
            self.item['id'] = ITEM_ID
            if unmanaged:
                self.item['tags'] = []
            if complete:
                handoff.set_field(self.item, handoff.OTP_FIELD_ID, 'OTP',
                                  'one-time password', handoff.otp_uri('B' * 32))
                handoff.set_field(self.item, handoff.RECOVERY_FIELD_ID, 'CONCEALED',
                                  'Umami recovery codes', '\n'.join(RECOVERY_CODES))
                handoff.set_state(self.item, 'complete')

    def result(self, args, returncode=0, stdout='', stderr=''):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    def run(self, args, *, input_text=None, timeout=30):
        args = list(args)
        self.calls.append((args, input_text))
        if args[0] == 'ssh':
            if args[-1] == handoff.REMOTE_PASSWORD_COMMAND:
                return self.result(args, stdout=PASSWORD + '\n')
            request = json.loads(input_text)
            operation = request['operation']
            self.rpc_operations.append(operation)
            if operation == 'login':
                if self.server_two_factor:
                    payload = {'requiresTwoFactor': True, 'partialToken': 'partial-token-value'}
                else:
                    payload = {'token': 'full-token-value', 'user': {'id': 'admin-user-id'}}
            elif operation == 'twoFactorInitiate':
                self.pending_seed = True
                payload = {'manualKey': 'B' * 32, 'qrCodeDataUrl': 'suppressed'}
            elif operation == 'twoFactorCancel':
                self.pending_seed = False
                payload = {'ok': True}
            elif operation == 'twoFactorConfirm':
                self.server_two_factor = True
                self.pending_seed = False
                payload = {'backupCodes': RECOVERY_CODES}
            elif operation == 'twoFactorVerify':
                payload = {'token': 'verified-full-token', 'user': {'id': 'admin-user-id'}}
            elif operation == 'authVerify':
                payload = {'id': 'admin-user-id', 'username': 'admin', 'isAdmin': True}
            else:
                raise AssertionError(f'unexpected RPC operation {operation}')
            return self.result(args, stdout=json.dumps({'status': 200, 'payload': payload}))

        if args[1:3] == ['item', 'list']:
            metadata = [] if self.item is None else [{'id': ITEM_ID, 'title': handoff.ITEM_TITLE}]
            return self.result(args, stdout=json.dumps(metadata))
        if args[1:3] == ['item', 'create']:
            self.item = json.loads(input_text)
            self.item['id'] = ITEM_ID
            if self.uncertain_create:
                return self.result(args, returncode=1, stderr='uncertain result')
            return self.result(args, stdout=json.dumps(self.item))
        if args[1:3] == ['item', 'get'] and '--otp' in args:
            value = self.otp_values.pop(0) if len(self.otp_values) > 1 else self.otp_values[0]
            return self.result(args, stdout=value + '\n')
        if args[1:3] == ['item', 'get']:
            return self.result(args, stdout=json.dumps(self.item))
        if args[1:3] == ['item', 'edit']:
            candidate = json.loads(input_text)
            state = handoff.get_state(candidate)
            if state == 'seed-stored' and self.fail_seed_edit:
                return self.result(args, returncode=1, stderr='sensitive output suppressed')
            if state == 'two-factor-enabled' and self.fail_recovery_edit:
                return self.result(args, returncode=1, stderr='sensitive output suppressed')
            self.item = candidate
            return self.result(args, stdout=json.dumps(self.item))
        raise AssertionError(f'unexpected command {args}')


class HandoffTests(unittest.TestCase):
    def make_handoff(self, runner, recovery_dir=None):
        return handoff.UmamiOnePasswordHandoff(
            '/opt/op', VAULT_ID, runner=runner,
            recovery_dir=recovery_dir, sleeper=lambda _seconds: None,
        )

    def test_recovery_pattern_matches_umami_3_3_1_contract(self):
        actual_shape = '0123456789ABCDEF-FEDCBA9876543210'
        obsolete_mock_shape = 'A' * 32 + '-' + 'B' * 32

        self.assertIsNotNone(handoff.RECOVERY_PATTERN.fullmatch(actual_shape))
        self.assertIsNone(handoff.RECOVERY_PATTERN.fullmatch(obsolete_mock_shape))

    def test_happy_flow_keeps_all_secrets_out_of_process_arguments(self):
        runner = FakeRunner()
        result = self.make_handoff(runner).run()

        self.assertEqual(result['itemId'], ITEM_ID)
        self.assertTrue(all(value is True for key, value in result.items() if key != 'itemId'))
        all_arguments = '\n'.join(' '.join(call[0]) for call in runner.calls)
        for secret in [PASSWORD, 'B' * 32, *RECOVERY_CODES, '123456', '654321']:
            self.assertNotIn(secret, all_arguments)
        self.assertNotIn('--account', all_arguments)
        self.assertEqual(runner.rpc_operations, [
            'login', 'twoFactorInitiate', 'twoFactorConfirm',
            'login', 'twoFactorVerify', 'authVerify',
        ])

    def test_complete_managed_item_is_reused_without_duplicate_or_reset(self):
        runner = FakeRunner(existing=True, complete=True)
        result = self.make_handoff(runner).run()

        self.assertEqual(result['itemId'], ITEM_ID)
        mutations = [args for args, _ in runner.calls
                     if args[0] == '/opt/op' and args[1:3] in (['item', 'create'], ['item', 'edit'])]
        self.assertEqual(mutations, [])
        self.assertEqual(runner.rpc_operations, ['login', 'twoFactorVerify', 'authVerify'])

    def test_uncertain_creation_resolves_exact_item_without_retrying_create(self):
        runner = FakeRunner(uncertain_create=True)
        result = self.make_handoff(runner).run()
        self.assertEqual(result['itemId'], ITEM_ID)
        creates = [args for args, _ in runner.calls if args[1:3] == ['item', 'create']]
        self.assertEqual(len(creates), 1)

    def test_seed_vault_failure_cancels_pending_setup_before_confirmation(self):
        runner = FakeRunner(fail_seed_edit=True)
        with self.assertRaises(handoff.HandoffError) as error:
            self.make_handoff(runner).run()

        self.assertIn('seed', str(error.exception).lower())
        self.assertIn('twoFactorCancel', runner.rpc_operations)
        self.assertNotIn('twoFactorConfirm', runner.rpc_operations)
        self.assertFalse(runner.server_two_factor)

    def test_recovery_code_vault_failure_preserves_mode_0600_file_without_leaking(self):
        runner = FakeRunner(fail_recovery_edit=True)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(handoff.HandoffError) as error:
                self.make_handoff(runner, directory).run()
            message = str(error.exception)
            self.assertNotIn(RECOVERY_CODES[0], message)
            path = Path(message.rsplit(' ', 1)[-1])
            self.assertTrue(path.is_file())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_text(encoding='utf-8').splitlines(), RECOVERY_CODES)

    def test_unmanaged_title_collision_is_never_overwritten_or_reset(self):
        runner = FakeRunner(existing=True, complete=True, unmanaged=True)
        with self.assertRaises(handoff.HandoffError):
            self.make_handoff(runner).run()

        self.assertEqual(runner.rpc_operations, [])
        self.assertFalse(any(args[1:3] == ['item', 'edit'] for args, _ in runner.calls))

    def test_ssh_and_rpc_targets_are_fixed_and_strict(self):
        runner = FakeRunner(existing=True, complete=True)
        self.make_handoff(runner).run()
        ssh_calls = [args for args, _ in runner.calls if args[0] == 'ssh']
        self.assertTrue(ssh_calls)
        for args in ssh_calls:
            self.assertIn('BatchMode=yes', args)
            self.assertIn('StrictHostKeyChecking=yes', args)
            self.assertEqual(args[-2], handoff.REMOTE_TARGET)
            self.assertIn(args[-1], [handoff.REMOTE_PASSWORD_COMMAND, handoff.REMOTE_RPC_COMMAND])

    def test_real_process_runner_strips_noninteractive_1password_tokens(self):
        completed = subprocess.CompletedProcess([], 0, '', '')
        injected = {
            'OP_SERVICE_ACCOUNT_TOKEN': 'service-secret',
            'OP_CONNECT_TOKEN': 'connect-secret',
            'OP_CONNECT_HOST': 'https://connect.invalid',
            'OP_SESSION_example': 'session-secret',
        }
        with patch.dict(os.environ, injected), patch.object(
                handoff.subprocess, 'run', return_value=completed) as run:
            handoff.ProcessRunner().run(['/opt/op', '--version'])
        environment = run.call_args.kwargs['env']
        for name in injected:
            self.assertNotIn(name, environment)


if __name__ == '__main__':
    unittest.main()
