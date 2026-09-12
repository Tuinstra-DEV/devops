from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import os
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader(
    "production_profile_executor",
    str(ROOT / "scripts" / "production-profile-executor"),
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
EXECUTOR = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(EXECUTOR)


def request(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "contract_version": 1,
        "host_slug": "tuinstra-prod-01",
        "operation": "check",
        "job_id": "job_01993cec-aaaa-bbbb-cccc-123456789012",
        "plan_hash": "a" * 64,
        "profile_version": "2026.09.12.3",
        "profile_content_hash": "b" * 64,
        "source_sha": "c" * 40,
        "procedure": "baseline_umami_restore",
    }
    value.update(changes)
    return value


def command(payload: dict[str, object]) -> bytes:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return f"tuinstra-profile-v1 {encoded}".encode()


class ProductionProfileExecutorTest(unittest.TestCase):
    def decode(self, payload: dict[str, object]) -> dict[str, object]:
        fake_stdin = mock.Mock()
        fake_stdin.buffer = io.BytesIO(command(payload))
        with mock.patch.object(EXECUTOR.sys, "stdin", fake_stdin):
            return EXECUTOR.decode_request()

    def test_accepts_only_the_fixed_check_contract(self) -> None:
        self.assertEqual(request(), self.decode(request()))

    def test_rejects_command_path_and_unknown_fields(self) -> None:
        for field in ("command", "path", "inventory", "shell", "role"):
            with self.subTest(field=field), self.assertRaises(EXECUTOR.Rejected):
                self.decode(request(**{field: "forbidden"}))

    def test_apply_requires_the_approved_check_hash(self) -> None:
        with self.assertRaises(EXECUTOR.Rejected):
            self.decode(request(operation="apply"))
        apply = request(operation="apply", approved_check_result_hash="d" * 64)
        self.assertEqual(apply, self.decode(apply))

    def test_normalized_evidence_hash_ignores_raw_output(self) -> None:
        payload = request()
        first = EXECUTOR.evidence_hash(payload, 2, 0, 0, ["A", "B"])
        second = EXECUTOR.evidence_hash(payload, 2, 0, 0, ["A", "B"])
        changed = EXECUTOR.evidence_hash(payload, 3, 0, 0, ["A", "B"])
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_real_ansible_recap_records_changed_task_names(self) -> None:
        output = """TASK [production_host_baseline : Configure Docker] ****************************
changed: [prod01]
TASK [production_host_baseline : Keep Caddy running] **************************
ok: [prod01]
PLAY RECAP *********************************************************************
prod01 : ok=47 changed=1 unreachable=0 failed=0 skipped=0 rescued=0 ignored=0
"""

        changed, failed, unreachable, tasks = EXECUTOR.parse_recap(output, "prod01")

        self.assertEqual((1, 0, 0), (changed, failed, unreachable))
        self.assertEqual(["production_host_baseline : Configure Docker"], tasks)

    def test_umami_procedure_aggregates_baseline_then_restore_scaffolding(self) -> None:
        results = [
            {"successful": True, "exit_code": 0, "changed_count": 2, "failed_count": 0, "unreachable_count": 0, "changed_tasks": ["baseline"], "stdout_hash": "a" * 64, "stderr_hash": "b" * 64, "output_truncated": False},
            {"successful": True, "exit_code": 0, "changed_count": 1, "failed_count": 0, "unreachable_count": 0, "changed_tasks": ["umami"], "stdout_hash": "c" * 64, "stderr_hash": "d" * 64, "output_truncated": False},
        ]
        with mock.patch.object(EXECUTOR, "run_command", side_effect=results) as runner:
            result = EXECUTOR.run_phase(request(), {"limit": "prod01"}, (pathlib.Path("wrapper"), pathlib.Path("inventory"), pathlib.Path("vars")), "check")

        self.assertEqual(["configure", "configure-umami-restore"], [call.args[4] for call in runner.call_args_list])
        self.assertTrue(all(call.args[5] for call in runner.call_args_list))
        self.assertEqual(3, result["changed_count"])
        self.assertEqual(["baseline", "umami"], result["changed_tasks"])
        self.assertEqual(2, result["steps_completed"])

    def test_baseline_procedure_never_selects_umami_role(self) -> None:
        result = {"successful": True, "exit_code": 0, "changed_count": 0, "failed_count": 0, "unreachable_count": 0, "changed_tasks": [], "stdout_hash": "a" * 64, "stderr_hash": "b" * 64, "output_truncated": False}
        with mock.patch.object(EXECUTOR, "run_command", return_value=result) as runner:
            EXECUTOR.run_phase(request(procedure="baseline"), {"limit": "prod02"}, (pathlib.Path("wrapper"), pathlib.Path("inventory"), pathlib.Path("vars")), "verify")

        self.assertEqual("verify", runner.call_args.args[4])
        self.assertFalse(runner.call_args.args[5])

    def test_terminal_journal_replays_without_running_or_locking_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "journal").mkdir(mode=0o700)
            payload = request()
            request_hash = EXECUTOR.hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            terminal = {"contract_version": 1, "status": "succeeded", "summary": "done", "retry_safe": False}
            journal = root / "journal" / f"{payload['job_id']}-{payload['plan_hash']}.json"
            journal.write_text(json.dumps({"request_hash": request_hash, "response": terminal}), encoding="utf-8")
            journal.chmod(0o600)
            with mock.patch.object(EXECUTOR, "ROOT", root), mock.patch.object(EXECUTOR, "safe_directory"), mock.patch.object(EXECUTOR, "safe_file"), mock.patch.object(EXECUTOR, "acquire_host_lock") as locker, mock.patch.object(EXECUTOR, "run_phase") as runner:
                replayed = EXECUTOR.execute(payload, {}, (pathlib.Path(), pathlib.Path(), pathlib.Path()))

            self.assertEqual(terminal, replayed)
            locker.assert_not_called()
            runner.assert_not_called()

    def test_apply_stops_before_configure_when_fresh_check_differs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "journal").mkdir(mode=0o700)
            lock_path = root / "lock"
            lock_path.touch(mode=0o600)
            payload = request(operation="apply", approved_check_result_hash="d" * 64)
            preflight = {"successful": True, "evidence_hash": "e" * 64, "phase": "preflight"}

            def acquire(_: str) -> int:
                return os.open(lock_path, os.O_RDWR)

            with mock.patch.object(EXECUTOR, "ROOT", root), mock.patch.object(EXECUTOR, "safe_directory"), mock.patch.object(EXECUTOR, "acquire_host_lock", side_effect=acquire), mock.patch.object(EXECUTOR, "run_phase", return_value=preflight) as runner:
                result = EXECUTOR.execute(payload, {}, (pathlib.Path(), pathlib.Path(), pathlib.Path()))

            self.assertEqual("failed", result["status"])
            self.assertTrue(result["retry_safe"])
            runner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
