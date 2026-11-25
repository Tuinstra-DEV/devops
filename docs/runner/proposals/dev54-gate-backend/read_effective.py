#!/usr/bin/env python3
"""Read-only policy preflight. Never reads credentials or changes host/GitHub state."""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import time
import tomllib


def inspect(manager_path, config_path, group_path, repositories_path, package, after=False, before_config=None):
    manifest = json.loads((package / 'manifest.json').read_text())
    expected_hash = manifest['deployed_manager_after_sha256' if after else 'deployed_manager_before_sha256']
    if hashlib.sha256(manager_path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('manager fingerprint differs')
    if any(abs(time.time() - path.stat().st_mtime) > 300 for path in (group_path, repositories_path)):
        raise ValueError('fresh GitHub snapshots required')
    loader = importlib.machinery.SourceFileLoader('effective_manager', str(manager_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    cfg = tomllib.loads(config_path.read_text())
    module.validate_org_config(cfg)
    before = json.loads((package / ('group-after.json' if after else 'group-before.json')).read_text())['selected_workflows']
    group = json.loads(group_path.read_text())
    refs = group.get('selected_workflows')
    if (group.get('id') != 4 or group.get('name') != 'sanctuary-trusted-verification' or
            group.get('visibility') != 'selected' or group.get('restricted_to_workflows') is not True or
            group.get('allows_public_repositories') is not False or group.get('inherited') is not False or
            group.get('default') is not False or not isinstance(refs, list) or
            len(refs) != (7 if after else 6) or set(refs) != set(before) or
            set(module.configured_org_workflow_refs(cfg)) != set(before) or
            cfg.get('org_runner_group_id') != 4):
        raise ValueError('effective policy differs')
    if after:
        expected_role = tomllib.loads((package / 'manager-role.toml').read_text())['org_execution_roles']['gate-backend-pilot']
        cfg_without_role = tomllib.loads(config_path.read_text())
        if (before_config is None or
                cfg_without_role.get('org_execution_roles', {}).pop('gate-backend-pilot', None) != expected_role or
                cfg_without_role != tomllib.loads(before_config.read_text())):
            raise ValueError('only the exact new role may change')
    elif 'gate-backend-pilot' in cfg.get('org_execution_roles', {}):
        raise ValueError('role already present')
    repositories = json.loads(repositories_path.read_text())
    rows = repositories.get('repositories')
    if not isinstance(rows, list) or repositories.get('total_count') != len(rows) or any(
            not isinstance(row, dict) or row.get('private') is not True or
            type(row.get('id')) is not int for row in rows):
        raise ValueError('repository snapshot invalid')
    actual = {row['full_name']: row['id'] for row in rows}
    if len(actual) != len(rows) or actual != cfg['org_repository_ids'] or actual != json.loads((package / 'repositories-before.json').read_text()):
        raise ValueError('repository access differs')
    # Do not serialize config, token paths, credentials, or arbitrary exception text.
    return {'policy_verified': True, 'existing_workflow_refs': len(before),
            'existing_repository_count': len(actual), 'proposed_workflow_ref': manifest['workflow_ref'],
            'runtime_and_host_credential_readiness': 'separate required checks; not verified here'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manager', type=Path, default=Path('/usr/local/bin/ci-runner-manager'))
    parser.add_argument('--config', type=Path, default=Path('/etc/ci-runner/manager.toml'))
    parser.add_argument('--group-state', type=Path, required=True)
    parser.add_argument('--repositories-state', type=Path, required=True)
    parser.add_argument('--after', action='store_true')
    parser.add_argument('--before-config', type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.manager, args.config, args.group_state, args.repositories_state, Path(__file__).parent,
                         args.after, args.before_config)
    except Exception as exc:
        print(json.dumps({'policy_verified': False, 'error_class': type(exc).__name__}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
