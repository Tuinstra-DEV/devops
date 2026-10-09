"""Docker-independent verification of the locally built scanner OCI archive."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts/gate-pr-security-local-integration.py"
sys.path.insert(0, str(ROOT / ".github/actions/gate-pr-security"))
spec = importlib.util.spec_from_file_location("gate_pr_security_local_integration", MODULE)
integration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integration)


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


class OciArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = self.root / "scanner.oci.tar"
        self.receipt = self.root / "receipt.json"
        self.write_archive()
        self.image = "ghcr.io/tuinstra-dev/gate/ci-scanner@" + self.manifest_digest
        self.profile = {**integration.constants.profile_for("Tuinstra-DEV/gate"), "image": self.image}
        self.write_receipt()

    def tearDown(self):
        self.temp.cleanup()

    def write_archive(self, *, config=None, index_override=None, extra_members=()):
        config = config or json.dumps({"os": "linux", "architecture": "amd64", "config": {}}).encode()
        config_digest = digest(config)
        layer = b"synthetic layer"
        manifest = json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                       "digest": config_digest, "size": len(config)},
            "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar",
                        "digest": digest(layer), "size": len(layer)}],
        }, separators=(",", ":")).encode()
        manifest_digest = digest(manifest)
        index = index_override or json.dumps({
            "schemaVersion": 2,
            "manifests": [{"mediaType": "application/vnd.oci.image.manifest.v1+json",
                           "digest": manifest_digest, "size": len(manifest),
                           "platform": {"os": "linux", "architecture": "amd64"}}],
        }, separators=(",", ":")).encode()
        members = {
            "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
            "index.json": index,
            "blobs/sha256/" + config_digest.split(":")[1]: config,
            "blobs/sha256/" + digest(layer).split(":")[1]: layer,
            "blobs/sha256/" + manifest_digest.split(":")[1]: manifest,
        }
        with tarfile.open(self.archive, "w") as tar:
            for name, data in list(members.items()) + list(extra_members):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(data))
        self.manifest_digest = manifest_digest
        self.config_id = config_digest

    def write_receipt(self, **changes):
        raw = self.archive.read_bytes()
        value = {
            "artifact": "/build-output/scanner.oci.tar",
            "artifact_sha256": digest(raw),
            "oci_manifest_digest": self.manifest_digest,
            "loaded_image_id": self.config_id,
            "loaded_image_size_bytes": 123456,
            "base_image_id": integration.NATIVE_BASE_IMAGE_ID,
            "base_oci_manifest_digest": None,
            "base_oci_config_digest": None,
            "base_registry_reference": integration.NATIVE_BASE_REFERENCE,
            "runtime_profile": "native-amd64",
            "platform": "linux/amd64",
            "runner_source_manifest_sha256": "sha256:" + "e" * 64,
            "runner_source_file_count": 12,
            "source_commit": integration.NATIVE_SOURCE_COMMIT,
            "source_worktree_clean": True,
        }
        value.update(changes)
        self.receipt.write_text(json.dumps(value), encoding="utf-8")

    def verify(self, **kwargs):
        profile = kwargs.pop("profile", self.profile)
        return integration.verify_oci_archive(self.archive, self.receipt, profile, **kwargs)

    def test_valid_archive_binds_registry_manifest_and_loaded_config_id(self):
        self.assertEqual((self.config_id, self.manifest_digest), self.verify())

    def test_rejects_archive_sha_mismatch(self):
        self.write_receipt(artifact_sha256="sha256:" + "0" * 64)
        with self.assertRaisesRegex(integration.LocalIntegrationError, "receipt_archive_mismatch"):
            self.verify()

    def test_rejects_registry_pin_not_matching_receipt_and_archive(self):
        with self.assertRaisesRegex(integration.LocalIntegrationError, "scanner_manifest_pin_mismatch"):
            wrong_profile = {**self.profile,
                             "image": "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:" + "9" * 64}
            integration.verify_oci_archive(self.archive, self.receipt, wrong_profile)

    def test_accepts_repository_profile_mapping_and_keeps_pin_binding(self):
        tracker_profile = integration.constants.profile_for("Tuinstra-DEV/tracker")
        with self.assertRaisesRegex(integration.LocalIntegrationError, "scanner_manifest_pin_mismatch"):
            integration.verify_oci_archive(self.archive, self.receipt, tracker_profile)

    def test_rejects_receipt_manifest_mismatch(self):
        self.write_receipt(oci_manifest_digest="sha256:" + "0" * 64)
        with self.assertRaisesRegex(integration.LocalIntegrationError, "receipt_manifest_mismatch"):
            self.verify()

    def test_rejects_loaded_config_id_mismatch(self):
        self.write_receipt(loaded_image_id="sha256:" + "0" * 64)
        with self.assertRaisesRegex(integration.LocalIntegrationError, "receipt_config_mismatch"):
            self.verify()

    def test_rejects_unclean_or_wrong_platform_receipt(self):
        for change in ({"source_worktree_clean": False}, {"platform": "linux/arm64"},
                       {"runtime_profile": "hosted-arm64"},
                       {"base_oci_config_digest": "sha256:" + "d" * 64},
                       {"base_registry_reference": "ghcr.io/tuinstra-dev/gate/php:latest"}):
            with self.subTest(change=change):
                self.write_receipt(**change)
                with self.assertRaises(integration.LocalIntegrationError):
                    self.verify()

    def test_rejects_unapproved_native_base_or_build_source(self):
        for change in ({"base_image_id": "sha256:" + "b" * 64},
                       {"source_commit": "f" * 40},
                       {"base_oci_manifest_digest": "sha256:" + "c" * 64}):
            with self.subTest(change=change):
                self.write_receipt(**change)
                with self.assertRaisesRegex(integration.LocalIntegrationError, "build_receipt_invalid"):
                    self.verify()

    def test_rejects_wrong_descriptor_platform(self):
        index = json.loads(self.read_index())
        index["manifests"][0]["platform"] = {"os": "linux", "architecture": "arm64"}
        self.write_archive(index_override=json.dumps(index).encode())
        self.write_receipt(artifact_sha256=digest(self.archive.read_bytes()),
                           oci_manifest_digest=self.manifest_digest, loaded_image_id=self.config_id)
        with self.assertRaises(integration.LocalIntegrationError):
            self.verify()

    def test_rejects_layer_blob_digest_mismatch(self):
        self.corrupt_layer_blob()
        self.write_receipt(artifact_sha256=digest(self.archive.read_bytes()))
        with self.assertRaisesRegex(integration.LocalIntegrationError, "oci_blob_digest_mismatch"):
            self.verify()

    def test_rejects_malformed_archive(self):
        self.archive.write_bytes(b"not an OCI archive")
        self.write_receipt(artifact_sha256=digest(self.archive.read_bytes()))
        with self.assertRaises(integration.LocalIntegrationError):
            self.verify()

    def test_rejects_symlink_archive_and_receipt(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.write_bytes(self.archive.read_bytes())
        self.archive.unlink()
        self.archive.symlink_to(elsewhere)
        with self.assertRaises(integration.LocalIntegrationError):
            self.verify()
        self.archive.unlink()
        self.archive.write_bytes(elsewhere.read_bytes())
        other_receipt = self.root / "receipt-copy.json"
        other_receipt.write_bytes(self.receipt.read_bytes())
        self.receipt.unlink()
        self.receipt.symlink_to(other_receipt)
        with self.assertRaises(integration.LocalIntegrationError):
            self.verify()

    def test_rejects_nonregular_archive_path(self):
        self.archive.unlink()
        self.archive.mkdir()
        with self.assertRaises(integration.LocalIntegrationError):
            self.verify()

    def test_rejects_duplicate_archive_members(self):
        self.write_archive(extra_members=[("index.json", b"{}")])
        self.write_receipt(artifact_sha256=digest(self.archive.read_bytes()))
        with self.assertRaises(integration.LocalIntegrationError):
            self.verify()

    def read_index(self):
        with tarfile.open(self.archive) as tar:
            return tar.extractfile("index.json").read()

    def corrupt_layer_blob(self):
        target = "blobs/sha256/" + hashlib.sha256(b"synthetic layer").hexdigest()
        entries = []
        with tarfile.open(self.archive) as tar:
            for member in tar:
                data = tar.extractfile(member).read() if member.isfile() else b""
                if member.name == target:
                    data = b"synthetic layeR"
                entries.append((member.name, member.type, member.mode, data))
        with tarfile.open(self.archive, "w") as tar:
            for name, kind, mode, data in entries:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.mode = mode
                if kind == tarfile.REGTYPE:
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                else:
                    tar.addfile(info)

    def docker_setup(self, reports):
        output = self.root / "docker-output"
        output.mkdir()
        calls = []

        def invoke(command, **kwargs):
            calls.append((command, kwargs))
            if command[1:3] == ["image", "inspect"]:
                return reports.get(command[3], (1, b""))
            return 0, b""

        return output, calls, invoke

    def configure_with_mock_docker(self, reports, repository="Tuinstra-DEV/gate"):
        output, calls, invoke = self.docker_setup(reports)
        profile = {**integration.constants.profile_for(repository), "image": self.image}
        with patch.object(integration.shutil, "which", return_value="/usr/bin/docker"), \
                patch.object(Path, "is_socket", return_value=True), \
                patch.dict(os.environ, {"DOCKER_HOST": "unix:///tmp/gate-test-docker.sock"}), \
                patch.object(integration.constants, "profile_for", return_value=profile), \
                patch.object(integration.source, "run", side_effect=invoke) as mocked_run:
            integration.configure_docker(output, self.archive, self.receipt, repository)
            configured_run = integration.source.run
            configured_run(["/usr/bin/docker", "create", self.image, self.image + ".suffix"])
            return calls, mocked_run.call_args_list

    def docker_inspect(self, image_id, *, os_name="linux", architecture="amd64", descriptor=None):
        fields = [image_id, os_name, architecture]
        if descriptor is not None:
            fields.append(json.dumps(descriptor))
        return (0, "|".join(fields).encode())

    def test_configure_uses_classic_docker_config_id(self):
        calls, invoked = self.configure_with_mock_docker({
            self.config_id: self.docker_inspect(self.config_id),
        })
        self.assertEqual(self.config_id, calls[0][0][3])
        self.assertEqual(self.config_id, invoked[-1].args[0][2])
        self.assertEqual(self.image + ".suffix", invoked[-1].args[0][3])

    def test_configure_selects_tracker_profile_by_exact_repository(self):
        descriptor = {"digest": self.manifest_digest,
                      "mediaType": "application/vnd.oci.image.manifest.v1+json"}
        calls, invoked = self.configure_with_mock_docker({
            self.config_id: (1, b""),
            self.manifest_digest: self.docker_inspect(self.manifest_digest, descriptor=descriptor),
        }, "Tuinstra-DEV/tracker")
        self.assertEqual([self.config_id, self.manifest_digest], [row[0][3] for row in calls[:2]])
        self.assertEqual(self.manifest_digest, invoked[-1].args[0][2])

    def test_configure_accepts_containerd_platform_manifest_id(self):
        descriptor = {"digest": self.manifest_digest,
                      "mediaType": "application/vnd.oci.image.manifest.v1+json"}
        calls, invoked = self.configure_with_mock_docker({
            self.config_id: (1, b""),
            self.manifest_digest: self.docker_inspect(self.manifest_digest, descriptor=descriptor),
        })
        self.assertEqual([self.config_id, self.manifest_digest], [row[0][3] for row in calls[:2]])
        self.assertEqual(self.manifest_digest, invoked[-1].args[0][2])

    def test_configure_rejects_wrong_docker_id(self):
        with self.assertRaises(integration.source.Failure):
            self.configure_with_mock_docker({
                self.config_id: self.docker_inspect("sha256:" + "9" * 64),
                self.manifest_digest: (1, b""),
            })

    def test_configure_rejects_wrong_manifest_descriptor(self):
        wrong = {"digest": "sha256:" + "9" * 64,
                 "mediaType": "application/vnd.oci.image.manifest.v1+json"}
        with self.assertRaises(integration.source.Failure):
            self.configure_with_mock_docker({
                self.config_id: (1, b""),
                self.manifest_digest: self.docker_inspect(self.manifest_digest, descriptor=wrong),
            })

    def test_configure_rejects_wrong_image_platform(self):
        descriptor = {"digest": self.manifest_digest,
                      "mediaType": "application/vnd.oci.image.manifest.v1+json"}
        with self.assertRaises(integration.source.Failure):
            self.configure_with_mock_docker({
                self.config_id: (1, b""),
                self.manifest_digest: self.docker_inspect(self.manifest_digest, architecture="arm64",
                                                          descriptor=descriptor),
            })


if __name__ == "__main__":
    unittest.main()
