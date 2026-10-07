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
import stat
import struct
import subprocess
import sys
import tempfile
import tomllib
from typing import Any
import xml.etree.ElementTree as ET

LEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PREFIX = "sanctuary-ci-"
OVERLAY_ROOT = Path("/var/lib/ci-runner/overlay")
IMAGE_ROOT = Path("/var/lib/ci-runner/images")
BASE_IMAGE = IMAGE_ROOT / "ubuntu-24.04-runner.qcow2"
IMAGE_RE = re.compile(r"^ubuntu-24\.04-runner-[a-f0-9]{64}\.qcow2$")
NETWORK = "sanctuary-ci"
LIBVIRT_URI = "qemu:///system"
MAX_CONCURRENCY = 4
LEGACY_CONCURRENCY = 1
VCPUS = 4
MEMORY_MIB = 6144
DISK_GIB = "120G"
PROFILE_RESOURCES = {"heavy": (4, 6144), "medium": (2, 3072)}
PROFILE_LIMITS = {"heavy": 2, "medium": 2}
PROFILE_DISK_GIB = {"heavy": 12, "medium": 4}
MIN_FREE_DISK_GIB = 60
DISK_MARGIN_GIB = 2
MAX_LEASE_SECONDS = "7200"
HELPER_LOCK = Path("/run/lock/ci-runner-host-helper.lock")
HELPER_PATH = "/usr/local/libexec/ci-runner-host-helper"
POLICY_PATH = Path("/etc/ci-runner/manager.toml")
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
    "blockdev", "cloud-localds", "fallocate", "findmnt", "mkfs.ext4",
    "qemu-img", "losetup", "nsenter", "systemctl", "systemd-mount",
    "systemd-run", "udevadm", "virsh", "virt-install",
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


def run(command: list[str], *, check: bool = True,
        input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=check, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=90, input=input_text)
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


def validate_resources(vcpus: Any, memory_mib: Any,
                       profile: str | None = None) -> tuple[int, int]:
    if profile is None:
        expected = (VCPUS, MEMORY_MIB)
    else:
        if not isinstance(profile, str):
            raise ProtocolError("invalid runner profile")
        expected = PROFILE_RESOURCES.get(profile)
        if expected is None:
            raise ProtocolError("invalid runner profile")
    if not isinstance(vcpus, int) or isinstance(vcpus, bool) or vcpus != expected[0]:
        raise ProtocolError(f"vcpus must be exactly {expected[0]}")
    if not isinstance(memory_mib, int) or isinstance(memory_mib, bool) \
            or memory_mib != expected[1]:
        raise ProtocolError(f"memory_mib must be exactly {expected[1]}")
    return vcpus, memory_mib


def policy_mode_from_mapping(cfg: dict[str, Any]) -> str:
    common = {"runner_vcpus": 4, "runner_memory_mib": 6144,
              "runner_label": "trusted-heavy", "host_memory_reserve_mib": 4096,
              "min_free_disk_gib": 60}
    mode = cfg.get("pool_mode", "legacy")
    expected = dict(common)
    if mode == "legacy":
        expected["max_concurrency"] = 1
        forbidden = {"medium_runner_label", "medium_runner_vcpus",
                     "medium_runner_memory_mib", "storage_mode", "max_heavy",
                     "max_medium", "heavy_disk_reservation_gib",
                     "medium_disk_reservation_gib"}
        if forbidden.intersection(cfg):
            raise ProtocolError("legacy policy has four-pool keys")
    elif mode == "four":
        expected.update({
            "max_concurrency": 4, "medium_runner_label": "trusted-medium",
            "medium_runner_vcpus": 2, "medium_runner_memory_mib": 3072,
            "max_heavy": 2, "max_medium": 2,
            "heavy_disk_reservation_gib": 12,
            "medium_disk_reservation_gib": 4, "storage_mode": "bounded-loop",
        })
    else:
        raise ProtocolError("invalid installed runner pool mode")
    for key, value in expected.items():
        if key not in cfg or type(cfg[key]) is not type(value) or cfg[key] != value:
            raise ProtocolError("installed runner pool policy is not approved")
    return mode


def active_pool_mode() -> str:
    for path in (POLICY_PATH.parent, POLICY_PATH):
        metadata = path.lstat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or \
                (path == POLICY_PATH and not stat.S_ISREG(metadata.st_mode)) or \
                (path != POLICY_PATH and not stat.S_ISDIR(metadata.st_mode)):
            raise ProtocolError("installed runner policy ownership is invalid")
    with POLICY_PATH.open("rb") as handle:
        return policy_mode_from_mapping(tomllib.load(handle))


def enforce_launch_mode(profile: str | None) -> None:
    mode = active_pool_mode()
    if mode == "four" and (not isinstance(profile, str)
                           or profile not in PROFILE_RESOURCES):
        raise ProtocolError("four-pool launch requires a bounded profile")
    if mode == "legacy" and profile is not None:
        raise ProtocolError("legacy launch rejects bounded profiles")


def profile_has_slot(counts: dict[str, int], profile: str) -> bool:
    if profile not in PROFILE_LIMITS or set(counts) - set(PROFILE_LIMITS):
        return False
    if any(not isinstance(count, int) or count < 0 for count in counts.values()):
        return False
    projected = dict(counts)
    projected[profile] = projected.get(profile, 0) + 1
    return all(projected.get(kind, 0) <= maximum
               for kind, maximum in PROFILE_LIMITS.items()) and sum(
                   projected.get(kind, 0) * PROFILE_RESOURCES[kind][0]
                   for kind in PROFILE_LIMITS
               ) <= 12 and sum(projected.values()) <= MAX_CONCURRENCY


def reservation_fits(free_bytes: int, profile: str) -> bool:
    if profile not in PROFILE_DISK_GIB:
        return False
    return free_bytes - PROFILE_DISK_GIB[profile] * 1024**3 >= \
        (MIN_FREE_DISK_GIB + DISK_MARGIN_GIB) * 1024**3


def bounded_paths(lease: str) -> tuple[Path, Path, Path]:
    directory = lease_dir(lease)
    return directory, directory / "bounded.img", directory / "work"


def recorded_profile(lease: str) -> str:
    directory = lease_dir(lease)
    marker = directory / "profile"
    if marker.is_symlink():
        raise RuntimeError("runner profile marker is a link")
    if marker.is_file():
        profile = marker.read_text(encoding="ascii").strip()
        if profile not in PROFILE_RESOURCES:
            raise RuntimeError("runner profile marker is invalid")
        _, backing, mountpoint = bounded_paths(lease)
        if backing.is_symlink() or not backing.is_file():
            raise RuntimeError("runner bounded backing file is invalid")
        stat = backing.stat()
        expected_bytes = PROFILE_DISK_GIB[profile] * 1024**3
        if stat.st_size != expected_bytes or stat.st_blocks * 512 < expected_bytes:
            raise RuntimeError("runner bounded backing file is not fully allocated")
        if not verify_bounded_mount(backing, mountpoint):
            raise RuntimeError("runner bounded mount is not active")
        return profile
    if (directory / "root.qcow2").is_file() and not (directory / "bounded.img").exists():
        return "heavy"
    raise RuntimeError("runner profile is not known")


def host_mount_source(mountpoint: Path) -> str | None:
    result = run([
        "nsenter", "--mount=/proc/1/ns/mnt", "--", "findmnt", "--noheadings",
        "--output", "SOURCE", "--mountpoint", str(mountpoint),
    ], check=False)
    if result.returncode == 1 and not result.stdout.strip():
        return None
    if result.returncode != 0 or len(result.stdout.splitlines()) != 1:
        raise RuntimeError("runner mount state cannot be verified")
    return result.stdout.strip()


def local_mount_source(mountpoint: Path) -> str | None:
    result = run(["findmnt", "--noheadings", "--output", "SOURCE",
                  "--mountpoint", str(mountpoint)], check=False)
    if result.returncode == 1 and not result.stdout.strip():
        return None
    if result.returncode != 0 or len(result.stdout.splitlines()) != 1:
        raise RuntimeError("local runner mount state cannot be verified")
    return result.stdout.strip()


def associated_loop(backing: Path) -> str | None:
    result = run(["losetup", "--noheadings", "--output", "NAME",
                  "--associated", str(backing)], check=False)
    if result.returncode != 0 or len(result.stdout.splitlines()) > 1:
        raise RuntimeError("runner loop association cannot be verified")
    loop = result.stdout.strip()
    if loop and not re.fullmatch(r"/dev/loop[0-9]+", loop):
        raise RuntimeError("runner loop association is invalid")
    return loop or None


def loop_discard_limit(loop: str) -> int:
    if not re.fullmatch(r"/dev/loop[0-9]+", loop):
        raise RuntimeError("runner loop device is invalid")
    path = f"/sys/class/block/{Path(loop).name}/queue/discard_max_bytes"
    value = run(["nsenter", "--mount=/proc/1/ns/mnt", "--", "cat", path]).stdout.strip()
    if not value.isdecimal():
        raise RuntimeError("runner loop discard limit cannot be verified")
    return int(value)


def disable_loop_discard(loop: str) -> None:
    if not re.fullmatch(r"/dev/loop[0-9]+", loop):
        raise RuntimeError("runner loop device is invalid")
    path = f"/sys/class/block/{Path(loop).name}/queue/discard_max_bytes"
    run(["nsenter", "--mount=/proc/1/ns/mnt", "--", "tee", path], input_text="0\n")
    if loop_discard_limit(loop) != 0:
        raise RuntimeError("runner loop discard could not be disabled")


def verify_loop_binding(backing: Path, loop: str, *, require_discard: bool = True) -> None:
    if not re.fullmatch(r"/dev/loop[0-9]+", loop):
        raise RuntimeError("runner mount is not loop-backed")
    result = run(["losetup", "--noheadings", "--output", "BACK-FILE", loop])
    if result.stdout.strip() != str(backing) or associated_loop(backing) != loop:
        raise RuntimeError("runner loop backing file does not match lease")
    size = run(["blockdev", "--getsize64", loop]).stdout.strip()
    if not size.isdecimal() or int(size) != backing.stat().st_size:
        raise RuntimeError("runner loop size does not match lease")
    if require_discard and loop_discard_limit(loop) != 0:
        raise RuntimeError("runner loop discard is enabled")


def verify_bounded_mount(backing: Path, mountpoint: Path) -> bool:
    source = host_mount_source(mountpoint)
    local = local_mount_source(mountpoint)
    if source is None:
        if local is not None:
            raise RuntimeError("runner mount namespaces disagree")
        return False
    if local != source:
        raise RuntimeError("runner mount namespaces disagree")
    verify_loop_binding(backing, source)
    return True


def create_bounded_storage(lease: str, profile: str, qemu_gid: int) -> Path:
    directory, backing, mountpoint = bounded_paths(lease)
    if not reservation_fits(shutil.disk_usage(OVERLAY_ROOT).free, profile):
        raise RuntimeError("bounded runner reservation would breach NVMe floor")
    bytes_reserved = PROFILE_DISK_GIB[profile] * 1024**3
    marker = directory / "profile"
    with marker.open("x", encoding="ascii") as handle:
        handle.write(profile + "\n")
    marker.chmod(0o600)
    fd = os.open(backing, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    run(["fallocate", "--length", str(bytes_reserved), str(backing)])
    stat = backing.stat()
    if stat.st_size != bytes_reserved or stat.st_blocks * 512 < bytes_reserved:
        raise RuntimeError("runner backing file is not fully allocated")
    if shutil.disk_usage(OVERLAY_ROOT).free < \
            (MIN_FREE_DISK_GIB + DISK_MARGIN_GIB) * 1024**3:
        raise RuntimeError("NVMe floor was breached during reservation")
    run(["mkfs.ext4", "-q", "-F", "-m", "0", "-E", "nodiscard", str(backing)])
    if backing.stat().st_blocks * 512 < bytes_reserved:
        raise RuntimeError("filesystem creation released backing reservation")
    if associated_loop(backing) is not None:
        raise RuntimeError("runner backing file already has a loop device")
    loop = run(["losetup", "--find", "--show", "--nooverlap", str(backing)]).stdout.strip()
    verify_loop_binding(backing, loop, require_discard=False)
    disable_loop_discard(loop)
    verify_loop_binding(backing, loop)
    mountpoint.mkdir(mode=0o700)
    run(["systemd-mount", "--collect", "--type=ext4",
         "--options=nodiscard,nodev,nosuid,noexec", loop, str(mountpoint)])
    if not verify_bounded_mount(backing, mountpoint):
        raise RuntimeError("runner mount did not become visible in both namespaces")
    if backing.stat().st_blocks * 512 < bytes_reserved:
        raise RuntimeError("runner backing reservation changed before guest creation")
    set_qemu_access(mountpoint, 0, qemu_gid, 0o710)
    return mountpoint


def remove_bounded_storage(lease: str) -> None:
    directory, backing, mountpoint = bounded_paths(lease)
    source = host_mount_source(mountpoint)
    loop = associated_loop(backing)
    if source is not None:
        if source != loop:
            raise RuntimeError("runner mount does not match lease loop")
        verify_loop_binding(backing, source, require_discard=False)
        run(["systemd-mount", "--umount", str(mountpoint)])
    if host_mount_source(mountpoint) is not None or local_mount_source(mountpoint) is not None:
        raise RuntimeError("runner mount remains active")
    if loop is not None:
        verify_loop_binding(backing, loop, require_discard=False)
        run(["losetup", "--detach", loop])
        run(["udevadm", "settle", "--timeout=10"])
        if associated_loop(backing) is not None:
            raise RuntimeError("runner loop device release is not verified")
    shutil.rmtree(directory)


def cloud_init_user_data(encoded_jit: bytes) -> str:
    encoded_jit = validate_jit_payload(encoded_jit)
    return """#cloud-config
bootcmd:
  - [install, -d, -o, ci-runner, -g, ci-runner, -m, '0700', /run/ci-runner]
  - [install, -d, -o, root, -g, root, -m, '0755', /etc/systemd/system/ci-runner-job.service.d]
  - [chown, root:ci-runner, /opt/actions-runner]
  - [chmod, '1770', /opt/actions-runner]
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
      ReadWritePaths=/opt/actions-runner
      UMask=0022
  - path: /etc/docker/daemon.json
    owner: root:root
    permissions: '0644'
    content: |
      {
        "features": {
          "containerd-snapshotter": false
        },
        "storage-driver": "overlay2"
      }
  - path: /usr/local/sbin/ci-runner-prepare-docker
    owner: root:root
    permissions: '0755'
    content: |
      #!/bin/sh
      set -eu
      systemctl restart docker.service
      systemctl is-active --quiet docker.service
      test "$(docker info --format '{{.Driver}}')" = overlay2
      systemctl daemon-reload
      systemctl start --no-block ci-runner-job.service
runcmd:
  - [/usr/local/sbin/ci-runner-prepare-docker]
""" % base64.b64encode(encoded_jit).decode("ascii")


def cloud_init_network_config() -> str:
    """Enable DHCP without depending on a hypervisor-specific interface name."""
    return """version: 2
ethernets:
  runner:
    match:
      name: "en*"
    dhcp4: true
    dhcp6: false
"""


def launch(lease: str, encoded_jit: bytes | None = None, *,
           vcpus: int = VCPUS, memory_mib: int = MEMORY_MIB,
           profile: str | None = None) -> None:
    if os.geteuid() != 0:
        raise PermissionError("helper must run as root")
    enforce_launch_mode(profile)
    validate_lease(lease)
    if encoded_jit is None:
        encoded_jit = sys.stdin.buffer.read(MAX_JIT_BYTES + 1).strip()
    encoded_jit = validate_jit_payload(encoded_jit)
    vcpus, memory_mib = validate_resources(vcpus, memory_mib, profile)
    base_image = resolved_base_image()
    existing = run(["virsh", "--connect", LIBVIRT_URI, "list", "--all", "--name"])
    runner_domains = [
        line for line in existing.stdout.splitlines() if line.startswith(PREFIX)
    ]
    if profile is None:
        if len(runner_domains) >= LEGACY_CONCURRENCY:
            raise RuntimeError("all sanctuary CI domain slots are occupied")
    else:
        counts = {"heavy": 0, "medium": 0}
        for domain in runner_domains:
            existing_lease = domain[len(PREFIX):]
            existing_directory = lease_dir(existing_lease)
            if not (existing_directory / "profile").is_file():
                raise RuntimeError("bounded pool requires all legacy domains to drain")
            counts[recorded_profile(existing_lease)] += 1
        if not profile_has_slot(counts, profile):
            raise RuntimeError("all sanctuary CI profile slots are occupied")
    if name(lease) in runner_domains:
        raise RuntimeError("sanctuary CI domain already exists")
    directory = lease_dir(lease)
    if directory.exists():
        raise FileExistsError("lease directory already exists")
    directory.mkdir(mode=0o700)
    qemu_uid, qemu_gid = qemu_identity()
    set_qemu_access(directory, 0, qemu_gid, 0o710)
    try:
        work = create_bounded_storage(lease, profile, qemu_gid) \
            if profile is not None else directory
        overlay = work / "root.qcow2"
        seed = work / "seed.iso"
        run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base_image), str(overlay), DISK_GIB])
        set_qemu_access(overlay, qemu_uid, qemu_gid, 0o600)
        user_data = cloud_init_user_data(encoded_jit)
        meta_data = f"instance-id: {name(lease)}\nlocal-hostname: ci-worker\n"
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            temp = Path(temporary)
            (temp / "user-data").write_text(user_data, encoding="utf-8")
            (temp / "meta-data").write_text(meta_data, encoding="utf-8")
            (temp / "network-config").write_text(
                cloud_init_network_config(), encoding="utf-8"
            )
            os.chmod(temp / "user-data", 0o600)
            os.chmod(temp / "network-config", 0o600)
            run([
                "cloud-localds",
                f"--network-config={temp / 'network-config'}",
                str(seed), str(temp / "user-data"), str(temp / "meta-data"),
            ])
        set_qemu_access(seed, qemu_uid, qemu_gid, 0o600)
        run(["virt-install", "--connect", LIBVIRT_URI,
             "--name", name(lease), "--memory", str(memory_mib),
             "--vcpus", str(vcpus),
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
    remaining = run(["virsh", "--connect", LIBVIRT_URI, "list", "--all", "--name"])
    if domain in remaining.stdout.splitlines():
        raise RuntimeError("runner domain remains defined; preserving lease storage")
    directory = lease_dir(lease)
    if directory.exists():
        if (directory / "profile").exists() or (directory / "bounded.img").exists():
            remove_bounded_storage(lease)
        else:
            shutil.rmtree(directory)


def list_leases() -> dict[str, str]:
    result = run(["virsh", "--connect", LIBVIRT_URI, "list", "--all", "--name"])
    leases = {}
    for domain in sorted(line for line in result.stdout.splitlines() if line.startswith(PREFIX)):
        state = run(["virsh", "--connect", LIBVIRT_URI, "domstate", domain]).stdout.strip().lower()
        leases[domain[len(PREFIX):]] = state
    return leases


def qemu_rss_mib(domain: str) -> int | None:
    matches: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
            names = [arguments[index + 1] for index, value in enumerate(arguments[:-1])
                     if value == b"-name"]
            if not any(name.decode("ascii", "ignore").split(",")[0] == f"guest={domain}"
                       for name in names):
                continue
            executable = (entry / "exe").resolve(strict=True).name
            if not executable.startswith("qemu-system-"):
                continue
            for line in (entry / "status").read_text(encoding="ascii").splitlines():
                if line.startswith("VmRSS:"):
                    matches.append((int(line.split()[1]) + 1023) // 1024)
                    break
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return matches[0] if len(matches) == 1 else None


def domain_resources(domain: str) -> tuple[int, int]:
    xml = run(["virsh", "--connect", LIBVIRT_URI, "dumpxml", domain]).stdout
    root = ET.fromstring(xml)
    cpu_text = root.findtext("vcpu")
    memory = root.find("memory")
    if cpu_text is None or memory is None or memory.text is None:
        raise RuntimeError("runner domain resources are missing")
    cpu = int(cpu_text.strip())
    unit = memory.attrib.get("unit", "KiB")
    if unit not in {"KiB", "MiB"}:
        raise RuntimeError("runner domain memory unit is unsupported")
    memory_mib = int(memory.text.strip()) // 1024 if unit == "KiB" \
        else int(memory.text.strip())
    return cpu, memory_mib


def list_resource_details() -> list[dict[str, Any]]:
    details = []
    for lease, state in list_leases().items():
        domain = name(lease)
        cpu, memory = domain_resources(domain)
        details.append({
            "lease": lease, "state": state, "profile": recorded_profile(lease),
            "vcpus": cpu, "memory_mib": memory, "rss_mib": qemu_rss_mib(domain),
            "bounded": (lease_dir(lease) / "profile").is_file(),
        })
    return details


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
    if operation in {"list", "resources"}:
        if set(request) != {"v", "id", "op"}:
            raise ProtocolError("invalid query request schema")
    elif operation in {"launch", "destroy"}:
        expected = {"v", "id", "op", "lease"}
        if operation == "launch":
            expected.update({"vcpus", "memory_mib"})
            if "profile" in request:
                expected.add("profile")
        if set(request) != expected:
            raise ProtocolError("invalid mutation request schema")
        lease = request.get("lease")
        if not isinstance(lease, str):
            raise ProtocolError("invalid lease type")
        try:
            validate_lease(lease)
        except ValueError as exc:
            raise ProtocolError("invalid lease") from exc
        if operation == "launch":
            if "profile" in request and (not isinstance(request["profile"], str)
                                         or request["profile"] not in PROFILE_RESOURCES):
                raise ProtocolError("invalid runner profile")
            validate_resources(request.get("vcpus"), request.get("memory_mib"),
                               request.get("profile"))
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
        if request["op"] == "launch":
            enforce_launch_mode(request.get("profile"))
        jit_payload = None
        if request["op"] == "launch":
            jit_payload = validate_jit_payload(recv_packet(connection, MAX_JIT_BYTES))
        connection.settimeout(None)

        with HELPER_LOCK.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if request["op"] == "list":
                result: Any = list_leases()
            elif request["op"] == "resources":
                result = list_resource_details()
            elif request["op"] == "launch":
                launch_kwargs: dict[str, Any] = {
                    "vcpus": request["vcpus"], "memory_mib": request["memory_mib"],
                }
                if "profile" in request:
                    launch_kwargs["profile"] = request["profile"]
                launch(
                    request["lease"], jit_payload, **launch_kwargs,
                )
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
