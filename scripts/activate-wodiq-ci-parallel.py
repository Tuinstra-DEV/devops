#!/usr/bin/env python3
"""Human-run, checksummed upgrade after natural drain; never stops a job VM."""
from pathlib import Path
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib

STAGE = Path('/root/wodiq-ci-parallel')
CONFIG = Path('/etc/ci-runner/manager.toml')
MANAGER = Path('/usr/local/bin/ci-runner-manager')
HELPER = Path('/usr/local/libexec/ci-runner-host-helper')
SERVICE = 'ci-runner-manager.service'
PREFIX = 'Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@'
OLD_WORKFLOW = PREFIX + '604ad2b9e636798899cb90a7aa8bdfa8d6ad2a4d'
KNOWN_MANAGERS = {'5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3',
                  '81aa1f6405f71ebf15740079bbde89c8f4e7d44820110172a6ad1565728591ec'}
KNOWN_HELPERS = {'fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613',
                 'addf16e83af68ecfd99fcc080dfa93f8f04e2c8e57098f091c5688c100be6954'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def candidate_config(data, commit):
    assert re.fullmatch('[a-f0-9]{40}', commit), 'invalid source commit'
    text = data.decode(); old = tomllib.loads(text)
    assert old.get('pool_mode') in {'four', 'parallel'}, 'requires existing bounded pools'
    assert (old.get('max_concurrency'), old.get('max_heavy'), old.get('max_medium')) == ((4,2,2) if old['pool_mode'] == 'four' else (5,3,2))
    assert old.get('org_routing_enabled') is True
    assert old.get('org_runner_group_id') == 4
    assert old.get('runner_vcpus') == 4 and old.get('runner_memory_mib') == 6144
    assert old.get('medium_runner_vcpus') == 2 and old.get('medium_runner_memory_mib') == 3072
    assert old.get('host_memory_reserve_mib') == 4096 and old.get('min_free_disk_gib') == 60
    refs = old.get('org_workflow_refs')
    assert isinstance(refs, list) and OLD_WORKFLOW in refs and len(set(refs)) == len(refs)
    ref = PREFIX + commit
    updated = list(refs)
    if ref not in updated:
        updated.insert(len(updated) - 1, ref)  # preserve the metadata-role tail pin
    assert len(updated) <= 6
    replacements = {'pool_mode': 'parallel', 'max_concurrency': 5, 'max_heavy': 3,
                    'org_workflow_refs': updated}
    for key, value in replacements.items():
        pattern = r'(?m)^' + key + r'\s*=\s*' + (r'\[[\s\S]*?\]' if isinstance(value, list) else r'[^\n#]+')
        text, count = re.subn(pattern, key + ' = ' + json.dumps(value), text)
        assert count == 1, 'ambiguous manager field'
    expected = {**old, **replacements}
    assert tomllib.loads(text) == expected, 'unrelated configuration changed'
    return text.encode()


def run(argv):
    result = subprocess.run(argv, capture_output=True, check=True, text=True, timeout=120)
    return result.stdout


def require_drained():
    domains = run(['virsh', '--connect', 'qemu:///system', 'list', '--all', '--name'])
    assert not any(x.startswith('sanctuary-ci-') for x in domains.splitlines()), 'wait for job VMs to drain naturally'
    overlay = Path('/var/lib/ci-runner/overlay')
    state = Path('/var/lib/ci-runner-manager/state')
    assert overlay.is_dir() and not any(overlay.iterdir()), 'wait for overlay cleanup; never delete active state'
    assert state.is_dir() and not list(state.glob('lease-*.json')) and not list(state.glob('cleanup-*.json')), 'wait for lease cleanup'


def private_read(path):
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
    return path.read_bytes(), info


def atomic_write(path, data, info):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.wodiq-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.chown(name, info.st_uid, info.st_gid); os.chmod(name, stat.S_IMODE(info.st_mode))
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def main():
    assert os.geteuid() == 0 and sys.argv[1:] in [['--preflight'], ['--apply']]
    assert Path(__file__).resolve().parent == STAGE
    info = STAGE.lstat()
    assert info.st_uid == 0 and stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o077
    os.chdir(STAGE)
    lock_fd = os.open(STAGE / '.activation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    run(['sha256sum', '--check', '--quiet', str(STAGE / 'SHA256SUMS')])
    commit = (STAGE / 'SOURCE_COMMIT').read_text().strip()
    source_manager = private_read(STAGE / 'runner/manager/ci_runner_manager.py')[0]
    source_helper = private_read(STAGE / 'runner/host-helper/ci_runner_host_helper.py')[0]
    originals = {p: private_read(p) for p in [CONFIG, MANAGER, HELPER]}
    assert sha(originals[MANAGER][0]) in KNOWN_MANAGERS | {sha(source_manager)}
    assert sha(originals[HELPER][0]) in KNOWN_HELPERS | {sha(source_helper)}
    candidate = candidate_config(originals[CONFIG][0], commit)
    spec = importlib.util.spec_from_file_location('reviewed_manager', STAGE / 'runner/manager/ci_runner_manager.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with tempfile.NamedTemporaryFile(dir=STAGE, prefix='.candidate-', delete=True) as temporary:
        temporary.write(candidate); temporary.flush(); module.load_config(Path(temporary.name))
    assert (os.cpu_count() or 0) >= 16
    memory = dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    assert int(memory['MemTotal'].split()[0]) >= 28672 * 1024
    assert shutil.disk_usage('/var/lib/ci-runner/overlay').free >= 60 * 1024**3
    require_drained()
    if sys.argv[1] == '--preflight':
        print('PASS: exact source/config, hardware, existing identities and natural drain; nothing changed')
        return
    # Stop only the reconciler after a successful natural drain; never touch VMs.
    run(['systemctl', 'stop', SERVICE])
    backup = None
    try:
        require_drained()
        for path, (data, _) in originals.items(): assert path.read_bytes() == data
        backup = Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%dT%H%M%S-'), dir='/var/backups'))
        backup.chmod(0o700)
        for path, (data, _) in originals.items():
            (backup / path.name).write_bytes(data); (backup / path.name).chmod(0o600)
        for path, data in [(HELPER, source_helper), (MANAGER, source_manager), (CONFIG, candidate)]:
            atomic_write(path, data, originals[path][1])
        run(['systemctl', 'start', SERVICE]); run(['systemctl', 'is-active', SERVICE])
    except Exception:
        if backup:
            for path, (data, info) in originals.items(): atomic_write(path, data, info)
        run(['systemctl', 'start', SERVICE])
        raise
    print('PASS: parallel capacity installed; existing credentials, trust and old workflow pins preserved')
    print('rollback_dir=' + str(backup))
    print('workflow_ref=' + PREFIX + commit)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Runner upgrade stopped; private configuration/output suppressed. Inspect preflight; no job VM was stopped.', file=sys.stderr)
        raise SystemExit(1)
