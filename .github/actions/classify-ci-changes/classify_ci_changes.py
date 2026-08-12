#!/usr/bin/env python3
"""Deterministic, fail-closed CI change classifier."""

from __future__ import annotations

import argparse
from datetime import date
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any

SCHEMA = "devops.ci-change-routing/v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
STAGES = (
    "backend", "frontend-static", "frontend-browser", "browser-integration",
    "containers", "php", "api-contract", "docker", "frontend",
)
FULL_CLASSES = {"runtime", "container", "contract", "security", "workflow"}
CONTROL_PLANE_PATTERNS = (
    ".github/workflows/**", ".github/actions/**", ".github/ci/**",
    ".github/CODEOWNERS", ".github/dependabot.yml", "renovate.json",
    "scripts/ci/**", "tests/ci/**", "runner/**", "infra/**",
    "**/*lock*", "**/package.json", "**/composer.json", "**/Dockerfile",
    "**/.dockerignore", "**/*compose*.yml", "**/*compose*.yaml",
    "**/.env*", "**/*auth*", "**/*secret*", "**/*credential*",
    "**/*security*", "**/*openapi*", "**/*release*", "**/*deploy*",
    "Makefile",
)


class ClassifierError(RuntimeError):
    pass


def safe_path(value: str) -> str:
    if not value or "\\" in value or value.startswith("/") or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ClassifierError("unsafe-path")
    parts = PurePosixPath(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ClassifierError("unsafe-path")
    return value


def matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    # `**/foo` must cover both `foo` at repository root and nested `a/foo`.
    # Python's fnmatch treats the slash literally, unlike GitHub's glob syntax.
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])
    )


def forced_path(path: str) -> bool:
    lowered = path.casefold()
    return any(matches(lowered, pattern.casefold()) for pattern in CONTROL_PLANE_PATTERNS)


def canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_policy(path: Path, repository: str | None = None, today: date | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClassifierError("invalid-policy") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA:
        raise ClassifierError("invalid-policy-schema")
    if not isinstance(payload.get("repository"), str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", payload["repository"]
    ):
        raise ClassifierError("invalid-policy-repository")
    if repository and payload["repository"].casefold() != repository.casefold():
        raise ClassifierError("policy-repository-mismatch")
    if not isinstance(payload.get("owner"), str) or not payload["owner"].strip():
        raise ClassifierError("missing-policy-owner")
    try:
        reviewed = date.fromisoformat(payload["reviewedAt"])
        expires = date.fromisoformat(payload["expiresAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ClassifierError("invalid-policy-lifetime") from exc
    if reviewed > expires or (today or date.today()) > expires:
        raise ClassifierError("stale-policy")
    classes = payload.get("classes")
    routes = payload.get("routes")
    if not isinstance(classes, list) or not classes or not isinstance(routes, dict):
        raise ClassifierError("invalid-policy-rules")
    names: set[str] = set()
    for rule in classes:
        if not isinstance(rule, dict) or rule.get("name") not in {
            "runtime", "frontend", "backend", "container", "contract",
            "security", "workflow", "documentation",
        }:
            raise ClassifierError("invalid-policy-class")
        name = rule["name"]
        if name in names:
            raise ClassifierError("duplicate-policy-class")
        names.add(name)
        patterns = rule.get("patterns")
        if not isinstance(patterns, list) or not patterns or any(
            not isinstance(pattern, str) or not pattern for pattern in patterns
        ):
            raise ClassifierError("invalid-policy-pattern")
    if names != {
        "runtime", "frontend", "backend", "container", "contract",
        "security", "workflow", "documentation",
    }:
        raise ClassifierError("incomplete-policy-classes")
    for name, route in routes.items():
        if name not in names or not isinstance(route, dict) or any(
            stage not in STAGES or not isinstance(enabled, bool)
            for stage, enabled in route.items()
        ):
            raise ClassifierError("invalid-policy-route")
    return payload


def parse_name_status_z(raw: bytes) -> list[dict[str, Any]]:
    if not raw:
        raise ClassifierError("missing-diff")
    try:
        tokens = raw.decode("utf-8", "strict").split("\0")
    except UnicodeDecodeError as exc:
        raise ClassifierError("invalid-diff-encoding") from exc
    if tokens[-1] != "":
        raise ClassifierError("malformed-diff")
    tokens.pop()
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if not re.fullmatch(r"[ACDMRTUXB][0-9]{0,3}", status):
            raise ClassifierError("malformed-diff-status")
        count = 2 if status.startswith(("R", "C")) else 1
        if index + count > len(tokens):
            raise ClassifierError("malformed-diff")
        paths = [safe_path(token) for token in tokens[index:index + count]]
        index += count
        records.append({"status": status, "paths": paths})
    return records


def git_diff(base_sha: str, head_sha: str) -> list[dict[str, Any]]:
    if not SHA_RE.fullmatch(base_sha) or not SHA_RE.fullmatch(head_sha):
        raise ClassifierError("invalid-sha")
    if base_sha == head_sha:
        raise ClassifierError("empty-sha-range")
    process = subprocess.run(
        ["git", "diff", "--name-status", "-z", base_sha, head_sha],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    if process.returncode != 0:
        raise ClassifierError("diff-unavailable")
    return parse_name_status_z(process.stdout)


def full_routes() -> dict[str, bool]:
    return {stage: True for stage in STAGES}


def classify(policy: dict[str, Any], records: list[dict[str, Any]], *, mode: str,
             force_full: bool, policy_digest: str) -> dict[str, Any]:
    paths = sorted({path for record in records for path in record["paths"]})
    status_records = sorted(records, key=lambda row: (row["paths"], row["status"]))
    if force_full:
        reason = "caller-forced-full"
    elif mode != "selective":
        reason = "central-full"
    else:
        reason = "selective"
    selected_classes: set[str] = set()
    unknown: list[str] = []
    control_plane: list[str] = []
    for path in paths:
        is_control_plane = forced_path(path)
        if is_control_plane:
            control_plane.append(path)
        matched = {
            rule["name"] for rule in policy["classes"]
            if any(matches(path, pattern) for pattern in rule["patterns"])
        }
        if not matched and not is_control_plane:
            unknown.append(path)
        selected_classes.update(matched)
    full_safe = force_full or mode != "selective" or bool(unknown or control_plane) or bool(
        selected_classes.intersection(FULL_CLASSES)
    )
    if unknown:
        reason = "unknown-path"
    elif control_plane:
        reason = "non-overridable-full-safe-path"
    elif selected_classes.intersection(FULL_CLASSES):
        reason = "full-safe-class"
    routes = full_routes() if full_safe else {stage: False for stage in STAGES}
    if not full_safe:
        for class_name in selected_classes:
            for stage, enabled in policy["routes"].get(class_name, {}).items():
                routes[stage] = routes[stage] or enabled
    evidence = {
        "schemaVersion": SCHEMA,
        "repository": policy["repository"],
        "decision": "full-safe" if full_safe else "selective",
        "reason": reason,
        "mode": mode,
        "classes": sorted(selected_classes),
        "routes": routes,
        "changedFiles": status_records,
        "unknownPaths": unknown,
        "controlPlanePaths": control_plane,
        "policyDigest": policy_digest,
    }
    evidence["evidenceDigest"] = canonical_digest(evidence)
    return evidence


def fail_closed(reason: str, mode: str) -> dict[str, Any]:
    evidence = {
        "schemaVersion": SCHEMA,
        "repository": None,
        "decision": "full-safe",
        "reason": reason,
        "mode": mode,
        "classes": [],
        "routes": full_routes(),
        "changedFiles": [],
        "unknownPaths": [],
        "controlPlanePaths": [],
        "policyDigest": None,
    }
    evidence["evidenceDigest"] = canonical_digest(evidence)
    return evidence


def write_outputs(evidence: dict[str, Any], evidence_path: Path,
                  github_output: Path | None, github_summary: Path | None) -> None:
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if github_output:
        lines = [
            f"decision={evidence['decision']}", f"reason={evidence['reason']}",
            f"classes={json.dumps(evidence['classes'], separators=(',', ':'))}",
            f"full-safe={'true' if evidence['decision'] == 'full-safe' else 'false'}",
            f"policy-digest={evidence['policyDigest'] or ''}",
            f"evidence-path={evidence_path}",
        ]
        lines.extend(
            f"run-{stage}={'true' if evidence['routes'][stage] else 'false'}"
            for stage in STAGES
        )
        with github_output.open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    if github_summary:
        with github_summary.open("a", encoding="utf-8") as stream:
            stream.write("## CI change routing\n\n```json\n")
            stream.write(json.dumps(evidence, indent=2, sort_keys=True))
            stream.write("\n```\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--mode", default="full")
    parser.add_argument("--force-full", default="false")
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    parser.add_argument("--github-summary")
    parser.add_argument("--repository")
    parser.add_argument("--today")
    parser.add_argument("--diff-fixture")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mode = args.mode.strip().casefold()
    try:
        if args.force_full not in {"true", "false"}:
            raise ClassifierError("invalid-force-full")
        policy = load_policy(
            Path(args.policy), args.repository,
            date.fromisoformat(args.today) if args.today else None,
        )
        digest = canonical_digest(policy)
        records = parse_name_status_z(Path(args.diff_fixture).read_bytes()) \
            if args.diff_fixture else git_diff(args.base_sha, args.head_sha)
        evidence = classify(
            policy, records, mode=mode,
            force_full=args.force_full == "true", policy_digest=digest,
        )
    except (ClassifierError, OSError, ValueError) as exc:
        evidence = fail_closed(str(exc) or "classifier-error", mode)
    write_outputs(
        evidence, Path(args.output),
        Path(args.github_output) if args.github_output else None,
        Path(args.github_summary) if args.github_summary else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
