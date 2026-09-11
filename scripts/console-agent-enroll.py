#!/usr/bin/env python3
"""Enroll an empty production host without printing or persisting a token locally.

Uses the existing Console host-scoped issuance CLI. Never rotates credentials.
The private image is streamed from an already authenticated source Docker host;
no registry credentials are copied to the target. Run after host bootstrap.
"""
import argparse
import hashlib
import pathlib
import re
import shlex
import subprocess
import sys

IMAGE_REF = 'ghcr.io/tuinstra-dev/console@sha256:b29e7355aa96cf203d74c4b041a20bafde69ae916dfddef8bd9ff2a6ddda6b72'
IMAGE_ID = 'sha256:f9aa80b69a2f6b9e46dc8961bab6b50a9f7bd9d2971c21df0f4be2a3b9c4a939'
INSTALL_DIR = '/var/www/_platform/console-agent'
SSH_OPTIONS = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15']


def ssh_command(target, command):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*@[a-zA-Z0-9.-]+', target):
        raise ValueError('Use an explicit user@host SSH destination')
    return ['ssh', *SSH_OPTIONS, target, command]


def remote(target, args, *, data=None, allow_failure=False):
    command = shlex.join(args)
    try:
        result = subprocess.run(ssh_command(target, command), input=data, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'Remote timeout on {target}; inspect target/hub state before retrying') from None
    if result.returncode and not allow_failure:
        # No raw output: the issuance CLI may include a token or installation hint.
        raise RuntimeError(f'Remote command failed on {target}; exit {result.returncode}')
    return result


def token_from_output(output):
    tokens = set(re.findall(rb'cag_[A-Za-z0-9_-]+', output))
    if len(tokens) != 1:
        raise RuntimeError('Expected exactly one issued token; raw output suppressed')
    return tokens.pop() + b'\n'


def ensure_image(source, target):
    check = remote(target, ['sudo', '-n', 'docker', 'image', 'inspect', IMAGE_ID,
                            '--format', '{{.Id}}'], allow_failure=True)
    if check.returncode == 0 and check.stdout.strip().decode() == IMAGE_ID:
        return
    source_id = remote(source, ['docker', 'image', 'inspect', IMAGE_REF,
                                '--format', '{{.Id}}']).stdout.strip().decode()
    if source_id != IMAGE_ID:
        raise RuntimeError('Source image identity differs from reviewed image')
    print('Streaming the verified Console image over SSH...', flush=True)
    save_pipeline = shlex.join(['docker', 'image', 'save', IMAGE_ID]) + ' | gzip -1'
    producer = subprocess.Popen(ssh_command(source, shlex.join(['bash', '-o', 'pipefail', '-c', save_pipeline])),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        consumer = subprocess.run(ssh_command(target, 'sudo -n docker image load'),
                                  stdin=producer.stdout, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=1200)
        producer.stdout.close()
        producer.wait(timeout=30)
        if consumer.returncode or producer.returncode:
            raise RuntimeError('Image transport failed; no credential has been issued')
    finally:
        if producer.poll() is None:
            producer.terminate()
            producer.wait()
    actual = remote(target, ['sudo', '-n', 'docker', 'image', 'inspect', IMAGE_ID,
                             '--format', '{{.Id}}']).stdout.strip().decode()
    if actual != IMAGE_ID:
        raise RuntimeError('Target image identity mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Existing Docker image source, user@host')
    parser.add_argument('--hub', required=True, help='Console CLI host, user@host')
    parser.add_argument('--target', required=True, help='New admin user@host')
    parser.add_argument('--host-slug', required=True)
    args = parser.parse_args()
    for target in (args.source, args.hub, args.target):
        ssh_command(target, 'true')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}', args.host_slug):
        raise ValueError('Invalid host slug')
    actual = remote(args.target, ['hostname']).stdout.strip().decode()
    if actual != args.host_slug:
        raise RuntimeError('Target hostname must match Console host slug')
    env = INSTALL_DIR + '/.env'
    exists = remote(args.target, ['sudo', '-n', 'test', '-e', INSTALL_DIR], allow_failure=True)
    if exists.returncode == 0:
        raise RuntimeError('Console installation already exists; verify or repair explicitly, never rotate automatically')
    if exists.returncode != 1:
        raise RuntimeError('Could not establish installation state')
    installer = pathlib.Path(__file__).resolve().parents[1] / 'infra/console-agent/install-container.sh'
    content = installer.read_bytes()
    remote(args.target, ['sudo', '-n', 'install', '-d', '-m', '0755', '/usr/local/lib/tuinstra'])
    remote(args.target, ['sudo', '-n', 'tee', '/usr/local/lib/tuinstra/install-console-agent.sh'], data=content)
    remote(args.target, ['sudo', '-n', 'chmod', '0755', '/usr/local/lib/tuinstra/install-console-agent.sh'])
    remote_hash = remote(args.target, ['sha256sum', '/usr/local/lib/tuinstra/install-console-agent.sh']).stdout.split()[0].decode()
    if remote_hash != hashlib.sha256(content).hexdigest():
        raise RuntimeError('Remote installer checksum differs')
    ensure_image(args.source, args.target)
    agent_id = 'agent:' + args.host_slug
    console = ['docker', 'exec', 'console_prod_php', 'php', 'bin/console']
    token = None
    issued = False
    try:
        result = remote(args.hub, console + ['app:agent:issue-token', args.host_slug, agent_id,
                        '--display-name=' + args.host_slug, '--environment=production',
                        '--no-interaction', '--no-ansi'])
        issued = True
        token = token_from_output(result.stdout)
        # Keep token only in process memory and the installer's protected target env.
        result = None
        remote(args.target, ['sudo', '-n', 'env', 'INSTALL_DIR=' + INSTALL_DIR,
                 '/usr/local/lib/tuinstra/install-console-agent.sh', '--host-slug', args.host_slug,
                 '--agent-id', agent_id, '--environment', 'production', '--image', IMAGE_ID,
                 '--token-stdin', '--no-start'], data=token)
        token = None
        compose = ['sudo', '-n', 'docker', 'compose', '--env-file', env,
                   '-f', INSTALL_DIR + '/docker-compose.yml']
        remote(args.target, compose + ['config', '--quiet'])
        source_record = (f'Upstream: {IMAGE_REF}\nImage ID: {IMAGE_ID}\n'
                         f'Installer sha256: {hashlib.sha256(content).hexdigest()}\n')
        remote(args.target, ['sudo', '-n', 'tee', INSTALL_DIR + '/source-image.txt'],
               data=source_record.encode())
        remote(args.target, compose + ['up', '-d', '--pull', 'never'])
        print(f'{args.host_slug}: agent started; verify accepted inventory and last_used_at at Console.')
    except Exception:
        if issued and 'compose' in locals():
            try:
                remote(args.target, compose + ['stop'], allow_failure=True)
            except RuntimeError:
                print('Exact agent stack stop could not be verified.', file=sys.stderr)
        if issued:
            try:
                remote(args.hub, console + ['app:agent:set-enabled', agent_id, 'disabled',
                       '--no-interaction', '--no-ansi'])
                print('New credential disabled after failed enrollment.', file=sys.stderr)
            except Exception:
                print('Credential disablement could not be verified: operator intervention required.', file=sys.stderr)
        raise
    finally:
        token = None


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
