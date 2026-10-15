"""Offline checks for the narrow DEV-50 organization-routing operator upgrade."""

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
SCRIPT = ROOT / "scripts/activate-org-runner-routing.py"
loader = SourceFileLoader("activate_org_runner_routing", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
activation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = activation
loader.exec_module(activation)


def result(args, output="", code=0):
    return subprocess.CompletedProcess(args, code, output, "")


def four_pool_config():
    legacy = (ROOT / "runner/config/manager.toml").read_text()
    return (legacy.replace("max_concurrency = 1", "max_concurrency = 4") +
            '\npool_mode = "four"\nmedium_runner_label = "trusted-medium"\n'
            'medium_runner_vcpus = 2\nmedium_runner_memory_mib = 3072\n'
            'max_heavy = 2\nmax_medium = 2\nheavy_disk_reservation_gib = 24\n'
            'medium_disk_reservation_gib = 4\nstorage_mode = "bounded-loop"\n').encode()


class OrgActivationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.stage = root / "stage"
        self.stage.mkdir()
        self.stage_manager = self.stage / "ci_runner_manager.py"
        self.stage_manager.write_bytes((ROOT / "runner/manager/ci_runner_manager.py").read_bytes())
        self.manager = root / "manager"
        self.manager.write_bytes(b"old manager")
        self.manager.chmod(0o755)
        self.config = root / "manager.toml"
        self.config.write_bytes(four_pool_config())
        self.config.chmod(0o640)
        self.helper = root / "helper"
        self.helper.write_bytes((ROOT / "runner/host-helper/ci_runner_host_helper.py").read_bytes())
        self.helper.chmod(0o755)
        self.unit = root / "unit"
        self.unit.write_bytes((ROOT / "runner/systemd/ci-runner-manager.service").read_bytes())
        self.token = root / "org.token"
        self.token.write_bytes(b"synthetic-token")
        self.token.chmod(0o600)
        self.overlay = root / "overlay"
        self.overlay.mkdir()
        self.state = root / "state"
        self.state.mkdir()
        self.layout = activation.Layout(
            manager=self.manager, config=self.config, helper=self.helper,
            unit=self.unit, org_token=self.token,
            dropin=root / "persistent" / "20-org-credential.conf",
            drain=root / "runtime" / "dev50-org-drain.conf",
            state=self.state, overlay=self.overlay, backup_root=root / "backup",
            lock=root / "activate.lock", helper_lock=root / "helper.lock",
        )
        self.workflow_sha = "a" * 40

    def test_pinned_source_and_manifest_match_checkout(self):
        self.assertEqual(activation.sha(self.stage_manager.read_bytes()), activation.NEW_MANAGER_SHA)
        self.assertEqual(activation.sha(self.helper.read_bytes()), activation.HELPER_SHA)
        self.assertEqual(activation.sha(self.unit.read_bytes()), activation.UNIT_SHA)
        manifest = json.loads((ROOT / "runner/policy/org-routing-repositories.json").read_text())
        self.assertEqual(activation.REPOSITORIES, manifest)
        self.assertEqual(len(manifest), 12)

    def test_config_preserves_all_existing_policy_and_appends_only_org_keys(self):
        old = tomllib.loads(self.config.read_text())
        candidate, data = activation.candidate_config(self.config.read_bytes(), self.workflow_sha, 42)
        self.assertEqual(candidate, tomllib.loads(data.decode()))
        self.assertEqual({key: candidate[key] for key in old}, old)
        self.assertEqual(candidate["org_repository_ids"], activation.REPOSITORIES)
        self.assertEqual(candidate["org_workflow_ref"], activation.ORG_REF_PREFIX + self.workflow_sha)
        self.assertIn(b'heavy_disk_reservation_gib = 24', data)

    def test_config_rejects_policy_drift_or_unreviewed_inputs(self):
        data = self.config.read_bytes()
        examples = (data.replace(b'heavy_disk_reservation_gib = 24',
                                 b'heavy_disk_reservation_gib = 12'),
                    data.replace(b'github_token_file = "/run/credentials/ci-runner-manager.service/github_token"',
                                 b'github_token_file = "/tmp/token"'),
                    data + b'org_routing_enabled = false\n',
                    data + b'[unknown]\nvalue = 1\n')
        for invalid in examples:
            with self.subTest(invalid=invalid[-50:]), \
                    self.assertRaises(activation.ActivationError):
                activation.candidate_config(invalid, self.workflow_sha, 42)
        for sha, group in (("A" * 40, 42), ("a" * 39, 42), ("a" * 40, 1)):
            with self.assertRaises(activation.ActivationError):
                activation.candidate_config(data, sha, group)

    def test_group_preflight_rejects_wrong_visibility_ref_and_repositories(self):
        cfg, _ = activation.candidate_config(self.config.read_bytes(), self.workflow_sha, 42)
        group = {"id": 42, "name": "sanctuary-trusted-verification",
                 "visibility": "selected", "default": False, "inherited": False,
                 "allows_public_repositories": False, "restricted_to_workflows": True,
                 "selected_workflows": [cfg["org_workflow_ref"]]}
        selection = {"total_count": 12, "repositories": [
            {"full_name": name, "id": identifier, "private": True}
            for name, identifier in activation.REPOSITORIES.items()]}

        class Response:
            status = 200
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return json.dumps(self.data).encode()

        class Opener:
            def open(self, request, timeout):
                self_test.assertEqual(timeout, 20)
                self_test.assertEqual(request.get_method(), "GET")
                return Response(group if request.full_url.endswith("/42") else selection)

        self_test = self
        with mock.patch.object(activation.urllib.request, "build_opener", return_value=Opener()):
            activation.verify_group(cfg, b"synthetic-token")
            for mutate in (
                lambda: group.update(visibility="all"),
                lambda: group.update(visibility="selected", selected_workflows=["wrong"]),
                lambda: group.update(selected_workflows=[cfg["org_workflow_ref"]],
                                     allows_public_repositories=True),
                lambda: selection["repositories"][0].update(private=False),
            ):
                mutate()
                with self.assertRaisesRegex(activation.ActivationError, "group preflight"):
                    activation.verify_group(cfg, b"synthetic-token")
                group.update(visibility="selected", allows_public_repositories=False,
                             selected_workflows=[cfg["org_workflow_ref"]])
                selection["repositories"][0]["private"] = True

    def test_preflight_fails_before_drain_or_service_mutation(self):
        calls = []
        def probe(_cfg, _token):
            calls.append("group_get")
            raise activation.ActivationError("group mismatch")
        def root_file(path, **_):
            info = path.stat()
            return path.read_bytes(), SimpleNamespace(st_gid=os.getgid() if path == self.config else 0,
                                                      st_uid=0, st_mode=info.st_mode,
                                                      st_dev=info.st_dev, st_ino=info.st_ino)
        with mock.patch.object(activation, "checked_dir"), \
             mock.patch.object(activation, "read_file", side_effect=root_file), \
             mock.patch.object(activation, "lock", side_effect=lambda path:
                               os.open(path, os.O_RDWR | os.O_CREAT, 0o600)), \
             mock.patch.object(activation, "OLD_MANAGER_SHA", activation.sha(self.manager.read_bytes())), \
             mock.patch.object(activation.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=os.getgid())), \
             mock.patch.object(activation, "verify_unit", side_effect=lambda *_a, **_k:
                               calls.append("unit_read")), \
             mock.patch.object(activation, "drain") as drain:
            with self.assertRaisesRegex(activation.ActivationError, "group mismatch"):
                activation.activate(self.stage, self.workflow_sha, 42, 60, self.layout,
                                    command=lambda *_a, **_k: result([]), group_probe=probe)
            drain.assert_not_called()
        self.assertEqual(calls, ["unit_read", "group_get"])

    def test_drain_refuses_guest_and_cleanup_obligation(self):
        with self.assertRaisesRegex(activation.ActivationError, "VM remains"):
            activation.assert_drained(self.layout,
                lambda args, **_: result(args, "sanctuary-ci-gh-123\n"))
        (self.state / "cleanup-pending.json").write_text("{}")
        with self.assertRaisesRegex(activation.ActivationError, "cleanup obligation"):
            activation.assert_drained(self.layout, lambda args, **_: result(args))

    def test_partial_install_restores_old_bytes_only_when_known_and_drained(self):
        original = {"manager": self.manager.read_bytes(), "config": self.config.read_bytes()}
        replacement = {"manager": b"new manager", "config": b"new config"}
        infos = {"manager": SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100755),
                 "config": SimpleNamespace(st_uid=0, st_gid=1000, st_mode=0o100640)}
        def root_file(path, **_):
            value = path.read_bytes()
            mode = 0o100640 if path == self.config else 0o100755 if path == self.manager else 0o100600
            gid = 1000 if path == self.config else 0
            return value, SimpleNamespace(st_uid=0, st_gid=gid, st_mode=mode,
                                          st_dev=path.stat().st_dev, st_ino=path.stat().st_ino)
        with mock.patch.object(activation, "checked_dir"), \
             mock.patch.object(activation, "read_file", side_effect=root_file), \
             mock.patch.object(activation, "atomic_write", side_effect=lambda path, data, _:
                               path.write_bytes(data)):
            folder = activation.backup(self.layout, original, replacement, infos)
            self.manager.write_bytes(replacement["manager"])
            # Verify both source and config are checked before any restore.
            self.config.write_bytes(b"foreign config")
            with self.assertRaisesRegex(activation.ActivationError, "changed"):
                activation.restore(self.layout, folder, partial=True)
            self.assertEqual(self.manager.read_bytes(), replacement["manager"])
            self.config.write_bytes(original["config"])
            activation.restore(self.layout, folder, partial=True)
            self.assertEqual(self.manager.read_bytes(), original["manager"])
            self.assertEqual(self.config.read_bytes(), original["config"])

    def test_drain_failure_restarts_original_manager_without_killing_live_vm(self):
        calls = []
        def command(args, **_):
            calls.append(args)
            return result(args)
        original = {"manager": self.manager.read_bytes(), "config": self.config.read_bytes()}
        with mock.patch.object(activation, "lock", side_effect=lambda path:
                               os.open(path, os.O_RDWR | os.O_CREAT, 0o600)), \
             mock.patch.object(activation, "preflight", return_value=(original, {}, {})), \
             mock.patch.object(activation, "drain", side_effect=activation.ActivationError("drain timed out")), \
             mock.patch.object(activation, "read_file", side_effect=lambda path, **_:
                               (path.read_bytes(), path.stat())), \
             mock.patch.object(activation, "active", side_effect=lambda name, _command:
                               name == activation.SERVICE and
                               ["systemctl", "start", activation.SERVICE] in calls), \
             mock.patch.object(activation, "no_helpers") as helpers, \
             mock.patch.object(activation, "assert_drained") as drained, \
             mock.patch.object(activation, "verify_unit"):
            with self.assertRaisesRegex(activation.ActivationError, "original manager restored"):
                activation.activate(self.stage, self.workflow_sha, 42, 60, self.layout, command)
            helpers.assert_not_called()
            drained.assert_not_called()
        self.assertIn(["systemctl", "start", activation.SERVICE], calls)
        self.assertFalse(any("sanctuary-ci-" in " ".join(call) for call in calls))

    def test_partial_atomic_install_failure_restores_both_files_before_restart(self):
        calls = []
        original = {"manager": self.manager.read_bytes(), "config": self.config.read_bytes()}
        replacement = {"manager": b"new manager", "config": b"new config"}
        folder = self.layout.backup_root / "dev50-20261008T120000Z-1234abcd"
        def command(args, **_):
            calls.append(args)
            return result(args)
        def write(path, data, _info):
            calls.append(["atomic", path.name])
            if path == self.config:
                raise OSError("simulated second-file write failure")
            path.write_bytes(data)
        with mock.patch.object(activation, "lock", side_effect=lambda path:
                               os.open(path, os.O_RDWR | os.O_CREAT, 0o600)), \
             mock.patch.object(activation, "preflight", return_value=(original, replacement,
                               {"manager": self.manager.stat(), "config": self.config.stat()})), \
             mock.patch.object(activation, "drain", return_value=(1, 2)), \
             mock.patch.object(activation, "active", side_effect=lambda name, _command:
                               name == activation.SERVICE and
                               ["systemctl", "start", activation.SERVICE] in calls), \
             mock.patch.object(activation, "no_helpers"), \
             mock.patch.object(activation, "assert_drained"), \
             mock.patch.object(activation, "backup", return_value=folder), \
             mock.patch.object(activation, "atomic_write", side_effect=write), \
             mock.patch.object(activation, "read_file", side_effect=lambda path, **_:
                               (path.read_bytes(), path.stat())), \
             mock.patch.object(activation, "restore") as restore, \
             mock.patch.object(activation, "remove_own_file") as remove, \
             mock.patch.object(activation, "verify_unit"):
            with self.assertRaisesRegex(activation.ActivationError, "original manager restored"):
                activation.activate(self.stage, self.workflow_sha, 42, 60, self.layout, command)
            restore.assert_called_once_with(self.layout, folder, partial=True)
            remove.assert_called_once_with(self.layout.drain, (1, 2), activation.DRAIN_BYTES)
        self.assertLess(calls.index(["atomic", "manager"]),
                        calls.index(["atomic", "manager.toml"]))
        self.assertIn(["systemctl", "start", activation.SERVICE], calls)

    def test_backup_records_dropin_identity_for_explicit_rollback(self):
        folder = self.layout.backup_root / "dev50-20261008T120000Z-1234abcd"
        folder.mkdir(parents=True)
        manifest = folder / "manifest.json"
        manifest.write_text('{"manager":{},"config":{}}')
        with mock.patch.object(activation, "checked_dir"), \
             mock.patch.object(activation, "read_file", side_effect=lambda path, **_:
                               (path.read_bytes(), path.stat())), \
             mock.patch.object(activation, "atomic_write", side_effect=lambda path, data, _:
                               path.write_bytes(data)):
            activation.record_dropin(folder, (7, 101))
            self.assertEqual(activation.recorded_dropin(folder), (7, 101))
            with self.assertRaisesRegex(activation.ActivationError, "already recorded"):
                activation.record_dropin(folder, (7, 102))
            data = json.loads(manifest.read_text())
            data["dropin"]["sha256"] = "0" * 64
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(activation.ActivationError, "identity is invalid"):
                activation.recorded_dropin(folder)

    def test_effective_credential_pair_requires_exact_org_binding(self):
        def command(pairs):
            def response(args, **_):
                if args[0] == "busctl":
                    return result(args, json.dumps({"type": "a(ss)", "data": pairs}))
                if "--property=ExecStart" in args:
                    return result(args, "{ argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30 ; }")
                if "--property=DropInPaths" in args:
                    return result(args, str(self.layout.dropin) + "\n")
                values = {"--property=FragmentPath": str(self.unit),
                          "--property=Type": "simple", "--property=Restart": "always",
                          "--property=User": "ci-runner-manager",
                          "--property=Group": "ci-runner-manager"}
                return result(args, values.get(args[-2], ""))
            return response
        with mock.patch.object(activation, "read_file", side_effect=lambda path, **_:
                               (path.read_bytes(), path.stat())), \
             mock.patch.object(activation, "UNIT_SHA", activation.sha(self.unit.read_bytes())):
            pairs = [["github_token", "/etc/ci-runner/github.token"],
                     ["org_github_token", activation.ORG_TOKEN_SOURCE]]
            activation.verify_unit(self.layout, command(pairs), org=True)
            with self.assertRaisesRegex(activation.ActivationError, "credential binding"):
                activation.verify_unit(self.layout, command(pairs[:1]), org=True)


if __name__ == "__main__":
    unittest.main()
