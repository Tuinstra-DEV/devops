#!/usr/bin/env python3
"""Complete a source-verified publisher bundle with the already trusted runtime."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys

ROOT = Path('/home/mtuinstra/tuinstra-console-worker-install')
SOURCE = Path('/opt/tuinstra-console-worker/runtime/v1.12.7+build.20260912/frankenphp')
PIN = '54158c17e8bbb03b8426a68dfef027e65a6c6fe604299067a73ef6c1c0512d59'


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1 or Path(__file__).resolve().parent != ROOT:
        raise RuntimeError('run the fixed staged helper through sudo, without arguments')
    subprocess.run(['sha256sum', '--check', '--quiet', 'SOURCE_SHA256SUMS'], cwd=ROOT, check=True)
    manifest = json.loads((ROOT / 'runtime-manifest.json').read_text())
    assert manifest['sha256'] == PIN
    info = SOURCE.lstat()
    assert SOURCE.resolve() == SOURCE and stat.S_ISREG(info.st_mode)
    assert info.st_uid == 0 and not info.st_mode & 0o022 and digest(SOURCE) == PIN
    target = ROOT / 'frankenphp-linux-x86_64-gnu'
    if target.exists() or target.is_symlink():
        assert not target.is_symlink() and target.is_file() and digest(target) == PIN
    else:
        with SOURCE.open('rb') as source, target.open('xb') as out:
            shutil.copyfileobj(source, out)
            out.flush()
            os.fsync(out.fileno())
    os.chown(target, 0, 0)
    target.chmod(0o755)
    assert digest(target) == PIN
    # This records reuse of an already approved component, not a fresh upstream
    # download/attestation verification. The original runtime is never modified.
    record = {'verification_mode': 'reuse-preexisting-trusted-root-owned-runtime',
              'sha256': PIN, 'source': str(SOURCE), 'new_download_used': False,
              'runtime_source_unchanged': True}
    proof = ROOT / 'runtime-attestation-verification.json'
    if proof.is_symlink():
        raise RuntimeError('unsafe runtime provenance file')
    proof.write_text(json.dumps(record, sort_keys=True) + '\n')
    proof.chmod(0o600)
    checks = (ROOT / 'SOURCE_SHA256SUMS').read_text()
    checks += PIN + '  frankenphp-linux-x86_64-gnu\n'
    checks += digest(proof) + '  runtime-attestation-verification.json\n'
    (ROOT / 'SHA256SUMS').write_text(checks)
    subprocess.run(['sha256sum', '--check', '--quiet', 'SHA256SUMS'], cwd=ROOT, check=True)
    print('PASS: existing trusted runtime hash and complete publisher bundle verified; no runtime download/replacement')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Publisher runtime reuse stopped; no new runtime was approved', file=sys.stderr)
        raise SystemExit(1)
