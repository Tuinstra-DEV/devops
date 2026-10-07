# DEV-50: bounded Sanctuary runner pools

The installed policy remains one `trusted-heavy` runner until an operator explicitly
selects `runner_pool_mode: four` and `runner_max_concurrency: 4` in Ansible.
That mode allows at most two heavy VMs (4 vCPU, 6144 MiB each) and two medium
VMs (2 vCPU, 3072 MiB each), subject to a 12-vCPU total, a 4096-MiB host
memory reserve, and the root NVMe free-space floor. The existing heavy label
and repository allowlist remain unchanged. The automatic dispatcher still
selects only `trusted-heavy`; medium PR routing awaits a separate trust and
GitHub assignment review. Ordinary light jobs remain on GitHub-hosted runners.

Each new bounded lease gets a fully allocated 12-GiB (heavy) or 4-GiB (medium)
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
The 32-GiB sum bounds four active reservations. Failed or orphan leases may
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
12/4-GiB caps are initial hypotheses;
measure peak usage and adjust only through a separately reviewed policy change.
If any check fails, stop admission, let active leases finish, restore the
backed-up single-runner policy and prior binaries, and restart the manager.
Do not delete an active lease or backing file to force rollback.

Local verification: `PYTHONPATH=runner/manager:runner/host-helper python3 -m
unittest runner.tests.test_manager runner.tests.test_host_helper`,
`scripts/test-runner-platform.sh`, `make lint`, and `make test`. A local macOS
test cannot prove Linux mount namespace propagation or loop lifecycle; the
guarded Sanctuary canary is required before enabling four-pool admission.
