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
                self.assertEqual(outer["payload_files"][0]["sha256"], "sha256:" + hashlib.sha256(raw).hexdigest())

    def test_verified_absence_is_preserved_without_rewriting_report(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, report, staging = self.package_fixture(Path(directory) / "work", "pair-verified-absence.json")
            self.assertEqual((staging / "reports/result.json").read_bytes(), raw)
            self.assertEqual(json.loads((staging / "reports/result.json").read_bytes()), report)
            self.assertEqual(report["inputs"][0]["head"]["reasons"], ["verified_absence"])

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
