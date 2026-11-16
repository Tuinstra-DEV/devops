#!/usr/bin/env python3
"""Read-only operator checks. Output only bounded verification results, never secrets."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET

from read_effective import inspect

SYSTEMD_CREDENTIAL_DIRECTORY = Path('/run/credentials/ci-runner-manager.service')

PHP = '''echo json_encode([
"php_version" => PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION,
"pdo_mysql" => extension_loaded("pdo_mysql") && in_array("mysql",PDO::getAvailableDrivers(),true),
"pdo_sqlite" => extension_loaded("pdo_sqlite") && in_array("sqlite",PDO::getAvailableDrivers(),true)
]);'''
GUEST_DOCKER = '''import json,os,pwd,subprocess
r={"runner_user":pwd.getpwuid(os.geteuid()).pw_name,"docker_daemon":False,"storage_driver":None}
try:
 p=subprocess.run(["/usr/bin/docker","info","--format","{{json .Driver}}"],capture_output=True,text=True,timeout=8)
 if p.returncode==0:
  r["storage_driver"]=json.loads(p.stdout);r["docker_daemon"]=True
except Exception: pass
print(json.dumps(r))
'''


class Blocked(Exception):
    def __init__(self, code, http_status=None):
        self.code, self.http_status = code, http_status


def command(args, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise Blocked('command_failed_' + Path(args[0]).name)
    return result.stdout


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def manager_environment(pid):
    # Never print, copy into our environment, or return this service environment
    # to the operator. The caller selects only CREDENTIALS_DIRECTORY in memory.
    return (Path('/proc') / pid / 'environ').read_bytes()


def loaded_manager_credential(cfg, module):
    path = SYSTEMD_CREDENTIAL_DIRECTORY / 'org_github_token'
    if cfg.get('org_github_token_file') != str(path):
        raise Blocked('credential_service_binding_differs')
    # Diagnose file reachability before the reader's context-sensitive mode
    # check. No fallback to /etc source files, chmod, copies or other credentials.
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise Blocked('credential_service_binding_differs')
        with path.open('rb'):
            pass
    except OSError:
        raise Blocked('credential_file_unreachable') from None
    try:
        binding = json.loads(command(['busctl', '--json=short', 'get-property',
            'org.freedesktop.systemd1', '/org/freedesktop/systemd1/unit/ci_2drunner_2dmanager_2eservice',
            'org.freedesktop.systemd1.Service', 'LoadCredential']))
    except Exception:
        raise Blocked('credential_service_binding_unavailable') from None
    expected = [['github_token', '/etc/ci-runner/github.token'],
                ['org_github_token', '/etc/ci-runner/org-github.token']]
    if binding.get('type') != 'a(ss)' or sorted(binding.get('data', [])) != sorted(expected):
        raise Blocked('credential_service_binding_differs')
    pid_args = ['systemctl', 'show', 'ci-runner-manager.service', '-p', 'MainPID', '--value']
    try:
        pid = command(pid_args).strip()
        if not re.fullmatch(r'[1-9][0-9]*', pid):
            raise ValueError('inactive service')
        environment = manager_environment(pid)
    except Exception:
        raise Blocked('credential_service_context_unavailable') from None
    directories = [entry.partition(b'=')[2] for entry in environment.split(b'\0')
                   if entry.startswith(b'CREDENTIALS_DIRECTORY=')]
    if not directories:
        raise Blocked('credential_service_context_missing')
    if directories != [os.fsencode(SYSTEMD_CREDENTIAL_DIRECTORY)]:
        raise Blocked('credential_service_context_differs')
    # Reproduce only the verified active service's credential boundary in this
    # probe process, just for the unchanged manager reader. sudo normally drops
    # this variable. Never inherit the rest of the service environment.
    previous = os.environ.get('CREDENTIALS_DIRECTORY')
    try:
        os.environ['CREDENTIALS_DIRECTORY'] = os.fsdecode(directories[0])
        try:
            token = module.read_token(path)
        except OSError:
            raise Blocked('credential_file_unreachable') from None
        except Exception:
            raise Blocked('credential_reader_rejected') from None
    finally:
        if previous is None:
            os.environ.pop('CREDENTIALS_DIRECTORY', None)
        else:
            os.environ['CREDENTIALS_DIRECTORY'] = previous
    try:
        if command(pid_args).strip() != pid:
            raise Blocked('credential_service_context_changed')
    except Blocked:
        raise Blocked('credential_service_context_changed') from None
    return token


def credential_checks(cfg, manifest, module):
    if cfg.get('github_api_url', 'https://api.github.com').rstrip('/') != 'https://api.github.com':
        raise Blocked('github_api_origin_differs')
    # Use only the existing manager credential in memory. No gh auth output,
    # token printing, Environment output, shell tracing, or new permission grant.
    token = loaded_manager_credential(cfg, module)
    try:
        client = module.GitHubClient(token)
    except Exception:
        raise Blocked('credential_value_rejected') from None
    results = {}

    def get(name, path):
        try:
            value = client.request('GET', path)
        except Exception as exc:
            match = re.search(r'HTTP ([0-9]{3})', str(exc))
            raise Blocked('credential_get_' + name + '_failed', int(match[1]) if match else None) from None
        results[name] = True
        return value

    repo = '/repos/Tuinstra-DEV/gate'
    pr = get('pull_request', repo + '/pulls/444')
    if (pr.get('state') != 'open' or pr.get('head', {}).get('sha') != manifest['gate_remote_head'] or
            pr.get('base', {}).get('sha') != manifest['gate_base']):
        raise Blocked('gate_candidate_differs')
    branch = get('protected_develop', repo + '/branches/develop')
    if branch.get('protected') is not True or branch.get('commit', {}).get('sha') != manifest['gate_base']:
        raise Blocked('protected_develop_differs')
    merge_sha = pr.get('merge_commit_sha')
    if not isinstance(merge_sha, str) or not re.fullmatch(r'[a-f0-9]{40}', merge_sha):
        raise Blocked('candidate_merge_not_available')
    merge = get('merge_commit', repo + '/git/commits/' + merge_sha)
    if (merge.get('sha') != merge_sha or len(merge.get('parents', [])) != 2 or
            {p.get('sha') for p in merge['parents']} != {manifest['gate_remote_head'], manifest['gate_base']}):
        raise Blocked('candidate_merge_parents_differ')
    get('actions_run', repo + '/actions/runs/43845010575')
    get('actions_jobs', repo + '/actions/runs/43845010575/jobs?per_page=100')
    try:
        client.verify_org_group(cfg)
    except Exception as exc:
        match = re.search(r'HTTP ([0-9]{3})', str(exc))
        raise Blocked('credential_runner_group_read_failed', int(match[1]) if match else None) from None
    results['runner_group_and_repositories'] = True
    return results


def active_image(package, helper=Path('/usr/local/libexec/ci-runner-host-helper'),
                 selector=Path('/var/lib/ci-runner/images/ubuntu-24.04-runner.qcow2'), verify_content=True):
    expected = json.loads((package / 'operator-manifest.json').read_text())
    if sha256_file(helper) != expected['helper_sha256']:
        raise Blocked('active_image_selector_helper_changed')
    image = selector.resolve(strict=True)
    if str(image) != expected['active_image']:
        raise Blocked('active_runner_image_changed')
    match = re.fullmatch(r'ubuntu-24\.04-runner-([a-f0-9]{64})\.qcow2', image.name)
    if match is None or (verify_content and sha256_file(image) != match[1]):
        raise Blocked('selected_runner_image_hash_differs')
    return image


def image_php_check(image):
    # libguestfs --ro inspects the selected sealed image, not host PHP. Any
    # appliance scratch state is disposable; the image itself is never changed.
    probe = '/usr/sbin/runuser -u ci-runner -- /usr/bin/php8.3 -d opcache.enable_cli=0 -r ' + shlex.quote(PHP)
    try:
        result = json.loads(command(['guestfish', '--ro', '--format=qcow2', '-a', str(image), '-i', 'sh', probe], timeout=180))
    except Exception:
        raise Blocked('selected_image_php_probe_failed') from None
    allowed = {key: result.get(key) for key in ['php_version', 'pdo_mysql', 'pdo_sqlite']}
    if allowed['php_version'] != '8.3': raise Blocked('selected_image_php_version_differs')
    if allowed['pdo_mysql'] is not True: raise Blocked('selected_image_pdo_mysql_missing')
    if allowed['pdo_sqlite'] is not True: raise Blocked('selected_image_pdo_sqlite_missing')
    return allowed


def docker_guest_check(image):
    domains = command(['virsh', '-c', 'qemu:///system', 'list', '--name']).splitlines()
    matches, connected = [], []
    for domain in domains:
        if not re.fullmatch(r'sanctuary-ci-[A-Za-z0-9._-]+', domain): continue
        root = ET.fromstring(command(['virsh', '-c', 'qemu:///system', 'dumpxml', domain]))
        sources = root.findall('./devices/disk/backingStore/source')
        if not any(source.get('file') == str(image) for source in sources): continue
        matches.append(domain)
        agent = root.find('./devices/channel/target[@name="org.qemu.guest_agent.0"]')
        if agent is not None and agent.get('state') == 'connected': connected.append(domain)
    if not matches: raise Blocked('no_running_runner_from_selected_image')
    if not connected: raise Blocked('selected_image_runner_guest_agent_disconnected')
    # No VM is booted, registered, stopped or modified. Only an existing guest's
    # read-only docker info runs as the existing ci-runner account.
    domain = sorted(connected)[0]

    def agent(payload):
        try:
            return json.loads(command(['virsh', '-c', 'qemu:///system', 'qemu-agent-command', domain,
                                       json.dumps(payload), '--timeout', '10']))['return']
        except Exception:
            raise Blocked('existing_guest_agent_command_unavailable') from None

    process = agent({'execute': 'guest-exec', 'arguments': {
        'path': '/usr/sbin/runuser', 'arg': ['-u', 'ci-runner', '--', '/usr/bin/python3', '-c', GUEST_DOCKER],
        'capture-output': True}})
    for _ in range(25):
        status = agent({'execute': 'guest-exec-status', 'arguments': {'pid': process['pid']}})
        if status.get('exited'):
            if status.get('exitcode') != 0: raise Blocked('guest_docker_probe_failed')
            try:
                decoded = base64.b64decode(status.get('out-data', ''), validate=True)
                if len(decoded) > 2048: raise ValueError('bounded output')
                value = json.loads(decoded)
            except Exception:
                raise Blocked('guest_docker_probe_invalid_result') from None
            if value.get('runner_user') != 'ci-runner': raise Blocked('guest_probe_wrong_user')
            if value.get('docker_daemon') is not True: raise Blocked('guest_docker_daemon_unavailable')
            if value.get('storage_driver') != 'overlay2': raise Blocked('guest_docker_storage_driver_differs')
            return {'verified_in_runner_guest': True, 'runner_user': 'ci-runner', 'storage_driver': 'overlay2'}
        time.sleep(1)
    raise Blocked('guest_docker_probe_timeout')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--group-state', type=Path, required=True)
    parser.add_argument('--repositories-state', type=Path, required=True)
    args = parser.parse_args()
    package = Path(__file__).parent
    result = {'all_checks_green': False, 'policy': 'not_checked', 'credential_read_access': 'not_checked',
              'selected_image_php': 'not_checked', 'runner_guest_docker': 'not_checked'}
    stage = 'policy'
    try:
        if os.geteuid() != 0: raise Blocked('existing_operator_sudo_required')
        if (command(['systemctl', 'show', 'ci-runner-manager.service', '-p', 'ActiveState', '--value']).strip() != 'active' or
                command(['systemctl', 'show', 'ci-runner-manager.service', '-p', 'User', '--value']).strip() != 'ci-runner-manager' or
                'argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30' not in
                command(['systemctl', 'show', 'ci-runner-manager.service', '-p', 'ExecStart', '--value'])):
            raise Blocked('effective_service_binding_changed')
        config = Path('/etc/ci-runner/manager.toml')
        original_config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
        result['policy'] = inspect(Path('/usr/local/bin/ci-runner-manager'), config, args.group_state,
                                   args.repositories_state, package)
        cfg = tomllib.loads(config.read_text())
        manifest = json.loads((package / 'manifest.json').read_text())
        stage = 'credential_read_access'
        result[stage] = credential_checks(cfg, manifest, sys.modules['effective_manager'])
        stage = 'selected_image_php'
        image = active_image(package)
        image_stat = (image.stat().st_size, image.stat().st_mtime_ns)
        result['selected_image_sha256_verified'] = True
        result[stage] = image_php_check(image)
        stage = 'runner_guest_docker'
        result[stage] = docker_guest_check(image)
        if (active_image(package, verify_content=False) != image or
                (image.stat().st_size, image.stat().st_mtime_ns) != image_stat or
                hashlib.sha256(config.read_bytes()).hexdigest() != original_config_hash):
            raise Blocked('effective_state_changed_during_check')
        result['all_checks_green'] = True
    except Blocked as exc:
        result['blocked_stage'], result['blocker'] = stage, exc.code
        if exc.http_status is not None: result['http_status'] = exc.http_status
    except Exception as exc:
        result['blocked_stage'], result['blocker'] = stage, 'check_failed'
        result['error_class'] = type(exc).__name__
        known_policy_errors = {
            'manager fingerprint differs': 'deployed_manager_hash_differs',
            'fresh GitHub snapshots required': 'github_snapshots_stale',
            'effective policy differs': 'effective_manager_or_group_policy_differs',
            'repository access differs': 'repository_selection_differs',
            'role already present': 'pilot_role_already_present',
        }
        if stage == 'policy' and type(exc) is ValueError:
            result['blocker'] = known_policy_errors.get(str(exc), 'effective_policy_validation_failed')
    print(json.dumps(result, separators=(',', ':')))
    return 0 if result['all_checks_green'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
