#!/usr/bin/env python3
"""Read-only bounded diagnostics; never emit log lines, config or credentials."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

MANAGER = Path('/usr/local/bin/ci-runner-manager')
HELPER = Path('/usr/local/libexec/ci-runner-host-helper')
CONFIG = Path('/etc/ci-runner/manager.toml')
MANAGER_SHA = '5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3'
HELPER_SHA = 'fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613'
JOBS = (133931380577, 133931391484)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(text):
    lower = text.lower()
    patterns = {
        'qemu_storage_full': r'no space left on device|\benospc\b',
        'qemu_io_error': r'input/output error|\bi/o error\b',
        'qemu_guest_panic': r'guest.panicked|guest panic',
        'qemu_signal_termination': r'terminating on signal',
        'qemu_shutdown': r'shutting down|reason=shutdown',
    }
    result = {'categories': [key for key, regex in patterns.items() if re.search(regex, lower)]}
    signals = {int(value) for value in re.findall(r'terminating on signal ([0-9]{1,2})\b', lower)}
    result['termination_signals'] = sorted(value for value in signals if 0 < value < 65)
    result['diagnostic_lines'] = sum(bool(re.search(r'error|no space|enospc|terminating|shutting down', line.lower()))
                                     for line in text.splitlines())
    return result


def helper_limits(source):
    allowed = {'PROFILE_DISK_GIB', 'DISK_GIB', 'MAX_LEASE_SECONDS', 'PROFILE_RESOURCES'}
    values = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in allowed:
                values[name] = ast.literal_eval(node.value)
    return values


def main():
    result = {'read_only': True, 'diagnostics_verified': False}
    try:
        if os.geteuid() != 0:
            result['blocker'] = 'existing_operator_sudo_required'
            return result
        if fingerprint(MANAGER) != MANAGER_SHA or fingerprint(HELPER) != HELPER_SHA:
            result['blocker'] = 'deployed_code_changed'
            return result
        before = fingerprint(CONFIG)
        cfg = tomllib.loads(CONFIG.read_text())
        result['helper_limits'] = helper_limits(HELPER.read_text())
        # Verification outcomes only; never serialize configuration values.
        ttl = cfg.get('max_lease_seconds')
        reservation = cfg.get('heavy_disk_reservation_gib')
        result['effective_bounds'] = {
            'lease_ttl_at_least_two_hours': type(ttl) is int and ttl >= 7200,
            'heavy_reservation_matches_helper': reservation is None or
                reservation == result['helper_limits'].get('PROFILE_DISK_GIB', {}).get('heavy')}
        result['qemu'] = []
        for job in JOBS:
            row = {'job_id': job, 'readable': False}
            try:
                path = Path('/var/log/libvirt/qemu') / f'sanctuary-ci-gh-{job}.log'
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 262144))
                    text = stream.read(262144).decode('utf-8', 'replace')
                row.update(classify(text), readable=True)
            except OSError:
                row['category'] = 'qemu_log_unreachable'
            result['qemu'].append(row)
        kernel = subprocess.run(['journalctl', '-k', '--since', '2026-11-17 09:25:00 UTC',
            '--until', '2026-11-17 10:15:00 UTC', '--no-pager', '-o', 'cat'],
            capture_output=True, text=True, timeout=30)
        result['host_kernel_readable'] = kernel.returncode == 0
        if kernel.returncode == 0:
            result['host_oom_recorded'] = bool(re.search(r'out of memory|oom-kill|killed process', kernel.stdout, re.I))
        result['policy_unchanged'] = (before == fingerprint(CONFIG) and fingerprint(MANAGER) == MANAGER_SHA
                                      and fingerprint(HELPER) == HELPER_SHA)
        result['diagnostics_verified'] = result['policy_unchanged'] and all(row['readable'] for row in result['qemu'])
    except Exception:
        result['blocker'] = 'read_only_diagnostics_failed'
    return result


if __name__ == '__main__':
    print(json.dumps(main(), separators=(',', ':')))
