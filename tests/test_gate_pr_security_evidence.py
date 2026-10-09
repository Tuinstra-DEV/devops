import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).parents[1]
ACTION = ROOT / ".github/actions/gate-pr-security"
sys.path.insert(0, str(ACTION))
SPEC = importlib.util.spec_from_file_location("gate_pr_security_evidence", ACTION / "evidence.py")
evidence = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(evidence)


def environment():
    return {
        "GATE_WORKFLOW_REF": evidence.APPROVED_WORKFLOW_PATH + "@" + "f" * 40,
        "GATE_WORKFLOW_SHA": "f" * 40,
        "GATE_CHECK_RUN_ID": "44332211",
        "GATE_EVENT_NAME": "pull_request_target",
        "GATE_EXECUTION_REF": "refs/heads/main",
        "GATE_EXECUTION_SHA": "9" * 40,
    }


def state(scanner_exit):
    return {
        "repository": "Tuinstra-DEV/gate",
        "repository_id": "100100",
        "owner_id": "200200",
        "pull_request": 73,
        "run_id": "300300",
        "run_attempt": 2,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "base_tree": "c" * 40,
        "head_tree": "d" * 40,
        "scanner_image": evidence.constants.IMAGE,
        "scanner_exit": scanner_exit,
        "prepare_duration_ms": 11,
        "scan_duration_ms": 22,
    }


def setup_work(work: Path, fixture: str, exit_code: int | None = None):
    work.mkdir()
    (work / "output/pair").mkdir(parents=True)
    report_path = ROOT / "tests/fixtures/gate-pr-security" / fixture
    report = current_profile(json.loads(report_path.read_bytes()))
    raw = (json.dumps(report, indent=2) + "\n").encode()
    code = {"pass": 0, "blocked": 1, "incomplete": 2}[report["outcome"]] if exit_code is None else exit_code
    (work / "state.json").write_text(json.dumps(state(code)), encoding="utf-8")
    (work / "output/pair/result.json").write_bytes(raw)
    (work / "output/pair/result.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  result.json\n", encoding="ascii")
    return raw, report


def write_report(work: Path, report: dict) -> bytes:
    """Keep the scanner's checksum valid when testing report-contract failures."""
    raw = (json.dumps(report, indent=2) + "\n").encode()
    (work / "output/pair/result.json").write_bytes(raw)
    (work / "output/pair/result.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  result.json\n", encoding="ascii")
    return raw


def current_profile(report: dict) -> dict:
    """Keep synthetic cases tied to the verified deployment constants under test."""
    report["bundle_digest"] = evidence.constants.BUNDLE_DIGEST
    report["policy_digest"] = evidence.constants.POLICY_DIGEST
    report["dataset_manifest_digest"] = evidence.constants.DATASET_DIGEST
    for row in report["coverage"]:
        row["rules_digest"] = evidence.constants.BUNDLE_DIGEST
        if row["scanner"] == "osv":
            row["advisory_digest"] = evidence.constants.BUNDLE_DIGEST
    return report


def content_bound_record(rule="GATE-TEXT-WORKFLOW-TRUST", identifier="IN-3-workflow-trust"):
    workflow = ".github/workflows/gate-heal-qa-gate.yml"
    fingerprint = "sha256:" + hashlib.sha256("\0".join((
        "gate-text", rule, "security", workflow, rule.lower(),
    )).encode()).hexdigest()
    return {
        "id": identifier, "fingerprint": fingerprint, "repository": "Tuinstra-DEV/gate",
        "rule_id": rule, "path": workflow, "expires_at": "2030-01-01T00:00:00Z",
        "owner": "security-team", "reason": "Synthetic bounded protocol fixture.",
        "inputs": [
            {"path": workflow, "sha256": "sha256:" + "a" * 64},
            {"path": "scripts/ci/heal-qa-bridge.mjs", "sha256": "sha256:" + "b" * 64},
        ],
    }


def coverage_row(scope: str, scanner: str, version: str) -> dict:
    return {
        "scanner": scanner, "version": version, "scope": scope,
        "rules_digest": evidence.constants.BUNDLE_DIGEST,
        "advisory_digest": evidence.constants.BUNDLE_DIGEST if scanner == "osv" else None,
        "base_result": "complete", "head_result": "complete",
    }


def input_row(scope: str, path: str, reasons: list[str] | None = None) -> dict:
    side = {"discovered": [path], "scanned": [path], "reasons": reasons or []}
    return {"scope": scope, "base": dict(side), "head": dict(side)}


def absent_input_row(scope: str) -> dict:
    side = {"discovered": [], "scanned": [], "reasons": ["verified_absence"]}
    return {"scope": scope, "base": dict(side), "head": dict(side)}


def unsupported_yarn_report(report: dict) -> dict:
    """Match the unsupported Yarn row emitted by the pinned Gate scanner."""
    report["coverage"][1].update({
        "scanner": "osv", "version": evidence.TOOL_VERSIONS["osv"], "scope": "yarn",
        "advisory_digest": evidence.constants.BUNDLE_DIGEST,
        "base_result": "unsupported", "head_result": "unsupported",
    })
    report["inputs"][1]["scope"] = "yarn"
    for side in ("base", "head"):
        report["inputs"][1][side]["discovered"] = ["frontend/yarn.lock"]
    return report


def git_exclusion_source(work: Path, placeholder: bytes = b"") -> tuple[str, str, str]:
    """Create synthetic bare objects; never clone or check out application code."""
    store = work / "objects.git"
    subprocess.run(["git", "init", "--bare", "--template=", str(store)], check=True, capture_output=True)

    def git(*args: str, stdin: bytes = b"") -> str:
        env = {
            "GIT_AUTHOR_NAME": "Synthetic Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2001-01-01T00:00:00+0000", "GIT_COMMITTER_DATE": "2001-01-01T00:00:00+0000",
        }
        process = subprocess.run(["git", "--git-dir", str(store), *args], input=stdin,
                                 check=True, capture_output=True, env=env)
        return process.stdout.decode().strip()

    def tree(entries: list[tuple[str, str, str, str]]) -> str:
        data = "".join(f"{mode} {kind} {oid}\t{name}\n" for mode, kind, oid, name in entries)
        return git("mktree", stdin=data.encode())

    placeholder_blob = git("hash-object", "-w", "--stdin", stdin=placeholder)
    php_blob = git("hash-object", "-w", "--stdin", stdin=b"<?php echo 'synthetic';\n")
    controller = tree([("100644", "blob", placeholder_blob, ".placeholder")])
    src = tree([("040000", "tree", controller, "Controller")])
    backend = tree([("040000", "tree", src, "src")])
    source = tree([("100644", "blob", php_blob, "app.php")])
    root = tree([("040000", "tree", backend, "backend"), ("040000", "tree", source, "src")])
    base = git("commit-tree", root, "-m", "synthetic base")
    head = git("commit-tree", root, "-p", base, "-m", "synthetic head")
    return base, head, root


def exclusion_report(work: Path, placeholder: bytes = b"") -> tuple[dict, bytes]:
    _, report = setup_work(work, "pair-pass.json")
    base, head, tree = git_exclusion_source(work, placeholder)
    state_data = json.loads((work / "state.json").read_text())
    for container in (state_data, report):
        container.update({"base_sha": base, "head_sha": head, "base_tree": tree, "head_tree": tree})
    (work / "state.json").write_text(json.dumps(state_data), encoding="utf-8")
    report["coverage"].append(coverage_row("unknown-input", "policy-exclusion", "1"))
    report["inputs"].append(input_row("unknown-input", "backend/src/Controller/.placeholder", ["approved_exclusion"]))
    raw = write_report(work, report)
    return report, raw


class ScannerProfileBindingTests(unittest.TestCase):
    def test_producer_matches_the_reviewed_scanner_manifest_bytes(self):
        raw = (ROOT / "tests/fixtures/gate-pr-security/scanner-frozen.json").read_bytes()
        manifest = json.loads(raw)
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        self.assertEqual(digest, evidence.constants.BUNDLE_DIGEST)
        self.assertEqual(digest, evidence.constants.DATASET_DIGEST)
        self.assertEqual(manifest["files"]["policy.json"], evidence.constants.POLICY_DIGEST)


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self, maximum):
        return self._body[:maximum]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    def __init__(self, token, submit_responses=None):
        self.token = token
        self.submit_responses = list(submit_responses or [FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000","admissionReady":true}')])
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if request.get_method() == "GET":
            return FakeResponse(200, json.dumps({"value": self.token}).encode())
        response = self.submit_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


FIXED_NOW = 1_800_000_000


def fake_token(claim_changes=None):
    claims = {
        "iss": "https://token.actions.githubusercontent.com",
        "iat": FIXED_NOW - 30,
        "exp": FIXED_NOW + 240,
        "aud": evidence.constants.AUDIENCE,
        "repository": "Tuinstra-DEV/gate",
        "repository_id": "100100",
        "repository_owner_id": "200200",
        "run_id": "300300",
        "run_attempt": "2",
        "job_workflow_ref": environment()["GATE_WORKFLOW_REF"],
        "job_workflow_sha": environment()["GATE_WORKFLOW_SHA"],
        "check_run_id": environment()["GATE_CHECK_RUN_ID"],
        "event_name": "pull_request_target",
        "ref": environment()["GATE_EXECUTION_REF"],
        "sha": environment()["GATE_EXECUTION_SHA"],
    }
    claims.update(claim_changes or {})
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "e30." + payload + ".synthetic-signature"


class GatePrSecurityEvidenceTests(unittest.TestCase):
    def package_fixture(self, work, fixture, env=None):
        raw, report = setup_work(work, fixture)
        staging = evidence.package(work, environment() if env is None else env)
        return raw, report, staging

    def prepare_submit(self, work, responses=None, claim_changes=None):
        self.package_fixture(work, "pair-pass.json")
        env = environment()
        env.update({
            "GATE_ARTIFACT_ID": "8675309", "GATE_ARTIFACT_DIGEST": "sha256:" + "1" * 64,
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/run?api-version=2.0",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-request-token",
        })
        opener = FakeOpener(fake_token(claim_changes), responses)
        return env, opener

    def test_packages_pass_blocked_and_incomplete_bytes_unchanged(self):
        for fixture in ("pair-pass.json", "pair-blocked.json", "pair-incomplete.json", "pair-unsupported.json"):
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                raw, report, staging = self.package_fixture(work, fixture)
                self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
                self.assertEqual(sorted(path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file()), ["evidence.json", "reports/result.json"])
                outer = json.loads((staging / "evidence.json").read_bytes())
                self.assertEqual(outer["schema_version"], "1.1")
                self.assertEqual(outer["exclusions"], [])
                self.assertEqual(outer["coverage"], report["coverage"])
                self.assertEqual(outer["findings"], report["findings"])
                self.assertEqual(outer["outcome"], report["outcome"])
                self.assertEqual(outer["context"], "pull_request")
                self.assertEqual(outer["pull_requests"], [73])
                self.assertEqual(outer["repository"]["repository_id"], "100100")
                self.assertEqual(outer["run"]["workflow_sha"], environment()["GATE_WORKFLOW_SHA"])
                self.assertEqual(outer["scanner_image"], evidence.constants.IMAGE)
                self.assertEqual(outer["payload_files"][0]["sha256"], "sha256:" + hashlib.sha256(raw).hexdigest())

    def test_transports_exact_optional_content_bound_proof_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            _, report = setup_work(work, "pair-pass.json")
            current_profile(report)
            report["content_bound_exceptions"] = [
                content_bound_record(),
                content_bound_record("GATE-TEXT-WORKFLOW-WRITE", "IN-3-workflow-write"),
            ]
            raw = write_report(work, report)
            staging = evidence.package(work, environment())
            self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
            self.assertEqual(json.loads(raw)["content_bound_exceptions"], report["content_bound_exceptions"])
            outer = json.loads((staging / "evidence.json").read_bytes())
            self.assertNotIn("content_bound_exceptions", outer)
            self.assertEqual(outer["payload_files"][0]["sha256"], "sha256:" + hashlib.sha256(raw).hexdigest())

    def test_transports_partial_bound_proof_without_grant_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            _, report = setup_work(work, "pair-pass.json")
            current_profile(report)
            report["content_bound_exceptions"] = [content_bound_record()]
            raw = write_report(work, report)
            staging = evidence.package(work, environment())
            self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
            self.assertEqual(json.loads(raw)["content_bound_exceptions"], report["content_bound_exceptions"])

    def test_producer_transports_past_dated_proof_without_expiry_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            _, report = setup_work(work, "pair-pass.json")
            current_profile(report)
            record = content_bound_record()
            record["expires_at"] = "2020-01-01T00:00:00Z"
            report["content_bound_exceptions"] = [record]
            raw = write_report(work, report)
            staging = evidence.package(work, environment())
            self.assertEqual((staging / "reports/result.json").read_bytes(), raw)

    def test_rejects_gate_bound_proof_for_other_state_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            _, report = setup_work(work, "pair-pass.json")
            current_profile(report)
            report["content_bound_exceptions"] = [content_bound_record()]
            write_report(work, report)
            state_data = json.loads((work / "state.json").read_text())
            state_data["repository"] = "acme/app"
            (work / "state.json").write_text(json.dumps(state_data))
            with self.assertRaisesRegex(evidence.EvidenceError, "unknown_repository"):
                evidence.package(work, environment())
            self.assertFalse((work / "staging").exists())

    def test_empty_optional_bound_proof_preserves_genuine_incomplete_result(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            _, report = setup_work(work, "pair-incomplete.json")
            current_profile(report)
            report["content_bound_exceptions"] = []
            raw = write_report(work, report)
            staging = evidence.package(work, environment())
            self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
            self.assertEqual(json.loads((staging / "evidence.json").read_bytes())["outcome"], "incomplete")

    def test_rejects_malformed_optional_content_bound_proof_before_staging(self):
        def mutate(case, report):
            records = report["content_bound_exceptions"]
            record = records[0]
            if case == "not_list":
                report["content_bound_exceptions"] = {}
            elif case == "extra_report_field":
                report["unapproved_field"] = []
            elif case == "extra_record_field":
                record["unapproved_field"] = "x"
            elif case == "wrong_repo":
                record["repository"] = "acme/app"
            elif case == "wrong_rule":
                record["rule_id"] = "OTHER"
            elif case == "wrong_fingerprint":
                record["fingerprint"] = "sha256:" + "c" * 64
            elif case == "wrong_path":
                record["path"] = "other.yml"
            elif case == "wrong_order":
                record["inputs"].reverse()
            elif case == "duplicate_input":
                record["inputs"][1]["path"] = record["inputs"][0]["path"]
            elif case == "extra_input_field":
                record["inputs"][0]["extra"] = True
            elif case == "bad_digest":
                record["inputs"][0]["sha256"] = "sha256:" + "A" * 64
            elif case == "bad_calendar_date":
                record["expires_at"] = "2030-02-30T00:00:00Z"
            elif case == "non_utc_date":
                record["expires_at"] = "2030-01-01T00:00:00+00:00"
            elif case == "blank_owner":
                record["owner"] = "  "
            elif case == "long_owner":
                record["owner"] = "é" * 101
            elif case == "long_reason":
                record["reason"] = "x" * 501
            elif case == "duplicate_id":
                second = content_bound_record("GATE-TEXT-WORKFLOW-WRITE", record["id"])
                records.append(second)
            elif case == "duplicate_fingerprint":
                second = content_bound_record(identifier="different-id")
                records.append(second)
            elif case == "too_many":
                records.extend([content_bound_record(identifier="other-1"), content_bound_record(identifier="other-2")])

        cases = (
            "not_list", "extra_report_field", "extra_record_field", "wrong_repo", "wrong_rule",
            "wrong_fingerprint", "wrong_path", "wrong_order", "duplicate_input", "extra_input_field",
            "bad_digest", "bad_calendar_date", "non_utc_date", "blank_owner", "long_owner",
            "long_reason", "duplicate_id", "duplicate_fingerprint", "too_many",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                _, report = setup_work(work, "pair-pass.json")
                current_profile(report)
                report["content_bound_exceptions"] = [content_bound_record()]
                mutate(case, report)
                write_report(work, report)
                error = "invalid_report" if case == "extra_report_field" else "invalid_content_bound_exceptions"
                with self.assertRaisesRegex(evidence.EvidenceError, error):
                    evidence.package(work, environment())
                self.assertFalse((work / "staging").exists())

    def test_verified_absence_is_preserved_without_rewriting_report(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, report, staging = self.package_fixture(Path(directory) / "work", "pair-verified-absence.json")
            self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
            self.assertEqual(json.loads((staging / "reports/result.json").read_bytes()), report)
            self.assertEqual(report["inputs"][0]["head"]["reasons"], ["verified_absence"])

    def test_packages_gate_text_embedded_web_and_pnpm_as_distinct_scanner_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            _, report = setup_work(work, "pair-pass.json")
            current_profile(report)
            cases = (
                ("configuration", "gate-text", "1", ".github/workflows/ci.yml"),
                ("embedded-web", "semgrep", evidence.TOOL_VERSIONS["semgrep"], "frontend/App.vue"),
                ("pnpm", "osv", evidence.TOOL_VERSIONS["osv"], "frontend/pnpm-lock.yaml"),
            )
            for scope, scanner, version, path in cases:
                report["coverage"].append(coverage_row(scope, scanner, version))
                report["inputs"].append(input_row(scope, path))
            report["coverage"].append(coverage_row("opaque-input", "policy-exclusion", "1"))
            report["inputs"].append(absent_input_row("opaque-input"))
            raw = write_report(work, report)
            staging = evidence.package(work, environment())
            self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
            outer = json.loads((staging / "evidence.json").read_bytes())
            self.assertEqual(outer["coverage"], report["coverage"])
            self.assertEqual({row["scope"]: row["scanner"] for row in outer["coverage"]}, {
                "secrets": "gitleaks", "configuration": "gate-text", "embedded-web": "semgrep",
                "pnpm": "osv", "opaque-input": "policy-exclusion",
            })
            self.assertEqual(report["inputs"][-1]["head"]["reasons"], ["verified_absence"])

    def test_rejects_forged_complete_yarn_osv_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            _, report = setup_work(work, "pair-pass.json")
            current_profile(report)
            report["coverage"].append(coverage_row("yarn", "osv", evidence.TOOL_VERSIONS["osv"]))
            report["inputs"].append(input_row("yarn", "frontend/yarn.lock"))
            write_report(work, report)
            with self.assertRaisesRegex(evidence.EvidenceError, "untrusted_coverage"):
                evidence.package(work, environment())
            self.assertFalse((work / "staging").exists())

    def test_preserves_unsupported_yarn_as_incomplete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            _, report = setup_work(work, "pair-unsupported.json")
            unsupported_yarn_report(report)
            raw = write_report(work, report)
            staging = evidence.package(work, environment())
            self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
            outer = json.loads((staging / "evidence.json").read_bytes())
            self.assertEqual(outer["outcome"], "incomplete")
            self.assertEqual(outer["coverage"][1]["scanner"], "osv")
            self.assertEqual(outer["coverage"][1]["base_result"], "unsupported")
            self.assertEqual(outer["coverage"][1]["head_result"], "unsupported")

    def test_rejects_mixed_or_unpinned_unsupported_yarn_coverage(self):
        for case in ("head_complete", "base_failed", "wrong_version", "wrong_advisory"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                _, report = setup_work(work, "pair-unsupported.json")
                unsupported_yarn_report(report)
                row = report["coverage"][1]
                if case == "head_complete":
                    row["head_result"] = "complete"
                    report["inputs"][1]["head"].update({"scanned": ["frontend/yarn.lock"], "reasons": []})
                elif case == "base_failed":
                    row["base_result"] = "failed"
                elif case == "wrong_version":
                    row["version"] = "0"
                else:
                    row["advisory_digest"] = "sha256:" + "e" * 64
                write_report(work, report)
                with self.assertRaisesRegex(evidence.EvidenceError, "untrusted_coverage"):
                    evidence.package(work, environment())
                self.assertFalse((work / "staging").exists())

    def test_approved_exclusion_cannot_be_laundered_into_a_complete_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            _, report = setup_work(work, "pair-pass.json")
            current_profile(report)
            report["coverage"].append(coverage_row("opaque-input", "policy-exclusion", "1"))
            report["inputs"].append(input_row("opaque-input", "assets/model.bin", ["approved_exclusion"]))
            write_report(work, report)
            with self.assertRaisesRegex(evidence.EvidenceError, "unregistered_exclusion"):
                evidence.package(work, environment())
            self.assertFalse((work / "staging").exists())

    def test_attests_exact_policy_exclusion_from_both_git_source_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            report, raw = exclusion_report(work)
            staging = evidence.package(work, environment())
            outer = json.loads((staging / "evidence.json").read_bytes())
            self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
            self.assertEqual(json.loads(raw), report)
            self.assertEqual(outer["schema_version"], "1.1")
            self.assertEqual(outer["exclusions"], [
                {"scope": "unknown-input", "side": side, "path": "backend/src/Controller/.placeholder",
                 "source_sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                 "reason_id": "approved_exclusion"}
                for side in ("base", "head")
            ])
            self.assertEqual(outer["coverage"][-1]["scanner"], "policy-exclusion")

    def test_exclusion_attestation_fails_closed_on_unbound_sources_and_reasons(self):
        for case in ("wrong_hash", "missing_git", "wrong_tree", "unknown_path", "duplicate", "stripped_reason"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                report, _ = exclusion_report(work, b"not-empty" if case == "wrong_hash" else b"")
                if case == "missing_git":
                    shutil.rmtree(work / "objects.git")
                elif case == "wrong_tree":
                    state_data = json.loads((work / "state.json").read_text())
                    report["head_tree"] = state_data["head_tree"] = "e" * 40
                    (work / "state.json").write_text(json.dumps(state_data), encoding="utf-8")
                elif case == "unknown_path":
                    for side in ("base", "head"):
                        report["inputs"][1][side]["discovered"] = ["backend/src/Controller/unlisted.placeholder"]
                        report["inputs"][1][side]["scanned"] = ["backend/src/Controller/unlisted.placeholder"]
                elif case == "duplicate":
                    report["inputs"][1]["head"]["discovered"].append("backend/src/Controller/.placeholder")
                    report["inputs"][1]["head"]["scanned"].append("backend/src/Controller/.placeholder")
                elif case == "stripped_reason":
                    report["inputs"][1]["head"]["reasons"] = []
                write_report(work, report)
                with self.assertRaises(evidence.EvidenceError):
                    evidence.package(work, environment())
                self.assertFalse((work / "staging").exists())

    def test_rejects_exclusion_laundered_as_real_scanner_coverage(self):
        cases = (
            ("php", "policy-exclusion", "1", ["approved_exclusion"], "untrusted_coverage"),
            ("opaque-input", "semgrep", evidence.TOOL_VERSIONS["semgrep"], [], "untrusted_coverage"),
            ("configuration", "gate-text", "1", ["approved_exclusion"], "outcome_input_mismatch"),
        )
        for scope, scanner, version, reasons, error in cases:
            with self.subTest(scope=scope, scanner=scanner), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                _, report = setup_work(work, "pair-pass.json")
                current_profile(report)
                report["coverage"].append(coverage_row(scope, scanner, version))
                report["inputs"].append(input_row(scope, "src/example.php", reasons))
                write_report(work, report)
                with self.assertRaisesRegex(evidence.EvidenceError, error):
                    evidence.package(work, environment())
                self.assertFalse((work / "staging").exists())

    def test_rejects_policy_exclusion_without_explicit_approval_reason(self):
        for reasons in ([], ["verified_absence"], ["approved_exclusion", "other"]):
            with self.subTest(reasons=reasons), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                _, report = setup_work(work, "pair-pass.json")
                current_profile(report)
                report["coverage"].append(coverage_row("opaque-input", "policy-exclusion", "1"))
                report["inputs"].append(input_row("opaque-input", "assets/model.bin", reasons))
                write_report(work, report)
                with self.assertRaisesRegex(evidence.EvidenceError, "outcome_input_mismatch"):
                    evidence.package(work, environment())
                self.assertFalse((work / "staging").exists())

    def test_rejects_wrong_gate_scope_version_and_profile_identities(self):
        cases = ("gate_text_version", "policy_exclusion_version", "policy_digest", "dataset_digest", "rules_digest", "advisory_digest")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                _, report = setup_work(work, "pair-pass.json")
                current_profile(report)
                report["coverage"].append(coverage_row("configuration", "gate-text", "1"))
                report["inputs"].append(input_row("configuration", ".github/workflows/ci.yml"))
                report["coverage"].append(coverage_row("opaque-input", "policy-exclusion", "1"))
                report["inputs"].append(absent_input_row("opaque-input"))
                report["coverage"].append(coverage_row("pnpm", "osv", evidence.TOOL_VERSIONS["osv"]))
                report["inputs"].append(input_row("pnpm", "frontend/pnpm-lock.yaml"))
                if case == "gate_text_version":
                    report["coverage"][1]["version"] = "2"
                elif case == "policy_exclusion_version":
                    report["coverage"][2]["version"] = "2"
                elif case == "policy_digest":
                    report["policy_digest"] = "sha256:" + "e" * 64
                elif case == "dataset_digest":
                    report["dataset_manifest_digest"] = "sha256:" + "e" * 64
                elif case == "rules_digest":
                    report["coverage"][1]["rules_digest"] = "sha256:" + "e" * 64
                else:
                    report["coverage"][3]["advisory_digest"] = "sha256:" + "e" * 64
                write_report(work, report)
                error = "report_profile_mismatch" if case in {"policy_digest", "dataset_digest"} else "untrusted_coverage"
                with self.assertRaisesRegex(evidence.EvidenceError, error):
                    evidence.package(work, environment())
                self.assertFalse((work / "staging").exists())

    def test_rejects_mismatched_state_sha_bundle_and_checksum(self):
        cases = ("state_sha", "bundle", "checksum")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                setup_work(work, "pair-pass.json")
                if case == "state_sha":
                    state_data = json.loads((work / "state.json").read_text())
                    state_data["head_sha"] = "e" * 40
                    (work / "state.json").write_text(json.dumps(state_data))
                elif case == "bundle":
                    report_path = work / "output/pair/result.json"
                    report = json.loads(report_path.read_text())
                    report["bundle_digest"] = "sha256:" + "e" * 64
                    raw = (json.dumps(report, indent=2) + "\n").encode()
                    report_path.write_bytes(raw)
                    (work / "output/pair/result.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  result.json\n")
                else:
                    (work / "output/pair/result.sha256").write_text("0" * 64 + "  result.json\n")
                with self.assertRaises(evidence.EvidenceError):
                    evidence.package(work, environment())

    def test_rejects_state_with_a_different_executed_image_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            setup_work(work, "pair-pass.json")
            state_data = json.loads((work / "state.json").read_text())
            state_data["scanner_image"] = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:" + "e" * 64
            (work / "state.json").write_text(json.dumps(state_data))
            with self.assertRaisesRegex(evidence.EvidenceError, "scanner_image_mismatch"):
                evidence.package(work, environment())
            self.assertFalse((work / "staging").exists())

    def test_rejects_unknown_scanner_version_and_rule_profile(self):
        cases = ("scanner", "version", "rules_digest")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                _, report = setup_work(work, "pair-pass.json")
                if case == "scanner":
                    report["coverage"][0]["scanner"] = "unregistered"
                elif case == "version":
                    report["coverage"][0]["version"] = "0"
                else:
                    report["coverage"][0]["rules_digest"] = "sha256:" + "e" * 64
                write_report(work, report)
                with self.assertRaisesRegex(evidence.EvidenceError, "untrusted_coverage"):
                    evidence.package(work, environment())
                self.assertFalse((work / "staging").exists())

    def test_rejects_exit_outcome_mismatch_without_forcing_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            setup_work(work, "pair-incomplete.json", exit_code=0)
            with self.assertRaisesRegex(evidence.EvidenceError, "scanner_exit_mismatch"):
                evidence.package(work, environment())
            self.assertFalse((work / "staging").exists())

    def test_rejects_total_package_size_with_zip_overhead_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            setup_work(work, "pair-pass.json")
            report_path = work / "output/pair/result.json"
            report = json.loads(report_path.read_text())
            report["findings"] = [
                {
                    "fingerprint": "sha256:" + hashlib.sha256(str(index).encode()).hexdigest(),
                    "rule_id": "legacy.rule", "category": "sast", "path": "src/app.php",
                    "change": "existing", "base_severity": "low", "head_severity": "low", "exception_id": None,
                }
                for index in range(30000)
            ]
            raw = json.dumps(report, separators=(",", ":")).encode()
            self.assertLess(len(raw), evidence.MAX_REPORT_BYTES)
            report_path.write_bytes(raw)
            (work / "output/pair/result.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  result.json\n")
            with self.assertRaisesRegex(evidence.EvidenceError, "package_size_limit"):
                evidence.package(work, environment())

    def test_rejects_unregistered_scanner_scope_and_incomplete_pass(self):
        for mutation in ("scope", "coverage"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                setup_work(work, "pair-pass.json")
                report_path = work / "output/pair/result.json"
                report = json.loads(report_path.read_text())
                if mutation == "scope":
                    report["coverage"][0]["scope"] = "unknown"
                else:
                    report["coverage"][0]["head_result"] = "failed"
                raw = (json.dumps(report, indent=2) + "\n").encode()
                report_path.write_bytes(raw)
                (work / "output/pair/result.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  result.json\n")
                with self.assertRaises(evidence.EvidenceError):
                    evidence.package(work, environment())

    def test_rejects_wrong_event_and_unpinned_workflow_context(self):
        for key, value in (("GATE_EVENT_NAME", "pull_request"), ("GATE_WORKFLOW_REF", evidence.APPROVED_WORKFLOW_PATH + "@refs/heads/main")):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                setup_work(work, "pair-pass.json")
                env = environment()
                env[key] = value
                with self.assertRaises(evidence.EvidenceError):
                    evidence.package(work, env)

    def test_rejects_raw_or_unredacted_finding_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            setup_work(work, "pair-pass.json")
            report_path = work / "output/pair/result.json"
            report = json.loads(report_path.read_text())
            report["findings"].append({"message": "private scanner output"})
            raw = (json.dumps(report, indent=2) + "\n").encode()
            report_path.write_bytes(raw)
            (work / "output/pair/result.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  result.json\n")
            with self.assertRaises(evidence.EvidenceError):
                evidence.package(work, environment())

    def test_submit_posts_actual_artifact_identity_with_same_token_after_transient_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            self.package_fixture(work, "pair-pass.json")
            env = environment()
            artifact_id = "8675309"
            artifact_digest = "sha256:" + "1" * 64
            env.update({
                "GATE_ARTIFACT_ID": artifact_id,
                "GATE_ARTIFACT_DIGEST": artifact_digest,
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/run?api-version=2.0",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-request-token",
            })
            token = fake_token()
            opener = FakeOpener(token, [URLError("private network detail"), FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000","admissionReady":true}')])
            with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener), patch.object(evidence.time, "sleep"):
                evidence.submit(work, env)
            self.assertEqual(len(opener.requests), 3)  # token request, transient submit, exact retry
            self.assertEqual([request.get_method() for request, _ in opener.requests], ["GET", "POST", "POST"])
            posted = [request for request, _ in opener.requests if request.get_method() == "POST"]
            self.assertEqual(len(posted), 2)
            self.assertEqual(posted[0].data, posted[1].data)
            self.assertEqual(posted[0].get_header("Authorization"), posted[1].get_header("Authorization"))
            self.assertEqual(posted[0].get_header("Authorization"), "Bearer " + token)
            self.assertEqual(json.loads(posted[0].data), {
                "repositoryId": "100100", "pullRequest": 73, "baseSha": "a" * 40,
                "headSha": "b" * 40, "artifactId": artifact_id, "artifactDigest": artifact_digest,
            })

    def test_submit_waits_for_durable_admission_with_same_body_and_token(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            receipt_id = "123e4567-e89b-42d3-a456-426614174000"
            responses = [
                FakeResponse(202, json.dumps({"status": "accepted", "receiptId": receipt_id, "admissionReady": False}).encode()),
                FakeResponse(202, json.dumps({"status": "accepted", "receiptId": receipt_id, "admissionReady": True}).encode()),
            ]
            env, opener = self.prepare_submit(work, responses)
            token = opener.token
            with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener), patch.object(evidence.time, "sleep") as sleep:
                evidence.submit(work, env)
            self.assertEqual([request.get_method() for request, _ in opener.requests], ["GET", "POST", "POST"])
            posted = [request for request, _ in opener.requests if request.get_method() == "POST"]
            self.assertEqual(posted[0].data, posted[1].data)
            self.assertEqual(posted[0].get_header("Authorization"), "Bearer " + token)
            self.assertEqual(posted[1].get_header("Authorization"), "Bearer " + token)
            self.assertTrue(all(timeout > 0 for _, timeout in opener.requests))
            sleep.assert_called_once()

    def test_submit_rejects_missing_or_non_boolean_admission_flag(self):
        responses = [
            b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000"}',
            b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000","admissionReady":"true"}',
            b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000","admissionReady":1}',
        ]
        for body in responses:
            with self.subTest(body_shape=len(body)), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                env, opener = self.prepare_submit(work, [FakeResponse(202, body)])
                with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener):
                    with self.assertRaisesRegex(evidence.EvidenceError, "receipt_response_invalid"):
                        evidence.submit(work, env)
                self.assertEqual(sum(request.get_method() == "POST" for request, _ in opener.requests), 1)

    def test_submit_rejects_receipt_identity_change_while_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            responses = [
                FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000","admissionReady":false}'),
                FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174001","admissionReady":true}'),
            ]
            env, opener = self.prepare_submit(work, responses)
            with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener), patch.object(evidence.time, "sleep"):
                with self.assertRaisesRegex(evidence.EvidenceError, "receipt_identity_changed"):
                    evidence.submit(work, env)
            self.assertEqual(sum(request.get_method() == "POST" for request, _ in opener.requests), 2)

    def test_submit_stops_polling_at_monotonic_admission_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            response = FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000","admissionReady":false}')
            env, opener = self.prepare_submit(work, [response])
            with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence.time, "monotonic", side_effect=[0.0, 0.0, 181.0]), patch.object(evidence, "build_opener", return_value=opener), patch.object(evidence.time, "sleep"):
                with self.assertRaisesRegex(evidence.EvidenceError, "publication_admission_timeout"):
                    evidence.submit(work, env)
            self.assertEqual(sum(request.get_method() == "POST" for request, _ in opener.requests), 1)

    def test_submit_has_finite_poll_attempt_cap_when_clock_does_not_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            pending = FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000","admissionReady":false}')
            env, opener = self.prepare_submit(work, [pending, pending])
            with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence.time, "monotonic", return_value=0.0), patch.object(evidence, "PUBLICATION_MAX_ATTEMPTS", 2), patch.object(evidence, "build_opener", return_value=opener), patch.object(evidence.time, "sleep"):
                with self.assertRaisesRegex(evidence.EvidenceError, "publication_admission_timeout"):
                    evidence.submit(work, env)
            self.assertEqual(sum(request.get_method() == "POST" for request, _ in opener.requests), 2)

    def test_submit_rejects_expired_or_future_oidc_times_before_post(self):
        invalid_claims = (
            {"exp": FIXED_NOW},
            {"iat": FIXED_NOW + 61},
            {"iat": FIXED_NOW - 301},
            {"iat": True},
            {"exp": FIXED_NOW + 1000},
        )
        for changes in invalid_claims:
            with self.subTest(claims=tuple(changes)), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                env, opener = self.prepare_submit(work, claim_changes=changes)
                with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener):
                    with self.assertRaisesRegex(evidence.EvidenceError, "oidc_token_time_invalid"):
                        evidence.submit(work, env)
                self.assertFalse(any(request.get_method() == "POST" for request, _ in opener.requests))

    def test_submit_rejects_expiry_while_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            response = FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000","admissionReady":false}')
            env, opener = self.prepare_submit(work, [response])
            wall_times = [FIXED_NOW, FIXED_NOW, FIXED_NOW, FIXED_NOW + 241]
            with patch.object(evidence.time, "time", side_effect=wall_times), patch.object(evidence.time, "monotonic", side_effect=[0.0, 0.0, 0.0, 1.6]), patch.object(evidence, "build_opener", return_value=opener), patch.object(evidence.time, "sleep"):
                with self.assertRaisesRegex(evidence.EvidenceError, "oidc_token_time_invalid"):
                    evidence.submit(work, env)
            self.assertEqual(sum(request.get_method() == "POST" for request, _ in opener.requests), 1)

    def test_submit_rejects_forbidden_or_replay_response(self):
        for status in (403, 409):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                env, opener = self.prepare_submit(work, [FakeResponse(status, b"{}")])
                with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener):
                    with self.assertRaisesRegex(evidence.EvidenceError, "receipt_rejected"):
                        evidence.submit(work, env)
                self.assertEqual(sum(request.get_method() == "POST" for request, _ in opener.requests), 1)

    def test_submit_retries_service_unavailable_only_three_times(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            env, opener = self.prepare_submit(work, [FakeResponse(503, b"{}") for _ in range(3)])
            with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener), patch.object(evidence.time, "sleep"):
                with self.assertRaisesRegex(evidence.EvidenceError, "publisher_temporarily_unavailable"):
                    evidence.submit(work, env)
            self.assertEqual(sum(request.get_method() == "POST" for request, _ in opener.requests), 3)

    def test_submit_rechecks_exclusion_git_objects_before_oidc_or_http(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            exclusion_report(work)
            staging = evidence.package(work, environment())
            original_manifest = (staging / "evidence.json").read_bytes()
            original_report = (staging / "reports/result.json").read_bytes()
            shutil.rmtree(work / "objects.git")
            env = environment()
            env.update({
                "GATE_ARTIFACT_ID": "8675309", "GATE_ARTIFACT_DIGEST": "sha256:" + "1" * 64,
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/run",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-request-token",
            })
            with patch.object(evidence, "build_opener", side_effect=AssertionError("OIDC or HTTP reached")) as opener:
                with self.assertRaisesRegex(evidence.EvidenceError, "exclusion_source_unavailable"):
                    evidence.submit(work, env)
            opener.assert_not_called()
            self.assertEqual((staging / "evidence.json").read_bytes(), original_manifest)
            self.assertEqual((staging / "reports/result.json").read_bytes(), original_report)

    def test_submit_rejects_stale_attempt_claim_and_wrong_oidc_origin(self):
        for claim_changes, url in (({"run_attempt": "1"}, "https://pipelines.actions.githubusercontent.com/run"), (None, "https://attacker.invalid/token")):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                self.package_fixture(work, "pair-pass.json")
                env = environment()
                env.update({
                    "GATE_ARTIFACT_ID": "8675309", "GATE_ARTIFACT_DIGEST": "sha256:" + "1" * 64,
                    "ACTIONS_ID_TOKEN_REQUEST_URL": url,
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-request-token",
                })
                opener = FakeOpener(fake_token(claim_changes))
                with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener):
                    with self.assertRaises(evidence.EvidenceError):
                        evidence.submit(work, env)
                self.assertFalse(any(request.get_method() == "POST" for request, _ in opener.requests))

    def test_submit_exposes_only_known_bounded_receipt_denial_codes(self):
        cases = (
            (b'{"code":"oidc_unsupported_algorithm_or_key","detail":"synthetic-private-marker"}', "receipt_rejected_oidc_unsupported_algorithm_or_key"),
            (b'{"code":"synthetic_private_token","detail":"synthetic-private-marker"}', "receipt_rejected"),
            (b'<html>synthetic-private-marker</html>', "receipt_rejected"),
            (b'{"code":"oidc_unapproved_workflow"}' + b' ' * 8193, "receipt_rejected"),
        )
        for body, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                env, opener = self.prepare_submit(work, [FakeResponse(403, body)])
                with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener):
                    with self.assertRaises(evidence.EvidenceError) as raised:
                        evidence.submit(work, env)
                self.assertEqual(expected, raised.exception.reason)
                self.assertNotIn("synthetic-private-marker", str(raised.exception))
                self.assertEqual(1, sum(request.get_method() == "POST" for request, _ in opener.requests))

    def test_submit_known_http_error_closes_stream_and_redacts_detail(self):
        import io
        from urllib.error import HTTPError
        stream = io.BytesIO(b'{"code":"oidc_unapproved_workflow","detail":"synthetic-private-marker"}')
        error = HTTPError(evidence.constants.ENDPOINT, 403, "Forbidden", {}, stream)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            env, opener = self.prepare_submit(work, [error])
            with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener):
                with self.assertRaises(evidence.EvidenceError) as raised:
                    evidence.submit(work, env)
            self.assertEqual("receipt_rejected_oidc_unapproved_workflow", raised.exception.reason)
            self.assertNotIn("synthetic-private-marker", str(raised.exception))
            self.assertTrue(stream.closed)
            self.assertEqual(1, sum(request.get_method() == "POST" for request, _ in opener.requests))

    def test_transient_http_error_is_closed_without_reading_its_body(self):
        import io
        from urllib.error import HTTPError
        class UnreadableTransientBody(io.BytesIO):
            def read(self, *args):
                raise AssertionError("transient response body must not be read")
        stream = UnreadableTransientBody(b"synthetic-private-marker")
        error = HTTPError(evidence.constants.ENDPOINT, 503, "Unavailable", {}, stream)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            ready = FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000","admissionReady":true}')
            env, opener = self.prepare_submit(work, [error, ready])
            with patch.object(stream, "read", wraps=stream.read) as read_body, patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener), patch.object(evidence.time, "sleep"):
                evidence.submit(work, env)
                read_body.assert_not_called()
            self.assertTrue(stream.closed)
            self.assertEqual(2, sum(request.get_method() == "POST" for request, _ in opener.requests))

    def test_submit_only_accepts_202(self):
        cases = ((200, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000"}', "receipt_rejected"),
                 (202, b'{"status":"accepted","receiptId":"not-a-uuid"}', "receipt_response_invalid"))
        for status, response, reason in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                self.package_fixture(work, "pair-pass.json")
                env = environment()
                env.update({
                    "GATE_ARTIFACT_ID": "8675309", "GATE_ARTIFACT_DIGEST": "sha256:" + "1" * 64,
                    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/run",
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-request-token",
                })
                opener = FakeOpener(fake_token(), [FakeResponse(status, response)])
                with patch.object(evidence.time, "time", return_value=FIXED_NOW), patch.object(evidence, "build_opener", return_value=opener):
                    with self.assertRaisesRegex(evidence.EvidenceError, reason):
                        evidence.submit(work, env)


class OriginRegression(unittest.TestCase):
    HOSTS=('pipelines.actions.githubusercontent.com','run-actions-1-azure-eastus.actions.githubusercontent.com','run-actions-2-azure-eastus.actions.githubusercontent.com','run-actions-3-azure-eastus.actions.githubusercontent.com')
    def test_old_and_observed_host(self):
        from urllib.parse import parse_qsl,urlsplit
        for host in self.HOSTS:
            with self.subTest(host=host):
                value=urlsplit(evidence._oidc_url('https://'+host+'/run?api-version=2.0'))
                self.assertEqual(value.netloc,host)
                self.assertEqual(parse_qsl(value.query),[('api-version','2.0'),('audience',evidence.constants.AUDIENCE)])
    def test_guards_remain_exact(self):
        for host in self.HOSTS:
            for raw in ('http://'+host+'/run','https://'+host+'.attacker.invalid/run','https://x.'+host+'/run','https://'+host+'./run','https://user@'+host+'/run','https://'+host+':443/run','https://'+host+':/run','https://'+host+'/run#fragment','https://'+host+'/run#','https://'+host+'/run?audience=other','https://'+host+'/run?%61udience=other','https://run-actions-2-azure-westus.actions.githubusercontent.com/run'):
                with self.subTest(raw=raw),self.assertRaisesRegex(evidence.EvidenceError,'oidc_url_rejected'):
                    evidence._oidc_url(raw)



if __name__ == "__main__":
    unittest.main()
