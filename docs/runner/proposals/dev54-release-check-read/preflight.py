#!/usr/bin/env python3
"""Read-only comparison of GitHub, Sanctuary and the fixed DEV-54 proposal."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

import plan

PACKAGE = Path(__file__).resolve().parent
PERMANENT = Path('/home/mtuinstra/dev54-gate-permanent-a083c789196e-20261119')
INPUTS = {
    'gate-pilot-transaction.py': '918bdbed1676eceb72ca4d6fdb629c9c86b4dfff49d7a5adf294e543d944218b',
    'operator_check.py': '66932bd68800c7969d03327d85b3087b5b79791d7b9dd8cb66aa2708d98be0c2',
    'read_effective.py': '6d4288e47fcf900fbf81a50b6cc35cd2abcca857a98d4260c7a466b13c2252bd',
}
MANAGER_HASH = '82d9abb407546ebefab4a68f944232cd791415f981ab205e558ed9b0dca3a66c'


def load_base():
    for leaf, digest in INPUTS.items():
        path = PERMANENT / leaf
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('permanent_operator_package_changed')
    spec = importlib.util.spec_from_file_location('release_read_base', PERMANENT / 'gate-pilot-transaction.py')
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    base.PACKAGE = PERMANENT
    base.INPUT_HASHES = INPUTS
    return base


def verify_policy(base, manager, original, group, repositories):
    candidate = plan.render_config(original)
    if plan.render_config(candidate, rollback=True) != original:
        raise ValueError('config_inverse_differs')
    current = base.parse_manager_config(original)
    changed = base.parse_manager_config(candidate)
    manager.validate_org_config(current)
    manager.validate_org_config(changed)
    proposed_group = plan.group_delta(group)
    if not base.group_policy_matches(group):
        raise ValueError('runner_group_policy_changed')
    for config, expected in ((current, group), (changed, proposed_group)):
        if set(manager.configured_org_workflow_refs(config)) != set(expected['selected_workflows']):
            raise ValueError('manager_and_group_disagree')
    selected = base.repository_map(repositories)
    if len(selected) != 11 or current.get('org_repository_ids') != selected:
        raise ValueError('repository_selection_changed')
    return candidate, proposed_group


def inspect():
    if os.geteuid() != 0:
        raise ValueError('existing_operator_sudo_required')
    base = load_base()
    base.secure_regular(base.MANAGER, MANAGER_HASH)
    base.secure_regular(base.HELPER, base.HELPER_HASH)
    if not base.SELECTOR.is_symlink() or base.SELECTOR.resolve(strict=True) != base.NEW_IMAGE:
        raise ValueError('active_image_selector_changed')
    base.verify_sealed_candidate(base.NEW_IMAGE)
    if base.service_state()['ActiveState'] != 'active':
        raise ValueError('manager_not_running')
    manager = base.load_module(base.MANAGER, 'release_read_effective_manager')
    original = base.secure_regular(base.CONFIG)
    cfg = base.parse_manager_config(original)
    checker = base.load_operator_checker(PERMANENT)
    _, client = base.existing_manager_client(cfg, checker, manager)
    group, repositories = base.read_group(client, cfg)
    operator_group = json.loads((PACKAGE / 'group-state.json').read_bytes())
    operator_repositories = json.loads((PACKAGE / 'repositories-state.json').read_bytes())
    if group != operator_group or base.repository_map(repositories) != base.repository_map(operator_repositories):
        raise ValueError('operator_github_snapshot_differs')
    verify_policy(base, manager, original, group, repositories)
    try:
        base.ensure_empty()
        empty = True
    except base.Blocked as error:
        if error.code != 'runner_window_not_empty':
            raise
        empty = False
    if base.secure_regular(base.CONFIG) != original:
        raise ValueError('effective_config_changed_during_read')
    return {'all_checks_green': True, 'read_only': True, 'effective_config_delta_verified': True,
            'config_inverse_verified': True, 'manager_sha256': MANAGER_HASH,
            'image_sha256': base.CANDIDATE_HASH, 'runner_window_empty': empty,
            'workflow_refs': len(group['selected_workflows']), 'repositories': 11}


if __name__ == '__main__':
    try:
        print(json.dumps(inspect()))
    except Exception as error:
        code = getattr(error, 'code', None)
        if code is None:
            code = str(error) if type(error) is ValueError and re.fullmatch('[a-z0-9_]+', str(error)) else type(error).__name__
        result = {'all_checks_green': False, 'read_only': True, 'blocker': code}
        status = getattr(error, 'http_status', None)
        if type(status) is int and 100 <= status <= 599:
            result['http_status'] = status
        elif match := re.search(r'HTTP ([1-5][0-9]{2})', str(error)):
            result['http_status'] = int(match[1])
        print(json.dumps(result))
        sys.exit(1)
