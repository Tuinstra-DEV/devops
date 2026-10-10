from __future__ import annotations

import base64
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import os
import signal
import tempfile
import subprocess
import sys
import time
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

    def test_status_contract_binds_the_original_execution_operation(self) -> None:
        payload = request(operation="status", execution_operation="apply", approved_check_result_hash="d" * 64)
        self.assertEqual(payload, self.decode(payload))
        for invalid in [request(operation="status"), request(operation="status", execution_operation="shell"), request(operation="status", execution_operation="apply")]:
            with self.subTest(invalid=invalid), self.assertRaises(EXECUTOR.Rejected):
                self.decode(invalid)

    def test_status_without_journal_never_executes_or_creates_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "journal").mkdir(mode=0o700)
            payload = request(operation="status", execution_operation="apply", approved_check_result_hash="d" * 64)
            with mock.patch.object(EXECUTOR, "ROOT", root), mock.patch.object(EXECUTOR, "safe_directory"), mock.patch.object(EXECUTOR, "run_phase") as runner, mock.patch.object(EXECUTOR, "acquire_host_lock") as locker:
                result = EXECUTOR.read_status(payload)
            self.assertEqual("uncertain", result["status"])
            self.assertFalse(result["journal_present"])
            self.assertFalse(result["journal_terminal"])
            self.assertEqual([], list((root / "journal").iterdir()))
            runner.assert_not_called()
            locker.assert_not_called()

    def test_status_requires_exact_original_request_and_preserves_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "journal").mkdir(mode=0o700)
            original = request(operation="apply", approved_check_result_hash="d" * 64)
            digest = EXECUTOR.hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            journal = root / "journal" / f"{original['job_id']}-{original['plan_hash']}.json"
            for response in [None, {"status": "succeeded", "summary": "done", "retry_safe": False}]:
                journal.write_text(json.dumps({"request_hash": digest, "response": response}))
                journal.chmod(0o600)
                before = journal.read_bytes()
                query = {**original, "operation": "status", "execution_operation": "apply"}
                with mock.patch.object(EXECUTOR, "ROOT", root), mock.patch.object(EXECUTOR, "safe_directory"), mock.patch.object(EXECUTOR, "safe_file"), mock.patch.object(EXECUTOR, "run_phase") as runner:
                    result = EXECUTOR.read_status(query)
                    self.assertTrue(result["journal_present"])
                    self.assertEqual(response is not None, result["journal_terminal"])
                    with self.assertRaises(EXECUTOR.Rejected):
                        EXECUTOR.read_status({**query, "approved_check_result_hash": "e" * 64})
                self.assertEqual(before, journal.read_bytes())
                runner.assert_not_called()

    def test_status_survives_policy_rotation_and_unavailable_old_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root/'journal').mkdir(mode=0o700)
            config = root/'config'
            config.mkdir(mode=0o700)
            (config/'host-identity').write_text('tuinstra-prod-01\n')
            (config/'host-identity').chmod(0o644)
            original = request(operation='apply', approved_check_result_hash='d'*64)
            digest = EXECUTOR.hashlib.sha256(json.dumps(original, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            journal = root/'journal'/f"{original['job_id']}-{original['plan_hash']}.json"
            journal.write_text(json.dumps({'request_hash':digest,'response':{'contract_version':1,'status':'succeeded'}}))
            journal.chmod(0o600)
            query = {**original, 'operation':'status', 'execution_operation':'apply'}
            fake_input = mock.Mock();fake_input.buffer=io.BytesIO(command(query))
            output = io.StringIO()
            with mock.patch.object(EXECUTOR, 'ROOT', root), mock.patch.object(EXECUTOR, 'CONFIG_ROOT', config), mock.patch.object(EXECUTOR, 'safe_file'), mock.patch.object(EXECUTOR, 'safe_directory'), mock.patch.object(EXECUTOR.os,'geteuid',return_value=0), mock.patch.object(EXECUTOR.sys,'argv',['executor']), mock.patch.dict(EXECUTOR.os.environ,{'SUDO_USER':'console-profile-executor'}), mock.patch.object(EXECUTOR.sys,'stdin',fake_input), mock.patch.object(EXECUTOR.sys,'stdout',output), mock.patch.object(EXECUTOR,'load_policy',side_effect=EXECUTOR.Rejected('rotated profile')) as policy, mock.patch.object(EXECUTOR,'verify_bundle',side_effect=FileNotFoundError('old bundle')) as bundle:
                code = EXECUTOR.main()
            self.assertEqual(0, code)
            result = json.loads(output.getvalue())
            self.assertEqual('succeeded', result['status'])
            self.assertTrue(result['journal_terminal'])
            self.assertEqual(original['source_sha'],result['source_sha'])
            policy.assert_not_called();bundle.assert_not_called()

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

    def test_terminal_receipt_is_persisted_before_releasing_host_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "journal").mkdir(mode=0o700)
            lock_path = root / "lock"
            lock_path.touch(mode=0o600)
            persisted_while_locked: list[bool] = []
            original_atomic_json = EXECUTOR.atomic_json

            def acquire(_: str) -> int:
                descriptor = os.open(lock_path, os.O_RDWR)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor

            def persist(path: pathlib.Path, value: dict[str, object]) -> None:
                if isinstance(value.get("response"), dict):
                    with lock_path.open("r+") as contender:
                        try:
                            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            persisted_while_locked.append(True)
                        else:
                            persisted_while_locked.append(False)
                            fcntl.flock(contender, fcntl.LOCK_UN)
                original_atomic_json(path, value)

            with mock.patch.object(EXECUTOR, "ROOT", root), mock.patch.object(EXECUTOR, "safe_directory"), mock.patch.object(EXECUTOR, "acquire_host_lock", side_effect=acquire), mock.patch.object(EXECUTOR, "run_phase", return_value={"successful": True}), mock.patch.object(EXECUTOR, "atomic_json", side_effect=persist):
                result = EXECUTOR.execute(request(), {}, (pathlib.Path(), pathlib.Path(), pathlib.Path()))

            self.assertEqual("succeeded", result["status"])
            self.assertEqual([True], persisted_while_locked)

    def test_initial_journal_write_failure_releases_host_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "journal").mkdir(mode=0o700)
            lock_path = root / "lock"
            lock_path.touch(mode=0o600)
            acquired: list[int] = []

            def acquire(_: str) -> int:
                descriptor = os.open(lock_path, os.O_RDWR)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired.append(descriptor)
                return descriptor

            try:
                with mock.patch.object(EXECUTOR, "ROOT", root), mock.patch.object(EXECUTOR, "safe_directory"), mock.patch.object(EXECUTOR, "acquire_host_lock", side_effect=acquire), mock.patch.object(EXECUTOR, "atomic_json", side_effect=OSError("synthetic receipt write failure")), mock.patch.object(EXECUTOR, "run_phase") as runner:
                    with self.assertRaises(OSError):
                        EXECUTOR.execute(request(), {}, (pathlib.Path(), pathlib.Path(), pathlib.Path()))
                runner.assert_not_called()
                with lock_path.open("r+") as contender:
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(contender, fcntl.LOCK_UN)
            finally:
                for descriptor in acquired:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    def test_disconnect_kills_descendants_before_unlock_and_persists_uncertainty(self) -> None:
        driver = """
import fcntl,importlib.machinery,importlib.util,json,os,pathlib,subprocess,sys,tempfile
from unittest import mock
loader=importlib.machinery.SourceFileLoader('executor',sys.argv[1]);spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
root=pathlib.Path(sys.argv[2]);(root/'journal').mkdir(mode=0o700);lock=root/'lock';lock.touch(mode=0o600)
request=json.loads(sys.argv[3])
def acquire(host):
    fd=os.open(lock,os.O_RDWR);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);return fd
def phase(request,policy,paths,phase):
    if phase=='preflight': return {'successful':True,'evidence_hash':request['approved_check_result_hash']}
    child="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"
    program="import os,pathlib,subprocess,sys,time;pathlib.Path(sys.argv[1]+'.group').write_text(str(os.getpid()));p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]);pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
    with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
        module.run_bounded([sys.executable,'-c',program,str(root/'child.pid'),child],root,{'PATH':os.environ.get('PATH','')},out,err,45)
with mock.patch.object(module,'ROOT',root),mock.patch.object(module,'safe_directory'),mock.patch.object(module,'acquire_host_lock',side_effect=acquire),mock.patch.object(module,'run_phase',side_effect=phase):
    result=module.execute(request,{},(root,root,root));(root/'receipt.json').write_text(json.dumps(result))
"""
        for selected_signal in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=selected_signal), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                parent = subprocess.Popen([sys.executable, '-c', driver, str(ROOT/'scripts'/'production-profile-executor'), directory, json.dumps(request(operation='apply', approved_check_result_hash='d'*64))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                pid = None
                try:
                    deadline = time.monotonic()+5
                    while not (root/'child.pid').exists() and parent.poll() is None and time.monotonic()<deadline:
                        time.sleep(0.02)
                    self.assertTrue((root/'child.pid').exists(), 'synthetic descendant did not start')
                    pid = int((root/'child.pid').read_text())
                    os.kill(parent.pid, selected_signal)
                    parent.wait(timeout=8)
                    if selected_signal == signal.SIGKILL:
                        self.assertFalse((root/'receipt.json').exists())
                        journals = list((root/'journal').glob('*.json'))
                        self.assertEqual(1, len(journals))
                        self.assertIsNone(json.loads(journals[0].read_text())['response'])
                        with (root/'lock').open('r+') as contender:
                            with self.assertRaises(BlockingIOError):
                                fcntl.flock(contender, fcntl.LOCK_EX|fcntl.LOCK_NB)
                        continue
                    self.assertTrue((root/'receipt.json').exists(), 'disconnect lost the durable uncertain receipt')
                    self.assertEqual('uncertain', json.loads((root/'receipt.json').read_text())['status'])
                    for _ in range(40):
                        try:
                            os.kill(pid, 0)
                            process_state = pathlib.Path(f'/proc/{pid}/stat')
                            if process_state.exists() and process_state.read_text().split()[2]=='Z':
                                break
                        except ProcessLookupError:
                            break
                        time.sleep(0.025)
                    else:
                        self.fail('descendant survived target disconnect')
                    with (root/'lock').open('r+') as contender:
                        fcntl.flock(contender, fcntl.LOCK_EX|fcntl.LOCK_NB)
                finally:
                    if parent.poll() is None:
                        parent.kill();parent.wait()
                    if pid is not None:
                        try:
                            os.killpg(int((root/'child.pid.group').read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_bounded_runner_terminates_the_entire_child_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            child_pid = pathlib.Path(directory) / "child.pid"
            child = "import signal,time;signal.signal(signal.SIGTERM, signal.SIG_IGN);time.sleep(60)"
            program = "import pathlib,subprocess,sys,time;p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]);pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                with self.assertRaises(subprocess.TimeoutExpired):
                    EXECUTOR.run_bounded([sys.executable, "-c", program, str(child_pid), child], pathlib.Path(directory), {"PATH": os.environ.get("PATH", "")}, stdout, stderr, 0.5)
            pid = int(child_pid.read_text(encoding="ascii"))
            for _ in range(20):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("descendant survived the bounded process-group timeout")


if __name__ == "__main__":
    unittest.main()
