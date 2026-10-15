"""Offline state-transition checks for the reviewed DEV-50 workflow upgrade."""

from importlib.machinery import SourceFileLoader
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/transition-org-runner-workflows.py"
loader = SourceFileLoader("transition_org_runner_workflows", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
upgrade = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = upgrade
loader.exec_module(upgrade)
base = upgrade.base
OLD = "36f32aebe315274df8825674b7bd0ced637ab056"
SECOND = "e1a9f09504b903120e9b5297eff64f58a891c907"
NEW = "23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e"


def result(args, output="", code=0):
    return subprocess.CompletedProcess(args, code, output, "")


def config():
    source = (ROOT / "runner/config/manager.toml").read_text()
    four = (source.replace("max_concurrency = 1", "max_concurrency = 4") +
            '\npool_mode = "four"\nmedium_runner_label = "trusted-medium"\n'
            'medium_runner_vcpus = 2\nmedium_runner_memory_mib = 3072\n'
            'max_heavy = 2\nmax_medium = 2\nheavy_disk_reservation_gib = 24\n'
            'medium_disk_reservation_gib = 4\nstorage_mode = "bounded-loop"\n').encode()
    return base.candidate_config(four, OLD, 42)[1]


def two_ref_config():
    return config().replace(
        ("org_workflow_ref = " + json.dumps(upgrade.REF_PREFIX + OLD)).encode(),
        ("org_workflow_refs = " + json.dumps([
            upgrade.REF_PREFIX + OLD, upgrade.REF_PREFIX + SECOND])).encode())


def three_ref_config():
    return upgrade.candidate_config(two_ref_config(), NEW, 42)[0]


class FakeGroup:
    def __init__(self, before):
        self.current = tuple(before)
        self.calls = []
        self.fail_patch = False

    def verify(self, expected):
        self.calls.append(("verify", tuple(expected)))
        if self.current != tuple(expected):
            raise upgrade.Error("group mismatch")

    def patch(self, before, after):
        self.calls.append(("patch", tuple(before), tuple(after)))
        self.verify(before)
        if self.fail_patch:
            self.fail_patch = False
            raise upgrade.Error("patch failed")
        self.current = tuple(after)
        self.verify(after)


class TransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.stage = root / "stage"
        self.stage.mkdir()
        (self.stage / "ci_runner_manager.py").write_bytes(b"new manager")
        (self.stage / "activate-org-runner-routing.py").write_bytes(b"activation")
        self.manager = root / "manager"
        self.manager.write_bytes(b"old manager")
        self.manager.chmod(0o755)
        self.config = root / "manager.toml"
        self.config.write_bytes(config())
        self.config.chmod(0o640)
        self.helper = root / "helper"
        self.helper.write_bytes((ROOT / "runner/host-helper/ci_runner_host_helper.py").read_bytes())
        self.helper.chmod(0o755)
        self.unit = root / "unit"
        self.unit.write_bytes((ROOT / "runner/systemd/ci-runner-manager.service").read_bytes())
        self.token = root / "org.token"
        self.token.write_bytes(b"synthetic-token")
        self.token.chmod(0o600)
        self.dropin = root / "org-dropin"
        self.dropin.write_bytes(base.DROPIN_BYTES)
        self.state = root / "state"
        self.state.mkdir()
        self.overlay = root / "overlay"
        self.overlay.mkdir()
        self.layout = upgrade.Layout(manager=self.manager, config=self.config,
            helper=self.helper, unit=self.unit, org_token=self.token,
            dropin=self.dropin, drain=root / "runtime" / "drain.conf",
            state=self.state, overlay=self.overlay, backup_root=root / "backups",
            lock=root / "activation.lock", helper_lock=root / "helper.lock")

    def test_reviewed_sources_match_repository(self):
        self.assertEqual(base.sha((ROOT / "runner/manager/ci_runner_manager.py").read_bytes()),
                         upgrade.NEW_MANAGER_SHA)
        self.assertEqual(base.sha((ROOT / "scripts/activate-org-runner-routing.py").read_bytes()),
                         upgrade.ACTIVATION_SHA)

    def test_preflight_requires_exact_installed_three_ref_manager_only_version(self):
        original = {"manager": b"old manager", "config": three_ref_config()}
        replacement = {"manager": b"new manager"}
        group = FakeGroup(upgrade.refs(tomllib.loads(original["config"].decode())))
        with mock.patch.object(upgrade, "verify_sources", return_value=(original,
                replacement, {"token": b"synthetic-token"})), \
             mock.patch.object(base, "verify_unit"), \
             mock.patch.object(upgrade, "OLD_MANAGER_SHA", base.sha(b"old manager")), \
             mock.patch.object(upgrade, "NEW_MANAGER_SHA", base.sha(b"new manager")):
            result = upgrade.preflight(self.stage, NEW, 42, self.layout,
                group_factory=lambda *_: group)
            self.assertEqual(result[1]["config"], original["config"])
            self.assertEqual(result[4:7], (group.current, group.current, False))
            for invalid in (config(), two_ref_config(),
                            three_ref_config().replace(NEW.encode(), ("b" * 40).encode())):
                with self.subTest(invalid=invalid[-60:]):
                    original["config"] = invalid
                    with self.assertRaises(upgrade.Error):
                        upgrade.preflight(self.stage, NEW, 42, self.layout,
                            group_factory=lambda *_: group)
            original["config"] = three_ref_config()
            original["manager"] = b"foreign manager"
            with self.assertRaises(upgrade.Error):
                upgrade.preflight(self.stage, NEW, 42, self.layout,
                    group_factory=lambda *_: group)

    def test_candidate_preserves_policy_and_adds_exact_third_ref(self):
        middle = two_ref_config()
        self.config.write_bytes(middle)
        final, before, after, applied = upgrade.candidate_config(middle, NEW, 42)
        self.assertFalse(applied)
        self.assertEqual(after, (*before, upgrade.REF_PREFIX + NEW))
        old = tomllib.loads(middle.decode())
        new = tomllib.loads(final.decode())
        self.assertEqual({key: value for key, value in new.items() if key != "org_workflow_refs"},
                         {key: value for key, value in old.items() if key != "org_workflow_refs"})
        self.assertEqual(upgrade.candidate_config(final, NEW, 42), (final, after, after, True))

    def test_candidate_rejects_unknown_duplicate_nonexact_and_policy_widening(self):
        original = self.config.read_bytes()
        for invalid in (
            original.replace(OLD.encode(), ("b" * 40).encode()),
            original.replace(OLD.encode(), b"refs/heads/main"),
            original.replace(b'allowed_owner = "Tuinstra-DEV"', b'allowed_owner = "Anyone"'),
            original.replace(b'runner_label = "trusted-heavy"', b'runner_label = "untrusted"'),
            original.replace(b'org_runner_group_id = 42', b'org_runner_group_id = 43'),
            original.replace(b'"Tuinstra-DEV/gate" = 1273386924',
                             b'"Tuinstra-DEV/gate" = 123'),
        ):
            with self.subTest(invalid=invalid[-60:]), self.assertRaises(upgrade.Error):
                upgrade.candidate_config(invalid, NEW, 42)
        two = two_ref_config()
        duplicate = two.replace((upgrade.REF_PREFIX + SECOND).encode(),
                                (upgrade.REF_PREFIX + OLD).encode())
        with self.assertRaises(upgrade.Error):
            upgrade.candidate_config(duplicate, NEW, 42)
        multiline = original.replace(
            ("org_workflow_ref = " + json.dumps(upgrade.REF_PREFIX + OLD)).encode(),
            ("org_workflow_refs = [\n  " + json.dumps(upgrade.REF_PREFIX + OLD) + "\n]").encode())
        self.assertEqual(upgrade.refs(tomllib.loads(multiline.decode())), (upgrade.REF_PREFIX + OLD,))
        with self.assertRaisesRegex(upgrade.Error, "single root TOML line"):
            upgrade.candidate_config(multiline, NEW, 42)
        for sha in ("A" * 40, "a" * 39, "b" * 40, OLD):
            with self.assertRaises(upgrade.Error):
                upgrade.candidate_config(original, sha, 42)

    def test_group_verify_checks_exact_selection_and_private_repository_ids(self):
        current = (upgrade.REF_PREFIX + OLD,)
        group = {"id": 42, "name": upgrade.GROUP_NAME, "visibility": "selected",
                 "default": False, "inherited": False, "allows_public_repositories": False,
                 "restricted_to_workflows": True, "selected_workflows": list(current)}
        selection = {"total_count": len(base.REPOSITORIES), "repositories": [
            {"full_name": name, "id": identifier, "private": True}
            for name, identifier in base.REPOSITORIES.items()]}
        client = upgrade.GitHubGroup(42, b"synthetic-token")
        def request(url, method, payload=None):
            self.assertEqual(method, "GET")
            return group if url == client.url else selection
        with mock.patch.object(client, "request", side_effect=request):
            client.verify(current)
            for change in (
                lambda: group.update(selected_workflows=[*current, *current]),
                lambda: group.update(selected_workflows=list(current), visibility="all"),
                lambda: group.update(visibility="selected", allows_public_repositories=True),
                lambda: selection["repositories"][0].update(private=False),
                lambda: selection["repositories"][0].update(private=True, id=99),
            ):
                change()
                with self.assertRaises(upgrade.Error):
                    client.verify(current)
                group.update(selected_workflows=list(current), visibility="selected",
                             allows_public_repositories=False)
                selection["repositories"][0].update(private=True,
                    id=base.REPOSITORIES[selection["repositories"][0]["full_name"]])

    def test_group_workflow_order_is_not_an_admission_policy_change(self):
        current = (upgrade.REF_PREFIX + OLD, upgrade.REF_PREFIX + SECOND)
        group = {"id": 42, "name": upgrade.GROUP_NAME, "visibility": "selected",
                 "default": False, "inherited": False, "allows_public_repositories": False,
                 "restricted_to_workflows": True, "selected_workflows": list(reversed(current))}
        selected = {"total_count": len(base.REPOSITORIES), "repositories": [
            {"id": value, "full_name": name, "private": True}
            for name, value in base.REPOSITORIES.items()]}
        client = upgrade.GitHubGroup(42, b"synthetic-token")
        with mock.patch.object(client, "request", side_effect=[group, selected]):
            client.verify(current)

    def test_group_patch_sends_only_selected_workflows(self):
        before = (upgrade.REF_PREFIX + OLD,)
        after = (*before, upgrade.REF_PREFIX + NEW)
        client = upgrade.GitHubGroup(42, b"synthetic-token")
        seen = []
        with mock.patch.object(client, "verify", side_effect=lambda value: seen.append(tuple(value))), \
             mock.patch.object(client, "request", return_value={}) as request:
            client.patch(before, after)
        self.assertEqual(seen, [before, after])
        request.assert_called_once_with(client.url, "PATCH", {"selected_workflows": list(after)})

    def test_source_preflight_rejects_hash_owner_and_symlink(self):
        source = (ROOT / "runner/manager/ci_runner_manager.py").read_bytes()
        activation = (ROOT / "scripts/activate-org-runner-routing.py").read_bytes()
        (self.stage / "ci_runner_manager.py").write_bytes(source)
        (self.stage / "activate-org-runner-routing.py").write_bytes(activation)
        self.manager.write_bytes(b"old manager")
        def root_file(path, **_):
            info = path.lstat()
            if path.is_symlink():
                raise upgrade.Error("unsafe file")
            gid = 123 if path == self.config else 0
            return path.read_bytes(), SimpleNamespace(st_gid=gid, st_uid=0,
                st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino)
        with mock.patch.object(base, "checked_dir"), \
             mock.patch.object(base, "read_file", side_effect=root_file), \
             mock.patch.object(upgrade, "OLD_MANAGER_SHA", base.sha(self.manager.read_bytes())), \
             mock.patch.object(base.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=123)):
            upgrade.verify_sources(self.stage, self.layout)
            (self.stage / "ci_runner_manager.py").write_bytes(b"wrong")
            with self.assertRaises(upgrade.Error):
                upgrade.verify_sources(self.stage, self.layout)
            (self.stage / "ci_runner_manager.py").write_bytes(source)
            self.manager.unlink()
            self.manager.symlink_to(self.helper)
            with self.assertRaises(upgrade.Error):
                upgrade.verify_sources(self.stage, self.layout)
            self.manager.unlink()
            self.manager.write_bytes(b"old manager")
            self.manager.chmod(0o755)
            with mock.patch.object(base.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=999)):
                with self.assertRaises(upgrade.Error):
                    upgrade.verify_sources(self.stage, self.layout)

    def _simulation(self, *, applied=False, fail=None):
        self.config.write_bytes(three_ref_config())
        original = {"manager": self.manager.read_bytes(), "config": self.config.read_bytes()}
        final, before, after, _ = upgrade.candidate_config(original["config"], NEW, 42)
        replacement = {"manager": b"new manager", "config": final}
        if applied:
            self.manager.write_bytes(replacement["manager"])
            self.config.write_bytes(final)
        group = FakeGroup(after if applied else before)
        infos = {"manager": self.manager.stat(), "config": self.config.stat()}
        calls = []
        running = {upgrade.SERVICE, upgrade.SOCKET}
        def command(args, **_):
            calls.append(tuple(args))
            if args[:2] == ["systemctl", "stop"]:
                running.discard(args[2])
            if args[:2] == ["systemctl", "start"]:
                if fail == "restart" and args[2] == upgrade.SERVICE:
                    raise upgrade.Error("restart failed")
                running.add(args[2])
            return result(args)
        def active(name, _command):
            return name in running
        def write(path, data, _info):
            if fail == "manager" and path == self.manager and data == replacement["manager"]:
                raise OSError("manager write failed")
            path.write_bytes(data)
        def restore(_layout, _folder, *, partial):
            self.manager.write_bytes(original["manager"])
            self.config.write_bytes(original["config"])
        folder = self.layout.backup_root / "dev50-20261015T120000Z-1234abcd"
        import contextlib
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(upgrade, "preflight", return_value=(
            original if not applied else replacement, replacement, infos, group,
            before, after, applied)))
        stack.enter_context(mock.patch.object(upgrade, "drain", return_value=(1, 2)))
        stack.enter_context(mock.patch.object(upgrade, "assert_idle"))
        stack.enter_context(mock.patch.object(upgrade, "save_backup", return_value=folder))
        stack.enter_context(mock.patch.object(base, "lock", side_effect=lambda path:
            os.open(path, os.O_RDWR | os.O_CREAT, 0o600)))
        stack.enter_context(mock.patch.object(base, "active", side_effect=active))
        stack.enter_context(mock.patch.object(base, "read_file", side_effect=lambda path, **_:
            (path.read_bytes(), path.stat())))
        stack.enter_context(mock.patch.object(base, "atomic_write", side_effect=write))
        stack.enter_context(mock.patch.object(base, "restore", side_effect=restore))
        stack.enter_context(mock.patch.object(base, "remove_own_file"))
        stack.enter_context(mock.patch.object(base, "verify_unit"))
        return stack, command, group, calls, original, replacement, running

    def test_success_and_already_applied_are_idempotent(self):
        stack, command, group, calls, _, replacement, _ = self._simulation()
        with stack:
            self.assertEqual(upgrade.transition(self.stage, NEW, 42, 60,
                                                self.layout, command),
                             ("applied", "dev50-20261015T120000Z-1234abcd"))
        self.assertEqual(self.manager.read_bytes(), replacement["manager"])
        self.assertEqual(self.config.read_bytes(), replacement["config"])
        self.assertEqual(group.current, upgrade.refs(tomllib.loads(replacement["config"].decode())))
        self.assertFalse(any(call[0] == "patch" for call in group.calls))
        self.assertEqual(self.config.read_bytes(), three_ref_config())
        stack, command, group, calls, _, _, _ = self._simulation(applied=True)
        with stack:
            self.assertEqual(upgrade.transition(self.stage, NEW, 42, 60,
                                                self.layout, command), ("already_applied", None))
        self.assertEqual(calls, [])

    def test_manager_write_and_restart_failures_restore_original(self):
        for point in ("manager", "restart"):
            with self.subTest(point=point):
                self.manager.write_bytes(b"old manager")
                self.config.write_bytes(three_ref_config())
                stack, command, group, calls, original, _, running = self._simulation(fail=point)
                with stack, self.assertRaises(upgrade.Error):
                    upgrade.transition(self.stage, NEW, 42, 60, self.layout, command)
                self.assertEqual(self.manager.read_bytes(), original["manager"])
                self.assertEqual(self.config.read_bytes(), original["config"])
                self.assertEqual(group.current, upgrade.refs(tomllib.loads(original["config"].decode())))
                self.assertFalse(any(call[0] == "patch" for call in group.calls))
                if point != "restart":
                    self.assertIn(upgrade.SERVICE, running)

    def test_failed_drain_reopens_original_admission(self):
        stack, command, group, calls, original, _, running = self._simulation()
        def timeout(*_):
            command(["systemctl", "stop", upgrade.SERVICE])
            raise upgrade.Error("runner drain timed out")
        with stack, mock.patch.object(upgrade, "drain", side_effect=timeout):
            with self.assertRaisesRegex(upgrade.Error, "original admission restored"):
                upgrade.transition(self.stage, NEW, 42, 60, self.layout, command)
        self.assertIn(upgrade.SERVICE, running)
        self.assertIn(upgrade.SOCKET, running)
        self.assertEqual(self.manager.read_bytes(), original["manager"])
        self.assertEqual(self.config.read_bytes(), original["config"])
        self.assertEqual(group.current, upgrade.refs(tomllib.loads(original["config"].decode())))

    def test_preflight_refusal_does_not_mutate_services(self):
        calls = []
        with mock.patch.object(base, "lock", side_effect=lambda path:
                               os.open(path, os.O_RDWR | os.O_CREAT, 0o600)), \
             mock.patch.object(upgrade, "preflight", side_effect=upgrade.Error("policy drift")):
            with self.assertRaisesRegex(upgrade.Error, "policy drift"):
                upgrade.transition(self.stage, NEW, 42, 60, self.layout,
                                   lambda args, **_: calls.append(args))
        self.assertEqual(calls, [])

    def test_drain_timeout_recovers_without_destroying_guest(self):
        (self.state / "lease-live.json").write_text(json.dumps({
            "lease": "live", "launched_at": int(upgrade.time.time())}))
        calls = []
        def command(args, **_):
            calls.append(args)
            if args[:2] == ["systemctl", "show"]:
                key = args[-2].split("=", 1)[1]
                values = {"Type": "oneshot", "Restart": "no",
                          "ExecStart": "argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml reconcile",
                          "DropInPaths": f"{self.dropin} {self.layout.drain}"}
                return result(args, values[key])
            if args == ["systemctl", "start", upgrade.SERVICE]:
                return result(args, code=1)
            return result(args)
        with mock.patch.object(base, "active", return_value=True), \
             mock.patch.object(base, "checked_dir"), \
             mock.patch.object(base, "create_file", return_value=(1, 2)), \
             mock.patch.object(base, "remove_own_file") as remove, \
             mock.patch.object(base, "credential_pairs", return_value=base.ORG_CREDENTIALS), \
             mock.patch.object(upgrade.time, "monotonic", side_effect=[0, 61]):
            with self.assertRaisesRegex(upgrade.Error, "timed out"):
                upgrade.drain(self.layout, 60, command, pause=lambda _: None)
            remove.assert_called_once()
        self.assertIn(["systemctl", "stop", upgrade.SERVICE], calls)
        self.assertFalse(any("destroy" in " ".join(call) for call in calls))

    def test_failed_reconcile_waits_for_live_guest_without_admitting_work(self):
        lease = self.state / "lease-live.json"
        lease.write_text(json.dumps({"lease": "live", "launched_at": 1000}))
        starts = []
        calls = []
        def command(args, **options):
            calls.append(tuple(args))
            if args[:2] == ["systemctl", "show"]:
                key = args[-2].split("=", 1)[1]
                values = {"Type": "oneshot", "Restart": "no",
                          "ExecStart": "argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml reconcile",
                          "DropInPaths": f"{self.dropin} {self.layout.drain}"}
                return result(args, values[key])
            if args == ["systemctl", "start", upgrade.SERVICE]:
                starts.append(options)
                if len(starts) == 1:
                    if options.get("check", True):
                        raise upgrade.Error("reconcile failed")
                    return result(args, code=1)
            return result(args)
        def finish_guest(_):
            lease.unlink()
        with mock.patch.object(base, "active", return_value=True), \
             mock.patch.object(base, "checked_dir"), \
             mock.patch.object(base, "create_file", return_value=(1, 2)), \
             mock.patch.object(base, "remove_own_file"), \
             mock.patch.object(base, "credential_pairs", return_value=base.ORG_CREDENTIALS), \
             mock.patch.object(upgrade.time, "time", return_value=1000), \
             mock.patch.object(upgrade.time, "monotonic", side_effect=[0, 1, 2]):
            self.assertEqual(upgrade.drain(self.layout, 60, command, pause=finish_guest), (1, 2))
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(options.get("check") is False for options in starts))
        self.assertFalse(any("destroy" in " ".join(call) for call in calls))

    def test_near_expiry_lease_aborts_before_reconcile_start(self):
        lease = self.state / "lease-live.json"
        lease.write_text(json.dumps({"lease": "live", "launched_at": 1000}))
        calls = []
        def command(args, **_):
            calls.append(tuple(args))
            if args[:2] == ["systemctl", "show"]:
                key = args[-2].split("=", 1)[1]
                values = {"Type": "oneshot", "Restart": "no",
                          "ExecStart": "argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml reconcile",
                          "DropInPaths": f"{self.dropin} {self.layout.drain}"}
                return result(args, values[key])
            return result(args)
        with mock.patch.object(base, "active", return_value=True), \
             mock.patch.object(base, "checked_dir"), \
             mock.patch.object(base, "create_file", return_value=(1, 2)), \
             mock.patch.object(base, "remove_own_file") as remove, \
             mock.patch.object(base, "credential_pairs", return_value=base.ORG_CREDENTIALS), \
             mock.patch.object(upgrade.time, "time", return_value=8000):
            with self.assertRaisesRegex(upgrade.Error, "near expiry"):
                upgrade.drain(self.layout, 60, command, pause=lambda _: None)
        remove.assert_called_once()
        self.assertNotIn(("systemctl", "start", upgrade.SERVICE), calls)
        lease.unlink()
        lease.symlink_to(self.config)
        with self.assertRaisesRegex(upgrade.Error, "unreadable"):
            upgrade.assert_reconcile_safe(self.layout, 1000)

    def test_idle_check_refuses_live_lease_and_active_helper(self):
        (self.state / "lease-live.json").write_text("{}")
        with self.assertRaisesRegex(upgrade.Error, "lease"):
            upgrade.assert_idle(self.layout, lambda args, **_: result(args))
        (self.state / "lease-live.json").unlink()
        def command(args, **_):
            output = "ci-runner-host-helper@live.service loaded active running\n" if \
                "list-units" in args else ""
            return result(args, output)
        with self.assertRaisesRegex(upgrade.Error, "host helper"):
            upgrade.assert_idle(self.layout, command)
        self.overlay.rmdir()
        self.overlay.symlink_to(self.state)
        with self.assertRaisesRegex(upgrade.Error, "unsafe"):
            upgrade.assert_idle(self.layout, lambda args, **_: result(args))

    def test_rollback_drain_timeout_restores_installed_admission(self):
        final = three_ref_config()
        self.config.write_bytes(final)
        self.manager.write_bytes(b"new manager")
        group = FakeGroup(upgrade.refs(tomllib.loads(final.decode())))
        running = {upgrade.SOCKET}
        calls = []
        def command(args, **_):
            calls.append(tuple(args))
            if args[:2] == ["systemctl", "start"]:
                running.add(args[2])
            return result(args)
        with mock.patch.object(base, "lock", side_effect=lambda path:
                               os.open(path, os.O_RDWR | os.O_CREAT, 0o600)), \
             mock.patch.object(upgrade, "read_backup", return_value=(
                 self.layout.backup_root / "dev50-20261015T120000Z-1234abcd",
                 {"id": 42, "before": list(group.current),
                  "after": list(group.current)}, {})), \
             mock.patch.object(base, "read_file", side_effect=lambda path, **_:
                               (path.read_bytes(), SimpleNamespace(st_gid=0,
                                st_mode=path.stat().st_mode))), \
             mock.patch.object(upgrade, "NEW_MANAGER_SHA", base.sha(b"new manager")), \
             mock.patch.object(base, "verify_unit"), \
             mock.patch.object(base, "active", side_effect=lambda name, _cmd: name in running), \
             mock.patch.object(upgrade, "drain", side_effect=upgrade.Error("drain timed out")):
            with self.assertRaisesRegex(upgrade.Error, "installed admission unchanged"):
                upgrade.rollback("dev50-20261015T120000Z-1234abcd", 60,
                                 self.layout, command, lambda *_: group)
        self.assertIn(upgrade.SERVICE, running)
        self.assertEqual(group.current, upgrade.refs(tomllib.loads(final.decode())))
        self.assertEqual(self.config.read_bytes(), final)

    def test_strict_rollback_rejects_changed_installation(self):
        original = {"manager": b"old manager", "config": three_ref_config()}
        final = upgrade.candidate_config(original["config"], NEW, 42)[0]
        replacement = {"manager": b"new manager", "config": final}
        self.manager.write_bytes(replacement["manager"])
        self.config.write_bytes(final)
        infos = {"manager": self.manager.stat(), "config": self.config.stat()}
        with mock.patch.object(base, "checked_dir"), \
             mock.patch.object(base, "read_file", side_effect=lambda path, **_:
                               (path.read_bytes(), path.stat())), \
             mock.patch.object(base, "atomic_write", side_effect=lambda path, data, _:
                               path.write_bytes(data)):
            current = upgrade.refs(tomllib.loads(final.decode()))
            folder = upgrade.save_backup(self.layout, original, replacement, infos, 42,
                current, current)
            self.manager.write_bytes(b"foreign manager")
            with self.assertRaisesRegex(upgrade.Error, "changed"):
                upgrade.read_backup(self.layout, folder.name)
            self.assertEqual(self.manager.read_bytes(), b"foreign manager")

    def test_manager_only_backup_and_rollback_preserve_group_and_config(self):
        original = {"manager": b"old manager", "config": three_ref_config()}
        replacement = {"manager": b"new manager", "config": original["config"]}
        self.manager.write_bytes(replacement["manager"])
        self.config.write_bytes(replacement["config"])
        current = upgrade.refs(tomllib.loads(original["config"].decode()))
        group = FakeGroup(current)
        def read_file(path, **_):
            info = path.stat()
            return path.read_bytes(), SimpleNamespace(st_uid=0, st_gid=0,
                st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino)
        infos = {key: read_file(path)[1] for key, path in
                 (("manager", self.manager), ("config", self.config))}
        running = {upgrade.SOCKET, upgrade.SERVICE}
        def command(args, **_):
            if args[:2] == ["systemctl", "stop"]:
                running.discard(args[2])
            if args[:2] == ["systemctl", "start"]:
                running.add(args[2])
            return result(args)
        with mock.patch.object(base, "checked_dir"), \
             mock.patch.object(base, "read_file", side_effect=read_file), \
             mock.patch.object(base, "atomic_write", side_effect=lambda path, data, _:
                               path.write_bytes(data)), \
             mock.patch.object(base, "lock", side_effect=lambda path:
                               os.open(path, os.O_RDWR | os.O_CREAT, 0o600)), \
             mock.patch.object(base, "active", side_effect=lambda name, _cmd: name in running), \
             mock.patch.object(base, "verify_unit"), \
             mock.patch.object(base, "remove_own_file"), \
             mock.patch.object(upgrade, "drain", return_value=(1, 2)), \
             mock.patch.object(upgrade, "assert_idle"), \
             mock.patch.object(upgrade, "OLD_MANAGER_SHA", base.sha(original["manager"])), \
             mock.patch.object(upgrade, "NEW_MANAGER_SHA", base.sha(replacement["manager"])):
            folder = upgrade.save_backup(self.layout, original, replacement, infos,
                                         42, current, current)
            self.assertEqual(upgrade.read_backup(self.layout, folder.name)[1],
                             {"id": 42, "before": list(current), "after": list(current)})
            upgrade.rollback(folder.name, 60, self.layout, command, lambda *_: group)
        self.assertEqual(self.manager.read_bytes(), original["manager"])
        self.assertEqual(self.config.read_bytes(), original["config"])
        self.assertFalse(any(call[0] == "patch" for call in group.calls))
        self.assertEqual(running, {upgrade.SOCKET, upgrade.SERVICE})


if __name__ == "__main__":
    unittest.main()
