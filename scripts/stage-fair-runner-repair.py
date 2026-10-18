#!/usr/bin/env python3
"""Stage the hash-pinned DEV-50 scheduler repair, then run its guarded operator."""

import argparse
import hashlib
import os
from pathlib import Path
import stat
import tempfile

PAYLOADS = {
    "ci_runner_manager.py": "b78512b6ffb6f5d6a65c8971d9e566a18419d2418ca405ab9e55069a077cec09",
    "transition-org-runner-workflows.py": "76267591bf8f047f273e42984a61661800f49fe84b9fb770dde5eee7a122c4b8",
    "activate-org-runner-routing.py": "d19f0da14430cd05169836ef76c5c92d9805972b74a16379886ba6229541726f",
}


def stage_verified(source: Path, parent: Path) -> Path:
    # Hash all bytes before creating the private stage; do not execute imports
    # from the operator's writable source directory. Replacing a source after
    # this read cannot change the already verified bytes we install.
    payloads = {}
    for name, digest in PAYLOADS.items():
        data = (source / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"reviewed payload mismatch: {name}")
        payloads[name] = data
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("stage parent must be a protected directory owned by the operator")
    stage = Path(tempfile.mkdtemp(prefix="dev50-fair-scheduler-", dir=parent))
    for name, data in payloads.items():
        target = stage / name
        with target.open("xb") as stream:
            stream.write(data)
        target.chmod(0o644)
        if hashlib.sha256(target.read_bytes()).hexdigest() != PAYLOADS[name]:
            raise ValueError("staged payload readback differs")
    return stage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true")
    actions.add_argument("--rollback", metavar="BACKUP_ID")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("run through the named operator account's sudo")
    os.environ["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    stage = stage_verified(Path(__file__).resolve().parent, Path("/root"))
    helper = stage / "transition-org-runner-workflows.py"
    command = ["/usr/bin/python3", "-I", str(helper), "--stage", str(stage)]
    if args.rollback:
        command += ["--rollback", args.rollback]
    else:
        command += ["--workflow-sha", "23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e", "--group-id", "4"]
        if args.apply:
            command += ["--apply"]
    print(f"Verified root-owned stage: {stage}", flush=True)
    print(f"Rollback after an applied result: sudo /usr/bin/python3 -I {helper} --rollback BACKUP_ID", flush=True)
    os.execv(command[0], command)


if __name__ == "__main__":
    main()
