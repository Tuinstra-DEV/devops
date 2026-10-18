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


def legacy_source():
    return WORKFLOW.read_text().split("  gate-php-trusted:", 1)[0]


def bundle_script():
    match = re.search(r"^          python3 - <<'BUNDLE'\n(?P<script>.*?)^          BUNDLE$", WORKFLOW.read_text(), re.M | re.S)
    if not match:
        raise AssertionError("strict bundle contract missing")
    return textwrap.dedent(match.group("script"))


def identity_script():
    match = re.search(r"^          python3 - <<'IDENTITY'\n(?P<script>.*?)^          IDENTITY$", WORKFLOW.read_text(), re.M | re.S)
    if not match:
        raise AssertionError("bundle identity contract missing")
    return textwrap.dedent(match.group("script"))


def preflight_script():
    source = legacy_source()
    match = re.search(r"^          python3 - <<'PY'\n(?P<script>.*?)^          PY$", source, re.M | re.S)
    if not match:
        raise AssertionError("hosted inline Python preflight missing")
    return textwrap.dedent(match.group("script"))


def contract_script():
    source = legacy_source()
    match = re.search(r"^          python3 - <<'CONTRACT'\n(?P<script>.*?)^          CONTRACT$", source, re.M | re.S)
    if not match:
        raise AssertionError("fixed consumer contract missing")
    return textwrap.dedent(match.group("script"))


def trusted_php_script():
    source = legacy_source()
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
            "run_attempt": 1,
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
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_ACTOR": "marcel-tuinstra",
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

    def execute_bundle(self, bundle, selection, repo=None, **legacy):
        self.output_path.write_text("")
        env = {"GITHUB_REPOSITORY": repo or REPO, "GITHUB_OUTPUT": str(self.output_path),
               "BUNDLE": bundle, "SELECTION_JSON": selection,
               "PROFILE": legacy.get("profile", ""), "JOB_KEY": legacy.get("key", ""),
               "PLAN_JSON": legacy.get("plan", "")}
        with patch.dict(os.environ, env, clear=True):
            exec(compile(bundle_script(), str(WORKFLOW), "exec"), {"__name__": "__main__"})
        return dict(line.split("=", 1) for line in self.output_path.read_text().splitlines())

    def execute_identity(self, *, api_error=False):
        self.event_path.write_text(json.dumps(self.event))
        self.output_path.write_text("")
        def urlopen(request, timeout):
            self.assertEqual(timeout, 10)
            if api_error:
                raise OSError("simulated GitHub API outage")
            url = request.full_url
            if "/actions/runs/123" in url:
                value = self.run
            elif "/pulls/17" in url:
                value = self.pr
            elif "/git/ref/heads/" in url:
                value = self.ref
            elif f"/git/commits/{MERGE}" in url:
                value = self.merge
            else:
                self.fail(f"unexpected identity API path: {url}")
            return FakeResponse(json.dumps(value).encode())
        import urllib.request
        with patch.dict(os.environ, self.env, clear=True), patch.object(urllib.request, "urlopen", side_effect=urlopen):
            exec(compile(identity_script(), str(WORKFLOW), "exec"), {"__name__": "__main__"})
        return dict(line.split("=", 1) for line in self.output_path.read_text().splitlines())

    def test_bundle_contract_canonical_digest_and_exact_fixed_keys(self):
        import hashlib
        gate = {"schemaVersion": 1, "jobs": [
            {"job-key": "gate-php", "plan": {"backendSuites": "Unit", "quality": False, "apiSchema": True}},
            {"job-key": "gate-frontend-static", "plan": {"frontendTests": "unit", "frontendBuild": False,
                                                          "quality": False, "apiTypes": True}},
        ]}
        raw = json.dumps(gate, indent=2)
        result = self.execute_bundle("gate-quality", raw, "Tuinstra-DEV/gate")
        canonical = json.dumps(gate, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(result["selection-sha256"], hashlib.sha256(canonical.encode()).hexdigest())
        self.assertEqual(json.loads(result["selected-jobs"]), ["gate-php", "gate-frontend-static"])
        plans = json.loads(result["bundle-plans"])
        self.assertEqual(json.loads(plans["gate-php"]), gate["jobs"][0]["plan"])
        self.assertEqual(json.loads(plans["gate-frontend-static"]), gate["jobs"][1]["plan"])
        reordered = '{"jobs":[{"plan":{"quality":false,"apiSchema":true,"backendSuites":"Unit"},"job-key":"gate-php"},{"plan":{"apiTypes":true,"quality":false,"frontendBuild":false,"frontendTests":"unit"},"job-key":"gate-frontend-static"}],"schemaVersion":1}'
        self.assertEqual(self.execute_bundle("gate-quality", reordered, "Tuinstra-DEV/gate")["selection-sha256"], result["selection-sha256"])
        empty = self.execute_bundle("tracker-images", '{"schemaVersion":1,"jobs":[]}', "Tuinstra-DEV/tracker")
        self.assertEqual(empty["selected-jobs"], "[]")

    def test_bundle_contract_rejects_unsafe_or_partial_selections(self):
        php = {"job-key": "gate-php", "plan": {"backendSuites": "Unit", "quality": True, "apiSchema": False}}
        front = {"job-key": "gate-frontend-static", "plan": {"frontendTests": "unit", "frontendBuild": False,
                                                        "quality": True, "apiTypes": False}}
        selection = lambda jobs: json.dumps({"schemaVersion": 1, "jobs": jobs})
        wodiq = {"version": 2, **{key: False for key in ("core", "migration", "api", "management", "worker", "client")},
                 "builds": {key: False for key in ("api", "management", "worker")},
                 "proofs": {key: False for key in ("all", "activity", "coach", "managementAuth", "garmin")}}
        cases = [
            ("gate-quality", "Tuinstra-DEV/gate", selection([php, php]), {}),
            ("gate-quality", "Tuinstra-DEV/gate", selection([{**php, "command": "id"}]), {}),
            ("gate-quality", "Tuinstra-DEV/gate", selection([{**php, "plan": {**php["plan"], "quality": "true"}}]), {}),
            ("gate-quality", "Tuinstra-DEV/gate", selection([{**php, "plan": {**php["plan"], "quality": False}}]), {}),
            ("gate-quality", "Tuinstra-DEV/gate", selection([php, {**front, "plan": {**front["plan"], "apiTypes": True}}]), {}),
            ("gate-quality", "Tuinstra-DEV/gate", '{"schemaVersion":1,"jobs":[],"jobs":[]}', {}),
            ("gate-quality", "Tuinstra-DEV/gate", selection([php]), {"profile": "medium"}),
            ("tracker-images", "Tuinstra-DEV/gate", selection([]), {}),
            ("wodiq-platform", REPO, selection([{"job-key": "wodiq-api", "plan": wodiq}]), {}),
        ]
        for bundle, repo, raw, legacy in cases:
            with self.subTest(bundle=bundle, raw=raw), self.assertRaises(SystemExit):
                self.execute_bundle(bundle, raw, repo, **legacy)
        all_keys = ("wodiq-core", "wodiq-runtime-build", "wodiq-api", "wodiq-management", "wodiq-worker")
        full = self.execute_bundle("wodiq-platform", selection([{"job-key": key, "plan": wodiq} for key in all_keys]))
        self.assertEqual(json.loads(full["selected-jobs"]), list(all_keys))

    def test_bundle_identity_binds_hosted_route_to_current_attempt_and_pin(self):
        self.env.update(BUNDLE="wodiq-platform", PROFILE="", JOB_KEY="", GITHUB_ACTOR="untrusted-user")
        trust, _ = self.execute()
        self.assertEqual(trust["trusted"], "false")
        identity = self.execute_identity()
        self.assertEqual(identity, {"trusted": "false", "source-sha": MERGE, "head-sha": SHA, "base-sha": BASE,
                                    "run-id": "123", "run-attempt": "1", "workflow-sha": WORKFLOW_SHA})
        self.env["GITHUB_ACTOR"] = "marcel-tuinstra"
        self.assertEqual(self.execute_identity()["trusted"], "true")
        with self.assertRaises(OSError):
            self.execute_identity(api_error=True)
        cases = [
            lambda: self.run.update(run_attempt=2),
            lambda: self.run["referenced_workflows"][0].update(sha=SHA),
            lambda: self.pr["head"].update(sha=BASE),
            lambda: self.merge.update(parents=[{"sha": SHA}, {"sha": SHA}]),
        ]
        for mutation in cases:
            with self.subTest(mutation=str(mutation)):
                self.setUp()
                mutation()
                with self.assertRaises(SystemExit):
                    self.execute_identity()

    def test_bundle_static_jobs_proof_and_least_privilege(self):
        import subprocess
        workflow = WORKFLOW.read_text()
        helper_sha = "7c284a1b4405d844e581ad369b754885ab2415cc"
        bundles = {
            "gate-quality": ("gate-php", "gate-frontend-static"),
            "tracker-quality": ("tracker-backend-postgres", "tracker-frontend-static"),
            "tracker-images": ("tracker-container-api", "tracker-container-web", "tracker-container-php-prod", "tracker-container-nginx-prod"),
            "wodiq-platform": ("wodiq-core", "wodiq-runtime-build", "wodiq-api", "wodiq-management", "wodiq-worker"),
        }
        for keys in bundles.values():
            for key in keys:
                for route in ("trusted", "hosted"):
                    block = re.split(r"(?m)^  [a-z][a-z0-9-]*:\n", workflow.split(f"  {key}-{route}:\n", 1)[1], maxsplit=1)[0]
                    self.assertIn("permissions:\n      contents: read", block)
                    self.assertNotIn("actions: read", block)
                    self.assertNotIn("pull-requests: read", block)
                    self.assertIn(f"trusted-bundle-{route}@{helper_sha}", block)
                    if route == "trusted":
                        profile = "medium" if key in ("gate-php", "tracker-frontend-static", "wodiq-core", "wodiq-runtime-build") else "heavy"
                        self.assertIn(f"labels: [self-hosted, trusted-{profile}, sanctuary-", block)
                    self.assertIn("verified: ${{ steps.execute.outputs.verified }}", block)
        self.assertIn("value: ${{ toJSON(jobs) }}", workflow)
        self.assertTrue(" ".join(IsolatedRoleRoutingTests.document(WORKFLOW)["jobs"]["preflight"]["runs-on"].split()).endswith("|| 'ubuntu-slim' }}"))
        for route in ("trusted", "hosted"):
            helper = WORKFLOW.parents[2] / f".github/actions/trusted-bundle-{route}/action.yml"
            text = helper.read_text()
            self.assertLess(text.index("bash .github/ci/sanctuary-ci --strict"),
                            text.index("run: echo 'verified=true'"))
            self.assertIn("ref: ${{ inputs.source-sha }}", text)
            self.assertIn("ci-runtime-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}", text)
        ruby = subprocess.run(["ruby", "-e", "require 'yaml'; ARGV.each { |p| YAML.safe_load(File.read(p), aliases: true) }",
                               str(WORKFLOW), *(str(WORKFLOW.parents[2] / f".github/actions/trusted-bundle-{route}/action.yml") for route in ("trusted", "hosted"))],
                              capture_output=True, text=True)
        self.assertEqual(ruby.returncode, 0, ruby.stderr)

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
        source = legacy_source()
        self.assertIn("group: sanctuary-trusted-verification", source)
        self.assertIn("labels: ${{ fromJSON(needs.preflight.outputs.runner-labels) }}", source)
        self.assertIn("runs-on: ubuntu-24.04", source)
        self.assertIn("if: ${{ inputs.bundle == '' && needs.preflight.outputs.trusted != 'true' }}", source)
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
        source = legacy_source()
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
        source = legacy_source()
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
        source = legacy_source()
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        for route in (trusted, hosted):
            checkout = route.split("uses: actions/checkout@", 1)[1].split("      - name:", 1)[0]
            self.assertIn("fetch-depth: 2", checkout)
            self.assertIn("persist-credentials: false", checkout)
        self.assertIn("ref: ${{ needs.preflight.outputs.source-sha }}", trusted)

    def test_trusted_baked_php_selection_checks_version_extensions_and_composer(self):
        source = legacy_source()
        trusted = source.split("  trusted-verification:", 1)[1].split("  hosted-fallback:", 1)[0]
        hosted = source.split("  hosted-fallback:", 1)[1]
        self.assertNotIn("shivammathur/setup-php@", trusted)
        self.assertIn("shivammathur/setup-php@f3e473d116dcccaddc5834248c87452386958240", hosted)
        self.assertNotIn("sudo", trusted_php_script())
        self.assertIn('"gate-php": ("medium", "", "", "8.3", "ctype, iconv, pdo_sqlite, sqlite3")', source)

        fake_php = """#!/usr/bin/env python3
import os
import sys
if len(sys.argv) >= 3 and sys.argv[1] == '-r':
    if 'PHP_MAJOR_VERSION' in sys.argv[2]:
        print(os.environ.get('FAKE_PHP_VERSION', '8.4'), end='')
    elif 'extension_loaded' in sys.argv[2]:
        if os.environ.get('FAKE_MISSING_EXTENSION') in sys.argv[3].split(','):
            raise SystemExit(1)
    elif 'sqlite::memory:' in sys.argv[2]:
        if os.environ.get('FAKE_MISSING_EXTENSION') == 'sqlite-driver':
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
            ("Tuinstra-DEV/gate", "gate-php", "8.3", "ctype, iconv, pdo_sqlite, sqlite3", "8.3", "", "", True),
            ("Tuinstra-DEV/tracker", "tracker-backend-postgres", "8.4",
             "ctype, fileinfo, iconv, intl, mbstring, openssl, pdo_pgsql, zip", "8.4", "", "", True),
            ("Tuinstra-DEV/console", "console-php-postgres", "8.4", "intl, pdo_pgsql", "8.4", "", "", True),
            ("Tuinstra-DEV/gate", "gate-php", "8.3", "ctype, iconv, pdo_sqlite, sqlite3", "8.4", "", "", False),
            ("Tuinstra-DEV/gate", "gate-php", "8.3", "ctype, iconv, pdo_sqlite, sqlite3", "8.3", "pdo_sqlite", "", False),
            ("Tuinstra-DEV/gate", "gate-php", "8.3", "ctype, iconv, pdo_sqlite, sqlite3", "8.3", "sqlite-driver", "", False),
            ("Tuinstra-DEV/console", "console-php-postgres", "8.4", "intl, pdo_pgsql", "8.4", "intl", "", False),
            ("Tuinstra-DEV/gate", "gate-php", "8.3", "ctype, iconv, pdo_sqlite, sqlite3", "8.3", "", "3.0.0", False),
            ("Tuinstra-DEV/gate", "gate-php", "8.4", "ctype, iconv, pdo_sqlite, sqlite3", "8.4", "", "", False),
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
        source = legacy_source()
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
        source = legacy_source()
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
        source = legacy_source()
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
        source = legacy_source()
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
            self.assertIn("run: sdkmanager 'platforms;android-37.0'", route)
            self.assertIn("subosito/flutter-action@1a449444c387b1966244ae4d4f8c696479add0b2", route)
            self.assertIn("flutter-version: 3.47.1", route)
            self.assertIn("pub-cache-key: openairco-${{ runner.os }}-${{ runner.arch }}-flutter-3.47.1-stable-pub", route)

class IsolatedRoleRoutingTests(unittest.TestCase):
    REQUEST = 'request-isolated-role-runner'
    PRODUCER = WORKFLOW.with_name('reusable-gate-pr-security.yml')
    ORIGINAL_HASHES = {
        WORKFLOW.name: 'dd1c5f4f332a542c7f882e38eb491e255591120332660ca351477d51f42c2388',
        PRODUCER.name: 'f81578d9db84d2ac1b128094acf57f6de15857915862934c9fa0ab2e23d65dee',
    }

    @staticmethod
    def document(path):
        # Ruby/Psych is already a required repository verification dependency.
        result = subprocess.run(['ruby', '-rjson', '-ryaml', '-e',
                                 'print JSON.generate(YAML.safe_load(STDIN.read, aliases: false))'],
                                input=path.read_text(), text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    @staticmethod
    def request_guards(event):
        guards = [
            "inputs['request-isolated-role-runner'] == true",
            "contains(fromJSON('[\"Tuinstra-DEV/gate\",\"Tuinstra-DEV/tracker\"]'), github.repository)",
            'github.event.repository.full_name == github.repository',
            "toJSON(github.event.repository.private) == 'true'",
            "toJSON(github.event.repository.fork) == 'false'",
            f"github.event_name == '{event}'",
            "github.actor == 'marcel-tuinstra'",
            "github.triggering_actor == 'marcel-tuinstra'",
            "github.event.sender.login == 'marcel-tuinstra'",
            "github.event.sender.type == 'User'",
            "github.event.pull_request.user.login == 'marcel-tuinstra'",
            "github.event.pull_request.user.type == 'User'",
            "github.event.pull_request.state == 'open'",
            "toJSON(github.event.pull_request.merged) == 'false'",
            'github.event.pull_request.number > 0',
            "github.event.pull_request.head.sha != ''",
            "github.event.pull_request.head.ref != ''",
            "github.event.pull_request.base.ref != ''",
            'github.event.pull_request.head.repo.full_name == github.repository',
            "toJSON(github.event.pull_request.head.repo.private) == 'true'",
            "toJSON(github.event.pull_request.head.repo.fork) == 'false'",
            'github.event.pull_request.base.repo.full_name == github.repository',
            "toJSON(github.event.pull_request.base.repo.private) == 'true'",
            "toJSON(github.event.pull_request.base.repo.fork) == 'false'",
        ]
        if event == 'pull_request_target':
            guards += [
                "github.event.repository.default_branch != ''",
                "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)",
                "github.sha != ''",
            ]
        return guards

    def test_request_is_exactly_default_false_boolean_without_authority_inputs(self):
        expected = {'description': 'Request isolated scheduling under the fixed role policy; does not grant trust',
                    'required': False, 'default': False, 'type': 'boolean'}
        for path in (WORKFLOW, self.PRODUCER):
            with self.subTest(workflow=path.name):
                doc = self.document(path)
                call = doc.get('on', doc.get('true'))['workflow_call']
                self.assertIsInstance(call, dict)
                self.assertEqual(call['inputs'][self.REQUEST], expected)
                self.assertNotIn('secrets', call)
                if path == self.PRODUCER:
                    self.assertEqual(set(call), {'inputs'})
                    self.assertEqual(set(call['inputs']), {self.REQUEST})
                else:
                    self.assertEqual(set(call['inputs']), {self.REQUEST, 'profile', 'job-key',
                                                          'plan-json', 'bundle', 'selection-json'})

    def test_exact_guarded_selector_preserves_group_and_derives_all_labels_from_github(self):
        for path, job_id, event, role, hosted in (
                (WORKFLOW, 'preflight', 'pull_request', 'ci-metadata-preflight', 'ubuntu-slim'),
                (self.PRODUCER, 'producer', 'pull_request_target', 'ci-pr-security-producer', 'ubuntu-24.04')):
            with self.subTest(workflow=path.name):
                job = self.document(path)['jobs'][job_id]
                selector = ' '.join(job['runs-on'].split())
                prefix, template = selector.rsplit(' && fromJSON(format(', 1)
                self.assertEqual(prefix, '${{ ' + ' && '.join(self.request_guards(event)))
                execution_slot = ',{3}' if event == 'pull_request_target' else ''
                execution_argument = ", toJSON(format('ci-execution-sha-{0}', github.sha))" if event == 'pull_request_target' else ''
                self.assertEqual(template,
                    "'{{\"group\":\"sanctuary-trusted-verification\",\"labels\":[\"self-hosted\",\"" + role +
                    "\",{0},{1},{2}" + execution_slot + "]}}', "
                    "toJSON(github.repository == 'Tuinstra-DEV/gate' && 'sanctuary-gate' || 'sanctuary-tracker'), "
                    "toJSON(format('ci-run-{0}-{1}', github.run_id, github.run_attempt)), "
                    "toJSON(format('ci-pr-head-{0}', github.event.pull_request.head.sha))" + execution_argument + ")) || '" + hosted + "' }}")
                self.assertNotIn('trusted-heavy', selector)
                self.assertNotIn('trusted-medium', selector)
                self.assertNotIn('vars.', selector)
                self.assertNotIn('needs.', selector)
                self.assertNotIn('secrets.', selector)
                self.assertNotIn('strategy', job)
                self.assertNotIn('if', job)
                self.assertNotIn('continue-on-error', job)

    def test_all_preexisting_execution_bytes_survive_runner_selection_change(self):
        import hashlib
        for path, hosted in ((WORKFLOW, 'ubuntu-slim'), (self.PRODUCER, 'ubuntu-24.04')):
            source = path.read_text()
            source = re.sub(r'(?m)^    runs-on: >-\n(?:^      [^\n]*\n)+',
                            '    runs-on: ' + hosted + '\n', source, count=1)
            if path == WORKFLOW:
                source = re.sub(r'(?m)^      request-isolated-role-runner:\n(?:^        [^\n]*\n)+', '', source)
            else:
                source = re.sub(r'(?m)^    inputs:\n      request-isolated-role-runner:\n(?:^        [^\n]*\n)+', '', source)
            self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), self.ORIGINAL_HASHES[path.name])

    def test_exact_role_labels_are_compatible_with_manager_and_not_trusted_labels(self):
        import importlib.util
        path = WORKFLOW.parents[2] / 'runner/manager/ci_runner_manager.py'
        spec = importlib.util.spec_from_file_location('central_routing_manager_contract', path)
        manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manager)
        for repo in ('gate', 'tracker'):
            for role in ('metadata-preflight', 'pr-security-producer'):
                labels = ['self-hosted', 'ci-' + role, 'sanctuary-' + repo,
                          'ci-run-123-4', 'ci-pr-head-' + 'a' * 40]
                execution = 'b' * 40 if role == 'pr-security-producer' else ''
                if execution:
                    labels.append('ci-execution-sha-' + execution)
                name = 'Tuinstra-DEV/' + repo
                self.assertTrue(manager.role_labels_match(labels, name, role, 123, 4, 'a' * 40, execution))
                self.assertFalse(manager.role_labels_match(labels, name, role, 124, 4, 'a' * 40, execution))
                self.assertFalse(manager.role_labels_match(labels, name, role, 123, 5, 'a' * 40, execution))
                self.assertFalse(manager.role_labels_match(labels, name, role, 123, 4, 'b' * 40, execution))
                self.assertFalse(manager.organization_labels_match(labels, name, 'heavy'))
                self.assertFalse(manager.organization_labels_match(labels, name, 'medium'))

    def test_merge_group_still_reaches_visible_producer_unsupported_event_failure(self):
        producer = self.document(self.PRODUCER)['jobs']['producer']
        self.assertNotIn('if', producer)
        self.assertNotIn('continue-on-error', producer)
        self.assertEqual(producer['steps'][0]['uses'],
                         'Tuinstra-DEV/devops/.github/actions/gate-pr-security@b2dda8a614e776837baab920c8570848a0062b7b')
        source = WORKFLOW.parents[2] / '.github/actions/gate-pr-security/source.py'
        tree = ast.parse(source.read_text())
        function = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == 'provider_state')
        calls = []
        def require(ok, code):
            calls.append(code)
            if not ok:
                raise ValueError(code)
        namespace = {'require': require}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        with self.assertRaisesRegex(ValueError, '^unsupported_event$'):
            namespace['provider_state']({'GATE_EVENT_NAME': 'merge_group'})
        self.assertEqual(calls, ['unsupported_event'])


if __name__ == "__main__":
    unittest.main()
