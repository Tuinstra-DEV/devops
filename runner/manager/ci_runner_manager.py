#!/usr/bin/env python3
"""Admission and lifecycle boundary for the single sanctuary CI worker."""

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
import shutil
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


class RunnerError(RuntimeError):
    pass


class RateLimited(RunnerError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("GitHub API rate limit reached")
        self.retry_after = max(5, min(retry_after, 3600))


class NotFound(RunnerError):
    pass


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        cfg = tomllib.load(handle)
    required = {"state_dir", "lock_file", "audit_log", "helper", "min_free_memory_mib", "min_free_disk_gib", "max_load_1m", "max_lease_seconds", "github_token_file", "repositories", "runner_label"}
    missing = required.difference(cfg)
    if missing:
        raise RunnerError(f"missing configuration keys: {', '.join(sorted(missing))}")
    return cfg


def validate_lease_id(value: str) -> str:
    if not LEASE_RE.fullmatch(value):
        raise RunnerError("lease id must contain only letters, digits, dot, underscore, or dash")
    return value


def read_jit_config(path: Path) -> bytes:
    stat = path.stat()
    if stat.st_mode & 0o077:
        raise RunnerError("JIT configuration file must not be accessible by group or others")
    raw = path.read_bytes().strip()
    if not raw or len(raw) > 131072:
        raise RunnerError("JIT configuration is empty or exceeds 128 KiB")
    try:
        base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise RunnerError("JIT configuration is not valid base64") from exc
    return raw


def memory_available_mib() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise RunnerError("cannot determine available memory")


def capacity_errors(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if (os.cpu_count() or 0) < 8:
        errors.append("host has fewer than 8 logical CPUs")
    if memory_available_mib() < int(cfg["min_free_memory_mib"]):
        errors.append("host free memory is below admission threshold")
    disk = shutil.disk_usage(str(cfg.get("overlay_root", "/mnt/ssd1000-01/ci-runner")))
    if disk.free < int(cfg["min_free_disk_gib"]) * 1024**3:
        errors.append("overlay filesystem free space is below admission threshold")
    if os.getloadavg()[0] > float(cfg["max_load_1m"]):
        errors.append("host one-minute load is above admission threshold")
    return errors


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

    def write_cleanup_obligation(self, lease: str, state: dict[str, Any], now: int) -> None:
        pending = dict(state)
        pending.update({"lease": lease, "phase": "registration_pending",
                        "created_at": now, "cleanup_attempts": 0,
                        "next_retry_at": now + REGISTRATION_GRACE_SECONDS})
        destination = self.cleanup_path(pending)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(pending, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)

    def defer_cleanup(self, lease: str, state: dict[str, Any], now: int) -> None:
        attempts = int(state.get("cleanup_attempts", 0)) + 1
        pending = dict(state)
        pending.update({
            "phase": "cleanup_pending",
            "cleanup_attempts": attempts,
            "next_retry_at": now + min(30 * (2 ** min(attempts - 1, 7)), 3600),
        })
        destination = self.cleanup_path(pending)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(pending, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        self.remove(lease)

    def remove_cleanup(self, state: dict[str, Any]) -> None:
        self.cleanup_path(state).unlink(missing_ok=True)


class DispatchHistory:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "dispatch-history.json"

    def load(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RunnerError("invalid dispatch history")
        return {str(key): int(timestamp) for key, timestamp in value.items()}

    def contains(self, key: str, now: int) -> bool:
        return key in {item for item, seen in self.load().items() if seen > now - 86400}

    def add(self, key: str, now: int) -> None:
        values = {item: seen for item, seen in self.load().items() if seen > now - 86400}
        values[key] = now
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)


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
            "name": f"sanctuary-{job_id}", "runner_group_id": group_id,
            "labels": ["self-hosted", "linux", "x64", label], "work_folder": "_work",
        })

    def delete_runner(self, repo: str, runner_id: int) -> None:
        try:
            self.request("DELETE", f"{self.repo_path(repo)}/actions/runners/{runner_id}")
        except NotFound:
            return


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


def helper(cfg: dict[str, Any], *args: str, stdin: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["sudo", "--non-interactive", str(cfg["helper"]), *args]
    return subprocess.run(command, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=check, timeout=120, text=False)


def with_lock(path: Path):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def launch(cfg: dict[str, Any], lease: str, jit_path: Path, *, metadata: dict[str, Any] | None = None) -> None:
    lease = validate_lease_id(lease)
    jit = read_jit_config(jit_path)
    store = StateStore(Path(cfg["state_dir"]))
    with with_lock(Path(cfg["lock_file"])):
        if store.leases():
            raise RunnerError("maximum concurrency is 1; an active lease already exists")
        inventory = json.loads(helper(cfg, "list").stdout.decode("utf-8"))
        if not isinstance(inventory, dict):
            raise RunnerError("helper returned invalid domain inventory")
        if inventory:
            raise RunnerError("maximum concurrency is 1; a runner domain already exists")
        failures = capacity_errors(cfg)
        if failures:
            raise RunnerError("; ".join(failures))
        state = {"lease": lease, "phase": "provisioning", "launched_at": int(time.time())}
        if metadata:
            state.update(metadata)
        store.write(lease, state)
        LOG.info("launch requested lease=%s", lease)
        try:
            helper(cfg, "launch", lease, stdin=jit)
        except Exception:
            store.remove(lease)
            raise
        state["phase"] = "running"
        store.write(lease, state)
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
            store.remove(lease)
        LOG.info("destroy completed lease=%s", lease)


def retry_pending_cleanup(store: StateStore, client: GitHubClient | None, now: int,
                          active_states: list[dict[str, Any]] | None = None) -> None:
    active_keys = {store.cleanup_key(state) for state in (active_states or [])
                   if isinstance(state.get("repo"), str) and isinstance(state.get("runner_id"), int)}
    for state in store.cleanup_items():
        lease = validate_lease_id(str(state.get("lease", "")))
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
            store.remove_cleanup(state)
            LOG.info("GitHub runner cleanup completed lease=%s", lease)


def reconcile(cfg: dict[str, Any], client: GitHubClient | None = None) -> None:
    store = StateStore(Path(cfg["state_dir"]))
    with with_lock(Path(cfg["lock_file"])):
        result = helper(cfg, "list", check=True)
        domains = json.loads(result.stdout.decode("utf-8"))
        if not isinstance(domains, dict):
            raise RunnerError("helper returned invalid domain inventory")
        known = {item["lease"] for item in store.leases() if "lease" in item}
        now = int(time.time())
        states = {item["lease"]: item for item in store.leases() if "lease" in item}
        expired = {lease for lease, state in states.items()
                   if int(state.get("launched_at", 0)) + int(cfg["max_lease_seconds"]) < now}
        running = {lease for lease, state in domains.items() if state == "running"} - expired
        for lease in sorted(known - running):
            LOG.warning("cleaning completed or missing domain lease=%s", lease)
            helper(cfg, "destroy", lease)
            try:
                cleanup_github_runner(client, states[lease])
            except RunnerError as exc:
                LOG.warning("GitHub runner cleanup deferred lease=%s error=%s", lease, exc)
                store.defer_cleanup(lease, states[lease], now)
            else:
                store.remove(lease)
        for lease in sorted(set(domains) - known):
            LOG.warning("destroying orphan domain lease=%s", lease)
            helper(cfg, "destroy", lease)
        retry_pending_cleanup(store, client, now, store.leases())


def dispatch_once(cfg: dict[str, Any], client: GitHubClient) -> bool:
    store = StateStore(Path(cfg["state_dir"]))
    if store.leases():
        return False
    now = int(time.time())
    history = DispatchHistory(Path(cfg["state_dir"]))
    label = str(cfg.get("runner_label", "trusted-heavy"))
    for repo in validate_repositories(cfg):
        for job in client.candidate_jobs(repo, label):
            key = f"{repo}:{job['job_id']}"
            if history.contains(key, now):
                continue
            response = client.generate_jit(repo, job["job_id"], int(cfg.get("runner_group_id", 1)), label)
            encoded = response.get("encoded_jit_config")
            runner_id = response.get("runner", {}).get("id")
            lease = f"gh-{job['job_id']}"
            registration = {
                "lease": lease,
                "repo": repo,
                "runner_id": runner_id,
                "run_id": job["run_id"],
                "job_id": job["job_id"],
            }
            if isinstance(runner_id, int):
                store.write_cleanup_obligation(lease, registration, int(time.time()))
            if not isinstance(encoded, str) or not isinstance(runner_id, int):
                if isinstance(runner_id, int):
                    try:
                        client.delete_runner(repo, runner_id)
                    except RunnerError as exc:
                        LOG.error("deferring malformed GitHub runner cleanup repo=%s runner_id=%s error=%s",
                                  repo, runner_id, exc)
                        store.defer_cleanup(lease, registration, int(time.time()))
                    else:
                        store.remove_cleanup(registration)
                raise RunnerError("GitHub returned an invalid JIT response")
            jit_path = Path(cfg.get("runtime_dir", "/run/ci-runner-manager")) / f"{lease}.jit"
            try:
                jit_path.write_text(encoded, encoding="utf-8")
                os.chmod(jit_path, 0o600)
                launch(cfg, lease, jit_path, metadata={
                    "repo": repo,
                    "runner_id": runner_id,
                    "run_id": job["run_id"],
                    "job_id": job["job_id"],
                })
                store.remove_cleanup(registration)
                history.add(key, now)
                LOG.info("dispatched repo=%s run_id=%s job_id=%s", repo, job["run_id"], job["job_id"])
                return True
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
                        "run_id": job["run_id"],
                        "job_id": job["job_id"],
                    }, int(time.time()))
                else:
                    store.remove_cleanup(registration)
                raise
            finally:
                jit_path.unlink(missing_ok=True)
    return False


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
