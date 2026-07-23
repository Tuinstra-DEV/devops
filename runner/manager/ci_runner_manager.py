#!/usr/bin/env python3
"""Admission and lifecycle boundary for sanctuary CI workers."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

LEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LOG = logging.getLogger("ci-runner-manager")
REGISTRATION_GRACE_SECONDS = 300
HANDOFF_GRACE_SECONDS = 30
HANDOFF_RETRY_MAX_SECONDS = 600
DISPATCH_RETRY_BASE_SECONDS = 60
DISPATCH_RETRY_MAX_SECONDS = 3600
ASSIGNMENT_RUNS_PER_STATUS = 10
HELPER_HEADER_MAX = 4096
HELPER_RESPONSE_MAX = 65536
HELPER_JIT_MAX = 131072
HELPER_ERROR_CODES = {"invalid_request", "unauthorized", "operation_failed", "busy"}
HELPER_QUERY_TIMEOUT_SECONDS = 120
HELPER_MUTATION_TIMEOUT_SECONDS = 330
MAX_CONCURRENCY = 2
RUNNER_VCPUS = 4
RUNNER_MEMORY_MIB = 6144


class RunnerError(RuntimeError):
    pass


class RateLimited(RunnerError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("GitHub API rate limit reached")
        self.retry_after = max(5, min(retry_after, 3600))


class NotFound(RunnerError):
    pass


def trigger_job_id(state: dict[str, Any]) -> int | None:
    """Return the admission trigger, including leases written before the rename."""
    value = state.get("trigger_job_id", state.get("job_id"))
    return value if isinstance(value, int) else None


def runner_name_for_trigger(job_id: int) -> str:
    return f"sanctuary-{job_id}"


def strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RunnerError("host helper returned an invalid response")
        result[key] = value
    return result


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        cfg = tomllib.load(handle)
    required = {
        "state_dir", "lock_file", "audit_log", "helper_socket",
        "max_concurrency", "runner_vcpus", "runner_memory_mib",
        "host_memory_reserve_mib", "min_free_disk_gib", "max_load_1m",
        "max_lease_seconds", "github_token_file", "repositories", "runner_label",
    }
    missing = required.difference(cfg)
    if missing:
        raise RunnerError(f"missing configuration keys: {', '.join(sorted(missing))}")
    exact_resources = {
        "max_concurrency": MAX_CONCURRENCY,
        "runner_vcpus": RUNNER_VCPUS,
        "runner_memory_mib": RUNNER_MEMORY_MIB,
    }
    for key, expected in exact_resources.items():
        value = cfg[key]
        if not isinstance(value, int) or isinstance(value, bool) or value != expected:
            raise RunnerError(f"{key} must be exactly {expected}")
    reserve = cfg["host_memory_reserve_mib"]
    if not isinstance(reserve, int) or isinstance(reserve, bool) or not 1024 <= reserve <= 65536:
        raise RunnerError("host_memory_reserve_mib must be between 1024 and 65536")
    return cfg


def validate_lease_id(value: str) -> str:
    if not LEASE_RE.fullmatch(value):
        raise RunnerError("lease id must contain only letters, digits, dot, underscore, or dash")
    return value


def validate_jit_config(raw: bytes) -> bytes:
    raw = raw.strip()
    if not raw or len(raw) > HELPER_JIT_MAX:
        raise RunnerError("JIT configuration is empty or exceeds 128 KiB")
    try:
        base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise RunnerError("JIT configuration is not valid base64") from exc
    return raw


def read_jit_config(path: Path) -> bytes:
    stat = path.stat()
    if stat.st_mode & 0o077:
        raise RunnerError("JIT configuration file must not be accessible by group or others")
    return validate_jit_config(path.read_bytes())


def memory_value_mib(field: str) -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{field}:"):
            return int(line.split()[1]) // 1024
    raise RunnerError(f"cannot determine {field} memory")


def memory_available_mib() -> int:
    return memory_value_mib("MemAvailable")


def memory_total_mib() -> int:
    return memory_value_mib("MemTotal")


def configured_resource(cfg: dict[str, Any], key: str, expected: int) -> int:
    value = cfg.get(key, expected)
    if not isinstance(value, int) or isinstance(value, bool) or value != expected:
        raise RunnerError(f"{key} must be exactly {expected}")
    return value


def capacity_errors(cfg: dict[str, Any], projected_runner_count: int = 1) -> list[str]:
    errors: list[str] = []
    concurrency = configured_resource(cfg, "max_concurrency", MAX_CONCURRENCY)
    vcpus = configured_resource(cfg, "runner_vcpus", RUNNER_VCPUS)
    memory_mib = configured_resource(cfg, "runner_memory_mib", RUNNER_MEMORY_MIB)
    if not isinstance(projected_runner_count, int) or isinstance(projected_runner_count, bool) \
            or not 1 <= projected_runner_count <= concurrency:
        raise RunnerError("projected runner count is outside the configured concurrency limit")
    projected_vcpus = projected_runner_count * vcpus
    if (os.cpu_count() or 0) < projected_vcpus:
        errors.append(
            f"host has fewer than {projected_vcpus} logical CPUs required for projected runners"
        )
    reserve_mib = int(cfg.get("host_memory_reserve_mib", cfg.get("min_free_memory_mib", 0)))
    if memory_total_mib() < projected_runner_count * memory_mib + reserve_mib:
        errors.append("host memory cannot fit projected runners and reserve")
    projected_available_mib = memory_available_mib() - memory_mib
    if projected_available_mib < reserve_mib:
        errors.append("host projected free memory is below the configured reserve")
    disk = shutil.disk_usage(str(cfg.get("overlay_root", "/mnt/ssd1000-01/ci-runner")))
    if disk.free < int(cfg["min_free_disk_gib"]) * 1024**3:
        errors.append("overlay filesystem free space is below admission threshold")
    if os.getloadavg()[0] > float(cfg["max_load_1m"]):
        errors.append("host one-minute load is above admission threshold")
    return errors


def validate_inventory(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RunnerError("helper returned invalid domain inventory")
    inventory: dict[str, str] = {}
    for lease, state in value.items():
        if not isinstance(lease, str) or not isinstance(state, str) or not state:
            raise RunnerError("helper returned invalid domain inventory")
        inventory[validate_lease_id(lease)] = state
    return inventory


class StateStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def leases(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.directory.glob("lease-*.json")):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                LOG.error("invalid state file %s: %s", path.name, exc)
        return result

    def cleanup_items(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.directory.glob("cleanup-*.json")):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                LOG.error("invalid cleanup state file %s: %s", path.name, exc)
        return result

    def pending_dispatch_keys(self) -> set[str]:
        return {
            f"{state['repo']}:{job_id}"
            for state in self.cleanup_items()
            if isinstance(state.get("repo"), str)
            and (job_id := trigger_job_id(state)) is not None
        }

    def write(self, lease: str, state: dict[str, Any]) -> None:
        destination = self.directory / f"lease-{lease}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)

    def remove(self, lease: str) -> None:
        (self.directory / f"lease-{lease}.json").unlink(missing_ok=True)

    @staticmethod
    def cleanup_key(state: dict[str, Any]) -> str:
        repo = state.get("repo")
        runner_id = state.get("runner_id")
        if not isinstance(repo, str) or not isinstance(runner_id, int):
            raise RunnerError("cleanup obligation requires a GitHub runner identity")
        return hashlib.sha256(f"{repo}:{runner_id}".encode("utf-8")).hexdigest()

    def cleanup_path(self, state: dict[str, Any]) -> Path:
        return self.directory / f"cleanup-{self.cleanup_key(state)}.json"

    def write_cleanup_state(self, state: dict[str, Any]) -> None:
        destination = self.cleanup_path(state)
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def write_cleanup_obligation(self, lease: str, state: dict[str, Any], now: int) -> None:
        pending = dict(state)
        pending.update({"lease": lease, "phase": "registration_pending",
                        "created_at": now, "cleanup_attempts": 0,
                        "next_retry_at": now + REGISTRATION_GRACE_SECONDS})
        self.write_cleanup_state(pending)

    def defer_cleanup(self, lease: str, state: dict[str, Any], now: int, *,
                      preserve_lease: bool = False) -> None:
        attempts = int(state.get("cleanup_attempts", 0)) + 1
        pending = dict(state)
        pending.update({
            "phase": "cleanup_pending",
            "cleanup_attempts": attempts,
            "next_retry_at": now + min(30 * (2 ** min(attempts - 1, 7)), 3600),
        })
        self.write_cleanup_state(pending)
        if not preserve_lease:
            self.remove(lease)

    def write_handoff_obligation(self, lease: str, state: dict[str, Any], now: int) -> None:
        pending = dict(state)
        pending.update({
            "lease": lease,
            "phase": "handoff_pending",
            "handoff_checks": int(state.get("handoff_checks", 0)),
            "next_retry_at": now + HANDOFF_GRACE_SECONDS,
        })
        self.write_cleanup_state(pending)

    def defer_handoff_check(self, state: dict[str, Any], now: int) -> None:
        checks = int(state.get("handoff_checks", 0)) + 1
        pending = dict(state)
        pending.update({
            "phase": "handoff_pending",
            "handoff_checks": checks,
            "next_retry_at": now + min(
                HANDOFF_GRACE_SECONDS * (2 ** min(checks - 1, 7)),
                HANDOFF_RETRY_MAX_SECONDS,
            ),
        })
        self.write_cleanup_state(pending)

    def remove_cleanup(self, state: dict[str, Any]) -> None:
        self.cleanup_path(state).unlink(missing_ok=True)


class DispatchHistory:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "dispatch-history.json"

    def load(self) -> dict[str, dict[str, int]]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RunnerError("invalid dispatch history")
        result: dict[str, dict[str, int]] = {}
        for key, entry in value.items():
            if isinstance(entry, int):
                result[str(key)] = {"blocked_until": entry + 86400, "attempts": 1}
                continue
            if not isinstance(entry, dict) or set(entry) != {"blocked_until", "attempts"}:
                raise RunnerError("invalid dispatch history")
            blocked_until = entry.get("blocked_until")
            attempts = entry.get("attempts")
            if not isinstance(blocked_until, int) or not isinstance(attempts, int) or attempts < 1:
                raise RunnerError("invalid dispatch history")
            result[str(key)] = {"blocked_until": blocked_until, "attempts": attempts}
        return result

    def contains(self, key: str, now: int) -> bool:
        entry = self.load().get(key)
        return entry is not None and entry["blocked_until"] > now

    def write(self, values: dict[str, dict[str, int]]) -> None:
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temporary.write_text(json.dumps(values, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def add(self, key: str, now: int) -> int:
        values = {
            item: entry for item, entry in self.load().items()
            if item == key or entry["blocked_until"] > now
        }
        attempts = values.get(key, {}).get("attempts", 0) + 1
        values[key] = {"blocked_until": now + 86400, "attempts": attempts}
        self.write(values)
        return attempts

    def retry_after_cooldown(self, key: str, now: int, attempts_hint: int = 1) -> int:
        values = self.load()
        entry = values.get(key)
        if entry is None:
            entry = {"blocked_until": now, "attempts": max(1, attempts_hint)}
            values[key] = entry
        delay = min(
            DISPATCH_RETRY_BASE_SECONDS * (2 ** min(entry["attempts"] - 1, 10)),
            DISPATCH_RETRY_MAX_SECONDS,
        )
        entry["blocked_until"] = now + delay
        self.write(values)
        return delay


class GitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        if not token or "\n" in token:
            raise RunnerError("invalid GitHub credential")
        self._token = token
        self._api_url = api_url.rstrip("/")

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._api_url + path, method=method, data=data,
            headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self._token}",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "sanctuary-ci-manager"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFound("GitHub resource was already removed") from exc
            remaining = exc.headers.get("X-RateLimit-Remaining")
            if exc.code == 429 or (exc.code == 403 and (exc.headers.get("Retry-After") or remaining == "0")):
                retry = exc.headers.get("Retry-After")
                reset = exc.headers.get("X-RateLimit-Reset")
                delay = int(retry) if retry and retry.isdigit() else max(5, int(reset) - int(time.time()) if reset and reset.isdigit() else 60)
                raise RateLimited(delay) from exc
            raise RunnerError(f"GitHub API returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RunnerError("GitHub API request failed") from exc

    @staticmethod
    def repo_path(repo: str) -> str:
        owner, name = repo.split("/", 1)
        return f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"

    def candidate_jobs(self, repo: str, required_label: str) -> list[dict[str, Any]]:
        candidates = []
        for status in ("queued", "in_progress"):
            runs = self.request("GET", f"{self.repo_path(repo)}/actions/runs?status={status}&per_page=100")
            for run in runs.get("workflow_runs", []):
                jobs = self.request("GET", f"{self.repo_path(repo)}/actions/runs/{int(run['id'])}/jobs?filter=latest&per_page=100")
                for job in jobs.get("jobs", []):
                    labels = job.get("labels", [])
                    if job.get("status") == "queued" and required_label in labels:
                        candidates.append({"repo": repo, "run_id": int(run["id"]), "job_id": int(job["id"])})
        return candidates

    def generate_jit(self, repo: str, job_id: int, group_id: int, label: str) -> dict[str, Any]:
        return self.request("POST", f"{self.repo_path(repo)}/actions/runners/generate-jitconfig", {
            "name": runner_name_for_trigger(job_id), "runner_group_id": group_id,
            "labels": ["self-hosted", "linux", "x64", label], "work_folder": "_work",
        })

    def find_assigned_job(self, repo: str, runner_id: int,
                          runner_name: str, *,
                          preferred_run_id: int | None = None,
                          include_completed: bool = True) -> dict[str, Any] | None:
        if not runner_name or runner_id < 1:
            return None
        id_matches: dict[tuple[int, int], dict[str, Any]] = {}

        def inspect_run(run_id: int) -> None:
            jobs = self.request(
                "GET", f"{self.repo_path(repo)}/actions/runs/{run_id}"
                "/jobs?filter=latest&per_page=100&page=1"
            )
            for job in jobs.get("jobs", []):
                job_id = job.get("id")
                if not isinstance(job_id, int):
                    continue
                reported_runner_id = job.get("runner_id")
                reported_runner_name = job.get("runner_name")
                assignment = {"repo": repo, "run_id": run_id, "job_id": job_id}
                key = (run_id, job_id)
                if not isinstance(reported_runner_id, int) \
                        or reported_runner_id != runner_id:
                    continue
                if reported_runner_name not in (None, "", runner_name):
                    raise RunnerError("GitHub returned a conflicting runner assignment")
                id_matches[key] = assignment

        if isinstance(preferred_run_id, int) and preferred_run_id > 0:
            inspect_run(preferred_run_id)
            if len(id_matches) > 1:
                raise RunnerError("GitHub returned an ambiguous runner assignment")
            if id_matches:
                return next(iter(id_matches.values()))

        statuses = ("in_progress", "completed", "queued") \
            if include_completed else ("in_progress",)
        for status in statuses:
            runs = self.request(
                "GET", f"{self.repo_path(repo)}/actions/runs?status={status}"
                f"&per_page={ASSIGNMENT_RUNS_PER_STATUS}&page=1"
            )
            for run in runs.get("workflow_runs", []):
                run_id = int(run["id"])
                if run_id != preferred_run_id:
                    inspect_run(run_id)
        if len(id_matches) > 1:
            raise RunnerError("GitHub returned an ambiguous runner assignment")
        return next(iter(id_matches.values())) if id_matches else None

    def delete_runner(self, repo: str, runner_id: int) -> None:
        try:
            self.request("DELETE", f"{self.repo_path(repo)}/actions/runners/{runner_id}")
        except NotFound:
            return

    def get_runner(self, repo: str, runner_id: int) -> dict[str, Any]:
        runner = self.request(
            "GET", f"{self.repo_path(repo)}/actions/runners/{runner_id}"
        )
        if not isinstance(runner, dict) or runner.get("id") != runner_id \
                or runner.get("status") not in {"online", "offline"} \
                or not isinstance(runner.get("busy"), bool):
            raise RunnerError("GitHub returned an invalid runner response")
        return runner

    def get_job(self, repo: str, job_id: int) -> dict[str, Any]:
        job = self.request("GET", f"{self.repo_path(repo)}/actions/jobs/{job_id}")
        if not isinstance(job, dict) or job.get("id") != job_id:
            raise RunnerError("GitHub returned an invalid job response")
        return job


def validate_repositories(cfg: dict[str, Any]) -> list[str]:
    repos = cfg.get("repositories", [])
    owner = str(cfg.get("allowed_owner", "Tuinstra-DEV"))
    if not isinstance(repos, list) or not repos:
        raise RunnerError("repository allowlist must not be empty")
    pattern = re.compile(rf"^{re.escape(owner)}/[A-Za-z0-9_.-]+$")
    if any(not isinstance(repo, str) or not pattern.fullmatch(repo) for repo in repos):
        raise RunnerError("repository allowlist contains an invalid repository")
    return repos


def read_token(path: Path) -> str:
    stat = path.stat()
    credential_directory = os.environ.get("CREDENTIALS_DIRECTORY")
    inside_systemd_credentials = False
    if credential_directory:
        try:
            inside_systemd_credentials = path.resolve(strict=True).parent == Path(credential_directory).resolve(strict=True)
        except OSError:
            inside_systemd_credentials = False
    if not inside_systemd_credentials and stat.st_mode & 0o077:
        raise RunnerError("GitHub credential must not be accessible by group or others")
    return path.read_text(encoding="utf-8").strip()


def helper(cfg: dict[str, Any], *args: str, stdin: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    if not args or args[0] not in {"list", "launch", "destroy"} or len(args) > 2:
        raise RunnerError("invalid host helper operation")
    operation = args[0]
    request_id = secrets.token_hex(16)
    request: dict[str, Any] = {"v": 1, "id": request_id, "op": operation}
    if operation in {"launch", "destroy"}:
        if len(args) != 2:
            raise RunnerError("host helper operation requires a lease")
        request["lease"] = validate_lease_id(args[1])
    elif len(args) != 1:
        raise RunnerError("list operation does not accept a lease")
    if operation == "launch":
        if stdin is None:
            raise RunnerError("launch requires JIT configuration")
        request["vcpus"] = configured_resource(cfg, "runner_vcpus", RUNNER_VCPUS)
        request["memory_mib"] = configured_resource(
            cfg, "runner_memory_mib", RUNNER_MEMORY_MIB
        )
        stdin = validate_jit_config(stdin)
    elif stdin is not None:
        raise RunnerError("host helper payload is only valid for launch")
    header = json.dumps(request, separators=(",", ":")).encode("utf-8")
    if len(header) > HELPER_HEADER_MAX:
        raise RunnerError("host helper request header exceeds limit")
    command = ["host-helper", operation, *args[1:]]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.settimeout(HELPER_QUERY_TIMEOUT_SECONDS if operation == "list"
                                  else HELPER_MUTATION_TIMEOUT_SECONDS)
            connection.connect(str(cfg["helper_socket"]))
            if connection.send(header) != len(header):
                raise RunnerError("host helper request was truncated")
            if stdin is not None and connection.send(stdin) != len(stdin):
                raise RunnerError("host helper JIT payload was truncated")
            payload, _ancillary, flags, _address = connection.recvmsg(HELPER_RESPONSE_MAX)
    except (OSError, TimeoutError) as exc:
        raise RunnerError("host helper is unavailable") from exc
    if flags & socket.MSG_TRUNC or not payload:
        raise RunnerError("host helper returned an invalid response")
    try:
        response = json.loads(payload, object_pairs_hook=strict_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RunnerError) as exc:
        raise RunnerError("host helper returned an invalid response") from exc
    if not isinstance(response, dict) or response.get("v") != 1 or response.get("id") != request_id:
        raise RunnerError("host helper returned an invalid response")
    if response.get("ok") is True and set(response) == {"v", "id", "ok", "result"}:
        result = response["result"]
        if (operation == "list" and not isinstance(result, dict)) or \
                (operation != "list" and result is not None):
            raise RunnerError("host helper returned an invalid response")
        stdout = json.dumps(result, sort_keys=True).encode("utf-8") if operation == "list" else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")
    error = response.get("error")
    if response.get("ok") is not False or set(response) != {"v", "id", "ok", "error"} or error not in HELPER_ERROR_CODES:
        raise RunnerError("host helper returned an invalid response")
    completed = subprocess.CompletedProcess(command, 1, stdout=b"", stderr=str(error).encode("ascii"))
    if check:
        raise subprocess.CalledProcessError(1, command, output=b"", stderr=completed.stderr)
    return completed


def with_lock(path: Path):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def launch(cfg: dict[str, Any], lease: str, jit_source: Path | bytes, *,
           metadata: dict[str, Any] | None = None,
           dispatch_history_key: str | None = None) -> None:
    lease = validate_lease_id(lease)
    jit = read_jit_config(jit_source) if isinstance(jit_source, Path) else validate_jit_config(jit_source)
    store = StateStore(Path(cfg["state_dir"]))
    with with_lock(Path(cfg["lock_file"])):
        now = int(time.time())
        history = DispatchHistory(Path(cfg["state_dir"]))
        if dispatch_history_key is not None:
            if dispatch_history_key in store.pending_dispatch_keys():
                raise RunnerError("dispatch target has pending cleanup")
            if history.contains(dispatch_history_key, now):
                raise RunnerError("dispatch target is still in retry history")
        states = store.leases()
        state_by_lease: dict[str, dict[str, Any]] = {}
        for item in states:
            state_lease = item.get("lease")
            if not isinstance(state_lease, str) or state_lease in state_by_lease:
                raise RunnerError("runner lifecycle state requires reconciliation")
            state_by_lease[validate_lease_id(state_lease)] = item
        inventory = validate_inventory(
            json.loads(helper(cfg, "list").stdout.decode("utf-8"))
        )
        if set(state_by_lease) != set(inventory) or any(
            item.get("phase") != "running" or inventory[state_lease] != "running"
            for state_lease, item in state_by_lease.items()
        ):
            raise RunnerError("runner lifecycle state requires reconciliation")
        concurrency = configured_resource(cfg, "max_concurrency", MAX_CONCURRENCY)
        if len(state_by_lease) >= concurrency:
            raise RunnerError(f"maximum concurrency is {concurrency}; all runner slots are occupied")
        if lease in state_by_lease or lease in inventory:
            raise RunnerError("lease already exists")
        failures = capacity_errors(cfg, len(state_by_lease) + 1)
        if failures:
            raise RunnerError("; ".join(failures))
        state = {"lease": lease, "phase": "provisioning", "launched_at": now}
        if metadata:
            state.update(metadata)
        if dispatch_history_key is not None:
            if not isinstance(state.get("repo"), str) or not isinstance(state.get("runner_id"), int):
                raise RunnerError("dispatch launch requires GitHub runner identity")
            store.write_cleanup_obligation(lease, state, now)
        store.write(lease, state)
        if dispatch_history_key is not None:
            state["dispatch_attempts"] = history.add(dispatch_history_key, now)
            store.write(lease, state)
        LOG.info("launch requested lease=%s", lease)
        try:
            helper(cfg, "launch", lease, stdin=jit)
        except Exception:
            # The socket may close while the root-side mutation is still being
            # terminated. Keep the cleanup obligation durable so the next
            # reconcile destroys both a possible domain and its lease files.
            state["phase"] = "provisioning_failed"
            store.write(lease, state)
            raise
        state["phase"] = "running"
        store.write(lease, state)
        if dispatch_history_key is not None:
            store.remove_cleanup(state)
        LOG.info("launch completed lease=%s", lease)


def cleanup_github_runner(client: GitHubClient | None, state: dict[str, Any]) -> None:
    repo = state.get("repo")
    runner_id = state.get("runner_id")
    if repo is None and runner_id is None:
        return
    if client is None:
        raise RunnerError("GitHub client is required to clean a registered runner")
    if not isinstance(repo, str) or not isinstance(runner_id, int):
        raise RunnerError("lease contains invalid GitHub runner identity")
    client.delete_runner(repo, runner_id)


def has_dispatch_target(state: dict[str, Any]) -> bool:
    return isinstance(state.get("repo"), str) and trigger_job_id(state) is not None


def record_verified_assignment(store: StateStore, client: GitHubClient | None,
                               state: dict[str, Any], *,
                               include_completed: bool) -> dict[str, Any]:
    if client is None or isinstance(state.get("actual_job_id"), int):
        return state
    repo = state.get("repo")
    runner_name = state.get("runner_name")
    lease = state.get("lease")
    runner_id = state.get("runner_id")
    trigger_id = trigger_job_id(state)
    trigger_run_id = state.get("trigger_run_id", state.get("run_id"))
    if not isinstance(repo, str) or not isinstance(runner_name, str) \
            or not isinstance(lease, str) or not isinstance(runner_id, int) \
            or trigger_id is None:
        return state
    try:
        assignment = client.find_assigned_job(
            repo,
            runner_id,
            runner_name,
            preferred_run_id=trigger_run_id if isinstance(trigger_run_id, int) else None,
            include_completed=include_completed,
        )
    except RunnerError as exc:
        LOG.warning(
            "runner assignment could not be verified lease=%s repo=%s runner_id=%s "
            "trigger_job_id=%s error=%s",
            lease, repo, runner_id, trigger_id, exc,
        )
        return state
    if assignment is None:
        return state
    actual_job_id = assignment.get("job_id")
    actual_run_id = assignment.get("run_id")
    if not isinstance(actual_job_id, int) or not isinstance(actual_run_id, int):
        return state
    updated = dict(state)
    updated.update({"actual_job_id": actual_job_id, "actual_run_id": actual_run_id})
    store.write(lease, updated)
    LOG.info(
        "runner assignment verified lease=%s repo=%s runner_id=%s runner_name=%s "
        "trigger_job_id=%s actual_job_id=%s",
        lease, repo, runner_id, runner_name, trigger_id, actual_job_id,
    )
    return updated


def unassigned_lease_is_stale(client: GitHubClient | None,
                              state: dict[str, Any], now: int) -> bool:
    """Identify an unassigned runner with positive offline evidence.

    Trigger identity is only an admission hint: a JIT runner can accept another
    same-label job. API failures and missing ephemeral registrations therefore
    preserve the lease. After the boot grace period, only a present, offline,
    non-busy registration is safe to reap before the local domain stops or its
    hard TTL expires.
    """
    if client is None or isinstance(state.get("actual_job_id"), int):
        return False
    repo = state.get("repo")
    runner_id = state.get("runner_id")
    lease = state.get("lease")
    trigger_id = trigger_job_id(state)
    if not isinstance(repo, str) or not isinstance(runner_id, int) \
            or not isinstance(lease, str) or trigger_id is None:
        return False
    try:
        job = client.get_job(repo, trigger_id)
    except (NotFound, RunnerError) as exc:
        LOG.warning(
            "runner trigger status could not be verified lease=%s repo=%s "
            "runner_id=%s trigger_job_id=%s error=%s",
            lease, repo, runner_id, trigger_id, exc,
        )
        return False
    job_status = job.get("status")
    if job_status not in {"queued", "in_progress", "completed"}:
        LOG.warning(
            "runner trigger returned invalid status lease=%s repo=%s "
            "runner_id=%s trigger_job_id=%s",
            lease, repo, runner_id, trigger_id,
        )
        return False
    launched_at = state.get("launched_at")
    if not isinstance(launched_at, int) \
            or launched_at + REGISTRATION_GRACE_SECONDS >= now:
        return False
    try:
        runner = client.get_runner(repo, runner_id)
    except NotFound:
        LOG.info(
            "preserving unassigned lease with missing ephemeral registration "
            "lease=%s repo=%s runner_id=%s trigger_job_id=%s",
            lease, repo, runner_id, trigger_id,
        )
        return False
    except RunnerError as exc:
        LOG.warning(
            "runner registration status could not be verified lease=%s repo=%s "
            "runner_id=%s trigger_job_id=%s error=%s",
            lease, repo, runner_id, trigger_id, exc,
        )
        return False
    if runner["status"] == "offline" and runner["busy"] is False:
        LOG.warning(
            "unassigned runner stayed offline beyond registration grace "
            "lease=%s repo=%s runner_id=%s trigger_job_id=%s",
            lease, repo, runner_id, trigger_id,
        )
        return True
    return False


def retry_dispatch_handoff(store: StateStore, client: GitHubClient | None,
                           state: dict[str, Any], now: int) -> None:
    repo = state.get("repo")
    job_id = trigger_job_id(state)
    if client is None or not isinstance(repo, str) or not isinstance(job_id, int):
        store.defer_handoff_check(state, now)
        return
    try:
        job = client.get_job(repo, job_id)
    except NotFound:
        store.remove_cleanup(state)
        LOG.info(
            "dispatch handoff trigger no longer exists repo=%s trigger_job_id=%s",
            repo, job_id,
        )
        return
    except RunnerError as exc:
        LOG.warning(
            "dispatch handoff trigger could not be verified repo=%s trigger_job_id=%s error=%s",
            repo, job_id, exc,
        )
        store.defer_handoff_check(state, now)
        return
    status = job.get("status")
    if status == "completed":
        store.remove_cleanup(state)
        LOG.info("dispatch handoff trigger completed repo=%s trigger_job_id=%s", repo, job_id)
        return
    if status == "queued":
        key = f"{repo}:{job_id}"
        delay = DispatchHistory(store.directory).retry_after_cooldown(
            key, now, int(state.get("dispatch_attempts", 1))
        )
        store.remove_cleanup(state)
        LOG.info(
            "dispatch handoff trigger queued for retry repo=%s trigger_job_id=%s retry_after=%s",
            repo, job_id, delay,
        )
        return
    store.defer_handoff_check(state, now)
    LOG.info(
        "dispatch handoff trigger remains pending repo=%s trigger_job_id=%s status=%s",
        repo, job_id, status if isinstance(status, str) else "invalid",
    )


def destroy(cfg: dict[str, Any], lease: str, client: GitHubClient | None = None) -> None:
    lease = validate_lease_id(lease)
    store = StateStore(Path(cfg["state_dir"]))
    with with_lock(Path(cfg["lock_file"])):
        LOG.info("destroy requested lease=%s", lease)
        state = next((item for item in store.leases() if item.get("lease") == lease), {"lease": lease})
        helper(cfg, "destroy", lease)
        try:
            cleanup_github_runner(client, state)
        except RunnerError:
            store.defer_cleanup(lease, state, int(time.time()))
            raise
        else:
            if has_dispatch_target(state):
                store.write_handoff_obligation(lease, state, int(time.time()))
            store.remove(lease)
            if not has_dispatch_target(state) \
                    and isinstance(state.get("repo"), str) \
                    and isinstance(state.get("runner_id"), int):
                store.remove_cleanup(state)
        LOG.info("destroy completed lease=%s", lease)


def retry_pending_cleanup(store: StateStore, client: GitHubClient | None, now: int,
                          active_states: list[dict[str, Any]] | None = None) -> None:
    active_keys = {store.cleanup_key(state) for state in (active_states or [])
                   if isinstance(state.get("repo"), str) and isinstance(state.get("runner_id"), int)}
    for state in store.cleanup_items():
        lease = validate_lease_id(str(state.get("lease", "")))
        if state.get("phase") == "handoff_pending":
            if int(state.get("next_retry_at", 0)) <= now:
                retry_dispatch_handoff(store, client, state, now)
            continue
        if state.get("phase") == "registration_pending" and store.cleanup_key(state) in active_keys:
            store.remove_cleanup(state)
            LOG.info("GitHub cleanup obligation transferred to active lease=%s", lease)
            continue
        if int(state.get("next_retry_at", 0)) > now:
            continue
        try:
            cleanup_github_runner(client, state)
        except RunnerError as exc:
            LOG.warning("GitHub runner cleanup deferred lease=%s error=%s", lease, exc)
            store.defer_cleanup(lease, state, now)
        else:
            if has_dispatch_target(state):
                store.write_handoff_obligation(lease, state, now)
                LOG.info("GitHub runner cleanup completed; handoff pending lease=%s", lease)
            else:
                store.remove_cleanup(state)
                LOG.info("GitHub runner cleanup completed lease=%s", lease)


def reconcile(cfg: dict[str, Any], client: GitHubClient | None = None) -> None:
    store = StateStore(Path(cfg["state_dir"]))
    with with_lock(Path(cfg["lock_file"])):
        result = helper(cfg, "list", check=True)
        domains = validate_inventory(json.loads(result.stdout.decode("utf-8")))
        known = {item["lease"] for item in store.leases() if "lease" in item}
        now = int(time.time())
        states = {item["lease"]: item for item in store.leases() if "lease" in item}
        expired = {lease for lease, state in states.items()
                   if int(state.get("launched_at", 0)) + int(cfg["max_lease_seconds"]) < now}
        healthy = {
            lease for lease, domain_state in domains.items()
            if domain_state == "running" and states.get(lease, {}).get("phase") == "running"
        } - expired
        for lease, state in list(states.items()):
            states[lease] = record_verified_assignment(
                store, client, state, include_completed=lease not in healthy
            )
        stale_unassigned = {
            lease for lease in healthy
            if unassigned_lease_is_stale(client, states[lease], now)
        }
        healthy -= stale_unassigned
        for lease in sorted(known - healthy):
            LOG.warning("cleaning completed or missing domain lease=%s", lease)
            helper(cfg, "destroy", lease)
            try:
                cleanup_github_runner(client, states[lease])
            except RunnerError as exc:
                LOG.warning("GitHub runner cleanup deferred lease=%s error=%s", lease, exc)
                store.defer_cleanup(lease, states[lease], now)
            else:
                if has_dispatch_target(states[lease]):
                    store.write_handoff_obligation(lease, states[lease], now)
                store.remove(lease)
                if not has_dispatch_target(states[lease]) \
                        and isinstance(states[lease].get("repo"), str) \
                        and isinstance(states[lease].get("runner_id"), int):
                    store.remove_cleanup(states[lease])
        for lease in sorted(set(domains) - known):
            LOG.warning("destroying orphan domain lease=%s", lease)
            helper(cfg, "destroy", lease)
        retry_pending_cleanup(store, client, now, store.leases())


def dispatch_once(cfg: dict[str, Any], client: GitHubClient) -> bool:
    store = StateStore(Path(cfg["state_dir"]))
    concurrency = configured_resource(cfg, "max_concurrency", MAX_CONCURRENCY)
    available_slots = concurrency - len(store.leases())
    if available_slots <= 0:
        return False
    now = int(time.time())
    history = DispatchHistory(Path(cfg["state_dir"]))
    pending_dispatches = store.pending_dispatch_keys()
    label = str(cfg.get("runner_label", "trusted-heavy"))
    launched = 0
    for repo in validate_repositories(cfg):
        if launched >= available_slots:
            break
        for job in client.candidate_jobs(repo, label):
            if launched >= available_slots:
                break
            key = f"{repo}:{job['job_id']}"
            if key in pending_dispatches or history.contains(key, now):
                continue
            response = client.generate_jit(repo, job["job_id"], int(cfg.get("runner_group_id", 1)), label)
            encoded = response.get("encoded_jit_config")
            runner = response.get("runner", {})
            runner_id = runner.get("id") if isinstance(runner, dict) else None
            reported_runner_name = runner.get("name") if isinstance(runner, dict) else None
            runner_name = runner_name_for_trigger(job["job_id"])
            runner_name_conflicts = reported_runner_name not in (None, "", runner_name)
            lease = f"gh-{job['job_id']}"
            registration = {
                "lease": lease,
                "repo": repo,
                "runner_id": runner_id,
                "trigger_run_id": job["run_id"],
                "trigger_job_id": job["job_id"],
            }
            registration["runner_name"] = runner_name
            if not isinstance(encoded, str) or not isinstance(runner_id, int) \
                    or runner_name_conflicts:
                if isinstance(runner_id, int):
                    store.write_cleanup_obligation(lease, registration, int(time.time()))
                    try:
                        client.delete_runner(repo, runner_id)
                    except RunnerError as exc:
                        LOG.error("deferring malformed GitHub runner cleanup repo=%s runner_id=%s error=%s",
                                  repo, runner_id, exc)
                        store.defer_cleanup(lease, registration, int(time.time()))
                    else:
                        store.remove_cleanup(registration)
                raise RunnerError("GitHub returned an invalid JIT response")
            try:
                launch(cfg, lease, encoded.encode("ascii"), metadata={
                    "repo": repo,
                    "runner_id": runner_id,
                    "runner_name": runner_name,
                    "trigger_run_id": job["run_id"],
                    "trigger_job_id": job["job_id"],
                }, dispatch_history_key=key)
                LOG.info(
                    "runner registered lease=%s repo=%s runner_id=%s trigger_run_id=%s "
                    "trigger_job_id=%s assignment=unverified",
                    lease, repo, runner_id, job["run_id"], job["job_id"],
                )
                launched += 1
            except Exception:
                try:
                    client.delete_runner(repo, runner_id)
                except RunnerError as exc:
                    LOG.error("deferring unused GitHub runner cleanup repo=%s runner_id=%s error=%s",
                              repo, runner_id, exc)
                    store.defer_cleanup(lease, {
                        "lease": lease,
                        "repo": repo,
                        "runner_id": runner_id,
                        "runner_name": runner_name,
                        "trigger_run_id": job["run_id"],
                        "trigger_job_id": job["job_id"],
                    }, int(time.time()), preserve_lease=True)
                else:
                    store.remove_cleanup(registration)
                raise
    return launched > 0


def configure_logging(path: Path) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/etc/ci-runner/manager.toml"))
    sub = parser.add_subparsers(dest="command", required=True)
    launch_parser = sub.add_parser("launch")
    launch_parser.add_argument("--lease", required=True)
    launch_parser.add_argument("--jit-config-file", required=True, type=Path)
    destroy_parser = sub.add_parser("destroy")
    destroy_parser.add_argument("--lease", required=True)
    sub.add_parser("reconcile")
    daemon_parser = sub.add_parser("daemon")
    daemon_parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        configure_logging(Path(cfg["audit_log"]))
        if args.command == "launch":
            launch(cfg, args.lease, args.jit_config_file)
        elif args.command == "destroy":
            client = GitHubClient(read_token(Path(cfg["github_token_file"])), str(cfg.get("github_api_url", "https://api.github.com")))
            destroy(cfg, args.lease, client)
        elif args.command == "reconcile":
            client = GitHubClient(read_token(Path(cfg["github_token_file"])), str(cfg.get("github_api_url", "https://api.github.com")))
            reconcile(cfg, client)
        else:
            if args.interval < 5:
                raise RunnerError("daemon interval must be at least 5 seconds")
            client = GitHubClient(read_token(Path(cfg["github_token_file"])), str(cfg.get("github_api_url", "https://api.github.com")))
            while True:
                try:
                    reconcile(cfg, client)
                    dispatch_once(cfg, client)
                except RateLimited as exc:
                    LOG.warning("GitHub rate limited; retrying later")
                    time.sleep(exc.retry_after)
                    continue
                except Exception:
                    LOG.exception("poll or reconciliation failed")
                time.sleep(args.interval)
        return 0
    except (RunnerError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
