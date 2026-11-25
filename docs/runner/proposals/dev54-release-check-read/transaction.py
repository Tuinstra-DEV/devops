#!/usr/bin/env python3
"""Fixed release ref activation with a journal-bound recovery path."""
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import plan
import preflight
import recovery
from live_recovery import LiveRecovery

BACKUP_ROOT = Path('/var/backups/dev54-release-check-read')
JOURNAL = BACKUP_ROOT / 'active.json'


def saved_file(base, path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if base.secure_regular(path) != data:
        raise ValueError('backup_readback_differs')


def prepare(base, manager, original, group, repos):
    """Seal original state and inverse inputs before stopping the manager."""
    if JOURNAL.exists() or JOURNAL.is_symlink():
        raise ValueError('existing_release_journal')
    candidate, after_group = preflight.verify_policy(base, manager, original, group, repos)
    BACKUP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = BACKUP_ROOT.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('backup_directory_unsafe')
    backup = BACKUP_ROOT / 'snapshot'
    backup.mkdir(mode=0o700)
    fingerprints = base.source_fingerprints()
    manager_info, config_info = base.MANAGER.stat(), base.CONFIG.stat()
    metadata = {**fingerprints, 'selector_link': os.readlink(base.SELECTOR),
                'manager_uid': manager_info.st_uid, 'manager_gid': manager_info.st_gid,
                'manager_mode': stat.S_IMODE(manager_info.st_mode),
                'config_uid': config_info.st_uid, 'config_gid': config_info.st_gid,
                'config_mode': stat.S_IMODE(config_info.st_mode)}
    for name, data in [('manager.before', base.secure_regular(base.MANAGER)),
                       ('manager.toml.before', original), ('manager.toml.after', candidate),
                       ('group-before.json', json.dumps(group).encode()),
                       ('group-after.json', json.dumps(after_group).encode()),
                       ('repositories-before.json', json.dumps(base.repository_map(repos)).encode())]:
        saved_file(base, backup / name, data)
    state = {'phase': 'prepared', 'backup': str(backup), 'before': metadata,
             'config_after_sha256': hashlib.sha256(candidate).hexdigest()}
    base.write_json(backup / 'before.json', metadata)
    base.write_json(backup / 'transaction.json', state)
    base.write_json(JOURNAL, state)
    if json.loads(base.secure_regular(JOURNAL)) != state or json.loads(base.secure_regular(backup / 'transaction.json')) != state:
        raise ValueError('journal_readback_differs')
    if base.load_backup_before(backup, metadata) != metadata:
        raise ValueError('backup_metadata_differs')
    base.verify_restored_files(metadata)
    return state, backup, candidate, after_group


def verify_baseline(base, original, group, repos, client):
    if base.secure_regular(base.CONFIG) != original:
        raise ValueError('prechange_config_drift')
    current_group, current_repos = base.read_group(client, {})
    if current_group != group or base.repository_map(current_repos) != base.repository_map(repos):
        raise ValueError('prechange_group_drift')
    if base.sha256_file(base.MANAGER) != preflight.MANAGER_HASH:
        raise ValueError('prechange_manager_drift')
    if base.SELECTOR.resolve(strict=True) != base.NEW_IMAGE:
        raise ValueError('prechange_image_drift')
    base.secure_regular(base.HELPER, base.HELPER_HASH)
    base.verify_sealed_candidate(base.NEW_IMAGE)


def activate(base, manager, checker, client, original, group, repos):
    base.wait_until_empty(base.MAX_WAIT_SECONDS, 'release_ref_waiting_for_empty_window')
    verify_baseline(base, original, group, repos, client)
    state, backup, candidate, after_group = prepare(base, manager, original, group, repos)
    # The journal is durable before the service or group changes. The recovery
    # procedure handles interruption at each following boundary.
    try:
        verify_baseline(base, original, group, repos, client)
        base.stop_manager_when_empty('release_ref_waiting_for_empty_window')
        verify_baseline(base, original, group, repos, client)
        base.patch_group(client, after_group['selected_workflows'])
        metadata = state['before']
        base.atomic_write(base.CONFIG, candidate, metadata['config_uid'], metadata['config_gid'], metadata['config_mode'])
        if base.secure_regular(base.CONFIG) != candidate:
            raise ValueError('config_after_readback_differs')
        base.safe_command(['systemctl', 'start', base.SERVICE])
        base.wait_service(True)
        cfg = base.parse_manager_config(base.secure_regular(base.CONFIG))
        manager.validate_org_config(cfg)
        _, current_client = base.existing_manager_client(cfg, checker, manager)
        actual_group, actual_repos = base.read_group(current_client, cfg)
        if (actual_group != after_group or base.repository_map(actual_repos) != base.repository_map(repos) or
                set(manager.configured_org_workflow_refs(cfg)) != set(after_group['selected_workflows'])):
            raise ValueError('activated_policy_readback_differs')
        state['phase'] = 'active'
        base.write_json(JOURNAL, state)
        return state
    except BaseException:
        recover(base, manager, checker)
        raise


def recover(base, manager, checker):
    state = json.loads(base.secure_regular(JOURNAL))
    backup = Path(state['backup'])
    if backup != BACKUP_ROOT / 'snapshot':
        raise ValueError('recovery_backup_path_differs')
    meta = base.load_backup_before(backup, state['before'])
    old = base.secure_regular(backup / 'manager.toml.before', meta['config_sha256'])
    new = base.secure_regular(backup / 'manager.toml.after', state['config_after_sha256'])
    old_group = json.loads(base.secure_regular(backup / 'group-before.json'))
    new_group = json.loads(base.secure_regular(backup / 'group-after.json'))
    repositories = json.loads(base.secure_regular(backup / 'repositories-before.json'))
    if plan.render_config(old) != new or plan.group_delta(old_group) != new_group:
        raise ValueError('recovery_proposal_differs')
    before = SimpleNamespace(config=old, group=old_group, repositories=repositories, fixed=meta)
    after = SimpleNamespace(config=new, group=new_group)
    adapter = LiveRecovery(base, manager, checker, backup, meta)
    recovery.restore(adapter, before, after)
    state['phase'] = 'rolled_back'
    base.write_json(JOURNAL, state)
    return state


def configure(base):
    """Keep the deployed manager and current image as both recovery baselines."""
    base.OLD_MANAGER_HASH = preflight.MANAGER_HASH
    base.NEW_MANAGER_HASH = preflight.MANAGER_HASH
    base.SOURCE_IMAGE = base.NEW_IMAGE
    base.BACKUP_ROOT = BACKUP_ROOT
    base.JOURNAL = JOURNAL
    base.STATUS = BACKUP_ROOT / 'status.json'


def main(mode):
    if os.geteuid() != 0 or not os.isatty(0):
        raise ValueError('existing_operator_sudo_tty_required')
    base = preflight.load_base()
    configure(base)
    info = BACKUP_ROOT.lstat() if BACKUP_ROOT.exists() else None
    if info is None:
        BACKUP_ROOT.mkdir(mode=0o700, parents=True)
    else:
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('backup_directory_unsafe')
    import fcntl
    lock = os.open('/run/lock/dev54-release-ref.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    lock_info = os.fstat(lock)
    if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != 0 or lock_info.st_nlink != 1 or stat.S_IMODE(lock_info.st_mode) != 0o600:
        raise ValueError('transaction_lock_unsafe')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    base.secure_regular(base.MANAGER, preflight.MANAGER_HASH)
    manager = base.load_module(base.MANAGER, 'release_ref_manager')
    checker = base.load_operator_checker(preflight.PERMANENT)
    if mode == 'rollback':
        return {'phase': recover(base, manager, checker)['phase']}
    if mode != 'activate':
        raise ValueError('unsupported_transaction_action')
    preflight.inspect()
    original = base.secure_regular(base.CONFIG)
    config = base.parse_manager_config(original)
    _, client = base.existing_manager_client(config, checker, manager)
    pull = client.request('GET', '/repos/Tuinstra-DEV/devops/pulls/235')
    if not pull.get('merged') or pull.get('head', {}).get('sha') != 'd25c21b69c89698ef59a0587ec4de7bb572f1f45':
        raise ValueError('provider_review_not_merged')
    comparison = client.request('GET', f'/repos/Tuinstra-DEV/devops/compare/{plan.NEW}...main')
    if comparison.get('status') not in ('ahead', 'identical') or comparison.get('behind_by') != 0:
        raise ValueError('provider_pin_not_on_main')
    group, repositories = base.read_group(client, config)
    result = activate(base, manager, checker, client, original, group, repositories)
    return {'phase': result['phase'], 'recovery_metadata_verified': True,
            'workflow_pin': plan.NEW, 'production_activated': False}


if __name__ == '__main__':
    import re
    import sys
    try:
        if len(sys.argv) != 2:
            raise ValueError('unsupported_transaction_action')
        print(json.dumps(main(sys.argv[1]), separators=(',', ':')))
    except BaseException as error:
        category = getattr(error, 'code', None)
        if category is None:
            category = str(error) if type(error) is ValueError and re.fullmatch('[a-z0-9_]+', str(error)) else type(error).__name__
        result = {'phase': 'blocked', 'blocker': category}
        if type(getattr(error, 'http_status', None)) is int:
            result['http_status'] = error.http_status
        print(json.dumps(result, separators=(',', ':')))
        raise SystemExit(1)
