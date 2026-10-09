#!/usr/bin/env python3
"""Run offline, synthetic-source Gate producer/publisher integration cases.

Requires Python 3.10+, Git, PHP with ZIP and the existing Gate backend's installed
Composer dependencies, and a local Docker Unix socket with the exact reviewed
scanner image already loaded. No image pulls, registry/API requests, credentials,
real application checkouts, or database access are used. The Git object fixtures
contain only generated source. --output-dir must be a new dedicated directory;
case evidence is retained there and results.json records compact outcomes.
The verified-absence case requires the publisher correction covered by this proof.
"""
import argparse
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import stat
import sys
import tarfile
import uuid
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/actions/gate-pr-security"))
import constants
import evidence
import source

BRIDGE = ROOT / "tests/fixtures/gate-pr-security/publisher-bridge.php"
CONTEXT = {
    "GATE_WORKFLOW_REF": evidence.APPROVED_WORKFLOW_PATH + "@" + "a" * 40,
    "GATE_WORKFLOW_SHA": "a" * 40, "GATE_CHECK_RUN_ID": "80",
    "GATE_EVENT_NAME": "pull_request_target", "GATE_EXECUTION_REF": "refs/heads/main",
    "GATE_EXECUTION_SHA": "d" * 40,
}
EXCLUSION_CASES = {"approved-exclusions", "changed-exclusion", "changed-braces-exclusion", "removed-exclusions", "blocked-with-exclusions"}
OCI_ARCHIVE_MAX_BYTES = 4 * 1024 * 1024 * 1024
OCI_RECEIPT_MAX_BYTES = 64 * 1024
OCI_METADATA_MAX_BYTES = 4 * 1024 * 1024
OCI_MAX_MEMBERS = 50000
NATIVE_BASE_IMAGE_ID = "sha256:233fb39e26c4560a390d89e7068761451d02286cdddc7914d47cbd1d190edc11"
NATIVE_BASE_REFERENCE = "ghcr.io/tuinstra-dev/gate/php@sha256:85d0c3c875740254f6ed86b3eb20001fe4697716b909c622f1ef1dfae8658ec6"
NATIVE_SOURCE_COMMIT = "0c9eabe7475c588f2c85521346dcf3e26a618273"
BASE_FILES = {
    "safe.php": b'<?php $stmt=$PDO->prepare("SELECT name FROM users WHERE id = ?"); $stmt->execute([$id]);\n',
    "safe.js": b'export function display(req) { return String(req.query.value); }\n',
    "composer.lock": json.dumps({"packages": [{"name": "twig/twig", "version": "v3.27.0"}], "packages-dev": []}).encode(),
    "package-lock.json": json.dumps({"name": "fixture", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
        "packages": {"": {"name": "fixture", "version": "1.0.0"}, "node_modules/left-pad": {"version": "1.3.0"}}}).encode(),
    "README.md": b"Synthetic DEV-46 integration fixture.\n",
}
COMPOSER_MANIFEST = {"name": "fixture/app", "require": {"symfony/framework-bundle": "^7.4"}}
COMPOSER_HASH = hashlib.md5(json.dumps(COMPOSER_MANIFEST, sort_keys=True, separators=(",", ":")).replace("/", "\\/").encode()).hexdigest()
BASE_FILES.update({
    "composer.json": json.dumps(COMPOSER_MANIFEST).encode(),
    "composer.lock": json.dumps({"content-hash": COMPOSER_HASH, "packages": [
        {"name": "symfony/framework-bundle", "version": "v7.4.0"},
        {"name": "twig/twig", "version": "v3.27.0"}], "packages-dev": []}).encode(),
    "pnpm-app/package.json": b'{"dependencies":{"left-pad":"1.3.0"}}',
    "pnpm-app/pnpm-lock.yaml": b"lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n      left-pad:\n        specifier: 1.3.0\n        version: 1.3.0\npackages:\n  left-pad@1.3.0:\n    resolution:\n      integrity: sha512-QUFBQQ==\nsnapshots:\n  left-pad@1.3.0: {}\n",
    "component.vue": b'<template><div>{{ value }}</div></template>\n<script setup lang="ts">const value = "safe"</script>\n',
    ".github/workflows/checks.yml": b"name: Synthetic checks\non: [pull_request]\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: []\n",
    "config.json": b'{"tls_verify":true}',
    "Dockerfile": b"FROM scratch\nCOPY app /app\n",
    "styles.css": b"body { color: black; }\n",
    "page.twig": b"{{ title }}\n",
    "Makefile": b"check:\n\techo synthetic\n",
})


def fixture_tree(store, files):
    nested = {}
    for path, body in files.items():
        cursor = nested
        parts = path.split("/")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = body

    def tree(entries):
        rows = []
        for name, value in sorted(entries.items()):
            if isinstance(value, dict):
                rows.append(b"040000 tree " + tree(value) + b"\t" + name.encode() + b"\0")
            else:
                blob = source.git(store, ["hash-object", "-w", "--stdin"], data=value).strip()
                rows.append(b"100644 blob " + blob + b"\t" + name.encode() + b"\0")
        return source.git(store, ["mktree", "-z"], data=b"".join(rows)).strip()

    return tree(nested).decode()



def fixture(work, head_files, base_files, repository):
    work.mkdir(mode=0o700)
    marker = {"version": 1, "id": uuid.uuid4().hex, "path": str(work), "uid": os.getuid()}
    (work / ".gate-work.json").write_text(json.dumps(marker), encoding="utf-8")
    store = work / "objects.git"
    code, _ = source.run([source.GIT, "init", "--bare", "--template=", str(store)])
    source.require(code == 0, "fixture_init_failed")
    git_env = source.clean_env() | {
        "GIT_AUTHOR_NAME": "Synthetic fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2026-09-27T00:00:00Z", "GIT_COMMITTER_DATE": "2026-09-27T00:00:00Z",
    }
    repository_ids = {"Tuinstra-DEV/gate": "42", "Tuinstra-DEV/tracker": "43"}
    source.require(repository in repository_ids, "unknown_repository")
    state = {"repository": repository, "repository_id": repository_ids[repository], "owner_id": "12", "pull_request": 7,
        "run_id": "900", "run_attempt": 2, "prepare_duration_ms": 0}
    for side, files in [("base", base_files), ("head", head_files)]:
        tree = fixture_tree(store, files)
        commit = source.git(store, ["commit-tree", tree], env=git_env, data=b"DEV-46 synthetic source\n").decode().strip()
        state[side + "_sha"], state[side + "_tree"] = commit, tree
        source.materialize(store, commit, work / side)
    for path in store.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    store.chmod(0o555)
    (work / "output").mkdir(mode=0o700)
    source.save_state(work, state)


def safe_reason(error):
    reason = str(error) if isinstance(error, (LocalIntegrationError, source.Failure, evidence.EvidenceError)) else "local_integration_failed"
    return reason if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason) else "local_integration_failed"


class LocalIntegrationError(Exception):
    """A safe, stable local verification failure code."""


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", value) is not None


def _read_regular(path, maximum, reason):
    stream = _open_regular(path, maximum, reason)
    try:
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > maximum:
            raise LocalIntegrationError("unsafe_or_oversized_" + reason)
        return raw
    finally:
        stream.close()


def _open_regular(path, maximum, reason):
    path = Path(path)
    descriptor = None
    try:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise LocalIntegrationError("nofollow_open_unavailable")
        flags = os.O_RDONLY | nofollow
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size < 1 or metadata.st_size > maximum:
            raise LocalIntegrationError("unsafe_or_oversized_" + reason)
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        return stream
    except LocalIntegrationError:
        raise
    except OSError:
        raise LocalIntegrationError("unreadable_" + reason) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _json_object(raw, reason):
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise LocalIntegrationError(reason) from None
    if not isinstance(value, dict):
        raise LocalIntegrationError(reason)
    return value


def _verify_blob(tar, members, descriptor, expected_media_type, *, maximum=None):
    if not isinstance(descriptor, dict) or descriptor.get("mediaType") != expected_media_type:
        raise LocalIntegrationError("oci_descriptor_invalid")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if not _digest(digest) or type(size) is not int or size < 1:
        raise LocalIntegrationError("oci_descriptor_invalid")
    name = "blobs/sha256/" + digest.split(":", 1)[1]
    member = members.get(name)
    if member is None or member.size != size or (maximum is not None and size > maximum):
        raise LocalIntegrationError("oci_blob_missing_or_oversized")
    stream = tar.extractfile(member)
    if stream is None:
        raise LocalIntegrationError("oci_blob_unreadable")
    hasher = hashlib.sha256()
    raw = bytearray() if maximum is not None else None
    total = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        hasher.update(chunk)
        if raw is not None:
            raw.extend(chunk)
    if total != size or "sha256:" + hasher.hexdigest() != digest:
        raise LocalIntegrationError("oci_blob_digest_mismatch")
    return bytes(raw) if raw is not None else None


def verify_oci_archive(archive_path, receipt_path, profile):
    """Bind a clean-source build receipt, OCI manifest, pinned ref, and loaded config ID."""
    if not isinstance(profile, Mapping) or profile.get("platform") != "linux/amd64" or \
            not isinstance(profile.get("image"), str):
        raise LocalIntegrationError("scanner_profile_invalid")
    image_ref = profile.get("image")
    receipt = _json_object(_read_regular(receipt_path, OCI_RECEIPT_MAX_BYTES, "build_receipt"),
                           "build_receipt_invalid")
    required = {"artifact", "artifact_sha256", "oci_manifest_digest", "loaded_image_id",
                "loaded_image_size_bytes", "base_image_id", "base_oci_manifest_digest",
                "base_oci_config_digest", "base_registry_reference", "runtime_profile",
                "platform", "runner_source_manifest_sha256", "runner_source_file_count",
                "source_commit", "source_worktree_clean"}
    if not required.issubset(receipt) or not isinstance(receipt["artifact"], str) or not receipt["artifact"]:
        raise LocalIntegrationError("build_receipt_invalid")
    if receipt["source_worktree_clean"] is not True or receipt["platform"] != profile["platform"]:
        raise LocalIntegrationError("build_source_or_platform_invalid")
    if (not re.fullmatch(r"[a-f0-9]{40}", str(receipt["source_commit"]))
            or not _digest(receipt["artifact_sha256"]) or not _digest(receipt["oci_manifest_digest"])
            or not _digest(receipt["loaded_image_id"]) or not _digest(receipt["base_image_id"])
            or receipt["base_image_id"] != NATIVE_BASE_IMAGE_ID
            or receipt["runtime_profile"] != "native-amd64"
            or receipt["base_registry_reference"] != NATIVE_BASE_REFERENCE
            or receipt["source_commit"] != NATIVE_SOURCE_COMMIT
            or receipt["base_oci_manifest_digest"] is not None
            or receipt["base_oci_config_digest"] is not None
            or not _digest(receipt["runner_source_manifest_sha256"])
            or type(receipt["runner_source_file_count"]) is not int or receipt["runner_source_file_count"] < 1
            or type(receipt["loaded_image_size_bytes"]) is not int or receipt["loaded_image_size_bytes"] < 1):
        raise LocalIntegrationError("build_receipt_invalid")
    if not re.fullmatch(r"ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}", image_ref):
        raise LocalIntegrationError("invalid_scanner_pin")

    archive_file = _open_regular(archive_path, OCI_ARCHIVE_MAX_BYTES, "oci_archive")
    try:
        hasher = hashlib.sha256()
        while True:
            chunk = archive_file.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
        if "sha256:" + hasher.hexdigest() != receipt["artifact_sha256"]:
            raise LocalIntegrationError("receipt_archive_mismatch")
        archive_file.seek(0)
        with tarfile.open(fileobj=archive_file, mode="r:") as tar:
            members = {}
            seen_names = set()
            entry_count = 0
            for member in tar:
                entry_count += 1
                name = member.name
                if (entry_count > OCI_MAX_MEMBERS or not name or name.startswith("/")
                        or "\\" in name or name in seen_names):
                    raise LocalIntegrationError("oci_archive_invalid")
                seen_names.add(name)
                normalized = posixpath.normpath(name.rstrip("/"))
                if normalized in {".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
                    raise LocalIntegrationError("oci_archive_invalid")
                if member.isdir():
                    if normalized not in {"blobs", "blobs/sha256"} or name.rstrip("/") != normalized:
                        raise LocalIntegrationError("oci_archive_invalid")
                    continue
                if not member.isfile() or normalized != name:
                    raise LocalIntegrationError("oci_archive_invalid")
                if name not in {"oci-layout", "index.json"} and not re.fullmatch(r"blobs/sha256/[a-f0-9]{64}", name):
                    raise LocalIntegrationError("oci_archive_invalid")
                members[name] = member
            layout_member = members.get("oci-layout")
            index_member = members.get("index.json")
            if layout_member is None or index_member is None or layout_member.size > 4096:
                raise LocalIntegrationError("oci_layout_invalid")
            layout = _json_object(tar.extractfile(layout_member).read(4097), "oci_layout_invalid")
            if layout.get("imageLayoutVersion") != "1.0.0":
                raise LocalIntegrationError("oci_layout_invalid")
            if index_member.size > OCI_METADATA_MAX_BYTES:
                raise LocalIntegrationError("oci_index_invalid")
            index = _json_object(tar.extractfile(index_member).read(OCI_METADATA_MAX_BYTES + 1), "oci_index_invalid")
            descriptors = index.get("manifests")
            if index.get("schemaVersion") != 2 or not isinstance(descriptors, list) or len(descriptors) != 1:
                raise LocalIntegrationError("oci_index_invalid")
            descriptor = descriptors[0]
            platform = descriptor.get("platform") if isinstance(descriptor, dict) else None
            if (not isinstance(platform, dict) or platform.get("os") != "linux"
                    or platform.get("architecture") != "amd64"
                    or set(platform) - {"os", "architecture", "variant"}
                    or platform.get("variant") is not None):
                raise LocalIntegrationError("oci_platform_mismatch")
            manifest_digest = descriptor.get("digest")
            manifest_raw = _verify_blob(tar, members, descriptor,
                                        "application/vnd.oci.image.manifest.v1+json", maximum=OCI_METADATA_MAX_BYTES)
            manifest = _json_object(manifest_raw, "oci_manifest_invalid")
            if manifest.get("schemaVersion") != 2:
                raise LocalIntegrationError("oci_manifest_invalid")
            config_raw = _verify_blob(tar, members, manifest.get("config"),
                                      "application/vnd.oci.image.config.v1+json", maximum=OCI_METADATA_MAX_BYTES)
            config_digest = manifest["config"]["digest"]
            config = _json_object(config_raw, "oci_config_invalid")
            if config.get("os") != "linux" or config.get("architecture") != "amd64":
                raise LocalIntegrationError("oci_platform_mismatch")
            layers = manifest.get("layers")
            if not isinstance(layers, list):
                raise LocalIntegrationError("oci_manifest_invalid")
            for layer in layers:
                if not isinstance(layer, dict) or not isinstance(layer.get("mediaType"), str):
                    raise LocalIntegrationError("oci_descriptor_invalid")
                _verify_blob(tar, members, layer, layer["mediaType"])
    except LocalIntegrationError:
        raise
    except (tarfile.TarError, OSError, KeyError, TypeError, ValueError):
        raise LocalIntegrationError("oci_archive_invalid") from None
    finally:
        archive_file.close()

    pin_digest = image_ref.split("@", 1)[1]
    if manifest_digest != receipt["oci_manifest_digest"]:
        raise LocalIntegrationError("receipt_manifest_mismatch")
    if config_digest != receipt["loaded_image_id"]:
        raise LocalIntegrationError("receipt_config_mismatch")
    if manifest_digest != pin_digest:
        raise LocalIntegrationError("scanner_manifest_pin_mismatch")
    return config_digest, manifest_digest


def configure_docker(output, oci_archive=None, build_receipt=None, repository="Tuinstra-DEV/gate"):
    try:
        profile = constants.profile_for(repository)
    except ValueError:
        raise LocalIntegrationError("unknown_repository") from None
    image_ref = profile["image"]
    platform = profile["platform"]
    source.require(platform == "linux/amd64", "scanner_profile_invalid")
    docker = shutil.which("docker")
    source.require(docker is not None, "docker_unavailable")
    host = os.environ.get("DOCKER_HOST")
    if not host:
        context_env = source.clean_env() | {"HOME": str(Path.home())}
        if os.environ.get("DOCKER_CONTEXT"):
            context_env["DOCKER_CONTEXT"] = os.environ["DOCKER_CONTEXT"]
        code, raw = source.run([docker, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                              env=context_env, max_bytes=4096)
        source.require(code == 0, "docker_context_unavailable")
        host = raw.decode().strip()
    source.require(host.startswith("unix://") and Path(host[7:]).is_absolute()
                   and Path(host[7:]).is_socket(), "local_unix_docker_socket_required")
    source.require(re.fullmatch(r"ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}", image_ref), "invalid_scanner_pin")
    source.require((oci_archive is None) == (build_receipt is None), "oci_archive_receipt_pair_required")
    verified_ids = verify_oci_archive(oci_archive, build_receipt, profile) if oci_archive else None
    empty_config = output / "empty-docker-config"
    empty_config.mkdir(mode=0o700)
    docker_env = source.clean_env() | {"DOCKER_HOST": host, "DOCKER_CONFIG": str(empty_config)}
    original_run = source.run
    if verified_ids:
        config_id, manifest_id = verified_ids
        accepted_id = None
        for candidate, is_manifest in ((config_id, False), (manifest_id, True)):
            image_format = "{{.Id}}|{{.Os}}|{{.Architecture}}|{{json .Descriptor}}" if is_manifest else "{{.Id}}|{{.Os}}|{{.Architecture}}"
            code, raw = original_run([docker, "image", "inspect", candidate, "--format", image_format],
                                     env=docker_env, max_bytes=4096)
            try:
                fields = raw.decode().strip().split("|")
                image_id, image_os, image_arch = fields[:3]
                descriptor = json.loads(fields[3]) if is_manifest and len(fields) == 4 else None
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                image_id, image_os, image_arch, descriptor = "", "", "", None
            if (code != 0 or image_id != candidate or image_os != "linux" or image_arch != "amd64"
                    or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None):
                continue
            if is_manifest and (not isinstance(descriptor, dict) or descriptor.get("digest") != manifest_id
                                or descriptor.get("mediaType") != "application/vnd.oci.image.manifest.v1+json"):
                continue
            accepted_id = candidate
            break
        source.require(accepted_id is not None, "reviewed_scanner_image_not_loaded")
        image = accepted_id
    else:
        code, raw = original_run([docker, "image", "inspect", image_ref,
                                  "--format", "{{.Id}}|{{.Os}}|{{.Architecture}}|{{json .RepoDigests}}"],
                                 env=docker_env, max_bytes=4096)
        try:
            image_id, image_os, image_arch, repo_digests = raw.decode().strip().split("|", 3)
            repo_digests = json.loads(repo_digests)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            image_id, image_os, image_arch, repo_digests = "", "", "", []
        if not isinstance(repo_digests, list):
            repo_digests = []
        source.require(code == 0 and re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is not None
                       and image_os == "linux" and image_arch == "amd64"
                       and image_ref in repo_digests, "reviewed_scanner_image_not_loaded")
        image = image_ref
    source.DOCKER = docker

    def local_run(command, **kwargs):
        if command[0] == docker:
            command = [image if value == image_ref else value for value in command]
            kwargs["env"] = docker_env
        return original_run(command, **kwargs)

    source.run = local_run


def run_case(name, expected, head_files, base_files, output, backend, php, repository):
    work = output / name
    try:
        fixture(work, head_files, base_files, repository)
        source.scan(work)
        report = json.loads((work / "output/pair/result.json").read_text(encoding="utf-8"))
        staging = evidence.package(work, CONTEXT)
        archive = work / "publisher-evidence.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zipped:
            for entry in ["evidence.json", "reports/result.json"]:
                zipped.write(staging / entry, entry)
        profile = "exclusions" if name in EXCLUSION_CASES else "ordinary"
        code, raw = source.run([php, str(BRIDGE), str(backend), str(work / "state.json"), str(archive), expected, repository, profile],
                               max_bytes=4096)
        bridge = json.loads(raw)
        source.require(isinstance(bridge, dict), "invalid_bridge_result")
        if bridge.get("ok") is True and bridge.get("outcome") in ("pass", "blocked", "incomplete"):
            safe_bridge = {"ok": True, "outcome": bridge["outcome"]}
            for key in ("scannedInputCount", "excludedInputCount"):
                value = bridge.get(key)
                source.require(type(value) is int and value >= 0, "invalid_bridge_counts")
                safe_bridge[key] = value
        else:
            reason = bridge.get("reason", "invalid_bridge_result")
            source.require(isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason), "invalid_bridge_result")
            safe_bridge = {"ok": False, "reason": reason}
        return {"case": name, "ok": code == 0 and safe_bridge.get("ok") is True
                and safe_bridge.get("outcome") == expected and report["outcome"] == expected,
                "expected": expected, "scanner_outcome": report["outcome"],
                "publisher_bridge": safe_bridge, "bridge_exit": code,
                "findings": len(report["findings"]), "coverage_scopes": [row["scope"] for row in report["coverage"]],
                "raw_report_sha256": "sha256:" + hashlib.sha256((work / "output/pair/result.json").read_bytes()).hexdigest(),
                "archive_sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
                "assurance_version": json.loads((staging / "evidence.json").read_bytes())["schema_version"],
                "exclusion_records": len(json.loads((staging / "evidence.json").read_bytes())["exclusions"])}
    except Exception as error:
        return {"case": name, "ok": False, "reason": safe_reason(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gate-backend", type=Path, required=True, help="Existing trusted Gate backend directory with vendor/autoload.php; never cloned.")
    parser.add_argument("--output-dir", type=Path, required=True, help="New dedicated directory for synthetic fixtures and results.json (must not exist).")
    parser.add_argument("--case", action="append", choices=["pass", "blocked", "incomplete", "verified-absence", "existing-debt", "unsupported-yarn", "unknown-input", *sorted(EXCLUSION_CASES)], help="Run selected synthetic scenarios; repeat to select more than one.")
    parser.add_argument("--repository", choices=sorted(constants.PROFILES), default="Tuinstra-DEV/gate",
                        help="Exact repository profile to exercise (Gate or Tracker).")
    parser.add_argument("--scanner-oci-archive", type=Path, help="Exact local OCI archive already loaded into Docker; requires its build receipt.")
    parser.add_argument("--scanner-build-receipt", type=Path, help="Build receipt corresponding to --scanner-oci-archive.")
    args = parser.parse_args()
    results = []
    output = None
    try:
        source.require((args.scanner_oci_archive is None) == (args.scanner_build_receipt is None),
                       "oci_archive_receipt_pair_required")
        backend = args.gate_backend.resolve(strict=True)
        source.require((backend / "vendor/autoload.php").is_file(), "gate_dependencies_unavailable")
        php = shutil.which("php")
        source.require(php is not None, "php_unavailable")
        candidate = args.output_dir.absolute()
        source.require(not candidate.exists() and not candidate.is_symlink(), "output_directory_already_exists")
        candidate.mkdir(mode=0o700, parents=True)
        output = candidate.resolve()
        profile = constants.profile_for(args.repository)
        configure_docker(output, args.scanner_oci_archive, args.scanner_build_receipt, args.repository)
        debt = dict(BASE_FILES, **{"legacy.php": b'<?php eval($_GET["legacy"]);\n'})
        excluded = dict(BASE_FILES, **{
            "backend/src/Controller/.placeholder": b"",
            "frontend/public/favicon-16x16.png": (backend.parent / "frontend/public/favicon-16x16.png").read_bytes(),
            "scripts/test-php-runtime.sh": (backend.parent / "scripts/test-php-runtime.sh").read_bytes(),
            "frontend/patches/braces@3.0.3.patch": (backend.parent / "frontend/patches/braces@3.0.3.patch").read_bytes(),
        })
        cases = [
            ("pass", "pass", dict(BASE_FILES), BASE_FILES),
            ("blocked", "blocked", dict(BASE_FILES, **{"unsafe.php": b'<?php eval($_GET["input"]);\n'}), BASE_FILES),
            ("incomplete", "incomplete", dict(BASE_FILES, **{".gitleaksignore": b"untrusted-ignore-fixture\n"}), BASE_FILES),
            ("verified-absence", "pass", {key: value for key, value in BASE_FILES.items() if key != "safe.php"}, BASE_FILES),
            ("existing-debt", "pass", dict(debt, **{"README.md": b"Synthetic documentation update.\n"}), debt),
            ("unsupported-yarn", "incomplete", dict(BASE_FILES, **{"yarn.lock": b"# Synthetic unsupported Yarn lock\n"}), BASE_FILES),
            ("unknown-input", "incomplete", dict(BASE_FILES, **{"unclassified.payload": b"Synthetic unknown input.\n"}), BASE_FILES),
            ("approved-exclusions", "pass", dict(excluded), excluded),
            ("changed-exclusion", "incomplete", dict(excluded, **{"frontend/public/favicon-16x16.png": excluded["frontend/public/favicon-16x16.png"] + b"changed"}), excluded),
            ("changed-braces-exclusion", "incomplete", dict(excluded, **{"frontend/patches/braces@3.0.3.patch": excluded["frontend/patches/braces@3.0.3.patch"] + b"changed"}), excluded),
            ("removed-exclusions", "pass", dict(BASE_FILES), excluded),
            ("blocked-with-exclusions", "blocked", dict(excluded, **{"unsafe.php": b'<?php eval($_GET["input"]);\n'}), excluded),
        ]
        requested = set(args.case or ())
        if args.repository == "Tuinstra-DEV/tracker" and requested & EXCLUSION_CASES:
            raise LocalIntegrationError("profile_exclusions_unsupported")
        selected = [case for case in cases if (not requested or case[0] in requested)
                    and (args.repository != "Tuinstra-DEV/tracker" or case[0] not in EXCLUSION_CASES)]
        results = [run_case(name, expected, files, base, output, backend, php, args.repository) for name, expected, files, base in selected]
        summary = {"synthetic": True, "proof_scope": "synthetic-common-source-scopes", "repository": args.repository, "scanner_image": profile["image"],
                   "policy_digest": profile["policy_digest"], "bundle_digest": profile["bundle_digest"],
                   "ok": all(result["ok"] for result in results), "results": results}
    except Exception as error:
        summary = {"synthetic": True, "ok": False, "reason": safe_reason(error), "results": results}
    if output is not None:
        (output / "results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
