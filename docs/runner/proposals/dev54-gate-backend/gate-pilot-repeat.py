#!/usr/bin/env python3
"""Bind one newly authorized comparison to the unchanged 91c9cf3 rollback."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys

BASE_SHA256 = '918bdbed1676eceb72ca4d6fdb629c9c86b4dfff49d7a5adf294e543d944218b'
PACKAGE = Path('/home/mtuinstra/dev54-gate-pilot-transaction-20261118-r3')
PREVIOUS_JOURNAL = Path('/var/backups/dev54-gate-backend/active-transaction.json')
JOURNAL = Path('/var/backups/dev54-gate-backend/active-transaction-20261118-r3.json')
PREACTIVATION_HEAD = '6aaeef338a75546c817938f17f2b40b21cd263c4'
TRIAL_HEAD = '698d6a92c20cf5502d5d95e08245d7acab376952'


def verify_saved_recovery(base, state: dict) -> dict:
    """Open the exact restore inputs as this root recovery process before stop."""
    before = state['before']
    backup = Path(state['backup'])
    persisted = base.load_backup_before(backup, before)
    if persisted != before:
        raise base.Blocked('repeat_backup_baseline_differs')
    for prefix in ('manager', 'config'):
        for suffix in ('uid', 'gid', 'mode'):
            value = before.get(prefix + '_' + suffix)
            if type(value) is not int or value < 0 or (suffix == 'mode' and value > 0o7777):
                raise base.Blocked('repeat_recovery_metadata_incomplete')
    for name, digest in (('manager.before', before['manager_sha256']),
                         ('manager.toml.before', before['config_sha256'])):
        path = backup / name
        base.secure_regular(path, digest)
        if stat.S_IMODE(path.lstat().st_mode) != 0o600:
            raise base.Blocked('repeat_backup_permissions_differ')
    base.verify_restored_files(before)
    for path in (base.JOURNAL, backup / 'transaction.json'):
        if json.loads(base.secure_regular(path)) != state:
            raise base.Blocked('repeat_recovery_journal_readback_differs')
    return {'phase': 'recovery_metadata_verified', 'baseline_metadata_complete': True,
            'backup_files_readable_and_verified': True, 'original_files_match_backup': True,
            'journal_readback_verified': True, 'rollback_source_sha256': BASE_SHA256}


def load_base(directory: Path):
    path = directory / 'gate-pilot-transaction.py'
    if hashlib.sha256(path.read_bytes()).hexdigest() != BASE_SHA256:
        raise ValueError('repeat_base_source_hash_differs')
    spec = importlib.util.spec_from_file_location('dev54_repeat_pinned_transaction', path)
    base = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = base
    spec.loader.exec_module(base)
    base.PACKAGE = PACKAGE
    base.STAGED_MANAGER = PACKAGE / 'ci_runner_manager.candidate.py'
    base.STATUS = PACKAGE / 'status.json'
    base.JOURNAL = JOURNAL
    base.PREACTIVATION_HEAD_SHA = PREACTIVATION_HEAD
    base.HEAD_SHA = TRIAL_HEAD
    original_write = base.write_json
    original_readiness = base.verify_operator_readiness

    def readiness_for_current_head(package, checker, cfg, old_module):
        class BoundChecker:
            def __getattr__(self, name):
                return getattr(checker, name)

            def credential_checks(self, config, manifest, module):
                # Keep the hash-pinned original manifest/readers; rebind only
                # the authorized current source head for their existing GETs.
                return checker.credential_checks(
                    config, {**manifest, 'gate_remote_head': PREACTIVATION_HEAD}, module)

        return original_readiness(package, BoundChecker(), cfg, old_module)

    def write_with_restore_readback(path, value, mode=0o600):
        original_write(path, value, mode)
        if path == base.JOURNAL and value.get('phase') == 'prepared' and value.get('activated_at') == 0:
            receipt = verify_saved_recovery(base, value)
            original_write(PACKAGE / 'recovery-metadata-verification.json', receipt, 0o644)
            original_write(base.STATUS, receipt, 0o644)
            print(json.dumps(receipt, separators=(',', ':')), flush=True)

    base.write_json = write_with_restore_readback
    base.verify_operator_readiness = readiness_for_current_head
    return base


def main() -> int:
    try:
        base = load_base(Path(__file__).parent)
    except Exception:
        print('{"phase":"blocked","blocker":"repeat_base_source_unavailable_or_changed"}')
        return 1
    if sys.argv[1:] != ['--rollback']:
        try:
            previous = json.loads(base.secure_regular(PREVIOUS_JOURNAL))
            if previous.get('phase') != 'rolled_back':
                raise base.Blocked('previous_trial_not_rolled_back')
        except Exception:
            print('{"phase":"blocked","blocker":"previous_trial_rollback_not_verified"}')
            return 1
    return base.main()


if __name__ == '__main__':
    raise SystemExit(main())
