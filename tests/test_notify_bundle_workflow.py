"""Execute the Notify bundle selector and required verdict against route failures."""
import json
import os
from pathlib import Path
import re
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from tests import test_trusted_verification_workflow as contracts

WORKFLOW = contracts.WORKFLOW
bundle_script = contracts.bundle_script


class NotifyBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "output"

    def execute(self, source, environment):
        self.output.write_text("")
        with patch.dict(os.environ, {**environment, "GITHUB_OUTPUT": str(self.output)}, clear=True):
            exec(compile(source, str(WORKFLOW), "exec"), {"__name__": "__main__"})
        return self.output.read_text()

    @staticmethod
    def verdict_script():
        source = re.search(r"^          python3 - <<'NOTIFY'\n(.*?)^          NOTIFY$",
                           WORKFLOW.read_text(), re.M | re.S)
        return textwrap.dedent(source.group(1))

    @staticmethod
    def route(trusted=True, keys=("notify-quality", "notify-production-images")):
        values = {"PREFLIGHT_RESULT": "success", "TRUSTED": str(trusted).lower(),
                  "SELECTED_JSON": json.dumps(list(keys))}
        for key, prefix in (("notify-quality", "QUALITY"), ("notify-production-images", "IMAGES")):
            for candidate in ("TRUSTED", "HOSTED"):
                active = key in keys and candidate == ("TRUSTED" if trusted else "HOSTED")
                values[prefix + "_" + candidate + "_RESULT"] = "success" if active else "skipped"
                values[prefix + "_" + candidate + "_PROOF"] = "true" if active else ""
        return values

    def test_selection_has_only_fixed_notify_keys_and_empty_plans(self):
        selection = {"schemaVersion": 1, "jobs": [
            {"job-key": key, "plan": {}} for key in ("notify-quality", "notify-production-images")]}
        environment = {"GITHUB_REPOSITORY": "Tuinstra-DEV/notify", "BUNDLE": "notify",
                       "PROFILE": "", "JOB_KEY": "", "PLAN_JSON": "",
                       "SELECTION_JSON": json.dumps(selection)}
        self.assertIn('selected-jobs=["notify-quality","notify-production-images"]',
                      self.execute(bundle_script(), environment))
        for changed in (
            {"GITHUB_REPOSITORY": "Tuinstra-DEV/gate"}, {"PROFILE": "heavy"},
            {"SELECTION_JSON": '{"schemaVersion":1,"jobs":[]}'},
            {"SELECTION_JSON": json.dumps({"schemaVersion": 1, "jobs": selection["jobs"] * 2})},
            {"SELECTION_JSON": '{"schemaVersion":1,"jobs":[{"job-key":"notify-quality","plan":{"command":"x"}}]}'},
            {"SELECTION_JSON": '{"schemaVersion":1,"jobs":[{"job-key":"unknown","plan":{}}]}'},
        ):
            with self.subTest(changed=changed), self.assertRaises(SystemExit):
                self.execute(bundle_script(), {**environment, **changed})

    def test_each_route_and_selection_requires_complete_unambiguous_proofs(self):
        for trusted in (True, False):
            for keys in (("notify-quality",), ("notify-production-images",),
                         ("notify-quality", "notify-production-images")):
                environment = self.route(trusted, keys)
                self.assertEqual(self.execute(self.verdict_script(), environment), "verified=true\n")
                for name, value in environment.items():
                    if name.endswith("_RESULT"):
                        for invalid in ("failure", "cancelled", "skipped" if value == "success" else "success"):
                            with self.subTest(route=trusted, keys=keys, name=name, invalid=invalid), self.assertRaises(SystemExit):
                                self.execute(self.verdict_script(), {**environment, name: invalid})
                    if name.endswith("_PROOF"):
                        with self.subTest(route=trusted, keys=keys, name=name), self.assertRaises(SystemExit):
                            self.execute(self.verdict_script(), {**environment, name: "" if value else "true"})
        for invalid in ({"TRUSTED": ""}, {"TRUSTED": "unknown"}, {"SELECTED_JSON": "[]"},
                        {"SELECTED_JSON": "{}"}, {"SELECTED_JSON": "not JSON"},
                        {"SELECTED_JSON": '["notify-quality","notify-quality"]'},
                        {"SELECTED_JSON": '["other"]'}):
            with self.subTest(invalid=invalid), self.assertRaises(SystemExit):
                self.execute(self.verdict_script(), {**self.route(), **invalid})

    def test_one_hosted_job_combines_fallback_and_always_running_required_verdict(self):
        document = contracts.IsolatedRoleRoutingTests.document(WORKFLOW)
        jobs = document["jobs"]
        verdict = jobs["notify-verdict"]
        self.assertEqual(verdict["name"], "Notify verification")
        self.assertEqual(verdict["runs-on"], "ubuntu-24.04")
        self.assertEqual(verdict["if"], "${{ always() && inputs.bundle == 'notify' }}")
        self.assertEqual(verdict["permissions"], {"contents": "read"})
        self.assertEqual(verdict["needs"], ["preflight", "notify-quality-trusted", "notify-production-images-trusted"])
        self.assertEqual(verdict["steps"][-1]["if"], "${{ always() }}")
        for key in ("notify-quality", "notify-production-images"):
            job = jobs[key + "-trusted"]
            self.assertEqual(job["runs-on"], {"group": "sanctuary-trusted-verification",
                                              "labels": ["self-hosted", "trusted-heavy", "sanctuary-notify"]})
            self.assertEqual(job["permissions"], {"contents": "read"})
            self.assertNotIn("continue-on-error", job)
            self.assertEqual(job["steps"][0]["uses"],
                             "Tuinstra-DEV/devops/.github/actions/trusted-bundle-trusted@7c284a1b4405d844e581ad369b754885ab2415cc")
        interface = document.get("on", document.get("true"))["workflow_call"]
        self.assertEqual(interface["outputs"]["notify-proof"]["value"], "${{ jobs.notify-verdict.outputs.verified }}")


if __name__ == "__main__":
    unittest.main()
