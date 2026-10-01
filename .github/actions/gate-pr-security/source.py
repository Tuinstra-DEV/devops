#!/usr/bin/env python3
"""Trusted source preparation and offline scan; application files are data only."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from constants import IMAGE

API = "https://api.github.com"
GIT = "/usr/bin/git"
DOCKER = "/usr/bin/docker"
SHA = re.compile(r"[a-f0-9]{40}\Z")
MAX_FILES = 20000
MAX_FILE = 4194304
MAX_SOURCE = 134217728
MAX_REPORT = 8 * 1024 * 1024
MAX_OBJECT_STORE = 256 * 1024 * 1024


class Failure(Exception):
    """Only fixed, non-sensitive reason codes may leave this module."""


def require(condition, reason):
    if not condition:
        raise Failure(reason)


def clean_env():
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0",
    }


def storage_bytes(root):
    total = 0
    for item in root.rglob("*"):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            # Git may atomically replace a temporary pack between polls.
            continue
        if stat.S_ISREG(metadata.st_mode):
            total += metadata.st_size
    return total


def run(command, *, env=None, timeout=60, max_bytes=16 * 1024 * 1024, data=b"", storage_root=None):
    """Spool bounded output, never print it, and terminate the process group."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as inp:
        inp.write(data)
        inp.seek(0)
        try:
            process = subprocess.Popen(command, stdin=inp, stdout=out, stderr=err,
                                       env=env or clean_env(), start_new_session=True)
        except OSError:
            raise Failure("process_unavailable") from None
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                require(time.monotonic() < deadline, "process_timeout")
                require(os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size <= max_bytes,
                        "process_output_limit")
                if storage_root is not None:
                    require(storage_bytes(storage_root)
                            <= MAX_OBJECT_STORE, "object_store_limit")
                time.sleep(0.025)
            require(os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size <= max_bytes,
                    "process_output_limit")
            if storage_root is not None:
                require(storage_bytes(storage_root)
                        <= MAX_OBJECT_STORE, "object_store_limit")
            out.seek(0)
            return process.returncode, out.read()
        finally:
            # Also kills children if the direct process exited first.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def git(store, arguments, *, env=None, data=b"", max_bytes=16 * 1024 * 1024, timeout=60, storage_root=None):
    code, output = run([
        GIT, "--no-pager", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false", "-c", "credential.helper=", "-c", "protocol.file.allow=never",
        "-c", "protocol.ext.allow=never", "-c", "http.followRedirects=false",
        "-c", "fetch.fsckObjects=true", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
        "--git-dir=" + str(store), *arguments,
    ], env=env, data=data, max_bytes=max_bytes, timeout=timeout, storage_root=storage_root)
    require(code == 0, "git_failed")
    return output


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(path, token):
    require(path.startswith("/repos/"), "invalid_api_path")
    request = urllib.request.Request(API + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Gate-PR-Security",
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            require(response.status == 200, "github_unavailable")
            body = response.read(1048577)
        require(len(body) <= 1048576, "github_response_limit")
        value = decode_json(body)
        require(isinstance(value, dict), "github_response_invalid")
        return value
    except (OSError, urllib.error.URLError, ValueError):
        raise Failure("github_unavailable") from None


def decode_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Failure("invalid_json")))
    except (ValueError, UnicodeError):
        raise Failure("invalid_json") from None


def decimal(value):
    require(isinstance(value, (str, int)) and not isinstance(value, bool), "invalid_identity")
    value = str(value)
    require(re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None, "invalid_identity")
    return value


def sha(value):
    require(isinstance(value, str) and SHA.fullmatch(value), "invalid_sha")
    return value


def provider_state(environ):
    require(environ.get("GATE_EVENT_NAME") == "pull_request_target", "unsupported_event")
    repository = environ.get("GATE_REPOSITORY", "")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", repository),
            "invalid_repository")
    state = {"repository": repository}
    for field, name in [("repository_id", "REPOSITORY_ID"), ("owner_id", "OWNER_ID"),
                        ("run_id", "RUN_ID"), ("pull_request", "PR_NUMBER"), ("run_attempt", "RUN_ATTEMPT")]:
        state[field] = decimal(environ.get("GATE_" + name, ""))
    state["pull_request"] = int(state["pull_request"])
    state["run_attempt"] = int(state["run_attempt"])
    require(state["pull_request"] <= 2147483647 and state["run_attempt"] <= 1000000, "invalid_identity")
    token = environ.get("GITHUB_TOKEN", "")
    require(bool(token) and "\n" not in token and "\r" not in token, "github_token_missing")
    prefix = "/repos/" + repository
    repo = api(prefix, token)
    require(decimal(repo.get("id")) == state["repository_id"]
            and decimal(repo.get("owner", {}).get("id")) == state["owner_id"]
            and repo.get("full_name", "").lower() == repository.lower(), "repository_mismatch")
    pr = api(prefix + "/pulls/" + str(state["pull_request"]), token)
    require(pr.get("number") == state["pull_request"] and pr.get("state") == "open"
            and pr.get("merged") is False, "pr_not_current")
    base, head = pr.get("base", {}), pr.get("head", {})
    require(decimal(base.get("repo", {}).get("id")) == state["repository_id"]
            and decimal(base.get("repo", {}).get("owner", {}).get("id")) == state["owner_id"], "pr_repository_mismatch")
    state["base_sha"], state["head_sha"] = sha(base.get("sha")), sha(head.get("sha"))
    head_repository = decimal(head.get("repo", {}).get("id"))
    run_path = prefix + "/actions/runs/" + state["run_id"]
    for current in [api(run_path, token), api(run_path + "/attempts/" + str(state["run_attempt"]), token)]:
        require(decimal(current.get("id")) == state["run_id"]
                and current.get("run_attempt") == state["run_attempt"]
                and current.get("event") == "pull_request_target"
                and current.get("head_sha") == state["head_sha"]
                and decimal(current.get("repository", {}).get("id")) == state["repository_id"]
                and decimal(current.get("head_repository", {}).get("id")) == head_repository, "run_mismatch")
        associations = current.get("pull_requests", [])
        require(isinstance(associations, list), "run_mismatch")
        for association in associations:
            require(isinstance(association, dict) and association.get("number") == state["pull_request"], "run_pr_mismatch")
    return state, token


def safe_path(raw):
    try:
        path = raw.decode("utf-8")
    except UnicodeError:
        raise Failure("unsafe_source_path") from None
    require(bool(path) and len(raw) <= 512 and not path.startswith("/")
            and not re.search(r"[\x00-\x20\x7f\\:]", path)
            and all(part not in ("", ".", "..", ".git") for part in path.split("/")), "unsafe_source_path")
    return path


def materialize(store, commit, destination):
    require(git(store, ["cat-file", "-t", commit]).strip() == b"commit", "source_not_commit")
    tree = sha(git(store, ["rev-parse", "--verify", commit + "^{tree}"]).decode().strip())
    inventory = git(store, ["ls-tree", "-rlz", tree])
    files, total = [], 0
    for entry in inventory.split(b"\0"):
        if not entry:
            continue
        match = re.fullmatch(rb"(100644|100755) blob ([a-f0-9]{40}) +([0-9]+)\t(.+)", entry)
        require(match is not None, "unsupported_source_entry")
        path = safe_path(match[4])
        size = int(match[3])
        total += size
        require(size <= MAX_FILE and total <= MAX_SOURCE and len(files) < MAX_FILES, "source_limit")
        files.append((path, match[2].decode(), size))
    # One bounded batch avoids a separate subprocess per application file.
    batch = git(store, ["cat-file", "--batch"], data=b"".join(blob.encode() + b"\n" for _, blob, _ in files),
                max_bytes=MAX_SOURCE + MAX_FILES * 100, timeout=120) if files else b""
    destination.mkdir(mode=0o755)
    offset = 0
    for path, blob, size in files:
        end = batch.find(b"\n", offset)
        require(end >= offset and batch[offset:end] == f"{blob} blob {size}".encode(), "source_blob_mismatch")
        contents = batch[end + 1:end + 1 + size]
        offset = end + 1 + size
        require(len(contents) == size and batch[offset:offset + 1] == b"\n", "source_blob_mismatch")
        offset += 1
        require(hashlib.sha1(b"blob " + str(size).encode() + b"\0" + contents).hexdigest() == blob, "source_blob_mismatch")
        require(not contents.startswith(b"version https://git-lfs.github.com/spec/v1"), "unsupported_lfs_pointer")
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with target.open("xb") as output:
            output.write(contents)
        target.chmod(0o444)
    require(offset == len(batch), "source_blob_mismatch")
    for directory in destination.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o555)
    destination.chmod(0o555)
    return tree


def work_path(value):
    path = Path(value)
    require(path.is_absolute() and path.name not in ("", ".", ".."), "invalid_work_directory")
    require(not any(part.is_symlink() for part in [path, *path.parents]), "invalid_work_directory")
    require(path != Path("/") and not re.search(r"[\x00-\x1f\x7f,]", str(path)), "invalid_work_directory")
    runner_temp = Path(os.environ.get("RUNNER_TEMP", ""))
    require(runner_temp.is_absolute() and runner_temp.resolve() in path.resolve().parents, "work_outside_runner_temp")
    return path


def save_state(work, state):
    temporary = work / "state.tmp"
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(state, handle, sort_keys=True)
        handle.write("\n")
    temporary.chmod(0o600)
    temporary.replace(work / "state.json")


def prepare(work, environ=None):
    started = time.monotonic()
    environ = os.environ if environ is None else environ
    require(not work.exists() or (work.is_dir() and not any(work.iterdir())), "work_directory_not_empty")
    work.mkdir(parents=False, exist_ok=True, mode=0o700)
    work.chmod(0o700)
    marker = {"version": 1, "id": uuid.uuid4().hex, "path": str(work.resolve()), "uid": os.getuid()}
    with (work / ".gate-work.json").open("x", encoding="utf-8") as handle:
        json.dump(marker, handle)
    (work / ".gate-work.json").chmod(0o600)
    state, token = provider_state(environ)
    store = work / "objects.git"
    code, _ = run([GIT, "-c", "core.hooksPath=/dev/null", "init", "--bare", "--template=", str(store)])
    require(code == 0, "git_failed")
    environment = clean_env()
    environment.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                        "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + token).encode()).decode()})
    git(store, ["fetch", "--no-tags", "--no-recurse-submodules", "--depth=1",
                "https://github.com/" + state["repository"] + ".git",
                state["base_sha"] + ":refs/gate/base",
                "refs/pull/" + str(state["pull_request"]) + "/head:refs/gate/head"], env=environment, timeout=180, storage_root=store)
    for side in ("base", "head"):
        require(git(store, ["rev-parse", "--verify", "refs/gate/" + side]).decode().strip() == state[side + "_sha"],
                "fetched_sha_mismatch")
        state[side + "_tree"] = materialize(store, state[side + "_sha"], work / side)
    for path in store.rglob("*"):
        require(not path.is_symlink(), "unsafe_object_store")
        path.chmod(0o555 if path.is_dir() else 0o444)
    store.chmod(0o555)
    output = work / "output"
    output.mkdir(mode=0o1777)
    output.chmod(0o1777)
    state["prepare_duration_ms"] = int((time.monotonic() - started) * 1000)
    save_state(work, state)


def marker_for(work):
    marker_path = work / ".gate-work.json"
    require(work.is_dir() and not work.is_symlink() and marker_path.is_file()
            and not marker_path.is_symlink() and marker_path.stat().st_size < 4096, "unmarked_work_directory")
    marker = decode_json(marker_path.read_bytes())
    require(isinstance(marker, dict) and marker.get("version") == 1
            and marker.get("uid") == os.getuid() and marker.get("path") == str(work.resolve())
            and isinstance(marker.get("id"), str) and re.fullmatch(r"[a-f0-9]{32}", marker["id"]),
            "unmarked_work_directory")
    return marker


def remove_container(marker):
    name = "gate-ci-" + marker["id"]
    code, output = run([DOCKER, "container", "ls", "--all", "--filter", "name=^/" + name + "$",
                        "--format", '{{.Names}}|{{.Label "io.tuinstra.gate.work"}}'], timeout=30, max_bytes=1048576)
    require(code == 0, "container_cleanup_failed")
    if output.strip():
        require(output.strip() == (name + "|" + marker["id"]).encode(), "container_cleanup_identity_mismatch")
        code, _ = run([DOCKER, "rm", "--force", name], timeout=30, max_bytes=1048576)
        require(code == 0, "container_cleanup_failed")


def cleanup(work):
    if not work.exists():
        return
    marker = marker_for(work)
    remove_container(marker)
    require(shutil.rmtree.avoids_symlink_attacks, "safe_cleanup_unavailable")
    # Source directories were made read-only; only this marked tree is changed.
    for _, _, _, descriptor in os.fwalk(work, follow_symlinks=False):
        os.fchmod(descriptor, 0o700)
    shutil.rmtree(work)


def scan(work):
    started = time.monotonic()
    marker = marker_for(work)
    require(os.getuid() != 0, "root_scanner_forbidden")
    require(re.fullmatch(r"ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}", IMAGE) is not None, "invalid_scanner_pin")
    require(work.is_dir() and not (work / "state.json").is_symlink(), "invalid_work_directory")
    state = decode_json((work / "state.json").read_bytes())
    require(isinstance(state, dict) and "scanner_exit" not in state, "scan_already_recorded")
    for side in ("base", "head"):
        sha(state.get(side + "_sha"))
    for name in ("objects.git", "base", "head", "output"):
        require((work / name).is_dir() and not (work / name).is_symlink(), "invalid_input_root")
    require(not (work / "output/pair").exists(), "scan_output_exists")
    name = "gate-ci-" + marker["id"]
    command = [DOCKER, "create", "--name", name, "--label", "io.tuinstra.gate.work=" + marker["id"],
               "--user", str(os.getuid()) + ":" + str(os.getgid()), "--pull", "never", "--platform", "linux/arm64",
               "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--memory", "3g", "--cpus", "2", "--pids-limit", "128",
               "--tmpfs", "/tmp:rw,nosuid,nodev,exec,size=512m,mode=1777"]
    for source, target in [("objects.git", "objects.git"), ("base", "base"), ("head", "head")]:
        command.extend(["--mount", f"type=bind,src={work / source},dst=/input/{target},readonly"])
    command.extend(["--mount", f"type=bind,src={work / 'output'},dst=/output", IMAGE,
                    "--git-dir", "/input/objects.git", "--base-dir", "/input/base", "--head-dir", "/input/head",
                    "--base", state["base_sha"], "--head", state["head_sha"], "--bundle", "/opt/gate-ci",
                    "--output", "/output/pair"])
    try:
        code, identifier = run(command, timeout=60, max_bytes=1048576)
        require(code == 0 and re.fullmatch(rb"[a-f0-9]{64}\n?", identifier), "container_create_failed")
        code, _ = run([DOCKER, "start", "--attach", name], timeout=900, max_bytes=1048576,
                      storage_root=work / "output")
        require(code in (0, 1, 2), "scanner_execution_failed")
    finally:
        remove_container(marker)
    result = work / "output/pair/result.json"
    require(not result.parent.is_symlink() and result.is_file() and not result.is_symlink()
            and result.stat().st_size <= MAX_REPORT, "scanner_report_missing_or_unsafe")
    report = decode_json(result.read_bytes())
    require(isinstance(report, dict) and report.get("schema_version") == "1.0"
            and report.get("document_type") == "ci-pair-result"
            and report.get("outcome") == {0: "pass", 1: "blocked", 2: "incomplete"}[code]
            and report.get("base_sha") == state["base_sha"] and report.get("head_sha") == state["head_sha"],
            "scanner_report_invalid")
    for field in ("coverage", "findings", "inputs", "reasons", "uncompared_findings"):
        require(isinstance(report.get(field), list), "scanner_report_invalid")
    state["scanner_image"] = IMAGE
    state["scanner_exit"] = code
    state["scan_duration_ms"] = int((time.monotonic() - started) * 1000)
    save_state(work, state)


def interrupted(signum, frame):
    raise Failure("execution_interrupted")


def main():
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "scan", "cleanup"))
    parser.add_argument("--work-dir", required=True)
    arguments = parser.parse_args()
    try:
        work = work_path(arguments.work_dir)
        {"prepare": prepare, "scan": scan, "cleanup": cleanup}[arguments.operation](work)
    except Failure as error:
        parser.exit(2, "Gate preparation failed: " + str(error) + "\n")
    except Exception:
        parser.exit(2, "Gate preparation failed: unexpected_failure\n")


if __name__ == "__main__":
    main()
