# DEV-50: bounded Sanctuary runner pools

The installed policy remains one `trusted-heavy` runner until an operator explicitly
selects `runner_pool_mode: four` and `runner_max_concurrency: 4` in Ansible.
That mode allows at most two heavy VMs (4 vCPU, 6144 MiB each) and two medium
VMs (2 vCPU, 3072 MiB each), subject to a 12-vCPU total, a 4096-MiB host
memory reserve, and the root NVMe free-space floor. The existing heavy label
and repository allowlist remain unchanged. The automatic dispatcher still
selects only `trusted-heavy`; medium PR routing awaits a separate trust and
GitHub assignment review. Ordinary light jobs remain on GitHub-hosted runners.

Each new bounded lease gets a fully allocated 24-GiB (heavy) or 4-GiB (medium)
ext4 image file at `/var/lib/ci-runner/overlay/<lease>/bounded.img`, on the
existing Kingston root NVMe. The helper attaches an exact-size loop device,
disables its discard queue, and verifies the readback before PID 1 mounts the
device through `systemd-mount` at the lease's `work` subdirectory. The qcow2
overlay and seed ISO live inside that fixed-size filesystem. The helper checks
allocation, both mount namespaces, and the exact loop backing before starting
a VM. It reserves the full image size before admission,
requires at least 62 GiB free afterward (60-GiB policy floor plus 2-GiB
margin), and retains the files if VM or mount cleanup cannot be verified.
Sparse qcow2 virtual size and `RLIMIT_FSIZE` are not physical-space quotas.
The 56-GiB sum bounds four active reservations. With about 98 GiB free before
runner allocation, the unchanged 62-GiB admission floor currently allows one
heavy plus two medium runners, and queues the second heavy. Four simultaneous
slots require at least 118 GiB free plus filesystem overhead. Failed or orphan leases may
retain backing files beyond those slots; their allocated blocks still reduce
the root free space used for admission.

The manager counts active VMs by profile and admits memory using
`MemAvailable - new VM allocation - sum(max(0, active allocation - active RSS))`.
The current resident QEMU memory is already reflected in `MemAvailable`.
Unknown VM identity, resources, RSS, backing allocation, or mount state deny
admission. The root helper independently enforces profile tuples and slot
counts and checks the root-owned installed manager policy. A four-pool host
rejects legacy no-profile launches. Existing legacy leases must drain before
the four-pool policy can run.

Deployment is deliberately gated. Wait for all `sanctuary-ci-*` domains and
overlays to drain, then apply the reviewed Ansible policy with
`runner_allow_pool_policy_transition: true`. The role checks for domains before
stopping admission and retains a backup of the prior manager configuration.
Verify the host has `fallocate`, `mkfs.ext4`, `systemd-mount`, `nsenter`, and
`losetup`, and that the root overlay directory still resolves directly to the
reviewed NVMe filesystem. Canary a heavy lease; a medium job awaits separate
approval of its trust and GitHub assignment gate. Confirm the bounded image's
allocated blocks, its PID-1- and helper-visible loop mount, a zero discard
queue, unchanged allocation after `fstrim` on the mounted lease, guest Docker
operation, successful unmount/loop release, and the 60-GiB root floor. The
24/4-GiB caps remain sizing hypotheses after the first Gate image job filled its
12-GiB filesystem;
measure peak usage and adjust only through a separately reviewed policy change.
If any check fails, stop admission, let active leases finish, restore the
backed-up single-runner policy and prior binaries, and restart the manager.
Do not delete an active lease or backing file to force rollback.

Local verification: `PYTHONPATH=runner/manager:runner/host-helper python3 -m
unittest runner.tests.test_manager runner.tests.test_host_helper`,
`scripts/test-runner-platform.sh`, `make lint`, and `make test`. A local macOS
test cannot prove Linux mount namespace propagation or loop lifecycle; the
guarded Sanctuary canary is required before enabling four-pool admission.

## Recovery after the first Gate canary

The first Gate PHP image job completed both vulnerability scans and uploaded its
artifact, then lost its guest during cache finalization. Its bounded filesystem
had 4096 bytes available. This proves insufficient disk capacity; the final QEMU
shutdown reason must still be captured on the host before attributing the exit.

Linux may shorten a loop backing pathname after the creator mount namespace
exits. The helper therefore verifies kernel device and inode identity using
`LOOP_GET_STATUS64`, preserving exact allocation, mount and discard checks.
Existing root-marked 12-GiB heavy storage remains recognizable for cleanup;
new heavy allocation is always 24 GiB.

Run the pinned `scripts/recover-loop-identity.py` against the reviewed intermediate
helper (SHA-256 `e36b48d938f108a348621664bd62fa0fe5fc55180d5ac3fce67070d72e59f34b`)
while the installed policy is still 12 GiB. It records only filesystem usage and
sanitized shutdown reason, swaps the helper with a recoverable backup and probes
the actual socket. Canceled Gate claims are retained. Then drain normally and run
the reviewed activation script, which can transform an exact existing 12-GiB
four-pool configuration to 24 GiB without altering credentials or repositories.
Do not install the 24-GiB helper alone against the old policy.
