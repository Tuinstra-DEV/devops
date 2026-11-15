#!/usr/bin/env python3
"""Human-invoked fixed catalog-scope upgrade; token values never reach output/argv."""
from pathlib import Path
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid

WORKER = 'sanctuary-backup-worker'
ENV = Path('/etc/tuinstra-console-worker/worker.env')
OLD = {'tuinstra-prod-01/umami/' + operation for operation in (
    'backup.run', 'backup.check', 'backup.retention_reconcile', 'backup.restore_test',
    'backup.catalog', 'backup.restore_production')}
NEW = OLD | {'tuinstra-prod-02/wodiq/backup.catalog'}
QUERY = r'''<?php
require '/app/vendor/autoload.php';
$kernel = new App\Kernel('prod', false);
$kernel->boot();
$c = $kernel->getContainer()->get('doctrine')->getConnection();
$r = $c->fetchOne("SELECT scopes FROM worker_credentials WHERE worker_id = 'sanctuary-backup-worker' AND enabled = true");
if (false === $r) { exit(1); }
echo $r;
'''


def flattened(scopes):
    return {f"{item['host_slug']}/{app}/{operation}" for item in scopes
            for app in (item['app_ids'] or ['-']) for operation in item['operations']}


def replace_token(text, token):
    lines = text.splitlines()
    assert sum(line.startswith('WORKER_ID=') for line in lines) == 1
    assert next(line.split('=', 1)[1].strip('"\'') for line in lines if line.startswith('WORKER_ID=')) == WORKER
    assert sum(line.startswith('WORKER_TOKEN=') for line in lines) == 1
    assert re.fullmatch(r'cwk_[A-Za-z0-9_-]{43}', token)
    return '\n'.join('WORKER_TOKEN=' + token if line.startswith('WORKER_TOKEN=') else line for line in lines) + '\n'


def read_environment():
    info = ENV.lstat()
    assert ENV.resolve() == ENV and stat.S_ISREG(info.st_mode)
    assert info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600
    original = ENV.read_text()
    replace_token(original, 'cwk_' + 'a' * 43)
    return original


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise RuntimeError('run through sudo, without arguments')
    original = read_environment()
    observed = subprocess.run(['docker', 'exec', '-i', 'console_prod_php', 'php'],
                              input=QUERY, text=True, capture_output=True, check=True)
    scopes = flattened(json.loads(observed.stdout))
    if scopes not in (OLD, NEW):
        raise RuntimeError('worker scopes changed; stop rather than overwrite other permissions')
    # Always rotate, including an already upgraded scope set: a previous attempt
    # may have changed the DB before failing to persist the matching env token.
    backup = ENV.with_name('worker.env.before-wodiq-' + uuid.uuid4().hex[:8])
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(original)
        handle.flush()
        os.fsync(handle.fileno())
    argv = ['docker', 'exec', 'console_prod_php', 'php', 'bin/console',
            'app:worker:issue-token', WORKER, '--rotate', '--no-interaction']
    argv += ['--scope=' + scope for scope in sorted(NEW)]
    issued = subprocess.run(argv, text=True, capture_output=True, check=True)
    tokens = [line.split('=', 1)[1] for line in issued.stdout.splitlines() if line.startswith('WORKER_TOKEN=')]
    assert len(tokens) == 1
    updated = replace_token(original, tokens[0])
    # Persist the new root-private env before replacement, so it remains usable
    # for recovery if an unexpected filesystem failure occurs after DB rotation.
    descriptor, pending = tempfile.mkstemp(prefix='.worker.env.wodiq-', dir=ENV.parent)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(updated)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(pending, 0o600)
    os.replace(pending, ENV)
    descriptor = os.open(ENV.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print('PASS: seven catalog/Umami scopes confirmed; token rotated and stored privately; restart/publish required')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Catalog scope upgrade stopped; private diagnostics/credential values suppressed', file=sys.stderr)
        raise SystemExit(1)
