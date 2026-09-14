#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
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
        backup.ROOT_UID = os.getuid()

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
        recipient.chmod(0o600)
        compose = self.root / "compose.yml"
        compose.write_text("services: {}\n")
        secret = self.root / "secret.env"
        secret.write_text("PASSWORD=do-not-log\n")
        postgres_image = "docker.io/library/postgres:15-alpine@sha256:" + "a" * 64
        umami_image = "ghcr.io/umami-software/umami:3.3.1@sha256:" + "b" * 64
        return {
            "schema_version": 1, "host_slug": "tuinstra-prod-01",
            "spool_dir": str(spool), "work_dir": str(work),
            "receipt_dir": str(self.root / "receipts"),
            "lock_file": str(self.root / "export.lock"), "spool_quota_bytes": 1024 * 1024,
            "age_recipient_file": str(recipient),
            "applications": [{"app_id": "umami", "enabled": True,
                "adapter": "postgres-compose-v1", "compose_project": "umami",
                "compose_file": str(compose), "postgres_service": "db", "application_service": "umami",
                "approved_images": {"db": postgres_image, "umami": umami_image},
                "included_files": [{"name": "compose", "path": str(compose)},
                                   {"name": "secret", "path": str(secret)}]}],
        }

    def tracker_app(self):
        compose = self.root / "tracker-compose.json"
        compose.write_text(json.dumps({"services": {
            "minio": {"environment": ["MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"]},
        }}))
        env_file = self.root / "tracker.env"
        env_file.write_text("S3_BUCKET=tracker-attachments\nMINIO_ROOT_USER=fixture-user\nMINIO_ROOT_PASSWORD=fixture-password\n")
        env_file.chmod(0o600)
        attachments = self.root / "attachments"
        (attachments / "nested").mkdir(parents=True)
        (attachments / "nested" / "report.txt").write_text("safe attachment\n")
        approved = {
            service: f"registry.example/{service}:release@sha256:{(hex(index)[2:] * 64)[:64]}"
            for index, service in enumerate(("postgres", "php", "php-mcp", "worker",
                                               "catalog-worker", "release-evidence-worker", "nginx", "minio",
                                               "minio-init", "clamav"), 1)
        }
        return {
            "app_id": "tracker", "enabled": True, "adapter": "tracker-compose-v1",
            "compose_project": "tracker", "compose_file": str(compose),
            "compose_images_file": str(compose), "compose_env_file": str(env_file),
            "object_store_bucket": "tracker-attachments",
            "postgres_service": "postgres", "application_service": "php",
            "runtime_image_services": ["postgres", "php", "php-mcp", "worker",
                                        "catalog-worker", "release-evidence-worker", "nginx", "minio"],
            "quiesce_services": ["php", "php-mcp", "worker", "catalog-worker",
                                  "release-evidence-worker", "nginx", "minio"],
            "approved_images": approved,
            "included_files": [],
            "included_directories": [{"name": "attachments", "path": str(attachments), "max_bytes": 1024}],
        }

    def test_tracker_attachment_snapshot_is_manifestable_and_rejects_links(self):
        app = self.tracker_app()
        source = Path(app["included_directories"][0]["path"])
        target = self.root / "attachments.tar"
        backup.snapshot_directory(source, target, 1024)
        with tarfile.open(target) as archive:
            names = {member.name for member in archive.getmembers()}
        self.assertIn("attachments/nested/report.txt", names)
        (source / "unsafe").symlink_to(source / "nested" / "report.txt")
        with self.assertRaisesRegex(backup.BackupError, "symbolic link"):
            backup.snapshot_directory(source, self.root / "rejected.tar", 1024)
        self.assertFalse((self.root / "rejected.tar").exists())

    def test_tracker_quiescence_restores_only_services_that_were_running(self):
        app = self.tracker_app()
        active = {"php", "worker"}
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if "ps" in argv and "--status" in argv:
                if "--services" in argv:
                    return mock.Mock(stdout=b"php\nworker\n")
                return mock.Mock(stdout=(("c" * 64 + "\n") if argv[-1] in active else "").encode())
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "run", side_effect=fake_run):
            with backup.tracker_quiescence(app):
                pass
        self.assertEqual(calls[-2][-2:], ["php", "worker"])
        self.assertEqual(calls[-1][-2:], ["php", "worker"])

    def test_tracker_quiescence_rejects_running_orphan_service_before_stop(self):
        app = self.tracker_app()
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if "ps" in argv and "--services" in argv:
                return mock.Mock(stdout=b"php\nclamav\n")
            return mock.Mock(stdout=("c" * 64 + "\n").encode())

        with mock.patch.object(backup, "run", side_effect=fake_run):
            with self.assertRaisesRegex(backup.BackupError, "orphan"):
                with backup.tracker_quiescence(app):
                    pass
        self.assertFalse(any("stop" in call for call in calls))

    def test_tracker_quiescence_keeps_postgres_running_for_database_export(self):
        app = self.tracker_app()
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if "ps" in argv and "--services" in argv:
                return mock.Mock(stdout=b"php\npostgres\n")
            if "ps" in argv and "--quiet" in argv:
                return mock.Mock(stdout=("c" * 64 + "\n").encode() if argv[-1] == "php" else b"")
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "run", side_effect=fake_run):
            with backup.tracker_quiescence(app):
                pass
        stop_calls = [call for call in calls if "stop" in call]
        self.assertEqual(stop_calls[-1][-1:], ["php"])

    def test_tracker_quiescence_restarts_original_set_after_partial_stop_failure(self):
        app = self.tracker_app()
        calls = []

        def failing_stop(argv, **kwargs):
            calls.append(argv)
            if "--services" in argv:
                return mock.Mock(stdout=b"php\nworker\n")
            if "--quiet" in argv:
                return mock.Mock(stdout=("c" * 64 + "\n").encode())
            if " stop " in f" {' '.join(argv)} ":
                raise backup.BackupError("stop failed")
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "run", side_effect=failing_stop):
            with self.assertRaisesRegex(backup.BackupError, "stop failed"):
                with backup.tracker_quiescence(app):
                    pass
        self.assertTrue(any("start" in call for call in calls))

    def test_tracker_restore_evidence_requires_isolated_non_application_health(self):
        evidence = {
            "schema_version": 1, "adapter": "tracker-compose-v1", "application": "tracker",
            "status": "passed", "public_table_count": 3,
            "application_health": "not-run-external-effects-blocked",
            "encrypted_secret_validation": "not-applicable", "database_content_marker": "passed",
            "object_store_reconciliation": "passed",
            "network": "loopback-only-network-namespace", "external_effects_blocked": True,
            "host_ports": 0, "postgres_image": "postgres@sha256:" + "a" * 64,
            "application_image": "tracker@sha256:" + "b" * 64,
            "containers_removed": True, "workspace_removed": True,
        }
        self.assertEqual(backup.validate_tracker_restore_adapter_result(evidence), evidence)
        evidence["application_health"] = "passed"
        with self.assertRaisesRegex(backup.BackupError, "health"):
            backup.validate_tracker_restore_adapter_result(evidence)

    def test_tracker_export_uses_host_bucket_when_minio_exposes_only_credentials(self):
        app = self.tracker_app()
        compose_contract = json.loads(Path(app["compose_file"]).read_text(encoding="utf-8"))
        self.assertNotIn("S3_BUCKET", compose_contract["services"]["minio"]["environment"])
        config = {
            "schema_version": 1, "host_slug": "tuinstra-prod-02",
            "spool_dir": str(self.root / "spool"), "work_dir": str(self.root / "work"),
            "receipt_dir": str(self.root / "receipts"), "lock_file": str(self.root / "export.lock"),
            "spool_quota_bytes": 1024 * 1024, "age_recipient_file": str(self.root / "recipient"),
            "applications": [app],
        }
        for key in ("spool_dir", "work_dir"):
            Path(config[key]).mkdir()
        Path(config["age_recipient_file"]).write_text("age1tracker\n")
        config_path = Path(config["age_recipient_file"])
        config_path.chmod(0o600)
        migrations = ["DoctrineMigrations\\Version20260813120000", "DoctrineMigrations\\Version20260912160000"]
        row_counts = {"organization_count": 1, "project_count": 2, "story_count": 3, "attachment_count": 1}
        attachment_inventory = [{"status": "available", "object_key": "nested/report.txt",
                                 "deletion_object_key": None, "declared_size": len(b"safe attachment\n"),
                                 "declared_sha256": hashlib.sha256(b"safe attachment\n").hexdigest()}]
        calls = []
        service_ids = {service: (hex(index)[2:] * 64)[:64]
                       for index, service in enumerate(app["approved_images"], 1)}
        image_digests = {service: image.rpartition("@sha256:")[2]
                         for service, image in app["approved_images"].items()}
        id_digests = {service_ids[service]: image_digests[service] for service in service_ids}

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == "age":
                destination = Path(argv[argv.index("--output") + 1])
                shutil.copyfile(argv[-1], destination)
                return mock.Mock(stdout=b"")
            if argv[:2] == ["docker", "compose"] and "config" in argv:
                return mock.Mock(stdout=("\n".join(app["approved_images"].values()) + "\n").encode())
            if argv[:2] == ["docker", "compose"] and "ps" in argv:
                if "--status" in argv:
                    if "--services" in argv:
                        return mock.Mock(stdout=b"php\nworker\nminio\n")
                    return mock.Mock(stdout=("d" * 64 + "\n").encode())
                service = argv[-1]
                return mock.Mock(stdout=(service_ids[service] + "\n").encode())
            if argv[:2] == ["docker", "inspect"]:
                return mock.Mock(stdout=("sha256:" + id_digests[next(key for key in id_digests if key == argv[-1])] + "\n").encode())
            if argv[:3] == ["docker", "image", "inspect"]:
                digest = argv[-1].partition("sha256:")[2]
                return mock.Mock(stdout=json.dumps(["registry@sha256:" + digest]).encode())
            if argv[:2] == ["docker", "run"] and "ls" in argv:
                return mock.Mock(stdout=(json.dumps({"status": "success", "type": "file",
                    "key": "nested/report.txt", "size": len(b"safe attachment\n")}) + "\n").encode())
            if argv[:2] == ["docker", "run"] and "cat" in argv:
                kwargs["stdout"].write(b"safe attachment\n")
                return mock.Mock(stdout=None)
            if "exec" in argv and "MINIO_ROOT_USER" in argv[-1]:
                return mock.Mock(stdout=b"fixture-user\nfixture-password\n")
            if "exec" in argv and "pg_dump --version" in argv[-1]:
                return mock.Mock(stdout=b"pg_dump (PostgreSQL) 17.5\n")
            if "exec" in argv and "show server_version" in argv[-1]:
                return mock.Mock(stdout=b"17.5\n")
            if "exec" in argv and "doctrine_migration_versions" in argv[-1]:
                return mock.Mock(stdout=("\n".join(migrations) + "\n").encode())
            if "exec" in argv and backup.TRACKER_ATTACHMENT_INVENTORY_SQL in argv[-1]:
                return mock.Mock(stdout=(json.dumps(attachment_inventory) + "\n").encode())
            if "exec" in argv and "jsonb_build_object" in argv[-1]:
                return mock.Mock(stdout=(json.dumps(row_counts) + "\n").encode())
            if "exec" in argv:
                kwargs["stdout"].write(b"PGDMP-tracker")
                return mock.Mock(stdout=None)
            if argv[:2] == ["docker", "compose"] and ("stop" in argv or "start" in argv):
                return mock.Mock(stdout=b"")
            raise AssertionError(argv)

        with mock.patch.object(backup, "run", side_effect=fake_run):
            result = backup.create_export(config, "tracker")
        self.assertEqual(result["adapter"], "tracker-compose-v1")
        with tarfile.open(next((self.root / "spool").glob("*.age"))) as archive:
            internal = json.load(archive.extractfile("payload/backup-manifest.json"))
            self.assertIn("payload/files/attachments.tar", archive.getnames())
            object_manifest = json.load(archive.extractfile("payload/files/object-manifest.json"))
        self.assertEqual(internal["database"]["content_marker"]["row_counts"], row_counts)
        self.assertEqual(internal["database"]["content_marker"]["migration_count"], len(migrations))
        self.assertEqual(internal["database"]["attachment_inventory"], attachment_inventory)
        self.assertEqual(internal["object_store_bucket"], "tracker-attachments")
        self.assertEqual(object_manifest["objects"][0]["bytes"], len(b"safe attachment\n"))
        self.assertEqual(object_manifest["objects"][0]["sha256"], hashlib.sha256(b"safe attachment\n").hexdigest())
        self.assertTrue(any("--env-file" in call and call.count("--file") == 2 for call in calls))
        self.assertFalse(any("fixture-password" in argument for call in calls for argument in call))
        self.assertFalse(any("S3_BUCKET" in argument for call in calls for argument in call
                             if "exec" in call))

    def test_tracker_empty_inventory_is_valid_when_only_withdrawn_records_remain(self):
        app = self.tracker_app()
        inventory = [{"status": "withdrawn", "object_key": "retained/a",
                      "deletion_object_key": None, "declared_size": 1, "declared_sha256": "a" * 64}]

        def fake_run(argv, **kwargs):
            if argv[:2] == ["docker", "compose"] and "ps" in argv:
                return mock.Mock(stdout=("d" * 64 + "\n").encode())
            if "exec" in argv:
                return mock.Mock(stdout=b"fixture-user\nfixture-password\n")
            if argv[:2] == ["docker", "run"]:
                return mock.Mock(stdout=b"")
            raise AssertionError(argv)

        payload_one, payload_two = self.root / "payload-one", self.root / "payload-two"
        payload_one.mkdir(); payload_two.mkdir()
        with mock.patch.object(backup, "run", side_effect=fake_run):
            result_one = backup.tracker_object_manifest(app, payload_one, inventory)
            result_two = backup.tracker_object_manifest(app, payload_two, inventory)
        self.assertEqual(json.loads((payload_one / "files/object-manifest.json").read_text()), {
            "algorithm": "tracker-s3-object-v1", "bucket": "tracker-attachments", "objects": [],
            "object_count": 0, "total_bytes": 0,
        })
        self.assertEqual(result_one["sha256"], result_two["sha256"])
        self.assertEqual(result_one["bytes"], result_two["bytes"])

    def test_tracker_attachment_inventory_accepts_available_object_with_matching_metadata(self):
        content = b"safe attachment\n"
        inventory = [{"status": "available", "object_key": "a/report.txt",
                      "deletion_object_key": None, "declared_size": len(content),
                      "declared_sha256": hashlib.sha256(content).hexdigest()}]
        backup._reconcile_tracker_objects(inventory, [{"key": "a/report.txt", "bytes": len(content),
                                                        "sha256": hashlib.sha256(content).hexdigest()}])

    def test_tracker_attachment_inventory_rejects_missing_available_object(self):
        inventory = [{"status": "available", "object_key": "a/report.txt",
                      "deletion_object_key": None, "declared_size": 1, "declared_sha256": "a" * 64}]
        with self.assertRaisesRegex(backup.BackupError, "has no object"):
            backup._reconcile_tracker_objects(inventory, [])

    def test_tracker_attachment_inventory_rejects_object_checksum_mismatch(self):
        inventory = [{"status": "available", "object_key": "a/report.txt",
                      "deletion_object_key": None, "declared_size": 1, "declared_sha256": "a" * 64}]
        with self.assertRaisesRegex(backup.BackupError, "checksum or size mismatch"):
            backup._reconcile_tracker_objects(inventory, [{"key": "a/report.txt", "bytes": 1, "sha256": "b" * 64}])

    def test_tracker_attachment_inventory_rejects_orphan_object(self):
        with self.assertRaisesRegex(backup.BackupError, "orphan"):
            backup._reconcile_tracker_objects([], [{"key": "orphan.bin", "bytes": 1, "sha256": "a" * 64}])

    def test_tracker_empty_helper_output_is_not_an_error_but_helper_failure_is(self):
        self.assertEqual(backup._parse_tracker_object_listing(b""), [])
        app = self.tracker_app()
        payload = self.root / "payload"
        payload.mkdir()
        with mock.patch.object(backup, "run", side_effect=backup.BackupError("external command failed: docker")), \
             self.assertRaisesRegex(backup.BackupError, "external command failed"):
            backup.tracker_object_manifest(app, payload, [])
        self.assertFalse(list(payload.rglob("object-manifest.json")))

    def fake_export_run(self, config, encrypted_hook=None):
        app = config["applications"][0]

        def execute(argv, **kwargs):
            if argv[0] == "age":
                encrypted = Path(argv[argv.index("--output") + 1])
                if encrypted_hook:
                    encrypted_hook(encrypted)
                shutil.copyfile(argv[-1], encrypted)
                return mock.Mock(stdout=b"")
            if argv[:2] != ["docker", "compose"] and argv[:3] != ["docker", "image", "inspect"] \
                    and argv[:2] != ["docker", "inspect"]:
                raise AssertionError(argv)
            if "config" in argv and "--images" in argv:
                return mock.Mock(stdout=("\n".join(app["approved_images"].values()) + "\n").encode())
            if "ps" in argv and "--quiet" in argv:
                service = argv[-1]
                return mock.Mock(stdout=(("a" if service == "db" else "b") * 64 + "\n").encode())
            if argv[:2] == ["docker", "inspect"]:
                return mock.Mock(stdout=("sha256:" + argv[-1] + "\n").encode())
            if argv[:3] == ["docker", "image", "inspect"]:
                digest = "a" * 64 if argv[-1].endswith("a" * 64) else "b" * 64
                return mock.Mock(stdout=json.dumps([f"repo@sha256:{digest}"]).encode())
            if "exec" in argv and "pg_dump --version" in argv[-1]:
                return mock.Mock(stdout=b"pg_dump (PostgreSQL) 15.15\n")
            if "exec" in argv and "show server_version" in argv[-1]:
                return mock.Mock(stdout=b"15.15\n")
            if "exec" in argv and "jsonb_build_object" in argv[-1]:
                marker = {"user_count": 1, "two_factor_count": 1,
                          "admin": {"user_id": "11111111-1111-4111-8111-111111111111", "username": "admin"},
                          "admin_two_factor": {"user_id": "11111111-1111-4111-8111-111111111111",
                                               "is_enabled": True, "secret": "encrypted-marker"}}
                return mock.Mock(stdout=(json.dumps(marker, sort_keys=True) + "\n").encode())
            if "exec" in argv:
                kwargs["stdout"].write(b"PGDMP-test")
                return mock.Mock(stdout=None)
            raise AssertionError(argv)

        return execute

    def test_export_is_atomic_encrypted_and_public_manifest_is_secret_free(self):
        config = self.producer()

        with mock.patch.object(backup, "run", side_effect=self.fake_export_run(config)):
            result = backup.create_export(config, "umami")
        spool = Path(config["spool_dir"])
        self.assertEqual(len(list(spool.glob("*.age"))), 1)
        self.assertEqual(len(list(spool.glob("*.json"))), 1)
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(result["payload_sha256"], backup.sha256(next(spool.glob("*.age"))))
        self.assertFalse(any(path.suffix == ".tar" for path in spool.iterdir()))
        with tarfile.open(next(spool.glob("*.age")), "r") as archive:
            internal = json.load(archive.extractfile("payload/backup-manifest.json"))
        self.assertEqual(internal["database"], {
            "engine": "postgresql", "server_version": "15.15",
            "dump_version": "pg_dump (PostgreSQL) 15.15", "dump_format": "custom", "service": "db",
            "content_marker": {"algorithm": "umami-admin-two-factor-v1",
                               "sha256": internal["database"]["content_marker"]["sha256"],
                               "user_count": 1, "two_factor_count": 1},
        })
        self.assertRegex(internal["database"]["content_marker"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(internal["image_services"], config["applications"][0]["approved_images"])

    def test_export_rejects_running_image_that_does_not_match_approved_digest(self):
        config = self.producer()
        normal = self.fake_export_run(config)

        def mismatched(argv, **kwargs):
            if argv[:3] == ["docker", "image", "inspect"]:
                return mock.Mock(stdout=json.dumps(["repo@sha256:" + "f" * 64]).encode())
            return normal(argv, **kwargs)

        with mock.patch.object(backup, "run", side_effect=mismatched), \
             self.assertRaisesRegex(backup.BackupError, r"source export failed \[stage=compose-contract\]"):
            backup.create_export(config, "umami")
        self.assertFalse(list(Path(config["spool_dir"]).glob("*.age")))

    def test_export_reports_compose_contract_stage_without_remote_reason(self):
        config = self.producer()
        with mock.patch.object(backup, "running_compose_images",
                               side_effect=backup.BackupError("compose image configuration does not match")), \
             self.assertRaisesRegex(backup.BackupError, r"source export failed \[stage=compose-contract\]"):
            backup.create_export(config, "umami")
        self.assertFalse(list(Path(config["spool_dir"]).glob("*.age")))

    def test_export_reports_disabled_application_contract_stage(self):
        config = self.producer()
        config["applications"][0]["enabled"] = False
        with self.assertRaisesRegex(backup.BackupError,
                                    r"source export failed \[stage=application-contract\]"):
            backup.create_export(config, "umami")
        self.assertFalse(list(Path(config["spool_dir"]).glob("*.age")))

    def test_export_stages_ciphertext_on_spool_filesystem_before_atomic_publication(self):
        config = self.producer()
        spool = Path(config["spool_dir"])
        real_replace = backup.os.replace
        ciphertext_publications = []

        def reject_cross_device_replace(source, destination):
            source_path, destination_path = Path(source), Path(destination)
            if destination_path.suffix == ".age":
                ciphertext_publications.append((source_path, destination_path))
                self.assertEqual(source_path.parent, destination_path.parent)
            return real_replace(source, destination)

        with mock.patch.object(backup, "run", side_effect=self.fake_export_run(
                config, lambda encrypted: self.assertEqual(encrypted.parent, spool))), \
             mock.patch.object(backup.os, "replace", side_effect=reject_cross_device_replace):
            backup.create_export(config, "umami")
        self.assertEqual(len(ciphertext_publications), 1)
        self.assertEqual(len(list(spool.glob("*.age"))), 1)
        self.assertFalse(list(spool.glob(".*.age.tmp")))

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
        # A lost SSH response may repeat the exact ACK. It returns the persisted receipt
        # and completes any partially interrupted source cleanup without changing identity.
        retried = io.BytesIO()
        backup.dispatch(config, f"ack {artifact} {manifest['payload_sha256']} restic:abc", retried)
        self.assertEqual(json.loads(retried.getvalue())["receipt_id"], "restic:abc")

    def test_ack_receipt_retry_removes_crash_leftovers_and_rejects_changed_identity(self):
        config = self.producer()
        artifact, manifest = self.ready_artifact(config)
        receipt = {**manifest, "received_at": "2026-09-12T01:00:00Z", "receipt_id": "restic:abc"}
        backup.atomic_json(Path(config["receipt_dir"]) / f"{artifact}.json", receipt)
        backup.dispatch(config, f"ack {artifact} {manifest['payload_sha256']} restic:abc", io.BytesIO())
        self.assertFalse((Path(config["spool_dir"]) / f"{artifact}.json").exists())
        with self.assertRaisesRegex(backup.BackupError, "receipt identity"):
            backup.dispatch(config, f"ack {artifact} {manifest['payload_sha256']} restic:different", io.BytesIO())

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
        (password / "umami.password").chmod(0o600)
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

        stored = {**manifest, "snapshot_id": "a" * 64, "stored_at": "x",
                  "integrity_checked_at": "x", "integrity_coverage": "full-repository-data"}

        def fake_run(argv, **kwargs):
            calls.append((argv[0], argv[1:3]))
            if argv[0] == "ssh":
                kwargs["stdout"].write(payload)
                return mock.Mock(stdout=None)
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "ssh_json", side_effect=fake_remote), \
             mock.patch.object(backup, "run", side_effect=fake_run), \
             mock.patch.object(backup, "ingest_artifact", return_value=stored) as ingest, \
             mock.patch.object(backup, "cleanup_ingest"):
            result = backup.pull_host(config, "tuinstra-prod-01")
        self.assertEqual(result[0]["snapshot_id"], "a" * 64)
        self.assertTrue(any(call[0] == "ssh" and str(call[1]).startswith("ack ") for call in calls))
        ingest.assert_called_once()
        self.assertFalse((self.root / "incoming" / artifact).exists())

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
        (password / "umami.password").chmod(0o600)
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

    def test_safety_ingest_pins_exact_operation_and_returns_full_checked_snapshot(self):
        artifact = "12345678-1234-4123-8123-123456789abc"
        run_id = "87654321-4321-4321-8321-cba987654321"
        operation = "restore-op-123"
        incoming = self.root / "incoming" / artifact
        incoming.mkdir(parents=True)
        payload = incoming / "payload.age"
        payload.write_bytes(b"cipher")
        manifest = {"schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
                    "app_id": "umami", "adapter": "postgres-compose-v1",
                    "created_at": "2026-09-12T00:00:00Z", "payload_sha256": backup.sha256(payload),
                    "payload_bytes": payload.stat().st_size}
        backup.atomic_json(incoming / "manifest.json", manifest, 0o600)
        password = self.root / "passwords/tuinstra-prod-01"
        password.mkdir(parents=True)
        (password / "umami.password").write_text("secret")
        (password / "umami.password").chmod(0o600)
        policy = backup.policy_document("tuinstra-prod-01", "umami", "production-v1", 2, 0, 7, 4, 12)
        policy_path = self.root / "policies/tuinstra-prod-01"
        policy_path.mkdir(parents=True)
        backup.atomic_json(policy_path / "umami.json", {**policy, "plan_hash": backup.document_hash(policy)})
        config = {
            "hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"]}],
            "incoming_root": str(self.root / "incoming"), "ingest_root": str(self.root / "ingest"),
            "max_artifact_bytes": 1024, "repository_root": str(self.root / "repositories"),
            "password_root": str(self.root / "passwords"), "operation_lock_root": str(self.root / "locks"),
            "catalog_root": str(self.root / "catalog"), "policy_root": str(self.root / "policies"),
        }
        self.add_host_lock(config)
        calls = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            if argv[:2] == ["restic", "snapshots"]:
                return mock.Mock(stdout=b"[]")
            if argv[:2] == ["restic", "backup"]:
                return mock.Mock(stdout=(json.dumps({"message_type": "summary", "snapshot_id": "a" * 64}) + "\n").encode())
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "run", side_effect=fake_run):
            result = backup.safety_ingest(config, "tuinstra-prod-01", "umami", artifact, operation, run_id)
        command = next(argv for argv in calls if argv[:2] == ["restic", "backup"])
        tags = [command[index + 1] for index, value in enumerate(command) if value == "--tag"]
        self.assertIn("tuinstra:production-restore-safety", tags)
        self.assertIn(f"operation:{operation}", tags)
        self.assertEqual(result["status"], "safety-stored")
        self.assertEqual(result["snapshot_id"], "a" * 64)
        self.assertEqual(result["tags"], ["tuinstra:production-restore-safety", f"operation:{operation}"])
        self.assertEqual(result["integrity_coverage"], "full-repository-data")

    def test_safety_ingest_rejects_existing_artifact_without_matching_pin(self):
        artifact = "12345678-1234-4123-8123-123456789abc"
        staged = self.root / "staged"
        staged.mkdir()
        payload = staged / "payload.age"
        payload.write_bytes(b"cipher")
        backup.atomic_json(staged / "manifest.json", {
            "schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
            "app_id": "umami", "adapter": "postgres-compose-v1",
            "created_at": "2026-09-12T00:00:00Z", "payload_sha256": backup.sha256(payload),
            "payload_bytes": payload.stat().st_size,
        }, 0o600)
        config = {"hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"]}],
                  "operation_lock_root": str(self.root / "locks"), "max_artifact_bytes": 1024}
        self.add_host_lock(config)
        with mock.patch.object(backup, "materialize_ingest", return_value=staged), \
             mock.patch.object(backup, "run", return_value=mock.Mock(stdout=json.dumps([{
                 "id": "a" * 64, "tags": [f"artifact:{artifact}"]}]).encode())), \
             mock.patch.object(backup, "restic_env", return_value=({}, self.root)):
            with self.assertRaisesRegex(backup.BackupError, "safety pin"):
                backup.safety_ingest(config, "tuinstra-prod-01", "umami",
                    artifact, "restore-op-123",
                    "87654321-4321-4321-8321-cba987654321")

    def test_safety_pull_fetches_only_exact_artifact_and_acks_after_local_cleanup(self):
        artifact = "12345678-1234-4123-8123-123456789abc"
        run_id = "87654321-4321-4321-8321-cba987654321"
        operation = "restore-op-123"
        payload = self.root / "cipher"
        payload.write_bytes(b"cipher")
        manifest = {"schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
                    "app_id": "umami", "adapter": "postgres-compose-v1",
                    "created_at": "2026-09-12T00:00:00Z", "payload_sha256": backup.sha256(payload),
                    "payload_bytes": payload.stat().st_size}
        host = {"host_slug": "tuinstra-prod-01", "applications": ["umami"], "ssh_user": "pull",
                "ssh_host": "prod", "identity_file": "/key", "known_hosts_file": "/known"}
        config = {"hosts": [host], "pull_lock_root": str(self.root / "pull-locks"),
                  "incoming_root": str(self.root / "incoming"), "ingest_root": str(self.root / "ingest"),
                  "max_artifact_bytes": 1024}
        self.add_host_lock(config)
        incoming = Path(config["incoming_root"]) / artifact
        ingest = Path(config["ingest_root"]) / artifact
        stored = {**manifest, "snapshot_id": "a" * 64, "stored_at": backup.now(),
                  "integrity_checked_at": backup.now(), "integrity_coverage": "full-repository-data"}
        remote_calls = []

        def stage(*_args):
            incoming.mkdir(parents=True)
            incoming.chmod(0o700)
            return incoming

        def fake_remote(_host, command):
            remote_calls.append(command)
            if command == "list":
                return [manifest]
            self.assertTrue(command.startswith("ack "))
            self.assertFalse(incoming.exists())
            self.assertFalse(ingest.exists())
            return {"received": True}

        with mock.patch.object(backup, "reconcile_safety_point", return_value=None), \
             mock.patch.object(backup, "stage_remote_artifact", side_effect=stage), \
             mock.patch.object(backup, "ingest_artifact", return_value=stored) as durable, \
             mock.patch.object(backup, "ssh_json", side_effect=fake_remote):
            result = backup.safety_pull(config, "tuinstra-prod-01", "umami", artifact, operation, run_id)
        durable.assert_called_once_with(config, "tuinstra-prod-01", "umami", artifact, run_id, "console",
                                        safety_operation=operation)
        self.assertEqual(remote_calls[0], "list")
        self.assertTrue(remote_calls[-1].startswith("ack "))
        self.assertEqual(result["status"], "safety-stored")

    def test_safety_pull_reconciles_checked_snapshot_and_retries_exact_ack_without_duplicate(self):
        artifact = "12345678-1234-4123-8123-123456789abc"
        result = {"status": "safety-stored", "host_slug": "tuinstra-prod-01", "app_id": "umami",
                  "artifact_id": artifact, "operation_id": "restore-op-123", "snapshot_id": "a" * 64,
                  "tags": [backup.SAFETY_TAG, "operation:restore-op-123"],
                  "integrity_checked_at": backup.now(), "integrity_coverage": "full-repository-data"}
        config = {"hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"]}],
                  "pull_lock_root": str(self.root / "pull-locks")}
        self.add_host_lock(config)
        with mock.patch.object(backup, "reconcile_safety_point", return_value=(result, "b" * 64)), \
             mock.patch.object(backup, "ssh_json", return_value={"received": True}) as remote, \
             mock.patch.object(backup, "ingest_artifact") as ingest:
            actual = backup.safety_pull(config, "tuinstra-prod-01", "umami", artifact,
                                        "restore-op-123", "87654321-4321-4321-8321-cba987654321")
        remote.assert_called_once_with(config["hosts"][0],
            f"ack {artifact} {'b' * 64} restic:{'a' * 64}")
        ingest.assert_not_called()
        self.assertEqual(actual, result)

    def test_safety_pull_ack_failure_retries_ack_from_durable_pin_without_second_ingest(self):
        artifact = "12345678-1234-4123-8123-123456789abc"
        operation = "restore-op-123"
        run_id = "87654321-4321-4321-8321-cba987654321"
        manifest = {"schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
                    "app_id": "umami", "adapter": "postgres-compose-v1", "created_at": backup.now(),
                    "payload_sha256": "b" * 64, "payload_bytes": 6}
        stored = {**manifest, "snapshot_id": "a" * 64, "stored_at": backup.now(),
                  "integrity_checked_at": backup.now(), "integrity_coverage": "full-repository-data"}
        result = backup.safety_result(stored, operation)
        host = {"host_slug": "tuinstra-prod-01", "applications": ["umami"]}
        config = {"hosts": [host], "pull_lock_root": str(self.root / "pull-locks"),
                  "max_artifact_bytes": 1024}
        self.add_host_lock(config)
        with mock.patch.object(backup, "reconcile_safety_point",
                               side_effect=[None, (result, manifest["payload_sha256"])]), \
             mock.patch.object(backup, "stage_remote_artifact", return_value=self.root), \
             mock.patch.object(backup, "cleanup_ingest"), \
             mock.patch.object(backup, "cleanup_worker_staging"), \
             mock.patch.object(backup, "ingest_artifact", return_value=stored) as ingest, \
             mock.patch.object(backup, "ssh_json", side_effect=[
                 [manifest], backup.BackupError("source unreachable"), {"received": True},
             ]) as remote:
            with self.assertRaisesRegex(backup.BackupError, "unreachable"):
                backup.safety_pull(config, "tuinstra-prod-01", "umami", artifact, operation, run_id)
            retried = backup.safety_pull(config, "tuinstra-prod-01", "umami", artifact, operation, run_id)
        self.assertEqual(ingest.call_count, 1)
        self.assertEqual(remote.call_args_list[-1], mock.call(
            host, f"ack {artifact} {manifest['payload_sha256']} restic:{stored['snapshot_id']}"))
        self.assertEqual(retried, result)

    def test_safety_pull_never_acks_when_durable_ingest_fails(self):
        artifact = "12345678-1234-4123-8123-123456789abc"
        manifest = {"schema_version": 1, "artifact_id": artifact, "host_slug": "tuinstra-prod-01",
                    "app_id": "umami", "adapter": "postgres-compose-v1",
                    "created_at": "2026-09-12T00:00:00Z", "payload_sha256": "a" * 64,
                    "payload_bytes": 6}
        host = {"host_slug": "tuinstra-prod-01", "applications": ["umami"]}
        config = {"hosts": [host], "pull_lock_root": str(self.root / "pull-locks"),
                  "max_artifact_bytes": 1024}
        self.add_host_lock(config)
        with mock.patch.object(backup, "reconcile_safety_point", return_value=None), \
             mock.patch.object(backup, "ssh_json", return_value=[manifest]) as remote, \
             mock.patch.object(backup, "stage_remote_artifact", return_value=self.root), \
             mock.patch.object(backup, "ingest_artifact", side_effect=backup.BackupError("integrity failed")):
            with self.assertRaisesRegex(backup.BackupError, "integrity"):
                backup.safety_pull(config, "tuinstra-prod-01", "umami", artifact, "restore-op-123",
                                   "87654321-4321-4321-8321-cba987654321")
        self.assertEqual(remote.call_args_list, [mock.call(host, "list")])

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
        (password / "app.password").chmod(0o600)
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

    def test_inspect_reports_allowlisted_app_without_active_policy_as_not_applicable(self):
        config = {
            "hosts": [{"host_slug": "tuinstra-prod-02", "applications": ["tracker"]}],
            "policy_root": str(self.root / "policies"),
            "repository_root": str(self.root / "repositories"),
            "password_root": str(self.root / "passwords"),
        }
        result = backup.inspect(config)
        self.assertEqual(result["hosts"], [{
            "host_slug": "tuinstra-prod-02",
            "applications": [{
                "app_id": "tracker",
                "status": "not-applicable",
                "reason": "no-active-policy",
            }],
        }])

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
        (password / "app.password").chmod(0o600)
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

    def test_inherited_host_lock_descriptor_must_be_the_exact_authoritative_inode(self):
        config = self.add_host_lock({})
        authoritative = backup.host_operation_lock_path(config, "tuinstra-prod-01")
        descriptor = os.open(authoritative, os.O_RDWR)
        wrong = self.root / "wrong.lock"
        wrong.touch()
        wrong.chmod(0o660)
        wrong_descriptor = os.open(wrong, os.O_RDWR)
        try:
            backup.fcntl.flock(descriptor, backup.fcntl.LOCK_EX | backup.fcntl.LOCK_NB)
            backup.validate_host_lock_descriptor(config, "tuinstra-prod-01", descriptor)
            with self.assertRaisesRegex(backup.BackupError, "authoritative lock"):
                backup.validate_host_lock_descriptor(config, "tuinstra-prod-01", wrong_descriptor)
        finally:
            os.close(wrong_descriptor)
            os.close(descriptor)

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

    def test_manual_run_resolves_immutable_active_policy_without_cli_hash(self):
        config = {"hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"]}],
                  "policy_root": str(self.root / "policies")}
        document = backup.policy_document("tuinstra-prod-01", "umami", "updated-v2", 3, 15, 9, 5, 13)
        path = self.root / "policies/tuinstra-prod-01"
        path.mkdir(parents=True)
        plan_hash = backup.document_hash(document)
        backup.atomic_json(path / "umami.json", {**document, "plan_hash": plan_hash})
        with mock.patch.object(backup, "cycle", return_value={"status": "durable"}) as execute:
            result = backup.run_active(config, "tuinstra-prod-01", "umami", "manual")
        execute.assert_called_once_with(config, "tuinstra-prod-01", "umami", "updated-v2",
                                        plan_hash, "manual")
        self.assertEqual(result["status"], "durable")

    def test_ssh_exit_codes_distinguish_remote_export_failure_from_unreachable_source(self):
        with mock.patch.object(backup.subprocess, "run", side_effect=__import__("subprocess").CalledProcessError(
                1, ["ssh"], stderr=b"backup operation failed: source export failed [stage=object-inventory]\n")):
            with self.assertRaisesRegex(backup.BackupError, "export") as failure:
                backup.run(["ssh", "fixed-host", "export"])
        self.assertEqual(backup.failure_code(failure.exception), "source_export_failed")
        self.assertEqual(backup.failure_stage(failure.exception), "object-inventory")
        self.assertNotIn("PASSWORD", str(failure.exception))
        with mock.patch.object(backup.subprocess, "run", side_effect=__import__("subprocess").CalledProcessError(
                255, ["ssh"])):
            with self.assertRaisesRegex(backup.BackupError, "unreachable") as failure:
                backup.run(["ssh", "fixed-host", "export"])
        self.assertEqual(backup.failure_code(failure.exception), "source_unreachable")

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
        self.assertIsNone(current["latest_attempt"]["stage_code"])
        self.assertEqual(current["recovery_points"], [])

    def test_source_export_failure_records_safe_stage_code_without_reason(self):
        config = {"catalog_root": str(self.root / "catalog"), "max_artifact_bytes": 1024,
                  "hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"]}]}
        started = backup.attempt_start(config, "tuinstra-prod-01", "umami", "manual")
        failure = backup.BackupError("source export failed [stage=runtime-evidence]")
        backup.attempt_finish(config, "tuinstra-prod-01", "umami", started["run_id"], "failed",
                              backup.failure_code(failure), backup.failure_stage(failure))
        current = backup.catalog(config, "tuinstra-prod-01", "umami")
        self.assertEqual(current["latest_attempt"]["error_code"], "source_export_failed")
        self.assertEqual(current["latest_attempt"]["stage_code"], "runtime-evidence")
        self.assertNotIn("PASSWORD", json.dumps(current["latest_attempt"]))

    def test_attempt_cannot_report_success_without_exact_durable_run_proof(self):
        config = {"catalog_root": str(self.root / "catalog"), "max_artifact_bytes": 1024,
                  "hosts": [{"host_slug": "tuinstra-prod-01", "applications": ["umami"]}]}
        started = backup.attempt_start(config, "tuinstra-prod-01", "umami", "scheduled")
        with self.assertRaisesRegex(backup.BackupError, "durable snapshot"):
            backup.attempt_finish(config, "tuinstra-prod-01", "umami", started["run_id"],
                                  "succeeded", None)
        current = backup.load_catalog(config, "tuinstra-prod-01", "umami")
        current["recovery_points"].append({
            "snapshot_id": "a" * 64, "artifact_id": "12345678-1234-4123-8123-123456789abc",
            "host_slug": "tuinstra-prod-01", "app_id": "umami", "created_at": backup.now(),
            "stored_at": backup.now(), "integrity_checked_at": backup.now(),
            "integrity_coverage": "full-repository-data", "payload_bytes": 10,
            "payload_sha256": "b" * 64, "policy_version": "production-v1",
            "engine": "tuinstra-backup-v1", "state": "available", "removed_at": None,
            "repository_observed_at": backup.now(), "run_id": started["run_id"], "trigger": "scheduled",
            "source_id": "tuinstra-prod-01", "destination_id": "sanctuary-restic",
        })
        backup.write_catalog(config, current)
        finished = backup.attempt_finish(config, "tuinstra-prod-01", "umami", started["run_id"],
                                         "succeeded", None)
        self.assertEqual(finished["status"], "succeeded")

    def restore_fixture(self):
        host_slug = "tuinstra-prod-01"
        app_id = "umami"
        artifact_id = "12345678-1234-4123-8123-123456789abc"
        snapshot_id = "a" * 64
        payload = self.root / "payload.age"
        payload.write_bytes(b"encrypted-payload")
        public = {
            "schema_version": 1, "artifact_id": artifact_id, "host_slug": host_slug,
            "app_id": app_id, "adapter": "postgres-compose-v1",
            "created_at": "2026-09-12T00:00:00Z", "payload_sha256": backup.sha256(payload),
            "payload_bytes": payload.stat().st_size,
        }
        extracted = self.root / "archive-source" / "payload"
        extracted.mkdir(parents=True)
        files = {
            "database.dump": b"PGDMP-test",
            "files/postgres-env": b"POSTGRES_DB=umami\nPOSTGRES_USER=umami\nPOSTGRES_PASSWORD=test\n",
            "files/umami-env": b"APP_SECRET=test\nTWO_FACTOR_ENCRYPTION_KEY=" + b"d" * 64 + b"\n",
            "files/two-factor-encryption-key": b"d" * 64 + b"\n",
            "files/admin-password": b"admin-test-password\n",
        }
        inputs = []
        for name, content in files.items():
            target = extracted / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            inputs.append({"name": name, "sha256": backup.sha256(target), "bytes": len(content)})
        internal = {"schema_version": 1, "artifact_id": artifact_id, "host_slug": host_slug,
            "app_id": app_id, "adapter": "postgres-compose-v1", "created_at": public["created_at"],
            "database_service": "db", "inputs": inputs, "images": [
            "docker.io/library/postgres:15-alpine@sha256:" + "b" * 64,
            "ghcr.io/umami-software/umami:3.3.1@sha256:" + "c" * 64,
        ], "image_services": {
            "db": "docker.io/library/postgres:15-alpine@sha256:" + "b" * 64,
            "umami": "ghcr.io/umami-software/umami:3.3.1@sha256:" + "c" * 64,
        }, "database": {
            "engine": "postgresql", "server_version": "15.15",
            "dump_version": "pg_dump (PostgreSQL) 15.15", "dump_format": "custom", "service": "db",
            "content_marker": {"algorithm": backup.UMAMI_CONTENT_MARKER_ALGORITHM,
                               "sha256": "d" * 64, "user_count": 1, "two_factor_count": 1},
        }}
        (extracted / "backup-manifest.json").write_text(json.dumps(internal))
        archive = self.root / "payload.tar"
        with tarfile.open(archive, "w") as handle:
            handle.add(extracted.parent / "payload", arcname="payload")

        password = self.root / "passwords" / host_slug
        password.mkdir(parents=True)
        (password / f"{app_id}.password").write_text("restic-password")
        (password / f"{app_id}.password").chmod(0o600)
        identity = self.root / "age-identity.txt"
        identity.write_text("AGE-SECRET-KEY-test")
        identity.chmod(0o600)
        config = {
            "hosts": [{"host_slug": host_slug, "applications": [app_id]}],
            "repository_root": str(self.root / "repositories"),
            "password_root": str(self.root / "passwords"),
            "restore_work_dir": str(self.root / "restore-work"),
            "restore_lock_file": str(self.root / "restore.lock"),
            "operation_lock_root": str(self.root / "operation-locks"),
            "evidence_root": str(self.root / "evidence"),
            "catalog_root": str(self.root / "catalog"),
            "max_artifact_bytes": 1024 * 1024,
            "age_identity_file": str(identity),
            "restore_adapter": "/fixed/restore-umami",
        }
        self.add_host_lock(config, host_slug)
        point = {
            "snapshot_id": snapshot_id, "artifact_id": artifact_id, "host_slug": host_slug,
            "app_id": app_id, "created_at": public["created_at"],
            "stored_at": "2026-09-12T00:01:00Z", "integrity_checked_at": "2026-09-12T00:02:00Z",
            "integrity_coverage": "full-repository-data", "payload_bytes": public["payload_bytes"],
            "payload_sha256": public["payload_sha256"], "policy_version": "production-v1",
            "engine": "tuinstra-backup-v1", "state": "available", "removed_at": None,
            "repository_observed_at": "2026-09-12T00:02:00Z",
            "run_id": "87654321-4321-4321-8321-cba987654321", "trigger": "scheduled",
            "source_id": host_slug, "destination_id": "sanctuary-restic",
        }
        catalog = backup.empty_catalog(host_slug, app_id)
        catalog["recovery_points"] = [point]
        backup.write_catalog(config, catalog)
        return config, public, payload, archive, snapshot_id

    def test_payload_manifest_rejects_unsafe_duplicate_or_unmanifested_inputs(self):
        _config, public, _payload, _archive, _snapshot = self.restore_fixture()
        root = self.root / "archive-source"
        manifest_path = root / "payload/backup-manifest.json"
        original = json.loads(manifest_path.read_text())
        cases = []
        absolute = json.loads(json.dumps(original))
        absolute["inputs"][0]["name"] = "/etc/passwd"
        cases.append(("absolute", absolute, "input name"))
        traversal = json.loads(json.dumps(original))
        traversal["inputs"][0]["name"] = "files/../database.dump"
        cases.append(("traversal", traversal, "input name"))
        duplicate = json.loads(json.dumps(original))
        duplicate["inputs"][1]["name"] = duplicate["inputs"][0]["name"]
        cases.append(("duplicate", duplicate, "unique"))
        extra_key = json.loads(json.dumps(original))
        extra_key["inputs"][0]["path"] = "/tmp/attacker"
        cases.append(("extra-key", extra_key, "input schema"))
        invalid_marker = json.loads(json.dumps(original))
        invalid_marker["database"]["content_marker"]["sha256"] = "not-a-checksum"
        cases.append(("invalid-content-marker", invalid_marker, "database version evidence"))
        for label, changed, error in cases:
            with self.subTest(label=label):
                manifest_path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(backup.BackupError, error):
                    backup.validate_payload(root, public)
        manifest_path.write_text(json.dumps(original))
        (root / "payload/unmanifested").write_text("unexpected")
        with self.assertRaisesRegex(backup.BackupError, "unmanifested"):
            backup.validate_payload(root, public)

    def test_payload_manifest_rejects_symlink_in_input_path(self):
        _config, public, _payload, _archive, _snapshot = self.restore_fixture()
        root = self.root / "archive-source"
        manifest_path = root / "payload/backup-manifest.json"
        internal = json.loads(manifest_path.read_text())
        target = root / "outside"
        target.mkdir()
        (target / "secret").write_text("secret")
        (root / "payload/link").symlink_to(target, target_is_directory=True)
        secret = target / "secret"
        internal["inputs"].append({"name": "link/secret", "sha256": backup.sha256(secret),
                                   "bytes": secret.stat().st_size})
        manifest_path.write_text(json.dumps(internal))
        with self.assertRaisesRegex(backup.BackupError, "symbolic link"):
            backup.validate_payload(root, public)

    def test_restore_test_records_only_truthful_explicit_evidence_after_cleanup(self):
        config, public, payload, archive, snapshot_id = self.restore_fixture()
        restore_targets = []
        postgres_image = "docker.io/library/postgres:15-alpine@sha256:" + "b" * 64
        application_image = "ghcr.io/umami-software/umami:3.3.1@sha256:" + "c" * 64

        def fake_run(argv, **_kwargs):
            if argv == ["restic", "check", "--read-data"]:
                return mock.Mock(stdout=b"")
            if argv[:2] == ["restic", "restore"]:
                self.assertEqual(argv[2], snapshot_id)
                target = Path(argv[argv.index("--target") + 1])
                restore_targets.append(target.parent)
                artifact = target / "artifact"
                artifact.mkdir(parents=True)
                (artifact / "manifest.json").write_text(json.dumps(public))
                shutil.copyfile(payload, artifact / "payload.age")
                return mock.Mock(stdout=b"")
            if argv[0] == "age":
                shutil.copyfile(archive, argv[argv.index("--output") + 1])
                return mock.Mock(stdout=b"")
            if argv[0] == config["restore_adapter"]:
                adapter = {
                    "schema_version": 1, "adapter": "postgres-compose-v1", "application": "umami",
                    "status": "passed", "public_table_count": 12,
                    "application_health": "passed", "encrypted_secret_validation": "passed",
                    "database_content_marker": "passed",
                    "network": "loopback-only-network-namespace",
                    "external_effects_blocked": True, "host_ports": 0,
                    "postgres_image": postgres_image, "application_image": application_image,
                    "containers_removed": True, "workspace_removed": True,
                }
                return mock.Mock(stdout=json.dumps(adapter).encode())
            raise AssertionError(argv)

        with mock.patch.object(backup, "run", side_effect=fake_run), \
             mock.patch.object(backup.shutil, "disk_usage", return_value=mock.Mock(free=2 * 1024**3)):
            evidence = backup.restore_test(config, "tuinstra-prod-01", "umami", "latest")

        self.assertEqual(evidence["snapshot_id"], snapshot_id)
        self.assertRegex(evidence["manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(evidence["restore_status"], "passed")
        self.assertEqual(evidence["preflight"]["key"], "passed")
        self.assertEqual(evidence["preflight"]["payload_checksum"], "passed")
        self.assertEqual(evidence["preflight"]["compatibility"], "passed")
        self.assertGreaterEqual(evidence["preflight"]["available_bytes"], evidence["preflight"]["required_bytes"])
        self.assertEqual(evidence["validation"]["schema"], "passed")
        self.assertEqual(evidence["validation"]["data"], "passed")
        self.assertEqual(evidence["validation"]["public_table_count"], 12)
        self.assertEqual(evidence["validation"]["input_files_verified"], 5)
        self.assertEqual(evidence["validation"]["database_content_marker"], "passed")
        self.assertEqual(evidence["validation"]["encrypted_two_factor_authentication"], "passed")
        self.assertEqual(evidence["validation"]["application_health"], "passed")
        self.assertEqual(evidence["versions"]["postgres_image"], postgres_image)
        self.assertTrue(evidence["isolation"]["external_effects_blocked"])
        self.assertEqual(evidence["isolation"]["host_ports"], 0)
        self.assertEqual(evidence["cleanup"], {"status": "passed", "containers_removed": True,
                                                "workspace_removed": True})
        self.assertGreaterEqual(evidence["duration_seconds"], 0)
        self.assertLessEqual(evidence["duration_seconds"], 14400)
        self.assertEqual(evidence["engine_version"], "tuinstra-backup-v1")
        self.assertTrue(restore_targets)
        self.assertTrue(all(not target.exists() for target in restore_targets))
        persisted = json.loads((self.root / "evidence/tuinstra-prod-01/umami" /
                                f'{public["artifact_id"]}.json').read_text())
        self.assertEqual(persisted, evidence)

    def test_restore_test_rejects_adapter_cleanup_failure_without_success_evidence(self):
        config, public, payload, archive, snapshot_id = self.restore_fixture()

        def fake_run(argv, **_kwargs):
            if argv == ["restic", "check", "--read-data"]:
                return mock.Mock(stdout=b"")
            if argv[:2] == ["restic", "restore"]:
                target = Path(argv[argv.index("--target") + 1]) / "artifact"
                target.mkdir(parents=True)
                (target / "manifest.json").write_text(json.dumps(public))
                shutil.copyfile(payload, target / "payload.age")
                return mock.Mock(stdout=b"")
            if argv[0] == "age":
                shutil.copyfile(archive, argv[argv.index("--output") + 1])
                return mock.Mock(stdout=b"")
            return mock.Mock(stdout=json.dumps({"schema_version": 1, "adapter": "postgres-compose-v1",
                "application": "umami", "status": "passed", "public_table_count": 1,
                "application_health": "passed", "encrypted_secret_validation": "passed",
                "database_content_marker": "passed",
                "network": "loopback-only-network-namespace",
                "external_effects_blocked": True, "host_ports": 0,
                "postgres_image": "postgres@sha256:" + "b" * 64,
                "application_image": "umami@sha256:" + "c" * 64,
                "containers_removed": False, "workspace_removed": True}).encode())

        with mock.patch.object(backup, "run", side_effect=fake_run), \
             mock.patch.object(backup.shutil, "disk_usage", return_value=mock.Mock(free=2 * 1024**3)):
            with self.assertRaisesRegex(backup.BackupError, "cleanup"):
                backup.restore_test(config, "tuinstra-prod-01", "umami", snapshot_id)
        self.assertFalse((self.root / "evidence/tuinstra-prod-01/umami" /
                          f'{public["artifact_id"]}.json').exists())

    def test_restore_test_fails_capacity_preflight_before_restic_or_decryption(self):
        config, _public, _payload, _archive, snapshot_id = self.restore_fixture()
        with mock.patch.object(backup, "run") as execute, \
             mock.patch.object(backup.shutil, "disk_usage", return_value=mock.Mock(free=1)):
            with self.assertRaisesRegex(backup.BackupError, "capacity"):
                backup.restore_test(config, "tuinstra-prod-01", "umami", snapshot_id)
        execute.assert_not_called()

    def recovery_bundle(self):
        source = self.root / "recovery.json"
        private_key = ("-----BEGIN OPENSSH PRIVATE KEY-----\n" + "a" * 64
                       + "\n-----END OPENSSH PRIVATE KEY-----\n")
        source.write_text(json.dumps({
            "schema_version": 1, "source_host": "sanctuary", "secrets": {
                "age_identity": "# created: 2026-09-12T00:00:00Z\n# public key: age1example\n"
                                + "AGE-SECRET-KEY-1" + "A" * 58 + "\n",
                "restic_prod01_umami": "restic-password-from-vault",
                "ssh_prod01": private_key, "ssh_prod02": private_key,
            },
        }))
        source.chmod(0o600)
        return source

    def test_escrow_recovery_test_uses_fixed_independent_keys_and_removes_all_staging(self):
        source = self.recovery_bundle()
        restore_work = self.root / "restore-work"
        config = {"restore_work_dir": str(restore_work), "max_artifact_bytes": 1024 * 1024, "hosts": [
            {"host_slug": "tuinstra-prod-01", "applications": ["umami"],
             "known_hosts_file": "/fixed/known", "ssh_user": "pull", "ssh_host": "prod01"},
            {"host_slug": "tuinstra-prod-02", "applications": [],
             "known_hosts_file": "/fixed/known", "ssh_user": "pull", "ssh_host": "prod02"},
        ]}
        seen = []

        def verify(host, identity, maximum_bytes):
            self.assertEqual(maximum_bytes, 1024 * 1024)
            seen.append((host["host_slug"], identity.read_text()))

        evidence = {"snapshot_id": "a" * 64}
        with mock.patch.object(backup.pwd, "getpwnam", return_value=mock.Mock(pw_uid=os.getuid())), \
             mock.patch.object(backup, "verify_recovered_transport", side_effect=verify), \
             mock.patch.object(backup, "restore_test", return_value=evidence) as restore:
            old_root_uid = backup.ROOT_UID
            backup.ROOT_UID = os.geteuid()
            try:
                result = backup.escrow_recovery_test(config, source)
            finally:
                backup.ROOT_UID = old_root_uid
        self.assertFalse(source.exists())
        self.assertEqual([item[0] for item in seen], ["tuinstra-prod-01", "tuinstra-prod-02"])
        recovered_config = restore.call_args.args[0]
        self.assertNotEqual(recovered_config["age_identity_file"], config.get("age_identity_file"))
        self.assertEqual(restore.call_args.args[1:], ("tuinstra-prod-01", "umami", "latest"))
        self.assertEqual(result["credential_source"], "independent-escrow-copy")
        self.assertNotIn("password", json.dumps(result))
        self.assertEqual(list(restore_work.iterdir()), [])

    def test_escrow_recovery_failure_preserves_user_bundle_and_removes_root_private_copies(self):
        source = self.recovery_bundle()
        restore_work = self.root / "restore-work"
        config = {"restore_work_dir": str(restore_work), "max_artifact_bytes": 1024 * 1024, "hosts": [
            {"host_slug": "tuinstra-prod-01", "applications": ["umami"]},
            {"host_slug": "tuinstra-prod-02", "applications": []},
        ]}
        with mock.patch.object(backup.pwd, "getpwnam", return_value=mock.Mock(pw_uid=os.getuid())), \
             mock.patch.object(backup, "verify_recovered_transport"), \
             mock.patch.object(backup, "restore_test",
                               side_effect=backup.BackupError("restore validation failed")):
            old_root_uid = backup.ROOT_UID
            backup.ROOT_UID = os.geteuid()
            try:
                with self.assertRaisesRegex(backup.BackupError, "restore validation"):
                    backup.escrow_recovery_test(config, source)
            finally:
                backup.ROOT_UID = old_root_uid
        self.assertTrue(source.exists())
        self.assertEqual(list(restore_work.iterdir()), [])

    def test_materialize_snapshot_uses_fixed_private_destination_and_is_idempotent(self):
        config, public, payload, archive, snapshot_id = self.restore_fixture()
        config["materialized_root"] = str(self.root / "materialized")

        def fake_run(argv, **_kwargs):
            if argv[:2] == ["restic", "restore"]:
                target = Path(argv[argv.index("--target") + 1]) / "artifact"
                target.mkdir(parents=True)
                (target / "manifest.json").write_text(json.dumps(public))
                shutil.copyfile(payload, target / "payload.age")
                return mock.Mock(stdout=b"")
            if argv[0] == "age":
                shutil.copyfile(archive, argv[argv.index("--output") + 1])
                return mock.Mock(stdout=b"")
            if argv == ["restic", "check", "--read-data"]:
                return mock.Mock(stdout=b"")
            raise AssertionError(argv)

        with mock.patch.object(backup, "run", side_effect=fake_run):
            result = backup.materialize_snapshot(config, "tuinstra-prod-01", "umami", snapshot_id,
                                                 "restore-op-123", "restore")
        self.assertEqual(result["status"], "materialized")
        self.assertEqual(result["snapshot_id"], snapshot_id)
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertTrue((self.root / "materialized/restore-op-123/restore/payload/database.dump").is_file())
        with mock.patch.object(backup, "run") as execute:
            retried = backup.materialize_snapshot(config, "tuinstra-prod-01", "umami", snapshot_id,
                                                   "restore-op-123", "restore")
        execute.assert_not_called()
        self.assertEqual(retried, result)

    def test_materialize_rejects_wrong_target_binding_and_unsafe_operation(self):
        config, _public, _payload, _archive, snapshot_id = self.restore_fixture()
        config["materialized_root"] = str(self.root / "materialized")
        with mock.patch.object(backup, "run") as execute:
            with self.assertRaisesRegex(backup.BackupError, "operation id"):
                backup.materialize_snapshot(config, "tuinstra-prod-01", "umami", snapshot_id,
                                             "../escape", "restore")
            with self.assertRaisesRegex(backup.BackupError, "allowlisted"):
                backup.materialize_snapshot(config, "tuinstra-prod-02", "umami", snapshot_id,
                                             "restore-op-123", "restore")
        execute.assert_not_called()

    def test_materialize_rejects_snapshot_artifact_or_payload_checksum_mismatch(self):
        config, public, payload, _archive, snapshot_id = self.restore_fixture()
        config["materialized_root"] = str(self.root / "materialized")

        def artifact_mismatch(argv, **_kwargs):
            if argv == ["restic", "check", "--read-data"]:
                return mock.Mock(stdout=b"")
            target = Path(argv[argv.index("--target") + 1]) / "artifact"
            target.mkdir(parents=True)
            changed = {**public, "artifact_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
            (target / "manifest.json").write_text(json.dumps(changed))
            shutil.copyfile(payload, target / "payload.age")
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "run", side_effect=artifact_mismatch):
            with self.assertRaisesRegex(backup.BackupError, "catalog identity"):
                backup.materialize_snapshot(config, "tuinstra-prod-01", "umami", snapshot_id,
                                             "restore-op-artifact", "restore")

        def checksum_mismatch(argv, **_kwargs):
            if argv == ["restic", "check", "--read-data"]:
                return mock.Mock(stdout=b"")
            target = Path(argv[argv.index("--target") + 1]) / "artifact"
            target.mkdir(parents=True)
            (target / "manifest.json").write_text(json.dumps(public))
            (target / "payload.age").write_bytes(b"different-ciphertext")
            return mock.Mock(stdout=b"")

        with mock.patch.object(backup, "run", side_effect=checksum_mismatch):
            with self.assertRaisesRegex(backup.BackupError, "checksum"):
                backup.materialize_snapshot(config, "tuinstra-prod-01", "umami", snapshot_id,
                                             "restore-op-checksum", "restore")

    def test_materialize_cli_has_no_path_parameter(self):
        with mock.patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            backup.parser().parse_args(["--config", "/fixed", "materialize", "--host", "tuinstra-prod-01",
                "--app", "umami", "--snapshot", "a" * 64, "--operation", "restore-op-123",
                "--purpose", "restore", "--path", "/tmp/escape"])


if __name__ == "__main__":
    unittest.main()
