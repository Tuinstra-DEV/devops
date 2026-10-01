#!/usr/bin/env python3
"""Run offline, synthetic-source DEV-49 producer/publisher integration cases.

Requires Python 3.10+, Git, PHP with ZIP and the existing Gate backend's installed
Composer dependencies, and a local Docker Unix socket with the exact reviewed
scanner image already loaded. No image pulls, registry/API requests, credentials,
real application checkouts, or database access are used. The Git object fixtures
contain only generated source. --output-dir must be a new dedicated directory;
case evidence is retained there and results.json records compact outcomes.
The verified-absence case requires the publisher correction covered by this proof.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
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



def fixture(work, head_files, base_files):
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
    state = {"repository": "Tuinstra-DEV/gate", "repository_id": "42", "owner_id": "12", "pull_request": 7,
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
    reason = str(error) if isinstance(error, (source.Failure, evidence.EvidenceError)) else "local_integration_failed"
    return reason if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason) else "local_integration_failed"


def configure_docker(output):
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
    source.require(re.fullmatch(r"ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}", constants.IMAGE), "invalid_scanner_pin")
    image = constants.IMAGE.split("@", 1)[1]
    empty_config = output / "empty-docker-config"
    empty_config.mkdir(mode=0o700)
    docker_env = source.clean_env() | {"DOCKER_HOST": host, "DOCKER_CONFIG": str(empty_config)}
    original_run = source.run
    code, raw = original_run([docker, "image", "inspect", image, "--format", "{{.Id}}"],
                             env=docker_env, max_bytes=4096)
    source.require(code == 0 and raw.decode().strip() == image, "reviewed_scanner_image_not_loaded")
    source.DOCKER = docker

    def local_run(command, **kwargs):
        if command[0] == docker:
            command = [image if value == constants.IMAGE else value for value in command]
            kwargs["env"] = docker_env
        return original_run(command, **kwargs)

    source.run = local_run


def run_case(name, expected, head_files, base_files, output, backend, php):
    work = output / name
    try:
        fixture(work, head_files, base_files)
        source.scan(work)
        report = json.loads((work / "output/pair/result.json").read_text(encoding="utf-8"))
        staging = evidence.package(work, CONTEXT)
        archive = work / "publisher-evidence.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zipped:
            for entry in ["evidence.json", "reports/result.json"]:
                zipped.write(staging / entry, entry)
        code, raw = source.run([php, str(BRIDGE), str(backend), str(work / "state.json"), str(archive), expected],
                               max_bytes=4096)
        bridge = json.loads(raw)
        source.require(isinstance(bridge, dict), "invalid_bridge_result")
        if bridge.get("ok") is True and bridge.get("outcome") in ("pass", "blocked", "incomplete"):
            safe_bridge = {"ok": True, "outcome": bridge["outcome"]}
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
                "archive_sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()}
    except Exception as error:
        return {"case": name, "ok": False, "reason": safe_reason(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gate-backend", type=Path, required=True, help="Existing trusted Gate backend directory with vendor/autoload.php; never cloned.")
    parser.add_argument("--output-dir", type=Path, required=True, help="New dedicated directory for synthetic fixtures and results.json (must not exist).")
    parser.add_argument("--case", action="append", choices=["pass", "blocked", "incomplete", "verified-absence", "existing-debt", "unsupported-yarn", "unknown-input"], help="Run selected synthetic scenarios; repeat to select more than one.")
    args = parser.parse_args()
    results = []
    output = None
    try:
        backend = args.gate_backend.resolve(strict=True)
        source.require((backend / "vendor/autoload.php").is_file(), "gate_dependencies_unavailable")
        php = shutil.which("php")
        source.require(php is not None, "php_unavailable")
        candidate = args.output_dir.absolute()
        source.require(not candidate.exists() and not candidate.is_symlink(), "output_directory_already_exists")
        candidate.mkdir(mode=0o700, parents=True)
        output = candidate.resolve()
        configure_docker(output)
        debt = dict(BASE_FILES, **{"legacy.php": b'<?php eval($_GET["legacy"]);\n'})
        cases = [
            ("pass", "pass", dict(BASE_FILES), BASE_FILES),
            ("blocked", "blocked", dict(BASE_FILES, **{"unsafe.php": b'<?php eval($_GET["input"]);\n'}), BASE_FILES),
            ("incomplete", "incomplete", dict(BASE_FILES, **{".gitleaksignore": b"untrusted-ignore-fixture\n"}), BASE_FILES),
            ("verified-absence", "pass", {key: value for key, value in BASE_FILES.items() if key != "safe.php"}, BASE_FILES),
            ("existing-debt", "pass", dict(debt, **{"README.md": b"Synthetic documentation update.\n"}), debt),
            ("unsupported-yarn", "incomplete", dict(BASE_FILES, **{"yarn.lock": b"# Synthetic unsupported Yarn lock\n"}), BASE_FILES),
            ("unknown-input", "incomplete", dict(BASE_FILES, **{"unclassified.payload": b"Synthetic unknown input.\n"}), BASE_FILES),
        ]
        selected = [case for case in cases if not args.case or case[0] in args.case]
        results = [run_case(name, expected, files, base, output, backend, php) for name, expected, files, base in selected]
        summary = {"synthetic": True, "scanner_image": constants.IMAGE, "policy_digest": constants.POLICY_DIGEST, "bundle_digest": constants.BUNDLE_DIGEST, "ok": all(result["ok"] for result in results), "results": results}
    except Exception as error:
        summary = {"synthetic": True, "ok": False, "reason": safe_reason(error), "results": results}
    if output is not None:
        (output / "results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
