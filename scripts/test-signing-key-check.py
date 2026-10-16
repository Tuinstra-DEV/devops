#!/usr/bin/env python3
"""Exercise the real signing-key Ansible task against isolated local files.

Uses a loopback-only HTTP fixture for the legacy get_url regression. No system
keyring, package repository, production host or external network is modified.
Requires the existing Ansible/PyYAML runtime; --key-file supports the pre-fix RED.
"""
import argparse
import copy
import http.server
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "infra/ansible/roles/production_host_baseline"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, default=ROLE / "files/docker.asc")
    args = parser.parse_args()
    key = args.key_file.read_bytes()
    assert key.startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----"), "public-key fixture required"
    executable = shutil.which("ansible-playbook")
    assert executable, "ansible-playbook is required for this native regression"
    tasks = yaml.safe_load((ROLE / "tasks/docker_repository.yml").read_text())
    installs = [(t, m) for t in tasks for m in ("ansible.builtin.get_url", "ansible.builtin.copy")
                if t.get(m, {}).get("dest") == "/etc/apt/keyrings/docker.asc"]
    assert len(installs) == 1, "one actual signing-key installation task required"
    source, module = installs[0]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(key)))
            self.end_headers()

        def do_GET(self):
            self.do_HEAD()
            self.wfile.write(key)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="dev36-key-check-") as temporary:
            folder = Path(temporary)
            target = folder / "installed.asc"
            target.write_bytes(key)
            target.chmod(0o644)
            task = copy.deepcopy(source)
            options = task[module]
            options["dest"] = str(target)
            # The isolated fixture uses the current user, never the system keyring.
            options.pop("owner", None)
            options.pop("group", None)
            if module == "ansible.builtin.copy":
                options["src"] = str(ROLE / "files" / options["src"])
            play = [{"hosts": "localhost", "connection": "local", "gather_facts": False,
                     "vars": {"ansible_python_interpreter": sys.executable,
                              "production_docker_apt_key_url": f"http://127.0.0.1:{server.server_port}/docker.asc"},
                     "tasks": [task]}]
            path = folder / "play.yml"
            path.write_text(yaml.safe_dump(play))
            configuration = folder / "ansible.cfg"
            configuration.write_text("[defaults]\n")
            environment = os.environ.copy()
            environment.update({"ANSIBLE_CONFIG": str(configuration), "ANSIBLE_HOME": str(folder / "ansible"),
                                "ANSIBLE_LOCAL_TEMP": str(folder / "tmp"), "ANSIBLE_REMOTE_TMP": str(folder / "remote"), "ANSIBLE_NOCOLOR": "1",
                                "ANSIBLE_STDOUT_CALLBACK": "default", "PYTHONDONTWRITEBYTECODE": "1"})

            def run(check):
                argv = [executable, "-i", "localhost,", str(path)]
                if check:
                    argv.append("--check")
                result = subprocess.run(argv, capture_output=True, text=True, env=environment, timeout=90)
                assert result.returncode == 0, result.stdout + result.stderr
                recap = re.search(r"localhost\s*:\s*ok=\d+\s+changed=(\d+)", result.stdout)
                assert recap, result.stdout
                return int(recap.group(1))

            assert run(True) == 0, "unchanged approved public key falsely reports drift in check mode"
            assert target.read_bytes() == key, "check mode changed existing public key"
            target.write_bytes(b"synthetic corrupt key fixture\n")
            assert run(True) == 1, "actual public-key corruption must be reported"
            assert target.read_bytes() != key, "check mode must not repair corruption"
            assert run(False) == 1, "actual apply must restore the approved public key"
            assert target.read_bytes() == key and target.stat().st_mode & 0o777 == 0o644
            assert run(True) == 0, "restored key must have zero repeated drift"
            print("Signing-key native regression PASS: unchanged=0, corrupt=1, repair=1, repeat=0")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
