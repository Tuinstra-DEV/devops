from datetime import date
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / ".github/actions/classify-ci-changes/classify_ci_changes.py"
SPEC = importlib.util.spec_from_file_location("classify_ci_changes", SCRIPT)
classifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(classifier)


def policy():
    return {
        "schemaVersion": classifier.SCHEMA,
        "repository": "Tuinstra-DEV/example",
        "owner": "platform@example.invalid",
        "reviewedAt": "2026-08-12",
        "expiresAt": "2026-11-10",
        "classes": [
            {"name": "documentation", "patterns": ["docs/**", "*.md"]},
            {"name": "frontend", "patterns": ["frontend/**"]},
            {"name": "backend", "patterns": ["backend/**"]},
            {"name": "runtime", "patterns": ["runtime/**"]},
            {"name": "container", "patterns": ["containers/**"]},
            {"name": "contract", "patterns": ["contracts/**"]},
            {"name": "security", "patterns": ["secure/**"]},
            {"name": "workflow", "patterns": ["automation/**"]},
        ],
        "routes": {
            "documentation": {},
            "frontend": {"frontend-static": True, "frontend-browser": True},
            "backend": {"backend": True, "browser-integration": True},
            "runtime": {}, "container": {}, "contract": {}, "security": {}, "workflow": {},
        },
    }


def records(*paths):
    return [{"status": "M", "paths": [path]} for path in paths]


class ClassifierTests(unittest.TestCase):
    def classify(self, *paths, mode="selective", force=False):
        payload = policy()
        return classifier.classify(
            payload, records(*paths), mode=mode, force_full=force,
            policy_digest=classifier.canonical_digest(payload),
        )

    def test_documentation_frontend_backend_minimal_routes(self):
        docs = self.classify("docs/guide.md")
        self.assertEqual(docs["decision"], "selective")
        self.assertFalse(any(docs["routes"].values()))
        frontend = self.classify("frontend/app.vue")
        self.assertTrue(frontend["routes"]["frontend-static"])
        self.assertTrue(frontend["routes"]["frontend-browser"])
        self.assertFalse(frontend["routes"]["backend"])
        backend = self.classify("backend/service.php")
        self.assertTrue(backend["routes"]["backend"])
        self.assertTrue(backend["routes"]["browser-integration"])

    def test_all_eight_classes_are_recorded(self):
        result = self.classify(
            "docs/a.md", "frontend/a", "backend/a", "runtime/a", "containers/a",
            "contracts/a", "secure/a", "automation/a",
        )
        self.assertEqual(len(result["classes"]), 8)
        self.assertEqual(result["decision"], "full-safe")

    def test_mixed_frontend_backend_unions_routes(self):
        result = self.classify("frontend/a", "backend/a")
        self.assertEqual(result["decision"], "selective")
        self.assertTrue(result["routes"]["frontend-static"])
        self.assertTrue(result["routes"]["backend"])

    def test_full_classes_and_non_overridable_paths_force_full(self):
        for path in (
            "runtime/config", "containers/base", "contracts/openapi.json", "secure/auth.py",
            "automation/task", ".github/workflows/ci.yml", "package-lock.json",
            "Dockerfile", "deploy/release.sh",
        ):
            with self.subTest(path=path):
                result = self.classify(path)
                self.assertEqual(result["decision"], "full-safe")
                self.assertTrue(all(result["routes"].values()))

    def test_root_control_plane_files_are_recognized_not_merely_unknown(self):
        for path in ("package-lock.json", "Dockerfile", ".env.production"):
            with self.subTest(path=path):
                result = self.classify(path)
                self.assertEqual(result["reason"], "non-overridable-full-safe-path")
                self.assertEqual(result["controlPlanePaths"], [path])
                self.assertEqual(result["unknownPaths"], [])

    def test_unknown_path_fails_closed(self):
        result = self.classify("new-area/file.txt")
        self.assertEqual(result["reason"], "unknown-path")
        self.assertTrue(all(result["routes"].values()))

    def test_mode_and_force_full_are_central_rollback(self):
        for mode, force in (("full", False), ("garbage", False), ("selective", True)):
            result = self.classify("docs/a.md", mode=mode, force=force)
            self.assertEqual(result["decision"], "full-safe")

    def test_nul_diff_supports_renames_and_deletes(self):
        parsed = classifier.parse_name_status_z(
            b"R100\0frontend/old.vue\0frontend/new.vue\0D\0backend/old.php\0"
        )
        self.assertEqual(parsed[0]["paths"], ["frontend/old.vue", "frontend/new.vue"])
        self.assertEqual(parsed[1], {"status": "D", "paths": ["backend/old.php"]})

    def test_malformed_missing_and_unsafe_diff_fail(self):
        for raw in (b"", b"M\0../secret\0", b"M\0bad", b"Q\0docs/a\0"):
            with self.subTest(raw=raw), self.assertRaises(classifier.ClassifierError):
                classifier.parse_name_status_z(raw)

    def test_policy_lifetime_repository_and_routes_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy()), encoding="utf-8")
            loaded = classifier.load_policy(
                path, "Tuinstra-DEV/example", date(2026, 8, 13),
            )
            self.assertEqual(loaded["schemaVersion"], classifier.SCHEMA)
            with self.assertRaisesRegex(classifier.ClassifierError, "stale-policy"):
                classifier.load_policy(path, today=date(2026, 11, 11))
            with self.assertRaisesRegex(classifier.ClassifierError, "mismatch"):
                classifier.load_policy(path, "Tuinstra-DEV/other", date(2026, 8, 13))
            incomplete = policy()
            incomplete["classes"] = incomplete["classes"][:-1]
            path.write_text(json.dumps(incomplete), encoding="utf-8")
            with self.assertRaisesRegex(classifier.ClassifierError, "incomplete"):
                classifier.load_policy(path, today=date(2026, 8, 13))

    def test_evidence_and_policy_digest_are_deterministic(self):
        first = self.classify("frontend/z", "backend/a")
        second = self.classify("backend/a", "frontend/z")
        self.assertEqual(first["policyDigest"], second["policyDigest"])
        self.assertEqual(first["evidenceDigest"], second["evidenceDigest"])

    def test_main_invalid_sha_and_policy_fail_closed_with_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            output = root / "evidence.json"
            github_output = root / "github-output"
            code = classifier.main([
                "--policy", str(policy_path), "--base-sha", "bad", "--head-sha", "also-bad",
                "--mode", "selective", "--output", str(output),
                "--github-output", str(github_output), "--today", "2026-08-13",
            ])
            self.assertEqual(code, 0)
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(evidence["decision"], "full-safe")
            self.assertEqual(evidence["reason"], "invalid-sha")
            self.assertIn("run-backend=true", github_output.read_text(encoding="utf-8"))

    def test_main_forced_backstop_does_not_require_a_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            output = root / "evidence.json"
            code = classifier.main([
                "--policy", str(policy_path), "--base-sha", "", "--head-sha", "",
                "--mode", "selective", "--force-full", "true",
                "--output", str(output), "--today", "2026-08-13",
            ])
            self.assertEqual(code, 0)
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(evidence["reason"], "caller-forced-full")
            self.assertEqual(evidence["changedFiles"], [])
            self.assertTrue(all(evidence["routes"].values()))

    def test_composite_action_uses_environment_not_shell_interpolation(self):
        action = (ROOT / ".github/actions/classify-ci-changes/action.yml").read_text()
        self.assertIn('python3 "$GITHUB_ACTION_PATH/classify_ci_changes.py"', action)
        self.assertIn('ROUTING_REPOSITORY: ${{ github.repository }}', action)
        self.assertIn('--repository "$ROUTING_REPOSITORY"', action)
        self.assertNotIn("${{ inputs.base-sha }} ", action)


if __name__ == "__main__":
    unittest.main()
