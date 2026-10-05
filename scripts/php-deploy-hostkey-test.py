#!/usr/bin/env python3
"""Exercise the PHP deploy workflow's real SSH setup and remote command steps."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-cd-php.yml"
HOST = "deploy.example.test"
PORT = "2222"
KNOWN_HOST = f"[{HOST}]:{PORT}"


def fail(message: str) -> None:
    raise AssertionError(message)


def extracted_deploy_steps() -> list[dict[str, object]]:
    ruby = r'''
      require "yaml"
      require "json"
      document = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true)
      steps = document.fetch("jobs").fetch("deploy").fetch("steps")
      puts JSON.generate(steps.select { |step| step["run"] }.map { |step|
        {"name" => step["name"], "run" => step["run"], "env" => step["env"] || {}}
      })
    '''
    result = subprocess.run(
        ["ruby", "-e", ruby, str(WORKFLOW)], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def keypair(directory: Path, name: str) -> tuple[Path, str, str]:
    private_key = directory / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)],
        check=True,
    )
    public_parts = private_key.with_suffix(".pub").read_text().split()
    fingerprint_output = subprocess.run(
        ["ssh-keygen", "-lf", str(private_key.with_suffix(".pub"))],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    fingerprint = re.search(r"SHA256:[A-Za-z0-9+/]+", fingerprint_output)
    if not fingerprint:
        fail("ssh-keygen did not return an ED25519 SHA256 fingerprint")
    return private_key, public_parts[1], fingerprint.group(0)


def interpolated_run(run: str) -> str:
    values = {
        "inputs.ssh-port": PORT,
        "vars.SSH_HOST": HOST,
        "inputs.ssh-user": "deployer",
        "inputs.remote-path": "/srv/example",
        "inputs.compose-file": "docker-compose.yml",
        "inputs.service-name": "php",
        "inputs.nginx-service-name": "nginx",
        "inputs.pre-deploy-cleanup": "none",
        "inputs.immutable-runtime": "false",
        "inputs.run-migrations": "true",
        "inputs.restart-worker": "true",
        "inputs.worker-services != ''": "true",
        "inputs.verify-runtime-command != ''": "true",
        "inputs.verify-runtime-command": "php bin/console about",
        "inputs.verify-runtime-command-contains": "Symfony",
        "inputs.port": "80",
        "inputs.health-path": "/health",
        "inputs.health-timeout-seconds": "1",
        "github.run_id": "501",
        "github.run_attempt": "1",
    }
    return re.sub(
        r"\$\{\{\s*(.*?)\s*\}\}",
        lambda match: values.get(match.group(1), "synthetic-value"),
        run,
    )


def write_stubs(bin_dir: Path) -> None:
    scripts = {
        "ssh-keyscan": r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$CALL_LOG/keyscan.args"
if [[ -n "${KEYSCAN_FAIL:-}" ]]; then exit 42; fi
cat "$KEYSCAN_OUTPUT"
''',
        "ssh": r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\0' "$@" >> "$CALL_LOG/ssh.args0"
printf '\n' >> "$CALL_LOG/ssh.args0"
index=0
if [[ -f "$CALL_LOG/ssh.count" ]]; then read -r index < "$CALL_LOG/ssh.count"; fi
index=$((index + 1))
printf '%s\n' "$index" > "$CALL_LOG/ssh.count"
cat > "$CALL_LOG/ssh.stdin.$index"
joined="$*"
if [[ "$joined" == *" compose -f docker-compose.yml port "* ]]; then
  printf '0.0.0.0:18080\n'
fi
''',
        "scp": r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\0' "$@" >> "$CALL_LOG/scp.args0"
printf '\n' >> "$CALL_LOG/scp.args0"
''',
        "timeout": "#!/usr/bin/env bash\nshift\nexec \"$@\"\n",
        "sleep": "#!/usr/bin/env bash\nexit 0\n",
    }
    for name, content in scripts.items():
        path = bin_dir / name
        path.write_text(content)
        path.chmod(0o755)


def read_invocations(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    data = path.read_bytes()
    records = [record for record in data.split(b"\n") if record]
    return [[part.decode() for part in record.split(b"\0") if part] for record in records]


def run_case(
    steps: list[dict[str, object]],
    *,
    pin: str,
    scan_output: str,
    scan_fails: bool = False,
    stale_known_hosts: bool = False,
    execute_remote: bool = False,
    host: str = HOST,
    port: str = PORT,
    repeat_configure: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    with tempfile.TemporaryDirectory(prefix="php-deploy-hostkey-") as raw:
        tmp = Path(raw)
        runner_temp = tmp / "runner-temp"
        runner_temp.mkdir()
        ssh_dir = runner_temp / "php-deploy-ssh"
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        write_stubs(bin_dir)
        key_dir = tmp / "keys"
        key_dir.mkdir()
        private_key, _, _ = keypair(key_dir, "deploy")
        ssh_dir.mkdir(exist_ok=True)
        if stale_known_hosts:
            ssh_dir.mkdir(parents=True, exist_ok=True)
            (ssh_dir / "deploy_known_hosts").write_text("old.example.test ssh-ed25519 AAAA\n")
        output_file = tmp / "scan.out"
        output_file.write_text(scan_output)
        call_log = tmp / "calls"
        call_log.mkdir()
        (call_log / "expected-config").write_text(str(ssh_dir / "deploy_config"))
        (call_log / "expected-trust").write_text(str(ssh_dir / "deploy_known_hosts"))
        (call_log / "expected-identity").write_text(str(ssh_dir / "id_ed25519"))
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "RUNNER_TEMP": str(runner_temp),
                "CALL_LOG": str(call_log),
                "KEYSCAN_OUTPUT": str(output_file),
                "SSH_PRIVATE_KEY": private_key.read_text(),
                "SSH_HOST": host,
                "SSH_PORT": port,
                "SSH_USER": "deployer",
                "SSH_HOST_ED25519_FINGERPRINT": pin,
                "GITHUB_OUTPUT": str(tmp / "github-output"),
                "GITHUB_SHA": "a" * 40,
                "WORKER_SERVICES": "worker",
                "PHP_IMAGE_REF": "ghcr.io/example/app@sha256:" + "a" * 64,
                "NGINX_IMAGE_REF": "ghcr.io/example/app@sha256:" + "b" * 64,
                "VERIFY_RUNTIME_COMMAND": "php bin/console about",
                "VERIFY_RUNTIME_COMMAND_CONTAINS": "Symfony",
            }
        )
        if scan_fails:
            env["KEYSCAN_FAIL"] = "1"
        configure = next(step for step in steps if step["name"] == "Configure SSH")
        command = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", interpolated_run(str(configure["run"]))],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if repeat_configure and command.returncode == 0:
            command = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", interpolated_run(str(configure["run"]))],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        if command.returncode == 0:
            ssh_binary = shutil.which("ssh")
            if not ssh_binary:
                fail("ssh is required for offline effective-config validation")
            effective = subprocess.run(
                [ssh_binary, "-G", "-F", str(ssh_dir / "deploy_config"), "deploy-target"],
                env=env,
                capture_output=True,
                text=True,
            )
            if effective.returncode != 0:
                fail(f"OpenSSH rejected generated config: {effective.stderr}")
            (call_log / "ssh.effective-config").write_text(effective.stdout)
        if execute_remote and command.returncode == 0:
            for step in steps:
                if step["name"] in {"Configure SSH", "Ensure SSH key is present"}:
                    continue
                step_env = env.copy()
                for key, value in dict(step.get("env", {})).items():
                    if key == "SSH_PRIVATE_KEY":
                        step_env[key] = env["SSH_PRIVATE_KEY"]
                    elif key in env:
                        step_env[key] = env[key]
                    else:
                        step_env[key] = str(value)
                result = subprocess.run(
                    ["bash", "-euo", "pipefail", "-c", interpolated_run(str(step["run"]))],
                    cwd=ROOT,
                    env=step_env,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    command = result
                    break
        # Copy artifacts before TemporaryDirectory is cleaned up.
        saved = Path(tempfile.mkdtemp(prefix="php-deploy-hostkey-result-"))
        shutil.copytree(ssh_dir, saved / ".ssh")
        shutil.copytree(call_log, saved / "calls")
        command._saved_result = saved  # type: ignore[attr-defined]
        return command, saved / ".ssh", saved / "calls"


def assert_remote_calls_are_pinned(calls: Path) -> None:
    ssh_calls = read_invocations(calls / "ssh.args0")
    scp_calls = read_invocations(calls / "scp.args0")
    expected_config = (calls / "expected-config").read_text()
    if not ssh_calls or not scp_calls:
        fail("successful deploy did not exercise both SSH and SCP")
    for operation, invocations in (("ssh", ssh_calls), ("scp", scp_calls)):
        for args in invocations:
            if "-F" not in args or args[args.index("-F") + 1] != expected_config:
                fail(f"{operation} did not use the generated isolated SSH config: {args}")
            alias_used = any(arg == "deploy-target" or arg.startswith("deploy-target:") for arg in args)
            if not alias_used:
                fail(f"{operation} bypassed the verified deploy-target alias: {args}")
            if "-p" in args or "-P" in args:
                fail(f"{operation} bypassed the alias port configuration: {args}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", choices=("product-owner", "project-manager", "developer", "tester"))
    args = parser.parse_args()
    steps = extracted_deploy_steps()
    with tempfile.TemporaryDirectory(prefix="php-deploy-hostkey-fixtures-") as raw:
        fixtures = Path(raw)
        _, key_blob, fingerprint = keypair(fixtures, "correct")
        _, other_blob, other_fingerprint = keypair(fixtures, "substituted")
        noncanonical_fingerprint = fingerprint[:-1] + ("B" if fingerprint[-1] != "B" else "C")
        rsa_key = fixtures / "rsa"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "rsa", "-b", "2048", "-N", "", "-f", str(rsa_key)],
            check=True,
        )
        rsa_blob = rsa_key.with_suffix(".pub").read_text().split()[1]
        valid_line = f"{KNOWN_HOST} ssh-ed25519 {key_blob}\n"
        cases = [
            ("missing pin", "", valid_line, False, True),
            ("malformed pin", "SHA256:bad", valid_line, False, True),
            ("noncanonical pin", noncanonical_fingerprint, valid_line, False, True),
            ("invalid host", fingerprint, valid_line, False, True),
            ("invalid port", fingerprint, valid_line, False, True),
            ("scan exit failure", fingerprint, valid_line, True, True),
            ("empty scan", fingerprint, "", False, True),
            ("malformed scan", fingerprint, f"{KNOWN_HOST} ssh-ed25519 not-a-key\n", False, True),
            ("duplicate scan records", fingerprint, valid_line + valid_line, False, True),
            ("substituted host key", fingerprint, f"{KNOWN_HOST} ssh-ed25519 {other_blob}\n", False, True),
            ("wrong host", fingerprint, f"[other.example.test]:{PORT} ssh-ed25519 {key_blob}\n", False, True),
            ("wrong port", fingerprint, f"[{HOST}]:2223 ssh-ed25519 {key_blob}\n", False, True),
            ("wrong algorithm", fingerprint, f"{KNOWN_HOST} ssh-rsa {rsa_blob}\n", False, True),
            ("wrong fingerprint", other_fingerprint, valid_line, False, True),
        ]
        for label, pin, output, scan_fails, stale in cases:
            case_host = "127.0.0.1" if label == "invalid host" else HOST
            case_port = "70000" if label == "invalid port" else PORT
            result, ssh_dir, calls = run_case(
                steps,
                pin=pin,
                scan_output=output,
                scan_fails=scan_fails,
                stale_known_hosts=stale,
                host=case_host,
                port=case_port,
            )
            if result.returncode == 0:
                fail(f"{label}: invalid host trust was accepted")
            if read_invocations(calls / "ssh.args0") or read_invocations(calls / "scp.args0"):
                fail(f"{label}: a remote SSH/SCP operation ran after verification failed")
            if (ssh_dir / "deploy_known_hosts").exists():
                fail(f"{label}: existing trusted host material survived failed verification")
            if label.startswith("invalid ") or "pin" in label:
                if (calls / "keyscan.args").exists():
                    fail(f"{label}: host scan ran before input validation")
            print(f"PASS failure gate: {label}; zero SSH/SCP operations")
            shutil.rmtree(result._saved_result)  # type: ignore[attr-defined]

        result, ssh_dir, calls = run_case(
            steps,
            pin=fingerprint,
            scan_output=valid_line,
            execute_remote=True,
            repeat_configure=True,
        )
        if result.returncode != 0:
            fail(f"valid pinned deployment failed: {result.stderr}")
        config_path = ssh_dir / "deploy_config"
        if not config_path.exists():
            fail("successful host verification did not create SSH alias configuration")
        config = config_path.read_text()
        for required in (
            f"HostName {HOST}",
            "User deployer",
            f"Port {PORT}",
            "StrictHostKeyChecking yes",
            "HostKeyAlgorithms ssh-ed25519",
            "GlobalKnownHostsFile /dev/null",
        ):
            if required not in config:
                fail(f"SSH alias configuration is missing {required!r}")
        effective_config = (calls / "ssh.effective-config").read_text()
        effective = dict(line.split(" ", 1) for line in effective_config.splitlines() if " " in line)
        expected_effective = {
            "hostname": HOST,
            "user": "deployer",
            "port": PORT,
            "stricthostkeychecking": "true",
            "hostkeyalgorithms": "ssh-ed25519",
            "userknownhostsfile": (calls / "expected-trust").read_text(),
            "globalknownhostsfile": "/dev/null",
            "identityfile": (calls / "expected-identity").read_text(),
        }
        for name, expected in expected_effective.items():
            actual = effective.get(name, "")
            if actual != expected:
                fail(f"effective OpenSSH config {name} was {actual!r}, expected {expected!r}")
        assert_remote_calls_are_pinned(calls)
        ssh_calls = read_invocations(calls / "ssh.args0")
        scp_calls = read_invocations(calls / "scp.args0")
        ssh_text = "\n".join(" ".join(call) for call in ssh_calls)
        ssh_inputs = "\n".join(
            path.read_text() for path in sorted(calls.glob("ssh.stdin.*"), key=lambda p: int(p.name.rsplit(".", 1)[1]))
        )
        actual_operations = ssh_text + "\n" + ssh_inputs
        operation_counts = {
            "upload": int(any(any("mkdir -p" in arg for arg in call) for call in ssh_calls)) + len(scp_calls),
            "deploy": sum(any("PHP_IMAGE_REF" in arg and "IMMUTABLE_RUNTIME" not in arg for arg in call) for call in ssh_calls),
            "runtime-image-guard": sum(any("IMMUTABLE_RUNTIME" in arg for arg in call) for call in ssh_calls),
            "runtime-verify": ssh_text.count("VERIFY_RUNTIME_COMMAND='php bin/console about'"),
            "migration": ssh_inputs.count("doctrine:migrations:migrate"),
            "worker-restart": ssh_inputs.count("docker compose -f docker-compose.yml restart"),
            "health": actual_operations.count(" compose -f docker-compose.yml port ") + actual_operations.count("curl "),
        }
        if not all(operation_counts[name] > 0 for name in ("upload", "deploy", "runtime-image-guard", "runtime-verify", "migration", "worker-restart", "health")):
            fail(f"synthetic caller journey did not exercise every expected deploy operation: {operation_counts}")
        print(f"PASS synthetic caller path: verified ED25519 pin at {HOST}:{PORT}; every SSH/SCP used strict alias; operations={operation_counts}")
        if args.persona:
            print(f"PASS synthetic caller persona scenario: {args.persona}; actual workflow operations={operation_counts}")
        shutil.rmtree(result._saved_result)  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
