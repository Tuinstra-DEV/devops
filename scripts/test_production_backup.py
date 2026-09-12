#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "backup" / "tuinstra_backup.py"
SPEC = importlib.util.spec_from_file_location("tuinstra_backup", SOURCE)
backup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def add_host_lock(self, config, host_slug="tuinstra-prod-01"):
        lock_root = self.root / "host-locks"
        lock_root.mkdir(exist_ok=True)
        lock_path = lock_root / f"operations.host.{host_slug}.lock"
        lock_path.touch()
        lock_path.chmod(0o660)
        config["host_lock_root"] = str(lock_root)
        backup.ROOT_UID = os.getuid()
        return config

    def producer(self):
        spool = self.root / "spool"
        work = self.root / "work"
        spool.mkdir()
        work.mkdir()
        recipient = self.root / "recipient"
        recipient.write_text("age1test\n")
        compose = self.root / "compose.yml"
        compose.write_text("services: {}\n")
        secret = self.root / "secret.env"
        secret.write_text("PASSWORD=do-not-log\n")
        return {
            "schema_version": 1, "host_slug": "tuinstra-prod-01",
            "spool_dir": str(spool), "work_dir": str(work),
            "receipt_dir": str(self.root / "receipts"),
            "lock_file": str(self.root / "export.lock"), "spool_quota_bytes": 1024 * 1024,
            "age_recipient_file": str(recipient),
            "applications": [{"app_id": "umami", "enabled": True,
                "adapter": "postgres-compose-v1", "compose_project": "umami",
                "compose_file": str(compose), "postgres_service": "db",
                "included_files": [{"name": "compose", "path": str(compose)},
                                   {"name": "secret", "path": str(secret)}]}],
        }

    def test_export_is_atomic_encrypted_and_public_manifest_is_secret_free(self):
        config = self.producer()

        def fake_run(argv, **kwargs):
            if argv[0] == "docker" and "exec" in argv:
                kwargs["stdout"].write(b"PGDMP-test")
                return mock.Mock(stdout=None)
            if argv[0] == "docker":
                return mock.Mock(stdout=b"postgres@sha256:" + b"a" * 64 + b"\n")
            if argv[0] == "age":
                shutil.copyfile(argv[-1], argv[argv.index("--output") + 1])
                return mock.Mock(stdout=b"")
            raise AssertionError(argv)

        with mock.patch.object(backup, "run", side_effect=fake_run):
            result = backup.create_export(config, "umami")
        spool = Path(config["spool_dir"])
        self.assertEqual(len(list(spool.glob("*.age"))), 1)
        self.assertEqual(len(list(spool.glob("*.json"))), 1)
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(result["payload_sha256"], backup.sha256(next(spool.glob("*.age"))))
        self.assertFalse(any(path.suffix == ".tar" for path in spool.iterdir()))

    def test_export_fails_closed_when_spool_quota_is_reached(self):
        config = self.producer()
        config["spool_quota_bytes"] = 1
        (Path(config["spool_dir"]) / "pending.age").write_bytes(b"x")
        with self.assertRaisesRegex(backup.BackupError, "quota"):
            backup.create_export(config, "umami")

    def ready_artifact(self, config):
        artifact = "12345678-1234-4123-8123-123456789abc"
        payload = Path(config["spool_dir"]) / f"{artifact}.age"
        payload.write_bytes(b"ciphertext")
        manifest = {"schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
                    "app_id": "umami", "adapter": "postgres-compose-v1", "created_at": "2026-09-12T00:00:00Z",
                    "payload_sha256": backup.sha256(payload), "payload_bytes": payload.stat().st_size}
        backup.atomic_json(Path(config["spool_dir"]) / f"{artifact}.json", manifest)
        return artifact, manifest

    def test_dispatch_lists_fetches_and_only_then_acknowledges_exact_checksum(self):
        config = self.producer()
        artifact, manifest = self.ready_artifact(config)
        output = io.BytesIO()
        backup.dispatch(config, "list", output)
        self.assertEqual(json.loads(output.getvalue()), [manifest])
        output = io.BytesIO()
        backup.dispatch(config, f"fetch {artifact}", output)
        self.assertEqual(output.getvalue(), b"ciphertext")
        with self.assertRaisesRegex(backup.BackupError, "checksum"):
            backup.dispatch(config, f"ack {artifact} {'0' * 64} restic:bad", io.BytesIO())
        self.assertTrue((Path(config["spool_dir"]) / f"{artifact}.age").exists())
        backup.dispatch(config, f"ack {artifact} {manifest['payload_sha256']} restic:abc", io.BytesIO())
        self.assertFalse((Path(config["spool_dir"]) / f"{artifact}.age").exists())
        self.assertTrue((Path(config["receipt_dir"]) / f"{artifact}.json").exists())

    def test_dispatch_rejects_commands_and_path_traversal(self):
        config = self.producer()
        for command in ("", "fetch ../../etc/passwd", "rm anything", "list extra"):
            with self.subTest(command=command), self.assertRaises(backup.BackupError):
                backup.dispatch(config, command, io.BytesIO())

    def test_safe_extract_rejects_traversal_and_links(self):
        archive = self.root / "bad.tar"
        with tarfile.open(archive, "w") as tar:
            info = tarfile.TarInfo("../../escape")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(backup.BackupError, "unsafe"):
            backup.safe_extract(archive, self.root / "out")

    def test_config_rejects_group_writable_file(self):
        source = self.root / "config.json"
        source.write_text('{"schema_version":1}')
        source.chmod(0o660)
        with self.assertRaisesRegex(backup.BackupError, "writable"):
            backup.load_config(str(source))

    def test_prod02_without_apps_reports_not_applicable_without_network(self):
        config = {"hosts": [{"host_slug": "tuinstra-prod-02", "applications": []}],
                  "pull_lock_root": str(self.root / "locks")}
        with mock.patch.object(backup, "ssh_json") as remote:
            result = backup.pull_host(config, "tuinstra-prod-02")
        remote.assert_not_called()
        self.assertEqual(result[0]["status"], "not-applicable")

    def test_pull_acks_only_after_restic_snapshot_and_integrity_check(self):
        work = self.root / "work"
        work.mkdir()
        password = self.root / "passwords" / "tuinstra-prod-01"
        password.mkdir(parents=True)
        (password / "umami.password").write_text("secret")
        artifact = "12345678-1234-4123-8123-123456789abc"
        payload = b"cipher"
        manifest = {"schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
                    "app_id": "umami", "adapter": "postgres-compose-v1", "created_at": "2026-09-12T00:00:00Z",
                    "payload_sha256": __import__("hashlib").sha256(payload).hexdigest(), "payload_bytes": len(payload)}
        host = {"host_slug": "tuinstra-prod-01", "applications": ["umami"], "ssh_user": "pull",
                "ssh_host": "prod", "identity_file": "/key", "known_hosts_file": "/known"}
        config = {"hosts": [host], "pull_lock_root": str(self.root / "locks"), "work_dir": str(work),
                  "repository_root": str(self.root / "repos"), "password_root": str(self.root / "passwords"),
                  "incoming_root": str(self.root / "incoming"), "executable": "/fixed/backup",
                  "installed_config": "/fixed/config", "check_subset": "5%",
                  "max_artifact_bytes": 1024 * 1024}
        calls = []

        def fake_remote(_host, command):
            calls.append(("ssh", command))
            return [manifest] if command == "list" else {"received": True}

        def fake_run(argv, **kwargs):
            calls.append((argv[0], argv[1:3]))
            if argv[0] == "ssh":
                kwargs["stdout"].write(payload)
                return mock.Mock(stdout=None)
            if argv[0] == "sudo":
                stored = {**manifest, "snapshot_id": "a" * 64, "stored_at": "x",
                          "integrity_checked_at": "x", "integrity_coverage": "full-repository-data"}
                return mock.Mock(stdout=json.dumps(stored).encode())
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "ssh_json", side_effect=fake_remote), \
             mock.patch.object(backup, "run", side_effect=fake_run):
            result = backup.pull_host(config, "tuinstra-prod-01")
        self.assertEqual(result[0]["snapshot_id"], "a" * 64)
        self.assertTrue(any(call[0] == "ssh" and str(call[1]).startswith("ack ") for call in calls))
        self.assertTrue(any(call[0] == "sudo" and "-n" in call[1] for call in calls))

    def test_failed_integrity_check_never_acknowledges_remote(self):
        # This invariant protects unreceived exports during an offline/corrupt destination event.
        host = {"host_slug": "tuinstra-prod-01", "applications": ["umami"], "ssh_user": "pull",
                "ssh_host": "prod", "identity_file": "/key", "known_hosts_file": "/known"}
        config = {"hosts": [host], "pull_lock_root": str(self.root / "locks"), "work_dir": str(self.root),
                  "incoming_root": str(self.root / "incoming"), "executable": "/fixed/backup",
                  "installed_config": "/fixed/config"}
        with mock.patch.object(backup, "ssh_json", side_effect=backup.BackupError("offline")) as remote:
            with self.assertRaisesRegex(backup.BackupError, "offline"):
                backup.pull_host(config, "tuinstra-prod-01")
        self.assertEqual(remote.call_args_list, [mock.call(host, "list")])

    def test_corrupt_transfer_never_reaches_restic_or_ack(self):
        work = self.root / "work"
        work.mkdir()
        artifact = "12345678-1234-4123-8123-123456789abc"
        manifest = {"schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
                    "app_id": "umami", "adapter": "postgres-compose-v1", "created_at": "2026-09-12T00:00:00Z",
                    "payload_sha256": "0" * 64, "payload_bytes": 6}
        host = {"host_slug": "tuinstra-prod-01", "applications": ["umami"], "ssh_user": "pull",
                "ssh_host": "prod", "identity_file": "/key", "known_hosts_file": "/known"}
        config = {"hosts": [host], "pull_lock_root": str(self.root / "locks"), "work_dir": str(work),
                  "incoming_root": str(self.root / "incoming"), "executable": "/fixed/backup",
                  "installed_config": "/fixed/config", "max_artifact_bytes": 1024 * 1024}

        def fake_run(argv, **kwargs):
            self.assertEqual(argv[0], "ssh")
            kwargs["stdout"].write(b"broken")
            return mock.Mock(stdout=None)

        with mock.patch.object(backup, "ssh_json", return_value=[manifest]) as remote, \
             mock.patch.object(backup, "run", side_effect=fake_run):
            with self.assertRaisesRegex(backup.BackupError, "checksum"):
                backup.pull_host(config, "tuinstra-prod-01")
        self.assertEqual(remote.call_args_list, [mock.call(host, "list")])

    def test_privileged_ingest_is_idempotent_and_checks_repository(self):
        artifact = "12345678-1234-4123-8123-123456789abc"
        incoming = self.root / "incoming" / artifact
        incoming.mkdir(parents=True)
        payload = incoming / "payload.age"
        payload.write_bytes(b"cipher")
        manifest = {"schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
                    "app_id": "umami", "adapter": "postgres-compose-v1", "created_at": "2026-09-12T00:00:00Z",
                    "payload_sha256": backup.sha256(payload), "payload_bytes": payload.stat().st_size}
        backup.atomic_json(incoming / "manifest.json", manifest)
        password = self.root / "passwords" / "tuinstra-prod-01"
        password.mkdir(parents=True)
        (password / "umami.password").write_text("secret")
        policies = self.root / "policies" / "tuinstra-prod-01"
        policies.mkdir(parents=True)
        policy = backup.policy_document("tuinstra-prod-01", "umami", "production-v1", 2, 0, 7, 4, 12)
        backup.atomic_json(policies / "umami.json", {**policy, "plan_hash": backup.document_hash(policy)})
        config = {"hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"],
                              "credential_name": "prod01.key", "identity_file": "/fixed/key"}],
                  "incoming_root": str(self.root / "incoming"), "repository_root": str(self.root / "repos"),
                  "password_root": str(self.root / "passwords"), "check_subset": "5%",
                  "operation_lock_root": str(self.root / "operations"),
                  "ingest_root": str(self.root / "ingest"), "max_artifact_bytes": 1024 * 1024,
                  "policy_root": str(self.root / "policies"), "catalog_root": str(self.root / "catalog")}
        calls = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            if argv[:2] == ["restic", "snapshots"]:
                return mock.Mock(stdout=b"[]")
            if argv[:2] == ["restic", "backup"]:
                return mock.Mock(stdout=(json.dumps({"message_type": "summary", "snapshot_id": "a" * 64}) + "\n").encode())
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "run", side_effect=fake_run):
            result = backup.ingest_artifact(config, "tuinstra-prod-01", "umami", artifact,
                                            "87654321-4321-4321-8321-cba987654321", "scheduled")
        self.assertEqual(result["snapshot_id"], "a" * 64)
        self.assertTrue(any(argv == ["restic", "check", "--read-data"] for argv in calls))
        self.assertEqual(result["integrity_coverage"], "full-repository-data")

        def retry_run(argv, **_kwargs):
            if argv[:2] == ["restic", "snapshots"]:
                return mock.Mock(stdout=json.dumps([{"id": "a" * 64, "short_id": "aaaaaaaa"}]).encode())
            if argv[:2] == ["restic", "backup"]:
                raise AssertionError("retry must reconcile existing artifact instead of backing up twice")
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "run", side_effect=retry_run):
            retried = backup.ingest_artifact(config, "tuinstra-prod-01", "umami", artifact,
                                             "87654321-4321-4321-8321-cba987654321", "scheduled")
        self.assertEqual(retried["snapshot_id"], "a" * 64)

    def test_ingest_materializes_only_allowlisted_files_into_private_staging(self):
        artifact = "12345678-1234-4123-8123-123456789abc"
        incoming = self.root / "incoming" / artifact
        incoming.mkdir(parents=True)
        payload = incoming / "payload.age"
        payload.write_bytes(b"x")
        manifest = {"schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
                    "app_id": "umami", "adapter": "postgres-compose-v1",
                    "created_at": "2026-09-12T00:00:00Z", "payload_sha256": backup.sha256(payload),
                    "payload_bytes": 1}
        backup.atomic_json(incoming / "manifest.json", manifest, 0o600)
        (incoming / "unexpected").write_bytes(b"x")
        config = {"hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"],
                              "credential_name": "prod01.key", "identity_file": "/fixed/key"}],
                  "incoming_root": str(self.root / "incoming"), "ingest_root": str(self.root / "ingest"),
                  "max_artifact_bytes": 1024}
        staged = backup.materialize_ingest(config, artifact)
        self.assertEqual({item.name for item in staged.iterdir()}, {"manifest.json", "payload.age"})
        payload.write_bytes(b"worker-replaced-after-copy")
        self.assertEqual((staged / "payload.age").read_bytes(), b"x")

    def test_retention_is_exactly_daily_weekly_monthly_and_checked(self):
        password = self.root / "passwords" / "prod"
        password.mkdir(parents=True)
        (password / "app.password").write_text("secret")
        policies = self.root / "policies" / "prod"
        policies.mkdir(parents=True)
        document = backup.policy_document("prod", "app", "production-v1", 2, 0, 7, 4, 12)
        backup.atomic_json(policies / "app.json", {**document, "plan_hash": backup.document_hash(document)})
        config = {"repository_root": str(self.root / "repos"), "password_root": str(self.root / "passwords"),
                  "retention_lock_file": str(self.root / "retention.lock"), "check_subset": "5%",
                  "policy_root": str(self.root / "policies"),
                  "retention_audit_root": str(self.root / "audit"),
                  "operation_lock_root": str(self.root / "operations"),
                  "catalog_root": str(self.root / "catalog")}
        self.add_host_lock(config, "prod")
        with mock.patch.object(backup, "run", side_effect=[
                mock.Mock(stdout=b'[{"keep":[{"id":"abc"}],"remove":[]}]'),
                mock.Mock(stdout=b""), mock.Mock(stdout=b""), mock.Mock(stdout=b"[]")]) as execute:
            backup.retain(config, "prod", "app", "production-v1")
        forget = execute.call_args_list[0].args[0]
        self.assertEqual(forget[forget.index("--keep-daily") + 1], "7")
        self.assertEqual(forget[forget.index("--keep-weekly") + 1], "4")
        self.assertEqual(forget[forget.index("--keep-monthly") + 1], "12")
        self.assertEqual(forget[forget.index("--group-by") + 1], "")
        self.assertEqual(forget[forget.index("--keep-tag") + 1], "tuinstra:production-restore-safety")
        self.assertEqual(execute.call_args_list[2].args[0][:2], ["restic", "check"])

    def test_retention_rejects_mutable_or_unknown_policy(self):
        config = {"policy_root": str(self.root / "policies")}
        with self.assertRaisesRegex(backup.BackupError, "policy"):
            backup.retain(config, "prod", "app", "custom")

    def test_policy_bounds_hash_immutability_and_idempotent_unit_apply(self):
        for field, value in (("hour", 24), ("minute", 60), ("daily", 0), ("weekly", 53), ("monthly", 25)):
            args = {"host_slug": "tuinstra-prod-01", "app_id": "umami", "policy_version": "v1",
                    "hour": 2, "minute": 0, "daily": 7, "weekly": 4, "monthly": 12}
            args[field] = value
            with self.subTest(field=field), self.assertRaises(backup.BackupError):
                backup.policy_document(**args)
        document = backup.policy_document("tuinstra-prod-01", "umami", "production-v1", 2, 0, 7, 4, 12)
        config = {"hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"],
                              "credential_name": "prod01.key", "identity_file": "/fixed/key"}],
                  "policy_root": str(self.root / "policies"), "systemd_unit_root": str(self.root / "systemd"),
                  "repository_root": str(self.root / "repos"), "work_dir": str(self.root / "work"),
                  "incoming_root": str(self.root / "incoming"), "pull_lock_root": str(self.root / "locks"),
                  "operation_lock_root": str(self.root / "operations"), "executable": "/fixed/backup",
                  "installed_config": "/fixed/config.json", "ingest_root": str(self.root / "ingest"),
                  "catalog_root": str(self.root / "catalog")}
        self.add_host_lock(config)
        plan_hash = backup.document_hash(document)
        with mock.patch.object(backup, "run", return_value=mock.Mock(stdout=b"")) as execute:
            first = backup.reconcile_policy(config, document, plan_hash)
            second = backup.reconcile_policy(config, document, plan_hash)
        self.assertEqual(first["status"], "applied")
        self.assertEqual(second["status"], "unchanged")
        systemctl_calls = [call.args[0][:3] for call in execute.call_args_list]
        self.assertIn(["systemctl", "enable", "--now"], systemctl_calls)
        self.assertIn(["systemctl", "is-enabled", "--quiet"], systemctl_calls)
        self.assertIn(["systemctl", "is-active", "--quiet"], systemctl_calls)
        fallback = self.root / "systemd/tuinstra-backup-daily-fallback-tuinstra-prod-01--umami.timer"
        self.assertIn("OnCalendar=*-*-* 03:15:00 Europe/Amsterdam", fallback.read_text())
        fallback_service = fallback.with_suffix(".service")
        self.assertIn(" ensure-daily ", fallback_service.read_text())
        changed = backup.policy_document("tuinstra-prod-01", "umami", "production-v1", 3, 0, 7, 4, 12)
        with self.assertRaisesRegex(backup.BackupError, "immutable"):
            backup.reconcile_policy(config, changed, backup.document_hash(changed))

    def test_retention_dry_run_must_keep_at_least_one_snapshot(self):
        password = self.root / "passwords" / "prod"
        password.mkdir(parents=True)
        (password / "app.password").write_text("secret")
        policies = self.root / "policies" / "prod"
        policies.mkdir(parents=True)
        document = backup.policy_document("prod", "app", "v1", 2, 0, 1, 1, 1)
        backup.atomic_json(policies / "app.json", {**document, "plan_hash": backup.document_hash(document)})
        config = {"repository_root": str(self.root / "repos"), "password_root": str(self.root / "passwords"),
                  "retention_lock_file": str(self.root / "retention.lock"), "policy_root": str(self.root / "policies"),
                  "operation_lock_root": str(self.root / "operations")}
        self.add_host_lock(config, "prod")
        with mock.patch.object(backup, "run", return_value=mock.Mock(stdout=b'[{"keep":[],"remove":[{"id":"last"}]}]')):
            with self.assertRaisesRegex(backup.BackupError, "last known"):
                backup.retain(config, "prod", "app", "v1")

    def test_authoritative_host_lock_rejects_concurrent_or_unsafe_operations(self):
        config = self.add_host_lock({})
        path = backup.host_operation_lock_path(config, "tuinstra-prod-01")
        descriptor = os.open(path, os.O_RDWR)
        try:
            backup.fcntl.flock(descriptor, backup.fcntl.LOCK_EX | backup.fcntl.LOCK_NB)
            with self.assertRaisesRegex(backup.BackupError, "another active operation"):
                with backup.host_operation_lock(config, "tuinstra-prod-01"):
                    pass
        finally:
            os.close(descriptor)
        path.chmod(0o666)
        with self.assertRaisesRegex(backup.BackupError, "unsafe"):
            with backup.host_operation_lock(config, "tuinstra-prod-01"):
                pass

    def test_catalog_marks_retention_removed_points_without_losing_history(self):
        config = {"catalog_root": str(self.root / "catalog"), "max_artifact_bytes": 1024}
        snapshot = "a" * 64
        value = backup.empty_catalog("tuinstra-prod-01", "umami")
        value["recovery_points"] = [{
            "snapshot_id": snapshot, "artifact_id": "12345678-1234-4123-8123-123456789abc",
            "host_slug": "tuinstra-prod-01", "app_id": "umami", "created_at": "2026-09-11T00:00:00Z",
            "stored_at": "2026-09-11T00:01:00Z", "integrity_checked_at": "2026-09-11T00:02:00Z",
            "integrity_coverage": "full-repository-data", "payload_bytes": 10,
            "payload_sha256": "b" * 64, "policy_version": "production-v1",
            "engine": "tuinstra-backup-v1", "state": "available", "removed_at": None,
            "repository_observed_at": "2026-09-11T00:02:00Z",
            "run_id": "87654321-4321-4321-8321-cba987654321", "trigger": "scheduled",
            "source_id": "tuinstra-prod-01", "destination_id": "sanctuary-restic",
        }]
        backup.write_catalog(config, value)
        backup.refresh_catalog(config, "tuinstra-prod-01", "umami", [], "2026-09-12T00:00:00Z")
        point = backup.load_catalog(config, "tuinstra-prod-01", "umami")["recovery_points"][0]
        self.assertEqual(point["state"], "removed")
        self.assertEqual(point["removed_at"], "2026-09-12T00:00:00Z")

    def test_daily_fallback_is_idempotent_after_a_durable_local_day_snapshot(self):
        config = {"catalog_root": str(self.root / "catalog"),
                  "max_artifact_bytes": 1024,
                  "hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"]}]}
        self.add_host_lock(config)
        value = backup.empty_catalog("tuinstra-prod-01", "umami")
        timestamp = backup.now()
        value["recovery_points"] = [{
            "snapshot_id": "a" * 64, "artifact_id": "12345678-1234-4123-8123-123456789abc",
            "host_slug": "tuinstra-prod-01", "app_id": "umami", "created_at": timestamp,
            "stored_at": timestamp, "integrity_checked_at": timestamp,
            "integrity_coverage": "full-repository-data", "payload_bytes": 10,
            "payload_sha256": "b" * 64, "policy_version": "production-v1",
            "engine": "tuinstra-backup-v1", "state": "available", "removed_at": None,
            "repository_observed_at": timestamp,
            "run_id": "87654321-4321-4321-8321-cba987654321", "trigger": "scheduled",
            "source_id": "tuinstra-prod-01", "destination_id": "sanctuary-restic",
        }]
        backup.write_catalog(config, value)
        with mock.patch.object(backup, "cycle") as run_cycle:
            result = backup.ensure_daily(config, "tuinstra-prod-01", "umami", "production-v1",
                                         "b" * 64, "scheduled")
        run_cycle.assert_not_called()
        self.assertEqual(result["status"], "already-durable-today")

    def test_attempt_failure_updates_status_without_removing_last_good_point(self):
        config = {"catalog_root": str(self.root / "catalog"), "max_artifact_bytes": 1024,
                  "hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"]}]}
        started = backup.attempt_start(config, "tuinstra-prod-01", "umami", "scheduled")
        self.assertEqual(started["status"], "running")
        finished = backup.attempt_finish(config, "tuinstra-prod-01", "umami", started["run_id"],
                                         "failed", "source_unreachable")
        self.assertEqual(finished["status"], "failed")
        current = backup.catalog(config, "tuinstra-prod-01", "umami")
        self.assertEqual(current["latest_attempt"]["error_code"], "source_unreachable")
        self.assertEqual(current["recovery_points"], [])


if __name__ == "__main__":
    unittest.main()
