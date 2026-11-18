#!/usr/bin/env python3
"""Render a reviewed admission plan; never writes runner settings or calls GitHub."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runner/manager'))
import ci_runner_manager as manager  # noqa: E402


def plan(group, repositories, pin):
    if not re.fullmatch(r'[a-f0-9]{40}', pin):
        raise ValueError('Expected an immutable 40-character workflow commit')
    refs = group.get('selected_workflows')
    ids = [r['id'] for r in repositories['repositories']]
    if (group.get('id') != 4 or group.get('name') != 'sanctuary-trusted-verification' or
            group.get('visibility') != 'selected' or group.get('allows_public_repositories') is not False or
            group.get('restricted_to_workflows') is not True or not isinstance(refs, list) or
            len(refs) != len(set(refs)) or repositories['total_count'] != len(ids) or
            len(ids) != len(set(ids)) or not any(r['id'] == 1273386924 and r['full_name'] == 'Tuinstra-DEV/gate' for r in repositories['repositories'])):
        raise ValueError('Unexpected runner-group or repository-selection policy')
    if any('/reusable-product-ci.yml@' in ref for ref in refs):
        raise ValueError('Temporary pilot admission must be rolled back first')
    roles = {}
    for role in ('gate-backend', 'gate-release-browser'):
        ref = manager.EXECUTION_ROLES[role]['workflow'] + '@' + pin
        if ref in refs:
            raise ValueError('New admission is already present; obtain a fresh baseline')
        roles[role] = {'workflow_ref': ref, 'repositories': ['Tuinstra-DEV/gate'],
                       'events': ['pull_request'], 'actors': [{'login': 'marcel-tuinstra', 'type': 'User'}]}
    proposed = {**group, 'selected_workflows': refs + [r['workflow_ref'] for r in roles.values()]}
    return {'read_only_plan': True, 'manager_sha256': hashlib.sha256((ROOT / 'runner/manager/ci_runner_manager.py').read_bytes()).hexdigest(),
            'workflow_pin': pin, 'runner_group_before': group, 'runner_group_after': proposed,
            'repository_ids_unchanged': ids, 'merge_into_existing_org_execution_roles': roles,
            'image': json.loads((ROOT / 'runner/policy/gate-compact-image.json').read_text()),
            'enable_after_activation': {'SANCTUARY_GATE_BACKEND_ENABLED': 'true'},
            'rollback': 'disable the opt-in, drain; restore original manager/config bytes+metadata, selector and complete group snapshot; verify shared dispatch'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', type=Path, required=True)
    parser.add_argument('--repositories', type=Path, required=True)
    parser.add_argument('--workflow-sha', required=True)
    args = parser.parse_args()
    print(json.dumps(plan(json.loads(args.group.read_text()), json.loads(args.repositories.read_text()), args.workflow_sha), indent=2))
