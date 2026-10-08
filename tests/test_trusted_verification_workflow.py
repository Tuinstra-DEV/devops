"""Executable trust and routing contract for the reusable Sanctuary workflow."""

import ast
import io
import json
import os
from pathlib import Path
import re
import subprocess
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


def contract_script():
    source = WORKFLOW.read_text()
    match = re.search(r"^          python3 - <<'CONTRACT'\n(?P<script>.*?)^          CONTRACT$", source, re.M | re.S)
    if not match:
        raise AssertionError("fixed consumer contract missing")
    return textwrap.dedent(match.group("script"))


def trusted_php_script():
    source = WORKFLOW.read_text()
    trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
    step = trusted.split("      - name: Select verified baked PHP and Composer without privileges", 1)[1]
    return textwrap.dedent(step.split("        run: |\n", 1)[1].split("      - name:", 1)[0])


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
            "head_branch": "feat/DEV-50-job-routing",
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
        self.open_prs = [{"number": 17}]
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
            elif "/pulls?state=open&head=" in request.full_url:
                self.assertIn("Tuinstra-DEV%3Afeat%2FDEV-50-job-routing&per_page=100", request.full_url)
                value = self.open_prs
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

    def execute_contract(self, repo, key, profile, plan=""):
        self.output_path.write_text("")
        env = {"GITHUB_REPOSITORY": repo, "JOB_KEY": key, "PROFILE": profile,
               "PLAN_JSON": plan, "GITHUB_OUTPUT": str(self.output_path)}
        with patch.dict(os.environ, env, clear=True):
            exec(compile(contract_script(), str(WORKFLOW), "exec"), {"__name__": "__main__"})
        return dict(line.split("=", 1) for line in self.output_path.read_text().splitlines())

    def test_fixed_consumer_contract_maps_exact_repository_key_and_toolchain(self):
        wodiq_plan = {"version": 2, **{key: False for key in ("core", "migration", "api", "management", "worker", "client")},
                      "builds": {key: False for key in ("api", "management", "worker")},
                      "proofs": {key: False for key in ("all", "activity", "coach", "managementAuth", "garmin")}}
        cases = [
            (REPO, "wodiq-runtime-build", "medium", json.dumps(wodiq_plan), ("24", "", "")),
            (REPO, "wodiq-api", "heavy", json.dumps(wodiq_plan), ("24", "", "")),
            ("Tuinstra-DEV/gate", "gate-php", "medium", '{"backendSuites":"Unit,Integration"}', ("", "", "8.3")),
            ("Tuinstra-DEV/gate", "gate-frontend-static", "heavy", '{"frontendTests":"all","frontendBuild":true}', ("24", "10.28.2", "")),
            ("Tuinstra-DEV/tracker", "tracker-backend-postgres", "heavy", "", ("", "", "8.4")),
            ("Tuinstra-DEV/tracker", "tracker-frontend-static", "medium", "", ("24.18.0", "11.1.1", "")),
            ("Tuinstra-DEV/tracker", "tracker-container-api", "heavy", "", ("24.18.0", "", "")),
            ("Tuinstra-DEV/notify", "notify-quality", "heavy", "", ("", "", "")),
            ("Tuinstra-DEV/console", "console-php-lint", "medium", "", ("", "", "8.4")),
            ("Tuinstra-DEV/console", "console-php-postgres", "heavy", "", ("", "", "8.4")),
            ("Tuinstra-DEV/marcel-site", "site-node", "medium", "", ("24", "", "")),
            ("Tuinstra-DEV/tuinstra-site", "site-node", "medium", "", ("24", "", "")),
            ("Tuinstra-DEV/wodiq-site", "site-node", "medium", "", ("24", "", "")),
            ("Tuinstra-DEV/openairco-site", "site-node", "medium", "", ("24", "", "")),
            ("Tuinstra-DEV/wodiq-app", "wodiq-app-checks", "heavy", "", ("24", "", "")),
            ("Tuinstra-DEV/status", "status-quality", "heavy", "", ("", "", "")),
            ("Tuinstra-DEV/openairco", "openairco-flutter", "heavy", "", ("", "", "")),
        ]
        for repo, key, profile, plan, expected in cases:
            with self.subTest(repo=repo, key=key):
                output = self.execute_contract(repo, key, profile, plan)
                self.assertEqual(tuple(output[name] for name in ("node-version", "pnpm-version", "php-version")), expected)
                expected_plan = (json.dumps(json.loads(plan), separators=(",", ":")) if plan else
                                 '{"frontend-web":true,"frontend-unit":true,"frontend-component":true,"frontend-site":true}'
                                 if key == "tracker-frontend-static" else "")
                self.assertEqual(output["plan-json"], expected_plan)
                self.assertEqual(output["runtime-download"], "false")
                self.assertEqual(output["gate-frontend-output"], "true" if key == "gate-frontend-static" else "false")

        wodiq_plan["builds"]["api"] = True
        output = self.execute_contract(REPO, "wodiq-api", "heavy", json.dumps(wodiq_plan))
        self.assertEqual(output["runtime-download"], "true")
        gate_no_build = self.execute_contract("Tuinstra-DEV/gate", "gate-frontend-static", "heavy",
                                              '{"frontendTests":"unit","frontendBuild":false}')
        self.assertEqual(gate_no_build["gate-frontend-output"], "false")

    def test_tracker_frontend_plan_preserves_selected_suites_and_full_fallback(self):
        repo, key, profile = "Tuinstra-DEV/tracker", "tracker-frontend-static", "medium"
        selected = '{"frontend-web":true,"frontend-unit":false,"frontend-component":false,"frontend-site":true}'
        output = self.execute_contract(repo, key, profile, selected)
        self.assertEqual(json.loads(output["plan-json"]), json.loads(selected))
        full = self.execute_contract(repo, key, profile)
        self.assertEqual(json.loads(full["plan-json"]), {
            "frontend-web": True, "frontend-unit": True, "frontend-component": True, "frontend-site": True,
        })
        for bad in (
            '{"frontend-web":false,"frontend-unit":false,"frontend-component":false,"frontend-site":false}',
            '{"frontend-web":false,"frontend-unit":true,"frontend-component":false,"frontend-site":true}',
            '{"frontend-web":false,"frontend-unit":false,"frontend-component":true,"frontend-site":true}',
            '{"frontend-web":"true","frontend-unit":false,"frontend-component":false,"frontend-site":true}',
            '{"frontend-web":true,"frontend-unit":false,"frontend-component":false,"frontend-site":true,"command":"id"}',
        ):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                self.execute_contract(repo, key, profile, bad)

    def test_fixed_consumer_contract_rejects_unknown_pair_profile_and_plan(self):
        cases = [
            (REPO, "wodiq-api", "medium", "{}"),
            (REPO, "wodiq-api", "heavy", '{"command":"id"}'),
            ("Tuinstra-DEV/gate", "gate-frontend-static", "medium", '{"frontendTests":"all","frontendBuild":true}'),
            ("Tuinstra-DEV/gate", "gate-php", "medium", '{"backendSuites":"Functional,Unit"}'),
            ("Tuinstra-DEV/gate", "gate-php", "medium", '{"backendSuites":[]}'),
            ("Tuinstra-DEV/gate", "gate-frontend-static", "medium", '{"frontendTests":"all","frontendBuild":"true"}'),
            ("Tuinstra-DEV/gate", "gate-frontend-static", "medium", '{"frontendTests":[],"frontendBuild":true}'),
            ("Tuinstra-DEV/tracker", "tracker-frontend-static", "medium", '{"path":"/tmp"}'),
            ("Tuinstra-DEV/notify", "notify-quality", "heavy", '{"service":"postgres"}'),
            ("Tuinstra-DEV/console", "console-php-lint", "heavy", ""),
            ("Tuinstra-DEV/marcel-site", "site-docker", "heavy", ""),
            ("Tuinstra-DEV/wodiq-app", "wodiq-app-checks", "medium", ""),
            ("Tuinstra-DEV/status", "status-quality", "medium", ""),
            ("Tuinstra-DEV/openairco", "openairco-flutter", "medium", ""),
            ("Tuinstra-DEV/status", "status-quality", "heavy", '{"command":"id"}'),
            ("Tuinstra-DEV/openairco", "openairco-flutter", "heavy", '{"path":"/tmp"}'),
            ("Tuinstra-DEV/unknown", "wodiq-api", "heavy", ""),
        ]
        for repo, key, profile, plan in cases:
            with self.subTest(repo=repo, key=key, plan=plan):
                with self.assertRaises(SystemExit):
                    self.execute_contract(repo, key, profile, plan)

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

    def test_empty_run_pr_associations_still_verify_exact_current_pr(self):
        self.run["pull_requests"] = []
        outputs, requests = self.execute()
        self.assertEqual(outputs["trusted"], "true")
        self.assertEqual(outputs["source-sha"], MERGE)
        self.assertEqual(len(requests), 5)

    def test_empty_run_association_requires_unique_current_branch_pr(self):
        self.run["pull_requests"] = []
        for candidates in ([], [{"number": 17}, {"number": 18}], [{"number": 18}],
                           [{"number": True}], [{"number": "17"}]):
            with self.subTest(candidates=candidates):
                self.open_prs = candidates
                outputs, _ = self.execute()
                self.assertEqual(outputs["trusted"], "false")
                self.assertEqual(outputs["reason"], "run-pr-ambiguous")

    def test_unsupported_branch_or_multiple_run_associations_reject_trusted_route(self):
        self.run["pull_requests"].append({"number": 18})
        outputs, _ = self.execute()
        self.assertEqual(outputs["reason"], "run-pr-mismatch")
        self.run["pull_requests"] = []
        for branch in ("a+b", "a@b", "a//b", "a/", ".hidden", "a..b", "a" * 256):
            with self.subTest(branch=branch):
                self.event["pull_request"]["head"]["ref"] = branch
                self.run["head_branch"] = branch
                outputs, _ = self.execute()
                self.assertEqual(outputs["trusted"], "false")
                self.assertEqual(outputs["reason"], "event-ref-invalid")

    def test_nonempty_wrong_run_pr_association_remains_untrusted(self):
        self.run["pull_requests"] = [{"number": 999, "head": {"sha": SHA}, "base": {"sha": BASE}}]
        outputs, _ = self.execute()
        self.assertEqual(outputs["trusted"], "false")
        self.assertEqual(outputs["reason"], "run-pr-mismatch")

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
            "run_head_branch_mismatch": lambda: self.run.update(head_branch="other/head"),
            "run_head_branch_missing": lambda: self.run.pop("head_branch"),
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
        preflight = source.split("  preflight:", 1)[1].split("  trusted-verification:", 1)[0]
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        self.assertIn("actions: read", preflight)
        self.assertIn("pull-requests: read", preflight)
        for route in (trusted, hosted):
            self.assertIn("permissions:\n      contents: read", route)
            self.assertNotIn("actions: read", route)
            self.assertNotIn("pull-requests: read", route)
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

    def test_artifact_handoff_is_same_run_and_precedes_proof_on_both_routes(self):
        source = WORKFLOW.read_text()
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        for route in (trusted, hosted):
            self.assertLess(route.index("Download exact current-run WODIQ runtime artifact"),
                            route.index("bash .github/ci/sanctuary-ci --strict"))
            self.assertLess(route.index("bash .github/ci/sanctuary-ci --strict"),
                            route.index("Upload exact current-run WODIQ runtime artifact"))
            self.assertLess(route.index("Upload exact current-run Gate frontend output"),
                            route.index("run: echo 'verified=true'"))
            self.assertIn("ci-runtime-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}", route)
            self.assertIn("gate-frontend-output-${{ github.run_id }}-${{ github.run_attempt }}", route)
            self.assertIn("if-no-files-found: error", route)
            self.assertIn("include-hidden-files: true", route)

    def test_both_routes_checkout_merge_commit_with_parent_for_site_docs(self):
        source = WORKFLOW.read_text()
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        for route in (trusted, hosted):
            checkout = route.split("uses: actions/checkout@", 1)[1].split("      - name:", 1)[0]
            self.assertIn("fetch-depth: 2", checkout)
            self.assertIn("persist-credentials: false", checkout)
        self.assertIn("ref: ${{ needs.preflight.outputs.source-sha }}", trusted)

    def test_trusted_baked_php_selection_checks_version_extensions_and_composer(self):
        source = WORKFLOW.read_text()
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        self.assertNotIn("shivammathur/setup-php@", trusted)
        self.assertIn("shivammathur/setup-php@f3e473d116dcccaddc5834248c87452386958240", hosted)
        self.assertNotIn("sudo", trusted_php_script())

        fake_php = """#!/usr/bin/env python3
import os
import sys
if len(sys.argv) >= 3 and sys.argv[1] == '-r':
    if 'PHP_MAJOR_VERSION' in sys.argv[2]:
        print(os.environ.get('FAKE_PHP_VERSION', '8.4'), end='')
    elif 'extension_loaded' in sys.argv[2]:
        if os.environ.get('FAKE_MISSING_EXTENSION') in sys.argv[3].split(','):
            raise SystemExit(1)
elif len(sys.argv) >= 2 and sys.argv[1] == os.environ['FAKE_COMPOSER_PATH']:
    print('Composer version ' + os.environ.get('FAKE_COMPOSER_VERSION', '2.9.0'))
else:
    raise SystemExit(2)
"""
        fake_root = Path(self.temp.name)
        fake_bin = fake_root / "bin"
        fake_bin.mkdir()
        fake_composer = fake_root / "composer"
        fake_composer.write_text("fixture")
        for version in ("8.3", "8.4"):
            binary = fake_bin / f"php{version}"
            binary.write_text(fake_php)
            binary.chmod(0o755)
        script = trusted_php_script().replace("/usr/bin/php", str(fake_bin / "php"))
        script = script.replace("/usr/local/bin/composer", str(fake_composer))

        cases = (
            ("Tuinstra-DEV/gate", "gate-php", "8.3", "ctype, iconv", "8.3", "", "", True),
            ("Tuinstra-DEV/tracker", "tracker-backend-postgres", "8.4",
             "ctype, fileinfo, iconv, intl, mbstring, openssl, pdo_pgsql, zip", "8.4", "", "", True),
            ("Tuinstra-DEV/console", "console-php-postgres", "8.4", "intl, pdo_pgsql", "8.4", "", "", True),
            ("Tuinstra-DEV/gate", "gate-php", "8.3", "ctype, iconv", "8.4", "", "", False),
            ("Tuinstra-DEV/console", "console-php-postgres", "8.4", "intl, pdo_pgsql", "8.4", "intl", "", False),
            ("Tuinstra-DEV/gate", "gate-php", "8.3", "ctype, iconv", "8.3", "", "3.0.0", False),
            ("Tuinstra-DEV/gate", "gate-php", "8.4", "ctype, iconv", "8.4", "", "", False),
        )
        for index, (repo, key, version, extensions, actual, missing, composer_version, succeeds) in enumerate(cases):
            with self.subTest(repo=repo, key=key, index=index):
                path_file = fake_root / f"path-{index}"
                env = {**os.environ, "RUNNER_TEMP": str(fake_root), "GITHUB_PATH": str(path_file),
                       "GITHUB_REPOSITORY": repo, "JOB_KEY": key, "PHP_VERSION": version,
                       "PHP_EXTENSIONS": extensions, "FAKE_PHP_VERSION": actual,
                       "FAKE_MISSING_EXTENSION": missing, "FAKE_COMPOSER_PATH": str(fake_composer),
                       "FAKE_COMPOSER_VERSION": composer_version or "2.9.0"}
                result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                self.assertEqual(path_file.exists(), succeeds)
                if succeeds:
                    shim = Path(path_file.read_text().strip())
                    self.assertEqual(shim.parent, fake_root)
                    self.assertEqual(subprocess.run([str(shim / "composer"), "--version"], env=env,
                                                    capture_output=True, text=True).returncode, 0)

    def test_download_caches_use_fixed_lockfile_identities_on_both_routes(self):
        source = WORKFLOW.read_text()
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        for route in (trusted, hosted):
            self.assertIn("cache: npm\n          cache-dependency-path: package-lock.json", route)
            self.assertIn("cache: pnpm\n          cache-dependency-path: frontend/pnpm-lock.yaml", route)
            self.assertIn("code/web/pnpm-lock.yaml\n            code/site/pnpm-lock.yaml", route)
            self.assertIn("gate-${{ runner.os }}-${{ runner.arch }}-php83-composer-${{ hashFiles('backend/composer.lock') }}", route)
            self.assertIn("tracker-${{ runner.os }}-${{ runner.arch }}-php84-composer-${{ hashFiles('code/api/composer.lock') }}", route)
            self.assertIn("console-${{ runner.os }}-${{ runner.arch }}-php84-composer-${{ hashFiles('composer.lock') }}", route)
            self.assertIn("echo \"COMPOSER_CACHE_DIR=$cache_path\" >> \"$GITHUB_ENV\"", route)
            self.assertEqual(route.count("actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"), 4)
            self.assertIn("~/.gradle/caches/modules-2", route)
            self.assertIn("~/.gradle/wrapper/dists", route)
            self.assertIn("flutter/pubspec.lock', 'flutter/android/gradle.properties'", route)
            self.assertNotIn("restore-keys:", route)
            self.assertNotIn("node_modules", route)
            self.assertNotIn("vendor/", route)

    def test_composer_cache_does_not_require_a_root_manifest(self):
        source = WORKFLOW.read_text()
        for route in (source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0],
                      source.split("  hosted-fallback:", 1)[1]):
            step = route.split("      - name: Resolve fixed Composer download cache", 1)[1]
            script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("      - name:", 1)[0])
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for repository in ("Tuinstra-DEV/gate", "Tuinstra-DEV/tracker", "Tuinstra-DEV/console"):
                    for environment in ("self-hosted", "github-hosted"):
                        output, export = root / "output", root / "export"
                        output.write_text("")
                        export.write_text("")
                        env = {**os.environ, "RUNNER_TEMP": str(root), "GITHUB_OUTPUT": str(output),
                               "GITHUB_ENV": str(export), "GITHUB_REPOSITORY": repository,
                               "RUNNER_ENVIRONMENT": environment}
                        result = subprocess.run(["bash", "-c", script], cwd=root, env=env,
                                                capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(output.read_text(), f"path={root}/composer-cache\n")
                        self.assertEqual(export.read_text(), f"COMPOSER_CACHE_DIR={root}/composer-cache\n")
                        self.assertTrue((root / "composer-cache").is_dir())

    def test_fixed_docker_layer_cache_is_local_bounded_and_after_verification(self):
        source = WORKFLOW.read_text()
        self.assertNotIn("ghaction-github-runtime", source)
        self.assertNotIn("type=gha", source)
        self.assertNotIn("ACTIONS_RUNTIME_TOKEN", source)
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        for route in (trusted, hosted):
            self.assertIn("CI_BUILDKIT_CACHE_ROOT=$RUNNER_TEMP/ci-buildkit-cache", route)
            self.assertIn("hashFiles('**/composer.lock', '**/package-lock.json', '**/pnpm-lock.yaml', '**/Dockerfile', '**/Dockerfile.*')", route)
            self.assertIn("pr-ci-v1-${repo_slug}-${JOB_KEY}-${RUNNER_OS}-${RUNNER_ARCH}-${SOURCE_DIGEST}-${engine_digest}", route)
            self.assertIn("docker buildx version", route)
            self.assertIn("docker version --format '{{.Server.Version}}'", route)
            self.assertIn("uses: actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9", route)
            self.assertIn("uses: actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9", route)
            self.assertIn("size_kib > 2097152", route)
            self.assertIn("size_kib <= 2097152", route)
            self.assertIn("find \"$cache_root\" -type l", route)
            self.assertIn("steps.docker-cache-restore.outputs.cache-hit != 'true'", route)
            self.assertNotIn("restore-keys:", route)
            self.assertLess(route.index("Restore exact local Docker layer cache"), route.index("Run fixed consumer adapter"))
            self.assertLess(route.index("Run fixed consumer adapter"), route.index("Save bounded local Docker layers after verification"))
            self.assertLess(route.index("Save bounded local Docker layers after verification"), route.index("run: echo 'verified=true'"))

    def test_openairco_and_status_fixed_heavy_toolchains_on_both_routes(self):
        source = WORKFLOW.read_text()
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        for route in (trusted, hosted):
            self.assertIn("github.repository == 'Tuinstra-DEV/status'", route)
            self.assertIn("actions/setup-java@cf277c60eb25467037889841efdb72551f06f6c3", route)
            self.assertIn("java-version: '17'", route)
            self.assertIn("android-actions/setup-android@9fc6c4e9069bf8d3d10b2204b1fb8f6ef7065407", route)
            android = route.split("      - name: Set up OpenAirco Android SDK", 1)[1].split("      - name:", 1)[0]
            # The pinned action defaults to obsolete `tools platform-tools`.
            self.assertRegex(android, r"(?m)^          packages: ['\"]?platform-tools['\"]?$")
            self.assertIn("run: sdkmanager 'platforms;android-37'", route)
            self.assertIn("subosito/flutter-action@1a449444c387b1966244ae4d4f8c696479add0b2", route)
            self.assertIn("flutter-version: 3.47.1", route)
            self.assertIn("pub-cache-key: openairco-${{ runner.os }}-${{ runner.arch }}-flutter-3.47.1-stable-pub", route)


if __name__ == "__main__":
    unittest.main()
