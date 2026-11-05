#!/usr/bin/env python3
"""One-purpose, operator-run immutable runner repair. Never edits manager policy."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import tomllib

VERSION = '2.338.2'
ARCHIVE_SHA = 'baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40'
OLD_SHA = '4edecc084221f8b753d782e731f84e60cbee8753b64277ec0901c30480aa1317'
ROOT = Path('/var/lib/ci-runner/images')
OLD = ROOT / f'ubuntu-24.04-runner-{OLD_SHA}.qcow2'
CURRENT = ROOT / 'ubuntu-24.04-runner.qcow2'
PREVIOUS = ROOT / 'ubuntu-24.04-runner.previous.qcow2'
WORK = Path('/var/lib/ci-runner/runner-2338-repair')
STATE = Path('/var/lib/ci-runner-manager/state')
OVERLAY = Path('/var/lib/ci-runner/overlay')
SERVICE = 'ci-runner-manager.service'
DRAIN = Path('/run/systemd/system/ci-runner-manager.service.d/runner-2338-drain.conf')
DRAIN_TEXT = ('[Service]\nType=oneshot\nExecStart=\nExecStart=/usr/local/bin/ci-runner-manager '
              '--config /etc/ci-runner/manager.toml reconcile\nRestart=no\n')
RECOVER = ('sudo rm -f /run/systemd/system/ci-runner-manager.service.d/runner-2338-drain.conf && '
           'sudo systemctl daemon-reload && sudo systemctl start ci-runner-manager.service')
POLICY = (Path('/etc/ci-runner/manager.toml'), Path('/usr/local/bin/ci-runner-manager'),
          Path('/usr/local/libexec/ci-runner-host-helper'))


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(args, timeout=180, check=True):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True,
                            timeout=timeout, env={**os.environ, 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                                 'LIBGUESTFS_BACKEND': 'direct'})
    if check:
        require(result.returncode == 0, Path(str(args[0])).name + '-failed')
    return result.stdout.strip()


def regular(path, owner=0):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == owner and
            info.st_nlink == 1 and not info.st_mode & 0o022, 'unsafe-file-' + path.name)


def image(path, expected):
    require(path.parent == ROOT and path.name == f'ubuntu-24.04-runner-{expected}.qcow2', 'image-path')
    info = path.lstat()
    # Existing production images are libvirt-qemu:kvm 0444; new images are root:root 0444.
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o444 and
            info.st_nlink == 1, 'image-metadata')
    require(sha(path) == expected, 'image-digest')
    data = json.loads(run(['qemu-img', 'info', '--output=json', path]))
    require(data['format'] == 'qcow2' and not data.get('backing-filename') and
            not data.get('encrypted'), 'image-must-be-standalone')


def policy_snapshot():
    for path in POLICY:
        regular(path)
    return {str(path): sha(path) for path in POLICY}


def atomic_link(path, target):
    temporary = path.with_name(path.name + '.runner2338-new')
    require(not temporary.exists() and not temporary.is_symlink(), 'temporary-link-exists')
    require(path.is_symlink() or not path.exists(), 'refusing-non-symlink')
    temporary.symlink_to(target)
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def idle():
    domains = run(['virsh', '--connect', 'qemu:///system', 'list', '--all', '--name']).splitlines()
    require(not any(x.startswith('sanctuary-ci-') for x in domains), 'runner-domain-remains')
    require(not list(OVERLAY.iterdir()), 'runner-overlay-remains')
    require(not any(x.name.startswith(('lease-', 'cleanup-')) or x.name.endswith('.tmp')
                    for x in STATE.iterdir()), 'lease-or-cleanup-remains')
    require(not run(['systemctl', 'list-units', '--all', '--type=service',
                     '--state=activating,running,reloading,deactivating', '--plain', '--no-legend',
                     'ci-runner-host-helper@*.service']), 'host-helper-active')


@contextlib.contextmanager
def drained():
    """Use the existing reconcile-only drain route; never cancel/destroy a job."""
    require(not DRAIN.exists() and not DRAIN.is_symlink(), 'existing-drain-needs-operator')
    run(['systemctl', 'stop', SERVICE])
    DRAIN.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with DRAIN.open('x') as stream:
        stream.write(DRAIN_TEXT)
    os.chmod(DRAIN, 0o644)
    run(['systemctl', 'daemon-reload'])
    require(run(['systemctl', 'show', SERVICE, '--property=Type', '--value']) == 'oneshot' and
            run(['systemctl', 'show', SERVICE, '--property=Restart', '--value']) == 'no', 'drain-unit')
    deadline = time.monotonic() + 600
    while True:
        run(['systemctl', 'start', SERVICE])
        try:
            idle()
            break
        except RuntimeError:
            require(time.monotonic() < deadline, 'drain-timeout-admission-stays-paused')
            time.sleep(15)
    # Manager remains stopped in reconcile-only mode, helper independently serializes launches.
    with open('/run/lock/ci-runner-host-helper.lock', 'a') as helper_lock:
        fcntl.flock(helper_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        idle()
        yield
    require(DRAIN.read_text() == DRAIN_TEXT, 'drain-file-changed')
    DRAIN.unlink()
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'start', SERVICE])
    require(run(['systemctl', 'is-active', SERVICE]) == 'active', 'manager-not-active')


def build():
    require(CURRENT.resolve(strict=True) == OLD, 'unexpected-current-image')
    image(OLD, OLD_SHA)
    require(shutil.disk_usage(ROOT).free >= 70 * 1024**3, 'need-70-GiB-free-for-build-and-reserve')
    for tool in ('qemu-img', 'virt-customize', 'curl'):
        require(shutil.which(tool), 'missing-' + tool)
    require(not WORK.exists(), 'build-work-directory-exists-preserved')
    WORK.mkdir(mode=0o700)
    print('build_phase=download-verified-runner', flush=True)
    archive = WORK / 'runner.tar.gz'
    run(['curl', '--fail', '--silent', '--show-error', '--location',
         f'https://github.com/actions/runner/releases/download/v{VERSION}/actions-runner-linux-x64-{VERSION}.tar.gz',
         '--output', archive], timeout=600)
    require(sha(archive) == ARCHIVE_SHA, 'runner-archive-checksum')
    print('build_phase=standalone-image-copy', flush=True)
    candidate = WORK / 'candidate.qcow2'
    run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', OLD, candidate], timeout=900)
    os.chmod(candidate, 0o600)
    guest = WORK / 'swap-runner.sh'
    guest.write_text(GUEST)
    contract = WORK / 'verify-image-contract.sh'
    contract.write_text(CONTRACT)
    # Keep full build diagnostics private in the root-owned artifact directory.
    args = ['virt-customize', '--format', 'qcow2', '-a', candidate, '--no-network',
            '--copy-in', str(archive) + ':/root', '--copy-in', str(guest) + ':/root',
            '--copy-in', str(contract) + ':/root', '--run-command', 'bash /root/swap-runner.sh']
    print('build_phase=offline-swap-and-contract', flush=True)
    result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=900, env={**os.environ, 'LIBGUESTFS_BACKEND': 'direct'})
    (WORK / 'build.log').write_bytes(result.stdout)
    require(result.returncode == 0, 'offline-build-failed-see-root-private-build.log')
    require(sha(OLD) == OLD_SHA, 'source-image-changed')
    digest = sha(candidate)
    target = ROOT / f'ubuntu-24.04-runner-{digest}.qcow2'
    require(not target.exists() and not target.is_symlink(), 'target-already-exists')
    require(shutil.disk_usage(ROOT).free >= 60 * 1024**3, 'disk-reserve')
    os.chmod(candidate, 0o444)
    # Same filesystem; no duplicate 8GiB allocation and no existing file replacement.
    os.link(candidate, target)
    candidate.unlink()
    image(target, digest)
    (WORK / 'receipt.json').write_text(json.dumps({'source_sha256': OLD_SHA,
        'image_sha256': digest, 'runner': VERSION, 'archive_sha256': ARCHIVE_SHA}) + '\n')
    print('build=verified runner=' + VERSION + ' image_sha256=' + digest, flush=True)


def github(path):
    require(re.fullmatch(r'(?:orgs/Tuinstra-DEV/actions/runners/[0-9]+|repos/Tuinstra-DEV/[A-Za-z0-9_.-]+/actions/(?:jobs/[0-9]+|runs/[0-9]+/jobs\?per_page=100))', path), 'github-path')
    for filename in ('org-github.token', 'github.token'):
        token = Path('/etc/ci-runner') / filename
        if not token.exists():
            continue
        regular(token)
        result = subprocess.run(['/usr/bin/gh', 'api', path], capture_output=True, text=True,
            timeout=40, env={'PATH': '/usr/bin:/bin', 'HOME': '/root',
                            'GH_TOKEN': token.read_text().strip(), 'GH_HOST': 'github.com'})
        if result.returncode == 0:
            return json.loads(result.stdout)
    return None


def assigned_job(data, job):
    return (type(job.get('runner_id')) is int and job['runner_id'] == data['runner_id'] and
            job.get('runner_name') == data.get('runner_name') and bool(job.get('started_at')))


def credential_preflight():
    bindings = json.loads(run(['busctl', '--json=short', 'get-property', 'org.freedesktop.systemd1',
        '/org/freedesktop/systemd1/unit/ci_2drunner_2dmanager_2eservice',
        'org.freedesktop.systemd1.Service', 'LoadCredential']))
    require(bindings.get('type') == 'a(ss)' and sorted(bindings.get('data', [])) == sorted([
        ['github_token', '/etc/ci-runner/github.token'],
        ['org_github_token', '/etc/ci-runner/org-github.token']]), 'credential-bindings-differ')
    cfg = tomllib.loads(POLICY[0].read_text())
    for key, name in (('github_token_file', 'github_token'), ('org_github_token_file', 'org_github_token')):
        require(cfg.get(key) == '/run/credentials/' + SERVICE + '/' + name, 'credential-config-differs')
    for name in ('github.token', 'org-github.token'):
        path = Path('/etc/ci-runner') / name
        regular(path)
        require(stat.S_IMODE(path.stat().st_mode) == 0o600 and bool(path.read_bytes().strip()), 'credential-missing-or-mode')
    print('credential_preflight=verified-existing-manager-bindings', flush=True)


def smoke(target):
    # No registration is created by this check: observe the manager's natural queue dispatch.
    observed = {}
    online = set()
    backing = set()
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        for path in STATE.glob('lease-*.json'):
            data = json.loads(path.read_text())
            if type(data.get('runner_id')) is int and data.get('trigger_run_id'):
                observed[data['runner_id']] = data
                domain = 'sanctuary-ci-' + data['lease']
                xml = run(['virsh', '--connect', 'qemu:///system', 'dumpxml', domain], check=False)
                # The live domain's backingStore must identify this immutable image.
                if str(target) in xml:
                    backing.add(data['runner_id'])
        for runner_id, data in observed.items():
            if data.get('scope') == 'organization':
                runner = github(f'orgs/Tuinstra-DEV/actions/runners/{runner_id}')
                if runner and runner.get('id') == runner_id and runner.get('name') == data.get('runner_name') and runner.get('status') == 'online':
                    online.add(runner_id)
                    if runner.get('busy') is True and runner_id in backing:
                        print(f"smoke=passed runner={VERSION} runner_id={runner_id} repo={data['repo']} assigned=busy image_sha256={sha_from_name(target)}", flush=True)
                        return
            # Reconciliation records cross-run handoffs using actual_job_id/actual_run_id.
            # Check that actual assignment too, not only the queue trigger's run.
            if type(data.get('actual_job_id')) is int:
                job = github(f"repos/{data['repo']}/actions/jobs/{data['actual_job_id']}")
                if job and assigned_job(data, job) and runner_id in backing:
                    print(f"smoke=passed runner={VERSION} runner_id={runner_id} repo={data['repo']} job_id={job['id']} image_sha256={sha_from_name(target)}", flush=True)
                    return
            jobs = github(f"repos/{data['repo']}/actions/runs/{data['trigger_run_id']}/jobs?per_page=100")
            for job in (jobs or {}).get('jobs', []):
                if assigned_job(data, job):
                    # Assignment itself proves the JIT runner was online even if a fast job has deregistered.
                    online.add(runner_id)
                    if runner_id in backing and runner_id in online:
                        print(f"smoke=passed runner={VERSION} runner_id={runner_id} repo={data['repo']} job_id={job['id']} backing_sha256={sha_from_name(target)}", flush=True)
                        return
        time.sleep(10)
    raise RuntimeError('no-verified-online-runner-and-job-pickup-within-600s')


def sha_from_name(target):
    return target.stem.removeprefix('ubuntu-24.04-runner-')


def activate(rollback=False):
    regular(WORK / 'receipt.json')
    receipt = json.loads((WORK / 'receipt.json').read_text())
    digest = receipt['image_sha256']
    require(re.fullmatch('[a-f0-9]{64}', digest), 'receipt-digest')
    new = ROOT / f'ubuntu-24.04-runner-{digest}.qcow2'
    expected, target = (new, OLD) if rollback else (OLD, new)
    image(target, OLD_SHA if rollback else digest)
    require(CURRENT.resolve(strict=True) == expected, 'unexpected-current-image')
    credential_preflight()
    before = policy_snapshot()
    with drained():
        require(CURRENT.resolve(strict=True) == expected and policy_snapshot() == before, 'host-drift')
        if not rollback:
            atomic_link(PREVIOUS, OLD)
        atomic_link(CURRENT, target)
    require(policy_snapshot() == before, 'manager-helper-or-config-changed')
    print('rollback=active' if rollback else 'activation=active', flush=True)
    if not rollback:
        try:
            smoke(target)
        except (Exception, KeyboardInterrupt):
            print('smoke=failed starting-safe-rollback-without-job-cancellation', flush=True)
            activate(rollback=True)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'activate', 'rollback'))
    args = parser.parse_args()
    require(os.geteuid() == 0 and os.uname().nodename == 'sanctuary', 'run-on-sanctuary-with-sudo')
    os.umask(0o077)
    with open('/run/lock/runner-2338-repair.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if args.command == 'build':
                credential_preflight()
                build()
            else:
                activate(args.command == 'rollback')
        except (Exception, KeyboardInterrupt):
            if args.command != 'build':
                print('current_image=' + str(CURRENT.resolve()), flush=True)
                print('After reviewing the failure and current image, restore admission with:', flush=True)
                print(RECOVER, flush=True)
            raise


GUEST = r'''#!/bin/bash
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
snapshot() { dpkg-query -W -f='${binary:Package}=${Version}\n' | LC_ALL=C sort; }
snapshot > /root/runner-packages.before
sha256sum /etc/systemd/system/ci-runner-job.service /usr/local/bin/run-jit-runner > /root/runner-security.before
grep -qx 'Environment=RUNNER_DISABLEUPDATE=1' /etc/systemd/system/ci-runner-job.service
test "$(/opt/actions-runner/bin/Runner.Listener --version)" = 2.336.0
test "$(grep -c '^runner=' /etc/ci-runner-image-manifest)" = 1
grep -qx 'runner=2.336.0' /etc/ci-runner-image-manifest
for state in .runner .credentials .credentials_rsaparams .runner_migrated .credentials_migrated; do
  test ! -e "/opt/actions-runner/$state"
  test ! -L "/opt/actions-runner/$state"
done
printf '%s\n' 'baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40  /root/runner.tar.gz' | sha256sum --check --strict
# Only the new offline derivative is mutated. No package install/update hooks.
rm -rf /opt/actions-runner
install -d -o root -g root -m 0755 /opt/actions-runner
tar --extract --gzip --file /root/runner.tar.gz --directory /opt/actions-runner --no-same-owner
chown -R root:root /opt/actions-runner
chmod -R go-w /opt/actions-runner
install -d -o ci-runner -g ci-runner -m 0700 /opt/actions-runner/_diag /opt/actions-runner/_work
test "$(/opt/actions-runner/bin/Runner.Listener --version)" = 2.338.2
chmod 0644 /etc/ci-runner-image-manifest
sed -i 's/^runner=2\.336\.0$/runner=2.338.2/' /etc/ci-runner-image-manifest
chmod 0444 /etc/ci-runner-image-manifest
snapshot > /root/runner-packages.after
cmp /root/runner-packages.before /root/runner-packages.after
sha256sum --check --strict /root/runner-security.before
# Register existing guest binfmt handlers in this disposable appliance kernel.
mountpoint -q /proc/sys/fs/binfmt_misc || mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc
/lib/systemd/systemd-binfmt
bash /root/verify-image-contract.sh
rm -f /root/runner.tar.gz /root/swap-runner.sh /root/verify-image-contract.sh /root/runner-packages.before /root/runner-packages.after /root/runner-security.before
'''

CONTRACT = '#!/usr/bin/env bash\nset -euo pipefail\n\n# libguestfs may omit local tool directories from its command environment.\nexport PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n\nrequired_commands=(\n  composer\n  corepack\n  curl\n  docker\n  git\n  gh\n  skopeo\n  qemu-aarch64-static\n  jq\n  node\n  npm\n  npx\n  php8.3\n  php8.4\n  trivy\n  unzip\n)\n\nfor command in "${required_commands[@]}"; do\n  command -v "$command" >/dev/null || {\n    echo "image contract missing command: $command" >&2\n    exit 1\n  }\ndone\n\ndocker buildx version\ndocker compose version\nnode --version | grep -E \'^v24\\.\' >/dev/null\nphp8.3 --version | grep -E \'^PHP 8\\.3\\.\' >/dev/null\nphp8.4 --version | grep -E \'^PHP 8\\.4\\.\' >/dev/null\n# Fail the image build before a no-new-privileges job discovers a missing module.\nphp8.3 -r \'\n  foreach (["ctype", "iconv", "openssl", "pdo", "pdo_sqlite", "sqlite3", "Zend OPcache", "zip", "xdebug"] as $ext) {\n    if (!extension_loaded($ext)) { fwrite(STDERR, "PHP 8.3 missing $ext\\n"); exit(1); }\n  }\n  try {\n    $pdo = new PDO("sqlite::memory:");\n    $pdo->exec("CREATE TABLE ci_probe (value INTEGER NOT NULL)");\n    $pdo->exec("INSERT INTO ci_probe (value) VALUES (1)");\n    if ((int) $pdo->query("SELECT value FROM ci_probe")->fetchColumn() !== 1) {\n      throw new RuntimeException("query mismatch");\n    }\n  } catch (Throwable $error) { fwrite(STDERR, "PHP 8.3 SQLite PDO unusable\\n"); exit(1); }\n\'\nXDEBUG_MODE=coverage php8.3 -r \'if (!in_array("coverage", xdebug_info("mode"), true)) { fwrite(STDERR, "PHP 8.3 coverage mode unavailable\\n"); exit(1); }\'\nskopeo --version\ngh --version\nqemu-aarch64-static --version\ntest -r /proc/sys/fs/binfmt_misc/qemu-aarch64\ngrep -E \'^flags:.*F\' /proc/sys/fs/binfmt_misc/qemu-aarch64 >/dev/null\n\nphp8.4 -r \'foreach (["ctype", "fileinfo", "iconv", "intl", "mbstring", "openssl", "pdo_pgsql", "zip", "dom", "SimpleXML", "xml", "xmlwriter", "tokenizer"] as $ext) { if (!extension_loaded($ext)) { fwrite(STDERR, "PHP 8.4 missing $ext\\n"); exit(1); } }\'\ncomposer --version | grep -E \'^Composer version 2\\.\' >/dev/null\nplaywright --version\nchromium --version\ntrivy --version\ntest -x /opt/actions-runner/run.sh\ntest -x /usr/local/bin/run-jit-runner\ntest "$(stat -c \'%U:%G:%a\' /opt/actions-runner)" = "root:root:755"\nfor runtime_file in .runner .credentials .credentials_rsaparams .runner_migrated .credentials_migrated; do\n  runtime_path="/opt/actions-runner/$runtime_file"\n  if [[ -e "$runtime_path" || -L "$runtime_path" ]]; then\n    echo "image contract found pre-existing runner state: $runtime_path" >&2\n    exit 1\n  fi\ndone\nunsafe_runner_entry="$(find /opt/actions-runner -xdev \\\n  \\( -path /opt/actions-runner/_diag -o -path /opt/actions-runner/_work \\) -prune -o \\\n  \\( \\( ! -user root -o ! -group root \\) -o \\( \\( -type f -o -type d \\) -perm /022 \\) \\) \\\n  -print -quit)"\ntest -z "$unsafe_runner_entry" || {\n  echo "image contract found mutable non-runtime runner entry: $unsafe_runner_entry" >&2\n  exit 1\n}\ntest -d /opt/ms-playwright\ntest -x /usr/local/bin/chromium\nreadlink -f /usr/local/bin/chromium | grep -E \'^/opt/ms-playwright/\' >/dev/null\ntest -s /etc/ci-runner-image-manifest\nphp83_manifest_version="$(sed -n \'s/^php83=//p\' /etc/ci-runner-image-manifest)"\nphp83_sqlite_manifest_version="$(sed -n \'s/^php83_sqlite=//p\' /etc/ci-runner-image-manifest)"\ntest -n "$php83_manifest_version"\ntest "$php83_manifest_version" = "$php83_sqlite_manifest_version"\ntest "$php83_sqlite_manifest_version" = "$(dpkg-query -W -f=\'${Version}\' php8.3-sqlite3)"\n\nfor package in "skopeo" "gh" "php8.3-xdebug" "qemu-user-static"; do\n  case "$package" in\n    php8.3-xdebug) manifest_key=php83_xdebug ;;\n    qemu-user-static) manifest_key=qemu_user_static ;;\n    *) manifest_key=$package ;;\n  esac\n  declared="$(sed -n "s/^${manifest_key}=//p" /etc/ci-runner-image-manifest)"\n  test -n "$declared"\n  test "$declared" = "$(dpkg-query -W -f=\'${Version}\' "$package")"\ndone\n\necho "immutable runner image contract passed"\n'

if __name__ == "__main__":
    main()
