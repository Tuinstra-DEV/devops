"""Source/runner contract tests; Git repositories below are synthetic object fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / ".github/actions/gate-pr-security/source.py"
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location("gate_pr_security_source", MODULE)
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
A, B, T = "a" * 40, "b" * 40, "c" * 40


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / "work"

    def tearDown(self):
        for directory, _, _ in os.walk(self.root, followlinks=False):
            os.chmod(directory, 0o700)
        self.temp.cleanup()

    def state(self):
        return {"repository": "Tuinstra-DEV/gate", "repository_id": "42", "owner_id": "12",
                "pull_request": 7, "run_id": "900", "run_attempt": 2,
                "base_sha": A, "head_sha": B, "base_tree": T, "head_tree": T}

    def context(self):
        return {"GATE_REPOSITORY": "Tuinstra-DEV/gate", "GATE_REPOSITORY_ID": "42",
                "GATE_OWNER_ID": "12", "GATE_PR_NUMBER": "7", "GATE_RUN_ID": "900",
                "GATE_RUN_ATTEMPT": "2", "GITHUB_TOKEN": "secret-test-token",
                "GATE_EVENT_NAME": "pull_request_target"}

    def provider(self, *, head=B, attempt=2, merged=False):
        repo = {"id": 42, "owner": {"id": 12}, "full_name": "Tuinstra-DEV/gate"}
        pr = {"number": 7, "state": "open", "merged": merged,
              "base": {"sha": A, "repo": repo}, "head": {"sha": B, "repo": {"id": 99}}}
        run = {"id": 900, "run_attempt": attempt, "event": "pull_request_target", "head_sha": head,
               "repository": repo, "head_repository": {"id": 99}, "pull_requests": []}
        return [repo, pr, run, run.copy()]

    def marker(self):
        self.work.mkdir()
        marker = {"version": 1, "id": "d" * 32, "path": str(self.work), "uid": os.getuid()}
        (self.work / ".gate-work.json").write_text(json.dumps(marker))
        return marker

    def scan_fixture(self, outcome="pass"):
        marker = self.marker()
        for name in ("objects.git", "base", "head", "output"):
            (self.work / name).mkdir()
        source.save_state(self.work, self.state())
        report = {"schema_version": "1.0", "document_type": "ci-pair-result", "base_sha": A,
                  "head_sha": B, "outcome": outcome, "coverage": [], "findings": [],
                  "inputs": [], "reasons": [], "uncompared_findings": []}
        return marker, report

    def docker(self, marker, report, exit_code=0, fail_start=False):
        calls = []
        def invoke(command, **kwargs):
            calls.append((command, kwargs))
            if command[1] == "create":
                return 0, b"f" * 64 + b"\n"
            if command[1] == "start":
                if fail_start:
                    raise source.Failure("process_timeout")
                pair = self.work / "output/pair"
                pair.mkdir()
                if report is not None:
                    (pair / "result.json").write_text(json.dumps(report))
                return exit_code, b"raw scanner secret must never be printed"
            if command[1:3] == ["container", "ls"]:
                return 0, ("gate-ci-" + marker["id"] + "|" + marker["id"]).encode()
            if command[1] == "rm":
                return 0, b"removed"
            raise AssertionError(command)
        return invoke, calls

    def synthetic_store(self, filename="app.php", content=b"<?php echo 'fixture';", mode="100644"):
        store = self.root / "objects.git"
        self.assertEqual(0, source.run([source.GIT, "init", "--bare", "--template=", str(store)])[0])
        blob = source.git(store, ["hash-object", "-w", "--stdin"], data=content).strip()
        tree = source.git(store, ["mktree", "-z"], data=mode.encode() + b" blob " + blob + b"\t" + filename.encode() + b"\0").strip()
        env = source.clean_env() | {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                                    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        commit = source.git(store, ["commit-tree", tree.decode()], env=env, data=b"synthetic fixture\n").strip().decode()
        return store, commit, tree.decode()

    def test_provider_accepts_fork_target_with_independent_source_binding(self):
        with patch.object(source, "api", side_effect=self.provider()) as api:
            state, token = source.provider_state(self.context())
        self.assertEqual((A, B, "42", 2), (state["base_sha"], state["head_sha"], state["repository_id"], state["run_attempt"]))
        self.assertEqual("secret-test-token", token)
        self.assertTrue(api.call_args_list[-1].args[0].endswith("/attempts/2"))

    def test_only_registered_target_event_is_accepted(self):
        for event in (None, "pull_request", "workflow_dispatch"):
            with self.subTest(event=event), patch.object(source, "api") as api:
                with self.assertRaisesRegex(source.Failure, "unsupported_event"):
                    source.provider_state(self.context() | {"GATE_EVENT_NAME": event})
                api.assert_not_called()
        payloads = self.provider()
        payloads[2]["event"] = "pull_request"
        with patch.object(source, "api", side_effect=payloads), self.assertRaisesRegex(source.Failure, "run_mismatch"):
            source.provider_state(self.context())

    def test_provider_rejects_stale_attempt_wrong_source_or_closed_pr(self):
        for kwargs in ({"head": A}, {"attempt": 1}, {"merged": True}):
            with self.subTest(kwargs=kwargs), patch.object(source, "api", side_effect=self.provider(**kwargs)):
                with self.assertRaises(source.Failure):
                    source.provider_state(self.context())

    def test_provider_rejects_repository_swap_and_conflicting_pr_association(self):
        payloads = self.provider()
        payloads[0]["owner"]["id"] = 999
        with patch.object(source, "api", side_effect=payloads), self.assertRaises(source.Failure):
            source.provider_state(self.context())
        payloads = self.provider()
        payloads[2]["pull_requests"] = [{"number": 8}]
        with patch.object(source, "api", side_effect=payloads), self.assertRaisesRegex(source.Failure, "run_pr_mismatch"):
            source.provider_state(self.context())

    def test_exact_blob_materialization_never_executes_source(self):
        store, commit, tree = self.synthetic_store("payload.sh", b"#!/bin/sh\ntouch NEVER_EXECUTE\n", "100755")
        destination = self.root / "snapshot"
        self.assertEqual(tree, source.materialize(store, commit, destination))
        self.assertEqual(b"#!/bin/sh\ntouch NEVER_EXECUTE\n", (destination / "payload.sh").read_bytes())
        self.assertEqual(0o444, (destination / "payload.sh").stat().st_mode & 0o777)
        self.assertFalse((self.root / "NEVER_EXECUTE").exists())

    def test_symlink_and_lfs_are_rejected(self):
        for mode, content in (("120000", b"/etc/passwd"), ("100644", b"version https://git-lfs.github.com/spec/v1\n")):
            with self.subTest(mode=mode):
                store, commit, _ = self.synthetic_store(content=content, mode=mode)
                destination = self.root / ("snapshot" + mode)
                with self.assertRaisesRegex(source.Failure, "unsupported_source_entry|unsupported_lfs_pointer"):
                    source.materialize(store, commit, destination)
                shutil.rmtree(store)

    def test_unsafe_paths_and_size_limits(self):
        for path in (b"../outside", b"/absolute", b".git/config", b"space name.php", b"a\\b", b"a:b", b"a//b", b"bad\xff"):
            with self.subTest(path=path), self.assertRaises(source.Failure):
                source.safe_path(path)
        store, commit, _ = self.synthetic_store(content=b"12345")
        with patch.object(source, "MAX_FILE", 4), self.assertRaisesRegex(source.Failure, "source_limit"):
            source.materialize(store, commit, self.root / "oversize")

    def test_prepare_uses_auth_only_in_fetch_environment_and_records_bound_state(self):
        def init(command, **kwargs):
            (self.work / "objects.git").mkdir()
            return 0, b""
        def git_call(store, arguments, **kwargs):
            if arguments[0] == "fetch":
                self.assertNotIn("secret-test-token", " ".join(arguments))
                self.assertIn("GIT_CONFIG_VALUE_0", kwargs["env"])
                return b""
            return (A if arguments[-1].endswith("base") else B).encode()
        def snapshot(store, commit, destination):
            destination.mkdir()
            return T
        with patch.object(source, "provider_state", return_value=(self.state(), "secret-test-token")), \
             patch.object(source, "run", side_effect=init), patch.object(source, "git", side_effect=git_call), \
             patch.object(source, "materialize", side_effect=snapshot):
            source.prepare(self.work, self.context())
        state_text = (self.work / "state.json").read_text()
        self.assertNotIn("secret-test-token", state_text)
        self.assertIsInstance(json.loads(state_text)["prepare_duration_ms"], int)
        self.assertEqual(T, json.loads(state_text)["head_tree"])

    def test_prepare_rejects_preexisting_work_and_changed_fetched_head(self):
        self.work.mkdir()
        (self.work / "existing").write_text("preserve")
        with self.assertRaisesRegex(source.Failure, "work_directory_not_empty"):
            source.prepare(self.work, self.context())
        self.assertEqual("preserve", (self.work / "existing").read_text())
        shutil.rmtree(self.work)
        def init(command, **kwargs):
            (self.work / "objects.git").mkdir()
            return 0, b""
        with patch.object(source, "provider_state", return_value=(self.state(), "secret-test-token")), \
             patch.object(source, "run", side_effect=init), \
             patch.object(source, "git", side_effect=[b"", b"e" * 40]), \
             self.assertRaisesRegex(source.Failure, "fetched_sha_mismatch"):
            source.prepare(self.work, self.context())

    def test_all_semantic_exits_preserve_report_and_container_is_isolated(self):
        for code, outcome in ((0, "pass"), (1, "blocked"), (2, "incomplete")):
            with self.subTest(code=code):
                marker, report = self.scan_fixture(outcome)
                invoke, calls = self.docker(marker, report, code)
                with patch.object(source, "run", side_effect=invoke):
                    source.scan(self.work)
                state = json.loads((self.work / "state.json").read_text())
                self.assertEqual(code, state["scanner_exit"])
                self.assertIsInstance(state["scan_duration_ms"], int)
                create = calls[0][0]
                for fragment in ("--read-only", "--cap-drop", "no-new-privileges", source.IMAGE, "--user"):
                    self.assertIn(fragment, create)
                self.assertEqual("none", create[create.index("--network") + 1])
                self.assertEqual("never", create[create.index("--pull") + 1])
                self.assertEqual(4, create.count("--mount"))
                self.assertEqual(self.work / "output", calls[1][1]["storage_root"])
                self.assertNotIn("--env", create)
                self.assertNotIn("--env-file", create)
                self.assertEqual(3, sum(value.endswith(",readonly") for value in create))
                self.assertEqual("rm", calls[-1][0][1])
                shutil.rmtree(self.work)

    def test_missing_report_or_semantically_mismatched_report_fails(self):
        for problem in ("missing", "sha", "outcome"):
            with self.subTest(problem=problem):
                marker, report = self.scan_fixture()
                if problem == "missing":
                    report = None
                elif problem == "sha":
                    report["head_sha"] = A
                else:
                    report["outcome"] = "blocked"
                invoke, _ = self.docker(marker, report)
                with patch.object(source, "run", side_effect=invoke), self.assertRaises(source.Failure):
                    source.scan(self.work)
                self.assertNotIn("scanner_exit", json.loads((self.work / "state.json").read_text()))
                shutil.rmtree(self.work)

    def test_timeout_still_removes_only_our_container(self):
        marker, report = self.scan_fixture()
        invoke, calls = self.docker(marker, report, fail_start=True)
        with patch.object(source, "run", side_effect=invoke), self.assertRaisesRegex(source.Failure, "process_timeout"):
            source.scan(self.work)
        self.assertEqual([source.DOCKER, "rm", "--force", "gate-ci-" + marker["id"]], calls[-1][0])

    def test_cleanup_refuses_unmarked_work_and_never_follows_source_symlink(self):
        self.work.mkdir()
        with self.assertRaisesRegex(source.Failure, "unmarked_work_directory"):
            source.cleanup(self.work)
        self.work.rmdir()
        self.marker()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "preserve").write_text("keep")
        (self.work / "link").symlink_to(outside, target_is_directory=True)
        with patch.object(source, "run", return_value=(0, b"")):
            source.cleanup(self.work)
        self.assertFalse(self.work.exists())
        self.assertEqual("keep", (outside / "preserve").read_text())

    def test_cleanup_rejects_container_label_mismatch(self):
        self.marker()
        with patch.object(source, "run", return_value=(0, b"different|owner")), \
             self.assertRaisesRegex(source.Failure, "container_cleanup_identity_mismatch"):
            source.cleanup(self.work)
        self.assertTrue(self.work.exists())

    def test_work_path_is_scoped_to_runner_temp(self):
        with patch.dict(os.environ, {"RUNNER_TEMP": str(self.root)}):
            self.assertEqual(self.work, source.work_path(str(self.work)))
            for path in (str(self.root), "/", str(self.root / "comma,path")):
                with self.subTest(path=path), self.assertRaises(source.Failure):
                    source.work_path(path)

    def test_process_output_limits_timeout_and_clean_environment(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "must-not-inherit"}):
            code, output = source.run([sys.executable, "-c", "import os; print(os.getenv('GITHUB_TOKEN', 'absent'))"])
        self.assertEqual((0, b"absent\n"), (code, output))
        with self.assertRaisesRegex(source.Failure, "process_output_limit"):
            source.run([sys.executable, "-c", "print('x' * 100000)"], max_bytes=1024)
        with self.assertRaisesRegex(source.Failure, "process_timeout"):
            source.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)

    def test_git_download_storage_limit_is_enforced(self):
        with patch.object(source, "MAX_OBJECT_STORE", 4), self.assertRaisesRegex(source.Failure, "object_store_limit"):
            source.run([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'12345')",
                        str(self.root / "pack")], storage_root=self.root)

    def test_duplicate_json_and_nonfinite_values_are_rejected(self):
        for value in (b'{"outcome":"pass","outcome":"blocked"}', b'{"bad": NaN}'):
            with self.assertRaises(source.Failure):
                source.decode_json(value)


if __name__ == "__main__":
    unittest.main()
