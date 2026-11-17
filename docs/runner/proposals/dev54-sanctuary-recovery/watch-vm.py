#!/usr/bin/env python3
"""Bounded unprivileged libvirt observation of one diagnostic CI lease."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time


def query(*args):
    try:
        result = subprocess.run(['/usr/bin/virsh', '-c', 'qemu:///system', *args],
            capture_output=True, text=True, timeout=10,
            env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        return result.stdout if result.returncode == 0 else None
    except subprocess.TimeoutExpired:
        return None


def state_value(text):
    if text is None:
        return {'state': 'absent_or_unavailable'}
    lower = text.strip().lower()
    state = next((v for v in ['running', 'paused', 'shut off', 'crashed', 'in shutdown', 'pmsuspended']
                  if lower.startswith(v)), 'unknown')
    return {'state': state, 'io_error_reason': 'i/o error' in lower or 'ioerror' in lower}


def block_errors(text):
    if text is None:
        return ['query_unavailable']
    lower = text.lower().strip()
    if not lower or 'no errors found' in lower:
        return []
    return ['storage_full' if 'no space' in lower or 'enospc' in lower else 'block_io_error']


def metrics(text):
    result = {}
    if text is None:
        return result
    for line in text.splitlines():
        key, separator, value = line.strip().partition('=')
        if separator and re.fullmatch(r'(state\.(state|reason)|balloon\.(current|usable|rss)|block\.[0-9]+\.(allocation|capacity|physical))', key) and value.isdecimal():
            result[key] = int(value)
    return result


def storage_stats(job):
    if not re.fullmatch(r'[1-9][0-9]{1,19}', job):
        raise ValueError('invalid diagnostic job')
    try:
        info = os.statvfs('/var/lib/ci-runner/overlay/gh-' + job + '/work')
        capacity = info.f_blocks * info.f_frsize
        return {'metadata_readable': True, 'capacity_bytes': capacity,
                'free_bytes': info.f_bavail * info.f_frsize,
                'bounded_volume': 23 * 1024**3 <= capacity < 24 * 1024**3}
    except OSError:
        return {'metadata_readable': False, 'bounded_volume': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True)
    parser.add_argument('--duration', type=int, default=2700)
    parser.add_argument('--storage-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[1-9][0-9]{1,19}', args.job) or not 1 <= args.duration <= 2700:
        raise SystemExit('invalid diagnostic scope')
    domain = 'sanctuary-ci-gh-' + args.job
    directory = Path.home() / ('dev54-vm-watch-' + args.job)
    directory.mkdir(mode=0o700, exist_ok=args.storage_only)
    seen = False
    missing = 0
    last_state = None
    deadline = time.monotonic() + args.duration
    filename = 'storage-samples.jsonl' if args.storage_only else 'samples.jsonl'
    with (directory / filename).open('x') as output:
        print(json.dumps({'watch_ready': True, 'job_id': args.job}), flush=True)
        while time.monotonic() < deadline:
            row = {'utc': datetime.now(timezone.utc).isoformat(), 'storage': storage_stats(args.job)}
            if args.storage_only:
                row['state'] = 'volume_present' if row['storage']['bounded_volume'] else 'absent_or_unavailable'
            else:
                row.update(state_value(query('domstate', domain, '--reason')))
            if row['state'] != 'absent_or_unavailable':
                seen = True
                missing = 0
                if not args.storage_only:
                    row['block_errors'] = block_errors(query('domblkerror', domain))
                    row['metrics'] = metrics(query('domstats', domain, '--raw', '--state', '--block', '--balloon'))
            elif seen:
                missing += 1
            output.write(json.dumps(row) + '\n')
            output.flush()
            low = row['storage'].get('bounded_volume') and row['storage'].get('free_bytes', 1024**3) < 512 * 1024**2
            if row['state'] != last_state or row.get('block_errors') or low:
                print(json.dumps(row), flush=True)
                last_state = row['state']
            if missing >= 3:
                print(json.dumps({'watch_complete': True, 'domain_departed': True}), flush=True)
                return
            time.sleep(1 if args.storage_only else 2)
    print(json.dumps({'watch_complete': True, 'time_limit_reached': True}), flush=True)


if __name__ == '__main__':
    main()
