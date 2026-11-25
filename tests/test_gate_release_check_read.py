"""Exact reversible release-only delta; no live settings or credentials."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'docs/runner/proposals/dev54-release-check-read/plan.py'
spec = importlib.util.spec_from_file_location('release_read_plan', PATH)
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)


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
