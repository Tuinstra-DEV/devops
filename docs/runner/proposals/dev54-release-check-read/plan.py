"""Pure release admission delta. No filesystem writes, credentials or HTTP."""
import copy
import re
import tomllib

OLD = 'a083c789196ee8cd89b4ab3253c458d8d1de6dd6'
NEW = '716ce046a09da7a01b647e74f966ba0c89a0a7ad'
PREFIX = 'Tuinstra-DEV/devops/.github/workflows/reusable-gate-release-ci.yml@'
ROLES = ('gate-release-build', 'gate-release-verify')
POLICY = {'repositories': ['Tuinstra-DEV/gate'], 'events': ['pull_request'],
          'actors': [{'login': 'marcel-tuinstra', 'type': 'User'}]}


def render_config(original, *, rollback=False):
    source, target = (NEW, OLD) if rollback else (OLD, NEW)
    before = tomllib.loads(original.decode())
    expected = copy.deepcopy(before)
    for role in ROLES:
        actual = before.get('org_execution_roles', {}).get(role)
        if actual != POLICY | {'workflow_ref': PREFIX + source}:
            raise ValueError('release_role_baseline_changed')
        expected['org_execution_roles'][role]['workflow_ref'] = PREFIX + target
    lines = original.splitlines(keepends=True)
    current = None
    found = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(b'['):
            current = next((r for r in ROLES if stripped == ('[org_execution_roles.' + r + ']').encode()), None)
        if current and re.match(rb'^\s*workflow_ref\s*=', line):
            old = (PREFIX + source).encode()
            if current in found or line.count(old) != 1:
                raise ValueError('release_ref_layout_changed')
            lines[i] = line.replace(old, (PREFIX + target).encode())
            found.add(current)
    result = b''.join(lines)
    if found != set(ROLES) or tomllib.loads(result.decode()) != expected:
        raise ValueError('config_delta_not_exact')
    return result


def group_delta(group, *, rollback=False):
    source, target = (NEW, OLD) if rollback else (OLD, NEW)
    refs = group.get('selected_workflows', [])
    if (group.get('id') != 4 or group.get('visibility') != 'selected' or
            group.get('restricted_to_workflows') is not True or
            group.get('allows_public_repositories') is not False or
            len(refs) != 8 or len(set(refs)) != 8 or
            refs.count(PREFIX + source) != 1 or PREFIX + target in refs):
        raise ValueError('runner_group_baseline_changed')
    return group | {'selected_workflows': [PREFIX + target if ref == PREFIX + source else ref for ref in refs]}
