"""Exact reversible release-only delta; no live settings or credentials."""
import copy
import importlib.util
import sys
from pathlib import Path
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / 'docs/runner/proposals/dev54-release-check-read/plan.py'
spec = importlib.util.spec_from_file_location('release_read_plan', PATH)
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)
with mock.patch.dict(sys.modules, {'plan': plan}):
    spec = importlib.util.spec_from_file_location('release_read_preflight', PATH.with_name('preflight.py'))
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)


def config():
    text = '# original bytes and comments must survive\nmax_parallel = 3\n'
    for role in ('gate-backend', *plan.ROLES):
        ref = ('Tuinstra-DEV/devops/.github/workflows/reusable-gate-ci.yml@' + plan.OLD) if role == 'gate-backend' else plan.PREFIX + plan.OLD
        text += ('\n[org_execution_roles.' + role + ']\nworkflow_ref = "' + ref + '" # keep\n'
                 'repositories = ["Tuinstra-DEV/gate"]\nevents = ["pull_request"]\n'
                 'actors = [{ login = "marcel-tuinstra", type = "User" }]\n')
    return text.encode()


class ReleaseReadPlanTests(unittest.TestCase):
    def test_only_two_refs_change_and_config_rollback_is_byte_exact(self):
        original = config()
        changed = plan.render_config(original)
        self.assertEqual(changed.count(plan.NEW.encode()), 2)
        self.assertIn(('reusable-gate-ci.yml@' + plan.OLD).encode(), changed)
        self.assertEqual(plan.render_config(changed, rollback=True), original)
        with self.assertRaises(ValueError): plan.render_config(changed)

    def test_inline_roles_from_permanent_activation_round_trip(self):
        entries = []
        for role in plan.ROLES:
            entries.append(
                f'{role} = {{ workflow_ref = "{plan.PREFIX}{plan.OLD}", '
                'repositories = ["Tuinstra-DEV/gate"], events = ["pull_request"], '
                'actors = [{ login = "marcel-tuinstra", type = "User" }] }'
            )
        original = ('org_execution_roles = { ' + ', '.join(entries) + ' } # keep\n').encode()
        changed = plan.render_config(original)
        self.assertEqual(changed.count(plan.NEW.encode()), 2)
        self.assertEqual(plan.render_config(changed, rollback=True), original)

    def test_extra_textual_match_does_not_rewrite_unrelated_bytes(self):
        original = ('# ' + plan.PREFIX + plan.OLD + '\n').encode() + config()
        with self.assertRaisesRegex(ValueError, 'release_ref_layout_changed'):
            plan.render_config(original)

    def test_trust_or_pin_drift_fails_closed(self):
        for old, new in [('marcel-tuinstra', 'unknown'), ('"pull_request"', '"push"'),
                         ('Tuinstra-DEV/gate', 'Tuinstra-DEV/other'), (plan.OLD, plan.NEW),
                         ('workflow_ref = ', 'workflow_ref\t=\t')]:
            raw = config().replace(old.encode(), new.encode())
            if old.startswith('workflow_ref'):
                self.assertEqual(plan.render_config(plan.render_config(raw), rollback=True), raw)
            else:
                with self.subTest(new=new), self.assertRaises(ValueError): plan.render_config(raw)

    def test_group_changes_one_exact_ref_and_rolls_back_without_mutating_input(self):
        group = {'id': 4, 'visibility': 'selected', 'restricted_to_workflows': True,
                 'allows_public_repositories': False, 'selected_workflows': [plan.PREFIX + plan.OLD] + ['existing-' + str(i) for i in range(7)], 'name': 'sanctuary-trusted-verification'}
        before = copy.deepcopy(group)
        changed = plan.group_delta(group)
        self.assertEqual(group, before)
        self.assertEqual(plan.group_delta(changed, rollback=True), before)
        for field, value in [('restricted_to_workflows', False), ('allows_public_repositories', True), ('selected_workflows', [])]:
            with self.subTest(field=field), self.assertRaises(ValueError): plan.group_delta(group | {field: value})


class ReleaseReadPreflightTests(unittest.TestCase):
    def test_preflight_requires_both_pin_sets_and_selected_repositories_to_match(self):
        selected = {f'Tuinstra-DEV/test-{i}': i for i in range(1, 11)} | {'Tuinstra-DEV/gate': 1273386924}
        original = config() + ('\n[org_repository_ids]\n' + ''.join(f'"{name}" = {ident}\n' for name, ident in selected.items())).encode()
        core = 'Tuinstra-DEV/devops/.github/workflows/reusable-gate-ci.yml@' + plan.OLD
        existing = [core] + [f'existing-{i}' for i in range(6)]
        group = {'id': 4, 'visibility': 'selected', 'restricted_to_workflows': True,
                 'allows_public_repositories': False, 'selected_workflows': existing + [plan.PREFIX + plan.OLD]}
        base = mock.Mock()
        base.parse_manager_config.side_effect = lambda raw: plan.tomllib.loads(raw.decode())
        base.repository_map.return_value = selected
        manager = mock.Mock()
        manager.configured_org_workflow_refs.side_effect = lambda cfg: existing + [cfg['org_execution_roles']['gate-release-build']['workflow_ref']]
        proposed, new_group = preflight.verify_policy(base, manager, original, group, {})
        self.assertEqual(plan.render_config(proposed, rollback=True), original)
        self.assertEqual(new_group['selected_workflows'], existing + [plan.PREFIX + plan.NEW])
        self.assertEqual(manager.validate_org_config.call_count, 2)
        manager.configured_org_workflow_refs.side_effect = None
        manager.configured_org_workflow_refs.return_value = group['selected_workflows']
        with self.assertRaisesRegex(ValueError, 'manager_and_group_disagree'):
            preflight.verify_policy(base, manager, original, group, {})
        manager.configured_org_workflow_refs.side_effect = lambda cfg: existing + [cfg['org_execution_roles']['gate-release-build']['workflow_ref']]
        base.repository_map.return_value = selected | {'Tuinstra-DEV/gate': 42}
        with self.assertRaisesRegex(ValueError, 'repository_selection_changed'):
            preflight.verify_policy(base, manager, original, group, {})
