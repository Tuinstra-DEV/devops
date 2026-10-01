#!/usr/bin/env python3
"""Build and submit a small, provenance-bound Gate PR-security receipt."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import constants
import source


MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_HTTP_RESPONSE_BYTES = 64 * 1024
MAX_ARCHIVE_DIGEST_BYTES = 128
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
ZIP_OVERHEAD_RESERVE = 4096
MAX_EXCLUDED_INPUTS_BYTES = 256 * 1024
MAX_EXCLUSION_BLOB_BYTES = 4 * 1024 * 1024
SHA_RE = re.compile(r"\A[a-f0-9]{40}\Z")
DIGEST_RE = re.compile(r"\Asha256:[a-f0-9]{64}\Z")
POSITIVE_RE = re.compile(r"\A[1-9][0-9]{0,18}\Z")
WORKFLOW_REF_MAX = 256
APPROVED_WORKFLOW_PATH = "Tuinstra-DEV/devops/.github/workflows/reusable-gate-pr-security.yml"
EVENT_NAMES = {"pull_request_target"}
OUTCOME_EXIT = {"pass": 0, "blocked": 1, "incomplete": 2}
TOOL_VERSIONS = {"semgrep": "1.136.0", "gitleaks": "8.30.1", "osv": "2.3.8", "gate-text": "1", "policy-exclusion": "1"}
SCOPE_SCANNERS = {
    "secrets": "gitleaks",
    "php": "semgrep",
    "javascript-typescript": "semgrep",
    "composer": "osv",
    "npm": "osv",
    "pnpm": "osv",
    "embedded-web": "semgrep",
    "configuration": "gate-text",
    "shell-infrastructure": "gate-text",
    "dockerfile": "gate-text",
    "web-assets": "gate-text",
    "template": "gate-text",
    "php-framework": "gate-text",
    "build-configuration": "gate-text",
    "opaque-input": "policy-exclusion",
    "unknown-input": "policy-exclusion",
    "embedded-code": "policy-exclusion",
}
PAIR_REQUIRED = {
    "schema_version", "document_type", "policy_version", "base_sha", "head_sha",
    "base_tree", "head_tree", "bundle_digest", "policy_digest",
    "dataset_manifest_digest", "outcome", "coverage", "findings", "inputs",
    "reasons", "uncompared_findings",
}
STATE_REQUIRED = {
    "repository", "repository_id", "owner_id", "pull_request", "run_id",
    "run_attempt", "base_sha", "head_sha", "base_tree", "head_tree", "scanner_exit",
    "prepare_duration_ms", "scan_duration_ms", "scanner_image",
}


class EvidenceError(Exception):
    """Safe error code only; never carries provider or report content."""

    def __init__(self, reason: str, exit_code: int = 2):
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def _decode_json(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceError(reason) from None
    if not isinstance(value, dict):
        raise EvidenceError(reason)
    return value


def _read_regular(path: Path, maximum: int, missing_reason: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError:
        raise EvidenceError(missing_reason) from None
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or metadata.st_size < 1 or metadata.st_size > maximum:
        raise EvidenceError("unsafe_or_oversized_file")
    try:
        with path.open("rb") as handle:
            raw = handle.read(maximum + 1)
    except OSError:
        raise EvidenceError("file_unreadable") from None
    if len(raw) != metadata.st_size or len(raw) > maximum:
        raise EvidenceError("file_changed_during_read")
    return raw


def _text(value: Any, pattern: re.Pattern[str], reason: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise EvidenceError(reason)
    return value


def _positive_int(value: Any, reason: str) -> int:
    if type(value) is not int or value < 1:
        raise EvidenceError(reason)
    return value


def _safe_work_dir(value: str | Path) -> Path:
    path = Path(value)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        raise EvidenceError("work_directory_unavailable") from None
    if path.is_symlink() or not metadata.st_mode or not resolved.is_dir():
        raise EvidenceError("unsafe_work_directory")
    return resolved


def _load_inputs(work_dir: Path) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    state_raw = _read_regular(work_dir / "state.json", 64 * 1024, "state_unavailable")
    state = _decode_json(state_raw, "invalid_state")
    if set(state) != STATE_REQUIRED:
        raise EvidenceError("invalid_state")
    report_path = work_dir / "output" / "pair" / "result.json"
    report_raw = _read_regular(report_path, MAX_REPORT_BYTES, "report_unavailable")
    report = _decode_json(report_raw, "invalid_report")
    checksum_path = report_path.with_name("result.sha256")
    checksum = _read_regular(checksum_path, 256, "report_checksum_unavailable")
    expected_checksum = f"{hashlib.sha256(report_raw).hexdigest()}  result.json\n".encode()
    if checksum != expected_checksum:
        raise EvidenceError("report_checksum_mismatch")
    return state, report_raw, report


def _validate_state(state: dict[str, Any]) -> None:
    if state["scanner_image"] != constants.IMAGE:
        raise EvidenceError("scanner_image_mismatch")
    repository = state["repository"]
    if not isinstance(repository, str) or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise EvidenceError("invalid_state")
    for key in ("repository_id", "owner_id", "run_id"):
        _text(state[key], POSITIVE_RE, "invalid_state")
    _positive_int(state["pull_request"], "invalid_state")
    _positive_int(state["run_attempt"], "invalid_state")
    if type(state["scanner_exit"]) is not int or state["scanner_exit"] not in (0, 1, 2):
        raise EvidenceError("invalid_state")
    for key in ("prepare_duration_ms", "scan_duration_ms"):
        if type(state[key]) is not int or state[key] < 0:
            raise EvidenceError("invalid_state")
    for key in ("base_sha", "head_sha", "base_tree", "head_tree"):
        _text(state[key], SHA_RE, "invalid_state")


def _coverage(report: dict[str, Any], complete_outcome: bool) -> list[dict[str, Any]]:
    coverage = report["coverage"]
    if not isinstance(coverage, list) or not coverage:
        raise EvidenceError("coverage_missing")
    scopes: set[str] = set()
    for row in coverage:
        if not isinstance(row, dict) or set(row) != {
            "scanner", "version", "scope", "rules_digest", "advisory_digest", "base_result", "head_result",
        }:
            raise EvidenceError("invalid_coverage")
        scanner = row["scanner"]
        scope = row["scope"]
        if not isinstance(scanner, str) or not isinstance(scope, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", scope) is None:
            raise EvidenceError("untrusted_coverage")
        if scope in scopes:
            raise EvidenceError("duplicate_coverage_scope")
        scopes.add(scope)
        if row["rules_digest"] != constants.BUNDLE_DIGEST:
            raise EvidenceError("untrusted_coverage")
        if not isinstance(row["base_result"], str) or row["base_result"] not in {"complete", "failed", "unsupported"} or not isinstance(row["head_result"], str) or row["head_result"] not in {"complete", "failed", "unsupported"}:
            raise EvidenceError("invalid_coverage")
        if scanner == "unsupported":
            if complete_outcome or row["version"] != "unregistered" or row["advisory_digest"] is not None or row["base_result"] != "unsupported" or row["head_result"] != "unsupported":
                raise EvidenceError("untrusted_coverage")
            continue
        # This image retains the OSV adapter identity for unsupported Yarn.
        # Preserve its nonpassing report; never treat it as supported coverage.
        unsupported_yarn = (not complete_outcome and scope == "yarn" and scanner == "osv"
                            and row["base_result"] == "unsupported" and row["head_result"] == "unsupported")
        if ((SCOPE_SCANNERS.get(scope) != scanner and not unsupported_yarn)
            or scanner not in TOOL_VERSIONS or row["version"] != TOOL_VERSIONS[scanner]):
            raise EvidenceError("untrusted_coverage")
        expected_advisory = constants.BUNDLE_DIGEST if "osv" == scanner else None
        if row["advisory_digest"] != expected_advisory:
            raise EvidenceError("untrusted_coverage")
        if complete_outcome and (row["base_result"] != "complete" or row["head_result"] != "complete"):
            raise EvidenceError("outcome_coverage_mismatch")
    if "secrets" not in scopes:
        raise EvidenceError("mandatory_secrets_coverage_missing")
    return coverage


def _validate_findings(findings: Any) -> None:
    if not isinstance(findings, list):
        raise EvidenceError("invalid_findings")
    fingerprints: set[str] = set()
    severities = {None, "info", "low", "medium", "high", "critical"}
    changes = {"new", "worsened", "existing", "resolved"}
    for row in findings:
        if not isinstance(row, dict) or set(row) != {
            "fingerprint", "rule_id", "category", "path", "change", "base_severity", "head_severity", "exception_id",
        }:
            raise EvidenceError("invalid_findings")
        fingerprint = _text(row["fingerprint"], DIGEST_RE, "invalid_findings")
        if fingerprint in fingerprints:
            raise EvidenceError("duplicate_finding")
        fingerprints.add(fingerprint)
        if (not isinstance(row["change"], str) or row["change"] not in changes
            or (row["base_severity"] is not None and (not isinstance(row["base_severity"], str) or row["base_severity"] not in severities))
            or (row["head_severity"] is not None and (not isinstance(row["head_severity"], str) or row["head_severity"] not in severities))):
            raise EvidenceError("invalid_findings")
        if (not isinstance(row["rule_id"], str) or not row["rule_id"] or len(row["rule_id"]) > 256
            or not isinstance(row["category"], str) or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", row["category"]) is None
            or not isinstance(row["path"], str) or not row["path"] or len(row["path"]) > 512
            or re.fullmatch(r"[A-Za-z0-9._@+/-]+", row["path"]) is None
            or row["path"].startswith("/") or "\\" in row["path"] or any(part in {"", ".", ".."} for part in row["path"].split("/"))):
            raise EvidenceError("invalid_findings")
        if row["exception_id"] is not None and not isinstance(row["exception_id"], str):
            raise EvidenceError("invalid_findings")
        base, head, change = row["base_severity"], row["head_severity"], row["change"]
        weights = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        valid_change = (
            (change == "new" and base is None and isinstance(head, str))
            or (change == "resolved" and isinstance(base, str) and head is None)
            or (change == "existing" and isinstance(base, str) and isinstance(head, str) and weights[head] <= weights[base])
            or (change == "worsened" and isinstance(base, str) and isinstance(head, str) and weights[head] > weights[base])
        )
        if not valid_change:
            raise EvidenceError("invalid_findings")
        exception_id = row["exception_id"]
        if exception_id is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", exception_id) is None:
            raise EvidenceError("invalid_findings")


def _inputs(report: dict[str, Any], coverage: list[dict[str, Any]], complete_outcome: bool) -> None:
    entries = report["inputs"]
    if not isinstance(entries, list):
        raise EvidenceError("invalid_inputs")
    by_scope: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"scope", "base", "head"} or not isinstance(entry.get("scope"), str):
            raise EvidenceError("invalid_inputs")
        scope = entry["scope"]
        if scope in by_scope:
            raise EvidenceError("duplicate_input_scope")
        by_scope[scope] = entry
    if set(by_scope) != {row["scope"] for row in coverage}:
        raise EvidenceError("input_coverage_mismatch")
    coverage_by_scope = {row["scope"]: row for row in coverage}
    for entry in entries:
        for side in ("base", "head"):
            data = entry[side]
            if not isinstance(data, dict) or set(data) != {"discovered", "scanned", "reasons"}:
                raise EvidenceError("invalid_inputs")
            discovered, scanned, reasons = data["discovered"], data["scanned"], data["reasons"]
            if not all(isinstance(value, list) for value in (discovered, scanned, reasons)):
                raise EvidenceError("invalid_inputs")
            if any(not isinstance(path, str) or not path or len(path) > 512 or path.startswith("/") or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")) for path in discovered + scanned):
                raise EvidenceError("invalid_inputs")
            if len(discovered) != len(set(discovered)) or len(scanned) != len(set(scanned)):
                raise EvidenceError("invalid_inputs")
            if any(not isinstance(reason, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,80}", reason) is None for reason in reasons):
                raise EvidenceError("invalid_inputs")
            verified_absence = not discovered and not scanned and reasons == ["verified_absence"]
            policy_exclusion = coverage_by_scope[entry["scope"]]["scanner"] == "policy-exclusion"
            approved_exclusion = (policy_exclusion and bool(discovered) and discovered == scanned
                                  and reasons == ["approved_exclusion"])
            if "approved_exclusion" in reasons and not approved_exclusion:
                raise EvidenceError("outcome_input_mismatch")
            if (policy_exclusion and coverage_by_scope[entry["scope"]][side + "_result"] == "complete"
                and not verified_absence and not approved_exclusion):
                raise EvidenceError("outcome_input_mismatch")
            if complete_outcome and (not discovered and not verified_absence
                                     or discovered != scanned
                                     or reasons and not verified_absence and not approved_exclusion):
                raise EvidenceError("outcome_input_mismatch")


def _trusted_exclusions() -> dict[tuple[str, str], set[str]]:
    path = Path(__file__).with_name("excluded-inputs.json")
    raw = _read_regular(path, MAX_EXCLUDED_INPUTS_BYTES, "exclusion_allowlist_unavailable")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != constants.EXCLUDED_INPUTS_DIGEST:
        raise EvidenceError("exclusion_allowlist_mismatch")
    document = _decode_json(raw, "exclusion_allowlist_invalid")
    if set(document) != {"policy_digest", "excluded_inputs"} or document["policy_digest"] != constants.POLICY_DIGEST:
        raise EvidenceError("exclusion_allowlist_invalid")
    entries = document["excluded_inputs"]
    if not isinstance(entries, list) or len(entries) > 1000:
        raise EvidenceError("exclusion_allowlist_invalid")
    allowlist: dict[tuple[str, str], set[str]] = {}
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"scope", "path", "sha256"}:
            raise EvidenceError("exclusion_allowlist_invalid")
        scope, path, digest = entry["scope"], entry["path"], entry["sha256"]
        if (not isinstance(scope, str) or scope not in {"opaque-input", "unknown-input", "embedded-code"}
            or not isinstance(path, str) or re.fullmatch(r"[A-Za-z0-9._@+/-]{1,512}", path) is None
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None):
            raise EvidenceError("exclusion_allowlist_invalid")
        key = (scope, path, digest)
        if key in seen:
            raise EvidenceError("exclusion_allowlist_invalid")
        seen.add(key)
        allowlist.setdefault((scope, path), set()).add(digest)
    return allowlist


def _source_exclusions(root: Path, state: dict[str, Any], report: dict[str, Any]) -> list[dict[str, str]]:
    """Bind every approved policy exclusion to exact base/head Git blobs."""
    candidates: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    coverage = {row["scope"]: row for row in report["coverage"]}
    for entry in report["inputs"]:
        scope = entry["scope"]
        if coverage[scope]["scanner"] != "policy-exclusion":
            for side in ("base", "head"):
                if "approved_exclusion" in entry[side]["reasons"]:
                    raise EvidenceError("untrusted_exclusion_scope")
            continue
        for side in ("base", "head"):
            data = entry[side]
            discovered, scanned, reasons = data["discovered"], data["scanned"], data["reasons"]
            if not discovered and not scanned and reasons == ["verified_absence"]:
                continue
            if coverage[scope][side + "_result"] != "complete" and "approved_exclusion" not in reasons:
                continue
            if not discovered or discovered != scanned or reasons != ["approved_exclusion"]:
                raise EvidenceError("untrusted_exclusion_reason")
            for path in discovered:
                candidate = (scope, side, path)
                if candidate in seen:
                    raise EvidenceError("duplicate_exclusion_input")
                seen.add(candidate)
                candidates.append(candidate)
                if len(candidates) > 1000:
                    raise EvidenceError("exclusion_count_limit")
    if not candidates:
        return []

    allowlist = _trusted_exclusions()
    for scope, _, path in candidates:
        if (scope, path) not in allowlist:
            raise EvidenceError("unregistered_exclusion")

    store = root / "objects.git"
    try:
        store_metadata = store.lstat()
    except OSError:
        raise EvidenceError("exclusion_source_unavailable") from None
    if store.is_symlink() or not store.is_dir() or not store_metadata.st_mode:
        raise EvidenceError("exclusion_source_unavailable")
    try:
        if any(path.is_symlink() for path in store.rglob("*")):
            raise EvidenceError("exclusion_source_unavailable")
    except OSError:
        raise EvidenceError("exclusion_source_unavailable") from None
    for name in ("alternates", "http-alternates"):
        if (store / "objects" / "info" / name).exists():
            raise EvidenceError("exclusion_source_unavailable")
    if source.storage_bytes(store) > source.MAX_OBJECT_STORE:
        raise EvidenceError("exclusion_source_unavailable")

    trees: dict[str, str] = {}
    for side in ("base", "head"):
        commit = state[side + "_sha"]
        try:
            object_type = source.git(store, ["cat-file", "-t", commit], max_bytes=128).strip()
            tree = source.git(store, ["rev-parse", "--verify", commit + "^{tree}"]).decode("ascii").strip()
        except (source.Failure, UnicodeError):
            raise EvidenceError("exclusion_source_unavailable") from None
        if object_type != b"commit" or SHA_RE.fullmatch(tree) is None or tree != state[side + "_tree"]:
            raise EvidenceError("exclusion_tree_mismatch")
        trees[side] = tree

    exclusions: list[dict[str, str]] = []
    for scope, side, path in sorted(candidates, key=lambda item: (item[0], item[1] != "base", item[2])):
        try:
            listing = source.git(store, ["ls-tree", "-rlz", trees[side], "--", path], max_bytes=64 * 1024)
        except source.Failure:
            raise EvidenceError("exclusion_source_unavailable") from None
        match = re.fullmatch(rb"(100644|100755) blob ([a-f0-9]{40}) +([0-9]+)\t([^\x00]+)\x00", listing)
        if match is None or match[4].decode("utf-8", "strict") != path:
            raise EvidenceError("exclusion_blob_mismatch")
        object_id = match[2].decode("ascii")
        size = int(match[3])
        if size > MAX_EXCLUSION_BLOB_BYTES:
            raise EvidenceError("exclusion_blob_too_large")
        try:
            contents = source.git(store, ["cat-file", "blob", object_id], max_bytes=MAX_EXCLUSION_BLOB_BYTES + 1)
        except source.Failure:
            raise EvidenceError("exclusion_source_unavailable") from None
        if (len(contents) != size
            or hashlib.sha1(b"blob " + str(size).encode("ascii") + b"\0" + contents).hexdigest() != object_id):
            raise EvidenceError("exclusion_blob_mismatch")
        digest = "sha256:" + hashlib.sha256(contents).hexdigest()
        if digest not in allowlist[(scope, path)]:
            raise EvidenceError("untrusted_exclusion_blob")
        exclusions.append({
            "scope": scope,
            "side": side,
            "path": path,
            "source_sha256": digest,
            "reason_id": "approved_exclusion",
        })
    return exclusions


def _validate_report(state: dict[str, Any], report: dict[str, Any]) -> None:
    _validate_state(state)
    if set(report) != PAIR_REQUIRED or report.get("schema_version") != "1.0" or report.get("document_type") != "ci-pair-result" or report.get("policy_version") != "gate-assurance-1":
        raise EvidenceError("invalid_report")
    for key in ("base_sha", "head_sha", "base_tree", "head_tree"):
        _text(report[key], SHA_RE, "report_provenance_mismatch")
        if report[key] != state[key]:
            raise EvidenceError("report_provenance_mismatch")
    if report["bundle_digest"] != constants.BUNDLE_DIGEST or report["dataset_manifest_digest"] != constants.DATASET_DIGEST or report["policy_digest"] != constants.POLICY_DIGEST:
        raise EvidenceError("report_profile_mismatch")
    outcome = report["outcome"]
    if not isinstance(outcome, str) or outcome not in OUTCOME_EXIT:
        raise EvidenceError("invalid_report")
    if state["scanner_exit"] != OUTCOME_EXIT[outcome]:
        raise EvidenceError("scanner_exit_mismatch")
    complete = outcome in {"pass", "blocked"}
    coverage = _coverage(report, complete)
    _validate_findings(report["findings"])
    _inputs(report, coverage, complete)
    if not isinstance(report["reasons"], list) or not isinstance(report["uncompared_findings"], list):
        raise EvidenceError("invalid_report")
    if any(not isinstance(reason, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,80}", reason) is None for reason in report["reasons"]):
        raise EvidenceError("invalid_report")
    for finding in report["uncompared_findings"]:
        if not isinstance(finding, dict) or set(finding) != {"side", "scope", "rule_id", "category", "path", "fingerprint", "severity"}:
            raise EvidenceError("invalid_report")
        if finding["side"] not in {"base", "head"} or not isinstance(finding["scope"], str) or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", finding["scope"]) is None:
            raise EvidenceError("invalid_report")
        if not isinstance(finding["rule_id"], str) or not finding["rule_id"] or len(finding["rule_id"]) > 256:
            raise EvidenceError("invalid_report")
        if not isinstance(finding["category"], str) or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", finding["category"]) is None:
            raise EvidenceError("invalid_report")
        if not isinstance(finding["path"], str) or re.fullmatch(r"[A-Za-z0-9._@+/-]+", finding["path"]) is None or any(part in {"", ".", ".."} for part in finding["path"].split("/")):
            raise EvidenceError("invalid_report")
        _text(finding["fingerprint"], DIGEST_RE, "invalid_report")
        if not isinstance(finding["severity"], str) or finding["severity"] not in {"info", "low", "medium", "high", "critical"}:
            raise EvidenceError("invalid_report")
    if complete and (report["reasons"] or report["uncompared_findings"]):
        raise EvidenceError("outcome_evidence_mismatch")
    if not complete and not report["reasons"] and not report["uncompared_findings"] and all(
        row["base_result"] == "complete" and row["head_result"] == "complete" for row in coverage
    ):
        raise EvidenceError("incomplete_without_cause")
    blocking = any(
        finding["change"] in {"new", "worsened"}
        and finding["head_severity"] in {"high", "critical"}
        and finding["exception_id"] is None
        for finding in report["findings"]
    )
    if complete and ((outcome == "blocked") != blocking):
        raise EvidenceError("outcome_findings_mismatch")


def _load_context(environment: dict[str, str]) -> dict[str, str]:
    required = (
        "GATE_WORKFLOW_REF", "GATE_WORKFLOW_SHA", "GATE_CHECK_RUN_ID", "GATE_EVENT_NAME",
        "GATE_EXECUTION_REF", "GATE_EXECUTION_SHA",
    )
    context: dict[str, str] = {}
    for name in required:
        value = environment.get(name, "")
        if not value or len(value) > 512 or any(ord(char) < 32 for char in value):
            raise EvidenceError("identity_unavailable")
        context[name] = value
    if context["GATE_EVENT_NAME"] not in EVENT_NAMES:
        raise EvidenceError("event_not_allowed")
    _text(context["GATE_WORKFLOW_SHA"], SHA_RE, "identity_invalid")
    _text(context["GATE_EXECUTION_SHA"], SHA_RE, "identity_invalid")
    _text(context["GATE_CHECK_RUN_ID"], POSITIVE_RE, "identity_invalid")
    expected_ref = APPROVED_WORKFLOW_PATH + "@" + context["GATE_WORKFLOW_SHA"]
    if len(context["GATE_WORKFLOW_REF"]) > WORKFLOW_REF_MAX or context["GATE_WORKFLOW_REF"] != expected_ref:
        raise EvidenceError("identity_invalid")
    return context


def _outer_evidence(state: dict[str, Any], report_raw: bytes, report: dict[str, Any], context: dict[str, str], exclusions: list[dict[str, str]]) -> bytes:
    evidence = {
        "schema_version": "1.1",
        "policy_version": "gate-assurance-1",
        "document_type": "pr-security-evidence",
        "evidence_mode": "live",
        "repository": {
            "repository": state["repository"],
            "repository_id": state["repository_id"],
            "owner_id": state["owner_id"],
        },
        "context": "pull_request",
        "pull_requests": [state["pull_request"]],
        "base_sha": state["base_sha"],
        "head_sha": state["head_sha"],
        "run": {
            "id": state["run_id"],
            "attempt": state["run_attempt"],
            "workflow_ref": context["GATE_WORKFLOW_REF"],
            "workflow_sha": context["GATE_WORKFLOW_SHA"],
        },
        "policy_digest": constants.POLICY_DIGEST,
        "scanner_image": constants.IMAGE,
        "dataset_manifest_digest": constants.DATASET_DIGEST,
        "coverage": report["coverage"],
        "findings": report["findings"],
        "exclusions": exclusions,
        "payload_files": [{
            "path": "reports/result.json",
            "sha256": "sha256:" + hashlib.sha256(report_raw).hexdigest(),
        }],
        "outcome": report["outcome"],
    }
    return (json.dumps(evidence, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")


def package(work_dir: str | Path, environment: dict[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    root = _safe_work_dir(work_dir)
    state, report_raw, report = _load_inputs(root)
    _validate_report(state, report)
    exclusions = _source_exclusions(root, state, report)
    context = _load_context(environment)
    staging = root / "staging"
    if staging.exists() or staging.is_symlink():
        raise EvidenceError("staging_already_exists")
    evidence_raw = _outer_evidence(state, report_raw, report, context, exclusions)
    if len(report_raw) + len(evidence_raw) + ZIP_OVERHEAD_RESERVE > MAX_PACKAGE_BYTES:
        raise EvidenceError("package_size_limit")
    staging.mkdir(mode=0o700)
    reports = staging / "reports"
    reports.mkdir(mode=0o700)
    try:
        (staging / "evidence.json").write_bytes(evidence_raw)
        (reports / "result.json").write_bytes(report_raw)
        for path in (staging / "evidence.json", reports / "result.json"):
            path.chmod(0o400)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        raise EvidenceError("staging_write_failed") from None
    return staging


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _oidc_url(raw_url: str) -> str:
    try:
        parsed = urlsplit(raw_url)
        if parsed.scheme != "https" or parsed.hostname != "pipelines.actions.githubusercontent.com" or parsed.username or parsed.password or parsed.port:
            raise EvidenceError("oidc_url_rejected")
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if any(key == "audience" for key, _ in query):
            raise EvidenceError("oidc_url_rejected")
        query.append(("audience", constants.AUDIENCE))
        return urlunsplit(("https", parsed.netloc, parsed.path, urlencode(query), ""))
    except ValueError:
        raise EvidenceError("oidc_url_rejected") from None


def _decode_token_claims(token: str) -> dict[str, Any]:
    pieces = token.split(".")
    if len(pieces) != 3 or not pieces[1]:
        raise EvidenceError("oidc_token_invalid")
    try:
        payload = pieces[1] + "=" * (-len(pieces[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = _decode_json(decoded, "oidc_token_invalid")
    except (ValueError, UnicodeEncodeError):
        raise EvidenceError("oidc_token_invalid") from None
    if len(decoded) > MAX_HTTP_RESPONSE_BYTES:
        raise EvidenceError("oidc_token_invalid")
    return claims


def _check_claims(claims: dict[str, Any], state: dict[str, Any], context: dict[str, str]) -> None:
    aud = claims.get("aud")
    if claims.get("iss") != "https://token.actions.githubusercontent.com" or aud != constants.AUDIENCE:
        raise EvidenceError("oidc_identity_mismatch")
    expected = {
        "repository": state["repository"],
        "repository_id": state["repository_id"],
        "repository_owner_id": state["owner_id"],
        "run_id": state["run_id"],
        "run_attempt": str(state["run_attempt"]),
        "job_workflow_ref": context["GATE_WORKFLOW_REF"],
        "job_workflow_sha": context["GATE_WORKFLOW_SHA"],
        "check_run_id": context["GATE_CHECK_RUN_ID"],
        "event_name": context["GATE_EVENT_NAME"],
        "ref": context["GATE_EXECUTION_REF"],
        "sha": context["GATE_EXECUTION_SHA"],
    }
    for claim, value in expected.items():
        if claims.get(claim) != value:
            raise EvidenceError("oidc_identity_mismatch")


def _read_http_body(response: Any) -> bytes:
    raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
    if len(raw) > MAX_HTTP_RESPONSE_BYTES:
        raise EvidenceError("provider_response_too_large")
    return raw


def _fetch_oidc_token(environment: dict[str, str], opener: Any | None = None) -> str:
    raw_url = environment.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = environment.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not raw_url or not request_token or len(request_token) > 16_384:
        raise EvidenceError("oidc_token_unavailable")
    url = _oidc_url(raw_url)
    request = Request(url, headers={"Authorization": "Bearer " + request_token, "Accept": "application/json"}, method="GET")
    opener = build_opener(_NoRedirect()) if opener is None else opener
    try:
        with opener.open(request, timeout=10) as response:
            if response.status != 200:
                raise EvidenceError("oidc_token_request_failed")
            body = _read_http_body(response)
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("oidc_token_request_failed", 1) from None
    value = _decode_json(body, "oidc_token_response_invalid").get("value")
    if not isinstance(value, str) or len(value) > 16_384:
        raise EvidenceError("oidc_token_response_invalid")
    return value


def _post_receipt(body: bytes, token: str, opener: Any) -> None:
    parsed = urlsplit(constants.ENDPOINT)
    if parsed.scheme != "https" or parsed.netloc != "gate.tuinstra.dev" or parsed.path != "/integrations/github/pr-security/receipts" or parsed.query or parsed.fragment:
        raise EvidenceError("publisher_endpoint_invalid")
    request = Request(
        constants.ENDPOINT,
        data=body,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with opener.open(request, timeout=15) as response:
                status = response.status
                response_body = _read_http_body(response)
            if status == 202:
                receipt = _decode_json(response_body, "receipt_response_invalid")
                receipt_id = receipt.get("receiptId")
                if receipt.get("status") != "accepted" or not isinstance(receipt_id, str) or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", receipt_id) is None:
                    raise EvidenceError("receipt_response_invalid")
                return
            if status in {408, 425, 429} or 500 <= status <= 599:
                if attempt < 2:
                    time.sleep(0.15 * (attempt + 1))
                    continue
                raise EvidenceError("publisher_temporarily_unavailable", 1)
            raise EvidenceError("receipt_rejected")
        except HTTPError as error:
            status = error.code
            error.close()
            if status in {408, 425, 429} or 500 <= status <= 599:
                if attempt < 2:
                    time.sleep(0.15 * (attempt + 1))
                    continue
                raise EvidenceError("publisher_temporarily_unavailable", 1) from None
            raise EvidenceError("receipt_rejected") from None
        except (URLError, TimeoutError, OSError):
            if attempt < 2:
                time.sleep(0.15 * (attempt + 1))
                continue
            raise EvidenceError("publisher_temporarily_unavailable", 1) from None
    raise EvidenceError("publisher_temporarily_unavailable", 1)


def submit(work_dir: str | Path, environment: dict[str, str] | None = None) -> None:
    environment = os.environ if environment is None else environment
    root = _safe_work_dir(work_dir)
    state, report_raw, report = _load_inputs(root)
    _validate_report(state, report)
    exclusions = _source_exclusions(root, state, report)
    context = _load_context(environment)
    staging = root / "staging"
    if staging.is_symlink() or not staging.is_dir():
        raise EvidenceError("staging_unavailable")
    evidence_path = staging / "evidence.json"
    packaged_result = _read_regular(staging / "reports" / "result.json", MAX_REPORT_BYTES, "staging_unavailable")
    evidence_raw = _read_regular(evidence_path, MAX_REPORT_BYTES, "staging_unavailable")
    if packaged_result != report_raw:
        raise EvidenceError("staging_report_changed")
    evidence = _decode_json(evidence_raw, "staging_evidence_invalid")
    expected_evidence = _outer_evidence(state, report_raw, report, context, exclusions)
    if evidence_raw != expected_evidence:
        raise EvidenceError("staging_evidence_changed")
    artifact_id = _text(environment.get("GATE_ARTIFACT_ID"), POSITIVE_RE, "artifact_identity_invalid")
    artifact_digest = _text(environment.get("GATE_ARTIFACT_DIGEST"), DIGEST_RE, "artifact_identity_invalid")
    opener = build_opener(_NoRedirect())
    token = _fetch_oidc_token(environment, opener)
    claims = _decode_token_claims(token)
    _check_claims(claims, state, context)
    body = json.dumps({
        "repositoryId": state["repository_id"],
        "pullRequest": state["pull_request"],
        "baseSha": state["base_sha"],
        "headSha": state["head_sha"],
        "artifactId": artifact_id,
        "artifactDigest": artifact_digest,
    }, separators=(",", ":")).encode("utf-8")
    _post_receipt(body, token, opener)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("package", "submit"))
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "package":
            package(args.work_dir)
        else:
            submit(args.work_dir)
        return 0
    except EvidenceError as error:
        print("gate-pr-security: " + error.reason, file=sys.stderr)
        return error.exit_code
    except Exception:
        print("gate-pr-security: unexpected_failure", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
