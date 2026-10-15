#!/usr/bin/env python3
"""Execute pinned Caddy config and HTTP redirect checks in an isolated container.

No host ports, public network, real certificates, secrets or site backends.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

IMAGE = "caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
SOURCE = Path(__file__).resolve().parents[1] / "infra/caddy/public-websites"
HOSTS = ("wodiq.nl", "www.wodiq.nl", "marcel.tuinstra.dev", "tuinstra.dev", "openairco.tuinstra.dev")


def run(args):
    try:
        return subprocess.check_output(args, stderr=subprocess.PIPE, timeout=30)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(error.stderr.decode()[-2048:]) from None


# Existing Mac/Lima setups share repository paths, not macOS's /var/folders.
with tempfile.TemporaryDirectory(prefix=".DEV-26-caddy-", dir=SOURCE.parents[2]) as directory:
    root = Path(directory)
    shutil.copytree(SOURCE, root / "sites")
    config = root / "Caddyfile"
    config.write_text("{\n\tadmin off\n}\n\nimport /etc/caddy/sites/*.caddy\n\n:80 {\n\trespond 404\n}\n")
    options = ["docker", "run", "--pull=never", "--network=none", "--read-only",
               "--cap-drop=ALL", "--cap-add=NET_BIND_SERVICE", "--security-opt=no-new-privileges:true",
               "--tmpfs", "/data:rw,noexec,nosuid,size=16m", "--tmpfs", "/config:rw,noexec,nosuid,size=16m",
               "--mount", f"type=bind,source={root},target=/etc/caddy,readonly"]
    run(options + ["--rm", IMAGE, "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"])
    name = "dev26-caddy-" + uuid.uuid4().hex[:12]
    container = run(options + ["--detach", "--name", name, IMAGE, "caddy", "run", "--config",
                               "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]).decode().strip()
    try:
        info = json.loads(run(["docker", "inspect", container]))[0]
        assert info["HostConfig"]["NetworkMode"] == "none"
        assert not info["HostConfig"].get("PortBindings")
        rows = []
        for host in HOSTS:
            # Test preservation of both the path and harmless synthetic query.
            request = f"GET /privacy/?dev26_check=synthetic HTTP/1.0\r\nHost: {host}\r\n\r\n"
            response = ""
            for _ in range(20):
                result = subprocess.run(["docker", "exec", "--interactive", container, "nc", "-w", "1", "127.0.0.1", "80"],
                                        input=request.encode(), capture_output=True, timeout=5)
                response = result.stdout.decode()
                if response:
                    break
                time.sleep(0.1)
            assert response.startswith("HTTP/1.0 308") or response.startswith("HTTP/1.1 308"), (host, response)
            assert f"Location: https://{host}/privacy/?dev26_check=synthetic\r\n" in response, (host, response)
            rows.append({"host": host, "redirect": 308, "pathAndQueryPreserved": True})
        config.write_text("{ broken configuration\n")
        rejected = subprocess.run(options + ["--rm", IMAGE, "caddy", "validate", "--config",
                                              "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                                  capture_output=True, timeout=30)
        assert rejected.returncode != 0, "invalid candidate must be rejected before reload"
        print(json.dumps({"image": IMAGE, "network": "none", "hostPorts": 0, "routes": rows,
                          "invalidCandidateRejected": True, "pass": True}, indent=2))
    finally:
        subprocess.run(["docker", "rm", "--force", container], check=True, stdout=subprocess.DEVNULL, timeout=30)
