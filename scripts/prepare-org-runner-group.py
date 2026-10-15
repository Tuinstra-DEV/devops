#!/usr/bin/env python3
"""Prepare a restricted group request offline. Never modifies GitHub or credentials."""
import argparse
import json
from pathlib import Path
import re


def prepare(workflow_sha: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", workflow_sha):
        raise ValueError("workflow SHA must be a full lowercase commit SHA")
    manifest = Path(__file__).resolve().parents[1] / "runner/policy/org-routing-repositories.json"
    repositories = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(repositories, dict) or not repositories:
        raise ValueError("repository manifest is empty or invalid")
    for name, repository_id in repositories.items():
        if not re.fullmatch(r"Tuinstra-DEV/[a-z0-9-]+", name) or name in (
                "Tuinstra-DEV/devops", "Tuinstra-DEV/agent-lab"):
            raise ValueError("unexpected repository in manifest")
        if type(repository_id) is not int or repository_id <= 0:
            raise ValueError("invalid repository ID")
    ids = list(repositories.values())
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate repository identity")
    return {
        "name": "sanctuary-trusted-verification",
        "visibility": "selected",
        "selected_repository_ids": sorted(ids),
        "allows_public_repositories": False,
        "restricted_to_workflows": True,
        "selected_workflows": [
            "Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@" + workflow_sha],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = prepare(args.workflow_sha)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    print(f"Prepared {len(payload['selected_repository_ids'])} selected repositories; no access changed")


if __name__ == "__main__":
    main()
