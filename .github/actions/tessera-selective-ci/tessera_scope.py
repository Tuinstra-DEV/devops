#!/usr/bin/env python3
"""Validate a current Tessera PR and classify its trusted Git diff."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Callable

REPOSITORY = "Tuinstra-DEV/sudoku-tessera"
RUBY_PATHS = {
    "app/Gemfile",
    "app/Gemfile.lock",
    "app/fastlane/Fastfile",
}
SHA = re.compile(r"[0-9a-f]{40}\Z")


class ScopeError(ValueError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ScopeError(reason)


def classify(paths: set[str], base_ref: str) -> str:
    """Release PRs and every unknown/mixed/empty diff stay on full Flutter CI."""
    return "ruby-lite" if base_ref == "develop" and paths and paths <= RUBY_PATHS else "full"


def verified_paths(repo_path: Path, base_sha: str, head_sha: str, source_sha: str) -> set[str]:
    require(all(SHA.fullmatch(value or "") for value in (base_sha, head_sha, source_sha)), "commit SHA")
    parents = subprocess.run(
        ["git", "-C", str(repo_path), "rev-list", "--parents", "-n", "1", source_sha],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    require(len(parents) == 3 and parents[0] == source_sha and set(parents[1:]) == {base_sha, head_sha},
            "source is not the exact two-parent PR merge")
    changed = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--no-renames", "--name-only", "-z", base_sha, head_sha],
        check=True,
        capture_output=True,
    ).stdout
    return {path.decode("utf-8", "surrogateescape") for path in changed.split(b"\0") if path}


def validate_pr(event: dict[str, Any], env: dict[str, str], api_get: Callable[[str], dict[str, Any]]) -> tuple[str, str, str, str]:
    require(isinstance(event, dict), "PR event shape")
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY and env.get("GITHUB_EVENT_NAME") == "pull_request",
            "repository or event")
    pull = event.get("pull_request") or {}
    number = pull.get("number")
    head, base = pull.get("head") or {}, pull.get("base") or {}
    head_sha, base_sha, source_sha = head.get("sha"), base.get("sha"), env.get("GITHUB_SHA")
    require(type(number) is int and number > 0 and
            all(isinstance(value, str) and SHA.fullmatch(value) for value in (head_sha, base_sha, source_sha)),
            "PR identity")
    require((event.get("repository") or {}).get("full_name") == REPOSITORY and
            (event.get("repository") or {}).get("private") is True and
            (base.get("repo") or {}).get("full_name") == REPOSITORY and
            base.get("ref") in ("develop", "main"), "base repository")
    require(env.get("GITHUB_REF") == f"refs/pull/{number}/merge", "PR merge ref")

    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]*", run_id) is not None and
            re.fullmatch(r"[1-9][0-9]*", attempt) is not None, "run identity")
    run = api_get(f"/repos/{REPOSITORY}/actions/runs/{run_id}")
    require(run.get("id") == int(run_id) and run.get("run_attempt") == int(attempt) and
            run.get("event") == "pull_request" and run.get("head_sha") == head_sha and
            run.get("head_branch") == head.get("ref") and
            (run.get("repository") or {}).get("full_name") == REPOSITORY, "Actions run record")
    current = api_get(f"/repos/{REPOSITORY}/pulls/{number}")
    current_head, current_base = current.get("head") or {}, current.get("base") or {}
    require(current.get("number") == number and current.get("state") == "open" and
            current.get("merged") is False and current_head.get("sha") == head_sha and
            current_head.get("ref") == head.get("ref") and current_base.get("sha") == base_sha and
            (current_base.get("repo") or {}).get("full_name") == REPOSITORY and
            current_base.get("ref") == base.get("ref"), "current open PR")
    merge = api_get(f"/repos/{REPOSITORY}/git/commits/{source_sha}")
    parents = merge.get("parents") or []
    require(merge.get("sha") == source_sha and len(parents) == 2 and
            {parent.get("sha") for parent in parents} == {head_sha, base_sha}, "merge parent API proof")
    return base_sha, head_sha, source_sha, base["ref"]


def github_api_get(path: str) -> dict[str, Any]:
    url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/") + path
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ScopeError("GitHub API returned an invalid response")
    return payload


def main() -> int:
    try:
        with Path(os.environ["GITHUB_EVENT_PATH"]).open(encoding="utf-8") as event_file:
            event = json.load(event_file)
        base_sha, head_sha, source_sha, base_ref = validate_pr(event, os.environ, github_api_get)
        paths = verified_paths(Path(os.environ["GITHUB_WORKSPACE"]), base_sha, head_sha, source_sha)
        scope = classify(paths, base_ref)
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"scope={scope}\n")
        print(scope)
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError,
            urllib.error.URLError, subprocess.CalledProcessError) as error:
        # Keep errors bounded and avoid exposing API bodies or environment values.
        print(f"Tessera CI source verification failed: {type(error).__name__}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
