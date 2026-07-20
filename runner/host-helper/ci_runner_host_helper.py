#!/usr/bin/env python3
"""Root-only, narrow libvirt helper for sanctuary CI VMs."""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

LEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PREFIX = "sanctuary-ci-"
OVERLAY_ROOT = Path("/mnt/ssd1000-01/ci-runner")
BASE_IMAGE = Path("/var/lib/ci-runner/images/ubuntu-24.04-runner.qcow2")
NETWORK = "sanctuary-ci"
VCPUS = "8"
MEMORY_MIB = "12288"
DISK_GIB = "120G"
HELPER_LOCK = Path("/run/lock/ci-runner-host-helper.lock")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=90)


def validate_lease(value: str) -> str:
    if not LEASE_RE.fullmatch(value):
        raise ValueError("invalid lease id")
    return value


def name(lease: str) -> str:
    return PREFIX + validate_lease(lease)


def lease_dir(lease: str) -> Path:
    directory = OVERLAY_ROOT / validate_lease(lease)
    if directory.parent != OVERLAY_ROOT:
        raise ValueError("invalid lease path")
    return directory


def launch(lease: str) -> None:
    if os.geteuid() != 0:
        raise PermissionError("helper must run as root")
    if not BASE_IMAGE.is_file():
        raise FileNotFoundError(f"base image missing: {BASE_IMAGE}")
    existing = run(["virsh", "list", "--all", "--name"])
    if any(line.startswith(PREFIX) for line in existing.stdout.splitlines()):
        raise RuntimeError("a sanctuary CI domain already exists")
    encoded_jit = sys.stdin.buffer.read(131073).strip()
    if not encoded_jit or len(encoded_jit) > 131072:
        raise ValueError("invalid JIT configuration size")
    base64.b64decode(encoded_jit, validate=True)
    directory = lease_dir(lease)
    if directory.exists():
        raise FileExistsError("lease directory already exists")
    directory.mkdir(mode=0o700)
    overlay = directory / "root.qcow2"
    seed = directory / "seed.iso"
    try:
        run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(BASE_IMAGE), str(overlay), DISK_GIB])
        user_data = """#cloud-config
bootcmd:
  - [install, -d, -o, ci-runner, -g, ci-runner, -m, '0700', /run/ci-runner]
write_files:
  - path: /run/ci-runner/jit.config
    owner: ci-runner:ci-runner
    permissions: '0600'
    encoding: b64
    content: %s
runcmd:
  - [systemctl, start, ci-runner-job.service]
""" % base64.b64encode(encoded_jit).decode("ascii")
        meta_data = f"instance-id: {name(lease)}\nlocal-hostname: ci-worker\n"
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            temp = Path(temporary)
            (temp / "user-data").write_text(user_data, encoding="utf-8")
            (temp / "meta-data").write_text(meta_data, encoding="utf-8")
            os.chmod(temp / "user-data", 0o600)
            run(["cloud-localds", str(seed), str(temp / "user-data"), str(temp / "meta-data")])
        run(["virt-install", "--name", name(lease), "--memory", MEMORY_MIB, "--vcpus", VCPUS,
             "--cpu", "host-passthrough", "--import", "--noautoconsole", "--os-variant", "ubuntu24.04",
             "--disk", f"path={overlay},format=qcow2,bus=virtio,cache=none,discard=unmap",
             "--disk", f"path={seed},device=cdrom,readonly=on",
             "--network", f"network={NETWORK},model=virtio", "--graphics", "none",
             "--rng", "/dev/urandom", "--controller", "type=scsi,model=virtio-scsi"])
    except Exception:
        destroy(lease)
        raise


def destroy(lease: str) -> None:
    domain = name(lease)
    run(["virsh", "destroy", domain], check=False)
    run(["virsh", "undefine", domain, "--nvram"], check=False)
    directory = lease_dir(lease)
    if directory.exists():
        shutil.rmtree(directory)


def list_leases() -> None:
    result = run(["virsh", "list", "--all", "--name"])
    leases = {}
    for domain in sorted(line for line in result.stdout.splitlines() if line.startswith(PREFIX)):
        state = run(["virsh", "domstate", domain]).stdout.strip().lower()
        leases[domain[len(PREFIX):]] = state
    print(json.dumps(leases, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("launch", "destroy"):
        child = sub.add_parser(command)
        child.add_argument("lease")
    sub.add_parser("list")
    args = parser.parse_args()
    try:
        with HELPER_LOCK.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.command == "launch":
                launch(args.lease)
            elif args.command == "destroy":
                destroy(args.lease)
            else:
                list_leases()
        return 0
    except Exception as exc:
        print(f"ci-runner-host-helper: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
