"""Fixed repository-to-native-image bindings for the trusted PR producer."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[1]
ACTION = ROOT / ".github/actions/gate-pr-security"
sys.path.insert(0, str(ACTION))
import constants
import evidence

FIXTURES = ROOT / "tests/fixtures/gate-pr-security"
EXPECTED_FROZEN = {
    "Tuinstra-DEV/gate": ("scanner-frozen.json", "4f004763e61e1e52c4e1c20188d87fb725273a2f187be580e662760dfefb6217"),
    "Tuinstra-DEV/tracker": ("scanner-frozen-tracker.json", "57c42d26810429cfff7d122c2001930c9f3fa64c977912a73020da162f751037"),
}
TRACKER_BINDING = {
    "image": "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:" + "a" * 64,
    "policy_digest": "sha256:70119e1c38a2598b4f2cfbcc69a3922819adff3c95ec492788a002e6087686e9",
    "bundle_digest": "sha256:" + "b" * 64,
    "dataset_digest": "sha256:" + "b" * 64,
}


def state(repository: str) -> dict:
    binding = TRACKER_BINDING.copy() if repository == "Tuinstra-DEV/tracker" else None
    profile = constants.profile_for_binding(repository, binding)
    return {
        "repository": repository,
        "repository_id": "100100",
        "owner_id": "200200",
        "pull_request": 1,
        "run_id": "300300",
        "run_attempt": 1,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "base_tree": "c" * 40,
        "head_tree": "d" * 40,
        "scanner_image": profile["image"],
        "scanner_exit": 0,
        "prepare_duration_ms": 1,
        "scan_duration_ms": 1,
        **({"scanner_binding": binding} if binding is not None else {}),
    }


def coverage_row(profile: dict, scope: str, scanner: str, version: str) -> dict:
    return {
        "scanner": scanner,
        "version": version,
        "scope": scope,
        "rules_digest": profile["bundle_digest"],
        "advisory_digest": profile["bundle_digest"] if scanner == "osv" else None,
        "base_result": "complete",
        "head_result": "complete",
    }


class NativeProfileTests(unittest.TestCase):
    def test_tracker_binding_requires_exact_valid_json_and_pinned_values(self):
        raw = json.dumps(TRACKER_BINDING)
        self.assertEqual(constants.parse_tracker_scanner_binding(raw), TRACKER_BINDING)
        for invalid in (
            None,
            "",
            "{}",
            raw[:-1] + ',"unexpected":true}',
            '{"image":"x","image":"y","policy_digest":"x","bundle_digest":"x","dataset_digest":"x"}',
            json.dumps(TRACKER_BINDING | {"image": "ghcr.io/tuinstra-dev/gate/ci-scanner:latest"}),
            json.dumps(TRACKER_BINDING | {"policy_digest": "sha256:" + "c" * 64}),
            json.dumps(TRACKER_BINDING | {"dataset_digest": "sha256:" + "c" * 64}),
            " " * 2049,
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                constants.parse_tracker_scanner_binding(invalid)

    def test_tracker_binding_is_required_and_cannot_select_another_repository(self):
        with self.assertRaisesRegex(ValueError, "^tracker_binding_required$"):
            constants.profile_for_binding("Tuinstra-DEV/tracker", None)
        with self.assertRaisesRegex(ValueError, "^binding_repository_mismatch$"):
            constants.profile_for_binding("Tuinstra-DEV/gate", TRACKER_BINDING)
        profile = constants.profile_for_binding("Tuinstra-DEV/tracker", TRACKER_BINDING)
        self.assertEqual(TRACKER_BINDING["image"], profile["image"])
        self.assertEqual(TRACKER_BINDING["bundle_digest"], profile["bundle_digest"])

    def test_action_exposes_tracker_binding_only_to_trusted_prepare_step(self):
        action = (ACTION / "action.yml").read_text(encoding="utf-8")
        self.assertEqual(1, action.count("GATE_TRACKER_SCANNER_BINDING:"))
        prepare = action.split("- name: Prepare exact PR sources", 1)[1].split("- name: Pull fixed scanner image", 1)[0]
        self.assertIn("GATE_TRACKER_SCANNER_BINDING: ${{ vars.GATE_TRACKER_SCANNER_BINDING }}", prepare)
        self.assertNotIn("GATE_TRACKER_SCANNER_BINDING", action.split("- name: Pull fixed scanner image", 1)[1])

    def test_evidence_requires_exact_tracker_binding_state(self):
        tracker = state("Tuinstra-DEV/tracker")
        self.assertEqual(TRACKER_BINDING["image"], evidence._validate_state(tracker)["image"])
        for invalid in (
            tracker | {"scanner_binding": TRACKER_BINDING | {"image": constants.profile_for("Tuinstra-DEV/gate")["image"]}},
            tracker | {"scanner_binding": TRACKER_BINDING | {"bundle_digest": "sha256:" + "c" * 64}},
            tracker | {"unexpected": "value"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(evidence.EvidenceError):
                evidence._validate_state(invalid)

        gate = state("Tuinstra-DEV/gate") | {"scanner_binding": TRACKER_BINDING.copy()}
        with self.assertRaisesRegex(evidence.EvidenceError, "^invalid_state$"):
            evidence._validate_state(gate)

    def test_only_exact_repository_names_select_profiles(self):
        gate = constants.profile_for("Tuinstra-DEV/gate")
        tracker = constants.profile_for("Tuinstra-DEV/tracker")
        self.assertNotEqual(gate["image"], tracker["image"])
        self.assertEqual(gate["platform"], "linux/amd64")
        self.assertEqual(tracker["platform"], "linux/amd64")
        for invalid in (None, 4, "", "tuinstra-dev/gate", "Tuinstra-DEV/other", "Tuinstra-DEV/tracker "):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "^unknown_repository$"):
                constants.profile_for(invalid)

    def test_frozen_fixtures_match_reviewed_hashes_and_tool_profiles(self):
        for repository, (filename, expected_digest) in EXPECTED_FROZEN.items():
            with self.subTest(repository=repository):
                raw = (FIXTURES / filename).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_digest)
                frozen = json.loads(raw)
                profile = constants.profile_for(repository)
                for tool, frozen_tool in frozen["tools"].items():
                    if tool in profile["tool_versions"]:
                        self.assertEqual(frozen_tool["version"], profile["tool_versions"][tool])
                self.assertEqual(frozen["tools"]["semgrep"]["version"], profile["tool_versions"]["semgrep"])
                self.assertEqual(frozen["tools"]["gitleaks"]["version"], profile["tool_versions"]["gitleaks"])
                self.assertEqual(frozen["tools"]["osv"]["version"], profile["tool_versions"]["osv"])
                self.assertIn("secrets", frozen["supported_scopes"])
                self.assertIn("python", frozen["supported_scopes"])
        tracker = json.loads((FIXTURES / "scanner-frozen-tracker.json").read_bytes())
        self.assertEqual(tracker["tools"]["node"]["version"], "24.21.0")
        self.assertEqual(tracker["tools"]["python"]["version"], "3.11.2")

    def test_state_image_is_selected_from_repository_binding(self):
        for repository in EXPECTED_FROZEN:
            with self.subTest(repository=repository):
                selected = constants.profile_for(repository)
                candidate = state(repository)
                selected = constants.profile_for_binding(repository, candidate.get("scanner_binding"))
                candidate["scanner_image"] = selected["image"]
                self.assertEqual(evidence._validate_state(candidate), selected)
        wrong = state("Tuinstra-DEV/tracker")
        wrong["scanner_image"] = constants.profile_for("Tuinstra-DEV/gate")["image"]
        with self.assertRaisesRegex(evidence.EvidenceError, "^scanner_image_mismatch$"):
            evidence._validate_state(wrong)
        unknown = state("Tuinstra-DEV/gate")
        unknown["repository"] = "Tuinstra-DEV/unknown"
        with self.assertRaisesRegex(evidence.EvidenceError, "^unknown_repository$"):
            evidence._validate_state(unknown)

    def test_report_and_coverage_reject_cross_profile_bindings(self):
        report = json.loads((FIXTURES / "pair-pass.json").read_bytes())
        for repository in EXPECTED_FROZEN:
            candidate_state = state(repository)
            profile = constants.profile_for_binding(repository, candidate_state.get("scanner_binding"))
            candidate = copy.deepcopy(report)
            candidate["bundle_digest"] = profile["bundle_digest"]
            candidate["dataset_manifest_digest"] = profile["dataset_digest"]
            candidate["policy_digest"] = profile["policy_digest"]
            for row in candidate["coverage"]:
                row["rules_digest"] = profile["bundle_digest"]
            evidence._validate_report(candidate_state, candidate)

        tracker = constants.profile_for("Tuinstra-DEV/tracker")
        row = coverage_row(tracker, "static-assets", "gate-assets", "1")
        secrets = coverage_row(tracker, "secrets", "gitleaks", "8.30.1")
        self.assertEqual(evidence._coverage({"coverage": [secrets, row]}, True, tracker), [secrets, row])
        wrong_scanner = copy.deepcopy(row)
        wrong_scanner["scanner"] = "gate-text"
        wrong_scanner["version"] = "1"
        with self.assertRaisesRegex(evidence.EvidenceError, "^untrusted_coverage$"):
            evidence._coverage({"coverage": [secrets, wrong_scanner]}, True, tracker)
        wrong_tool_version = coverage_row(tracker, "secrets", "gitleaks", "8.30.1")
        wrong_tool_version["version"] = "8.30.0"
        with self.assertRaisesRegex(evidence.EvidenceError, "^untrusted_coverage$"):
            evidence._coverage({"coverage": [wrong_tool_version],}, True, tracker)

    def test_exclusion_files_are_profile_bound_and_tracker_has_no_exclusions(self):
        for repository in EXPECTED_FROZEN:
            profile = constants.profile_for(repository)
            path = ACTION / profile["exclusions_file"]
            raw = path.read_bytes()
            self.assertEqual("sha256:" + hashlib.sha256(raw).hexdigest(), profile["excluded_inputs_digest"])
            document = json.loads(raw)
            self.assertEqual(document["policy_digest"], profile["policy_digest"])
            self.assertIsInstance(evidence._trusted_exclusions(profile), dict)
        tracker = constants.profile_for("Tuinstra-DEV/tracker")
        self.assertEqual(evidence._trusted_exclusions(tracker), {})
        gate = constants.profile_for("Tuinstra-DEV/gate")
        self.assertGreater(len(evidence._trusted_exclusions(gate)), 0)

    def test_outer_evidence_reports_selected_tracker_profile(self):
        profile = constants.profile_for_binding("Tuinstra-DEV/tracker", TRACKER_BINDING)
        report = json.loads((FIXTURES / "pair-pass.json").read_bytes())
        report["bundle_digest"] = profile["bundle_digest"]
        report["dataset_manifest_digest"] = profile["dataset_digest"]
        report["policy_digest"] = profile["policy_digest"]
        for row in report["coverage"]:
            row["rules_digest"] = profile["bundle_digest"]
        raw = json.dumps(report).encode()
        context = {
            "GATE_WORKFLOW_REF": evidence.APPROVED_WORKFLOW_PATH + "@" + "f" * 40,
            "GATE_WORKFLOW_SHA": "f" * 40,
        }
        payload = json.loads(evidence._outer_evidence(state("Tuinstra-DEV/tracker"), raw, report, context, []))
        self.assertEqual(payload["scanner_image"], profile["image"])
        self.assertEqual(payload["policy_digest"], profile["policy_digest"])
        self.assertEqual(payload["dataset_manifest_digest"], profile["dataset_digest"])


if __name__ == "__main__":
    unittest.main()
