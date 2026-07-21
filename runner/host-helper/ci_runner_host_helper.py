#!/usr/bin/env python3
"""Root-only, narrow libvirt helper for sanctuary CI VMs."""

from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
from typing import Any

LEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PREFIX = "sanctuary-ci-"
OVERLAY_ROOT = Path("/mnt/ssd1000-01/ci-runner")
IMAGE_ROOT = Path("/var/lib/ci-runner/images")
BASE_IMAGE = IMAGE_ROOT / "ubuntu-24.04-runner.qcow2"
IMAGE_RE = re.compile(r"^ubuntu-24\.04-runner-[a-f0-9]{64}\.qcow2$")
NETWORK = "sanctuary-ci"
LIBVIRT_URI = "qemu:///system"
VCPUS = "8"
MEMORY_MIB = "12288"
DISK_GIB = "120G"
MAX_LEASE_SECONDS = "7200"
HELPER_LOCK = Path("/run/lock/ci-runner-host-helper.lock")
HELPER_PATH = "/usr/local/libexec/ci-runner-host-helper"
MANAGER_USER = "ci-runner-manager"
QEMU_USER = "libvirt-qemu"
QEMU_GROUP = "kvm"
PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 4096
MAX_JIT_BYTES = 131072
MAX_RESPONSE_BYTES = 65536
REQUEST_ID_RE = re.compile(r"^[a-f0-9]{32}$")
ERROR_CODES = frozenset({"invalid_request", "unauthorized", "operation_failed", "busy"})
DIAGNOSTIC_COMMANDS = frozenset({
    "cloud-localds", "qemu-img", "systemctl", "systemd-run", "virsh", "virt-install",
})
UNKNOWN_REQUEST_ID = "0" * 32
SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)


class ProtocolError(ValueError):
    """A request error that is safe to represent with a generic code."""


class HostCommandError(RuntimeError):
    """A host command failure containing only allowlisted diagnostic metadata."""

    def __init__(self, command_name: str, returncode: int | str) -> None:
        safe_name = Path(command_name).name
        self.command_name = safe_name if safe_name in DIAGNOSTIC_COMMANDS else "unknown"
        self.returncode = returncode if isinstance(returncode, int) else "timeout"
        super().__init__(f"{self.command_name} rc={self.returncode}")


def strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON member")
        result[key] = value
    return result


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=check, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=90)
    except subprocess.CalledProcessError as exc:
        raise HostCommandError(command[0], exc.returncode) from exc
    except subprocess.TimeoutExpired as exc:
        raise HostCommandError(command[0], "timeout") from exc


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


def resolved_base_image() -> Path:
    image = BASE_IMAGE.resolve(strict=True)
    if image.parent != IMAGE_ROOT or not IMAGE_RE.fullmatch(image.name) or not image.is_file():
        raise ValueError("base image link does not target a digest-versioned runner image")
    return image


def qemu_identity() -> tuple[int, int]:
    user = pwd.getpwnam(QEMU_USER)
    group = grp.getgrnam(QEMU_GROUP)
    if user.pw_gid != group.gr_gid:
        raise RuntimeError("libvirt QEMU primary group does not match configured group")
    return user.pw_uid, group.gr_gid


def set_qemu_access(path: Path, uid: int, gid: int, mode: int) -> None:
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def validate_jit_payload(encoded_jit: bytes) -> bytes:
    if not encoded_jit or len(encoded_jit) > MAX_JIT_BYTES:
        raise ProtocolError("invalid JIT configuration size")
    try:
        base64.b64decode(encoded_jit, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProtocolError("invalid JIT configuration encoding") from exc
    return encoded_jit


def cloud_init_user_data(encoded_jit: bytes) -> str:
    encoded_jit = validate_jit_payload(encoded_jit)
    return """#cloud-config
bootcmd:
  - [install, -d, -o, ci-runner, -g, ci-runner, -m, '0700', /run/ci-runner]
  - [install, -d, -o, root, -g, root, -m, '0755', /etc/systemd/system/ci-runner-job.service.d]
  - [install, -o, ci-runner, -g, ci-runner, -m, '0600', /dev/null, /opt/actions-runner/.runner]
  - [install, -o, ci-runner, -g, ci-runner, -m, '0600', /dev/null, /opt/actions-runner/.credentials]
  - [install, -o, ci-runner, -g, ci-runner, -m, '0600', /dev/null, /opt/actions-runner/.credentials_rsaparams]
write_files:
  - path: /run/ci-runner/jit.config
    owner: ci-runner:ci-runner
    permissions: '0600'
    encoding: b64
    content: %s
  - path: /etc/systemd/system/ci-runner-job.service.d/10-jit-files.conf
    owner: root:root
    permissions: '0644'
    content: |
      [Service]
      ReadWritePaths=/opt/actions-runner/.runner /opt/actions-runner/.credentials /opt/actions-runner/.credentials_rsaparams
runcmd:
  - [systemctl, daemon-reload]
  - [systemctl, start, --no-block, ci-runner-job.service]
""" % base64.b64encode(encoded_jit).decode("ascii")


def launch(lease: str, encoded_jit: bytes | None = None) -> None:
    if os.geteuid() != 0:
        raise PermissionError("helper must run as root")
    validate_lease(lease)
    if encoded_jit is None:
        encoded_jit = sys.stdin.buffer.read(MAX_JIT_BYTES + 1).strip()
    encoded_jit = validate_jit_payload(encoded_jit)
    base_image = resolved_base_image()
    existing = run(["virsh", "--connect", LIBVIRT_URI, "list", "--all", "--name"])
    if any(line.startswith(PREFIX) for line in existing.stdout.splitlines()):
        raise RuntimeError("a sanctuary CI domain already exists")
    directory = lease_dir(lease)
    if directory.exists():
        raise FileExistsError("lease directory already exists")
    directory.mkdir(mode=0o700)
    overlay = directory / "root.qcow2"
    seed = directory / "seed.iso"
    qemu_uid, qemu_gid = qemu_identity()
    set_qemu_access(directory, 0, qemu_gid, 0o710)
    try:
        run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base_image), str(overlay), DISK_GIB])
        set_qemu_access(overlay, qemu_uid, qemu_gid, 0o600)
        user_data = cloud_init_user_data(encoded_jit)
        meta_data = f"instance-id: {name(lease)}\nlocal-hostname: ci-worker\n"
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            temp = Path(temporary)
            (temp / "user-data").write_text(user_data, encoding="utf-8")
            (temp / "meta-data").write_text(meta_data, encoding="utf-8")
            os.chmod(temp / "user-data", 0o600)
            run(["cloud-localds", str(seed), str(temp / "user-data"), str(temp / "meta-data")])
        set_qemu_access(seed, qemu_uid, qemu_gid, 0o600)
        run(["virt-install", "--connect", LIBVIRT_URI,
             "--name", name(lease), "--memory", MEMORY_MIB, "--vcpus", VCPUS,
             "--cpu", "host-passthrough", "--import", "--noautoconsole", "--os-variant", "ubuntu24.04",
             "--disk", f"path={overlay},format=qcow2,bus=virtio,cache=none,discard=unmap",
             "--disk", f"path={seed},device=cdrom,readonly=on",
             "--network", f"network={NETWORK},model=virtio", "--graphics", "none",
             "--rng", "/dev/urandom", "--controller", "type=scsi,model=virtio-scsi"])
        run(["systemd-run", "--unit", f"{PREFIX}expire-{lease}",
             "--on-active", f"{MAX_LEASE_SECONDS}s", "--timer-property", "AccuracySec=30s",
             "--property=NoNewPrivileges=yes", "--property=ProtectSystem=strict",
             "--property=ProtectHome=yes", "--property=PrivateTmp=yes",
             f"--property=ReadWritePaths={OVERLAY_ROOT} /run/lock",
             "--property=RestrictAddressFamilies=AF_UNIX",
             HELPER_PATH, "destroy", lease])
    except Exception:
        destroy(lease)
        raise


def destroy(lease: str) -> None:
    domain = name(lease)
    run(["systemctl", "stop", f"{PREFIX}expire-{lease}.timer"], check=False)
    run(["virsh", "--connect", LIBVIRT_URI, "destroy", domain], check=False)
    run(["virsh", "--connect", LIBVIRT_URI, "undefine", domain, "--nvram"], check=False)
    directory = lease_dir(lease)
    if directory.exists():
        shutil.rmtree(directory)


def list_leases() -> dict[str, str]:
    result = run(["virsh", "--connect", LIBVIRT_URI, "list", "--all", "--name"])
    leases = {}
    for domain in sorted(line for line in result.stdout.splitlines() if line.startswith(PREFIX)):
        state = run(["virsh", "--connect", LIBVIRT_URI, "domstate", domain]).stdout.strip().lower()
        leases[domain[len(PREFIX):]] = state
    return leases


def parse_request(packet: bytes) -> dict[str, Any]:
    if not packet or len(packet) > MAX_REQUEST_BYTES:
        raise ProtocolError("invalid request size")
    try:
        request = json.loads(packet, object_pairs_hook=strict_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid request JSON") from exc
    if not isinstance(request, dict):
        raise ProtocolError("request must be an object")
    if request.get("v") != PROTOCOL_VERSION or isinstance(request.get("v"), bool):
        raise ProtocolError("invalid protocol version")
    request_id = request.get("id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise ProtocolError("invalid request id")
    operation = request.get("op")
    if operation == "list":
        if set(request) != {"v", "id", "op"}:
            raise ProtocolError("invalid list request schema")
    elif operation in {"launch", "destroy"}:
        if set(request) != {"v", "id", "op", "lease"}:
            raise ProtocolError("invalid mutation request schema")
        lease = request.get("lease")
        if not isinstance(lease, str):
            raise ProtocolError("invalid lease type")
        try:
            validate_lease(lease)
        except ValueError as exc:
            raise ProtocolError("invalid lease") from exc
    else:
        raise ProtocolError("invalid operation")
    return request


def request_id_from_packet(packet: bytes) -> str:
    try:
        request = json.loads(packet)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return UNKNOWN_REQUEST_ID
    if isinstance(request, dict) and isinstance(request.get("id"), str) \
            and REQUEST_ID_RE.fullmatch(request["id"]):
        return request["id"]
    return UNKNOWN_REQUEST_ID


def peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    raw = connection.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)


def recv_packet(connection: socket.socket, maximum: int) -> bytes:
    packet = connection.recv(maximum + 1)
    if not packet or len(packet) > maximum:
        raise ProtocolError("invalid packet size")
    return packet


def encode_response(request_id: str, *, result: Any | None = None,
                    error: str | None = None) -> bytes:
    if not REQUEST_ID_RE.fullmatch(request_id):
        request_id = UNKNOWN_REQUEST_ID
    if error is None:
        response = {"v": PROTOCOL_VERSION, "id": request_id, "ok": True, "result": result}
    else:
        if error not in ERROR_CODES:
            error = "operation_failed"
        response = {"v": PROTOCOL_VERSION, "id": request_id, "ok": False, "error": error}
    encoded = json.dumps(response, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = json.dumps({
            "v": PROTOCOL_VERSION,
            "id": request_id,
            "ok": False,
            "error": "operation_failed",
        }, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return encoded


def serve_connection(connection: socket.socket, *, expected_uid: int | None = None) -> None:
    request_id = UNKNOWN_REQUEST_ID
    try:
        if expected_uid is None:
            expected_uid = pwd.getpwnam(MANAGER_USER).pw_uid
        _pid, uid, _gid = peer_credentials(connection)
        if uid != expected_uid:
            raise PermissionError("rejected peer uid")
        connection.settimeout(5.0)
        request_packet = recv_packet(connection, MAX_REQUEST_BYTES)
        request_id = request_id_from_packet(request_packet)
        request = parse_request(request_packet)
        jit_payload = None
        if request["op"] == "launch":
            jit_payload = validate_jit_payload(recv_packet(connection, MAX_JIT_BYTES))
        connection.settimeout(None)

        with HELPER_LOCK.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if request["op"] == "list":
                result: Any = list_leases()
            elif request["op"] == "launch":
                launch(request["lease"], jit_payload)
                result = None
            else:
                destroy(request["lease"])
                result = None
        response = encode_response(request_id, result=result)
    except PermissionError as exc:
        print(f"ci-runner-host-helper: {exc}", file=sys.stderr)
        response = encode_response(request_id, error="unauthorized")
    except ProtocolError as exc:
        print(f"ci-runner-host-helper: {exc}", file=sys.stderr)
        response = encode_response(request_id, error="invalid_request")
    except HostCommandError as exc:
        print(f"ci-runner-host-helper: command failed: {exc}", file=sys.stderr)
        response = encode_response(request_id, error="operation_failed")
    except Exception as exc:
        print(f"ci-runner-host-helper: operation failed: {type(exc).__name__}", file=sys.stderr)
        response = encode_response(request_id, error="operation_failed")
    connection.sendall(response)


def serve() -> None:
    connection = socket.fromfd(sys.stdin.fileno(), socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        serve_connection(connection)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("launch", "destroy"):
        child = sub.add_parser(command)
        child.add_argument("lease")
    sub.add_parser("list")
    sub.add_parser("serve")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            serve()
            return 0
        with HELPER_LOCK.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.command == "launch":
                launch(args.lease)
            elif args.command == "destroy":
                destroy(args.lease)
            else:
                print(json.dumps(list_leases(), sort_keys=True))
        return 0
    except HostCommandError as exc:
        print(f"ci-runner-host-helper: command failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ci-runner-host-helper: operation failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
