#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backup"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


restore = load("production_restore", ROOT / "backup" / "production_restore.py")
target = load("production_restore_target", ROOT / "backup" / "production_restore_target.py")


class ProductionRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config = {
            "schema_version": 1,
            "journal_root": str(self.root / "journals"),
            "stage_root": str(self.root / "stages"),
            "maximum_stage_bytes": 1024 * 1024,
            "host_lock_root": str(self.root / "host-locks"),
            "hosts": [{
                "host_slug": "tuinstra-prod-01",
                "applications": ["umami"],
                "ssh_host": "prod.example",
                "ssh_user": "tuinstra-restore",
                "identity_file": "/fixed/restore-key",
                "known_hosts_file": "/fixed/known-hosts",
            }],
        }
        (self.root / "host-locks").mkdir()
        host_lock = self.root / "host-locks/operations.host.tuinstra-prod-01.lock"
        host_lock.touch()
        host_lock.chmod(0o660)
        restore.ROOT_UID = os.getuid()

    def tearDown(self):
        self.temporary.cleanup()

    def test_request_requires_full_snapshot_and_exact_allowlisted_target(self):
        with self.assertRaisesRegex(restore.RestoreError, "snapshot"):
            restore.RestoreRequest.create(self.config, "tuinstra-prod-01", "umami", "deadbeef", "op-01", "a" * 64)
        with self.assertRaisesRegex(restore.RestoreError, "allowlisted"):
            restore.RestoreRequest.create(self.config, "tuinstra-prod-02", "umami", "a" * 64, "op-01", "b" * 64)
        request = restore.RestoreRequest.create(
            self.config, "tuinstra-prod-01", "umami", "a" * 64, "op-01", "b" * 64,
        )
        self.assertEqual(request.environment, "production")

    def test_journal_is_immutable_for_operation_binding_and_never_contains_secrets(self):
        store = restore.JournalStore(Path(self.config["journal_root"]))
        request = restore.RestoreRequest.create(
            self.config, "tuinstra-prod-01", "umami", "a" * 64, "op-01", "b" * 64,
        )
        journal = store.create(request, artifact_id="12345678-1234-4123-8123-123456789abc",
                               manifest_sha256="c" * 64, stage_sha256="d" * 64,
                               restore_evidence_id="evidence-01")
        self.assertEqual(journal["state"], "preflight-passed")
        with self.assertRaisesRegex(restore.RestoreError, "binding"):
            store.create(restore.RestoreRequest.create(
                self.config, "tuinstra-prod-01", "umami", "f" * 64, "op-01", "b" * 64,
            ), artifact_id=journal["artifact_id"], manifest_sha256="c" * 64,
                stage_sha256="d" * 64, restore_evidence_id="evidence-01")
        serialized = (Path(self.config["journal_root"]) / "op-01.json").read_text()
        self.assertNotIn("password", serialized.lower())
        self.assertNotIn("secret", serialized.lower())

    def test_apply_requires_durable_pinned_safety_snapshot_before_target_overwrite(self):
        request = restore.RestoreRequest.create(
            self.config, "tuinstra-prod-01", "umami", "a" * 64, "op-01", "b" * 64,
        )
        store = restore.JournalStore(Path(self.config["journal_root"]))
        store.create(request, artifact_id="12345678-1234-4123-8123-123456789abc",
                     manifest_sha256="c" * 64, stage_sha256="d" * 64,
                     restore_evidence_id="evidence-01")
        events = []
        transport = mock.Mock()
        transport.rpc.side_effect = [
            {"status": "prepared", "target_state": "populated",
             "safety_artifact_id": "22345678-1234-4123-8123-123456789abc"},
            {"status": "succeeded", "health": "passed", "data": "passed"},
        ]

        def safety(_request, artifact, _lock_descriptor):
            events.append(("safety", artifact))
            return {"snapshot_id": "e" * 64, "tags": [restore.SAFETY_TAG, "operation:op-01"],
                    "integrity_coverage": "full-repository-data"}

        result = restore.apply(self.config, request, store, transport, safety)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["safety_snapshot_id"], "e" * 64)
        self.assertEqual(events[0][0], "safety")
        self.assertEqual(transport.rpc.call_args_list[1].args[1], "apply")

    def test_failed_safety_copy_never_calls_target_apply_and_keeps_maintenance(self):
        request = restore.RestoreRequest.create(
            self.config, "tuinstra-prod-01", "umami", "a" * 64, "op-01", "b" * 64,
        )
        store = restore.JournalStore(Path(self.config["journal_root"]))
        store.create(request, artifact_id="12345678-1234-4123-8123-123456789abc",
                     manifest_sha256="c" * 64, stage_sha256="d" * 64,
                     restore_evidence_id="evidence-01")
        transport = mock.Mock()
        transport.rpc.return_value = {
            "status": "prepared", "target_state": "populated",
            "safety_artifact_id": "22345678-1234-4123-8123-123456789abc",
        }
        with self.assertRaisesRegex(RuntimeError, "offline"):
            restore.apply(self.config, request, store, transport,
                          lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
        journal = store.load("op-01")
        self.assertEqual(journal["state"], "failed")
        self.assertTrue(journal["maintenance_active"])
        self.assertEqual(transport.rpc.call_count, 1)

    def test_interrupted_apply_is_uncertain_and_cannot_be_replayed(self):
        request = restore.RestoreRequest.create(
            self.config, "tuinstra-prod-01", "umami", "a" * 64, "op-01", "b" * 64,
        )
        store = restore.JournalStore(Path(self.config["journal_root"]))
        store.create(request, artifact_id="12345678-1234-4123-8123-123456789abc",
                     manifest_sha256="c" * 64, stage_sha256="d" * 64,
                     restore_evidence_id="evidence-01")
        transport = mock.Mock()
        transport.rpc.side_effect = [
            {"status": "prepared", "target_state": "populated",
             "safety_artifact_id": "22345678-1234-4123-8123-123456789abc"},
            restore.TransportUncertain("connection lost"),
        ]
        safety = lambda *_: {"snapshot_id": "e" * 64, "tags": [restore.SAFETY_TAG, "operation:op-01"],
                             "integrity_coverage": "full-repository-data"}
        with self.assertRaises(restore.TransportUncertain):
            restore.apply(self.config, request, store, transport, safety)
        self.assertEqual(store.load("op-01")["state"], "uncertain")
        with self.assertRaisesRegex(restore.RestoreError, "reconcile"):
            restore.apply(self.config, request, store, transport, safety)

    def test_rollback_uses_only_recorded_safety_snapshot(self):
        request = restore.RestoreRequest.create(
            self.config, "tuinstra-prod-01", "umami", "a" * 64, "op-01", "b" * 64,
        )
        store = restore.JournalStore(Path(self.config["journal_root"]))
        store.create(request, artifact_id="12345678-1234-4123-8123-123456789abc",
                     manifest_sha256="c" * 64, stage_sha256="d" * 64,
                     restore_evidence_id="evidence-01")
        store.update("op-01", state="failed", maintenance_active=True, safety_snapshot_id="e" * 64)
        transport = mock.Mock()
        transport.rpc.return_value = {"status": "rolled-back", "health": "passed", "data": "passed"}
        result = restore.rollback(self.config, request, "f" * 64, store, transport,
                                  lambda snapshot, purpose, lock: self.assertEqual(snapshot, "e" * 64))
        self.assertEqual(result["state"], "rolled-back")
        self.assertEqual(transport.rpc.call_args.args[2]["safety_snapshot_id"], "e" * 64)

    def test_core_cli_inherits_the_authoritative_host_lock_descriptor(self):
        self.config.update({"executable": "/fixed/tuinstra-backup", "installed_config": "/fixed/worker.json"})
        completed = mock.Mock(stdout=b'{"status":"materialized"}')
        with mock.patch.object(restore, "run", return_value=completed) as command:
            result = restore.core_json(
                self.config,
                ["materialize", "--host", "tuinstra-prod-01", "--app", "umami"],
                17,
            )
        self.assertEqual(result["status"], "materialized")
        self.assertEqual(command.call_args.kwargs["pass_fds"], (17,))
        self.assertEqual(command.call_args.args[0][-2:], ["--inherited-host-lock-fd", "17"])

    def test_restore_evidence_must_include_complete_isolation_and_cleanup_proof(self):
        artifact = "12345678-1234-4123-8123-123456789abc"
        snapshot = "a" * 64
        request = restore.RestoreRequest.create(
            self.config, "tuinstra-prod-01", "umami", snapshot, "op-01", "b" * 64,
        )
        evidence_root = self.root / "evidence"
        evidence_file = evidence_root / "tuinstra-prod-01/umami" / f"{artifact}.json"
        evidence_file.parent.mkdir(parents=True)
        evidence = {
            "snapshot_id": snapshot,
            "artifact_id": artifact,
            "host_slug": "tuinstra-prod-01",
            "app_id": "umami",
            "restore_status": "passed",
            "engine_version": "tuinstra-backup-v1",
            "duration_seconds": 60,
            "manifest_sha256": "c" * 64,
            "preflight": {key: "passed" for key in ("key", "payload_checksum", "compatibility", "capacity")},
            "validation": {key: "passed" for key in (
                "schema", "data", "application_health", "database_content_marker",
                "encrypted_two_factor_authentication",
            )},
            "isolation": {"external_effects_blocked": True, "host_ports": 0},
            "cleanup": {"status": "passed", "containers_removed": True, "workspace_removed": True},
        }
        evidence_file.write_text(json.dumps(evidence))
        self.config["evidence_root"] = str(evidence_root)
        point = {"artifact_id": artifact}
        restore._validate_restore_evidence(self.config, request, point)

        evidence["cleanup"]["workspace_removed"] = False
        evidence_file.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(restore.RestoreError, "exact recovery point"):
            restore._validate_restore_evidence(self.config, request, point)

        evidence["cleanup"]["workspace_removed"] = True
        evidence["artifact_id"] = "22345678-1234-4123-8123-123456789abc"
        evidence_file.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(restore.RestoreError, "exact recovery point"):
            restore._validate_restore_evidence(self.config, request, point)

        evidence["artifact_id"] = artifact
        for proof in ("database_content_marker", "encrypted_two_factor_authentication"):
            for value in (None, "failed"):
                with self.subTest(proof=proof, value=value):
                    if value is None:
                        evidence["validation"].pop(proof)
                    else:
                        evidence["validation"][proof] = value
                    evidence_file.write_text(json.dumps(evidence))
                    with self.assertRaisesRegex(restore.RestoreError, "exact recovery point"):
                        restore._validate_restore_evidence(self.config, request, point)
                    evidence["validation"][proof] = "passed"

    def test_preflight_rejects_materialized_manifest_not_used_by_restore_drill(self):
        request = restore.RestoreRequest.create(
            self.config, "tuinstra-prod-01", "umami", "a" * 64, "op-01", "b" * 64,
        )
        store = restore.JournalStore(self.root / "journals")
        bundle = self.root / "target.tar"
        bundle.write_bytes(b"exact-bundle")
        transport = mock.Mock()
        with (mock.patch.object(restore, "_catalog_point", return_value={
                  "artifact_id": "12345678-1234-4123-8123-123456789abc",
              }),
              mock.patch.object(restore, "_validate_restore_evidence", return_value=({
                  "manifest_sha256": "c" * 64,
              }, "restore-12345678-1234-4123-8123-123456789abc")),
              mock.patch.object(restore, "materialize_bundle", return_value=(bundle, {
                  "result": {"manifest_sha256": "d" * 64},
                  "work_root": str(self.root / "materialized-work"),
              }))):
            with self.assertRaisesRegex(restore.RestoreError, "isolated restore evidence"):
                restore.preflight(self.config, request, store, transport)
        transport.stage.assert_not_called()

    def test_restore_transport_rejects_shared_or_untrusted_identity_material(self):
        identity = self.root / "restore-key"
        known_hosts = self.root / "known-hosts"
        identity.write_text("not-a-real-key")
        known_hosts.write_text("prod.example ssh-ed25519 placeholder")
        identity.chmod(0o600)
        known_hosts.chmod(0o644)
        self.config["hosts"][0].update({
            "restore_ssh_user": "tuinstra-restore",
            "restore_identity_file": str(identity),
            "restore_credential_name": "prod01-restore.key",
            "known_hosts_file": str(known_hosts),
        })
        request = restore.RestoreRequest.create(
            self.config, "tuinstra-prod-01", "umami", "a" * 64, "op-01", "b" * 64,
        )
        transport = restore.RemoteTransport(self.config, request)
        self.assertIn(str(identity), transport._argv("status op-01 " + "b" * 64))
        identity.chmod(0o640)
        with self.assertRaisesRegex(restore.RestoreError, "credential"):
            transport._argv("status op-01 " + "b" * 64)


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config = target.TargetConfig.for_test(self.root)
        target.ROOT_UID = os.getuid()

    def tearDown(self):
        self.temporary.cleanup()

    def bundle(self) -> bytes:
        payload_root = self.root / "payload-source"
        (payload_root / "files").mkdir(parents=True)
        for name, value in {
            "compose": "services: {}\n", "postgres-env": "POSTGRES_DB=umami\nPOSTGRES_USER=umami\nPOSTGRES_PASSWORD=test\n",
            "umami-env": "APP_SECRET=test\n", "database-password": "db\n", "app-secret": "app\n",
            "two-factor-encryption-key": "two\n", "admin-password": "admin\n",
        }.items():
            (payload_root / "files" / name).write_text(value)
        (payload_root / "database.dump").write_bytes(b"PGDMP-test")
        inputs = []
        for file in sorted(path for path in payload_root.rglob("*") if path.is_file()):
            relative = file.relative_to(payload_root).as_posix()
            inputs.append({"name": relative, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                           "bytes": file.stat().st_size})
        manifest = {"schema_version": 1, "artifact_id": "12345678-1234-4123-8123-123456789abc",
                    "host_slug": "tuinstra-prod-01", "app_id": "umami", "adapter": "postgres-compose-v1",
                    "created_at": "2026-09-12T00:00:00Z", "database_service": "db",
                    "images": list(target.APPROVED_IMAGES), "inputs": inputs}
        (payload_root / "backup-manifest.json").write_text(json.dumps(manifest))
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            archive.add(payload_root, arcname="payload")
        return stream.getvalue()

    def test_stage_rejects_traversal_and_checksum_mismatch(self):
        bad = io.BytesIO()
        with tarfile.open(fileobj=bad, mode="w") as archive:
            info = tarfile.TarInfo("../../escape")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        value = bad.getvalue()
        with self.assertRaisesRegex(target.TargetError, "unsafe"):
            target.stage(self.config, "op-01", "a" * 64, "restore", hashlib.sha256(value).hexdigest(), len(value), io.BytesIO(value))
        bundle = self.bundle()
        with self.assertRaisesRegex(target.TargetError, "checksum"):
            target.stage(self.config, "op-01", "a" * 64, "restore", "b" * 64, len(bundle), io.BytesIO(bundle))

    def test_dispatch_rejects_free_commands_paths_and_partial_digests(self):
        adapter = mock.Mock()
        for command in ("", "rm -rf anything", "apply op-01 deadbeef deadbeef",
                        "stage ../../root " + "a" * 64 + " restore " + "b" * 64 + " 10"):
            with self.subTest(command=command), self.assertRaises(target.TargetError):
                target.dispatch(self.config, command, io.BytesIO(b""), adapter)

    def test_host_lock_is_nonblocking_and_rejects_unsafe_mode(self):
        with target.host_lock(self.config):
            with self.assertRaisesRegex(target.TargetError, "another active"):
                with target.host_lock(self.config):
                    pass
        self.config.host_lock.chmod(0o666)
        with self.assertRaisesRegex(target.TargetError, "unsafe"):
            with target.host_lock(self.config):
                pass

    def test_rollback_stage_is_bound_to_separate_approved_plan(self):
        bundle = self.bundle()
        digest = hashlib.sha256(bundle).hexdigest()
        target.stage(self.config, "op-01", "a" * 64, "restore", digest, len(bundle), io.BytesIO(bundle))
        store = target.TargetJournalStore(self.config.journal_root)
        store.update("op-01", state="failed", maintenance_active=True, safety_snapshot_id="e" * 64)
        target.stage(self.config, "op-01", "f" * 64, "rollback", digest, len(bundle), io.BytesIO(bundle))
        journal = store.load("op-01")
        self.assertEqual(journal["plan_hash"], "a" * 64)
        self.assertEqual(journal["rollback_plan_hash"], "f" * 64)
        self.assertEqual(journal["rollback_stage_sha256"], digest)

    def test_prepare_sets_maintenance_quiesces_app_and_exports_before_overwrite(self):
        bundle = self.bundle()
        target.stage(self.config, "op-01", "a" * 64, "restore", hashlib.sha256(bundle).hexdigest(), len(bundle), io.BytesIO(bundle))
        adapter = mock.Mock()
        adapter.target_state.return_value = "populated"
        adapter.create_safety_export.return_value = "22345678-1234-4123-8123-123456789abc"
        calls = []
        adapter.enable_maintenance.side_effect = lambda *_: calls.append("marker")
        adapter.publish_maintenance_route.side_effect = lambda *_: calls.append("route")
        adapter.quiesce_writes.side_effect = lambda *_: calls.append("quiesce")
        adapter.create_safety_export.side_effect = lambda *_: (
            calls.append("export") or "22345678-1234-4123-8123-123456789abc"
        )
        result = target.prepare(self.config, "op-01", "a" * 64, adapter)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(calls, ["marker", "route", "quiesce", "export"])
        adapter.enable_maintenance.assert_called_once()
        adapter.quiesce_writes.assert_called_once()
        adapter.publish_maintenance_route.assert_called_once()
        adapter.create_safety_export.assert_called_once()
        adapter.apply.assert_not_called()

    def test_empty_target_prepare_does_not_require_unrestored_compose_secrets(self):
        bundle = self.bundle()
        target.stage(self.config, "op-01", "a" * 64, "restore", hashlib.sha256(bundle).hexdigest(), len(bundle), io.BytesIO(bundle))
        adapter = mock.Mock()
        adapter.target_state.return_value = "empty"
        result = target.prepare(self.config, "op-01", "a" * 64, adapter)
        self.assertEqual(result["target_state"], "empty")
        adapter.enable_maintenance.assert_called_once()
        adapter.publish_maintenance_route.assert_called_once()
        adapter.quiesce_writes.assert_not_called()
        adapter.create_safety_export.assert_not_called()

    def test_restored_admin_marker_is_private_and_operation_bound(self):
        adapter = target.UmamiAdapter(self.config, mock.Mock())
        adapter._record_restored_admin_state("op-01")
        marker = self.config.secret_root / "admin-bootstrap.complete"
        self.assertEqual(marker.read_text(), "restored:op-01\n")
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)

    def test_target_preflight_runs_dump_and_capacity_validation(self):
        bundle = self.bundle()
        target.stage(self.config, "op-01", "a" * 64, "restore", hashlib.sha256(bundle).hexdigest(), len(bundle), io.BytesIO(bundle))
        adapter = mock.Mock()
        adapter.target_state.return_value = "empty"
        result = target.preflight(self.config, "op-01", "a" * 64, adapter)
        self.assertEqual(result["status"], "preflight-passed")
        adapter.validate_restore.assert_called_once()

    def test_database_dump_preflight_attaches_stdin_to_the_pinned_postgres_image(self):
        payload = self.root / "payload-source"
        self.bundle()
        runner = mock.Mock(return_value=mock.Mock(stdout=b"", returncode=0))
        target.UmamiAdapter(self.config, runner).validate_restore(payload)
        command = runner.call_args.args[0]
        self.assertIn("--interactive", command)
        self.assertEqual(command[-2:], [target.POSTGRES_IMAGE, "--list"])

    def test_real_adapter_maintenance_marker_and_route_are_operation_bound(self):
        runner = mock.Mock(return_value=mock.Mock(stdout=b"", returncode=0))
        adapter = target.UmamiAdapter(self.config, runner)
        original = self.config.caddy_route.read_text()
        adapter.enable_maintenance("op-01")
        marker = self.config.maintenance_root / "umami"
        self.assertEqual(marker.read_text(), "op-01\n")
        adapter.publish_maintenance_route("op-01")
        self.assertIn("503", self.config.caddy_route.read_text())
        with self.assertRaisesRegex(target.TargetError, "another maintenance"):
            adapter.enable_maintenance("op-02")
        adapter.disable_maintenance("op-01")
        self.assertFalse(marker.exists())
        self.assertEqual(self.config.caddy_route.read_text(), original)

    def test_apply_failure_keeps_maintenance_and_records_failed_state(self):
        bundle = self.bundle()
        target.stage(self.config, "op-01", "a" * 64, "restore", hashlib.sha256(bundle).hexdigest(), len(bundle), io.BytesIO(bundle))
        store = target.TargetJournalStore(self.config.journal_root)
        store.create("op-01", "a" * 64, "restore", hashlib.sha256(bundle).hexdigest())
        store.update("op-01", state="prepared", maintenance_active=True, target_state="populated",
                     safety_artifact_id="22345678-1234-4123-8123-123456789abc")
        adapter = mock.Mock()
        adapter.apply.side_effect = RuntimeError("database restore failed")
        with self.assertRaisesRegex(RuntimeError, "database"):
            target.apply(self.config, "op-01", "a" * 64, "e" * 64, adapter)
        journal = store.load("op-01")
        self.assertEqual(journal["state"], "failed")
        self.assertTrue(journal["maintenance_active"])
        adapter.disable_maintenance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
