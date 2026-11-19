#!/usr/bin/env python3
"""Read-only, bounded permanent Gate preflight; credentials never leave memory."""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib

PACKAGE = Path(__file__).resolve().parent
MANAGER = Path('/usr/local/bin/ci-runner-manager')
CONFIG = Path('/etc/ci-runner/manager.toml')
HELPER = Path('/usr/local/libexec/ci-runner-host-helper')
STATE = Path('/var/lib/ci-runner-manager/state')
MEMINFO = Path('/proc/meminfo')


def module(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    loader.exec_module(value)
    return value


def fail(category):
    raise ValueError(category)


def inspect():
    if os.geteuid() != 0:
        fail('existing_operator_sudo_required')
    manifest = json.loads((PACKAGE / 'manager-manifest.json').read_text())
    raw = MANAGER.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['source_sha256']:
        fail('effective_manager_baseline_changed')
    for path in (MANAGER, CONFIG):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            fail('effective_file_metadata_unsafe')
    effective = module(MANAGER, 'effective_gate_manager')
    cfg = tomllib.loads(CONFIG.read_text())
    effective.validate_org_config(cfg)
    checker = module(PACKAGE / 'operator_check.py', 'gate_operator_checker')
    for name in ('group-state.json', 'repositories-state.json'):
        if abs(time.time() - (PACKAGE / name).stat().st_mtime) > 300:
            fail('fresh_github_snapshots_required')
    group = json.loads((PACKAGE / 'group-state.json').read_text())
    repos = json.loads((PACKAGE / 'repositories-state.json').read_text())
    expected = json.loads((PACKAGE / 'group-before.json').read_text())
    if (group.get('id') != 4 or group.get('visibility') != 'selected' or group.get('restricted_to_workflows') is not True or
            group.get('allows_public_repositories') is not False or group.get('selected_workflows') != expected['selected_workflows'] or
            set(effective.configured_org_workflow_refs(cfg)) != set(group['selected_workflows']) or cfg.get('org_runner_group_id') != 4):
        fail('effective_group_policy_changed')
    selected = {r['full_name']: r['id'] for r in repos['repositories']}
    if (repos['total_count'] != len(selected) or cfg['org_repository_ids'] != selected or
            selected != json.loads((PACKAGE / 'repositories-before.json').read_text())):
        fail('effective_repository_selection_changed')
    if cfg.get('github_api_url', 'https://api.github.com').rstrip('/') != 'https://api.github.com':
        fail('github_origin_changed')
    token = checker.loaded_manager_credential(cfg, effective)
    client = effective.GitHubClient(token)
    permissions = {}
    for name, path in {
        'pr': '/repos/Tuinstra-DEV/gate/pulls/444',
        'develop': '/repos/Tuinstra-DEV/gate/branches/develop',
        'main': '/repos/Tuinstra-DEV/gate/branches/main',
        'run': '/repos/Tuinstra-DEV/gate/actions/runs/44509670352',
        'jobs': '/repos/Tuinstra-DEV/gate/actions/runs/44509670352/jobs?per_page=100',
    }.items():
        try:
            client.request('GET', path)
        except Exception as error:
            status = re.search(r'HTTP ([0-9]{3})', str(error))
            fail('github_get_' + name + ('_http_' + status[1] if status else '_failed'))
        permissions[name] = True
    client.verify_org_group(cfg)
    candidate, evidence = checker.gate_candidate_image(PACKAGE)
    php = checker.candidate_image_php_check(candidate)
    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(candidate)], text=True))
    if info.get('backing-filename') is not None:
        fail('candidate_not_standalone')
    helper = HELPER.read_text()
    if not all(value in helper for value in ('"heavy": (4, 6144)', '"medium": (2, 3072)')):
        # The immutable helper hash is the authoritative control; resource parsing
        # uses its literal dictionary without executing the helper.
        import ast
        tree = ast.parse(helper)
        resources = next((ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PROFILE_RESOURCES' for t in n.targets)), None)
        if resources != {'heavy': (4, 6144), 'medium': (2, 3072)}:
            fail('runner_resource_contract_changed')
    memory = dict(line.split(':', 1) for line in MEMINFO.read_text().splitlines())
    free = shutil.disk_usage('/var/lib/ci-runner').free
    if free < 62 * 1024**3:
        fail('insufficient_host_disk_headroom')
    state = STATE
    state_info = state.lstat()
    if not stat.S_ISDIR(state_info.st_mode) or state_info.st_mode & 0o022:
        fail('lease_state_directory_unsafe')
    leases = len(list(state.glob('lease-*')))
    cleanup = len(list(state.glob('cleanup-*')))
    domains = subprocess.check_output(['virsh', '-c', 'qemu:///system', 'list', '--uuid'], text=True).strip().splitlines()
    return {'all_checks_green': True, 'read_only': True, 'policy_verified': True,
            'manager_baseline_sha256': manifest['source_sha256'], 'existing_refs': len(group['selected_workflows']),
            'existing_repositories': len(selected), 'credential_gets': permissions, 'credential_group_read': True,
            'candidate_sha256': evidence['sha256'], 'candidate_php': php,
            'guest_docker': 'reused immutable-image isolated VM proof',
            'host_free_gib': free // 1024**3, 'host_available_memory_mib': int(memory['MemAvailable'].split()[0]) // 1024,
            'runner_window': {'active_domains': len(domains), 'leases': leases, 'cleanup': cleanup,
                              'empty': not (domains or leases or cleanup)}}


if __name__ == '__main__':
    try:
        print(json.dumps(inspect(), separators=(',', ':')))
    except Exception as error:
        category = getattr(error, 'code', None)
        if category is None:
            category = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z0-9_]+', str(error)) else type(error).__name__
        print(json.dumps({'all_checks_green': False, 'read_only': True, 'blocker': category}))
        raise SystemExit(1)
