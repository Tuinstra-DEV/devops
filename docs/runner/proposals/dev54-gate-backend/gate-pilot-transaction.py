#!/usr/bin/env python3
"""Fixed-purpose, fail-closed one-trial Gate Backend CI transaction."""
from __future__ import annotations

import fcntl
import ctypes
import errno
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone

PACKAGE = Path('/home/mtuinstra/dev54-gate-pilot-transaction-20261118-r2')
STAGED_MANAGER = PACKAGE / 'ci_runner_manager.candidate.py'
MANAGER = Path('/usr/local/bin/ci-runner-manager')
CONFIG = Path('/etc/ci-runner/manager.toml')
HELPER = Path('/usr/local/libexec/ci-runner-host-helper')
SELECTOR = Path('/var/lib/ci-runner/images/ubuntu-24.04-runner.qcow2')
SOURCE_IMAGE = Path('/var/lib/ci-runner/images/ubuntu-24.04-runner-66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4.qcow2')
CANDIDATE_SOURCE = Path('/home/mtuinstra/dev54-image-candidate-20261118-guard/candidate-php83-8.3.36-guard-standalone-operator-owned.qcow2')
CANDIDATE_HASH = '2faaf1bed35a253fd134bb52bcd2f9ce3e93f204a0bcdc4d96a5a2df925971ce'
NEW_IMAGE = Path('/var/lib/ci-runner/images/ubuntu-24.04-runner-' + CANDIDATE_HASH + '.qcow2')
OLD_MANAGER_HASH = '5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3'
NEW_MANAGER_HASH = 'e9b2f1865fb915f80f01803a81d36476c637d8b655044f5c664b67acfc6f185c'
HELPER_HASH = 'fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613'
BASE_SHA = '1ca529f02479c4e1d0a14353382a6a7dc9cc74f7'
PREACTIVATION_HEAD_SHA = '188356ba01eb7796b5dc9a746eeabf34f95cf33f'
HEAD_SHA = '4ddbadada67147dc8d2ecb81c061cc61b971f9ba'
WORKFLOW_PIN = 'Tuinstra-DEV/devops/.github/workflows/reusable-product-ci.yml@9e4a72c0b8ca586cd97af4b6e33e65a919accb43'
ROLE_TOML = ("\n[org_execution_roles.gate-backend-pilot]\n"
             'workflow_ref = "' + WORKFLOW_PIN + '"\n'
             'repositories = ["Tuinstra-DEV/gate"]\n'
             'events = ["pull_request"]\n'
             'actors = [{ login = "marcel-tuinstra", type = "User" }]\n').encode()
ROLE_INLINE = b'gate-backend-pilot = { ' + b', '.join(
    line for line in ROLE_TOML.splitlines() if line and not line.startswith(b'[')) + b' }'
GROUP_PATH = '/orgs/Tuinstra-DEV/actions/runner-groups/4'
REPO_PATH = '/repos/Tuinstra-DEV/gate'
SERVICE = 'ci-runner-manager.service'
LOCK = Path('/run/lock/dev54-gate-pilot-transaction.lock')
BACKUP_ROOT = Path('/var/backups/dev54-gate-backend')
JOURNAL = BACKUP_ROOT / 'active-transaction.json'
STATUS = PACKAGE / 'status.json'
MIN_FREE_BYTES = 62 * 1024**3
WORKFLOWS = {
    419883688: ('.github/workflows/ci-compact-pilot.yml', 'CI (compact pilot)', 'ci / Backend CI'),
    298278418: ('.github/workflows/ci.yml', 'Continuous Integration', 'lint / gate-quality / gate-php trusted verification'),
}
INPUT_HASHES = {
    'manifest.json': '768409c30460ae2a507ccd8a1915e1c482f470de407ed71831d04edaa7bdf9a4',
    'group-before.json': 'ccb331309e974d1e53d4c767e82299d934439263457eea617067ee50dcea6d6c',
    'group-after.json': 'd4db99eb68c55255d0fafe56544de71bba5ab13b15c00221144c0ad3d8c8b749',
    'repositories-before.json': 'fc3bdd6f5f7496b7b34cfadfeb72fd1dc39c70c72482c1f394ecc53f8c41d858',
    'operator_check.py': '66932bd68800c7969d03327d85b3087b5b79791d7b9dd8cb66aa2708d98be0c2',
    'image-maintenance-update-result.json': '309cef6b0dbb650967b2f15dcdca22ab6b10f1088bb73ed922543fc3f875dc32',
    'read_effective.py': '6d4288e47fcf900fbf81a50b6cc35cd2abcca857a98d4260c7a466b13c2252bd',
}
MAX_WAIT_SECONDS = 3 * 60 * 60
POLL_SECONDS = 15


class Blocked(Exception):
    def __init__(self, code: str, http_status: int | None = None):
        self.code, self.http_status = code, http_status


class OperatorInterrupt(Exception):
    pass


SIGNAL_CONTROL = {'abort_enabled': False, 'requested': None}


def install_operator_signal_handlers() -> dict[int, object]:
    previous = {}

    def handle(signum, _frame):
        SIGNAL_CONTROL['requested'] = signum
        if SIGNAL_CONTROL['abort_enabled']:
            raise OperatorInterrupt('operator requested interruption')

    for signum in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, handle)
    return previous


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_command(args: list[str], timeout: int = 60, check: bool = True) -> str:
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    except Exception:
        raise Blocked('host_command_unavailable') from None
    if check and result.returncode != 0:
        raise Blocked('host_command_failed_' + Path(args[0]).name)
    return result.stdout.strip()


def load_module(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def secure_regular(path: Path, expected_hash: str | None = None) -> bytes:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
            raise Blocked('protected_file_metadata_differs')
        data = path.read_bytes()
        if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
            raise Blocked('protected_file_hash_differs')
        return data
    except Blocked:
        raise
    except Exception:
        raise Blocked('protected_file_unavailable') from None


def read_staged_manager() -> bytes:
    try:
        info = STAGED_MANAGER.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 1000 or info.st_nlink != 1 or
                info.st_mode & 0o022):
            raise Blocked('staged_manager_metadata_differs')
        data = STAGED_MANAGER.read_bytes()
        if hashlib.sha256(data).hexdigest() != NEW_MANAGER_HASH:
            raise Blocked('staged_manager_hash_differs')
        return data
    except Blocked: raise
    except Exception: raise Blocked('staged_manager_unavailable') from None


def read_input(name: str) -> bytes:
    try:
        path = PACKAGE / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022:
            raise Blocked('transaction_input_metadata_differs')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != INPUT_HASHES[name]:
            raise Blocked('transaction_input_hash_differs')
        return data
    except Blocked: raise
    except Exception: raise Blocked('transaction_input_unavailable') from None


def verify_package_inputs(package: Path) -> None:
    for name, expected in INPUT_HASHES.items():
        if sha256_file(package / name) != expected:
            raise Blocked('transaction_input_hash_differs')
    before = json.loads((package / 'group-before.json').read_text())['selected_workflows']
    after = json.loads((package / 'group-after.json').read_text())['selected_workflows']
    if len(before) != 6 or len(after) != 7 or set(after) != set(before) | {WORKFLOW_PIN}:
        raise Blocked('transaction_group_refs_differs')


def atomic_write(path: Path, data: bytes, owner: int, group: int, mode: int) -> None:
    temp = path.with_name('.' + path.name + '.dev54-tmp')
    created = False
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        created = True
        os.fchown(fd, owner, group)
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except FileExistsError:
        raise Blocked('transaction_temp_already_exists') from None
    except Exception:
        if created:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        raise Blocked('atomic_file_update_failed') from None


def write_json(path: Path, value: dict, mode: int = 0o600) -> None:
    body = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    temp = path.with_name('.' + path.name + '.tmp')
    created = False
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        created = True
        os.fchown(fd, 0, 0)
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    except Exception:
        if created:
            temp.unlink(missing_ok=True)
        raise Blocked('status_write_failed') from None


def rename_noreplace(source: Path, destination: Path) -> None:
    """Linux atomic rename that refuses an already existing destination."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, 'renameat2', None)
    if renameat2 is None:
        raise Blocked('atomic_noreplace_rename_unavailable')
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise Blocked('transaction_target_occupied')
    raise Blocked('atomic_noreplace_rename_failed')


def service_state() -> dict[str, str]:
    values = {}
    for key in ('ActiveState', 'MainPID', 'ExecMainStatus', 'NRestarts'):
        values[key] = safe_command(['systemctl', 'show', SERVICE, '-p', key, '--value'])
    return values


def lease_inventory(state: Path) -> tuple[list[Path], list[Path]]:
    try:
        info = state.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022 or
                not os.access(state, os.R_OK | os.X_OK)):
            raise Blocked('lease_state_directory_unsafe')
        lease_files = list(state.glob('lease-*'))
        cleanup_files = list(state.glob('cleanup-*'))
        if any(not p.is_file() or p.is_symlink() for p in lease_files + cleanup_files):
            raise Blocked('lease_state_unsafe')
        return lease_files, cleanup_files
    except Blocked: raise
    except Exception:
        raise Blocked('lease_state_directory_unavailable') from None


def ensure_empty() -> dict[str, int]:
    # Fail closed on unreadable manager state, pending cleanup obligations,
    # unknown domain names or any active Sanctuary runner VM.
    state = Path('/var/lib/ci-runner-manager/state')
    try:
        lease_files, cleanup_files = lease_inventory(state)
        output = safe_command(['virsh', '-c', 'qemu:///system', 'list', '--uuid'])
        domains = [line for line in output.splitlines() if line.strip()]
        if domains or lease_files or cleanup_files:
            raise Blocked('runner_window_not_empty')
        return {'active_domains': 0, 'lease_files': 0, 'cleanup_obligations': 0}
    except Blocked:
        raise
    except Exception:
        raise Blocked('runner_window_state_unavailable') from None


def parse_manager_config(data: bytes, category: str = 'existing_manager_config_toml_invalid') -> dict:
    try:
        return tomllib.loads(data.decode())
    except (UnicodeError, tomllib.TOMLDecodeError):
        raise Blocked(category) from None


def render_pilot_config(before: bytes) -> bytes:
    """Insert only the approved role; preserve all existing bytes and values."""
    cfg = parse_manager_config(before)
    roles = cfg.get('org_execution_roles', {})
    if not isinstance(roles, dict):
        raise Blocked('existing_manager_roles_invalid')
    if 'gate-backend-pilot' in roles:
        raise Blocked('pilot_role_already_present')
    role = tomllib.loads(ROLE_TOML.decode())['org_execution_roles']['gate-backend-pilot']
    expected = {**cfg, 'org_execution_roles': {**roles, 'gate-backend-pilot': role}}

    def matches(data: bytes) -> bool:
        try:
            return tomllib.loads(data.decode()) == expected
        except (UnicodeError, tomllib.TOMLDecodeError):
            return False

    appended = before + ROLE_TOML
    if matches(appended):
        return appended
    # TOML inline tables cannot be extended with a later table declaration.
    # Test bounded insertion positions with the real parser and require exactly
    # the expected semantic delta, including every untouched credential/setting.
    if len(before) > 262144 or before.count(b'}') > 1024:
        raise Blocked('proposed_role_layout_unsupported')
    insertion = (b', ' if roles else b'') + ROLE_INLINE
    candidates = []
    for index, byte in enumerate(before):
        if byte == ord('}'):
            candidate = before[:index] + insertion + before[index:]
            if matches(candidate):
                candidates.append(candidate)
    if len(candidates) != 1:
        raise Blocked('proposed_role_layout_unsupported')
    return candidates[0]


def config_and_group_preflight(package: Path, old_module, new_module, config_bytes: bytes,
                               group: dict, repositories: dict) -> tuple[dict, dict]:
    verify_package_inputs(package)
    before_group = json.loads((package / 'group-before.json').read_text())
    after_group = json.loads((package / 'group-after.json').read_text())
    repo_snapshot = json.loads((package / 'repositories-before.json').read_text())
    cfg = parse_manager_config(config_bytes)
    if 'gate-backend-pilot' in cfg.get('org_execution_roles', {}):
        raise Blocked('pilot_role_already_present')
    try:
        old_module.validate_org_config(cfg)
        old_refs = old_module.configured_org_workflow_refs(cfg)
    except Exception:
        raise Blocked('effective_manager_policy_invalid') from None
    if (set(old_refs) != set(before_group['selected_workflows']) or len(old_refs) != 6 or
            cfg.get('org_runner_group_id') != 4):
        raise Blocked('effective_manager_group_differs')
    if (not group_policy_matches(group) or
            set(group.get('selected_workflows', [])) != set(before_group['selected_workflows'])):
        raise Blocked('runner_group_before_differs')
    if repository_map(repositories) != repo_snapshot:
        raise Blocked('runner_group_repositories_differ')
    if cfg.get('org_repository_ids') != repo_snapshot:
        raise Blocked('manager_repository_selection_differs')
    # New module and TOML are validated before any live service/file change.
    after_cfg = parse_manager_config(render_pilot_config(config_bytes), 'proposed_manager_config_toml_invalid')
    try:
        new_module.validate_org_config(after_cfg)
        if set(new_module.configured_org_workflow_refs(after_cfg)) != set(after_group['selected_workflows']):
            raise ValueError
    except Exception:
        raise Blocked('proposed_manager_policy_invalid') from None
    if (after_cfg.get('org_runner_group_id') != cfg.get('org_runner_group_id') or
            after_cfg.get('org_repository_ids') != cfg.get('org_repository_ids') or
            cfg.get('state_dir') != '/var/lib/ci-runner-manager/state' or
            {k: v for k, v in after_cfg.items() if k != 'org_execution_roles'} !=
                {k: v for k, v in cfg.items() if k != 'org_execution_roles'}):
        raise Blocked('proposed_manager_policy_changes_other_settings')
    role = after_cfg['org_execution_roles']['gate-backend-pilot']
    if role != {'workflow_ref': WORKFLOW_PIN, 'repositories': ['Tuinstra-DEV/gate'],
                'events': ['pull_request'], 'actors': [{'login': 'marcel-tuinstra', 'type': 'User'}]}:
        raise Blocked('proposed_pilot_role_differs')
    return cfg, after_cfg


def verify_operator_readiness(package: Path, checker, cfg: dict, old_module) -> None:
    """Re-run existing credential GETs and candidate checks before mutations."""
    try:
        checker.credential_checks(cfg, json.loads((package / 'manifest.json').read_text()), old_module)
        image, evidence = checker.gate_candidate_image(package)
        if image != CANDIDATE_SOURCE or evidence.get('sha256') != CANDIDATE_HASH:
            raise Blocked('candidate_preflight_target_differs')
        checker.candidate_image_php_check(image)
    except checker.Blocked as exc:
        raise Blocked(exc.code, exc.http_status) from None


def verify_candidate_and_source() -> None:
    secure_regular(HELPER, HELPER_HASH)
    link_info = SELECTOR.lstat()
    if not stat.S_ISLNK(link_info.st_mode) or link_info.st_uid != 0 or stat.S_IMODE(link_info.st_mode) != 0o777:
        raise Blocked('active_selector_metadata_differs')
    source_hash = SOURCE_IMAGE.name.removeprefix('ubuntu-24.04-runner-').removesuffix('.qcow2')
    verify_sealed_image(SOURCE_IMAGE, source_hash)
    if SELECTOR.resolve(strict=True) != SOURCE_IMAGE:
        raise Blocked('source_image_or_selector_differs')
    verify_candidate_source()
    if NEW_IMAGE.exists() or NEW_IMAGE.is_symlink():
        verify_sealed_candidate(NEW_IMAGE)


def verify_pre_activation_state(client, package: Path, old_module, new_module,
                                expected_config_bytes: bytes | None = None,
                                service_active: bool | None = None) -> tuple[bytes, dict, dict]:
    """Revalidate the original state immediately before any live mutation."""
    if service_active is not None:
        state = service_state()
        expected_state = 'active' if service_active else 'inactive'
        if (state['ActiveState'] != expected_state or
                (service_active and state['ExecMainStatus'] != '0')):
            raise Blocked('manager_service_state_changed')
    if sha256_file(MANAGER) != OLD_MANAGER_HASH or sha256_file(HELPER) != HELPER_HASH:
        raise Blocked('pre_activation_manager_or_helper_changed')
    if SELECTOR.resolve(strict=True) != SOURCE_IMAGE:
        raise Blocked('pre_activation_selector_changed')
    config_bytes = secure_regular(CONFIG)
    if expected_config_bytes is not None and config_bytes != expected_config_bytes:
        raise Blocked('manager_config_changed_while_waiting')
    cfg = parse_manager_config(config_bytes)
    api_url = cfg.get('github_api_url', 'https://api.github.com')
    if not isinstance(api_url, str) or api_url.rstrip('/') != 'https://api.github.com':
        raise Blocked('github_api_origin_not_allowed')
    group, repositories = read_group(client, cfg)
    _, after_cfg = config_and_group_preflight(package, old_module, new_module,
                                             config_bytes, group, repositories)
    before_refs = json.loads((package / 'group-before.json').read_text())['selected_workflows']
    if group.get('selected_workflows') != before_refs:
        raise Blocked('runner_group_ref_order_differs')
    pr = client.request('GET', REPO_PATH + '/pulls/444')
    if (pr.get('state') != 'open' or pr.get('head', {}).get('sha') != PREACTIVATION_HEAD_SHA or
            pr.get('base', {}).get('sha') != BASE_SHA or
            pr.get('head', {}).get('ref') != 'chore/DEV-54-compact-ci-pilot' or
            pr.get('base', {}).get('ref') != 'develop'):
        raise Blocked('gate_candidate_changed')
    merge_sha = pr.get('merge_commit_sha')
    if not isinstance(merge_sha, str) or not re.fullmatch(r'[a-f0-9]{40}', merge_sha):
        raise Blocked('gate_merge_commit_unavailable')
    merge = client.request('GET', REPO_PATH + '/git/commits/' + merge_sha)
    if (merge.get('sha') != merge_sha or len(merge.get('parents', [])) != 2 or
            {p.get('sha') for p in merge['parents']} != {PREACTIVATION_HEAD_SHA, BASE_SHA}):
        raise Blocked('gate_merge_parents_changed')
    return config_bytes, cfg, after_cfg


def verify_sealed_image(path: Path, expected_hash: str) -> None:
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != 0o444):
            raise Blocked('sealed_image_metadata_or_hash_differs')
        qemu_uid = int(safe_command(['id', '-u', 'libvirt-qemu']))
        qemu_gid = int(safe_command(['getent', 'group', 'kvm']).split(':')[2])
        valid_owner = (info.st_uid, info.st_gid) in {(0, 0), (qemu_uid, qemu_gid)}
        if not valid_owner or sha256_file(path) != expected_hash:
            raise Blocked('sealed_image_metadata_or_hash_differs')
    except Blocked: raise
    except Exception: raise Blocked('sealed_image_unavailable') from None


def verify_sealed_candidate(path: Path) -> None:
    verify_sealed_image(path, CANDIDATE_HASH)


def verify_candidate_source() -> os.stat_result:
    try:
        info = CANDIDATE_SOURCE.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 1000 or info.st_nlink != 1 or
                info.st_mode & 0o022 or sha256_file(CANDIDATE_SOURCE) != CANDIDATE_HASH):
            raise Blocked('candidate_source_metadata_or_hash_differs')
        return info
    except Blocked: raise
    except Exception: raise Blocked('candidate_source_unavailable') from None


def candidate_source_restored(promotion: dict) -> bool:
    """Finish or recognize the journal-bound original after an interrupted move."""
    source = Path(promotion['source'])
    try:
        info = source.lstat()
        owner = (info.st_uid, info.st_gid)
        original_owner = (promotion['source_uid'], promotion['source_gid'])
        original_metadata = (owner == original_owner and
                             stat.S_IMODE(info.st_mode) in {promotion['source_mode'], 0o444})
        just_moved_from_sealed = False
        if not original_metadata and stat.S_IMODE(info.st_mode) == 0o444:
            qemu_owner = (int(safe_command(['id', '-u', 'libvirt-qemu'])),
                          int(safe_command(['getent', 'group', 'kvm']).split(':')[2]))
            just_moved_from_sealed = owner == qemu_owner
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                not (original_metadata or just_moved_from_sealed) or
                info.st_size != promotion['source_size'] or info.st_mtime_ns != promotion['source_mtime_ns'] or
                sha256_file(source) != CANDIDATE_HASH):
            return False
        # Complete the metadata restoration if the previous process died
        # immediately after rename or chown. The content and timestamps are
        # pinned by the root-owned transaction journal and candidate digest.
        os.chown(source, promotion['source_uid'], promotion['source_gid'])
        os.chmod(source, promotion['source_mode'])
        os.utime(source, ns=(promotion['source_atime_ns'], promotion['source_mtime_ns']))
        restored = source.lstat()
        return (restored.st_uid == promotion['source_uid'] and
                restored.st_gid == promotion['source_gid'] and
                stat.S_IMODE(restored.st_mode) == promotion['source_mode'] and
                restored.st_size == promotion['source_size'] and
                restored.st_mtime_ns == promotion['source_mtime_ns'])
    except Exception:
        return False


def prepare_sealed_candidate() -> dict:
    """Promote our exact artifact without duplication; copy only across filesystems."""
    if NEW_IMAGE.exists() or NEW_IMAGE.is_symlink():
        verify_sealed_candidate(NEW_IMAGE)
        return {'mode': 'preexisting', 'source': str(CANDIDATE_SOURCE)}
    source_info = CANDIDATE_SOURCE.lstat()
    if (not stat.S_ISREG(source_info.st_mode) or source_info.st_uid != 1000 or
            source_info.st_nlink != 1 or source_info.st_mode & 0o022):
        raise Blocked('candidate_source_metadata_differs')
    if CANDIDATE_SOURCE.stat().st_dev == NEW_IMAGE.parent.stat().st_dev:
        if NEW_IMAGE.exists() or NEW_IMAGE.is_symlink():
            raise Blocked('sealed_candidate_path_occupied')
        metadata = {'mode': 'same_filesystem_move', 'source': str(CANDIDATE_SOURCE),
                    'source_uid': source_info.st_uid, 'source_gid': source_info.st_gid,
                    'source_mode': stat.S_IMODE(source_info.st_mode),
                    'source_atime_ns': source_info.st_atime_ns, 'source_mtime_ns': source_info.st_mtime_ns,
                    'source_size': source_info.st_size}
        moved = False
        try:
            rename_noreplace(CANDIDATE_SOURCE, NEW_IMAGE)
            moved = True
            qemu_uid = int(safe_command(['id', '-u', 'libvirt-qemu']))
            qemu_gid = int(safe_command(['getent', 'group', 'kvm']).split(':')[2])
            os.chown(NEW_IMAGE, qemu_uid, qemu_gid); os.chmod(NEW_IMAGE, 0o444)
            if sha256_file(NEW_IMAGE) != CANDIDATE_HASH:
                raise Blocked('sealed_candidate_move_hash_differs')
            return metadata
        except BaseException:
            try:
                if moved and NEW_IMAGE.exists() and not CANDIDATE_SOURCE.exists():
                    rename_noreplace(NEW_IMAGE, CANDIDATE_SOURCE)
                    os.chown(CANDIDATE_SOURCE, source_info.st_uid, source_info.st_gid)
                    os.chmod(CANDIDATE_SOURCE, stat.S_IMODE(source_info.st_mode))
                    os.utime(CANDIDATE_SOURCE, ns=(source_info.st_atime_ns, source_info.st_mtime_ns))
            except Exception:
                pass
            raise
    if shutil.disk_usage(NEW_IMAGE.parent).free < source_info.st_blocks * 512 + MIN_FREE_BYTES:
        raise Blocked('image_copy_would_breach_disk_floor')
    temp = NEW_IMAGE.with_name('.' + NEW_IMAGE.name + '.dev54-stage')
    if temp.exists() or temp.is_symlink():
        raise Blocked('sealed_candidate_temp_occupied')
    temp_created = False
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        temp_created = True
        with CANDIDATE_SOURCE.open('rb') as src, os.fdopen(fd, 'wb') as dst:
            shutil.copyfileobj(src, dst, 16 * 1024 * 1024)
            dst.flush(); os.fsync(dst.fileno())
        qemu_uid = int(safe_command(['id', '-u', 'libvirt-qemu']))
        qemu_gid = int(safe_command(['getent', 'group', 'kvm']).split(':')[2])
        os.chown(temp, qemu_uid, qemu_gid); os.chmod(temp, 0o444)
        if sha256_file(temp) != CANDIDATE_HASH:
            raise Blocked('sealed_candidate_copy_hash_differs')
        safe_command(['qemu-img', 'check', '-q', str(temp)], timeout=180)
        if shutil.disk_usage(NEW_IMAGE.parent).free < MIN_FREE_BYTES:
            raise Blocked('image_copy_breached_disk_floor')
        rename_noreplace(temp, NEW_IMAGE)
        dfd = os.open(NEW_IMAGE.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
        return {'mode': 'cross_filesystem_copy', 'source': str(CANDIDATE_SOURCE),
                'source_uid': source_info.st_uid, 'source_gid': source_info.st_gid,
                'source_mode': stat.S_IMODE(source_info.st_mode), 'source_atime_ns': source_info.st_atime_ns,
                'source_mtime_ns': source_info.st_mtime_ns, 'source_size': source_info.st_size}
    except Blocked:
        if temp_created: temp.unlink(missing_ok=True)
        raise
    except Exception:
        if temp_created: temp.unlink(missing_ok=True)
        raise Blocked('sealed_candidate_copy_failed') from None


def restore_candidate_source(promotion: dict, *, sealed_image_preexisting: bool = False) -> None:
    mode = promotion.get('mode')
    if mode == 'preexisting' or (mode == 'not_started' and sealed_image_preexisting):
        return
    if mode == 'not_started':
        source = Path(promotion['source'])
        if not NEW_IMAGE.exists():
            if candidate_source_restored(promotion):
                return
            raise Blocked('rollback_candidate_image_missing')
        verify_sealed_candidate(NEW_IMAGE)
        if source.exists() or source.is_symlink():
            if not source.is_file() or sha256_file(source) != CANDIDATE_HASH:
                raise Blocked('rollback_candidate_source_path_occupied')
            # A copy path leaves the validated source in place; remove only the
            # sealed file created by this transaction after restoring selector.
            NEW_IMAGE.unlink()
            return
        # A same-filesystem rename may have completed immediately before a
        # crash/journal update. Rehome that exact validated inode without overwrite.
        rename_noreplace(NEW_IMAGE, source)
        os.chown(source, promotion['source_uid'], promotion['source_gid'])
        os.chmod(source, promotion['source_mode'])
        os.utime(source, ns=(promotion['source_atime_ns'], promotion['source_mtime_ns']))
        if sha256_file(source) != CANDIDATE_HASH:
            raise Blocked('rollback_candidate_source_hash_differs')
        return
    if not NEW_IMAGE.exists():
        if mode in {'same_filesystem_move', 'cross_filesystem_copy'} and candidate_source_restored(promotion):
            return
        raise Blocked('rollback_candidate_image_differs')
    verify_sealed_candidate(NEW_IMAGE)
    if mode == 'same_filesystem_move':
        source = Path(promotion['source'])
        if source.exists() or source.is_symlink():
            raise Blocked('rollback_candidate_source_path_occupied')
        if source.parent.stat().st_dev != NEW_IMAGE.stat().st_dev:
            raise Blocked('rollback_candidate_filesystem_changed')
        rename_noreplace(NEW_IMAGE, source)
        os.chown(source, promotion['source_uid'], promotion['source_gid'])
        os.chmod(source, promotion['source_mode'])
        os.utime(source, ns=(promotion['source_atime_ns'], promotion['source_mtime_ns']))
        if sha256_file(source) != CANDIDATE_HASH:
            raise Blocked('rollback_candidate_source_hash_differs')
        return
    if mode == 'cross_filesystem_copy':
        # The validated source still exists on the original filesystem. Remove
        # only this transaction's verified copy after the old selector is back.
        NEW_IMAGE.unlink()
        return
    raise Blocked('rollback_candidate_promotion_unknown')


def read_group(client, cfg: dict) -> tuple[dict, dict]:
    group = client.request('GET', GROUP_PATH)
    repositories = client.request('GET', GROUP_PATH + '/repositories?per_page=100&page=1')
    return group, repositories


def patch_group(client, selected_workflows: list[str]) -> None:
    try:
        client.request('PATCH', GROUP_PATH, {'selected_workflows': selected_workflows})
        result = client.request('GET', GROUP_PATH)
    except Exception as exc:
        match = re.search(r'HTTP ([0-9]{3})', str(exc))
        raise Blocked('runner_group_patch_failed', int(match.group(1)) if match else None) from None
    if (not group_policy_matches(result) or
            set(result.get('selected_workflows', [])) != set(selected_workflows) or
            len(result.get('selected_workflows', [])) != len(selected_workflows)):
        raise Blocked('runner_group_patch_readback_differs')


def load_operator_checker(package: Path):
    for name in ('operator_check.py', 'read_effective.py'):
        read_input(name)
    sys.path.insert(0, str(package))
    return load_module(package / 'operator_check.py', 'dev54_pinned_operator_check')


def existing_manager_client(cfg: dict, credential_checker, manager_module):
    api_url = cfg.get('github_api_url', 'https://api.github.com')
    if not isinstance(api_url, str) or api_url.rstrip('/') != 'https://api.github.com':
        raise Blocked('github_api_origin_not_allowed')
    token = credential_checker.loaded_manager_credential(cfg, manager_module)
    return token, manager_module.GitHubClient(token, api_url=api_url)


def validate_pr_and_runs(client, activated_at: int, seen: dict[int, int]) -> dict[int, dict]:
    pr = client.request('GET', REPO_PATH + '/pulls/444')
    if (pr.get('state') != 'open' or pr.get('base', {}).get('sha') != BASE_SHA or
            pr.get('head', {}).get('ref') != 'chore/DEV-54-compact-ci-pilot' or
            pr.get('base', {}).get('ref') != 'develop'):
        raise Blocked('gate_candidate_changed')
    merge_sha = pr.get('merge_commit_sha')
    if not isinstance(merge_sha, str) or not re.fullmatch(r'[a-f0-9]{40}', merge_sha):
        raise Blocked('gate_merge_commit_unavailable')
    merge = client.request('GET', REPO_PATH + '/git/commits/' + merge_sha)
    current_head = pr.get('head', {}).get('sha')
    if current_head == PREACTIVATION_HEAD_SHA:
        if (merge.get('sha') != merge_sha or len(merge.get('parents', [])) != 2 or
                {p.get('sha') for p in merge['parents']} != {PREACTIVATION_HEAD_SHA, BASE_SHA}):
            raise Blocked('gate_merge_parents_changed')
        raise Blocked('gate_caller_not_published')
    if current_head != HEAD_SHA:
        raise Blocked('gate_candidate_changed')
    if (merge.get('sha') != merge_sha or len(merge.get('parents', [])) != 2 or
            {p.get('sha') for p in merge['parents']} != {HEAD_SHA, BASE_SHA}):
        raise Blocked('gate_merge_parents_changed')
    found = {}
    for workflow_id, (path, name, expected_job) in WORKFLOWS.items():
        runs = client.request('GET', f'{REPO_PATH}/actions/workflows/{workflow_id}/runs?event=pull_request&per_page=100')
        values = runs.get('workflow_runs') if isinstance(runs, dict) else None
        if not isinstance(values, list):
            raise Blocked('gate_run_list_invalid')
        matching = []
        for run in values:
            if (type(run.get('id')) is not int or run['id'] <= seen[workflow_id] or
                    run.get('event') != 'pull_request' or run.get('head_sha') != HEAD_SHA or
                    run.get('head_branch') != 'chore/DEV-54-compact-ci-pilot' or
                    run.get('workflow_id') != workflow_id or run.get('path', '').split('@', 1)[0] != path or
                    run.get('name') != name or run.get('run_attempt') != 1):
                continue
            created = run.get('created_at')
            if not isinstance(created, str): continue
            try:
                created_epoch = int(datetime.fromisoformat(created.replace('Z', '+00:00')).timestamp())
            except ValueError: continue
            if created_epoch < activated_at: continue
            pull_requests = run.get('pull_requests')
            if (not isinstance(pull_requests, list) or
                    not any(isinstance(item, dict) and item.get('number') == 444 for item in pull_requests)):
                continue
            matching.append(run)
        if len(matching) > 1:
            raise Blocked('multiple_gate_candidate_runs')
        if not matching:
            continue
        found[workflow_id] = attach_backend_job(client, workflow_id, matching[0])
    return found


def attach_backend_job(client, workflow_id: int, run: dict) -> dict:
    expected_job = WORKFLOWS[workflow_id][2]
    jobs = client.request('GET', f'{REPO_PATH}/actions/runs/{run["id"]}/jobs?per_page=100')
    job_rows = jobs.get('jobs') if isinstance(jobs, dict) else None
    if not isinstance(job_rows, list):
        raise Blocked('gate_jobs_list_invalid')
    backend = [job for job in job_rows if job.get('name') == expected_job]
    if len(backend) > 1:
        raise Blocked('gate_backend_job_ambiguous')
    if not backend:
        run['backend_job_status'] = 'missing_at_terminal' if run.get('status') == 'completed' else 'not_created_yet'
        run['backend_role_label_verified'] = False if workflow_id == 419883688 and run.get('status') == 'completed' else None
        return run
    backend_job = backend[0]
    run['backend_job_status'] = backend_job.get('status')
    run['backend_job_conclusion'] = backend_job.get('conclusion')
    run['backend_runner_id'] = backend_job.get('runner_id')
    run['backend_runner_name'] = backend_job.get('runner_name')
    run['backend_runner_group'] = backend_job.get('runner_group_name')
    if workflow_id == 419883688:
        expected_runner_name = f'sanctuary-{backend_job.get("id")}'
        labels = backend_job.get('labels')
        assigned = (type(backend_job.get('id')) is int and type(backend_job.get('runner_id')) is int and
                    backend_job.get('runner_id', 0) > 0)
        if assigned:
            run['backend_role_label_verified'] = (
                backend_job.get('runner_name') == expected_runner_name and
                backend_job.get('runner_group_name') == 'sanctuary-trusted-verification' and
                backend_job.get('runner_group_id') == 4 and isinstance(labels, list) and
                {'self-hosted', 'ci-gate-backend-pilot', 'sanctuary-gate'}.issubset(labels))
        elif run.get('status') == 'completed' or backend_job.get('status') in {'completed', 'in_progress'}:
            run['backend_role_label_verified'] = False
        else:
            run['backend_role_label_verified'] = None
    return run


def refresh_latched_run(client, workflow_id: int, run_id: int) -> dict:
    run = client.request('GET', f'{REPO_PATH}/actions/runs/{run_id}')
    path, name, _expected_job = WORKFLOWS[workflow_id]
    if (run.get('id') != run_id or run.get('workflow_id') != workflow_id or
            run.get('event') != 'pull_request' or run.get('head_sha') != HEAD_SHA or
            run.get('head_branch') != 'chore/DEV-54-compact-ci-pilot' or
            run.get('path', '').split('@', 1)[0] != path or run.get('name') != name or
            run.get('run_attempt') != 1):
        raise Blocked('latched_gate_run_identity_changed')
    return attach_backend_job(client, workflow_id, run)


def terminal_runs_ready(runs: dict[int, dict], *, discovery_expired: bool) -> bool:
    if len(runs) == len(WORKFLOWS) and all(run.get('status') == 'completed' for run in runs.values()):
        return True
    # Once the bounded discovery period expires, a missing workflow is a
    # failed-trial result. Every run that did start must still be terminal.
    return discovery_expired and all(run.get('status') == 'completed' for run in runs.values())


def workflow_high_watermarks(client) -> dict[int, int]:
    result = {}
    for workflow_id in WORKFLOWS:
        data = client.request('GET', f'{REPO_PATH}/actions/workflows/{workflow_id}/runs?event=pull_request&per_page=100')
        values = data.get('workflow_runs') if isinstance(data, dict) else None
        if not isinstance(values, list) or any(type(run.get('id')) is not int for run in values):
            raise Blocked('gate_run_list_invalid')
        result[workflow_id] = max([run['id'] for run in values] or [0])
    return result


def source_fingerprints() -> dict:
    info = CONFIG.stat()
    return {
        'manager_sha256': sha256_file(MANAGER),
        'helper_sha256': sha256_file(HELPER),
        'config_sha256': sha256_file(CONFIG),
        'config_uid': info.st_uid,
        'config_gid': info.st_gid,
        'config_mode': stat.S_IMODE(info.st_mode),
        'selector_target': str(SELECTOR.resolve(strict=True)),
        'active_image_sha256': sha256_file(SELECTOR.resolve(strict=True)),
    }


def wait_until_empty(timeout_seconds: int, stage: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            ensure_empty()
            return
        except Blocked as exc:
            if exc.code != 'runner_window_not_empty':
                raise
            write_json(STATUS, {'phase': stage, 'active_work_untouched': True,
                                'manager_service_left_running': True}, 0o644)
            if time.monotonic() >= deadline:
                raise Blocked('runner_window_wait_timed_out') from None
            time.sleep(POLL_SECONDS)


def stop_manager_when_empty(stage: str) -> None:
    """Stop admission only for a verified empty window, reopening on a race."""
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        ensure_empty()
        safe_command(['systemctl', 'stop', SERVICE])
        wait_service(False)
        try:
            ensure_empty()
            return
        except Blocked as exc:
            try:
                safe_command(['systemctl', 'start', SERVICE])
                wait_service(True)
            except Exception:
                raise Blocked('manager_restart_after_empty_race_failed') from None
            if exc.code != 'runner_window_not_empty':
                raise
            remaining = max(0, int(deadline - time.monotonic()))
            wait_until_empty(remaining, stage)


def make_backup() -> tuple[Path, dict]:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory = BACKUP_ROOT / stamp
    try:
        BACKUP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_info = BACKUP_ROOT.lstat()
        if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != 0 or
                stat.S_IMODE(root_info.st_mode) != 0o700):
            raise Blocked('backup_root_unsafe')
        directory.mkdir(mode=0o700)
        if directory.stat().st_uid != 0 or stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise Blocked('backup_directory_unsafe')
    except Blocked: raise
    except Exception: raise Blocked('backup_directory_unavailable') from None
    original = source_fingerprints()
    manager_info = MANAGER.stat()
    config_info = CONFIG.stat()
    for source, name in ((MANAGER, 'manager.before'), (CONFIG, 'manager.toml.before')):
        data = secure_regular(source, original['manager_sha256'] if source == MANAGER else original['config_sha256'])
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    for name in ('group-before.json', 'group-after.json', 'repositories-before.json'):
        data = read_input(name)
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    write_json(directory / 'before.json', {
        **original, 'selector_link': os.readlink(SELECTOR),
        'manager_uid': manager_info.st_uid, 'manager_gid': manager_info.st_gid,
        'manager_mode': stat.S_IMODE(manager_info.st_mode),
        'config_uid': config_info.st_uid, 'config_gid': config_info.st_gid,
        'config_mode': stat.S_IMODE(config_info.st_mode),
        'group_ref_count': 6, 'group_snapshot': 'group-before.json',
    })
    # Use the exact protected snapshot persisted for crash recovery. The
    # fingerprint-only return value omits ownership and mode needed to restore.
    return directory, json.loads((directory / 'before.json').read_text())


def atomic_selector(target: Path) -> None:
    temporary = SELECTOR.with_name('.ubuntu-24.04-runner.qcow2.dev54')
    try:
        if temporary.exists() or temporary.is_symlink():
            raise Blocked('selector_temp_occupied')
        temporary.symlink_to(target)
        os.chown(temporary, 0, 0, follow_symlinks=False)
        os.replace(temporary, SELECTOR)
        dfd = os.open(SELECTOR.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    except Blocked: raise
    except Exception: raise Blocked('selector_update_failed') from None


def wait_service(active: bool, timeout: int = 45) -> None:
    expected = 'active' if active else 'inactive'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if service_state()['ActiveState'] == expected:
                if not active or (service_state()['ExecMainStatus'] == '0' and service_state()['NRestarts'] == '0'):
                    return
        except Blocked: pass
        time.sleep(1)
    raise Blocked('manager_service_state_timeout')


def install_candidate_manager_and_config(config_bytes: bytes) -> None:
    config_after = render_pilot_config(config_bytes)
    current = MANAGER.stat(); candidate = read_staged_manager()
    compile(candidate, str(MANAGER), 'exec')
    atomic_write(MANAGER, candidate, current.st_uid, current.st_gid, stat.S_IMODE(current.st_mode))
    conf = CONFIG.stat()
    atomic_write(CONFIG, config_after, conf.st_uid, conf.st_gid, stat.S_IMODE(conf.st_mode))


def restore_old_files(backup: Path, before: dict) -> None:
    old_manager = secure_regular(backup / 'manager.before', before['manager_sha256'])
    old_config = secure_regular(backup / 'manager.toml.before', before['config_sha256'])
    atomic_write(MANAGER, old_manager, before['manager_uid'], before['manager_gid'], before['manager_mode'])
    atomic_write(CONFIG, old_config, before['config_uid'], before['config_gid'], before['config_mode'])
    atomic_selector(Path(before['selector_target']))


def expected_group_refs(package: Path, after: bool) -> list[str]:
    filename = 'group-after.json' if after else 'group-before.json'
    return json.loads((package / filename).read_text())['selected_workflows']


def backed_group_refs(backup: Path, after: bool) -> list[str]:
    filename = 'group-after.json' if after else 'group-before.json'
    return json.loads((backup / filename).read_text())['selected_workflows']


def repository_map(repositories: dict) -> dict:
    rows = repositories.get('repositories') if isinstance(repositories, dict) else None
    if not isinstance(rows, list):
        raise Blocked('runner_group_repositories_invalid')
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or row.get('private') is not True or
                not isinstance(row.get('full_name'), str) or type(row.get('id')) is not int or
                row['full_name'] in result):
            raise Blocked('runner_group_repositories_invalid')
        result[row['full_name']] = row['id']
    if repositories.get('total_count') != len(result):
        raise Blocked('runner_group_repositories_invalid')
    return result


def group_policy_matches(group: dict) -> bool:
    return (isinstance(group, dict) and group.get('id') == 4 and
            group.get('name') == 'sanctuary-trusted-verification' and
            group.get('visibility') == 'selected' and group.get('default') is False and
            group.get('inherited') is False and group.get('allows_public_repositories') is False and
            group.get('restricted_to_workflows') is True and
            isinstance(group.get('selected_workflows'), list) and
            all(isinstance(ref, str) for ref in group['selected_workflows']))


def verify_restored_files(before: dict) -> None:
    """Check original bytes and metadata before declaring recovery complete."""
    for path, prefix in ((MANAGER, 'manager'), (CONFIG, 'config')):
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) !=
                (before[prefix + '_uid'], before[prefix + '_gid'], before[prefix + '_mode']) or
                sha256_file(path) != before[prefix + '_sha256']):
            raise Blocked('rollback_restored_file_fingerprint_differs')


def verify_running_state(new: bool, backup: Path, manager_module) -> None:
    expected_hash = NEW_MANAGER_HASH if new else OLD_MANAGER_HASH
    expected_image = NEW_IMAGE if new else SOURCE_IMAGE
    if sha256_file(MANAGER) != expected_hash or SELECTOR.resolve(strict=True) != expected_image:
        raise Blocked('live_fingerprint_differs')
    if sha256_file(HELPER) != HELPER_HASH:
        raise Blocked('host_helper_hash_differs')
    expected_image_hash = CANDIDATE_HASH if new else expected_image.name.removeprefix('ubuntu-24.04-runner-').removesuffix('.qcow2')
    if sha256_file(expected_image) != expected_image_hash:
        raise Blocked('live_image_hash_differs')
    cfg = parse_manager_config(CONFIG.read_bytes())
    try:
        manager_module.validate_org_config(cfg)
        refs = manager_module.configured_org_workflow_refs(cfg)
    except Exception:
        raise Blocked('live_manager_policy_invalid') from None
    expected = set(backed_group_refs(backup, new))
    if set(refs) != expected or len(refs) != len(expected):
        raise Blocked('live_manager_workflow_refs_differ')
    if not new:
        verify_restored_files(load_backup_before(backup, {}))


def restore_transaction(client, package: Path, backup: Path, before: dict,
                         old_module, new_module, *, activation_failure: bool = False,
                         allow_original_group: bool = False) -> None:
    # Refuse to overwrite any concurrent edit. The only allowed live state is
    # the exact new manager/config/group/image fingerprint from this trial.
    active = source_fingerprints()
    expected_after = json.loads((JOURNAL if JOURNAL.exists() else backup / 'transaction.json').read_text())
    if (active['manager_sha256'] != NEW_MANAGER_HASH or active['selector_target'] != str(NEW_IMAGE) or
            active['config_sha256'] != expected_after.get('config_after_sha256') or
            active['active_image_sha256'] != CANDIDATE_HASH or active['helper_sha256'] != HELPER_HASH):
        raise Blocked('rollback_fingerprint_changed')
    live_group, live_repos = read_group(client, {})
    live_refs = live_group.get('selected_workflows', [])
    valid_group_states = [backed_group_refs(backup, True)]
    if allow_original_group:
        valid_group_states.append(backed_group_refs(backup, False))
    if (not group_policy_matches(live_group) or
            not any(len(live_refs) == len(refs) and set(live_refs) == set(refs)
                    for refs in valid_group_states)):
        raise Blocked('rollback_group_fingerprint_changed')
    if repository_map(live_repos) != json.loads((backup / 'repositories-before.json').read_text()):
        raise Blocked('rollback_repository_fingerprint_changed')
    try:
        stop_manager_when_empty('rollback_waiting_for_idle')
        active = source_fingerprints()
        if (active['manager_sha256'] != NEW_MANAGER_HASH or
                active['selector_target'] != str(NEW_IMAGE) or
                active['config_sha256'] != expected_after.get('config_after_sha256') or
                active['active_image_sha256'] != CANDIDATE_HASH or
                active['helper_sha256'] != HELPER_HASH):
            raise Blocked('rollback_fingerprint_changed_after_drain')
        group_after_drain, repos_after_drain = read_group(client, {})
        drained_refs = group_after_drain.get('selected_workflows', [])
        if (not group_policy_matches(group_after_drain) or
                not any(len(drained_refs) == len(refs) and set(drained_refs) == set(refs)
                        for refs in valid_group_states) or
                repository_map(repos_after_drain) !=
                json.loads((backup / 'repositories-before.json').read_text())):
            raise Blocked('rollback_group_fingerprint_changed_after_drain')
        before_refs = backed_group_refs(backup, False)
        if not (len(drained_refs) == len(before_refs) and set(drained_refs) == set(before_refs)):
            patch_group(client, before_refs)
        restore_old_files(backup, before)
        restore_candidate_source(expected_after['candidate_promotion'],
                                sealed_image_preexisting=expected_after.get('sealed_image_preexisting', False))
        safe_command(['systemctl', 'start', SERVICE])
        wait_service(True)
        verify_running_state(False, backup, old_module)
        after_group, repos = read_group(client, {})
        if (not group_policy_matches(after_group) or
                set(after_group.get('selected_workflows', [])) != set(backed_group_refs(backup, False))):
            raise Blocked('rollback_group_readback_differs')
        expected_repositories = json.loads((backup / 'repositories-before.json').read_text())
        if repository_map(repos) != expected_repositories:
            raise Blocked('rollback_repository_readback_differs')
        write_json(STATUS, {'phase': 'rolled_back', 'manager_restored': True,
                            'source_image_restored': True, 'runner_group_restored': True,
                            'candidate_source_restored': expected_after['candidate_promotion'].get('mode') != 'preexisting',
                            'host_helper_unchanged': True,
                            'backup_directory': str(backup)}, 0o644)
        state = json.loads(JOURNAL.read_text())
        state['phase'] = 'rolled_back'
        write_json(JOURNAL, state)
    except Exception:
        try:
            if service_state()['ActiveState'] != 'active':
                safe_command(['systemctl', 'start', SERVICE])
                wait_service(True)
        except Exception:
            write_json(STATUS, {'phase': 'rollback_recovery_required',
                                'blocker': 'manager_restart_after_rollback_failure'}, 0o644)
        raise


def activate(package: Path) -> tuple[dict, Path, dict, object, object, str]:
    if os.geteuid() != 0 or not sys.stdin.isatty():
        raise Blocked('existing_operator_sudo_tty_required')
    if JOURNAL.exists() or JOURNAL.is_symlink():
        raise Blocked('previous_transaction_journal_present')
    if sha256_file(MANAGER) != OLD_MANAGER_HASH or sha256_file(HELPER) != HELPER_HASH:
        raise Blocked('deployed_manager_or_helper_hash_differs')
    initial_service = service_state()
    if initial_service['ActiveState'] != 'active' or initial_service['ExecMainStatus'] != '0':
        raise Blocked('manager_service_unhealthy')
    if SELECTOR.resolve(strict=True) != SOURCE_IMAGE:
        raise Blocked('active_source_image_differs')
    staged = read_staged_manager()
    compile(staged, str(MANAGER), 'exec')
    old_module = load_module(MANAGER, 'dev54_gate_old_manager')
    new_module = load_module(STAGED_MANAGER, 'dev54_gate_new_manager')
    credential_checker = load_operator_checker(package)
    config_bytes = secure_regular(CONFIG)
    group_before = json.loads((package / 'group-before.json').read_text())
    group_after = json.loads((package / 'group-after.json').read_text())
    repos_snapshot = json.loads((package / 'repositories-before.json').read_text())
    cfg = parse_manager_config(config_bytes)
    token, client = existing_manager_client(cfg, credential_checker, old_module)
    config_bytes, cfg, after_cfg = verify_pre_activation_state(client, package, old_module, new_module,
                                                               service_active=True)
    wait_until_empty(MAX_WAIT_SECONDS, 'waiting_for_empty_activation_window')
    if SIGNAL_CONTROL['requested'] is not None:
        raise Blocked('operator_interrupted_before_activation')
    config_bytes, cfg, after_cfg = verify_pre_activation_state(client, package, old_module, new_module,
                                                               expected_config_bytes=config_bytes,
                                                               service_active=True)
    verify_candidate_and_source()
    verify_operator_readiness(package, credential_checker, cfg, old_module)
    print(json.dumps({'phase': 'read_only_preflight_verified', 'all_checks_green': True,
                      'candidate_image_sha256': CANDIDATE_HASH}, separators=(',', ':')), flush=True)
    # Do not pause admission or copy the image until an empty window is observed.
    backup, before = make_backup()
    source_info = CANDIDATE_SOURCE.stat()
    state = {'phase': 'prepared', 'backup': str(backup), 'before': before,
             'candidate_promotion': {'mode': 'not_started', 'source': str(CANDIDATE_SOURCE),
                'source_uid': source_info.st_uid, 'source_gid': source_info.st_gid,
                'source_mode': stat.S_IMODE(source_info.st_mode), 'source_atime_ns': source_info.st_atime_ns,
                'source_mtime_ns': source_info.st_mtime_ns, 'source_size': source_info.st_size},
             'sealed_image_preexisting': NEW_IMAGE.exists(),
             'activated_at': 0, 'before_run_ids': {},
             'config_after_sha256': hashlib.sha256(render_pilot_config(config_bytes)).hexdigest(),
             'group_before_refs': group_before['selected_workflows'],
             'group_after_refs': group_after['selected_workflows']}
    write_json(backup / 'transaction.json', state)
    write_json(JOURNAL, state)
    SIGNAL_CONTROL['abort_enabled'] = True
    prechange_drift = False
    activation_mutations_started = False
    try:
        if SIGNAL_CONTROL['requested'] is not None:
            raise OperatorInterrupt('operator requested interruption before activation')
        while True:
            wait_until_empty(MAX_WAIT_SECONDS, 'waiting_for_empty_activation_window')
            try:
                config_bytes, cfg, after_cfg = verify_pre_activation_state(
                    client, package, old_module, new_module, expected_config_bytes=config_bytes,
                    service_active=True)
            except Exception:
                prechange_drift = True
                raise
            safe_command(['systemctl', 'stop', SERVICE])
            wait_service(False)
            try:
                ensure_empty()
            except Blocked as exc:
                if exc.code != 'runner_window_not_empty':
                    prechange_drift = True
                    safe_command(['systemctl', 'start', SERVICE]); wait_service(True)
                    raise
                # A job won the race between the empty observation and stop.
                # Resume service untouched and wait for another safe window.
                safe_command(['systemctl', 'start', SERVICE]); wait_service(True)
                continue
            try:
                config_bytes, cfg, after_cfg = verify_pre_activation_state(
                    client, package, old_module, new_module, expected_config_bytes=config_bytes,
                    service_active=False)
            except Exception:
                prechange_drift = True
                safe_command(['systemctl', 'start', SERVICE]); wait_service(True)
                raise
            break
        candidate_promotion = prepare_sealed_candidate()
        state['candidate_promotion'] = candidate_promotion
        state['before_run_ids'] = workflow_high_watermarks(client)
        state['activated_at'] = int(time.time())
        write_json(JOURNAL, state)
        try:
            ensure_empty()
        except Blocked as exc:
            if exc.code != 'runner_window_not_empty': raise
            raise Blocked('runner_window_changed_after_manager_stop') from None
        activation_mutations_started = True
        patch_group(client, group_after['selected_workflows'])
        install_candidate_manager_and_config(config_bytes)
        atomic_selector(NEW_IMAGE)
        if sha256_file(MANAGER) != NEW_MANAGER_HASH or hashlib.sha256(CONFIG.read_bytes()).hexdigest() != state['config_after_sha256']:
            raise Blocked('activation_file_readback_differs')
        safe_command(['systemctl', 'start', SERVICE])
        wait_service(True)
        verify_running_state(True, backup, new_module)
        live_group, live_repos = read_group(client, after_cfg)
        if (not group_policy_matches(live_group) or
                set(live_group.get('selected_workflows', [])) != set(group_after['selected_workflows']) or
                repository_map(live_repos) != repos_snapshot):
            raise Blocked('activation_group_readback_differs')
        activated_at = int(time.time())
        state.update({'phase': 'active_watching', 'activated_at': activated_at})
        write_json(JOURNAL, state)
        write_json(STATUS, {'phase': 'active_watching', 'candidate_image_sha256': CANDIDATE_HASH,
                            'manager_role_active': True, 'runner_group_refs': 7,
                            'gate_head': HEAD_SHA, 'awaiting_workflows': sorted(WORKFLOWS)}, 0o644)
        print(json.dumps({'phase': 'active_watching', 'candidate_image_sha256': CANDIDATE_HASH,
                          'runner_group_refs': 7, 'manager_role_active': True}, separators=(',', ':')), flush=True)
        SIGNAL_CONTROL['abort_enabled'] = False
        return state, backup, before, old_module, new_module, token
    except BaseException:
        SIGNAL_CONTROL['abort_enabled'] = False
        if prechange_drift:
            # We have not changed the runner group, manager, selector, or
            # candidate location. Preserve any concurrent operator changes.
            try:
                if service_state()['ActiveState'] != 'active':
                    safe_command(['systemctl', 'start', SERVICE]); wait_service(True)
                state['phase'] = 'aborted_no_live_changes'
                write_json(backup / 'transaction.json', state)
                write_json(JOURNAL, state)
                write_json(STATUS, {'phase': 'activation_aborted_prechange_drift',
                                    'manager_service_running': service_state()['ActiveState'] == 'active'}, 0o644)
            except Exception:
                write_json(STATUS, {'phase': 'activation_recovery_required',
                                    'blocker': 'manager_restart_after_prechange_drift_failed'}, 0o644)
            raise
        if not activation_mutations_started:
            # No remote or active selector/config change has begun. Restore
            # only the staged image move and reopen the original manager.
            try:
                promotion = state.get('candidate_promotion', {})
                if (promotion.get('mode') not in {None, 'preexisting'} and
                        not (promotion.get('mode') == 'not_started' and
                             state.get('sealed_image_preexisting', False))):
                    restore_candidate_source(promotion,
                                             sealed_image_preexisting=state.get('sealed_image_preexisting', False))
                if service_state()['ActiveState'] != 'active':
                    safe_command(['systemctl', 'start', SERVICE]); wait_service(True)
                state['phase'] = 'aborted_no_live_changes'
                write_json(backup / 'transaction.json', state)
                write_json(JOURNAL, state)
                write_json(STATUS, {'phase': 'activation_aborted_before_live_mutation',
                                    'manager_service_running': service_state()['ActiveState'] == 'active'}, 0o644)
            except Exception:
                write_json(STATUS, {'phase': 'activation_recovery_required',
                                    'blocker': 'manager_or_candidate_recovery_failed'}, 0o644)
            raise
        # Restore only after every admitted VM has finished. If the stop raced
        # a new lease, resume the unchanged/new manager and wait instead of
        # touching a running job.
        try:
            if service_state()['ActiveState'] != 'active':
                safe_command(['systemctl', 'start', SERVICE]); wait_service(True)
            wait_until_empty(MAX_WAIT_SECONDS, 'activation_abort_waiting_for_idle')
            stop_manager_when_empty('activation_abort_waiting_for_idle')
            current_group, _ = read_group(client, {})
            current_refs = current_group.get('selected_workflows', [])
            if set(current_refs) not in (set(group_before['selected_workflows']), set(group_after['selected_workflows'])):
                raise Blocked('activation_abort_group_fingerprint_changed')
            live = source_fingerprints()
            if (live['manager_sha256'] not in {OLD_MANAGER_HASH, NEW_MANAGER_HASH} or
                    live['config_sha256'] not in {before['config_sha256'], state['config_after_sha256']} or
                    live['selector_target'] not in {str(SOURCE_IMAGE), str(NEW_IMAGE)} or
                    live['helper_sha256'] != HELPER_HASH):
                raise Blocked('activation_abort_live_fingerprint_changed')
            if set(current_refs) != set(group_before['selected_workflows']):
                patch_group(client, group_before['selected_workflows'])
            restore_old_files(backup, before)
            if state.get('candidate_promotion', {}).get('mode') not in {None, 'not_started'}:
                restore_candidate_source(state['candidate_promotion'],
                                         sealed_image_preexisting=state.get('sealed_image_preexisting', False))
            safe_command(['systemctl', 'start', SERVICE])
            wait_service(True)
            verify_running_state(False, backup, old_module)
            state['phase'] = 'rolled_back'
            write_json(backup / 'transaction.json', state)
            write_json(JOURNAL, state)
            write_json(STATUS, {'phase': 'activation_aborted_and_restored', 'live_state_restored': True}, 0o644)
        except Exception:
            try:
                if service_state()['ActiveState'] != 'active':
                    safe_command(['systemctl', 'start', SERVICE]); wait_service(True)
                write_json(STATUS, {'phase': 'activation_recovery_required',
                                    'manager_service_running': service_state()['ActiveState'] == 'active',
                                    'recovery': 'run fixed --rollback after reviewing protected journal'}, 0o644)
            except Exception:
                write_json(STATUS, {'phase': 'activation_recovery_required',
                                    'blocker': 'manager_restart_after_activation_abort_failed'}, 0o644)
        raise


def watch_and_rollback(package: Path, state: dict, backup: Path, before: dict,
                       old_module, new_module, token: str) -> bool:
    client = new_module.GitHubClient(token, api_url='https://api.github.com')
    seen = state['before_run_ids']
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    drain_deadline = deadline + MAX_WAIT_SECONDS
    latched = {}
    watch_failure = None
    discovery_expired = False
    while time.monotonic() < deadline:
        phase = 'active_watching'
        if SIGNAL_CONTROL['requested'] is not None:
            watch_failure = watch_failure or 'operator_or_terminal_interrupt'
            discovery_expired = True
            break
        try:
            found = validate_pr_and_runs(client, state['activated_at'], seen)
            for workflow_id, current in found.items():
                previous = latched.get(workflow_id)
                if previous and current.get('id') != previous.get('id'):
                    watch_failure = watch_failure or 'multiple_gate_candidate_runs'
                    continue
                if previous and previous.get('backend_role_label_verified') is True and \
                        current.get('backend_role_label_verified') is not True:
                    for key in ('backend_role_label_verified', 'backend_runner_id', 'backend_runner_name',
                                'backend_runner_group'):
                        current[key] = previous.get(key)
                latched[workflow_id] = current
            if len(latched) == len(WORKFLOWS) and all(item.get('status') == 'completed' for item in latched.values()):
                break
        except Blocked as exc:
            if exc.code == 'gate_caller_not_published':
                phase = 'awaiting_caller_publication'
            elif exc.code in {'gate_candidate_changed', 'gate_merge_commit_unavailable', 'gate_merge_parents_changed'}:
                watch_failure = watch_failure or exc.code
                discovery_expired = True
                break
            else:
                watch_failure = watch_failure or exc.code
        except Exception:
            watch_failure = watch_failure or 'github_read_failed'
        write_json(STATUS, {'phase': phase, 'candidate_image_sha256': CANDIDATE_HASH,
                            'runs_completed': sorted(wid for wid, item in latched.items()
                                                     if item.get('status') == 'completed'),
                            'runs_latched': sorted(latched), 'required_workflows': sorted(WORKFLOWS),
                            'watch_failure': watch_failure}, 0o644)
        time.sleep(POLL_SECONDS)
    discovery_expired = discovery_expired or time.monotonic() >= deadline
    timed_out = time.monotonic() >= deadline
    # After discovery timeout a missing run is a failed trial, but any run
    # already latched is polled by its immutable ID until it is terminal.
    # This also works if PR #444 moves to a different head mid-trial.
    while not terminal_runs_ready(latched, discovery_expired=discovery_expired):
        discovery_expired = discovery_expired or time.monotonic() >= deadline
        write_json(STATUS, {'phase': 'rollback_waiting_for_gate_runs', 'timed_out': timed_out,
                            'watch_failure': watch_failure, 'runs_latched': sorted(latched),
                            'runs_completed': sorted(wid for wid, item in latched.items()
                                                     if item.get('status') == 'completed')}, 0o644)
        try:
            for workflow_id, previous in list(latched.items()):
                if previous.get('status') == 'completed':
                    continue
                current = refresh_latched_run(client, workflow_id, previous['id'])
                if previous.get('backend_role_label_verified') is True and \
                        current.get('backend_role_label_verified') is not True:
                    for key in ('backend_role_label_verified', 'backend_runner_id', 'backend_runner_name',
                                'backend_runner_group'):
                        current[key] = previous.get(key)
                latched[workflow_id] = current
        except Exception:
            watch_failure = watch_failure or 'github_read_failed'
        time.sleep(POLL_SECONDS)
        timed_out = timed_out or time.monotonic() >= deadline
        if time.monotonic() >= drain_deadline and not terminal_runs_ready(latched, discovery_expired=True):
            write_json(STATUS, {'phase': 'rollback_blocked_run_status_unavailable',
                                'watch_failure': watch_failure or 'gate_run_not_terminal',
                                'runs_latched': sorted(latched),
                                'recovery': 'use fixed rollback only after exact latched runs are terminal and Sanctuary is idle'}, 0o644)
            return False
    while True:
        try:
            ensure_empty()
            break
        except Blocked as exc:
            if exc.code != 'runner_window_not_empty': raise
            write_json(STATUS, {'phase': 'rollback_waiting_for_idle', 'timed_out': timed_out,
                                'runs_completed': sorted(wid for wid, item in latched.items()
                                                         if item.get('status') == 'completed')}, 0o644)
            time.sleep(POLL_SECONDS)
    # Verify exact active fingerprints again before stopping/rolling back.
    verify_running_state(True, backup, new_module)
    restore_transaction(client, package, backup, before, old_module, new_module)
    report = {'phase': 'rolled_back_after_gate_comparison', 'timed_out': timed_out,
              'watch_failure': watch_failure or (None if len(latched) == len(WORKFLOWS) else 'required_workflow_missing'),
              'runs': {str(wid): {'id': run['id'],
                                  'url': f'https://github.com/Tuinstra-DEV/gate/actions/runs/{run["id"]}',
                                  'workflow_name': run.get('name'), 'head_sha': run.get('head_sha'),
                                  'run_attempt': run.get('run_attempt'), 'status': run.get('status'),
                                  'conclusion': run.get('conclusion'),
                                  'backend_job_status': run.get('backend_job_status'),
                                  'backend_job_conclusion': run.get('backend_job_conclusion'),
                                  'backend_runner_id': run.get('backend_runner_id'),
                                  'backend_runner_name': run.get('backend_runner_name'),
                                  'backend_runner_group': run.get('backend_runner_group'),
                                  'backend_role_label_verified': run.get('backend_role_label_verified')}
                       for wid, run in latched.items()},
              'manager_restored': True, 'source_image_restored': True, 'runner_group_restored': True}
    write_json(STATUS, report, 0o644)
    try: print(json.dumps(report, separators=(',', ':')), flush=True)
    except BrokenPipeError: pass
    return True


def finish_already_restored_rollback(client, state: dict, backup: Path, before: dict,
                                    old_module) -> bool:
    """Make a crash after service restart but before journal write idempotent."""
    active = source_fingerprints()
    old_image_hash = SOURCE_IMAGE.name.removeprefix('ubuntu-24.04-runner-').removesuffix('.qcow2')
    if (active['manager_sha256'] != OLD_MANAGER_HASH or
            active['selector_target'] != str(SOURCE_IMAGE) or
            active['config_sha256'] != before['config_sha256'] or
            active['active_image_sha256'] != old_image_hash or
            active['helper_sha256'] != HELPER_HASH):
        return False
    group, repositories = read_group(client, {})
    expected_refs = backed_group_refs(backup, False)
    expected_repositories = json.loads((backup / 'repositories-before.json').read_text())
    if (not group_policy_matches(group) or group.get('selected_workflows') != expected_refs or
            repository_map(repositories) != expected_repositories):
        return False
    verify_running_state(False, backup, old_module)
    promotion = state.get('candidate_promotion', {})
    if not state.get('sealed_image_preexisting', False) and promotion.get('mode') != 'preexisting':
        if not candidate_source_restored(promotion):
            return False
    state['phase'] = 'rolled_back'
    write_json(JOURNAL, state)
    write_json(STATUS, {'phase': 'rolled_back_after_interrupted_rollback',
                        'manager_restored': True, 'source_image_restored': True,
                        'runner_group_restored': True, 'host_helper_unchanged': True}, 0o644)
    return True


def load_backup_before(backup: Path, journal_before: dict) -> dict:
    """Load protected baseline metadata and ensure it matches the journal."""
    try:
        raw = secure_regular(backup / 'before.json')
        persisted = json.loads(raw)
    except Blocked:
        raise
    except Exception:
        raise Blocked('rollback_baseline_unavailable') from None
    required = {'manager_sha256', 'config_sha256', 'selector_target',
                'manager_uid', 'manager_gid', 'manager_mode',
                'config_uid', 'config_gid', 'config_mode'}
    if not isinstance(persisted, dict) or not required.issubset(persisted):
        raise Blocked('rollback_baseline_invalid')
    if not isinstance(journal_before, dict) or any(
            key not in persisted or persisted[key] != value
            for key, value in journal_before.items()):
        raise Blocked('rollback_baseline_journal_mismatch')
    return persisted


def rollback_only(package: Path) -> None:
    if os.geteuid() != 0 or not JOURNAL.is_file() or JOURNAL.is_symlink():
        raise Blocked('no_active_transaction_journal')
    journal_info = JOURNAL.lstat()
    if (journal_info.st_uid != 0 or journal_info.st_nlink != 1 or
            stat.S_IMODE(journal_info.st_mode) != 0o600):
        raise Blocked('transaction_journal_unsafe')
    state = json.loads(JOURNAL.read_text())
    backup = Path(state['backup'])
    before = load_backup_before(backup, state.get('before'))
    if state.get('phase') not in {'prepared', 'active_watching', 'active', 'rollback_pending'}:
        raise Blocked('transaction_not_active')
    if service_state()['ActiveState'] != 'active':
        rollback_stopped_transaction(package, state, backup, before)
        return
    current_manager = load_module(MANAGER, 'dev54_gate_rollback_manager')
    credential_checker = load_operator_checker(package)
    token, client = existing_manager_client(parse_manager_config(CONFIG.read_bytes()), credential_checker,
                                            current_manager)
    ensure_empty()
    if finish_already_restored_rollback(client, state, backup, before, current_manager):
        return
    active = source_fingerprints()
    if (active['manager_sha256'] == OLD_MANAGER_HASH and
            active['config_sha256'] == before['config_sha256'] and
            active['selector_target'] == str(SOURCE_IMAGE) and
            active['active_image_sha256'] == SOURCE_IMAGE.name.removeprefix(
                'ubuntu-24.04-runner-').removesuffix('.qcow2') and
            active['helper_sha256'] == HELPER_HASH):
        # Recovery may have restarted the old service to regain its existing
        # credential context, then died before restoring the GitHub group.
        # Accept only journal-bound old bytes and either exact known ref set.
        verify_restored_files(before)
        group, repositories = read_group(client, {})
        refs = group.get('selected_workflows', [])
        known_refs = (backed_group_refs(backup, False), backed_group_refs(backup, True))
        if (not group_policy_matches(group) or not any(
                len(refs) == len(expected) and set(refs) == set(expected) for expected in known_refs)):
            raise Blocked('recovery_group_fingerprint_changed')
        if repository_map(repositories) != json.loads((backup / 'repositories-before.json').read_text()):
            raise Blocked('recovery_repository_fingerprint_changed')
        stop_manager_when_empty('rollback_waiting_for_idle')
        rollback_stopped_transaction(package, state, backup, before)
        return
    restore_transaction(client, package, backup, before, current_manager, current_manager,
                        allow_original_group=True)


def rollback_stopped_transaction(package: Path, state: dict, backup: Path, before: dict) -> None:
    """Recover a process interruption during the tiny stopped-manager window."""
    ensure_empty()
    live = source_fingerprints()
    allowed_manager = {OLD_MANAGER_HASH, NEW_MANAGER_HASH}
    allowed_config = {before['config_sha256'], state['config_after_sha256']}
    allowed_selector = {str(SOURCE_IMAGE), str(NEW_IMAGE)}
    if (live['manager_sha256'] not in allowed_manager or live['config_sha256'] not in allowed_config or
            live['selector_target'] not in allowed_selector or live['helper_sha256'] != HELPER_HASH or
            (live['selector_target'] == str(NEW_IMAGE) and live['active_image_sha256'] != CANDIDATE_HASH)):
        raise Blocked('rollback_fingerprint_changed')
    old_manager = secure_regular(backup / 'manager.before', before['manager_sha256'])
    old_config = secure_regular(backup / 'manager.toml.before', before['config_sha256'])
    atomic_write(MANAGER, old_manager, before['manager_uid'], before['manager_gid'], before['manager_mode'])
    atomic_write(CONFIG, old_config, before['config_uid'], before['config_gid'], before['config_mode'])
    atomic_selector(Path(before['selector_target']))
    restore_candidate_source(state.get('candidate_promotion', {'mode': 'not_started',
        'source': str(CANDIDATE_SOURCE), 'source_uid': 1000, 'source_gid': 1000,
        'source_mode': 0o644, 'source_atime_ns': 0, 'source_mtime_ns': 0}),
        sealed_image_preexisting=state.get('sealed_image_preexisting', False))
    safe_command(['systemctl', 'start', SERVICE])
    wait_service(True)
    old_module = load_module(MANAGER, 'dev54_gate_recovered_manager')
    cfg = parse_manager_config(CONFIG.read_bytes())
    credential_checker = load_operator_checker(package)
    token, client = existing_manager_client(cfg, credential_checker, old_module)
    group, repos = read_group(client, cfg)
    before_refs = backed_group_refs(backup, False)
    after_refs = backed_group_refs(backup, True)
    actual_refs = group.get('selected_workflows', [])
    if (not group_policy_matches(group) or not any(
            len(actual_refs) == len(refs) and set(actual_refs) == set(refs)
            for refs in (before_refs, after_refs))):
        safe_command(['systemctl', 'stop', SERVICE])
        wait_service(False)
        raise Blocked('recovery_group_fingerprint_changed')
    if repository_map(repos) != json.loads((backup / 'repositories-before.json').read_text()):
        safe_command(['systemctl', 'stop', SERVICE])
        wait_service(False)
        raise Blocked('recovery_repository_fingerprint_changed')
    if set(actual_refs) == set(after_refs):
        patch_group(client, before_refs)
    verify_running_state(False, backup, old_module)
    final_group, final_repositories = read_group(client, {})
    if (not group_policy_matches(final_group) or
            len(final_group['selected_workflows']) != len(before_refs) or
            set(final_group['selected_workflows']) != set(before_refs) or
            repository_map(final_repositories) != json.loads((backup / 'repositories-before.json').read_text())):
        raise Blocked('recovery_policy_readback_differs')
    state['phase'] = 'rolled_back'
    write_json(JOURNAL, state)
    write_json(STATUS, {'phase': 'rolled_back_after_interrupted_activation',
                        'manager_restored': True, 'source_image_restored': True,
                        'runner_group_restored': True, 'host_helper_unchanged': True}, 0o644)


def main() -> int:
    package = Path(__file__).parent
    rollback = len(sys.argv) == 2 and sys.argv[1] == '--rollback'
    if len(sys.argv) > 1 and not rollback:
        print(json.dumps({'phase': 'blocked', 'blocker': 'unsupported_arguments'})); return 2
    previous_handlers = install_operator_signal_handlers()
    try:
        if os.geteuid() != 0: raise Blocked('existing_operator_sudo_required')
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lock_info = os.fstat(fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != 0 or lock_info.st_nlink != 1 or
                lock_info.st_mode & 0o022):
            raise Blocked('transaction_lock_unsafe')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if rollback:
            SIGNAL_CONTROL['abort_enabled'] = False
            rollback_only(package); return 0
        state, backup, before, old_module, new_module, token = activate(package)
        if not watch_and_rollback(package, state, backup, before, old_module, new_module, token):
            return 2
        return 0
    except OperatorInterrupt:
        value = {'phase': 'interrupted', 'blocker': 'operator_interrupt',
                 'recovery': 'check sanitized status; use fixed rollback only after exact runs are terminal and Sanctuary is idle'}
        print(json.dumps(value, separators=(',', ':')), file=sys.stderr)
        return 130
    except Blocked as exc:
        value = {'phase': 'blocked', 'blocker': exc.code}
        if exc.http_status is not None: value['http_status'] = exc.http_status
        try: write_json(STATUS, value, 0o644)
        except Exception: pass
        print(json.dumps(value, separators=(',', ':')), file=sys.stderr)
        return 1
    except Exception as exc:
        value = {'phase': 'blocked', 'blocker': 'unexpected_transaction_error',
                 'error_class': type(exc).__name__}
        try: write_json(STATUS, value, 0o644)
        except Exception: pass
        print(json.dumps(value, separators=(',', ':')), file=sys.stderr)
        return 1
    finally:
        SIGNAL_CONTROL['abort_enabled'] = False
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    raise SystemExit(main())
