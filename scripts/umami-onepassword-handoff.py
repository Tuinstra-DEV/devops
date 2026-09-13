#!/usr/bin/env python3
"""Store the prod-01 Umami login and complete its 1Password-backed 2FA setup.

The SSH target, remote secret path, Compose project and Umami RPC operations
are fixed. Secret values travel only through captured process memory and stdin.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote


ITEM_TITLE = 'Umami — prod01'
ITEM_URL = 'https://umami.tuinstra.dev'
ITEM_TAG = 'tuinstra-managed-umami-prod01'
STATE_FIELD_ID = 'tuinstraHandoffState'
OTP_FIELD_ID = 'totp'
RECOVERY_FIELD_ID = 'umamiRecoveryCodes'
REMOTE_TARGET = 'mtuinstra@vps01.tuinstra.dev'
REMOTE_PASSWORD_COMMAND = (
    'sudo -n /usr/bin/cat /etc/tuinstra/umami/admin-password'
)
REMOTE_RPC_COMMAND = (
    'sudo -n /usr/bin/docker compose --project-name umami '
    '--project-directory /var/www/umami --file /var/www/umami/compose.yml '
    'exec -T umami node /opt/tuinstra/onepassword-handoff.mjs'
)
SSH_OPTIONS = [
    '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=15',
]
VAULT_ID_PATTERN = re.compile(r'^[a-z0-9]{26}$')
ITEM_ID_PATTERN = re.compile(r'^[a-z0-9]{26}$')
PASSWORD_PATTERN = re.compile(r'^[a-f0-9]{48}$')
TOTP_SECRET_PATTERN = re.compile(r'^[A-Z2-7]{16,128}$')
OTP_PATTERN = re.compile(r'^\d{6}$')
RECOVERY_PATTERN = re.compile(r'^[A-F0-9]{16}-[A-F0-9]{16}$')
VALID_STATES = {
    'credential-stored', 'seed-stored', 'two-factor-enabled', 'complete',
}


class HandoffError(RuntimeError):
    """A sanitized error whose message never contains process output."""


class ProcessRunner:
    def run(self, args, *, input_text=None, timeout=30):
        environment = os.environ.copy()
        for name in list(environment):
            if name in {'OP_SERVICE_ACCOUNT_TOKEN', 'OP_CONNECT_HOST', 'OP_CONNECT_TOKEN'} \
                    or name.startswith('OP_SESSION_'):
                environment.pop(name)
        return subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
        )


def set_field(item, field_id, field_type, label, value, *, purpose=None):
    fields = item.setdefault('fields', [])
    field = next((candidate for candidate in fields if candidate.get('id') == field_id), None)
    replacement = {'id': field_id, 'type': field_type, 'label': label, 'value': value}
    if purpose:
        replacement['purpose'] = purpose
    if field is None:
        fields.append(replacement)
    else:
        field.clear()
        field.update(replacement)


def field_value(item, field_id):
    for field in item.get('fields', []):
        if field.get('id') == field_id:
            return field.get('value')
    return None


def set_state(item, state):
    if state not in VALID_STATES:
        raise ValueError('Invalid handoff state')
    set_field(item, STATE_FIELD_ID, 'STRING', 'Tuinstra handoff state', state)


def get_state(item):
    return field_value(item, STATE_FIELD_ID)


def otp_uri(secret):
    if not TOTP_SECRET_PATTERN.fullmatch(secret):
        raise HandoffError('Umami returned an invalid TOTP seed; raw value suppressed')
    label = quote('Umami:admin', safe=':')
    return f'otpauth://totp/{label}?secret={secret}&issuer=Umami'


def build_login_item(password):
    item = {
        'title': ITEM_TITLE,
        'category': 'LOGIN',
        'tags': [ITEM_TAG],
        'urls': [{'label': 'website', 'primary': True, 'href': ITEM_URL}],
        'fields': [],
    }
    set_field(item, 'username', 'STRING', 'username', 'admin', purpose='USERNAME')
    set_field(item, 'password', 'CONCEALED', 'password', password, purpose='PASSWORD')
    set_field(
        item,
        'notesPlain',
        'STRING',
        'notesPlain',
        'Managed by the DEV-24 prod-01 Umami handoff. Do not duplicate.',
        purpose='NOTES',
    )
    set_state(item, 'credential-stored')
    return item


class SafeCommands:
    def __init__(self, runner):
        self.runner = runner

    def run(self, args, *, input_text=None, timeout=30, operation='Command'):
        try:
            result = self.runner.run(args, input_text=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise HandoffError(f'{operation} timed out; its outcome is uncertain') from None
        except OSError:
            raise HandoffError(f'{operation} could not be started') from None
        if result.returncode != 0:
            raise HandoffError(f'{operation} failed; raw output suppressed')
        if len(result.stdout) > 131072:
            raise HandoffError(f'{operation} returned too much data; raw output suppressed')
        return result.stdout

    def json(self, args, *, input_value=None, timeout=30, operation='Command'):
        input_text = None if input_value is None else json.dumps(input_value)
        output = self.run(args, input_text=input_text, timeout=timeout, operation=operation)
        try:
            return json.loads(output)
        except (TypeError, ValueError):
            raise HandoffError(f'{operation} returned invalid JSON; raw output suppressed') from None


class OnePassword:
    def __init__(self, executable, vault_id, commands):
        self.executable = executable
        self.vault_id = vault_id
        self.commands = commands

    def matches(self):
        value = self.commands.json(
            [self.executable, 'item', 'list', '--vault', self.vault_id,
             '--categories', 'Login', '--format', 'json'],
            operation='1Password item metadata listing',
        )
        if not isinstance(value, list):
            raise HandoffError('1Password item metadata listing returned an invalid shape')
        return [entry for entry in value if entry.get('title') == ITEM_TITLE]

    def get(self, item_id):
        self._validate_item_id(item_id)
        value = self.commands.json(
            [self.executable, 'item', 'get', item_id, '--vault', self.vault_id,
             '--format', 'json', '--reveal'],
            operation='1Password managed item read',
        )
        if not isinstance(value, dict):
            raise HandoffError('1Password managed item returned an invalid shape')
        return value

    def create(self, item):
        create_error = None
        try:
            created = self.commands.json(
                [self.executable, 'item', 'create', '-', '--vault', self.vault_id,
                 '--format', 'json'],
                input_value=item,
                operation='1Password managed item creation',
            )
            item_id = created.get('id') if isinstance(created, dict) else None
        except HandoffError as error:
            create_error = error
            item_id = None

        matches = self.matches()
        if len(matches) != 1:
            suffix = 'duplicate candidates found' if matches else 'item not found after attempt'
            raise HandoffError(f'1Password item creation outcome is uncertain; {suffix}') from create_error
        resolved_id = matches[0].get('id')
        self._validate_item_id(resolved_id)
        if item_id is not None and item_id != resolved_id:
            raise HandoffError('1Password item creation returned a conflicting item identity')
        return self.get(resolved_id)

    def edit(self, item, verifier, operation):
        item_id = item.get('id')
        self._validate_item_id(item_id)
        error = None
        try:
            self.commands.json(
                [self.executable, 'item', 'edit', item_id, '--vault', self.vault_id,
                 '--format', 'json'],
                input_value=item,
                operation=operation,
            )
        except HandoffError as caught:
            error = caught
        try:
            current = self.get(item_id)
        except HandoffError:
            current = None
        if current is not None and verifier(current):
            return current
        if error:
            raise error
        raise HandoffError(f'{operation} could not be verified')

    def otp(self, item_id):
        self._validate_item_id(item_id)
        value = self.commands.run(
            [self.executable, 'item', 'get', item_id, '--vault', self.vault_id, '--otp'],
            operation='1Password one-time password read',
        ).strip()
        if not OTP_PATTERN.fullmatch(value):
            raise HandoffError('1Password returned an invalid one-time password; value suppressed')
        return value

    @staticmethod
    def _validate_item_id(item_id):
        if not isinstance(item_id, str) or not ITEM_ID_PATTERN.fullmatch(item_id):
            raise HandoffError('Invalid 1Password item identity')


class RemoteUmami:
    def __init__(self, commands):
        self.commands = commands

    @staticmethod
    def ssh(command):
        return ['ssh', *SSH_OPTIONS, REMOTE_TARGET, command]

    def password(self):
        value = self.commands.run(
            self.ssh(REMOTE_PASSWORD_COMMAND),
            timeout=30,
            operation='Umami managed password read',
        ).strip()
        if not PASSWORD_PATTERN.fullmatch(value):
            raise HandoffError('Managed Umami password has an invalid shape; value suppressed')
        return value

    def call(self, operation, **values):
        request = {'operation': operation, **values}
        response = self.commands.json(
            self.ssh(REMOTE_RPC_COMMAND),
            input_value=request,
            timeout=45,
            operation=f'Umami {operation} RPC',
        )
        if not isinstance(response, dict):
            raise HandoffError(f'Umami {operation} RPC returned an invalid shape')
        status = response.get('status')
        payload = response.get('payload')
        if not isinstance(status, int) or not isinstance(payload, dict):
            raise HandoffError(f'Umami {operation} RPC returned an invalid shape')
        return status, payload


class UmamiOnePasswordHandoff:
    def __init__(self, op_executable, vault_id, *, runner=None, recovery_dir=None,
                 sleeper=time.sleep):
        if not VAULT_ID_PATTERN.fullmatch(vault_id):
            raise ValueError('Use the exact 26-character 1Password vault ID')
        commands = SafeCommands(runner or ProcessRunner())
        self.onepassword = OnePassword(op_executable, vault_id, commands)
        self.umami = RemoteUmami(commands)
        self.recovery_dir = recovery_dir or tempfile.gettempdir()
        self.sleeper = sleeper

    def managed_item(self, password):
        matches = self.onepassword.matches()
        if len(matches) > 1:
            raise HandoffError('Multiple exact Umami Login items exist; refusing to choose')
        if not matches:
            item = self.onepassword.create(build_login_item(password))
        else:
            item = self.onepassword.get(matches[0].get('id'))
        self.validate_managed_item(item, password)
        return item

    @staticmethod
    def validate_managed_item(item, password):
        if item.get('title') != ITEM_TITLE or item.get('category') != 'LOGIN':
            raise HandoffError('Existing Umami item does not match the managed Login contract')
        if ITEM_TAG not in item.get('tags', []):
            raise HandoffError('Exact Umami title belongs to an unmanaged item; refusing overwrite')
        urls = item.get('urls', [])
        if not any(url.get('href') == ITEM_URL for url in urls):
            raise HandoffError('Managed Umami item has an unexpected URL')
        if field_value(item, 'username') != 'admin':
            raise HandoffError('Managed Umami item has an unexpected username')
        if field_value(item, 'password') != password:
            raise HandoffError('Managed Umami password differs from the server credential')
        if get_state(item) not in VALID_STATES:
            raise HandoffError('Managed Umami item has an invalid handoff state')

    @staticmethod
    def require_http_ok(status, operation):
        if status != 200:
            raise HandoffError(f'Umami {operation} failed with HTTP {status}; payload suppressed')

    @staticmethod
    def bearer(payload, field='token'):
        value = payload.get(field)
        if not isinstance(value, str) or not 10 <= len(value) <= 8192:
            raise HandoffError('Umami returned an invalid authentication token; value suppressed')
        return value

    def next_otp(self, item_id, previous):
        for _attempt in range(21):
            candidate = self.onepassword.otp(item_id)
            if candidate != previous:
                return candidate
            self.sleeper(2)
        raise HandoffError('A fresh 1Password OTP was not available within 42 seconds')

    def store_seed(self, item, secret, auth_token):
        try:
            uri = otp_uri(secret)
        except HandoffError:
            status, _payload = self.umami.call(
                'twoFactorCancel', authToken=auth_token,
            )
            self.require_http_ok(status, 'pending 2FA cancellation')
            raise
        updated = copy.deepcopy(item)
        set_field(updated, OTP_FIELD_ID, 'OTP', 'one-time password', uri)
        set_state(updated, 'seed-stored')
        try:
            stored = self.onepassword.edit(
                updated,
                lambda value: field_value(value, OTP_FIELD_ID) == uri
                and get_state(value) == 'seed-stored',
                '1Password TOTP seed storage',
            )
        except HandoffError:
            try:
                current = self.onepassword.get(item['id'])
            except HandoffError:
                raise HandoffError(
                    'TOTP seed storage outcome is uncertain; pending setup remains disabled'
                ) from None
            if field_value(current, OTP_FIELD_ID) == uri:
                raise HandoffError(
                    'TOTP seed exists in 1Password but its handoff state is uncertain; rerun safely'
                ) from None
            status, _payload = self.umami.call(
                'twoFactorCancel', authToken=auth_token,
            )
            self.require_http_ok(status, 'pending 2FA cancellation')
            raise HandoffError('TOTP seed was not stored; pending Umami setup was cancelled') from None
        # A separate OTP read proves 1Password accepted the field as a TOTP seed.
        self.onepassword.otp(stored['id'])
        return stored

    def preserve_recovery_codes(self, codes):
        Path(self.recovery_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, path = tempfile.mkstemp(
            prefix='umami-prod01-recovery-', suffix='.txt', dir=self.recovery_dir, text=True,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                handle.write('\n'.join(codes) + '\n')
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        return path

    def store_recovery_codes(self, item, codes):
        if (not isinstance(codes, list) or len(codes) != 10
                or any(not isinstance(code, str) or not RECOVERY_PATTERN.fullmatch(code)
                       for code in codes)):
            raise HandoffError('Umami returned invalid recovery codes; values suppressed')
        joined = '\n'.join(codes)
        updated = copy.deepcopy(item)
        set_field(
            updated, RECOVERY_FIELD_ID, 'CONCEALED', 'Umami recovery codes', joined,
        )
        set_state(updated, 'two-factor-enabled')
        try:
            return self.onepassword.edit(
                updated,
                lambda value: field_value(value, RECOVERY_FIELD_ID) == joined
                and get_state(value) == 'two-factor-enabled',
                '1Password recovery-code storage',
            )
        except HandoffError:
            path = self.preserve_recovery_codes(codes)
            raise HandoffError(
                f'Recovery-code vault write could not be verified; protected copy: {path}'
            ) from None

    def verify_login(self, item, partial_token, previous_otp=None):
        if previous_otp:
            otp = self.next_otp(item['id'], previous_otp)
        else:
            otp = self.onepassword.otp(item['id'])
        status, verified = self.umami.call(
            'twoFactorVerify', partialToken=partial_token, otp=otp,
        )
        self.require_http_ok(status, '2FA verification')
        full_token = self.bearer(verified)
        status, identity = self.umami.call('authVerify', authToken=full_token)
        self.require_http_ok(status, 'authenticated identity verification')
        if identity.get('username') != 'admin' or identity.get('isAdmin') is not True:
            raise HandoffError('Authenticated Umami identity is not the expected administrator')
        return True

    def complete_item(self, item):
        if get_state(item) == 'complete':
            return item
        updated = copy.deepcopy(item)
        set_state(updated, 'complete')
        return self.onepassword.edit(
            updated,
            lambda value: get_state(value) == 'complete',
            '1Password handoff completion state',
        )

    def run(self):
        password = self.umami.password()
        item = self.managed_item(password)
        state = get_state(item)

        status, login = self.umami.call('login', password=password)
        self.require_http_ok(status, 'administrator login')

        if login.get('requiresTwoFactor') is True:
            if state not in {'two-factor-enabled', 'complete'}:
                raise HandoffError(
                    'Umami already has 2FA, but the managed item has no confirmed recovery handoff; '
                    'refusing reset'
                )
            partial = self.bearer(login, 'partialToken')
            self.verify_login(item, partial)
            item = self.complete_item(item)
        else:
            if state in {'two-factor-enabled', 'complete'}:
                raise HandoffError(
                    'Managed item claims completed 2FA while Umami does not; refusing reset'
                )
            auth_token = self.bearer(login)
            if state == 'credential-stored':
                status, initiated = self.umami.call(
                    'twoFactorInitiate', authToken=auth_token,
                )
                self.require_http_ok(status, '2FA initiation')
                secret = initiated.get('manualKey')
                if not isinstance(secret, str):
                    raise HandoffError('Umami 2FA initiation omitted the seed; payload suppressed')
                item = self.store_seed(item, secret, auth_token)
            elif state != 'seed-stored':
                raise HandoffError('Managed Umami item cannot resume from its current state')

            otp = self.onepassword.otp(item['id'])
            status, confirmed = self.umami.call(
                'twoFactorConfirm', authToken=auth_token, otp=otp,
            )
            self.require_http_ok(status, '2FA confirmation')
            item = self.store_recovery_codes(item, confirmed.get('backupCodes'))

            status, login = self.umami.call('login', password=password)
            self.require_http_ok(status, 'post-enrollment administrator login')
            if login.get('requiresTwoFactor') is not True:
                raise HandoffError('Umami did not require 2FA after enrollment')
            partial = self.bearer(login, 'partialToken')
            self.verify_login(item, partial, previous_otp=otp)
            item = self.complete_item(item)

        return {
            'itemId': item['id'],
            'credentialStored': True,
            'twoFactorStored': True,
            'recoveryCodesStored': True,
            'loginVerified': True,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--op', required=True, help='Absolute path to the 1Password CLI')
    parser.add_argument('--vault', required=True, help='Exact 26-character 1Password vault ID')
    args = parser.parse_args()

    executable = Path(args.op)
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('--op must be an absolute executable file')
    result = UmamiOnePasswordHandoff(str(executable), args.vault).run()
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (HandoffError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
