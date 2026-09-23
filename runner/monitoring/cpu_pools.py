#!/usr/bin/env python3
"""Emit one credential-free Sanctuary CPU pool sample as JSON."""

from __future__ import annotations

import json
from pathlib import Path
import time

POOLS = {
    "host": (0, 8),
    "wow": (1, 9, 2, 10, 3, 11),
    "ci_1": (4, 12, 5, 13),
    "ci_2": (6, 14, 7, 15),
}


def read_cpus(path: Path = Path("/proc/stat")) -> dict[int, tuple[int, int, int]]:
    samples = {}
    for line in path.read_text(encoding="ascii").splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("cpu") or not parts[0][3:].isdigit():
            continue
        values = [int(value) for value in parts[1:]]
        if len(values) < 8:
            raise ValueError("incomplete CPU accounting")
        samples[int(parts[0][3:])] = (sum(values[:8]), values[3] + values[4], values[7])
    return samples


def utilization(before: dict[int, tuple[int, int, int]],
                after: dict[int, tuple[int, int, int]],
                cpus: tuple[int, ...]) -> dict[str, float]:
    if any(cpu not in before or cpu not in after for cpu in cpus):
        raise ValueError("CPU topology does not match Sanctuary allocation")
    total = sum(after[cpu][0] - before[cpu][0] for cpu in cpus)
    idle = sum(after[cpu][1] - before[cpu][1] for cpu in cpus)
    steal = sum(after[cpu][2] - before[cpu][2] for cpu in cpus)
    if total <= 0 or idle < 0 or steal < 0:
        raise ValueError("invalid CPU accounting interval")
    return {"busy_percent": round(100 * (total - idle - steal) / total, 2),
            "steal_percent": round(100 * steal / total, 2)}


def main() -> None:
    before = read_cpus()
    time.sleep(1)
    after = read_cpus()
    print(json.dumps({"timestamp_unix": int(time.time()),
                      "pools": {name: utilization(before, after, cpus)
                                for name, cpus in POOLS.items()}}, sort_keys=True))


if __name__ == "__main__":
    main()
