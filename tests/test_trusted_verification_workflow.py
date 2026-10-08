"""Executable trust and routing contract for the reusable Sanctuary workflow."""

import ast
import io
import json
import os
from pathlib import Path
import re
import tempfile
import textwrap
import unittest
import urllib.error
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/reusable-trusted-verification.yml"
SHA = "a" * 40
BASE = "b" * 40
WORKFLOW_SHA = "c" * 40
MERGE = "e" * 40
REPO = "Tuinstra-DEV/wodiq-platform"


def preflight_script():
    source = WORKFLOW.read_text()
    match = re.search(r"^          python3 - <<'PY'\n(?P<script>.*?)^          PY$", source, re.M | re.S)
    if not match:
        raise AssertionError("hosted inline Python preflight missing")
    return textwrap.dedent(match.group("script"))


def consumer_verdict(call_result, preflight_trusted, trusted_proof, hosted_proof):
    """Required alias predicate; no skipped call or ambiguous proof may pass."""
    return call_result == "success" and (
        (preflight_trusted == "true" and trusted_proof == "true" and hosted_proof == "") or
        (preflight_trusted == "false" and trusted_proof == "" and hosted_proof == "true")
    )


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class TrustedVerificationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.event_path = Path(self.temp.name) / "event.json"
        self.output_path = Path(self.temp.name) / "output"
        repo = {"full_name": REPO, "private": True, "fork": False}
        self.event = {
            "repository": repo,
            "sender": {"login": "marcel-tuinstra", "type": "User"},
            "pull_request": {
                "number": 17,
                "user": {"login": "marcel-tuinstra", "type": "User"},
                "head": {"sha": SHA, "ref": "feat/DEV-50-job-routing", "repo": repo},
                "base": {"sha": BASE, "repo": repo},
            },
        }
        self.run = {
            "id": 123, "event": "pull_request", "head_sha": SHA,
            "actor": {"login": "marcel-tuinstra", "type": "User"},
            "triggering_actor": {"login": "marcel-tuinstra", "type": "User"},
            "repository": repo, "head_repository": repo,
            "pull_requests": [{"number": 17, "head": {"sha": SHA}, "base": {"sha": BASE}}],
            "referenced_workflows": [{
                "path": f"Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@{WORKFLOW_SHA}",
                "sha": WORKFLOW_SHA,
            }],
        }
        self.pr = {
            "number": 17, "state": "open", "merged": False,
            "user": {"login": "marcel-tuinstra", "type": "User"},
            "head": {"sha": SHA, "ref": "feat/DEV-50-job-routing", "repo": repo},
            "base": {"sha": BASE, "repo": repo},
        }
        self.ref = {"ref": "refs/heads/feat/DEV-50-job-routing", "object": {"type": "commit", "sha": SHA}}
        self.merge = {"sha": MERGE, "parents": [{"sha": BASE}, {"sha": SHA}]}
        self.env = {
            "GITHUB_EVENT_PATH": str(self.event_path), "GITHUB_OUTPUT": str(self.output_path),
            "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "123", "GITHUB_ACTOR": "marcel-tuinstra",
            "GITHUB_SHA": MERGE, "GITHUB_REF": "refs/pull/17/merge",
            "GITHUB_TRIGGERING_ACTOR": "marcel-tuinstra", "GITHUB_TOKEN": "fixture-only",
            "PROFILE": "medium", "JOB_KEY": "unit", "GITHUB_API_URL": "https://api.github.test",
        }

    def execute(self, *, api_error=False):
        self.event_path.write_text(json.dumps(self.event))
        self.output_path.write_text("")
        requests = []

        def urlopen(request, timeout):
            self.assertEqual(timeout, 10)
            self.assertEqual(request.get_header("Authorization"), "Bearer fixture-only")
            requests.append(request.full_url)
            if isinstance(api_error, Exception):
                raise api_error
            if api_error:
                raise OSError("private API failure with a secret that must not escape")
            if "/actions/runs/123" in request.full_url:
                value = self.run
            elif "/pulls/17" in request.full_url:
                value = self.pr
            elif "/git/ref/heads/" in request.full_url:
                value = self.ref
            elif f"/git/commits/{MERGE}" in request.full_url:
                value = self.merge
            else:
                self.fail(f"unexpected API path: {request.full_url}")
            return FakeResponse(json.dumps(value).encode())

        import urllib.request
        with patch.dict(os.environ, self.env, clear=True), patch.object(urllib.request, "urlopen", side_effect=urlopen):
            exec(compile(preflight_script(), str(WORKFLOW), "exec"), {"__name__": "__main__"})
        outputs = dict(line.split("=", 1) for line in self.output_path.read_text().splitlines())
        return outputs, requests

    def test_trusted_medium_and_heavy_route_to_fixed_labels(self):
        for profile in ("medium", "heavy"):
            with self.subTest(profile=profile):
                self.env["PROFILE"] = profile
                outputs, requests = self.execute()
                self.assertEqual(outputs["trusted"], "true")
                self.assertEqual(outputs["source-sha"], MERGE)
                self.assertEqual(outputs["head-sha"], SHA)
                self.assertEqual(json.loads(outputs["runner-labels"]),
                                 ["self-hosted", f"trusted-{profile}", "sanctuary-wodiq-platform"])
                self.assertEqual(len(requests), 4)

    def test_untrusted_contexts_use_hosted_fallback(self):
        cases = {
            "fork": lambda: self.event["pull_request"]["head"]["repo"].update(full_name="elsewhere/devops", fork=True),
            "bot_actor": lambda: self.env.update(GITHUB_ACTOR="dependabot[bot]"),
            "bot_rerun": lambda: self.env.update(GITHUB_TRIGGERING_ACTOR="dependabot[bot]"),
            "bot_author": lambda: self.event["pull_request"]["user"].update(login="dependabot[bot]", type="Bot"),
            "pull_request_target": lambda: self.env.update(GITHUB_EVENT_NAME="pull_request_target"),
            "unknown_repo": lambda: self.env.update(GITHUB_REPOSITORY="Tuinstra-DEV/unknown"),
            "stale_head": lambda: self.ref["object"].update(sha="d" * 40),
            "run_mismatch": lambda: self.run.update(head_sha="d" * 40),
            "run_pr_mismatch": lambda: self.run["pull_requests"][0].update(number=18),
            "current_pr_author_mismatch": lambda: self.pr["user"].update(login="other"),
            "current_pr_fork": lambda: self.pr["head"]["repo"].update(full_name="elsewhere/devops", fork=True),
            "current_pr_sha_mismatch": lambda: self.pr["head"].update(sha="d" * 40),
            "current_pr_base_change": lambda: self.pr["base"].update(sha="d" * 40),
            "public_pr_repo": lambda: self.pr["head"]["repo"].update(private=False),
            "mutable_workflow_ref": lambda: self.run["referenced_workflows"][0].update(path="Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@refs/heads/main"),
            "wrong_workflow_sha": lambda: self.run["referenced_workflows"][0].update(sha="d" * 40),
            "missing_workflow_reference": lambda: self.run.update(referenced_workflows=[]),
            "merge_parent_mismatch": lambda: self.merge["parents"][1].update(sha="d" * 40),
            "non_merge_ref": lambda: self.env.update(GITHUB_REF="refs/heads/main"),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                self.setUp()
                change()
                outputs, _ = self.execute()
                self.assertEqual(outputs["trusted"], "false")
                self.assertEqual(outputs["source-sha"], "")
                self.assertEqual(outputs["head-sha"], "")
                self.assertEqual(outputs["runner-labels"], "[]")

    def test_api_error_falls_back_without_leaking_exception(self):
        outputs, _ = self.execute(api_error=True)
        self.assertEqual(outputs["trusted"], "false")
        self.assertEqual(outputs["reason"], "api-unavailable")
        denied = urllib.error.HTTPError("https://api.github.test", 403, "forbidden", None, None)
        try:
            outputs, _ = self.execute(api_error=denied)
        finally:
            denied.close()
        self.assertEqual(outputs["trusted"], "false")
        self.assertEqual(outputs["reason"], "api-unavailable")

    def test_invalid_contract_fails_closed(self):
        for name, value in (("PROFILE", "fast"), ("JOB_KEY", "../../escape"), ("JOB_KEY", "unit;id")):
            with self.subTest(name=name, value=value):
                old = self.env[name]
                self.env[name] = value
                with self.assertRaises(SystemExit):
                    self.execute()
                self.env[name] = old

    def test_workflow_has_literal_group_read_only_permissions_and_fixed_adapter(self):
        source = WORKFLOW.read_text()
        self.assertIn("group: sanctuary-trusted-verification", source)
        self.assertIn("labels: ${{ fromJSON(needs.preflight.outputs.runner-labels) }}", source)
        self.assertIn("runs-on: ubuntu-24.04", source)
        self.assertIn("if: ${{ needs.preflight.outputs.trusted != 'true' }}", source)
        self.assertIn("bash .github/ci/sanctuary-ci --strict \"$JOB_KEY\"", source)
        self.assertEqual(source.count("persist-credentials: false"), 2)
        self.assertNotIn("secrets: inherit", source)
        self.assertNotIn("pull-requests: write", source)
        self.assertIn("contents: read", source)
        self.assertIn("actions: read", source)
        self.assertIn("pull-requests: read", source)
        self.assertNotIn("job.workflow_ref", source)
        self.assertNotIn("job.workflow_sha", source)
        self.assertNotIn('"Tuinstra-DEV/devops": "sanctuary-devops"', source)
        self.assertNotIn('"Tuinstra-DEV/agent-lab": "sanctuary-agent-lab"', source)
        mapping_source = re.search(r"^          REPO_LABELS = (\{.*?^          \})", source, re.M | re.S)
        self.assertIsNotNone(mapping_source)
        allowlisted = ast.literal_eval(textwrap.dedent(mapping_source.group(1)))
        manifest = WORKFLOW.parents[2] / "runner/policy/org-routing-repositories.json"
        self.assertEqual(set(allowlisted), set(json.loads(manifest.read_text())))

    def test_consumer_verdict_truth_table(self):
        cases = [
            ("success", "true", "true", "", True),
            ("success", "false", "", "true", True),
            ("failure", "true", "true", "", False),
            ("skipped", "false", "", "true", False),
            ("cancelled", "true", "true", "", False),
            ("success", "true", "", "", False),
            ("success", "false", "", "", False),
            ("success", "true", "false", "", False),
            ("success", "false", "", "false", False),
            ("success", "true", "true", "true", False),
            ("success", "false", "true", "true", False),
            ("success", "true", "true", "false", False),
            ("success", "false", "false", "true", False),
            ("success", "unknown", "", "true", False),
        ]
        for result, route, trusted, hosted, expected in cases:
            with self.subTest(results=(result, route, trusted, hosted)):
                self.assertEqual(consumer_verdict(result, route, trusted, hosted), expected)

    def test_direct_proof_contract_and_timeouts(self):
        source = WORKFLOW.read_text()
        self.assertIn("value: ${{ jobs.preflight.outputs.trusted }}", source)
        self.assertIn("value: ${{ jobs.trusted-verification.outputs.verified }}", source)
        self.assertIn("value: ${{ jobs.hosted-fallback.outputs.verified }}", source)
        self.assertEqual(source.count("verified: ${{ steps.proof.outputs.verified }}"), 2)
        self.assertEqual(source.count("run: echo 'verified=true' >> \"$GITHUB_OUTPUT\""), 2)
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        for route in (trusted, hosted):
            self.assertLess(route.index("bash .github/ci/sanctuary-ci --strict"), route.index("run: echo 'verified=true'"))
            self.assertNotIn("always()", route)
        self.assertNotIn("  finalize:", source)
        self.assertEqual(source.count("timeout-minutes: 5"), 1)
        self.assertEqual(source.count("timeout-minutes: 60"), 2)


if __name__ == "__main__":
    unittest.main()
