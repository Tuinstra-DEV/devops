from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION_DIR = ROOT / ".github/actions/tessera-selective-ci"
sys.path.insert(0, str(ACTION_DIR))
import tessera_scope


class TesseraScopeActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "ci@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "CI Fixture"], check=True)
        self._write("baseline.txt", "baseline")
        self._commit("base")
        self.base_sha = self._sha()
        self._write("app/Gemfile", "ruby")
        self._commit("head")
        self.head_sha = self._sha()
        self.source_sha = subprocess.run(
            ["git", "-C", str(self.repo), "commit-tree", f"{self.head_sha}^{{tree}}",
             "-p", self.base_sha, "-p", self.head_sha, "-m", "merge"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.base = {
            "sha": self.base_sha,
            "ref": "develop",
            "repo": {"full_name": "Tuinstra-DEV/sudoku-tessera"},
        }
        self.head = {
            "sha": self.head_sha,
            "ref": "chore/test",
            "repo": {"full_name": "Tuinstra-DEV/sudoku-tessera"},
        }
        self.event = {
            "repository": {"full_name": "Tuinstra-DEV/sudoku-tessera", "private": True},
            "pull_request": {"number": 181, "head": self.head, "base": self.base},
        }
        self.env = {
            "GITHUB_REPOSITORY": "Tuinstra-DEV/sudoku-tessera",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": "refs/pull/181/merge",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": self.source_sha,
        }
        self.run = {
            "id": 12345,
            "run_attempt": 1,
            "event": "pull_request",
            "head_sha": self.head_sha,
            "head_branch": "chore/test",
            "repository": {"full_name": "Tuinstra-DEV/sudoku-tessera"},
        }
        self.pr = {
            "number": 181,
            "state": "open",
            "merged": False,
            "head": self.head,
            "base": self.base,
        }
        self.merge = {"sha": self.source_sha, "parents": [{"sha": self.base_sha}, {"sha": self.head_sha}]}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, path: str, value: str) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value + "\n", encoding="utf-8")

    def _commit(self, message: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), "add", "--all"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", message], check=True)

    def _sha(self) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()

    def _api(self, path: str) -> dict:
        return {
            "/repos/Tuinstra-DEV/sudoku-tessera/actions/runs/12345": self.run,
            "/repos/Tuinstra-DEV/sudoku-tessera/pulls/181": self.pr,
            f"/repos/Tuinstra-DEV/sudoku-tessera/git/commits/{self.source_sha}": self.merge,
        }[path]

    def test_only_exact_gemfile_diff_on_develop_selects_light_route(self) -> None:
        base_sha, head_sha, source_sha, base_ref = tessera_scope.validate_pr(self.event, self.env, self._api)
        paths = tessera_scope.verified_paths(self.repo, base_sha, head_sha, source_sha)
        self.assertEqual(paths, {"app/Gemfile"})
        self.assertEqual(tessera_scope.classify(paths, base_ref), "ruby-lite")

    def test_fastfile_can_use_the_existing_fixture_but_fixture_changes_go_full(self) -> None:
        self.assertEqual(tessera_scope.classify({"app/fastlane/Fastfile"}, "develop"), "ruby-lite")
        self.assertEqual(tessera_scope.classify({"app/fastlane/test_fastfile_ipa_path.rb"}, "develop"), "full")

    def test_valid_release_pull_request_to_main_is_accepted_but_never_lite(self) -> None:
        self.base = {**self.base, "ref": "main"}
        self.pr = {**self.pr, "base": self.base}
        self.event = {**self.event, "pull_request": {**self.event["pull_request"], "base": self.base}}
        base_sha, head_sha, source_sha, base_ref = tessera_scope.validate_pr(self.event, self.env, self._api)
        paths = tessera_scope.verified_paths(self.repo, base_sha, head_sha, source_sha)
        self.assertEqual(tessera_scope.classify(paths, base_ref), "full")

    def test_release_main_and_mixed_unknown_or_empty_diffs_select_full_route(self) -> None:
        for paths, base_ref in (
            ({"app/Gemfile"}, "main"),
            ({"app/Gemfile", "app/lib/main.dart"}, "develop"),
            ({".github/workflows/ci.yml"}, "develop"),
            ({"app/fastlane/test_fastfile_ipa_path.rb"}, "develop"),
            ({"app/fastlane/Appfile"}, "develop"),
            (set(), "develop"),
        ):
            with self.subTest(paths=paths, base_ref=base_ref):
                self.assertEqual(tessera_scope.classify(paths, base_ref), "full")

    def test_merge_parent_or_stale_pull_request_mismatch_fails_closed(self) -> None:
        self.merge = {"sha": self.source_sha, "parents": [{"sha": self.head_sha}, {"sha": self.head_sha}]}
        with self.assertRaisesRegex(tessera_scope.ScopeError, "merge parent API proof"):
            tessera_scope.validate_pr(self.event, self.env, self._api)

        self.merge = {"sha": self.source_sha, "parents": [{"sha": self.base_sha}, {"sha": self.head_sha}]}
        self.pr = {**self.pr, "state": "closed"}
        with self.assertRaisesRegex(tessera_scope.ScopeError, "current open PR"):
            tessera_scope.validate_pr(self.event, self.env, self._api)

    def test_wrong_repository_event_or_run_identity_fails_closed(self) -> None:
        with self.assertRaisesRegex(tessera_scope.ScopeError, "repository or event"):
            tessera_scope.validate_pr(self.event, {**self.env, "GITHUB_REPOSITORY": "other/repo"}, self._api)
        with self.assertRaisesRegex(tessera_scope.ScopeError, "Actions run record"):
            tessera_scope.validate_pr(self.event, {**self.env, "GITHUB_RUN_ATTEMPT": "2"}, self._api)

    def test_composite_uses_its_immutable_action_path_and_proves_only_after_fixture(self) -> None:
        action = (ACTION_DIR / "action.yml").read_text(encoding="utf-8")
        self.assertIn("SCOPE_SCRIPT: ${{ github.action_path }}/tessera_scope.py", action)
        self.assertNotIn("checkout@", action)
        fixture = "run: ruby app/fastlane/test_fastfile_ipa_path.rb"
        proof = "echo 'hosted_proof=true' >> \"$GITHUB_OUTPUT\""
        self.assertIn(fixture, action)
        self.assertLess(action.index(fixture), action.index(proof))
        self.assertIn("preflight_trusted=false", action)
        self.assertIn("trusted_proof=", action)


if __name__ == "__main__":
    unittest.main()

