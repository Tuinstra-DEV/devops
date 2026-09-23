#!/usr/bin/env python3
"""Keep the Sanctuary WoW container on its reserved complete CPU pairs."""

from __future__ import annotations

import re
import subprocess
import sys


DOCKER = "/usr/bin/docker"
CONTAINER = "tuinstra-realm-world"
CPU_SET = "1,9,2,10,3,11"
EXPECTED_CPUS = {1, 2, 3, 9, 10, 11}


def parse_cpu_set(value: str) -> set[int]:
    cpus: set[int] = set()
    if not value:
        return cpus
    for part in value.split(","):
        if re.fullmatch(r"\d+", part):
            cpus.add(int(part))
        elif re.fullmatch(r"\d+-\d+", part):
            first, last = map(int, part.split("-"))
            if first > last:
                raise ValueError("invalid CPU range")
            cpus.update(range(first, last + 1))
        else:
            raise ValueError("invalid CPU set")
    return cpus


def inspect(container: str) -> tuple[str, bool, set[int]]:
    result = subprocess.run(
        [DOCKER, "inspect", "--format",
         "{{.Id}}|{{.State.Running}}|{{.HostConfig.CpusetCpus}}", container],
        check=True, capture_output=True, text=True, timeout=15,
    )
    identity, running, cpuset = result.stdout.strip().split("|", 2)
    if not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("invalid container identity")
    return identity, running == "true", parse_cpu_set(cpuset)


def reconcile() -> None:
    identity, running, cpus = inspect(CONTAINER)
    if not running:
        raise RuntimeError("WoW container is not running")
    if cpus == EXPECTED_CPUS:
        return
    subprocess.run(
        [DOCKER, "update", f"--cpuset-cpus={CPU_SET}", identity],
        check=True, capture_output=True, text=True, timeout=30,
    )
    verified_id, verified_running, verified_cpus = inspect(identity)
    if verified_id != identity or not verified_running or verified_cpus != EXPECTED_CPUS:
        raise RuntimeError("WoW CPU allocation did not verify")


if __name__ == "__main__":
    try:
        reconcile()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"WoW CPU pin reconciliation failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
