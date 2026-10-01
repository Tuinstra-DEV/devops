import base64
import hashlib
import importlib.util
import json
from pathlib import Path
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
    raw = report_path.read_bytes()
    report = json.loads(raw)
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
        self.submit_responses = list(submit_responses or [FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000"}')])
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if request.get_method() == "GET":
            return FakeResponse(200, json.dumps({"value": self.token}).encode())
        response = self.submit_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def fake_token(claim_changes=None):
    claims = {
        "iss": "https://token.actions.githubusercontent.com",
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

    def test_packages_pass_blocked_and_incomplete_bytes_unchanged(self):
        for fixture in ("pair-pass.json", "pair-blocked.json", "pair-incomplete.json", "pair-unsupported.json"):
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / "work"
                raw, report, staging = self.package_fixture(work, fixture)
                self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
                self.assertEqual(sorted(path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file()), ["evidence.json", "reports/result.json"])
                outer = json.loads((staging / "evidence.json").read_bytes())
                self.assertEqual(outer["coverage"], report["coverage"])
                self.assertEqual(outer["findings"], report["findings"])
                self.assertEqual(outer["outcome"], report["outcome"])
                self.assertEqual(outer["context"], "pull_request")
                self.assertEqual(outer["pull_requests"], [73])
                self.assertEqual(outer["repository"]["repository_id"], "100100")
                self.assertEqual(outer["run"]["workflow_sha"], environment()["GATE_WORKFLOW_SHA"])
                self.assertEqual(outer["scanner_image"], evidence.constants.IMAGE)
                self.assertEqual(outer["payload_files"][0]["sha256"], "sha256:" + hashlib.sha256(raw).hexdigest())

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
            with self.assertRaisesRegex(evidence.EvidenceError, "outcome_input_mismatch"):
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
            opener = FakeOpener(token, [URLError("private network detail"), FakeResponse(202, b'{"status":"accepted","receiptId":"123e4567-e89b-42d3-a456-426614174000"}')])
            with patch.object(evidence, "build_opener", return_value=opener), patch.object(evidence.time, "sleep"):
                evidence.submit(work, env)
            self.assertEqual(len(opener.requests), 3)  # token request, transient submit, exact retry
            posted = [request for request, _ in opener.requests if request.get_method() == "POST"]
            self.assertEqual(len(posted), 2)
            self.assertEqual(posted[0].data, posted[1].data)
            self.assertEqual(posted[0].get_header("Authorization"), posted[1].get_header("Authorization"))
            self.assertEqual(posted[0].get_header("Authorization"), "Bearer " + token)
            self.assertEqual(json.loads(posted[0].data), {
                "repositoryId": "100100", "pullRequest": 73, "baseSha": "a" * 40,
                "headSha": "b" * 40, "artifactId": artifact_id, "artifactDigest": artifact_digest,
            })

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
                with patch.object(evidence, "build_opener", return_value=opener):
                    with self.assertRaises(evidence.EvidenceError):
                        evidence.submit(work, env)
                self.assertFalse(any(request.get_method() == "POST" for request, _ in opener.requests))

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
                with patch.object(evidence, "build_opener", return_value=opener):
                    with self.assertRaisesRegex(evidence.EvidenceError, reason):
                        evidence.submit(work, env)


if __name__ == "__main__":
    unittest.main()
